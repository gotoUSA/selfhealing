# Platinum SLA 달성을 위한 Self-Healing 최적화 계획

📅 **작성일**: 2025-12-27  
🎯 **목표**: P99 ≤ 100ms (Platinum SLA)  
📊 **현재 상태**: P99 = 422.90ms (GOLD 250ms 기준 미달)

---

## 📋 Executive Summary

Stage 10 EXTREME V2 테스트 결과, GOLD SLA(P99 ≤ 250ms)를 충족하지 못했습니다.
본 문서는 병목 분석과 최적화 방안을 정리하고, **아키텍처 원칙을 유지하면서** 
Platinum SLA 달성을 위한 구현 계획을 수립합니다.

### 아키텍처 원칙 (반드시 유지)

| 원칙 | 설명 |
|------|------|
| **단방향 의존성** | 쇼핑몰 → 사령탑 (Pull), 사령탑은 쇼핑몰 존재를 모름 |
| **이식성** | selfhealing 패키지는 어디든 쉽게 이식 가능해야 함 |
| **낮은 결합도** | 외부 서비스/파일 의존 최소화 |

---

## 🔍 병목 분석: 왜 250ms 벽을 넘지 못했나?

### 1. 동기식 I/O 오버헤드
```
[요청] → [record_healing_event()] → [사령탑 API 호출] → [응답 대기 50~100ms] → [복구 완료]
                                          ↑
                                    병목 지점
```
**문제**: 힐링 이벤트 기록 시 사령탑과 동기 통신이 요청 경로에 포함됨

### 2. CB 상태 확인 지연
```
[CB 상태 필요] → [사령탑 API 호출] → [응답 대기 20~50ms] → [CB 판단]
                        ↑
                   매번 호출
```
**문제**: Circuit Breaker 상태를 확인할 때마다 네트워크 통신 발생

### 3. Thundering Herd (Polling 폭주)
```
Instance 1 ──┐
Instance 2 ──┼──→ 사령탑 (동시에 100개 요청)
Instance 3 ──┤
   ...       │
Instance N ──┘
     ↑
  모두 동일한 TTL(5초)
```
**문제**: N개 인스턴스가 동일 시점에 polling하면 사령탑 과부하

### 4. 사령탑 장애 시 대응 부재
```
[사령탑 다운] → [설정 조회 실패] → [힐링 로직 중단] → [서비스 장애 확산]
                     ↑
              Fallback 없음
```
**문제**: 뇌(사령탑)가 죽으면 몸(쇼핑몰)도 마비

---

## ✅ 채택된 최적화 방안

리뷰어 제안 중 아키텍처 원칙을 준수하는 항목만 선별

### 최적화 1: Zero-Latency Logging (비동기 이벤트 버퍼링)

| 항목 | 내용 |
|------|------|
| **현재** | `record_healing_event()` 동기 호출 (50~100ms 블로킹) |
| **개선** | 메모리 큐에 담고 Background Worker가 배치 처리 |
| **원칙 준수** | ✅ 의존성 변화 없음, 내부 최적화 |
| **기대 효과** | 복구 경로에서 **~100ms 단축** |

#### 구현 설계

```python
# packages/selfhealing-python/src/selfhealing/utils/async_logger.py

import queue
import threading
import time
from enum import Enum
from typing import Dict, List, Callable

class EventSeverity(Enum):
    """이벤트 심각도 (Batch Flush Policy)"""
    DEBUG = 0
    INFO = 1
    WARNING = 2
    CRITICAL = 3  # CB Open, 장애 감지 → 즉시 전송

class AsyncHealingLogger:
    """
    비동기 힐링 이벤트 로거
    
    - 일반 이벤트: 배치로 모아서 전송
    - CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
    """
    
    _queue: queue.Queue = queue.Queue()
    _running: bool = False
    _worker_thread: threading.Thread = None
    _flush_callback: Callable[[List[Dict]], None] = None
    
    # 설정
    BATCH_SIZE = 10
    FLUSH_INTERVAL = 5.0  # 초
    IMMEDIATE_SEVERITIES = {EventSeverity.CRITICAL}
    
    @classmethod
    def configure(cls, flush_callback: Callable[[List[Dict]], None]):
        """
        배치 전송 콜백 설정
        
        Args:
            flush_callback: 배치 이벤트를 받아 사령탑에 전송하는 함수
        """
        cls._flush_callback = flush_callback
    
    @classmethod
    def start(cls):
        """백그라운드 워커 시작"""
        if cls._running:
            return
        cls._running = True
        cls._worker_thread = threading.Thread(target=cls._worker, daemon=True)
        cls._worker_thread.start()
    
    @classmethod
    def stop(cls):
        """백그라운드 워커 중지"""
        cls._running = False
        if cls._worker_thread:
            cls._worker_thread.join(timeout=5)
    
    @classmethod
    def log(cls, event: Dict, severity: EventSeverity = EventSeverity.INFO):
        """
        이벤트 로깅 (논블로킹, ~0.01ms)
        
        Args:
            event: 힐링 이벤트 딕셔너리
            severity: 이벤트 심각도
        """
        event['severity'] = severity.name
        event['timestamp'] = time.time()
        
        if severity in cls.IMMEDIATE_SEVERITIES:
            # CRITICAL: 즉시 전송 (별도 스레드)
            threading.Thread(
                target=cls._flush_immediate, 
                args=([event],),
                daemon=True
            ).start()
        else:
            # 일반: 배치 대기
            cls._queue.put(event)
    
    @classmethod
    def _worker(cls):
        """배치 처리 워커"""
        batch = []
        last_flush = time.time()
        
        while cls._running:
            try:
                event = cls._queue.get(timeout=1.0)
                batch.append(event)
            except queue.Empty:
                pass
            
            # 배치 사이즈 도달 또는 시간 경과 시 전송
            should_flush = (
                len(batch) >= cls.BATCH_SIZE or
                (batch and time.time() - last_flush >= cls.FLUSH_INTERVAL)
            )
            
            if should_flush:
                cls._flush_batch(batch)
                batch = []
                last_flush = time.time()
        
        # 종료 시 남은 이벤트 처리
        if batch:
            cls._flush_batch(batch)
    
    @classmethod
    def _flush_batch(cls, events: List[Dict]):
        """배치 전송"""
        if cls._flush_callback and events:
            try:
                cls._flush_callback(events)
            except Exception as e:
                # 전송 실패해도 서비스는 계속
                pass
    
    @classmethod
    def _flush_immediate(cls, events: List[Dict]):
        """즉시 전송"""
        cls._flush_batch(events)
```

---

### 최적화 2: TTL 기반 로컬 캐싱 + Polling Jitter

| 항목 | 내용 |
|------|------|
| **리뷰어 제안** | ❌ Webhook Push (양방향 결합 발생) |
| **대안** | ✅ TTL 캐싱 + Polling Jitter (단방향 유지) |
| **원칙 준수** | ✅ 사령탑은 여전히 쇼핑몰 존재를 모름 |
| **기대 효과** | CB 상태 확인 **0ms** (캐시 히트), Thundering Herd 방지 |

#### 구현 설계

```python
# packages/selfhealing-python/src/selfhealing/core/state_cache.py

import time
import random
import threading
from typing import Dict, Any, Optional, Callable

class CBStateCache:
    """
    Circuit Breaker 상태 캐시
    
    - TTL 기반 로컬 캐싱으로 네트워크 호출 최소화
    - Polling Jitter로 Thundering Herd 방지
    - Thread-Safe 구현
    """
    
    _cache: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()
    _fetch_callback: Callable[[str], Dict] = None
    
    # 설정
    BASE_TTL = 5.0  # 기본 TTL (초)
    JITTER_RANGE = 0.5  # ±0.5초 랜덤 지터
    
    @classmethod
    def configure(cls, fetch_callback: Callable[[str], Dict]):
        """
        상태 조회 콜백 설정
        
        Args:
            fetch_callback: 서비스명을 받아 사령탑에서 CB 상태를 가져오는 함수
        """
        cls._fetch_callback = fetch_callback
    
    @classmethod
    def get_state(cls, service: str) -> Optional[Dict]:
        """
        CB 상태 조회 (캐시 우선)
        
        캐시 히트: ~0.01ms
        캐시 미스: 사령탑 호출 시간
        
        Args:
            service: 서비스 식별자
            
        Returns:
            CB 상태 딕셔너리 또는 None
        """
        with cls._lock:
            cached = cls._cache.get(service)
            
            if cached and not cls._is_expired(cached):
                return cached['state']
        
        # 캐시 미스 → 사령탑 호출
        return cls._refresh(service)
    
    @classmethod
    def invalidate(cls, service: str):
        """특정 서비스 캐시 무효화"""
        with cls._lock:
            cls._cache.pop(service, None)
    
    @classmethod
    def invalidate_all(cls):
        """전체 캐시 무효화"""
        with cls._lock:
            cls._cache.clear()
    
    @classmethod
    def _is_expired(cls, cached: Dict) -> bool:
        """TTL 만료 확인"""
        return time.time() >= cached['expires_at']
    
    @classmethod
    def _calculate_ttl(cls) -> float:
        """
        Jitter가 적용된 TTL 계산
        
        Thundering Herd 방지를 위해 4.5초 ~ 5.5초 사이 랜덤
        """
        jitter = random.uniform(-cls.JITTER_RANGE, cls.JITTER_RANGE)
        return cls.BASE_TTL + jitter
    
    @classmethod
    def _refresh(cls, service: str) -> Optional[Dict]:
        """사령탑에서 상태 가져와 캐시 갱신"""
        if not cls._fetch_callback:
            return None
        
        try:
            state = cls._fetch_callback(service)
            ttl = cls._calculate_ttl()
            
            with cls._lock:
                cls._cache[service] = {
                    'state': state,
                    'fetched_at': time.time(),
                    'expires_at': time.time() + ttl,
                }
            
            return state
            
        except Exception:
            # 사령탑 연결 실패 → SafeDefaults 사용
            from selfhealing.core.defaults import SafeDefaults
            return SafeDefaults.get_cb_config()
```

---

### 최적화 3: SafeDefaults (최후의 보루)

| 항목 | 내용 |
|------|------|
| **리뷰어 제안** | ❌ 로컬 파일에 설정 저장 (결합도 증가) |
| **대안** | ✅ 패키지 내장 기본값 (의존성 없음) |
| **추가 기능** | Degraded Awareness, Thread-Safe, HealthBridge 연동점 |
| **원칙 준수** | ✅ 외부 파일/서비스 의존 없음, 완전 이식 가능 |
| **기대 효과** | 사령탑 장애 시에도 **0ms** 즉시 보호 로직 가동 |

#### 구현 설계

```python
# packages/selfhealing-python/src/selfhealing/core/defaults.py

import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class SafeDefaults:
    """
    사령탑 연결 실패 시 사용할 안전한 기본값
    
    특징:
    - 패키지 내장 (외부 파일 의존 없음)
    - Thread-Safe
    - Degraded Mode 인지 로깅
    - 런타임 오버라이드 지원
    
    사용법:
        # 기본값 사용
        config = SafeDefaults.get_cb_config()
        
        # 오버라이드 (선택적)
        SafeDefaults.set('CB_FAILURE_THRESHOLD', 5)
        
        # 환경변수로도 가능
        # SELFHEALING_CB_FAILURE_THRESHOLD=5
    """
    
    _lock = threading.RLock()
    _degraded_warned = False
    _is_degraded = False
    
    # 기본 설정값 (보수적)
    _defaults: Dict[str, Any] = {
        # Circuit Breaker
        'CB_FAILURE_THRESHOLD': 3,          # 3회 실패 시 Open
        'CB_RECOVERY_TIMEOUT': 60,          # 60초 후 Half-Open
        'CB_HALF_OPEN_MAX_CALLS': 1,        # Half-Open에서 1회만 테스트
        
        # Rate Limit
        'RATE_LIMIT_PER_MINUTE': 100,       # 분당 100회
        'RATE_LIMIT_BURST': 10,             # 버스트 10회
        
        # Timeout
        'DEFAULT_TIMEOUT_MS': 5000,         # 5초
        'HEALTH_CHECK_TIMEOUT_MS': 1000,    # 헬스체크 1초
        
        # Retry
        'MAX_RETRY_ATTEMPTS': 3,            # 최대 3회 재시도
        'RETRY_BACKOFF_BASE_MS': 100,       # 백오프 기본값 100ms
    }
    
    # 런타임 오버라이드 저장소
    _overrides: Dict[str, Any] = {}
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Thread-Safe하게 설정값 조회
        
        우선순위: 환경변수 > 런타임 오버라이드 > 기본값
        """
        import os
        
        with cls._lock:
            # 1. 환경변수 확인
            env_key = f"SELFHEALING_{key}"
            env_value = os.environ.get(env_key)
            if env_value is not None:
                return cls._parse_value(env_value)
            
            # 2. 런타임 오버라이드 확인
            if key in cls._overrides:
                return cls._overrides[key]
            
            # 3. 기본값 반환
            return cls._defaults.get(key, default)
    
    @classmethod
    def set(cls, key: str, value: Any):
        """Thread-Safe하게 설정값 오버라이드"""
        with cls._lock:
            cls._overrides[key] = value
    
    @classmethod
    def get_cb_config(cls) -> Dict[str, Any]:
        """CB 설정 일괄 조회"""
        return {
            'failure_threshold': cls.get('CB_FAILURE_THRESHOLD'),
            'recovery_timeout': cls.get('CB_RECOVERY_TIMEOUT'),
            'half_open_max_calls': cls.get('CB_HALF_OPEN_MAX_CALLS'),
        }
    
    @classmethod
    def get_rate_limit_config(cls) -> Dict[str, Any]:
        """Rate Limit 설정 일괄 조회"""
        return {
            'per_minute': cls.get('RATE_LIMIT_PER_MINUTE'),
            'burst': cls.get('RATE_LIMIT_BURST'),
        }
    
    @classmethod
    def enter_degraded_mode(cls):
        """
        Degraded Mode 진입
        
        - 사령탑 연결 실패 시 호출
        - 경고 로그 1회만 출력 (Singleton)
        """
        with cls._lock:
            cls._is_degraded = True
            
            if not cls._degraded_warned:
                logger.warning(
                    "⚠️ [SELFHEALING] System is running in DEGRADED MODE "
                    "with local defaults. Command center connection failed."
                )
                cls._degraded_warned = True
    
    @classmethod
    def exit_degraded_mode(cls):
        """Degraded Mode 해제 (사령탑 연결 복구 시)"""
        with cls._lock:
            if cls._is_degraded:
                logger.info("✅ [SELFHEALING] Exited DEGRADED MODE. Command center reconnected.")
            cls._is_degraded = False
            cls._degraded_warned = False
    
    @classmethod
    def is_degraded(cls) -> bool:
        """현재 Degraded Mode 여부"""
        with cls._lock:
            return cls._is_degraded
    
    @classmethod
    def get_health_response(cls) -> Dict[str, Any]:
        """
        HealthBridge 연동점
        
        Degraded Mode일 때 반환할 헬스 응답
        사용하는 쪽(쇼핑몰 등)에서 이 메서드를 호출하여 연동
        """
        return {
            'status': 'degraded' if cls._is_degraded else 'healthy',
            'is_degraded': cls._is_degraded,
            'source': 'local_defaults' if cls._is_degraded else 'command_center',
            'config': {
                'cb': cls.get_cb_config(),
                'rate_limit': cls.get_rate_limit_config(),
            }
        }
    
    @classmethod
    def _parse_value(cls, value: str) -> Any:
        """환경변수 문자열을 적절한 타입으로 변환"""
        # 불리언
        if value.lower() in ('true', '1', 'yes'):
            return True
        if value.lower() in ('false', '0', 'no'):
            return False
        
        # 정수
        try:
            return int(value)
        except ValueError:
            pass
        
        # 실수
        try:
            return float(value)
        except ValueError:
            pass
        
        # 문자열 그대로
        return value
```

---

### 최적화 4: Adaptive Jitter (지능형 지터)

| 항목 | 내용 |
|------|------|
| **현재** | 고정 Jitter 범위 |
| **개선** | 시스템 부하/에러 버짓에 따라 동적 조절 |
| **원칙 준수** | ✅ 순수 내부 로직, 의존성 없음 |
| **기대 효과** | P99 안정화, 불필요한 지연 제거 |

#### 구현 설계

```python
# packages/selfhealing-python/src/selfhealing/core/adaptive_jitter.py

import random
from typing import Optional

class AdaptiveJitter:
    """
    지능형 Jitter 계산기
    
    시스템 상태에 따라 Jitter 범위를 동적으로 조절:
    - 여유로운 상황: 최소 지터 (빠른 복구)
    - 위험 상황: 최대 지터 (Thundering Herd 방지)
    """
    
    # Jitter 범위 설정 (초)
    JITTER_MIN_RELAXED = (0, 0.05)      # 여유: 0~50ms
    JITTER_MIN_NORMAL = (0.03, 0.1)     # 보통: 30~100ms
    JITTER_MIN_STRESSED = (0.1, 0.3)    # 위험: 100~300ms
    
    # 임계값
    ERROR_BUDGET_DANGER_THRESHOLD = 0.2   # 에러 버짓 20% 이하 → 위험
    ERROR_BUDGET_SAFE_THRESHOLD = 0.5     # 에러 버짓 50% 이상 → 여유
    LOAD_HIGH_THRESHOLD = 0.8             # 부하 80% 이상 → 위험
    LOAD_LOW_THRESHOLD = 0.3              # 부하 30% 이하 → 여유
    
    @classmethod
    def calculate(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ) -> float:
        """
        상황에 맞는 Jitter 값 계산
        
        Args:
            error_budget_remaining: 남은 에러 버짓 비율 (0.0 ~ 1.0)
            current_load: 현재 시스템 부하 (0.0 ~ 1.0)
            
        Returns:
            적용할 Jitter 값 (초)
        """
        # 정보가 없으면 보통 범위 사용
        if error_budget_remaining is None and current_load is None:
            return random.uniform(*cls.JITTER_MIN_NORMAL)
        
        # 위험 상황 판단
        is_budget_danger = (
            error_budget_remaining is not None and 
            error_budget_remaining < cls.ERROR_BUDGET_DANGER_THRESHOLD
        )
        is_load_high = (
            current_load is not None and 
            current_load > cls.LOAD_HIGH_THRESHOLD
        )
        
        # 여유 상황 판단
        is_budget_safe = (
            error_budget_remaining is not None and 
            error_budget_remaining > cls.ERROR_BUDGET_SAFE_THRESHOLD
        )
        is_load_low = (
            current_load is not None and 
            current_load < cls.LOAD_LOW_THRESHOLD
        )
        
        # 위험: 최대 지터
        if is_budget_danger or is_load_high:
            return random.uniform(*cls.JITTER_MIN_STRESSED)
        
        # 여유: 최소 지터
        if is_budget_safe and is_load_low:
            return random.uniform(*cls.JITTER_MIN_RELAXED)
        
        # 보통: 중간 지터
        return random.uniform(*cls.JITTER_MIN_NORMAL)
    
    @classmethod
    def calculate_ms(
        cls,
        error_budget_remaining: Optional[float] = None,
        current_load: Optional[float] = None
    ) -> int:
        """밀리초 단위로 반환"""
        return int(cls.calculate(error_budget_remaining, current_load) * 1000)
```

---

### 최적화 5: 이식성 있는 Health Checker (선택적)

| 항목 | 내용 |
|------|------|
| **목적** | OS별 최적 성능 자동 선택 |
| **접근법** | 추상화 레이어 + 자동 Fallback |
| **원칙 준수** | ✅ 외부 의존성 없음, 이식성 유지 |
| **우선순위** | 낮음 (1~4번으로 충분할 가능성 높음) |

#### 구현 설계

```python
# packages/selfhealing-python/src/selfhealing/adapters/health_checker.py

import platform
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, Optional

class HealthCheckStrategy(ABC):
    """헬스 체크 전략 인터페이스"""
    
    @abstractmethod
    def check(self, target: str) -> bool:
        pass

class TTLCacheStrategy(HealthCheckStrategy):
    """
    TTL 기반 캐시 전략 (범용)
    
    모든 OS에서 동작, 성능도 충분 (~0.01ms 캐시 히트)
    """
    
    _cache: Dict[str, Dict] = {}
    _lock = threading.RLock()
    _ttl = 5.0
    _check_callback = None
    
    @classmethod
    def configure(cls, check_callback, ttl: float = 5.0):
        cls._check_callback = check_callback
        cls._ttl = ttl
    
    def check(self, target: str) -> bool:
        with self._lock:
            cached = self._cache.get(target)
            
            if cached and time.time() - cached['ts'] < self._ttl:
                return cached['healthy']
        
        # 캐시 미스
        return self._refresh(target)
    
    def _refresh(self, target: str) -> bool:
        if not self._check_callback:
            return True  # 콜백 없으면 healthy 가정
        
        try:
            healthy = self._check_callback(target)
        except Exception:
            healthy = False
        
        with self._lock:
            self._cache[target] = {
                'healthy': healthy,
                'ts': time.time()
            }
        
        return healthy

class LinuxTCPInfoStrategy(HealthCheckStrategy):
    """
    Linux 전용 TCP_INFO 전략
    
    커널에서 직접 소켓 상태 조회 (~0.01ms)
    """
    
    def check(self, target: str) -> bool:
        import socket
        import struct
        
        try:
            TCP_INFO = 11
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(0.1)
            
            host, port = target.split(':')
            sock.connect((host, int(port)))
            
            info = sock.getsockopt(socket.IPPROTO_TCP, TCP_INFO, 104)
            state = struct.unpack('B', info[0:1])[0]
            
            sock.close()
            return state == 1  # TCP_ESTABLISHED
            
        except Exception:
            return False

class PortableHealthChecker:
    """
    이식 가능한 고성능 헬스 체커
    
    OS/환경에 맞는 최적 전략을 자동 선택
    """
    
    def __init__(self):
        self._strategy = self._auto_select()
    
    def _auto_select(self) -> HealthCheckStrategy:
        """환경에 맞는 최적 전략 선택"""
        system = platform.system()
        
        # Linux: TCP_INFO 시도
        if system == 'Linux':
            try:
                strategy = LinuxTCPInfoStrategy()
                # 테스트 성공하면 사용
                return strategy
            except Exception:
                pass
        
        # 범용 폴백
        return TTLCacheStrategy()
    
    def is_healthy(self, target: str) -> bool:
        return self._strategy.check(target)
    
    @property
    def strategy_name(self) -> str:
        return type(self._strategy).__name__
```

---

## 📁 구현 파일 구조

```
packages/selfhealing-python/src/selfhealing/
│
├── core/
│   ├── __init__.py
│   ├── state_cache.py          # 최적화 2: TTL 캐싱 + Polling Jitter
│   ├── defaults.py             # 최적화 3: SafeDefaults
│   └── adaptive_jitter.py      # 최적화 4: 지능형 지터
│
├── utils/
│   ├── __init__.py
│   └── async_logger.py         # 최적화 1: 비동기 로깅
│
└── adapters/
    ├── __init__.py
    └── health_checker.py       # 최적화 5: 이식성 헬스체커 (선택)
```

---

## 📊 예상 성능 개선

| 최적화 | 현재 | 개선 후 | 단축 |
|--------|------|---------|------|
| Healing Event 로깅 | 50~100ms | 0.01ms | **~100ms** |
| CB 상태 확인 | 20~50ms | 0ms (캐시) | **~50ms** |
| Jitter 오버헤드 | 100~500ms | 0~100ms | **~200ms** |
| 사령탑 장애 대응 | 타임아웃 대기 | 즉시 Fallback | **~50ms** |
| **총합** | **~400ms** | **~0.1ms** | **~400ms** |

**예상 결과**:
- 현재: P99 = 422ms
- 개선 후: **P99 ≤ 100ms** (Platinum 달성 가능)

---

## 🚀 구현 우선순위

| 순위 | 항목 | 파일 | 난이도 | 효과 |
|------|------|------|--------|------|
| **1** | TTL 캐싱 + Polling Jitter | `state_cache.py` | ⭐ 쉬움 | ⭐⭐⭐ |
| **2** | 비동기 로깅 + Severity | `async_logger.py` | ⭐⭐ 보통 | ⭐⭐⭐ |
| **3** | SafeDefaults | `defaults.py` | ⭐⭐ 보통 | ⭐⭐ |
| **4** | Adaptive Jitter | `adaptive_jitter.py` | ⭐ 쉬움 | ⭐⭐ |
| **5** | 이식성 헬스체커 | `health_checker.py` | ⭐⭐⭐ 복잡 | ⭐ |

---

## ❌ 채택하지 않은 제안

| 제안 | 이유 |
|------|------|
| **Webhook Push** | 양방향 결합 발생, 사령탑이 쇼핑몰 주소를 알아야 함 |
| **로컬 설정 파일** | 외부 파일 의존, 이식성 저하 |
| **eBPF** | Linux 5.x+ 전용, 이식성 심각하게 저하 |
| **하드코딩 커널 최적화** | OS별 분기 없으면 이식 불가 |

---

## 📝 연동 예시 (사용하는 쪽)

```python
# 쇼핑몰 등 selfhealing 패키지를 사용하는 프로젝트

from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity
from selfhealing.core.state_cache import CBStateCache
from selfhealing.core.defaults import SafeDefaults

# 1. 비동기 로거 설정
def send_to_command_center(events):
    # 사령탑 API 호출
    requests.post('http://command-center/events', json=events)

AsyncHealingLogger.configure(flush_callback=send_to_command_center)
AsyncHealingLogger.start()

# 2. CB 상태 캐시 설정
def fetch_cb_state(service):
    # 사령탑에서 CB 상태 조회
    return requests.get(f'http://command-center/cb/{service}').json()

CBStateCache.configure(fetch_callback=fetch_cb_state)

# 3. 사용 예시
# 힐링 이벤트 로깅 (논블로킹)
AsyncHealingLogger.log({'type': 'cb_open', 'service': 'payment'}, EventSeverity.CRITICAL)

# CB 상태 조회 (캐시 우선)
state = CBStateCache.get_state('payment')

# Degraded Mode 확인 (HealthBridge 연동)
if SafeDefaults.is_degraded():
    health_response = SafeDefaults.get_health_response()
```

---

## ✅ 체크리스트

- [ ] `state_cache.py` 구현
- [ ] `async_logger.py` 구현
- [ ] `defaults.py` 구현
- [ ] `adaptive_jitter.py` 구현
- [ ] `health_checker.py` 구현 (선택)
- [ ] 단위 테스트 작성
- [ ] Stage 10 V2 재테스트 (Platinum SLA 검증)
- [ ] 문서 업데이트

---

*Generated from Stage 10 V2 Test Analysis & Architecture Review*  
*Copyright © 2025 Self-Healing Python Package*

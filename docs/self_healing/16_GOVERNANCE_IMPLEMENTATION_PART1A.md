# Governance Implementation Plan - Part 1-A

> API Rate Limit, 티어링 시스템, Emergency Mode 구현 계획

---

## 개요

Part 1에 이어지는 트래픽 제어 및 Load Shedding 구현 계획입니다.

| 순위 | 항목 | 중요도 | 예상 작업 |
|------|------|--------|----------|
| 3 | API Rate Limit (Hybrid Throttling) | 상 | 0.5일 |
| 4 | API 티어링 시스템 | 상 | 1일 |
| 5 | 티어링 장애 대비 (Defense-in-Depth) | 최상 | 0.5일 |
| 6 | Emergency Mode 고급 기능 | 중 | 1일 |

---

## 1. API Rate Limit

### 1.1 현재 상태

- Control API에 Rate Limit 없음
- 무제한 호출 가능 → 시스템 공격 수단 될 수 있음

### 1.2 목표 상태

Redis 기반 Rate Limit + 장애 대비 Hybrid Throttling 전략.

### 1.3 구현 계획

#### Phase 1: Rate Limit 미들웨어

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py`

```python
"""
Redis-based Rate Limiting for Control API.

Features:
- Redis INCR 기반 슬라이딩 윈도우
- Fail-Open: Redis 장애 시 통과 (+ 경고 로그)
- IP + User 복합 키
"""
import time
import logging
from typing import Optional, Tuple
from django.http import JsonResponse

logger = logging.getLogger(__name__)

# 기본 설정
DEFAULT_RATE_LIMIT = 100  # 요청/분
DEFAULT_WINDOW_SECONDS = 60
CONTROL_API_PATH_PREFIX = '/api/self-healing/'


class RateLimitMiddleware:
    """
    Control API Rate Limiting Middleware.
    
    Redis 장애 시 Fail-Open (통과) + 경고 로그.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        self.redis_client = self._get_redis_client()
        
    def _get_redis_client(self):
        """Redis 클라이언트 획득 (lazy)."""
        try:
            from django.core.cache import caches
            return caches['default'].client.get_client()
        except Exception:
            return None
    
    def __call__(self, request):
        # Control API만 대상
        if not request.path.startswith(CONTROL_API_PATH_PREFIX):
            return self.get_response(request)
        
        # Rate limit 체크
        is_allowed, remaining, reset_time = self._check_rate_limit(request)
        
        if not is_allowed:
            return self._rate_limit_response(remaining, reset_time)
        
        # 정상 처리
        response = self.get_response(request)
        
        # Rate limit 헤더 추가
        response['X-RateLimit-Remaining'] = str(remaining)
        response['X-RateLimit-Reset'] = str(reset_time)
        
        return response
    
    def _get_client_key(self, request) -> str:
        """Rate limit 키 생성 (IP + User)."""
        ip = self._get_client_ip(request)
        user_id = getattr(request.user, 'id', 'anonymous')
        return f"ratelimit:control_api:{ip}:{user_id}"
    
    def _get_client_ip(self, request) -> str:
        """클라이언트 IP 추출."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', 'unknown')
    
    def _check_rate_limit(self, request) -> Tuple[bool, int, int]:
        """Rate limit 체크."""
        if not self.redis_client:
            logger.warning("[RateLimit] Redis unavailable - Fail-Open")
            return (True, DEFAULT_RATE_LIMIT, 0)
        
        try:
            key = self._get_client_key(request)
            now = int(time.time())
            window_start = now - DEFAULT_WINDOW_SECONDS
            
            pipe = self.redis_client.pipeline()
            pipe.zadd(key, {str(now): now})
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zcard(key)
            pipe.expire(key, DEFAULT_WINDOW_SECONDS + 10)
            
            results = pipe.execute()
            current_count = results[2]
            
            remaining = max(0, DEFAULT_RATE_LIMIT - current_count)
            reset_time = now + DEFAULT_WINDOW_SECONDS
            
            if current_count > DEFAULT_RATE_LIMIT:
                return (False, 0, reset_time)
            
            return (True, remaining, reset_time)
            
        except Exception as e:
            logger.error(f"[RateLimit] Redis error - Fail-Open: {e}")
            return (True, DEFAULT_RATE_LIMIT, 0)
    
    def _rate_limit_response(self, remaining: int, reset_time: int):
        """429 응답 생성."""
        return JsonResponse(
            {
                "error": "rate_limit_exceeded",
                "message": "Too many requests to Control API",
                "retry_after": reset_time - int(time.time()),
            },
            status=429,
            headers={
                'X-RateLimit-Remaining': '0',
                'X-RateLimit-Reset': str(reset_time),
                'Retry-After': str(reset_time - int(time.time())),
            }
        )
```

#### Phase 2: 설정에 미들웨어 추가

```python
# myproject/settings.py
MIDDLEWARE = [
    # ... 기존 미들웨어 ...
    'selfhealing.api.django.rate_limit.RateLimitMiddleware',
]
```

### 1.4 지능형 하이브리드 제동 (Hybrid Throttling)

기존 Fail-Open은 위험합니다. Redis 장애 시 **L1 로컬 메모리 기반 비상 제동**으로 전환합니다.

#### 기존 vs 개선된 동작

| 상황 | 기존 (Fail-Open) | 개선 (Hybrid) | 이유 |
|------|-----------------|---------------|------|
| Redis 연결 실패 | 그냥 통과 ⚠️ | 로컬 메모리 기반 **엄격한 제한** | Cascading Failure 방지 |
| 로그 기록 | 일반 경고 | **Critical Alert + Audit Tagging** | Forensic 분석 + 즉시 개입 유도 |
| 복구 시점 | 자동 재개 | **Jitter 기반 점진적 복구** | Thundering Herd 방지 |

#### 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Hybrid Rate Limit Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Request ──▶ Redis Health? ──OK──▶ L2 (Redis) Rate Limit            │
│                    │                     │                           │
│                    │                     ▼                           │
│               UNHEALTHY            Normal Limit (100 req/min)        │
│                    │                                                 │
│                    ▼                                                 │
│         ┌─────────────────────┐                                      │
│         │  L1 Local Fallback  │                                      │
│         │  (Emergency Mode)   │                                      │
│         └─────────────────────┘                                      │
│                    │                                                 │
│                    ▼                                                 │
│         Strict Limit (10 req/min) ◀── 10배 엄격                      │
│                    │                                                 │
│                    ▼                                                 │
│         Shadow Audit: "REDIS_FAILURE_BYPASS"                         │
│                    │                                                 │
│                    ▼                                                 │
│         Prometheus: rate_limit_degraded_mode=1                       │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

#### L1 로컬 메모리 레이트 리미터

```python
"""L1 Local Memory Rate Limiter (Emergency Fallback)."""
import time
import threading
from collections import defaultdict
from typing import Tuple

EMERGENCY_RATE_LIMIT = 10  # 10 req/min per pod
EMERGENCY_WINDOW_SECONDS = 60


class LocalMemoryRateLimiter:
    """L1 로컬 메모리 기반 레이트 리미터."""
    
    def __init__(
        self,
        max_requests: int = EMERGENCY_RATE_LIMIT,
        window_seconds: int = EMERGENCY_WINDOW_SECONDS
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()
    
    def is_allowed(self, key: str) -> Tuple[bool, int]:
        now = time.time()
        window_start = now - self.window_seconds
        
        with self._lock:
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts > window_start
            ]
            
            current_count = len(self._requests[key])
            
            if current_count >= self.max_requests:
                return (False, 0)
            
            self._requests[key].append(now)
            remaining = self.max_requests - current_count - 1
            
            return (True, remaining)
```

### 1.5 메트릭

```python
from prometheus_client import Counter, Gauge

rate_limit_exceeded_total = Counter(
    'selfhealing_rate_limit_exceeded_total',
    'Rate limit exceeded count',
    ['client_ip', 'mode']
)

rate_limit_degraded_mode = Gauge(
    'selfhealing_rate_limit_degraded_mode',
    'Rate limit operating in degraded mode (1=yes, 0=no)'
)

rate_limit_failover_total = Counter(
    'selfhealing_rate_limit_failover_total',
    'Number of times rate limit failed over to local memory'
)
```

---

## 2. API 티어링 시스템 (Criticality-Based Load Shedding)

### 2.1 개요

비상 모드 시 모든 API를 동일하게 제한하는 대신, **중요도 기반 차등 제한**을 적용합니다.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    API Tiering System                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │
│  │   Tier 1    │  │   Tier 2    │  │   Tier 3    │                  │
│  │  Critical   │  │  Standard   │  │ Non-Essential│                  │
│  │             │  │             │  │             │                  │
│  │  50% 허용   │  │  10% 허용   │  │   즉시 차단  │                  │
│  └─────────────┘  └─────────────┘  └─────────────┘                  │
│        │                │                │                           │
│        ▼                ▼                ▼                           │
│  자가치유 액션     설정 변경       대시보드 통계                     │
│  결제 API         로그 조회       핑/헬스체크                        │
│  보안 킬스위치    DLQ 리플레이    메트릭 조회                        │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 티어 정의 레지스트리

```python
@dataclass
class TierDefinition:
    """티어 정의."""
    id: str                    # 고유 ID (예: "critical")
    name: str                  # 표시 이름 (예: "최우선 보호")
    multiplier: float          # 비상 모드 배율 (0.0 ~ 1.0)
    priority: int              # 우선순위 (높을수록 중요)
    description: str = ""
    color: str = "#000000"


DEFAULT_TIER_DEFINITIONS = [
    TierDefinition(
        id="critical",
        name="Mission Critical",
        multiplier=0.5,
        priority=100,
        description="장애 시에도 반드시 동작해야 하는 핵심 API",
        color="#FF0000"
    ),
    TierDefinition(
        id="standard",
        name="Operational",
        multiplier=0.1,
        priority=50,
        description="일반 운영 API",
        color="#FFA500"
    ),
    TierDefinition(
        id="non_essential",
        name="Non-Essential",
        multiplier=0.0,
        priority=10,
        description="비필수 API (Load Shedding 대상)",
        color="#808080"
    ),
]
```

### 2.3 동적 매핑 엔진

```python
@dataclass
class TierMapping:
    """API 경로 → 티어 매핑."""
    pattern: str           # 경로 패턴 (RegEx 또는 Wildcard)
    tier_id: str           # 매핑할 티어 ID
    pattern_type: str      # "regex" | "wildcard" | "exact"
    priority: int = 0      # 매핑 우선순위
    description: str = ""


DEFAULT_TIER_MAPPINGS = [
    TierMapping(
        pattern="/api/self-healing/control/allow/",
        tier_id="critical",
        pattern_type="exact",
        priority=100,
        description="자가치유 허용 액션"
    ),
    TierMapping(
        pattern="/api/self-healing/config/*",
        tier_id="standard",
        pattern_type="wildcard",
        priority=50,
        description="설정 변경 API"
    ),
    TierMapping(
        pattern="/api/self-healing/dashboard/*",
        tier_id="non_essential",
        pattern_type="wildcard",
        priority=10,
        description="대시보드 API"
    ),
]
```

---

## 3. 티어링 시스템 장애 대비 (Defense-in-Depth) ✅

> **핵심 원칙**: "티어링이 죽어도 결제는 살린다"

### 3.1 방어 계층 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│              Tiering Fallback Decision Tree                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  Request Path ──▶ Tiering Circuit Breaker OPEN?                     │
│                          │                                           │
│                   YES    │    NO                                     │
│                    ▼     │     ▼                                     │
│         ┌────────────────┤  ┌────────────────┐                       │
│         │ Bypass to      │  │ Dynamic Mapping │                      │
│         │ Static+Default │  │ Engine (RegEx)  │                      │
│         └────────────────┘  └────────────────┘                       │
│                    │               │                                 │
│                    │          Exception?                             │
│                    │           YES │ NO                              │
│                    │               │  ▼                              │
│                    ▼               │  Normal Tier Result             │
│         Static Critical Path? ─────┘                                 │
│              │                                                       │
│        YES   │    NO                                                 │
│         ▼    │     ▼                                                 │
│   ┌──────────┐ ┌──────────────┐                                      │
│   │ Critical │ │ Fail-Closed  │                                      │
│   │  (보호)  │ │(Non-Essential)│                                     │
│   └──────────┘ └──────────────┘                                      │
│                                                                      │
│  결론: 핵심 API는 항상 보호, 알 수 없는 건 보수적으로               │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 업계 표준 비교

| 회사/프레임워크 | 전략 | 핵심 원칙 |
|----------------|------|----------|
| **Netflix (Hystrix)** | Circuit Breaker + Static Fallback | "Everything fails eventually" |
| **AWS (Cell Architecture)** | Static + Dynamic 혼합 | 장애 격리 (Blast Radius 제한) |
| **Google SRE** | Fail-Safe Defaults | 불확실하면 보수적으로 |
| **Cloudflare** | 다단계 Rate Limit | L1 Edge → L2 Origin → L3 Static |

### 3.3 장애 대응 매트릭스

| 상황 | 원인 | 대응 로직 (Fallback) | 기대 효과 |
|------|------|---------------------|----------|
| 설정 누락 | 운영자 실수 | Default Tier 적용 (non_essential) | 비정상적 허용 방지 |
| 엔진 오류 | RegEx 버그 등 | Static Path Lookup | 핵심 기능 영구 보호 |
| 성능 저하 | 과도한 매핑 규칙 | Bypass to Phase 1 Rate Limit | 응답 지연 방지 |

### 3.4 L1 정적 Critical 경로 (하드코딩 최후의 방어선)

> **절대 변경 불가** - 코드 배포 필요

```python
"""
L1 Static Critical Paths.

이 목록은 DB/Redis 없이도 항상 Critical로 처리되는 경로입니다.
AWS Lambda의 "Fallback of last resort" 패턴과 동일합니다.
"""

# 불변 집합 (frozenset으로 런타임 수정 방지)
STATIC_CRITICAL_PATHS = frozenset([
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
    "/api/auth/token/",
])

# Prefix 매칭용 (tuple로 startswith 최적화)
STATIC_CRITICAL_PREFIXES = (
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
)
```

### 3.5 폴백 사유 추적 (Shadow Audit)

```python
class TierFallbackReason(Enum):
    """폴백 발생 사유 (Shadow Audit용)."""
    NONE = "none"
    CONFIG_MISSING = "config_missing"       # 매핑 없음
    ENGINE_ERROR = "engine_error"           # RegEx 예외
    ENGINE_TIMEOUT = "engine_timeout"       # 50ms 초과
    CIRCUIT_OPEN = "circuit_open"           # CB 열림
    STATIC_PATH_MATCH = "static_path_match" # 정적 경로 매칭


@dataclass
class TierResult:
    """티어 결정 결과."""
    tier_id: str
    is_fallback: bool
    fallback_reason: TierFallbackReason
    latency_ms: float
```

### 3.6 티어링 전용 Circuit Breaker

> **"메타 서킷 브레이커"**: 티어링 엔진 자체의 장애를 감지

```python
class TieringCircuitBreaker:
    """
    티어링 엔진 전용 Mini Circuit Breaker.
    
    RegEx 연산이 느려지거나 에러율이 높으면 바이패스.
    Envoy Proxy의 라우팅 규칙 바이패스 패턴과 동일.
    """
    
    FAILURE_THRESHOLD = 5       # 5회 연속 실패 시 OPEN
    TIMEOUT_MS = 50             # 50ms 초과 시 slow로 카운트
    SLOW_THRESHOLD = 10         # 10회 slow 시 OPEN
    HALF_OPEN_DELAY_SEC = 30    # 30초 후 HALF_OPEN
    
    def __init__(self):
        self._state = "CLOSED"
        self._failure_count = 0
        self._slow_count = 0
        self._last_failure_time: Optional[float] = None
    
    @property
    def is_open(self) -> bool:
        if self._state == "OPEN":
            if time.time() - self._last_failure_time > self.HALF_OPEN_DELAY_SEC:
                self._state = "HALF_OPEN"
                return False
            return True
        return False
    
    def record_success(self, latency_ms: float):
        """성공 기록."""
        if self._state == "HALF_OPEN":
            self._state = "CLOSED"
            logger.info("[TieringCB] CLOSED - recovered")
        
        self._failure_count = 0
        
        if latency_ms > self.TIMEOUT_MS:
            self._slow_count += 1
            if self._slow_count >= self.SLOW_THRESHOLD:
                self._trip("slow_responses")
        else:
            self._slow_count = 0
    
    def record_failure(self, error: Exception):
        """실패 기록."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._failure_count >= self.FAILURE_THRESHOLD:
            self._trip(f"failures: {error}")
    
    def _trip(self, reason: str):
        """서킷 OPEN."""
        self._state = "OPEN"
        logger.critical(f"[TieringCB] OPEN - {reason}")
```

### 3.7 통합된 TierMappingEngine (Defense-in-Depth)

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/tier_engine.py`

```python
"""
Tiering System with Defense-in-Depth Fallback.

Layer 1: Static Critical Paths (하드코딩 - 최후의 방어선)
Layer 2: Default Tier (설정 누락 시)
Layer 3: Circuit Breaker Bypass (성능 문제 시)

Reference:
- Netflix Hystrix: "Fallback of last resort"
- Google SRE: "Fail-Safe Defaults"
- AWS Lambda: 환경변수 기본값 폴백
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List
import re
import fnmatch

logger = logging.getLogger(__name__)


# ============================================================
# L1: 정적 Critical 경로 (절대 변경 불가 - 코드 배포 필요)
# ============================================================
STATIC_CRITICAL_PATHS = frozenset([
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
    "/api/auth/token/",
])

STATIC_CRITICAL_PREFIXES = (
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
)


class TierFallbackReason(Enum):
    """폴백 발생 사유 (Shadow Audit용)."""
    NONE = "none"
    CONFIG_MISSING = "config_missing"
    ENGINE_ERROR = "engine_error"
    ENGINE_TIMEOUT = "engine_timeout"
    CIRCUIT_OPEN = "circuit_open"
    STATIC_PATH_MATCH = "static_path_match"


@dataclass
class TierResult:
    """티어 결정 결과."""
    tier_id: str
    is_fallback: bool
    fallback_reason: TierFallbackReason
    latency_ms: float


@dataclass
class TierMapping:
    """API 경로 → 티어 매핑."""
    pattern: str
    tier_id: str
    pattern_type: str  # "regex" | "wildcard" | "exact"
    priority: int = 0
    
    _compiled_regex: Optional[re.Pattern] = None
    
    def matches(self, path: str) -> bool:
        """경로 매칭 확인."""
        if self.pattern_type == "exact":
            return path == self.pattern
        elif self.pattern_type == "wildcard":
            return fnmatch.fnmatch(path, self.pattern)
        elif self.pattern_type == "regex":
            if self._compiled_regex is None:
                self._compiled_regex = re.compile(self.pattern)
            return bool(self._compiled_regex.match(path))
        return False


class TieringCircuitBreaker:
    """티어링 엔진 전용 Circuit Breaker."""
    
    FAILURE_THRESHOLD = 5
    TIMEOUT_MS = 50
    SLOW_THRESHOLD = 10
    HALF_OPEN_DELAY_SEC = 30
    
    def __init__(self):
        self._state = "CLOSED"
        self._failure_count = 0
        self._slow_count = 0
        self._last_failure_time: Optional[float] = None
    
    @property
    def is_open(self) -> bool:
        if self._state == "OPEN":
            if self._last_failure_time and \
               time.time() - self._last_failure_time > self.HALF_OPEN_DELAY_SEC:
                self._state = "HALF_OPEN"
                return False
            return True
        return False
    
    def record_success(self, latency_ms: float):
        if self._state == "HALF_OPEN":
            self._state = "CLOSED"
            logger.info("[TieringCB] CLOSED - recovered")
        
        self._failure_count = 0
        
        if latency_ms > self.TIMEOUT_MS:
            self._slow_count += 1
            if self._slow_count >= self.SLOW_THRESHOLD:
                self._trip("slow_responses")
        else:
            self._slow_count = 0
    
    def record_failure(self, error: Exception):
        self._failure_count += 1
        self._last_failure_time = time.time()
        
        if self._failure_count >= self.FAILURE_THRESHOLD:
            self._trip(f"failures: {error}")
    
    def _trip(self, reason: str):
        self._state = "OPEN"
        logger.critical(f"[TieringCB] OPEN - {reason}")
        self._record_metrics()
    
    def _record_metrics(self):
        try:
            from selfhealing.services.metrics import tiering_circuit_open
            tiering_circuit_open.set(1)
        except Exception:
            pass


class TierMappingEngine:
    """
    티어 매핑 엔진 (Defense-in-Depth 적용).
    
    Fallback Chain:
    1. Dynamic Mapping (DB/Redis에서 로드)
    2. Static Critical Path Check (하드코딩)
    3. Default Tier (Fail-Closed: non_essential)
    """
    
    # L2: 기본 티어 (설정 누락 시) - Fail-Closed
    DEFAULT_TIER = "non_essential"
    
    # L1 Static Critical 경로용 티어
    STATIC_CRITICAL_TIER = "critical"
    
    def __init__(self, mappings: Optional[List[TierMapping]] = None):
        self.circuit_breaker = TieringCircuitBreaker()
        self._dynamic_mappings: List[TierMapping] = mappings or []
        # 우선순위로 정렬
        self._dynamic_mappings.sort(key=lambda m: -m.priority)
    
    def get_tier(self, path: str) -> TierResult:
        """
        경로에 대한 티어 결정.
        
        Fallback Chain:
        1. Circuit Breaker OPEN → Static + Default
        2. Dynamic Mapping 시도
        3. 실패 시 Static Check
        4. 최종 Default (Fail-Closed)
        """
        start_time = time.perf_counter()
        
        # Circuit Breaker OPEN → 바이패스
        if self.circuit_breaker.is_open:
            return self._static_or_default(
                path, 
                TierFallbackReason.CIRCUIT_OPEN,
                start_time
            )
        
        try:
            # 동적 매핑 시도
            tier_id = self._evaluate_dynamic_mappings(path)
            
            if tier_id:
                latency_ms = (time.perf_counter() - start_time) * 1000
                self.circuit_breaker.record_success(latency_ms)
                
                return TierResult(
                    tier_id=tier_id,
                    is_fallback=False,
                    fallback_reason=TierFallbackReason.NONE,
                    latency_ms=latency_ms,
                )
            
            # 동적 매핑 없음 → Static 체크
            return self._static_or_default(
                path,
                TierFallbackReason.CONFIG_MISSING,
                start_time
            )
            
        except Exception as e:
            self.circuit_breaker.record_failure(e)
            self._log_fallback_audit(path, e)
            
            return self._static_or_default(
                path,
                TierFallbackReason.ENGINE_ERROR,
                start_time
            )
    
    def _static_or_default(
        self, 
        path: str, 
        reason: TierFallbackReason,
        start_time: float
    ) -> TierResult:
        """
        L1 Static Check → L2 Default.
        
        핵심: Static Critical Path는 항상 보호!
        """
        latency_ms = (time.perf_counter() - start_time) * 1000
        
        # L1: Static Critical Path 체크
        if self._is_static_critical(path):
            return TierResult(
                tier_id=self.STATIC_CRITICAL_TIER,
                is_fallback=True,
                fallback_reason=TierFallbackReason.STATIC_PATH_MATCH,
                latency_ms=latency_ms,
            )
        
        # L2: Default (Fail-Closed)
        return TierResult(
            tier_id=self.DEFAULT_TIER,
            is_fallback=True,
            fallback_reason=reason,
            latency_ms=latency_ms,
        )
    
    def _is_static_critical(self, path: str) -> bool:
        """L1 Static Critical 경로 체크."""
        if path in STATIC_CRITICAL_PATHS:
            return True
        return path.startswith(STATIC_CRITICAL_PREFIXES)
    
    def _evaluate_dynamic_mappings(self, path: str) -> Optional[str]:
        """동적 매핑 평가 (우선순위 순)."""
        for mapping in self._dynamic_mappings:
            if mapping.matches(path):
                return mapping.tier_id
        return None
    
    def _log_fallback_audit(self, path: str, error: Exception):
        """Shadow Audit - 폴백 발생 기록."""
        try:
            from selfhealing.audit import log_config_change
            
            log_config_change(
                config_type="tiering_fallback",
                changes={
                    "path": path,
                    "error": str(error),
                    "fallback_tier": self.DEFAULT_TIER,
                },
                changed_by="system",
                reason="Tiering engine fallback activated",
                metadata={
                    "severity": "warning",
                    "tag": "TIERING_FALLBACK",
                    "circuit_state": self.circuit_breaker._state,
                }
            )
        except Exception as audit_error:
            logger.error(f"[Tiering] Audit failed: {audit_error}")
    
    def reload_mappings(self, mappings: List[TierMapping]):
        """동적 매핑 리로드 (Hot Reload)."""
        self._dynamic_mappings = sorted(mappings, key=lambda m: -m.priority)
        logger.info(f"[Tiering] Reloaded {len(mappings)} mappings")


# 싱글톤
_engine: Optional[TierMappingEngine] = None


def get_tier_mapping_engine() -> TierMappingEngine:
    """티어 매핑 엔진 싱글톤 획득."""
    global _engine
    if _engine is None:
        _engine = TierMappingEngine()
    return _engine
```

### 3.8 메트릭

```python
from prometheus_client import Counter, Gauge, Histogram

# 티어링 폴백 발생 횟수
tiering_fallback_total = Counter(
    'selfhealing_tiering_fallback_total',
    'Tiering fallback count',
    ['reason', 'tier']  # reason: config_missing, engine_error, circuit_open, static_path_match
)

# 티어링 Circuit Breaker 상태
tiering_circuit_open = Gauge(
    'selfhealing_tiering_circuit_open',
    'Tiering circuit breaker state (1=open, 0=closed)'
)

# 티어링 엔진 레이턴시
tiering_latency_seconds = Histogram(
    'selfhealing_tiering_latency_seconds',
    'Tiering engine latency',
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1]
)
```

### 3.9 Grafana 알림 규칙

```yaml
# Tiering Fallback Alert
- alert: TieringFallbackHigh
  expr: rate(selfhealing_tiering_fallback_total[5m]) > 0.1
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "Tiering fallback rate is high"
    description: |
      Tiering engine is falling back frequently.
      Check RegEx patterns and dynamic mapping configuration.

# Tiering Circuit Breaker Open
- alert: TieringCircuitBreakerOpen
  expr: selfhealing_tiering_circuit_open == 1
  for: 1m
  labels:
    severity: critical
  annotations:
    summary: "Tiering Circuit Breaker is OPEN"
    description: |
      Tiering engine has failed repeatedly.
      - Static critical paths are still protected
      - All other paths fall back to non_essential tier
      - Check RegEx performance and error logs
```

---

## 4. Emergency Mode 고급 기능

### 4.1 Emergency Level (단계별 비상 모드)

```python
class EmergencyLevel(Enum):
    """비상 모드 레벨."""
    NORMAL = 0       # 정상 운영
    LEVEL_1 = 1      # 경미한 장애 - Tier 3만 차단
    LEVEL_2 = 2      # 중간 장애 - Tier 2, 3 차단
    LEVEL_3 = 3      # 심각한 장애 - Tier 1만 허용 (50%)


EMERGENCY_LEVEL_RULES = {
    EmergencyLevel.NORMAL: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 1.0
    },
    EmergencyLevel.LEVEL_1: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 0.0
    },
    EmergencyLevel.LEVEL_2: {
        "critical": 1.0,
        "standard": 0.1,
        "non_essential": 0.0
    },
    EmergencyLevel.LEVEL_3: {
        "critical": 0.5,
        "standard": 0.0,
        "non_essential": 0.0
    },
}
```

### 4.2 Recovery Gate (복구 안정화)

```python
@dataclass
class RecoveryGateConfig:
    """복구 게이트 설정."""
    stabilization_period_seconds: int = 300  # 5분
    require_metrics_stable: bool = True
    cpu_threshold_percent: float = 80.0
    error_rate_threshold: float = 0.05
    gradual_recovery: bool = True
    level_step_delay_seconds: int = 60
```

### 4.3 Manual Emergency API

```python
class ManualEmergencyAPI(APIView):
    """POST /api/self-healing/emergency/trigger/"""
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def post(self, request):
        level = request.data.get('level', 'LEVEL_1')
        reason = request.data.get('reason', '')
        duration_minutes = request.data.get('duration_minutes', 30)
        
        if not reason:
            return Response({"error": "reason is required"}, status=400)
        
        emergency_manager = get_emergency_manager()
        emergency_manager.activate_manual(
            level=EmergencyLevel[level],
            reason=reason,
            duration_minutes=duration_minutes,
            activated_by=request.user.username,
        )
        
        return Response({
            "status": "activated",
            "level": level,
            "activated_by": request.user.username,
        })
```

---

## API 엔드포인트 요약

### Rate Limit & Emergency

| 엔드포인트 | 메서드 | 설명 | 권한 |
|-----------|--------|------|------|
| `/api/self-healing/emergency/trigger/` | POST | 수동 비상 모드 활성화 | Admin |
| `/api/self-healing/emergency/release/` | POST | 수동 비상 모드 해제 | Admin |
| `/api/self-healing/emergency/status/` | GET | 현재 비상 모드 상태 | Viewer |
| `/api/self-healing/config/emergency/` | GET/PUT | Emergency 설정 조회/변경 | Admin |

### Tier Configuration

| 엔드포인트 | 메서드 | 설명 | 권한 |
|-----------|--------|------|------|
| `/api/self-healing/config/tiers/` | GET/PUT | 티어 정의 조회/변경 | Admin |
| `/api/self-healing/config/tier-mappings/` | GET/PUT | 티어 매핑 조회/변경 | Admin |
| `/api/self-healing/config/tier-overrides/` | GET/PUT | 티어 오버라이드 조회/변경 | Admin |
| `/api/self-healing/config/tiers/dry-run/` | POST | 티어 설정 시뮬레이션 | Operator |

---

## 체크리스트

### Phase 1: API Rate Limit (Hybrid Throttling) ✅
- [x] `LocalMemoryRateLimiter` 생성 (L1 비상 리미터)
- [x] `RedisHealthChecker` 생성 (Mini Circuit Breaker)
- [x] `HybridRateLimitMiddleware` 생성 (통합 미들웨어)
- [x] settings.py에 미들웨어 추가
- [x] Prometheus 메트릭 추가
- [x] 테스트 파일 작성 (`test_rate_limit.py`)

### Phase 2: API 티어링 시스템 ✅
- [x] `TierDefinition` 데이터클래스 생성
- [x] `TierMapping` 데이터클래스 생성 (RegEx/Wildcard)
- [x] `TierConfigValidator` 생성 (Safe Boundary)
- [x] 기본 템플릿 정의
- [x] 테스트 작성 (`test_tiering.py`)

### Phase 3: 티어링 장애 대비 (Defense-in-Depth) ✅
- [x] `STATIC_CRITICAL_PATHS` 정적 리스트
- [x] `TierFallbackReason` Enum
- [x] `TieringCircuitBreaker` 생성
- [x] `TierMappingEngine` with Fallback Chain
- [x] Shadow Audit 연동
- [x] Prometheus 메트릭 추가
- [x] 테스트 작성 (`test_tier_fallback.py`)

### Phase 4: Emergency Mode 고급 기능 ✅
- [x] `EmergencyLevel` Enum 생성
- [x] `GracefulDegradationManager` 생성
- [x] `RecoveryGate` 생성
- [x] `ManualEmergencyAPI` 구현
- [x] Integration 테스트 작성 (`test_emergency_mode.py`)

---

## 관련 문서

- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Part 1 (RBAC, 환경변수 Audit)
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Part 2 (Config Versioning, Fail-Safe)
- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 보안
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 권한 테이블

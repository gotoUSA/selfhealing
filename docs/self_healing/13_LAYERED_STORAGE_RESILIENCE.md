# Layered Storage Resilience (L1+L2 저장소 복원력)

> **핵심 원칙**: "외부 인프라가 통째로 마비되어도, 각 서버 노드 내에서 독립적으로 생존하여 자가 치유를 계속한다."

## 📋 목차

1. [개요](#1-개요)
2. [아키텍처](#2-아키텍처)
3. [L2 장애 시: 우아한 고립 (Graceful Isolation)](#3-l2-장애-시-우아한-고립-graceful-isolation)
4. [L1 콜드 스타트 보호 (Cold Start Protection)](#4-l1-콜드-스타트-보호-cold-start-protection)
5. [설정 오류 시: 지능형 자가 복구 (Intelligent Fallback)](#5-설정-오류-시-지능형-자가-복구-intelligent-fallback)
6. [드리프트 복구 (Drift Reconciliation)](#6-드리프트-복구-drift-reconciliation)
7. [장애 모드 로깅 (Shadow Logging)](#7-장애-모드-로깅-shadow-logging)
8. [구현 계획](#8-구현-계획)
9. [테스트 계획](#9-테스트-계획)
10. [메트릭 및 모니터링](#10-메트릭-및-모니터링)

---

## 1. 개요

### 1.1 배경

Self-Healing 시스템의 Circuit Breaker 상태 저장소는 다음 모드를 지원합니다:

| 모드 | 환경변수 | 설명 |
|------|----------|------|
| **Memory** (기본) | `SELFHEALING_STORAGE=memory` | L1만 사용, 설치 즉시 작동 |
| **Layered** | `SELFHEALING_STORAGE=layered` | L1 + L2, 분산 환경 지원 |
| **Django** | `SELFHEALING_STORAGE=django` | DB 직접 사용 (opt-in) |

### 1.2 문제 정의

Layered Storage(L1+L2)에서 발생할 수 있는 장애 시나리오:

```
┌──────────────────────────────────────────────────────────────┐
│                    장애 시나리오                              │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   1. L2 장애          Redis/DB가 응답하지 않음               │
│      └─ 위험: 동기화 대기로 메인 스레드 블로킹               │
│                                                              │
│   2. L1 콜드 스타트    서버 재시작으로 메모리 비어있음        │
│      └─ 위험: L2 로드 전 공백기에 잘못된 판정                │
│                                                              │
│   3. 설정 오류         Redis 연결 정보 누락                  │
│      └─ 위험: 시스템 부팅 실패 (Downtime)                    │
│                                                              │
│   4. 상태 드리프트     L2 장애 동안 L1만 업데이트됨          │
│      └─ 위험: L2 복구 후 데이터 불일치                       │
│                                                              │
│   5. Thundering Herd   L2 복구 시 모든 Pod가 동시 쓰기       │
│      └─ 위험: L2 과부하로 재장애                             │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 1.3 설계 원칙

| 원칙 | 설명 |
|------|------|
| **Fail-Fast** | 느린 실패보다 빠른 실패가 안전 |
| **Graceful Degradation** | 장애 시 기능 저하 모드로 계속 동작 |
| **Self-Healing** | 장애 복구 후 자동으로 일관성 회복 |
| **Observable** | 모든 장애 상황을 로그/메트릭으로 추적 가능 |

---

## 2. 아키텍처

```
┌──────────────────────────────────────────────────────────────┐
│                 Layered Storage Architecture                  │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   Application (Pod A, B, C...)                               │
│         │                                                    │
│         ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐   │
│   │           LayeredCircuitBreakerStateRepository       │   │
│   │                                                      │   │
│   │   ┌─────────────┐         ┌─────────────────────┐   │   │
│   │   │ L1 (Memory) │ ──────▶ │ L2 (Redis/Django)   │   │   │
│   │   │             │         │                     │   │   │
│   │   │ • 0.01ms    │  async  │ • 1~200ms           │   │   │
│   │   │ • 즉시 판정 │  sync   │ • 분산 공유         │   │   │
│   │   │ • 장애 내성 │         │ • 영속성            │   │   │
│   │   └─────────────┘         └─────────────────────┘   │   │
│   │         │                         │                  │   │
│   │         ▼                         ▼                  │   │
│   │   ┌─────────────┐         ┌─────────────────────┐   │   │
│   │   │ Shadow Log  │         │ Drift Reconciler    │   │   │
│   │   │ (L2 실패시) │         │ (L2 복구시)         │   │   │
│   │   └─────────────┘         └─────────────────────┘   │   │
│   │                                                      │   │
│   └─────────────────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. L2 장애 시: 우아한 고립 (Graceful Isolation)

### 3.1 문제

L2(Redis/DB)가 네트워크 장애나 락으로 응답하지 않을 때, 동기화 로직이 메인 스레드를 블로킹하면 안 됩니다.

### 3.2 해결책: 가변 타임아웃 + 비동기 배압

```python
@dataclass
class L2TimeoutConfig:
    """어댑터별 타임아웃 설정."""
    
    redis_timeout_ms: int = 50      # Redis: 빠름, 50ms면 충분
    database_timeout_ms: int = 200  # DB: 부하 시 느려짐, 200ms 필요
    fallback_timeout_ms: int = 100  # 알 수 없는 어댑터
    
    def get_timeout_for_adapter(self, adapter_type: str) -> float:
        """어댑터 타입에 따른 타임아웃 반환 (초 단위)."""
        timeouts = {
            "redis": self.redis_timeout_ms,
            "database": self.database_timeout_ms,
            "django": self.database_timeout_ms,
        }
        return timeouts.get(adapter_type, self.fallback_timeout_ms) / 1000.0
```

### 3.3 동작 흐름

```
┌──────────────────────────────────────────────────────────────┐
│               L2 타임아웃 처리 흐름                           │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   L2 조회/쓰기 요청                                          │
│         │                                                    │
│         ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  타임아웃 내 응답?                                   │   │
│   │  (Redis: 50ms, DB: 200ms)                           │   │
│   └─────────────────────────────────────────────────────┘   │
│         │                                                    │
│    Yes  │  No (타임아웃)                                     │
│         │         │                                          │
│         ▼         ▼                                          │
│   정상 처리    ┌─────────────────────────────────────────┐   │
│               │  1. 즉시 포기 (Fail-Fast)                │   │
│               │  2. L1 데이터만으로 판정                 │   │
│               │  3. Shadow Log에 실패 기록               │   │
│               │  4. 메트릭 증가 (l2_timeout_total)       │   │
│               │  5. 로그: "L2 unreachable, isolated L1"  │   │
│               └─────────────────────────────────────────┘   │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 3.4 업계 사례

| 라이브러리 | 타임아웃 전략 |
|-----------|-------------|
| Netflix Hystrix | 기본 1000ms, 권장 100ms 이하 |
| Resilience4j | TimeLimiter 별도 레이어, 50~100ms |
| AWS SDK | connectTimeout + socketTimeout 필수 |

---

## 4. L1 콜드 스타트 보호 (Cold Start Protection)

### 4.1 문제

서버 재시작 시 L1 메모리가 비어있고, L2에서 데이터 로드 전까지 "공백기"가 존재합니다.

### 4.2 해결책: 보수적 기본값 (Conservative Default)

```python
def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
    """데이터가 없으면 안전한 기본값으로 생성."""
    if service_name not in self._storage:
        state = CircuitBreakerStateData(
            state=CircuitBreakerStateEnum.CLOSED.value,  # 안전: 트래픽 허용
            failure_count=0,
            success_count=0,
            # ...
        )
        self._storage[service_name] = state
    return self._storage[service_name]
```

### 4.3 현재 상태

**이미 구현됨:**
- Circuit Breaker: `CLOSED` (트래픽 허용)
- Gate Fault Detector: `HEALTHY` (자동화 허용)

**설계 철학:**
> "일단 허용, 문제 발생 시 즉시 감지"가 "일단 차단"보다 가용성 측면에서 안전

---

## 5. 설정 오류 시: 지능형 자가 복구 (Intelligent Fallback)

### 5.1 문제

`SELFHEALING_STORAGE=layered` 설정했지만 Redis 연결 정보 누락 등 설정 미스 발생 가능.

### 5.2 해결책: 부트스트랩 유효성 검사

```python
def _create_layered_repository(self, repo_type: str) -> Any:
    """L2 연결 실패 시 자동으로 Memory 모드로 폴백."""
    l2_repo = None
    
    try:
        # 1. 어댑터 import 시도
        from selfhealing.adapters.redis import RedisCircuitBreakerStateRepository
        l2_repo = RedisCircuitBreakerStateRepository()
        
        # 2. Ping 테스트 (연결 확인)
        l2_repo.ping()
        
        logger.info("[ServiceFactory] L2=Redis connected successfully")
        
    except ImportError:
        logger.warning(
            "[ServiceFactory] Redis adapter not available. "
            "Running in Memory-only mode."
        )
    except Exception as e:
        logger.warning(
            f"[ServiceFactory] L2 connection failed: {e}. "
            f"Switching to Memory-only mode for safety."
        )
        l2_repo = None
    
    return LayeredCircuitBreakerStateRepository(l2_repo=l2_repo)
```

### 5.3 현재 상태

**이미 구현됨:**
- Import 실패 → Memory 폴백
- 연결 실패 → Memory 폴백 + 경고 로그

---

## 6. 드리프트 복구 (Drift Reconciliation)

### 6.1 문제

L2 장애 동안 L1만 업데이트되면, L2 복구 후 L1과 L2의 상태가 불일치합니다.

```
시간 →  T1        T2        T3        T4
        │         │         │         │
L2:    CLOSED ───(장애)───────────── CLOSED

L1-A:  CLOSED → OPEN ─────────────→ OPEN   (Pod A)
L1-B:  CLOSED ─────────────────────→ CLOSED (Pod B)

T4에서 L2 복구 시: Pod A와 Pod B가 충돌!
```

### 6.2 해결책: Most Restrictive Wins

서킷브레이커는 **OPEN이 더 중요한 신호**입니다:

```python
class DriftReconciler:
    """L2 복구 시 상태 드리프트 해결."""
    
    STATE_PRIORITY = {
        "open": 3,       # 가장 제한적 (우선)
        "half_open": 2,
        "closed": 1,     # 가장 허용적
    }
    
    def reconcile(
        self, 
        service_name: str,
        l1_state: str, 
        l2_state: str,
        l1_updated_at: datetime,
        l2_updated_at: datetime,
    ) -> str:
        """
        드리프트 해결 전략:
        1. 더 제한적인 상태가 우선 (Most Restrictive Wins)
        2. 같은 레벨이면 더 최신 타임스탬프가 우선
        """
        l1_priority = self.STATE_PRIORITY.get(l1_state, 0)
        l2_priority = self.STATE_PRIORITY.get(l2_state, 0)
        
        if l1_priority > l2_priority:
            return l1_state  # L1이 더 제한적 → L2에 전파
        elif l2_priority > l1_priority:
            return l2_state  # L2가 더 제한적 → L1에 전파
        else:
            # 같은 레벨: 타임스탬프 비교
            return l1_state if l1_updated_at > l2_updated_at else l2_state
```

### 6.3 Thundering Herd 방지: Reconciliation Jitter

L2 복구 시 모든 Pod가 동시에 쓰기 요청을 보내면 L2 과부하로 재장애 발생 가능.

```python
import random

class DriftReconciler:
    """L2 복구 시 상태 드리프트 해결 + Jitter 적용."""
    
    def __init__(
        self,
        min_jitter_seconds: float = 0.0,
        max_jitter_seconds: float = 5.0,
    ):
        self._min_jitter = min_jitter_seconds
        self._max_jitter = max_jitter_seconds
    
    async def schedule_reconciliation(self, service_name: str):
        """Jitter를 적용하여 순차적으로 동기화."""
        jitter = random.uniform(self._min_jitter, self._max_jitter)
        
        logger.info(
            f"[DriftReconciler] Scheduling reconciliation for {service_name} "
            f"in {jitter:.2f}s (jitter applied)"
        )
        
        await asyncio.sleep(jitter)
        await self._do_reconciliation(service_name)
```

### 6.4 동작 흐름

```
┌──────────────────────────────────────────────────────────────┐
│               드리프트 복구 흐름 (with Jitter)                │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│   L2 복구 감지 (Ping 성공)                                   │
│         │                                                    │
│         ▼                                                    │
│   ┌─────────────────────────────────────────────────────┐   │
│   │  Jitter 적용: 0~5초 무작위 대기                      │   │
│   │  (Thundering Herd 방지)                              │   │
│   └─────────────────────────────────────────────────────┘   │
│         │                                                    │
│         ▼                                                    │
│   각 서비스별 Reconciliation:                                │
│         │                                                    │
│         ├─ L1 상태 조회                                      │
│         ├─ L2 상태 조회                                      │
│         ├─ Most Restrictive Wins 적용                        │
│         └─ 승리한 상태를 양쪽에 동기화                       │
│                                                              │
│   로그: "[DriftReconciler] Reconciled: OPEN wins over CLOSED"│
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. 장애 모드 로깅 (Shadow Logging)

### 7.1 문제

L2 장애 동안 발생한 상태 변화가 유실되면, 사후 분석(Forensic)이 불가능합니다.

### 7.2 해결책: L2 Sync Failure Record

```python
@dataclass
class L2SyncFailureRecord:
    """L2 동기화 실패 기록."""
    
    service_name: str
    intended_state: str
    failure_time: datetime
    error_message: str
    l1_state_at_failure: str
    synced_after_recovery: bool = False  # 복구 후 동기화 완료 여부
    recovery_time: Optional[datetime] = None


class ShadowLogger:
    """L2 장애 동안의 상태 변화를 로컬에 기록."""
    
    def __init__(self):
        self._failure_log: List[L2SyncFailureRecord] = []
        self._lock = threading.RLock()
    
    def record_sync_failure(
        self,
        service_name: str,
        intended_state: str,
        error: Exception,
    ) -> None:
        """L2 동기화 실패 기록."""
        with self._lock:
            record = L2SyncFailureRecord(
                service_name=service_name,
                intended_state=intended_state,
                failure_time=datetime.now(timezone.utc),
                error_message=str(error),
                l1_state_at_failure=intended_state,
            )
            self._failure_log.append(record)
            
            logger.warning(
                f"[ShadowLog] L2 sync failed: {service_name} "
                f"state={intended_state} error={error}"
            )
    
    def get_unsynced_records(self) -> List[L2SyncFailureRecord]:
        """아직 동기화되지 않은 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if not r.synced_after_recovery]
    
    def mark_as_synced(self, service_name: str) -> None:
        """복구 후 동기화 완료 마킹."""
        with self._lock:
            for record in self._failure_log:
                if record.service_name == service_name and not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = datetime.now(timezone.utc)
```

### 7.3 Forensic Advisor 연동

```python
# Forensic Advisor에서 Shadow Log 조회
def analyze_l2_failures(self) -> Dict[str, Any]:
    """L2 장애 기간 동안의 상태 변화 분석."""
    shadow_logger = get_shadow_logger()
    unsynced = shadow_logger.get_unsynced_records()
    
    return {
        "unsynced_count": len(unsynced),
        "affected_services": list(set(r.service_name for r in unsynced)),
        "failure_timeline": [
            {
                "service": r.service_name,
                "state": r.intended_state,
                "time": r.failure_time.isoformat(),
                "error": r.error_message,
            }
            for r in sorted(unsynced, key=lambda x: x.failure_time)
        ],
    }
```

---

## 8. 구현 계획

### 8.1 Phase 1: L2 타임아웃 (우선순위: 높음)

| 항목 | 설명 | 예상 시간 |
|------|------|----------|
| `L2TimeoutConfig` 추가 | 어댑터별 타임아웃 설정 | 0.5h |
| `_sync_to_l2_with_timeout()` 구현 | 타임아웃 적용 동기화 | 1h |
| 메트릭 추가 | `l2_timeout_total`, `l2_latency_seconds` | 0.5h |
| 테스트 | 타임아웃 동작 검증 | 1h |

### 8.2 Phase 2: Shadow Logging (우선순위: 높음)

| 항목 | 설명 | 예상 시간 |
|------|------|----------|
| `L2SyncFailureRecord` 정의 | 실패 기록 데이터 클래스 | 0.5h |
| `ShadowLogger` 구현 | 실패 기록 및 조회 | 1h |
| Forensic 연동 | `analyze_l2_failures()` | 0.5h |
| 테스트 | 로깅 및 조회 검증 | 1h |

### 8.3 Phase 3: 드리프트 복구 (우선순위: 중간)

| 항목 | 설명 | 예상 시간 |
|------|------|----------|
| L2 복구 감지 | Ping 기반 헬스체크 | 1h |
| `DriftReconciler` 구현 | Most Restrictive Wins 로직 | 1.5h |
| Jitter 적용 | Thundering Herd 방지 | 0.5h |
| 테스트 | 드리프트 시나리오 검증 | 1.5h |

### 8.4 Phase 4: 부트스트랩 강화 (우선순위: 낮음)

| 항목 | 설명 | 예상 시간 |
|------|------|----------|
| L2 Ping 테스트 | 부팅 시 연결 확인 | 0.5h |
| 로그 개선 | 더 명확한 폴백 메시지 | 0.5h |
| 테스트 | 설정 오류 시나리오 | 0.5h |

---

## 9. 테스트 계획

### 9.1 단위 테스트

```python
# tests/self_healing/unit/test_layered_repository.py

class TestL2Timeout:
    """L2 타임아웃 테스트."""
    
    def test_timeout_on_slow_l2(self):
        """L2가 느리면 타임아웃 발생."""
        pass
    
    def test_fallback_to_l1_on_timeout(self):
        """타임아웃 시 L1만으로 동작."""
        pass
    
    def test_adapter_specific_timeout(self):
        """어댑터별 다른 타임아웃 적용."""
        pass


class TestShadowLogging:
    """Shadow Logging 테스트."""
    
    def test_record_sync_failure(self):
        """동기화 실패 기록."""
        pass
    
    def test_get_unsynced_records(self):
        """미동기화 기록 조회."""
        pass
    
    def test_mark_as_synced(self):
        """동기화 완료 마킹."""
        pass


class TestDriftReconciliation:
    """드리프트 복구 테스트."""
    
    def test_most_restrictive_wins(self):
        """OPEN > HALF_OPEN > CLOSED 우선순위."""
        pass
    
    def test_timestamp_tiebreaker(self):
        """같은 상태면 타임스탬프로 결정."""
        pass
    
    def test_jitter_applied(self):
        """Jitter가 적용되어 지연됨."""
        pass


class TestColdStartProtection:
    """콜드 스타트 보호 테스트."""
    
    def test_default_state_is_safe(self):
        """기본 상태가 안전한 값(CLOSED)."""
        pass
    
    def test_l2_load_before_first_request(self):
        """첫 요청 전 L2 로드 시도."""
        pass


class TestIntelligentFallback:
    """지능형 폴백 테스트."""
    
    def test_fallback_on_import_error(self):
        """Import 실패 시 Memory 폴백."""
        pass
    
    def test_fallback_on_connection_error(self):
        """연결 실패 시 Memory 폴백."""
        pass
    
    def test_fallback_on_invalid_config(self):
        """잘못된 설정 시 Memory 폴백."""
        pass
```

### 9.2 통합 테스트

```python
# tests/self_healing/integration/test_layered_storage_resilience.py

class TestL2FailureScenarios:
    """L2 장애 시나리오 통합 테스트."""
    
    @pytest.mark.integration
    def test_l2_network_failure_graceful_isolation(self):
        """L2 네트워크 장애 시 우아한 고립."""
        pass
    
    @pytest.mark.integration
    def test_l2_recovery_drift_reconciliation(self):
        """L2 복구 후 드리프트 해결."""
        pass
    
    @pytest.mark.integration
    def test_shadow_log_forensic_analysis(self):
        """Shadow Log를 통한 Forensic 분석."""
        pass
```

### 9.3 카오스 테스트

```python
# tests/self_healing/chaos/test_layered_storage_chaos.py

class TestLayeredStorageChaos:
    """Layered Storage 카오스 테스트."""
    
    @pytest.mark.chaos
    def test_random_l2_failures(self):
        """무작위 L2 장애 주입."""
        pass
    
    @pytest.mark.chaos
    def test_thundering_herd_prevention(self):
        """다수 Pod 동시 복구 시 부하 분산."""
        pass
```

---

## 10. 메트릭 및 모니터링

### 10.1 Prometheus 메트릭

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `selfhealing_l2_timeout_total` | Counter | L2 타임아웃 발생 횟수 |
| `selfhealing_l2_latency_seconds` | Histogram | L2 응답 시간 |
| `selfhealing_l2_sync_failure_total` | Counter | L2 동기화 실패 횟수 |
| `selfhealing_l2_connection_status` | Gauge | L2 연결 상태 (1=정상, 0=장애) |
| `selfhealing_drift_reconciliation_total` | Counter | 드리프트 복구 횟수 |
| `selfhealing_shadow_log_unsynced_count` | Gauge | 미동기화 Shadow Log 수 |

### 10.2 알림 규칙

```yaml
# Prometheus Alert Rules

- alert: L2StorageUnreachable
  expr: selfhealing_l2_connection_status == 0
  for: 1m
  labels:
    severity: warning
  annotations:
    summary: "L2 Storage unreachable"
    description: "Self-Healing L2 (Redis/DB) has been unreachable for 1+ minute."

- alert: HighL2Timeout
  expr: rate(selfhealing_l2_timeout_total[5m]) > 10
  for: 2m
  labels:
    severity: warning
  annotations:
    summary: "High L2 timeout rate"
    description: "L2 timeout rate is above 10/min for 2+ minutes."

- alert: ShadowLogBacklog
  expr: selfhealing_shadow_log_unsynced_count > 100
  for: 5m
  labels:
    severity: critical
  annotations:
    summary: "Shadow log backlog growing"
    description: "More than 100 unsynced shadow log entries."
```

---

## 관련 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - Error Budget Gate
- [11_FORENSIC_ADVISOR.md](11_FORENSIC_ADVISOR.md) - Forensic Advisor

---

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0 | 2024-12-22 | 초안 작성 |

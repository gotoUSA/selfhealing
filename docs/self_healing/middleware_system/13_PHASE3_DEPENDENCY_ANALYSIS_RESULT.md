# Phase 3 결과 보고서: 의존성 분석

> **생성일**: 2026-01-04
> **Phase**: 3 (의존성 분석)
> **상태**: ✅ 완료

---

## 📋 요약

| 항목 | 수치 |
|------|:----:|
| **분석된 미들웨어** | 11개 |
| Redis 의존 미들웨어 | 4개 |
| DB 의존 미들웨어 | 3개 |
| 외부 서비스 의존 | 3개 |
| 완전 독립 미들웨어 | 3개 |
| Signal 기반 독립 컴포넌트 | 1개 |
| Management Command | 8개 |

---

## 1. 미들웨어 의존성 매트릭스

### 1.1 전체 의존성 맵

| 미들웨어 | Redis | DB | Celery | EmergencyManager | CircuitBreaker | 기타 |
|----------|:-----:|:--:|:------:|:----------------:|:--------------:|------|
| trace_id_middleware | ❌ | ❌ | ❌ | ❌ | ❌ | contextvars |
| HealthBridgeMiddleware | ❌ | ❌ | ❌ | ❌ | ✅ (읽기) | 메모리 캐시 |
| TieringMiddleware | ❌ | ❌ | ❌ | ✅ | ❌ | TierRegistry |
| SelfHealingMiddleware | ❌ | ❌ | ❌ | ❌ | ✅ | AuditLogger |
| ActorContextMiddleware | ❌ | ❌ | ❌ | ❌ | ❌ | ActorContext |
| HybridRateLimitMiddleware | ✅ | ❌ | ❌ | ❌ | ❌ | LocalMemory (폴백) |
| PoolCircuitBreakerMiddleware | ❌ | ✅ | ❌ | ❌ | ❌ | 백그라운드 스레드 |
| PoolTimeoutMiddleware | ❌ | ✅ | ❌ | ❌ | ❌ | SQLAlchemy |
| ChaosMiddleware | ❌ | ✅ | ❌ | ❌ | ❌ | - |
| ConnectionPoolLimiterMiddleware | ❌ | ✅ | ❌ | ❌ | ❌ | - |
| AuditMiddleware | ❌ | ❌ | ❌ | ❌ | ❌ | ContinuousAuditRecorder |

### 1.2 의존성 시각화

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          미들웨어 의존성 그래프                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌─────────────────┐                                                       │
│   │     Redis       │◄─────────────────────────────────────┐                │
│   └────────┬────────┘                                      │                │
│            │                                               │                │
│            ▼                                               │                │
│   ┌─────────────────────────┐                              │                │
│   │ HybridRateLimitMiddleware│───────┐                     │                │
│   └─────────────────────────┘        │ fallback            │                │
│                                      ▼                     │                │
│                              ┌───────────────┐             │                │
│                              │ LocalMemory   │             │                │
│                              └───────────────┘             │                │
│                                                            │                │
│   ┌─────────────────┐                                      │                │
│   │   Database      │◄────────────────────┐                │                │
│   └────────┬────────┘                     │                │                │
│            │                              │                │                │
│            ├────────┬─────────────────────┤                │                │
│            ▼        ▼                     ▼                │                │
│   ┌─────────┐ ┌───────────┐  ┌───────────────────┐         │                │
│   │PoolCB   │ │PoolTimeout│  │ ChaosMiddleware   │         │                │
│   │Middleware│ │Middleware │  │ (HELLMODE only)   │         │                │
│   └─────────┘ └───────────┘  └───────────────────┘         │                │
│                                                            │                │
│   ┌─────────────────────────┐    ┌─────────────────────┐   │                │
│   │   EmergencyManager      │◄───│  TieringMiddleware  │   │                │
│   └─────────────────────────┘    └─────────────────────┘   │                │
│            ▲                                               │                │
│            │ (상태 조회)                                    │                │
│            │                                               │                │
│   ┌─────────────────────────┐    ┌─────────────────────┐   │                │
│   │ CircuitBreaker Service  │◄───│ HealthBridgeMiddleware │ │                │
│   └─────────────────────────┘    └─────────────────────┘   │                │
│            ▲                                               │                │
│            │ (실패 기록)                                    │                │
│            │                                               │                │
│   ┌─────────────────────────┐                              │                │
│   │  SelfHealingMiddleware  │──────────────────────────────┘                │
│   └─────────────────────────┘                                               │
│            │                                                                │
│            │ (Audit 이벤트)                                                  │
│            ▼                                                                │
│   ┌─────────────────────────┐                                               │
│   │     AuditMiddleware     │                                               │
│   └─────────────────────────┘                                               │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 미들웨어별 상세 의존성 분석

### 2.1 [1] trace_id_middleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟢 완전 독립 |
| **인프라 의존** | 없음 |
| **라이브러리 의존** | `contextvars`, `threading` (Python 표준) |
| **다른 미들웨어 의존** | 없음 |
| **장애 시 영향** | 없음 (자체적으로 trace_id 생성) |

**코드 경로**: `selfhealing/audit/trace.py`

```python
# 핵심 의존성
import contextvars
import threading
import uuid  # 표준 라이브러리만 사용
```

---

### 2.2 [2] HealthBridgeMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟡 내부 서비스 의존 (선택적) |
| **인프라 의존** | 없음 (DB 독립이 목적!) |
| **서비스 의존** | CircuitBreaker Service (스냅샷 갱신용, 선택적) |
| **장애 시 동작** | 마지막 캐시된 CB 상태 반환 |

**코드 경로**: `selfhealing/api/django/middleware.py`

```python
# 선택적 의존성 (없어도 동작)
from selfhealing.services.circuit_breaker.convenience import get_circuit_breaker_service

# 실패 시 graceful degradation
cb_service = get_circuit_breaker_service()
if cb_service is None:
    return  # 스킵
```

**핵심 설계**: DB가 죽어도 /health/l3 응답 가능 (Worker Saturation 방지)

---

### 2.3 [3] TieringMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟡 내부 서비스 의존 |
| **인프라 의존** | 없음 |
| **서비스 의존** | EmergencyManager, TierRegistry |
| **장애 시 동작** | 모든 요청 허용 (Fail-Open) |

**코드 경로**: `selfhealing/api/django/tiering/middleware.py`

```python
# 필수 의존성
from selfhealing.services.emergency_mode import get_emergency_manager
from selfhealing.services.emergency_mode.enums import EmergencyLevel, EMERGENCY_LEVEL_RULES

# TierRegistry 의존
from .registry import get_tier_registry
```

**비상 모드 연동**:
- `EmergencyManager`에서 현재 레벨 조회
- 레벨에 따라 Tier별 트래픽 제어

---

### 2.4 [4] SelfHealingMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟡 내부 서비스 의존 |
| **인프라 의존** | 없음 (직접 연결 안함) |
| **서비스 의존** | CircuitBreaker Service, AuditLogger, DLQ Service |
| **장애 시 동작** | 로깅 후 정상 처리 계속 (Fail-Open) |

**코드 경로**: `selfhealing/api/django/middleware.py` (Line 578+)

```python
# 서비스 의존성 (Lazy Init)
from selfhealing.services.circuit_breaker.convenience import get_circuit_breaker_service
from selfhealing.audit import get_audit_logger

self._cb_service = get_circuit_breaker_service()  # CB 상태 기록용
self._audit_logger = get_audit_logger()           # Audit 기록용
```

**동작 흐름**:
1. DB 오류/5xx 감지 시 → CB에 실패 기록
2. DLQ 적재 대상 요청 → DLQ에 자동 저장
3. Audit 이벤트 발행 → AuditMiddleware에서 수집

---

### 2.5 [5] ActorContextMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟢 완전 독립 |
| **인프라 의존** | 없음 |
| **서비스 의존** | ActorContext (메모리 기반 Context Manager) |
| **장애 시 동작** | actor 정보 없이 진행 |

**코드 경로**: `myproject/middleware/actor_middleware.py`

```python
# 유일한 의존성 (메모리 기반)
from selfhealing.context.actor_context import ActorContext

with ActorContext.set_actor_from_django_request(request):
    response = self.get_response(request)
```

---

### 2.6 [6] HybridRateLimitMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🔴 Redis 의존 (폴백 있음) |
| **인프라 의존** | **Redis** (L2 Primary) |
| **폴백** | LocalMemoryRateLimiter (L1) |
| **장애 시 동작** | 10배 엄격한 로컬 메모리 제한 적용 |

**코드 경로**: `selfhealing/api/django/rate_limit.py`

```python
# Primary: Redis
self._redis_client = self._get_redis_client()
self._redis_client.ping()

# Fallback: Local Memory (Redis 실패 시)
class LocalMemoryRateLimiter:
    """L1 로컬 메모리 기반 레이트 리미터. Redis 장애 시 활성화."""
```

**Defense-in-Depth 전략**:
| Layer | 저장소 | 한도 (기본) | 상태 |
|:-----:|--------|:-----------:|------|
| L2 | Redis | 100 req/min | Primary |
| L1 | Local Memory | 10 req/min | Fallback |

---

### 2.7 [7] PoolCircuitBreakerMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🔴 DB 의존 |
| **인프라 의존** | **PostgreSQL/DB** (Pool 상태 체크) |
| **기타 의존** | 백그라운드 스레드 (Pool 상태 갱신) |
| **장애 시 동작** | 즉시 503 반환 (Fail Fast) |

**코드 경로**: `selfhealing/api/django/pool_circuit_breaker.py`

```python
from django.db import connections

# 백그라운드 스레드에서 Pool 상태 갱신
def _background_refresh_loop(self):
    while not self._stop_background.is_set():
        self._refresh_pool_status()
        time.sleep(self._cache_interval_ms / 1000.0)

# 캐시 기반 조회 (블로킹 없음)
cached_status = self._cached_pool_status
```

**v6.2.0+ 개선사항**:
- 매 요청마다 Pool 상태 직접 조회 → 캐시 기반 조회로 변경
- 백그라운드 스레드에서 100ms 주기로 갱신

---

### 2.8 [8] PoolTimeoutMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🔴 DB 의존 (예외 감지용) |
| **인프라 의존** | SQLAlchemy (TimeoutError 타입) |
| **장애 시 동작** | Pool Timeout 예외 → 503 반환 |

**코드 경로**: `myproject/middleware/pool_timeout_middleware.py`

```python
# SQLAlchemy TimeoutError import
try:
    from sqlalchemy.exc import TimeoutError as SATimeoutError
    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SATimeoutError = Exception
    SQLALCHEMY_AVAILABLE = False
```

**감지 패턴**:
- `timeout` in error_str
- `queuepool limit` in error_str
- `pool exhausted` in error_str
- `no connections available` in error_str

---

### 2.9 [9-10] ChaosMiddleware & ConnectionPoolLimiterMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🔴 DB 의존 (장애 주입용) |
| **인프라 의존** | PostgreSQL (lock_timeout, statement_timeout) |
| **활성화 조건** | X-Test-Mode: hellmode/chaos-monkey 헤더 |
| **용도** | HELLMODE 테스트 전용 |

**코드 경로**: `myproject/middleware/chaos_middleware.py`

```python
from django.db import connection, connections

def _set_db_lock_timeout(self, timeout_ms: int):
    with connection.cursor() as cursor:
        cursor.execute(f"SET lock_timeout = '{timeout_ms}ms'")

def _set_db_statement_timeout(self, timeout_ms: int):
    with connection.cursor() as cursor:
        cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")
```

---

### 2.10 [11] AuditMiddleware

| 항목 | 내용 |
|------|------|
| **의존성 분류** | 🟢 완전 독립 (Fail-Open) |
| **인프라 의존** | 없음 |
| **서비스 의존** | ContinuousAuditRecorder (선택적) |
| **장애 시 동작** | stderr로 fallback 출력, 비즈니스 로직 계속 |

**코드 경로**: `selfhealing/api/django/audit_middleware.py`

```python
# Fail-Open 정책
# - Audit 기록 실패가 비즈니스 로직을 중단시키지 않음
# - 실패 시 stderr로 fallback 출력

try:
    self._record_events(events)
except Exception as e:
    logger.error(f"Audit recording failed: {e}")
    # 비즈니스 로직은 계속 진행
```

---

## 3. 미들웨어 간 실행 순서 의존성

### 3.1 순서가 중요한 미들웨어 쌍

| 선행 | 후행 | 이유 |
|------|------|------|
| `trace_id_middleware` | 모든 미들웨어 | trace_id가 있어야 다른 미들웨어에서 로깅 가능 |
| `HealthBridgeMiddleware` | `SelfHealingMiddleware` | /health/l3는 DB 엔진 로드 전에 응답해야 함 |
| `TieringMiddleware` | 비즈니스 로직 | 트래픽 제어 후 비즈니스 로직 실행 |
| `ActorContextMiddleware` | `AuditMiddleware` | actor 정보가 있어야 Audit에 포함 가능 |
| 모든 미들웨어 | `AuditMiddleware` | 모든 이벤트 수집 후 기록 |

### 3.2 순서 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│               미들웨어 실행 순서 (중요도 표시)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] trace_id_middleware           ⬛⬛⬛⬛⬛ (최상단 필수)      │
│       │                                                         │
│       ▼                                                         │
│  [2] HealthBridgeMiddleware        ⬛⬛⬛⬛⬜ (상단 권장)        │
│       │                                                         │
│       ▼                                                         │
│  [3] TieringMiddleware             ⬛⬛⬛⬜⬜ (상단 권장)        │
│       │                                                         │
│       ▼                                                         │
│  [4] SelfHealingMiddleware         ⬛⬛⬛⬜⬜                     │
│       │                                                         │
│       ▼                                                         │
│  [5] ActorContextMiddleware        ⬛⬛⬜⬜⬜                     │
│       │                                                         │
│       ▼                                                         │
│  [6-13] Django Core                ⬛⬜⬜⬜⬜ (순서 유연)        │
│       │                                                         │
│       ▼                                                         │
│  [14] HybridRateLimitMiddleware    ⬛⬛⬜⬜⬜                     │
│       │                                                         │
│       ▼                                                         │
│  [15] PoolCircuitBreakerMiddleware ⬛⬛⬛⬜⬜                     │
│       │                                                         │
│       ▼                                                         │
│  [16] PoolTimeoutMiddleware        ⬛⬛⬜⬜⬜                     │
│       │                                                         │
│       ▼                                                         │
│  [17-18] ChaosMiddleware           ⬜⬜⬜⬜⬜ (테스트 전용)      │
│       │                                                         │
│       ▼                                                         │
│  ═══════════════════════════════════════════════════════════   │
│  ║                    [VIEW 실행]                            ║   │
│  ═══════════════════════════════════════════════════════════   │
│       │                                                         │
│       ▼                                                         │
│  [19] AuditMiddleware              ⬛⬛⬛⬛⬛ (맨 마지막 필수)  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 인프라별 의존 컴포넌트

### 4.1 Redis 의존 컴포넌트

| 컴포넌트 | 용도 | 장애 시 동작 |
|----------|------|-------------|
| `HybridRateLimitMiddleware` | Sliding Window Rate Limit | LocalMemory 폴백 (10 req/min) |
| `CircuitBreaker Service` | CB 상태 저장 | Memory Repository 폴백 |
| `DLQ Service` | 실패 요청 저장 | WAL 파일 폴백 |
| `RuntimeConfigManager` | 런타임 설정 저장 | 기본값 폴백 |

### 4.2 DB 의존 컴포넌트

| 컴포넌트 | 용도 | 장애 시 동작 |
|----------|------|-------------|
| `PoolCircuitBreakerMiddleware` | Pool 상태 체크 | 503 즉시 반환 |
| `PoolTimeoutMiddleware` | Pool Timeout 감지 | 503 반환 |
| `ChaosMiddleware` | lock/statement timeout 주입 | 테스트 전용 |
| Django ORM | 비즈니스 데이터 | - |

### 4.3 Celery 의존 컴포넌트

| 컴포넌트 | 용도 | 장애 시 동작 |
|----------|------|-------------|
| `Signal Hooks` | 태스크 실패 감지 | 로그만 기록 |
| `DLQ Replay Tasks` | 실패 요청 재시도 | 큐에 대기 |
| `Metrics Collection` | 메트릭 수집 태스크 | 수집 건너뜀 |
| `CB Recovery Check` | CB 복구 체크 태스크 | 수동 복구 필요 |

---

## 5. 독립 컴포넌트 목록

### 5.1 Signal 기반 컴포넌트

| 컴포넌트 | 파일 위치 | 기능 |
|----------|----------|------|
| `Celery Signal Hooks` | `selfhealing/adapters/celery/signal_hooks.py` | 태스크 실패 시 CB/DLQ 연동 |

**Signal Hooks 상세**:
```python
from celery.signals import (
    task_failure,    # 태스크 실패 시
    task_success,    # 태스크 성공 시
    task_retry,      # 태스크 재시도 시
    task_prerun,     # 태스크 시작 전
    task_postrun,    # 태스크 종료 후
)
```

### 5.2 Management Commands

| Command | 파일 위치 | 기능 |
|---------|----------|------|
| `generate_self_healing_alerts` | `shopping/management/commands/` | Self-Healing 알림 생성 |
| `check_selfhealing_config` | `shopping/management/commands/` | Self-Healing 설정 검증 |
| `create_load_test_users` | `shopping/management/commands/` | 부하 테스트 사용자 생성 |
| `cleanup_expired_tokens` | `shopping/management/commands/` | 만료 토큰 정리 |
| `cleanup_old_carts` | `shopping/management/commands/` | 오래된 장바구니 정리 |
| `delete_unverified_users` | `shopping/management/commands/` | 미인증 사용자 삭제 |
| `create_test_data` | `shopping/management/commands/` | 테스트 데이터 생성 |
| `security_review` | `shopping/management/commands/` | 보안 검토 |
| `test_point_expiry` | `shopping/management/commands/` | 포인트 만료 테스트 |

### 5.3 Scheduled Tasks (Celery Beat)

| Task | 주기 | 기능 |
|------|------|------|
| `check_circuit_breaker_recovery` | 1분 | CB 상태 복구 체크 |
| `expire_manual_overrides` | 5분 | 수동 오버라이드 만료 |
| `collect_self_healing_metrics` | 1분 | 메트릭 수집 |
| `cleanup_resolved_dlq_entries` | 1시간 | 해결된 DLQ 항목 정리 |

---

## 6. 장애 전파 분석

### 6.1 Redis 장애 시 영향 범위

```
┌─────────────────────────────────────────────────────────────────┐
│                     Redis 장애 시 영향                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────┐                                       │
│  │      Redis DOWN      │                                       │
│  └──────────┬───────────┘                                       │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ HybridRateLimitMiddleware                                 │   │
│  │ → LocalMemory 폴백 (10 req/min으로 축소)                  │   │
│  │ → Prometheus 메트릭: degraded_mode=1                      │   │
│  └──────────────────────────────────────────────────────────┘   │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ CircuitBreaker Service                                    │   │
│  │ → Memory Repository로 폴백                                │   │
│  │ → Pod 재시작 시 상태 유실 위험                            │   │
│  └──────────────────────────────────────────────────────────┘   │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ DLQ Service                                               │   │
│  │ → WAL 파일 폴백                                           │   │
│  │ → 복구 후 자동 동기화                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  결론: 서비스 지속 가능 (Graceful Degradation)                  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6.2 DB 장애 시 영향 범위

```
┌─────────────────────────────────────────────────────────────────┐
│                      DB 장애 시 영향                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────────┐                                       │
│  │     Database DOWN     │                                       │
│  └──────────┬───────────┘                                       │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ PoolCircuitBreakerMiddleware                              │   │
│  │ → OPEN 상태 전환                                          │   │
│  │ → 모든 요청에 503 즉시 반환 (Fail Fast)                   │   │
│  └──────────────────────────────────────────────────────────┘   │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ HealthBridgeMiddleware                                    │   │
│  │ → /health/l3 여전히 응답 가능                             │   │
│  │ → 캐시된 CB 상태 반환                                     │   │
│  └──────────────────────────────────────────────────────────┘   │
│             │                                                   │
│             ▼                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ SelfHealingMiddleware                                     │   │
│  │ → DB 오류 감지 → CB에 실패 기록                           │   │
│  │ → 복구 가능한 요청 DLQ 적재                               │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                 │
│  결론: 빠른 실패 + 자동 복구 준비 (Self-Healing)                │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 권장 사항

### 7.1 미들웨어 순서 검증

| 검증 항목 | 현재 상태 | 권장 |
|----------|:--------:|------|
| trace_id가 최상단인가? | ✅ | 유지 |
| HealthBridge가 DB 접근 전인가? | ✅ | 유지 |
| AuditMiddleware가 맨 마지막인가? | ✅ | 유지 |
| ActorContext가 Audit 전인가? | ✅ | 유지 |

### 7.2 장애 대응 개선 포인트

| 인프라 | 현재 폴백 | 개선 가능 |
|--------|----------|----------|
| Redis | LocalMemory | Redis Sentinel/Cluster 도입 |
| DB | 503 Fail Fast | Read Replica 도입 |
| Celery | 로그 기록 | Dead Letter Exchange 활용 |

### 7.3 모니터링 권장 항목

| 지표 | Prometheus 메트릭 | 알림 임계값 |
|------|------------------|------------|
| Redis 상태 | `selfhealing_rate_limit_degraded_mode` | > 0 (즉시) |
| Pool 상태 | `selfhealing_pool_cb_state` | OPEN (즉시) |
| DLQ 크기 | `selfhealing_dlq_size` | > 100 (경고) |
| CB 상태 | `selfhealing_circuit_breaker_state` | OPEN (경고) |

---

## 📚 관련 문서

- [11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md](11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md) - 미들웨어 상세 분석
- [12_PHASE4_FEATURE_FLAG_RESULT.md](12_PHASE4_FEATURE_FLAG_RESULT.md) - Feature Flag 정리
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - 스토리지 폴백 설계

---

*이 문서는 Phase 3 의존성 분석의 결과물입니다.*

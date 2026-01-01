# 53. 미연결 기능 분석 및 통합 가이드

> **문서 버전**: 1.1.0
> **생성일**: 2025-12-31
> **최종 수정**: 2025-12-31
> **목적**: 정의되었지만 연결되지 않은 기능들의 분석 및 통합 가이드

---

## 📋 목차

1. [개요](#1-개요)
2. [분류 기준](#2-분류-기준)
3. [미들웨어 분석](#3-미들웨어-분석)
4. [서비스 분석](#4-서비스-분석)
5. [Core 컴포넌트 분석](#5-core-컴포넌트-분석)
6. [Audit 컴포넌트 분석](#6-audit-컴포넌트-분석)
6a. [시그널 및 이벤트 핸들러 분석](#6a-시그널-및-이벤트-핸들러-분석)
6b. [환경 변수 기반 기능 토글 (Feature Flags)](#6b-환경-변수-기반-기능-토글-feature-flags)
6c. [Management Commands 분석](#6c-management-commands-분석)
7. [독립 기능 탐색 방법](#7-독립-기능-탐색-방법)
8. [통합 로드맵](#8-통합-로드맵)

---

## 1. 개요

### 1.1 배경

Self-Healing 시스템은 **도메인 프리(Domain-Free)** 아키텍처로 설계되었습니다:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Self-Healing Package                          │
│                    (Domain-Free Library)                         │
├─────────────────────────────────────────────────────────────────┤
│  • 어떤 도메인에도 독립적                                        │
│  • Shopping API = 테스트베드 (실험 및 검증용)                    │
│  • 일부 기능은 의도적으로 테스트에서만 사용                      │
│  • 라이브러리 사용자가 선택적으로 활성화                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Shopping API (Testbed)                        │
│                    (도메인 구현체)                               │
├─────────────────────────────────────────────────────────────────┤
│  • Self-Healing 기능 검증용                                      │
│  • 모든 기능을 활성화할 필요 없음                                │
│  • 프로덕션 시뮬레이션 환경                                      │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 분석 목표

| 목표 | 설명 |
|------|------|
| **분류** | 각 기능이 "테스트 전용"인지 "연결 필요"인지 판단 |
| **문서화** | 미연결 기능의 사용 방법 및 통합 가이드 제공 |
| **로드맵** | 필요한 경우 통합 우선순위 제시 |

---

## 2. 분류 기준

### 2.1 기능 분류 카테고리

```
┌─────────────────────────────────────────────────────────────────┐
│                     기능 분류 매트릭스                           │
├──────────────────┬──────────────────────────────────────────────┤
│                  │              통합 필요성                      │
│                  ├──────────────────┬───────────────────────────┤
│                  │      높음        │         낮음              │
├──────────────────┼──────────────────┼───────────────────────────┤
│ 도메인           │  🔴 즉시 연결    │  🟡 선택적 연결           │
│ 종속적           │  (프로덕션 필수) │  (선택적 활성화)          │
├──────────────────┼──────────────────┼───────────────────────────┤
│ 도메인           │  🟢 라이브러리   │  ⚪ 테스트 전용           │
│ 프리             │  (문서화만 필요) │  (의도적 미연결)          │
└──────────────────┴──────────────────┴───────────────────────────┘
```

### 2.2 분류 아이콘 범례

| 아이콘 | 분류 | 설명 |
|--------|------|------|
| 🔴 | **즉시 연결 필요** | 프로덕션 환경에서 반드시 필요한 기능 |
| 🟡 | **선택적 연결** | 상황에 따라 활성화 가능 |
| 🟢 | **라이브러리 기능** | 도메인 프리, 사용자가 필요시 통합 |
| ⚪ | **테스트 전용** | 의도적으로 테스트에서만 사용 |

---

## 3. 미들웨어 분석

### 3.1 미들웨어 전체 현황

```
┌───────────────────────────────────────────────────────────────────────────┐
│                      현재 MIDDLEWARE 설정 (base.py)                        │
├───────────────────────────────────────────────────────────────────────────┤
│ ✅ HealthBridgeMiddleware          → 연결됨 (최상단)                      │
│ ✅ SelfHealingMiddleware           → 연결됨                               │
│ ✅ Django Core Middlewares         → 연결됨                               │
│ ✅ HybridRateLimitMiddleware       → 연결됨                               │
│ ✅ ChaosMiddleware                 → 연결됨 (HELLMODE용)                  │
│ ✅ ConnectionPoolLimiterMiddleware → 연결됨 (HELLMODE용)                  │
├───────────────────────────────────────────────────────────────────────────┤
│                        미연결 미들웨어                                     │
├───────────────────────────────────────────────────────────────────────────┤
│ ❌ TieringMiddleware               → 미연결                               │
│ ❌ SensitiveAccessLoggingMiddleware → 미연결                              │
│ ❌ ActorContextMiddleware          → 미연결                               │
│ ✅ PoolCircuitBreakerMiddleware    → v6.2.0 해결 (조건부 연결 가능)       │
│ ⚠️ PoolTimeoutMiddleware           → 조건부 (local.py에서만 활성화)       │
└───────────────────────────────────────────────────────────────────────────┘
```

### 3.2 개별 미들웨어 분석

---

#### 3.2.1 TieringMiddleware

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/api/django/tiering/middleware.py` |
| **분류** | 🟢 **라이브러리 기능** |
| **목적** | Emergency Mode 시 API Tier에 따른 트래픽 제어 (Load Shedding) |
| **도메인 종속성** | ❌ 없음 (도메인 프리) |

**설계 의도**:
```python
# Emergency Level별 동작:
# - NORMAL (0): 모든 요청 허용
# - LEVEL_1 (1): non_essential 차단
# - LEVEL_2 (2): standard 90% 차단, non_essential 100% 차단
# - LEVEL_3 (3): critical 50% 차단, standard/non_essential 100% 차단
```

**미연결 이유**:
- Emergency Mode가 아닌 상태에서는 불필요한 오버헤드
- Shopping API는 Emergency Mode 테스트를 위한 테스트베드이므로 필요시에만 활성화
- 라이브러리 사용자가 자신의 프로덕션에서 선택적으로 활성화

**통합 가이드**:
```python
# settings.py
MIDDLEWARE = [
    ...
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # Emergency Mode Load Shedding (선택적)
    "selfhealing.api.django.tiering.TieringMiddleware",
    ...
]

# 비활성화하려면:
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

**권장 조치**: 📝 문서화만 필요 (현재 상태 유지)

---

#### 3.2.2 SensitiveAccessLoggingMiddleware

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/api/django/middleware.py:455` |
| **분류** | 🟡 **선택적 연결** |
| **목적** | 민감한 엔드포인트 접근 로깅 (컴플라이언스 감사) |
| **도메인 종속성** | ❌ 없음 |

**설계 특징**:
```
FAIL-OPEN Design:
- 로깅 실패가 요청 처리를 차단하지 않음
- 주요 기능(요청 처리) > 부가 기능(로깅)
```

**민감 엔드포인트 정의**:
```python
SENSITIVE_ENDPOINTS = [
    "/api/self-healing/audit/",
    "/api/self-healing/config/",
    "/api/self-healing/chaos/schedules/",
]
```

**미연결 이유**:
- 컴플라이언스 요구사항이 있는 프로덕션에서만 필요
- 테스트베드에서는 audit 테이블로 충분히 추적 가능
- 추가적인 접근 로깅은 오버헤드

**통합 가이드**:
```python
# 컴플라이언스 요구사항이 있는 경우 활성화
MIDDLEWARE = [
    ...
    # 민감 접근 로깅 (컴플라이언스용)
    "selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware",
    ...
]
```

**권장 조치**: 📝 문서화만 필요 (프로덕션 배포 시 고려)

---

#### 3.2.3 ActorContextMiddleware

| 항목 | 내용 |
|------|------|
| **파일** | `myproject/middleware/actor_middleware.py:35` |
| **분류** | 🟡 **선택적 연결** |
| **목적** | 모든 요청에서 "누가" 작업을 수행하는지 자동 추적 |
| **도메인 종속성** | ❌ 없음 |

**기능**:
```python
# 활성화 시:
# 1. 모든 AuditEntry에 actor_id, actor_type 자동 채움
# 2. Admin 페이지 변경 시 누가 변경했는지 기록
# 3. API 호출 시 어느 사용자가 호출했는지 추적
# 4. IP 주소, 세션 ID 등 보안 감사 정보 수집
```

**미연결 이유**:
- 테스트에서는 대부분 인증 없이 진행
- Audit 시스템이 이미 request 기반으로 actor 추출 가능
- 프로덕션에서 상세 감사 로그가 필요한 경우에만 활성화

**통합 가이드**:
```python
# 상세 Actor 추적이 필요한 경우 활성화
MIDDLEWARE = [
    ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Actor 컨텍스트 (인증 미들웨어 이후에 배치)
    "myproject.middleware.actor_middleware.ActorContextMiddleware",
    ...
]
```

**권장 조치**: 📝 문서화만 필요 (필요시 활성화)

---

#### 3.2.4 PoolCircuitBreakerMiddleware

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/api/django/pool_circuit_breaker.py:394` |
| **분류** | 🟢 **v6.2.1에서 해결됨 - 연결 가능** |
| **목적** | DB Connection Pool 고갈 시 즉시 503 반환 (Fail Fast) |
| **도메인 종속성** | ❌ 없음 |

**v6.2.0 (2026-01-01) 블로킹 이슈 해결**:

기존 문제:
- `check_pool_status()` 호출 시 `pool_container.has()`, `pool_container.get()` 등에서 락 경합 발생
- 고부하 시 추가 블로킹으로 인한 성능 저하

해결 방법:
```python
# AS-IS (v6.1.x): 매 요청마다 직접 Pool 조회 → 블로킹!
def should_allow_request(self):
    pool_status = self.check_pool_status()  # 블로킹 가능!
    ...

# TO-BE (v6.2.0): 캐시된 Pool 상태 사용 → Non-Blocking!
def should_allow_request(self):
    pool_status = self.get_cached_pool_status()  # 항상 즉시 반환
    ...

# 백그라운드 스레드에서 주기적으로 Pool 상태 갱신 (기본 100ms)
def _background_refresh_loop(self):
    while not self._stop_background.is_set():
        new_status = self._fetch_pool_status_internal()  # 여기서만 블로킹
        with self._cache_lock:
            self._cached_pool_status = new_status
        self._stop_background.wait(timeout=0.1)  # 100ms
```

**v6.2.1 추가 개선사항**:

| 개선 항목 | 설명 |
|-----------|------|
| **TTL 범위 검증** | 50ms ~ 1000ms로 제한, 범위 밖 값은 자동 보정 |
| **Stale 데이터 단계별 처리** | 경고(1초) → 안전 폴백(5초) 단계적 처리 |
| **백그라운드 스레드 자동 재시작** | 스레드 죽음 감지 시 자동 재시작 |
| **Audit 연동** | 503 거부 시 `decision_source: "cached_pool_status"` 명시 |

```python
# v6.2.1 Stale 처리 정책
┌──────────────────┬──────────────────────────────────────────────┐
│ 캐시 나이        │ 처리 방법                                    │
├──────────────────┼──────────────────────────────────────────────┤
│ < 1초            │ ✅ 정상 - 캐시 데이터 사용                   │
│ 1초 ~ 5초        │ 🟡 경고 - 로그 남기고 캐시 데이터 사용       │
│ > 5초            │ 🔴 안전 폴백 - is_exhausted=False 강제 설정  │
└──────────────────┴──────────────────────────────────────────────┘
```

**현재 상태**:
```python
# myproject/settings/local.py:
USE_POOL_CIRCUIT_BREAKER = os.getenv("USE_POOL_CIRCUIT_BREAKER", "FALSE") == "TRUE"

if USE_POOL_CIRCUIT_BREAKER:
    # v6.2.1: PoolCircuitBreakerMiddleware 블로킹 이슈 해결 + Stale 처리 + Audit
    MIDDLEWARE.insert(2, "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware")
    MIDDLEWARE.insert(3, "myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware")
```

**통합 가이드**:
```bash
# 환경 변수로 활성화
USE_POOL_CIRCUIT_BREAKER=TRUE

# 캐시 갱신 주기 조정 (기본 100ms, 범위: 50-1000ms)
POOL_CB_CACHE_INTERVAL_MS=100

# v6.2.1: Stale 처리 설정
POOL_CB_STALE_MULTIPLIER=10      # 경고 임계값 = interval * 10 (기본: 1초)
POOL_CB_CRITICAL_STALE_MS=5000   # 안전 폴백 임계값 (기본: 5초)

# Circuit Breaker 설정
POOL_CB_FAILURE_THRESHOLD=3   # 3회 실패 시 OPEN
POOL_CB_SUCCESS_THRESHOLD=2   # 2회 성공 시 CLOSED
POOL_CB_RECOVERY_TIMEOUT=10   # 10초 후 HALF_OPEN
```

**미들웨어 배치 순서 권장**:
```
1. HealthBridgeMiddleware        (DB 무관하게 /health 응답)
2. SelfHealingMiddleware         (에러 감지 및 DLQ)
3. PoolCircuitBreakerMiddleware  (Pool 고갈 감지 및 Fail Fast) ← v6.2.1
4. PoolTimeoutMiddleware         (예외 폴백)
5. Django Core Middlewares...
```

**테스트**:
```bash
# 단위 테스트 (v6.2.0 + v6.2.1)
pytest tests/unit/selfhealing/test_pool_circuit_breaker_v620.py -v

# 통합 테스트 (Connection Pool 환경 필요)
USE_CONNECTION_POOL=TRUE USE_POOL_CIRCUIT_BREAKER=TRUE \
    python tests/self_healing/e2e/test_pool_recovery.py
```

**권장 조치**: ✅ v6.2.1에서 연결 가능 (USE_POOL_CIRCUIT_BREAKER=TRUE)

---

#### 3.2.5 PoolTimeoutMiddleware

| 항목 | 내용 |
|------|------|
| **파일** | `myproject/middleware/pool_timeout_middleware.py:23` |
| **분류** | 🟡 **환경별 선택적** |
| **목적** | Pool Timeout 에러를 캐치하여 통계 수집 |
| **도메인 종속성** | ❌ 없음 |

**현재 상태**:
```python
# myproject/settings/local.py:36
MIDDLEWARE.insert(1, "myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware")
```

**미연결 이유 (base.py)**:
- 개발 환경에서만 유용 (Pool 문제 디버깅)
- 프로덕션에서는 메트릭 시스템으로 충분
- 환경별로 다르게 설정 필요

**권장 조치**: ✅ 현재 설정 유지 (local.py에서만 활성화)

---

## 4. 서비스 분석

### 4.1 서비스 전체 현황

```
┌───────────────────────────────────────────────────────────────────────────┐
│                        서비스 연결 현황                                    │
├───────────────────────────────────────────────────────────────────────────┤
│ ✅ CircuitBreakerService           → URL 연결됨                           │
│ ✅ DLQService                      → URL 연결됨                           │
│ ✅ ReplayService                   → URL 연결됨                           │
│ ✅ BlastRadiusService              → URL 연결됨                           │
│ ✅ AutoTuningService               → URL 연결됨                           │
│ ✅ RollbackService                 → URL 연결됨                           │
│ ✅ DashboardService                → URL 연결됨                           │
│ ✅ ErrorBudgetService              → URL 연결됨                           │
├───────────────────────────────────────────────────────────────────────────┤
│                        미연결/미사용 서비스                                │
├───────────────────────────────────────────────────────────────────────────┤
│ ⚪ IdempotencyService              → 테스트에서만 사용                     │
│ ⚪ SecurityNotificationService     → 간접 사용 (ViolationService 내부)    │
│ ⚪ SecurityViolationService        → 테스트에서만 사용                     │
└───────────────────────────────────────────────────────────────────────────┘
```

### 4.2 개별 서비스 분석

---

#### 4.2.1 IdempotencyService

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/services/idempotency_service.py` (462줄) |
| **분류** | ⚪ **테스트 전용** (의도적) |
| **목적** | 안전한 재시도를 위한 멱등성 키 관리 |
| **도메인 종속성** | ❌ 없음 (도메인 프리 설계) |

**도메인 프리 설계**:
```python
class IdempotencyDomain(Enum):
    """Domains that support idempotency checking (domain-neutral)."""
    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    EVENT = "event"
    CUSTOM = "custom"
```

**미연결 이유**:
- **의도적 미연결**: 도메인 프리 라이브러리 기능
- Shopping API는 테스트베드로서 멱등성 검증만 수행
- 실제 프로덕션에서 라이브러리 사용자가 직접 통합

**사용 예시 (라이브러리 사용자용)**:
```python
# 결제 시스템에서 사용 예시
from selfhealing.services.idempotency_service import IdempotencyService, IdempotencyKey

service = IdempotencyService()

# 결제 요청의 멱등성 체크
key = IdempotencyKey.for_operation(
    entity_type="payment",
    entity_id=payment_id,
    operation="process"
)

result = service.check(key, lookup_fn=lambda: get_existing_payment(payment_id))
if result.is_duplicate:
    return result.existing_result  # 중복 요청이면 기존 결과 반환
```

**권장 조치**: 📝 라이브러리 문서화 (테스트베드 연결 불필요)

---

#### 4.2.2 SecurityViolationService

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/services/security_violation_service.py` (673줄) |
| **분류** | ⚪ **테스트 전용** (의도적) |
| **목적** | 보안 위반 감지 및 분류 (self-heal 하면 안 되는 것들) |
| **도메인 종속성** | ❌ 없음 (도메인 프리 설계) |

**위반 유형 (도메인 중립적)**:
```python
class ViolationType(str, Enum):
    """Types of security violations that never self-heal (domain-neutral)."""
    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
```

**미연결 이유**:
- **의도적 미연결**: 도메인 프리 라이브러리 기능
- Shopping API는 보안 위반 시뮬레이션 테스트용
- 실제 보안 시스템과의 통합은 라이브러리 사용자 책임

**권장 조치**: 📝 라이브러리 문서화 (테스트베드 연결 불필요)

---

#### 4.2.3 SecurityNotificationService

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/services/security_notification_service.py` (795줄) |
| **분류** | ⚪ **간접 사용** |
| **목적** | 다채널 보안 알림 (Slack, Email, SMS, PagerDuty) |
| **도메인 종속성** | ❌ 없음 |

**채널 라우팅**:
```
- CRITICAL: Slack + Email + SMS + PagerDuty
- HIGH: Slack + Email
- MEDIUM: Slack only
```

**미연결 이유**:
- `SecurityViolationService` 내부에서만 호출됨
- 독립적인 API 노출 불필요
- 라이브러리 사용자가 Webhook URL 등 설정 필요

**권장 조치**: 📝 문서화만 필요 (내부 사용)

---

## 5. Core 컴포넌트 분석

### 5.1 Core 전체 현황

```
┌───────────────────────────────────────────────────────────────────────────┐
│                     Core 컴포넌트 연결 현황                                │
├───────────────────────────────────────────────────────────────────────────┤
│ ✅ CircuitBreaker                  → 전체적으로 사용                       │
│ ✅ Backoff                         → RetryHandler에서 사용                │
│ ✅ PoolMonitor                     → 여러 미들웨어에서 사용                │
│ ✅ ShutdownCoordinator             → FastAPI adapter에서 사용             │
│ ✅ RuntimeFeedback                 → services에서 사용                    │
│ ✅ SafetyBounds                    → AutoTuning에서 사용                  │
├───────────────────────────────────────────────────────────────────────────┤
│                        미연결/테스트 전용 컴포넌트                          │
├───────────────────────────────────────────────────────────────────────────┤
│ ⚪ CertificateExpiryMonitor        → 테스트에서만 사용                     │
│ ⚪ CertificateAlertManager         → 테스트에서만 사용                     │
│ ⚪ TLSErrorClassifier              → 테스트에서만 사용                     │
│ ⚪ SimpleTLSResilientClient        → 테스트에서만 사용                     │
│ 🟢 AutoRollbackGuard               → AutoTuningService 내부 사용          │
└───────────────────────────────────────────────────────────────────────────┘
```

### 5.2 개별 컴포넌트 분석

---

#### 5.2.1 CertificateExpiryMonitor / CertificateAlertManager

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/core/cert_monitor.py` (259줄) |
| **분류** | ⚪ **테스트 전용** (의도적) |
| **목적** | SSL/TLS 인증서 만료 사전 감지 |
| **도메인 종속성** | ❌ 없음 |

**기능**:
```python
# 인증서 상태 분류
class CertificateStatus(str, Enum):
    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"  # < 30 days
    CRITICAL = "critical"            # < 7 days
    EXPIRED = "expired"

# 모니터 초기화
monitor = CertificateExpiryMonitor(
    warning_days=30,
    critical_days=7,
    alert_callback=my_alert_fn
)
```

**미연결 이유**:
- **의도적 미연결**: 인프라 레벨 기능
- Shopping API는 내부 테스트 환경 (인증서 모니터링 불필요)
- 실제 프로덕션에서 Celery Beat 스케줄러로 연결

**통합 가이드 (프로덕션용)**:
```python
# shopping/tasks/infra_tasks.py
from celery import shared_task
from selfhealing.core.cert_monitor import CertificateExpiryMonitor

@shared_task(name="check_certificate_expiry")
def check_certificate_expiry():
    """매일 실행: 인증서 만료 체크"""
    monitor = CertificateExpiryMonitor(
        warning_days=30,
        critical_days=7,
        alert_callback=send_slack_alert
    )

    endpoints = [
        "https://api.payment-provider.com",
        "https://toss.im",
    ]

    for endpoint in endpoints:
        cert_info = monitor.check_endpoint(endpoint)
        if cert_info.needs_attention:
            monitor.send_alert(cert_info)

# Celery Beat 설정
CELERY_BEAT_SCHEDULE = {
    'check-certificates': {
        'task': 'check_certificate_expiry',
        'schedule': crontab(hour=9, minute=0),  # 매일 오전 9시
    },
}
```

**권장 조치**: 📝 라이브러리 문서화 (테스트베드 연결 불필요)

---

#### 5.2.2 TLSErrorClassifier / SimpleTLSResilientClient

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/core/tls_handler.py` (342줄) |
| **분류** | ⚪ **테스트 전용** (의도적) |
| **목적** | TLS 오류 분류 및 복원력 있는 HTTP 클라이언트 |
| **도메인 종속성** | ❌ 없음 |

**TLS 오류 분류**:
```python
class TLSErrorType(str, Enum):
    CERTIFICATE_EXPIRED = "cert_expired"
    CERTIFICATE_NOT_YET_VALID = "cert_not_yet_valid"
    CERTIFICATE_REVOKED = "cert_revoked"
    CERTIFICATE_HOSTNAME_MISMATCH = "cert_hostname_mismatch"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    # ...
```

**미연결 이유**:
- **의도적 미연결**: HTTP 클라이언트 래퍼 라이브러리
- Shopping API는 내부 환경에서 TLS 문제 없음
- 외부 API 연동 시 라이브러리 사용자가 직접 통합

**통합 가이드 (외부 API 연동 시)**:
```python
from selfhealing.core.tls_handler import SimpleTLSResilientClient

# TLS 복원력 클라이언트 사용
client = SimpleTLSResilientClient(
    base_url="https://external-api.com",
    retry_on_tls_error=True,
    max_retries=3
)

try:
    response = client.get("/endpoint")
except TLSError as e:
    if e.error_type == TLSErrorType.CERTIFICATE_EXPIRED:
        # 인증서 만료 알림
        send_urgent_alert(e)
```

**권장 조치**: 📝 라이브러리 문서화 (테스트베드 연결 불필요)

---

#### 5.2.3 ShutdownCoordinator

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/core/shutdown_coordinator.py` (341줄) |
| **분류** | 🟢 **라이브러리 기능** (연결됨) |
| **목적** | Graceful Shutdown 관리 |
| **도메인 종속성** | ❌ 없음 |

**현재 상태**: ✅ FastAPI adapter에서 사용 중
```python
# selfhealing/adapters/fastapi/middleware.py에서 import
from selfhealing.core.shutdown_coordinator import ShutdownCoordinator
```

**권장 조치**: ✅ 이미 연결됨 (문서화 보완)

---

#### 5.2.4 AutoRollbackGuard

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/core/auto_rollback_guard.py` |
| **분류** | 🟢 **내부 사용** |
| **목적** | Auto-Tuning 시 안전하지 않은 변경 자동 롤백 |
| **도메인 종속성** | ❌ 없음 |

**현재 상태**: ✅ AutoTuningService 내부에서 사용 중

**권장 조치**: ✅ 현재 상태 유지 (내부 사용)

---

## 6. Audit 컴포넌트 분석

### 6.1 Audit 전체 현황

Audit 시스템은 **컴플라이언스 및 법적 증거 보존**을 위한 고급 기능들로 구성됩니다.
대부분 **CLI 도구** 또는 **인프라 레벨 통합**으로 사용됩니다.

```
┌───────────────────────────────────────────────────────────────────────────┐
│                     Audit 컴포넌트 연결 현황                               │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  📝 Core Audit (API 연결됨)                                               │
│  ─────────────────────────                                                 │
│  ✅ ContinuousAuditRecorder        → AuditEntry API 연결                  │
│  ✅ AuditLogAdapter                → 다양한 백엔드 지원                   │
│                                                                            │
│  🔧 고급 Audit (CLI/인프라용)                                             │
│  ─────────────────────────────                                             │
│  ⚪ WriteAheadLog (WAL)            → 테스트 전용 (장애 복구용)            │
│  ⚪ AuditWatchdog                  → 테스트 전용 (헬스 모니터링)          │
│  ⚪ SignedManifest / MerkleTree    → 테스트 전용 (법적 무결성 증명)       │
│  ⚪ AuditExporter                  → CLI 도구로 제공                      │
│  ⚪ AuditIntegrityVerifier         → CLI 도구로 제공                      │
│  ⚪ ResilientContinuousAuditRecorder → 테스트 전용 (HA 구성)              │
│  ⚪ IntegratedAuditRecorder        → 테스트 전용 (통합 레코더)            │
│                                                                            │
│  🔌 WORM 어댑터 (외부 저장소용)                                           │
│  ──────────────────────────────                                            │
│  ⚪ S3ObjectLockAdapter            → 테스트 전용 (AWS S3 WORM)            │
│  ⚪ LokiAdapter                    → 테스트 전용 (Grafana Loki)           │
│  ⚪ HTTPWebhookAdapter             → 테스트 전용 (Webhook)                │
│                                                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### 6.2 개별 Audit 컴포넌트 분석

---

#### 6.2.1 WriteAheadLog (WAL)

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/audit/wal.py` |
| **분류** | ⚪ **테스트 전용** (인프라 레벨) |
| **목적** | 장애 복구를 위한 Write-Ahead 로그 |
| **도메인 종속성** | ❌ 없음 |

**기능**:
```python
# DB 쓰기 전에 로컬 파일에 먼저 기록
# 장애 발생 시 WAL에서 복구 가능
wal = WriteAheadLog(config=WALConfig(directory="/var/log/audit/wal"))
wal.write(entry)  # 먼저 WAL에 기록
db.save(entry)    # 그 후 DB에 저장
wal.commit()      # 성공 시 WAL 정리
```

**미연결 이유**:
- 분산 시스템에서의 장애 복구용 (단일 서버에서는 불필요)
- 인프라 아키텍트가 필요에 따라 활성화

---

#### 6.2.2 AuditWatchdog

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/audit/audit_watchdog.py` |
| **분류** | ⚪ **테스트 전용** |
| **목적** | Audit 시스템 헬스 모니터링 및 하트비트 |
| **도메인 종속성** | ❌ 없음 |

**기능**:
```python
# 외부 모니터링 시스템에 하트비트 전송
# Audit 시스템 장애 감지
watchdog = AuditWatchdog(config=WatchdogConfig(
    heartbeat_url="https://monitoring.example.com/heartbeat",
    interval_seconds=30
))
watchdog.start()
```

**미연결 이유**:
- 외부 모니터링 시스템(PagerDuty, Datadog 등) 연동 필요
- 프로덕션 인프라 구성에 따라 활성화

---

#### 6.2.3 SignedManifest / MerkleTree

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/audit/signed_manifest.py` |
| **분류** | ⚪ **테스트 전용** (컴플라이언스용) |
| **목적** | 블록체인 수준 무결성 증명, RFC3161 타임스탬프 |
| **도메인 종속성** | ❌ 없음 |

**기능**:
```python
# 법적 효력이 있는 감사 로그 매니페스트 생성
manifest = SignedManifest()
manifest.add_entries(audit_entries)
manifest.finalize()  # Merkle Root 계산
manifest.sign(private_key)  # 디지털 서명
manifest.timestamp(rfc3161_server)  # 공인 타임스탬프
manifest.save("/audit/manifests/2024-Q4.manifest")
```

**미연결 이유**:
- 금융/의료 등 규제 산업에서만 필요
- 법적 감사 요구사항이 있는 경우에만 활성화
- CLI 도구로 제공됨 (`python -m selfhealing.audit.signed_manifest`)

---

#### 6.2.4 AuditExporter

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/audit/export.py` |
| **분류** | 🔧 **CLI 도구** |
| **목적** | Audit 로그 내보내기 (JSON, CSV, WORM) |
| **도메인 종속성** | ❌ 없음 |

**CLI 사용법**:
```bash
# JSON 내보내기
python -m selfhealing.audit.export --format json --output /backup/audit.json

# S3 Object Lock으로 내보내기
python -m selfhealing.audit.export --target s3 --bucket audit-archive

# Loki로 푸시
python -m selfhealing.audit.export --target loki --url http://loki:3100
```

**미연결 이유**:
- 배치 작업으로 실행 (Celery Beat 또는 cron)
- API로 노출할 필요 없음

---

#### 6.2.5 AuditIntegrityVerifier

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/audit/verify_audit_integrity.py` |
| **분류** | 🔧 **CLI 도구** |
| **목적** | Audit 로그 해시 체인 무결성 검증 |
| **도메인 종속성** | ❌ 없음 |

**CLI 사용법**:
```bash
# 전체 검증
python -m selfhealing.audit.verify_audit_integrity

# 특정 기간 검증
python -m selfhealing.audit.verify_audit_integrity \
    --start 2024-01-01 --end 2024-12-31

# JSON 출력
python -m selfhealing.audit.verify_audit_integrity --format json
```

**미연결 이유**:
- 정기 감사 시 수동 실행
- 자동화할 경우 Celery Beat로 스케줄링

---

#### 6.2.6 WORM 어댑터 (S3ObjectLockAdapter, LokiAdapter, HTTPWebhookAdapter)

| 항목 | 내용 |
|------|------|
| **파일** | `selfhealing/adapters/audit/worm_adapters.py` |
| **분류** | ⚪ **테스트 전용** (인프라 레벨) |
| **목적** | Write-Once-Read-Many 저장소 연동 |
| **도메인 종속성** | ❌ 없음 |

**어댑터 종류**:
```python
# AWS S3 Object Lock (Compliance Mode)
adapter = S3ObjectLockAdapter(
    bucket="audit-logs",
    region="ap-northeast-2",
    retention_days=365 * 7  # 7년 보관
)

# Grafana Loki
adapter = LokiAdapter(
    url="http://loki:3100",
    labels={"app": "selfhealing", "env": "prod"}
)

# 범용 Webhook
adapter = HTTPWebhookAdapter(
    url="https://siem.example.com/ingest",
    headers={"Authorization": "Bearer xxx"}
)
```

**미연결 이유**:
- 외부 저장소 인프라 필요 (AWS S3, Loki 등)
- AuditExporter CLI에서 사용
- 프로덕션 인프라 구성에 따라 선택

---

## 6a. 시그널 및 이벤트 핸들러 분석

### 6a.1 Django 시그널 현황

시그널은 직접 import하지 않아도 특정 이벤트 발생 시 자동 실행됩니다.

```
┌───────────────────────────────────────────────────────────────────────────┐
│                     Django 시그널 연결 현황                                │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  📍 shopping/signals.py (apps.py에서 import됨 ✅)                         │
│  ───────────────────────────────────────────────                           │
│  ✅ @receiver(pre_social_login)                                            │
│     → 소셜 로그인 시 이메일 자동 인증                                      │
│                                                                            │
│  ✅ @receiver(post_save, sender=SocialAccount)                             │
│     → 신규 소셜 계정 생성 시 이메일 인증                                   │
│                                                                            │
│  ✅ @receiver(post_save, sender=Order)                                     │
│     → 주문 생성 시 주문번호 자동 생성                                      │
│                                                                            │
│  📍 shopping/models/product.py                                             │
│  ─────────────────────────────                                             │
│  ✅ @receiver([post_save, post_delete], sender=Category)                   │
│     → 카테고리 변경 시 캐시 무효화                                         │
│                                                                            │
│  ✅ @receiver([post_save, post_delete], sender=Product)                    │
│     → 상품 변경 시 캐시 무효화                                             │
│                                                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### 6a.2 시그널 활성화 확인

```python
# shopping/apps.py
class ShoppingConfig(AppConfig):
    def ready(self):
        import shopping.signals  # noqa ← 이 import가 시그널 활성화!
        self._configure_selfhealing()
```

⚠️ **주의**: `apps.py`의 `ready()` 메서드에서 signals 모듈을 import해야 시그널이 동작합니다!

### 6a.3 내부 이벤트 버스

Self-Healing 패키지는 내부 Pub-Sub 이벤트 버스도 제공합니다:

```python
# selfhealing/services/event_bus.py
class EventType(Enum):
    EMERGENCY_LEVEL_CHANGED = "emergency_level_changed"
    ERROR_BUDGET_CRITICAL = "error_budget_critical"
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"
    CONFIG_UPDATED = "config_updated"
    KILL_SWITCH_ACTIVATED = "kill_switch_activated"
    # ...

# 사용 예시
event_bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
event_bus.publish(EventType.EMERGENCY_LEVEL_CHANGED, payload)
```

---

## 6b. 환경 변수 기반 기능 토글 (Feature Flags)

### 6b.1 발견된 Feature Flags

코드에 로직은 있지만 환경 변수가 설정되어야만 활성화되는 기능들입니다:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                    환경 변수 기반 기능 토글 전체 목록                         │
├──────────────────────────────┬───────────────────────────────────────────────┤
│ 환경 변수                    │ 기능 / 기본값                                 │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ **인증/보안**                │                                               │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ DISABLE_SELFHEALING_AUTH     │ Self-Healing API 인증 비활성화 / false        │
│ SELFHEALING_THRESHOLD_OPERATOR│ Operator 권한 임계값 / 0.15                  │
│ SELFHEALING_THRESHOLD_ADMIN  │ Admin 권한 임계값 / 0.30                      │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ **테스트/개발**              │                                               │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ ENABLE_RESILIENCE_TESTING    │ 복원력 테스트 모드 / false                    │
│ ENABLE_STRESS_TESTS          │ 스트레스 테스트 URL 노출 / false              │
│ CHAOS_ENABLED                │ Chaos Engineering 기능 / false                │
│ DISABLE_RATE_LIMITING        │ Rate Limiting 비활성화 / false                │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ **인프라**                   │                                               │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ USE_CONNECTION_POOL          │ 커넥션 풀 사용 / false                        │
│ POOL_CB_FAILURE_THRESHOLD    │ Pool CB 실패 임계값 / 3                       │
│ POOL_CB_SUCCESS_THRESHOLD    │ Pool CB 성공 임계값 / 2                       │
│ POOL_CB_RECOVERY_TIMEOUT     │ Pool CB 복구 타임아웃 / 10초                  │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ **Audit**                    │                                               │
├──────────────────────────────┼───────────────────────────────────────────────┤
│ AUDIT_HEARTBEAT_URL          │ Watchdog 하트비트 URL / 없음                  │
│ AUDIT_HEARTBEAT_INTERVAL     │ 하트비트 간격 / 30초                          │
│ AUDIT_BUFFER_CAPACITY        │ 버퍼 크기 / 10000                             │
│ AUDIT_FLUSH_INTERVAL         │ 플러시 간격 / 1초                             │
│ AUDIT_SYSLOG_ENABLED         │ Syslog 폴백 활성화 / true                     │
│ SELFHEALING_WAL_ENABLED      │ Write-Ahead Log 활성화 / false                │
└──────────────────────────────┴───────────────────────────────────────────────┘
```

### 6b.2 Feature Flag 사용 패턴

```python
# 조건부 URL 노출 예시 (urls.py)
if getattr(settings, "DEBUG", False) or getattr(settings, "ENABLE_STRESS_TESTS", False):
    urlpatterns += [
        path("stress/", include(stress_urls)),
    ]

# 조건부 미들웨어 활성화 예시
class ChaosMiddleware:
    def __init__(self, get_response):
        self._enabled = getattr(settings, 'CHAOS_MIDDLEWARE_ENABLED', False)

    def __call__(self, request):
        if not self._enabled:
            return self.get_response(request)
        # Chaos 로직...
```

---

## 6c. Management Commands 분석

### 6c.1 발견된 Commands

```
┌───────────────────────────────────────────────────────────────────────────┐
│                   Management Commands 연결 현황                            │
├───────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  shopping/management/commands/                                             │
│  ────────────────────────────                                              │
│  🔧 cleanup_old_carts.py           → Celery Beat 연결 가능                 │
│  🔧 delete_unverified_users.py     → Celery Beat 연결 권장                 │
│  🔧 create_test_data.py            → 수동 실행용                           │
│  🔧 create_load_test_users.py      → 부하 테스트 전 실행                   │
│  🔧 generate_self_healing_alerts.py → 데모/테스트용                        │
│  🔧 security_review.py             → 정기 보안 감사용                      │
│  🔧 test_point_expiry.py           → 포인트 만료 테스트                    │
│                                                                            │
└───────────────────────────────────────────────────────────────────────────┘
```

### 6c.2 Celery Beat 연결 권장 Commands

```python
# CELERY_BEAT_SCHEDULE에 추가 권장:
'cleanup-old-carts': {
    'task': 'shopping.tasks.cleanup.cleanup_old_carts',
    'schedule': crontab(hour=3, minute=0),  # 매일 새벽 3시
},
'delete-unverified-users': {
    'task': 'shopping.tasks.cleanup.delete_unverified_users',
    'schedule': crontab(hour=4, minute=0, day_of_week=0),  # 매주 일요일
},
```

---

## 7. 독립 기능 탐색 방법

### 7.1 "어디에도 연결 안 된 독립 기능" 찾기

**질문**: 정의됐지만 **어디서도 import되지 않는** 완전히 독립된 기능은 어떻게 찾나요?

**답변**: Import 역추적으로 찾을 수 있습니다!

```bash
#!/bin/bash
# find_orphan_code.sh - 고아 코드 탐색 스크립트

# 1. 모든 클래스 목록 추출
grep -rh "^class " packages/selfhealing-python/src/selfhealing/**/*.py \
    | grep -v test | grep -v __pycache__ \
    | sed 's/class \([A-Za-z_]*\).*/\1/' \
    | sort -u > /tmp/all_classes.txt

# 2. 각 클래스의 import 횟수 확인
while read class_name; do
    count=$(grep -r "import.*$class_name\|from.*import.*$class_name" \
        --include="*.py" . 2>/dev/null | grep -v test | wc -l)
    echo "$count $class_name"
done < /tmp/all_classes.txt | sort -n

# 출력 예시:
# 0 OrphanClass          <- 완전히 독립 (고아 코드)
# 1 TestOnlyClass        <- __init__.py에서만 export
# 2 InternalClass        <- 내부 사용
# 10+ CoreClass          <- 핵심 컴포넌트
```

### 7.2 분석 결과

현재 프로젝트에서 **완전히 독립된 고아 코드**는 발견되지 않았습니다:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Import 횟수별 분류 결과                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ✅ 0회 (고아 코드): 0개                                                 │
│     - 완전히 독립된 코드 없음                                            │
│                                                                          │
│  ⚪ 1~2회 (테스트/내부 전용): 15개                                       │
│     - __init__.py에서 export만 됨                                        │
│     - 테스트에서만 import됨                                              │
│     - 의도적 미연결 (도메인 프리)                                        │
│                                                                          │
│  🟢 3회 이상 (실제 사용): 나머지 전부                                    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 7.3 왜 고아 코드가 없는가?

1. **`__init__.py` Export 패턴**: 모든 컴포넌트는 패키지 `__init__.py`에서 export됨
2. **테스트 커버리지**: 모든 기능에 대한 유닛 테스트 존재
3. **CLI 도구 제공**: Audit 도구들은 `__main__` 블록으로 직접 실행 가능
4. **문서화된 의도적 미연결**: 도메인 프리 기능은 문서에 명시됨

### 7.4 고아 코드 방지 전략

```python
# 각 모듈의 __init__.py에서 모든 public API export
# selfhealing/audit/__init__.py
__all__ = [
    "WriteAheadLog",
    "AuditWatchdog",
    "SignedManifest",
    "MerkleTree",
    "AuditExporter",
    "AuditIntegrityVerifier",
    # ... 모든 public 클래스
]

# 이렇게 하면:
# 1. from selfhealing.audit import * 가 동작
# 2. import 역추적 시 최소 1회 이상 카운트
# 3. 문서 자동 생성 도구(Sphinx 등)에서 발견 가능
```

---

## 8. 통합 로드맵

### 6.1 요약 매트릭스

| 컴포넌트 | 분류 | 현재 상태 | 권장 조치 |
|----------|------|-----------|-----------|
| **미들웨어** ||||
| TieringMiddleware | 🟢 라이브러리 | 미연결 | 📝 문서화만 |
| SensitiveAccessLoggingMiddleware | 🟡 선택적 | 미연결 | 📝 문서화만 |
| ActorContextMiddleware | 🟡 선택적 | 미연결 | 📝 문서화만 |
| PoolCircuitBreakerMiddleware | 🟢 **v6.2.0 해결** | **연결 가능** | ✅ `USE_POOL_CIRCUIT_BREAKER=TRUE` |
| PoolTimeoutMiddleware | 🟡 환경별 | local.py만 | ✅ 현재 유지 |
| **서비스** ||||
| IdempotencyService | ⚪ 테스트 전용 | 테스트만 | 📝 라이브러리 문서화 |
| SecurityViolationService | ⚪ 테스트 전용 | 테스트만 | 📝 라이브러리 문서화 |
| SecurityNotificationService | ⚪ 간접 사용 | 내부 사용 | 📝 문서화만 |
| **Core 컴포넌트** ||||
| CertificateExpiryMonitor | ⚪ 테스트 전용 | 테스트만 | 📝 라이브러리 문서화 |
| TLSErrorClassifier | ⚪ 테스트 전용 | 테스트만 | 📝 라이브러리 문서화 |
| ShutdownCoordinator | 🟢 라이브러리 | 연결됨 | ✅ 문서화 보완 |
| AutoRollbackGuard | 🟢 내부 사용 | 내부 사용 | ✅ 현재 유지 |

### 6.2 도메인 프리 설계 원칙

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Self-Healing 도메인 프리 원칙                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. 라이브러리는 도메인 중립적으로 유지                                  │
│     - Shopping API는 테스트베드일 뿐                                     │
│     - 결제, 주문 등 도메인 로직과 무관                                   │
│                                                                          │
│  2. 모든 통합은 라이브러리 사용자의 선택                                 │
│     - MIDDLEWARE 추가 여부는 프로젝트마다 다름                           │
│     - Celery 태스크 연결도 선택적                                        │
│                                                                          │
│  3. 테스트베드(Shopping API)의 역할                                      │
│     - 기능 동작 검증                                                     │
│     - 부하 테스트 환경                                                   │
│     - 통합 예시 제공                                                     │
│                                                                          │
│  4. 미연결 ≠ 미사용                                                      │
│     - 의도적으로 테스트에서만 검증                                       │
│     - 문서화로 사용법 안내                                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.3 프로덕션 통합 체크리스트

라이브러리 사용자가 프로덕션에서 Self-Healing을 통합할 때의 체크리스트:

```
□ 필수 미들웨어
  ☑ HealthBridgeMiddleware (최상단)
  ☑ SelfHealingMiddleware
  ☑ HybridRateLimitMiddleware

□ 선택적 미들웨어 (요구사항에 따라)
  □ TieringMiddleware - Emergency Mode Load Shedding
  □ SensitiveAccessLoggingMiddleware - 컴플라이언스 감사
  □ ActorContextMiddleware - 상세 Actor 추적

□ 서비스 통합 (도메인 로직에 따라)
  □ IdempotencyService - 결제/주문 멱등성
  □ SecurityViolationService - 보안 위반 처리

□ 인프라 태스크 (운영 요구사항에 따라)
  □ CertificateExpiryMonitor - 인증서 모니터링
  □ TLSResilientClient - 외부 API TLS 복원력
```

---

## 📚 관련 문서

- [52_HOOK_REGISTRY_ARCHITECTURE.md](52_HOOK_REGISTRY_ARCHITECTURE.md) - Hook Registry 아키텍처
- [02_ARCHITECTURE.md](02_ARCHITECTURE.md) - 시스템 아키텍처
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 설정 가이드
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드

---

*문서 끝*

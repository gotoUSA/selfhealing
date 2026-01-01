# Self-Healing 시스템 미들웨어 레퍼런스

> 버전: 2.2.0
> 최종 업데이트: 2026-01-02

## 변경 로그

### v2.2.0 (2026-01-02)
- Interfaces 모듈 전체 추가 (Repository, Cache, TaskQueue, WebFramework 등)
- Metrics 모듈 추가 (SelfHealingMetrics, EventHandlers, MetricReconciler 등)
- Utils 모듈 추가 (AsyncHealingLogger, time utilities)
- Tasks 모듈 추가 (chaos_scheduler, config_apply, drift_detection, governance)
- Models/SLO/Config 모듈 추가
- DRF/Django 컴포넌트 추가 (permissions, throttle_adapter, reauthentication 등)
- Notification 인터페이스 추가
- Celery Signal Hooks 상세화
- SQLAlchemy Adapters 추가
- Frameworks Adapters (FastAPIAdapter) 추가
- Context 모듈 헬퍼 함수 보완

### v2.1.0 (2026-01-01)
- 실제 코드베이스와 문서 일치성 검증
- 누락된 Adapters 섹션 추가 (Alert, Cache, Metrics, Queue, Observability 등)
- 누락된 Services 섹션 추가 (FinOps, Learning, Error Budget Gate 등)
- Audit Backends 상세 추가 (CloudWatch, Datadog, S3 WORM 등)
- Resilience 모듈 추가
- 미들웨어 통합/연결에 집중

### v2.0.0 (2026-01-01)
- 미들웨어 중심으로 문서 재구성
- API 엔드포인트/Serializers → 07_CONTROL_API.md로 이동
- Services/Adapters 상세 → 02_ARCHITECTURE.md로 이동
- 미들웨어 통합/연결에 집중

### v1.5.0 (2026-01-01)
- Django Views API 엔드포인트, Serializers, Permissions 섹션 추가 (이동됨)

### v1.3.0 (2026-01-01)
- trace_id_middleware 함수형 미들웨어 추가
- TieringMiddleware 섹션 추가

### v1.2.0 (2026-01-01)
- FastAPI 미들웨어 섹션 추가
- ActorContextMiddlewareSimple 상세 설명 추가

### v1.1.0 (2026-01-01)
- 초기 문서 작성

---

## 목차

1. [개요](#1-개요)
2. [등록된 미들웨어](#2-등록된-미들웨어)
3. [미등록 미들웨어](#3-미등록-미들웨어)
4. [미들웨어 연결 서비스](#4-미들웨어-연결-서비스)
5. [유틸리티 컴포넌트](#5-유틸리티-컴포넌트)
6. [미등록 기능 목록](#6-미등록-기능-목록)
7. [FastAPI 미들웨어](#7-fastapi-미들웨어)
8. [API Tiering (트래픽 제어)](#8-api-tiering-트래픽-제어)
9. [Adapters (어댑터 계층)](#9-adapters-어댑터-계층)
10. [추가 Services](#10-추가-services-미등록)
11. [Audit Backends](#11-audit-backends-미등록)
12. [Resilience 모듈](#12-resilience-모듈-미등록)
13. [추가 Core 컴포넌트](#13-추가-core-컴포넌트-미등록)
14. [추가 Audit 컴포넌트](#14-추가-audit-컴포넌트-미등록)
15. [Celery Adapters](#15-celery-adapters-미등록)
16. [Interfaces 모듈](#16-interfaces-모듈-미등록)
17. [Metrics 모듈](#17-metrics-모듈-미등록)
18. [Utils 모듈](#18-utils-모듈-미등록)
19. [Tasks 모듈](#19-tasks-모듈-celery-beat)
20. [Models/SLO 모듈](#20-modelsslo-모듈-미등록)
21. [DRF/Django 컴포넌트](#21-drfdjango-컴포넌트-미등록)
22. [Notification 인터페이스](#22-notification-인터페이스-미등록)
23. [SQLAlchemy Adapters](#23-sqlalchemy-adapters-미등록)
24. [Frameworks Adapters](#24-frameworks-adapters-미등록)
25. [Provider Registry/Factory](#25-provider-registryfactory-미등록)
26. [Django Adapters](#26-django-adapters-미등록)
27. [Throttle Services](#27-throttle-services-미등록)

---

## 1. 개요

Self-Healing 시스템은 장애 자동 감지, 복구, 그리고 감사 로깅을 제공하는 포괄적인 인프라 레이어입니다.

### 미들웨어 등록 순서 (settings.py)

```python
MIDDLEWARE = [
    # 1. HealthBridgeMiddleware - DB-independent 헬스 체크 (최상단 필수!)
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",

    # 2. SelfHealingMiddleware - DB 오류/5xx 감지 및 DLQ 자동 적재
    "selfhealing.api.django.middleware.SelfHealingMiddleware",

    # 3-9. Django Core Middlewares
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",

    # 10. HybridRateLimitMiddleware - Redis/Local 하이브리드 레이트 리미팅
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",

    # 11. ChaosMiddleware - HELLMODE 테스트용 장애 주입
    "myproject.middleware.chaos_middleware.ChaosMiddleware",

    # 12. ConnectionPoolLimiterMiddleware - 커넥션 풀 제한 (테스트용)
    "myproject.middleware.chaos_middleware.ConnectionPoolLimiterMiddleware",
]
```

---

## 2. 등록된 미들웨어

### 2.1 HealthBridgeMiddleware

**경로**: `selfhealing.api.django.middleware.HealthBridgeMiddleware`

**목적**: DB-independent 헬스 엔드포인트 제공

**기능**:
- `/api/self-healing/health/l3/`, `/api/self-healing/health/bridge/` 경로 처리
- DB 죽어도 즉시 응답 (Worker Saturation 방지)
- CircuitBreaker 스냅샷을 메모리에 저장하여 외부에서 CB 상태 관찰 가능

**위치**: MIDDLEWARE 리스트 **최상단** 필수

---

### 2.2 SelfHealingMiddleware

**경로**: `selfhealing.api.django.middleware.SelfHealingMiddleware`

**목적**: 장애 자동 감지 및 DLQ 적재

**기능**:
- **DB 오류 감지**: OperationalError, InterfaceError, DatabaseError 등
- **HTTP 5xx 감지**: 502, 503, 504 응답 모니터링
- **CircuitBreaker 자동 기록**: 실패 시 `record_failure()` 호출
- **DLQ 자동 적재**: 복구 가능한 POST/PUT/PATCH 요청 자동 저장
- **CB OPEN 선제적 DLQ 적재**: CB가 열려있으면 요청을 바로 DLQ에 저장
- **도메인 추론**: 경로 패턴에서 도메인 자동 추론 (`/payments/` → payment)

**설정 옵션** (settings.py):
```python
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [r"^/api/orders/", r"^/api/payments/", ...]
SELF_HEALING_INFRA_FAILURE_PATHS = [r"^/api/orders/", r"^/api/payments/", ...]
SELF_HEALING_DOMAIN_MAPPING = {"/payments/": "payment", "/orders/": "order", ...}
```

---

### 2.3 HybridRateLimitMiddleware

**경로**: `selfhealing.api.django.rate_limit.HybridRateLimitMiddleware`

**목적**: Control API 레이트 리미팅

**기능**:
- **L2 (Primary)**: Redis 기반 슬라이딩 윈도우 (기본 100 req/min)
- **L1 (Fallback)**: Redis 장애 시 로컬 메모리 레이트 리밋 (기본 10 req/min)
- Mini CircuitBreaker로 Redis 헬스 체크
- Jitter 기반 점진적 복구 (Thundering Herd 방지)
- Prometheus 메트릭 제공

**설정**: RuntimeConfig API를 통해 런타임 조정 가능

---

### 2.4 ChaosMiddleware

**경로**: `myproject.middleware.chaos_middleware.ChaosMiddleware`

**목적**: HELLMODE 테스트용 장애 주입

**기능**:
- **DB Lock Timeout 주입**: `X-DB-Lock-Timeout` 헤더
- **Statement Timeout 주입**: `X-DB-Statement-Timeout` 헤더
- **Chaos 모드**:
  - `deadlock`: 데드락 유발
  - `pool-starve`: 커넥션 풀 고갈
  - `slow-query`: 의도적 느린 쿼리
  - `connection-poison`: 랜덤 커넥션 종료
  - `random-failure`: 확률적 실패
- **랜덤 pg_sleep 주입**: 락 점유 시간 강제 연장

**보안**: `X-Test-Mode: hellmode` 헤더 필수

---

### 2.5 ConnectionPoolLimiterMiddleware

**경로**: `myproject.middleware.chaos_middleware.ConnectionPoolLimiterMiddleware`

**목적**: 테스트용 동시 커넥션 수 제한

**기능**:
- HELLMODE에서 동시 커넥션을 5개로 제한
- 풀 고갈 시 503 반환
- `X-Max-Connections` 헤더로 제한 수 조정 가능

---

## 3. 미등록 미들웨어

> 코드에는 존재하지만 settings.py MIDDLEWARE에 등록되지 않은 미들웨어

### 3.1 PoolCircuitBreakerMiddleware

**경로**: `selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware`

**목적**: Pool 상태 기반 Fail Fast

**기능**:
- 요청 도착 시 Pool 상태 체크 (캐시 기반, Non-Blocking)
- Pool 고갈 시 즉시 503 반환 (Pool 대기 안함)
- CircuitBreaker 상태로 자동 복구 관리
- 백그라운드 스레드에서 Pool 상태 갱신 (기본 100ms)
- Stale 캐시 감지 및 안전 폴백

**상태**: CLOSED → OPEN → HALF_OPEN → CLOSED

---

### 3.2 AuditMiddleware

**경로**: `selfhealing.api.django.audit_middleware.AuditMiddleware`

**목적**: 중앙화된 Audit 이벤트 수집 및 해시 체인 기록

**기능**:
- 요청 시작 시 `request_id` 생성 및 버퍼 초기화
- 응답 반환 전 모든 이벤트 수집
- ContinuousAuditRecorder를 통해 일괄 기록 (HashChain 포함)
- Fail-Open 정책: Audit 실패가 비즈니스 로직 중단 안함

**위치**: MIDDLEWARE 리스트 **맨 마지막** 권장

---

### 3.3 SensitiveAccessLoggingMiddleware

**경로**: `selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware`

**목적**: 민감 엔드포인트 접근 로깅

**기능**:
- `/api/self-healing/audit/`, `/api/self-healing/config/` 등 접근 기록
- IP 마스킹 (내부 IP는 네트워크 부분만 표시)
- Fail-Open: 로깅 실패가 요청 처리 차단 안함

---

### 3.4 TieringMiddleware

**경로**: `selfhealing.api.django.tiering.middleware.TieringMiddleware`

**목적**: Emergency Mode 시 API Tier 기반 트래픽 제어

**기능**:
- Emergency Level에 따라 API Tier별 차등 제한
- LEVEL_1: `non_essential` 차단
- LEVEL_2: `standard` 90% 차단
- LEVEL_3: `critical` 50% 차단, 나머지 100% 차단

---

### 3.5 ActorContextMiddleware

**경로**: `myproject.middleware.actor_middleware.ActorContextMiddleware`

**목적**: 현재 사용자를 thread-local하게 자동 추적

**기능**:
- 요청마다 ActorContext 설정 (context manager 사용)
- 어디서든 `ActorContext.get_current()` 로 현재 actor 조회
- Django, Celery 등 다양한 환경 지원
- IP 주소, 세션 ID 등 보안 감사 정보 자동 수집
- AuditEntry에 자동으로 actor_id, actor_type 채움

**간소화 버전**: `ActorContextMiddlewareSimple` - selfhealing 패키지 없이 thread-local만 사용

**ActorContextMiddlewareSimple 기능**:
- thread-local 스토리지 기반 (contextvars 미사용)
- `actor_id`, `actor_type`, `ip_address`, `request_path` 자동 추적
- 요청 완료 후 자동 정리
- selfhealing 패키지 의존성 없음

---

### 3.6 PoolTimeoutMiddleware

**경로**: `myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware`

**목적**: SQLAlchemy Pool Timeout 발생 시 즉시 503 반환

**기능**:
- Pool Timeout (QueuePool limit, timeout, pool exhausted 등) 감지
- 복잡한 CircuitBreaker 로직 없이 직접 503 반환
- Timeout 카운터 통계 제공
- SQLAlchemy TimeoutError 및 문자열 기반 감지 지원

**감지 패턴**:
- `timeout`
- `queuepool limit`
- `pool exhausted`
- `no connections available`
- `can't get connection`

---

### 3.7 trace_id_middleware (함수형)

**경로**: `selfhealing.audit.trace.trace_id_middleware`

**목적**: 분산 추적을 위한 Trace ID 자동 관리

**기능**:
- 요청 헤더에서 Trace ID 추출 (X-Request-ID, X-Trace-ID, traceparent 등)
- 없으면 새 Trace ID 자동 생성 (`req-{uuid4_short}`)
- 요청 객체에 `trace_id` 속성 추가
- 응답에 `X-Request-ID` 헤더 자동 추가
- 요청 완료 후 Trace ID 자동 정리

**지원 헤더**:
- `X-Request-ID`
- `X-Trace-ID`
- `X-Correlation-ID`
- `traceparent` (W3C Trace Context)
- `X-Amzn-Trace-Id` (AWS X-Ray)

**사용법**:
```python
MIDDLEWARE = [
    'selfhealing.audit.trace.trace_id_middleware',
    # ... other middleware
]
```

---

## 4. 미들웨어 연결 서비스

> 미들웨어가 호출하는 핵심 서비스들

### 4.1 CircuitBreakerService

**경로**: `selfhealing.services.circuit_breaker.service.CircuitBreakerService`

**기능**:
- Toggle 기반 서킷브레이커 관리
- Force Open/Close 수동 제어
- 복구 시 조건부 리플레이 트리거
- Rate Limit 폭풍 감지 (429 자동 CB 오픈)
- Self-DDoS 보호 (재시도 증폭 방지)
- 폴백 전략: 캐시, DLQ, 기본 응답

---

### 4.2 DLQService

**경로**: `selfhealing.services.dlq_service.DLQService`

**기능**:
- 실패 작업 저장 (전체 Forensic 컨텍스트 포함)
- DLQ 항목 조회 및 필터링
- 라이프사이클 관리: pending → reviewing → resolved/rejected
- 배치 리플레이
- 정리, 아카이브, 퍼지 관리

---

### 4.3 ReplayService

**경로**: `selfhealing.services.replay_service`

**기능**:
- **Manual Replay**: 운영자 선택 개별 리플레이
- **Batch Replay**: 필터 기반 다중 항목 리플레이
- **Conditional Replay**: CB 복구 시 자동 리플레이
- 거버넌스 체크 자동 수행

---

### 4.4 SystemControlManager

**경로**: `selfhealing.services.system_control.SystemControlManager`

**기능**:
- 글로벌 킬 스위치
- Dry Run 모드 (관찰만, 실행 안함)
- Pluggable 백엔드: File, Redis, Memory
- 서버 재시작 시 자동 상태 복구
- 다중 서버 상태 공유 (Redis 사용 시)

---

### 4.5 EmergencyManager

**경로**: `selfhealing.services.emergency_mode.manager`

**기능**:
- Emergency Level 관리: NORMAL, LEVEL_1, LEVEL_2, LEVEL_3
- Recovery Gate: 자동 복구 활성화 조건 판단
- 레벨별 자동화 제한

---

### 4.6 ErrorBudgetService

**경로**: `selfhealing.services.error_budget`

**기능**:
- Error Budget 잔여량 계산 (SLO 기반)
- Burn Rate 계산 (Fast/Slow)
- 배포 동결 권고 (Freeze Advisor)
- 결정 기록 (Audit Trail)

---

### 4.7 HealthCheckService

**경로**: `selfhealing.services.health_check.HealthCheckService`

**기능**:
- 기본 DB 연결 확인
- 모든 DB 연결 확인
- 커넥션 풀 상태 조회
- Kubernetes Liveness/Readiness 프로브

---

### 4.8 IdempotencyService

**경로**: `selfhealing.services.idempotency_service.IdempotencyService`

**기능**:
- 멱등성 키 관리 (안전한 재시도)
- 도메인별 키 생성: 외부서비스, 내부프로세스, 비동기태스크, 이벤트
- 캐시 기반 중복 체크

---

### 4.9 ForensicContext

**경로**: `selfhealing.services.forensic_context`

**기능**:
- 타이밍 정보 캡처 (요청/응답 타임스탬프, 레이턴시)
- 재시도 히스토리 기록
- 상태 스냅샷 (before/after)
- Task/Worker 컨텍스트 기록

**관련 클래스**:
- `ForensicContext`: 포렌식 컨텍스트 데이터 클래스
- `ForensicContextBuilder`: Fluent API 기반 빌더
- `RetryAttempt`: 재시도 시도 기록
- `StateSnapshot`: 상태 스냅샷 (도메인 중립적 설계)
- `capture_forensic_context()`: 헬퍼 함수

---

## 5. 유틸리티 컴포넌트

### 5.1 PoolCircuitBreaker (싱글톤)

**경로**: `selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreaker`

**기능**:
- Pool 상태 기반 CB
- 캐시 기반 Pool 상태 조회 (Non-Blocking)
- 백그라운드 스레드 갱신
- Stale 캐시 처리 (경고, 폴백)

---

### 5.2 ConfigChangeTracker

**경로**: `selfhealing.config_tracker.ConfigChangeTracker`

**기능**:
- 설정 변경 전/후 값 자동 기록
- 변경자 자동 추적 (ActorContext 사용)
- 캐시 무효화 여부 기록

---

### 5.3 DecisionEngine

**경로**: `selfhealing.core.decision_engine.DecisionEngine`

**기능**:
- 실시간 메트릭 분석
- 파라미터 조정 제안 (Netflix Hystrix/Google Autopilot 스타일)
- 기본 규칙: timeout, retry_count, CB threshold, jitter

---

### 5.4 ConnectionPoolMonitor

**경로**: `selfhealing.core.pool_monitor.ConnectionPoolMonitor`

**기능**:
- Active/Available 커넥션 수 모니터링
- Wait Queue 길이 추적
- 커넥션 누수 감지
- Pool 고갈 예측

---

### 5.5 SelfHealingRecoveryLogger

**경로**: `selfhealing.api.django.middleware.SelfHealingRecoveryLogger`

**기능**:
- 복구 이벤트 체인 기록
- 해시 체인 감사 증적
- 복구 타임라인 생성
- 감사 보고서 생성

---

### 5.6 GovernanceChecks

**경로**: `selfhealing.services.governance_checks`

**기능**:
- Kill Switch 체크
- Emergency Mode 체크
- Error Budget 체크
- 데코레이터/믹스인/직접 호출 지원
- 차단 시 자동 Audit 로깅

---

### 5.7 AuditHelpers

**경로**: `selfhealing.services.audit_helpers`

**기능**:
- DLQ 저장/리플레이 Audit 로깅
- 하이브리드 로직: request 있으면 버퍼에 적재, 없으면 직접 기록

---

## 6. 미등록 기능 목록

### 6.1 미등록 미들웨어 요약

| 미들웨어 | 경로 | 용도 |
|---------|------|------|
| PoolCircuitBreakerMiddleware | `selfhealing/api/django/pool_circuit_breaker.py` | Pool 기반 Fail Fast |
| AuditMiddleware | `selfhealing/api/django/audit_middleware.py` | 중앙 Audit 수집 |
| SensitiveAccessLoggingMiddleware | `selfhealing/api/django/middleware.py` | 민감 엔드포인트 로깅 |
| TieringMiddleware | `selfhealing/api/django/tiering/middleware.py` | Emergency 트래픽 제어 |
| ActorContextMiddleware | `myproject/middleware/actor_middleware.py` | 사용자 컨텍스트 자동 추적 |
| ActorContextMiddlewareSimple | `myproject/middleware/actor_middleware.py` | 간소화된 사용자 컨텍스트 추적 |
| PoolTimeoutMiddleware | `myproject/middleware/pool_timeout_middleware.py` | Pool Timeout 감지 및 503 반환 |
| SelfHealingMiddleware (FastAPI) | `selfhealing/adapters/fastapi/middleware.py` | FastAPI용 Self-Healing |
| ShutdownMiddleware (FastAPI) | `selfhealing/adapters/fastapi/middleware.py` | FastAPI용 그레이스풀 셧다운 |

### 6.2 미등록 핵심 컴포넌트

| 컴포넌트 | 경로 | 용도 |
|---------|------|------|
| FailSecureIsAuthenticated | `selfhealing/api/django/middleware.py` | Fail-Secure 인증 체크 |
| FailSecureIsAdminUser | `selfhealing/api/django/middleware.py` | Fail-Secure 관리자 체크 |
| LocalMemoryRateLimiter | `selfhealing/api/django/rate_limit.py` | 비상 로컬 레이트 리미터 |
| RedisHealthChecker | `selfhealing/api/django/rate_limit.py` | Redis 헬스 체크 + Mini CB |
| RedisHealthState | `selfhealing/api/django/rate_limit.py` | Redis 헬스 상태 Enum |
| SensitiveEndpointAccessLogger | `selfhealing/api/django/middleware.py` | 접근 로거 서비스 |
| AccessLogEntry | `selfhealing/api/django/middleware.py` | 접근 로그 엔트리 데이터 클래스 |
| PoolCircuitBreaker | `selfhealing/api/django/pool_circuit_breaker.py` | Pool 상태 기반 싱글톤 CB |

### 6.2.1 추가 서비스 컴포넌트

| 컴포넌트 | 경로 | 용도 |
|---------|------|------|
| BackoffCalculator | `selfhealing/services/backoff_calculator.py` | 백오프 계산기 |
| ChaosContext | `selfhealing/services/chaos_context.py` | Chaos 실험 컨텍스트 |
| ControlAPIService | `selfhealing/services/control_api_service.py` | Control API 서비스 |
| DashboardService | `selfhealing/services/dashboard_service.py` | 대시보드 서비스 |
| DLQModels | `selfhealing/services/dlq_models.py` | DLQ 데이터 모델 |
| ExecutionServices | `selfhealing/services/execution_services.py` | 실행 서비스 |
| ForensicAdvisor | `selfhealing/services/forensic_advisor.py` | 포렌식 어드바이저 |
| ForensicContext | `selfhealing/services/forensic_context.py` | 포렌식 컨텍스트 |
| GovernanceService | `selfhealing/services/governance_service.py` | 거버넌스 서비스 |
| PendingConfig | `selfhealing/services/pending_config.py` | 대기 중인 설정 관리 |
| RateLimitCoordinator | `selfhealing/services/rate_limit_coordinator.py` | 레이트 리밋 조정자 |
| RetryHandler | `selfhealing/services/retry_handler.py` | 재시도 핸들러 |
| RuntimeConfigManager | `selfhealing/services/runtime_config/` | 런타임 설정 관리 |
| SecurityViolationService | `selfhealing/services/security_violation_service.py` | 보안 위반 서비스 |

### 6.2.2 서비스 하위 모듈

| 모듈 | 경로 | 용도 |
|---------|------|------|
| FinOps | `selfhealing/services/finops/` | 비용 최적화 서비스 |
| Learning | `selfhealing/services/learning/` | 학습 기반 최적화 |
| Metrics | `selfhealing/services/metrics/` | 메트릭 수집 및 분석 |
| Factory | `selfhealing/services/factory/` | 서비스 팩토리 |
| ErrorBudgetGate | `selfhealing/services/error_budget_gate/` | Error Budget 게이트 |
| ConfigHistory | `selfhealing/services/config_history.py` | 설정 변경 이력 서비스 |

### 6.3 Chaos 서비스 (미등록)

**경로**: `selfhealing.services.chaos/`

| 컴포넌트 | 용도 |
|---------|------|
| SafetyGuard | Chaos 실험 안전 장치 |
| BlastRadiusController | 폭발 반경 제어 |
| ExperimentScheduler | 실험 스케줄링 |
| StopConditions | 자동 중단 조건 |

### 6.4 Auto Tuning (미등록)

**경로**: `selfhealing.services.auto_tuning/`

| 컴포넌트 | 용도 |
|---------|------|
| AutoTuningService | 파라미터 자동 조정 |
| AdjustmentRecorder | 조정 이력 기록 |

### 6.5 Rollback (미등록)

**경로**: `selfhealing.services.rollback/`

| 컴포넌트 | 용도 |
|---------|------|
| RollbackService | 롤백 실행 |
| RollbackModels | 롤백 데이터 모델 |

---

### 6.6 Corruption Shield (미등록)

**경로**: `selfhealing.services.corruption_shield/`

| 컴포넌트 | 용도 |
|---------|------|
| CorruptionShield | 통합 데이터 무결성 보호 |
| L1SchemaValidator | 스키마 검증 |
| L2BusinessRulesValidator | 비즈니스 규칙 검증 |
| L3AnomalyDetector | 이상 탐지 |

---

### 6.7 Adaptive Throttle (미등록)

**경로**: `selfhealing.services.throttle/`

| 컴포넌트 | 용도 |
|---------|------|
| AdaptiveThrottle | Netflix Gradient 기반 동적 레이트 조정 |
| GradientCalculator | RTT 그라디언트 계산 |
| SlidingWindowThrottle | 슬라이딩 윈도우 쓰로틀 |

---

### 6.8 Compliance DNA (미등록)

**경로**: `selfhealing.services.compliance/`

| 컴포넌트 | 용도 |
|---------|------|
| ComplianceService | 규정 준수 관리 |
| DORA-2025 체크 | EU 디지털 운영 복원력 법규 준수 |
| PCI-DSS 체크 | 결제 카드 보안 표준 준수 |

---

### 6.9 Blast Radius DNA (미등록)

**경로**: `selfhealing.services.blast_radius/`

| 컴포넌트 | 용도 |
|---------|------|
| BlastRadiusService | 장애 영향 범위 분석/관리 |
| ImpactAssessment | 영향 평가 |
| ServiceDependency | 서비스 종속성 관리 |

---

### 6.10 Event Bus (미등록)

**경로**: `selfhealing.services.event_bus`

| 컴포넌트 | 용도 |
|---------|------|
| EventBus | 컴포넌트 간 느슨한 결합 제공 |
| EventType | 이벤트 타입 정의 |

**지원 이벤트**:
- `EmergencyLevelChanged`: 비상 모드 레벨 변경
- `ErrorBudgetCritical`: 에러 예산 임계치 도달
- `CircuitBreakerStateChanged`: CB 상태 변경
- `ConfigUpdated`: 런타임 설정 변경

---

### 6.11 Security Notification (미등록)

**경로**: `selfhealing.services.security_notification_service`

| 컴포넌트 | 용도 |
|---------|------|
| SecurityNotificationService | 보안 알림 멀티채널 전송 |

**알림 채널** (심각도별):
- CRITICAL: Slack + Email + SMS + PagerDuty
- HIGH: Slack + Email
- MEDIUM: Slack only

---

### 6.12 Pre-computed Cache (미등록)

**경로**: `selfhealing.services.precomputed_cache`

| 컴포넌트 | 용도 |
|---------|------|
| PrecomputedCacheService | L3 엔드포인트 사전 계산 캐시 |
| L1Cache | In-process TTLCache 래퍼 |
| fast_json_dumps | orjson 기반 빠른 직렬화 |
| fast_json_loads | orjson 기반 빠른 역직렬화 |

**캐시 계층**:
- L1: In-process TTLCache (2초) - 0ms 오버헤드
- L2: Redis Pre-computed JSON (15초) - 1-5ms 오버헤드
- L3: Direct Compute (fallback) - 50-200ms 오버헤드

---

### 6.12.1 Context 모듈 (미등록)

**경로**: `selfhealing.context/`

| 컴포넌트 | 용도 |
|---------|------|
| Actor | 작업 수행 주체 데이터 클래스 |
| ActorContext | Thread-safe Actor 컨텍스트 관리 |
| ActorTrackingWarning | Actor 추적 경고 예외 |
| SYSTEM_ACTOR | 시스템 작업용 Sentinel Actor |
| ANONYMOUS_ACTOR | 익명 작업용 Sentinel Actor |

**헬퍼 함수**:

| 함수 | 용도 |
|------|------|
| get_audit_actor_info | 현재 Actor 정보를 Audit 포맷으로 반환 |
| warn_if_untracked | 추적되지 않은 민감 작업에 대해 경고 발생 |
| require_actor_for_action | 추적 필수 작업에서 Actor 강제 |
| get_actor_for_celery | Celery 태스크로 전달할 Actor 정보 직렬화 |
| restore_actor_from_celery | Celery 태스크에서 Actor 정보 복원 |
| set_management_command_actor | Management Command용 Actor 설정 |

**사용법**:
```python
# Django middleware에서 자동 설정
class ActorMiddleware:
    def __call__(self, request):
        with ActorContext.set_actor(
            actor_id=request.user.email,
            actor_type="user",
            source="web"
        ):
            return self.get_response(request)

# 어디서든 현재 actor 조회
actor = ActorContext.get_current()
print(f"Current user: {actor.actor_id}")

# Celery task에서 명시적 설정
@task
def my_task(actor_id: str):
    with ActorContext.set_actor(actor_id=actor_id, actor_type="scheduler"):
        do_work()
```

---

### 6.13 Core 유틸리티 (미등록)

| 컴포넌트 | 경로 | 용도 |
|---------|------|------|
| ActionExecutor | `core/action_executor.py` | 액션 실행기 |
| AdaptiveJitter | `core/adaptive_jitter.py` | 적응형 지터 계산 |
| ApplyStrategy | `core/apply_strategy.py` | 적용 전략 관리 |
| AutoRollbackGuard | `core/auto_rollback_guard.py` | 자동 롤백 보호 |
| Backoff | `core/backoff.py` | 백오프 알고리즘 |
| BypassRegistry | `core/hooks.py` | 우회 훅 레지스트리 |
| BypassResult | `core/hooks.py` | 우회 결정 결과 |
| CertMonitor | `core/cert_monitor.py` | 인증서 만료 모니터링 |
| ConnectionHealth | `core/connection_health.py` | 연결 상태 체크 |
| Constants | `core/constants.py` | 상수 정의 |
| DecisionEngine | `core/decision_engine.py` | 실시간 메트릭 분석 및 파라미터 조정 제안 |
| DecisionLogger | `core/decision_logger.py` | 결정 로깅 |
| DegradedModeHandler | `core/degraded_mode_handler.py` | 저하 모드 핸들링 |
| ExecutionMode | `core/execution_mode.py` | 실행 모드 관리 |
| FallbackStrategy | `core/fallback_strategy.py` | 폴백 전략 관리 |
| Forensic | `core/forensic.py` | 포렌식 분석 |
| Hooks | `core/hooks.py` | 훅 시스템 |
| PoolMonitor | `core/pool_monitor.py` | 커넥션 풀 모니터링 |
| PoolWatchdog | `core/pool_watchdog.py` | 풀 워치독 |
| RequestContext | `core/request_context.py` | 요청 컨텍스트 관리 |
| RequestTracker | `core/shutdown_coordinator.py` | 요청 추적기 |
| RuntimeFeedback | `core/runtime_feedback.py` | 런타임 피드백 수집 |
| SafeDefaults | `core/safe_defaults.py` | 안전 기본값 |
| SafetyBounds | `core/safety_bounds.py` | 안전 경계 체크 |
| ShutdownCoordinator | `core/shutdown_coordinator.py` | 그레이스풀 셧다운 |
| ShutdownPhase | `core/shutdown_coordinator.py` | 셧다운 단계 Enum |
| StateBackend | `core/state_backend.py` | 상태 백엔드 |
| StateCache | `core/state_cache.py` | 상태 캐시 |
| Timezone | `core/timezone.py` | 타임존 유틸리티 |
| TimeProvider | `core/time_provider.py` | 시간 제공자 |
| TLSHandler | `core/tls_handler.py` | TLS 핸들링 |
| Types | `core/types.py` | 타입 정의 |

---

### 6.14 Audit 시스템 (미등록)

| 컴포넌트 | 경로 | 용도 |
|---------|------|------|
| AuditAPI | `audit/api.py` | Audit API 인터페이스 |
| AuditEventType | `audit/event_buffer.py` | Audit 이벤트 타입 Enum |
| AuditIntegration | `audit/audit_integration.py` | 감사 통합 |
| AuditWatchdog | `audit/audit_watchdog.py` | 감사 무결성 감시 |
| Checksum | `audit/checksum.py` | 체크섬 계산 |
| ContinuousAuditRecorder | `audit/continuous_audit.py` | 해시 체인 기반 연속 감사 |
| ContinuousAuditAPI | `audit/continuous_audit_api.py` | 연속 감사 API |
| EnvSnapshot | `audit/env_snapshot.py` | 환경 스냅샷 |
| RequestAuditBuffer | `audit/event_buffer.py` | 요청별 이벤트 버퍼 |
| AuditExport | `audit/export.py` | 감사 로그 내보내기 |
| Integrity | `audit/integrity.py` | 무결성 검증 |
| AuditLogger | `audit/logger.py` | 감사 로거 |
| Masking | `audit/masking.py` | 민감 정보 마스킹 |
| Resilience | `audit/resilience.py` | 복원력 로직 |
| ResilientRecorder | `audit/resilient_recorder.py` | 복원력 있는 기록기 |
| RingBuffer | `audit/ring_buffer.py` | 순환 버퍼 |
| SelfAudit | `audit/self_audit.py` | 자체 감사 |
| SignedManifest | `audit/signed_manifest.py` | 서명된 매니페스트 |
| Trace | `audit/trace.py` | 추적 |
| TraceContext | `audit/trace.py` | Trace ID 스코핑 컨텍스트 관리자 |
| VerifyAuditIntegrity | `audit/verify_audit_integrity.py` | 감사 무결성 검증 |
| WALWriter | `audit/wal.py` | Write-Ahead Log |
| AuditConfig | `audit/config.py` | Audit 설정 |
| AuditBackends | `audit/backends/` | Audit 백엔드 (DB, File, CloudWatch, Datadog, S3 WORM 등) |
| HashChainManager | `audit/integrity.py` | 해시 체인 관리자 |
| DegradedModeManager | `audit/resilience.py` | Audit 저하 모드 관리자 |

---

## 7. FastAPI 미들웨어

> FastAPI/Starlette 기반 애플리케이션을 위한 ASGI 미들웨어

### 7.1 SelfHealingMiddleware (FastAPI)

**경로**: `selfhealing.adapters.fastapi.middleware.SelfHealingMiddleware`

**목적**: FastAPI 애플리케이션용 Self-Healing 기능 제공

**기능**:
- Request ID 생성 및 전파
- 그레이스풀 셧다운 시 요청 추적
- 응답 시간 로깅 (`X-Response-Time` 헤더)
- Circuit Breaker 헤더 전파
- 셧다운 중 503 응답 반환

**사용법**:
```python
from fastapi import FastAPI
from selfhealing.adapters.fastapi import SelfHealingMiddleware

app = FastAPI()
app.add_middleware(
    SelfHealingMiddleware,
    request_tracker=tracker,
    exclude_paths=["/health", "/metrics"],
)
```

---

### 7.2 ShutdownMiddleware (FastAPI)

**경로**: `selfhealing.adapters.fastapi.middleware.ShutdownMiddleware`

**목적**: FastAPI용 그레이스풀 셧다운 전용 미들웨어

**기능**:
- `SelfHealingMiddleware`보다 가벼운 셧다운 전용 버전
- 셧다운 중 `/health`, `/healthz`, `/ready` 경로는 degraded 상태로 응답
- 셧다운 단계(Phase) 정보 포함 503 응답
- 재시도 헤더 (`Retry-After: 30`) 자동 추가

**사용법**:
```python
from fastapi import FastAPI
from selfhealing.adapters.fastapi import ShutdownMiddleware

app = FastAPI()
app.add_middleware(ShutdownMiddleware, shutdown_coordinator=coordinator)
```

---

## 8. API Tiering (TieringMiddleware)

> 비상 모드 기반 API 우선순위 제어 미들웨어

**경로**: `selfhealing.api.django.tiering.TieringMiddleware`

### 8.1 Tier Hierarchy

| Tier | 이름 | 비상 모드 허용율 | 용도 |
|------|------|-----------------|------|
| Tier 1 | Critical | 50% | Self-Healing, Payment API |
| Tier 2 | Standard | 10% | Config, DLQ Replay |
| Tier 3 | Non-Essential | 0% | Dashboard, Metrics |

### 8.2 Emergency Level Rules

| Level | Critical | Standard | Non-Essential |
|-------|----------|----------|---------------|
| NORMAL (0) | 100% | 100% | 100% |
| LEVEL_1 (1) | 100% | 100% | 0% |
| LEVEL_2 (2) | 100% | 10% | 0% |
| LEVEL_3 (3) | 50% | 0% | 0% |

### 8.3 Core Components

**경로**: `selfhealing.api.django.tiering/`

| 컴포넌트 | 파일 | 용도 |
|---------|------|------|
| `TieringMiddleware` | middleware.py | Django 미들웨어 |
| `TierRegistry` | registry.py | Tier 설정 레지스트리 (싱글톤) |
| `TierDefinition` | models.py | Tier 정의 |
| `TierMapping` | models.py | 경로-Tier 매핑 |
| `TierOverride` | models.py | Tier 오버라이드 |
| `TieringCircuitBreaker` | circuit_breaker.py | Tiering용 CB |
| `TierConfigValidator` | validator.py | 설정 검증기 |

### 8.4 사용법

```python
# settings.py
MIDDLEWARE = [
    ...
    'selfhealing.api.django.tiering.TieringMiddleware',
    ...
]

# 비활성화 옵션
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = True  # False로 설정 시 비활성화
```

---

## 참고 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [02_ARCHITECTURE.md](02_ARCHITECTURE.md) - 아키텍처 설계
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - 서킷브레이커 상세
- [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) - DLQ 상세
- [07_CONTROL_API.md](07_CONTROL_API.md) - API 엔드포인트 및 Serializers
- [56_AUDIT_MIDDLEWARE_DESIGN.md](56_AUDIT_MIDDLEWARE_DESIGN.md) - Audit 미들웨어 설계

---

## 9. Adapters (어댑터 계층)

> 외부 시스템과의 통합을 위한 어댑터 컴포넌트

### 9.1 Alert Adapters (미등록)

**경로**: `selfhealing.adapters.alert/`

| 컴포넌트 | 용도 |
|---------|------|
| FileAlertAdapter | 파일 기반 알림 어댑터 |
| NullAlertAdapter | No-op 알림 어댑터 (테스트용) |
| StdoutAlertAdapter | 표준 출력 알림 어댑터 |

---

### 9.2 Cache Adapters (미등록)

**경로**: `selfhealing.adapters.cache/`

| 컴포넌트 | 용도 |
|---------|------|
| RedisCacheAdapter | Redis 캐시 어댑터 (기본 L2 캐시) |
| RedisDistributedLock | Redis 분산 잠금 구현 |
| MemcachedCacheAdapter | Memcached 캐시 어댑터 |
| MemcachedDistributedLock | Memcached 분산 잠금 구현 |
| InMemoryCacheAdapter | 인메모리 캐시 어댑터 (테스트/로컬용) |
| InMemoryLock | 인메모리 잠금 구현 |
| CacheEntry | 캐시 항목 데이터 클래스 |

---

### 9.3 Rate Limit Adapters (미등록)

**경로**: `selfhealing.adapters.rate_limit/`

| 컴포넌트 | 용도 |
|---------|------|
| RedisRateLimitStorage | Redis 레이트 리밋 저장소 (슬라이딩 윈도우) |
| InMemoryRateLimitStorage | 인메모리 레이트 리밋 저장소 (비상용) |
| DatabaseRateLimitStorage | 데이터베이스 레이트 리밋 저장소 |

---

### 9.4 Queue Adapters (미등록)

**경로**: `selfhealing.adapters.queues/`

| 컴포넌트 | 용도 |
|---------|------|
| CeleryTaskAdapter | Celery 태스크 큐 어댑터 |
| RQTaskAdapter | RQ (Redis Queue) 태스크 어댑터 |
| RQAsyncResult | RQ 비동기 결과 래퍼 |
| SyncTaskAdapter | 동기 태스크 어댑터 (테스트용) |
| TaskRecord | 태스크 레코드 데이터 클래스 |
| RegisteredTask | 등록된 태스크 메타데이터 |

---

### 9.5 Metrics Adapters (미등록)

**경로**: `selfhealing.adapters.metrics/`

| 컴포넌트 | 용도 |
|---------|------|
| AutoTuningMetricsAdapter | Auto-Tuning 메트릭 프로토콜 |
| InternalMetricsAdapter | 내부 메트릭 어댑터 |
| PrometheusMetricsAdapter | Prometheus 메트릭 어댑터 |
| MockMetricsAdapter | 목 메트릭 어댑터 (테스트용) |
| DjangoMetricSourceAdapter | Django 메트릭 소스 어댑터 |
| RedisMetricSourceAdapter | Redis 메트릭 소스 어댑터 |
| MetricSourceAdapter | 메트릭 소스 프로토콜 |
| NullMetricSourceAdapter | No-op 메트릭 소스 어댑터 |

---

### 9.6 Observability/OpenTelemetry Adapters (미등록)

**경로**: `selfhealing.adapters.observability/`

| 컴포넌트 | 용도 |
|---------|------|
| OpenTelemetryAdapter | OpenTelemetry 통합 어댑터 |
| SelfHealingEventType | Self-Healing 이벤트 타입 Enum |
| EventAttribute | 이벤트 속성 헬퍼 |
| DecisionType | 결정 타입 Enum |
| DecisionOutcome | 결정 결과 Enum |
| DecisionSpanContext | 결정 스팬 컨텍스트 매니저 |
| OpenTelemetryConfig | OpenTelemetry 설정 |
| NoOpSpan | No-op 스팬 (OTel 미설치 시) |
| NoOpTracer | No-op 트레이서 |
| NoOpOpenTelemetryAdapter | No-op OTel 어댑터 |

---

### 9.7 Memory Adapters (미등록)

**경로**: `selfhealing.adapters.memory/`

| 컴포넌트 | 용도 |
|---------|------|
| InMemoryCircuitBreakerStateRepository | 인메모리 CB 상태 저장소 |
| InMemoryFailedOperationRepository | 인메모리 실패 작업 저장소 |
| InMemorySecurityIncidentRepository | 인메모리 보안 인시던트 저장소 |
| LayeredCircuitBreakerStateRepository | 레이어드 CB 상태 저장소 (L1+L2) |
| ShadowLogger | 섀도우 로거 (L2 동기화 실패 기록) |
| DriftReconciler | L1-L2 드리프트 조정기 |

---

### 9.8 AirGap Adapters (미등록)

**경로**: `selfhealing.adapters.airgap/`

| 컴포넌트 | 용도 |
|---------|------|
| AirGapStorageAdapter | AirGap 저장소 프로토콜 |
| BaseAirGapAdapter | AirGap 기본 어댑터 |
| AirGapKeys | AirGap 키 상수 |
| RedisAirGapAdapter | Redis AirGap 어댑터 |
| NullAirGapAdapter | No-op AirGap 어댑터 |

---

### 9.9 Health Checker Adapters (미등록)

**경로**: `selfhealing.adapters.health_checker.py`

| 컴포넌트 | 용도 |
|---------|------|
| HealthCheckStrategy | 헬스 체크 전략 ABC |
| TTLCacheStrategy | TTL 캐시 기반 헬스 체크 |
| LinuxTCPInfoStrategy | Linux TCP 정보 기반 헬스 체크 |
| SimpleSocketStrategy | 소켓 기반 헬스 체크 |
| PortableHealthChecker | 크로스 플랫폼 헬스 체커 |

---

### 9.10 Audit Adapters (미등록)

**경로**: `selfhealing.adapters.audit/`

| 컴포넌트 | 용도 |
|---------|------|
| FileAuditLogAdapter | 파일 기반 감사 로그 어댑터 |
| NullAuditLogAdapter | No-op 감사 로그 어댑터 |
| StdoutAuditLogAdapter | 표준 출력 감사 로그 어댑터 |
| WORMAdapter | Write-Once-Read-Many 기본 어댑터 |
| S3ObjectLockAdapter | S3 Object Lock WORM 어댑터 |
| LokiAdapter | Grafana Loki WORM 어댑터 |
| HTTPWebhookAdapter | HTTP Webhook WORM 어댑터 |
| SidecarFileWatcher | 사이드카 파일 감시자 |

---

### 9.11 Django Repositories (미등록)

**경로**: `selfhealing.adapters.django_repositories.py`

| 컴포넌트 | 용도 |
|---------|------|
| DjangoFailedOperationRepository | Django 실패 작업 저장소 |
| DjangoCircuitBreakerStateRepository | Django CB 상태 저장소 |
| DjangoSecurityIncidentRepository | Django 보안 인시던트 저장소 |
| DjangoConfigProvider | Django 설정 제공자 |

---

## 10. 추가 Services (미등록)

### 10.1 Metrics 모듈 (미등록)

**경로**: `selfhealing.services.metrics/`

| 컴포넌트 | 용도 |
|---------|------|
| register_metric | 안전한 메트릭 등록 헬퍼 |
| DOMAIN_HISTOGRAM_REGISTRY | 도메인 히스토그램 레지스트리 |
| DOMAIN_COUNTER_REGISTRY | 도메인 카운터 레지스트리 |
| record_cb_state_change | CB 상태 변경 기록 함수 |
| record_dlq_operation | DLQ 작업 기록 함수 |
| record_replay_attempt | 리플레이 시도 기록 함수 |
| ALERTING_RULES | Prometheus 알림 규칙 정의 |

---

### 10.2 FinOps (미등록)

**경로**: `selfhealing.services.finops/`

| 컴포넌트 | 용도 |
|---------|------|
| FinOpsService | 비용 최적화 서비스 |
| CostTier | 비용 계층 Enum |
| CostBudget | 비용 예산 데이터 클래스 |
| CostRecord | 비용 기록 데이터 클래스 |
| CostReport | 비용 리포트 생성기 |
| CostAlert | 비용 알림 트리거 |

---

### 10.3 Learning (미등록)

**경로**: `selfhealing.services.learning/`

| 컴포넌트 | 용도 |
|---------|------|
| LearningService | 학습 기반 최적화 서비스 |
| PatternType | 패턴 타입 Enum (spike, gradual, periodic 등) |
| SuggestionPriority | 제안 우선순위 Enum |
| LearningPattern | 학습된 패턴 데이터 클래스 |
| LearningSession | 학습 세션 관리 |
| Suggestion | 파라미터 조정 제안 |
| PerformanceMetric | 성능 메트릭 클래스 |

---

### 10.4 Error Budget Gate (미등록)

**경로**: `selfhealing.services.error_budget_gate/`

| 컴포넌트 | 용도 |
|---------|------|
| ErrorBudgetGate | Error Budget 게이트 (자동화 차단) |
| ErrorBudgetGateConfig | 게이트 설정 |
| GateStatus | 게이트 상태 Enum |
| GateCheckResult | 게이트 체크 결과 |
| GateFaultDetector | 게이트 장애 감지기 |
| GateAlertManager | 게이트 알림 관리자 |
| AutomationBlockedError | 자동화 차단 예외 |

---

### 10.5 Error Budget Reconciliation (미등록)

**경로**: `selfhealing.services.error_budget/reconciliation/`

| 컴포넌트 | 용도 |
|---------|------|
| ErrorBudgetReconciliationService | Error Budget 조정 서비스 |
| ShadowBudgetCalculator | Shadow Budget 계산기 |
| FailSafePeriodTracker | Fail-Safe 기간 추적기 |
| ReconciliationStatus | 조정 상태 Enum |
| ApplyMode | 적용 모드 Enum |

---

### 10.6 Runtime Config (미등록)

**경로**: `selfhealing.services.runtime_config/`

| 컴포넌트 | 용도 |
|---------|------|
| RuntimeConfigManager | 런타임 설정 관리자 (Mixin 통합) |
| BaseConfigManager | 기본 설정 관리자 |
| CoreConfigMixin | 핵심 설정 Mixin |
| AdvancedConfigMixin | 고급 설정 Mixin |
| StrategyMixin | 전략 Mixin |
| ApprovalMixin | 승인 Mixin |
| ChaosStorageMixin | Chaos 저장소 Mixin |

---

### 10.7 Service Factory (미등록)

**경로**: `selfhealing.services.factory/`

| 컴포넌트 | 용도 |
|---------|------|
| ServiceFactory | 서비스 팩토리 (DI 컨테이너) |
| StorageMode | 저장소 모드 Enum (memory, django, sqlalchemy) |
| FrameworkType | 프레임워크 타입 Enum |
| PROVIDER_REGISTRY | 프로바이더 레지스트리 (싱글톤) |

---

## 11. Audit Backends (미등록)

**경로**: `selfhealing.audit.backends/`

### 11.1 Backend ABC

| 컴포넌트 | 용도 |
|---------|------|
| AuditBackend | Audit 백엔드 ABC |
| AsyncAuditBackend | 비동기 Audit 백엔드 |
| BufferedBackend | 버퍼링 백엔드 |
| CompositeBackend | 복합 백엔드 (멀티 백엔드 지원) |
| BackendStatus | 백엔드 상태 Enum |
| BackendHealth | 백엔드 헬스 데이터 클래스 |

---

### 11.2 Backend 구현체

| 컴포넌트 | 용도 |
|---------|------|
| LocalFileBackend | 로컬 파일 백엔드 |
| S3WORMBackend | S3 WORM 백엔드 (Object Lock 지원) |
| CloudWatchBackend | AWS CloudWatch 백엔드 |
| DatadogBackend | Datadog 백엔드 |
| RemoteAuditBackend | 원격 감사 백엔드 |

---

## 12. Resilience 모듈 (미등록)

**경로**: `selfhealing.resilience/`

| 컴포넌트 | 용도 |
|---------|------|
| CircuitBreaker | Audit용 서킷브레이커 |
| CircuitBreakerRegistry | CB 레지스트리 |
| CircuitState | CB 상태 Enum |
| CircuitBreakerConfig | CB 설정 |
| AuditMetrics | Audit 메트릭 |
| SyslogFallback | Syslog 폴백 (Audit 장애 시) |
| DegradedModeManager | Audit 저하 모드 관리자 |

### 12.1 Bypass Hooks

**경로**: `selfhealing.resilience.bypass_hooks`

| 컴포넌트 | 용도 |
|---------|------|
| register_resilience_hooks | 복원력 테스트 훅 등록 |
| platinum_bypass_hook | PLATINUM 모드 레이트 리미터 완전 바이패스 |
| chaos_monkey_bypass_hook | Chaos Monkey 모드 바이패스 |
| hellmode_bypass_hook | HELLMODE 스트레스 테스트 바이패스 |
| integration_test_bypass_hook | 통합 테스트 바이패스 |

**Hook Priority Levels**:
- 1000+: Emergency/Admin overrides
- 500-999: Stress testing modes (PLATINUM, HELLMODE)
- 100-499: Standard testing modes (chaos-monkey, integration)
- 1-99: Low priority/fallback hooks

**조건**: 프로덕션 환경에서는 절대 활성화 안됨 (ENVIRONMENT != "production")

---

## 13. 추가 Core 컴포넌트 (미등록)

### 13.1 TLS Handler (미등록)

**경로**: `selfhealing.core.tls_handler.py`

| 컴포넌트 | 용도 |
|---------|------|
| TLSErrorClassifier | TLS 에러 분류기 |
| TLSResilientClient | TLS 복원력 있는 클라이언트 |
| SimpleTLSResilientClient | 간소화된 TLS 클라이언트 |
| TLSErrorType | TLS 에러 타입 Enum |
| TLSErrorSeverity | TLS 에러 심각도 Enum |
| TLSErrorInfo | TLS 에러 정보 데이터 클래스 |

---

### 13.2 Certificate Monitor (미등록)

**경로**: `selfhealing.core.cert_monitor.py`

| 컴포넌트 | 용도 |
|---------|------|
| CertificateExpiryMonitor | 인증서 만료 모니터 |
| CertificateAlertManager | 인증서 알림 관리자 |
| CertificateStatus | 인증서 상태 Enum |
| CertificateInfo | 인증서 정보 데이터 클래스 |

---

### 13.3 Connection Health (미등록)

**경로**: `selfhealing.core.connection_health.py`

| 컴포넌트 | 용도 |
|---------|------|
| ConnectionHealthMonitor | 연결 상태 모니터 |
| DefaultConnectionHealthMonitor | 기본 연결 상태 모니터 |
| ConnectionType | 연결 타입 Enum (db, redis, external 등) |
| ConnectionStatus | 연결 상태 Enum |
| ConnectionHealth | 연결 헬스 데이터 클래스 |
| PartitionState | 네트워크 파티션 상태 |

---

### 13.4 Pool Watchdog (미등록)

**경로**: `selfhealing.core.pool_watchdog.py`

| 컴포넌트 | 용도 |
|---------|------|
| PoolWatchdog | 커넥션 풀 워치독 |
| PoolRecoveryHandler | 풀 복구 핸들러 |
| RecoveryAction | 복구 액션 Enum |
| RecoveryResult | 복구 결과 데이터 클래스 |

---

### 13.5 Runtime Feedback Loop (미등록)

**경로**: `selfhealing.core.runtime_feedback.py`

| 컴포넌트 | 용도 |
|---------|------|
| RuntimeFeedbackLoop | 런타임 피드백 루프 |
| FeedbackLoopState | 피드백 루프 상태 |
| AdjustmentResult | 조정 결과 데이터 클래스 |

---

### 13.6 Graceful Shutdown (미등록)

**경로**: `selfhealing.core.shutdown_coordinator.py`

| 컴포넌트 | 용도 |
|---------|------|
| GracefulShutdownCoordinator | 그레이스풀 셧다운 코디네이터 |
| ShutdownHandler | 셧다운 핸들러 ABC |
| RequestTracker | 진행 중 요청 추적기 |
| ShutdownPhase | 셧다운 단계 Enum (RUNNING, DRAINING, TERMINATED) |
| RequestState | 요청 상태 Enum |
| TrackedRequest | 추적 중인 요청 데이터 클래스 |
| ShutdownStats | 셧다운 통계 |

---

### 13.7 State Backend (미등록)

**경로**: `selfhealing.core.state_backend.py`

| 컴포넌트 | 용도 |
|---------|------|
| StateBackend | 상태 백엔드 ABC |
| FileStateBackend | 파일 상태 백엔드 |
| RedisStateBackend | Redis 상태 백엔드 |
| MemoryStateBackend | 메모리 상태 백엔드 |

---

### 13.8 Time Provider (미등록)

**경로**: `selfhealing.core.time_provider.py`

| 컴포넌트 | 용도 |
|---------|------|
| TimeProvider | 시간 제공자 프로토콜 |
| SystemTimeProvider | 시스템 시간 제공자 |
| MockTimeProvider | 목 시간 제공자 (테스트용) |
| FrozenTime | 고정 시간 컨텍스트 매니저 |

---

## 14. 추가 Audit 컴포넌트 (미등록)

### 14.1 Write-Ahead Log (미등록)

**경로**: `selfhealing.audit.wal.py`

| 컴포넌트 | 용도 |
|---------|------|
| WriteAheadLog | WAL 구현 (감사 로그 내구성) |
| WALEntry | WAL 엔트리 데이터 클래스 |
| WALConfig | WAL 설정 |
| WALStats | WAL 통계 |
| WALState | WAL 상태 Enum |
| WALError | WAL 에러 클래스 |
| WALCorruptionError | WAL 손상 에러 |

---

### 14.2 Ring Buffer (미등록)

**경로**: `selfhealing.audit.ring_buffer.py`

| 컴포넌트 | 용도 |
|---------|------|
| RingBuffer | 순환 버퍼 (메모리 제한 버퍼) |
| RingBufferStats | 버퍼 통계 |
| BackpressureStrategy | 백프레셔 전략 Enum |

---

### 14.3 Signed Manifest (미등록)

**경로**: `selfhealing.audit.signed_manifest.py`

| 컴포넌트 | 용도 |
|---------|------|
| SignedManifest | 서명된 매니페스트 (무결성 증명) |
| MerkleTree | Merkle 트리 구현 |
| RFC3161Client | RFC 3161 타임스탬프 클라이언트 |
| RFC3161Timestamp | RFC 3161 타임스탬프 데이터 클래스 |
| ManifestEntry | 매니페스트 엔트리 |
| SignedManifestData | 서명된 매니페스트 데이터 |

---

### 14.4 Audit Export (미등록)

**경로**: `selfhealing.audit.export.py`

| 컴포넌트 | 용도 |
|---------|------|
| AuditExporter | 감사 로그 내보내기 |
| ExportFormat | 내보내기 형식 Enum (json, csv, parquet) |
| ExportTarget | 내보내기 대상 Enum (file, s3, gcs) |
| ExportOptions | 내보내기 옵션 |

---

### 14.5 Self Audit (미등록)

**경로**: `selfhealing.audit.self_audit.py`

| 컴포넌트 | 용도 |
|---------|------|
| SelfAuditLogger | 자체 감사 로거 (Self-Healing 자체 동작 기록) |
| SelfAuditEvent | 자체 감사 이벤트 |
| SelfAuditStats | 자체 감사 통계 |

---

### 14.6 Audit Watchdog (미등록)

**경로**: `selfhealing.audit.audit_watchdog.py`

| 컴포넌트 | 용도 |
|---------|------|
| AuditWatchdog | 감사 워치독 (무결성 감시) |
| WatchdogChecker | 워치독 체커 |
| WatchdogState | 워치독 상태 Enum |
| HeartbeatTarget | 하트비트 대상 |
| WatchdogConfig | 워치독 설정 |
| WatchdogStats | 워치독 통계 |

---

### 14.7 Audit Integration (미등록)

**경로**: `selfhealing.audit.audit_integration.py`

| 컴포넌트 | 용도 |
|---------|------|
| AsyncLoggerAdapter | 비동기 로거 어댑터 |
| IntegratedAuditRecorder | 통합 감사 기록기 |
| EventSeverity | 이벤트 심각도 Enum |
| AsyncLoggerConfig | 비동기 로거 설정 |
| AuditEventObserver | 감사 이벤트 옵저버 |

---

## 15. Celery Adapters (미등록)

**경로**: `selfhealing.adapters.celery/`

| 컴포넌트 | 용도 |
|---------|------|
| SignalHooksConfig | Celery 시그널 훅 설정 |
| tasks 모듈 | Celery 태스크 정의 (DLQ 리플레이, 정리 등) |

### 15.1 Celery Tasks

**경로**: `selfhealing.adapters.celery.tasks`

| 태스크 | 용도 |
|--------|------|
| conditional_replay_on_circuit_close | CB 복구 시 조건부 리플레이 |
| check_circuit_breaker_recovery | CB 복구 상태 체크 |
| force_open_circuit_breaker | CB 강제 오픈 |
| force_close_circuit_breaker | CB 강제 클로즈 |
| expire_manual_overrides | 수동 오버라이드 만료 처리 |
| replay_single_dlq_entry | 단일 DLQ 항목 리플레이 |
| replay_batch_by_domain | 도메인별 배치 리플레이 |
| cleanup_resolved_dlq_entries | 해결된 DLQ 항목 정리 |
| collect_self_healing_metrics | Self-Healing 메트릭 수집 |
| check_and_report_sla_breaches | SLA 위반 체크 및 리포트 |

### 15.2 Signal Hooks

**경로**: `selfhealing.adapters.celery.signal_hooks`

| 컴포넌트 | 용도 |
|---------|------|
| setup_selfhealing_signals | Self-Healing 시그널 자동 설정 |
| disconnect_selfhealing_signals | 시그널 연결 해제 |
| is_signals_connected | 시그널 연결 상태 확인 |
| reload_signal_hooks_config | 시그널 훅 설정 재로드 |
| get_signal_hooks_config | 시그널 훅 설정 조회 |
| selfhealing_task | Self-Healing 태스크 데코레이터 |
| SignalHooksConfig | 시그널 훅 설정 클래스 |

**자동 통합 기능**:
- 태스크 실패 시 Circuit Breaker에 자동 기록
- 실패한 태스크 DLQ에 자동 저장
- Forensic Context 자동 캡처
- 메트릭 자동 업데이트

**환경 변수 설정**:
```bash
SELFHEALING_ENABLED=true                    # 전체 활성화
SELFHEALING_CB_ENABLED=true                 # Circuit Breaker 기록
SELFHEALING_DLQ_ENABLED=true                # DLQ 저장
SELFHEALING_METRICS_ENABLED=true            # 메트릭 기록
SELFHEALING_FORENSICS_ENABLED=true          # Forensic Context 캡처
SELFHEALING_CB_FAILURE_THRESHOLD=5          # CB 오픈 임계치
SELFHEALING_CB_RECOVERY_TIMEOUT=60          # Half-Open 전환 시간
SELFHEALING_CB_SUCCESS_THRESHOLD=2          # CB 클로즈 성공 임계치
SELFHEALING_TASK_DOMAIN_MAPPING='{"task_name": "domain"}'  # 태스크-도메인 매핑
```

---

## 16. Interfaces 모듈 (미등록)

> 플러그인 아키텍처를 위한 추상 인터페이스 정의

**경로**: `selfhealing.interfaces/`

### 16.1 Repository Interfaces

**경로**: `selfhealing.interfaces.repositories`

| 컴포넌트 | 용도 |
|---------|------|
| FailedOperationDomain | DLQ 도메인 Enum (external_service, async_task 등) |
| FailedOperationStatus | DLQ 상태 Enum (pending, reviewing, resolved 등) |
| CircuitBreakerStateEnum | CB 상태 Enum (closed, open, half_open) |
| SecurityIncidentType | 보안 인시던트 타입 Enum |
| SecuritySeverity | 보안 심각도 Enum (critical, high, medium) |
| SecurityIncidentStatus | 보안 인시던트 상태 Enum |
| FailedOperationData | DLQ 데이터 클래스 (도메인 중립적) |
| CircuitBreakerStateData | CB 상태 데이터 클래스 |
| SecurityIncidentData | 보안 인시던트 데이터 클래스 |
| FailedOperationRepository | DLQ 저장소 ABC |
| CircuitBreakerStateRepository | CB 상태 저장소 ABC |
| SecurityIncidentRepository | 보안 인시던트 저장소 ABC |

### 16.2 Cache Provider Interface

**경로**: `selfhealing.interfaces.cache_provider`

| 컴포넌트 | 용도 |
|---------|------|
| DistributedLock | 분산 잠금 프로토콜 |
| LockAcquisitionError | 잠금 획득 실패 예외 |
| LockNotOwnedError | 잠금 미소유 예외 |
| CacheProviderInterface | 캐시 프로바이더 ABC |

### 16.3 Task Queue Interface

**경로**: `selfhealing.interfaces.task_queue`

| 컴포넌트 | 용도 |
|---------|------|
| TaskStatus | 태스크 상태 Enum |
| TaskPriority | 태스크 우선순위 Enum |
| TaskResult | 태스크 결과 DTO |
| TaskOptions | 태스크 옵션 DTO |
| ScheduleInfo | 스케줄 정보 DTO |
| TaskQueueError | 태스크 큐 에러 |
| TaskNotFoundError | 태스크 미발견 에러 |
| TaskTimeoutError | 태스크 타임아웃 에러 |
| TaskRevokedError | 태스크 취소 에러 |
| TaskQueueInterface | 태스크 큐 ABC |

### 16.4 Web Framework Interface

**경로**: `selfhealing.interfaces.web_framework`

| 컴포넌트 | 용도 |
|---------|------|
| HttpMethod | HTTP 메서드 Enum |
| ContentType | 콘텐츠 타입 Enum |
| RequestContext | 요청 컨텍스트 DTO |
| ResponseContext | 응답 컨텍스트 DTO |
| WebFrameworkError | 웹 프레임워크 에러 |
| RouteNotFoundError | 라우트 미발견 에러 |
| AuthenticationError | 인증 에러 |
| PermissionDeniedError | 권한 거부 에러 |
| WebFrameworkInterface | 웹 프레임워크 ABC |
| HandlerFunc | 핸들러 함수 타입 별칭 |

### 16.5 Config Provider Interface

**경로**: `selfhealing.interfaces.config_provider`

| 컴포넌트 | 용도 |
|---------|------|
| ConfigProviderInterface | 설정 프로바이더 ABC |
| DictConfigProvider | 딕셔너리 기반 설정 프로바이더 |
| EnvConfigProvider | 환경 변수 기반 설정 프로바이더 |

### 16.6 Rate Limit Storage Interface

**경로**: `selfhealing.interfaces.rate_limit_storage`

| 컴포넌트 | 용도 |
|---------|------|
| RateLimitStorageType | 저장소 타입 Enum |
| RateLimitState | 레이트 리밋 상태 DTO |
| RateLimitStorageInterface | 레이트 리밋 저장소 ABC |
| RateLimitStorageError | 저장소 에러 |
| RateLimitStorageUnavailableError | 저장소 사용 불가 에러 |

### 16.7 Audit Log Adapter Interface

**경로**: `selfhealing.interfaces.audit_adapter`

| 컴포넌트 | 용도 |
|---------|------|
| AuditAction | 감사 액션 Enum (create, update, delete, read, execute 등) |
| ContextType | 컨텍스트 타입 Enum |
| AuditEntry | 감사 엔트리 데이터 클래스 |
| AuditLogAdapter | 감사 로그 어댑터 ABC |

### 16.8 Alert Adapter Interface

**경로**: `selfhealing.interfaces.alert_adapter`

| 컴포넌트 | 용도 |
|---------|------|
| AlertSeverity | 알림 심각도 Enum |
| AlertCategory | 알림 카테고리 Enum |
| Alert | 알림 데이터 클래스 |
| AlertAdapter | 알림 어댑터 ABC |

---

## 17. Metrics 모듈 (미등록)

> Prometheus 메트릭, 이벤트 핸들러, 신뢰도 관리

**경로**: `selfhealing.metrics/`

### 17.1 Prometheus Metrics

**경로**: `selfhealing.metrics.prometheus`

| 컴포넌트 | 용도 |
|---------|------|
| SelfHealingMetrics | Self-Healing 메트릭 클래스 (싱글톤) |
| get_metrics | 메트릭 인스턴스 조회 함수 |

**제공 메트릭**:
- `dlq_items_total`: DLQ 항목 총 개수 (Counter)
- `dlq_pending_count`: 대기 중인 DLQ 항목 수 (Gauge)
- `circuit_breaker_state`: CB 상태 (Gauge)
- `recovery_time_seconds`: 복구 시간 (Histogram)
- `replay_attempts_total`: 리플레이 시도 횟수 (Counter)

### 17.2 Event Handlers

**경로**: `selfhealing.metrics.event_handlers`

| 컴포넌트 | 용도 |
|---------|------|
| DLQMetricEventHandler | DLQ 메트릭 이벤트 핸들러 |
| CircuitBreakerEventHandler | CB 메트릭 이벤트 핸들러 |
| ReplayEventHandler | 리플레이 메트릭 이벤트 핸들러 |
| reset_event_handler_cache | 이벤트 핸들러 캐시 초기화 |

### 17.3 Safe Gauge

**경로**: `selfhealing.metrics.safe_gauge`

| 컴포넌트 | 용도 |
|---------|------|
| SafeGauge | 스레드 안전 Gauge 래퍼 |
| SafeGaugeChild | SafeGauge 자식 래퍼 |

### 17.4 Decorators

**경로**: `selfhealing.metrics.decorators`

| 컴포넌트 | 용도 |
|---------|------|
| track_dlq_creation | DLQ 생성 추적 데코레이터 |
| track_dlq_resolution | DLQ 해결 추적 데코레이터 |
| track_replay | 리플레이 추적 데코레이터 |
| track_execution_time | 실행 시간 추적 데코레이터 |
| track_counter | 카운터 추적 데코레이터 |

### 17.5 Jitter

**경로**: `selfhealing.metrics.jitter`

| 컴포넌트 | 용도 |
|---------|------|
| with_jitter | 지터 적용 컨텍스트 매니저 |
| calculate_jitter | 지터 계산 함수 |
| sleep_with_jitter | 지터가 적용된 슬립 함수 |
| JitterConfig | 지터 설정 클래스 |

### 17.6 Reconciler

**경로**: `selfhealing.metrics.reconciler`

| 컴포넌트 | 용도 |
|---------|------|
| MetricReconciler | 메트릭 드리프트 조정기 |
| DriftSeverity | 드리프트 심각도 Enum |
| DriftResult | 드리프트 결과 DTO |
| SyncResult | 동기화 결과 DTO |
| get_reconciler | 조정기 인스턴스 조회 함수 |

### 17.7 Reliability

**경로**: `selfhealing.metrics.reliability`

| 컴포넌트 | 용도 |
|---------|------|
| MetricReliability | 메트릭 신뢰도 Enum (EXACT, EVENTUAL, APPROXIMATE) |
| METRIC_RELIABILITY_MAP | 메트릭별 신뢰도 매핑 |
| get_metric_reliability | 메트릭 신뢰도 조회 함수 |
| get_reliability_description | 신뢰도 설명 조회 함수 |

### 17.8 Reliability Manager

**경로**: `selfhealing.metrics.reliability_manager`

| 컴포넌트 | 용도 |
|---------|------|
| ReliabilityLevel | 신뢰도 레벨 Enum (HIGH, MEDIUM, LOW, UNKNOWN, RECOVERING) |
| OperatingMode | 운영 모드 Enum (NORMAL, CAUTIOUS, STRICT, EMERGENCY) |
| ReliabilityThresholds | 신뢰도 임계값 설정 |
| MetricReliabilityState | 메트릭 신뢰도 상태 |

### 17.9 Snapshot Storage

**경로**: `selfhealing.metrics.snapshot_storage`

| 컴포넌트 | 용도 |
|---------|------|
| MetricSnapshot | 메트릭 스냅샷 데이터 클래스 |
| L1 Local Snapshot | 로컬 파일 기반 스냅샷 저장소 |

---

## 18. Utils 모듈 (미등록)

> 유틸리티 함수 및 헬퍼

**경로**: `selfhealing.utils/`

### 18.1 Time Utilities

**경로**: `selfhealing.utils.time`

| 함수 | 용도 |
|------|------|
| utc_now | UTC 현재 시간 조회 |
| ensure_aware | 타임존 인식 datetime 변환 |
| to_iso_string | ISO 문자열 변환 |
| from_iso_string | ISO 문자열 파싱 |
| elapsed_seconds | 경과 시간 계산 |
| is_expired | 만료 여부 확인 |
| add_seconds | 초 추가 |
| format_duration | 기간 포맷팅 |

### 18.2 Async Healing Logger

**경로**: `selfhealing.utils.async_logger`

| 컴포넌트 | 용도 |
|---------|------|
| AsyncHealingLogger | 비동기 힐링 이벤트 로거 (Zero-Latency) |
| EventSeverity | 이벤트 심각도 Enum |

**기능**:
- 일반 이벤트: 배치로 모아서 전송
- CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
- 복구 경로에서 ~100ms 단축

**사용법**:
```python
def send_to_command_center(events):
    requests.post('http://command-center/events', json=events)

AsyncHealingLogger.configure(flush_callback=send_to_command_center)
AsyncHealingLogger.start()

# 일반 이벤트 (배치 처리)
AsyncHealingLogger.log({'type': 'retry', 'service': 'payment'})

# CRITICAL 이벤트 (즉시 전송)
AsyncHealingLogger.log({'type': 'cb_open', 'service': 'payment'}, EventSeverity.CRITICAL)
```

---

## 19. Tasks 모듈 (Celery Beat)

> Celery Beat 스케줄러 태스크 함수

**경로**: `selfhealing.tasks/`

### 19.1 Chaos Scheduler

**경로**: `selfhealing.tasks.chaos_scheduler`

| 함수 | 용도 |
|------|------|
| run_scheduled_experiments | 스케줄된 Chaos 실험 실행 |
| generate_daily_resilience_report | 일일 복원력 리포트 생성 |
| cleanup_expired_approvals | 만료된 승인 요청 정리 |
| check_and_alert_pending_approvals | 대기 중인 승인 알림 발송 |

### 19.2 Config Apply

**경로**: `selfhealing.tasks.config_apply`

| 태스크 | 용도 |
|--------|------|
| apply_pending_config_changes | 대기 중인 설정 변경 적용 |
| apply_graceful_config_change | Graceful 설정 변경 적용 (진행 중 작업 대기) |

### 19.3 Drift Detection

**경로**: `selfhealing.tasks.drift_detection`

| 컴포넌트 | 용도 |
|---------|------|
| SLADriftDetector | SLA 드리프트 감지기 (프레임워크 중립적) |
| FailedOperationQuerySet | 쿼리셋 프로토콜 |
| FailedOperationProtocol | FailedOperation 프로토콜 |
| SLAThresholdsProtocol | SLA 임계값 프로토콜 |

**원칙**: "System provides data, humans make decisions." - 경고만 생성, 자동 조정 없음

### 19.4 Governance

**경로**: `selfhealing.tasks.governance`

| 함수 | 용도 |
|------|------|
| check_emergency_mode_expiry | 긴급 모드 만료 체크 및 자동 복구 |
| get_governance_beat_schedule | Celery Beat 스케줄 설정 조회 |

**자동 복구 타임라인**:
- 4시간 경과: Admin 경고 발송
- 6시간 경과: "2시간 후 자동 복구" 최종 경고
- 8시간 경과: NORMAL 모드로 자동 복구

---

## 20. Models/SLO 모듈 (미등록)

### 20.1 SLO 정의

**경로**: `selfhealing.slo`

| 컴포넌트 | 용도 |
|---------|------|
| SLI | Service Level Indicator Enum |
| SLO | Service Level Objective 데이터 클래스 |
| ErrorBudget | Error Budget 계산 클래스 |

**SLI 타입**:
- `AVAILABILITY`: 가용성 (성공 비율)
- `LATENCY_P50/P90/P99`: 레이턴시 백분위
- `ERROR_RATE`: 에러율
- `THROUGHPUT`: 처리량
- `CUSTOM`: 사용자 정의

### 20.2 Drift Config

**경로**: `selfhealing.models.drift_config`

| 컴포넌트 | 용도 |
|---------|------|
| DriftThresholdConfig | 드리프트 임계값 설정 |

**임계값**:
- `warning_threshold`: 5% - 경고, 로그만 기록
- `critical_threshold`: 20% - 심각, 알림 발송
- `incident_threshold`: 50% - 인시던트, 이벤트 유실 의심

### 20.3 Configuration

**경로**: `selfhealing.config`

| 컴포넌트 | 용도 |
|---------|------|
| NotificationLimits | 알림 메시지 제한 설정 |
| ForensicSettings | Forensic Context 설정 |

---

## 21. DRF/Django 컴포넌트 (미등록)

### 21.1 Permissions

**경로**: `selfhealing.api.django.permissions`

| 컴포넌트 | 용도 |
|---------|------|
| IsSelfHealingAuthenticated | 인증 체크 (테스트 바이패스 지원) |
| IsViewer | 읽기 전용 권한 (selfhealing_viewer 그룹) |
| IsOperator | 운영자 권한 (selfhealing_operator 그룹) |
| IsAdmin | 관리자 권한 (selfhealing_admin 그룹) |
| EmergencyEscalation | Break Glass 비상 권한 |
| ThresholdBased | 위험 기반 접근 제어 |

### 21.2 Throttle Adapter

**경로**: `selfhealing.api.django.throttle_adapter`

| 컴포넌트 | 용도 |
|---------|------|
| AdaptiveDRFThrottle | Netflix Gradient 기반 적응형 DRF 쓰로틀 |

**기능**:
- Netflix Gradient 알고리즘 기반 동적 레이트 조정
- SLA-aware 임계값 조정
- RTT (Response Time) 추적
- 시스템 부하 기반 자동 한도 조정

### 21.3 Reauthentication

**경로**: `selfhealing.api.django.reauthentication`

| 컴포넌트 | 용도 |
|---------|------|
| ReauthenticationConfig | 재인증 설정 |
| ReauthenticationProvider | 재인증 프로바이더 ABC |
| requires_reauthentication | 재인증 필수 데코레이터 |

**기능**:
- 민감 작업에 대한 재인증 강제
- Idle Timeout / Session Duration 기반
- PCI-DSS 준수 설계

### 21.4 Stress Views

**경로**: `selfhealing.api.django.stress_views`

| 함수 | 용도 |
|------|------|
| get_pool_info | SQLAlchemy Pool 정보 조회 |

**용도**: 테스트 전용, DB Connection Pool 스트레스 테스트

### 21.5 Config Descriptions

**경로**: `selfhealing.api.django.config_descriptions`

| 컴포넌트 | 용도 |
|---------|------|
| CONFIG_DESCRIPTIONS | 설정 필드 설명 딕셔너리 |
| get_field_description | 필드 설명 조회 함수 |
| format_value_change | 값 변경 포맷팅 함수 |

**용도**: 감사 로그 가독성 향상, 비즈니스 친화적 설정 이름 제공

---

## 22. Notification 인터페이스 (미등록)

**경로**: `selfhealing.interfaces.notification`

| 컴포넌트 | 용도 |
|---------|------|
| NotificationSeverity | 알림 긴급도 Enum (critical, high, medium, low, info) |
| NotificationChannel | 알림 채널 Enum (slack, teams, pagerduty, email, webhook, sms, stdout, file) |
| Notification | 알림 페이로드 데이터 클래스 |
| NotificationAdapter | 알림 어댑터 프로토콜 |
| StdoutNotificationAdapter | 표준 출력 알림 어댑터 (기본) |
| LoggingNotificationAdapter | 로깅 알림 어댑터 |

**사용법**:
```python
from selfhealing.interfaces.notification import register_notification_adapter

class SlackNotificationAdapter(NotificationAdapter):
    def send(self, notification: Notification) -> bool:
        # Slack webhook 구현
        return True

register_notification_adapter(SlackNotificationAdapter())
```

---

## 23. SQLAlchemy Adapters (미등록)

**경로**: `selfhealing.adapters.sqlalchemy/`

### 23.1 Models

**경로**: `selfhealing.adapters.sqlalchemy.models`

| 모델 | 용도 |
|------|------|
| FailedOperationModel | DLQ 엔트리 SQLAlchemy 모델 |
| CircuitBreakerStateModel | CB 상태 SQLAlchemy 모델 |
| SecurityIncidentModel | 보안 인시던트 SQLAlchemy 모델 |

### 23.2 Repositories

| 파일 | 용도 |
|------|------|
| failed_operation.py | DLQ 저장소 구현 |
| circuit_breaker.py | CB 상태 저장소 구현 |
| security_incident.py | 보안 인시던트 저장소 구현 |

### 23.3 Base

**경로**: `selfhealing.adapters.sqlalchemy.base`

| 컴포넌트 | 용도 |
|---------|------|
| BaseRepository | SQLAlchemy 저장소 기본 클래스 |
| create_session_factory | 세션 팩토리 생성 함수 |

---

## 24. Frameworks Adapters (미등록)

**경로**: `selfhealing.adapters.frameworks/`

### 24.1 FastAPI Adapter

**경로**: `selfhealing.adapters.frameworks.fastapi_adapter`

| 컴포넌트 | 용도 |
|---------|------|
| FastAPIAdapter | FastAPI WebFrameworkInterface 구현 |

**기능**:
- APIRouter 생성 및 라우트 추가
- 동기/비동기 핸들러 지원
- 의존성 주입 연동
- OpenAPI 문서 자동 생성

**사용법**:
```python
from fastapi import FastAPI
from selfhealing.adapters.frameworks import FastAPIAdapter

app = FastAPI()
adapter = FastAPIAdapter()

router = adapter.create_router(prefix="/api/v1", tags=["payments"])

def handle_payment(ctx: RequestContext) -> ResponseContext:
    payment_id = ctx.path_params.get("id")
    return ResponseContext.json({"id": payment_id})

adapter.add_route(
    router,
    path="/payments/{id}",
    method=HttpMethod.GET,
    handler=handle_payment,
)

app.include_router(router)
```

---

## 25. Provider Registry/Factory (미등록)

> 플러그인 컴포넌트 중앙 레지스트리 및 팩토리

**경로**: `selfhealing.factory`

| 컴포넌트 | 용도 |
|---------|------|
| ProviderRegistry | 플러그인 컴포넌트 중앙 레지스트리 (싱글톤) |

**레지스트리 메서드**:

| 메서드 | 용도 |
|--------|------|
| register_cache | 캐시 프로바이더 등록 |
| register_queue | 태스크 큐 등록 |
| register_failed_operation_repo | DLQ 저장소 등록 |
| register_circuit_breaker_repo | CB 상태 저장소 등록 |
| register_security_repo | 보안 인시던트 저장소 등록 |
| register_audit_adapter | 감사 로그 어댑터 등록 |
| get_cache | 캐시 프로바이더 조회 |
| get_queue | 태스크 큐 조회 |
| get_failed_operation_repo | DLQ 저장소 조회 |
| get_circuit_breaker_repo | CB 상태 저장소 조회 |
| get_security_repo | 보안 인시던트 저장소 조회 |
| get_audit_adapter | 감사 로그 어댑터 조회 |
| set_default_cache | 기본 캐시 설정 |
| set_default_queue | 기본 큐 설정 |
| set_default_repo | 기본 저장소 설정 |
| set_default_audit | 기본 감사 어댑터 설정 |

**기본값**:
- Cache: `memory`
- Queue: `sync`
- Repository: `django`
- Audit: `file`

**사용법**:
```python
from selfhealing.factory import ProviderRegistry

# 기본 프로바이더 조회
cache = ProviderRegistry.get_cache()
queue = ProviderRegistry.get_queue()

# 특정 프로바이더 조회
cache = ProviderRegistry.get_cache("redis")
queue = ProviderRegistry.get_queue("sync")

# 커스텀 프로바이더 등록
ProviderRegistry.register_cache("custom", CustomCacheAdapter)
```

---

## 26. Django Adapters (미등록)

> Django ORM 기반 어댑터 구현

**경로**: `selfhealing.adapters.django/`

### 26.1 Django Models

**경로**: `selfhealing.adapters.django.models`

| 모델 | 용도 |
|------|------|
| FailedOperation | DLQ 엔트리 Django 모델 |
| CircuitBreakerState | CB 상태 Django 모델 |
| SecurityIncident | 보안 인시던트 Django 모델 |

**FailedOperation 필드**:
- `domain`: 도메인 (payment, point, inventory 등)
- `failure_type`: 실패 유형
- `status`: 상태 (pending, reviewing, resolved 등)
- `entity_type`, `entity_id`: 제네릭 엔티티 참조
- `snapshot_data`: JSON 스냅샷
- `retry_count`, `max_retries`: 재시도 정보
- `resolution_type`, `resolution_note`: 해결 정보

### 26.2 Django Repositories

| 파일 | 용도 |
|------|------|
| failed_operation_repository.py | DLQ 저장소 Django 구현 |
| circuit_breaker_repository.py | CB 상태 저장소 Django 구현 |
| security_incident_repository.py | 보안 인시던트 저장소 Django 구현 |
| config_provider.py | Django settings 기반 설정 프로바이더 |

---

## 27. Throttle Services (미등록)

> Netflix Gradient 기반 적응형 쓰로틀링

**경로**: `selfhealing.services.throttle/`

### 27.1 Config

**경로**: `selfhealing.services.throttle.config`

| 컴포넌트 | 용도 |
|---------|------|
| ThrottleConfig | 쓰로틀 설정 클래스 |
| ThrottleResult | 쓰로틀 결과 DTO |

### 27.2 Base

**경로**: `selfhealing.services.throttle.base`

| 컴포넌트 | 용도 |
|---------|------|
| BaseThrottle | 쓰로틀 기본 클래스 |
| SlidingWindowThrottle | 슬라이딩 윈도우 쓰로틀 |

### 27.3 Adaptive

**경로**: `selfhealing.services.throttle.adaptive`

| 컴포넌트 | 용도 |
|---------|------|
| AdaptiveThrottle | Netflix Gradient 적응형 쓰로틀 |
| GradientCalculator | RTT 그라디언트 계산기 |
| RTTSample | RTT 샘플 데이터 클래스 |
| get_adaptive_throttle | 적응형 쓰로틀 인스턴스 조회 |
| reset_adaptive_throttle | 적응형 쓰로틀 초기화 |

**알고리즘**:
1. 500ms마다 RTT 샘플링
2. RTT 그라디언트 계산 (증가/감소 추세)
3. 리밋 조정:
   - 그라디언트 > 0 (RTT 증가): limit = limit × 0.9
   - 그라디언트 <= 0 (RTT 안정/감소): limit = limit + 1

---

## 참고 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [02_ARCHITECTURE.md](02_ARCHITECTURE.md) - 아키텍처 설계
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - 서킷브레이커 상세
- [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) - DLQ 상세
- [07_CONTROL_API.md](07_CONTROL_API.md) - API 엔드포인트 및 Serializers
- [56_AUDIT_MIDDLEWARE_DESIGN.md](56_AUDIT_MIDDLEWARE_DESIGN.md) - Audit 미들웨어 설계



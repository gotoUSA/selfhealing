# Self-Healing 미들웨어 게이트웨이 파이프라인

> **Version**: 2.2.0
> **Updated**: 2026-01-02
> **Category**: 요청/응답 파이프라인

---

## 📋 목차

1. [개요](#1-개요)
2. [Django 등록 미들웨어](#2-django-등록-미들웨어)
3. [Django 미등록 미들웨어](#3-django-미등록-미들웨어)
4. [FastAPI 미들웨어](#4-fastapi-미들웨어)
5. [Tiering 시스템](#5-tiering-시스템)
6. [DRF 컴포넌트](#6-drf-컴포넌트)
7. [Frameworks 어댑터](#7-frameworks-어댑터)
8. [Throttle 서비스](#8-throttle-서비스)

---

## 1. 개요

이 문서는 Self-Healing 시스템의 **요청/응답 파이프라인**을 담당하는 미들웨어 및 게이트웨이 컴포넌트를 다룹니다.

### 1.1 범위

- Django 미들웨어 (등록/미등록)
- FastAPI 미들웨어
- Tiering 시스템
- Rate Limiting & Throttling
- DRF 권한/인증 컴포넌트

### 1.2 요청 흐름

```
HTTP Request
    │
    ▼
┌─────────────────────────────────────────────────┐
│ Django/FastAPI Middleware Stack                  │
│ ┌─────────────────────────────────────────────┐ │
│ │ HealthBridgeMiddleware (헬스체크 바이패스)   │ │
│ ├─────────────────────────────────────────────┤ │
│ │ SelfHealingMiddleware (복원력)              │ │
│ ├─────────────────────────────────────────────┤ │
│ │ HybridRateLimitMiddleware (레이트 리밋)     │ │
│ ├─────────────────────────────────────────────┤ │
│ │ ChaosMiddleware (카오스 엔지니어링)         │ │
│ ├─────────────────────────────────────────────┤ │
│ │ ConnectionPoolLimiterMiddleware (커넥션)    │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
    │
    ▼
  View / Handler
```

---

## 2. Django 등록 미들웨어

> `settings.py`의 `MIDDLEWARE` 배열에 등록된 미들웨어

### 2.1 HealthBridgeMiddleware

**경로**: `myproject.middleware.HealthBridgeMiddleware`

**역할**: Django 헬스 체크 엔드포인트 연결

| 구성요소 | 설명 |
|----------|------|
| 헬스 경로 | `/health/`, `/healthz/` |
| 바이패스 | 헬스 경로는 이후 미들웨어 스킵 |
| 연동 | Kubernetes Liveness/Readiness Probe |

```python
class HealthBridgeMiddleware:
    def __call__(self, request):
        if request.path in ['/health/', '/healthz/']:
            return JsonResponse({'status': 'healthy'})
        return self.get_response(request)
```

### 2.2 SelfHealingMiddleware

**경로**: `myproject.middleware.SelfHealingMiddleware`

**역할**: 복원력 로직 통합 (Circuit Breaker, DLQ 연동)

| 구성요소 | 설명 |
|----------|------|
| Circuit Breaker 체크 | 요청 전 CB 상태 확인 |
| 예외 캡처 | 실패 시 DLQ 전송 |
| 컨텍스트 전파 | Request ID, Actor 정보 전파 |

**연동 서비스** ([02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md) 참조):
- `CircuitBreakerService`
- `DLQService`
- `ForensicContextService`

### 2.3 HybridRateLimitMiddleware

**경로**: `myproject.middleware.HybridRateLimitMiddleware`

**역할**: 하이브리드 레이트 리밋 (Token Bucket + Sliding Window)

| 구성요소 | 설명 |
|----------|------|
| Token Bucket | 순간 버스트 허용 |
| Sliding Window | 일관된 비율 유지 |
| 적응형 리밋 | Netflix Gradient 알고리즘 기반 동적 조정 |

**설정 예시**:
```python
RATE_LIMIT_CONFIG = {
    'default': {
        'bucket_capacity': 100,
        'refill_rate': 10,  # per second
        'window_size': 60,  # seconds
        'window_limit': 300,
    }
}
```

### 2.4 ChaosMiddleware

**경로**: `myproject.middleware.ChaosMiddleware`

**역할**: Chaos Engineering 실험 주입

| 구성요소 | 설명 |
|----------|------|
| 레이턴시 주입 | 임의 지연 추가 |
| 에러 주입 | 임의 예외 발생 |
| 중단 주입 | 요청 중단 시뮬레이션 |
| 승인 기반 | 실험 승인 후 실행 |

**주입 타입**:
- `latency`: 지연 (ms 단위)
- `exception`: 예외 발생
- `abort`: HTTP 에러 응답
- `blackhole`: 응답 없음

### 2.5 ConnectionPoolLimiterMiddleware

**경로**: `myproject.middleware.ConnectionPoolLimiterMiddleware`

**역할**: DB 커넥션 풀 보호

| 구성요소 | 설명 |
|----------|------|
| 풀 감시 | 커넥션 사용률 모니터링 |
| 조기 거부 | 풀 고갈 전 요청 거부 |
| Backpressure | 클라이언트에 재시도 신호 |

### 2.6 RateLimitingMiddleware (미사용)

**경로**: `myproject.middleware.RateLimitingMiddleware`

**상태**: Deprecated - HybridRateLimitMiddleware로 대체

### 2.7 PrometheusBeforeMiddleware / PrometheusAfterMiddleware

**경로**: `django_prometheus.middleware`

**역할**: Prometheus 메트릭 수집

| 구성요소 | 설명 |
|----------|------|
| Before | 요청 시작 시간 기록 |
| After | 응답 메트릭 수집 |

---

## 3. Django 미등록 미들웨어

> `settings.py`에 등록되지 않았으나 코드베이스에 존재하는 미들웨어

### 3.1 PoolCircuitBreakerMiddleware

**경로**: `myproject.middleware.pool_circuit_breaker`

**역할**: Connection Pool 전용 Circuit Breaker

| 구성요소 | 설명 |
|----------|------|
| 풀 상태 감시 | DB 커넥션 풀 헬스 체크 |
| 자동 차단 | 풀 고갈 시 CB Open |
| Graceful 복구 | Half-Open → Closed 전환 |

### 3.2 AuditMiddleware

**경로**: `myproject.middleware.audit`

**역할**: 감사 로그 자동 수집

| 구성요소 | 설명 |
|----------|------|
| Request 로깅 | 요청 정보 기록 |
| Response 로깅 | 응답 정보 기록 |
| Actor 추적 | 사용자/시스템 식별 |

**연동**: [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) - Audit Backends

### 3.3 SensitiveAccessLoggingMiddleware

**경로**: `myproject.middleware.sensitive_access_logging`

**역할**: 민감 데이터 접근 감사

| 구성요소 | 설명 |
|----------|------|
| PII 탐지 | 개인정보 접근 감지 |
| 경로 매칭 | 민감 경로 패턴 매칭 |
| 알림 연동 | 보안팀 자동 알림 |

### 3.4 TieringMiddleware

**경로**: `selfhealing.api.django.tiering.middleware.TieringMiddleware`

**역할**: Emergency Mode 트래픽 제어 ([섹션 5](#5-tiering-시스템) 참조)

### 3.5 ActorContextMiddleware

**경로**: `myproject.middleware.actor_context`

**역할**: Actor 컨텍스트 설정

| 구성요소 | 설명 |
|----------|------|
| User 추출 | 인증된 사용자 정보 |
| Service 추출 | 서비스 간 호출 정보 |
| 컨텍스트 저장 | ContextVar에 저장 |

### 3.6 PoolTimeoutMiddleware

**경로**: `myproject.middleware.pool_timeout`

**역할**: 커넥션 획득 타임아웃 설정

| 구성요소 | 설명 |
|----------|------|
| 동적 타임아웃 | 부하에 따른 타임아웃 조정 |
| Backpressure | 타임아웃 시 503 응답 |

### 3.7 trace_id Middleware

**경로**: `myproject.middleware.trace_id`

**역할**: 분산 추적 ID 전파

| 구성요소 | 설명 |
|----------|------|
| 헤더 추출 | X-Request-ID, X-Trace-ID |
| 자동 생성 | 헤더 없을 시 UUID 생성 |
| 로깅 연동 | 모든 로그에 trace_id 포함 |

### 3.8 ExitProofMiddleware

**경로**: `myproject.middleware.exit_proof`

**역할**: 정상 종료 증명

| 구성요소 | 설명 |
|----------|------|
| Shutdown 감지 | SIGTERM 수신 감지 |
| 진행 중 요청 | 완료 대기 후 종료 |
| 타임아웃 | 최대 대기 시간 설정 |

### 3.9 DDoSProtectionMiddleware

**경로**: `myproject.middleware.ddos_protection`

**역할**: DDoS 공격 방어

| 구성요소 | 설명 |
|----------|------|
| 패턴 탐지 | 비정상 트래픽 패턴 감지 |
| IP 차단 | 공격 IP 자동 차단 |
| 레이트 리밋 | 동적 레이트 리밋 적용 |

---

## 4. FastAPI 미들웨어

> FastAPI 애플리케이션용 미들웨어

**경로**: `selfhealing.adapters.fastapi.middleware`

### 4.1 SelfHealingMiddleware

**역할**: FastAPI용 복원력 통합 미들웨어

```python
from selfhealing.adapters.fastapi.middleware import SelfHealingMiddleware

app = FastAPI()
app.add_middleware(SelfHealingMiddleware)
```

| 기능 | 설명 |
|------|------|
| Circuit Breaker | 요청별 CB 체크 |
| DLQ 연동 | 실패 시 DLQ 전송 |
| 메트릭 수집 | Prometheus 메트릭 |
| 컨텍스트 전파 | Request ID, Actor |

### 4.2 ShutdownMiddleware

**역할**: Graceful Shutdown 지원

```python
from selfhealing.adapters.fastapi.middleware import ShutdownMiddleware

app.add_middleware(ShutdownMiddleware, shutdown_timeout=30)
```

| 기능 | 설명 |
|------|------|
| 신호 처리 | SIGTERM/SIGINT 처리 |
| 진행 중 요청 | 완료 대기 |
| 타임아웃 | 최대 대기 시간 |
| 상태 엔드포인트 | `/shutdown/status` |

---

## 5. Tiering 시스템

> Emergency Mode Load Shedding을 위한 API 계층 관리

**경로**: `selfhealing.api.django.tiering/`

### 5.1 개요

Tiering 시스템은 **비상 모드(Emergency Mode)에서 API 경로 기반으로 트래픽을 제어**합니다.
고객 등급이 아닌 API의 중요도에 따라 Tier를 분류하고, 시스템 과부하 시 비필수 API를 우선 차단합니다.

```
Emergency Mode 발생
    │
    ▼
┌─────────────────────────────────────────────────┐
│ TieringMiddleware                                │
│ ┌─────────────────────────────────────────────┐ │
│ │ 1. EmergencyManager에서 현재 레벨 확인      │ │
│ ├─────────────────────────────────────────────┤ │
│ │ 2. 요청 경로 → Tier 매핑                    │ │
│ ├─────────────────────────────────────────────┤ │
│ │ 3. Tier multiplier로 확률적 허용/차단       │ │
│ └─────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────┘
    │
    ▼
  허용 → View  /  차단 → 503 Load Shedding
```

### 5.2 Tier 정의

| Tier ID | 이름 | Multiplier | 용도 |
|---------|------|------------|------|
| `critical` | Mission Critical | 0.5 (50%) | 장애 시에도 반드시 동작해야 하는 핵심 API |
| `standard` | Operational | 0.1 (10%) | 일반 운영 API |
| `non_essential` | Non-Essential | 0.0 (0%) | 비필수 API (Load Shedding 우선 대상) |

> **Multiplier**: Emergency Mode에서 허용되는 요청 비율 (0.0 = 전체 차단, 1.0 = 전체 허용)

### 5.3 Emergency Level별 동작

| Level | 설명 | critical | standard | non_essential |
|-------|------|----------|----------|---------------|
| `NORMAL (0)` | 정상 운영 | 100% | 100% | 100% |
| `LEVEL_1 (1)` | 경계 | 100% | 100% | 0% |
| `LEVEL_2 (2)` | 주의 | 100% | 10% | 0% |
| `LEVEL_3 (3)` | 위험 | 50% | 0% | 0% |

### 5.4 Tier 매핑 (기본값)

| API 경로 패턴 | Tier | 패턴 타입 |
|---------------|------|----------|
| `/api/self-healing/control/` | `critical` | EXACT |
| `/api/self-healing/allow/*` | `critical` | WILDCARD |
| `/api/self-healing/block/*` | `critical` | WILDCARD |
| `/api/self-healing/system/*` | `critical` | WILDCARD |
| `/api/self-healing/config/*` | `standard` | WILDCARD |
| `/api/self-healing/dlq/*` | `standard` | WILDCARD |
| `/api/self-healing/status/*` | `standard` | WILDCARD |
| `/api/self-healing/dashboard/*` | `non_essential` | WILDCARD |
| `/api/self-healing/metrics/` | `non_essential` | EXACT |

### 5.5 Override 기능

특정 클라이언트에 대해 Tier를 재정의할 수 있습니다:

| 식별자 타입 | 예시 | 설명 |
|-------------|------|------|
| IP | `10.0.0.0/8` | CIDR 지원, 내부 네트워크 우선 처리 |
| User ID | `admin_user_123` | 특정 사용자 우선 처리 |
| API Key | `sk_live_xxx` | 특정 API 키 우선 처리 |

**기본 Override (내부 네트워크 → critical)**:
- `10.0.0.0/8` - 내부 모니터링 시스템
- `172.16.0.0/12` - 내부 네트워크
- `192.168.0.0/16` - 내부 네트워크

### 5.6 Static Critical Paths (Defense-in-Depth)

코드에 하드코딩된 최후 방어선 (설정 변경 불가):

```python
STATIC_CRITICAL_PATHS = frozenset([
    "/api/self-healing/control/",
    "/api/self-healing/emergency/",
    "/api/auth/token/",
])
```

### 5.7 설정

```python
# settings.py
MIDDLEWARE = [
    ...
    'selfhealing.api.django.tiering.TieringMiddleware',
    ...
]

# 미들웨어 비활성화 (선택)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

---

## 6. DRF 컴포넌트

> Django REST Framework 권한/인증/쓰로틀 컴포넌트

**경로**: `selfhealing.api.django/`

### 6.1 Permissions

**경로**: `selfhealing.api.django.permissions`

| 클래스 | 용도 |
|--------|------|
| `IsSelfHealingAuthenticated` | 인증 체크 (테스트 바이패스 지원) |
| `IsViewer` | 읽기 전용 권한 (`selfhealing_viewer` 그룹) |
| `IsOperator` | 운영자 권한 (`selfhealing_operator` 그룹) |
| `IsAdmin` | 관리자 권한 (`selfhealing_admin` 그룹) |
| `EmergencyEscalation` | Break Glass 비상 권한 |
| `ThresholdBased` | 위험 기반 접근 제어 |

**권한 계층**:
```
Admin > Operator > Viewer
         │
         └─ EmergencyEscalation (위기 시)
```

### 6.2 Throttle Adapter

**경로**: `selfhealing.api.django.throttle_adapter`

| 클래스 | 용도 |
|--------|------|
| `AdaptiveDRFThrottle` | Netflix Gradient 기반 적응형 쓰로틀 |

**기능**:
- RTT (Response Time) 추적
- 그라디언트 기반 리밋 조정
- SLA-aware 임계값 조정
- 시스템 부하 기반 자동 조정

```python
class MyAPIView(APIView):
    throttle_classes = [AdaptiveDRFThrottle]
```

### 6.3 Reauthentication

**경로**: `selfhealing.api.django.reauthentication`

| 컴포넌트 | 용도 |
|---------|------|
| `ReauthenticationConfig` | 재인증 설정 |
| `ReauthenticationProvider` | 재인증 프로바이더 ABC |
| `requires_reauthentication` | 재인증 필수 데코레이터 |

**기능**:
- 민감 작업에 대한 재인증 강제
- Idle Timeout / Session Duration 기반
- PCI-DSS 준수 설계

```python
@requires_reauthentication(max_idle=300)  # 5분 idle 시 재인증
def sensitive_action(request):
    ...
```

### 6.4 Config Descriptions

**경로**: `selfhealing.api.django.config_descriptions`

| 컴포넌트 | 용도 |
|---------|------|
| `CONFIG_DESCRIPTIONS` | 설정 필드 설명 딕셔너리 |
| `get_field_description` | 필드 설명 조회 |
| `format_value_change` | 값 변경 포맷팅 |

**용도**: 감사 로그 가독성 향상, 비즈니스 친화적 설정 이름 제공

### 6.5 Stress Views

**경로**: `selfhealing.api.django.stress_views`

| 함수 | 용도 |
|------|------|
| `get_pool_info` | SQLAlchemy Pool 정보 조회 |

**용도**: 테스트 전용, DB Connection Pool 스트레스 테스트

---

## 7. Frameworks 어댑터

> 다양한 웹 프레임워크 통합 어댑터

**경로**: `selfhealing.adapters.frameworks/`

### 7.1 FastAPIAdapter

**경로**: `selfhealing.adapters.frameworks.fastapi_adapter`

| 컴포넌트 | 용도 |
|---------|------|
| `FastAPIAdapter` | FastAPI WebFrameworkInterface 구현 |

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

## 8. Throttle 서비스

> Netflix Gradient 기반 적응형 쓰로틀링

**경로**: `selfhealing.services.throttle/`

### 8.1 Config

**경로**: `selfhealing.services.throttle.config`

| 컴포넌트 | 용도 |
|---------|------|
| `ThrottleConfig` | 쓰로틀 설정 클래스 |
| `ThrottleResult` | 쓰로틀 결과 DTO |

### 8.2 Base

**경로**: `selfhealing.services.throttle.base`

| 컴포넌트 | 용도 |
|---------|------|
| `BaseThrottle` | 쓰로틀 기본 클래스 |
| `SlidingWindowThrottle` | 슬라이딩 윈도우 쓰로틀 |

### 8.3 Adaptive

**경로**: `selfhealing.services.throttle.adaptive`

| 컴포넌트 | 용도 |
|---------|------|
| `AdaptiveThrottle` | Netflix Gradient 적응형 쓰로틀 |
| `GradientCalculator` | RTT 그라디언트 계산기 |
| `RTTSample` | RTT 샘플 데이터 클래스 |
| `get_adaptive_throttle` | 인스턴스 조회 |
| `reset_adaptive_throttle` | 인스턴스 초기화 |

**알고리즘**:
1. 500ms마다 RTT 샘플링
2. RTT 그라디언트 계산 (증가/감소 추세)
3. 리밋 조정:
   - 그라디언트 > 0 (RTT 증가): `limit = limit × 0.9`
   - 그라디언트 ≤ 0 (RTT 안정/감소): `limit = limit + 1`

**설정 예시**:
```python
from selfhealing.services.throttle import AdaptiveThrottle, ThrottleConfig

config = ThrottleConfig(
    initial_limit=100,
    min_limit=10,
    max_limit=1000,
    sample_interval_ms=500,
    smoothing_factor=0.2,
)

throttle = AdaptiveThrottle(config)

# 요청 처리
if throttle.acquire():
    try:
        response = process_request()
        throttle.on_success(response_time_ms=50)
    except Exception:
        throttle.on_failure()
else:
    # 쓰로틀링됨
    return HttpResponse(status=429)
```

---

## 📎 관련 문서

- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
- [02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md) - 비즈니스 로직 엔진
- [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) - 인프라 어댑터
- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 시스템

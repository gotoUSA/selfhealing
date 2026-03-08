# 320. Django Auto-Configuration — Consumer 설정 최소화

> **Status**: Planning
> **Severity**: P1 (HIGH) — repo 분리 선행 조건
> **Target**: `selfhealing/adapters/django/` (configure_selfhealing + AppConfig)
> **References**:
> - 319 — Repo Separation Overview (커플링 C1, C3, C5, C6)
> - 223 — Host App Decoupling (AppConfig 기반 확장)
> - 316 — Gunicorn Preload Optimization (Fork-Safety)
> - 322 — Gunicorn Hook Helper (post_fork / post_worker_init)

---

## ADR: 왜 AppConfig.ready() 대신 명시적 Wrapper를 선택했는가

### 배경

초기 설계(v1)는 `AppConfig.ready()`에서 `settings.MIDDLEWARE`, `settings.REST_FRAMEWORK`를
런타임에 수정하는 "마법(Magic)" 방식이었다. 기술 리뷰를 통해 7가지 구조적 문제가 발견되었다.

### 결정

**`configure_selfhealing()` 명시적 래퍼 + 역할 분리 하이브리드 아키텍처**를 채택한다.

| 영역 | 방식 | 이유 |
|------|------|------|
| MIDDLEWARE, REST_FRAMEWORK | `configure_selfhealing()` 래퍼 (settings.py) | Django 전역 설정 → 명시적이어야 함 |
| 내부 상태 초기화 | `AppConfig.ready()` 유지 | selfhealing 내부 상태 → AppConfig가 적합 |
| OTEL | 322 문서의 `post_worker_init` 훅 | Fork-safety 필수 → Gunicorn 훅이 적합 |
| Celery 시그널 | Consumer `celery.py`에서 1줄 수동 호출 | 앱 인스턴스 바인딩 보장 필요 |

### 근거 (리뷰에서 발견된 7가지 문제)

1. **미들웨어 캐싱 타이밍 (Q1)**: `ready()`에서 MIDDLEWARE를 수정해도 표준 라이프사이클에서는
   반영되지만, ASGI 환경(Uvicorn direct import)과 `django.test.Client`에서 `load_middleware()`가
   `ready()` 이전에 호출될 수 있다. 래퍼 패턴은 settings.py 평가 시점에 MIDDLEWARE가 완성되므로
   이 타이밍 이슈가 원천 소멸한다.

2. **DRF api_settings 캐싱 충돌 (Q2)**: DRF는 모듈 임포트 시점에 `settings.REST_FRAMEWORK`를
   `APISettings` 인스턴스에 캐싱한다. `ready()`에서 settings를 수정해도 이미 캐싱된 `api_settings`
   인스턴스에는 반영되지 않는다. 래퍼 함수를 settings.py 최하단에 배치하면 DRF 임포트 전에
   `REST_FRAMEWORK`가 완성되므로 100% 안전하다.

3. **명시성 vs 마법 (Q3)**: 기존 `ready()`의 9개 메서드는 selfhealing **내부** 상태 초기화이지만,
   MIDDLEWARE/REST_FRAMEWORK 수정은 **Consumer의 Django 전역 설정 변경**으로 질적으로 다르다.
   엔터프라이즈 환경에서 "누가 이 설정을 넣었는지" 추적 불가능한 마법은 극도로 기피된다.

4. **OTEL Fork-Safety (Q4)**: `initialize_opentelemetry()`이 `BatchSpanProcessor`를 생성하면
   마스터에서 gRPC 채널 + 백그라운드 스레드가 생성된다. `fork()` 후 워커에서 해당 스레드 사망 →
   Span 유실 + Deadlock 가능. 322 문서의 `post_worker_init` 훅으로 완전 위임해야 한다.

5. **Celery current_app 바인딩 (Q5)**: `ready()` 시점에서 `from celery import current_app`은
   Consumer의 Celery 인스턴스가 아닌 디폴트 인스턴스가 잡힐 수 있다. Consumer `celery.py`에서
   `setup_selfhealing_signals(app=app)` 1줄을 유지하는 것이 바인딩을 보장한다.

6. **미들웨어 순서 제어권 (Q6)**: `after:ClassName` 방식은 Consumer가 selfhealing 미들웨어
   사이에 자체 미들웨어를 넣을 수 없다. 그룹(Slot) 패턴으로 전환하여 Consumer에게 완전한
   순서 제어권을 보장한다.

7. **Opt-out 플래그 파편화 (Q7)**: 4개 auto 플래그 + 5개 미들웨어 토글 = 9개 플래그가
   settings.py에 흩어진다. `configure_selfhealing()` 함수 파라미터로 통합하고,
   기존 Pydantic 설정 체계(`AutoConfigSettings`)에 편입한다.

---

## 1. 현황 및 문제

### 1.1 현재: Consumer가 수동으로 설정하는 것들

Consumer(`myproject/settings/base.py`)에서 selfhealing 미들웨어 14개를 직접 MIDDLEWARE에 나열하고,
`myproject/celery.py`에서 시그널을 수동 설정하고,
`myproject/wsgi.py`에서 `get_config()`을 수동 호출한다.

**문제**: 새 consumer 앱을 만들 때 이 설정을 모두 복사해야 하며, 미들웨어 순서를 잘못 배치하면 동작이 깨진다.

### 1.2 목표: 명시적이면서도 최소한의 설정

```python
# consumer settings.py — Django 코어만 나열하고, 맨 마지막에 래퍼 1줄
from selfhealing.adapters.django import configure_selfhealing

INSTALLED_APPS = ["selfhealing.adapters.django", ...]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "...",
}

# 파일 맨 마지막 — selfhealing이 MIDDLEWARE, REST_FRAMEWORK를 안전하게 래핑
configure_selfhealing(namespace=globals())
```

---

## 2. 아키텍처: 역할 분리 하이브리드

### 2.1 configure_selfhealing() — Consumer 설정 래핑 (settings.py)

**역할**: MIDDLEWARE 자동 삽입, REST_FRAMEWORK EXCEPTION_HANDLER 설정

**배치**: `selfhealing/adapters/django/__init__.py` 또는 `selfhealing/adapters/django/auto_config.py`

### 2.2 AppConfig.ready() — 내부 상태 초기화 (기존 유지)

**역할**: hash chain sync, orphan services, secrets validation, session signals 등

**변경 없음**: `apps.py:125-181`의 기존 `ready()` 메서드 그대로 유지

### 2.3 Gunicorn Hook — Fork-sensitive 컴포넌트 (322 문서)

**역할**: OTEL 초기화, 백그라운드 스레드 시작

**배치**: Consumer의 `gunicorn.conf.py` → `selfhealing.server.post_worker_init_start()`

### 2.4 Consumer celery.py — Celery 시그널 (수동 1줄)

**역할**: `setup_selfhealing_signals(app=app)` — 앱 인스턴스 바인딩 보장

---

## 3. configure_selfhealing() 상세 설계

### 3.1 미들웨어 그룹(Slot) 패턴

현재 14개 selfhealing 미들웨어는 기능적 의존성에 의해 자연스럽게 3개 그룹으로 나뉜다:

| 그룹 | 미들웨어 | 배치 규칙 |
|------|---------|----------|
| **early** | trace_id, HealthBridge, Tiering, IPBan, SelfHealing, ActorContext | Django core **이전** (PrometheusBeforeMiddleware 직후) |
| **post_auth** | CellTagging, BaggageSync, RateLimit, PoolCB | `AuthenticationMiddleware` **이후** |
| **tail** | AuditMiddleware | 항상 **마지막** (PrometheusAfterMiddleware 직전) |

```python
# selfhealing/adapters/django/auto_config.py

DEFAULT_EARLY_GROUP = [
    "selfhealing.audit.trace.trace_id_middleware",
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.tiering.TieringMiddleware",
    "selfhealing.api.django.middleware.IPBanMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
]

DEFAULT_POST_AUTH_GROUP = [
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",
    "selfhealing.api.django.cell.middleware.BaggageSyncMiddleware",
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",
]

DEFAULT_TAIL_GROUP = [
    "selfhealing.api.django.audit_middleware.AuditMiddleware",
]

# 미들웨어별 토글 설정명 매핑
MIDDLEWARE_TOGGLES = {
    "selfhealing.api.django.tiering.TieringMiddleware": "SELFHEALING_TIERING_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware": "SELFHEALING_ACTOR_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware": "SELFHEALING_CELL_TAGGING_ENABLED",
    "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware": "SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED",
    "selfhealing.api.django.audit_middleware.AuditMiddleware": "SELFHEALING_AUDIT_MIDDLEWARE_ENABLED",
}
```

### 3.2 함수 시그니처

```python
def configure_selfhealing(
    namespace: dict,
    *,
    early_group: list[str] | None = None,
    post_auth_group: list[str] | None = None,
    tail_group: list[str] | None = None,
    domains: list[str] | None = None,
    disable_auto_otel: bool = False,
) -> None:
    """Consumer settings.py에서 호출하여 selfhealing 설정을 명시적으로 래핑.

    Args:
        namespace: Consumer settings 모듈의 globals() — MIDDLEWARE, REST_FRAMEWORK 등을 수정
        early_group: Django core 이전에 삽입할 미들웨어 리스트 (기본: DEFAULT_EARLY_GROUP)
        post_auth_group: AuthenticationMiddleware 이후 삽입할 미들웨어 리스트 (기본: DEFAULT_POST_AUTH_GROUP)
        tail_group: 마지막에 삽입할 미들웨어 리스트 (기본: DEFAULT_TAIL_GROUP)
        domains: SELFHEALING_CORE_DOMAINS 설정 (비즈니스 도메인 목록)
        disable_auto_otel: True이면 OTEL 관련 설정 건너뜀 (Gunicorn 훅으로 지연 시)

    Note:
        이 함수는 반드시 settings.py의 **맨 마지막**에서 호출해야 한다.
        MIDDLEWARE, REST_FRAMEWORK 등이 모두 정의된 후에 호출되어야 올바르게 동작한다.
    """
```

### 3.3 미들웨어 삽입 로직

```python
def _inject_middleware_groups(namespace, early, post_auth, tail):
    """MIDDLEWARE 리스트에 selfhealing 미들웨어 그룹을 삽입."""
    middleware = list(namespace.get("MIDDLEWARE", []))

    # 토글로 비활성화된 미들웨어 필터링
    early = _filter_by_toggles(early, namespace)
    post_auth = _filter_by_toggles(post_auth, namespace)
    tail = _filter_by_toggles(tail, namespace)

    # 이미 존재하는 항목은 건너뜀 (Consumer가 수동으로 넣은 경우)
    early = [m for m in early if m not in middleware]
    post_auth = [m for m in post_auth if m not in middleware]
    tail = [m for m in tail if m not in middleware]

    # early → PrometheusBeforeMiddleware 직후 (없으면 맨 앞)
    early_idx = _find_insert_point(middleware, "PrometheusBeforeMiddleware", after=True, fallback=0)
    for i, m in enumerate(early):
        middleware.insert(early_idx + i, m)

    # post_auth → AuthenticationMiddleware 직후 (없으면 Django core 뒤)
    auth_idx = _find_insert_point(middleware, "AuthenticationMiddleware", after=True, fallback=len(middleware))
    # XFrameOptionsMiddleware 이후까지 건너뜀 (Django core 전체 통과)
    xframe_idx = _find_insert_point(middleware, "XFrameOptionsMiddleware", after=True, fallback=auth_idx)
    insert_idx = max(auth_idx, xframe_idx)
    for i, m in enumerate(post_auth):
        middleware.insert(insert_idx + i, m)

    # tail → PrometheusAfterMiddleware 직전 (없으면 맨 뒤)
    tail_idx = _find_insert_point(middleware, "PrometheusAfterMiddleware", after=False, fallback=len(middleware))
    for i, m in enumerate(tail):
        middleware.insert(tail_idx + i, m)

    namespace["MIDDLEWARE"] = middleware


def _filter_by_toggles(group, namespace):
    """토글 설정이 False인 미들웨어를 그룹에서 제거."""
    result = []
    for m in group:
        toggle = MIDDLEWARE_TOGGLES.get(m)
        if toggle and not namespace.get(toggle, True):
            continue
        result.append(m)
    return result


def _find_insert_point(middleware, target_substr, *, after, fallback):
    """미들웨어 리스트에서 target을 찾아 삽입 위치 반환."""
    for i, m in enumerate(middleware):
        if target_substr in m:
            return (i + 1) if after else i
    return fallback
```

### 3.4 EXCEPTION_HANDLER 자동 설정

```python
def _setup_exception_handler(namespace):
    """DRF EXCEPTION_HANDLER를 자동 설정."""
    rest_settings = namespace.get("REST_FRAMEWORK", {})
    if "EXCEPTION_HANDLER" not in rest_settings:
        rest_settings["EXCEPTION_HANDLER"] = (
            "selfhealing.api.django.exceptions.handler.selfhealing_exception_handler"
        )
    namespace["REST_FRAMEWORK"] = rest_settings
```

### 3.5 런타임 검증

```python
def _validate_prerequisites(namespace):
    """래퍼 호출 전에 필수 설정이 존재하는지 검증."""
    if "MIDDLEWARE" not in namespace:
        raise ImproperlyConfigured(
            "configure_selfhealing()은 MIDDLEWARE가 정의된 이후에 호출해야 합니다. "
            "settings.py의 맨 마지막에 배치하세요."
        )
    if "INSTALLED_APPS" not in namespace:
        raise ImproperlyConfigured(
            "configure_selfhealing()은 INSTALLED_APPS가 정의된 이후에 호출해야 합니다."
        )
```

---

## 4. Celery 시그널: 수동 1줄 유지 + 미등록 감지

### 4.1 Consumer celery.py (변경 없음)

```python
# consumer celery.py — 기존과 동일하게 1줄 유지
from selfhealing.adapters.celery import setup_selfhealing_signals
setup_selfhealing_signals(app=app, enabled=True, cb_enabled=True, ...)
```

### 4.2 미등록 감지 경고

Consumer가 `setup_selfhealing_signals()` 호출을 빼먹는 휴먼 에러를 방지하기 위해,
`AppConfig.ready()` 또는 첫 Celery 태스크 실행 시점에서 시그널 등록 여부를 검증한다.

```python
# apps.py ready()에 추가
def _warn_if_celery_signals_missing(self):
    """Celery 시그널이 등록되지 않았으면 경고 로그 발생."""
    try:
        from celery.signals import task_failure
        if not task_failure.receivers:
            logger.warning(
                "self_healing.celery_signals_not_registered",
                hint="Consumer celery.py에서 setup_selfhealing_signals(app=app)를 호출하세요.",
            )
    except ImportError:
        pass  # Celery 미설치 환경
```

### 4.3 CI 린터 룰

318 문서의 wiring verification AST 분석 패턴을 확장하여,
Consumer `celery.py`에 `setup_selfhealing_signals` 호출이 존재하는지 CI에서 검증한다.

---

## 5. OTEL: Gunicorn 훅으로 완전 위임

### 5.1 왜 ready()에서 OTEL을 초기화하면 안 되는가

`initialize_opentelemetry()`이 `BatchSpanProcessor`를 생성하면:
1. 마스터에서 gRPC 채널 + 백그라운드 export 스레드 생성
2. `fork()` 후 워커에서 해당 스레드 사망
3. Span 유실 + gRPC 채널 corruption → Deadlock 가능

### 5.2 해결: post_worker_init 훅 (322 문서)

```python
# consumer gunicorn.conf.py
def post_worker_init(worker):
    from selfhealing.server import post_worker_init_start
    post_worker_init_start(worker)  # OTEL 초기화 + 백그라운드 스레드 시작
```

### 5.3 워커 재시작 시 OTEL Teardown

워커가 죽고 재시작될 때 이전 OTEL 리소스를 정리해야 한다:

```python
# selfhealing/server.py — worker_exit_cleanup()에 추가
def _teardown_opentelemetry():
    """이전 OTEL 리소스 클린업."""
    try:
        from opentelemetry import trace, metrics
        tracer_provider = trace.get_tracer_provider()
        if hasattr(tracer_provider, "shutdown"):
            tracer_provider.shutdown()
        meter_provider = metrics.get_meter_provider()
        if hasattr(meter_provider, "shutdown"):
            meter_provider.shutdown()
    except Exception:
        pass
```

### 5.4 개발 서버 (manage.py runserver)

Gunicorn을 사용하지 않는 개발 환경에서는 `configure_selfhealing(disable_auto_otel=False)`로
래퍼 함수 내에서 OTEL을 초기화할 수 있다. `_should_start_background_threads()`와 동일한
Gunicorn 마스터 감지 가드를 적용한다.

---

## 6. Opt-out 설정 통합: AutoConfigSettings

### 6.1 Pydantic 설정 클래스

기존 50+ 설정 파일의 `SettingsConfigDict(env_prefix="SELFHEALING_...")` 패턴을 따른다.

```python
# selfhealing/settings/auto_config.py
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class AutoConfigSettings(BaseSettings):
    """configure_selfhealing() 래퍼의 동작을 제어하는 설정."""

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_AUTO_",
        env_file=None,
        extra="ignore",
        validate_default=True,
    )

    middleware: bool = True
    exception_handler: bool = True
    otel: bool = True
    celery_signal_warning: bool = True
```

### 6.2 환경변수 오버라이딩

12-Factor App 방법론을 충족하기 위해 환경변수로 투명하게 오버라이딩 가능:

```bash
# 환경변수로 OTEL 자동 초기화 비활성화
SELFHEALING_AUTO_OTEL=false

# 환경변수로 미들웨어 자동 삽입 비활성화
SELFHEALING_AUTO_MIDDLEWARE=false
```

---

## 7. Consumer 사용 예시

### 7.1 기본 사용 (99% 케이스)

```python
# settings.py
from selfhealing.adapters.django import configure_selfhealing

INSTALLED_APPS = ["selfhealing.adapters.django", ...]

MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]

REST_FRAMEWORK = {
    "DEFAULT_PAGINATION_CLASS": "...",
}

SELFHEALING_CORE_DOMAINS = ["payment", "order", "inventory"]
SELFHEALING_TASK_DOMAIN_MAPPING = {
    "shopping.tasks.payment_tasks.confirm_toss_payment": "payment",
}

# 맨 마지막에서 selfhealing이 MIDDLEWARE, REST_FRAMEWORK를 안전하게 래핑
configure_selfhealing(namespace=globals())
```

### 7.2 미들웨어 순서 커스텀 (1% 케이스)

Consumer가 SelfHealingMiddleware와 ActorContextMiddleware 사이에 사내 미들웨어를 넣어야 할 때:

```python
configure_selfhealing(
    namespace=globals(),
    early_group=[
        "selfhealing.audit.trace.trace_id_middleware",
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",
        "selfhealing.api.django.tiering.TieringMiddleware",
        "selfhealing.api.django.middleware.IPBanMiddleware",
        "selfhealing.api.django.middleware.SelfHealingMiddleware",
        "mycompany.security.InternalSecurityMiddleware",          # 사이에 삽입
        "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
    ],
    # post_auth_group, tail_group은 기본값 사용 → 생략
)
```

### 7.3 특정 기능 비활성화

```python
# 방법 1: 래퍼 파라미터 (권장)
configure_selfhealing(
    namespace=globals(),
    disable_auto_otel=True,  # OTEL은 Gunicorn 훅으로 지연
)

# 방법 2: 환경변수 (12-Factor)
# SELFHEALING_AUTO_OTEL=false

# 방법 3: 개별 미들웨어 토글 (기존 패턴 유지)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

### 7.4 하위 호환성

- Consumer가 MIDDLEWARE에 selfhealing 미들웨어를 이미 넣은 경우 → 중복 삽입 방지 (이미 존재 체크)
- Consumer가 EXCEPTION_HANDLER를 이미 설정한 경우 → 덮어쓰지 않음
- 기존 `setup_selfhealing_signals()` 수동 호출도 계속 동작 (중복 호출 방지 플래그)
- `configure_selfhealing()` 없이 기존 방식(수동 나열)도 계속 동작

---

## 8. AppConfig.ready() 수정

### 8.1 변경 요약

`ready()`에는 **Consumer 설정 변경 코드를 추가하지 않는다** (ADR 참조).
Celery 시그널 미등록 감지 경고만 추가한다.

```python
def ready(self):
    # === 기존 (변경 없음) ===
    post_migrate.connect(...)
    self._connect_session_signals()
    self._autodiscover_celery_tasks()
    self._log_env_snapshot()
    self._sync_hash_chain_on_startup()
    self._validate_startup_config()
    self._initialize_orphan_services()

    # === 신규 (320) ===
    self._warn_if_celery_signals_missing()  # Celery 시그널 미등록 감지

    # === 기존 (계속) ===
    if self._should_start_background_threads():
        self._start_all_background_threads()
    self._validate_secrets()
    self._register_jwt_blacklist_hook()
```

---

## 9. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | `configure_selfhealing(namespace=globals())` 호출 | MIDDLEWARE에 14개 selfhealing 미들웨어 자동 삽입 확인 |
| 2 | 미들웨어 그룹 순서 검증 | early → Django core → post_auth → tail 순서 보장 |
| 3 | Consumer 수동 설정과 자동 설정 공존 | 중복 미들웨어 없음 확인 |
| 4 | `early_group=` 커스텀 | Consumer 미들웨어가 지정 위치에 삽입됨 확인 |
| 5 | 토글 비활성화 | `SELFHEALING_TIERING_MIDDLEWARE_ENABLED=False` → 해당 미들웨어 미삽입 |
| 6 | EXCEPTION_HANDLER 자동 설정 | REST_FRAMEWORK에 자동 반영 확인 |
| 7 | EXCEPTION_HANDLER 수동 설정 우선 | Consumer가 이미 설정 → 덮어쓰지 않음 확인 |
| 8 | `configure_selfhealing()` 미호출 시 | 기존 수동 방식 그대로 동작 확인 (하위 호환) |
| 9 | MIDDLEWARE 미정의 시 에러 | `ImproperlyConfigured` 예외 발생 확인 |
| 10 | Celery 시그널 미등록 감지 | 경고 로그 `self_healing.celery_signals_not_registered` 발생 확인 |
| 11 | 환경변수 오버라이딩 | `SELFHEALING_AUTO_MIDDLEWARE=false` → 미들웨어 미삽입 확인 |
| 12 | OTEL 래퍼 내 초기화 (개발서버) | `disable_auto_otel=False` + 비-Gunicorn → OTEL 초기화 확인 |
| 13 | OTEL Gunicorn 마스터 가드 | Gunicorn 마스터에서 OTEL 초기화 건너뜀 확인 |

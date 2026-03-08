# 320. Django Auto-Configuration — Consumer 설정 최소화

> **Status**: Planning
> **Severity**: P1 (HIGH) — repo 분리 선행 조건
> **Target**: `selfhealing/adapters/django/apps.py` (SelfHealingConfig)
> **References**:
> - 319 — Repo Separation Overview (커플링 C1, C3, C5, C6)
> - 223 — Host App Decoupling (AppConfig 기반 확장)

---

## 1. 현황 및 문제

### 1.1 현재: Consumer가 수동으로 설정하는 것들

Consumer(`myproject/settings/base.py`)에서 selfhealing 미들웨어 10+개를 직접 MIDDLEWARE에 나열하고,
`myproject/celery.py`에서 시그널을 수동 설정하고,
`myproject/wsgi.py`에서 `get_config()`을 수동 호출한다.

**문제**: 새 consumer 앱을 만들 때 이 설정을 모두 복사해야 하며, 미들웨어 순서를 잘못 배치하면 동작이 깨진다.

### 1.2 목표: `INSTALLED_APPS` 1줄로 자동 동작

```python
# consumer settings.py — 이것만으로 미들웨어, 시그널, OTEL, 예외 핸들러 전부 자동
INSTALLED_APPS = ["selfhealing.adapters.django"]
```

---

## 2. 자동화 대상 (4개)

### 2.1 미들웨어 자동 삽입

**현재** (`myproject/settings/base.py`):
```python
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "selfhealing.audit.trace.trace_id_middleware",           # 수동
    "selfhealing.api.django.middleware.HealthBridgeMiddleware", # 수동
    "selfhealing.api.django.tiering.TieringMiddleware",      # 수동
    # ... 8개 더
    "django.middleware.security.SecurityMiddleware",
    # ... Django 코어 ...
    "selfhealing.api.django.audit_middleware.AuditMiddleware", # 수동 (마지막)
    "django_prometheus.middleware.PrometheusAfterMiddleware",
]
```

**목표**: AppConfig.ready()에서 자동 삽입

```python
# Consumer settings.py — selfhealing 미들웨어 없이 Django 코어만 나열
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
```

**구현**: `apps.py` ready()에서 MIDDLEWARE 리스트에 자동 삽입

```python
# selfhealing/adapters/django/auto_config.py (신규)

# 미들웨어 정의: (dotted_path, position, setting_toggle)
SELFHEALING_MIDDLEWARE = [
    # (미들웨어 경로, 삽입 위치, 활성화 설정명)
    # position: "after:ClassName" 또는 "before:ClassName" 또는 "first" 또는 "last"
    ("selfhealing.audit.trace.trace_id_middleware", "first", None),
    ("selfhealing.api.django.middleware.HealthBridgeMiddleware", "after:trace_id_middleware", None),
    ("selfhealing.api.django.tiering.TieringMiddleware", "after:HealthBridgeMiddleware", "SELFHEALING_TIERING_MIDDLEWARE_ENABLED"),
    ("selfhealing.api.django.middleware.IPBanMiddleware", "after:TieringMiddleware", None),
    ("selfhealing.api.django.middleware.SelfHealingMiddleware", "after:IPBanMiddleware", None),
    ("selfhealing.api.django.middleware.actor_context.ActorContextMiddleware", "after:SelfHealingMiddleware", "SELFHEALING_ACTOR_MIDDLEWARE_ENABLED"),
    ("selfhealing.api.django.cell.middleware.CellTaggingMiddleware", "after:AuthenticationMiddleware", "SELFHEALING_CELL_TAGGING_ENABLED"),
    ("selfhealing.api.django.cell.middleware.BaggageSyncMiddleware", "after:CellTaggingMiddleware", None),
    ("selfhealing.api.django.rate_limit.HybridRateLimitMiddleware", "after:BaggageSyncMiddleware", None),
    ("selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware", "after:HybridRateLimitMiddleware", "SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED"),
    ("selfhealing.api.django.audit_middleware.AuditMiddleware", "last", "SELFHEALING_AUDIT_MIDDLEWARE_ENABLED"),
]


def inject_middleware(settings_module):
    """MIDDLEWARE 리스트에 selfhealing 미들웨어를 자동 삽입."""
    middleware = list(settings_module.MIDDLEWARE)

    for dotted_path, position, toggle_setting in SELFHEALING_MIDDLEWARE:
        # 이미 존재하면 건너뜀 (consumer가 수동으로 넣은 경우)
        if dotted_path in middleware:
            continue

        # 토글 설정 확인
        if toggle_setting and not getattr(settings_module, toggle_setting, True):
            continue

        # 삽입 위치 결정
        idx = _resolve_position(middleware, position)
        middleware.insert(idx, dotted_path)

    settings_module.MIDDLEWARE = middleware


def _resolve_position(middleware, position):
    """삽입 위치 해석."""
    if position == "first":
        return 0
    if position == "last":
        return len(middleware)
    if position.startswith("after:"):
        target = position[6:]
        for i, m in enumerate(middleware):
            if target in m:
                return i + 1
        return len(middleware)  # 대상 못 찾으면 마지막에 삽입
    if position.startswith("before:"):
        target = position[7:]
        for i, m in enumerate(middleware):
            if target in m:
                return i
        return 0
    return len(middleware)
```

### 2.2 Celery 시그널 자동 등록

**현재** (`myproject/celery.py`):
```python
from selfhealing.adapters.celery import setup_selfhealing_signals
setup_selfhealing_signals(app=app, enabled=True, cb_enabled=True, ...)
```

**목표**: AppConfig.ready()에서 자동 호출

```python
# apps.py ready()에 추가
def _setup_celery_signals(self):
    """Celery 시그널 자동 등록 (consumer가 수동 호출 불필요)."""
    try:
        from celery import current_app
        from selfhealing.adapters.celery import setup_selfhealing_signals

        setup_selfhealing_signals(
            app=current_app,
            enabled=True,
            cb_enabled=True,
            dlq_enabled=True,
            metrics_enabled=True,
            forensics_enabled=True,
            # task_domain_mapping은 Django settings에서 가져옴
            task_domain_mapping=getattr(
                settings, "SELFHEALING_TASK_DOMAIN_MAPPING", {}
            ),
        )
    except ImportError:
        pass
```

**Consumer 설정**:
```python
# settings.py — 도메인 매핑만 정의 (비즈니스 로직이므로 consumer 영역)
SELFHEALING_TASK_DOMAIN_MAPPING = {
    "shopping.tasks.payment_tasks.confirm_toss_payment": "payment",
    "shopping.tasks.order_tasks.process_order": "order",
}
```

### 2.3 EXCEPTION_HANDLER 자동 설정

**현재** (`myproject/settings/base.py`):
```python
REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "selfhealing.api.django.exceptions.handler.selfhealing_exception_handler",
}
```

**목표**: AppConfig.ready()에서 자동 설정

```python
def _setup_exception_handler(self):
    """DRF EXCEPTION_HANDLER 자동 설정."""
    try:
        rest_settings = getattr(settings, "REST_FRAMEWORK", {})
        # Consumer가 이미 설정했으면 건너뜀
        if "EXCEPTION_HANDLER" not in rest_settings:
            rest_settings["EXCEPTION_HANDLER"] = (
                "selfhealing.api.django.exceptions.handler.selfhealing_exception_handler"
            )
            settings.REST_FRAMEWORK = rest_settings
    except Exception:
        pass
```

### 2.4 OTEL 자동 초기화

**현재** (`myproject/settings/base.py`):
```python
from selfhealing.observability import initialize_opentelemetry
_otel_initialized = initialize_opentelemetry()
```

**목표**: AppConfig.ready()에서 자동 처리 (이미 이런 패턴 없으므로 추가)

```python
def _setup_observability(self):
    """OpenTelemetry 자동 초기화."""
    try:
        from selfhealing.observability import initialize_opentelemetry
        initialize_opentelemetry()
    except ImportError:
        pass
```

---

## 3. AppConfig.ready() 수정

### 3.1 변경 요약

`SelfHealingConfig.ready()`에 다음 4개 메서드 호출 추가:

```python
def ready(self):
    # === 기존 ===
    post_migrate.connect(...)
    self._connect_session_signals()
    self._autodiscover_celery_tasks()
    self._log_env_snapshot()
    self._sync_hash_chain_on_startup()
    self._validate_startup_config()
    self._initialize_orphan_services()

    # === 신규 (320) ===
    self._inject_middleware()       # C1: 미들웨어 자동 삽입
    self._setup_celery_signals()    # C3: Celery 시그널 자동 등록
    self._setup_exception_handler() # C5: EXCEPTION_HANDLER 자동 설정
    self._setup_observability()     # C6: OTEL 자동 초기화

    # === 기존 (계속) ===
    if self._should_start_background_threads():
        self._start_all_background_threads()
    self._validate_secrets()
    self._register_jwt_blacklist_hook()
```

### 3.2 Opt-out 메커니즘

Consumer가 자동 설정을 비활성화하고 싶을 때:

```python
# settings.py
SELFHEALING_AUTO_MIDDLEWARE = False    # 미들웨어 자동 삽입 비활성화
SELFHEALING_AUTO_CELERY_SIGNALS = False  # Celery 시그널 자동 등록 비활성화
SELFHEALING_AUTO_EXCEPTION_HANDLER = False  # EXCEPTION_HANDLER 자동 설정 비활성화
SELFHEALING_AUTO_OTEL = False         # OTEL 자동 초기화 비활성화
```

---

## 4. Consumer 마이그레이션 가이드

### 4.1 최소 설정 (분리 후)

```python
# settings.py
INSTALLED_APPS = [
    "selfhealing.adapters.django",  # 이 1줄로 모든 자동 설정 활성화
]

# 비즈니스 도메인 설정 (consumer 영역)
SELFHEALING_CORE_DOMAINS = ["payment", "order", "inventory"]
SELF_HEALING_DLQ_ELIGIBLE_PATHS = [r"^/api/orders/", r"^/api/payments/"]
SELF_HEALING_DOMAIN_MAPPING = {"/payments/": "payment", "/orders/": "order"}
SELFHEALING_TASK_DOMAIN_MAPPING = {
    "shopping.tasks.payment_tasks.confirm_toss_payment": "payment",
}

# 특정 기능 비활성화 (선택사항)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

### 4.2 하위 호환성

- Consumer가 MIDDLEWARE에 selfhealing 미들웨어를 이미 넣은 경우 → 중복 삽입 방지 (이미 존재 체크)
- Consumer가 EXCEPTION_HANDLER를 이미 설정한 경우 → 덮어쓰지 않음
- 기존 `setup_selfhealing_signals()` 수동 호출도 계속 동작 (중복 호출 방지 플래그)

---

## 5. 테스트 계획

| # | 테스트 | 검증 |
|---|--------|------|
| 1 | INSTALLED_APPS만으로 미들웨어 자동 삽입 | MIDDLEWARE에 10+개 selfhealing 미들웨어 존재 확인 |
| 2 | 미들웨어 순서 검증 | trace_id → HealthBridge → ... → Audit 순서 보장 |
| 3 | Consumer 수동 설정과 자동 설정 공존 | 중복 미들웨어 없음 확인 |
| 4 | SELFHEALING_AUTO_MIDDLEWARE=False | 자동 삽입 비활성화 확인 |
| 5 | Celery 시그널 자동 등록 | consumer celery.py에서 수동 호출 제거 후 동작 확인 |
| 6 | EXCEPTION_HANDLER 자동 설정 | REST_FRAMEWORK 설정 자동 반영 확인 |
| 7 | OTEL 자동 초기화 | settings에서 수동 import 제거 후 동작 확인 |

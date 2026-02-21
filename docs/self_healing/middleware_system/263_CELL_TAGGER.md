# 263. Cell Tagger — Django 미들웨어 및 Celery 태스크 태깅

> **Version**: 2.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Implemented
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/tagger.py`, `context/cell_context.py`, `api/django/cell/middleware.py`

---

## 0. 요약

`CellTagger`는 들어오는 요청/태스크에 `cell_id`를 태깅하여, 이후 `TrafficGate`, `BulkheadRegistry`, `CellHealthAggregator`가 Cell 단위로 동작할 수 있게 한다.

**진입점**:
1. **Django 미들웨어**: HTTP 요청에 `request.cell_id` 어트리뷰트 + `ContextVar` 추가
2. **Celery 태스크**: 태스크 헤더에 `cell_id` 삽입 (하이브리드 전파)

**태깅 키**: `tenant_id`, `user_id`, `session_id`, 또는 `client_ip` (우선순위 순)

> **v2.0.0 변경**: 리뷰 반영으로 미들웨어 배치 순서 수정, Celery 하이브리드 전파,
> ContextVar 기반 전역 전파, Fallback 분산 할당, AdmissionControlMiddleware 연동 전략 추가.

---

## 1. 설계 근거 — 기존 미들웨어 패턴

### 1.1 기존 미들웨어 토글 패턴

**`TieringMiddleware`** (`api/django/tiering/middleware.py` L18–L35):

```python
class TieringMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._check_enabled():
            return self.get_response(request)
        # ... 로직
```

→ `_check_enabled()`로 토글 비활성 시 즉시 패스스루.

### 1.2 기존 요청 컨텍스트 패턴

**`ActorContextMiddleware`** (`api/django/middleware/actor_context.py` L44) — 요청에 `actor_id`, `actor_type` 어트리뷰트 추가하는 기존 미들웨어.

`CellTaggingMiddleware`도 동일 패턴으로 `request.cell_id`를 추가한다.

### 1.3 기존 ContextVar 전파 패턴

코드베이스에 이미 확립된 `ContextVar` 패턴을 따른다:

| 모듈 | ContextVar | 타입 | 복원 방식 | 참조 |
|------|-----------|------|----------|------|
| `context/actor_context.py` L53 | `_current_actor` | `Actor \| None` | `token.reset()` | Actor 추적 |
| `context/causation_context.py` L226 | `_current_causation` | `CausationInfo \| None` | `token.reset()` | 인과관계 |
| `scaling/deadline_context.py` L51 | `_request_deadline` | `float \| None` | `set(previous)` | 요청 만료 |
| `decorators/domain_tag.py` L50 | `_current_domain` | `str \| None` | `token.reset()` | 도메인 태그 |

`CellTagger`도 동일 패턴으로 `_current_cell_id: ContextVar[str | None]`을 추가한다.

---

## 2. Cell Context — ContextVar 전역 전파

> **설계 결정**: `request.cell_id` 어트리뷰트만으로는 Django 미들웨어 체인 외부
> (뷰 서비스 레이어, Celery 태스크 발행 시점 등)에서 cell_id에 접근할 수 없다.
> 기존 `_current_actor`, `_current_causation`, `_request_deadline` 패턴과 동일하게
> `contextvars.ContextVar`를 사용하여 어디서든 `cell_id`에 접근 가능하게 한다.
>
> 이 결정은 3번 리뷰 — "TieringMiddleware 수정 없이 TrafficGate에 cell_id를 전달하는 방법"과
> 4.1절 Celery Context Propagation의 핵심 기반이다.

```python
"""
Cell Context — cell_id ContextVar 전역 전파.

CellTaggingMiddleware에서 설정하며,
미들웨어 체인 외부(서비스 레이어, Celery 발행 시점 등)에서도
cell_id에 접근 가능하게 한다.

기존 패턴 참조:
- context/actor_context.py L53: _current_actor ContextVar
- context/causation_context.py L226: _current_causation ContextVar
- scaling/deadline_context.py L51: _request_deadline ContextVar
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Generator

_current_cell_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "selfhealing_cell_id", default=None
)


def get_current_cell_id() -> str | None:
    """현재 컨텍스트의 cell_id 반환."""
    return _current_cell_id.get()


def set_cell_id(cell_id: str) -> contextvars.Token[str | None]:
    """cell_id 설정. 반환된 Token으로 복원 필요."""
    return _current_cell_id.set(cell_id)


@contextmanager
def cell_scope(cell_id: str) -> Generator[str, None, None]:
    """
    cell_id Context Manager.

    사용 예:
        with cell_scope("cell-3"):
            # 이 블록 내에서 get_current_cell_id() == "cell-3"
            task.delay(...)  # before_task_publish에서 cell_id 자동 전파
    """
    token = _current_cell_id.set(cell_id)
    try:
        yield cell_id
    finally:
        _current_cell_id.reset(token)
```

---

## 3. CellTagger — 코어 태깅 로직

```python
"""
Cell Tagger — 요청/태스크에 cell_id 태깅.

CellRegistry.get_cell_for_key()를 사용하여
일관된 Cell 할당을 수행합니다.

태깅 키 우선순위:
1. tenant_id (멀티테넌트 환경)
2. user_id (인증된 사용자)
3. session_id (세션 기반)
4. client_ip (최후의 수단)
5. trace_id (Fallback — 분산 해시 기반 균등 분배)
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class CellTagger:
    """
    Cell Tagger — 요청 컨텍스트에서 cell_id를 결정.

    CellRegistry와 연동하여 Consistent Hash 기반
    Cell 할당을 수행합니다.
    """

    # 태깅 키 우선순위 (높은 순)
    TAG_KEY_PRIORITY = [
        "tenant_id",
        "user_id",
        "session_id",
        "client_ip",
    ]

    def __init__(self):
        self._cell_registry = None

    def _get_registry(self):
        """CellRegistry 지연 로딩."""
        if self._cell_registry is None:
            from selfhealing.services.cell_topology import get_cell_registry
            self._cell_registry = get_cell_registry()
        return self._cell_registry

    def resolve_cell_id(self, context: dict[str, Any]) -> str:
        """
        컨텍스트에서 cell_id 결정.

        Args:
            context: 태깅 컨텍스트
                - tenant_id: 테넌트 식별자
                - user_id: 사용자 식별자
                - session_id: 세션 식별자
                - client_ip: 클라이언트 IP
                - trace_id: 분산 추적 ID (Fallback용)

        Returns:
            cell_id (예: "cell-3")
        """
        registry = self._get_registry()
        settings = registry._settings

        if not settings.enabled or not settings.tagging_enabled:
            return f"{settings.cell_prefix}-0"

        # 우선순위 순으로 태깅 키 탐색
        for key_name in self.TAG_KEY_PRIORITY:
            value = context.get(key_name)
            if value:
                return registry.get_cell_for_key(f"{key_name}:{value}")

        # ── Fallback: 분산 해시 기반 균등 분배 ──
        # cell-0 고정 할당 대신 Hash Ring을 재사용하여 ACTIVE Cell에 균등 분배.
        # trace_id가 있으면 사용 (동일 요청 재시도 시 동일 Cell 보장),
        # 없으면 monotonic_ns로 분산.
        # 이 방식은 CellRegistry.get_cell_for_key()의 DRAINING/ISOLATED
        # 건너뛰기 로직(registry.py L143-L155)을 그대로 활용하므로
        # 장애 Cell에는 Fallback 트래픽도 라우팅되지 않는다.
        trace_id = context.get("trace_id")
        fallback_key = f"fallback:{trace_id or time.monotonic_ns()}"
        return registry.get_cell_for_key(fallback_key)

    def resolve_cell_id_from_request(self, request: Any) -> str:
        """
        Django HttpRequest에서 cell_id 결정.

        Args:
            request: Django HttpRequest

        Returns:
            cell_id

        Note:
            request.user, request.session 접근을 위해
            반드시 AuthenticationMiddleware, SessionMiddleware
            이후에 실행되어야 한다. (3.1절 참조)
        """
        context: dict[str, Any] = {}

        # tenant_id (멀티테넌트 미들웨어에서 설정)
        tenant_id = getattr(request, "tenant_id", None)
        if tenant_id:
            context["tenant_id"] = str(tenant_id)

        # user_id (인증 미들웨어에서 설정)
        if hasattr(request, "user") and hasattr(request.user, "pk"):
            if request.user.pk:
                context["user_id"] = str(request.user.pk)

        # session_id
        session_key = getattr(request, "session", None)
        if session_key and hasattr(session_key, "session_key"):
            if session_key.session_key:
                context["session_id"] = session_key.session_key

        # client_ip
        context["client_ip"] = self._get_client_ip(request)

        # trace_id (Fallback용 — trace_id_middleware가 [1]에서 선행 설정)
        trace_id = getattr(request, "trace_id", None)
        if trace_id:
            context["trace_id"] = trace_id

        return self.resolve_cell_id(context)

    @staticmethod
    def _get_client_ip(request: Any) -> str:
        """클라이언트 IP 추출 (TieringMiddleware._get_client_ip와 동일 패턴)."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")
```

### 3.1 Fallback 분산 설계 근거

**문제**: 원래 설계는 모든 식별자 누락 시 `cell-0`으로 고정 할당했다.
인증 전/세션 없는 상태에서 `REMOTE_ADDR`까지 누락("unknown")되면,
모든 익명 트래픽이 `cell-0`에 집중되어 Thundering Herd 발생.

**해결**: `time.monotonic_ns()` 또는 `trace_id`를 Hash Ring에 전달하여 ACTIVE Cell에 균등 분배.

- **`trace_id` 사용 시 장점**: `trace_id_middleware`가 MIDDLEWARE `[1]`번 위치에서 선행 실행되므로
  (`settings/base.py` L92), CellTaggingMiddleware 실행 시점에는 `request.trace_id`가 항상 존재.
  동일 trace_id를 가진 재시도 요청은 동일 Cell로 라우팅되어 디버깅 일관성 확보.
- **`enabled=False` 일 때의 `cell-0`**: 이 경우는 "Cell Topology 자체가 꺼진 상태"이므로
  모든 요청을 단일 Cell로 모으는 것이 올바른 동작. 분산 Fallback은 `enabled=True` 상태에서만 적용.

---

## 4. Django CellTaggingMiddleware

```python
"""
Cell Tagging Django Middleware.

HTTP 요청에 cell_id 어트리뷰트를 추가하고,
ContextVar에 설정하여 서비스 레이어에서도 접근 가능하게 합니다.

활성화:
    SELFHEALING_CELL_TOPOLOGY_ENABLED=true
    SELFHEALING_CELL_TAGGING_ENABLED=true

MIDDLEWARE 설정:
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware"
    → AuthenticationMiddleware 이후, HybridRateLimitMiddleware 이전 배치
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class CellTaggingMiddleware:
    """
    요청에 cell_id를 태깅하는 Django 미들웨어.

    토글 패턴: TieringMiddleware와 동일
    - SELFHEALING_CELL_TOPOLOGY_ENABLED=false → 즉시 패스스루
    - SELFHEALING_CELL_TAGGING_ENABLED=false → 즉시 패스스루

    ContextVar 전파:
    - request.cell_id 어트리뷰트 + _current_cell_id ContextVar 동시 설정
    - 요청 종료 시 ContextVar 자동 복원 (token.reset)
    """

    def __init__(self, get_response: Any):
        self.get_response = get_response
        self._tagger = None

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not self._check_enabled():
            return self.get_response(request)

        tagger = self._get_tagger()
        cell_id = tagger.resolve_cell_id_from_request(request)

        # 요청에 cell_id 어트리뷰트 추가
        request.cell_id = cell_id  # type: ignore[attr-defined]

        # ContextVar 설정 — 서비스 레이어 및 Celery 발행 시점에서 접근 가능
        from selfhealing.context.cell_context import _current_cell_id
        token = _current_cell_id.set(cell_id)

        try:
            response = self.get_response(request)
        finally:
            # 요청 종료 시 ContextVar 복원 (actor_context.py 패턴)
            _current_cell_id.reset(token)

        # 응답 헤더에 Cell 정보 추가 (디버깅용)
        response["X-Cell-Id"] = cell_id

        return response

    def _check_enabled(self) -> bool:
        """토글 확인."""
        try:
            from django.conf import settings as django_settings

            if not getattr(django_settings, "SELFHEALING_CELL_TOPOLOGY_ENABLED", False):
                return False
            if not getattr(django_settings, "SELFHEALING_CELL_TAGGING_ENABLED", False):
                return False
            return True
        except Exception:
            return False

    def _get_tagger(self) -> Any:
        """CellTagger 지연 로딩."""
        if self._tagger is None:
            from selfhealing.services.cell_topology.tagger import CellTagger
            self._tagger = CellTagger()
        return self._tagger
```

### 4.1 미들웨어 배치 순서

> ⚠️ **CRITICAL**: `CellTaggingMiddleware`는 반드시 `AuthenticationMiddleware`와
> `SessionMiddleware` **이후**에 배치해야 한다. `request.user.pk`와
> `request.session.session_key`는 이 미들웨어들이 설정하기 때문이다.
>
> 현재 `settings/base.py`에서 `TieringMiddleware[3]`과
> `ActorContextMiddleware[5]`는 `AuthenticationMiddleware[6]` **이전**에 배치되어 있어
> `request.user`에 접근할 수 없다. CellTaggingMiddleware는 user_id/session_id 기반
> 태깅이 핵심이므로 반드시 `AuthenticationMiddleware` 이후여야 한다.

> ⚠️ **WARNING**: 멀티테넌트 식별 미들웨어(`request.tenant_id`를 설정하는)를
> **향후 도입할 경우**, 반드시 `CellTaggingMiddleware` **이전**에 배치해야 한다.
> `tenant_id`는 Cell 할당의 최우선 키(TAG_KEY_PRIORITY[0])이므로,
> 누락 시 user_id/session_id/IP 기반의 차선 할당이 적용되어
> 동일 테넌트 트래픽이 복수 Cell에 분산될 수 있다.
>
> **코드 근거**: 현재 코드베이스에 `request.tenant_id`를 설정하는 미들웨어는
> 존재하지 않는다. `tenant_id`는 Loki WORM 어댑터(`adapters/audit/worm_adapters.py` L70)의
> `X-Scope-OrgID` 헤더와 테스트 fixture에서만 사용된다.
> 이 프로젝트는 도메인 프리(domain-free) 셀프힐링 프레임워크이므로,
> 테넌트 미들웨어는 프레임워크 소비자(consumer)가 구현할 영역이다.

**현재 `settings/base.py` MIDDLEWARE 배열** 기반 배치:

```python
MIDDLEWARE = [
    # [0] PrometheusBeforeMiddleware
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    # [1] trace_id_middleware — request.trace_id 설정 (Fallback 해시 키)
    "selfhealing.audit.trace.trace_id_middleware",
    # [2] HealthBridgeMiddleware
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    # [3] TieringMiddleware — Emergency Mode Load Shedding
    "selfhealing.api.django.tiering.TieringMiddleware",
    # [3.5] IPBanMiddleware
    "selfhealing.api.django.middleware.IPBanMiddleware",
    # [4] SelfHealingMiddleware
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # [5] ActorContextMiddleware
    "selfhealing.api.django.middleware.actor_context.ActorContextMiddleware",
    # ── [6] Django Core ──
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",      # ← session 설정
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",    # ← user 설정
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    # ── [6.5] CellTaggingMiddleware (신규) ──
    # AuthenticationMiddleware + SessionMiddleware 이후 →
    # request.user.pk, request.session.session_key 사용 가능
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",
    # [7] HybridRateLimitMiddleware
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    # [8] PoolCircuitBreakerMiddleware
    # ...
    # [11] AuditMiddleware (마지막)
    # [12] PrometheusAfterMiddleware
]
```

**배치 근거**:
- `[1] trace_id_middleware`가 선행 → `request.trace_id`를 Fallback 키로 활용
- `[6] AuthenticationMiddleware` 이후 → `request.user.pk` 접근 가능
- `[6] SessionMiddleware` 이후 → `request.session.session_key` 접근 가능
- `[7] HybridRateLimitMiddleware` 이전 → Rate Limit에 Cell 컨텍스트 전파

---

## 5. Celery 태스크 태깅 — 하이브리드 전파

### 5.1 문제: task_name 해싱의 Noisy Neighbor

원래 설계(`registry.get_cell_for_key(f"task:{task_name}")`)는
태스크 이름 자체를 해시 키로 사용했다.

**치명적 결함**: `force_open_circuit_breaker` 태스크는 어떤 `service_name`이든
100% 동일한 Cell(예: `cell-3`)로만 할당된다. 외부 서비스 하나가 폭주하면
해당 Cell의 Bulkhead가 가득 차서 다른 서비스의 CB 태스크까지 차단.

**코드 근거** — selfhealing 내부 Celery 태스크 라우팅 키 현황:

| 태스크 | 라우팅 가능 kwargs | 파일 |
|--------|-------------------|------|
| `force_open_circuit_breaker` | `service_name`, `user_id` | `adapters/celery/tasks/circuit_breaker.py` L195 |
| `force_close_circuit_breaker` | `service_name`, `user_id` | `adapters/celery/tasks/circuit_breaker.py` L263 |
| `conditional_replay_on_circuit_close` | `service_name` | `adapters/celery/tasks/circuit_breaker.py` L24 |
| `process_individual_postmortem` | `service_name` | `adapters/celery/tasks/postmortem.py` L543 |
| `close_incident_group` | `namespace` | `adapters/celery/tasks/postmortem.py` L31 |
| `execute_recovery_step` | `namespace` | `services/coordination/recovery_tasks.py` L259 |
| `replay_batch_by_domain` | `domain` | `adapters/celery/tasks/dlq_replay.py` L92 |
| `replay_single_dlq_entry` | `actor_info` (dict) | `adapters/celery/tasks/dlq_replay.py` L23 |
| `collect_self_healing_metrics` | (없음 — cron) | `adapters/celery/tasks/monitoring.py` L30 |

> **참고**: 이 프로젝트는 도메인 프리(domain-free) 셀프힐링 프레임워크이다.
> 위 태스크 목록은 전부 `packages/selfhealing-python/` 내부의 인프라 태스크이며,
> 쇼핑몰(`shopping/`) 등 외부 도메인은 테스트베드일 뿐 프레임워크 코드가 아니다.
> kwargs 구조도 **전부 플랫한 기본 타입**(str, int, dict)이며,
> 중첩 DTO나 Pydantic 모델 직렬화 패턴은 존재하지 않는다.

### 5.2 해결: 하이브리드 3단계 전파

기존 `CausationContext` 전파 패턴(`context/celery_propagation.py`)과 동일한 구조를 사용한다:

```python
"""
Celery 태스크에 cell_id 태깅 — 하이브리드 3단계 전파.

전파 우선순위:
1. HTTP 컨텍스트의 cell_id 상속 (ContextVar)
2. kwargs에서 service_name/namespace/domain 추출
3. task_name Fallback (최후의 수단)

기존 전파 패턴 참조:
- context/celery_propagation.py: CausationContext before_task_publish 자동 주입
- adapters/celery/signal_hooks.py L381: on_before_task_publish 핸들러
"""

from __future__ import annotations

import logging
from typing import Any

from celery.signals import before_task_publish, task_prerun, task_postrun

logger = logging.getLogger(__name__)

# kwargs에서 추출할 라우팅 키 (우선순위 순)
CELERY_ROUTING_KEYS = [
    "service_name",   # CB, Postmortem 태스크
    "namespace",      # Recovery, Incident 태스크
    "domain",         # DLQ Replay 태스크
    "user_id",        # CB Force Open/Close 태스크
]


def _extract_routing_key(kwargs: dict[str, Any]) -> tuple[str, str] | None:
    """
    Celery kwargs에서 라우팅 키 추출.

    Args:
        kwargs: 태스크 kwargs

    Returns:
        (key_name, value) 또는 None

    Note:
        현재 코드베이스의 모든 Celery 태스크는 플랫한 1-depth kwargs를
        사용하므로 단순 dict.get()으로 충분하다.
        향후 중첩 구조 도입 시 이 함수 내부만 수정하면 된다.
    """
    for key in CELERY_ROUTING_KEYS:
        value = kwargs.get(key)
        if value is not None:
            return (key, str(value))
    return None


@before_task_publish.connect
def add_cell_id_to_task(
    sender: str | None = None,
    headers: dict | None = None,
    body: Any = None,
    **kwargs,
) -> None:
    """
    태스크 발행 시 cell_id 삽입 — 하이브리드 3단계.

    기존 on_before_task_publish (celery_propagation.py L57)와 동일한
    시그널 패턴. 이미 cell_id가 있으면 덮어쓰지 않음.
    """
    if headers is None:
        return

    # 이미 cell_id가 명시적으로 설정된 경우 skip
    if headers.get("cell_id"):
        return

    try:
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        settings = get_cell_topology_settings()
        if not settings.enabled or not settings.tagging_enabled:
            return

        # ── 1순위: HTTP 컨텍스트의 cell_id 상속 (ContextVar) ──
        from selfhealing.context.cell_context import get_current_cell_id

        current_cell = get_current_cell_id()
        if current_cell:
            headers["cell_id"] = current_cell
            return

        # ── 2순위: kwargs에서 라우팅 키 추출 ──
        from selfhealing.services.cell_topology import get_cell_registry

        registry = get_cell_registry()

        # body 구조: [args, kwargs, embed] (Celery 프로토콜 v2)
        task_kwargs: dict[str, Any] = {}
        if body and isinstance(body, (list, tuple)) and len(body) > 1:
            if isinstance(body[1], dict):
                task_kwargs = body[1]

        routing = _extract_routing_key(task_kwargs)
        if routing:
            key_name, value = routing
            headers["cell_id"] = registry.get_cell_for_key(f"{key_name}:{value}")
            return

        # ── 3순위: task_name Fallback ──
        task_name = headers.get("task", "unknown")
        headers["cell_id"] = registry.get_cell_for_key(f"task:{task_name}")

    except Exception:
        pass  # 태깅 실패 시 무시 — 기존 동작 유지 (Fail-Open)


@task_prerun.connect
def extract_cell_id_on_prerun(task=None, **kwargs) -> None:
    """
    태스크 실행 전 cell_id를 ContextVar에 설정.

    signal_hooks.py L460의 _setup_causation_context()와 동일 패턴.
    """
    try:
        if task and hasattr(task.request, "get"):
            cell_id = task.request.get("cell_id")
            if cell_id:
                from selfhealing.context.cell_context import _current_cell_id
                task._cell_id_token = _current_cell_id.set(cell_id)
    except Exception:
        pass


@task_postrun.connect
def clear_cell_id_on_postrun(task=None, **kwargs) -> None:
    """태스크 종료 후 ContextVar 정리."""
    try:
        token = getattr(task, "_cell_id_token", None)
        if token:
            from selfhealing.context.cell_context import _current_cell_id
            _current_cell_id.reset(token)
    except Exception:
        pass
```

### 5.3 하이브리드 전파 흐름도

```
[HTTP 요청 → CellTaggingMiddleware]
  → _current_cell_id.set("cell-3")
  → 뷰에서 force_open_circuit_breaker.delay(service_name="toss_api")
    → before_task_publish:
        1순위: _current_cell_id.get() == "cell-3" → headers["cell_id"] = "cell-3" ✓

[Celery Beat (cron) → check_circuit_breaker_recovery]
  → before_task_publish:
        1순위: _current_cell_id.get() == None
        2순위: kwargs == {} (인자 없음)
        3순위: task_name fallback → headers["cell_id"] = hash("task:...check_cb_recovery")

[Celery Beat → execute_recovery_step(session_id="s1", namespace="global")]
  → before_task_publish:
        1순위: _current_cell_id.get() == None
        2순위: kwargs["namespace"] = "global" → headers["cell_id"] = hash("namespace:global")

[Worker → task_prerun]
  → headers에서 cell_id 추출 → _current_cell_id.set(cell_id)
  → 태스크 내부에서 다른 태스크 발행 시 1순위로 cell_id 자동 상속
```

---

## 6. TrafficGate 연동 — AdmissionControlMiddleware 확장

### 6.1 기존 코드 분석

**`AdmissionControlMiddleware`** (`api/django/admission_control.py` L56)가
이미 `TrafficGate.should_allow(bulkhead_name=...)` 호출을 구현하고 있다:

```python
# admission_control.py L227-237 (현재 코드)
bulkhead_name = f"tier:{tier_id}"
decision = self._traffic_gate.should_allow(
    priority=traffic_priority,
    bulkhead_name=bulkhead_name,
    bulkhead_timeout=bulkhead_timeout,
    metadata={"tier_id": tier_id},
)
```

현재는 `bulkhead_name = f"tier:{tier_id}"`로 **티어 단위 격벽**(tier:critical, tier:standard 등)만 사용.

### 6.2 설계 결정: 별도 CellBulkheadMiddleware vs AdmissionControlMiddleware 확장

| 방안 | 장점 | 단점 |
|------|------|------|
| **A. 별도 CellBulkheadMiddleware** | OCP 준수, 기존 코드 무수정 | **이중 Bulkhead 획득** — 동일 요청이 Cell Bulkhead + Tier Bulkhead를 동시 점유 → 리소스 낭비, 릴리즈 타이밍 복잡 |
| **B. AdmissionControlMiddleware 확장** ✅ | 단일 Bulkhead 슬롯으로 Cell×Tier 격벽 구현, 기존 파이프라인 재사용 | AC 미들웨어 소폭 수정 (3줄) |

**선택: B** — `AdmissionControlMiddleware`의 `bulkhead_name` 결정 로직을 확장한다.

**근거**: 별도 미들웨어를 추가하면 요청 하나가 Bulkhead를 2개 점유하는 근본적 문제가 생긴다.
Cell Bulkhead는 통과했는데 Tier Bulkhead에서 거부 시 Cell Bulkhead를 어느 시점에 릴리즈할지,
Exception 발생 시 양쪽 모두 안전하게 릴리즈되는지 등 복잡도가 급격히 증가한다.
`AdmissionControlMiddleware`는 이미 Bulkhead 획득/릴리즈 생명주기를 완벽히 관리하고 있으므로,
`bulkhead_name`만 확장하면 추가 생명주기 관리 없이 Cell×Tier 격벽이 구현된다.

### 6.3 AdmissionControlMiddleware 확장 (3줄 변경)

```python
# admission_control.py L227 — 변경 전:
bulkhead_name = f"tier:{tier_id}"

# 변경 후:
from selfhealing.context.cell_context import get_current_cell_id

cell_id = get_current_cell_id()
bulkhead_name = f"cell:{cell_id}:tier:{tier_id}" if cell_id else f"tier:{tier_id}"
```

이 변경으로:
- `cell_id`가 없으면 (Cell Topology 비활성) → 기존 `tier:critical` 동작 유지
- `cell_id`가 있으면 → `cell:cell-3:tier:critical` — Cell×Tier 2차원 격벽 자동 적용
- `CellRegistry._register_cell_bulkheads()` (`registry.py` L286)가
  Cell별 Bulkhead를 자동 등록하므로 추가 설정 불필요

### 6.4 Cell별 트래픽 제어 흐름 (수정)

```
HTTP Request 도착
  → [1] trace_id_middleware: request.trace_id 설정
  → [6] AuthenticationMiddleware: request.user 설정
  → [6.5] CellTaggingMiddleware:
      cell_id = "cell-3" (user_id 기반 Consistent Hash)
      request.cell_id = "cell-3"
      _current_cell_id.set("cell-3")
  → [7+] AdmissionControlMiddleware (활성화 시):
      cell_id = get_current_cell_id()  # "cell-3"
      bulkhead_name = "cell:cell-3:tier:standard"
      TrafficGate.should_allow(
          priority=50,
          bulkhead_name="cell:cell-3:tier:standard",
      )
      → Stage 1: Bulkhead "cell:cell-3:tier:standard" 격리 확인
      → Stage 2: CascadeLoadShedding
      → Stage 3: RateController
      → TrafficDecision(allowed=True/False)
```

### 6.5 `TrafficGate.should_allow()` 시그니처 (변경 없음)

**파일**: `scaling/traffic_gate.py` L193

```python
def should_allow(
    self,
    priority: int = 0,
    bulkhead_name: str | None = None,   # ← "cell:cell-3:tier:standard" 전달
    metadata: dict[str, Any] | None = None,
    bulkhead_timeout: float | None = None,
) -> TrafficDecision:
```

---

## 7. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `context/cell_context.py` | 신규 생성 — `_current_cell_id` ContextVar | 필수 |
| `services/cell_topology/tagger.py` | 신규 생성 — CellTagger 코어 로직 | 필수 |
| `api/django/cell/__init__.py` | 신규 생성 | 필수 |
| `api/django/cell/middleware.py` | 신규 생성 — CellTaggingMiddleware | 필수 |
| `adapters/celery/signal_hooks.py` | `before_task_publish` 핸들러 추가 또는 별도 모듈 | 필수 |
| `api/django/admission_control.py` L227 | `bulkhead_name`에 `cell_id` prefix 추가 (3줄) | 선택 |

**기존 파일 변경 최소화**: `TrafficGate`, `BulkheadRegistry`, `TieringMiddleware`는 무수정.
`AdmissionControlMiddleware`만 `bulkhead_name` 결정 로직 3줄 확장.

---

## 8. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정) |
| `262_CELL_REGISTRY.md` | `get_cell_for_key()` 제공자 |
| `264_CELL_HEALTH.md` | 태깅된 cell_id로 메트릭 수집 |
| `265_CELL_EVACUATION_POLICY.md` | DRAINING/ISOLATED → Hash Ring 건너뛰기 연동 |
| `266_CELL_OTEL_PROPAGATION.md` | OTel Baggage를 통한 cell_id 통합 전파 (Phase 2) |
| `267_CELL_EXTERNAL_API_CONTEXT.md` | 외부 API 호출 시 cell_id 자동 inject (Phase 2) |
| `api/django/tiering/middleware.py` | 토글/미들웨어 패턴 참조 |
| `api/django/admission_control.py` | `bulkhead_name` 확장 대상 (6.3절) |
| `scaling/traffic_gate.py` | `bulkhead_name` 파라미터 사용 |
| `context/celery_propagation.py` | Celery `before_task_publish` 전파 패턴 참조 |

> **OTel Baggage 및 외부 API 컨텍스트 전파**는 이 문서의 범위가 아니며,
> `266_CELL_OTEL_PROPAGATION.md`와 `267_CELL_EXTERNAL_API_CONTEXT.md`에서 다룬다.
> 이는 누락이 아닌 의도적 Phase 분리이다.
> 263에서 구현하는 `_current_cell_id` ContextVar가 266/267의 OTel 확장 기반이 된다.

# 263. Cell Tagger — Django 미들웨어 및 Celery 태스크 태깅

> **Version**: 1.0.0
> **Created**: 2026-02-22
> **Updated**: 2026-02-22
> **Status**: Planned
> **Parent**: [261_CELL_TOPOLOGY_OVERVIEW.md](261_CELL_TOPOLOGY_OVERVIEW.md)
> **Implements**: `services/cell_topology/tagger.py`, `api/django/cell/middleware.py`

---

## 0. 요약

`CellTagger`는 들어오는 요청/태스크에 `cell_id`를 태깅하여, 이후 `TrafficGate`, `BulkheadRegistry`, `CellHealthAggregator`가 Cell 단위로 동작할 수 있게 한다.

**진입점**:
1. **Django 미들웨어**: HTTP 요청에 `request.cell_id` 어트리뷰트 추가
2. **Celery 태스크**: 태스크 헤더에 `cell_id` 삽입

**태깅 키**: `user_id`, `tenant_id`, `session_id`, 또는 `client_ip` (우선순위 순)

---

## 1. 설계 근거 — 기존 미들웨어 패턴

### 1.1 기존 미들웨어 토글 패턴

**`TieringMiddleware`** (`api/django/tiering/middleware.py` L20–L35):

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

**`ActorContextMiddleware`** — 요청에 `actor_id`, `actor_type` 어트리뷰트 추가하는 기존 미들웨어.

`CellTaggingMiddleware`도 동일 패턴으로 `request.cell_id`를 추가한다.

---

## 2. CellTagger — 코어 태깅 로직

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
"""

from __future__ import annotations

import logging
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

        # 모든 키가 없으면 기본 Cell
        return f"{settings.cell_prefix}-0"

    def resolve_cell_id_from_request(self, request: Any) -> str:
        """
        Django HttpRequest에서 cell_id 결정.

        Args:
            request: Django HttpRequest

        Returns:
            cell_id
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

        return self.resolve_cell_id(context)

    @staticmethod
    def _get_client_ip(request: Any) -> str:
        """클라이언트 IP 추출 (TieringMiddleware._get_client_ip와 동일 패턴)."""
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "unknown")
```

---

## 3. Django CellTaggingMiddleware

```python
"""
Cell Tagging Django Middleware.

HTTP 요청에 cell_id 어트리뷰트를 추가합니다.
이후 미들웨어/뷰에서 request.cell_id로 접근 가능.

활성화:
    SELFHEALING_CELL_TOPOLOGY_ENABLED=true
    SELFHEALING_CELL_TAGGING_ENABLED=true

MIDDLEWARE 설정:
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware"
    → TieringMiddleware 앞에 배치 (cell_id가 먼저 태깅되어야 함)
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

        response = self.get_response(request)

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

### 3.1 미들웨어 배치 순서

```python
MIDDLEWARE = [
    # ... Django 기본 미들웨어
    "selfhealing.api.django.cell.middleware.CellTaggingMiddleware",  # cell_id 태깅 (먼저)
    "selfhealing.api.django.tiering.middleware.TieringMiddleware",   # EmergencyMode + Backpressure
    "selfhealing.api.django.middleware.backpressure.BackpressureMiddleware",
    # ...
]
```

`CellTaggingMiddleware`가 먼저 실행되어 `request.cell_id`를 설정하면, 이후 `TieringMiddleware`가 Cell 단위 EmergencyLevel을 적용할 수 있다.

---

## 4. Celery 태스크 태깅

### 4.1 Celery Signal 연동

```python
"""
Celery 태스크에 cell_id 태깅.

before_task_publish 시그널에서 태스크 헤더에 cell_id를 삽입합니다.
worker_process_init에서 cell_id를 추출하여 태스크 컨텍스트에 설정합니다.
"""

from celery.signals import before_task_publish, task_prerun


@before_task_publish.connect
def add_cell_id_to_task(headers: dict, **kwargs) -> None:
    """태스크 발행 시 cell_id 삽입."""
    try:
        from selfhealing.settings.cell_topology import get_cell_topology_settings

        settings = get_cell_topology_settings()
        if not settings.enabled or not settings.tagging_enabled:
            return

        from selfhealing.services.cell_topology import get_cell_registry

        registry = get_cell_registry()

        # 태스크 이름으로 Cell 할당
        task_name = headers.get("task", "unknown")
        cell_id = registry.get_cell_for_key(f"task:{task_name}")
        headers["cell_id"] = cell_id

    except Exception:
        pass  # 태깅 실패 시 무시 (기존 동작 유지)


@task_prerun.connect
def extract_cell_id(task=None, **kwargs) -> None:
    """태스크 실행 전 cell_id를 태스크 컨텍스트에 설정."""
    try:
        if task and hasattr(task.request, "get"):
            cell_id = task.request.get("cell_id")
            if cell_id:
                task.request.cell_id = cell_id
    except Exception:
        pass
```

---

## 5. TrafficGate 연동

### 5.1 Cell별 트래픽 제어 흐름

```
HTTP Request 도착
  → CellTaggingMiddleware: cell_id = "cell-3"
  → TieringMiddleware (또는 뷰):
      TrafficGate.should_allow(
          priority=request_priority,
          bulkhead_name="cell-3",    ← cell_id를 bulkhead_name으로 전달
      )
      → Stage 1: Bulkhead "cell-3" 격리 확인
      → Stage 2: CascadeLoadShedding
      → Stage 3: RateController
      → TrafficDecision(allowed=True/False)
```

**기존 `TrafficGate.should_allow()` API 변경 없음** — `bulkhead_name` 파라미터에 `cell_id`를 전달하면 Cell 단위 격벽이 자동 적용된다.

### 5.2 `TrafficGate.should_allow()` 시그니처 (변경 없음)

**파일**: `scaling/traffic_gate.py` L193

```python
def should_allow(
    self,
    priority: int = 0,
    bulkhead_name: str | None = None,   # ← cell_id 전달
    metadata: dict[str, Any] | None = None,
    bulkhead_timeout: float | None = None,
) -> TrafficDecision:
```

---

## 6. 변경 범위

| 파일 | 변경 | 유형 |
|------|------|------|
| `services/cell_topology/tagger.py` | 신규 생성 | 필수 |
| `api/django/cell/__init__.py` | 신규 생성 | 필수 |
| `api/django/cell/middleware.py` | 신규 생성 | 필수 |

**기존 파일 변경 없음** — `TrafficGate`, `BulkheadRegistry`, `TieringMiddleware` 모두 기존 API 그대로 사용.

---

## 7. 관련 문서

| 문서 | 관계 |
|------|------|
| `261_CELL_TOPOLOGY_OVERVIEW.md` | 부모 (설정) |
| `262_CELL_REGISTRY.md` | `get_cell_for_key()` 제공자 |
| `264_CELL_HEALTH.md` | 태깅된 cell_id로 메트릭 수집 |
| `api/django/tiering/middleware.py` | 토글/미들웨어 패턴 참조 |
| `scaling/traffic_gate.py` | `bulkhead_name` 파라미터 사용 |

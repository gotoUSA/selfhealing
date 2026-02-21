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

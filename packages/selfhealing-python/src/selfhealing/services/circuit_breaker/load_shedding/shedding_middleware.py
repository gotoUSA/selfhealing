"""
Load Shedding Middleware.

요청 처리 전에 Load Shedding 정책을 적용하여 트래픽을 제한합니다.

Usage:
    middleware = LoadSheddingMiddleware(manager)

    # Django middleware처럼 사용
    def process_request(service_id, request):
        decision = middleware.process(service_id)
        if not decision.allow_request:
            return Response(status=503, detail=decision.reason)
        # 정상 처리
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker.load_shedding.manager import (
        LoadSheddingManager,
    )
    from selfhealing.services.circuit_breaker.load_shedding.shedding_models import (
        SheddingDecision,
    )

logger = logging.getLogger(__name__)


class LoadSheddingMiddleware:
    """
    Load Shedding Middleware.

    요청 처리 전에 Load Shedding 정책을 적용하여 트래픽을 제한합니다.
    """

    def __init__(
        self,
        manager: Optional["LoadSheddingManager"] = None,
        on_shed_callback: Optional[Callable[[str, "SheddingDecision"], None]] = None,
    ):
        """
        초기화.

        Args:
            manager: LoadSheddingManager 인스턴스 (없으면 싱글톤 사용)
            on_shed_callback: Shedding 발생 시 콜백 (메트릭, 로깅 등)
        """
        self._manager = manager
        self._on_shed_callback = on_shed_callback

    @property
    def manager(self) -> "LoadSheddingManager":
        """Manager 인스턴스."""
        if self._manager is None:
            from selfhealing.services.circuit_breaker.load_shedding import (
                get_load_shedding_manager,
            )

            self._manager = get_load_shedding_manager()
        return self._manager

    def process(self, service_id: str) -> "SheddingDecision":
        """
        요청에 대한 Shedding 결정.

        Args:
            service_id: 서비스 ID

        Returns:
            SheddingDecision: 허용 여부 및 상세 정보
        """
        decision = self.manager.should_allow_request(service_id)

        if decision.is_shed and self._on_shed_callback:
            try:
                self._on_shed_callback(service_id, decision)
            except Exception as e:
                logger.error(f"[LoadSheddingMiddleware] Callback failed: {e}")

        return decision

    def record_result(self, service_id: str, success: bool) -> None:
        """
        요청 결과 기록.

        Args:
            service_id: 서비스 ID
            success: 성공 여부
        """
        if success:
            self.manager.record_success(service_id)
        else:
            self.manager.record_failure(service_id)

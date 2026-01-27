"""
DLQ Service Base Class.

Provides the base DLQService class with initialization and common utilities.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import FailedOperationRepository
    from selfhealing.services.dlq_models import DLQConfig

logger = logging.getLogger(__name__)


class DLQServiceBase:
    """
    Base class for DLQ Service.

    Provides initialization and common utilities.
    """

    def __init__(
        self,
        config: DLQConfig | None = None,
        repository: FailedOperationRepository | None = None,
    ):
        """
        Initialize the DLQ service.

        Args:
            config: Optional configuration, loads from settings if None
            repository: Optional repository for DI, uses Django adapter if None
        """
        from selfhealing.services.dlq_models import DLQConfig

        self.config = config or DLQConfig.from_settings()
        self._repository = repository

    @property
    def repository(self) -> FailedOperationRepository:
        """Get the repository using ProviderRegistry (Redis by default)."""
        if self._repository is None:
            from selfhealing.factory import ProviderRegistry

            self._repository = ProviderRegistry.get_failed_operation_repo()
        return self._repository

    @property
    def is_enabled(self) -> bool:
        """Check if DLQ is enabled."""
        return self.config.enabled

    def _log_dlq_audit(
        self,
        action: str,
        dlq_id: int,
        domain: str,
        failure_type: str = "",
        error_message: str = "",
        success: bool = True,
        actor_id: str | None = None,
        request: Any = None,
    ) -> None:
        """
        DLQ 작업을 Audit 로그에 기록.

        하이브리드 로직:
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

        Args:
            action: 작업 유형 (store, replay, resolve 등)
            dlq_id: DLQ 엔트리 ID
            domain: 비즈니스 도메인
            failure_type: 실패 유형 (store 시)
            error_message: 에러 메시지
            success: 작업 성공 여부
            actor_id: 작업 실행자
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)
        """
        try:
            from selfhealing.services.audit_helpers import (
                log_dlq_replay_audit,
                log_dlq_store_audit,
            )

            if action == "store":
                log_dlq_store_audit(
                    dlq_id=dlq_id,
                    domain=domain,
                    failure_type=failure_type,
                    error_message=error_message,
                    request=request,  # 버퍼 패턴 지원
                )
            elif action == "replay":
                log_dlq_replay_audit(
                    dlq_id=dlq_id,
                    domain=domain,
                    success=success,
                    actor_id=actor_id,
                    error_message=error_message if not success else None,
                    request=request,  # 버퍼 패턴 지원
                )
        except Exception as e:
            # Audit logging should never break the main flow
            logger.debug(f"[DLQService] Audit logging skipped: {e}")

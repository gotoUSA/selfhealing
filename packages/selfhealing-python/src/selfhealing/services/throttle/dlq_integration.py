"""
Throttle DLQ 연계 모듈.

Throttle에서 요청이 거부될 때 DLQ에 저장하고,
CB CLOSE 시 자동으로 replay하는 기능을 제공합니다.

주요 기능:
- Throttle deny 시 DLQ 저장 옵션
- CB CLOSE 이벤트 수신 시 자동 replay
- 서비스별 DLQ replay 관리
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ThrottleDeniedRequest:
    """Throttle에서 거부된 요청 정보."""

    service_name: str
    request_key: str  # IP + user 조합
    request_data: dict[str, Any]
    denied_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = "rate_limit_exceeded"

    # DLQ 저장 후 할당됨
    dlq_entry_id: int | None = None


@dataclass
class ThrottleDLQConfig:
    """Throttle DLQ 연계 설정."""

    # DLQ 저장 활성화
    enabled: bool = True

    # DLQ 도메인 (기본: throttle)
    dlq_domain: str = "throttle"

    # CB CLOSE 시 자동 replay 활성화
    auto_replay_on_cb_close: bool = True

    # replay 배치 크기
    replay_batch_size: int = 50

    # replay 간격 (초)
    replay_interval_seconds: float = 5.0


class ThrottleDLQIntegration:
    """
    Throttle과 DLQ 간 연계를 관리.

    Throttle deny 발생 시 DLQ에 저장하고,
    CB CLOSE 이벤트 수신 시 자동으로 replay합니다.
    """

    def __init__(self, config: ThrottleDLQConfig | None = None):
        self.config = config or ThrottleDLQConfig()
        self._lock = threading.RLock()

        # 서비스별 pending 요청 카운트 (메트릭용)
        self._pending_counts: dict[str, int] = {}

        # DLQ 서비스 (lazy loading)
        self._dlq_service = None

    def _get_dlq_service(self):
        """DLQ 서비스 인스턴스 가져오기."""
        if self._dlq_service is None:
            try:
                from selfhealing.services.dlq_service import get_dlq_service

                self._dlq_service = get_dlq_service()
            except ImportError:
                logger.warning("[ThrottleDLQ] DLQ service not available")
        return self._dlq_service

    def store_denied_request(
        self,
        service_name: str,
        request_key: str,
        request_data: dict[str, Any],
        throttle_limit: int,
        current_count: int,
    ) -> ThrottleDeniedRequest | None:
        """
        Throttle deny된 요청을 DLQ에 저장.

        Args:
            service_name: 서비스 이름
            request_key: Rate limit 키 (IP + user 조합)
            request_data: 요청 데이터
            throttle_limit: 현재 throttle limit
            current_count: 현재 요청 수

        Returns:
            저장된 요청 정보 또는 None (비활성화/실패 시)
        """
        if not self.config.enabled:
            return None

        denied_request = ThrottleDeniedRequest(
            service_name=service_name,
            request_key=request_key,
            request_data=request_data,
        )

        dlq_service = self._get_dlq_service()
        if dlq_service is None:
            logger.debug("[ThrottleDLQ] DLQ service unavailable, skipping storage")
            return None

        try:
            result = dlq_service.store_failure(
                domain=self.config.dlq_domain,
                failure_type="throttle_denied",
                entity_type="request",
                entity_id=request_key,
                error_code="THROTTLE_RATE_LIMIT_EXCEEDED",
                error_message=f"Request denied by throttle for service '{service_name}'",
                snapshot_data={
                    "service_name": service_name,
                    "request_key": request_key,
                    "throttle_limit": throttle_limit,
                    "current_count": current_count,
                },
                request_data=request_data,
                metadata={
                    "denied_at": denied_request.denied_at.isoformat(),
                    "reason": denied_request.reason,
                },
                recommended_action="auto_replay",
            )

            if result.success:
                denied_request.dlq_entry_id = result.entry_id
                with self._lock:
                    self._pending_counts[service_name] = self._pending_counts.get(service_name, 0) + 1

                logger.info(
                    f"[ThrottleDLQ] Stored denied request: "
                    f"service={service_name}, key={request_key}, "
                    f"dlq_id={result.entry_id}"
                )
                return denied_request
            else:
                logger.warning(f"[ThrottleDLQ] Failed to store: {result.message}")
                return None

        except Exception as e:
            logger.error(f"[ThrottleDLQ] Store failed: {e}")
            return None

    def replay_denied_requests(
        self,
        service_name: str | None = None,
        batch_size: int | None = None,
    ) -> dict[str, Any]:
        """
        DLQ에 저장된 거부 요청들을 replay.

        Args:
            service_name: 특정 서비스만 replay (None이면 전체)
            batch_size: 배치 크기

        Returns:
            replay 결과 통계
        """
        if not self.config.enabled or not self.config.auto_replay_on_cb_close:
            return {"skipped": True, "reason": "auto_replay_disabled"}

        dlq_service = self._get_dlq_service()
        if dlq_service is None:
            return {"skipped": True, "reason": "dlq_service_unavailable"}

        batch_size = batch_size or self.config.replay_batch_size

        try:
            result = dlq_service.replay(
                domain=self.config.dlq_domain,
                batch_size=batch_size,
            )

            # 카운트 업데이트
            with self._lock:
                if service_name:
                    self._pending_counts[service_name] = max(0, self._pending_counts.get(service_name, 0) - result.success)
                else:
                    # 전체 replay 시 카운트 리셋
                    for svc in list(self._pending_counts.keys()):
                        self._pending_counts[svc] = 0

            logger.info(
                f"[ThrottleDLQ] Replay completed: "
                f"processed={result.processed}, success={result.success}, "
                f"failed={result.failed}"
            )

            return {
                "processed": result.processed,
                "success": result.success,
                "failed": result.failed,
                "errors": result.errors[:5],  # 최대 5개 에러만 반환
            }

        except Exception as e:
            logger.error(f"[ThrottleDLQ] Replay failed: {e}")
            return {"error": str(e)}

    def on_circuit_breaker_closed(self, service_name: str) -> dict[str, Any]:
        """
        CB CLOSE 이벤트 핸들러.

        CB가 닫히면 해당 서비스의 거부된 요청들을 자동 replay합니다.

        Args:
            service_name: 서비스 이름

        Returns:
            replay 결과
        """
        logger.info(f"[ThrottleDLQ] CB CLOSED for '{service_name}', " f"triggering replay")
        return self.replay_denied_requests(service_name=service_name)

    def get_pending_count(self, service_name: str) -> int:
        """서비스별 pending 요청 수 조회."""
        with self._lock:
            return self._pending_counts.get(service_name, 0)

    def get_all_pending_counts(self) -> dict[str, int]:
        """모든 서비스의 pending 요청 수 조회."""
        with self._lock:
            return self._pending_counts.copy()


# =============================================================================
# Singleton
# =============================================================================

_throttle_dlq_integration: ThrottleDLQIntegration | None = None
_dlq_lock = threading.Lock()


def get_throttle_dlq_integration(
    config: ThrottleDLQConfig | None = None,
) -> ThrottleDLQIntegration:
    """전역 ThrottleDLQIntegration 인스턴스."""
    global _throttle_dlq_integration

    if _throttle_dlq_integration is None:
        with _dlq_lock:
            if _throttle_dlq_integration is None:
                _throttle_dlq_integration = ThrottleDLQIntegration(config)

    return _throttle_dlq_integration


def reset_throttle_dlq_integration() -> None:
    """테스트용 리셋."""
    global _throttle_dlq_integration

    with _dlq_lock:
        _throttle_dlq_integration = None

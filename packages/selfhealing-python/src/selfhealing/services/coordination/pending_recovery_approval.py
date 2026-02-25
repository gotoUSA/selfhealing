"""
Pending Recovery Approval.

중요 네임스페이스에서 수동 승인을 요구하거나,
방치된 복구 대기 상태에 대해 알림을 발송합니다.

Features:
- 수동 승인 요청 관리
- 대기 중인 승인 목록 조회
- 승인/거부 처리
- 방치 알림 에스컬레이션

Code reference:
    governance.py#L404 (acknowledge_warning 패턴)
    chaos_scheduler.py#L193 (check_and_alert_pending_approvals 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.4
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


class RecoveryApprovalStatus(str, Enum):
    """
    복구 승인 상태.
    """

    PENDING = "pending"
    """승인 대기 중."""

    APPROVED = "approved"
    """승인됨."""

    REJECTED = "rejected"
    """거부됨."""

    EXPIRED = "expired"
    """만료됨 (자동 또는 타임아웃)."""

    ESCALATED = "escalated"
    """에스컬레이션됨 (상위 권한으로 전달)."""


@dataclass
class RecoveryApprovalRequest:
    """
    복구 승인 요청.

    수동 승인이 필요한 복구 세션에 대한 요청 정보.

    Code reference:
        governance.py#L404 (acknowledge_warning 패턴)
    """

    # 요청 정보
    request_id: str = ""
    """요청 고유 ID."""

    session_id: str = ""
    """복구 세션 ID."""

    namespace: str = ""
    """네임스페이스."""

    trigger_level: str = ""
    """복구 대상 Emergency 레벨."""

    # 상태
    status: RecoveryApprovalStatus = RecoveryApprovalStatus.PENDING
    """승인 상태."""

    # 시간
    requested_at: datetime | None = None
    """요청 시각."""

    timeout_minutes: int = 60
    """타임아웃 (분)."""

    expires_at: datetime | None = None
    """만료 시각."""

    # 승인/거부 정보
    approved_by: str | None = None
    """승인자 (사용자 ID 또는 역할)."""

    approved_at: datetime | None = None
    """승인 시각."""

    approval_reason: str = ""
    """승인/거부 사유."""

    # 알림
    reminder_count: int = 0
    """알림 발송 횟수."""

    last_reminder_at: datetime | None = None
    """마지막 알림 시각."""

    # 메타데이터
    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def __post_init__(self):
        """초기화 후 처리."""
        if not self.request_id:
            self.request_id = f"approval-{uuid.uuid4().hex[:12]}"

        if self.requested_at is None:
            self.requested_at = datetime.now(timezone.utc)

        if self.expires_at is None and self.requested_at:
            self.expires_at = self.requested_at + timedelta(minutes=self.timeout_minutes)

    def is_expired(self) -> bool:
        """만료 여부 확인."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

    def is_pending(self) -> bool:
        """대기 중 여부 확인."""
        return self.status == RecoveryApprovalStatus.PENDING

    def get_waiting_time_minutes(self) -> float:
        """대기 시간 (분) 계산."""
        if self.requested_at is None:
            return 0.0

        elapsed = datetime.now(timezone.utc) - self.requested_at
        return elapsed.total_seconds() / 60.0

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "namespace": self.namespace,
            "trigger_level": self.trigger_level,
            "status": self.status.value,
            "requested_at": (self.requested_at.isoformat() if self.requested_at else None),
            "timeout_minutes": self.timeout_minutes,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "approved_by": self.approved_by,
            "approved_at": self.approved_at.isoformat() if self.approved_at else None,
            "approval_reason": self.approval_reason,
            "reminder_count": self.reminder_count,
            "last_reminder_at": (self.last_reminder_at.isoformat() if self.last_reminder_at else None),
            "waiting_time_minutes": self.get_waiting_time_minutes(),
            "is_expired": self.is_expired(),
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryApprovalRequest:
        """딕셔너리에서 생성."""
        data = dict(data)

        if "status" in data and isinstance(data["status"], str):
            data["status"] = RecoveryApprovalStatus(data["status"])

        for dt_field in [
            "requested_at",
            "expires_at",
            "approved_at",
            "last_reminder_at",
        ]:
            if dt_field in data and isinstance(data[dt_field], str):
                data[dt_field] = datetime.fromisoformat(data[dt_field])

        # 불필요한 필드 제거
        data.pop("waiting_time_minutes", None)
        data.pop("is_expired", None)

        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class PendingRecoveryApprovalManager:
    """
    복구 승인 대기 관리자.

    수동 승인이 필요한 복구 요청을 관리하고,
    방치된 요청에 대해 알림을 발송합니다.

    Usage:
        manager = PendingRecoveryApprovalManager()

        # 승인 요청 생성
        request = manager.create_request(
            session_id="recovery-abc123",
            namespace="seoul",
            trigger_level="LEVEL_3",
            timeout_minutes=60,
        )

        # 대기 중인 요청 조회
        pending = manager.list_pending_requests()

        # 승인 처리
        manager.approve(
            request_id="approval-xyz",
            approved_by="admin@example.com",
            reason="Manual review completed",
        )

    Reference:
        77_RECOVERY_COORDINATOR.md#8.4
    """

    def __init__(
        self,
        notification_callback: Callable[[RecoveryApprovalRequest, str], None] | None = None,
        reminder_intervals_minutes: list[int] | None = None,
    ):
        """
        Args:
            notification_callback: 알림 발송 콜백 (request, message_type) -> None
            reminder_intervals_minutes: 리마인더 발송 간격 (분)
        """
        self._notification_callback = notification_callback
        self._reminder_intervals = reminder_intervals_minutes or [15, 30, 60]

        # 요청 저장소
        self._requests: dict[str, RecoveryApprovalRequest] = {}
        # session_id -> request_id 매핑
        self._session_to_request: dict[str, str] = {}

        self._lock = threading.RLock()

    def create_request(
        self,
        session_id: str,
        namespace: str,
        trigger_level: str,
        timeout_minutes: int = 60,
        metadata: dict[str, Any] | None = None,
    ) -> RecoveryApprovalRequest:
        """
        승인 요청 생성.

        Args:
            session_id: 복구 세션 ID
            namespace: 네임스페이스
            trigger_level: 복구 대상 Emergency 레벨
            timeout_minutes: 타임아웃 (분)
            metadata: 추가 메타데이터

        Returns:
            생성된 승인 요청

        Raises:
            ValueError: 이미 해당 세션에 대한 요청이 존재하는 경우
        """
        with self._lock:
            # 중복 확인
            if session_id in self._session_to_request:
                existing_id = self._session_to_request[session_id]
                existing = self._requests.get(existing_id)
                if existing and existing.is_pending():
                    raise ValueError(f"Approval request already exists for session: {session_id}")

            request = RecoveryApprovalRequest(
                session_id=session_id,
                namespace=namespace,
                trigger_level=trigger_level,
                timeout_minutes=timeout_minutes,
                metadata=metadata or {},
            )

            self._requests[request.request_id] = request
            self._session_to_request[session_id] = request.request_id

            logger.info(
                "pending_recovery_approval.created_request",
                request=request.request_id,
                session_id=session_id,
                namespace=namespace,
            )

            # 초기 알림 발송
            self._send_notification(request, "created")

            return request

    def approve(
        self,
        request_id: str,
        approved_by: str,
        reason: str = "",
    ) -> RecoveryApprovalRequest | None:
        """
        승인 처리.

        Args:
            request_id: 요청 ID
            approved_by: 승인자
            reason: 승인 사유

        Returns:
            업데이트된 요청 또는 None
        """
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                logger.warning(
                    "pending_recovery_approval.request_found",
                    request_id=request_id,
                )
                return None

            if not request.is_pending():
                logger.warning(
                    "pending_recovery_approval.request_pending",
                    request_id=request_id,
                    status=request.status.value,
                )
                return request

            request.status = RecoveryApprovalStatus.APPROVED
            request.approved_by = approved_by
            request.approved_at = datetime.now(timezone.utc)
            request.approval_reason = reason

            logger.info(
                "pending_recovery_approval.approved",
                request_id=request_id,
                approved_by=approved_by,
            )

            # 승인 알림
            self._send_notification(request, "approved")

            return request

    def reject(
        self,
        request_id: str,
        rejected_by: str,
        reason: str = "",
    ) -> RecoveryApprovalRequest | None:
        """
        거부 처리.

        Args:
            request_id: 요청 ID
            rejected_by: 거부자
            reason: 거부 사유

        Returns:
            업데이트된 요청 또는 None
        """
        with self._lock:
            request = self._requests.get(request_id)
            if not request:
                return None

            if not request.is_pending():
                return request

            request.status = RecoveryApprovalStatus.REJECTED
            request.approved_by = rejected_by
            request.approved_at = datetime.now(timezone.utc)
            request.approval_reason = reason

            logger.info(
                "pending_recovery_approval.rejected",
                request_id=request_id,
                rejected_by=rejected_by,
                reason=reason,
            )

            # 거부 알림
            self._send_notification(request, "rejected")

            return request

    def get_request(self, request_id: str) -> RecoveryApprovalRequest | None:
        """
        요청 조회.

        Args:
            request_id: 요청 ID

        Returns:
            요청 또는 None
        """
        with self._lock:
            return self._requests.get(request_id)

    def get_request_by_session(
        self,
        session_id: str,
    ) -> RecoveryApprovalRequest | None:
        """
        세션 ID로 요청 조회.

        Args:
            session_id: 복구 세션 ID

        Returns:
            요청 또는 None
        """
        with self._lock:
            request_id = self._session_to_request.get(session_id)
            if request_id:
                return self._requests.get(request_id)
            return None

    def get_request_by_session_or_pending(
        self,
        namespace: str,
    ) -> RecoveryApprovalRequest | None:
        """
        네임스페이스의 대기 중인 요청 조회.

        해당 네임스페이스에서 대기 중인 첫 번째 요청을 반환합니다.

        Args:
            namespace: 네임스페이스

        Returns:
            대기 중인 요청 또는 None
        """
        pending = self.list_pending_requests(namespace=namespace)
        return pending[0] if pending else None

    def list_pending_requests(
        self,
        namespace: str | None = None,
    ) -> list[RecoveryApprovalRequest]:
        """
        대기 중인 요청 목록 조회.

        Args:
            namespace: 필터링할 네임스페이스 (없으면 전체)

        Returns:
            대기 중인 요청 목록 (요청 시각 오름차순)
        """
        with self._lock:
            pending = [
                r for r in self._requests.values() if r.is_pending() and (namespace is None or r.namespace == namespace)
            ]
            # 오래된 순으로 정렬
            pending.sort(key=lambda r: r.requested_at or datetime.min.replace(tzinfo=timezone.utc))
            return pending

    def list_stale_requests(
        self,
        stale_threshold_minutes: int = 30,
    ) -> list[RecoveryApprovalRequest]:
        """
        방치된 요청 목록 조회.

        Args:
            stale_threshold_minutes: 방치 기준 시간 (분)

        Returns:
            방치된 요청 목록
        """
        with self._lock:
            threshold = timedelta(minutes=stale_threshold_minutes)
            now = datetime.now(timezone.utc)

            stale = [
                r for r in self._requests.values() if r.is_pending() and r.requested_at and (now - r.requested_at) >= threshold
            ]

            return stale

    def check_and_send_reminders(self) -> list[RecoveryApprovalRequest]:
        """
        방치된 요청에 대해 리마인더 발송.

        주기적으로 호출하여 대기 중인 요청에 알림을 발송합니다.

        Returns:
            리마인더가 발송된 요청 목록
        """
        with self._lock:
            reminded = []
            now = datetime.now(timezone.utc)

            for request in self._requests.values():
                if not request.is_pending():
                    continue

                if request.is_expired():
                    # 만료 처리
                    request.status = RecoveryApprovalStatus.EXPIRED
                    self._send_notification(request, "expired")
                    continue

                # 리마인더 발송 여부 확인
                if self._should_send_reminder(request, now):
                    request.reminder_count += 1
                    request.last_reminder_at = now
                    self._send_notification(request, "reminder")
                    reminded.append(request)

            return reminded

    def expire_old_requests(self) -> list[RecoveryApprovalRequest]:
        """
        만료된 요청 처리.

        Returns:
            만료 처리된 요청 목록
        """
        with self._lock:
            expired = []

            for request in self._requests.values():
                if request.is_pending() and request.is_expired():
                    request.status = RecoveryApprovalStatus.EXPIRED
                    self._send_notification(request, "expired")
                    expired.append(request)

                    logger.warning(
                        "pending_recovery_approval.expired",
                        request=request.request_id,
                    )

            return expired

    def cleanup_old_requests(
        self,
        max_age_hours: int | None = None,
    ) -> int:
        """
        오래된 요청 정리.

        Args:
            max_age_hours: 보관 기간 (시간). None이면 Settings에서 로드.

        Returns:
            정리된 요청 수
        """
        # Settings에서 기본값 로드
        if max_age_hours is None:
            try:
                from selfhealing.settings.cleanup import get_cleanup_settings

                _cleanup_settings = get_cleanup_settings()
                max_age_hours = _cleanup_settings.approval_cleanup_max_age_hours
            except Exception:
                max_age_hours = 24  # 폴백

        with self._lock:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
            to_remove = []

            for request_id, request in self._requests.items():
                if not request.is_pending():
                    completed_at = request.approved_at or request.requested_at
                    if completed_at and completed_at < cutoff:
                        to_remove.append(request_id)

            for request_id in to_remove:
                request = self._requests.pop(request_id)
                self._session_to_request.pop(request.session_id, None)

            if to_remove:
                logger.info(
                    "pending_recovery_approval.cleaned_up_old_requests",
                    count=len(to_remove),
                )

            return len(to_remove)

    def get_stats(self) -> dict[str, Any]:
        """
        통계 조회.

        Returns:
            통계 정보
        """
        with self._lock:
            pending = [r for r in self._requests.values() if r.is_pending()]
            approved = [r for r in self._requests.values() if r.status == RecoveryApprovalStatus.APPROVED]
            rejected = [r for r in self._requests.values() if r.status == RecoveryApprovalStatus.REJECTED]
            expired = [r for r in self._requests.values() if r.status == RecoveryApprovalStatus.EXPIRED]

            return {
                "total_requests": len(self._requests),
                "pending_count": len(pending),
                "approved_count": len(approved),
                "rejected_count": len(rejected),
                "expired_count": len(expired),
                "pending_by_namespace": self._group_by_namespace(pending),
                "oldest_pending_minutes": self._get_oldest_pending_minutes(pending),
            }

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _should_send_reminder(
        self,
        request: RecoveryApprovalRequest,
        now: datetime,
    ) -> bool:
        """리마인더 발송 여부 판단."""
        if not request.requested_at:
            return False

        elapsed_minutes = (now - request.requested_at).total_seconds() / 60.0

        # 다음 리마인더 간격 확인
        for idx, interval in enumerate(self._reminder_intervals):
            if request.reminder_count <= idx and elapsed_minutes >= interval:
                return True

        return False

    def _send_notification(
        self,
        request: RecoveryApprovalRequest,
        message_type: str,
    ) -> None:
        """알림 발송."""
        if self._notification_callback:
            try:
                self._notification_callback(request, message_type)
            except Exception as e:
                logger.exception(
                    "pending_recovery_approval.notification_failed",
                    error=e,
                )
        else:
            # 기본 로깅
            logger.info(
                "pending_recovery_approval.notification",
                message_type=message_type,
                request=request.request_id,
                namespace=request.namespace,
            )

    def _group_by_namespace(
        self,
        requests: list[RecoveryApprovalRequest],
    ) -> dict[str, int]:
        """네임스페이스별 그룹핑."""
        counts: dict[str, int] = {}
        for r in requests:
            counts[r.namespace] = counts.get(r.namespace, 0) + 1
        return counts

    def _get_oldest_pending_minutes(
        self,
        pending: list[RecoveryApprovalRequest],
    ) -> float:
        """가장 오래 대기 중인 요청의 대기 시간 (분)."""
        if not pending:
            return 0.0

        now = datetime.now(timezone.utc)
        oldest = min(r.requested_at or now for r in pending)

        return (now - oldest).total_seconds() / 60.0


# =============================================================================
# Singleton
# =============================================================================

_approval_manager: PendingRecoveryApprovalManager | None = None
_manager_lock = threading.Lock()


def get_pending_recovery_approval_manager() -> PendingRecoveryApprovalManager:
    """PendingRecoveryApprovalManager 싱글톤 반환."""
    global _approval_manager

    if _approval_manager is not None:
        return _approval_manager

    with _manager_lock:
        if _approval_manager is None:
            _approval_manager = PendingRecoveryApprovalManager()
        return _approval_manager


def reset_pending_recovery_approval_manager() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _approval_manager
    with _manager_lock:
        _approval_manager = None

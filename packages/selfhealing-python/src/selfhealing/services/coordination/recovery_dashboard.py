"""
Recovery Dashboard Service.

복구 프로세스 대시보드 데이터를 제공하는 서비스 레이어.

Features:
- 복구 상태 요약 정보 제공
- 리전별 상태 조회
- 대기 중인 승인 통계
- 사용 가능한 액션 목록

Code reference:
    dashboard_service.py (DashboardService 패턴)
    recovery_coordinator.py (RecoveryCoordinator)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.6
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from .enums import RecoveryStatus

if TYPE_CHECKING:
    from .pending_recovery_approval import PendingRecoveryApprovalManager
    from .recovery_circuit_breaker import RecoveryCircuitBreaker
    from .recovery_coordinator import RecoveryCoordinator
    from .regional_recovery_policy import RegionalRecoveryPolicyEngine


logger = structlog.get_logger()


# =============================================================================
# Settings Helpers
# =============================================================================


def _get_stale_threshold_minutes() -> int:
    """DashboardSettings에서 방치 기준 시간을 가져온다."""
    try:
        from selfhealing.settings.dashboard import get_dashboard_settings

        return get_dashboard_settings().stale_threshold_minutes
    except Exception:
        return 30  # fallback


def _get_max_regional_status() -> int:
    """DashboardSettings에서 최대 리전 표시 수를 가져온다."""
    try:
        from selfhealing.settings.dashboard import get_dashboard_settings

        return get_dashboard_settings().max_regional_status
    except Exception:
        return 5  # fallback


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class RecoverySessionProgress:
    """복구 세션 진행 상태."""

    percent: int = 0
    """완료 비율 (0-100)."""

    current_step: str | None = None
    """현재 진행 중인 단계 이름."""

    completed_steps: int = 0
    """완료된 단계 수."""

    total_steps: int = 0
    """전체 단계 수."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "percent": self.percent,
            "current_step": self.current_step,
            "completed_steps": self.completed_steps,
            "total_steps": self.total_steps,
        }


@dataclass
class ActiveSessionInfo:
    """활성 복구 세션 정보."""

    session_id: str = ""
    """세션 ID."""

    progress: RecoverySessionProgress = field(default_factory=RecoverySessionProgress)
    """진행 상태."""

    namespace: str = ""
    """네임스페이스."""

    started_at: str | None = None
    """시작 시각 (ISO format)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "session_id": self.session_id,
            "progress": self.progress.to_dict(),
            "namespace": self.namespace,
            "started_at": self.started_at,
        }


@dataclass
class PendingApprovalsInfo:
    """대기 중인 승인 정보."""

    count: int = 0
    """대기 중인 요청 수."""

    stale_count: int = 0
    """방치된 요청 수."""

    urgent: bool = False
    """긴급 여부 (방치된 요청 존재 시 True)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "count": self.count,
            "stale_count": self.stale_count,
            "urgent": self.urgent,
        }


@dataclass
class RecoveryStats:
    """복구 통계."""

    total_recoveries: int = 0
    """총 복구 요청 수."""

    approved: int = 0
    """승인된 수."""

    rejected: int = 0
    """거부된 수."""

    completed: int = 0
    """완료된 수."""

    aborted: int = 0
    """중단된 수."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "total_recoveries": self.total_recoveries,
            "approved": self.approved,
            "rejected": self.rejected,
            "completed": self.completed,
            "aborted": self.aborted,
        }


@dataclass
class RegionalStatusInfo:
    """리전별 상태 정보."""

    namespace: str = ""
    """네임스페이스."""

    circuit_breaker_state: str = "unknown"
    """회로 차단기 상태."""

    require_manual_approval: bool = False
    """수동 승인 필수 여부."""

    priority: int = 0
    """우선순위."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "namespace": self.namespace,
            "circuit_breaker_state": self.circuit_breaker_state,
            "require_manual_approval": self.require_manual_approval,
            "priority": self.priority,
        }


@dataclass
class RecoveryActionWidget:
    """사용 가능한 복구 액션."""

    action: str = ""
    """액션 ID."""

    label: str = ""
    """표시 레이블."""

    enabled: bool = True
    """활성화 여부."""

    urgent: bool = False
    """긴급 여부."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "action": self.action,
            "label": self.label,
            "enabled": self.enabled,
            "urgent": self.urgent,
        }


@dataclass
class RecoveryWidgetData:
    """복구 대시보드 위젯 데이터."""

    status: str = ""
    """현재 상태 값."""

    status_display: str = ""
    """상태 표시 문자열."""

    status_color: str = "gray"
    """상태 색상."""

    active_session: ActiveSessionInfo | None = None
    """활성 세션 정보."""

    pending_approvals: PendingApprovalsInfo = field(default_factory=PendingApprovalsInfo)
    """대기 중인 승인 정보."""

    stats: RecoveryStats = field(default_factory=RecoveryStats)
    """복구 통계."""

    regional_status: list[RegionalStatusInfo] = field(default_factory=list)
    """리전별 상태 목록."""

    actions: list[RecoveryActionWidget] = field(default_factory=list)
    """사용 가능한 액션 목록."""

    timestamp: str = ""
    """데이터 생성 시각."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "status": self.status,
            "status_display": self.status_display,
            "status_color": self.status_color,
            "active_session": (self.active_session.to_dict() if self.active_session else None),
            "pending_approvals": self.pending_approvals.to_dict(),
            "stats": self.stats.to_dict(),
            "regional_status": [r.to_dict() for r in self.regional_status],
            "actions": [a.to_dict() for a in self.actions],
            "timestamp": self.timestamp,
        }


# =============================================================================
# Status Display Helpers
# =============================================================================


# 상태별 표시 문자열
STATUS_DISPLAY_MAP: dict[RecoveryStatus, str] = {
    RecoveryStatus.NOT_STARTED: "대기",
    RecoveryStatus.IN_PROGRESS: "진행 중",
    RecoveryStatus.HEALTH_CHECK: "건강 확인 중",
    RecoveryStatus.READY_TO_RESTORE: "복구 대기",
    RecoveryStatus.COMPLETED: "완료",
    RecoveryStatus.FAILED: "실패",
    RecoveryStatus.ABORTED: "중단됨",
}

# 상태별 색상
STATUS_COLOR_MAP: dict[RecoveryStatus, str] = {
    RecoveryStatus.NOT_STARTED: "gray",
    RecoveryStatus.IN_PROGRESS: "yellow",
    RecoveryStatus.HEALTH_CHECK: "blue",
    RecoveryStatus.READY_TO_RESTORE: "orange",
    RecoveryStatus.COMPLETED: "green",
    RecoveryStatus.FAILED: "red",
    RecoveryStatus.ABORTED: "gray",
}


def get_status_display(status: RecoveryStatus) -> str:
    """상태 표시 문자열 반환."""
    return STATUS_DISPLAY_MAP.get(status, status.value)


def get_status_color(status: RecoveryStatus) -> str:
    """상태 색상 반환."""
    return STATUS_COLOR_MAP.get(status, "gray")


# =============================================================================
# Recovery Dashboard Service
# =============================================================================


class RecoveryDashboardService:
    """
    복구 대시보드 서비스.

    복구 프로세스 모니터링을 위한 대시보드 데이터를 제공합니다.

    Usage:
        service = get_recovery_dashboard_service()

        # 위젯 데이터 조회
        widget_data = service.get_widget_data(namespace="global")

        # 리전별 상태 조회
        regional = service.get_regional_status(limit=5)

    Reference:
        77_RECOVERY_COORDINATOR.md#8.6
    """

    # 하위 호환성용 레거시 상수
    DEFAULT_STALE_THRESHOLD_MINUTES = 30
    DEFAULT_MAX_REGIONAL_STATUS = 5

    def __init__(
        self,
        coordinator: RecoveryCoordinator | None = None,
        circuit_breaker: RecoveryCircuitBreaker | None = None,
        approval_manager: PendingRecoveryApprovalManager | None = None,
        policy_engine: RegionalRecoveryPolicyEngine | None = None,
        stale_threshold_minutes: int | None = None,
        max_regional_status: int | None = None,
    ):
        """
        Args:
            coordinator: RecoveryCoordinator 인스턴스
            circuit_breaker: RecoveryCircuitBreaker 인스턴스
            approval_manager: PendingRecoveryApprovalManager 인스턴스
            policy_engine: RegionalRecoveryPolicyEngine 인스턴스
            stale_threshold_minutes: 방치 기준 시간 (분). None이면 Settings에서 가져옴.
            max_regional_status: 최대 리전 표시 수. None이면 Settings에서 가져옴.
        """
        self._coordinator = coordinator
        self._circuit_breaker = circuit_breaker
        self._approval_manager = approval_manager
        self._policy_engine = policy_engine
        self._stale_threshold_minutes = (
            stale_threshold_minutes if stale_threshold_minutes is not None else _get_stale_threshold_minutes()
        )
        self._max_regional_status = max_regional_status if max_regional_status is not None else _get_max_regional_status()
        self._lock = threading.RLock()

    def _get_coordinator(self) -> RecoveryCoordinator:
        """RecoveryCoordinator 획득."""
        if self._coordinator is not None:
            return self._coordinator
        from .recovery_coordinator import get_recovery_coordinator

        return get_recovery_coordinator()

    def _get_circuit_breaker(self) -> RecoveryCircuitBreaker:
        """RecoveryCircuitBreaker 획득."""
        if self._circuit_breaker is not None:
            return self._circuit_breaker
        from .recovery_circuit_breaker import get_recovery_circuit_breaker

        return get_recovery_circuit_breaker()

    def _get_approval_manager(self) -> PendingRecoveryApprovalManager:
        """PendingRecoveryApprovalManager 획득."""
        if self._approval_manager is not None:
            return self._approval_manager
        from .pending_recovery_approval import get_pending_recovery_approval_manager

        return get_pending_recovery_approval_manager()

    def _get_policy_engine(self) -> RegionalRecoveryPolicyEngine:
        """RegionalRecoveryPolicyEngine 획득."""
        if self._policy_engine is not None:
            return self._policy_engine
        from .regional_recovery_policy import get_regional_recovery_policy_engine

        return get_regional_recovery_policy_engine()

    def get_widget_data(self, namespace: str = "global") -> RecoveryWidgetData:
        """
        대시보드 위젯 데이터 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            RecoveryWidgetData: 위젯 표시용 데이터
        """
        with self._lock:
            coordinator = self._get_coordinator()
            approval_manager = self._get_approval_manager()

            # 현재 상태
            current_status = self._get_current_status(namespace)

            # 활성 세션
            active_session = self._get_active_session_info(namespace)

            # 대기 중인 승인
            pending_info = self._get_pending_approvals_info()

            # 통계
            stats = self._get_recovery_stats()

            # 리전별 상태 (Settings에서 가져온 max_regional_status 사용)
            regional_status = self.get_regional_status(limit=self._max_regional_status)

            # 사용 가능한 액션
            actions = self._get_available_actions(
                status=current_status,
                has_active_session=(active_session is not None),
                pending_count=pending_info.count,
                stale_count=pending_info.stale_count,
            )

            return RecoveryWidgetData(
                status=current_status.value,
                status_display=get_status_display(current_status),
                status_color=get_status_color(current_status),
                active_session=active_session,
                pending_approvals=pending_info,
                stats=stats,
                regional_status=regional_status,
                actions=actions,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

    def get_regional_status(self, limit: int = 5) -> list[RegionalStatusInfo]:
        """
        리전별 상태 조회.

        우선순위 높은 순으로 정렬하여 반환합니다.

        Args:
            limit: 최대 반환 수

        Returns:
            리전별 상태 목록
        """
        policy_engine = self._get_policy_engine()
        circuit_breaker = self._get_circuit_breaker()

        namespaces = policy_engine.get_namespaces_by_priority()
        regional_status = []

        for ns in namespaces[:limit]:
            cb_status = circuit_breaker.get_status(ns)
            config = policy_engine.get_config(ns)
            regional_status.append(
                RegionalStatusInfo(
                    namespace=ns,
                    circuit_breaker_state=cb_status.get("state", "unknown"),
                    require_manual_approval=config.require_manual_approval,
                    priority=config.priority,
                )
            )

        return regional_status

    def get_recovery_summary(self) -> dict[str, Any]:
        """
        복구 시스템 전체 요약 조회.

        DashboardSummaryView 통합용 요약 데이터를 반환합니다.

        Returns:
            복구 시스템 요약 딕셔너리
        """
        with self._lock:
            # 전체 네임스페이스의 상태 확인
            policy_engine = self._get_policy_engine()
            approval_manager = self._get_approval_manager()

            pending = approval_manager.list_pending_requests()
            stale = approval_manager.list_stale_requests(stale_threshold_minutes=self._stale_threshold_minutes)
            stats = self._get_recovery_stats()

            # 활성 복구 세션 수 확인
            active_sessions = 0
            for ns in policy_engine.get_namespaces_by_priority():
                session = self._get_active_session_info(ns)
                if session:
                    active_sessions += 1

            return {
                "active_recovery_sessions": active_sessions,
                "pending_approvals": len(pending),
                "stale_approvals": len(stale),
                "has_urgent_approvals": len(stale) > 0,
                "total_recoveries": stats.total_recoveries,
                "completed_recoveries": stats.completed,
                "health_status": self._determine_recovery_health(
                    pending_count=len(pending),
                    stale_count=len(stale),
                    active_sessions=active_sessions,
                ),
            }

    def _get_current_status(self, namespace: str) -> RecoveryStatus:
        """현재 복구 상태 조회."""
        try:
            coordinator = self._get_coordinator()
            session = coordinator.get_active_session(namespace)
            if session:
                return session.status
            return RecoveryStatus.NOT_STARTED
        except Exception as e:
            logger.warning(
                "recovery_dashboard.failed_get_status",
                error=e,
            )
            return RecoveryStatus.NOT_STARTED

    def _get_active_session_info(self, namespace: str) -> ActiveSessionInfo | None:
        """활성 세션 정보 조회."""
        try:
            coordinator = self._get_coordinator()
            session = coordinator.get_active_session(namespace)
            if not session:
                return None

            # 진행 상태 계산
            completed = sum(1 for s in session.steps if hasattr(s, "status") and s.status == RecoveryStatus.COMPLETED)
            total = len(session.steps)
            current_step = None
            if session.current_step_index < total:
                current_step = session.steps[session.current_step_index]

            progress = RecoverySessionProgress(
                percent=int((completed / total) * 100) if total > 0 else 0,
                current_step=current_step.step_type.value if current_step else None,
                completed_steps=completed,
                total_steps=total,
            )

            return ActiveSessionInfo(
                session_id=session.id,
                progress=progress,
                namespace=session.namespace,
                started_at=(session.started_at if hasattr(session, "started_at") else None),
            )
        except Exception as e:
            logger.warning(
                "recovery_dashboard.failed_get_session",
                error=e,
            )
            return None

    def _get_pending_approvals_info(self) -> PendingApprovalsInfo:
        """대기 중인 승인 정보 조회."""
        try:
            approval_manager = self._get_approval_manager()
            pending = approval_manager.list_pending_requests()
            stale = approval_manager.list_stale_requests(stale_threshold_minutes=self._stale_threshold_minutes)

            return PendingApprovalsInfo(
                count=len(pending),
                stale_count=len(stale),
                urgent=len(stale) > 0,
            )
        except Exception as e:
            logger.warning(
                "recovery_dashboard.failed_get_approvals",
                error=e,
            )
            return PendingApprovalsInfo()

    def _get_recovery_stats(self) -> RecoveryStats:
        """복구 통계 조회."""
        try:
            approval_manager = self._get_approval_manager()
            stats = approval_manager.get_stats()

            return RecoveryStats(
                total_recoveries=stats.get("total_requests", 0),
                approved=stats.get("approved_count", 0),
                rejected=stats.get("rejected_count", 0),
                completed=stats.get("completed_count", 0),
                aborted=stats.get("aborted_count", 0),
            )
        except Exception as e:
            logger.warning(
                "recovery_dashboard.failed_get_stats",
                error=e,
            )
            return RecoveryStats()

    def _get_available_actions(
        self,
        status: RecoveryStatus,
        has_active_session: bool,
        pending_count: int,
        stale_count: int,
    ) -> list[RecoveryActionWidget]:
        """사용 가능한 액션 목록 생성."""
        actions = []

        # 복구 시작 가능 (세션 없을 때)
        if not has_active_session and status == RecoveryStatus.NOT_STARTED:
            actions.append(
                RecoveryActionWidget(
                    action="start_recovery",
                    label="복구 시작",
                    enabled=True,
                )
            )

        # 복구 중단 가능 (세션 있을 때)
        if has_active_session and status in (
            RecoveryStatus.IN_PROGRESS,
            RecoveryStatus.HEALTH_CHECK,
        ):
            actions.append(
                RecoveryActionWidget(
                    action="abort_recovery",
                    label="복구 중단",
                    enabled=True,
                )
            )

        # 승인 대기 중
        if pending_count > 0:
            actions.append(
                RecoveryActionWidget(
                    action="approve_recovery",
                    label=f"승인 대기 ({pending_count}건)",
                    enabled=True,
                    urgent=stale_count > 0,
                )
            )

        return actions

    def _determine_recovery_health(
        self,
        pending_count: int,
        stale_count: int,
        active_sessions: int,
    ) -> str:
        """
        복구 시스템 건강 상태 판단.

        Returns:
            "healthy": 정상
            "warning": 주의 필요 (방치된 승인 등)
            "critical": 긴급 조치 필요
        """
        if stale_count > 3:
            return "critical"
        if stale_count > 0 or pending_count > 5:
            return "warning"
        return "healthy"


# =============================================================================
# Singleton Instance
# =============================================================================


_recovery_dashboard_service: RecoveryDashboardService | None = None
_service_lock = threading.Lock()


def get_recovery_dashboard_service() -> RecoveryDashboardService:
    """
    RecoveryDashboardService 싱글톤 반환.

    Returns:
        RecoveryDashboardService 인스턴스
    """
    global _recovery_dashboard_service

    if _recovery_dashboard_service is not None:
        return _recovery_dashboard_service

    with _service_lock:
        if _recovery_dashboard_service is None:
            _recovery_dashboard_service = RecoveryDashboardService()
        return _recovery_dashboard_service


def reset_recovery_dashboard_service() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _recovery_dashboard_service
    with _service_lock:
        _recovery_dashboard_service = None

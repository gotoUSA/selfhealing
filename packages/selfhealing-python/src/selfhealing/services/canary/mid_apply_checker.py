"""
적용 도중 체크기 (Mid-Apply Check).

설정 변경을 클러스터별로 적용하면서 각 단계에서 Emergency 상태를 확인합니다.
인터락 발동 시 적용을 중단하고 이미 적용된 클러스터를 롤백합니다.

주요 기능:
- MidApplyCheckResult: 체크 결과
- MidApplyInterlockChecker: 클러스터별 적용 + 체크 로직

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.13
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from selfhealing.services.canary.interlock import (
        CanarySafetyInterlock,
    )

logger = structlog.get_logger()


@dataclass
class MidApplyCheckResult:
    """
    적용 도중 체크 결과.

    Attributes:
        should_continue: 계속 진행해야 하는지
        interlock_triggered: 인터락이 발동되었는지
        applied_clusters: 성공적으로 적용된 클러스터 목록
        remaining_clusters: 아직 적용되지 않은 클러스터 목록
        rollback_required: 롤백이 필요한지
        interlock_result: 인터락 체크 결과 (발동 시)
    """

    should_continue: bool
    """계속 진행해야 하는지."""

    interlock_triggered: bool
    """인터락이 발동되었는지."""

    applied_clusters: list[str] = field(default_factory=list)
    """성공적으로 적용된 클러스터 목록."""

    remaining_clusters: list[str] = field(default_factory=list)
    """아직 적용되지 않은 클러스터 목록."""

    rollback_required: bool = False
    """롤백이 필요한지."""

    interlock_result: Any | None = None
    """인터락 체크 결과 (발동 시)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        result = {
            "should_continue": self.should_continue,
            "interlock_triggered": self.interlock_triggered,
            "applied_clusters": self.applied_clusters,
            "remaining_clusters": self.remaining_clusters,
            "rollback_required": self.rollback_required,
        }

        if self.interlock_result:
            result["interlock_result"] = {
                "action": self.interlock_result.action.value,
                "reason": self.interlock_result.reason,
                "emergency_level": self.interlock_result.emergency_level,
            }

        return result


class MidApplyInterlockChecker:
    """
    적용 도중 인터락 체크기.

    클러스터별로 설정을 적용하면서 각 단계에서 Emergency 상태를 확인합니다.
    인터락 발동 시 적용을 중단하고 롤백을 수행합니다.

    Usage:
        checker = MidApplyInterlockChecker(safety_interlock=interlock)

        result = checker.apply_with_check(
            rollout_id="rollout-1",
            target_clusters=["cluster-1", "cluster-2", "cluster-3"],
            apply_fn=lambda cluster: apply_config(cluster),
            rollback_fn=lambda cluster: rollback_config(cluster),
        )

        if result.interlock_triggered:
            # 인터락 발동됨, 이미 롤백 처리됨
            notify_operator(result)
    """

    def __init__(
        self,
        safety_interlock: CanarySafetyInterlock,
        check_interval: int = 1,
    ):
        """
        MidApplyInterlockChecker 초기화.

        Args:
            safety_interlock: CanarySafetyInterlock 인스턴스
            check_interval: 체크 간격 (N개 클러스터마다 체크)
        """
        self._safety_interlock = safety_interlock
        self._check_interval = max(1, check_interval)

    def apply_with_check(
        self,
        rollout_id: str,
        target_clusters: list[str],
        apply_fn: Callable[[str], bool],
        rollback_fn: Callable[[str], bool] | None = None,
        namespace: str | None = None,
    ) -> MidApplyCheckResult:
        """
        클러스터별 적용 + 인터락 체크.

        Args:
            rollout_id: 롤아웃 ID
            target_clusters: 대상 클러스터 목록
            apply_fn: 클러스터에 설정 적용 함수 (cluster) -> success
            rollback_fn: 클러스터 롤백 함수 (cluster) -> success
            namespace: 네임스페이스

        Returns:
            적용 결과
        """
        applied_clusters: list[str] = []
        remaining_clusters = list(target_clusters)

        for i, cluster in enumerate(target_clusters):
            # 체크 간격에 따라 인터락 체크
            if i % self._check_interval == 0:
                interlock_result = self._safety_interlock.check(
                    operation="mid_apply",
                    rollout_id=rollout_id,
                    namespace=namespace,
                )

                # 인터락 발동 (진행 불가)
                if not interlock_result.allowed:
                    logger.warning(
                        "mid_apply_interlock_checker.interlock_triggered_cluster",
                        i=i,
                        interlock_result=interlock_result.action,
                        reason=interlock_result.reason,
                    )

                    # 이미 적용된 클러스터 롤백
                    rollback_required = len(applied_clusters) > 0
                    if rollback_required and rollback_fn:
                        for applied in applied_clusters:
                            try:
                                rollback_fn(applied)
                                logger.info(
                                    "mid_apply_interlock_checker.rolled_back",
                                    applied=applied,
                                )
                            except Exception as e:
                                logger.exception(
                                    "mid_apply_interlock_checker.rollback_failed",
                                    applied=applied,
                                    error=e,
                                )

                    return MidApplyCheckResult(
                        should_continue=False,
                        interlock_triggered=True,
                        applied_clusters=applied_clusters,
                        remaining_clusters=remaining_clusters,
                        rollback_required=rollback_required,
                        interlock_result=interlock_result,
                    )

            # 클러스터에 적용
            try:
                success = apply_fn(cluster)
                if success:
                    applied_clusters.append(cluster)
                    remaining_clusters.remove(cluster)
            except Exception as e:
                logger.exception(
                    "mid_apply_interlock_checker.apply_failed",
                    cluster=cluster,
                    error=e,
                )

        # 모든 클러스터 적용 완료
        return MidApplyCheckResult(
            should_continue=True,
            interlock_triggered=False,
            applied_clusters=applied_clusters,
            remaining_clusters=remaining_clusters,
            rollback_required=False,
        )


__all__ = [
    "MidApplyCheckResult",
    "MidApplyInterlockChecker",
]

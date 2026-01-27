"""
리전별 Interlock 정책.

멀티 리전 환경에서 특정 리전에만 Emergency가 발생했을 때
롤아웃을 어떻게 처리할지 결정하는 정책.

주요 기능:
- RegionalInterlockBehavior: 4가지 행동 유형 enum
- RegionalInterlockPolicy: 상황별 행동 결정 로직

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.9
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RegionalInterlockBehavior(str, Enum):
    """
    리전별 Interlock 행동 유형.

    글로벌 카나리 진행 중 특정 리전에 Emergency가 발생했을 때
    어떻게 대응할지 정의합니다.
    """

    PAUSE_ALL = "pause_all"
    """
    기본값 (안전 우선).

    한 리전이라도 Emergency면 전체 롤아웃 일시 중지.
    가장 보수적이지만 가장 안전한 옵션.
    """

    ROLLBACK_AFFECTED_ONLY = "rollback_affected_only"
    """
    영향 리전만 롤백.

    리전별 격리 배포일 때만 사용 가능.
    해당 리전의 previous_values로 복원, 다른 리전은 계속 진행.

    주의: 리전 간 설정 불일치가 발생할 수 있음.
    """

    HYBRID = "hybrid"
    """
    하이브리드 전략.

    영향 리전: 즉시 롤백
    나머지 리전: 일시 중지 (PAUSE)

    영향 리전의 복구를 기다린 후 수동으로 재개.
    """

    CONTINUE_HEALTHY = "continue_healthy"
    """
    건강한 리전만 계속 진행 (위험!).

    영향 리전을 롤백하고 나머지 리전은 프로모션 계속.
    설정 불일치 리스크가 있으므로 신중하게 사용.

    Warning: 리전 간 설정 드리프트 발생 가능.
    """


@dataclass
class RegionalInterlockPolicy:
    """
    리전별 Interlock 정책.

    멀티 리전 환경에서 특정 리전에만 Emergency가 발생했을 때
    롤아웃을 어떻게 처리할지 결정합니다.

    Attributes:
        default_behavior: 기본 행동 (안전 우선: PAUSE_ALL)
        allow_isolated_rollback: 격리 롤백 허용 여부
        require_manual_resume_after_regional_rollback: 리전 롤백 후 수동 재개 필수
        max_affected_regions_for_isolated_rollback: 격리 롤백 허용 최대 영향 리전 수
    """

    default_behavior: RegionalInterlockBehavior = RegionalInterlockBehavior.PAUSE_ALL
    """기본 행동 (안전 우선: PAUSE_ALL)."""

    allow_isolated_rollback: bool = False
    """
    격리 롤백 허용 여부.

    True면 ROLLBACK_AFFECTED_ONLY와 CONTINUE_HEALTHY 사용 가능.
    False면 항상 PAUSE_ALL 또는 HYBRID만 사용.
    """

    require_manual_resume_after_regional_rollback: bool = True
    """리전 롤백 후 수동 재개 필수 여부."""

    max_affected_regions_for_isolated_rollback: int = 1
    """격리 롤백 허용 최대 영향 리전 수."""

    def get_behavior_for_situation(
        self,
        affected_regions: list,
        total_regions: list,
        is_region_isolated_deployment: bool = False,
    ) -> RegionalInterlockBehavior:
        """
        상황에 맞는 행동 결정.

        Args:
            affected_regions: Emergency 상태인 리전 목록
            total_regions: 전체 배포 대상 리전 목록
            is_region_isolated_deployment: 리전별 격리 배포인지 여부

        Returns:
            적용할 행동
        """
        # 모든 리전이 영향받으면 무조건 PAUSE_ALL
        if len(affected_regions) >= len(total_regions):
            return RegionalInterlockBehavior.PAUSE_ALL

        # 영향 리전 없으면 변경 없음 (호출 안 되어야 하지만 방어코드)
        if not affected_regions:
            return RegionalInterlockBehavior.PAUSE_ALL

        # 격리 배포가 아니면 ROLLBACK_AFFECTED_ONLY, CONTINUE_HEALTHY 사용 불가
        if not is_region_isolated_deployment:
            if self.default_behavior in (
                RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
                RegionalInterlockBehavior.CONTINUE_HEALTHY,
            ):
                return RegionalInterlockBehavior.PAUSE_ALL

        # 영향 리전이 너무 많으면 PAUSE_ALL
        if len(affected_regions) > self.max_affected_regions_for_isolated_rollback:
            return RegionalInterlockBehavior.PAUSE_ALL

        # 격리 롤백이 허용되지 않으면 PAUSE_ALL
        if not self.allow_isolated_rollback:
            if self.default_behavior in (
                RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
                RegionalInterlockBehavior.CONTINUE_HEALTHY,
            ):
                return RegionalInterlockBehavior.PAUSE_ALL

        return self.default_behavior

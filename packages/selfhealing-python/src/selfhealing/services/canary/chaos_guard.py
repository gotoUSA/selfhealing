"""
Chaos Experiment Guard for Canary Rollouts.

카오스 실험과의 충돌 방어.
카오스 실험 중 Canary 롤아웃 시 원인 분석이 불가능해지는 것을 방지합니다.

Reference:
    - chaos/safety_guard/guard.py
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Usage:
    guard = CanaryChaosGuard()
    result = guard.check_conflict(
        target_clusters=["seoul", "tokyo"],
        force_during_chaos=False,
    )

    if not result.can_proceed:
        raise ValueError(result.warning_message)

    # result.safe_clusters만 롤아웃 진행
"""

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ChaosConflictPolicy(str, Enum):
    """
    카오스 충돌 정책.

    롤아웃 대상 클러스터에서 카오스 실험이 진행 중일 때의 처리 방식.

    Values:
        STRICT: 카오스 실험 중 Canary 완전 차단
        SMART: 카오스 중인 클러스터만 제외하고 진행 (기본값)
        LOOSE: 경고 후 전체 진행 (권장하지 않음)
    """

    STRICT = "strict"
    SMART = "smart"
    LOOSE = "loose"


@dataclass
class ChaosConflictResult:
    """
    충돌 검사 결과.

    Attributes:
        has_conflict: 충돌 발생 여부
        chaos_clusters: 카오스 실험 중인 클러스터 목록
        safe_clusters: 롤아웃 가능한 클러스터 목록
        policy_applied: 적용된 정책
        can_proceed: 롤아웃 진행 가능 여부
        warning_message: 경고 메시지 (충돌 시)
    """

    has_conflict: bool
    chaos_clusters: list[str]
    safe_clusters: list[str]
    policy_applied: ChaosConflictPolicy
    can_proceed: bool
    warning_message: str | None = None


class CanaryChaosGuard:
    """
    Canary Rollout과 Chaos 실험 간 충돌 방어.

    카오스 실험이 진행 중인 클러스터에 Canary 롤아웃을 적용하면,
    메트릭 변화의 원인이 설정 변경인지 카오스인지 구분할 수 없습니다.
    이 가드는 충돌을 감지하고 정책에 따라 처리합니다.

    기본 정책: SMART (실험 중인 클러스터만 제외)

    Example:
        guard = CanaryChaosGuard(policy=ChaosConflictPolicy.SMART)

        result = guard.check_conflict(
            target_clusters=["seoul", "tokyo", "singapore"],
            force_during_chaos=False,
        )

        if result.has_conflict:
            logger.warning(f"Chaos clusters excluded: {result.chaos_clusters}")

        if not result.can_proceed:
            raise ValueError("All target clusters have active chaos")

        # result.safe_clusters로 롤아웃 진행
        proceed_with_clusters(result.safe_clusters)
    """

    DEFAULT_POLICY = ChaosConflictPolicy.SMART

    def __init__(self, policy: ChaosConflictPolicy = None):
        """
        CanaryChaosGuard 초기화.

        Args:
            policy: 충돌 정책 (기본값: SMART)
        """
        self._policy = policy or self.DEFAULT_POLICY

    def check_conflict(
        self,
        target_clusters: list[str],
        force_during_chaos: bool = False,
    ) -> ChaosConflictResult:
        """
        카오스 실험 충돌 검사.

        Args:
            target_clusters: 롤아웃 대상 클러스터
            force_during_chaos: 강제 진행 플래그 (True면 경고만)

        Returns:
            충돌 검사 결과 (ChaosConflictResult)

        Note:
            force_during_chaos=True는 긴급 상황에서만 사용하세요.
            메트릭 기반 자동 롤백이 오작동할 수 있습니다.
        """
        chaos_clusters = self._get_clusters_with_active_chaos()
        target_set = set(target_clusters)
        conflict_set = target_set & chaos_clusters
        safe_set = target_set - chaos_clusters

        has_conflict = len(conflict_set) > 0

        # 충돌 없음
        if not has_conflict:
            return ChaosConflictResult(
                has_conflict=False,
                chaos_clusters=[],
                safe_clusters=target_clusters,
                policy_applied=self._policy,
                can_proceed=True,
            )

        # 충돌 있음 - 정책 적용
        if force_during_chaos:
            # 강제 진행 - 경고만
            logger.warning(
                f"[ChaosGuard] FORCE: Proceeding despite active chaos on {conflict_set}"
            )
            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=target_clusters,  # 전체 진행
                policy_applied=ChaosConflictPolicy.LOOSE,
                can_proceed=True,
                warning_message=(
                    f"FORCE: Proceeding despite active chaos on {list(conflict_set)}"
                ),
            )

        # 정책별 처리
        return self._apply_policy(
            conflict_set=conflict_set,
            safe_set=safe_set,
            target_clusters=target_clusters,
        )

    def _apply_policy(
        self,
        conflict_set: set[str],
        safe_set: set[str],
        target_clusters: list[str],
    ) -> ChaosConflictResult:
        """정책에 따른 충돌 처리."""

        if self._policy == ChaosConflictPolicy.STRICT:
            # STRICT: 어떤 클러스터라도 카오스 중이면 전체 차단
            logger.warning(
                f"[ChaosGuard] STRICT: Blocked due to active chaos on {conflict_set}"
            )
            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=[],
                policy_applied=self._policy,
                can_proceed=False,
                warning_message="STRICT: Blocked due to active chaos experiments",
            )

        if self._policy == ChaosConflictPolicy.SMART:
            # SMART: 카오스 중인 클러스터만 제외
            if not safe_set:
                # 모든 클러스터가 카오스 중
                logger.warning(
                    "[ChaosGuard] SMART: All target clusters have active chaos"
                )
                return ChaosConflictResult(
                    has_conflict=True,
                    chaos_clusters=list(conflict_set),
                    safe_clusters=[],
                    policy_applied=self._policy,
                    can_proceed=False,
                    warning_message="SMART: All target clusters have active chaos",
                )

            # 안전한 클러스터만 진행
            logger.info(
                f"[ChaosGuard] SMART: Excluding {conflict_set}, "
                f"proceeding with {safe_set}"
            )
            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=list(safe_set),
                policy_applied=self._policy,
                can_proceed=True,
                warning_message=(
                    f"SMART: Excluding chaos clusters {list(conflict_set)}, "
                    f"proceeding with {list(safe_set)}"
                ),
            )

        # LOOSE: 경고 후 전체 진행
        logger.warning(f"[ChaosGuard] LOOSE: Warning - active chaos on {conflict_set}")
        return ChaosConflictResult(
            has_conflict=True,
            chaos_clusters=list(conflict_set),
            safe_clusters=target_clusters,
            policy_applied=self._policy,
            can_proceed=True,
            warning_message=f"LOOSE: Warning - active chaos on {list(conflict_set)}",
        )

    def _get_clusters_with_active_chaos(self) -> set[str]:
        """
        활성 카오스 실험 중인 클러스터 조회.

        Returns:
            카오스 실험 진행 중인 클러스터 ID 집합
        """
        try:

            # Redis에서 활성 실험 조회
            active_experiments = self._get_active_experiments()

            # 실험에서 클러스터 추출
            clusters = set()
            for exp in active_experiments:
                # 실험 메타데이터에서 클러스터 정보 추출
                target_cluster = getattr(exp, "target_cluster", None)
                if target_cluster:
                    clusters.add(target_cluster)

                # target_domain에서 클러스터 추론 (fallback)
                target_domain = getattr(exp, "target_domain", None)
                if target_domain and not target_cluster:
                    # domain에서 클러스터 추출 시도 (예: "seoul-payment-api")
                    parts = target_domain.split("-")
                    if parts:
                        clusters.add(parts[0])

            return clusters

        except Exception as e:
            logger.warning(f"[ChaosGuard] Failed to get active chaos: {e}")
            return set()

    def _get_active_experiments(self) -> list:
        """활성 카오스 실험 목록 조회."""
        try:
            # Redis에서 활성 실험 키 조회
            from django.core.cache import caches

            from selfhealing.services.chaos_context import (
                ChaosExperimentContext,
                ChaosExperimentStatus,
            )

            cache = caches.get("default")
            if not cache:
                return []

            redis_client = cache.client.get_client()
            if not redis_client:
                return []

            # 활성 실험 키 패턴 검색
            # selfhealing:*:chaos:experiment:*
            pattern = "*:chaos:experiment:*"
            keys = redis_client.keys(pattern)

            experiments = []
            for key in keys:
                try:
                    data = redis_client.get(key)
                    if data:
                        import json

                        exp_data = json.loads(data)
                        if exp_data.get("status") == ChaosExperimentStatus.ACTIVE.value:
                            exp = ChaosExperimentContext.from_dict(exp_data)
                            experiments.append(exp)
                except Exception as e:
                    logger.debug(f"[ChaosGuard] Failed to parse experiment: {e}")

            return experiments

        except ImportError:
            logger.debug("[ChaosGuard] Django cache not available")
            return []
        except Exception as e:
            logger.warning(f"[ChaosGuard] Failed to get experiments: {e}")
            return []

    @property
    def policy(self) -> ChaosConflictPolicy:
        """현재 정책 조회."""
        return self._policy

    @policy.setter
    def policy(self, value: ChaosConflictPolicy) -> None:
        """정책 변경."""
        self._policy = value
        logger.info(f"[ChaosGuard] Policy changed to: {value}")

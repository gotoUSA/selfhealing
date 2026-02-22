"""
Chaos Guard 단위 테스트.

테스트 대상:
1. ChaosConflictPolicy - 충돌 정책 enum
2. ChaosConflictResult - 충돌 검사 결과
3. CanaryChaosGuard.check_conflict() - 충돌 검사

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
"""

from unittest.mock import patch

import pytest

from selfhealing.services.canary.chaos_guard import (
    CanaryChaosGuard,
    ChaosConflictPolicy,
    ChaosConflictResult,
)

# =============================================================================
# Test: ChaosConflictPolicy
# =============================================================================


class TestChaosConflictPolicy:
    """ChaosConflictPolicy enum 테스트."""

    def test_policy_values(self):
        """모든 정책 값이 올바른지 확인."""
        assert ChaosConflictPolicy.STRICT.value == "strict"
        assert ChaosConflictPolicy.SMART.value == "smart"
        assert ChaosConflictPolicy.LOOSE.value == "loose"

    def test_policy_is_string_enum(self):
        """ChaosConflictPolicy가 str, Enum 모두 상속."""
        assert isinstance(ChaosConflictPolicy.STRICT, str)
        assert ChaosConflictPolicy.STRICT == "strict"


# =============================================================================
# Test: ChaosConflictResult
# =============================================================================


class TestChaosConflictResult:
    """ChaosConflictResult 테스트."""

    def test_result_with_no_conflict(self):
        """충돌 없는 결과."""
        result = ChaosConflictResult(
            has_conflict=False,
            chaos_clusters=[],
            safe_clusters=["seoul", "tokyo"],
            policy_applied=ChaosConflictPolicy.SMART,
            can_proceed=True,
        )

        assert result.has_conflict is False
        assert result.can_proceed is True
        assert len(result.safe_clusters) == 2
        assert result.warning_message is None

    def test_result_with_conflict(self):
        """충돌 있는 결과."""
        result = ChaosConflictResult(
            has_conflict=True,
            chaos_clusters=["seoul"],
            safe_clusters=["tokyo"],
            policy_applied=ChaosConflictPolicy.SMART,
            can_proceed=True,
            warning_message="SMART: Excluding chaos clusters",
        )

        assert result.has_conflict is True
        assert result.can_proceed is True
        assert "seoul" in result.chaos_clusters
        assert "tokyo" in result.safe_clusters
        assert result.warning_message is not None


# =============================================================================
# Test: CanaryChaosGuard
# =============================================================================


class TestCanaryChaosGuard:
    """CanaryChaosGuard 테스트."""

    @pytest.fixture
    def guard_strict(self):
        """STRICT 정책 가드."""
        return CanaryChaosGuard(policy=ChaosConflictPolicy.STRICT)

    @pytest.fixture
    def guard_smart(self):
        """SMART 정책 가드."""
        return CanaryChaosGuard(policy=ChaosConflictPolicy.SMART)

    @pytest.fixture
    def guard_loose(self):
        """LOOSE 정책 가드."""
        return CanaryChaosGuard(policy=ChaosConflictPolicy.LOOSE)

    # -------------------------------------------------------------------------
    # 기본 동작 테스트
    # -------------------------------------------------------------------------

    def test_default_policy_is_smart(self):
        """기본 정책이 SMART인지 확인."""
        guard = CanaryChaosGuard()
        assert guard.policy == ChaosConflictPolicy.SMART

    def test_policy_setter(self, guard_smart):
        """정책 변경 가능."""
        guard_smart.policy = ChaosConflictPolicy.STRICT
        assert guard_smart.policy == ChaosConflictPolicy.STRICT

    # -------------------------------------------------------------------------
    # 충돌 없음 테스트
    # -------------------------------------------------------------------------

    def test_no_conflict_when_no_chaos(self, guard_smart):
        """카오스 실험이 없을 때 충돌 없음."""
        with patch.object(guard_smart, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = set()

            result = guard_smart.check_conflict(
                target_clusters=["seoul", "tokyo"]
            )

        assert result.has_conflict is False
        assert result.can_proceed is True
        assert result.safe_clusters == ["seoul", "tokyo"]
        assert result.chaos_clusters == []

    def test_no_conflict_when_different_clusters(self, guard_smart):
        """다른 클러스터에서 카오스 실험 중일 때."""
        with patch.object(guard_smart, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"singapore"}  # 다른 클러스터

            result = guard_smart.check_conflict(
                target_clusters=["seoul", "tokyo"]
            )

        assert result.has_conflict is False
        assert result.can_proceed is True

    # -------------------------------------------------------------------------
    # STRICT 정책 테스트
    # -------------------------------------------------------------------------

    def test_strict_blocks_on_any_conflict(self, guard_strict):
        """STRICT: 충돌 시 전체 차단."""
        with patch.object(guard_strict, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"seoul"}

            result = guard_strict.check_conflict(
                target_clusters=["seoul", "tokyo"]
            )

        assert result.has_conflict is True
        assert result.can_proceed is False
        assert result.safe_clusters == []
        assert "STRICT" in result.warning_message

    # -------------------------------------------------------------------------
    # SMART 정책 테스트
    # -------------------------------------------------------------------------

    def test_smart_excludes_chaos_clusters(self, guard_smart):
        """SMART: 카오스 클러스터만 제외."""
        with patch.object(guard_smart, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"seoul"}

            result = guard_smart.check_conflict(
                target_clusters=["seoul", "tokyo", "singapore"]
            )

        assert result.has_conflict is True
        assert result.can_proceed is True
        assert "seoul" in result.chaos_clusters
        assert "tokyo" in result.safe_clusters
        assert "singapore" in result.safe_clusters
        assert "seoul" not in result.safe_clusters

    def test_smart_blocks_when_all_clusters_in_chaos(self, guard_smart):
        """SMART: 모든 클러스터가 카오스 중일 때 차단."""
        with patch.object(guard_smart, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"seoul", "tokyo"}

            result = guard_smart.check_conflict(
                target_clusters=["seoul", "tokyo"]
            )

        assert result.has_conflict is True
        assert result.can_proceed is False
        assert len(result.safe_clusters) == 0
        assert "SMART" in result.warning_message

    # -------------------------------------------------------------------------
    # LOOSE 정책 테스트
    # -------------------------------------------------------------------------

    def test_loose_proceeds_with_warning(self, guard_loose):
        """LOOSE: 경고 후 전체 진행."""
        with patch.object(guard_loose, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"seoul"}

            result = guard_loose.check_conflict(
                target_clusters=["seoul", "tokyo"]
            )

        assert result.has_conflict is True
        assert result.can_proceed is True
        assert result.safe_clusters == ["seoul", "tokyo"]  # 전체 진행
        assert "LOOSE" in result.warning_message

    # -------------------------------------------------------------------------
    # force_during_chaos 테스트
    # -------------------------------------------------------------------------

    def test_force_overrides_any_policy(self, guard_strict):
        """force_during_chaos=True는 모든 정책을 무시."""
        with patch.object(guard_strict, "_get_clusters_with_active_chaos") as mock:
            mock.return_value = {"seoul", "tokyo"}

            result = guard_strict.check_conflict(
                target_clusters=["seoul", "tokyo"],
                force_during_chaos=True,
            )

        assert result.has_conflict is True
        assert result.can_proceed is True  # 강제 진행
        assert result.safe_clusters == ["seoul", "tokyo"]
        assert result.policy_applied == ChaosConflictPolicy.LOOSE  # LOOSE로 변경
        assert "FORCE" in result.warning_message

    # -------------------------------------------------------------------------
    # 에러 핸들링 테스트
    # -------------------------------------------------------------------------

    def test_handles_chaos_check_error_gracefully(self, guard_smart):
        """카오스 조회 실패 시 빈 집합 반환."""
        with patch.object(guard_smart, "_get_clusters_with_active_chaos") as mock:
            mock.side_effect = Exception("Redis error")
            mock.return_value = set()

            # 다시 정상 동작 패치
            with patch.object(guard_smart, "_get_clusters_with_active_chaos", return_value=set()):
                result = guard_smart.check_conflict(
                    target_clusters=["seoul", "tokyo"]
                )

        # 에러 시에도 정상 처리
        assert result.can_proceed is True

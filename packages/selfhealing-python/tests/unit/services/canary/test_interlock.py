"""
Canary Safety Interlock 단위 테스트.

테스트 대상:
1. InterlockAction, InterlockCheckFailure enum 값
2. InterlockResult 팩토리 메서드
3. CanarySafetyInterlock.check() - 레벨별 액션 결정
4. CanarySafetyInterlock - Fail-Closed 정책
5. CanarySafetyInterlock.check_and_apply() - 자동 적용
6. 싱글톤 관리

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""

import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass

from selfhealing.services.canary.interlock import (
    InterlockAction,
    InterlockCheckFailure,
    InterlockResult,
    CanarySafetyInterlock,
    get_canary_safety_interlock,
    reset_canary_safety_interlock,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singleton():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_canary_safety_interlock()
    yield
    reset_canary_safety_interlock()


@pytest.fixture
def mock_tracker():
    """Mock NamespacedEmergencyTracker."""
    tracker = MagicMock()
    
    # 기본값: NORMAL 상태
    mock_state = MagicMock()
    mock_state.emergency_level = EmergencyLevel.NORMAL
    mock_state.namespace = "test-namespace"
    tracker.get_effective_state.return_value = mock_state
    
    return tracker


def create_mock_state(level: EmergencyLevel, namespace: str = "test-namespace"):
    """Mock ScopedEmergencyState 생성 헬퍼."""
    mock_state = MagicMock()
    mock_state.emergency_level = level
    mock_state.namespace = namespace
    return mock_state


# =============================================================================
# Test: InterlockAction Enum
# =============================================================================


class TestInterlockAction:
    """InterlockAction enum 테스트."""

    def test_action_values(self):
        """액션 값이 올바르게 정의되어 있는지 확인."""
        assert InterlockAction.ALLOW.value == "allow"
        assert InterlockAction.ALLOW_WITH_WARNING.value == "allow_with_warning"
        assert InterlockAction.PAUSE.value == "pause"
        assert InterlockAction.ROLLBACK.value == "rollback"
        assert InterlockAction.BLOCK.value == "block"

    def test_action_is_string_enum(self):
        """InterlockAction이 문자열 비교 가능한지 확인."""
        assert InterlockAction.ALLOW == "allow"
        assert InterlockAction.PAUSE == "pause"


# =============================================================================
# Test: InterlockCheckFailure Enum
# =============================================================================


class TestInterlockCheckFailure:
    """InterlockCheckFailure enum 테스트."""

    def test_failure_values(self):
        """실패 유형 값이 올바르게 정의되어 있는지 확인."""
        assert InterlockCheckFailure.BACKEND_UNAVAILABLE.value == "backend_unavailable"
        assert InterlockCheckFailure.TRACKER_ERROR.value == "tracker_error"
        assert InterlockCheckFailure.TIMEOUT.value == "timeout"


# =============================================================================
# Test: InterlockResult Factory Methods
# =============================================================================


class TestInterlockResultFactoryMethods:
    """InterlockResult 팩토리 메서드 테스트."""

    def test_allow_factory(self):
        """allow() 팩토리 메서드 테스트."""
        result = InterlockResult.allow(
            emergency_level=0,
            emergency_level_name="NORMAL",
            namespace="seoul",
        )
        
        assert result.action == InterlockAction.ALLOW
        assert result.allowed is True
        assert result.emergency_level == 0
        assert result.emergency_level_name == "NORMAL"
        assert result.namespace == "seoul"
        assert result.is_fail_closed is False
        assert result.check_failure is None

    def test_allow_with_warning_factory(self):
        """allow_with_warning() 팩토리 메서드 테스트."""
        result = InterlockResult.allow_with_warning(
            emergency_level=1,
            emergency_level_name="LEVEL_1",
            namespace="tokyo",
        )
        
        assert result.action == InterlockAction.ALLOW_WITH_WARNING
        assert result.allowed is True
        assert result.emergency_level == 1
        assert "LEVEL_1" in result.reason

    def test_pause_factory(self):
        """pause() 팩토리 메서드 테스트."""
        result = InterlockResult.pause(
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="oregon",
        )
        
        assert result.action == InterlockAction.PAUSE
        assert result.allowed is False
        assert result.emergency_level == 2
        assert "LEVEL_2" in result.reason

    def test_pause_factory_with_custom_reason(self):
        """pause() 팩토리 메서드 커스텀 사유 테스트."""
        result = InterlockResult.pause(
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="oregon",
            reason="DB 장애로 인한 일시 중지",
        )
        
        assert result.reason == "DB 장애로 인한 일시 중지"

    def test_rollback_factory(self):
        """rollback() 팩토리 메서드 테스트."""
        result = InterlockResult.rollback(
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="global",
        )
        
        assert result.action == InterlockAction.ROLLBACK
        assert result.allowed is False
        assert result.emergency_level == 3
        assert "LEVEL_3" in result.reason

    def test_fail_closed_factory(self):
        """fail_closed() 팩토리 메서드 테스트."""
        result = InterlockResult.fail_closed(
            failure=InterlockCheckFailure.BACKEND_UNAVAILABLE,
            namespace="unknown",
            error_message="Redis connection timeout",
        )
        
        assert result.action == InterlockAction.ROLLBACK
        assert result.allowed is False
        assert result.emergency_level == 3  # LEVEL_3로 간주
        assert result.is_fail_closed is True
        assert result.check_failure == InterlockCheckFailure.BACKEND_UNAVAILABLE
        assert "Redis connection timeout" in result.reason

    def test_block_factory(self):
        """block() 팩토리 메서드 테스트."""
        result = InterlockResult.block(
            emergency_level=3,
            emergency_level_name="LEVEL_3",
            namespace="seoul",
        )
        
        assert result.action == InterlockAction.BLOCK
        assert result.allowed is False


# =============================================================================
# Test: CanarySafetyInterlock.check() - Emergency Level별 액션
# =============================================================================


class TestCanarySafetyInterlockCheck:
    """CanarySafetyInterlock.check() 테스트."""

    def test_check_normal_level_allows(self, mock_tracker):
        """NORMAL 레벨에서는 ALLOW."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(operation="promote", rollout_id="test-123")
        
        assert result.action == InterlockAction.ALLOW
        assert result.allowed is True
        assert result.emergency_level == 0

    def test_check_level1_allows_with_warning(self, mock_tracker):
        """LEVEL_1에서는 ALLOW_WITH_WARNING."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_1
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(operation="promote", rollout_id="test-123")
        
        assert result.action == InterlockAction.ALLOW_WITH_WARNING
        assert result.allowed is True
        assert result.emergency_level == 1

    def test_check_level2_pauses(self, mock_tracker):
        """LEVEL_2에서는 PAUSE."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_2
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(operation="promote", rollout_id="test-123")
        
        assert result.action == InterlockAction.PAUSE
        assert result.allowed is False
        assert result.emergency_level == 2

    def test_check_level3_rollbacks(self, mock_tracker):
        """LEVEL_3에서는 ROLLBACK."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_3
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(operation="start", rollout_id="test-123")
        
        assert result.action == InterlockAction.ROLLBACK
        assert result.allowed is False
        assert result.emergency_level == 3


# =============================================================================
# Test: CanarySafetyInterlock - 커스텀 정책
# =============================================================================


class TestCanarySafetyInterlockCustomPolicy:
    """커스텀 정책 테스트."""

    def test_custom_policy_overrides_default(self, mock_tracker):
        """커스텀 정책이 기본 정책을 오버라이드."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_1
        )
        
        # LEVEL_1에서도 PAUSE하는 커스텀 정책
        custom_policy = {
            0: InterlockAction.ALLOW,
            1: InterlockAction.PAUSE,  # 기본은 ALLOW_WITH_WARNING
            2: InterlockAction.ROLLBACK,  # 기본은 PAUSE
            3: InterlockAction.ROLLBACK,
        }
        
        interlock = CanarySafetyInterlock(
            policy=custom_policy,
            emergency_tracker_factory=lambda: mock_tracker,
        )
        
        result = interlock.check(operation="promote")
        
        assert result.action == InterlockAction.PAUSE
        assert result.allowed is False


# =============================================================================
# Test: CanarySafetyInterlock - Fail-Closed 정책
# =============================================================================


class TestCanarySafetyInterlockFailClosed:
    """Fail-Closed 정책 테스트."""

    def test_fail_closed_on_backend_error(self):
        """백엔드 오류 시 Fail-Closed로 ROLLBACK."""
        def failing_tracker_factory():
            raise ConnectionError("Redis connection failed")
        
        interlock = CanarySafetyInterlock(
            fail_closed=True,
            emergency_tracker_factory=failing_tracker_factory,
        )
        
        result = interlock.check(operation="promote", rollout_id="test-123")
        
        assert result.action == InterlockAction.ROLLBACK
        assert result.allowed is False
        assert result.is_fail_closed is True
        assert result.check_failure == InterlockCheckFailure.BACKEND_UNAVAILABLE
        assert result.emergency_level == 3  # LEVEL_3로 간주

    def test_fail_open_on_backend_error(self):
        """Fail-Open 모드에서는 백엔드 오류 시 ALLOW."""
        def failing_tracker_factory():
            raise ConnectionError("Redis connection failed")
        
        interlock = CanarySafetyInterlock(
            fail_closed=False,  # Fail-Open (위험!)
            emergency_tracker_factory=failing_tracker_factory,
        )
        
        result = interlock.check(operation="promote", rollout_id="test-123")
        
        assert result.action == InterlockAction.ALLOW
        assert result.allowed is True
        assert result.is_fail_closed is False

    def test_fail_closed_includes_error_message(self):
        """Fail-Closed 결과에 에러 메시지 포함."""
        def failing_tracker_factory():
            raise TimeoutError("Backend timeout after 5s")
        
        interlock = CanarySafetyInterlock(
            fail_closed=True,
            emergency_tracker_factory=failing_tracker_factory,
        )
        
        result = interlock.check(operation="start")
        
        assert "Backend timeout after 5s" in result.reason


# =============================================================================
# Test: CanarySafetyInterlock.check_and_apply()
# =============================================================================


class TestCanarySafetyInterlockCheckAndApply:
    """check_and_apply() 테스트."""

    def test_check_and_apply_pauses_on_level2(self, mock_tracker):
        """LEVEL_2에서 자동으로 pause 호출."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_2
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        mock_canary_service = MagicMock()
        mock_canary_service.pause.return_value = True
        
        result = interlock.check_and_apply(
            canary_service=mock_canary_service,
            rollout_id="test-123",
            operation="promote",
        )
        
        assert result.action == InterlockAction.PAUSE
        mock_canary_service.pause.assert_called_once_with("test-123")
        assert result.metadata.get("auto_applied") is True
        assert result.metadata.get("apply_success") is True

    def test_check_and_apply_rollbacks_on_level3(self, mock_tracker):
        """LEVEL_3에서 자동으로 rollback 호출."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_3
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        mock_canary_service = MagicMock()
        mock_canary_service.rollback.return_value = True
        
        result = interlock.check_and_apply(
            canary_service=mock_canary_service,
            rollout_id="test-456",
            operation="start",
        )
        
        assert result.action == InterlockAction.ROLLBACK
        mock_canary_service.rollback.assert_called_once()
        assert result.metadata.get("auto_applied") is True

    def test_check_and_apply_no_action_on_allow(self, mock_tracker):
        """ALLOW에서는 canary_service 호출 없음."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        mock_canary_service = MagicMock()
        
        result = interlock.check_and_apply(
            canary_service=mock_canary_service,
            rollout_id="test-789",
            operation="promote",
        )
        
        assert result.action == InterlockAction.ALLOW
        mock_canary_service.pause.assert_not_called()
        mock_canary_service.rollback.assert_not_called()

    def test_check_and_apply_handles_service_error(self, mock_tracker):
        """canary_service 호출 실패 시 메타데이터에 오류 기록."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_3
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        mock_canary_service = MagicMock()
        mock_canary_service.rollback.side_effect = Exception("DB connection error")
        
        result = interlock.check_and_apply(
            canary_service=mock_canary_service,
            rollout_id="test-error",
            operation="promote",
        )
        
        assert result.action == InterlockAction.ROLLBACK
        assert result.metadata.get("auto_applied") is True
        assert result.metadata.get("apply_success") is False
        assert "DB connection error" in result.metadata.get("apply_error", "")


# =============================================================================
# Test: Singleton
# =============================================================================


class TestSingleton:
    """싱글톤 테스트."""

    def test_get_returns_same_instance(self):
        """get_canary_safety_interlock()이 동일 인스턴스 반환."""
        reset_canary_safety_interlock()
        
        with patch(
            "selfhealing.services.canary.interlock."
            "CanarySafetyInterlock._get_emergency_tracker"
        ):
            instance1 = get_canary_safety_interlock()
            instance2 = get_canary_safety_interlock()
        
        assert instance1 is instance2

    def test_reset_clears_singleton(self):
        """reset_canary_safety_interlock()이 싱글톤 초기화."""
        with patch(
            "selfhealing.services.canary.interlock."
            "CanarySafetyInterlock._get_emergency_tracker"
        ):
            instance1 = get_canary_safety_interlock()
            reset_canary_safety_interlock()
            instance2 = get_canary_safety_interlock()
        
        assert instance1 is not instance2


# =============================================================================
# Test: Default Policy
# =============================================================================


class TestDefaultPolicy:
    """기본 정책 테스트."""

    def test_default_policy_mapping(self):
        """기본 정책 매핑 확인."""
        assert CanarySafetyInterlock.DEFAULT_POLICY == {
            0: InterlockAction.ALLOW,
            1: InterlockAction.ALLOW_WITH_WARNING,
            2: InterlockAction.PAUSE,
            3: InterlockAction.ROLLBACK,
        }

    def test_policy_not_mutated(self, mock_tracker):
        """인스턴스 정책이 DEFAULT_POLICY를 변경하지 않음."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        # 인스턴스 정책 변경
        interlock.policy[0] = InterlockAction.BLOCK
        
        # DEFAULT_POLICY는 변경되지 않음
        assert CanarySafetyInterlock.DEFAULT_POLICY[0] == InterlockAction.ALLOW


# =============================================================================
# Test: Namespace Support
# =============================================================================


class TestNamespaceSupport:
    """네임스페이스 지원 테스트."""

    def test_check_with_namespace(self, mock_tracker):
        """네임스페이스 파라미터가 트래커에 전달되는지 확인."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.NORMAL, namespace="seoul"
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(
            operation="promote",
            rollout_id="test-123",
            namespace="seoul",
        )
        
        mock_tracker.get_effective_state.assert_called_once_with(namespace="seoul")
        assert result.namespace == "seoul"

    def test_result_includes_namespace(self, mock_tracker):
        """결과에 네임스페이스 포함."""
        mock_tracker.get_effective_state.return_value = create_mock_state(
            EmergencyLevel.LEVEL_2, namespace="tokyo"
        )
        
        interlock = CanarySafetyInterlock(
            emergency_tracker_factory=lambda: mock_tracker
        )
        
        result = interlock.check(operation="promote", namespace="tokyo")
        
        assert result.namespace == "tokyo"


# =============================================================================
# Phase 2 Tests: EmergencyOverrideRequest
# =============================================================================


from selfhealing.services.canary.interlock import (
    EmergencyOverrideRequest,
    EmergencyOverridePolicy,
    RegionalInterlockBehavior,
    RegionalInterlockPolicy,
    PauseContext,
    PauseReasonTracker,
    get_pause_reason_tracker,
    reset_pause_reason_tracker,
)


class TestEmergencyOverrideRequest:
    """EmergencyOverrideRequest 테스트."""

    def test_valid_request(self):
        """유효한 Override 요청."""
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구를 위한 설정 변경입니다",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
        )
        
        assert override.is_valid() is True

    def test_invalid_request_short_reason(self):
        """사유가 너무 짧으면 무효."""
        override = EmergencyOverrideRequest(
            reason="짧음",  # 10자 미만
            requested_by="sre@example.com",
        )
        
        assert override.is_valid() is False

    def test_invalid_request_empty_requested_by(self):
        """요청자가 비어있으면 무효."""
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구를 위한 설정 변경입니다",
            requested_by="",
        )
        
        assert override.is_valid() is False

    def test_requires_approval_token_for_level_3(self):
        """LEVEL_3에서는 승인 토큰 필요."""
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
        )
        
        assert override.requires_approval_token(level_value=3) is True
        assert override.requires_approval_token(level_value=2) is False
        assert override.requires_approval_token(level_value=1) is False

    def test_acknowledged_risks_default_empty(self):
        """인지한 위험 목록 기본값은 빈 리스트."""
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
        )
        
        assert override.acknowledged_risks == []


# =============================================================================
# Phase 2 Tests: EmergencyOverridePolicy
# =============================================================================


class TestEmergencyOverridePolicy:
    """EmergencyOverridePolicy 테스트."""

    def test_default_policy_values(self):
        """기본 정책 값 확인."""
        policy = EmergencyOverridePolicy()
        
        assert policy.enabled is True
        assert policy.min_reason_length == 10
        assert policy.require_ticket_id is True
        assert policy.require_approval_on_level_3 is True
        assert policy.default_ttl_minutes == 60
        assert policy.max_ttl_minutes == 240
        assert policy.pir_required is True
        assert "admin" in policy.allowed_roles

    def test_validate_valid_request(self):
        """유효한 요청 검증 통과."""
        policy = EmergencyOverridePolicy()
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구를 위한 설정 변경입니다",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
        )
        
        is_valid, error = policy.validate_request(override, level_value=2)
        
        assert is_valid is True
        assert error is None

    def test_validate_fails_when_disabled(self):
        """Override 비활성화 시 검증 실패."""
        policy = EmergencyOverridePolicy(enabled=False)
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
        )
        
        is_valid, error = policy.validate_request(override, level_value=2)
        
        assert is_valid is False
        assert "disabled" in error

    def test_validate_fails_without_ticket(self):
        """티켓 ID 필수 시 누락되면 검증 실패."""
        policy = EmergencyOverridePolicy(require_ticket_id=True)
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
            ticket_id=None,  # 티켓 없음
        )
        
        is_valid, error = policy.validate_request(override, level_value=2)
        
        assert is_valid is False
        assert "Ticket ID" in error

    def test_validate_fails_level3_without_approval(self):
        """LEVEL_3에서 승인 토큰 없으면 검증 실패."""
        policy = EmergencyOverridePolicy(require_approval_on_level_3=True)
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
            approval_token=None,  # 승인 토큰 없음
        )
        
        is_valid, error = policy.validate_request(override, level_value=3)
        
        assert is_valid is False
        assert "Approval token" in error

    def test_validate_passes_level3_with_approval(self):
        """LEVEL_3에서 승인 토큰 있으면 검증 통과."""
        policy = EmergencyOverridePolicy(require_approval_on_level_3=True)
        override = EmergencyOverrideRequest(
            reason="긴급 장애 복구입니다",
            requested_by="sre@example.com",
            ticket_id="INC-12345",
            approval_token="APPROVED-ABC123",
        )
        
        is_valid, error = policy.validate_request(override, level_value=3)
        
        assert is_valid is True
        assert error is None


# =============================================================================
# Phase 2 Tests: RegionalInterlockBehavior & RegionalInterlockPolicy
# =============================================================================


class TestRegionalInterlockBehavior:
    """RegionalInterlockBehavior enum 테스트."""

    def test_behavior_values(self):
        """행동 값이 올바르게 정의되어 있는지 확인."""
        assert RegionalInterlockBehavior.PAUSE_ALL.value == "pause_all"
        assert RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY.value == "rollback_affected_only"
        assert RegionalInterlockBehavior.HYBRID.value == "hybrid"
        assert RegionalInterlockBehavior.CONTINUE_HEALTHY.value == "continue_healthy"

    def test_behavior_is_string_enum(self):
        """RegionalInterlockBehavior가 문자열 비교 가능한지 확인."""
        assert RegionalInterlockBehavior.PAUSE_ALL == "pause_all"
        assert RegionalInterlockBehavior.HYBRID == "hybrid"


class TestRegionalInterlockPolicy:
    """RegionalInterlockPolicy 테스트."""

    def test_default_policy_values(self):
        """기본 정책 값 확인."""
        policy = RegionalInterlockPolicy()
        
        assert policy.default_behavior == RegionalInterlockBehavior.PAUSE_ALL
        assert policy.allow_isolated_rollback is False
        assert policy.require_manual_resume_after_regional_rollback is True
        assert policy.max_affected_regions_for_isolated_rollback == 1

    def test_all_regions_affected_returns_pause_all(self):
        """모든 리전이 영향받으면 PAUSE_ALL 반환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.CONTINUE_HEALTHY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul", "tokyo"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_non_isolated_deployment_ignores_rollback_affected_only(self):
        """격리 배포가 아니면 ROLLBACK_AFFECTED_ONLY를 PAUSE_ALL로 변환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=False,  # 격리 배포 아님
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_too_many_affected_regions_returns_pause_all(self):
        """영향 리전이 max_affected_regions보다 많으면 PAUSE_ALL."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
            max_affected_regions_for_isolated_rollback=1,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul", "tokyo"],  # 2개 영향
            total_regions=["seoul", "tokyo", "oregon"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_isolated_rollback_disabled_returns_pause_all(self):
        """격리 롤백 비활성화 시 ROLLBACK_AFFECTED_ONLY를 PAUSE_ALL로 변환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=False,  # 격리 롤백 비활성화
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.PAUSE_ALL

    def test_hybrid_allowed_for_non_isolated(self):
        """격리 배포 아니어도 HYBRID는 허용."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.HYBRID,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo"],
            is_region_isolated_deployment=False,
        )
        
        assert behavior == RegionalInterlockBehavior.HYBRID

    def test_isolated_rollback_enabled_with_valid_conditions(self):
        """모든 조건 충족 시 설정된 행동 반환."""
        policy = RegionalInterlockPolicy(
            default_behavior=RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY,
            allow_isolated_rollback=True,
            max_affected_regions_for_isolated_rollback=2,
        )
        
        behavior = policy.get_behavior_for_situation(
            affected_regions=["seoul"],
            total_regions=["seoul", "tokyo", "oregon"],
            is_region_isolated_deployment=True,
        )
        
        assert behavior == RegionalInterlockBehavior.ROLLBACK_AFFECTED_ONLY


# =============================================================================
# Phase 2 Tests: PauseContext
# =============================================================================


class TestPauseContext:
    """PauseContext 테스트."""

    def test_create_pause_context(self):
        """PauseContext 생성."""
        context = PauseContext(
            reason="Emergency LEVEL_2 발생으로 일시 중지",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )
        
        assert context.reason == "Emergency LEVEL_2 발생으로 일시 중지"
        assert context.triggered_by == "interlock"
        assert context.emergency_level == 2

    def test_explain_interlock_triggered(self):
        """인터락으로 인한 중지 설명."""
        context = PauseContext(
            reason="시스템 안정화 필요",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )
        
        explanation = context.explain()
        
        assert "일시 중지" in explanation
        assert "LEVEL_2" in explanation
        assert "seoul" in explanation

    def test_explain_manual_triggered(self):
        """수동 중지 설명."""
        context = PauseContext(
            reason="운영자 요청",
            triggered_by="manual",
        )
        
        explanation = context.explain()
        
        assert "수동 중지" in explanation

    def test_explain_chaos_guard_triggered(self):
        """Chaos Guard로 인한 중지 설명."""
        context = PauseContext(
            reason="Chaos 실험 충돌",
            triggered_by="chaos_guard",
        )
        
        explanation = context.explain()
        
        assert "Chaos" in explanation

    def test_explain_metrics_triggered(self):
        """메트릭 악화로 인한 중지 설명."""
        context = PauseContext(
            reason="에러율 증가",
            triggered_by="metrics",
        )
        
        explanation = context.explain()
        
        assert "메트릭" in explanation

    def test_explain_with_causation_chain(self):
        """CausationChain ID 포함 설명."""
        context = PauseContext(
            reason="테스트",
            triggered_by="interlock",
            causation_chain_id="chain-123",
        )
        
        explanation = context.explain()
        
        assert "chain-123" in explanation

    def test_to_dict(self):
        """딕셔너리 변환."""
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
            emergency_level=2,
            emergency_level_name="LEVEL_2",
            namespace="seoul",
        )
        
        data = context.to_dict()
        
        assert data["reason"] == "테스트 사유"
        assert data["triggered_by"] == "interlock"
        assert data["emergency_level"] == 2
        assert data["namespace"] == "seoul"


# =============================================================================
# Phase 2 Tests: PauseReasonTracker
# =============================================================================


@pytest.fixture(autouse=True)
def reset_pause_tracker():
    """각 테스트 전후로 PauseReasonTracker 싱글톤 초기화."""
    reset_pause_reason_tracker()
    yield
    reset_pause_reason_tracker()


class TestPauseReasonTracker:
    """PauseReasonTracker 테스트."""

    def test_record_pause(self):
        """PAUSE 이벤트 기록."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )
        
        chain_id = tracker.record_pause("rollout-123", context)
        
        assert chain_id is not None
        assert len(chain_id) > 0

    def test_record_pause_sets_causation_chain_id(self):
        """record_pause가 causation_chain_id를 설정."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )
        
        chain_id = tracker.record_pause("rollout-123", context)
        
        assert context.causation_chain_id == chain_id

    def test_record_pause_sets_paused_at(self):
        """record_pause가 paused_at을 설정."""
        tracker = PauseReasonTracker()
        context = PauseContext(
            reason="테스트 사유",
            triggered_by="interlock",
        )
        
        tracker.record_pause("rollout-123", context)
        
        assert context.paused_at is not None

    def test_get_pause_history(self):
        """PAUSE 이력 조회."""
        tracker = PauseReasonTracker()
        context1 = PauseContext(reason="첫 번째 중지", triggered_by="interlock")
        context2 = PauseContext(reason="두 번째 중지", triggered_by="manual")
        
        tracker.record_pause("rollout-123", context1)
        tracker.record_pause("rollout-123", context2)
        
        history = tracker.get_pause_history("rollout-123")
        
        assert len(history) == 2
        assert history[0].reason == "첫 번째 중지"
        assert history[1].reason == "두 번째 중지"

    def test_get_pause_history_empty(self):
        """존재하지 않는 롤아웃의 이력은 빈 리스트."""
        tracker = PauseReasonTracker()
        
        history = tracker.get_pause_history("nonexistent")
        
        assert history == []

    def test_get_latest_pause(self):
        """최신 PAUSE 컨텍스트 조회."""
        tracker = PauseReasonTracker()
        context1 = PauseContext(reason="첫 번째", triggered_by="interlock")
        context2 = PauseContext(reason="두 번째", triggered_by="manual")
        
        tracker.record_pause("rollout-123", context1)
        tracker.record_pause("rollout-123", context2)
        
        latest = tracker.get_latest_pause("rollout-123")
        
        assert latest is not None
        assert latest.reason == "두 번째"

    def test_get_latest_pause_none(self):
        """이력 없으면 None 반환."""
        tracker = PauseReasonTracker()
        
        latest = tracker.get_latest_pause("nonexistent")
        
        assert latest is None

    def test_clear(self):
        """특정 롤아웃 이력 삭제."""
        tracker = PauseReasonTracker()
        tracker.record_pause("rollout-1", PauseContext(reason="테스트", triggered_by="manual"))
        tracker.record_pause("rollout-2", PauseContext(reason="테스트", triggered_by="manual"))
        
        tracker.clear("rollout-1")
        
        assert tracker.get_pause_history("rollout-1") == []
        assert len(tracker.get_pause_history("rollout-2")) == 1

    def test_clear_all(self):
        """모든 이력 삭제."""
        tracker = PauseReasonTracker()
        tracker.record_pause("rollout-1", PauseContext(reason="테스트", triggered_by="manual"))
        tracker.record_pause("rollout-2", PauseContext(reason="테스트", triggered_by="manual"))
        
        tracker.clear_all()
        
        assert tracker.get_pause_history("rollout-1") == []
        assert tracker.get_pause_history("rollout-2") == []


class TestPauseReasonTrackerSingleton:
    """PauseReasonTracker 싱글톤 테스트."""

    def test_get_returns_same_instance(self):
        """get_pause_reason_tracker()가 동일 인스턴스 반환."""
        instance1 = get_pause_reason_tracker()
        instance2 = get_pause_reason_tracker()
        
        assert instance1 is instance2

    def test_reset_clears_singleton(self):
        """reset_pause_reason_tracker()가 싱글톤 초기화."""
        instance1 = get_pause_reason_tracker()
        reset_pause_reason_tracker()
        instance2 = get_pause_reason_tracker()
        
        assert instance1 is not instance2

"""
Phase 2: EmergencyOverrideRequest, EmergencyOverridePolicy 단위 테스트.

Break Glass (비상 우회) 기능 테스트.

Reference: docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md
"""


from selfhealing.services.canary.override import (
    EmergencyOverridePolicy,
    EmergencyOverrideRequest,
)

# =============================================================================
# Test: EmergencyOverrideRequest
# =============================================================================


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
# Test: EmergencyOverridePolicy
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

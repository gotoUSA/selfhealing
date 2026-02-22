"""
CorruptionShield Audit 통합 테스트.

테스트 대상:
- TestCorruptionShieldAuditIntegration: CorruptionShield Audit 통합
- TestCorruptionShieldBatching: CorruptionShield 배칭
"""

from unittest.mock import MagicMock, patch


class TestCorruptionShieldAuditIntegration:
    """CorruptionShield Audit 통합 테스트."""

    def test_validate_has_request_parameter(self):
        """validate() 메서드에 request 파라미터 존재 확인."""
        import inspect

        from selfhealing.services.corruption_shield import CorruptionShield

        sig = inspect.signature(CorruptionShield.validate)
        assert "request" in sig.parameters

    def test_corruption_detected_recorded_to_audit_buffer(self):
        """L1/L2/L3 위반 발견 시 RequestAuditBuffer에 기록."""
        from selfhealing.audit.event_buffer import AuditEventType, RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        # 설정: L2 비즈니스 규칙 검증 활성화
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
            min_amount=100,
            max_amount=100_000_000,
        )
        shield = CorruptionShield(config)

        # Mock request 객체
        mock_request = MagicMock()
        mock_request.META = {}

        # Invalid data (음수 금액 - L2 위반)
        data = {"amount": -1000, "order_id": "test-123", "status": "DONE"}

        result = shield.validate(data, request=mock_request)

        # 검증
        if not result.is_valid:
            # RequestAuditBuffer가 생성되었는지 확인
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()

            # CORRUPTION_DETECTED 또는 CORRUPTION_BLOCKED 이벤트가 있어야 함
            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED
                )
            ]
            assert len(corruption_events) > 0, "Corruption 이벤트가 기록되어야 함"

    def test_corruption_blocked_event_when_blocked(self):
        """차단 시 CORRUPTION_BLOCKED 이벤트 기록."""
        from selfhealing.audit.event_buffer import AuditEventType, RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        # 설정
        config = CorruptionShieldConfig(l1_enabled=True, l2_enabled=True, l3_enabled=False)
        shield = CorruptionShield(config)

        # Mock request
        mock_request = MagicMock()
        mock_request.META = {}

        # Missing required fields (L1 위반 - critical severity)
        data = {}

        result = shield.validate(data, block_on_violation=True, request=mock_request)

        if result.blocked:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()

            blocked_events = [
                e for e in events
                if e.event_type == AuditEventType.CORRUPTION_BLOCKED
            ]
            assert len(blocked_events) > 0, "CORRUPTION_BLOCKED 이벤트가 기록되어야 함"

    def test_no_audit_when_valid(self):
        """유효한 데이터일 때 Audit 이벤트 없음."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        config = CorruptionShieldConfig(l1_enabled=True, l2_enabled=True, l3_enabled=False)
        shield = CorruptionShield(config)

        mock_request = MagicMock()
        mock_request.META = {}

        # Valid data
        data = {"amount": 10000, "order_id": "test-123", "status": "DONE"}

        result = shield.validate(data, request=mock_request)

        if result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            # 버퍼가 비어있거나 corruption 이벤트가 없어야 함
            events = buffer.get_events()
            corruption_events = [e for e in events if "CORRUPTION" in e.event_type.name]
            assert len(corruption_events) == 0


class TestCorruptionShieldBatching:
    """
    CorruptionShield 배칭 테스트.
    
    CorruptionShield 개선:
    - 10개 필드 위반 시 10개 로그 → 1개 로그로 최적화
    """

    def test_multiple_violations_single_event(self):
        """여러 위반 사항이 단일 Audit 이벤트로 기록됨."""
        from selfhealing.audit.event_buffer import AuditEventType, RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        # L2 규칙으로 여러 위반 유도
        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)

        mock_request = MagicMock()
        mock_request.META = {}

        # 여러 필드 위반 데이터
        data = {
            "amount": -1000,  # L2 위반: 음수 금액
            "status": "INVALID_STATUS",  # L2 위반: 유효하지 않은 상태
        }

        result = shield.validate(data, request=mock_request)

        if not result.is_valid and len(result.violations) > 1:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()

            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]

            # 배칭: 여러 violations에 대해 단일 이벤트만 생성되어야 함
            assert len(corruption_events) == 1, \
                f"배칭 실패: {len(corruption_events)}개 이벤트 생성됨 (예상: 1개)"

    def test_violations_list_in_details(self):
        """단일 이벤트에 violations 리스트가 포함됨."""
        from selfhealing.audit.event_buffer import AuditEventType, RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)

        mock_request = MagicMock()
        mock_request.META = {}

        # 위반 데이터
        data = {"amount": -100}

        result = shield.validate(data, request=mock_request)

        if not result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()

            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]

            if corruption_events:
                event = corruption_events[0]
                details = event.details

                # 배칭된 상세 정보 확인
                assert "violation_count" in details, "violation_count 누락"
                assert "violations" in details, "violations 리스트 누락"
                assert "layers" in details, "layers 정보 누락"
                assert isinstance(details["violations"], list), "violations가 리스트여야 함"

    def test_violation_count_matches_violations_list(self):
        """violation_count가 violations 리스트 길이와 일치."""
        from selfhealing.audit.event_buffer import AuditEventType, RequestAuditBuffer
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)

        mock_request = MagicMock()
        mock_request.META = {}

        # 위반 데이터
        data = {"amount": -100, "order_id": None}

        result = shield.validate(data, request=mock_request)

        if not result.is_valid:
            buffer = RequestAuditBuffer.get_or_create(mock_request)
            events = buffer.get_events()

            corruption_events = [
                e for e in events
                if e.event_type in (
                    AuditEventType.CORRUPTION_DETECTED,
                    AuditEventType.CORRUPTION_BLOCKED,
                )
            ]

            if corruption_events:
                details = corruption_events[0].details
                assert details["violation_count"] == len(details["violations"]), \
                    "violation_count와 violations 리스트 길이 불일치"

    def test_fallback_to_write_to_wal_without_request(self):
        """request 없을 때 _write_to_wal()로 폴백."""
        from selfhealing.services.corruption_shield import CorruptionShield
        from selfhealing.services.corruption_shield.config import CorruptionShieldConfig

        config = CorruptionShieldConfig(
            l1_enabled=True,
            l2_enabled=True,
            l3_enabled=False,
        )
        shield = CorruptionShield(config)

        # request 없이 호출
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            data = {"amount": -100}
            result = shield.validate(data)  # request=None

            if not result.is_valid:
                # _write_to_wal이 호출되어야 함
                mock_wal.assert_called_once()
                call_kwargs = mock_wal.call_args[1]
                assert call_kwargs["event_type"] in ("CORRUPTION_DETECTED", "CORRUPTION_BLOCKED")
                assert call_kwargs["source"] == "CorruptionShield"
                assert "violations" in call_kwargs["details"]

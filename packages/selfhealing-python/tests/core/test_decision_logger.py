"""
Tests for Decision Logger Skeleton

Verifies that:
- All structures exist
- All functions are callable
- No logs are produced
- No behavior is introduced
"""

import pytest

from selfhealing.core.decision_logger import (
    ReasonCode,
    EventType,
    DecisionLogger,
    log_enter_pre_decision_zone,
    log_intervention_evaluated,
    log_exit_pre_decision_zone,
)


class TestReasonCode:
    """Test ReasonCode enum."""

    def test_reason_codes_exist(self):
        """Verify all required reason codes exist."""
        assert ReasonCode.THRESHOLD_NOT_MET == "THRESHOLD_NOT_MET"
        assert ReasonCode.STABILITY_OK_NO_INTERVENTION == "STABILITY_OK_NO_INTERVENTION"
        assert ReasonCode.POLICY_CONSTRAINT_ACTIVE == "POLICY_CONSTRAINT_ACTIVE"
        assert ReasonCode.INTERVENTION_ALLOWED == "INTERVENTION_ALLOWED"

    def test_only_four_reason_codes(self):
        """Verify exactly 4 reason codes exist."""
        assert len(ReasonCode) == 4


class TestEventType:
    """Test EventType enum."""

    def test_event_types_exist(self):
        """Verify all required event types exist."""
        assert EventType.ENTER_PRE_DECISION_ZONE == "ENTER_PRE_DECISION_ZONE"
        assert EventType.INTERVENTION_EVALUATED == "INTERVENTION_EVALUATED"
        assert EventType.EXIT_PRE_DECISION_ZONE == "EXIT_PRE_DECISION_ZONE"

    def test_only_three_event_types(self):
        """Verify exactly 3 event types exist."""
        assert len(EventType) == 3


class TestModuleFunctions:
    """Test module-level functions."""

    def test_log_enter_pre_decision_zone_callable(self):
        """Verify function is callable and returns None."""
        result = log_enter_pre_decision_zone(service_name="test_service")
        assert result is None

    def test_log_enter_pre_decision_zone_with_policy_version(self):
        """Verify function accepts policy_version."""
        result = log_enter_pre_decision_zone(
            service_name="test_service",
            policy_version="v1.0.0",
        )
        assert result is None

    def test_log_intervention_evaluated_allowed(self):
        """Verify function accepts allowed=True."""
        result = log_intervention_evaluated(
            service_name="test_service",
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )
        assert result is None

    def test_log_intervention_evaluated_not_allowed(self):
        """Verify function accepts allowed=False with various reasons."""
        for reason in [
            ReasonCode.THRESHOLD_NOT_MET,
            ReasonCode.STABILITY_OK_NO_INTERVENTION,
            ReasonCode.POLICY_CONSTRAINT_ACTIVE,
        ]:
            result = log_intervention_evaluated(
                service_name="test_service",
                allowed=False,
                reason=reason,
            )
            assert result is None

    def test_log_exit_pre_decision_zone_callable(self):
        """Verify function is callable and returns None."""
        result = log_exit_pre_decision_zone(service_name="test_service")
        assert result is None


class TestDecisionLogger:
    """Test DecisionLogger class."""

    def test_initialization(self):
        """Verify class can be instantiated."""
        logger = DecisionLogger(service_name="test_service")
        assert logger._service_name == "test_service"
        assert logger._policy_version is None

    def test_initialization_with_policy_version(self):
        """Verify class accepts policy_version."""
        logger = DecisionLogger(
            service_name="test_service",
            policy_version="v1.0.0",
        )
        assert logger._service_name == "test_service"
        assert logger._policy_version == "v1.0.0"

    def test_enter_pre_decision_zone_returns_none(self):
        """Verify method returns None."""
        logger = DecisionLogger(service_name="test_service")
        result = logger.enter_pre_decision_zone()
        assert result is None

    def test_intervention_evaluated_returns_none(self):
        """Verify method returns None."""
        logger = DecisionLogger(service_name="test_service")
        result = logger.intervention_evaluated(
            allowed=True,
            reason=ReasonCode.INTERVENTION_ALLOWED,
        )
        assert result is None

    def test_exit_pre_decision_zone_returns_none(self):
        """Verify method returns None."""
        logger = DecisionLogger(service_name="test_service")
        result = logger.exit_pre_decision_zone()
        assert result is None


class TestLogOutput:
    """Verify logs are produced with correct structure."""

    def test_logs_produced_with_correct_fields(self, caplog):
        """Verify logs are produced with required fields."""
        import json
        import logging

        with caplog.at_level(logging.INFO, logger="selfhealing.decision_record"):
            log_enter_pre_decision_zone(service_name="test_service")
            log_intervention_evaluated(
                service_name="test_service",
                allowed=True,
                reason=ReasonCode.INTERVENTION_ALLOWED,
            )
            log_exit_pre_decision_zone(service_name="test_service")

        # Verify log records exist
        decision_records = [r for r in caplog.records if r.name == "selfhealing.decision_record"]
        assert len(decision_records) == 3

        # Verify ENTER event
        enter_record = json.loads(decision_records[0].message)
        assert enter_record["event"] == "ENTER_PRE_DECISION_ZONE"
        assert enter_record["service_name"] == "test_service"
        assert "timestamp" in enter_record

        # Verify INTERVENTION_EVALUATED event
        eval_record = json.loads(decision_records[1].message)
        assert eval_record["event"] == "INTERVENTION_EVALUATED"
        assert eval_record["allowed"] is True
        assert eval_record["reason"] == "INTERVENTION_ALLOWED"
        assert eval_record["service_name"] == "test_service"
        assert "timestamp" in eval_record

        # Verify EXIT event
        exit_record = json.loads(decision_records[2].message)
        assert exit_record["event"] == "EXIT_PRE_DECISION_ZONE"
        assert exit_record["service_name"] == "test_service"
        assert "timestamp" in exit_record

    def test_policy_version_included_when_provided(self, caplog):
        """Verify policy_version is included when provided."""
        import json
        import logging

        with caplog.at_level(logging.INFO, logger="selfhealing.decision_record"):
            log_enter_pre_decision_zone(
                service_name="test_service",
                policy_version="v1.0.0",
            )

        decision_records = [r for r in caplog.records if r.name == "selfhealing.decision_record"]
        assert len(decision_records) == 1

        record = json.loads(decision_records[0].message)
        assert record["policy_version"] == "v1.0.0"

    def test_only_allowed_fields_present(self, caplog):
        """Verify no extra fields beyond specification."""
        import json
        import logging

        allowed_fields_enter = {"event", "service_name", "policy_version", "timestamp"}
        allowed_fields_eval = {"event", "allowed", "reason", "service_name", "policy_version", "timestamp"}

        with caplog.at_level(logging.INFO, logger="selfhealing.decision_record"):
            log_enter_pre_decision_zone(service_name="test")
            log_intervention_evaluated(
                service_name="test",
                allowed=False,
                reason=ReasonCode.THRESHOLD_NOT_MET,
            )

        decision_records = [r for r in caplog.records if r.name == "selfhealing.decision_record"]

        enter_record = json.loads(decision_records[0].message)
        assert set(enter_record.keys()) == allowed_fields_enter

        eval_record = json.loads(decision_records[1].message)
        assert set(eval_record.keys()) == allowed_fields_eval


class TestImportFromCore:
    """Verify imports work from core module."""

    def test_import_from_core(self):
        """Verify all symbols are exported from core.__init__."""
        from selfhealing.core import (
            ReasonCode,
            EventType,
            DecisionLogger,
            log_enter_pre_decision_zone,
            log_intervention_evaluated,
            log_exit_pre_decision_zone,
        )

        assert ReasonCode is not None
        assert EventType is not None
        assert DecisionLogger is not None
        assert callable(log_enter_pre_decision_zone)
        assert callable(log_intervention_evaluated)
        assert callable(log_exit_pre_decision_zone)

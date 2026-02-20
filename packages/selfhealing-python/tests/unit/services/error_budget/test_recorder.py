"""
Tests for Freeze Decision Recorder

Covers:
- FreezeDecisionRecorder class
- Freeze acknowledged recording
- Override approved recording
- Freeze lifted recording
"""

import pytest
from unittest.mock import patch, MagicMock


class TestFreezeDecisionRecorderInit:
    """Tests for FreezeDecisionRecorder initialization."""
    
    def test_init_with_defaults(self):
        """Test initialization with defaults."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        
        recorder = FreezeDecisionRecorder()
        
        assert recorder.advisor is not None
        assert recorder._records == []
    
    def test_init_with_custom_advisor(self):
        """Test initialization with custom advisor."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = DeploymentPolicyAdvisor()
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        assert recorder.advisor is advisor
    
    def test_init_with_persist_function(self):
        """Test initialization with persist function."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        
        persist_fn = MagicMock()
        recorder = FreezeDecisionRecorder(persist_record=persist_fn)
        
        assert recorder._persist_record is persist_fn
    
    def test_init_with_metric_function(self):
        """Test initialization with metric function."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        
        metric_fn = MagicMock()
        recorder = FreezeDecisionRecorder(emit_metric=metric_fn)
        
        assert recorder._emit_metric is metric_fn


class TestRecordFreezeAcknowledged:
    """Tests for record_freeze_acknowledged method."""
    
    def test_records_freeze_acknowledged(self):
        """Test recording freeze acknowledged."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_freeze_acknowledged(
            decided_by="admin@example.com",
            justification="High error rate observed",
        )
        
        assert record.decision_type == "freeze_acknowledged"
        assert record.decided_by == "admin@example.com"
        assert record.justification == "High error rate observed"
    
    def test_freeze_acknowledged_stored_in_memory(self):
        """Test freeze acknowledged stored in memory."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        assert len(recorder._records) == 1
    
    def test_freeze_acknowledged_calls_persist(self):
        """Test freeze acknowledged calls persist function."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        persist_fn = MagicMock()
        recorder = FreezeDecisionRecorder(advisor=advisor, persist_record=persist_fn)
        
        recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        persist_fn.assert_called_once()


class TestRecordOverrideApproved:
    """Tests for record_override_approved method."""
    
    def test_records_override_approved(self):
        """Test recording override approved."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.enums import OverrideType
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        advisor._active_overrides = {}  # Add required attribute
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 10.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_override_approved(
            decided_by="cto@example.com",
            justification="Critical security fix",
            override_type=OverrideType.SECURITY_PATCH,
            deployment_id="deploy-123",
        )
        
        assert record.decision_type == "override_approved"
        assert record.decided_by == "cto@example.com"
        assert record.override_type == OverrideType.SECURITY_PATCH
        assert record.deployment_id == "deploy-123"
    
    def test_override_with_expiration(self):
        """Test override with custom expiration."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.enums import OverrideType
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        advisor._active_overrides = {}  # Add required attribute
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 10.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_override_approved(
            decided_by="cto@example.com",
            justification="Hotfix",
            override_type=OverrideType.HOTFIX,
            expires_hours=2,
        )
        
        assert record is not None
    
    def test_override_with_deployment_name(self):
        """Test override with deployment name."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.enums import OverrideType
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        advisor._active_overrides = {}  # Add required attribute
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 10.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_override_approved(
            decided_by="admin",
            justification="Rollback",
            override_type=OverrideType.ROLLBACK,
            deployment_name="payment-service-v2.1",
        )
        
        assert record.deployment_name == "payment-service-v2.1"


class TestRecordFreezeListed:
    """Tests for record_freeze_lifted method."""
    
    def test_records_freeze_lifted(self):
        """Test recording freeze lifted."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        advisor._active_overrides = {}  # Add required attribute
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 80.0
        mock_verdict.status = "proceed"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_freeze_lifted(
            decided_by="admin@example.com",
            justification="Error budget recovered",
        )
        
        assert record.decision_type == "freeze_lifted"
        assert record.decided_by == "admin@example.com"
        assert record.justification == "Error budget recovered"


class TestAuditTrail:
    """Tests for audit trail functionality."""
    
    def test_records_have_decision_id(self):
        """Test all records have decision ID."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        assert record.decision_id is not None
        assert len(record.decision_id) > 0
    
    def test_records_have_timestamp(self):
        """Test all records have timestamp."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        assert record.decided_at is not None
    
    def test_records_have_budget_status(self):
        """Test all records include budget status."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = "freeze_recommended"
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        recorder = FreezeDecisionRecorder(advisor=advisor)
        
        record = recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        assert record.budget_remaining_percent == 15.0
    
    def test_emit_metric_called(self):
        """Test emit_metric called on record."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.enums import FreezeStatus
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = FreezeStatus.FREEZE_RECOMMENDED
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        emit_fn = MagicMock()
        recorder = FreezeDecisionRecorder(advisor=advisor, emit_metric=emit_fn)
        
        recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        emit_fn.assert_called()
    
    def test_emit_otel_event_called(self):
        """Test emit_otel_event called on record."""
        from selfhealing.services.error_budget.recorder import FreezeDecisionRecorder
        from selfhealing.services.error_budget.advisor import DeploymentPolicyAdvisor
        from selfhealing.services.error_budget.enums import FreezeStatus
        
        advisor = MagicMock(spec=DeploymentPolicyAdvisor)
        mock_verdict = MagicMock()
        mock_verdict.budget_status.budget_remaining_percent = 15.0
        mock_verdict.status = FreezeStatus.FREEZE_RECOMMENDED
        advisor.get_deployment_verdict.return_value = mock_verdict
        
        otel_fn = MagicMock()
        recorder = FreezeDecisionRecorder(advisor=advisor, emit_otel_event=otel_fn)
        
        recorder.record_freeze_acknowledged(
            decided_by="admin",
            justification="Test",
        )
        
        otel_fn.assert_called()

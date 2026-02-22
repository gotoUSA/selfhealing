"""
Recovery Audit 단위 테스트.

Phase 5 구현 검증:
- DangerousForceRecoveryAuditEntry 생성 및 직렬화
- RecoveryAuditRecorder 기록 기능
- record_dangerous_force_recovery() 편의 함수

selfhealing 패키지만 import하는 순수 단위 테스트입니다.

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#Phase5
"""


from selfhealing.services.coordination.recovery_audit import (
    DangerousForceRecoveryAuditEntry,
    ForceRecoveryType,
    RecoveryAuditEntry,
    RecoveryAuditEventType,
    RecoveryAuditRecorder,
    get_recovery_audit_recorder,
    record_dangerous_force_recovery,
    record_recovery_event,
)


class TestForceRecoveryType:
    """ForceRecoveryType enum 테스트."""

    def test_enum_values_exist(self):
        """모든 enum 값이 존재하는지 확인."""
        assert ForceRecoveryType.SKIP_MANUAL_APPROVAL == "skip_manual_approval"
        assert ForceRecoveryType.SKIP_STABILITY_CHECK == "skip_stability_check"
        assert ForceRecoveryType.OVERRIDE_CIRCUIT_BREAKER == "override_circuit_breaker"
        assert ForceRecoveryType.FORCE_GOVERNANCE_NORMAL == "force_governance_normal"
        assert ForceRecoveryType.EMERGENCY_BUDGET_RESET == "emergency_budget_reset"
        assert ForceRecoveryType.OTHER == "other"


class TestRecoveryAuditEventType:
    """RecoveryAuditEventType enum 테스트."""

    def test_enum_values_exist(self):
        """모든 enum 값이 존재하는지 확인."""
        assert RecoveryAuditEventType.RECOVERY_STARTED == "recovery_started"
        assert RecoveryAuditEventType.RECOVERY_STEP_EXECUTED == "recovery_step_executed"
        assert RecoveryAuditEventType.RECOVERY_COMPLETED == "recovery_completed"
        assert RecoveryAuditEventType.RECOVERY_ABORTED == "recovery_aborted"
        assert RecoveryAuditEventType.DANGEROUS_FORCE_RECOVERY == "dangerous_force_recovery"
        assert RecoveryAuditEventType.MANUAL_APPROVAL_GRANTED == "manual_approval_granted"
        assert RecoveryAuditEventType.RE_ESCALATION == "re_escalation"


class TestDangerousForceRecoveryAuditEntry:
    """DangerousForceRecoveryAuditEntry 모델 테스트."""

    def test_create_entry_with_defaults(self):
        """기본값으로 엔트리 생성."""
        entry = DangerousForceRecoveryAuditEntry()

        assert entry.audit_id.startswith("audit-force-")
        assert entry.event_type == RecoveryAuditEventType.DANGEROUS_FORCE_RECOVERY
        assert entry.force_type == ForceRecoveryType.OTHER
        assert entry.executed_at is not None

    def test_create_entry_with_all_fields(self):
        """모든 필드를 지정하여 엔트리 생성."""
        entry = DangerousForceRecoveryAuditEntry(
            force_type=ForceRecoveryType.SKIP_MANUAL_APPROVAL,
            session_id="test-session-123",
            namespace="payment",
            trigger_level="LEVEL_3",
            executed_by="operator_kim",
            reason="긴급 복구 필요",
            justification="고객 결제 장애로 즉각 조치 필요",
            skipped_checks=["manual_approval", "stability_check"],
            previous_state={"mode": "STRICT"},
            resulting_state={"mode": "NORMAL"},
            cascade_event_id="cascade-abc123",
            metadata={"customer_impact": "high"},
        )

        assert entry.force_type == ForceRecoveryType.SKIP_MANUAL_APPROVAL
        assert entry.session_id == "test-session-123"
        assert entry.namespace == "payment"
        assert entry.trigger_level == "LEVEL_3"
        assert entry.executed_by == "operator_kim"
        assert len(entry.skipped_checks) == 2
        assert entry.cascade_event_id == "cascade-abc123"

    def test_to_dict_and_from_dict(self):
        """직렬화/역직렬화 테스트."""
        original = DangerousForceRecoveryAuditEntry(
            force_type=ForceRecoveryType.OVERRIDE_CIRCUIT_BREAKER,
            session_id="session-456",
            namespace="order",
            executed_by="admin",
            reason="Circuit breaker 우회",
            justification="시스템 정상 확인됨",
        )

        data = original.to_dict()

        assert data["force_type"] == "override_circuit_breaker"
        assert data["session_id"] == "session-456"
        assert "executed_at" in data

        # 역직렬화
        restored = DangerousForceRecoveryAuditEntry.from_dict(data)

        assert restored.force_type == ForceRecoveryType.OVERRIDE_CIRCUIT_BREAKER
        assert restored.session_id == "session-456"
        assert restored.namespace == "order"


class TestRecoveryAuditEntry:
    """RecoveryAuditEntry 모델 테스트."""

    def test_create_entry_with_defaults(self):
        """기본값으로 엔트리 생성."""
        entry = RecoveryAuditEntry()

        assert entry.audit_id.startswith("audit-recovery-")
        assert entry.event_type == RecoveryAuditEventType.RECOVERY_STARTED
        assert entry.executed_by == "system"
        assert entry.success is True

    def test_create_step_executed_entry(self):
        """단계 실행 엔트리 생성."""
        entry = RecoveryAuditEntry(
            event_type=RecoveryAuditEventType.RECOVERY_STEP_EXECUTED,
            session_id="session-789",
            namespace="global",
            step_type="BUDGET_RESET",
            step_order=1,
            success=True,
            duration_ms=150.5,
        )

        assert entry.event_type == RecoveryAuditEventType.RECOVERY_STEP_EXECUTED
        assert entry.step_type == "BUDGET_RESET"
        assert entry.step_order == 1
        assert entry.duration_ms == 150.5

    def test_create_failed_step_entry(self):
        """실패한 단계 엔트리 생성."""
        entry = RecoveryAuditEntry(
            event_type=RecoveryAuditEventType.RECOVERY_STEP_FAILED,
            session_id="session-fail",
            namespace="global",
            step_type="HEALTH_CHECK",
            step_order=2,
            success=False,
            error_message="Health check timeout",
        )

        assert entry.success is False
        assert entry.error_message == "Health check timeout"


class TestRecoveryAuditRecorder:
    """RecoveryAuditRecorder 테스트."""

    def test_record_recovery_event(self):
        """일반 복구 이벤트 기록."""
        recorder = RecoveryAuditRecorder()

        entry = recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STARTED,
            session_id="test-session",
            namespace="global",
            executed_by="system",
        )

        assert entry.event_type == RecoveryAuditEventType.RECOVERY_STARTED
        assert entry.session_id == "test-session"

        # 메모리에 저장되었는지 확인
        recent = recorder.get_recent_events(limit=10)
        assert len(recent) >= 1

    def test_record_dangerous_force_recovery(self):
        """위험한 강제 복구 기록."""
        notification_received = []

        def notification_callback(entry):
            notification_received.append(entry)

        recorder = RecoveryAuditRecorder(notification_callback=notification_callback)

        entry = recorder.record_dangerous_force_recovery(
            force_type=ForceRecoveryType.SKIP_MANUAL_APPROVAL,
            session_id="force-session",
            namespace="payment",
            executed_by="admin_user",
            reason="긴급 복구",
            justification="고객 장애 해결",
        )

        assert entry.force_type == ForceRecoveryType.SKIP_MANUAL_APPROVAL
        assert entry.executed_by == "admin_user"

        # 알림 콜백 호출 확인
        assert len(notification_received) == 1
        assert notification_received[0].session_id == "force-session"

    def test_get_recent_events_with_filter(self):
        """필터로 이벤트 조회."""
        recorder = RecoveryAuditRecorder()

        # 여러 이벤트 기록
        recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STARTED,
            session_id="session-a",
            namespace="global",
        )
        recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STEP_EXECUTED,
            session_id="session-a",
            namespace="global",
        )
        recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STARTED,
            session_id="session-b",
            namespace="global",
        )

        # 세션 ID로 필터
        events_a = recorder.get_recent_events(session_id="session-a")
        assert len(events_a) == 2

        # 이벤트 유형으로 필터
        started_events = recorder.get_recent_events(
            event_types=[RecoveryAuditEventType.RECOVERY_STARTED]
        )
        assert len(started_events) == 2


class TestConvenienceFunctions:
    """편의 함수 테스트."""

    def test_record_dangerous_force_recovery_function(self):
        """record_dangerous_force_recovery 편의 함수."""
        entry = record_dangerous_force_recovery(
            force_type=ForceRecoveryType.EMERGENCY_BUDGET_RESET,
            session_id="conv-session",
            namespace="test",
            executed_by="test_user",
            reason="테스트",
            justification="단위 테스트",
        )

        assert entry.force_type == ForceRecoveryType.EMERGENCY_BUDGET_RESET
        assert entry.executed_by == "test_user"

    def test_record_recovery_event_function(self):
        """record_recovery_event 편의 함수."""
        entry = record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_COMPLETED,
            session_id="conv-complete",
            namespace="test",
        )

        assert entry.event_type == RecoveryAuditEventType.RECOVERY_COMPLETED


class TestSingletonAccess:
    """싱글톤 접근 테스트."""

    def test_get_recovery_audit_recorder_returns_same_instance(self):
        """싱글톤이 동일 인스턴스를 반환하는지 확인."""
        recorder1 = get_recovery_audit_recorder()
        recorder2 = get_recovery_audit_recorder()

        assert recorder1 is recorder2

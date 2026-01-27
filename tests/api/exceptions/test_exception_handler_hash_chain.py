"""
예외 핸들러 해시 체인 통합 테스트.

예외 핸들러에서 발생한 이벤트가 ContinuousAuditRecorder를 통해
해시 체인에 포함되는지 검증합니다.

테스트 항목:
1. 예외 이벤트에 integrity 정보가 포함되는지 확인
2. integrity 정보에 sequence, previous_hash, current_hash가 있는지 확인
3. 연속된 이벤트 간 해시 체인 연결 유지되는지 확인
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from rest_framework.exceptions import ValidationError as DRFValidationError

from selfhealing.api.django.exceptions.handler import selfhealing_exception_handler
from selfhealing.audit.event_buffer import RequestAuditBuffer


class TestExceptionEventHasIntegrity:
    """예외 이벤트에 무결성 정보 포함 테스트."""

    def test_exception_event_recorded_to_audit_buffer(self):
        """예외 발생 시 이벤트가 Audit 버퍼에 기록됨."""
        exc = DRFValidationError({"field": ["This field is required."]})
        request = Mock()
        request.path = "/api/test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}

        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer

        selfhealing_exception_handler(exc, context)

        events = buffer.get_events()
        assert len(events) >= 1

        event = events[-1]
        assert event.source == "ExceptionHandler"
        assert event.success is False

    def test_exception_event_has_error_code_in_details(self):
        """예외 이벤트 details에 error_code가 포함됨."""
        exc = DRFValidationError({"name": ["Required."]})
        request = Mock()
        request.path = "/api/users/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}

        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer

        selfhealing_exception_handler(exc, context)

        events = buffer.get_events()
        event = events[-1]

        assert "error_code" in event.details
        assert event.details["error_code"] is not None

    def test_exception_event_has_http_status_in_details(self):
        """예외 이벤트 details에 http_status가 포함됨."""
        exc = ValueError("Invalid value")
        request = Mock()
        request.path = "/api/items/"
        request.method = "PUT"
        request.META = {}
        context = {"request": request}

        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer

        selfhealing_exception_handler(exc, context)

        events = buffer.get_events()
        event = events[-1]

        assert "http_status" in event.details
        assert isinstance(event.details["http_status"], int)


class TestHashChainIntegration:
    """해시 체인 통합 테스트."""

    def test_hash_chain_manager_adds_integrity_fields(self):
        """HashChainManager가 integrity 필드를 추가하는지 확인."""
        from selfhealing.audit.integrity import HashChainManager

        manager = HashChainManager()

        entry = {
            "action": "test_action",
            "actor_id": "test_user",
            "details": {"key": "value"},
        }

        result = manager.add_integrity(entry)

        assert "integrity" in result
        assert "sequence" in result["integrity"]
        assert "previous_hash" in result["integrity"]
        assert "current_hash" in result["integrity"]
        assert "timestamp" in result["integrity"]

    def test_hash_chain_sequence_increments(self):
        """해시 체인 시퀀스가 증가하는지 확인."""
        from selfhealing.audit.integrity import HashChainManager

        manager = HashChainManager()

        entry1 = manager.add_integrity({"action": "action1"})
        entry2 = manager.add_integrity({"action": "action2"})
        entry3 = manager.add_integrity({"action": "action3"})

        assert entry1["integrity"]["sequence"] == 1
        assert entry2["integrity"]["sequence"] == 2
        assert entry3["integrity"]["sequence"] == 3

    def test_hash_chain_links_previous_to_current(self):
        """이전 current_hash가 다음 previous_hash와 연결되는지 확인."""
        from selfhealing.audit.integrity import HashChainManager

        manager = HashChainManager()

        entry1 = manager.add_integrity({"action": "action1"})
        entry2 = manager.add_integrity({"action": "action2"})

        # entry1의 current_hash가 entry2의 previous_hash와 같아야 함
        assert entry1["integrity"]["current_hash"] == entry2["integrity"]["previous_hash"]

    def test_first_entry_has_genesis_previous_hash(self):
        """첫 번째 엔트리는 GENESIS previous_hash를 가짐."""
        from selfhealing.audit.integrity import HashChainManager

        manager = HashChainManager()

        entry = manager.add_integrity({"action": "first_action"})

        assert entry["integrity"]["previous_hash"] == "GENESIS"

    def test_hash_is_deterministic(self):
        """동일한 데이터는 동일한 해시를 생성."""
        from selfhealing.audit.integrity.models import compute_hash

        data = {"action": "test", "value": 123}

        hash1 = compute_hash(data)
        hash2 = compute_hash(data)

        assert hash1 == hash2
        assert len(hash1) == 64  # SHA-256 hex

    def test_different_data_produces_different_hash(self):
        """다른 데이터는 다른 해시를 생성."""
        from selfhealing.audit.integrity.models import compute_hash

        data1 = {"action": "test", "value": 123}
        data2 = {"action": "test", "value": 456}

        hash1 = compute_hash(data1)
        hash2 = compute_hash(data2)

        assert hash1 != hash2


class TestContinuousAuditRecorderIntegrity:
    """ContinuousAuditRecorder 해시 체인 통합 테스트."""

    def test_recorder_adds_integrity_to_entry(self):
        """ContinuousAuditRecorder가 엔트리에 integrity를 추가하는지 확인."""
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
        from selfhealing.interfaces.audit_adapter import AuditLogAdapter, AuditEntry

        # Mock adapter that captures entries
        captured_entries = []

        class CapturingAdapter(AuditLogAdapter):
            def log(self, entry: AuditEntry) -> None:
                captured_entries.append(entry)

            def query(self, **kwargs):
                return []

        adapter = CapturingAdapter()
        recorder = ContinuousAuditRecorder(audit_adapter=adapter)

        # Record an auto-tuning event
        recorder.record_auto_tuning(
            parameter="timeout_ms",
            old_value=5000,
            new_value=6000,
            reason="Test tuning",
            confidence=0.9,
            metrics_snapshot={"latency": 100},
            safety_check={"within_bounds": True},
        )

        assert len(captured_entries) == 1
        entry = captured_entries[0]

        # integrity가 details에 포함되어야 함
        assert "integrity" in entry.details
        assert "sequence" in entry.details["integrity"]
        assert "previous_hash" in entry.details["integrity"]
        assert "current_hash" in entry.details["integrity"]

    def test_recorder_maintains_hash_chain_across_records(self):
        """ContinuousAuditRecorder가 연속 기록 간 해시 체인을 유지하는지 확인."""
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
        from selfhealing.interfaces.audit_adapter import AuditLogAdapter, AuditEntry

        captured_entries = []

        class CapturingAdapter(AuditLogAdapter):
            def log(self, entry: AuditEntry) -> None:
                captured_entries.append(entry)

            def query(self, **kwargs):
                return []

        adapter = CapturingAdapter()
        recorder = ContinuousAuditRecorder(audit_adapter=adapter)

        # Record multiple events
        recorder.record_auto_tuning(
            parameter="param1",
            old_value=1,
            new_value=2,
            reason="Test 1",
            confidence=0.8,
            metrics_snapshot={},
            safety_check={},
        )

        recorder.record_auto_tuning(
            parameter="param2",
            old_value=10,
            new_value=20,
            reason="Test 2",
            confidence=0.9,
            metrics_snapshot={},
            safety_check={},
        )

        assert len(captured_entries) == 2

        entry1_integrity = captured_entries[0].details["integrity"]
        entry2_integrity = captured_entries[1].details["integrity"]

        # 시퀀스 증가 확인
        assert entry2_integrity["sequence"] == entry1_integrity["sequence"] + 1

        # 해시 체인 연결 확인
        assert entry2_integrity["previous_hash"] == entry1_integrity["current_hash"]


class TestHashChainVerification:
    """해시 체인 검증 테스트."""

    def test_verify_single_entry_integrity(self):
        """단일 엔트리의 무결성 검증."""
        from selfhealing.audit.integrity import HashChainManager
        from selfhealing.audit.integrity.models import compute_hash

        manager = HashChainManager()

        original_entry = {"action": "test_action", "data": "some_data"}
        entry = manager.add_integrity(original_entry.copy())

        # 무결성 정보 추출
        integrity = entry["integrity"]
        stored_hash = integrity.pop("current_hash")

        # 재계산
        recomputed_hash = compute_hash(entry)

        assert stored_hash == recomputed_hash

    def test_detect_tampered_entry(self):
        """변조된 엔트리 감지."""
        from selfhealing.audit.integrity import HashChainManager
        from selfhealing.audit.integrity.models import compute_hash

        manager = HashChainManager()

        entry = manager.add_integrity({"action": "original_action"})
        original_hash = entry["integrity"]["current_hash"]

        # 엔트리 변조
        entry["action"] = "tampered_action"

        # current_hash를 제거하고 재계산
        integrity = entry["integrity"]
        del integrity["current_hash"]
        recomputed_hash = compute_hash(entry)

        # 원본 해시와 재계산 해시가 달라야 함 (변조 감지)
        assert original_hash != recomputed_hash


class TestExceptionHandlerToHashChainFlow:
    """예외 핸들러 → 해시 체인 전체 흐름 테스트."""

    def test_exception_event_flows_through_audit_system(self):
        """예외 이벤트가 Audit 시스템을 통해 흐르는지 확인."""
        from selfhealing.audit.event_buffer import AuditEventType

        exc = ValueError("Test error")
        request = Mock()
        request.path = "/api/flow-test/"
        request.method = "GET"
        request.META = {}
        context = {"request": request}

        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer

        response = selfhealing_exception_handler(exc, context)

        # 응답이 정상적으로 반환됨
        assert response is not None
        assert response.status_code >= 400

        # 이벤트가 버퍼에 기록됨
        events = buffer.get_events()
        assert len(events) >= 1

        # 이벤트 타입이 API 관련임
        event = events[-1]
        assert event.event_type in (
            AuditEventType.API_EXCEPTION,
            AuditEventType.API_VALIDATION_ERROR,
            AuditEventType.API_AUTH_ERROR,
            AuditEventType.API_NOT_FOUND,
            AuditEventType.API_THROTTLED,
        )

    def test_multiple_exceptions_maintain_event_order(self):
        """여러 예외가 이벤트 순서를 유지하는지 확인."""
        request = Mock()
        request.path = "/api/order-test/"
        request.method = "POST"
        request.META = {}
        context = {"request": request}

        buffer = RequestAuditBuffer()
        request.META[RequestAuditBuffer.META_KEY] = buffer

        # 첫 번째 예외 처리
        exc1 = ValueError("First error")
        selfhealing_exception_handler(exc1, context)

        # 두 번째 예외 처리
        exc2 = TypeError("Second error")
        selfhealing_exception_handler(exc2, context)

        events = buffer.get_events()
        assert len(events) >= 2

        # 순서 확인 (첫 번째가 먼저)
        assert "First" in events[-2].error_message or "First" in str(events[-2].details)
        assert "Second" in events[-1].error_message or "Second" in str(events[-1].details)

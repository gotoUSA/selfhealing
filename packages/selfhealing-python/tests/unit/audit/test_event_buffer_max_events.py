"""
RequestAuditBuffer MAX_EVENTS_PER_REQUEST 제한 테스트.

단일 요청에서 무제한 이벤트 누적으로 인한 메모리 폭발을 방지하기 위해
RequestAuditBuffer에 최대 이벤트 수 제한 기능을 테스트합니다.

테스트 항목:
- 기본 max_events 제한 (100)
- 환경변수를 통한 max_events 설정
- 한도 초과 시 이벤트 버려짐 (truncation)
- 마지막 이벤트에 truncated 메타데이터 추가
- to_dict()에 truncation 정보 포함
"""

import os
from unittest.mock import patch

import pytest

from selfhealing.audit.event_buffer import (
    RequestAuditBuffer,
    AuditEvent,
    AuditEventType,
)


class TestRequestAuditBufferMaxEvents:
    """RequestAuditBuffer 최대 이벤트 수 제한 테스트."""

    def test_default_max_events_is_100(self):
        """기본 max_events 값은 100."""
        buffer = RequestAuditBuffer()
        assert buffer.max_events == 100
        assert buffer._max_events == 100

    def test_max_events_custom_value(self):
        """생성자 인자로 max_events 커스텀 설정."""
        buffer = RequestAuditBuffer(max_events=50)
        assert buffer.max_events == 50

    def test_max_events_from_environment_variable(self):
        """SELFHEALING_MAX_EVENTS_PER_REQUEST 환경변수로 설정."""
        with patch.dict(os.environ, {"SELFHEALING_MAX_EVENTS_PER_REQUEST": "25"}):
            buffer = RequestAuditBuffer()
            assert buffer.max_events == 25

    def test_add_event_under_limit(self):
        """한도 미만일 때 이벤트 정상 추가."""
        buffer = RequestAuditBuffer(max_events=5)

        for i in range(5):
            event = AuditEvent(
                event_type=AuditEventType.GENERIC,
                source=f"test_{i}",
            )
            result = buffer.add_event(event)
            assert result is True

        assert buffer.event_count() == 5
        assert buffer.truncated_count == 0
        assert buffer.is_truncated is False

    def test_add_event_over_limit_returns_false(self):
        """한도 초과 시 add_event()는 False 반환."""
        buffer = RequestAuditBuffer(max_events=3)

        # 3개까지 정상 추가
        for i in range(3):
            event = AuditEvent(event_type=AuditEventType.GENERIC, source=f"test_{i}")
            assert buffer.add_event(event) is True

        # 4번째부터 버려짐
        event = AuditEvent(event_type=AuditEventType.GENERIC, source="test_overflow")
        result = buffer.add_event(event)

        assert result is False
        assert buffer.event_count() == 3
        assert buffer.truncated_count == 1
        assert buffer.is_truncated is True

    def test_add_method_returns_none_when_over_limit(self):
        """한도 초과 시 add() 메서드는 None 반환."""
        buffer = RequestAuditBuffer(max_events=2)

        # 2개까지 정상 추가
        event1 = buffer.add(event_type=AuditEventType.ERROR_DETECTED, source="test1")
        event2 = buffer.add(event_type=AuditEventType.ERROR_DETECTED, source="test2")

        assert event1 is not None
        assert event2 is not None

        # 3번째부터 None 반환
        event3 = buffer.add(event_type=AuditEventType.ERROR_DETECTED, source="test3")
        assert event3 is None

        assert buffer.event_count() == 2
        assert buffer.truncated_count == 1

    def test_truncated_count_increments(self):
        """한도 초과 시 truncated_count 증가."""
        buffer = RequestAuditBuffer(max_events=2)

        buffer.add(event_type=AuditEventType.GENERIC, source="test1")
        buffer.add(event_type=AuditEventType.GENERIC, source="test2")

        # 5개 더 추가 시도
        for i in range(5):
            buffer.add(event_type=AuditEventType.GENERIC, source=f"overflow_{i}")

        assert buffer.event_count() == 2
        assert buffer.truncated_count == 5

    def test_last_event_has_truncated_metadata(self):
        """마지막 이벤트에 _truncated 메타데이터 추가."""
        buffer = RequestAuditBuffer(max_events=2)

        buffer.add(event_type=AuditEventType.GENERIC, source="test1", details={"key": "value1"})
        buffer.add(event_type=AuditEventType.GENERIC, source="test2", details={"key": "value2"})

        # 추가 이벤트 시도
        buffer.add(event_type=AuditEventType.GENERIC, source="overflow1")
        buffer.add(event_type=AuditEventType.GENERIC, source="overflow2")

        # 마지막 이벤트 확인
        last_event = buffer.events[-1]
        assert last_event.details.get("_truncated") is True
        assert last_event.details.get("_truncated_count") == 2
        # 원래 details도 유지
        assert last_event.details.get("key") == "value2"

    def test_to_dict_includes_truncation_info(self):
        """to_dict()에 truncation 정보 포함."""
        buffer = RequestAuditBuffer(max_events=3)

        for i in range(5):
            buffer.add(event_type=AuditEventType.GENERIC, source=f"test_{i}")

        result = buffer.to_dict()

        assert result["event_count"] == 3
        assert result["truncated"] is True
        assert result["truncated_count"] == 2
        assert result["max_events"] == 3

    def test_to_dict_no_truncation_info_when_not_truncated(self):
        """truncation 없으면 to_dict()에 관련 필드 없음."""
        buffer = RequestAuditBuffer(max_events=10)

        buffer.add(event_type=AuditEventType.GENERIC, source="test")

        result = buffer.to_dict()

        assert result["event_count"] == 1
        assert "truncated" not in result
        assert "truncated_count" not in result
        assert "max_events" not in result

    def test_clear_resets_truncated_count(self):
        """clear() 호출 시 truncated_count 초기화."""
        buffer = RequestAuditBuffer(max_events=2)

        buffer.add(event_type=AuditEventType.GENERIC, source="test1")
        buffer.add(event_type=AuditEventType.GENERIC, source="test2")
        buffer.add(event_type=AuditEventType.GENERIC, source="overflow")

        assert buffer.truncated_count == 1

        buffer.clear()

        assert buffer.event_count() == 0
        assert buffer.truncated_count == 0
        assert buffer.is_truncated is False

    def test_max_events_zero_rejects_all(self):
        """max_events=0이면 모든 이벤트 거부."""
        buffer = RequestAuditBuffer(max_events=0)

        result = buffer.add(event_type=AuditEventType.GENERIC, source="test")

        assert result is None
        assert buffer.event_count() == 0
        assert buffer.truncated_count == 1

    def test_max_events_negative_treated_as_zero(self):
        """max_events 음수는 0과 동일하게 동작."""
        buffer = RequestAuditBuffer(max_events=-1)

        result = buffer.add(event_type=AuditEventType.GENERIC, source="test")

        # 음수도 >= 연산자에서 0 이상이므로 거부됨
        assert result is None


class TestRequestAuditBufferProperties:
    """RequestAuditBuffer 속성 접근자 테스트."""

    def test_max_events_property(self):
        """max_events 속성 접근."""
        buffer = RequestAuditBuffer(max_events=42)
        assert buffer.max_events == 42

    def test_truncated_count_property(self):
        """truncated_count 속성 접근."""
        buffer = RequestAuditBuffer(max_events=1)
        buffer.add(event_type=AuditEventType.GENERIC, source="test1")
        buffer.add(event_type=AuditEventType.GENERIC, source="test2")

        assert buffer.truncated_count == 1

    def test_is_truncated_property(self):
        """is_truncated 속성 접근."""
        buffer = RequestAuditBuffer(max_events=1)

        assert buffer.is_truncated is False

        buffer.add(event_type=AuditEventType.GENERIC, source="test1")
        buffer.add(event_type=AuditEventType.GENERIC, source="test2")

        assert buffer.is_truncated is True

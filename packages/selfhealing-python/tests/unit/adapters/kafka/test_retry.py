"""
Non-Blocking Retry 핸들러 단위 테스트.

재시도 토픽 전송, DLQ 이동, 헤더 처리를 테스트합니다.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from selfhealing.adapters.kafka.retry import (
    DLQ_REASON_HEADER,
    ORIGINAL_TOPIC_HEADER,
    RETRY_COUNT_HEADER,
    RETRY_DELAY_HEADER,
    NonBlockingRetryHandler,
    RetryTopicConfig,
)


class TestRetryTopicConfig:
    """RetryTopicConfig 단위 테스트."""

    def test_default_values(self) -> None:
        """기본값 확인."""
        config = RetryTopicConfig(main_topic="audit.events")

        assert config.main_topic == "audit.events"
        assert config.retry_delays == [60, 300, 900]  # 1분, 5분, 15분
        assert config.max_retries == 3

    def test_custom_retry_delays(self) -> None:
        """커스텀 재시도 지연."""
        config = RetryTopicConfig(
            main_topic="audit.events",
            retry_delays=[30, 60, 120, 240],
        )

        assert len(config.retry_delays) == 4
        assert config.max_retries == 4

    def test_retry_topics_generated(self) -> None:
        """재시도 토픽 이름 생성."""
        config = RetryTopicConfig(
            main_topic="audit.events",
            retry_delays=[60, 300, 900],
        )

        topics = config.retry_topics

        assert len(topics) == 3
        assert topics[0] == "audit.events.retry.1"
        assert topics[1] == "audit.events.retry.2"
        assert topics[2] == "audit.events.retry.3"

    def test_final_dlq_topic_default(self) -> None:
        """기본 DLQ 토픽 이름."""
        config = RetryTopicConfig(main_topic="audit.events")

        assert config.final_dlq_topic == "audit.events.dlq"

    def test_final_dlq_topic_custom(self) -> None:
        """커스텀 DLQ 토픽 이름."""
        config = RetryTopicConfig(
            main_topic="audit.events",
            dlq_topic="custom.dlq",
        )

        assert config.final_dlq_topic == "custom.dlq"

    def test_empty_retry_delays(self) -> None:
        """빈 재시도 지연 시 기본값."""
        config = RetryTopicConfig(
            main_topic="audit.events",
            retry_delays=[],
        )

        assert config.retry_delays == [60]
        assert config.max_retries == 1


class TestNonBlockingRetryHandler:
    """NonBlockingRetryHandler 단위 테스트."""

    @pytest.fixture
    def mock_producer(self) -> MagicMock:
        """Mock Producer."""
        producer = MagicMock()
        producer.publish = MagicMock(return_value=True)
        return producer

    @pytest.fixture
    def retry_config(self) -> RetryTopicConfig:
        """테스트용 설정."""
        return RetryTopicConfig(
            main_topic="audit.events",
            retry_delays=[60, 300, 900],
        )

    def test_first_failure_sends_to_retry_1(self, mock_producer, retry_config) -> None:
        """첫 실패 시 retry.1로 전송."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123", "data": "test"}
        error = ValueError("Processing failed")

        success = handler.handle_failure(
            message=message,
            retry_count=0,
            error=error,
            key="order-123",
        )

        assert success is True
        mock_producer.publish.assert_called_once()

        call_kwargs = mock_producer.publish.call_args[1]
        assert call_kwargs["topic"] == "audit.events.retry.1"
        assert call_kwargs["key"] == "order-123"

    def test_second_failure_sends_to_retry_2(self, mock_producer, retry_config) -> None:
        """두 번째 실패 시 retry.2로 전송."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}
        error = ValueError("Processing failed again")

        handler.handle_failure(
            message=message,
            retry_count=1,
            error=error,
        )

        call_kwargs = mock_producer.publish.call_args[1]
        assert call_kwargs["topic"] == "audit.events.retry.2"

    def test_max_retries_exceeded_sends_to_dlq(self, mock_producer, retry_config) -> None:
        """최대 재시도 초과 시 DLQ로 전송."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}
        error = ValueError("Final failure")

        handler.handle_failure(
            message=message,
            retry_count=3,  # max_retries = 3
            error=error,
        )

        call_kwargs = mock_producer.publish.call_args[1]
        assert call_kwargs["topic"] == "audit.events.dlq"

    def test_retry_info_added_to_message(self, mock_producer, retry_config) -> None:
        """재시도 정보가 메시지에 추가됨."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}
        error = ValueError("Test error")

        handler.handle_failure(
            message=message,
            retry_count=0,
            error=error,
        )

        call_kwargs = mock_producer.publish.call_args[1]
        event = call_kwargs["event"]

        assert "_retry_info" in event
        assert event["_retry_info"]["retry_count"] == 1
        assert event["_retry_info"]["delay_seconds"] == 60
        assert "Test error" in event["_retry_info"]["error_message"]

    def test_dlq_info_added_to_message(self, mock_producer, retry_config) -> None:
        """DLQ 정보가 메시지에 추가됨."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}
        error = ValueError("Final error")

        handler.handle_failure(
            message=message,
            retry_count=3,
            error=error,
        )

        call_kwargs = mock_producer.publish.call_args[1]
        event = call_kwargs["event"]

        assert "_dlq_info" in event
        assert event["_dlq_info"]["original_topic"] == "audit.events"
        assert event["_dlq_info"]["retry_count"] == 3

    def test_headers_include_retry_count(self, mock_producer, retry_config) -> None:
        """헤더에 재시도 횟수 포함."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}

        handler.handle_failure(
            message=message,
            retry_count=1,
            error=ValueError("Error"),
        )

        call_kwargs = mock_producer.publish.call_args[1]
        headers = call_kwargs["headers"]

        assert RETRY_COUNT_HEADER.encode() in headers
        assert headers[RETRY_COUNT_HEADER.encode()] == b"2"

    def test_headers_include_delay(self, mock_producer, retry_config) -> None:
        """헤더에 지연 시간 포함."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}

        handler.handle_failure(
            message=message,
            retry_count=0,
            error=ValueError("Error"),
        )

        call_kwargs = mock_producer.publish.call_args[1]
        headers = call_kwargs["headers"]

        assert RETRY_DELAY_HEADER.encode() in headers
        assert headers[RETRY_DELAY_HEADER.encode()] == b"60000"  # 60초 = 60000ms

    def test_headers_include_original_topic(self, mock_producer, retry_config) -> None:
        """헤더에 원본 토픽 포함."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}

        handler.handle_failure(
            message=message,
            retry_count=0,
            error=ValueError("Error"),
        )

        call_kwargs = mock_producer.publish.call_args[1]
        headers = call_kwargs["headers"]

        assert ORIGINAL_TOPIC_HEADER.encode() in headers
        assert headers[ORIGINAL_TOPIC_HEADER.encode()] == b"audit.events"

    def test_dlq_headers_include_reason(self, mock_producer, retry_config) -> None:
        """DLQ 헤더에 실패 사유 포함."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        message = {"event_id": "evt-123"}
        error = ValueError("Specific error message")

        handler.handle_failure(
            message=message,
            retry_count=3,
            error=error,
        )

        call_kwargs = mock_producer.publish.call_args[1]
        headers = call_kwargs["headers"]

        assert DLQ_REASON_HEADER.encode() in headers
        assert b"Specific error message" in headers[DLQ_REASON_HEADER.encode()]

    def test_get_stats(self, mock_producer, retry_config) -> None:
        """통계 조회."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        handler.handle_failure(
            message={},
            retry_count=0,
            error=ValueError("Error 1"),
        )
        handler.handle_failure(
            message={},
            retry_count=3,
            error=ValueError("Error 2"),
        )

        stats = handler.get_stats()

        assert stats["retries_sent"] == 1
        assert stats["dlq_sent"] == 1
        assert stats["errors"] == 0

    def test_producer_failure_increments_errors(self, mock_producer, retry_config) -> None:
        """Producer 실패 시 에러 카운트 증가."""
        mock_producer.publish.return_value = False

        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        success = handler.handle_failure(
            message={},
            retry_count=0,
            error=ValueError("Error"),
        )

        assert success is False
        assert handler.get_stats()["errors"] == 1

    def test_extract_retry_count(self, mock_producer, retry_config) -> None:
        """헤더에서 재시도 횟수 추출."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        headers = {
            RETRY_COUNT_HEADER.encode(): b"5",
        }

        count = handler.extract_retry_count(headers)

        assert count == 5

    def test_extract_retry_count_missing(self, mock_producer, retry_config) -> None:
        """헤더 없으면 0 반환."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        count = handler.extract_retry_count({})

        assert count == 0

    def test_is_retry_message(self, mock_producer, retry_config) -> None:
        """재시도 메시지 여부 확인."""
        handler = NonBlockingRetryHandler(retry_config, mock_producer)

        headers = {
            ORIGINAL_TOPIC_HEADER.encode(): b"audit.events",
        }

        assert handler.is_retry_message(headers) is True
        assert handler.is_retry_message({}) is False

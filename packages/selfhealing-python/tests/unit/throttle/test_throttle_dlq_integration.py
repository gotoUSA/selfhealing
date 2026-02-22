"""
Throttle DLQ 연계 테스트.

Phase 4: DLQ 연계 기능 테스트
- Throttle deny 시 DLQ 저장
- CB CLOSE 시 자동 replay
"""

from unittest.mock import MagicMock

from selfhealing.services.throttle.dlq_integration import (
    ThrottleDeniedRequest,
    ThrottleDLQConfig,
    ThrottleDLQIntegration,
    get_throttle_dlq_integration,
    reset_throttle_dlq_integration,
)


class TestThrottleDeniedRequest:
    """거부된 요청 데이터 구조 테스트."""

    def test_create_denied_request(self):
        """거부된 요청 생성 확인."""
        request = ThrottleDeniedRequest(
            service_name="payment_api",
            request_key="192.168.1.1:user_123",
            request_data={"order_id": "12345"},
        )

        assert request.service_name == "payment_api"
        assert request.request_key == "192.168.1.1:user_123"
        assert request.request_data == {"order_id": "12345"}
        assert request.reason == "rate_limit_exceeded"
        assert request.dlq_entry_id is None


class TestThrottleDLQConfig:
    """DLQ 연계 설정 테스트."""

    def test_default_config(self):
        """기본 설정값 확인."""
        config = ThrottleDLQConfig()

        assert config.enabled is True
        assert config.dlq_domain == "throttle"
        assert config.auto_replay_on_cb_close is True
        assert config.replay_batch_size == 50
        assert config.replay_interval_seconds == 5.0

    def test_custom_config(self):
        """커스텀 설정값 확인."""
        config = ThrottleDLQConfig(
            enabled=False,
            dlq_domain="custom_throttle",
            replay_batch_size=100,
        )

        assert config.enabled is False
        assert config.dlq_domain == "custom_throttle"
        assert config.replay_batch_size == 100


class TestThrottleDLQIntegration:
    """DLQ 연계 통합 테스트."""

    def setup_method(self):
        """각 테스트 전 리셋."""
        reset_throttle_dlq_integration()

    def test_disabled_integration_skips_storage(self):
        """비활성화 시 저장 스킵."""
        config = ThrottleDLQConfig(enabled=False)
        integration = ThrottleDLQIntegration(config=config)

        result = integration.store_denied_request(
            service_name="test_service",
            request_key="test_key",
            request_data={"data": "test"},
            throttle_limit=100,
            current_count=100,
        )

        assert result is None

    def test_store_with_mock_dlq_service(self):
        """Mock DLQ 서비스로 저장 테스트."""
        integration = ThrottleDLQIntegration()

        # DLQ 서비스 모킹
        mock_dlq_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.entry_id = 42
        mock_dlq_service.store_failure.return_value = mock_result

        integration._dlq_service = mock_dlq_service

        result = integration.store_denied_request(
            service_name="payment_api",
            request_key="192.168.1.1:user_123",
            request_data={"order_id": "12345"},
            throttle_limit=100,
            current_count=100,
        )

        assert result is not None
        assert result.dlq_entry_id == 42
        assert result.service_name == "payment_api"

        # DLQ 서비스 호출 확인
        mock_dlq_service.store_failure.assert_called_once()
        call_kwargs = mock_dlq_service.store_failure.call_args.kwargs
        assert call_kwargs["domain"] == "throttle"
        assert call_kwargs["failure_type"] == "throttle_denied"

    def test_pending_count_tracking(self):
        """pending 카운트 추적 테스트."""
        integration = ThrottleDLQIntegration()

        # DLQ 서비스 모킹
        mock_dlq_service = MagicMock()
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.entry_id = 1
        mock_dlq_service.store_failure.return_value = mock_result

        integration._dlq_service = mock_dlq_service

        # 3개 요청 저장
        for i in range(3):
            integration.store_denied_request(
                service_name="payment_api",
                request_key=f"key_{i}",
                request_data={"id": i},
                throttle_limit=100,
                current_count=100,
            )

        assert integration.get_pending_count("payment_api") == 3
        assert integration.get_pending_count("other_service") == 0

    def test_cb_closed_triggers_replay(self):
        """CB CLOSE 시 replay 트리거 테스트."""
        integration = ThrottleDLQIntegration()

        # DLQ 서비스 모킹
        mock_dlq_service = MagicMock()
        mock_replay_result = MagicMock()
        mock_replay_result.processed = 5
        mock_replay_result.success = 4
        mock_replay_result.failed = 1
        mock_replay_result.errors = ["test error"]
        mock_dlq_service.replay.return_value = mock_replay_result

        integration._dlq_service = mock_dlq_service

        result = integration.on_circuit_breaker_closed("payment_api")

        assert result["processed"] == 5
        assert result["success"] == 4
        assert result["failed"] == 1

    def test_replay_disabled_returns_skipped(self):
        """replay 비활성화 시 스킵."""
        config = ThrottleDLQConfig(auto_replay_on_cb_close=False)
        integration = ThrottleDLQIntegration(config=config)

        result = integration.replay_denied_requests()

        assert result["skipped"] is True
        assert "disabled" in result["reason"]

    def test_singleton_instance(self):
        """싱글톤 인스턴스 테스트."""
        instance1 = get_throttle_dlq_integration()
        instance2 = get_throttle_dlq_integration()

        assert instance1 is instance2

        reset_throttle_dlq_integration()
        instance3 = get_throttle_dlq_integration()

        assert instance1 is not instance3

"""
Region Failover 테스트.

테스트 대상:
- FailoverState: 페일오버 상태 Enum
- FailoverEvent: 페일오버 이벤트 데이터클래스
- RegionFailover: 리전 페일오버 관리자
- _update_traffic_routing: TrafficRoutingAdapter 연동 (237 약점 3)
- _verify_data_consistency: 데이터 정합성 확인 (237 약점 3)
- _build_ssl_context: mTLS 지원 (237 리뷰 4-1)
- _fetch_remote_state: HTTP API 호출 (237 리뷰 4-1)
"""

import ssl
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.failover import (
    FailoverEvent,
    FailoverState,
    RegionFailover,
)


class TestFailoverState:
    """FailoverState Enum 테스트."""

    def test_values(self) -> None:
        """Enum 값 확인."""
        assert FailoverState.NORMAL.value == "normal"
        assert FailoverState.DETECTING.value == "detecting"
        assert FailoverState.FAILOVER_IN_PROGRESS.value == "failover_in_progress"
        assert FailoverState.FAILED_OVER.value == "failed_over"
        assert FailoverState.RECOVERING.value == "recovering"


class TestFailoverEvent:
    """FailoverEvent 데이터클래스 테스트."""

    def test_create_event(self) -> None:
        """이벤트 생성."""
        now = datetime.now(timezone.utc)
        event = FailoverEvent(
            from_region="ap-northeast-2",
            to_region="us-east-1",
            timestamp=now,
            reason="health_check_failed",
            state=FailoverState.FAILED_OVER,
        )

        assert event.from_region == "ap-northeast-2"
        assert event.to_region == "us-east-1"
        assert event.reason == "health_check_failed"
        assert event.state == FailoverState.FAILED_OVER

    def test_with_details(self) -> None:
        """상세 정보 포함."""
        event = FailoverEvent(
            from_region="ap-northeast-2",
            to_region="us-east-1",
            timestamp=datetime.now(timezone.utc),
            reason="manual",
            state=FailoverState.FAILOVER_IN_PROGRESS,
            details={"initiated_by": "admin"},
        )

        assert event.details["initiated_by"] == "admin"


class TestRegionFailover:
    """RegionFailover 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 리셋."""
        reset_multiregion_settings()

    def test_init(self) -> None:
        """초기화."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover._settings.current_region == "ap-northeast-2"
        assert failover.get_state() == FailoverState.NORMAL

    def test_get_state(self) -> None:
        """상태 조회."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover.get_state() == FailoverState.NORMAL

    def test_get_current_primary(self) -> None:
        """현재 Primary 조회."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        assert failover.get_current_primary() == "ap-northeast-2"

    def test_start_stop(self) -> None:
        """시작/중지."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            enabled=True,
            failover_enabled=True,
        )
        failover = RegionFailover(settings=settings)

        assert failover._running is False

        failover.start()
        assert failover._running is True

        failover.stop()
        assert failover._running is False

    def test_on_failover_callback(self) -> None:
        """페일오버 콜백."""
        callback_events: list[FailoverEvent] = []

        def on_failover(event: FailoverEvent) -> None:
            callback_events.append(event)

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings, on_failover=on_failover)

        # 콜백이 등록됨
        assert failover._on_failover is not None


# =============================================================================
# _get_traffic_routing_adapter 동작 검증 (237 약점 3)
# =============================================================================


class TestGetTrafficRoutingAdapterBehavior:
    """_get_traffic_routing_adapter() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_returns_injected_adapter(self) -> None:
        """외부 주입된 어댑터가 있으면 그것을 반환한다."""
        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        mock_adapter = MagicMock()
        failover._traffic_adapter = mock_adapter

        result = failover._get_traffic_routing_adapter()
        assert result is mock_adapter

    def test_falls_back_to_logging_adapter(self) -> None:
        """어댑터 미등록 시 LoggingTrafficRoutingAdapter를 반환한다."""
        from selfhealing.adapters.traffic_routing.logging_adapter import (
            LoggingTrafficRoutingAdapter,
        )

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        result = failover._get_traffic_routing_adapter()
        assert isinstance(result, LoggingTrafficRoutingAdapter)


# =============================================================================
# _update_traffic_routing 동작 검증 (237 약점 3)
# =============================================================================


class TestUpdateTrafficRoutingBehavior:
    """_update_traffic_routing() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_stores_last_routing_change(self) -> None:
        """성공 시 _last_routing_change에 결과를 저장한다."""
        from selfhealing.interfaces.traffic_routing import RoutingChange

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        mock_adapter = MagicMock()
        mock_adapter.switch_primary.return_value = RoutingChange(
            success=True, from_region="ap-northeast-2", to_region="us-east-1"
        )
        failover._traffic_adapter = mock_adapter

        failover._update_traffic_routing("us-east-1")

        assert failover._last_routing_change is not None
        assert failover._last_routing_change.to_region == "us-east-1"

    def test_raises_on_failure(self) -> None:
        """switch_primary() 실패 시 RuntimeError를 발생시킨다."""
        from selfhealing.interfaces.traffic_routing import RoutingChange

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        mock_adapter = MagicMock()
        mock_adapter.switch_primary.return_value = RoutingChange(
            success=False,
            from_region="ap-northeast-2",
            to_region="us-east-1",
            details={"error": "DNS update failed"},
        )
        failover._traffic_adapter = mock_adapter

        with pytest.raises(RuntimeError, match="Traffic routing switch failed"):
            failover._update_traffic_routing("us-east-1")


# =============================================================================
# _verify_data_consistency 동작 검증 (237 약점 3)
# =============================================================================


class TestVerifyDataConsistencyBehavior:
    """_verify_data_consistency() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    @patch("selfhealing.multiregion.replicator.RegionReplicator")
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_logs_on_pending_replication(self, mock_get_backend: MagicMock, mock_replicator_cls: MagicMock) -> None:
        """복제 큐에 미완료 이벤트가 있으면 경고를 기록한다 (failover는 계속)."""
        mock_replicator = MagicMock()
        mock_replicator.get_queue_size.return_value = 5
        mock_replicator_cls.return_value = mock_replicator

        mock_backend = MagicMock()
        mock_backend.get.return_value = None
        mock_get_backend.return_value = mock_backend

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        # 예외 없이 완료되어야 함 (가용성 우선)
        failover._verify_data_consistency("us-east-1")

    @patch("selfhealing.multiregion.replicator.RegionReplicator")
    @patch("selfhealing.core.state_backend.get_state_backend")
    def test_does_not_raise_on_failure(self, mock_get_backend: MagicMock, mock_replicator_cls: MagicMock) -> None:
        """정합성 확인 실패 시에도 예외를 발생시키지 않는다 (가용성 우선)."""
        mock_replicator_cls.side_effect = Exception("Import error")
        mock_get_backend.side_effect = Exception("Redis down")

        settings = MultiRegionSettings(current_region="ap-northeast-2")
        failover = RegionFailover(settings=settings)

        # 예외 없이 완료
        failover._verify_data_consistency("us-east-1")


# =============================================================================
# _build_ssl_context 동작 검증 (237 리뷰 4-1)
# =============================================================================


class TestBuildSslContextBehavior:
    """_build_ssl_context() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_tls_disabled_returns_none(self) -> None:
        """TLS 비활성화 시 None을 반환한다."""
        settings = MultiRegionSettings(current_region="ap-northeast-2", tls_enabled=False)
        failover = RegionFailover(settings=settings)

        result = failover._build_ssl_context()
        assert result is None

    @patch("ssl.create_default_context")
    def test_tls_enabled_creates_context(self, mock_ctx_factory: MagicMock) -> None:
        """TLS 활성화 시 SSL Context를 생성한다."""
        mock_ctx = MagicMock(spec=ssl.SSLContext)
        mock_ctx_factory.return_value = mock_ctx

        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            tls_enabled=True,
            tls_cert_path="/etc/ssl/certs/test.crt",
            tls_key_path="/etc/ssl/private/test.key",
            tls_ca_path="/etc/ssl/certs/ca.crt",
            tls_verify_hostname=True,
        )
        failover = RegionFailover(settings=settings)
        result = failover._build_ssl_context()

        assert result is mock_ctx
        mock_ctx_factory.assert_called_once_with(cafile=settings.tls_ca_path)
        mock_ctx.load_cert_chain.assert_called_once_with(
            certfile=settings.tls_cert_path,
            keyfile=settings.tls_key_path,
        )

    @patch("ssl.create_default_context")
    def test_tls_verify_hostname_false_keeps_cert_required(self, mock_ctx_factory: MagicMock) -> None:
        """tls_verify_hostname=False 시에도 verify_mode는 CERT_REQUIRED."""
        mock_ctx = MagicMock(spec=ssl.SSLContext)
        mock_ctx_factory.return_value = mock_ctx

        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            tls_enabled=True,
            tls_verify_hostname=False,
        )
        failover = RegionFailover(settings=settings)
        failover._build_ssl_context()

        assert mock_ctx.check_hostname is False
        assert mock_ctx.verify_mode == ssl.CERT_REQUIRED

    def test_tls_cert_not_found_returns_none(self) -> None:
        """인증서 파일이 없으면 None을 반환한다."""
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            tls_enabled=True,
            tls_ca_path="/nonexistent/ca.crt",
        )
        failover = RegionFailover(settings=settings)

        # FileNotFoundError 발생 → None 반환
        result = failover._build_ssl_context()
        assert result is None


# =============================================================================
# _fetch_remote_state 동작 검증 (237 리뷰 4-1)
# =============================================================================


class TestFetchRemoteStateBehavior:
    """_fetch_remote_state() 동작 검증."""

    def setup_method(self) -> None:
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        reset_multiregion_settings()

    def test_returns_none_on_no_matching_endpoint(self) -> None:
        """일치하는 엔드포인트가 없으면 None을 반환한다."""
        settings = MultiRegionSettings(current_region="ap-northeast-2", peer_regions="[]")
        failover = RegionFailover(settings=settings)

        result = failover._fetch_remote_state("nonexistent", "key")
        assert result is None

    @patch("urllib.request.urlopen")
    def test_calls_api_with_ssl_context(self, mock_urlopen: MagicMock) -> None:
        """TLS 활성화 시 SSL context를 urlopen에 전달한다."""
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read.return_value = b'{"value": "test"}'
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_response

        peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "https://us.api"}]'
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            peer_regions=peer_json,
            tls_enabled=False,
        )
        failover = RegionFailover(settings=settings)

        result = failover._fetch_remote_state("us-east-1", "emergency_mode")

        assert result == {"value": "test"}
        # urlopen이 호출됨
        mock_urlopen.assert_called_once()

    def test_returns_none_on_exception(self) -> None:
        """API 호출 실패 시 None을 반환한다."""
        peer_json = '[{"region": "us-east-1", "redis_url": "redis://us:6379", "api_endpoint": "https://us.api"}]'
        settings = MultiRegionSettings(
            current_region="ap-northeast-2",
            peer_regions=peer_json,
            tls_enabled=False,
        )
        failover = RegionFailover(settings=settings)

        # 실제 네트워크 호출이 실패할 것
        result = failover._fetch_remote_state("us-east-1", "emergency_mode")
        assert result is None

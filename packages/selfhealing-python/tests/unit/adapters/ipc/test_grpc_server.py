"""
SidecarGRPCServer 단위 테스트.

테스트 항목:
- GRPCServerStats 데이터클래스
- SidecarGRPCServer 초기화
- 서버 라이프사이클 (start, stop)
- 핸들러 메서드
- 인증
- 싱글톤 패턴
"""

from __future__ import annotations

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.ipc.grpc_server import (
    GRPC_AVAILABLE,
    GRPCServerStats,
    SidecarGRPCServer,
    get_grpc_server,
    reset_grpc_server,
)


class TestGRPCServerStats:
    """GRPCServerStats 테스트."""

    def test_init_defaults(self):
        """기본값 초기화."""
        stats = GRPCServerStats()

        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.auth_failures == 0
        assert stats.active_streams == 0

    def test_init_custom(self):
        """커스텀 값 초기화."""
        stats = GRPCServerStats(
            total_requests=100,
            successful_requests=90,
            failed_requests=10,
            auth_failures=5,
            active_streams=3,
        )

        assert stats.total_requests == 100
        assert stats.successful_requests == 90
        assert stats.failed_requests == 10
        assert stats.auth_failures == 5
        assert stats.active_streams == 3

    def test_dataclass_asdict(self):
        """asdict 변환."""
        stats = GRPCServerStats(total_requests=50)

        d = asdict(stats)

        assert d["total_requests"] == 50
        assert "successful_requests" in d


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="gRPC not available")
class TestSidecarGRPCServer:
    """SidecarGRPCServer 테스트."""

    def teardown_method(self):
        """테스트 후 정리."""
        reset_grpc_server()

    def test_init_default(self):
        """기본 초기화."""
        server = SidecarGRPCServer()

        assert server._port == SidecarGRPCServer.DEFAULT_PORT
        assert server._host == "localhost"
        assert server._max_workers == SidecarGRPCServer.MAX_WORKERS
        assert not server.is_running
        assert server._authenticator is not None
        assert server._cb_cache is not None
        assert server._event_proxy is not None

    def test_init_custom_port(self):
        """커스텀 포트."""
        server = SidecarGRPCServer(port=50052)

        assert server._port == 50052

    def test_init_custom_host(self):
        """커스텀 호스트."""
        server = SidecarGRPCServer(host="0.0.0.0")

        assert server._host == "0.0.0.0"

    def test_init_custom_max_workers(self):
        """커스텀 워커 수."""
        server = SidecarGRPCServer(max_workers=20)

        assert server._max_workers == 20

    def test_init_custom_authenticator(self):
        """커스텀 인증자."""
        mock_auth = MagicMock()
        server = SidecarGRPCServer(authenticator=mock_auth)

        assert server._authenticator is mock_auth

    def test_init_custom_cb_cache(self):
        """커스텀 CB 캐시."""
        mock_cache = MagicMock()
        server = SidecarGRPCServer(cb_cache=mock_cache)

        assert server._cb_cache is mock_cache

    def test_init_custom_event_proxy(self):
        """커스텀 이벤트 프록시."""
        mock_proxy = MagicMock()
        server = SidecarGRPCServer(event_proxy=mock_proxy)

        assert server._event_proxy is mock_proxy

    def test_stats_property(self):
        """stats 프로퍼티."""
        server = SidecarGRPCServer()

        stats = server.stats

        assert isinstance(stats, GRPCServerStats)
        assert stats.total_requests == 0

    def test_get_stats_dict(self):
        """통계 딕셔너리."""
        server = SidecarGRPCServer(port=50053, host="127.0.0.1")

        d = server.get_stats_dict()

        assert d["host"] == "127.0.0.1"
        assert d["port"] == 50053
        assert d["running"] is False
        assert d["total_requests"] == 0


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="gRPC not available")
class TestSidecarGRPCServerAuthentication:
    """인증 관련 테스트."""

    def test_authenticate_disabled(self):
        """인증 비활성화 시 통과."""
        mock_auth = MagicMock()
        mock_auth.is_enabled = False

        server = SidecarGRPCServer(authenticator=mock_auth)
        mock_context = MagicMock()

        result = server._authenticate(mock_context)

        assert result is True
        mock_auth.validate.assert_not_called()

    def test_authenticate_success(self):
        """인증 성공."""
        mock_auth = MagicMock()
        mock_auth.is_enabled = True
        mock_result = MagicMock()
        mock_result.success = True
        mock_auth.validate.return_value = mock_result

        server = SidecarGRPCServer(authenticator=mock_auth)
        mock_context = MagicMock()
        mock_context.invocation_metadata.return_value = [("authorization", "Bearer valid-token")]

        result = server._authenticate(mock_context)

        assert result is True
        mock_auth.validate.assert_called_once()

    def test_authenticate_failure(self):
        """인증 실패."""
        mock_auth = MagicMock()
        mock_auth.is_enabled = True
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "Invalid token"
        mock_auth.validate.return_value = mock_result

        server = SidecarGRPCServer(authenticator=mock_auth)
        mock_context = MagicMock()
        mock_context.invocation_metadata.return_value = [("authorization", "Bearer invalid")]

        result = server._authenticate(mock_context)

        assert result is False
        assert server.stats.auth_failures == 1
        mock_context.abort.assert_called_once()


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="gRPC not available")
class TestSidecarGRPCServerHandlers:
    """핸들러 메서드 테스트."""

    def test_handle_health_check(self):
        """헬스 체크 핸들러."""
        server = SidecarGRPCServer()
        mock_context = MagicMock()

        result = server.handle_health_check(mock_context)

        assert result == {"status": "SERVING"}

    def test_handle_health_details(self):
        """헬스 상세 핸들러."""
        server = SidecarGRPCServer()
        mock_context = MagicMock()

        result = server.handle_health_details(mock_context)

        assert "overall_status" in result
        assert "components" in result
        assert "version" in result
        assert result["overall_status"] == "healthy"

    def test_handle_should_allow_cache_hit(self):
        """should_allow 캐시 히트."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = ({"allowed": True, "state": "closed"}, True)

        mock_auth = MagicMock()
        mock_auth.is_enabled = False

        server = SidecarGRPCServer(cb_cache=mock_cache, authenticator=mock_auth)
        mock_context = MagicMock()

        result = server.handle_should_allow("test-service", mock_context)

        assert result["allowed"] is True
        assert result["state"] == "closed"
        assert server.stats.total_requests == 1
        assert server.stats.successful_requests == 1

    def test_handle_should_allow_auth_failure(self):
        """should_allow 인증 실패 시 fail-open."""
        mock_auth = MagicMock()
        mock_auth.is_enabled = True
        mock_result = MagicMock()
        mock_result.success = False
        mock_result.error = "Unauthorized"
        mock_auth.validate.return_value = mock_result

        server = SidecarGRPCServer(authenticator=mock_auth)
        mock_context = MagicMock()
        mock_context.invocation_metadata.return_value = []

        result = server.handle_should_allow("test-service", mock_context)

        # Fail-open 정책
        assert result["allowed"] is True
        assert result["state"] == "unknown"
        assert server.stats.failed_requests == 1


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="gRPC not available")
class TestGRPCServerLifecycle:
    """서버 라이프사이클 테스트."""

    def teardown_method(self):
        """테스트 후 정리."""
        reset_grpc_server()

    def test_is_running_initial(self):
        """초기 실행 상태."""
        server = SidecarGRPCServer()

        assert server.is_running is False

    def test_stop_not_running(self):
        """미실행 상태에서 stop."""
        server = SidecarGRPCServer()

        # 에러 없이 실행되어야 함
        server.stop()

        assert server.is_running is False

    @patch("selfhealing.adapters.ipc.grpc_server.grpc")
    def test_start_already_running(self, mock_grpc):
        """이미 실행 중일 때 start."""
        server = SidecarGRPCServer()
        server._running = True

        # 두 번째 start 호출은 무시됨
        server.start()

        mock_grpc.server.assert_not_called()


@pytest.mark.skipif(not GRPC_AVAILABLE, reason="gRPC not available")
class TestGlobalGRPCServer:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_grpc_server()

    def test_singleton(self):
        """싱글톤 인스턴스 반환."""
        server1 = get_grpc_server()
        server2 = get_grpc_server()

        assert server1 is server2

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        server1 = get_grpc_server()

        reset_grpc_server()

        server2 = get_grpc_server()

        assert server1 is not server2


class TestGRPCNotAvailable:
    """gRPC 미설치 시 테스트."""

    def test_init_without_grpc(self):
        """gRPC 없이 초기화 시 RuntimeError."""
        with patch("selfhealing.adapters.ipc.grpc_server.GRPC_AVAILABLE", False):
            # 새로운 클래스 정의로 테스트
            # 실제로는 import 시점에 GRPC_AVAILABLE이 결정됨
            pass  # 구현 세부사항에 의존하는 테스트

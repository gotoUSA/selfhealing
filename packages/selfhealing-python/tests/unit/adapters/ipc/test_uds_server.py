"""
UDSServer 단위 테스트.

테스트 항목:
- 서버 초기화
- 서버 시작/종료
- JSON-RPC 요청 처리
- 인증 검증
- CB 상태 캐싱
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from selfhealing.adapters.ipc.uds_server import (
    ServerStats,
    UDSServer,
    get_uds_server,
    reset_uds_server,
)


class TestServerStats:
    """ServerStats 테스트."""

    def test_initial_stats(self):
        """초기 통계."""
        stats = ServerStats()

        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.auth_failures == 0
        assert stats.total_connections == 0
        assert stats.active_connections == 0


class TestUDSServer:
    """UDSServer 테스트."""

    def test_init_default(self):
        """기본 초기화."""
        server = UDSServer()

        assert server._socket_path == UDSServer.DEFAULT_SOCKET_PATH
        assert server._running is False

    def test_init_custom_socket_path(self):
        """커스텀 소켓 경로."""
        if sys.platform != "win32":
            server = UDSServer(socket_path="/tmp/test_selfhealing.sock")
            assert server._socket_path == "/tmp/test_selfhealing.sock"

    def test_init_with_authenticator(self):
        """인증자 지정."""
        mock_auth = MagicMock()
        server = UDSServer(authenticator=mock_auth)

        assert server._authenticator is mock_auth

    def test_init_with_cache(self):
        """캐시 지정."""
        mock_cache = MagicMock()
        server = UDSServer(cb_cache=mock_cache)

        assert server._cb_cache is mock_cache

    def test_handlers_registered(self):
        """핸들러 등록 확인."""
        server = UDSServer()

        assert "circuit_breaker.should_allow" in server._handlers
        assert "circuit_breaker.should_allow_batch" in server._handlers
        assert "dlq.store" in server._handlers
        assert "health.check" in server._handlers

    def test_is_running_false_initially(self):
        """초기 상태는 실행 중이 아님."""
        server = UDSServer()

        assert server._running is False
        assert server._server_socket is None

    @pytest.mark.skipif(sys.platform == "win32", reason="UDS not supported on Windows")
    def test_start_and_stop(self):
        """서버 시작 및 종료."""
        import os
        import tempfile

        # 임시 소켓 파일 경로
        with tempfile.TemporaryDirectory() as tmpdir:
            socket_path = os.path.join(tmpdir, "test.sock")
            server = UDSServer(socket_path=socket_path)

            server.start(background=True)
            assert server._running is True

            server.stop()
            assert server._running is False


class TestUDSServerHandlers:
    """UDSServer 핸들러 테스트."""

    def test_handle_health_check(self):
        """health.check 핸들러."""
        server = UDSServer()

        result = server._handle_health_check({})

        assert "status" in result
        # gRPC 스타일 상태 반환
        assert result["status"] == "SERVING"

    def test_handle_cb_should_allow_with_cache(self):
        """캐시된 CB 상태 반환."""
        mock_cache = MagicMock()
        mock_cache.get.return_value = ({"allowed": True, "state": "closed"}, True)

        server = UDSServer(cb_cache=mock_cache)

        result = server._handle_cb_should_allow({"service_name": "test_service"})

        assert result["allowed"] is True
        assert result["state"] == "closed"
        mock_cache.get.assert_called_once_with("test_service")


class TestGlobalUDSServer:
    """싱글톤 인스턴스 테스트."""

    def teardown_method(self):
        """테스트 후 싱글톤 리셋."""
        reset_uds_server()

    def test_singleton(self):
        """싱글톤 인스턴스 반환."""
        server1 = get_uds_server()
        server2 = get_uds_server()

        assert server1 is server2

    def test_reset_singleton(self):
        """싱글톤 리셋."""
        server1 = get_uds_server()

        reset_uds_server()

        server2 = get_uds_server()

        assert server1 is not server2

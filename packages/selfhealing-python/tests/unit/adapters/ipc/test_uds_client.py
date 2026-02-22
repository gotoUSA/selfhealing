"""
UDSClient 단위 테스트.

테스트 항목:
- 클라이언트 초기화
- Fail-Open 정책
- 요청 구성
- 통계 추적
"""

from __future__ import annotations

import sys

from selfhealing.adapters.ipc.uds_client import (
    ClientStats,
    FailOpenUDSClient,
    UDSClient,
    UDSClientError,
)


class TestClientStats:
    """ClientStats 테스트."""

    def test_initial_stats(self):
        """초기 통계."""
        stats = ClientStats()

        assert stats.total_requests == 0
        assert stats.successful_requests == 0
        assert stats.failed_requests == 0
        assert stats.total_latency_ms == 0.0

    def test_avg_latency_zero(self):
        """요청 없을 때 평균 지연 0."""
        stats = ClientStats()

        assert stats.avg_latency_ms == 0.0

    def test_avg_latency_calculation(self):
        """평균 지연 시간 계산."""
        stats = ClientStats(
            total_requests=10,
            total_latency_ms=100.0,
        )

        assert stats.avg_latency_ms == 10.0


class TestUDSClient:
    """UDSClient 테스트."""

    def test_init_default(self):
        """기본 초기화."""
        client = UDSClient()

        assert client._socket_path == UDSClient.DEFAULT_SOCKET_PATH
        assert client._timeout == UDSClient.DEFAULT_TIMEOUT
        assert client._fail_open is True

    def test_init_custom_socket_path(self):
        """커스텀 소켓 경로."""
        if sys.platform != "win32":
            client = UDSClient(socket_path="/tmp/custom.sock")
            assert client._socket_path == "/tmp/custom.sock"

    def test_init_with_auth_token(self):
        """인증 토큰 설정."""
        client = UDSClient(auth_token="my-token")

        assert client._auth_token == "my-token"

    def test_init_fail_open_disabled(self):
        """Fail-Open 비활성화."""
        client = UDSClient(fail_open=False)

        assert client._fail_open is False

    def test_is_connected_initial(self):
        """초기 연결 상태."""
        client = UDSClient()

        assert client.is_connected() is False

    def test_next_request_id(self):
        """요청 ID 생성."""
        client = UDSClient()

        id1 = client._next_request_id()
        id2 = client._next_request_id()

        assert id1 == "req-1"
        assert id2 == "req-2"

    def test_stats_initial(self):
        """초기 통계."""
        client = UDSClient()

        assert client._stats.total_requests == 0
        assert client._stats.successful_requests == 0


class TestFailOpenUDSClient:
    """FailOpenUDSClient 테스트."""

    def test_init(self):
        """초기화."""
        client = FailOpenUDSClient()

        assert client._connected is False
        assert client._fail_open is True

    def test_init_shorter_timeout(self):
        """더 짧은 기본 타임아웃."""
        client = FailOpenUDSClient()

        # FailOpenUDSClient는 1초 타임아웃
        assert client._timeout == 1.0

    def test_should_allow_returns_true_when_disconnected(self):
        """연결 해제 시 True 반환 (Fail-Open)."""
        client = FailOpenUDSClient()
        client._connected = False

        # 부모 메서드가 예외를 던지거나 기본값을 반환
        # FailOpenUDSClient.should_allow는 bool을 반환
        result = client.should_allow("test_service")

        # Fail-open이므로 True 반환
        assert result is True

    def test_fail_open_forced(self):
        """Fail-Open이 항상 강제됨."""
        # fail_open은 생성자에서 무시됨
        client = FailOpenUDSClient()

        assert client._fail_open is True

    def test_inherits_uds_client(self):
        """UDSClient 상속 확인."""
        client = FailOpenUDSClient()

        assert isinstance(client, UDSClient)


class TestUDSClientError:
    """UDSClientError 테스트."""

    def test_create_error(self):
        """에러 생성."""
        error = UDSClientError("Connection failed")

        assert str(error) == "Connection failed"
        assert isinstance(error, Exception)

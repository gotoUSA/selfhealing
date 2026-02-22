"""
Secure Redis Client 테스트.

테스트 대상:
- SecureRedisClient: mTLS Redis 클라이언트
"""


from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    reset_multiregion_settings,
)
from selfhealing.multiregion.secure_client import (
    SecureRedisClient,
)


class TestSecureRedisClient:
    """SecureRedisClient 테스트."""

    def setup_method(self) -> None:
        """테스트 전 설정 리셋."""
        reset_multiregion_settings()

    def teardown_method(self) -> None:
        """테스트 후 설정 리셋."""
        reset_multiregion_settings()

    def test_init(self) -> None:
        """초기화."""
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://redis.us-east-1:6379",
            kafka_bootstrap="",
            api_endpoint="http://api.us-east-1",
        )
        settings = MultiRegionSettings(tls_enabled=False)

        client = SecureRedisClient(endpoint=endpoint, settings=settings)

        assert client._endpoint.region == "us-east-1"
        assert client._settings.tls_enabled is False

    def test_ssl_context_disabled(self) -> None:
        """TLS 비활성화 시 SSL 컨텍스트 None."""
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://localhost:6379",
            kafka_bootstrap="",
            api_endpoint="http://localhost",
        )
        settings = MultiRegionSettings(tls_enabled=False)
        client = SecureRedisClient(endpoint=endpoint, settings=settings)

        context = client._create_ssl_context()

        assert context is None

    def test_ssl_context_enabled_missing_certs(self) -> None:
        """TLS 활성화 + 인증서 없음 → None 반환 (경고 로그)."""
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://localhost:6379",
            kafka_bootstrap="",
            api_endpoint="http://localhost",
        )
        settings = MultiRegionSettings(
            tls_enabled=True,
            tls_ca_path="/nonexistent/ca.pem",
            tls_cert_path="/nonexistent/cert.pem",
            tls_key_path="/nonexistent/key.pem",
        )
        client = SecureRedisClient(endpoint=endpoint, settings=settings)

        context = client._create_ssl_context()

        # 인증서 파일 없으면 None
        assert context is None

    def test_is_connected_not_initialized(self) -> None:
        """클라이언트 미초기화 시 is_connected."""
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://localhost:6379",
            kafka_bootstrap="",
            api_endpoint="http://localhost",
        )
        settings = MultiRegionSettings(tls_enabled=False)
        client = SecureRedisClient(endpoint=endpoint, settings=settings)

        # 클라이언트 미생성 상태
        assert client._client is None

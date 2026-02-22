"""
Secure Redis Client for Multi-Region.

mTLS를 적용한 리전 간 Redis 통신 클라이언트.

리전 간 데이터는 공용 인터넷을 통과할 수 있으므로
mTLS로 암호화 및 상호 인증을 적용합니다.
"""

from __future__ import annotations

import structlog
import ssl
from typing import Any

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
)

logger = structlog.get_logger()


class SecureRedisClient:
    """
    mTLS 적용 Redis 클라이언트.

    리전 간 통신 시 TLS로 암호화하고 클라이언트 인증서로 상호 인증합니다.

    사용 예:
        endpoint = RegionEndpoint(
            region="us-east-1",
            redis_url="redis://redis.us-east-1:6379",
            kafka_bootstrap="",
            api_endpoint="",
        )

        secure_client = SecureRedisClient(endpoint)
        redis = secure_client.get_client()

        # 일반 Redis 클라이언트처럼 사용
        redis.set("key", "value")
        value = redis.get("key")
    """

    def __init__(
        self,
        endpoint: RegionEndpoint,
        settings: MultiRegionSettings | None = None,
    ):
        """
        초기화.

        Args:
            endpoint: 리전 엔드포인트
            settings: Multi-Region 설정 (None이면 기본 설정)
        """
        self._endpoint = endpoint
        self._settings = settings or get_multiregion_settings()
        self._client: Any = None

    def _create_ssl_context(self) -> ssl.SSLContext | None:
        """
        SSL 컨텍스트 생성.

        TLS 설정이 비활성화되어 있으면 None을 반환합니다.

        Returns:
            SSL 컨텍스트 또는 None
        """
        if not self._settings.tls_enabled:
            return None

        try:
            # 클라이언트용 SSL 컨텍스트 생성
            context = ssl.create_default_context(
                cafile=self._settings.tls_ca_path,
            )

            # 클라이언트 인증서 로드 (mTLS)
            context.load_cert_chain(
                certfile=self._settings.tls_cert_path,
                keyfile=self._settings.tls_key_path,
            )

            # 검증 설정
            context.check_hostname = self._settings.tls_verify_hostname
            context.verify_mode = ssl.CERT_REQUIRED

            return context
        except FileNotFoundError as e:
            logger.warning(
                "secure_redis.certificate_file_found",
                error=e,
            )
            return None
        except ssl.SSLError as e:
            logger.error(
                "secure_redis.ssl_context_creation_failed",
                error=e,
            )
            raise
        except Exception as e:
            logger.error(
                "secure_redis.unexpected_error_creating_ssl",
                error=e,
            )
            raise

    def get_client(self) -> Any:
        """
        Redis 클라이언트 반환.

        TLS 설정에 따라 암호화된 연결을 생성합니다.

        Returns:
            Redis 클라이언트
        """
        if self._client is None:
            try:
                import redis
            except ImportError:
                logger.error("secure_redis.redis_package_installed")
                raise

            ssl_context = self._create_ssl_context()

            # TLS 활성화 시 URL 스킴 변경
            url = self._endpoint.redis_url
            if self._settings.tls_enabled and url.startswith("redis://"):
                url = url.replace("redis://", "rediss://", 1)

            if ssl_context:
                self._client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                    ssl=True,
                    ssl_ca_certs=self._settings.tls_ca_path,
                    ssl_certfile=self._settings.tls_cert_path,
                    ssl_keyfile=self._settings.tls_key_path,
                    ssl_cert_reqs="required",
                    ssl_check_hostname=self._settings.tls_verify_hostname,
                )
            else:
                self._client = redis.Redis.from_url(
                    url,
                    decode_responses=True,
                )

        return self._client

    def is_connected(self) -> bool:
        """
        연결 상태 확인.

        Returns:
            True if 연결됨
        """
        try:
            client = self.get_client()
            client.ping()
            return True
        except Exception as e:
            logger.debug(
                "secure_redis.connection_check_failed",
                error=e,
            )
            return False

    def close(self) -> None:
        """연결 종료."""
        if self._client:
            try:
                self._client.close()
            except Exception as e:
                logger.debug(
                    "secure_redis.close_error",
                    error=e,
                )
            finally:
                self._client = None

    def get_endpoint(self) -> RegionEndpoint:
        """엔드포인트 반환."""
        return self._endpoint

    def is_tls_enabled(self) -> bool:
        """TLS 활성화 여부."""
        return self._settings.tls_enabled


class SecureRedisPool:
    """
    리전별 Secure Redis 클라이언트 풀.

    여러 리전의 Redis 클라이언트를 관리합니다.

    사용 예:
        pool = SecureRedisPool()

        # 리전별 클라이언트 획득
        us_redis = pool.get_client("us-east-1")
        kr_redis = pool.get_client("ap-northeast-2")
    """

    def __init__(self, settings: MultiRegionSettings | None = None):
        """
        초기화.

        Args:
            settings: Multi-Region 설정
        """
        self._settings = settings or get_multiregion_settings()
        self._clients: dict[str, SecureRedisClient] = {}

        # 피어 리전 엔드포인트로 클라이언트 초기화
        for endpoint in self._settings.get_peer_endpoints():
            self._clients[endpoint.region] = SecureRedisClient(
                endpoint=endpoint,
                settings=self._settings,
            )

    def get_client(self, region: str) -> SecureRedisClient | None:
        """
        리전별 클라이언트 반환.

        Args:
            region: 리전 이름

        Returns:
            SecureRedisClient 또는 None
        """
        return self._clients.get(region)

    def get_all_regions(self) -> list[str]:
        """등록된 리전 목록 반환."""
        return list(self._clients.keys())

    def get_connected_regions(self) -> list[str]:
        """연결된 리전 목록 반환."""
        return [region for region, client in self._clients.items() if client.is_connected()]

    def close_all(self) -> None:
        """모든 연결 종료."""
        for client in self._clients.values():
            client.close()

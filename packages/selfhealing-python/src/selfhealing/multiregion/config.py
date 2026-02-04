"""
Multi-Region 설정 관리.

리전 간 Active-Active 아키텍처를 위한 설정을 관리합니다.

주요 설정:
- 리전 식별 및 역할
- 복제 모드 (sync, async, eventual)
- 페일오버 정책
- 피어 리전 목록
- TLS 보안 설정
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class RegionEndpoint:
    """
    리전 엔드포인트 정보.

    다른 리전에 연결하기 위한 연결 정보를 담습니다.

    Attributes:
        region: 리전 이름 (예: 'us-east-1')
        redis_url: Redis 연결 URL
        kafka_bootstrap: Kafka 부트스트랩 서버
        api_endpoint: API 엔드포인트 URL
        priority: 리전 우선순위 (낮을수록 높은 우선순위)
    """

    def __init__(
        self,
        region: str,
        redis_url: str,
        kafka_bootstrap: str,
        api_endpoint: str,
        priority: int = 100,
    ):
        """
        리전 엔드포인트 초기화.

        Args:
            region: 리전 이름 (예: 'us-east-1')
            redis_url: Redis 연결 URL
            kafka_bootstrap: Kafka 부트스트랩 서버
            api_endpoint: API 엔드포인트 URL
            priority: 리전 우선순위 (낮을수록 높은 우선순위, 기본값 100)
        """
        self.region = region
        self.redis_url = redis_url
        self.kafka_bootstrap = kafka_bootstrap
        self.api_endpoint = api_endpoint
        self.priority = priority

    def __repr__(self) -> str:
        """문자열 표현."""
        return f"RegionEndpoint(region={self.region!r}, " f"priority={self.priority})"


class MultiRegionSettings(BaseSettings):
    """
    Multi-Region 설정.

    환경변수로 설정하며 SELFHEALING_MULTIREGION_ 접두사를 사용합니다.

    Example:
        SELFHEALING_MULTIREGION_ENABLED=true
        SELFHEALING_MULTIREGION_CURRENT_REGION=ap-northeast-2
        SELFHEALING_MULTIREGION_REGION_ROLE=primary
    """

    # 활성화 여부
    enabled: bool = Field(
        default=False,
        description="Multi-Region 기능 활성화 여부",
    )

    # 현재 리전 정보
    current_region: str = Field(
        default="ap-northeast-2",
        description="현재 리전 이름 (예: 'ap-northeast-2')",
    )

    # 리전 역할
    region_role: Literal["primary", "secondary", "readonly"] = Field(
        default="primary",
        description="리전 역할 (primary: 주 리전, secondary: 보조 리전, readonly: 읽기 전용)",
    )

    # 피어 리전 목록 (JSON 문자열)
    peer_regions: str = Field(
        default="[]",
        description='피어 리전 JSON (예: [{"region": "us-east-1", "redis_url": "...", ...}])',
    )

    # =========================================================================
    # 복제 설정
    # =========================================================================

    replication_mode: Literal["sync", "async", "eventual"] = Field(
        default="async",
        description="복제 모드 (sync: 동기, async: 비동기, eventual: 최종 일관성)",
    )

    replication_batch_size: int = Field(
        default=100,
        ge=1,
        le=10000,
        description="비동기 복제 배치 크기",
    )

    replication_interval_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=60.0,
        description="비동기 복제 주기 (초)",
    )

    replication_queue_size: int = Field(
        default=10000,
        ge=100,
        le=1000000,
        description="복제 큐 최대 크기",
    )

    replication_worker_count: int = Field(
        default=1,
        ge=1,
        le=16,
        description="비동기 복제 워커 수",
    )

    # =========================================================================
    # 충돌 해결 설정
    # =========================================================================

    conflict_resolution: Literal["lww", "crdt", "manual"] = Field(
        default="lww",
        description="충돌 해결 전략 (lww: Last-Write-Wins, crdt: CRDT, manual: 수동)",
    )

    # Clock Skew 설정 (AWS Time Sync Service 사용 권장)
    clock_skew_tolerance_ms: int = Field(
        default=5,
        ge=1,
        le=1000,
        description="Clock Skew 허용 오차 (ms). AWS Time Sync Service 사용 시 5ms 권장",
    )

    clock_skew_fallback_seconds: float = Field(
        default=1.0,
        ge=0.1,
        le=30.0,
        description="Time Sync 실패 시 폴백 tolerance (초)",
    )

    # =========================================================================
    # 건강 모니터링 설정
    # =========================================================================

    health_check_interval_seconds: float = Field(
        default=10.0,
        ge=1.0,
        le=300.0,
        description="리전 건강 체크 주기 (초)",
    )

    health_check_timeout_seconds: float = Field(
        default=5.0,
        ge=1.0,
        le=30.0,
        description="리전 건강 체크 타임아웃 (초)",
    )

    unhealthy_threshold: int = Field(
        default=3,
        ge=1,
        le=10,
        description="비정상 판정을 위한 연속 실패 횟수",
    )

    # Replication Lag 임계치
    replication_lag_warning_ms: int = Field(
        default=500,
        ge=100,
        le=10000,
        description="복제 지연 경고 임계치 (ms)",
    )

    replication_lag_critical_ms: int = Field(
        default=2000,
        ge=500,
        le=60000,
        description="복제 지연 치명적 임계치 (ms). 초과 시 DEGRADED 상태",
    )

    # =========================================================================
    # 페일오버 설정
    # =========================================================================

    failover_enabled: bool = Field(
        default=True,
        description="자동 페일오버 활성화 여부",
    )

    failover_cooldown_seconds: float = Field(
        default=300.0,
        ge=30.0,
        le=3600.0,
        description="페일오버 쿨다운 (초). 연속 페일오버 방지",
    )

    # =========================================================================
    # TLS 보안 설정
    # =========================================================================

    tls_enabled: bool = Field(
        default=True,
        description="리전 간 통신 TLS 활성화 여부",
    )

    tls_cert_path: str = Field(
        default="/etc/ssl/certs/multiregion-client.crt",
        description="클라이언트 인증서 경로",
    )

    tls_key_path: str = Field(
        default="/etc/ssl/private/multiregion-client.key",
        description="클라이언트 키 경로",
    )

    tls_ca_path: str = Field(
        default="/etc/ssl/certs/multiregion-ca.crt",
        description="CA 인증서 경로",
    )

    tls_verify_hostname: bool = Field(
        default=True,
        description="TLS 호스트명 검증 여부",
    )

    model_config = {
        "env_prefix": "SELFHEALING_MULTIREGION_",
        "env_file": ".env",
        "extra": "ignore",
    }

    @field_validator("peer_regions", mode="before")
    @classmethod
    def parse_peer_regions(cls, v: str) -> str:
        """JSON 형식 검증."""
        if v:
            try:
                json.loads(v)
            except json.JSONDecodeError:
                raise ValueError("peer_regions must be valid JSON")
        return v

    def get_peer_endpoints(self) -> list[RegionEndpoint]:
        """
        피어 리전 엔드포인트 목록 반환.

        Returns:
            RegionEndpoint 목록
        """
        if not self.peer_regions:
            return []

        try:
            data = json.loads(self.peer_regions)
            return [
                RegionEndpoint(
                    region=r["region"],
                    redis_url=r.get("redis_url", ""),
                    kafka_bootstrap=r.get("kafka_bootstrap", ""),
                    api_endpoint=r.get("api_endpoint", ""),
                    priority=r.get("priority", 100),
                )
                for r in data
            ]
        except Exception as e:
            logger.warning(f"[MultiRegion] Failed to parse peer_regions: {e}")
            return []

    def is_primary(self) -> bool:
        """현재 리전이 Primary인지 확인."""
        return self.region_role == "primary"

    def is_secondary(self) -> bool:
        """현재 리전이 Secondary인지 확인."""
        return self.region_role == "secondary"

    def is_readonly(self) -> bool:
        """현재 리전이 읽기 전용인지 확인."""
        return self.region_role == "readonly"


@lru_cache(maxsize=1)
def get_multiregion_settings() -> MultiRegionSettings:
    """
    MultiRegionSettings 싱글톤 반환.

    Returns:
        MultiRegionSettings 인스턴스
    """
    return MultiRegionSettings()


def reset_multiregion_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_multiregion_settings.cache_clear()

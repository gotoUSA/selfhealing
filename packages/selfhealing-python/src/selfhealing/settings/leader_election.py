"""
Leader Election Settings - Pydantic v2.

클러스터 내 리더 선출을 위한 설정 클래스.
Redis/etcd 기반 분산 리더 선출 지원.

Moved from: coordination/config.py (위치 통일)
"""

from __future__ import annotations

import structlog
import os
import socket
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = structlog.get_logger()


class LeaderElectionSettings(BaseSettings):
    """
    Leader Election 설정.

    환경변수 접두사: SELFHEALING_LEADER_
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_LEADER_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # 활성화 설정
    enabled: bool = Field(
        default=True,
        description="Leader Election 활성화 여부",
    )

    # 백엔드 설정
    backend: Literal["redis", "etcd"] = Field(
        default="redis",
        description="리더 선출 백엔드 (redis 또는 etcd)",
    )

    # 노드 식별자
    node_id: str = Field(
        default="",
        description="노드 고유 ID (비어있으면 hostname 사용)",
    )

    # Lease 설정
    lease_ttl_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        description="리더십 Lease 유효 기간 (초)",
    )

    renew_interval_seconds: float | None = Field(
        default=None,
        ge=1.0,
        description="Lease 갱신 주기 (초). None이면 자동 계산",
    )

    # Safe Margin 설정
    lease_safety_margin_ratio: float = Field(
        default=0.1,
        ge=0.05,
        le=0.3,
        description="Lease 갱신 안전 마진 (TTL 대비 비율)",
    )

    # 재시도 설정
    retry_interval_seconds: float = Field(
        default=5.0,
        ge=1.0,
        description="선출 실패 시 재시도 주기 (초)",
    )

    retry_jitter_factor: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="재시도 간격 Jitter 비율 (Thundering Herd 방지)",
    )

    max_retry_attempts: int = Field(
        default=3,
        ge=0,
        description="연속 실패 최대 횟수 (0=무제한)",
    )

    # Self-Fencing 설정
    self_fencing_enabled: bool = Field(
        default=True,
        description="Lease 갱신 실패 시 즉시 리더십 포기",
    )

    # 리전 우선순위 설정
    region_priority: int = Field(
        default=100,
        ge=0,
        le=1000,
        description="리전 우선순위 (낮을수록 높은 우선순위)",
    )

    primary_region: str = Field(
        default="",
        description="Primary 리전 (예: ap-northeast-2)",
    )

    # Redis 설정
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis 연결 URL",
    )

    redis_key_prefix: str = Field(
        default="selfhealing:leader:",
        description="Redis 키 접두사",
    )

    # etcd 설정
    etcd_endpoints: str = Field(
        default="localhost:2379",
        description="etcd 엔드포인트 (쉼표 구분)",
    )

    etcd_key_prefix: str = Field(
        default="/selfhealing/leader/",
        description="etcd 키 접두사",
    )

    @model_validator(mode="after")
    def validate_timing_constraints(self) -> "LeaderElectionSettings":
        """타이밍 제약 조건 검증."""
        effective_interval = self.get_effective_renew_interval()

        # renew_interval은 lease_ttl/2보다 작아야 함 (최소 2회 갱신 기회 보장)
        max_allowed = self.lease_ttl_seconds / 2
        if effective_interval >= max_allowed:
            raise ValueError(
                f"renew_interval ({effective_interval}s) must be < " f"lease_ttl/2 ({max_allowed}s) for safe renewal"
            )

        # 권장 범위 확인 (lease_ttl/4 ~ lease_ttl/3)
        recommended_min = self.lease_ttl_seconds / 4
        recommended_max = self.lease_ttl_seconds / 3
        if not (recommended_min <= effective_interval <= recommended_max):
            logger.warning(
                f"renew_interval ({effective_interval}s) outside recommended "
                f"range [{recommended_min:.1f}s, {recommended_max:.1f}s]"
            )

        return self

    def get_node_id(self) -> str:
        """노드 ID 반환 (설정값 또는 호스트명)."""
        if self.node_id:
            return self.node_id

        # Kubernetes Pod 이름 우선
        pod_name = os.environ.get("HOSTNAME", "")
        if pod_name:
            return pod_name

        return socket.gethostname()

    def get_effective_renew_interval(self) -> float:
        """
        실제 사용될 갱신 주기 반환.

        사용자 지정 값이 있으면 사용, 없으면 자동 계산:
        - 기본: lease_ttl/3 - safety_margin
        """
        if self.renew_interval_seconds is not None:
            return self.renew_interval_seconds

        # 자동 계산: TTL의 1/3에서 안전 마진 차감
        base = self.lease_ttl_seconds / 3
        margin = self.lease_ttl_seconds * self.lease_safety_margin_ratio
        return max(base - margin, 1.0)


@lru_cache(maxsize=1)
def get_leader_election_settings() -> LeaderElectionSettings:
    """설정 싱글톤 반환."""
    return LeaderElectionSettings()


def reset_leader_election_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_leader_election_settings.cache_clear()

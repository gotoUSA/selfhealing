"""
Cluster Identity - Multi-Cluster SSOT.

각 Pod가 자신의 클러스터 정보를 인지하는 단일 진실 소스(SSOT).

코드 근거:
- redis_manager.py#L146-147: pod_id = os.environ.get("HOSTNAME", ...)
- 기존에 Pod ID만 인식, 클러스터/리전 정보 없음

Usage:
    from selfhealing.core.cluster_identity import get_cluster_identity
    
    identity = get_cluster_identity()
    print(identity.cluster_id)    # "seoul-prod-01"
    print(identity.region)        # "seoul"
    print(identity.full_prefix)   # "selfhealing:seoul:"

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClusterIdentity:
    """
    클러스터 식별 정보 (Immutable).
    
    Attributes:
        cluster_id: 클러스터 고유 ID (필수)
        region: 리전 식별자 (예: seoul, tokyo)
        environment: 환경 (dev, staging, prod)
        tenant: SaaS 테넌트 ID (옵션)
        pod_id: 현재 Pod ID
    """
    
    cluster_id: str
    region: Optional[str] = None
    environment: str = "production"
    tenant: Optional[str] = None
    pod_id: str = field(
        default_factory=lambda: os.environ.get("HOSTNAME", "unknown")
    )
    
    @property
    def namespace(self) -> str:
        """Redis 키 네임스페이스 반환."""
        # 우선순위: region > tenant > environment
        return self.region or self.tenant or self.environment
    
    @property
    def full_prefix(self) -> str:
        """완전한 Redis 키 프리픽스 반환."""
        return f"selfhealing:{self.namespace}:"
    
    @property
    def trace_id_prefix(self) -> str:
        """Trace ID용 클러스터 접두사."""
        # 짧게: 리전 앞 3글자 + 환경 앞 1글자
        region_short = (self.region or "unk")[:3]
        env_short = self.environment[0] if self.environment else "u"
        return f"{region_short}{env_short}"
    
    def validate(self, fail_fast: Optional[bool] = None) -> bool:
        """
        클러스터 ID 유효성 검증.
        
        Fail-Fast 강화:
        - SELFHEALING_CLUSTER_ID 누락 시 프로세스 즉시 중단 옵션
        - 잘못된 네임스페이스 건드리는 것을 원천 방지
        
        코드 근거:
        - tools/hold_row_lock.py#L160-162: Fail-Fast 패턴 존재
        - apps.py#L287-295: Quarantine Mode 패턴 존재
        
        Args:
            fail_fast: True면 sys.exit(1), False면 Quarantine Mode
                       None이면 환경변수 SELFHEALING_FAIL_FAST 참조 (기본: True)
        
        Returns:
            유효하면 True, 아니면 False (fail_fast=False일 때만)
        """
        # 환경변수에서 fail_fast 설정 읽기
        if fail_fast is None:
            fail_fast = os.environ.get(
                "SELFHEALING_FAIL_FAST", "true"
            ).lower() == "true"
        
        if not self.cluster_id or self.cluster_id in ("unknown", "default"):
            error_msg = (
                "❌ [FATAL] SELFHEALING_CLUSTER_ID not set or invalid. "
                "Refusing to start to prevent namespace collision. "
                f"Current value: '{self.cluster_id}'"
            )
            
            if fail_fast:
                logger.critical(error_msg)
                import sys
                sys.exit(1)  # Fail-Fast: 즉시 종료
            else:
                logger.error(
                    f"{error_msg} "
                    "Running in Quarantine Mode (SELFHEALING_FAIL_FAST=false)"
                )
                return False
        
        logger.info(
            f"✅ [ClusterIdentity] Cluster: {self.cluster_id}, "
            f"Region: {self.region}, Env: {self.environment}, Pod: {self.pod_id}"
        )
        return True


# =============================================================================
# Factory & Singleton
# =============================================================================

_identity: Optional[ClusterIdentity] = None


def get_cluster_identity(skip_validation: bool = False) -> ClusterIdentity:
    """
    ClusterIdentity 싱글톤 반환.
    
    Args:
        skip_validation: True면 validation 스킵 (테스트용)
    
    Returns:
        ClusterIdentity 인스턴스
    """
    global _identity
    if _identity is None:
        _identity = ClusterIdentity(
            cluster_id=os.environ.get("SELFHEALING_CLUSTER_ID", "default"),
            region=os.environ.get("SELFHEALING_REGION"),
            environment=os.environ.get("SELFHEALING_ENV", "production"),
            tenant=os.environ.get("SELFHEALING_TENANT"),
        )
        if not skip_validation:
            # Fail-Fast 비활성화 상태에서만 validation 실행
            # 기본적으로 개발 환경에서는 경고만 출력
            _identity.validate(fail_fast=False)
    return _identity


def reset_cluster_identity() -> None:
    """테스트용 싱글톤 리셋."""
    global _identity
    _identity = None

"""
Namespace Settings - Multi-Cluster Support.

환경변수로 클러스터/리전/테넌트 등의 네임스페이스를 설정합니다.

Usage:
    # 방법 1: 통합 네임스페이스
    SELFHEALING_NAMESPACE=seoul
    
    # 방법 2: 개별 설정 (우선순위: NAMESPACE > REGION > TENANT > ENV)
    SELFHEALING_REGION=seoul
    SELFHEALING_TENANT=customer123
    SELFHEALING_ENV=production

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""
from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NamespaceSettings(BaseSettings):
    """
    네임스페이스 설정.
    
    다중 클러스터/리전/테넌트 환경에서 Redis 키를 분리합니다.
    """
    
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    
    # 통합 네임스페이스 (최우선)
    namespace: Optional[str] = Field(
        default=None,
        description="Unified namespace (highest priority)",
    )
    
    # 개별 설정 (우선순위 순)
    region: Optional[str] = Field(
        default=None,
        description="Region identifier (e.g., seoul, tokyo)",
    )
    tenant: Optional[str] = Field(
        default=None,
        description="Tenant identifier for SaaS multi-tenancy",
    )
    env: Optional[str] = Field(
        default=None,
        description="Environment (dev, staging, production)",
    )
    
    # 기본값 (아무것도 설정 안 된 경우)
    default_namespace: str = Field(
        default="default",
        description="Fallback namespace when nothing is set",
    )
    
    # 네임스페이스 활성화 여부
    namespace_enabled: bool = Field(
        default=False,
        description="Enable namespace-based key prefixing",
    )
    
    def get_effective_namespace(self) -> str:
        """
        유효 네임스페이스 반환.
        
        우선순위: namespace > region > tenant > env > default
        
        Returns:
            유효한 네임스페이스 문자열. 비활성화 시 빈 문자열.
        """
        if not self.namespace_enabled:
            return ""  # 비활성화 시 빈 문자열 (기존 동작 유지)
        
        return (
            self.namespace or
            self.region or
            self.tenant or
            self.env or
            self.default_namespace
        )
    
    def get_key_prefix(self, base_prefix: str = "selfhealing") -> str:
        """
        Redis 키 프리픽스 생성.
        
        Args:
            base_prefix: 기본 프리픽스
            
        Returns:
            완전한 키 프리픽스 (예: "selfhealing:seoul:" 또는 "selfhealing:")
        """
        ns = self.get_effective_namespace()
        if ns:
            return f"{base_prefix}:{ns}:"
        return f"{base_prefix}:"


# =============================================================================
# Singleton
# =============================================================================

_settings: Optional[NamespaceSettings] = None


def get_namespace_settings() -> NamespaceSettings:
    """NamespaceSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = NamespaceSettings()
    return _settings


def reset_namespace_settings() -> None:
    """테스트용 싱글톤 리셋."""
    global _settings
    _settings = None


def get_key_prefix(base_prefix: str = "selfhealing") -> str:
    """
    현재 네임스페이스 기반 키 프리픽스 반환.
    
    편의 함수로, 어디서든 호출 가능.
    
    Args:
        base_prefix: 기본 프리픽스
        
    Returns:
        완전한 키 프리픽스
    """
    return get_namespace_settings().get_key_prefix(base_prefix)

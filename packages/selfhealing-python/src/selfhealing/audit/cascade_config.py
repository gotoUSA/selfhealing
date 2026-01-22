"""
Cascade Audit 설정.

Cascade Event 처리와 관련된 설정값들을 정의합니다.

Settings:
- CascadeChainConfig: 체인 깊이 제한 설정
- CascadeRetentionConfig: 데이터 보관 정책 (Phase 4에서 구현)
- AuditBackpressureConfig: 배압 설정 (Phase 5에서 구현)

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

from dataclasses import dataclass


# =============================================================================
# CascadeChainConfig
# =============================================================================


@dataclass
class CascadeChainConfig:
    """
    Cascade 체인 깊이 설정.
    
    자동화 시스템 간의 과도한 연쇄 반응을 방지하기 위해
    체인 깊이를 제한합니다.
    
    Attributes:
        max_chain_depth: 최대 체인 깊이 (초과 시 경고/차단)
        warn_at_depth: 경고를 발생시킬 깊이
        block_on_exceed: 깊이 초과 시 차단 여부
        detect_cycles: 순환 참조 감지 활성화
    
    Code reference:
        services/error_budget/propagation.py#L78-84 (max_hops 패턴)
    """
    
    max_chain_depth: int = 10
    """
    최대 체인 깊이.
    
    이 값을 초과하면 경고 발생 또는 차단.
    기본값 10은 대부분의 정상 케이스를 커버합니다.
    """
    
    warn_at_depth: int = 7
    """
    경고를 발생시킬 깊이.
    
    이 깊이에 도달하면 로그 경고를 발생시킵니다.
    """
    
    block_on_exceed: bool = True
    """
    깊이 초과 시 차단 여부.
    
    True: CascadeChainDepthExceeded 예외 발생
    False: 경고만 발생하고 계속 진행
    """
    
    detect_cycles: bool = True
    """
    순환 참조 감지 활성화.
    
    True: 순환 참조 감지 시 CascadeCycleDetected 예외 발생
    False: 순환 참조 감지 비활성화
    """
    
    def __post_init__(self) -> None:
        """설정값 검증."""
        if self.warn_at_depth >= self.max_chain_depth:
            # warn_at_depth는 max_chain_depth보다 작아야 함
            self.warn_at_depth = max(1, self.max_chain_depth - 3)


# =============================================================================
# Default Configurations
# =============================================================================


DEFAULT_CASCADE_CHAIN_CONFIG = CascadeChainConfig()
"""기본 Cascade 체인 설정."""


def get_cascade_chain_config() -> CascadeChainConfig:
    """
    Cascade 체인 설정 반환.
    
    Django settings 또는 환경 변수에서 설정을 로드합니다.
    """
    import os
    
    try:
        from django.conf import settings
        
        return CascadeChainConfig(
            max_chain_depth=getattr(
                settings, "SELFHEALING_CASCADE_MAX_DEPTH", 10
            ),
            warn_at_depth=getattr(
                settings, "SELFHEALING_CASCADE_WARN_DEPTH", 7
            ),
            block_on_exceed=getattr(
                settings, "SELFHEALING_CASCADE_BLOCK_ON_EXCEED", True
            ),
            detect_cycles=getattr(
                settings, "SELFHEALING_CASCADE_DETECT_CYCLES", True
            ),
        )
    except Exception:
        # Django 없는 환경에서는 환경 변수 사용
        return CascadeChainConfig(
            max_chain_depth=int(
                os.environ.get("SELFHEALING_CASCADE_MAX_DEPTH", "10")
            ),
            warn_at_depth=int(
                os.environ.get("SELFHEALING_CASCADE_WARN_DEPTH", "7")
            ),
            block_on_exceed=os.environ.get(
                "SELFHEALING_CASCADE_BLOCK_ON_EXCEED", "true"
            ).lower() == "true",
            detect_cycles=os.environ.get(
                "SELFHEALING_CASCADE_DETECT_CYCLES", "true"
            ).lower() == "true",
        )

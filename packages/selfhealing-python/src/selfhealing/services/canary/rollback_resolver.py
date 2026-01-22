"""
롤백 값 해결기.

Rollback 시 사용할 previous_values를 어디서 가져올지 결정합니다.
3단계 폴백 전략: previous_values -> ConfigHistory -> DefaultConfig

주요 기능:
- RollbackValueSource: 롤백 값 소스 enum
- ResolvedRollbackValue: 해결된 롤백 값 결과
- RollbackValueResolver: 롤백 값 해결 로직

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.12
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RollbackValueSource(str, Enum):
    """
    롤백 값 소스.
    
    Rollback 시 previous_values를 어디서 가져오는지 정의합니다.
    """
    
    PREVIOUS_VALUES = "previous_values"
    """Tier 1: 롤아웃에 저장된 previous_values."""
    
    CONFIG_HISTORY = "config_history"
    """Tier 2: ConfigHistory 서비스에서 조회."""
    
    DEFAULT_CONFIG = "default_config"
    """Tier 3: 하드코딩된 기본 설정."""
    
    UNKNOWN = "unknown"
    """모든 소스 실패."""


@dataclass
class ResolvedRollbackValue:
    """
    해결된 롤백 값 결과.
    
    Attributes:
        values: 롤백할 설정 값들
        source: 값을 가져온 소스
        is_fallback: 폴백 값 사용 여부
        fallback_reason: 폴백 사유
        warning: 경고 메시지
    """
    
    values: Dict[str, Any]
    """롤백할 설정 값들."""
    
    source: RollbackValueSource
    """값을 가져온 소스."""
    
    is_fallback: bool = False
    """폴백 값 사용 여부."""
    
    fallback_reason: Optional[str] = None
    """폴백 사유."""
    
    warning: Optional[str] = None
    """경고 메시지."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "values": self.values,
            "source": self.source.value,
            "is_fallback": self.is_fallback,
            "fallback_reason": self.fallback_reason,
            "warning": self.warning,
        }


class RollbackValueResolver:
    """
    롤백 값 해결기.
    
    3단계 폴백 전략:
    1. Tier 1: previous_values (롤아웃에 저장된 값)
    2. Tier 2: ConfigHistory (설정 이력 서비스)
    3. Tier 3: DefaultConfig (하드코딩된 기본값)
    
    Attributes:
        config_history_service: ConfigHistory 서비스 (Tier 2용)
        default_configs: 기본 설정 (Tier 3용)
    """
    
    # 하드코딩된 기본 설정 (Tier 3)
    DEFAULT_CONFIGS: Dict[str, Dict[str, Any]] = {
        "circuit_breaker": {
            "failure_threshold": 5,
            "success_threshold": 3,
            "timeout_seconds": 30,
            "half_open_max_calls": 3,
        },
        "dlq": {
            "max_retry_count": 3,
            "retry_delay_seconds": 60,
            "dead_letter_queue_enabled": True,
        },
        "rate_limiter": {
            "requests_per_second": 100,
            "burst_size": 50,
            "enabled": True,
        },
        "retry": {
            "max_attempts": 3,
            "initial_delay_ms": 100,
            "max_delay_ms": 5000,
            "multiplier": 2.0,
        },
    }
    
    def __init__(
        self,
        config_history_service: Optional[Any] = None,
        default_configs: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        """
        RollbackValueResolver 초기화.
        
        Args:
            config_history_service: ConfigHistory 서비스 인스턴스
            default_configs: 커스텀 기본 설정 (None이면 DEFAULT_CONFIGS 사용)
        """
        self._config_history_service = config_history_service
        self._default_configs = (
            default_configs if default_configs is not None 
            else self.DEFAULT_CONFIGS.copy()
        )
    
    def resolve(
        self,
        rollout_id: str,
        config_type: str,
        previous_values: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> ResolvedRollbackValue:
        """
        롤백 값 해결.
        
        Args:
            rollout_id: 롤아웃 ID
            config_type: 설정 유형 (circuit_breaker, dlq 등)
            previous_values: Tier 1 값 (롤아웃에 저장된 값)
            created_at: 롤아웃 생성 시각 (Tier 2용)
        
        Returns:
            해결된 롤백 값
        """
        # Tier 1: previous_values
        if previous_values:
            return ResolvedRollbackValue(
                values=previous_values,
                source=RollbackValueSource.PREVIOUS_VALUES,
                is_fallback=False,
            )
        
        # Tier 2: ConfigHistory
        if self._config_history_service and created_at:
            try:
                version = self._config_history_service.get_version_before(
                    config_type=config_type,
                    before=created_at,
                )
                if version and version.values:
                    return ResolvedRollbackValue(
                        values=version.values,
                        source=RollbackValueSource.CONFIG_HISTORY,
                        is_fallback=True,
                        fallback_reason="previous_values was empty, using config history",
                    )
            except Exception as e:
                logger.warning(
                    f"[RollbackValueResolver] ConfigHistory lookup failed: {e}"
                )
        
        # Tier 3: DefaultConfig
        if config_type in self._default_configs:
            return ResolvedRollbackValue(
                values=self._default_configs[config_type],
                source=RollbackValueSource.DEFAULT_CONFIG,
                is_fallback=True,
                fallback_reason="previous_values and config_history unavailable",
                warning=f"Using hardcoded default config for {config_type}",
            )
        
        # 모든 Tier 실패
        return ResolvedRollbackValue(
            values={},
            source=RollbackValueSource.UNKNOWN,
            is_fallback=True,
            fallback_reason="All tiers exhausted",
            warning=f"CRITICAL: No rollback values found for {config_type}",
        )


__all__ = [
    "RollbackValueSource",
    "ResolvedRollbackValue",
    "RollbackValueResolver",
]

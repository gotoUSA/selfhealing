"""
Exception Budget Weight Map.

ErrorCode별 Error Budget 소진 가중치를 정의합니다.
에러 유형에 따라 Error Budget에 미치는 영향도가 다릅니다.

Weight Categories:
- SYSTEM_*: 1.0x (내부 시스템 결함 - 최대 영향)
- SERVICE_*: 0.5x (외부 서비스 - 부분 책임)
- RESOURCE_*: 0.3x (리소스 문제)
- AUTH_*: 0.2x (보안 관련)
- VALIDATION_*: 0.1x (클라이언트 오류)

Weight Combination Policy:
- MAX: 가장 높은 가중치만 적용 (권장)
- SUM: 가중치 합산
- MULTIPLY: 가중치 곱셈 (버짓 폭발 위험)

Usage:
    from selfhealing.services.error_budget.exception_weights import (
        ExceptionBudgetWeightMap,
        WeightCombinePolicy,
        get_weight_for_error_code,
        combine_weights,
    )
    
    # ErrorCode별 가중치 조회
    weight = get_weight_for_error_code(ErrorCode.SYSTEM_INTERNAL_ERROR)  # 1.0
    
    # EmergencyLevel과 ErrorCode 가중치 결합
    final = combine_weights(
        emergency_weight=5.0,
        error_weight=2.0,
        policy=WeightCombinePolicy.MAX,  # → 5.0
    )
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)


class WeightCombinePolicy(Enum):
    """
    가중치 중첩 정책.
    
    EmergencyLevel 가중치와 ErrorCode 가중치가 동시 적용될 때의 계산 방식.
    """
    
    MAX = "max"
    """
    최댓값 정책 (권장).
    
    가장 강력한 리스크 인자 하나만 채택합니다.
    안정적인 운영을 위해 권장됩니다.
    
    Example:
        LEVEL_3(5.0x) + CONFIG_LOCKED(2.0x) → 5.0x
    """
    
    SUM = "sum"
    """
    합산 정책.
    
    두 가중치를 더합니다. 선형 증가로 예측 가능합니다.
    
    Example:
        LEVEL_3(5.0x) + CONFIG_LOCKED(2.0x) → 7.0x
    """
    
    MULTIPLY = "multiply"
    """
    곱셈 정책 (비권장).
    
    두 가중치를 곱합니다. 버짓 폭발 위험이 있습니다.
    
    Example:
        LEVEL_3(5.0x) * CONFIG_LOCKED(2.0x) → 10.0x
    """


# =============================================================================
# Default Weight Configuration
# =============================================================================

# ErrorCode 카테고리별 기본 가중치
DEFAULT_CATEGORY_WEIGHTS: Dict[str, float] = {
    "SYSTEM": 1.0,       # 내부 시스템 결함 - 최대 영향
    "SERVICE": 0.5,      # 외부 서비스 - 부분 책임
    "RESOURCE": 0.3,     # 리소스 문제
    "CONFIG": 0.4,       # 설정 관련
    "AUTH": 0.2,         # 인증 관련
    "AUTHZ": 0.2,        # 인가 관련
    "RATE": 0.1,         # 요청 제한
    "VALIDATION": 0.1,   # 클라이언트 오류
}

# 개별 ErrorCode 가중치 오버라이드 (카테고리 기본값보다 우선)
DEFAULT_CODE_WEIGHTS: Dict[str, float] = {
    # SYSTEM 카테고리 내 세부 조정
    "SYSTEM_INTERNAL_ERROR": 1.0,
    "SYSTEM_DATABASE_ERROR": 1.0,
    "SYSTEM_DLQ_ERROR": 0.8,
    
    # SERVICE 카테고리 내 세부 조정
    "SERVICE_CIRCUIT_OPEN": 0.3,   # Circuit Breaker는 의도된 동작
    "SERVICE_TIMEOUT": 0.5,
    "SERVICE_UNAVAILABLE": 0.5,
    "SERVICE_BAD_GATEWAY": 0.5,
    
    # CONFIG 카테고리
    "CONFIG_LOCKED": 0.4,          # 락은 정상적인 동시성 제어
    "CONFIG_INVALID": 0.3,
    
    # AUTHZ 카테고리
    "AUTHZ_ERROR_BUDGET_BLOCKED": 0.0,  # 자체 차단은 버짓 소진 없음
    "AUTHZ_GOVERNANCE_BLOCKED": 0.0,    # 거버넌스 차단도 마찬가지
}


@dataclass
class ExceptionBudgetWeightMap:
    """
    ErrorCode별 Error Budget 소진 가중치 매핑.
    
    카테고리별 기본 가중치와 개별 코드 오버라이드를 지원합니다.
    환경변수로 런타임 설정이 가능합니다.
    
    Attributes:
        category_weights: 카테고리별 기본 가중치
        code_weights: 개별 ErrorCode 가중치 (오버라이드)
        default_weight: 매핑되지 않은 코드의 기본 가중치
    """
    
    category_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CATEGORY_WEIGHTS)
    )
    code_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CODE_WEIGHTS)
    )
    default_weight: float = 1.0
    
    def get_weight(self, error_code) -> float:
        """
        ErrorCode에 대한 가중치 반환.
        
        우선순위:
        1. code_weights (개별 코드 오버라이드)
        2. category_weights (카테고리 기본값)
        3. default_weight (폴백)
        
        Args:
            error_code: ErrorCode enum 또는 문자열
        
        Returns:
            가중치 값 (0.0 ~ 1.0)
        """
        # ErrorCode enum 또는 문자열 처리
        code_str = error_code.value if hasattr(error_code, 'value') else str(error_code)
        
        # 1. 개별 코드 오버라이드
        if code_str in self.code_weights:
            return self.code_weights[code_str]
        
        # 2. 카테고리 기본값 (코드에서 카테고리 추출)
        category = self._extract_category(code_str)
        if category and category in self.category_weights:
            return self.category_weights[category]
        
        # 3. 폴백
        return self.default_weight
    
    def _extract_category(self, code: str) -> Optional[str]:
        """
        ErrorCode 문자열에서 카테고리 추출.
        
        Example:
            "SYSTEM_INTERNAL_ERROR" → "SYSTEM"
            "VALIDATION_FIELD_REQUIRED" → "VALIDATION"
        """
        if "_" in code:
            return code.split("_")[0]
        return None
    
    def set_code_weight(self, error_code, weight: float) -> None:
        """
        개별 ErrorCode 가중치 설정.
        
        Args:
            error_code: ErrorCode enum 또는 문자열
            weight: 가중치 값
        """
        code_str = error_code.value if hasattr(error_code, 'value') else str(error_code)
        self.code_weights[code_str] = weight
        logger.debug(f"[ExceptionWeights] Set weight: {code_str}={weight}")
    
    def set_category_weight(self, category: str, weight: float) -> None:
        """
        카테고리 기본 가중치 설정.
        
        Args:
            category: 카테고리 이름 (SYSTEM, SERVICE 등)
            weight: 가중치 값
        """
        self.category_weights[category.upper()] = weight
        logger.debug(f"[ExceptionWeights] Set category weight: {category}={weight}")
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "category_weights": self.category_weights,
            "code_weights": self.code_weights,
            "default_weight": self.default_weight,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExceptionBudgetWeightMap":
        """딕셔너리에서 생성."""
        return cls(
            category_weights=data.get("category_weights", dict(DEFAULT_CATEGORY_WEIGHTS)),
            code_weights=data.get("code_weights", dict(DEFAULT_CODE_WEIGHTS)),
            default_weight=data.get("default_weight", 1.0),
        )


# =============================================================================
# Singleton Instance
# =============================================================================

_weight_map: Optional[ExceptionBudgetWeightMap] = None


def get_exception_weight_map() -> ExceptionBudgetWeightMap:
    """
    ExceptionBudgetWeightMap 싱글톤 반환.
    
    설정 우선순위:
    1. settings/error_budget.py의 exception_weights_json
    2. 환경변수 SELFHEALING_EXCEPTION_WEIGHTS_JSON (직접)
    3. 기본값
    """
    global _weight_map
    
    if _weight_map is None:
        weights_json = None
        
        # 1. Pydantic 설정에서 로드 시도
        try:
            from selfhealing.settings.error_budget import get_error_budget_settings
            settings = get_error_budget_settings()
            weights_json = settings.exception_weights_json
        except ImportError:
            pass
        
        # 2. 환경변수 직접 확인 (설정이 없을 경우)
        if not weights_json:
            weights_json = os.environ.get("SELFHEALING_EXCEPTION_WEIGHTS_JSON")
        
        # 3. JSON 파싱 및 WeightMap 생성
        if weights_json:
            try:
                import json
                data = json.loads(weights_json)
                _weight_map = ExceptionBudgetWeightMap.from_dict(data)
                logger.info("[ExceptionWeights] Loaded custom weights from settings")
            except Exception as e:
                logger.warning(f"[ExceptionWeights] Failed to parse weights JSON: {e}")
                _weight_map = ExceptionBudgetWeightMap()
        else:
            _weight_map = ExceptionBudgetWeightMap()
    
    return _weight_map


def reset_exception_weight_map() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _weight_map
    _weight_map = None


# =============================================================================
# Convenience Functions
# =============================================================================

def get_weight_for_error_code(error_code) -> float:
    """
    ErrorCode에 대한 Error Budget 소진 가중치 반환.
    
    Args:
        error_code: ErrorCode enum 또는 문자열
    
    Returns:
        가중치 값 (0.0 ~ 1.0)
    
    Example:
        weight = get_weight_for_error_code(ErrorCode.SYSTEM_INTERNAL_ERROR)  # 1.0
        weight = get_weight_for_error_code("VALIDATION_FIELD_REQUIRED")       # 0.1
    """
    return get_exception_weight_map().get_weight(error_code)


def combine_weights(
    emergency_weight: float,
    error_weight: float,
    policy: WeightCombinePolicy = WeightCombinePolicy.MAX,
    max_weight: Optional[float] = None,
) -> float:
    """
    EmergencyLevel 가중치와 ErrorCode 가중치 결합.
    
    Args:
        emergency_weight: EmergencyLevel 기반 가중치 (CrisisMultiplier)
        error_weight: ErrorCode 기반 가중치
        policy: 결합 정책 (기본: MAX)
        max_weight: 최대 가중치 상한 (기본: 설정에서 로드)
    
    Returns:
        결합된 가중치 (max_weight 이하)
    
    Example:
        # LEVEL_3(5.0x) + CONFIG_LOCKED(0.4x) with MAX policy
        result = combine_weights(5.0, 0.4, WeightCombinePolicy.MAX)  # → 5.0
        
        # with SUM policy
        result = combine_weights(5.0, 0.4, WeightCombinePolicy.SUM)  # → 5.4
    """
    # 기본 max_weight 로드
    if max_weight is None:
        try:
            from selfhealing.settings.error_budget import get_error_budget_settings
            max_weight = get_error_budget_settings().multiplier_max
        except ImportError:
            max_weight = 10.0
    
    # 정책별 계산
    if policy == WeightCombinePolicy.MAX:
        combined = max(emergency_weight, error_weight)
    elif policy == WeightCombinePolicy.SUM:
        combined = emergency_weight + error_weight
    elif policy == WeightCombinePolicy.MULTIPLY:
        combined = emergency_weight * error_weight
    else:
        combined = max(emergency_weight, error_weight)
    
    # 상한 적용
    result = min(combined, max_weight)
    
    logger.debug(
        f"[ExceptionWeights] combine_weights: "
        f"emergency={emergency_weight}, error={error_weight}, "
        f"policy={policy.value}, result={result}"
    )
    
    return result


def get_weight_combine_policy() -> WeightCombinePolicy:
    """
    현재 가중치 결합 정책 반환.
    
    설정 우선순위:
    1. settings/error_budget.py의 weight_combine_policy
    2. 환경변수 SELFHEALING_WEIGHT_COMBINE_POLICY (직접)
    3. 기본값 MAX
    
    Returns:
        WeightCombinePolicy enum
    """
    policy_str = None
    
    # 1. Pydantic 설정에서 로드 시도
    try:
        from selfhealing.settings.error_budget import get_error_budget_settings
        settings = get_error_budget_settings()
        policy_str = settings.weight_combine_policy
    except ImportError:
        pass
    
    # 2. 환경변수 직접 확인 (설정이 없을 경우)
    if not policy_str:
        policy_str = os.environ.get("SELFHEALING_WEIGHT_COMBINE_POLICY", "MAX")
    
    policy_str = policy_str.upper()
    
    try:
        return WeightCombinePolicy[policy_str]
    except KeyError:
        logger.warning(
            f"[ExceptionWeights] Invalid policy '{policy_str}', using MAX"
        )
        return WeightCombinePolicy.MAX


__all__ = [
    "WeightCombinePolicy",
    "ExceptionBudgetWeightMap",
    "get_exception_weight_map",
    "reset_exception_weight_map",
    "get_weight_for_error_code",
    "combine_weights",
    "get_weight_combine_policy",
    "DEFAULT_CATEGORY_WEIGHTS",
    "DEFAULT_CODE_WEIGHTS",
]

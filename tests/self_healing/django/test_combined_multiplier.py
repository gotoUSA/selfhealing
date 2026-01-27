"""
Crisis Multiplier 가중치 결합 테스트.

테스트 범위:
1. get_combined_multiplier() 기본 동작
2. EmergencyLevel + ErrorCode 가중치 결합
3. WeightCombinePolicy별 계산 (MAX, SUM, MULTIPLY)
4. max_multiplier 상한 적용
5. 환경변수 설정 반영

Docker Compose로 실행:
    docker-compose -f docker-compose.test.yml run --rm test \
        pytest tests/self_healing/django/test_combined_multiplier.py -v
"""

import os
from unittest.mock import MagicMock, patch

import pytest


class TestGetCombinedMultiplier:
    """get_combined_multiplier() 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_singletons(self):
        """테스트 전후 싱글톤 초기화."""
        from selfhealing.services.error_budget.multiplier import (
            reset_crisis_multiplier_provider,
        )
        from selfhealing.services.error_budget.exception_weights import (
            reset_exception_weight_map,
        )
        
        reset_crisis_multiplier_provider()
        reset_exception_weight_map()
        yield
        reset_crisis_multiplier_provider()
        reset_exception_weight_map()
    
    def test_combined_multiplier_basic(self):
        """기본 결합 가중치 계산."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
        )
        
        # Mock emergency tracker
        provider = CrisisMultiplierProvider()
        
        # EmergencyLevel 가중치 mock (NORMAL = 1.0)
        with patch.object(
            provider, "get_current_multiplier", return_value=1.0
        ):
            # ErrorCode 가중치: SYSTEM_INTERNAL_ERROR = 1.0
            result = provider.get_combined_multiplier(
                error_code="SYSTEM_INTERNAL_ERROR"
            )
            
            # MAX(1.0, 1.0) = 1.0
            assert result == 1.0
    
    def test_combined_multiplier_max_policy(self):
        """MAX 정책: 최댓값 반환."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
        )
        
        provider = CrisisMultiplierProvider()
        
        # EmergencyLevel = 5.0 (LEVEL_3)
        with patch.object(
            provider, "get_current_multiplier", return_value=5.0
        ):
            # ErrorCode: SERVICE_TIMEOUT = 0.5
            with patch.dict(os.environ, {"SELFHEALING_WEIGHT_COMBINE_POLICY": "MAX"}):
                result = provider.get_combined_multiplier(
                    error_code="SERVICE_TIMEOUT"
                )
                
                # MAX(5.0, 0.5) = 5.0
                assert result == 5.0
    
    def test_combined_multiplier_sum_policy(self):
        """SUM 정책: 합산."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
        )
        from selfhealing.services.error_budget.exception_weights import (
            reset_exception_weight_map,
            WeightCombinePolicy,
        )
        
        provider = CrisisMultiplierProvider()
        reset_exception_weight_map()
        
        # EmergencyLevel = 3.0
        with patch.object(
            provider, "get_current_multiplier", return_value=3.0
        ):
            # get_weight_combine_policy와 get_weight_for_error_code를 직접 mock
            with patch(
                "selfhealing.services.error_budget.exception_weights.get_weight_combine_policy",
                return_value=WeightCombinePolicy.SUM,
            ):
                with patch(
                    "selfhealing.services.error_budget.exception_weights.get_weight_for_error_code",
                    return_value=0.1,  # VALIDATION 카테고리
                ):
                    result = provider.get_combined_multiplier(
                        error_code="VALIDATION_FIELD_REQUIRED"
                    )
                    
                    # SUM(3.0, 0.1) = 3.1
                    assert result == 3.1
    
    def test_combined_multiplier_multiply_policy(self):
        """MULTIPLY 정책: 곱셈."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
        )
        from selfhealing.services.error_budget.exception_weights import (
            reset_exception_weight_map,
            WeightCombinePolicy,
        )
        
        provider = CrisisMultiplierProvider()
        reset_exception_weight_map()
        
        # EmergencyLevel = 2.0
        with patch.object(
            provider, "get_current_multiplier", return_value=2.0
        ):
            # get_weight_combine_policy와 get_weight_for_error_code를 직접 mock
            with patch(
                "selfhealing.services.error_budget.exception_weights.get_weight_combine_policy",
                return_value=WeightCombinePolicy.MULTIPLY,
            ):
                with patch(
                    "selfhealing.services.error_budget.exception_weights.get_weight_for_error_code",
                    return_value=0.5,  # SERVICE_UNAVAILABLE
                ):
                    result = provider.get_combined_multiplier(
                        error_code="SERVICE_UNAVAILABLE"
                    )
                    
                    # MULTIPLY(2.0, 0.5) = 1.0
                    assert result == 1.0
    
    def test_combined_multiplier_respects_max_multiplier(self):
        """max_multiplier 상한 적용."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
            CrisisMultiplierConfig,
        )
        from selfhealing.services.error_budget.exception_weights import (
            reset_exception_weight_map,
        )
        
        # max_multiplier = 5.0으로 설정
        config = CrisisMultiplierConfig(max_multiplier=5.0)
        provider = CrisisMultiplierProvider(config=config)
        reset_exception_weight_map()
        
        # EmergencyLevel = 8.0 (높은 값)
        with patch.object(
            provider, "get_current_multiplier", return_value=8.0
        ):
            with patch.dict(os.environ, {"SELFHEALING_WEIGHT_COMBINE_POLICY": "SUM"}):
                reset_exception_weight_map()
                
                # ErrorCode: SYSTEM_INTERNAL_ERROR = 1.0
                result = provider.get_combined_multiplier(
                    error_code="SYSTEM_INTERNAL_ERROR"
                )
                
                # SUM(8.0, 1.0) = 9.0 → min(9.0, 5.0) = 5.0
                assert result == 5.0
    
    def test_combined_multiplier_without_error_code(self):
        """ErrorCode 없이 호출 시 EmergencyLevel 가중치만 반환."""
        from selfhealing.services.error_budget.multiplier import (
            CrisisMultiplierProvider,
        )
        
        provider = CrisisMultiplierProvider()
        
        with patch.object(
            provider, "get_current_multiplier", return_value=3.5
        ):
            result = provider.get_combined_multiplier(error_code=None)
            
            # ErrorCode 가중치 = 1.0 (기본값)
            # MAX(3.5, 1.0) = 3.5
            assert result == 3.5


class TestCombinedMultiplierWithRealTracker:
    """실제 EmergencyTracker 연동 테스트."""
    
    @pytest.fixture(autouse=True)
    def reset_all(self):
        """모든 싱글톤 초기화."""
        from selfhealing.services.error_budget.multiplier import (
            reset_crisis_multiplier_provider,
        )
        from selfhealing.services.error_budget.exception_weights import (
            reset_exception_weight_map,
        )
        
        reset_crisis_multiplier_provider()
        reset_exception_weight_map()
        yield
        reset_crisis_multiplier_provider()
        reset_exception_weight_map()
    
    def test_end_to_end_combined_multiplier(self):
        """종단간 결합 가중치 계산."""
        from selfhealing.services.error_budget.multiplier import (
            get_crisis_multiplier_provider,
        )
        
        # 기본 설정으로 provider 획득
        provider = get_crisis_multiplier_provider()
        
        # Mock tracker state (NORMAL level)
        mock_tracker = MagicMock()
        mock_state = MagicMock()
        mock_state.emergency_level = MagicMock()
        mock_state.emergency_level.name = "NORMAL"
        mock_tracker.get_effective_state.return_value = mock_state
        
        with patch.object(
            provider, "_get_emergency_tracker", return_value=mock_tracker
        ):
            # NORMAL level = 1.0
            # SERVICE_CIRCUIT_OPEN = 0.3 (의도된 동작)
            result = provider.get_combined_multiplier(
                error_code="SERVICE_CIRCUIT_OPEN"
            )
            
            # MAX(1.0, 0.3) = 1.0
            assert result == 1.0

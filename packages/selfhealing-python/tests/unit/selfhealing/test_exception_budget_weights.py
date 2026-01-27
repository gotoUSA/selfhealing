"""
Exception Budget Weights 테스트.

테스트 범위:
1. ExceptionBudgetWeightMap 가중치 조회
2. 카테고리별 기본 가중치
3. 개별 ErrorCode 가중치 오버라이드
4. WeightCombinePolicy 정책별 계산
5. combine_weights() 함수
6. 환경변수 설정 반영
7. 싱글톤 동작
"""

import json
import os
from unittest.mock import patch

import pytest

from selfhealing.services.error_budget.exception_weights import (
    ExceptionBudgetWeightMap,
    WeightCombinePolicy,
    get_exception_weight_map,
    reset_exception_weight_map,
    get_weight_for_error_code,
    combine_weights,
    get_weight_combine_policy,
    DEFAULT_CATEGORY_WEIGHTS,
    DEFAULT_CODE_WEIGHTS,
)


class TestExceptionBudgetWeightMap:
    """ExceptionBudgetWeightMap 클래스 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """각 테스트 전후 싱글톤 초기화."""
        reset_exception_weight_map()
        yield
        reset_exception_weight_map()

    def test_default_category_weights(self):
        """기본 카테고리 가중치 확인."""
        weight_map = ExceptionBudgetWeightMap()

        # SYSTEM 카테고리는 최대 가중치
        assert weight_map.get_weight("SYSTEM_INTERNAL_ERROR") == 1.0

        # VALIDATION 카테고리는 최소 가중치
        assert weight_map.get_weight("VALIDATION_FIELD_REQUIRED") == 0.1

        # SERVICE 카테고리는 중간 가중치
        assert weight_map.get_weight("SERVICE_UNAVAILABLE") == 0.5

    def test_code_override_priority(self):
        """개별 코드 오버라이드가 카테고리보다 우선."""
        weight_map = ExceptionBudgetWeightMap()

        # SERVICE_CIRCUIT_OPEN은 개별 오버라이드 (0.3)
        assert weight_map.get_weight("SERVICE_CIRCUIT_OPEN") == 0.3

        # SERVICE_TIMEOUT은 카테고리 기본값이 아닌 개별값 (0.5)
        assert weight_map.get_weight("SERVICE_TIMEOUT") == 0.5

    def test_authz_blocked_zero_weight(self):
        """거버넌스/버짓 차단은 0 가중치."""
        weight_map = ExceptionBudgetWeightMap()

        # 자체 차단은 버짓 소진 없음
        assert weight_map.get_weight("AUTHZ_ERROR_BUDGET_BLOCKED") == 0.0
        assert weight_map.get_weight("AUTHZ_GOVERNANCE_BLOCKED") == 0.0

    def test_unknown_code_uses_default(self):
        """알 수 없는 코드는 기본값 사용."""
        weight_map = ExceptionBudgetWeightMap(default_weight=0.5)

        assert weight_map.get_weight("UNKNOWN_ERROR_CODE") == 0.5

    def test_set_code_weight(self):
        """개별 코드 가중치 설정."""
        weight_map = ExceptionBudgetWeightMap()

        weight_map.set_code_weight("CUSTOM_ERROR", 0.75)

        assert weight_map.get_weight("CUSTOM_ERROR") == 0.75

    def test_set_category_weight(self):
        """카테고리 가중치 설정."""
        weight_map = ExceptionBudgetWeightMap()

        weight_map.set_category_weight("CUSTOM", 0.6)

        assert weight_map.get_weight("CUSTOM_SOME_ERROR") == 0.6

    def test_to_dict_and_from_dict(self):
        """직렬화/역직렬화."""
        original = ExceptionBudgetWeightMap()
        original.set_code_weight("TEST_CODE", 0.9)

        data = original.to_dict()
        restored = ExceptionBudgetWeightMap.from_dict(data)

        assert restored.get_weight("TEST_CODE") == 0.9
        assert restored.get_weight("SYSTEM_INTERNAL_ERROR") == 1.0

    def test_extract_category(self):
        """카테고리 추출."""
        weight_map = ExceptionBudgetWeightMap()

        assert weight_map._extract_category("SYSTEM_INTERNAL_ERROR") == "SYSTEM"
        assert weight_map._extract_category("VALIDATION_FIELD_REQUIRED") == "VALIDATION"
        # 언더스코어가 있는 경우 첫 부분을 카테고리로 추출
        assert weight_map._extract_category("NO_UNDERSCORE") == "NO"
        # 언더스코어가 없는 경우 None
        assert weight_map._extract_category("NOUNDERSCORE") is None


class TestGetWeightForErrorCode:
    """get_weight_for_error_code() 함수 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_exception_weight_map()
        yield
        reset_exception_weight_map()

    def test_with_string_code(self):
        """문자열 코드로 가중치 조회."""
        weight = get_weight_for_error_code("SYSTEM_DATABASE_ERROR")
        assert weight == 1.0

    def test_with_string_validation_code(self):
        """VALIDATION 문자열 코드 가중치 조회."""
        weight = get_weight_for_error_code("VALIDATION_FIELD_INVALID")
        assert weight == 0.1

    def test_service_codes(self):
        """SERVICE 카테고리 코드별 가중치."""
        assert get_weight_for_error_code("SERVICE_CIRCUIT_OPEN") == 0.3
        assert get_weight_for_error_code("SERVICE_TIMEOUT") == 0.5
        assert get_weight_for_error_code("SERVICE_UNAVAILABLE") == 0.5


class TestWeightCombinePolicy:
    """WeightCombinePolicy 테스트."""

    def test_enum_values(self):
        """Enum 값 확인."""
        assert WeightCombinePolicy.MAX.value == "max"
        assert WeightCombinePolicy.SUM.value == "sum"
        assert WeightCombinePolicy.MULTIPLY.value == "multiply"

    def test_combine_weights_max_policy(self):
        """MAX 정책: 최댓값 선택."""
        result = combine_weights(
            emergency_weight=5.0,
            error_weight=2.0,
            policy=WeightCombinePolicy.MAX,
        )
        assert result == 5.0

        result = combine_weights(
            emergency_weight=1.0,
            error_weight=3.0,
            policy=WeightCombinePolicy.MAX,
        )
        assert result == 3.0

    def test_combine_weights_sum_policy(self):
        """SUM 정책: 합산."""
        result = combine_weights(
            emergency_weight=5.0,
            error_weight=2.0,
            policy=WeightCombinePolicy.SUM,
        )
        assert result == 7.0

    def test_combine_weights_multiply_policy(self):
        """MULTIPLY 정책: 곱셈."""
        result = combine_weights(
            emergency_weight=5.0,
            error_weight=2.0,
            policy=WeightCombinePolicy.MULTIPLY,
        )
        assert result == 10.0

    def test_combine_weights_respects_max_weight(self):
        """max_weight 상한 적용."""
        result = combine_weights(
            emergency_weight=10.0,
            error_weight=5.0,
            policy=WeightCombinePolicy.SUM,
            max_weight=10.0,
        )
        assert result == 10.0  # 15.0 → 10.0 (상한)

    def test_combine_weights_default_policy_is_max(self):
        """기본 정책은 MAX."""
        result = combine_weights(
            emergency_weight=5.0,
            error_weight=2.0,
        )
        assert result == 5.0  # MAX 적용


class TestGetWeightCombinePolicy:
    """get_weight_combine_policy() 함수 테스트."""

    def test_default_is_max(self):
        """기본값은 MAX."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("SELFHEALING_WEIGHT_COMBINE_POLICY", None)
            policy = get_weight_combine_policy()
            assert policy == WeightCombinePolicy.MAX

    def test_env_sum(self):
        """환경변수로 SUM 설정."""
        with patch("selfhealing.settings.error_budget.get_error_budget_settings") as mock_settings:
            # settings에서 None 반환하여 env var 사용하도록
            mock_settings.return_value.weight_combine_policy = None
            with patch.dict(os.environ, {"SELFHEALING_WEIGHT_COMBINE_POLICY": "SUM"}):
                policy = get_weight_combine_policy()
                assert policy == WeightCombinePolicy.SUM

    def test_env_multiply(self):
        """환경변수로 MULTIPLY 설정."""
        with patch("selfhealing.settings.error_budget.get_error_budget_settings") as mock_settings:
            # settings에서 None 반환하여 env var 사용하도록
            mock_settings.return_value.weight_combine_policy = None
            with patch.dict(os.environ, {"SELFHEALING_WEIGHT_COMBINE_POLICY": "MULTIPLY"}):
                policy = get_weight_combine_policy()
                assert policy == WeightCombinePolicy.MULTIPLY

    def test_invalid_env_falls_back_to_max(self):
        """잘못된 환경변수는 MAX로 폴백."""
        with patch.dict(os.environ, {"SELFHEALING_WEIGHT_COMBINE_POLICY": "INVALID"}):
            policy = get_weight_combine_policy()
            assert policy == WeightCombinePolicy.MAX


class TestEnvironmentConfiguration:
    """환경변수 설정 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_exception_weight_map()
        yield
        reset_exception_weight_map()

    def test_custom_weights_from_env(self):
        """환경변수에서 커스텀 가중치 로드."""
        custom_config = {
            "category_weights": {"SYSTEM": 0.8, "VALIDATION": 0.2},
            "code_weights": {"CUSTOM_CODE": 0.99},
            "default_weight": 0.5,
        }

        with patch.dict(os.environ, {"SELFHEALING_EXCEPTION_WEIGHTS_JSON": json.dumps(custom_config)}):
            reset_exception_weight_map()
            weight_map = get_exception_weight_map()

            assert weight_map.get_weight("SYSTEM_ERROR") == 0.8
            assert weight_map.get_weight("CUSTOM_CODE") == 0.99
            assert weight_map.default_weight == 0.5


class TestDefaultWeights:
    """기본 가중치 상수 테스트."""

    def test_category_weights_defined(self):
        """모든 주요 카테고리 정의됨."""
        assert "SYSTEM" in DEFAULT_CATEGORY_WEIGHTS
        assert "SERVICE" in DEFAULT_CATEGORY_WEIGHTS
        assert "VALIDATION" in DEFAULT_CATEGORY_WEIGHTS
        assert "AUTH" in DEFAULT_CATEGORY_WEIGHTS
        assert "AUTHZ" in DEFAULT_CATEGORY_WEIGHTS
        assert "RATE" in DEFAULT_CATEGORY_WEIGHTS
        assert "RESOURCE" in DEFAULT_CATEGORY_WEIGHTS
        assert "CONFIG" in DEFAULT_CATEGORY_WEIGHTS

    def test_system_is_highest(self):
        """SYSTEM이 가장 높은 가중치."""
        assert DEFAULT_CATEGORY_WEIGHTS["SYSTEM"] >= max(v for k, v in DEFAULT_CATEGORY_WEIGHTS.items() if k != "SYSTEM")

    def test_validation_is_lowest(self):
        """VALIDATION이 가장 낮은 가중치."""
        assert DEFAULT_CATEGORY_WEIGHTS["VALIDATION"] <= min(
            v for k, v in DEFAULT_CATEGORY_WEIGHTS.items() if k != "VALIDATION"
        )


class TestIntegrationWithErrorCode:
    """ErrorCode 문자열 기반 통합 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        reset_exception_weight_map()
        yield
        reset_exception_weight_map()

    def test_all_known_error_codes_have_valid_weight(self):
        """알려진 모든 ErrorCode 문자열이 유효한 가중치 반환."""
        # 주요 ErrorCode 문자열 목록
        error_codes = [
            "SYSTEM_INTERNAL_ERROR",
            "SYSTEM_DATABASE_ERROR",
            "SYSTEM_DLQ_ERROR",
            "SERVICE_UNAVAILABLE",
            "SERVICE_CIRCUIT_OPEN",
            "SERVICE_TIMEOUT",
            "SERVICE_BAD_GATEWAY",
            "VALIDATION_FIELD_REQUIRED",
            "VALIDATION_FIELD_INVALID",
            "VALIDATION_PARSE_ERROR",
            "AUTH_NOT_AUTHENTICATED",
            "AUTH_TOKEN_INVALID",
            "AUTH_TOKEN_EXPIRED",
            "AUTHZ_PERMISSION_DENIED",
            "AUTHZ_GOVERNANCE_BLOCKED",
            "AUTHZ_ERROR_BUDGET_BLOCKED",
            "RESOURCE_NOT_FOUND",
            "RESOURCE_CONFLICT",
            "RATE_LIMIT_EXCEEDED",
            "RATE_THROTTLED",
            "CONFIG_LOCKED",
            "CONFIG_INVALID",
        ]

        for code in error_codes:
            weight = get_weight_for_error_code(code)
            assert 0.0 <= weight <= 1.0, f"{code}: weight={weight} out of range"

    def test_system_errors_high_weight(self):
        """SYSTEM 에러는 높은 가중치."""
        assert get_weight_for_error_code("SYSTEM_INTERNAL_ERROR") >= 0.8
        assert get_weight_for_error_code("SYSTEM_DATABASE_ERROR") >= 0.8

    def test_validation_errors_low_weight(self):
        """VALIDATION 에러는 낮은 가중치."""
        assert get_weight_for_error_code("VALIDATION_FIELD_REQUIRED") <= 0.2
        assert get_weight_for_error_code("VALIDATION_PARSE_ERROR") <= 0.2

"""
CanaryFeatureFlag 모듈 단위 테스트.

테스트 대상:
    - CanaryFlagConfig: Feature Flag 설정 데이터클래스
    - CanaryDecision: Canary 결정 결과 데이터클래스
    - CanaryFeatureFlag: Request-level Canary 판단 로직
    - compute_stable_hash: 안정적 해시 함수
    - RequestContextExtractor: 요청 컨텍스트 추출

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md (Step 6)
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import Mock

import pytest

from selfhealing.services.canary.feature_flag import (
    CanaryFeatureFlag,
    CanaryFlagConfig,
    CanarySelectionStrategy,
    RequestContextExtractor,
    compute_stable_hash,
    reset_canary_feature_flag,
)
from selfhealing.utils.time import utc_now

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_singletons():
    """테스트 전후 싱글톤 초기화."""
    reset_canary_feature_flag()
    yield
    reset_canary_feature_flag()


@pytest.fixture
def mock_request() -> Mock:
    """Mock Django HttpRequest."""
    request = Mock()
    request.user = Mock()
    request.user.id = 12345
    request.user.username = "testuser"
    request.session = Mock()
    request.session.session_key = "session-abc123"
    request.META = {
        "REMOTE_ADDR": "192.168.1.100",
        "HTTP_X_FORWARDED_FOR": "10.0.0.1, 10.0.0.2",
    }
    return request


@pytest.fixture
def sample_flag_config() -> CanaryFlagConfig:
    """샘플 Feature Flag 설정."""
    return CanaryFlagConfig(
        config_type="circuit_breaker",
        enabled=True,
        percentage=20.0,
        strategy=CanarySelectionStrategy.USER_ID_HASH,
        baseline_config={"failure_threshold": 5},
        canary_config={"failure_threshold": 3},
    )


@pytest.fixture
def feature_flag() -> CanaryFeatureFlag:
    """CanaryFeatureFlag 인스턴스."""
    return CanaryFeatureFlag()


# =============================================================================
# compute_stable_hash Tests
# =============================================================================


class TestComputeStableHash:
    """compute_stable_hash 함수 테스트."""

    def test_same_input_same_output(self):
        """동일 입력에 대해 동일 출력 확인."""
        input_str = "test:user123"

        hash1 = compute_stable_hash(input_str)
        hash2 = compute_stable_hash(input_str)
        hash3 = compute_stable_hash(input_str)

        assert hash1 == hash2 == hash3

    def test_output_range(self):
        """출력 범위 0-99 확인."""
        test_inputs = [
            "user1",
            "user2",
            "circuit_breaker:12345",
            "dlq:67890",
            "a" * 1000,
        ]

        for input_str in test_inputs:
            result = compute_stable_hash(input_str)
            assert 0 <= result <= 99

    def test_different_inputs_different_outputs(self):
        """다른 입력에 대해 다른 출력 (대부분의 경우)."""
        inputs = [f"user{i}" for i in range(100)]
        outputs = [compute_stable_hash(s) for s in inputs]

        # 최소한 일부는 달라야 함 (충돌 가능성 있음)
        unique_outputs = set(outputs)
        assert len(unique_outputs) > 50  # 100개 중 50개 이상 고유해야 함


# =============================================================================
# RequestContextExtractor Tests
# =============================================================================


class TestRequestContextExtractor:
    """RequestContextExtractor 테스트."""

    def test_get_user_id_from_id(self, mock_request: Mock):
        """사용자 ID 추출 테스트."""
        result = RequestContextExtractor.get_user_id(mock_request)
        assert result == "12345"

    def test_get_user_id_from_username(self):
        """사용자명으로 ID 추출 테스트."""
        request = Mock()
        request.user = Mock()
        request.user.id = None
        request.user.username = "fallback_user"

        result = RequestContextExtractor.get_user_id(request)
        assert result == "fallback_user"

    def test_get_user_id_none(self):
        """사용자 정보 없을 때 None 반환."""
        request = Mock()
        request.user = None

        result = RequestContextExtractor.get_user_id(request)
        assert result is None

    def test_get_client_ip_from_x_forwarded_for(self, mock_request: Mock):
        """X-Forwarded-For 헤더에서 IP 추출 테스트."""
        result = RequestContextExtractor.get_client_ip(mock_request)
        assert result == "10.0.0.1"  # 첫 번째 IP

    def test_get_client_ip_from_remote_addr(self):
        """REMOTE_ADDR에서 IP 추출 테스트."""
        request = Mock()
        request.META = {"REMOTE_ADDR": "192.168.1.50"}

        result = RequestContextExtractor.get_client_ip(request)
        assert result == "192.168.1.50"

    def test_get_header(self):
        """헤더 추출 테스트."""
        request = Mock()
        request.META = {
            "HTTP_X_CANARY_CONFIG": "true",
            "HTTP_AUTHORIZATION": "Bearer token",
        }

        result = RequestContextExtractor.get_header(request, "X-Canary-Config")
        assert result == "true"

    def test_get_session_id(self, mock_request: Mock):
        """세션 ID 추출 테스트."""
        result = RequestContextExtractor.get_session_id(mock_request)
        assert result == "session-abc123"


# =============================================================================
# CanaryFlagConfig Tests
# =============================================================================


class TestCanaryFlagConfig:
    """CanaryFlagConfig 데이터클래스 테스트."""

    def test_create_with_defaults(self):
        """기본값으로 생성 테스트."""
        config = CanaryFlagConfig(config_type="circuit_breaker")

        assert config.enabled is True
        assert config.percentage == 10.0
        assert config.strategy == CanarySelectionStrategy.USER_ID_HASH
        assert config.whitelist_user_ids == set()
        assert config.canary_header == "X-Canary-Config"

    def test_create_with_custom_values(self, sample_flag_config: CanaryFlagConfig):
        """커스텀 값으로 생성 테스트."""
        assert sample_flag_config.config_type == "circuit_breaker"
        assert sample_flag_config.percentage == 20.0
        assert sample_flag_config.baseline_config == {"failure_threshold": 5}
        assert sample_flag_config.canary_config == {"failure_threshold": 3}

    def test_is_expired_false(self, sample_flag_config: CanaryFlagConfig):
        """만료되지 않음 테스트."""
        assert sample_flag_config.is_expired() is False

    def test_is_expired_true(self):
        """만료됨 테스트."""
        expired_config = CanaryFlagConfig(
            config_type="test",
            expires_at=utc_now() - timedelta(hours=1),
        )
        assert expired_config.is_expired() is True

    def test_to_dict(self, sample_flag_config: CanaryFlagConfig):
        """딕셔너리 변환 테스트."""
        result = sample_flag_config.to_dict()

        assert result["config_type"] == "circuit_breaker"
        assert result["percentage"] == 20.0
        assert result["strategy"] == "user_id_hash"
        assert result["baseline_config"] == {"failure_threshold": 5}
        assert result["canary_config"] == {"failure_threshold": 3}

    def test_from_dict(self):
        """딕셔너리에서 생성 테스트."""
        data = {
            "config_type": "dlq",
            "enabled": False,
            "percentage": 50.0,
            "strategy": "random",
            "baseline_config": {"max_retries": 3},
            "canary_config": {"max_retries": 5},
            "created_at": "2024-01-15T10:00:00",
        }

        config = CanaryFlagConfig.from_dict(data)

        assert config.config_type == "dlq"
        assert config.enabled is False
        assert config.percentage == 50.0
        assert config.strategy == CanarySelectionStrategy.RANDOM


# =============================================================================
# CanaryFeatureFlag Tests
# =============================================================================


class TestCanaryFeatureFlag:
    """CanaryFeatureFlag 테스트."""

    def test_register_and_get_flag(
        self,
        feature_flag: CanaryFeatureFlag,
        sample_flag_config: CanaryFlagConfig,
    ):
        """Flag 등록 및 조회 테스트."""
        feature_flag.register_flag(sample_flag_config)

        retrieved = feature_flag.get_flag("circuit_breaker")

        assert retrieved is not None
        assert retrieved.config_type == "circuit_breaker"

    def test_unregister_flag(
        self,
        feature_flag: CanaryFeatureFlag,
        sample_flag_config: CanaryFlagConfig,
    ):
        """Flag 등록 해제 테스트."""
        feature_flag.register_flag(sample_flag_config)
        assert feature_flag.get_flag("circuit_breaker") is not None

        result = feature_flag.unregister_flag("circuit_breaker")

        assert result is True
        assert feature_flag.get_flag("circuit_breaker") is None

    def test_unregister_nonexistent_flag(self, feature_flag: CanaryFeatureFlag):
        """존재하지 않는 Flag 해제 시도 테스트."""
        result = feature_flag.unregister_flag("nonexistent")
        assert result is False

    def test_list_flags(self, feature_flag: CanaryFeatureFlag):
        """Flag 목록 조회 테스트."""
        flag1 = CanaryFlagConfig(config_type="type1")
        flag2 = CanaryFlagConfig(config_type="type2")

        feature_flag.register_flag(flag1)
        feature_flag.register_flag(flag2)

        flags = feature_flag.list_flags()

        assert len(flags) == 2
        config_types = {f.config_type for f in flags}
        assert config_types == {"type1", "type2"}

    def test_evaluate_no_flag_registered(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """등록되지 않은 Flag 평가 테스트."""
        decision = feature_flag.evaluate(mock_request, "unregistered")

        assert decision.use_canary is False
        assert decision.reason == "no_flag_registered"

    def test_evaluate_disabled_flag(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """비활성화된 Flag 평가 테스트."""
        disabled_flag = CanaryFlagConfig(
            config_type="disabled_type",
            enabled=False,
            baseline_config={"key": "baseline_value"},
        )
        feature_flag.register_flag(disabled_flag)

        decision = feature_flag.evaluate(mock_request, "disabled_type")

        assert decision.use_canary is False
        assert decision.reason == "flag_disabled"
        assert decision.effective_config == {"key": "baseline_value"}

    def test_evaluate_expired_flag(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """만료된 Flag 평가 테스트."""
        expired_flag = CanaryFlagConfig(
            config_type="expired_type",
            expires_at=utc_now() - timedelta(hours=1),
            baseline_config={"key": "baseline_value"},
        )
        feature_flag.register_flag(expired_flag)

        decision = feature_flag.evaluate(mock_request, "expired_type")

        assert decision.use_canary is False
        assert decision.reason == "flag_expired"

    def test_evaluate_header_based_true(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """헤더 기반 전략 - Canary 헤더 있음 테스트."""
        request = Mock()
        request.META = {"HTTP_X_CANARY_CONFIG": "true"}

        flag = CanaryFlagConfig(
            config_type="header_test",
            strategy=CanarySelectionStrategy.HEADER_BASED,
            canary_config={"enabled": True},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(request, "header_test")

        assert decision.use_canary is True
        assert decision.reason == "header_present"
        assert decision.strategy_used == "header_based"

    def test_evaluate_header_based_false(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """헤더 기반 전략 - Canary 헤더 없음 테스트."""
        request = Mock()
        request.META = {}

        flag = CanaryFlagConfig(
            config_type="header_test",
            strategy=CanarySelectionStrategy.HEADER_BASED,
            baseline_config={"enabled": False},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(request, "header_test")

        assert decision.use_canary is False
        assert decision.reason == "header_absent"

    def test_evaluate_whitelist_user_in_list(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """화이트리스트 전략 - 사용자 포함 테스트."""
        flag = CanaryFlagConfig(
            config_type="whitelist_test",
            strategy=CanarySelectionStrategy.WHITELIST,
            whitelist_user_ids={"12345", "67890"},
            canary_config={"feature": True},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(mock_request, "whitelist_test")

        assert decision.use_canary is True
        assert decision.reason == "user_in_whitelist"

    def test_evaluate_whitelist_user_not_in_list(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """화이트리스트 전략 - 사용자 미포함 테스트."""
        request = Mock()
        request.user = Mock()
        request.user.id = 99999
        request.META = {"REMOTE_ADDR": "192.168.1.1"}

        flag = CanaryFlagConfig(
            config_type="whitelist_test",
            strategy=CanarySelectionStrategy.WHITELIST,
            whitelist_user_ids={"12345", "67890"},
            whitelist_ips=set(),
            baseline_config={"feature": False},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(request, "whitelist_test")

        assert decision.use_canary is False
        assert decision.reason == "not_in_whitelist"

    def test_evaluate_whitelist_ip_in_list(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """화이트리스트 전략 - IP 포함 테스트."""
        request = Mock()
        request.user = None
        request.META = {"REMOTE_ADDR": "10.0.0.5"}

        flag = CanaryFlagConfig(
            config_type="whitelist_ip_test",
            strategy=CanarySelectionStrategy.WHITELIST,
            whitelist_user_ids=set(),
            whitelist_ips={"10.0.0.5", "10.0.0.10"},
            canary_config={"ip_feature": True},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(request, "whitelist_ip_test")

        assert decision.use_canary is True
        assert decision.reason == "ip_in_whitelist"

    def test_evaluate_user_id_hash_consistency(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """사용자 ID 해시 전략 - 일관성 테스트."""
        flag = CanaryFlagConfig(
            config_type="hash_test",
            strategy=CanarySelectionStrategy.USER_ID_HASH,
            percentage=50.0,  # 50% 비율
        )
        feature_flag.register_flag(flag)

        # 동일 사용자에 대해 여러 번 평가해도 동일 결과
        decisions = [feature_flag.evaluate(mock_request, "hash_test") for _ in range(10)]

        first_result = decisions[0].use_canary
        assert all(d.use_canary == first_result for d in decisions)

    def test_evaluate_user_id_hash_no_identifier(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """사용자 ID 해시 전략 - 식별자 없음 테스트."""
        request = Mock()
        request.user = None
        request.session = None
        request.META = {}

        flag = CanaryFlagConfig(
            config_type="no_id_test",
            strategy=CanarySelectionStrategy.USER_ID_HASH,
            baseline_config={"default": True},
        )
        feature_flag.register_flag(flag)

        decision = feature_flag.evaluate(request, "no_id_test")

        assert decision.use_canary is False
        assert decision.reason == "no_identifier"

    def test_evaluate_random_strategy(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """무작위 전략 테스트."""
        flag = CanaryFlagConfig(
            config_type="random_test",
            strategy=CanarySelectionStrategy.RANDOM,
            percentage=50.0,
        )
        feature_flag.register_flag(flag)

        # 무작위이므로 결과가 다를 수 있음
        decisions = [feature_flag.evaluate(mock_request, "random_test") for _ in range(100)]

        # 50% 비율이면 대략 반반이어야 함 (완전히 정확하진 않음)
        canary_count = sum(1 for d in decisions if d.use_canary)
        assert 20 <= canary_count <= 80  # 넓은 범위로 허용

    def test_should_use_canary_config(
        self,
        feature_flag: CanaryFeatureFlag,
        sample_flag_config: CanaryFlagConfig,
        mock_request: Mock,
    ):
        """should_use_canary_config 편의 메서드 테스트."""
        feature_flag.register_flag(sample_flag_config)

        # evaluate()와 동일한 결과
        full_decision = feature_flag.evaluate(mock_request, "circuit_breaker")
        simple_result = feature_flag.should_use_canary_config(mock_request, "circuit_breaker")

        assert simple_result == full_decision.use_canary

    def test_get_effective_config_canary(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """get_effective_config - Canary 설정 반환 테스트."""
        # 100% Canary 비율로 설정
        flag = CanaryFlagConfig(
            config_type="effective_test",
            strategy=CanarySelectionStrategy.HEADER_BASED,
            baseline_config={"value": "baseline"},
            canary_config={"value": "canary"},
        )
        feature_flag.register_flag(flag)

        request = Mock()
        request.META = {"HTTP_X_CANARY_CONFIG": "true"}

        result = feature_flag.get_effective_config(request, "effective_test")

        assert result == {"value": "canary"}

    def test_get_effective_config_baseline(
        self,
        feature_flag: CanaryFeatureFlag,
    ):
        """get_effective_config - Baseline 설정 반환 테스트."""
        flag = CanaryFlagConfig(
            config_type="effective_test",
            strategy=CanarySelectionStrategy.HEADER_BASED,
            baseline_config={"value": "baseline"},
            canary_config={"value": "canary"},
        )
        feature_flag.register_flag(flag)

        request = Mock()
        request.META = {}  # 헤더 없음

        result = feature_flag.get_effective_config(request, "effective_test")

        assert result == {"value": "baseline"}

    def test_get_effective_config_no_flag(
        self,
        feature_flag: CanaryFeatureFlag,
        mock_request: Mock,
    ):
        """get_effective_config - Flag 없을 때 테스트."""
        result = feature_flag.get_effective_config(
            mock_request,
            "nonexistent",
            baseline_config={"fallback": True},
        )

        assert result == {"fallback": True}

    def test_update_percentage(
        self,
        feature_flag: CanaryFeatureFlag,
        sample_flag_config: CanaryFlagConfig,
    ):
        """비율 업데이트 테스트."""
        feature_flag.register_flag(sample_flag_config)

        result = feature_flag.update_percentage("circuit_breaker", 50.0)

        assert result is True
        updated_flag = feature_flag.get_flag("circuit_breaker")
        assert updated_flag.percentage == 50.0

    def test_update_percentage_nonexistent(self, feature_flag: CanaryFeatureFlag):
        """존재하지 않는 Flag 비율 업데이트 테스트."""
        result = feature_flag.update_percentage("nonexistent", 50.0)
        assert result is False

    def test_update_percentage_invalid_value(
        self,
        feature_flag: CanaryFeatureFlag,
        sample_flag_config: CanaryFlagConfig,
    ):
        """유효하지 않은 비율 업데이트 테스트."""
        feature_flag.register_flag(sample_flag_config)

        with pytest.raises(ValueError) as exc_info:
            feature_flag.update_percentage("circuit_breaker", 150.0)

        assert "between 0 and 100" in str(exc_info.value)


# =============================================================================
# CanaryConfigMiddleware Tests
# =============================================================================


class TestCanaryConfigMiddleware:
    """CanaryConfigMiddleware 테스트."""

    def test_middleware_adds_canary_decisions(
        self,
        sample_flag_config: CanaryFlagConfig,
    ):
        """미들웨어가 canary_decisions 속성 추가하는지 테스트."""
        from selfhealing.services.canary.feature_flag import CanaryConfigMiddleware

        # Mock response 함수
        def get_response(request):
            response = Mock()
            response.__setitem__ = Mock()
            return response

        middleware = CanaryConfigMiddleware(get_response)

        # Flag 등록
        middleware.feature_flag.register_flag(sample_flag_config)

        # Mock request
        request = Mock()
        request.user = Mock()
        request.user.id = 12345
        request.session = Mock()
        request.session.session_key = "session-123"
        request.META = {"REMOTE_ADDR": "192.168.1.1"}
        request.canary_decisions = None

        # 미들웨어 실행
        response = middleware(request)

        # canary_decisions 추가됨
        assert hasattr(request, "canary_decisions")
        assert "circuit_breaker" in request.canary_decisions

    def test_middleware_response_headers_in_debug_mode(
        self,
        sample_flag_config: CanaryFlagConfig,
        monkeypatch,
    ):
        """디버그 모드에서 응답 헤더 추가 테스트."""
        from selfhealing.services.canary.feature_flag import CanaryConfigMiddleware

        # 디버그 모드 활성화
        monkeypatch.setenv("CANARY_DEBUG", "true")

        response_headers = {}

        def get_response(request):
            response = Mock()
            response.__setitem__ = lambda self, k, v: response_headers.__setitem__(k, v)
            return response

        middleware = CanaryConfigMiddleware(get_response)
        middleware.feature_flag.register_flag(sample_flag_config)

        request = Mock()
        request.user = Mock()
        request.user.id = 12345
        request.session = None
        request.META = {"REMOTE_ADDR": "192.168.1.1"}
        request.canary_decisions = None

        middleware(request)

        # 디버그 헤더 추가됨
        assert "X-Canary-circuit_breaker" in response_headers

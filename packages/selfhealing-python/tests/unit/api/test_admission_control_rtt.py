"""
단위 테스트 — AdmissionControlMiddleware의 RTT 샘플 수집 및 metadata 전달.

테스트 항목:
- HTTP 2xx 응답 시 RTT 샘플 수집 (add_sample 호출)
- HTTP 4xx/5xx 응답 시 RTT 미수집
- 최소 임계치 미만 응답 시 RTT 미수집
- 확률 샘플링 비율 적용
- TrafficGate.should_allow()에 metadata["tier_id"] 전달
- RTT 수집 실패 시 요청 처리 무영향 (Fail-Open)
"""

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from unittest.mock import MagicMock, call, patch

import pytest

from selfhealing.api.django.admission_control import (
    AdmissionControlMiddleware,
    _RTT_MIN_SAMPLE_MS,
    _RTT_SAMPLE_RATE,
)
from selfhealing.scaling.deadline_context import _request_deadline
from selfhealing.services.throttle.gradient import (
    get_gradient_calculator,
    reset_gradient_calculators,
)


@pytest.fixture(autouse=True)
def _reset_state():
    """각 테스트 전후로 Calculator 레지스트리와 deadline ContextVar를 초기화."""
    reset_gradient_calculators()
    _request_deadline.set(None)
    yield
    reset_gradient_calculators()
    _request_deadline.set(None)


class TestAdmissionControlRttSamplingContract:
    """RTT 샘플 수집 상수 계약값 검증."""

    def test_rtt_min_sample_ms_default(self):
        """RTT 최소 임계치 기본값은 5ms이다."""
        assert _RTT_MIN_SAMPLE_MS == 5.0

    def test_rtt_sample_rate_default(self):
        """RTT 확률 샘플링 비율 기본값은 0.1(10%)이다."""
        assert _RTT_SAMPLE_RATE == 0.1


class TestAdmissionControlRttSamplingBehavior:
    """AdmissionControlMiddleware RTT 샘플 수집 동작 검증."""

    @pytest.fixture
    def mock_tier_result(self):
        result = MagicMock()
        result.tier_id = "standard"
        return result

    @pytest.fixture
    def mock_tier_def(self):
        td = MagicMock()
        td.priority = 50
        return td

    @pytest.fixture
    def mock_registry(self, mock_tier_result, mock_tier_def):
        registry = MagicMock()
        registry.resolve_tier_with_fallback.return_value = mock_tier_result
        registry.get_tier.return_value = mock_tier_def
        return registry

    @pytest.fixture
    def mock_traffic_gate(self):
        gate = MagicMock()
        decision = MagicMock()
        decision.allowed = True
        decision.bulkhead_acquired = False
        decision.bulkhead_name = None
        gate.should_allow.return_value = decision
        return gate

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.enabled = True
        settings.get_tier_max_concurrent.side_effect = lambda tid: {
            "critical": 100,
            "standard": 50,
            "non_essential": 20,
        }.get(tid, 50)
        return settings

    def _create_middleware(self, mock_settings, mock_registry, mock_traffic_gate, response_status=200):
        """패치된 AdmissionControlMiddleware 생성."""
        mock_response = MagicMock()
        mock_response.status_code = response_status
        get_response = MagicMock(return_value=mock_response)

        with (
            patch("selfhealing.api.django.admission_control." "AdmissionControlMiddleware._init_dependencies"),
            patch(
                "selfhealing.settings.admission_control." "get_admission_control_settings",
                return_value=mock_settings,
            ),
        ):
            middleware = AdmissionControlMiddleware(get_response)
            middleware._enabled = True
            middleware._registry = mock_registry
            middleware._traffic_gate = mock_traffic_gate
            middleware._settings = mock_settings

        return middleware, get_response, mock_response

    def _create_request(self):
        """Django request Mock 생성."""
        request = MagicMock()
        request.path = "/api/test"
        request.method = "GET"
        request.META = {"REMOTE_ADDR": "127.0.0.1"}
        request.user.is_authenticated = False
        return request

    def test_metadata_tier_id_passed_to_traffic_gate(self, mock_settings, mock_registry, mock_traffic_gate):
        """TrafficGate.should_allow()에 metadata={"tier_id": tier_id}가 전달된다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate)
        request = self._create_request()

        middleware(request)

        # should_allow 호출 시 metadata에 tier_id 포함 확인
        mock_traffic_gate.should_allow.assert_called_once()
        call_kwargs = mock_traffic_gate.should_allow.call_args
        assert call_kwargs.kwargs.get("metadata") == {"tier_id": "standard"}

    def test_2xx_response_triggers_rtt_sampling(self, mock_settings, mock_registry, mock_traffic_gate):
        """HTTP 200 OK 응답 시 RTT 샘플 수집이 시도된다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=200)
        request = self._create_request()

        # random.random()이 항상 0을 반환하도록 하여 샘플링 통과
        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0  # 항상 _RTT_SAMPLE_RATE 미만
            with patch("selfhealing.api.django.admission_control.time") as mock_time:
                # perf_counter가 10ms 차이를 만들도록 설정
                mock_time.perf_counter.side_effect = [0.0, 0.010]
                with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                    mock_calc = MagicMock()
                    mock_get_calc.return_value = mock_calc

                    middleware(request)

                    mock_get_calc.assert_called_once_with("admission_control:standard")
                    mock_calc.add_sample.assert_called_once()
                    # elapsed_ms = (0.010 - 0.0) * 1000 = 10.0
                    sampled_ms = mock_calc.add_sample.call_args[0][0]
                    assert sampled_ms == pytest.approx(10.0, abs=1.0)

    def test_4xx_response_not_sampled(self, mock_settings, mock_registry, mock_traffic_gate):
        """HTTP 400 Bad Request 응답 시 RTT 샘플이 수집되지 않는다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=400)
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0
            with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                middleware(request)
                mock_get_calc.assert_not_called()

    def test_5xx_response_not_sampled(self, mock_settings, mock_registry, mock_traffic_gate):
        """HTTP 500 Error 응답 시 RTT 샘플이 수집되지 않는다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=500)
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0
            with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                middleware(request)
                mock_get_calc.assert_not_called()

    def test_below_min_threshold_not_sampled(self, mock_settings, mock_registry, mock_traffic_gate):
        """최소 임계치(_RTT_MIN_SAMPLE_MS) 미만 응답은 수집되지 않는다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=200)
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0
            with patch("selfhealing.api.django.admission_control.time") as mock_time:
                # 3ms — _RTT_MIN_SAMPLE_MS(5ms) 미만
                mock_time.perf_counter.side_effect = [0.0, 0.003]
                with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                    middleware(request)
                    mock_get_calc.assert_not_called()

    def test_sampling_rate_respected(self, mock_settings, mock_registry, mock_traffic_gate):
        """random.random() >= _RTT_SAMPLE_RATE 이면 수집하지 않는다."""
        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=200)
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            # _RTT_SAMPLE_RATE = 0.1 이므로, 0.5 >= 0.1 → 수집 스킵
            mock_random.random.return_value = 0.5
            with patch("selfhealing.api.django.admission_control.time") as mock_time:
                mock_time.perf_counter.side_effect = [0.0, 0.100]  # 100ms
                with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                    middleware(request)
                    mock_get_calc.assert_not_called()

    def test_tier_separated_calculator_name(self, mock_settings, mock_registry, mock_traffic_gate):
        """Tier별로 다른 Calculator 이름("admission_control:{tier_id}")을 사용한다."""
        # critical tier 설정
        mock_tier_result = MagicMock()
        mock_tier_result.tier_id = "critical"
        mock_registry.resolve_tier_with_fallback.return_value = mock_tier_result
        mock_tier_def = MagicMock()
        mock_tier_def.priority = 10
        mock_registry.get_tier.return_value = mock_tier_def

        middleware, _, _ = self._create_middleware(mock_settings, mock_registry, mock_traffic_gate, response_status=200)
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0
            with patch("selfhealing.api.django.admission_control.time") as mock_time:
                mock_time.perf_counter.side_effect = [0.0, 0.050]  # 50ms
                with patch("selfhealing.services.throttle.gradient.get_gradient_calculator") as mock_get_calc:
                    mock_calc = MagicMock()
                    mock_get_calc.return_value = mock_calc

                    middleware(request)

                    # critical tier용 Calculator 이름 확인
                    mock_get_calc.assert_called_once_with("admission_control:critical")

    def test_rtt_collection_fail_open(self, mock_settings, mock_registry, mock_traffic_gate):
        """RTT 수집 중 예외 발생 시 요청 응답에 영향 없다 (Fail-Open)."""
        middleware, get_response, mock_response = self._create_middleware(
            mock_settings, mock_registry, mock_traffic_gate, response_status=200
        )
        request = self._create_request()

        with patch("selfhealing.api.django.admission_control.random") as mock_random:
            mock_random.random.return_value = 0.0
            with patch("selfhealing.api.django.admission_control.time") as mock_time:
                mock_time.perf_counter.side_effect = [0.0, 0.050]
                with patch(
                    "selfhealing.services.throttle.gradient.get_gradient_calculator",
                    side_effect=RuntimeError("unexpected error"),
                ):
                    # 예외가 발생해도 응답은 정상 반환
                    response = middleware(request)

        assert response == mock_response
        get_response.assert_called_once_with(request)

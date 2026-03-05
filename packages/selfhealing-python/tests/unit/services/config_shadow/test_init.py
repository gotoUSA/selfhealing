"""
Unit tests for Config Shadow __init__.py singleton lifecycle.

검증 항목:
- get_shadow_evaluator_service: 싱글톤 캐싱 동작
- get_shadow_evaluator_service: 리셋 후 새 인스턴스 생성

테스트 대상: selfhealing.services.config_shadow.__init__
"""

import threading
from unittest.mock import patch

import selfhealing.services.config_shadow as config_shadow_module
from selfhealing.services.config_shadow import get_shadow_evaluator_service
from selfhealing.services.config_shadow.service import ShadowEvaluatorService


class TestGetShadowEvaluatorServiceBehavior:
    """get_shadow_evaluator_service 싱글톤 동작 검증."""

    def setup_method(self):
        """각 테스트 전 싱글톤 캐시 초기화."""
        config_shadow_module._service = None

    def teardown_method(self):
        """각 테스트 후 싱글톤 캐시 초기화."""
        config_shadow_module._service = None

    @patch(
        "selfhealing.services.config_shadow.service.ShadowEvaluatorService.__init__",
        return_value=None,
    )
    def test_returns_shadow_evaluator_service_instance(self, mock_init):
        """ShadowEvaluatorService 인스턴스를 반환한다."""
        result = get_shadow_evaluator_service()
        assert isinstance(result, ShadowEvaluatorService)

    @patch(
        "selfhealing.services.config_shadow.service.ShadowEvaluatorService.__init__",
        return_value=None,
    )
    def test_returns_same_instance_on_repeated_calls(self, mock_init):
        """반복 호출 시 동일 인스턴스를 반환한다 (싱글톤)."""
        first = get_shadow_evaluator_service()
        second = get_shadow_evaluator_service()
        assert first is second
        assert mock_init.call_count == 1

    @patch(
        "selfhealing.services.config_shadow.service.ShadowEvaluatorService.__init__",
        return_value=None,
    )
    def test_reset_creates_new_instance(self, mock_init):
        """_service를 None으로 리셋하면 새 인스턴스가 생성된다."""
        first = get_shadow_evaluator_service()
        config_shadow_module._service = None
        second = get_shadow_evaluator_service()
        assert first is not second
        assert mock_init.call_count == 2

    @patch(
        "selfhealing.services.config_shadow.service.ShadowEvaluatorService.__init__",
        return_value=None,
    )
    def test_concurrent_init_creates_single_instance(self, mock_init):
        """동시 초기화 시 Lock으로 인해 단일 인스턴스만 생성된다."""
        results = []

        def call_service():
            results.append(get_shadow_evaluator_service())

        threads = [threading.Thread(target=call_service) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(r is results[0] for r in results)
        assert mock_init.call_count == 1

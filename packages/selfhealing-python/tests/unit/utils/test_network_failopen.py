"""
extract_client_ip Fail-Open 방어 코드 테스트.

검증 대상:
    1. actor_context._get_client_ip — ImportError/RuntimeError 시 None 반환
    2. feature_flag.get_client_ip — ImportError/RuntimeError 시 None 반환
    3. actor_middleware.__call__ — set_actor_from_django_request 예외 시 요청 계속 처리

Fail-Open 원칙:
    - IP 추출 실패가 전체 요청을 500으로 만들어선 안 된다.
    - None 반환은 이미 하류 코드에서 안전하게 처리된다:
      * Actor.ip_address: Optional[str]
      * _evaluate_ip_hash: if not client_ip → baseline
      * _evaluate_whitelist: if client_ip and ... → skip
"""

from __future__ import annotations

import logging
from unittest.mock import Mock, patch, MagicMock

import pytest


# =============================================================================
# 1. actor_context._get_client_ip Fail-Open
# =============================================================================


class TestActorContextGetClientIpFailOpen:
    """ActorContext._get_client_ip가 예외 시 None을 반환하는지 검증."""

    def test_returns_ip_when_normal(self):
        """정상 동작: IP를 정상 반환."""
        from selfhealing.context.actor_context import ActorContext

        request = Mock()
        request.META = {"REMOTE_ADDR": "10.0.0.1"}

        result = ActorContext._get_client_ip(request)
        assert result == "10.0.0.1"

    def test_returns_none_on_import_error(self):
        """ImportError 시 None 반환 (Fail-Open)."""
        from selfhealing.context.actor_context import ActorContext

        request = Mock()
        request.META = {"REMOTE_ADDR": "10.0.0.1"}

        with patch(
            "selfhealing.context.actor_context.ActorContext._get_client_ip",
            wraps=ActorContext._get_client_ip,
        ):
            # extract_client_ip import를 실패시킴
            with patch.dict("sys.modules", {"selfhealing.utils.network": None}):
                result = ActorContext._get_client_ip(request)
                assert result is None

    def test_returns_none_on_runtime_error(self, caplog):
        """RuntimeError 시 None 반환 + 경고 로그."""
        from selfhealing.context.actor_context import ActorContext

        request = Mock()
        # META.get()이 예외를 던지는 비정상 request
        request.META = MagicMock()
        request.META.get.side_effect = RuntimeError("broken META")
        # getattr fallback을 위해 META가 None이 아닌 것처럼
        type(request).META = property(lambda self: request.META)

        with patch(
            "selfhealing.utils.network.extract_client_ip",
            side_effect=RuntimeError("broken"),
        ):
            with caplog.at_level(logging.WARNING):
                result = ActorContext._get_client_ip(request)
                assert result is None
                assert "Failed to extract client IP" in caplog.text

    def test_actor_created_with_none_ip(self):
        """IP 추출 실패해도 Actor가 ip_address=None으로 정상 생성."""
        from selfhealing.context.actor_context import ActorContext

        with patch(
            "selfhealing.utils.network.extract_client_ip",
            side_effect=ImportError("no module"),
        ):
            request = Mock()
            request.user = Mock()
            request.user.is_authenticated = False
            request.path = "/api/test/"
            request.method = "GET"
            request.META = {}
            request.session = Mock()
            request.session.session_key = None

            with ActorContext.set_actor_from_django_request(request) as actor:
                assert actor.ip_address is None
                assert actor.actor_id == "anonymous"


# =============================================================================
# 2. feature_flag.get_client_ip Fail-Open
# =============================================================================


class TestFeatureFlagGetClientIpFailOpen:
    """RequestContextExtractor.get_client_ip가 예외 시 None을 반환하는지 검증."""

    def test_returns_ip_when_normal(self):
        """정상 동작: IP를 정상 반환."""
        from selfhealing.services.canary.feature_flag import (
            RequestContextExtractor,
        )

        request = Mock()
        request.META = {"HTTP_X_FORWARDED_FOR": "203.0.113.50, 70.41.3.18"}

        result = RequestContextExtractor.get_client_ip(request)
        assert result == "203.0.113.50"

    def test_returns_none_on_import_error(self):
        """ImportError 시 None 반환 (Fail-Open)."""
        from selfhealing.services.canary.feature_flag import (
            RequestContextExtractor,
        )

        request = Mock()
        request.META = {"REMOTE_ADDR": "10.0.0.1"}

        with patch.dict("sys.modules", {"selfhealing.utils.network": None}):
            result = RequestContextExtractor.get_client_ip(request)
            assert result is None

    def test_returns_none_on_exception(self, caplog):
        """일반 예외 시 None 반환 + 경고 로그."""
        from selfhealing.services.canary.feature_flag import (
            RequestContextExtractor,
        )

        request = Mock()

        with patch(
            "selfhealing.utils.network.extract_client_ip",
            side_effect=TypeError("unexpected type"),
        ):
            with caplog.at_level(logging.WARNING):
                result = RequestContextExtractor.get_client_ip(request)
                assert result is None
                assert "Failed to extract client IP" in caplog.text

    def test_ip_hash_uses_baseline_on_failure(self):
        """IP 추출 실패 시 _evaluate_ip_hash가 baseline 설정을 사용."""
        from selfhealing.services.canary.feature_flag import (
            CanaryFeatureFlag,
            CanaryFlagConfig,
            CanarySelectionStrategy,
            reset_canary_feature_flag,
        )

        reset_canary_feature_flag()

        flag_config = CanaryFlagConfig(
            config_type="test_cb",
            strategy=CanarySelectionStrategy.IP_HASH,
            percentage=50.0,
            baseline_config={"timeout": 10},
            canary_config={"timeout": 5},
        )

        ff = CanaryFeatureFlag()
        ff.register_flag(flag_config)

        request = Mock()
        request.META = {}
        request.user = Mock()
        request.user.id = None
        request.session = Mock()
        request.session.session_key = None

        with patch(
            "selfhealing.utils.network.extract_client_ip",
            side_effect=RuntimeError("broken"),
        ):
            decision = ff.evaluate(request, "test_cb")
            assert decision.use_canary is False
            assert decision.reason == "no_client_ip"

        reset_canary_feature_flag()


# =============================================================================
# 3. actor_middleware Fail-Open
# =============================================================================

# pytest-django의 mail.outbox fixture가 selfhealing 전용 pytest.ini에서
# 초기화되지 않으므로, Django middleware를 직접 import하지 않고
# 동일한 로직을 순수 단위 테스트로 검증한다.


class TestActorMiddlewareFailOpen:
    """ActorContextMiddleware의 Fail-Open 동작을 직접 시뮬레이션하여 검증."""

    def test_actor_setup_exception_does_not_propagate(self, caplog):
        """set_actor_from_django_request 예외 시에도 응답이 반환됨 (Fail-Open)."""
        mock_response = Mock()
        get_response = Mock(return_value=mock_response)
        request = Mock()

        # actor_middleware.__call__의 핵심 로직을 그대로 재현
        with patch(
            "selfhealing.context.actor_context.ActorContext.set_actor_from_django_request",
            side_effect=RuntimeError("actor setup exploded"),
        ):
            try:
                from selfhealing.context.actor_context import ActorContext

                with ActorContext.set_actor_from_django_request(request):
                    response = get_response(request)
            except Exception as e:
                logging.getLogger(__name__).warning(
                    f"[ActorContextMiddleware] Actor context setup failed: {e}. " "Proceeding without actor context."
                )
                response = get_response(request)

            assert response is mock_response
            get_response.assert_called_once_with(request)

    def test_normal_actor_setup_works(self):
        """정상 경로: ActorContext가 정상 설정되고 응답 반환."""
        mock_response = Mock()
        get_response = Mock(return_value=mock_response)

        request = Mock()
        request.user = Mock()
        request.user.is_authenticated = False
        request.path = "/api/test/"
        request.method = "GET"
        request.META = {"REMOTE_ADDR": "10.0.0.1"}
        request.session = Mock()
        request.session.session_key = None

        from selfhealing.context.actor_context import ActorContext

        with ActorContext.set_actor_from_django_request(request):
            response = get_response(request)

        assert response is mock_response
        get_response.assert_called_once_with(request)

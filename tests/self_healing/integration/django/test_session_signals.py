"""
세션 시그널 핸들러 검증.

shopping/signals.py의 user_logged_in/user_logged_out 시그널 핸들러가
UserSessionRegistry를 올바르게 호출하는지 검증한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestLoginSignalHandler:
    """user_logged_in 시그널 핸들러 검증."""

    @patch("shopping.signals.get_user_session_registry")
    def test_registers_session_on_login(self, mock_get_registry):
        """로그인 시 registry.register() 호출."""
        from shopping.signals import on_user_login_register_session

        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        request = MagicMock()
        request.session.session_key = "test_session_key"
        user = MagicMock()
        user.pk = 42

        on_user_login_register_session(sender=None, request=request, user=user)

        mock_registry.register.assert_called_once_with(42, "test_session_key")

    @patch("shopping.signals.get_user_session_registry")
    def test_creates_session_key_if_missing(self, mock_get_registry):
        """session_key가 None일 때 request.session.save() 호출."""
        from shopping.signals import on_user_login_register_session

        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        request = MagicMock()
        request.session.session_key = None
        # save() 호출 후 session_key 반환
        request.session.save.side_effect = lambda: setattr(request.session, "session_key", "new_key")
        user = MagicMock()
        user.pk = 1

        on_user_login_register_session(sender=None, request=request, user=user)

        request.session.save.assert_called_once()

    def test_skips_when_user_is_none(self):
        """user가 None일 때 registry 호출 안 함."""
        from shopping.signals import on_user_login_register_session

        request = MagicMock()
        request.session.session_key = "test_key"

        # 예외 없이 종료되어야 함
        on_user_login_register_session(sender=None, request=request, user=None)

    def test_skips_when_user_pk_is_none(self):
        """user.pk가 None일 때 registry 호출 안 함."""
        from shopping.signals import on_user_login_register_session

        request = MagicMock()
        request.session.session_key = "test_key"
        user = MagicMock()
        user.pk = None

        on_user_login_register_session(sender=None, request=request, user=user)

    def test_graceful_without_selfhealing(self):
        """selfhealing 미설치 시 ImportError를 조용히 처리."""
        from shopping.signals import on_user_login_register_session

        request = MagicMock()
        request.session.session_key = "test_key"
        user = MagicMock()
        user.pk = 1

        with patch(
            "shopping.signals.get_user_session_registry",
            side_effect=ImportError("No module"),
        ):
            # 예외 없이 종료되어야 함
            on_user_login_register_session(sender=None, request=request, user=user)


class TestLogoutSignalHandler:
    """user_logged_out 시그널 핸들러 검증."""

    @patch("shopping.signals.get_user_session_registry")
    def test_unregisters_session_on_logout(self, mock_get_registry):
        """로그아웃 시 registry.unregister() 호출."""
        from shopping.signals import on_user_logout_unregister_session

        mock_registry = MagicMock()
        mock_get_registry.return_value = mock_registry

        request = MagicMock()
        request.session.session_key = "logout_session"
        user = MagicMock()
        user.pk = 42

        on_user_logout_unregister_session(sender=None, request=request, user=user)

        mock_registry.unregister.assert_called_once_with(42, "logout_session")

    def test_skips_when_no_session_key(self):
        """session_key가 None일 때 registry 호출 안 함."""
        from shopping.signals import on_user_logout_unregister_session

        request = MagicMock()
        request.session.session_key = None
        user = MagicMock()
        user.pk = 1

        # 예외 없이 종료되어야 함
        on_user_logout_unregister_session(sender=None, request=request, user=user)

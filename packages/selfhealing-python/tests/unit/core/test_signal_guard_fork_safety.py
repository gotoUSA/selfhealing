"""Unit tests for signal guard pattern across 5 modules (Section 5.3).

All 5 modules skip signal.signal() registration when is_gunicorn_worker()
returns True. This prevents Gunicorn Master's signal handlers from being
overwritten by application code.

Modules tested:
    - core/shutdown_coordinator.py — GracefulShutdownCoordinator.register_signals()
    - coordination/shutdown_integration.py — _setup_signal_handlers()
    - audit/async_audit_lifecycle.py — register_shutdown_handlers()
    - audit/persistence/disk_buffer.py — _register_signal_handlers()
    - adapters/audit/redis_buffer.py — RedisAuditBuffer._register_shutdown_hooks()

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.3
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestShutdownCoordinatorSignalGuardBehavior:
    """GracefulShutdownCoordinator.register_signals() skips in Gunicorn Worker."""

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=True)
    @patch("signal.signal")
    def test_skips_signal_registration_in_gunicorn_worker(
        self, mock_signal, mock_is_worker
    ):
        """In Gunicorn Worker, signal.signal() must NOT be called."""
        from selfhealing.core.shutdown_coordinator import GracefulShutdownCoordinator

        coordinator = GracefulShutdownCoordinator(request_tracker=MagicMock())
        coordinator.register_signals()

        mock_signal.assert_not_called()

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=False)
    @patch("signal.signal")
    def test_registers_signals_outside_gunicorn(self, mock_signal, mock_is_worker):
        """Outside Gunicorn, signal.signal() IS called."""
        from selfhealing.core.shutdown_coordinator import GracefulShutdownCoordinator

        coordinator = GracefulShutdownCoordinator(request_tracker=MagicMock())
        coordinator.register_signals()

        assert mock_signal.call_count >= 1


class TestShutdownIntegrationSignalGuardBehavior:
    """coordination/shutdown_integration._setup_signal_handlers() guard."""

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=True)
    @patch("signal.signal")
    def test_skips_signal_registration_in_gunicorn_worker(
        self, mock_signal, mock_is_worker
    ):
        """In Gunicorn Worker, signal registration is skipped."""
        from selfhealing.coordination.shutdown_integration import (
            _setup_signal_handlers,
        )

        _setup_signal_handlers()
        mock_signal.assert_not_called()


class TestAsyncAuditLifecycleSignalGuardBehavior:
    """audit/async_audit_lifecycle.register_shutdown_handlers() guard."""

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=True)
    @patch("selfhealing.audit.async_audit_lifecycle._is_test_mode", return_value=False)
    @patch(
        "selfhealing.audit.async_audit_lifecycle._register_signal_handler",
        autospec=True,
    )
    def test_skips_signal_registration_in_gunicorn_worker(
        self, mock_register, mock_test_mode, mock_is_worker
    ):
        """In Gunicorn Worker, signal handlers are NOT registered."""
        from selfhealing.audit import async_audit_lifecycle

        async_audit_lifecycle._shutdown_registered = False

        result = async_audit_lifecycle.register_shutdown_handlers()

        mock_register.assert_not_called()
        assert result is True
        async_audit_lifecycle._shutdown_registered = False

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=False)
    @patch("selfhealing.audit.async_audit_lifecycle._is_test_mode", return_value=False)
    @patch(
        "selfhealing.audit.async_audit_lifecycle._register_signal_handler",
        autospec=True,
    )
    def test_registers_signals_outside_gunicorn(
        self, mock_register, mock_test_mode, mock_is_worker
    ):
        """Outside Gunicorn, signal handlers ARE registered."""
        from selfhealing.audit import async_audit_lifecycle

        async_audit_lifecycle._shutdown_registered = False

        result = async_audit_lifecycle.register_shutdown_handlers()

        assert mock_register.call_count >= 1
        async_audit_lifecycle._shutdown_registered = False


class TestDiskBufferSignalGuardBehavior:
    """audit/persistence/disk_buffer._register_signal_handlers() guard."""

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=True)
    @patch("signal.getsignal")
    def test_skips_signal_registration_in_gunicorn_worker(
        self, mock_getsignal, mock_is_worker
    ):
        """In Gunicorn Worker, returns early before getsignal."""
        from selfhealing.audit.persistence.disk_buffer import _register_signal_handlers

        _register_signal_handlers()

        # getsignal should NOT be called because we returned early
        mock_getsignal.assert_not_called()


class TestRedisBufferSignalGuardBehavior:
    """adapters/audit/redis_buffer.RedisAuditBuffer._register_shutdown_hooks() guard."""

    @patch("selfhealing.core.process_utils.is_gunicorn_worker", return_value=True)
    @patch("signal.signal")
    def test_skips_signal_registration_in_gunicorn_worker(
        self, mock_signal, mock_is_worker
    ):
        """In Gunicorn Worker, signal.signal() is NOT called but atexit IS."""
        from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer

        buffer = RedisAuditBuffer.__new__(RedisAuditBuffer)
        buffer._shutdown_registered = False
        buffer._graceful_shutdown = MagicMock()
        buffer._signal_handler = MagicMock()

        with patch("atexit.register") as mock_atexit:
            buffer._register_shutdown_hooks()

        mock_atexit.assert_called_once()
        mock_signal.assert_not_called()
        assert buffer._shutdown_registered is True

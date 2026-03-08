"""Unit tests for PrometheusTextMetricsView and IPC __init__ sidecar cleanup.

PrometheusTextMetricsView tests require Django settings (DRF dependency).
IPC __init__ cleanup tests are pure unit tests.

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.4, §5.7
"""

from __future__ import annotations

import pytest


class TestPrometheusTextMetricsViewContract:
    """Contract: view configuration per design spec."""

    @pytest.fixture(autouse=True)
    def _check_django(self):
        """Skip if Django settings not configured."""
        try:
            import django.conf

            django.conf.settings.REST_FRAMEWORK  # noqa: B018
        except Exception:
            pytest.skip("Django settings not configured")

    def test_permission_classes_allow_any(self):
        """Prometheus scraping endpoint requires no authentication."""
        from rest_framework.permissions import AllowAny

        from selfhealing.api.django.views.health import PrometheusTextMetricsView

        assert AllowAny in PrometheusTextMetricsView.permission_classes

    def test_authentication_classes_empty(self):
        """No authentication classes — open for ServiceMonitor scraping."""
        from selfhealing.api.django.views.health import PrometheusTextMetricsView

        assert PrometheusTextMetricsView.authentication_classes == []


class TestIPCInitSidecarCleanupContract:
    """Contract: IPC __init__ no longer exports sidecar-only symbols."""

    def test_removed_sidecar_exports(self):
        """Sidecar-only symbols must NOT be in __all__."""
        from selfhealing.adapters.ipc import __all__ as ipc_all

        sidecar_only = [
            "UDSServer",
            "UDSClient",
            "FailOpenUDSClient",
            "SidecarGRPCServer",
            "SidecarAuthenticator",
            "EventStreamProxy",
            "SidecarIPCProbe",
            "sidecar_metrics",
            "record_ipc_request",
        ]
        for name in sidecar_only:
            assert name not in ipc_all, f"{name} should have been removed"

    def test_retained_library_mode_exports(self):
        """Library-mode symbols must still be in __all__."""
        from selfhealing.adapters.ipc import __all__ as ipc_all

        library_mode = [
            "IPCStateCache",
            "CBStateCache",
            "CBStateSnapshot",
            "get_cb_state_snapshot",
            "reset_cb_state_snapshot",
            "RequestHandler",
            "IPCError",
        ]
        for name in library_mode:
            assert name in ipc_all, f"{name} should be retained"

    def test_reset_cb_state_snapshot_is_importable(self):
        """reset_cb_state_snapshot must be importable (used in post_fork)."""
        from selfhealing.adapters.ipc import reset_cb_state_snapshot

        assert callable(reset_cb_state_snapshot)

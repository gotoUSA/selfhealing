"""Unit tests for core/process_utils.py — Gunicorn Worker detection.

Tests the is_gunicorn_worker() function that gates signal handler
registration and background thread lifecycle in fork-safe mode.

Reference:
    docs/self_healing/middleware_system/316_GUNICORN_PRELOAD_OPTIMIZATION.md §5.2–5.3
"""

from __future__ import annotations

from unittest.mock import patch

from selfhealing.core.process_utils import is_gunicorn_worker


class TestIsGunicornWorkerContract:
    """Contract: detection relies on GUNICORN_WORKER env var set to '1'."""

    def test_returns_true_when_gunicorn_worker_env_is_one(self):
        """GUNICORN_WORKER='1' → True (set by post_worker_init hook)."""
        with patch.dict("os.environ", {"GUNICORN_WORKER": "1"}):
            assert is_gunicorn_worker() is True

    def test_returns_false_when_gunicorn_worker_env_is_absent(self):
        """No GUNICORN_WORKER env → False (default process)."""
        with patch.dict("os.environ", {}, clear=True):
            assert is_gunicorn_worker() is False

    def test_returns_false_when_gunicorn_worker_env_is_zero(self):
        """GUNICORN_WORKER='0' → False (not the contract value)."""
        with patch.dict("os.environ", {"GUNICORN_WORKER": "0"}):
            assert is_gunicorn_worker() is False

    def test_returns_false_when_gunicorn_worker_env_is_true_string(self):
        """GUNICORN_WORKER='true' → False (only '1' is accepted)."""
        with patch.dict("os.environ", {"GUNICORN_WORKER": "true"}):
            assert is_gunicorn_worker() is False

    def test_returns_false_when_gunicorn_worker_env_is_empty(self):
        """GUNICORN_WORKER='' → False."""
        with patch.dict("os.environ", {"GUNICORN_WORKER": ""}):
            assert is_gunicorn_worker() is False


class TestIsGunicornWorkerBehavior:
    """Behavior: idempotent, no side effects."""

    def test_idempotent_returns_same_result_on_repeated_calls(self):
        """Same env → same result for N calls."""
        with patch.dict("os.environ", {"GUNICORN_WORKER": "1"}):
            results = [is_gunicorn_worker() for _ in range(5)]
            assert all(r is True for r in results)

    def test_responds_to_env_change_dynamically(self):
        """Result changes when env var changes between calls."""
        with patch.dict("os.environ", {}, clear=True):
            assert is_gunicorn_worker() is False

        with patch.dict("os.environ", {"GUNICORN_WORKER": "1"}):
            assert is_gunicorn_worker() is True

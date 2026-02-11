"""
_capture_forensic_context() 동작 검증.

selfhealing.services.forensic_context 모듈은 아직 미구현이므로,
ImportError 발생 시 graceful하게 None을 반환하는지 확인한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.adapters.celery.signal_hooks import _capture_forensic_context


class TestCaptureForensicContextBehavior:
    """_capture_forensic_context() 동작 검증."""

    def test_returns_none_when_module_not_found(self):
        """forensic_context 모듈 미존재 시 None 반환 (ImportError graceful)."""
        result = _capture_forensic_context(
            task_name="test_task",
            task_id="abc-123",
            exception=ValueError("test"),
            args=(),
            kwargs={},
            einfo=None,
        )
        assert result is None

    def test_returns_context_when_module_exists(self):
        """forensic_context 모듈 존재 시 캡처 결과 반환."""
        mock_context = {"task_id": "abc-123", "captured": True}

        with patch(
            "selfhealing.adapters.celery.signal_hooks._capture_forensic_context",
            wraps=_capture_forensic_context,
        ):
            with patch.dict(
                "sys.modules",
                {
                    "selfhealing.services.forensic_context": MagicMock(
                        capture_forensic_context=MagicMock(return_value=mock_context)
                    )
                },
            ):
                result = _capture_forensic_context(
                    task_name="test_task",
                    task_id="abc-123",
                    exception=ValueError("test"),
                    args=(),
                    kwargs={},
                    einfo=None,
                )
                assert result == mock_context

    def test_returns_none_on_unexpected_exception(self):
        """캡처 중 예외 발생 시 None 반환."""
        with patch.dict(
            "sys.modules",
            {
                "selfhealing.services.forensic_context": MagicMock(
                    capture_forensic_context=MagicMock(side_effect=RuntimeError("unexpected"))
                )
            },
        ):
            result = _capture_forensic_context(
                task_name="test_task",
                task_id="abc-123",
                exception=ValueError("test"),
                args=(),
                kwargs={},
                einfo=None,
            )
            assert result is None

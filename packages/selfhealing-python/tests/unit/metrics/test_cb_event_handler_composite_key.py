"""
CircuitBreakerEventHandler Composite Key 파싱 단위 테스트.

테스트 대상: metrics/event_handlers.py CircuitBreakerEventHandler.on_state_changed()
- Composite Key(service::cell_id) 분리하여 메트릭 Label에 반영
- 레거시 키(cell_id 없음) 호환
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.services.cell_topology.cb_namespace import (
    make_cell_scoped_cb_name,
)


class TestCircuitBreakerEventHandlerCompositeKeyBehavior:
    """on_state_changed의 Composite Key 파싱 동작 검증."""

    def _call_on_state_changed(self, service: str, from_state: str, to_state: str):
        """on_state_changed 호출 + mock metrics 반환."""
        mock_metrics = MagicMock()
        mock_metrics.circuit_breaker_state = MagicMock()
        mock_metrics.circuit_breaker_transitions = MagicMock()

        with patch(
            "selfhealing.metrics.event_handlers._get_metrics",
            return_value=mock_metrics,
        ):
            from selfhealing.metrics.event_handlers import (
                CircuitBreakerEventHandler,
            )

            CircuitBreakerEventHandler.on_state_changed(
                service=service,
                from_state=from_state,
                to_state=to_state,
            )
        return mock_metrics

    def test_composite_key_splits_service_and_cell_id(self):
        """Composite Key가 service와 cell_id로 분리되어 labels에 전달된다."""
        composite = make_cell_scoped_cb_name("payment_api", "cell-3")
        mock_metrics = self._call_on_state_changed(composite, "closed", "open")

        mock_metrics.circuit_breaker_state.labels.assert_called_once_with(
            service_name="payment_api",
            cell_id="cell-3",
        )

    def test_legacy_key_passes_empty_cell_id(self):
        """레거시 키는 cell_id=''로 전달된다."""
        mock_metrics = self._call_on_state_changed("legacy_svc", "closed", "open")

        mock_metrics.circuit_breaker_state.labels.assert_called_once_with(
            service_name="legacy_svc",
            cell_id="",
        )

    def test_transition_counter_includes_cell_id(self):
        """전환 카운터에도 cell_id가 포함된다."""
        composite = make_cell_scoped_cb_name("order_api", "cell-7")
        mock_metrics = self._call_on_state_changed(composite, "open", "closed")

        mock_metrics.circuit_breaker_transitions.labels.assert_called_once_with(
            service_name="order_api",
            cell_id="cell-7",
            from_state="open",
            to_state="closed",
        )

    def test_no_error_when_metrics_unavailable(self):
        """메트릭이 없을 때도 예외 없이 동작한다."""
        with patch(
            "selfhealing.metrics.event_handlers._get_metrics",
            return_value=None,
        ):
            from selfhealing.metrics.event_handlers import (
                CircuitBreakerEventHandler,
            )

            # 예외 없이 반환되어야 함
            CircuitBreakerEventHandler.on_state_changed(
                service="svc::cell-1",
                from_state="closed",
                to_state="open",
            )

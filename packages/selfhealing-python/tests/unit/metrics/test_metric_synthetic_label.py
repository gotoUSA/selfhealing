"""
메트릭 is_synthetic 레이블 자동 태깅 테스트.

TestModeContext 활성화 시 메트릭에 is_synthetic 레이블이 자동 추가되는지 검증합니다.
"""

import pytest
from unittest.mock import patch, MagicMock

from selfhealing.core.test_mode_context import TestModeContext


class TestMetricSyntheticLabel:
    """메트릭 합성 레이블 테스트."""

    def test_dlq_metric_without_synthetic_context(self):
        """합성 컨텍스트 없이 DLQ 메트릭 기록."""
        from selfhealing.services.metrics import recorders

        # TestModeContext 비활성 상태 확인
        assert TestModeContext.is_synthetic() is False

        with patch.object(recorders, "dlq_items_total") as mock_counter:
            mock_labels = MagicMock()
            mock_counter.labels.return_value = mock_labels

            recorders.record_dlq_item_created(domain="payment", failure_type="timeout")

            mock_counter.labels.assert_called_once_with(
                domain="payment",
                failure_type="timeout",
                is_synthetic="false",
            )
            mock_labels.inc.assert_called_once()

    def test_dlq_metric_with_synthetic_context(self):
        """합성 컨텍스트에서 DLQ 메트릭 기록."""
        from selfhealing.services.metrics import recorders

        with TestModeContext.start(session_id="test-123"):
            with patch.object(recorders, "dlq_items_total") as mock_counter:
                mock_labels = MagicMock()
                mock_counter.labels.return_value = mock_labels

                recorders.record_dlq_item_created(domain="payment", failure_type="timeout")

                mock_counter.labels.assert_called_once_with(
                    domain="payment",
                    failure_type="timeout",
                    is_synthetic="true",
                )
                mock_labels.inc.assert_called_once()

    def test_retry_metric_synthetic_label(self):
        """Retry 메트릭 합성 레이블 테스트."""
        from selfhealing.services.metrics import recorders

        # 비합성 모드
        with patch.object(recorders, "retry_attempts_histogram") as mock_hist:
            with patch.object(recorders, "retry_outcomes_total") as mock_counter:
                mock_hist.labels.return_value = MagicMock()
                mock_counter.labels.return_value = MagicMock()

                recorders.record_retry_attempt(domain="order", attempt_count=3, outcome="success")

                mock_hist.labels.assert_called_with(
                    domain="order",
                    is_synthetic="false",
                )

        # 합성 모드
        with TestModeContext.start():
            with patch.object(recorders, "retry_attempts_histogram") as mock_hist:
                with patch.object(recorders, "retry_outcomes_total") as mock_counter:
                    mock_hist.labels.return_value = MagicMock()
                    mock_counter.labels.return_value = MagicMock()

                    recorders.record_retry_attempt(domain="order", attempt_count=3, outcome="success")

                    mock_hist.labels.assert_called_with(
                        domain="order",
                        is_synthetic="true",
                    )

    def test_circuit_breaker_metric_synthetic_label(self):
        """Circuit Breaker 메트릭 합성 레이블 테스트."""
        from selfhealing.services.metrics import recorders

        with TestModeContext.start(session_id="cb-test"):
            with patch.object(recorders, "circuit_breaker_state") as mock_gauge:
                with patch.object(recorders, "circuit_breaker_transitions") as mock_counter:
                    mock_gauge.labels.return_value = MagicMock()
                    mock_counter.labels.return_value = MagicMock()

                    recorders.record_circuit_breaker_state_change(
                        service="payment-gateway", from_state="closed", to_state="open"
                    )

                    mock_counter.labels.assert_called_once_with(
                        service="payment-gateway",
                        cell_id="",
                        from_state="closed",
                        to_state="open",
                        is_synthetic="true",
                    )

    def test_metric_is_synthetic_label(self):
        """
        문서 137 섹션 5.1 명시 테스트: 메트릭 레이블 자동 설정.

        TestModeContext 활성화 시 메트릭에 is_synthetic="true" 레이블이
        자동으로 설정되는지 검증합니다.
        """
        # 합성 모드 비활성화 상태
        assert TestModeContext.get_synthetic_label_value() == "false"

        # 합성 모드 활성화 상태
        with TestModeContext.start():
            assert TestModeContext.get_synthetic_label_value() == "true"

        # 다시 비활성화 상태
        assert TestModeContext.get_synthetic_label_value() == "false"

    def test_replay_metric_synthetic_label(self):
        """Replay 메트릭 합성 레이블 테스트."""
        from selfhealing.services.metrics import recorders

        # 합성 모드
        with TestModeContext.start():
            with patch.object(recorders, "replay_attempts_total") as mock_attempts:
                with patch.object(recorders, "replay_outcomes_total") as mock_outcomes:
                    mock_attempts.labels.return_value = MagicMock()
                    mock_outcomes.labels.return_value = MagicMock()

                    recorders.record_replay_attempt(domain="inventory", replay_type="batch", success=True)

                    mock_attempts.labels.assert_called_once_with(
                        domain="inventory",
                        replay_type="batch",
                        is_synthetic="true",
                    )
                    mock_outcomes.labels.assert_called_once_with(
                        domain="inventory",
                        outcome="success",
                        is_synthetic="true",
                    )


class TestMetricLabelTransition:
    """메트릭 레이블 상태 전환 테스트."""

    def test_label_changes_with_context(self):
        """컨텍스트 진입/종료 시 레이블 변경."""
        # 초기 상태
        assert TestModeContext.get_synthetic_label_value() == "false"

        # 진입
        with TestModeContext.start():
            assert TestModeContext.get_synthetic_label_value() == "true"

        # 종료 후
        assert TestModeContext.get_synthetic_label_value() == "false"

    def test_nested_context_label_values(self):
        """중첩 컨텍스트에서 레이블 값."""
        assert TestModeContext.get_synthetic_label_value() == "false"

        with TestModeContext.start():
            assert TestModeContext.get_synthetic_label_value() == "true"

            with TestModeContext.start():
                assert TestModeContext.get_synthetic_label_value() == "true"

            assert TestModeContext.get_synthetic_label_value() == "true"

        assert TestModeContext.get_synthetic_label_value() == "false"

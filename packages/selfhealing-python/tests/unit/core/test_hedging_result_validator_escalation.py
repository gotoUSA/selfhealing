"""
Hedging Result Validator - Meta-Watchdog 에스컬레이션 테스트.

result_validator.py의 _handle_critical_mismatch 메서드에서
EscalationManager.escalate() 호출을 검증합니다.

구조적 불일치(type, structure) 감지 시 Meta-Watchdog으로 에스컬레이션하여
운영자에게 알림을 전달합니다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from selfhealing.core.hedging.result_validator import (
    HedgingResultValidator,
    ResultMismatchRecord,
)


class TestMetaWatchdogEscalation:
    """
    Meta-Watchdog 에스컬레이션 테스트.

    HedgingResultValidator가 구조적 불일치(type, structure) 감지 시
    EscalationManager.escalate()를 호출하는지 검증합니다.
    """

    def test_escalate_on_type_mismatch(self):
        """타입 불일치 시 에스컬레이션 호출 검증."""
        with patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_mgr_cls:
            # Mock 설정
            mock_mgr_instance = MagicMock()
            mock_escalation_mgr_cls.return_value = mock_mgr_instance

            # 에스컬레이션 활성화, 영속화 비활성화 설정
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,  # 100% 검증
                escalate_on_structure=True,
                persist_critical=False,
            )

            # 타입 불일치 레코드 생성 (int vs str)
            record = ResultMismatchRecord(
                operation_id="test-op-001",
                winner_source="primary",
                winner_value_hash="abc123",
                other_source="secondary",
                other_value_hash="def456",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
                winner_region="ap-northeast-2",
                other_region="us-east-1",
            )

            # _handle_critical_mismatch 직접 호출
            validator._handle_critical_mismatch(record)

            # EscalationManager.escalate() 호출 검증
            mock_mgr_instance.escalate.assert_called_once()
            call_args = mock_mgr_instance.escalate.call_args

            # 호출 인자 검증
            assert call_args.kwargs["component"] == "hedging"
            assert "type mismatch" in call_args.kwargs["message"]
            assert "primary" in call_args.kwargs["message"]
            assert "secondary" in call_args.kwargs["message"]
            assert call_args.kwargs["details"]["operation_id"] == "test-op-001"
            assert call_args.kwargs["details"]["mismatch_type"] == "type"

    def test_escalate_on_structure_mismatch(self):
        """구조 불일치 시 에스컬레이션 호출 검증."""
        with patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_mgr_cls:
            mock_mgr_instance = MagicMock()
            mock_escalation_mgr_cls.return_value = mock_mgr_instance

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=False,
            )

            record = ResultMismatchRecord(
                operation_id="test-op-002",
                winner_source="region_a",
                winner_value_hash="hash1",
                other_source="region_b",
                other_value_hash="hash2",
                mismatch_type="structure",
                detected_at=datetime.now(timezone.utc),
                winner_region="ap-northeast-2",
                other_region="eu-west-1",
            )

            validator._handle_critical_mismatch(record)

            mock_mgr_instance.escalate.assert_called_once()
            call_args = mock_mgr_instance.escalate.call_args
            assert call_args.kwargs["details"]["mismatch_type"] == "structure"

    def test_no_escalate_when_disabled(self):
        """에스컬레이션 비활성화 시 호출하지 않음."""
        with patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_mgr_cls:
            mock_mgr_instance = MagicMock()
            mock_escalation_mgr_cls.return_value = mock_mgr_instance

            # 에스컬레이션 비활성화
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,  # 비활성화
                persist_critical=False,
            )

            record = ResultMismatchRecord(
                operation_id="test-op-003",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
            )

            validator._handle_critical_mismatch(record)

            # EscalationManager가 생성되지 않아야 함
            mock_escalation_mgr_cls.assert_not_called()

    def test_escalate_exception_handling(self):
        """에스컬레이션 실패 시 예외 처리 검증."""
        with patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_mgr_cls:
            mock_mgr_instance = MagicMock()
            mock_mgr_instance.escalate.side_effect = RuntimeError("Slack connection failed")
            mock_escalation_mgr_cls.return_value = mock_mgr_instance

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=False,
            )

            record = ResultMismatchRecord(
                operation_id="test-op-004",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="structure",
                detected_at=datetime.now(timezone.utc),
            )

            # 예외가 발생해도 실패하지 않아야 함 (graceful handling)
            validator._handle_critical_mismatch(record)

            # escalate는 호출되었어야 함
            mock_mgr_instance.escalate.assert_called_once()

    def test_escalate_import_error_handling(self):
        """EscalationManager 모듈 없을 때 graceful 처리."""
        # 모듈 import 실패 시나리오 테스트
        with patch.dict(
            "sys.modules",
            {"selfhealing.meta.escalation": None},
        ):
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=False,
            )

            record = ResultMismatchRecord(
                operation_id="test-op-005",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
            )

            # ImportError가 발생해도 실패하지 않아야 함
            validator._handle_critical_mismatch(record)


class TestValidateAsyncTriggersEscalation:
    """
    validate_async가 에스컬레이션을 트리거하는지 통합 테스트.
    """

    def test_validate_async_triggers_escalation_on_type_mismatch(self):
        """validate_async로 타입 불일치 감지 시 에스컬레이션 트리거."""
        with (
            patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_cls,
            patch("selfhealing.core.hedging.result_validator.record_result_mismatch"),
        ):
            mock_mgr = MagicMock()
            mock_escalation_cls.return_value = mock_mgr

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=False,
            )

            # 타입 불일치 발생: int vs str
            validator.validate_async(
                operation_id="async-op-001",
                winner_source="primary",
                winner_value=100,  # int
                winner_latency_ms=50.0,
                other_results={
                    "secondary": ("not_an_int", 100.0),  # str
                },
            )

            # 백그라운드 스레드 완료 대기
            time.sleep(0.5)
            validator.shutdown()

            # 에스컬레이션 호출 검증
            mock_mgr.escalate.assert_called_once()

    def test_validate_async_no_trigger_on_value_mismatch(self):
        """validate_async로 값 불일치 감지 시 에스컬레이션 미트리거."""
        with (
            patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_cls,
            patch("selfhealing.core.hedging.result_validator.record_result_mismatch"),
        ):
            mock_mgr = MagicMock()
            mock_escalation_cls.return_value = mock_mgr

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=False,
            )

            # 값 불일치 발생: 동일 타입/구조, 값만 다름
            validator.validate_async(
                operation_id="async-op-002",
                winner_source="primary",
                winner_value={"a": 1},
                winner_latency_ms=50.0,
                other_results={
                    "secondary": ({"a": 2}, 100.0),  # 값만 다름
                },
            )

            # 백그라운드 스레드 완료 대기
            time.sleep(0.5)
            validator.shutdown()

            # 값 불일치는 구조적 불일치가 아니므로 에스컬레이션 미호출
            mock_escalation_cls.assert_not_called()

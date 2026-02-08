"""
Hedging Result Validator - DiskPersistentBuffer 영속화 테스트.

result_validator.py의 _handle_critical_mismatch 메서드에서
DiskPersistentBuffer.put() 호출을 검증합니다.

구조적 불일치(type, structure) 감지 시 LMDB 기반 DiskBuffer에 영속 저장하여
Pod 재시작 후에도 불일치 기록을 보존합니다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.core.hedging.result_validator import (
    HedgingResultValidator,
    ResultMismatchRecord,
)


class TestDiskPersistentBufferPersistence:
    """
    DiskPersistentBuffer 영속화 테스트.

    HedgingResultValidator가 구조적 불일치(type, structure) 감지 시
    DiskPersistentBuffer.put()을 호출하여 영속 저장하는지 검증합니다.
    """

    def test_persist_type_mismatch_to_disk_buffer(self):
        """타입 불일치 시 DiskBuffer에 영속 저장 검증."""
        with patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            # 영속화 활성화, 에스컬레이션 비활성화
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,  # 영속화 활성화
            )

            record = ResultMismatchRecord(
                operation_id="persist-op-001",
                winner_source="primary",
                winner_value_hash="abc123",
                other_source="secondary",
                other_value_hash="def456",
                mismatch_type="type",
                detected_at=datetime(2026, 2, 5, 12, 0, 0, tzinfo=timezone.utc),
                winner_region="ap-northeast-2",
                other_region="us-east-1",
            )

            validator._handle_critical_mismatch(record)

            # DiskBuffer.put() 호출 검증
            mock_buffer.put.assert_called_once()
            call_args = mock_buffer.put.call_args[0][0]

            # 저장 데이터 검증
            assert call_args["type"] == "hedging_mismatch"
            assert call_args["operation_id"] == "persist-op-001"
            assert call_args["mismatch_type"] == "type"
            assert call_args["winner_source"] == "primary"
            assert call_args["other_source"] == "secondary"
            assert call_args["winner_region"] == "ap-northeast-2"
            assert call_args["other_region"] == "us-east-1"
            assert "2026-02-05" in call_args["detected_at"]

    def test_persist_structure_mismatch_to_disk_buffer(self):
        """구조 불일치 시 DiskBuffer에 영속 저장 검증."""
        with patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,
            )

            record = ResultMismatchRecord(
                operation_id="persist-op-002",
                winner_source="region_a",
                winner_value_hash="hash1",
                other_source="region_b",
                other_value_hash="hash2",
                mismatch_type="structure",
                detected_at=datetime.now(timezone.utc),
                winner_region="eu-west-1",
                other_region="ap-southeast-1",
            )

            validator._handle_critical_mismatch(record)

            mock_buffer.put.assert_called_once()
            call_args = mock_buffer.put.call_args[0][0]
            assert call_args["mismatch_type"] == "structure"
            assert call_args["winner_region"] == "eu-west-1"
            assert call_args["other_region"] == "ap-southeast-1"

    def test_no_persist_when_disabled(self):
        """영속화 비활성화 시 DiskBuffer 호출하지 않음."""
        with patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=False,  # 비활성화
            )

            record = ResultMismatchRecord(
                operation_id="persist-op-003",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
            )

            validator._handle_critical_mismatch(record)

            # get_disk_buffer가 호출되지 않아야 함
            mock_get_buffer.assert_not_called()

    def test_persist_exception_handling(self):
        """DiskBuffer 저장 실패 시 예외 처리 검증."""
        with patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer:
            mock_buffer = MagicMock()
            mock_buffer.put.side_effect = RuntimeError("Disk full")
            mock_get_buffer.return_value = mock_buffer

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,
            )

            record = ResultMismatchRecord(
                operation_id="persist-op-004",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="structure",
                detected_at=datetime.now(timezone.utc),
            )

            # 예외가 발생해도 실패하지 않아야 함 (graceful handling)
            validator._handle_critical_mismatch(record)

            # put은 호출되었어야 함
            mock_buffer.put.assert_called_once()

    def test_persist_import_error_handling(self):
        """DiskBuffer 모듈 없을 때 graceful 처리."""
        with patch.dict(
            "sys.modules",
            {"selfhealing.audit.persistence.disk_buffer": None},
        ):
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,
            )

            record = ResultMismatchRecord(
                operation_id="persist-op-005",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
            )

            # ImportError가 발생해도 실패하지 않아야 함
            validator._handle_critical_mismatch(record)


class TestValidateAsyncTriggersPersistence:
    """
    validate_async가 영속화를 트리거하는지 통합 테스트.
    """

    def test_validate_async_triggers_persistence_on_structure_mismatch(self):
        """validate_async로 구조 불일치 감지 시 영속화 트리거."""
        with (
            patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer,
            patch("selfhealing.core.hedging.result_validator.record_result_mismatch"),
        ):
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,
            )

            # 구조 불일치 발생: dict 키가 다름
            validator.validate_async(
                operation_id="async-op-001",
                winner_source="primary",
                winner_value={"a": 1, "b": 2},
                winner_latency_ms=50.0,
                other_results={
                    "secondary": ({"a": 1, "c": 3}, 100.0),  # 키가 다름
                },
            )

            # 백그라운드 스레드 완료 대기
            time.sleep(0.5)
            validator.shutdown()

            # 영속화 호출 검증
            mock_buffer.put.assert_called_once()
            call_args = mock_buffer.put.call_args[0][0]
            assert call_args["mismatch_type"] == "structure"

    def test_validate_async_no_trigger_on_value_mismatch(self):
        """validate_async로 값 불일치 감지 시 영속화 미트리거."""
        with (
            patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer,
            patch("selfhealing.core.hedging.result_validator.record_result_mismatch"),
        ):
            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=False,
                persist_critical=True,
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

            # shutdown(wait=True)가 모든 백그라운드 작업 완료를 보장
            validator.shutdown()

            # 값 불일치는 구조적 불일치가 아니므로 영속화 미호출
            mock_get_buffer.assert_not_called()


class TestEscalationAndPersistenceTogether:
    """
    에스컬레이션과 영속화 동시 테스트.

    두 기능이 동시에 활성화되었을 때 모두 동작하는지 검증합니다.
    """

    def test_both_escalation_and_persistence_on_type_mismatch(self):
        """타입 불일치 시 에스컬레이션과 영속화 모두 동작."""
        with (
            patch("selfhealing.meta.escalation.EscalationManager") as mock_escalation_cls,
            patch("selfhealing.audit.persistence.disk_buffer.get_disk_buffer") as mock_get_buffer,
        ):
            # Mock 설정
            mock_mgr = MagicMock()
            mock_escalation_cls.return_value = mock_mgr

            mock_buffer = MagicMock()
            mock_get_buffer.return_value = mock_buffer

            # 둘 다 활성화
            validator = HedgingResultValidator(
                enabled=True,
                sample_rate=1.0,
                escalate_on_structure=True,
                persist_critical=True,
            )

            record = ResultMismatchRecord(
                operation_id="both-op-001",
                winner_source="primary",
                winner_value_hash="abc",
                other_source="secondary",
                other_value_hash="def",
                mismatch_type="type",
                detected_at=datetime.now(timezone.utc),
                winner_region="ap-northeast-2",
                other_region="us-east-1",
            )

            validator._handle_critical_mismatch(record)

            # 둘 다 호출되었어야 함
            mock_mgr.escalate.assert_called_once()
            mock_buffer.put.assert_called_once()

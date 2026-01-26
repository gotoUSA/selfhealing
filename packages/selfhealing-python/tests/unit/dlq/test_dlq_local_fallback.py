"""
DLQ Local Fallback 테스트.

DB 저장 실패 시 로컬 파일로 fallback하여 무손실 보장.

Test Scenarios:
1. DB 실패 → Local fallback 저장 성공
2. DB 실패 + Fallback 실패 → failed 반환
3. Fallback 파일에 올바른 형식으로 저장되는지 확인
4. DLQEntryResult.is_fallback 속성 확인
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.dlq_models import DLQConfig, DLQEntryResult
from selfhealing.services.dlq.store_operations import StoreOperationsMixin, DLQ_FALLBACK_PATH


class MockDLQService(StoreOperationsMixin):
    """테스트용 Mock DLQ Service."""
    
    def __init__(self, config=None, repository=None):
        self.config = config or DLQConfig(enabled=True)
        self._repository = repository
    
    @property
    def repository(self):
        return self._repository
    
    @property
    def is_enabled(self):
        return self.config.enabled
    
    def _log_dlq_audit(self, **kwargs):
        """Audit 로깅 Mock."""
        pass


class TestDLQLocalFallback:
    """DLQ Local Fallback 테스트."""
    
    def test_db_failure_triggers_local_fallback(self, tmp_path):
        """DB 실패 시 로컬 파일 fallback이 동작하는지 확인."""
        # Given: DB 저장 시 예외 발생하는 repository
        mock_repo = MagicMock()
        mock_repo.create.side_effect = Exception("DB connection failed")
        
        service = MockDLQService(repository=mock_repo)
        
        # Fallback 경로를 임시 디렉토리로 변경
        fallback_path = tmp_path / "dlq_fallback.jsonl"
        
        with patch("selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH", fallback_path):
            # When: store_failure 호출
            result = service.store_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
                error_message="Payment gateway timeout",
            )
        
        # Then: fallback 결과 반환
        assert result.success is False
        assert result.is_fallback is True
        assert result.fallback_path == str(fallback_path)
        assert "DB connection failed" in result.error
        
        # And: 파일에 저장되었는지 확인
        assert fallback_path.exists()
        with open(fallback_path, "r") as f:
            line = f.readline()
            entry = json.loads(line)
        
        assert entry["pending_reconciliation"] is True
        assert entry["entry_data"]["domain"] == "payment"
        assert entry["entry_data"]["failure_type"] == "PG_TIMEOUT"
        assert "timestamp" in entry
    
    def test_db_and_fallback_both_fail(self, tmp_path):
        """DB와 fallback 모두 실패 시 failed 반환."""
        # Given: DB 저장 실패
        mock_repo = MagicMock()
        mock_repo.create.side_effect = Exception("DB connection failed")
        
        service = MockDLQService(repository=mock_repo)
        
        # Fallback 쓰기도 실패하도록 mock
        with patch("selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH") as mock_path:
            mock_path.parent.mkdir.side_effect = PermissionError("Cannot create directory")
            
            # When: store_failure 호출
            result = service.store_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
            )
        
        # Then: fallback 없이 failed 반환
        assert result.success is False
        assert result.is_fallback is False
        assert result.fallback_path is None
    
    def test_fallback_entry_format(self, tmp_path):
        """Fallback 엔트리 형식 검증."""
        # Given
        mock_repo = MagicMock()
        mock_repo.create.side_effect = Exception("DB error")
        
        service = MockDLQService(repository=mock_repo)
        fallback_path = tmp_path / "dlq_fallback.jsonl"
        
        with patch("selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH", fallback_path):
            # When
            service.store_failure(
                domain="inventory",
                failure_type="STOCK_UPDATE_FAILED",
                entity_type="product",
                entity_id="SKU-12345",
                error_code="INV-001",
                error_message="Stock update failed",
                snapshot_data={"stock_before": 100, "stock_after": None},
                metadata={"warehouse": "WH-01"},
            )
        
        # Then: 모든 필드가 올바르게 저장되었는지 확인
        with open(fallback_path, "r") as f:
            entry = json.loads(f.readline())
        
        data = entry["entry_data"]
        assert data["domain"] == "inventory"
        assert data["failure_type"] == "STOCK_UPDATE_FAILED"
        assert data["entity_type"] == "product"
        assert data["entity_id"] == "SKU-12345"
        assert data["error_code"] == "INV-001"
        assert data["error_message"] == "Stock update failed"
        assert data["snapshot_data"]["stock_before"] == 100
        assert data["metadata"]["warehouse"] == "WH-01"
        
        assert entry["original_error"] == "DB error"
        assert entry["pending_reconciliation"] is True
    
    def test_multiple_fallback_entries_appended(self, tmp_path):
        """여러 fallback 엔트리가 순차적으로 추가되는지 확인."""
        # Given
        mock_repo = MagicMock()
        mock_repo.create.side_effect = Exception("DB unavailable")
        
        service = MockDLQService(repository=mock_repo)
        fallback_path = tmp_path / "dlq_fallback.jsonl"
        
        with patch("selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH", fallback_path):
            # When: 3개의 실패 저장
            for i in range(3):
                service.store_failure(
                    domain=f"domain_{i}",
                    failure_type=f"TYPE_{i}",
                )
        
        # Then: 3줄 저장되었는지 확인
        with open(fallback_path, "r") as f:
            lines = f.readlines()
        
        assert len(lines) == 3
        
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["entry_data"]["domain"] == f"domain_{i}"
            assert entry["entry_data"]["failure_type"] == f"TYPE_{i}"


class TestDLQEntryResultFallback:
    """DLQEntryResult fallback 관련 테스트."""
    
    def test_is_fallback_true_when_fallback_path_set(self):
        """fallback_path가 설정되면 is_fallback이 True."""
        result = DLQEntryResult.fallback(
            error="DB failed",
            fallback_path="/tmp/dlq_fallback.jsonl"
        )
        
        assert result.success is False
        assert result.is_fallback is True
        assert result.fallback_path == "/tmp/dlq_fallback.jsonl"
        assert result.error == "DB failed"
    
    def test_is_fallback_false_when_no_fallback_path(self):
        """fallback_path가 없으면 is_fallback이 False."""
        result = DLQEntryResult.failed("Some error")
        
        assert result.success is False
        assert result.is_fallback is False
        assert result.fallback_path is None
    
    def test_created_result_not_fallback(self):
        """정상 생성된 결과는 fallback이 아님."""
        result = DLQEntryResult.created(dlq_id=123)
        
        assert result.success is True
        assert result.is_fallback is False
        assert result.dlq_id == 123

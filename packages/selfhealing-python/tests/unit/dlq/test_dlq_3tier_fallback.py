"""
DLQ 3단계 Fallback 체인 단위 테스트.

테스트 대상: selfhealing.services.dlq.store_operations.StoreOperationsMixin._write_to_local_fallback

테스트 시나리오:
1. 1차 DiskPersistentBuffer (LMDB) 성공 시 경로 반환
2. DiskPersistentBuffer 불가 시 JSONL 파일 Fallback
3. DiskPersistentBuffer Import 실패 시 JSONL Fallback
4. 모든 Fallback 실패 시 stderr + None 반환
5. DiskPersistentBuffer에 올바른 데이터 전달 검증
"""

import json
from unittest.mock import MagicMock, patch

from selfhealing.services.dlq.store_operations import (
    StoreOperationsMixin,
)
from selfhealing.services.dlq.models import DLQConfig


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
        pass


class TestDiskPersistentBufferFallback:
    """1차 Fallback: DiskPersistentBuffer (LMDB) 테스트."""

    def test_lmdb_fallback_used_first(self):
        """DiskPersistentBuffer 사용 가능 시 1차 Fallback으로 사용."""
        mock_buffer = MagicMock()
        mock_adapter_cls = MagicMock()
        mock_adapter_cls.get_instance.return_value = mock_buffer

        service = MockDLQService()

        with patch.dict(
            "sys.modules",
            {"selfhealing.audit.persistence.disk_buffer": MagicMock(DiskBufferAdapter=mock_adapter_cls)},
        ):
            result = service._write_to_local_fallback({"domain": "test"}, "db_error")

        assert result == "disk_persistent_buffer://dlq_fallback"
        mock_buffer.put.assert_called_once()

    def test_lmdb_fallback_data_format(self):
        """DiskPersistentBuffer에 올바른 형식의 데이터 전달."""
        mock_buffer = MagicMock()
        mock_adapter_cls = MagicMock()
        mock_adapter_cls.get_instance.return_value = mock_buffer

        service = MockDLQService()

        with patch.dict(
            "sys.modules",
            {"selfhealing.audit.persistence.disk_buffer": MagicMock(DiskBufferAdapter=mock_adapter_cls)},
        ):
            entry_data = {"domain": "payment", "failure_type": "PG_TIMEOUT"}
            service._write_to_local_fallback(entry_data, "connection_error")

        put_arg = mock_buffer.put.call_args[0][0]
        assert put_arg["category"] == "dlq_fallback"
        assert put_arg["original_error"] == "connection_error"
        assert put_arg["entry_data"] == entry_data
        assert put_arg["pending_reconciliation"] is True
        assert "timestamp" in put_arg


class TestJsonlFallback:
    """2차 Fallback: JSONL 파일 테스트."""

    def test_jsonl_fallback_when_lmdb_import_fails(self, tmp_path):
        """DiskPersistentBuffer Import 실패 시 JSONL 파일 Fallback."""
        service = MockDLQService()
        fallback_path = tmp_path / "dlq_fallback.jsonl"

        # DiskBufferAdapter import가 ImportError를 발생하도록 설정
        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "selfhealing.audit.persistence.disk_buffer":
                raise ImportError("No LMDB")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch(
                "selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH",
                fallback_path,
            ):
                result = service._write_to_local_fallback({"domain": "test"}, "db_error")

        assert result == str(fallback_path)
        assert fallback_path.exists()

        with open(fallback_path) as f:
            saved = json.loads(f.readline())

        assert saved["pending_reconciliation"] is True
        assert saved["entry_data"]["domain"] == "test"

    def test_jsonl_fallback_when_lmdb_runtime_fails(self, tmp_path):
        """DiskPersistentBuffer 런타임 오류 시 JSONL 파일 Fallback."""
        service = MockDLQService()
        fallback_path = tmp_path / "dlq_fallback.jsonl"

        mock_adapter_cls = MagicMock()
        mock_adapter_cls.get_instance.side_effect = RuntimeError("LMDB corrupted")

        with patch.dict(
            "sys.modules",
            {"selfhealing.audit.persistence.disk_buffer": MagicMock(DiskBufferAdapter=mock_adapter_cls)},
        ):
            with patch(
                "selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH",
                fallback_path,
            ):
                result = service._write_to_local_fallback({"domain": "inventory"}, "db_timeout")

        assert result == str(fallback_path)
        assert fallback_path.exists()


class TestStderrFallback:
    """3차 Fallback: stderr 출력 테스트."""

    def test_stderr_fallback_when_all_fail(self):
        """모든 Fallback 실패 시 stderr 출력 후 None 반환."""
        service = MockDLQService()

        original_import = __builtins__.__import__ if hasattr(__builtins__, "__import__") else __import__

        def mock_import(name, *args, **kwargs):
            if name == "selfhealing.audit.persistence.disk_buffer":
                raise ImportError("No LMDB")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with patch("selfhealing.services.dlq.store_operations.DLQ_FALLBACK_PATH") as mock_path:
                mock_path.parent.mkdir.side_effect = PermissionError("Cannot create dir")

                result = service._write_to_local_fallback({"domain": "webhook"}, "db_error")

        assert result is None

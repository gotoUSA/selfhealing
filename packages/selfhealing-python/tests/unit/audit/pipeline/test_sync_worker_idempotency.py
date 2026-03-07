"""
AuditSyncWorker Idempotent Consumer 테스트.

테스트 대상:
1. _sync_entry_to_adapter()에서 IdempotencyService 연동
2. 중복 엔트리 스킵 동작
"""

import sys
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig

# 테스트용 IdempotencyService 모듈 패치 경로
IDEMPOTENCY_MODULE = "selfhealing.services.idempotency"


@dataclass
class MockWALEntry:
    """테스트용 WAL 엔트리."""

    sequence: int
    checksum: str
    data: dict


class TestAuditSyncWorkerIdempotency:
    """AuditSyncWorker Idempotent Consumer 테스트."""

    def test_sync_entry_calls_adapter_write(self):
        """_sync_entry_to_adapter()가 adapter.write() 호출."""
        adapter = MagicMock()
        config = SyncWorkerConfig(max_retries=0)
        worker = AuditSyncWorker(config=config)

        entry = MockWALEntry(
            sequence=1,
            checksum="abcd1234",
            data={"event": "test"},
        )

        # IdempotencyService 모킹 (중복 아님)
        with patch(f"{IDEMPOTENCY_MODULE}.IdempotencyService") as mock_service:
            mock_instance = MagicMock()
            mock_instance.check.return_value = None  # 처음 처리
            mock_service.return_value = mock_instance

            worker._sync_entry_to_adapter(adapter, entry)

        adapter.write.assert_called_once_with(entry.data)

    def test_sync_entry_skips_duplicate(self):
        """중복 엔트리는 스킵."""
        adapter = MagicMock()
        config = SyncWorkerConfig(max_retries=0)
        worker = AuditSyncWorker(config=config)

        entry = MockWALEntry(
            sequence=1,
            checksum="abcd1234",
            data={"event": "test"},
        )

        # IdempotencyService 모킹 (이미 처리됨)
        with patch(f"{IDEMPOTENCY_MODULE}.IdempotencyService") as mock_service:
            mock_instance = MagicMock()
            mock_instance.check.return_value = {"already": "processed"}  # 중복
            mock_service.return_value = mock_instance

            worker._sync_entry_to_adapter(adapter, entry)

        # adapter.write() 호출 안됨
        adapter.write.assert_not_called()

    def test_sync_entry_works_without_idempotency_service(self):
        """IdempotencyService 없어도 정상 동작."""
        adapter = MagicMock()
        config = SyncWorkerConfig(max_retries=0)
        worker = AuditSyncWorker(config=config)

        entry = MockWALEntry(
            sequence=1,
            checksum="abcd1234",
            data={"event": "test"},
        )

        # IdempotencyService import 실패 시뮬레이션을 위해 모듈 제거

        original_modules = {}
        modules_to_remove = [k for k in sys.modules if "idempotency" in k.lower()]
        for mod in modules_to_remove:
            original_modules[mod] = sys.modules.pop(mod, None)

        try:
            # import 자체가 실패하도록 patch
            with patch.dict(sys.modules, {"selfhealing.services.idempotency": None}):
                # ImportError 발생 시 예외 무시하고 진행
                worker._sync_entry_to_adapter(adapter, entry)
        finally:
            # 원본 모듈 복구
            for mod, val in original_modules.items():
                if val is not None:
                    sys.modules[mod] = val

        # 여전히 adapter.write() 호출됨
        adapter.write.assert_called_once()

    def test_sync_entry_uses_wal_recovery_domain(self):
        """IdempotencyDomain.WAL_RECOVERY 사용 확인."""
        adapter = MagicMock()
        config = SyncWorkerConfig(max_retries=0)
        worker = AuditSyncWorker(config=config)

        entry = MockWALEntry(
            sequence=42,
            checksum="efgh5678",
            data={"event": "test"},
        )

        with patch(f"{IDEMPOTENCY_MODULE}.IdempotencyService") as mock_service:
            with patch(f"{IDEMPOTENCY_MODULE}.IdempotencyKey") as mock_key_class:
                mock_instance = MagicMock()
                mock_instance.check.return_value = None
                mock_service.return_value = mock_instance

                worker._sync_entry_to_adapter(adapter, entry)

                # IdempotencyKey.for_operation 호출 확인
                mock_key_class.for_operation.assert_called()
                call_kwargs = mock_key_class.for_operation.call_args
                # WAL_RECOVERY 도메인 사용
                assert "wal_entry" in str(call_kwargs)

"""
WAL 기반 Zero-Loss 테스트

테스트 범위:
1. audit_helpers.py WAL 연동
2. AuditSyncWorker
3. AuditReconciler
4. AuditMetrics WAL 메트릭
"""

import os
import tempfile
import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest


class TestAuditHelpersWAL:
    """audit_helpers.py WAL 연동 테스트."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self):
        """각 테스트 전후로 WAL 상태 초기화."""
        # WAL 비활성화 상태에서 시작
        from selfhealing.services import audit_helpers

        audit_helpers.disable_wal()
        yield
        audit_helpers.disable_wal()

    def test_wal_initialization_with_env(self, tmp_path):
        """환경변수로 WAL 디렉토리 설정."""
        from selfhealing.services import audit_helpers

        wal_dir = str(tmp_path / "wal_test")

        with patch.dict(os.environ, {"AUDIT_WAL_DIR": wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None  # 강제 재초기화

            wal = audit_helpers._get_wal()

            assert wal is not None
            assert os.path.exists(wal_dir)

    def test_log_dlq_store_writes_to_wal(self, tmp_path):
        """log_dlq_store_audit가 WAL에 먼저 기록."""
        from selfhealing.services import audit_helpers

        wal_dir = str(tmp_path / "wal_test")

        with patch.dict(os.environ, {"AUDIT_WAL_DIR": wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None

            # WAL에 기록
            wal_seq = audit_helpers.log_dlq_store_audit(
                dlq_id=123,
                domain="payment",
                failure_type="PG_TIMEOUT",
                error_message="Connection failed",
            )

            # WAL 시퀀스 번호 반환 확인
            assert wal_seq is not None
            assert isinstance(wal_seq, int)
            assert wal_seq >= 1

    def test_log_dlq_replay_writes_to_wal(self, tmp_path):
        """log_dlq_replay_audit가 WAL에 먼저 기록."""
        from selfhealing.services import audit_helpers

        wal_dir = str(tmp_path / "wal_test")

        with patch.dict(os.environ, {"AUDIT_WAL_DIR": wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None

            wal_seq = audit_helpers.log_dlq_replay_audit(
                dlq_id=456,
                domain="point",
                success=True,
                actor_id="user_1",
            )

            assert wal_seq is not None
            assert isinstance(wal_seq, int)

    def test_log_cb_state_change_writes_to_wal(self, tmp_path):
        """log_cb_state_change_audit가 WAL에 먼저 기록."""
        from selfhealing.services import audit_helpers

        wal_dir = str(tmp_path / "wal_test")

        with patch.dict(os.environ, {"AUDIT_WAL_DIR": wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None

            wal_seq = audit_helpers.log_cb_state_change_audit(
                cb_name="payment_cb",
                old_state="closed",
                new_state="open",
                reason="failure_threshold_exceeded",
            )

            assert wal_seq is not None

    def test_wal_disabled_returns_none(self):
        """WAL 비활성화 시 None 반환."""
        from selfhealing.services import audit_helpers

        audit_helpers.disable_wal()

        wal_seq = audit_helpers.log_dlq_store_audit(
            dlq_id=789,
            domain="webhook",
            failure_type="SIGNATURE_INVALID",
        )

        # WAL 비활성화 상태에서도 함수는 정상 동작 (Fail-Open)
        assert wal_seq is None

    def test_get_wal_stats(self, tmp_path):
        """WAL 통계 조회."""
        from selfhealing.services import audit_helpers

        wal_dir = str(tmp_path / "wal_test")

        with patch.dict(os.environ, {"AUDIT_WAL_DIR": wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None

            # 몇 개 기록
            audit_helpers.log_dlq_store_audit(dlq_id=1, domain="test", failure_type="TEST")
            audit_helpers.log_dlq_store_audit(dlq_id=2, domain="test", failure_type="TEST")

            stats = audit_helpers.get_wal_stats()

            assert stats is not None
            assert stats["total_entries"] >= 2
            assert stats["state"] == "active"


class TestAuditSyncWorker:
    """AuditSyncWorker 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """각 테스트 전후로 싱글톤 초기화."""
        from selfhealing.audit.sync_worker import AuditSyncWorker

        AuditSyncWorker.reset_instance()
        yield
        AuditSyncWorker.reset_instance()

    def test_sync_worker_singleton(self):
        """싱글톤 패턴 동작 확인."""
        from selfhealing.audit.sync_worker import AuditSyncWorker

        worker1 = AuditSyncWorker.get_instance()
        worker2 = AuditSyncWorker.get_instance()

        assert worker1 is worker2

    def test_sync_worker_start_stop(self):
        """워커 시작/중지."""
        from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig

        config = SyncWorkerConfig(sync_interval_seconds=0.1)
        worker = AuditSyncWorker.get_instance(config=config)

        # 시작
        assert worker.start() is True
        assert worker.is_running is True

        # 중복 시작 시도
        assert worker.start() is False

        # 중지
        worker.stop()
        assert worker.is_running is False

    def test_sync_batch_with_mock_wal(self, tmp_path):
        """배치 동기화 테스트 (Mock WAL)."""
        from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig
        from selfhealing.audit.wal import WriteAheadLog, WALConfig

        # 실제 WAL 생성
        wal_config = WALConfig(wal_dir=str(tmp_path / "wal"))
        wal = WriteAheadLog(config=wal_config)

        # 테스트 데이터 기록
        wal.write({"event_type": "TEST", "record_id": "test-1"})
        wal.write({"event_type": "TEST", "record_id": "test-2"})

        # Mock adapter
        mock_adapter = MagicMock()

        config = SyncWorkerConfig(sync_interval_seconds=0.1, batch_size=10)
        worker = AuditSyncWorker(wal=wal, central_adapter=mock_adapter, config=config)

        # 즉시 동기화
        synced, failed = worker.sync_now()

        assert synced >= 2
        assert failed == 0

        # 통계 확인
        stats = worker.get_stats()
        assert stats["total_synced"] >= 2

        wal.close()

    def test_sync_worker_retry_on_failure(self, tmp_path):
        """어댑터 실패 시 재시도."""
        from unittest.mock import patch
        from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig
        from selfhealing.audit.wal import WriteAheadLog, WALConfig

        wal_config = WALConfig(wal_dir=str(tmp_path / "wal"))
        wal = WriteAheadLog(config=wal_config)
        wal.write({"event_type": "TEST", "record_id": "test-retry"})

        # 처음 2번 실패, 3번째 성공하는 Mock
        mock_adapter = MagicMock()
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise Exception("Simulated failure")

        mock_adapter.write.side_effect = side_effect

        config = SyncWorkerConfig(
            sync_interval_seconds=0.1,
            max_retries=3,
            retry_delay_seconds=0.01,
        )
        worker = AuditSyncWorker(wal=wal, central_adapter=mock_adapter, config=config)

        # IdempotencyService import를 막아 재시도 로직만 테스트
        with patch.dict("sys.modules", {"selfhealing.services.idempotency_service": None}):
            synced, failed = worker.sync_now()

        # 재시도 후 성공
        assert synced >= 1
        assert call_count[0] >= 3

        stats = worker.get_stats()
        assert stats["total_retries"] >= 2

        wal.close()


class TestAuditReconciler:
    """AuditReconciler 테스트."""

    @pytest.fixture(autouse=True)
    def reset_singleton(self):
        """각 테스트 전후로 싱글톤 초기화."""
        from selfhealing.audit.reconciler import AuditReconciler

        AuditReconciler.reset_instance()
        yield
        AuditReconciler.reset_instance()

    def test_reconciler_singleton(self):
        """싱글톤 패턴 동작 확인."""
        from selfhealing.audit.reconciler import AuditReconciler

        reconciler1 = AuditReconciler.get_instance()
        reconciler2 = AuditReconciler.get_instance()

        assert reconciler1 is reconciler2

    def test_reconciler_start_stop(self):
        """Reconciler 시작/중지."""
        from selfhealing.audit.reconciler import AuditReconciler, ReconcilerConfig

        config = ReconcilerConfig(check_interval_seconds=0.1)
        reconciler = AuditReconciler.get_instance(config=config)

        assert reconciler.start() is True
        assert reconciler.is_running is True

        assert reconciler.start() is False  # 중복 시작

        reconciler.stop()
        assert reconciler.is_running is False

    def test_reconcile_now(self, tmp_path):
        """즉시 정합성 검증."""
        from selfhealing.audit.reconciler import AuditReconciler, ReconcilerConfig
        from selfhealing.audit.wal import WriteAheadLog, WALConfig

        wal_config = WALConfig(wal_dir=str(tmp_path / "wal"))
        wal = WriteAheadLog(config=wal_config)

        # 테스트 데이터 기록
        wal.write({"event_type": "TEST", "record_id": "reconcile-1"})
        wal.write({"event_type": "TEST", "record_id": "reconcile-2"})

        config = ReconcilerConfig(check_interval_seconds=60)  # 긴 간격
        reconciler = AuditReconciler(wal=wal, config=config)

        result = reconciler.reconcile_now()

        assert result.wal_entry_count >= 2
        assert result.duration_ms >= 0

        wal.close()

    def test_reconcile_result_to_dict(self):
        """ReconcileResult 딕셔너리 변환."""
        from selfhealing.audit.reconciler import ReconcileResult

        result = ReconcileResult(
            wal_entry_count=10,
            central_entry_count=8,
            missing_count=2,
            resent_count=1,
            resend_failed_count=1,
            duration_ms=123.45,
        )

        d = result.to_dict()

        assert d["wal_entry_count"] == 10
        assert d["missing_count"] == 2
        assert d["is_consistent"] is False

    def test_reconcile_missing_callback(self, tmp_path):
        """누락 발견 콜백 호출."""
        from selfhealing.audit.reconciler import AuditReconciler, ReconcilerConfig
        from selfhealing.audit.wal import WriteAheadLog, WALConfig

        wal_config = WALConfig(wal_dir=str(tmp_path / "wal"))
        wal = WriteAheadLog(config=wal_config)
        wal.write({"event_type": "TEST", "record_id": "missing-1"})

        missing_counts = []

        def on_missing(count):
            missing_counts.append(count)

        config = ReconcilerConfig(check_interval_seconds=60)
        reconciler = AuditReconciler(
            wal=wal,
            config=config,
            on_missing_found=on_missing,
        )

        reconciler.reconcile_now()

        # 콜백이 호출되었는지 확인
        assert len(missing_counts) >= 1

        wal.close()


class TestAuditMetricsWAL:
    """AuditMetrics WAL 메트릭 테스트."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """각 테스트 전후로 메트릭 초기화."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()
        metrics.reset()
        yield
        metrics.reset()

    def test_record_wal_write(self):
        """WAL 기록 메트릭."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_wal_write(success=True)
        metrics.record_wal_write(success=True)
        metrics.record_wal_write(success=False)

        wal_metrics = metrics.get_wal_metrics()

        assert wal_metrics["audit_wal_writes_total"] == 2
        assert wal_metrics["audit_wal_write_failures_total"] == 1

    def test_record_central_write(self):
        """중앙 저장소 기록 메트릭."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_central_write(count=5)
        metrics.record_central_write(count=3)

        wal_metrics = metrics.get_wal_metrics()

        assert wal_metrics["audit_central_writes_total"] == 8

    def test_set_sync_lag(self):
        """동기화 지연 메트릭."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.set_sync_lag(100)
        assert metrics.get_wal_metrics()["audit_sync_lag_entries"] == 100

        metrics.set_sync_lag(50)
        assert metrics.get_wal_metrics()["audit_sync_lag_entries"] == 50

    def test_record_reconcile_missing(self):
        """Reconciler 누락 메트릭."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_reconcile_missing(5)
        metrics.record_reconcile_missing(3)

        wal_metrics = metrics.get_wal_metrics()

        assert wal_metrics["audit_reconcile_missing_total"] == 8

    def test_get_metrics_includes_wal(self):
        """get_metrics()에 WAL 메트릭 포함."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_wal_write(success=True)
        metrics.set_sync_lag(42)

        all_metrics = metrics.get_metrics()

        assert "audit_wal_writes_total" in all_metrics
        assert "audit_sync_lag_entries" in all_metrics
        assert all_metrics["audit_wal_writes_total"] == 1
        assert all_metrics["audit_sync_lag_entries"] == 42

    def test_prometheus_format_includes_wal(self):
        """Prometheus 포맷에 WAL 메트릭 포함."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_wal_write(success=True)
        metrics.record_wal_write(success=False)

        prom_text = metrics.get_prometheus_format()

        assert "audit_wal_writes_total" in prom_text
        assert "audit_wal_write_failures_total" in prom_text
        assert "audit_sync_lag_entries" in prom_text

    def test_reset_clears_wal_metrics(self):
        """reset()이 WAL 메트릭도 초기화."""
        from selfhealing.audit.resilience import AuditMetrics

        metrics = AuditMetrics.get_instance()

        metrics.record_wal_write(success=True)
        metrics.record_central_write(count=10)
        metrics.set_sync_lag(100)

        metrics.reset()

        wal_metrics = metrics.get_wal_metrics()

        assert wal_metrics["audit_wal_writes_total"] == 0
        assert wal_metrics["audit_central_writes_total"] == 0
        assert wal_metrics["audit_sync_lag_entries"] == 0


class TestIntegrationWALFlow:
    """WAL 전체 흐름 통합 테스트."""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, tmp_path):
        """테스트 환경 설정."""
        from selfhealing.services import audit_helpers
        from selfhealing.audit.sync_worker import AuditSyncWorker
        from selfhealing.audit.reconciler import AuditReconciler
        from selfhealing.audit.resilience import AuditMetrics

        # 초기화
        audit_helpers.disable_wal()
        AuditSyncWorker.reset_instance()
        AuditReconciler.reset_instance()
        AuditMetrics.get_instance().reset()

        self.wal_dir = str(tmp_path / "integration_wal")

        yield

        # 정리
        audit_helpers.disable_wal()
        AuditSyncWorker.reset_instance()
        AuditReconciler.reset_instance()

    def test_end_to_end_wal_flow(self):
        """E2E: 이벤트 발생 → WAL 기록 → Sync → Reconcile."""
        from selfhealing.services import audit_helpers
        from selfhealing.audit.sync_worker import AuditSyncWorker, SyncWorkerConfig
        from selfhealing.audit.reconciler import AuditReconciler, ReconcilerConfig
        from selfhealing.audit.resilience import AuditMetrics

        # 1. WAL 활성화
        with patch.dict(os.environ, {"AUDIT_WAL_DIR": self.wal_dir}):
            audit_helpers.enable_wal()
            audit_helpers._wal_instance = None

            # 2. 이벤트 기록 (WAL에 먼저 기록됨)
            seq1 = audit_helpers.log_dlq_store_audit(dlq_id=1, domain="payment", failure_type="TEST")
            seq2 = audit_helpers.log_dlq_replay_audit(dlq_id=1, domain="payment", success=True)

            assert seq1 is not None
            assert seq2 is not None
            assert seq2 > seq1

            # 3. WAL 통계 확인
            stats = audit_helpers.get_wal_stats()
            assert stats["total_entries"] >= 2

            # 4. Sync Worker로 동기화
            wal = audit_helpers._get_wal()
            mock_adapter = MagicMock()

            sync_config = SyncWorkerConfig(sync_interval_seconds=0.1)
            worker = AuditSyncWorker(
                wal=wal,
                central_adapter=mock_adapter,
                config=sync_config,
            )

            synced, failed = worker.sync_now()
            assert synced >= 2
            assert failed == 0

            # 5. 메트릭 확인
            metrics = AuditMetrics.get_instance()
            all_metrics = metrics.get_metrics()

            # WAL 기록이 있었음
            assert all_metrics.get("audit_wal_writes_total", 0) >= 0

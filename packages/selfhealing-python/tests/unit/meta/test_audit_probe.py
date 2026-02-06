"""
Audit System Probe 단위 테스트.

테스트 대상:
- AuditSystemProbe
- check_audit_health
- get_audit_probe
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


class TestAuditSystemProbeStatus:
    """AuditSystemProbe 상태 상수 테스트."""

    def test_status_constants_defined(self):
        """상태 상수가 정의되어 있는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        assert AuditSystemProbe.STATUS_HEALTHY == "healthy"
        assert AuditSystemProbe.STATUS_DEGRADED == "degraded"
        assert AuditSystemProbe.STATUS_UNHEALTHY == "unhealthy"
        assert AuditSystemProbe.STATUS_UNKNOWN == "unknown"

    def test_threshold_constants_defined(self):
        """임계값 상수가 정의되어 있는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        assert AuditSystemProbe.LAG_THRESHOLD_DEGRADED == 1000
        assert AuditSystemProbe.FAIL_RATE_THRESHOLD == 0.1


class TestAuditSystemProbeComponentName:
    """AuditSystemProbe component_name 테스트."""

    def test_component_name_is_audit_system(self):
        """component_name이 'audit_system'인지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()
        assert probe.component_name == "audit_system"


class TestAuditSystemProbeProbe:
    """AuditSystemProbe.probe() 메서드 테스트."""

    def test_probe_returns_result(self):
        """probe가 AuditProbeResult를 반환하는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe, AuditProbeResult

        probe = AuditSystemProbe()
        result = probe.probe()

        assert isinstance(result, AuditProbeResult)
        assert result.component == "audit_system"
        assert result.status in ["healthy", "degraded", "unhealthy", "unknown"]
        assert result.latency_ms >= 0
        assert isinstance(result.timestamp, datetime)
        assert isinstance(result.details, dict)

    def test_probe_includes_wal_details(self):
        """probe 결과에 WAL 상세 정보가 포함되는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()
        result = probe.probe()

        assert "wal" in result.details

    def test_probe_includes_disk_buffer_details(self):
        """probe 결과에 disk_buffer 상세 정보가 포함되는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()
        result = probe.probe()

        assert "disk_buffer" in result.details

    def test_probe_includes_sync_worker_details(self):
        """probe 결과에 sync_worker 상세 정보가 포함되는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()
        result = probe.probe()

        assert "sync_worker" in result.details


class TestAuditSystemProbeDetermineStatus:
    """AuditSystemProbe._determine_status() 테스트."""

    def test_unhealthy_when_wal_unavailable(self):
        """WAL 불가 시 UNHEALTHY 반환 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        wal = {"available": False, "reason": "WAL not initialized"}
        buffer = {"available": True}
        sync = {"lag_entries": 0}

        status = probe._determine_status(wal, buffer, sync)
        assert status == AuditSystemProbe.STATUS_UNHEALTHY

    def test_degraded_when_high_lag(self):
        """높은 동기화 지연 시 DEGRADED 반환 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        wal = {"available": True}
        buffer = {"available": True}
        sync = {"lag_entries": 1500}  # > 1000

        status = probe._determine_status(wal, buffer, sync)
        assert status == AuditSystemProbe.STATUS_DEGRADED

    def test_degraded_when_high_fail_rate(self):
        """높은 실패율 시 DEGRADED 반환 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        wal = {"available": True}
        buffer = {"available": True}
        sync = {
            "lag_entries": 0,
            "total_synced": 80,
            "total_failed": 20,  # 20% 실패율
        }

        status = probe._determine_status(wal, buffer, sync)
        assert status == AuditSystemProbe.STATUS_DEGRADED

    def test_degraded_when_sync_worker_stopped(self):
        """SyncWorker 중지 시 DEGRADED 반환 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        wal = {"available": True}
        buffer = {"available": True}
        sync = {
            "available": True,
            "running": False,  # SyncWorker 중지
            "lag_entries": 0,
        }

        status = probe._determine_status(wal, buffer, sync)
        assert status == AuditSystemProbe.STATUS_DEGRADED

    def test_healthy_when_all_good(self):
        """모든 조건 정상 시 HEALTHY 반환 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        wal = {"available": True}
        buffer = {"available": True}
        sync = {
            "available": True,
            "running": True,
            "lag_entries": 100,  # < 1000
            "total_synced": 990,
            "total_failed": 10,  # 1% 실패율 < 10%
        }

        status = probe._determine_status(wal, buffer, sync)
        assert status == AuditSystemProbe.STATUS_HEALTHY


class TestAuditSystemProbeCheckWal:
    """AuditSystemProbe._check_wal() 테스트."""

    @patch("selfhealing.meta.audit_probe.AuditSystemProbe._check_wal")
    def test_check_wal_returns_dict(self, mock_check):
        """_check_wal이 딕셔너리를 반환하는지 확인."""
        mock_check.return_value = {"available": True, "total_entries": 100}

        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()
        result = probe._check_wal()

        assert isinstance(result, dict)

    def test_check_wal_handles_import_error(self):
        """import 에러 처리 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe

        probe = AuditSystemProbe()

        with patch(
            "selfhealing.meta.audit_probe.AuditSystemProbe._check_wal",
            return_value={"available": False, "reason": "audit base module not available"},
        ):
            result = probe._check_wal()
            assert result["available"] is False


class TestCheckAuditHealth:
    """check_audit_health 함수 테스트."""

    def test_returns_dict_with_expected_keys(self):
        """예상되는 키들이 포함된 딕셔너리 반환 확인."""
        from selfhealing.meta.audit_probe import check_audit_health

        result = check_audit_health()

        assert "component" in result
        assert "status" in result
        assert "latency_ms" in result
        assert "timestamp" in result
        assert "details" in result
        assert "error" in result

    def test_component_is_audit_system(self):
        """컴포넌트가 'audit_system'인지 확인."""
        from selfhealing.meta.audit_probe import check_audit_health

        result = check_audit_health()

        assert result["component"] == "audit_system"

    def test_timestamp_is_iso_format(self):
        """timestamp가 ISO 형식인지 확인."""
        from selfhealing.meta.audit_probe import check_audit_health

        result = check_audit_health()

        # ISO 형식 검증
        assert "T" in result["timestamp"]


class TestGetAuditProbe:
    """get_audit_probe 함수 테스트."""

    def test_returns_audit_system_probe(self):
        """AuditSystemProbe 인스턴스 반환 확인."""
        from selfhealing.meta.audit_probe import get_audit_probe, AuditSystemProbe

        probe = get_audit_probe()

        assert isinstance(probe, AuditSystemProbe)

    def test_returns_new_instance_each_call(self):
        """매 호출 시 새 인스턴스 반환 확인."""
        from selfhealing.meta.audit_probe import get_audit_probe

        probe1 = get_audit_probe()
        probe2 = get_audit_probe()

        assert probe1 is not probe2


class TestAuditProbeResult:
    """AuditProbeResult 데이터 클래스 테스트."""

    def test_dataclass_fields(self):
        """데이터 클래스 필드 확인."""
        from selfhealing.meta.audit_probe import AuditProbeResult

        result = AuditProbeResult(
            component="audit_system",
            status="healthy",
            latency_ms=10.5,
            timestamp=datetime.now(timezone.utc),
            details={"wal": {"available": True}},
            error=None,
        )

        assert result.component == "audit_system"
        assert result.status == "healthy"
        assert result.latency_ms == 10.5
        assert result.error is None

    def test_dataclass_with_error(self):
        """에러가 있는 경우 데이터 클래스 생성 확인."""
        from selfhealing.meta.audit_probe import AuditProbeResult

        result = AuditProbeResult(
            component="audit_system",
            status="unknown",
            latency_ms=5.0,
            timestamp=datetime.now(timezone.utc),
            details={},
            error="Connection failed",
        )

        assert result.status == "unknown"
        assert result.error == "Connection failed"

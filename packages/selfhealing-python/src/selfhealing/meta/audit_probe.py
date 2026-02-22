"""
Audit System Health Probe - 감사 시스템 건강 상태 수집.

감사 시스템(WAL, DiskBuffer, SyncWorker)의 건강 상태를
주기적으로 확인하는 프로브.

확인 항목:
1. WAL 쓰기 가능 여부
2. WAL → 중앙 저장소 동기화 지연
3. DiskPersistentBuffer 상태
4. 최근 감사 실패율
"""

from __future__ import annotations

import structlog
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger()


@dataclass
class AuditProbeResult:
    """감사 시스템 프로브 결과."""

    component: str
    status: str  # "healthy", "degraded", "unhealthy", "unknown"
    latency_ms: float
    timestamp: datetime
    details: dict[str, Any]
    error: str | None = None


class AuditSystemProbe:
    """
    감사 시스템 건강 프로브.

    확인 항목:
    1. WAL 쓰기 가능 여부
    2. WAL → 중앙 저장소 동기화 지연
    3. DiskPersistentBuffer 상태
    4. 최근 감사 실패율
    """

    # 상태 상수
    STATUS_HEALTHY = "healthy"
    STATUS_DEGRADED = "degraded"
    STATUS_UNHEALTHY = "unhealthy"
    STATUS_UNKNOWN = "unknown"

    # 임계값 상수
    LAG_THRESHOLD_DEGRADED = 1000  # 1000+ 엔트리 지연 시 DEGRADED
    FAIL_RATE_THRESHOLD = 0.1  # 10% 이상 실패 시 DEGRADED

    @property
    def component_name(self) -> str:
        return "audit_system"

    def probe(self) -> AuditProbeResult:
        """
        감사 시스템 건강 상태 프로브 수행.

        Returns:
            AuditProbeResult: 프로브 결과
        """
        start = time.time()
        details: dict[str, Any] = {}

        try:
            # 1. WAL 상태 확인
            wal_status = self._check_wal()
            details["wal"] = wal_status

            # 2. DiskPersistentBuffer 상태
            buffer_status = self._check_disk_buffer()
            details["disk_buffer"] = buffer_status

            # 3. SyncWorker 지연 확인
            sync_status = self._check_sync_worker()
            details["sync_worker"] = sync_status

            # 상태 결정
            status = self._determine_status(wal_status, buffer_status, sync_status)

            return AuditProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details=details,
            )

        except Exception as e:
            return AuditProbeResult(
                component=self.component_name,
                status=self.STATUS_UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details=details,
                error=str(e),
            )

    def _check_wal(self) -> dict[str, Any]:
        """WAL 상태 확인."""
        try:
            from selfhealing.services.audit.base import get_wal_stats

            stats = get_wal_stats()
            if stats is None:
                return {"available": False, "reason": "WAL not initialized"}

            return {
                "available": True,
                "state": stats.get("state", "unknown"),
                "total_entries": stats.get("total_entries", 0),
                "last_sequence": stats.get("last_sequence", 0),
                "current_size_bytes": stats.get("current_size_bytes", 0),
            }
        except ImportError:
            return {"available": False, "reason": "audit base module not available"}
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _check_disk_buffer(self) -> dict[str, Any]:
        """DiskPersistentBuffer 상태 확인."""
        try:
            from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

            buffer = DiskPersistentBuffer.get_instance()
            stats = buffer.get_stats()
            return {
                "available": True,
                "entry_count": stats.get("entry_count", 0),
                "state": stats.get("state", "unknown"),
            }
        except ImportError:
            return {"available": False, "reason": "DiskPersistentBuffer not installed"}
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _check_sync_worker(self) -> dict[str, Any]:
        """SyncWorker 상태 확인."""
        try:
            from selfhealing.audit.sync_worker import AuditSyncWorker

            worker = AuditSyncWorker.get_instance()

            if not hasattr(worker, "is_running"):
                return {
                    "available": True,
                    "running": True,
                    "lag_entries": 0,
                    "note": "sync_worker running (limited stats)",
                }

            is_running = worker.is_running()
            stats = worker.get_stats() if hasattr(worker, "get_stats") else {}

            return {
                "available": True,
                "running": is_running,
                "lag_entries": (
                    getattr(stats, "current_lag_entries", 0)
                    if hasattr(stats, "current_lag_entries")
                    else stats.get("current_lag_entries", 0)
                ),
                "total_synced": (
                    getattr(stats, "total_synced", 0) if hasattr(stats, "total_synced") else stats.get("total_synced", 0)
                ),
                "total_failed": (
                    getattr(stats, "total_failed", 0) if hasattr(stats, "total_failed") else stats.get("total_failed", 0)
                ),
                "last_error": getattr(stats, "last_error", None) if hasattr(stats, "last_error") else stats.get("last_error"),
            }
        except ImportError:
            return {"available": False, "reason": "sync_worker module not available"}
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _determine_status(
        self,
        wal: dict[str, Any],
        buffer: dict[str, Any],
        sync: dict[str, Any],
    ) -> str:
        """종합 상태 결정."""
        # WAL 불가 → UNHEALTHY
        if not wal.get("available"):
            return self.STATUS_UNHEALTHY

        # 동기화 지연 심각 (LAG_THRESHOLD_DEGRADED+ entries) → DEGRADED
        lag_entries = sync.get("lag_entries", 0)
        if lag_entries > self.LAG_THRESHOLD_DEGRADED:
            return self.STATUS_DEGRADED

        # 최근 실패율 높음 → DEGRADED
        total_synced = sync.get("total_synced", 0)
        total_failed = sync.get("total_failed", 0)
        total = total_synced + total_failed
        if total > 0:
            fail_rate = total_failed / total
            if fail_rate > self.FAIL_RATE_THRESHOLD:
                return self.STATUS_DEGRADED

        # SyncWorker 중지 상태 → DEGRADED
        if sync.get("available") and not sync.get("running", True):
            return self.STATUS_DEGRADED

        return self.STATUS_HEALTHY


def get_audit_probe() -> AuditSystemProbe:
    """AuditSystemProbe 인스턴스 반환."""
    return AuditSystemProbe()


def check_audit_health() -> dict[str, Any]:
    """
    감사 시스템 건강 상태 빠른 확인.

    Returns:
        건강 상태 딕셔너리
    """
    probe = AuditSystemProbe()
    result = probe.probe()
    return {
        "component": result.component,
        "status": result.status,
        "latency_ms": result.latency_ms,
        "timestamp": result.timestamp.isoformat(),
        "details": result.details,
        "error": result.error,
    }

"""
Shadow Logger Module

L2 장애 동안의 상태 변화를 로컬에 기록합니다.
Shadow Log는 L2 복구 후 재동기화 및 Forensic 분석에 활용됩니다.

Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)


@dataclass
class L2SyncFailureRecord:
    """
    L2 동기화 실패 기록.

    L2 장애 동안 발생한 상태 변화를 기록하여
    사후 분석(Forensic) 및 복구 후 재동기화에 활용합니다.

    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
    """

    service_name: str
    intended_state: str
    failure_time: datetime
    error_message: str
    l1_state_at_failure: str
    adapter_type: str = "unknown"
    operation: str = "sync"  # sync, update, delete
    synced_after_recovery: bool = False
    recovery_time: Optional[datetime] = None


class ShadowLogger:
    """
    L2 장애 동안의 상태 변화를 로컬에 기록.

    Shadow Log는 L2가 장애 상태일 때 발생한 모든 상태 변경을
    메모리에 기록하여, L2 복구 후 재동기화 및 Forensic 분석에 활용됩니다.

    Thread-safe 구현으로 동시 접근에 안전합니다.

    Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7
    """

    _instance: Optional["ShadowLogger"] = None
    _lock_class = None

    def __new__(cls) -> "ShadowLogger":
        """Singleton pattern."""
        if cls._instance is None:
            cls._lock_class = threading.Lock()
            with cls._lock_class:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self) -> None:
        """Initialize shadow logger."""
        self._failure_log: List[L2SyncFailureRecord] = []
        self._lock = threading.RLock()
        self._max_entries = 1000  # 기본값, 런타임에 변경 가능

    def set_max_entries(self, max_entries: int) -> None:
        """Set maximum entries to keep."""
        with self._lock:
            self._max_entries = max_entries
            # Trim if over limit
            if len(self._failure_log) > max_entries:
                self._failure_log = self._failure_log[-max_entries:]

    def record_sync_failure(
        self,
        service_name: str,
        intended_state: str,
        error: Exception,
        adapter_type: str = "unknown",
        operation: str = "sync",
    ) -> None:
        """
        L2 동기화 실패 기록.

        Args:
            service_name: 서비스 이름
            intended_state: 동기화하려던 상태
            error: 발생한 예외
            adapter_type: L2 어댑터 타입 (redis, django 등)
            operation: 작업 유형 (sync, update, delete)
        """
        with self._lock:
            record = L2SyncFailureRecord(
                service_name=service_name,
                intended_state=intended_state,
                failure_time=datetime.now(timezone.utc),
                error_message=str(error),
                l1_state_at_failure=intended_state,
                adapter_type=adapter_type,
                operation=operation,
            )
            self._failure_log.append(record)

            # Trim old entries if over limit
            if len(self._failure_log) > self._max_entries:
                self._failure_log = self._failure_log[-self._max_entries:]

            logger.warning(
                f"[ShadowLog] L2 sync failed: service={service_name} "
                f"state={intended_state} adapter={adapter_type} error={error}"
            )

    def get_unsynced_records(self) -> List[L2SyncFailureRecord]:
        """아직 동기화되지 않은 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if not r.synced_after_recovery]

    def get_all_records(self) -> List[L2SyncFailureRecord]:
        """모든 기록 조회."""
        with self._lock:
            return list(self._failure_log)

    def mark_as_synced(self, service_name: str) -> int:
        """
        복구 후 동기화 완료 마킹.

        Args:
            service_name: 서비스 이름

        Returns:
            마킹된 레코드 수
        """
        count = 0
        with self._lock:
            for record in self._failure_log:
                if record.service_name == service_name and not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = datetime.now(timezone.utc)
                    count += 1
        if count > 0:
            logger.info(f"[ShadowLog] Marked {count} records as synced for {service_name}")
        return count

    def mark_all_as_synced(self) -> int:
        """모든 미동기화 레코드를 동기화 완료로 마킹."""
        count = 0
        with self._lock:
            now_time = datetime.now(timezone.utc)
            for record in self._failure_log:
                if not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = now_time
                    count += 1
        if count > 0:
            logger.info(f"[ShadowLog] Marked all {count} records as synced")
        return count

    def get_stats(self) -> Dict[str, Any]:
        """Shadow Log 통계 조회."""
        with self._lock:
            unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
            services = set(r.service_name for r in self._failure_log)
            return {
                "total_records": len(self._failure_log),
                "unsynced_count": len(unsynced),
                "affected_services": list(services),
                "max_entries": self._max_entries,
                "oldest_record": (
                    self._failure_log[0].failure_time.isoformat()
                    if self._failure_log else None
                ),
                "newest_record": (
                    self._failure_log[-1].failure_time.isoformat()
                    if self._failure_log else None
                ),
            }

    def clear(self) -> None:
        """Clear all records (for testing)."""
        with self._lock:
            self._failure_log.clear()

    def analyze_l2_failures(self) -> Dict[str, Any]:
        """
        L2 장애 기간 동안의 상태 변화 분석.

        Forensic Advisor와 연동하여 L2 장애 시 발생한
        상태 변화를 타임라인 형태로 분석합니다.

        Returns:
            분석 결과 딕셔너리

        Reference: docs/self_healing/13_LAYERED_STORAGE_RESILIENCE.md §7.3
        """
        with self._lock:
            unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
            all_records = list(self._failure_log)

        if not all_records:
            return {
                "unsynced_count": 0,
                "affected_services": [],
                "failure_timeline": [],
                "by_adapter": {},
                "by_operation": {},
                "time_range": None,
                "recommendations": ["No L2 failures recorded."],
            }

        # 서비스별 집계
        affected_services = list(set(r.service_name for r in unsynced))

        # 타임라인 생성
        sorted_records = sorted(all_records, key=lambda x: x.failure_time)
        failure_timeline = [
            {
                "service": r.service_name,
                "state": r.intended_state,
                "time": r.failure_time.isoformat(),
                "error": r.error_message,
                "adapter": r.adapter_type,
                "operation": r.operation,
                "synced": r.synced_after_recovery,
            }
            for r in sorted_records
        ]

        # 어댑터별 통계
        by_adapter: Dict[str, int] = {}
        for r in all_records:
            by_adapter[r.adapter_type] = by_adapter.get(r.adapter_type, 0) + 1

        # 작업별 통계
        by_operation: Dict[str, int] = {}
        for r in all_records:
            by_operation[r.operation] = by_operation.get(r.operation, 0) + 1

        # 시간 범위
        time_range = None
        if sorted_records:
            time_range = {
                "start": sorted_records[0].failure_time.isoformat(),
                "end": sorted_records[-1].failure_time.isoformat(),
                "duration_seconds": (
                    sorted_records[-1].failure_time - sorted_records[0].failure_time
                ).total_seconds(),
            }

        # 권장 조치 생성
        recommendations = self._generate_recommendations(
            unsynced_count=len(unsynced),
            affected_services=affected_services,
            by_adapter=by_adapter,
            total_records=len(all_records),
        )

        return {
            "unsynced_count": len(unsynced),
            "affected_services": affected_services,
            "failure_timeline": failure_timeline,
            "by_adapter": by_adapter,
            "by_operation": by_operation,
            "time_range": time_range,
            "recommendations": recommendations,
        }

    def _generate_recommendations(
        self,
        unsynced_count: int,
        affected_services: List[str],
        by_adapter: Dict[str, int],
        total_records: int,
    ) -> List[str]:
        """권장 조치 생성."""
        recommendations = []

        if unsynced_count > 0:
            recommendations.append(
                f"Sync {unsynced_count} unsynced records to L2 using "
                f"POST /api/self-healing/l2-storage/sync/to-l2"
            )

        if len(affected_services) > 3:
            recommendations.append(
                f"Multiple services affected ({len(affected_services)}). "
                f"Consider checking L2 infrastructure health."
            )

        if total_records > 100:
            recommendations.append(
                "High failure count detected. Consider increasing L2 timeout "
                "or optimizing L2 storage performance."
            )

        # 어댑터별 권장사항
        for adapter, count in by_adapter.items():
            if count > 50:
                recommendations.append(
                    f"Adapter '{adapter}' has {count} failures. "
                    f"Check {adapter} connectivity and performance."
                )

        if not recommendations:
            recommendations.append("No critical issues detected.")

        return recommendations

    def get_records_by_service(self, service_name: str) -> List[L2SyncFailureRecord]:
        """특정 서비스의 실패 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if r.service_name == service_name]

    def get_records_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> List[L2SyncFailureRecord]:
        """시간 범위 내 실패 기록 조회."""
        with self._lock:
            return [
                r for r in self._failure_log
                if start_time <= r.failure_time <= end_time
            ]


def get_shadow_logger() -> ShadowLogger:
    """Get the singleton ShadowLogger instance."""
    return ShadowLogger()

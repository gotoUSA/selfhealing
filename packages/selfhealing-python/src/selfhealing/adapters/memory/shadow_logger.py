"""
Shadow Logger Module

L2 장애 동안의 상태 변화를 로컬에 기록합니다.
Shadow Log는 L2 복구 후 재동기화 및 Forensic 분석에 활용됩니다.

Version: 6.4.0 - Drift Detection 메트릭 추가
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog

# Drift Detection 메트릭
try:
    from selfhealing.metrics.drift_metrics import (
        record_shadow_log_recovered,
        record_shadow_log_sync_failure,
        update_shadow_log_affected_services,
        update_shadow_log_oldest_unsynced_age,
        update_shadow_log_unsynced_count,
    )

    HAS_DRIFT_METRICS = True
except ImportError:
    HAS_DRIFT_METRICS = False


logger = structlog.get_logger()


@dataclass
class L2SyncFailureRecord:
    """
    L2 동기화 실패 기록.

    L2 장애 동안 발생한 상태 변화를 기록하여
    사후 분석(Forensic) 및 복구 후 재동기화에 활용합니다.
    """

    service_name: str
    intended_state: str
    failure_time: datetime
    error_message: str
    l1_state_at_failure: str
    adapter_type: str = "unknown"
    operation: str = "sync"  # sync, update, delete
    synced_after_recovery: bool = False
    recovery_time: datetime | None = None


class ShadowLogger:
    """
    L2 장애 동안의 상태 변화를 로컬에 기록.

    Shadow Log는 L2가 장애 상태일 때 발생한 모든 상태 변경을
    메모리에 기록하여, L2 복구 후 재동기화 및 Forensic 분석에 활용됩니다.

    Thread-safe 구현으로 동시 접근에 안전합니다.
    """

    _instance: ShadowLogger | None = None
    _lock_class = None

    def __new__(cls) -> ShadowLogger:
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
        self._failure_log: list[L2SyncFailureRecord] = []
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
                self._failure_log = self._failure_log[-self._max_entries :]

            # Drift Detection 메트릭 기록
            if HAS_DRIFT_METRICS:
                record_shadow_log_sync_failure(adapter_type, operation)
                self._update_drift_metrics()

            # Audit 기록
            self._record_audit_event(
                event_type="SHADOW_LOG_SYNC_FAILED",
                service_name=service_name,
                details={
                    "intended_state": intended_state,
                    "error_message": str(error),
                    "adapter_type": adapter_type,
                    "operation": operation,
                },
            )

            logger.warning(
                "shadow_log.sync_failed",
                service_name=service_name,
                intended_state=intended_state,
                adapter_type=adapter_type,
                error=error,
            )

    def get_unsynced_records(self) -> list[L2SyncFailureRecord]:
        """아직 동기화되지 않은 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if not r.synced_after_recovery]

    def get_all_records(self) -> list[L2SyncFailureRecord]:
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
        recovery_time = datetime.now(timezone.utc)
        with self._lock:
            for record in self._failure_log:
                if record.service_name == service_name and not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = recovery_time
                    count += 1
        if count > 0:
            # Drift Detection 메트릭 기록
            if HAS_DRIFT_METRICS:
                record_shadow_log_recovered(service_name, count)
                with self._lock:
                    self._update_drift_metrics()
            # Audit 기록
            self._record_audit_event(
                event_type="SHADOW_LOG_RECOVERED",
                service_name=service_name,
                details={
                    "recovered_count": count,
                    "recovery_time": recovery_time.isoformat(),
                },
            )
            logger.info(
                "shadow_log.marked_records_synced",
                count=count,
                service_name=service_name,
            )
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
            # Drift Detection 메트릭 업데이트
            if HAS_DRIFT_METRICS and count > 0:
                self._update_drift_metrics()
        if count > 0:
            logger.info(
                "shadow_log.marked_all_records_synced",
                count=count,
            )
        return count

    def _update_drift_metrics(self) -> None:
        """
        Drift Detection 메트릭 업데이트.

        Note: 이 메서드는 _lock이 이미 획득된 상태에서 호출되어야 함.
        """
        if not HAS_DRIFT_METRICS:
            return

        unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
        services = set(r.service_name for r in self._failure_log)

        # 미동기화 레코드 수
        update_shadow_log_unsynced_count(len(unsynced))

        # 영향받은 서비스 수
        update_shadow_log_affected_services(len(services))

        # 가장 오래된 미동기화 레코드 age
        if unsynced:
            oldest = min(r.failure_time for r in unsynced)
            age_seconds = (datetime.now(timezone.utc) - oldest).total_seconds()
            update_shadow_log_oldest_unsynced_age(age_seconds)
        else:
            update_shadow_log_oldest_unsynced_age(0)

    def get_stats(self) -> dict[str, Any]:
        """Shadow Log 통계 조회."""
        with self._lock:
            unsynced = [r for r in self._failure_log if not r.synced_after_recovery]
            services = set(r.service_name for r in self._failure_log)

            # Drift Detection 메트릭 업데이트
            if HAS_DRIFT_METRICS:
                self._update_drift_metrics()

            return {
                "total_records": len(self._failure_log),
                "unsynced_count": len(unsynced),
                "affected_services": list(services),
                "max_entries": self._max_entries,
                "oldest_record": (self._failure_log[0].failure_time.isoformat() if self._failure_log else None),
                "newest_record": (self._failure_log[-1].failure_time.isoformat() if self._failure_log else None),
            }

    def clear(self) -> None:
        """Clear all records (for testing)."""
        with self._lock:
            self._failure_log.clear()
            self._max_entries = 1000

    def analyze_l2_failures(self) -> dict[str, Any]:
        """
        L2 장애 기간 동안의 상태 변화 분석.

        Forensic Advisor와 연동하여 L2 장애 시 발생한
        상태 변화를 타임라인 형태로 분석합니다.

        Returns:
            분석 결과 딕셔너리
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
        by_adapter: dict[str, int] = {}
        for r in all_records:
            by_adapter[r.adapter_type] = by_adapter.get(r.adapter_type, 0) + 1

        # 작업별 통계
        by_operation: dict[str, int] = {}
        for r in all_records:
            by_operation[r.operation] = by_operation.get(r.operation, 0) + 1

        # 시간 범위
        time_range = None
        if sorted_records:
            time_range = {
                "start": sorted_records[0].failure_time.isoformat(),
                "end": sorted_records[-1].failure_time.isoformat(),
                "duration_seconds": (sorted_records[-1].failure_time - sorted_records[0].failure_time).total_seconds(),
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
        affected_services: list[str],
        by_adapter: dict[str, int],
        total_records: int,
    ) -> list[str]:
        """권장 조치 생성."""
        recommendations = []

        if unsynced_count > 0:
            recommendations.append(
                f"Sync {unsynced_count} unsynced records to L2 using " f"POST /api/self-healing/l2-storage/sync/to-l2"
            )

        if len(affected_services) > 3:
            recommendations.append(
                f"Multiple services affected ({len(affected_services)}). " f"Consider checking L2 infrastructure health."
            )

        if total_records > 100:
            recommendations.append(
                "High failure count detected. Consider increasing L2 timeout " "or optimizing L2 storage performance."
            )

        # 어댑터별 권장사항
        for adapter, count in by_adapter.items():
            if count > 50:
                recommendations.append(
                    f"Adapter '{adapter}' has {count} failures. " f"Check {adapter} connectivity and performance."
                )

        if not recommendations:
            recommendations.append("No critical issues detected.")

        return recommendations

    def get_records_by_service(self, service_name: str) -> list[L2SyncFailureRecord]:
        """특정 서비스의 실패 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if r.service_name == service_name]

    def get_records_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> list[L2SyncFailureRecord]:
        """시간 범위 내 실패 기록 조회."""
        with self._lock:
            return [r for r in self._failure_log if start_time <= r.failure_time <= end_time]

    def _record_audit_event(
        self,
        event_type: str,
        service_name: str,
        details: dict[str, Any],
    ) -> None:
        """
        Audit 이벤트 기록.

        Audit 통합 개선:
        - _write_to_wal() 직접 호출로 ActorContext/TraceContext 자동 결합
        - "L2 장애 중 어떤 운영자의 어떤 작업에서 동기화 실패 발생" 추적 가능
        """
        try:
            from selfhealing.services.audit.base import _write_to_wal

            _write_to_wal(
                event_type=event_type,
                source="ShadowLogger",
                details={
                    "service_name": service_name,
                    **details,
                },
            )
            # 자동으로 actor_id, actor_roles, trace_id가 포함됨
        except ImportError:
            # _write_to_wal 미사용 환경: 로거로 폴백
            logger.debug("shadow_logger.audit_recording_skipped_available")
        except Exception as e:
            # Audit 실패가 메인 로직을 방해하면 안됨
            logger.debug(
                "shadow_logger.audit_recording_failed",
                error=e,
            )


def get_shadow_logger() -> ShadowLogger:
    """Get the singleton ShadowLogger instance."""
    return ShadowLogger()

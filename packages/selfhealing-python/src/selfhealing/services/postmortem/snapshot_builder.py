"""
Snapshot Builder for Postmortem Timeline.

Postmortem 생성 시 타임라인 스냅샷을 구축합니다.
CB OPEN 시점과 CLOSED 시점의 메트릭, Prometheus 피크 값, 에러 로그 등을 수집하여
완전한 인시던트 분석 자료를 제공합니다.

Features:
- CB OPEN 시점 스냅샷 (Redis에서 조회)
- CLOSED 시점 스냅샷 수집
- Prometheus 피크 메트릭 쿼리
- 에러 로그 수집
- Grafana/Prometheus 대시보드 링크 생성
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = structlog.get_logger()


@dataclass
class TimelineSnapshot:
    """타임라인 스냅샷 데이터."""

    events: list[dict[str, Any]] = field(default_factory=list)
    metrics_at_open: dict[str, Any] = field(default_factory=dict)
    metrics_at_close: dict[str, Any] = field(default_factory=dict)
    peak_metrics: dict[str, Any] = field(default_factory=dict)
    captured_logs: list[dict[str, Any]] = field(default_factory=list)
    dashboard_links: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "events": self.events,
            "metrics_at_open": self.metrics_at_open,
            "metrics_at_close": self.metrics_at_close,
            "peak_metrics": self.peak_metrics,
            "captured_logs": self.captured_logs,
            "dashboard_links": self.dashboard_links,
        }


class SnapshotBuilder:
    """
    Postmortem 타임라인 스냅샷을 구축하는 빌더.

    빌드 순서:
    1. Redis에서 OPEN 스냅샷 조회 (실패 시 빈 dict)
    2. CLOSED 시점 스냅샷 수집 (필수)
    3. Prometheus 피크 쿼리 (실패 시 건너뛰기)
    4. 에러 로그 수집 (실패 시 빈 리스트)
    5. 대시보드 링크 생성 (실패 시 건너뛰기)
    """

    # Redis 키 패턴
    OPEN_SNAPSHOT_KEY_PATTERN = "selfhealing:cb_snapshot:open:{service}"
    OPEN_SNAPSHOT_TTL = 1800  # 30분

    def __init__(
        self,
        service_name: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ):
        """
        SnapshotBuilder 초기화.

        Args:
            service_name: 서비스 이름
            start_time: 인시던트 시작 시각 (CB OPEN 시점)
            end_time: 인시던트 종료 시각 (CB CLOSED 시점)
        """
        self._service_name = service_name
        self._start_time = start_time
        self._end_time = end_time or datetime.now(timezone.utc)
        self._snapshot = TimelineSnapshot()

    def _get_redis_client(self):
        """Redis 클라이언트 반환."""
        try:
            from selfhealing.adapters.redis import get_redis_client

            return get_redis_client()
        except ImportError:
            return None

    def _get_open_snapshot(self) -> dict[str, Any]:
        """Redis에서 CB OPEN 시점 스냅샷 조회."""
        try:
            redis_client = self._get_redis_client()
            if not redis_client:
                logger.debug("snapshot_builder.redis_client_available")
                return {}

            key = self.OPEN_SNAPSHOT_KEY_PATTERN.format(service=self._service_name)
            data = redis_client.hgetall(key)

            if not data:
                logger.debug(
                    "snapshot_builder.no_open_snapshot_found",
                    self=self._service_name,
                )
                return {}

            # Redis HASH에서 조회 후 타입 변환
            snapshot = {}
            for field_name, value in data.items():
                field_key = field_name.decode() if isinstance(field_name, bytes) else field_name
                field_value = value.decode() if isinstance(value, bytes) else value
                # 숫자 변환 시도
                try:
                    if "." in str(field_value):
                        snapshot[field_key] = float(field_value)
                    else:
                        snapshot[field_key] = int(field_value)
                except ValueError:
                    snapshot[field_key] = field_value

            logger.debug(
                "snapshot_builder.retrieved_open_snapshot",
                self=self._service_name,
            )
            return snapshot

        except Exception as e:
            logger.warning(
                "snapshot_builder.failed_get_open_snapshot",
                error=e,
            )
            return {}

    def _collect_close_snapshot(self) -> dict[str, Any]:
        """CLOSED 시점 시스템 스냅샷 수집."""
        try:
            from selfhealing.api.django.views.xtest.base import collect_system_snapshot

            snapshot = collect_system_snapshot()

            # 추가 정보
            snapshot["captured_at"] = "close"
            snapshot["service"] = self._service_name

            # CB 상태 정보 추가
            try:
                from selfhealing.services.circuit_breaker_service import (
                    get_circuit_breaker_service,
                )

                cb_service = get_circuit_breaker_service()
                cb_states = {}
                for name in cb_service.get_all_services():
                    status = cb_service.get_status(name)
                    cb_states[name] = status.get("state", "UNKNOWN") if status else "UNKNOWN"
                snapshot["cb_states"] = cb_states
            except Exception as e:
                logger.debug(
                    "snapshot_builder.failed_get_cb_states",
                    error=e,
                )
                snapshot["cb_states"] = {}

            return snapshot

        except ImportError:
            logger.warning("snapshot_builder.available")
            return {"timestamp": datetime.now(timezone.utc).isoformat(), "error": "snapshot_unavailable"}
        except Exception as e:
            logger.error(
                "snapshot_builder.failed_collect_close_snapshot",
                error=e,
            )
            return {"timestamp": datetime.now(timezone.utc).isoformat(), "error": str(e)}

    def _query_prometheus_peaks(self) -> dict[str, Any]:
        """Prometheus에서 피크 메트릭 조회."""
        try:
            from selfhealing.services.postmortem.prometheus_collector import (
                get_prometheus_collector,
            )

            if not self._start_time:
                logger.debug("snapshot_builder.no_skipping_prometheus_query")
                return {}

            collector = get_prometheus_collector()
            if not collector.is_enabled():
                logger.debug("snapshot_builder.prometheus_query_disabled")
                return {}

            peak = collector.get_peak_metrics(self._start_time, self._end_time)

            result = {}
            if peak.max_cpu_percent is not None:
                result["max_cpu_percent"] = peak.max_cpu_percent
            if peak.max_memory_percent is not None:
                result["max_memory_mb"] = peak.max_memory_percent
            if peak.max_error_rate_percent is not None:
                result["max_error_rate_percent"] = peak.max_error_rate_percent
            if peak.max_latency_p99_seconds is not None:
                result["max_latency_p99_seconds"] = peak.max_latency_p99_seconds
            if peak.query_error:
                result["query_error"] = peak.query_error

            return result

        except ImportError:
            logger.debug("snapshot_builder.prometheuscollector_available")
            return {}
        except Exception as e:
            logger.warning(
                "snapshot_builder.prometheus_query_failed",
                error=e,
            )
            return {"query_error": str(e)}

    def _collect_error_logs(self) -> list[dict[str, Any]]:
        """에러 로그 수집."""
        try:
            from selfhealing.services.postmortem.log_buffer import (
                get_incident_log_buffer,
            )

            log_buffer = get_incident_log_buffer()
            if not log_buffer.is_enabled():
                return []

            if self._start_time:
                logs = log_buffer.get_logs_for_period(self._start_time, self._end_time)
            else:
                logs = log_buffer.get_recent_logs()

            return logs

        except ImportError:
            logger.debug("snapshot_builder.incidentlogbuffer_available")
            return []
        except Exception as e:
            logger.warning(
                "snapshot_builder.failed_collect_error_logs",
                error=e,
            )
            return []

    def _generate_dashboard_links(self) -> dict[str, str]:
        """대시보드 링크 생성."""
        try:
            from selfhealing.services.postmortem.prometheus_collector import (
                get_prometheus_collector,
            )

            if not self._start_time:
                return {}

            collector = get_prometheus_collector()
            links = collector.generate_dashboard_links(
                start=self._start_time,
                end=self._end_time,
                service=self._service_name,
            )

            return links

        except ImportError:
            logger.debug("snapshot_builder.prometheuscollector_available")
            return {}
        except Exception as e:
            logger.warning(
                "snapshot_builder.failed_generate_dashboard_links",
                error=e,
            )
            return {}

    def build(self, timeline_events: list[dict[str, Any]] | None = None) -> TimelineSnapshot:
        """
        타임라인 스냅샷 빌드.

        Args:
            timeline_events: 기존 타임라인 이벤트 목록

        Returns:
            완성된 TimelineSnapshot
        """
        # 1. 타임라인 이벤트
        if timeline_events:
            self._snapshot.events = timeline_events

        # 2. OPEN 스냅샷 조회 (Redis)
        self._snapshot.metrics_at_open = self._get_open_snapshot()

        # 3. CLOSED 스냅샷 수집 (필수)
        self._snapshot.metrics_at_close = self._collect_close_snapshot()

        # 4. Prometheus 피크 쿼리 (선택)
        self._snapshot.peak_metrics = self._query_prometheus_peaks()

        # 5. 에러 로그 수집 (선택)
        self._snapshot.captured_logs = self._collect_error_logs()

        # 6. 대시보드 링크 생성 (선택)
        self._snapshot.dashboard_links = self._generate_dashboard_links()

        logger.info(
            f"[SnapshotBuilder] Snapshot built for {self._service_name}: "
            f"open_metrics={bool(self._snapshot.metrics_at_open)}, "
            f"peak_metrics={bool(self._snapshot.peak_metrics)}, "
            f"logs={len(self._snapshot.captured_logs)}"
        )

        return self._snapshot

    def build_dict(self, timeline_events: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """딕셔너리 형태로 스냅샷 빌드."""
        snapshot = self.build(timeline_events)
        return snapshot.to_dict()


def save_open_snapshot_to_redis(
    service_name: str,
    snapshot_data: dict[str, Any],
) -> bool:
    """
    CB OPEN 시점 스냅샷을 Redis에 저장.

    CB OPEN 이벤트 핸들러에서 호출하여 스냅샷을 임시 저장합니다.
    TTL은 30분으로 설정되어 자동으로 만료됩니다.

    Args:
        service_name: 서비스 이름
        snapshot_data: 스냅샷 데이터

    Returns:
        저장 성공 여부
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            logger.warning("snapshot_builder.redis_client_available")
            return False

        key = SnapshotBuilder.OPEN_SNAPSHOT_KEY_PATTERN.format(service=service_name)

        # HASH로 저장 (각 필드를 개별 저장)
        for field_name, value in snapshot_data.items():
            redis_client.hset(key, field_name, str(value))

        # TTL 설정
        redis_client.expire(key, SnapshotBuilder.OPEN_SNAPSHOT_TTL)

        logger.debug(
            "snapshot_builder.saved_open_snapshot",
            service_name=service_name,
        )
        return True

    except Exception as e:
        logger.warning(
            "snapshot_builder.failed_save_open_snapshot",
            error=e,
        )
        return False


def delete_open_snapshot_from_redis(service_name: str) -> bool:
    """
    CB OPEN 스냅샷을 Redis에서 삭제.

    Postmortem 생성 후 정리용으로 사용합니다.

    Args:
        service_name: 서비스 이름

    Returns:
        삭제 성공 여부
    """
    try:
        redis_client = get_redis_client()
        if not redis_client:
            return False

        key = SnapshotBuilder.OPEN_SNAPSHOT_KEY_PATTERN.format(service=service_name)
        redis_client.delete(key)

        logger.debug(
            "snapshot_builder.deleted_open_snapshot",
            service_name=service_name,
        )
        return True

    except Exception as e:
        logger.warning(
            "snapshot_builder.failed_delete_open_snapshot",
            error=e,
        )
        return False


def get_redis_client():
    """Redis 클라이언트를 가져옵니다. 모킹 용이성을 위한 래퍼."""
    try:
        from selfhealing.adapters.redis import get_redis_client as _get_redis_client

        return _get_redis_client()
    except ImportError:
        return None


# =============================================================================
# Module Exports
# =============================================================================

__all__ = [
    "SnapshotBuilder",
    "TimelineSnapshot",
    "save_open_snapshot_to_redis",
    "delete_open_snapshot_from_redis",
]

"""
DiskPersistentBuffer Prometheus 메트릭.

버퍼 상태, 성능, 에러를 모니터링하기 위한 메트릭 정의.

사용법:
    from selfhealing.audit.persistence.disk_buffer_metrics import (
        update_disk_buffer_metrics,
    )

    buffer = DiskPersistentBuffer()
    update_disk_buffer_metrics(buffer, instance="default")
"""

from __future__ import annotations

import structlog
import shutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

logger = structlog.get_logger()

METRICS_AVAILABLE = False

try:
    from prometheus_client import Counter, Gauge, Histogram

    METRICS_AVAILABLE = True

    # ──────────────────────────────────────────────────────
    # Gauge 메트릭 (현재 상태)
    # ──────────────────────────────────────────────────────

    disk_buffer_entries = Gauge(
        "selfhealing_disk_buffer_entries",
        "Current number of entries in disk buffer",
        ["instance"],
    )

    disk_buffer_size_bytes = Gauge(
        "selfhealing_disk_buffer_size_bytes",
        "Current size of disk buffer in bytes",
        ["instance"],
    )

    disk_buffer_state = Gauge(
        "selfhealing_disk_buffer_state",
        "Current state of disk buffer " "(0=UNINITIALIZED, 1=ACTIVE, 2=DISK_FULL_FAILOPEN, 3=CORRUPTED, 4=CLOSED)",
        ["instance"],
    )

    disk_buffer_dead_letters = Gauge(
        "selfhealing_disk_buffer_dead_letters",
        "Current number of entries in dead letter DB",
        ["instance"],
    )

    disk_buffer_disk_free_ratio = Gauge(
        "selfhealing_disk_buffer_disk_free_ratio",
        "Disk free space ratio (0.0-1.0)",
        ["instance"],
    )

    disk_buffer_sequence = Gauge(
        "selfhealing_disk_buffer_sequence",
        "Current sequence number",
        ["instance"],
    )

    # ──────────────────────────────────────────────────────
    # Counter 메트릭 (누적)
    # ──────────────────────────────────────────────────────

    disk_buffer_puts_total = Counter(
        "selfhealing_disk_buffer_puts_total",
        "Total number of put operations",
        ["instance"],
    )

    disk_buffer_gets_total = Counter(
        "selfhealing_disk_buffer_gets_total",
        "Total number of get operations",
        ["instance"],
    )

    disk_buffer_deletes_total = Counter(
        "selfhealing_disk_buffer_deletes_total",
        "Total number of delete operations",
        ["instance"],
    )

    disk_buffer_flushes_total = Counter(
        "selfhealing_disk_buffer_flushes_total",
        "Total number of flush operations",
        ["instance"],
    )

    disk_buffer_checksum_errors_total = Counter(
        "selfhealing_disk_buffer_checksum_errors_total",
        "Total number of checksum errors detected",
        ["instance"],
    )

    disk_buffer_disk_full_events_total = Counter(
        "selfhealing_disk_buffer_disk_full_events_total",
        "Total number of disk full events",
        ["instance"],
    )

    disk_buffer_dead_letter_moves_total = Counter(
        "selfhealing_disk_buffer_dead_letter_moves_total",
        "Total number of entries moved to dead letter DB",
        ["instance"],
    )

    disk_buffer_group_commit_flushes_total = Counter(
        "selfhealing_disk_buffer_group_commit_flushes_total",
        "Total number of group commit flushes",
        ["instance"],
    )

    disk_buffer_quarantine_events_total = Counter(
        "selfhealing_disk_buffer_quarantine_events_total",
        "Total number of DB quarantine events",
        ["instance"],
    )

    # ──────────────────────────────────────────────────────
    # Histogram 메트릭 (분포)
    # ──────────────────────────────────────────────────────

    disk_buffer_put_latency = Histogram(
        "selfhealing_disk_buffer_put_latency_seconds",
        "Latency of put operations",
        ["instance"],
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
    )

    disk_buffer_flush_latency = Histogram(
        "selfhealing_disk_buffer_flush_latency_seconds",
        "Latency of flush operations",
        ["instance"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )

except ImportError:
    logger.debug("disk_buffer_metrics.available")


# 이전 카운터 값 저장 (증분 계산용)
_previous_counters: dict[str, dict[str, int]] = {}


def update_disk_buffer_metrics(
    buffer: DiskPersistentBuffer,
    instance: str = "default",
) -> None:
    """
    DiskPersistentBuffer 메트릭 업데이트.

    주기적으로 호출하여 Prometheus에 최신 상태를 노출합니다.

    Args:
        buffer: DiskPersistentBuffer 인스턴스
        instance: 인스턴스 레이블
    """
    if not METRICS_AVAILABLE:
        return

    try:
        stats = buffer.get_stats()

        # Gauge 업데이트
        disk_buffer_entries.labels(instance=instance).set(stats.get("count", 0))
        disk_buffer_size_bytes.labels(instance=instance).set(stats.get("db_size_bytes", 0))
        disk_buffer_state.labels(instance=instance).set(buffer.state.value)
        disk_buffer_sequence.labels(instance=instance).set(stats.get("sequence", 0))

        # Dead Letter 수 (활성화된 경우)
        if buffer._settings.enable_dead_letter_db:
            try:
                dl_count = len(buffer.get_dead_letters(limit=10000))
                disk_buffer_dead_letters.labels(instance=instance).set(dl_count)
            except Exception:
                pass

        # 디스크 여유 공간
        try:
            usage = shutil.disk_usage(buffer._settings.data_path)
            free_ratio = usage.free / usage.total
            disk_buffer_disk_free_ratio.labels(instance=instance).set(free_ratio)
        except Exception:
            pass

        # Counter 증분 업데이트
        _update_counter_increments(stats, instance)

    except Exception as e:
        logger.debug(
            "disk_buffer_metrics.update_failed",
            error=e,
        )


def _update_counter_increments(stats: dict[str, Any], instance: str) -> None:
    """Counter 메트릭 증분 업데이트."""
    global _previous_counters

    if instance not in _previous_counters:
        _previous_counters[instance] = {}

    prev = _previous_counters[instance]

    # 각 카운터에 대해 증분 계산 후 inc() 호출
    counter_mappings = [
        ("total_puts", disk_buffer_puts_total),
        ("total_gets", disk_buffer_gets_total),
        ("total_deletes", disk_buffer_deletes_total),
        ("checksum_errors", disk_buffer_checksum_errors_total),
        ("disk_full_events", disk_buffer_disk_full_events_total),
        ("dead_letter_moves", disk_buffer_dead_letter_moves_total),
        ("group_commit_flushes", disk_buffer_group_commit_flushes_total),
        ("quarantine_events", disk_buffer_quarantine_events_total),
    ]

    for stat_key, counter in counter_mappings:
        current_value = stats.get(stat_key, 0)
        prev_value = prev.get(stat_key, 0)

        if current_value > prev_value:
            increment = current_value - prev_value
            counter.labels(instance=instance).inc(increment)

        prev[stat_key] = current_value


def record_put_latency(instance: str, latency_seconds: float) -> None:
    """put 작업 지연 시간 기록."""
    if METRICS_AVAILABLE:
        disk_buffer_put_latency.labels(instance=instance).observe(latency_seconds)


def record_flush_latency(instance: str, latency_seconds: float) -> None:
    """flush 작업 지연 시간 기록."""
    if METRICS_AVAILABLE:
        disk_buffer_flush_latency.labels(instance=instance).observe(latency_seconds)


def get_metrics_status() -> dict[str, Any]:
    """
    메트릭 모듈 상태 반환.

    Returns:
        {
            "available": bool,
            "metrics_count": int,
        }
    """
    return {
        "available": METRICS_AVAILABLE,
        "metrics_count": 15 if METRICS_AVAILABLE else 0,
    }

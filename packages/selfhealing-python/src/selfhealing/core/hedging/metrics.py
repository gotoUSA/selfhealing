"""
Hedging Prometheus Metrics - 헷징 전략 메트릭.

헷징 실행 횟수, 성공/실패 횟수, 지연시간, 헷징 이득 등의
Prometheus 메트릭을 정의합니다.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger()

# Prometheus 메트릭 (선택적 의존성)
try:
    from prometheus_client import Counter, Histogram

    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False
    Counter = None
    Histogram = None


# =============================================================================
# 메트릭 정의
# =============================================================================

if _HAS_PROMETHEUS:
    # 총 헷징 실행 수
    HEDGING_TOTAL = Counter(
        "selfhealing_hedging_total",
        "Total number of hedging executions",
        ["mode"],  # immediate, delayed, adaptive
    )

    # 헷징 성공 수 (소스별)
    HEDGING_SUCCESS_TOTAL = Counter(
        "selfhealing_hedging_success_total",
        "Number of successful hedging executions by source",
        ["source"],  # primary, secondary, candidate_N, fallback
    )

    # 헷징 실패 수 (모든 후보 실패)
    HEDGING_FAILED_TOTAL = Counter(
        "selfhealing_hedging_failed_total",
        "Number of hedging executions where all candidates failed",
        [],
    )

    # 헷징 타임아웃 수
    HEDGING_TIMEOUT_TOTAL = Counter(
        "selfhealing_hedging_timeout_total",
        "Number of hedging executions that timed out",
        [],
    )

    # 확정적 에러로 인한 즉시 실패 수
    HEDGING_NON_RETRYABLE_TOTAL = Counter(
        "selfhealing_hedging_non_retryable_total",
        "Number of hedging executions aborted due to non-retryable errors",
        [],
    )

    # 헷징으로 Secondary가 선택된 횟수
    HEDGING_HEDGED_TOTAL = Counter(
        "selfhealing_hedging_hedged_total",
        "Number of times hedging selected a non-primary candidate",
        [],
    )

    # 부하로 인한 헷징 비활성화 횟수
    HEDGING_DISABLED_DUE_TO_LOAD = Counter(
        "selfhealing_hedging_disabled_due_to_load_total",
        "Number of times hedging was disabled due to high load",
        ["load_level"],  # high, critical
    )

    # 헷징 응답 지연시간 (소스별)
    HEDGING_LATENCY_SECONDS = Histogram(
        "selfhealing_hedging_latency_seconds",
        "Hedging response latency in seconds",
        ["source"],
        buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
    )

    # 헷징으로 인한 지연시간 개선 (밀리초)
    HEDGING_BENEFIT_MS = Histogram(
        "selfhealing_hedging_benefit_ms",
        "Latency improvement achieved by hedging in milliseconds",
        [],
        buckets=[10, 50, 100, 250, 500, 1000, 2500],
    )

    # 후보별 실행 횟수
    HEDGING_CANDIDATE_TRIED = Counter(
        "selfhealing_hedging_candidate_tried_total",
        "Number of times each candidate was tried",
        ["candidate"],
    )

    # 결과 불일치 감지 횟수
    HEDGING_MISMATCH_TOTAL = Counter(
        "selfhealing_hedging_mismatch_total",
        "Number of result mismatches detected between candidates",
        ["mismatch_type"],  # value, type, structure
    )

else:
    # Prometheus 미설치 시 더미 객체
    HEDGING_TOTAL = None
    HEDGING_SUCCESS_TOTAL = None
    HEDGING_FAILED_TOTAL = None
    HEDGING_TIMEOUT_TOTAL = None
    HEDGING_NON_RETRYABLE_TOTAL = None
    HEDGING_HEDGED_TOTAL = None
    HEDGING_DISABLED_DUE_TO_LOAD = None
    HEDGING_LATENCY_SECONDS = None
    HEDGING_BENEFIT_MS = None
    HEDGING_CANDIDATE_TRIED = None
    HEDGING_MISMATCH_TOTAL = None


# =============================================================================
# 메트릭 기록 함수
# =============================================================================


def record_hedging_execution(mode: str) -> None:
    """
    헷징 실행 기록.

    Args:
        mode: 헷징 모드 (immediate, delayed, adaptive)
    """
    if HEDGING_TOTAL is not None:
        HEDGING_TOTAL.labels(mode=mode).inc()


def record_hedging_success(source: str, latency_seconds: float) -> None:
    """
    헷징 성공 기록.

    Args:
        source: 성공한 후보 이름
        latency_seconds: 응답 지연시간 (초)
    """
    if HEDGING_SUCCESS_TOTAL is not None:
        HEDGING_SUCCESS_TOTAL.labels(source=source).inc()
    if HEDGING_LATENCY_SECONDS is not None:
        HEDGING_LATENCY_SECONDS.labels(source=source).observe(latency_seconds)


def record_hedging_failure() -> None:
    """모든 후보 실패 기록."""
    if HEDGING_FAILED_TOTAL is not None:
        HEDGING_FAILED_TOTAL.inc()


def record_hedging_timeout() -> None:
    """헷징 타임아웃 기록."""
    if HEDGING_TIMEOUT_TOTAL is not None:
        HEDGING_TIMEOUT_TOTAL.inc()


def record_hedging_non_retryable() -> None:
    """확정적 에러로 인한 즉시 실패 기록."""
    if HEDGING_NON_RETRYABLE_TOTAL is not None:
        HEDGING_NON_RETRYABLE_TOTAL.inc()


def record_hedging_hedged() -> None:
    """헷징으로 Secondary가 선택됨을 기록."""
    if HEDGING_HEDGED_TOTAL is not None:
        HEDGING_HEDGED_TOTAL.inc()


def record_hedging_disabled(load_level: str) -> None:
    """
    부하로 인한 헷징 비활성화 기록.

    Args:
        load_level: 부하 레벨 (high, critical)
    """
    if HEDGING_DISABLED_DUE_TO_LOAD is not None:
        HEDGING_DISABLED_DUE_TO_LOAD.labels(load_level=load_level).inc()


def record_hedging_benefit(benefit_ms: float) -> None:
    """
    헷징으로 인한 지연시간 개선 기록.

    Args:
        benefit_ms: 개선된 밀리초
    """
    if HEDGING_BENEFIT_MS is not None and benefit_ms > 0:
        HEDGING_BENEFIT_MS.observe(benefit_ms)


def record_candidate_tried(candidate: str) -> None:
    """
    후보 실행 기록.

    Args:
        candidate: 후보 이름
    """
    if HEDGING_CANDIDATE_TRIED is not None:
        HEDGING_CANDIDATE_TRIED.labels(candidate=candidate).inc()


def record_result_mismatch(mismatch_type: str) -> None:
    """
    결과 불일치 기록.

    Args:
        mismatch_type: 불일치 유형 (value, type, structure)
    """
    if HEDGING_MISMATCH_TOTAL is not None:
        HEDGING_MISMATCH_TOTAL.labels(mismatch_type=mismatch_type).inc()

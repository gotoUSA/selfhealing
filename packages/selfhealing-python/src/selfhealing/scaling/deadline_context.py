"""
Deadline Context — gRPC Deadline Propagation 패턴.

상위 서비스의 deadline을 하위 서비스에 ContextVar + HTTP 헤더로 전파한다.
남은 시간이 예상 처리시간 미만이면 즉시 거절(Fast-Fail)하여
무의미한 작업을 방지한다.

MSA 호출 체인 A → B → C 에서:
- A가 3초 타임아웃으로 B를 호출
- B가 2.5초 소요 후 C를 호출
- C는 남은 시간 0.5초인데 예상 처리시간 2초 → Fast-Fail로 자원 절약

ContextVar 기반 전파:
- WSGI gthread 환경에서 스레드별 독립 컨텍스트 보장
- ThreadPool(Bulkhead, Hedging)에서 copy_context().run()으로 자동 전파
- Celery Task에는 전파하지 않음 (독립 라이프사이클)

HTTP 헤더 규약:
- X-Deadline-Remaining: 2500ms (밀리초 단위)
- 외부 진입점(Nginx)에서 클라이언트 헤더 제거 (DoS 방지)
"""

from __future__ import annotations

import logging
import os
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Generator

logger = logging.getLogger(__name__)

# HTTP 헤더 이름
DEADLINE_HEADER = "X-Deadline-Remaining"

# Django META 키 (HTTP_X_DEADLINE_REMAINING)
DEADLINE_META_KEY = "HTTP_X_DEADLINE_REMAINING"

# ContextVar: 요청 deadline (monotonic clock 기준 절대 시각)
_request_deadline: ContextVar[float | None] = ContextVar("request_deadline", default=None)

# 최소 유효 시간 (ms) — 이보다 적으면 Fast-Fail
DEFAULT_MINIMUM_USEFUL_TIME_MS: float = float(os.environ.get("SELFHEALING_DEADLINE_MINIMUM_USEFUL_MS", "50"))

# 네트워크 레이턴시 보정 버퍼 (ms)
# 같은 AZ 내 Pod 간 1~5ms, Cross-AZ 10~30ms, 안전 마진 2× Cross-AZ = 50ms
DEFAULT_NETWORK_LATENCY_BUFFER_MS: float = float(os.environ.get("SELFHEALING_DEADLINE_NETWORK_BUFFER_MS", "50"))

# 헤더 파싱용 정규식: "2500ms", "2500", "1500.5ms" 등
_DEADLINE_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:ms)?\s*$", re.IGNORECASE)


def parse_deadline_header(header_value: str) -> float | None:
    """
    X-Deadline-Remaining 헤더 값 파싱.

    Args:
        header_value: 헤더 값 (예: "2500ms", "2500", "1500.5ms")

    Returns:
        남은 시간(ms) 또는 파싱 실패 시 None
    """
    if not header_value:
        return None

    match = _DEADLINE_PATTERN.match(header_value)
    if match:
        return float(match.group(1))

    logger.debug("[DeadlineContext] Failed to parse header: %s", header_value)
    return None


def set_deadline(remaining_ms: float) -> None:
    """
    현재 컨텍스트에 deadline 설정.
    네트워크 레이턴시 Buffer를 차감하여 보수적으로 계산한다.

    Args:
        remaining_ms: 남은 시간 (밀리초)
    """
    adjusted = remaining_ms - DEFAULT_NETWORK_LATENCY_BUFFER_MS

    if adjusted <= 0:
        logger.warning(
            "[DeadlineContext] Deadline exhausted on arrival: "
            "remaining=%.0fms, buffer=%.0fms — possible network congestion",
            remaining_ms,
            DEFAULT_NETWORK_LATENCY_BUFFER_MS,
        )
        adjusted = 0

    deadline = time.monotonic() + (adjusted / 1000.0)
    _request_deadline.set(deadline)


def get_remaining_ms() -> float | None:
    """
    현재 컨텍스트의 남은 시간 반환.

    Returns:
        남은 시간(ms) 또는 deadline 미설정 시 None
    """
    deadline = _request_deadline.get()
    if deadline is None:
        return None
    remaining = (deadline - time.monotonic()) * 1000.0
    return max(0.0, remaining)


def is_expired() -> bool:
    """
    deadline이 만료되었는지 확인.

    Returns:
        만료 시 True, deadline 미설정 시 False
    """
    remaining = get_remaining_ms()
    if remaining is None:
        return False
    return remaining <= 0.0


def should_fast_fail(
    estimated_processing_ms: float,
    minimum_useful_ms: float = DEFAULT_MINIMUM_USEFUL_TIME_MS,
) -> bool:
    """
    남은 시간이 예상 처리시간 미만이면 True (Fast-Fail 권장).

    Args:
        estimated_processing_ms: 예상 처리시간 (밀리초)
        minimum_useful_ms: 최소 유효 시간 (밀리초)

    Returns:
        True이면 Fast-Fail 권장
    """
    remaining = get_remaining_ms()
    if remaining is None:
        return False  # deadline 미설정 시 Fast-Fail 하지 않음

    if remaining < minimum_useful_ms:
        return True  # 최소 유효 시간 미만

    return remaining < estimated_processing_ms


def clear_deadline() -> None:
    """현재 컨텍스트의 deadline 제거."""
    _request_deadline.set(None)


def get_propagation_header_value() -> str | None:
    """
    하위 서비스로 전파할 헤더 값 생성.

    Returns:
        "1234ms" 형식 또는 deadline 미설정/만료 시 None
    """
    remaining = get_remaining_ms()
    if remaining is None or remaining <= 0:
        return None
    return f"{remaining:.0f}ms"


def get_deadline_aware_statement_timeout(
    default_db_timeout_ms: int = 30_000,
) -> int | None:
    """
    DeadlineContext 남은 시간과 기본 DB timeout 중 작은 값 반환.
    deadline 미설정이거나 기본 DB timeout보다 넉넉하면 None (SET 불필요).

    Args:
        default_db_timeout_ms: DB 기본 statement_timeout (production.py 설정과 동기화)

    Returns:
        설정할 timeout(ms) 또는 None(SET 불필요)
    """
    remaining = get_remaining_ms()
    if remaining is None:
        return None  # deadline 미설정

    if remaining >= default_db_timeout_ms:
        return None  # 넉넉하면 SET 스킵

    return max(1, int(remaining))  # 최소 1ms


@contextmanager
def deadline_scope(remaining_ms: float) -> Generator[None, None, None]:
    """
    deadline 범위 컨텍스트 매니저.

    블록 진입 시 deadline을 설정하고, 블록 종료 시 이전 값을 복원한다.

    Usage:
        with deadline_scope(3000):
            if should_fast_fail(estimated_ms=2000):
                raise TimeoutError("Fast-Fail")
            process_request()

    Args:
        remaining_ms: 남은 시간 (밀리초)
    """
    previous = _request_deadline.get()
    set_deadline(remaining_ms)
    try:
        yield
    finally:
        _request_deadline.set(previous)

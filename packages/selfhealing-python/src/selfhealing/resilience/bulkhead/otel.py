"""
Bulkhead OpenTelemetry Integration - 선택적 트레이싱 통합.

OpenTelemetry가 활성화된 경우 격벽 작업에 대한 Span을 생성합니다.
OTel이 비활성화되어 있어도 정상 동작합니다 (graceful degradation).

Usage:
    with bulkhead_span("database", 10, timeout=5.0) as span_data:
        # 작업 수행
        span_data["custom_attr"] = "value"
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger(__name__)


def _is_otel_enabled() -> bool:
    """OpenTelemetry 활성화 여부 확인."""
    try:
        from selfhealing.observability import is_otel_enabled

        return is_otel_enabled()
    except ImportError:
        return False
    except Exception:
        return False


@contextmanager
def bulkhead_span(
    bulkhead_name: str,
    max_concurrent: int,
    timeout: float | None = None,
) -> Generator[dict[str, Any], None, None]:
    """
    격벽 작업에 대한 OpenTelemetry Span 생성 (선택적).

    OTel이 비활성화된 경우 빈 컨텍스트만 반환하고 정상 동작합니다.

    Args:
        bulkhead_name: 격벽 이름
        max_concurrent: 최대 동시 실행 수
        timeout: 타임아웃 설정

    Yields:
        span_data: Span에 추가할 데이터 딕셔너리

    Examples:
        with bulkhead_span("database", 10) as span_data:
            # 작업 수행
            result = do_work()
            span_data["result_size"] = len(result)
    """
    span_data: dict[str, Any] = {}
    start_time = time.time()
    span = None

    if _is_otel_enabled():
        try:
            from selfhealing.observability import get_tracer

            tracer = get_tracer()
            if tracer is not None:
                span = tracer.start_span(
                    name=f"bulkhead.acquire.{bulkhead_name}",
                    attributes={
                        "bulkhead.name": bulkhead_name,
                        "bulkhead.max_concurrent": max_concurrent,
                        "bulkhead.timeout": timeout or 0,
                    },
                )
        except Exception as e:
            logger.debug(f"[BulkheadOTel] Failed to create span: {e}")

    try:
        yield span_data
    except Exception as e:
        # 예외 발생 시 Span에 기록
        if span is not None:
            try:
                span.set_attribute("bulkhead.error", True)
                span.set_attribute("bulkhead.error_type", type(e).__name__)
                span.set_attribute("bulkhead.error_message", str(e))
            except Exception:
                pass
        raise
    finally:
        wait_time_ms = (time.time() - start_time) * 1000

        if span is not None:
            try:
                span.set_attribute("bulkhead.wait_time_ms", wait_time_ms)
                for key, value in span_data.items():
                    # 문자열, 숫자, 불린만 허용
                    if isinstance(value, (str, int, float, bool)):
                        span.set_attribute(f"bulkhead.{key}", value)
                span.end()
            except Exception as e:
                logger.debug(f"[BulkheadOTel] Failed to end span: {e}")


@contextmanager
def bulkhead_operation_span(
    bulkhead_name: str,
    operation: str,
) -> Generator[dict[str, Any], None, None]:
    """
    격벽 내 작업에 대한 Span 생성 (선택적).

    Args:
        bulkhead_name: 격벽 이름
        operation: 작업 이름 (예: "db_query", "api_call")

    Yields:
        span_data: Span에 추가할 데이터 딕셔너리

    Examples:
        with bulkhead.acquire():
            with bulkhead_operation_span("database", "user_query") as span_data:
                users = User.objects.all()
                span_data["count"] = len(users)
    """
    span_data: dict[str, Any] = {}
    start_time = time.time()
    span = None

    if _is_otel_enabled():
        try:
            from selfhealing.observability import get_tracer

            tracer = get_tracer()
            if tracer is not None:
                span = tracer.start_span(
                    name=f"bulkhead.operation.{operation}",
                    attributes={
                        "bulkhead.name": bulkhead_name,
                        "bulkhead.operation": operation,
                    },
                )
        except Exception as e:
            logger.debug(f"[BulkheadOTel] Failed to create operation span: {e}")

    try:
        yield span_data
    except Exception as e:
        if span is not None:
            try:
                span.set_attribute("bulkhead.error", True)
                span.set_attribute("bulkhead.error_type", type(e).__name__)
            except Exception:
                pass
        raise
    finally:
        duration_ms = (time.time() - start_time) * 1000

        if span is not None:
            try:
                span.set_attribute("bulkhead.duration_ms", duration_ms)
                for key, value in span_data.items():
                    if isinstance(value, (str, int, float, bool)):
                        span.set_attribute(f"bulkhead.{key}", value)
                span.end()
            except Exception as e:
                logger.debug(f"[BulkheadOTel] Failed to end operation span: {e}")

"""
Hedging OpenTelemetry Integration - 선택적 트레이싱 통합.

OpenTelemetry가 활성화된 경우 헷징 작업에 대한 Span을 생성합니다.
OTel이 비활성화되어 있어도 정상 동작합니다 (graceful degradation).

Usage:
    with hedging_span(mode="delayed", candidates_count=3) as span:
        result = executor.execute(candidates)
        span.set_attribute("hedging.winner", result.source)
"""

from __future__ import annotations

import structlog
import time
from contextlib import contextmanager
from typing import Any, Generator

logger = structlog.get_logger()


def _is_otel_enabled() -> bool:
    """OpenTelemetry 활성화 여부 확인."""
    try:
        from selfhealing.observability import is_otel_enabled

        return is_otel_enabled()
    except ImportError:
        return False
    except Exception:
        return False


class _NoOpSpan:
    """OTel 미설치 시 사용하는 더미 Span."""

    def set_attribute(self, key: str, value: Any) -> None:
        """속성 설정 (무시)."""
        pass

    def set_status(self, status: Any) -> None:
        """상태 설정 (무시)."""
        pass

    def record_exception(self, exc: Exception) -> None:
        """예외 기록 (무시)."""
        pass

    def __enter__(self) -> "_NoOpSpan":
        return self

    def __exit__(self, *args: Any) -> None:
        pass


@contextmanager
def hedging_span(
    mode: str,
    candidates_count: int,
    operation_name: str = "hedging.execute",
) -> Generator[Any, None, None]:
    """
    헷징 실행을 위한 OTel span 컨텍스트 매니저.

    OTel이 비활성화된 경우 더미 span을 반환하고 정상 동작합니다.

    Args:
        mode: 헷징 모드 (immediate, delayed, adaptive)
        candidates_count: 후보 수
        operation_name: span 이름

    Yields:
        OTel Span 또는 더미 Span

    Usage:
        with hedging_span(mode="delayed", candidates_count=3) as span:
            result = executor.execute(candidates)
            span.set_attribute("hedging.winner", result.source)
            span.set_attribute("hedging.latency_ms", result.latency_ms)
    """
    start_time = time.time()
    span = None

    if _is_otel_enabled():
        try:
            from selfhealing.observability import get_tracer

            tracer = get_tracer()
            if tracer is not None:
                span = tracer.start_span(
                    name=operation_name,
                    attributes={
                        "hedging.mode": mode,
                        "hedging.candidates_count": candidates_count,
                    },
                )
        except Exception as e:
            logger.debug(
                "hedging_o_tel.failed_create_span",
                error=e,
            )

    if span is None:
        span = _NoOpSpan()

    try:
        yield span
    except Exception as e:
        # 예외 발생 시 Span에 기록
        try:
            span.set_attribute("hedging.error", True)
            span.set_attribute("hedging.error_type", type(e).__name__)
            span.set_attribute("hedging.error_message", str(e))
            span.record_exception(e)
        except Exception:
            pass
        raise
    finally:
        elapsed_ms = (time.time() - start_time) * 1000

        try:
            span.set_attribute("hedging.elapsed_ms", elapsed_ms)
        except Exception:
            pass

        # 실제 OTel span인 경우 종료
        if hasattr(span, "end"):
            try:
                span.end()
            except Exception:
                pass


def record_hedging_result(
    span: Any,
    winner: str,
    latency_ms: float,
    hedged: bool,
    benefit_ms: float | None = None,
) -> None:
    """
    헷징 결과를 span에 기록.

    Args:
        span: OTel span 또는 더미 span
        winner: 승리한 후보 이름
        latency_ms: 응답 지연시간
        hedged: 헷징 발생 여부
        benefit_ms: 헷징 이득 (밀리초)
    """
    try:
        span.set_attribute("hedging.winner", winner)
        span.set_attribute("hedging.latency_ms", latency_ms)
        span.set_attribute("hedging.hedged", hedged)

        if benefit_ms is not None:
            span.set_attribute("hedging.benefit_ms", benefit_ms)
    except Exception as e:
        logger.debug(
            "hedging_o_tel.failed_record_result",
            error=e,
        )

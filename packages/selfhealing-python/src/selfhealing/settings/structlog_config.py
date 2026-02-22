"""
structlog 전역 설정 — stdlib logging 호환 모드.

structlog를 stdlib logging의 wrapper로 구성하여 기존 인프라를 그대로 유지한다:
- OTEL LoggingInstrumentor: structlog 아래에서 stdlib LogRecord를 가로채 Loki로 전송
- IncidentLogHandler: stdlib logging.Handler 서브클래스이므로 변경 없이 작동
- LoggingSettings: stdlib logger 레벨 설정 그대로 유지
- Django/Celery 내부 로깅: foreign_pre_chain으로 structlog 파이프라인 통과

환경별 Renderer:
- structured_json=True  (production):  JSONRenderer  → Loki/Datadog JSON 자동 파싱
- structured_json=False (development): ConsoleRenderer → 터미널 가독성

공통 프로세서 파이프라인 순서:
  1. merge_contextvars  — contextvars에 bind된 값 자동 병합
  2. add_log_level      — level 필드 자동 주입
  3. add_logger_name    — logger 필드 자동 주입 (__name__ 기반)
  4. TimeStamper(iso)   — timestamp ISO-8601 형식으로 주입
  5. _inject_otel_trace_context — trace_id, span_id 자동 주입 (OTEL 활성 시)
  6. StackInfoRenderer  — 스택 정보 렌더링
  7. format_exc_info    — 예외 정보 렌더링
"""

from __future__ import annotations

import logging
import sys
import threading
from typing import Any

import structlog

# OTEL trace context 주입 프로세서의 재진입을 방지하는 thread-local 플래그.
# observability 초기화 함수 내부 로그가 다시 프로세서를 호출하여
# 무한 재귀가 발생하는 것을 막는다.
_otel_injection_in_progress = threading.local()


def configure_structlog() -> None:
    """structlog 전역 설정을 초기화한다.

    중복 호출에 안전하도록 설계되어 있으며,
    `structured_json` 설정에 따라 렌더러를 선택한다.
    """
    from selfhealing.settings.logging_config import get_logging_settings

    settings = get_logging_settings()

    renderer: structlog.types.Processor
    if settings.structured_json:
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        _inject_otel_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # stdlib logging 핸들러에 structlog ProcessorFormatter 적용
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    # 중복 핸들러 방지: structlog 포매터를 가진 핸들러만 교체
    root_logger.handlers = [
        h for h in root_logger.handlers if not isinstance(getattr(h, "formatter", None), structlog.stdlib.ProcessorFormatter)
    ]
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.DEBUG)


def _inject_otel_trace_context(
    logger: Any,
    method_name: str,
    event_dict: dict[str, Any],
) -> dict[str, Any]:
    """활성 OTEL 스팬의 trace_id, span_id를 event_dict에 주입하는 프로세서.

    OTEL이 설치되지 않았거나 활성 스팬이 없으면 event_dict를 그대로 반환한다.

    재진입 방지: OTEL 초기화 내부에서 발생하는 로그가 이 프로세서를 다시
    호출해 무한 재귀가 발생하는 것을 thread-local 플래그로 차단한다.
    """
    if getattr(_otel_injection_in_progress, "active", False):
        return event_dict

    _otel_injection_in_progress.active = True
    try:
        from selfhealing.observability import (
            get_current_span_id_from_otel,
            get_current_trace_id_from_otel,
        )

        trace_id = get_current_trace_id_from_otel()
        span_id = get_current_span_id_from_otel()

        if trace_id:
            event_dict["trace_id"] = trace_id
        if span_id:
            event_dict["span_id"] = span_id
    except ImportError:
        pass
    finally:
        _otel_injection_in_progress.active = False

    return event_dict

"""
빌트인 Runbook 정의 등록.

시스템 기본 제공 런북(Circuit Breaker 복구, Error Budget 대응 등)을
RunbookRegistry에 등록한다.

TODO: 빌트인 런북 정의 추가 시 이 파일에 구현.

Reference:
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §8.1
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def register_builtin_runbooks() -> None:
    """빌트인 Runbook을 레지스트리에 등록."""
    logger.debug("runbook_builtins.register_called")

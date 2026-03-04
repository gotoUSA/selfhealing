"""
빌트인 ActionPrimitive 등록.

시스템 기본 제공 프리미티브(force_open_cb, scale_replicas 등)를
ActionPrimitiveRegistry에 등록한다.

TODO: 빌트인 프리미티브 추가 시 이 파일에 구현.

Reference:
    docs/self_healing/middleware_system/278_RUNBOOK_SERVICE.md §8.1
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def register_builtin_primitives() -> None:
    """빌트인 ActionPrimitive를 레지스트리에 등록."""
    logger.debug("runbook_primitives.register_called")

"""
Config Shadow Evaluator — 설정 변경 사전 시뮬레이션 엔진.

과거 이벤트를 리플레이하여 설정 변경 효과를 예측한다.
"""

from __future__ import annotations

import threading

from selfhealing.services.config_shadow.service import ShadowEvaluatorService

_service: ShadowEvaluatorService | None = None
_lock = threading.Lock()


def get_shadow_evaluator_service() -> ShadowEvaluatorService:
    global _service
    if _service is None:
        with _lock:
            if _service is None:
                _service = ShadowEvaluatorService()
    return _service


__all__ = [
    "ShadowEvaluatorService",
    "get_shadow_evaluator_service",
]

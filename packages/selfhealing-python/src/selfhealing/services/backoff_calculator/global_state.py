"""
Global Throttle State Manager

Redis-based global throttle state management for cluster-wide coordination.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .models import GlobalThrottleState, ThrottleState

logger = logging.getLogger(__name__)


# =============================================================================
# Global Throttle State Manager
# =============================================================================


class GlobalThrottleStateManager:
    """
    Redis 기반 글로벌 Throttle 상태 관리자.

    외부 API 공통 호출 시 클러스터 전체의 평균 부하를 참조하여
    재시도 강도를 조절합니다.
    """

    REDIS_KEY = "selfhealing:throttle:global_state"
    STATE_TTL_SECONDS = 60

    def __init__(self, redis_client: Any = None):
        self._redis = redis_client

    @property
    def redis(self) -> Any | None:
        """Redis 클라이언트 지연 초기화."""
        if self._redis is None:
            try:
                from selfhealing.adapters.cache import get_redis_client

                self._redis = get_redis_client()
            except Exception:
                return None
        return self._redis

    def report_local_state(self, local_state: ThrottleState, pod_id: str) -> None:
        """로컬 상태를 글로벌에 보고."""
        if not self.redis:
            return

        try:
            import json

            # 개별 Pod 상태 저장
            pod_key = f"{self.REDIS_KEY}:pod:{pod_id}"
            self.redis.setex(
                pod_key,
                self.STATE_TTL_SECONDS,
                json.dumps(
                    {
                        "emergency_level": local_state.emergency_level,
                        "sla_warning": local_state.sla_warning_active,
                        "sla_critical": local_state.sla_critical_active,
                        "timestamp": time.time(),
                    }
                ),
            )
        except Exception as e:
            logger.debug(f"[GlobalThrottleState] Failed to report: {e}")

    def get_global_state(self) -> GlobalThrottleState | None:
        """클러스터 전체 상태 조회."""
        if not self.redis:
            return None

        try:
            import json

            # 모든 Pod 상태 조회
            pod_keys = self.redis.keys(f"{self.REDIS_KEY}:pod:*")
            if not pod_keys:
                return None

            total_emergency = 0
            warning_count = 0
            critical_count = 0

            for key in pod_keys:
                data = self.redis.get(key)
                if data:
                    pod_state = json.loads(data)
                    total_emergency += pod_state.get("emergency_level", 0)
                    if pod_state.get("sla_warning"):
                        warning_count += 1
                    if pod_state.get("sla_critical"):
                        critical_count += 1

            pod_count = len(pod_keys)
            return GlobalThrottleState(
                cluster_emergency_level=(total_emergency // pod_count if pod_count > 0 else 0),
                cluster_sla_warning_count=warning_count,
                cluster_sla_critical_count=critical_count,
                reporting_pod_count=pod_count,
                last_updated=time.time(),
            )
        except Exception as e:
            logger.debug(f"[GlobalThrottleState] Failed to get: {e}")
            return None

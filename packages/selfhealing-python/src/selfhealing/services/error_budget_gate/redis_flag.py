"""
Budget Exhausted Flag Manager.

Redis 기반 예산 소진 상태 글로벌 플래그 관리.
고부하 환경에서 ErrorBudgetService 부하 감소를 위한 캐싱 레이어.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

logger = structlog.get_logger()


# Redis 키 패턴
BUDGET_EXHAUSTED_FLAG_KEY = "selfhealing:error_budget:exhausted"
BUDGET_EXHAUSTED_BY_SLO_KEY = "selfhealing:error_budget:exhausted:{slo_name}"
BUDGET_STATUS_KEY = "selfhealing:error_budget:status:{slo_name}"

# 리전별 Redis 키 패턴
BUDGET_EXHAUSTED_BY_SLO_REGION_KEY = "selfhealing:error_budget:exhausted:{slo_name}:{region}"
BUDGET_STATUS_REGION_KEY = "selfhealing:error_budget:status:{slo_name}:{region}"

# TTL: 캐시보다 약간 길게 (stale 방지)
BUDGET_FLAG_TTL_SECONDS = 60


class BudgetExhaustedFlagManager:
    """
    Redis 기반 예산 소진 상태 글로벌 플래그 관리.

    다계층 캐시 구조:
    1. 로컬 메모리 캐시 (5초 TTL)
    2. Redis 캐시 (60초 TTL)
    3. ErrorBudgetService 실시간 조회 (Fallback)

    Fail-Open 설계:
    - Redis 장애 시 로컬 캐시 사용
    - 모든 캐시 실패 시 False 반환 (소진되지 않은 것으로 가정)
    """

    def __init__(self, redis_client: Any = None):
        """
        Args:
            redis_client: Redis 클라이언트 (선택적)
        """
        self._redis = redis_client
        self._local_cache: dict[str, bool] = {}
        self._local_cache_time: dict[str, float] = {}
        self._local_ttl_seconds = 5.0  # 로컬 캐시 TTL

    @staticmethod
    def _build_slo_key(slo_name: str, region: str | None = None) -> str:
        """SLO별 Redis 키 생성. region이 있으면 리전별 키 반환."""
        if region:
            return BUDGET_EXHAUSTED_BY_SLO_REGION_KEY.format(slo_name=slo_name, region=region)
        return BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name=slo_name)

    @staticmethod
    def _build_status_key(slo_name: str, region: str | None = None) -> str:
        """SLO별 상태 Redis 키 생성. region이 있으면 리전별 키 반환."""
        if region:
            return BUDGET_STATUS_REGION_KEY.format(slo_name=slo_name, region=region)
        return BUDGET_STATUS_KEY.format(slo_name=slo_name)

    def set_exhausted(
        self,
        slo_name: str,
        exhausted: bool,
        region: str | None = None,
    ) -> None:
        """
        예산 소진 상태 설정 (Redis + 로컬 캐시).

        Args:
            slo_name: SLO 이름 (예: "availability", "availability:payment")
            exhausted: 소진 여부
            region: 리전 식별자 (None이면 글로벌 키 사용)
        """
        key = self._build_slo_key(slo_name, region)
        cache_key = f"{slo_name}:{region}" if region else slo_name

        if self._redis:
            try:
                if exhausted:
                    self._redis.setex(key, BUDGET_FLAG_TTL_SECONDS, "1")
                else:
                    self._redis.delete(key)
            except Exception as e:
                logger.warning(
                    "budget_flag.redis_write_failed",
                    error=e,
                )

        # 로컬 캐시 업데이트
        self._local_cache[cache_key] = exhausted
        self._local_cache_time[cache_key] = time.time()

    def is_exhausted(
        self,
        slo_name: str = "availability",
        region: str | None = None,
    ) -> bool:
        """
        예산 소진 상태 조회 (로컬 캐시 → Redis → False).

        Args:
            slo_name: SLO 이름
            region: 리전 식별자 (None이면 글로벌 키 조회)

        Returns:
            True if budget is exhausted, False otherwise (Fail-Open)
        """
        cache_key = f"{slo_name}:{region}" if region else slo_name
        now = time.time()

        # 1. 로컬 캐시 확인
        if cache_key in self._local_cache:
            cache_time = self._local_cache_time.get(cache_key, 0)
            if now - cache_time < self._local_ttl_seconds:
                return self._local_cache[cache_key]

        # 2. Redis 조회
        if self._redis:
            try:
                key = self._build_slo_key(slo_name, region)
                value = self._redis.get(key)
                result = value == b"1" or value == "1"
                self._local_cache[cache_key] = result
                self._local_cache_time[cache_key] = now
                return result
            except Exception as e:
                logger.warning(
                    "budget_flag.redis_read_failed",
                    error=e,
                )

        # 3. Fail-Open: 소진되지 않은 것으로 가정
        return False

    def clear(self, slo_name: str | None = None) -> None:
        """
        캐시 초기화.

        Args:
            slo_name: 특정 SLO만 초기화 (None이면 전체)
        """
        if slo_name:
            self._local_cache.pop(slo_name, None)
            self._local_cache_time.pop(slo_name, None)
            if self._redis:
                try:
                    key = BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name=slo_name)
                    self._redis.delete(key)
                except Exception as e:
                    logger.warning(
                        "budget_flag.redis_delete_failed",
                        error=e,
                    )
        else:
            self._local_cache.clear()
            self._local_cache_time.clear()

    def get_status(self) -> dict[str, Any]:
        """현재 상태 정보 반환."""
        return {
            "local_cache_size": len(self._local_cache),
            "local_ttl_seconds": self._local_ttl_seconds,
            "redis_available": self._redis is not None,
            "cached_slos": list(self._local_cache.keys()),
        }


# 싱글톤 인스턴스
_global_flag_manager: BudgetExhaustedFlagManager | None = None


def get_budget_exhausted_flag_manager() -> BudgetExhaustedFlagManager:
    """글로벌 BudgetExhaustedFlagManager 인스턴스 반환."""
    global _global_flag_manager

    if _global_flag_manager is None:
        # Redis 클라이언트 가져오기 시도
        redis_client = None
        try:
            from selfhealing.services.event_bus_redis import get_redis_client

            redis_client = get_redis_client()
        except Exception:
            pass

        _global_flag_manager = BudgetExhaustedFlagManager(redis_client=redis_client)

    return _global_flag_manager


def reset_budget_exhausted_flag_manager() -> None:
    """글로벌 인스턴스 리셋 (테스트용)."""
    global _global_flag_manager
    _global_flag_manager = None


__all__ = [
    "BudgetExhaustedFlagManager",
    "get_budget_exhausted_flag_manager",
    "reset_budget_exhausted_flag_manager",
    "BUDGET_EXHAUSTED_FLAG_KEY",
    "BUDGET_EXHAUSTED_BY_SLO_KEY",
    "BUDGET_STATUS_KEY",
    "BUDGET_EXHAUSTED_BY_SLO_REGION_KEY",
    "BUDGET_STATUS_REGION_KEY",
    "BUDGET_FLAG_TTL_SECONDS",
]

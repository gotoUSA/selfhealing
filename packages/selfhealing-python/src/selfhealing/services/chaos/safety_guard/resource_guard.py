"""
X-Test Resource Guard.

시스템 CPU/메모리가 과부하 상태일 때 X-Test 요청을 자동 차단하여
테스트가 운영 시스템에 추가 부담을 주는 것을 방지.

Features:
- CPU 임계값(80%) 초과 시 차단
- 메모리 임계값(85%) 초과 시 차단
- cgroup 우선, psutil 폴백 지원
- 429 Too Many Requests + Retry-After 응답

Usage:
    from selfhealing.services.chaos.safety_guard import (
        ResourceGuard,
        get_resource_guard,
    )

    guard = get_resource_guard()
    result = guard.is_safe_for_chaos()

    if not result.is_safe:
        # 429 응답 반환
        retry_after = guard.get_recommended_wait()
"""

from __future__ import annotations

from dataclasses import dataclass

import psutil
import structlog

from selfhealing.core.resource_monitor import CgroupResourceMonitor
from selfhealing.settings.resource_guard import get_resource_guard_settings

logger = structlog.get_logger()


@dataclass
class ResourceStatus:
    """
    시스템 리소스 상태.

    Attributes:
        cpu_percent: 현재 CPU 사용률 (%)
        memory_percent: 현재 메모리 사용률 (%)
        is_cgroup_available: cgroup 메트릭 사용 가능 여부
        source: 리소스 측정 소스 (cgroup_v2, cgroup_v1, psutil)
    """

    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    is_cgroup_available: bool = False
    source: str = "psutil"


@dataclass
class ResourceCheckResult:
    """
    리소스 체크 결과.

    Attributes:
        is_safe: Chaos 테스트 실행 가능 여부
        cpu_percent: 현재 CPU 사용률 (%)
        memory_percent: 현재 메모리 사용률 (%)
        cpu_threshold: CPU 임계값 (%)
        memory_threshold: 메모리 임계값 (%)
        block_reason: 차단된 경우 사유
        source: 리소스 측정 소스
    """

    is_safe: bool = True
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    cpu_threshold: float = 80.0
    memory_threshold: float = 85.0
    block_reason: str | None = None
    source: str = "psutil"

    def to_response_dict(self) -> dict:
        """429 응답 본문용 딕셔너리 변환."""
        return {
            "error": "resource_overloaded",
            "message": self.block_reason or "System resource overloaded",
            "cpu_percent": round(self.cpu_percent, 1),
            "memory_percent": round(self.memory_percent, 1),
            "cpu_threshold": self.cpu_threshold,
            "memory_threshold": self.memory_threshold,
            "retry_after": get_resource_guard_settings().retry_after_seconds,
        }


class ResourceGuard:
    """
    X-Test 리소스 가드.

    시스템 CPU/메모리 상태를 체크하여 과부하 시 X-Test 요청을 차단.
    RecoveryGate의 cpu_threshold_percent(80%)와 일관성 유지.

    측정 우선순위:
    1. cgroup v2 (Kubernetes 1.25+, Docker 20.10+)
    2. cgroup v1 (레거시)
    3. psutil (일반 환경)
    """

    def __init__(self):
        """ResourceGuard 초기화."""
        self._settings = get_resource_guard_settings()

    def _get_cpu_percent(self) -> float:
        """
        현재 CPU 사용률 조회.

        SystemMetricsCache에서 캐시된 값을 우선 조회하고 (~0ms),
        캐시 미가동 시 psutil 직접 측정으로 fallback.

        Returns:
            CPU 사용률 (0.0 ~ 100.0)
        """
        try:
            from selfhealing.services.system_metrics_cache import (
                get_system_metrics_cache,
            )

            cache = get_system_metrics_cache()
            if cache.is_running():
                return cache.get_cpu_percent()
        except Exception:
            pass

        # Fallback: 직접 측정 (캐시 미가동 시)
        try:
            return psutil.cpu_percent(interval=0.1)
        except Exception as e:
            logger.warning(
                "resource_guard.failed_get_cpu_percent",
                error=e,
            )
            return 0.0

    def _get_memory_percent_cgroup(self) -> float | None:
        """
        cgroup 기반 메모리 사용률 조회.

        컨테이너 환경에서 cgroup 메트릭을 우선 사용.
        cgroup v2 > cgroup v1 순서로 시도.

        Returns:
            메모리 사용률 (0.0 ~ 100.0) 또는 None (cgroup 미지원)
        """
        return CgroupResourceMonitor.get_memory_usage_percent()

    def _get_memory_percent_psutil(self) -> float:
        """
        psutil 기반 메모리 사용률 조회.

        일반 환경(비컨테이너)에서 사용.

        Returns:
            메모리 사용률 (0.0 ~ 100.0)
        """
        try:
            memory = psutil.virtual_memory()
            return memory.percent
        except Exception as e:
            logger.warning(
                "resource_guard.failed_get_memory_percent",
                error=e,
            )
            return 0.0

    def get_resource_status(self) -> ResourceStatus:
        """
        현재 시스템 리소스 상태 조회.

        cgroup 메트릭이 가능하면 우선 사용하고,
        불가능하면 psutil로 폴백.

        Returns:
            ResourceStatus: 현재 리소스 상태
        """
        cpu_percent = self._get_cpu_percent()

        # 메모리: cgroup 우선, psutil 폴백
        cgroup_memory = self._get_memory_percent_cgroup()

        if cgroup_memory is not None:
            return ResourceStatus(
                cpu_percent=cpu_percent,
                memory_percent=cgroup_memory,
                is_cgroup_available=True,
                source="cgroup",
            )

        psutil_memory = self._get_memory_percent_psutil()
        return ResourceStatus(
            cpu_percent=cpu_percent,
            memory_percent=psutil_memory,
            is_cgroup_available=False,
            source="psutil",
        )

    def is_safe_for_chaos(self) -> ResourceCheckResult:
        """
        X-Test(Chaos) 실행이 안전한지 확인.

        CPU가 80% 초과하거나 메모리가 85% 초과하면 차단.
        설정에서 리소스 체크가 비활성화되어 있으면 항상 허용.

        Returns:
            ResourceCheckResult: 체크 결과 (is_safe=False면 차단)
        """
        settings = self._settings

        # 리소스 체크 비활성화 시 항상 허용
        if not settings.resource_check_enabled:
            logger.debug("resource_guard.resource_check_disabled_allowing")
            return ResourceCheckResult(
                is_safe=True,
                cpu_threshold=settings.cpu_threshold,
                memory_threshold=settings.memory_threshold,
            )

        status = self.get_resource_status()

        result = ResourceCheckResult(
            is_safe=True,
            cpu_percent=status.cpu_percent,
            memory_percent=status.memory_percent,
            cpu_threshold=settings.cpu_threshold,
            memory_threshold=settings.memory_threshold,
            source=status.source,
        )

        # CPU 임계값 체크
        if status.cpu_percent > settings.cpu_threshold:
            result.is_safe = False
            result.block_reason = f"CPU usage {status.cpu_percent:.1f}% exceeds threshold " f"{settings.cpu_threshold}%"
            logger.warning(
                "resource_guard.test_blocked",
                result=result.block_reason,
            )
            return result

        # 메모리 임계값 체크
        if status.memory_percent > settings.memory_threshold:
            result.is_safe = False
            result.block_reason = (
                f"Memory usage {status.memory_percent:.1f}% exceeds threshold " f"{settings.memory_threshold}%"
            )
            logger.warning(
                "resource_guard.test_blocked",
                result=result.block_reason,
            )
            return result

        logger.debug(
            "resource_guard.resource_check_passed",
            status=status.cpu_percent,
            memory_percent=status.memory_percent,
        )
        return result

    def get_recommended_wait(self) -> int:
        """
        권장 대기 시간 반환.

        429 응답의 Retry-After 헤더에 사용.

        Returns:
            대기 시간 (초)
        """
        return self._settings.retry_after_seconds


# =============================================================================
# Singleton Pattern
# =============================================================================

_resource_guard: ResourceGuard | None = None


def get_resource_guard() -> ResourceGuard:
    """
    캐시된 ResourceGuard 인스턴스 반환.

    Returns:
        ResourceGuard: 싱글톤 인스턴스
    """
    global _resource_guard
    if _resource_guard is None:
        _resource_guard = ResourceGuard()
        logger.debug("resource_guard.initialized_singleton_instance")
    return _resource_guard


def reset_resource_guard() -> None:
    """
    캐시된 ResourceGuard 초기화 (테스트용).
    """
    global _resource_guard
    _resource_guard = None
    logger.debug("resource_guard.reset_singleton")

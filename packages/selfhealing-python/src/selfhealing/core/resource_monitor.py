"""
Container/VM Resource Monitor.

cgroup v1/v2 지원 리소스 모니터링 유틸리티.

Cgroup 기반으로 컨테이너의 메모리/CPU 제한을 감지하고,
Chaos Experiment의 Resource Exhaustion이 안전 한계 내에서 동작하도록 합니다.

설정값은 ResourceMonitorSettings를 통해 환경변수로 오버라이드 가능:
- SELFHEALING_RESOURCE_SAFETY_MARGIN
"""

from __future__ import annotations

import structlog
from pathlib import Path

from selfhealing.settings.resource_monitor import get_resource_monitor_settings

logger = structlog.get_logger()


class CgroupResourceMonitor:
    """
    Cgroup 기반 리소스 모니터.

    Memory, CPU 제한 감지 및 현재 사용량 조회.
    cgroup v1 및 v2 모두 지원.

    Usage:
        max_bytes = CgroupResourceMonitor.get_memory_max_bytes()
        current_bytes = CgroupResourceMonitor.get_memory_current_bytes()
        available = CgroupResourceMonitor.get_available_memory_bytes()
    """

    # cgroup v2 경로 (Kubernetes 1.25+, Docker 20.10+)
    CGROUP_V2_MEMORY_MAX = Path("/sys/fs/cgroup/memory.max")
    CGROUP_V2_MEMORY_CURRENT = Path("/sys/fs/cgroup/memory.current")

    # cgroup v1 경로 (레거시 호환)
    CGROUP_V1_MEMORY_LIMIT = Path("/sys/fs/cgroup/memory/memory.limit_in_bytes")
    CGROUP_V1_MEMORY_USAGE = Path("/sys/fs/cgroup/memory/memory.usage_in_bytes")

    @classmethod
    def _get_default_safety_margin(cls) -> float:
        """기본 안전 마진 (15%). ResourceMonitorSettings에서 로드."""
        return get_resource_monitor_settings().safety_margin

    @classmethod
    def get_memory_max_bytes(cls) -> int | None:
        """
        컨테이너 메모리 제한 (bytes).

        Returns:
            메모리 제한 (bytes). None = 제한 없음 또는 감지 불가.
        """
        try:
            # cgroup v2 먼저 시도
            if cls.CGROUP_V2_MEMORY_MAX.exists():
                content = cls.CGROUP_V2_MEMORY_MAX.read_text().strip()
                if content != "max":  # "max" = 제한 없음
                    return int(content)
                return None

            # cgroup v1 폴백
            if cls.CGROUP_V1_MEMORY_LIMIT.exists():
                value = int(cls.CGROUP_V1_MEMORY_LIMIT.read_text().strip())
                # 매우 큰 값은 사실상 무제한 (9EB 정도)
                if value < 2**62:
                    return value
                return None

            return None
        except Exception as e:
            logger.debug(
                "cgroup_resource_monitor.failed_read_memory_max",
                error=e,
            )
            return None

    @classmethod
    def get_memory_current_bytes(cls) -> int | None:
        """
        현재 메모리 사용량 (bytes).

        Returns:
            현재 사용량 (bytes). None = 감지 불가.
        """
        try:
            if cls.CGROUP_V2_MEMORY_CURRENT.exists():
                return int(cls.CGROUP_V2_MEMORY_CURRENT.read_text().strip())

            if cls.CGROUP_V1_MEMORY_USAGE.exists():
                return int(cls.CGROUP_V1_MEMORY_USAGE.read_text().strip())

            return None
        except Exception as e:
            logger.debug(
                "cgroup_resource_monitor.failed_read_memory_current",
                error=e,
            )
            return None

    @classmethod
    def get_available_memory_bytes(
        cls,
        safety_margin: float | None = None,
    ) -> int | None:
        """
        안전하게 사용 가능한 메모리 (bytes).

        OOM Killer 발동을 방지하기 위해 안전 마진을 적용합니다.

        Args:
            safety_margin: OOM 방지 여유분 비율 (기본 15%, 환경변수로 설정 가능)

        Returns:
            (max - current) * (1 - safety_margin). None = 계산 불가.

        Example:
            # 1GB 제한, 700MB 사용 중, 15% 마진
            # available = (1024MB - 700MB) * 0.85 = 275MB
        """
        if safety_margin is None:
            safety_margin = cls._get_default_safety_margin()

        max_bytes = cls.get_memory_max_bytes()
        current_bytes = cls.get_memory_current_bytes()

        if max_bytes is None or current_bytes is None:
            return None

        available = max_bytes - current_bytes
        safe_available = int(available * (1.0 - safety_margin))

        logger.debug(
            f"[CgroupResourceMonitor] max={max_bytes / 1024 / 1024:.0f}MB, "
            f"current={current_bytes / 1024 / 1024:.0f}MB, "
            f"available={available / 1024 / 1024:.0f}MB, "
            f"safe(margin={safety_margin * 100:.0f}%)={safe_available / 1024 / 1024:.0f}MB"
        )

        return max(0, safe_available)

    @classmethod
    def get_memory_usage_percent(cls) -> float | None:
        """
        현재 메모리 사용률 (%).

        Returns:
            사용률 0.0~100.0. None = 계산 불가.
        """
        max_bytes = cls.get_memory_max_bytes()
        current_bytes = cls.get_memory_current_bytes()

        if max_bytes is None or current_bytes is None or max_bytes == 0:
            return None

        return (current_bytes / max_bytes) * 100.0

    @classmethod
    def is_memory_constrained(cls) -> bool:
        """
        컨테이너가 메모리 제한이 설정되어 있는지 확인.

        Returns:
            True if cgroup 메모리 제한이 설정됨.
        """
        return cls.get_memory_max_bytes() is not None

    @classmethod
    def check_safe_for_exhaustion(
        cls,
        requested_bytes: int,
        safety_margin: float | None = None,
    ) -> tuple[bool, int]:
        """
        ResourceExhaustion 실험에서 요청된 메모리가 안전한지 확인.

        Args:
            requested_bytes: 요청된 메모리 (bytes)
            safety_margin: 안전 마진 (기본 15%, 환경변수로 설정 가능)

        Returns:
            (is_safe, actual_bytes_to_use)
            - is_safe: 요청량이 안전 한계 내인지
            - actual_bytes_to_use: 실제 사용해야 할 bytes (캡 적용됨)
        """
        if safety_margin is None:
            safety_margin = cls._get_default_safety_margin()

        available = cls.get_available_memory_bytes(safety_margin)

        if available is None:
            # cgroup 감지 불가 - 제한 없이 허용
            logger.warning("cgroup_resource_monitor.cannot_detect_cgroup_limits")
            return True, requested_bytes

        if requested_bytes <= available:
            return True, requested_bytes

        # 안전 한계 초과 - 캡 적용
        logger.warning(
            f"[CgroupResourceMonitor] Requested {requested_bytes / 1024 / 1024:.0f}MB "
            f"exceeds safe limit {available / 1024 / 1024:.0f}MB, capping"
        )
        return False, available

    # =========================================================================
    # Phase 3 (238_PREDICTIVE_ANOMALY_FORECASTER): OOM 예측
    # =========================================================================

    @classmethod
    def predict_oom_minutes(
        cls,
        memory_samples: list[int],
        max_memory_bytes: int | None = None,
        safety_margin: float | None = None,
    ) -> float | None:
        """
        HoltLinear 기반 OOM 발생까지 예상 시간(분) 예측.

        메모리 사용량 시계열 데이터를 분석하여 현재 증가 추세를 기반으로
        메모리 한도(max_memory_bytes)에 도달하기까지의 시간을 예측한다.

        Args:
            memory_samples: 메모리 사용량 시계열 (bytes). 60초 간격 가정.
            max_memory_bytes: 메모리 한도. None이면 cgroup에서 자동 감지.
            safety_margin: 안전 마진. None이면 기본값 사용.

        Returns:
            예상 OOM까지 시간(분). None = 데이터 부족 또는 쓰레드 정체/감소 중.

        코드 근거:
            기존 get_available_memory_bytes()는 현재 스냅샷만 확인.
            HoltLinear 트렌드 분석으로 메모리 누수 패턴을 사전 감지하여
            OOM Killer 발동 전에 선제적 조치를 가능하게 함.
        """
        if len(memory_samples) < 5:
            return None

        if max_memory_bytes is None:
            max_memory_bytes = cls.get_memory_max_bytes()
        if max_memory_bytes is None:
            return None

        if safety_margin is None:
            safety_margin = cls._get_default_safety_margin()

        # 안전 한도 = max * (1 - margin)
        safe_limit = int(max_memory_bytes * (1.0 - safety_margin))

        try:
            from selfhealing.services.predictive_forecaster.time_series import (
                HoltLinearForecaster,
            )

            forecaster = HoltLinearForecaster(alpha=0.3, beta=0.1, warmup_samples=min(5, len(memory_samples)))
            for sample in memory_samples:
                forecaster.update(float(sample))

            if not forecaster.is_warmed_up:
                return None

            trend_slope = forecaster.get_trend_slope()

            # 트렌드가 0 이하이면 메모리 증가 없음 → OOM 위험 없음
            if trend_slope <= 0:
                return None

            current_level = forecaster._level
            if current_level is None or current_level >= safe_limit:
                return 0.0  # 이미 한도 초과

            # 남은 메모리 / 분당 증가량 = OOM까지 분
            remaining = safe_limit - current_level
            minutes_to_oom = remaining / trend_slope

            logger.debug(
                f"[CgroupResourceMonitor] OOM Prediction: "
                f"current={current_level / 1024 / 1024:.0f}MB, "
                f"limit={safe_limit / 1024 / 1024:.0f}MB, "
                f"slope={trend_slope / 1024 / 1024:.2f}MB/step, "
                f"est_minutes={minutes_to_oom:.1f}"
            )

            return max(0.0, minutes_to_oom)
        except Exception as e:
            logger.debug(
                "cgroup_resource_monitor.oom_prediction_failed",
                error=e,
            )
            return None


# Backward compatibility alias
CgroupMemoryMonitor = CgroupResourceMonitor

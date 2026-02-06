"""
Exponential Backoff Calculator

Provides configurable exponential backoff with jitter for retry logic.

Features:
- Exponential backoff: base^attempt (4, 16, 64, ...)
- Maximum delay cap to prevent excessive wait times
- Jitter (±25%) to prevent thundering herd problem
- Per-domain configuration support
- Throttle-aware backoff with dynamic multipliers
- EventBus push-based state caching
- Global (Redis) throttle state sharing
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from selfhealing.settings import get_config

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

# 시스템 전체 타임아웃 (30분) - 이를 초과하면 사용자 체감 불가
SYSTEM_TIMEOUT_SECONDS = 1800


# =============================================================================
# Throttle State Models
# =============================================================================


@dataclass
class ThrottleState:
    """AdaptiveThrottle 현재 상태 스냅샷."""

    current_limit: int
    initial_limit: int
    emergency_level: int = 0
    full_stop_active: bool = False
    sla_warning_active: bool = False
    sla_critical_active: bool = False
    recovery_dampening_active: bool = False
    error_budget_reduction_active: bool = False


@dataclass
class PushBasedThrottleStateCache:
    """
    EventBus 푸시 기반 Throttle 상태 캐시.

    매번 get_stats() 호출로 Lock 경합하는 대신
    EventBus 이벤트를 구독하여 상태 변경 시에만 캐시를 업데이트합니다.
    """

    multiplier: float = 1.0
    reason: str = "normal"
    last_updated: float = 0.0
    full_stop_active: bool = False
    emergency_level: int = 0

    # 캐시 유효 시간 (EventBus 이벤트 누락 대비 폴백)
    max_cache_age_seconds: float = 30.0

    def is_stale(self) -> bool:
        """캐시가 오래되었는지 확인 (Fail-safe)."""
        return (time.time() - self.last_updated) > self.max_cache_age_seconds


@dataclass
class GlobalThrottleState:
    """
    클러스터 전체 Throttle 상태 (Redis 저장).

    Pod 간 상태 공유를 위한 집계 데이터 구조.
    """

    cluster_avg_rtt_ms: float = 0.0
    cluster_emergency_level: int = 0
    cluster_sla_warning_count: int = 0
    cluster_sla_critical_count: int = 0
    reporting_pod_count: int = 0
    last_updated: float = 0.0

    def to_dict(self) -> dict:
        """직렬화용 딕셔너리 변환."""
        return {
            "cluster_avg_rtt_ms": self.cluster_avg_rtt_ms,
            "cluster_emergency_level": self.cluster_emergency_level,
            "cluster_sla_warning_count": self.cluster_sla_warning_count,
            "cluster_sla_critical_count": self.cluster_sla_critical_count,
            "reporting_pod_count": self.reporting_pod_count,
            "last_updated": self.last_updated,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GlobalThrottleState":
        """딕셔너리에서 객체 생성."""
        return cls(
            cluster_avg_rtt_ms=data.get("cluster_avg_rtt_ms", 0.0),
            cluster_emergency_level=data.get("cluster_emergency_level", 0),
            cluster_sla_warning_count=data.get("cluster_sla_warning_count", 0),
            cluster_sla_critical_count=data.get("cluster_sla_critical_count", 0),
            reporting_pod_count=data.get("reporting_pod_count", 0),
            last_updated=data.get("last_updated", 0.0),
        )


# =============================================================================
# Adaptive Retry Budget
# =============================================================================


@dataclass
class AdaptiveRetryBudget:
    """
    적응형 재시도 예산 관리자.

    전체 요청 대비 재시도 비율을 관리하여 Self-DDoS를 방지합니다.
    Throttle 상태에 따라 예산이 동적으로 삭감됩니다.
    """

    max_retry_ratio: float = 0.10  # 기본 10%
    current_retry_count: int = 0
    current_total_count: int = 0
    window_seconds: int = 60
    _window_start: float = field(default_factory=time.time)

    # Throttle 연동 동적 삭감 비율
    THROTTLE_BUDGET_RATIOS: dict[str, float] = field(
        default_factory=lambda: {
            "normal": 0.10,  # 10%
            "sla_warning": 0.07,  # 7%
            "sla_critical": 0.05,  # 5%
            "emergency_level_1": 0.03,  # 3%
            "emergency_level_2": 0.03,  # 3%
            "emergency_1_2": 0.03,  # 3%
            "emergency_level_3": 0.01,  # 1%
            "emergency_3": 0.01,  # 1%
            "full_stop": 0.0,  # 0% (재시도 금지)
            "full_stop_active": 0.0,  # 0% (재시도 금지)
        }
    )

    def should_allow_retry(self) -> bool:
        """재시도 허용 여부 확인."""
        self._maybe_reset_window()

        if self.current_total_count == 0:
            return True

        current_ratio = self.current_retry_count / self.current_total_count
        return current_ratio < self.max_retry_ratio

    def record_request(self, is_retry: bool = False) -> None:
        """요청 기록."""
        self._maybe_reset_window()
        self.current_total_count += 1
        if is_retry:
            self.current_retry_count += 1

    def _maybe_reset_window(self) -> None:
        """윈도우 초과 시 리셋."""
        now = time.time()
        if now - self._window_start > self.window_seconds:
            self.current_retry_count = 0
            self.current_total_count = 0
            self._window_start = now

    def adjust_budget_for_throttle_state(self, throttle_reason: str) -> None:
        """Throttle 상태에 따라 예산 동적 조정."""
        if throttle_reason in self.THROTTLE_BUDGET_RATIOS:
            self.max_retry_ratio = self.THROTTLE_BUDGET_RATIOS[throttle_reason]
        else:
            # 알 수 없는 상태면 보수적으로 5%
            self.max_retry_ratio = 0.05

    def get_stats(self) -> dict:
        """현재 상태 통계."""
        return {
            "max_retry_ratio": self.max_retry_ratio,
            "current_retry_count": self.current_retry_count,
            "current_total_count": self.current_total_count,
            "current_ratio": (self.current_retry_count / self.current_total_count if self.current_total_count > 0 else 0.0),
            "budget_remaining": max(
                0,
                int(self.current_total_count * self.max_retry_ratio) - self.current_retry_count,
            ),
        }


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


@dataclass
class BackoffConfig:
    """Configuration for exponential backoff calculation."""

    base: int = 4  # Base for exponential (4^n seconds)
    max_delay: int = 180  # Maximum wait time (3 minutes)
    jitter_percent: int = 25  # ±25% random jitter
    min_delay: int = 1  # Minimum delay in seconds

    @classmethod
    def from_settings(cls, domain: str | None = None) -> BackoffConfig:
        """
        Load configuration from core config.

        Args:
            domain: Optional domain for per-domain overrides

        Returns:
            BackoffConfig with merged settings
        """
        retry_settings = get_config().retry

        # Default values from centralized config
        config = cls(
            base=retry_settings.backoff_base,
            max_delay=int(retry_settings.max_delay),
            jitter_percent=retry_settings.jitter_percent,
            min_delay=retry_settings.min_delay,
        )

        # Apply per-domain overrides if available
        if domain:
            # Get domain config from centralized config
            full_config = get_config()
            domain_configs = getattr(full_config, "domain_configs", {})
            domain_config = domain_configs.get(domain, {})
            if "backoff_base" in domain_config:
                config.base = domain_config["backoff_base"]

        return config


class BackoffCalculator:
    """
    Calculates exponential backoff delays with jitter.

    The formula is:
        delay = min(base^attempt, max_delay) * (1 ± jitter_percent/100)

    Example with default settings (base=4, max=180, jitter=25%):
        - Attempt 1: 4s (±1s jitter) → 3-5s
        - Attempt 2: 16s (±4s jitter) → 12-20s
        - Attempt 3: 64s (±16s jitter) → 48-80s
        - Attempt 4+: 180s (capped at max)
    """

    def __init__(self, config: BackoffConfig | None = None):
        """
        Initialize the calculator.

        Args:
            config: BackoffConfig instance, or None to load from settings
        """
        self.config = config or BackoffConfig.from_settings()

    def calculate(self, attempt: int, with_jitter: bool = True) -> int:
        """
        Calculate backoff delay for a given attempt.

        Args:
            attempt: The attempt number (1-based)
            with_jitter: Whether to apply jitter

        Returns:
            Delay in seconds (integer)
        """
        if attempt < 1:
            return self.config.min_delay

        # Exponential backoff: base^attempt
        delay = self.config.base**attempt

        # Cap at maximum delay
        delay = min(delay, self.config.max_delay)

        # Apply jitter if enabled
        if with_jitter and self.config.jitter_percent > 0:
            jitter_factor = self.config.jitter_percent / 100.0
            # Random value between -jitter_factor and +jitter_factor
            jitter = delay * jitter_factor * (random.random() * 2 - 1)
            delay = int(delay + jitter)

        # Ensure minimum delay
        return max(self.config.min_delay, delay)

    def get_delays_sequence(self, max_attempts: int, with_jitter: bool = False) -> list[int]:
        """
        Get the sequence of delays for multiple attempts.

        Args:
            max_attempts: Number of attempts to calculate
            with_jitter: Whether to apply jitter

        Returns:
            List of delay values in seconds
        """
        return [self.calculate(attempt, with_jitter) for attempt in range(1, max_attempts + 1)]


# =============================================================================
# Throttle-Aware Backoff Calculator
# =============================================================================


class ThrottleAwareBackoffCalculator(BackoffCalculator):
    """
    AdaptiveThrottle 상태를 인식하는 Backoff 계산기.

    시스템 부하 상태에 따라 동적으로 재시도 간격을 조정합니다.
    Full Stop 시 즉시 DLQ 이동을 위한 신호를 반환합니다.
    """

    # 상태별 Backoff 배율
    BACKOFF_MULTIPLIERS: dict[str, float] = {
        "normal": 1.0,
        "sla_warning": 1.5,
        "sla_critical": 2.0,
        "emergency_1_2": 2.5,
        "emergency_3": 4.0,
        "error_budget_critical": 3.0,
    }

    def __init__(
        self,
        config: BackoffConfig | None = None,
        throttle_getter: Callable[[], Any] | None = None,
        enable_push_cache: bool = True,
        use_global_state: bool = False,
        service_name: str = "default",
        error_budget_check_enabled: bool = True,
    ):
        """
        초기화.

        Args:
            config: Backoff 설정
            throttle_getter: AdaptiveThrottle 인스턴스 getter (DI용)
            enable_push_cache: EventBus 푸시 캐싱 활성화 여부
            use_global_state: Redis 기반 글로벌 상태 사용 여부
            service_name: 서비스명 (ThrottleRegistry 연동용)
            error_budget_check_enabled: Error Budget 체크 활성화 여부 (테스트용)
        """
        super().__init__(config)
        self._throttle_getter = throttle_getter
        self._enable_push_cache = enable_push_cache
        self._use_global_state = use_global_state
        self._service_name = service_name
        self._error_budget_check_enabled = error_budget_check_enabled

        # 푸시 기반 캐시 (기본 활성화)
        self._state_cache = PushBasedThrottleStateCache()
        if enable_push_cache:
            self._subscribe_throttle_events()

        # 글로벌 상태 관리자
        self._global_state_manager = GlobalThrottleStateManager() if use_global_state else None

    def _subscribe_throttle_events(self) -> None:
        """Throttle 상태 변경 이벤트 구독 (기본 활성화)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(EventType.THROTTLE_LIMIT_CHANGED, self._on_throttle_changed)
            bus.subscribe(EventType.THROTTLE_SLA_WARNING, self._on_sla_warning)
            bus.subscribe(EventType.THROTTLE_SLA_CRITICAL, self._on_sla_critical)

            logger.debug("[ThrottleAwareBackoff] EventBus subscription enabled")
        except Exception as e:
            logger.warning(f"[ThrottleAwareBackoff] EventBus subscription failed: {e}")
            self._enable_push_cache = False  # 폴백: 직접 조회 모드

    def _on_throttle_changed(self, event: Any) -> None:
        """Throttle limit 변경 시 캐시 업데이트."""
        data = event.data if hasattr(event, "data") else event
        reason = data.get("reason", "")

        self._state_cache.last_updated = time.time()
        self._state_cache.full_stop_active = data.get("full_stop", False)

        if self._state_cache.full_stop_active:
            self._state_cache.multiplier = float("inf")
            self._state_cache.reason = "full_stop_active"
        elif "emergency" in reason:
            level = data.get("emergency_level", 1)
            self._state_cache.emergency_level = level
            self._state_cache.multiplier = 4.0 if level >= 3 else 2.5
            self._state_cache.reason = f"emergency_level_{level}"
        elif "sla_critical" in reason:
            self._state_cache.multiplier = 2.0
            self._state_cache.reason = "sla_critical"
        elif "sla_warning" in reason:
            self._state_cache.multiplier = 1.5
            self._state_cache.reason = "sla_warning"
        else:
            self._state_cache.multiplier = 1.0
            self._state_cache.reason = "normal"

    def _on_sla_warning(self, event: Any) -> None:
        """SLA Warning 이벤트 처리."""
        self._state_cache.last_updated = time.time()
        self._state_cache.multiplier = 1.5
        self._state_cache.reason = "sla_warning"

    def _on_sla_critical(self, event: Any) -> None:
        """SLA Critical 이벤트 처리."""
        self._state_cache.last_updated = time.time()
        self._state_cache.multiplier = 2.0
        self._state_cache.reason = "sla_critical"

    def _get_throttle(self) -> Any | None:
        """서비스별 AdaptiveThrottle 인스턴스 획득 (Fail-Open)."""
        if self._throttle_getter:
            return self._throttle_getter()

        # 서비스별 Throttle 사용 시도
        if self._service_name != "default":
            try:
                from selfhealing.services.throttle.registry import get_throttle_registry

                return get_throttle_registry().get_throttle(self._service_name)
            except Exception as e:
                logger.debug(f"[ThrottleAwareBackoff] Registry lookup failed: {e}")

        # 폴백: 전역 싱글톤
        try:
            from selfhealing.services.throttle.adaptive import get_adaptive_throttle

            return get_adaptive_throttle()
        except ImportError:
            return None
        except Exception:
            return None

    def _get_throttle_state(self) -> ThrottleState | None:
        """현재 Throttle 상태 스냅샷 획득."""
        throttle = self._get_throttle()
        if throttle is None:
            return None

        try:
            stats = throttle.get_stats()
            adaptive_stats = stats.get("adaptive", {})
            emergency_stats = stats.get("emergency", {})

            return ThrottleState(
                current_limit=stats.get("current_limit", 100),
                initial_limit=throttle.config.initial_limit,
                emergency_level=emergency_stats.get("level", 0),
                full_stop_active=emergency_stats.get("full_stop_active", False),
                sla_warning_active=adaptive_stats.get("sla_warnings", 0) > 0,
                sla_critical_active=adaptive_stats.get("sla_criticals", 0) > 0,
                recovery_dampening_active=stats.get("recovery", {}).get("dampening_active", False),
                error_budget_reduction_active=getattr(throttle, "_error_budget_limit_reduction_active", False),
            )
        except Exception as e:
            logger.debug(f"[ThrottleAwareBackoff] get_throttle_state failed: {e}")
            return None

    def _get_throttle_state_cached(self) -> tuple[float, str]:
        """캐시된 상태 반환 (stale 시 직접 조회 폴백)."""
        if self._enable_push_cache and not self._state_cache.is_stale():
            return self._state_cache.multiplier, self._state_cache.reason

        # 폴백: 직접 조회
        state = self._get_throttle_state()
        if state is None:
            return 1.0, "throttle_unavailable"

        multiplier = self._calculate_multiplier(state)
        reason = self._determine_reason(state)
        return multiplier, reason

    def _check_error_budget_critical_or_warning(self) -> bool:
        """
        ErrorBudgetGate CRITICAL 또는 WARNING 상태 확인.

        차단 직전 단계에서도 재시도 빈도를 낮추는 Soft-Landing 전략.
        """
        if not self._error_budget_check_enabled:
            return False

        try:
            from selfhealing.services.error_budget_gate import get_error_budget_gate
            from selfhealing.services.error_budget_gate.gate import GateStatus

            gate = get_error_budget_gate()
            result = gate.check()

            # WARNING 또는 BLOCKED 상태면 배율 적용
            return result.status in (GateStatus.WARNING, GateStatus.BLOCKED)
        except ImportError:
            return False
        except Exception as e:
            logger.debug(f"[ThrottleAwareBackoff] ErrorBudgetGate check failed: {e}")
            return False

    def _calculate_multiplier(self, state: ThrottleState) -> float:
        """상태 기반 Backoff 배율 계산."""
        # Full Stop: 최대 배율 (재시도 차단에 가까움)
        if state.full_stop_active:
            return float("inf")  # 무한대 → execute()에서 즉시 DLQ 이동

        # Error Budget Critical/Warning 우선 검사
        if self._check_error_budget_critical_or_warning():
            return self.BACKOFF_MULTIPLIERS["error_budget_critical"]

        # Emergency LEVEL_3
        if state.emergency_level >= 3:
            return self.BACKOFF_MULTIPLIERS["emergency_3"]

        # Emergency LEVEL_1~2
        if state.emergency_level > 0:
            return self.BACKOFF_MULTIPLIERS["emergency_1_2"]

        # Error Budget Reduction Active (별도 flag)
        if state.error_budget_reduction_active:
            return self.BACKOFF_MULTIPLIERS["error_budget_critical"]

        # SLA Critical
        if state.sla_critical_active:
            return self.BACKOFF_MULTIPLIERS["sla_critical"]

        # SLA Warning
        if state.sla_warning_active:
            return self.BACKOFF_MULTIPLIERS["sla_warning"]

        # 정상 상태
        return self.BACKOFF_MULTIPLIERS["normal"]

    def _determine_reason(self, state: ThrottleState) -> str:
        """상태에서 reason 문자열 결정."""
        if state.full_stop_active:
            return "full_stop_active"
        if state.emergency_level >= 3:
            return "emergency_level_3"
        if state.emergency_level > 0:
            return f"emergency_level_{state.emergency_level}"
        if state.error_budget_reduction_active:
            return "error_budget_critical"
        if state.sla_critical_active:
            return "sla_critical"
        if state.sla_warning_active:
            return "sla_warning"
        return "normal"

    def _calculate_global_multiplier(self, state: GlobalThrottleState) -> tuple[float, str]:
        """글로벌 상태 기반 배율 계산."""
        # 클러스터 과반수가 SLA Critical이면 2.0x
        if state.cluster_sla_critical_count > state.reporting_pod_count / 2:
            return 2.0, "cluster_sla_critical"

        # 클러스터 평균 Emergency Level 기반
        if state.cluster_emergency_level >= 3:
            return 4.0, "cluster_emergency_level_3"
        elif state.cluster_emergency_level > 0:
            return 2.5, f"cluster_emergency_level_{state.cluster_emergency_level}"

        return 1.0, "cluster_normal"

    def _get_effective_multiplier(self) -> tuple[float, str]:
        """로컬 또는 글로벌 상태 기반 배율 계산."""
        if self._use_global_state and self._global_state_manager:
            global_state = self._global_state_manager.get_global_state()
            if global_state:
                return self._calculate_global_multiplier(global_state)

        # 폴백: 로컬 상태
        return self._get_throttle_state_cached()

    def _record_backoff_metrics(
        self,
        domain: str,
        original_delay: int,
        adjusted_delay: int,
        multiplier: float,
        reason: str,
    ) -> None:
        """Prometheus 메트릭 기록."""
        try:
            from selfhealing.services.metrics.definitions import (
                retry_backoff_adjusted_seconds,
                retry_backoff_multiplier,
                retry_backoff_original_seconds,
                retry_throttle_full_stop_skips_total,
            )

            retry_backoff_multiplier.labels(domain=domain, reason=reason).observe(multiplier)
            retry_backoff_original_seconds.labels(domain=domain).observe(original_delay)
            retry_backoff_adjusted_seconds.labels(domain=domain).observe(adjusted_delay)

            if multiplier == float("inf"):
                retry_throttle_full_stop_skips_total.labels(domain=domain).inc()

        except ImportError:
            pass  # Fail-Open
        except Exception as e:
            logger.debug(f"[ThrottleAwareBackoff] Metrics recording failed: {e}")

    def calculate_with_throttle_context(
        self,
        attempt: int,
        with_jitter: bool = True,
    ) -> tuple[int, float, str]:
        """
        Throttle 상태를 고려한 Backoff 계산.

        Args:
            attempt: 재시도 횟수
            with_jitter: Jitter 적용 여부

        Returns:
            (adjusted_delay, multiplier, reason) 튜플.
            delay=-1은 Full Stop 즉시 DLQ 이동 신호.
        """
        base_delay = self.calculate(attempt, with_jitter)

        state = self._get_throttle_state()
        if state is None:
            return base_delay, 1.0, "throttle_unavailable"

        multiplier = self._calculate_multiplier(state)

        # Full Stop 시 무한대 → 특수 처리
        if multiplier == float("inf"):
            self._record_backoff_metrics(
                domain=self._service_name,
                original_delay=base_delay,
                adjusted_delay=-1,
                multiplier=multiplier,
                reason="full_stop_active",
            )
            return -1, float("inf"), "full_stop_active"

        adjusted_delay = int(base_delay * multiplier)

        # 시스템 타임아웃 기준 cap (임의적 2배 대신)
        if adjusted_delay > SYSTEM_TIMEOUT_SECONDS:
            logger.warning(
                f"[ThrottleAwareBackoff] Delay capped at SYSTEM_TIMEOUT: "
                f"original={base_delay * multiplier}s → {SYSTEM_TIMEOUT_SECONDS}s"
            )
            adjusted_delay = SYSTEM_TIMEOUT_SECONDS

        reason = self._determine_reason(state)

        # 메트릭 기록
        self._record_backoff_metrics(
            domain=self._service_name,
            original_delay=base_delay,
            adjusted_delay=adjusted_delay,
            multiplier=multiplier,
            reason=reason,
        )

        return adjusted_delay, multiplier, reason


def calculate_backoff(
    attempt: int,
    base: int = 4,
    max_delay: int = 180,
    jitter_percent: int = 25,
) -> int:
    """
    Convenience function to calculate backoff delay.

    Args:
        attempt: The attempt number (1-based)
        base: Base for exponential calculation
        max_delay: Maximum delay in seconds
        jitter_percent: Jitter percentage (0-100)

    Returns:
        Delay in seconds

    Example:
        >>> calculate_backoff(1)  # First retry
        4  # (approximately, with jitter)
        >>> calculate_backoff(2)  # Second retry
        16  # (approximately, with jitter)
    """
    config = BackoffConfig(
        base=base,
        max_delay=max_delay,
        jitter_percent=jitter_percent,
    )
    calculator = BackoffCalculator(config)
    return calculator.calculate(attempt)


# Domain-specific calculator instances
_calculators: dict[str, BackoffCalculator] = {}


def get_calculator_for_domain(domain: str) -> BackoffCalculator:
    """
    Get a BackoffCalculator configured for a specific domain.

    Caches calculator instances per domain for efficiency.

    Args:
        domain: Domain name (payment, webhook, notification, etc.)

    Returns:
        BackoffCalculator configured for the domain
    """
    if domain not in _calculators:
        config = BackoffConfig.from_settings(domain)
        _calculators[domain] = BackoffCalculator(config)
    return _calculators[domain]

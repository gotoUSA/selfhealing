"""
Canary Recovery

HALF_OPEN 상태에서 즉시 100% 트래픽을 보내는 대신,
점진적으로 트래픽을 늘려 Thundering Herd를 방지합니다.

상태 머신:
    OPEN → HALF_OPEN → CANARY_1(10%) → CANARY_2(30%) → CANARY_3(60%) → CLOSED(100%)
                                ↓              ↓              ↓
                            실패 시 OPEN으로 복귀 ──────────────┘
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.services.circuit_breaker.models import (
    CanaryRecoveryStageConfig,
    RecoveryStrategy,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Canary State
# =============================================================================


class CanaryRecoveryStage(str, Enum):
    """
    Canary Recovery 상태.

    HALF_OPEN 상태 이후 점진적 복구 단계를 나타냅니다.
    """

    NOT_IN_CANARY = "not_in_canary"  # Canary 복구 진행 중 아님
    CANARY_1 = "canary_1"  # Stage 1: 10% 트래픽
    CANARY_2 = "canary_2"  # Stage 2: 30% 트래픽
    CANARY_3 = "canary_3"  # Stage 3: 60% 트래픽
    CANARY_4 = "canary_4"  # Stage 4: 100% 트래픽 (CLOSED 직전)


# =============================================================================
# Canary Stage Metrics
# =============================================================================


@dataclass
class CanaryStageMetrics:
    """
    개별 Canary Stage의 메트릭.

    Attributes:
        stage: 현재 Canary 단계
        started_at: 단계 시작 시간
        total_requests: 총 요청 수
        success_count: 성공 요청 수
        failure_count: 실패 요청 수
        current_success_rate: 현재 성공률
    """

    stage: CanaryRecoveryStage
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    total_requests: int = 0
    success_count: int = 0
    failure_count: int = 0

    @property
    def current_success_rate(self) -> float:
        """현재 성공률 계산 (0~100%)."""
        if self.total_requests == 0:
            return 100.0  # 요청 없으면 100% 성공으로 간주
        return (self.success_count / self.total_requests) * 100.0

    def record_success(self) -> None:
        """성공 기록."""
        self.total_requests += 1
        self.success_count += 1

    def record_failure(self) -> None:
        """실패 기록."""
        self.total_requests += 1
        self.failure_count += 1

    def elapsed_seconds(self) -> float:
        """단계 시작 후 경과 시간 (초)."""
        return (datetime.now(timezone.utc) - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "stage": self.stage.value,
            "started_at": self.started_at.isoformat(),
            "total_requests": self.total_requests,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "current_success_rate": self.current_success_rate,
            "elapsed_seconds": self.elapsed_seconds(),
        }


# =============================================================================
# Canary Recovery State
# =============================================================================


@dataclass
class CanaryRecoveryState:
    """
    서비스별 Canary Recovery 상태.

    Attributes:
        service_id: 서비스 ID
        current_stage: 현재 Canary 단계
        stage_index: 현재 단계 인덱스 (0~3)
        metrics: 현재 단계의 메트릭
        recovery_started_at: 복구 시작 시간
        recovery_strategy: 적용된 복구 전략
        stage_history: 단계 이력
    """

    service_id: str
    current_stage: CanaryRecoveryStage = CanaryRecoveryStage.NOT_IN_CANARY
    stage_index: int = -1
    metrics: CanaryStageMetrics | None = None
    recovery_started_at: datetime | None = None
    recovery_strategy: RecoveryStrategy | None = None
    stage_history: list[dict[str, Any]] = field(default_factory=list)

    def start_recovery(self, strategy: RecoveryStrategy) -> None:
        """Canary 복구 시작."""
        self.current_stage = CanaryRecoveryStage.CANARY_1
        self.stage_index = 0
        self.recovery_started_at = datetime.now(timezone.utc)
        self.recovery_strategy = strategy
        self.metrics = CanaryStageMetrics(stage=CanaryRecoveryStage.CANARY_1)
        self.stage_history = []

    def advance_stage(self) -> bool:
        """
        다음 단계로 진행.

        Returns:
            True if advanced, False if already at last stage
        """
        if self.recovery_strategy is None:
            return False

        # 현재 메트릭을 히스토리에 추가
        if self.metrics:
            self.stage_history.append(self.metrics.to_dict())

        # 다음 단계로 진행
        next_index = self.stage_index + 1

        if next_index >= len(self.recovery_strategy.canary_stages):
            # 모든 단계 완료 → CLOSED
            self.current_stage = CanaryRecoveryStage.NOT_IN_CANARY
            self.stage_index = -1
            self.metrics = None
            return False

        # 다음 Canary 단계 설정
        stage_names = [
            CanaryRecoveryStage.CANARY_1,
            CanaryRecoveryStage.CANARY_2,
            CanaryRecoveryStage.CANARY_3,
            CanaryRecoveryStage.CANARY_4,
        ]
        self.stage_index = next_index
        self.current_stage = stage_names[min(next_index, len(stage_names) - 1)]
        self.metrics = CanaryStageMetrics(stage=self.current_stage)
        return True

    def reset(self) -> None:
        """Canary 상태 초기화 (실패 시)."""
        if self.metrics:
            self.stage_history.append(
                {
                    **self.metrics.to_dict(),
                    "result": "failed",
                }
            )

        self.current_stage = CanaryRecoveryStage.NOT_IN_CANARY
        self.stage_index = -1
        self.metrics = None
        self.recovery_started_at = None

    def is_in_canary(self) -> bool:
        """Canary 복구 진행 중인지 확인."""
        return self.current_stage != CanaryRecoveryStage.NOT_IN_CANARY

    def get_current_config(self) -> CanaryRecoveryStageConfig | None:
        """현재 단계의 설정 반환."""
        if self.recovery_strategy is None or self.stage_index < 0:
            return None
        if self.stage_index >= len(self.recovery_strategy.canary_stages):
            return None
        return self.recovery_strategy.canary_stages[self.stage_index]

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "service_id": self.service_id,
            "current_stage": self.current_stage.value,
            "stage_index": self.stage_index,
            "is_in_canary": self.is_in_canary(),
            "metrics": self.metrics.to_dict() if self.metrics else None,
            "recovery_started_at": (self.recovery_started_at.isoformat() if self.recovery_started_at else None),
            "stage_history": self.stage_history,
        }


# =============================================================================
# Canary Decision
# =============================================================================


@dataclass
class CanaryRecoveryDecision:
    """
    Canary 요청 허용 결정 결과.

    Attributes:
        allow_backend: 백엔드 호출 허용 여부
        is_canary_request: Canary 요청 여부 (메트릭 추적 대상)
        use_stale_cache: Stale Cache 사용 여부
        current_stage: 현재 Canary 단계
        traffic_percent: 현재 단계 트래픽 비율
        reason: 결정 사유
    """

    allow_backend: bool = False
    is_canary_request: bool = False
    use_stale_cache: bool = False
    current_stage: CanaryRecoveryStage | None = None
    traffic_percent: float = 0.0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "allow_backend": self.allow_backend,
            "is_canary_request": self.is_canary_request,
            "use_stale_cache": self.use_stale_cache,
            "current_stage": self.current_stage.value if self.current_stage else None,
            "traffic_percent": self.traffic_percent,
            "reason": self.reason,
        }


# =============================================================================
# Canary Stage Transition Result
# =============================================================================


@dataclass
class CanaryStageTransitionResult:
    """
    Canary 단계 전이 결과.

    Attributes:
        transitioned: 단계가 전이되었는지
        previous_stage: 이전 단계
        new_stage: 새 단계
        success_rate: 전이 시점 성공률
        reason: 전이 사유
        completed: 모든 단계 완료 여부 (CLOSED로 전환)
        failed: 실패로 OPEN 복귀 여부
    """

    transitioned: bool = False
    previous_stage: CanaryRecoveryStage | None = None
    new_stage: CanaryRecoveryStage | None = None
    success_rate: float = 0.0
    reason: str = ""
    completed: bool = False
    failed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "transitioned": self.transitioned,
            "previous_stage": (self.previous_stage.value if self.previous_stage else None),
            "new_stage": self.new_stage.value if self.new_stage else None,
            "success_rate": self.success_rate,
            "reason": self.reason,
            "completed": self.completed,
            "failed": self.failed,
        }


# =============================================================================
# Canary Recovery Manager
# =============================================================================


class CanaryRecoveryManager:
    """
    Canary Recovery 관리자.

    HALF_OPEN 상태에서 점진적으로 트래픽을 늘려가며 서비스 복구를 관리합니다.

    상태 머신:
        HALF_OPEN → CANARY_1(10%) → CANARY_2(30%) → CANARY_3(60%) → CANARY_4(100%) → CLOSED
                        ↓              ↓              ↓              ↓
                    실패 시 ────────────────────────────────────────────→ OPEN

    Usage:
        manager = CanaryRecoveryManager()

        # HALF_OPEN 진입 시 Canary 시작
        manager.start_canary_recovery("payment-api")

        # 요청마다 결정
        decision = manager.should_allow_request("payment-api")
        if decision.allow_backend:
            try:
                result = call_backend()
                manager.record_success("payment-api")
            except:
                manager.record_failure("payment-api")
    """

    _instance: CanaryRecoveryManager | None = None
    _lock: threading.Lock = threading.Lock()

    def __new__(cls) -> CanaryRecoveryManager:
        """싱글톤 패턴 구현."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        default_strategy: RecoveryStrategy | None = None,
        on_stage_advanced: Callable[[str, CanaryStageTransitionResult], None] | None = None,
        on_recovery_completed: Callable[[str, dict[str, Any]], None] | None = None,
        on_recovery_failed: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        """
        초기화.

        Args:
            default_strategy: 기본 복구 전략
            on_stage_advanced: 단계 전이 시 콜백
            on_recovery_completed: 복구 완료 시 콜백
            on_recovery_failed: 복구 실패 시 콜백
        """
        if getattr(self, "_initialized", False):
            return

        self._default_strategy = default_strategy or RecoveryStrategy()
        self._recovery_states: dict[str, CanaryRecoveryState] = {}
        self._service_strategies: dict[str, RecoveryStrategy] = {}
        self._state_lock = threading.RLock()

        # 콜백
        self._on_stage_advanced = on_stage_advanced
        self._on_recovery_completed = on_recovery_completed
        self._on_recovery_failed = on_recovery_failed

        self._initialized = True

    # =========================================================================
    # Configuration
    # =========================================================================

    def set_default_strategy(self, strategy: RecoveryStrategy) -> None:
        """기본 복구 전략 설정."""
        self._default_strategy = strategy

    def set_service_strategy(self, service_id: str, strategy: RecoveryStrategy) -> None:
        """서비스별 복구 전략 설정."""
        with self._state_lock:
            self._service_strategies[service_id] = strategy

    def get_strategy(self, service_id: str) -> RecoveryStrategy:
        """서비스 복구 전략 조회."""
        with self._state_lock:
            return self._service_strategies.get(service_id, self._default_strategy)

    # =========================================================================
    # Canary Recovery Control
    # =========================================================================

    def start_canary_recovery(
        self,
        service_id: str,
        strategy: RecoveryStrategy | None = None,
    ) -> CanaryRecoveryState:
        """
        Canary 복구 시작 (HALF_OPEN 진입 시 호출).

        Args:
            service_id: 서비스 ID
            strategy: 복구 전략 (없으면 기본값 또는 서비스별 설정 사용)

        Returns:
            CanaryRecoveryState: 생성된 복구 상태
        """
        with self._state_lock:
            effective_strategy = strategy or self.get_strategy(service_id)

            # immediate 전략이면 Canary 사용 안 함
            if effective_strategy.type == "immediate":
                logger.info(f"[CanaryRecovery] {service_id}: immediate strategy, skipping canary")
                return CanaryRecoveryState(service_id=service_id)

            # Canary 복구 상태 생성
            state = CanaryRecoveryState(service_id=service_id)
            state.start_recovery(effective_strategy)
            self._recovery_states[service_id] = state

            logger.info(
                f"[CanaryRecovery] {service_id}: Started canary recovery, "
                f"stage={state.current_stage.value}, "
                f"traffic={effective_strategy.canary_stages[0].traffic_percent}%"
            )

            return state

    def stop_canary_recovery(self, service_id: str, reason: str = "manual") -> bool:
        """
        Canary 복구 중단.

        Args:
            service_id: 서비스 ID
            reason: 중단 사유

        Returns:
            True if stopped, False if not in recovery
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)
            if state is None or not state.is_in_canary():
                return False

            state.reset()
            logger.info(f"[CanaryRecovery] {service_id}: Stopped canary recovery, reason={reason}")
            return True

    def get_recovery_state(self, service_id: str) -> CanaryRecoveryState | None:
        """
        서비스의 Canary 복구 상태 조회.

        Args:
            service_id: 서비스 ID

        Returns:
            CanaryRecoveryState or None if not in recovery
        """
        with self._state_lock:
            return self._recovery_states.get(service_id)

    def is_in_canary_recovery(self, service_id: str) -> bool:
        """
        서비스가 Canary 복구 중인지 확인.

        Args:
            service_id: 서비스 ID

        Returns:
            True if in canary recovery
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)
            return state is not None and state.is_in_canary()

    # =========================================================================
    # Request Decision
    # =========================================================================

    def should_allow_request(self, service_id: str) -> CanaryRecoveryDecision:
        """
        요청 허용 결정 (Canary 비율 적용).

        Args:
            service_id: 서비스 ID

        Returns:
            CanaryRecoveryDecision: 요청 허용 결정
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)

            # Canary 복구 중이 아니면 전부 허용
            if state is None or not state.is_in_canary():
                return CanaryRecoveryDecision(
                    allow_backend=True,
                    is_canary_request=False,
                    reason="not in canary recovery",
                )

            # 현재 단계 설정 가져오기
            stage_config = state.get_current_config()
            if stage_config is None:
                return CanaryRecoveryDecision(
                    allow_backend=True,
                    is_canary_request=False,
                    reason="no stage config",
                )

            # 확률 기반 Canary 선택
            is_canary = random.random() * 100 < stage_config.traffic_percent

            if is_canary:
                return CanaryRecoveryDecision(
                    allow_backend=True,
                    is_canary_request=True,
                    use_stale_cache=False,
                    current_stage=state.current_stage,
                    traffic_percent=stage_config.traffic_percent,
                    reason=f"canary request ({stage_config.traffic_percent}%)",
                )
            else:
                return CanaryRecoveryDecision(
                    allow_backend=False,
                    is_canary_request=False,
                    use_stale_cache=True,
                    current_stage=state.current_stage,
                    traffic_percent=stage_config.traffic_percent,
                    reason=f"non-canary request, use stale cache ({100 - stage_config.traffic_percent}%)",
                )

    # =========================================================================
    # Metrics Recording
    # =========================================================================

    def record_success(self, service_id: str) -> CanaryStageTransitionResult | None:
        """
        성공 기록 및 단계 전이 확인.

        Args:
            service_id: 서비스 ID

        Returns:
            CanaryStageTransitionResult if stage transition occurred
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)
            if state is None or not state.is_in_canary() or state.metrics is None:
                return None

            state.metrics.record_success()
            return self._check_stage_transition(service_id, state)

    def record_failure(self, service_id: str) -> CanaryStageTransitionResult | None:
        """
        실패 기록 및 복구 실패 확인.

        Args:
            service_id: 서비스 ID

        Returns:
            CanaryStageTransitionResult if recovery failed
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)
            if state is None or not state.is_in_canary() or state.metrics is None:
                return None

            state.metrics.record_failure()
            return self._check_stage_transition(service_id, state)

    def _check_stage_transition(
        self,
        service_id: str,
        state: CanaryRecoveryState,
    ) -> CanaryStageTransitionResult | None:
        """
        단계 전이 조건 확인 및 처리.

        전이 조건:
        1. 단계 유지 시간(duration_seconds) 경과
        2. 성공률이 required_success_rate 이상

        실패 조건:
        - 성공률이 required_success_rate 미만이고 충분한 샘플(10개 이상)
        """
        if state.metrics is None or state.recovery_strategy is None:
            return None

        stage_config = state.get_current_config()
        if stage_config is None:
            return None

        metrics = state.metrics
        previous_stage = state.current_stage

        # 최소 샘플 수 확인
        min_samples = 5
        if metrics.total_requests < min_samples:
            return None  # 아직 판단하기 이름

        # 성공률 확인
        success_rate = metrics.current_success_rate
        required_rate = stage_config.required_success_rate

        # strict_mode면 100% 요구
        if state.recovery_strategy.strict_mode:
            required_rate = 100.0

        # 실패 조건: 성공률 미달 (10개 이상 샘플)
        if metrics.total_requests >= 10 and success_rate < required_rate:
            # 복구 실패 → OPEN 복귀
            result = CanaryStageTransitionResult(
                transitioned=True,
                previous_stage=previous_stage,
                new_stage=None,
                success_rate=success_rate,
                reason=f"success rate {success_rate:.1f}% < required {required_rate:.1f}%",
                failed=True,
            )

            state.reset()

            logger.warning(
                f"[CanaryRecovery] {service_id}: Recovery FAILED at {previous_stage.value}, "
                f"success_rate={success_rate:.1f}% (required={required_rate:.1f}%)"
            )

            if self._on_recovery_failed:
                self._on_recovery_failed(service_id, result.to_dict())

            return result

        # 성공 조건: 시간 경과 + 성공률 충족
        elapsed = metrics.elapsed_seconds()
        if elapsed >= stage_config.duration_seconds and success_rate >= required_rate:
            # 다음 단계로 진행
            if state.advance_stage():
                new_stage = state.current_stage
                result = CanaryStageTransitionResult(
                    transitioned=True,
                    previous_stage=previous_stage,
                    new_stage=new_stage,
                    success_rate=success_rate,
                    reason=f"advanced after {elapsed:.1f}s with {success_rate:.1f}% success rate",
                )

                logger.info(
                    f"[CanaryRecovery] {service_id}: Advanced {previous_stage.value} → {new_stage.value}, "
                    f"success_rate={success_rate:.1f}%"
                )

                if self._on_stage_advanced:
                    self._on_stage_advanced(service_id, result)

                return result
            else:
                # 모든 단계 완료 → CLOSED
                result = CanaryStageTransitionResult(
                    transitioned=True,
                    previous_stage=previous_stage,
                    new_stage=None,
                    success_rate=success_rate,
                    reason="all stages completed, ready for CLOSED",
                    completed=True,
                )

                logger.info(f"[CanaryRecovery] {service_id}: Recovery COMPLETED, " f"final success_rate={success_rate:.1f}%")

                if self._on_recovery_completed:
                    self._on_recovery_completed(service_id, state.to_dict())

                return result

        return None

    # =========================================================================
    # Status & Diagnostics
    # =========================================================================

    def get_all_recovery_states(self) -> dict[str, dict[str, Any]]:
        """모든 서비스의 Canary 복구 상태 조회."""
        with self._state_lock:
            return {service_id: state.to_dict() for service_id, state in self._recovery_states.items()}

    def get_active_recoveries(self) -> list[str]:
        """현재 Canary 복구 중인 서비스 목록."""
        with self._state_lock:
            return [service_id for service_id, state in self._recovery_states.items() if state.is_in_canary()]

    def get_recovery_stats(self, service_id: str) -> dict[str, Any] | None:
        """
        서비스의 Canary 복구 통계.

        Args:
            service_id: 서비스 ID

        Returns:
            복구 통계 딕셔너리
        """
        with self._state_lock:
            state = self._recovery_states.get(service_id)
            if state is None:
                return None

            return state.to_dict()

    def reset(self) -> None:
        """모든 상태 초기화."""
        with self._state_lock:
            self._recovery_states.clear()
            self._service_strategies.clear()
            logger.info("[CanaryRecovery] All states reset")


# =============================================================================
# Module-level Singleton Functions
# =============================================================================


_manager_instance: CanaryRecoveryManager | None = None
_manager_lock = threading.Lock()


def get_canary_recovery_manager() -> CanaryRecoveryManager:
    """싱글톤 CanaryRecoveryManager 인스턴스 반환."""
    global _manager_instance
    if _manager_instance is None:
        with _manager_lock:
            if _manager_instance is None:
                _manager_instance = CanaryRecoveryManager()
    return _manager_instance


def reset_canary_recovery_manager() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _manager_instance
    with _manager_lock:
        if _manager_instance is not None:
            _manager_instance.reset()
        _manager_instance = None
        CanaryRecoveryManager._instance = None


# =============================================================================
# Convenience Functions
# =============================================================================


def start_canary_recovery(
    service_id: str,
    strategy: RecoveryStrategy | None = None,
) -> CanaryRecoveryState:
    """Canary 복구 시작."""
    return get_canary_recovery_manager().start_canary_recovery(service_id, strategy)


def stop_canary_recovery(service_id: str, reason: str = "manual") -> bool:
    """Canary 복구 중단."""
    return get_canary_recovery_manager().stop_canary_recovery(service_id, reason)


def is_in_canary_recovery(service_id: str) -> bool:
    """Canary 복구 중인지 확인."""
    return get_canary_recovery_manager().is_in_canary_recovery(service_id)


def canary_should_allow_request(service_id: str) -> CanaryRecoveryDecision:
    """Canary 요청 허용 결정."""
    return get_canary_recovery_manager().should_allow_request(service_id)


def canary_record_success(service_id: str) -> CanaryStageTransitionResult | None:
    """Canary 성공 기록."""
    return get_canary_recovery_manager().record_success(service_id)


def canary_record_failure(service_id: str) -> CanaryStageTransitionResult | None:
    """Canary 실패 기록."""
    return get_canary_recovery_manager().record_failure(service_id)


def get_canary_recovery_state(service_id: str) -> CanaryRecoveryState | None:
    """Canary 복구 상태 조회."""
    return get_canary_recovery_manager().get_recovery_state(service_id)

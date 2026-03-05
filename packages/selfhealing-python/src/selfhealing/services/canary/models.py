"""
Canary Rollout Data Models.

설정 변경의 점진적 배포를 위한 데이터 모델 정의.

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

State Transitions:
    CREATED ──▶ CANARY ──▶ PROMOTING ──▶ COMPLETED
       │          │           │
       │          ▼           ▼
       │       PAUSED     ROLLED_BACK
       │          │
       ▼          ▼
    CANCELLED  FAILED
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any

from selfhealing.utils.time import utc_now


class PauseTriggerPriority(IntEnum):
    """
    Pause 트리거 우선순위.

    높은 값이 더 우선 (근본 원인에 가까움).
    동시 발생 시 가장 높은 우선순위 사유만 기록.
    """

    METRICS = 100  # 직접적 장애 (에러율/레이턴시)
    INTERLOCK = 90  # Safety Interlock
    ERROR_BUDGET = 80  # 거버넌스 (에러 예산)
    GOVERNANCE = 75  # 거버넌스 체크 실패 (Kill Switch, Emergency 등)
    CHAOS_GUARD = 70  # Chaos 실험 충돌
    MANUAL = 10  # 수동 중지


# triggered_by 값 → 우선순위 매핑
TRIGGER_PRIORITY_MAP: dict[str, int] = {
    "metrics": PauseTriggerPriority.METRICS,
    "interlock": PauseTriggerPriority.INTERLOCK,
    "error_budget": PauseTriggerPriority.ERROR_BUDGET,
    "governance": PauseTriggerPriority.GOVERNANCE,
    "chaos_guard": PauseTriggerPriority.CHAOS_GUARD,
    "manual": PauseTriggerPriority.MANUAL,
}

# Zombie 판정에서 제외할 pause_triggered_by 값 목록
ZOMBIE_EXEMPT_TRIGGERS: list[str] = ["error_budget", "governance"]


class CanaryState(str, Enum):
    """
    Canary 롤아웃 상태.

    상태 전이:
    - CREATED: 초기 상태, 아직 시작되지 않음
    - CANARY: 일부 클러스터에 적용 중
    - PROMOTING: 다음 단계로 프로모션 중
    - PAUSED: 일시 중지 (수동 또는 자동)
    - COMPLETED: 모든 클러스터에 성공적으로 적용 완료
    - ROLLED_BACK: 롤백 완료
    - FAILED: 실패 상태
    - CANCELLED: 취소됨
    """

    CREATED = "created"
    CANARY = "canary"
    PROMOTING = "promoting"
    PAUSED = "paused"
    COMPLETED = "completed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class PassCriteria:
    """
    자동 프로모션을 위한 합격 기준 (임계값 DTO).

    판정 로직은 LiveCanaryEvaluator가 담당한다.
    이 클래스는 임계값 데이터만 보유한다.
    """

    # 에러율 관련
    error_rate_absolute_max: float = 0.05  # 5% 절대 한계
    error_rate_increase_max: float = 0.01  # 1% 증가 한계

    # 레이턴시 관련
    latency_p95_delta_ms: float = 50.0  # p95 50ms 증가 한계
    latency_p99_delta_pct: float = 0.2  # p99 20% 증가 한계

    # Error Budget 관련
    error_budget_drain_rate_max: float = 1.2  # 1.2x 소진률 한계
    error_budget_remaining_min: float = 0.1  # 10% 이상 남아있어야 함

    # 평가 기간
    min_requests_required: int = 100  # 최소 샘플 수
    evaluation_window_seconds: int = 300  # 5분 윈도우

    @classmethod
    def for_tier(cls, tier_id: str) -> "PassCriteria":
        """
        티어별 기본 PassCriteria 반환.

        Args:
            tier_id: "critical" | "standard" | "non_essential"

        Returns:
            해당 티어의 기본 PassCriteria
        """
        _TIER_DEFAULTS: dict[str, dict] = {
            "critical": {
                "error_budget_drain_rate_max": 0.8,
                "error_budget_remaining_min": 0.15,
                "error_rate_absolute_max": 0.03,
            },
            "standard": {
                "error_budget_drain_rate_max": 1.2,
                "error_budget_remaining_min": 0.10,
                "error_rate_absolute_max": 0.05,
            },
            "non_essential": {
                "error_budget_drain_rate_max": 2.0,
                "error_budget_remaining_min": 0.05,
                "error_rate_absolute_max": 0.10,
            },
        }
        overrides = _TIER_DEFAULTS.get(tier_id, {})
        return cls(**overrides)


@dataclass
class CanaryStage:
    """
    Canary 단계 정의.

    하나의 롤아웃은 여러 단계로 구성되며, 각 단계는 특정 클러스터들에
    설정을 적용합니다.

    Example:
        stage1 = CanaryStage(
            name="canary",
            clusters=["seoul-canary"],
            percentage=10.0,
            duration_minutes=5,
        )
    """

    name: str  # 단계 이름 (예: "canary", "50%", "full")
    clusters: list[str]  # 이 단계에 포함된 클러스터들
    percentage: float  # 전체 중 몇 %인지 (참고용)
    duration_minutes: int = 5  # 이 단계 유지 시간

    # 자동 프로모션 조건
    auto_promote: bool = True
    pass_criteria: PassCriteria = field(default_factory=PassCriteria)

    # 레거시 필드 (하위 호환성, pass_criteria로 대체 권장)
    error_rate_threshold: float = 0.05  # 5% 초과 시 중단
    latency_increase_threshold: float = 0.5  # 50% 증가 시 중단


@dataclass
class CanaryMetrics:
    """
    Canary 단계의 메트릭.

    설정 변경 전후의 메트릭을 비교하여 건강 상태를 판정합니다.
    """

    cluster: str
    stage_name: str

    # 에러율
    error_rate_before: float = 0.0
    error_rate_after: float = 0.0

    # 레이턴시
    latency_p50_before: float = 0.0
    latency_p50_after: float = 0.0
    latency_p99_before: float = 0.0
    latency_p99_after: float = 0.0

    # 트래픽
    requests_total: int = 0
    errors_total: int = 0

    # 판정
    is_healthy: bool = True
    unhealthy_reason: str | None = None


@dataclass
class CanaryRollout:
    """
    Canary 롤아웃 정보.

    하나의 설정 변경에 대한 전체 롤아웃 계획 및 상태.

    Example:
        rollout = CanaryRollout(
            id="abc12345",
            config_type="circuit_breaker",
            previous_values={"failure_threshold": 5},
            new_values={"failure_threshold": 3},
            stages=[
                CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10.0),
                CanaryStage(name="50%", clusters=["seoul-main"], percentage=50.0),
                CanaryStage(name="full", clusters=["tokyo", "singapore"], percentage=100.0),
            ],
            created_by="admin@example.com",
            reason="Reduce failure threshold for faster detection",
        )
    """

    id: str
    config_type: str  # circuit_breaker, dlq, retry 등

    # 설정값
    previous_values: dict[str, Any]
    new_values: dict[str, Any]

    # 상태
    state: CanaryState = CanaryState.CREATED
    current_stage_index: int = 0

    # 단계 정의
    stages: list[CanaryStage] = field(default_factory=list)

    # 메타데이터
    created_by: str = ""
    created_at: datetime = field(default_factory=utc_now)
    reason: str = ""

    # 결과
    completed_at: datetime | None = None
    rollback_reason: str | None = None

    # PAUSED 상태 사유 추적
    pause_reason: str | None = None
    """일시 중지 사유 (예: 'Error budget below threshold (5.0% < 10.0%)')."""

    pause_triggered_by: str | None = None
    """
    일시 중지 트리거 유형.

    Values:
    - "interlock": Safety Interlock에 의해 자동 중지
    - "manual": 운영자 수동 중지
    - "chaos_guard": Chaos Guard에 의해 중지
    - "metrics": 메트릭 악화로 인한 중지
    - "error_budget": 에러 예산 부족으로 중지
    - "governance": 거버넌스 체크 실패로 중지
    """

    paused_at: datetime | None = None
    """일시 중지 시각."""

    @property
    def current_stage(self) -> CanaryStage | None:
        """현재 단계 반환."""
        if 0 <= self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None

    @property
    def affected_clusters(self) -> list[str]:
        """현재까지 적용된 클러스터 목록."""
        clusters = []
        for i in range(self.current_stage_index + 1):
            if i < len(self.stages):
                clusters.extend(self.stages[i].clusters)
        return clusters

    @property
    def is_terminal(self) -> bool:
        """종료 상태인지 확인."""
        return self.state in (
            CanaryState.COMPLETED,
            CanaryState.ROLLED_BACK,
            CanaryState.FAILED,
            CanaryState.CANCELLED,
        )

    @property
    def progress_percentage(self) -> float:
        """롤아웃 진행률 (0.0 ~ 100.0)."""
        if not self.stages:
            return 0.0
        if self.state == CanaryState.COMPLETED:
            return 100.0
        if self.state == CanaryState.CREATED:
            return 0.0
        # 현재 단계까지의 percentage 합계
        return sum(
            stage.percentage
            for i, stage in enumerate(self.stages)
            if i <= self.current_stage_index
        )


def apply_tier_floor(user_criteria: PassCriteria, tier_id: str) -> PassCriteria:
    """
    사용자 기준과 티어 하한 중 더 엄격한 값을 적용.

    "max" 필드: min(user, tier) → 더 작은 값이 더 엄격
    "min" 필드: max(user, tier) → 더 큰 값이 더 엄격

    Args:
        user_criteria: 사용자가 지정한 PassCriteria
        tier_id: "critical" | "standard" | "non_essential"

    Returns:
        티어 하한이 적용된 새 PassCriteria
    """
    tier_floor = PassCriteria.for_tier(tier_id)

    return PassCriteria(
        # max 필드: 더 작은 값 = 더 엄격
        error_rate_absolute_max=min(
            user_criteria.error_rate_absolute_max,
            tier_floor.error_rate_absolute_max,
        ),
        error_rate_increase_max=min(
            user_criteria.error_rate_increase_max,
            tier_floor.error_rate_increase_max,
        ),
        error_budget_drain_rate_max=min(
            user_criteria.error_budget_drain_rate_max,
            tier_floor.error_budget_drain_rate_max,
        ),
        # min 필드: 더 큰 값 = 더 엄격
        error_budget_remaining_min=max(
            user_criteria.error_budget_remaining_min,
            tier_floor.error_budget_remaining_min,
        ),
        min_requests_required=max(
            user_criteria.min_requests_required,
            tier_floor.min_requests_required,
        ),
        # 사용자 값 유지 (티어와 무관)
        latency_p95_delta_ms=user_criteria.latency_p95_delta_ms,
        latency_p99_delta_pct=user_criteria.latency_p99_delta_pct,
        evaluation_window_seconds=user_criteria.evaluation_window_seconds,
    )

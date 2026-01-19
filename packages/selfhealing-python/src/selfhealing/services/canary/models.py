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
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from selfhealing.utils.time import utc_now


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
    자동 프로모션을 위한 합격 기준.

    모든 조건을 만족해야 프로모션 허용.
    Reference: SafetyGuard 패턴 (chaos/safety_guard/guard.py)
    """

    # 에러율 관련
    error_rate_absolute_max: float = 0.05      # 5% 절대 한계
    error_rate_increase_max: float = 0.01      # 1% 증가 한계

    # 레이턴시 관련
    latency_p95_delta_ms: float = 50.0         # p95 50ms 증가 한계
    latency_p99_delta_pct: float = 0.2         # p99 20% 증가 한계

    # Error Budget 관련
    error_budget_drain_rate_max: float = 1.2   # 1.2x 소진률 한계
    error_budget_remaining_min: float = 0.1    # 10% 이상 남아있어야 함

    # 평가 기간
    min_requests_required: int = 100           # 최소 샘플 수
    evaluation_window_seconds: int = 300       # 5분 윈도우

    def evaluate(self, metrics: "CanaryMetrics") -> Tuple[bool, Optional[str]]:
        """
        메트릭 평가.

        Args:
            metrics: 평가할 CanaryMetrics 객체

        Returns:
            (합격 여부, 실패 사유). 합격시 사유는 None.
        """
        # 최소 샘플 수 확인
        if metrics.requests_total < self.min_requests_required:
            return True, None  # 샘플 부족 - 통과 (보수적)

        # 에러율 절대값 검사
        if metrics.error_rate_after > self.error_rate_absolute_max:
            return False, (
                f"Error rate {metrics.error_rate_after:.2%} exceeds "
                f"{self.error_rate_absolute_max:.2%}"
            )

        # 에러율 증가분 검사
        error_increase = metrics.error_rate_after - metrics.error_rate_before
        if error_increase > self.error_rate_increase_max:
            return False, (
                f"Error rate increased by {error_increase:.2%} "
                f"(max: {self.error_rate_increase_max:.2%})"
            )

        # p99 레이턴시 검사
        if metrics.latency_p99_before > 0:
            latency_pct = (
                (metrics.latency_p99_after - metrics.latency_p99_before) /
                metrics.latency_p99_before
            )
            if latency_pct > self.latency_p99_delta_pct:
                return False, (
                    f"p99 latency increased by {latency_pct:.1%} "
                    f"(max: {self.latency_p99_delta_pct:.1%})"
                )

        return True, None


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
    name: str                         # 단계 이름 (예: "canary", "50%", "full")
    clusters: List[str]               # 이 단계에 포함된 클러스터들
    percentage: float                 # 전체 중 몇 %인지 (참고용)
    duration_minutes: int = 5         # 이 단계 유지 시간

    # 자동 프로모션 조건
    auto_promote: bool = True
    pass_criteria: PassCriteria = field(default_factory=PassCriteria)

    # 레거시 필드 (하위 호환성, pass_criteria로 대체 권장)
    error_rate_threshold: float = 0.05        # 5% 초과 시 중단
    latency_increase_threshold: float = 0.5   # 50% 증가 시 중단


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
    unhealthy_reason: Optional[str] = None


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
    previous_values: Dict[str, Any]
    new_values: Dict[str, Any]

    # 상태
    state: CanaryState = CanaryState.CREATED
    current_stage_index: int = 0

    # 단계 정의
    stages: List[CanaryStage] = field(default_factory=list)

    # 메타데이터
    created_by: str = ""
    created_at: datetime = field(default_factory=utc_now)
    reason: str = ""

    # 결과
    completed_at: Optional[datetime] = None
    rollback_reason: Optional[str] = None

    @property
    def current_stage(self) -> Optional[CanaryStage]:
        """현재 단계 반환."""
        if 0 <= self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None

    @property
    def affected_clusters(self) -> List[str]:
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

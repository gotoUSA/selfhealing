"""
Hedging Configuration - 헷징 전략 설정 및 후보 정의.

헷징 모드(IMMEDIATE/DELAYED/ADAPTIVE), 타임아웃, 딜레이 등의 설정과
실행할 후보 함수를 정의합니다. Backpressure 연동 설정도 포함합니다.

.. note::
    bulkhead_name/acquire_bulkhead_per_candidate 필드는 deprecated 됩니다.
    HedgingPolicy의 per_candidate_policy/overall_policy를 사용하세요.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class HedgingMode(str, Enum):
    """
    헷징 실행 모드.

    IMMEDIATE: 모든 후보를 즉시 동시 실행
    DELAYED: Primary 먼저 실행, 응답 지연 시 Secondary 추가 실행
    ADAPTIVE: 과거 지연시간 기반으로 동적 delay 결정
    """

    IMMEDIATE = "immediate"
    """모든 후보를 즉시 동시 실행."""

    DELAYED = "delayed"
    """Primary 응답 지연 시 Secondary 실행."""

    ADAPTIVE = "adaptive"
    """과거 지연시간 기반 동적 결정."""


@dataclass
class HedgingConfig:
    """
    헷징 설정.

    헷징 전략의 동작을 제어하는 모든 설정을 포함합니다.
    Bulkhead, Backpressure 연동 및 재시도 불가 예외 설정도 포함합니다.
    """

    mode: HedgingMode = HedgingMode.DELAYED
    """헷징 모드."""

    timeout: float = 5.0
    """전체 타임아웃 (초)."""

    delay: float = 0.1
    """DELAYED 모드에서 Secondary 실행 전 대기 시간 (초)."""

    max_candidates: int = 3
    """최대 동시 실행 후보 수."""

    cancel_on_success: bool = True
    """첫 성공 시 나머지 작업 취소."""

    require_idempotent: bool = True
    """멱등 작업만 허용 (경고 표시용)."""

    # =========================================================================
    # Bulkhead 연동 설정 (deprecated — HedgingPolicy의 per_candidate_policy/overall_policy 사용 권장)
    # =========================================================================
    bulkhead_name: str | None = field(
        default=None,
        metadata={"deprecated": "Use per_candidate_policy or overall_policy instead"},
    )
    """
    헷징 요청이 사용할 격벽 이름. None이면 격벽 미사용.

    .. deprecated:: 2.0
        HedgingPolicy의 overall_policy=BulkheadPolicy(...)를 사용하세요.
    """

    acquire_bulkhead_per_candidate: bool = field(
        default=False,
        metadata={"deprecated": "Use per_candidate_policy instead"},
    )
    """
    True: 각 후보마다 격벽 획득 (도메인별 격리 강화).
    False: 전체 헷징에 대해 1회만 격벽 획득 (기본값, 리소스 효율).

    .. deprecated:: 2.0
        HedgingPolicy의 per_candidate_policy=BulkheadPolicy(...)를 사용하세요.
    """

    # =========================================================================
    # Backpressure 연동 설정 (부하 기반 동적 제어)
    # =========================================================================
    disable_on_load_level: str = "high"
    """
    이 BackpressureLevel 이상이면 헷징 비활성화.
    값: "none", "low", "medium", "high", "critical"
    기본값 "high": HIGH/CRITICAL 레벨에서 헷징 비활성화.
    """

    delay_multiplier_on_medium: float = 2.0
    """MEDIUM 레벨에서 delay 배율. delay * 2.0 = 200ms."""

    delay_multiplier_on_high: float = 5.0
    """HIGH 레벨에서 delay 배율 (비활성화 전 적용). delay * 5.0 = 500ms."""

    # =========================================================================
    # 재시도 불가 예외 처리 (확정적 에러 즉시 실패)
    # =========================================================================
    non_retryable_exceptions: tuple[type[Exception], ...] = field(
        default_factory=lambda: (
            PermissionError,
            KeyError,
            ValueError,
        )
    )
    """이 예외 발생 시 다른 후보 대기 없이 즉시 실패 처리."""

    non_retryable_http_codes: frozenset[int] = field(default_factory=lambda: frozenset({400, 401, 403, 404, 405, 410, 422}))
    """재시도 불가 HTTP 상태 코드. 403, 404 등."""

    # =========================================================================
    # 결과 정합성 검증 (비동기 백그라운드)
    # =========================================================================
    result_validator: Callable[[T, T], bool] | None = None
    """
    두 결과 비교 함수. None이면 검증 비활성화.

    중요: 비동기 검증이므로 첫 응답 속도에 영향 없음.
    불일치 시 로깅만 하고 채택 결과는 변경하지 않음.
    """

    validation_sample_rate: float = 0.1
    """
    검증 샘플링 비율 (0.0~1.0).
    기본값 0.1 = 10% 요청만 검증.
    운영 오버헤드 최소화.
    """

    validation_policy: str = "any_mismatch"
    """
    검증 정책 (3개 이상 후보 시).
    - "any_mismatch": 어떤 불일치든 로깅 (기본값, 단순)
    - "all_same": 모든 결과가 동일해야 정상
    - "majority_wins": 과반수가 동의하면 정상 (Quorum)
    """

    skip_validation_on_high_load: bool = True
    """HIGH/CRITICAL 부하 시 검증 생략."""

    escalate_on_structure_mismatch: bool = True
    """구조적 불일치(type, structure) 시 에스컬레이션."""

    persist_critical_mismatches: bool = False
    """구조적 불일치는 영속적 버퍼에 저장 (Graceful Shutdown 대응)."""


@dataclass
class HedgingCandidate:
    """
    헷징 후보.

    병렬 실행될 후보 함수와 메타데이터를 정의합니다.
    """

    name: str
    """후보 이름 (로깅/메트릭용)."""

    fn: Callable[[], T]
    """실행할 함수."""

    priority: int = 0
    """우선순위 (낮을수록 높은 우선순위)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터 (리전 정보 등)."""

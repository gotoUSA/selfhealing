"""
Saga Step ABC.

SagaStep(ABC) — Forward + Compensate 인터페이스 (도메인-프리).
비즈니스 로직은 어댑터 레이어에서 구현한다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.saga.models import SagaContext, StepResult


class SagaStep(ABC):
    """Saga의 개별 단계를 정의하는 Abstract Base Class.

    도메인-프리 인터페이스. 비즈니스 로직은 어댑터 레이어에서 구현.

    ReplayHandler 패턴과 동일한 구조:
    - 코어 패키지: ABC 정의 + 레지스트리
    - 어댑터 레이어: 도메인별 구현 + 등록
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Step 이름 (고유 식별자).

        SagaContext.step_results의 키로 사용됨.
        """
        pass

    @abstractmethod
    def execute(self, ctx: SagaContext) -> StepResult:
        """Forward 실행.

        Args:
            ctx: Saga 컨텍스트 (이전 Step 결과 + 초기 데이터 포함)

        Returns:
            StepResult — 성공 시 data에 다음 Step/compensate가 필요한 정보 포함
        """
        pass

    @abstractmethod
    def compensate(self, ctx: SagaContext) -> StepResult:
        """역순 보상.

        이 Step의 execute()가 성공한 후 이후 Step이 실패했을 때 호출됨.
        ctx.get()으로 자신의 execute()가 생성한 데이터에 접근 가능.

        Args:
            ctx: Saga 컨텍스트

        Returns:
            StepResult — 실패 시 Orchestrator가 DLQ에 저장
        """
        pass

    def can_execute(self, ctx: SagaContext) -> tuple[bool, str]:
        """실행 가능 여부 확인 (선택적 오버라이드).

        기본값: 항상 실행 가능.
        오버라이드하여 전제 조건 검증 가능.

        Returns:
            (can_execute, reason) 튜플
        """
        return True, ""

    @property
    def timeout_seconds(self) -> int | None:
        """Step별 타임아웃 (초). None이면 Saga 전체 타임아웃 적용.

        기본값: None (Saga 전체 타임아웃에 위임)
        """
        return None

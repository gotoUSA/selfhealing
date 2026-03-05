"""
Config Shadow Evaluators.

ConfigEvaluator Protocol 및 구현체.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from selfhealing.interfaces.event_journal import JournalEntry
from selfhealing.services.config_shadow.models import EvaluatorResult


@runtime_checkable
class ConfigEvaluator(Protocol):
    """설정 변경 효과를 시뮬레이션하는 Evaluator 프로토콜."""

    @property
    def name(self) -> str:
        """Evaluator 이름 (예: "circuit_breaker")."""
        ...

    @property
    def event_types(self) -> list[str]:
        """이 Evaluator가 처리하는 이벤트 타입 리스트."""
        ...

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult:
        """이벤트 스트림에 대해 baseline과 candidate 설정을 비교 평가한다.

        Args:
            events: EventJournal에서 조회한 이벤트 리스트 (시퀀스 오름차순)
            baseline_config: 현재 적용된 설정
            candidate_config: 변경하려는 후보 설정

        Returns:
            비교 결과
        """
        ...

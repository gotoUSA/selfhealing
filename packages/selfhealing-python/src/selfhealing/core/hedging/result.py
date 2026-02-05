"""
Hedging Result - 헷징 실행 결과.

헷징 실행 결과를 담는 데이터 클래스로, 값, 성공 여부, 소스,
지연시간, 헷징 발생 여부 등의 정보를 포함합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

T = TypeVar("T")


@dataclass
class HedgingResult(Generic[T]):
    """
    헷징 실행 결과.

    병렬 실행된 후보들 중 가장 먼저 성공한 결과와
    실행 관련 메타데이터를 포함합니다.
    """

    value: T | None
    """결과 값."""

    success: bool
    """성공 여부."""

    source: str
    """응답을 제공한 후보 이름."""

    latency_ms: float
    """응답 지연시간 (밀리초)."""

    hedged: bool
    """헷징이 발생했는지 여부 (Primary 외 후보 사용)."""

    candidates_tried: int
    """시도된 후보 수."""

    candidates_succeeded: int
    """성공한 후보 수."""

    candidates_failed: int
    """실패한 후보 수."""

    error: str | None = None
    """실패 시 에러 메시지."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    """결과 생성 시간."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    @property
    def hedging_benefit_ms(self) -> float | None:
        """
        헷징으로 인한 지연시간 개선 (밀리초).

        Primary보다 빠른 Secondary가 사용된 경우에만 의미 있음.
        Primary 지연시간에서 실제 사용된 지연시간을 뺀 값을 반환합니다.

        Returns:
            개선된 밀리초 또는 None (계산 불가 시)
        """
        primary_latency = self.metadata.get("primary_latency_ms")
        if primary_latency and self.hedged:
            return primary_latency - self.latency_ms
        return None

    @property
    def primary_latency_ms(self) -> float | None:
        """Primary 후보의 지연시간 (밀리초)."""
        return self.metadata.get("primary_latency_ms")

    @property
    def latency_seconds(self) -> float:
        """응답 지연시간 (초)."""
        return self.latency_ms / 1000.0

    def to_dict(self) -> dict[str, Any]:
        """결과를 딕셔너리로 변환."""
        return {
            "value": self.value,
            "success": self.success,
            "source": self.source,
            "latency_ms": self.latency_ms,
            "hedged": self.hedged,
            "candidates_tried": self.candidates_tried,
            "candidates_succeeded": self.candidates_succeeded,
            "candidates_failed": self.candidates_failed,
            "error": self.error,
            "timestamp": self.timestamp.isoformat(),
            "hedging_benefit_ms": self.hedging_benefit_ms,
            "metadata": self.metadata,
        }

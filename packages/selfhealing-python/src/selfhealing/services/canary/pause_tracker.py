"""
PAUSED 상태 사유 추적기.

롤아웃이 왜 멈췄는지 기록하고 조회하는 기능.
CausationChain과 연동하여 인과관계를 추적합니다.

주요 기능:
- PauseContext: PAUSE 상태 컨텍스트 정보
- PauseReasonTracker: 롤아웃별 PAUSE 이력 관리

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.10
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class PauseContext:
    """
    PAUSED 상태의 컨텍스트 정보.

    왜 배포가 멈췄는지를 운영자에게 명확히 설명합니다.

    Attributes:
        reason: 일시 중지 사유
        triggered_by: 트리거 유형 (interlock, manual, chaos_guard, metrics)
        emergency_level: 인터락 발동 시의 Emergency 레벨 값
        emergency_level_name: Emergency 레벨 이름
        namespace: 영향받은 네임스페이스
        causation_chain_id: CausationChain ID (인과관계 추적)
        paused_at: 일시 중지 시각 ISO 형식
        auto_resume_condition: 자동 재개 조건
        estimated_resume_at: 예상 재개 시각 ISO 형식
    """

    reason: str
    """일시 중지 사유."""

    triggered_by: str
    """트리거 유형 (interlock, manual, chaos_guard, metrics)."""

    emergency_level: int | None = None
    """인터락 발동 시의 Emergency 레벨 값."""

    emergency_level_name: str | None = None
    """Emergency 레벨 이름."""

    namespace: str | None = None
    """영향받은 네임스페이스."""

    causation_chain_id: str | None = None
    """CausationChain ID (인과관계 추적)."""

    paused_at: str | None = None
    """일시 중지 시각 ISO 형식."""

    auto_resume_condition: str | None = None
    """자동 재개 조건 (있는 경우)."""

    estimated_resume_at: str | None = None
    """예상 재개 시각 ISO 형식 (있는 경우)."""

    def explain(self) -> str:
        """
        운영자를 위한 설명 생성.

        Returns:
            사람이 읽을 수 있는 설명 문자열
        """
        parts = ["배포가 일시 중지되었습니다."]

        if self.triggered_by == "interlock":
            level_name = self.emergency_level_name or "Unknown"
            parts.append(f"원인: Emergency {level_name} 발생")
            if self.namespace:
                parts.append(f"영향 리전: {self.namespace}")
        elif self.triggered_by == "chaos_guard":
            parts.append("원인: Chaos 실험 충돌 감지")
        elif self.triggered_by == "metrics":
            parts.append("원인: 메트릭 악화 감지")
        else:
            parts.append("원인: 운영자 수동 중지")

        parts.append(f"사유: {self.reason}")

        if self.causation_chain_id:
            parts.append(f"인과관계 추적: {self.causation_chain_id}")

        if self.auto_resume_condition:
            parts.append(f"자동 재개 조건: {self.auto_resume_condition}")

        return "\n".join(parts)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "reason": self.reason,
            "triggered_by": self.triggered_by,
            "emergency_level": self.emergency_level,
            "emergency_level_name": self.emergency_level_name,
            "namespace": self.namespace,
            "causation_chain_id": self.causation_chain_id,
            "paused_at": self.paused_at,
            "auto_resume_condition": self.auto_resume_condition,
            "estimated_resume_at": self.estimated_resume_at,
        }


class PauseReasonTracker:
    """
    PAUSED 상태 사유 추적기.

    롤아웃이 왜 멈췄는지 기록하고 조회합니다.
    CausationChain과 연동하여 인과관계를 추적합니다.

    Features:
    - PAUSE 이벤트 기록
    - 롤아웃별 PAUSE 이력 조회
    - CausationChain ID 생성 및 연동
    """

    def __init__(self):
        """PauseReasonTracker 초기화."""
        self._pause_records: dict[str, list] = {}  # rollout_id -> List[PauseContext]
        self._lock = threading.Lock()

    def record_pause(
        self,
        rollout_id: str,
        context: PauseContext,
    ) -> str:
        """
        PAUSE 이벤트 기록.

        Args:
            rollout_id: 롤아웃 ID
            context: PAUSE 컨텍스트

        Returns:
            생성된 causation_chain_id
        """
        import uuid
        from datetime import datetime, timezone

        # causation_chain_id 생성
        if not context.causation_chain_id:
            context.causation_chain_id = str(uuid.uuid4())

        # paused_at 설정
        if not context.paused_at:
            context.paused_at = datetime.now(timezone.utc).isoformat()

        # 기록 저장
        with self._lock:
            if rollout_id not in self._pause_records:
                self._pause_records[rollout_id] = []
            self._pause_records[rollout_id].append(context)

        # 로깅
        logger.info(
            "pause_reason_tracker.recorded_pause",
            rollout_id=rollout_id,
            context=context.triggered_by,
            context_2=context.causation_chain_id,
        )

        return context.causation_chain_id

    def get_pause_history(self, rollout_id: str) -> list:
        """
        롤아웃의 PAUSE 이력 조회.

        Args:
            rollout_id: 롤아웃 ID

        Returns:
            PauseContext 목록
        """
        with self._lock:
            return self._pause_records.get(rollout_id, []).copy()

    def get_latest_pause(self, rollout_id: str) -> PauseContext | None:
        """
        롤아웃의 최신 PAUSE 컨텍스트 조회.

        Args:
            rollout_id: 롤아웃 ID

        Returns:
            최신 PauseContext (없으면 None)
        """
        history = self.get_pause_history(rollout_id)
        return history[-1] if history else None

    def clear(self, rollout_id: str) -> None:
        """
        롤아웃의 PAUSE 이력 삭제.

        Args:
            rollout_id: 롤아웃 ID
        """
        with self._lock:
            self._pause_records.pop(rollout_id, None)

    def clear_all(self) -> None:
        """모든 PAUSE 이력 삭제 (테스트용)."""
        with self._lock:
            self._pause_records.clear()


# =============================================================================
# Singleton
# =============================================================================

_pause_reason_tracker: PauseReasonTracker | None = None
_pause_tracker_lock = threading.Lock()


def get_pause_reason_tracker() -> PauseReasonTracker:
    """
    PauseReasonTracker 싱글톤 반환.

    Returns:
        PauseReasonTracker 인스턴스
    """
    global _pause_reason_tracker

    if _pause_reason_tracker is None:
        with _pause_tracker_lock:
            if _pause_reason_tracker is None:
                _pause_reason_tracker = PauseReasonTracker()

    return _pause_reason_tracker


def reset_pause_reason_tracker() -> None:
    """
    PauseReasonTracker 싱글톤 초기화 (테스트용).
    """
    global _pause_reason_tracker
    with _pause_tracker_lock:
        if _pause_reason_tracker is not None:
            _pause_reason_tracker.clear_all()
        _pause_reason_tracker = None

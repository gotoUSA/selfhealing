"""
Postmortem Throttle 데이터 수집 모듈.

Postmortem 생성 시 Throttle 상태 정보를 수집하여 포함시킵니다.

포함 필드:
- throttle_limit_history: 인시던트 기간 동안의 limit 변화 이력
- throttle_min_limit: 기간 중 최저 limit 값
- throttle_adjustment_count: limit 조정 횟수
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()


@dataclass
class ThrottleLimitChange:
    """Throttle limit 변경 기록."""

    timestamp: datetime
    previous_limit: int
    new_limit: int
    reason: str
    trigger_source: str | None = None


@dataclass
class ThrottlePostmortemData:
    """Postmortem용 Throttle 상태 데이터."""

    throttle_limit_history: list[dict[str, Any]] = field(default_factory=list)
    throttle_min_limit: int | None = None
    throttle_max_limit: int | None = None
    throttle_adjustment_count: int = 0
    throttle_current_limit: int | None = None
    throttle_emergency_adjustments: int = 0
    throttle_cb_adjustments: int = 0
    throttle_sla_warnings: int = 0
    throttle_sla_criticals: int = 0

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "throttle_limit_history": self.throttle_limit_history,
            "throttle_min_limit": self.throttle_min_limit,
            "throttle_max_limit": self.throttle_max_limit,
            "throttle_adjustment_count": self.throttle_adjustment_count,
            "throttle_current_limit": self.throttle_current_limit,
            "throttle_emergency_adjustments": self.throttle_emergency_adjustments,
            "throttle_cb_adjustments": self.throttle_cb_adjustments,
            "throttle_sla_warnings": self.throttle_sla_warnings,
            "throttle_sla_criticals": self.throttle_sla_criticals,
        }


class ThrottleLimitHistoryCollector:
    """
    Throttle limit 변경 이력 수집기.

    Postmortem 생성 시 인시던트 기간 동안의 limit 변경 이력을 제공합니다.
    """

    _instance: "ThrottleLimitHistoryCollector | None" = None
    MAX_HISTORY_SIZE = 1000

    def __new__(cls) -> "ThrottleLimitHistoryCollector":
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._init()
            cls._instance = instance
        return cls._instance

    def _init(self) -> None:
        """초기화."""
        self._history: deque[ThrottleLimitChange] = deque(maxlen=self.MAX_HISTORY_SIZE)
        self._emergency_adjustments: int = 0
        self._cb_adjustments: int = 0

    def record_limit_change(
        self,
        previous_limit: int,
        new_limit: int,
        reason: str,
        trigger_source: str | None = None,
    ) -> None:
        """limit 변경 기록."""
        change = ThrottleLimitChange(
            timestamp=datetime.now(timezone.utc),
            previous_limit=previous_limit,
            new_limit=new_limit,
            reason=reason,
            trigger_source=trigger_source,
        )
        self._history.append(change)

        # 조정 카운터 증가
        if trigger_source == "emergency_mode":
            self._emergency_adjustments += 1
        elif trigger_source == "circuit_breaker":
            self._cb_adjustments += 1

    def get_history_for_period(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> list[ThrottleLimitChange]:
        """특정 기간의 limit 변경 이력 조회."""
        result = []
        for change in self._history:
            if start_time and change.timestamp < start_time:
                continue
            if end_time and change.timestamp > end_time:
                continue
            result.append(change)
        return result

    def get_postmortem_data(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ThrottlePostmortemData:
        """Postmortem용 데이터 수집."""
        history = self.get_history_for_period(start_time, end_time)

        # 현재 throttle 상태 가져오기
        current_limit = None
        sla_warnings = 0
        sla_criticals = 0

        try:
            from selfhealing.services.throttle.adaptive import get_adaptive_throttle

            throttle = get_adaptive_throttle()
            current_limit = throttle.current_limit
            stats = throttle.get_stats()
            adaptive_stats = stats.get("adaptive", {})
            sla_warnings = adaptive_stats.get("sla_warnings", 0)
            sla_criticals = adaptive_stats.get("sla_criticals", 0)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "throttle_postmortem.failed_get_throttle_stats",
                error=e,
            )

        # limit 변경 이력을 딕셔너리로 변환
        history_dicts = []
        limits = []
        for change in history:
            history_dicts.append(
                {
                    "timestamp": change.timestamp.isoformat(),
                    "previous_limit": change.previous_limit,
                    "new_limit": change.new_limit,
                    "reason": change.reason,
                    "trigger_source": change.trigger_source,
                }
            )
            limits.append(change.new_limit)

        # 최소/최대 limit 계산
        min_limit = min(limits) if limits else current_limit
        max_limit = max(limits) if limits else current_limit

        return ThrottlePostmortemData(
            throttle_limit_history=history_dicts,
            throttle_min_limit=min_limit,
            throttle_max_limit=max_limit,
            throttle_adjustment_count=len(history),
            throttle_current_limit=current_limit,
            throttle_emergency_adjustments=self._emergency_adjustments,
            throttle_cb_adjustments=self._cb_adjustments,
            throttle_sla_warnings=sla_warnings,
            throttle_sla_criticals=sla_criticals,
        )

    def reset(self) -> None:
        """이력 초기화 (테스트용)."""
        self._history.clear()
        self._emergency_adjustments = 0
        self._cb_adjustments = 0


def get_throttle_history_collector() -> ThrottleLimitHistoryCollector:
    """Throttle 이력 수집기 싱글톤 획득."""
    return ThrottleLimitHistoryCollector()


def collect_throttle_postmortem_data(
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Postmortem용 Throttle 데이터 수집.

    Args:
        start_time: 인시던트 시작 시각
        end_time: 인시던트 종료 시각

    Returns:
        Throttle 관련 Postmortem 데이터 딕셔너리
    """
    try:
        collector = get_throttle_history_collector()
        data = collector.get_postmortem_data(start_time, end_time)
        return data.to_dict()
    except Exception as e:
        logger.warning(
            "throttle_postmortem.failed_collect_data",
            error=e,
        )
        return {}

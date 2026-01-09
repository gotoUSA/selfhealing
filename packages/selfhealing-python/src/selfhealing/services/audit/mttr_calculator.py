"""
MTTR (Mean Time To Recovery) Calculator.

27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md §8.3.1

Audit 로그 기반으로 장애 복구 시간을 자동 계산합니다.

비즈니스 가치:
- "우리는 장애를 이만큼 빨리 복구하며, 이를 시스템적으로 증명한다"
- CB_STATE_CHANGE 이벤트 분석을 통한 MTTR 자동 계산
- 서비스별, 기간별 MTTR 리포트 생성
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RecoveryEvent:
    """복구 이벤트."""
    
    service_name: str
    """서비스 이름."""
    
    incident_start: datetime
    """장애 시작 시점 (OPEN)."""
    
    incident_end: datetime
    """장애 종료 시점 (CLOSED)."""
    
    duration_seconds: float
    """복구 소요 시간 (초)."""
    
    cause: str = "unknown"
    """장애 원인."""
    
    failure_count: int = 0
    """장애 발생 횟수 (OPEN 시점의 failure count)."""
    
    trace_id: Optional[str] = None
    """추적 ID."""


@dataclass
class MTTRReport:
    """MTTR 리포트."""
    
    period_start: datetime
    """분석 기간 시작."""
    
    period_end: datetime
    """분석 기간 종료."""
    
    total_incidents: int
    """총 장애 건수."""
    
    avg_mttr_seconds: float
    """평균 MTTR (초)."""
    
    min_mttr_seconds: float
    """최소 MTTR (초)."""
    
    max_mttr_seconds: float
    """최대 MTTR (초)."""
    
    p50_mttr_seconds: float
    """P50 MTTR (초)."""
    
    p90_mttr_seconds: float
    """P90 MTTR (초)."""
    
    p99_mttr_seconds: float
    """P99 MTTR (초)."""
    
    by_service: Dict[str, float]
    """서비스별 평균 MTTR."""
    
    recovery_events: List[RecoveryEvent] = field(default_factory=list)
    """복구 이벤트 목록."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "total_incidents": self.total_incidents,
            "avg_mttr_seconds": round(self.avg_mttr_seconds, 2),
            "avg_mttr_minutes": round(self.avg_mttr_seconds / 60, 2),
            "min_mttr_seconds": round(self.min_mttr_seconds, 2),
            "max_mttr_seconds": round(self.max_mttr_seconds, 2),
            "p50_mttr_seconds": round(self.p50_mttr_seconds, 2),
            "p90_mttr_seconds": round(self.p90_mttr_seconds, 2),
            "p99_mttr_seconds": round(self.p99_mttr_seconds, 2),
            "by_service": {
                k: round(v, 2) for k, v in self.by_service.items()
            },
            "recovery_event_count": len(self.recovery_events),
        }


class MTTRCalculator:
    """
    MTTR 계산기.
    
    CB_STATE_CHANGE 이벤트를 분석하여 MTTR을 계산합니다.
    
    사용 예:
        calculator = MTTRCalculator()
        
        events = [
            {"timestamp": "...", "service_name": "payment", "old_state": "closed", "new_state": "open"},
            {"timestamp": "...", "service_name": "payment", "old_state": "open", "new_state": "closed"},
        ]
        
        report = calculator.calculate_mttr(events)
        print(f"Average MTTR: {report.avg_mttr_seconds}s")
    """
    
    def calculate_mttr(
        self,
        events: List[Dict[str, Any]],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> MTTRReport:
        """
        MTTR 계산.
        
        Args:
            events: CB_STATE_CHANGE 이벤트 목록
                필수 필드:
                - timestamp: ISO 8601 형식 타임스탬프
                - service_name: 서비스 이름
                - new_state: 새 상태 ("open", "half_open", "closed")
                선택 필드:
                - old_state: 이전 상태
                - cause: 장애 원인
                - failure_count: 실패 횟수
                - trace_id: 추적 ID
            period_start: 분석 시작 시간 (None이면 첫 이벤트 기준)
            period_end: 분석 종료 시간 (None이면 마지막 이벤트 기준)
            
        Returns:
            MTTRReport
        """
        if not events:
            return self._create_empty_report(period_start, period_end)
        
        # 유효한 이벤트 필터링 및 정렬
        sorted_events = self._filter_and_sort_events(events)
        if not sorted_events:
            return self._create_empty_report(period_start, period_end)
        
        # 복구 이벤트 수집
        recovery_events = self._collect_recovery_events(sorted_events)
        
        # 리포트 생성
        return self._build_report(sorted_events, recovery_events, period_start, period_end)
    
    def _create_empty_report(
        self,
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> MTTRReport:
        """빈 MTTR 리포트 생성."""
        now_time = datetime.now(timezone.utc)
        return MTTRReport(
            period_start=period_start or now_time,
            period_end=period_end or now_time,
            total_incidents=0,
            avg_mttr_seconds=0,
            min_mttr_seconds=0,
            max_mttr_seconds=0,
            p50_mttr_seconds=0,
            p90_mttr_seconds=0,
            p99_mttr_seconds=0,
            by_service={},
            recovery_events=[],
        )
    
    def _filter_and_sort_events(
        self,
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """유효한 타임스탬프를 가진 이벤트 필터링 및 정렬."""
        valid_events = [
            e for e in events 
            if self._parse_timestamp(e.get("timestamp", "")) is not None
        ]
        return sorted(
            valid_events, 
            key=lambda e: self._parse_timestamp(e.get("timestamp", ""))  # type: ignore
        )
    
    def _collect_recovery_events(
        self,
        sorted_events: List[Dict[str, Any]],
    ) -> List[RecoveryEvent]:
        """정렬된 이벤트에서 복구 이벤트 수집."""
        open_times: Dict[str, Dict[str, Any]] = {}
        recovery_events: List[RecoveryEvent] = []
        
        for event in sorted_events:
            service = event.get("service_name", "unknown")
            new_state = event.get("new_state", "").lower()
            timestamp = self._parse_timestamp(event.get("timestamp", ""))
            
            if new_state == "open":
                self._record_open_event(open_times, service, event, timestamp)
            elif new_state == "closed" and service in open_times:
                recovery_event = self._create_recovery_event(
                    service, open_times[service], timestamp
                )
                recovery_events.append(recovery_event)
                del open_times[service]
        
        return recovery_events
    
    def _record_open_event(
        self,
        open_times: Dict[str, Dict[str, Any]],
        service: str,
        event: Dict[str, Any],
        timestamp: Optional[datetime],
    ) -> None:
        """OPEN 이벤트 기록."""
        open_times[service] = {
            "timestamp": timestamp,
            "cause": event.get("cause", "unknown"),
            "failure_count": event.get("failure_count", 0),
            "trace_id": event.get("trace_id"),
        }
    
    def _create_recovery_event(
        self,
        service: str,
        open_info: Dict[str, Any],
        closed_timestamp: Optional[datetime],
    ) -> RecoveryEvent:
        """복구 이벤트 생성."""
        duration = (closed_timestamp - open_info["timestamp"]).total_seconds()
        return RecoveryEvent(
            service_name=service,
            incident_start=open_info["timestamp"],
            incident_end=closed_timestamp,
            duration_seconds=duration,
            cause=open_info["cause"],
            failure_count=open_info["failure_count"],
            trace_id=open_info["trace_id"],
        )
    
    def _build_report(
        self,
        sorted_events: List[Dict[str, Any]],
        recovery_events: List[RecoveryEvent],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> MTTRReport:
        """MTTR 리포트 빌드."""
        if not recovery_events:
            first_ts = self._parse_timestamp(sorted_events[0].get("timestamp", ""))
            last_ts = self._parse_timestamp(sorted_events[-1].get("timestamp", ""))
            return MTTRReport(
                period_start=period_start or first_ts or datetime.now(timezone.utc),
                period_end=period_end or last_ts or datetime.now(timezone.utc),
                total_incidents=0,
                avg_mttr_seconds=0,
                min_mttr_seconds=0,
                max_mttr_seconds=0,
                p50_mttr_seconds=0,
                p90_mttr_seconds=0,
                p99_mttr_seconds=0,
                by_service={},
                recovery_events=[],
            )
        
        durations = sorted(e.duration_seconds for e in recovery_events)
        
        return MTTRReport(
            period_start=period_start or recovery_events[0].incident_start,
            period_end=period_end or recovery_events[-1].incident_end,
            total_incidents=len(recovery_events),
            avg_mttr_seconds=statistics.mean(durations),
            min_mttr_seconds=min(durations),
            max_mttr_seconds=max(durations),
            p50_mttr_seconds=self._percentile(durations, 50),
            p90_mttr_seconds=self._percentile(durations, 90),
            p99_mttr_seconds=self._percentile(durations, 99),
            by_service=self._group_by_service(recovery_events),
            recovery_events=recovery_events,
        )
    
    def calculate_mttr_by_period(
        self,
        events: List[Dict[str, Any]],
        period_hours: int = 24,
    ) -> List[MTTRReport]:
        """
        기간별 MTTR 계산.
        
        Args:
            events: CB_STATE_CHANGE 이벤트 목록
            period_hours: 분석 기간 단위 (시간)
            
        Returns:
            기간별 MTTRReport 목록
        """
        if not events:
            return []
        
        # 타임스탬프로 정렬
        sorted_events = sorted(
            events,
            key=lambda e: self._parse_timestamp(e.get("timestamp", "")) or datetime.min.replace(tzinfo=timezone.utc)
        )
        
        # 첫/마지막 타임스탬프 확인
        first_ts = self._parse_timestamp(sorted_events[0].get("timestamp", ""))
        last_ts = self._parse_timestamp(sorted_events[-1].get("timestamp", ""))
        
        if not first_ts or not last_ts:
            return []
        
        reports = []
        period_delta = timedelta(hours=period_hours)
        current_start = first_ts
        
        while current_start < last_ts:
            current_end = current_start + period_delta
            
            # 해당 기간의 이벤트 필터링
            period_events = [
                e for e in sorted_events
                if current_start <= (self._parse_timestamp(e.get("timestamp", "")) or datetime.min.replace(tzinfo=timezone.utc)) < current_end
            ]
            
            if period_events:
                report = self.calculate_mttr(
                    period_events,
                    period_start=current_start,
                    period_end=current_end,
                )
                reports.append(report)
            
            current_start = current_end
        
        return reports
    
    def calculate_service_mttr(
        self,
        events: List[Dict[str, Any]],
        service_name: str,
    ) -> MTTRReport:
        """
        특정 서비스의 MTTR 계산.
        
        Args:
            events: CB_STATE_CHANGE 이벤트 목록
            service_name: 서비스 이름
            
        Returns:
            MTTRReport
        """
        service_events = [
            e for e in events
            if e.get("service_name") == service_name
        ]
        return self.calculate_mttr(service_events)
    
    def _parse_timestamp(self, ts: str) -> Optional[datetime]:
        """타임스탬프 파싱."""
        if not ts:
            return None
        
        try:
            # ISO 8601 형식 파싱
            if ts.endswith('Z'):
                ts = ts[:-1] + '+00:00'
            
            dt = datetime.fromisoformat(ts)
            
            # timezone-aware로 변환
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            
            return dt
        except (ValueError, TypeError) as e:
            logger.debug(f"[MTTRCalculator] Timestamp parse error: {ts}, {e}")
            return None
    
    def _percentile(self, data: List[float], p: int) -> float:
        """백분위수 계산."""
        if not data:
            return 0.0
        
        if len(data) == 1:
            return data[0]
        
        k = (len(data) - 1) * p / 100
        f = int(k)
        c = min(f + 1, len(data) - 1)
        
        return data[f] + (data[c] - data[f]) * (k - f)
    
    def _group_by_service(self, events: List[RecoveryEvent]) -> Dict[str, float]:
        """서비스별 평균 MTTR."""
        by_service: Dict[str, List[float]] = {}
        
        for e in events:
            if e.service_name not in by_service:
                by_service[e.service_name] = []
            by_service[e.service_name].append(e.duration_seconds)
        
        return {
            service: statistics.mean(durations)
            for service, durations in by_service.items()
        }


# 싱글톤 인스턴스
_calculator_instance: Optional[MTTRCalculator] = None


def get_mttr_calculator() -> MTTRCalculator:
    """
    MTTRCalculator 싱글톤 인스턴스 반환.
    
    Returns:
        MTTRCalculator 인스턴스
    """
    global _calculator_instance
    if _calculator_instance is None:
        _calculator_instance = MTTRCalculator()
    return _calculator_instance

"""
Incident Timeline — 통합 타임라인 자동 생성.

EventDAG(251)와 RootCauseAnalysis(253)를 입력으로,
인시던트의 전체 라이프사이클을 하나의 타임라인 문서로 자동 생성한다.

기존 build_timeline()과 SnapshotBuilder를 확장하여,
CB 이벤트뿐 아니라 45개 전체 EventType을 포함하는 통합 타임라인을 만든다.

설계 문서: docs/self_healing/middleware_system/255_CORRELATION_INCIDENT_TIMELINE.md

리뷰 반영 사항:
  R1. Phase 분류 — 전역 필터링 → 상태 머신(State Machine) 단방향 전이
  R2. TimelineStatus 도입 — Ongoing/Verifying/Resolved/Confirmed 명시
  R3. TTAR(Time to Automated Response) 메트릭 신설 및 세일즈 지표화
  R4. Payload Trimming — _sanitize_value()로 JSON 폭탄 방어
  R5. 미등록 EventType Fallback — _humanize_event_type() + prefix/suffix severity 추론 + logger.info 추적
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from selfhealing.services.correlation_engine.event_graph import (
    CausalEdge,
    EventDAG,
    EventNode,
)
from selfhealing.services.correlation_engine.root_cause_ranker import (
    RootCauseAnalysis,
    RootCauseCandidate,
)

logger = logging.getLogger(__name__)


# =============================================================================
# 상수
# =============================================================================

DEFAULT_SEVERITY = "info"
"""SEVERITY_MAP 및 CATEGORY_SEVERITY_DEFAULTS 모두에서 매칭 실패 시 기본 severity."""

DESCRIPTION_MAX_LENGTH = 500
"""설명 문자열 최대 길이 — PagerDuty handler의 description[:500] 패턴
(security_notification/pagerduty_handler.py L97)."""

MAX_METADATA_VALUE_BYTES = 1024
"""메타데이터 개별 문자열 값 최대 바이트 — JSON 폭탄 방어 (R4).
PagerDuty handler가 description[:500]을 사용하는 것과 동일 패턴."""

HUMAN_AVG_RESPONSE_SECONDS = 900.0
"""인간 SRE 평균 대응 시간 기준선 (15분) — TTAR speedup_factor 산출용 (R3)."""


# =============================================================================
# R2: TimelineStatus — 인시던트 상태 (Ongoing / Resolved / Flapping / Confirmed)
# =============================================================================
# IncidentGroupStatus(OPEN/CLOSED/COMPLETED) 3단계 패턴을 확장.
# IncidentTimeline은 사후 분석 문서이므로 대시보드용 VERIFYING 대신
# 데이터 관점의 4단계를 사용한다.
# 대시보드 레이어(DashboardService)에서는 RESOLVED를 "복구 후 안정성 검증 중"
# 으로 렌더링하고, CONFIRMED를 "최종 확인 완료"로 렌더링한다.


class TimelineStatus(str, enum.Enum):
    """인시던트 타임라인 상태.

    IncidentGroupStatus(OPEN→CLOSED→COMPLETED) 패턴과 대응:
      ONGOING    ← OPEN       (이벤트 수집 중, recovery 없음)
      RESOLVED   ← CLOSED     (마지막 recovery 후 critical 재발 없음)
      FLAPPING   ← (신규)     (recovery 후 critical 재발 — flapping)
      CONFIRMED  ← COMPLETED  (resolved + 안정화 윈도우 경과)
    """

    ONGOING = "ongoing"
    """recovery 이벤트 없음 — 장애 진행 중"""

    RESOLVED = "resolved"
    """마지막 recovery 후 critical 재발 없음"""

    FLAPPING = "flapping"
    """recovery 후 critical 재발 — 플래핑 상태"""

    CONFIRMED = "confirmed"
    """resolved + 안정화 윈도우 경과 — 최종 확인 완료"""


# =============================================================================
# R3: TTAR — 자동 개입 이벤트 타입
# =============================================================================
# EventGraphTrigger의 6개 트리거 이벤트(event_graph_trigger.py L26-L35)와 매핑.
# "시스템이 스스로 개입하여 상황을 완화(mitigate)하려고 시도한" 이벤트.

MITIGATION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "throttle_limit_changed",
        "emergency_level_changed",
        "emergency_activated",
        "kill_switch_activated",
        "load_shedding_level_changed",
        "rate_limit_cooldown_start",
    }
)
"""TTAR 산출에 사용되는 자동 개입(Mitigation) 이벤트 타입."""


# =============================================================================
# 복구 이벤트 타입 (is_resolution 판정)
# =============================================================================

RESOLUTION_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "circuit_breaker_closed",
        "emergency_deactivated",
        "emergency_recovery_completed",
        "error_budget_recovered",
        "throttle_limit_recovered",
        "kill_switch_deactivated",
        "rate_limit_cooldown_end",
    }
)


# =============================================================================
# R5: 카테고리 기반 severity 기본값 — prefix/suffix 매칭
# =============================================================================
# SEVERITY_MAP에 하드코딩되지 않은 이벤트에 대해 다단계 fallback을 제공한다.
# 전체 45개 EventType 중 SEVERITY_MAP에 33개, 나머지 12개는 이 테이블에서 추론.
# 매칭 순서: 정확 매칭(SEVERITY_MAP) → prefix 매칭 → suffix 매칭 → DEFAULT_SEVERITY

CATEGORY_SEVERITY_DEFAULTS: dict[str, str] = {
    # prefix 매칭 (이벤트 타입이 이 문자열로 시작하면)
    "emergency_": "critical",
    "kill_switch_": "critical",
    "security_violation_": "critical",
    "saga_compensation_failed": "critical",
    "load_shedding_": "warning",
    "saga_timed_out": "warning",
    "region_heartbeat_expired": "warning",
    "region_instance_stopping": "warning",
    # suffix 매칭 (이벤트 타입이 이 문자열로 끝나면)
    "_recovered": "recovery",
    "_completed": "recovery",
    "_deactivated": "recovery",
    "_failed": "warning",
    "_blocked": "warning",
}


# =============================================================================
# TimelineEntry — 타임라인 개별 항목
# =============================================================================


@dataclass(frozen=True)
class TimelineEntry:
    """타임라인의 개별 항목."""

    timestamp: float
    """Unix timestamp"""

    event_type: str
    """EventType.value (예: 'circuit_breaker_opened')"""

    service_name: str
    """이벤트가 발생한 서비스"""

    description: str
    """사람이 읽을 수 있는 설명 (최대 500자)"""

    severity: str
    """'critical' | 'warning' | 'info' | 'recovery'"""

    is_root_cause: bool
    """Root Cause Ranker가 식별한 근본 원인 여부"""

    is_resolution: bool
    """복구 완료 이벤트 여부"""

    causal_parent: str | None
    """DAG에서 이 이벤트의 원인 이벤트 ID"""

    phase_name: str = ""
    """R1: 이 이벤트가 속한 Phase 이름 (상태 머신에 의해 할당)"""

    is_re_escalation: bool = False
    """R1: Recovery/Mitigation Phase 중 재발한 critical/warning 이벤트 여부"""

    metadata: dict = field(default_factory=dict)
    """추가 컨텍스트 (메트릭 값, 설정 변경 등)"""

    @property
    def formatted_time(self) -> str:
        """HH:MM:SS 포맷."""
        return datetime.fromtimestamp(self.timestamp, tz=timezone.utc).strftime("%H:%M:%S")

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 직렬화 — R4: metadata는 _sanitize_value로 보호."""
        return {
            "timestamp": self.timestamp,
            "formatted_time": self.formatted_time,
            "event_type": self.event_type,
            "service_name": self.service_name,
            "description": self.description,
            "severity": self.severity,
            "is_root_cause": self.is_root_cause,
            "is_resolution": self.is_resolution,
            "causal_parent": self.causal_parent,
            "phase_name": self.phase_name,
            "is_re_escalation": self.is_re_escalation,
            "metadata": _sanitize_value(self.metadata),
        }


# =============================================================================
# TimelinePhase — 타임라인 단계
# =============================================================================


@dataclass(frozen=True)
class TimelinePhase:
    """타임라인의 단계.

    R1 상태 머신에 의해 생성되며, 단방향 전이만 허용:
    DETECTION → ESCALATION → MITIGATION → RECOVERY → POST_RECOVERY
    """

    name: str
    """'detection' | 'escalation' | 'mitigation' | 'recovery' | 'post_recovery'"""

    started_at: float
    """단계 시작 Unix timestamp"""

    ended_at: float | None
    """단계 종료 Unix timestamp (진행 중이면 None)"""

    duration_seconds: float | None
    """단계 소요 시간 (초)"""

    key_events: list[str]
    """이 단계의 주요 이벤트 타입 목록"""

    re_escalation_count: int = 0
    """R1: 이 단계에서 발생한 재에스컬레이션 이벤트 수"""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 직렬화."""
        return {
            "name": self.name,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_seconds": self.duration_seconds,
            "key_events": self.key_events,
            "re_escalation_count": self.re_escalation_count,
        }


# =============================================================================
# IncidentTimeline — 인시던트 전체 타임라인
# =============================================================================


@dataclass
class IncidentTimeline:
    """인시던트 전체 타임라인."""

    incident_id: str
    """인시던트 고유 ID"""

    started_at: float
    """인시던트 시작 Unix timestamp"""

    resolved_at: float | None
    """인시던트 해결 Unix timestamp (미해결이면 None)"""

    duration_seconds: float | None
    """인시던트 총 소요 시간 (초)"""

    root_cause_summary: str
    """RootCauseAnalysis.summary"""

    entries: list[TimelineEntry]
    """시간순 정렬된 타임라인 항목"""

    affected_services: list[str]
    """영향받은 서비스 목록"""

    phases: list[TimelinePhase]
    """감지 → 대응 → 복구 단계 구분"""

    dag_reference: str | None
    """EventDAG incident_id (교차 참조)"""

    status: TimelineStatus = TimelineStatus.ONGOING
    """R2: 인시던트 현재 상태"""

    def to_dict(self) -> dict[str, Any]:
        """JSON 직렬화 (Postmortem/Dashboard용)."""
        ttar = self.get_ttar()
        speedup = _calc_speedup_factor(ttar)

        return {
            "incident_id": self.incident_id,
            "status": self.status.value,
            "started_at": self.started_at,
            "resolved_at": self.resolved_at,
            "duration_seconds": self.duration_seconds,
            "root_cause_summary": self.root_cause_summary,
            "entries": [e.to_dict() for e in self.entries],
            "affected_services": self.affected_services,
            "phases": [p.to_dict() for p in self.phases],
            "dag_reference": self.dag_reference,
            "metrics": {
                "mttd_seconds": self.get_mttd(),
                "mttr_seconds": self.get_mttr(),
                "ttar_seconds": ttar,
                "ttar_speedup_factor": speedup,
                "human_baseline_seconds": HUMAN_AVG_RESPONSE_SECONDS,
                "affected_services": len(self.affected_services),
                "total_events": len(self.entries),
                "escalation_rate": self.get_escalation_rate(),
                "re_escalation_count": sum(1 for e in self.entries if e.is_re_escalation),
            },
        }

    def to_markdown(self) -> str:
        """사람이 읽을 수 있는 마크다운 출력."""
        lines: list[str] = []

        # 헤더
        lines.append(f"## Incident Timeline: {self.incident_id}")
        lines.append("")

        # 상태 배지 (R2)
        status_badge = _status_badge(self.status)
        lines.append(f"**Status**: {status_badge}")

        # 요약
        lines.append(f"**Root Cause**: {self.root_cause_summary}")
        duration_str = _format_duration(self.duration_seconds)
        svc_count = len(self.affected_services)
        lines.append(f"**Duration**: {duration_str}  |  **Affected Services**: {svc_count}개")

        mttd = self.get_mttd()
        mttr = self.get_mttr()
        mttd_str = _format_duration(mttd)
        mttr_str = _format_duration(mttr)
        lines.append(f"**MTTD**: {mttd_str}  |  **MTTR**: {mttr_str}")

        # R3: TTAR 하이라이트
        ttar = self.get_ttar()
        if ttar is not None:
            ttar_str = _format_duration(ttar)
            speedup = _calc_speedup_factor(ttar)
            if speedup is not None:
                lines.append(f"**TTAR**: {ttar_str}  |  " f"\u26a1 인간 평균 대비 **{speedup:,.0f}배** 빠른 자동 대응")
            else:
                lines.append(f"**TTAR**: {ttar_str}")
        lines.append("")

        # Re-escalation 경고 (R1)
        re_esc_count = sum(1 for e in self.entries if e.is_re_escalation)
        if re_esc_count > 0:
            lines.append(
                f"> \u26a0\ufe0f **Flapping 감지**: Recovery 중 {re_esc_count}건의 " f"재에스컬레이션 이벤트가 발생했습니다."
            )
            lines.append("")

        # Phase별 테이블
        for phase in self.phases:
            phase_duration = _format_duration(phase.duration_seconds)
            phase_title = phase.name.replace("_", " ").title()
            re_esc_suffix = ""
            if phase.re_escalation_count > 0:
                re_esc_suffix = f" — \u26a0\ufe0f 재에스컬레이션 {phase.re_escalation_count}건"
            lines.append(f"### {phase_title} Phase ({phase_duration}){re_esc_suffix}")
            lines.append("| Time | Service | Event | Severity |")
            lines.append("|------|---------|-------|----------|")

            phase_entries = self._get_entries_for_phase(phase)
            for entry in phase_entries:
                time_str = entry.formatted_time
                event_display = entry.event_type
                if entry.is_root_cause:
                    event_display = f"**[ROOT CAUSE] {entry.event_type}**"
                elif entry.is_re_escalation:
                    event_display = f"*[RE-ESC] {entry.event_type}*"
                severity_icon = _severity_icon(entry.severity)
                lines.append(f"| {time_str} | {entry.service_name} " f"| {event_display} | {severity_icon} {entry.severity} |")
            lines.append("")

        return "\n".join(lines)

    # ── SRE 메트릭 ──

    def get_mttd(self) -> float | None:
        """Mean Time To Detect — 첫 이벤트부터 첫 critical 알림까지."""
        if not self.entries:
            return None
        first = self.entries[0]
        first_critical = next((e for e in self.entries if e.severity == "critical"), None)
        if first and first_critical:
            return first_critical.timestamp - first.timestamp
        return None

    def get_mttr(self) -> float | None:
        """Mean Time To Resolve — 첫 이벤트부터 마지막 recovery까지."""
        if self.started_at and self.resolved_at:
            return self.resolved_at - self.started_at
        return None

    def get_ttar(self) -> float | None:
        """R3: Time to Automated Response — 첫 critical부터 첫 자동 개입까지.

        EventGraphTrigger의 6개 트리거 이벤트(event_graph_trigger.py L26-L35)와
        매핑되는 MITIGATION_EVENT_TYPES를 사용한다.

        Returns:
            초 단위 TTAR, 또는 해당 이벤트가 없으면 None.
        """
        first_critical = next((e for e in self.entries if e.severity == "critical"), None)
        first_mitigation = next(
            (e for e in self.entries if e.event_type in MITIGATION_EVENT_TYPES),
            None,
        )
        if first_critical and first_mitigation:
            gap = first_mitigation.timestamp - first_critical.timestamp
            # 자동 개입이 critical보다 먼저 발생할 수 있음 (예: throttle 선제 조치)
            return max(0.0, gap)
        return None

    def get_escalation_rate(self) -> float:
        """에스컬레이션 속도 — critical 이벤트 수 / 총 이벤트 수."""
        if not self.entries:
            return 0.0
        critical_count = sum(1 for e in self.entries if e.severity == "critical")
        return critical_count / len(self.entries)

    # ── 내부 헬퍼 ──

    def _get_entries_for_phase(self, phase: TimelinePhase) -> list[TimelineEntry]:
        """특정 phase에 할당된 entries 반환 — R1: phase_name 기반."""
        return [e for e in self.entries if e.phase_name == phase.name]

    @staticmethod
    def _format_duration(seconds: float | None) -> str:
        """초를 사람이 읽기 쉬운 문자열로 변환."""
        return _format_duration(seconds)


# =============================================================================
# R1: Phase 상태 머신 — 단방향 전이
# =============================================================================
# RecoveryDampeningManager(PHASE_1→PHASE_2→COMPLETE)와
# IncidentGroupStatus(OPEN→CLOSED→COMPLETED) 패턴을 따르는
# 단방향 Phase 전이 상태 머신.
#
# 전이 규칙:
#   DETECTION  → 첫 critical/warning 이벤트 도달 → ESCALATION
#   ESCALATION → 첫 recovery 또는 half_open 이벤트 → MITIGATION
#   MITIGATION → 연속 recovery 이벤트 → RECOVERY
#   RECOVERY   → (타임라인 끝) → POST_RECOVERY는 사후 결정
#
# Recovery 중 critical/warning 재발 시 Phase를 역전하지 않고
# is_re_escalation=True 태그를 부착하여 현재 Phase 내에 기록한다.
# AntiFlappingGuard(coordination/anti_flapping.py)의
# "역전 방지" 철학과 동일.


class _PhaseState(str, enum.Enum):
    """Phase 상태 머신 내부 상태."""

    DETECTION = "detection"
    ESCALATION = "escalation"
    MITIGATION = "mitigation"
    RECOVERY = "recovery"
    POST_RECOVERY = "post_recovery"


# =============================================================================
# 유틸리티 함수
# =============================================================================


def _severity_icon(severity: str) -> str:
    """severity에 해당하는 아이콘 반환."""
    return {
        "critical": "\U0001f534",  # 🔴
        "warning": "\U0001f7e1",  # 🟡
        "info": "\U0001f535",  # 🔵
        "recovery": "\U0001f7e2",  # 🟢
    }.get(
        severity, "\u26aa"
    )  # ⚪


def _status_badge(status: TimelineStatus) -> str:
    """R2: 상태에 해당하는 배지 문자열 반환."""
    return {
        TimelineStatus.ONGOING: "\U0001f534 Ongoing (진행 중)",
        TimelineStatus.RESOLVED: "\U0001f7e2 Resolved (복구됨)",
        TimelineStatus.FLAPPING: "\U0001f7e1 Flapping (불안정)",
        TimelineStatus.CONFIRMED: "\u2705 Confirmed (확인 완료)",
    }.get(status, str(status.value))


def _format_duration(seconds: float | None) -> str:
    """초를 사람이 읽기 쉬운 문자열로 변환."""
    if seconds is None:
        return "N/A"
    if seconds < 1:
        return f"{seconds:.3f}초"
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes = int(seconds // 60)
    remaining = int(seconds % 60)
    if remaining == 0:
        return f"{minutes}분"
    return f"{minutes}분 {remaining}초"


def _calc_speedup_factor(ttar: float | None) -> float | None:
    """R3: TTAR 대비 인간 평균의 배수 산출.

    인간 SRE 평균 대응 시간(HUMAN_AVG_RESPONSE_SECONDS=900초)을
    TTAR로 나누어 speedup factor를 계산한다.
    """
    if ttar is None or ttar <= 0:
        return None
    return HUMAN_AVG_RESPONSE_SECONDS / ttar


def _humanize_event_type(event_type: str) -> str:
    """R5: snake_case → Human Readable Title Case.

    'saga_timed_out' → 'Saga Timed Out'
    'kill_switch_activated' → 'Kill Switch Activated'
    """
    return event_type.replace("_", " ").title()


def _resolve_severity(event_type: str, severity_map: dict[str, str]) -> str:
    """R5: 다단계 severity 추론.

    1단계: severity_map 정확 매칭
    2단계: CATEGORY_SEVERITY_DEFAULTS prefix 매칭
    3단계: CATEGORY_SEVERITY_DEFAULTS suffix 매칭
    4단계: DEFAULT_SEVERITY ('info') fallback + logger.info 추적

    tier_mapping.py의 logger.warning("Unknown criticality") 패턴과 동일 맥락이나,
    severity fallback은 "에러"가 아닌 "정상 경로의 기본값 사용"이므로
    logger.info를 사용한다.
    """
    # 1) 정확 매칭
    if event_type in severity_map:
        return severity_map[event_type]

    # 2) prefix 매칭
    for pattern, severity in CATEGORY_SEVERITY_DEFAULTS.items():
        if not pattern.startswith("_") and event_type.startswith(pattern):
            return severity

    # 3) suffix 매칭
    for pattern, severity in CATEGORY_SEVERITY_DEFAULTS.items():
        if pattern.startswith("_") and event_type.endswith(pattern):
            return severity

    # 4) fallback — logger.info 추적 (R5)
    logger.info(
        "[IncidentTimeline] EventType '%s' not in SEVERITY_MAP, " "resolved via fallback to '%s'",
        event_type,
        DEFAULT_SEVERITY,
    )
    return DEFAULT_SEVERITY


# =============================================================================
# R4: Payload Trimming — JSON 폭탄 방어
# =============================================================================
# SelfHealingEvent.data는 dict[str, Any]로 크기 제한이 전혀 없다.
# (event_bus/bus/__init__.py L208 — "data: dict[str, Any]" 무제한)
# PagerDuty handler가 description[:500]을 사용하는 것처럼
# (security_notification/pagerduty_handler.py L97),
# 직렬화 시점에 문자열 값을 MAX_METADATA_VALUE_BYTES로 절삭한다.
#
# 메모리 내에서는 원본 데이터 유지 (디버깅/분석용),
# to_dict() 직렬화 시점에만 절삭 (저장/전송 최적화).


def _sanitize_value(value: Any, _depth: int = 0) -> Any:
    """R4: 메타데이터 값의 크기를 제한 — 재귀적 dict/list 처리.

    Args:
        value: 직렬화할 값
        _depth: 재귀 깊이 (무한루프 방지, 최대 5)

    Returns:
        크기가 제한된 값
    """
    if _depth > 5:
        return "[Max depth exceeded]"

    if isinstance(value, str):
        if len(value.encode("utf-8", errors="replace")) > MAX_METADATA_VALUE_BYTES:
            # 바이트 기준으로 자르되 문자 경계를 존중
            truncated = value[:MAX_METADATA_VALUE_BYTES]
            return truncated + " [Truncated]"
        return value

    if isinstance(value, dict):
        return {k: _sanitize_value(v, _depth + 1) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item, _depth + 1) for item in value]

    # int, float, bool, None 등 원시 타입은 그대로
    return value


# =============================================================================
# IncidentTimelineBuilder — 타임라인 생성 엔진
# =============================================================================


class IncidentTimelineBuilder:
    """DAG + Root Cause → 통합 타임라인 생성.

    설계 문서: 255_CORRELATION_INCIDENT_TIMELINE.md §3.1

    리뷰 반영:
      R1. Phase 분류 — 상태 머신(State Machine) 단방향 전이
      R2. TimelineStatus — Ongoing/Flapping/Resolved 상태 결정
      R3. TTAR 메트릭 — MITIGATION_EVENT_TYPES 기반
      R4. Payload Trimming — _extract_metadata 화이트리스트 + 직렬화 시 절삭
      R5. Severity 추론 — _resolve_severity() 다단계 fallback
    """

    # 이벤트 타입별 severity 매핑 — 33개 하드코딩
    SEVERITY_MAP: dict[str, str] = {
        # Critical (6)
        "circuit_breaker_opened": "critical",
        "emergency_level_changed": "critical",
        "emergency_activated": "critical",
        "kill_switch_activated": "critical",
        "security_violation_critical": "critical",
        "load_shedding_level_changed": "critical",
        # Warning (8)
        "error_budget_critical": "warning",
        "error_budget_warning": "warning",
        "throttle_limit_changed": "warning",
        "throttle_sla_warning": "warning",
        "throttle_sla_critical": "warning",
        "security_violation_detected": "warning",
        "rate_limit_429": "warning",
        "saga_step_failed": "warning",
        "saga_compensation_failed": "warning",
        # Info (9)
        "circuit_breaker_half_opened": "info",
        "config_updated": "info",
        "saga_started": "info",
        "saga_step_completed": "info",
        "chaos_experiment_started": "info",
        "chaos_experiment_stopped": "info",
        "region_primary_changed": "info",
        "saga_suspended": "info",
        "saga_resumed": "info",
        # Recovery (10)
        "circuit_breaker_closed": "recovery",
        "emergency_deactivated": "recovery",
        "emergency_recovery_completed": "recovery",
        "error_budget_recovered": "recovery",
        "throttle_limit_recovered": "recovery",
        "kill_switch_deactivated": "recovery",
        "rate_limit_cooldown_end": "recovery",
        "saga_completed": "recovery",
        "saga_compensated": "recovery",
        "dlq_replay_completed": "recovery",
    }

    # 이벤트 타입별 설명 템플릿 — DESCRIPTION_MAP에 없는 이벤트는
    # _humanize_event_type() fallback(R5)을 사용
    DESCRIPTION_MAP: dict[str, str] = {
        "circuit_breaker_opened": "Circuit Breaker OPEN — {service_name} 트래픽 차단",
        "circuit_breaker_closed": "Circuit Breaker CLOSED — {service_name} 정상 복구",
        "circuit_breaker_half_opened": "Circuit Breaker HALF-OPEN — {service_name} 시범 트래픽",
        "emergency_level_changed": "Emergency Level → {level}",
        "emergency_activated": "Emergency 모드 활성화",
        "emergency_deactivated": "Emergency 해제 — 정상 모드 복귀",
        "emergency_recovery_completed": "Emergency 복구 완료",
        "emergency_recovery_started": "Emergency 복구 시작",
        "throttle_limit_changed": "Throttle 제한 변경 — {service_name}",
        "throttle_limit_recovered": "Throttle 제한 복구 — {service_name}",
        "error_budget_critical": "Error Budget 임계치 초과 — {service_name}",
        "error_budget_warning": "Error Budget 경고 — {service_name}",
        "error_budget_recovered": "Error Budget 복구 — {service_name}",
        "kill_switch_activated": "Kill Switch 활성화",
        "kill_switch_deactivated": "Kill Switch 비활성화",
        "security_violation_detected": "보안 위반 감지 — {service_name}",
        "security_violation_critical": "CRITICAL 보안 위반 — {service_name}",
        "load_shedding_level_changed": "Load Shedding 레벨 변경",
        "rate_limit_429": "외부 API 429 응답 수신 — {service_name}",
        "rate_limit_cooldown_start": "Rate Limit Cooldown 시작",
        "rate_limit_cooldown_end": "Rate Limit Cooldown 종료",
        "saga_started": "Saga 실행 시작",
        "saga_completed": "Saga 완료",
        "saga_step_failed": "Saga Step 실패",
        "saga_compensated": "Saga 보상 완료",
        "saga_compensation_failed": "Saga 보상 실패",
        "saga_timed_out": "Saga 타임아웃",
    }

    def build(
        self,
        dag: EventDAG,
        root_cause_analysis: RootCauseAnalysis,
        snapshot_data: dict[str, Any] | None = None,
    ) -> IncidentTimeline:
        """DAG + 분석 결과 → 통합 타임라인.

        Args:
            dag: 인과관계 DAG
            root_cause_analysis: 근본 원인 분석 결과
            snapshot_data: 추가 스냅샷 데이터 (선택)

        Returns:
            IncidentTimeline 인스턴스
        """
        if not dag.nodes:
            logger.warning("[IncidentTimeline] Empty DAG — returning minimal timeline")
            return IncidentTimeline(
                incident_id=dag.incident_id,
                started_at=0.0,
                resolved_at=None,
                duration_seconds=None,
                root_cause_summary=root_cause_analysis.summary,
                entries=[],
                affected_services=[],
                phases=[],
                dag_reference=dag.incident_id,
                status=TimelineStatus.ONGOING,
            )

        # 근본 원인 이벤트 ID 집합 (상위 후보만)
        root_cause_event_ids: set[str] = set()
        if root_cause_analysis.candidates:
            root_cause_event_ids.add(root_cause_analysis.primary_cause.event_node.event_id)

        # 1) DAG 노드 → 초기 TimelineEntry 변환 (시간순, phase 미할당)
        raw_entries: list[TimelineEntry] = []
        sorted_nodes = sorted(dag.nodes.values(), key=lambda n: (n.timestamp, n.event_id))

        for node in sorted_nodes:
            is_root = node.event_id in root_cause_event_ids
            causal_parent = self._find_causal_parent(node, dag)
            description = self._format_description(node, is_root, causal_parent)
            severity = _resolve_severity(node.event_type, self.SEVERITY_MAP)

            entry = TimelineEntry(
                timestamp=node.timestamp,
                event_type=node.event_type,
                service_name=node.service_name,
                description=description[:DESCRIPTION_MAX_LENGTH],
                severity=severity,
                is_root_cause=is_root,
                is_resolution=node.event_type in RESOLUTION_EVENT_TYPES,
                causal_parent=causal_parent.event_id if causal_parent else None,
                metadata=self._extract_metadata(node, snapshot_data),
            )
            raw_entries.append(entry)

        # 2) R1: 상태 머신 Phase 분류 + phase_name/is_re_escalation 할당
        entries, phases = self._classify_phases_state_machine(raw_entries)

        # 3) 메타 정보 산출
        started_at = entries[0].timestamp if entries else 0.0
        resolved_at = self._find_resolution_time(entries)
        duration = (resolved_at - started_at) if resolved_at is not None else None

        # 4) R2: TimelineStatus 결정
        status = self._determine_status(entries, resolved_at)

        affected_services = sorted({e.service_name for e in entries})

        timeline = IncidentTimeline(
            incident_id=dag.incident_id,
            started_at=started_at,
            resolved_at=resolved_at,
            duration_seconds=duration,
            root_cause_summary=root_cause_analysis.summary,
            entries=entries,
            affected_services=affected_services,
            phases=phases,
            dag_reference=dag.incident_id,
            status=status,
        )

        logger.info(
            "[IncidentTimeline] Built timeline for %s: " "%d entries, %d phases, %d services, status=%s",
            dag.incident_id,
            len(entries),
            len(phases),
            len(affected_services),
            status.value,
        )
        return timeline

    # ── R1: Phase 분류 (상태 머신) ──

    def _classify_phases_state_machine(self, entries: list[TimelineEntry]) -> tuple[list[TimelineEntry], list[TimelinePhase]]:
        """R1: 상태 머신 기반 Phase 분류.

        전이 규칙 (단방향):
          DETECTION  → 첫 critical/warning 이벤트 → ESCALATION
          ESCALATION → 첫 recovery 또는 half_open → MITIGATION
          MITIGATION → 연속 recovery 이벤트 → RECOVERY
          RECOVERY   → (타임라인 끝) → POST_RECOVERY는 사후 결정

        Recovery/Mitigation Phase 중 critical/warning 재발 시:
          Phase를 역전하지 않고 is_re_escalation=True 태그 부착.
          AntiFlappingGuard(coordination/anti_flapping.py)의
          min_stable_duration_before_recovery_seconds=600 패턴과 동일한
          "역전 방지" 철학.

        Returns:
            (phase_name이 할당된 entries, TimelinePhase 리스트)
        """
        if not entries:
            return [], []

        current_state = _PhaseState.DETECTION
        phase_starts: dict[_PhaseState, float] = {
            _PhaseState.DETECTION: entries[0].timestamp,
        }
        phase_events: dict[_PhaseState, list[str]] = {
            _PhaseState.DETECTION: [],
        }
        re_esc_counts: dict[_PhaseState, int] = {}

        updated_entries: list[TimelineEntry] = []

        for entry in entries:
            is_re_escalation = False
            assigned_phase = current_state

            if current_state == _PhaseState.DETECTION:
                if entry.severity in ("critical", "warning"):
                    # 첫 critical/warning → ESCALATION 전이
                    current_state = _PhaseState.ESCALATION
                    assigned_phase = _PhaseState.ESCALATION
                    phase_starts[_PhaseState.ESCALATION] = entry.timestamp
                    phase_events[_PhaseState.ESCALATION] = []

            elif current_state == _PhaseState.ESCALATION:
                if entry.severity == "recovery" or entry.event_type == "circuit_breaker_half_opened":
                    # 첫 recovery/half_open → MITIGATION 전이
                    current_state = _PhaseState.MITIGATION
                    assigned_phase = _PhaseState.MITIGATION
                    phase_starts[_PhaseState.MITIGATION] = entry.timestamp
                    phase_events[_PhaseState.MITIGATION] = []

            elif current_state == _PhaseState.MITIGATION:
                if entry.severity == "recovery":
                    # 연속 recovery → RECOVERY 전이
                    current_state = _PhaseState.RECOVERY
                    assigned_phase = _PhaseState.RECOVERY
                    phase_starts[_PhaseState.RECOVERY] = entry.timestamp
                    phase_events[_PhaseState.RECOVERY] = []
                elif entry.severity in ("critical", "warning"):
                    # mitigation 중 재발 → 재에스컬레이션 태깅
                    is_re_escalation = True

            elif current_state == _PhaseState.RECOVERY:
                if entry.severity in ("critical", "warning"):
                    # Recovery 중 재발 → Phase 역전하지 않고 태깅
                    is_re_escalation = True

            # Phase 이벤트 목록에 추가
            if assigned_phase not in phase_events:
                phase_events[assigned_phase] = []
            phase_events[assigned_phase].append(entry.event_type)

            # 재에스컬레이션 카운트
            if is_re_escalation:
                re_esc_counts[assigned_phase] = re_esc_counts.get(assigned_phase, 0) + 1

            # phase_name, is_re_escalation이 할당된 새 entry 생성
            # (TimelineEntry는 frozen이므로 새 인스턴스 생성)
            updated_entry = TimelineEntry(
                timestamp=entry.timestamp,
                event_type=entry.event_type,
                service_name=entry.service_name,
                description=entry.description,
                severity=entry.severity,
                is_root_cause=entry.is_root_cause,
                is_resolution=entry.is_resolution,
                causal_parent=entry.causal_parent,
                phase_name=assigned_phase.value,
                is_re_escalation=is_re_escalation,
                metadata=entry.metadata,
            )
            updated_entries.append(updated_entry)

        # Phase 객체 생성 — 실제 이벤트가 할당된 Phase만 생성
        result_phases: list[TimelinePhase] = []
        active_phases = [ps for ps in _PhaseState if ps in phase_starts and ps in phase_events and phase_events[ps]]

        for i, ps in enumerate(active_phases):
            started = phase_starts[ps]
            # ended_at = 다음 Phase 시작 또는 마지막 이벤트
            if i + 1 < len(active_phases):
                ended = phase_starts[active_phases[i + 1]]
            else:
                # 마지막 Phase — 해당 Phase의 마지막 이벤트 시점
                phase_entry_times = [e.timestamp for e in updated_entries if e.phase_name == ps.value]
                ended = max(phase_entry_times) if phase_entry_times else started

            result_phases.append(
                TimelinePhase(
                    name=ps.value,
                    started_at=started,
                    ended_at=ended,
                    duration_seconds=ended - started,
                    key_events=phase_events[ps],
                    re_escalation_count=re_esc_counts.get(ps, 0),
                )
            )

        return updated_entries, result_phases

    # ── R2: 상태 결정 ──

    @staticmethod
    def _determine_status(
        entries: list[TimelineEntry],
        resolved_at: float | None,
    ) -> TimelineStatus:
        """R2: 인시던트 상태 결정.

        - recovery 없음 → ONGOING
        - 마지막 recovery 후 critical 재발 → FLAPPING
        - 마지막 recovery 후 critical 없음 → RESOLVED
        - (CONFIRMED는 외부에서 안정화 윈도우 경과 후 설정)
        """
        if resolved_at is None:
            return TimelineStatus.ONGOING

        # 마지막 recovery 이후에 critical이 있는지 확인
        has_critical_after_resolve = any(e.timestamp > resolved_at and e.severity == "critical" for e in entries)
        if has_critical_after_resolve:
            return TimelineStatus.FLAPPING

        return TimelineStatus.RESOLVED

    # ── 내부 헬퍼 ──

    def _find_causal_parent(self, node: EventNode, dag: EventDAG) -> EventNode | None:
        """DAG에서 특정 노드의 가장 높은 confidence 원인 노드를 반환한다."""
        parent_edges = [e for e in dag.edges if e.target.event_id == node.event_id]
        if not parent_edges:
            return None
        best_edge = max(parent_edges, key=lambda e: e.confidence)
        return best_edge.source

    def _format_description(
        self,
        node: EventNode,
        is_root: bool,
        causal_parent: EventNode | None,
    ) -> str:
        """이벤트 노드를 사람이 읽을 수 있는 설명으로 변환.

        R5: DESCRIPTION_MAP에 없는 이벤트는 _humanize_event_type() fallback.
        """
        template = self.DESCRIPTION_MAP.get(node.event_type)

        if template:
            # 템플릿 변수 치환
            format_vars: dict[str, Any] = {
                "service_name": node.service_name,
            }
            if "level" in node.data:
                format_vars["level"] = node.data["level"]
            elif "new_level" in node.data:
                format_vars["level"] = node.data["new_level"]

            try:
                desc = template.format_map(_SafeFormatDict(format_vars))
            except (KeyError, ValueError):
                desc = f"{_humanize_event_type(node.event_type)} — {node.service_name}"
        else:
            # R5: 자동 변환 fallback
            desc = f"{_humanize_event_type(node.event_type)} — {node.service_name}"

        # 근본 원인 표시
        if is_root:
            desc = f"[ROOT CAUSE] {desc}"

        # 인과관계 전파 표시
        if causal_parent and not is_root:
            desc = f"{desc} ← {causal_parent.service_name}에서 전파"

        return desc

    def _extract_metadata(
        self,
        node: EventNode,
        snapshot_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """R4: 노드에서 메타데이터를 추출한다 — 화이트리스트 기반.

        EventNode.data(SelfHealingEvent.data)를 1차 소스로,
        snapshot_data를 2차(보강) 소스로 사용하는 2-Tier 접근.
        원본 data에서 주요 필드만 화이트리스트로 복사하여
        거대한 payload가 그대로 유입되는 것을 방지한다.

        실제 크기 제한은 to_dict() 직렬화 시점에 _sanitize_value()로 적용.
        """
        meta: dict[str, Any] = {}

        # Tier 1: 화이트리스트 필드만 복사
        _WHITELIST_KEYS = (
            "level",
            "previous_level",
            "new_level",
            "error_rate",
            "current_rtt_ms",
            "threshold_ms",
            "current_limit",
            "previous_limit",
            "new_limit",
            "reason",
            "severity",
            "budget_percent",
            "threshold",
            "new_state",
            "previous_state",
            "failure_count",
            "service_name",
        )
        for key in _WHITELIST_KEYS:
            if key in node.data:
                meta[key] = node.data[key]

        # Tier 2: 스냅샷 데이터에서 이 서비스의 메트릭 추가
        if snapshot_data and node.service_name in snapshot_data:
            meta["snapshot"] = snapshot_data[node.service_name]

        return meta

    @staticmethod
    def _find_resolution_time(
        entries: list[TimelineEntry],
    ) -> float | None:
        """R2: 해결 시점 결정 — 역순 탐색 + flapping 방어.

        마지막 recovery 이벤트의 timestamp를 반환하되,
        그 이후에 critical 이벤트가 존재하면 None을 반환한다.
        (resolved_at이 확정되지 않음 → TimelineStatus.FLAPPING 또는 ONGOING)
        """
        last_recovery_ts: float | None = None
        for entry in reversed(entries):
            if entry.is_resolution or entry.severity == "recovery":
                last_recovery_ts = entry.timestamp
                break

        if last_recovery_ts is None:
            return None

        # 마지막 recovery 이후에 critical이 있으면 미해결
        for entry in entries:
            if entry.timestamp > last_recovery_ts and entry.severity == "critical":
                return None

        return last_recovery_ts


class _SafeFormatDict(dict):
    """format_map()에서 누락 키를 빈 문자열로 대체하는 dict.

    tier_mapping.py의 logger.warning fallback 패턴과 유사하게,
    알 수 없는 키에 대해 경고 없이 graceful하게 처리한다.
    """

    def __missing__(self, key: str) -> str:
        logger.warning(
            "[IncidentTimeline] Missing template variable '%s', " "falling back to empty string",
            key,
        )
        return ""

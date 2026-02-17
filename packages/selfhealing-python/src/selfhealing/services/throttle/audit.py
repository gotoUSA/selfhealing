"""
Throttle 감사 로그 및 CascadeEvent 연동 모듈.

감사 이벤트:
- throttle_limit_adjusted: limit 변경 시 기록
- throttle_emergency_sync: Emergency 연동 시 기록
- throttle_cb_sync: CB 연동 시 기록
- throttle_sla_warning: SLA Warning 임계값 도달
- throttle_sla_critical: SLA Critical 임계값 도달
- throttle_full_stop_activated: Full Stop 활성화
- throttle_full_stop_deactivated: Full Stop 비활성화
- throttle_429_response: 외부 API 429 연동
- throttle_recovery_started: Recovery Dampening 시작
- throttle_recovery_completed: Recovery 완료

CascadeEvent 기록 정책:
- 비상 상황으로 인한 강제 강등만 기록 (emergency, cb, sla_critical, full_stop)
- 일반 rate limit은 메트릭만 기록
"""

from __future__ import annotations

import logging
import random
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from enum import Enum
from queue import Full, Queue
from threading import Lock, Thread
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 감사 이벤트 타입 상수 (확장)
# =============================================================================

# Limit 변경
AUDIT_THROTTLE_LIMIT_ADJUSTED = "throttle_limit_adjusted"

# Emergency 연동
AUDIT_THROTTLE_EMERGENCY_SYNC = "throttle_emergency_sync"

# CB 연동
AUDIT_THROTTLE_CB_SYNC = "throttle_cb_sync"

# SLA 위반 (레거시 호환)
AUDIT_THROTTLE_SLA_BREACH = "throttle_sla_breach"

# SLA 위반 (새 이벤트)
AUDIT_THROTTLE_SLA_WARNING = "throttle_sla_warning"
AUDIT_THROTTLE_SLA_CRITICAL = "throttle_sla_critical"

# Full Stop
AUDIT_THROTTLE_FULL_STOP_ACTIVATED = "throttle_full_stop_activated"
AUDIT_THROTTLE_FULL_STOP_DEACTIVATED = "throttle_full_stop_deactivated"

# 429 연동
AUDIT_THROTTLE_429_RESPONSE = "throttle_429_response"

# Recovery
AUDIT_THROTTLE_RECOVERY_STARTED = "throttle_recovery_started"
AUDIT_THROTTLE_RECOVERY_COMPLETED = "throttle_recovery_completed"


# =============================================================================
# CascadeEvent 대상 이벤트 집합
# =============================================================================

CASCADE_EVENT_ACTIONS: set[str] = {
    AUDIT_THROTTLE_EMERGENCY_SYNC,
    AUDIT_THROTTLE_CB_SYNC,
    AUDIT_THROTTLE_SLA_CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
    AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
}


# =============================================================================
# Severity 정의
# =============================================================================


class AuditSeverity(str, Enum):
    """감사 이벤트 심각도."""

    DEBUG = "debug"  # 디버깅용 (샘플링 대상)
    INFO = "info"  # 일반 정보
    WARNING = "warning"  # 주의 필요
    CRITICAL = "critical"  # 즉각 대응 필요


# 이벤트별 Severity 매핑
AUDIT_SEVERITY_MAP: dict[str, AuditSeverity] = {
    # DEBUG: 샘플링 대상
    AUDIT_THROTTLE_LIMIT_ADJUSTED: AuditSeverity.DEBUG,
    # INFO: 일반 정보
    AUDIT_THROTTLE_429_RESPONSE: AuditSeverity.INFO,
    AUDIT_THROTTLE_RECOVERY_STARTED: AuditSeverity.INFO,
    AUDIT_THROTTLE_RECOVERY_COMPLETED: AuditSeverity.INFO,
    AUDIT_THROTTLE_SLA_BREACH: AuditSeverity.INFO,
    # WARNING: 주의 필요
    AUDIT_THROTTLE_SLA_WARNING: AuditSeverity.WARNING,
    AUDIT_THROTTLE_CB_SYNC: AuditSeverity.WARNING,
    # CRITICAL: 즉각 대응 필요 (CASCADE_EVENT)
    AUDIT_THROTTLE_SLA_CRITICAL: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_EMERGENCY_SYNC: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_ACTIVATED: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_DEACTIVATED: AuditSeverity.CRITICAL,
}


# =============================================================================
# Correlation ID 컨텍스트 관리
# =============================================================================

_audit_event_chain: ContextVar[list[str]] = ContextVar("audit_event_chain", default=[])


def _generate_event_id() -> str:
    """감사 이벤트 고유 ID 생성."""
    return f"evt-{uuid.uuid4().hex[:12]}"


def _get_correlation_ids() -> tuple[str, str | None]:
    """
    현재 이벤트 ID와 부모 이벤트 ID 반환.

    Returns:
        (event_id, parent_event_id) 튜플
    """
    chain = _audit_event_chain.get()
    event_id = _generate_event_id()
    parent_id = chain[-1] if chain else None

    # 체인에 현재 이벤트 추가
    new_chain = list(chain) + [event_id]
    _audit_event_chain.set(new_chain)

    return event_id, parent_id


def reset_event_chain() -> None:
    """이벤트 체인 초기화 (요청 종료 시 호출)."""
    _audit_event_chain.set([])


# =============================================================================
# 비동기 감사 워커
# =============================================================================

_audit_queue: Queue[dict[str, Any]] = Queue(maxsize=10000)
_worker_started = False
_worker_lock = Lock()


def _start_audit_worker() -> None:
    """백그라운드 감사 워커 시작."""
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def _worker_loop() -> None:
        while True:
            try:
                audit_data = _audit_queue.get(timeout=1.0)
                _process_audit_event(audit_data)
            except Exception:
                pass  # 타임아웃 또는 에러 무시

    thread = Thread(target=_worker_loop, daemon=True, name="ThrottleAuditWorker")
    thread.start()
    logger.info("[ThrottleAudit] Background worker started")


def _process_audit_event(audit_data: dict[str, Any]) -> None:
    """감사 이벤트 실제 처리."""
    action = audit_data.get("action", "unknown")
    is_critical = audit_data.pop("_is_critical", False)

    # WAL 기록
    _write_to_wal_safe(action, audit_data)

    # CascadeEvent 기록 (해당되는 경우)
    if action in CASCADE_EVENT_ACTIONS:
        _record_cascade_event_safe(action, audit_data)

    # 중요 이벤트 즉시 동기화
    if is_critical:
        _sync_to_central_immediately(audit_data)


# =============================================================================
# 샘플링 설정
# =============================================================================


def _get_sampling_config() -> dict[str, float]:
    """
    이벤트별 샘플링 비율 반환.

    Returns:
        {action: sample_rate} 딕셔너리 (1.0 = 100%)
    """
    return {
        # 일반 이벤트: 샘플링 적용
        AUDIT_THROTTLE_LIMIT_ADJUSTED: 0.1,  # 10%
        AUDIT_THROTTLE_429_RESPONSE: 0.5,  # 50%
        # 중요 이벤트: 항상 100%
        AUDIT_THROTTLE_RECOVERY_STARTED: 1.0,
        AUDIT_THROTTLE_RECOVERY_COMPLETED: 1.0,
        AUDIT_THROTTLE_EMERGENCY_SYNC: 1.0,
        AUDIT_THROTTLE_CB_SYNC: 1.0,
        AUDIT_THROTTLE_SLA_WARNING: 1.0,
        AUDIT_THROTTLE_SLA_CRITICAL: 1.0,
        AUDIT_THROTTLE_FULL_STOP_ACTIVATED: 1.0,
        AUDIT_THROTTLE_FULL_STOP_DEACTIVATED: 1.0,
    }


def should_sample(action: str) -> bool:
    """
    해당 이벤트를 기록할지 결정.

    Args:
        action: 감사 이벤트 타입

    Returns:
        True면 기록, False면 스킵
    """
    # CRITICAL, WARNING 이벤트는 항상 기록
    severity = AUDIT_SEVERITY_MAP.get(action, AuditSeverity.INFO)
    if severity in (AuditSeverity.CRITICAL, AuditSeverity.WARNING):
        return True

    config = _get_sampling_config()
    rate = config.get(action, 1.0)  # 기본 100%

    if rate >= 1.0:
        return True

    return random.random() < rate


def _get_severity(action: str, override: AuditSeverity | None = None) -> str:
    """이벤트 Severity 결정."""
    if override:
        return override.value
    return AUDIT_SEVERITY_MAP.get(action, AuditSeverity.INFO).value


def _is_critical_action(action: str) -> bool:
    """CASCADE_EVENT 대상 액션인지 확인."""
    return action in CASCADE_EVENT_ACTIONS


# =============================================================================
# 클러스터 정보 자동 주입
# =============================================================================


def _inject_cluster_identity(audit_data: dict[str, Any]) -> dict[str, Any]:
    """
    감사 데이터에 클러스터 정보 자동 주입.

    Multi-Cluster 환경에서 어느 리전의 이벤트인지 구분 가능.
    ClusterIdentity 실패해도 감사 기록 자체는 계속됨.
    """
    try:
        from selfhealing.core.cluster_identity import get_cluster_identity

        identity = get_cluster_identity()

        # 클러스터 정보 주입
        audit_data["cluster"] = {
            "cluster_id": identity.cluster_id,
            "region": identity.region,
            "environment": identity.environment,
            "pod_id": identity.pod_id,
        }

        # tenant 정보 (SaaS 환경)
        if identity.tenant:
            audit_data["cluster"]["tenant"] = identity.tenant

    except ImportError:
        logger.debug("[ThrottleAudit] ClusterIdentity not available")
    except Exception as e:
        logger.debug(f"[ThrottleAudit] ClusterIdentity injection failed: {e}")

    return audit_data


# =============================================================================
# 감사 데이터 빌더 (확장)
# =============================================================================


def _collect_extension_fields(
    *,
    smoothed_rtt_ms: float | None = None,
    gradient: float | None = None,
    long_rtt_ms: float | None = None,
    consecutive_429s: int | None = None,
    cooldown_seconds: float | None = None,
    recovery_step: int | None = None,
    full_stop_reason: str | None = None,
) -> dict[str, Any]:
    """확장 필드(RTT/429/Recovery/Full Stop)를 딕셔너리로 수집."""
    fields: dict[str, Any] = {}
    if smoothed_rtt_ms is not None:
        fields["smoothed_rtt_ms"] = smoothed_rtt_ms
    if gradient is not None:
        fields["gradient"] = round(gradient, 6)
    if long_rtt_ms is not None:
        fields["long_rtt_ms"] = long_rtt_ms
    if consecutive_429s is not None:
        fields["consecutive_429s"] = consecutive_429s
    if cooldown_seconds is not None:
        fields["cooldown_seconds"] = cooldown_seconds
    if recovery_step is not None:
        fields["recovery_step"] = recovery_step
    if full_stop_reason:
        fields["full_stop_reason"] = full_stop_reason
    return fields


def _build_audit_data(
    action: str,
    *,
    old_limit: int | None = None,
    new_limit: int | None = None,
    reason: str | None = None,
    trigger_source: str | None = None,
    emergency_level: int | None = None,
    applied_multiplier: float | None = None,
    service_name: str | None = None,
    cb_state: str | None = None,
    rtt_ms: float | None = None,
    threshold_ms: int | None = None,
    # 확장 필드: RTT 추세 데이터
    smoothed_rtt_ms: float | None = None,
    gradient: float | None = None,
    long_rtt_ms: float | None = None,
    # 확장 필드: 429 연동
    consecutive_429s: int | None = None,
    cooldown_seconds: float | None = None,
    # 확장 필드: Recovery
    recovery_step: int | None = None,
    # 확장 필드: Full Stop
    full_stop_reason: str | None = None,
    # 확장 필드: Config Snapshot
    config_snapshot: dict[str, Any] | None = None,
    # Severity 오버라이드
    severity: AuditSeverity | None = None,
    # Correlation ID
    correlation_id: str | None = None,
    parent_event_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """감사 데이터 딕셔너리 구성 (확장)."""
    # Correlation ID 생성
    event_id, auto_parent_id = _get_correlation_ids()

    audit_data: dict[str, Any] = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": event_id,
        "correlation_id": correlation_id or event_id,
        "parent_event_id": parent_event_id or auto_parent_id,
        "severity": _get_severity(action, severity),
        "service": service_name or "default",
    }

    # 기본 필드
    optional_fields: dict[str, Any] = {
        "old_limit": old_limit,
        "new_limit": new_limit,
        "reason": reason,
        "trigger_source": trigger_source,
        "emergency_level": emergency_level,
        "applied_multiplier": applied_multiplier,
        "cb_state": cb_state,
        "rtt_ms": rtt_ms,
        "threshold_ms": threshold_ms,
    }

    # 확장 필드 수집
    optional_fields.update(
        _collect_extension_fields(
            smoothed_rtt_ms=smoothed_rtt_ms,
            gradient=gradient,
            long_rtt_ms=long_rtt_ms,
            consecutive_429s=consecutive_429s,
            cooldown_seconds=cooldown_seconds,
            recovery_step=recovery_step,
            full_stop_reason=full_stop_reason,
        )
    )

    for key, value in optional_fields.items():
        if value is not None:
            audit_data[key] = value

    if extra_data:
        audit_data.update(extra_data)

    # CASCADE_EVENT에만 config_snapshot 포함 (데이터 크기 고려)
    if config_snapshot and action in CASCADE_EVENT_ACTIONS:
        audit_data["config_snapshot"] = config_snapshot

    # 클러스터 정보 자동 주입
    audit_data = _inject_cluster_identity(audit_data)

    return audit_data


# =============================================================================
# WAL 및 CascadeEvent 기록 헬퍼 (Fail-Open)
# =============================================================================


def _write_to_wal_safe(action: str, audit_data: dict[str, Any]) -> None:
    """WAL에 감사 이벤트 기록 (Fail-Open)."""
    try:
        from selfhealing.services.audit.base import _write_to_wal

        _write_to_wal(
            event_type=action,
            source="AdaptiveThrottle",
            details=audit_data,
            success=True,
            domain="throttle",
        )
    except Exception as e:
        logger.debug(f"[ThrottleAudit] WAL write failed: {e}")


def _record_cascade_event_safe(action: str, audit_data: dict[str, Any]) -> None:
    """CascadeEvent 기록 (Fail-Open)."""
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()
        trigger_type = _map_action_to_trigger_type(action)
        effects = _build_throttle_effects(audit_data)

        auditor.record(
            trigger_type=trigger_type,
            trigger_details=audit_data,
            effects=effects,
            namespace="default",
            triggered_by="throttle",
        )
        logger.debug(f"[ThrottleAudit] CascadeEvent recorded for {action}")
    except Exception as e:
        logger.debug(f"[ThrottleAudit] CascadeEvent failed: {e}")


def _sync_to_central_immediately(audit_data: dict[str, Any]) -> None:
    """
    중앙 저장소에 즉시 동기화.

    Group Commit 대기 없이 개별 전송.
    실패해도 WAL에 이미 기록되어 있으므로 Fail-Open.
    """
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        worker = AuditSyncWorker.get_instance()
        if hasattr(worker, "sync_single"):
            worker.sync_single(audit_data)

    except Exception as e:
        logger.warning(f"[ThrottleAudit] Immediate sync failed (will retry): {e}")


# =============================================================================
# 메인 감사 기록 함수 (확장)
# =============================================================================


def record_throttle_audit(
    action: str,
    old_limit: int | None = None,
    new_limit: int | None = None,
    reason: str | None = None,
    trigger_source: str | None = None,
    emergency_level: int | None = None,
    applied_multiplier: float | None = None,
    service_name: str | None = None,
    cb_state: str | None = None,
    rtt_ms: float | None = None,
    threshold_ms: int | None = None,
    # 확장 필드: RTT 추세 데이터
    smoothed_rtt_ms: float | None = None,
    gradient: float | None = None,
    long_rtt_ms: float | None = None,
    # 확장 필드: 429 연동
    consecutive_429s: int | None = None,
    cooldown_seconds: float | None = None,
    # 확장 필드: Recovery
    recovery_step: int | None = None,
    # 확장 필드: Full Stop
    full_stop_reason: str | None = None,
    # 확장 필드: Config Snapshot (CASCADE_EVENT만)
    config_snapshot: dict[str, Any] | None = None,
    # 확장 필드: Transaction Policy
    is_critical: bool | None = None,
    # Severity 오버라이드
    severity: AuditSeverity | None = None,
    # Correlation ID
    correlation_id: str | None = None,
    parent_event_id: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> None:
    """
    Throttle 감사 이벤트 기록 (확장).

    모든 감사 이벤트는:
    1. 샘플링 확인 (일반 이벤트만)
    2. 비동기 큐에 추가 (Non-blocking)
    3. 워커가 WAL에 기록
    4. CascadeEvent에 선택적 기록 (비상 상황만)

    Args:
        action: 감사 이벤트 유형 (AUDIT_THROTTLE_* 상수)
        old_limit: 이전 limit 값
        new_limit: 새 limit 값
        reason: 조정 사유
        trigger_source: 트리거 소스 (emergency, cb, gradient, sla, 429 등)
        emergency_level: Emergency 레벨 (0-3)
        applied_multiplier: 적용된 배율
        service_name: 서비스 이름
        cb_state: Circuit Breaker 상태
        rtt_ms: RTT (ms) - 원시 RTT
        threshold_ms: SLA 임계값 (ms)
        smoothed_rtt_ms: EMA 평활화된 RTT (ms)
        gradient: RTT 기울기 (추세). 양수면 증가, 음수면 감소
        long_rtt_ms: 장기 RTT (noload_latency 계산용)
        consecutive_429s: 연속 429 횟수
        cooldown_seconds: Cooldown 시간 (초)
        recovery_step: Recovery Dampening 단계
        full_stop_reason: Full Stop 사유
        config_snapshot: Throttle 설정 스냅샷 (CASCADE_EVENT만)
        is_critical: True면 즉시 동기화 (None이면 자동 결정)
        severity: Severity 오버라이드
        correlation_id: Correlation ID
        parent_event_id: 부모 이벤트 ID
        extra_data: 추가 데이터
    """
    try:
        # 샘플링 확인
        if not should_sample(action):
            logger.debug(f"[ThrottleAudit] Sampled out: {action}")
            return

        # 워커 시작 확인
        _start_audit_worker()

        # 감사 데이터 구성
        audit_data = _build_audit_data(
            action,
            old_limit=old_limit,
            new_limit=new_limit,
            reason=reason,
            trigger_source=trigger_source,
            emergency_level=emergency_level,
            applied_multiplier=applied_multiplier,
            service_name=service_name,
            cb_state=cb_state,
            rtt_ms=rtt_ms,
            threshold_ms=threshold_ms,
            smoothed_rtt_ms=smoothed_rtt_ms,
            gradient=gradient,
            long_rtt_ms=long_rtt_ms,
            consecutive_429s=consecutive_429s,
            cooldown_seconds=cooldown_seconds,
            recovery_step=recovery_step,
            full_stop_reason=full_stop_reason,
            config_snapshot=config_snapshot,
            severity=severity,
            correlation_id=correlation_id,
            parent_event_id=parent_event_id,
            extra_data=extra_data,
        )

        # is_critical 자동 결정
        if is_critical is None:
            is_critical = _is_critical_action(action)

        audit_data["_is_critical"] = is_critical

        # 큐에 Non-blocking 추가
        try:
            _audit_queue.put_nowait(audit_data)
        except Full:
            # 큐 가득 참 - Fail-Open
            logger.warning("[ThrottleAudit] Queue full, dropping event")

            # 중요 이벤트는 동기 처리로 fallback
            if is_critical:
                _process_audit_event(audit_data)

        logger.debug(f"[ThrottleAudit] Queued {action}: event_id={audit_data.get('event_id')}")

    except ImportError:
        logger.debug("[ThrottleAudit] Audit module not available")
    except Exception as e:
        logger.warning(f"[ThrottleAudit] Failed to record audit: {e}")


# =============================================================================
# CascadeEvent 매핑 및 Effects 빌더
# =============================================================================


def _map_action_to_trigger_type(action: str) -> str:
    """감사 액션을 CascadeEvent trigger_type으로 매핑."""
    mapping = {
        AUDIT_THROTTLE_EMERGENCY_SYNC: "THROTTLE_EMERGENCY_SYNC",
        AUDIT_THROTTLE_CB_SYNC: "THROTTLE_CB_SYNC",
        AUDIT_THROTTLE_LIMIT_ADJUSTED: "THROTTLE_LIMIT_ADJUSTED",
        AUDIT_THROTTLE_SLA_BREACH: "THROTTLE_SLA_BREACH",
        AUDIT_THROTTLE_SLA_WARNING: "THROTTLE_SLA_WARNING",
        AUDIT_THROTTLE_SLA_CRITICAL: "THROTTLE_SLA_CRITICAL",
        AUDIT_THROTTLE_FULL_STOP_ACTIVATED: "THROTTLE_FULL_STOP_ACTIVATED",
        AUDIT_THROTTLE_FULL_STOP_DEACTIVATED: "THROTTLE_FULL_STOP_DEACTIVATED",
        AUDIT_THROTTLE_429_RESPONSE: "THROTTLE_429_RESPONSE",
        AUDIT_THROTTLE_RECOVERY_STARTED: "THROTTLE_RECOVERY_STARTED",
        AUDIT_THROTTLE_RECOVERY_COMPLETED: "THROTTLE_RECOVERY_COMPLETED",
    }
    return mapping.get(action, "THROTTLE_UNKNOWN")


def _build_throttle_effects(audit_data: dict[str, Any]) -> list[dict[str, Any]]:
    """감사 데이터에서 CascadeEvent effects 구성."""
    effects = []
    action = audit_data.get("action", "")

    old_limit = audit_data.get("old_limit")
    new_limit = audit_data.get("new_limit")

    # Limit 변경 효과
    if old_limit is not None and new_limit is not None:
        change_percent = 0
        if old_limit > 0:
            change_percent = round((new_limit - old_limit) / old_limit * 100, 2)

        effects.append(
            {
                "action_type": "THROTTLE_LIMIT_CHANGE",
                "success": True,
                "details": {
                    "old_limit": old_limit,
                    "new_limit": new_limit,
                    "change_percent": change_percent,
                },
            }
        )

    # Emergency Level 효과
    emergency_level = audit_data.get("emergency_level")
    if emergency_level is not None:
        effects.append(
            {
                "action_type": "EMERGENCY_LEVEL_APPLIED",
                "success": True,
                "details": {
                    "level": emergency_level,
                    "multiplier": audit_data.get("applied_multiplier"),
                },
            }
        )

    # CB 상태 효과
    cb_state = audit_data.get("cb_state")
    if cb_state:
        effects.append(
            {
                "action_type": "CB_STATE_APPLIED",
                "success": True,
                "details": {
                    "cb_state": cb_state,
                    "service_name": audit_data.get("service_name"),
                },
            }
        )

    # Full Stop 효과
    if action == AUDIT_THROTTLE_FULL_STOP_ACTIVATED:
        effects.append(
            {
                "action_type": "FULL_STOP_ACTIVATED",
                "success": True,
                "details": {
                    "reason": audit_data.get("full_stop_reason"),
                    "all_requests_blocked": True,
                },
            }
        )
    elif action == AUDIT_THROTTLE_FULL_STOP_DEACTIVATED:
        effects.append(
            {
                "action_type": "FULL_STOP_DEACTIVATED",
                "success": True,
                "details": {
                    "recovery_started": True,
                },
            }
        )

    # SLA Critical 효과
    if action == AUDIT_THROTTLE_SLA_CRITICAL:
        effects.append(
            {
                "action_type": "SLA_CRITICAL_TRIGGERED",
                "success": True,
                "details": {
                    "rtt_ms": audit_data.get("rtt_ms"),
                    "threshold_ms": audit_data.get("threshold_ms"),
                },
            }
        )

    return effects


# =============================================================================
# 편의 함수 (각 이벤트 유형별)
# =============================================================================


def record_throttle_limit_adjusted(
    old_limit: int,
    new_limit: int,
    reason: str,
    trigger_source: str,
    gradient: float | None = None,
    rtt_ms: float | None = None,
    smoothed_rtt_ms: float | None = None,
    long_rtt_ms: float | None = None,
) -> None:
    """limit 변경 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_LIMIT_ADJUSTED,
        old_limit=old_limit,
        new_limit=new_limit,
        reason=reason,
        trigger_source=trigger_source,
        gradient=gradient,
        rtt_ms=rtt_ms,
        smoothed_rtt_ms=smoothed_rtt_ms,
        long_rtt_ms=long_rtt_ms,
    )


def record_throttle_sla_warning(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
    previous_limit: int,
    gradient: float | None = None,
    smoothed_rtt_ms: float | None = None,
) -> None:
    """SLA Warning 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_WARNING,
        old_limit=previous_limit,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        gradient=gradient,
        smoothed_rtt_ms=smoothed_rtt_ms,
        trigger_source="sla_warning",
    )


def record_throttle_sla_critical(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
    previous_limit: int,
    reduction_percent: int,
    gradient: float | None = None,
    smoothed_rtt_ms: float | None = None,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """SLA Critical 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_CRITICAL,
        old_limit=previous_limit,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        gradient=gradient,
        smoothed_rtt_ms=smoothed_rtt_ms,
        trigger_source="sla_critical",
        config_snapshot=config_snapshot,
        extra_data={"reduction_percent": reduction_percent},
    )


def record_throttle_emergency_sync(
    old_limit: int,
    new_limit: int,
    emergency_level: int,
    applied_multiplier: float,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """Emergency 연동 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_EMERGENCY_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        emergency_level=emergency_level,
        applied_multiplier=applied_multiplier,
        trigger_source="emergency_mode",
        config_snapshot=config_snapshot,
    )


def record_throttle_cb_sync(
    old_limit: int,
    new_limit: int,
    service_name: str,
    cb_state: str,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """CB 연동 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_CB_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        service_name=service_name,
        cb_state=cb_state,
        trigger_source="circuit_breaker",
        config_snapshot=config_snapshot,
    )


def record_throttle_full_stop_activated(
    previous_limit: int,
    reason: str,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """Full Stop 활성화 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
        old_limit=previous_limit,
        new_limit=0,
        full_stop_reason=reason,
        trigger_source="full_stop",
        config_snapshot=config_snapshot,
    )


def record_throttle_full_stop_deactivated(
    new_limit: int,
    config_snapshot: dict[str, Any] | None = None,
) -> None:
    """Full Stop 비활성화 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
        old_limit=0,
        new_limit=new_limit,
        trigger_source="full_stop_recovery",
        config_snapshot=config_snapshot,
    )


def record_throttle_429_response(
    key: str,
    consecutive_429s: int,
    cooldown_seconds: float,
    limit_before: int,
    limit_after: int,
) -> None:
    """429 응답 연동 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_429_RESPONSE,
        old_limit=limit_before,
        new_limit=limit_after,
        consecutive_429s=consecutive_429s,
        cooldown_seconds=cooldown_seconds,
        trigger_source="rate_limit_429",
        extra_data={"rate_limit_key": key},
    )


def record_throttle_recovery_started(
    base_limit: int,
    initial_limit: int,
    step: int = 0,
) -> None:
    """Recovery Dampening 시작 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_RECOVERY_STARTED,
        old_limit=0 if step == 0 else None,  # Full Stop에서 복구 시
        new_limit=initial_limit,
        recovery_step=step,
        trigger_source="recovery_dampening",
        extra_data={"base_limit": base_limit, "dampening_percent": 80},
    )


def record_throttle_recovery_completed(
    final_limit: int,
) -> None:
    """Recovery 완료 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_RECOVERY_COMPLETED,
        new_limit=final_limit,
        recovery_step=2,  # 100%
        trigger_source="recovery_completed",
    )


def record_throttle_sla_breach(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
) -> None:
    """SLA 위반 시 감사 기록 (레거시 호환)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_BREACH,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        trigger_source="sla_threshold",
    )

# 190. Audit System - AdaptiveThrottle 감사 로깅 연동 구현

> **문서 버전**: 2.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/throttle/audit.py`, `selfhealing/services/audit/base.py`, `selfhealing/audit/cascade_auditor.py`
> **확장 리뷰**: 15가지 리뷰항목 구현 (섹션 8-10)

## 1. 개요

본 문서는 `AdaptiveThrottle`의 감사 로깅(Audit Logging) 시스템 연동 구현을 정의합니다.

### 1.1 문제 정의

현재 `AdaptiveThrottle`은 **부분적인 감사 로깅만 구현**:
- `_record_audit_safe()` 헬퍼로 기본 감사 기록
- Emergency/CB 연동 시에만 CascadeEvent 기록

**문제점**: 전체 limit 변경 이력, SLA 위반 이력의 체계적 감사 기록 부재

---

## 2. 현재 구현 분석

### 2.1 기존 감사 헬퍼 함수

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) (Line 48-68)

```python
def _record_audit_safe(
    action: str,
    **kwargs,
) -> None:
    """
    감사 로그 기록 (Fail-Open).

    실패해도 주요 기능에 영향 없음.
    """
    try:
        from selfhealing.services.throttle.audit import record_throttle_audit

        record_throttle_audit(action=action, **kwargs)
    except ImportError:
        logger.debug("[AdaptiveThrottle] Audit module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record audit: {e}")
```

### 2.2 Throttle 전용 감사 모듈

**코드 위치**: [throttle/audit.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/audit.py)

```python
# 감사 이벤트 타입 상수
AUDIT_THROTTLE_LIMIT_ADJUSTED = "throttle_limit_adjusted"
AUDIT_THROTTLE_EMERGENCY_SYNC = "throttle_emergency_sync"
AUDIT_THROTTLE_CB_SYNC = "throttle_cb_sync"
AUDIT_THROTTLE_SLA_BREACH = "throttle_sla_breach"


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
    extra_data: dict[str, Any] | None = None,
) -> None:
    """Throttle 감사 이벤트 기록."""
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()

        # 감사 데이터 구성
        audit_data = {
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        # ... (필드 추가)

        # CascadeEvent로 기록 (비상 상황 강등만)
        if action in (AUDIT_THROTTLE_EMERGENCY_SYNC, AUDIT_THROTTLE_CB_SYNC):
            _record_cascade_event(auditor, action, audit_data)

        logger.debug(f"[ThrottleAudit] Recorded {action}: {audit_data}")
    except ImportError:
        logger.debug("[ThrottleAudit] Audit module not available")
```

### 2.3 WAL 기반 감사 시스템 구조

**코드 위치**: [audit/base.py](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py)

```python
"""
Audit Base - WAL 공통 로직 및 헬퍼 함수

WAL 기반 누락 0 보장 (20_AUDIT_UNIFICATION_PLAN.md ADR-005):
- 모든 audit 이벤트는 WAL에 먼저 기록 (로컬 파일, 거의 실패 안함)
- 이후 중앙 저장소에 기록 시도 (Best Effort)
- Background Sync Worker가 WAL → 중앙 저장소 동기화
- Reconciler가 주기적으로 누락 감지 및 재전송
"""

def _write_to_wal(
    event_type: str,
    source: str,
    details: dict[str, Any],
    success: bool = True,
    error_message: str | None = None,
    domain: str | None = None,
    target_id: str | None = None,
    actor_roles: list[str] | None = None,
    trace_id: str | None = None,
) -> int | None:
    """WAL에 audit 이벤트 기록."""
    wal = _get_wal()
    if wal is None:
        return None

    # 컨텍스트에서 정보 추출
    actor_id, actor_type, final_roles = _get_actor_info(actor_roles)
    final_trace_id = _get_trace_id_from_context(trace_id)
    celery_context = _get_celery_context()

    wal_entry = {
        "record_id": f"audit-{uuid.uuid4().hex[:12]}",
        "event_type": event_type,
        "trace_id": final_trace_id,
        "source": source,
        "details": details,
        "success": success,
        # ... (추가 필드)
    }

    seq = wal.write(wal_entry)
    return seq
```

---

## 3. 확장 감사 설계

### 3.1 감사 이벤트 분류

| 카테고리 | 이벤트 타입 | 설명 | CascadeEvent |
|----------|-------------|------|--------------|
| **Limit 변경** | `throttle_limit_adjusted` | 모든 limit 변경 | ✗ |
| **SLA 위반** | `throttle_sla_warning` | SLA Warning 도달 | ✗ |
| **SLA 위반** | `throttle_sla_critical` | SLA Critical 도달 | ✓ |
| **Emergency** | `throttle_emergency_sync` | Emergency 연동 | ✓ |
| **CB 연동** | `throttle_cb_sync` | CB 상태 연동 | ✓ |
| **Full Stop** | `throttle_full_stop_activated` | Full Stop 활성화 | ✓ |
| **Full Stop** | `throttle_full_stop_deactivated` | Full Stop 비활성화 | ✓ |
| **429 연동** | `throttle_429_response` | 외부 API 429 연동 | ✗ |
| **Recovery** | `throttle_recovery_started` | Recovery Dampening 시작 | ✗ |
| **Recovery** | `throttle_recovery_completed` | Recovery 완료 | ✗ |

### 3.2 감사 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           감사 로깅 아키텍처                                     │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐                                                          │
│  │ AdaptiveThrottle  │                                                          │
│  │                   │                                                          │
│  │  Limit 변경       │──► _record_audit_safe()                                  │
│  │  Emergency 연동   │──► record_throttle_emergency_sync()                      │
│  │  Full Stop        │──► record_throttle_full_stop()                           │
│  └───────────────────┘                                                          │
│            │                                                                    │
│            ▼                                                                    │
│  ┌───────────────────────────────────────────────────────────────┐              │
│  │                    throttle/audit.py                          │              │
│  │                                                               │              │
│  │  record_throttle_audit()                                      │              │
│  │       │                                                       │              │
│  │       ├──► 일반 감사 로그 (WAL)                               │              │
│  │       │                                                       │              │
│  │       └──► CascadeEvent (비상 상황만)                         │              │
│  └────────────────────────┬──────────────────────────────────────┘              │
│                           │                                                     │
│            ┌──────────────┼──────────────┐                                      │
│            │              │              │                                      │
│            ▼              ▼              ▼                                      │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐                            │
│  │     WAL      │ │ CascadeEvent │ │  In-Memory   │                            │
│  │ (Primary)    │ │   Auditor    │ │   Buffer     │                            │
│  │              │ │              │ │  (Fallback)  │                            │
│  └──────┬───────┘ └──────────────┘ └──────────────┘                            │
│         │                                                                       │
│         │ Background Sync                                                       │
│         ▼                                                                       │
│  ┌──────────────┐                                                               │
│  │ 중앙 저장소  │                                                               │
│  │ (DB/Kafka)   │                                                               │
│  └──────────────┘                                                               │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 구현 코드

### 4.1 확장된 감사 이벤트 타입

**수정 위치**: `throttle/audit.py`

```python
"""
Throttle 감사 로그 및 CascadeEvent 연동 모듈 (확장).

감사 이벤트 전체 목록:
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
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
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

# SLA 위반
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


# CascadeEvent 기록 대상 이벤트
CASCADE_EVENT_ACTIONS = {
    AUDIT_THROTTLE_EMERGENCY_SYNC,
    AUDIT_THROTTLE_CB_SYNC,
    AUDIT_THROTTLE_SLA_CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
    AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
}
```

### 4.2 확장된 감사 기록 함수

**수정 위치**: `throttle/audit.py`

```python
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
    gradient: float | None = None,
    consecutive_429s: int | None = None,
    cooldown_seconds: float | None = None,
    recovery_step: int | None = None,
    full_stop_reason: str | None = None,
    extra_data: dict[str, Any] | None = None,
) -> None:
    """
    Throttle 감사 이벤트 기록.

    모든 감사 이벤트는:
    1. WAL에 기록 (누락 0 보장)
    2. CascadeEvent에 선택적 기록 (비상 상황만)

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
        rtt_ms: RTT (ms)
        threshold_ms: SLA 임계값 (ms)
        gradient: RTT 기울기
        consecutive_429s: 연속 429 횟수
        cooldown_seconds: Cooldown 시간 (초)
        recovery_step: Recovery Dampening 단계
        full_stop_reason: Full Stop 사유
        extra_data: 추가 데이터
    """
    try:
        # 감사 데이터 구성
        audit_data = {
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "service": service_name or "default",
        }

        # 선택적 필드 추가
        if old_limit is not None:
            audit_data["old_limit"] = old_limit
        if new_limit is not None:
            audit_data["new_limit"] = new_limit
        if reason:
            audit_data["reason"] = reason
        if trigger_source:
            audit_data["trigger_source"] = trigger_source
        if emergency_level is not None:
            audit_data["emergency_level"] = emergency_level
        if applied_multiplier is not None:
            audit_data["applied_multiplier"] = applied_multiplier
        if cb_state:
            audit_data["cb_state"] = cb_state
        if rtt_ms is not None:
            audit_data["rtt_ms"] = rtt_ms
        if threshold_ms is not None:
            audit_data["threshold_ms"] = threshold_ms
        if gradient is not None:
            audit_data["gradient"] = gradient
        if consecutive_429s is not None:
            audit_data["consecutive_429s"] = consecutive_429s
        if cooldown_seconds is not None:
            audit_data["cooldown_seconds"] = cooldown_seconds
        if recovery_step is not None:
            audit_data["recovery_step"] = recovery_step
        if full_stop_reason:
            audit_data["full_stop_reason"] = full_stop_reason
        if extra_data:
            audit_data.update(extra_data)

        # 1. WAL 기록 (모든 이벤트)
        _write_to_wal_safe(action, audit_data)

        # 2. CascadeEvent 기록 (비상 상황만)
        if action in CASCADE_EVENT_ACTIONS:
            _record_cascade_event_safe(action, audit_data)

        logger.debug(f"[ThrottleAudit] Recorded {action}: {audit_data}")

    except ImportError:
        logger.debug("[ThrottleAudit] Audit module not available")
    except Exception as e:
        logger.warning(f"[ThrottleAudit] Failed to record audit: {e}")


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
```

### 4.3 편의 함수

**수정 위치**: `throttle/audit.py`

```python
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
    )


def record_throttle_sla_warning(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
    previous_limit: int,
    gradient: float | None = None,
) -> None:
    """SLA Warning 시 감사 기록."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_WARNING,
        old_limit=previous_limit,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        gradient=gradient,
        trigger_source="sla_warning",
    )


def record_throttle_sla_critical(
    rtt_ms: float,
    threshold_ms: int,
    current_limit: int,
    previous_limit: int,
    reduction_percent: int,
    gradient: float | None = None,
) -> None:
    """SLA Critical 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_SLA_CRITICAL,
        old_limit=previous_limit,
        new_limit=current_limit,
        rtt_ms=rtt_ms,
        threshold_ms=threshold_ms,
        gradient=gradient,
        trigger_source="sla_critical",
        extra_data={"reduction_percent": reduction_percent},
    )


def record_throttle_emergency_sync(
    old_limit: int,
    new_limit: int,
    emergency_level: int,
    applied_multiplier: float,
) -> None:
    """Emergency 연동 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_EMERGENCY_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        emergency_level=emergency_level,
        applied_multiplier=applied_multiplier,
        trigger_source="emergency_mode",
    )


def record_throttle_cb_sync(
    old_limit: int,
    new_limit: int,
    service_name: str,
    cb_state: str,
) -> None:
    """CB 연동 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_CB_SYNC,
        old_limit=old_limit,
        new_limit=new_limit,
        service_name=service_name,
        cb_state=cb_state,
        trigger_source="circuit_breaker",
    )


def record_throttle_full_stop_activated(
    previous_limit: int,
    reason: str,
) -> None:
    """Full Stop 활성화 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
        old_limit=previous_limit,
        new_limit=0,
        full_stop_reason=reason,
        trigger_source="full_stop",
    )


def record_throttle_full_stop_deactivated(
    new_limit: int,
) -> None:
    """Full Stop 비활성화 시 감사 기록 (CascadeEvent 포함)."""
    record_throttle_audit(
        action=AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
        old_limit=0,
        new_limit=new_limit,
        trigger_source="full_stop_recovery",
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
```

### 4.4 CascadeEvent Effects 빌더

**수정 위치**: `throttle/audit.py`

```python
def _map_action_to_trigger_type(action: str) -> str:
    """감사 액션을 CascadeEvent trigger_type으로 매핑."""
    mapping = {
        AUDIT_THROTTLE_EMERGENCY_SYNC: "THROTTLE_EMERGENCY_SYNC",
        AUDIT_THROTTLE_CB_SYNC: "THROTTLE_CB_SYNC",
        AUDIT_THROTTLE_SLA_CRITICAL: "THROTTLE_SLA_CRITICAL",
        AUDIT_THROTTLE_FULL_STOP_ACTIVATED: "THROTTLE_FULL_STOP_ACTIVATED",
        AUDIT_THROTTLE_FULL_STOP_DEACTIVATED: "THROTTLE_FULL_STOP_DEACTIVATED",
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

        effects.append({
            "action_type": "THROTTLE_LIMIT_CHANGE",
            "success": True,
            "details": {
                "old_limit": old_limit,
                "new_limit": new_limit,
                "change_percent": change_percent,
            },
        })

    # Emergency Level 효과
    emergency_level = audit_data.get("emergency_level")
    if emergency_level is not None:
        effects.append({
            "action_type": "EMERGENCY_LEVEL_APPLIED",
            "success": True,
            "details": {
                "level": emergency_level,
                "multiplier": audit_data.get("applied_multiplier"),
            },
        })

    # CB 상태 효과
    cb_state = audit_data.get("cb_state")
    if cb_state:
        effects.append({
            "action_type": "CB_STATE_APPLIED",
            "success": True,
            "details": {
                "cb_state": cb_state,
                "service_name": audit_data.get("service_name"),
            },
        })

    # Full Stop 효과
    if action == AUDIT_THROTTLE_FULL_STOP_ACTIVATED:
        effects.append({
            "action_type": "FULL_STOP_ACTIVATED",
            "success": True,
            "details": {
                "reason": audit_data.get("full_stop_reason"),
                "all_requests_blocked": True,
            },
        })
    elif action == AUDIT_THROTTLE_FULL_STOP_DEACTIVATED:
        effects.append({
            "action_type": "FULL_STOP_DEACTIVATED",
            "success": True,
            "details": {
                "recovery_started": True,
            },
        })

    # SLA Critical 효과
    if action == AUDIT_THROTTLE_SLA_CRITICAL:
        effects.append({
            "action_type": "SLA_CRITICAL_TRIGGERED",
            "success": True,
            "details": {
                "rtt_ms": audit_data.get("rtt_ms"),
                "threshold_ms": audit_data.get("threshold_ms"),
            },
        })

    return effects
```

---

## 5. AdaptiveThrottle 통합

### 5.1 감사 로깅 통합 위치

**수정 위치**: `adaptive.py`

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    """AdaptiveThrottle with comprehensive audit logging."""

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """Adjust limit with audit logging."""
        # ... 기존 로직 ...

        if rtt_ms >= self.config.sla_critical_ms:
            # 기존: _emit_throttle_event("THROTTLE_SLA_CRITICAL", ...)

            # 감사 로깅 추가
            from selfhealing.services.throttle.audit import record_throttle_sla_critical
            record_throttle_sla_critical(
                rtt_ms=rtt_ms,
                threshold_ms=self.config.sla_critical_ms,
                current_limit=self._current_limit,
                previous_limit=previous_limit,
                reduction_percent=30,
                gradient=gradient,
            )
            return

        if rtt_ms >= self.config.sla_warning_ms:
            # 감사 로깅 추가
            from selfhealing.services.throttle.audit import record_throttle_sla_warning
            record_throttle_sla_warning(
                rtt_ms=rtt_ms,
                threshold_ms=self.config.sla_warning_ms,
                current_limit=self._current_limit,
                previous_limit=previous_limit,
                gradient=gradient,
            )

    def activate_full_stop(self, reason: str) -> None:
        """Full Stop with audit logging."""
        if self._full_stop_active:
            return

        previous_limit = self._current_limit
        self._full_stop_active = True
        self._current_limit = 0

        # 감사 로깅
        from selfhealing.services.throttle.audit import record_throttle_full_stop_activated
        record_throttle_full_stop_activated(
            previous_limit=previous_limit,
            reason=reason,
        )

        # ... 기존 이벤트 발행 로직 ...

    def deactivate_full_stop(self) -> None:
        """Full Stop deactivation with audit logging."""
        if not self._full_stop_active:
            return

        self._full_stop_active = False
        self.start_recovery_dampening()

        # 감사 로깅
        from selfhealing.services.throttle.audit import record_throttle_full_stop_deactivated
        record_throttle_full_stop_deactivated(new_limit=self._current_limit)

    def start_recovery_dampening(self) -> None:
        """Recovery dampening with audit logging."""
        # 기존 로직...
        target_limit = int(self._base_limit_before_emergency * 0.8)
        self.current_limit = target_limit

        # 감사 로깅
        from selfhealing.services.throttle.audit import record_throttle_recovery_started
        record_throttle_recovery_started(
            base_limit=self._base_limit_before_emergency,
            initial_limit=target_limit,
            step=0,
        )

    def complete_recovery_dampening(self) -> None:
        """Recovery completion with audit logging."""
        # 기존 로직...
        self.current_limit = self._base_limit_before_emergency

        # 감사 로깅
        from selfhealing.services.throttle.audit import record_throttle_recovery_completed
        record_throttle_recovery_completed(final_limit=self._base_limit_before_emergency)
```

---

## 6. 감사 로그 조회

### 6.1 WAL 로그 조회

```python
from selfhealing.services.audit.base import get_wal_stats

# WAL 통계 조회
stats = get_wal_stats()
print(f"Total entries: {stats['total_entries']}")
print(f"Last sequence: {stats['last_sequence']}")
```

### 6.2 CascadeEvent 조회

```python
from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

auditor = get_cascade_event_auditor()

# 최근 Throttle 관련 CascadeEvent 조회
events = auditor.query(
    trigger_type_prefix="THROTTLE_",
    limit=100,
    since_hours=24,
)

for event in events:
    print(f"{event.timestamp}: {event.trigger_type} - {event.trigger_details}")
```

---

## 7. 테스트

### 7.1 감사 로깅 테스트

```python
class TestThrottleAuditLogging:
    """Throttle 감사 로깅 테스트."""

    def test_limit_adjusted_writes_to_wal(self):
        """limit 변경 시 WAL 기록 확인."""
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            record_throttle_limit_adjusted(
                old_limit=100,
                new_limit=80,
                reason="gradient_increase",
                trigger_source="gradient",
            )

            mock_wal.assert_called_once()
            call_args = mock_wal.call_args
            assert call_args.kwargs["event_type"] == "throttle_limit_adjusted"

    def test_sla_critical_records_cascade_event(self):
        """SLA Critical 시 CascadeEvent 기록 확인."""
        with patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor") as mock_auditor:
            mock_instance = MagicMock()
            mock_auditor.return_value = mock_instance

            record_throttle_sla_critical(
                rtt_ms=600.0,
                threshold_ms=500,
                current_limit=70,
                previous_limit=100,
                reduction_percent=30,
            )

            mock_instance.record.assert_called_once()
            call_args = mock_instance.record.call_args
            assert call_args.kwargs["trigger_type"] == "THROTTLE_SLA_CRITICAL"

    def test_full_stop_records_both_wal_and_cascade(self):
        """Full Stop 시 WAL과 CascadeEvent 모두 기록 확인."""
        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            with patch("selfhealing.audit.cascade_auditor.get_cascade_event_auditor") as mock_auditor:
                mock_instance = MagicMock()
                mock_auditor.return_value = mock_instance

                record_throttle_full_stop_activated(
                    previous_limit=100,
                    reason="LEVEL_3+DB_CB_OPEN+BUDGET_EXHAUSTED",
                )

                # WAL 기록 확인
                mock_wal.assert_called_once()

                # CascadeEvent 기록 확인
                mock_instance.record.assert_called_once()
```

---

## 8. 확장 리뷰항목 구현 (15가지)

본 섹션은 감사 시스템의 완성도를 높이기 위한 15가지 리뷰항목의 상세 구현입니다.

### 8.1 리뷰항목 요약

| # | 항목 | 상태 | 코드 근거 |
|---|------|------|-----------|
| 1 | 페이로드 확장 (smoothed_rtt, gradient) | 구현 필요 | `GradientCalculator.get_snapshot()` |
| 2 | Correlation ID 필드 | 구현 필요 | `trace_id` 패턴 활용 |
| 3 | Region 정보 자동 주입 | 구현 필요 | `ClusterIdentity` |
| 4 | DiskPersistentBuffer 통합 | **우선순위 1** | `disk_buffer.py` 176번 |
| 5 | Time-based retention policy | 구현 필요 | `retention_days` 설정 존재 |
| 6 | Transaction policy (is_critical) | 구현 필요 | Fail-Open 패턴 확장 |
| 7 | Audit log sampling | 구현 필요 | 일반 이벤트만 대상 |
| 8 | Async background worker | 구현 필요 | `ResilientRecorder` 패턴 |
| 9 | Prometheus Exemplar | ✅ 구현 완료 | `trace_id` Exemplar |
| 10 | Loki/ELK tagging | 가이드 필요 | `LokiConfig.labels` |
| 11 | Severity field | 구현 필요 | 이벤트별 레벨 정의 |
| 12 | Full Stop event escalation | 구현 필요 | `CASCADE_EVENT_ACTIONS` 확장 |
| 13 | Meta-Watchdog audit probe | 구현 필요 | `HealthProbeManager` 확장 |
| 14 | Immutable Audit Log 가이드 | 가이드 필요 | `WORMAdapter` 존재 |
| 15 | Config Snapshot recording | 구현 필요 | `get_stats()` 활용 |

---

### 8.2 [리뷰 1] 페이로드 확장 - smoothed_rtt, gradient

**문제**: 현재 `rtt_ms`만 기록하고 있어 RTT 추세 분석이 어려움

**코드 근거**: `GradientCalculator.get_snapshot()` - [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) Line 397-410

```python
# 기존 GradientCalculator.get_snapshot() - (smoothed_rtt, gradient) 튜플 반환
def get_snapshot(self) -> tuple[float | None, float]:
    """현재 smoothed_rtt와 gradient 스냅샷 반환."""
    with self._lock:
        rtt = self._smoothed_rtt
        if self._prev_smoothed_rtt is not None and rtt is not None:
            grad = (rtt - self._prev_smoothed_rtt) / self._prev_smoothed_rtt
        else:
            grad = 0.0
        return rtt, grad
```

**구현 - 확장된 record_throttle_audit() 시그니처**:

```python
def record_throttle_audit(
    action: str,
    old_limit: int | None = None,
    new_limit: int | None = None,
    reason: str | None = None,
    trigger_source: str | None = None,
    # ... 기존 파라미터 ...

    # 리뷰 1: 페이로드 확장 - Netflix Gradient 알고리즘 데이터
    smoothed_rtt_ms: float | None = None,  # EMA 평활화된 RTT
    gradient: float | None = None,          # RTT 기울기 (추세)
    long_rtt_ms: float | None = None,       # 장기 RTT (noload_latency 계산용)

    extra_data: dict[str, Any] | None = None,
) -> None:
    """
    Throttle 감사 이벤트 기록 (확장).

    Args:
        smoothed_rtt_ms: EMA로 평활화된 RTT (밀리초). GradientCalculator._smoothed_rtt
        gradient: RTT 기울기. 양수면 증가 추세, 음수면 감소 추세
        long_rtt_ms: 장기 RTT. noload_latency 계산에 사용
    """
    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 리뷰 1: 확장 필드 추가
    if smoothed_rtt_ms is not None:
        audit_data["smoothed_rtt_ms"] = smoothed_rtt_ms
    if gradient is not None:
        audit_data["gradient"] = round(gradient, 6)  # 소수점 6자리
    if long_rtt_ms is not None:
        audit_data["long_rtt_ms"] = long_rtt_ms
```

**AdaptiveThrottle 통합**:

```python
class AdaptiveThrottle:
    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        # 기존 로직 ...

        # 리뷰 1: GradientCalculator에서 스냅샷 획득
        smoothed_rtt, gradient = self._gradient_calculator.get_snapshot()
        long_rtt = self._gradient_calculator.get_long_rtt()  # 장기 RTT

        # 감사 로깅에 전달
        record_throttle_limit_adjusted(
            old_limit=previous_limit,
            new_limit=self._current_limit,
            reason=reason,
            trigger_source=trigger_source,
            smoothed_rtt_ms=smoothed_rtt,  # 추가
            gradient=gradient,              # 추가
            long_rtt_ms=long_rtt,          # 추가
            rtt_ms=rtt_ms,                 # 기존 (원시 RTT 유지)
        )
```

**선택 이유**:
- `rtt_ms`(원시)와 `smoothed_rtt_ms`(평활화)를 분리하여 노이즈 vs 추세 구분 가능
- `gradient`로 RTT 변화 방향 즉시 파악 (양수: 악화, 음수: 개선)

---

### 8.3 [리뷰 2] Correlation ID 필드 - 이벤트 체인 추적

**문제**: 연쇄 이벤트 간 인과관계 추적 불가

**코드 근거**: 현재 `trace_id`만 존재 - [audit/base.py](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py) Line 134-140

```python
# 현재 구현
wal_entry = {
    "record_id": f"audit-{uuid.uuid4().hex[:12]}",
    "trace_id": final_trace_id,  # 요청 trace_id
    # parent_event_id 없음
}
```

**구현 - Correlation ID 체계**:

```python
from contextvars import ContextVar
import uuid

# 감사 이벤트 체인 컨텍스트
_audit_event_chain: ContextVar[list[str]] = ContextVar("audit_event_chain", default_factory=list)


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
    new_chain = chain + [event_id]
    _audit_event_chain.set(new_chain)

    return event_id, parent_id


def record_throttle_audit(
    action: str,
    # ... 기존 파라미터 ...

    # 리뷰 2: Correlation ID
    correlation_id: str | None = None,     # 명시적 지정 시 사용
    parent_event_id: str | None = None,    # 부모 이벤트 ID

) -> None:
    """Throttle 감사 이벤트 기록."""

    # 리뷰 2: Correlation ID 자동 생성
    event_id, auto_parent_id = _get_correlation_ids()

    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_id": event_id,
        "correlation_id": correlation_id or event_id,
        "parent_event_id": parent_event_id or auto_parent_id,
    }
```

**사용 예시 - Emergency → Throttle → CB 연쇄**:

```python
# Emergency 레벨 상승 → 감사 기록 (root 이벤트)
# event_id: evt-abc123, parent: None

# Throttle limit 감소 → 감사 기록 (자식 이벤트)
# event_id: evt-def456, parent: evt-abc123

# CB HALF_OPEN → 감사 기록 (손자 이벤트)
# event_id: evt-ghi789, parent: evt-def456
```

**선택 이유**:
- `ContextVar` 사용으로 스레드/비동기 안전
- 기존 `trace_id`(요청 단위)와 `event_id`(이벤트 단위) 분리

---

### 8.4 [리뷰 3] Region 정보 자동 주입

**문제**: Multi-Cluster 환경에서 어느 리전의 이벤트인지 구분 불가

**코드 근거**: `ClusterIdentity` 존재 - [cluster_identity.py](../../packages/selfhealing-python/src/selfhealing/core/cluster_identity.py) Line 30-50

```python
@dataclass(frozen=True)
class ClusterIdentity:
    """클러스터 식별 정보 (Immutable)."""

    cluster_id: str
    region: str | None = None
    environment: str = "production"
    tenant: str | None = None
    pod_id: str = field(default_factory=lambda: os.environ.get("HOSTNAME", "unknown"))
```

**구현 - 자동 주입 헬퍼**:

```python
def _inject_cluster_identity(audit_data: dict[str, Any]) -> dict[str, Any]:
    """
    감사 데이터에 클러스터 정보 자동 주입.

    리뷰 3: Region 정보 자동 주입 구현
    코드 근거: ClusterIdentity (70_MULTI_CLUSTER_ARCHITECTURE.md)
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


def record_throttle_audit(
    action: str,
    # ... 기존 파라미터 ...
) -> None:
    """Throttle 감사 이벤트 기록."""

    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 리뷰 3: 클러스터 정보 자동 주입
    audit_data = _inject_cluster_identity(audit_data)

    # ... 나머지 로직 ...
```

**출력 예시**:

```json
{
  "action": "throttle_limit_adjusted",
  "timestamp": "2026-02-06T10:30:00Z",
  "cluster": {
    "cluster_id": "seoul-prod-01",
    "region": "seoul",
    "environment": "production",
    "pod_id": "selfhealing-app-7d8f9-abc12"
  },
  "old_limit": 100,
  "new_limit": 80
}
```

**선택 이유**:
- `ClusterIdentity`가 이미 SSOT로 구현되어 있음 (70번 문서)
- Fail-Open: ClusterIdentity 실패해도 감사 기록 자체는 계속

---

### 8.5 [리뷰 4] DiskPersistentBuffer 통합 (우선순위 1)

**문제**: 현재 `InMemoryAuditBuffer`는 휘발성 - Pod 재시작 시 데이터 손실

**코드 근거**:
- 현재 사용: `InMemoryAuditBuffer` - [audit/base.py](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py) Line 131-138
- 대체 후보: `DiskPersistentBuffer` - [disk_buffer.py](../../packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py) (176번 문서)

```python
# 현재 구현 (휘발성)
def _save_to_memory_buffer(entry: dict) -> None:
    from selfhealing.audit.resilience import InMemoryAuditBuffer
    buffer = InMemoryAuditBuffer.get_instance()  # 메모리만 사용
    buffer.add(entry)
```

```python
# DiskPersistentBuffer 특징 (176번 문서)
class DiskPersistentBuffer:
    """
    LMDB 기반 Disk-Persistent Buffer.

    특징:
    - Pod 재시작에도 데이터 보존
    - ACID 트랜잭션
    - CRC32 체크섬으로 무결성 검증
    - Disk Full 시 Fail-Open 모드
    """
```

**구현 - Fallback 체인 개선**:

```python
# 수정 위치: audit/base.py

def _save_to_fallback_buffer(entry: dict) -> None:
    """
    Fallback 버퍼에 저장.

    리뷰 4: DiskPersistentBuffer 우선 사용
    Fallback 체인: DiskPersistentBuffer → InMemoryAuditBuffer → stderr

    코드 근거:
    - DiskPersistentBuffer: 176_DISK_PERSISTENT_BUFFER.md
    - InMemoryAuditBuffer: 기존 휘발성 버퍼 (레거시 호환)
    """
    # 1차: DiskPersistentBuffer (영속)
    try:
        from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

        buffer = DiskPersistentBuffer.get_instance()
        buffer.put(entry)
        logger.debug("[AuditBase] Saved to DiskPersistentBuffer")
        return

    except ImportError:
        logger.debug("[AuditBase] DiskPersistentBuffer not available")
    except Exception as e:
        logger.warning(f"[AuditBase] DiskPersistentBuffer failed: {e}")

    # 2차: InMemoryAuditBuffer (레거시 호환)
    try:
        from selfhealing.audit.resilience import InMemoryAuditBuffer

        buffer = InMemoryAuditBuffer.get_instance()
        buffer.add(entry)
        logger.debug("[AuditBase] Saved to InMemoryAuditBuffer (fallback)")
        return

    except Exception as e:
        logger.warning(f"[AuditBase] InMemoryAuditBuffer failed: {e}")

    # 3차: stderr (최후의 수단)
    import sys
    import json
    sys.stderr.write(f"[AUDIT_FALLBACK] {json.dumps(entry)}\n")
```

**설정 연동**:

```python
# settings/audit_settings.py 확장

class AuditSettings(BaseSettings):
    # 리뷰 4: DiskPersistentBuffer 설정
    use_disk_buffer: bool = Field(
        default=True,
        description="DiskPersistentBuffer 사용 여부 (False면 InMemoryAuditBuffer)",
    )

    disk_buffer_path: str = Field(
        default="/var/lib/selfhealing/audit",
        description="LMDB 저장 경로",
    )

    disk_buffer_map_size_mb: int = Field(
        default=100,
        ge=10,
        le=1024,
        description="LMDB map_size (MB)",
    )
```

**선택 이유**:
- `InMemoryAuditBuffer`는 휘발성이므로 "누락 0 보장" 원칙 위반 가능
- `DiskPersistentBuffer`는 LMDB 기반으로 Pod 재시작에도 데이터 보존
- 기존 `InMemoryAuditBuffer`는 레거시 호환용으로 유지

---

### 8.6 [리뷰 5] Time-based Retention Policy

**문제**: WAL 파일이 무한 증가할 수 있음

**코드 근거**: `retention_days` 설정 존재 - [audit_settings.py](../../packages/selfhealing-python/src/selfhealing/settings/audit_settings.py) Line 67-72

```python
class AuditSettings(BaseSettings):
    retention_days: int = Field(
        default=90,
        ge=30,
        le=365,
        description="감사 로그 보관 기간 (일)",
    )
```

**현재 상태**: `retention_days` 설정은 존재하지만 WAL 정리 로직 미구현

**구현 - WAL Retention Cleaner**:

```python
# 신규 파일: audit/retention_cleaner.py

"""
WAL Retention Cleaner - 시간 기반 정리.

리뷰 5: Time-based retention policy 구현
코드 근거: AuditSettings.retention_days
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from selfhealing.settings.audit_settings import get_audit_settings

logger = logging.getLogger(__name__)


class WALRetentionCleaner:
    """
    WAL 파일 보관 기간 기반 정리.

    정책:
    1. retention_days 이전 파일 삭제
    2. 파일 개수 제한 (max_files)과 병행
    3. Graceful: 중앙 저장소 동기화 완료된 파일만 대상
    """

    def __init__(
        self,
        wal_dir: Path | str,
        retention_days: int | None = None,
        check_synced: bool = True,
    ):
        """
        Initialize cleaner.

        Args:
            wal_dir: WAL 파일 디렉토리
            retention_days: 보관 기간 (None이면 설정에서 로드)
            check_synced: 동기화 완료 확인 여부
        """
        self._wal_dir = Path(wal_dir)
        self._retention_days = retention_days or get_audit_settings().retention_days
        self._check_synced = check_synced

    def cleanup(self) -> int:
        """
        보관 기간 초과 WAL 파일 정리.

        Returns:
            삭제된 파일 수
        """
        if not self._wal_dir.exists():
            return 0

        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        deleted_count = 0

        for wal_file in self._wal_dir.glob("*.wal"):
            try:
                # 파일 수정 시간 확인
                mtime = datetime.fromtimestamp(
                    wal_file.stat().st_mtime,
                    tz=timezone.utc,
                )

                if mtime < cutoff:
                    # 동기화 완료 확인 (옵션)
                    if self._check_synced and not self._is_synced(wal_file):
                        logger.warning(
                            f"[RetentionCleaner] Skipping unsynced old file: {wal_file}"
                        )
                        continue

                    wal_file.unlink()
                    deleted_count += 1
                    logger.info(
                        f"[RetentionCleaner] Deleted expired WAL: {wal_file.name} "
                        f"(age: {(datetime.now(timezone.utc) - mtime).days} days)"
                    )

            except Exception as e:
                logger.error(f"[RetentionCleaner] Failed to clean {wal_file}: {e}")

        return deleted_count

    def _is_synced(self, wal_file: Path) -> bool:
        """WAL 파일이 동기화 완료되었는지 확인."""
        # .synced 마커 파일 존재 확인
        synced_marker = wal_file.with_suffix(".synced")
        return synced_marker.exists()


def schedule_retention_cleanup(interval_hours: int = 24) -> None:
    """
    주기적 Retention 정리 스케줄링.

    Args:
        interval_hours: 정리 주기 (시간)
    """
    import threading

    def _cleanup_loop():
        from selfhealing.audit.wal import get_wal_config

        config = get_wal_config()
        cleaner = WALRetentionCleaner(wal_dir=config.wal_dir)

        while True:
            try:
                deleted = cleaner.cleanup()
                if deleted > 0:
                    logger.info(f"[RetentionCleaner] Cleaned {deleted} expired WAL files")
            except Exception as e:
                logger.error(f"[RetentionCleaner] Cleanup error: {e}")

            time.sleep(interval_hours * 3600)

    thread = threading.Thread(target=_cleanup_loop, daemon=True, name="WAL-RetentionCleaner")
    thread.start()
```

**Celery Beat 연동 (권장)**:

```python
# celery_config.py

CELERYBEAT_SCHEDULE = {
    'wal-retention-cleanup': {
        'task': 'selfhealing.tasks.cleanup_wal_retention',
        'schedule': crontab(hour=3, minute=0),  # 매일 03:00
    },
}
```

**선택 이유**:
- 시간 기반과 파일 개수 기반 정리를 병행
- 동기화 완료 확인으로 데이터 손실 방지

---

### 8.7 [리뷰 6] Transaction Policy - is_critical 옵션

**문제**: 모든 감사 이벤트가 동일하게 처리됨. 중요 이벤트도 Group Commit 지연 발생

**코드 근거**: Fail-Open 원칙 - [audit/base.py](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py)

**구현 - is_critical 플래그**:

```python
def record_throttle_audit(
    action: str,
    # ... 기존 파라미터 ...

    # 리뷰 6: 중요 이벤트 즉시 기록 옵션
    is_critical: bool = False,

) -> None:
    """
    Throttle 감사 이벤트 기록.

    Args:
        is_critical: True면 Group Commit 없이 즉시 동기화
    """
    # ... 감사 데이터 구성 ...

    # 리뷰 6: 중요 이벤트 처리
    if is_critical:
        _write_to_wal_sync(action, audit_data)  # 동기 기록
        _sync_to_central_immediately(audit_data)  # 즉시 중앙 저장소
    else:
        _write_to_wal_safe(action, audit_data)  # 기존 비동기


def _sync_to_central_immediately(audit_data: dict[str, Any]) -> None:
    """
    중앙 저장소에 즉시 동기화.

    Group Commit 대기 없이 개별 전송.
    실패해도 WAL에 이미 기록되어 있으므로 Fail-Open.
    """
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        worker = AuditSyncWorker.get_instance()
        worker.sync_single(audit_data)  # 개별 동기화 메서드 추가 필요

    except Exception as e:
        logger.warning(f"[ThrottleAudit] Immediate sync failed (will retry): {e}")


# CASCADE_EVENT 이벤트는 기본적으로 is_critical=True
CASCADE_EVENT_ACTIONS = {
    AUDIT_THROTTLE_EMERGENCY_SYNC,
    AUDIT_THROTTLE_CB_SYNC,
    AUDIT_THROTTLE_SLA_CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
    AUDIT_THROTTLE_FULL_STOP_DEACTIVATED,
}


def _is_critical_action(action: str) -> bool:
    """CASCADE_EVENT 대상 액션인지 확인."""
    return action in CASCADE_EVENT_ACTIONS
```

**자동 적용 로직**:

```python
def record_throttle_audit(action: str, is_critical: bool | None = None, ...):
    # is_critical이 명시되지 않으면 CASCADE_EVENT 여부로 결정
    if is_critical is None:
        is_critical = _is_critical_action(action)
```

**선택 이유**:
- `CASCADE_EVENT` 대상 이벤트는 자동으로 `is_critical=True`
- 일반 `throttle_limit_adjusted`는 Group Commit으로 I/O 효율성 유지

---

### 8.8 [리뷰 7] Audit Log Sampling

**문제**: 트래픽이 많을 때 감사 로그가 과도하게 생성됨

**코드 근거**: `ResilientRecorder` 패턴 - [resilient_recorder.py](../../packages/selfhealing-python/src/selfhealing/audit/resilient_recorder.py)

**구현 - 샘플링 레이트 적용**:

```python
# 신규: throttle/audit_sampling.py

"""
Audit Sampling - 고빈도 이벤트 샘플링.

리뷰 7: 일반 이벤트만 샘플링, CASCADE_EVENT는 100% 기록
"""

import random
from functools import lru_cache


@lru_cache(maxsize=1)
def get_sampling_config() -> dict[str, float]:
    """
    이벤트별 샘플링 비율 반환.

    Returns:
        {action: sample_rate} 딕셔너리 (1.0 = 100%)
    """
    from selfhealing.settings.audit_settings import get_audit_settings

    settings = get_audit_settings()

    return {
        # 일반 이벤트: 샘플링 적용
        "throttle_limit_adjusted": getattr(settings, 'sampling_rate_limit_adjusted', 0.1),  # 10%
        "throttle_429_response": getattr(settings, 'sampling_rate_429', 0.5),  # 50%
        "throttle_recovery_started": 1.0,  # 100% (중요)
        "throttle_recovery_completed": 1.0,  # 100% (중요)

        # CASCADE_EVENT 이벤트: 항상 100%
        "throttle_emergency_sync": 1.0,
        "throttle_cb_sync": 1.0,
        "throttle_sla_warning": 1.0,
        "throttle_sla_critical": 1.0,
        "throttle_full_stop_activated": 1.0,
        "throttle_full_stop_deactivated": 1.0,
    }


def should_sample(action: str) -> bool:
    """
    해당 이벤트를 기록할지 결정.

    Args:
        action: 감사 이벤트 타입

    Returns:
        True면 기록, False면 스킵
    """
    config = get_sampling_config()
    rate = config.get(action, 1.0)  # 기본 100%

    if rate >= 1.0:
        return True

    return random.random() < rate


# record_throttle_audit 수정
def record_throttle_audit(action: str, ...):
    # 리뷰 7: 샘플링 적용
    if not should_sample(action):
        logger.debug(f"[ThrottleAudit] Sampled out: {action}")
        return

    # ... 기존 로직 ...
```

**설정 추가**:

```python
# settings/audit_settings.py

class AuditSettings(BaseSettings):
    # 리뷰 7: 샘플링 설정
    sampling_rate_limit_adjusted: float = Field(
        default=0.1,
        ge=0.01,
        le=1.0,
        description="throttle_limit_adjusted 샘플링 비율 (0.1 = 10%)",
    )

    sampling_rate_429: float = Field(
        default=0.5,
        ge=0.01,
        le=1.0,
        description="throttle_429_response 샘플링 비율 (0.5 = 50%)",
    )

    # CASCADE_EVENT는 환경변수로 오버라이드 불가 (항상 100%)
```

**선택 이유**:
- `CASCADE_EVENT` 이벤트는 법적 효력 필요 → 100% 기록
- 일반 `throttle_limit_adjusted`는 빈도가 높으므로 샘플링
- 환경변수로 샘플링 비율 조정 가능

---

### 8.9 [리뷰 8] Async Background Worker

**문제**: 동기 기록으로 인한 메인 스레드 지연

**코드 근거**: `AuditSyncWorker` 패턴 - [sync_worker.py](../../packages/selfhealing-python/src/selfhealing/audit/sync_worker.py)

**구현 - 비동기 큐 기반**:

```python
# throttle/audit.py 수정

from queue import Queue, Full
import threading

# 감사 이벤트 큐 (Non-blocking)
_audit_queue: Queue[dict] = Queue(maxsize=10000)
_worker_started = False
_worker_lock = threading.Lock()


def _start_audit_worker() -> None:
    """백그라운드 감사 워커 시작."""
    global _worker_started

    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True

    def _worker_loop():
        while True:
            try:
                audit_data = _audit_queue.get(timeout=1.0)
                _process_audit_event(audit_data)
            except Exception:
                pass  # 타임아웃 또는 에러

    thread = threading.Thread(target=_worker_loop, daemon=True, name="ThrottleAuditWorker")
    thread.start()
    logger.info("[ThrottleAudit] Background worker started")


def _process_audit_event(audit_data: dict) -> None:
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


def record_throttle_audit(
    action: str,
    is_critical: bool | None = None,
    # ... 기존 파라미터 ...
) -> None:
    """
    Throttle 감사 이벤트 기록 (Non-blocking).

    리뷰 8: 큐 기반 비동기 처리
    """
    # 워커 시작 확인
    _start_audit_worker()

    # 감사 데이터 구성
    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "_is_critical": is_critical if is_critical is not None else _is_critical_action(action),
    }
    # ... 필드 추가 ...

    # 큐에 Non-blocking 추가
    try:
        _audit_queue.put_nowait(audit_data)
    except Full:
        # 큐 가득 참 - Fail-Open
        logger.warning("[ThrottleAudit] Queue full, dropping event")

        # 중요 이벤트는 동기 처리로 fallback
        if audit_data.get("_is_critical"):
            _process_audit_event(audit_data)
```

**선택 이유**:
- 기존 `AuditSyncWorker` 패턴 재활용
- `Queue.put_nowait()`으로 Non-blocking
- 큐 가득 참 시 중요 이벤트만 동기 fallback

---

### 8.10 [리뷰 9] Prometheus Exemplar (구현 완료)

**상태**: ✅ 이미 구현됨

**코드 근거**: [metrics.py](../../packages/selfhealing-python/src/selfhealing/metrics.py) - `trace_id` Exemplar

```python
# 현재 구현
histogram.observe(
    rtt_ms,
    exemplar={"trace_id": current_trace_id}
)
```

**권장 확장** (선택 사항):

```python
# 감사 이벤트 ID도 Exemplar에 추가
histogram.observe(
    rtt_ms,
    exemplar={
        "trace_id": current_trace_id,
        "audit_event_id": audit_event_id,  # 리뷰 2와 연계
    }
)
```

---

### 8.11 [리뷰 10] Loki/ELK Tagging 가이드

**문제**: 표준 라벨 없이 로그 검색 어려움

**코드 근거**: `LokiConfig.labels` - [worm_adapters.py](../../packages/selfhealing-python/src/selfhealing/adapters/audit/worm_adapters.py) Line 64-68

```python
@dataclass
class LokiConfig:
    endpoint: str = "http://loki:3100/loki/api/v1/push"
    labels: dict[str, str] = None

    def __post_init__(self):
        if self.labels is None:
            self.labels = {"job": "selfhealing-audit", "env": "production"}
```

**구현 - 표준 라벨 체계**:

```python
# 신규: audit/loki_labels.py

"""
Loki/ELK 표준 라벨 정의.

리뷰 10: 로그 시스템 친화적 라벨링
"""


def get_standard_labels(audit_data: dict) -> dict[str, str]:
    """
    감사 데이터에서 표준 라벨 추출.

    Loki Label Best Practice:
    - 낮은 카디널리티 (Low Cardinality)
    - 고정 값 선호 (cluster, env, component)
    - 높은 카디널리티는 라벨 대신 로그 내용으로
    """
    cluster_info = audit_data.get("cluster", {})

    return {
        # 낮은 카디널리티 라벨
        "job": "selfhealing-audit",
        "component": "throttle",
        "env": cluster_info.get("environment", "production"),
        "region": cluster_info.get("region", "unknown"),
        "cluster": cluster_info.get("cluster_id", "unknown"),

        # 이벤트 분류
        "audit_action": audit_data.get("action", "unknown"),
        "severity": audit_data.get("severity", "info"),

        # CASCADE_EVENT 여부
        "is_cascade": str(audit_data.get("action") in CASCADE_EVENT_ACTIONS).lower(),
    }


# LokiAdapter 수정
class LokiAdapter(WORMAdapter):
    def _build_push_payload(self, entry: AuditEntry) -> dict:
        # 리뷰 10: 표준 라벨 적용
        labels = get_standard_labels(entry.details)
        labels.update(self._config.labels)  # 사용자 정의 라벨 병합

        return {
            "streams": [{
                "stream": labels,
                "values": [[str(int(time.time() * 1e9)), entry.to_json()]]
            }]
        }
```

**ELK (Elasticsearch) Index Template**:

```json
{
  "index_patterns": ["selfhealing-audit-*"],
  "mappings": {
    "properties": {
      "action": { "type": "keyword" },
      "severity": { "type": "keyword" },
      "cluster.region": { "type": "keyword" },
      "cluster.cluster_id": { "type": "keyword" },
      "timestamp": { "type": "date" },
      "old_limit": { "type": "integer" },
      "new_limit": { "type": "integer" },
      "smoothed_rtt_ms": { "type": "float" },
      "gradient": { "type": "float" }
    }
  }
}
```

**선택 이유**:
- Loki는 낮은 카디널리티 라벨 권장
- `trace_id`, `event_id`는 라벨이 아닌 로그 내용으로

---

### 8.12 [리뷰 11] Severity 필드

**문제**: 모든 이벤트가 동일 레벨로 취급됨

**구현 - 이벤트별 Severity 정의**:

```python
# throttle/audit.py

from enum import Enum


class AuditSeverity(str, Enum):
    """감사 이벤트 심각도."""

    DEBUG = "debug"      # 디버깅용 (샘플링 대상)
    INFO = "info"        # 일반 정보
    WARNING = "warning"  # 주의 필요
    CRITICAL = "critical"  # 즉각 대응 필요


# 이벤트별 기본 Severity
AUDIT_SEVERITY_MAP: dict[str, AuditSeverity] = {
    # DEBUG: 디버깅용, 높은 샘플링
    AUDIT_THROTTLE_LIMIT_ADJUSTED: AuditSeverity.DEBUG,

    # INFO: 일반 정보
    AUDIT_THROTTLE_429_RESPONSE: AuditSeverity.INFO,
    AUDIT_THROTTLE_RECOVERY_STARTED: AuditSeverity.INFO,
    AUDIT_THROTTLE_RECOVERY_COMPLETED: AuditSeverity.INFO,

    # WARNING: 주의 필요
    AUDIT_THROTTLE_SLA_WARNING: AuditSeverity.WARNING,
    AUDIT_THROTTLE_CB_SYNC: AuditSeverity.WARNING,

    # CRITICAL: 즉각 대응 (CASCADE_EVENT)
    AUDIT_THROTTLE_SLA_CRITICAL: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_EMERGENCY_SYNC: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_ACTIVATED: AuditSeverity.CRITICAL,
    AUDIT_THROTTLE_FULL_STOP_DEACTIVATED: AuditSeverity.CRITICAL,
}


def _get_severity(action: str, override: AuditSeverity | None = None) -> str:
    """이벤트 Severity 결정."""
    if override:
        return override.value
    return AUDIT_SEVERITY_MAP.get(action, AuditSeverity.INFO).value


def record_throttle_audit(
    action: str,
    severity: AuditSeverity | None = None,  # 명시적 오버라이드
    # ... 기존 파라미터 ...
) -> None:
    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "severity": _get_severity(action, severity),  # 리뷰 11
    }
```

**Severity와 샘플링 연동**:

```python
def should_sample(action: str) -> bool:
    severity = AUDIT_SEVERITY_MAP.get(action, AuditSeverity.INFO)

    # CRITICAL, WARNING은 항상 100%
    if severity in (AuditSeverity.CRITICAL, AuditSeverity.WARNING):
        return True

    # DEBUG, INFO는 샘플링 적용
    config = get_sampling_config()
    rate = config.get(action, 1.0)
    return random.random() < rate
```

---

### 8.13 [리뷰 12] Full Stop Event Escalation

**문제**: Full Stop 활성화/비활성화가 `CASCADE_EVENT_ACTIONS`에 정의만 되고 구현 안됨

**코드 근거**: `activate_full_stop()` - [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) Line 1350-1400

```python
# 현재: EventBus만 사용, CascadeAuditor 미연동
def activate_full_stop(self, reason: str) -> None:
    self._full_stop_active = True
    self._current_limit = 0
    self._event_bus.publish("THROTTLE_FULL_STOP_ACTIVATED", {...})
```

**구현 - CascadeEvent 연동**:

```python
# adaptive.py 수정

def activate_full_stop(self, reason: str) -> None:
    """
    Full Stop 활성화.

    리뷰 12: CascadeEvent 연동
    """
    if self._full_stop_active:
        return

    previous_limit = self._current_limit
    self._full_stop_active = True
    self._current_limit = 0

    # 리뷰 12: 감사 로깅 (CascadeEvent 포함)
    from selfhealing.services.throttle.audit import record_throttle_full_stop_activated
    record_throttle_full_stop_activated(
        previous_limit=previous_limit,
        reason=reason,
        # 추가 컨텍스트
        config_snapshot=self.get_config_snapshot(),  # 리뷰 15
    )

    # 기존 EventBus 발행 유지
    self._event_bus.publish("THROTTLE_FULL_STOP_ACTIVATED", {
        "reason": reason,
        "previous_limit": previous_limit,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


def deactivate_full_stop(self) -> None:
    """
    Full Stop 비활성화.

    리뷰 12: CascadeEvent 연동
    """
    if not self._full_stop_active:
        return

    self._full_stop_active = False
    self.start_recovery_dampening()

    # 리뷰 12: 감사 로깅 (CascadeEvent 포함)
    from selfhealing.services.throttle.audit import record_throttle_full_stop_deactivated
    record_throttle_full_stop_deactivated(
        new_limit=self._current_limit,
    )
```

**CascadeEvent Effects 확장**:

```python
# _build_throttle_effects 함수에 이미 구현됨 (섹션 4.4 참조)

# Full Stop 효과
if action == AUDIT_THROTTLE_FULL_STOP_ACTIVATED:
    effects.append({
        "action_type": "FULL_STOP_ACTIVATED",
        "success": True,
        "details": {
            "reason": audit_data.get("full_stop_reason"),
            "all_requests_blocked": True,
        },
    })
```

---

### 8.14 [리뷰 13] Meta-Watchdog Audit Probe

**문제**: 감사 시스템 자체의 건강 상태 모니터링 부재

**코드 근거**: `HealthProbeManager._create_default_probes()` - [health_probe.py](../../packages/selfhealing-python/src/selfhealing/meta/health_probe.py) Line 402-409

```python
def _create_default_probes(self) -> list[HealthProbe]:
    """기본 프로브 목록 생성."""
    return [
        CircuitBreakerProbe(),
        DLQProbe(),
        RecoveryPipelineProbe(),
        RedisProbe(),
        # AuditSystemProbe 없음!
    ]
```

**구현 - AuditSystemProbe 추가**:

```python
# 신규: meta/audit_probe.py

"""
Audit System Health Probe.

리뷰 13: Meta-Watchdog 감사 시스템 프로브
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from selfhealing.meta.health_probe import HealthProbe, HealthStatus, ProbeResult


class AuditSystemProbe(HealthProbe):
    """
    감사 시스템 건강 프로브.

    확인 항목:
    1. WAL 쓰기 가능 여부
    2. WAL → 중앙 저장소 동기화 지연
    3. DiskPersistentBuffer 상태
    4. 최근 감사 실패율
    """

    @property
    def component_name(self) -> str:
        return "audit_system"

    def probe(self) -> ProbeResult:
        start = time.time()
        details: dict = {}

        try:
            # 1. WAL 상태 확인
            wal_status = self._check_wal()
            details["wal"] = wal_status

            # 2. DiskPersistentBuffer 상태
            buffer_status = self._check_disk_buffer()
            details["disk_buffer"] = buffer_status

            # 3. SyncWorker 지연 확인
            sync_status = self._check_sync_worker()
            details["sync_worker"] = sync_status

            # 상태 결정
            status = self._determine_status(wal_status, buffer_status, sync_status)

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details=details,
            )

        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )

    def _check_wal(self) -> dict:
        """WAL 상태 확인."""
        try:
            from selfhealing.audit.wal import get_wal

            wal = get_wal()
            if wal is None:
                return {"available": False, "reason": "WAL not initialized"}

            stats = wal.get_stats()
            return {
                "available": True,
                "total_entries": stats.get("total_entries", 0),
                "last_sequence": stats.get("last_sequence", 0),
                "disk_usage_bytes": stats.get("disk_usage_bytes", 0),
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _check_disk_buffer(self) -> dict:
        """DiskPersistentBuffer 상태 확인."""
        try:
            from selfhealing.audit.persistence.disk_buffer import DiskPersistentBuffer

            buffer = DiskPersistentBuffer.get_instance()
            stats = buffer.get_stats()
            return {
                "available": True,
                "entry_count": stats.get("entry_count", 0),
                "state": stats.get("state", "unknown"),
            }
        except ImportError:
            return {"available": False, "reason": "DiskPersistentBuffer not installed"}
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _check_sync_worker(self) -> dict:
        """SyncWorker 상태 확인."""
        try:
            from selfhealing.audit.sync_worker import AuditSyncWorker

            worker = AuditSyncWorker.get_instance()
            stats = worker.get_stats()
            return {
                "running": worker.is_running(),
                "lag_entries": stats.current_lag_entries,
                "total_synced": stats.total_synced,
                "total_failed": stats.total_failed,
                "last_error": stats.last_error,
            }
        except Exception as e:
            return {"available": False, "error": str(e)}

    def _determine_status(
        self,
        wal: dict,
        buffer: dict,
        sync: dict,
    ) -> HealthStatus:
        """종합 상태 결정."""
        # WAL 불가 → UNHEALTHY
        if not wal.get("available"):
            return HealthStatus.UNHEALTHY

        # 동기화 지연 심각 (1000+ entries) → DEGRADED
        if sync.get("lag_entries", 0) > 1000:
            return HealthStatus.DEGRADED

        # 최근 실패율 높음 → DEGRADED
        total = sync.get("total_synced", 0) + sync.get("total_failed", 0)
        if total > 0:
            fail_rate = sync.get("total_failed", 0) / total
            if fail_rate > 0.1:  # 10% 이상 실패
                return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY


# HealthProbeManager 기본 프로브에 추가
def _create_default_probes(self) -> list[HealthProbe]:
    """기본 프로브 목록 생성."""
    from selfhealing.meta.audit_probe import AuditSystemProbe

    return [
        CircuitBreakerProbe(),
        DLQProbe(),
        RecoveryPipelineProbe(),
        RedisProbe(),
        AuditSystemProbe(),  # 리뷰 13: 추가
    ]
```

---

### 8.15 [리뷰 14] Immutable Audit Log 가이드

**문제**: 법적 효력을 위한 변경 불가 로그 저장 방법 가이드 부재

**코드 근거**: `WORMAdapter` 존재 - [worm_adapters.py](../../packages/selfhealing-python/src/selfhealing/adapters/audit/worm_adapters.py)

```python
class WORMAdapter(AuditLogAdapter):
    """
    WORM 저장소 어댑터 기본 클래스.

    특징:
    - 한번 기록된 데이터는 수정/삭제 불가
    - 법적 효력을 위한 무결성 보장
    """
```

**가이드 - 구현 옵션**:

```python
# 옵션 1: AWS S3 Object Lock (권장 - 클라우드 환경)
from selfhealing.adapters.audit.worm_adapters import S3ObjectLockAdapter

adapter = S3ObjectLockAdapter(
    bucket="audit-logs",
    region="ap-northeast-2",
    object_lock_mode="COMPLIANCE",  # 삭제 불가
    retention_days=2555,  # 7년 (PCI-DSS)
)

# 옵션 2: Loki with Retention (간편 - 온프레미스)
from selfhealing.adapters.audit.worm_adapters import LokiAdapter

adapter = LokiAdapter(
    endpoint="http://loki:3100",
    labels={"job": "selfhealing-audit-immutable"},
)
# Loki retention 설정으로 보관 기간 강제

# 옵션 3: 로컬 파일 + append-only 권한 (최소 설정)
# /var/log/audit 디렉토리에 append-only 속성 설정
# $ chattr +a /var/log/audit/*.jsonl
```

**운영 가이드**:

```yaml
# docker-compose.yml - S3 Object Lock 예시
services:
  selfhealing:
    environment:
      SELFHEALING_WORM_ADAPTER: "s3"
      SELFHEALING_S3_BUCKET: "audit-logs-worm"
      SELFHEALING_S3_REGION: "ap-northeast-2"
      SELFHEALING_S3_OBJECT_LOCK_MODE: "COMPLIANCE"
      SELFHEALING_S3_RETENTION_DAYS: "2555"
```

**선택 이유**:
- S3 Object Lock `COMPLIANCE` 모드는 AWS 루트 계정도 삭제 불가
- 온프레미스는 Loki + 파일시스템 append-only 조합 권장

---

### 8.16 [리뷰 15] Config Snapshot Recording

**문제**: 이벤트 발생 시점의 설정 상태 기록 부재로 분석 어려움

**코드 근거**: `AdaptiveThrottle.get_stats()` 등 존재

**구현 - 설정 스냅샷 포함**:

```python
# adaptive.py

def get_config_snapshot(self) -> dict[str, Any]:
    """
    현재 Throttle 설정 스냅샷 반환.

    리뷰 15: 감사 이벤트에 설정 상태 포함
    """
    return {
        "base_limit": self._base_limit,
        "current_limit": self._current_limit,
        "min_limit": self.config.min_limit,
        "max_limit": self.config.max_limit,
        "sla_warning_ms": self.config.sla_warning_ms,
        "sla_critical_ms": self.config.sla_critical_ms,
        "emergency_level": self._current_emergency_level,
        "full_stop_active": self._full_stop_active,
        "recovery_dampening_active": self._recovery_dampening_active,
        "cb_states": self._get_cb_states_snapshot(),
    }


def _get_cb_states_snapshot(self) -> dict[str, str]:
    """연결된 CB 상태 스냅샷."""
    try:
        states = {}
        for service_name in self._monitored_services:
            cb = self._cb_registry.get(service_name)
            if cb:
                states[service_name] = cb.state.name
        return states
    except Exception:
        return {}


# throttle/audit.py 수정

def record_throttle_audit(
    action: str,
    # ... 기존 파라미터 ...

    # 리뷰 15: 설정 스냅샷
    config_snapshot: dict[str, Any] | None = None,

) -> None:
    audit_data = {
        "action": action,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # 리뷰 15: 중요 이벤트에만 설정 스냅샷 포함 (데이터 크기 고려)
    if config_snapshot and action in CASCADE_EVENT_ACTIONS:
        audit_data["config_snapshot"] = config_snapshot
```

**선택적 적용 - CASCADE_EVENT만**:

```python
# 일반 이벤트: 스냅샷 생략 (I/O 효율)
# CASCADE_EVENT: 스냅샷 포함 (분석용)

def record_throttle_full_stop_activated(
    previous_limit: int,
    reason: str,
    config_snapshot: dict[str, Any] | None = None,  # 옵션
) -> None:
    record_throttle_audit(
        action=AUDIT_THROTTLE_FULL_STOP_ACTIVATED,
        old_limit=previous_limit,
        new_limit=0,
        full_stop_reason=reason,
        config_snapshot=config_snapshot,  # CASCADE_EVENT이므로 기록
    )
```

---

## 9. 확장 아키텍처

### 9.1 개선된 감사 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        확장된 감사 로깅 아키텍처                                  │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐     ┌────────────────────┐                               │
│  │ AdaptiveThrottle  │     │  GradientCalculator │                              │
│  │                   │     │                    │                               │
│  │  Limit 변경       │◄────│  get_snapshot()    │  [리뷰 1]                     │
│  │  Emergency 연동   │     │  smoothed_rtt      │                               │
│  │  Full Stop        │     │  gradient          │                               │
│  └───────────────────┘     └────────────────────┘                               │
│            │                                                                    │
│            ▼                                                                    │
│  ┌───────────────────────────────────────────────────────────────────────────┐  │
│  │                         throttle/audit.py (확장)                          │  │
│  │                                                                           │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐      │  │
│  │  │ Correlation │  │  Severity   │  │  Sampling   │  │ Config Snap │      │  │
│  │  │  ID 체계    │  │   레벨      │  │    Rate     │  │    shot     │      │  │
│  │  │  [리뷰 2]   │  │  [리뷰 11]  │  │  [리뷰 7]   │  │  [리뷰 15]  │      │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘      │  │
│  │                                                                           │  │
│  │  record_throttle_audit()                                                  │  │
│  │       │                                                                   │  │
│  │       ├──► _inject_cluster_identity() [리뷰 3]                            │  │
│  │       │                                                                   │  │
│  │       └──► Async Queue (Non-blocking) [리뷰 8]                            │  │
│  └────────────────────────────┬──────────────────────────────────────────────┘  │
│                               │                                                  │
│            ┌──────────────────┼──────────────────┐                               │
│            │                  │                  │                               │
│            ▼                  ▼                  ▼                               │
│  ┌──────────────┐   ┌──────────────┐   ┌────────────────────┐                   │
│  │     WAL      │   │ CascadeEvent │   │ DiskPersistent     │                   │
│  │ (Primary)    │   │   Auditor    │   │   Buffer           │                   │
│  │              │   │              │   │ [리뷰 4] (Fallback)│                   │
│  │ Retention    │   │ Full Stop    │   │                    │                   │
│  │ [리뷰 5]     │   │ [리뷰 12]    │   │ LMDB 기반 영속     │                   │
│  └──────┬───────┘   └──────────────┘   └────────────────────┘                   │
│         │                                                                        │
│         │ Background Sync [리뷰 8]                                               │
│         ▼                                                                        │
│  ┌──────────────────────────────────────────────────────────────────┐           │
│  │                         중앙 저장소                               │           │
│  │                                                                   │           │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │           │
│  │  │   Loki   │  │   ELK    │  │    DB    │  │ S3 Object Lock   │  │           │
│  │  │ [리뷰10] │  │ [리뷰10] │  │          │  │ (WORM) [리뷰14]  │  │           │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │           │
│  └──────────────────────────────────────────────────────────────────┘           │
│                                                                                  │
│  ┌──────────────────────────────────────────────────────────────────┐           │
│  │                     Meta-Watchdog [리뷰 13]                       │           │
│  │                                                                   │           │
│  │  HealthProbeManager                                               │           │
│  │    ├── CircuitBreakerProbe                                        │           │
│  │    ├── DLQProbe                                                   │           │
│  │    ├── RecoveryPipelineProbe                                      │           │
│  │    ├── RedisProbe                                                 │           │
│  │    └── AuditSystemProbe  ◄── [리뷰 13] 추가                       │           │
│  │                                                                   │           │
│  └──────────────────────────────────────────────────────────────────┘           │
│                                                                                  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 10. 확장 테스트 코드

### 10.1 리뷰항목 통합 테스트

```python
"""
190번 문서 - 15가지 리뷰항목 통합 테스트.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestReview1PayloadExtension:
    """리뷰 1: 페이로드 확장 테스트."""

    def test_smoothed_rtt_and_gradient_included(self):
        """smoothed_rtt, gradient가 감사 데이터에 포함되는지 확인."""
        from selfhealing.services.throttle.audit import record_throttle_limit_adjusted

        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            record_throttle_limit_adjusted(
                old_limit=100,
                new_limit=80,
                reason="gradient_increase",
                trigger_source="gradient",
                smoothed_rtt_ms=150.5,
                gradient=0.15,
                long_rtt_ms=50.0,
            )

            call_args = mock_wal.call_args
            details = call_args.kwargs["details"]

            assert details["smoothed_rtt_ms"] == 150.5
            assert details["gradient"] == 0.15
            assert details["long_rtt_ms"] == 50.0


class TestReview2CorrelationId:
    """리뷰 2: Correlation ID 테스트."""

    def test_event_id_generated(self):
        """event_id가 자동 생성되는지 확인."""
        from selfhealing.services.throttle.audit import record_throttle_audit

        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            record_throttle_audit(action="throttle_limit_adjusted")

            details = mock_wal.call_args.kwargs["details"]

            assert "event_id" in details
            assert details["event_id"].startswith("evt-")


class TestReview3ClusterIdentity:
    """리뷰 3: Region 정보 자동 주입 테스트."""

    def test_cluster_info_injected(self):
        """클러스터 정보가 자동 주입되는지 확인."""
        from selfhealing.services.throttle.audit import _inject_cluster_identity

        with patch("selfhealing.core.cluster_identity.get_cluster_identity") as mock_identity:
            mock_identity.return_value = MagicMock(
                cluster_id="seoul-prod-01",
                region="seoul",
                environment="production",
                pod_id="pod-123",
            )

            audit_data = {"action": "test"}
            result = _inject_cluster_identity(audit_data)

            assert result["cluster"]["region"] == "seoul"
            assert result["cluster"]["cluster_id"] == "seoul-prod-01"


class TestReview4DiskPersistentBuffer:
    """리뷰 4: DiskPersistentBuffer 통합 테스트."""

    def test_disk_buffer_used_first(self):
        """DiskPersistentBuffer가 우선 사용되는지 확인."""
        from selfhealing.services.audit.base import _save_to_fallback_buffer

        with patch("selfhealing.audit.persistence.disk_buffer.DiskPersistentBuffer") as mock_buffer:
            mock_instance = MagicMock()
            mock_buffer.get_instance.return_value = mock_instance

            _save_to_fallback_buffer({"test": "data"})

            mock_instance.put.assert_called_once()


class TestReview5RetentionPolicy:
    """리뷰 5: Time-based retention policy 테스트."""

    def test_old_files_cleaned(self, tmp_path):
        """보관 기간 초과 파일이 정리되는지 확인."""
        from selfhealing.audit.retention_cleaner import WALRetentionCleaner
        import time

        # 오래된 파일 생성
        old_file = tmp_path / "old.wal"
        old_file.touch()
        # 파일 시간을 100일 전으로 설정
        old_time = time.time() - (100 * 24 * 3600)
        import os
        os.utime(old_file, (old_time, old_time))

        # Synced 마커 생성
        (tmp_path / "old.synced").touch()

        cleaner = WALRetentionCleaner(wal_dir=tmp_path, retention_days=90)
        deleted = cleaner.cleanup()

        assert deleted == 1
        assert not old_file.exists()


class TestReview6TransactionPolicy:
    """리뷰 6: is_critical 옵션 테스트."""

    def test_critical_event_synced_immediately(self):
        """is_critical=True 시 즉시 동기화되는지 확인."""
        from selfhealing.services.throttle.audit import record_throttle_audit

        with patch("selfhealing.services.throttle.audit._sync_to_central_immediately") as mock_sync:
            record_throttle_audit(
                action="throttle_full_stop_activated",
                is_critical=True,
            )

            mock_sync.assert_called_once()


class TestReview7Sampling:
    """리뷰 7: 샘플링 테스트."""

    def test_cascade_event_not_sampled(self):
        """CASCADE_EVENT는 샘플링되지 않는지 확인."""
        from selfhealing.services.throttle.audit import should_sample

        # CASCADE_EVENT는 항상 True
        assert should_sample("throttle_full_stop_activated") == True
        assert should_sample("throttle_emergency_sync") == True


class TestReview11Severity:
    """리뷰 11: Severity 필드 테스트."""

    def test_severity_assigned_correctly(self):
        """이벤트별 Severity가 올바르게 할당되는지 확인."""
        from selfhealing.services.throttle.audit import _get_severity, AuditSeverity

        assert _get_severity("throttle_full_stop_activated") == "critical"
        assert _get_severity("throttle_sla_warning") == "warning"
        assert _get_severity("throttle_limit_adjusted") == "debug"


class TestReview13AuditProbe:
    """리뷰 13: AuditSystemProbe 테스트."""

    def test_audit_probe_returns_healthy(self):
        """정상 상태에서 HEALTHY 반환하는지 확인."""
        from selfhealing.meta.audit_probe import AuditSystemProbe
        from selfhealing.meta.health_probe import HealthStatus

        probe = AuditSystemProbe()

        with patch.object(probe, "_check_wal", return_value={"available": True}):
            with patch.object(probe, "_check_disk_buffer", return_value={"available": True}):
                with patch.object(probe, "_check_sync_worker", return_value={"lag_entries": 0}):
                    result = probe.probe()

                    assert result.status == HealthStatus.HEALTHY


class TestReview15ConfigSnapshot:
    """Config Snapshot 테스트."""

    def test_config_snapshot_included_in_cascade(self):
        """CASCADE_EVENT에 config_snapshot이 포함되는지 확인."""
        from selfhealing.services.throttle.audit import record_throttle_full_stop_activated

        with patch("selfhealing.services.audit.base._write_to_wal") as mock_wal:
            record_throttle_full_stop_activated(
                previous_limit=100,
                reason="test",
                config_snapshot={"base_limit": 100, "min_limit": 10},
            )

            details = mock_wal.call_args.kwargs["details"]

            assert "config_snapshot" in details
            assert details["config_snapshot"]["base_limit"] == 100
```

---

## 11. 참조

- [throttle/audit.py 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/audit.py)
- [audit/base.py 소스](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [DiskPersistentBuffer 소스](../../packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py)
- [ClusterIdentity 소스](../../packages/selfhealing-python/src/selfhealing/core/cluster_identity.py)
- [HealthProbeManager 소스](../../packages/selfhealing-python/src/selfhealing/meta/health_probe.py)
- [WORMAdapter 소스](../../packages/selfhealing-python/src/selfhealing/adapters/audit/worm_adapters.py)
- [AuditSyncWorker 소스](../../packages/selfhealing-python/src/selfhealing/audit/sync_worker.py)
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md)
- [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md)
- [176_DISK_PERSISTENT_BUFFER.md](176_DISK_PERSISTENT_BUFFER.md)
- [70_MULTI_CLUSTER_ARCHITECTURE.md](70_MULTI_CLUSTER_ARCHITECTURE.md)
- [177_META_WATCHDOG.md](177_META_WATCHDOG.md)

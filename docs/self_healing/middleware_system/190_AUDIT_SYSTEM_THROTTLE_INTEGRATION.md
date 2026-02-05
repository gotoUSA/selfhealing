# 190. Audit System - AdaptiveThrottle 감사 로깅 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/throttle/audit.py`, `selfhealing/services/audit/base.py`, `selfhealing/audit/cascade_auditor.py`

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

## 8. 참조

- [throttle/audit.py 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/audit.py)
- [audit/base.py 소스](../../packages/selfhealing-python/src/selfhealing/services/audit/base.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md)
- [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md)

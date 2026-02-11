# 219. 해시체인 무결성 3계층: 상시 감시 · 복구 게이트 · 머클 스팟체크

> **문서 번호**: 219
> **분류**: Integrity - Audit / Self-Healing
> **선행 문서**: 43 (DISTRIBUTED_HASH_CHAIN_ENHANCED), 76 (CASCADE_EVENT_AUDIT), 218
> **작성일**: 2026-02-11
> **심각도**: HIGH (데이터 무결성)
> **구현 상태**: ✅ 구현 완료

---

## 0. 배경 및 목적

외부 리뷰에서 해시체인 무결성 검증에 대해 3가지 방안이 제시되었다:

1. **Continuous Background Audit** — 백그라운드 상시 감시
2. **Post-Recovery Self-Check** — 장애 복구 직후 자가 진단
3. **Merkle Tree Spot Check** — 대규모 데이터에서 효율적 블록 단위 검증

이 문서는 각 방안에 대해 **기존 코드에 이미 존재하는 부분**과 **새로 구현해야 할 부분**을 명확히 구분하고, 구체적 구현 설계를 제시한다.

---

## 1. 현재 시스템 무결성 인프라 현황

### 1.1 이미 존재하는 컴포넌트 (코드 근거)

| 컴포넌트 | 파일 | 역할 | 상태 |
|----------|------|------|------|
| `HashChainManager` | `audit/integrity/local_manager.py` L22 | 로컬 해시체인 상태 관리, `add_integrity()` | ✅ |
| `RedisHashChainManager` | `audit/integrity/redis_manager.py` | Redis 기반 분산 해시체인 | ✅ |
| `HashChainVerifier` | `audit/integrity/verifier.py` L22 | 체인 검증, 변조 탐지 (`verify_chain()`, `find_tampering()`) | ✅ |
| `HashChainReconciler` | `audit/integrity/reconciler.py` L21 | degraded 엔트리 메인 체인 병합 | ✅ |
| `StartupHashChainSync` | `audit/integrity/sync.py` L19 | 부팅 시 Redis↔File 동기화 | ✅ |
| `DailyHashAnchor` | `audit/integrity/anchor.py` L26 | 일일 체크포인트, 앵커 이후만 검증 | ✅ |
| `IntegrityHealthScore` | `audit/integrity/health_score.py` L95 | Prometheus Gauge 연동 건강 점수 | ✅ |
| `MerkleTree` | `audit/signed_manifest.py` L55 | 머클 트리, Proof 생성/검증 | ✅ |
| `SignedManifest` | `audit/signed_manifest.py` L256 | 법적 무결성 증명 (머클 루트 + RFC 3161) | ✅ |
| `ContinuousAuditRecorder` | `audit/continuous_audit.py` L50 | 해시체인 기반 감사 기록기 | ✅ |
| `_write_to_wal()` | `services/audit/base.py` L193 | WAL 기반 누락 0 보장 기록 | ✅ |
| `verify_cascade_chain_integrity()` | `tasks/cascade_cleanup_tasks.py` L334 | Cascade 체인 무결성 검증 태스크 | ✅ |
| `AuditIntegritySettings` | `settings/audit_integrity.py` L29 | `integrity_check_interval` 등 설정 | ✅ |
| EventBus CB CLOSED 핸들러 3개 | `services/event_bus/bus.py` L1660-1722 | Replay, Postmortem, Throttle 복구 | ✅ |

### 1.2 누락된 부분 (새로 구현 필요)

| 누락 사항 | 설명 |
|-----------|------|
| **주기적 해시체인 순회 Worker** | Celery Beat으로 1~5분 주기 해시체인 검증 태스크 없음 |
| **CB Closed 시 무결성 게이트** | 리플레이 전 WAL↔해시체인 대조 핸들러 없음 |
| **무결성 실패 → 리플레이 차단** | 무결성 깨진 경우 리플레이 중단 로직 없음 |
| **블록 단위 머클 스팟체크** | `MerkleTree`는 존재하나 해시체인 블록 검증 용도로 미연결 |
| **통합 Beat Schedule** | 무결성 전용 beat schedule 함수 없음 |

---

## 2. 구현 1: BackgroundIntegrityVerifier (상시 감시)

### 2.1 네이밍 결정

| 후보 | 채택 여부 | 이유 |
|------|----------|------|
| `ContinuousBackgroundAudit` | ❌ | `ContinuousAuditRecorder`(`audit/continuous_audit.py` L50)와 "Continuous" 접두어 충돌 |
| `PeriodicHashChainAuditor` | ❌ | "Auditor"가 `CascadeEventAuditor`(`audit/cascade_auditor.py`)와 혼동 가능 |
| **`BackgroundIntegrityVerifier`** | ✅ | 기존 `HashChainVerifier`와 "Verifier" 계열 일관성, "Background"로 상시 감시 의미 명확 |

기존 네이밍 충돌 확인: `BackgroundIntegrityVerifier`는 시스템에 존재하지 않음 ✅

### 2.2 설계

```
파일: selfhealing/tasks/integrity_tasks.py (신규)
```

```python
"""
Hash Chain Integrity Verification Tasks.

Celery Beat으로 주기적으로 해시체인 무결성을 검증합니다.

Tasks:
- verify_hash_chain_integrity: 백그라운드 해시체인 검증
- verify_wal_integrity: WAL↔해시체인 대조

Reference:
    docs/self_healing/middleware_system/219_HASH_CHAIN_INTEGRITY_TRIAD.md
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def verify_hash_chain_integrity(
    namespace: str = "global",
    use_merkle_spot_check: bool = True,
    block_size: int = 1000,
) -> dict[str, Any]:
    """
    백그라운드 해시체인 무결성 검증.

    마지막 검증 지점(DailyHashAnchor)부터 최신 엔트리까지 검증.
    데이터가 block_size * 10 이상이면 MerkleSpotChecker로 전환.

    Args:
        namespace: 검증 대상 네임스페이스
        use_merkle_spot_check: Merkle 스팟체크 활성화
        block_size: 머클 블록 단위 크기

    Returns:
        검증 결과 딕셔너리
    """
    start = time.time()

    try:
        from selfhealing.audit.integrity import (
            HashChainVerifier,
            IntegrityHealthScore,
            get_integrity_health_score,
        )

        verifier = HashChainVerifier()
        health = get_integrity_health_score()

        # Step 1: DailyHashAnchor 기반 검증 범위 결정
        entries = _get_entries_since_last_anchor(namespace)
        entry_count = len(entries)

        # Step 2: 데이터 규모에 따라 검증 전략 분기
        if use_merkle_spot_check and entry_count > block_size * 10:
            # 대규모: MerkleSpotChecker 사용 (O(log n))
            result = _merkle_spot_check(entries, block_size)
        else:
            # 소규모: 전체 순회 검증 (O(n))
            is_valid, error_msg = verifier.verify_chain(entries)
            result = {
                "valid": is_valid,
                "strategy": "full_chain",
                "checked": entry_count,
                "errors": [error_msg] if error_msg else [],
            }

        # Step 3: Prometheus 지표 업데이트
        duration_ms = (time.time() - start) * 1000
        if result["valid"]:
            health.record_recovery(
                event_type="background_verify_ok",
                sequences_affected=entry_count,
                recovery_time_ms=duration_ms,
            )
        else:
            health.record_chain_break()
            _alert_integrity_violation(namespace, result)

        result["duration_ms"] = duration_ms
        result["namespace"] = namespace
        return result

    except Exception as e:
        logger.error(f"[BackgroundIntegrityVerifier] Failed: {e}", exc_info=True)
        return {"valid": False, "error": str(e), "namespace": namespace}


def _get_entries_since_last_anchor(namespace: str) -> list[dict]:
    """DailyHashAnchor 이후 엔트리만 로드."""
    # DailyHashAnchor.verify_from_anchor() 패턴 재사용
    # anchor.py L207-L300 참조
    try:
        from selfhealing.audit.cascade_auditor import get_cascade_event_auditor

        auditor = get_cascade_event_auditor()
        events = auditor.get_recent_events(namespace, limit=100000)
        return [e.to_dict() if hasattr(e, 'to_dict') else e for e in events]
    except Exception as e:
        logger.warning(f"[BackgroundIntegrityVerifier] Entry load failed: {e}")
        return []


def _merkle_spot_check(
    entries: list[dict],
    block_size: int,
) -> dict[str, Any]:
    """
    MerkleSpotChecker를 사용한 블록 단위 검증.

    구현 3 (MerkleSpotChecker)에서 상세 설명.
    """
    from selfhealing.audit.integrity.merkle_spot_checker import MerkleSpotChecker

    checker = MerkleSpotChecker(block_size=block_size)
    return checker.spot_check(entries)


def _alert_integrity_violation(namespace: str, result: dict) -> None:
    """무결성 위반 시 관리자 알림."""
    try:
        from selfhealing.services.event_bus.bus import EventType, get_event_bus

        bus = get_event_bus()
        # 기존 EventBus 인프라 활용
        logger.critical(
            f"[INTEGRITY_VIOLATION] namespace={namespace}, "
            f"errors={result.get('errors', [])}"
        )
    except Exception as e:
        logger.error(f"[BackgroundIntegrityVerifier] Alert failed: {e}")


def get_integrity_beat_schedule() -> dict[str, Any]:
    """
    무결성 검증 Beat Schedule.

    기존 패턴 참조:
    - tasks/cleanup_tasks.py:get_cleanup_beat_schedule()
    - tasks/intelligence_tasks.py:get_intelligence_beat_schedule()

    Returns:
        dict: Celery Beat Schedule 설정
    """
    try:
        from celery.schedules import crontab

        return {
            # 5분마다 - 해시체인 무결성 검증
            "verify-hash-chain-integrity": {
                "task": "selfhealing.verify_hash_chain_integrity",
                "schedule": crontab(minute="*/5"),
                "options": {"queue": "integrity"},
                "kwargs": {
                    "namespace": "global",
                    "use_merkle_spot_check": True,
                },
            },
            # 매일 01:00 - 전체 체인 풀 검증 (Merkle 비활성화)
            "verify-hash-chain-full": {
                "task": "selfhealing.verify_hash_chain_integrity",
                "schedule": crontab(hour=1, minute=0),
                "options": {"queue": "integrity"},
                "kwargs": {
                    "namespace": "global",
                    "use_merkle_spot_check": False,
                },
            },
        }
    except ImportError:
        return {}
```

### 2.3 주기 결정: 1분 vs 5분

| 항목 | 1분 | 5분 |
|------|-----|-----|
| 탐지 속도 | 빠름 | 최대 5분 지연 |
| Redis/DB 부하 | 높음 | 중간 |
| 기존 시스템 참고 | — | `verify-reconciliation-accuracy`: 5분 (`intelligence_tasks.py` L867) |
| `AuditIntegritySettings.integrity_check_interval` | `ge=300` (최소 5분) | 기본값 범위 내 |

**선택: 5분 주기**

이유:
1. `AuditIntegritySettings.integrity_check_interval`의 `ge=300` 제약조건(`settings/audit_integrity.py` L97)이 최소 5분을 강제함. 이를 1분으로 바꾸려면 설정 스키마를 변경해야 하고, 기존 배포 환경의 하위 호환성이 깨짐.
2. 기존 `verify-reconciliation-accuracy` 태스크도 5분 주기(`intelligence_tasks.py` L867)이므로 시스템 내 리얼타임 분석 큐의 표준 주기와 일치.
3. `DailyHashAnchor`가 앵커 이후만 검증하므로 5분 동안 쌓이는 엔트리 수는 제한적.
4. 빅테크 규모에서 1분 주기 풀 스캔은 비현실적 — 이 문제는 구현 3의 MerkleSpotChecker가 해결.

---

## 3. 구현 2: PostRecoveryIntegrityGate (복구 직후 자가 진단)

### 3.1 네이밍 결정

| 후보 | 채택 여부 | 이유 |
|------|----------|------|
| `PostRecoverySelfCheck` | ❌ | "SelfCheck"가 너무 범용적. 기존 `self_audit.py`와 혼동 가능 |
| `RecoveryIntegrityCheck` | ❌ | 리플레이 차단 의미 없음 |
| **`PostRecoveryIntegrityGate`** | ✅ | "Gate" = 리플레이 전 차단/통과 판정. 기존 `RegionalGate`(`regional_gate.py`)와 "Gate" 패턴 일관성 |

기존 네이밍 충돌 확인: `PostRecoveryIntegrityGate`는 시스템에 존재하지 않음 ✅

### 3.2 핵심 동작 원리

기존 `CIRCUIT_BREAKER_CLOSED` 이벤트 핸들러 등록 현황 (`bus.py` L1660-1722):

```
EventType.CIRCUIT_BREAKER_CLOSED
  ├─ _on_circuit_breaker_closed          (NORMAL)   → Track 1 Replay
  ├─ _on_circuit_breaker_closed_postmortem (LOW)     → 자동 Postmortem
  └─ _on_circuit_breaker_closed_throttle   (NORMAL)  → Throttle 복원
```

**새 핸들러를 `CRITICAL` 우선순위로 추가:**

```
EventType.CIRCUIT_BREAKER_CLOSED
  ├─ _on_circuit_breaker_closed_integrity_gate (CRITICAL)  → ★ 신규: WAL 무결성 검증
  ├─ _on_circuit_breaker_closed                (NORMAL)    → Track 1 Replay
  ├─ _on_circuit_breaker_closed_throttle       (NORMAL)    → Throttle 복원
  └─ _on_circuit_breaker_closed_postmortem     (LOW)       → 자동 Postmortem
```

`CRITICAL`이 `NORMAL`보다 먼저 실행되므로, 무결성 검증 실패 시 이벤트 데이터에 `integrity_failed=True` 플래그를 설정하여 후속 핸들러(Replay)가 이를 확인하고 중단할 수 있다.

### 3.3 설계

```
파일: selfhealing/services/event_bus/integrity_gate.py (신규)
```

```python
"""
Post-Recovery Integrity Gate.

CB CLOSED 시 리플레이 전에 WAL↔해시체인 무결성을 검증합니다.
실패 시 리플레이를 차단하고 관리자에게 경고합니다.

EventBus 핸들러 우선순위:
- CRITICAL: 이 게이트 (리플레이보다 먼저 실행)
- NORMAL: _on_circuit_breaker_closed (리플레이)
- LOW: _on_circuit_breaker_closed_postmortem

Reference:
    docs/self_healing/middleware_system/219_HASH_CHAIN_INTEGRITY_TRIAD.md
    services/event_bus/bus.py L718-800 (기존 CB CLOSED 핸들러 패턴)
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# 이벤트 데이터 플래그 키
INTEGRITY_GATE_KEY = "integrity_gate_result"
INTEGRITY_FAILED_KEY = "integrity_failed"


def on_circuit_breaker_closed_integrity_gate(event) -> None:
    """
    CB 복구 시 무결성 게이트.

    CRITICAL 우선순위로 등록되어 Replay 핸들러보다 먼저 실행됩니다.

    동작:
    1. 서킷이 Open이었던 시간대의 WAL 엔트리 수집
    2. 해당 구간의 해시체인 무결성 검증
    3. 실패 시 event.data에 integrity_failed=True 설정
    4. 후속 _on_circuit_breaker_closed가 이 플래그를 확인

    코드 근거:
    - bus.py L718: _on_circuit_breaker_closed 패턴
    - verifier.py L34: HashChainVerifier.verify_chain()
    - base.py L193: _write_to_wal()의 WAL 시퀀스 체계
    """
    service_name = event.data.get("service_name", "unknown")
    start = time.time()

    logger.info(
        f"[IntegrityGate] Checking WAL integrity for {service_name} "
        f"before replay"
    )

    try:
        result = _verify_recovery_window_integrity(service_name, event)

        duration_ms = (time.time() - start) * 1000

        # event.data에 결과 저장 (후속 핸들러가 참조)
        event.data[INTEGRITY_GATE_KEY] = {
            "valid": result["valid"],
            "checked": result.get("checked", 0),
            "duration_ms": duration_ms,
            "strategy": result.get("strategy", "full_chain"),
        }

        if not result["valid"]:
            event.data[INTEGRITY_FAILED_KEY] = True
            logger.critical(
                f"[IntegrityGate] INTEGRITY VIOLATION for {service_name}! "
                f"Replay will be BLOCKED. errors={result.get('errors', [])}"
            )
            _send_integrity_violation_alert(service_name, result, duration_ms)
        else:
            event.data[INTEGRITY_FAILED_KEY] = False
            logger.info(
                f"[IntegrityGate] Integrity OK for {service_name} "
                f"({result.get('checked', 0)} entries, {duration_ms:.1f}ms)"
            )

        # IntegrityHealthScore 업데이트
        _update_health_score(result, duration_ms)

    except Exception as e:
        # 게이트 자체 실패 시 Fail-Open (리플레이 허용)
        # ContinuousAuditRecorder의 Fail-Open 정책과 일관
        # (continuous_audit.py L72: "Fail-Open Design Policy")
        logger.warning(
            f"[IntegrityGate] Gate check failed for {service_name}: {e}. "
            f"Proceeding with Fail-Open policy."
        )
        event.data[INTEGRITY_FAILED_KEY] = False
        event.data[INTEGRITY_GATE_KEY] = {
            "valid": None,
            "error": str(e),
            "policy": "fail_open",
        }


def _verify_recovery_window_integrity(
    service_name: str,
    event,
) -> dict[str, Any]:
    """
    서킷 Open 기간 동안 쌓인 WAL 데이터의 해시체인 검증.

    코드 근거:
    - anchor.py L207: verify_from_anchor() - 앵커 기반 부분 검증
    - verifier.py L34: verify_chain() - 순차 검증
    - cascade_auditor.py L342: verify_chain_integrity() - 해시 재계산+체인 연결

    Returns:
        {"valid": bool, "checked": int, "errors": list, "strategy": str}
    """
    from selfhealing.audit.integrity import HashChainVerifier

    verifier = HashChainVerifier()

    # WAL에서 미동기화 엔트리 수집
    wal_entries = _get_unsynced_wal_entries(service_name)

    if not wal_entries:
        return {"valid": True, "checked": 0, "errors": [], "strategy": "no_entries"}

    # 해시체인 검증
    is_valid, error_msg = verifier.verify_chain(wal_entries)
    issues = verifier.find_tampering(wal_entries) if not is_valid else []

    return {
        "valid": is_valid,
        "checked": len(wal_entries),
        "errors": [i["message"] for i in issues] if issues else (
            [error_msg] if error_msg else []
        ),
        "strategy": "wal_chain_verify",
    }


def _get_unsynced_wal_entries(service_name: str) -> list[dict]:
    """
    WAL에서 아직 동기화되지 않은 엔트리 조회.

    코드 근거:
    - base.py L193: _write_to_wal()에서 synced=False로 기록
    - base.py L263: wal.write(wal_entry) → WAL 시퀀스 반환
    """
    try:
        from selfhealing.services.audit.base import _get_wal

        wal = _get_wal()
        if wal is None:
            return []

        stats = wal.get_stats()
        # WAL에서 unsynced 엔트리 읽기
        unsynced = wal.read_unprocessed() if hasattr(wal, 'read_unprocessed') else []
        return [e for e in unsynced if isinstance(e, dict)]

    except Exception as e:
        logger.warning(f"[IntegrityGate] WAL read failed: {e}")
        return []


def _send_integrity_violation_alert(
    service_name: str,
    result: dict,
    duration_ms: float,
) -> None:
    """
    무결성 위반 알림 발송.

    기존 알림 인프라 활용:
    - bus.py L676: _on_circuit_breaker_opened_notify 패턴
    - services/audit/cb_audit.py: _write_to_wal() 기반 감사 기록
    """
    try:
        from selfhealing.services.audit.base import _write_to_wal

        _write_to_wal(
            event_type="INTEGRITY_VIOLATION",
            source="PostRecoveryIntegrityGate",
            details={
                "service_name": service_name,
                "checked": result.get("checked", 0),
                "errors": result.get("errors", []),
                "duration_ms": duration_ms,
            },
            success=False,
            error_message="Hash chain integrity violation detected during post-recovery check",
        )
    except Exception as e:
        logger.error(f"[IntegrityGate] Audit write failed: {e}")


def _update_health_score(result: dict, duration_ms: float) -> None:
    """IntegrityHealthScore 업데이트."""
    try:
        from selfhealing.audit.integrity import get_integrity_health_score

        health = get_integrity_health_score()
        if result["valid"]:
            health.record_recovery(
                event_type="post_recovery_gate_ok",
                sequences_affected=result.get("checked", 0),
                recovery_time_ms=duration_ms,
            )
        else:
            health.record_chain_break()
    except Exception as e:
        logger.debug(f"[IntegrityGate] Health score update failed: {e}")
```

### 3.4 기존 Replay 핸들러 수정

`_on_circuit_breaker_closed` (`bus.py` L718)에 게이트 확인 로직 추가:

```python
# bus.py의 _on_circuit_breaker_closed 함수 시작부에 추가
def _on_circuit_breaker_closed(event: SelfHealingEvent):
    service_name = event.data.get("service_name", "unknown")

    # ★ 신규: IntegrityGate 결과 확인
    if event.data.get("integrity_failed", False):
        logger.critical(
            f"[EventHandler] Replay BLOCKED for {service_name}: "
            f"integrity gate failed. "
            f"Details: {event.data.get('integrity_gate_result', {})}"
        )
        return  # 리플레이 중단

    # ... 이하 기존 로직 동일 (bus.py L730-767)
```

### 3.5 EventBus 등록

`bus.py`의 `_register_default_handlers()` 함수에 추가:

```python
# bus.py L1660 부근, CB CLOSED 핸들러 등록 블록에 추가

from selfhealing.services.event_bus.integrity_gate import (
    on_circuit_breaker_closed_integrity_gate,
)

# ★ 무결성 게이트 (CRITICAL: Replay보다 먼저 실행)
bus.subscribe(
    EventType.CIRCUIT_BREAKER_CLOSED,
    on_circuit_breaker_closed_integrity_gate,
    priority=EventPriority.CRITICAL,
)
```

### 3.6 Fail-Open vs Fail-Secure 결정

| 모드 | 동작 | 리스크 |
|------|------|--------|
| Fail-Open | 게이트 자체 오류 시 리플레이 허용 | 오염 데이터 리플레이 가능성 |
| **Fail-Secure** | 게이트 자체 오류 시 리플레이 차단 | 가용성 저하 |

**선택: Fail-Open (기본), Fail-Secure (선택적)**

이유:
1. `ContinuousAuditRecorder`의 기존 정책이 Fail-Open (`continuous_audit.py` L72: "FAIL-OPEN Design Policy").
2. docstring에 명시: "Netflix Zuul: Fail-Open (가용성 우선)", "SOC2: Fail-Open 허용 (실패 기록만 있으면 됨)".
3. 단, PCI-DSS 환경에서는 설정으로 Fail-Secure 전환 가능하도록 설계. 이는 `ContinuousAuditRecorder.__init__`의 `fail_open` 파라미터(`continuous_audit.py` L108)와 동일 패턴.

```python
# 설정 추가 (settings/audit_integrity.py에 필드 추가)
integrity_gate_fail_open: bool = Field(
    default=True,
    description="무결성 게이트 Fail-Open 정책. False면 Fail-Secure (PCI-DSS).",
)
```

---

## 4. 구현 3: MerkleSpotChecker (머클 블록 스팟체크)

### 4.1 빅테크 규모에서의 필요성 재평가

리뷰어의 원래 제안에 대해 이전 분석에서는 "현재 불필요"라고 평가했으나, **수백만~수천만 건 규모를 목표하는 시스템에서는 필수**이다.

**현재 한계 — `DailyHashAnchor`의 O(n) 문제:**

`DailyHashAnchor.verify_from_anchor()` (`anchor.py` L207-L300)은 앵커 이후 엔트리를 **모두 순회**한다:

```python
# anchor.py L256-L300: _verify_chain_from_point()
for entry in entries:         # ← O(n) 순회
    integrity = entry.get("integrity", {})
    seq = integrity.get("sequence", 0)
    if seq != expected_sequence:
        return (False, f"Missing entry: ...")
    # ... 해시 재계산 + 비교
```

`verify_cascade_chain_integrity()` (`cascade_cleanup_tasks.py` L334)도 동일:

```python
# cascade_auditor.py L375-L392
for i, event in enumerate(events):    # ← O(n)
    recalculated_hash = event.calculate_hash()
    if recalculated_hash != event.current_hash:
        errors.append(...)
```

**빅테크 규모 시나리오:**

| 시간 | 엔트리 수 | O(n) 순회 시간 (추정) | Merkle O(log n) |
|------|----------|---------------------|-----------------|
| 1일 | 100만 건 | ~30초 | ~0.3초 (블록 1000개 검증) |
| 1일 | 1000만 건 | ~5분 | ~0.5초 |
| 5분 간격 | ~3,500건 | ~0.1초 | ~0.01초 |
| 5분 간격 | ~35,000건 | ~1초 | ~0.05초 |

5분 주기 검증에서 3,500건은 O(n)으로도 감당 가능하지만, **일일 풀 검증**(01:00 스케줄)에서 1000만 건을 O(n)으로 처리하면 5분이 소요된다. Merkle 방식은 변조된 블록만 드릴다운하므로 정상 상태에서는 O(블록 수) = O(n/block_size)만 소요된다.

**결론: 일일 풀 검증 + 이상 탐지 시 드릴다운용으로 Merkle 스팟체크 필수.**

### 4.2 네이밍 결정

| 후보 | 채택 여부 | 이유 |
|------|----------|------|
| `MerkleTreeSpotCheck` | ❌ | `MerkleTree`(`signed_manifest.py` L55)와 혼동 |
| `BlockwiseMerkleVerifier` | ❌ | "Blockwise"가 블록체인과 혼동 가능 |
| **`MerkleSpotChecker`** | ✅ | 기존 `MerkleTree`(데이터 구조)와 분리, `Checker`로 검증 역할 명확 |

기존 네이밍 충돌 확인: `MerkleSpotChecker`는 시스템에 존재하지 않음 ✅

### 4.3 설계

```
파일: selfhealing/audit/integrity/merkle_spot_checker.py (신규)
```

```python
"""
Merkle Spot Checker.

대규모 해시체인에서 블록 단위 머클 루트 비교를 통해
변조된 블록만 정밀 검증하는 효율적 무결성 검증기.

핵심 원리:
1. 전체 엔트리를 block_size 단위로 분할
2. 각 블록의 머클 루트를 계산하여 저장된 루트와 비교
3. 불일치 블록만 HashChainVerifier로 정밀 검증 (드릴다운)

성능:
- 정상 상태: O(n/block_size) — 블록 루트만 비교
- 변조 감지 시: O(block_size * 변조_블록수) — 변조 블록만 순회
- vs 기존 O(n) 전체 순회: 1000만 건 기준 ~100배 성능 향상

업계 사례:
- Cassandra: Anti-Entropy Repair에서 노드 간 머클 트리 비교
- AWS S3: ETag 기반 블록 체크섬으로 대용량 객체 무결성 검증
- Bitcoin: SPV (Simplified Payment Verification)에서 머클 프루프 사용

기존 코드 재사용:
- MerkleTree: audit/signed_manifest.py L55 (트리 구조, proof, verify)
- HashChainVerifier: audit/integrity/verifier.py L22 (정밀 검증)
- DailyHashAnchor: audit/integrity/anchor.py L26 (앵커 시점)

Reference:
    docs/self_healing/middleware_system/219_HASH_CHAIN_INTEGRITY_TRIAD.md
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# 블록 머클 루트 저장 키 (Redis)
BLOCK_MERKLE_ROOT_KEY = "selfhealing:{namespace}:audit:merkle_block:{block_id}"


class MerkleSpotChecker:
    """
    블록 단위 머클 스팟체크.

    데이터 흐름:
        전체 엔트리 → 블록 분할 → 블록별 머클 루트
            ↓                          ↓
        저장된 루트와 비교        불일치 시 드릴다운
            ↓                          ↓
        OK (블록 루트 일치)      HashChainVerifier로 정밀 검증

    사용 시나리오:
    1. BackgroundIntegrityVerifier: 5분 주기 검증에서 엔트리가 많을 때
    2. 일일 풀 검증: 01:00 스케줄에서 수백만 건 효율적 처리
    3. PostRecoveryIntegrityGate: 장애 기간이 길어 WAL이 많을 때
    """

    def __init__(
        self,
        block_size: int = 1000,
        redis_client: Any | None = None,
        namespace: str = "global",
    ):
        """
        Args:
            block_size: 블록 당 엔트리 수 (기본 1000)
            redis_client: Redis 클라이언트 (머클 루트 캐시용)
            namespace: 네임스페이스
        """
        self._block_size = block_size
        self._redis = redis_client
        self._namespace = namespace

    def spot_check(self, entries: list[dict]) -> dict[str, Any]:
        """
        블록 단위 스팟체크 실행.

        Args:
            entries: 검증할 엔트리 목록

        Returns:
            {
                "valid": bool,
                "strategy": "merkle_spot_check",
                "total_entries": int,
                "total_blocks": int,
                "blocks_checked": int,
                "blocks_failed": int,
                "failed_block_ids": list[int],
                "drill_down_results": list[dict],
                "errors": list[str],
                "duration_ms": float,
            }
        """
        start = time.time()

        if not entries:
            return {
                "valid": True,
                "strategy": "merkle_spot_check",
                "total_entries": 0,
                "total_blocks": 0,
                "blocks_checked": 0,
                "blocks_failed": 0,
                "failed_block_ids": [],
                "drill_down_results": [],
                "errors": [],
            }

        # Step 1: 블록 분할
        blocks = self._split_into_blocks(entries)
        total_blocks = len(blocks)

        # Step 2: 각 블록의 머클 루트 계산 및 비교
        failed_blocks = []
        for block_id, block_entries in enumerate(blocks):
            current_root = self._compute_block_merkle_root(block_entries)
            stored_root = self._get_stored_merkle_root(block_id)

            if stored_root is None:
                # 첫 실행: 루트 저장만 수행
                self._store_merkle_root(block_id, current_root)
            elif current_root != stored_root:
                failed_blocks.append(block_id)

        # Step 3: 실패 블록만 드릴다운
        drill_down_results = []
        errors = []

        if failed_blocks:
            from selfhealing.audit.integrity.verifier import HashChainVerifier
            verifier = HashChainVerifier()

            for block_id in failed_blocks:
                block_entries = blocks[block_id]
                issues = verifier.find_tampering(block_entries)
                drill_down_results.append({
                    "block_id": block_id,
                    "block_range": f"seq {block_id * self._block_size + 1}"
                                   f"-{(block_id + 1) * self._block_size}",
                    "issues": issues,
                })
                errors.extend([i["message"] for i in issues])

        duration_ms = (time.time() - start) * 1000

        return {
            "valid": len(failed_blocks) == 0,
            "strategy": "merkle_spot_check",
            "total_entries": len(entries),
            "total_blocks": total_blocks,
            "blocks_checked": total_blocks,
            "blocks_failed": len(failed_blocks),
            "failed_block_ids": failed_blocks,
            "drill_down_results": drill_down_results,
            "errors": errors,
            "duration_ms": duration_ms,
        }

    def build_merkle_roots(self, entries: list[dict]) -> dict[str, Any]:
        """
        전체 엔트리의 블록별 머클 루트를 계산하고 저장.

        일일 앵커 생성(DailyHashAnchor.create_anchor) 후 호출하여
        다음 주기 스팟체크의 기준선을 만듭니다.

        기존 패턴 참조:
        - anchor.py L90: create_anchor() → 일일 기준점 생성
        - 이 함수: 블록별 기준점 생성 (anchor의 세분화 버전)

        Returns:
            {"blocks_stored": int, "total_entries": int}
        """
        blocks = self._split_into_blocks(entries)
        stored = 0

        for block_id, block_entries in enumerate(blocks):
            root = self._compute_block_merkle_root(block_entries)
            self._store_merkle_root(block_id, root)
            stored += 1

        logger.info(
            f"[MerkleSpotChecker] Built {stored} block merkle roots "
            f"for {len(entries)} entries"
        )

        return {"blocks_stored": stored, "total_entries": len(entries)}

    def _split_into_blocks(
        self, entries: list[dict]
    ) -> list[list[dict]]:
        """엔트리를 block_size 단위로 분할."""
        return [
            entries[i:i + self._block_size]
            for i in range(0, len(entries), self._block_size)
        ]

    def _compute_block_merkle_root(self, block_entries: list[dict]) -> str:
        """
        블록의 머클 루트 계산.

        기존 MerkleTree (signed_manifest.py L55) 재사용.
        """
        from selfhealing.audit.signed_manifest import MerkleTree

        tree = MerkleTree()
        for entry in block_entries:
            # 엔트리 전체를 정규화 직렬화 후 리프로 추가
            entry_bytes = json.dumps(
                entry, sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
            tree.add_leaf(entry_bytes)

        return tree.compute_root_hex()

    def _get_stored_merkle_root(self, block_id: int) -> str | None:
        """저장된 블록 머클 루트 조회."""
        if self._redis is None:
            return None

        try:
            key = BLOCK_MERKLE_ROOT_KEY.format(
                namespace=self._namespace, block_id=block_id
            )
            value = self._redis.get(key)
            if isinstance(value, bytes):
                return value.decode("utf-8")
            return value
        except Exception:
            return None

    def _store_merkle_root(self, block_id: int, root: str) -> None:
        """블록 머클 루트 저장."""
        if self._redis is None:
            return

        try:
            key = BLOCK_MERKLE_ROOT_KEY.format(
                namespace=self._namespace, block_id=block_id
            )
            # DailyHashAnchor 보관일(90일)과 동일 TTL
            self._redis.set(key, root, ex=90 * 86400)
        except Exception as e:
            logger.warning(
                f"[MerkleSpotChecker] Failed to store root for block {block_id}: {e}"
            )
```

### 4.4 DailyHashAnchor와의 연동

기존 `create_cascade_daily_checkpoint()` (`cascade_cleanup_tasks.py` L299) 실행 후 머클 루트도 함께 빌드:

```python
# cascade_cleanup_tasks.py의 create_cascade_daily_checkpoint()에 추가

def create_cascade_daily_checkpoint(namespace: str = "global") -> dict[str, Any]:
    """일일 체크포인트 생성."""
    # ... 기존 로직 (앵커 생성) ...

    # ★ 신규: 머클 블록 루트도 함께 빌드
    try:
        from selfhealing.audit.integrity.merkle_spot_checker import MerkleSpotChecker

        checker = MerkleSpotChecker(
            block_size=1000,
            redis_client=_get_redis_client(),
            namespace=namespace,
        )
        entries = auditor.get_recent_events(namespace, limit=100000)
        merkle_result = checker.build_merkle_roots(
            [e.to_dict() for e in entries]
        )
        checkpoint["merkle_blocks"] = merkle_result["blocks_stored"]
    except Exception as e:
        logger.warning(f"[CascadeCleanup] Merkle root build failed: {e}")

    return checkpoint
```

### 4.5 아키텍처 다이어그램

```
┌─────────────────────────────────────────────────────────────────┐
│                    Hash Chain Integrity Triad                    │
│                                                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌───────────────┐ │
│  │  Background       │  │  PostRecovery    │  │  Merkle       │ │
│  │  Integrity        │  │  Integrity       │  │  Spot         │ │
│  │  Verifier         │  │  Gate            │  │  Checker      │ │
│  │                   │  │                  │  │               │ │
│  │  Celery Beat      │  │  EventBus Hook   │  │  O(log n)     │ │
│  │  5분 / 1일 주기   │  │  CB CLOSED       │  │  블록 비교    │ │
│  │                   │  │  CRITICAL 우선순위│  │               │ │
│  └────────┬──────────┘  └────────┬─────────┘  └───────┬───────┘ │
│           │                      │                     │         │
│           ▼                      ▼                     ▼         │
│  ┌──────────────────────────────────────────────────────────────┐│
│  │              기존 Integrity 인프라 (재사용)                   ││
│  │                                                               ││
│  │  HashChainVerifier · DailyHashAnchor · MerkleTree            ││
│  │  HashChainReconciler · StartupHashChainSync                  ││
│  │  IntegrityHealthScore (Prometheus Gauge)                     ││
│  │  WAL (_write_to_wal) · ContinuousAuditRecorder              ││
│  └──────────────────────────────────────────────────────────────┘│
│           │                      │                     │         │
│           ▼                      ▼                     ▼         │
│     Grafana Dashboard: "Integrity Status: OK/WARN/CRITICAL"     │
│     selfhealing_integrity_health_score (0-100)                   │
│     selfhealing_integrity_degraded_count                         │
│     selfhealing_integrity_recoveries_today                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. 통합: 3계층이 함께 동작하는 시나리오

### 5.1 정상 운영 중 (장애 없음)

```
[Celery Beat 5분 주기]
    → BackgroundIntegrityVerifier.verify_hash_chain_integrity()
        → 엔트리 < 10,000건: HashChainVerifier.verify_chain() (O(n))
        → 엔트리 ≥ 10,000건: MerkleSpotChecker.spot_check() (O(n/block))
        → IntegrityHealthScore 업데이트
        → Grafana: "Integrity Status: OK" ✅

[Celery Beat 매일 01:00]
    → 전체 풀 검증 (use_merkle_spot_check=False)
    → DailyHashAnchor.create_anchor() + MerkleSpotChecker.build_merkle_roots()
```

### 5.2 장애 발생 → 복구 시

```
[CB Open → Closed 전환]
    → EventBus: CIRCUIT_BREAKER_CLOSED 발행

    → (1) PostRecoveryIntegrityGate (CRITICAL)
        → WAL 미동기화 엔트리 수집
        → HashChainVerifier.verify_chain()
        → 통과 시: event.data["integrity_failed"] = False
        → 실패 시: event.data["integrity_failed"] = True + 알림

    → (2) _on_circuit_breaker_closed (NORMAL)
        → event.data["integrity_failed"] 확인
        → False → Track 1 Replay 실행
        → True  → Replay 차단, 관리자 수동 확인 대기

    → (3) _on_circuit_breaker_closed_postmortem (LOW)
        → 자동 Post-mortem 생성 (무결성 결과 포함)
```

### 5.3 대규모 변조 감지 시

```
[BackgroundIntegrityVerifier]
    → MerkleSpotChecker.spot_check()
        → 블록 #3,847의 머클 루트 불일치 감지!
        → 드릴다운: HashChainVerifier.find_tampering(block_3847)
            → "Entry 3847042 has been modified"
        → IntegrityHealthScore.record_chain_break()
        → Prometheus: selfhealing_integrity_health_score = 85.3 (WARNING)
        → Grafana: "Integrity Status: WARNING" ⚠️
        → _alert_integrity_violation() → 관리자 알림
```

---

## 6. 설정 통합

### 6.1 AuditIntegritySettings 확장

기존 `settings/audit_integrity.py`에 추가할 필드:

```python
# ====================================================================
# Integrity Triad (219) - Background Verifier + Recovery Gate + Merkle
# ====================================================================

# BackgroundIntegrityVerifier
background_verify_interval: int = Field(
    default=300,
    ge=60,
    le=3600,
    description="백그라운드 검증 주기 (초). 5분 기본.",
)

background_verify_merkle_threshold: int = Field(
    default=10000,
    ge=1000,
    le=1000000,
    description="이 엔트리 수 이상이면 MerkleSpotChecker 전환. 10000 기본.",
)

# MerkleSpotChecker
merkle_block_size: int = Field(
    default=1000,
    ge=100,
    le=10000,
    description="머클 스팟체크 블록 크기. 1000 기본.",
)

# PostRecoveryIntegrityGate
integrity_gate_fail_open: bool = Field(
    default=True,
    description="무결성 게이트 Fail-Open 정책. False면 Fail-Secure (PCI-DSS).",
)

integrity_gate_max_entries: int = Field(
    default=50000,
    ge=1000,
    le=1000000,
    description="게이트 검증 시 최대 엔트리 수. 초과 시 MerkleSpotChecker 사용.",
)
```

### 6.2 환경변수 매핑

```bash
SELFHEALING_AUDIT_INTEGRITY_BACKGROUND_VERIFY_INTERVAL=300
SELFHEALING_AUDIT_INTEGRITY_BACKGROUND_VERIFY_MERKLE_THRESHOLD=10000
SELFHEALING_AUDIT_INTEGRITY_MERKLE_BLOCK_SIZE=1000
SELFHEALING_AUDIT_INTEGRITY_INTEGRITY_GATE_FAIL_OPEN=true
SELFHEALING_AUDIT_INTEGRITY_INTEGRITY_GATE_MAX_ENTRIES=50000
```

---

## 7. 신규 파일 목록

| 파일 | 역할 | 크기 (추정) |
|------|------|------------|
| `selfhealing/tasks/integrity_tasks.py` | BackgroundIntegrityVerifier + Beat Schedule | ~150 LOC |
| `selfhealing/services/event_bus/integrity_gate.py` | PostRecoveryIntegrityGate | ~200 LOC |
| `selfhealing/audit/integrity/merkle_spot_checker.py` | MerkleSpotChecker | ~250 LOC |

### 7.1 기존 파일 수정

| 파일 | 변경 | 영향 범위 |
|------|------|----------|
| `services/event_bus/bus.py` | `_on_circuit_breaker_closed`에 integrity_failed 체크 추가, 핸들러 등록 | 3줄 + import |
| `settings/audit_integrity.py` | 5개 필드 추가 | 기존 설정 하위 호환 (모두 기본값 있음) |
| `tasks/cascade_cleanup_tasks.py` | `create_cascade_daily_checkpoint`에 머클 빌드 추가 | try/except로 감싸 기존 로직 무영향 |
| `audit/integrity/__init__.py` | `MerkleSpotChecker` + `canonical_json_bytes` export 추가 | 2줄 |
| `audit/integrity/models.py` | `canonical_json_bytes()` 함수 추가 (기존 `compute_hash` 변경 없음) | ~20 LOC |

---

## 8. Grafana 대시보드 패널 구성

기존 `IntegrityHealthScore`(`health_score.py` L120-127)의 Prometheus 지표 활용:

### 8.1 메인 패널: Integrity Status

```promql
# 상태 표시 (Stat Panel)
selfhealing_integrity_health_score

# 임계값:
# ≥ 95: OK (green)      ← health_healthy_threshold (settings)
# ≥ 80: WARNING (yellow) ← health_warning_threshold
# < 50: CRITICAL (red)   ← health_critical_threshold
```

### 8.2 리커버리 추적

```promql
# 24시간 자동 복구 횟수 (Gauge)
selfhealing_integrity_recoveries_today

# Degraded 시퀀스 수 (실시간)
selfhealing_integrity_degraded_count

# Orphaned 시퀀스 수
selfhealing_integrity_orphaned_count
```

### 8.3 신규 지표 (추가 필요)

```python
# integrity_tasks.py에서 추가
GAUGE_LAST_VERIFY_DURATION = "selfhealing_integrity_verify_duration_ms"
GAUGE_LAST_VERIFY_ENTRIES = "selfhealing_integrity_verify_entries_count"
COUNTER_VERIFY_FAILURES = "selfhealing_integrity_verify_failures_total"
COUNTER_GATE_BLOCKS = "selfhealing_integrity_gate_blocks_total"
```

---

## 9. 구현 우선순위 및 의존성

```
Phase 1 (즉시):  PostRecoveryIntegrityGate
   ├─ 기존 EventBus + HashChainVerifier 조합
   ├─ 신규 코드 최소 (핸들러 1개 + bus.py 수정 3줄)
   └─ 즉각적 안전성 향상 (오염 리플레이 차단)

Phase 2 (1일):   BackgroundIntegrityVerifier
   ├─ Celery Beat 등록 + 기존 verify 함수 래핑
   ├─ IntegrityHealthScore 연동
   └─ Grafana 대시보드 구성

Phase 3 (2-3일): MerkleSpotChecker
   ├─ 기존 MerkleTree 재사용
   ├─ Redis 블록 루트 저장 체계
   ├─ DailyHashAnchor 연동
   └─ BackgroundIntegrityVerifier에서 자동 전환 로직
```

---

## 10. 테스트 전략

### 10.1 PostRecoveryIntegrityGate

```python
# tests/self_healing/services/event_bus/test_integrity_gate.py
class TestPostRecoveryIntegrityGate:
    def test_valid_chain_allows_replay(self):
        """무결성 정상 → integrity_failed=False → 리플레이 허용."""

    def test_broken_chain_blocks_replay(self):
        """무결성 깨짐 → integrity_failed=True → 리플레이 차단."""

    def test_gate_error_fail_open(self):
        """게이트 자체 오류 → Fail-Open → 리플레이 허용."""

    def test_gate_error_fail_secure(self):
        """PCI-DSS 모드 → 게이트 오류 시 리플레이 차단."""

    def test_critical_priority_runs_before_replay(self):
        """CRITICAL 우선순위가 NORMAL(Replay) 전에 실행되는지 확인."""
```

### 10.2 BackgroundIntegrityVerifier

```python
# tests/self_healing/tasks/test_integrity_tasks.py
class TestBackgroundIntegrityVerifier:
    def test_small_dataset_uses_full_chain(self):
        """10000건 미만 → HashChainVerifier.verify_chain() 사용."""

    def test_large_dataset_uses_merkle(self):
        """10000건 이상 → MerkleSpotChecker.spot_check() 전환."""

    def test_health_score_updated_on_success(self):
        """검증 성공 시 IntegrityHealthScore 업데이트."""

    def test_beat_schedule_registration(self):
        """get_integrity_beat_schedule() 반환값 검증."""
```

### 10.3 MerkleSpotChecker

```python
# tests/self_healing/audit/integrity/test_merkle_spot_checker.py
class TestMerkleSpotChecker:
    def test_spot_check_all_valid(self):
        """정상 데이터 → valid=True, blocks_failed=0."""

    def test_spot_check_detects_tampered_block(self):
        """변조된 블록 → valid=False + 정확한 block_id 반환."""

    def test_drill_down_identifies_exact_entry(self):
        """드릴다운 → find_tampering()으로 정확한 시퀀스 식별."""

    def test_build_merkle_roots_stores_to_redis(self):
        """build_merkle_roots() → Redis에 블록 루트 저장."""

    def test_first_run_stores_roots_without_failure(self):
        """첫 실행 시 저장된 루트 없음 → 저장만, 실패 아님."""

    def test_performance_vs_full_chain(self):
        """100만 건에서 MerkleSpotChecker가 O(n) 대비 빠른지 확인."""
```

---

## 11. 리뷰 반영 보완사항

> **리뷰 일자**: 2026-02-11
> **리뷰 항목**: 5건 (외부 리뷰) + 1건 (자체 발견) = 총 6건
> **상태**: 모든 항목 설계 반영 완료

### 11.1 리뷰 요약 및 판정표

| # | 리뷰 항목 | 심각도 | 판정 | 원본 섹션 | 코드 근거 |
|---|-----------|--------|------|-----------|----------|
| R1 | event.data 쓰기 안전장치 (상수 키 + docstring) | MEDIUM | 동의 — 위치 수정 | §3.3, §3.4 | `bus.py` L380-388: 동기 for-loop, 기존 핸들러 `event.data.get()` 읽기 전용 20건+ |
| R2 | MerkleSpotChecker 시퀀스 기반 블록 분할 | **CRITICAL** | 완전 동의 | §4.3 | `verifier.py` L57: `expected_sequence` 기반 검증, `local_manager.py` L88: 단조증가 |
| R3 | Redis 분산 락 적용 + WAL 읽기 최적화 | HIGH | 동의 | §2.2 | `redis_manager.py` L123: `RedisDistributedLock` 기존 사용 패턴 |
| R4 | Redis TTL 하드코딩 → 설정 참조 | MEDIUM | 동의 — 부분 반영됨 | §4.3 | `anchor.py` L137: `self._retention_days * 86400` 패턴 |
| R5-A | Canonical Serialization Helper | HIGH | 동의 — 위치 수정 | §4.3 신규 | `models.py` L38 vs `checksum.py` L197: 직렬화 불일치 확인 |
| R5-B | 3회 재시도 + 소스 리로드 | MEDIUM | 동의 | §2.2 | `redis_manager.py` L108: fallback 패턴 참고 |
| R5-C | Fail-Open 설정 주입 | LOW | 동의 — 이미 설계됨 | §3.6, §6.1 | `continuous_audit.py` L93: `fail_open` 파라미터 패턴 |
| A1 | WAL API 메서드명 오류 | **CRITICAL** | 자체 발견 | §3.3 | `wal.py` L776: `recover_unprocessed()` (`read_unprocessed` 미존재) |

---

### 11.2 [R1] event.data 쓰기 안전장치

**문제**: 기존 코드베이스에서 `event.data[key] = value` (쓰기)는 전례 없는 패턴. 오타로 인한 로직 누락 위험.

**코드 근거**:
- `bus.py` L380-388: `subscription.handler(event)` — 동기 순차 호출, 동일 `event` 객체 공유
- `bus.py` 전체에서 `event.data[...] = ...` 패턴: **0건** (grep 확인)
- 기존 핸들러 20건+: 모두 `event.data.get(...)` 읽기 전용

**결정**: 상수 키를 `integrity_gate.py`에 정의하고, `bus.py`에서 import하여 참조.

리뷰어는 "bus.py나 공용 상수 파일"에 정의를 제안했으나, 기존 코드베이스의 상수 정의 패턴을 참고하여 **모듈 로컬 정의 + import 참조** 방식을 선택:
- `incident_group.py` L324: `REDIS_KEY_DATA` → 사용하는 모듈 자체에 정의
- `BUDGET_EXHAUSTED_BY_SLO_KEY` → 사용하는 모듈 자체에 정의
- `bus.py`는 이미 1850줄이며 integrity 관련 상수를 넣으면 관심사 분리 위반
- `event_bus/` 디렉토리에 전용 상수 파일 미존재 (bus.py, redis_bus.py, \_\_init\_\_.py만 있음)

**네이밍 확인**: `INTEGRITY_GATE_KEY`, `INTEGRITY_FAILED_KEY` — 시스템 미존재 ✅

**수정 코드 — `bus.py`의 `_on_circuit_breaker_closed` 수정분**:

```python
# bus.py L718 부근 — 수정된 _on_circuit_breaker_closed

from selfhealing.services.event_bus.integrity_gate import INTEGRITY_FAILED_KEY


def _on_circuit_breaker_closed(event: SelfHealingEvent):
    """
    CB 복구 시 자동 Replay 트리거 (Track 1).

    ★ 의존성: CRITICAL 우선순위의 PostRecoveryIntegrityGate
    (integrity_gate.py)가 이 핸들러보다 먼저 실행되어
    event.data[INTEGRITY_FAILED_KEY] 플래그를 설정합니다.
    플래그가 True인 경우 리플레이를 차단합니다.
    (219_HASH_CHAIN_INTEGRITY_TRIAD.md §3.2 참조)

    RuntimeConfig에서 track1_enabled 설정을 확인하고,
    활성화된 경우 conditional_replay_on_circuit_close 태스크를 트리거합니다.
    """
    service_name = event.data.get("service_name", "unknown")

    # ★ R1: IntegrityGate 결과 확인 (상수 import로 오타 방지)
    if event.data.get(INTEGRITY_FAILED_KEY, False):
        logger.critical(
            f"[EventHandler] Replay BLOCKED for {service_name}: "
            f"integrity gate failed. "
            f"Details: {event.data.get('integrity_gate_result', {})}"
        )
        return  # 리플레이 중단

    # ... 이하 기존 로직 동일 (bus.py L730-767)
```

---

### 11.3 [R2] MerkleSpotChecker 시퀀스 기반 블록 분할 [Critical]

**문제**: §4.3의 `entries[i:i+1000]` 리스트 슬라이싱은 중간 엔트리 삭제 시 모든 후속 블록의 머클 루트를 오염시킴.

**코드 근거**:
- `local_manager.py` L88: `self._sequence += 1` — sequence는 1부터 단조 증가
- `verifier.py` L57: `if seq != expected_sequence` — 시퀀스 연속성 기반 검증
- `verifier.py` L103-110: `find_tampering()`도 missing entry를 별도 보고하되 **존재하는 엔트리만으로 해시 검증** 수행

**결정**:
- 블록 정의: `block_id = (sequence - 1) // block_size` (절대적 시퀀스 기반)
- Gap 처리: **"있는 것만으로 계산"** 채택 (NULL 리프 불채택)
  - 이유: `HashChainVerifier.find_tampering()` (`verifier.py` L103)이 이미 이 방식 사용
  - 빌드/검증 시 동일 로직 보장으로 false positive 원천 차단
- 반환 타입: `list[list[dict]]` → `dict[int, list[dict]]`로 변경
  - `spot_check()`, `build_merkle_roots()`의 이터레이션을 `blocks.items()`로 수정

**네이밍 확인**: 메서드명 `_split_into_blocks` — 유지 (내부 private, 시그니처만 변경)

**수정 코드 — `merkle_spot_checker.py` 변경분**:

```python
def _split_into_blocks(self, entries: list[dict]) -> dict[int, list[dict]]:
    """
    시퀀스 기반 블록 분할.

    block_id = (sequence - 1) // block_size

    리스트 인덱스가 아닌 절대적 시퀀스 번호로 블록을 결정하므로,
    중간 엔트리가 삭제되어도 다른 블록의 루트에 영향을 주지 않음.

    코드 근거:
    - local_manager.py L88: sequence는 1부터 단조 증가
    - verifier.py L103: find_tampering()도 존재하는 엔트리만 검증

    Returns:
        {block_id: [entries_in_block]} 딕셔너리
    """
    blocks: dict[int, list[dict]] = {}
    for entry in entries:
        seq = entry.get("integrity", {}).get("sequence", 0)
        if seq <= 0:
            continue
        block_id = (seq - 1) // self._block_size
        blocks.setdefault(block_id, []).append(entry)
    return blocks


def spot_check(self, entries: list[dict]) -> dict[str, Any]:
    """블록 단위 스팟체크 실행. (시퀀스 기반 블록)"""
    start = time.time()

    if not entries:
        return {
            "valid": True,
            "strategy": "merkle_spot_check",
            "total_entries": 0,
            "total_blocks": 0,
            "blocks_checked": 0,
            "blocks_failed": 0,
            "failed_block_ids": [],
            "drill_down_results": [],
            "errors": [],
        }

    # Step 1: 시퀀스 기반 블록 분할
    blocks = self._split_into_blocks(entries)
    total_blocks = len(blocks)

    # Step 2: 각 블록의 머클 루트 계산 및 비교
    failed_blocks = []
    for block_id, block_entries in sorted(blocks.items()):
        current_root = self._compute_block_merkle_root(block_entries)
        stored_root = self._get_stored_merkle_root(block_id)

        if stored_root is None:
            self._store_merkle_root(block_id, current_root)
        elif current_root != stored_root:
            failed_blocks.append(block_id)

    # Step 3: 실패 블록만 드릴다운
    drill_down_results = []
    errors = []

    if failed_blocks:
        from selfhealing.audit.integrity.verifier import HashChainVerifier
        verifier = HashChainVerifier()

        for block_id in failed_blocks:
            block_entries = blocks[block_id]
            issues = verifier.find_tampering(block_entries)
            seq_start = block_id * self._block_size + 1
            seq_end = (block_id + 1) * self._block_size
            drill_down_results.append({
                "block_id": block_id,
                "block_range": f"seq {seq_start}-{seq_end}",
                "actual_entries": len(block_entries),
                "issues": issues,
            })
            errors.extend([i["message"] for i in issues])

    duration_ms = (time.time() - start) * 1000

    return {
        "valid": len(failed_blocks) == 0,
        "strategy": "merkle_spot_check",
        "total_entries": len(entries),
        "total_blocks": total_blocks,
        "blocks_checked": total_blocks,
        "blocks_failed": len(failed_blocks),
        "failed_block_ids": failed_blocks,
        "drill_down_results": drill_down_results,
        "errors": errors,
        "duration_ms": duration_ms,
    }


def build_merkle_roots(self, entries: list[dict]) -> dict[str, Any]:
    """블록별 머클 루트 계산 및 저장. (시퀀스 기반)"""
    blocks = self._split_into_blocks(entries)
    stored = 0

    for block_id, block_entries in sorted(blocks.items()):
        root = self._compute_block_merkle_root(block_entries)
        self._store_merkle_root(block_id, root)
        stored += 1

    logger.info(
        f"[MerkleSpotChecker] Built {stored} block merkle roots "
        f"for {len(entries)} entries"
    )
    return {"blocks_stored": stored, "total_entries": len(entries)}
```

---

### 11.4 [R3] Redis 분산 락 적용

**문제**: `threading.RLock`은 Celery Worker(별도 프로세스)와 Django(별도 프로세스) 간 동시성을 보장하지 못함.

**코드 근거**:
- `redis_manager.py` L123: `RedisDistributedLock` 기존 사용 (해시체인 기록 시)
- `postmortem/store.py` L203: 동일 패턴 (포스트모템 저장 시)
- `RedisDistributedLock` (`adapters/cache/redis_adapter.py` L32): SET NX PX + Lua 스크립트 기반
- `DistributedLockSettings` (`settings/distributed_lock.py` L28): timeout, retry 설정 체계

**결정**: 기존 `RedisDistributedLock` 인프라 그대로 재사용. `verify_hash_chain_integrity` 태스크 진입 시 락 획득.

Redis 클라이언트 획득은 기존 `adapters/redis/__init__.py` L24의 `get_redis_client()` 사용.

**네이밍 확인**: `selfhealing:integrity:background_verify_lock` — 시스템 미존재 ✅

**수정 코드 — `integrity_tasks.py`의 `verify_hash_chain_integrity` 수정분**:

```python
def verify_hash_chain_integrity(
    namespace: str = "global",
    use_merkle_spot_check: bool = True,
    block_size: int = 1000,
) -> dict[str, Any]:
    """
    백그라운드 해시체인 무결성 검증. (Redis 분산 락 적용)

    변경사항 (리뷰 R3, R5-B):
    - RedisDistributedLock으로 멀티 프로세스 중복 실행 방지
    - 3회 재시도 + 소스 리로드로 false positive 방지
    - AuditIntegritySettings에서 threshold/block_size 읽기
    """
    start = time.time()

    try:
        from datetime import timedelta

        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock
        from selfhealing.adapters.redis import get_redis_client
        from selfhealing.audit.integrity import (
            HashChainVerifier,
            get_integrity_health_score,
        )
        from selfhealing.settings.audit_integrity import get_audit_integrity_settings

        settings = get_audit_integrity_settings()
        verifier = HashChainVerifier()
        health = get_integrity_health_score()

        # ★ R3: Redis 분산 락 — 멀티 프로세스 중복 실행 방지
        # redis_manager.py L123 패턴 재사용
        redis_client = get_redis_client()
        if redis_client is None:
            logger.warning("[BackgroundIntegrityVerifier] Redis unavailable, skip")
            return {"valid": True, "skipped": True, "reason": "redis_unavailable"}

        lock = RedisDistributedLock(
            redis_client=redis_client,
            name="selfhealing:integrity:background_verify_lock",
            timeout=timedelta(seconds=settings.hash_chain_lock_timeout),
            blocking_timeout=5.0,  # 5초 대기 후 포기
        )

        if not lock.acquire(blocking=True):
            logger.info(
                "[BackgroundIntegrityVerifier] Another verify task running, skip"
            )
            return {"valid": True, "skipped": True, "reason": "lock_contention"}

        try:
            entries = _get_entries_since_last_anchor(namespace)
            entry_count = len(entries)

            if (
                use_merkle_spot_check
                and entry_count > settings.background_verify_merkle_threshold
            ):
                result = _merkle_spot_check(entries, settings.merkle_block_size)
            else:
                # ★ R5-B: 3회 재시도 + 소스 리로드
                is_valid, error_msg = _verify_with_retry(
                    verifier=verifier,
                    entries_loader=lambda: _get_entries_since_last_anchor(namespace),
                    max_retries=3,
                )
                result = {
                    "valid": is_valid,
                    "strategy": "full_chain",
                    "checked": entry_count,
                    "errors": [error_msg] if error_msg else [],
                }

            duration_ms = (time.time() - start) * 1000
            if result["valid"]:
                health.record_recovery(
                    event_type="background_verify_ok",
                    sequences_affected=entry_count,
                    recovery_time_ms=duration_ms,
                )
            else:
                health.record_chain_break()
                _alert_integrity_violation(namespace, result)

            result["duration_ms"] = duration_ms
            result["namespace"] = namespace
            return result

        finally:
            lock.release()

    except Exception as e:
        logger.error(f"[BackgroundIntegrityVerifier] Failed: {e}", exc_info=True)
        return {"valid": False, "error": str(e), "namespace": namespace}
```

---

### 11.5 [R4] Redis TTL 설정 참조

**문제**: §4.3의 `_store_merkle_root()`에서 `ex=90 * 86400`으로 하드코딩됨.

**코드 근거**:
- `anchor.py` L137: `self._redis.expire(anchor_key, self._retention_days * 86400)` — 설정 참조 패턴
- `settings/audit_integrity.py`: `anchor_retention_days = 90` (기본값)

**결정**: `get_audit_integrity_settings().anchor_retention_days` 참조로 anchor.py L137과 통일.

**수정 코드 — `merkle_spot_checker.py`의 `_store_merkle_root` 수정분**:

```python
def _store_merkle_root(self, block_id: int, root: str) -> None:
    """블록 머클 루트 저장. (TTL: anchor_retention_days 설정 참조)"""
    if self._redis is None:
        return

    try:
        from selfhealing.settings.audit_integrity import get_audit_integrity_settings

        settings = get_audit_integrity_settings()
        key = BLOCK_MERKLE_ROOT_KEY.format(
            namespace=self._namespace, block_id=block_id
        )
        # ★ R4: anchor.py L137 패턴과 동일 — 설정에서 TTL 참조
        self._redis.set(key, root, ex=settings.anchor_retention_days * 86400)
    except Exception as e:
        logger.warning(
            f"[MerkleSpotChecker] Failed to store root for block {block_id}: {e}"
        )
```

---

### 11.6 [R5-A] Canonical Serialization Helper

**문제**: 코드베이스의 JSON 직렬화가 일관성 없음.

| 위치 | `sort_keys` | `default=str` | `separators` | `ensure_ascii` |
|------|:-----------:|:-------------:|:------------:|:--------------:|
| `integrity/models.py` `compute_hash()` L38 | ✅ | ✅ | ❌ (기본 공백) | ❌ (기본 True) |
| `checksum.py` `_normalize_to_bytes()` L197 | ✅ | ❌ | ✅ `(",",":")` | ❌ |
| `canary/audit.py` L228 | ✅ | ✅ | ❌ | ❌ |
| `cascade_event.py` L572 | ✅ | ❌ | ❌ | ❌ |

동일한 dict를 `compute_hash()`와 `_normalize_to_bytes()`에 넣으면 **다른 해시**가 산출됨.

**결정**:

리뷰어는 `audit/utils.py` 신규 생성을 제안. 그러나:
- `audit/utils.py`는 현재 미존재 (file_search 0건)
- `compute_hash()`가 이미 `audit/integrity/models.py`에 정의됨
- 같은 파일에 `canonical_json_bytes()` 추가가 import 경로상 자연스러움

따라서 **`audit/integrity/models.py`에 추가** 선택.

**⚠️ Breaking Change 방지**:

기존 `compute_hash()`의 `separators` 없는 출력을 변경하면 Redis/파일에 저장된 모든 기존 해시와 불일치. 따라서:
1. 기존 `compute_hash()` **변경 금지** (하위 호환성)
2. 신규 `canonical_json_bytes()`는 **MerkleSpotChecker 전용**
3. MerkleSpotChecker의 빌드와 검증이 동일 함수 사용 = false positive 없음

**네이밍 확인**: `canonical_json_bytes` — 시스템 미존재 ✅

**추가 코드 — `audit/integrity/models.py`에 함수 추가**:

```python
def canonical_json_bytes(data: dict[str, Any]) -> bytes:
    """
    정규화된 JSON 직렬화 (Merkle 해시 계산용).

    MerkleSpotChecker의 빌드/검증에서 동일한 바이트열을 보장합니다.
    기존 compute_hash()는 하위 호환성을 위해 변경하지 않습니다.

    직렬화 규칙:
    - sort_keys=True: 키 순서 결정적
    - default=str: datetime 등 비직렬화 타입 처리
    - separators=(",", ":"): 공백 없는 컴팩트 출력
    - ensure_ascii=False: UTF-8 원본 보존

    기존 직렬화와의 차이점:
    - compute_hash(): separators 없음 (공백 포함), ensure_ascii 기본 True
    - _normalize_to_bytes() (checksum.py): default=str 없음
    - 이 함수: 모든 옵션 명시적 — MerkleSpotChecker 전용
    """
    return json.dumps(
        data,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
```

**`merkle_spot_checker.py`의 `_compute_block_merkle_root` 수정분**:

```python
def _compute_block_merkle_root(self, block_entries: list[dict]) -> str:
    """
    블록의 머클 루트 계산.

    ★ R5-A: canonical_json_bytes() 사용으로 직렬화 일관성 보장.
    기존 MerkleTree (signed_manifest.py L55) 구조 재사용.
    """
    from selfhealing.audit.integrity.models import canonical_json_bytes
    from selfhealing.audit.signed_manifest import MerkleTree

    tree = MerkleTree()
    for entry in block_entries:
        entry_bytes = canonical_json_bytes(entry)
        tree.add_leaf(entry_bytes)

    return tree.compute_root_hex()
```

---

### 11.7 [R5-B] 3회 재시도 + 소스 리로드

**문제**: 일시적 글리치(캐시 오염, 동시 쓰기 중 읽기)로 인한 false positive.

**코드 근거**:
- `redis_manager.py` L108: Redis 실패 시 fallback 패턴 (1회 재시도)
- 이 시스템에는 "N회 재시도 후 확정" 패턴이 없으므로 신규 도입

**결정**: `entries_loader` callable을 받아 매 시도마다 **소스에서 리로드**. 단순 동일 데이터 재검증이 아닌, 소스 재조회로 일시적 글리치 해소.

**네이밍 확인**: `_verify_with_retry` — 시스템 미존재 ✅

**추가 코드 — `integrity_tasks.py`에 함수 추가**:

```python
from collections.abc import Callable


def _verify_with_retry(
    verifier,
    entries_loader: Callable[[], list[dict]],
    max_retries: int = 3,
) -> tuple[bool, str | None]:
    """
    재시도 포함 해시체인 검증.

    매 시도마다 entries_loader()로 소스에서 리로드하여
    일시적 캐시 오염/동시 쓰기 글리치를 해소합니다.

    Args:
        verifier: HashChainVerifier 인스턴스
        entries_loader: 엔트리 로드 callable (매번 새로 로드)
        max_retries: 최대 재시도 횟수

    Returns:
        (is_valid, error_message) 튜플
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        entries = entries_loader()
        if not entries:
            return True, None

        is_valid, error_msg = verifier.verify_chain(entries)
        if is_valid:
            if attempt > 1:
                logger.info(
                    f"[BackgroundIntegrityVerifier] Verification succeeded "
                    f"on retry {attempt}/{max_retries}"
                )
            return True, None

        last_error = error_msg
        logger.warning(
            f"[BackgroundIntegrityVerifier] Attempt {attempt}/{max_retries} "
            f"failed: {error_msg}"
        )

    # 모든 재시도 실패 → 실제 위반으로 확정
    logger.critical(
        f"[BackgroundIntegrityVerifier] All {max_retries} attempts failed. "
        f"Confirming integrity violation: {last_error}"
    )
    return False, last_error
```

---

### 11.8 [R5-C] Fail-Open 설정 주입

**문제**: PostRecoveryIntegrityGate의 Fail-Open/Secure 모드가 설정에서 주입되어야 함.

**코드 근거**:
- `continuous_audit.py` L93: `fail_open: bool = True` — 생성자 파라미터 패턴
- `bus.py` L730-738: `get_runtime_config_manager()._get_config(...)` — 핸들러 함수 내 설정 로드 패턴
- Gate는 EventBus 핸들러 **함수**(클래스 아님)이므로 생성자 방식 불가

**결정**: 기존 핸들러 함수의 설정 로드 패턴(`bus.py` L730) 따라 `get_audit_integrity_settings()` 사용.

**수정 코드 — `integrity_gate.py`의 `on_circuit_breaker_closed_integrity_gate` 수정분**:

```python
def on_circuit_breaker_closed_integrity_gate(event) -> None:
    """CB 복구 시 무결성 게이트."""
    service_name = event.data.get("service_name", "unknown")
    start = time.time()

    # ★ R5-C: 설정에서 Fail-Open/Secure 모드 로드
    # bus.py L730 패턴: 핸들러 함수 내 설정 로드
    try:
        from selfhealing.settings.audit_integrity import get_audit_integrity_settings
        fail_open = get_audit_integrity_settings().integrity_gate_fail_open
    except Exception:
        fail_open = True  # 설정 로드 실패 시 안전한 기본값

    logger.info(
        f"[IntegrityGate] Checking WAL integrity for {service_name} "
        f"before replay (fail_open={fail_open})"
    )

    try:
        result = _verify_recovery_window_integrity(service_name, event)
        duration_ms = (time.time() - start) * 1000

        event.data[INTEGRITY_GATE_KEY] = {
            "valid": result["valid"],
            "checked": result.get("checked", 0),
            "duration_ms": duration_ms,
            "strategy": result.get("strategy", "full_chain"),
        }

        if not result["valid"]:
            event.data[INTEGRITY_FAILED_KEY] = True
            logger.critical(
                f"[IntegrityGate] INTEGRITY VIOLATION for {service_name}! "
                f"Replay will be BLOCKED. errors={result.get('errors', [])}"
            )
            _send_integrity_violation_alert(service_name, result, duration_ms)
        else:
            event.data[INTEGRITY_FAILED_KEY] = False
            logger.info(
                f"[IntegrityGate] Integrity OK for {service_name} "
                f"({result.get('checked', 0)} entries, {duration_ms:.1f}ms)"
            )

        _update_health_score(result, duration_ms)

    except Exception as e:
        # ★ R5-C: fail_open 설정에 따라 분기
        if fail_open:
            logger.warning(
                f"[IntegrityGate] Gate check failed for {service_name}: {e}. "
                f"Proceeding with Fail-Open policy."
            )
            event.data[INTEGRITY_FAILED_KEY] = False
        else:
            logger.critical(
                f"[IntegrityGate] Gate check failed for {service_name}: {e}. "
                f"Fail-Secure: BLOCKING replay. (PCI-DSS mode)"
            )
            event.data[INTEGRITY_FAILED_KEY] = True

        event.data[INTEGRITY_GATE_KEY] = {
            "valid": None,
            "error": str(e),
            "policy": "fail_open" if fail_open else "fail_secure",
        }
```

---

### 11.9 [A1] WAL API 메서드명 수정

**문제**: §3.3의 `_get_unsynced_wal_entries`가 `wal.read_unprocessed()`를 호출하지만, 이 메서드는 존재하지 않음.

**코드 근거**:
- `wal.py` L776: 실제 메서드명은 `recover_unprocessed(last_processed_seq: int = 0)`
- `wal.py` 전체에서 `read_unprocessed` grep: **0건**
- `recover_unprocessed()`는 `WALEntry` 객체 리스트를 반환하며, 각 엔트리는 `.data` 속성(dict)을 가짐 (`wal.py` L80: `WALEntry.data: dict[str, Any]`)

**수정 코드 — `integrity_gate.py`의 `_get_unsynced_wal_entries` 수정분**:

```python
def _get_unsynced_wal_entries(service_name: str) -> list[dict]:
    """
    WAL에서 아직 동기화되지 않은 엔트리 조회.

    ★ A1 수정: wal.recover_unprocessed() 사용 (read_unprocessed 미존재)

    코드 근거:
    - wal.py L776: recover_unprocessed(last_processed_seq) API
    - wal.py L80: WALEntry.data — dict[str, Any] 필드
    """
    try:
        from selfhealing.services.audit.base import _get_wal

        wal = _get_wal()
        if wal is None:
            return []

        # ★ A1 수정: recover_unprocessed (wal.py L776)
        # last_processed_seq=0 → 전체 미처리 엔트리 수집
        wal_entries = wal.recover_unprocessed(last_processed_seq=0)
        # WALEntry.data 필드 추출 (wal.py L80)
        return [e.data for e in wal_entries if hasattr(e, 'data')]

    except Exception as e:
        logger.warning(f"[IntegrityGate] WAL read failed: {e}")
        return []
```

---

### 11.10 수정된 파일 영향도 (갱신)

**신규 파일** (§7 갱신):

| 파일 | 역할 | 크기 (추정) |
|------|------|------------|
| `selfhealing/tasks/integrity_tasks.py` | BackgroundIntegrityVerifier + Beat Schedule + Retry + Lock | ~220 LOC |
| `selfhealing/services/event_bus/integrity_gate.py` | PostRecoveryIntegrityGate + Fail-Open Config | ~230 LOC |
| `selfhealing/audit/integrity/merkle_spot_checker.py` | MerkleSpotChecker (시퀀스 기반) + Canonical Serialization | ~290 LOC |

**기존 파일 수정** (§7.1 갱신):

| 파일 | 변경 | 리뷰 항목 |
|------|------|----------|
| `services/event_bus/bus.py` | `INTEGRITY_FAILED_KEY` import + 게이트 체크 + docstring 보강 | R1 |
| `settings/audit_integrity.py` | 5개 필드 추가 (§6.1과 동일) | — |
| `tasks/cascade_cleanup_tasks.py` | 머클 빌드 추가 (§4.4와 동일) | — |
| `audit/integrity/__init__.py` | `MerkleSpotChecker` + `canonical_json_bytes` export 추가 | R5-A |
| `audit/integrity/models.py` | `canonical_json_bytes()` 함수 추가 (기존 `compute_hash` 변경 없음) | R5-A |

**신규 식별자 네이밍 충돌 검증** (모두 시스템 미존재 확인 ✅):

| 식별자 | 유형 | 검증 결과 |
|--------|------|----------|
| `INTEGRITY_GATE_KEY` | 상수 | ✅ |
| `INTEGRITY_FAILED_KEY` | 상수 | ✅ |
| `canonical_json_bytes` | 함수 | ✅ |
| `_verify_with_retry` | 함수 | ✅ |
| `selfhealing:integrity:background_verify_lock` | Redis 키 | ✅ |
| `BLOCK_MERKLE_ROOT_KEY` | 상수 | ✅ |

---

### 11.11 리뷰 반영 후 추가 테스트 케이스

기존 §10 테스트에 추가할 케이스:

```python
# §10.1 PostRecoveryIntegrityGate — 추가 케이스
class TestPostRecoveryIntegrityGate:
    # ... 기존 5개 ...

    def test_fail_secure_mode_blocks_on_gate_error(self):
        """R5-C: integrity_gate_fail_open=False → 게이트 오류 시 리플레이 차단."""

    def test_settings_injection_from_audit_integrity(self):
        """R5-C: get_audit_integrity_settings()에서 fail_open 로드 확인."""

    def test_wal_recover_unprocessed_called(self):
        """A1: recover_unprocessed() 호출 확인 (read_unprocessed 아님)."""

    def test_constant_key_imported_not_hardcoded(self):
        """R1: INTEGRITY_FAILED_KEY가 integrity_gate.py에서 import되는지 확인."""


# §10.2 BackgroundIntegrityVerifier — 추가 케이스
class TestBackgroundIntegrityVerifier:
    # ... 기존 4개 ...

    def test_redis_lock_prevents_concurrent_execution(self):
        """R3: 동시 실행 시 두 번째 태스크가 skip되는지 확인."""

    def test_verify_with_retry_succeeds_on_second_attempt(self):
        """R5-B: 첫 시도 실패 + 두 번째 리로드 후 성공."""

    def test_verify_with_retry_confirms_violation_after_3_failures(self):
        """R5-B: 3회 모두 실패 시 위반 확정."""

    def test_merkle_threshold_from_settings(self):
        """R3: settings.background_verify_merkle_threshold 기반 전략 분기."""


# §10.3 MerkleSpotChecker — 추가 케이스
class TestMerkleSpotChecker:
    # ... 기존 6개 ...

    def test_sequence_based_block_split(self):
        """R2: 시퀀스 갭이 있어도 블록 ID가 안정적인지 확인."""

    def test_deleted_entry_does_not_affect_other_blocks(self):
        """R2: 중간 엔트리 삭제 시 다른 블록의 루트가 변하지 않는지 확인."""

    def test_canonical_json_bytes_used_for_leaf(self):
        """R5-A: canonical_json_bytes() 사용 확인."""

    def test_ttl_from_settings_not_hardcoded(self):
        """R4: anchor_retention_days 설정 참조 확인."""
```

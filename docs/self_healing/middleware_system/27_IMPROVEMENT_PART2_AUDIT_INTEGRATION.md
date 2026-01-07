# Part 2: Audit Integration 개선 구현 가이드

**문서 버전**: 1.0.0  
**작성일**: 2026-01-07  
**근거 코드**: 실제 소스 코드 분석 기반

---

## 1. 현황 분석

### 1.1 현재 AuditEventType 정의

[event_buffer.py](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py#L46-120) 파일에서 확인된 현재 이벤트 유형:

```python
# event_buffer.py L46-120
class AuditEventType(Enum):
    """Audit 이벤트 유형."""
    
    # DLQ 관련
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    DLQ_ESCALATE = "dlq_escalate"
    
    # Circuit Breaker 관련
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    CB_REJECTION = "circuit_breaker_rejection"
    CB_RECOVERY = "circuit_breaker_recovery"
    
    # Governance 관련
    GOVERNANCE_BLOCKED = "governance_blocked"
    GOVERNANCE_KILL_SWITCH = "governance_kill_switch"
    
    # Rate Limit 관련
    RATE_LIMITED = "rate_limited"
    
    # Pool Circuit Breaker 관련
    POOL_CB_REJECTION = "pool_circuit_breaker_rejection"
    POOL_CB_STATE_CHANGE = "pool_circuit_breaker_state_change"
    
    # 에러 및 시스템 관련
    ERROR_DETECTED = "error_detected"
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"
    
    # 복구 관련
    RECOVERY_EVENT = "recovery_event"
    RECOVERY_CHAIN_STARTED = "recovery_chain_started"
    RECOVERY_CHAIN_COMPLETED = "recovery_chain_completed"
    
    # 재시도 관련
    RETRY_ATTEMPTED = "retry_attempted"
    RETRY_EXHAUSTED = "retry_exhausted"
    
    # Chaos 실험 관련
    CHAOS_EXPERIMENT_STARTED = "chaos_experiment_started"
    CHAOS_EXPERIMENT_COMPLETED = "chaos_experiment_completed"
    CHAOS_INJECTION_APPLIED = "chaos_injection_applied"
    CHAOS_ROLLBACK_TRIGGERED = "chaos_rollback_triggered"
    
    # Emergency Mode 관련
    EMERGENCY_MODE_ACTIVATED = "emergency_mode_activated"
    EMERGENCY_MODE_DEACTIVATED = "emergency_mode_deactivated"
    
    # Error Budget 관련
    ERROR_BUDGET_DEPLETED = "error_budget_depleted"
    ERROR_BUDGET_BLOCKED = "error_budget_blocked"
    
    # Compliance 관련
    COMPLIANCE_VIOLATION = "compliance_violation"
    COMPLIANCE_CHECK_PASSED = "compliance_check_passed"
    
    # Blast Radius 관련
    BLAST_RADIUS_ISOLATION = "blast_radius_isolation"
    BLAST_RADIUS_VIOLATION = "blast_radius_violation"
    
    # FinOps 관련
    FINOPS_THRESHOLD_EXCEEDED = "finops_threshold_exceeded"
    FINOPS_BUDGET_EXCEEDED = "finops_budget_exceeded"
    
    # 데이터 접근
    DATA_ACCESS = "data_access"
    
    # 일반
    GENERIC = "generic"
```

### 1.2 Audit 미연결 서비스 분석

#### Gap 1: CorruptionShield → Audit 미연결

[shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py#L205-240) 분석:

```python
# shield.py L205-240
def _maybe_create_security_incident(self, data: dict, result: ValidationResult) -> None:
    """Create security incident for critical violations."""
    critical_violations = [
        v for v in result.violations
        if v.severity == "critical"
    ]
    
    if not critical_violations:
        return
    
    # ⚠️ SecurityViolationService만 호출
    # ⚠️ RequestAuditBuffer에 기록하지 않음
    try:
        from selfhealing.services.security_violation_service import (
            SecurityViolationService,
        )
        
        service = SecurityViolationService()
        
        for violation in critical_violations:
            service.record_violation(
                violation_type=f"corruption_{violation.code}",
                details={...},
            )
    except Exception as e:
        logger.warning(f"[CorruptionShield] Failed to create security incident: {e}")
```

**누락된 Audit 이벤트**:
- `CORRUPTION_DETECTED` - L1/L2/L3 위반 발견 시
- `CORRUPTION_BLOCKED` - 위반으로 요청 차단 시

#### Gap 2: ShadowLogger → Audit 미연결

[shadow_logger.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py#L85-110) 분석:

```python
# shadow_logger.py L85-110
def record_sync_failure(
    self,
    service_name: str,
    intended_state: str,
    error: Exception,
    adapter_type: str = "unknown",
    operation: str = "sync",
) -> None:
    """L2 동기화 실패 기록."""
    with self._lock:
        record = L2SyncFailureRecord(...)
        self._failure_log.append(record)
        
        # ⚠️ 로거에만 기록
        # ⚠️ Audit 시스템에 기록하지 않음
        logger.warning(
            f"[ShadowLog] L2 sync failed: service={service_name} "
            f"state={intended_state} adapter={adapter_type} error={error}"
        )
```

**누락된 Audit 이벤트**:
- `SHADOW_LOG_SYNC_FAILED` - L2 동기화 실패 시
- `SHADOW_LOG_RECOVERED` - L2 복구 후 재동기화 완료 시

#### Gap 3: WAL → Audit 미연결

[wal.py](../../../packages/selfhealing-python/src/selfhealing/audit/wal.py#L1-50) 분석:

```python
# wal.py - 현재 상태
class WriteAheadLog:
    """
    Write-Ahead Log with CRC32 Checksum.
    
    특징:
    - Thread-safe
    - CRC32 체크섬으로 무결성 검증
    - 파일 로테이션
    - 미처리 엔트리 복구
    """
    # ⚠️ WAL 이벤트를 Audit에 기록하지 않음
```

**누락된 Audit 이벤트**:
- `WAL_CORRUPTION_DETECTED` - CRC32 체크섬 불일치 시
- `WAL_RECOVERED` - WAL 복구 완료 시

---

## 2. 신규 AuditEventType 추가

### 2.1 추가할 이벤트 유형

```python
# event_buffer.py 추가안
class AuditEventType(Enum):
    # ... 기존 이벤트 ...
    
    # ═══════════════════════════════════════════════════════════
    # CorruptionShield 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    CORRUPTION_DETECTED = "corruption_detected"
    """데이터 무결성 위반 발견 (L1/L2/L3)."""
    
    CORRUPTION_BLOCKED = "corruption_blocked"
    """무결성 위반으로 요청 차단."""
    
    # ═══════════════════════════════════════════════════════════
    # ShadowLogger/L2 Sync 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    SHADOW_LOG_SYNC_FAILED = "shadow_log_sync_failed"
    """L2 동기화 실패 기록."""
    
    SHADOW_LOG_RECOVERED = "shadow_log_recovered"
    """L2 복구 후 재동기화 완료."""
    
    # ═══════════════════════════════════════════════════════════
    # WAL 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    WAL_CORRUPTION_DETECTED = "wal_corruption_detected"
    """WAL CRC32 체크섬 불일치 발견."""
    
    WAL_RECOVERED = "wal_recovered"
    """WAL 미처리 엔트리 복구 완료."""
    
    WAL_ROTATED = "wal_rotated"
    """WAL 파일 로테이션 발생."""
    
    # ═══════════════════════════════════════════════════════════
    # Forensic 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    FORENSIC_CAPTURE_STARTED = "forensic_capture_started"
    """Forensic 캡처 시작."""
    
    FORENSIC_CAPTURE_COMPLETED = "forensic_capture_completed"
    """Forensic 캡처 완료."""
    
    FORENSIC_ANOMALY_DETECTED = "forensic_anomaly_detected"
    """Forensic 분석 중 이상 패턴 발견."""
```

---

## 3. 서비스별 Audit 통합 구현

### 3.1 CorruptionShield Audit 통합

#### 수정 위치
[shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py)

#### 구현 코드

```python
# shield.py 수정안

class CorruptionShield:
    
    def validate(
        self,
        data: dict,
        context: Optional[dict] = None,
        block_on_violation: bool = True,
        request = None,  # ✅ 신규: Django request 객체
    ) -> ValidationResult:
        """Validate data through all enabled layers."""
        # ... 기존 검증 로직 ...
        
        result = ValidationResult(
            is_valid=is_valid,
            violations=all_violations,
            blocked=blocked,
            # ...
        )
        
        # ✅ 신규: Audit 이벤트 기록
        if not is_valid:
            self._record_audit_event(result, data, request)
        
        return result
    
    def _record_audit_event(
        self, 
        result: ValidationResult, 
        data: dict,
        request = None,
    ) -> None:
        """
        Corruption 이벤트를 Audit 시스템에 기록.
        
        request가 있으면 RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 처리)
        request가 없으면 직접 로깅
        """
        # Phase 3 패턴: 버퍼 우선
        if request is not None:
            try:
                from selfhealing.audit.event_buffer import (
                    RequestAuditBuffer, 
                    AuditEventType
                )
                
                buffer = RequestAuditBuffer.get_or_create(request)
                
                for violation in result.violations:
                    # 이벤트 유형 결정
                    if result.blocked:
                        event_type = AuditEventType.CORRUPTION_BLOCKED
                    else:
                        event_type = AuditEventType.CORRUPTION_DETECTED
                    
                    buffer.add(
                        event_type=event_type,
                        source="CorruptionShield",
                        details={
                            "layer": violation.layer,
                            "code": violation.code,
                            "message": violation.message,
                            "field": violation.field,
                            "severity": violation.severity,
                            "blocked": result.blocked,
                        },
                        success=False,
                        error_message=violation.message,
                    )
                return
            except ImportError:
                pass  # event_buffer 미사용 환경
        
        # Fallback: 직접 로깅
        for violation in result.violations:
            logger.warning(
                f"[CorruptionShield/Audit] {violation.layer} violation: "
                f"{violation.code} - {violation.message}"
            )
```

### 3.2 ShadowLogger Audit 통합

#### 수정 위치
[shadow_logger.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py)

#### 구현 코드

```python
# shadow_logger.py 수정안

class ShadowLogger:
    
    def record_sync_failure(
        self,
        service_name: str,
        intended_state: str,
        error: Exception,
        adapter_type: str = "unknown",
        operation: str = "sync",
    ) -> None:
        """L2 동기화 실패 기록."""
        with self._lock:
            record = L2SyncFailureRecord(...)
            self._failure_log.append(record)
            
            # ✅ 신규: Audit 시스템에 기록
            self._record_audit_event(
                event_type="SHADOW_LOG_SYNC_FAILED",
                service_name=service_name,
                details={
                    "intended_state": intended_state,
                    "error_message": str(error),
                    "adapter_type": adapter_type,
                    "operation": operation,
                },
            )
            
            # 기존 로깅 유지
            logger.warning(
                f"[ShadowLog] L2 sync failed: service={service_name} "
                f"state={intended_state} adapter={adapter_type} error={error}"
            )
    
    def mark_as_synced(self, service_name: str) -> int:
        """복구 후 동기화 완료 마킹."""
        count = 0
        with self._lock:
            for record in self._failure_log:
                if record.service_name == service_name and not record.synced_after_recovery:
                    record.synced_after_recovery = True
                    record.recovery_time = datetime.now(timezone.utc)
                    count += 1
            
            # ✅ 신규: 복구 완료 Audit 기록
            if count > 0:
                self._record_audit_event(
                    event_type="SHADOW_LOG_RECOVERED",
                    service_name=service_name,
                    details={
                        "recovered_count": count,
                        "recovery_time": datetime.now(timezone.utc).isoformat(),
                    },
                )
        
        return count
    
    def _record_audit_event(
        self,
        event_type: str,
        service_name: str,
        details: dict,
    ) -> None:
        """Audit 이벤트 기록."""
        try:
            from selfhealing.factory import ProviderRegistry
            
            adapter = ProviderRegistry.get_audit_adapter()
            if adapter:
                adapter.log_event(
                    event_type=event_type,
                    source="ShadowLogger",
                    details={
                        "service_name": service_name,
                        **details,
                    },
                )
        except Exception as e:
            # Audit 실패가 메인 로직을 방해하면 안됨
            logger.debug(f"[ShadowLogger] Audit recording failed: {e}")
```

### 3.3 WAL Audit 통합

#### 수정 위치
[wal.py](../../../packages/selfhealing-python/src/selfhealing/audit/wal.py)

#### 구현 코드

```python
# wal.py 수정안

class WriteAheadLog:
    
    def __init__(
        self,
        config: Optional[WALConfig] = None,
        on_rotate: Optional[Callable[[str], None]] = None,
        on_corruption: Optional[Callable[[WALCorruptionError], None]] = None,
        audit_adapter = None,  # ✅ 신규: Audit 어댑터
    ):
        self._config = config or WALConfig()
        self._on_rotate = on_rotate
        self._on_corruption = on_corruption
        self._audit_adapter = audit_adapter
        # ... 기존 초기화 ...
    
    def _verify_checksum(self, entry: WALEntry) -> bool:
        """CRC32 체크섬 검증."""
        computed = self._compute_checksum(entry.data)
        if computed != entry.checksum:
            # ✅ 신규: Corruption 발견 시 Audit 기록
            self._record_audit_event(
                event_type="WAL_CORRUPTION_DETECTED",
                details={
                    "sequence": entry.sequence,
                    "expected_checksum": entry.checksum,
                    "computed_checksum": computed,
                    "timestamp": entry.timestamp,
                },
            )
            
            # 기존 콜백 호출
            if self._on_corruption:
                error = WALCorruptionError(
                    f"Checksum mismatch at seq {entry.sequence}",
                    entry.sequence,
                    entry.checksum,
                    computed,
                )
                self._on_corruption(error)
            
            return False
        return True
    
    def recover_unprocessed(self, last_processed_seq: int) -> List[WALEntry]:
        """미처리 엔트리 복구."""
        entries = []
        # ... 복구 로직 ...
        
        if entries:
            # ✅ 신규: 복구 완료 Audit 기록
            self._record_audit_event(
                event_type="WAL_RECOVERED",
                details={
                    "recovered_count": len(entries),
                    "last_processed_seq": last_processed_seq,
                    "new_last_seq": entries[-1].sequence if entries else last_processed_seq,
                },
            )
        
        return entries
    
    def _rotate_file(self) -> None:
        """WAL 파일 로테이션."""
        # ... 기존 로직 ...
        
        # ✅ 신규: 로테이션 Audit 기록
        self._record_audit_event(
            event_type="WAL_ROTATED",
            details={
                "old_file": old_file_path,
                "new_file": new_file_path,
                "old_size_bytes": old_size,
            },
        )
    
    def _record_audit_event(self, event_type: str, details: dict) -> None:
        """Audit 이벤트 기록."""
        if self._audit_adapter:
            try:
                self._audit_adapter.log_event(
                    event_type=event_type,
                    source="WriteAheadLog",
                    details=details,
                )
            except Exception as e:
                # Audit 실패가 WAL 동작을 방해하면 안됨
                pass
```

---

## 4. Forensic-Audit 연결 강화

### 4.1 현재 ForensicSettings 분석

[config.py](../../../packages/selfhealing-python/src/selfhealing/core/config.py) 내 ForensicSettings:

```python
# config.py (ForensicSettings 위치)
@dataclass
class ForensicSettings:
    """Forensic 관련 설정."""
    max_stack_depth: int = 10
    max_context_size: int = 1024
    sensitive_fields: List[str] = field(default_factory=lambda: [
        "password", "secret", "token", "api_key", "credit_card"
    ])
    enable_memory_snapshot: bool = False
    snapshot_interval_seconds: int = 60
```

### 4.2 Forensic Audit 연결 구현

```python
# forensic_audit_bridge.py (신규 파일)
"""
Forensic-Audit 브릿지 모듈.

Forensic 캡처 이벤트를 Audit 시스템에 연결합니다.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class ForensicAuditBridge:
    """
    Forensic 캡처를 Audit 시스템에 연결.
    
    Forensic 캡처 시점:
    1. 예외 발생 시 스택 트레이스 캡처
    2. 주기적 메모리 스냅샷
    3. 이상 패턴 감지 시 컨텍스트 캡처
    """
    
    def __init__(self, audit_adapter=None):
        self._audit_adapter = audit_adapter
    
    def on_exception_captured(
        self,
        exception: Exception,
        stack_trace: str,
        context: Dict[str, Any],
        sanitized: bool = True,
    ) -> None:
        """
        예외 캡처 시 Audit 기록.
        
        Args:
            exception: 캡처된 예외
            stack_trace: 스택 트레이스 (ForensicSettings.max_stack_depth 적용됨)
            context: 실행 컨텍스트 (민감 정보 마스킹됨)
            sanitized: 민감 정보 마스킹 여부
        """
        self._record_audit(
            event_type="FORENSIC_CAPTURE_COMPLETED",
            details={
                "exception_type": type(exception).__name__,
                "exception_message": str(exception)[:500],
                "stack_depth": len(stack_trace.split("\n")),
                "context_keys": list(context.keys()),
                "sanitized": sanitized,
                "capture_reason": "exception",
            },
        )
    
    def on_anomaly_detected(
        self,
        anomaly_type: str,
        score: float,
        threshold: float,
        context: Dict[str, Any],
    ) -> None:
        """
        이상 패턴 감지 시 Audit 기록.
        
        Args:
            anomaly_type: 이상 유형 (statistical, behavioral 등)
            score: 이상 점수
            threshold: 감지 임계값
            context: 관련 컨텍스트
        """
        self._record_audit(
            event_type="FORENSIC_ANOMALY_DETECTED",
            details={
                "anomaly_type": anomaly_type,
                "score": score,
                "threshold": threshold,
                "exceeded_by": score - threshold,
                "context_summary": self._summarize_context(context),
            },
        )
    
    def on_memory_snapshot(
        self,
        snapshot_id: str,
        memory_mb: float,
        object_count: int,
    ) -> None:
        """
        메모리 스냅샷 시 Audit 기록 (설정에서 활성화된 경우).
        """
        self._record_audit(
            event_type="FORENSIC_CAPTURE_STARTED",
            details={
                "capture_type": "memory_snapshot",
                "snapshot_id": snapshot_id,
                "memory_mb": memory_mb,
                "object_count": object_count,
            },
        )
    
    def _summarize_context(self, context: Dict[str, Any]) -> Dict[str, str]:
        """컨텍스트 요약 (크기 제한)."""
        summary = {}
        for key, value in context.items():
            value_str = str(value)
            if len(value_str) > 100:
                summary[key] = f"{value_str[:100]}... (truncated)"
            else:
                summary[key] = value_str
        return summary
    
    def _record_audit(self, event_type: str, details: Dict[str, Any]) -> None:
        """Audit 시스템에 기록."""
        if self._audit_adapter:
            try:
                self._audit_adapter.log_event(
                    event_type=event_type,
                    source="ForensicCapture",
                    details=details,
                )
            except Exception as e:
                logger.debug(f"[ForensicAuditBridge] Audit recording failed: {e}")
```

---

## 5. 테스트 계획

### 5.1 단위 테스트

```python
# tests/self_healing/unit/test_corruption_audit_integration.py

import pytest
from unittest.mock import MagicMock, patch

class TestCorruptionShieldAuditIntegration:
    """CorruptionShield Audit 통합 테스트."""
    
    def test_corruption_detected_recorded_to_audit(self):
        """L1/L2/L3 위반 발견 시 Audit에 기록."""
        from selfhealing.services.corruption_shield import get_corruption_shield
        
        shield = get_corruption_shield()
        
        # Invalid data
        data = {"amount": -1000}  # 음수 금액 (L2 위반)
        
        with patch("selfhealing.audit.event_buffer.RequestAuditBuffer") as mock_buffer:
            mock_request = MagicMock()
            result = shield.validate(data, request=mock_request)
            
            # Audit 버퍼에 기록 확인
            if not result.is_valid:
                mock_buffer.get_or_create.assert_called_once_with(mock_request)
    
    def test_corruption_blocked_recorded_to_audit(self):
        """위반으로 차단 시 CORRUPTION_BLOCKED 이벤트 기록."""
        # 테스트 구현
        pass


class TestShadowLoggerAuditIntegration:
    """ShadowLogger Audit 통합 테스트."""
    
    def test_sync_failure_recorded_to_audit(self):
        """L2 동기화 실패 시 Audit에 기록."""
        # 테스트 구현
        pass
    
    def test_recovery_recorded_to_audit(self):
        """복구 완료 시 Audit에 기록."""
        # 테스트 구현
        pass


class TestWALAuditIntegration:
    """WAL Audit 통합 테스트."""
    
    def test_corruption_detected_recorded_to_audit(self):
        """CRC32 불일치 시 Audit에 기록."""
        # 테스트 구현
        pass
    
    def test_recovery_recorded_to_audit(self):
        """복구 완료 시 Audit에 기록."""
        # 테스트 구현
        pass
```

---

## 6. 영향 분석

### 6.1 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `audit/event_buffer.py` | 수정 | 신규 AuditEventType 추가 |
| `services/corruption_shield/shield.py` | 수정 | Audit 통합 |
| `adapters/memory/shadow_logger.py` | 수정 | Audit 통합 |
| `audit/wal.py` | 수정 | Audit 통합 |
| `services/forensic_audit_bridge.py` | 신규 | Forensic-Audit 브릿지 |

### 6.2 하위 호환성

- **기존 API 유지**: 모든 public 메서드 시그니처 변경 없음
- **Optional 파라미터**: `request` 파라미터는 선택적
- **Fail-safe**: Audit 기록 실패 시 메인 로직 영향 없음

---

## 7. 구현 우선순위

| 순위 | 작업 | 중요도 | 예상 공수 |
|------|------|--------|----------|
| 1 | AuditEventType 신규 추가 | 🔴 Critical | 1시간 |
| 2 | CorruptionShield Audit 통합 | 🔴 Critical | 2시간 |
| 3 | ShadowLogger Audit 통합 | 🟡 High | 1.5시간 |
| 4 | WAL Audit 통합 | 🟡 High | 1.5시간 |
| 5 | ForensicAuditBridge 구현 | 🟢 Medium | 2시간 |
| 6 | 단위 테스트 작성 | 🟢 Medium | 3시간 |

**총 예상 공수**: 11시간

---

## 8. 관련 문서

- [event_buffer.py](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py) - Audit 이벤트 버퍼
- [shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py) - CorruptionShield
- [shadow_logger.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py) - ShadowLogger
- [wal.py](../../../packages/selfhealing-python/src/selfhealing/audit/wal.py) - Write-Ahead Log
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - Audit 통합 계획

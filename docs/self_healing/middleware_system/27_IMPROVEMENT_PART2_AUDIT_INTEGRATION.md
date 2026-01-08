# Part 2: Audit Integration 개선 구현 가이드

**문서 버전**: 1.3.0  
**작성일**: 2026-01-07  
**최종 수정일**: 2026-01-08  
**구현 상태**: 🟢 Phase 1,2 완료  
**근거 코드**: 실제 소스 코드 분석 기반

---

## 구현 완료 요약

| 항목 | 상태 | 파일 |
|------|------|------|
| AuditEventType 신규 추가 (10개) | ✅ 완료 | `event_buffer.py` |
| CorruptionShield Audit 통합 | ✅ 완료 | `shield.py` |
| ShadowLogger Audit 통합 | ✅ 완료 | `shadow_logger.py` |
| WAL Audit 통합 | ✅ 완료 | `wal.py` |
| ForensicAuditBridge | ✅ 완료 | `forensic_audit_bridge.py` |
| 단위 테스트 | ✅ 완료 | `test_audit_integration_part2.py` |

### 리뷰 피드백 반영 현황

| 항목 | 상태 | 파일 |
|------|------|------|
| CorruptionShield 배칭 (Batching) | ✅ 완료 | `shield.py` |
| ActorContext/TraceContext 자동 결합 | ✅ 완료 | `shadow_logger.py`, `wal.py` |
| Forensic 민감정보 마스킹 연동 | 🟡 계획됨 | `forensic_audit_bridge.py` |
| InMemoryAuditBuffer 폴백 | 🟡 계획됨 | `resilience.py`, `base.py` |
| Forensic Rate Limiter (SlidingWindow) | 🟡 계획됨 | `forensic_audit_bridge.py` |
| RedisAuditBuffer 분산 버퍼 | 🟡 계획됨 | `redis_buffer.py` (신규) |
| MTTR Calculator | 🟡 계획됨 | `mttr_calculator.py` (신규) |

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

| 순위 | 작업 | 중요도 | 상태 |
|------|------|--------|------|
| 1 | AuditEventType 신규 추가 | 🔴 Critical | ✅ 완료 |
| 2 | CorruptionShield Audit 통합 | 🔴 Critical | ✅ 완료 |
| 3 | ShadowLogger Audit 통합 | 🟡 High | ✅ 완료 |
| 4 | WAL Audit 통합 | 🟡 High | ✅ 완료 |
| 5 | ForensicAuditBridge 구현 | 🟢 Medium | ✅ 완료 |
| 6 | 단위 테스트 작성 | 🟢 Medium | ✅ 완료 |

**구현 완료일**: 2026-01-07

---

## 8. 리뷰 피드백 및 보완 계획

### 8.1 Architect's Review (설계 리뷰)

#### 8.1.1 CorruptionShield 배칭(Batching) 권장

**리뷰 내용**:
> 현재 CorruptionShield는 위반 사항마다 개별 Audit 이벤트를 생성합니다. 10개 필드 위반 시 10개 로그 발생.

**분석 결과**: ✅ 타당한 지적

현재 [shield.py#L217-L244](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py#L217-L244):
```python
for violation in result.violations:
    buffer.add(event_type=..., details={...})  # 개별 이벤트
```

**보완 구현안**: 단일 이벤트에 violations 리스트 포함

```python
def _record_audit_event(self, result: ValidationResult, data: dict, request=None):
    if request is not None:
        buffer = RequestAuditBuffer.get_or_create(request)
        
        # ✅ 배칭: 단일 이벤트에 모든 violations 포함
        event_type = (AuditEventType.CORRUPTION_BLOCKED 
                      if result.blocked else AuditEventType.CORRUPTION_DETECTED)
        
        buffer.add(
            event_type=event_type,
            source="CorruptionShield",
            details={
                "violation_count": len(result.violations),
                "blocked": result.blocked,
                "violations": [
                    {
                        "layer": v.layer,
                        "code": v.code,
                        "message": v.message,
                        "field": v.field,
                        "severity": v.severity,
                    }
                    for v in result.violations
                ],
                "layers": {
                    "l1_passed": result.l1_passed,
                    "l2_passed": result.l2_passed,
                    "l3_passed": result.l3_passed,
                },
            },
        )
```

**장점**:
- 로그 양 획기적 감소
- 하나의 '사건(Request)'에 대한 모든 위반 정보 한눈에 파악

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `shield.py` |

---

#### 8.1.2 Trace ID 및 ActorContext 자동 결합

**리뷰 내용**:
> ShadowLogger나 WAL에서 log_event 호출 시 ActorContext.get_current()를 참조하여 "어떤 운영자의 어떤 작업 중에 WAL 로테이션이 발생했는지" 기록 필요.

**분석 결과**: ✅ 타당한 지적

현재 `_write_to_wal()` 함수([base.py#L72-L178](../../../packages/selfhealing-python/src/selfhealing/services/audit/base.py#L72-L178))는 **이미 ActorContext와 TraceContext를 자동 결합**:

```python
# Phase 25: ActorContext에서 actor 정보 자동 추출
from selfhealing.context.actor_context import ActorContext
if ActorContext.is_set():
    actor = ActorContext.get_current()
    actor_id = actor.actor_id
    actor_roles = actor.roles

# Phase 25: TraceContext에서 trace_id 자동 추출
from selfhealing.audit.trace import get_trace_id
trace_id = get_trace_id()
```

**문제점 발견**: 
ShadowLogger와 WAL의 `_record_audit_event()`가 `adapter.log_event()`를 호출하지만, `AuditLogAdapter` 인터페이스에 `log_event()` 메서드가 없음.

**보완 구현안**: ShadowLogger/WAL에서 `_write_to_wal()`을 직접 사용

```python
# shadow_logger.py 수정안
def _record_audit_event(self, event_type: str, service_name: str, details: Dict) -> None:
    try:
        from selfhealing.services.audit.base import _write_to_wal
        
        _write_to_wal(
            event_type=event_type,
            source="ShadowLogger",
            details={"service_name": service_name, **details},
        )
        # ✅ 자동으로 actor_id, actor_roles, trace_id가 포함됨
    except Exception as e:
        logger.debug(f"[ShadowLogger] Audit recording failed: {e}")
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `shadow_logger.py`, `wal.py` |

---

#### 8.1.3 Forensic 민감 정보 마스킹 연동

**리뷰 내용**:
> ForensicAuditBridge에서 sanitized 플래그만 있고 실제 마스킹 수행 안함. GDPR/ISMS 위반 위험.

**분석 결과**: ✅ 타당한 지적

현재 [forensic_audit_bridge.py](../../../packages/selfhealing-python/src/selfhealing/services/forensic_audit_bridge.py)에 sanitized 플래그는 있지만 실제 마스킹 로직 없음.

**이미 존재하는 마스킹 인프라**:
- `ForensicSettings.sensitive_field_patterns` ([config.py#L65-L90](../../../packages/selfhealing-python/src/selfhealing/config.py#L65-L90))
- `mask_sensitive_fields()` ([masking.py#L118-L172](../../../packages/selfhealing-python/src/selfhealing/audit/masking.py#L118-L172))

**보완 구현안**:

```python
# forensic_audit_bridge.py 수정안
def on_exception_captured(self, exception, stack_trace, context, sanitized=True):
    # ForensicSettings와 연동하여 실제 마스킹 수행
    masked_context = context
    if sanitized:
        try:
            from selfhealing.config import get_forensic_settings
            from selfhealing.audit.masking import mask_sensitive_fields
            
            settings = get_forensic_settings()
            if settings.mask_sensitive_fields:
                masked_context = mask_sensitive_fields(
                    context, 
                    list(settings.sensitive_field_patterns)
                )
        except ImportError:
            pass
    
    self._record_audit(
        event_type="FORENSIC_CAPTURE_COMPLETED",
        details={
            "context_keys": list(masked_context.keys()),
            "sanitized": sanitized,
            # ...
        },
    )
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `forensic_audit_bridge.py` |

---

### 8.2 Security Review (보안 리뷰)

#### 8.2.1 Audit 시스템 Self-Healing (메모리 버퍼 폴백)

**리뷰 내용**:
> 디스크 가득 차거나 권한 문제로 WAL 기록 실패 시, 메모리에 임시 보관 후 정상화 시 파일로 쏟아내는 로직 필요.

**분석 결과**: ✅ 매우 타당한 지적

현재 [resilience.py](../../../packages/selfhealing-python/src/selfhealing/audit/resilience.py)에 `CircuitBreaker`, `AuditMetrics`는 있지만 **메모리 버퍼 폴백은 없음**.

**보완 구현안**: `InMemoryAuditBuffer` 클래스 추가

```python
# resilience.py에 추가
class InMemoryAuditBuffer:
    """
    WAL 실패 시 메모리 폴백 버퍼.
    
    디스크 장애 시 중요 로그를 메모리에 임시 보관하고,
    시스템 정상화 시 파일로 플러시합니다.
    
    설계 원칙:
    - 최대 10,000개 엔트리 보관 (메모리 고갈 방지)
    - FIFO: 용량 초과 시 가장 오래된 엔트리 삭제
    - 주기적 플러시 시도 (30초 간격)
    """
    
    _instance = None
    _lock = threading.Lock()
    
    MAX_ENTRIES = 10_000
    FLUSH_INTERVAL_SECONDS = 30
    
    def __init__(self):
        self._buffer: List[Dict[str, Any]] = []
        self._buffer_lock = threading.RLock()
        self._last_flush_attempt: Optional[datetime] = None
        self._flush_failures: int = 0
    
    @classmethod
    def get_instance(cls) -> "InMemoryAuditBuffer":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def add(self, entry: Dict[str, Any]) -> bool:
        """
        엔트리 추가.
        
        Returns:
            True if added, False if dropped (buffer full and oldest removed)
        """
        with self._buffer_lock:
            if len(self._buffer) >= self.MAX_ENTRIES:
                # FIFO: 가장 오래된 엔트리 삭제
                self._buffer.pop(0)
                logger.warning(
                    "[InMemoryAuditBuffer] Buffer full, dropped oldest entry"
                )
            
            entry["buffered_at"] = datetime.now(timezone.utc).isoformat()
            self._buffer.append(entry)
            return True
    
    def try_flush(self, wal_write_func: Callable) -> int:
        """
        버퍼를 WAL로 플러시 시도.
        
        Returns:
            플러시된 엔트리 수
        """
        with self._buffer_lock:
            if not self._buffer:
                return 0
            
            flushed = 0
            remaining = []
            
            for entry in self._buffer:
                try:
                    wal_write_func(**entry)
                    flushed += 1
                except Exception:
                    remaining.append(entry)
            
            self._buffer = remaining
            self._last_flush_attempt = datetime.now(timezone.utc)
            
            if flushed > 0:
                logger.info(f"[InMemoryAuditBuffer] Flushed {flushed} entries")
            
            return flushed
    
    def get_stats(self) -> Dict[str, Any]:
        with self._buffer_lock:
            return {
                "buffered_entries": len(self._buffer),
                "max_entries": self.MAX_ENTRIES,
                "last_flush_attempt": (
                    self._last_flush_attempt.isoformat() 
                    if self._last_flush_attempt else None
                ),
                "flush_failures": self._flush_failures,
            }
```

**_write_to_wal() 수정안**:

```python
# base.py 수정
def _write_to_wal(...) -> Optional[int]:
    wal = _get_wal()
    
    try:
        seq = wal.write(wal_entry)
        
        # ✅ 성공 시 메모리 버퍼 플러시 시도
        _try_flush_memory_buffer()
        
        return seq
    except Exception as e:
        logger.error(f"[AuditHelpers] WAL write failed: {e}")
        
        # ✅ 실패 시 메모리 버퍼에 저장
        try:
            from selfhealing.audit.resilience import InMemoryAuditBuffer
            buffer = InMemoryAuditBuffer.get_instance()
            buffer.add(wal_entry)
            logger.warning("[AuditHelpers] Entry saved to memory buffer")
        except Exception as buffer_error:
            logger.critical(f"[AuditHelpers] Memory buffer also failed: {buffer_error}")
        
        return None
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `resilience.py`, `base.py` |

---

#### 8.2.2 Forensic Rate Limiting (에러 폭풍 방지)

**리뷰 내용**:
> 초당 수천 건의 에러 발생 시 Audit 파일이 기가바이트 단위로 비대해짐. 자가 공격 방지 필요.

**분석 결과**: ✅ 매우 타당한 지적

현재 `ForensicAuditBridge`에 Rate Limiting 없음.

**알고리즘 선택: Sliding Window**

| 알고리즘 | 기존 코드베이스 용도 | 특성 |
|----------|---------------------|------|
| `SlidingWindowThrottle` | 일반 throttle ([throttle/base.py](../../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py)) | 정확한 시간 윈도우, 버스트 불허 |
| `TokenBucket` | chaos/traffic shaping ([traffic_shaper.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/traffic_shaper.py)) | 버스트 허용, 연속 트래픽 제어 |

**선택 이유**: Forensic 쿼리는 "일관된 제한"이 필요 (버스트 불허) → **SlidingWindow 채택**

**보완 구현안**: SlidingWindow Rate Limiter (기존 패턴 재사용)

```python
# forensic_audit_bridge.py 수정안
import time
import threading
from collections import defaultdict

class ForensicRateLimiter:
    """
    Forensic 이벤트 Rate Limiter.
    
    Sliding Window 알고리즘 (SlidingWindowThrottle과 동일 패턴):
    - 분당 최대 10건의 예외 캡처
    - 분당 최대 1건의 메모리 스냅샷
    - 정확한 윈도우 기반 제어 (버스트 불허)
    
    기존 코드 참조: services/throttle/base.py::SlidingWindowThrottle
    """
    
    def __init__(
        self,
        exception_limit: int = 10,
        snapshot_limit: int = 1,
        window_seconds: float = 60.0,
    ):
        self._exception_limit = exception_limit
        self._snapshot_limit = snapshot_limit
        self._window_seconds = window_seconds
        
        # Sliding Window: 타임스탬프 리스트
        self._exception_timestamps: list[float] = []
        self._snapshot_timestamps: list[float] = []
        self._lock = threading.Lock()
        
        # 통계
        self._exceptions_dropped = 0
        self._snapshots_dropped = 0
    
    def _cleanup_old_timestamps(self, timestamps: list[float], now: float) -> list[float]:
        """윈도우 밖의 오래된 타임스탬프 제거."""
        cutoff = now - self._window_seconds
        return [ts for ts in timestamps if ts > cutoff]
    
    def try_acquire_exception(self) -> bool:
        """예외 캡처 허용 여부."""
        with self._lock:
            now = time.time()
            self._exception_timestamps = self._cleanup_old_timestamps(
                self._exception_timestamps, now
            )
            
            if len(self._exception_timestamps) < self._exception_limit:
                self._exception_timestamps.append(now)
                return True
            
            self._exceptions_dropped += 1
            return False
    
    def try_acquire_snapshot(self) -> bool:
        """스냅샷 캡처 허용 여부."""
        with self._lock:
            now = time.time()
            self._snapshot_timestamps = self._cleanup_old_timestamps(
                self._snapshot_timestamps, now
            )
            
            if len(self._snapshot_timestamps) < self._snapshot_limit:
                self._snapshot_timestamps.append(now)
                return True
            
            self._snapshots_dropped += 1
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            now = time.time()
            exception_ts = self._cleanup_old_timestamps(self._exception_timestamps, now)
            snapshot_ts = self._cleanup_old_timestamps(self._snapshot_timestamps, now)
            return {
                "exception_requests_in_window": len(exception_ts),
                "exception_limit": self._exception_limit,
                "snapshot_requests_in_window": len(snapshot_ts),
                "snapshot_limit": self._snapshot_limit,
                "exceptions_dropped": self._exceptions_dropped,
                "snapshots_dropped": self._snapshots_dropped,
            }


class ForensicAuditBridge:
    def __init__(self, audit_adapter=None, rate_limiter=None):
        self._audit_adapter = audit_adapter
        self._rate_limiter = rate_limiter or ForensicRateLimiter()
    
    def on_exception_captured(self, exception, stack_trace, context, sanitized=True):
        # ✅ Rate Limiting 적용
        if not self._rate_limiter.try_acquire_exception():
            logger.debug("[ForensicAuditBridge] Exception capture rate limited")
            return
        
        # ... 기존 로직 ...
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `forensic_audit_bridge.py` |

---

#### 8.2.3 분산 환경 데이터 무결성 전략

**리뷰 내용**:
> 멀티 프로세스/멀티 인스턴스 환경에서 Audit 데이터 일관성 보장 전략 필요.

**분석 결과**: Redis 사용 전제 시 최적 전략 도출

**기존 Redis 사용 패턴** (코드 기반):

| 용도 | 구현 파일 | 패턴 |
|------|----------|------|
| CB 상태 L1/L2 Cache | [layered_repository.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/layered_repository.py) | L1 Memory + L2 Redis 비동기 동기화 |
| Pre-computed Cache | [precomputed_cache.py](../../../packages/selfhealing-python/src/selfhealing/services/precomputed_cache.py) | L1 TTLCache (2초) + L2 Redis (15초) |
| Rate Limiting | [rate_limit.py](../../../packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py) | L2 Redis + L1 Local Fallback |
| Metric Write-Through | [redis_adapter.py](../../../packages/selfhealing-python/src/selfhealing/adapters/metrics/redis_adapter.py) | 즉시 쓰기 |
| AirGap Adapter | [redis_adapter.py](../../../packages/selfhealing-python/src/selfhealing/adapters/airgap/redis_adapter.py) | 분산 상태 공유 |

**전략 비교**:

| 전략 | 분산 일관성 | 순서 보장 | 장애 복원력 | 의존성 |
|------|------------|----------|------------|--------|
| Stateless (File Write-through) | ❌ 인스턴스별 | ⚠️ 로컬만 | ✅ 우수 | ✅ 없음 |
| Redis Shared Buffer | ✅ 우수 | ✅ FIFO | ⚠️ Redis SPOF | ⚠️ Redis |
| **Redis + File Fallback** | ✅ 우수 | ✅ FIFO | ✅ 우수 | ⚠️ Redis (선택적) |
| Sidecar Log Shipper | ✅ 중앙 집계 | ⚠️ 타임스탬프 | ✅ 우수 | ✅ 별도 컨테이너 |

**권장 전략: Redis + File Fallback (기존 CB 패턴 재사용)**

```
┌─────────────────────────────────────────────────────────────────┐
│                     메인 애플리케이션                             │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  AuditService / CorruptionShield                        │   │
│  │                       │                                 │   │
│  │                       ▼                                 │   │
│  │  ┌─────────────────────────────────────────────┐        │   │
│  │  │ RedisAuditBuffer (Primary)                  │        │   │
│  │  │  - LPUSH audit:buffer:{domain}              │        │   │
│  │  │  - TTL 24시간 (자동 정리)                    │        │   │
│  │  │  - 분산 인스턴스 간 공유                     │        │   │
│  │  └──────────────────┬──────────────────────────┘        │   │
│  │                     │ Redis 장애 시                     │   │
│  │                     ▼                                   │   │
│  │  ┌─────────────────────────────────────────────┐        │   │
│  │  │ FileAuditLogAdapter (Fallback)              │        │   │
│  │  │  - 로컬 파일 즉시 쓰기                       │        │   │
│  │  │  - Redis 복구 시 WAL 동기화                  │        │   │
│  │  └─────────────────────────────────────────────┘        │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                           │
          ┌────────────────┴────────────────┐
          ▼                                 ▼
┌─────────────────────┐          ┌─────────────────────┐
│ Redis Cluster       │          │ 로컬 파일 시스템     │
│ audit:buffer:*      │          │ /var/log/audit/     │
└─────────────────────┘          └─────────────────────┘
                           │ (선택적)
                           ▼
          ┌─────────────────────────────────────┐
          │ SidecarFileWatcher (별도 컨테이너)   │
          │  - S3 Object Lock / Loki 전송       │
          │  - 기존 구현: worm_adapters.py      │
          └─────────────────────────────────────┘
```

**구현안: RedisAuditBuffer**

```python
# adapters/audit/redis_buffer.py (신규)
"""
Redis 기반 분산 Audit 버퍼.

기존 패턴 참조:
- CB Advanced Protection의 Redis-First + WAL 패턴
- RedisMetricSourceAdapter의 Write-Through 패턴
"""
import json
import logging
from typing import TYPE_CHECKING, Optional, Callable
from datetime import datetime, timezone

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


class RedisAuditBuffer:
    """
    분산 환경용 Redis Audit 버퍼.
    
    특징:
    - 모든 인스턴스가 동일한 Redis 버퍼 사용
    - LPUSH/RPOP으로 FIFO 순서 보장
    - Redis 장애 시 FileAuditLogAdapter로 자동 fallback
    - Redis 복구 시 로컬 WAL → Redis 동기화
    """
    
    DEFAULT_KEY_PREFIX = "audit:buffer:"
    DEFAULT_TTL_SECONDS = 86400  # 24시간
    
    def __init__(
        self,
        redis_client: "redis.Redis",
        fallback_adapter: Optional["AuditLogAdapter"] = None,
        key_prefix: str = DEFAULT_KEY_PREFIX,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        on_fallback: Optional[Callable[[Exception], None]] = None,
    ):
        self._redis = redis_client
        self._fallback = fallback_adapter
        self._key_prefix = key_prefix
        self._ttl_seconds = ttl_seconds
        self._on_fallback = on_fallback
        
        # 상태 추적 (CB Advanced Protection 패턴)
        self._consecutive_failures = 0
        self._max_failures_before_fallback = 3
    
    def log(self, entry: "AuditEntry", domain: str = "default") -> bool:
        """
        Audit 엔트리 기록.
        
        Returns:
            True if Redis 성공, False if fallback 사용
        """
        key = f"{self._key_prefix}{domain}"
        
        try:
            payload = {
                "entry": entry.to_dict(),
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instance_id": self._get_instance_id(),
            }
            
            pipe = self._redis.pipeline()
            pipe.lpush(key, json.dumps(payload))
            pipe.expire(key, self._ttl_seconds)
            pipe.execute()
            
            # 성공 시 failure count 리셋
            self._consecutive_failures = 0
            return True
            
        except Exception as e:
            logger.warning(f"[RedisAuditBuffer] Redis write failed: {e}")
            self._consecutive_failures += 1
            
            if self._on_fallback:
                self._on_fallback(e)
            
            # Fallback to file
            if self._fallback:
                try:
                    self._fallback.log(entry)
                    logger.info("[RedisAuditBuffer] Used file fallback")
                except Exception as fallback_error:
                    logger.error(f"[RedisAuditBuffer] Fallback also failed: {fallback_error}")
            
            return False
    
    def flush_to_external(self, target_adapter: "AuditLogAdapter", batch_size: int = 100) -> int:
        """
        Redis 버퍼를 외부 저장소로 플러시 (Sidecar/배치 작업용).
        
        Returns:
            플러시된 엔트리 수
        """
        flushed = 0
        
        for key in self._redis.scan_iter(match=f"{self._key_prefix}*"):
            while True:
                item = self._redis.rpop(key)
                if not item:
                    break
                    
                try:
                    payload = json.loads(item)
                    # AuditEntry 재구성 후 전송
                    target_adapter.log_raw(payload["entry"])
                    flushed += 1
                except Exception as e:
                    logger.error(f"[RedisAuditBuffer] Flush error: {e}")
                    # 실패 시 다시 넣기 (앞에)
                    self._redis.rpush(key, item)
                    break
        
        return flushed
    
    def get_buffer_stats(self) -> dict:
        """버퍼 상태 조회."""
        stats = {
            "consecutive_failures": self._consecutive_failures,
            "domains": {},
        }
        
        for key in self._redis.scan_iter(match=f"{self._key_prefix}*"):
            domain = key.decode().replace(self._key_prefix, "")
            stats["domains"][domain] = self._redis.llen(key)
        
        return stats
    
    def _get_instance_id(self) -> str:
        """현재 인스턴스 식별자."""
        import os
        return os.environ.get("HOSTNAME", os.environ.get("INSTANCE_ID", "unknown"))
```

**사용 예시**:

```python
# factory.py에서 설정
import redis
from selfhealing.adapters.audit.redis_buffer import RedisAuditBuffer
from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter

def create_audit_adapter():
    # File fallback 준비
    file_fallback = FileAuditLogAdapter(log_dir="/var/log/audit")
    
    try:
        redis_client = redis.from_url(settings.REDIS_URL)
        redis_client.ping()  # 연결 확인
        
        return RedisAuditBuffer(
            redis_client=redis_client,
            fallback_adapter=file_fallback,
            on_fallback=lambda e: logger.warning(f"Redis audit fallback: {e}"),
        )
    except Exception:
        # Redis 없으면 파일만 사용
        logger.info("Redis unavailable, using file-only audit")
        return file_fallback
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `adapters/audit/redis_buffer.py` (신규) |

---

### 8.3 Business Value Review (비즈니스 가치 리뷰)

#### 8.3.1 MTTR 자동 계산 대시보드 쿼리

**리뷰 내용**:
> "우리는 장애를 이만큼 빨리 복구하며, 이를 시스템적으로 증명한다"가 제품의 진짜 가격.

**분석 결과**: ✅ 매우 타당하며 비즈니스 가치 높음

현재 CB_STATE_CHANGE 이벤트가 기록되므로 MTTR 계산 가능.

**Grafana/Loki 쿼리 예시**:

```logql
# MTTR 계산을 위한 LogQL 쿼리

# 1. 서킷 브레이커 OPEN → CLOSED 전환 이벤트 추출
{app="selfhealing"} 
  |= "CB_STATE_CHANGE" 
  | json
  | new_state="closed" and old_state="open"

# 2. 서비스별 MTTR 계산 (분 단위)
sum by (service_name) (
  rate(
    {app="selfhealing"} 
    |= "CB_STATE_CHANGE" 
    | json
    | unwrap duration_seconds
  [1h])
) / 60
```

**Prometheus 메트릭 + Grafana 대시보드**:

```yaml
# Grafana Dashboard JSON (단순화)
{
  "title": "Self-Healing MTTR Dashboard",
  "panels": [
    {
      "title": "Average MTTR by Service (Last 24h)",
      "type": "stat",
      "targets": [
        {
          "expr": "avg(selfhealing_mttr_seconds) by (service_name) / 60",
          "legendFormat": "{{service_name}}"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "unit": "m",
          "thresholds": {
            "steps": [
              {"color": "green", "value": null},
              {"color": "yellow", "value": 5},
              {"color": "red", "value": 15}
            ]
          }
        }
      }
    },
    {
      "title": "Recovery Events Timeline",
      "type": "timeseries",
      "targets": [
        {
          "expr": "sum(rate(selfhealing_cb_recovery_total[5m])) by (service_name)"
        }
      ]
    },
    {
      "title": "MTTR Trend (7 Days)",
      "type": "timeseries",
      "targets": [
        {
          "expr": "avg_over_time(selfhealing_mttr_seconds[1d]) / 60"
        }
      ]
    }
  ]
}
```

**Python 기반 MTTR 계산 로직**:

```python
# services/audit/mttr_calculator.py (신규 파일)
"""
MTTR (Mean Time To Recovery) Calculator.

Audit 로그 기반으로 장애 복구 시간을 자동 계산합니다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

@dataclass
class RecoveryEvent:
    """복구 이벤트."""
    service_name: str
    incident_start: datetime  # OPEN 시점
    incident_end: datetime    # CLOSED 시점
    duration_seconds: float
    cause: str  # 장애 원인

@dataclass
class MTTRReport:
    """MTTR 리포트."""
    period_start: datetime
    period_end: datetime
    total_incidents: int
    avg_mttr_seconds: float
    min_mttr_seconds: float
    max_mttr_seconds: float
    p50_mttr_seconds: float
    p90_mttr_seconds: float
    p99_mttr_seconds: float
    by_service: Dict[str, float]
    recovery_events: List[RecoveryEvent]

class MTTRCalculator:
    """
    MTTR 계산기.
    
    CB_STATE_CHANGE 이벤트를 분석하여 MTTR을 계산합니다.
    """
    
    def calculate_mttr(
        self,
        events: List[Dict],
        period_start: Optional[datetime] = None,
        period_end: Optional[datetime] = None,
    ) -> MTTRReport:
        """
        MTTR 계산.
        
        Args:
            events: CB_STATE_CHANGE 이벤트 목록
            period_start: 분석 시작 시간
            period_end: 분석 종료 시간
            
        Returns:
            MTTRReport
        """
        # 서비스별 OPEN 시점 추적
        open_times: Dict[str, datetime] = {}
        recovery_events: List[RecoveryEvent] = []
        
        for event in sorted(events, key=lambda e: e["timestamp"]):
            service = event.get("service_name", "unknown")
            new_state = event.get("new_state")
            timestamp = datetime.fromisoformat(event["timestamp"])
            
            if new_state == "open":
                open_times[service] = timestamp
            elif new_state == "closed" and service in open_times:
                duration = (timestamp - open_times[service]).total_seconds()
                recovery_events.append(RecoveryEvent(
                    service_name=service,
                    incident_start=open_times[service],
                    incident_end=timestamp,
                    duration_seconds=duration,
                    cause=event.get("cause", "unknown"),
                ))
                del open_times[service]
        
        if not recovery_events:
            return MTTRReport(
                period_start=period_start or datetime.now(),
                period_end=period_end or datetime.now(),
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
        
        durations = [e.duration_seconds for e in recovery_events]
        durations.sort()
        
        return MTTRReport(
            period_start=period_start or recovery_events[0].incident_start,
            period_end=period_end or recovery_events[-1].incident_end,
            total_incidents=len(recovery_events),
            avg_mttr_seconds=sum(durations) / len(durations),
            min_mttr_seconds=min(durations),
            max_mttr_seconds=max(durations),
            p50_mttr_seconds=self._percentile(durations, 50),
            p90_mttr_seconds=self._percentile(durations, 90),
            p99_mttr_seconds=self._percentile(durations, 99),
            by_service=self._group_by_service(recovery_events),
            recovery_events=recovery_events,
        )
    
    def _percentile(self, data: List[float], p: int) -> float:
        if not data:
            return 0.0
        k = (len(data) - 1) * p / 100
        f = int(k)
        c = f + 1 if f + 1 < len(data) else f
        return data[f] + (data[c] - data[f]) * (k - f)
    
    def _group_by_service(self, events: List[RecoveryEvent]) -> Dict[str, float]:
        by_service: Dict[str, List[float]] = {}
        for e in events:
            if e.service_name not in by_service:
                by_service[e.service_name] = []
            by_service[e.service_name].append(e.duration_seconds)
        
        return {
            service: sum(durations) / len(durations)
            for service, durations in by_service.items()
        }
```

| 상태 | 구현 파일 |
|------|----------|
| 🟡 계획됨 | `services/audit/mttr_calculator.py` (신규) |

---

### 8.4 Multi-Process 환경 질문 답변

**질문**: ProviderRegistry.get_audit_adapter()가 멀티 프로세스 환경(Celery 워커 등)에서 싱글톤 상태를 잘 유지하는가?

**코드 근거 분석**:

[factory.py](../../../packages/selfhealing-python/src/selfhealing/factory.py)의 `ProviderRegistry`는 **클래스 변수**로 상태 관리:

```python
class ProviderRegistry:
    _instances: dict[str, object] = {}  # 클래스 변수 (프로세스별 독립)
```

**결론**:

| 환경 | 싱글톤 유지 | 데이터 일관성 |
|------|------------|---------------|
| 단일 프로세스 (Django runserver) | ✅ | ✅ |
| Celery prefork | ⚠️ 프로세스별 | ✅ (파일 어댑터) |
| Celery gevent/eventlet | ✅ | ✅ |
| Gunicorn prefork | ⚠️ 프로세스별 | ✅ (파일 어댑터) |

- 각 Celery 워커 프로세스는 **독립적인 메모리 공간** → `_instances`도 프로세스마다 별도
- `FileAuditLogAdapter`는 파일에 append 모드 기록 → **데이터 일관성 유지**
- 메모리 기반 상태를 가진 어댑터 사용 시 프로세스 간 상태 불일치 가능

---

## 9. 종합 구현 계획

### 9.1 구현 우선순위 및 일정

| Phase | 작업 | 중요도 | 예상 소요 | 상태 |
|-------|------|--------|----------|------|
| **Phase 1** | CorruptionShield 배칭 적용 | 🔴 Critical | 2h | ✅ 완료 |
| **Phase 2** | ShadowLogger/WAL → _write_to_wal() 연동 | 🔴 Critical | 3h | ✅ 완료 |
| **Phase 3** | Forensic 민감정보 마스킹 연동 | 🟡 High | 2h | 🟡 계획됨 |
| **Phase 4** | InMemoryAuditBuffer 폴백 구현 | 🟡 High | 4h | 🟡 계획됨 |
| **Phase 5** | Forensic Rate Limiter 구현 (SlidingWindow) | 🟡 High | 3h | 🟡 계획됨 |
| **Phase 6** | RedisAuditBuffer 분산 버퍼 구현 | 🟡 High | 4h | 🟡 계획됨 |
| **Phase 7** | MTTR Calculator 구현 | 🟢 Medium | 4h | 🟡 계획됨 |
| **Phase 8** | 단위 테스트 작성 | 🟢 Medium | 4h | ✅ 완료 |

**Phase 1,2 완료일**: 2026-01-08  
**테스트 결과**: 9개 테스트 통과

### 9.2 변경 파일 목록

| 파일 | 변경 유형 | Phase | 상태 |
|------|----------|-------|------|
| `services/corruption_shield/shield.py` | 수정 | 1 | ✅ 완료 |
| `adapters/memory/shadow_logger.py` | 수정 | 2 | ✅ 완료 |
| `audit/wal.py` | 수정 | 2 | ✅ 완료 |
| `tests/self_healing/unit/test_audit_integration_part2.py` | 수정 | 1, 2 | ✅ 완료 |
| `services/forensic_audit_bridge.py` | 수정 | 3, 5 | 🟡 계획됨 |
| `audit/resilience.py` | 수정 | 4 | 🟡 계획됨 |
| `services/audit/base.py` | 수정 | 4 | 🟡 계획됨 |
| `adapters/audit/redis_buffer.py` | 신규 | 6 | 🟡 계획됨 |
| `services/audit/mttr_calculator.py` | 신규 | 7 | 🟡 계획됨 |

### 9.3 테스트 계획

```python
# tests/self_healing/unit/test_audit_integration_part2.py (완료됨)

class TestCorruptionShieldBatching:
    """CorruptionShield 배칭 테스트."""
    
    def test_multiple_violations_single_event(self):
        """여러 위반 시 1개 Audit 이벤트만 생성."""
        # ✅ 통과
    
    def test_violations_list_in_details(self):
        """violations 리스트가 details에 포함."""
        # ✅ 통과
    
    def test_violation_count_matches_violations_list(self):
        """violation_count가 violations 리스트 길이와 일치."""
        # ✅ 통과
    
    def test_fallback_to_write_to_wal_without_request(self):
        """request 없을 때 _write_to_wal()로 폴백."""
        # ✅ 통과


class TestAuditContextAutoInjection:
    """ActorContext/TraceContext 자동 주입 테스트."""
    
    def test_shadow_logger_uses_write_to_wal(self):
        """ShadowLogger가 _write_to_wal()을 직접 호출."""
        # ✅ 통과
    
    def test_shadow_logger_recovery_uses_write_to_wal(self):
        """ShadowLogger 복구 시 _write_to_wal() 호출."""
        # ✅ 통과
    
    def test_wal_uses_write_to_wal_for_rotation(self):
        """WAL 로테이션 시 _write_to_wal() 호출."""
        # ✅ 통과
    
    def test_wal_audit_adapter_priority_over_write_to_wal(self):
        """WAL에 audit_adapter가 주입되면 우선 사용."""
        # ✅ 통과
    
    def test_shadow_logger_graceful_on_import_error(self):
        """_write_to_wal import 실패 시 graceful 처리."""
        # ✅ 통과
```


# 향후 테스트 (Phase 3-7 구현 시 추가)

```python
    
    def test_shadow_logger_includes_actor_id(self):
        """ShadowLogger 이벤트에 actor_id 포함."""
        pass
    
    def test_wal_rotation_includes_trace_id(self):
        """WAL 로테이션 이벤트에 trace_id 포함."""
        pass


class TestForensicMasking:
    """Forensic 민감정보 마스킹 테스트."""
    
    def test_password_masked_in_context(self):
        """password 필드 마스킹."""
        pass
    
    def test_api_key_masked_in_context(self):
        """api_key 필드 마스킹."""
        pass


class TestInMemoryAuditBuffer:
    """메모리 버퍼 폴백 테스트."""
    
    def test_wal_failure_triggers_memory_buffer(self):
        """WAL 실패 시 메모리 버퍼 저장."""
        pass
    
    def test_buffer_flush_on_wal_recovery(self):
        """WAL 복구 시 버퍼 플러시."""
        pass
    
    def test_buffer_overflow_drops_oldest(self):
        """버퍼 초과 시 가장 오래된 엔트리 삭제."""
        pass


class TestForensicRateLimiter:
    """Forensic Rate Limiter 테스트."""
    
    def test_exception_rate_limit(self):
        """분당 10건 초과 시 드롭."""
        pass
    
    def test_snapshot_rate_limit(self):
        """분당 1건 초과 시 드롭."""
        pass
    
    def test_token_refill_after_window(self):
        """윈도우 경과 후 토큰 재충전."""
        pass


class TestMTTRCalculator:
    """MTTR 계산기 테스트."""
    
    def test_mttr_calculation_accuracy(self):
        """MTTR 계산 정확도."""
        pass
    
    def test_percentile_calculation(self):
        """P50/P90/P99 계산."""
        pass
    
    def test_group_by_service(self):
        """서비스별 그룹핑."""
        pass
```

---

## 10. 관련 문서

- [event_buffer.py](../../../packages/selfhealing-python/src/selfhealing/audit/event_buffer.py) - Audit 이벤트 버퍼
- [shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py) - CorruptionShield
- [shadow_logger.py](../../../packages/selfhealing-python/src/selfhealing/adapters/memory/shadow_logger.py) - ShadowLogger
- [wal.py](../../../packages/selfhealing-python/src/selfhealing/audit/wal.py) - Write-Ahead Log
- [forensic_audit_bridge.py](../../../packages/selfhealing-python/src/selfhealing/services/forensic_audit_bridge.py) - Forensic-Audit 브릿지
- [resilience.py](../../../packages/selfhealing-python/src/selfhealing/audit/resilience.py) - Audit Resilience
- [base.py](../../../packages/selfhealing-python/src/selfhealing/services/audit/base.py) - Audit Base Helpers
- [masking.py](../../../packages/selfhealing-python/src/selfhealing/audit/masking.py) - 민감정보 마스킹
- [config.py](../../../packages/selfhealing-python/src/selfhealing/config.py) - ForensicSettings
- [test_audit_integration_part2.py](../../../tests/self_healing/unit/test_audit_integration_part2.py) - 단위 테스트
- [20_AUDIT_UNIFICATION_PLAN.md](20_AUDIT_UNIFICATION_PLAN.md) - Audit 통합 계획

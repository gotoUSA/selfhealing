# Part 3: ViolationType & IdempotencyDomain 확장 가이드

**문서 버전**: 1.0.0  
**작성일**: 2026-01-07  
**근거 코드**: 실제 소스 코드 분석 기반

---

## 1. 현황 분석

### 1.1 현재 ViolationType 정의

[security_violation_service.py](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py#L38-50) 파일에서 확인:

```python
# security_violation_service.py L38-50
class ViolationType(str, Enum):
    """Types of security violations that never self-heal (domain-neutral)."""

    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
```

**Severity 매핑** (L62-71):
```python
SEVERITY_BY_VIOLATION_TYPE: dict[str, Severity] = {
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
    ViolationType.TOKEN_FORGED: Severity.CRITICAL,
    ViolationType.REPLAY_ATTACK: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.INJECTION_ATTEMPT: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ViolationType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
}
```

### 1.2 현재 IdempotencyDomain 정의

[idempotency_service.py](../../../packages/selfhealing-python/src/selfhealing/services/idempotency_service.py#L42-50) 파일에서 확인:

```python
# idempotency_service.py L42-50
class IdempotencyDomain(Enum):
    """Domains that support idempotency checking (domain-neutral)."""

    EXTERNAL_SERVICE = "external_service"
    INTERNAL_PROCESS = "internal_process"
    ASYNC_TASK = "async_task"
    EVENT = "event"
    CUSTOM = "custom"
```

---

## 2. ViolationType 확장

### 2.1 누락된 ViolationType 분석

#### Gap 1: CorruptionShield L3 위반

[shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py#L225-230) 분석:

```python
# shield.py L225-230
service.record_violation(
    violation_type=f"corruption_{violation.code}",  # 동적 문자열
    details={...},
)
# ⚠️ ViolationType enum을 사용하지 않음
# ⚠️ 표준화되지 않은 위반 유형
```

**필요한 ViolationType**:
- `ANOMALY_STATISTICAL` - 통계적 이상 감지 (L3)
- `ANOMALY_BEHAVIORAL` - 행위 이상 감지

#### Gap 2: Audit 무결성 위반

현재 Audit 시스템에 Tampering 감지가 있으나 ViolationType에 없음:

**필요한 ViolationType**:
- `AUDIT_TAMPERING` - Audit 로그 조작 시도
- `HASH_CHAIN_BROKEN` - 해시 체인 무결성 위반

#### Gap 3: 설정 무단 변경

RuntimeConfig에서 Governance 우회 시도:

**필요한 ViolationType**:
- `UNAUTHORIZED_OVERRIDE` - 권한 없는 설정 변경 시도
- `GOVERNANCE_BYPASS_ATTEMPT` - Governance 우회 시도

### 2.2 신규 ViolationType 구현

```python
# security_violation_service.py 수정안

class ViolationType(str, Enum):
    """Types of security violations that never self-heal (domain-neutral)."""

    # ═══════════════════════════════════════════════════════════
    # 기존 ViolationType
    # ═══════════════════════════════════════════════════════════
    SIGNATURE_INVALID = "signature_invalid"
    DATA_TAMPERED = "data_tampered"
    TOKEN_FORGED = "token_forged"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    RATE_LIMIT_ABUSE = "rate_limit_abuse"
    SUSPICIOUS_ACTIVITY = "suspicious_activity"
    REPLAY_ATTACK = "replay_attack"
    INJECTION_ATTEMPT = "injection_attempt"
    
    # ═══════════════════════════════════════════════════════════
    # CorruptionShield / 이상 감지 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    ANOMALY_STATISTICAL = "anomaly_statistical"
    """L3 통계적 이상 감지 (Z-score 기반)."""
    
    ANOMALY_BEHAVIORAL = "anomaly_behavioral"
    """행위 이상 감지 (시퀀스 패턴 이탈)."""
    
    SCHEMA_VIOLATION = "schema_violation"
    """L1 스키마 위반 (필수 필드 누락, 타입 불일치)."""
    
    BUSINESS_RULE_VIOLATION = "business_rule_violation"
    """L2 비즈니스 규칙 위반."""
    
    # ═══════════════════════════════════════════════════════════
    # Audit 무결성 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    AUDIT_TAMPERING = "audit_tampering"
    """Audit 로그 조작 시도 감지."""
    
    HASH_CHAIN_BROKEN = "hash_chain_broken"
    """ContinuousAuditRecorder 해시 체인 무결성 위반."""
    
    WAL_CORRUPTION = "wal_corruption"
    """WAL CRC32 체크섬 불일치."""
    
    # ═══════════════════════════════════════════════════════════
    # Governance 위반 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    UNAUTHORIZED_OVERRIDE = "unauthorized_override"
    """권한 없는 설정 변경 시도."""
    
    GOVERNANCE_BYPASS_ATTEMPT = "governance_bypass_attempt"
    """Kill Switch/Emergency Mode 우회 시도."""
    
    PRIVILEGE_ESCALATION = "privilege_escalation"
    """권한 상승 시도."""


# Severity 매핑 업데이트
SEVERITY_BY_VIOLATION_TYPE: dict[str, Severity] = {
    # 기존
    ViolationType.SIGNATURE_INVALID: Severity.CRITICAL,
    ViolationType.DATA_TAMPERED: Severity.CRITICAL,
    ViolationType.TOKEN_FORGED: Severity.CRITICAL,
    ViolationType.REPLAY_ATTACK: Severity.CRITICAL,
    ViolationType.UNAUTHORIZED_ACCESS: Severity.HIGH,
    ViolationType.INJECTION_ATTEMPT: Severity.HIGH,
    ViolationType.RATE_LIMIT_ABUSE: Severity.MEDIUM,
    ViolationType.SUSPICIOUS_ACTIVITY: Severity.MEDIUM,
    
    # 신규 - Critical (즉시 차단)
    ViolationType.AUDIT_TAMPERING: Severity.CRITICAL,
    ViolationType.HASH_CHAIN_BROKEN: Severity.CRITICAL,
    ViolationType.WAL_CORRUPTION: Severity.CRITICAL,
    ViolationType.GOVERNANCE_BYPASS_ATTEMPT: Severity.CRITICAL,
    ViolationType.PRIVILEGE_ESCALATION: Severity.CRITICAL,
    
    # 신규 - High (차단, DLQ 저장)
    ViolationType.ANOMALY_STATISTICAL: Severity.HIGH,
    ViolationType.ANOMALY_BEHAVIORAL: Severity.HIGH,
    ViolationType.UNAUTHORIZED_OVERRIDE: Severity.HIGH,
    ViolationType.BUSINESS_RULE_VIOLATION: Severity.HIGH,
    
    # 신규 - Medium (로깅, 모니터링)
    ViolationType.SCHEMA_VIOLATION: Severity.MEDIUM,
}
```

### 2.3 CorruptionShield 연동 수정

```python
# shield.py 수정안

def _maybe_create_security_incident(
    self,
    data: dict,
    result: ValidationResult,
) -> None:
    """Create security incident for critical violations."""
    critical_violations = [
        v for v in result.violations
        if v.severity == "critical"
    ]
    
    if not critical_violations:
        return
    
    try:
        from selfhealing.services.security_violation_service import (
            SecurityViolationService,
            ViolationType,  # ✅ ViolationType enum 임포트
        )
        
        service = SecurityViolationService()
        
        for violation in critical_violations:
            # ✅ 표준 ViolationType 매핑
            violation_type = self._map_to_violation_type(violation)
            
            service.record_violation(
                violation_type=violation_type,
                details={
                    "layer": violation.layer,
                    "message": violation.message,
                    "field": violation.field,
                    "data_sample": str(data)[:200],
                },
            )
    except Exception as e:
        logger.warning(f"[CorruptionShield] Failed to create security incident: {e}")

def _map_to_violation_type(self, violation) -> ViolationType:
    """
    Corruption 위반을 표준 ViolationType으로 매핑.
    
    Args:
        violation: CorruptionShield의 Violation 객체
    
    Returns:
        ViolationType enum 값
    """
    from selfhealing.services.security_violation_service import ViolationType
    
    layer_mapping = {
        "L1": ViolationType.SCHEMA_VIOLATION,
        "L2": ViolationType.BUSINESS_RULE_VIOLATION,
        "L3": ViolationType.ANOMALY_STATISTICAL,
    }
    
    # 특수 케이스 처리
    if "anomaly" in violation.code.lower():
        if "behavioral" in violation.code.lower():
            return ViolationType.ANOMALY_BEHAVIORAL
        return ViolationType.ANOMALY_STATISTICAL
    
    return layer_mapping.get(violation.layer, ViolationType.SUSPICIOUS_ACTIVITY)
```

---

## 3. IdempotencyDomain 확장

### 3.1 누락된 IdempotencyDomain 분석

#### Gap 1: Chaos 실험 중복 실행 방지

ChaosScheduler에서 동일 실험의 중복 실행 방지가 필요:

```python
# scheduler.py L502-510
def execute_now(self, schedule_id: str, force: bool = False) -> ExecutionResult:
    experiment_id = f"chaos-{uuid.uuid4().hex[:12]}"
    # ⚠️ 동일 schedule_id에 대한 중복 실행 체크 없음
```

**필요한 IdempotencyDomain**:
- `CHAOS_EXPERIMENT` - Chaos 실험 중복 실행 방지

#### Gap 2: 설정 변경 중복 적용 방지

RuntimeConfig에서 동일 설정 변경의 중복 적용 방지가 필요:

**필요한 IdempotencyDomain**:
- `CONFIG_CHANGE` - 설정 변경 중복 적용 방지

#### Gap 3: L2 동기화 중복 방지

ShadowLogger에서 복구 시 동일 레코드의 중복 동기화 방지가 필요:

**필요한 IdempotencyDomain**:
- `L2_SYNC` - L2 동기화 중복 방지

### 3.2 신규 IdempotencyDomain 구현

```python
# idempotency_service.py 수정안

class IdempotencyDomain(Enum):
    """Domains that support idempotency checking (domain-neutral)."""

    # ═══════════════════════════════════════════════════════════
    # 기존 도메인
    # ═══════════════════════════════════════════════════════════
    EXTERNAL_SERVICE = "external_service"
    """외부 서비스 호출 (결제, 알림 등)."""
    
    INTERNAL_PROCESS = "internal_process"
    """내부 프로세스 (재고 차감, 포인트 적립 등)."""
    
    ASYNC_TASK = "async_task"
    """비동기 작업 (Celery Task 등)."""
    
    EVENT = "event"
    """이벤트 처리 (Webhook, 메시지 등)."""
    
    CUSTOM = "custom"
    """커스텀 도메인."""
    
    # ═══════════════════════════════════════════════════════════
    # Chaos Engineering 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    CHAOS_EXPERIMENT = "chaos_experiment"
    """Chaos 실험 실행 (동일 실험 중복 실행 방지)."""
    
    # ═══════════════════════════════════════════════════════════
    # 설정 관리 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    CONFIG_CHANGE = "config_change"
    """설정 변경 (동일 변경 중복 적용 방지)."""
    
    # ═══════════════════════════════════════════════════════════
    # 저장소 동기화 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    L2_SYNC = "l2_sync"
    """L2 저장소 동기화 (복구 후 재동기화 중복 방지)."""
    
    WAL_RECOVERY = "wal_recovery"
    """WAL 복구 (동일 엔트리 중복 처리 방지)."""
    
    # ═══════════════════════════════════════════════════════════
    # Auto Tuning 관련 (신규)
    # ═══════════════════════════════════════════════════════════
    AUTO_ADJUSTMENT = "auto_adjustment"
    """자율 조정 (동일 조정 중복 적용 방지)."""


# IdempotencyKey 팩토리 메서드 추가
@dataclass
class IdempotencyKey:
    # ... 기존 필드 ...
    
    @classmethod
    def for_chaos_experiment(
        cls,
        schedule_id: str,
        experiment_type: str,
        target_service: str,
    ) -> "IdempotencyKey":
        """
        Chaos 실험에 대한 멱등성 키 생성.
        
        동일 스케줄의 실험이 동시에 실행되는 것을 방지.
        
        Args:
            schedule_id: 스케줄 ID
            experiment_type: 실험 유형
            target_service: 대상 서비스
            
        Returns:
            IdempotencyKey for chaos experiment
        """
        key = f"chaos:{schedule_id}:{experiment_type}:{target_service}"
        return cls(
            domain=IdempotencyDomain.CHAOS_EXPERIMENT,
            key=key,
            components={
                "schedule_id": schedule_id,
                "experiment_type": experiment_type,
                "target_service": target_service,
            },
        )
    
    @classmethod
    def for_config_change(
        cls,
        config_key: str,
        new_value_hash: str,
        changed_by: str,
    ) -> "IdempotencyKey":
        """
        설정 변경에 대한 멱등성 키 생성.
        
        동일 설정 변경이 중복 적용되는 것을 방지.
        
        Args:
            config_key: 설정 키
            new_value_hash: 새 값의 해시
            changed_by: 변경 주체
            
        Returns:
            IdempotencyKey for config change
        """
        key = f"config:{config_key}:{new_value_hash}"
        return cls(
            domain=IdempotencyDomain.CONFIG_CHANGE,
            key=key,
            components={
                "config_key": config_key,
                "new_value_hash": new_value_hash,
                "changed_by": changed_by,
            },
        )
    
    @classmethod
    def for_l2_sync(
        cls,
        service_name: str,
        record_id: str,
        intended_state: str,
    ) -> "IdempotencyKey":
        """
        L2 동기화에 대한 멱등성 키 생성.
        
        복구 후 동일 레코드가 중복 동기화되는 것을 방지.
        
        Args:
            service_name: 서비스 이름
            record_id: 레코드 ID
            intended_state: 목표 상태
            
        Returns:
            IdempotencyKey for L2 sync
        """
        key = f"l2sync:{service_name}:{record_id}"
        return cls(
            domain=IdempotencyDomain.L2_SYNC,
            key=key,
            components={
                "service_name": service_name,
                "record_id": record_id,
                "intended_state": intended_state,
            },
        )
    
    @classmethod
    def for_auto_adjustment(
        cls,
        module: str,
        parameter: str,
        target_value: str,
    ) -> "IdempotencyKey":
        """
        자율 조정에 대한 멱등성 키 생성.
        
        동일 조정이 중복 적용되는 것을 방지.
        
        Args:
            module: 모듈 이름 (circuit_breaker, retry 등)
            parameter: 파라미터 이름
            target_value: 목표 값
            
        Returns:
            IdempotencyKey for auto adjustment
        """
        key = f"adjust:{module}:{parameter}:{target_value}"
        return cls(
            domain=IdempotencyDomain.AUTO_ADJUSTMENT,
            key=key,
            components={
                "module": module,
                "parameter": parameter,
                "target_value": target_value,
            },
        )
```

### 3.3 ChaosScheduler Idempotency 적용

```python
# scheduler.py 수정안

from selfhealing.services.idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyDomain,
)

class ChaosSchedulerService:
    
    def __init__(self, config: Optional[SchedulerConfig] = None):
        self._config = config or SchedulerConfig()
        # ... 기존 초기화 ...
        
        # ✅ 신규: IdempotencyService 초기화
        self._idempotency_service = IdempotencyService()
    
    def execute_now(self, schedule_id: str, force: bool = False) -> ExecutionResult:
        """Execute a scheduled experiment immediately."""
        schedule = self.get_schedule(schedule_id)
        if not schedule:
            return ExecutionResult(
                schedule_id=schedule_id,
                experiment_id="",
                status="error",
                error_message=f"Schedule not found: {schedule_id}",
            )
        
        experiment_id = f"chaos-{uuid.uuid4().hex[:12]}"
        started_at = now()
        
        # ✅ 신규: 멱등성 체크
        if not force:
            idempotency_key = IdempotencyKey.for_chaos_experiment(
                schedule_id=schedule_id,
                experiment_type=schedule.experiment_type,
                target_service=schedule.target_service,
            )
            
            idem_result = self._idempotency_service.check(idempotency_key)
            
            if idem_result.is_duplicate:
                return ExecutionResult(
                    schedule_id=schedule_id,
                    experiment_id="",
                    status="duplicate",
                    skipped=True,
                    skip_reason=f"Duplicate experiment execution: {idem_result.message}",
                    started_at=started_at.isoformat(),
                    completed_at=now().isoformat(),
                )
        
        try:
            # ... 기존 실행 로직 ...
            
            # ✅ 실험 완료 후 멱등성 마킹
            if not force:
                self._idempotency_service.mark_as_processed(idempotency_key)
            
            return execution_result
            
        except Exception as e:
            # ... 에러 처리 ...
            pass
```

---

## 4. 테스트 계획

### 4.1 ViolationType 테스트

```python
# tests/self_healing/unit/test_violation_type_extension.py

import pytest
from selfhealing.services.security_violation_service import (
    ViolationType,
    Severity,
    SEVERITY_BY_VIOLATION_TYPE,
)

class TestViolationTypeExtension:
    """ViolationType 확장 테스트."""
    
    def test_new_violation_types_exist(self):
        """신규 ViolationType이 정의되어 있는지 확인."""
        new_types = [
            "ANOMALY_STATISTICAL",
            "ANOMALY_BEHAVIORAL",
            "AUDIT_TAMPERING",
            "HASH_CHAIN_BROKEN",
            "WAL_CORRUPTION",
            "UNAUTHORIZED_OVERRIDE",
            "GOVERNANCE_BYPASS_ATTEMPT",
        ]
        
        for type_name in new_types:
            assert hasattr(ViolationType, type_name), f"Missing: {type_name}"
    
    def test_new_violation_types_have_severity(self):
        """신규 ViolationType에 Severity가 매핑되어 있는지 확인."""
        for vtype in ViolationType:
            assert vtype in SEVERITY_BY_VIOLATION_TYPE, f"Missing severity: {vtype}"
    
    def test_critical_violations(self):
        """Critical 위반 유형 확인."""
        critical_types = [
            ViolationType.AUDIT_TAMPERING,
            ViolationType.HASH_CHAIN_BROKEN,
            ViolationType.WAL_CORRUPTION,
            ViolationType.GOVERNANCE_BYPASS_ATTEMPT,
        ]
        
        for vtype in critical_types:
            assert SEVERITY_BY_VIOLATION_TYPE[vtype] == Severity.CRITICAL
```

### 4.2 IdempotencyDomain 테스트

```python
# tests/self_healing/unit/test_idempotency_domain_extension.py

import pytest
from selfhealing.services.idempotency_service import (
    IdempotencyDomain,
    IdempotencyKey,
)

class TestIdempotencyDomainExtension:
    """IdempotencyDomain 확장 테스트."""
    
    def test_new_domains_exist(self):
        """신규 IdempotencyDomain이 정의되어 있는지 확인."""
        new_domains = [
            "CHAOS_EXPERIMENT",
            "CONFIG_CHANGE",
            "L2_SYNC",
            "WAL_RECOVERY",
            "AUTO_ADJUSTMENT",
        ]
        
        for domain_name in new_domains:
            assert hasattr(IdempotencyDomain, domain_name), f"Missing: {domain_name}"
    
    def test_chaos_experiment_key_factory(self):
        """for_chaos_experiment 팩토리 테스트."""
        key = IdempotencyKey.for_chaos_experiment(
            schedule_id="sched-123",
            experiment_type="latency_injection",
            target_service="payment",
        )
        
        assert key.domain == IdempotencyDomain.CHAOS_EXPERIMENT
        assert "sched-123" in key.key
        assert key.components["experiment_type"] == "latency_injection"
    
    def test_config_change_key_factory(self):
        """for_config_change 팩토리 테스트."""
        key = IdempotencyKey.for_config_change(
            config_key="max_retries",
            new_value_hash="abc123",
            changed_by="admin",
        )
        
        assert key.domain == IdempotencyDomain.CONFIG_CHANGE
        assert "max_retries" in key.key
    
    def test_l2_sync_key_factory(self):
        """for_l2_sync 팩토리 테스트."""
        key = IdempotencyKey.for_l2_sync(
            service_name="payment",
            record_id="rec-456",
            intended_state="open",
        )
        
        assert key.domain == IdempotencyDomain.L2_SYNC
        assert "payment" in key.key
```

---

## 5. 영향 분석

### 5.1 변경 파일 목록

| 파일 | 변경 유형 | 설명 |
|------|----------|------|
| `services/security_violation_service.py` | 수정 | 신규 ViolationType 추가 |
| `services/idempotency_service.py` | 수정 | 신규 IdempotencyDomain 및 팩토리 메서드 추가 |
| `services/corruption_shield/shield.py` | 수정 | ViolationType 매핑 |
| `services/chaos/scheduler.py` | 수정 | Idempotency 적용 |

### 5.2 하위 호환성

- **기존 Enum 값 유지**: 모든 기존 ViolationType, IdempotencyDomain 값 변경 없음
- **추가만**: 신규 값만 추가, 기존 코드 영향 없음
- **Optional 사용**: 신규 팩토리 메서드는 선택적 사용

---

## 6. 구현 우선순위

| 순위 | 작업 | 중요도 | 예상 공수 |
|------|------|--------|----------|
| 1 | ViolationType 신규 추가 | 🔴 Critical | 1시간 |
| 2 | Severity 매핑 업데이트 | 🔴 Critical | 0.5시간 |
| 3 | CorruptionShield ViolationType 매핑 | 🟡 High | 1시간 |
| 4 | IdempotencyDomain 신규 추가 | 🟡 High | 1시간 |
| 5 | IdempotencyKey 팩토리 메서드 추가 | 🟡 High | 1.5시간 |
| 6 | ChaosScheduler Idempotency 적용 | 🟢 Medium | 1.5시간 |
| 7 | 단위 테스트 작성 | 🟢 Medium | 2시간 |

**총 예상 공수**: 8.5시간

---

## 7. 관련 문서

- [security_violation_service.py](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py) - SecurityViolationService
- [idempotency_service.py](../../../packages/selfhealing-python/src/selfhealing/services/idempotency_service.py) - IdempotencyService
- [shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py) - CorruptionShield
- [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) - ChaosScheduler

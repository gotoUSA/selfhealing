# Part 3: ViolationType & IdempotencyDomain 확장 가이드

**문서 버전**: 2.2.0  
**작성일**: 2026-01-07  
**최종 수정**: 2026-01-08  
**근거 코드**: 실제 소스 코드 분석 기반

---

## 📋 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-01-07 | 초안 작성 |
| 2.0.0 | 2026-01-08 | 아키텍트 리뷰 반영: ActionPolicy, Anti-Flapping, Escape Strategy 추가 |
| 2.1.0 | 2026-01-08 | 롤백 로직, StateBackend 기반 영속성, Redis 분산 캐싱 추가 |
| 2.2.0 | 2026-01-08 | 순환 참조 방지 가이드, 블랙리스트 Admin API, triggering_trace_id 추가 |

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
| **0** | **ActionPolicy Enum 및 ProtectionOrchestrator** | 🔴 Critical | 2시간 |
| **0.3** | **ProtectionOrchestrator 롤백 로직** (v2.1.0) | 🔴 Critical | 1시간 |
| **0.5** | **Self-Healing Loop ViolationType 추가** | 🔴 Critical | 1시간 |
| 1 | ViolationType 신규 추가 | 🔴 Critical | 1시간 |
| 2 | Severity 매핑 업데이트 | 🔴 Critical | 0.5시간 |
| **2.5** | **EventBus → Emergency Mode 연동** | 🟡 High | 1.5시간 |
| 3 | CorruptionShield ViolationType 매핑 | 🟡 High | 1시간 |
| 4 | IdempotencyDomain 신규 추가 | 🟡 High | 1시간 |
| 5 | IdempotencyKey 팩토리 메서드 추가 (**Anti-Flapping 포함**) | 🟡 High | 2시간 |
| **5.3** | **AntiFlappingWindow Redis 분산 캐싱** (v2.1.0) | 🟡 High | 1시간 |
| **5.5** | **Escape Strategy (LearningService 블랙리스트)** | 🟡 High | 2시간 |
| **5.7** | **ParameterBlacklist StateBackend 영속성** (v2.1.0) | 🟡 High | 1시간 |
| 6 | ChaosScheduler Idempotency 적용 | 🟢 Medium | 1.5시간 |
| 7 | 단위 테스트 작성 (롤백, 영속성, Redis 포함) | 🟢 Medium | 4시간 |

**총 예상 공수**: 20.5시간

---

## 7. 관련 문서

- [security_violation_service.py](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py) - SecurityViolationService
- [idempotency_service.py](../../../packages/selfhealing-python/src/selfhealing/services/idempotency_service.py) - IdempotencyService
- [shield.py](../../../packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py) - CorruptionShield
- [scheduler.py](../../../packages/selfhealing-python/src/selfhealing/services/chaos/scheduler.py) - ChaosScheduler
- [learning/service.py](../../../packages/selfhealing-python/src/selfhealing/services/learning/service.py) - LearningService
- [event_bus.py](../../../packages/selfhealing-python/src/selfhealing/services/event_bus.py) - EventBus
- [throttle/base.py](../../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py) - SlidingWindowThrottle
- [state_backend.py](../../../packages/selfhealing-python/src/selfhealing/core/state_backend.py) - StateBackend (v2.1.0)

---

## 8. 아키텍트 리뷰 반영 (v2.0.0)

### 8.1 질문 및 답변

#### Q1: SecurityViolationService.record_violation의 처리 방식

**코드 근거**: [security_violation_service.py#L218-294](../../../packages/selfhealing-python/src/selfhealing/services/security_violation_service.py#L218-294)

| 기능 | 현재 상태 | 코드 위치 |
|------|----------|----------|
| Audit 저장 | ✅ 구현됨 | `self.repository.create()` |
| 실시간 차단 | ✅ 구현됨 | `_take_protective_action()` - IP 차단, 세션 무효화 |
| EventBus 연동 | ❌ 미구현 | EventBus 트리거 없음 |
| Emergency Mode 연동 | ❌ 미구현 | 자동 선포 로직 없음 |

**결론**: 개별 보호 조치는 구현되어 있으나, 시스템 전체 연동(EventBus → Emergency Mode)은 §8.3에서 신규 구현 필요.

#### Q2: Idempotency 저장소의 영속성

**코드 근거**: [idempotency_service.py#L302-402](../../../packages/selfhealing-python/src/selfhealing/services/idempotency_service.py#L302-402)

```python
# 이미 Graceful Degradation 구현됨
try:
    cached_value = cache.get(key.cache_key)
except Exception:
    logger.warning("Cache unavailable, falling back to DB")

# DB Fallback
if lookup_fn:
    existing = lookup_fn(**key.components)
```

| 질문 | 답변 | 근거 |
|------|------|------|
| Redis에만 저장? | ❌ 아님 | Redis + DB 이중 체크 (Graceful Degradation) |
| Redis 재시작 시 복구? | ✅ DB Fallback | DB가 Source of Truth |
| WAL 연동 필요? | ❌ **불필요** | 아래 상세 분석 참조 |

**WAL 기반 멱등성 복구가 불필요한 이유**:

1. **현재 설계가 이미 충분**: IdempotencyService는 Redis 장애 시 자동으로 DB로 Fallback
2. **Source of Truth는 DB**: Redis는 캐시 역할, DB가 실제 상태 저장
3. **테스트로 검증됨**: [test_redis_failure_scenarios.py#L142-180](../../../tests/self_healing/integration/test_redis_failure_scenarios.py#L142-180)
4. **WAL 목적이 다름**: 현재 WAL은 Audit 무결성 보장용, 멱등성 키 저장은 오버엔지니어링

#### Q3: Violation Severity와 Error Budget 연결

**코드 근거**: [event_bus.py#L531-545](../../../packages/selfhealing-python/src/selfhealing/services/event_bus.py#L531-545)

| 질문 | 답변 | 근거 |
|------|------|------|
| CRITICAL → Error Budget 0? | ❌ 미구현 | 연동 로직 없음 |
| 즉시 소진 로직? | ❌ 미구현 | 계획 필요 |

**결론**: Error Budget은 SLO 기반 메트릭으로만 계산됨. Severity → Budget 연동은 §8.3.5에서 설계.

---

### 8.2 time_bucket vs window_id (슬라이딩 윈도우) 선택

**코드 근거**: [throttle/base.py#L68-130](../../../packages/selfhealing-python/src/selfhealing/services/throttle/base.py#L68-130)

```python
# 현재 시스템의 SlidingWindow 구현 (SlidingWindowThrottle)
def check(self, key: str) -> ThrottleResult:
    now = time.time()
    window_start = now - self.config.window_seconds
    
    # 타임스탬프 기반 슬라이딩 윈도우
    self._windows[key] = [
        ts for ts in self._windows[key] if ts > window_start
    ]
```

| 항목 | time_bucket | window_id (슬라이딩) |
|------|-------------|----------------------|
| 방식 | 고정 시간 버킷 (예: 60초 단위) | 연속적 시간 윈도우 |
| 정밀도 | 버킷 경계에서 부정확 | 연속적으로 정확 |
| 기존 시스템 호환성 | ❌ 별도 구현 필요 | ✅ SlidingWindowThrottle 재사용 가능 |
| ForensicRateLimiter 호환 | ❌ | ✅ 동일 패턴 ([forensic_audit_bridge.py#L45-100](../../../packages/selfhealing-python/src/selfhealing/services/forensic_audit_bridge.py#L45-100)) |

**결정**: ✅ **window_id (슬라이딩) 채택**

**이유**:
1. `SlidingWindowThrottle`이 이미 검증됨
2. `ForensicRateLimiter`도 동일 패턴 사용
3. 버킷 경계 문제 없이 정확한 윈도우 추적

---

### 8.3 신규 구현 계획 (아키텍트 리뷰 반영)

#### 8.3.1 ActionPolicy Enum 및 ProtectionOrchestrator (리뷰 ①)

**목적**: 보호 조치의 원자성 보장 - "가장 강력한 정책 우선" 원칙

```python
# security_violation_service.py 추가

from enum import Enum, auto
from typing import List, Tuple
from dataclasses import dataclass


class ActionPolicy(str, Enum):
    """즉각적 대응 정책 (우선순위 높은 순)."""
    
    # Priority 1: 시스템 전체 보호
    EMERGENCY_LEVEL_3 = "emergency_level_3"
    """전체 시스템 보호 모드."""
    
    EMERGENCY_LEVEL_2 = "emergency_level_2"
    """부분 시스템 보호 모드."""
    
    EMERGENCY_LEVEL_1 = "emergency_level_1"
    """경고 모드."""
    
    # Priority 2: 사용자/세션 격리
    ACCOUNT_FREEZE = "account_freeze"
    """계정 동결."""
    
    SESSION_INVALIDATE = "session_invalidate"
    """모든 세션 무효화."""
    
    # Priority 3: 네트워크 차단
    IP_PERMANENT_BAN = "ip_permanent_ban"
    """영구 IP 차단."""
    
    IP_TEMPORARY_BAN = "ip_temporary_ban"
    """임시 IP 차단."""
    
    # Priority 4: 로깅
    BLOCK_AND_LOG = "block_and_log"
    """차단 및 로깅."""


# 정책 우선순위 (숫자가 낮을수록 높은 우선순위)
ACTION_POLICY_PRIORITY: dict[ActionPolicy, int] = {
    ActionPolicy.EMERGENCY_LEVEL_3: 1,
    ActionPolicy.EMERGENCY_LEVEL_2: 2,
    ActionPolicy.EMERGENCY_LEVEL_1: 3,
    ActionPolicy.ACCOUNT_FREEZE: 4,
    ActionPolicy.SESSION_INVALIDATE: 5,
    ActionPolicy.IP_PERMANENT_BAN: 6,
    ActionPolicy.IP_TEMPORARY_BAN: 7,
    ActionPolicy.BLOCK_AND_LOG: 8,
}


# ViolationType → ActionPolicy 매핑
ACTION_POLICY_BY_VIOLATION_TYPE: dict[ViolationType, list[ActionPolicy]] = {
    # 가장 심각한 위반 → 다중 정책
    ViolationType.PRIVILEGE_ESCALATION: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.ACCOUNT_FREEZE,
    ],
    ViolationType.AUDIT_TAMPERING: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.IP_PERMANENT_BAN,
    ],
    ViolationType.GOVERNANCE_BYPASS_ATTEMPT: [
        ActionPolicy.EMERGENCY_LEVEL_2,
        ActionPolicy.SESSION_INVALIDATE,
    ],
    ViolationType.RECOVERY_LOOP_DETECTED: [
        ActionPolicy.EMERGENCY_LEVEL_3,  # 전체 보호!
    ],
    
    # 높은 심각도
    ViolationType.TOKEN_FORGED: [
        ActionPolicy.SESSION_INVALIDATE,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.SIGNATURE_INVALID: [
        ActionPolicy.BLOCK_AND_LOG,
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    
    # 기본
    ViolationType.RATE_LIMIT_ABUSE: [
        ActionPolicy.IP_TEMPORARY_BAN,
    ],
    ViolationType.SUSPICIOUS_ACTIVITY: [
        ActionPolicy.BLOCK_AND_LOG,
    ],
}


@dataclass
class ProtectionResult:
    """
    보호 조치 실행 결과.
    
    v2.2.0: triggering_trace_id 추가 - 대시보드에서 "어떤 요청 때문에
    이 사용자의 세션이 무효화되었는가"를 원클릭으로 추적 가능.
    
    Reference: 
    - [tracing.py#L573](../../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/tracing.py#L573)
    - CB에서 triggering_trace_id 동일 네이밍 사용 중
    """
    success: bool
    executed_policies: list[ActionPolicy]
    failed_policies: list[ActionPolicy]
    highest_priority_succeeded: bool
    rolled_back_policies: list[ActionPolicy] = field(default_factory=list)
    rollback_success: bool = True
    error_message: str = ""
    
    # v2.2.0: Tracing Deep Link
    triggering_trace_id: Optional[str] = None
    """보호 조치를 유발한 원본 요청의 trace_id (Jaeger/Zipkin 연동용)."""
    
    triggering_request_path: Optional[str] = None
    """보호 조치를 유발한 원본 요청 경로 (예: POST /api/payments/)."""
    
    def get_trace_url(self, template: str = "") -> Optional[str]:
        """
        Trace UI Deep Link 생성.
        
        Args:
            template: URL 템플릿 (예: "https://jaeger.example.com/trace/{trace_id}")
            
        Returns:
            Deep Link URL 또는 None
        """
        if not self.triggering_trace_id:
            return None
        if not template:
            template = os.environ.get("SELFHEALING_TRACE_URL_TEMPLATE", "")
        if not template:
            return None
        return template.replace("{trace_id}", self.triggering_trace_id)


class ProtectionOrchestrator:
    """
    보호 조치 오케스트레이터.
    
    원자성 보장:
    - 가장 강력한 정책(우선순위 높은)부터 실행
    - 최고 우선순위 정책 실패 시 전체 실패 처리 + 롤백
    - 하위 정책 실패는 경고 로깅 후 계속 진행
    
    롤백 정책 (v2.1.0):
    - 최고 우선순위 실패 시 이미 실행된 정책들을 역순으로 롤백
    - 롤백 가능한 정책만 롤백 시도 (Emergency Mode는 롤백 불가)
    - 롤백 실패 시 로깅 후 수동 개입 권장
    
    Reference: Architect Review - "가장 강력한 정책 우선 성공 보장"
    """
    
    def __init__(self, security_service: "SecurityViolationService"):
        self._service = security_service
        self._policy_executors = {
            ActionPolicy.EMERGENCY_LEVEL_3: self._execute_emergency_3,
            ActionPolicy.EMERGENCY_LEVEL_2: self._execute_emergency_2,
            ActionPolicy.EMERGENCY_LEVEL_1: self._execute_emergency_1,
            ActionPolicy.ACCOUNT_FREEZE: self._execute_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: self._execute_session_invalidate,
            ActionPolicy.IP_PERMANENT_BAN: self._execute_ip_permanent_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._execute_ip_temporary_ban,
            ActionPolicy.BLOCK_AND_LOG: self._execute_block_and_log,
        }
        
        # 롤백 실행자 (롤백 가능한 정책만)
        self._policy_rollback_executors = {
            # Emergency Mode는 롤백 불가 (이미 발동되면 수동 해제 필요)
            # ActionPolicy.EMERGENCY_LEVEL_3: None,  # 롤백 불가
            # ActionPolicy.EMERGENCY_LEVEL_2: None,  # 롤백 불가
            # ActionPolicy.EMERGENCY_LEVEL_1: None,  # 롤백 불가
            ActionPolicy.ACCOUNT_FREEZE: self._rollback_account_freeze,
            ActionPolicy.SESSION_INVALIDATE: None,  # 세션은 롤백 불가 (이미 무효화됨)
            ActionPolicy.IP_PERMANENT_BAN: self._rollback_ip_permanent_ban,
            ActionPolicy.IP_TEMPORARY_BAN: self._rollback_ip_temporary_ban,
            ActionPolicy.BLOCK_AND_LOG: None,  # 로그는 롤백 불가
        }
    
    def execute_policies(
        self,
        policies: list[ActionPolicy],
        context: dict,
    ) -> ProtectionResult:
        """
        정책 목록을 우선순위 순으로 실행.
        
        원자성 보장:
        1. 가장 높은 우선순위 정책 먼저 실행
        2. 최고 우선순위 실패 시 전체 실패 반환 (시스템 잠금 권장)
        3. 하위 정책 실패는 로깅 후 계속 진행
        """
        if not policies:
            return ProtectionResult(
                success=True,
                executed_policies=[],
                failed_policies=[],
                highest_priority_succeeded=True,
            )
        
        # 우선순위 순 정렬 (낮은 숫자 = 높은 우선순위)
        sorted_policies = sorted(
            policies,
            key=lambda p: ACTION_POLICY_PRIORITY.get(p, 999)
        )
        
        executed = []
        failed = []
        highest_priority_policy = sorted_policies[0]
        highest_succeeded = False
        
        for policy in sorted_policies:
            try:
                executor = self._policy_executors.get(policy)
                if executor:
                    executor(context)
                    executed.append(policy)
                    
                    if policy == highest_priority_policy:
                        highest_succeeded = True
                        
            except Exception as e:
                failed.append(policy)
                logger.error(f"[ProtectionOrchestrator] Policy {policy.value} failed: {e}")
                
                # 최고 우선순위 실패 시 즉시 중단 + 롤백 시도
                if policy == highest_priority_policy:
                    # 이미 실행된 정책들 롤백 시도
                    rolled_back, rollback_success = self._rollback_executed_policies(
                        executed, context
                    )
                    
                    return ProtectionResult(
                        success=False,
                        executed_policies=executed,
                        failed_policies=failed,
                        highest_priority_succeeded=False,
                        rolled_back_policies=rolled_back,
                        rollback_success=rollback_success,
                        error_message=f"Highest priority policy failed: {e}",
                    )
        
        return ProtectionResult(
            success=len(failed) == 0,
            executed_policies=executed,
            failed_policies=failed,
            highest_priority_succeeded=highest_succeeded,
        )
    
    def _execute_emergency_3(self, context: dict) -> None:
        """Emergency Level 3 선포."""
        from selfhealing.services.event_bus import get_event_bus, EventType
        
        bus = get_event_bus()
        bus.emit(
            event_type=EventType.EMERGENCY_ACTIVATED,
            data={
                "level": 3,
                "reason": context.get("reason", "Security violation"),
                "trigger_source": "security_violation_service",
                "incident_id": context.get("incident_id"),
            },
            source="protection_orchestrator",
        )
    
    def _execute_emergency_2(self, context: dict) -> None:
        """Emergency Level 2 선포."""
        from selfhealing.services.event_bus import get_event_bus, EventType
        
        bus = get_event_bus()
        bus.emit(
            event_type=EventType.EMERGENCY_ACTIVATED,
            data={
                "level": 2,
                "reason": context.get("reason", "Security violation"),
                "trigger_source": "security_violation_service",
                "incident_id": context.get("incident_id"),
            },
            source="protection_orchestrator",
        )
    
    def _execute_emergency_1(self, context: dict) -> None:
        """Emergency Level 1 선포."""
        from selfhealing.services.event_bus import get_event_bus, EventType
        
        bus = get_event_bus()
        bus.emit(
            event_type=EventType.EMERGENCY_ACTIVATED,
            data={
                "level": 1,
                "reason": context.get("reason", "Security warning"),
                "trigger_source": "security_violation_service",
            },
            source="protection_orchestrator",
        )
    
    def _execute_account_freeze(self, context: dict) -> None:
        """계정 동결."""
        user_id = context.get("user_id")
        if user_id:
            # 구현: 계정 동결 로직
            logger.warning(f"[Protection] Account frozen: user_id={user_id}")
    
    def _execute_session_invalidate(self, context: dict) -> None:
        """세션 무효화."""
        user_id = context.get("user_id")
        if user_id:
            self._service._invalidate_user_sessions(user_id)
    
    def _execute_ip_permanent_ban(self, context: dict) -> None:
        """영구 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._permanent_ip_ban(source_ip)
    
    def _execute_ip_temporary_ban(self, context: dict) -> None:
        """임시 IP 차단."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._temporary_ip_ban(source_ip)
    
    def _execute_block_and_log(self, context: dict) -> None:
        """차단 및 로깅."""
        logger.warning(f"[Protection] Blocked and logged: {context}")
    
    # =========================================================================
    # 롤백 메서드 (v2.1.0)
    # =========================================================================
    
    def _rollback_executed_policies(
        self,
        executed: list[ActionPolicy],
        context: dict,
    ) -> tuple[list[ActionPolicy], bool]:
        """
        이미 실행된 정책들을 역순으로 롤백.
        
        Args:
            executed: 실행된 정책 목록
            context: 실행 컨텍스트
            
        Returns:
            (rolled_back_policies, all_success)
        """
        rolled_back = []
        all_success = True
        
        # 역순으로 롤백 (마지막 실행된 것부터)
        for policy in reversed(executed):
            rollback_fn = self._policy_rollback_executors.get(policy)
            
            if rollback_fn is None:
                # 롤백 불가능한 정책
                logger.warning(
                    f"[ProtectionOrchestrator] Policy {policy.value} cannot be rolled back"
                )
                continue
            
            try:
                rollback_fn(context)
                rolled_back.append(policy)
                logger.info(f"[ProtectionOrchestrator] Rolled back: {policy.value}")
            except Exception as e:
                logger.error(
                    f"[ProtectionOrchestrator] Rollback failed for {policy.value}: {e}"
                )
                all_success = False
        
        if not all_success:
            logger.critical(
                "[ProtectionOrchestrator] Some rollbacks failed! "
                "Manual intervention may be required."
            )
        
        return rolled_back, all_success
    
    def _rollback_account_freeze(self, context: dict) -> None:
        """계정 동결 해제."""
        user_id = context.get("user_id")
        if user_id:
            # 계정 동결 해제 로직
            logger.info(f"[Protection] Account unfrozen: user_id={user_id}")
    
    def _rollback_ip_permanent_ban(self, context: dict) -> None:
        """영구 IP 차단 해제."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._remove_ip_ban(source_ip)
            logger.info(f"[Protection] IP permanent ban removed: {source_ip}")
    
    def _rollback_ip_temporary_ban(self, context: dict) -> None:
        """임시 IP 차단 해제."""
        source_ip = context.get("source_ip")
        if source_ip:
            self._service._remove_ip_ban(source_ip)
            logger.info(f"[Protection] IP temporary ban removed: {source_ip}")
```

#### 8.3.2 Self-Healing Loop ViolationType 추가 (리뷰 ③ 관련)

```python
# security_violation_service.py - ViolationType 추가

class ViolationType(str, Enum):
    # ... 기존 타입들 ...
    
    # ═══════════════════════════════════════════════════════════
    # Self-Healing 루프 감지 관련 (신규 v2.0.0)
    # ═══════════════════════════════════════════════════════════
    RECOVERY_LOOP_DETECTED = "recovery_loop_detected"
    """복구/조정 무한 루프 감지 - 가장 심각."""
    
    CONFLICTING_ADJUSTMENT = "conflicting_adjustment"
    """상충하는 자율 조정 감지 (예: A→B→A 반복)."""
    
    HEALING_TIMEOUT = "healing_timeout"
    """Self-Healing 작업 시간 초과."""
    
    FLAPPING_DETECTED = "flapping_detected"
    """파라미터 플래핑 감지 (미세 조정 반복)."""


# Severity 매핑 추가
SEVERITY_BY_VIOLATION_TYPE.update({
    ViolationType.RECOVERY_LOOP_DETECTED: Severity.CRITICAL,  # 즉시 전체 차단
    ViolationType.CONFLICTING_ADJUSTMENT: Severity.HIGH,
    ViolationType.HEALING_TIMEOUT: Severity.MEDIUM,
    ViolationType.FLAPPING_DETECTED: Severity.HIGH,
})
```

#### 8.3.3 Anti-Flapping IdempotencyKey (리뷰 ②)

**목적**: AI 자율 조정의 미세 진동 방지

```python
# idempotency_service.py 추가

import time
from collections import defaultdict
from threading import Lock


class AntiFlappingWindow:
    """
    Anti-Flapping 윈도우 (슬라이딩 윈도우 기반).
    
    동일하거나 유사한 값이 짧은 시간 내 반복되는 것을 감지.
    
    분산 환경 지원 (v2.1.0):
    - Redis 사용 가능 시: ZSET 기반 분산 슬라이딩 윈도우
    - Redis 미사용 시: 메모리 기반 로컬 윈도우 (기존 동작)
    
    Reference: 
    - Architect Review: "1% 미만의 조정 반복을 중복/루프로 간주"
    - 기존 SlidingWindowThrottle 패턴 재사용
    - [state_backend.py](../../../packages/selfhealing-python/src/selfhealing/core/state_backend.py)
    """
    
    REDIS_KEY_PREFIX = "selfhealing:anti_flapping:"
    
    def __init__(
        self,
        window_seconds: int = 60,
        similarity_threshold: float = 0.01,  # 1% 이내 = 유사
        max_similar_changes: int = 3,
        use_redis: bool = True,  # Redis 사용 여부
    ):
        self.window_seconds = window_seconds
        self.similarity_threshold = similarity_threshold
        self.max_similar_changes = max_similar_changes
        self._use_redis = use_redis
        
        # 메모리 기반 로컬 윈도우 (fallback)
        # key -> [(timestamp, value), ...]
        self._windows: dict[str, list[tuple[float, float]]] = defaultdict(list)
        self._lock = Lock()
        
        # Redis 클라이언트 초기화
        self._redis_client = None
        if use_redis:
            self._init_redis_client()
    
    def _init_redis_client(self) -> None:
        """Redis 클라이언트 초기화."""
        try:
            from selfhealing.core.state_backend import get_state_backend, RedisStateBackend
            
            backend = get_state_backend()
            if isinstance(backend, RedisStateBackend):
                self._redis_client = backend._client
                logger.info("[AntiFlappingWindow] Redis mode enabled (distributed)")
            else:
                logger.info("[AntiFlappingWindow] File backend detected, using memory mode")
        except Exception as e:
            logger.warning(f"[AntiFlappingWindow] Redis init failed, using memory: {e}")
    
    def check_and_record(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """
        새 값이 플래핑인지 확인하고 기록.
        
        Args:
            key: 파라미터 키 (예: "circuit_breaker:threshold")
            new_value: 새로운 값
            
        Returns:
            (is_flapping, reason)
        """
        if self._redis_client:
            return self._check_and_record_redis(key, new_value)
        else:
            return self._check_and_record_memory(key, new_value)
    
    def _check_and_record_redis(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """
        Redis ZSET 기반 분산 슬라이딩 윈도우.
        
        ZSET 활용:
        - score: timestamp
        - member: "timestamp:value" 문자열
        - ZRANGEBYSCORE로 윈도우 내 값들 조회
        - ZREMRANGEBYSCORE로 만료된 엔트리 제거
        """
        redis_key = f"{self.REDIS_KEY_PREFIX}{key}"
        now = time.time()
        window_start = now - self.window_seconds
        
        try:
            pipe = self._redis_client.pipeline()
            
            # 1. 오래된 엔트리 제거
            pipe.zremrangebyscore(redis_key, "-inf", window_start)
            
            # 2. 현재 윈도우 내 모든 엔트리 조회
            pipe.zrangebyscore(redis_key, window_start, "+inf", withscores=True)
            
            results = pipe.execute()
            entries = results[1]  # [(member, score), ...]
            
            # 3. 유사한 값 변경 횟수 계산
            similar_count = 0
            for member, _ in entries:
                # member 형식: "timestamp:value"
                try:
                    _, val_str = member.split(":", 1)
                    val = float(val_str)
                    if self._is_similar(val, new_value):
                        similar_count += 1
                except (ValueError, AttributeError):
                    continue
            
            # 4. 플래핑 감지
            if similar_count >= self.max_similar_changes:
                return True, f"Flapping detected: {similar_count} similar changes in {self.window_seconds}s"
            
            # 5. 현재 값 기록
            member = f"{now}:{new_value}"
            self._redis_client.zadd(redis_key, {member: now})
            
            # 6. TTL 설정 (윈도우 * 2로 안전하게)
            self._redis_client.expire(redis_key, self.window_seconds * 2)
            
            return False, ""
            
        except Exception as e:
            logger.warning(f"[AntiFlappingWindow] Redis error, fallback to memory: {e}")
            return self._check_and_record_memory(key, new_value)
    
    def _check_and_record_memory(
        self,
        key: str,
        new_value: float,
    ) -> tuple[bool, str]:
        """메모리 기반 로컬 슬라이딩 윈도우 (기존 로직)."""
        now = time.time()
        window_start = now - self.window_seconds
        
        with self._lock:
            # 슬라이딩 윈도우: 오래된 엔트리 제거
            self._windows[key] = [
                (ts, val) for ts, val in self._windows[key]
                if ts > window_start
            ]
            
            # 유사한 값 변경 횟수 계산
            similar_count = 0
            for ts, val in self._windows[key]:
                if self._is_similar(val, new_value):
                    similar_count += 1
            
            # 플래핑 감지
            if similar_count >= self.max_similar_changes:
                return True, f"Flapping detected: {similar_count} similar changes in {self.window_seconds}s"
            
            # 현재 값 기록
            self._windows[key].append((now, new_value))
            
            return False, ""
    
    def _is_similar(self, val1: float, val2: float) -> bool:
        """두 값이 유사한지 확인 (threshold 이내)."""
        if val1 == 0 and val2 == 0:
            return True
        if val1 == 0 or val2 == 0:
            return False
        
        diff_ratio = abs(val1 - val2) / max(abs(val1), abs(val2))
        return diff_ratio <= self.similarity_threshold


# 전역 Anti-Flapping 윈도우
_anti_flapping_window: AntiFlappingWindow | None = None


def get_anti_flapping_window() -> AntiFlappingWindow:
    """Get singleton AntiFlappingWindow."""
    global _anti_flapping_window
    if _anti_flapping_window is None:
        _anti_flapping_window = AntiFlappingWindow()
    return _anti_flapping_window


@dataclass
class IdempotencyKey:
    # ... 기존 필드 및 메서드 ...
    
    @classmethod
    def for_config_change(
        cls,
        config_key: str,
        new_value_hash: str,
        changed_by: str,
        # v2.0.0 추가: 슬라이딩 윈도우 기반 멱등성
        request_id: Optional[str] = None,
        window_id: Optional[str] = None,
    ) -> "IdempotencyKey":
        """
        설정 변경에 대한 멱등성 키 생성.
        
        Args:
            config_key: 설정 키
            new_value_hash: 새 값의 해시
            changed_by: 변경 주체
            request_id: 요청 ID (동일 요청 재시도만 중복 처리)
            window_id: 슬라이딩 윈도우 ID (시간 창 기반, 권장)
            
        멱등성 범위 정책:
        - request_id 제공: 동일 요청의 재시도만 중복
        - window_id 제공: 동일 윈도우 내 동일 변경만 중복
        - 둘 다 없음: new_value_hash 기준 (기존 동작)
        
        Reference: Architect Review - "의도된 재설정 vs 중복 구분"
        """
        if request_id:
            # 요청 단위 멱등성 (가장 엄격)
            key = f"config:{config_key}:{request_id}"
        elif window_id:
            # 슬라이딩 윈도우 기반 (권장)
            key = f"config:{config_key}:{new_value_hash}:w{window_id}"
        else:
            # 기존 동작 (값 기반)
            key = f"config:{config_key}:{new_value_hash}"
        
        return cls(
            domain=IdempotencyDomain.CONFIG_CHANGE,
            key=key,
            components={
                "config_key": config_key,
                "new_value_hash": new_value_hash,
                "changed_by": changed_by,
                "request_id": request_id,
                "window_id": window_id,
            },
        )
    
    @classmethod
    def for_auto_adjustment(
        cls,
        module: str,
        parameter: str,
        target_value: str,
        # v2.0.0 추가: Anti-Flapping 체크
        check_flapping: bool = True,
        numeric_value: Optional[float] = None,
    ) -> tuple["IdempotencyKey", bool, str]:
        """
        자율 조정에 대한 멱등성 키 생성 (Anti-Flapping 포함).
        
        Args:
            module: 모듈 이름 (circuit_breaker, retry 등)
            parameter: 파라미터 이름
            target_value: 목표 값 (문자열)
            check_flapping: 플래핑 체크 여부
            numeric_value: 숫자 값 (플래핑 체크용)
            
        Returns:
            (IdempotencyKey, is_flapping, flapping_reason)
            
        Reference: Architect Review - "유사한 값 반복도 중복/루프로 간주"
        """
        key = f"adjust:{module}:{parameter}:{target_value}"
        
        idem_key = cls(
            domain=IdempotencyDomain.AUTO_ADJUSTMENT,
            key=key,
            components={
                "module": module,
                "parameter": parameter,
                "target_value": target_value,
            },
        )
        
        # Anti-Flapping 체크
        is_flapping = False
        flapping_reason = ""
        
        if check_flapping and numeric_value is not None:
            flapping_key = f"{module}:{parameter}"
            window = get_anti_flapping_window()
            is_flapping, flapping_reason = window.check_and_record(
                flapping_key,
                numeric_value,
            )
        
        return idem_key, is_flapping, flapping_reason
```

#### 8.3.4 Escape Strategy - LearningService 블랙리스트 (리뷰 ③)

**목적**: 루프 감지 후 "왜 고장났는지 학습하고 다음에 반복하지 않음"

```python
# learning/service.py 확장

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Set
from enum import Enum


class BlacklistReason(str, Enum):
    """블랙리스트 등록 사유."""
    RECOVERY_LOOP = "recovery_loop"
    CONFLICTING_ADJUSTMENT = "conflicting_adjustment"
    FLAPPING = "flapping"
    MANUAL_BLOCK = "manual_block"


@dataclass
class BlacklistedParameter:
    """블랙리스트된 파라미터."""
    module: str
    parameter: str
    blocked_values: Set[str]  # 차단된 값들
    reason: BlacklistReason
    registered_at: datetime
    registered_by: str  # "system" 또는 사용자 ID
    incident_id: Optional[int] = None
    expires_at: Optional[datetime] = None  # None = 영구


class ParameterBlacklist:
    """
    파라미터 블랙리스트 관리.
    
    루프/플래핑 감지 시 해당 파라미터 조합을 블랙리스트에 등록하여
    동일 실수를 반복하지 않도록 함.
    
    영속성 (v2.1.0):
    - StateBackend를 통한 영속성 보장
    - Redis 모드: 분산 환경에서 공유 (RedisStateBackend)
    - File 모드: 단일 서버에서 재시작 시에도 유지 (FileStateBackend)
    - DB 의존성 없음 - 기존 시스템 인프라 활용
    
    Reference:
    - Architect Review - "고장 원인 학습 및 반복 방지"
    - [state_backend.py](../../../packages/selfhealing-python/src/selfhealing/core/state_backend.py)
    """
    
    STORAGE_KEY = "selfhealing:parameter_blacklist"
    
    def __init__(self):
        # 메모리 캐시 (빠른 조회용)
        self._blacklist: Dict[str, BlacklistedParameter] = {}
        
        # StateBackend (영속성 - Redis 또는 File)
        from selfhealing.core.state_backend import get_state_backend
        self._backend = get_state_backend()
        
        # 시작 시 저장소에서 로드
        self._load_from_storage()
    
    def register(
        self,
        module: str,
        parameter: str,
        blocked_values: Set[str],
        reason: BlacklistReason,
        registered_by: str = "system",
        incident_id: Optional[int] = None,
        ttl_hours: Optional[int] = None,
    ) -> BlacklistedParameter:
        """
        파라미터를 블랙리스트에 등록.
        
        Args:
            module: 모듈 이름
            parameter: 파라미터 이름
            blocked_values: 차단할 값들
            reason: 등록 사유
            registered_by: 등록 주체
            incident_id: 관련 인시던트 ID
            ttl_hours: 만료 시간 (None = 영구)
            
        Returns:
            등록된 BlacklistedParameter
        """
        key = f"{module}:{parameter}"
        
        expires_at = None
        if ttl_hours:
            expires_at = datetime.now() + timedelta(hours=ttl_hours)
        
        entry = BlacklistedParameter(
            module=module,
            parameter=parameter,
            blocked_values=blocked_values,
            reason=reason,
            registered_at=datetime.now(),
            registered_by=registered_by,
            incident_id=incident_id,
            expires_at=expires_at,
        )
        
        self._blacklist[key] = entry
        
        # StateBackend에 영속화
        self._save_to_storage()
        
        logger.warning(
            f"[ParameterBlacklist] Registered: {key} "
            f"blocked_values={blocked_values} reason={reason.value}"
        )
        
        return entry
    
    def is_blocked(
        self,
        module: str,
        parameter: str,
        value: str,
    ) -> tuple[bool, Optional[BlacklistedParameter]]:
        """
        값이 블랙리스트에 있는지 확인.
        
        Returns:
            (is_blocked, entry)
        """
        key = f"{module}:{parameter}"
        entry = self._blacklist.get(key)
        
        if not entry:
            return False, None
        
        # 만료 확인
        if entry.expires_at and datetime.now() > entry.expires_at:
            del self._blacklist[key]
            return False, None
        
        if value in entry.blocked_values:
            return True, entry
        
        return False, None
    
    def get_all(self) -> List[BlacklistedParameter]:
        """모든 블랙리스트 항목 조회 (만료 제거 후)."""
        now = datetime.now()
        
        # 만료된 항목 제거
        expired_keys = [
            k for k, v in self._blacklist.items()
            if v.expires_at and now > v.expires_at
        ]
        for k in expired_keys:
            del self._blacklist[k]
        
        return list(self._blacklist.values())
    
    def unregister(self, module: str, parameter: str) -> bool:
        """블랙리스트에서 제거 (수동)."""
        key = f"{module}:{parameter}"
        if key in self._blacklist:
            del self._blacklist[key]
            self._save_to_storage()  # 영속화
            logger.info(f"[ParameterBlacklist] Unregistered: {key}")
            return True
        return False
    
    # =========================================================================
    # 영속성 메서드 (v2.1.0) - StateBackend 활용
    # =========================================================================
    
    def _load_from_storage(self) -> None:
        """
        StateBackend에서 블랙리스트 로드.
        
        StateBackend는 Redis 또는 File 기반으로 동작:
        - Redis: 분산 환경에서 모든 서버가 동일한 블랙리스트 공유
        - File: 단일 서버에서 재시작 시에도 블랙리스트 유지
        """
        try:
            stored = self._backend.get(self.STORAGE_KEY)
            if stored:
                for key, entry_dict in stored.items():
                    self._blacklist[key] = BlacklistedParameter(
                        module=entry_dict["module"],
                        parameter=entry_dict["parameter"],
                        blocked_values=set(entry_dict["blocked_values"]),
                        reason=BlacklistReason(entry_dict["reason"]),
                        registered_at=datetime.fromisoformat(entry_dict["registered_at"]),
                        registered_by=entry_dict["registered_by"],
                        incident_id=entry_dict.get("incident_id"),
                        expires_at=(
                            datetime.fromisoformat(entry_dict["expires_at"])
                            if entry_dict.get("expires_at")
                            else None
                        ),
                    )
                logger.info(
                    f"[ParameterBlacklist] Loaded {len(self._blacklist)} entries from storage"
                )
        except Exception as e:
            logger.warning(f"[ParameterBlacklist] Failed to load from storage: {e}")
    
    def _save_to_storage(self) -> None:
        """
        StateBackend에 블랙리스트 저장.
        
        직렬화 가능한 형태로 변환하여 저장.
        """
        try:
            serialized = {}
            for key, entry in self._blacklist.items():
                serialized[key] = {
                    "module": entry.module,
                    "parameter": entry.parameter,
                    "blocked_values": list(entry.blocked_values),
                    "reason": entry.reason.value,
                    "registered_at": entry.registered_at.isoformat(),
                    "registered_by": entry.registered_by,
                    "incident_id": entry.incident_id,
                    "expires_at": (
                        entry.expires_at.isoformat() if entry.expires_at else None
                    ),
                }
            
            self._backend.set(self.STORAGE_KEY, serialized)
            logger.debug(f"[ParameterBlacklist] Saved {len(serialized)} entries to storage")
        except Exception as e:
            logger.error(f"[ParameterBlacklist] Failed to save to storage: {e}")


# LearningService 확장
class LearningService:
    # ... 기존 코드 ...
    
    def __init__(self):
        # ... 기존 초기화 ...
        self._parameter_blacklist = ParameterBlacklist()
    
    def register_dangerous_parameter(
        self,
        module: str,
        parameter: str,
        blocked_values: Set[str],
        reason: BlacklistReason,
        incident_id: Optional[int] = None,
    ) -> BlacklistedParameter:
        """
        위험한 파라미터 조합 등록.
        
        루프/플래핑 감지 시 호출되어 향후 동일 조합 사용을 차단.
        
        Reference: Architect Review - Escape Strategy
        """
        # 블랙리스트 등록
        entry = self._parameter_blacklist.register(
            module=module,
            parameter=parameter,
            blocked_values=blocked_values,
            reason=reason,
            registered_by="system",
            incident_id=incident_id,
            ttl_hours=24 * 7,  # 기본 7일 후 만료 (학습 기회 제공)
        )
        
        # FAILURE 패턴으로도 학습
        self.learn_pattern(
            pattern_type=PatternType.FAILURE,
            name=f"DangerousParameter:{module}:{parameter}",
            description=f"Parameter combination caused {reason.value}",
            features={
                "module": module,
                "parameter": parameter,
                "blocked_values": list(blocked_values),
            },
            confidence=0.95,
            metadata={
                "incident_id": incident_id,
                "blacklist_reason": reason.value,
            },
        )
        
        return entry
    
    def is_parameter_blocked(
        self,
        module: str,
        parameter: str,
        value: str,
    ) -> tuple[bool, Optional[BlacklistedParameter]]:
        """파라미터 조합이 차단되었는지 확인."""
        return self._parameter_blacklist.is_blocked(module, parameter, value)
    
    def set_manual_only_mode(self, module: str, enabled: bool = True) -> None:
        """
        모듈을 Manual Only 모드로 전환.
        
        루프 감지 시 자동 조정을 비활성화하고 수동 조정만 허용.
        
        Reference: Architect Review - "해당 모듈을 Manual Only 모드로 전환"
        """
        if enabled:
            self.learn_pattern(
                pattern_type=PatternType.FAILURE,
                name=f"ManualOnlyMode:{module}",
                description=f"Module {module} switched to manual-only mode",
                features={"module": module, "manual_only": True},
                confidence=1.0,
            )
            logger.warning(f"[LearningService] Module '{module}' is now MANUAL ONLY")
        else:
            # 해당 패턴 제거 (구현 생략)
            logger.info(f"[LearningService] Module '{module}' autonomous mode restored")
```

#### 8.3.5 CRITICAL Severity → Error Budget 연동 (Q3 답변 관련)

```python
# event_bus.py 핸들러 추가

def _on_security_violation_critical(event: SelfHealingEvent) -> None:
    """
    CRITICAL 보안 위반 시 Error Budget 강제 소진.
    
    Reference: Architect Q3 - "CRITICAL → Error Budget 0 연동"
    """
    violation_type = event.data.get("violation_type")
    severity = event.data.get("severity")
    
    if severity != "critical":
        return
    
    logger.critical(
        f"[EventHandler] CRITICAL security violation: {violation_type} "
        "- Forcing error budget consumption"
    )
    
    try:
        from selfhealing.services.error_budget import get_error_budget_service
        
        service = get_error_budget_service()
        # 인위적 에러 주입으로 버짓 소진
        # ErrorBudgetService._simulated_errors 활용
        service._simulated_errors += 1000  # 대량 에러 주입
        
        logger.warning(
            f"[EventHandler] Error budget force consumed for {violation_type}"
        )
    except Exception as e:
        logger.error(f"[EventHandler] Failed to consume error budget: {e}")


# EventType 추가
class EventType(Enum):
    # ... 기존 ...
    
    # Security Violation Events (신규)
    SECURITY_VIOLATION_DETECTED = "security_violation_detected"
    SECURITY_VIOLATION_CRITICAL = "security_violation_critical"


# 핸들러 등록
def register_default_handlers():
    # ... 기존 핸들러 ...
    
    bus.subscribe(
        EventType.SECURITY_VIOLATION_CRITICAL,
        _on_security_violation_critical,
    )
```

---

### 8.4 구현 흐름도 (v2.0.0)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Security Violation 발생                               │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ SecurityViolationService.handle_violation()                              │
│   1. Repository.create() → Audit 저장                                    │
│   2. ViolationType 확인                                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ ACTION_POLICY_BY_VIOLATION_TYPE 조회                                     │
│   예: PRIVILEGE_ESCALATION → [EMERGENCY_LEVEL_2, SESSION_INVALIDATE, ...]│
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ ProtectionOrchestrator.execute_policies()                                │
│   1. 우선순위 정렬 (높은 것 먼저)                                         │
│   2. 가장 강력한 정책 먼저 실행                                           │
│   3. 최고 우선순위 실패 시 전체 실패 반환                                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
┌──────────────────────────────┐    ┌──────────────────────────────────────┐
│ Emergency Mode 선포           │    │ Session Invalidate / IP Ban          │
│ (EventBus → EmergencyManager)│    │ (직접 실행)                           │
└──────────────────────────────┘    └──────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ RECOVERY_LOOP_DETECTED인 경우:                                           │
│   1. EMERGENCY_LEVEL_3 선포 (전체 보호)                                  │
│   2. LearningService.register_dangerous_parameter() 호출                 │
│   3. LearningService.set_manual_only_mode(module, True)                  │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ CRITICAL Severity인 경우:                                                │
│   EventBus.emit(SECURITY_VIOLATION_CRITICAL)                             │
│   → _on_security_violation_critical → Error Budget 강제 소진             │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### 8.5 Auto-Adjustment 플래핑 방지 흐름

```
┌─────────────────────────────────────────────────────────────────────────┐
│ Auto-Tuning Engine: 파라미터 조정 요청                                    │
│   예: circuit_breaker.threshold = 0.51 → 0.50                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ IdempotencyKey.for_auto_adjustment(check_flapping=True)                  │
│   1. 슬라이딩 윈도우에서 최근 조정 이력 조회                               │
│   2. 유사한 값 변경 횟수 계산 (1% 이내 = 유사)                            │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
          is_flapping = True                is_flapping = False
                    │                               │
                    ▼                               ▼
┌──────────────────────────────┐    ┌──────────────────────────────────────┐
│ 1. 조정 차단                  │    │ 조정 실행                             │
│ 2. FLAPPING_DETECTED 위반 기록│    │ 윈도우에 현재 값 기록                  │
│ 3. LearningService 블랙리스트 │    └──────────────────────────────────────┘
│ 4. Manual Only 모드 전환      │
└──────────────────────────────┘
```

---

## 9. 테스트 계획 (v2.0.0 확장)

### 9.1 ProtectionOrchestrator 테스트

```python
# tests/self_healing/unit/test_protection_orchestrator.py

class TestProtectionOrchestrator:
    """ProtectionOrchestrator 테스트."""
    
    def test_highest_priority_executed_first(self):
        """가장 높은 우선순위 정책이 먼저 실행되는지 확인."""
        policies = [
            ActionPolicy.IP_TEMPORARY_BAN,  # Priority 7
            ActionPolicy.EMERGENCY_LEVEL_2,  # Priority 2
            ActionPolicy.BLOCK_AND_LOG,  # Priority 8
        ]
        
        orchestrator = ProtectionOrchestrator(mock_service)
        result = orchestrator.execute_policies(policies, context={})
        
        # EMERGENCY_LEVEL_2가 먼저 실행되어야 함
        assert result.executed_policies[0] == ActionPolicy.EMERGENCY_LEVEL_2
    
    def test_highest_priority_failure_stops_execution(self):
        """최고 우선순위 실패 시 전체 실패 반환."""
        # EMERGENCY_LEVEL_2 실패 시뮬레이션
        with patch.object(orchestrator, '_execute_emergency_2', side_effect=Exception("fail")):
            result = orchestrator.execute_policies(
                [ActionPolicy.EMERGENCY_LEVEL_2, ActionPolicy.IP_TEMPORARY_BAN],
                context={},
            )
        
        assert result.success is False
        assert result.highest_priority_succeeded is False
        assert ActionPolicy.EMERGENCY_LEVEL_2 in result.failed_policies
```

### 9.2 Anti-Flapping 테스트

```python
# tests/self_healing/unit/test_anti_flapping.py

class TestAntiFlappingWindow:
    """Anti-Flapping 윈도우 테스트."""
    
    def test_similar_values_detected_as_flapping(self):
        """1% 이내 유사 값 반복이 플래핑으로 감지되는지 확인."""
        window = AntiFlappingWindow(
            window_seconds=60,
            similarity_threshold=0.01,
            max_similar_changes=3,
        )
        
        key = "circuit_breaker:threshold"
        
        # 유사한 값 4번 변경
        window.check_and_record(key, 0.50)
        window.check_and_record(key, 0.505)  # 1% 이내
        window.check_and_record(key, 0.502)  # 1% 이내
        is_flapping, reason = window.check_and_record(key, 0.501)  # 4번째
        
        assert is_flapping is True
        assert "Flapping detected" in reason
```

### 9.3 LearningService 블랙리스트 테스트

```python
# tests/self_healing/unit/test_learning_blacklist.py

class TestParameterBlacklist:
    """파라미터 블랙리스트 테스트."""
    
    def test_blocked_parameter_rejected(self):
        """블랙리스트된 파라미터가 차단되는지 확인."""
        service = LearningService()
        
        service.register_dangerous_parameter(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1", "0.2"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )
        
        is_blocked, entry = service.is_parameter_blocked(
            "circuit_breaker", "threshold", "0.1"
        )
        
        assert is_blocked is True
        assert entry.reason == BlacklistReason.RECOVERY_LOOP
```

### 9.4 롤백 로직 테스트 (v2.1.0)

```python
# tests/self_healing/unit/test_protection_rollback.py

class TestProtectionRollback:
    """보호 조치 롤백 테스트."""
    
    def test_rollback_executed_on_highest_priority_failure(self):
        """최고 우선순위 실패 시 이미 실행된 정책이 롤백되는지 확인."""
        # IP_TEMPORARY_BAN이 먼저 실행되고, EMERGENCY_LEVEL_2가 실패하는 시나리오
        # (실제로는 EMERGENCY가 먼저 실행되지만, 테스트를 위해 순서 변경)
        orchestrator = ProtectionOrchestrator(mock_service)
        
        # IP ban 성공 후 Emergency 실패 시뮬레이션
        with patch.object(orchestrator, '_execute_ip_temporary_ban'):
            with patch.object(orchestrator, '_execute_emergency_2', side_effect=Exception("fail")):
                result = orchestrator.execute_policies(
                    [ActionPolicy.IP_TEMPORARY_BAN, ActionPolicy.EMERGENCY_LEVEL_2],
                    context={"source_ip": "1.2.3.4"},
                )
        
        # 롤백 확인
        assert result.success is False
        assert ActionPolicy.IP_TEMPORARY_BAN in result.rolled_back_policies
    
    def test_rollback_failure_logged_as_critical(self):
        """롤백 실패 시 CRITICAL 로그가 기록되는지 확인."""
        orchestrator = ProtectionOrchestrator(mock_service)
        
        with patch.object(orchestrator, '_rollback_ip_temporary_ban', side_effect=Exception("rollback fail")):
            rolled_back, success = orchestrator._rollback_executed_policies(
                [ActionPolicy.IP_TEMPORARY_BAN],
                context={"source_ip": "1.2.3.4"},
            )
        
        assert success is False
        assert ActionPolicy.IP_TEMPORARY_BAN not in rolled_back
    
    def test_non_rollbackable_policies_skipped(self):
        """롤백 불가능한 정책(Emergency, Session)은 스킵되는지 확인."""
        orchestrator = ProtectionOrchestrator(mock_service)
        
        # Emergency Mode는 롤백 불가
        rolled_back, success = orchestrator._rollback_executed_policies(
            [ActionPolicy.EMERGENCY_LEVEL_2, ActionPolicy.BLOCK_AND_LOG],
            context={},
        )
        
        # 둘 다 롤백 불가이므로 rolled_back은 비어있음
        assert len(rolled_back) == 0
        assert success is True  # 스킵은 실패가 아님
```

### 9.5 StateBackend 기반 영속성 테스트 (v2.1.0)

```python
# tests/self_healing/unit/test_blacklist_persistence.py

class TestBlacklistPersistence:
    """블랙리스트 StateBackend 영속성 테스트."""
    
    def test_blacklist_persisted_to_state_backend(self):
        """블랙리스트가 StateBackend에 저장되는지 확인."""
        from selfhealing.core.state_backend import get_state_backend
        
        blacklist = ParameterBlacklist()
        blacklist.register(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )
        
        # StateBackend에서 직접 조회
        backend = get_state_backend()
        stored = backend.get(ParameterBlacklist.STORAGE_KEY)
        
        assert stored is not None
        assert "circuit_breaker:threshold" in stored
    
    def test_blacklist_restored_after_restart(self):
        """재시작 후 블랙리스트가 복원되는지 확인."""
        from selfhealing.core.state_backend import get_state_backend
        
        # 1. 저장
        blacklist1 = ParameterBlacklist()
        blacklist1.register(
            module="retry",
            parameter="max_attempts",
            blocked_values={"100"},
            reason=BlacklistReason.FLAPPING,
        )
        
        # 2. 새 인스턴스 생성 (재시작 시뮬레이션)
        blacklist2 = ParameterBlacklist()
        
        # 3. 복원 확인
        is_blocked, _ = blacklist2.is_blocked("retry", "max_attempts", "100")
        assert is_blocked is True

### 9.6 Anti-Flapping Redis 분산 테스트 (v2.1.0)

```python
# tests/self_healing/integration/test_anti_flapping_redis.py

@pytest.mark.redis
class TestAntiFlappingRedis:
    """Anti-Flapping Redis 분산 환경 테스트."""
    
    def test_flapping_detected_across_servers(self):
        """서로 다른 서버에서의 조정이 하나의 윈도우로 집계되는지 확인."""
        # 두 개의 AntiFlappingWindow 인스턴스 (서로 다른 서버 시뮬레이션)
        window1 = AntiFlappingWindow(use_redis=True)
        window2 = AntiFlappingWindow(use_redis=True)
        
        key = "circuit_breaker:threshold"
        
        # 서버 1에서 2번 조정
        window1.check_and_record(key, 0.50)
        window1.check_and_record(key, 0.505)
        
        # 서버 2에서 2번 조정 (총 4번 → 플래핑)
        window2.check_and_record(key, 0.502)
        is_flapping, reason = window2.check_and_record(key, 0.501)
        
        assert is_flapping is True
    
    def test_fallback_to_memory_on_redis_failure(self):
        """Redis 실패 시 메모리로 폴백되는지 확인."""
        window = AntiFlappingWindow(use_redis=True)
        
        # Redis 연결 강제 끊기
        window._redis_client = None
        
        # 여전히 동작해야 함 (메모리 모드)
        is_flapping, _ = window.check_and_record("test:key", 0.5)
        assert is_flapping is False
```

---

## 10. 설계 결정 요약 (v2.1.0)

### 10.1 영속성 설계: DB vs StateBackend

| 항목 | DB 직접 사용 | StateBackend (채택) |
|------|-------------|---------------------|
| 외부 시스템 의존성 | ❌ 상대 시스템 침투 | ✅ 자체 인프라만 사용 |
| 기존 패턴 재사용 | ❌ 별도 구현 필요 | ✅ RuntimeConfig, ChaosScheduler와 동일 패턴 |
| Redis 분산 지원 | 별도 구현 필요 | ✅ RedisStateBackend 자동 전환 |
| File 단일서버 지원 | 별도 구현 필요 | ✅ FileStateBackend 자동 전환 |
| 코드 근거 | - | [state_backend.py](../../../packages/selfhealing-python/src/selfhealing/core/state_backend.py) |

**결정 근거**:
1. 시스템 설계 철학: "상대 시스템 침투 X 및 의존성 최소화"
2. 기존 `RuntimeConfigManager`, `ChaosScheduler`가 동일 패턴 사용 중
3. DB 스키마 변경 없이 영속성 확보

### 10.2 롤백 정책 결정

| 정책 유형 | 롤백 가능 여부 | 이유 |
|----------|--------------|------|
| EMERGENCY_LEVEL_* | ❌ 불가 | 이미 시스템 상태 변경됨, 수동 해제 필요 |
| SESSION_INVALIDATE | ❌ 불가 | 세션 이미 무효화됨, 재로그인 필요 |
| ACCOUNT_FREEZE | ✅ 가능 | 동결 상태 플래그 해제 가능 |
| IP_*_BAN | ✅ 가능 | 차단 목록에서 제거 가능 |
| BLOCK_AND_LOG | ❌ 불가 | 로그는 삭제 불가 (immutable) |

### 10.3 Anti-Flapping 저장소 선택

| 저장소 | 장점 | 단점 | 선택 |
|--------|------|------|------|
| 메모리 | 빠름, 단순 | 단일 서버만, 재시작 시 손실 | 폴백용 |
| Redis ZSET | 분산 환경 지원, TTL 자동 | Redis 의존성 | ✅ 메인 |
| DB | 영구 저장 | 너무 무거움, 불필요 | ❌ |

**결정**: Redis ZSET (메인) + 메모리 (폴백)
- 플래핑 윈도우는 단기간(60초) 데이터
- 재시작해도 손실 영향 미미
- Redis 실패 시 메모리로 Graceful Degradation

---

## 11. v2.2.0 아키텍트 추가 리뷰 반영

### 11.1 순환 참조 방지: Local Import 패턴 가이드 (리뷰 ①)

**리뷰 내용**: "EventBus에서 emit 시 순환 참조 발생 가능성 → Local Import 패턴 권장"

**코드 근거**: 현재 시스템에서 이미 Local Import 패턴이 표준으로 사용되고 있음.

```python
# 예시: emergency_mode/manager.py#L79
def _register_event_handlers(self) -> None:
    """이벤트 핸들러 등록 (순환 참조 방지를 위해 Local Import 사용)."""
    from selfhealing.services.event_bus import get_event_bus, EventType  # ← Local Import
    
    event_bus = get_event_bus()
    event_bus.subscribe(EventType.RECOVERY_LOOP_DETECTED, self._on_recovery_loop)
```

**Reference Files**:
- [emergency_mode/manager.py#L79](../../../packages/selfhealing-python/src/selfhealing/services/emergency_mode/manager.py#L79)
- [circuit_breaker/service.py](../../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/service.py) - 동일 패턴 사용
- [error_budget_gate/gate.py](../../../packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py) - 동일 패턴 사용

**확장 구현 가이드라인**:

```python
# ❌ WRONG: 모듈 상단에서 import (순환 참조 발생)
from selfhealing.services.event_bus import get_event_bus, EventType
from selfhealing.services.learning import LearningService

class MyService:
    def __init__(self):
        self.event_bus = get_event_bus()  # 초기화 시점에 순환 참조

# ✅ CORRECT: 함수 내부에서 Local Import
class MyService:
    def __init__(self):
        pass  # 초기화 시점에는 import 안함
    
    def emit_event(self, data: dict) -> None:
        """이벤트 발행 (Local Import로 순환 참조 방지)."""
        from selfhealing.services.event_bus import get_event_bus, EventType
        
        event_bus = get_event_bus()
        event_bus.emit(EventType.SECURITY_VIOLATION_DETECTED, data)
    
    def subscribe_events(self) -> None:
        """이벤트 구독 등록."""
        from selfhealing.services.event_bus import get_event_bus, EventType
        from selfhealing.services.learning import LearningService  # 핸들러에서만 필요
        
        event_bus = get_event_bus()
        event_bus.subscribe(EventType.VIOLATION_RESOLVED, self._on_resolved)
```

**추가 권장사항**:
1. `TYPE_CHECKING`을 활용한 타입 힌트 분리
2. Lazy initialization 패턴 적용

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.services.event_bus import EventBus

class MyService:
    _event_bus: Optional["EventBus"] = None
    
    @property
    def event_bus(self) -> "EventBus":
        """Lazy initialization으로 순환 참조 방지."""
        if self._event_bus is None:
            from selfhealing.services.event_bus import get_event_bus
            self._event_bus = get_event_bus()
        return self._event_bus
```

---

### 11.2 블랙리스트 수동 해제 Admin API (리뷰 ②)

**리뷰 내용**: "블랙리스트된 항목을 수동으로 해제할 수 있는 관리 API 필요"

**코드 근거**: 현재 governance 폴더에 블랙리스트 관리 API가 없음.

**Reference Files**:
- [governance/views.py](../../../packages/selfhealing-python/src/selfhealing/api/governance/views.py) - 기존 API 패턴 참고
- [governance/urls.py](../../../packages/selfhealing-python/src/selfhealing/api/governance/urls.py) - URL 패턴 참고

**신규 구현 설계**:

```python
# governance/views.py 확장

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from selfhealing.services.learning import LearningService


class ParameterBlacklistView(APIView):
    """
    블랙리스트 조회 API.
    
    GET /api/self-healing/governance/blacklist/
    """
    permission_classes = [IsAdminUser]
    
    def get(self, request) -> Response:
        """전체 블랙리스트 조회."""
        service = LearningService()
        blacklist = service._parameter_blacklist.get_all()
        
        return Response({
            "count": len(blacklist),
            "items": [
                {
                    "module": entry.module,
                    "parameter": entry.parameter,
                    "blocked_values": list(entry.blocked_values),
                    "reason": entry.reason.value,
                    "registered_at": entry.registered_at.isoformat(),
                    "registered_by": entry.registered_by,
                    "expires_at": entry.expires_at.isoformat() if entry.expires_at else None,
                    "incident_id": entry.incident_id,
                }
                for entry in blacklist
            ],
        })


class ParameterBlacklistDetailView(APIView):
    """
    블랙리스트 개별 항목 관리 API.
    
    DELETE /api/self-healing/governance/blacklist/<module>/<parameter>/
    """
    permission_classes = [IsAdminUser]
    
    def delete(self, request, module: str, parameter: str) -> Response:
        """
        블랙리스트 항목 수동 해제.
        
        Args:
            module: 모듈 이름 (예: circuit_breaker)
            parameter: 파라미터 이름 (예: threshold)
            
        Request Body (optional):
            {
                "value": "0.1",  // 특정 값만 해제, 없으면 전체 해제
                "reason": "수동 해제 사유"
            }
        """
        service = LearningService()
        
        # 존재 확인
        entry = service._parameter_blacklist.get(module, parameter)
        if entry is None:
            return Response(
                {"error": f"Blacklist entry not found: {module}:{parameter}"},
                status=status.HTTP_404_NOT_FOUND,
            )
        
        # 특정 값만 해제 또는 전체 해제
        value_to_remove = request.data.get("value")
        reason = request.data.get("reason", "Manual removal via Admin API")
        
        if value_to_remove:
            success = service._parameter_blacklist.remove_value(
                module, parameter, value_to_remove
            )
        else:
            success = service._parameter_blacklist.remove(module, parameter)
        
        if not success:
            return Response(
                {"error": "Failed to remove blacklist entry"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        
        # Audit 로깅
        logger.info(
            f"[ParameterBlacklist] Manual removal: {module}:{parameter} "
            f"by {request.user.username}, reason: {reason}"
        )
        
        return Response(
            {
                "message": f"Successfully removed: {module}:{parameter}",
                "removed_by": request.user.username,
                "reason": reason,
            },
            status=status.HTTP_200_OK,
        )


# governance/urls.py 확장

from django.urls import path
from .views import (
    ParameterBlacklistView,
    ParameterBlacklistDetailView,
)

urlpatterns = [
    # 기존 URL...
    path('blacklist/', ParameterBlacklistView.as_view(), name='blacklist-list'),
    path(
        'blacklist/<str:module>/<str:parameter>/',
        ParameterBlacklistDetailView.as_view(),
        name='blacklist-detail',
    ),
]
```

**ParameterBlacklist 확장 (remove 메서드)**:

```python
# learning/service.py 확장

class ParameterBlacklist:
    # ... 기존 코드 ...
    
    def remove(self, module: str, parameter: str) -> bool:
        """
        블랙리스트 항목 완전 제거.
        
        Returns:
            성공 여부
        """
        key = f"{module}:{parameter}"
        
        if key not in self._entries:
            return False
        
        del self._entries[key]
        self._persist()
        
        logger.info(f"[ParameterBlacklist] Removed: {key}")
        return True
    
    def remove_value(self, module: str, parameter: str, value: str) -> bool:
        """
        블랙리스트에서 특정 값만 제거.
        
        Args:
            value: 제거할 값
            
        Returns:
            성공 여부
        """
        key = f"{module}:{parameter}"
        
        if key not in self._entries:
            return False
        
        entry = self._entries[key]
        if value not in entry.blocked_values:
            return False
        
        entry.blocked_values.discard(value)
        
        # 모든 값이 제거되면 항목 자체 삭제
        if not entry.blocked_values:
            del self._entries[key]
        
        self._persist()
        
        logger.info(f"[ParameterBlacklist] Removed value {value} from {key}")
        return True
    
    def get(self, module: str, parameter: str) -> Optional[BlacklistedParameter]:
        """특정 항목 조회."""
        return self._entries.get(f"{module}:{parameter}")
    
    def get_all(self) -> list[BlacklistedParameter]:
        """전체 항목 조회."""
        self._cleanup_expired()
        return list(self._entries.values())
```

---

### 11.3 Tracing Deep Link: triggering_trace_id 연동 (리뷰 ③)

**리뷰 내용**: "보호 조치에 triggering_trace_id 추가 → 대시보드에서 원클릭 추적"

**코드 근거**: Circuit Breaker tracing에서 이미 동일 네이밍 컨벤션 사용 중.

**Reference**:
- [circuit_breaker/tracing.py#L573](../../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/tracing.py#L573): `"cb.triggering_trace_id": triggering_info.trace_id`

**ProtectionResult 확장** (위 §8.3.3에서 이미 추가됨):

```python
@dataclass
class ProtectionResult:
    # ... 기존 필드 ...
    
    # v2.2.0: Tracing Deep Link
    triggering_trace_id: Optional[str] = None
    """보호 조치를 유발한 원본 요청의 trace_id (Jaeger/Zipkin 연동용)."""
    
    triggering_request_path: Optional[str] = None
    """보호 조치를 유발한 원본 요청 경로 (예: POST /api/payments/)."""
    
    def get_trace_url(self, template: str = "") -> Optional[str]:
        """Trace UI Deep Link 생성."""
        # ... 구현은 §8.3.3 참조 ...
```

**ProtectionOrchestrator에서 trace_id 주입**:

```python
class ProtectionOrchestrator:
    def execute_policies(
        self,
        policies: list[ActionPolicy],
        context: dict,
    ) -> ProtectionResult:
        """정책 실행 (trace_id 자동 주입)."""
        from selfhealing.audit.trace import get_trace_id  # Local Import
        
        # 실행 로직...
        
        result = ProtectionResult(
            success=...,
            executed_policies=...,
            # v2.2.0: 현재 요청의 trace_id 주입
            triggering_trace_id=get_trace_id(),
            triggering_request_path=context.get("request_path"),
        )
        
        return result
```

**대시보드 연동 예시**:

```python
# Admin Dashboard에서 사용 예시

def render_protection_result(result: ProtectionResult) -> dict:
    """보호 조치 결과를 대시보드용 JSON으로 변환."""
    trace_url = result.get_trace_url()
    
    return {
        "success": result.success,
        "policies": [p.name for p in result.executed_policies],
        "triggering_request": {
            "path": result.triggering_request_path,
            "trace_id": result.triggering_trace_id,
            "trace_url": trace_url,  # 클릭 시 Jaeger/Zipkin으로 이동
        },
    }
```

**환경 변수 설정**:

```bash
# .env
SELFHEALING_TRACE_URL_TEMPLATE=https://jaeger.example.com/trace/{trace_id}
```

---

## 12. v2.2.0 테스트 계획

### 12.1 블랙리스트 Admin API 테스트

```python
# tests/self_healing/api/test_blacklist_api.py

class TestParameterBlacklistAPI:
    """블랙리스트 Admin API 테스트."""
    
    def test_delete_blacklist_entry(self, admin_client):
        """블랙리스트 항목 삭제 테스트."""
        # 1. 블랙리스트 등록
        service = LearningService()
        service.register_dangerous_parameter(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1", "0.2"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )
        
        # 2. API로 삭제
        response = admin_client.delete(
            "/api/self-healing/governance/blacklist/circuit_breaker/threshold/",
            data={"reason": "테스트 완료"},
            content_type="application/json",
        )
        
        assert response.status_code == 200
        assert "Successfully removed" in response.json()["message"]
        
        # 3. 삭제 확인
        is_blocked, _ = service.is_parameter_blocked(
            "circuit_breaker", "threshold", "0.1"
        )
        assert is_blocked is False
    
    def test_delete_specific_value(self, admin_client):
        """특정 값만 삭제 테스트."""
        service = LearningService()
        service.register_dangerous_parameter(
            module="retry",
            parameter="max_attempts",
            blocked_values={"50", "100"},
            reason=BlacklistReason.FLAPPING,
        )
        
        # 특정 값만 삭제
        response = admin_client.delete(
            "/api/self-healing/governance/blacklist/retry/max_attempts/",
            data={"value": "50"},
            content_type="application/json",
        )
        
        assert response.status_code == 200
        
        # 50은 해제, 100은 유지
        is_blocked_50, _ = service.is_parameter_blocked("retry", "max_attempts", "50")
        is_blocked_100, _ = service.is_parameter_blocked("retry", "max_attempts", "100")
        
        assert is_blocked_50 is False
        assert is_blocked_100 is True
```

### 12.2 triggering_trace_id 테스트

```python
# tests/self_healing/unit/test_protection_tracing.py

class TestProtectionTracing:
    """ProtectionResult trace_id 연동 테스트."""
    
    def test_trace_id_injected_from_context(self):
        """현재 요청의 trace_id가 결과에 주입되는지 확인."""
        from selfhealing.audit.trace import set_trace_id
        
        # trace_id 설정
        set_trace_id("abc123")
        
        orchestrator = ProtectionOrchestrator(mock_service)
        result = orchestrator.execute_policies(
            [ActionPolicy.BLOCK_AND_LOG],
            context={"request_path": "POST /api/payments/"},
        )
        
        assert result.triggering_trace_id == "abc123"
        assert result.triggering_request_path == "POST /api/payments/"
    
    def test_get_trace_url_with_template(self):
        """환경 변수 템플릿으로 URL 생성."""
        result = ProtectionResult(
            success=True,
            executed_policies=[],
            failed_policies=[],
            highest_priority_succeeded=True,
            triggering_trace_id="xyz789",
        )
        
        url = result.get_trace_url("https://jaeger.local/trace/{trace_id}")
        
        assert url == "https://jaeger.local/trace/xyz789"
```

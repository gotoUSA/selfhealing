# 24. Corruption Shield (Multi-Layer Data Integrity)

## 개요

Corruption Shield는 **3계층 데이터 무결성 보호 시스템**입니다. SQL Injection, XSS 등의 보안 공격부터 비즈니스 규칙 위반, 통계적 이상치까지 다층적으로 탐지하고 차단합니다.

## 문제점

데이터 무결성 위협:
- **L1 위협**: SQL Injection, XSS, 타입 불일치
- **L2 위협**: 금액 조작, 상태 위변조, 서명 불일치
- **L3 위협**: 통계적 이상치 (fraud, anomaly)

기존 시스템의 한계:
- 단일 계층 검증으로 우회 가능
- 비즈니스 규칙 검증 부재
- 이상 탐지 미적용

## 해결책: Multi-Layer Defense

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Corruption Shield                             │
├─────────────────────────────────────────────────────────────────────┤
│  ┌───────────────┐    ┌───────────────┐    ┌───────────────┐        │
│  │ L1 Schema     │───▶│ L2 Business   │───▶│ L3 Anomaly    │        │
│  │ Validator     │    │ Rules         │    │ Detector      │        │
│  └───────────────┘    └───────────────┘    └───────────────┘        │
│         │                    │                    │                  │
│         ▼                    ▼                    ▼                  │
│  • SQL Injection      • Amount Range       • Z-Score              │
│  • XSS Detection      • Status Valid       • IQR Outlier          │
│  • Type Check         • Signature          • Statistical          │
│  • Required Fields    • Mismatch           • Pattern              │
└─────────────────────────────────────────────────────────────────────┘
```

## 모듈 구조

```
packages/selfhealing-python/src/selfhealing/services/corruption_shield/
├── __init__.py          # 모듈 exports
├── config.py            # CorruptionShieldConfig
├── validators.py        # L1, L2, L3 Validators
└── shield.py            # 통합 CorruptionShield
```

## 설정

### CorruptionShieldConfig

```python
from dataclasses import dataclass

@dataclass
class CorruptionShieldConfig:
    """Corruption Shield 설정."""
    
    # 계층별 활성화
    l1_enabled: bool = True
    l2_enabled: bool = True
    l3_enabled: bool = True
    
    # L1: Schema Validation
    max_string_length: int = 10000
    
    # L2: Business Rules
    min_amount: int = 100
    max_amount: int = 100_000_000  # 1억
    valid_statuses: tuple = (
        "PENDING", "PAID", "CANCELLED", "REFUNDED", "DONE"
    )
    
    # L3: Anomaly Detection
    z_score_threshold: float = 3.0
    iqr_multiplier: float = 1.5
    min_samples: int = 10
    
    # Logging
    log_violations: bool = True
    log_to_security_incident: bool = True
```

## L1: Schema Validator

### 탐지 패턴

```python
class L1SchemaValidator:
    """스키마 기반 검증 (보안 공격 차단)."""
    
    # SQL Injection 패턴
    INJECTION_PATTERNS = [
        r"(?i)('|\"|;|--|\bOR\b|\bAND\b|\bUNION\b|\bSELECT\b|\bINSERT\b|\bUPDATE\b|\bDELETE\b|\bDROP\b|\bEXEC\b|\bEXECUTE\b)",
        r"(?i)(xp_|sp_|0x[0-9a-f]+)",
        r"(?i)(WAITFOR\s+DELAY|BENCHMARK\s*\()",
    ]
    
    # XSS 패턴
    XSS_PATTERNS = [
        r"(?i)<script|javascript:|on\w+=",
        r"(?i)<iframe|<object|<embed",
        r"(?i)expression\s*\(|url\s*\(",
    ]
```

### 검증 항목

| 항목 | 설명 | 심각도 |
|------|------|--------|
| injection_attempt | SQL/Command Injection | CRITICAL |
| xss_attempt | XSS 공격 시도 | CRITICAL |
| invalid_type | 타입 불일치 | HIGH |
| invalid_nan | NaN/Infinity 값 | HIGH |
| string_too_long | 문자열 길이 초과 | MEDIUM |

## L2: Business Rules Validator

### 검증 규칙

```python
class L2BusinessRulesValidator:
    """비즈니스 규칙 검증."""
    
    def validate(self, data: dict, context: dict) -> List[Violation]:
        violations = []
        
        # 1. 금액 범위 검증
        if "amount" in data:
            amount = data["amount"]
            if amount < 0:
                violations.append(Violation(
                    layer="L2",
                    code="negative_amount",
                    message=f"Amount cannot be negative: {amount}",
                    severity="critical",
                ))
            elif amount < self.config.min_amount:
                violations.append(Violation(
                    layer="L2",
                    code="amount_too_small",
                    message=f"Amount {amount} below minimum {self.config.min_amount}",
                    severity="high",
                ))
            elif amount > self.config.max_amount:
                violations.append(Violation(
                    layer="L2",
                    code="amount_too_large",
                    message=f"Amount {amount} exceeds maximum {self.config.max_amount}",
                    severity="high",
                ))
        
        # 2. 상태 값 검증
        if "status" in data:
            if data["status"] not in self.config.valid_statuses:
                violations.append(Violation(
                    layer="L2",
                    code="invalid_status",
                    message=f"Invalid status: {data['status']}",
                    severity="medium",
                ))
        
        # 3. 금액 불일치 검증 (context 필요)
        if "expected_amount" in context and "amount" in data:
            if data["amount"] != context["expected_amount"]:
                violations.append(Violation(
                    layer="L2",
                    code="amount_mismatch",
                    message=f"Amount {data['amount']} != expected {context['expected_amount']}",
                    severity="critical",
                ))
        
        return violations
```

### 검증 항목

| 항목 | 설명 | 심각도 |
|------|------|--------|
| negative_amount | 음수 금액 | CRITICAL |
| amount_too_small | 최소 금액 미달 | HIGH |
| amount_too_large | 최대 금액 초과 | HIGH |
| invalid_status | 유효하지 않은 상태 | MEDIUM |
| amount_mismatch | 금액 불일치 | CRITICAL |

## L3: Anomaly Detector

### 탐지 알고리즘

```python
class L3AnomalyDetector:
    """통계 기반 이상 탐지."""
    
    def __init__(self, config: CorruptionShieldConfig):
        self.config = config
        self._samples: Dict[str, List[float]] = defaultdict(list)
        self._max_samples = 1000
    
    def _check_z_score(self, field: str, value: float) -> Optional[Violation]:
        """Z-Score 기반 이상치 탐지."""
        samples = self._samples.get(field, [])
        
        if len(samples) < self.config.min_samples:
            return None
        
        mean = sum(samples) / len(samples)
        variance = sum((x - mean) ** 2 for x in samples) / len(samples)
        std = variance ** 0.5
        
        if std == 0:
            return None
        
        z_score = abs(value - mean) / std
        
        if z_score > self.config.z_score_threshold:
            return Violation(
                layer="L3",
                code="statistical_outlier",
                message=f"Z-score {z_score:.2f} exceeds threshold {self.config.z_score_threshold}",
                severity="medium",
                field=field,
            )
        return None
    
    def _check_iqr(self, field: str, value: float) -> Optional[Violation]:
        """IQR 기반 이상치 탐지."""
        samples = sorted(self._samples.get(field, []))
        
        if len(samples) < self.config.min_samples:
            return None
        
        n = len(samples)
        q1 = samples[n // 4]
        q3 = samples[3 * n // 4]
        iqr = q3 - q1
        
        lower_bound = q1 - self.config.iqr_multiplier * iqr
        upper_bound = q3 + self.config.iqr_multiplier * iqr
        
        if value < lower_bound or value > upper_bound:
            return Violation(
                layer="L3",
                code="iqr_outlier",
                message=f"Value {value} outside IQR bounds [{lower_bound:.2f}, {upper_bound:.2f}]",
                severity="medium",
                field=field,
            )
        return None
```

### 탐지 항목

| 항목 | 설명 | 심각도 |
|------|------|--------|
| statistical_outlier | Z-Score 이상치 | MEDIUM |
| iqr_outlier | IQR 이상치 | MEDIUM |

## 사용법

### 기본 사용

```python
from selfhealing.services.corruption_shield import get_corruption_shield

shield = get_corruption_shield()

# 데이터 검증
result = shield.validate(
    data={
        "amount": 50000,
        "order_id": "ORD-12345",
        "status": "PAID",
    },
    context={
        "expected_amount": 50000,
        "valid_statuses": ["PENDING", "PAID", "CANCELLED"],
    },
)

if not result.is_valid:
    for violation in result.violations:
        print(f"[{violation.layer}] {violation.code}: {violation.message}")
    
    if result.blocked:
        raise ValidationError("Request blocked due to data corruption")
```

### Django REST Framework 통합

```python
from rest_framework.permissions import BasePermission
from selfhealing.services.corruption_shield import get_corruption_shield

class CorruptionShieldPermission(BasePermission):
    """DRF Permission으로 데이터 무결성 검증."""
    
    def has_permission(self, request, view):
        if request.method in ['POST', 'PUT', 'PATCH']:
            shield = get_corruption_shield()
            result = shield.validate(request.data)
            
            if result.blocked:
                self.message = f"Blocked: {result.violations[0].message}"
                return False
        return True
```

### Webhook 검증

```python
def validate_toss_webhook(payload: dict, expected_order: Order):
    """Toss 웹훅 데이터 검증."""
    shield = get_corruption_shield()
    
    result = shield.validate(
        data=payload,
        context={
            "expected_amount": expected_order.total_amount,
            "expected_order_id": str(expected_order.id),
        },
    )
    
    if not result.is_valid:
        log_security_incident(
            type="webhook_corruption",
            violations=result.violations,
        )
        return False
    
    return True
```

## ValidationResult

```python
@dataclass
class ValidationResult:
    """검증 결과."""
    
    is_valid: bool              # 전체 통과 여부
    violations: List[Violation] # 위반 목록
    blocked: bool               # 차단 여부 (critical/high)
    
    # 계층별 통과 여부
    l1_passed: bool = True
    l2_passed: bool = True
    l3_passed: bool = True
    
    validation_time_ms: float = 0.0
```

## 통계 및 모니터링

### 통계 조회

```python
shield = get_corruption_shield()
stats = shield.get_stats()

# {
#     "total_validations": 10000,
#     "passed": 9500,
#     "blocked": 500,
#     "l1_violations": 200,
#     "l2_violations": 250,
#     "l3_violations": 50,
#     "block_rate_percent": 5.0
# }
```

### Grafana 대시보드 메트릭

```python
from prometheus_client import Counter, Histogram

corruption_validations = Counter(
    'selfhealing_corruption_validations_total',
    'Total validations',
    ['result']  # passed, blocked
)

corruption_violations = Counter(
    'selfhealing_corruption_violations_total',
    'Total violations by layer',
    ['layer', 'code', 'severity']
)

corruption_latency = Histogram(
    'selfhealing_corruption_latency_seconds',
    'Validation latency'
)
```

## API Endpoints

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/self-healing/corruption-shield/status/` | 상태 조회 |
| GET | `/api/self-healing/corruption-shield/stats/` | 통계 조회 |
| POST | `/api/self-healing/corruption-shield/validate/` | 데이터 검증 |
| POST | `/api/self-healing/corruption-shield/validate/batch/` | 배치 검증 |
| GET | `/api/self-healing/corruption-shield/violations/` | 위반 이력 |
| GET | `/api/self-healing/corruption-shield/config/` | 설정 조회 |
| PUT | `/api/self-healing/corruption-shield/config/` | 설정 변경 |

## Hell Mode 테스트

```python
@task(3)
def test_corruption_shield(self):
    """Corruption Shield 테스트."""
    shield = get_corruption_shield()
    
    # Test 1: Valid data → PASS
    # Test 2: SQL Injection → L1 BLOCK
    # Test 3: Negative amount → L2 BLOCK
    # Test 4: Amount mismatch → L2 BLOCK
    # Test 5: XSS attempt → L1 BLOCK
    # Test 6: Statistical outlier → L3 WARN
```

## 보안 사고 연동

```python
def _maybe_create_security_incident(self, data: dict, result: ValidationResult):
    """Critical 위반 시 보안 사고 생성."""
    critical_violations = [
        v for v in result.violations
        if v.severity == "critical"
    ]
    
    if critical_violations:
        from selfhealing.services.security_violation_service import SecurityViolationService
        
        service = SecurityViolationService()
        for violation in critical_violations:
            service.record_violation(
                violation_type=f"corruption_{violation.code}",
                details={
                    "layer": violation.layer,
                    "message": violation.message,
                    "field": violation.field,
                },
            )
```

## 참고 자료

- [OWASP SQL Injection Prevention](https://owasp.org/www-community/attacks/SQL_Injection)
- [OWASP XSS Prevention](https://owasp.org/www-community/xss-filter-evasion-cheatsheet)
- [Anomaly Detection with Z-Score](https://en.wikipedia.org/wiki/Standard_score)
- [IQR Method for Outlier Detection](https://en.wikipedia.org/wiki/Interquartile_range)

---

**작성일**: 2024-12-28  
**작성자**: Self-Healing System Team

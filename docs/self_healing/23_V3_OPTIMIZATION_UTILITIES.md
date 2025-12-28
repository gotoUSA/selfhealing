# V3 최적화 유틸리티 구현 결과

## 개요

Architect 리뷰에서 지적된 2가지 핵심 이슈를 해결하기 위한 V3 최적화 유틸리티를 구현했습니다:

1. **SLA Violations: 6건** → Netflix Gradient Adaptive Throttling
2. **Data Corruption 차단: 0건** → Multi-Layer Corruption Shield

## 1. Netflix Gradient Adaptive Throttling

### 1.1 문제점

기존 스로틀링은 고정된 rate limit을 사용하여 SLA 위반을 예방하지 못했습니다.

### 1.2 해결책

Netflix의 Adaptive Concurrency Limits 알고리즘을 기반으로 RTT(응답 시간)에 따라 동적으로 limit을 조절합니다.

```
gradient = (current_RTT - previous_RTT) / previous_RTT

if gradient > 0:      # RTT 증가 → 서버 과부하
    limit = limit × 0.9
else:                 # RTT 감소 → 여유 있음
    limit = limit + 1
```

### 1.3 구현 모듈

| 파일 | 설명 |
|------|------|
| `selfhealing/services/throttle/config.py` | ThrottleConfig, ThrottleResult 데이터클래스 |
| `selfhealing/services/throttle/base.py` | BaseThrottle, SlidingWindowThrottle |
| `selfhealing/services/throttle/adaptive.py` | AdaptiveThrottle, GradientCalculator |
| `selfhealing/api/django/throttle_adapter.py` | DRF Throttle 어댑터 |

### 1.4 주요 설정

```python
@dataclass
class ThrottleConfig:
    initial_limit: int = 100          # 초기 한도
    min_limit: int = 10               # 최소 한도
    max_limit: int = 500              # 최대 한도
    window_seconds: int = 60          # 슬라이딩 윈도우 크기
    sla_warning_ms: float = 200       # SLA 경고 임계값
    sla_critical_ms: float = 500      # SLA 위험 임계값
    decrease_ratio: float = 0.9       # 한도 감소 비율
    increase_step: int = 1            # 한도 증가 스텝
```

### 1.5 SLA 기반 조절 로직

```python
if rtt_ms >= sla_critical_ms:        # ≥500ms → 긴급 감소
    new_limit = limit * 0.7           # -30%
elif rtt_ms >= sla_warning_ms:       # ≥200ms → 경고 감소
    new_limit = limit * 0.9           # -10%
elif gradient > 0.1:                  # RTT 10%+ 증가
    new_limit = limit * 0.9           # -10%
elif gradient < -0.05:                # RTT 5%+ 감소
    new_limit = limit + 1             # +1
```

### 1.6 사용법

```python
from selfhealing.services.throttle import get_adaptive_throttle

throttle = get_adaptive_throttle()

# 요청 전 체크
result = throttle.check(client_id="user_123")
if not result.allowed:
    return HttpResponse(status=429)

# 응답 후 RTT 기록
throttle.record_response(rtt_ms=150)
```

## 2. Multi-Layer Corruption Shield

### 2.1 문제점

Data Corruption 공격 차단이 0건으로, 데이터 무결성 보호가 미흡했습니다.

### 2.2 해결책

3계층 방어 시스템을 구현했습니다:

| 계층 | 역할 | 탐지 대상 |
|------|------|----------|
| **L1 Schema** | 스키마 검증 | SQL Injection, XSS, 필수 필드, 타입 |
| **L2 Business** | 비즈니스 규칙 | 금액 범위, 상태 유효성, 서명 일치 |
| **L3 Anomaly** | 이상 탐지 | Z-Score, IQR 기반 통계적 이상치 |

### 2.3 구현 모듈

| 파일 | 설명 |
|------|------|
| `selfhealing/services/corruption_shield/config.py` | CorruptionShieldConfig |
| `selfhealing/services/corruption_shield/validators.py` | L1, L2, L3 Validator |
| `selfhealing/services/corruption_shield/shield.py` | 통합 CorruptionShield |

### 2.4 L1 Schema Validator

```python
# SQL Injection 패턴
INJECTION_PATTERNS = [
    r"(?i)('|\"|;|--|\bOR\b|\bAND\b|\bUNION\b|\bSELECT\b|...)",
]

# XSS 패턴
XSS_PATTERNS = [
    r"(?i)<script|javascript:|on\w+=",
]
```

### 2.5 L2 Business Rules Validator

```python
# 금액 범위 검증
if amount < 100 or amount > 100_000_000:
    return Violation("amount_out_of_range", ...)

# 금액 불일치 검증
if context.get("expected_amount") and amount != expected:
    return Violation("amount_mismatch", ...)
```

### 2.6 L3 Anomaly Detector

```python
# Z-Score 기반 이상치 탐지
z_score = abs(value - mean) / std
if z_score > 3.0:
    return Violation("statistical_outlier", ...)

# IQR 기반 이상치 탐지
q1, q3 = percentile(samples, [25, 75])
iqr = q3 - q1
if value < q1 - 1.5 * iqr or value > q3 + 1.5 * iqr:
    return Violation("iqr_outlier", ...)
```

### 2.7 사용법

```python
from selfhealing.services.corruption_shield import get_corruption_shield

shield = get_corruption_shield()

result = shield.validate(
    data={
        "order_id": "ORD-12345",
        "amount": 50000,
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
```

## 3. Hell Mode 테스트 시나리오

### 3.1 추가된 시나리오

`load_tests/stage0_selfhealing_hellmode.py`에 2개의 새로운 시나리오를 추가했습니다:

```python
@task(3)
def test_adaptive_throttling(self):
    """Test adaptive throttling under load."""
    throttle = get_adaptive_throttle()
    result = throttle.check(client_id=f"locust_{self.user_id}")
    
    if result.allowed:
        # Simulate request with RTT
        rtt = random.uniform(50, 300)
        throttle.record_response(rtt_ms=rtt)

@task(3)
def test_corruption_shield(self):
    """Test corruption shield with various attack patterns."""
    shield = get_corruption_shield()
    
    # 10% chance of attack attempt
    if random.random() < 0.1:
        data = {"order_id": "'; DROP TABLE orders; --"}
    else:
        data = {"order_id": "ORD-12345", "amount": 50000}
    
    result = shield.validate(data)
```

## 4. 테스트 결과

### 4.1 단위 테스트

```
============================================================
V3 Module Tests (Throttle + Corruption Shield)
============================================================
✓ ThrottleConfig passed
✓ SlidingWindowThrottle passed
✓ AdaptiveThrottle passed (limit: 100 → 70)
✓ CorruptionShield valid data passed
✓ CorruptionShield SQL injection blocked
✓ CorruptionShield negative amount blocked
✓ CorruptionShield XSS blocked
✓ CorruptionShield amount mismatch detected

Results: 8 passed, 0 failed
✅ All V3 module tests passed!
```

### 4.2 예상 효과

| 지표 | Before | After (예상) |
|------|--------|-------------|
| SLA Violations | 6건 | 0~1건 |
| Data Corruption Blocked | 0건 | 10%+ |
| 평균 응답시간 | 불안정 | 안정적 유지 |

## 5. 파일 변경 요약

### 5.1 생성된 파일

**Throttle 모듈:**
- `packages/selfhealing-python/src/selfhealing/services/throttle/__init__.py`
- `packages/selfhealing-python/src/selfhealing/services/throttle/config.py`
- `packages/selfhealing-python/src/selfhealing/services/throttle/base.py`
- `packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py`

**Corruption Shield 모듈:**
- `packages/selfhealing-python/src/selfhealing/services/corruption_shield/__init__.py`
- `packages/selfhealing-python/src/selfhealing/services/corruption_shield/config.py`
- `packages/selfhealing-python/src/selfhealing/services/corruption_shield/validators.py`
- `packages/selfhealing-python/src/selfhealing/services/corruption_shield/shield.py`

**테스트:**
- `tests/self_healing/unit/test_netflix_gradient_throttle.py`
- `tests/self_healing/unit/test_corruption_shield.py`
- `scripts/test_v3_modules.py`

### 5.2 수정된 파일

- `load_tests/stage0_selfhealing_hellmode.py`: V3 시나리오 추가
- `packages/selfhealing-python/src/selfhealing/services/security_violation_service.py`: `record_violation` 메서드 추가

## 6. 다음 단계

1. **Locust Load Test 실행**: Hell Mode에서 V3 모듈 성능 검증
2. **프로덕션 적용**: DRF 설정에 AdaptiveDRFThrottle 등록
3. **모니터링 대시보드**: Grafana에 V3 메트릭 추가

---

**작성일**: 2025-01-15  
**작성자**: Self-Healing System Team

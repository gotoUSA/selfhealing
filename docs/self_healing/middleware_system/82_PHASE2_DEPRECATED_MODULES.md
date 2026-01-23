# Phase 2: Deprecated 모듈/클래스 정리

> **상위 문서**: [80_DEPRECATION_CLEANUP_MASTER_PLAN.md](80_DEPRECATION_CLEANUP_MASTER_PLAN.md)  
> **예상 작업 시간**: 2-4시간  
> **난이도**: 중

---

## 1. 대상 목록

### 1.1 Deprecated 모듈

| 모듈 경로 | 새 경로 | 상태 |
|----------|---------|------|
| `selfhealing.audit.hash_chain_performance` | `selfhealing.audit.performance` | Lazy import + DeprecationWarning ✅ |
| `selfhealing.services.factory` (단일 모듈) | `selfhealing.services.factory` (패키지) | 문서에만 deprecated 표시 |
| `selfhealing.metrics.jitter` | `selfhealing.utils.jitter` | DeprecationWarning ✅ |

### 1.2 Deprecated 클래스 별칭

| 파일 | 별칭 | 실제 클래스 |
|------|------|------------|
| services/error_budget_gate/fault_detector.py (L132) | `CircuitState` | `GateFaultState` |
| services/error_budget_gate/fault_detector.py (L133) | `InMemoryCircuitBreaker` | `GateFaultDetector` |

### 1.3 Deprecated 상수

| 파일 | 상수 | 대체 방법 |
|------|------|----------|
| services/security_notification/models.py (L35) | `SLACK_BLOCK_TEXT_LIMIT` | `_get_notification_limits()` |
| services/security_notification/models.py (L36) | `DESCRIPTION_MAX_LENGTH` | `_get_notification_limits()` |
| services/security_notification/models.py (L37) | `ACTION_TAKEN_MAX_LENGTH` | `_get_notification_limits()` |
| services/security_notification/models.py (L38) | `TITLE_MAX_LENGTH` | `_get_notification_limits()` |

---

## 2. 실행 순서

### Step 2.1: 모듈 사용처 검색

```bash
# hash_chain_performance 사용처
grep -rn "from selfhealing.audit.hash_chain_performance" --include="*.py"
grep -rn "import selfhealing.audit.hash_chain_performance" --include="*.py"

# metrics.jitter 사용처
grep -rn "from selfhealing.metrics.jitter" --include="*.py"
grep -rn "from selfhealing.metrics import.*jitter" --include="*.py"

# services.factory (단일 모듈) 사용처
grep -rn "from selfhealing.services.factory import" --include="*.py"

# 클래스 별칭 사용처
grep -rn "CircuitState" --include="*.py"
grep -rn "InMemoryCircuitBreaker" --include="*.py"

# 상수 사용처
grep -rn "SLACK_BLOCK_TEXT_LIMIT\|DESCRIPTION_MAX_LENGTH\|ACTION_TAKEN_MAX_LENGTH\|TITLE_MAX_LENGTH" --include="*.py"
```

### Step 2.2: 직접 Import로 변경

**모듈 변경**:

| Before | After |
|--------|-------|
| `from selfhealing.audit.hash_chain_performance import X` | `from selfhealing.audit.performance import X` |
| `from selfhealing.metrics.jitter import X` | `from selfhealing.utils.jitter import X` |

**클래스 별칭 변경**:

| Before | After |
|--------|-------|
| `from ...fault_detector import CircuitState` | `from ...fault_detector import GateFaultState` |
| `from ...fault_detector import InMemoryCircuitBreaker` | `from ...fault_detector import GateFaultDetector` |

### Step 2.3: 강화된 경고 추가

이미 DeprecationWarning이 있는 모듈은 유지하고, 없는 곳에 추가합니다.

**fault_detector.py 클래스 별칭에 경고 추가**:

적용 위치: `services/error_budget_gate/fault_detector.py` L128-140

```python
# 현재: 단순 별칭
CircuitState = GateFaultState
InMemoryCircuitBreaker = GateFaultDetector

# 변경: 경고 추가 (속성 접근 시)
def __getattr__(name):
    if name == "CircuitState":
        warnings.warn("CircuitState is deprecated, use GateFaultState", DeprecationWarning)
        return GateFaultState
    if name == "InMemoryCircuitBreaker":
        warnings.warn("InMemoryCircuitBreaker is deprecated, use GateFaultDetector", DeprecationWarning)
        return GateFaultDetector
```

### Step 2.4: 통합 테스트

```bash
pytest tests/ -v
pytest tests/ -W error::DeprecationWarning
```

---

## 3. 상세 파일별 작업

### 3.1 selfhealing/audit/hash_chain_performance.py

| 라인 | 현재 상태 | 작업 |
|------|----------|------|
| L1-99 | Lazy import + DeprecationWarning 구현됨 | 유지 (향후 제거) |

**향후 제거 시점**: 모든 사용처가 새 경로로 마이그레이션된 후

### 3.2 selfhealing/metrics/jitter.py

| 라인 | 현재 상태 | 작업 |
|------|----------|------|
| L1-35 | Re-export + DeprecationWarning 구현됨 | 유지 (향후 제거) |

### 3.3 selfhealing/services/factory.py (단일 모듈)

| 라인 | 현재 상태 | 작업 |
|------|----------|------|
| L1-48 | Re-export wrapper | DeprecationWarning 추가 |

### 3.4 selfhealing/services/error_budget_gate/fault_detector.py

| 라인 | 현재 상태 | 작업 |
|------|----------|------|
| L128-140 | `__getattr__` 패턴으로 DeprecationWarning 구현됨 ✅ | 완료 |

### 3.5 selfhealing/services/security_notification/models.py

| 라인 | 현재 상태 | 작업 |
|------|----------|------|
| L35-70 | `__getattr__` 패턴으로 DeprecationWarning 구현됨 ✅ | 완료 |
| service.py | `_get_notification_limits()` 사용으로 마이그레이션 ✅ | 완료 |

---

## 4. 완료 체크리스트

- [x] `hash_chain_performance` - 이미 DeprecationWarning 있음 ✅ (유지)
- [x] `metrics.jitter` - 이미 DeprecationWarning 있음 ✅ (유지)
- [x] `services.factory` 단일 모듈에 경고 추가 ✅ (2026-01-23)
- [x] `CircuitState`, `InMemoryCircuitBreaker` 별칭에 경고 추가 ✅ (2026-01-23)
  - fault_detector.py: `__getattr__` 패턴 구현
  - error_budget_gate/__init__.py: lazy import로 변경
- [x] 상수 4개 DeprecationWarning 추가 ✅ (2026-01-23)
  - models.py: `__getattr__` 패턴 구현
  - __init__.py: lazy import로 변경
  - service.py: `_get_notification_limits()` 사용으로 마이그레이션
- [x] 통합 테스트 통과 ✅ (2026-01-23)
  - error_budget_gate 테스트 62개 통과

---

## 5. 주의사항

### 5.1 즉시 제거하면 안 되는 항목

- **hash_chain_performance.py**: 외부 사용자가 있을 수 있음
- **metrics/jitter.py**: 로드 테스트 등에서 사용 가능

### 5.2 제거 타임라인

| 항목 | 경고 추가 | 제거 예정 |
|------|----------|----------|
| hash_chain_performance | ✅ 완료 | v3.0.0 |
| metrics/jitter | ✅ 완료 | v3.0.0 |
| CircuitState/InMemoryCircuitBreaker | ✅ 완료 | v3.0.0 |
| 상수 4개 | ✅ 완료 | v3.0.0 |
| services.factory 단일 모듈 | ✅ 완료 | v3.0.0 |

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 1.0 | 2026-01-23 | 초안 작성 |
| 1.1 | 2026-01-23 | Phase 2 구현 완료 - DeprecationWarning 추가 |

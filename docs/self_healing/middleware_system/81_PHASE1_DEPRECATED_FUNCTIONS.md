# Phase 1: Deprecated 함수/메서드 정리

> **상위 문서**: [80_DEPRECATION_CLEANUP_MASTER_PLAN.md](80_DEPRECATION_CLEANUP_MASTER_PLAN.md)  
> **예상 작업 시간**: 4-8시간  
> **난이도**: 중

---

## 1. 대상 목록

### 1.1 Return 모델 메서드 (shopping/models/return_request.py)

| 메서드 | 라인 | 대체 함수 | 사용처 검색 쿼리 |
|--------|------|----------|------------------|
| `generate_return_number()` | L264 | `ReturnService.generate_return_number()` | `\.generate_return_number\(` |
| `calculate_refund_amount()` | L273 | `ReturnService.calculate_refund_amount()` | `\.calculate_refund_amount\(` |
| `approve()` | L369 | `ReturnService.approve_return()` | `\.approve\(` (Return 컨텍스트) |
| `reject()` | L381 | `ReturnService.reject_return()` | `\.reject\(` (Return 컨텍스트) |
| `confirm_receive()` | L393 | `ReturnService.confirm_receive_return()` | `\.confirm_receive\(` |
| `complete_refund()` | L404 | `ReturnService.complete_refund()` | `\.complete_refund\(` |
| `complete_exchange()` | L413 | `ReturnService.complete_exchange()` | `\.complete_exchange\(` |

### 1.2 Metrics 함수 (packages/selfhealing-python/)

| 함수 | 파일 (라인) | 대체 함수 |
|------|-------------|----------|
| `track_replay()` | services/metrics/updaters.py (L241) | `selfhealing.metrics.decorators.track_replay` |

### 1.3 DLQ 함수 (packages/selfhealing-python/)

| 함수 | 파일 (라인) | 대체 방법 |
|------|-------------|----------|
| `store_with_forensic_context()` | services/dlq/store_operations.py (L125) | `store_failure()` 직접 사용 |

### 1.4 Error Budget Gate 메서드 (packages/selfhealing-python/)

| 메서드 | 파일 (라인) | 대체 메서드 |
|--------|-------------|------------|
| `get_circuit_breaker_status()` | services/error_budget_gate/gate.py (L582) | `get_fault_detector_status()` |
| `reset_circuit_breaker()` | services/error_budget_gate/gate.py (L592) | `reset_fault_detector()` |

### 1.5 Rate Limit 함수 (packages/selfhealing-python/)

| 함수 | 파일 (라인) | 대체 함수 |
|------|-------------|----------|
| `_should_bypass_for_xtest()` | api/django/rate_limit.py (L573) | `_check_bypass_registry()` |

---

## 2. 실행 순서

### Step 1.1: 사용처 검색

각 deprecated 함수의 사용처를 검색합니다.

```bash
# Return 모델 메서드 사용처
grep -rn "\.generate_return_number\(" --include="*.py"
grep -rn "\.calculate_refund_amount\(" --include="*.py"
grep -rn "\.approve\(" shopping/ tests/ --include="*.py"
grep -rn "\.reject\(" shopping/ tests/ --include="*.py"
grep -rn "\.confirm_receive\(" --include="*.py"
grep -rn "\.complete_refund\(" --include="*.py"
grep -rn "\.complete_exchange\(" --include="*.py"

# 기타 함수
grep -rn "from.*metrics.*updaters.*import.*track_replay" --include="*.py"
grep -rn "store_with_forensic_context" --include="*.py"
grep -rn "get_circuit_breaker_status\(" --include="*.py"
grep -rn "_should_bypass_for_xtest" --include="*.py"
```

### Step 1.2: DeprecationWarning 표준화

현재 주석만 있는 deprecated 함수에 런타임 경고 추가합니다.

**적용 패턴**:
```python
def deprecated_method(self, ...):
    """
    .. deprecated:: X.X.X
        Use :meth:`NewClass.new_method` instead.
        Will be removed in version Y.Y.Y.
    """
    import warnings
    warnings.warn(
        "deprecated_method() is deprecated. Use new_method() instead. "
        "This method will be removed in vY.Y.Y.",
        DeprecationWarning,
        stacklevel=2
    )
    # 기존 로직 (새 함수 호출)
    return NewClass.new_method(self, ...)
```

### Step 1.3: 사용처 마이그레이션

**Return 모델 → ReturnService 변경 예시**:

| Before | After |
|--------|-------|
| `return_obj.approve(admin_user)` | `ReturnService.approve_return(return_obj, admin_user)` |
| `return_obj.reject(reason)` | `ReturnService.reject_return(return_obj, reason)` |
| `return_obj.complete_refund()` | `ReturnService.complete_refund(return_obj)` |

### Step 1.4: 테스트 업데이트

**영향받는 테스트 파일 검색**:
```bash
grep -rn "\.approve\(\|\.reject\(\|\.complete_refund\(" tests/ --include="*.py"
```

**테스트 변경 패턴**:
- 직접 모델 메서드 호출 → ReturnService 메서드 호출
- DeprecationWarning 발생 테스트 추가 (선택)

### Step 1.5: 통합 테스트

```bash
# 전체 테스트 실행
pytest tests/ -v

# Deprecation Warning 체크
pytest tests/ -W error::DeprecationWarning
```

---

## 3. 상세 파일별 작업

### 3.1 shopping/models/return_request.py

| 작업 | 설명 |
|------|------|
| L264-269 | `generate_return_number()`: DeprecationWarning 추가 |
| L271-278 | `calculate_refund_amount()`: DeprecationWarning 추가 |
| L367-379 | `approve()`: DeprecationWarning 추가 |
| L381-390 | `reject()`: DeprecationWarning 추가 |
| L392-402 | `confirm_receive()`: DeprecationWarning 추가 |
| L404-411 | `complete_refund()`: DeprecationWarning 추가 |
| L413-424 | `complete_exchange()`: DeprecationWarning 추가 |

### 3.2 packages/selfhealing-python/src/selfhealing/services/metrics/updaters.py

| 작업 | 설명 |
|------|------|
| L241-257 | `track_replay()`: 이미 DeprecationWarning 있음 ✅ |

### 3.3 packages/selfhealing-python/src/selfhealing/services/dlq/store_operations.py

| 작업 | 설명 |
|------|------|
| L110-144 | `store_with_forensic_context()`: 이미 NotImplementedError 발생 ✅ |

### 3.4 packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py

| 작업 | 설명 |
|------|------|
| L580-583 | `get_circuit_breaker_status()`: DeprecationWarning 추가 |
| L590-593 | `reset_circuit_breaker()`: DeprecationWarning 추가 |

### 3.5 packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py

| 작업 | 설명 |
|------|------|
| L573-587 | `_should_bypass_for_xtest()`: 이미 DeprecationWarning 있음 ✅ |

---

## 4. 완료 체크리스트

- [ ] Return 모델 7개 메서드에 DeprecationWarning 추가
- [ ] gate.py 2개 메서드에 DeprecationWarning 추가
- [ ] 사용처 마이그레이션 완료
- [ ] 관련 테스트 업데이트
- [ ] 통합 테스트 통과
- [ ] 코드 리뷰 완료

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 1.0 | 2026-01-23 | 초안 작성 |

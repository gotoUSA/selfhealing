# 234. Response Caching (Polly CachePolicy) 미도입 결정 및 기존 시스템 개선 구현 계획

작성일: 2026-02-16
범위: `packages/selfhealing-python/src/selfhealing`
근거 기준: 코드 본문(추측 없음)

---

## 1) 배경

Response Caching(Polly CachePolicy)은 "성공 응답을 자동 캐시 → 실패 시 이전 성공 응답을 투명하게 반환"하는 패턴이다.
분석 결과, 이 패턴은 현재 시스템의 **데이터 정합성 100% 보장 원칙과 양립 불가**하여 도입하지 않는다.

대신, CachePolicy 도입 시 장점으로 거론된 5가지 항목이 **현재 시스템으로 이미 해결 가능**함을 확인하고,
이를 더 명확히 활용하기 위한 개선 구현 계획을 수립한다.

---

## 2) 미도입 결정 근거

### 2-1. 현재 시스템의 데이터 정합성 보장 구조

현재 시스템은 "불확실한 데이터보다 실패 반환"을 원칙으로 한다.

| 상황 | 현재 동작 | 코드 근거 |
|------|-----------|-----------|
| 원본 호출 성공 | `PolicyOutcome.SUCCESS` 반환 | `fallback.py` L148-L153 — `execute()` 성공 경로 |
| 원본 실패 + fallback 없음 | `PolicyOutcome.FAILURE` 반환 (데이터 없이 실패) | `fallback.py` L243-L249 — `_apply_fallback()` 최종 경로 |
| 원본 실패 + fallback 있음 | `SUCCESS_WITH_FALLBACK` + `metadata["fallback_used"]=True` | `fallback.py` L200-L211 — fallback_chain 성공 경로 |
| CB OPEN + Stale 만료 | `reject=True` (요청 거부) | `stale_cache_integration.py` L575-L581 — `_handle_stale_cache_miss()` 기본 동작 |
| Stale Cache 만료 | 캐시 엔트리 **삭제** | `stale_cache_integration.py` L218-L222 — `StaleCacheStore.get()` 만료 처리 |

### 2-2. CachePolicy 도입 시 정합성 파괴 지점

CachePolicy의 "실패 시 이전 성공 응답 자동 반환"은 다음 3개 지점에서 현재 시스템과 충돌한다:

1. **FallbackPolicy FAILURE 경로 가로채기**: `fallback.py` L243-L249에서 `PolicyOutcome.FAILURE`를 반환하는 경로를 CachePolicy가 캐시 데이터로 대체하면, 소비자가 실패를 인지하지 못함
2. **StaleCacheStore 만료 삭제 우회**: `stale_cache_integration.py` L218-L222에서 `is_expired()` 시 데이터를 삭제하지만, CachePolicy가 별도 TTL로 동일 데이터를 보유하면 삭제된 데이터가 여전히 반환됨
3. **DriftReconciler 검증 불가**: `drift_reconciliation.py` L78-L88의 "Most Restrictive Wins" 전략이 CachePolicy 자동 캐시에는 적용되지 않음

### 2-3. 대체 수단으로도 해결 불가

원본 시스템이 장애 상태이므로 캐시된 데이터의 현재 유효성을 검증할 방법이 없다.
어떤 캐시 무효화 전략(이벤트 기반, TTL, 버전 체크)을 적용해도 원본에 "이 캐시 아직 유효한가?"를 질의할 수 없다.
이것은 Response Caching 패턴 자체의 본질적 한계이다.

---

## 3) 기존 시스템으로 해결 가능한 5가지 항목 — 구현 계획

### 개선 A. FallbackPolicy fallback_fn 수동 주입 보일러플레이트 정리

**현황:** `partition_aware_chain()`이 이미 보일러플레이트를 해결하고 있다.

- 코드 근거: `fallback.py` L477-L544 — `partition_aware_chain()` 함수
- 동작: `state_provider`, `cache_fn`, `db_fn`을 받아 `PartitionState` 가용성 체크를 자동 수행하는 `fallback_chain` 리스트를 생성한다

```python
# fallback.py L507-L518 — 사용 예시 (코드에 존재)
FallbackPolicy(
    fallback_chain=partition_aware_chain(
        state_provider=lambda: health_monitor.get_state(),
        cache_fn=lambda: redis.get("product:123"),
        db_fn=lambda: Product.objects.get(id=123),
    ),
    default_value={"status": "degraded"},
)
```

**구현 작업:**
- `presets.py`의 `standard_pipeline()` (L27-L67)과 `ha_pipeline()` (L70-L135)에 `FallbackPolicy` + `partition_aware_chain()` 조합을 선택적 파라미터로 추가
- 현재 두 프리셋 모두 FallbackPolicy를 포함하지 않으므로, `fallback_default` 파라미터를 추가하여 소비자가 원할 때 활성화할 수 있게 함

**영향 범위:** `resilience/policies/presets.py`

---

### 개선 B. StaleCacheStore update_cache() 수동 호출 누락 방지

**현황:** `record_success()`와 `update_cache()`가 같은 클래스에 있으나 연결되어 있지 않다.

- 코드 근거:
  - `stale_cache_integration.py` L630-L642 — `record_success()`: `backend_success` 카운터 증가 + `canary_manager.record_success()` 호출만 수행
  - `stale_cache_integration.py` L595-L615 — `update_cache()`: 캐시 데이터 저장 — 소비자가 **별도로** 호출해야 함
  - `stale_cache_integration.py` L323-L340 — docstring 사용 예시에서 `service.update_cache()`를 수동 호출하도록 안내

**현재 문제:** `record_success()`를 호출하면서 `update_cache()`를 빠뜨리면 Stale Cache가 비어 있는 상태로 남는다.

**구현 작업:**
- `record_success()`에 `cache_key`와 `response_data` 선택적 파라미터 추가
- 전달된 경우 내부에서 `update_cache()`를 자동 호출하여 누락 방지
- 기존 시그니처(`record_success(service_id)`)와 하위 호환 유지

**영향 범위:** `services/circuit_breaker/stale_cache_integration.py`

---

### 개선 C. CB 상태 무관 범용 캐시 복원 — FallbackPolicy 단독 활용

**현황:** `FallbackPolicy`는 CB 상태와 독립적으로 모든 예외에서 작동한다. `StaleCacheStore`의 CB 종속성과 혼동하지 않아야 한다.

- 코드 근거:
  - `fallback.py` L143-L160 — `execute()`: `try/except Exception`으로 **모든 예외**를 잡아 `_apply_fallback()` 위임
  - `fallback.py` L119-L122 — `_default_predicate()`: `outcome != PolicyOutcome.SUCCESS`이면 Fallback 활성화 — CB 상태 확인 없음
  - 대비: `stale_cache_integration.py` L421-L443 — `should_allow_with_fallback()`: `cb_state` 파라미터를 **필수**로 받으며, CLOSED이면 캐시를 사용하지 않음

**구현 작업:**
- 현재 `FallbackPolicy`가 CB 독립적이라는 사실을 활용하여, `partition_aware_chain()`에 `cache_fn`으로 Stale Cache 조회를 넣으면 CB CLOSED 상태에서의 일시적 실패에도 캐시 조회가 가능
- `partition_aware_chain()`의 docstring에 이 사용 패턴을 추가

**영향 범위:** `resilience/policies/fallback.py` (docstring만 추가)

---

### 개선 D. PolicyComposer 파이프라인 — 이미 해결됨 확인

**현황:** `FallbackPolicy`는 이미 `PolicyComposer` 파이프라인에 완전 통합되어 있다.

- 코드 근거:
  - `composer.py` L220-L246 — `_execute_policy_chain()`에서 FallbackPolicy를 특별 처리: `_apply_fallback()` 호출 + `_FallbackApplied` 시그널로 `SUCCESS_WITH_FALLBACK` outcome 전파
  - `composer.py` L630-L644 — `compose()` 편의 함수 docstring에 `FallbackPolicy` 포함 예시 존재:
    ```python
    compose(
        RetryPolicy(max_retries=3),
        CircuitBreakerPolicy(service_name="payment"),
        BulkheadPolicy(bulkhead=semaphore),
        FallbackPolicy(default_value={"status": "degraded"}),
    ).execute(lambda: call_payment_api())
    ```
  - `__init__.py` L105-L138 — `__all__`에 `FallbackPolicy` 등록 완료

**구현 작업:** 없음 — 이미 완전 구현됨. 추가 변경 불필요.

**영향 범위:** 없음

---

### 개선 E. partition_aware_chain() 캐시 가용성 검사 — 의도된 안전장치 확인

**현황:** `partition_aware_chain()`이 매 실행 시 `PartitionState`를 조회하는 것은 **캐시 서버** 자체의 생존을 확인하는 안전장치이다.

- 코드 근거:
  - `fallback.py` L523-L527 — `_cache_fallback()` 내부:
    ```python
    def _cache_fallback() -> T:
        ps = state_provider()
        if ps.cache_available:
            return cache_fn()
        raise RuntimeError("Cache unavailable at fallback execution time")
    ```
  - `ps.cache_available`이 False면 캐시 조회를 **시도하지 않고** `RuntimeError`를 발생시킴
  - 만약 이 체크가 없으면 죽은 캐시 서버에 접근을 시도하다 타임아웃 발생

**구현 작업:** 없음 — 이것은 중복이 아니라 의도된 안전장치이다. 제거하면 오히려 장애 전파(타임아웃) 위험이 있으므로 유지한다.

**영향 범위:** 없음

---

## 4) 구현 대상 요약

| 항목 | 구현 필요 | 대상 파일 | 작업 내용 |
|------|-----------|-----------|-----------|
| A. Preset에 FallbackPolicy 통합 | **예** | `resilience/policies/presets.py` | `standard_pipeline()`, `ha_pipeline()`에 `fallback_default` 파라미터 추가 |
| B. record_success() 캐시 자동 저장 | **예** | `services/circuit_breaker/stale_cache_integration.py` | `record_success()`에 `cache_key`, `response_data` 선택적 파라미터 추가 |
| C. partition_aware_chain() 사용 패턴 문서화 | **예** | `resilience/policies/fallback.py` | docstring에 CB 독립 캐시 조회 패턴 추가 |
| D. PolicyComposer 통합 | **아니오** | — | 이미 완전 구현됨 |
| E. 캐시 가용성 체크 | **아니오** | — | 의도된 안전장치, 변경 불필요 |

---

## 5) Response Caching 패턴 최종 판정

| 평가 항목 | 판정 |
|-----------|------|
| 도입 시 고유 장점 (기존 시스템으로 불가능한 것) | **0개** |
| 도입 시 깨지는 기존 보장 | 데이터 정합성 100% (3개 충돌 지점) |
| 도입 시 Trade-off (해결 불가) | Stale 데이터 투명 반환, 메모리 증가 |
| 기존 시스템으로 대체 가능한 장점 | **5개 중 5개** (그 중 2개는 이미 구현 완료) |

**결론:** Response Caching(Polly CachePolicy)을 도입하지 않고, 기존 시스템의 3개 파일 개선으로 동일 효과를 달성한다.

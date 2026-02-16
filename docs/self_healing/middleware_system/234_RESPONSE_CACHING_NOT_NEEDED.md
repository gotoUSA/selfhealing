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

---

## 6) 구현 계획 리뷰 및 보완 사항

### R1. FallbackPolicy 위치 — 가장 바깥쪽 배치 확정

**리뷰 판정: 충분함 — 추가/수정 불필요**

`composer.py` `compose()` 편의 함수 (L630-L644)의 docstring이 실행 순서를 명시한다:

```
policies 순서 = 바깥→안쪽 실행 순서:
- compose(Retry, CB, Bulkhead).execute(func)
- = Retry(CB(Bulkhead(func)))
```

`_execute_policy_chain()` (L221)에서 `reversed(self._policies)`로 역순 중첩을 구성하므로,
리스트 마지막 = 가장 바깥쪽 래퍼가 된다. `compose()` docstring 예시에서도 `FallbackPolicy`가 마지막에 위치한다.

따라서 `presets.py` 개선 시 `FallbackPolicy`를 `compose()` 인자 목록의 **마지막**에 배치한다.

**구현 시 적용 방법:**

```python
# presets.py — standard_pipeline 개선 예시
compose(
    RetryPolicy(config=retry_config),
    FallbackPolicy(...),  # 마지막 = 가장 바깥쪽
)
```

---

### R2. Presets 파라미터 확장 — 3단계 Fallback 소스 지원

**리뷰 판정: 충분함 — 원안의 `fallback_default`만 언급한 부분을 3단계로 확장**

`FallbackPolicy.__init__()` (`fallback.py` L88-L97)이 이미 3단계를 지원한다:
- `fallback_chain: list[Callable[[], T]]` — `partition_aware_chain()` 등 고급 사용
- `fallback_fn: Callable[[], T]` — 단일 callable fallback
- `default_value: T` — 고정 기본값

`_apply_fallback()` (`fallback.py` L195-L249)의 실행 순서: `fallback_chain → fallback_fn → default_value`

**네이밍 검증:**
- `fallback_chain`: `fallback.py`에서 이미 사용 중 (L93, L111, L195) — 동일 이름 재사용하여 일관성 유지
- `fallback_fn`: `fallback.py`에서 이미 사용 중 (L91, L109, L213) — 동일 이름 재사용
- `fallback_default`: 현재 코드베이스에 존재하지 않음 — `FallbackPolicy`의 `default_value`에 매핑
- `presets.py`에서 `fallback_chain`, `fallback_fn`, `fallback_default`는 현재 사용되지 않으므로 충돌 없음

**구현 시 파라미터 설계:**

```python
def standard_pipeline(
    service_name: str,
    max_retries: int = 3,
    domain: str = "default",
    # --- Fallback 선택적 파라미터 (3단계) ---
    fallback_chain: list[Callable[[], Any]] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    fallback_default: Any = None,
) -> PolicyComposer:
```

셋 중 하나라도 `None`이 아니면 `FallbackPolicy`를 주입하고, 모두 `None`이면 포함하지 않는다.

---

### R3. 캐시 저장의 동기 처리 — 인메모리 기반 확인

**리뷰 판정: 충분함 — 추가/수정 불필요**

`StaleCacheStore` (`stale_cache_integration.py` L186-L189):
```python
self._cache: dict[str, StaleCacheEntry] = {}
self._lock = threading.RLock()
```

인메모리 `dict` + `threading.RLock()` 기반이다. `set()` (L255-L270)은
`dict.__setitem__` + `_evict_oldest()` — 네트워크 I/O 없는 순수 메모리 연산이다.

비동기 큐/스레드 도입은 오버헤드만 증가시키므로 동기 처리가 적합하다.

---

### R4. Mutable Reference 안전장치 — **보완 추가**

**리뷰 판정: 보완 필요 — docstring 경고 추가**

`StaleCacheStore.set()` (`stale_cache_integration.py` L261-L268):
```python
entry = StaleCacheEntry(
    key=key,
    value=value,  # 참조(Reference) 그대로 저장
    ...
)
self._cache[key] = entry
```

인메모리 dict이므로 Python 객체의 **참조가 그대로 저장**된다.
호출자가 저장 후 원본 객체를 수정하면 캐시 내 데이터도 함께 오염된다.

**위험 시나리오:**
1. `record_success(data)` → `data` 참조가 캐시에 저장됨
2. 호출자가 `data["price"] = new_price` 수정
3. Fallback 시 오염된 `data`가 반환됨

**현재 코드베이스에서 `copy.copy` / `copy.deepcopy` / `import copy` 사용:** 0건 — 코드베이스 전체에서 복사 패턴이 사용되지 않는다.

**보완 방법:** 성능 오버헤드를 고려하여 `copy.deepcopy()` 자동 적용 대신, `StaleCacheStore.set()`의 docstring에 **불변 객체 원칙 경고**를 추가한다.

**구현 작업:**
- `StaleCacheStore.set()` docstring에 다음 경고 추가:

```
Warning:
    인메모리 캐시이므로 value의 참조(Reference)가 그대로 저장된다.
    저장 후 원본 객체를 수정하면 캐시 데이터도 오염된다.
    호출자는 다음 중 하나를 준수해야 한다:
    1. 저장할 객체를 불변(Immutable)으로 취급
    2. 저장 전 copy.copy() 또는 copy.deepcopy()로 사본 전달
```

**영향 범위:** `services/circuit_breaker/stale_cache_integration.py` (docstring만 추가)

---

### R5. Cache Key 헬퍼 메서드 — **추가 구현 필요**

**리뷰 판정: 추가 필요 — 헬퍼 메서드 구현**

현재 `cache_key`는 3곳에서 수동 문자열로 전달된다:
- `should_allow_with_fallback(cache_key="payment:user123")` (L407)
- `update_cache("payment:user123", result)` (docstring L337)
- FallbackPolicy의 `cache_fn=lambda: store.get("payment:user123")`

**기존 패턴 확인 — `ErrorBudgetGate._build_cache_key()`** (`gate.py` L119-L121):
```python
@staticmethod
def _build_cache_key(region: str | None, tier_id: str | None) -> str:
    """티어/리전 조합으로 캐시 키 생성."""
    return f"{region or '__global__'}:{tier_id or '__global__'}"
```

시스템 내에 `_build_cache_key` 네이밍이 `ErrorBudgetGate`에서 이미 사용 중이다.
`CanaryWithStaleCacheService`에 같은 이름을 사용하면 혼동되므로, **`build_stale_cache_key`** 네이밍을 사용한다.

**네이밍 검증:**
- `build_stale_cache_key`: 코드베이스 전체에 0건 — 충돌 없음
- `build_cache_key`: `ErrorBudgetGate._build_cache_key`와 의미가 다르므로 구분 필요
- `_build_cache_key`는 private(`_` 접두사) + 클래스 스코프이므로 기술적 충돌은 없으나, 가독성을 위해 명확히 구분한다

**구현 작업:**
- `CanaryWithStaleCacheService`에 정적 메서드 추가:

```python
@staticmethod
def build_stale_cache_key(domain: str, identifier: str) -> str:
    """
    Stale Cache Key 생성 규칙 중앙화.

    should_allow_with_fallback(), update_cache(), FallbackPolicy cache_fn에서
    동일한 키를 사용하도록 보장한다.

    Args:
        domain: 서비스 도메인 (예: "payment", "product")
        identifier: 리소스 식별자 (예: "user123", "order456")

    Returns:
        정규화된 캐시 키 (예: "payment:user123")
    """
    return f"{domain}:{identifier}"
```

- 모듈 레벨 편의 함수도 추가 (기존 `should_allow_with_fallback`, `update_stale_cache` 등과 동일 패턴):

```python
def build_stale_cache_key(domain: str, identifier: str) -> str:
    """Stale Cache Key 생성."""
    return CanaryWithStaleCacheService.build_stale_cache_key(domain, identifier)
```

**영향 범위:** `services/circuit_breaker/stale_cache_integration.py`

---

### R6. Fallback 연결 Factory — docstring 패턴 예시로 충분

**리뷰 판정: 충분함 — 추가 팩토리 불필요, docstring 예시만 추가**

`partition_aware_chain()` (`fallback.py` L487-L544)의 `cache_fn` 시그니처는 `Callable[[], T]`이다.
`StaleCacheStore.get(key)` 호출은 `lambda: store.get(key)` 한 줄이므로
별도 팩토리 함수의 추상화 이점이 없다.

**구현 작업:** 개선 C(docstring 추가) 시 다음 예시를 포함한다:

```python
# StaleCacheStore를 FallbackPolicy와 연결하는 패턴 (CB 독립)
from selfhealing.services.circuit_breaker.stale_cache_integration import (
    get_canary_stale_cache_service,
)

stale_service = get_canary_stale_cache_service()
cache_key = CanaryWithStaleCacheService.build_stale_cache_key("product", "123")

FallbackPolicy(
    fallback_chain=partition_aware_chain(
        state_provider=lambda: health_monitor.get_state(),
        cache_fn=lambda: stale_service._cache.get(cache_key).value,
        db_fn=lambda: Product.objects.get(id=123),
    ),
    default_value={"status": "degraded"},
)
```

**영향 범위:** `resilience/policies/fallback.py` (docstring만 추가)

---

### R7. 캐시 쓰기 실패 Suppress — 기존 패턴과 일치

**리뷰 판정: 충분함 — 추가/수정 불필요**

기존 코드에서 동일한 `try/except + logger.warning + suppress` 패턴이 2곳에 존재한다:

1. `composer.py` `_process_sinks()` (L614-L621):
   ```python
   except Exception as e:
       logger.warning("Sink handle_failure failed: %s", e)
   ```

2. `fallback.py` `_apply_fallback()` (L204-L206):
   ```python
   except Exception as e:
       logger.warning(f"Fallback chain[{i}] failed: {e}")
       continue
   ```

`record_success()` 내부의 캐시 저장 실패도 동일 패턴을 적용한다:
```python
if cache_key is not None and response_data is not None:
    try:
        self.update_cache(cache_key, response_data, service_id=service_id)
    except Exception as e:
        logger.warning("Auto cache update failed (suppressed): %s", e)
```

원본 비즈니스 로직은 이미 성공했으므로 캐시 저장 실패로 전체 요청을 실패 처리하면
시스템의 "불확실한 데이터보다 실패 반환" 원칙에 위배된다 (성공을 실패로 바꾸는 것이므로).

---

## 7) 최종 구현 대상 요약 (리뷰 반영)

| 항목 | 구현 필요 | 대상 파일 | 작업 내용 |
|------|-----------|-----------|-----------|
| A. Preset에 FallbackPolicy 통합 | **예** | `resilience/policies/presets.py` | `fallback_chain`, `fallback_fn`, `fallback_default` 3단계 파라미터 추가 (R1, R2) |
| B. record_success() 캐시 자동 저장 | **예** | `services/circuit_breaker/stale_cache_integration.py` | `cache_key`, `response_data` 선택적 파라미터 + suppress 패턴 (R3, R7) |
| B+. StaleCacheStore.set() docstring 경고 | **예** | `services/circuit_breaker/stale_cache_integration.py` | Mutable Reference 오염 경고 docstring 추가 (R4) |
| B++. build_stale_cache_key() 헬퍼 | **예** | `services/circuit_breaker/stale_cache_integration.py` | 정적 메서드 + 모듈 편의 함수 추가 (R5) |
| C. partition_aware_chain() 사용 패턴 문서화 | **예** | `resilience/policies/fallback.py` | CB 독립 캐시 조회 + StaleCacheStore 연결 예시 docstring 추가 (R6) |
| D. PolicyComposer 통합 | **아니오** | — | 이미 완전 구현됨 |
| E. 캐시 가용성 체크 | **아니오** | — | 의도된 안전장치, 변경 불필요 |

# 227. CircuitBreakerPolicy 전환 설계

## 1. 개요

Circuit Breaker는 현재 시스템에서 **가장 독립적인 패턴** 중 하나이다.
`services/circuit_breaker/service.py`는 자체 패키지 내부만 참조하며,
크로스-패턴 하드코딩 의존성이 0건이다.

전환의 핵심은 기존 `CircuitBreakerService.should_allow()` 기반의 **조건 검사 서비스**를
`ResiliencePolicy.execute()` 기반의 **함수 래핑 Policy**로 변환하는 것이다.

## 2. 현재 구현 분석

### 2.1 파일 구조

```
services/circuit_breaker/
├── service.py              # 메인 서비스 (907줄) — ManualControlMixin + ProtectionMixin 상속
├── config.py               # CircuitBreakerConfig, CircuitBreakerResult, CircuitState
├── protection.py           # ProtectionMixin (rate limit cascade, self-DDoS)
├── manual_control.py       # ManualControlMixin (force_open/close)
├── rate_limit_tracker.py   # RateLimitTracker
├── recovery_strategy.py    # 복구 전략
├── convenience.py          # 모듈 레벨 편의 함수
├── load_shedding/          # Load Shedding 서브패키지
├── adaptive_threshold.py   # 적응형 임계값
└── ...
```

### 2.2 현재 사용 패턴 — "조건 검사" 방식

현재 CB는 **함수를 래핑하지 않는다**. 소비자가 직접 `should_allow()`를 호출한다:

```python
# 현재 사용 패턴 (shopping/tasks/payment_recovery_tasks.py L415)
from selfhealing.services import get_circuit_breaker_service

service = get_circuit_breaker_service()
if service.should_allow("external_api"):
    # 요청 진행
    result = call_external_api()
else:
    # 차단됨 — 소비자가 직접 fallback 처리
    return handle_fallback()
```

`service.py` L228-L268의 `should_allow()`:
- `CircuitState.CLOSED` → True
- `CircuitState.OPEN` → recovery_timeout 경과 시 HALF_OPEN 전환 후 True, 아니면 False
- `CircuitState.HALF_OPEN` → True (제한된 요청 허용)

### 2.3 should_allow_with_fallback() — 내장 Fallback

`service.py` L283에 `should_allow_with_fallback()` 메서드가 존재한다:

```python
def should_allow_with_fallback(
    self,
    service_name: str,
    cache_key: str | None = None,        # 캐시 fallback
    default_response: Any | None = None,  # 기본값 fallback
    request_data: dict[str, Any] | None = None,  # DLQ fallback
) -> CircuitBreakerFallbackResult:
```

이 메서드는 CB가 OPEN일 때 3가지 Fallback 전략을 내장한다:
1. `strategy == "cache"` → 캐시 데이터 반환
2. `strategy == "dlq"` → DLQ 큐잉
3. `default_response` → 기본 응답

**문제점**: Fallback 로직이 CB 내부에 하드코딩. Policy Composition에서는 FallbackPolicy가 담당해야 한다.

### 2.4 크로스-패턴 의존성 (service.py 내부)

| 의존 대상 | 위치 | 방식 | 용도 |
|-----------|------|------|------|
| `ProviderRegistry` | L180 lazy import | Repository 획득 | 인프라 의존 (유지) |
| `audit_helpers.log_cb_state_change_audit` | L253 lazy import | 상태 변경 감사 | Hook으로 분리 가능 |
| `EventBus` | L270 lazy import | 상태 변경 이벤트 발행 | Hook으로 분리 가능 |

**핵심 판단**: 2건의 lazy import(Audit, EventBus)가 있지만, 이미 try/except로 Fail-Open 처리되어 있으므로 Hook으로 분리하기 용이하다.

## 3. 전환 설계

### 3.1 CircuitBreakerPolicy 클래스

```python
class CircuitBreakerPolicy(ResiliencePolicy[T]):
    """
    Circuit Breaker Policy — 함수 래핑 방식.

    현재 should_allow() 기반의 "조건 검사" 방식을
    execute() 기반의 "함수 래핑" 방식으로 변환한다.

    내부적으로 기존 CircuitBreakerService를 재사용한다.
    """

    def __init__(
        self,
        service_name: str,
        cb_service: CircuitBreakerService | None = None,
        config: CircuitBreakerConfig | None = None,
        # 예외 필터링 (§8.2 확정)
        failure_exceptions: tuple[type[Exception], ...] = (Exception,),
        ignore_exceptions: tuple[type[Exception], ...] = (),
    ):
        self._service_name = service_name
        self._cb_service = cb_service or CircuitBreakerService(config=config)
        self._failure_exceptions = failure_exceptions
        self._ignore_exceptions = ignore_exceptions

    @property
    def name(self) -> str:
        return "circuit_breaker"

    def _is_failure(self, error: Exception) -> bool:
        """
        예외가 실패로 카운팅되어야 하는지 판단.

        ignore_exceptions에 해당하면 False.
        failure_exceptions에 해당하면 True.
        """
        if isinstance(error, self._ignore_exceptions):
            return False
        return isinstance(error, self._failure_exceptions)

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        context: PolicyContext | None = None,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        CB 상태 기반 함수 실행.

        1. should_allow() → False면 REJECTED 반환
        2. True면 func 실행
        3. 성공 → record_success()
        4. 실패 → _is_failure() 판단 후 record_failure() → 임계값 초과 시 OPEN 전환

        Args:
            func: 실행할 함수
            *args: 함수 위치 인자
            context: PolicyContext (225 Protocol 준수)
            **kwargs: 함수 키워드 인자

        Returns:
            PolicyResult[T]: 통합 결과. 예외를 던지지 않는다 (reject 시).
            다만 func 실행 실패 시에는 raise하여 상위 Policy에 전파한다.
        """
        # CB 비활성화 시 바로 실행
        if not self._cb_service.is_enabled:
            result = func(*args, **kwargs)
            return PolicyResult(value=result, outcome=PolicyOutcome.SUCCESS,
                              executed_policies=["circuit_breaker"])

        # 요청 허용 여부 확인
        if not self._cb_service.should_allow(self._service_name):
            return PolicyResult(
                outcome=PolicyOutcome.REJECTED,
                error=CircuitBreakerOpenError(self._service_name),
                executed_policies=["circuit_breaker"],
                metadata={
                    "service_name": self._service_name,
                    "state": self._cb_service.get_state(self._service_name),
                },
            )

        # 함수 실행
        try:
            result = func(*args, **kwargs)
            self._cb_service.record_success(self._service_name)
            return PolicyResult(
                value=result,
                outcome=PolicyOutcome.SUCCESS,
                executed_policies=["circuit_breaker"],
            )
        except Exception as e:
            # 예외 필터링: ignore_exceptions은 카운팅하지 않음
            if self._is_failure(e):
                self._cb_service.record_failure(
                    self._service_name,
                    error_context={"error": str(e), "type": type(e).__name__},
                )
            raise  # 상위 Policy(Retry 등)에서 처리하도록 전파
```

### 3.2 기존 should_allow() 패턴과의 공존

기존 소비자의 `should_allow()` 직접 호출은 즉시 변경하지 않는다:

```python
# 기존 방식 (계속 지원)
if cb_service.should_allow("api"):
    call_api()

# 새로운 방식 (Policy Composition)
policy = compose(
    circuit_breaker("api"),
    retry(max_attempts=3),
    fallback(default_fn),
)
result = policy.execute(call_api)
```

### 3.3 should_allow_with_fallback() 분리

현재 CB 내부의 Fallback 로직을 FallbackPolicy로 분리한다:

**Before**:
```python
# service.py L283 — Fallback이 CB 내부에 하드코딩
result = cb_service.should_allow_with_fallback(
    "api",
    cache_key="api:cache",
    default_response={"status": "unavailable"},
)
```

**After**:
```python
# CB 거부 시 FallbackPolicy가 자동 처리
policy = compose(
    circuit_breaker("api"),
    fallback(cache_fn=lambda: get_cache("api:cache"),
             default={"status": "unavailable"}),
)
result = policy.execute(call_api)
```

## 4. record_success / record_failure 메서드 — 확정

### 4.1 메서드 존재 확인 (문서 초기 기술 오류 정정)

~~현재 `CircuitBreakerService`에 이 메서드가 **존재하지 않는다** (토글 기반이므로).~~

**정정**: `CircuitBreakerService`에 `record_failure()`와 `record_success()`가 **이미 구현되어 있다**.

- `service.py` L451: `record_failure(self, service_name: str, error_context: dict[str, Any] | None = None)`
  — failure_count 증가 후 `_should_open_circuit()` 판단, 임계값 초과 시 자동 OPEN 전환
- `service.py` L713: `record_success(self, service_name: str)`
  — HALF_OPEN 상태에서 `success_threshold` 도달 시 자동 CLOSED 전환
- `service.py` L523-L561: `_should_open_circuit()` — count-based (`failure_count >= failure_threshold`)
  AND rate-based (`failure_rate_threshold > 0` 시 비율 계산) 양쪽 구현 완료

Repository 레이어에도 대응 메서드가 존재한다:
- `interfaces/repositories.py` L620: `record_failure(service_name) -> CircuitBreakerStateData` (ABC)
- `interfaces/repositories.py` L625: `record_success(service_name) -> CircuitBreakerStateData` (ABC)
- `adapters/redis/circuit_breaker.py` L498: `RedisCircuitBreakerStateRepository.record_failure()`
- `adapters/memory/circuit_breaker.py` L234: `InMemoryCircuitBreakerStateRepository.record_failure()`

### 4.2 Config에 이미 존재하는 자동 카운팅 설정

`config.py` L34-L48에 자동 카운팅용 Config 필드가 **이미 존재**한다:

```python
# config.py — 이미 존재하는 필드들
failure_threshold: int = 5              # count-based 임계값
recovery_timeout: int = 60              # seconds
success_threshold: int = 2              # HALF_OPEN → CLOSED 성공 횟수
minimum_calls: int = 10                 # 최소 호출 수 (false positive 방지)
sliding_window_size: int = 100          # Sliding Window 크기
failure_rate_threshold: float = 0.0     # 0 = disabled, >0 = percentage
```

### 4.3 선택지 재평가 → B안 (자동 카운팅) 확정

| 방안 | 설명 | 상태 |
|------|------|------|
| A. 토글 유지 | CB는 운영자 수동 제어만 | ❌ 기각 — record_failure가 이미 존재 |
| **B. 자동 카운팅** | record_failure() + 임계값 초과 시 자동 OPEN | **✅ 확정** — 이미 구현되어 있음 |
| C. 하이브리드 | 수동 제어 + ProtectionMixin 활용 | ❌ 기각 — B안이 이미 하이브리드를 포함 |

**B안이 실질적으로 하이브리드인 이유**:
- `record_failure()` L468: `if state.manually_controlled: return` — 수동 제어 시 자동 카운팅 skip
- `ProtectionMixin`의 429 cascade → `force_open()` 경로는 별개 유지
- 즉, 자동 카운팅(record_failure)과 수동 제어(force_open/close)와 429 보호(ProtectionMixin)가 **3개 레이어로 공존**

### 4.4 3.1 코드 수정 사항

`record_failure()`의 실제 시그니처는 `(service_name: str, error_context: dict | None)` 이므로,
3.1 코드의 `self._cb_service.record_failure(self._service_name, e)` 호출을 수정해야 한다:

```python
# Before (3.1 의사코드)
self._cb_service.record_failure(self._service_name, e)

# After (실제 시그니처 대응)
self._cb_service.record_failure(
    self._service_name,
    error_context={"error": str(e), "type": type(e).__name__},
)
```

## 5. 영향 범위

### 5.1 현재 직접 사용처

| 위치 | 사용 방식 |
|------|-----------|
| `shopping/tasks/payment_recovery_tasks.py` L415 | `get_circuit_breaker_service()` → `should_allow()` |
| `tasks/traffic_aware_replay.py` L74-L81 | `get_circuit_breaker_service()` → 상태 확인 |
| `services/circuit_breaker/convenience.py` | 모듈 레벨 편의 함수 (should_allow_request 등) |

### 5.2 변경 불필요

| 컴포넌트 | 이유 |
|----------|------|
| `manual_control.py` | 운영자 제어 — Policy와 별개 레이어 |
| `protection.py` | self-DDoS 보호 — 인프라 레이어 유지 |
| `rate_limit_tracker.py` | CB 내부 트래커 — 리팩토링 불필요 |
| `load_shedding/` | 인프라 가드 — Policy와 별개 |

## 6. 체크리스트

- [x] `CircuitBreakerPolicy` 클래스 생성 (§3.1 갱신 코드 기반)
- [x] `CircuitBreakerOpenError` 예외 타입 정의 (`services/circuit_breaker/exceptions.py` 신설, §8.6)
- [x] `execute()` 구현 — `context: PolicyContext | None` 서명 준수 (225 Protocol)
- [x] `_is_failure()` 예외 필터링 구현 (§8.2)
- [x] `failure_exceptions` / `ignore_exceptions` 생성자 파라미터 (§8.2)
- [x] `should_allow_with_fallback()` → `DeprecationWarning` 추가 (§8.7, 229번 연계)
- [x] Audit lazy import → PolicyHook `on_reject`, `on_success` 연결 (`hooks.py` `AuditPolicyHook`, `policy.py` `_invoke_hooks`)
- [x] EventBus lazy import → PolicyHook 연결 (`hooks.py` `EventBusPolicyHook`, `policy.py` `_invoke_hooks`)
- [x] 기존 `convenience.py` 함수들의 하위 호환성 유지
- [x] `service_name` 미지정 시 `func.__qualname__` fallback (데코레이터용, §8.8)
- [x] 기존 테스트 통과 확인
- [x] **Sliding Window 구현** — `_should_open_circuit()`에서 `config.sliding_window_size` 실제 사용 (§9)
- [x] `InMemoryCircuitBreakerStateRepository.record_failure()`에 ring buffer 도입 (§9.3)
- [x] `_should_open_circuit()`의 `total_calls` 계산을 ring buffer + `sliding_window_size` cap 기반으로 수정 (§9.4)
- [x] `failure_rate_threshold` 기본값 `0.0` → 프로덕션 활성화 가이드 작성 (§9.8)
- [x] **저장소 전환** — `ProviderRegistry`에 `"layered"` 등록 추가, `CircuitBreakerPolicy`가 기본으로 layered 사용 (§7.4)
- [x] `CircuitBreakerPolicy`가 `LayeredRepository` 기반 `CircuitBreakerService`를 사용하도록 통합 (§7.4)
- [x] `CircuitBreakerPolicy(ResiliencePolicy[T])` 명시적 Protocol 상속 — `isinstance()` 검증 통과
- [x] **단위 테스트 89건 작성** — CircuitBreakerPolicy, Sliding Window, DeprecationWarning, export 검증 (§10)

## 7. 설계 논의 확정 사항

8가지 설계 질문에 대한 논의 결과를 확정한다. 모든 결정은 코드 근거에 기반한다.

### 7.1 record_failure / record_success 실체 확인

**질문**: `CircuitBreakerService`에 `record_failure()`와 `record_success()`가 실제로 구현되어 있는가?
문서 §4에서 "존재하지 않는다"고 기술했으나 사실인가?

**확정**: **이미 구현되어 있다.** 문서 §4의 초기 기술은 오류였으며, 정정 완료 (§4.1 참조).

**코드 근거**:
- `service.py` L451: `record_failure(self, service_name: str, error_context: dict[str, Any] | None = None)`
  — `repository.record_failure()` 호출 후 `_should_open_circuit()` 판정
- `service.py` L713: `record_success(self, service_name: str)`
  — HALF_OPEN에서 `success_count >= config.success_threshold` 시 CLOSED 전환
- `service.py` L473: `updated_state = self.repository.record_failure(service_name)` — Repository 위임
- `service.py` L737: `updated_state = self.repository.record_success(service_name)` — Repository 위임
- `interfaces/repositories.py` L620-625: `record_failure()`, `record_success()` ABC 메서드
- `adapters/redis/circuit_breaker.py` L498: Redis 구현체
- `adapters/memory/circuit_breaker.py` L234: InMemory 구현체

**3.1 코드 수정**: `record_failure(name, e)` → `record_failure(name, error_context={...})` 변경 완료.

### 7.2 예외(Exception) 판단 기준 — `failure_exceptions` / `ignore_exceptions`

**질문**: `CircuitBreakerConfig`에 예외 필터링 필드가 없는 상태에서,
Policy가 함수를 래핑하면 "어떤 예외를 실패로 간주할 것인가"를 어떻게 결정하는가?

**확정**: `CircuitBreakerPolicy.__init__`에 `failure_exceptions`와 `ignore_exceptions` 파라미터 추가.
Config(dataclass) 자체에는 추가하지 않고 **Policy 생성자 레벨**에서 처리한다.

**코드 근거**:
- `service.py` L451-497: `record_failure()`는 호출되면 **무조건** failure 카운팅.
  예외 판별 로직이 내부에 없음 → 호출자(Policy)가 판단해야 함
- `models.py` L55-56: `RetryPolicyConfig`의 `retryable_exceptions` / `non_retryable_exceptions` 선례
  — Retry도 Config가 아닌 PolicyConfig 레벨에서 예외 필터링 보유

**Config에 넣지 않는 이유**:
- `CircuitBreakerConfig`는 `from_settings()` 팩토리로 Redis/설정에서 로드되는 dataclass
- `tuple[type[Exception], ...]`는 직렬화 불가 → RuntimeConfigManager 호환 불가
- `RetryPolicyConfig`도 같은 이유로 별도 Policy 레벨에 보유

**네이밍 선택**:
| 후보 | 채택 | 이유 |
|------|------|------|
| `record_failure_exceptions` | ❌ | 과도하게 긴 이름 |
| `failure_exceptions` | ✅ | `RetryPolicyConfig.retryable_exceptions`와 유사한 간결함 |
| `ignore_exceptions` | ✅ | `RetryPolicyConfig.non_retryable_exceptions`의 의미 동일 (더 직관적) |

**네이밍 충돌 검증**: `failure_exceptions` — 시스템 전체 검색 0건. `ignore_exceptions` — 시스템 전체 검색 0건. 안전.

**구현 코드** (§3.1 갱신 반영):
```python
def _is_failure(self, error: Exception) -> bool:
    if isinstance(error, self._ignore_exceptions):
        return False
    return isinstance(error, self._failure_exceptions)
```

### 7.3 Half-Open Probe 성공 처리 — `record_success()` 호출만으로 충분

**질문**: HALF_OPEN 상태에서 탐침 요청이 성공하면 누가 서킷을 CLOSED로 바꾸는가?
`CircuitBreakerPolicy.execute()` 내부에서 `force_close()`를 호출해야 하는가?

**확정**: **`record_success()`만 호출하면 된다.** `force_close()` 호출은 불필요.

**코드 근거**:
- `service.py` L727-750:
  ```python
  if state.state == "half_open":
      updated_state = self.repository.record_success(service_name)
      if updated_state.success_count >= self.config.success_threshold:
          self.repository.update_state(
              service_name=service_name,
              state="closed",
              failure_count=0,
              success_count=0,
              opened_at=None,
          )
          circuit_closed = True
  ```
  HALF_OPEN에서 `success_threshold` (기본값 2) 도달 시 **자동 CLOSED 전환**.
- `service.py` L754-766: 전환 후 동기 콜백 → Audit → Metrics Push → 조건부 Replay 순차 실행
- `service.py` L748-750: CLOSED 상태에서 `record_success()` 호출 시 `failure_count`를 0으로 리셋

**force_close()와의 차이**:
| 속성 | `record_success()` | `force_close()` |
|------|--------------------|-----------------|
| `manually_controlled` 설정 | ❌ | ✅ (True) |
| Kill Switch 체크 | ❌ | ✅ |
| TTL 설정 | ❌ | ✅ |
| 용도 | 자동 복구 | 운영자 수동 제어 |

Policy는 자동 복구 경로이므로 `record_success()`가 정확히 맞는 메서드.

### 7.4 상태 저장소 — Layered 하이브리드 확정 (§7.4 개정)

**질문**: 매 `execute()` 호출마다 Redis I/O가 발생하는데 이를 수용하는가?
로컬 메모리 캐싱(Sliding Window)을 별도 도입해야 하는가?

~~**확정**: 기존 `ProviderRegistry` 경유 Redis Repository를 그대로 사용. 로컬 캐싱 추가 없음.~~

**개정**: **`LayeredCircuitBreakerStateRepository` (L1=Memory, L2=Redis) 하이브리드 구조로 전환.**
Redis 직접 사용은 hot path에 I/O가 개입하여 레이턴시 민감 서비스에 부적합.
Sliding Window 구현을 위해서도 L1 Memory 레이어가 필수 (§9 참조).

#### 7.4.1 기존 판정의 문제 (Redis 직접)

Redis 직접 사용 시 `execute()` 1회당 최소 2~3 Redis I/O:
| 단계 | 호출 | Redis 작업 |
|------|------|------------|
| `should_allow()` | `get_or_create()` | 1 HGETALL |
| (OPEN→HALF_OPEN 시) | `update_state()` | 1 HSET |
| (성공) | `record_success()` | 1 HGETALL + 1 HSET |
| (실패) | `record_failure()` | 1 HGETALL + 1 HSET + (자동 OPEN 시) 1 HSET |

- 분산 환경에서 상태 일관성은 중요하나, **판정 호출을 hot path에서 Redis로 매번 치는 것은 과도**
- `failure_rate_threshold` 활성화 시 sliding window가 필수인데, Redis에서 window를 관리하면
  `ZADD`/`ZRANGEBYSCORE` 등 추가 I/O 발생 → 더 악화

#### 7.4.2 개정 — Layered 하이브리드 구조

**확정**: `LayeredCircuitBreakerStateRepository` 사용.

**코드 근거 — 이미 완전 구현되어 있다**:
- `adapters/memory/layered_repository/__init__.py`: 7개 Mixin + Base 조합
  ```
  LayeredCircuitBreakerStateRepository(
      L2LoadMixin, ErrorHandlingMixin, DriftOperationsMixin,
      L2SyncMixin, RepositoryOperationsMixin, MonitoringMixin,
      AuditHelpersMixin, LayeredRepositoryBase,
      CircuitBreakerStateRepository,
  )
  ```
- `adapters/memory/layered_repository/base.py` L73: `self._l1 = InMemoryCircuitBreakerStateRepository()`
- `adapters/memory/layered_repository/base.py` L74: `self._l2 = l2_repo` (Redis 등 외부 저장소)
- `adapters/memory/layered_repository/repository_operations.py` L173-177:
  ```python
  def record_failure(self, service_name: str) -> CircuitBreakerStateData:
      """L1에서 실패 기록 후 L2 동기화."""
      result = self._l1.record_failure(service_name)
      self._sync_to_l2_async(service_name, result)
      return result
  ```
- `adapters/memory/layered_repository/l2_sync.py`: L2 비동기 동기화 + 타임아웃 (Fail-Fast)
- `adapters/memory/layered_repository/base.py` L89-99: DriftReconciler, Bulkhead, ShadowLogger 통합
- `services/factory/base.py` L193-220: `StorageMode.LAYERED` 선택 시 자동 생성:
  ```python
  def _create_layered_repository(self, repo_type: str):
      l2_repo = RedisCircuitBreakerStateRepository()  # Redis를 L2로
      return LayeredCircuitBreakerStateRepository(l2_repo=l2_repo)
  ```

**테스트 존재**: `test_drift_reconciliation.py` (12+ 케이스), `test_shadow_log_forensic.py`, `test_l2_timeout.py`

**동작 구조**:
```
execute() → should_allow() → L1 Memory (0.01ms)  ← hot path, Redis I/O 없음
                                  │
                             백그라운드
                                  ↓
                          L2 Redis async sync
                                  │
                             DriftReconciler
                                  ↓
                          L1 ← L2 eventual consistency
```

#### 7.4.3 3가지 저장소 비교 (모두 구현 완료)

| 저장소 | 구현 위치 | 판정 레이턴시 | 분산 동기화 | Redis 장애 내성 | 권장 시나리오 |
|--------|----------|-------------|-----------|---------------|-------------|
| `RedisCircuitBreakerStateRepository` | `adapters/redis/circuit_breaker.py` (745줄) | 2~5ms (HGETALL) | ✅ 즉시 일관 | ⚠️ `ResilientStorageBackend` WAL fallback | Redis 레이턴시 허용 가능한 서비스 |
| `InMemoryCircuitBreakerStateRepository` | `adapters/memory/circuit_breaker.py` (459줄) | 0.01ms | ❌ 프로세스 격리 | ✅ 외부 의존 없음 | 단일 프로세스, 사이드카, 테스트 |
| **`LayeredCircuitBreakerStateRepository`** | `adapters/memory/layered_repository/` (8파일) | **0.01ms (L1)** | **✅ L2 비동기** | **✅ L1 격리 + ShadowLog** | **프로덕션 권장** |

**ProviderRegistry 등록 상태**:
- `factory.py` L614: `register_circuit_breaker_repo("memory", InMemoryCircuitBreakerStateRepository)` ✅
- `factory.py` L633: `register_circuit_breaker_repo("redis", _create_redis_cb_repo)` ✅
- `factory/base.py` L197: `StorageMode.LAYERED` → `LayeredCircuitBreakerStateRepository(l2_repo=...)` ✅

#### 7.4.4 Layered 구조에서 RedisRepository의 역할

`RedisCircuitBreakerStateRepository`는 단독 사용 시 hot path I/O 문제가 있으나,
**L2로 사용 시 다음 이점을 제공**:

1. **분산 상태 수렴**: 여러 워커/노드가 동일 CB 상태에 최종적으로 수렴
2. **`ResilientStorageBackend`의 WAL 보호**: L2(Redis) 자체가 장애 나도 Memory+WAL로 자동 전환
   (`adapters/resilient/backend.py` L427-441: `hset()` → Redis 실패 시 `_hset_degraded()` → WAL-First)
3. **DriftReconciler 연동**: L1↔L2 불일치 자동 감지 및 보정
   (`adapters/memory/layered_repository/base.py` L81: `self._drift_reconciler`)

**최종 권장 구성**:
```python
# 프로덕션 환경
LayeredCircuitBreakerStateRepository(
    l2_repo=RedisCircuitBreakerStateRepository(backend=get_storage_backend()),
    sync_interval_seconds=5,
    adapter_type="redis",
    use_bulkhead=True,  # L2 작업 리소스 격리
)
```

### 7.5 ProtectionMixin 이원화 — 자동 카운팅 vs 429 보호

**질문**: `CircuitBreakerPolicy`와 `ProtectionMixin`의 역할 분담은?

**확정**: **이원화 구조 유지.**
- `CircuitBreakerPolicy` (`record_failure`)  → 일반 Exception 기반 자동 카운팅 경로
- `ProtectionMixin` (`record_rate_limit_response`) → 429 / 트래픽 기반 `force_open` 경로
- `ManualControlMixin` (`force_open` / `force_close`) → 운영자 수동 제어 경로

**코드 근거**:
- `service.py` L468: `if state.manually_controlled: return` — `record_failure()`는 수동 제어 상태 skip
- `protection.py` L80: `self.force_open(...)` — ProtectionMixin은 `force_open()` 호출 (수동 제어 플래그 설정)
- `protection.py` L46-87: `record_rate_limit_response()` — 429 카운트 → cascade 임계값 → `force_open()`

**3개 경로 비교**:
| 경로 | 트리거 | 상태 전환 | manually_controlled |
|------|--------|-----------|---------------------|
| `record_failure()` | 일반 Exception | `update_state("open")` | ❌ False |
| `record_rate_limit_response()` | 429 storm | `force_open()` | ✅ True 가능 |
| `force_open()` | 운영자 수동 | `atomic_force_open()` | ✅ True |

**Policy는 ProtectionMixin의 로직을 침범하지 않는다.** 429 감지는 ProtectionMixin이
별도로 미들웨어/백그라운드에서 감지하여 `force_open()`을 호출하는 기존 흐름을 유지한다.

### 7.6 CircuitBreakerOpenError 정의 위치 — `services/circuit_breaker/exceptions.py`

**질문**: 새 예외 타입을 어디에 정의하는가?

**확정**: `services/circuit_breaker/exceptions.py` (신규 파일)

**코드 근거 — 기존 유사 에러 분석**:
| 클래스 | 위치 | 부모 | 도메인 |
|--------|------|------|--------|
| `CircuitBreakerOpenError` | `shopping/services/payment_recovery_service.py` L40 | `PaymentRecoveryError` | Shopping 전용 |
| `IPCCircuitBreakerOpenError` | `adapters/ipc/exceptions.py` L112 | `IPCError` | IPC 전용 |
| (신규) `CircuitBreakerOpenError` | `services/circuit_breaker/exceptions.py` | `Exception` | selfhealing 범용 |

**Shopping의 `CircuitBreakerOpenError`와의 충돌 분석**:
- Shopping 버전: `from shopping.services.payment_recovery_service import CircuitBreakerOpenError`
- selfhealing 버전: `from selfhealing.services.circuit_breaker.exceptions import CircuitBreakerOpenError`
- **모듈 경로가 다르므로 import 충돌 없음** (Python은 fully qualified name으로 구분)
- Shopping 버전은 `PaymentRecoveryError`를 상속, selfhealing 버전은 `Exception`을 상속
  → `isinstance()` 체크에서도 충돌 없음

**신규 파일 내용**:
```python
# services/circuit_breaker/exceptions.py

class CircuitBreakerOpenError(Exception):
    """Circuit Breaker가 OPEN 상태일 때 Policy에서 발생하는 범용 예외."""

    def __init__(self, service_name: str, message: str | None = None):
        self.service_name = service_name
        super().__init__(message or f"Circuit breaker '{service_name}' is OPEN")
```

### 7.7 should_allow_with_fallback Deprecated 처리

**질문**: `should_allow_with_fallback()` 메서드를 삭제하는가, 유지하는가?

**확정**: **DeprecationWarning 추가 후 유지.** 229번 FallbackPolicy 도입 후 점진 제거.

**코드 근거 — 현재 사용처**:
| 위치 | 호출 방식 |
|------|----------|
| `stale_cache_integration.py` L320 | `service.should_allow_with_fallback(...)` |
| `recovery_strategy.py` L458 | `self._stale_cache.should_allow_with_fallback(...)` |
| `__init__.py` L404 | `should_allow_with_fallback as canary_should_allow_with_fallback` re-export |
| 테스트 6건 | `test_circuit_breaker_enhancements.py` L221-312 |

**Deprecated 처리 계획** (229번 문서 L476-486과 일관):
```python
# service.py — 기존 메서드에 DeprecationWarning 추가
def should_allow_with_fallback(self, ...) -> CircuitBreakerFallbackResult:
    import warnings
    warnings.warn(
        "should_allow_with_fallback() is deprecated. "
        "Use compose(circuit_breaker(), fallback()) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    # 기존 로직 그대로 유지
    ...
```

**제거 조건**:
1. FallbackPolicy (229번) 구현 완료
2. `stale_cache_integration.py`, `recovery_strategy.py` 마이그레이션 완료
3. 최소 1 릴리스 주기 동안 DeprecationWarning 노출

### 7.8 service_name 동적 처리 — 명시적 이름 기본, `__qualname__` fallback

**질문**: 데코레이터 사용 시 `service_name`을 자동으로 함수 이름에서 추론하는가?

**확정**: `CircuitBreakerPolicy.__init__`은 `service_name: str` 필수 파라미터.
향후 데코레이터에서 `func.__qualname__`을 기본값으로 활용.

**코드 근거**:
- 현재 `@circuit_breaker` 데코레이터는 **시스템에 존재하지 않음** (전체 검색 0건)
- 기존 사용 패턴은 모두 명시적 이름 전달:
  - `convenience.py` L39: `should_allow_request(service_name: str)`
  - `shopping/tasks/payment_recovery_tasks.py` L415: `should_allow("external_api")`
- `service_name`은 외부 서비스 식별자 (예: `"toss_payment"`, `"pg_api"`)로 사용
  → 함수명 자동 추론은 의미가 달라질 수 있음

**데코레이터 설계 (향후 Phase 5용)**:
```python
# 향후 convenience 레이어에 추가
def circuit_breaker(
    service_name: str | None = None,
    failure_exceptions: tuple[type[Exception], ...] = (Exception,),
    ignore_exceptions: tuple[type[Exception], ...] = (),
    **kwargs,
):
    def decorator(func):
        name = service_name or func.__qualname__
        policy = CircuitBreakerPolicy(
            service_name=name,
            failure_exceptions=failure_exceptions,
            ignore_exceptions=ignore_exceptions,
            **kwargs,
        )
        @wraps(func)
        def wrapper(*args, **kw):
            return policy.execute(func, *args, **kw)
        return wrapper
    return decorator
```

**네이밍 충돌 검증**: `circuit_breaker` 함수 — 시스템 전체에서 `def circuit_breaker` 검색 0건.
224번 마스터 플랜의 `compose(circuit_breaker("api"), ...)` 구문과 일관.

## 8. 네이밍 충돌 검증 종합

| 새 이름 | 시스템 존재 여부 | 판정 | 비고 |
|---------|----------------|------|------|
| `CircuitBreakerPolicy` | 문서에만 존재 (227 본 문서) | ✅ 안전 | 구현 파일 없음 |
| `CircuitBreakerOpenError` | `shopping/` L40 (도메인 전용) | ✅ 안전 | 모듈 경로 다름, 상속 계통 다름 |
| `failure_exceptions` | 미존재 | ✅ 안전 | |
| `ignore_exceptions` | 미존재 | ✅ 안전 | |
| `_is_failure` | 미존재 (Policy 내부 private) | ✅ 안전 | |
| `exceptions.py` (CB 패키지) | 미존재 | ✅ 안전 | 신규 파일 |
| `circuit_breaker` (데코레이터) | 미존재 | ✅ 안전 | 224 마스터 플랜과 일관 |

## 9. Sliding Window 구현 설계

`failure_rate_threshold`를 프로덕션에서 활성화하기 위해 Sliding Window 구현이 필수이다.
현재 `config.sliding_window_size = 100`은 **Dead Code**이며, 실제 판정 로직에서 참조되지 않는다.

### 9.1 현재 문제 — `sliding_window_size`가 Dead Code

**코드 근거**:

`config.py` L43에 선언:
```python
sliding_window_size: int = 100  # Number of calls to track
failure_rate_threshold: float = 0.0  # 0 = disabled, >0 = percentage
```

그러나 `service.py` L523-561 `_should_open_circuit()`에서 **한 번도 참조하지 않는다**:
```python
def _should_open_circuit(self, state: CircuitBreakerStateData) -> bool:
    total_calls = state.failure_count + state.success_count  # ← 누적 카운터

    if total_calls < self.config.minimum_calls:
        return False

    if self.config.failure_rate_threshold > 0:
        failure_rate = (state.failure_count / total_calls * 100)  # ← 누적 비율
        if failure_rate >= self.config.failure_rate_threshold:
            return True

    if state.failure_count >= self.config.failure_threshold:
        return True

    return False
```

- `self.config.sliding_window_size` 참조: **0건** (service.py 전체 검색)
- `total_calls`는 `state.failure_count + state.success_count` — **누적 값**이므로 window 개념이 없음
- `failure_rate`는 전체 누적 호출 대비 비율 → 시간이 지날수록 과거 데이터에 오염

Repository 레이어에도 window 로직이 없다:
- `adapters/redis/circuit_breaker.py` L498-509: `record_failure()` → `increment_failure()` → 단순 `failure_count + 1`
- `adapters/memory/circuit_breaker.py` L234-257: `record_failure()` → `new_count = entry.failure_count + 1`

### 9.2 왜 `failure_rate_threshold` 프로덕션 활성화가 필수인가

count-based (`failure_threshold=5`)만으로는 트래픽 규모 대응이 불가:

| 시나리오 | 트래픽 | 5건 실패 의미 | count-based 판정 |
|---------|--------|-------------|------------------|
| 고트래픽 | 10,000 RPM | 0.05% 오류 | ❌ OPEN (과민 반응) |
| 저트래픽 | 10 RPM | 50% 오류 | ✅ OPEN (적절한 반응) |

비율 기반은 트래픽 규모와 무관하게 일관된 판정을 보장한다.
그러나 비율 기반이 의미를 가지려면 **"최근 N건"에 대한 비율**이어야 한다.
누적 비율은 시간이 지날수록 희석되어 무의미해진다.

### 9.3 구현 설계 — Count-Based Sliding Window (Ring Buffer)

**방식 선택**:
| 방식 | 구현 복잡도 | 메모리 | I/O 부담 | 정밀도 |
|------|-----------|--------|---------|--------|
| **Count-Based Ring Buffer** | ✅ 낮음 | N bytes | ✅ 없음 (L1 메모리) | O(1) |
| Time-Based Bucket | ⚠️ 중간 | 가변 | ⚠️ 타이머 필요 | 시간 정밀도 |
| Redis Sorted Set | ❌ 높음 | Redis 측 | ❌ ZADD/ZRANGEBYSCORE per call | 정밀하나 고비용 |

**업계 표준과의 비교**:
| 라이브러리 | 기본 window | 타입 |
|-----------|------------|------|
| **resilience4j** (Java) | **100 (count)** / 60s (time) | 선택 가능 |
| Netflix Hystrix | 10초 × 10 bucket | time-based |
| Polly (.NET) | 수동 설정 | — |
| Envoy proxy | 5초 / 10초 | time-based |

`config.sliding_window_size = 100` 기본값은 **resilience4j와 동일**하며 업계 표준.
RuntimeConfigManager를 통해 서비스별 조정 가능 (`config.py` L87: `runtime_config.get("sliding_window_size", 100)`).

**구현 위치: `InMemoryCircuitBreakerStateRepository`**

`adapters/memory/circuit_breaker.py`의 `record_failure()` / `record_success()`에
ring buffer를 추가한다. L1 메모리 레이어에서 window를 관리하므로:
1. Redis I/O 없음 (hot path 보호)
2. `LayeredRepository`의 L2 동기화에는 **집계된 카운트만 전달** (window 전체를 동기화할 필요 없음)
3. `repository_operations.py` L173-177의 기존 `L1 → L2 async sync` 흐름에 영향 없음

**의사코드**:
```python
class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    def __init__(self, sliding_window_size: int = 100):
        self._storage: dict[str, CircuitBreakerStateData] = {}
        self._lock = threading.RLock()
        # Sliding Window: 서비스별 ring buffer
        self._sliding_window_size = sliding_window_size
        self._call_windows: dict[str, deque[bool]] = {}  # True=success, False=failure

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        with self._lock:
            window = self._get_or_create_window(service_name)
            if len(window) >= self._sliding_window_size:
                window.popleft()  # 가장 오래된 항목 제거
            window.append(False)  # failure

            # window 기반 카운트 계산
            failure_count = window.count(False)
            success_count = window.count(True)
            # ... CircuitBreakerStateData 갱신

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        with self._lock:
            window = self._get_or_create_window(service_name)
            if len(window) >= self._sliding_window_size:
                window.popleft()
            window.append(True)  # success
            # ... CircuitBreakerStateData 갱신

    def _get_or_create_window(self, service_name: str) -> deque[bool]:
        if service_name not in self._call_windows:
            self._call_windows[service_name] = deque(maxlen=self._sliding_window_size)
        return self._call_windows[service_name]
```

### 9.4 `_should_open_circuit()` 수정

현재 코드(`service.py` L536)에서 `total_calls = state.failure_count + state.success_count`는
ring buffer 도입 후 **자동으로 window 내 카운트**가 된다.

InMemoryRepo(ring buffer)가 window-based count를 제공하므로 기본 동작은 동일하지만,
non-windowed repo(Redis 등) 사용 시 누적 카운트 오염을 방지하기 위해
`_should_open_circuit()`에서 `config.sliding_window_size`를 **직접 참조하여 cap**한다:

```python
def _should_open_circuit(self, state: CircuitBreakerStateData) -> bool:
    total_calls = state.failure_count + state.success_count

    # Sliding Window: total_calls를 window 크기로 제한 (§9)
    window_size = self.config.sliding_window_size
    if window_size > 0 and total_calls > window_size:
        total_calls = window_size  # non-windowed repo fallback 방어

    if total_calls < self.config.minimum_calls:
        return False
    # ... rate/count threshold 판정
```

### 9.5 L2 동기화 시 Window 처리

`LayeredRepository`의 L2 동기화는 `state.failure_count` / `state.success_count`만 전달한다.
Window 자체(ring buffer)는 L1 로컬에서만 유지:

```
L1 (InMemory)           L2 (Redis)
┌──────────────────┐   ┌──────────────────┐
│ ring buffer:     │   │ failure_count: 3  │ ← 집계된 값만
│ [F,S,S,F,S,F,S]  │──→│ success_count: 4  │
│ failure_count: 3 │   │ state: closed     │
│ success_count: 4 │   └──────────────────┘
└──────────────────┘
```

- 각 노드가 독립적으로 ring buffer를 운영 → 노드별 판정은 즉시
- L2에는 집계 카운트만 비동기 동기화 → 노드 간 최종 수렴
- DriftReconciler가 L1↔L2 불일치 자동 감지 → 극단적 불일치 시 보정

### 9.6 `sliding_window_size` 서비스별 오버라이드

100이 기본값이나, 서비스별 조정이 가능해야 한다:

```python
# RuntimeConfigManager 경유 — 이미 지원됨 (config.py L87)
sliding_window_size=runtime_config.get("sliding_window_size", 100)

# 서비스별 추천값
# 고트래픽 (toss_payment): sliding_window_size=1000, failure_rate_threshold=50.0
# 중트래픽 (internal_api):  sliding_window_size=100,  failure_rate_threshold=60.0
# 저트래픽 (admin_api):     sliding_window_size=20,   failure_rate_threshold=80.0
```

### 9.7 구현 순서

| 단계 | 작업 | 영향 범위 |
|------|------|----------|
| 1 | `InMemoryCircuitBreakerStateRepository`에 ring buffer 추가 | `adapters/memory/circuit_breaker.py` |
| 2 | `__init__`에 `sliding_window_size` 파라미터 추가 | 동일 파일 |
| 3 | `record_failure()` / `record_success()`가 window 기반 count 반환 | 동일 파일 |
| 4 | `reset()` / `clear()` 시 window도 초기화 | 동일 파일 |
| 5 | `CircuitBreakerService` 또는 `CircuitBreakerPolicy`가 config.sliding_window_size를 Repository에 전달 | `service.py` 또는 Policy 생성 시 |
| 6 | `LayeredRepositoryBase.__init__`에서 L1 생성 시 window_size 전달 | `layered_repository/base.py` L73 |
| 7 | 기존 테스트 + window 관련 신규 테스트 | `tests/` |

### 9.8 `failure_rate_threshold` 프로덕션 활성화 가이드

#### 전제 조건

`failure_rate_threshold`를 활성화하려면 다음이 **모두 충족**되어야 한다:

1. **Sliding Window Ring Buffer 구현 완료** — `InMemoryCircuitBreakerStateRepository`에 `deque(maxlen=N)` 기반 ring buffer 적용 (§9.3)
2. **LayeredRepository 기본 경로 확보** — `CircuitBreakerPolicy`가 `ProviderRegistry.get_circuit_breaker_repo(name="layered")`를 사용 (§7.4)
3. **`_should_open_circuit()`의 `sliding_window_size` 직접 참조** — non-windowed repo fallback 시 누적 카운트 오염 방지 (§9.4)

#### 활성화 단계

**Step 1 — Django settings 변경** (`settings.py` 또는 환경 변수):

```python
# settings.py
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "failure_rate_threshold": 50.0,   # 50% 이상 실패 시 OPEN
        "sliding_window_size": 100,        # 최근 100건 기준
        "minimum_calls": 10,               # 최소 10건 이후 판정
        "failure_threshold": 5,            # count-based도 동시 유지 (안전망)
    }
}
```

**Step 2 — RuntimeConfigManager 경유 서비스별 오버라이드** (무중단 변경):

```python
from selfhealing.services.circuit_breaker.config import CircuitBreakerConfig

# 서비스별 config 생성
config = CircuitBreakerConfig.from_runtime_config(
    service_name="payment_api",
    # RuntimeConfigManager가 서비스별 설정을 반환
)
# → config.failure_rate_threshold = 50.0  (payment_api 전용)
# → config.sliding_window_size = 1000     (고트래픽 설정)
```

**Step 3 — 모니터링 확인**:

| 지표 | Prometheus 쿼리 | 기대값 |
|------|-----------------|--------|
| CB OPEN 빈도 | `rate(circuit_breaker_state_changes_total{to="open"}[5m])` | 급등 없음 |
| 거부율 | `rate(circuit_breaker_rejections_total[5m])` | 이전 대비 ±10% 이내 |
| failure_rate | `circuit_breaker_failure_rate{service="X"}` | threshold 근처 수렴 확인 |

**Step 4 — Canary 배포** (권장):

```yaml
# k8s/ 환경에서 canary pod에만 Rate-based threshold 활성화
env:
  - name: SELF_HEALING__CIRCUIT_BREAKER__FAILURE_RATE_THRESHOLD
    value: "50.0"
```

일반 pod는 `failure_rate_threshold=0.0`(비활성) 유지.
Canary pod의 CB 판정을 2-4시간 관찰 후 전체 롤아웃.

#### 서비스별 추천 프로파일

| 서비스 유형 | `sliding_window_size` | `failure_rate_threshold` | `minimum_calls` | 근거 |
|------------|----------------------|-------------------------|-----------------|------|
| 고트래픽 (결제 API) | 1000 | 50.0% | 50 | 0.05% 오류에 과민 반응 방지 |
| 중트래픽 (내부 API) | 100 | 60.0% | 10 | resilience4j 기본값 준용 |
| 저트래픽 (관리자 API) | 20 | 80.0% | 5 | 소량 호출에서도 작동 보장 |

#### Rollback 절차

`failure_rate_threshold`를 `0.0`으로 되돌리면 즉시 rate-based 판정이 비활성화된다.
count-based (`failure_threshold`) 판정은 항상 활성 상태이므로 안전망이 유지된다.

## 10. 단위 테스트 (155건)

### 10.1 테스트 파일 구조

```
tests/unit/circuit_breaker/
├── test_circuit_breaker_policy.py     # CircuitBreakerPolicy 핵심 동작 (57건)
├── test_sliding_window.py             # InMemoryRepo Sliding Window (22건)
├── test_cb_policy_integration.py      # DeprecationWarning, Layered, export (10건)
├── test_circuit_breaker_hooks.py      # PolicyHook 구현체 + build_default_hooks (32건)
└── test_cb_policy_hooks_window.py     # Protocol 상속, hooks 파라미터, _invoke_hooks, _create_default_service, window cap (34건)
```

### 10.2 테스트 분류

UNIT_TEST_GUIDELINES.md 기준 계약/동작 검증 분리:

| 파일 | 클래스 | 유형 | 건수 | 대상 |
|------|--------|------|------|------|
| `test_circuit_breaker_policy.py` | `TestCircuitBreakerPolicyContract` | 계약 | 10 | name, outcome, executed_policies, 기본값 |
| | `TestCircuitBreakerPolicyDisabledBehavior` | 동작 | 5 | CB disabled → 직접 실행 |
| | `TestCircuitBreakerPolicyRejectedBehavior` | 동작 | 7 | CB OPEN → REJECTED |
| | `TestCircuitBreakerPolicySuccessBehavior` | 동작 | 8 | 성공 경로 → record_success |
| | `TestCircuitBreakerPolicyFailureBehavior` | 동작 | 5 | 실패 경로 → record_failure + raise |
| | `TestCircuitBreakerPolicyExceptionFilterBehavior` | 동작 | 7 | _is_failure(), ignore/failure 필터 |
| | `TestCircuitBreakerPolicyContextBehavior` | 동작 | 2 | PolicyContext 전달 |
| | `TestCircuitBreakerOpenErrorContract` | 계약 | 5 | 예외 속성, 메시지, 상속 |
| | `TestCircuitBreakerDecoratorBehavior` | 동작 | 8 | @circuit_breaker() 데코레이터 |
| `test_sliding_window.py` | `TestSlidingWindowContract` | 계약 | 4 | 기본값 100, deque maxlen |
| | `TestSlidingWindowRecordFailureBehavior` | 동작 | 7 | record_failure() ring buffer |
| | `TestSlidingWindowRecordSuccessBehavior` | 동작 | 4 | record_success() ring buffer |
| | `TestSlidingWindowResetBehavior` | 동작 | 5 | reset/clear 시 window 초기화 |
| | `TestSlidingWindowIsolationBehavior` | 동작 | 2 | 서비스별 window 격리 |
| `test_cb_policy_integration.py` | `TestShouldAllowWithFallbackDeprecationContract` | 계약 | 3 | DeprecationWarning 발생/메시지 |
| | `TestLayeredRepositorySlidingWindowBehavior` | 동작 | 3 | sliding_window_size L1 전달 |
| | `TestCircuitBreakerModuleExportsContract` | 계약 | 4 | __init__.py export 검증 |
| `test_circuit_breaker_hooks.py` | `TestAuditPolicyHookContract` | 계약 | 5 | PolicyHook 인터페이스 메서드 존재 |
| | `TestAuditPolicyHookBehavior` | 동작 | 8 | on_reject → log_cb_state_change_audit, Fail-Open |
| | `TestEventBusPolicyHookContract` | 계약 | 5 | PolicyHook 인터페이스 메서드 존재 |
| | `TestEventBusPolicyHookBehavior` | 동작 | 7 | on_reject → EventBus.emit, Fail-Open |
| | `TestBuildDefaultHooksContract` | 계약 | 4 | 반환 리스트, 2개, 타입 순서 |
| | `TestBuildDefaultHooksBehavior` | 동작 | 3 | 개별 실패 허용, 전부 실패 시 빈 리스트 |
| `test_cb_policy_hooks_window.py` | `TestCircuitBreakerPolicyProtocolContract` | 계약 | 4 | ResiliencePolicy[T] isinstance, MRO |
| | `TestCircuitBreakerPolicyHooksParamContract` | 계약 | 3 | hooks 기본값, 커스텀, 빈 리스트 |
| | `TestInvokeHooksBehavior` | 동작 | 5 | _invoke_hooks 전달 인자, Fail-Open |
| | `TestPolicyExecuteHooksIntegrationBehavior` | 동작 | 9 | execute() 내 on_execute/on_reject/on_success/on_failure 타이밍 |
| | `TestCreateDefaultServiceBehavior` | 동작 | 5 | ProviderRegistry "layered" → fallback |
| | `TestShouldOpenCircuitWindowCapBehavior` | 동작 | 8 | sliding_window_size cap, count-based threshold |

### 10.3 코드 근거 매핑

| 테스트 축 | 코드 근거 |
|-----------|----------|
| `name == "circuit_breaker"` | `policy.py` → `CircuitBreakerPolicy.__init__` super().__init__(name=) |
| CB disabled → SUCCESS | `policy.py` → `execute()`: `if not self._cb_service.is_enabled` |
| REJECTED + CircuitBreakerOpenError | `policy.py` → `execute()`: `if not self._cb_service.should_allow()` |
| record_success 호출 | `policy.py` → `execute()`: `self._cb_service.record_success()` |
| record_failure + error_context | `policy.py` → `execute()`: `error_context={"error": str(e), "type": type(e).__name__}` |
| 예외 재전파 (raise) | `policy.py` → `execute()`: `raise` |
| ignore_exceptions 우선 | `policy.py` → `_is_failure()`: `if isinstance(error, self._ignore_exceptions): return False` |
| 데코레이터 qualname | `policy.py` → `circuit_breaker()`: `name = service_name or func.__qualname__` |
| Sliding Window ring buffer | `circuit_breaker.py` → `InMemoryCircuitBreakerStateRepository.__init__`: `deque(maxlen=sliding_window_size)` |
| Window overflow eviction | `circuit_breaker.py` → `record_failure()`/`record_success()`: `window.append()` + deque maxlen |
| DeprecationWarning | `service.py` → `should_allow_with_fallback()`: `warnings.warn(...)` |
| LayeredRepo window_size 전달 | `base.py` → `__init__`: `InMemoryCircuitBreakerStateRepository(sliding_window_size=)` |
| __init__.py export | `__init__.py`: `from .exceptions import ...`, `from .policy import ...` |
| ResiliencePolicy[T] 상속 | `policy.py` → `class CircuitBreakerPolicy(ResiliencePolicy[T])` |
| hooks 기본값 build_default_hooks | `policy.py` → `__init__`: `hooks: list[PolicyHook] = None` → `build_default_hooks()` |
| _invoke_hooks Fail-Open | `policy.py` → `_invoke_hooks()`: try/except per hook, logger.warning |
| on_execute/on_reject/on_success/on_failure 타이밍 | `policy.py` → `execute()`: 각 분기에서 `_invoke_hooks()` 호출 |
| AuditPolicyHook.on_reject → audit | `hooks.py` → `AuditPolicyHook.on_reject()`: lazy import `log_cb_state_change_audit` |
| EventBusPolicyHook.on_reject → EventBus | `hooks.py` → `EventBusPolicyHook.on_reject()`: `EventBus.emit(CIRCUIT_BREAKER_OPENED)` |
| build_default_hooks 개별 실패 허용 | `hooks.py` → `build_default_hooks()`: try/except per hook, 실패 시 건너뜀 |
| _create_default_service layered fallback | `policy.py` → `_create_default_service()`: `ProviderRegistry.get("layered")` → except → `CircuitBreakerService(config)` |
| _should_open_circuit window cap | `service.py` → `_should_open_circuit()`: `if window_size > 0 and total_calls > window_size: total_calls = window_size` |

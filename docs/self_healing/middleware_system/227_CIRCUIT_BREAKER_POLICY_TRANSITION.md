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
    ):
        self._service_name = service_name
        self._cb_service = cb_service or CircuitBreakerService(config=config)

    @property
    def name(self) -> str:
        return "circuit_breaker"

    def execute(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> PolicyResult[T]:
        """
        CB 상태 기반 함수 실행.

        1. should_allow() → False면 REJECTED 반환
        2. True면 func 실행
        3. 성공 → record_success()
        4. 실패 → record_failure() → 임계값 초과 시 OPEN 전환
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
                error=CircuitBreakerOpenError(
                    f"Circuit breaker '{self._service_name}' is OPEN"
                ),
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
            self._cb_service.record_failure(self._service_name, e)
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

## 4. record_success / record_failure 메서드

현재 `CircuitBreakerService`에 이 메서드가 **존재하지 않는다** (토글 기반이므로).
CircuitBreakerPolicy에서 자동 실패 카운팅을 지원하려면 추가가 필요하다.

현재 구현은 `manual_control.py`의 `force_open()`/`force_close()`만 제공:

```python
# manual_control.py — 운영자 수동 제어만 존재
def force_open(self, service_name, reason, controlled_by): ...
def force_close(self, service_name, reason, controlled_by, trigger_replay): ...
```

### 선택지

| 방안 | 설명 | 장단점 |
|------|------|--------|
| A. 토글 유지 | CB는 운영자 수동 제어만. 자동 카운팅 안 함 | 현재 동작 유지. Policy에서는 should_allow() 체크만 |
| B. 자동 카운팅 추가 | record_failure() 추가, 임계값 초과 시 자동 OPEN | resilience4j 스타일. 완전한 Policy 구현 |
| C. 하이브리드 | 수동 제어 유지 + ProtectionMixin의 기존 자동 보호 활용 | `protection.py`에 이미 rate_limit_cascade, self_ddos 보호 존재 |

**권장**: **방안 C (하이브리드)**. 이유:
- `protection.py`에 이미 자동 보호 로직이 존재 (rate limit cascade → 자동 CB open)
- 운영자 수동 제어(force_open/close)는 엔터프라이즈 요구사항
- CircuitBreakerPolicy는 should_allow() 기반으로 구현하되,
  향후 자동 카운팅이 필요하면 ProtectionMixin을 확장

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

- [ ] `CircuitBreakerPolicy` 클래스 생성
- [ ] `CircuitBreakerOpenError` 예외 타입 정의
- [ ] should_allow() 기반 execute() 구현
- [ ] should_allow_with_fallback() 내장 Fallback을 FallbackPolicy로 분리 계획
- [ ] Audit lazy import → PolicyHook으로 분리 계획
- [ ] EventBus lazy import → PolicyHook으로 분리 계획
- [ ] 기존 convenience.py 함수들의 하위 호환성 유지
- [ ] 기존 테스트 통과 확인

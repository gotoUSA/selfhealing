# Retry & Exponential Backoff

> 이 문서는 Self-Healing 시스템의 재시도 전략과 지수 백오프 알고리즘을 상세히 설명합니다.

## 📋 목차

1. [개념 및 목적](#1-개념-및-목적)
2. [지수 백오프 알고리즘](#2-지수-백오프-알고리즘)
3. [RetryHandler](#3-retryhandler)
4. [BackoffCalculator](#4-backoffcalculator)
5. [설정](#5-설정)
6. [재시도 가능 여부 판단](#6-재시도-가능-여부-판단)
7. [사용 예시](#7-사용-예시)

---

## 1. 개념 및 목적

### 1.1 왜 재시도가 필요한가?

분산 시스템에서는 일시적인 오류가 자주 발생합니다:

- **네트워크 타임아웃**: 일시적인 네트워크 지연
- **서버 과부하**: 일시적인 503 응답
- **Rate Limiting**: 일시적인 429 응답
- **연결 실패**: 일시적인 연결 불가

이런 오류는 **잠시 기다린 후 재시도하면 성공**할 가능성이 높습니다.

### 1.2 왜 지수 백오프인가?

단순 재시도의 문제점:

```
❌ 고정 간격 (Fixed Interval)
   1초 → 1초 → 1초 → 1초 → ...
   
   문제: 서버가 복구될 시간을 주지 않음
```

지수 백오프의 장점:

```
✅ 지수 백오프 (Exponential Backoff)
   4초 → 16초 → 64초 → 180초 (cap)
   
   장점: 서버에 복구 시간을 줌, 부하 분산
```

### 1.3 Jitter가 필요한 이유

여러 클라이언트가 동시에 실패하면, 동일한 백오프 간격으로 동시에 재시도하여 **Thundering Herd** 문제가 발생합니다.

```
❌ Jitter 없음
   Client A: 4초 → 16초 → 64초
   Client B: 4초 → 16초 → 64초
   Client C: 4초 → 16초 → 64초
   
   → 모든 클라이언트가 동시에 재시도!
   
✅ Jitter 적용 (±25%)
   Client A: 3초 → 14초 → 52초
   Client B: 5초 → 18초 → 70초
   Client C: 4초 → 12초 → 80초
   
   → 재시도가 분산됨
```

---

## 2. 지수 백오프 알고리즘

### 2.1 기본 공식

```
delay = min(base^attempt, max_delay) × (1 ± jitter_percent/100)
```

### 2.2 기본 설정값

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `base` | 4 | 지수 베이스 (4^n 초) |
| `max_delay` | 180 | 최대 대기 시간 (3분) |
| `jitter_percent` | 25 | ±25% 랜덤 변동 |
| `min_delay` | 1 | 최소 대기 시간 (1초) |

### 2.3 재시도 간격 예시

```
┌──────────────────────────────────────────────────────────────┐
│                    재시도 간격 (Jitter 제외)                  │
├──────────┬────────────────┬──────────────────────────────────┤
│ Attempt  │ 계산           │ 결과                             │
├──────────┼────────────────┼──────────────────────────────────┤
│ 1        │ 4^1 = 4        │ 4초                              │
│ 2        │ 4^2 = 16       │ 16초                             │
│ 3        │ 4^3 = 64       │ 64초                             │
│ 4        │ 4^4 = 256 → 180│ 180초 (max_delay로 캡)           │
│ 5        │ 4^5 = 1024→ 180│ 180초                            │
└──────────┴────────────────┴──────────────────────────────────┘
```

### 2.4 Jitter 적용 후 예시

```
┌──────────────────────────────────────────────────────────────┐
│                    재시도 간격 (Jitter ±25%)                  │
├──────────┬────────────────┬──────────────────────────────────┤
│ Attempt  │ Base           │ Jitter 범위                      │
├──────────┼────────────────┼──────────────────────────────────┤
│ 1        │ 4초            │ 3초 ~ 5초                        │
│ 2        │ 16초           │ 12초 ~ 20초                      │
│ 3        │ 64초           │ 48초 ~ 80초                      │
│ 4+       │ 180초          │ 135초 ~ 225초                    │
└──────────┴────────────────┴──────────────────────────────────┘
```

---

## 3. RetryHandler

### 3.1 클래스 개요

```python
from selfhealing.services import RetryHandler
from selfhealing.core.config import RetryConfig

class RetryHandler:
    """
    재시도 로직 핸들러.
    
    Features:
    - 지수 백오프 with Jitter
    - 재시도 가능/불가능 예외 분류
    - 최대 재시도 횟수 제한
    - DLQ 자동 라우팅
    - 멱등성 체크
    """
```

### 3.2 RetryConfig

```python
@dataclass
class RetryConfig:
    max_attempts: int = 3                    # 최대 시도 횟수
    backoff_base: int = 4                    # 백오프 베이스
    backoff_max: int = 180                   # 최대 백오프 (초)
    jitter_percent: int = 25                 # Jitter 비율
    retryable_exceptions: tuple = (Exception,)  # 재시도 가능 예외
    non_retryable_exceptions: tuple = ()     # 재시도 불가 예외
    enable_dlq: bool = True                  # DLQ 라우팅 활성화
    domain: str = "default"                  # 도메인 (설정 오버라이드용)
    
    @classmethod
    def from_settings(cls, domain: str = "default") -> "RetryConfig":
        """Django settings에서 설정 로드"""
        ...
```

### 3.3 RetryResult

```python
@dataclass
class RetryResult:
    success: bool           # 최종 성공 여부
    action: RetryAction     # 취한 액션 (SUCCESS, RETRY, DLQ, ABORT)
    attempt: int            # 시도 횟수
    value: Any = None       # 성공 시 반환값
    error: Exception = None # 마지막 에러
    dlq_id: int = None      # DLQ 저장 시 ID
    next_delay: int = None  # 다음 재시도까지 대기 시간
    
    @property
    def should_retry(self) -> bool:
        """재시도 필요 여부"""
        return self.action == RetryAction.RETRY
```

### 3.4 핵심 메서드

#### `execute(func, *args, **kwargs) -> RetryResult`

```python
handler = RetryHandler(domain="payment")

# 동기 함수 실행 (자동 재시도)
result = handler.execute(
    call_toss_api,
    payment_key="pk_test_xxx",
    amount=50000,
)

if result.success:
    return result.value
elif result.action == RetryAction.DLQ:
    # DLQ에 저장됨
    log.warning(f"DLQ 저장됨: ID={result.dlq_id}")
```

#### `should_retry(exception, attempt) -> bool`

```python
# 재시도 가능 여부 판단
if handler.should_retry(exception, current_attempt):
    delay = handler.get_next_delay(current_attempt)
    time.sleep(delay)
    # 재시도...
else:
    # DLQ에 저장 또는 예외 발생
```

#### `get_next_delay(attempt) -> int`

```python
# 다음 재시도까지 대기 시간 계산
delay = handler.get_next_delay(attempt=2)
# Returns: 약 12-20초 (16초 ± 25% jitter)
```

---

## 4. BackoffCalculator

### 4.1 클래스 개요

```python
from selfhealing.core.backoff import BackoffCalculator, BackoffConfig

class BackoffCalculator:
    """
    지수 백오프 계산기.
    
    공식: delay = min(base^attempt, max_delay) × (1 ± jitter)
    """
```

### 4.2 BackoffConfig

```python
@dataclass
class BackoffConfig:
    base: int = 4           # 지수 베이스
    max_delay: int = 180    # 최대 대기 (초)
    jitter_percent: int = 25  # Jitter 비율
    min_delay: int = 1      # 최소 대기 (초)
    
    @classmethod
    def from_settings(cls, domain: str = None) -> "BackoffConfig":
        """설정에서 로드, 도메인별 오버라이드 지원"""
        ...
```

### 4.3 핵심 메서드

#### `calculate(attempt, with_jitter=True) -> int`

```python
calculator = BackoffCalculator()

# 각 시도별 대기 시간 계산
delay_1 = calculator.calculate(1)  # ~4초 (3-5초)
delay_2 = calculator.calculate(2)  # ~16초 (12-20초)
delay_3 = calculator.calculate(3)  # ~64초 (48-80초)

# Jitter 없이 정확한 값
exact_delay = calculator.calculate(2, with_jitter=False)  # 정확히 16초
```

#### `get_delays_sequence(max_attempts, with_jitter=False) -> List[int]`

```python
# 전체 재시도 시퀀스 미리보기
delays = calculator.get_delays_sequence(5, with_jitter=False)
# Returns: [4, 16, 64, 180, 180]
```

### 4.4 편의 함수

```python
from selfhealing.core.backoff import calculate_backoff

# 간단한 백오프 계산
delay = calculate_backoff(
    attempt=2,
    base=4,
    max_delay=180,
    jitter_percent=25,
)
```

---

## 5. 설정

### 5.1 전역 설정

```python
# settings.py
SELF_HEALING = {
    "RETRY": {
        "MAX_ATTEMPTS": 3,
        "BACKOFF_BASE": 4,
        "BACKOFF_MAX": 180,
        "JITTER_PERCENT": 25,
        "MIN_DELAY": 1,
    },
}
```

### 5.2 도메인별 오버라이드

```python
# settings.py
SELF_HEALING = {
    "RETRY": {
        "MAX_ATTEMPTS": 3,
        "BACKOFF_BASE": 4,
        "BACKOFF_MAX": 180,
    },
    "DOMAIN_CONFIG": {
        "payment": {
            "max_attempts": 5,      # 결제는 더 많이 재시도
            "backoff_base": 2,      # 더 짧은 간격으로 시작
            "backoff_max": 300,     # 더 긴 최대 대기
        },
        "webhook": {
            "max_attempts": 10,     # 웹훅은 많이 재시도
            "backoff_base": 10,     # 더 긴 간격
        },
        "notification": {
            "max_attempts": 3,
            "backoff_max": 600,     # 알림은 오래 기다려도 됨
        },
    },
}
```

### 5.3 도메인별 설정 사용

```python
# Payment 도메인 설정 적용
handler = RetryHandler(domain="payment")
config = handler.config

print(config.max_attempts)   # 5
print(config.backoff_base)   # 2
print(config.backoff_max)    # 300

# Webhook 도메인
webhook_handler = RetryHandler(domain="webhook")
# max_attempts=10, backoff_base=10
```

---

## 6. 재시도 가능 여부 판단

### 6.1 재시도 가능한 오류

```python
# 일시적인 오류 (Transient Errors)
RETRYABLE_EXCEPTIONS = (
    TimeoutError,           # 타임아웃
    ConnectionError,        # 연결 실패
    HTTPError,              # 5xx 서버 오류
    RateLimitError,         # 429 Rate Limit
    ServiceUnavailable,     # 503 Service Unavailable
)

# HTTP 상태 코드별
RETRYABLE_STATUS_CODES = [
    408,  # Request Timeout
    429,  # Too Many Requests
    500,  # Internal Server Error
    502,  # Bad Gateway
    503,  # Service Unavailable
    504,  # Gateway Timeout
]
```

### 6.2 재시도 불가능한 오류

```python
# 영구적인 오류 (Permanent Errors)
NON_RETRYABLE_EXCEPTIONS = (
    ValidationError,        # 입력값 오류
    AuthenticationError,    # 인증 실패
    PermissionDenied,       # 권한 없음
    NotFoundError,          # 리소스 없음
    DuplicateError,         # 중복
    AmountMismatchError,    # 금액 불일치
    SignatureInvalidError,  # 서명 위변조
)

# HTTP 상태 코드별
NON_RETRYABLE_STATUS_CODES = [
    400,  # Bad Request
    401,  # Unauthorized
    403,  # Forbidden
    404,  # Not Found
    409,  # Conflict
    422,  # Unprocessable Entity
]
```

### 6.3 판단 로직

```python
class RetryHandler:
    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """재시도 가능 여부 판단"""
        
        # 1. 최대 시도 횟수 체크
        if attempt >= self.config.max_attempts:
            return False
        
        # 2. Non-retryable 예외 체크 (우선)
        if isinstance(exception, self.config.non_retryable_exceptions):
            return False
        
        # 3. Retryable 예외 체크
        if isinstance(exception, self.config.retryable_exceptions):
            return True
        
        # 4. 기본값: 재시도 안 함
        return False
```

---

## 7. 사용 예시

### 7.1 기본 사용

```python
from selfhealing.services import RetryHandler

def call_external_api(order_id: int) -> dict:
    """외부 API 호출 (실패할 수 있음)"""
    response = requests.post(f"{API_URL}/orders/{order_id}")
    response.raise_for_status()
    return response.json()

# RetryHandler로 래핑
handler = RetryHandler(domain="payment")
result = handler.execute(call_external_api, order_id=12345)

if result.success:
    print(f"성공: {result.value}")
else:
    print(f"실패: {result.error}, 시도 횟수: {result.attempt}")
```

### 7.2 Celery 태스크에서 사용

```python
from celery import shared_task
from selfhealing.services import RetryHandler

@shared_task(
    bind=True,
    max_retries=0,  # Celery 재시도 비활성화 (RetryHandler가 처리)
)
def process_payment_task(self, order_id: int, payment_data: dict):
    """결제 처리 태스크"""
    
    handler = RetryHandler(domain="payment")
    
    def _call_toss():
        return toss_client.confirm_payment(payment_data)
    
    result = handler.execute(_call_toss)
    
    if result.success:
        return {"status": "success", "data": result.value}
    elif result.action == RetryAction.DLQ:
        return {"status": "dlq", "dlq_id": result.dlq_id}
    else:
        raise result.error
```

### 7.3 컨텍스트 매니저 패턴

```python
from selfhealing.services import RetryHandler

handler = RetryHandler(domain="payment")

for attempt in range(1, handler.config.max_attempts + 1):
    try:
        result = call_external_api(order_id)
        handler.record_success()  # 성공 기록
        return result
        
    except Exception as e:
        if handler.should_retry(e, attempt):
            delay = handler.get_next_delay(attempt)
            log.warning(f"Attempt {attempt} failed, retrying in {delay}s: {e}")
            time.sleep(delay)
            continue
        else:
            # 재시도 불가 - DLQ로 라우팅
            handler.route_to_dlq(
                error=e,
                context={"order_id": order_id},
            )
            raise
```

### 7.4 비동기 재시도 (Celery)

```python
from celery import shared_task

@shared_task(
    bind=True,
    autoretry_for=(TimeoutError, ConnectionError),
    retry_backoff=True,       # 지수 백오프 활성화
    retry_backoff_max=300,    # 최대 5분
    retry_jitter=True,        # Jitter 활성화
    max_retries=5,
)
def async_api_call(self, url: str, data: dict):
    """Celery 내장 재시도 사용"""
    try:
        response = requests.post(url, json=data, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if e.response.status_code in [429, 500, 502, 503, 504]:
            raise  # autoretry_for에 의해 재시도
        else:
            # 재시도 불가 오류 - DLQ 저장
            store_to_dlq(...)
            raise
```

### 7.5 커스텀 백오프 전략

```python
from selfhealing.core.backoff import BackoffCalculator, BackoffConfig

# 더 공격적인 백오프 (짧은 초기 간격)
aggressive_config = BackoffConfig(
    base=2,          # 2^n: 2, 4, 8, 16, 32...
    max_delay=60,
    jitter_percent=10,
)
aggressive_calc = BackoffCalculator(aggressive_config)

# 더 보수적인 백오프 (긴 초기 간격)
conservative_config = BackoffConfig(
    base=10,         # 10^n: 10, 100, 1000...
    max_delay=600,   # 최대 10분
    jitter_percent=50,
)
conservative_calc = BackoffCalculator(conservative_config)

# 선형 백오프 (base=1이면 고정 간격)
linear_config = BackoffConfig(
    base=1,
    max_delay=30,
    min_delay=10,    # 항상 10초
)
```

---

## 버전 정보

- **현재 버전**: 1.0.0
- **마지막 업데이트**: 2025-12-20

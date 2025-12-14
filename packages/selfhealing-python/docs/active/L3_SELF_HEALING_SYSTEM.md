# L3 Self-Healing System (자동 복구 시스템)

> **요약**: L3는 새로운 시스템을 설치하는 개념이 아니라, 장애 발생 후 자동 복구를 위한 **Retry/Backoff/SLA/Dead-letter 정책의 구현**입니다. 현재 **Celery + Django ORM** 조합만으로도 Self-Healing 처리 흐름을 구현할 수 있습니다.

---

## 📋 목차

1. [개요](#개요)
2. [아키텍처](#아키텍처)
3. [핵심 기능](#핵심-기능)
   - [Retry with Exponential Backoff](#1-retry-with-exponential-backoff)
   - [Dead Letter Queue (DLQ)](#2-dead-letter-queue-dlq)
   - [SLA Timeout Abort](#3-sla-timeout-abort)
   - [Circuit Breaker](#4-circuit-breaker-toggle-기반)
   - [Rate Limit Cascade Detection](#5-rate-limit-cascade-detection)
   - [Self-DDoS Protection](#6-self-ddos-protection)
4. [설정](#설정)
5. [모델](#모델)
6. [서비스](#서비스)
7. [Celery 태스크](#celery-태스크)
8. [사용 예시](#사용-예시)
9. [테스트](#테스트)
10. [확장성](#확장성)
11. [제한 사항](#제한-사항)

---

## 개요

### 왜 L3가 필요한가?

```
결제 실패 → 재시도 없음 → 매출 손실 + 고객 이탈
결제 실패 → 자동 복구 → 매출 보전 + 신뢰 유지
```

금융/결제 시스템에서 100%에 가까운 신뢰성은 필수입니다. L3 Self-Healing 시스템은:

- **일시적 장애** (네트워크 오류, 타임아웃) → 자동 재시도로 복구
- **영구적 장애** (잘못된 카드, 잔액 부족) → Dead Letter Queue에 저장하여 추적
- **PG 장애** → Circuit Breaker로 시스템 보호

### 설계 원칙

1. **현재 인프라 활용**: Celery + Django ORM (추가 인프라 불필요)
2. **추상화**: 추후 Kafka/RabbitMQ 도입 시 쉽게 마이그레이션 가능
3. **Toggle 기반**: Circuit Breaker는 기본 OFF, 필요 시 운영자가 수동 활성화
4. **100% 추적**: 모든 실패 결제는 DLQ에 기록되어 누락 없음

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Payment Request                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Circuit Breaker Check                                │
│                    (CIRCUIT_BREAKER_ENABLED = False by default)              │
├─────────────────────────────────────────────────────────────────────────────┤
│  [Closed] ──────────────────────────────────────────────────────► Allow     │
│  [Open]   ──────► Recovery Timeout? ──► [Half-Open] ──► Test Request        │
│                         │                                                    │
│                         └──► Block Request                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           Toss Payment API                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                          │                    │
                     Success                 Failure
                          │                    │
                          ▼                    ▼
                   ┌──────────┐    ┌─────────────────────────────────┐
                   │  Done!   │    │      PaymentRecoveryHandler      │
                   └──────────┘    ├─────────────────────────────────┤
                                   │ 1. Retryable Error?              │
                                   │    → Schedule Retry (Backoff)    │
                                   │                                  │
                                   │ 2. Max Retries Exceeded?         │
                                   │    → Move to DLQ                 │
                                   │                                  │
                                   │ 3. Non-Retryable Error?          │
                                   │    → Move to DLQ (immediately)   │
                                   │                                  │
                                   │ 4. SLA Timeout?                  │
                                   │    → Abort + Rollback            │
                                   └─────────────────────────────────┘
                                                  │
                                                  ▼
                                   ┌─────────────────────────────────┐
                                   │      Dead Letter Queue          │
                                   │      (FailedPayment 모델)        │
                                   ├─────────────────────────────────┤
                                   │ • 수동 검토/복구 가능            │
                                   │ • 배치 재처리 가능               │
                                   │ • 30일 보관 후 자동 정리         │
                                   └─────────────────────────────────┘
```

---

## 핵심 기능

### 1. Retry with Exponential Backoff

일시적 오류 발생 시 지수적으로 증가하는 대기 시간을 두고 재시도합니다.

```
시도 1 실패 → 4초 대기 → 재시도
시도 2 실패 → 16초 대기 → 재시도
시도 3 실패 → 64초 대기 → 재시도
시도 4 실패 → DLQ로 이동 (최대 재시도 초과)
```

**설정**:
```python
PAYMENT_RECOVERY = {
    "RETRY_MAX_ATTEMPTS": 3,        # 최대 재시도 횟수
    "RETRY_BACKOFF_BASE": 4,        # 백오프 기본값 (4^n 초)
    "RETRY_BACKOFF_MAX": 180,       # 최대 대기 시간 (3분)
    "RETRY_JITTER": True,           # ±25% 랜덤 지터 추가
}
```

**Jitter란?**
- 동시에 여러 요청이 실패했을 때 모두 같은 시간에 재시도하면 서버에 부하가 집중됩니다.
- Jitter는 각 재시도 시간을 ±25% 범위 내에서 랜덤하게 분산시켜 이 문제를 해결합니다.

**재시도 가능한 오류** (TOSS_RETRYABLE_ERRORS):
- `NETWORK_ERROR`
- `TIMEOUT`
- `PROVIDER_ERROR`
- `FAILED_INTERNAL_SYSTEM_PROCESSING`
- HTTP 5xx 오류

### 2. Dead Letter Queue (DLQ)

복구 불가능한 결제를 별도 테이블에 저장하여 추적합니다.

**DLQ로 이동하는 경우**:
1. 최대 재시도 횟수 초과 (`max_retries_exceeded`)
2. 재시도 불가능한 오류 (`non_retryable_error`)
3. SLA 타임아웃 (`sla_timeout`)
4. Circuit Breaker 차단 (`circuit_breaker_open`)

**재시도 불가능한 오류** (TOSS_NON_RETRYABLE_ERRORS):
- `ALREADY_PROCESSED_PAYMENT` - 이미 처리된 결제
- `INVALID_CARD_NUMBER` - 잘못된 카드 번호
- `EXCEED_MAX_PAYMENT_AMOUNT` - 결제 한도 초과
- `PAY_PROCESS_CANCELED` - 사용자 취소
- 등 (전체 목록은 `shopping/constants.py` 참조)

**DLQ 레코드 정보**:
```python
FailedPayment:
    - payment, order, user (원본 참조)
    - payment_key, toss_order_id, amount (스냅샷)
    - failure_type, error_code, error_message
    - retry_count, last_retry_at
    - status: pending → reviewing → resolved/rejected
    - request_data, response_data, metadata (디버깅용)
    - expires_at (30일 후 자동 정리)
```

### 3. SLA Timeout Abort

결제 처리가 SLA 시간을 초과하면 자동으로 abort하고 롤백합니다.

**설정**:
```python
PAYMENT_RECOVERY = {
    "SLA_TIMEOUT_SECONDS": 300,     # 5분 내 완료 필요
    "SLA_ABORT_ENABLED": True,      # SLA abort 활성화
}
```

**동작 원리**:
1. `check_sla_violations` 태스크가 주기적으로 실행
2. `in_progress` 상태로 5분 이상 남아있는 결제 감지
3. DLQ에 `sla_timeout` 유형으로 저장
4. `rollback_payment_failure` 태스크 트리거 (재고 복구, 포인트 환불)

### 4. Circuit Breaker (Toggle 기반)

외부 PG 장애 시 시스템을 보호합니다. **기본적으로 비활성화**되어 있으며, 운영자가 필요 시 수동으로 활성화합니다.

**상태 전환**:
```
[Closed] ──(연속 5회 실패)──► [Open] ──(60초 후)──► [Half-Open]
    ▲                            │                       │
    │                            │                       │
    └──────(2회 연속 성공)────────┴───────(실패)──────────┘
```

**설정**:
```python
PAYMENT_RECOVERY = {
    "CIRCUIT_BREAKER_ENABLED": False,           # 기본 OFF
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 5,     # 연속 5회 실패 시 Open
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 60,     # 60초 후 Half-Open
    "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,     # Half-Open에서 2회 성공 시 Close
}
```

**왜 Toggle 기반인가?**
- 평상시에는 Circuit Breaker 체크 오버헤드가 없음
- PG 장애 감지 시 운영자가 수동으로 `force_open()` 호출
- 복구 확인 후 `force_close()` 호출

**운영자 수동 제어**:
```python
from shopping.tasks import reset_circuit_breaker

# PG 장애 감지 시 수동 Open
reset_circuit_breaker.delay(
    service_name="toss_payment",
    action="open",
    reason="PG 장애 감지 - 수동 차단",
    controlled_by_id=admin_user.id,
)

# PG 복구 확인 후 수동 Close
reset_circuit_breaker.delay(
    service_name="toss_payment",
    action="close",
    reason="PG 복구 확인",
    controlled_by_id=admin_user.id,
)
```

### 5. Rate Limit Cascade Detection

외부 API의 Rate Limit (HTTP 429) 응답이 연속으로 발생하면 자동으로 Circuit Breaker를 Open합니다.

**왜 필요한가?**
- Rate Limit은 일시적인 오류처럼 보이지만, 연속 발생 시 외부 서비스가 과부하 상태
- 계속 요청을 보내면 상황 악화 (차단 시간 증가, IP 블랙리스트 등)
- Circuit Breaker를 열어 외부 서비스 복구 시간 확보

**동작 원리**:
```
429 응답 10회 연속 (60초 내) → Circuit Breaker Open → 백오프 대기 후 재시도
```

**설정**:
```python
from shopping.services.self_healing.config import self_healing_config

# CircuitBreakerSettings에 추가된 설정
config = self_healing_config.circuit_breaker
config.rate_limit_cascade_threshold  # 기본값: 10 (연속 429 응답 횟수)
config.rate_limit_cascade_window_seconds  # 기본값: 60 (감지 윈도우)
```

**사용 예시**:
```python
from shopping.services.self_healing.circuit_breaker_service import (
    record_rate_limit,
    get_circuit_breaker
)

# 외부 API 호출 후 429 응답 수신 시
def call_external_api():
    response = requests.post(external_url, ...)

    if response.status_code == 429:
        # Rate limit 기록 및 cascade 체크
        is_cascade = record_rate_limit("external_service")

        if is_cascade:
            # Circuit Breaker가 자동으로 Open됨
            logger.warning("Rate limit cascade detected, CB opened")

    return response
```

### 6. Self-DDoS Protection

시스템 자체의 과도한 요청으로 인한 자기 자신에 대한 DDoS 방지 메커니즘입니다.

**왜 필요한가?**
- Retry 로직이 과도하게 동작하면 내부적으로 DDoS 상황 발생
- 외부 서비스가 복구 중인데 지속적인 재시도가 복구를 방해
- 적응형 백오프로 시스템 안정성 확보

**동작 원리**:
```
서비스별 요청 추적 → 임계치 초과 감지 → 적응형 백오프 적용
                                      ↓
               요청 100회/60초 초과 → 백오프 배수 증가 (1.5x)
                                      ↓
               연속 감지 시 → 백오프 2x, 3x, ... 증가
```

#### 6.1 Rate Limit Coordinator (분산 환경 지원)

> **신규 기능**: 다중 서버 환경에서도 100% Self-DDoS 방지를 보장하는 분산 Rate Limit 상태 관리 시스템

**SaaS 철학**:
- 외부 미들웨어에 의존하지 않음
- 어떤 환경에서도 이식 가능 (Portable)
- Database fallback으로 100% 커버리지 보장

**아키텍처**:
```
┌────────────────────────────────────────────────────────────────┐
│                   RateLimitCoordinator                          │
│                   (Central Coordination)                        │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Server 1   │  │   Server 2   │  │   Server 3   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
│         │                 │                 │                   │
│         └────────────────┼────────────────┘                   │
│                          ▼                                      │
│              ┌──────────────────────┐                          │
│              │   Storage Adapter    │                          │
│              │   (Auto-detected)    │                          │
│              └──────────────────────┘                          │
│                          │                                      │
│         ┌───────────────┼───────────────┐                     │
│         ▼               ▼               ▼                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐              │
│  │    Redis    │ │  Database   │ │  In-Memory  │              │
│  │   ~0.1ms    │ │   ~1-5ms    │ │   (local)   │              │
│  │ (optional)  │ │  (always)   │ │  (fallback) │              │
│  └─────────────┘ └─────────────┘ └─────────────┘              │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

**Storage 우선순위**:
1. **Redis** (있으면): 가장 빠름 (~0.1ms), 다중 서버 완벽 지원
2. **Database** (항상 가능): 보편적 (~1-5ms), 100% 커버리지 보장
3. **In-Memory** (fallback): 단일 서버, 테스트용

**사용 예시**:
```python
from selfhealing.services import RateLimitCoordinator

# 1. 기본 생성 (자동 감지)
coordinator = RateLimitCoordinator()

# 2. RetryHandler와 통합
from selfhealing.services import RetryHandler

handler = RetryHandler(config={
    "rate_limit_aware": True  # Rate Limit Coordinator 활성화
})

# 요청 실행 - 자동으로 429 처리 및 글로벌 쿨다운 적용
result = await handler.execute(make_api_request)
```

**Django 모델 설정**:
```python
# shopping/models/rate_limit_state.py
from django.db import models

class RateLimitState(models.Model):
    key = models.CharField(max_length=255, unique=True, db_index=True)
    cooldown_until = models.DateTimeField(null=True, blank=True)
    consecutive_429s = models.IntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'rate_limit_state'
        verbose_name = 'Rate Limit State'
```

> 📚 **상세 문서**: [RATE_LIMIT_COORDINATOR.md](./RATE_LIMIT_COORDINATOR.md)

**설정**:
```python
from shopping.services.self_healing.config import self_healing_config

config = self_healing_config.circuit_breaker
config.self_ddos_protection_enabled  # 기본값: True
config.self_ddos_request_threshold   # 기본값: 100 (요청 임계치)
config.self_ddos_window_seconds      # 기본값: 60 (감지 윈도우)
config.self_ddos_backoff_multiplier  # 기본값: 1.5 (백오프 배수)
```

**사용 예시**:
```python
from shopping.services.self_healing.circuit_breaker_service import (
    should_allow_with_ddos_protection,
    get_protection_status
)

# 요청 전 DDoS 보호 체크
def make_request_with_protection(service_name: str):
    allowed, backoff = should_allow_with_ddos_protection(service_name)

    if not allowed:
        logger.warning(f"Self-DDoS detected, backoff: {backoff}s")
        time.sleep(backoff)
        return None

    return call_external_service()

# 보호 상태 확인
def check_protection_status():
    status = get_protection_status("toss_payment")
    print(f"DDoS Detected: {status['self_ddos_detected']}")
    print(f"Current Backoff: {status['current_backoff']}s")
    print(f"Request Count: {status['request_count']}")
```

**적응형 백오프 계산**:
```
기본 백오프 × (배수 ^ 감지 횟수)

예시 (배수 = 1.5):
- 1차 감지: 4초 × 1.5^1 = 6초
- 2차 감지: 4초 × 1.5^2 = 9초
- 3차 감지: 4초 × 1.5^3 = 13.5초
```

---

## 설정

### 전체 설정 (settings/components/payment.py)

```python
PAYMENT_RECOVERY = {
    # Retry 정책 (지수 백오프)
    "RETRY_MAX_ATTEMPTS": int(os.environ.get("PAYMENT_RETRY_MAX_ATTEMPTS", 3)),
    "RETRY_BACKOFF_BASE": int(os.environ.get("PAYMENT_RETRY_BACKOFF_BASE", 4)),
    "RETRY_BACKOFF_MAX": int(os.environ.get("PAYMENT_RETRY_BACKOFF_MAX", 180)),
    "RETRY_JITTER": os.environ.get("PAYMENT_RETRY_JITTER", "true").lower() == "true",

    # SLA 정책
    "SLA_TIMEOUT_SECONDS": int(os.environ.get("PAYMENT_SLA_TIMEOUT", 300)),
    "SLA_ABORT_ENABLED": os.environ.get("PAYMENT_SLA_ABORT_ENABLED", "true").lower() == "true",

    # Circuit Breaker (Toggle 기반)
    "CIRCUIT_BREAKER_ENABLED": os.environ.get("PAYMENT_CIRCUIT_BREAKER_ENABLED", "false").lower() == "true",
    "CIRCUIT_BREAKER_FAILURE_THRESHOLD": int(os.environ.get("PAYMENT_CB_FAILURE_THRESHOLD", 5)),
    "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": int(os.environ.get("PAYMENT_CB_RECOVERY_TIMEOUT", 60)),
    "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": int(os.environ.get("PAYMENT_CB_SUCCESS_THRESHOLD", 2)),

    # Dead Letter Queue 정책
    "DLQ_ENABLED": os.environ.get("PAYMENT_DLQ_ENABLED", "true").lower() == "true",
    "DLQ_RETENTION_DAYS": int(os.environ.get("PAYMENT_DLQ_RETENTION_DAYS", 30)),

    # 알림 정책
    "NOTIFY_ON_DLQ": os.environ.get("PAYMENT_NOTIFY_ON_DLQ", "true").lower() == "true",
    "NOTIFY_ON_CIRCUIT_OPEN": os.environ.get("PAYMENT_NOTIFY_ON_CIRCUIT_OPEN", "true").lower() == "true",
}
```

### 환경변수 오버라이드 예시 (.env)

```bash
# 프로덕션 환경에서 Circuit Breaker 활성화
PAYMENT_CIRCUIT_BREAKER_ENABLED=true

# 더 긴 SLA 타임아웃 (10분)
PAYMENT_SLA_TIMEOUT=600

# 더 많은 재시도 (5회)
PAYMENT_RETRY_MAX_ATTEMPTS=5
```

---

## 모델

### FailedPayment (Dead Letter Queue)

**위치**: `shopping/models/failed_payment.py`

```python
class FailedPayment(models.Model):
    """
    Dead Letter Queue: 복구 불가능한 결제 추적
    """

    # 실패 유형
    FAILURE_TYPE_CHOICES = [
        ("max_retries_exceeded", "최대 재시도 횟수 초과"),
        ("non_retryable_error", "재시도 불가능한 오류"),
        ("sla_timeout", "SLA 타임아웃 초과"),
        ("circuit_breaker_open", "Circuit Breaker 차단"),
        ("manual_abort", "수동 중단"),
        ("unknown", "알 수 없는 오류"),
    ]

    # 처리 상태
    STATUS_CHOICES = [
        ("pending", "검토 대기"),
        ("reviewing", "검토 중"),
        ("resolved", "해결됨"),
        ("rejected", "복구 불가"),
        ("expired", "보관 기간 만료"),
    ]

    # 원본 참조
    payment = models.ForeignKey("Payment", null=True, ...)
    order = models.ForeignKey("Order", null=True, ...)
    user = models.ForeignKey("User", null=True, ...)

    # 스냅샷 데이터
    payment_key = models.CharField(max_length=200, blank=True)
    toss_order_id = models.CharField(max_length=100, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=0)

    # 실패 정보
    failure_type = models.CharField(max_length=30, choices=FAILURE_TYPE_CHOICES)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.TextField(blank=True)
    retry_count = models.PositiveIntegerField(default=0)

    # 처리 상태
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    resolved_at = models.DateTimeField(null=True)
    resolved_by = models.ForeignKey("User", null=True, related_name="resolved_failed_payments")
    resolution_note = models.TextField(blank=True)

    # 디버깅 데이터
    request_data = models.JSONField(default=dict)
    response_data = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict)

    # 보관 만료
    expires_at = models.DateTimeField(null=True)

    # 헬퍼 메서드
    def mark_as_resolved(self, resolved_by, note=""): ...
    def mark_as_rejected(self, resolved_by, note=""): ...

    @classmethod
    def create_from_payment_failure(cls, payment, order, user, ...): ...
```

### CircuitBreakerState

```python
class CircuitBreakerState(models.Model):
    """
    Circuit Breaker 상태 저장
    """

    STATE_CHOICES = [
        ("closed", "정상 (Closed)"),
        ("open", "차단 (Open)"),
        ("half_open", "테스트 중 (Half-Open)"),
    ]

    service_name = models.CharField(max_length=50, unique=True)
    state = models.CharField(max_length=20, choices=STATE_CHOICES, default="closed")
    failure_count = models.PositiveIntegerField(default=0)
    success_count = models.PositiveIntegerField(default=0)
    last_failure_at = models.DateTimeField(null=True)
    opened_at = models.DateTimeField(null=True)

    # 수동 제어
    manually_controlled = models.BooleanField(default=False)
    controlled_by = models.ForeignKey("User", null=True)
    control_reason = models.TextField(blank=True)

    # 헬퍼 메서드
    def record_failure(self): ...
    def record_success(self): ...
    def should_allow_request(self) -> bool: ...
    def force_open(self, controlled_by, reason): ...
    def force_close(self, controlled_by, reason): ...
```

---

## 서비스

### PaymentRecoveryHandler (추상 클래스)

**위치**: `shopping/services/payment_recovery_service.py`

```python
class PaymentRecoveryHandler(ABC):
    """
    결제 복구 핸들러 추상 클래스
    추후 Kafka/RabbitMQ 기반으로 교체 시 이 인터페이스를 구현
    """

    @abstractmethod
    def handle_failure(self, payment_id, order_id, error_code, error_message, retry_count, ...): ...

    @abstractmethod
    def schedule_retry(self, payment_id, order_id, attempt, delay_seconds=None): ...

    @abstractmethod
    def move_to_dlq(self, payment_id, order_id, failure_type, error_code, ...): ...

    @abstractmethod
    def check_circuit_breaker(self, service_name="toss_payment") -> bool: ...

    @abstractmethod
    def check_sla_timeout(self, created_at) -> bool: ...
```

### CeleryPaymentRecovery (구현체)

```python
class CeleryPaymentRecovery(PaymentRecoveryHandler):
    """
    Celery 기반 결제 복구 구현체
    """

    def get_backoff_delay(self, attempt: int) -> int:
        """지수 백오프 지연 시간 계산 (Jitter 포함)"""
        base = self.config.get("RETRY_BACKOFF_BASE", 4)
        max_delay = self.config.get("RETRY_BACKOFF_MAX", 180)
        delay = min(base ** attempt, max_delay)

        if self.config.get("RETRY_JITTER", True):
            jitter = delay * 0.25 * (random.random() * 2 - 1)
            delay = int(delay + jitter)

        return max(1, delay)

    def handle_failure(self, ...):
        """
        1. 재시도 불가능한 오류 → DLQ
        2. 최대 재시도 초과 → DLQ
        3. 그 외 → 재시도 스케줄링
        """
        ...

    def schedule_retry(self, payment_id, order_id, attempt, delay_seconds=None):
        """Celery 태스크로 재시도 스케줄링"""
        from ..tasks.payment_recovery_tasks import retry_failed_payment
        result = retry_failed_payment.apply_async(
            args=[payment_id, order_id, attempt],
            countdown=delay_seconds or self.get_backoff_delay(attempt),
        )
        return result.id

    def abort_for_sla(self, payment_id, order_id, created_at):
        """SLA 타임아웃 abort + 롤백 트리거"""
        ...

# 싱글톤 인스턴스
def get_payment_recovery_handler() -> PaymentRecoveryHandler:
    return CeleryPaymentRecovery()
```

---

## Celery 태스크

### 위치: `shopping/tasks/payment_recovery_tasks.py`

| 태스크 | 설명 | 큐 |
|--------|------|-----|
| `retry_failed_payment` | 실패한 결제 재시도 | `payment_critical` |
| `check_sla_violations` | SLA 위반 결제 감지 및 abort | `default` |
| `cleanup_expired_dlq` | 만료된 DLQ 레코드 정리 | `default` |
| `process_dlq_batch` | DLQ 배치 재처리 | `default` |
| `reset_circuit_breaker` | Circuit Breaker 수동 제어 | `default` |

### 주요 태스크 상세

```python
@shared_task(
    bind=True,
    name="shopping.tasks.payment_recovery_tasks.retry_failed_payment",
    queue="payment_critical",
    max_retries=0,  # 자체 재시도 로직 사용
    time_limit=60,
    acks_late=True,
)
def retry_failed_payment(self, payment_id: int, order_id: int, attempt: int):
    """
    실패한 결제 재시도

    1. Circuit Breaker 확인
    2. SLA 타임아웃 확인
    3. 이미 완료된 결제 확인
    4. Toss API 재호출
    5. 성공 시 finalize, 실패 시 handle_failure
    """
    ...
```

### Celery Beat 스케줄 (권장)

```python
# myproject/celery.py

app.conf.beat_schedule = {
    # 5분마다 SLA 위반 체크
    "check-sla-violations": {
        "task": "shopping.tasks.payment_recovery_tasks.check_sla_violations",
        "schedule": crontab(minute="*/5"),
    },
    # 매일 자정 만료된 DLQ 정리
    "cleanup-expired-dlq": {
        "task": "shopping.tasks.payment_recovery_tasks.cleanup_expired_dlq",
        "schedule": crontab(hour=0, minute=0),
    },
}
```

---

## 사용 예시

### 결제 실패 처리

```python
from shopping.services.payment_recovery_service import get_payment_recovery_handler
from shopping.utils.toss_payment import TossPaymentError

recovery = get_payment_recovery_handler()

try:
    # Toss API 호출
    toss_client.confirm_payment(payment_key, order_id, amount)
except TossPaymentError as e:
    # 실패 처리 (자동으로 재시도 또는 DLQ 이동)
    result = recovery.handle_failure(
        payment_id=payment.id,
        order_id=order.id,
        error_code=e.code,
        error_message=e.message,
        retry_count=current_retry_count,
    )

    if result["action"] == "retry_scheduled":
        logger.info(f"재시도 예약됨: task_id={result['task_id']}")
    elif result["action"] == "moved_to_dlq":
        logger.warning(f"DLQ 이동: dlq_id={result['dlq_id']}")
```

### Circuit Breaker 수동 제어

```python
from shopping.models.failed_payment import CircuitBreakerState

# 상태 조회
cb = CircuitBreakerState.objects.get(service_name="toss_payment")
print(f"현재 상태: {cb.state}, 실패 횟수: {cb.failure_count}")

# PG 장애 시 수동 Open
cb.force_open(
    controlled_by=request.user,
    reason="Toss 서버 장애 (2024-01-15 10:30)",
)

# PG 복구 후 수동 Close
cb.force_close(
    controlled_by=request.user,
    reason="Toss 서버 정상화 확인",
)
```

### DLQ 레코드 검토

```python
from shopping.models.failed_payment import FailedPayment

# 대기 중인 DLQ 레코드 조회
pending = FailedPayment.objects.filter(status="pending").order_by("-created_at")

for record in pending:
    print(f"[{record.failure_type}] {record.toss_order_id}")
    print(f"  에러: {record.error_code} - {record.error_message}")
    print(f"  재시도: {record.retry_count}회")

# 수동 해결 처리
record = FailedPayment.objects.get(pk=123)
record.mark_as_resolved(
    resolved_by=request.user,
    note="고객 확인 후 수동 환불 처리 완료",
)
```

---

## 테스트

### 테스트 파일

**Core Tests**: `shopping/tests/integration/test_l3_self_healing.py`

### 테스트 시나리오

| 분류 | 테스트 | 설명 |
|------|--------|------|
| **L3-A** | Exponential Backoff | 지수 백오프 지연 시간 계산 |
| **L3-A** | Max Limit | 최대 지연 시간 제한 |
| **L3-A** | Jitter | ±25% 랜덤 지터 |
| **L3-A** | Retry Scheduling | 재시도 스케줄링 |
| **L3-B** | Max Retries → DLQ | 최대 재시도 초과 시 DLQ |
| **L3-B** | Non-Retryable → DLQ | 재시도 불가 오류 즉시 DLQ |
| **L3-B** | Full Context | DLQ 레코드 전체 컨텍스트 |
| **L3-B** | Resolution Workflow | DLQ 해결 워크플로우 |
| **L3-C** | Timeout Detection | SLA 타임아웃 감지 |
| **L3-C** | Within Limit | SLA 시간 내 정상 처리 |
| **L3-C** | Disabled Mode | SLA abort 비활성화 |
| **L3-C** | Abort + Rollback | SLA abort + 롤백 트리거 |
| **L3-D** | Disabled by Default | Circuit Breaker 기본 비활성화 |
| **L3-D** | Opens on Threshold | 임계값 도달 시 Open |
| **L3-D** | Blocks When Open | Open 상태 요청 차단 |
| **L3-D** | Half-Open Transition | Recovery timeout 후 전환 |
| **L3-D** | Closes on Success | Half-Open 성공 시 Close |
| **L3-D** | Manual Control | 운영자 수동 제어 |
| **통합** | Complete Flow | 전체 복구 플로우 |


### 테스트 실행

```bash
# Docker Compose로 Core L3 테스트 실행
docker-compose run --rm web pytest shopping/tests/integration/test_l3_self_healing.py -v


# 특정 테스트만
docker-compose run --rm web pytest shopping/tests/integration/test_l3_self_healing.py::TestL3CircuitBreaker -v
```

---

## 확장성

### 현재 구조의 장점

```
┌─────────────────────────────────────────────────────────────────┐
│                    PaymentRecoveryHandler                        │
│                       (추상 클래스)                               │
├─────────────────────────────────────────────────────────────────┤
│  현재: CeleryPaymentRecovery                                     │
│        (Celery + Django ORM)                                     │
│                                                                  │
│  향후: KafkaPaymentRecovery                                      │
│        (Kafka + Schema Registry)                                 │
│                                                                  │
│  또는: RabbitMQPaymentRecovery                                   │
│        (RabbitMQ + Dead Letter Exchange)                         │
└─────────────────────────────────────────────────────────────────┘
```

### Kafka로 마이그레이션 시

```python
class KafkaPaymentRecovery(PaymentRecoveryHandler):
    """Kafka 기반 구현체"""

    def schedule_retry(self, payment_id, order_id, attempt, delay_seconds=None):
        # Kafka에 메시지 발행 (delay 토픽 활용)
        producer.send(
            topic="payment.retry",
            key=str(payment_id),
            value={
                "payment_id": payment_id,
                "order_id": order_id,
                "attempt": attempt,
                "scheduled_at": time.time() + delay_seconds,
            },
        )

    def move_to_dlq(self, ...):
        # Kafka DLQ 토픽에 발행
        producer.send(topic="payment.dlq", ...)
```

### 전환 시 변경사항

1. `settings.PAYMENT_RECOVERY_BACKEND = "kafka"` 설정 추가
2. `get_payment_recovery_handler()` 팩토리 함수 수정
3. Kafka Consumer 워커 추가
4. 기존 Celery 태스크는 그대로 유지 (점진적 전환 가능)

---

## 마이그레이션

### 0023_add_failed_payment_and_circuit_breaker.py

```bash
# 마이그레이션 생성 (이미 완료됨)
python manage.py makemigrations shopping --name="add_failed_payment_and_circuit_breaker"

# 마이그레이션 적용
python manage.py migrate shopping
```

**생성되는 테이블**:
- `shopping_failed_payment` (Dead Letter Queue)
- `shopping_circuit_breaker_state` (Circuit Breaker 상태)

---

## 운영 가이드

### 모니터링 포인트

1. **DLQ 레코드 수**: `pending` 상태가 급증하면 PG 장애 가능성
2. **Circuit Breaker 상태**: `open` 상태 지속 시 즉각 대응
3. **SLA 위반 빈도**: 자주 발생하면 SLA 값 조정 필요
4. **재시도 성공률**: 낮으면 백오프 정책 검토

### 알림 설정 (향후 확장)

```python
# settings.py
PAYMENT_RECOVERY = {
    ...
    "NOTIFY_ON_DLQ": True,              # DLQ 진입 시 알림
    "NOTIFY_ON_CIRCUIT_OPEN": True,     # Circuit Open 시 알림
}

# 향후 Slack/Email 연동
SLACK_WEBHOOK_URL = "https://hooks.slack.com/..."
ADMIN_EMAIL = "admin@example.com"
```

### 장애 대응 시나리오

**시나리오 1: PG 장애 감지**
1. 모니터링에서 연속 실패 감지
2. 운영자가 `reset_circuit_breaker(action="open")` 호출
3. 신규 결제 요청은 즉시 실패 처리 (사용자에게 "잠시 후 다시 시도" 안내)
4. PG 복구 확인 후 `reset_circuit_breaker(action="close")` 호출

**시나리오 2: DLQ 레코드 급증**
1. `pending` 상태 DLQ 레코드 검토
2. 공통 에러 코드 확인 (예: `REJECT_CARD_COMPANY`)
3. 고객 안내 또는 수동 처리
4. `mark_as_resolved()` 또는 `mark_as_rejected()` 처리

---

## 제한 사항

| 제한 사항 | 설명 |
|----------|------|
| **모니터링 지연 리스크** | SLA Timeout은 Celery Beat 주기(기본 1분)에 의존하므로, 최대 `SLA_TIMEOUT + 60초`까지 지연될 수 있음 |
| **운영자 개입 필요** | Circuit Breaker 활성화/비활성화, DLQ 레코드 검토 등 완전 자동화가 아닌 반자동 시스템 |

---

## 면접/포트폴리오 설명

> **"메시지 큐 없이도 Self-Healing 패턴을 구현했고, 추후 Kafka/RabbitMQ 도입 시 쉽게 마이그레이션 가능하도록 추상화했습니다."**

### 핵심 포인트

1. **기술 선택의 합리성**: 현재 인프라(Celery)로 충분히 구현 가능한 것은 과도한 엔지니어링 없이 구현
2. **확장성 고려**: 추상 클래스로 인터페이스 정의, 구현체 교체만으로 MQ 전환 가능
3. **운영 편의성**: Toggle 기반 Circuit Breaker로 평시 오버헤드 없이, 장애 시에만 활성화
4. **100% 추적**: Dead Letter Queue로 모든 실패 결제 누락 없이 추적

### 기술 스택

- **Django ORM**: FailedPayment, CircuitBreakerState 모델
- **Celery**: 비동기 재시도, 배치 처리
- **Redis**: Celery 브로커 (이미 사용 중)
- **PostgreSQL**: 데이터 영속성

---

## 파일 목록

| 파일 | 설명 |
|------|------|
| `myproject/settings/components/payment.py` | PAYMENT_RECOVERY 설정 |
| `shopping/models/failed_payment.py` | FailedPayment, CircuitBreakerState 모델 |
| `shopping/services/payment_recovery_service.py` | 복구 서비스 (추상화 + 구현체) |
| `shopping/tasks/payment_recovery_tasks.py` | Celery 태스크 |
| `shopping/tests/integration/test_l3_self_healing.py` | L3 테스트 (20개) |
| `shopping/migrations/0023_add_failed_payment_and_circuit_breaker.py` | 마이그레이션 |

---

*문서 작성일: 2025년 12월 8일*

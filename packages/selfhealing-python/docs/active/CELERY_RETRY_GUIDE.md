# Celery Retry 정책 가이드

## 개요

이 문서는 Celery 태스크의 재시도(Retry) 정책에 대한 구현 내용과 검증 가이드를 제공합니다.

## 변경 내용 요약

### 1. 재시도 설정 개선

#### 적용된 설정

| 설정 | 값 | 설명 |
|-----|---|-----|
| `retry_backoff` | True | 지수 백오프 활성화 (5s → 10s → 20s) |
| `retry_backoff_max` | 180 | 최대 백오프 3분 |
| `retry_jitter` | True | 랜덤 분산 (Thundering Herd 방지) |
| `time_limit` | 30 | 30초 후 강제 종료 |
| `soft_time_limit` | 25 | 25초에 정리 시간 확보 |
| `acks_late` | True | 완료 후 ACK (유실 방지) |

#### 적용된 태스크

- `call_toss_confirm_api` - Toss 결제 API 호출
- `finalize_payment_confirm` - 결제 최종 처리
- `send_verification_email_task` - 이메일 인증 발송
- `expire_points_task` - 포인트 만료 처리
- `send_expiry_notification_task` - 만료 알림 발송
- `send_email_notification` - 이메일 알림 발송
- `add_points_after_payment` - 결제 후 포인트 적립

### 2. Non-Retryable 오류 분류

재시도해도 의미 없는 오류를 명시적으로 분류했습니다.

#### 재시도 불가 오류 (`TOSS_NON_RETRYABLE_ERRORS`)

```python
# 이미 처리됨/중복
"ALREADY_PROCESSED_PAYMENT"
"ALREADY_CANCELED_PAYMENT"
"DUPLICATED_ORDER_ID"

# 잘못된 요청
"INVALID_PAYMENT_KEY"
"INVALID_AMOUNT"
"INVALID_CARD_NUMBER"

# 사용자 취소
"PAY_PROCESS_CANCELED"

# 인증 실패
"UNAUTHORIZED_KEY"

# 잔액/한도 문제
"REJECT_CARD_PAYMENT"
"REJECT_ACCOUNT_PAYMENT"
```

#### 재시도 가능 오류 (`TOSS_RETRYABLE_ERRORS`)

```python
"NETWORK_ERROR"
"TIMEOUT"
"PROVIDER_ERROR"
"FAILED_INTERNAL_SYSTEM_PROCESSING"
"COMMON_ERROR"
```

### 3. 타임아웃 처리 (SoftTimeLimitExceeded)

```python
except SoftTimeLimitExceeded:
    # 25초 경과 시 정리 작업 수행
    payment.status = "timeout"
    PaymentLog.objects.create(...)
    raise
```

---

## 검증 가이드

### 1. 테스트 실행

#### 전체 재시도 관련 테스트

```bash
docker-compose exec web pytest shopping/tests/tasks/test_retry_configuration.py -v --no-cov
```

예상 결과: 19개 테스트 통과

#### Celery 크래시 복구 테스트

```bash
docker-compose exec web pytest shopping/tests/integration/test_celery_crash_recovery.py -v --no-cov
```

예상 결과: 5개 테스트 통과

#### 전체 태스크 테스트

```bash
docker-compose exec web pytest shopping/tests/tasks/ -v --no-cov
```

예상 결과: 74개 테스트 통과

### 2. 설정 검증

#### Backoff 설정 확인

```python
from shopping.tasks.payment_tasks import call_toss_confirm_api

assert call_toss_confirm_api.retry_backoff is True
assert call_toss_confirm_api.retry_jitter is True
assert call_toss_confirm_api.retry_backoff_max == 180
assert call_toss_confirm_api.acks_late is True
```

#### Non-Retryable 오류 확인

```python
from shopping.constants import TOSS_NON_RETRYABLE_ERRORS

assert "ALREADY_PROCESSED_PAYMENT" in TOSS_NON_RETRYABLE_ERRORS
assert "INVALID_AMOUNT" in TOSS_NON_RETRYABLE_ERRORS
```

### 3. 수동 검증 시나리오

#### 시나리오 1: Non-Retryable 오류 재시도 안 함

1. Toss API를 mock하여 `ALREADY_PROCESSED_PAYMENT` 오류 발생
2. 태스크가 재시도 없이 즉시 실패하는지 확인
3. 로그에 "재시도 불가능한 오류" 메시지 확인

#### 시나리오 2: Retryable 오류 재시도

1. Toss API를 mock하여 `NETWORK_ERROR` 오류 발생
2. 태스크가 재시도되는지 확인
3. 재시도 간격이 지수적으로 증가하는지 확인

#### 시나리오 3: 타임아웃 처리

1. Toss API 응답을 30초 이상 지연시킴
2. 25초에 `SoftTimeLimitExceeded` 발생 확인
3. Payment 상태가 "timeout"으로 변경되는지 확인

---

## 변경 파일 목록

| 파일 | 변경 내용 |
|-----|----------|
| `shopping/constants.py` | `TOSS_NON_RETRYABLE_ERRORS`, `TOSS_RETRYABLE_ERRORS` 상수 추가 |
| `shopping/tasks/payment_tasks.py` | backoff, jitter, 타임아웃, Non-Retryable 로직 적용 |
| `shopping/tasks/email_tasks.py` | `autoretry_for` 제거, SMTPException만 재시도 |
| `shopping/tasks/point_tasks.py` | backoff, jitter, Non-Retryable 예외 구분 |
| `shopping/tests/tasks/test_retry_configuration.py` | 재시도 설정 검증 테스트 추가 |

---

## 재시도 로직 흐름도

```
TossPaymentError 발생
    │
    ▼
e.code in NON_RETRYABLE_ERRORS? ──Yes──► 즉시 실패 (재시도 안 함)
    │
    No
    ▼
e.code in RETRYABLE_ERRORS? ──Yes──► 재시도 (backoff + jitter)
    │
    No
    ▼
e.status_code >= 500? ──Yes──► 재시도 (서버 오류)
    │
    No
    ▼
즉시 실패 (4xx 클라이언트 오류)
```

---

## 업계 표준 준수

| 항목 | 업계 표준 | 우리 구현 |
|-----|----------|----------|
| Backoff | 지수 백오프 | ✅ `retry_backoff=True` |
| Jitter | 랜덤 분산 | ✅ `retry_jitter=True` |
| Max Backoff | 1~5분 | ✅ 180초 (3분) |
| 타임아웃 | 30초 권장 | ✅ `time_limit=30` |
| 유실 방지 | acks_late | ✅ `acks_late=True` |
| Non-Retryable 구분 | 명시적 분류 | ✅ 상수로 정의 |

---

## 주의사항

1. **멱등성 필수**: `acks_late=True` 사용 시 태스크는 멱등성이 보장되어야 함
   - 현재 `payment.is_paid` 체크로 중복 처리 방지됨

2. **모니터링**: 알 수 없는 오류 발생 시 로그 확인 필요
   - "클라이언트 오류, 재시도 안 함" 로그 모니터링

3. **상수 업데이트**: Toss API 오류 코드 변경 시 `TOSS_NON_RETRYABLE_ERRORS` 업데이트 필요

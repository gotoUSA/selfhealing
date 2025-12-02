# 외부 API 실패/지연/디버깅 케이스 테스트 추가 계획서

## 1. 현재 테스트 현황 분석

### 1.1 Toss 결제 관련 테스트 현황

| 테스트 케이스 | 현재 상태 | 파일 위치 |
|-------------|----------|----------|
| 결제 승인 성공 | ✅ 있음 | `test_payment_confirm.py` |
| 결제 취소 성공 | ✅ 있음 | `test_payment_cancel.py` |
| 네트워크 에러 | ✅ 있음 | `test_toss_payment.py` |
| Timeout 에러 | ✅ 있음 | `test_toss_payment.py` |
| Webhook DONE 처리 | ✅ 있음 | `test_webhook_payment_done.py` |
| Webhook CANCELED 처리 | ✅ 있음 | `test_webhook_payment_canceled.py` |
| Webhook FAILED 처리 | ✅ 있음 | `test_webhook_payment_failed.py` |
| Confirm + Webhook Race Condition | ✅ 있음 | `test_confirm_webhook_race.py` |
| 중복 Webhook 방지 | ✅ 있음 | `test_webhook_concurrency.py` |
| **Confirm Timeout + Retry** | ❌ 누락 | - |
| **Cancel Webhook 순서 뒤바뀜** | ❌ 누락 | - |
| **Webhook이 승인 전 도착하는 레이스** | ⚠️ 부분 | `test_confirm_webhook_race.py` (개선 필요) |

### 1.2 소셜 로그인 관련 테스트 현황

| 테스트 케이스 | 현재 상태 | 파일 위치 |
|-------------|----------|----------|
| Google OAuth 정상 처리 | ✅ 있음 | `test_adapters.py` |
| Kakao OAuth 정상 처리 | ✅ 있음 | `test_adapters.py` |
| Naver OAuth 정상 처리 | ✅ 있음 | `test_adapters.py` |
| 이메일 없는 소셜 로그인 | ✅ 있음 | `test_adapters.py` |
| 이메일 자동 인증 설정 | ✅ 있음 | `test_adapters.py` |
| **Kakao 이메일 미동의 고객** | ❌ 누락 | - |
| **Kakao payload 필드 누락** | ❌ 누락 | - |
| **Naver nickname만 있는 유저** | ❌ 누락 | - |
| **Naver email 미제공 계정** | ❌ 누락 | - |
| **OAuth 토큰 만료/갱신 실패** | ❌ 누락 | - |

---

## 2. 누락된 테스트 케이스 상세 목록

### 2.1 Toss 결제 관련 누락 케이스

#### 2.1.1 Toss Confirm Timeout → Retry (우선순위: 🔴 높음)
```
시나리오:
1. /api/payments/confirm/ 호출
2. Toss API 응답 지연 (timeout 발생)
3. Celery 태스크 재시도 로직
4. 재시도 중 중복 결제 방지
5. 최종 실패 시 사용자 알림
```

**필요한 테스트:**
- `test_confirm_timeout_triggers_retry`
- `test_confirm_retry_success_after_first_timeout`
- `test_confirm_retry_max_attempts_exceeded`
- `test_confirm_retry_idempotency_key_reuse`
- `test_confirm_partial_timeout_rollback`

#### 2.1.2 Cancel Webhook 도착 순서 뒤바뀜 (우선순위: 🔴 높음)
```
시나리오 A: CANCELED → DONE 순서로 도착
1. 사용자가 취소 요청
2. CANCELED 웹훅이 먼저 도착
3. 뒤늦게 DONE 웹훅 도착
4. DONE은 무시되어야 함

시나리오 B: DONE → CANCELED → DONE 순서
1. DONE 먼저 도착 (결제 완료)
2. CANCELED 도착 (취소 처리)
3. 또 다시 DONE 도착 (무시해야 함)
```

**필요한 테스트:**
- `test_canceled_then_done_webhook_ignores_done`
- `test_done_canceled_done_final_state_is_canceled`
- `test_webhook_order_independence`
- `test_multiple_canceled_webhooks_safe`

#### 2.1.3 Webhook이 결제 승인 전 도착하는 레이스 (우선순위: 🔴 높음)
```
시나리오:
1. Toss에서 Webhook을 매우 빠르게 발송
2. Webhook이 Confirm API 응답보다 먼저 서버에 도착
3. Payment가 아직 ready 상태일 때 DONE 웹훅 수신
4. 정상 처리 또는 재시도 로직
```

**필요한 테스트:**
- `test_webhook_arrives_before_confirm_response`
- `test_webhook_on_ready_payment_status`
- `test_webhook_retry_on_not_ready_payment`
- `test_webhook_and_confirm_true_parallel`

### 2.2 소셜 로그인 관련 누락 케이스

#### 2.2.1 Kakao 이메일 미동의 고객 (우선순위: 🔴 높음)
```
시나리오:
- 카카오에서 이메일 제공 동의 안 한 사용자
- kakao_account.email이 None인 경우
- 또는 kakao_account.has_email이 False
```

**필요한 테스트:**
- `test_kakao_no_email_consent_user`
- `test_kakao_has_email_false`
- `test_kakao_email_not_verified`
- `test_kakao_auto_generate_username_without_email`

#### 2.2.2 Kakao OAuth payload 필드 누락 (우선순위: 🟡 중간)
```
시나리오:
- kakao_account 필드 자체가 없음
- profile 객체가 없음
- nickname이 None
- id가 문자열 vs 정수
```

**필요한 테스트:**
- `test_kakao_missing_kakao_account_field`
- `test_kakao_missing_profile_field`
- `test_kakao_nickname_is_none`
- `test_kakao_id_type_variations`

#### 2.2.3 Naver nickname만 있는 유저 (우선순위: 🔴 높음)
```
시나리오:
- email은 없고 nickname만 있는 경우
- name 없이 nickname만 있는 경우
- 필수 필드(id)만 있는 극단적 케이스
```

**필요한 테스트:**
- `test_naver_only_nickname_no_email`
- `test_naver_only_nickname_no_name`
- `test_naver_minimal_response_only_id`
- `test_naver_generate_username_from_nickname`

#### 2.2.4 OAuth 토큰 관련 (우선순위: 🟡 중간)
```
시나리오:
- 액세스 토큰 만료
- 리프레시 토큰 갱신 실패
- 잘못된 토큰 응답
```

**필요한 테스트:**
- `test_oauth_access_token_expired`
- `test_oauth_refresh_token_failed`
- `test_oauth_invalid_token_response`

---

## 3. 추가로 발견된 중요 테스트 누락 케이스

### 3.1 결제 관련 추가 케이스 (우선순위: 🟡 중간)

| 케이스 | 설명 |
|-------|------|
| 부분 취소 후 재취소 | 부분 취소된 결제에 대한 추가 취소 처리 |
| 가상계좌 입금 대기 timeout | 가상계좌 입금 기한 초과 시 자동 취소 |
| 중복 결제 요청 방지 | 동일 주문에 대한 중복 confirm 요청 |
| 결제 금액 불일치 | Toss 응답 금액 != 요청 금액 |

### 3.2 동시성 관련 추가 케이스 (우선순위: 🔴 높음)

| 케이스 | 설명 |
|-------|------|
| 재고 경쟁 조건 | 동시 주문 시 재고 오버셀링 방지 |
| 포인트 경쟁 조건 | 동시 사용 시 마이너스 포인트 방지 |
| 주문 상태 전이 경쟁 | 동시 상태 변경 시 무결성 보장 |

### 3.3 에러 복구 관련 (우선순위: 🟡 중간)

| 케이스 | 설명 |
|-------|------|
| DB 연결 끊김 후 복구 | 트랜잭션 중 DB 연결 끊김 |
| Redis 연결 실패 | 캐시 서버 다운 시 폴백 |
| Celery Worker 크래시 | 태스크 실행 중 워커 종료 |

---

## 4. 테스트 파일 구조 계획

```
shopping/tests/
├── unit/
│   ├── services/
│   │   ├── test_toss_timeout_retry.py           # 신규
│   │   ├── test_toss_webhook_order_race.py      # 신규
│   │   └── ...
│   ├── adapters/
│   │   ├── test_kakao_edge_cases.py             # 신규
│   │   ├── test_naver_edge_cases.py             # 신규
│   │   └── ...
│   └── ...
├── integration/
│   ├── test_webhook_order_race.py               # 신규
│   ├── test_confirm_timeout_integration.py      # 신규
│   └── ...
└── api/
    └── webhook/
        ├── test_webhook_out_of_order.py         # 신규
        └── ...
```

---

## 5. 구현 우선순위 및 일정

### Phase 1: 🔴 높은 우선순위 (즉시 구현)

| 테스트 파일 | 예상 테스트 수 | 담당 범위 |
|------------|--------------|----------|
| `test_toss_timeout_retry.py` | 5개 | Toss API Timeout + Retry |
| `test_webhook_out_of_order.py` | 4개 | Webhook 순서 뒤바뀜 |
| `test_kakao_edge_cases.py` | 4개 | Kakao 이메일 미동의 |
| `test_naver_edge_cases.py` | 4개 | Naver nickname만 있는 케이스 |

### Phase 2: 🟡 중간 우선순위 (1주 내)

| 테스트 파일 | 예상 테스트 수 | 담당 범위 |
|------------|--------------|----------|
| `test_kakao_payload_missing.py` | 4개 | Kakao payload 필드 누락 |
| `test_oauth_token_errors.py` | 3개 | OAuth 토큰 만료/갱신 |
| `test_partial_cancel.py` | 3개 | 부분 취소 시나리오 |

### Phase 3: 🟢 낮은 우선순위 (필요시)

| 테스트 파일 | 예상 테스트 수 | 담당 범위 |
|------------|--------------|----------|
| `test_db_connection_recovery.py` | 3개 | DB 연결 복구 |
| `test_redis_fallback.py` | 3개 | Redis 폴백 |
| `test_celery_crash_recovery.py` | 3개 | Celery 크래시 복구 |

---

## 6. 테스트 구현 상세 설계

### 6.1 Toss Timeout + Retry 테스트

```python
# shopping/tests/unit/services/test_toss_timeout_retry.py

class TestTossConfirmTimeoutRetry:
    """Toss Confirm API Timeout 및 Retry 테스트"""
    
    def test_confirm_timeout_triggers_retry(self):
        """첫 번째 요청 timeout 시 자동 재시도"""
        pass
    
    def test_confirm_retry_success_after_timeout(self):
        """재시도에서 성공하는 케이스"""
        pass
    
    def test_confirm_max_retry_exceeded(self):
        """최대 재시도 횟수 초과 시 실패 처리"""
        pass
    
    def test_confirm_idempotency_on_retry(self):
        """재시도 시 멱등성 보장"""
        pass
    
    def test_confirm_timeout_partial_rollback(self):
        """Timeout 발생 시 부분 처리된 상태 롤백"""
        pass
```

### 6.2 Webhook 순서 뒤바뀜 테스트

```python
# shopping/tests/api/webhook/test_webhook_out_of_order.py

class TestWebhookOutOfOrder:
    """Webhook 도착 순서 뒤바뀜 시나리오"""
    
    def test_canceled_then_done_ignores_done(self):
        """CANCELED 먼저 → DONE 나중: DONE 무시"""
        pass
    
    def test_done_canceled_done_final_canceled(self):
        """DONE → CANCELED → DONE: 최종 상태 canceled"""
        pass
    
    def test_multiple_done_webhooks(self):
        """DONE 여러 번 도착해도 1번만 처리"""
        pass
    
    def test_webhook_before_payment_ready(self):
        """Payment가 ready 전에 webhook 도착"""
        pass
```

### 6.3 Kakao Edge Cases 테스트

```python
# shopping/tests/unit/adapters/test_kakao_edge_cases.py

class TestKakaoNoEmailConsent:
    """Kakao 이메일 미동의 사용자 처리"""
    
    def test_kakao_no_email_consent(self):
        """이메일 제공 미동의 사용자 가입"""
        pass
    
    def test_kakao_has_email_false(self):
        """has_email: false인 경우"""
        pass
    
    def test_kakao_email_not_verified_by_kakao(self):
        """카카오에서 이메일 미인증된 경우"""
        pass


class TestKakaoPayloadMissing:
    """Kakao OAuth payload 필드 누락"""
    
    def test_missing_kakao_account(self):
        """kakao_account 필드 없음"""
        pass
    
    def test_missing_profile(self):
        """profile 객체 없음"""
        pass
    
    def test_nickname_is_none(self):
        """nickname이 None"""
        pass
```

### 6.4 Naver Edge Cases 테스트

```python
# shopping/tests/unit/adapters/test_naver_edge_cases.py

class TestNaverMinimalData:
    """Naver 최소 데이터 케이스"""
    
    def test_naver_only_nickname(self):
        """email 없이 nickname만 있는 경우"""
        pass
    
    def test_naver_no_name_field(self):
        """name 필드 없음"""
        pass
    
    def test_naver_only_id(self):
        """id만 있는 극단적 케이스"""
        pass
    
    def test_naver_response_wrapper_missing(self):
        """response 래퍼 없는 경우"""
        pass
```

---

## 7. 예상 효과

### 7.1 버그 방지 효과
- Toss 결제 타임아웃 시 중복 결제 방지
- Webhook 순서 문제로 인한 결제 상태 불일치 방지
- 소셜 로그인 시 이메일 없는 사용자 처리 안정화

### 7.2 커버리지 개선
- 예상 신규 테스트: 약 30개
- 커버리지 증가 예상: +3~5%

### 7.3 운영 안정성
- 외부 API 장애 시 graceful degradation
- 사용자 경험 개선 (명확한 에러 메시지)

---

## 8. 다음 단계

1. Phase 1 테스트 파일 생성 및 구현
2. CI/CD 파이프라인에 신규 테스트 추가
3. 테스트 실행 및 버그 수정
4. Phase 2, 3 순차 구현

---

*작성일: 2025년 12월 3일*
*작성자: GitHub Copilot*

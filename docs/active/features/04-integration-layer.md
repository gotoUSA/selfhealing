# 4. Integration Layer

> 외부 시스템 연동

---

## 4.1 토스페이먼츠 연동

### 4.1.1 결제 API `shopping/utils/toss_payment.py`

| 메서드 | HTTP | 엔드포인트 | 설명 |
|--------|------|-----------|------|
| `confirm_payment()` | POST | `/v1/payments/confirm` | 결제 승인 |
| `cancel_payment()` | POST | `/v1/payments/{paymentKey}/cancel` | 결제 취소 |
| `get_payment()` | GET | `/v1/payments/{paymentKey}` | 결제 조회 |
| `create_billing_key()` | POST | `/v1/billing/authorizations/issue` | 빌링키 생성 |

### 4.1.2 웹훅 처리 `shopping/webhooks/toss_webhook_view.py`

| 함수 | 설명 |
|------|------|
| `toss_webhook` | 토스페이먼츠 웹훅 메인 핸들러 |
| `_verify_signature()` | 웹훅 서명 검증 |
| `_dispatch_event()` | 이벤트 타입별 분기 처리 |

### 4.1.3 웹훅 서비스 `shopping/services/toss_webhook_service.py`

| 이벤트 | 핸들러 | 설명 |
|--------|--------|------|
| `PAYMENT_STATUS_CHANGED` (DONE) | `handle_payment_done()` | 결제 완료 |
| `PAYMENT_STATUS_CHANGED` (CANCELED) | `handle_payment_canceled()` | 결제 취소 |
| `PAYMENT_STATUS_CHANGED` (FAILED) | `handle_payment_failed()` | 결제 실패 |

---

## 4.2 소셜 로그인 연동 (OAuth 2.0)

### 4.2.1 지원 제공자

| 제공자 | 설정 위치 |
|--------|----------|
| Google | `myproject/settings/components/social_auth.py` |
| Kakao | `myproject/settings/components/social_auth.py` |
| Naver | `myproject/settings/components/social_auth.py` |

### 4.2.2 OAuth 서비스 `shopping/services/social_auth_service.py`

| 메서드 | 설명 |
|--------|------|
| `exchange_code_for_token()` | Authorization Code → Access Token |
| `get_user_info()` | 제공자별 사용자 정보 조회 |
| `normalize_user_info()` | 제공자별 응답 정규화 |
| `process_oauth_callback()` | OAuth 콜백 전체 처리 |

---

## 4.3 이메일 발송

### 4.3.1 비동기 발송 `shopping/tasks/email_tasks.py`

| 태스크 | 큐 | 설명 |
|--------|-----|------|
| `send_verification_email_task` | default | 인증 이메일 |
| `send_email_task` | default | 일반 이메일 |
| `retry_failed_emails_task` | default | 실패 재발송 |

### 4.3.2 템플릿 `shopping/templates/email/`

| 템플릿 | 용도 |
|--------|------|
| `verification.html` | 이메일 인증 메일 |

---

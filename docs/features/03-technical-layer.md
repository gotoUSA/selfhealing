# 3. Technical Capability Layer

> 엔지니어링 역량 (Settings, Tasks, Utils, Admin, Observability)

---

## 3.1 프로젝트 설정 `myproject/settings/`

### 3.1.1 환경별 설정

| 파일 | 설명 |
|------|------|
| `__init__.py` | 환경 감지 및 설정 로드 (테스트/프로덕션/로컬 자동 전환) |
| `base.py` | 공통 설정 (SECRET_KEY, INSTALLED_APPS, MIDDLEWARE, REST_FRAMEWORK, JWT 등) |
| `local.py` | 로컬 개발 설정 (DEBUG=True, Debug Toolbar, 개발용 DB/Cache) |
| `production.py` | 프로덕션 설정 (SSL, HSTS, 보안 설정) |
| `test.py` | 테스트 설정 (CELERY_TASK_ALWAYS_EAGER, DummyCache, 빠른 해싱) |

### 3.1.2 컴포넌트 설정 `myproject/settings/components/`

| 파일 | 설정 항목 |
|------|---------|
| `logging.py` | `get_logging_config()` - 로깅 설정 생성, 로그 디렉토리 자동 생성 |
| `payment.py` | TOSS_CLIENT_KEY, TOSS_SECRET_KEY, TOSS_WEBHOOK_SECRET, TOSS_BASE_URL |
| `social_auth.py` | allauth 설정, SOCIALACCOUNT_PROVIDERS (Google/Kakao/Naver), OAuth 설정 |

---

## 3.2 Celery 비동기 처리 `myproject/celery.py`

### 3.2.1 스케줄 태스크 (Celery Beat)

| 태스크 | 스케줄 | 설명 |
|--------|--------|------|
| `retry-failed-emails` | 5분마다 | 실패한 이메일 재발송 |
| `delete-unverified-users` | 매일 03:00 | 미인증 사용자 삭제 |
| `cleanup-old-email-logs` | 일요일 04:00 | 오래된 이메일 로그 정리 |
| `cleanup-used-tokens` | 일요일 04:30 | 사용된 토큰 정리 |
| `cleanup-expired-tokens` | 매일 02:00 | 만료된 토큰 정리 |
| `expire-points-daily` | 매일 02:00 | 포인트 만료 처리 |
| `send-expiry-notifications` | 매일 10:00 | 만료 예정 알림 발송 |

### 3.2.2 큐 설정

| 큐 이름 | 용도 |
|--------|------|
| `default` | 기본 태스크 |
| `payment_critical` | 결제 관련 (최우선) |
| `order_processing` | 주문 처리 |
| `external_api` | 외부 API 호출 |
| `points` | 포인트 처리 |
| `notifications` | 알림 발송 |

---

## 3.3 비동기 태스크 `shopping/tasks/`

### 3.3.1 정리 태스크 `cleanup_tasks.py`

| 태스크 | 설명 |
|--------|------|
| `delete_unverified_users_task` | 미인증 사용자 삭제 |
| `cleanup_old_email_logs_task` | 오래된 이메일 로그 정리 |
| `cleanup_used_tokens_task` | 사용된 토큰 정리 |
| `cleanup_expired_tokens_task` | 만료된 토큰 정리 |

### 3.3.2 이메일 태스크 `email_tasks.py`

| 태스크 | 설명 |
|--------|------|
| `send_verification_email_task` | 인증 이메일 발송 |
| `retry_failed_emails_task` | 실패한 이메일 재발송 |
| `send_email_task` | 일반 이메일 발송 |

### 3.3.3 주문 태스크 `order_tasks.py`

| 태스크 | 설명 |
|--------|------|
| `process_order_heavy_tasks` | 주문 후처리 무거운 작업 |

### 3.3.4 결제 태스크 `payment_tasks.py`

| 태스크 | 설명 |
|--------|------|
| `call_toss_confirm_api` | 토스 결제 승인 API 호출 |
| `finalize_payment_confirm` | 결제 승인 완료 처리 |

### 3.3.5 포인트 태스크 `point_tasks.py`

| 태스크 | 설명 |
|--------|------|
| `expire_points_task` | 포인트 만료 처리 |
| `send_expiry_notification_task` | 만료 예정 알림 발송 |
| `send_email_notification` | 이메일 알림 발송 (포인트 관련 등) |
| `add_points_after_payment` | 결제 후 포인트 적립 |
| `process_single_user_points` | 특정 사용자 포인트 만료 개별 처리 |
| `cleanup_old_point_histories` | 오래된 포인트 이력 정리 |

---

## 3.4 유틸리티 `shopping/utils/`

**encryption.py**

| 함수 | 설명 |
|------|------|
| `encrypt_account_number()` | 계좌번호 암호화 |
| `decrypt_account_number()` | 계좌번호 복호화 |
| `mask_account_number()` | 계좌번호 마스킹 |
| `is_encrypted()` | 암호화 여부 확인 |

**toss_payment.py**

| 클래스/함수 | 설명 |
|------------|------|
| `TossPaymentClient` | 토스페이먼츠 API 클라이언트 |
| `TossPaymentClient.__init__()` | 클라이언트 초기화 |
| `TossPaymentClient.confirm_payment()` | 결제 승인 요청 |
| `TossPaymentClient.cancel_payment()` | 결제 취소 요청 |
| `TossPaymentClient.get_payment()` | 결제 정보 조회 |
| `TossPaymentClient.verify_webhook()` | 웹훅 서명 검증 |
| `TossPaymentClient.create_billing_key()` | 빌링키 생성 |
| `TossPaymentError` | 토스 결제 에러 클래스 |
| `TossPaymentError.__init__()` | 에러 초기화 (code, message, status_code) |
| `TossPaymentError.to_dict()` | 에러를 딕셔너리로 변환 |
| `get_error_message()` | 에러 코드별 메시지 조회 |

**spectacular_hooks.py**

| 함수 | 설명 |
|------|------|
| `preprocess_exclude_endpoints()` | 특정 엔드포인트 제외 |
| `postprocess_tags()` | 태그 정리 후처리 |
| `TAG_MAPPING` | 태그 변환 맵핑 |

---

## 3.5 권한 & 스로틀링

### 3.5.1 권한 `shopping/permissions.py`

| 클래스 | 메서드 | 설명 |
|--------|--------|------|
| `IsSeller` | `has_permission()` | 판매자 권한 확인 |
| `IsSellerAndOwner` | `has_permission()`, `has_object_permission()` | 판매자이면서 소유자 권한 |
| `IsSellerAndProductOwner` | `has_permission()`, `has_permission_for_product()` | 판매자이면서 상품 소유자 권한 |
| `IsSellerOrReadOnly` | `has_permission()` | 판매자이거나 읽기 전용 |
| `IsOrderOwnerOrAdmin` | `has_object_permission()` | 주문 소유자이거나 관리자 |

### 3.5.2 Rate Limiting `shopping/throttles.py`

| 클래스 | 용도 |
|--------|------|
| `LoginRateThrottle` | 로그인 요청 제한 |
| `RegisterRateThrottle` | 회원가입 요청 제한 |
| `TokenRefreshRateThrottle` | 토큰 갱신 요청 제한 |
| `PasswordResetRateThrottle` | 비밀번호 재설정 요청 제한 |
| `EmailVerificationRateThrottle` | 이메일 인증 요청 제한 |
| `EmailVerificationResendRateThrottle` | 이메일 재발송 요청 제한 |
| `PaymentRequestRateThrottle` | 결제 요청 제한 |
| `PaymentConfirmRateThrottle` | 결제 승인 요청 제한 |
| `PaymentCancelRateThrottle` | 결제 취소 요청 제한 |
| `OrderCreateRateThrottle` | 주문 생성 요청 제한 |
| `OrderCancelRateThrottle` | 주문 취소 요청 제한 |
| `GlobalAnonRateThrottle` | 익명 사용자 전역 제한 |
| `GlobalUserRateThrottle` | 인증 사용자 전역 제한 |
| `WebhookRateThrottle` | 웹훅 요청 제한 |

---

## 3.6 Admin 관리자 `shopping/admin.py`

### 3.6.1 Admin 클래스

| Admin 클래스 | 모델 | 특징 |
|-------------|------|------|
| `UserAdmin` | User | 사용자 관리, SellerProfileInline 포함 |
| `CategoryAdmin` | Category | DraggableMPTTAdmin (드래그앤드롭 계층 구조) |
| `ProductAdmin` | Product | ProductImageInline, ProductReviewInline 포함 |
| `ProductReviewAdmin` | ProductReview | 리뷰 전체 관리, 평점별 필터링 |
| `OrderAdmin` | Order | OrderItemInline 포함, 상태 변경 액션 |
| `CartAdmin` | Cart | 장바구니 관리, CartItemInline 포함 |
| `PaymentAdmin` | Payment | 결제 관리, 색상별 상태 표시 |
| `PaymentLogAdmin` | PaymentLog | 결제 로그 관리 (읽기 전용) |
| `PointHistoryAdmin` | PointHistory | 포인트 이력 관리 (삭제/추가 불가) |
| `EmailVerificationTokenAdmin` | EmailVerificationToken | 인증 토큰 관리 |
| `EmailLogAdmin` | EmailLog | 이메일 로그 관리, 통계 표시 |
| `NotificationAdmin` | Notification | 알림 관리 (추가 불가) |
| `ProductQuestionAdmin` | ProductQuestion | ProductAnswerInline 포함 |
| `ProductAnswerAdmin` | ProductAnswer | 답변 개별 관리 |
| `ReturnAdmin` | Return | ReturnItemInline 포함, 승인/거부 액션 |
| `ReturnItemAdmin` | ReturnItem | 반품 상품 개별 관리 (추가 불가) |
| `SellerProfileAdmin` | SellerProfile | 판매자 프로필 관리 |

### 3.6.2 Inline 클래스

| Inline 클래스 | 연결 Admin | 설명 |
|--------------|-----------|------|
| `SellerProfileInline` | UserAdmin | 판매자 프로필 (판매자인 경우에만 표시) |
| `ProductImageInline` | ProductAdmin | 상품 이미지 편집 |
| `ProductReviewInline` | ProductAdmin | 상품 리뷰 조회 (읽기 전용) |
| `OrderItemInline` | OrderAdmin | 주문 상품 조회 (삭제 불가) |
| `CartItemInline` | CartAdmin | 장바구니 아이템 편집 |
| `ProductAnswerInline` | ProductQuestionAdmin | 문의 답변 편집 |
| `ReturnItemInline` | ReturnAdmin | 반품 상품 조회 (삭제 불가) |

### 3.6.3 Admin 액션

| Admin 클래스 | 액션 | 설명 |
|-------------|------|------|
| `OrderAdmin` | `mark_as_paid` | 선택된 주문을 결제완료로 변경 |
| `OrderAdmin` | `mark_as_shipped` | 선택된 주문을 배송중으로 변경 |
| `OrderAdmin` | `mark_as_delivered` | 선택된 주문을 배송완료로 변경 |
| `EmailLogAdmin` | `mark_as_sent` | 선택한 이메일을 발송 완료로 표시 |
| `EmailLogAdmin` | `mark_as_failed` | 선택한 이메일을 발송 실패로 표시 |
| `ReturnAdmin` | `approve_returns` | 선택된 교환/환불 승인 |
| `ReturnAdmin` | `reject_returns` | 선택된 교환/환불 거부 |
| `ReturnAdmin` | `mark_as_received` | 선택된 교환/환불을 반품 도착으로 변경 |

### 3.6.4 Admin 사이트 설정

| 설정 | 값 |
|------|-----|
| `site_header` | "쇼핑몰 관리자" |
| `site_title` | "쇼핑몰 Admin" |
| `index_title` | "쇼핑몰 관리" |

---

## 3.7 시그널 & 어댑터

### 3.7.1 시그널 `shopping/signals.py`

| 시그널 핸들러 | 데코레이터 | 설명 |
|--------------|-----------|------|
| `handle_social_login` | `@receiver(pre_social_login)` | 소셜 로그인 전처리: 이메일 자동 인증 + 기존 인증 토큰 무효화 |
| `handle_new_social_account` | `@receiver(post_save, sender=SocialAccount)` | 신규 소셜 가입 시: 이메일 자동 인증 + 미사용 토큰 무효화 |
| `generate_order_number` | `@receiver(post_save, sender=Order)` | 주문 생성 시 주문번호 자동 생성 (YYYYMMDD + 6자리 ID 형식, 예: 202401150000042) |

### 3.7.2 어댑터 `shopping/adapters.py`

| 클래스/메서드 | 설명 |
|-------------|------|
| `CustomSocialAccountAdapter` | 소셜 계정 어댑터 |
| `pre_social_login()` | 소셜 로그인 전처리 |
| `is_auto_signup_allowed()` | 자동 회원가입 허용 여부 |
| `populate_user()` | 사용자 정보 채우기 |
| `save_user()` | 사용자 저장 |

---

## 3.8 상수 `shopping/constants.py`

| 상수 | 값 | 설명 |
|------|-----|------|
| `LOCK_CONTENTION_WARNING_THRESHOLD` | 1.0초 | 락 경합 경고 임계값 |
| `LOCK_CONTENTION_CRITICAL_THRESHOLD` | 3.0초 | 락 경합 위험 임계값 |

---

## 3.9 Management Commands `shopping/management/commands/`

| 커맨드 | 인자 | 설명 |
|--------|------|------|
| `cleanup_expired_tokens` | `--used-days`, `--dry-run` | 만료된 비밀번호 재설정 토큰 정리 |
| `cleanup_old_carts` | `--anonymous-days`, `--inactive-days`, `--dry-run` | 오래된 장바구니 정리 |
| `delete_unverified_users` | `--days`, `--dry-run`, `--verbose` | 미인증 계정 삭제 |

---

## 3.10 URL 라우팅 `shopping/urls.py`

### 3.10.1 Router 기반

| Router | 엔드포인트 |
|--------|-----------|
| `DefaultRouter` | 상품, 카테고리, 주문, 장바구니, 알림, 문의, 반품 |
| `NestedSimpleRouter` | 상품별 문의 중첩 라우팅 |

### 3.10.2 URL 패턴

| 카테고리 | 패턴 |
|---------|------|
| 인증 | register, login, logout, token/refresh, profile, password-change, withdraw |
| 이메일 인증 | send, verify-token, verify-code, resend, status |
| 비밀번호 재설정 | request, confirm |
| 소셜 로그인 | google, kakao, naver, accounts, disconnect |
| 찜 목록 | list, toggle, add, remove, bulk-add, clear, check, stats, move-to-cart |
| 결제 | request, confirm, confirm-async, cancel, fail, logs |
| 웹훅 | toss/webhook |

---

## 3.11 앱 설정 `shopping/apps.py`

| 클래스/메서드 | 설명 |
|-------------|------|
| `ShoppingConfig` | 앱 설정 클래스 |
| `ShoppingConfig.ready()` | 앱 준비 시 시그널 등록 |

---

## 3.12 프로젝트 루트 파일

| 파일 | 설명 |
|------|------|
| `manage.py` | Django 관리 명령 실행 |
| `check_points.py` | 포인트 현황 확인 스크립트 (디버깅용) |
| `myproject/__init__.py` | Celery 앱 로드 |
| `myproject/asgi.py` | ASGI 애플리케이션 |
| `myproject/wsgi.py` | WSGI 애플리케이션 |
| `myproject/urls.py` | 루트 URL 설정 |

---

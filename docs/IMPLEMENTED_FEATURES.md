# 구현된 기능 목록 (Implemented Features)

> 자동 생성일: 2025-12-04
> 스캔 범위: 프로젝트 루트, `myproject/`, `shopping/`, `load_tests/`, `scripts/` 디렉토리 전체

---

## 프로젝트 루트

### manage.py
- main - Django 관리 명령 실행

### check_points.py
- 포인트 현황 확인 스크립트 (디버깅용)

---

## myproject/settings/

### myproject/settings/__init__.py
- 환경 감지 및 설정 로드 (_is_testing, 테스트/프로덕션/로컬 자동 전환)

### myproject/settings/base.py
- Django 기본 설정
- SECRET_KEY 환경변수 검증
- ALLOWED_HOSTS 설정
- CSRF_TRUSTED_ORIGINS 설정
- INSTALLED_APPS 설정
- AUTH_USER_MODEL 설정
- MIDDLEWARE 설정
- REST_FRAMEWORK 기본 설정
- SPECTACULAR_SETTINGS (Swagger/ReDoc 설정)
- SIMPLE_JWT 설정
- CELERY_BROKER_URL 설정
- CELERY_RESULT_BACKEND 설정
- CELERY_BEAT_SCHEDULER 설정
- EMAIL_BACKEND 설정
- PASSWORD_RESET_TIMEOUT 설정
- ENCRYPTION_KEY 설정
- RETURN_REQUEST_DEADLINE_DAYS 설정
- FRONTEND_URL 설정

### myproject/settings/local.py
- DEBUG 모드 설정
- Debug Toolbar 설정
- INTERNAL_IPS 설정 (Docker 환경 지원)
- PostgreSQL 데이터베이스 설정 (로컬)
- Redis 캐시 설정 (로컬)
- Rate Limiting 설정 (개발용)
- 로깅 설정 (Debug 모드)
- ENCRYPTION_KEY 경고

### myproject/settings/production.py
- SECURE_SSL_REDIRECT 설정
- SECURE_PROXY_SSL_HEADER 설정
- SESSION_COOKIE_SECURE 설정
- CSRF_COOKIE_SECURE 설정
- HSTS 설정
- SECURE_CONTENT_TYPE_NOSNIFF 설정
- X_FRAME_OPTIONS 설정
- PostgreSQL 데이터베이스 설정 (프로덕션)
- Redis 캐시 설정 (프로덕션)
- Rate Limiting 설정 (프로덕션)
- 로깅 설정 (프로덕션)
- ENCRYPTION_KEY 필수 검증

### myproject/settings/test.py
- TESTING 플래그 설정
- PostgreSQL 데이터베이스 설정 (테스트)
- DummyCache 설정
- Celery 동기 실행 설정 (CELERY_TASK_ALWAYS_EAGER)
- Rate Limiting 비활성화 설정
- 빠른 패스워드 해싱 (MD5)
- 로컬 메모리 이메일 백엔드

---

## myproject/settings/components/

### myproject/settings/components/logging.py
- get_logging_config - 로깅 설정 생성 함수
- 로그 디렉토리 자동 생성

### myproject/settings/components/payment.py
- TOSS_CLIENT_KEY 설정
- TOSS_SECRET_KEY 설정
- TOSS_WEBHOOK_SECRET 설정
- TOSS_BASE_URL 설정

### myproject/settings/components/social_auth.py
- SITE_ID 설정
- ACCOUNT_SIGNUP_FIELDS 설정
- ACCOUNT_EMAIL_VERIFICATION 설정
- ACCOUNT_LOGIN_METHODS 설정
- SOCIALACCOUNT_AUTO_SIGNUP 설정
- SOCIALACCOUNT_EMAIL_VERIFICATION 설정
- SOCIALACCOUNT_QUERY_EMAIL 설정
- SOCIALACCOUNT_STORE_TOKENS 설정
- SOCIALACCOUNT_LOGIN_ON_GET 설정
- REST_AUTH 설정 (JWT)
- REST_AUTH_TOKEN_MODEL 설정
- ACCOUNT_ADAPTER 설정
- SOCIALACCOUNT_ADAPTER 설정
- SOCIALACCOUNT_PROVIDERS (Google) 설정
- SOCIALACCOUNT_PROVIDERS (Kakao) 설정
- SOCIALACCOUNT_PROVIDERS (Naver) 설정
- SOCIAL_LOGIN_REDIRECT_URI 설정
- OAUTH_CALLBACK_URI 설정
- OAUTH_REQUEST_TIMEOUT 설정
- OAUTH_RETRY_ATTEMPTS 설정
- OAUTH_RETRY_WAIT_SECONDS 설정

---

## myproject/urls.py
- admin/ URL 패턴
- api/ URL 패턴 (shopping.urls 포함)
- api-auth/ URL 패턴 (DRF 인증)
- accounts/ URL 패턴 (allauth)
- api/schema/ URL 패턴 (SpectacularAPIView)
- api/docs/swagger/ URL 패턴 (SpectacularSwaggerView)
- api/docs/redoc/ URL 패턴 (SpectacularRedocView)
- __debug__/ URL 패턴 (Debug Toolbar)
- 미디어 파일 서빙 (DEBUG 모드)
- 정적 파일 서빙 (DEBUG 모드)

---

## myproject/celery.py
- Celery 앱 생성 (myproject)
- Django 설정 모듈 자동 로드
- 태스크 자동 발견 (autodiscover_tasks)
- retry-failed-emails 스케줄 (5분마다)
- delete-unverified-users 스케줄 (매일 03:00)
- cleanup-old-email-logs 스케줄 (일요일 04:00)
- cleanup-used-tokens 스케줄 (일요일 04:30)
- cleanup-expired-tokens 스케줄 (매일 02:00)
- expire-points-daily 스케줄 (매일 02:00)
- send-expiry-notifications 스케줄 (매일 10:00)
- 큐 설정 (default)
- 큐 설정 (payment_critical)
- 큐 설정 (order_processing)
- 큐 설정 (external_api)
- 큐 설정 (points)
- 큐 설정 (notifications)
- 태스크 라우팅 설정
- debug_task
- task_failure_handler 시그널 핸들러

---

## myproject/__init__.py
- Celery 앱 로드 (celery_app)

---

## myproject/asgi.py
- application - ASGI 애플리케이션

---

## myproject/wsgi.py
- application - WSGI 애플리케이션

---

## Views (shopping/views/)

### shopping/views/auth_views.py
- RegisterView - 회원가입
- LoginView - 로그인
- LogoutView - 로그아웃
- TokenRefreshView - JWT 토큰 갱신
- UserProfileView - 사용자 프로필 조회
- UserProfileUpdateView - 사용자 프로필 수정
- PasswordChangeView - 비밀번호 변경
- WithdrawView - 회원 탈퇴

### shopping/views/cart_views.py
- CartViewSet.list - 장바구니 조회
- CartViewSet.retrieve - 장바구니 상세 조회
- CartItemViewSet.list - 장바구니 아이템 목록 조회
- CartItemViewSet.create - 장바구니 아이템 추가
- CartItemViewSet.update - 장바구니 아이템 수량 변경
- CartItemViewSet.partial_update - 장바구니 아이템 부분 수정
- CartItemViewSet.destroy - 장바구니 아이템 삭제
- CartItemViewSet.clear - 장바구니 비우기
- CartItemViewSet.summary - 장바구니 요약 정보 조회
- CartItemViewSet.check_stock - 재고 확인
- CartItemViewSet.check_price_changes - 가격 변동 확인
- CartItemViewSet.update_prices - 가격 업데이트

### shopping/views/email_verification_views.py
- SendVerificationEmailView - 이메일 인증 메일 발송
- VerifyEmailByTokenView - UUID 토큰으로 이메일 인증
- VerifyEmailByCodeView - 6자리 코드로 이메일 인증
- ResendVerificationEmailView - 인증 메일 재발송
- EmailVerificationStatusView - 이메일 인증 상태 조회

### shopping/views/notification_views.py
- NotificationViewSet.list - 알림 목록 조회
- NotificationViewSet.retrieve - 알림 상세 조회
- NotificationViewSet.destroy - 알림 삭제
- NotificationViewSet.unread - 읽지 않은 알림 조회
- NotificationViewSet.mark_read - 알림 읽음 처리
- NotificationViewSet.mark_all_read - 전체 알림 읽음 처리
- NotificationViewSet.clear_read - 읽은 알림 전체 삭제
- NotificationViewSet.unread_count - 읽지 않은 알림 개수 조회

### shopping/views/order_views.py
- OrderViewSet.list - 주문 목록 조회
- OrderViewSet.retrieve - 주문 상세 조회
- OrderViewSet.create - 주문 생성 (장바구니 → 주문 변환)
- OrderViewSet.cancel - 주문 취소
- OrderViewSet.my_orders - 내 주문 목록 조회
- OrderViewSet.status - 주문 상태 조회

### shopping/views/password_reset_views.py
- PasswordResetRequestView - 비밀번호 재설정 요청 (이메일 발송)
- PasswordResetConfirmView - 비밀번호 재설정 확인 (새 비밀번호 설정)

### shopping/views/payment_views.py
- PaymentViewSet.list - 결제 목록 조회
- PaymentViewSet.retrieve - 결제 상세 조회
- PaymentViewSet.request_payment - 결제 요청 (결제창 호출 전)
- PaymentViewSet.confirm - 결제 승인 (토스페이먼츠 콜백)
- PaymentViewSet.confirm_async - 비동기 결제 승인
- PaymentViewSet.cancel - 결제 취소
- PaymentViewSet.fail - 결제 실패 처리
- PaymentViewSet.logs - 결제 로그 조회

### shopping/views/point_views.py
- PointViewSet.list - 포인트 이력 조회
- PointViewSet.my_points - 내 포인트 조회
- PointViewSet.summary - 포인트 요약 정보 조회
- PointViewSet.expiring_soon - 만료 예정 포인트 조회
- PointViewSet.check - 포인트 사용 가능 여부 확인
- PointViewSet.use - 포인트 사용

### shopping/views/product_qa_views.py
- ProductQuestionViewSet.list - 상품 문의 목록 조회
- ProductQuestionViewSet.retrieve - 상품 문의 상세 조회
- ProductQuestionViewSet.create - 상품 문의 작성
- ProductQuestionViewSet.update - 상품 문의 수정
- ProductQuestionViewSet.partial_update - 상품 문의 부분 수정
- ProductQuestionViewSet.destroy - 상품 문의 삭제
- ProductQuestionViewSet.answer - 문의 답변 작성
- MyQuestionViewSet.list - 내 문의 목록 조회
- MyQuestionViewSet.retrieve - 내 문의 상세 조회

### shopping/views/product_views.py
- ProductViewSet.list - 상품 목록 조회
- ProductViewSet.retrieve - 상품 상세 조회
- ProductViewSet.create - 상품 등록
- ProductViewSet.update - 상품 수정
- ProductViewSet.partial_update - 상품 부분 수정
- ProductViewSet.destroy - 상품 삭제
- ProductViewSet.reviews - 상품 리뷰 목록 조회
- ProductViewSet.add_review - 상품 리뷰 작성
- ProductViewSet.my_products - 판매자 본인 상품 목록 조회
- ProductViewSet.set_primary_image - 대표 이미지 설정
- CategoryViewSet.list - 카테고리 목록 조회
- CategoryViewSet.retrieve - 카테고리 상세 조회
- CategoryViewSet.tree - 카테고리 트리 구조 조회
- CategoryViewSet.products - 카테고리별 상품 목록 조회

### shopping/views/return_views.py
- ReturnViewSet.list - 교환/환불 신청 목록 조회
- ReturnViewSet.retrieve - 교환/환불 신청 상세 조회
- ReturnViewSet.create - 교환/환불 신청
- ReturnViewSet.update_tracking - 반품 송장번호 입력
- ReturnViewSet.cancel - 교환/환불 신청 취소
- SellerReturnViewSet.list - 판매자 반품 목록 조회
- SellerReturnViewSet.retrieve - 판매자 반품 상세 조회
- SellerReturnViewSet.approve - 반품 승인
- SellerReturnViewSet.reject - 반품 거부
- SellerReturnViewSet.confirm_receive - 반품 도착 확인
- SellerReturnViewSet.complete - 반품 완료 처리 (환불/교환)

### shopping/views/social_auth_views.py
- GoogleLogin - 구글 소셜 로그인
- KakaoLogin - 카카오 소셜 로그인
- NaverLogin - 네이버 소셜 로그인
- SocialAccountListView - 연결된 소셜 계정 목록 조회
- SocialAccountDisconnectView - 소셜 계정 연결 해제

### shopping/views/user_views.py
- ProfileView.retrieve - 내 프로필 조회
- ProfileView.update - 프로필 전체 수정
- ProfileView.partial_update - 프로필 부분 수정
- PasswordChangeView.post - 비밀번호 변경
- withdraw - 회원 탈퇴 처리

### shopping/views/wishlist_views.py
- WishlistViewSet.list - 찜 목록 조회
- WishlistViewSet.toggle - 찜하기/찜 취소 토글
- WishlistViewSet.add - 찜 추가
- WishlistViewSet.remove - 찜 삭제
- WishlistViewSet.bulk_add - 여러 상품 한번에 찜하기
- WishlistViewSet.clear - 찜 목록 전체 삭제
- WishlistViewSet.check - 상품 찜 상태 확인
- WishlistViewSet.stats - 찜 목록 통계 조회
- WishlistViewSet.move_to_cart - 찜 상품 장바구니로 이동

### shopping/views/mixins.py
- EmailVerificationRequiredMixin - 이메일 인증 필요 믹스인
- EmailVerificationRequiredMixin.check_email_verification - 이메일 인증 여부 확인

---

## Models (shopping/models/)

### shopping/models/user.py
- User - 사용자 모델
- User.wishlist_products - 찜 목록 (ManyToMany)
- User.is_seller - 판매자 여부 확인
- User.get_total_orders - 총 주문 수 조회
- User.get_total_spent - 총 구매 금액 조회
- User.get_point_balance - 포인트 잔액 조회
- User.add_points - 포인트 추가
- User.use_points - 포인트 사용
- User.update_membership_level - 멤버십 레벨 업데이트
- User.check_membership_upgrade - 멤버십 업그레이드 확인
- User.get_available_coupons_count - 사용 가능한 쿠폰 수 조회
- User.add_to_wishlist - 찜 목록 추가
- User.is_in_wishlist - 찜 목록 여부 확인
- User.get_wishlist_count - 찜 목록 개수 조회
- User.clear_wishlist - 찜 목록 전체 삭제
- User.remove_from_wishlist - 찜 목록에서 삭제
- User.withdraw - 회원 탈퇴 처리
- User.restore - 탈퇴 회원 복구
- UserManager - 사용자 매니저
- UserManager.create_user - 일반 사용자 생성
- UserManager.create_superuser - 슈퍼유저 생성
- SellerProfile - 판매자 프로필 모델

### shopping/models/product.py
- Category - 카테고리 모델 (MPTT 계층 구조)
- Category.get_all_products - 하위 카테고리 포함 전체 상품 조회
- Category.get_product_count - 상품 수 조회
- Product - 상품 모델
- Product.stock_status - 재고 상태 프로퍼티
- Product.is_on_sale - 할인 중 여부 프로퍼티
- Product.discount_percentage - 할인율 프로퍼티
- Product.average_rating - 평균 평점 계산
- Product.increase_stock - 재고 증가
- Product.decrease_stock - 재고 감소
- Product.get_wishlist_count - 찜 수 조회
- Product.get_wishlist_users - 찜한 사용자 목록 조회
- Product.wishlist_count - 찜 수 프로퍼티
- Product.wishlist_count_display - 찜 수 표시 문자열
- ProductImage - 상품 이미지 모델
- ProductReview - 상품 리뷰 모델
- ProductReview.can_edit - 수정 가능 여부 확인
- ProductReview.can_delete - 삭제 가능 여부 확인

### shopping/models/cart.py
- Cart - 장바구니 모델
- Cart.get_or_create_active_cart - 활성 장바구니 조회 또는 생성
- Cart.get_total_amount - 총 금액 계산
- Cart.get_total_quantity - 총 수량 계산
- Cart.clear - 장바구니 비우기
- Cart.merge_from - 다른 장바구니 병합
- CartItem - 장바구니 아이템 모델
- CartItem.subtotal - 소계 계산 프로퍼티
- CartItem.is_available - 구매 가능 여부 확인
- CartItem.is_price_changed - 가격 변경 여부 프로퍼티
- CartItem.price_difference - 가격 차이 계산 프로퍼티
- CartItem.update_price_snapshot - 가격 스냅샷 업데이트

### shopping/models/order.py
- Order - 주문 모델
- Order.generate_order_number - 주문번호 생성
- Order.get_total_amount - 총 금액 계산
- Order.can_cancel - 취소 가능 여부 확인 프로퍼티
- Order.cancel - 주문 취소
- Order.get_total_shipping_fee - 총 배송비 계산
- Order.is_free_shipping - 무료 배송 여부 프로퍼티
- Order.update_earned_points - 적립 포인트 업데이트
- OrderItem - 주문 상품 모델
- OrderItem.get_subtotal - 소계 계산
- OrderQuerySet - 주문 쿼리셋
- OrderQuerySet.for_user - 사용자별 주문 필터링
- OrderQuerySet.recent - 최근 주문 필터링
- OrderQuerySet.with_status - 상태별 주문 필터링

### shopping/models/payment.py
- Payment - 결제 모델
- Payment.is_paid - 결제 완료 여부 프로퍼티
- Payment.can_cancel - 취소 가능 여부 프로퍼티
- Payment.mark_as_paid - 결제 완료 처리
- Payment.mark_as_canceled - 결제 취소 처리
- Payment.add_log - 결제 로그 추가
- PaymentLog - 결제 로그 모델

### shopping/models/point.py
- PointHistory - 포인트 이력 모델
- PointHistory.create_history - 포인트 이력 생성
- PointHistory.is_expired - 만료 여부 확인
- PointHistory.get_remaining_points - 남은 포인트 계산
- PointHistoryQuerySet - 포인트 이력 쿼리셋
- PointHistoryQuerySet.for_user - 사용자별 필터링
- PointHistoryQuerySet.earning - 적립 이력 필터링
- PointHistoryQuerySet.using - 사용 이력 필터링
- PointHistoryQuerySet.expiring_soon - 만료 예정 필터링
- PointHistoryQuerySet.expired - 만료된 이력 필터링
- PointHistoryQuerySet.not_expired - 유효한 이력 필터링
- PointHistoryQuerySet.get_total_earned - 총 적립 포인트 계산
- PointHistoryQuerySet.get_total_used - 총 사용 포인트 계산
- PointHistoryQuerySet.get_expiring_soon - 만료 예정 포인트 계산

### shopping/models/email_verification.py
- EmailVerificationToken - 이메일 인증 토큰 모델
- EmailVerificationToken.generate_verification_code - 6자리 인증 코드 생성
- EmailVerificationToken.is_expired - 만료 여부 확인
- EmailVerificationToken.can_resend - 재발송 가능 여부 확인
- EmailVerificationToken.mark_as_used - 사용 완료 처리
- EmailLog - 이메일 발송 로그 모델
- EmailLog.mark_sent - 발송 완료 처리
- EmailLog.mark_failed - 발송 실패 처리
- EmailLog.mark_opened - 열람 처리
- EmailLog.mark_clicked - 클릭 처리

### shopping/models/notification.py
- Notification - 알림 모델
- Notification.mark_as_read - 읽음 처리
- NotificationQuerySet - 알림 쿼리셋
- NotificationQuerySet.unread - 읽지 않은 알림 필터링
- NotificationQuerySet.for_user - 사용자별 필터링

### shopping/models/password_reset.py
- PasswordResetToken - 비밀번호 재설정 토큰 모델
- PasswordResetToken.generate_token - 토큰 생성
- PasswordResetToken.verify_token - 토큰 검증
- PasswordResetToken.is_expired - 만료 여부 확인
- PasswordResetToken.mark_as_used - 사용 완료 처리

### shopping/models/product_qa.py
- ProductQuestion - 상품 문의 모델
- ProductQuestion.is_answered - 답변 여부 프로퍼티
- ProductQuestion.can_view - 조회 권한 확인
- ProductQuestion.can_edit - 수정 권한 확인
- ProductQuestion.can_delete - 삭제 권한 확인
- ProductAnswer - 상품 답변 모델

### shopping/models/return_request.py
- Return - 교환/환불 신청 모델
- Return.generate_return_number - 반품번호 생성
- Return.get_masked_account_number - 마스킹된 계좌번호 조회
- Return.can_cancel - 취소 가능 여부 확인
- Return.calculate_refund_amount - 환불 금액 계산
- ReturnItem - 반품 상품 모델
- ReturnItem.get_subtotal - 소계 계산

### shopping/models/seller.py
- SellerProfile - 판매자 프로필 모델
- SellerProfile.save - 판매자 검증 후 저장

### shopping/models/webhook_event.py
- WebhookEvent - 웹훅 이벤트 로깅 모델

### shopping/models/point_history.py
- PointHistory 재수출 (backward compatibility)

---

## Services (shopping/services/)

### shopping/services/base.py
- log_service_call - 서비스 호출 로깅 데코레이터
- ServiceError - 서비스 에러 베이스 클래스

### shopping/services/cart_service.py
- CartService.get_or_create_cart - 장바구니 조회 또는 생성
- CartService.add_item - 장바구니 아이템 추가
- CartService.update_item_quantity - 장바구니 아이템 수량 변경
- CartService.remove_item - 장바구니 아이템 삭제
- CartService.clear_cart - 장바구니 비우기
- CartService.bulk_add_items - 여러 아이템 일괄 추가
- CartService.check_stock - 재고 확인
- CartService.check_price_changes - 가격 변동 확인
- CartService.update_item_prices - 가격 업데이트
- CartService.merge_anonymous_cart - 익명 장바구니 병합
- CartService.cleanup_unavailable_items - 구매 불가 아이템 정리
- CartServiceError - 장바구니 서비스 에러 클래스

### shopping/services/email_verification_service.py
- EmailVerificationService.send_verification_email - 인증 이메일 발송
- EmailVerificationService.verify_by_token - UUID 토큰으로 인증
- EmailVerificationService.verify_by_code - 6자리 코드로 인증
- EmailVerificationService.get_verification_status - 인증 상태 조회
- EmailVerificationService.invalidate_tokens - 토큰 무효화
- EmailVerificationService.get_active_token - 활성 토큰 조회
- EmailVerificationServiceError - 이메일 인증 서비스 에러 클래스

### shopping/services/notification_service.py
- NotificationService.get_queryset - 알림 쿼리셋 조회
- NotificationService.get_unread - 읽지 않은 알림 조회
- NotificationService.get_by_id - ID로 알림 조회
- NotificationService.mark_as_read - 알림 읽음 처리
- NotificationService.mark_single_as_read - 단일 알림 읽음 처리
- NotificationService.clear_read - 읽은 알림 전체 삭제
- NotificationService.delete_by_id - ID로 알림 삭제
- NotificationService.create - 알림 생성
- NotificationService.bulk_create - 알림 일괄 생성
- NotificationService.get_unread_count - 읽지 않은 알림 개수 조회
- NotificationServiceError - 알림 서비스 에러 클래스

### shopping/services/order_service.py
- OrderService.create_order_from_cart - 장바구니에서 주문 생성
- OrderService.create_order_hybrid - 하이브리드 방식 주문 생성 (비동기)
- OrderService.cancel_order - 주문 취소
- OrderService._create_order_items_and_decrease_stock - 주문 상품 생성 및 재고 차감
- OrderService._process_point_usage - 포인트 사용 처리
- OrderService._validate_point_usage - 포인트 사용 검증
- OrderServiceError - 주문 서비스 에러 클래스

### shopping/services/password_reset_service.py
- PasswordResetService.request_password_reset - 비밀번호 재설정 요청
- PasswordResetService.confirm_password_reset - 비밀번호 재설정 확인

### shopping/services/payment_service.py
- PaymentService.create_payment - 결제 정보 생성
- PaymentService.confirm_payment_sync - 동기 결제 승인
- PaymentService.confirm_payment_async - 비동기 결제 승인
- PaymentService.cancel_payment - 결제 취소
- PaymentService._generate_idempotency_key - 멱등성 키 생성
- PaymentService._check_idempotency - 멱등성 확인
- PaymentServiceError - 결제 서비스 에러 클래스

### shopping/services/point_service.py
- PointService.add_points - 포인트 적립
- PointService.use_points - 포인트 사용
- PointService.get_expired_points - 만료된 포인트 조회
- PointService.get_expiring_points_soon - 만료 예정 포인트 조회
- PointService.expire_points - 포인트 만료 처리
- PointService.get_remaining_points - 남은 포인트 계산
- PointService.get_usable_points - 사용 가능 포인트 조회
- PointService.use_points_fifo - FIFO 방식 포인트 사용
- PointService.send_expiry_notifications - 만료 예정 알림 발송

### shopping/services/point_query_service.py
- PointQueryService.get_filtered_history - 필터링된 포인트 이력 조회
- PointQueryService.get_history_summary - 포인트 이력 요약 조회
- PointQueryService.get_monthly_expiring_summary - 월별 만료 예정 요약
- PointQueryService.get_point_statistics - 포인트 통계 조회
- PointQueryService.get_recent_histories - 최근 이력 조회
- PointQueryService.build_filter_from_request - 요청에서 필터 생성

### shopping/services/product_qa_service.py
- ProductQAService.create_answer - 문의 답변 생성

### shopping/services/product_service.py
- ProductService.set_primary_image - 대표 이미지 설정

### shopping/services/return_service.py
- ReturnService.validate_order_for_return - 반품 가능 주문 검증
- ReturnService.validate_return_items - 반품 상품 검증
- ReturnService.generate_return_number - 반품번호 생성
- ReturnService.calculate_refund_amount - 환불 금액 계산
- ReturnService.create_return - 반품 신청 생성
- ReturnService.approve_return - 반품 승인
- ReturnService.reject_return - 반품 거부
- ReturnService.confirm_receive_return - 반품 도착 확인
- ReturnService.complete_refund - 환불 완료 처리
- ReturnService.complete_exchange - 교환 완료 처리
- ReturnValidationError - 반품 검증 에러 클래스

### shopping/services/shipping_service.py
- ShippingService.calculate_fee - 배송비 계산
- ShippingService.is_remote_area - 도서산간 지역 확인

### shopping/services/social_auth_service.py
- SocialAuthService.exchange_code_for_token - 인가 코드로 토큰 교환
- SocialAuthService.get_user_info - 사용자 정보 조회
- SocialAuthService.normalize_user_info - 사용자 정보 정규화
- SocialAuthService.process_oauth_callback - OAuth 콜백 처리

### shopping/services/token_service.py
- TokenService.validate_and_refresh_token - 토큰 검증 및 갱신
- TokenService.blacklist_token - 토큰 블랙리스트 등록

### shopping/services/toss_webhook_service.py
- TossWebhookService.is_webhook_duplicate - 웹훅 중복 확인
- TossWebhookService.mark_webhook_processed - 웹훅 처리 완료 표시
- TossWebhookService.log_webhook_event - 웹훅 이벤트 로깅
- TossWebhookService.handle_payment_done - 결제 완료 처리
- TossWebhookService.handle_payment_canceled - 결제 취소 처리
- TossWebhookService.handle_payment_failed - 결제 실패 처리
- TossWebhookServiceError - 토스 웹훅 서비스 에러 클래스

### shopping/services/user_service.py
- UserService.send_verification_email - 인증 이메일 발송
- UserService.create_tokens_for_user - 사용자 JWT 토큰 생성
- UserService.register_user - 사용자 등록
- UserService.login_user - 사용자 로그인
- UserService.withdraw_user - 사용자 탈퇴
- UserService.process_social_login - 소셜 로그인 처리

### shopping/services/wishlist_service.py
- WishlistService.toggle - 찜하기 토글
- WishlistService.add - 찜 추가
- WishlistService.remove - 찜 삭제
- WishlistService.bulk_add - 여러 상품 일괄 찜하기
- WishlistService.clear - 찜 목록 전체 삭제
- WishlistService.check - 찜 상태 확인
- WishlistService.get_list - 찜 목록 조회
- WishlistService.get_stats - 찜 목록 통계 조회
- WishlistService.move_to_cart - 찜 상품 장바구니로 이동
- WishlistFilter - 찜 목록 필터 클래스
- WishlistServiceError - 찜 서비스 에러 클래스

---

## Serializers (shopping/serializers/)

### shopping/serializers/cart_serializers.py
- CartItemSerializer - 장바구니 아이템 조회용
- CartItemCreateSerializer - 장바구니 아이템 추가용
- CartItemUpdateSerializer - 장바구니 아이템 수량 변경용
- CartSerializer - 장바구니 전체 정보 조회용
- SimpleCartSerializer - 장바구니 요약 정보용
- CartClearSerializer - 장바구니 비우기 확인용

### shopping/serializers/category_serializers.py
- CategorySerializer - 카테고리 기본 조회용
- CategoryTreeSerializer - 카테고리 트리 구조용
- CategoryCreateUpdateSerializer - 카테고리 생성/수정용
- SimpleCategorySerializer - 간단한 카테고리 정보용

### shopping/serializers/email_verification_serializers.py
- EmailVerificationTokenSerializer - 이메일 인증 토큰용 (Admin 전용)
- SendVerificationEmailSerializer - 이메일 발송 요청용
- VerifyEmailByTokenSerializer - UUID 토큰 인증용
- VerifyEmailByCodeSerializer - 6자리 코드 인증용
- ResendVerificationEmailSerializer - 이메일 재발송용
- EmailLogSerializer - 이메일 로그용 (Admin 전용)

### shopping/serializers/notification_serializers.py
- NotificationListSerializer - 알림 목록 조회용
- NotificationSerializer - 알림 상세 조회용
- NotificationMarkReadSerializer - 알림 읽음 처리용

### shopping/serializers/order_serializers.py
- OrderItemSerializer - 주문 상품 조회용
- OrderListSerializer - 주문 목록 조회용
- OrderDetailSerializer - 주문 상세 조회용
- OrderCreateSerializer - 주문 생성용
- TotalShippingFeeMixin - 배송비 계산 믹스인

### shopping/serializers/password_reset_serializers.py
- PasswordResetRequestSerializer - 비밀번호 재설정 요청용
- PasswordResetConfirmSerializer - 비밀번호 재설정 확인용

### shopping/serializers/payment_serializers.py
- PaymentSerializer - 결제 정보 조회용
- PaymentRequestSerializer - 결제 요청용
- PaymentConfirmSerializer - 결제 승인용
- PaymentCancelSerializer - 결제 취소용
- PaymentLogSerializer - 결제 로그 조회용
- PaymentWebhookSerializer - 토스페이먼츠 웹훅용
- PaymentFailSerializer - 결제 실패 처리용

### shopping/serializers/point_serializers.py
- PointHistorySerializer - 포인트 이력 조회용
- UserPointSerializer - 사용자 포인트 정보 조회용
- PointUseSerializer - 포인트 사용 요청용
- PointCancelSerializer - 취소/환불 포인트 회수용
- PointCheckSerializer - 포인트 사용 가능 여부 확인용

### shopping/serializers/product_qa_serializers.py
- ProductQuestionBaseSerializer - 문의 베이스 검증용
- ProductAnswerSerializer - 문의 답변 조회용
- ProductQuestionListSerializer - 문의 목록 조회용
- ProductQuestionDetailSerializer - 문의 상세 조회용
- ProductQuestionCreateSerializer - 문의 작성용
- ProductQuestionUpdateSerializer - 문의 수정용
- ProductAnswerCreateSerializer - 답변 작성용
- ProductAnswerUpdateSerializer - 답변 수정용

### shopping/serializers/product_serializers.py
- AverageRatingField - 평균 평점 필드
- ProductListSerializer - 상품 목록 조회용
- ProductImageSerializer - 상품 이미지용
- ProductReviewSerializer - 상품 리뷰용
- ProductDetailSerializer - 상품 상세 조회용
- ProductCreateUpdateSerializer - 상품 생성/수정용

### shopping/serializers/return_serializers.py
- ReturnItemSerializer - 반품 상품 항목용
- ReturnCreateSerializer - 교환/환불 신청용
- ReturnListSerializer - 교환/환불 목록 조회용
- ReturnDetailSerializer - 교환/환불 상세 조회용
- ReturnUpdateSerializer - 송장번호 업데이트용
- ReturnApproveSerializer - 반품 승인용
- ReturnRejectSerializer - 반품 거부용
- ReturnConfirmReceiveSerializer - 반품 도착 확인용
- ReturnCompleteSerializer - 반품 완료 처리용

### shopping/serializers/social_auth_serializers.py
- CustomSocialLoginSerializer - 소셜 로그인 커스텀용
- SocialAccountSerializer - 소셜 계정 정보용

### shopping/serializers/user_serializers.py
- UserListSerializer - 사용자 목록 조회용
- UserSerializer - 사용자 프로필 조회/수정용
- RegisterSerializer - 회원가입용
- LoginSerializer - 로그인용
- PasswordChangeSerializer - 비밀번호 변경용
- TokenResponseSerializer - 토큰 응답용

### shopping/serializers/wishlist_serializers.py
- WishlistProductSerializer - 찜 목록 상품 정보용
- WishlistToggleSerializer - 찜하기 토글용
- WishlistBulkAddSerializer - 여러 상품 찜하기용
- WishlistStatusSerializer - 찜 상태 확인용
- WishlistStatsSerializer - 찜 목록 통계용

---

## Tasks (shopping/tasks/)

### shopping/tasks/cleanup_tasks.py
- delete_unverified_users_task - 미인증 사용자 삭제 태스크
- cleanup_old_email_logs_task - 오래된 이메일 로그 정리 태스크
- cleanup_used_tokens_task - 사용된 토큰 정리 태스크
- cleanup_expired_tokens_task - 만료된 토큰 정리 태스크

### shopping/tasks/email_tasks.py
- send_verification_email_task - 인증 이메일 발송 태스크
- retry_failed_emails_task - 실패한 이메일 재발송 태스크
- send_email_task - 일반 이메일 발송 태스크

### shopping/tasks/order_tasks.py
- process_order_heavy_tasks - 주문 후처리 무거운 작업 태스크

### shopping/tasks/payment_tasks.py
- call_toss_confirm_api - 토스 결제 승인 API 호출 태스크
- finalize_payment_confirm - 결제 승인 완료 처리 태스크

### shopping/tasks/point_tasks.py
- expire_points_task - 포인트 만료 처리 태스크
- send_expiry_notification_task - 만료 예정 알림 발송 태스크
- send_email_notification - 이메일 알림 발송 함수
- add_points_after_payment - 결제 후 포인트 적립 태스크
- process_single_user_points - 단일 사용자 포인트 처리 함수
- cleanup_old_point_histories - 오래된 포인트 이력 정리 태스크

---

## Utils (shopping/utils/)

### shopping/utils/encryption.py
- encrypt_account_number - 계좌번호 암호화
- decrypt_account_number - 계좌번호 복호화
- mask_account_number - 계좌번호 마스킹
- is_encrypted - 암호화 여부 확인

### shopping/utils/toss_payment.py
- TossPaymentClient - 토스페이먼츠 API 클라이언트
- TossPaymentClient.confirm_payment - 결제 승인 요청
- TossPaymentClient.cancel_payment - 결제 취소 요청
- TossPaymentClient.get_payment - 결제 정보 조회
- TossPaymentClient.verify_webhook - 웹훅 서명 검증
- TossPaymentClient.create_billing_key - 빌링키 생성
- TossPaymentError - 토스 결제 에러 클래스
- get_error_message - 에러 메시지 조회 함수

### shopping/utils/spectacular_hooks.py
- preprocess_exclude_endpoints - 특정 엔드포인트 제외 훅
- postprocess_tags - 태그 정리 후처리 훅
- TAG_MAPPING - 태그 변환 맵핑

---

## Webhooks (shopping/webhooks/)

### shopping/webhooks/toss_webhook_view.py
- toss_webhook - 토스페이먼츠 웹훅 처리 뷰
- _verify_signature - 웹훅 서명 검증
- _dispatch_event - 이벤트 디스패치 처리

---

## Admin (shopping/admin.py)
- UserAdmin - 사용자 관리
- CategoryAdmin - 카테고리 관리 (DraggableMPTTAdmin)
- ProductAdmin - 상품 관리
- ProductImageInline - 상품 이미지 인라인
- ProductReviewAdmin - 상품 리뷰 관리
- OrderAdmin - 주문 관리
- OrderItemInline - 주문 상품 인라인
- CartAdmin - 장바구니 관리
- CartItemAdmin - 장바구니 아이템 관리
- CartItemInline - 장바구니 아이템 인라인
- PaymentAdmin - 결제 관리
- PaymentLogAdmin - 결제 로그 관리
- PaymentLogInline - 결제 로그 인라인
- PointHistoryAdmin - 포인트 이력 관리
- EmailVerificationTokenAdmin - 이메일 인증 토큰 관리
- EmailLogAdmin - 이메일 로그 관리
- NotificationAdmin - 알림 관리
- ProductQuestionAdmin - 상품 문의 관리
- ProductAnswerAdmin - 상품 답변 관리
- ProductAnswerInline - 상품 답변 인라인
- ReturnAdmin - 교환/환불 관리
- ReturnItemAdmin - 반품 상품 관리
- ReturnItemInline - 반품 상품 인라인
- SellerProfileAdmin - 판매자 프로필 관리

---

## Permissions (shopping/permissions.py)
- IsSeller - 판매자 권한 확인
- IsSellerAndOwner - 판매자이면서 소유자 권한 확인
- IsSellerAndProductOwner - 판매자이면서 상품 소유자 권한 확인
- IsSellerOrReadOnly - 판매자이거나 읽기 전용 권한
- IsOrderOwnerOrAdmin - 주문 소유자이거나 관리자 권한 확인

---

## Throttles (shopping/throttles.py)
- LoginRateThrottle - 로그인 요청 제한
- RegisterRateThrottle - 회원가입 요청 제한
- TokenRefreshRateThrottle - 토큰 갱신 요청 제한
- PasswordResetRateThrottle - 비밀번호 재설정 요청 제한
- EmailVerificationRateThrottle - 이메일 인증 요청 제한
- EmailVerificationResendRateThrottle - 이메일 재발송 요청 제한
- PaymentRequestRateThrottle - 결제 요청 제한
- PaymentConfirmRateThrottle - 결제 승인 요청 제한
- PaymentCancelRateThrottle - 결제 취소 요청 제한
- OrderCreateRateThrottle - 주문 생성 요청 제한
- OrderCancelRateThrottle - 주문 취소 요청 제한
- GlobalAnonRateThrottle - 익명 사용자 전역 요청 제한
- GlobalUserRateThrottle - 인증 사용자 전역 요청 제한
- WebhookRateThrottle - 웹훅 요청 제한

---

## Signals (shopping/signals.py)
- handle_social_login - 소셜 로그인 처리 시그널
- handle_new_social_account - 새 소셜 계정 연결 시그널
- generate_order_number - 주문번호 생성 시그널

---

## Adapters (shopping/adapters.py)
- CustomSocialAccountAdapter - 소셜 계정 어댑터
- CustomSocialAccountAdapter.pre_social_login - 소셜 로그인 전처리
- CustomSocialAccountAdapter.is_auto_signup_allowed - 자동 회원가입 허용 여부
- CustomSocialAccountAdapter.populate_user - 사용자 정보 채우기
- CustomSocialAccountAdapter.save_user - 사용자 저장

---

## Constants (shopping/constants.py)
- LOCK_CONTENTION_WARNING_THRESHOLD - 락 경합 경고 임계값 (1.0초)
- LOCK_CONTENTION_CRITICAL_THRESHOLD - 락 경합 위험 임계값 (3.0초)

---

## Templates (shopping/templates/)

### shopping/templates/email/
- verification.html - 이메일 인증 메일 템플릿

### shopping/templates/shopping/
- payment_success.html - 결제 성공 페이지 템플릿
- payment_fail.html - 결제 실패 페이지 템플릿
- payment_test.html - 결제 테스트 페이지 템플릿
- social_test.html - 소셜 로그인 테스트 페이지 템플릿

---

## DTOs (shopping/dtos/)

### shopping/dtos/product_filter.py
- ProductFilterParams - 상품 필터 파라미터 데이터 클래스
- ProductFilterParams.from_request - 요청에서 필터 생성

---

## Management Commands (shopping/management/commands/)

### shopping/management/commands/cleanup_expired_tokens.py
- Command.handle - 만료된 비밀번호 재설정 토큰 정리
- Command.add_arguments - 커맨드 인자 정의 (--used-days, --dry-run)

### shopping/management/commands/cleanup_old_carts.py
- Command.handle - 오래된 장바구니 정리
- Command.add_arguments - 커맨드 인자 정의 (--anonymous-days, --inactive-days, --dry-run)

### shopping/management/commands/create_load_test_users.py
- Command.handle - 부하 테스트용 사용자 생성
- Command.add_arguments - 커맨드 인자 정의 (--count, --points, --clear)

### shopping/management/commands/create_test_data.py
- Command.handle - 테스트용 데이터 생성
- Command.add_arguments - 커맨드 인자 정의 (--preset, --clear, --users, --reviews, --no-reviews, --show-presets)
- Command.show_presets - 프리셋 정보 표시
- Command.clear_existing_data - 기존 데이터 삭제
- Command.create_categories - 카테고리 생성
- Command.create_users - 테스트 사용자 생성
- Command.create_products - 상품 생성
- Command.create_reviews - 리뷰 생성
- Command.create_sample_carts - 장바구니 샘플 생성
- Command.create_sample_orders - 주문 샘플 생성
- Command.print_summary - 생성 결과 요약 출력
- Command.print_test_accounts - 테스트 계정 정보 출력

### shopping/management/commands/delete_unverified_users.py
- Command.handle - 미인증 계정 삭제
- Command.add_arguments - 커맨드 인자 정의 (--days, --dry-run, --verbose)

### shopping/management/commands/test_point_expiry.py
- Command.handle - 포인트 만료 기능 테스트
- Command.add_arguments - 커맨드 인자 정의 (--create-test-data, --expire, --notify, --use-points, --username)
- Command.create_test_data - 테스트 데이터 생성
- Command.test_expire_points - 포인트 만료 처리 테스트
- Command.test_notifications - 만료 예정 알림 테스트
- Command.test_use_points - FIFO 방식 포인트 사용 테스트

---

## URL Routing (shopping/urls.py)
- DefaultRouter - 상품, 카테고리, 주문, 장바구니, 알림, 문의, 반품 라우팅
- NestedSimpleRouter - 상품별 문의 중첩 라우팅
- 인증 관련 URL 패턴 (register, login, logout, token/refresh, profile, password-change, withdraw)
- 이메일 인증 URL 패턴 (send, verify-token, verify-code, resend, status)
- 비밀번호 재설정 URL 패턴 (request, confirm)
- 소셜 로그인 URL 패턴 (google, kakao, naver, accounts, disconnect)
- 찜 목록 URL 패턴 (list, toggle, add, remove, bulk-add, clear, check, stats, move-to-cart)
- 결제 URL 패턴 (request, confirm, confirm-async, cancel, fail, logs)
- 웹훅 URL 패턴 (toss/webhook)

---

## App Configuration (shopping/apps.py)
- ShoppingConfig - 앱 설정 클래스
- ShoppingConfig.ready - 앱 준비 시 시그널 등록

---

## Test Fixtures (shopping/tests/conftest.py)

### shopping/tests/conftest.py
- setup_celery_for_tests - Celery 테스트 설정 fixture
- setup_throttle_for_tests - Throttle 비활성화 fixture
- setup_logging_for_tests - 로깅 설정 fixture
- django_db_setup - 데이터베이스 설정 fixture
- api_client - APIClient fixture
- user - 일반 사용자 fixture
- seller_user - 판매자 사용자 fixture
- unverified_user - 미인증 사용자 fixture
- inactive_user - 비활성 사용자 fixture
- withdrawn_user - 탈퇴 사용자 fixture
- user_factory - 사용자 팩토리 fixture
- authenticated_client - 인증된 클라이언트 fixture
- seller_authenticated_client - 판매자 인증 클라이언트 fixture
- get_tokens - JWT 토큰 생성 fixture
- category - 카테고리 fixture
- product - 상품 fixture
- out_of_stock_product - 품절 상품 fixture
- inactive_product - 비활성 상품 fixture
- product_factory - 상품 팩토리 fixture
- multiple_products - 다중 상품 fixture
- cart - 장바구니 fixture
- cart_with_items - 아이템 포함 장바구니 fixture
- shipping_data - 배송 정보 fixture
- invalid_shipping_field_factory - 잘못된 배송 정보 팩토리 fixture
- order_factory - 주문 팩토리 fixture
- freeze_time - 시간 고정 fixture
- order - 주문 fixture
- paid_order - 결제 완료 주문 fixture
- order_with_multiple_items - 다중 아이템 주문 fixture
- pending_order - 대기 주문 fixture
- payment - 결제 fixture
- canceled_payment - 취소된 결제 fixture
- add_to_cart_helper - 장바구니 추가 헬퍼 fixture

### shopping/tests/api/auth/conftest.py
- reset_social_models - 소셜 모델 리셋 fixture
- expired_access_token - 만료된 액세스 토큰 fixture
- expired_refresh_token - 만료된 리프레시 토큰 fixture
- invalid_token - 유효하지 않은 토큰 fixture
- tampered_token - 변조된 토큰 fixture
- blacklisted_refresh_token - 블랙리스트 토큰 fixture
- verification_token - 인증 토큰 fixture
- expired_verification_token - 만료된 인증 토큰 fixture
- used_verification_token - 사용된 인증 토큰 fixture
- recent_verification_token - 최근 인증 토큰 fixture
- verification_token_factory - 인증 토큰 팩토리 fixture
- social_site - 소셜 사이트 fixture
- social_app_google - 구글 소셜 앱 fixture
- social_app_kakao - 카카오 소셜 앱 fixture
- social_app_naver - 네이버 소셜 앱 fixture
- mock_google_oauth_data - 구글 OAuth 모의 데이터 fixture
- mock_kakao_oauth_data - 카카오 OAuth 모의 데이터 fixture
- mock_naver_oauth_data - 네이버 OAuth 모의 데이터 fixture
- mock_time - 시간 모의 fixture
- password_reset_token - 비밀번호 재설정 토큰 fixture
- expired_password_reset_token - 만료된 비밀번호 재설정 토큰 fixture
- user_with_google_account - 구글 연결 사용자 fixture
- user_with_multiple_social_accounts - 다중 소셜 연결 사용자 fixture
- valid_login_data - 유효한 로그인 데이터 fixture
- valid_registration_data - 유효한 회원가입 데이터 fixture
- password_change_data - 비밀번호 변경 데이터 fixture
- profile_update_data - 프로필 수정 데이터 fixture
- registration_data_factory - 회원가입 데이터 팩토리 fixture
- login_data_factory - 로그인 데이터 팩토리 fixture
- password_change_data_factory - 비밀번호 변경 데이터 팩토리 fixture
- password_reset_confirm_data_factory - 비밀번호 재설정 확인 데이터 팩토리 fixture
- user_with_points - 포인트 보유 사용자 fixture
- second_user - 두 번째 사용자 fixture

### shopping/tests/api/webhook/conftest.py
- webhook_url - 웹훅 URL fixture
- mock_verify_webhook - 웹훅 검증 모의 fixture
- webhook_data_builder - 웹훅 데이터 빌더 fixture
- webhook_signature - 웹훅 서명 fixture

### shopping/tests/integration/conftest.py
- default_shipping_info - 기본 배송 정보 fixture
- remote_shipping_data - 도서산간 배송 정보 fixture
- create_order - 주문 생성 헬퍼 fixture
- toss_response_builder - 토스 응답 빌더 fixture
- toss_cancel_response_builder - 토스 취소 응답 빌더 fixture
- build_confirm_request - 결제 승인 요청 빌더 fixture
- build_payment_key - 결제 키 빌더 fixture
- adjust_stock - 재고 조정 헬퍼 fixture
- sku_generator - SKU 생성기 fixture
- user_with_high_points - 고액 포인트 사용자 fixture
- other_user - 다른 사용자 fixture
- paid_payment - 결제 완료 fixture
- authenticate_as - 인증 헬퍼 fixture

### shopping/tests/api/payment/conftest.py
- order_with_multiple_items - 다중 아이템 주문 fixture
- order_with_points - 포인트 사용 주문 fixture
- order_with_long_product_name - 긴 상품명 주문 fixture
- paid_order_with_payment - 결제 포함 완료 주문 fixture
- paid_payment - 결제 완료 fixture
- canceled_order - 취소된 주문 fixture
- order_with_existing_payment - 기존 결제 포함 주문 fixture
- other_user - 다른 사용자 fixture
- other_user_order - 다른 사용자 주문 fixture
- toss_client - 토스 클라이언트 fixture
- toss_success_response - 토스 성공 응답 fixture
- toss_error_response - 토스 에러 응답 fixture
- toss_cancel_response - 토스 취소 응답 fixture
- toss_webhook_data - 토스 웹훅 데이터 fixture
- mock_requests_response - 요청 응답 모의 fixture
- default_shipping_info - 기본 배송 정보 fixture
- alternative_shipping_info - 대체 배송 정보 fixture
- create_order - 주문 생성 헬퍼 fixture
- adjust_stock - 재고 조정 헬퍼 fixture
- sku_generator - SKU 생성기 fixture
- toss_response_builder - 토스 응답 빌더 fixture
- toss_cancel_response_builder - 토스 취소 응답 빌더 fixture
- build_payment_key - 결제 키 빌더 fixture
- build_confirm_request - 결제 승인 요청 빌더 fixture

### shopping/tests/api/order/conftest.py
- user_with_points - 포인트 보유 사용자 fixture
- user_with_high_points - 고액 포인트 사용자 fixture
- user_no_points - 포인트 없는 사용자 fixture
- unverified_user - 미인증 사용자 fixture
- admin_user - 관리자 사용자 fixture
- other_user - 다른 사용자 fixture
- remote_shipping_data - 도서산간 배송 정보 fixture
- invalid_shipping_data - 잘못된 배송 정보 fixture
- postal_codes - 우편번호 fixture
- low_stock_product - 저재고 상품 fixture
- inactive_product - 비활성 상품 fixture
- order_factory - 주문 팩토리 fixture
- bulk_order_factory - 대량 주문 팩토리 fixture
- order_statuses - 주문 상태 fixture
- login_helper - 로그인 헬퍼 fixture
- authenticate_as - 인증 헬퍼 fixture
- mock_payment_success - 결제 성공 모의 fixture
- mock_payment_cancel - 결제 취소 모의 fixture
- payment_confirm_context - 결제 승인 컨텍스트 fixture
- async_test_config - 비동기 테스트 설정 fixture

---

## Load Tests (load_tests/)

### load_tests/config.py
- HOST - 서버 호스트 설정
- TEST_USER_COUNT - 테스트 사용자 수 설정
- TEST_USER_PREFIX - 테스트 사용자 접두사
- TEST_USER_PASSWORD - 테스트 사용자 비밀번호
- USER_WEIGHTS - 사용자 행동 비율 (browser, shopper, buyer)
- WAIT_TIME_MIN - 최소 대기 시간
- WAIT_TIME_MAX - 최대 대기 시간
- ENDPOINTS - API 엔드포인트 설정
- SLA_TARGETS - 성능 목표 (SLA) 설정

### load_tests/locustfile.py
- Browser - 브라우징 사용자 (65% weight)
- Shopper - 장바구니 사용자 (25% weight)
- Buyer - 구매 완료 사용자 (10% weight)
- on_test_start - 테스트 시작 이벤트 핸들러
- on_test_stop - 테스트 종료 이벤트 핸들러 (통계 요약)

### load_tests/scenarios/concurrent_order.py
- OrderStats - 주문 통계 클래스
- on_test_stop - 테스트 종료 이벤트 핸들러
- ConcurrentOrderUser - 동시 주문 부하 테스트 사용자 클래스
- ConcurrentOrderUser._setup_target_products - 대상 상품 설정 메서드
- ConcurrentOrderUser.login - 로그인 메서드
- ConcurrentOrderUser.race_for_order - 주문 경쟁 태스크

### load_tests/scenarios/payment_stress.py
- PaymentStressUser - 결제 스트레스 테스트 사용자 클래스
- PaymentStressUser._fetch_product_ids - 상품 ID 조회 메서드
- PaymentStressUser.login - 로그인 메서드
- PaymentStressUser.full_payment_flow - 전체 결제 흐름 태스크
- PaymentStressUser.duplicate_payment_attempt - 중복 결제 시도 태스크

### load_tests/users/base.py
- BaseUser - 기본 부하 테스트 사용자 클래스
- BaseUser.on_start - 테스트 시작 메서드
- BaseUser._fetch_product_ids - 상품 ID 조회 메서드
- BaseUser.product_ids - 상품 ID 프로퍼티
- BaseUser.get_random_product_id - 랜덤 상품 ID 조회 메서드
- BaseUser.login - 로그인 메서드
- BaseUser.ensure_logged_in - 로그인 보장 메서드

### load_tests/users/browser.py
- BrowserUser - 브라우징 부하 테스트 사용자 클래스
- BrowserUser.browse_product_list - 상품 목록 브라우징 태스크
- BrowserUser.view_product_detail - 상품 상세 조회 태스크
- BrowserUser.search_products - 상품 검색 태스크
- BrowserUser.view_categories - 카테고리 조회 태스크
- BrowserUser.view_category_tree - 카테고리 트리 조회 태스크

### load_tests/users/buyer.py
- BuyerUser - 구매자 부하 테스트 사용자 클래스
- BuyerUser.on_start - 테스트 시작 메서드
- BuyerUser.browse_products - 상품 브라우징 태스크
- BuyerUser.complete_purchase - 구매 완료 태스크
- BuyerUser.view_orders - 주문 조회 태스크

### load_tests/users/shopper.py
- ShopperUser - 쇼핑객 부하 테스트 사용자 클래스
- ShopperUser.on_start - 테스트 시작 메서드
- ShopperUser.browse_and_view - 브라우징 및 조회 태스크
- ShopperUser.add_to_cart - 장바구니 추가 태스크
- ShopperUser.view_cart - 장바구니 조회 태스크
- ShopperUser.modify_cart - 장바구니 수정 태스크

---

## Test Factories (shopping/tests/factories.py)

### 상수
- TestConstants - 테스트 상수 클래스 (금액, 포인트, 재고, 배송 정보 등)

### User & Auth Factories
- UserFactory - 사용자 팩토리
- UserFactory.verified - 이메일 인증 완료 사용자
- UserFactory.unverified - 이메일 미인증 사용자
- UserFactory.inactive - 비활성화된 사용자
- UserFactory.withdrawn - 탈퇴한 사용자
- UserFactory.seller - 판매자 사용자
- UserFactory.admin - 관리자 사용자
- UserFactory.with_points - 포인트 보유 사용자
- UserFactory.with_high_points - 고액 포인트 사용자
- UserFactory.with_membership - 특정 등급 사용자
- UserFactory.old_unverified - 오래된 미인증 사용자
- UserFactory.recent_unverified - 최근 미인증 사용자
- UserFactory.old_verified - 오래된 인증 사용자
- EmailVerificationTokenFactory - 이메일 인증 토큰 팩토리
- EmailVerificationTokenFactory.valid - 유효한 토큰
- EmailVerificationTokenFactory.expired - 만료된 토큰
- EmailVerificationTokenFactory.used - 사용된 토큰
- EmailVerificationTokenFactory.recent - 최근 생성 토큰
- EmailVerificationTokenFactory.old_used - 오래된 사용 토큰
- EmailVerificationTokenFactory.recent_used - 최근 사용 토큰
- PasswordResetTokenFactory - 비밀번호 재설정 토큰 팩토리
- PasswordResetTokenFactory.valid - 유효한 토큰
- PasswordResetTokenFactory.expired - 만료된 토큰
- EmailLogFactory - 이메일 로그 팩토리
- EmailLogFactory.pending - 대기 상태
- EmailLogFactory.sent - 발송 완료
- EmailLogFactory.failed - 발송 실패
- EmailLogFactory.verified - 인증 완료
- EmailLogFactory.old_sent - 오래된 발송 로그
- EmailLogFactory.old_verified - 오래된 인증 로그
- EmailLogFactory.old_pending - 오래된 대기 로그
- EmailLogFactory.with_token - 토큰 연결 로그
- SocialAppFactory - 소셜 앱 팩토리
- SocialAppFactory.google - Google 소셜 앱
- SocialAppFactory.kakao - Kakao 소셜 앱
- SocialAppFactory.naver - Naver 소셜 앱
- SocialAccountFactory - 소셜 계정 팩토리
- SocialAccountFactory.google - Google 계정
- SocialAccountFactory.kakao - Kakao 계정
- SocialAccountFactory.naver - Naver 계정

### Product Factories
- CategoryFactory - 카테고리 팩토리
- ProductFactory - 상품 팩토리
- ProductFactory.active - 판매중 상품
- ProductFactory.inactive - 판매 중단 상품
- ProductFactory.out_of_stock - 품절 상품
- ProductFactory.low_stock - 저재고 상품
- ProductFactory.with_price - 특정 가격 상품
- ProductFactory.with_long_name - 긴 이름 상품
- ProductImageFactory - 상품 이미지 팩토리
- ProductImageFactory.primary - 대표 이미지
- ProductReviewFactory - 상품 리뷰 팩토리
- ProductReviewFactory.low_rating - 낮은 평점 리뷰
- ProductReviewFactory.with_rating - 특정 평점 리뷰

### Order & Payment Factories
- OrderFactory - 주문 팩토리
- OrderFactory.pending - 대기 주문
- OrderFactory.paid - 결제 완료 주문
- OrderFactory.canceled - 취소된 주문
- OrderFactory.shipped - 배송중 주문
- OrderFactory.delivered - 배송 완료 주문
- OrderFactory.with_points - 포인트 사용 주문
- OrderFactory.with_full_points - 포인트 전액 결제
- OrderFactory.with_free_shipping - 무료 배송 주문
- OrderFactory.with_remote_area - 도서산간 배송 주문
- OrderFactory.with_items - 여러 상품 포함 주문
- OrderItemFactory - 주문 상품 팩토리
- PaymentFactory - 결제 팩토리
- PaymentFactory.ready - 결제 준비 상태
- PaymentFactory.pending - 결제 대기 상태
- PaymentFactory.done - 결제 완료 상태
- PaymentFactory.done_card - 카드 결제 완료
- PaymentFactory.canceled - 결제 취소
- PaymentFactory.failed - 결제 실패
- PaymentFactory.aborted - 결제 중단
- PaidOrderFactory - 결제 완료 주문 팩토리
- CompletedPaymentFactory - 완료된 결제 팩토리
- OrderWithItemsFactory - 상품 포함 주문 팩토리

### Cart Factories
- CartFactory - 장바구니 팩토리
- CartFactory.active - 활성 장바구니
- CartFactory.inactive - 비활성 장바구니
- CartFactory.with_items - 상품 담긴 장바구니
- CartItemFactory - 장바구니 아이템 팩토리

### Point Factories
- PointHistoryFactory - 포인트 이력 팩토리
- PointHistoryFactory.earn - 포인트 적립
- PointHistoryFactory.use - 포인트 사용
- PointHistoryFactory.earn_expiring_soon - 만료 예정 적립
- PointHistoryFactory.earn_expired - 만료된 적립
- PointHistoryFactory.with_partial_usage - 부분 사용 포인트
- PointHistoryFactory.old_expire - 오래된 만료 이력
- PointHistoryFactory.recent_expire - 최근 만료 이력
- PointHistoryFactory.old_earn - 오래된 적립 이력

### Return Factories
- ReturnFactory - 교환/환불 신청 팩토리
- ReturnFactory.refund - 환불 신청
- ReturnFactory.exchange - 교환 신청
- ReturnFactory.requested - 신청 상태
- ReturnFactory.approved - 승인 상태
- ReturnFactory.rejected - 거부 상태
- ReturnFactory.shipping - 반품 배송중
- ReturnFactory.received - 반품 도착
- ReturnFactory.completed - 완료 상태
- ReturnFactory.with_items - 상품 포함 반품
- ReturnFactory.with_shipping_fee - 배송비 포함 반품
- ReturnItemFactory - 반품 상품 팩토리

### Product QA Factories
- ProductQuestionFactory - 상품 문의 팩토리
- ProductQuestionFactory.secret - 비밀글
- ProductQuestionFactory.answered - 답변 완료 문의
- ProductAnswerFactory - 상품 답변 팩토리

### Webhook Factories
- WebhookEventFactory - 웹훅 이벤트 팩토리
- WebhookEventFactory.payment_done - 결제 완료 이벤트
- WebhookEventFactory.payment_canceled - 결제 취소 이벤트
- WebhookEventFactory.payment_failed - 결제 실패 이벤트

### Data Builders
- TossResponseBuilder - Toss API 응답 빌더
- TossResponseBuilder.success_response - 결제 승인 성공 응답
- TossResponseBuilder.cancel_response - 결제 취소 성공 응답
- TossResponseBuilder.error_response - 에러 응답
- WebhookDataBuilder - 웹훅 데이터 빌더
- WebhookDataBuilder.payment_done - 결제 완료 이벤트
- WebhookDataBuilder.payment_canceled - 결제 취소 이벤트
- WebhookDataBuilder.payment_failed - 결제 실패 이벤트
- OAuthDataBuilder - OAuth 응답 빌더
- OAuthDataBuilder.google - Google OAuth 응답
- OAuthDataBuilder.kakao - Kakao OAuth 응답
- OAuthDataBuilder.naver - Naver OAuth 응답
- ShippingDataBuilder - 배송 정보 빌더
- ShippingDataBuilder.default - 기본 배송 정보
- ShippingDataBuilder.remote_area - 도서산간 배송지
- ShippingDataBuilder.invalid - 잘못된 배송 정보
- PaymentRequestBuilder - 결제 요청 데이터 빌더
- PaymentRequestBuilder.confirm_request - 결제 승인 요청 데이터

### Utilities
- SKUGenerator - SKU 생성기
- SKUGenerator.generate - SKU 생성
- SKUGenerator.reset - 카운터 리셋

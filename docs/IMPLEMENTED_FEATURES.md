# 구현된 기능 목록 (Implemented Features)

> 자동 생성일: 2025-12-04
> 최종 검증일: 2025-12-04
> 스캔 범위: 프로젝트 루트, `myproject/`, `shopping/`, `load_tests/`, `scripts/` 디렉토리 전체
> 아키텍처: 5-Layer Architecture
> 검증 상태: ✅ Models 검증 완료 | ✅ Services 검증 완료

---

# 📋 목차

1. [Business Domain Layer](#1-business-domain-layer) - 사용자 기능
2. [Application Architectural Layer](#2-application-architectural-layer) - 내부 구조
3. [Technical Capability Layer](#3-technical-capability-layer) - 엔지니어링 역량
4. [Integration Layer](#4-integration-layer) - 외부 연동
5. [Testing & Quality Layer](#5-testing--quality-layer) - QA

---

# 1. Business Domain Layer

> 사용자가 직접 사용하는 기능 (Views → User Features)

---

## 1.1 회원 관리 (User Management)

### 1.1.1 인증 (Authentication) `shopping/views/auth_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 회원가입 | `RegisterView` | POST | `/api/auth/register/` | 새 사용자 생성 + JWT 토큰 발급 + 이메일 인증 발송 |
| 로그인 | `LoginView` | POST | `/api/auth/login/` | 인증 후 JWT 토큰 발급 + 장바구니 병합 |
| 로그아웃 | `LogoutView` | POST | `/api/auth/logout/` | Refresh Token 블랙리스트 등록 + Cookie 삭제 |
| 토큰 갱신 | `CustomTokenRefreshView` | POST | `/api/auth/token/refresh/` | Access Token 재발급 (Cookie 기반) |
| 토큰 확인 | `check_token` | GET | `/api/auth/check-token/` | Access Token 유효성 확인 |

### 1.1.2 프로필 관리 (Profile) `shopping/views/user_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 프로필 조회 | `ProfileView.retrieve` | GET | `/api/profile/` | 현재 사용자 프로필 조회 |
| 프로필 전체 수정 | `ProfileView.update` | PUT | `/api/profile/` | 프로필 전체 필드 수정 |
| 프로필 부분 수정 | `ProfileView.partial_update` | PATCH | `/api/profile/` | 프로필 일부 필드 수정 |
| 비밀번호 변경 | `PasswordChangeView` | POST | `/api/profile/password/` | 현재 비밀번호 확인 후 변경 |
| 회원 탈퇴 | `withdraw` | POST | `/api/auth/withdraw/` | 비밀번호 확인 + 상태 변경 + 토큰 무효화 |

### 1.1.3 이메일 인증 (Email Verification) `shopping/views/email_verification_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 인증 메일 발송 | `SendVerificationEmailView` | POST | `/api/auth/email/send/` | 새 인증 토큰 생성 + 이메일 발송 (Celery) |
| UUID 토큰 인증 | `VerifyEmailView.get` | GET | `/api/auth/email/verify/` | 이메일 링크 클릭 시 인증 처리 |
| 6자리 코드 인증 | `VerifyEmailView.post` | POST | `/api/auth/email/verify/` | 6자리 코드 입력으로 인증 |
| 인증 메일 재발송 | `ResendVerificationEmailView` | POST | `/api/auth/email/resend/` | 1분 쿨다운 + 재발송 |
| 인증 상태 확인 | `check_verification_status` | GET | `/api/auth/email/status/` | 현재 인증 상태 조회 |

### 1.1.4 비밀번호 재설정 (Password Reset) `shopping/views/password_reset_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 재설정 요청 | `PasswordResetRequestView` | POST | `/api/auth/password/reset/request/` | 재설정 링크 이메일 발송 |
| 재설정 확인 | `PasswordResetConfirmView` | POST | `/api/auth/password/reset/confirm/` | 토큰 검증 + 새 비밀번호 설정 |

### 1.1.5 소셜 로그인 (Social Auth) `shopping/views/social_auth_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| OAuth 콜백 | `SocialCallbackView` | GET | `/api/social/{provider}/callback/` | Google/Kakao/Naver OAuth 콜백 처리 |

> ⚠️ **검증 필요**: 기존 문서에 있던 `GoogleLogin`, `KakaoLogin`, `NaverLogin`, `SocialAccountListView`, `SocialAccountDisconnectView`는 코드에서 확인되지 않음 - allauth 기반으로 변경된 것으로 보임

---

## 1.2 상품 관리 (Product Management)

### 1.2.1 상품 조회 (Product) `shopping/views/product_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 상품 목록 | `ProductViewSet.list` | GET | `/api/products/` | 검색/필터/페이지네이션 지원 |
| 상품 상세 | `ProductViewSet.retrieve` | GET | `/api/products/{id}/` | 판매자, 이미지, 리뷰 포함 |
| 상품 등록 | `ProductViewSet.create` | POST | `/api/products/` | 판매자 전용 + slug 자동 생성 |
| 상품 수정 | `ProductViewSet.update` | PUT | `/api/products/{id}/` | 판매자 본인만 |
| 상품 부분 수정 | `ProductViewSet.partial_update` | PATCH | `/api/products/{id}/` | 판매자 본인만 |
| 상품 삭제 | `ProductViewSet.destroy` | DELETE | `/api/products/{id}/` | 판매자 본인만 |
| 인기 상품 | `ProductViewSet.popular` | GET | `/api/products/popular/` | 리뷰 수 기준 상위 12개 |
| 평점 높은 상품 | `ProductViewSet.best_rating` | GET | `/api/products/best_rating/` | 평균 평점 기준 (리뷰 3개 이상) |
| 재고 부족 상품 | `ProductViewSet.low_stock` | GET | `/api/products/low_stock/` | 판매자용 재고 10개 이하 |

> ⚠️ **검증 필요**: 기존 문서의 `ProductViewSet.my_products`, `ProductViewSet.set_primary_image`는 코드에서 확인되지 않음

### 1.2.2 상품 리뷰 (Reviews) `shopping/views/product_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 리뷰 목록 | `ProductViewSet.reviews` | GET | `/api/products/{id}/reviews/` | 상품별 리뷰 조회 + 페이지네이션 |
| 리뷰 작성 | `ProductViewSet.create_review` | POST | `/api/products/{id}/reviews/` | 상품당 1개 제한 |

### 1.2.3 카테고리 (Category) `shopping/views/product_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 카테고리 목록 | `CategoryViewSet.list` | GET | `/api/categories/` | 활성 카테고리 + 상품 수 |
| 카테고리 상세 | `CategoryViewSet.retrieve` | GET | `/api/categories/{id}/` | 부모 카테고리 정보 포함 |
| 카테고리 트리 | `CategoryViewSet.tree` | GET | `/api/categories/tree/` | MPTT 계층 구조 + Redis 캐싱 |
| 카테고리별 상품 | `CategoryViewSet.products` | GET | `/api/categories/{id}/products/` | 하위 카테고리 포함 |

### 1.2.4 상품 문의 (Product Q&A) `shopping/views/product_qa_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 문의 목록 | `ProductQuestionViewSet.list` | GET | `/api/products/{id}/questions/` | 비밀글 필터링 적용 |
| 문의 상세 | `ProductQuestionViewSet.retrieve` | GET | `/api/products/{id}/questions/{qid}/` | 답변 포함 |
| 문의 작성 | `ProductQuestionViewSet.create` | POST | `/api/products/{id}/questions/` | 인증 사용자만 |
| 문의 수정 | `ProductQuestionViewSet.update` | PUT | `/api/products/{id}/questions/{qid}/` | 작성자만 + 답변 없을 때만 |
| 문의 삭제 | `ProductQuestionViewSet.destroy` | DELETE | `/api/products/{id}/questions/{qid}/` | 작성자/관리자만 |
| 답변 작성 | `ProductQuestionViewSet.answer` | POST | `/api/products/{id}/questions/{qid}/answer/` | 판매자만 |
| 답변 수정 | `ProductQuestionViewSet.update_answer` | PATCH | `/api/products/{id}/questions/{qid}/update_answer/` | 판매자/관리자만 |
| 답변 삭제 | `ProductQuestionViewSet.delete_answer` | DELETE | `/api/products/{id}/questions/{qid}/delete_answer/` | 판매자/관리자만 |
| 내 문의 목록 | `MyQuestionViewSet.list` | GET | `/api/my-questions/` | 현재 사용자 문의 |
| 내 문의 상세 | `MyQuestionViewSet.retrieve` | GET | `/api/my-questions/{id}/` | 답변 포함 |

---

## 1.3 쇼핑 기능 (Shopping Features)

### 1.3.1 장바구니 (Cart) `shopping/views/cart_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 장바구니 조회 | `CartViewSet.retrieve` | GET | `/api/cart/` | 재고/가격 변동 경고 포함 |
| 장바구니 요약 | `CartViewSet.summary` | GET | `/api/cart/summary/` | 헤더용 간단 정보 |
| 상품 추가 | `CartViewSet.add_item` | POST | `/api/cart/add_item/` | 동일 상품은 수량 증가 |
| 아이템 목록 | `CartViewSet.items` | GET | `/api/cart/items/` | 최근 추가순 정렬 |
| 수량 변경 | `CartViewSet.update_item` | PATCH | `/api/cart/{id}/items/` | 0이면 삭제 |
| 아이템 삭제 | `CartViewSet.delete_item` | DELETE | `/api/cart/{id}/items/` | 완전 제거 |
| 장바구니 비우기 | `CartViewSet.clear` | POST | `/api/cart/clear/` | confirm=true 필수 |
| 일괄 추가 | `CartViewSet.bulk_add` | POST | `/api/cart/bulk_add/` | 다중 상품 추가 |
| 재고 확인 | `CartViewSet.check_stock` | GET | `/api/cart/check_stock/` | 주문 전 확인용 |
| 구매불가 정리 | `CartViewSet.cleanup` | POST | `/api/cart/cleanup/` | 자동 정리 |
| 가격 업데이트 | `CartViewSet.update_prices` | POST | `/api/cart/update_prices/` | 현재 가격으로 갱신 |

> ⚠️ **검증 필요**: 기존 문서의 `CartItemViewSet.check_price_changes`는 `CartViewSet`에 통합됨

### 1.3.2 찜 목록 (Wishlist) `shopping/views/wishlist_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 찜 목록 조회 | `WishlistViewSet.list` | GET | `/api/wishlist/` | 필터/정렬 지원 |
| 찜 토글 | `WishlistViewSet.toggle` | POST | `/api/wishlist/toggle/` | 추가/제거 토글 |
| 찜 추가 | `WishlistViewSet.add` | POST | `/api/wishlist/add/` | 중복 시 무시 |
| 찜 제거 | `WishlistViewSet.remove` | DELETE | `/api/wishlist/remove/` | 단일 상품 제거 |
| 일괄 추가 | `WishlistViewSet.bulk_add` | POST | `/api/wishlist/bulk_add/` | 다중 상품 추가 |
| 전체 삭제 | `WishlistViewSet.clear` | DELETE | `/api/wishlist/clear/` | confirm=true 필수 |
| 찜 상태 확인 | `WishlistViewSet.check` | GET | `/api/wishlist/check/` | 단일 상품 상태 |
| 통계 조회 | `WishlistViewSet.stats` | GET | `/api/wishlist/stats/` | 가격 합계/할인 금액 등 |
| 장바구니 이동 | `WishlistViewSet.move_to_cart` | POST | `/api/wishlist/move_to_cart/` | 선택 상품 이동 |

---

## 1.4 주문 관리 (Order Management)

### 1.4.1 주문 (Orders) `shopping/views/order_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 주문 목록 | `OrderViewSet.list` | GET | `/api/orders/` | 상태별 필터 + 페이지네이션 |
| 주문 상세 | `OrderViewSet.retrieve` | GET | `/api/orders/{id}/` | 본인/관리자만 조회 |
| 주문 생성 | `OrderViewSet.create` | POST | `/api/orders/` | 이메일 인증 필수 + 비동기 처리 (202) |
| 주문 취소 | `OrderViewSet.cancel` | POST | `/api/orders/{id}/cancel/` | 배송 전만 + 포인트/재고 복구 |

> ⚠️ **검증 필요**: 기존 문서의 `OrderViewSet.my_orders`, `OrderViewSet.status`는 코드에서 별도 action으로 확인되지 않음

### 1.4.2 결제 (Payments) `shopping/views/payment_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 결제 목록 | `PaymentListView` | GET | `/api/payments/` | 상태별 필터 + 페이지네이션 |
| 결제 상세 | `PaymentDetailView` | GET | `/api/payments/{id}/` | 본인 결제만 |
| 결제 요청 | `PaymentRequestView` | POST | `/api/payments/request/` | 토스페이먼츠 결제창 호출 전 |
| 결제 승인 | `PaymentConfirmView` | POST | `/api/payments/confirm/` | 비동기 처리 (202) |
| 결제 취소 | `PaymentCancelView` | POST | `/api/payments/cancel/` | 포인트 환불/차감 처리 |
| 결제 상태 | `PaymentStatusView` | GET | `/api/payments/{id}/status/` | 폴링용 |
| 결제 실패 | `PaymentFailView` | POST | `/api/payments/fail/` | 결제창 실패/취소 처리 |

> ⚠️ **검증 필요**: 기존 문서의 `PaymentViewSet.logs`, `PaymentViewSet.confirm_async`는 별도 ViewSet이 아닌 개별 APIView로 구현됨

### 1.4.3 교환/환불 (Returns) `shopping/views/return_views.py`

**고객용 API:**

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 반품 목록 | `ReturnViewSet.list` | GET | `/api/returns/` | 상태/유형 필터 |
| 반품 상세 | `ReturnViewSet.retrieve` | GET | `/api/returns/{id}/` | 주문/상품 정보 포함 |
| 반품 신청 | `ReturnViewSet.create` | POST | `/api/returns/` | 환불/교환 신청 |
| 송장번호 입력 | `ReturnViewSet.partial_update` | PATCH | `/api/returns/{id}/` | 승인 후 입력 가능 |
| 신청 취소 | `ReturnViewSet.destroy` | DELETE | `/api/returns/{id}/` | requested 상태만 |

**판매자용 API:**

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 반품 목록 | `SellerReturnViewSet.list` | GET | `/api/seller/returns/` | 본인 상품 반품만 |
| 반품 상세 | `SellerReturnViewSet.retrieve` | GET | `/api/seller/returns/{id}/` | 신청자 정보 포함 |
| 반품 승인 | `SellerReturnViewSet.approve` | POST | `/api/seller/returns/{id}/approve/` | 반품 안내 발송 |
| 반품 거부 | `SellerReturnViewSet.reject` | POST | `/api/seller/returns/{id}/reject/` | 사유 필수 |
| 도착 확인 | `SellerReturnViewSet.confirm_receive` | POST | `/api/seller/returns/{id}/confirm-receive/` | 도착 확인 |
| 완료 처리 | `SellerReturnViewSet.complete` | POST | `/api/seller/returns/{id}/complete/` | 환불/교환 완료 |

> ⚠️ **검증 필요**: 기존 문서의 `ReturnViewSet.update_tracking`은 `partial_update`로 통합됨

---

## 1.5 포인트 & 알림 (Points & Notifications)

### 1.5.1 포인트 (Points) `shopping/views/point_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 내 포인트 | `MyPointView` | GET | `/api/points/my/` | 현재 포인트 + 최근 5건 |
| 포인트 이력 | `PointHistoryListView` | GET | `/api/points/history/` | 필터/페이지네이션 |
| 사용 가능 확인 | `PointCheckView` | POST | `/api/points/check/` | 주문 금액별 확인 |
| 만료 예정 | `ExpiringPointsView` | GET | `/api/points/expiring/` | 월별 만료 요약 |
| 포인트 통계 | `point_statistics` | GET | `/api/points/statistics/` | 종합 통계 |
| 포인트 사용 | `PointUseView` | POST | `/api/points/use/` | FIFO 방식 차감 |
| 취소 처리 | `PointCancelView` | POST | `/api/points/cancel/` | 환불/회수 처리 |

> ⚠️ **검증 필요**: 기존 문서의 `PointViewSet.list`, `PointViewSet.my_points`, `PointViewSet.summary`, `PointViewSet.expiring_soon`은 개별 APIView로 구현됨

### 1.5.2 알림 (Notifications) `shopping/views/notification_views.py`

| 기능 | View/Function | 메서드 | 엔드포인트 | 설명 |
|-----|--------------|--------|-----------|------|
| 알림 목록 | `NotificationViewSet.list` | GET | `/api/notifications/` | 전체 알림 |
| 알림 상세 | `NotificationViewSet.retrieve` | GET | `/api/notifications/{id}/` | 조회 시 자동 읽음 |
| 읽지 않은 알림 | `NotificationViewSet.unread` | GET | `/api/notifications/unread/` | 개수 + 최근 5개 |
| 읽음 처리 | `NotificationViewSet.mark_read` | POST | `/api/notifications/mark_read/` | 선택/전체 처리 |
| 읽은 알림 삭제 | `NotificationViewSet.clear` | DELETE | `/api/notifications/clear/` | 읽은 알림만 |

> ⚠️ **검증 필요**: 기존 문서의 `NotificationViewSet.destroy`, `NotificationViewSet.mark_all_read`, `NotificationViewSet.unread_count`는 다른 액션으로 통합됨

---

## 1.6 View Mixins `shopping/views/mixins.py`

| Mixin | 메서드 | 설명 |
|-------|--------|------|
| `EmailVerificationRequiredMixin` | `check_email_verification()` | 이메일 인증 필요 기능에서 사용 (결제/주문 등)

---

# 2. Application Architectural Layer

> 내부 구조 (Models, Services, Serializers, DTOs)

---

## 2.1 Domain Models `shopping/models/`

### 2.1.1 사용자 도메인 (User Domain)

**User** `shopping/models/user.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `email` | EmailField | 고유 이메일 (unique) |
| `phone_number` | CharField | 전화번호 (000-0000-0000 형식) |
| `birth_date` | DateField | 생년월일 |
| `postal_code`, `address`, `address_detail` | CharField | 배송지 주소 |
| `is_email_verified` | BooleanField | 이메일 인증 여부 |
| `is_phone_verified` | BooleanField | 휴대폰 인증 여부 |
| `agree_marketing_email`, `agree_marketing_sms` | BooleanField | 마케팅 수신 동의 |
| `membership_level` | CharField | 등급 (bronze/silver/gold/vip) |
| `points` | PositiveIntegerField | 보유 포인트 |
| `is_seller` | BooleanField | 판매자 여부 |
| `is_withdrawn`, `withdrawn_at` | Boolean/DateTime | 탈퇴 상태 |
| `wishlist_products` | M2M(Product) | 찜한 상품 |
| `get_full_address()` | method | 전체 주소 반환 |
| `get_earn_rate()` | method | 등급별 포인트 적립률 반환 |
| `is_vip` | property | VIP 여부 |
| `add_to_wishlist()` | method | 찜 추가 |
| `is_in_wishlist()` | method | 찜 여부 확인 |
| `get_wishlist_count()` | method | 찜 개수 |
| `clear_wishlist()` | method | 찜 전체 삭제 |
| `remove_from_wishlist()` | method | 찜 제거 |

> ⚠️ **기존 문서 오류 확인**: 다음 메서드들은 User 모델에 **존재하지 않음**
> - `get_total_orders`, `get_total_spent`, `get_point_balance`
> - `add_points`, `use_points` (PointService에서 처리)
> - `update_membership_level`, `check_membership_upgrade`
> - `get_available_coupons_count` (쿠폰 기능 미구현)
> - `withdraw`, `restore` (UserService에서 처리)
> - `UserManager.create_user`, `UserManager.create_superuser` (기본 AbstractUser 사용)
> - `SellerProfile` 모델 (`shopping/models/seller.py`에 별도 존재)

---

### 2.1.2 상품 도메인 (Product Domain)

**Category** `shopping/models/product.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `name`, `slug` | CharField | 카테고리명, URL slug |
| `parent` | TreeForeignKey | 상위 카테고리 (MPTT) |
| `description` | TextField | 설명 |
| `is_active` | BooleanField | 활성화 여부 |
| `get_full_path()` | method | 전체 경로 (전자제품 > 컴퓨터) |
| `get_all_products()` | method | 하위 포함 모든 상품 |
| `product_count` | property | 활성 상품 수 |
| `total_product_count` | property | 하위 포함 전체 상품 수 |

**Product** `shopping/models/product.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `name`, `slug` | CharField | 상품명, URL slug |
| `category` | FK(Category) | 카테고리 |
| `description`, `short_description` | TextField/CharField | 설명 |
| `price`, `compare_price` | DecimalField | 판매가, 할인 전 가격 |
| `stock` | PositiveIntegerField | 재고 수량 |
| `is_available`, `is_active` | BooleanField | 판매 가능, 활성화 |
| `sku` | CharField | 재고관리코드 (unique) |
| `brand`, `tags` | CharField | 브랜드, 태그 |
| `seller` | FK(User) | 판매자 |
| `view_count`, `sold_count` | PositiveIntegerField | 조회수, 판매량 |
| `is_on_sale` | property | 할인 중 여부 |
| `discount_percentage` | property | 할인율 |
| `is_in_stock` | property | 재고 여부 |
| `can_purchase(quantity)` | method | 구매 가능 여부 |
| `stock_status` | property | 재고 상태 문자열 |
| `get_wishlist_count()` | method | 찜 수 |
| `is_wished_by(user)` | method | 특정 유저 찜 여부 |
| `get_wishlist_users()` | method | 찜한 사용자 목록 |
| `wishlist_count` | property | 찜 개수 (property) |
| `wishlist_count_display()` | method | 찜 개수 포맷팅 표시 |

> ⚠️ **기존 문서 오류 확인**: 다음 메서드들은 Product 모델에 **존재하지 않음**
> - `average_rating` (View에서 annotate로 계산)
> - `increase_stock`, `decrease_stock` (직접 stock 필드 조작)

**ProductImage** `shopping/models/product.py`

| 필드 | 타입 | 설명 |
|------|------|------|
| `product` | FK(Product) | 상품 |
| `image` | ImageField | 이미지 파일 |
| `alt_text` | CharField | 대체 텍스트 |
| `is_primary` | BooleanField | 대표 이미지 (UniqueConstraint) |
| `order` | PositiveIntegerField | 표시 순서 |

**ProductReview** `shopping/models/product.py`

| 필드 | 타입 | 설명 |
|------|------|------|
| `product` | FK(Product) | 상품 |
| `user` | FK(User) | 작성자 |
| `rating` | IntegerField | 평점 (1-5) |
| `comment` | TextField | 리뷰 내용 |
| UniqueConstraint | - | 1상품 1리뷰 제한 |

> ⚠️ **기존 문서 오류 확인**: `ProductReview.can_edit`, `ProductReview.can_delete` 메서드 **존재하지 않음**

---

### 2.1.3 장바구니 도메인 (Cart Domain) `shopping/models/cart.py`

**Cart**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 (회원 장바구니) |
| `session_key` | CharField | 세션 키 (비회원 장바구니) |
| `is_active` | BooleanField | 활성 상태 |
| `get_total_amount()` | method | 장바구니 총 금액 계산 |
| `get_total_quantity()` | method | 장바구니 총 수량 |
| `clear()` | method | 장바구니 비우기 |
| `deactivate()` | method | 장바구니 비활성화 |
| `get_or_create_active_cart()` | classmethod | 활성 장바구니 조회/생성 |
| `merge_anonymous_cart()` | classmethod | 비회원 장바구니 병합 |

**CartItem**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `cart` | FK(Cart) | 장바구니 |
| `product` | FK(Product) | 상품 |
| `quantity` | PositiveIntegerField | 수량 |
| `price_at_add` | DecimalField | 담은 시점 가격 |
| `subtotal` | property | 소계 (현재가격 x 수량) |
| `is_price_changed` | property | 가격 변경 여부 |
| `price_difference` | property | 가격 차이 |
| `increase_quantity()` | method | 수량 증가 |
| `decrease_quantity()` | method | 수량 감소 |
| `update_quantity()` | method | 수량 직접 설정 |
| `is_available()` | method | 구매 가능 여부 |

---

### 2.1.4 주문 도메인 (Order Domain) `shopping/models/order.py`

**Order**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 주문자 |
| `status` | CharField | 주문 상태 (pending/confirmed/paid/preparing/shipped/delivered/canceled/refunded) |
| `failure_reason` | TextField | 실패 사유 |
| `shipping_*` | Fields | 배송 정보 (name, phone, postal_code, address, address_detail) |
| `order_memo` | TextField | 주문 메모 |
| `payment_method` | CharField | 결제 방법 |
| `total_amount` | DecimalField | 총 주문금액 |
| `used_points` | PositiveIntegerField | 사용 포인트 |
| `final_amount` | DecimalField | 최종 결제금액 |
| `earned_points` | PositiveIntegerField | 적립 포인트 |
| `earn_rate_at_order` | PositiveSmallIntegerField | 주문 시점 적립률 |
| `membership_at_order` | CharField | 주문 시점 회원등급 |
| `shipping_fee`, `additional_shipping_fee` | DecimalField | 배송비 |
| `is_free_shipping` | BooleanField | 무료배송 여부 |
| `order_number` | CharField | 주문번호 (auto) |
| `get_full_shipping_address` | property | 전체 배송 주소 |
| `is_paid` | property | 결제 완료 여부 |
| `can_cancel` | property | 취소 가능 여부 |
| `get_total_shipping_fee()` | method | 전체 배송비 |
| `payment_method_display` | property | 결제 방법 표시용 |

**OrderItem**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `order` | FK(Order) | 주문 |
| `product` | FK(Product) | 상품 |
| `product_name` | CharField | 상품명 (주문 당시) |
| `quantity` | PositiveIntegerField | 수량 |
| `price` | DecimalField | 단가 (주문 당시) |
| `get_subtotal()` | method | 소계 계산 |

---

### 2.1.5 결제 도메인 (Payment Domain) `shopping/models/payment.py`

**Payment**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `order` | OneToOneField(Order) | 주문 (1:1) |
| `payment_key` | CharField | 토스 결제키 |
| `toss_order_id` | CharField | 토스 주문번호 |
| `idempotency_key` | CharField | 멱등성 키 |
| `amount` | DecimalField | 결제 금액 |
| `method` | CharField | 결제 수단 |
| `card_company`, `card_number` | CharField | 카드 정보 |
| `installment_plan_months` | IntegerField | 할부 개월수 |
| `status` | CharField | 결제 상태 (ready/in_progress/waiting_for_deposit/done/canceled/partial_canceled/aborted/expired) |
| `approved_at` | DateTimeField | 승인 일시 |
| `receipt_url` | URLField | 영수증 URL |
| `is_canceled` | BooleanField | 취소 여부 |
| `canceled_amount` | DecimalField | 취소 금액 |
| `cancel_reason` | TextField | 취소 사유 |
| `canceled_at` | DateTimeField | 취소 일시 |
| `raw_response` | JSONField | 토스 응답 원본 |
| `fail_reason` | TextField | 실패 사유 |
| `is_paid` | property | 결제 완료 여부 |
| `can_cancel` | property | 취소 가능 여부 |
| `mark_as_paid()` | method | 결제 완료 처리 |
| `mark_as_failed()` | method | 결제 실패 처리 |
| `mark_as_canceled()` | method | 결제 취소 처리 |
| `mark_as_partial_canceled()` | method | 부분 취소 처리 (TODO) |
| `sanitize_raw_response()` | method | 민감 정보 제거 |

**PaymentLog**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `payment` | FK(Payment) | 결제 |
| `log_type` | CharField | 로그 타입 (request/approve/cancel/webhook/error) |
| `message` | TextField | 로그 메시지 |
| `data` | JSONField | 추가 데이터 |

---

### 2.1.6 포인트 도메인 (Point Domain) `shopping/models/point.py`

**PointHistoryManager**

| 메서드 | 설명 |
|--------|------|
| `get_total_earned()` | 총 적립 포인트 |
| `get_total_used()` | 총 사용 포인트 |
| `get_expiring_soon()` | 만료 예정 포인트 |
| `get_month_statistics()` | 월별 통계 |
| `optimized_for_list()` | 목록 조회 최적화 쿼리셋 |

**PointHistory**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 |
| `points` | IntegerField | 포인트 변동량 (양수=적립, 음수=사용) |
| `balance` | PositiveIntegerField | 변경 후 잔액 |
| `type` | CharField | 타입 (earn/use/cancel_refund/cancel_deduct/expire/admin_add/admin_deduct/event) |
| `order` | FK(Order) | 관련 주문 |
| `description` | CharField | 설명 |
| `expires_at` | DateTimeField | 만료일시 |
| `metadata` | JSONField | 메타데이터 |
| `create_history()` | classmethod | 포인트 이력 생성 헬퍼 |
| `get_user_balance()` | classmethod | 사용자 잔액 조회 |
| `get_expiring_points()` | classmethod | 만료 예정 포인트 조회 |

> ⚠️ **설계 특이사항**: 원장(Ledger) 무결성을 위해 `delete()` 및 핵심 필드 수정 금지

---

### 2.1.7 인증 도메인 (Auth Domain)

**EmailVerificationToken** `shopping/models/email_verification.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 |
| `token` | UUIDField | UUID 인증 토큰 (링크용) |
| `verification_code` | CharField | 6자리 인증 코드 (직접 입력용) |
| `is_used` | BooleanField | 사용 여부 |
| `used_at` | DateTimeField | 사용일시 |
| `generate_verification_code()` | method | 6자리 코드 생성 |
| `is_expired()` | method | 만료 여부 (24시간) |
| `mark_as_used()` | method | 사용 완료 처리 |
| `can_resend()` | method | 재발송 가능 여부 (1분 제한) |

**EmailLog** `shopping/models/email_verification.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 |
| `email_type` | CharField | 이메일 유형 |
| `recipient_email` | EmailField | 수신자 이메일 |
| `subject` | CharField | 제목 |
| `status` | CharField | 상태 (pending/sent/failed/opened/clicked/verified) |
| `token` | FK(EmailVerificationToken) | 연결된 인증 토큰 |
| `mark_as_sent()` | method | 발송 완료 처리 |
| `mark_as_failed()` | method | 발송 실패 처리 |
| `mark_as_verified()` | method | 인증 완료 처리 |

**PasswordResetToken** `shopping/models/password_reset.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 |
| `token_hash` | CharField | 토큰 해시 (SHA-256) |
| `is_used` | BooleanField | 사용 여부 |
| `used_at` | DateTimeField | 사용일시 |
| `generate_token()` | classmethod | 새 토큰 생성 (원본 반환, 해시만 저장) |
| `invalidate_previous_tokens()` | classmethod | 이전 토큰 무효화 |
| `verify_token()` | classmethod | 토큰 검증 |
| `is_expired()` | method | 만료 여부 |
| `mark_as_used()` | method | 사용 완료 처리 |

> ⚠️ **보안 특이사항**: 토큰 원본은 저장하지 않고 SHA-256 해시만 저장

---

### 2.1.8 알림 도메인 (Notification Domain) `shopping/models/notification.py`

**Notification**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | FK(User) | 사용자 |
| `notification_type` | CharField | 알림 타입 (qa_answer/order_status/point_earned/point_expiring/review_reply/return) |
| `title` | CharField | 제목 |
| `message` | TextField | 내용 |
| `link` | CharField | 클릭 시 이동 URL |
| `metadata` | JSONField | 추가 메타데이터 |
| `is_read` | BooleanField | 읽음 여부 |
| `read_at` | DateTimeField | 읽은 시간 |
| `mark_as_read()` | method | 읽음 처리 |

---

### 2.1.9 상품 문의 도메인 (Product Q&A Domain) `shopping/models/product_qa.py`

**ProductQuestion**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `product` | FK(Product) | 상품 |
| `user` | FK(User) | 작성자 |
| `title` | CharField | 제목 |
| `content` | TextField | 문의 내용 |
| `is_secret` | BooleanField | 비밀글 여부 |
| `is_answered` | BooleanField | 답변 완료 여부 |
| `can_view()` | method | 조회 권한 확인 |

**ProductAnswer**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `question` | OneToOneField(ProductQuestion) | 문의 (1:1) |
| `seller` | FK(User) | 답변자 (판매자) |
| `content` | TextField | 답변 내용 |

---

### 2.1.10 교환/환불 도메인 (Return Domain) `shopping/models/return_request.py`

**Return**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `order` | FK(Order) | 원본 주문 |
| `user` | FK(User) | 신청자 |
| `return_number` | CharField | 교환/환불 번호 (auto) |
| `type` | CharField | 타입 (refund/exchange) |
| `status` | CharField | 처리 상태 (requested/approved/rejected/shipping/received/completed) |
| `reason` | CharField | 사유 |
| `reason_detail` | TextField | 상세 사유 |
| `return_shipping_*` | Fields | 반품 배송 정보 |
| `refund_*` | Fields | 환불 정보 (amount, method, account) |
| `exchange_*` | Fields | 교환 정보 (product, shipping) |
| `admin_memo` | TextField | 관리자 메모 |
| `rejected_reason` | TextField | 거부 사유 |
| `get_decrypted_account_number()` | method | 계좌번호 복호화 |
| `get_masked_account_number()` | method | 계좌번호 마스킹 |
| `can_request_for_order()` | classmethod | 신청 가능 여부 확인 |
| `approve()` | method | 승인 처리 (→ ReturnService) |
| `reject()` | method | 거부 처리 (→ ReturnService) |
| `confirm_receive()` | method | 반품 도착 확인 (→ ReturnService) |
| `complete_refund()` | method | 환불 완료 (→ ReturnService) |
| `complete_exchange()` | method | 교환 완료 (→ ReturnService) |

> ⚠️ **보안 특이사항**: 계좌번호는 Fernet 대칭 암호화로 저장

**ReturnItem**

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `return_request` | FK(Return) | 교환/환불 신청 |
| `order_item` | FK(OrderItem) | 주문 상품 |
| `quantity` | PositiveIntegerField | 반품 수량 |
| `product_name` | CharField | 상품명 (스냅샷) |
| `product_price` | DecimalField | 단가 (스냅샷) |
| `get_subtotal()` | method | 반품 금액 계산 |

---

### 2.1.11 기타 도메인

**SellerProfile** `shopping/models/seller.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `user` | OneToOneField(User) | 사용자 (1:1) |
| `store_name` | CharField | 스토어명 |
| `store_description` | TextField | 스토어 소개 |
| `business_number` | CharField | 사업자등록번호 |
| `representative_name` | CharField | 대표자명 |
| `business_address` | CharField | 사업장 주소 |
| `bank_name`, `bank_account`, `bank_holder` | CharField | 정산 정보 |

> ⚠️ **제약사항**: `User.is_seller=True`인 사용자만 SellerProfile 생성 가능

**WebhookEvent** `shopping/models/webhook_event.py`

| 필드/메서드 | 타입 | 설명 |
|------------|------|------|
| `event_id` | CharField | 이벤트 ID |
| `event_type` | CharField | 이벤트 타입 (PAYMENT.DONE, PAYMENT.CANCELED 등) |
| `source` | CharField | 소스 (toss, kakao 등) |
| `order_id` | CharField | 주문 ID |
| `processed_at` | DateTimeField | 처리 시간 |

> ⚠️ **용도**: 순수 감사 로그 목적 (중복 방지는 Redis TTL 사용)

---

## 2.2 Services (비즈니스 로직) `shopping/services/`

### 2.2.1 사용자 서비스

**UserService** `shopping/services/user_service.py`

| 메서드 | 설명 |
|--------|------|
| `send_verification_email()` | 인증 이메일 발송 |
| `create_tokens_for_user()` | JWT 토큰 생성 |
| `register_user()` | 사용자 등록 후처리 |
| `login_user()` | 로그인 처리 (장바구니 병합 등) |
| `withdraw_user()` | 사용자 탈퇴 처리 |
| `process_social_login()` | 소셜 로그인 처리 |

**TokenService** `shopping/services/token_service.py`

| 메서드 | 설명 |
|--------|------|
| `validate_and_refresh_token()` | 토큰 검증 및 갱신 |
| `blacklist_token()` | 토큰 블랙리스트 등록 |

**EmailVerificationService** `shopping/services/email_verification_service.py`

| 메서드 | 설명 |
|--------|------|
| `send_verification_email()` | 인증 이메일 발송 |
| `verify_by_token()` | UUID 토큰으로 인증 |
| `verify_by_code()` | 6자리 코드로 인증 |
| `get_verification_status()` | 인증 상태 조회 |
| `invalidate_tokens()` | 토큰 무효화 |
| `get_active_token()` | 활성 토큰 조회 |

**PasswordResetService** `shopping/services/password_reset_service.py`

| 메서드 | 설명 |
|--------|------|
| `request_password_reset()` | 비밀번호 재설정 요청 |
| `confirm_password_reset()` | 비밀번호 재설정 확인 |

**SocialAuthService** `shopping/services/social_auth_service.py`

| 메서드 | 설명 |
|--------|------|
| `get_callback_uri()` | 콜백 URI 조회 |
| `get_supported_providers()` | 지원 OAuth 제공자 목록 |
| `is_supported_provider()` | OAuth 제공자 지원 여부 |
| `exchange_code_for_token()` | 인가 코드로 토큰 교환 |
| `get_user_info()` | 사용자 정보 조회 |
| `normalize_user_info()` | 사용자 정보 정규화 |
| `process_oauth_callback()` | OAuth 콜백 처리 |

---

### 2.2.2 상품 서비스

**ProductService** `shopping/services/product_service.py`

| 메서드 | 설명 |
|--------|------|
| `set_primary_image()` | 대표 이미지 설정 |

**ProductQAService** `shopping/services/product_qa_service.py`

| 메서드 | 설명 |
|--------|------|
| `create_answer()` | 문의 답변 생성 |

---

### 2.2.3 쇼핑 서비스

**CartService** `shopping/services/cart_service.py`

| 메서드 | 설명 |
|--------|------|
| `get_or_create_cart()` | 장바구니 조회/생성 |
| `add_item()` | 아이템 추가 |
| `update_item_quantity()` | 수량 변경 |
| `remove_item()` | 아이템 삭제 |
| `clear_cart()` | 장바구니 비우기 |
| `bulk_add_items()` | 일괄 추가 |
| `check_stock()` | 재고 확인 |
| `check_price_changes()` | 가격 변동 확인 |
| `update_item_prices()` | 가격 업데이트 |
| `merge_anonymous_cart()` | 익명 장바구니 병합 |
| `cleanup_unavailable_items()` | 구매 불가 아이템 정리 |

**WishlistService** `shopping/services/wishlist_service.py`

| 메서드 | 설명 |
|--------|------|
| `toggle()` | 찜 토글 |
| `add()` | 찜 추가 |
| `remove()` | 찜 삭제 |
| `bulk_add()` | 일괄 찜하기 |
| `clear()` | 찜 전체 삭제 |
| `check()` | 찜 상태 확인 |
| `get_list()` | 찜 목록 조회 |
| `get_stats()` | 찜 통계 조회 |
| `move_to_cart()` | 장바구니로 이동 |

---

### 2.2.4 주문/결제 서비스

**OrderService** `shopping/services/order_service.py`

| 메서드 | 설명 |
|--------|------|
| `create_order_from_cart()` | 장바구니에서 주문 생성 |
| `create_order_hybrid()` | 하이브리드 방식 주문 생성 (비동기) |
| `cancel_order()` | 주문 취소 |

**PaymentService** `shopping/services/payment_service.py`

| 메서드 | 설명 |
|--------|------|
| `create_payment()` | 결제 정보 생성 |
| `confirm_payment_sync()` | 동기 결제 승인 |
| `confirm_payment_async()` | 비동기 결제 승인 |
| `cancel_payment()` | 결제 취소 |

**ShippingService** `shopping/services/shipping_service.py`

| 메서드 | 설명 |
|--------|------|
| `calculate_fee()` | 배송비 계산 |
| `is_remote_area()` | 도서산간 지역 확인 |

**ReturnService** `shopping/services/return_service.py`

| 메서드 | 설명 |
|--------|------|
| `validate_order_for_return()` | 반품 가능 주문 검증 |
| `validate_return_items()` | 반품 상품 검증 |
| `generate_return_number()` | 반품번호 생성 |
| `calculate_refund_amount()` | 환불 금액 계산 |
| `create_return()` | 반품 신청 생성 |
| `approve_return()` | 반품 승인 |
| `reject_return()` | 반품 거부 |
| `confirm_receive_return()` | 반품 도착 확인 |
| `complete_refund()` | 환불 완료 처리 |
| `complete_exchange()` | 교환 완료 처리 |

---

### 2.2.5 포인트/알림 서비스

**PointService** `shopping/services/point_service.py`

| 메서드 | 설명 |
|--------|------|
| `add_points()` | 포인트 적립 |
| `use_points()` | 포인트 사용 |
| `get_expired_points()` | 만료된 포인트 조회 |
| `get_expiring_points_soon()` | 만료 예정 포인트 조회 |
| `expire_points()` | 포인트 만료 처리 |
| `get_remaining_points()` | 남은 포인트 계산 |
| `get_usable_points()` | 사용 가능 포인트 조회 |
| `use_points_fifo()` | FIFO 방식 포인트 사용 |
| `send_expiry_notifications()` | 만료 예정 알림 발송 |

**PointQueryService** `shopping/services/point_query_service.py`

| 메서드 | 설명 |
|--------|------|
| `get_filtered_history()` | 필터링된 포인트 이력 조회 |
| `get_history_summary()` | 포인트 이력 요약 조회 |
| `get_monthly_expiring_summary()` | 월별 만료 예정 요약 |
| `get_point_statistics()` | 포인트 통계 조회 |
| `get_recent_histories()` | 최근 이력 조회 |
| `build_filter_from_request()` | 요청에서 필터 생성 |

**NotificationService** `shopping/services/notification_service.py`

| 메서드 | 설명 |
|--------|------|
| `get_queryset()` | 알림 쿼리셋 조회 |
| `get_unread()` | 읽지 않은 알림 조회 |
| `get_by_id()` | ID로 알림 조회 |
| `mark_as_read()` | 알림 읽음 처리 |
| `mark_single_as_read()` | 단일 알림 읽음 처리 |
| `clear_read()` | 읽은 알림 전체 삭제 |
| `delete_by_id()` | ID로 알림 삭제 |
| `create()` | 알림 생성 |
| `bulk_create()` | 알림 일괄 생성 |
| `get_unread_count()` | 읽지 않은 알림 개수 조회 |

---

### 2.2.6 외부 연동 서비스

**TossWebhookService** `shopping/services/toss_webhook_service.py`

| 메서드 | 설명 |
|--------|------|
| `is_webhook_duplicate()` | 웹훅 중복 확인 |
| `mark_webhook_processed()` | 웹훅 처리 완료 표시 |
| `log_webhook_event()` | 웹훅 이벤트 로깅 |
| `handle_payment_done()` | 결제 완료 처리 |
| `handle_payment_canceled()` | 결제 취소 처리 |
| `handle_payment_failed()` | 결제 실패 처리 |

---

### 2.2.7 Base Service

**ServiceError** `shopping/services/base.py`

| 항목 | 설명 |
|------|------|
| `ServiceError` | 서비스 에러 베이스 클래스 |
| `@log_service_call` | 서비스 호출 로깅 데코레이터 |

---

## 2.3 Serializers `shopping/serializers/`

### 2.3.1 사용자 관련

| Serializer | 파일 | 용도 |
|------------|------|------|
| `UserListSerializer` | user_serializers.py | 사용자 목록 조회 |
| `UserSerializer` | user_serializers.py | 프로필 조회/수정 |
| `RegisterSerializer` | user_serializers.py | 회원가입 |
| `LoginSerializer` | user_serializers.py | 로그인 |
| `PasswordChangeSerializer` | user_serializers.py | 비밀번호 변경 |
| `TokenResponseSerializer` | user_serializers.py | 토큰 응답 |

### 2.3.2 이메일/비밀번호 인증

| Serializer | 파일 | 용도 |
|------------|------|------|
| `EmailVerificationTokenSerializer` | email_verification_serializers.py | Admin 전용 |
| `SendVerificationEmailSerializer` | email_verification_serializers.py | 이메일 발송 요청 |
| `VerifyEmailByTokenSerializer` | email_verification_serializers.py | UUID 토큰 인증 |
| `VerifyEmailByCodeSerializer` | email_verification_serializers.py | 6자리 코드 인증 |
| `ResendVerificationEmailSerializer` | email_verification_serializers.py | 이메일 재발송 |
| `EmailLogSerializer` | email_verification_serializers.py | Admin 전용 |
| `PasswordResetRequestSerializer` | password_reset_serializers.py | 재설정 요청 |
| `PasswordResetConfirmSerializer` | password_reset_serializers.py | 재설정 확인 |

### 2.3.3 상품 관련

| Serializer | 파일 | 용도 |
|------------|------|------|
| `CategorySerializer` | category_serializers.py | 카테고리 기본 |
| `CategoryTreeSerializer` | category_serializers.py | 트리 구조 |
| `CategoryCreateUpdateSerializer` | category_serializers.py | 생성/수정 |
| `SimpleCategorySerializer` | category_serializers.py | 간단 정보 |
| `ProductListSerializer` | product_serializers.py | 상품 목록 |
| `ProductImageSerializer` | product_serializers.py | 상품 이미지 |
| `ProductReviewSerializer` | product_serializers.py | 상품 리뷰 |
| `ProductDetailSerializer` | product_serializers.py | 상품 상세 |
| `ProductCreateUpdateSerializer` | product_serializers.py | 상품 생성/수정 |
| `AverageRatingField` | product_serializers.py | 평균 평점 필드 |

### 2.3.4 상품 문의

| Serializer | 파일 | 용도 |
|------------|------|------|
| `ProductQuestionBaseSerializer` | product_qa_serializers.py | 문의 베이스 검증 |
| `ProductAnswerSerializer` | product_qa_serializers.py | 답변 조회 |
| `ProductQuestionListSerializer` | product_qa_serializers.py | 문의 목록 |
| `ProductQuestionDetailSerializer` | product_qa_serializers.py | 문의 상세 |
| `ProductQuestionCreateSerializer` | product_qa_serializers.py | 문의 작성 |
| `ProductQuestionUpdateSerializer` | product_qa_serializers.py | 문의 수정 |
| `ProductAnswerCreateSerializer` | product_qa_serializers.py | 답변 작성 |
| `ProductAnswerUpdateSerializer` | product_qa_serializers.py | 답변 수정 |

### 2.3.5 장바구니/찜

| Serializer | 파일 | 용도 |
|------------|------|------|
| `CartItemSerializer` | cart_serializers.py | 아이템 조회 |
| `CartItemCreateSerializer` | cart_serializers.py | 아이템 추가 |
| `CartItemUpdateSerializer` | cart_serializers.py | 수량 변경 |
| `CartSerializer` | cart_serializers.py | 전체 정보 |
| `SimpleCartSerializer` | cart_serializers.py | 요약 정보 |
| `CartClearSerializer` | cart_serializers.py | 비우기 확인 |
| `WishlistProductSerializer` | wishlist_serializers.py | 찜 상품 정보 |
| `WishlistToggleSerializer` | wishlist_serializers.py | 찜 토글 |
| `WishlistBulkAddSerializer` | wishlist_serializers.py | 일괄 찜하기 |
| `WishlistStatusSerializer` | wishlist_serializers.py | 찜 상태 |
| `WishlistStatsSerializer` | wishlist_serializers.py | 찜 통계 |

### 2.3.6 주문/결제

| Serializer | 파일 | 용도 |
|------------|------|------|
| `OrderItemSerializer` | order_serializers.py | 주문 상품 |
| `OrderListSerializer` | order_serializers.py | 주문 목록 |
| `OrderDetailSerializer` | order_serializers.py | 주문 상세 |
| `OrderCreateSerializer` | order_serializers.py | 주문 생성 |
| `TotalShippingFeeMixin` | order_serializers.py | 배송비 계산 |
| `PaymentSerializer` | payment_serializers.py | 결제 정보 |
| `PaymentRequestSerializer` | payment_serializers.py | 결제 요청 |
| `PaymentConfirmSerializer` | payment_serializers.py | 결제 승인 |
| `PaymentCancelSerializer` | payment_serializers.py | 결제 취소 |
| `PaymentLogSerializer` | payment_serializers.py | 결제 로그 |
| `PaymentWebhookSerializer` | payment_serializers.py | 웹훅 |
| `PaymentFailSerializer` | payment_serializers.py | 결제 실패 |

### 2.3.7 교환/환불

| Serializer | 파일 | 용도 |
|------------|------|------|
| `ReturnItemSerializer` | return_serializers.py | 반품 상품 |
| `ReturnCreateSerializer` | return_serializers.py | 신청 |
| `ReturnListSerializer` | return_serializers.py | 목록 |
| `ReturnDetailSerializer` | return_serializers.py | 상세 |
| `ReturnUpdateSerializer` | return_serializers.py | 송장번호 |
| `ReturnApproveSerializer` | return_serializers.py | 승인 |
| `ReturnRejectSerializer` | return_serializers.py | 거부 |
| `ReturnConfirmReceiveSerializer` | return_serializers.py | 도착 확인 |
| `ReturnCompleteSerializer` | return_serializers.py | 완료 처리 |

### 2.3.8 포인트/알림

| Serializer | 파일 | 용도 |
|------------|------|------|
| `PointHistorySerializer` | point_serializers.py | 포인트 이력 |
| `UserPointSerializer` | point_serializers.py | 사용자 포인트 |
| `PointUseSerializer` | point_serializers.py | 포인트 사용 |
| `PointCancelSerializer` | point_serializers.py | 취소/환불 회수 |
| `PointCheckSerializer` | point_serializers.py | 사용 가능 확인 |
| `NotificationListSerializer` | notification_serializers.py | 알림 목록 |
| `NotificationSerializer` | notification_serializers.py | 알림 상세 |
| `NotificationMarkReadSerializer` | notification_serializers.py | 읽음 처리 |

### 2.3.9 소셜 로그인

| Serializer | 파일 | 용도 |
|------------|------|------|
| `CustomSocialLoginSerializer` | social_auth_serializers.py | 소셜 로그인 커스텀 |
| `SocialAccountSerializer` | social_auth_serializers.py | 소셜 계정 정보 |

---

## 2.4 DTOs `shopping/dtos/`

**ProductFilterParams** `shopping/dtos/product_filter.py`

| 필드 | 설명 |
|------|------|
| `category_id` | 카테고리 ID |
| `min_price`, `max_price` | 가격 범위 |
| `in_stock` | 재고 여부 |
| `seller_id` | 판매자 ID |
| `from_request()` | 요청에서 필터 생성 (classmethod)

---

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
| `add_points_after_payment` | 결제 후 포인트 적립 |
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
| `TossPaymentClient.confirm_payment()` | 결제 승인 요청 |
| `TossPaymentClient.cancel_payment()` | 결제 취소 요청 |
| `TossPaymentClient.get_payment()` | 결제 정보 조회 |
| `TossPaymentClient.verify_webhook()` | 웹훅 서명 검증 |
| `TossPaymentClient.create_billing_key()` | 빌링키 생성 |
| `TossPaymentError` | 토스 결제 에러 클래스 |
| `get_error_message()` | 에러 메시지 조회 |

**spectacular_hooks.py**

| 함수 | 설명 |
|------|------|
| `preprocess_exclude_endpoints()` | 특정 엔드포인트 제외 |
| `postprocess_tags()` | 태그 정리 후처리 |
| `TAG_MAPPING` | 태그 변환 맵핑 |

---

## 3.5 권한 & 스로틀링

### 3.5.1 권한 `shopping/permissions.py`

| 클래스 | 설명 |
|--------|------|
| `IsSeller` | 판매자 권한 확인 |
| `IsSellerAndOwner` | 판매자이면서 소유자 권한 |
| `IsSellerAndProductOwner` | 판매자이면서 상품 소유자 권한 |
| `IsSellerOrReadOnly` | 판매자이거나 읽기 전용 |
| `IsOrderOwnerOrAdmin` | 주문 소유자이거나 관리자 |

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

| Admin 클래스 | 모델 | 특징 |
|-------------|------|------|
| `UserAdmin` | User | 사용자 관리 |
| `CategoryAdmin` | Category | DraggableMPTTAdmin (드래그앤드롭) |
| `ProductAdmin` | Product | ProductImageInline 포함 |
| `ProductImageInline` | ProductImage | 상품 이미지 인라인 |
| `ProductReviewAdmin` | ProductReview | 리뷰 관리 |
| `OrderAdmin` | Order | OrderItemInline 포함 |
| `OrderItemInline` | OrderItem | 주문 상품 인라인 |
| `CartAdmin` | Cart | CartItemInline 포함 |
| `CartItemAdmin` | CartItem | 장바구니 아이템 관리 |
| `PaymentAdmin` | Payment | PaymentLogInline 포함 |
| `PaymentLogAdmin` | PaymentLog | 결제 로그 관리 |
| `PointHistoryAdmin` | PointHistory | 포인트 이력 관리 |
| `EmailVerificationTokenAdmin` | EmailVerificationToken | 인증 토큰 관리 |
| `EmailLogAdmin` | EmailLog | 이메일 로그 관리 |
| `NotificationAdmin` | Notification | 알림 관리 |
| `ProductQuestionAdmin` | ProductQuestion | ProductAnswerInline 포함 |
| `ProductAnswerAdmin` | ProductAnswer | 답변 관리 |
| `ReturnAdmin` | Return | ReturnItemInline 포함 |
| `ReturnItemAdmin` | ReturnItem | 반품 상품 관리 |
| `SellerProfileAdmin` | SellerProfile | 판매자 프로필 관리 |

---

## 3.7 시그널 & 어댑터

### 3.7.1 시그널 `shopping/signals.py`

| 시그널 핸들러 | 설명 |
|--------------|------|
| `handle_social_login` | 소셜 로그인 처리 |
| `handle_new_social_account` | 새 소셜 계정 연결 |
| `generate_order_number` | 주문번호 생성 |

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
| `create_load_test_users` | `--count`, `--points`, `--clear` | 부하 테스트용 사용자 생성 |
| `create_test_data` | `--preset`, `--clear`, `--users`, `--reviews`, `--show-presets` | 테스트용 데이터 생성 |
| `delete_unverified_users` | `--days`, `--dry-run`, `--verbose` | 미인증 계정 삭제 |
| `test_point_expiry` | `--create-test-data`, `--expire`, `--notify`, `--use-points`, `--username` | 포인트 만료 기능 테스트 |

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

# 5. Testing & Quality Layer

> 테스트 및 품질 보증

---

## 5.1 테스트 Fixtures `shopping/tests/conftest.py`

### 5.1.1 설정 Fixtures

| Fixture | 설명 |
|---------|------|
| `setup_celery_for_tests` | Celery 동기 모드 설정 |
| `setup_throttle_for_tests` | Throttle 비활성화 |
| `setup_logging_for_tests` | 로깅 설정 |
| `django_db_setup` | 데이터베이스 설정 |

### 5.1.2 사용자 Fixtures

| Fixture | 설명 |
|---------|------|
| `api_client` | APIClient 인스턴스 |
| `user` | 일반 사용자 |
| `seller_user` | 판매자 사용자 |
| `unverified_user` | 미인증 사용자 |
| `inactive_user` | 비활성 사용자 |
| `withdrawn_user` | 탈퇴 사용자 |
| `user_factory` | 사용자 팩토리 |
| `authenticated_client` | 인증된 클라이언트 |
| `seller_authenticated_client` | 판매자 인증 클라이언트 |
| `get_tokens` | JWT 토큰 생성 |

### 5.1.3 상품/장바구니 Fixtures

| Fixture | 설명 |
|---------|------|
| `category` | 카테고리 |
| `product` | 상품 |
| `out_of_stock_product` | 품절 상품 |
| `inactive_product` | 비활성 상품 |
| `product_factory` | 상품 팩토리 |
| `multiple_products` | 다중 상품 |
| `cart` | 장바구니 |
| `cart_with_items` | 아이템 포함 장바구니 |

### 5.1.4 주문/결제 Fixtures

| Fixture | 설명 |
|---------|------|
| `shipping_data` | 배송 정보 |
| `order_factory` | 주문 팩토리 |
| `order` | 주문 |
| `paid_order` | 결제 완료 주문 |
| `order_with_multiple_items` | 다중 아이템 주문 |
| `pending_order` | 대기 주문 |
| `payment` | 결제 |
| `canceled_payment` | 취소된 결제 |
| `add_to_cart_helper` | 장바구니 추가 헬퍼 |

---

## 5.2 테스트 Factories `shopping/tests/factories.py`

### 5.2.1 상수

| 상수 클래스 | 내용 |
|-----------|------|
| `TestConstants` | 금액, 포인트, 재고, 배송 정보 등 테스트용 상수 |

### 5.2.2 사용자 & 인증 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `UserFactory` | - | 기본 사용자 |
| | `.verified` | 이메일 인증 완료 |
| | `.unverified` | 이메일 미인증 |
| | `.inactive` | 비활성화 |
| | `.withdrawn` | 탈퇴 |
| | `.seller` | 판매자 |
| | `.admin` | 관리자 |
| | `.with_points` | 포인트 보유 |
| | `.with_high_points` | 고액 포인트 |
| | `.with_membership` | 특정 등급 |
| `EmailVerificationTokenFactory` | `.valid`, `.expired`, `.used`, `.recent` | 이메일 인증 토큰 |
| `PasswordResetTokenFactory` | `.valid`, `.expired` | 비밀번호 재설정 토큰 |
| `EmailLogFactory` | `.pending`, `.sent`, `.failed`, `.verified` | 이메일 로그 |
| `SocialAppFactory` | `.google`, `.kakao`, `.naver` | 소셜 앱 |
| `SocialAccountFactory` | `.google`, `.kakao`, `.naver` | 소셜 계정 |

### 5.2.3 상품 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `CategoryFactory` | - | 카테고리 |
| `ProductFactory` | - | 기본 상품 |
| | `.active` | 판매중 |
| | `.inactive` | 판매 중단 |
| | `.out_of_stock` | 품절 |
| | `.low_stock` | 저재고 |
| | `.with_price` | 특정 가격 |
| | `.with_long_name` | 긴 이름 |
| `ProductImageFactory` | `.primary` | 대표 이미지 |
| `ProductReviewFactory` | `.low_rating`, `.with_rating` | 상품 리뷰 |

### 5.2.4 주문 & 결제 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `OrderFactory` | - | 기본 주문 |
| | `.pending`, `.paid`, `.canceled`, `.shipped`, `.delivered` | 상태별 |
| | `.with_points`, `.with_full_points` | 포인트 사용 |
| | `.with_free_shipping`, `.with_remote_area` | 배송 옵션 |
| | `.with_items` | 상품 포함 |
| `OrderItemFactory` | - | 주문 상품 |
| `PaymentFactory` | - | 기본 결제 |
| | `.ready`, `.pending`, `.done`, `.done_card` | 상태별 |
| | `.canceled`, `.failed`, `.aborted` | 취소/실패 |
| `CartFactory` | `.active`, `.inactive`, `.with_items` | 장바구니 |
| `CartItemFactory` | - | 장바구니 아이템 |

### 5.2.5 포인트 & 반품 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `PointHistoryFactory` | `.earn`, `.use` | 적립/사용 |
| | `.earn_expiring_soon`, `.earn_expired` | 만료 관련 |
| | `.with_partial_usage` | 부분 사용 |
| `ReturnFactory` | `.refund`, `.exchange` | 유형별 |
| | `.requested`, `.approved`, `.rejected` | 상태별 |
| | `.shipping`, `.received`, `.completed` | 진행 상태 |
| `ReturnItemFactory` | - | 반품 상품 |

### 5.2.6 문의 & 웹훅 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `ProductQuestionFactory` | `.secret`, `.answered` | 상품 문의 |
| `ProductAnswerFactory` | - | 상품 답변 |
| `WebhookEventFactory` | `.payment_done`, `.payment_canceled`, `.payment_failed` | 웹훅 이벤트 |

---

## 5.3 Data Builders

### 5.3.1 API 응답 빌더

| Builder | 메서드 | 설명 |
|---------|--------|------|
| `TossResponseBuilder` | `success_response()` | 결제 승인 성공 응답 |
| | `cancel_response()` | 결제 취소 성공 응답 |
| | `error_response()` | 에러 응답 |
| `WebhookDataBuilder` | `payment_done()` | 결제 완료 이벤트 |
| | `payment_canceled()` | 결제 취소 이벤트 |
| | `payment_failed()` | 결제 실패 이벤트 |
| `OAuthDataBuilder` | `google()`, `kakao()`, `naver()` | OAuth 응답 |

### 5.3.2 요청 데이터 빌더

| Builder | 메서드 | 설명 |
|---------|--------|------|
| `ShippingDataBuilder` | `default()` | 기본 배송 정보 |
| | `remote_area()` | 도서산간 배송지 |
| | `invalid()` | 잘못된 배송 정보 |
| `PaymentRequestBuilder` | `confirm_request()` | 결제 승인 요청 |

---

## 5.4 유틸리티

| 클래스 | 메서드 | 설명 |
|--------|--------|------|
| `SKUGenerator` | `generate()` | SKU 생성 |
| | `reset()` | 카운터 리셋 |

---

## 5.5 부하 테스트 `load_tests/`

### 5.5.1 설정 `load_tests/config.py`

| 설정 | 설명 |
|------|------|
| `HOST` | 서버 호스트 |
| `TEST_USER_COUNT` | 테스트 사용자 수 |
| `TEST_USER_PREFIX` | 사용자 접두사 |
| `TEST_USER_PASSWORD` | 테스트 비밀번호 |
| `USER_WEIGHTS` | 행동 비율 (browser 65%, shopper 25%, buyer 10%) |
| `WAIT_TIME_MIN/MAX` | 대기 시간 범위 |
| `ENDPOINTS` | API 엔드포인트 |
| `SLA_TARGETS` | 성능 목표 (SLA) |

### 5.5.2 사용자 클래스

| 클래스 | Weight | 행동 |
|--------|--------|------|
| `BrowserUser` | 65% | 상품 목록/상세 조회, 검색, 카테고리 조회 |
| `ShopperUser` | 25% | 브라우징 + 장바구니 추가/수정/조회 |
| `BuyerUser` | 10% | 전체 구매 흐름 (장바구니 → 결제) |

### 5.5.3 시나리오

| 시나리오 | 파일 | 설명 |
|---------|------|------|
| 동시 주문 | `scenarios/concurrent_order.py` | 동시 주문 경쟁 테스트 |
| 결제 스트레스 | `scenarios/payment_stress.py` | 결제 부하 + 중복 결제 시도 |

---

## 5.6 코드 품질 도구

| 파일 | 설명 |
|------|------|
| `check-code-quality.sh` | 코드 품질 검사 스크립트 |
| `pyproject.toml` | 프로젝트 설정 (black, isort, pytest 등) |
| `setup.cfg` | 추가 도구 설정 |
| `pytest_mutmut.ini` | 뮤테이션 테스트 설정 |

---

## 5.7 문서화

| 파일 | 설명 |
|------|------|
| `docs/API.md` | API 문서 |
| `docs/CODE_ANALYSIS_TOOLS.md` | 코드 분석 도구 |
| `docs/CONCURRENCY_ANALYSIS.md` | 동시성 분석 |
| `docs/FEATURES.md` | 기능 목록 |
| `docs/IMPLEMENTED_FEATURES.md` | 구현된 기능 목록 (본 문서) |
| `docs/INFRASTRUCTURE.md` | 인프라 문서 |
| `docs/LOAD_TEST_GUIDE.md` | 부하 테스트 가이드 |
| `docs/LOGGING_GUIDELINES.md` | 로깅 가이드라인 |
| `docs/MODELS.md` | 모델 문서 |
| `docs/OAUTH_DEPLOYMENT_GUIDE.md` | OAuth 배포 가이드 |
| `docs/SETUP.md` | 설치 가이드 |
| `docs/TESTING.md` | 테스트 가이드 |

---

## 5.8 인프라 & 배포

| 파일 | 설명 |
|------|------|
| `Dockerfile` | Docker 이미지 빌드 |
| `docker-compose.yml` | Docker Compose 설정 |
| `DOCKER_LOADTEST_GUIDE.md` | Docker 부하 테스트 가이드 |
| `nginx/` | Nginx 설정 |
| `scripts/` | 배포 스크립트 |

---

# 📌 검증 결과 요약

> ✅ 2025-12-04 검증 완료

## 1단계 (Views) 검증 결과

### 확인된 변경사항
1. **소셜 로그인**: `GoogleLogin`, `KakaoLogin`, `NaverLogin`, `SocialAccountListView`, `SocialAccountDisconnectView` → allauth 기반 `SocialCallbackView`로 통합
2. **상품**: `ProductViewSet.my_products`, `ProductViewSet.set_primary_image` → 코드에서 확인되지 않음
3. **장바구니**: `CartItemViewSet.check_price_changes` → `CartViewSet`에 통합됨
4. **주문**: `OrderViewSet.my_orders`, `OrderViewSet.status` → 별도 action 없음, 기본 list/retrieve 사용
5. **결제**: `PaymentViewSet` → 개별 APIView로 분리 (`PaymentListView`, `PaymentDetailView`, `PaymentRequestView` 등)
6. **반품**: `ReturnViewSet.update_tracking` → `partial_update`로 통합
7. **포인트**: `PointViewSet` → 개별 APIView로 분리
8. **알림**: `NotificationViewSet` → 개별 action 메서드로 통합 (`mark_as_read`, `clear_read` 등)

## 2단계 (Models) 검증 결과

### ✅ User 모델 (`shopping/models/user.py`)
**존재하는 메서드/속성:**
- `get_full_address()`, `get_earn_rate()`, `is_vip` (property)
- `add_to_wishlist()`, `is_in_wishlist()`, `get_wishlist_count()`, `clear_wishlist()`, `remove_from_wishlist()`

**❌ 존재하지 않음 (문서에서 삭제 완료):**
- `get_total_orders`, `get_total_spent`, `get_point_balance`
- `add_points`, `use_points` → PointService에서 처리
- `update_membership_level`, `check_membership_upgrade`
- `get_available_coupons_count` → 쿠폰 기능 미구현
- `withdraw`, `restore` → UserService에서 처리

### ✅ Product 모델 (`shopping/models/product.py`)
**존재하는 메서드/속성:**
- `is_on_sale`, `discount_percentage`, `is_in_stock` (property)
- `can_purchase()`, `stock_status` (property)
- `get_wishlist_count()`, `is_wished_by()`, `get_wishlist_users()`, `wishlist_count` (property)

**❌ 존재하지 않음 (문서에서 삭제 완료):**
- `average_rating` → View에서 annotate로 계산
- `increase_stock`, `decrease_stock` → 직접 stock 필드 조작

### ✅ ProductReview 모델
**❌ 존재하지 않음 (문서에서 삭제 완료):**
- `can_edit`, `can_delete`

### ✅ Cart 모델 (`shopping/models/cart.py`) - 검증 완료
- `Cart`: `get_total_amount()`, `get_total_quantity()`, `clear()`, `deactivate()`, `get_or_create_active_cart()`, `merge_anonymous_cart()`
- `CartItem`: `subtotal`, `is_price_changed`, `price_difference` (property), `increase_quantity()`, `decrease_quantity()`, `update_quantity()`, `is_available()`

### ✅ Order 모델 (`shopping/models/order.py`) - 검증 완료
- `Order`: `get_full_shipping_address`, `is_paid`, `can_cancel` (property), `get_total_shipping_fee()`, `payment_method_display` (property)
- `OrderItem`: `get_subtotal()`

### ✅ Payment 모델 (`shopping/models/payment.py`) - 검증 완료
- `Payment`: `is_paid`, `can_cancel` (property), `mark_as_paid()`, `mark_as_failed()`, `mark_as_canceled()`, `sanitize_raw_response()`
- `PaymentLog`: 로그 기록용

### ✅ Point 모델 (`shopping/models/point.py`) - 검증 완료
- `PointHistoryManager`: `get_total_earned()`, `get_total_used()`, `get_expiring_soon()`, `get_month_statistics()`, `optimized_for_list()`
- `PointHistory`: `create_history()`, `get_user_balance()`, `get_expiring_points()` (classmethod)

### ✅ Auth 모델 - 검증 완료
- `EmailVerificationToken`: `generate_verification_code()`, `is_expired()`, `mark_as_used()`, `can_resend()`
- `EmailLog`: `mark_as_sent()`, `mark_as_failed()`, `mark_as_verified()`
- `PasswordResetToken`: `generate_token()`, `invalidate_previous_tokens()`, `verify_token()`, `is_expired()`, `mark_as_used()`

### ✅ 기타 모델 - 검증 완료
- `Notification`: `mark_as_read()`
- `ProductQuestion`: `can_view()`
- `ProductAnswer`: 판매자/관리자 검증 로직
- `Return`: `get_decrypted_account_number()`, `get_masked_account_number()`, `can_request_for_order()`, `approve()`, `reject()`, `confirm_receive()`, `complete_refund()`, `complete_exchange()`
- `ReturnItem`: `get_subtotal()`
- `SellerProfile`: User.is_seller=True 제약
- `WebhookEvent`: 감사 로그 모델

## 3단계 (Services) 검증 결과

### ✅ 모든 서비스 파일 검증 완료 (18개)

| 서비스 | 메서드 수 | 상태 |
|--------|----------|------|
| UserService | 6 | ✅ |
| TokenService | 2 | ✅ |
| EmailVerificationService | 6 | ✅ |
| PasswordResetService | 2 | ✅ |
| SocialAuthService | 7 | ✅ |
| ProductService | 1 | ✅ |
| ProductQAService | 1 | ✅ |
| CartService | 11 | ✅ |
| WishlistService | 9 | ✅ |
| OrderService | 3 | ✅ |
| PaymentService | 4 | ✅ |
| ShippingService | 2 | ✅ |
| ReturnService | 9 | ✅ |
| PointService | 9 | ✅ |
| PointQueryService | 6 | ✅ |
| NotificationService | 10 | ✅ |
| TossWebhookService | 6 | ✅ |
| base.py | ServiceError + @log_service_call | ✅ |


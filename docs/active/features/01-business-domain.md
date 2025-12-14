# 1. Business Domain Layer

> 사용자가 직접 사용하는 기능 (Views → User Features)

---

## 1.1 회원 관리 (User Management)

### 1.1.1 인증 (Authentication) `shopping/views/auth_views.py`

인증 진입점을 제공하며, 비즈니스 로직은 `UserService`, `TokenService`에 위임합니다.

| View | 책임 | 결과 |
|------|------|------|
| `RegisterView` | 사용자 계정 생성 및 인증 세션 시작 | 즉시 로그인 가능, 이메일 인증 대기 상태 |
| `LoginView` | 자격 증명 확인 및 인증 세션 시작 | 비회원 장바구니 보존, JWT 토큰 발급 |
| `LogoutView` | 현재 세션 종료 | 해당 Refresh Token 재사용 불가 |
| `CustomTokenRefreshView` | Access Token 갱신 | 재로그인 없이 세션 연장 |
| `check_token` | Access Token 유효성 확인 | 클라이언트가 토큰 상태 인지 |

### 1.1.2 프로필 관리 (Profile) `shopping/views/user_views.py`

사용자 정보 관리 진입점을 제공합니다.

| View | 책임 | 결과 |
|------|------|------|
| `ProfileView.retrieve` | 현재 사용자 프로필 조회 | 사용자 정보 반환 |
| `ProfileView.update` | 프로필 전체 필드 수정 | 프로필 정보 갱신 |
| `ProfileView.partial_update` | 프로필 일부 필드 수정 | 변경된 필드만 갱신 |
| `PasswordChangeView` | 현재 비밀번호 확인 후 새 비밀번호 설정 | 다음 로그인부터 새 비밀번호 적용 |
| `withdraw` | 회원 탈퇴 및 세션 종료 | 계정 비활성화, 즉시 로그아웃 |

### 1.1.3 이메일 인증 (Email Verification) `shopping/views/email_verification_views.py`

이메일 인증 진입점을 제공하며, 비즈니스 로직은 `EmailVerificationService`에 위임합니다.

| View | 책임 | 결과 |
|------|------|------|
| `SendVerificationEmailView` | 인증 이메일 발송 트리거 | 인증 이메일 수신 대기 |
| `VerifyEmailView.get` | UUID 토큰으로 이메일 인증 확정 | 이메일 인증 완료, 결제/주문 기능 사용 가능 |
| `VerifyEmailView.post` | 6자리 코드로 이메일 인증 확정 | 이메일 인증 완료, 결제/주문 기능 사용 가능 |
| `ResendVerificationEmailView` | 인증 이메일 재발송 (1분 쿨다운) | 새 인증 코드 수신 |
| `check_verification_status` | 현재 인증 상태 조회 | 클라이언트가 다음 액션 결정 가능 |

### 1.1.4 비밀번호 재설정 (Password Reset) `shopping/views/password_reset_views.py`

비밀번호 재설정 진입점을 제공하며, 비즈니스 로직은 `PasswordResetService`에 위임합니다.

| View | 책임 | 결과 |
|------|------|------|
| `PasswordResetRequestView` | 재설정 토큰 생성 및 이메일 발송 | 사용자는 이메일로 재설정 링크 수신 |
| `PasswordResetConfirmView` | 토큰 검증 및 새 비밀번호 설정 | 새 비밀번호로 로그인 가능 |

### 1.1.5 소셜 로그인 (Social Auth) `shopping/urls.py`, `shopping/views/social_auth_views.py`

**Access Token 방식 (dj-rest-auth)** - 클라이언트가 직접 OAuth 인증 후 토큰 전달

| View | 책임 | 결과 |
|------|------|------|
| `GoogleLogin` | 구글 access_token으로 사용자 인증 및 JWT 발급 | 구글 계정으로 즉시 로그인 |
| `KakaoLogin` | 카카오 access_token으로 사용자 인증 및 JWT 발급 | 카카오 계정으로 즉시 로그인 |
| `NaverLogin` | 네이버 access_token으로 사용자 인증 및 JWT 발급 | 네이버 계정으로 즉시 로그인 |

**Authorization Code 방식** - 서버 사이드 OAuth 콜백 처리

| View | 책임 | 결과 |
|------|------|------|
| `SocialCallbackView` | OAuth 콜백 처리 및 JWT 발급 | 신규 사용자 자동 생성 또는 기존 사용자 로그인, 이메일 자동 인증 |

> ℹ️ **참고**: 두 가지 방식 모두 지원. Access Token 방식은 SPA/모바일 앱용, Authorization Code 방식은 서버 사이드 렌더링용

---

## 1.2 상품 관리 (Product Management)

### 1.2.1 상품 (Product) `shopping/views/product_views.py`

상품 CRUD 및 검색/필터링 진입점을 제공합니다.

| View | 책임 | 결과 |
|------|------|------|
| `ProductViewSet.list` | 활성 상품 목록을 조건별로 필터링하여 제공 | 검색어, 카테고리, 가격 범위, 재고 상태별 상품 목록 |
| `ProductViewSet.retrieve` | 상품 상세 정보 제공 | 판매자, 카테고리, 이미지, 리뷰 포함 상세 정보 |
| `ProductViewSet.create` | 상품 등록 확정 | 판매자 자동 지정, slug 자동 생성 및 중복 방지 |
| `ProductViewSet.update` | 상품 정보 전체 갱신 | 본인 상품만, 이름 변경 시 slug 자동 재생성 |
| `ProductViewSet.partial_update` | 상품 정보 부분 갱신 | 본인 상품만, 요청 필드만 갱신 |
| `ProductViewSet.destroy` | 상품 삭제 | 본인 상품만 삭제 |
| `ProductViewSet.popular` | 인기 상품 목록 제공 | 리뷰 수 기준 상위 12개 상품 |
| `ProductViewSet.best_rating` | 고평점 상품 목록 제공 | 리뷰 3개 이상 중 평균 평점 상위 12개 |
| `ProductViewSet.low_stock` | 재고 부족 상품 알림 (판매자 전용) | 본인 상품 중 재고 10개 이하 목록 |

### 1.2.2 상품 리뷰 (Reviews) `shopping/views/product_views.py`

상품 리뷰 조회 및 작성 진입점을 제공합니다.

| View | 책임 | 결과 |
|------|------|------|
| `ProductViewSet.reviews` | 상품별 리뷰 목록 제공 | 정렬/페이지네이션 적용된 리뷰 목록 |
| `ProductViewSet.create_review` | 상품 리뷰 등록 (상품당 1개 제한) | 리뷰 등록 완료 또는 중복 시 오류 |

### 1.2.3 카테고리 (Category) `shopping/views/product_views.py`

카테고리 계층 구조 조회 진입점을 제공합니다.

| View | 책임 | 결과 |
|------|------|------|
| `CategoryViewSet.list` | 활성 카테고리 목록 제공 | 상품 수 포함된 카테고리 목록 |
| `CategoryViewSet.retrieve` | 카테고리 상세 정보 제공 | 부모 카테고리, 전체 경로 포함 |
| `CategoryViewSet.tree` | 전체 카테고리 계층 구조 제공 | 상품 수 포함된 트리 구조 |
| `CategoryViewSet.products` | 카테고리별 상품 목록 제공 | 하위 카테고리 상품 포함 |

### 1.2.4 상품 문의 (Product Q&A) `shopping/views/product_qa_views.py`

상품 문의/답변 진입점을 제공하며, 비밀글 권한 제어를 포함합니다.

| View | 책임 | 결과 |
|------|------|------|
| `ProductQuestionViewSet.list` | 상품별 문의 목록 제공 | 비밀글은 권한자만 조회 가능 |
| `ProductQuestionViewSet.retrieve` | 문의 상세 정보 제공 | 답변 포함, 비밀글 권한 검증 |
| `ProductQuestionViewSet.create` | 상품 문의 등록 | 인증 사용자만, 비밀글 설정 가능 |
| `ProductQuestionViewSet.update` | 문의 수정 | 작성자만, 답변 완료 문의는 수정 불가 |
| `ProductQuestionViewSet.destroy` | 문의 삭제 | 작성자/관리자만, 답변 완료 문의는 삭제 불가 |
| `ProductQuestionViewSet.answer` | 문의 답변 등록 | 판매자만, 중복 답변 불가 |
| `ProductQuestionViewSet.update_answer` | 답변 수정 | 판매자/관리자만 |
| `ProductQuestionViewSet.delete_answer` | 답변 삭제 및 문의 상태 롤백 | 판매자/관리자만, 답변 완료 상태 해제 |
| `MyQuestionViewSet.list` | 내 문의 목록 제공 | 최신순 정렬, 페이지네이션 적용 |
| `MyQuestionViewSet.retrieve` | 내 문의 상세 정보 제공 | 답변 포함 |

---

## 1.3 쇼핑 기능 (Shopping Features)

### 1.3.1 장바구니 (Cart) `shopping/views/cart_views.py`

장바구니 관리 진입점을 제공하며, 비즈니스 로직은 `CartService`에 위임합니다. 회원/비회원 모두 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `CartViewSet.retrieve` | 장바구니 전체 정보 제공 | 아이템 목록, 총 금액, 재고/가격 변동 경고 |
| `CartViewSet.summary` | 장바구니 요약 정보 제공 | 아이템 개수, 총 금액 |
| `CartViewSet.add_item` | 장바구니에 상품 추가 | 동일 상품은 수량 합산 |
| `CartViewSet.items` | 장바구니 아이템 목록 제공 | 최근 추가순 정렬 |
| `CartViewSet.update_item` | 아이템 수량 변경 | 수량 0이면 삭제 |
| `CartViewSet.delete_item` | 장바구니에서 아이템 제거 | 해당 상품 삭제 |
| `CartViewSet.clear` | 장바구니 전체 비우기 | confirm 필수 |
| `CartViewSet.bulk_add` | 여러 상품 일괄 추가 | 부분 성공 허용, 실패 목록 반환 |
| `CartViewSet.check_stock` | 장바구니 상품 재고 확인 | 품절/재고 부족 상품 목록 |
| `CartViewSet.cleanup` | 구매 불가 상품 자동 정리 | 품절 제거, 재고 부족은 수량 조정 |
| `CartViewSet.update_prices` | 변경된 상품 가격을 현재가로 갱신 | 가격 동기화 완료 |

#### 장바구니 아이템 RESTful API `CartItemViewSet`

`CartViewSet`과 동일한 기능을 RESTful 방식으로 제공합니다. 회원/비회원 모두 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `CartItemViewSet.list` | 장바구니 아이템 목록 제공 | 아이템 목록 |
| `CartItemViewSet.create` | 장바구니에 아이템 추가 | 상품 추가 완료 |
| `CartItemViewSet.update` | 아이템 수량 변경 | 수량 0이면 삭제 |
| `CartItemViewSet.destroy` | 아이템 삭제 | 장바구니에서 제거 |

### 1.3.2 찜 목록 (Wishlist) `shopping/views/wishlist_views.py`

찜 목록 관리 진입점을 제공하며, 비즈니스 로직은 `WishlistService`에 위임합니다. 인증 사용자만 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `WishlistViewSet.list` | 찜 목록을 필터/정렬하여 제공 | 구매 가능/세일 필터, 정렬 적용 |
| `WishlistViewSet.toggle` | 찜 상태 토글 (추가/제거) | 변경된 상태, 해당 상품 총 찜 수 |
| `WishlistViewSet.add` | 찜 목록에 상품 추가 | 중복 시 무시 |
| `WishlistViewSet.remove` | 찜 목록에서 상품 제거 | 삭제 완료 |
| `WishlistViewSet.bulk_add` | 여러 상품 일괄 찜하기 | 추가/스킵 개수 반환 |
| `WishlistViewSet.clear` | 찜 목록 전체 삭제 | confirm 필수 |
| `WishlistViewSet.check` | 특정 상품 찜 여부 확인 | 찜 상태, 해당 상품 총 찜 수 |
| `WishlistViewSet.stats` | 찜 목록 통계 제공 | 전체/구매가능/품절 개수, 가격 합계 |
| `WishlistViewSet.move_to_cart` | 찜 상품을 장바구니로 이동 | 재고 확인 후 이동, 옵션 시 찜에서 제거 |

---

## 1.4 주문 관리 (Order Management)

### 1.4.1 주문 (Orders) `shopping/views/order_views.py`

주문 관리 진입점을 제공하며, 비즈니스 로직은 `OrderService`에 위임합니다. 인증 사용자만 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `OrderViewSet.list` | 내 주문 목록을 상태별로 필터링하여 제공 | 페이지네이션 적용된 주문 목록 (최신순) |
| `OrderViewSet.retrieve` | 주문 상세 정보 제공 | 본인/관리자만 조회 가능, 주문 아이템 포함 |
| `OrderViewSet.create` | 장바구니 기반 주문 생성 확정 | 이메일 인증 필수, 즉시 주문 ID 발급 |
| `OrderViewSet.cancel` | 배송 전 주문 취소 확정 | 재고 복구, 사용 포인트 환불 |

### 1.4.2 결제 (Payments) `shopping/views/payment_views.py`

결제 관리 진입점을 제공하며, 비즈니스 로직은 `PaymentService`에 위임합니다. 인증 사용자만 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `PaymentListView` | 내 결제 목록을 상태별로 필터링하여 제공 | 페이지네이션 적용된 결제 목록 (최신순) |
| `PaymentDetailView` | 결제 상세 정보 제공 | 본인 결제만 조회 가능 |
| `PaymentRequestView` | 결제 정보 생성 및 PG사 연동 정보 제공 | 이메일 인증 필수, 결제창 호출에 필요한 정보 반환 |
| `PaymentConfirmView` | 결제 승인 확정 | 즉시 응답, 폴링으로 완료 확인 |
| `PaymentCancelView` | 결제 취소 확정 | 재고 복구, 사용 포인트 환불, 적립 포인트 회수 |
| `PaymentStatusView` | 결제 처리 상태 제공 | 폴링용 상태 확인, 완료 여부 반환 |
| `PaymentFailView` | 결제창 실패/취소 처리 확정 | 결제 상태를 실패로 변경, 실패 로그 기록 |

**템플릿 기반 결제 (레거시)**

| View | 책임 | 결과 |
|------|------|------|
| `payment_test_page` | 결제 테스트 페이지 렌더링 | 포인트 정보 포함된 결제 화면 |
| `payment_success` | 결제 성공 콜백 처리 및 페이지 렌더링 | 결제 승인 완료, 포인트 적립 |
| `payment_fail` | 결제 실패 콜백 처리 및 페이지 렌더링 | 결제 상태를 실패로 변경 |

### 1.4.3 교환/환불 (Returns) `shopping/views/return_views.py`

교환/환불 관리 진입점을 제공하며, 비즈니스 로직은 `ReturnService`에 위임합니다.

**고객용 API** `ReturnViewSet` - 인증 사용자만 사용 가능

| View | 책임 | 결과 |
|------|------|------|
| `ReturnViewSet.list` | 내 교환/환불 목록을 상태/유형별로 필터링하여 제공 | 신청 내역 목록 반환 |
| `ReturnViewSet.retrieve` | 교환/환불 상세 정보 제공 | 주문/상품 정보 포함 |
| `ReturnViewSet.create` | 교환/환불 신청 확정 | 신청 상태로 생성, 판매자 승인 대기 |
| `ReturnViewSet.partial_update` | 송장번호 등 정보 수정 확정 | 승인 후에만 송장번호 입력 가능 |
| `ReturnViewSet.destroy` | 교환/환불 신청 취소 확정 | 신청 상태에서만 취소 가능 |

**판매자용 API** `SellerReturnViewSet` - 판매자/관리자만 사용 가능

| View | 책임 | 결과 |
|------|------|------|
| `SellerReturnViewSet.list` | 내 상품에 대한 교환/환불 목록 제공 | 본인 상품 관련 신청만 조회, 관리자는 전체 조회 |
| `SellerReturnViewSet.retrieve` | 교환/환불 상세 정보 제공 | 신청자 정보 포함 |
| `SellerReturnViewSet.approve` | 교환/환불 승인 확정 | 상태가 승인으로 변경, 고객에게 반품 안내 |
| `SellerReturnViewSet.reject` | 교환/환불 거부 확정 | 거부 사유 필수, 상태가 거부로 변경 |
| `SellerReturnViewSet.confirm_receive` | 반품 도착 확인 확정 | 상태가 도착완료로 변경, 완료 처리 가능 |
| `SellerReturnViewSet.complete` | 환불/교환 완료 처리 확정 | 환불 시 자동 환불, 교환 시 송장번호 입력 |

---

## 1.5 포인트 & 알림 (Points & Notifications)

### 1.5.1 포인트 (Points) `shopping/views/point_views.py`

포인트 조회/사용/취소 진입점을 제공하며, 비즈니스 로직은 `PointService`, `PointQueryService`에 위임합니다. 인증 사용자만 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `MyPointView` | 현재 포인트 현황과 최근 변동 이력 제공 | 보유 포인트와 최근 5건 이력 |
| `PointHistoryListView` | 포인트 이력을 조건별로 필터링하여 제공 | 유형/기간별 이력 목록과 요약 통계 |
| `PointCheckView` | 주문 금액 기준 포인트 사용 가능 여부 판단 | 보유 포인트, 사용 가능 여부, 최대 사용 가능 금액 |
| `ExpiringPointsView` | 지정 기간 내 만료 예정 포인트 제공 | 만료 예정 총액, 월별 요약, 상세 이력 |
| `point_statistics` | 포인트 종합 통계 제공 | 현재/이번달/전체 적립·사용, 유형별 통계 |
| `PointUseView` | 만료 임박 포인트부터 차감하여 사용 확정 (최소 100P) | 사용 금액, 잔여 포인트, 차감 상세 내역 |
| `PointCancelView` | 주문 취소 시 포인트 환불 또는 회수 확정 | 처리 금액, 잔여 포인트, 처리 유형 |

### 1.5.2 알림 (Notifications) `shopping/views/notification_views.py`

알림 조회/읽음처리/삭제 진입점을 제공하며, 비즈니스 로직은 `NotificationService`에 위임합니다. 인증 사용자만 사용 가능합니다.

| View | 책임 | 결과 |
|------|------|------|
| `NotificationViewSet.list` | 사용자의 전체 알림 목록 제공 | 최신순 정렬된 알림 목록 |
| `NotificationViewSet.retrieve` | 알림 상세 조회 및 읽음 상태로 전환 | 상세 정보, 조회 시 자동 읽음 처리 |
| `NotificationViewSet.unread` | 읽지 않은 알림 요약 제공 | 미읽음 개수와 최근 5개 알림 (알림 아이콘용) |
| `NotificationViewSet.mark_read` | 선택 또는 전체 알림을 읽음 상태로 확정 | 처리 건수 |
| `NotificationViewSet.clear` | 읽은 알림을 일괄 삭제 | 삭제 건수 |

---

## 1.6 View Mixins `shopping/views/mixins.py`

| Mixin | 메서드 | 설명 |
|-------|--------|------|
| `EmailVerificationRequiredMixin` | `check_email_verification()` | 이메일 인증 필요 기능에서 사용 (결제/주문 등)

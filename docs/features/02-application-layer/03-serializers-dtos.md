# 2.3 Serializers & DTOs

> 데이터 변환/검증 `shopping/serializers/`, `shopping/dtos/`

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

**Category Serializers** `shopping/serializers/category_serializers.py`

| Serializer | 용도 |
|------------|------|
| `CategorySerializer` | 카테고리 기본 정보 + 부모 카테고리/상품 수/전체 경로 |
| `CategoryTreeSerializer` | 계층 구조 표현 (재귀적 children, 리프 노드 여부) |
| `CategoryCreateUpdateSerializer` | 생성/수정 (순환 참조 방지, 비활성화 제약 검증) |
| `SimpleCategorySerializer` | 다른 모델에서 참조 시 간단 정보 (id, name, slug) |

**Product Serializers** `shopping/serializers/product_serializers.py`

| Serializer | 용도 |
|------------|------|
| `ProductListSerializer` | 상품 목록 (간단 정보 + 평균 평점/리뷰 수/찜 수) |
| `ProductImageSerializer` | 상품 이미지 (대표 이미지 여부 포함) |
| `ProductReviewSerializer` | 상품 리뷰 (평점, 작성자, 내용) |
| `ProductDetailSerializer` | 상품 상세 (판매자/이미지/리뷰 전체 정보) |
| `ProductCreateUpdateSerializer` | 상품 생성/수정 (카테고리 검증 포함) |
| `AverageRatingField` | 평균 평점 커스텀 필드 |

### 2.3.4 상품 문의

**Product Q&A Serializers** `shopping/serializers/product_qa_serializers.py`

| Serializer | 용도 |
|------------|------|
| `ProductQuestionBaseSerializer` | 문의 베이스 (제목/내용 공통 검증) |
| `ProductAnswerSerializer` | 답변 조회 (판매자 정보 포함) |
| `ProductQuestionListSerializer` | 문의 목록 (비밀글 마스킹, 답변 여부) |
| `ProductQuestionDetailSerializer` | 문의 상세 (답변 포함) |
| `ProductQuestionCreateSerializer` | 문의 작성 (비밀글 설정 가능) |
| `ProductQuestionUpdateSerializer` | 문의 수정 (제목/내용/비밀글 여부) |
| `ProductAnswerCreateSerializer` | 답변 작성 및 문의 상태 변경 |
| `ProductAnswerUpdateSerializer` | 답변 수정 (내용만) |

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

### ProductFilterParams `shopping/dtos/product_filter.py`

상품 필터링 파라미터를 Request에서 추출하여 서비스 레이어로 전달하는 불변 DTO입니다.

| 필드 | 타입 | 설명 |
|------|------|------|
| `category_id` | int \| None | 카테고리 ID (하위 카테고리 포함) |
| `min_price` | int \| None | 최소 가격 |
| `max_price` | int \| None | 최대 가격 |
| `in_stock` | bool \| None | 재고 여부 (True: 있음, False: 없음) |
| `seller_id` | int \| None | 판매자 ID |

| 메서드 | 설명 |
|--------|------|
| `from_request()` | Request query_params에서 필터 조건 추출 (classmethod) |

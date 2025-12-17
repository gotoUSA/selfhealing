# 2.1 Domain Models

> 데이터 구조 및 도메인 모델 정의 `shopping/models/`

---

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

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
| | `.old_unverified` | 오래된 미인증 사용자 (정리 대상) |
| | `.recent_unverified` | 최근 미인증 사용자 (유지 대상) |
| | `.old_verified` | 오래된 인증 사용자 |
| `EmailVerificationTokenFactory` | `.valid`, `.expired`, `.used`, `.recent` | 이메일 인증 토큰 |
| | `.old_used`, `.recent_used` | 오래된/최근 사용 토큰 |
| `PasswordResetTokenFactory` | `.valid`, `.expired` | 비밀번호 재설정 토큰 |
| `EmailLogFactory` | `.pending`, `.sent`, `.failed`, `.verified` | 이메일 로그 |
| | `.old_sent`, `.old_verified`, `.old_pending` | 오래된 로그 (정리 대상) |
| | `.with_token` | 토큰 연결 로그 |
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
| `PaidOrderFactory` | - | 결제 완료 주문 (OrderFactory 상속, earned_points 자동 계산) |
| `CompletedPaymentFactory` | - | 카드 결제 완료 (PaymentFactory 상속, 카드 정보 기본 설정) |
| `OrderWithItemsFactory` | `.items` | OrderItem 포함 주문 (OrderFactory 상속) |
| `CartFactory` | `.active`, `.inactive`, `.with_items` | 장바구니 |
| `CartItemFactory` | - | 장바구니 아이템 |

### 5.2.5 포인트 & 반품 Factories

| Factory | Trait | 설명 |
|---------|-------|------|
| `PointHistoryFactory` | `.earn`, `.use` | 적립/사용 |
| | `.earn_expiring_soon`, `.earn_expired` | 만료 관련 |
| | `.with_partial_usage` | 부분 사용 |
| | `.old_expire`, `.recent_expire` | 오래된/최근 만료 이력 |
| | `.old_earn` | 오래된 적립 이력 |
| `ReturnFactory` | `.refund`, `.exchange` | 유형별 |
| | `.requested`, `.approved`, `.rejected` | 상태별 |
| | `.shipping`, `.received`, `.completed` | 진행 상태 |
| | `.with_items`, `.with_shipping_fee` | 아이템/배송비 포함 |
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




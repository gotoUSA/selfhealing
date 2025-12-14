# 2.2 Services

> 비즈니스 로직 `shopping/services/`

---

### 2.2.1 사용자 서비스

---

#### UserService `shopping/services/user_service.py`

회원가입, 로그인, 탈퇴, 소셜 로그인의 핵심 비즈니스 로직을 담당합니다.

**`register_user()`**
- **책임**: 회원가입 후처리를 완료
- **Side Effect**: 인증 토큰 생성, 이메일 인증 토큰 생성, 인증 이메일 발송 트리거
- **경계**: 이메일 발송은 백그라운드로 위임
- **결과**: 사용자 즉시 로그인 가능, 이메일 인증 대기 상태

**`login_user()`**
- **책임**: 로그인 상태를 확정하고 세션 정보를 갱신
- **Side Effect**: 마지막 로그인 시간/IP 갱신, 비회원 장바구니 병합
- **경계**: 장바구니 병합 실패해도 로그인은 계속 진행
- **결과**: 비회원 장바구니 보존, 인증 토큰 발급

**`withdraw_user()`**
- **책임**: 사용자를 탈퇴 상태로 전환하고 모든 세션을 종료
- **Side Effect**: 모든 토큰 무효화 처리
- **경계**: 포인트/주문 내역은 보존
- **결과**: 계정 비활성화, 즉시 재로그인 불가

**`process_social_login()`**
- **책임**: 소셜 계정으로 사용자를 인증하거나 신규 생성
- **Side Effect**: 신규 사용자 시 자동 생성, 로그인 시간 갱신
- **경계**: 이메일 없는 경우 대체 이메일 생성
- **결과**: 소셜 계정으로 즉시 로그인, 이메일 자동 인증 처리

**`create_tokens_for_user()`**
- **책임**: 사용자용 인증 토큰 쌍 생성
- **결과**: Access/Refresh 토큰 발급

**`send_verification_email()`**
- **책임**: 전달받은 토큰으로 인증 이메일 발송을 트리거
- **경계**: 토큰 생성은 호출자 책임, 실제 발송은 백그라운드로 위임
- **결과**: 이메일 발송 대기열 등록

> **Data Classes**: `LoginResult`, `WithdrawResult`, `SocialLoginResult`

---

#### TokenService `shopping/services/token_service.py`

인증 토큰 검증, 갱신, 무효화를 담당합니다.

**`validate_and_refresh_token()`**
- **책임**: Refresh Token을 검증하고 새 Access Token 발급
- **Side Effect**: 토큰 회전 설정 시 이전 토큰 무효화
- **결과**: 사용자 재로그인 없이 세션 연장

**`blacklist_token()`**
- **책임**: Refresh Token을 무효화 목록에 등록
- **Side Effect**: 토큰 무효화 처리
- **결과**: 해당 토큰으로 재인증 불가

> **Data Classes**: `TokenRefreshResult`

---

#### EmailVerificationService `shopping/services/email_verification_service.py`

이메일 인증 발송, 검증, 상태 관리를 담당합니다.

**`send_verification_email()`**
- **책임**: 새 인증 토큰을 생성하고 인증 이메일 발송을 트리거
- **Side Effect**: 기존 미사용 토큰 일괄 무효화
- **경계**: 이메일 발송은 백그라운드로 위임
- **결과**: 새 인증 코드 수신 대기

**`verify_by_token()`**
- **책임**: UUID 토큰으로 이메일 인증 상태를 확정
- **Side Effect**: 토큰 사용 완료 처리, 사용자 인증 상태 갱신
- **결과**: 이메일 인증 완료, 결제/주문 기능 사용 가능

**`verify_by_code()`**
- **책임**: 6자리 코드로 이메일 인증 상태를 확정
- **Side Effect**: 토큰 사용 완료 처리, 사용자 인증 상태 갱신
- **결과**: 이메일 인증 완료, 결제/주문 기능 사용 가능

**`get_verification_status()`**
- **책임**: 현재 인증 상태와 토큰 정보 조회
- **결과**: 클라이언트가 다음 액션 결정 가능

**`invalidate_tokens()`**
- **책임**: 사용자의 모든 미사용 토큰 무효화
- **Side Effect**: 토큰 일괄 무효화
- **결과**: 기존 인증 코드 무효

**`get_active_token()`**
- **책임**: 사용자의 활성 토큰 조회
- **결과**: 현재 유효한 토큰 반환

> **Data Classes**: `SendEmailResult`, `VerificationStatus`

---

#### PasswordResetService `shopping/services/password_reset_service.py`

비밀번호 재설정 요청 및 확인을 담당합니다.

**`request_password_reset()`**
- **책임**: 재설정 토큰을 생성하고 재설정 이메일 발송을 트리거
- **Side Effect**: 이전 미사용 토큰 무효화, 이메일 로그 생성
- **경계**: 이메일 발송은 백그라운드로 위임
- **결과**: 사용자는 이메일로 재설정 링크 수신

**`confirm_password_reset()`**
- **책임**: 토큰을 검증하고 새 비밀번호 설정
- **Side Effect**: 토큰 사용 완료 처리, 이메일 로그 상태 갱신
- **경계**: 비밀번호 변경과 토큰 무효화가 함께 처리됨
- **결과**: 새 비밀번호로 로그인 가능

> **Data Classes**: `PasswordResetRequestResult`, `PasswordResetConfirmResult`

---

#### SocialAuthService `shopping/services/social_auth_service.py`

OAuth 제공자(Google, Kakao, Naver)와의 통신 및 인증을 담당합니다.

**`process_oauth_callback()`**
- **책임**: OAuth 콜백 전체 흐름(코드→토큰→사용자정보)을 처리
- **경계**: 사용자 생성/로그인은 `UserService`로 위임
- **결과**: 정규화된 사용자 정보 반환

**`exchange_code_for_token()`**
- **책임**: Authorization code를 access token으로 교환
- **경계**: 일시적 네트워크 오류에 대해 자동 재시도
- **결과**: OAuth 토큰 발급

**`get_user_info()`**
- **책임**: OAuth 제공자로부터 사용자 정보 조회
- **경계**: 일시적 네트워크 오류에 대해 자동 재시도
- **결과**: 제공자별 원본 사용자 정보

**`normalize_user_info()`**
- **책임**: 제공자별 응답을 통일된 형식으로 변환
- **결과**: 정규화된 사용자 정보 (email, name, provider_id)

| 기타 메서드 | 설명 |
|--------|------|
| `get_callback_uri()` | 설정에서 OAuth 콜백 URI 조회 |
| `get_supported_providers()` | 지원하는 OAuth 제공자 목록 반환 |
| `is_supported_provider()` | 지원하는 OAuth 제공자인지 확인 |

> **Data Classes**: `OAuthTokens`, `NormalizedUserInfo`

---

### 2.2.2 상품 서비스

---

#### ProductService `shopping/services/product_service.py`

상품 이미지의 대표 설정 로직을 담당합니다.

**`set_primary_image()`**
- **책임**: 상품의 대표 이미지를 변경하고 기존 대표 이미지를 해제
- **Side Effect**: 동일 상품의 모든 이미지 is_primary 상태 갱신
- **경계**: 동시 요청에도 하나의 대표 이미지만 유지됨
- **결과**: 지정된 이미지가 유일한 대표 이미지로 설정

---

#### ProductQAService `shopping/services/product_qa_service.py`

상품 문의에 대한 답변 생성 및 알림 처리를 담당합니다.

**`create_answer()`**
- **책임**: 문의에 답변을 생성하고 문의를 답변 완료 상태로 전환
- **Side Effect**: 문의 상태 갱신, 질문자에게 알림 생성
- **경계**: 실시간 푸시는 별도 처리로 위임
- **결과**: 질문자는 알림으로 답변 도착 인지, 문의 상태가 답변 완료로 변경

---

### 2.2.3 쇼핑 서비스

---

#### CartService `shopping/services/cart_service.py`

장바구니의 아이템 추가/수정/삭제, 재고 검증, 일괄 처리의 핵심 비즈니스 로직을 담당합니다.

**`get_or_create_cart()`**
- **책임**: 사용자 또는 세션의 활성 장바구니를 반환하거나 신규 생성
- **Side Effect**: 비회원인 경우 세션 자동 생성
- **경계**: 회원은 user 기반, 비회원은 session 기반 장바구니
- **결과**: 아이템 정보가 포함된 활성 장바구니

**`add_item()`**
- **책임**: 장바구니에 상품을 추가하고 담은 시점 가격을 스냅샷으로 저장
- **Side Effect**: 기존 상품이면 수량만 증가, 가격은 최초 담은 시점 유지
- **경계**: 재고 검증 포함, 동시 요청에도 중복 추가 방지
- **결과**: 상품이 장바구니에 추가됨, 재고 부족 시 오류 반환

**`update_item_quantity()`**
- **책임**: 장바구니 아이템의 수량을 변경하거나 삭제
- **Side Effect**: 수량 0이면 아이템 삭제
- **경계**: 재고 검증 포함, 동시 요청에도 일관성 유지
- **결과**: 수량 변경 완료 또는 삭제 완료

**`remove_item()`**
- **책임**: 장바구니에서 특정 아이템을 완전히 제거
- **결과**: 아이템이 장바구니에서 삭제됨

**`clear_cart()`**
- **책임**: 장바구니의 모든 아이템을 일괄 삭제
- **결과**: 장바구니가 비워짐

**`bulk_add_items()`**
- **책임**: 여러 상품을 한 번에 장바구니에 추가
- **Side Effect**: 일부 실패해도 성공한 항목은 추가됨
- **경계**: 개별 오류는 errors 필드로 반환, 전체 실패 없이 부분 성공 허용
- **결과**: 성공/실패 개수와 오류 상세 정보 반환

**`check_stock()`**
- **책임**: 장바구니 상품들의 재고 상태를 확인
- **경계**: 판매 중단, 품절, 재고 부족을 구분하여 반환
- **결과**: 재고 문제 목록 반환, 문제 없으면 빈 리스트

**`check_price_changes()`**
- **책임**: 장바구니 상품들의 가격 변동을 확인
- **경계**: 담은 시점 가격과 현재 가격 비교
- **결과**: 가격 변경된 상품 목록과 변동 금액 반환

**`update_item_prices()`**
- **책임**: 담은 시점 가격을 현재 가격으로 일괄 갱신
- **결과**: 사용자 동의 후 가격 스냅샷 업데이트

**`merge_anonymous_cart()`**
- **책임**: 비회원 장바구니를 회원 장바구니로 병합
- **Side Effect**: 기존 상품은 수량 합산, 새 상품은 이전, 비회원 장바구니 비활성화
- **경계**: 로그인 시 호출하여 비회원 시절 상품 보존
- **결과**: 비회원 장바구니 상품이 회원 장바구니로 통합됨

**`cleanup_unavailable_items()`**
- **책임**: 구매 불가능한 상품을 자동으로 정리
- **Side Effect**: 비활성/품절 상품 제거, 재고 부족 상품 수량 조정
- **결과**: 정리된 상품 목록과 조정 내역 반환

> **Data Classes**: `StockIssue`, `PriceChange`, `BulkAddResult`

---

#### WishlistService `shopping/services/wishlist_service.py`

찜 목록의 추가/제거, 일괄 처리, 통계 계산, 장바구니 이동의 핵심 비즈니스 로직을 담당합니다.

**`toggle()`**
- **책임**: 찜 상태를 토글하여 추가 또는 제거
- **결과**: 변경된 찜 상태와 해당 상품의 전체 찜 수 반환

**`add()`**
- **책임**: 찜 목록에 상품 추가
- **경계**: 이미 찜한 상품이면 중복 없이 무시
- **결과**: 새로 추가 여부와 전체 찜 수 반환

**`remove()`**
- **책임**: 찜 목록에서 상품 제거
- **결과**: 상품이 찜 목록에서 삭제됨

**`bulk_add()`**
- **책임**: 여러 상품을 한 번에 찜 목록에 추가
- **Side Effect**: 중복 상품 자동 스킵
- **경계**: 이미 찜한 상품은 중복 없이 무시
- **결과**: 추가/스킵 개수와 전체 찜 개수 반환

**`clear()`**
- **책임**: 찜 목록 전체 삭제
- **결과**: 찜 목록이 비워짐

**`check()`**
- **책임**: 특정 상품의 찜 상태 확인
- **결과**: 찜 여부와 해당 상품의 전체 찜 수 반환

**`get_list()`**
- **책임**: 찜 목록을 필터링하고 정렬하여 조회
- **경계**: 구매 가능/세일 필터, 다양한 정렬 옵션 지원
- **결과**: 필터링된 상품 목록 반환

**`get_stats()`**
- **책임**: 찜 목록의 통계 정보를 집계
- **경계**: 세일 상품 판별은 compare_price와 price 비교 기준
- **결과**: 전체/구매가능/품절 개수, 가격 합계, 할인 금액 반환

**`move_to_cart()`**
- **책임**: 찜 상품을 장바구니로 이동
- **Side Effect**: 재고 확인 후 장바구니 추가, 옵션에 따라 찜 목록에서 제거
- **경계**: 품절 상품은 스킵, 이미 장바구니에 있는 상품은 스킵
- **결과**: 추가/스킵/품절 상품 목록과 결과 메시지 반환

> **Data Classes**: `ToggleResult`, `BulkAddResult`, `WishlistStats`, `MoveToCartResult`, `WishlistFilter`

---

### 2.2.4 주문/결제 서비스

---

#### OrderService `shopping/services/order_service.py`

장바구니 기반 주문 생성, 취소, 재고/포인트 처리의 핵심 비즈니스 로직을 담당합니다.

**`create_order_from_cart()`**
- **책임**: 장바구니를 주문으로 전환하고 재고를 차감
- **Side Effect**: 장바구니 비우기, 재고 차감, 포인트 사용 처리
- **경계**: 주문 시점 적립률/등급 스냅샷 저장, 동일 장바구니 중복 주문 방지
- **결과**: 주문 레코드 생성, 재고 차감 완료, 장바구니 비워짐

**`create_order_hybrid()`**
- **책임**: 주문 레코드를 빠르게 생성하고 무거운 작업을 백그라운드로 위임
- **Side Effect**: 장바구니 즉시 비활성화
- **경계**: 재고/포인트 처리는 후처리로 위임
- **결과**: 사용자는 즉시 주문 ID 수신, UI 지연 없음

**`cancel_order()`**
- **책임**: 주문을 취소 상태로 전환하고 재고/포인트 복구
- **Side Effect**: 재고 복구, 판매량 차감(결제 완료 주문만), 포인트 환불/회수
- **경계**: 배송 전 주문만 취소 가능
- **결과**: 주문 취소 완료, 재고 및 포인트 원상복구

---

#### PaymentService `shopping/services/payment_service.py`

결제 생성, 승인, 취소의 핵심 비즈니스 로직을 담당합니다.

**`create_payment()`**
- **책임**: 주문에 대한 결제 정보를 생성
- **Side Effect**: 기존 결제 정보 삭제 (재시도 시), 결제 요청 로그 기록
- **경계**: 짧은 시간 내 중복 요청 시 기존 결제 반환
- **결과**: 결제창 호출에 필요한 결제 정보

**`confirm_payment_sync()`**
- **책임**: 결제 승인 완료 및 후처리 완결
- **Side Effect**: 판매량 증가, 주문 상태 변경, 장바구니 비활성화, 포인트 적립
- **경계**: 중복 승인 방지, 이미 완료된 결제는 오류 반환
- **결과**: 결제 완료, 포인트 적립 완료

**`confirm_payment_async()`**
- **책임**: 결제 승인을 백그라운드로 위임하고 즉시 응답
- **Side Effect**: 결제 상태를 처리 중으로 변경
- **경계**: 승인 처리는 후처리로 위임, 중복 요청 차단
- **결과**: 사용자는 즉시 응답 수신, UI 지연 없음

**`cancel_payment()`**
- **책임**: 결제 취소 및 재고/포인트 복구
- **Side Effect**: 재고 복구, 판매량 차감, 주문 상태 변경, 포인트 환불/회수
- **경계**: 중복 취소 방지, 포인트 부족 시 취소 불가
- **결과**: 결제 취소 완료, 재고 및 포인트 원상복구

---

#### ShippingService `shopping/services/shipping_service.py`

배송비 계산 및 도서산간 지역 판별을 담당합니다.

**`calculate_fee()`**
- **책임**: 주문 금액과 배송지에 따른 배송비를 계산
- **경계**: 무료배송 기준 금액, 도서산간 추가 배송비 정책 적용
- **결과**: 기본 배송비, 추가 배송비, 무료배송 여부 반환

**`is_remote_area()`**
- **책임**: 우편번호로 도서산간 지역 여부를 판별
- **결과**: 도서산간 지역이면 True 반환

---

#### ReturnService `shopping/services/return_service.py`

교환/환불 신청, 승인, 거부, 완료 처리의 핵심 비즈니스 로직을 담당합니다.

**`validate_order_for_return()`**
- **책임**: 주문이 교환/환불 신청 가능한 상태인지 검증
- **경계**: 배송 완료 후 7일 이내, 이미 처리 중인 신청 없음
- **결과**: 검증 통과 또는 거부 사유 반환

**`validate_return_items()`**
- **책임**: 반품 상품 목록이 유효한지 검증
- **경계**: 주문에 속한 상품인지, 수량 초과 여부 확인
- **결과**: 검증된 OrderItem 목록 반환

**`generate_return_number()`**
- **책임**: 고유한 교환/환불 번호를 생성
- **경계**: 중복 번호 발급 방지
- **결과**: RET + 날짜 + 일련번호 형식의 고유 번호

**`create_return()`**
- **책임**: 교환/환불 신청을 생성하고 신청 상태로 확정
- **Side Effect**: 반품 상품 목록 생성, 환불 금액 계산
- **경계**: 교환 시 교환 상품 재고 사전 확인 (최종 확인은 완료 시점)
- **결과**: 신청 완료, 판매자 승인 대기 상태

**`approve_return()`**
- **책임**: 교환/환불 신청을 승인 상태로 전환
- **Side Effect**: 고객에게 승인 알림 발송
- **경계**: 신청 상태에서만 승인 가능
- **결과**: 상태가 승인으로 변경, 고객은 반품 안내 수신

**`reject_return()`**
- **책임**: 교환/환불 신청을 거부 상태로 전환
- **Side Effect**: 고객에게 거부 알림 발송 (거부 사유 포함)
- **경계**: 신청 상태에서만 거부 가능
- **결과**: 상태가 거부로 변경, 거부 사유 기록

**`confirm_receive_return()`**
- **책임**: 반품 상품 도착을 확인하고 상태를 전환
- **Side Effect**: 고객에게 도착 확인 알림 발송
- **경계**: 배송 중 상태에서만 확인 가능
- **결과**: 상태가 도착완료로 변경, 완료 처리 가능

**`complete_refund()`**
- **책임**: 환불을 완료하고 재고/포인트를 원상복구
- **Side Effect**: 결제사 환불 처리, 재고 복구, 포인트 환불/회수, 주문 상태 변경
- **경계**: 반품 도착 상태에서만 완료 가능, 포인트 부족 시 오류 반환
- **결과**: 환불 완료, 재고 및 포인트 원상복구, 고객에게 완료 알림

**`complete_exchange()`**
- **책임**: 교환을 완료하고 재고를 조정
- **Side Effect**: 교환 상품 재고 차감, 반품 상품 재고 증가, 송장번호 기록
- **경계**: 반품 도착 상태에서만 완료 가능, 교환 상품 재고 부족 시 오류 반환
- **결과**: 교환 완료, 송장번호 포함 알림 발송

---

### 2.2.5 포인트/알림 서비스

---

#### PointService `shopping/services/point_service.py`

포인트 적립, 사용, 만료 처리의 핵심 비즈니스 로직을 담당합니다.

**`add_points()`**
- **책임**: 사용자에게 포인트를 적립하고 이력 생성
- **Side Effect**: 사용자 잔액 증가, 적립 이력 생성
- **결과**: 포인트 적립 완료, 만료일 설정 (1년)

**`use_points_fifo()`**
- **책임**: 먼저 적립된 포인트부터 차감 (FIFO)
- **Side Effect**: 사용자 잔액 감소, 사용 이력 생성
- **경계**: 만료되지 않은 포인트만 차감 대상
- **결과**: 만료 임박 포인트부터 소진, 사용 상세 내역 반환

**`expire_points()`**
- **책임**: 만료된 포인트를 일괄 소멸
- **Side Effect**: 사용자 잔액 감소, 만료 이력 생성
- **경계**: 알림 발송은 별도 처리로 위임
- **결과**: 만료 포인트 자동 정리

**`send_expiry_notifications()`**
- **책임**: 만료 예정 포인트에 대한 알림 발송
- **결과**: 사용자가 만료 예정 포인트 인지

| 기타 메서드 | 설명 |
|--------|------|
| `use_points()` | 포인트 사용 (단순 차감) |
| `get_expired_points()` | 만료된 포인트 조회 |
| `get_expiring_points_soon()` | 만료 예정 포인트 조회 |
| `get_remaining_points()` | 특정 적립 건의 남은 포인트 계산 |
| `get_usable_points()` | 원장 기준 사용 가능 포인트 조회 |

---

#### PointQueryService `shopping/services/point_query_service.py`

포인트 조회 및 통계 관련 읽기 전용 쿼리를 담당합니다.

**`get_filtered_history()`**
- **책임**: 필터 조건에 맞는 포인트 이력을 페이지네이션하여 조회
- **경계**: 타입별, 기간별 필터링 지원
- **결과**: 페이지네이션된 이력 목록 반환

**`get_history_summary()`**
- **책임**: 포인트 이력의 적립/사용 합계를 계산
- **결과**: 현재 잔액, 총 적립, 총 사용 반환

**`get_monthly_expiring_summary()`**
- **책임**: 월별 만료 예정 포인트를 집계하여 요약
- **경계**: 지정 기간 내 만료 예정만 조회
- **결과**: 월별 만료 예정 포인트 및 총합 반환

**`get_point_statistics()`**
- **책임**: 포인트 종합 통계를 계산
- **경계**: 이번달/전체 기간/타입별 통계 통합
- **결과**: 대시보드용 종합 통계 반환

| 기타 메서드 | 설명 |
|--------|------|
| `get_recent_histories()` | 최근 N개 이력 조회 |
| `build_filter_from_request()` | Request에서 필터 조건 추출 |

> **Data Classes**: `PointHistoryFilter`, `PaginatedResult`, `HistorySummary`, `MonthlyExpiringSummary`, `PointStatistics`

---

#### NotificationService `shopping/services/notification_service.py`

알림 생성, 조회, 읽음 처리의 핵심 비즈니스 로직을 담당합니다.

**`create()`**
- **책임**: 사용자에게 알림을 생성하고 저장
- **Side Effect**: 알림 레코드 생성
- **결과**: 사용자가 알림 목록에서 새 알림 확인 가능

**`bulk_create()`**
- **책임**: 다수 사용자에게 동일 알림을 일괄 생성
- **Side Effect**: 알림 레코드 일괄 생성
- **경계**: 개별 생성 대비 효율적 처리
- **결과**: 대량 알림 발송 완료 (시스템 공지 등)

**`mark_as_read()`**
- **책임**: 사용자의 알림을 읽음 상태로 전환
- **Side Effect**: 읽음 상태 갱신
- **경계**: 특정 ID 목록 또는 전체 일괄 처리 가능
- **결과**: 읽음 처리 완료, 읽지 않은 알림 개수 감소

**`clear_read()`**
- **책임**: 읽은 알림을 일괄 삭제
- **Side Effect**: 읽은 알림 레코드 삭제
- **결과**: 알림 목록 정리 완료

| 조회 메서드 | 설명 |
|--------|------|
| `get_queryset()` | 사용자의 알림 목록 반환 (최신순) |
| `get_unread()` | 읽지 않은 알림 조회 (미리보기용) |
| `get_by_id()` | ID로 단일 알림 조회 |
| `get_unread_count()` | 읽지 않은 알림 개수 반환 |
| `mark_single_as_read()` | 단일 알림 읽음 처리 |
| `delete_by_id()` | ID로 단일 알림 삭제 |

> **Data Classes**: `UnreadResult`, `MarkReadResult`, `ClearResult`

---

### 2.2.6 외부 연동 서비스

---

#### TossWebhookService `shopping/services/toss_webhook_service.py`

토스페이먼츠 웹훅 이벤트 처리 및 중복 방어의 핵심 비즈니스 로직을 담당합니다.

**`handle_payment_done()`**
- **책임**: 결제 완료 웹훅을 수신하여 주문을 결제 완료 상태로 확정
- **Side Effect**: 판매량 증가, 주문 상태 변경, 장바구니 비활성화, 포인트 적립
- **경계**: 결제 승인 요청과 중복 처리 방지, 이미 완료/취소된 결제는 무시
- **결과**: 주문 상태가 paid로 확정, 포인트 자동 적립

**`handle_payment_canceled()`**
- **책임**: 결제 취소 웹훅을 수신하여 주문을 취소 상태로 전환
- **Side Effect**: 재고 복구, 판매량 차감, 포인트 회수
- **경계**: 이미 취소된 결제는 무시, 실패 상태 결제는 무시
- **결과**: 주문 취소 완료, 재고 및 포인트 원상복구

**`handle_payment_failed()`**
- **책임**: 결제 실패 웹훅을 수신하여 결제를 실패 상태로 전환
- **Side Effect**: 결제 실패 사유 기록
- **경계**: 이미 완료/취소된 결제는 무시
- **결과**: 결제 실패 상태로 기록

| 유틸리티 메서드 | 설명 |
|--------|------|
| `is_webhook_duplicate()` | 웹훅 중복 여부 확인 |
| `mark_webhook_processed()` | 웹훅 처리 완료 마킹 |
| `log_webhook_event()` | 웹훅 이벤트 감사 로그 기록 |

---

### 2.2.7 Tasks

> 비동기 백그라운드 작업 `shopping/tasks/`

---

#### Cleanup Tasks `shopping/tasks/cleanup_tasks.py`

시스템 데이터 정리 및 만료 처리의 주기적 백그라운드 작업을 담당합니다.

**`delete_unverified_users_task()`**
- **책임**: 일정 기간 미인증 계정을 자동 삭제
- **경계**: 주문 이력이 있는 사용자는 유지
- **결과**: 가입만 하고 방치된 계정 제거

**`cleanup_old_email_logs_task()`**
- **책임**: 오래된 이메일 발송 로그를 자동 삭제
- **경계**: pending 상태는 유지
- **결과**: 완료된 이메일 로그 주기적 정리

**`cleanup_used_tokens_task()`**
- **책임**: 사용 완료된 인증 토큰을 자동 삭제
- **결과**: 사용된 토큰 데이터 주기적 정리

**`cleanup_expired_tokens_task()`**
- **책임**: 만료된 미사용 토큰을 자동 삭제
- **결과**: 만료 토큰 데이터 주기적 정리

---

#### Email Tasks `shopping/tasks/email_tasks.py`

이메일 발송 처리의 백그라운드 작업을 담당합니다.

**`send_verification_email_task()`**
- **책임**: 인증 이메일을 백그라운드에서 발송
- **경계**: 이미 발송된 이메일은 중복 발송 방지, 실패 시 자동 재시도(최대 3회)
- **결과**: 사용자는 즉시 가입 완료 응답 수신, 인증 이메일은 지연 없이 백그라운드 발송

**`retry_failed_emails_task()`**
- **책임**: 24시간 이내 발송 실패한 인증 이메일을 주기적으로 재시도
- **경계**: 만료된 토큰 및 이미 인증 완료된 사용자는 스킵
- **결과**: 일시적 네트워크 오류로 실패한 이메일 자동 복구

**`send_email_task()`**
- **책임**: 범용 이메일(비밀번호 재설정, 일반 알림 등)을 백그라운드에서 발송
- **경계**: 실패 시 자동 재시도(최대 3회), 중복 발송 방지
- **결과**: 호출자는 즉시 응답 수신, 이메일은 백그라운드 발송

---

#### Order Tasks `shopping/tasks/order_tasks.py`

주문 후처리의 백그라운드 작업을 담당합니다.

**`process_order_heavy_tasks()`**
- **책임**: 주문 생성 후 무거운 작업을 백그라운드에서 처리하여 UI 지연 방지
- **Side Effect**: 재고 차감, 포인트 사용, OrderItem 생성, 장바구니 비우기, 주문 상태 confirmed로 변경
- **경계**: 재고 부족 시 주문 실패 처리, 포인트 부족 시 재고 복구 후 실패 처리, 이미 처리된 주문은 중복 처리 방지
- **결과**: 사용자는 즉시 주문 ID 수신, 무거운 처리는 백그라운드 진행, 실패 시 복구 처리 완료

---

#### Payment Tasks `shopping/tasks/payment_tasks.py`

결제 처리의 백그라운드 작업을 담당합니다.

**`call_toss_confirm_api()`**
- **책임**: 외부 결제 승인 API 호출을 분리하여 타임아웃(10초) 및 재시도 관리
- **경계**: 네트워크/타임아웃 오류 시 자동 재시도(최대 3회), DB 작업 없이 API 호출만 담당
- **결과**: 결제 승인 성공 시 외부 API 응답 반환, 실패 시 에러 로그 기록 및 결제 상태 aborted로 변경

**`finalize_payment_confirm()`**
- **책임**: 외부 API 승인 결과를 반영하여 결제 최종 상태 확정
- **Side Effect**: 판매량(sold_count) 증가, 주문 상태 paid로 변경, 장바구니 비활성화, 결제 로그 기록, 포인트 적립 트리거
- **경계**: 이미 완료된 결제는 중복 처리 방지, 포인트 적립은 별도 후처리로 위임
- **결과**: 결제 완료 상태 확정, 사용자는 즉시 결제 완료 확인

---

#### Point Tasks `shopping/tasks/point_tasks.py`

포인트 처리의 백그라운드 작업을 담당합니다.

**`expire_points_task()`**
- **책임**: 만료된 포인트를 일괄 소멸 (매일 새벽 2시 실행)
- **경계**: PointService.expire_points()로 위임
- **결과**: 만료 포인트 자동 정리, 만료 건수 반환

**`send_expiry_notification_task()`**
- **책임**: 만료 예정 포인트에 대한 알림 발송 (매일 오전 10시 실행)
- **경계**: PointService.send_expiry_notifications()로 위임
- **결과**: 사용자가 만료 예정 포인트 사전 인지, 발송 건수 반환

**`send_email_notification()`**
- **책임**: 이메일 알림을 백그라운드에서 발송
- **경계**: 실패 시 자동 재시도(최대 5회, 2분 간격)
- **결과**: 이메일 발송 성공 여부 반환

**`add_points_after_payment()`**
- **책임**: 결제 완료 후 포인트를 백그라운드에서 적립하여 UI 지연 방지
- **Side Effect**: 등급별 적립률 적용, 적립 이력 생성, 주문에 적립 포인트(earned_points) 기록
- **경계**: 포인트 전액 결제(final_amount ≤ 0)는 적립 제외, 배송비 제외한 상품 금액 기준 적립
- **결과**: 사용자는 즉시 결제 완료 확인, 포인트 적립은 백그라운드 진행

**`process_single_user_points()`**
- **책임**: 특정 사용자의 만료 포인트를 개별 처리
- **경계**: 관리자 수동 실행 또는 특정 이벤트 시 사용
- **결과**: 개별 사용자 포인트 만료 처리 완료

**`cleanup_old_point_histories()`**
- **책임**: 오래된 만료 이력(기본 2년)을 자동 삭제
- **경계**: 만료(expire) 타입 이력만 삭제 대상
- **결과**: 만료 이력 데이터 주기적 정리, 삭제 건수 반환

---

### 2.2.8 Webhooks

> 외부 서비스 연동 `shopping/webhooks/`

---

#### Toss Webhook View `shopping/webhooks/toss_webhook_view.py`

토스페이먼츠 결제 상태 변경 알림을 수신하고 처리하는 웹훅 엔드포인트입니다.

**`toss_webhook()`**
- **책임**: 토스페이먼츠 결제 상태 변경을 수신하여 시스템에 반영
- **Side Effect**: 서명 검증 후 이벤트 타입에 따라 적절한 핸들러 호출
- **경계**: 서명 검증 실패 시 401 반환, 지원하지 않는 이벤트는 무시, 실제 처리는 TossWebhookService로 위임
- **결과**: 외부 결제사 재전송에도 중복 처리 없이 상태 동기화

| 지원 이벤트 | 설명 |
|------------|------|
| `PAYMENT.DONE` | 결제 완료 → 주문 상태 paid로 확정 |
| `PAYMENT.CANCELED` | 결제 취소 → 재고/포인트 원상복구 |
| `PAYMENT.FAILED` | 결제 실패 → 실패 상태 기록 |

---

#### TossWebhookService `shopping/services/toss_webhook_service.py`

각 웹훅 이벤트에 대한 실제 처리 로직을 담당합니다.

**`handle_payment_done()`**
- **책임**: 결제 완료 웹훅을 수신하여 주문을 결제 완료 상태로 확정
- **Side Effect**: 판매량 증가, 주문 상태 paid로 변경, 장바구니 비활성화, 포인트 적립
- **경계**: 동일 웹훅 중복 차단, 이미 paid 상태면 스킵, 취소/실패 상태 결제는 무시
- **결과**: 주문 상태가 paid로 확정, 포인트 자동 적립

**`handle_payment_canceled()`**
- **책임**: 결제 취소 웹훅을 수신하여 주문을 취소 상태로 전환
- **Side Effect**: 재고 복구, 판매량 차감, 적립 포인트 회수, 주문 상태 canceled로 변경
- **경계**: 동일 웹훅 중복 차단, 이미 취소된 결제는 스킵, 실패 상태 결제는 무시
- **결과**: 주문 취소 완료, 재고 및 포인트 원상복구

**`handle_payment_failed()`**
- **책임**: 결제 실패 웹훅을 수신하여 결제를 실패 상태로 전환
- **Side Effect**: 결제 실패 사유 기록
- **경계**: 이미 실패 상태면 스킵, 완료/취소된 결제는 무시
- **결과**: 결제 실패 상태로 기록

| 유틸리티 메서드 | 설명 |
|----------------|------|
| `is_webhook_duplicate()` | 웹훅 중복 여부 확인 |
| `mark_webhook_processed()` | 웹훅 처리 완료 마킹 |
| `log_webhook_event()` | 웹훅 이벤트 감사 로그 기록 |

---

### 2.2.9 Base Service

#### ServiceError `shopping/services/base.py`

| 항목 | 설명 |
|------|------|
| `ServiceError` | 서비스 에러 베이스 클래스 |
| `@log_service_call` | 서비스 호출 로깅 데코레이터 |

---

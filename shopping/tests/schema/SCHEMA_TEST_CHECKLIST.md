# 📋 스키마 테스트 커버리지 체크리스트

> 이 문서는 OpenAPI 스키마 기반 API 테스트의 완성도를 점검하기 위한 지침입니다.
> 새로운 API 추가 시 또는 정기 점검 시 이 체크리스트를 사용하세요.

---

## 🎯 테스트 커버리지 요약

| 카테고리 | 파일 위치 | 테스트 수 | 상태 |
|----------|-----------|-----------|------|
| Contract (계약) | `contract/` | 6개 파일 | ✅ |
| Validation (검증) | `validation/` | 4개 파일 | ✅ |
| Workflow (워크플로우) | `workflow/` | 2개 파일 | ✅ |
| Stress (스트레스) | `stress/` | 4개 파일 | ✅ |

---

## 1️⃣ Contract 테스트 (API 계약 검증)

### 1.1 공개 엔드포인트 (`test_public_endpoints.py`)
- [x] 상품 목록 조회 (`GET /api/products/`)
- [x] 상품 상세 조회 (`GET /api/products/{id}/`)
- [x] 카테고리 목록 조회 (`GET /api/categories/`)
- [x] 카테고리 상세 조회 (`GET /api/categories/{id}/`)
- [x] 카테고리 트리 조회 (`GET /api/categories/tree/`)
- [x] 인기 상품 (`GET /api/products/popular/`)
- [x] 평점순 상품 (`GET /api/products/best_rating/`)

### 1.2 인증 필요 엔드포인트 (`test_authenticated_endpoints.py`)
- [x] 장바구니 조회 (`GET /api/cart/`)
- [x] 장바구니 요약 (`GET /api/cart/summary/`)
- [x] 장바구니 아이템 목록 (`GET /api/cart/items/`)
- [x] 주문 목록 (`GET /api/orders/`)
- [x] 주문 상세 (`GET /api/orders/{id}/`)
- [x] 위시리스트 목록 (`GET /api/wishlist/`)
- [x] 위시리스트 통계 (`GET /api/wishlist/stats/`)
- [x] 알림 목록 (`GET /api/notifications/`)
- [x] 사용자 프로필 (`GET /api/users/profile/`)
- [x] 결제 목록 (`GET /api/payments/`)
- [x] 교환/환불 목록 (`GET /api/returns/`)
- [x] 내 포인트 (`GET /api/points/my/`)
- [x] 재고 부족 상품 (`GET /api/products/low_stock/`) - 판매자
- [x] 판매자 반품 목록 (`GET /api/seller/returns/`) - 판매자

### 1.3 POST 엔드포인트 (`test_post_endpoints.py`)
- [x] 장바구니 상품 추가 (`POST /api/cart/add_item/`)
- [x] 위시리스트 토글 (`POST /api/wishlist/toggle/`)
- [x] 로그인 성공 (`POST /api/auth/login/`)
- [x] 로그인 실패
- [x] 회원가입 성공 (`POST /api/auth/register/`)
- [x] 회원가입 실패
- [x] 중복 사용자 등록 실패
- [x] 비밀번호 불일치 실패
- [x] 재고 없는 상품 추가 실패
- [x] 재고 초과 수량 추가 실패
- [x] 잘못된 데이터 타입 실패
- [x] 빈 장바구니 주문 실패

### 1.4 에러 응답 (`test_error_responses.py`)
- [x] 로그인 실패 - 잘못된 비밀번호 → 400/401
- [x] 로그인 실패 - 빈 필드 → 400/401
- [x] 회원가입 실패 - 유효하지 않은 데이터 → 400
- [x] 장바구니 추가 실패 - 필수 필드 누락 → 400
- [x] 장바구니 추가 실패 - 잘못된 수량 → 400
- [x] 장바구니 추가 실패 - 존재하지 않는 상품 → 400/404
- [x] 인증 없이 보호된 엔드포인트 접근 → 401
- [x] 위시리스트 토글 실패 - product_id 누락 → 400

### 1.5 Path Parameter (`test_path_parameters.py`)
- [x] 유효한 상품 ID → 200
- [x] 존재하지 않는 상품 ID → 404
- [x] 음수 ID → 400/404
- [x] 문자열 ID → 400/404
- [x] float ID → 400/404
- [x] SQL Injection 시도 → 400/404
- [x] 인증 없이 주문 상세 접근 → 401
- [x] 유효한 주문 ID (인증됨) → 200
- [x] 유효한 카테고리 ID → 200
- [x] 존재하지 않는 알림 ID → 404
- [x] 존재하지 않는 결제 ID → 404

### 1.6 Query Parameter (`test_query_parameters.py`)
- [x] 잘못된 페이지 파라미터 (음수, 0, 큰 수, 문자열)
- [x] 악성 검색어 (SQL Injection, XSS, Path Traversal)
- [x] 잘못된 정렬 파라미터
- [x] 복합 쿼리 파라미터
- [x] 카테고리 필터 파라미터

---

## 2️⃣ Validation 테스트 (스키마 검증)

### 2.1 스키마 발견 (`test_schema_discovery.py`)
- [x] OpenAPI 스키마 유효성
- [x] 스키마에 경로 정의 존재
- [x] 핵심 엔드포인트 존재 확인

### 2.2 Schemathesis 네이티브 검증 (`test_schemathesis_native.py`)
- [x] 공개 엔드포인트 스키마 일치
- [x] 인증 엔드포인트 스키마 일치
- [x] 에러 응답 스키마 일치

### 2.3 Strict 검증 (`test_strict_validation.py`)
- [x] 상품 상세 Strict 스키마 (추가 필드 없음)
- [x] 상품 목록 각 아이템 스키마

### 2.4 전체 스키마 검증 (`test_full_schema_validation.py`) `@slow`
- [x] 모든 GET 엔드포인트 5xx 에러 없음
- [x] 모든 GET 엔드포인트 유효한 JSON 반환
- [x] 모든 성공 응답 유효한 구조 (dict/list)

---

## 3️⃣ Workflow 테스트 (상태 전이 검증)

### 3.1 사용자 플로우 (`test_stateful_workflow.py`)
- [x] 완전한 구매 플로우 (비회원→회원가입→로그인→장바구니→주문조회)
- [x] 비인증 사용자 보호된 엔드포인트 접근 차단
- [x] 인증된 사용자 보호된 엔드포인트 접근 허용

### 3.2 위시리스트 플로우 (`test_stateful_workflow.py`)
- [x] 위시리스트 토글 (추가/제거)
- [x] 위시리스트 → 장바구니 이동

### 3.3 상품 탐색 플로우 (`test_stateful_workflow.py`)
- [x] 카테고리 트리 → 카테고리 상세 → 상품 목록 → 상품 상세
- [x] 상품 검색 및 필터링 (검색어, 가격, 인기, 평점)

### 3.4 장바구니 관리 플로우 (`test_stateful_workflow.py`)
- [x] 장바구니 아이템 생명주기 (추가→수량변경→재고확인→비우기)
- [x] 대량 작업 (bulk_add)

### 3.5 알림/포인트 플로우 (`test_stateful_workflow.py`)
- [x] 알림 조회 플로우
- [x] 포인트 조회 플로우

### 3.6 상태 머신 (`test_stateful_workflow.py`)
- [x] ANONYMOUS → AUTHENTICATED → HAS_CART 전이
- [x] 잘못된 상태 전이 차단 (비인증 상태에서 보호된 리소스 접근)

### 3.7 복잡한 워크플로우 (`test_stateful_workflow.py`) `@slow`
- [x] 완전한 쇼핑 경험 시뮬레이션

### 3.8 접근 제어 (`test_access_control.py`)
- [x] 타인의 주문 접근 → 403/404
- [x] 일반 사용자가 판매자 API 접근 → 403 또는 빈 결과
- [x] 비활성 상품 접근 → 404

---

## 4️⃣ Stress 테스트 (스트레스/부하 검증)

### 4.1 동시성 테스트 (`test_concurrency.py`)
- [x] 같은 상품 동시 장바구니 추가 - 수량 정확성
- [x] 재고 한도 동시 추가 - 재고 초과 방지
- [x] 여러 상품 동시 추가 `@slow`
- [x] 동시 주문 시 재고 음수 방지
- [x] 재고 1개 상품 동시 구매 `@slow`
- [x] 동시 상품 목록 조회 - 응답 일관성
- [x] 동시 로그인 - 토큰 발급 + JTI 고유성
- [x] 혼합 동시 요청 (읽기+쓰기) `@slow`

### 4.2 Fuzz 테스트 (`test_fuzz.py`)
- [x] 상품 검색 퍼징 (SQL Injection, XSS, 긴 문자열)
- [x] 페이지네이션 퍼징 (음수, 0, 큰 수, 문자열)
- [x] 정렬 파라미터 퍼징
- [x] 상품 ID 퍼징
- [x] 장바구니 추가 퍼징 (product_id, quantity)
- [x] 장바구니 수량 변경 퍼징
- [x] 로그인 자격 증명 퍼징
- [x] 토큰 갱신 퍼징
- [x] 카테고리 ID 퍼징
- [x] 전체 GET 엔드포인트 5xx 없음 `@slow`
- [x] 복합 파라미터 퍼징 `@slow`

### 4.3 Negative 테스트 (`test_negative.py`)
- [x] 잘못된 상품 ID 형식 (문자열, 음수, 0 등)
- [x] 필수 필드 누락 (장바구니 추가)
- [x] 잘못된 수량 값
- [x] 잘못된 로그인 데이터
- [x] SQL Injection 시도
- [x] XSS 시도 (검색, POST 데이터)
- [x] Path Traversal 시도
- [x] 페이지네이션 경계값
- [x] 매우 긴 검색어
- [x] 특수 문자 검색어
- [x] 잘못된 JSON 형식
- [x] 잘못된 Content-Type
- [x] 예상치 못한 필드 포함
- [x] 잘못된 인증 헤더
- [x] 다른 사용자의 주문 접근
- [x] 다른 사용자의 장바구니 접근

### 4.4 성능 테스트 (`test_performance.py`)
- [x] 상품 목록 응답 시간 < 500ms
- [x] 상품 상세 응답 시간 < 200ms
- [x] 카테고리 목록 응답 시간 < 300ms
- [x] 인기 상품 응답 시간 < 800ms `@slow`
- [x] 로그인 응답 시간 < 500ms
- [x] 토큰 갱신 응답 시간 < 300ms
- [x] 장바구니 조회 응답 시간 < 400ms
- [x] 장바구니 추가 응답 시간 < 500ms
- [x] 주문 목록 응답 시간 < 600ms `@slow`
- [x] 상품 목록 쿼리 수 (N+1 방지)
- [x] 첫 페이지 vs 마지막 페이지 성능 비교
- [x] 큰 page_size 요청 성능 `@slow`

---

## 5️⃣ 제외된 엔드포인트 (의도적 제외)

다음 엔드포인트는 테스트에서 의도적으로 제외되었습니다:

| 엔드포인트 | 제외 사유 |
|------------|-----------|
| `/api/webhooks/toss/` | 외부 서비스 콜백 (Toss Payments) |
| `/api/auth/password/reset/request/` | 실제 이메일 발송 |
| `/api/auth/password/reset/confirm/` | 실제 이메일 발송 |
| `/api/auth/social/*` | 외부 OAuth 의존 |
| `/api/payment/test/` | HTML 테스트 페이지 |
| `/api/schema/` | OpenAPI 스키마 자체 |

---

## 🔍 새 API 추가 시 체크리스트

새로운 API 엔드포인트를 추가할 때 다음을 확인하세요:

### 필수 항목
- [ ] **Contract 테스트**: 성공 응답 스키마 검증
- [ ] **Contract 테스트**: 에러 응답 스키마 검증
- [ ] **Path Parameter**: ID 형식 검증 (해당 시)
- [ ] **Query Parameter**: 필터/정렬 검증 (해당 시)
- [ ] **인증 테스트**: 인증 필요 시 401 반환 확인

### 권장 항목
- [ ] **Workflow 테스트**: 관련 사용자 시나리오에 추가
- [ ] **Fuzz 테스트**: 무작위 입력 내성 확인
- [ ] **Negative 테스트**: 의도적 잘못된 입력 처리
- [ ] **성능 테스트**: 응답 시간 임계값 정의

### POST/PUT/PATCH/DELETE 추가 시
- [ ] 성공 케이스 (200/201/204)
- [ ] 필수 필드 누락 (400)
- [ ] 잘못된 데이터 타입 (400)
- [ ] 권한 없음 (403)
- [ ] 리소스 없음 (404)
- [ ] 비즈니스 규칙 위반 (400)

---

## 📊 테스트 실행 명령어

```bash
# 전체 스키마 테스트
pytest shopping/tests/schema/ -v -m schema

# 빠른 테스트만 (slow 제외)
pytest shopping/tests/schema/ -v -m "schema and not slow"

# 카테고리별 실행
pytest shopping/tests/schema/contract/ -v          # Contract
pytest shopping/tests/schema/validation/ -v        # Validation
pytest shopping/tests/schema/workflow/ -v          # Workflow
pytest shopping/tests/schema/stress/ -v            # Stress

# 마커별 실행
pytest -m fuzz -v --no-cov -n 0                    # Fuzz
pytest -m negative -v --no-cov                     # Negative
pytest -m concurrency -v --no-cov -n 0             # Concurrency
pytest -m performance -v --no-cov -n 0             # Performance
pytest -m stateful -v --no-cov -n 0                # Stateful
```

---

## 📅 정기 점검 일정

| 점검 항목 | 주기 | 담당 |
|-----------|------|------|
| 전체 스키마 테스트 실행 | 매 PR | CI/CD |
| slow 테스트 포함 전체 실행 | 주 1회 | CI/CD (Nightly) |
| 커버리지 체크리스트 검토 | 월 1회 | 개발팀 |
| 새 API 테스트 추가 확인 | API 추가 시 | 담당 개발자 |

---

## ✅ 현재 커버리지 상태

**마지막 검토일**: 2025-12-05

| 영역 | 커버리지 | 비고 |
|------|----------|------|
| 공개 API Contract | 100% | ✅ |
| 인증 API Contract | 100% | ✅ |
| POST API Contract | 100% | ✅ |
| 에러 응답 Contract | 100% | ✅ |
| Path Parameter Edge Cases | 100% | ✅ |
| Query Parameter Edge Cases | 100% | ✅ |
| 스키마 유효성 검증 | 100% | ✅ |
| 워크플로우 상태 전이 | 100% | ✅ |
| 접근 제어 | 100% | ✅ |
| 동시성 | 100% | ✅ |
| Fuzz 테스트 | 100% | ✅ |
| Negative 테스트 | 100% | ✅ |
| 성능 임계값 | 100% | ✅ |

**종합 평가**: 🟢 **완료** - 모든 핵심 영역 커버됨

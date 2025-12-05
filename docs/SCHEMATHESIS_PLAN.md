# 📋 Schemathesis 도입 계획

## 🎯 현재 상태 분석

| 항목 | 상태 | 비고 |
|------|------|------|
| OpenAPI 스키마 | ✅ 준비됨 | `drf-spectacular` 사용 |
| 스키마 엔드포인트 | ✅ 활성화 | `/api/schema/` |
| 기존 테스트 | ✅ 풍부함 | pytest 기반, api/unit/integration 분리 |
| Factory | ✅ 구성됨 | `factory-boy`, `conftest.py` fixtures |

## 📊 진행 상태

| Phase | 상태 | 완료일 |
|-------|------|--------|
| Phase 1: 기본 설정 | ✅ 완료 | 2025-12-05 |
| Phase 2: Contract 테스트 | ✅ 완료 | 2025-12-05 |
| Phase 3: Stateful 테스트 | ✅ 완료 | 2025-12-05 |
| Phase 4: CI 통합 | 🔲 예정 | - |
| Phase 5: 고급 활용 | 🔲 선택 | - |

---

## 📌 Phase 1: 기본 설정 (1일) ✅ 완료

### 1.1 패키지 설치 ✅
```bash
# requirements-dev.txt에 추가됨
schemathesis==4.6.7  # 최신 버전으로 설치됨
```

### 1.2 기본 테스트 파일 생성 ✅
```
shopping/tests/
└── schema/           # 생성됨
    ├── __init__.py
    ├── conftest.py   # Schemathesis 전용 설정
    └── test_api_contract.py
```

### 1.3 고려사항 ✅ 구현됨
- **인증 처리**: JWT Bearer 토큰 설정 (`auth_token`, `auth_headers` fixture)
- **테스트 DB**: `pytest-django`와 통합
- **스키마 생성**: Django Test Client로 런타임 로드

---

## 📌 Phase 2: 기본 Contract 테스트 (2-3일) ✅ 완료

### 2.1 구현된 테스트 클래스

#### TestPublicEndpoints (공개 API)
- `test_products_list` - 상품 목록 API
- `test_categories_list` - 카테고리 목록 API
- `test_products_detail` - 상품 상세 API
- `test_categories_detail` - 카테고리 상세 API
- `test_categories_tree` - 카테고리 트리 API

#### TestAuthenticatedEndpoints (인증 필요)
- `test_cart_*` - 장바구니 API (retrieve, summary, items)
- `test_orders_*` - 주문 API (list, detail)
- `test_wishlist_*` - 위시리스트 API (list, stats)
- `test_notifications_*` - 알림 API (list, unread)
- `test_my_questions` - 내 문의 목록
- `test_payments_list` - 결제 목록
- `test_returns_list` - 교환/환불 목록
- `test_user_profile` - 사용자 프로필
- `test_points_*` - 포인트 API (my, history)

#### TestAuthenticationRequired (인증 검증)
- 7개 핵심 엔드포인트에 대한 401 응답 검증

#### TestPostEndpoints (생성 API)
- `test_cart_add_item` - 장바구니 상품 추가
- `test_wishlist_toggle` - 위시리스트 토글
- `test_auth_login` - 로그인 API
- `test_auth_register_validation` - 회원가입 유효성 검증

#### TestFullSchemaValidation (전체 검증)
- `test_all_get_endpoints_no_5xx` - 모든 GET에서 5xx 에러 없음
- `test_all_endpoints_return_valid_json` - 유효한 JSON 반환

#### TestSchemaDiscovery (스키마 발견)
- `test_schema_is_valid` - 스키마 유효성
- `test_schema_has_paths` - 경로 존재
- `test_critical_endpoints_exist` - 핵심 엔드포인트 존재

### 2.2 제외된 엔드포인트 (10개)
```python
EXCLUDED_ENDPOINTS = [
    "/api/webhooks/toss/",           # 외부 서비스 콜백
    "/api/auth/password/reset/request/",
    "/api/auth/email/send/",
    "/api/auth/email/resend/",       # 이메일 발송
    "/api/auth/social/google/",
    "/api/auth/social/kakao/",
    "/api/auth/social/naver/",
    "/api/social/callback/",         # 소셜 로그인
    "/api/payment/test/",
    "/api/social/test/",             # 테스트 페이지
]
```

### 2.3 테스트 실행 방법
```bash
# 스키마 테스트만 실행
pytest -m schema --no-cov -v -n 0

# slow 테스트 제외
pytest -m "schema and not slow" --no-cov -v
```

---

## 📌 Phase 3: Stateful 테스트 (3-5일) ✅ 완료

### 3.1 구현된 테스트 파일 ✅
```
shopping/tests/schema/
├── conftest.py           # 공통 fixture
├── test_api_contract.py  # Phase 2: Contract 테스트
└── test_stateful_workflow.py  # Phase 3: Stateful 워크플로우 테스트
```

### 3.2 State Machine 정의 ✅
```python
class UserState(Enum):
    ANONYMOUS = auto()       # 비인증 상태
    AUTHENTICATED = auto()   # 로그인 완료
    HAS_CART = auto()        # 장바구니에 상품 있음
    HAS_ORDER = auto()       # 주문 완료

class ProductState(Enum):
    IN_STOCK = auto()        # 재고 있음
    LOW_STOCK = auto()       # 재고 부족
    OUT_OF_STOCK = auto()    # 품절
```

### 3.3 구현된 테스트 클래스 (총 14개 테스트)

#### TestUserPurchaseFlow (사용자 구매 플로우)
- `test_complete_purchase_flow` - 완전한 구매 플로우
  - Anonymous → 회원가입 → 로그인 → 장바구니 추가 → 주문 조회
- `test_anonymous_cannot_access_protected_endpoints` - 비인증 접근 제한
- `test_authenticated_can_access_protected_endpoints` - 인증된 접근 확인

#### TestWishlistFlow (위시리스트 플로우)
- `test_wishlist_toggle_flow` - 위시리스트 토글 플로우
- `test_wishlist_to_cart_flow` - 위시리스트 → 장바구니 이동

#### TestProductBrowsingFlow (상품 탐색 플로우)
- `test_category_to_product_flow` - 카테고리 → 상품 탐색
- `test_product_search_and_filter_flow` - 검색 및 필터링

#### TestCartManagementFlow (장바구니 관리)
- `test_cart_item_lifecycle` - 장바구니 아이템 생명주기
- `test_cart_bulk_operations` - 대량 작업

#### TestNotificationFlow (알림 플로우)
- `test_notification_read_flow` - 알림 조회 및 읽음 처리

#### TestPointsFlow (포인트 플로우)
- `test_points_inquiry_flow` - 포인트 조회

#### TestStateMachineTransitions (상태 전이 검증)
- `test_user_state_transitions` - 상태 전이 검증
- `test_invalid_state_transition_blocked` - 잘못된 상태 전이 차단

#### TestComplexWorkflows (복잡한 워크플로우, @slow)
- `test_full_shopping_experience` - 완전한 쇼핑 경험 시뮬레이션

### 3.4 테스트 실행 방법
```bash
# Stateful 테스트만 실행
pytest -m stateful --no-cov -v -n 0

# 스키마 + Stateful 테스트 모두 실행
pytest -m "schema or stateful" --no-cov -v -n 0

# slow 테스트 제외
pytest -m "stateful and not slow" --no-cov -v
```

### 3.5 상태 전이 다이어그램
```
┌─────────────┐    회원가입/로그인    ┌───────────────┐
│  ANONYMOUS  │ ────────────────────> │ AUTHENTICATED │
└─────────────┘                       └───────────────┘
                                              │
                                              │ 장바구니 추가
                                              ▼
                                      ┌───────────────┐
                                      │   HAS_CART    │
                                      └───────────────┘
                                              │
                                              │ 주문 생성
                                              ▼
                                      ┌───────────────┐
                                      │   HAS_ORDER   │
                                      └───────────────┘
```

---

## 📌 Phase 4: CI 통합 (1-2일)

### 4.1 pytest 마커 추가
```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    # ... 기존 마커들
    "schema: OpenAPI 스키마 기반 계약 테스트",
]
```

### 4.2 GitHub Actions 설정
```yaml
# CI에서 분리 실행
- name: Schema Tests
  run: pytest -m schema --tb=short
```

### 4.3 실행 시간 관리
```python
# 빠른 피드백용 설정
@settings(max_examples=50)  # 기본값 100에서 줄임
```

---

## 📌 Phase 5: 고급 활용 (선택사항)

### 5.1 커스텀 데이터 생성
```python
@st.register_string_format("phone")
def phone_numbers():
    return st.from_regex(r"010-\d{4}-\d{4}")
```

### 5.2 Hook 활용
```python
@schemathesis.hook("before_generate_case")
def before_generate(context, strategy):
    # 특정 엔드포인트에 대한 데이터 조작
    ...
```

### 5.3 리포트 생성
```bash
schemathesis run http://localhost:8000/api/schema/ \
  --report=report.html
```

---

## ⚠️ 주의사항 및 고려사항

### 1. 데이터 격리
| 문제 | 해결책 |
|------|--------|
| 테스트 간 데이터 충돌 | `@pytest.mark.django_db(transaction=True)` |
| 생성된 데이터 정리 | 테스트 후 `teardown`에서 정리 |

### 2. 성능 이슈
| 문제 | 해결책 |
|------|--------|
| 느린 테스트 | `max_examples` 제한, 병렬화 |
| DB 연결 제한 | PostgreSQL connection pooling |

### 3. 스키마 정확성
| 문제 | 해결책 |
|------|--------|
| 불완전한 스키마 | `drf-spectacular` 설정 보완 |
| 누락된 응답 코드 | `@extend_schema` 데코레이터 추가 |

### 4. 현재 프로젝트 특수 고려사항
- **Toss Payment Webhook**: 외부 서비스이므로 제외 또는 모킹
- **Celery 비동기 작업**: `CELERY_TASK_ALWAYS_EAGER=True` 유지
- **Rate Limiting**: 테스트 환경에서 높게 설정 (이미 conftest에 있음 ✅)

---

## 📅 권장 일정

| 단계 | 기간 | 산출물 | 상태 |
|------|------|--------|------|
| Phase 1 | 1일 | 기본 설정, 패키지 설치 | ✅ 완료 |
| Phase 2 | 2-3일 | 기본 Contract 테스트 통과 | ✅ 완료 |
| Phase 3 | 3-5일 | Stateful 워크플로우 테스트 | ✅ 완료 |
| Phase 4 | 1-2일 | CI 통합 완료 | 🔲 예정 |
| **총계** | **7-11일** | | **3단계 완료** |

---

## 🚀 시작 추천 순서

1. **`schemathesis` 패키지 설치**
2. **스키마 검증 먼저 수행** (스키마 자체의 완전성 확인)
   ```bash
   schemathesis run http://localhost:8000/api/schema/ --dry-run
   ```
3. **간단한 GET 엔드포인트부터 테스트** (예: 상품 목록)
4. **인증 필요 엔드포인트 추가**
5. **점진적으로 범위 확장**

---

## 📁 생성된 파일 요약

```
shopping/tests/schema/
├── __init__.py
├── conftest.py              # Phase 1: 인증 fixture, 제외 엔드포인트 설정
├── test_api_contract.py     # Phase 2: API Contract 테스트 (45+ 테스트)
└── test_stateful_workflow.py # Phase 3: Stateful 워크플로우 테스트 (14 테스트)
```

### 테스트 마커
```toml
# pyproject.toml
markers = [
    "schema: OpenAPI 스키마 기반 계약 테스트 (Schemathesis)",
    "stateful: Stateful API 워크플로우 테스트 (상태 전이 검증)",
]
```

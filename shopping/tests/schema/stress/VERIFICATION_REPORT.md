# 📊 Stress 테스트 가이드 준수 검증 리포트

> 검증일: 2025-12-05
> 검증 대상: `shopping/tests/schema/stress/` 폴더의 테스트 파일들
> 가이드 기준: `shopping/tests/schema/guides/` 폴더의 **9개** 가이드 문서

---

## 📋 가이드 문서 목록

| # | 가이드 파일 | 주요 내용 |
|---|------------|----------|
| 01 | `01_SUCCESS_RESPONSE_SCHEMA.md` | 성공 응답(2xx) 스키마 검증 |
| 02 | `02_ERROR_RESPONSE_SCHEMA.md` | 에러 응답(4xx, 5xx) 스키마 검증 |
| 03 | `03_PATH_PARAMETER.md` | Path Parameter 검증 (/api/products/{id}/) |
| 04 | `04_QUERY_PARAMETER.md` | Query Parameter 검증 (?page=1&search=) |
| 05 | `05_AUTHENTICATION.md` | 인증/권한 테스트 (JWT 토큰) |
| 06 | `06_WORKFLOW.md` | Stateful 워크플로우 테스트 |
| 07 | `07_FUZZ_TESTING.md` | Hypothesis 기반 Fuzz 테스트 |
| 08 | `08_NEGATIVE_TESTING.md` | 의도적 잘못된 입력 테스트 |
| 09 | `09_PERFORMANCE_TESTING.md` | 성능/동시성 테스트 |

---

## 📁 Stress 폴더 테스트 파일 목록

| 파일 | 라인 수 | 클래스 수 | 테스트 수 |
|-----|--------|----------|----------|
| `test_fuzz.py` | 790 | 5 | ~15 |
| `test_negative.py` | 1318 | 8 | ~74 |
| `test_performance.py` | 943 | 5 | ~15 |
| `test_concurrency.py` | 947 | 3 | ~10 |

---

## 🔍 가이드별 상세 검증

---

### 1️⃣ 01_SUCCESS_RESPONSE_SCHEMA.md 검증

**가이드 체크리스트:**

| 항목 | 가이드 기준 | stress 폴더 현황 | 상태 |
|-----|------------|-----------------|------|
| HTTP 상태 코드 검증 | 200, 201, 204 확인 | ✅ 모든 파일에서 `status_code` 검증 | ✅ |
| 응답 구조 타입 | dict/list/paginated | ✅ `response.json()` 사용 | ✅ |
| 필수 필드 검증 | 필드 존재 확인 | ⚠️ stress 테스트 특성상 스키마보다 안정성 중심 | 해당없음 |
| 필드 타입 검증 | 타입 일치 확인 | ⚠️ stress 테스트 특성상 스키마보다 안정성 중심 | 해당없음 |
| AAA 패턴 | Arrange/Act/Assert | ✅ 모든 테스트에 적용 | ✅ |

**참고:** Stress 테스트는 성공 스키마 검증보다 **안정성/에러 방지**에 집중하므로, 상세 스키마 검증은 `contract/` 폴더에서 수행됨

---

### 2️⃣ 02_ERROR_RESPONSE_SCHEMA.md 검증

**가이드 체크리스트:**

| 항목 | 가이드 기준 | 구현 파일 | 상태 |
|-----|------------|----------|------|
| 400 Bad Request | 필수 필드 누락, 잘못된 타입 | `test_negative.py` | ✅ |
| 401 Unauthorized | 토큰 없음, 만료, 잘못된 형식 | `test_negative.py` (TestAuthorizationEdgeCases) | ✅ |
| 403 Forbidden | 타인 리소스 접근 | `test_negative.py` (test_access_other_user_*) | ✅ |
| 404 Not Found | 존재하지 않는 ID | `test_negative.py` (TestInvalidInputs) | ✅ |
| 에러 응답 구조 | dict, 에러 메시지 포함 | ✅ `response.json()` 검증 | ✅ |
| 민감 정보 미노출 | 스택 트레이스 없음 | `test_negative.py` (TestSensitiveInfoExposure) | ✅ |

**구현 현황 (test_negative.py):**

```python
# 에러 코드별 테스트
TestInvalidInputs:
  - test_invalid_product_id_format  # 400/404
  - test_missing_required_fields_cart_add  # 400
  - test_invalid_quantity_values  # 400
  - test_invalid_login_data  # 400/401

TestAuthorizationEdgeCases:
  - test_invalid_auth_headers  # 401
  - test_access_other_user_order  # 403/404
  - test_access_other_user_cart  # 403/404
  - test_expired_token  # 401
  - test_token_with_invalid_user_id  # 401

TestSensitiveInfoExposure:
  - test_error_response_no_stack_trace  # 민감 정보 미노출
  - test_auth_error_no_user_enumeration  # 사용자 열거 방지
  - test_database_error_no_details  # DB 정보 미노출
```

---

### 3️⃣ 03_PATH_PARAMETER.md 검증

**가이드 체크리스트:**

| 테스트 케이스 | 가이드 예시 | 구현 파일 | 상태 |
|-------------|------------|----------|------|
| 유효한 ID → 200 | `client.get(f"/api/products/{id}/")` | `test_fuzz.py`, `test_negative.py` | ✅ |
| 존재하지 않는 ID → 404 | `99999999` | `test_negative.py` (nonexistent_id) | ✅ |
| 음수 ID → 400/404 | `-1` | `test_negative.py`, `test_fuzz.py` | ✅ |
| 0 ID → 400/404 | `0` | `test_negative.py` (zero_id) | ✅ |
| 문자열 ID → 400/404 | `"abc"` | `test_negative.py` (string_id) | ✅ |
| float ID → 400/404 | `"1.5"` | `test_negative.py` (float_id) | ✅ |
| null 문자열 → 400/404 | `"null"` | `test_negative.py` (null_string) | ✅ |
| SQL Injection → 5xx 없음 | `"1; DROP TABLE--"` | `test_negative.py`, `test_fuzz.py` | ✅ |
| Path Traversal → 5xx 없음 | `"../../../etc/passwd"` | `test_negative.py` | ✅ |

**구현 현황:**

```python
# test_negative.py - TestInvalidInputs
@pytest.mark.parametrize("invalid_id,expected_codes", [
    ("abc", [400, 404]),        # string_id
    ("-1", [400, 404]),         # negative_id
    ("0", [400, 404]),          # zero_id
    ("99999999", [404]),        # nonexistent_id
    ("1.5", [400, 404]),        # float_id
    ("null", [400, 404]),       # null_string
    ("<script>", [400, 404]),   # script_tag
    ("1; DROP TABLE--", [400, 404]),  # sql_injection
    ("../../../", [400, 404]),  # path_traversal
])

# test_fuzz.py - product_id_strategy
product_id_strategy = st.one_of(
    st.integers(min_value=-1000, max_value=10000),
    st.text(...),
    st.just("null"),
    st.floats(allow_nan=True, allow_infinity=True),
)
```

---

### 4️⃣ 04_QUERY_PARAMETER.md 검증

**가이드 체크리스트:**

| 파라미터 | 테스트 케이스 | 구현 파일 | 상태 |
|---------|-------------|----------|------|
| page | 음수, 0, 큰 수, 문자열 | `test_fuzz.py`, `test_negative.py` | ✅ |
| page_size | 음수, 0, 매우 큰 수 | `test_negative.py` (TestBoundaryValues) | ✅ |
| search | SQL Injection | `test_negative.py` (TestSecurityInputs) | ✅ |
| search | XSS 시도 | `test_negative.py` (test_xss_attempt_*) | ✅ |
| search | 긴 문자열 | `test_negative.py` (test_very_long_search_query) | ✅ |
| search | 이모지/유니코드 | `test_negative.py` (test_special_characters_in_search) | ✅ |
| ordering | 없는 필드, SQL Injection | `test_fuzz.py` (ordering_strategy) | ✅ |
| 복합 파라미터 | 여러 파라미터 동시 사용 | `test_fuzz.py` (test_products_combined_params_fuzz) | ✅ |

**구현 현황:**

```python
# test_fuzz.py - Query Parameter Strategies
search_query_strategy = st.one_of(
    st.text(min_size=0, max_size=1000),
    st.just("'; DROP TABLE products; --"),  # SQL Injection
    st.just("<script>alert('xss')</script>"),  # XSS
    st.just("🎉💀🔥"),  # 이모지
    st.just("../../../etc/passwd"),  # Path Traversal
)

pagination_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),
    st.just(0), st.just(-1), st.just(999999999),
)

# test_negative.py - TestBoundaryValues
@pytest.mark.parametrize("page_param,expected_codes", [
    ({"page": 0}, [400, 404]),
    ({"page": -1}, [400, 404]),
    ({"page": 999999}, [200, 404]),
    ({"page_size": 0}, [400]),
    ({"page_size": -1}, [400]),
    ({"page_size": 10000}, [200, 400]),
])
```

---

### 5️⃣ 05_AUTHENTICATION.md 검증

**가이드 체크리스트:**

| 시나리오 | 예상 응답 | 구현 파일 | 상태 |
|---------|----------|----------|------|
| 토큰 없음 | 401 | `test_negative.py` (empty_header) | ✅ |
| 유효한 토큰 | 200 | `test_performance.py` (auth_client fixture) | ✅ |
| 만료된 토큰 | 401 | `test_negative.py` (test_expired_token) | ✅ |
| 잘못된 형식 토큰 | 401 | `test_negative.py` (invalid_token) | ✅ |
| Bearer 접두사 누락 | 401 | `test_negative.py` (no_bearer_prefix) | ✅ |
| 서명 불일치 토큰 | 401 | `test_negative.py` (wrong_signature) | ✅ |
| 존재하지 않는 사용자 토큰 | 401 | `test_negative.py` (test_token_with_invalid_user_id) | ✅ |
| 일반 사용자 → 판매자 API | 403 | ⚠️ stress 폴더에 없음 (contract/에 있을 수 있음) | 참고 |

**구현 현황 (test_negative.py - TestAuthorizationEdgeCases):**

```python
@pytest.mark.parametrize("auth_header,expected_code", [
    ("", 401),                              # empty_header
    ("Bearer", 401),                        # bearer_only
    ("Bearer ", 401),                       # bearer_empty_token
    ("Bearer invalid.token.here", 401),     # invalid_token
    ("invalid_token_no_bearer", 401),       # no_bearer_prefix
    ("Basic dXNlcjpwYXNz", 401),            # basic_auth
    ("Bearer eyJ...(wrong signature)", 401), # wrong_signature
])
def test_invalid_auth_headers(...)
```

---

### 6️⃣ 06_WORKFLOW.md 검증

**가이드 체크리스트:**

| 워크플로우 | 가이드 예시 | stress 폴더 현황 | 상태 |
|-----------|------------|-----------------|------|
| 구매 플로우 | 상품조회→회원가입→로그인→장바구니→주문 | ⚠️ stress 폴더에 없음 | 해당없음 |
| 상태 전이 | ANONYMOUS → AUTHENTICATED → HAS_CART | ⚠️ stress 폴더에 없음 | 해당없음 |
| 잘못된 전이 차단 | 비회원 장바구니 추가 시도 | `test_negative.py` (인증 필요 API 테스트) | ✅ |

**참고:** Stateful 워크플로우 테스트는 `workflow/` 폴더에서 전담 (`test_stateful_workflow.py`)
stress 폴더는 개별 API의 안정성/성능에 집중

---

### 7️⃣ 07_FUZZ_TESTING.md 검증 ⭐

**가이드 체크리스트:**

| 항목 | 가이드 기준 | 구현 현황 (test_fuzz.py) | 상태 |
|-----|------------|-------------------------|------|
| Hypothesis 전략 정의 | `st.one_of(...)` | ✅ 5개 전략 정의 | ✅ |
| `@given` 데코레이터 | 모든 fuzz 테스트에 | ✅ 적용됨 | ✅ |
| `@hypothesis_settings` | max_examples, deadline 등 | ✅ max_examples=50, deadline=None | ✅ |
| 5xx 에러 없음 검증 | `assert response.status_code < 500` | ✅ 모든 테스트에 | ✅ |
| product_id 전략 | 정수, 문자열, null, float | ✅ product_id_strategy | ✅ |
| quantity 전략 | 음수, 0, 큰 수, float | ✅ quantity_strategy | ✅ |
| search 전략 | SQL Injection, XSS, 이모지 | ✅ search_query_strategy | ✅ |
| `@pytest.mark.fuzz` | 클래스/메서드에 | ✅ 모든 클래스에 적용 | ✅ |
| AAA 주석 패턴 | Arrange/Act/Assert | ✅ 적용됨 | ✅ |
| 병렬 실행 비활성화 | `-n 0` 안내 | ✅ docstring에 안내 | ✅ |

**구현된 Fuzz 테스트 클래스:**

| 클래스 | 테스트 대상 | 전략 사용 |
|--------|-----------|----------|
| TestProductsFuzz | 상품 검색/상세/페이지네이션/정렬 | search, pagination, ordering, product_id |
| TestCartFuzz | 장바구니 추가/수량 변경 | product_id, quantity |
| TestAuthFuzz | 로그인/토큰 갱신 | username, password, refresh_token |
| TestCategoriesFuzz | 카테고리 상세 | product_id (재사용) |
| TestFullApiFuzz (@slow) | 전체 API 복합 파라미터 | search, page, ordering |

---

### 8️⃣ 08_NEGATIVE_TESTING.md 검증 ⭐

**가이드 체크리스트:**

| 카테고리 | 테스트 케이스 | 구현 현황 (test_negative.py) | 상태 |
|---------|-------------|----------------------------|------|
| **필수 필드 누락** | 빈 요청, 일부 필드 누락 | `test_missing_required_fields_cart_add` | ✅ |
| **형식 오류** | 이메일, 날짜 등 | `test_invalid_login_data`, `test_invalid_quantity_values` | ✅ |
| **경계값 오류** | 음수, 최대 초과 | `TestBoundaryValues` 클래스 | ✅ |
| **악의적 입력** | SQL Injection | `test_sql_injection_attempt` (9개 페이로드) | ✅ |
| **악의적 입력** | XSS | `test_xss_attempt_in_search` (7개), `test_xss_attempt_in_post_data` | ✅ |
| **악의적 입력** | Path Traversal | `test_path_traversal_attempt` (5개 페이로드) | ✅ |
| **악의적 입력** | SSTI `{{7*7}}` | `TestSSTIInputs` (10개 페이로드) | ✅ |
| **404 시나리오** | 존재하지 않는 ID | `test_invalid_product_id_format` (nonexistent_id) | ✅ |
| **잘못된 요청 형식** | 잘못된 JSON | `test_malformed_json_body` | ✅ |
| **잘못된 요청 형식** | 잘못된 Content-Type | `test_wrong_content_type` | ✅ |
| **잘못된 요청 형식** | 추가 필드 (__proto__) | `test_extra_unexpected_fields` | ✅ |
| `@pytest.mark.negative` | 클래스에 적용 | ✅ 모든 클래스에 적용 | ✅ |

**구현된 Negative 테스트 클래스:**

| 클래스 | 테스트 수 | 주요 테스트 |
|--------|----------|-----------|
| TestInvalidInputs | 4 | ID 형식, 필수 필드, 수량, 로그인 데이터 |
| TestSecurityInputs | 4 | SQL Injection, XSS (검색/POST), Path Traversal |
| TestBoundaryValues | 3 | 페이지네이션, 긴 검색어, 특수 문자 |
| TestMalformedRequests | 3 | JSON 형식, Content-Type, 추가 필드 |
| TestAuthorizationEdgeCases | 5 | 인증 헤더, 타인 주문/장바구니, 만료 토큰, 잘못된 사용자 토큰 |
| TestSSTIInputs | 2 | SSTI 검색/POST 데이터 (10개 페이로드) |
| TestSensitiveInfoExposure | 3 | 스택 트레이스, 사용자 열거, DB 정보 미노출 |

---

### 9️⃣ 09_PERFORMANCE_TESTING.md 검증 ⭐

**가이드 체크리스트:**

| 항목 | 가이드 기준 | 구현 현황 | 상태 |
|-----|------------|----------|------|
| **응답 시간 임계값 정의** | 딕셔너리로 정의 | `RESPONSE_TIME_THRESHOLDS` (14개 엔드포인트) | ✅ |
| **단일 요청 성능 테스트** | `time.perf_counter()` | `measure_response_time()` 함수 | ✅ |
| **다중 요청 평균 성능** | 반복 측정 후 통계 | `measure_multiple_times()` (mean, median, min, max) | ✅ |
| **N+1 쿼리 방지 검증** | `CaptureQueriesContext` | `TestDatabaseQueryPerformance` | ✅ |
| **동시 요청 테스트 (10개+)** | `ThreadPoolExecutor` | `run_concurrent_requests()`, 10+ 동시 요청 | ✅ |
| `@pytest.mark.performance` | 성능 테스트 클래스에 | ✅ 적용됨 | ✅ |
| `@pytest.mark.concurrency` | 동시성 테스트 클래스에 | ✅ 적용됨 (test_concurrency.py) | ✅ |
| **워밍업 요청** | 첫 요청 제외 | ✅ 모든 성능 테스트에서 워밍업 실행 | ✅ |
| **병렬 실행 비활성화** | `-n 0` | ✅ docstring에 안내 | ✅ |

**응답 시간 임계값:**

| 엔드포인트 | 임계값 (ms) | 테스트 메서드 |
|-----------|------------|--------------|
| /api/products/ | 500 | `test_product_list_response_time` |
| /api/products/{id}/ | 200 | `test_product_detail_response_time` |
| /api/categories/ | 300 | `test_category_list_response_time` |
| /api/auth/login/ | 500 | `test_login_response_time` |
| /api/auth/token/refresh/ | 300 | `test_token_refresh_response_time` |
| /api/cart/ | 400 | `test_cart_retrieve_response_time` |
| /api/cart/add_item/ | 500 | `test_cart_add_item_response_time` |
| /api/orders/ | 600 | `test_order_list_response_time` |
| /api/products/popular/ | 800 | `test_popular_products_response_time` |

**구현된 Performance 테스트 클래스:**

| 클래스 | 파일 | 테스트 수 |
|--------|-----|----------|
| TestPublicApiResponseTime | test_performance.py | 4 |
| TestAuthApiResponseTime | test_performance.py | 2 |
| TestAuthenticatedApiResponseTime | test_performance.py | 3 |
| TestDatabaseQueryPerformance | test_performance.py | 1 |
| TestPaginationPerformance | test_performance.py | 2 |

**구현된 Concurrency 테스트 클래스 (test_concurrency.py):**

| 클래스 | 테스트 수 | 동시 요청 수 |
|--------|----------|------------|
| TestConcurrentCartOperations | 3 | 10-50 |
| TestConcurrentStockDeduction | 2 | 10 |
| TestConcurrentApiResponses | 3 | 10 |

**동시성 추가 검증 항목:**

- ✅ Row Duplication 방지 (Race Condition)
- ✅ 회계 무결성 (stock + sold_count == 초기값)
- ✅ JWT JTI 고유성 검증
- ✅ 409 Conflict 응답 메시지 일관성
- ✅ 쓰기 중 GET 응답 JSON 파싱 가능성

---

## 📊 종합 준수율

| 가이드 | 필수 항목 | 구현 항목 | 준수율 | 비고 |
|--------|----------|----------|--------|------|
| 01_SUCCESS_RESPONSE_SCHEMA | 5 | 3 | 60% | stress 특성상 스키마보다 안정성 중심 |
| 02_ERROR_RESPONSE_SCHEMA | 6 | 6 | **100%** | ✅ 민감 정보 미노출 검증 추가 |
| 03_PATH_PARAMETER | 9 | 9 | **100%** | ✅ 완벽 준수 |
| 04_QUERY_PARAMETER | 8 | 8 | **100%** | ✅ 완벽 준수 |
| 05_AUTHENTICATION | 7 | 7 | **100%** | ✅ 만료 토큰 테스트 추가 |
| 06_WORKFLOW | 3 | 1 | 33% | workflow/ 폴더에서 전담 (해당없음) |
| 07_FUZZ_TESTING | 10 | 10 | **100%** | ✅ 완벽 준수 |
| 08_NEGATIVE_TESTING | 12 | 12 | **100%** | ✅ SSTI 테스트 추가 |
| 09_PERFORMANCE_TESTING | 8 | 8 | **100%** | ✅ 완벽 준수 |

---

## 🚀 Docker 테스트 실행 명령어

```bash
# 1. Docker 컨테이너에서 전체 stress 테스트 실행
docker-compose exec web pytest shopping/tests/schema/stress/ -v --no-cov -p no:xdist

# 2. 개별 카테고리 테스트
docker-compose exec web pytest -m fuzz shopping/tests/schema/stress/ --no-cov -v -p no:xdist
docker-compose exec web pytest -m negative shopping/tests/schema/stress/ --no-cov -v -p no:xdist
docker-compose exec web pytest -m performance shopping/tests/schema/stress/ --no-cov -v -p no:xdist
docker-compose exec web pytest -m concurrency shopping/tests/schema/stress/ --no-cov -v -p no:xdist

# 3. slow 테스트 포함 전체 실행
docker-compose exec web pytest shopping/tests/schema/stress/ -v --no-cov -p no:xdist --run-slow

# 4. 특정 테스트 파일만 실행
docker-compose exec web pytest shopping/tests/schema/stress/test_fuzz.py -v --no-cov -p no:xdist
docker-compose exec web pytest shopping/tests/schema/stress/test_negative.py -v --no-cov -p no:xdist
docker-compose exec web pytest shopping/tests/schema/stress/test_performance.py -v --no-cov -p no:xdist
docker-compose exec web pytest shopping/tests/schema/stress/test_concurrency.py -v --no-cov -p no:xdist
```

---

## 📝 개선 권장 사항

### 우선순위 높음

| 파일 | 개선 항목 | 가이드 근거 |
|-----|----------|-----------|
| `test_negative.py` | SSTI 테스트 추가 `{{7*7}}` | 08_NEGATIVE (악의적 입력) |
| `test_negative.py` | 만료된 토큰 테스트 추가 | 05_AUTHENTICATION |

### 우선순위 중간

| 파일 | 개선 항목 | 가이드 근거 |
|-----|----------|-----------|
| `test_negative.py` | 민감 정보 미노출 검증 | 02_ERROR_RESPONSE |
| `test_concurrency.py` | 포인트 동시 적립/차감 | 09_PERFORMANCE (언급됨) |

### 우선순위 낮음 (참고)

| 파일 | 개선 항목 | 비고 |
|-----|----------|-----|
| 전체 | 성능 리포팅 fixture | 09_PERFORMANCE 예시 참조 |
| 전체 | 캐시 효율성 테스트 | 09_PERFORMANCE 선택사항 |

---

## ✅ 최종 결론

**stress 폴더의 테스트 파일들은 가이드 07, 08, 09를 거의 완벽하게 준수하고 있으며,**
**가이드 03, 04의 내용도 충실히 반영되어 있습니다.**

- **test_fuzz.py**: 07_FUZZ_TESTING.md **100% 준수**
- **test_negative.py**: 08_NEGATIVE_TESTING.md **92% 준수** (SSTI 추가 권장)
- **test_performance.py + test_concurrency.py**: 09_PERFORMANCE_TESTING.md **100% 준수**

가이드 01, 02, 05, 06은 stress 테스트 특성상 일부만 적용되며,
나머지는 `contract/`, `workflow/` 폴더에서 전담합니다.

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
| Phase 4: CI 통합 | ✅ 완료 | 2025-12-05 |
| Phase 5: 고급 테스트 (Fuzz/Negative) | ✅ 완료 | 2025-12-05 |
| Phase 6: 현업 수준 고도화 | ✅ 완료 | 2025-12-05 |

---

## 📌 Phase 4: CI 통합 (1-2일)

### 4.1 pytest 마커 추가
```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    # ... 기존 마커들
    "schema: OpenAPI 스키마 기반 계약 테스트",
    "fuzz: Fuzz 테스트 (무작위 입력 검증)",
    "negative: Negative 테스트 (잘못된 입력 검증)",
]
```

### 4.2 GitHub Actions 설정
```yaml
# .github/workflows/schema-tests.yml
name: Schema Tests

on:
  pull_request:
    branches: [main, develop]
  schedule:
    - cron: '0 3 * * *'  # 매일 새벽 3시 (Nightly)

jobs:
  quick-schema-test:
    name: Quick Schema Test (PR용)
    runs-on: ubuntu-latest
    if: github.event_name == 'pull_request'
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements-dev.txt
      - name: Run Schema Tests
        run: |
          pytest -m "schema and not slow" --no-cov -v -n 0 \
            --tb=short --max-examples=50

  nightly-fuzz-test:
    name: Nightly Fuzz Test
    runs-on: ubuntu-latest
    if: github.event_name == 'schedule'
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install dependencies
        run: pip install -r requirements-dev.txt
      - name: Run Full Fuzz Tests
        run: |
          pytest -m "schema or fuzz" --no-cov -v -n 0 \
            --max-examples=500
      - name: Generate Schemathesis Report
        run: |
          schemathesis run http://localhost:8000/api/schema/ \
            --checks all \
            --max-examples 1000 \
            --report=schema-report.html
      - name: Upload Report
        uses: actions/upload-artifact@v4
        with:
          name: schema-report
          path: schema-report.html
```

### 4.3 실행 시간 관리 전략
| 실행 컨텍스트 | max_examples | 예상 시간 | 용도 |
|---------------|--------------|-----------|------|
| PR (빠른 피드백) | 50 | ~2분 | 기본 계약 검증 |
| Merge to main | 100 | ~5분 | 표준 검증 |
| Nightly | 500-1000 | ~30분 | 심층 퍼징 |

### 4.4 구현 체크리스트
- [ ] GitHub Actions 워크플로우 파일 생성
- [ ] pytest 마커 pyproject.toml에 추가
- [ ] Nightly 스케줄 설정
- [ ] 테스트 리포트 아카이빙 설정

---

## 📌 Phase 5: 고급 테스트 - Fuzz & Negative (2-3일)

### 5.1 Fuzz Testing (무작위 입력 검증)

Schemathesis의 **핵심 기능**인 자동 퍼징을 활용하여 API 견고성을 검증합니다.

#### 5.1.1 테스트 파일 생성
```
shopping/tests/schema/
└── test_fuzz.py  # 새로 생성
```

#### 5.1.2 구현할 테스트
```python
# test_fuzz.py
import schemathesis
from hypothesis import settings, given

schema = schemathesis.from_pytest_fixture("openapi_schema")

@pytest.mark.fuzz
class TestFuzzEndpoints:
    """무작위 입력으로 API 안정성 검증"""

    @given(case=schema["/api/products/"]["GET"].as_strategy())
    @settings(max_examples=100)
    def test_products_list_fuzz(self, case):
        """상품 목록 API 퍼징"""
        response = case.call()
        assert response.status_code < 500

    @given(case=schema["/api/products/{id}/"]["GET"].as_strategy())
    @settings(max_examples=100)
    def test_products_detail_fuzz(self, case):
        """상품 상세 API 퍼징 - 다양한 ID 형식"""
        response = case.call()
        assert response.status_code in [200, 400, 404]

    @given(case=schema["/api/cart/items/"]["POST"].as_strategy())
    @settings(max_examples=100)
    def test_cart_add_fuzz(self, case, auth_headers):
        """장바구니 추가 API 퍼징"""
        response = case.call(headers=auth_headers)
        assert response.status_code < 500

@pytest.mark.fuzz
@pytest.mark.slow
class TestFullApiFuzz:
    """전체 API 스키마 기반 퍼징"""

    @given(case=schema.as_strategy())
    @settings(max_examples=500)
    def test_all_endpoints_no_500(self, case, auth_headers):
        """모든 엔드포인트에서 5xx 에러 없음"""
        response = case.call(headers=auth_headers)
        assert response.status_code < 500, f"{case.path} returned {response.status_code}"
```

### 5.2 Negative Testing (부정 테스트)

잘못된 입력에 대한 적절한 에러 처리를 검증합니다.

#### 5.2.1 구현할 테스트
```python
# test_negative.py
@pytest.mark.negative
class TestInvalidInputs:
    """잘못된 입력에 대한 적절한 에러 응답 검증"""

    def test_invalid_product_id_format(self, api_client):
        """잘못된 상품 ID 형식"""
        invalid_ids = ["abc", "-1", "0", "99999999", "1.5", "null", "<script>"]
        for invalid_id in invalid_ids:
            response = api_client.get(f"/api/products/{invalid_id}/")
            assert response.status_code in [400, 404]
            assert response.status_code != 500

    def test_malformed_json_body(self, api_client, auth_headers):
        """잘못된 JSON 형식 요청"""
        response = api_client.post(
            "/api/cart/items/",
            data="not a json",
            content_type="application/json",
            **auth_headers
        )
        assert response.status_code == 400

    def test_missing_required_fields(self, api_client, auth_headers):
        """필수 필드 누락"""
        response = api_client.post(
            "/api/cart/items/",
            data={},  # product_id, quantity 누락
            format="json",
            **auth_headers
        )
        assert response.status_code == 400

    def test_invalid_quantity_values(self, api_client, auth_headers, product):
        """잘못된 수량 값"""
        invalid_quantities = [-1, 0, 999999, 1.5, "abc"]
        for qty in invalid_quantities:
            response = api_client.post(
                "/api/cart/items/",
                data={"product_id": product.id, "quantity": qty},
                format="json",
                **auth_headers
            )
            assert response.status_code in [400, 422]

@pytest.mark.negative
class TestSecurityInputs:
    """보안 관련 입력 테스트"""

    def test_sql_injection_attempt(self, api_client):
        """SQL 인젝션 시도"""
        payloads = [
            "1; DROP TABLE products;--",
            "1' OR '1'='1",
            "1 UNION SELECT * FROM users",
        ]
        for payload in payloads:
            response = api_client.get(f"/api/products/?search={payload}")
            assert response.status_code < 500

    def test_xss_attempt(self, api_client, auth_headers):
        """XSS 시도"""
        response = api_client.post(
            "/api/products/questions/",
            data={"content": "<script>alert('xss')</script>"},
            format="json",
            **auth_headers
        )
        # 저장되더라도 이스케이프 처리 확인
        assert "<script>" not in response.content.decode()

@pytest.mark.negative
class TestBoundaryValues:
    """경계값 테스트"""

    def test_pagination_boundaries(self, api_client):
        """페이지네이션 경계값"""
        boundary_cases = [
            {"page": 0, "expected": [400]},
            {"page": -1, "expected": [400]},
            {"page": 999999, "expected": [200, 404]},  # 빈 결과 또는 404
            {"page_size": 0, "expected": [400]},
            {"page_size": -1, "expected": [400]},
            {"page_size": 10000, "expected": [200, 400]},  # 최대 제한
        ]
        for case in boundary_cases:
            params = {k: v for k, v in case.items() if k != "expected"}
            response = api_client.get("/api/products/", params)
            assert response.status_code in case["expected"]

    def test_string_length_limits(self, api_client, auth_headers):
        """문자열 길이 제한"""
        # 매우 긴 검색어
        long_query = "a" * 10000
        response = api_client.get(f"/api/products/?search={long_query}")
        assert response.status_code in [200, 400, 414]  # 414: URI Too Long
```

### 5.3 구현 체크리스트
- [x] `test_fuzz.py` 파일 생성
- [x] `test_negative.py` 파일 생성
- [x] Hypothesis 설정 최적화
- [x] 보안 테스트 케이스 추가
- [x] 경계값 테스트 완성

---

## 📌 Phase 6: 현업 수준 고도화 (선택사항)

### 6.1 동시성 테스트
```python
# test_concurrency.py
import asyncio
import aiohttp

@pytest.mark.asyncio
@pytest.mark.concurrency
class TestConcurrentRequests:
    """동시 요청 처리 검증"""

    async def test_concurrent_cart_updates(self, auth_headers, product):
        """동시에 같은 장바구니 수정 시 데이터 무결성"""
        async def add_to_cart():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "http://localhost:8000/api/cart/items/",
                    json={"product_id": product.id, "quantity": 1},
                    headers=auth_headers
                ) as response:
                    return response.status

        # 10개 동시 요청
        tasks = [add_to_cart() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        # 모두 성공하거나 적절한 에러 반환
        assert all(status in [200, 201, 409] for status in results)

    async def test_concurrent_order_creation(self, auth_headers):
        """동시 주문 생성 시 재고 처리"""
        # 재고 1개인 상품에 동시 주문
        # 하나만 성공해야 함
        pass
```

### 6.2 성능 임계값 테스트
```python
# test_performance.py
import time

@pytest.mark.performance
class TestResponseTime:
    """API 응답 시간 검증"""

    THRESHOLDS = {
        "/api/products/": 500,      # 500ms
        "/api/products/{id}/": 200,  # 200ms
        "/api/cart/": 300,          # 300ms
    }

    @pytest.mark.parametrize("endpoint,threshold", THRESHOLDS.items())
    def test_response_time(self, api_client, endpoint, threshold):
        """응답 시간이 임계값 이내인지 확인"""
        start = time.time()
        response = api_client.get(endpoint)
        elapsed = (time.time() - start) * 1000

        assert response.status_code < 500
        assert elapsed < threshold, f"{endpoint} took {elapsed}ms (threshold: {threshold}ms)"
```

### 6.3 Rate Limiting 테스트
```python
# test_rate_limiting.py
@pytest.mark.rate_limiting
class TestRateLimiting:
    """Rate Limiting 동작 검증"""

    def test_rate_limit_triggered(self, api_client):
        """Rate limit 초과 시 429 응답"""
        for _ in range(150):  # Rate limit 초과 시도
            response = api_client.get("/api/products/")
            if response.status_code == 429:
                break
        else:
            pytest.skip("Rate limiting not configured")

        assert response.status_code == 429
        assert "Retry-After" in response.headers
```

### 6.4 외부 API 모니터링
```python
# test_external_contracts.py
@pytest.mark.external
class TestExternalApiContracts:
    """외부 서비스 API 계약 변경 감지"""

    def test_toss_payment_api_contract(self):
        """Toss Payment API 스키마 변경 감지"""
        # 외부 API의 응답 구조가 변경되었는지 확인
        pass
```

### 6.5 Stateful Link Testing (고급)
```bash
# OpenAPI Links를 활용한 자동 상태 추적
schemathesis run http://localhost:8000/api/schema/ \
  --stateful=links \
  --max-examples 100 \
  --checks all
```

### 6.6 커스텀 데이터 생성
```python
import schemathesis
from hypothesis import strategies as st

# 한국 전화번호 형식
@schemathesis.register_string_format("phone")
def phone_numbers():
    return st.from_regex(r"010-\d{4}-\d{4}", fullmatch=True)

# 한국 이메일 형식
@schemathesis.register_string_format("korean-email")
def korean_emails():
    domains = ["naver.com", "daum.net", "gmail.com", "kakao.com"]
    return st.builds(
        lambda user, domain: f"{user}@{domain}",
        st.from_regex(r"[a-z]{5,10}", fullmatch=True),
        st.sampled_from(domains)
    )
```

### 6.7 HTML 리포트 생성
```bash
# 상세 리포트 생성
schemathesis run http://localhost:8000/api/schema/ \
  --checks all \
  --max-examples 500 \
  --report=schema-report.html \
  --hypothesis-seed=12345  # 재현성을 위한 시드
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
| Phase 4 | 1-2일 | CI 통합, GitHub Actions | ✅ 완료 |
| Phase 5 | 2-3일 | Fuzz/Negative 테스트 | ✅ 완료 |
| Phase 6 | 2-3일 | 동시성/성능/외부 API | ✅ 완료 |
| **총계** | **11-17일** | | **🎉 전체 완료** |

---

## 📁 파일 구조

```
shopping/tests/schema/
├── __init__.py
├── conftest.py               # ✅ 완료: 인증 fixture, 제외 엔드포인트
├── test_api_contract.py      # ✅ 완료: API Contract 테스트 (45+)
├── test_stateful_workflow.py # ✅ 완료: Stateful 워크플로우 (14)
├── test_fuzz.py              # ✅ 완료: Fuzz 테스트
├── test_negative.py          # ✅ 완료: Negative 테스트
├── test_concurrency.py       # ✅ 완료: 동시성 테스트 (8)
└── test_performance.py       # ✅ 완료: 성능 테스트 (12)

.github/workflows/
├── django-ci.yml             # ✅ 기존: 메인 CI
└── schema-tests.yml          # ✅ 완료: 스키마 테스트 CI
```

### 테스트 마커 (이미 pyproject.toml에 정의됨)
```toml
# pyproject.toml
markers = [
    "schema: OpenAPI 스키마 기반 계약 테스트 (Schemathesis)",
    "stateful: Stateful API 워크플로우 테스트 (상태 전이 검증)",
    "fuzz: Fuzz 테스트 - Hypothesis/Schemathesis 기반 무작위 입력 검증",
    "negative: Negative 테스트 - 잘못된 입력에 대한 적절한 에러 처리 검증",
    "concurrency: 동시성 테스트 (race condition 검증)",  # ✅ 완료
    "performance: 성능 테스트",                          # ✅ 완료
]
```

---

## 🎯 테스트 커버리지 목표

### 전체 달성 ✅
| 테스트 유형 | 상태 | 보장하는 것 |
|-------------|------|-------------|
| Contract 테스트 | ✅ | API가 문서대로 동작 |
| Stateful 테스트 | ✅ | 비즈니스 플로우 정상 동작 |
| 인증 테스트 | ✅ | 보안 기본선 확보 |
| Fuzz 테스트 | ✅ | 무작위 입력에 대한 견고성 |
| Negative 테스트 | ✅ | 잘못된 입력 적절히 처리 |
| 동시성 테스트 | ✅ | 경쟁 조건 없음 |
| 성능 테스트 | ✅ | 응답 시간 임계값 준수 |

---

## 📊 현업 Best Practice 요약

| 실행 시점 | 테스트 범위 | max_examples | 소요 시간 |
|-----------|-------------|--------------|-----------|
| PR마다 | schema + stateful (not slow) | 50 | ~2분 |
| Merge to main | 전체 schema | 100 | ~5분 |
| Nightly | schema + fuzz + negative | 500 | ~30분 |
| Weekly | 전체 (동시성, 성능 포함) | 1000 | ~1시간 |

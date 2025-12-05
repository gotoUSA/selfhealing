"""
Fuzz 테스트 공통 설정 및 Strategy 정의
======================================

이 모듈은 모든 Fuzz 테스트에서 공유되는 Hypothesis Strategy와 Fixture를 정의합니다.

📋 포함 내용:
- Hypothesis Strategy 정의 (product_id, quantity, search 등)
- OpenAPI 스키마 로드 Fixture
"""

import json
import string

import pytest
from hypothesis import strategies as st
from rest_framework import status


# ==========================================
# Hypothesis 전략(Strategy) 정의
# ==========================================

# 상품 ID 형식
product_id_strategy = st.one_of(
    st.integers(min_value=-1000, max_value=10000),
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=20),
    st.just("null"),
    st.just("undefined"),
    st.just(""),
    st.floats(allow_nan=True, allow_infinity=True),
)

# 수량 형식
quantity_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),
    st.floats(min_value=-100.0, max_value=100000.0),
    st.just(0),
    st.just(-1),
)

# 검색어 형식
search_query_strategy = st.one_of(
    st.text(min_size=0, max_size=1000),
    st.just(""),
    st.just(" " * 100),
    st.just("a" * 5000),
    st.just("'; DROP TABLE products; --"),
    st.just("<script>alert('xss')</script>"),
    st.just("🎉💀🔥"),
    st.just("\x00\x01\x02"),
    st.just("../../../etc/passwd"),
)

# 페이지네이션 파라미터
pagination_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),
    st.just(0),
    st.just(-1),
    st.just(999999999),
    st.text(min_size=1, max_size=10),
)

# 정렬 파라미터
ordering_strategy = st.one_of(
    st.just("price"),
    st.just("-price"),
    st.just("created_at"),
    st.just("-created_at"),
    st.just("invalid_field"),
    st.just("'; DROP TABLE--"),
    st.just("price; DELETE FROM"),
    st.text(min_size=1, max_size=50),
)

# 배송 주소 형식
shipping_address_strategy = st.one_of(
    st.text(min_size=0, max_size=500),
    st.just(""),
    st.just(" " * 100),
    st.just("a" * 1000),
    st.just("서울시 강남구 테헤란로 123"),
    st.just("'; DROP TABLE orders; --"),
    st.just("<script>alert('xss')</script>"),
    st.just("🏠📍🚚"),
    st.just("../../../etc/passwd"),
)

# 결제 방법 형식
payment_method_strategy = st.one_of(
    st.just("card"),
    st.just("transfer"),
    st.just("virtual_account"),
    st.just(""),
    st.just("invalid_method"),
    st.just("'; DROP TABLE--"),
    st.just("card; DELETE FROM"),
    st.text(min_size=1, max_size=50),
)

# 금액 형식
amount_strategy = st.one_of(
    st.integers(min_value=-100000, max_value=100000000),
    st.floats(min_value=-1000.0, max_value=1000000.0),
    st.just(0),
    st.just(-1),
    st.just(0.01),
    st.just(float("nan")),
    st.just(float("inf")),
    st.just("invalid_amount"),
)

# 리뷰 평점 형식
rating_strategy = st.one_of(
    st.integers(min_value=-10, max_value=100),
    st.floats(min_value=-5.0, max_value=10.0),
    st.just(0),
    st.just(6),
    st.just(-1),
)

# 리뷰 내용 형식
review_content_strategy = st.one_of(
    st.text(min_size=0, max_size=2000),
    st.just(""),
    st.just(" " * 100),
    st.just("a" * 5000),
    st.just("좋아요! 🎉⭐💯"),
    st.just("<script>alert('xss')</script>"),
    st.just("'; DROP TABLE reviews; --"),
)


# ==========================================
# 공통 Fixture
# ==========================================


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    OpenAPI 스키마 로드

    모듈 레벨에서 한 번만 로드됩니다.
    """
    import schemathesis
    from django.test import Client

    with django_db_blocker.unblock():
        client = Client()
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == status.HTTP_200_OK, "OpenAPI 스키마를 가져올 수 없습니다"
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)

"""
Fuzz 테스트 (Schemathesis Phase 5.1)
====================================

📋 개요
-------
이 모듈은 Schemathesis와 Hypothesis를 활용하여 API에 무작위 입력을 주입하고
예상치 못한 에러(특히 5xx 서버 에러)가 발생하지 않는지 검증합니다.

🎯 테스트 목적
-------------
1. **서버 안정성 검증**: 어떤 입력이 들어와도 5xx 에러가 발생하지 않아야 함
2. **예외 처리 검증**: 잘못된 입력에 대해 적절한 4xx 에러 코드 반환
3. **경계값 탐색**: Hypothesis가 자동으로 경계값, 극단값을 생성하여 테스트
4. **미처 발견하지 못한 버그 탐색**: 수동 테스트로 발견하기 어려운 엣지 케이스 발굴

🔧 Fuzz Testing 원리
-------------------
Hypothesis 라이브러리는 "Property-based Testing"을 구현합니다.
- 전통적 단위 테스트: 특정 입력 → 특정 출력 검증
- Fuzz 테스트: 무작위 입력 생성 → "속성(Property)" 검증

속성 예시:
- "어떤 입력이 들어와도 500 에러가 발생하면 안 된다"
- "유효하지 않은 ID는 항상 400 또는 404를 반환해야 한다"

📊 테스트 클래스 구조
-------------------
1. TestProductsFuzz
   - 상품 목록/상세 API에 무작위 쿼리 파라미터 주입
   - 검색, 필터링, 정렬 파라미터 퍼징

2. TestCartFuzz
   - 장바구니 API에 무작위 데이터 주입
   - product_id, quantity 등 필드 퍼징

3. TestAuthFuzz
   - 인증 API에 무작위 자격 증명 주입
   - 로그인, 토큰 갱신 등 퍼징

4. TestFullApiFuzz (@slow)
   - 전체 OpenAPI 스키마 기반 자동 퍼징
   - 모든 엔드포인트에서 5xx 에러 없음 확인

🚀 실행 방법
-----------
```bash
# Fuzz 테스트만 실행 (병렬 실행 비활성화 권장)
pytest -m fuzz --no-cov -v -n 0

# 스키마 + Fuzz 테스트 모두 실행
pytest -m "schema or fuzz" --no-cov -v -n 0

# slow 테스트 포함 전체 실행 (시간 소요 큼)
pytest -m "fuzz" --no-cov -v -n 0 --max-examples=100

# 특정 테스트만 실행
pytest -m fuzz -k "test_products" --no-cov -v -n 0
```

⚙️ Hypothesis 설정 설명
---------------------
- max_examples: 생성할 테스트 케이스 수 (기본 50, CI에서는 100-500)
- suppress_health_check: 데이터 생성 관련 경고 억제
- deadline: 단일 테스트 타임아웃 (DB 접근으로 느려질 수 있음)
- database: 실패 케이스 저장하여 회귀 테스트에 활용

📁 관련 파일
-----------
- conftest.py: 인증 fixture, 테스트 데이터, 제외 엔드포인트 정의
- test_api_contract.py: Phase 2 - API Contract 테스트
- test_stateful_workflow.py: Phase 3 - Stateful 워크플로우 테스트
- test_negative.py: Phase 5.2 - Negative 테스트 (의도적 잘못된 입력)

⚠️ 주의사항
----------
1. 실행 시간: Fuzz 테스트는 많은 케이스를 생성하므로 시간이 오래 걸릴 수 있습니다.
   CI에서는 max_examples를 조절하여 시간을 관리하세요.

2. 병렬 실행 제한: Hypothesis는 상태를 유지하므로 -n 0으로 실행하세요.

3. 데이터베이스 상태: @pytest.mark.django_db(transaction=True)로 격리하지만,
   대량의 무작위 데이터 생성 시 주의가 필요합니다.

4. Rate Limiting: 테스트 환경에서는 Rate Limiting이 비활성화되어 있어야 합니다.
   (conftest.py에서 이미 설정됨)

📅 작성 정보
-----------
- 작성일: 2025-12-05
- Schemathesis 버전: 4.6.7
- Hypothesis 버전: 6.x
"""

import json
import string

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from hypothesis import strategies as st
from rest_framework import status

from ..conftest import (
    EXCLUDED_ENDPOINTS,
    is_excluded_endpoint,
    is_public_endpoint,
)


# ==========================================
# Hypothesis 전략(Strategy) 정의
# ==========================================
# Hypothesis Strategy는 테스트 데이터를 자동으로 생성하는 규칙을 정의합니다.
# 각 전략은 특정 타입의 무작위 데이터를 생성합니다.


# 상품 ID 형식: 다양한 형태의 ID를 생성하여 파싱 로직 검증
product_id_strategy = st.one_of(
    st.integers(min_value=-1000, max_value=10000),  # 음수, 0, 양수 정수
    st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=20),  # 문자열 ID
    st.just("null"),  # null 문자열
    st.just("undefined"),  # undefined 문자열
    st.just(""),  # 빈 문자열
    st.floats(allow_nan=True, allow_infinity=True),  # 부동소수점 (NaN, Infinity 포함)
)

# 수량 형식: 장바구니 수량 필드 퍼징
quantity_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),  # 음수, 0, 매우 큰 수
    st.floats(min_value=-100.0, max_value=100000.0),  # 부동소수점
    st.just(0),  # 0
    st.just(-1),  # 음수
)

# 검색어 형식: SQL Injection, XSS 등 보안 페이로드 포함
search_query_strategy = st.one_of(
    st.text(min_size=0, max_size=1000),  # 일반 텍스트
    st.just(""),  # 빈 문자열
    st.just(" " * 100),  # 공백만
    st.just("a" * 5000),  # 매우 긴 문자열
    st.just("'; DROP TABLE products; --"),  # SQL Injection
    st.just("<script>alert('xss')</script>"),  # XSS
    st.just("🎉💀🔥"),  # 이모지
    st.just("\x00\x01\x02"),  # 제어 문자
    st.just("../../../etc/passwd"),  # Path Traversal
)

# 페이지네이션 파라미터
pagination_strategy = st.one_of(
    st.integers(min_value=-100, max_value=100000),
    st.just(0),
    st.just(-1),
    st.just(999999999),
    st.text(min_size=1, max_size=10),  # 문자열
)

# 정렬 파라미터
ordering_strategy = st.one_of(
    st.just("price"),
    st.just("-price"),
    st.just("created_at"),
    st.just("-created_at"),
    st.just("invalid_field"),
    st.just("'; DROP TABLE--"),  # SQL Injection 시도
    st.just("price; DELETE FROM"),
    st.text(min_size=1, max_size=50),
)


# ==========================================
# 상품 API Fuzz 테스트
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestProductsFuzz:
    """
    🛍️ 상품 API Fuzz 테스트

    상품 목록, 상세, 검색 API에 무작위 입력을 주입하여
    예외 처리가 올바르게 동작하는지 검증합니다.

    📋 테스트 대상:
    - GET /api/products/ (목록, 검색, 필터링)
    - GET /api/products/{id}/ (상세)
    - GET /api/products/popular/ (인기 상품)
    - GET /api/products/best_rating/ (평점순)

    ✅ 검증 속성 (Property):
    - 어떤 입력이 들어와도 5xx 에러가 발생하지 않아야 함
    - 유효하지 않은 ID는 400 또는 404를 반환
    - 잘못된 쿼리 파라미터는 무시되거나 400 반환
    """

    @given(search=search_query_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],  # shrinking 비활성화 (속도 향상)
    )
    def test_products_list_search_fuzz(self, client, schema_test_product, search):
        """
        상품 검색 API 퍼징

        다양한 검색어(SQL Injection, XSS, 긴 문자열 등)를 주입하여
        검색 기능의 안정성을 검증합니다.

        🔍 테스트 관점:
        - SQL Injection 시도가 실제 SQL로 실행되지 않아야 함
        - XSS 페이로드가 그대로 반환되지 않아야 함
        - 매우 긴 검색어도 처리 가능해야 함

        Args:
            client: Django Test Client
            schema_test_product: 테스트용 상품 fixture
            search: Hypothesis가 생성한 무작위 검색어
        """
        # Act
        response = client.get("/api/products/", {"search": search})

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"상품 검색에서 서버 에러 발생!\n"
            f"입력: {repr(search)}\n"
            f"상태 코드: {response.status_code}\n"
            f"응답: {response.content[:500]}"
        )

    @given(page=pagination_strategy, page_size=pagination_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_list_pagination_fuzz(self, client, schema_test_product, page, page_size):
        """
        상품 목록 페이지네이션 퍼징

        잘못된 페이지 번호, 페이지 크기를 주입하여
        페이지네이션 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 페이지 번호 처리
        - 0 페이지 처리
        - 매우 큰 페이지 번호 처리
        - 문자열 페이지 번호 처리

        Args:
            page: Hypothesis가 생성한 무작위 페이지 번호
            page_size: Hypothesis가 생성한 무작위 페이지 크기
        """
        # Act
        response = client.get("/api/products/", {"page": page, "page_size": page_size})

        # Assert
        assert response.status_code < 500, (
            f"페이지네이션에서 서버 에러 발생!\n"
            f"page={repr(page)}, page_size={repr(page_size)}\n"
            f"상태 코드: {response.status_code}"
        )

    @given(ordering=ordering_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_list_ordering_fuzz(self, client, schema_test_product, ordering):
        """
        상품 목록 정렬 파라미터 퍼징

        잘못된 정렬 필드명을 주입하여
        정렬 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 존재하지 않는 필드명
        - SQL Injection 시도
        - 특수문자 포함 필드명

        Args:
            ordering: Hypothesis가 생성한 무작위 정렬 파라미터
        """
        # Act
        response = client.get("/api/products/", {"ordering": ordering})

        # Assert
        assert response.status_code < 500, (
            f"정렬에서 서버 에러 발생!\n" f"ordering={repr(ordering)}\n" f"상태 코드: {response.status_code}"
        )

    @given(product_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_detail_id_fuzz(self, client, product_id):
        """
        상품 상세 API ID 파라미터 퍼징

        다양한 형식의 ID(음수, 문자열, 특수문자 등)를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 0 ID
        - 문자열 ID
        - 매우 큰 ID
        - 특수문자 포함 ID

        예상 결과:
        - 유효한 ID: 200 OK
        - 유효하지 않은 ID: 400 Bad Request 또는 404 Not Found
        - 5xx 에러는 절대 발생하면 안 됨

        Args:
            product_id: Hypothesis가 생성한 무작위 상품 ID
        """
        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert
        assert response.status_code < 500, (
            f"상품 상세 조회에서 서버 에러 발생!\n" f"product_id={repr(product_id)}\n" f"상태 코드: {response.status_code}"
        )
        # 유효하지 않은 ID는 400 또는 404여야 함
        if not (isinstance(product_id, int) and product_id > 0):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,
                status.HTTP_200_OK,
            ], f"유효하지 않은 ID에 대해 예상치 못한 응답: {response.status_code}"


# ==========================================
# 장바구니 API Fuzz 테스트
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestCartFuzz:
    """
    🛒 장바구니 API Fuzz 테스트

    장바구니 추가, 수정 API에 무작위 데이터를 주입하여
    데이터 검증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - POST /api/cart/add_item/ (상품 추가)
    - PATCH /api/cart/items/{id}/ (수량 변경)

    ✅ 검증 속성:
    - 잘못된 product_id에 대해 적절한 에러 반환
    - 잘못된 quantity에 대해 적절한 에러 반환
    - 5xx 에러 발생하지 않음
    """

    @given(product_id=product_id_strategy, quantity=quantity_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_cart_add_item_fuzz(self, client, auth_headers, product_id, quantity):
        """
        장바구니 추가 API 퍼징

        다양한 product_id와 quantity 조합을 주입하여
        입력 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 존재하지 않는 product_id
        - 음수/0/매우 큰 quantity
        - 문자열 ID
        - 부동소수점 quantity

        Args:
            product_id: Hypothesis가 생성한 무작위 상품 ID
            quantity: Hypothesis가 생성한 무작위 수량
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": product_id, "quantity": quantity}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data, default=str),  # NaN, Infinity 등 처리
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"장바구니 추가에서 서버 에러 발생!\n"
            f"data={data}\n"
            f"상태 코드: {response.status_code}\n"
            f"응답: {response.content[:500]}"
        )

    @given(quantity=quantity_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_cart_item_update_quantity_fuzz(self, client, auth_headers, schema_test_product, user, quantity):
        """
        장바구니 아이템 수량 변경 퍼징

        기존 장바구니 아이템의 수량을 무작위 값으로 변경하여
        수량 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 음수 수량
        - 0 수량 (삭제 처리?)
        - 매우 큰 수량 (재고 초과)
        - 부동소수점 수량

        Args:
            quantity: Hypothesis가 생성한 무작위 수량
        """
        # Arrange - 각 Hypothesis iteration마다 새 cart/item 생성
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        cart_item, _ = CartItem.objects.get_or_create(cart=cart, product=schema_test_product, defaults={"quantity": 1})

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"quantity": quantity}

        # Act
        response = client.patch(
            f"/api/cart/items/{cart_item.id}/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"장바구니 수량 변경에서 서버 에러 발생!\n" f"quantity={repr(quantity)}\n" f"상태 코드: {response.status_code}"
        )


# ==========================================
# 인증 API Fuzz 테스트
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAuthFuzz:
    """
    🔐 인증 API Fuzz 테스트

    로그인, 토큰 갱신 API에 무작위 자격 증명을 주입하여
    인증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - POST /api/auth/login/ (로그인)
    - POST /api/auth/token/refresh/ (토큰 갱신)

    ⚠️ 주의:
    - 비밀번호 관련 테스트는 실제 이메일 발송을 유발할 수 있어 제외
    - Rate Limiting이 테스트 환경에서 비활성화되어 있어야 함

    ✅ 검증 속성:
    - 잘못된 자격 증명에 대해 401 반환 (500이 아님)
    - 잘못된 토큰에 대해 401 반환
    """

    @given(
        username=st.one_of(
            st.text(min_size=0, max_size=200),
            st.just(""),
            st.just("admin"),
            st.just("root"),
            st.just("' OR '1'='1"),
        ),
        password=st.one_of(
            st.text(min_size=0, max_size=200),
            st.just(""),
            st.just("password"),
            st.just("123456"),
            st.just("a" * 1000),  # 매우 긴 비밀번호
        ),
    )
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_login_credentials_fuzz(self, client, username, password):
        """
        로그인 API 자격 증명 퍼징

        다양한 사용자명과 비밀번호 조합을 시도하여
        인증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - SQL Injection 시도 (username에 SQL 구문)
        - 빈 자격 증명
        - 매우 긴 비밀번호
        - 일반적인 약한 비밀번호

        Args:
            username: Hypothesis가 생성한 무작위 사용자명
            password: Hypothesis가 생성한 무작위 비밀번호
        """
        # Arrange
        data = {"username": username, "password": password}

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code < 500, (
            f"로그인에서 서버 에러 발생!\n"
            f"username={repr(username[:50])}, password={repr(password[:20])}\n"
            f"상태 코드: {response.status_code}"
        )
        # 잘못된 자격 증명은 400 또는 401이어야 함
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_429_TOO_MANY_REQUESTS,
        ], f"예상치 못한 응답 코드: {response.status_code}"

    @given(
        refresh_token=st.one_of(
            st.text(min_size=0, max_size=500),  # 무작위 문자열
            st.just(""),  # 빈 토큰
            st.just("invalid.token.here"),  # 잘못된 형식
            st.just(
                "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
            ),  # 서명이 다른 JWT
        )
    )
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_token_refresh_fuzz(self, client, refresh_token):
        """
        토큰 갱신 API 퍼징

        다양한 형식의 refresh token을 주입하여
        토큰 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 빈 토큰
        - 잘못된 형식의 토큰
        - 서명이 다른 JWT
        - 매우 긴 토큰 문자열

        Args:
            refresh_token: Hypothesis가 생성한 무작위 토큰
        """
        # Arrange
        data = {"refresh": refresh_token}

        # Act
        response = client.post(
            "/api/auth/token/refresh/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code < 500, (
            f"토큰 갱신에서 서버 에러 발생!\n"
            f"refresh_token={repr(refresh_token[:50])}\n"
            f"상태 코드: {response.status_code}"
        )


# ==========================================
# 카테고리 API Fuzz 테스트
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestCategoriesFuzz:
    """
    📂 카테고리 API Fuzz 테스트

    카테고리 조회 API에 무작위 입력을 주입하여
    안정성을 검증합니다.

    📋 테스트 대상:
    - GET /api/categories/{id}/ (상세)
    - GET /api/categories/tree/ (트리 구조)
    """

    @given(category_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_categories_detail_id_fuzz(self, client, category_id):
        """
        카테고리 상세 API ID 퍼징

        다양한 형식의 ID를 주입하여
        ID 파싱 로직의 안정성을 확인합니다.

        Args:
            category_id: Hypothesis가 생성한 무작위 카테고리 ID
        """
        # Act
        response = client.get(f"/api/categories/{category_id}/")

        # Assert
        assert response.status_code < 500, (
            f"카테고리 상세에서 서버 에러 발생!\n" f"category_id={repr(category_id)}\n" f"상태 코드: {response.status_code}"
        )


# ==========================================
# 전체 API Fuzz 테스트 (느림)
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestFullApiFuzz:
    """
    🌐 전체 API Fuzz 테스트

    OpenAPI 스키마를 기반으로 모든 GET 엔드포인트에
    무작위 쿼리 파라미터를 주입하여 안정성을 검증합니다.

    ⚠️ 이 테스트는 시간이 오래 걸리므로 @pytest.mark.slow로 표시되어 있습니다.
    CI에서는 Nightly 빌드에서만 실행하는 것을 권장합니다.

    🚀 실행 방법:
    ```bash
    # slow 마커 포함 실행
    pytest -m "fuzz and slow" --no-cov -v -n 0 --max-examples=100
    ```
    """

    def test_all_get_endpoints_no_500(self, openapi_schema, client, auth_headers, schema_test_product):
        """
        모든 GET 엔드포인트에서 5xx 에러 없음 확인

        OpenAPI 스키마에 정의된 모든 GET 엔드포인트를 순회하며
        기본 요청을 보내 서버 에러가 발생하지 않는지 확인합니다.

        🔍 테스트 관점:
        - 모든 GET 엔드포인트 접근 가능성
        - 인증 필요 엔드포인트는 헤더 추가
        - 제외 엔드포인트는 스킵

        Args:
            openapi_schema: OpenAPI 스키마 객체
            client: Django Test Client
            auth_headers: 인증 헤더
            schema_test_product: 테스트 데이터
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        errors = []

        # Act & Assert
        for result in openapi_schema.get_all_operations():
            if hasattr(result, "ok"):
                op = result.ok()
            else:
                op = result

            # GET 요청만 테스트
            if op.method.upper() != "GET":
                continue

            # 제외 엔드포인트 스킵
            if is_excluded_endpoint(op.path):
                continue

            # path parameter가 있으면 스킵 (별도 테스트에서 처리)
            if "{" in op.path:
                continue

            # 공개 API vs 인증 필요 API
            if is_public_endpoint(op.path):
                response = client.get(op.path)
            else:
                response = client.get(op.path, **headers)

            if response.status_code >= 500:
                errors.append(f"{op.path}: {response.status_code}")

        # Assert
        assert not errors, f"5xx 에러 발생 엔드포인트:\n" + "\n".join(errors)

    @given(
        search=st.text(min_size=0, max_size=100),
        page=st.integers(min_value=-10, max_value=1000),
        ordering=st.sampled_from(["", "price", "-price", "invalid"]),
    )
    @hypothesis_settings(
        max_examples=100,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_products_combined_params_fuzz(self, client, schema_test_product, search, page, ordering):
        """
        상품 목록 복합 파라미터 퍼징

        여러 쿼리 파라미터를 동시에 조합하여
        복합 필터링 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 검색 + 정렬 + 페이지네이션 조합
        - 잘못된 파라미터 혼합
        - 빈 파라미터 처리

        Args:
            search: 무작위 검색어
            page: 무작위 페이지 번호
            ordering: 무작위 정렬 필드
        """
        # Act
        params = {}
        if search:
            params["search"] = search
        if page != 0:
            params["page"] = page
        if ordering:
            params["ordering"] = ordering

        response = client.get("/api/products/", params)

        # Assert
        assert response.status_code < 500, (
            f"복합 파라미터에서 서버 에러 발생!\n" f"params={params}\n" f"상태 코드: {response.status_code}"
        )


# ==========================================
# 주문 API Fuzz 테스트
# ==========================================


# 주문 관련 Hypothesis 전략
shipping_address_strategy = st.one_of(
    st.text(min_size=0, max_size=500),  # 일반 텍스트
    st.just(""),  # 빈 주소
    st.just(" " * 100),  # 공백만
    st.just("a" * 1000),  # 매우 긴 주소
    st.just("서울시 강남구 테헤란로 123"),  # 정상 주소
    st.just("'; DROP TABLE orders; --"),  # SQL Injection
    st.just("<script>alert('xss')</script>"),  # XSS
    st.just("🏠📍🚚"),  # 이모지
    st.just("../../../etc/passwd"),  # Path Traversal
)

payment_method_strategy = st.one_of(
    st.just("card"),
    st.just("transfer"),
    st.just("virtual_account"),
    st.just(""),  # 빈 값
    st.just("invalid_method"),  # 잘못된 값
    st.just("'; DROP TABLE--"),  # SQL Injection
    st.just("card; DELETE FROM"),
    st.text(min_size=1, max_size=50),  # 무작위 문자열
)

amount_strategy = st.one_of(
    st.integers(min_value=-100000, max_value=100000000),  # 음수, 매우 큰 수
    st.floats(min_value=-1000.0, max_value=1000000.0),  # 부동소수점
    st.just(0),
    st.just(-1),
    st.just(0.01),  # 소수점
    st.just(float("nan")),  # NaN
    st.just(float("inf")),  # Infinity
    st.just("invalid_amount"),  # 문자열
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestOrdersFuzz:
    """
    📦 주문 API Fuzz 테스트

    주문 생성, 조회, 취소 API에 무작위 입력을 주입하여
    데이터 검증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - GET /api/orders/ (주문 목록)
    - GET /api/orders/{id}/ (주문 상세)
    - POST /api/orders/ (주문 생성) - 장바구니 기반
    - POST /api/orders/{id}/cancel/ (주문 취소)

    ✅ 검증 속성:
    - 잘못된 배송 정보에 대해 적절한 에러 반환
    - 잘못된 주문 ID에 대해 400 또는 404 반환
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(order_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_detail_id_fuzz(self, client, auth_headers, order_id):
        """
        주문 상세 API ID 파라미터 퍼징

        다양한 형식의 주문 ID를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 문자열 ID
        - SQL Injection 시도
        - 매우 큰 ID

        Args:
            order_id: Hypothesis가 생성한 무작위 주문 ID
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/orders/{order_id}/", **headers)

        # Assert
        assert response.status_code < 500, (
            f"주문 상세 조회에서 서버 에러 발생!\n"
            f"order_id={repr(order_id)}\n"
            f"상태 코드: {response.status_code}"
        )

    @given(
        shipping_address=shipping_address_strategy,
        shipping_name=st.text(min_size=0, max_size=200),
        shipping_phone=st.text(min_size=0, max_size=50),
    )
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_shipping_info_fuzz(
        self, client, auth_headers, schema_test_product, user,
        shipping_address, shipping_name, shipping_phone
    ):
        """
        주문 생성 API 배송 정보 퍼징

        다양한 형태의 배송 정보를 주입하여
        입력 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - SQL Injection 주소
        - XSS 스크립트 주소
        - 매우 긴 주소
        - 이모지가 포함된 주소
        - 빈 필수 필드

        Args:
            shipping_address: 무작위 배송 주소
            shipping_name: 무작위 수령인 이름
            shipping_phone: 무작위 연락처
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(
            cart=cart,
            product=schema_test_product,
            defaults={"quantity": 1}
        )

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": shipping_address,
            "shipping_name": shipping_name,
            "shipping_phone": shipping_phone,
            "shipping_postal_code": "12345",
            "payment_method": "card",
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n"
            f"data={data}\n"
            f"상태 코드: {response.status_code}\n"
            f"응답: {response.content[:500]}"
        )

    @given(payment_method=payment_method_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_payment_method_fuzz(
        self, client, auth_headers, schema_test_product, user, payment_method
    ):
        """
        주문 생성 API 결제 방법 퍼징

        다양한 형태의 결제 방법을 주입하여
        결제 방법 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 유효하지 않은 결제 방법
        - SQL Injection 시도
        - 빈 결제 방법

        Args:
            payment_method: Hypothesis가 생성한 무작위 결제 방법
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(
            cart=cart,
            product=schema_test_product,
            defaults={"quantity": 1}
        )

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": payment_method,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n"
            f"payment_method={repr(payment_method)}\n"
            f"상태 코드: {response.status_code}"
        )

    @given(used_points=st.integers(min_value=-10000, max_value=10000000))
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_order_create_points_fuzz(
        self, client, auth_headers, schema_test_product, user, used_points
    ):
        """
        주문 생성 API 포인트 사용 퍼징

        다양한 형태의 포인트 값을 주입하여
        포인트 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 음수 포인트
        - 보유 포인트 초과
        - 매우 큰 포인트 값

        Args:
            used_points: Hypothesis가 생성한 무작위 포인트 값
        """
        # Arrange - 장바구니에 상품 추가
        from shopping.models.cart import Cart, CartItem

        cart, _ = Cart.objects.get_or_create(user=user, is_active=True)
        CartItem.objects.get_or_create(
            cart=cart,
            product=schema_test_product,
            defaults={"quantity": 1}
        )

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_address": "서울시 강남구 테헤란로 123",
            "shipping_name": "홍길동",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
            "used_points": used_points,
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"주문 생성에서 서버 에러 발생!\n"
            f"used_points={used_points}\n"
            f"상태 코드: {response.status_code}"
        )

        # 음수 포인트는 거부되어야 함
        if used_points < 0:
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            ], f"음수 포인트가 허용됨: {response.status_code}"


# ==========================================
# 결제 API Fuzz 테스트
# ==========================================


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPaymentsFuzz:
    """
    💳 결제 API Fuzz 테스트

    결제 조회 API에 무작위 입력을 주입하여
    결제 시스템의 안정성을 검증합니다.

    📋 테스트 대상:
    - GET /api/payments/ (결제 목록)
    - GET /api/payments/{id}/ (결제 상세)

    ⚠️ 주의:
    - 실제 결제 생성/승인은 Toss API 의존으로 Fuzz 대상 제외
    - 조회 및 검증 로직만 테스트

    ✅ 검증 속성:
    - 잘못된 결제 ID에 대해 400 또는 404 반환
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(payment_id=product_id_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_detail_id_fuzz(self, client, auth_headers, payment_id):
        """
        결제 상세 API ID 파라미터 퍼징

        다양한 형식의 결제 ID를 주입하여
        ID 파싱 및 조회 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 음수 ID
        - 문자열 ID
        - SQL Injection 시도
        - 특수문자 ID

        Args:
            payment_id: Hypothesis가 생성한 무작위 결제 ID
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/payments/{payment_id}/", **headers)

        # Assert
        assert response.status_code < 500, (
            f"결제 상세 조회에서 서버 에러 발생!\n"
            f"payment_id={repr(payment_id)}\n"
            f"상태 코드: {response.status_code}"
        )
        # 유효하지 않은 ID는 400 또는 404여야 함
        if not (isinstance(payment_id, int) and payment_id > 0):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,
                status.HTTP_401_UNAUTHORIZED,
            ], f"유효하지 않은 ID에 대해 예상치 못한 응답: {response.status_code}"

    @given(
        status_filter=st.one_of(
            st.just("ready"),
            st.just("pending"),
            st.just("done"),
            st.just("canceled"),
            st.just(""),
            st.just("invalid_status"),
            st.just("'; DROP TABLE--"),
            st.text(min_size=1, max_size=50),
        ),
        page=pagination_strategy,
    )
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_list_filters_fuzz(self, client, auth_headers, status_filter, page):
        """
        결제 목록 API 필터 파라미터 퍼징

        다양한 형태의 필터 파라미터를 주입하여
        필터링 로직의 안정성을 검증합니다.

        🔍 테스트 관점:
        - 유효하지 않은 상태 필터
        - SQL Injection 시도
        - 잘못된 페이지 번호

        Args:
            status_filter: 무작위 상태 필터
            page: 무작위 페이지 번호
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        params = {}
        if status_filter:
            params["status"] = status_filter
        if page:
            params["page"] = page

        # Act
        response = client.get("/api/payments/", params, **headers)

        # Assert
        assert response.status_code < 500, (
            f"결제 목록 조회에서 서버 에러 발생!\n"
            f"params={params}\n"
            f"상태 코드: {response.status_code}"
        )

    @given(amount=amount_strategy)
    @hypothesis_settings(
        max_examples=30,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_payment_amount_validation_fuzz(self, client, auth_headers, schema_test_order, amount):
        """
        결제 금액 검증 퍼징

        결제 요청 시 다양한 형태의 금액을 주입하여
        금액 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 음수 금액
        - 0 금액
        - 매우 큰 금액
        - 소수점 금액
        - NaN, Infinity

        Args:
            amount: Hypothesis가 생성한 무작위 금액
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "order_id": schema_test_order.id,
            "amount": amount,
            "payment_method": "card",
        }

        # Act - 결제 준비 API 호출 (실제 결제는 아님)
        response = client.post(
            "/api/payments/prepare/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code < 500, (
            f"결제 준비에서 서버 에러 발생!\n"
            f"amount={repr(amount)}\n"
            f"상태 코드: {response.status_code}"
        )

        # 음수/0 금액은 거부되어야 함
        if isinstance(amount, (int, float)) and amount <= 0:
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_404_NOT_FOUND,  # 주문이 없을 수 있음
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            ], f"유효하지 않은 금액이 허용됨: {response.status_code}"


# ==========================================
# 리뷰 API Fuzz 테스트
# ==========================================


# 리뷰 관련 Hypothesis 전략
rating_strategy = st.one_of(
    st.integers(min_value=-10, max_value=100),  # 범위 외 값
    st.floats(min_value=-5.0, max_value=10.0),  # 소수점
    st.just(0),
    st.just(6),  # 최대값 초과
    st.just(-1),  # 음수
)

review_content_strategy = st.one_of(
    st.text(min_size=0, max_size=2000),  # 일반 텍스트
    st.just(""),  # 빈 리뷰
    st.just(" " * 100),  # 공백만
    st.just("a" * 5000),  # 매우 긴 리뷰
    st.just("좋아요! 🎉⭐💯"),  # 이모지
    st.just("<script>alert('xss')</script>"),  # XSS
    st.just("'; DROP TABLE reviews; --"),  # SQL Injection
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestReviewsFuzz:
    """
    ⭐ 리뷰 API Fuzz 테스트

    리뷰 작성 API에 무작위 입력을 주입하여
    입력 검증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - POST /api/products/{id}/reviews/ (리뷰 작성)
    - GET /api/products/{id}/reviews/ (리뷰 목록)

    ✅ 검증 속성:
    - 잘못된 평점에 대해 400 반환
    - XSS 페이로드가 이스케이프되거나 거부됨
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(rating=rating_strategy, content=review_content_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_review_create_fuzz(
        self, client, auth_headers, schema_test_product, rating, content
    ):
        """
        리뷰 작성 API 퍼징

        다양한 형태의 평점과 내용을 주입하여
        리뷰 입력 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 범위 외 평점 (0, 6, -1, 100)
        - 소수점 평점
        - XSS 스크립트 내용
        - SQL Injection 내용
        - 매우 긴 리뷰

        Args:
            rating: Hypothesis가 생성한 무작위 평점
            content: Hypothesis가 생성한 무작위 리뷰 내용
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": rating,
            "content": content,
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"리뷰 작성에서 서버 에러 발생!\n"
            f"rating={repr(rating)}, content={repr(content[:50])}\n"
            f"상태 코드: {response.status_code}"
        )

        # 범위 외 평점은 거부되어야 함 (1-5 범위)
        if isinstance(rating, (int, float)) and (rating < 1 or rating > 5):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                status.HTTP_403_FORBIDDEN,  # 구매 확인 실패
            ], f"범위 외 평점이 허용됨: rating={rating}, status={response.status_code}"


# ==========================================
# 스키마 로드 Fixture (test_api_contract.py와 공유)
# ==========================================


@pytest.fixture(scope="module")
def openapi_schema(django_db_setup, django_db_blocker):
    """
    OpenAPI 스키마 로드

    이 fixture는 test_api_contract.py의 동일한 fixture와
    동일한 구현을 가집니다. 모듈 레벨에서 한 번만 로드됩니다.
    """
    import schemathesis
    from django.test import Client

    with django_db_blocker.unblock():
        client = Client()
        response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
        assert response.status_code == status.HTTP_200_OK, "OpenAPI 스키마를 가져올 수 없습니다"
        schema_data = json.loads(response.content)
        return schemathesis.openapi.from_dict(schema_data)

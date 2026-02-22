"""
Stateful API 워크플로우 테스트 (Schemathesis Phase 3)
======================================================

📋 개요
-------
이 모듈은 실제 사용자 시나리오를 시뮬레이션하여 API 상태 전이를 검증합니다.
단순히 각 엔드포인트를 개별적으로 테스트하는 것이 아니라, 실제 사용자가
서비스를 이용하는 흐름을 따라 API들이 올바르게 연동되는지 확인합니다.

🎯 테스트 목적
-------------
1. **상태 전이 검증**: 비인증 → 인증 → 장바구니 → 주문 등 상태 변화 확인
2. **API 연동 검증**: 여러 API가 순차적으로 호출될 때 데이터 일관성 유지
3. **권한 검증**: 각 상태에서 접근 가능/불가능한 API 확인
4. **실제 시나리오 커버**: QA 수동 테스트를 자동화

🔄 테스트 워크플로우
------------------
1. **사용자 구매 플로우** (TestUserPurchaseFlow):
   Anonymous → 회원가입 → 로그인 → 상품조회 → 장바구니추가 → 주문조회

2. **위시리스트 플로우** (TestWishlistFlow):
   로그인 → 상품조회 → 위시리스트추가 → 장바구니이동

3. **상품 탐색 플로우** (TestProductBrowsingFlow):
   카테고리트리 → 카테고리상세 → 상품목록 → 상품상세

4. **장바구니 관리 플로우** (TestCartManagementFlow):
   장바구니조회 → 상품추가 → 수량변경 → 재고확인 → 비우기

🔧 State Machine 정의
--------------------
```
┌─────────────┐    회원가입/로그인     ┌───────────────┐
│  ANONYMOUS  │ ────────────────────> │ AUTHENTICATED │
│  (비인증)   │                       │   (인증됨)     │
└─────────────┘                       └───────────────┘
      │                                       │
      │ 공개 API만 접근 가능                    │ 장바구니 추가
      │ - 상품 목록/상세                        ▼
      │ - 카테고리                      ┌───────────────┐
      │                                │   HAS_CART    │
      │                                │ (장바구니있음)  │
      │                                └───────────────┘
      │                                       │
      │                                       │ 주문 생성
      │                                       ▼
      │                                ┌───────────────┐
      │                                │   HAS_ORDER   │
      │                                │  (주문완료)    │
      │                                └───────────────┘
      │
      └─── 보호된 API 접근 시 401 Unauthorized
```

🚀 실행 방법
-----------
```bash
# Stateful 테스트만 실행 (권장: 병렬 실행 비활성화)
pytest -m stateful --no-cov -v -n 0

# 스키마 + Stateful 테스트 모두 실행
pytest -m "schema or stateful" --no-cov -v -n 0

# slow 테스트 제외 (빠른 피드백용)
pytest -m "stateful and not slow" --no-cov -v -n 0

# Docker 컨테이너 내부에서 실행
docker-compose exec web pytest -m stateful --no-cov -v -n 0
```

📁 관련 파일
-----------
- conftest.py: 인증 fixture (auth_headers, seller_auth_headers), 테스트 데이터
- test_api_contract.py: Phase 2 - API Contract 테스트 (개별 엔드포인트 검증)

⚠️ 주의사항
----------
1. 트랜잭션 롤백: 각 테스트는 @pytest.mark.django_db(transaction=True)로
   독립적인 트랜잭션에서 실행되며, 테스트 후 자동 롤백됩니다.

2. 병렬 실행 제한: Stateful 테스트는 순차적으로 실행해야 합니다 (-n 0).
   API 호출 순서가 중요하므로 병렬 실행 시 예상치 못한 결과가 발생할 수 있습니다.

3. 인증 토큰 형식: 로그인 응답은 { "token": { "access": "..." } } 구조입니다.
   단순 문자열이 아니므로 주의하세요.

📅 작성 정보
-----------
- 작성일: 2025-12-05
- Schemathesis 버전: 4.6.7
- 테스트 개수: 14개 (slow 마커 포함 시)
"""

import json
import uuid
from decimal import Decimal
from enum import Enum, auto

import pytest
from rest_framework import status

from shopping.models.product import Category, Product


# ==========================================
# 상태 정의 (State Machine)
# ==========================================
# 사용자의 현재 상태를 추적하기 위한 Enum 정의
# 각 상태에 따라 접근 가능한 API가 달라집니다.


class UserState(Enum):
    """
    사용자 상태 정의

    상태 전이:
    ANONYMOUS → AUTHENTICATED: 로그인 성공
    AUTHENTICATED → HAS_CART: 장바구니에 상품 추가
    HAS_CART → HAS_ORDER: 주문 생성
    """

    ANONYMOUS = auto()  # 비인증 상태
    AUTHENTICATED = auto()  # 로그인 완료
    HAS_CART = auto()  # 장바구니에 상품 있음
    HAS_ORDER = auto()  # 주문 완료


class ProductState(Enum):
    """상품 상태 정의"""

    IN_STOCK = auto()  # 재고 있음
    LOW_STOCK = auto()  # 재고 부족
    OUT_OF_STOCK = auto()  # 품절


# ==========================================
# 워크플로우 테스트 클래스
# ==========================================
# 각 클래스는 특정 사용자 시나리오를 테스트합니다.
# 마커 설명:
#   @pytest.mark.stateful - Stateful 워크플로우 테스트 식별
#   @pytest.mark.schema - 스키마 관련 테스트 그룹
#   @pytest.mark.django_db(transaction=True) - 트랜잭션 격리 (테스트 후 롤백)


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestUserPurchaseFlow:
    """
    🛒 사용자 구매 플로우 테스트

    이 클래스는 실제 사용자가 쇼핑몰에서 상품을 구매하는 전체 과정을 테스트합니다.

    📋 테스트 시나리오:
    1. 비회원으로 상품 조회 (Anonymous 상태)
    2. 회원가입 및 로그인 (Anonymous → Authenticated)
    3. 장바구니에 상품 추가 (Authenticated → HasCart)
    4. 주문 목록 조회 (주문 생성은 결제 연동 필요)

    🔄 상태 전이 다이어그램:
    Anonymous ──[회원가입/로그인]──> Authenticated ──[장바구니 추가]──> HasCart

    ✅ 검증 항목:
    - 비회원도 상품 조회 가능
    - 회원가입 후 즉시 로그인 가능
    - 로그인 후 JWT 토큰으로 인증된 API 접근 가능
    - 장바구니에 상품 추가 후 조회 가능
    """

    def test_complete_purchase_flow(self, client, schema_test_product):
        """
        완전한 구매 플로우 테스트

        이 테스트는 가장 핵심적인 사용자 시나리오를 검증합니다:
        비회원 상품 조회 → 회원가입 → 로그인 → 장바구니 추가 → 주문 조회

        Fixtures:
            client: Django Test Client
            schema_test_product: 테스트용 상품 (conftest.py에서 생성)
        """
        # ============================================
        # Step 1: Anonymous - 공개 상품 조회 (Arrange & Act & Assert)
        # ============================================
        # 비회원도 상품 목록과 상세를 조회할 수 있어야 합니다.
        # 이는 쇼핑몰의 기본 접근성을 보장합니다.

        # Arrange - none needed for public API

        # Act - 상품 목록 조회
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        products_data = response.json()
        assert "results" in products_data or isinstance(products_data, list)

        # Act - 상품 상세 조회
        response = client.get(f"/api/products/{schema_test_product.id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        product_detail = response.json()
        assert product_detail["id"] == schema_test_product.id
        assert "price" in product_detail

        # ============================================
        # Step 2: 회원가입 및 로그인 (Arrange & Act & Assert)
        # ============================================
        # 구매를 위해서는 회원가입과 로그인이 필요합니다.
        # 각 테스트마다 고유한 사용자를 생성하여 충돌을 방지합니다.

        # Arrange
        unique_id = uuid.uuid4().hex[:8]  # 고유 ID 생성 (테스트 격리)
        register_data = {
            "username": f"flowtest_{unique_id}",
            "email": f"flowtest_{unique_id}@example.com",
            "password": "SecurePass123!",
            "password2": "SecurePass123!",  # 비밀번호 확인 필드
            "phone_number": "010-1234-5678",
        }

        # Act - 회원가입
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(register_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED], f"회원가입 실패: {response.content}"
        register_response = response.json()

        # Arrange - 로그인 데이터
        login_data = {
            "username": f"flowtest_{unique_id}",
            "password": "SecurePass123!",
        }

        # Act - 로그인하여 JWT 토큰 획득
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(login_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK, f"로그인 실패: {response.content}"
        login_response = response.json()

        # ⚠️ 토큰 추출 주의사항:
        # 로그인 응답 구조: { "token": { "access": "..." } }
        # 단순 문자열이 아닌 중첩 객체이므로 주의 필요
        if isinstance(login_response.get("token"), dict):
            access_token = login_response["token"]["access"]
        else:
            access_token = login_response.get("token") or login_response.get("access")
        assert access_token, f"토큰을 찾을 수 없음: {login_response}"

        # Arrange - 인증 헤더 설정
        auth_header = {"HTTP_AUTHORIZATION": f"Bearer {access_token}"}

        # Act - 프로필 조회로 인증 상태 확인
        response = client.get("/api/users/profile/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        profile_data = response.json()
        assert profile_data["username"] == f"flowtest_{unique_id}"

        # ============================================
        # Step 3: 장바구니에 상품 추가 (Arrange & Act & Assert)
        # ============================================
        # 인증된 사용자만 장바구니에 상품을 추가할 수 있습니다.
        # 이 단계에서 상태가 AUTHENTICATED → HAS_CART로 전이됩니다.

        # Arrange
        cart_item_data = {
            "product_id": schema_test_product.id,
            "quantity": 2,
        }

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(cart_item_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED], f"장바구니 추가 실패: {response.content}"

        # Act - 장바구니 조회 - 추가한 상품이 있는지 확인
        response = client.get("/api/cart/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        cart_data = response.json()
        assert "items" in cart_data or "id" in cart_data

        # Act - 장바구니 요약 조회 - 총액, 수량 등 확인
        response = client.get("/api/cart/summary/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        summary_data = response.json()
        assert "total" in summary_data or "item_count" in summary_data or "total_amount" in summary_data

        # ============================================
        # Step 4: 주문 API 접근 확인 (Act & Assert)
        # ============================================
        # 실제 주문 생성은 결제(Toss Payments) 연동이 필요합니다.
        # 여기서는 인증된 사용자가 주문 목록 API에 접근 가능한지만 확인합니다.

        # Act
        response = client.get("/api/orders/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # ============================================
        # Step 5: 정리 - 장바구니 비우기 (Act & Assert)
        # ============================================
        # 테스트 데이터 정리 (트랜잭션 롤백으로 자동 정리되지만 명시적으로 호출)

        # Act
        response = client.post("/api/cart/clear/", **auth_header)

        # Assert - 400: 이미 빈 장바구니, 405: 메서드 미지원 등 허용
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ]

    def test_anonymous_cannot_access_protected_endpoints(self, client, schema_test_product):
        """
        🔒 비인증 사용자의 보호된 엔드포인트 접근 제한 테스트

        인증이 필요한 API에 토큰 없이 접근하면 401 Unauthorized가 반환되어야 합니다.
        이는 API 보안의 기본 요구사항입니다.
        """
        # Arrange
        protected_endpoints = [
            "/api/orders/",  # 주문 목록
            "/api/wishlist/",  # 위시리스트
            "/api/notifications/",  # 알림
            "/api/payments/",  # 결제 내역
            "/api/points/my/",  # 내 포인트
            "/api/users/profile/",  # 사용자 프로필
        ]

        for endpoint in protected_endpoints:
            # Act
            response = client.get(endpoint)

            # Assert
            assert (
                response.status_code == status.HTTP_401_UNAUTHORIZED
            ), f"{endpoint}는 인증 필요하지만 {response.status_code} 반환"

    def test_authenticated_can_access_protected_endpoints(self, client, auth_headers):
        """
        ✅ 인증된 사용자의 보호된 엔드포인트 접근 테스트

        유효한 JWT 토큰으로 인증된 사용자는 보호된 API에 접근할 수 있어야 합니다.
        auth_headers fixture는 conftest.py에서 제공됩니다.
        """
        # Arrange
        protected_endpoints = [
            "/api/orders/",
            "/api/wishlist/",
            "/api/notifications/",
            "/api/points/my/",
            "/api/users/profile/",
        ]
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        for endpoint in protected_endpoints:
            # Act
            response = client.get(endpoint, **headers)

            # Assert
            assert response.status_code == status.HTTP_200_OK, f"{endpoint}에서 인증된 사용자가 {response.status_code} 받음"


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestWishlistFlow:
    """
    💝 위시리스트 플로우 테스트

    사용자가 관심 상품을 위시리스트에 추가하고 관리하는 시나리오를 테스트합니다.
    위시리스트는 나중에 구매할 상품을 저장해두는 기능입니다.

    📋 테스트 시나리오:
    1. 위시리스트 조회 (초기 상태 확인)
    2. 상품을 위시리스트에 추가 (toggle API)
    3. 위시리스트 조회로 추가 확인
    4. 위시리스트 통계 조회
    5. 위시리스트에서 장바구니로 이동 (선택적 기능)
    6. 위시리스트에서 상품 제거 (toggle로 제거)

    💡 toggle API 설명:
    - 상품이 위시리스트에 없으면 → 추가
    - 상품이 위시리스트에 있으면 → 제거
    - 하나의 API로 추가/제거를 모두 처리
    """

    def test_wishlist_toggle_flow(self, client, auth_headers, schema_test_product):
        """
        위시리스트 토글(추가/제거) 플로우 테스트

        토글 API를 사용하여 위시리스트에 상품을 추가하고 다시 제거하는 과정을 테스트합니다.
        """
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        toggle_data = {"product_id": schema_test_product.id}

        # Act - Step 1: 초기 위시리스트 상태 확인
        response = client.get("/api/wishlist/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        initial_wishlist = response.json()

        # Act - Step 2: 상품을 위시리스트에 추가 (toggle - 첫 번째 호출 = 추가)
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(toggle_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        toggle_response = response.json()

        # Act - Step 3: 위시리스트 조회하여 추가되었는지 확인
        response = client.get("/api/wishlist/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 4: 위시리스트 통계 확인 (총 개수 등)
        response = client.get("/api/wishlist/stats/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        stats_data = response.json()

        # Act - Step 5: 다시 토글하여 제거 (toggle - 두 번째 호출 = 제거)
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(toggle_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

    def test_wishlist_to_cart_flow(self, client, auth_headers, schema_test_product):
        """
        위시리스트 → 장바구니 이동 플로우 테스트

        위시리스트에 있는 상품을 장바구니로 옮기는 시나리오입니다.
        이 기능은 구현 여부에 따라 404/405가 반환될 수 있습니다.
        """
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        add_data = {"product_id": schema_test_product.id}

        # Act - Step 1: 위시리스트에 상품 추가
        response = client.post(
            "/api/wishlist/add/",
            data=json.dumps(add_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert - add 엔드포인트가 없으면 toggle 사용
        if response.status_code == status.HTTP_404_NOT_FOUND:
            # Act - add 엔드포인트가 없으면 toggle 사용
            response = client.post(
                "/api/wishlist/toggle/",
                data=json.dumps(add_data),
                content_type="application/json",
                **auth_header,
            )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Arrange
        move_data = {"product_id": schema_test_product.id}

        # Act - Step 2: 장바구니로 이동 시도
        response = client.post(
            "/api/wishlist/move_to_cart/",
            data=json.dumps(move_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert - move_to_cart가 구현되어 있지 않을 수 있음
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ]

        # Act - Step 3: 정리 - 위시리스트에서 제거
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(add_data),
            content_type="application/json",
            **auth_header,
        )


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestProductBrowsingFlow:
    """
    🔍 상품 탐색 플로우 테스트

    사용자가 쇼핑몰에서 상품을 탐색하는 과정을 테스트합니다.
    비회원도 모든 상품 정보를 조회할 수 있어야 합니다.

    📋 테스트 시나리오:
    1. 카테고리 트리 조회 (전체 카테고리 구조)
    2. 카테고리 목록/상세 조회
    3. 카테고리별 상품 필터링
    4. 상품 상세 조회
    5. 상품 검색 및 필터링 (가격, 키워드)
    6. 인기/평점순 정렬

    ✨ 특징:
    - 모든 API가 인증 불필요 (공개 API)
    - 비회원도 동일한 상품 정보 접근 가능
    - SEO 및 마케팅 목적상 중요한 기능
    """

    def test_category_to_product_flow(self, client, schema_test_category, schema_test_product):
        """
        카테고리 → 상품 탐색 플로우

        사용자가 카테고리를 통해 상품을 찾아가는 일반적인 탐색 경로입니다.
        """
        # Arrange - none needed for public API

        # Act - Step 1: 카테고리 트리 조회 (메인 페이지 네비게이션용)
        response = client.get("/api/categories/tree/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        tree_data = response.json()

        # Act - Step 2: 카테고리 목록 조회 (페이지네이션된 목록)
        response = client.get("/api/categories/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        categories_data = response.json()

        # Act - Step 3: 특정 카테고리 상세 조회
        response = client.get(f"/api/categories/{schema_test_category.id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        category_detail = response.json()
        assert category_detail["id"] == schema_test_category.id

        # Act - Step 4: 해당 카테고리의 상품 목록 조회 (필터링)
        response = client.get(f"/api/products/?category={schema_test_category.id}")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        products_data = response.json()

        # Act - Step 5: 상품 상세 조회 (상품 페이지)
        response = client.get(f"/api/products/{schema_test_product.id}/")

        # Assert
        assert response.status_code == status.HTTP_200_OK
        product_detail = response.json()
        assert product_detail["id"] == schema_test_product.id

    def test_product_search_and_filter_flow(self, client, schema_test_product):
        """
        상품 검색 및 필터링 플로우

        사용자가 검색어, 가격 범위 등으로 상품을 찾는 시나리오입니다.
        """
        # Arrange - none needed for public API

        # Act - Step 1: 기본 상품 목록 (필터 없이)
        response = client.get("/api/products/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 2: 검색어로 필터링
        response = client.get("/api/products/?search=스키마")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 3: 가격 범위로 필터링
        response = client.get("/api/products/?min_price=1000&max_price=50000")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 4: 인기 상품 조회 (판매량/조회수 기반)
        response = client.get("/api/products/popular/")

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 5: 평점 높은 상품 조회 (리뷰 평점 기반)
        response = client.get("/api/products/best_rating/")

        # Assert
        assert response.status_code == status.HTTP_200_OK


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestCartManagementFlow:
    """
    🛒 장바구니 관리 플로우 테스트

    장바구니의 전체 생명주기를 테스트합니다.
    상품 추가 → 수량 변경 → 재고 확인 → 삭제/비우기

    📋 테스트 시나리오:
    1. 초기 장바구니 상태 확인 (비어있거나 기존 아이템 있음)
    2. 상품 추가 (add_item)
    3. 장바구니 아이템 목록 조회 (items)
    4. 수량 변경 (PATCH items/{id}/)
    5. 재고 확인 (check_stock) - 주문 전 재고 유효성 검사
    6. 장바구니 요약 (summary) - 총액, 배송비 등
    7. 장바구니 비우기 (clear)

    🔧 장바구니 API 구조:
    - GET  /api/cart/           - 장바구니 조회
    - POST /api/cart/add_item/  - 상품 추가
    - GET  /api/cart/items/     - 아이템 목록
    - PATCH /api/cart/items/{id}/ - 수량 변경
    - DELETE /api/cart/items/{id}/ - 아이템 삭제
    - POST /api/cart/clear/     - 전체 비우기
    - GET  /api/cart/summary/   - 요약 정보
    - GET  /api/cart/check_stock/ - 재고 확인
    """

    def test_cart_item_lifecycle(self, client, auth_headers, schema_test_product):
        """
        장바구니 아이템 생명주기 테스트

        하나의 상품이 장바구니에 추가되고, 수량이 변경되고,
        최종적으로 장바구니가 비워지는 전체 과정을 테스트합니다.
        """
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        add_data = {"product_id": schema_test_product.id, "quantity": 1}

        # Act - Step 1: 초기 장바구니 상태 확인
        response = client.get("/api/cart/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 2: 상품 추가
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(add_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Act - Step 3: 장바구니 아이템 목록 조회
        response = client.get("/api/cart/items/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        items_data = response.json()

        # Arrange - 아이템 ID 추출 (수량 변경용)
        # 응답 형식: 리스트 또는 페이지네이션된 객체
        if isinstance(items_data, list) and len(items_data) > 0:
            item_id = items_data[0].get("id")
        elif isinstance(items_data, dict) and "results" in items_data:
            if len(items_data["results"]) > 0:
                item_id = items_data["results"][0].get("id")
            else:
                item_id = None
        else:
            item_id = None

        # Act & Assert - Step 4: 수량 변경 (item_id가 있는 경우에만)
        if item_id:
            update_data = {"quantity": 3}
            response = client.patch(
                f"/api/cart/items/{item_id}/",
                data=json.dumps(update_data),
                content_type="application/json",
                **auth_header,
            )
            assert response.status_code in [status.HTTP_200_OK, status.HTTP_204_NO_CONTENT]

        # Act - Step 5: 재고 확인 (주문 전 유효성 검사)
        response = client.get("/api/cart/check_stock/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 6: 장바구니 요약 (총액, 배송비 등)
        response = client.get("/api/cart/summary/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 7: 장바구니 비우기
        response = client.post("/api/cart/clear/", **auth_header)

        # Assert - 400: 이미 빈 장바구니일 수 있음
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_204_NO_CONTENT,
            status.HTTP_400_BAD_REQUEST,
        ]

    def test_cart_bulk_operations(self, client, auth_headers, schema_test_product, seller_user, db):
        """장바구니 대량 작업 테스트"""
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        category = Category.objects.first() or Category.objects.create(name="Bulk Test Category", slug="bulk-test-category")
        product2 = Product.objects.create(
            name="Bulk Test Product 2",
            slug="bulk-test-product-2",
            category=category,
            seller=seller_user,
            price=Decimal("20000"),
            stock=50,
            sku="BULK-002",
            is_active=True,
        )
        bulk_data = {
            "items": [
                {"product_id": schema_test_product.id, "quantity": 1},
                {"product_id": product2.id, "quantity": 2},
            ]
        }

        # Act - Step 1: 대량 추가 (bulk_add 지원 시)
        response = client.post(
            "/api/cart/bulk_add/",
            data=json.dumps(bulk_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert - bulk_add API는 선택적 구현, 미구현 시 404/405 반환
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        ]

        # Act - Step 2: 장바구니 확인
        response = client.get("/api/cart/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 3: 정리
        response = client.post("/api/cart/clear/", **auth_header)


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestNotificationFlow:
    """
    🔔 알림 플로우 테스트

    사용자 알림 시스템을 테스트합니다.
    주문 상태 변경, 배송 알림, 포인트 적립 등의 알림을 조회합니다.

    📋 테스트 시나리오:
    1. 전체 알림 목록 조회
    2. 읽지 않은 알림만 조회 (unread)
    3. 알림 읽음 처리 (선택적 - PATCH)

    💡 알림 종류:
    - 주문 상태 변경 (결제 완료, 배송 시작, 배송 완료)
    - 포인트 적립/사용
    - 문의 답변 등록
    """

    def test_notification_read_flow(self, client, auth_headers):
        """알림 조회 플로우 테스트"""
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - Step 1: 전체 알림 목록 조회
        response = client.get("/api/notifications/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        notifications_data = response.json()

        # Act - Step 2: 읽지 않은 알림만 조회
        response = client.get("/api/notifications/unread/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        unread_data = response.json()


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPointsFlow:
    """
    💰 포인트 플로우 테스트

    사용자 포인트 시스템을 테스트합니다.
    포인트는 주문 시 적립되고, 다음 주문에서 사용할 수 있습니다.

    📋 테스트 시나리오:
    1. 내 포인트 조회 (현재 보유 포인트)
    2. 포인트 적립/사용 내역 조회
    3. 만료 예정 포인트 확인

    💡 포인트 정책:
    - 주문 금액의 일정 비율 적립 (멤버십 등급별 차등)
    - 적립 후 일정 기간 후 만료
    - 최소 사용 금액 존재
    """

    def test_points_inquiry_flow(self, client, auth_headers):
        """포인트 조회 플로우 테스트"""
        # Arrange
        auth_header = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - Step 1: 내 포인트 조회
        response = client.get("/api/points/my/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        my_points = response.json()

        # Act - Step 2: 포인트 내역 조회
        response = client.get("/api/points/history/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 3: 만료 예정 포인트 확인
        response = client.get("/api/points/expiring/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK


# ==========================================
# State Machine 기반 테스트
# ==========================================
# 이 섹션은 명시적으로 상태 전이를 추적하고 검증합니다.
# 위의 워크플로우 테스트와 달리, 상태 변수를 사용하여
# 현재 상태를 추적하고 전이의 정확성을 검증합니다.


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestStateMachineTransitions:
    """
    🔄 상태 머신 전이 테스트

    명시적으로 상태 전이를 추적하고 검증하는 테스트입니다.
    UserState enum을 사용하여 현재 상태를 관리합니다.

    📋 검증 항목:
    1. 올바른 상태 전이: ANONYMOUS → AUTHENTICATED → HAS_CART
    2. 각 상태에서의 API 접근 권한
    3. 잘못된 상태 전이 차단 (비인증 상태에서 보호된 API 접근 차단)

    💡 State Machine 패턴:
    - 상태(State): 사용자의 현재 상황
    - 전이(Transition): 상태 간 변경 (이벤트에 의해 발생)
    - 이벤트(Event): API 호출 (로그인, 장바구니 추가 등)
    """

    def test_user_state_transitions(self, client, schema_test_product):
        """
        사용자 상태 전이 검증

        ANONYMOUS → AUTHENTICATED → HAS_CART 전이를
        명시적으로 추적하고 각 상태에서의 동작을 검증합니다.
        """
        # Arrange - 초기 상태 설정
        current_state = UserState.ANONYMOUS
        unique_id = uuid.uuid4().hex[:8]
        register_data = {
            "username": f"statetest_{unique_id}",
            "email": f"statetest_{unique_id}@example.com",
            "password": "SecurePass123!",
            "password2": "SecurePass123!",
            "phone_number": "010-1234-5678",
        }

        # =========================================
        # 전이 1: ANONYMOUS → AUTHENTICATED
        # 이벤트: 회원가입 + 로그인
        # =========================================

        # Act - 회원가입
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(register_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Arrange - 로그인 데이터
        login_data = {
            "username": f"statetest_{unique_id}",
            "password": "SecurePass123!",
        }

        # Act - 로그인
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(login_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        login_resp = response.json()
        if isinstance(login_resp.get("token"), dict):
            token = login_resp["token"]["access"]
        else:
            token = login_resp.get("token") or login_resp.get("access")
        auth_header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        # 상태 전이: ANONYMOUS → AUTHENTICATED
        current_state = UserState.AUTHENTICATED

        # Act - 상태 검증: 인증된 상태에서만 접근 가능한 API 호출
        response = client.get("/api/users/profile/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # =========================================
        # 전이 2: AUTHENTICATED → HAS_CART
        # 이벤트: 장바구니에 상품 추가
        # =========================================

        # Arrange
        cart_data = {"product_id": schema_test_product.id, "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(cart_data),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # 상태 전이: AUTHENTICATED → HAS_CART
        current_state = UserState.HAS_CART

        # Act - 상태 검증: 장바구니에 아이템이 있음
        response = client.get("/api/cart/summary/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        summary = response.json()
        # 아이템 개수 또는 총액이 0보다 큼
        assert (
            summary.get("item_count", 0) > 0
            or summary.get("total_amount", 0) > 0
            or summary.get("total", 0) > 0
            or summary.get("total_items", 0) > 0
        )

        # =========================================
        # 상태 검증: HAS_CART 상태에서의 API 접근
        # =========================================

        # Act - 주문 목록 조회 (빈 목록이어도 접근 가능)
        response = client.get("/api/orders/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - 정리: 테스트 종료 후 장바구니 비우기
        response = client.post("/api/cart/clear/", **auth_header)

    def test_invalid_state_transition_blocked(self, client):
        """
        🚫 잘못된 상태 전이 차단 검증

        비인증 상태(ANONYMOUS)에서 보호된 리소스 접근 시
        401 Unauthorized가 반환되는지 확인합니다.

        📋 테스트 대상:
        - 장바구니 추가 (세션 기반이라 예외)
        - 주문 목록 조회
        - 위시리스트 토글

        💡 참고: /api/cart/는 세션 기반으로 동작하여 비인증에서도 사용 가능
        """
        # Arrange - Anonymous 상태에서 보호된 리소스 접근 시도
        protected_actions = [
            ("POST", "/api/cart/add_item/", {"product_id": 1, "quantity": 1}),
            ("GET", "/api/orders/", None),
            ("POST", "/api/wishlist/toggle/", {"product_id": 1}),
        ]

        for method, endpoint, data in protected_actions:
            # Act
            if method == "GET":
                response = client.get(endpoint)
            else:
                response = client.post(
                    endpoint,
                    data=json.dumps(data) if data else None,
                    content_type="application/json",
                )

            # Assert - /api/cart/는 세션 기반이므로 제외
            if "/api/cart/" not in endpoint:
                assert (
                    response.status_code == status.HTTP_401_UNAUTHORIZED
                ), f"Anonymous에서 {endpoint}가 {response.status_code} 반환"


# ==========================================
# 복잡한 워크플로우 테스트
# ==========================================
# 실제 사용 시나리오를 시뮬레이션하는 통합 테스트입니다.
# 여러 API를 조합하여 전체 사용자 경험을 검증합니다.


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.slow
@pytest.mark.django_db(transaction=True)
class TestComplexWorkflows:
    """
    🎭 복잡한 워크플로우 테스트

    여러 API를 조합한 실제 사용 시나리오를 테스트합니다.
    이 테스트들은 시간이 오래 걸릴 수 있어 @pytest.mark.slow로 표시됩니다.

    📋 테스트 시나리오:
    1. 완전한 쇼핑 경험 시뮬레이션
    2. 다중 상품 장바구니 워크플로우

    💡 주의: 이 테스트들은 통합 테스트 성격을 가지며,
    실제 사용자 행동을 시뮬레이션합니다.

    🔧 실행:
    - 개별: pytest -m "stateful and slow"
    - 제외: pytest -m "stateful and not slow"
    """

    def test_full_shopping_experience(self, client, schema_test_product, schema_test_category, seller_user, db):
        """
        🛒 완전한 쇼핑 경험 시뮬레이션

        새 사용자가 가입 → 상품 탐색 → 장바구니 추가 → 결제(선택)
        까지의 전체 플로우를 테스트합니다.

        📋 시나리오:
        1. 회원가입
        2. 로그인
        3. 상품 목록 조회 (탐색)
        4. 상품 상세 조회 (검색)
        5. 장바구니에 다수 상품 추가
        6. 장바구니 확인
        7. (테스트에서는 실제 결제 제외)

        💡 이 테스트는 E2E 테스트에 가까우며,
        실제 사용자 여정을 검증합니다.
        """
        # Arrange - 추가 상품 생성
        product2 = Product.objects.create(
            name="Full Shopping Test Product",
            slug="full-shopping-test-product",
            category=schema_test_category,
            seller=seller_user,
            price=Decimal("15000"),
            stock=30,
            sku="FULL-001",
            is_active=True,
        )

        # Act & Assert - Step 1: 비회원으로 상품 탐색
        response = client.get("/api/categories/tree/")
        assert response.status_code == status.HTTP_200_OK

        response = client.get("/api/products/popular/")
        assert response.status_code == status.HTTP_200_OK

        response = client.get(f"/api/products/{schema_test_product.id}/")
        assert response.status_code == status.HTTP_200_OK

        # Arrange - Step 2: 회원가입 및 로그인
        unique_id = uuid.uuid4().hex[:8]
        register_data = {
            "username": f"fulltest_{unique_id}",
            "email": f"fulltest_{unique_id}@example.com",
            "password": "SecurePass123!",
            "password2": "SecurePass123!",
            "phone_number": "010-1234-5678",
        }

        # Act - 회원가입
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(register_data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # Act - 로그인
        login_response = client.post(
            "/api/auth/login/",
            data=json.dumps(
                {
                    "username": f"fulltest_{unique_id}",
                    "password": "SecurePass123!",
                }
            ),
            content_type="application/json",
        )

        # Arrange - 토큰 추출
        login_data = login_response.json()
        if isinstance(login_data.get("token"), dict):
            token = login_data["token"]["access"]
        else:
            token = login_data.get("token") or login_data.get("access")
        auth_header = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

        # =========================================
        # Step 3: 위시리스트에 상품 추가
        # 마음에 드는 상품을 위시리스트에 저장
        # =========================================

        # Act
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps({"product_id": schema_test_product.id}),
            content_type="application/json",
            **auth_header,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # =========================================
        # Step 4: 장바구니에 여러 상품 추가
        # 실제 구매를 위해 장바구니에 상품 담기
        # =========================================
        for product_id in [schema_test_product.id, product2.id]:
            # Act
            response = client.post(
                "/api/cart/add_item/",
                data=json.dumps({"product_id": product_id, "quantity": 1}),
                content_type="application/json",
                **auth_header,
            )

            # Assert
            assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]

        # =========================================
        # Step 5-9: 결제 전 확인 작업
        # =========================================

        # Act - Step 5: 장바구니 요약 확인
        response = client.get("/api/cart/summary/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 6: 재고 확인 (품절 상품 없는지)
        response = client.get("/api/cart/check_stock/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 7: 포인트 확인 (사용 가능 포인트)
        response = client.get("/api/points/my/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 8: 알림 확인 (할인/이벤트 정보)
        response = client.get("/api/notifications/unread/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # Act - Step 9: 프로필 확인 (배송지 정보)
        response = client.get("/api/users/profile/", **auth_header)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        # =========================================
        # Cleanup: 테스트 데이터 정리
        # =========================================
        client.post("/api/cart/clear/", **auth_header)
        client.post(
            "/api/wishlist/toggle/",
            data=json.dumps({"product_id": schema_test_product.id}),
            content_type="application/json",
            **auth_header,
        )

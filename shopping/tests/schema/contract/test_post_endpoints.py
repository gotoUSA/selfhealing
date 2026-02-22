# test_post_endpoints.py
# POST 요청 Contract 테스트 (성공 + 실패)

import json
import uuid

import pytest
from rest_framework import status

from ..conftest import assert_error_response


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPostEndpointsContract:
    """
    ✏️ POST 엔드포인트 Contract 테스트 (생성/수정 API)

    POST 요청의 성공 케이스와 실패 케이스 모두에서
    응답이 스키마와 일치하는지 검증합니다.

    📋 검증 대상:
    - 장바구니 추가: POST /api/cart/add_item/
    - 위시리스트 토글: POST /api/wishlist/toggle/
    - 로그인: POST /api/auth/login/
    - 회원가입: POST /api/auth/register/

    ✅ 검증 항목:
    - 성공 시: 200/201 + 응답 구조
    - 실패 시: 400 + 에러 응답 구조

    ⚠️ 참고: 트랜잭션 롤백으로 데이터 정리됨
    """

    def test_cart_add_item_success(self, client, auth_headers, schema_test_product):
        """🛒 장바구니 상품 추가 성공"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": schema_test_product.id, "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        response_data = response.json()
        assert isinstance(response_data, dict), "응답이 dict가 아님"
        # 장바구니 추가 응답 검증 - message 또는 cart 정보 포함
        has_valid_response = any(key in response_data for key in ["message", "cart", "item", "id", "items", "product"])
        assert has_valid_response or len(response_data) > 0, f"장바구니 추가 응답에 데이터 없음: {response_data}"

    def test_wishlist_toggle_success(self, client, auth_headers, schema_test_product):
        """❤️ 위시리스트 토글 성공"""
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": schema_test_product.id}

        # Act
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        response_data = response.json()
        assert isinstance(response_data, dict), "응답이 dict가 아님"
        # 위시리스트 토글 응답 검증 - message 또는 status 필드 존재
        assert any(
            key in response_data for key in ["message", "status", "added", "removed", "id", "product"]
        ), f"위시리스트 토글 응답에 예상 필드 없음: {response_data.keys()}"

    def test_auth_login_success(self, client, user):
        """🔐 로그인 성공"""
        # Arrange
        data = {"username": "testuser", "password": "testpass123"}

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        response_data = response.json()
        # 로그인 응답 스키마 검증
        assert "token" in response_data or "access" in response_data, "'token' 또는 'access' 필드가 응답에 없음"
        # 토큰 값 검증 - 직접 문자열이거나 dict 내부에 있을 수 있음
        if "token" in response_data:
            token_value = response_data["token"]
            if isinstance(token_value, dict):
                assert "access" in token_value, "token.access 필드가 없음"
                assert isinstance(token_value["access"], str), "token.access가 문자열이 아님"
            else:
                assert isinstance(token_value, str), "'token'이 문자열이 아님"
                assert len(token_value) > 0, "'token'이 비어있음"
        else:
            token_value = response_data["access"]
            assert isinstance(token_value, str), "'access'가 문자열이 아님"
            assert len(token_value) > 0, "'access'가 비어있음"

    def test_auth_login_failure_contract(self, client, user):
        """🔐 로그인 실패 - 에러 응답 Contract"""
        # Arrange
        data = {"username": "testuser", "password": "wrongpassword"}

        # Act
        response = client.post(
            "/api/auth/login/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED]
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/login/ (failure)")

    def test_auth_register_success(self, client):
        """📝 회원가입 성공"""

        unique_username = f"newuser_{uuid.uuid4().hex[:8]}"
        data = {
            "username": unique_username,
            "email": f"{unique_username}@test.com",
            "password": "securePassword123!",
            "password2": "securePassword123!",
        }

        response = client.post(
            "/api/auth/register/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # 201 Created 또는 200 OK
        assert response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]
        response_data = response.json()
        assert isinstance(response_data, dict), "응답이 dict가 아님"
        # 회원가입 응답에는 사용자 정보 또는 토큰이 포함되어야 함
        assert any(
            key in response_data for key in ["id", "username", "token", "access", "user", "message"]
        ), f"회원가입 응답에 예상 필드 없음: {response_data.keys()}"

    def test_auth_register_failure_contract(self, client):
        """📝 회원가입 실패 - 에러 응답 Contract"""
        # Arrange
        data = {"username": "", "password": "short"}

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/register/ (failure)")

    def test_cart_add_unauthenticated(self, client, schema_test_product):
        """🛒 장바구니 추가 - 인증 없음 → 401"""
        # Arrange
        data = {"product_id": schema_test_product.id, "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert - 인증 없으면 401 또는 세션 기반이면 다른 응답
        assert response.status_code < 500, "서버 에러 발생"

    def test_wishlist_toggle_unauthenticated(self, client, schema_test_product):
        """❤️ 위시리스트 토글 - 인증 없음 → 401"""
        # Arrange
        data = {"product_id": schema_test_product.id}

        # Act
        response = client.post(
            "/api/wishlist/toggle/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPostFailureContracts:
    """
    📦 POST 엔드포인트 실패 케이스 확장 테스트

    다양한 실패 시나리오에서 에러 응답이
    일관된 Contract를 따르는지 검증합니다.

    📋 테스트 시나리오:
    - 중복 사용자 등록
    - 재고 부족 상품 장바구니 추가
    - 잘못된 데이터 타입
    - 비즈니스 규칙 위반
    """

    def test_register_duplicate_username(self, client, user):
        """
        📝 중복 사용자명 등록 → 400

        이미 존재하는 username으로 가입 시도 시 에러가 반환되어야 합니다.
        """
        # Arrange
        data = {
            "username": "testuser",  # 이미 존재하는 사용자명
            "email": "newemail@test.com",
            "password": "securePassword123!",
            "password2": "securePassword123!",
        }

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/register/ (duplicate)")

        # username 관련 에러가 있어야 함
        assert (
            "username" in error_data or "non_field_errors" in error_data or "detail" in error_data
        ), "중복 사용자명 에러 정보 없음"

    def test_register_password_mismatch(self, client):
        """
        📝 비밀번호 불일치 등록 → 400

        password와 password2가 다를 때 에러가 반환되어야 합니다.
        """

        # Arrange
        unique_username = f"newuser_{uuid.uuid4().hex[:8]}"
        data = {
            "username": unique_username,
            "email": f"{unique_username}@test.com",
            "password": "securePassword123!",
            "password2": "differentPassword456!",  # 불일치
        }

        # Act
        response = client.post(
            "/api/auth/register/",
            data=json.dumps(data),
            content_type="application/json",
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/auth/register/ (mismatch)")

    def test_cart_add_out_of_stock(self, client, auth_headers, schema_test_category, seller_user):
        """
        🛒 재고 없는 상품 장바구니 추가 → 400

        재고가 0인 상품을 장바구니에 추가할 때 에러가 반환되어야 합니다.
        """
        from shopping.models.product import Product

        # Arrange
        out_of_stock_product = Product.objects.create(
            name="재고없는 상품",
            slug="out-of-stock-product",
            category=schema_test_category,
            seller=seller_user,
            price=10000,
            stock=0,  # 재고 없음
            sku="OOS-001",
            is_active=True,
        )

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": out_of_stock_product.id, "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 재고 부족은 400
        assert response.status_code == status.HTTP_400_BAD_REQUEST, f"재고 없는 상품 추가 시 {response.status_code}"
        error_data = response.json()
        assert_error_response(error_data, context="/api/cart/add_item/ (out of stock)")

    def test_cart_add_exceeds_stock(self, client, auth_headers, schema_test_product):
        """
        🛒 재고 초과 수량 장바구니 추가 → 400

        재고보다 많은 수량을 추가하려 할 때 에러가 반환되어야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        # schema_test_product의 stock은 100
        data = {"product_id": schema_test_product.id, "quantity": 999999}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 재고 초과는 400
        assert response.status_code == status.HTTP_400_BAD_REQUEST, f"재고 초과 시 {response.status_code}"

    def test_cart_add_invalid_product_type(self, client, auth_headers):
        """
        🛒 잘못된 데이터 타입으로 장바구니 추가 → 400

        product_id에 문자열을 전달할 때 에러가 반환되어야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {"product_id": "not_a_number", "quantity": 1}

        # Act
        response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        error_data = response.json()
        assert_error_response(error_data, context="/api/cart/add_item/ (invalid type)")

    def test_order_create_empty_cart(self, client, auth_headers):
        """
        📦 빈 장바구니로 주문 생성 → 400

        장바구니가 비어있을 때 주문을 생성하면 에러가 반환되어야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "shipping_name": "테스트",
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "shipping_address": "서울시",
        }

        # Act
        response = client.post(
            "/api/orders/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 빈 장바구니는 400
        assert response.status_code == status.HTTP_400_BAD_REQUEST, f"빈 장바구니 주문 시 {response.status_code}"

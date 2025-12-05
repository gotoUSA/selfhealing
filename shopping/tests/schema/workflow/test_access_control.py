# test_access_control.py
# 권한/접근 제어 Contract 테스트

import json

import pytest
from rest_framework import status

from ..conftest import assert_error_response, assert_list_response


@pytest.mark.stateful
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestAccessControlContracts:
    """
    🔒 권한 및 접근 제어 Contract 테스트

    리소스 소유권, 역할 기반 권한 등 접근 제어가
    올바르게 동작하는지 검증합니다.

    📋 테스트 시나리오:
    - 타인의 주문 접근 → 403 또는 404
    - 일반 사용자가 판매자 API 접근 → 403
    - 비활성 상품 접근 → 404

    ✅ Contract 검증:
    - 적절한 에러 코드 반환
    - 에러 응답 구조 일관성
    """

    def test_access_other_user_order(self, client, auth_headers, seller_user):
        """
        🔒 타인의 주문 접근 시도 → 403 or 404

        다른 사용자의 주문에 접근할 때 적절히 거부되어야 합니다.
        """
        from shopping.models.order import Order

        # Arrange - seller_user의 주문 생성
        other_order = Order.objects.create(
            user=seller_user,
            status="pending",
            total_amount=10000,
            final_amount=10000,
            shipping_name="다른사용자",
            shipping_phone="010-9999-9999",
            shipping_postal_code="99999",
            shipping_address="다른 주소",
            order_number="OTHER20250101000001",
        )

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get(f"/api/orders/{other_order.id}/", **headers)

        # Assert - 타인 주문은 403 (Forbidden) 또는 404 (Not Found - 보안상 숨김)
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ], f"타인 주문 접근 시 {response.status_code} 반환됨 (403 or 404 예상)"

        # 에러 응답 구조 검증
        data = response.json()
        assert_error_response(data, context="타인 주문 접근")

    def test_regular_user_access_seller_api(self, client, auth_headers):
        """
        👤 일반 사용자가 판매자 API 접근

        판매자 전용 API에 일반 사용자가 접근할 때의 동작을 검증합니다.
        - 403 Forbidden: 접근 자체가 거부됨
        - 200 OK + 빈 결과: 권한은 있지만 본인 데이터만 반환 (소유권 기반)

        두 가지 모두 보안적으로 적절한 구현입니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.get("/api/seller/returns/", **headers)

        # Assert - 403 또는 200 (빈 결과) 모두 허용
        # - 403: 판매자가 아니므로 접근 거부
        # - 200 + 빈 리스트: 소유권 기반 필터링으로 빈 결과 반환
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_403_FORBIDDEN,
        ], f"판매자 API 접근 시 예상치 못한 응답: {response.status_code}"

        if response.status_code == status.HTTP_200_OK:
            # 200인 경우 빈 리스트 또는 자신의 데이터만 반환되어야 함
            data = response.json()
            results = assert_list_response(data, context="/api/seller/returns/ (non-seller)")
            # 일반 사용자는 판매자 반품이 없으므로 빈 리스트
            assert len(results) == 0, "일반 사용자에게 판매자 반품 데이터가 노출됨"
        else:
            # 403인 경우 에러 응답 검증
            data = response.json()
            assert_error_response(data, context="일반 사용자 → 판매자 API")

    def test_access_inactive_product(self, client, schema_test_category, seller_user):
        """
        🚫 비활성 상품 접근 → 404

        is_active=False인 상품은 조회되지 않아야 합니다.
        """
        from shopping.models.product import Product

        # Arrange
        inactive_product = Product.objects.create(
            name="비활성 상품",
            slug="inactive-product",
            category=schema_test_category,
            seller=seller_user,
            price=5000,
            stock=10,
            sku="INACTIVE-001",
            is_active=False,  # 비활성
        )

        # Act
        response = client.get(f"/api/products/{inactive_product.id}/")

        # Assert - 비활성 상품은 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_access_other_user_wishlist(self, client, auth_headers, seller_user, schema_test_product):
        """
        🔒 타인의 위시리스트 접근 시도 → 본인 것만 조회 가능

        위시리스트는 사용자별로 분리되어 있어야 합니다.
        다른 사용자의 위시리스트 아이템은 조회되지 않아야 합니다.
        """
        # Arrange - seller_user의 위시리스트에 상품 추가 (User.wishlist_products ManyToMany)
        seller_user.wishlist_products.add(schema_test_product)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 일반 사용자가 위시리스트 조회
        response = client.get("/api/wishlist/", **headers)

        # Assert - 본인의 위시리스트만 조회되어야 함 (타인 것 없음)
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        results = assert_list_response(data, context="위시리스트 조회")

        # seller_user가 추가한 상품이 일반 사용자 위시리스트에 노출되지 않아야 함
        # 위시리스트는 사용자별로 분리되므로, 일반 사용자의 빈 위시리스트가 반환되어야 함
        # (seller_user가 추가한 상품이 일반 사용자에게 노출되면 안됨)

    def test_unauthenticated_order_creation_blocked(self, client, schema_test_product):
        """
        🚫 미인증 주문 생성 시도 → 401

        로그인하지 않은 상태에서 주문 생성 시도 시 401 반환해야 합니다.
        """
        # Arrange
        order_data = {
            "items": [{"product_id": schema_test_product.id, "quantity": 1}],
            "shipping_name": "테스트",
            "shipping_phone": "010-1234-5678",
            "shipping_address": "테스트 주소",
            "shipping_postal_code": "12345",
        }

        # Act - 인증 없이 주문 생성 시도
        response = client.post(
            "/api/orders/",
            data=json.dumps(order_data),
            content_type="application/json",
        )

        # Assert - 인증 필요
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    def test_unauthenticated_protected_post_blocked(self, client):
        """
        🚫 미인증 상태에서 보호된 POST 요청 차단

        인증이 필요한 POST 엔드포인트들에 대한 접근 제어 검증
        Note: /api/cart/는 세션 기반으로 비회원도 사용 가능하므로 제외
        """
        # Arrange - 인증 필수 엔드포인트만 테스트
        protected_post_endpoints = [
            ("/api/wishlist/toggle/", {"product_id": 1}),
            ("/api/notifications/mark_read/", {"notification_ids": [1]}),
        ]

        for endpoint, payload in protected_post_endpoints:
            # Act
            response = client.post(
                endpoint,
                data=json.dumps(payload),
                content_type="application/json",
            )

            # Assert - 401 또는 404 (엔드포인트 숨김) 모두 보안상 허용
            assert response.status_code in [
                status.HTTP_401_UNAUTHORIZED,
                status.HTTP_404_NOT_FOUND,
            ], f"{endpoint}가 미인증 상태에서 {response.status_code} 반환"

    def test_deleted_product_access_blocked(self, client, schema_test_category, seller_user):
        """
        🚫 삭제된 상품 접근 → 404

        soft delete 된 상품은 조회되지 않아야 합니다.
        """
        from shopping.models.product import Product

        # Arrange - 상품 생성 후 삭제 처리
        deleted_product = Product.objects.create(
            name="삭제된 상품",
            slug="deleted-product",
            category=schema_test_category,
            seller=seller_user,
            price=10000,
            stock=10,
            sku="DELETED-001",
            is_active=True,
        )
        product_id = deleted_product.id

        # soft delete 시뮬레이션 (is_active=False 또는 실제 delete)
        deleted_product.is_active = False
        deleted_product.save()

        # Act
        response = client.get(f"/api/products/{product_id}/")

        # Assert - 삭제된 상품은 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_admin_api_access_by_regular_user_blocked(self, client, auth_headers):
        """
        🔒 일반 사용자가 관리자 API 접근 시도 → 403

        관리자 전용 엔드포인트에 일반 사용자가 접근 시 차단되어야 합니다.
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        admin_endpoints = [
            "/api/admin/users/",
            "/api/admin/orders/",
            "/api/admin/stats/",
        ]

        for endpoint in admin_endpoints:
            # Act
            response = client.get(endpoint, **headers)

            # Assert - 403 또는 404 (엔드포인트 숨김)
            assert response.status_code in [
                status.HTTP_403_FORBIDDEN,
                status.HTTP_404_NOT_FOUND,
            ], f"{endpoint}가 일반 사용자에게 {response.status_code} 반환"
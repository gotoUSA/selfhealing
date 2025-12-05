# test_access_control.py
# 권한/접근 제어 Contract 테스트

import pytest
from rest_framework import status

from ..conftest import assert_error_response, assert_list_response


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

        # seller_user의 주문 생성
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
        response = client.get(f"/api/orders/{other_order.id}/", **headers)

        # 타인 주문은 403 (Forbidden) 또는 404 (Not Found - 보안상 숨김)
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
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        response = client.get("/api/seller/returns/", **headers)

        # 403 또는 200 (빈 결과) 모두 허용
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

        response = client.get(f"/api/products/{inactive_product.id}/")

        # 비활성 상품은 404
        assert response.status_code == status.HTTP_404_NOT_FOUND

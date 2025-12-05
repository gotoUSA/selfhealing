"""
장바구니 API Fuzz 테스트
========================

장바구니 추가, 수정 API에 무작위 데이터를 주입하여
데이터 검증 로직의 안정성을 확인합니다.

📋 테스트 대상:
- POST /api/cart/add_item/ (상품 추가)
- PATCH /api/cart/items/{id}/ (수량 변경)

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_cart.py -v -n 0
```
"""

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck

from .conftest import (
    product_id_strategy,
    quantity_strategy,
)


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

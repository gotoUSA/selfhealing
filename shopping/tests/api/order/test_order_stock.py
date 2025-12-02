from django.urls import reverse

import pytest
from rest_framework import status

from shopping.models.order import Order, OrderItem
from shopping.models.product import Product


@pytest.mark.django_db
class TestOrderStockDecrease:
    """주문 생성 시 재고 차감 테스트"""

    def test_stock_decreases_on_order_creation(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """단일 상품 주문 생성 시 재고 차감"""
        # Arrange
        initial_stock = product.stock
        order_quantity = 2
        add_to_cart_helper(user, product, quantity=order_quantity)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data

        product.refresh_from_db()
        assert product.stock == initial_stock - order_quantity

    def test_stock_decreases_for_multiple_products(
        self, authenticated_client, user, multiple_products, add_to_cart_helper, shipping_data
    ):
        """여러 상품 주문 생성 시 모든 재고 차감"""
        # Arrange
        initial_stocks = {p.id: p.stock for p in multiple_products}
        order_quantity = 2

        for product in multiple_products:
            add_to_cart_helper(user, product, quantity=order_quantity)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data

        for product in multiple_products:
            product.refresh_from_db()
            expected_stock = initial_stocks[product.id] - order_quantity
            assert product.stock == expected_stock

    def test_order_with_exact_stock_available(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """재고와 정확히 같은 수량 주문 시 재고 0"""
        # Arrange
        product.stock = 5
        product.save()

        add_to_cart_helper(user, product, quantity=5)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data

        product.refresh_from_db()
        assert product.stock == 0

    def test_order_with_one_item_remaining(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """재고 1개 남도록 주문"""
        # Arrange
        product.stock = 10
        product.save()

        add_to_cart_helper(user, product, quantity=9)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_202_ACCEPTED
        assert "order_id" in response.data

        product.refresh_from_db()
        assert product.stock == 1

    def test_order_fails_when_stock_insufficient(self, authenticated_client, user, product, add_to_cart_helper, shipping_data):
        """재고 부족 시 주문 실패"""
        # Arrange
        product.stock = 5
        product.save()

        add_to_cart_helper(user, product, quantity=10)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "재고가 부족합니다" in str(response.data)

        product.refresh_from_db()
        assert product.stock == 5

    def test_order_fails_when_product_out_of_stock(
        self, authenticated_client, user, out_of_stock_product, add_to_cart_helper, shipping_data
    ):
        """품절 상품 주문 시 실패"""
        # Arrange
        assert out_of_stock_product.stock == 0

        add_to_cart_helper(user, out_of_stock_product, quantity=1)

        url = reverse("order-list")

        # Act
        response = authenticated_client.post(url, shipping_data, format="json")

        # Assert
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "품절되었습니다" in str(response.data)


@pytest.mark.django_db
class TestOrderStockRestore:
    """주문 취소 시 재고 복구 테스트"""

    def test_pending_order_cancel_restores_stock(self, authenticated_client, user, product, order_factory):
        """pending 상태 주문 취소 시 재고 복구"""
        # Arrange
        initial_stock = product.stock
        order_quantity = 3

        order = order_factory(user, status="pending", total_amount=product.price * order_quantity)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=order_quantity,
            price=product.price,
        )

        # 재고 차감 시뮬레이션
        Product.objects.filter(pk=product.pk).update(stock=product.stock - order_quantity)
        product.refresh_from_db()
        assert product.stock == initial_stock - order_quantity

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.data["message"] == "주문이 취소되었습니다."

        order.refresh_from_db()
        assert order.status == "canceled"

        product.refresh_from_db()
        assert product.stock == initial_stock

    def test_paid_order_cancel_restores_stock_and_sold_count(self, authenticated_client, user, product, order_factory):
        """paid 상태 주문 취소 시 재고 복구 및 sold_count 차감"""
        # Arrange
        initial_stock = product.stock
        initial_sold_count = product.sold_count
        order_quantity = 2

        order = order_factory(user, status="paid", total_amount=product.price * order_quantity, payment_method="card")
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=order_quantity,
            price=product.price,
        )

        # paid 상태 시뮬레이션 (재고 차감 + sold_count 증가)
        Product.objects.filter(pk=product.pk).update(
            stock=product.stock - order_quantity, sold_count=product.sold_count + order_quantity
        )
        product.refresh_from_db()
        assert product.stock == initial_stock - order_quantity
        assert product.sold_count == initial_sold_count + order_quantity

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == "canceled"

        product.refresh_from_db()
        assert product.stock == initial_stock
        assert product.sold_count == initial_sold_count

    def test_multiple_products_cancel_restores_all_stock(self, authenticated_client, user, multiple_products, order_factory):
        """여러 상품 주문 취소 시 모든 재고 복구"""
        # Arrange
        initial_stocks = {p.id: p.stock for p in multiple_products}
        order_quantity = 2

        order = order_factory(user, status="pending", total_amount=sum(p.price for p in multiple_products) * order_quantity)

        for product in multiple_products:
            OrderItem.objects.create(
                order=order,
                product=product,
                product_name=product.name,
                quantity=order_quantity,
                price=product.price,
            )
            # 재고 차감 시뮬레이션
            Product.objects.filter(pk=product.pk).update(stock=product.stock - order_quantity)

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        for product in multiple_products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id]

    def test_cancel_with_zero_stock_product(self, authenticated_client, user, product, order_factory):
        """재고 0인 상품 주문 취소 시 재고 복구"""
        # Arrange
        order_quantity = 3

        order = order_factory(user, status="pending", total_amount=product.price * order_quantity)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=order_quantity,
            price=product.price,
        )

        # 재고를 0으로 만듦
        Product.objects.filter(pk=product.pk).update(stock=0)
        product.refresh_from_db()
        assert product.stock == 0

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        product.refresh_from_db()
        assert product.stock == order_quantity

    def test_cancel_restores_large_quantity(self, authenticated_client, user, product, order_factory):
        """대량 주문 취소 시 재고 정확히 복구"""
        # Arrange
        product.stock
        large_quantity = 50

        product.stock = 100
        product.save()

        order = order_factory(user, status="pending", total_amount=product.price * large_quantity)
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=large_quantity,
            price=product.price,
        )

        # 재고 차감 시뮬레이션
        Product.objects.filter(pk=product.pk).update(stock=product.stock - large_quantity)

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        product.refresh_from_db()
        assert product.stock == 100

    def test_cancel_with_deleted_product_skips_restore(self, authenticated_client, user, product, order_factory):
        """상품이 삭제된 주문 취소 시 재고 복구 스킵"""
        # Arrange
        order = order_factory(user, status="pending", total_amount=product.price * 2)
        order_item = OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            quantity=2,
            price=product.price,
        )

        product_id = product.id

        # 상품 삭제
        product.delete()
        order_item.refresh_from_db()
        assert order_item.product is None

        url = reverse("order-cancel", kwargs={"pk": order.id})

        # Act
        response = authenticated_client.post(url)

        # Assert
        assert response.status_code == status.HTTP_200_OK

        order.refresh_from_db()
        assert order.status == "canceled"

        # 삭제된 상품은 복구 불가
        assert not Product.objects.filter(id=product_id).exists()

    def test_already_canceled_order_no_double_restore(self, authenticated_client, pending_order, product):
        """이미 취소된 주문 재취소 시 재고 중복 복구 방지"""
        # Arrange
        product.stock

        # 첫 번째 취소
        url = reverse("order-cancel", kwargs={"pk": pending_order.id})
        response1 = authenticated_client.post(url)
        assert response1.status_code == status.HTTP_200_OK

        product.refresh_from_db()
        stock_after_first_cancel = product.stock

        # Act - 두 번째 취소 시도
        response2 = authenticated_client.post(url)

        # Assert
        assert response2.status_code == status.HTTP_400_BAD_REQUEST
        assert "취소할 수 없는 주문" in response2.data["error"]

        product.refresh_from_db()
        assert product.stock == stock_after_first_cancel

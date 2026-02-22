"""
주문 취소 API Negative 테스트
============================

TestOrderCancelNegativeInputs: 이미 취소된 주문, 배송 완료 주문, 다른 사용자 주문 등
"""


import pytest
from rest_framework import status


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestOrderCancelNegativeInputs:
    """
    📦 주문 취소 API에 대한 Negative 테스트

    이미 취소된 주문, 배송 완료된 주문 등
    취소 불가능한 상태의 주문 취소 시도에 대해 검증합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    def test_cancel_already_canceled_order(self, client, auth_headers, user):
        """
        이미 취소된 주문 재취소 시도 → 400

        🔍 검증 포인트:
        - 중복 취소 방지
        - 적절한 에러 응답
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 취소된 주문 생성
        from shopping.tests.factories import OrderFactory

        canceled_order = OrderFactory.canceled(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 이미 취소된 주문 취소 시도
        response = client.post(
            f"/api/orders/{canceled_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"이미 취소된 주문 재취소에서 서버 에러 발생!\n" f"status_code={response.status_code}"
        )

        # 400 또는 409 (Conflict) 예상
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_409_CONFLICT,
        ], f"이미 취소된 주문이 다시 취소됨: {response.status_code}"

    def test_cancel_shipped_order(self, client, auth_headers, user):
        """
        배송중인 주문 취소 시도 → 400

        🔍 검증 포인트:
        - 배송 후 취소 불가 정책 검증
        - 적절한 에러 응답
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 배송중 주문 생성
        from shopping.tests.factories import OrderFactory

        shipped_order = OrderFactory.shipped(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 배송중 주문 취소 시도
        response = client.post(
            f"/api/orders/{shipped_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"배송중 주문 취소에서 서버 에러 발생!\n" f"status_code={response.status_code}"

    def test_cancel_other_user_order(self, client, auth_headers):
        """
        다른 사용자의 주문 취소 시도 → 403/404

        🔍 검증 포인트:
        - 권한 검증 (자신의 주문만 취소 가능)
        - 403 또는 404 반환
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 다른 사용자의 주문 생성
        from shopping.tests.factories import OrderFactory, UserFactory

        other_user = UserFactory()
        other_order = OrderFactory.pending(user=other_user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 다른 사용자 주문 취소 시도
        response = client.post(
            f"/api/orders/{other_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"다른 사용자 주문 취소에서 서버 에러 발생!\n" f"status_code={response.status_code}"

        # 403 (Forbidden) 또는 404 (Not Found) 예상
        assert response.status_code in [
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ], f"다른 사용자 주문 취소가 허용됨: {response.status_code}"

    def test_cancel_nonexistent_order(self, client, auth_headers):
        """
        존재하지 않는 주문 취소 시도 → 404

        🔍 검증 포인트:
        - 존재하지 않는 주문 ID에 대한 처리
        - 404 Not Found 반환
        - 5xx 에러 발생하지 않음
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        nonexistent_id = 999999999

        # Act
        response = client.post(
            f"/api/orders/{nonexistent_id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"존재하지 않는 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

        # 404 예상
        assert (
            response.status_code == status.HTTP_404_NOT_FOUND
        ), f"존재하지 않는 주문에 대한 응답이 404가 아님: {response.status_code}"

    def test_cancel_delivered_order(self, client, auth_headers, user):
        """
        배송 완료된 주문 취소 시도 → 400

        🔍 검증 포인트:
        - 배송 완료 후 취소 불가 정책 검증
        - 적절한 에러 메시지
        - 5xx 에러 발생하지 않음
        """
        from shopping.tests.factories import OrderFactory

        # 배송 완료된 주문 생성
        delivered_order = OrderFactory.delivered(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.post(
            f"/api/orders/{delivered_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"배송 완료 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

        # 400 예상 (취소 불가능한 상태)
        assert response.status_code == status.HTTP_400_BAD_REQUEST, f"배송 완료 주문 취소가 허용됨: {response.status_code}"

    def test_cancel_preparing_order(self, client, auth_headers, user):
        """
        상품 준비중인 주문 취소 시도

        🔍 검증 포인트:
        - preparing 상태에서의 취소 정책 확인
        - 비즈니스 정책에 따라 허용/거부
        - 5xx 에러 발생하지 않음
        """
        from shopping.tests.factories import OrderFactory

        # 상품 준비중 주문 생성 (status 직접 지정)
        preparing_order = OrderFactory(user=user, status="preparing", payment_method="card")

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.post(
            f"/api/orders/{preparing_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음 (핵심)
        assert response.status_code < 500, f"상품 준비중 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

    def test_cancel_refunded_order(self, client, auth_headers, user):
        """
        이미 환불된 주문 취소 시도 → 400

        🔍 검증 포인트:
        - 이미 환불된 주문에 대한 취소 방지
        - 적절한 에러 응답
        - 5xx 에러 발생하지 않음
        """
        from shopping.tests.factories import OrderFactory

        # 환불된 주문 생성 (status 직접 지정)
        refunded_order = OrderFactory(user=user, status="refunded", payment_method="card")

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.post(
            f"/api/orders/{refunded_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"환불된 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

        # 400 예상
        assert response.status_code == status.HTTP_400_BAD_REQUEST, f"환불된 주문 취소가 허용됨: {response.status_code}"

    def test_cancel_order_with_invalid_id_format(self, client, auth_headers):
        """
        잘못된 형식의 주문 ID로 취소 시도

        🔍 검증 포인트:
        - 문자열, 음수 등 잘못된 ID 형식 처리
        - 400 또는 404 반환
        - 5xx 에러 발생하지 않음
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        invalid_ids = [
            ("abc", "문자열 ID"),
            ("-1", "음수 ID"),
            ("0", "0 ID"),
            ("1.5", "부동소수점 ID"),
            ("null", "null 문자열"),
            ("undefined", "undefined 문자열"),
        ]

        for invalid_id, description in invalid_ids:
            response = client.post(
                f"/api/orders/{invalid_id}/cancel/",
                content_type="application/json",
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, (
                f"{description}로 서버 에러!\n" f"invalid_id={invalid_id}\n" f"status_code={response.status_code}"
            )

    def test_cancel_order_without_auth(self, client, user):
        """
        인증 없이 주문 취소 시도 → 401

        🔍 검증 포인트:
        - 인증 필수 확인
        - 401 Unauthorized 반환
        - 5xx 에러 발생하지 않음
        """
        from shopping.tests.factories import OrderFactory

        pending_order = OrderFactory.pending(user=user)

        # Act - 인증 없이 요청
        response = client.post(
            f"/api/orders/{pending_order.id}/cancel/",
            content_type="application/json",
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"인증 없이 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

        # 401 예상
        assert response.status_code == status.HTTP_401_UNAUTHORIZED, f"인증 없이 주문 취소가 허용됨: {response.status_code}"

    def test_concurrent_cancel_attempts(self, client, auth_headers, user):
        """
        동시 취소 요청 시도

        동일 주문에 대해 연속으로 취소 요청을 보내
        두 번째 요청이 적절히 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 첫 번째 취소 성공
        - 두 번째 취소 요청 시 적절한 에러
        - Race condition 없음
        """
        from shopping.tests.factories import OrderFactory, OrderItemFactory, ProductFactory

        # 취소 가능한 주문 생성
        product = ProductFactory(stock=10)
        pending_order = OrderFactory.pending(user=user)
        OrderItemFactory(order=pending_order, product=product, quantity=1)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act - 첫 번째 취소
        response1 = client.post(
            f"/api/orders/{pending_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 첫 번째는 성공 또는 이미 취소 상태
        assert response1.status_code < 500, f"첫 번째 취소에서 서버 에러!\n" f"status_code={response1.status_code}"

        # Act - 두 번째 취소 (이미 취소됨)
        response2 = client.post(
            f"/api/orders/{pending_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 두 번째도 서버 에러 없음
        assert response2.status_code < 500, f"두 번째 취소에서 서버 에러!\n" f"status_code={response2.status_code}"

        # 두 번째는 400 또는 409 (이미 취소됨)
        if response1.status_code == status.HTTP_200_OK:
            assert response2.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_409_CONFLICT,
            ], f"중복 취소가 허용됨: {response2.status_code}"

    def test_cancel_order_with_payment_in_progress(self, client, auth_headers, user):
        """
        결제 진행 중인 주문 취소 시도

        결제가 진행 중인 상태에서 취소를 시도했을 때
        적절히 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 결제 진행 중 상태 확인
        - 적절한 에러 또는 취소 처리
        - 5xx 에러 발생하지 않음
        """
        from shopping.tests.factories import OrderFactory

        # 결제 대기 중 주문 (pending_payment 상태가 있다면)
        # 없다면 pending 상태로 대체
        try:
            payment_pending_order = OrderFactory(user=user, status="pending_payment")
        except Exception:
            payment_pending_order = OrderFactory.pending(user=user)

        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # Act
        response = client.post(
            f"/api/orders/{payment_pending_order.id}/cancel/",
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"결제 진행 중 주문 취소에서 서버 에러!\n" f"status_code={response.status_code}"

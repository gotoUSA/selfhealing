"""결제 템플릿 뷰 테스트 (FBV: payment_test_page, payment_success, payment_fail)

Note: URL namespace가 설정되어 있지 않으므로 reverse("payment_test") 형식 사용
"""

import pytest
from django.urls import reverse
from rest_framework import status

from shopping.services.payment_service import PaymentConfirmError
from shopping.tests.factories import (
    CompletedPaymentFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    TossResponseBuilder,
    UserFactory,
)
from shopping.utils.toss_payment import TossPaymentError

# ==========================================
# payment_test_page 테스트
# ==========================================


@pytest.mark.django_db
class TestPaymentTestPageNormalCase:
    """정상 케이스 - 결제 테스트 페이지"""

    def test_render_payment_test_page(self, client, user, order):
        """정상 결제 테스트 페이지 렌더링"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_test.html" in [t.name for t in response.templates]

    def test_redirect_urls_point_at_mounted_routes(self, client, user, order):
        """결제창 성공/실패 리다이렉트가 실제로 존재하는 경로(/api/payment/…)를 가리킨다

        2025-11-30 에 앱 마운트가 /shopping/ → /api/ 로 바뀌었는데 이 페이지의 successUrl 은
        /shopping/payment/success/ 로 남아 결제창에서 돌아오면 404 였다.
        """
        client.force_login(user)
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))
        html = response.content.decode()

        assert "/api/payment/success/" in html
        assert "/api/payment/fail/" in html
        assert "/shopping/payment/" not in html
        assert reverse("payment_success") == "/api/payment/success/"

    def test_customer_phone_is_passed_digits_only(self, client, user, order):
        """토스 결제창의 customerMobilePhone 은 숫자만 허용 — 하이픈 있는 배송 연락처를 그대로 넘기면 결제창이 거부한다"""
        client.force_login(user)
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))
        html = response.content.decode()

        assert "-" in order.shipping_phone  # fixture 는 010-9999-8888 꼴
        assert (
            "customerMobilePhone: '" + order.shipping_phone + "'.replace(/\\D/g, '')"
            in html
        )

    def test_admin_can_access_any_order(self, client, user, order):
        """관리자는 모든 주문 접근 가능"""
        # Arrange
        admin_user = UserFactory.admin()
        client.force_login(admin_user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK

    def test_context_data(self, client, user, order, settings):
        """컨텍스트 데이터 확인"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        context = response.context

        assert "order" in context
        assert "client_key" in context
        assert "user" in context
        assert "user_points" in context
        assert "used_points" in context
        assert "final_amount" in context

        assert context["order"].id == order.id
        assert context["user_points"] == user.points
        assert context["used_points"] == order.used_points
        assert context["final_amount"] == order.final_amount


@pytest.mark.django_db
class TestPaymentTestPageBoundary:
    """경계값 - 결제 테스트 페이지"""

    def test_zero_points_order(self, client, user, product):
        """포인트 0원 주문"""
        # Arrange
        order = OrderFactory(user=user, used_points=0)
        OrderItemFactory(order=order, product=product)
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert response.context["used_points"] == 0


@pytest.mark.django_db
class TestPaymentTestPageException:
    """예외 케이스 - 결제 테스트 페이지"""

    def test_redirect_when_already_paid(self, client, user, product):
        """이미 결제된 주문 리다이렉트"""
        # Arrange
        order = OrderFactory(user=user, status="paid")
        OrderItemFactory(order=order, product=product)
        CompletedPaymentFactory(order=order)
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert - 주문 상세 페이지로 리다이렉트
        assert response.status_code == status.HTTP_302_FOUND
        assert f"/orders/{order.id}/" in response.url or "order_detail" in response.url

    def test_other_user_order_404(self, client, user, other_user, product):
        """다른 사용자 주문 접근 시 404"""
        # Arrange
        order = OrderFactory(user=other_user)
        OrderItemFactory(order=order, product=product)
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_nonexistent_order_404(self, client, user):
        """존재하지 않는 주문 404"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": 999999}))

        # Assert
        assert response.status_code == status.HTTP_404_NOT_FOUND

    def test_unauthenticated_redirect(self, client, order):
        """비인증 사용자 로그인 페이지로 리다이렉트"""
        # Act
        response = client.get(reverse("payment_test", kwargs={"order_id": order.id}))

        # Assert
        assert response.status_code == status.HTTP_302_FOUND
        assert "login" in response.url or "accounts/login" in response.url


# ==========================================
# payment_success 콜백 테스트
# ==========================================


@pytest.mark.django_db
class TestPaymentSuccessCallbackNormalCase:
    """정상 케이스 - 결제 성공 콜백"""

    def test_successful_payment_callback(self, client, user, order, mocker):
        """정상 결제 성공 콜백"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        toss_response = TossResponseBuilder.success_response(
            payment_key="test_key_123",
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        mocker.patch(
            "shopping.services.payment_service.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key_123",
                "orderId": str(order.id),
                "amount": str(int(payment.amount)),
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_success.html" in [t.name for t in response.templates]

    def test_renders_success_template_with_context(self, client, user, order, mocker):
        """성공 템플릿 컨텍스트 확인"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        toss_response = TossResponseBuilder.success_response(
            payment_key="test_key_456",
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        mocker.patch(
            "shopping.services.payment_service.TossPaymentClient.confirm_payment",
            return_value=toss_response,
        )

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key_456",
                "orderId": str(order.id),
                "amount": str(int(payment.amount)),
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        context = response.context
        assert "payment" in context
        assert "order" in context
        assert "points_earned" in context


@pytest.mark.django_db
class TestPaymentSuccessCallbackException:
    """예외 케이스 - 결제 성공 콜백"""

    def test_missing_parameters(self, client, user):
        """필수 파라미터 누락"""
        # Arrange
        client.force_login(user)

        # Act - paymentKey 누락
        response = client.get(
            reverse("payment_success"),
            {
                "orderId": "123",
                "amount": "10000",
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]
        assert "필수 파라미터가 누락되었습니다" in response.context.get("message", "")

    def test_missing_order_id(self, client, user):
        """orderId 누락"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "amount": "10000",
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

    def test_missing_amount(self, client, user):
        """amount 누락"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "orderId": "123",
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

    def test_payment_not_found(self, client, user):
        """결제 정보 없음"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "orderId": "99999999",
                "amount": "10000",
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]
        assert "결제 정보를 찾을 수 없습니다" in response.context.get("message", "")

    def test_toss_api_error(self, client, user, order, mocker):
        """Toss API 에러"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        mocker.patch(
            "shopping.services.payment_service.TossPaymentClient.confirm_payment",
            side_effect=TossPaymentError("PROVIDER_ERROR", "결제 승인 실패"),
        )

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "orderId": str(order.id),
                "amount": str(int(payment.amount)),
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]
        assert "오류가 발생했습니다" in response.context.get("message", "")

    def test_payment_confirm_error(self, client, user, order, mocker):
        """PaymentConfirmError 예외"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        mocker.patch(
            "shopping.views.payment_views.PaymentService.confirm_payment_sync",
            side_effect=PaymentConfirmError("이미 완료된 결제입니다."),
        )

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "orderId": str(order.id),
                "amount": str(int(payment.amount)),
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

    def test_generic_exception(self, client, user, order, mocker):
        """기타 예외"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        mocker.patch(
            "shopping.views.payment_views.PaymentService.confirm_payment_sync",
            side_effect=Exception("예상치 못한 오류"),
        )

        # Act
        response = client.get(
            reverse("payment_success"),
            {
                "paymentKey": "test_key",
                "orderId": str(order.id),
                "amount": str(int(payment.amount)),
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]
        assert "오류가 발생했습니다" in response.context.get("message", "")


# ==========================================
# payment_fail 콜백 테스트
# ==========================================


@pytest.mark.django_db
class TestPaymentFailCallbackNormalCase:
    """정상 케이스 - 결제 실패 콜백"""

    def test_fail_callback_with_valid_order_id(self, client, user, order):
        """정상 실패 콜백 처리"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_fail"),
            {
                "code": "USER_CANCEL",
                "message": "사용자가 결제를 취소했습니다",
                "orderId": payment.toss_order_id,
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

        # Assert - Payment 상태 변경 확인
        payment.refresh_from_db()
        assert payment.status == "aborted"

    def test_renders_fail_template_with_context(self, client, user, order):
        """실패 템플릿 컨텍스트 확인"""
        # Arrange
        payment = PaymentFactory(order=order)
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_fail"),
            {
                "code": "TIMEOUT",
                "message": "결제 시간 초과",
                "orderId": payment.toss_order_id,
            },
        )

        # Assert
        assert response.status_code == status.HTTP_200_OK
        context = response.context
        assert context["code"] == "TIMEOUT"
        assert context["message"] == "결제 시간 초과"
        assert context["order_id"] == payment.toss_order_id


@pytest.mark.django_db
class TestPaymentFailCallbackException:
    """예외 케이스 - 결제 실패 콜백"""

    def test_fail_callback_without_order_id(self, client, user):
        """order_id 없이 호출"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_fail"),
            {
                "code": "USER_CANCEL",
                "message": "취소",
            },
        )

        # Assert - orderId 없어도 템플릿은 렌더링
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

    def test_payment_not_found(self, client, user):
        """존재하지 않는 결제"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(
            reverse("payment_fail"),
            {
                "code": "USER_CANCEL",
                "message": "취소",
                "orderId": "nonexistent_order_id",
            },
        )

        # Assert - 에러 없이 템플릿 렌더링 (로그만 기록)
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]

    def test_fail_callback_all_params_none(self, client, user):
        """모든 파라미터 없이 호출"""
        # Arrange
        client.force_login(user)

        # Act
        response = client.get(reverse("payment_fail"))

        # Assert
        assert response.status_code == status.HTTP_200_OK
        assert "shopping/payment_fail.html" in [t.name for t in response.templates]
        assert response.context["code"] is None
        assert response.context["message"] is None
        assert response.context["order_id"] is None

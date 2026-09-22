"""
웹훅 예외 처리 테스트

토스페이먼츠 웹훅의 예외 상황 및 에러 핸들링 검증
- 데이터베이스 레벨 에러
- 비즈니스 로직 에러
- 트랜잭션 롤백 검증
- 예상치 못한 예외 처리
"""

from decimal import Decimal

import pytest
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.models.product import Product


@pytest.mark.django_db
class TestWebhookDatabaseErrorHandling:
    """데이터베이스 레벨 에러 처리"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_payment_found_processes_successfully(self, mock_get_payment, webhook_data_builder):
        """Payment가 존재하면 정상 처리"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK
        assert response.json()["message"] == "Webhook processed"

        # Assert - Payment 상태 확인
        self.payment.refresh_from_db()
        assert self.payment.status == "done"

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_payment_exists_but_already_processed(self, mock_get_payment, webhook_data_builder):
        """Payment는 있지만 이미 처리된 상태"""
        # Arrange
        mock_get_payment()

        self.payment.status = "done"
        self.payment.save()

        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고 중복 차감 안됨
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_payment_not_found_returns_success(self, mock_get_payment, webhook_data_builder, caplog):
        """Payment가 존재하지 않아도 200 OK 반환하고 에러 로그 남김"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            order_id="NONEXISTENT_ORDER_123",
            amount=10000,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 에러 로그 검증 (핵심만)
        assert "Payment not found" in caplog.text

    def test_payment_not_found_missing_order_id(self, mock_get_payment):
        """웹훅 데이터에 orderId가 누락되면 조회 전에 400 — 토스 조회를 호출하지 않는다"""
        # Arrange
        lookup = mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {
                "paymentKey": "test_key",
                # orderId 누락
            },
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "paymentKey and orderId are required"
        lookup.assert_not_called()

    def test_payment_canceled_not_found(self, mock_get_payment, webhook_data_builder, caplog):
        """CANCELED 상태에서 우리 DB에 Payment 가 없으면 200 OK 반환하고 에러 로그 남김"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="CANCELED",
            order_id="NONEXISTENT_ORDER_456",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 에러 로그 검증 (핵심만)
        assert "Payment not found" in caplog.text

    def test_payment_failed_not_found(self, mock_get_payment, webhook_data_builder, caplog):
        """ABORTED 상태에서 우리 DB에 Payment 가 없으면 200 OK 반환하고 에러 로그 남김"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="ABORTED",
            order_id="NONEXISTENT_ORDER_789",
            fail_reason="카드 한도 초과",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 에러 로그 검증 (핵심만)
        assert "Payment not found" in caplog.text


@pytest.mark.django_db
class TestWebhookStockBoundaryHandling:
    """재고 경계값 처리 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_sufficient_stock_deducts_correctly(self, mock_get_payment, webhook_data_builder):
        """웹훅 처리 시 sold_count 증가 확인 (재고 차감은 주문 생성 시 이미 처리됨)"""
        # Arrange
        mock_get_payment()
        initial_stock = self.product.stock
        initial_sold_count = self.product.sold_count

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - sold_count 증가 확인 (재고는 주문 생성 시 이미 차감됨)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock  # 재고는 변경되지 않음
        assert self.product.sold_count == initial_sold_count + 1  # sold_count만 증가

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_zero_stock_sold_count_increases(self, mock_get_payment, webhook_data_builder):
        """재고가 0이어도 sold_count는 정상 증가 (재고 차감은 주문 생성 시 처리됨)"""
        # Arrange
        mock_get_payment()

        self.product.stock = 0
        self.product.save()
        initial_sold_count = self.product.sold_count

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고는 0 유지, sold_count만 증가
        self.product.refresh_from_db()
        assert self.product.stock == 0
        assert self.product.sold_count == initial_sold_count + 1

    def test_insufficient_stock_prevented_by_greatest(self, mock_get_payment, webhook_data_builder):
        """재고 부족 시 Greatest로 음수 방지"""
        # Arrange
        mock_get_payment()

        self.product.stock = 0
        self.product.save()

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고가 음수가 되지 않음
        self.product.refresh_from_db()
        assert self.product.stock == 0

        # Assert - Payment는 정상 처리됨
        self.payment.refresh_from_db()
        assert self.payment.status == "done"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_product_deleted_after_order_created(self, mock_get_payment, webhook_data_builder):
        """Product가 삭제된 OrderItem은 건너뜀"""
        # Arrange
        mock_get_payment()

        order_item = self.order.order_items.first()
        order_item.product = None
        order_item.save()

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 정상 처리됨
        self.payment.refresh_from_db()
        assert self.payment.status == "done"


@pytest.mark.django_db
class TestWebhookTransactionRollback:
    """트랜잭션 롤백 및 원자성 검증"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_transaction_commits_on_success(self, mock_get_payment, webhook_data_builder):
        """모든 작업이 성공하면 전체 커밋"""
        # Arrange
        mock_get_payment()
        initial_stock = self.product.stock
        initial_sold_count = self.product.sold_count
        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 모든 변경사항 커밋됨
        self.payment.refresh_from_db()
        self.product.refresh_from_db()
        self.user.refresh_from_db()

        assert self.payment.status == "done"
        assert self.product.stock == initial_stock  # 재고는 주문 생성 시 이미 차감됨
        assert self.product.sold_count == initial_sold_count + 1  # sold_count만 증가
        # 배송비 제외된 order.total_amount 기준 포인트 적립
        expected_points = initial_points + int(self.order.total_amount * Decimal("0.01"))
        assert self.user.points == expected_points

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_multiple_order_items_all_processed(
        self,
        mock_get_payment,
        webhook_data_builder,
        order_with_multiple_items,
        multiple_products,
    ):
        """여러 상품 모두 처리됨 (sold_count 증가 확인)"""
        # Arrange
        from shopping.models.payment import Payment

        mock_get_payment()

        payment = Payment.objects.create(
            order=order_with_multiple_items,
            amount=order_with_multiple_items.total_amount,
            status="ready",
            toss_order_id=str(order_with_multiple_items.id),
        )

        initial_stocks = {p.id: p.stock for p in multiple_products}
        initial_sold_counts = {p.id: p.sold_count for p in multiple_products}

        webhook_data = webhook_data_builder(
            order_id=str(order_with_multiple_items.id),
            amount=int(payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 모든 상품의 sold_count 증가 (재고는 주문 생성 시 이미 차감됨)
        for product in multiple_products:
            product.refresh_from_db()
            assert product.stock == initial_stocks[product.id]  # 재고는 변경되지 않음
            assert product.sold_count == initial_sold_counts[product.id] + 1  # sold_count만 증가

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_transaction_rollback_on_mark_as_paid_failure(
        self, mocker, mock_get_payment, webhook_data_builder
    ):
        """mark_as_paid 실패 시 전체 롤백"""
        # Arrange
        mock_get_payment()

        mocker.patch(
            "shopping.models.payment.Payment.mark_as_paid",
            side_effect=Exception("Database error"),
        )

        initial_status = self.payment.status
        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 에러 응답
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "error" in response.json()
        assert response.json()["error"] == "Processing failed"

        # Assert - 트랜잭션 롤백으로 상태 변경 안됨
        self.payment.refresh_from_db()
        self.product.refresh_from_db()

        assert self.payment.status == initial_status
        assert self.product.stock == initial_stock

    def test_transaction_rollback_on_order_save_failure(
        self, mocker, mock_get_payment, webhook_data_builder
    ):
        """Order 저장 실패 시 전체 롤백"""
        # Arrange
        mock_get_payment()

        mocker.patch(
            "shopping.models.order.Order.save",
            side_effect=Exception("Order save error"),
        )

        initial_payment_status = self.payment.status
        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 에러 응답
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        # Assert - 롤백으로 Payment 상태 변경 안됨
        self.payment.refresh_from_db()
        self.product.refresh_from_db()

        assert self.payment.status == initial_payment_status
        assert self.product.stock == initial_stock


@pytest.mark.django_db
class TestWebhookUnexpectedExceptions:
    """예상치 못한 예외 처리"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # 1단계: 정상 케이스 (Happy Path)
    # ==========================================

    def test_normal_execution_no_exception(self, mock_get_payment, webhook_data_builder):
        """예외 없이 정상 실행"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

    # ==========================================
    # 2단계: 경계값 케이스 (Boundary)
    # ==========================================

    def test_empty_data_field_in_webhook(self, mock_get_payment):
        """data 필드가 빈 객체인 경우 — paymentKey/orderId 가 없으므로 400"""
        # Arrange
        mock_get_payment()
        webhook_data = {
            "eventType": "PAYMENT_STATUS_CHANGED",
            "data": {},
        }

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 조회할 paymentKey 가 없으니 거부
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["error"] == "paymentKey and orderId are required"

    # ==========================================
    # 3단계: 예외 케이스 (Exception)
    # ==========================================

    def test_generic_exception_during_payment_processing(
        self, mocker, mock_get_payment, webhook_data_builder
    ):
        """handle_payment_done 내부에서 예외 발생"""
        # Arrange
        mock_get_payment()

        # STATUS_HANDLERS 딕셔너리가 모듈 로드 시점에 함수 참조를 저장하므로
        # view 모듈의 STATUS_HANDLERS를 직접 패치해야 함
        mocker.patch.dict(
            "shopping.webhooks.toss_webhook_view.STATUS_HANDLERS",
            {"DONE": mocker.Mock(side_effect=Exception("Unexpected error"))},
        )

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 에러 응답
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert response.json()["error"] == "Processing failed"


@pytest.mark.django_db
class TestWebhookEventSpecificErrors:
    """이벤트별 특수 에러 케이스"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, order, payment, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.order = order
        self.payment = payment
        self.webhook_url = webhook_url

    # ==========================================
    # DONE 관련
    # ==========================================

    def test_done_event_user_none_skips_points(self, mock_get_payment, webhook_data_builder):
        """user가 None인 경우 포인트 적립 스킵"""
        # Arrange
        mock_get_payment()

        self.order.user = None
        self.order.save()

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=int(self.payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 정상 처리됨
        self.payment.refresh_from_db()
        assert self.payment.status == "done"

    def test_done_event_point_calculation_zero(self, mock_get_payment, webhook_data_builder):
        """포인트 계산 결과가 0인 경우"""
        # Arrange
        mock_get_payment()

        # 포인트 계산은 order.total_amount 기준 (배송비 제외된 순수 상품금액)
        self.order.total_amount = Decimal("50")
        self.order.final_amount = Decimal("50")
        self.order.save()

        self.payment.amount = Decimal("50")
        self.payment.save()

        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            order_id=str(self.order.id),
            amount=50,
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 포인트 변경 안됨 (0이므로 스킵)
        self.user.refresh_from_db()
        assert self.user.points == initial_points

    # ==========================================
    # CANCELED 관련
    # ==========================================

    def test_canceled_event_stock_restoration(self, mock_get_payment, webhook_data_builder):
        """재고 복구 정상 처리"""
        # Arrange
        mock_get_payment()

        self.order.status = "paid"
        self.order.save()

        self.payment.status = "done"
        self.payment.save()

        Product.objects.filter(pk=self.product.pk).update(stock=5, sold_count=5)
        self.product.refresh_from_db()

        webhook_data = webhook_data_builder(
            status="CANCELED",
            order_id=str(self.order.id),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 재고 복구됨
        self.product.refresh_from_db()
        assert self.product.stock == 6

    def test_canceled_event_point_deduction_when_user_exists(
        self, mock_get_payment, webhook_data_builder
    ):
        """포인트 회수 정상 처리"""
        # Arrange
        mock_get_payment()

        # 적립 포인트 설정 (배송비 제외된 total_amount 기준 1%)
        earned_points = int(self.order.total_amount * Decimal("0.01"))
        self.order.status = "paid"
        self.order.earned_points = earned_points
        self.order.save()

        self.payment.status = "done"
        self.payment.save()

        # 유저 포인트에 적립분 추가 (실제 시나리오 시뮬레이션)
        self.user.points += earned_points
        self.user.save()
        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            status="CANCELED",
            order_id=str(self.order.id),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 포인트 회수됨 (order.earned_points 기준)
        self.user.refresh_from_db()
        assert self.user.points == initial_points - earned_points

    def test_canceled_event_user_none_skips_point_deduction(
        self, mock_get_payment, webhook_data_builder
    ):
        """user가 None이면 포인트 회수 스킵"""
        # Arrange
        mock_get_payment()

        self.order.status = "paid"
        self.order.user = None
        self.order.save()

        self.payment.status = "done"
        self.payment.save()

        webhook_data = webhook_data_builder(
            status="CANCELED",
            order_id=str(self.order.id),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 취소 처리됨
        self.payment.refresh_from_db()
        assert self.payment.status == "canceled"

    # ==========================================
    # ABORTED 관련
    # ==========================================

    def test_failed_event_missing_reason_field(self, mock_get_payment, webhook_data_builder):
        """failure 객체가 누락된 ABORTED 는 빈 문자열 사유로 처리"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="ABORTED",
            order_id=str(self.order.id),
            failure=None,  # failure 객체 제거
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 실패 처리됨
        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == ""

    def test_failed_event_with_reason(self, mock_get_payment, webhook_data_builder):
        """실패 사유가 있는 경우 정상 저장"""
        # Arrange
        mock_get_payment()
        webhook_data = webhook_data_builder(
            status="ABORTED",
            order_id=str(self.order.id),
            fail_reason="카드 한도 초과",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - 실패 사유 저장됨
        self.payment.refresh_from_db()
        assert self.payment.status == "aborted"
        assert self.payment.fail_reason == "카드 한도 초과"

        # Assert - PaymentLog 생성됨
        log = PaymentLog.objects.filter(payment=self.payment, log_type="webhook").first()
        assert log is not None
        assert "카드 한도 초과" in log.message

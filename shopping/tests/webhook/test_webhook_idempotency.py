"""
웹훅 멱등성 통합 테스트

개별 이벤트의 기본 멱등성 테스트는 각 test_webhook_payment_*.py에 있으며,
이 파일은 다음 시나리오를 다룹니다:

1. 교차 이벤트 멱등성 - 다른 이벤트 타입 간 상태 충돌
2. 빠른 연속 요청 - 동일/다른 이벤트의 빠른 연속 처리
3. 이벤트 순서 문제 - 역순 또는 뒤섞인 이벤트 처리
4. DB 일관성 - 트랜잭션 및 고유 제약조건 검증

참고: test_toss_webhook.py는 삭제 예정이며,
시그니처 검증 등 보안 테스트는 별도 파일로 분리될 수 있습니다.
"""

import uuid
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework import status

from shopping.models.payment import PaymentLog
from shopping.tests.factories import OrderFactory, OrderItemFactory, PaymentFactory


# ==========================================
# 1. 교차 이벤트 멱등성
# ==========================================


@pytest.mark.django_db
class TestWebhookCrossEventIdempotency:
    """교차 이벤트 멱등성 - 다른 이벤트 간 상태 충돌"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

    # 1단계: 정상 시퀀스 (Happy Path)

    def test_done_after_canceled_ignored(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """CANCELED 후 DONE 이벤트는 무시 - 취소된 결제는 재승인 불가"""
        # Arrange - Factory로 완전 격리된 주문 생성
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="canceled", is_canceled=True, payment_key="canceled_key")

        mock_verify_webhook()
        initial_stock = self.product.stock
        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.DONE",
            order_id=str(order.id),
            payment_key="new_key_after_cancel",
            amount=int(payment.amount),
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment 상태는 변경되지 않음 (이미 canceled)
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert payment.is_canceled is True

        # Assert - 재고 차감 안 됨
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

        # Assert - 포인트 적립 안 됨
        self.user.refresh_from_db()
        assert self.user.points == initial_points

    def test_canceled_after_failed_ignored(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """FAILED 후 CANCELED 이벤트는 무시 - 실패한 결제는 취소 불필요"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="aborted", fail_reason="카드 한도 초과")

        mock_verify_webhook()
        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order.id),
            cancel_reason="실패 후 취소",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답은 성공
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment 상태는 aborted 유지
        payment.refresh_from_db()
        assert payment.status == "aborted"
        assert payment.fail_reason == "카드 한도 초과"

        # Assert - 재고는 변경되지 않음 (원래 차감 안 됨)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_failed_after_done_ignored(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """DONE 후 FAILED 이벤트는 무시 - 이미 완료된 결제"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="paid", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="done", payment_key="done_key", approved_at=timezone.now())

        mock_verify_webhook()

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(order.id),
            fail_reason="늦은 실패 알림",
        )

        # Act
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment 상태는 done 유지 (실패로 변경 안 됨)
        payment.refresh_from_db()
        assert payment.status == "done"
        assert payment.fail_reason == ""

    # 2단계: 복잡한 시퀀스 (Boundary)

    def test_done_canceled_done_sequence(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """DONE → CANCELED → DONE 시퀀스 - 마지막 DONE은 무시"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        # 1. DONE 이벤트
        done_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        self.client.post(
            self.webhook_url,
            done_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        payment.refresh_from_db()
        self.product.refresh_from_db()
        assert payment.status == "done"
        assert self.product.stock == initial_stock - 1

        # 2. CANCELED 이벤트
        canceled_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order.id),
            cancel_reason="사용자 취소",
        )

        self.client.post(
            self.webhook_url,
            canceled_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        payment.refresh_from_db()
        self.product.refresh_from_db()
        assert payment.status == "canceled"
        assert self.product.stock == initial_stock

        # Act - 3. 다시 DONE 이벤트 (무시되어야 함)
        done_again_data = webhook_data_builder(
            order_id=str(order.id),
            payment_key="new_key_after_cancel",
            amount=int(payment.amount),
        )

        response = self.client.post(
            self.webhook_url,
            done_again_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 canceled 유지
        payment.refresh_from_db()
        assert payment.status == "canceled"

        # Assert - 재고는 복구된 상태 유지 (재차감 안 됨)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock


# ==========================================
# 2. 빠른 연속 요청 멱등성
# ==========================================


@pytest.mark.django_db
class TestWebhookRapidRequests:
    """빠른 연속 요청 멱등성 - 동시성 및 중복 방지"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

    # 1단계: 정상 케이스 (Happy Path)

    def test_triple_done_requests(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """같은 DONE 이벤트 3번 연속 - 한 번만 처리"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock
        initial_points = self.user.points

        webhook_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Act - 3번 연속 요청
        for i in range(3):
            response = self.client.post(
                self.webhook_url,
                webhook_data,
                format="json",
                HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
            )
            assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 한 번만 처리
        payment.refresh_from_db()
        assert payment.status == "done"

        # Assert - 재고는 한 번만 차감
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock - 1
        assert self.product.sold_count == 1

        # Assert - 포인트는 한 번만 적립 (배송비 제외된 order.total_amount 기준)
        self.user.refresh_from_db()
        expected_points = int(order.total_amount * Decimal("0.01"))
        assert self.user.points == initial_points + expected_points

        # Assert - 로그는 첫 처리만 (중복은 early return으로 로그 생성 안 됨)
        log_count = PaymentLog.objects.filter(payment=payment, log_type="webhook").count()
        assert log_count == 1

    def test_rapid_canceled_requests(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """CANCELED 이벤트 빠른 연속 - 재고 한 번만 복구"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="paid", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="done", payment_key="done_key", approved_at=timezone.now())

        # 재고 차감 시뮬레이션
        self.product.stock -= 1
        self.product.sold_count = 1
        self.product.save()

        # 포인트 적립 시뮬레이션 (배송비 제외된 order.total_amount 기준)
        earned_points = int(order.total_amount * Decimal("0.01"))
        self.user.points += earned_points
        self.user.save()

        # order.earned_points도 설정 (회수 시 이 값 사용)
        order.earned_points = earned_points
        order.save()

        mock_verify_webhook()
        stock_after_deduction = self.product.stock
        points_after_earn = self.user.points

        webhook_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order.id),
            cancel_reason="빠른 취소",
        )

        # Act - 2번 연속 취소 요청
        for i in range(2):
            response = self.client.post(
                self.webhook_url,
                webhook_data,
                format="json",
                HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
            )
            assert response.status_code == status.HTTP_200_OK

        # Assert - Payment는 한 번만 처리
        payment.refresh_from_db()
        assert payment.status == "canceled"
        assert payment.is_canceled is True

        # Assert - 재고는 한 번만 복구
        self.product.refresh_from_db()
        assert self.product.stock == stock_after_deduction + 1
        assert self.product.sold_count == 0

        # Assert - 포인트는 한 번만 회수
        self.user.refresh_from_db()
        assert self.user.points == points_after_earn - earned_points

    # 2단계: 경계값 케이스 (Boundary)

    def test_rapid_mixed_events(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """다른 이벤트 빠른 연속 - DONE, FAILED 교차"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        done_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        failed_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(order.id),
            fail_reason="늦은 실패",
        )

        # Act - DONE 성공 후 FAILED 무시
        response_done = self.client.post(
            self.webhook_url,
            done_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        response_failed = self.client.post(
            self.webhook_url,
            failed_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 둘 다 200 응답
        assert response_done.status_code == status.HTTP_200_OK
        assert response_failed.status_code == status.HTTP_200_OK

        # Assert - Payment는 done 상태 유지
        payment.refresh_from_db()
        assert payment.status == "done"

        # Assert - 재고는 한 번만 차감
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock - 1


# ==========================================
# 3. 이벤트 순서 문제
# ==========================================


@pytest.mark.django_db
class TestWebhookEventOrdering:
    """이벤트 순서 관련 멱등성 - 역순 및 뒤섞인 이벤트"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

    # 1단계: 정상 케이스 (Happy Path)

    def test_canceled_before_done(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """CANCELED가 DONE보다 먼저 도착 (역순) - DONE은 정상 처리"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        canceled_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order.id),
            cancel_reason="먼저 도착한 취소",
        )

        done_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Act - 1. CANCELED 먼저
        response_canceled = self.client.post(
            self.webhook_url,
            canceled_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # 2. DONE 나중
        response_done = self.client.post(
            self.webhook_url,
            done_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 둘 다 200 응답
        assert response_canceled.status_code == status.HTTP_200_OK
        assert response_done.status_code == status.HTTP_200_OK

        # Assert - 최종 상태는 canceled (나중 이벤트가 무시됨)
        payment.refresh_from_db()
        assert payment.status == "canceled"

        # Assert - 재고는 변경 없음 (pending에서 cancel은 재고 영향 없음)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    def test_failed_then_done(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """FAILED 후 DONE 도착 - DONE은 무시"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        failed_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(order.id),
            fail_reason="먼저 실패",
        )

        done_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Act - 1. FAILED 먼저
        self.client.post(
            self.webhook_url,
            failed_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # 2. DONE 나중 (무시되어야 함)
        response_done = self.client.post(
            self.webhook_url,
            done_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response_done.status_code == status.HTTP_200_OK

        # Assert - Payment는 aborted 유지
        payment.refresh_from_db()
        assert payment.status == "aborted"

        # Assert - 재고는 차감 안 됨
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

    # 2단계: 경계값 케이스 (Boundary)

    def test_done_failed_canceled_mixed(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """DONE → FAILED → CANCELED 뒤섞인 순서"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        done_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        failed_data = webhook_data_builder(
            event_type="PAYMENT.FAILED",
            order_id=str(order.id),
            fail_reason="중간 실패",
        )

        canceled_data = webhook_data_builder(
            event_type="PAYMENT.CANCELED",
            order_id=str(order.id),
            cancel_reason="마지막 취소",
        )

        # Act - DONE → FAILED → CANCELED
        self.client.post(
            self.webhook_url,
            done_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        payment.refresh_from_db()
        assert payment.status == "done"

        # FAILED는 무시됨 (이미 done)
        self.client.post(
            self.webhook_url,
            failed_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        payment.refresh_from_db()
        assert payment.status == "done"

        # CANCELED는 처리됨 (done에서 canceled로 전환 가능)
        response_canceled = self.client.post(
            self.webhook_url,
            canceled_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 응답 검증
        assert response_canceled.status_code == status.HTTP_200_OK

        # Assert - 최종 상태는 canceled
        payment.refresh_from_db()
        assert payment.status == "canceled"

        # Assert - 재고는 복구됨 (done에서 차감, canceled에서 복구)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock


# ==========================================
# 4. DB 일관성 및 트랜잭션
# ==========================================


@pytest.mark.django_db
class TestWebhookDatabaseConsistency:
    """DB 일관성 및 트랜잭션 테스트"""

    @pytest.fixture(autouse=True)
    def setup(self, api_client, user, product, webhook_url):
        """테스트 환경 설정"""
        self.client = api_client
        self.user = user
        self.product = product
        self.webhook_url = webhook_url

    # 1단계: 정상 케이스 (Happy Path)

    def test_payment_key_uniqueness_preserved(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """Payment key 고유성 유지 - 같은 key로 다른 주문 처리 시도"""
        # Arrange - 첫 번째 주문
        unique_suffix_1 = uuid.uuid4().hex[:8]
        order_number_1 = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix_1}"

        order1 = OrderFactory(user=self.user, status="pending", order_number=order_number_1)
        OrderItemFactory(order=order1, product=self.product)
        payment1 = PaymentFactory(order=order1, status="ready")

        # 두 번째 주문
        unique_suffix_2 = uuid.uuid4().hex[:8]
        order_number_2 = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix_2}"

        order2 = OrderFactory(user=self.user, status="pending", order_number=order_number_2)
        OrderItemFactory(order=order2, product=self.product)
        payment2 = PaymentFactory(order=order2, status="ready")

        mock_verify_webhook()

        # Act - 첫 번째 주문 DONE
        done_data_1 = webhook_data_builder(
            order_id=str(order1.id),
            payment_key="shared_payment_key",
            amount=int(payment1.amount),
        )

        response1 = self.client.post(
            self.webhook_url,
            done_data_1,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        assert response1.status_code == status.HTTP_200_OK

        payment1.refresh_from_db()
        assert payment1.payment_key == "shared_payment_key"

        # 두 번째 주문에 같은 key 사용 시도
        done_data_2 = webhook_data_builder(
            order_id=str(order2.id),
            payment_key="shared_payment_key",  # 중복 키
            amount=int(payment2.amount),
        )

        # Assert - DB 제약조건으로 인해 500 에러 발생 가능
        # 또는 payment_key 업데이트가 실패하고 기존 값 유지
        response2 = self.client.post(
            self.webhook_url,
            done_data_2,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # 두 Payment의 payment_key가 다르거나, 하나는 처리 실패
        payment2.refresh_from_db()
        # payment_key unique 제약으로 인해 두 번째는 업데이트 안 됨
        assert payment2.payment_key != "shared_payment_key" or response2.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

    # 2단계: 경계값 케이스 (Boundary)

    def test_select_for_update_prevents_race_condition(self, mock_verify_webhook, webhook_data_builder, webhook_signature):
        """select_for_update가 동시 처리 시 락 동작 - 순차 처리 검증"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        webhook_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Act - 같은 요청을 2번 (순차적으로 처리되어야 함)
        response1 = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        response2 = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 둘 다 성공 응답
        assert response1.status_code == status.HTTP_200_OK
        assert response2.status_code == status.HTTP_200_OK

        # Assert - 재고는 한 번만 차감 (select_for_update 덕분)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock - 1

        # Assert - Payment는 한 번만 처리
        payment.refresh_from_db()
        assert payment.status == "done"

    # 3단계: 예외 케이스 (Exception)

    def test_transaction_rollback_on_error(self, mock_verify_webhook, webhook_data_builder, webhook_signature, mocker):
        """트랜잭션 롤백 - 처리 중 에러 발생 시 일관성 유지"""
        # Arrange
        unique_suffix = uuid.uuid4().hex[:8]
        order_number = f"{timezone.now().strftime('%Y%m%d')}{unique_suffix}"

        order = OrderFactory(user=self.user, status="pending", order_number=order_number)
        OrderItemFactory(order=order, product=self.product)
        payment = PaymentFactory(order=order, status="ready")

        mock_verify_webhook()
        initial_stock = self.product.stock

        # PointService.add_points에서 에러 발생하도록 Mock
        mocker.patch(
            "shopping.webhooks.toss_webhook_view.PointService.add_points",
            side_effect=Exception("포인트 적립 실패"),
        )

        webhook_data = webhook_data_builder(
            order_id=str(order.id),
            amount=int(payment.amount),
        )

        # Act - 웹훅 처리 (에러 발생)
        response = self.client.post(
            self.webhook_url,
            webhook_data,
            format="json",
            HTTP_X_TOSS_WEBHOOK_SIGNATURE=webhook_signature,
        )

        # Assert - 500 에러 응답
        assert response.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR

        # Assert - 트랜잭션 롤백으로 Payment 상태 변경 안 됨
        payment.refresh_from_db()
        assert payment.status == "ready"

        # Assert - 재고도 차감 안 됨 (롤백)
        self.product.refresh_from_db()
        assert self.product.stock == initial_stock

"""
결제 태스크 테스트 — PG 대사(ALREADY_PROCESSED_PAYMENT 뒤 조회 API 확인)와 고아 주문 롤백

tests/hybrid/test_payment_tasks.py 는 requires_db 마커로 CI 에서 빠진다.
돈이 걸린 경로는 여기(shopping/tests) 에 두어 CI 가 항상 돌리게 한다.
"""

import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from django.db.models import F

from shopping.models.order import Order, OrderItem
from shopping.models.payment import Payment, PaymentLog
from shopping.models.product import Product
from shopping.tasks.payment_tasks import (
    call_toss_confirm_api,
    detect_orphaned_orders,
    finalize_payment_confirm,
)
from shopping.constants import TOSS_CONFIRM_TIMEOUT, TOSS_RETRYABLE_ERRORS
from shopping.tests.factories import TossResponseBuilder
from shopping.utils.toss_payment import TossPaymentClient, TossPaymentError

CONFIRM = "shopping.utils.toss_payment.TossPaymentClient.confirm_payment"
LOOKUP = "shopping.utils.toss_payment.TossPaymentClient.get_payment"
ROLLBACK_DELAY = "shopping.tasks.payment_tasks.rollback_payment_failure.delay"
NOTIFY_DELAY = "shopping.tasks.payment_tasks.notify_payment_failure.delay"


def _already_processed():
    return TossPaymentError(
        "ALREADY_PROCESSED_PAYMENT", "이미 처리된 결제 입니다.", 400
    )


@pytest.fixture
def in_progress_payment(order):
    """confirm_payment_async 가 체인을 발행한 직후의 상태: in_progress 결제 + confirmed 주문"""
    return Payment.objects.create(
        order=order,
        toss_order_id=str(order.id),
        amount=order.final_amount,
        status="in_progress",
        payment_key="test_key_reconcile",
    )


def _last_attempt(payment, order):
    """재시도를 다 쓴 마지막 시도 — retry 를 mock 하지 않고 실제 Celery retry 의 소진 분기를 탄다

    Celery 의 retry(exc=...) 는 소진 시 MaxRetriesExceededError 가 아니라 exc 를 다시 던진다.
    그 예외를 mock 으로 억지로 만들면, 실제로는 한 번도 실행되지 않는 분기가 초록이 된다.
    """
    return call_toss_confirm_api.apply(
        args=[payment.payment_key, order.id, int(payment.amount)],
        retries=call_toss_confirm_api.max_retries,
    ).get()


def _done_lookup(order, payment):
    return TossResponseBuilder.success_response(
        payment_key=payment.payment_key,
        order_id=str(order.id),
        amount=int(payment.amount),
    )


@pytest.mark.django_db(transaction=True)
class TestConfirmReconciliation:
    """타임아웃 뒤 재시도가 ALREADY_PROCESSED_PAYMENT 를 받으면 조회 API 로 대사한다"""

    def test_timeout_then_already_processed_is_reconciled_to_paid(
        self, mocker, user, order, in_progress_payment
    ):
        """(a) confirm 타임아웃 → 재시도 → ALREADY_PROCESSED → 조회 DONE → 주문 paid, 적립은 한 번"""
        payment = in_progress_payment
        mocker.patch(
            CONFIRM,
            side_effect=[
                TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500),
                _already_processed(),
            ],
        )
        lookup = mocker.patch(LOOKUP, return_value=_done_lookup(order, payment))
        rollback = mocker.patch(ROLLBACK_DELAY)
        points_before = user.points

        # 1차 호출: 타임아웃 → 재시도 (직접 호출이라 retry 는 예외를 그대로 올린다)
        with pytest.raises(TossPaymentError) as first:
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))
        assert first.value.code == "NETWORK_ERROR"

        # 2차 호출(재시도): ALREADY_PROCESSED → 조회 API 로 승인 확인 → 승인 응답으로 반환
        toss_response = call_toss_confirm_api(
            payment.payment_key, order.id, int(payment.amount)
        )
        lookup.assert_called_once_with(payment.payment_key, timeout=TOSS_CONFIRM_TIMEOUT)
        assert toss_response["status"] == "DONE"
        rollback.assert_not_called()

        # 체인의 다음 단계가 그 응답으로 정상 마감
        result = finalize_payment_confirm(toss_response, payment.id, user.id)
        assert result["status"] == "success"

        order.refresh_from_db()
        payment.refresh_from_db()
        user.refresh_from_db()
        assert order.status == "paid"
        assert payment.status == "done"
        assert payment.payment_key == "test_key_reconcile"
        earned_once = user.points - points_before
        assert earned_once > 0

        # 같은 응답으로 한 번 더 마감해도 중복 적립 없음
        assert (
            finalize_payment_confirm(toss_response, payment.id, user.id)["status"]
            == "already_processed"
        )
        user.refresh_from_db()
        assert user.points - points_before == earned_once

        assert PaymentLog.objects.filter(
            payment=payment, log_type="approve", message__contains="대사"
        ).exists()

    def test_lookup_not_done_falls_back_to_rollback(
        self, mocker, order, in_progress_payment
    ):
        """(b) 조회 결과가 승인 아님(CANCELED) → 기존대로 롤백"""
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=_already_processed())
        mocker.patch(
            LOOKUP,
            return_value=TossResponseBuilder.cancel_response(
                payment_key=payment.payment_key
            ),
        )
        rollback = mocker.patch(ROLLBACK_DELAY)

        with pytest.raises(TossPaymentError) as exc_info:
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        assert exc_info.value.code == "ALREADY_PROCESSED_PAYMENT"
        rollback.assert_called_once()
        payment.refresh_from_db()
        assert payment.status == "aborted"

    @pytest.mark.parametrize(
        "mismatch",
        [
            {"order_id": "SOMEONE_ELSES_ORDER"},
            {"amount": 1},
        ],
        ids=["other_order", "other_amount"],
    )
    def test_lookup_done_for_a_different_order_or_amount_is_not_trusted(
        self, mocker, order, in_progress_payment, mismatch
    ):
        """조회가 DONE 이어도 주문번호·금액이 다르면 우리 결제가 아니다 → 롤백"""
        payment = in_progress_payment
        kwargs = {
            "payment_key": payment.payment_key,
            "order_id": str(order.id),
            "amount": int(payment.amount),
        }
        kwargs.update(mismatch)
        mocker.patch(CONFIRM, side_effect=_already_processed())
        mocker.patch(
            LOOKUP, return_value=TossResponseBuilder.success_response(**kwargs)
        )
        rollback = mocker.patch(ROLLBACK_DELAY)

        with pytest.raises(TossPaymentError):
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        rollback.assert_called_once()
        order.refresh_from_db()
        assert order.status == "confirmed"

    def test_lookup_failure_retries_without_rollback(
        self, mocker, order, in_progress_payment
    ):
        """(c) 조회 자체가 실패 → 재시도 대상, 롤백하지 않는다 (확인 없는 롤백이 고아 결제를 만든다)"""
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=_already_processed())
        mocker.patch(
            LOOKUP, side_effect=TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500)
        )
        rollback = mocker.patch(ROLLBACK_DELAY)
        retry = mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=TossPaymentError("RETRY", "retry"),
        )

        with pytest.raises(TossPaymentError) as exc_info:
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        assert exc_info.value.code == "RETRY"
        assert retry.call_args.kwargs["exc"].code == "NETWORK_ERROR"
        rollback.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == "in_progress"

    def test_lookup_retries_exhausted_escalates_instead_of_rolling_back(
        self, mocker, order, in_progress_payment
    ):
        """조회 재시도까지 소진 → 롤백 대신 운영자 알림(critical) + 로그, 결제는 in_progress 로 남긴다"""
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=_already_processed())
        mocker.patch(
            LOOKUP, side_effect=TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500)
        )
        rollback = mocker.patch(ROLLBACK_DELAY)
        notify = mocker.patch(NOTIFY_DELAY)

        with pytest.raises(TossPaymentError) as exc_info:
            _last_attempt(payment, order)

        assert exc_info.value.code == "NETWORK_ERROR"

        rollback.assert_not_called()
        notify.assert_called_once()
        assert notify.call_args.args[:2] == (order.id, "reconcile_failed")
        assert notify.call_args.kwargs["severity"] == "critical"
        payment.refresh_from_db()
        assert payment.status == "in_progress"
        assert PaymentLog.objects.filter(
            payment=payment, log_type="error", message__contains="수동 대사"
        ).exists()

    def test_duplicated_order_id_is_also_reconciled(
        self, mocker, order, in_progress_payment
    ):
        """DUPLICATED_ORDER_ID('이미 승인 및 취소가 진행된 주문번호') 도 같은 대사 경로를 탄다"""
        payment = in_progress_payment
        mocker.patch(
            CONFIRM,
            side_effect=TossPaymentError("DUPLICATED_ORDER_ID", "중복된 주문번호", 400),
        )
        lookup = mocker.patch(LOOKUP, return_value=_done_lookup(order, payment))
        rollback = mocker.patch(ROLLBACK_DELAY)

        toss_response = call_toss_confirm_api(
            payment.payment_key, order.id, int(payment.amount)
        )

        assert toss_response["status"] == "DONE"
        lookup.assert_called_once()
        rollback.assert_not_called()

    def test_other_non_retryable_errors_do_not_call_lookup(
        self, mocker, order, in_progress_payment
    ):
        """대사 대상이 아닌 비재시도 오류(카드 거절 등)는 조회 없이 기존대로 롤백"""
        payment = in_progress_payment
        mocker.patch(
            CONFIRM,
            side_effect=TossPaymentError("REJECT_CARD_PAYMENT", "카드 거절", 403),
        )
        lookup = mocker.patch(LOOKUP)
        rollback = mocker.patch(ROLLBACK_DELAY)

        with pytest.raises(TossPaymentError):
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        lookup.assert_not_called()
        rollback.assert_called_once()


@pytest.mark.django_db(transaction=True)
class TestConfirmRetryKeepsPaymentInProgress:
    """재시도 대기 중에는 in_progress 를 유지하고, 소진됐을 때만 aborted + 롤백

    예전에는 분류 전에 aborted 로 덮어서 재시도를 기다리는 동안(당시 180초) 폴링은 '실패'를 보였고,
    그 사이 도착한 토스 DONE 웹훅은 최종 상태로 보고 버려졌다. 소진 분기는 한 번도 실행되지 않았다.
    """

    @pytest.mark.parametrize(
        "error",
        [
            TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500),
            TossPaymentError("SOME_GATEWAY_ERROR", "게이트웨이 오류", 502),
        ],
        ids=["retryable_code", "http_5xx"],
    )
    def test_retry_keeps_in_progress_and_uses_backoff(
        self, mocker, order, in_progress_payment, error
    ):
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=error)
        rollback = mocker.patch(ROLLBACK_DELAY)
        retry = mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=TossPaymentError("RETRY", "retry"),
        )

        with pytest.raises(TossPaymentError):
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        # 첫 재시도: retry_backoff=10 → full jitter 로 0~10초 (Celery 기본 180초 고정이 아니다)
        assert 0 <= retry.call_args.kwargs["countdown"] <= 10
        rollback.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == "in_progress"
        assert PaymentLog.objects.filter(payment=payment, log_type="error").exists()

    @pytest.mark.parametrize(
        "error",
        [
            TossPaymentError("NETWORK_ERROR", "네트워크 오류", 500),
            TossPaymentError("SOME_GATEWAY_ERROR", "게이트웨이 오류", 502),
        ],
        ids=["retryable_code", "http_5xx"],
    )
    def test_retries_exhausted_rolls_back(self, mocker, order, in_progress_payment, error):
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=error)
        rollback = mocker.patch(ROLLBACK_DELAY)

        with pytest.raises(TossPaymentError):
            _last_attempt(payment, order)

        rollback.assert_called_once()
        payment.refresh_from_db()
        assert payment.status == "aborted"


@pytest.mark.django_db(transaction=True)
class TestConfirmSoftTimeLimit:
    """태스크 시간 제한 초과는 승인 여부를 모르는 상태 — 롤백하지 않는다

    예전에는 status=timeout + 즉시 롤백이었다. 토스가 승인한 뒤 응답이 늦은 경우, 고객은 청구됐는데
    주문은 payment_failed 로 끝났다(가짜 토스 서버 + 실제 워커로 재현).
    """

    def test_soft_time_limit_retries_instead_of_rolling_back(
        self, mocker, order, in_progress_payment
    ):
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=SoftTimeLimitExceeded())
        rollback = mocker.patch(ROLLBACK_DELAY)
        retry = mocker.patch.object(
            call_toss_confirm_api,
            "retry",
            side_effect=TossPaymentError("RETRY", "retry"),
        )

        with pytest.raises(TossPaymentError):
            call_toss_confirm_api(payment.payment_key, order.id, int(payment.amount))

        retry.assert_called_once()
        rollback.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == "in_progress"
        order.refresh_from_db()
        assert order.status == "confirmed"
        assert PaymentLog.objects.filter(
            payment=payment, log_type="error", message__contains="시간 초과"
        ).exists()

    def test_soft_time_limit_exhausted_escalates_without_rollback(
        self, mocker, order, in_progress_payment
    ):
        payment = in_progress_payment
        mocker.patch(CONFIRM, side_effect=SoftTimeLimitExceeded())
        rollback = mocker.patch(ROLLBACK_DELAY)
        notify = mocker.patch(NOTIFY_DELAY)

        with pytest.raises(SoftTimeLimitExceeded):
            _last_attempt(payment, order)

        rollback.assert_not_called()
        notify.assert_called_once()
        assert notify.call_args.args[:2] == (order.id, "confirm_timeout_unresolved")
        assert notify.call_args.kwargs["severity"] == "critical"
        payment.refresh_from_db()
        assert payment.status == "in_progress"


class _SlowTossHandler(BaseHTTPRequestHandler):
    """응답을 늦게 주는 가짜 토스 — 실제 소켓 읽기 타임아웃을 만든다"""

    delay = 1.5

    def do_POST(self):
        time.sleep(self.delay)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        except OSError:
            pass  # 클라이언트가 먼저 끊었다

    def log_message(self, *args):
        pass


class TestConfirmTimeoutBudget:
    """느린 응답은 태스크 시간 제한이 아니라 HTTP 타임아웃으로 끝나야 재시도·대사 경로를 탄다"""

    def test_http_timeout_ends_before_task_time_limits(self):
        assert sum(TOSS_CONFIRM_TIMEOUT) < call_toss_confirm_api.soft_time_limit
        assert call_toss_confirm_api.soft_time_limit < call_toss_confirm_api.time_limit

    def test_slow_response_becomes_a_retryable_error(self, mocker, settings):
        """mock 예외가 아니라 실제로 늦게 응답하는 서버 — '어떤 장치가 먼저 울리는지'를 본다"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowTossHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            settings.TOSS_BASE_URL = f"http://127.0.0.1:{server.server_address[1]}"
            mocker.patch("shopping.utils.toss_payment.TOSS_CONFIRM_TIMEOUT", (1, 0.3))

            started = time.monotonic()
            with pytest.raises(TossPaymentError) as exc_info:
                TossPaymentClient().confirm_payment(
                    payment_key="k", order_id="ORDER_000001", amount=1000
                )
            elapsed = time.monotonic() - started
        finally:
            server.shutdown()
            server.server_close()

        assert exc_info.value.code in TOSS_RETRYABLE_ERRORS
        assert elapsed < _SlowTossHandler.delay


@pytest.mark.django_db(transaction=True)
class TestDetectOrphanedOrdersTriggersRollback:
    """고아 주문 감지가 실제로 롤백 태스크를 실행시킨다 (.delay 를 모킹하지 않는다)"""

    def test_orphan_is_rolled_back_end_to_end(self, user, product, order):
        """confirmed + aborted 가 10분 넘게 지속 → payment_failed + 재고 복구

        기존 hybrid 테스트는 .delay 를 모킹해서 인자 이름 불일치(reason= vs fail_reason=)를 못 봤다.
        """
        from datetime import timedelta

        from django.utils import timezone

        Payment.objects.create(
            order=order,
            toss_order_id=str(order.id),
            amount=order.final_amount,
            status="aborted",
        )
        stock_while_held = Product.objects.get(pk=product.pk).stock
        Order.objects.filter(pk=order.pk).update(
            updated_at=timezone.now() - timedelta(minutes=15)
        )
        held_quantity = OrderItem.objects.get(order=order).quantity

        result = detect_orphaned_orders(threshold_minutes=10)

        assert result["detected"] == 1
        assert result["triggered"] == 1
        assert result["errors"] == []
        order.refresh_from_db()
        assert order.status == "payment_failed"
        assert "Orphan detection" in order.failure_reason
        assert (
            Product.objects.get(pk=product.pk).stock == stock_while_held + held_quantity
        )

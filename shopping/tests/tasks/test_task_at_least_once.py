"""
Celery 태스크 — 최소 한 번(at-least-once) 배달에서도 한 번만 일어나야 하는 것들과 재시도 정책

acks_late=True 인 태스크는 워커가 처리 중 죽으면 같은 메시지를 다시 받고, self.retry 는 처음부터 다시
실행한다. 그래서 "두 번 실행돼도 결과는 한 번"을 태스크마다 직접 두 번 실행해 확인한다.
(2026-09-24 실측: 적립 태스크는 재배달 한 번에 두 번 적립, 인증 메일 가드는 "send" 오타로 무력,
재발송 스윕은 재시도 대기 중인 메일을 또 보내 복구 시 3통, 수동 retry 는 설정과 달리 180초 고정)
"""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from smtplib import SMTPException

from django.conf import settings as django_settings
from django.db import IntegrityError, transaction
from django.utils import timezone

import pytest
import yaml

from shopping.models.email_verification import EmailLog
from shopping.models.payment import Payment, PaymentLog
from shopping.models.point import PointHistory
from shopping.tasks.email_tasks import retry_failed_emails_task, send_email_task, send_verification_email_task
from shopping.tasks.payment_tasks import finalize_payment_confirm, rollback_payment_failure
from shopping.tasks.point_tasks import add_points_after_payment
from shopping.tests.factories import (
    EmailLogFactory,
    EmailVerificationTokenFactory,
    OrderFactory,
    OrderItemFactory,
    PaymentFactory,
    ProductFactory,
    TossResponseBuilder,
    UserFactory,
)
from shopping.utils.toss_payment import TossPaymentError

SEND_MAIL = "shopping.tasks.email_tasks.send_mail"
NOTIFY_DELAY = "shopping.tasks.payment_tasks.notify_payment_failure.delay"
FINALIZE_DELAY = "shopping.tasks.payment_tasks.finalize_payment_confirm.delay"
LOOKUP = "shopping.utils.toss_payment.TossPaymentClient.get_payment"


class _RetryCalled(Exception):
    """task.retry 를 대신해 던지는 표식 — 넘겨받은 countdown 을 검사하려고 쓴다"""


def _capture_retry(mocker, task):
    return mocker.patch.object(task, "retry", side_effect=_RetryCalled())


def _paid_order(level="bronze", amount="10000"):
    user = UserFactory.with_membership(level=level)
    order = OrderFactory(
        user=user,
        status="paid",
        total_amount=Decimal(amount),
        final_amount=Decimal(amount),
    )
    OrderItemFactory(order=order, product=ProductFactory())
    PaymentFactory.done(order=order)
    return user, order


# ==========================================
# 포인트 적립 — 두 번 와도 한 번
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestPointEarningIsOncePerOrder:
    def test_second_delivery_of_the_same_message_does_not_earn_again(self):
        user, order = _paid_order()

        first = add_points_after_payment(user_id=user.id, order_id=order.id)
        second = add_points_after_payment(user_id=user.id, order_id=order.id)

        assert first["status"] == "success"
        assert second["status"] == "already_processed"
        user.refresh_from_db()
        assert user.points == 100  # 10000 * 1% 한 번
        assert list(PointHistory.objects.filter(order=order, type="earn").values_list("points", flat=True)) == [100]

    def test_failure_after_earning_leaves_nothing_so_the_retry_earns_once(self, mocker):
        """적립 뒤 단계(결제 로그)에서 실패하면 적립도 함께 롤백 — 재시도가 두 번째 적립을 만들지 않는다

        예전에는 적립이 먼저 따로 커밋되고 그 뒤에서 실패하면 retry 가 처음부터 다시 적립했다.
        """
        user, order = _paid_order()
        mocker.patch.object(PaymentLog.objects, "create", side_effect=RuntimeError("db blip"))
        _capture_retry(mocker, add_points_after_payment)

        with pytest.raises(_RetryCalled):
            add_points_after_payment(user_id=user.id, order_id=order.id)

        user.refresh_from_db()
        order.refresh_from_db()
        assert user.points == 0
        assert order.earned_points == 0
        assert not PointHistory.objects.filter(order=order, type="earn").exists()

        mocker.stopall()
        result = add_points_after_payment(user_id=user.id, order_id=order.id)
        assert result["status"] == "success"
        user.refresh_from_db()
        assert user.points == 100
        assert PointHistory.objects.filter(order=order, type="earn").count() == 1

    def test_database_rejects_a_second_earn_row_for_the_same_order(self):
        """마지막 방어선 — 적립 경로가 가드를 빠뜨려도 주문당 구매 적립 이력은 하나"""
        user, order = _paid_order()
        PointHistory.create_history(user=user, points=100, balance=100, type="earn", order=order)

        with pytest.raises(IntegrityError), transaction.atomic():
            PointHistory.create_history(user=user, points=100, balance=200, type="earn", order=order)

        # 구매 적립이 아닌 이력(환불 등)은 주문당 여러 개여도 된다
        PointHistory.create_history(user=user, points=50, balance=150, type="cancel_refund", order=order)
        PointHistory.create_history(user=user, points=50, balance=200, type="cancel_refund", order=order)


# ==========================================
# 메일 — 같은 메시지는 한 통
# ==========================================


@pytest.mark.django_db
class TestVerificationMailIsSentOncePerToken:
    @pytest.mark.parametrize("is_resend", [False, True])
    def test_already_sent_token_is_not_mailed_again(self, mocker, is_resend):
        """재배달·재시도·스윕이 같은 토큰으로 다시 와도 한 통 (재발송 버튼은 새 토큰을 만든다)

        예전 가드는 status == "send"(오타)라 한 번도 작동하지 않았다.
        """
        send_mail = mocker.patch(SEND_MAIL, return_value=1)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        send_verification_email_task(user_id=user.id, token_id=token.id, is_resend=is_resend)
        send_verification_email_task(user_id=user.id, token_id=token.id, is_resend=is_resend)

        assert send_mail.call_count == 1
        assert list(EmailLog.objects.filter(token=token).values_list("status", flat=True)) == ["sent"]


@pytest.mark.django_db
class TestGenericMailIsSentOncePerMessage:
    KWARGS = {
        "subject": "[Django 쇼핑몰] 비밀번호 재설정 안내",
        "message": "reset link",
        "email_type": "password_reset",
    }

    def test_redelivered_message_is_not_mailed_again(self, mocker):
        """같은 메시지(같은 task id)가 다시 오면 한 통"""
        send_mail = mocker.patch(SEND_MAIL, return_value=1)
        user = UserFactory()
        kwargs = {**self.KWARGS, "recipient_list": [user.email], "user_id": user.id}

        send_email_task.apply(kwargs=kwargs, task_id="redelivered-message").get()
        send_email_task.apply(kwargs=kwargs, task_id="redelivered-message").get()

        assert send_mail.call_count == 1
        assert list(EmailLog.objects.filter(task_id="redelivered-message").values_list("status", flat=True)) == ["sent"]

    def test_two_requests_with_the_same_content_are_both_mailed(self, mocker):
        """사용자가 비밀번호 재설정을 두 번 요청하면 두 통 — 인자가 같아도 다른 메시지다"""
        send_mail = mocker.patch(SEND_MAIL, return_value=1)
        user = UserFactory()
        kwargs = {**self.KWARGS, "recipient_list": [user.email], "user_id": user.id}

        send_email_task.apply(kwargs=kwargs, task_id="first-request").get()
        send_email_task.apply(kwargs=kwargs, task_id="second-request").get()

        assert send_mail.call_count == 2


@pytest.mark.django_db
class TestFailedMailSweep:
    def _failed_log(self, minutes_ago, resend_count=0):
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)
        return EmailLogFactory.failed(
            user=user,
            token=token,
            failed_at=timezone.now() - timedelta(minutes=minutes_ago),
            resend_count=resend_count,
        )

    def test_waits_while_the_task_may_still_be_retrying(self, mocker):
        """방금 실패한 메일은 발송 태스크의 재시도가 살아 있을 수 있다 — 스윕이 또 보내면 복구 시 여러 통"""
        delay = mocker.patch.object(send_verification_email_task, "delay")
        self._failed_log(minutes_ago=1)

        result = retry_failed_emails_task()

        assert result["retry_attempted"] == 0
        delay.assert_not_called()

    def test_resends_once_and_does_not_pick_the_same_log_again(self, mocker):
        delay = mocker.patch.object(send_verification_email_task, "delay")
        log = self._failed_log(minutes_ago=django_settings.EMAIL_RESEND_AFTER_MINUTES + 1)

        first = retry_failed_emails_task()
        second = retry_failed_emails_task()  # 재발송 메시지가 아직 처리되기 전의 다음 주기

        assert first["retry_attempted"] == 1
        assert second["retry_attempted"] == 0
        assert delay.call_count == 1
        log.refresh_from_db()
        assert log.status == "pending"
        assert log.resend_count == 1

    def test_stops_after_the_resend_limit(self, mocker):
        delay = mocker.patch.object(send_verification_email_task, "delay")
        self._failed_log(
            minutes_ago=django_settings.EMAIL_RESEND_AFTER_MINUTES + 1,
            resend_count=django_settings.EMAIL_MAX_RESENDS,
        )

        assert retry_failed_emails_task()["retry_attempted"] == 0
        delay.assert_not_called()

    def test_failure_records_when_it_failed(self, mocker):
        mocker.patch(SEND_MAIL, side_effect=SMTPException("down"))
        _capture_retry(mocker, send_verification_email_task)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)

        with pytest.raises(_RetryCalled):
            send_verification_email_task(user_id=user.id, token_id=token.id)

        log = EmailLog.objects.get(token=token)
        assert log.status == "failed"
        assert log.failed_at is not None


# ==========================================
# 수동 retry 에 데코레이터의 백오프가 실제로 적용된다
# ==========================================


@pytest.mark.django_db(transaction=True)
class TestManualRetryUsesConfiguredBackoff:
    """Celery 의 retry_backoff/jitter 는 autoretry_for 전용 — countdown 을 안 넘기면 180초 고정이었다"""

    def test_finalize(self, mocker):
        retry = _capture_retry(mocker, finalize_payment_confirm)
        with pytest.raises(_RetryCalled):
            finalize_payment_confirm({"status": "DONE"}, 999999, 1)  # 없는 결제 → 예외 → 재시도
        assert 0 <= retry.call_args.kwargs["countdown"] <= 5

    def test_rollback(self, mocker):
        from shopping.models.order import Order

        user, order = _paid_order()
        mocker.patch.object(Order.objects, "select_for_update", side_effect=RuntimeError("db blip"))
        retry = _capture_retry(mocker, rollback_payment_failure)
        with pytest.raises(_RetryCalled):
            rollback_payment_failure(order.id, "test")
        assert 0 <= retry.call_args.kwargs["countdown"] <= 5

    def test_add_points(self, mocker):
        user, order = _paid_order()
        mocker.patch(
            "shopping.services.point_service.PointService.add_points",
            side_effect=RuntimeError("db blip"),
        )
        retry = _capture_retry(mocker, add_points_after_payment)
        with pytest.raises(_RetryCalled):
            add_points_after_payment(user_id=user.id, order_id=order.id)
        assert 0 <= retry.call_args.kwargs["countdown"] <= 10

    def test_verification_mail(self, mocker):
        mocker.patch(SEND_MAIL, side_effect=SMTPException("down"))
        retry = _capture_retry(mocker, send_verification_email_task)
        user = UserFactory.unverified()
        token = EmailVerificationTokenFactory(user=user)
        with pytest.raises(_RetryCalled):
            send_verification_email_task(user_id=user.id, token_id=token.id)
        assert 0 <= retry.call_args.kwargs["countdown"] <= 30

    def test_generic_mail(self, mocker):
        mocker.patch(SEND_MAIL, side_effect=SMTPException("down"))
        retry = _capture_retry(mocker, send_email_task)
        user = UserFactory()
        with pytest.raises(_RetryCalled):
            send_email_task(subject="s", message="m", recipient_list=[user.email], user_id=user.id)
        assert 0 <= retry.call_args.kwargs["countdown"] <= 30


# ==========================================
# 결제 마감 소진 → 알림, 멈춘 결제 → 토스 조회로 대사
# ==========================================


@pytest.fixture
def stalled_payment(order):
    """토스 승인 뒤 마감이 끝나지 못한 결제: in_progress + payment_key, 11분 전부터 멈춤"""
    payment = Payment.objects.create(
        order=order,
        toss_order_id=order.order_number,
        amount=order.final_amount,
        status="in_progress",
        payment_key="stalled_key",
    )
    Payment.objects.filter(pk=payment.pk).update(
        updated_at=timezone.now() - timedelta(minutes=django_settings.PAYMENT_RECONCILE_AFTER_MINUTES + 1)
    )
    payment.refresh_from_db()
    return payment


def _toss_done(payment, **overrides):
    data = TossResponseBuilder.success_response(
        payment_key=payment.payment_key,
        order_id=payment.toss_order_id,
        amount=int(payment.amount),
    )
    data.update(overrides)
    return data


@pytest.mark.django_db
class TestFinalizeExhaustion:
    def test_last_attempt_failure_alerts_and_keeps_the_payment_for_reconciliation(self, mocker, stalled_payment):
        """토스는 청구했는데 마감이 끝내 실패 — 예전에는 ERROR 로그 한 줄이 전부였다"""
        payment = stalled_payment
        mocker.patch.object(Payment, "mark_as_paid", side_effect=RuntimeError("db down"))
        notify = mocker.patch(NOTIFY_DELAY)

        with pytest.raises(RuntimeError):
            finalize_payment_confirm.apply(
                args=[_toss_done(payment), payment.id, payment.order.user_id],
                retries=finalize_payment_confirm.max_retries,
            ).get()

        notify.assert_called_once()
        assert notify.call_args.args[1] == "finalize_exhausted"
        assert notify.call_args.kwargs["severity"] == "critical"
        payment.refresh_from_db()
        assert payment.status == "in_progress"
        assert PaymentLog.objects.filter(payment=payment, message__contains="재시도 소진").exists()


@pytest.mark.django_db
class TestReconcileStalledPayments:
    def _run(self):
        from shopping.tasks.payment_tasks import reconcile_stalled_payments

        return reconcile_stalled_payments()

    def test_approved_at_toss_is_finalized_again(self, mocker, stalled_payment):
        payment = stalled_payment
        mocker.patch(LOOKUP, return_value=_toss_done(payment))
        finalize = mocker.patch(FINALIZE_DELAY)

        result = self._run()

        assert result["refinalized"] == 1
        finalize.assert_called_once()
        assert finalize.call_args.args[1:] == (payment.id, payment.order.user_id)

    def test_end_to_end_the_stalled_payment_becomes_paid(self, mocker, stalled_payment):
        """마감 재발행을 mock 하지 않고 끝까지 — 멱등한 마감이 결제를 닫는다"""
        payment = stalled_payment
        mocker.patch(LOOKUP, return_value=_toss_done(payment))

        self._run()

        payment.refresh_from_db()
        assert payment.status == "done"
        assert payment.order.status == "paid"

    @pytest.mark.parametrize(
        "overrides",
        [{"status": "ABORTED"}, {"totalAmount": 1}, {"orderId": "someone-else"}],
        ids=["not_approved", "amount_mismatch", "order_mismatch"],
    )
    def test_anything_else_is_flagged_once_and_never_rolled_back(self, mocker, stalled_payment, overrides):
        payment = stalled_payment
        mocker.patch(LOOKUP, return_value=_toss_done(payment, **overrides))
        finalize = mocker.patch(FINALIZE_DELAY)
        notify = mocker.patch(NOTIFY_DELAY)
        rollback = mocker.patch("shopping.tasks.payment_tasks.rollback_payment_failure.delay")

        first = self._run()
        second = self._run()

        assert first["unresolved"] == 1
        assert second["unresolved"] == 0  # 같은 결제로 알림을 반복하지 않는다
        notify.assert_called_once()
        finalize.assert_not_called()
        rollback.assert_not_called()
        payment.refresh_from_db()
        assert payment.status == "in_progress"

    def test_unknown_to_toss_is_flagged(self, mocker, stalled_payment):
        mocker.patch(LOOKUP, side_effect=TossPaymentError("NOT_FOUND_PAYMENT", "없음", 404))
        notify = mocker.patch(NOTIFY_DELAY)

        assert self._run()["unresolved"] == 1
        notify.assert_called_once()

    def test_lookup_failure_is_retried_next_cycle(self, mocker, stalled_payment):
        mocker.patch(LOOKUP, side_effect=TossPaymentError("PROVIDER_ERROR", "오류", 500))
        notify = mocker.patch(NOTIFY_DELAY)

        result = self._run()

        assert result["unresolved"] == 0
        assert len(result["errors"]) == 1
        notify.assert_not_called()

    def test_recent_in_progress_is_left_to_the_running_chain(self, mocker, stalled_payment):
        Payment.objects.filter(pk=stalled_payment.pk).update(updated_at=timezone.now())
        lookup = mocker.patch(LOOKUP)

        assert self._run()["candidates"] == 0
        lookup.assert_not_called()


@pytest.mark.django_db
class TestConfirmStoresPaymentKey:
    def test_in_progress_payment_keeps_the_key_for_reconciliation(self, mocker, order, user):
        """승인 체인이 멈추면 대사 스윕이 이 키로 토스에 묻는다 — 예전에는 마감 때까지 키가 비어 있었다"""
        from shopping.services.payment_service import PaymentService

        payment = Payment.objects.create(
            order=order,
            toss_order_id=order.order_number,
            amount=order.final_amount,
            status="ready",
        )
        mocker.patch(
            "shopping.tasks.payment_tasks.call_toss_confirm_api",
            side_effect=RuntimeError("stop here"),
        )

        with pytest.raises(RuntimeError):
            PaymentService.confirm_payment_async(payment, "key_from_widget", order.id, int(payment.amount), user)

        payment.refresh_from_db()
        assert payment.status == "in_progress"
        assert payment.payment_key == "key_from_widget"

    def test_a_key_owned_by_another_payment_is_rejected(self, order, user):
        from shopping.services.payment_service import PaymentConfirmError, PaymentService

        other_user, other_order = _paid_order()
        Payment.objects.filter(order=other_order).update(payment_key="taken_key")
        payment = Payment.objects.create(
            order=order,
            toss_order_id=order.order_number,
            amount=order.final_amount,
            status="ready",
        )

        with pytest.raises(PaymentConfirmError):
            PaymentService.confirm_payment_async(payment, "taken_key", order.id, int(payment.amount), user)

        payment.refresh_from_db()
        assert payment.status == "ready"


# ==========================================
# 배포 설정 — 외부 API 큐는 전용 워커
# ==========================================


def test_external_api_queue_has_its_own_worker():
    """큐를 나눠도 한 워커가 전부 먹으면 격리가 없다 (실측: 토스 15초 지연 중 주문 확정 0.1초 → 8.8초)"""
    compose = yaml.safe_load((Path(django_settings.BASE_DIR) / "docker-compose.yml").read_text(encoding="utf-8"))
    queues_by_worker = {}
    for name, service in compose["services"].items():
        command = service.get("command") or ""
        if isinstance(command, list):
            command = " ".join(command)
        if "celery" in command and " worker" in command and "-Q " in command:
            queues_by_worker[name] = set(command.split("-Q ", 1)[1].split()[0].split(","))

    consumers = [name for name, queues in queues_by_worker.items() if "external_api" in queues]
    assert consumers, queues_by_worker
    for name in consumers:
        assert queues_by_worker[name] == {
            "external_api"
        }, f"{name} 가 external_api 와 다른 큐를 함께 처리: {queues_by_worker[name]}"

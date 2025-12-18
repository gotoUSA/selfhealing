from __future__ import annotations

import traceback
from datetime import timedelta
from smtplib import SMTPException
from typing import Any

from celery import shared_task
from celery.utils.log import get_task_logger
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone

# Phase 2 chaos injection
from shopping.chaos.decorators import (
    inject_phase2_point_orphan,
    Phase2PointOrphanException,
)

logger = get_task_logger(__name__)


@shared_task(
    bind=True,
    name="shopping.tasks.expire_points_task",
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def expire_points_task(self) -> dict[str, Any]:
    """
    포인트 만료 처리 태스크
    매일 새벽 2시에 실행됨

    Returns:
        처리 결과 딕셔너리
    """
    from shopping.services.point_service import PointService

    logger.info(f"포인트 만료 처리 시작: {timezone.now()}")

    try:
        service = PointService()
        expired_count = service.expire_points()

        result = {
            "status": "success",
            "expired_count": expired_count,
            "executed_at": timezone.now().isoformat(),
            "message": f"{expired_count}건의 포인트가 만료 처리되었습니다.",
        }

        logger.info(f"포인트 만료 처리 완료: {result}")
        return result

    except Exception as e:
        logger.error(f"포인트 만료 처리 실패: {str(e)}\n{traceback.format_exc()}")

        # 재시도
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="shopping.tasks.send_expiry_notification_task",
    max_retries=3,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def send_expiry_notification_task(self) -> dict[str, Any]:
    """
    포인트 만료 예정 알림 발송 태스크
    매일 오전 10시에 실행됨

    Returns:
        처리 결과 딕셔너리
    """
    from shopping.services.point_service import PointService

    logger.info(f"포인트 만료 알림 발송 시작: {timezone.now()}")

    try:
        service = PointService()
        notification_count = service.send_expiry_notifications()

        result = {
            "status": "success",
            "notification_count": notification_count,
            "executed_at": timezone.now().isoformat(),
            "message": f"{notification_count}명에게 만료 예정 알림을 발송했습니다.",
        }

        logger.info(f"포인트 만료 알림 발송 완료: {result}")
        return result

    except Exception as e:
        logger.error(f"포인트 만료 알림 발송 실패: {str(e)}\n{traceback.format_exc()}")
        raise self.retry(exc=e)


@shared_task(
    bind=True,
    name="shopping.tasks.send_email_notification",
    queue="notifications",
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def send_email_notification(self, email: str, subject: str, message: str, html_message: str | None = None) -> bool:
    """
    이메일 알림 발송 태스크

    Args:
        email: 수신자 이메일
        subject: 제목
        message: 본문 (텍스트)
        html_message: HTML 본문 (선택)

    Returns:
        발송 성공 여부
    """
    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=(settings.DEFAULT_FROM_EMAIL if hasattr(settings, "DEFAULT_FROM_EMAIL") else "noreply@shopping.com"),
            recipient_list=[email],
            html_message=html_message,
            fail_silently=False,
        )

        logger.info(f"이메일 발송 성공: {email} - {subject}")
        return True

    except SMTPException as e:
        # SMTP 오류는 재시도 가치 있음
        logger.error(f"SMTP 오류: {email} - {str(e)}")
        raise self.retry(exc=e)

    except Exception as e:
        # 그 외 오류는 재시도 안 함
        logger.error(f"이메일 발송 실패: {email} - {str(e)}")
        raise


@shared_task(
    bind=True,
    name="shopping.tasks.add_points_after_payment",
    queue="points",
    priority=5,  # 낮은 우선순위
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=180,
    retry_jitter=True,
    acks_late=True,
)
def add_points_after_payment(self, user_id: int, order_id: int) -> dict[str, Any]:
    """
    결제 완료 후 포인트 적립 처리 (비동기)

    Args:
        user_id: 사용자 ID
        order_id: 주문 ID

    Returns:
        처리 결과 딕셔너리
    """
    from decimal import Decimal

    from shopping.models.order import Order
    from shopping.models.payment import PaymentLog
    from shopping.models.user import User
    from shopping.services.point_service import PointService

    logger.info(f"포인트 적립 처리 시작: user_id={user_id}, order_id={order_id}")

    try:
        user = User.objects.get(pk=user_id)
        order = Order.objects.get(pk=order_id)

        # 포인트 적립 계산
        # 포인트로만 결제한 경우는 적립하지 않음
        if order.final_amount <= 0:
            logger.info(f"포인트 전액 결제로 적립 없음: order_id={order_id}")
            return {
                "status": "skipped",
                "message": "포인트 전액 결제로 적립하지 않음",
                "order_id": order_id,
            }

        # 등급별 적립률 적용
        earn_rate = user.get_earn_rate()  # 1, 2, 3, 5 (%)
        # total_amount는 이미 순수 상품 금액 (배송비 미포함)
        product_amount = order.total_amount
        points_to_add = int(product_amount * Decimal(earn_rate) / Decimal("100"))

        if points_to_add <= 0:
            logger.info(f"적립할 포인트 없음: order_id={order_id}, final_amount={order.final_amount}")
            return {
                "status": "skipped",
                "message": "적립할 포인트 없음",
                "order_id": order_id,
            }

        # [CHAOS PHASE 2] BP-29: Point accumulation orphan
        # Payment succeeded but points won't be accumulated
        try:
            inject_phase2_point_orphan(
                user_id=user_id,
                order_id=order_id,
                points=points_to_add
            )
        except Phase2PointOrphanException as e:
            logger.error(f"[CHAOS BP-29] Point orphan: user={user_id}, order={order_id}, points={points_to_add}")
            # INTENTIONAL: Customer paid but won't get points
            # Self-healing should detect missing points via reconciliation
            raise self.retry(exc=e, countdown=3)

        logger.info(f"포인트 적립: user_id={user.id}, order_id={order.id}, " f"points={points_to_add}, earn_rate={earn_rate}%")

        # 포인트 적립
        PointService.add_points(
            user=user,
            amount=points_to_add,
            type="earn",
            order=order,
            description=f"주문 #{order.order_number} 구매 적립",
            metadata={
                "order_id": order.id,
                "order_number": order.order_number,
                "payment_amount": str(order.final_amount),
                "product_amount": str(product_amount),
                "shipping_fee": str(order.get_total_shipping_fee()),
                "earn_rate": f"{earn_rate}%",
                "membership_level": user.membership_level,
            },
        )

        # 주문에 적립 포인트 기록
        order.earned_points = points_to_add
        order.save(update_fields=["earned_points"])

        # 포인트 적립 로그
        # Order와 Payment는 OneToOne 관계이므로 .payment로 접근
        if hasattr(order, "payment"):
            PaymentLog.objects.create(
                payment=order.payment,
                log_type="approve",
                message=f"포인트 {points_to_add}점 적립",
                data={"points": points_to_add},
            )

        logger.info(f"포인트 적립 완료: user_id={user.id}, order_id={order.id}, points={points_to_add}")

        return {
            "status": "success",
            "user_id": user_id,
            "order_id": order_id,
            "points_added": points_to_add,
            "message": f"{points_to_add} 포인트가 적립되었습니다.",
        }

    except (User.DoesNotExist, Order.DoesNotExist) as e:
        # 재시도 불가 - 데이터가 없으면 재시도해도 동일
        logger.error(f"포인트 적립 실패 - 데이터 없음: {str(e)}")
        return {
            "status": "failed",
            "message": str(e),
            "user_id": user_id,
            "order_id": order_id,
        }

    except Exception as e:
        logger.error(f"포인트 적립 처리 실패: user_id={user_id}, order_id={order_id}, error={str(e)}")

        # 재시도
        raise self.retry(exc=e)


@shared_task(name="shopping.tasks.process_single_user_points", queue="points")
def process_single_user_points(user_id: int) -> dict:
    """
    특정 사용자의 포인트 만료 처리
    관리자가 수동으로 실행하거나 특정 이벤트 시 사용

    Args:
        user_id: 사용자 ID

    Returns:
        처리 결과
    """
    from django.contrib.auth import get_user_model

    from shopping.services.point_service import PointService

    User = get_user_model()

    try:
        user = User.objects.get(id=user_id)
        service = PointService()

        # 해당 사용자의 만료 포인트만 처리
        expired_points = service.get_expired_points()
        user_expired = [p for p in expired_points if p.user_id == user_id]

        expired_count = 0
        for point in user_expired:
            # 개별 처리 로직...
            expired_count += 1

        return {
            "status": "success",
            "user": user.username,
            "expired_count": expired_count,
        }
    except User.DoesNotExist:
        logger.error(f"사용자를 찾을 수 없음: ID={user_id}")
        return {"status": "error", "message": f"User {user_id} not found"}
    except Exception as e:
        logger.error(f"사용자 포인트 처리 실패: User={user_id}, Error={str(e)}")
        return {"status": "error", "message": str(e)}


@shared_task(name="shopping.tasks.cleanup_old_point_histories", ignore_result=True)
def cleanup_old_point_histories(days: int = 730) -> dict:
    """
    오래된 포인트 이력 정리
    기본 2년 이상 된 만료 이력 삭제

    Args:
        days: 보관 기간 (일)

    Returns:
        삭제 결과
    """
    from shopping.models.point import PointHistory

    cutoff_date = timezone.now() - timedelta(days=days)

    try:
        # 만료된 포인트 중 오래된 것 삭제
        deleted_count, _ = PointHistory.objects.filter(type="expire", created_at__lt=cutoff_date).delete()

        logger.info(f"오래된 포인트 이력 삭제: {deleted_count}건")

        return {
            "status": "success",
            "deleted_count": deleted_count,
            "cutoff_date": cutoff_date.isoformat(),
        }

    except Exception as e:
        logger.error(f"포인트 이력 정리 실패: {str(e)}")
        return {"status": "error", "message": str(e)}

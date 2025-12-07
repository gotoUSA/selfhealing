"""
Celery 태스크 패키지
모든 태스크를 여기서 임포트하여 Celery가 자동으로 발견할 수 있게 함
"""

from .cleanup_tasks import (
    cleanup_expired_tokens_task,
    cleanup_old_email_logs_task,
    cleanup_used_tokens_task,
    delete_unverified_users_task,
)
from .email_tasks import retry_failed_emails_task, send_email_task, send_verification_email_task
from .order_tasks import process_order_heavy_tasks
from .payment_tasks import call_toss_confirm_api, finalize_payment_confirm
from .payment_recovery_tasks import (
    check_sla_violations,
    cleanup_expired_dlq,
    process_dlq_batch,
    reset_circuit_breaker,
    retry_failed_payment,
)
from .point_tasks import (
    add_points_after_payment,
    cleanup_old_point_histories,
    expire_points_task,
    process_single_user_points,
    send_email_notification,
    send_expiry_notification_task,
)

__all__ = [
    # 이메일 태스크
    "send_verification_email_task",
    "send_email_task",
    "retry_failed_emails_task",
    # 정리 태스크
    "delete_unverified_users_task",
    "cleanup_old_email_logs_task",
    "cleanup_used_tokens_task",
    "cleanup_expired_tokens_task",
    # 포인트 태스크
    "expire_points_task",
    "send_expiry_notification_task",
    "send_email_notification",
    "add_points_after_payment",
    "process_single_user_points",
    "cleanup_old_point_histories",
    # 주문 태스크
    "process_order_heavy_tasks",
    # 결제 태스크
    "call_toss_confirm_api",
    "finalize_payment_confirm",
    # 결제 복구 태스크 (L3 Self-Healing)
    "retry_failed_payment",
    "check_sla_violations",
    "cleanup_expired_dlq",
    "process_dlq_batch",
    "reset_circuit_breaker",
]

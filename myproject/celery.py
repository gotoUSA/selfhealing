"""
Celery 설정 파일
Redis를 브로커로 사용하여 비동기 작업 처리
"""

from __future__ import annotations

import os

from celery import Celery
from celery.schedules import crontab

# Django 설정 모듈 지정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

# Celery 앱 생성
app = Celery("myproject")

# Django 설정에서 CELERY_ 접두사가 붙은 설정 로드
app.config_from_object("django.conf:settings", namespace="CELERY")

# TESTING 환경에서는 Django settings의 broker 설정을 강제로 적용
# (app 생성 시점의 환경변수보다 Django settings 우선)
from django.conf import settings

if hasattr(settings, "CELERY_BROKER_URL"):
    app.conf.broker_url = settings.CELERY_BROKER_URL
if hasattr(settings, "CELERY_RESULT_BACKEND"):
    app.conf.result_backend = settings.CELERY_RESULT_BACKEND

# 등록된 Django 앱에서 tasks.py 자동 로드
app.autodiscover_tasks()

# =============================================================================
# Self-Healing Signal Hooks (Zero-Code Integration)
# =============================================================================
# This enables automatic Circuit Breaker, DLQ, Forensics, and Metrics tracking
# for ALL Celery tasks without modifying individual task code.
# =============================================================================
try:
    from selfhealing.adapters.celery import setup_selfhealing_signals

    setup_selfhealing_signals(
        enabled=True,
        cb_enabled=True,
        dlq_enabled=True,
        metrics_enabled=True,
        forensics_enabled=True,
        # Map specific tasks to domains for better classification
        task_domain_mapping={
            "shopping.tasks.payment_tasks.confirm_toss_payment": "payment",
            "shopping.tasks.payment_tasks.retry_failed_payment": "payment",
            "shopping.tasks.payment_tasks.process_toss_payment_confirm": "payment",
            "shopping.tasks.payment_tasks.detect_orphaned_orders": "order",
            "shopping.tasks.order_tasks.process_order": "order",
            "shopping.tasks.email_tasks.send_email_task": "notification",
            "shopping.tasks.email_tasks.retry_failed_emails_task": "notification",
        },
    )
    print("[SelfHealing] Celery signal hooks enabled - CB, DLQ, Forensics, Metrics active")
except ImportError:
    # selfhealing package not installed - that's OK
    print("[SelfHealing] Package not installed, signal hooks disabled")
except Exception as e:
    print(f"[SelfHealing] Failed to setup signal hooks: {e}")

# Celery Beat 스케줄 설정
app.conf.beat_schedule = {
    # 이메일 관련 태스크
    # 실패한 이메일 재시도 - 5분마다
    "retry-failed-emails": {
        "task": "shopping.tasks.email_tasks.retry_failed_emails_task",
        "schedule": crontab(minute="*/5"),  # 5분마다
        "options": {
            "expires": 300,  # 5분 후 만료
        },
    },
    # 정리(Cleanup) 관련 태스크
    # 미인증 계정 삭제 - 매일 새벽 3시
    "delete-unverified-users": {
        "task": "shopping.tasks.cleanup_tasks.delete_unverified_users_task",
        "schedule": crontab(hour=3, minute=0),  # 매일 03:00
        "options": {
            "expires": 3600,  # 1시간 후 만료
        },
    },
    # 오래된 이메일 로그 정리 - 매주 일요일 새벽 4시
    "cleanup-old-email-logs": {
        "task": "shopping.tasks.cleanup_tasks.cleanup_old_email_logs_task",
        "schedule": crontab(hour=4, minute=0, day_of_week=0),  # 일요일 04:00
        "options": {
            "expires": 3600,
        },
    },
    # 사용된 토큰 정리 - 매주 일요일 새벽 4시 30분
    "cleanup-used-tokens": {
        "task": "shopping.tasks.cleanup_tasks.cleanup_used_tokens_task",
        "schedule": crontab(hour=4, minute=30, day_of_week=0),  # 일요일 04:30
        "options": {
            "expires": 3600,
        },
    },
    # 만료된 토큰 정리 - 매일 새벽 2시
    "cleanup-expired-tokens": {
        "task": "shopping.tasks.cleanup_tasks.cleanup_expired_tokens_task",
        "schedule": crontab(hour=2, minute=0),  # 매일 02:00
        "options": {
            "expires": 3600,
        },
    },
    # 포인트 만료 처리 - 매일 새벽 2시
    "expire-points-daily": {
        "task": "shopping.tasks.expire_points_task",
        "schedule": crontab(hour=2, minute=0),  # 매일 02:00
        "options": {
            "expires": 3600,  # 1시간 후 만료
        },
    },
    # 포인트 만료 예정 알림 - 매일 오전 10시
    "send-expiry-notifications": {
        "task": "shopping.tasks.send_expiry_notification_task",
        "schedule": crontab(hour=10, minute=0),  # 매일 10:00
        "options": {
            "expires": 3600,
        },
    },
    # 결제 실패 후 미처리된 주문 감지 - 5분마다
    "detect-orphaned-orders": {
        "task": "shopping.tasks.payment_tasks.detect_orphaned_orders",
        "schedule": crontab(minute="*/5"),  # 5분마다
        "options": {
            "expires": 300,  # 5분 후 만료
        },
        "kwargs": {"threshold_minutes": 10},  # 10분 이상 불일치 상태인 주문만
    },
    # ==========================================================================
    # Self-Healing Tasks (selfhealing package)
    # ==========================================================================
    # Circuit breaker recovery check - 매분
    "check-circuit-breaker-recovery": {
        "task": "selfhealing.adapters.celery.tasks.check_circuit_breaker_recovery",
        "schedule": 60.0,  # 매분
        "options": {
            "expires": 55,
        },
    },
    # Manual override expiration - 5분마다
    "expire-manual-overrides": {
        "task": "selfhealing.adapters.celery.tasks.expire_manual_overrides",
        "schedule": 300.0,  # 5분마다
        "options": {
            "expires": 290,
        },
    },
    # Self-healing metrics collection - 매분
    "collect-self-healing-metrics": {
        "task": "selfhealing.adapters.celery.tasks.collect_self_healing_metrics",
        "schedule": 60.0,  # 매분
        "options": {
            "expires": 55,
        },
    },
    # SLA breach check - 5분마다
    "check-sla-breaches": {
        "task": "selfhealing.adapters.celery.tasks.check_and_report_sla_breaches",
        "schedule": 300.0,  # 5분마다
        "options": {
            "expires": 290,
        },
    },
    # DLQ cleanup - 매일 새벽 5시
    "cleanup-dlq-entries": {
        "task": "selfhealing.adapters.celery.tasks.cleanup_resolved_dlq_entries",
        "schedule": crontab(hour=5, minute=0),  # 매일 05:00
        "options": {
            "expires": 3600,
        },
    },
    # Phase 6: Chaos Recovery Monitoring (32_CHAOS_SYSTEM_INTEGRATION.md §15.3, §22.2.3)
    # Check RECOVERY_MONITORING experiments - 30초마다
    "check-chaos-recovery-monitoring": {
        "task": "chaos.check_recovery_monitoring",
        "schedule": 30.0,  # 30초마다
        "options": {
            "expires": 25,
            "queue": "chaos_monitoring",
        },
    },
    # Phase 7: Zombie Hunter (34_CHAOS_SAFETY_MECHANISMS.md §5)
    # Hunt orphaned experiments (worker crash recovery) - 60초마다
    "chaos-hunt-zombie-experiments": {
        "task": "chaos.hunt_zombie_experiments",
        "schedule": 60.0,  # 매 1분
        "options": {
            "expires": 55,
            "queue": "chaos",
        },
    },
    # 테스트용: 5분마다 실행 (개발 환경에서만 사용)
    # 'test-periodic-task': {
    #     'task': 'shopping.tasks.test_periodic_task',
    #     'schedule': crontab(minute='*/5'),  # 5분마다
    # },
    # 스케줄 설명
    # 실행 시간표:
    # - 02:00 - 만료된 토큰 정리
    # - 03:00 - 미인증 계정 삭제
    # - 04:00 - 이메일 로그 정리 (일요일만)
    # - 04:30 - 사용된 토큰 정리 (일요일만)
    # - */5분 - 실패한 이메일 재시도
    # 새벽 시간대에 정리 작업을 몰아서 처리하여
    # 서버 부하를 최소화합니다.
}


# Celery 설정
app.conf.update(
    # 작업 결과 만료 시간 (초)
    result_expires=3600,
    # 시간대 설정
    timezone="Asia/Seoul",
    # 작업 직렬화 방식
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    # 작업 실행 옵션
    task_soft_time_limit=300,  # 5분
    task_time_limit=600,  # 10분
    # 워커 설정
    worker_max_tasks_per_child=1000,  # 메모리 누수 방지
    worker_prefetch_multiplier=4,
    # 큐 설정
    task_default_queue="default",
    task_queues={
        "default": {
            "exchange": "default",
            "exchange_type": "direct",
            "routing_key": "default",
        },
        "payment_critical": {  # 결제 관련 작업 (최우선)
            "exchange": "payment_critical",
            "exchange_type": "direct",
            "routing_key": "payment.critical",
        },
        "order_processing": {  # 주문 처리
            "exchange": "order_processing",
            "exchange_type": "direct",
            "routing_key": "order.process",
        },
        "external_api": {  # 외부 API 호출
            "exchange": "external_api",
            "exchange_type": "direct",
            "routing_key": "external.api",
        },
        "points": {  # 포인트 관련 작업 전용 큐
            "exchange": "points",
            "exchange_type": "direct",
            "routing_key": "points.earn",
        },
        "notifications": {  # 알림 전용 큐
            "exchange": "notifications",
            "exchange_type": "direct",
            "routing_key": "notifications",
        },
    },
    # 라우팅 설정
    task_routes={
        # 결제 관련 (최우선)
        "shopping.tasks.payment_tasks.*": {
            "queue": "payment_critical",
            "routing_key": "payment.critical",
        },
        # 주문 처리
        "shopping.tasks.order_tasks.*": {
            "queue": "order_processing",
            "routing_key": "order.process",
        },
        # 외부 API 호출
        "shopping.tasks.external_api_tasks.*": {
            "queue": "external_api",
            "routing_key": "external.api",
        },
        # 포인트 (낮은 우선순위)
        "shopping.tasks.point_tasks.*": {
            "queue": "points",
            "routing_key": "points.earn",
        },
        # 기존 태스크 라우팅 (하위호환성)
        "shopping.tasks.expire_points_task": {"queue": "points"},
        "shopping.tasks.send_expiry_notification_task": {"queue": "notifications"},
    },
)

# TESTING 환경에서 broker 설정 최종 강제 적용
# app.conf.update() 이후에도 환경변수가 덮어쓸 수 있으므로 마지막에 재적용
if hasattr(settings, "TESTING") and settings.TESTING:
    if hasattr(settings, "CELERY_BROKER_URL"):
        app.conf.broker_url = settings.CELERY_BROKER_URL
    if hasattr(settings, "CELERY_RESULT_BACKEND"):
        app.conf.result_backend = settings.CELERY_RESULT_BACKEND


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> None:
    """디버그용 태스크"""
    print(f"Request: {self.request!r}")


from celery.signals import task_failure
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@task_failure.connect
def task_failure_handler(sender, task_id, exception, **kwargs):
    """
    Log failed tasks
    """
    logger.error(f"Task failed: {sender.name}, task_id={task_id}, error={exception}")

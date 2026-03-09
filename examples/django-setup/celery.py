from celery import Celery

app = Celery("myproject")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# Consumer Beat schedule (separate from selfhealing)
app.conf.beat_schedule = {
    # Your app tasks here
}

# ─── selfhealing Celery integration (per doc 321) ───
# 1. Beat schedule / Queue / Route / Task bulk registration
from selfhealing.adapters.celery.beat_schedule import configure_selfhealing_celery

configure_selfhealing_celery(
    app,
    # Per-module opt-out (all default to True)
    # include_intelligence=False,
    # include_saga=False,
    queue_prefix="myproject",  # Multi-service queue namespace isolation
    queue_type="quorum",  # RabbitMQ Quorum Queue (prevents message loss)
    enable_dlx=True,  # Dead Letter Exchange
)

# 2. Celery signal hooks (CB, DLQ, Metrics, Forensics auto-integration)
from selfhealing.adapters.celery import setup_selfhealing_signals

setup_selfhealing_signals(
    app=app,
    enabled=True,
    cb_enabled=True,
    dlq_enabled=True,
    metrics_enabled=True,
    forensics_enabled=True,
    task_domain_mapping={
        "myapp.tasks.process_payment": "payment",
        "myapp.tasks.process_order": "order",
    },
)

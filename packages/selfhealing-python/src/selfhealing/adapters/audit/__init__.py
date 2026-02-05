"""
Default Audit Log Adapter Implementations.

Provides non-invasive audit logging implementations:
- FileAuditLogAdapter: Log to files (default for production)
- StdoutAuditLogAdapter: Log to stdout (good for containers)
- NullAuditLogAdapter: No-op (for testing or opt-out)

WORM (Write Once Read Many) Storage Adapters:
- S3ObjectLockAdapter: AWS S3 with Object Lock (Compliance Mode)
- LokiAdapter: Grafana Loki (append-only log aggregation)
- HTTPWebhookAdapter: Generic HTTP POST to external systems
- SidecarFileWatcher: File watcher for sidecar pattern

비침투 원칙:
- 고객사 DB에 직접 접근하지 않음
- 기본값: FileAuditLogAdapter (로컬 JSONL)
- 외부 전송은 사이드카 패턴 또는 Export CLI로 수행

Users can implement their own adapters for:
- Database logging (사용자 책임)
- Custom solutions
"""

from .file_adapter import FileAuditLogAdapter
from .null_adapter import NullAuditLogAdapter
from .singleton import (
    get_audit_adapter,
    reset_audit_adapter,
    set_audit_adapter,
)
from .stdout_adapter import StdoutAuditLogAdapter
from .worm_adapters import (
    HTTPWebhookAdapter,
    LokiAdapter,
    LokiConfig,
    S3Config,
    S3ObjectLockAdapter,
    SidecarConfig,
    SidecarFileWatcher,
    WORMAdapter,
    create_worm_adapter,
)

# pylint: disable=undefined-all-variable
# Note: The following names are lazily imported via __getattr__
__all__ = [
    # Default Adapters (Non-invasive)
    "FileAuditLogAdapter",
    "StdoutAuditLogAdapter",
    "NullAuditLogAdapter",
    # WORM Storage Adapters
    "WORMAdapter",
    "S3Config",
    "S3ObjectLockAdapter",
    "LokiConfig",
    "LokiAdapter",
    "HTTPWebhookAdapter",
    # Sidecar Pattern
    "SidecarConfig",
    "SidecarFileWatcher",
    # Factory
    "create_worm_adapter",
    # Singleton Management
    "get_audit_adapter",
    "set_audit_adapter",
    "reset_audit_adapter",
    # Django Adapter (optional - requires Django)
    "DjangoAuditLogAdapter",
    "get_django_audit_adapter",
    # Kafka Producer Adapter (optional - requires confluent-kafka)
    "KafkaAuditAdapter",
    "get_kafka_audit_adapter",
    "reset_kafka_audit_adapter",
    # Kafka Consumer Adapters (optional - requires confluent-kafka)
    "KafkaConsumerConfig",
    "BaseAuditConsumer",
    "IdempotentAuditConsumer",
    "RebalanceAwareConsumer",
    "PostgreSQLSinkConfig",
    "PostgreSQLSinkConsumer",
]
# pylint: enable=undefined-all-variable


# Lazy import for Django adapter (to avoid import errors when Django is not installed)
def __getattr__(name: str):
    """Lazy import for optional adapters."""
    if name in ("DjangoAuditLogAdapter", "get_django_audit_adapter"):
        from .django_adapter import DjangoAuditLogAdapter, get_django_audit_adapter

        if name == "DjangoAuditLogAdapter":
            return DjangoAuditLogAdapter
        return get_django_audit_adapter

    if name in ("KafkaAuditAdapter", "get_kafka_audit_adapter", "reset_kafka_audit_adapter"):
        from .kafka_adapter import (
            KafkaAuditAdapter,
            get_kafka_audit_adapter,
            reset_kafka_audit_adapter,
        )

        if name == "KafkaAuditAdapter":
            return KafkaAuditAdapter
        if name == "get_kafka_audit_adapter":
            return get_kafka_audit_adapter
        return reset_kafka_audit_adapter

    # Kafka Consumer adapters
    consumer_names = (
        "KafkaConsumerConfig",
        "BaseAuditConsumer",
        "IdempotentAuditConsumer",
        "RebalanceAwareConsumer",
        "PostgreSQLSinkConfig",
        "PostgreSQLSinkConsumer",
    )
    if name in consumer_names:
        from .kafka_consumer import (
            BaseAuditConsumer,
            IdempotentAuditConsumer,
            KafkaConsumerConfig,
            PostgreSQLSinkConfig,
            PostgreSQLSinkConsumer,
            RebalanceAwareConsumer,
        )

        return {
            "KafkaConsumerConfig": KafkaConsumerConfig,
            "BaseAuditConsumer": BaseAuditConsumer,
            "IdempotentAuditConsumer": IdempotentAuditConsumer,
            "RebalanceAwareConsumer": RebalanceAwareConsumer,
            "PostgreSQLSinkConfig": PostgreSQLSinkConfig,
            "PostgreSQLSinkConsumer": PostgreSQLSinkConsumer,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""
Audit Backend Package.

Provides pluggable backend implementations for audit log storage.

Stage 1 (Active):
- LocalFileBackend: Local JSON Lines with hash chain

Stage 2 (Interface):
- CloudWatchBackend: AWS CloudWatch Logs
- DatadogBackend: Datadog logging

Stage 3 (Interface):
- S3WORMBackend: S3 with Object Lock (WORM)

Stage 4 (Interface):
- RemoteAuditBackend: Separate audit server with mTLS
"""

from selfhealing.audit.backends.base import (
    AuditBackend,
    AsyncAuditBackend,
    BackendHealth,
    BackendStatus,
    BufferedBackend,
    CompositeBackend,
)
from selfhealing.audit.backends.cloudwatch import CloudWatchBackend
from selfhealing.audit.backends.datadog import DatadogBackend
from selfhealing.audit.backends.local import LocalFileBackend
from selfhealing.audit.backends.remote import RemoteAuditBackend
from selfhealing.audit.backends.s3_worm import S3WORMBackend

__all__ = [
    # Base classes
    "AuditBackend",
    "AsyncAuditBackend",
    "BackendHealth",
    "BackendStatus",
    "BufferedBackend",
    "CompositeBackend",
    # Stage 1: Active
    "LocalFileBackend",
    # Stage 2: Interface only
    "CloudWatchBackend",
    "DatadogBackend",
    # Stage 3: Interface only
    "S3WORMBackend",
    # Stage 4: Interface only
    "RemoteAuditBackend",
]


def get_default_backend() -> LocalFileBackend:
    """Get the default local file backend."""
    return LocalFileBackend()


def create_composite_backend(
    local: bool = True,
    cloudwatch: bool = False,
    datadog: bool = False,
    s3_worm: bool = False,
    remote: bool = False,
    **kwargs,
) -> CompositeBackend:
    """
    Create a composite backend with multiple destinations.

    Args:
        local: Enable local file backend (default: True)
        cloudwatch: Enable CloudWatch backend
        datadog: Enable Datadog backend
        s3_worm: Enable S3 WORM backend
        remote: Enable remote audit server backend
        **kwargs: Additional arguments for backends

    Returns:
        CompositeBackend with configured backends
    """
    backends = []

    if local:
        backends.append(
            LocalFileBackend(
                log_dir=kwargs.get("log_dir"),
                enable_hash_chain=kwargs.get("enable_hash_chain", True),
            )
        )

    if cloudwatch:
        backends.append(
            CloudWatchBackend(
                log_group=kwargs.get("cloudwatch_log_group", "/selfhealing/audit"),
                region=kwargs.get("aws_region"),
            )
        )

    if datadog:
        backends.append(
            DatadogBackend(
                api_key=kwargs.get("datadog_api_key"),
                site=kwargs.get("datadog_site", "datadoghq.com"),
            )
        )

    if s3_worm:
        backends.append(
            S3WORMBackend(
                bucket=kwargs.get("s3_bucket", "selfhealing-audit-logs"),
                retention_days=kwargs.get("s3_retention_days", 365),
            )
        )

    if remote:
        backends.append(
            RemoteAuditBackend(
                server_url=kwargs.get("audit_server_url"),
                client_cert=kwargs.get("audit_client_cert"),
                client_key=kwargs.get("audit_client_key"),
            )
        )

    if not backends:
        # Always have at least local backend
        backends.append(LocalFileBackend())

    return CompositeBackend(backends)

"""
AWS S3 WORM (Write Once Read Many) Audit Backend (Interface Only).

This is a stub implementation for Stage 3: WORM Storage.
Actual implementation requires boto3 and S3 Object Lock enabled bucket.

To activate this backend:
1. pip install boto3
2. Create S3 bucket with Object Lock enabled
3. Configure retention policy
4. Set AWS credentials
"""

from datetime import datetime
from typing import Any

import structlog

from selfhealing.audit.backends.base import AuditBackend, BackendHealth, BackendStatus
from selfhealing.settings.audit_integrity import get_audit_integrity_settings

logger = structlog.get_logger()


def _get_default_retention_days() -> int:
    """Get default S3 WORM retention days from settings."""
    return get_audit_integrity_settings().s3_worm_retention_days


class S3WORMBackend(AuditBackend):
    """
    AWS S3 WORM (Object Lock) audit backend.

    Stores audit logs in S3 with Object Lock for immutable,
    tamper-proof long-term storage.

    Features (when implemented):
    - Object Lock (WORM) compliance
    - Automatic retention periods
    - Legal hold support
    - Cross-region replication
    - Glacier transition for cost optimization
    """

    def __init__(
        self,
        bucket: str = "selfhealing-audit-logs",
        prefix: str = "audit/",
        retention_days: int | None = None,
        retention_mode: str = "GOVERNANCE",  # or "COMPLIANCE"
        region: str | None = None,
    ):
        """
        Initialize S3 WORM backend.

        Args:
            bucket: S3 bucket name (must have Object Lock enabled)
            prefix: Key prefix for audit logs
            retention_days: Object retention period in days (default from AuditIntegritySettings)
            retention_mode: GOVERNANCE (can be overridden) or COMPLIANCE (immutable)
            region: AWS region
        """
        self._bucket = bucket
        self._prefix = prefix
        self._retention_days = (
            retention_days
            if retention_days is not None
            else _get_default_retention_days()
        )
        self._retention_mode = retention_mode
        self._region = region
        self._client = None
        self._enabled = False

        logger.info("s3_worm.backend_initialized")

    @property
    def name(self) -> str:
        """Return backend name."""
        return "S3WORM"

    def enable(self) -> bool:
        """Enable S3 WORM backend."""
        try:
            # Placeholder for actual check:
            # import boto3
            # self._client = boto3.client('s3', region_name=self._region)
            #
            # # Verify Object Lock is enabled
            # response = self._client.get_object_lock_configuration(Bucket=self._bucket)
            # if response['ObjectLockConfiguration']['ObjectLockEnabled'] != 'Enabled':
            #     raise ValueError("Object Lock not enabled on bucket")
            # self._enabled = True

            logger.warning("s3_worm.backend_not_configured")
            return False
        except Exception as e:
            logger.exception(
                "s3_worm_backend.failed_enable",
                error=e,
            )
            return False

    def write(self, entry: dict[str, Any]) -> bool:
        """
        Write an audit log entry to S3 with Object Lock.

        Currently a stub - returns True to not block the pipeline.
        """
        if not self._enabled:
            return True

        # Actual implementation:
        # import json
        # from datetime import datetime, timedelta, timezone
        #
        # try:
        #     timestamp = datetime.now(timezone.utc)
        #     key = f"{self._prefix}{timestamp:%Y/%m/%d}/{timestamp:%H%M%S}-{entry.get('integrity', {}).get('sequence', 0)}.json"
        #
        #     retention_date = timestamp + timedelta(days=self._retention_days)
        #
        #     self._client.put_object(
        #         Bucket=self._bucket,
        #         Key=key,
        #         Body=json.dumps(entry, default=str, ensure_ascii=False),
        #         ContentType='application/json',
        #         ObjectLockMode=self._retention_mode,
        #         ObjectLockRetainUntilDate=retention_date,
        #     )
        #     return True
        # except Exception as e:
        #     logger.exception(f"[S3WORMBackend] Write failed: {e}")
        #     return False

        return True

    def health_check(self) -> BackendHealth:
        """Check S3 WORM backend health."""
        if not self._enabled:
            return BackendHealth(
                status=BackendStatus.NOT_CONFIGURED,
                message="S3 WORM backend is interface-only (not configured)",
            )

        return BackendHealth(
            status=BackendStatus.NOT_CONFIGURED,
            message="S3 WORM backend requires configuration",
        )

    def query(
        self,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        config_type: str | None = None,
        user: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query audit logs from S3.

        Actual implementation would use S3 Select or Athena.
        """
        if not self._enabled:
            return []

        return []

    def get_configuration_template(self) -> dict[str, Any]:
        """Get configuration template for S3 WORM backend."""
        return {
            "required_packages": ["boto3"],
            "environment_variables": {
                "AWS_DEFAULT_REGION": "AWS region",
                "AWS_ACCESS_KEY_ID": "AWS access key",
                "AWS_SECRET_ACCESS_KEY": "AWS secret key",
            },
            "bucket_setup": {
                "step_1": "Create S3 bucket with Object Lock enabled (at bucket creation time)",
                "step_2": "Configure default retention policy",
                "step_3": "Set up lifecycle rules for Glacier transition (optional)",
                "step_4": "Enable versioning (required for Object Lock)",
                "step_5": "Configure cross-region replication (recommended)",
            },
            "terraform_example": """
resource "aws_s3_bucket" "audit_logs" {
  bucket = "selfhealing-audit-logs"

  object_lock_configuration {
    object_lock_enabled = "Enabled"
  }
}

resource "aws_s3_bucket_object_lock_configuration" "audit_logs" {
  bucket = aws_s3_bucket.audit_logs.bucket

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = 365
    }
  }
}
""",
            "config_options": {
                "bucket": self._bucket,
                "prefix": self._prefix,
                "retention_days": self._retention_days,
                "retention_mode": self._retention_mode,
            },
        }

    def place_legal_hold(self, object_key: str) -> bool:
        """
        Place a legal hold on an audit log object.

        Prevents deletion even after retention period expires.
        Use for investigations or legal requirements.
        """
        if not self._enabled:
            logger.warning("s3_worm.legal_hold_failed")
            return False

        # Actual implementation:
        # try:
        #     self._client.put_object_legal_hold(
        #         Bucket=self._bucket,
        #         Key=object_key,
        #         LegalHold={'Status': 'ON'}
        #     )
        #     return True
        # except Exception as e:
        #     logger.exception(f"[S3WORMBackend] Failed to place legal hold: {e}")
        #     return False

        return False

"""
WORM (Write Once Read Many) Storage Adapters.

고객사 DB에 침투하지 않으면서도 영구 보존을 달성하기 위한 어댑터들.

비침투 원칙:
- 고객사 인프라(DB)에 직접 접근하지 않음
- 독립적인 외부 저장소로 직접 전송
- 저장소 장애 시에도 메인 앱에 영향 없음 (FileAuditLogAdapter로 fallback)

제공되는 어댑터:
1. S3ObjectLockAdapter: AWS S3 Object Lock (Compliance Mode)
2. LokiAdapter: Grafana Loki (로그 집계)
3. SidecarAdapter: 파일 → 외부 전송 사이드카 패턴

사용법:
    # 직접 사용 (권장하지 않음 - 고객이 구현해야 함)
    adapter = S3ObjectLockAdapter(bucket="audit-logs", region="ap-northeast-2")

    # 또는 사이드카 패턴 (권장)
    # 메인 앱은 FileAuditLogAdapter 사용, 별도 프로세스가 S3로 전송
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selfhealing.interfaces.audit_adapter import AuditEntry, AuditLogAdapter

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# Configuration Classes
# ─────────────────────────────────────────────────────────────


@dataclass
class S3Config:
    """S3 Object Lock 설정."""

    bucket: str
    region: str = "ap-northeast-2"
    prefix: str = "audit/"
    object_lock_mode: str = "COMPLIANCE"  # GOVERNANCE or COMPLIANCE
    retention_days: int = 2555  # 7년 (PCI-DSS)

    # 인증 (AWS SDK 없이 사용 시)
    access_key_id: str | None = None
    secret_access_key: str | None = None

    # 또는 IAM Role 사용 (권장)
    use_iam_role: bool = True


@dataclass
class LokiConfig:
    """Grafana Loki 설정."""

    endpoint: str = "http://loki:3100/loki/api/v1/push"
    tenant_id: str | None = None
    labels: dict[str, str] = None
    timeout_seconds: float = 5.0
    batch_size: int = 100

    def __post_init__(self):
        if self.labels is None:
            self.labels = {"job": "selfhealing-audit", "env": "production"}


@dataclass
class SidecarConfig:
    """사이드카 패턴 설정."""

    watch_dir: str = "/var/log/audit"
    target_type: str = "s3"  # s3, loki, http
    poll_interval_seconds: float = 5.0
    on_success: str = "archive"  # archive, delete, rename


# ─────────────────────────────────────────────────────────────
# Abstract Base for WORM Adapters
# ─────────────────────────────────────────────────────────────


class WORMAdapter(AuditLogAdapter):
    """
    WORM 저장소 어댑터 기본 클래스.

    특징:
    - 한번 기록된 데이터는 수정/삭제 불가
    - 법적 효력을 위한 무결성 보장
    - 장애 시 로컬 fallback 지원
    """

    def __init__(
        self,
        fallback_path: Path | None = None,
        on_error: Callable[[Exception, AuditEntry], None] | None = None,
    ):
        """
        Initialize WORM adapter.

        Args:
            fallback_path: 장애 시 로컬 저장 경로
            on_error: 에러 발생 시 콜백 (모니터링용)
        """
        self._fallback_path = fallback_path or Path(
            "/var/log/audit/worm_fallback.jsonl"
        )
        self._on_error = on_error
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, entry: AuditEntry) -> None:
        """Log entry with fallback support."""
        try:
            self._write_to_worm(entry)
        except Exception as e:
            logger.warning(
                f"[WORM] Failed to write to WORM storage: {e}, using fallback"
            )
            self._write_to_fallback(entry)
            if self._on_error:
                self._on_error(e, entry)

    @abstractmethod
    def _write_to_worm(self, entry: AuditEntry) -> None:
        """Write to WORM storage. Subclasses must implement."""
        pass

    def _write_to_fallback(self, entry: AuditEntry) -> None:
        """Write to local fallback file."""
        try:
            with open(self._fallback_path, "a", encoding="utf-8") as f:
                f.write(entry.to_json() + "\n")
        except Exception as e:
            logger.error(f"[WORM] Fallback write failed: {e}")

    def query(
        self,
        action: Any | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        start_time: Any | None = None,
        end_time: Any | None = None,
        limit: int = 100,
    ) -> list:
        """WORM 저장소는 일반적으로 쿼리를 지원하지 않음."""
        raise NotImplementedError(
            "WORM storage does not support query. "
            "Use Export CLI or SignedManifest for auditing."
        )


# ─────────────────────────────────────────────────────────────
# S3 Object Lock Adapter (Interface Only)
# ─────────────────────────────────────────────────────────────


class S3ObjectLockAdapter(WORMAdapter):
    """
    AWS S3 Object Lock 어댑터 (인터페이스).

    Compliance Mode:
    - 보존 기간 동안 삭제/수정 불가능 (root 계정도 불가)
    - 법적 규정 준수에 적합

    NOTE: 실제 구현은 boto3 의존성이 필요합니다.
          이 클래스는 인터페이스 정의 + 예시 구현입니다.

    실제 사용 시:
        pip install boto3
        그리고 _write_to_worm() 메서드를 boto3로 구현하세요.

    Usage:
        # 기본 사용 (boto3 필요)
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="my-audit-bucket")
        )

        # 또는 커스텀 클라이언트 주입
        import boto3
        s3 = boto3.client('s3')
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="my-audit-bucket"),
            s3_client=s3
        )
    """

    def __init__(
        self,
        config: S3Config,
        s3_client: Any | None = None,  # boto3.client('s3')
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._config = config
        self._s3_client = s3_client

    def _write_to_worm(self, entry: AuditEntry) -> None:
        """
        Write to S3 with Object Lock.

        NOTE: 이 구현은 boto3가 설치되어 있어야 합니다.
        """
        if self._s3_client is None:
            raise NotImplementedError(
                "S3ObjectLockAdapter requires boto3. "
                "Install with: pip install boto3\n"
                "Then pass s3_client=boto3.client('s3') to constructor."
            )

        # Object key with timestamp and unique ID
        import uuid

        timestamp = datetime.now(timezone.utc).strftime("%Y/%m/%d/%H%M%S")
        unique_id = getattr(entry, "audit_id", None) or str(uuid.uuid4())[:8]
        key = f"{self._config.prefix}{timestamp}_{unique_id}.json"

        # Object Lock retention
        retention_date = datetime.now(timezone.utc)

        self._s3_client.put_object(
            Bucket=self._config.bucket,
            Key=key,
            Body=entry.to_json().encode("utf-8"),
            ContentType="application/json",
            ObjectLockMode=self._config.object_lock_mode,
            ObjectLockRetainUntilDate=retention_date,
        )


# ─────────────────────────────────────────────────────────────
# Loki Adapter (HTTP-based, no external dependencies)
# ─────────────────────────────────────────────────────────────


class LokiAdapter(WORMAdapter):
    """
    Grafana Loki 어댑터.

    Loki는 로그 집계 시스템으로, WORM과 유사한 특성을 가짐:
    - append-only 스토리지
    - 시간 기반 retention
    - 효율적인 쿼리

    NOTE: Loki 자체는 WORM이 아니지만, S3 백엔드와 함께 사용하면
          Object Lock으로 WORM 특성을 가질 수 있습니다.

    사용법:
        adapter = LokiAdapter(
            config=LokiConfig(endpoint="http://loki:3100/loki/api/v1/push")
        )
    """

    def __init__(self, config: LokiConfig | None = None, **kwargs):
        super().__init__(**kwargs)
        self._config = config or LokiConfig()
        self._batch: list[AuditEntry] = []

    def _write_to_worm(self, entry: AuditEntry) -> None:
        """Write to Loki via HTTP Push API."""
        # Loki Push API format
        # https://grafana.com/docs/loki/latest/api/#push-log-entries-to-loki
        timestamp_ns = int(datetime.now(timezone.utc).timestamp() * 1e9)

        payload = {
            "streams": [
                {
                    "stream": self._config.labels,
                    "values": [
                        [
                            str(timestamp_ns),
                            entry.to_json(),
                        ]
                    ],
                }
            ]
        }

        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            self._config.endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        if self._config.tenant_id:
            req.add_header("X-Scope-OrgID", self._config.tenant_id)

        try:
            with urllib.request.urlopen(
                req, timeout=self._config.timeout_seconds
            ) as response:
                if response.status not in (200, 204):
                    raise RuntimeError(f"Loki responded with status {response.status}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"Loki push failed: {e}") from e

    def query(
        self,
        query: str,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        Query logs from Loki.

        NOTE: Loki 쿼리는 읽기 전용이며, WORM 특성과 무관합니다.

        Args:
            query: LogQL query (e.g., '{job="selfhealing-audit"}')
            start: Start time
            end: End time
            limit: Max results

        Returns:
            List of log entries
        """
        # This is a placeholder - actual implementation would use Loki query API
        raise NotImplementedError("Loki query is not implemented in base adapter")


# ─────────────────────────────────────────────────────────────
# HTTP Webhook Adapter (Generic)
# ─────────────────────────────────────────────────────────────


class HTTPWebhookAdapter(WORMAdapter):
    """
    범용 HTTP Webhook 어댑터.

    외부 로그 수집 시스템으로 HTTP POST 전송.
    대상: Splunk, Elasticsearch, Datadog, 커스텀 시스템 등

    Usage:
        adapter = HTTPWebhookAdapter(
            endpoint="https://logs.example.com/ingest",
            headers={"Authorization": "Bearer xxx"},
        )
    """

    def __init__(
        self,
        endpoint: str,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 5.0,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._endpoint = endpoint
        self._headers = headers or {}
        self._timeout = timeout_seconds

    def _write_to_worm(self, entry: AuditEntry) -> None:
        """Write to HTTP endpoint."""
        data = entry.to_json().encode("utf-8")

        req = urllib.request.Request(
            self._endpoint,
            data=data,
            headers={
                "Content-Type": "application/json",
                **self._headers,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as response:
                if response.status >= 400:
                    raise RuntimeError(f"HTTP {response.status}: {response.read()}")
        except urllib.error.URLError as e:
            raise RuntimeError(f"HTTP push failed: {e}") from e


# ─────────────────────────────────────────────────────────────
# Sidecar Pattern Helper
# ─────────────────────────────────────────────────────────────


class SidecarFileWatcher:
    """
    사이드카 패턴을 위한 파일 감시자.

    메인 앱은 FileAuditLogAdapter로 로컬에 기록하고,
    이 클래스가 별도 프로세스에서 파일을 감시하여 외부 저장소로 전송합니다.

    비침투 원칙:
    - 메인 앱 프로세스와 완전히 분리
    - 사이드카가 죽어도 메인 앱에 영향 없음
    - 파일 기반 통신 (IPC 불필요)

    Usage:
        # 별도 프로세스 또는 컨테이너에서 실행
        watcher = SidecarFileWatcher(
            config=SidecarConfig(watch_dir="/var/log/audit"),
            target_adapter=S3ObjectLockAdapter(config=s3_config),
        )
        watcher.start()  # 블로킹 - 백그라운드에서 실행 권장
    """

    def __init__(
        self,
        config: SidecarConfig,
        target_adapter: AuditLogAdapter,
        on_success: Callable[[Path], None] | None = None,
        on_error: Callable[[Path, Exception], None] | None = None,
    ):
        self._config = config
        self._target = target_adapter
        self._on_success = on_success
        self._on_error = on_error
        self._running = False

    def start(self) -> None:
        """Start watching for new files (blocking)."""
        import time

        self._running = True
        watch_dir = Path(self._config.watch_dir)

        logger.info(f"[Sidecar] Watching {watch_dir} for audit files...")

        while self._running:
            try:
                self._process_files(watch_dir)
            except Exception as e:
                logger.error(f"[Sidecar] Error processing files: {e}")

            time.sleep(self._config.poll_interval_seconds)

    def stop(self) -> None:
        """Stop watching."""
        self._running = False

    def _process_files(self, watch_dir: Path) -> None:
        """Process all JSONL files in watch directory."""
        for file_path in watch_dir.glob("*.jsonl"):
            if file_path.name.startswith("."):  # Skip hidden/processing files
                continue

            try:
                self._process_file(file_path)
                if self._on_success:
                    self._on_success(file_path)
            except Exception as e:
                logger.error(f"[Sidecar] Failed to process {file_path}: {e}")
                if self._on_error:
                    self._on_error(file_path, e)

    def _process_file(self, file_path: Path) -> None:
        """Process a single JSONL file."""
        from selfhealing.interfaces.audit_adapter import AuditEntry

        # Mark as processing
        processing_path = file_path.with_suffix(".processing")
        file_path.rename(processing_path)

        try:
            with open(processing_path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry_dict = json.loads(line)

                    # AuditEntry 직접 생성
                    action = entry_dict.get("action", "config_change")
                    entry = AuditEntry(
                        action=action,
                        target_type=entry_dict.get("target_type"),
                        target_id=entry_dict.get("target_id"),
                        actor_id=entry_dict.get("actor_id"),
                        actor_type=entry_dict.get("actor_type", "system"),
                        details=entry_dict.get("details", {}),
                    )
                    self._target.log(entry)

            # Handle success
            if self._config.on_success == "delete":
                processing_path.unlink()
            elif self._config.on_success == "archive":
                archive_dir = file_path.parent / "archived"
                archive_dir.mkdir(exist_ok=True)
                processing_path.rename(archive_dir / file_path.name)
            else:  # rename
                processing_path.rename(file_path.with_suffix(".done"))

        except Exception:
            # Restore original file on error
            if processing_path.exists():
                processing_path.rename(file_path)
            raise


# ─────────────────────────────────────────────────────────────
# Factory Function
# ─────────────────────────────────────────────────────────────


def create_worm_adapter(
    adapter_type: str,
    config: dict[str, Any] | None = None,
    **kwargs,
) -> WORMAdapter:
    """
    WORM 어댑터 팩토리.

    Args:
        adapter_type: 's3', 'loki', 'http'
        config: 어댑터별 설정 딕셔너리
        **kwargs: 추가 설정

    Returns:
        WORMAdapter 인스턴스

    Usage:
        adapter = create_worm_adapter('loki', {'endpoint': 'http://loki:3100/...'})
    """
    config = config or {}

    if adapter_type == "s3":
        return S3ObjectLockAdapter(config=S3Config(**config), **kwargs)
    elif adapter_type == "loki":
        return LokiAdapter(config=LokiConfig(**config), **kwargs)
    elif adapter_type == "http":
        return HTTPWebhookAdapter(**config, **kwargs)
    else:
        raise ValueError(f"Unknown adapter type: {adapter_type}")

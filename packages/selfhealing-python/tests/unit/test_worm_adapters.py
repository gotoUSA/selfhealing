"""
WORM Adapters Tests.

S3ObjectLockAdapter, LokiAdapter, HTTPWebhookAdapter, SidecarFileWatcher 테스트.
"""

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock
import pytest

from selfhealing.adapters.audit.worm_adapters import (
    WORMAdapter,
    S3Config,
    S3ObjectLockAdapter,
    LokiConfig,
    LokiAdapter,
    HTTPWebhookAdapter,
    SidecarConfig,
    SidecarFileWatcher,
    create_worm_adapter,
)
from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction


# ─────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────


@pytest.fixture
def sample_entry():
    """샘플 감사 엔트리."""
    return AuditEntry(
        action=AuditAction.CONFIG_CHANGE,
        target_type="circuit_breaker",
        target_id="cb_payment",
        actor_id="test_user",
        actor_type="user",
        details={"key": "value"},
    )


@pytest.fixture
def temp_dir():
    """임시 디렉토리."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# ─────────────────────────────────────────────────────────────
# S3Config Tests
# ─────────────────────────────────────────────────────────────


class TestS3Config:
    """S3Config 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        config = S3Config(bucket="test-bucket")
        
        assert config.bucket == "test-bucket"
        assert config.region == "ap-northeast-2"
        assert config.prefix == "audit/"
        assert config.object_lock_mode == "COMPLIANCE"
        assert config.retention_days == 2555  # 7년

    def test_custom_values(self):
        """커스텀 값 설정."""
        config = S3Config(
            bucket="my-bucket",
            region="us-west-2",
            prefix="logs/audit/",
            object_lock_mode="GOVERNANCE",
            retention_days=365,
        )
        
        assert config.bucket == "my-bucket"
        assert config.region == "us-west-2"
        assert config.object_lock_mode == "GOVERNANCE"


# ─────────────────────────────────────────────────────────────
# S3ObjectLockAdapter Tests
# ─────────────────────────────────────────────────────────────


class TestS3ObjectLockAdapter:
    """S3ObjectLockAdapter 테스트."""

    def test_init_without_client_raises_not_implemented(self, sample_entry, temp_dir):
        """boto3 클라이언트 없이 사용 시 NotImplementedError."""
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="test"),
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        # log()는 fallback을 사용해야 함
        adapter.log(sample_entry)
        
        # fallback 파일에 기록되었는지 확인
        assert (temp_dir / "fallback.jsonl").exists()

    def test_with_mock_s3_client(self, sample_entry, temp_dir):
        """Mock S3 클라이언트 테스트."""
        mock_s3 = MagicMock()
        
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="test-bucket"),
            s3_client=mock_s3,
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        adapter.log(sample_entry)
        
        # S3 put_object 호출 확인
        mock_s3.put_object.assert_called_once()
        call_args = mock_s3.put_object.call_args
        
        assert call_args.kwargs["Bucket"] == "test-bucket"
        assert call_args.kwargs["ObjectLockMode"] == "COMPLIANCE"

    def test_fallback_on_s3_error(self, sample_entry, temp_dir):
        """S3 오류 시 fallback 사용."""
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 Error")
        
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="test-bucket"),
            s3_client=mock_s3,
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        adapter.log(sample_entry)
        
        # fallback 파일에 기록
        fallback_file = temp_dir / "fallback.jsonl"
        assert fallback_file.exists()
        
        with open(fallback_file) as f:
            line = f.readline()
            data = json.loads(line)
            assert data["action"] == "config_change"

    def test_error_callback(self, sample_entry, temp_dir):
        """에러 콜백 호출 확인."""
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = Exception("S3 Error")
        
        error_callback = MagicMock()
        
        adapter = S3ObjectLockAdapter(
            config=S3Config(bucket="test-bucket"),
            s3_client=mock_s3,
            fallback_path=temp_dir / "fallback.jsonl",
            on_error=error_callback,
        )
        
        adapter.log(sample_entry)
        
        error_callback.assert_called_once()


# ─────────────────────────────────────────────────────────────
# LokiConfig Tests
# ─────────────────────────────────────────────────────────────


class TestLokiConfig:
    """LokiConfig 테스트."""

    def test_default_values(self):
        """기본값 확인."""
        config = LokiConfig()
        
        assert config.endpoint == "http://loki:3100/loki/api/v1/push"
        assert config.tenant_id is None
        assert config.labels == {"job": "selfhealing-audit", "env": "production"}
        assert config.timeout_seconds == 5.0

    def test_custom_labels(self):
        """커스텀 레이블 설정."""
        config = LokiConfig(
            endpoint="http://custom-loki:3100/loki/api/v1/push",
            labels={"app": "myapp", "env": "staging"},
        )
        
        assert config.labels["app"] == "myapp"


# ─────────────────────────────────────────────────────────────
# LokiAdapter Tests
# ─────────────────────────────────────────────────────────────


class TestLokiAdapter:
    """LokiAdapter 테스트."""

    def test_successful_push(self, sample_entry, temp_dir):
        """성공적인 Loki 푸시."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 204
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            
            adapter = LokiAdapter(
                config=LokiConfig(endpoint="http://test-loki:3100/loki/api/v1/push"),
                fallback_path=temp_dir / "fallback.jsonl",
            )
            
            adapter.log(sample_entry)
            
            mock_urlopen.assert_called_once()

    def test_fallback_on_loki_error(self, sample_entry, temp_dir):
        """Loki 오류 시 fallback."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection refused")
            
            adapter = LokiAdapter(
                fallback_path=temp_dir / "fallback.jsonl",
            )
            
            adapter.log(sample_entry)
            
            # fallback 파일 확인
            assert (temp_dir / "fallback.jsonl").exists()

    def test_tenant_id_header(self, sample_entry, temp_dir):
        """Tenant ID 헤더 설정 확인."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 204
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            
            adapter = LokiAdapter(
                config=LokiConfig(tenant_id="my-tenant"),
                fallback_path=temp_dir / "fallback.jsonl",
            )
            
            adapter.log(sample_entry)
            
            # Request 객체 확인
            call_args = mock_urlopen.call_args
            request = call_args[0][0]
            assert request.get_header("X-scope-orgid") == "my-tenant"


# ─────────────────────────────────────────────────────────────
# HTTPWebhookAdapter Tests
# ─────────────────────────────────────────────────────────────


class TestHTTPWebhookAdapter:
    """HTTPWebhookAdapter 테스트."""

    def test_successful_post(self, sample_entry, temp_dir):
        """성공적인 HTTP POST."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_response = MagicMock()
            mock_response.status = 200
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_response
            
            adapter = HTTPWebhookAdapter(
                endpoint="https://logs.example.com/ingest",
                headers={"Authorization": "Bearer test-token"},
                fallback_path=temp_dir / "fallback.jsonl",
            )
            
            adapter.log(sample_entry)
            
            mock_urlopen.assert_called_once()
            
            # 헤더 확인
            request = mock_urlopen.call_args[0][0]
            assert request.get_header("Authorization") == "Bearer test-token"
            assert request.get_header("Content-type") == "application/json"

    def test_fallback_on_http_error(self, sample_entry, temp_dir):
        """HTTP 오류 시 fallback."""
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = Exception("Connection timeout")
            
            adapter = HTTPWebhookAdapter(
                endpoint="https://logs.example.com/ingest",
                fallback_path=temp_dir / "fallback.jsonl",
            )
            
            adapter.log(sample_entry)
            
            assert (temp_dir / "fallback.jsonl").exists()


# ─────────────────────────────────────────────────────────────
# SidecarFileWatcher Tests
# ─────────────────────────────────────────────────────────────


class TestSidecarFileWatcher:
    """SidecarFileWatcher 테스트."""

    def test_process_files(self, sample_entry, temp_dir):
        """파일 처리 테스트."""
        # 테스트 파일 생성
        input_file = temp_dir / "test.jsonl"
        with open(input_file, "w") as f:
            f.write(sample_entry.to_json() + "\n")
        
        # Mock target adapter
        mock_adapter = MagicMock()
        
        watcher = SidecarFileWatcher(
            config=SidecarConfig(
                watch_dir=str(temp_dir),
                on_success="delete",
            ),
            target_adapter=mock_adapter,
        )
        
        # 파일 처리
        watcher._process_files(temp_dir)
        
        # target adapter에 log 호출 확인
        mock_adapter.log.assert_called_once()
        
        # 파일 삭제 확인
        assert not input_file.exists()

    def test_archive_on_success(self, sample_entry, temp_dir):
        """성공 시 아카이브 옵션."""
        input_file = temp_dir / "test.jsonl"
        with open(input_file, "w") as f:
            f.write(sample_entry.to_json() + "\n")
        
        mock_adapter = MagicMock()
        
        watcher = SidecarFileWatcher(
            config=SidecarConfig(
                watch_dir=str(temp_dir),
                on_success="archive",
            ),
            target_adapter=mock_adapter,
        )
        
        watcher._process_files(temp_dir)
        
        # archived 폴더에 파일 존재 확인
        archive_dir = temp_dir / "archived"
        assert archive_dir.exists()
        assert (archive_dir / "test.jsonl").exists()

    def test_error_callback(self, temp_dir):
        """에러 콜백 테스트."""
        # 잘못된 JSON 파일 생성
        input_file = temp_dir / "bad.jsonl"
        with open(input_file, "w") as f:
            f.write("not valid json\n")
        
        mock_adapter = MagicMock()
        error_callback = MagicMock()
        
        watcher = SidecarFileWatcher(
            config=SidecarConfig(watch_dir=str(temp_dir)),
            target_adapter=mock_adapter,
            on_error=error_callback,
        )
        
        watcher._process_files(temp_dir)
        
        error_callback.assert_called_once()

    def test_start_stop(self, temp_dir):
        """시작/정지 테스트."""
        mock_adapter = MagicMock()
        
        watcher = SidecarFileWatcher(
            config=SidecarConfig(
                watch_dir=str(temp_dir),
                poll_interval_seconds=0.1,
            ),
            target_adapter=mock_adapter,
        )
        
        # 백그라운드에서 시작
        thread = threading.Thread(target=watcher.start, daemon=True)
        thread.start()
        
        time.sleep(0.2)
        
        watcher.stop()
        thread.join(timeout=1.0)
        
        assert not thread.is_alive()


# ─────────────────────────────────────────────────────────────
# Factory Tests
# ─────────────────────────────────────────────────────────────


class TestCreateWormAdapter:
    """create_worm_adapter 팩토리 테스트."""

    def test_create_loki_adapter(self, temp_dir):
        """Loki 어댑터 생성."""
        adapter = create_worm_adapter(
            "loki",
            {"endpoint": "http://loki:3100/loki/api/v1/push"},
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        assert isinstance(adapter, LokiAdapter)

    def test_create_http_adapter(self, temp_dir):
        """HTTP 어댑터 생성."""
        adapter = create_worm_adapter(
            "http",
            {"endpoint": "https://logs.example.com/ingest"},
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        assert isinstance(adapter, HTTPWebhookAdapter)

    def test_create_s3_adapter(self, temp_dir):
        """S3 어댑터 생성."""
        adapter = create_worm_adapter(
            "s3",
            {"bucket": "test-bucket"},
            fallback_path=temp_dir / "fallback.jsonl",
        )
        
        assert isinstance(adapter, S3ObjectLockAdapter)

    def test_unknown_type_raises_error(self):
        """알 수 없는 타입은 에러."""
        with pytest.raises(ValueError, match="Unknown adapter type"):
            create_worm_adapter("unknown", {})

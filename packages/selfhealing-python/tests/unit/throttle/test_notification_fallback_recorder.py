"""
알림 전송 실패 폴백 기록기 단위 테스트.

대상: selfhealing/services/throttle/notification_fallback_recorder.py
- NotificationFallbackRecorder.record_failed_notification()
- NotificationFallbackRecorder._write_to_file()
- NotificationFallbackRecorder._write_to_memory()
- NotificationFallbackRecorder.get_pending_notifications()
- NotificationFallbackRecorder.clear_memory_buffer()
"""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import pytest

from selfhealing.services.throttle.notification_fallback_recorder import (
    DEFAULT_FALLBACK_PATH,
)
from tests.unit.throttle.conftest import (
    CRITICAL_RTT_MS,
    DEDUP_KEY_PAYMENT,
    WARNING_RTT_MS,
)


class TestNotificationFallbackRecorderFile:
    """JSONL 파일 기록 테스트."""

    def test_record_single_entry(self, tmp_jsonl_file):
        """단일 실패 알림 JSONL 기록."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=tmp_jsonl_file)
        recorder.record_failed_notification(
            dedup_key=DEDUP_KEY_PAYMENT,
            notification_type="critical",
            event_data={"current_rtt_ms": CRITICAL_RTT_MS},
            error="ConnectionError: Slack API timeout",
        )

        with open(tmp_jsonl_file) as rf:
            entry = json.loads(rf.readline())
            assert entry["dedup_key"] == DEDUP_KEY_PAYMENT
            assert entry["notification_type"] == "critical"
            assert "ConnectionError" in entry["error"]
            assert "timestamp" in entry

    def test_record_multiple_entries(self, tmp_jsonl_file):
        """여러 실패 알림이 JSONL에 줄별로 기록."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=tmp_jsonl_file)

        for i in range(3):
            recorder.record_failed_notification(
                dedup_key=f"key:{i}",
                notification_type="warning",
                event_data={"index": i},
                error=f"Error {i}",
            )

        with open(tmp_jsonl_file) as rf:
            lines = rf.readlines()
            assert len(lines) == 3

            for i, line in enumerate(lines):
                entry = json.loads(line)
                assert entry["dedup_key"] == f"key:{i}"
                assert entry["event_data"]["index"] == i

    def test_event_data_preserved(self, tmp_jsonl_file):
        """event_data가 원본 그대로 저장."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=tmp_jsonl_file)
        original_data = {
            "current_rtt_ms": 450.5,
            "threshold_ms": 200,
            "service_name": "payment",
            "nested": {"key": "value"},
        }

        recorder.record_failed_notification(
            dedup_key="test",
            notification_type="critical",
            event_data=original_data,
            error="test error",
        )

        with open(tmp_jsonl_file) as rf:
            entry = json.loads(rf.readline())
            assert entry["event_data"] == original_data

    def test_timestamp_iso_format(self, tmp_jsonl_file):
        """timestamp가 ISO 형식."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=tmp_jsonl_file)
        recorder.record_failed_notification(
            dedup_key="test",
            notification_type="warning",
            event_data={},
            error="err",
        )

        with open(tmp_jsonl_file) as rf:
            entry = json.loads(rf.readline())
            ts = entry["timestamp"]
            # ISO format 검증: 숫자-숫자-숫자T숫자:숫자:숫자
            assert "T" in ts
            assert "-" in ts
            assert ":" in ts

    def test_directory_auto_creation(self):
        """부모 디렉토리가 없으면 자동 생성."""
        import shutil

        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        tmpdir = tempfile.mkdtemp()
        filepath = os.path.join(tmpdir, "subdir", "nested", "test.jsonl")

        try:
            recorder = NotificationFallbackRecorder(file_path=filepath)
            recorder.record_failed_notification(
                dedup_key="test",
                notification_type="warning",
                event_data={},
                error="err",
            )

            assert os.path.exists(filepath)
            with open(filepath) as rf:
                entry = json.loads(rf.readline())
                assert entry["dedup_key"] == "test"
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestNotificationFallbackRecorderMemory:
    """메모리 버퍼 폴백 테스트."""

    NONEXISTENT_PATH = "/nonexistent/path/test.jsonl"

    def test_fallback_to_memory_on_file_error(self):
        """파일 기록 실패 시 메모리 저장."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=self.NONEXISTENT_PATH)

        with patch.object(recorder, "_write_to_file", return_value=False):
            recorder.record_failed_notification(
                dedup_key="sla:throttle:order",
                notification_type="warning",
                event_data={"current_rtt_ms": WARNING_RTT_MS},
                error="FileError",
            )

        pending = recorder.get_pending_notifications()
        assert len(pending) == 1
        assert pending[0]["dedup_key"] == "sla:throttle:order"

    def test_memory_buffer_max_entries(self):
        """메모리 버퍼 최대 크기 제한 (deque maxlen)."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(
            file_path=self.NONEXISTENT_PATH,
            max_memory_entries=5,
        )

        with patch.object(recorder, "_write_to_file", return_value=False):
            for i in range(10):
                recorder.record_failed_notification(
                    dedup_key=f"key:{i}",
                    notification_type="warning",
                    event_data={},
                    error="err",
                )

        pending = recorder.get_pending_notifications()
        assert len(pending) == 5
        # 최근 5개만 유지 (key:5 ~ key:9)
        assert pending[0]["dedup_key"] == "key:5"
        assert pending[-1]["dedup_key"] == "key:9"

    def test_clear_memory_buffer(self):
        """메모리 버퍼 초기화."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(
            file_path=self.NONEXISTENT_PATH,
            max_memory_entries=10,
        )

        with patch.object(recorder, "_write_to_file", return_value=False):
            recorder.record_failed_notification(
                dedup_key="key:1",
                notification_type="warning",
                event_data={},
                error="err",
            )

        assert len(recorder.get_pending_notifications()) == 1
        recorder.clear_memory_buffer()
        assert len(recorder.get_pending_notifications()) == 0

    def test_get_pending_returns_list(self):
        """get_pending_notifications가 list 타입 반환."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=self.NONEXISTENT_PATH)
        result = recorder.get_pending_notifications()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_memory_entry_structure(self):
        """메모리 버퍼 항목의 구조 확인."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=self.NONEXISTENT_PATH)

        with patch.object(recorder, "_write_to_file", return_value=False):
            recorder.record_failed_notification(
                dedup_key="test:key",
                notification_type="critical",
                event_data={"rtt": CRITICAL_RTT_MS},
                error="Timeout",
            )

        pending = recorder.get_pending_notifications()
        entry = pending[0]
        assert "timestamp" in entry
        assert entry["dedup_key"] == "test:key"
        assert entry["notification_type"] == "critical"
        assert entry["event_data"] == {"rtt": CRITICAL_RTT_MS}
        assert entry["error"] == "Timeout"

    def test_file_write_success_does_not_buffer(self, tmp_jsonl_file):
        """파일 기록 성공 시 메모리 버퍼에 저장하지 않음."""
        from selfhealing.services.throttle.notification_fallback_recorder import (
            NotificationFallbackRecorder,
        )

        recorder = NotificationFallbackRecorder(file_path=tmp_jsonl_file)
        recorder.record_failed_notification(
            dedup_key="test",
            notification_type="warning",
            event_data={},
            error="err",
        )

        # 파일 기록 성공 시 메모리 버퍼는 비어 있어야 함
        assert len(recorder.get_pending_notifications()) == 0

    def test_default_fallback_path(self):
        """기본 폴백 경로 확인."""
        assert DEFAULT_FALLBACK_PATH == "/var/log/selfhealing/notification_fallback.jsonl"

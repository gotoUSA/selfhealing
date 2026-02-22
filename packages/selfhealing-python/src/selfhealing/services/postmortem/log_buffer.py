"""
Incident Log Buffer for Postmortem Timeline Snapshot.

에러/크리티컬 로그를 인메모리 버퍼에 저장하여 Postmortem 생성 시
핵심 에러 로그를 포함할 수 있도록 합니다.

Features:
- 최근 에러 로그 버퍼링 (기본 1000개)
- 기간 기반 로그 조회
- logging.Handler로 자동 연동
- 버퍼 크기 및 TTL 관리
"""

from __future__ import annotations

import structlog
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

logger = structlog.get_logger()


@dataclass
class CapturedLog:
    """캡처된 로그 항목."""

    timestamp: str
    level: str
    message: str
    service: str
    trace_id: str | None = None
    logger_name: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "service": self.service,
            "trace_id": self.trace_id,
            "logger_name": self.logger_name,
        }


class IncidentLogBuffer:
    """
    인시던트 기간 에러 로그를 버퍼링하는 클래스.

    Thread-safe 싱글톤으로 구현되며, logging.Handler와 연동하여
    ERROR 이상 레벨의 로그를 자동으로 캡처합니다.
    """

    _instance: IncidentLogBuffer | None = None
    _lock = threading.Lock()

    def __new__(cls) -> IncidentLogBuffer:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self):
        """초기화."""
        self._buffer: deque[CapturedLog] = deque(maxlen=1000)
        self._buffer_lock = threading.Lock()
        self._max_message_length = 500
        self._ttl_seconds = 3600  # 1시간
        self._default_service = "selfhealing"

    def _get_settings(self):
        """PostmortemSettings에서 설정 로드."""
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            return get_postmortem_settings()
        except ImportError:
            return None

    @property
    def max_log_count(self) -> int:
        """최대 로그 개수."""
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_logs_max_count", 50)
        return 50

    @property
    def max_message_length(self) -> int:
        """로그 메시지 최대 길이."""
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_logs_max_length", 500)
        return 500

    def is_enabled(self) -> bool:
        """로그 수집 활성화 여부."""
        settings = self._get_settings()
        if settings:
            return getattr(settings, "snapshot_logs_enabled", True)
        return True

    def add_log(
        self,
        level: str,
        message: str,
        service: str | None = None,
        trace_id: str | None = None,
        logger_name: str | None = None,
        timestamp: datetime | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        에러 로그를 버퍼에 추가.

        Args:
            level: 로그 레벨 (ERROR, CRITICAL)
            message: 로그 메시지
            service: 서비스명
            trace_id: 추적 ID
            logger_name: 로거 이름
            timestamp: 로그 시각 (None이면 현재 시각)
            extra: 추가 데이터
        """
        if not self.is_enabled():
            return

        # 메시지 길이 제한
        if len(message) > self.max_message_length:
            message = message[: self.max_message_length - 3] + "..."

        # 타임스탬프 처리
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        timestamp_str = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)

        log_entry = CapturedLog(
            timestamp=timestamp_str,
            level=level.upper(),
            message=message,
            service=service or self._default_service,
            trace_id=trace_id,
            logger_name=logger_name,
            extra=extra or {},
        )

        with self._buffer_lock:
            self._buffer.append(log_entry)

    def get_logs_for_period(
        self,
        start: datetime,
        end: datetime,
        max_count: int | None = None,
    ) -> list[dict[str, Any]]:
        """
        특정 기간 내 로그 조회.

        Args:
            start: 시작 시각
            end: 종료 시각
            max_count: 최대 반환 개수 (None이면 설정값 사용)

        Returns:
            로그 목록
        """
        if max_count is None:
            max_count = self.max_log_count

        # datetime을 ISO 문자열로 변환하여 비교
        start_str = start.isoformat() if isinstance(start, datetime) else str(start)
        end_str = end.isoformat() if isinstance(end, datetime) else str(end)

        with self._buffer_lock:
            filtered = [log.to_dict() for log in self._buffer if start_str <= log.timestamp <= end_str]

        # 최신 로그 우선
        filtered.sort(key=lambda x: x["timestamp"], reverse=True)
        return filtered[:max_count]

    def get_recent_logs(self, count: int | None = None) -> list[dict[str, Any]]:
        """
        최근 로그 조회.

        Args:
            count: 반환할 로그 개수 (None이면 설정값 사용)

        Returns:
            최근 로그 목록
        """
        if count is None:
            count = self.max_log_count

        with self._buffer_lock:
            logs = list(self._buffer)

        # 최신 로그 우선
        logs.sort(key=lambda x: x.timestamp, reverse=True)
        return [log.to_dict() for log in logs[:count]]

    def clear_old_logs(self, before: datetime | None = None) -> int:
        """
        오래된 로그 정리.

        Args:
            before: 이 시각 이전 로그 삭제 (None이면 TTL 기준)

        Returns:
            삭제된 로그 개수
        """
        if before is None:
            before = datetime.now(timezone.utc) - timedelta(seconds=self._ttl_seconds)

        before_str = before.isoformat()

        with self._buffer_lock:
            original_count = len(self._buffer)
            self._buffer = deque(
                (log for log in self._buffer if log.timestamp >= before_str),
                maxlen=self._buffer.maxlen,
            )
            return original_count - len(self._buffer)

    def clear(self) -> None:
        """버퍼 전체 삭제."""
        with self._buffer_lock:
            self._buffer.clear()

    def get_buffer_size(self) -> int:
        """현재 버퍼 크기."""
        with self._buffer_lock:
            return len(self._buffer)


class IncidentLogHandler(logging.Handler):
    """
    IncidentLogBuffer와 연동되는 logging.Handler.

    ERROR 이상 레벨의 로그를 자동으로 IncidentLogBuffer에 저장합니다.
    """

    def __init__(
        self,
        level: int = logging.ERROR,
        service_name: str = "selfhealing",
    ):
        """
        핸들러 초기화.

        Args:
            level: 최소 로그 레벨 (기본 ERROR)
            service_name: 서비스 이름
        """
        super().__init__(level=level)
        self._service_name = service_name
        self._buffer = get_incident_log_buffer()

    def emit(self, record: logging.LogRecord) -> None:
        """로그 레코드 처리."""
        try:
            # 메시지 포맷팅
            message = self.format(record)

            # trace_id 추출 (record에서)
            trace_id = getattr(record, "trace_id", None)
            if trace_id is None:
                trace_id = getattr(record, "correlation_id", None)

            # 타임스탬프
            timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc)

            # 버퍼에 추가
            self._buffer.add_log(
                level=record.levelname,
                message=message,
                service=self._service_name,
                trace_id=trace_id,
                logger_name=record.name,
                timestamp=timestamp,
                extra={
                    "filename": record.filename,
                    "lineno": record.lineno,
                    "funcName": record.funcName,
                },
            )

        except Exception:
            # 로깅 시스템 내부 오류는 무시
            self.handleError(record)


# =============================================================================
# Singleton & Factory
# =============================================================================

_log_buffer: IncidentLogBuffer | None = None


def get_incident_log_buffer() -> IncidentLogBuffer:
    """IncidentLogBuffer 싱글톤 반환."""
    global _log_buffer
    if _log_buffer is None:
        _log_buffer = IncidentLogBuffer()
    return _log_buffer


def reset_incident_log_buffer() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _log_buffer
    if _log_buffer is not None:
        _log_buffer.clear()
    _log_buffer = None
    IncidentLogBuffer._instance = None


def setup_incident_log_handler(
    service_name: str = "selfhealing",
    level: int = logging.ERROR,
    logger_name: str | None = None,
) -> IncidentLogHandler:
    """
    IncidentLogHandler를 로거에 추가.

    Args:
        service_name: 서비스 이름
        level: 최소 로그 레벨
        logger_name: 로거 이름 (None이면 루트 로거)

    Returns:
        추가된 핸들러
    """
    handler = IncidentLogHandler(level=level, service_name=service_name)

    if logger_name:
        target_logger = logging.getLogger(logger_name)
    else:
        target_logger = logging.getLogger()

    # 중복 핸들러 방지
    for existing in target_logger.handlers:
        if isinstance(existing, IncidentLogHandler):
            return existing

    target_logger.addHandler(handler)
    return handler


__all__ = [
    "IncidentLogBuffer",
    "IncidentLogHandler",
    "CapturedLog",
    "get_incident_log_buffer",
    "reset_incident_log_buffer",
    "setup_incident_log_handler",
]

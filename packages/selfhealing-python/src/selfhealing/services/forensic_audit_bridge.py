"""
Forensic-Audit 브릿지 모듈.

Forensic 캡처 이벤트를 Audit 시스템에 연결합니다.

민감정보 마스킹 연동:
- mask_sensitive_fields() 사용하여 실제 마스킹 수행
- ForensicSettings.sensitive_field_patterns 연동

Forensic Rate Limiter (SlidingWindow):
- 분당 최대 10건의 예외 캡처
- 분당 최대 1건의 메모리 스냅샷
- 에러 폭풍 시 Audit 파일 비대화 방지
"""

from __future__ import annotations

import threading
import time
from typing import Any

import structlog

logger = structlog.get_logger()

# 기본 민감 필드 패턴 (ForensicSettings가 없을 때 사용)
DEFAULT_SENSITIVE_PATTERNS = [
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "credit_card",
    "ssn",
    "social_security",
]


# =============================================================================
# Forensic Rate Limiter (SlidingWindow)
# =============================================================================


class ForensicRateLimiter:
    """
    Forensic 이벤트 Rate Limiter.

    Sliding Window 알고리즘:
    - 분당 최대 10건의 예외 캡처
    - 분당 최대 1건의 메모리 스냅샷
    - 정확한 윈도우 기반 제어 (버스트 불허)

    기존 코드 참조: services/throttle/base.py::SlidingWindowThrottle
    """

    def __init__(
        self,
        exception_limit: int = 10,
        snapshot_limit: int = 1,
        anomaly_limit: int = 5,
        window_seconds: float = 60.0,
    ):
        """
        ForensicRateLimiter 초기화.

        Args:
            exception_limit: 분당 예외 캡처 최대 횟수
            snapshot_limit: 분당 메모리 스냅샷 최대 횟수
            anomaly_limit: 분당 이상 탐지 최대 횟수
            window_seconds: 윈도우 크기 (초)
        """
        self._exception_limit = exception_limit
        self._snapshot_limit = snapshot_limit
        self._anomaly_limit = anomaly_limit
        self._window_seconds = window_seconds

        # Sliding Window: 타임스탬프 리스트
        self._exception_timestamps: list[float] = []
        self._snapshot_timestamps: list[float] = []
        self._anomaly_timestamps: list[float] = []
        self._lock = threading.Lock()

        # 통계
        self._exceptions_dropped = 0
        self._snapshots_dropped = 0
        self._anomalies_dropped = 0

    def _cleanup_old_timestamps(
        self, timestamps: list[float], now: float
    ) -> list[float]:
        """윈도우 밖의 오래된 타임스탬프 제거."""
        cutoff = now - self._window_seconds
        return [ts for ts in timestamps if ts > cutoff]

    def try_acquire_exception(self) -> bool:
        """
        예외 캡처 허용 여부.

        Returns:
            True if allowed, False if rate limited
        """
        with self._lock:
            now = time.time()
            self._exception_timestamps = self._cleanup_old_timestamps(
                self._exception_timestamps, now
            )

            if len(self._exception_timestamps) < self._exception_limit:
                self._exception_timestamps.append(now)
                return True

            self._exceptions_dropped += 1
            return False

    def try_acquire_snapshot(self) -> bool:
        """
        스냅샷 캡처 허용 여부.

        Returns:
            True if allowed, False if rate limited
        """
        with self._lock:
            now = time.time()
            self._snapshot_timestamps = self._cleanup_old_timestamps(
                self._snapshot_timestamps, now
            )

            if len(self._snapshot_timestamps) < self._snapshot_limit:
                self._snapshot_timestamps.append(now)
                return True

            self._snapshots_dropped += 1
            return False

    def try_acquire_anomaly(self) -> bool:
        """
        이상 탐지 캡처 허용 여부.

        Returns:
            True if allowed, False if rate limited
        """
        with self._lock:
            now = time.time()
            self._anomaly_timestamps = self._cleanup_old_timestamps(
                self._anomaly_timestamps, now
            )

            if len(self._anomaly_timestamps) < self._anomaly_limit:
                self._anomaly_timestamps.append(now)
                return True

            self._anomalies_dropped += 1
            return False

    def get_stats(self) -> dict[str, Any]:
        """Rate limiter 통계."""
        with self._lock:
            now = time.time()
            exception_ts = self._cleanup_old_timestamps(self._exception_timestamps, now)
            snapshot_ts = self._cleanup_old_timestamps(self._snapshot_timestamps, now)
            anomaly_ts = self._cleanup_old_timestamps(self._anomaly_timestamps, now)
            return {
                "exception_requests_in_window": len(exception_ts),
                "exception_limit": self._exception_limit,
                "snapshot_requests_in_window": len(snapshot_ts),
                "snapshot_limit": self._snapshot_limit,
                "anomaly_requests_in_window": len(anomaly_ts),
                "anomaly_limit": self._anomaly_limit,
                "exceptions_dropped": self._exceptions_dropped,
                "snapshots_dropped": self._snapshots_dropped,
                "anomalies_dropped": self._anomalies_dropped,
                "window_seconds": self._window_seconds,
            }

    def reset(self) -> None:
        """Rate limiter 상태 리셋 (테스트용)."""
        with self._lock:
            self._exception_timestamps.clear()
            self._snapshot_timestamps.clear()
            self._anomaly_timestamps.clear()
            self._exceptions_dropped = 0
            self._snapshots_dropped = 0
            self._anomalies_dropped = 0


# 기본 Rate Limiter 인스턴스 (싱글톤)
_default_rate_limiter: ForensicRateLimiter | None = None


def get_forensic_rate_limiter() -> ForensicRateLimiter:
    """
    기본 ForensicRateLimiter 싱글톤 인스턴스 반환.

    Returns:
        ForensicRateLimiter 인스턴스
    """
    global _default_rate_limiter
    if _default_rate_limiter is None:
        _default_rate_limiter = ForensicRateLimiter()
    return _default_rate_limiter


class ForensicAuditBridge:
    """
    Forensic 캡처를 Audit 시스템에 연결.

    Forensic 캡처 시점:
    1. 예외 발생 시 스택 트레이스 캡처
    2. 주기적 메모리 스냅샷
    3. 이상 패턴 감지 시 컨텍스트 캡처

    개선 사항:
    - 실제 민감정보 마스킹 수행 (GDPR/ISMS 준수)
    - ForensicSettings.sensitive_field_patterns 연동

    추가 개선:
    - Rate Limiter 통합 (에러 폭풍 방지)
    - SlidingWindow 알고리즘으로 분당 제한
    """

    def __init__(
        self,
        audit_adapter=None,
        sensitive_patterns: list[str] | None = None,
        rate_limiter: ForensicRateLimiter | None = None,
    ):
        """
        ForensicAuditBridge 초기화.

        Args:
            audit_adapter: Audit 어댑터 (이벤트 기록용)
            sensitive_patterns: 민감 필드 패턴 목록 (None이면 기본값 사용)
            rate_limiter: Rate Limiter (None이면 기본값 사용)
        """
        self._audit_adapter = audit_adapter
        self._sensitive_patterns = sensitive_patterns
        self._rate_limiter = rate_limiter or get_forensic_rate_limiter()

    def _get_sensitive_patterns(self) -> list[str]:
        """
        민감 필드 패턴 목록 반환.

        우선순위:
        1. 생성자에서 주입된 패턴
        2. ForensicSettings.sensitive_field_patterns
        3. 기본 패턴
        """
        if self._sensitive_patterns is not None:
            return self._sensitive_patterns

        # ForensicSettings에서 패턴 가져오기 시도
        try:
            from selfhealing.config import get_forensic_settings

            settings = get_forensic_settings()
            if (
                hasattr(settings, "sensitive_field_patterns")
                and settings.sensitive_field_patterns
            ):
                return list(settings.sensitive_field_patterns)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(
                "forensic_audit_bridge.failed_get_forensicsettings",
                error=e,
            )

        return DEFAULT_SENSITIVE_PATTERNS

    def _mask_context(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        컨텍스트의 민감 정보 마스킹.

        masking.py의 mask_sensitive_fields() 사용

        Args:
            context: 원본 컨텍스트

        Returns:
            마스킹된 컨텍스트
        """
        try:
            from selfhealing.audit.masking import mask_sensitive_fields

            patterns = self._get_sensitive_patterns()
            return mask_sensitive_fields(context, patterns)
        except ImportError:
            logger.debug(
                "[ForensicAuditBridge] masking module not available, using fallback"
            )
            return self._fallback_mask(context)
        except Exception as e:
            logger.debug(
                "forensic_audit_bridge.masking_failed",
                error=e,
            )
            return self._fallback_mask(context)

    def _fallback_mask(self, context: dict[str, Any]) -> dict[str, Any]:
        """mask_sensitive_fields 사용 불가 시 폴백 마스킹."""
        patterns = self._get_sensitive_patterns()
        result = {}
        for key, value in context.items():
            key_lower = key.lower()
            is_sensitive = any(p in key_lower for p in patterns)
            if is_sensitive:
                result[key] = "***REDACTED***"
            elif isinstance(value, dict):
                result[key] = self._fallback_mask(value)
            else:
                result[key] = value
        return result

    def on_exception_captured(
        self,
        exception: Exception,
        stack_trace: str,
        context: dict[str, Any],
        sanitized: bool = True,
    ) -> bool:
        """
        예외 캡처 시 Audit 기록.

        개선: sanitized=True일 때 실제 마스킹 수행
        추가 개선: Rate Limiter 적용

        Args:
            exception: 캡처된 예외
            stack_trace: 스택 트레이스 (ForensicSettings.max_stack_depth 적용됨)
            context: 실행 컨텍스트
            sanitized: 민감 정보 마스킹 여부

        Returns:
            True if recorded, False if rate limited
        """
        # Rate Limiting 적용
        if not self._rate_limiter.try_acquire_exception():
            logger.debug("forensic_audit_bridge.exception_capture_rate_limited")
            return False

        # 실제 마스킹 수행
        masked_context = context
        if sanitized:
            masked_context = self._mask_context(context)

        self._record_audit(
            event_type="FORENSIC_CAPTURE_COMPLETED",
            details={
                "exception_type": type(exception).__name__,
                "exception_message": str(exception)[:500],
                "stack_depth": len(stack_trace.split("\n")),
                "context_keys": list(masked_context.keys()),
                "sanitized": sanitized,
                "capture_reason": "exception",
            },
        )
        return True

    def on_anomaly_detected(
        self,
        anomaly_type: str,
        score: float,
        threshold: float,
        context: dict[str, Any],
    ) -> bool:
        """
        이상 패턴 감지 시 Audit 기록.

        개선: Rate Limiter 적용

        Args:
            anomaly_type: 이상 유형 (statistical, behavioral 등)
            score: 이상 점수
            threshold: 감지 임계값
            context: 관련 컨텍스트

        Returns:
            True if recorded, False if rate limited
        """
        # Rate Limiting 적용
        if not self._rate_limiter.try_acquire_anomaly():
            logger.debug("forensic_audit_bridge.anomaly_capture_rate_limited")
            return False

        self._record_audit(
            event_type="FORENSIC_ANOMALY_DETECTED",
            details={
                "anomaly_type": anomaly_type,
                "score": score,
                "threshold": threshold,
                "exceeded_by": score - threshold,
                "context_summary": self._summarize_context(context),
            },
        )
        return True

    def on_memory_snapshot(
        self,
        snapshot_id: str,
        memory_mb: float,
        object_count: int,
    ) -> bool:
        """
        메모리 스냅샷 시 Audit 기록 (설정에서 활성화된 경우).

        개선: Rate Limiter 적용

        Args:
            snapshot_id: 스냅샷 ID
            memory_mb: 메모리 사용량 (MB)
            object_count: 객체 수

        Returns:
            True if recorded, False if rate limited
        """
        # Rate Limiting 적용
        if not self._rate_limiter.try_acquire_snapshot():
            logger.debug("forensic_audit_bridge.snapshot_capture_rate_limited")
            return False

        self._record_audit(
            event_type="FORENSIC_CAPTURE_STARTED",
            details={
                "capture_type": "memory_snapshot",
                "snapshot_id": snapshot_id,
                "memory_mb": memory_mb,
                "object_count": object_count,
            },
        )
        return True

    def get_rate_limiter_stats(self) -> dict[str, Any]:
        """Rate Limiter 통계 반환."""
        return self._rate_limiter.get_stats()

    def _summarize_context(self, context: dict[str, Any]) -> dict[str, str]:
        """컨텍스트 요약 (크기 제한)."""
        summary = {}
        for key, value in context.items():
            value_str = str(value)
            if len(value_str) > 100:
                summary[key] = f"{value_str[:100]}... (truncated)"
            else:
                summary[key] = value_str
        return summary

    def _record_audit(self, event_type: str, details: dict[str, Any]) -> None:
        """Audit 시스템에 기록."""
        if self._audit_adapter:
            try:
                self._audit_adapter.log_event(
                    event_type=event_type,
                    source="ForensicCapture",
                    details=details,
                )
            except Exception as e:
                logger.debug(
                    "forensic_audit_bridge.audit_recording_failed",
                    error=e,
                )


# Singleton 패턴
_bridge_instance: ForensicAuditBridge | None = None


def get_forensic_audit_bridge(audit_adapter=None) -> ForensicAuditBridge:
    """
    ForensicAuditBridge 싱글톤 인스턴스 반환.

    Args:
        audit_adapter: Audit 어댑터 (최초 호출 시에만 사용)

    Returns:
        ForensicAuditBridge 인스턴스
    """
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ForensicAuditBridge(audit_adapter)
    return _bridge_instance

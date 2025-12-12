"""
TLS/SSL Error Handler

Handles TLS-related failures gracefully:
- Certificate expiration
- Certificate validation failures
- Handshake timeouts
- Protocol version mismatches

Provides:
- Error classification
- Retry strategies for transient failures
- Alert mechanisms for certificate issues

Reference: docs/STAGE_25_TLS_FAILURE.md
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable, Any, TypeVar
import ssl
import logging

logger = logging.getLogger(__name__)

T = TypeVar("T")


class TLSErrorType(str, Enum):
    """Classification of TLS errors"""

    CERTIFICATE_EXPIRED = "cert_expired"
    CERTIFICATE_NOT_YET_VALID = "cert_not_yet_valid"
    CERTIFICATE_REVOKED = "cert_revoked"
    CERTIFICATE_HOSTNAME_MISMATCH = "cert_hostname_mismatch"
    CERTIFICATE_SELF_SIGNED = "cert_self_signed"
    CERTIFICATE_CHAIN_INVALID = "cert_chain_invalid"
    HANDSHAKE_TIMEOUT = "handshake_timeout"
    HANDSHAKE_FAILURE = "handshake_failure"
    PROTOCOL_VERSION_MISMATCH = "protocol_mismatch"
    CONNECTION_RESET = "connection_reset"
    UNKNOWN = "unknown"


class TLSErrorSeverity(str, Enum):
    """Severity level for TLS errors"""

    CRITICAL = "critical"  # 즉시 알림 필요 (인증서 만료)
    HIGH = "high"  # 조속한 조치 필요
    MEDIUM = "medium"  # 모니터링 필요
    LOW = "low"  # 일시적, 재시도로 해결 가능


@dataclass
class TLSErrorInfo:
    """Detailed TLS error information"""

    error_type: TLSErrorType
    severity: TLSErrorSeverity
    endpoint: str
    error_message: str
    is_retryable: bool
    detected_at: datetime
    certificate_expiry: Optional[datetime] = None
    days_until_expiry: Optional[int] = None
    recommended_action: str = ""

    @property
    def is_certificate_error(self) -> bool:
        """True if this is a certificate-related error."""
        return self.error_type in {
            TLSErrorType.CERTIFICATE_EXPIRED,
            TLSErrorType.CERTIFICATE_NOT_YET_VALID,
            TLSErrorType.CERTIFICATE_REVOKED,
            TLSErrorType.CERTIFICATE_HOSTNAME_MISMATCH,
            TLSErrorType.CERTIFICATE_SELF_SIGNED,
            TLSErrorType.CERTIFICATE_CHAIN_INVALID,
        }

    @property
    def requires_immediate_action(self) -> bool:
        """True if this requires immediate attention."""
        return self.severity in {TLSErrorSeverity.CRITICAL, TLSErrorSeverity.HIGH}


class TLSErrorClassifier:
    """Classifies TLS/SSL errors"""

    @staticmethod
    def classify(error: Exception, endpoint: str = "") -> TLSErrorInfo:
        """
        Classify an SSL/TLS error.

        Args:
            error: The exception to classify
            endpoint: The endpoint URL where error occurred

        Returns:
            TLSErrorInfo with classification details
        """
        error_str = str(error).lower()
        now = datetime.now(timezone.utc)

        # Certificate expired
        if "certificate has expired" in error_str or "cert_has_expired" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_EXPIRED,
                severity=TLSErrorSeverity.CRITICAL,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Renew certificate immediately",
            )

        # Certificate not yet valid
        if "certificate is not yet valid" in error_str or "cert_not_yet_valid" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_NOT_YET_VALID,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check system clock or certificate dates",
            )

        # Certificate revoked
        if "certificate revoked" in error_str or "cert_revoked" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_REVOKED,
                severity=TLSErrorSeverity.CRITICAL,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Obtain new certificate - current one is revoked",
            )

        # Hostname mismatch
        if "hostname" in error_str and ("mismatch" in error_str or "doesn't match" in error_str):
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_HOSTNAME_MISMATCH,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check endpoint URL and certificate SAN",
            )

        # Self-signed certificate
        if "self signed" in error_str or "self-signed" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_SELF_SIGNED,
                severity=TLSErrorSeverity.MEDIUM,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Use CA-signed certificate or add to trust store",
            )

        # Certificate chain invalid
        if "certificate chain" in error_str or "unable to get local issuer certificate" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_CHAIN_INVALID,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check intermediate certificates in chain",
            )

        # Handshake timeout
        if "handshake" in error_str and ("timeout" in error_str or "timed out" in error_str):
            return TLSErrorInfo(
                error_type=TLSErrorType.HANDSHAKE_TIMEOUT,
                severity=TLSErrorSeverity.MEDIUM,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=True,
                detected_at=now,
                recommended_action="Check network connectivity and firewall",
            )

        # Connection reset
        if "connection reset" in error_str or "econnreset" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CONNECTION_RESET,
                severity=TLSErrorSeverity.MEDIUM,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=True,
                detected_at=now,
                recommended_action="Retry with backoff",
            )

        # Protocol version mismatch
        if "protocol" in error_str and ("version" in error_str or "unsupported" in error_str):
            return TLSErrorInfo(
                error_type=TLSErrorType.PROTOCOL_VERSION_MISMATCH,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check TLS version compatibility",
            )

        # Handshake failure (generic)
        if "handshake" in error_str and "fail" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.HANDSHAKE_FAILURE,
                severity=TLSErrorSeverity.MEDIUM,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=True,
                detected_at=now,
                recommended_action="Check TLS configuration and cipher suites",
            )

        # Default: unknown
        return TLSErrorInfo(
            error_type=TLSErrorType.UNKNOWN,
            severity=TLSErrorSeverity.MEDIUM,
            endpoint=endpoint,
            error_message=str(error),
            is_retryable=True,
            detected_at=now,
            recommended_action="Investigate error details",
        )


class TLSResilientClient(ABC):
    """Abstract base for TLS-resilient HTTP client wrapper"""

    @abstractmethod
    def request(self, method: str, url: str, **kwargs) -> Any:
        """Make HTTP request with TLS error handling"""
        pass

    @abstractmethod
    def on_tls_error(self, error_info: TLSErrorInfo) -> None:
        """Callback when TLS error occurs"""
        pass


class SimpleTLSResilientClient(TLSResilientClient):
    """
    Simple implementation of TLS-resilient client.
    Wraps any HTTP client and adds TLS error handling.
    """

    def __init__(
        self,
        http_client: Any,  # requests.Session, httpx.Client, etc.
        error_callback: Optional[Callable[[TLSErrorInfo], None]] = None,
        max_retries: int = 3,
    ):
        """
        Initialize TLS-resilient client.

        Args:
            http_client: HTTP client with request(method, url, **kwargs) method
            error_callback: Optional callback for TLS errors
            max_retries: Maximum retry attempts for retryable errors
        """
        self._client = http_client
        self._error_callback = error_callback
        self._max_retries = max_retries
        self._classifier = TLSErrorClassifier()

    def request(self, method: str, url: str, **kwargs) -> Any:
        """Make request with TLS error handling and retry."""
        last_error: Optional[Exception] = None

        for attempt in range(self._max_retries):
            try:
                return self._client.request(method, url, **kwargs)
            except ssl.SSLError as e:
                error_info = self._classifier.classify(e, url)
                self.on_tls_error(error_info)
                logger.warning(f"TLS error (attempt {attempt + 1}): {error_info.error_type}")

                if not error_info.is_retryable:
                    raise

                last_error = e
            except Exception as e:
                # Check if it's a wrapped SSL error
                error_name = str(type(e).__name__).lower()
                error_msg = str(e).lower()

                if "ssl" in error_name or "ssl" in error_msg or "tls" in error_msg:
                    error_info = self._classifier.classify(e, url)
                    self.on_tls_error(error_info)
                    logger.warning(f"TLS error (attempt {attempt + 1}): {error_info.error_type}")

                    if not error_info.is_retryable:
                        raise

                    last_error = e
                else:
                    raise

        if last_error:
            raise last_error

        # Should not reach here
        raise RuntimeError("No result after retries")  # pragma: no cover

    def on_tls_error(self, error_info: TLSErrorInfo) -> None:
        """Handle TLS error via callback."""
        if self._error_callback:
            try:
                self._error_callback(error_info)
            except Exception as e:
                logger.error(f"Error in TLS error callback: {e}")

    @property
    def client(self) -> Any:
        """Get the underlying HTTP client."""
        return self._client

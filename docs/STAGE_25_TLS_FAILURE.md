# Stage 25: Certificate 만료 / TLS Handshake 실패 테스트

## 🎯 목표

외부 API 연동 시 TLS/SSL 관련 장애에 대한 복원력 확보

## 📋 실제 장애 사례

- **2024년 네이버페이 3시간 장애**: Let's Encrypt 갱신 실패 → 외부 PG 전부 502

---

## 🏗️ 구현 내용

### 1. TLS Error Handler

**파일**: `packages/selfhealing-python/src/selfhealing/core/tls_handler.py`

```python
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
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Callable, Any
import ssl


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
    CRITICAL = "critical"     # 즉시 알림 필요 (인증서 만료)
    HIGH = "high"             # 조속한 조치 필요
    MEDIUM = "medium"         # 모니터링 필요
    LOW = "low"               # 일시적, 재시도로 해결 가능


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


class TLSErrorClassifier:
    """Classifies TLS/SSL errors"""

    @staticmethod
    def classify(error: Exception, endpoint: str = "") -> TLSErrorInfo:
        """Classify an SSL/TLS error"""
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
                recommended_action="Renew certificate immediately"
            )

        # Certificate not yet valid
        if "certificate is not yet valid" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.CERTIFICATE_NOT_YET_VALID,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check system clock or certificate dates"
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
                recommended_action="Check endpoint URL and certificate SAN"
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
                recommended_action="Use CA-signed certificate or add to trust store"
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
                recommended_action="Check network connectivity and firewall"
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
                recommended_action="Retry with backoff"
            )

        # Protocol version mismatch
        if "protocol" in error_str or "version" in error_str:
            return TLSErrorInfo(
                error_type=TLSErrorType.PROTOCOL_VERSION_MISMATCH,
                severity=TLSErrorSeverity.HIGH,
                endpoint=endpoint,
                error_message=str(error),
                is_retryable=False,
                detected_at=now,
                recommended_action="Check TLS version compatibility"
            )

        # Default: unknown
        return TLSErrorInfo(
            error_type=TLSErrorType.UNKNOWN,
            severity=TLSErrorSeverity.MEDIUM,
            endpoint=endpoint,
            error_message=str(error),
            is_retryable=True,
            detected_at=now,
            recommended_action="Investigate error details"
        )


class TLSResilientClient(ABC):
    """Abstract base for TLS-resilient HTTP client wrapper"""

    @abstractmethod
    def request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Any:
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
        self._client = http_client
        self._error_callback = error_callback
        self._max_retries = max_retries
        self._classifier = TLSErrorClassifier()

    def request(self, method: str, url: str, **kwargs) -> Any:
        last_error = None

        for attempt in range(self._max_retries):
            try:
                return self._client.request(method, url, **kwargs)
            except ssl.SSLError as e:
                error_info = self._classifier.classify(e, url)
                self.on_tls_error(error_info)

                if not error_info.is_retryable:
                    raise

                last_error = e
            except Exception as e:
                # Check if it's a wrapped SSL error
                if "ssl" in str(type(e).__name__).lower() or "ssl" in str(e).lower():
                    error_info = self._classifier.classify(e, url)
                    self.on_tls_error(error_info)

                    if not error_info.is_retryable:
                        raise

                    last_error = e
                else:
                    raise

        if last_error:
            raise last_error

    def on_tls_error(self, error_info: TLSErrorInfo) -> None:
        if self._error_callback:
            self._error_callback(error_info)
```

---

### 2. Certificate Expiry Monitor

**파일**: `packages/selfhealing-python/src/selfhealing/core/cert_monitor.py`

```python
"""
Certificate Expiry Monitor

Proactive monitoring of certificate expiration.
Can be run as scheduled task to alert before expiry.
"""

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from enum import Enum


class CertificateStatus(str, Enum):
    """Certificate validity status"""
    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"   # < 30 days
    CRITICAL = "critical"              # < 7 days
    EXPIRED = "expired"


@dataclass
class CertificateInfo:
    """Certificate information"""
    endpoint: str
    subject: str
    issuer: str
    not_before: datetime
    not_after: datetime
    status: CertificateStatus
    days_remaining: int
    checked_at: datetime


class CertificateExpiryMonitor:
    """Monitor certificate expiration for endpoints"""

    def __init__(
        self,
        warning_days: int = 30,
        critical_days: int = 7,
    ):
        self._warning_days = warning_days
        self._critical_days = critical_days

    def check_expiry(
        self,
        not_after: datetime,
        endpoint: str = "",
        subject: str = "",
        issuer: str = "",
        not_before: Optional[datetime] = None,
    ) -> CertificateInfo:
        """Check certificate expiry status"""
        now = datetime.now(timezone.utc)

        # Ensure timezone aware
        if not_after.tzinfo is None:
            not_after = not_after.replace(tzinfo=timezone.utc)

        days_remaining = (not_after - now).days

        if days_remaining < 0:
            status = CertificateStatus.EXPIRED
        elif days_remaining <= self._critical_days:
            status = CertificateStatus.CRITICAL
        elif days_remaining <= self._warning_days:
            status = CertificateStatus.EXPIRING_SOON
        else:
            status = CertificateStatus.VALID

        return CertificateInfo(
            endpoint=endpoint,
            subject=subject,
            issuer=issuer,
            not_before=not_before or now,
            not_after=not_after,
            status=status,
            days_remaining=max(0, days_remaining),
            checked_at=now,
        )

    def get_status_message(self, cert_info: CertificateInfo) -> str:
        """Get human-readable status message"""
        if cert_info.status == CertificateStatus.EXPIRED:
            return f"CRITICAL: Certificate for {cert_info.endpoint} has EXPIRED!"
        elif cert_info.status == CertificateStatus.CRITICAL:
            return f"CRITICAL: Certificate for {cert_info.endpoint} expires in {cert_info.days_remaining} days!"
        elif cert_info.status == CertificateStatus.EXPIRING_SOON:
            return f"WARNING: Certificate for {cert_info.endpoint} expires in {cert_info.days_remaining} days"
        else:
            return f"OK: Certificate for {cert_info.endpoint} valid for {cert_info.days_remaining} days"
```

---

### 3. 테스트 케이스

**파일**: `packages/selfhealing-python/tests/unit/test_tls_failure.py`

```python
"""
Stage 25: TLS/Certificate Failure Tests

Scenarios:
1. Certificate expired
2. Certificate not yet valid
3. Hostname mismatch
4. Self-signed certificate
5. Handshake timeout (retryable)
6. Connection reset (retryable)
"""

import pytest
import ssl
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock

from selfhealing.core.tls_handler import (
    TLSErrorType,
    TLSErrorSeverity,
    TLSErrorClassifier,
    SimpleTLSResilientClient,
)
from selfhealing.core.cert_monitor import (
    CertificateStatus,
    CertificateExpiryMonitor,
)


class TestTLSErrorClassification:
    """TLS 에러 분류 테스트"""

    def test_classify_certificate_expired(self):
        """만료된 인증서 에러 분류"""
        error = ssl.SSLError("certificate has expired")

        info = TLSErrorClassifier.classify(error, "https://api.example.com")

        assert info.error_type == TLSErrorType.CERTIFICATE_EXPIRED
        assert info.severity == TLSErrorSeverity.CRITICAL
        assert info.is_retryable is False

    def test_classify_certificate_not_yet_valid(self):
        """아직 유효하지 않은 인증서"""
        error = ssl.SSLError("certificate is not yet valid")

        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_NOT_YET_VALID
        assert info.severity == TLSErrorSeverity.HIGH

    def test_classify_hostname_mismatch(self):
        """호스트명 불일치"""
        error = ssl.SSLError("hostname 'api.example.com' doesn't match")

        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_HOSTNAME_MISMATCH

    def test_classify_self_signed(self):
        """Self-signed 인증서"""
        error = ssl.SSLError("self signed certificate in certificate chain")

        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CERTIFICATE_SELF_SIGNED

    def test_classify_handshake_timeout_retryable(self):
        """Handshake timeout은 재시도 가능"""
        error = ssl.SSLError("handshake operation timed out")

        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.HANDSHAKE_TIMEOUT
        assert info.is_retryable is True

    def test_classify_connection_reset_retryable(self):
        """Connection reset은 재시도 가능"""
        error = Exception("Connection reset by peer")

        info = TLSErrorClassifier.classify(error)

        assert info.error_type == TLSErrorType.CONNECTION_RESET
        assert info.is_retryable is True


class TestCertificateExpiryMonitor:
    """인증서 만료 모니터링 테스트"""

    def test_certificate_valid(self):
        """유효한 인증서 (30일 이상 남음)"""
        monitor = CertificateExpiryMonitor()
        future = datetime.now(timezone.utc) + timedelta(days=90)

        info = monitor.check_expiry(future, "api.example.com")

        assert info.status == CertificateStatus.VALID
        assert info.days_remaining == 90

    def test_certificate_expiring_soon(self):
        """곧 만료될 인증서 (30일 이내)"""
        monitor = CertificateExpiryMonitor()
        future = datetime.now(timezone.utc) + timedelta(days=15)

        info = monitor.check_expiry(future)

        assert info.status == CertificateStatus.EXPIRING_SOON

    def test_certificate_critical(self):
        """긴급한 인증서 (7일 이내)"""
        monitor = CertificateExpiryMonitor()
        future = datetime.now(timezone.utc) + timedelta(days=3)

        info = monitor.check_expiry(future)

        assert info.status == CertificateStatus.CRITICAL

    def test_certificate_expired(self):
        """이미 만료된 인증서"""
        monitor = CertificateExpiryMonitor()
        past = datetime.now(timezone.utc) - timedelta(days=1)

        info = monitor.check_expiry(past)

        assert info.status == CertificateStatus.EXPIRED


class TestTLSResilientClient:
    """TLS 복원력 클라이언트 테스트"""

    def test_successful_request(self):
        """정상 요청"""
        mock_client = Mock()
        mock_client.request.return_value = {"status": "ok"}

        client = SimpleTLSResilientClient(mock_client)
        result = client.request("GET", "https://api.example.com")

        assert result == {"status": "ok"}

    def test_retry_on_transient_error(self):
        """일시적 에러 시 재시도"""
        mock_client = Mock()
        mock_client.request.side_effect = [
            ssl.SSLError("handshake operation timed out"),
            {"status": "ok"}
        ]

        error_callback = Mock()
        client = SimpleTLSResilientClient(mock_client, error_callback=error_callback)

        result = client.request("GET", "https://api.example.com")

        assert result == {"status": "ok"}
        assert error_callback.called

    def test_no_retry_on_cert_expired(self):
        """인증서 만료는 재시도 안 함"""
        mock_client = Mock()
        mock_client.request.side_effect = ssl.SSLError("certificate has expired")

        client = SimpleTLSResilientClient(mock_client)

        with pytest.raises(ssl.SSLError):
            client.request("GET", "https://api.example.com")

        # 한 번만 호출됨 (재시도 없음)
        assert mock_client.request.call_count == 1
```

---

## 📁 파일 생성 순서

1. `packages/selfhealing-python/src/selfhealing/core/tls_handler.py`
2. `packages/selfhealing-python/src/selfhealing/core/cert_monitor.py`
3. `packages/selfhealing-python/src/selfhealing/core/__init__.py` 수정
4. `packages/selfhealing-python/tests/unit/test_tls_failure.py`

---

## ✅ 완료 기준

- [ ] TLSErrorClassifier 구현
- [ ] TLSResilientClient 구현
- [ ] CertificateExpiryMonitor 구현
- [ ] 모든 TLS 에러 타입 분류 테스트 통과
- [ ] 재시도 가능/불가능 구분 테스트 통과
- [ ] 인증서 만료 감지 테스트 통과

---

## 📝 새 세션 시작 프롬프트

```
STAGE_25_TLS_FAILURE.md 문서대로 구현해줘.
TLSErrorClassifier와 CertificateExpiryMonitor 구현.
```

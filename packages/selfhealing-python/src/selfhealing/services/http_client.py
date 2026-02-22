"""
Self-Healing HTTP Client

Chaos 실험 플래그 자동 전파 기능이 포함된 HTTP 클라이언트.
OpenTelemetry 활성화 시 자동 계측을 활용하고,
비활성화 시 수동으로 헤더를 전파합니다.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Generator

if TYPE_CHECKING:
    import requests

from selfhealing.settings.http_client import get_http_client_settings

logger = logging.getLogger(__name__)

# Kubernetes / 내부 DNS 기본 접미사 — Settings 로드 실패 시 폴백
_DEFAULT_INTERNAL_DNS_SUFFIXES = (
    ".svc.cluster.local",
    ".internal",
)

# Context variable for chaos experiment status
_is_chaos_request: ContextVar[bool] = ContextVar("is_chaos_request", default=False)

# Context variable for suppressing OTEL instrumentation (internal calls)
_suppress_otel_instrumentation: ContextVar[bool] = ContextVar("suppress_otel_instrumentation", default=False)

# Header names for chaos context propagation
SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"
CHAOS_EXPERIMENT_ID_HEADER = "X-Chaos-Experiment-Id"


def _is_otel_enabled() -> bool:
    """Check if OpenTelemetry is enabled."""
    try:
        from selfhealing.observability import is_otel_enabled

        return is_otel_enabled()
    except ImportError:
        return False


@contextmanager
def suppress_otel_instrumentation() -> Generator[None, None, None]:
    """
    Context manager to suppress OpenTelemetry automatic instrumentation.

    Use this for internal health checks or calls that should not generate spans.

    Usage:
        with suppress_otel_instrumentation():
            # HTTP calls in this block won't create OTEL spans
            response = client.get(internal_health_url)
    """
    if not _is_otel_enabled():
        yield
        return

    try:
        from opentelemetry.context import attach, detach
        from opentelemetry.context import _SUPPRESS_INSTRUMENTATION_KEY
        from opentelemetry import context

        # Create context with suppression flag
        token = attach(context.set_value(_SUPPRESS_INSTRUMENTATION_KEY, True))
        try:
            yield
        finally:
            detach(token)
    except ImportError:
        yield
    except Exception:
        yield


class SelfHealingHttpClient:
    """
    Self-Healing 시스템용 HTTP 클라이언트.

    Chaos 실험 플래그 자동 전파 기능 제공.
    OpenTelemetry 활성화 시 자동 계측을 활용하여 traceparent 헤더 자동 주입.
    OTEL 비활성화 시 수동으로 헤더를 전파합니다.

    Usage:
        client = SelfHealingHttpClient(base_headers={"Authorization": "Bearer xxx"})

        # Chaos 컨텍스트 설정
        SelfHealingHttpClient.set_chaos_context(is_chaos=True, experiment_id="exp-123")

        # 요청 시 자동으로 X-Self-Healing-Synthetic 헤더 추가
        response = client.post(url, json=data, timeout=30)

        # 내부 헬스체크 등 OTEL span 생성 불필요 시
        with suppress_otel_instrumentation():
            response = client.get(health_check_url)
    """

    def __init__(
        self,
        base_headers: dict[str, str] | None = None,
        timeout: float | None = None,
        suppress_internal_spans: bool = False,
        propagate_context: bool = True,
    ):
        """
        초기화.

        Args:
            base_headers: 모든 요청에 포함할 기본 헤더
            timeout: 기본 타임아웃 (초). None이면 Settings에서 로드.
            suppress_internal_spans: 내부 호출 시 OTEL span 생성 억제 여부
            propagate_context: OTel Baggage(cell_id 등) 전파 여부.
                True(기본)이면 DNS suffix 매칭으로 내부 서비스에만 전파.
                False이면 모든 요청에서 Baggage 전파를 차단.
        """
        _settings = get_http_client_settings()
        self.base_headers = base_headers or {}
        self.default_timeout = timeout if timeout is not None else _settings.default_timeout
        self._experiment_id: str | None = None
        self._suppress_internal_spans = suppress_internal_spans
        self._propagate_context = propagate_context

    def _get_headers(
        self,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, str]:
        """
        요청 헤더 생성 (chaos 플래그 자동 포함).

        OTEL 활성화 시 traceparent 헤더는 자동 계측에서 주입되므로 생략.
        OTEL 비활성화 시에도 Chaos 플래그는 항상 전파됩니다.

        Args:
            extra_headers: 추가 헤더

        Returns:
            최종 헤더 딕셔너리
        """
        headers = {**self.base_headers}

        if extra_headers:
            headers.update(extra_headers)

        # Chaos 실험 컨텍스트 전파 (OTEL 상태와 무관하게 항상 전파)
        if _is_chaos_request.get():
            headers[SYNTHETIC_HEADER] = "chaos-experiment"

            # 실험 ID가 있으면 추가
            if self._experiment_id:
                headers[CHAOS_EXPERIMENT_ID_HEADER] = self._experiment_id

        # Deadline 헤더 수동 전파 제거 (266)
        # - 수신 측(admission_control.py)은 외부 게이트웨이의 X-Deadline-Remaining 헤더를 직접 사용
        # - SelfHealingHttpClient 간 deadline Baggage 전파는 270에서 추가 예정

        return headers

    def _execute_request(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """
        HTTP 요청 실행 (공통 로직).

        suppress_internal_spans가 True이면 OTEL span 생성을 억제합니다.

        Args:
            method: HTTP 메서드 (get, post, put, delete, patch)
            url: 요청 URL
            **kwargs: requests 추가 인자

        Returns:
            Response 객체
        """
        import requests as req_lib

        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)

        # Deadline 기반 timeout 자동 조정: 남은 시간이 기본 timeout보다 짧으면 축소
        try:
            from selfhealing.scaling.deadline_context import get_remaining_ms

            remaining = get_remaining_ms()
            if remaining is not None and remaining > 0:
                deadline_timeout = remaining / 1000.0  # ms → seconds
                if timeout is None or deadline_timeout < timeout:
                    timeout = deadline_timeout
        except ImportError:
            pass

        request_func = getattr(req_lib, method.lower())

        # Trust Boundary 필터링 — 내부 서비스에만 Baggage 전파
        if self._should_propagate_context(url):
            from selfhealing.observability.baggage import (
                detach_baggage_token,
                sync_contextvars_to_baggage,
            )

            baggage_token = sync_contextvars_to_baggage()
        else:
            detach_baggage_token = None
            baggage_token = None

        try:
            if self._suppress_internal_spans and _is_otel_enabled():
                with suppress_otel_instrumentation():
                    return request_func(url, headers=headers, timeout=timeout, **kwargs)

            return request_func(url, headers=headers, timeout=timeout, **kwargs)
        finally:
            if baggage_token is not None:
                detach_baggage_token(baggage_token)

    def _should_propagate_context(self, url: str) -> bool:
        """
        Trust Boundary 판별 — 내부 서비스에만 컨텍스트 전파.

        계층적 결정:
        1. propagate_context=False (명시적) → 무조건 차단
        2. propagate_context=True → DNS suffix 매칭으로 자동 판별
        """
        if not self._propagate_context:
            return False

        try:
            from urllib.parse import urlparse

            hostname = urlparse(url).hostname
            if not hostname:
                return False

            from selfhealing.settings.cell_topology import get_cell_topology_settings

            settings = get_cell_topology_settings()
            suffixes = getattr(settings, "internal_dns_suffixes", None)
            if suffixes is None:
                suffixes = _DEFAULT_INTERNAL_DNS_SUFFIXES

            return any(hostname.endswith(suffix) for suffix in suffixes)
        except Exception:
            # 파싱 실패 시 안전하게 차단 (Fail-Closed)
            return False

    def get(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """
        GET 요청.

        Args:
            url: 요청 URL
            **kwargs: requests.get() 추가 인자

        Returns:
            Response 객체
        """
        return self._execute_request("get", url, **kwargs)

    def post(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """
        POST 요청.

        Args:
            url: 요청 URL
            **kwargs: requests.post() 추가 인자

        Returns:
            Response 객체
        """
        return self._execute_request("post", url, **kwargs)

    def put(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """PUT 요청."""
        return self._execute_request("put", url, **kwargs)

    def delete(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """DELETE 요청."""
        return self._execute_request("delete", url, **kwargs)

    def patch(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """PATCH 요청."""
        return self._execute_request("patch", url, **kwargs)

    # =========================================================================
    # Class Methods for Context Management
    # =========================================================================

    @classmethod
    def set_chaos_context(
        cls,
        is_chaos: bool = True,
        experiment_id: str | None = None,
    ) -> None:
        """
        Chaos 실험 컨텍스트 설정.

        이 컨텍스트가 설정된 동안 모든 HTTP 요청에
        X-Self-Healing-Synthetic 헤더가 자동으로 추가됩니다.

        Args:
            is_chaos: Chaos 실험 여부
            experiment_id: 실험 ID (선택)
        """
        _is_chaos_request.set(is_chaos)
        if experiment_id:
            logger.debug("[SelfHealingHttpClient] Chaos context set: %s", experiment_id)

    @classmethod
    def clear_chaos_context(cls) -> None:
        """Chaos 컨텍스트 해제."""
        _is_chaos_request.set(False)

    @classmethod
    def is_chaos_request(cls) -> bool:
        """
        현재 요청이 Chaos 실험인지 확인.

        Returns:
            True if current context is in chaos experiment.
        """
        return _is_chaos_request.get()

    def set_experiment_id(self, experiment_id: str) -> None:
        """인스턴스에 실험 ID 설정."""
        self._experiment_id = experiment_id


class ChaosContextManager:
    """
    Chaos 컨텍스트 관리자 (context manager).

    Usage:
        with ChaosContextManager(experiment_id="exp-123"):
            # 이 블록 내의 모든 HTTP 요청에 chaos 헤더 추가
            client.post(url, json=data)
        # 블록을 벗어나면 자동으로 컨텍스트 해제
    """

    def __init__(self, experiment_id: str | None = None):
        self.experiment_id = experiment_id
        self._previous_state: bool = False

    def __enter__(self) -> ChaosContextManager:
        self._previous_state = _is_chaos_request.get()
        SelfHealingHttpClient.set_chaos_context(
            is_chaos=True,
            experiment_id=self.experiment_id,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        _is_chaos_request.set(self._previous_state)


def is_synthetic_request(headers: dict[str, str]) -> bool:
    """
    요청이 synthetic/chaos 요청인지 확인 (서버 측에서 사용).

    Args:
        headers: HTTP 요청 헤더

    Returns:
        True if X-Self-Healing-Synthetic 헤더가 있음.
    """
    return SYNTHETIC_HEADER in headers


def get_experiment_id_from_headers(headers: dict[str, str]) -> str | None:
    """
    헤더에서 실험 ID 추출 (서버 측에서 사용).

    Args:
        headers: HTTP 요청 헤더

    Returns:
        실험 ID 또는 None
    """
    return headers.get(CHAOS_EXPERIMENT_ID_HEADER)

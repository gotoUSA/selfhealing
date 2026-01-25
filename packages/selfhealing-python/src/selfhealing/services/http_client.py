"""
Self-Healing HTTP Client

Chaos 실험 플래그 자동 전파 기능이 포함된 HTTP 클라이언트.
OpenTelemetry 의존 없이 수동으로 헤더를 전파합니다.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional

from selfhealing.settings.http_client import get_http_client_settings

logger = logging.getLogger(__name__)

# Context variable for chaos experiment status
_is_chaos_request: ContextVar[bool] = ContextVar("is_chaos_request", default=False)

# Header names for chaos context propagation
SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"
CHAOS_EXPERIMENT_ID_HEADER = "X-Chaos-Experiment-Id"


class SelfHealingHttpClient:
    """
    Self-Healing 시스템용 HTTP 클라이언트.
    
    Chaos 실험 플래그 자동 전파 (OTel 의존 없음).
    기존 requests 라이브러리를 래핑하여 헤더를 자동으로 추가합니다.
    
    Usage:
        client = SelfHealingHttpClient(base_headers={"Authorization": "Bearer xxx"})
        
        # Chaos 컨텍스트 설정
        SelfHealingHttpClient.set_chaos_context(is_chaos=True, experiment_id="exp-123")
        
        # 요청 시 자동으로 X-Self-Healing-Synthetic 헤더 추가
        response = client.post(url, json=data, timeout=30)
    """
    
    def __init__(
        self,
        base_headers: Optional[Dict[str, str]] = None,
        timeout: Optional[float] = None,
    ):
        """
        초기화.
        
        Args:
            base_headers: 모든 요청에 포함할 기본 헤더
            timeout: 기본 타임아웃 (초). None이면 Settings에서 로드.
        """
        _settings = get_http_client_settings()
        self.base_headers = base_headers or {}
        self.default_timeout = timeout if timeout is not None else _settings.default_timeout
        self._experiment_id: Optional[str] = None
    
    def _get_headers(
        self,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        """
        요청 헤더 생성 (chaos 플래그 자동 포함).
        
        Args:
            extra_headers: 추가 헤더
        
        Returns:
            최종 헤더 딕셔너리
        """
        headers = {**self.base_headers}
        
        if extra_headers:
            headers.update(extra_headers)
        
        # Chaos 실험 컨텍스트 전파
        if _is_chaos_request.get():
            headers[SYNTHETIC_HEADER] = "chaos-experiment"
            
            # 실험 ID가 있으면 추가
            if self._experiment_id:
                headers[CHAOS_EXPERIMENT_ID_HEADER] = self._experiment_id
        
        return headers
    
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
        import requests
        
        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)
        
        return requests.get(url, headers=headers, timeout=timeout, **kwargs)
    
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
        import requests
        
        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)
        
        return requests.post(url, headers=headers, timeout=timeout, **kwargs)
    
    def put(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """PUT 요청."""
        import requests
        
        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)
        
        return requests.put(url, headers=headers, timeout=timeout, **kwargs)
    
    def delete(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """DELETE 요청."""
        import requests
        
        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)
        
        return requests.delete(url, headers=headers, timeout=timeout, **kwargs)
    
    def patch(
        self,
        url: str,
        **kwargs: Any,
    ) -> "requests.Response":
        """PATCH 요청."""
        import requests
        
        headers = self._get_headers(kwargs.pop("headers", None))
        timeout = kwargs.pop("timeout", self.default_timeout)
        
        return requests.patch(url, headers=headers, timeout=timeout, **kwargs)
    
    # =========================================================================
    # Class Methods for Context Management
    # =========================================================================
    
    @classmethod
    def set_chaos_context(
        cls,
        is_chaos: bool = True,
        experiment_id: Optional[str] = None,
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
            logger.debug(f"[SelfHealingHttpClient] Chaos context set: {experiment_id}")
    
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
    
    def __init__(self, experiment_id: Optional[str] = None):
        self.experiment_id = experiment_id
        self._previous_state: bool = False
    
    def __enter__(self) -> "ChaosContextManager":
        self._previous_state = _is_chaos_request.get()
        SelfHealingHttpClient.set_chaos_context(
            is_chaos=True,
            experiment_id=self.experiment_id,
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        _is_chaos_request.set(self._previous_state)


def is_synthetic_request(headers: Dict[str, str]) -> bool:
    """
    요청이 synthetic/chaos 요청인지 확인 (서버 측에서 사용).
    
    Args:
        headers: HTTP 요청 헤더
    
    Returns:
        True if X-Self-Healing-Synthetic 헤더가 있음.
    """
    return SYNTHETIC_HEADER in headers


def get_experiment_id_from_headers(headers: Dict[str, str]) -> Optional[str]:
    """
    헤더에서 실험 ID 추출 (서버 측에서 사용).
    
    Args:
        headers: HTTP 요청 헤더
    
    Returns:
        실험 ID 또는 None
    """
    return headers.get(CHAOS_EXPERIMENT_ID_HEADER)

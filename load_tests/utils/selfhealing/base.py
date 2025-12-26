"""
SelfHealing Base HTTP Client.

requests 기반 HTTP 클라이언트.
"""

import logging
from typing import Any, Dict, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import get_config, SelfHealingConfig

logger = logging.getLogger(__name__)


class BaseClient:
    """기본 HTTP 클라이언트."""
    
    def __init__(
        self,
        config: Optional[SelfHealingConfig] = None,
        session: Optional[requests.Session] = None,
    ):
        """
        클라이언트 초기화.
        
        Args:
            config: 설정 객체 (없으면 기본값 사용)
            session: requests 세션 (없으면 새로 생성)
        """
        self.config = config or get_config()
        self.session = session or self._create_session()
        self._token: Optional[str] = None
    
    def _create_session(self) -> requests.Session:
        """재시도가 설정된 세션 생성."""
        session = requests.Session()
        
        retry_strategy = Retry(
            total=self.config.max_retries,
            status_forcelist=[502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST", "PUT", "DELETE"],
            backoff_factor=1,
        )
        
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        return session
    
    def _get_headers(self, extra_headers: Optional[Dict] = None) -> Dict[str, str]:
        """요청 헤더 생성."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        
        # JWT 토큰 추가
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        
        # 추가 헤더
        if extra_headers:
            headers.update(extra_headers)
        
        return headers
    
    def set_token(self, token: str) -> None:
        """인증 토큰 설정."""
        self._token = token
    
    def get(
        self,
        url: str,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        """GET 요청."""
        if self.config.debug:
            logger.debug(f"GET {url} params={params}")
        
        return self.session.get(
            url,
            params=params,
            headers=self._get_headers(headers),
            timeout=self.config.timeout,
            **kwargs,
        )
    
    def post(
        self,
        url: str,
        json: Optional[Dict] = None,
        data: Optional[Any] = None,
        headers: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        """POST 요청."""
        if self.config.debug:
            logger.debug(f"POST {url} json={json}")
        
        return self.session.post(
            url,
            json=json,
            data=data,
            headers=self._get_headers(headers),
            timeout=self.config.timeout,
            **kwargs,
        )
    
    def put(
        self,
        url: str,
        json: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        """PUT 요청."""
        if self.config.debug:
            logger.debug(f"PUT {url} json={json}")
        
        return self.session.put(
            url,
            json=json,
            headers=self._get_headers(headers),
            timeout=self.config.timeout,
            **kwargs,
        )
    
    def patch(
        self,
        url: str,
        json: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        """PATCH 요청."""
        if self.config.debug:
            logger.debug(f"PATCH {url} json={json}")
        
        return self.session.patch(
            url,
            json=json,
            headers=self._get_headers(headers),
            timeout=self.config.timeout,
            **kwargs,
        )
    
    def delete(
        self,
        url: str,
        headers: Optional[Dict] = None,
        **kwargs,
    ) -> requests.Response:
        """DELETE 요청."""
        if self.config.debug:
            logger.debug(f"DELETE {url}")
        
        return self.session.delete(
            url,
            headers=self._get_headers(headers),
            timeout=self.config.timeout,
            **kwargs,
        )
    
    def api_get(self, endpoint: str, **kwargs) -> requests.Response:
        """Self-Healing API GET 요청."""
        url = self.config.get_api_url(endpoint)
        return self.get(url, **kwargs)
    
    def api_post(self, endpoint: str, **kwargs) -> requests.Response:
        """Self-Healing API POST 요청."""
        url = self.config.get_api_url(endpoint)
        return self.post(url, **kwargs)
    
    def api_put(self, endpoint: str, **kwargs) -> requests.Response:
        """Self-Healing API PUT 요청."""
        url = self.config.get_api_url(endpoint)
        return self.put(url, **kwargs)
    
    def api_patch(self, endpoint: str, **kwargs) -> requests.Response:
        """Self-Healing API PATCH 요청."""
        url = self.config.get_api_url(endpoint)
        return self.patch(url, **kwargs)
    
    def api_delete(self, endpoint: str, **kwargs) -> requests.Response:
        """Self-Healing API DELETE 요청."""
        url = self.config.get_api_url(endpoint)
        return self.delete(url, **kwargs)
    
    def xtest_get(self, endpoint: str, **kwargs) -> requests.Response:
        """XTest API GET 요청 (Chaos 헤더 포함)."""
        url = self.config.get_xtest_url(endpoint)
        headers = kwargs.pop("headers", {})
        headers.update(self.config.get_xtest_headers())
        return self.get(url, headers=headers, **kwargs)
    
    def xtest_post(self, endpoint: str, **kwargs) -> requests.Response:
        """XTest API POST 요청 (Chaos 헤더 포함)."""
        url = self.config.get_xtest_url(endpoint)
        headers = kwargs.pop("headers", {})
        headers.update(self.config.get_xtest_headers())
        return self.post(url, headers=headers, **kwargs)
    
    def close(self) -> None:
        """세션 종료."""
        if self.session:
            self.session.close()
    
    @property
    def token(self) -> Optional[str]:
        """현재 인증 토큰 반환."""
        return self._token
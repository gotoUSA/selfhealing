"""
SelfHealing Authentication.

JWT 로그인 및 XTest 모드 인증 처리.
"""

import logging
from typing import Optional, Tuple

from .base import BaseClient

logger = logging.getLogger(__name__)


class AuthClient:
    """인증 클라이언트."""
    
    def __init__(self, base_client: BaseClient):
        """초기화."""
        self.client = base_client
        self.config = base_client.config
        self._authenticated = False
    
    def login(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        JWT 로그인.
        
        Args:
            username: 사용자 이름 (없으면 설정에서 가져옴)
            password: 비밀번호 (없으면 설정에서 가져옴)
        
        Returns:
            (성공 여부, 에러 메시지 또는 None)
        """
        username = username or self.config.username
        password = password or self.config.password
        
        try:
            # Django REST Framework JWT 엔드포인트
            response = self.client.post(
                f"{self.config.host}/api/token/",
                json={"username": username, "password": password},
            )
            
            if response.status_code == 200:
                data = response.json()
                token = data.get("access") or data.get("token")
                if token:
                    self.client.set_token(token)
                    self._authenticated = True
                    logger.info(f"[Auth] Login successful: {username}")
                    return True, None
                return False, "Token not found in response"
            
            return False, f"Login failed: {response.status_code}"
        
        except Exception as e:
            logger.error(f"[Auth] Login error: {e}")
            return False, str(e)
    
    def login_or_skip(
        self,
        username: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bool:
        """
        로그인 시도, 실패해도 계속 진행.
        
        XTest 모드 또는 Public 엔드포인트만 사용할 경우.
        """
        if self.config.auth_type == "none":
            logger.info("[Auth] Auth disabled, skipping login")
            return True
        
        if self.config.auth_type == "xtest":
            logger.info("[Auth] XTest mode, skipping JWT login")
            return True
        
        success, error = self.login(username, password)
        if not success:
            logger.warning(f"[Auth] Login failed, continuing anyway: {error}")
        return success
    
    def ensure_authenticated(self) -> bool:
        """
        인증 상태 확인, 필요시 로그인 시도.
        """
        if self._authenticated:
            return True
        
        if self.config.auth_type == "none":
            return True
        
        if self.config.auth_type == "xtest":
            return True
        
        return self.login_or_skip()
    
    @property
    def is_authenticated(self) -> bool:
        """인증 여부."""
        return self._authenticated or self.config.auth_type in ("none", "xtest")
    
    def logout(self) -> None:
        """로그아웃 (토큰 제거)."""
        self.client.set_token(None)
        self._authenticated = False
        logger.info("[Auth] Logged out")

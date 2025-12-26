"""
SelfHealing Client Configuration.

환경변수 기반 설정 관리.

Usage:
    from load_tests.utils.selfhealing.config import get_config
    
    config = get_config()
    print(config.host)  # http://localhost:8000
"""

import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SelfHealingConfig:
    """SelfHealing 클라이언트 설정."""
    
    # 호스트 설정
    host: str = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_HOST", "http://localhost:8000"
    ))
    
    # API 경로 prefix
    api_prefix: str = "/api/self-healing"
    xtest_prefix: str = "/api/self-healing/xtest"
    
    # 인증 설정
    auth_type: str = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_AUTH_TYPE", "jwt"  # jwt, xtest, none
    ))
    username: str = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_USERNAME", "admin"
    ))
    password: str = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_PASSWORD", "admin"
    ))
    
    # XTest 모드 설정
    xtest_header: str = "X-Test-Mode"
    xtest_value: str = "chaos-monkey"
    
    # 타임아웃 설정
    timeout: int = field(default_factory=lambda: int(os.environ.get(
        "SELFHEALING_TIMEOUT", "30"
    )))
    
    # 재시도 설정
    max_retries: int = field(default_factory=lambda: int(os.environ.get(
        "SELFHEALING_MAX_RETRIES", "3"
    )))
    
    # 디버그 모드
    debug: bool = field(default_factory=lambda: os.environ.get(
        "SELFHEALING_DEBUG", "false"
    ).lower() == "true")
    
    def get_api_url(self, endpoint: str) -> str:
        """API 엔드포인트 URL 생성."""
        endpoint = endpoint.lstrip("/")
        return f"{self.host}{self.api_prefix}/{endpoint}"
    
    def get_xtest_url(self, endpoint: str) -> str:
        """XTest 엔드포인트 URL 생성."""
        endpoint = endpoint.lstrip("/")
        return f"{self.host}{self.xtest_prefix}/{endpoint}"
    
    def get_xtest_headers(self) -> dict:
        """XTest 모드용 헤더 반환."""
        return {self.xtest_header: self.xtest_value}


# 싱글톤 인스턴스
_config: Optional[SelfHealingConfig] = None


def get_config() -> SelfHealingConfig:
    """설정 싱글톤 반환."""
    global _config
    if _config is None:
        _config = SelfHealingConfig()
    return _config


def reset_config() -> None:
    """설정 리셋 (테스트용)."""
    global _config
    _config = None


def configure(
    host: Optional[str] = None,
    auth_type: Optional[str] = None,
    username: Optional[str] = None,
    password: Optional[str] = None,
    timeout: Optional[int] = None,
    debug: Optional[bool] = None,
) -> SelfHealingConfig:
    """설정 커스터마이징."""
    global _config
    _config = SelfHealingConfig()
    
    if host:
        _config.host = host
    if auth_type:
        _config.auth_type = auth_type
    if username:
        _config.username = username
    if password:
        _config.password = password
    if timeout:
        _config.timeout = timeout
    if debug is not None:
        _config.debug = debug
    
    return _config

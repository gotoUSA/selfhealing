"""
Layered Configuration Provider.

설정 우선순위 (낮음 → 높음):
1. Hard-coded defaults (Pydantic Field default)
2. Static ENV (.env, 환경변수)
3. Dynamic DB/Redis (RuntimeConfigManager)
4. Request-scoped override (per-request context)
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any, Dict, Optional, Type, TypeVar

from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseSettings)

# =============================================================================
# Level 4: Request-scoped overrides using contextvars
# =============================================================================
_request_overrides: ContextVar[Dict[str, Dict[str, Any]]] = ContextVar(
    "selfhealing_request_config_overrides",
    default={},
)


def set_request_override(config_type: str, overrides: Dict[str, Any]) -> None:
    """
    Request 스코프 설정 오버라이드 설정.
    
    이 함수는 현재 요청/컨텍스트에서만 유효한 임시 설정을 적용합니다.
    Django 미들웨어나 async context에서 사용됩니다.
    
    Args:
        config_type: 설정 타입 (e.g., "circuit_breaker", "retry")
        overrides: 오버라이드할 필드 딕셔너리
        
    Example:
        set_request_override("circuit_breaker", {"failure_threshold": 10})
    """
    current = _request_overrides.get().copy()
    current[config_type] = overrides
    _request_overrides.set(current)
    logger.debug(f"[LayeredProvider] Request override set for {config_type}: {overrides}")


def get_request_override(config_type: str) -> Dict[str, Any]:
    """
    현재 컨텍스트의 Request 스코프 오버라이드 조회.
    
    Args:
        config_type: 설정 타입
        
    Returns:
        오버라이드 딕셔너리 (없으면 빈 dict)
    """
    return _request_overrides.get().get(config_type, {})


def clear_request_overrides() -> None:
    """
    Request 스코프 오버라이드 모두 초기화.
    
    Django 미들웨어의 process_response에서 호출하여 정리합니다.
    """
    _request_overrides.set({})
    logger.debug("[LayeredProvider] Request overrides cleared")


def get_all_request_overrides() -> Dict[str, Dict[str, Any]]:
    """
    모든 Request 스코프 오버라이드 조회.
    
    Returns:
        {config_type: {field: value}} 딕셔너리
    """
    return _request_overrides.get().copy()


# =============================================================================
# Layered Settings Provider
# =============================================================================

def get_layered_settings(
    settings_class: Type[T],
    config_type: str,
    include_runtime: bool = True,
    include_request: bool = True,
) -> T:
    """
    계층화된 설정 로드.
    
    우선순위: Hard-coded < ENV < DB/Redis < Request Override
    
    Args:
        settings_class: Pydantic Settings 클래스
        config_type: RuntimeConfigManager 설정 타입 (e.g., "circuit_breaker")
        include_runtime: Level 3 (DB/Redis) 포함 여부
        include_request: Level 4 (Request override) 포함 여부
        
    Returns:
        병합된 Settings 인스턴스
        
    Example:
        from selfhealing.settings import CircuitBreakerSettings
        
        # 전체 계층 적용
        settings = get_layered_settings(CircuitBreakerSettings, "circuit_breaker")
        
        # ENV까지만 (테스트용)
        settings = get_layered_settings(
            CircuitBreakerSettings, "circuit_breaker",
            include_runtime=False, include_request=False
        )
    """
    # Level 1 + 2: Pydantic defaults + ENV (BaseSettings가 자동 처리)
    base_settings = settings_class()
    base_dict = base_settings.model_dump()
    
    # Level 3: DB/Redis (RuntimeConfigManager)
    if include_runtime:
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            runtime_config = manager._get_config(config_type)
            
            # 유효한 필드만 병합
            valid_fields = set(base_dict.keys())
            for key, value in runtime_config.items():
                if key in valid_fields:
                    base_dict[key] = value
                    
            logger.debug(f"[LayeredProvider] Merged runtime config for {config_type}")
        except Exception as e:
            # Graceful fallback - RuntimeConfigManager 없어도 동작
            logger.debug(f"[LayeredProvider] Runtime config not available: {e}")
    
    # Level 4: Request-scoped override
    if include_request:
        request_overrides = get_request_override(config_type)
        if request_overrides:
            valid_fields = set(base_dict.keys())
            for key, value in request_overrides.items():
                if key in valid_fields:
                    base_dict[key] = value
            logger.debug(
                f"[LayeredProvider] Applied request overrides for {config_type}: "
                f"{list(request_overrides.keys())}"
            )
    
    # 병합된 값으로 새 인스턴스 생성
    return settings_class.model_validate(base_dict)


def detect_config_source(
    settings_class: Type[T],
    config_type: str,
    field_name: str,
) -> str:
    """
    설정 값의 출처 감지.
    
    Args:
        settings_class: Pydantic Settings 클래스
        config_type: 설정 타입
        field_name: 필드 이름
        
    Returns:
        출처 문자열: "DEFAULT", "ENV", "RUNTIME", "REQUEST"
    """
    import os
    
    # Level 4: Request override
    request_overrides = get_request_override(config_type)
    if field_name in request_overrides:
        return "REQUEST"
    
    # Level 3: Runtime (DB/Redis)
    try:
        from selfhealing.services.runtime_config import get_runtime_config_manager
        manager = get_runtime_config_manager()
        runtime_config = manager._cache.get(config_type, {})
        if field_name in runtime_config:
            return "RUNTIME"
    except Exception:
        pass
    
    # Level 2: Environment variable
    # Pydantic BaseSettings의 env_prefix 확인
    model_config = getattr(settings_class, "model_config", {})
    env_prefix = model_config.get("env_prefix", "SELFHEALING_")
    env_key = f"{env_prefix}{field_name.upper()}"
    if env_key in os.environ:
        return "ENV"
    
    # Level 1: Default
    return "DEFAULT"


def get_config_with_sources(
    settings_class: Type[T],
    config_type: str,
) -> Dict[str, Dict[str, Any]]:
    """
    모든 설정 값과 출처를 함께 반환.
    
    Args:
        settings_class: Pydantic Settings 클래스
        config_type: 설정 타입
        
    Returns:
        {field_name: {"value": ..., "source": ...}} 딕셔너리
        
    Example:
        >>> info = get_config_with_sources(CircuitBreakerSettings, "circuit_breaker")
        >>> info["failure_threshold"]
        {"value": 5, "source": "DEFAULT"}
    """
    settings = get_layered_settings(settings_class, config_type)
    result = {}
    
    for field_name in settings.model_fields.keys():
        value = getattr(settings, field_name)
        source = detect_config_source(settings_class, config_type, field_name)
        result[field_name] = {
            "value": value,
            "source": source,
        }
    
    return result


# =============================================================================
# Context Manager for Request Override
# =============================================================================

class RequestOverrideContext:
    """
    Request 오버라이드를 위한 Context Manager.
    
    with 블록 종료 시 자동으로 오버라이드가 정리됩니다.
    
    Example:
        with RequestOverrideContext("circuit_breaker", {"failure_threshold": 10}):
            # 이 블록 내에서는 failure_threshold가 10
            settings = get_layered_settings(CircuitBreakerSettings, "circuit_breaker")
            assert settings.failure_threshold == 10
        # 블록 종료 후 원래 값으로 복원
    """
    
    def __init__(self, config_type: str, overrides: Dict[str, Any]):
        self.config_type = config_type
        self.overrides = overrides
        self._previous: Optional[Dict[str, Dict[str, Any]]] = None
    
    def __enter__(self) -> "RequestOverrideContext":
        self._previous = get_all_request_overrides()
        set_request_override(self.config_type, self.overrides)
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # 이전 상태로 복원
        if self._previous is not None:
            _request_overrides.set(self._previous)
        else:
            clear_request_overrides()
        return None


# =============================================================================
# Convenience functions for common config types
# =============================================================================

def get_circuit_breaker_layered():
    """CircuitBreakerSettings를 계층화된 방식으로 로드."""
    from selfhealing.settings.circuit_breaker import CircuitBreakerSettings
    return get_layered_settings(CircuitBreakerSettings, "circuit_breaker")


def get_retry_layered():
    """RetrySettings를 계층화된 방식으로 로드."""
    from selfhealing.settings.retry import RetrySettings
    return get_layered_settings(RetrySettings, "retry")


def get_dlq_layered():
    """DLQSettings를 계층화된 방식으로 로드."""
    from selfhealing.settings.dlq import DLQSettings
    return get_layered_settings(DLQSettings, "dlq")


def get_rate_limit_layered():
    """RateLimitSettings를 계층화된 방식으로 로드."""
    from selfhealing.settings.rate_limit import RateLimitSettings
    return get_layered_settings(RateLimitSettings, "rate_limit")

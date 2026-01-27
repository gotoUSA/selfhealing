"""
Load Shedding (부분적 차단)

핵심 서비스에 장애 조짐이 보이면, 비핵심 서비스 트래픽을 먼저 제한하여
핵심 서비스에 리소스를 집중시킵니다.

Usage:
    manager = get_load_shedding_manager()

    # 서비스 트래픽 허용 비율 조회
    allowed_traffic = manager.evaluate_shedding("review-api")  # Returns 50.0 (%)

    # Shedding 활성화 여부 확인
    if manager.is_shedding_active():
        print(f"Current shedding level: {manager.get_current_level()}")
"""

from __future__ import annotations

from typing import Optional

# ============================================================
# Dashboard
# ============================================================
from .dashboard import (
    LoadSheddingDashboard,
)

# ============================================================
# Error Rate Provider
# ============================================================
from .error_rate import (
    ErrorRateProvider,
)

# ============================================================
# Manager
# ============================================================
from .manager import (
    LoadSheddingManager,
)

# ============================================================
# Middleware
# ============================================================
from .shedding_middleware import (
    LoadSheddingMiddleware,
)

# ============================================================
# Data Models
# ============================================================
from .shedding_models import (
    SheddingAuditEntry,
    SheddingDecision,
    SheddingState,
    SheddingStatus,
)

# ============================================================
# Module-level Convenience Functions
# ============================================================

_manager: LoadSheddingManager | None = None
_middleware: LoadSheddingMiddleware | None = None
_dashboard: LoadSheddingDashboard | None = None


def get_load_shedding_manager() -> LoadSheddingManager:
    """
    LoadSheddingManager 싱글톤 인스턴스 반환.

    Returns:
        LoadSheddingManager: 싱글톤 인스턴스
    """
    global _manager
    if _manager is None:
        _manager = LoadSheddingManager()
    return _manager


def reset_load_shedding_manager() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _manager, _middleware, _dashboard
    if _manager is not None:
        _manager.reset()
    _manager = None
    _middleware = None
    _dashboard = None
    LoadSheddingManager.reset_instance()


def get_load_shedding_middleware() -> LoadSheddingMiddleware:
    """
    LoadSheddingMiddleware 인스턴스 반환.

    Returns:
        LoadSheddingMiddleware 인스턴스
    """
    global _middleware
    if _middleware is None:
        _middleware = LoadSheddingMiddleware(get_load_shedding_manager())
    return _middleware


def get_load_shedding_dashboard() -> LoadSheddingDashboard:
    """
    LoadSheddingDashboard 인스턴스 반환.

    Returns:
        LoadSheddingDashboard 인스턴스
    """
    global _dashboard
    if _dashboard is None:
        _dashboard = LoadSheddingDashboard(get_load_shedding_manager())
    return _dashboard


# ============================================================
# Convenience Functions
# ============================================================


def register_load_shedding_service(config) -> bool:
    """
    서비스 등록.

    Args:
        config: 서비스 설정 (ServiceConfig)

    Returns:
        bool: 등록 성공 여부
    """
    return get_load_shedding_manager().register_service(config)


def evaluate_shedding(service_id: str) -> float:
    """
    서비스 트래픽 허용 비율 조회.

    Args:
        service_id: 서비스 ID

    Returns:
        float: 허용 트래픽 비율 (0~100)
    """
    return get_load_shedding_manager().evaluate_shedding(service_id)


def should_allow_shedding_request(service_id: str) -> SheddingDecision:
    """
    요청 허용 여부 결정.

    Args:
        service_id: 서비스 ID

    Returns:
        SheddingDecision: 허용 여부 및 상세 정보
    """
    return get_load_shedding_manager().should_allow_request(service_id)


def is_shedding_active() -> bool:
    """
    Shedding 활성화 여부.

    Returns:
        bool: 활성화 여부
    """
    return get_load_shedding_manager().is_shedding_active()


def get_shedding_status() -> SheddingStatus:
    """
    현재 Shedding 상태 조회.

    Returns:
        SheddingStatus: 현재 상태
    """
    return get_load_shedding_manager().get_status()


def set_service_error_rate(service_id: str, error_rate: float) -> None:
    """
    서비스 에러율 설정 (테스트/외부 메트릭 연동용).

    Args:
        service_id: 서비스 ID
        error_rate: 에러율 (0~100)
    """
    get_load_shedding_manager().set_error_rate(service_id, error_rate)


def update_shedding_state() -> SheddingAuditEntry | None:
    """
    Shedding 상태 업데이트.

    Returns:
        레벨 변화 시 SheddingAuditEntry, 없으면 None
    """
    return get_load_shedding_manager().update_shedding_state()


# ============================================================
# Public API
# ============================================================
__all__ = [
    # Data Models
    "SheddingState",
    "SheddingDecision",
    "SheddingStatus",
    "SheddingAuditEntry",
    # Error Rate Provider
    "ErrorRateProvider",
    # Manager
    "LoadSheddingManager",
    # Middleware
    "LoadSheddingMiddleware",
    # Dashboard
    "LoadSheddingDashboard",
    # Convenience Functions
    "get_load_shedding_manager",
    "reset_load_shedding_manager",
    "get_load_shedding_middleware",
    "get_load_shedding_dashboard",
    "register_load_shedding_service",
    "evaluate_shedding",
    "should_allow_shedding_request",
    "is_shedding_active",
    "get_shedding_status",
    "set_service_error_rate",
    "update_shedding_state",
]

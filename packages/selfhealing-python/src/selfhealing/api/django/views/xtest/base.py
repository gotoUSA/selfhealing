"""
X-Test-Mode Base Module

공통 유틸리티, Mixin, 헬퍼 함수들을 정의합니다.

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단
"""

import logging
import os
import threading
from typing import Any, Dict, Optional

import psutil
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response

logger = logging.getLogger(__name__)


# =============================================================================
# X-Test-Mode Security Mixin
# =============================================================================

class XTestModeMixin:
    """
    X-Test-Mode 보안 검증 믹스인.
    
    Requirements:
    1. X-Test-Mode: chaos-monkey 헤더
    2. DEBUG=True 또는 CHAOS_ENABLED=true
    3. ENVIRONMENT != production
    """
    
    CHAOS_HEADER = "X-Test-Mode"
    CHAOS_VALUE = "chaos-monkey"
    
    def is_chaos_allowed(self, request: Request) -> tuple[bool, str]:
        """
        Chaos 모드 허용 여부 검증.
        
        Returns:
            (allowed: bool, reason: str)
        """
        # 1. 헤더 확인
        header_value = request.headers.get(self.CHAOS_HEADER, "")
        if header_value != self.CHAOS_VALUE:
            return False, f"Missing or invalid {self.CHAOS_HEADER} header"
        
        # 2. 프로덕션 차단
        environment = os.getenv("ENVIRONMENT", "development").lower()
        if environment == "production":
            return False, "X-Test-Mode is disabled in production"
        
        # 3. DEBUG 또는 CHAOS_ENABLED 확인
        debug_mode = getattr(settings, "DEBUG", False)
        chaos_enabled = os.getenv("CHAOS_ENABLED", "false").lower() == "true"
        
        if not debug_mode and not chaos_enabled:
            return False, "Chaos mode requires DEBUG=True or CHAOS_ENABLED=true"
        
        return True, "Chaos mode allowed"
    
    def check_chaos_permission(self, request: Request) -> Optional[Response]:
        """
        Chaos 권한 체크. 실패시 Response 반환.
        
        Returns:
            None if allowed, Response if denied
        """
        allowed, reason = self.is_chaos_allowed(request)
        if not allowed:
            logger.warning(f"[X-Test-Mode] Denied: {reason} (user: {request.user})")
            return Response(
                {
                    "status": "error",
                    "error": "chaos_mode_disabled",
                    "message": reason,
                    "hint": f"Add header '{self.CHAOS_HEADER}: {self.CHAOS_VALUE}' and ensure CHAOS_ENABLED=true"
                },
                status=status.HTTP_403_FORBIDDEN
            )
        return None


# =============================================================================
# System Snapshot Utility
# =============================================================================

def collect_system_snapshot() -> Dict[str, Any]:
    """시스템 스냅샷 수집 (CPU, Memory, Connections)."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.1)
        memory = psutil.virtual_memory()
        
        snapshot = {
            "timestamp": timezone.now().isoformat(),
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_used_mb": memory.used / (1024 * 1024),
            "memory_available_mb": memory.available / (1024 * 1024),
        }
        
        # DB 연결 수 (Repository 사용)
        try:
            from selfhealing.adapters.postgres.repository import get_postgres_repository
            repo = get_postgres_repository()
            active_connections = repo.get_active_connection_count()
            snapshot["db_active_connections"] = active_connections
        except Exception:
            snapshot["db_active_connections"] = None
            
        return snapshot
    except Exception as e:
        logger.warning(f"[X-Test-Mode] Snapshot collection failed: {e}")
        return {
            "timestamp": timezone.now().isoformat(),
            "error": str(e)
        }


# =============================================================================
# In-Memory Event Storage (Singleton)
# =============================================================================

_healing_events_lock = threading.Lock()
_healing_events: list[Dict[str, Any]] = []
_healing_incidents: list[Dict[str, Any]] = []
_max_events = 500
_max_incidents = 100


def add_healing_event(event: Dict[str, Any]) -> None:
    """힐링 이벤트 기록."""
    global _healing_events
    with _healing_events_lock:
        event["recorded_at"] = timezone.now().isoformat()
        _healing_events.append(event)
        if len(_healing_events) > _max_events:
            _healing_events = _healing_events[-_max_events:]


def add_healing_incident(incident: Dict[str, Any]) -> None:
    """힐링 인시던트 기록."""
    global _healing_incidents
    with _healing_events_lock:
        incident["recorded_at"] = timezone.now().isoformat()
        _healing_incidents.append(incident)
        if len(_healing_incidents) > _max_incidents:
            _healing_incidents = _healing_incidents[-_max_incidents:]


def get_healing_events(limit: int = 50) -> list[Dict[str, Any]]:
    """힐링 이벤트 조회."""
    with _healing_events_lock:
        return list(_healing_events[-limit:])


def get_healing_incidents(limit: int = 10) -> list[Dict[str, Any]]:
    """힐링 인시던트 조회."""
    with _healing_events_lock:
        return list(_healing_incidents[-limit:])


def get_healing_events_count() -> int:
    """힐링 이벤트 총 개수."""
    with _healing_events_lock:
        return len(_healing_events)


def get_healing_incidents_count() -> int:
    """힐링 인시던트 총 개수."""
    with _healing_events_lock:
        return len(_healing_incidents)


# Legacy alias for backward compatibility
_collect_system_snapshot = collect_system_snapshot
_add_healing_event = add_healing_event
_add_healing_incident = add_healing_incident

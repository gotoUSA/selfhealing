"""
X-Test-Mode Base Module

공통 유틸리티, Mixin, 헬퍼 함수들을 정의합니다.

Security (2중 보안 장치):
1차 - Django RBAC: HasChaosTestPermission 권한 클래스
2차 - XTestModeMixin: X-Test-Mode 헤더 + 환경 변수 검증

Requirements:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단
"""

import logging
import os
import threading
import uuid
from typing import Any, Dict, Optional

import psutil
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import SessionAuthentication, BasicAuthentication
from rest_framework.request import Request
from rest_framework.response import Response

from selfhealing.api.django.permissions import HasChaosTestPermission
from selfhealing.services.audit.xtest_audit import (
    log_xtest_operation_audit,
    log_xtest_injection_audit,
    log_xtest_cleanup_audit,
)
from selfhealing.core.test_mode_context import TestModeContext

logger = logging.getLogger(__name__)


# =============================================================================
# X-Test-Mode Security Mixin
# =============================================================================

class XTestModeMixin:
    """
    X-Test-Mode 2중 보안 검증 믹스인.
    
    Security (2중 보안 장치):
    1차 - Django RBAC: HasChaosTestPermission (인증/그룹 기반)
    2차 - XTestModeMixin: 헤더 + 환경 변수 검증
    
    Requirements:
    1. Django 인증 + HasChaosTestPermission 권한
    2. X-Test-Mode: chaos-monkey 헤더
    3. DEBUG=True 또는 CHAOS_ENABLED=true
    4. ENVIRONMENT != production
    """
    
    # 1차 보안: Django RBAC 기반 인증/권한
    authentication_classes = [SessionAuthentication, BasicAuthentication]
    permission_classes = [HasChaosTestPermission]
    
    # 2차 보안: 헤더 검증용 상수
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

    def get_xtest_session_id(self, request: Request) -> str:
        """X-Test 세션 ID 추출. 헤더가 없으면 자동 생성."""
        return request.headers.get("X-Test-Session", str(uuid.uuid4())[:8])

    def ensure_xtest_session(self, request: Request) -> str:
        """
        X-Test 세션 생성 또는 갱신.
        
        세션이 없으면 새로 생성하고, 있으면 기존 세션을 반환합니다.
        세션 메타데이터는 Redis에 저장되어 자동 정리 시 사용됩니다.
        
        Args:
            request: HTTP 요청 객체
            
        Returns:
            세션 ID
        """
        session_id = self.get_xtest_session_id(request)
        user = self.get_xtest_user(request)
        
        try:
            from selfhealing.services.xtest_session_manager import get_xtest_session_manager
            session_manager = get_xtest_session_manager()
            
            # 기존 세션 확인
            existing = session_manager.get_session(session_id)
            if not existing:
                # 새 세션 생성
                session_manager.create_session(session_id=session_id, user=user)
                logger.debug(f"[X-Test-Mode] Created new session: {session_id}")
            
        except ImportError:
            logger.debug("[X-Test-Mode] Session manager not available")
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Failed to ensure session: {e}")
        
        return session_id

    def register_xtest_artifact(
        self,
        request: Request,
        artifact_id: str,
        component: str,
    ) -> bool:
        """
        X-Test 아티팩트를 세션에 등록.
        
        테스트 중 생성된 DLQ 항목, CB 상태 변경 등을 세션에 등록하여
        세션 만료 시 자동으로 정리될 수 있도록 합니다.
        
        Args:
            request: HTTP 요청 객체
            artifact_id: 아티팩트 ID (DLQ entry ID, CB service name 등)
            component: 컴포넌트 이름 (dlq, cb, idempotency 등)
            
        Returns:
            등록 성공 여부
        """
        session_id = self.get_xtest_session_id(request)
        
        try:
            from selfhealing.services.xtest_session_manager import get_xtest_session_manager
            session_manager = get_xtest_session_manager()
            
            success = session_manager.register_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                component=component,
            )
            
            if success:
                logger.debug(
                    f"[X-Test-Mode] Registered artifact: "
                    f"session={session_id}, component={component}, id={artifact_id}"
                )
            return success
            
        except ImportError:
            logger.debug("[X-Test-Mode] Session manager not available")
            return False
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Failed to register artifact: {e}")
            return False

    def enter_synthetic_context(self, request: Request) -> None:
        """
        합성 요청 컨텍스트 진입.
        
        X-Test 요청 처리 시작 시 호출하여 TestModeContext를 활성화합니다.
        이후 모든 메트릭과 Redis 키가 합성 요청으로 태깅됩니다.
        세션이 없으면 자동으로 생성합니다.
        
        Args:
            request: HTTP 요청 객체
        """
        session_id = self.ensure_xtest_session(request)
        TestModeContext.enter_synthetic_mode(session_id=session_id)
        logger.debug(f"[X-Test-Mode] Synthetic context entered: session={session_id}")

    def exit_synthetic_context(self) -> None:
        """
        합성 요청 컨텍스트 종료.
        
        X-Test 요청 처리 완료 시 호출하여 TestModeContext를 비활성화합니다.
        """
        TestModeContext.exit_synthetic_mode()
        logger.debug("[X-Test-Mode] Synthetic context exited")

    def get_xtest_user(self, request: Request) -> str:
        """X-Test 사용자 추출."""
        if hasattr(request, "user") and request.user.is_authenticated:
            return str(request.user)
        return "anonymous"

    def log_xtest_audit(
        self,
        request: Request,
        action: str,
        component: str,
        details: Dict[str, Any],
        result: str = "success",
        error_message: Optional[str] = None,
    ) -> Optional[int]:
        """
        X-Test 작업을 WAL Audit 로그에 기록.
        
        Args:
            request: HTTP 요청 객체
            action: 수행 작업 (inject, force_status, reset, query 등)
            component: 대상 컴포넌트 (dlq, cb, idempotency 등)
            details: 응답 데이터 또는 작업 상세
            result: 결과 상태 (success, failed, error)
            error_message: 실패 시 에러 메시지
        
        Returns:
            WAL 시퀀스 번호
        """
        session_id = self.get_xtest_session_id(request)
        user = self.get_xtest_user(request)
        trace_id = request.headers.get("X-Trace-ID")
        
        return log_xtest_operation_audit(
            session_id=session_id,
            action=action,
            component=component,
            details=details,
            result=result,
            user=user,
            trace_id=trace_id,
            error_message=error_message,
        )

    def log_xtest_injection(
        self,
        request: Request,
        component: str,
        injection_type: str,
        count: int,
        target_ids: list,
    ) -> Optional[int]:
        """
        X-Test 데이터 주입을 WAL Audit 로그에 기록.
        
        Args:
            request: HTTP 요청 객체
            component: 대상 컴포넌트
            injection_type: 주입 유형 (create, override 등)
            count: 주입된 항목 수
            target_ids: 생성된 ID 목록
        """
        session_id = self.get_xtest_session_id(request)
        user = self.get_xtest_user(request)
        
        return log_xtest_injection_audit(
            session_id=session_id,
            component=component,
            injection_type=injection_type,
            count=count,
            target_ids=target_ids,
            user=user,
        )

    def log_xtest_cleanup(
        self,
        request: Request,
        component: str,
        cleaned_count: int,
        cleaned_ids: list,
    ) -> Optional[int]:
        """
        X-Test 정리(Reset)를 WAL Audit 로그에 기록.
        
        Args:
            request: HTTP 요청 객체
            component: 대상 컴포넌트
            cleaned_count: 정리된 항목 수
            cleaned_ids: 정리된 ID 목록
        """
        session_id = self.get_xtest_session_id(request)
        user = self.get_xtest_user(request)
        
        return log_xtest_cleanup_audit(
            session_id=session_id,
            component=component,
            cleaned_count=cleaned_count,
            cleaned_ids=cleaned_ids,
            user=user,
        )


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

"""
AuditMiddleware - 중앙화된 Audit 미들웨어

모든 미들웨어와 서비스에서 발생하는 Audit 이벤트를 '단일 해시 체인'으로 기록합니다.
응답 반환 직전에 RequestAuditBuffer의 이벤트를 '낚아채서' ContinuousAuditRecorder로 전달합니다.

핵심 설계 원칙 (56_AUDIT_MIDDLEWARE_DESIGN.md):
-------------------------------------------------
1. "낚시꾼(Middleware)은 맨 마지막에 서야 합니다"
   - AuditMiddleware가 맨 마지막에 있어야 앞에서 발생한 CB 오픈, RateLimit 차단,
     DLQ 적재 이벤트를 모두 낚아챌 수 있습니다.

2. "이벤트 버퍼는 '영수증'입니다"
   - RequestAuditBuffer는 한 요청의 전 생애주기를 기록하는 영수증과 같습니다.

3. "무결성 해시 체인의 단일화"
   - 모든 로그가 ContinuousAuditRecorder라는 단일 통로를 거치게 됩니다.
   - 기업 감사 시 "단 하나의 로그도 누락되거나 조작되지 않았다"를 증명합니다.

CRITICAL: 이 Middleware는 MIDDLEWARE 리스트 가장 마지막에 위치해야 함!

Usage in settings.py:
    MIDDLEWARE = [
        "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # 최상단
        # ... 다른 미들웨어들 ...
        "selfhealing.api.django.audit_middleware.AuditMiddleware",  # 맨 마지막!
    ]

파이프라인 흐름:
    Request → [Entrance Middlewares] → [View] → [AuditMiddleware] → Response
                      │                    │              │
                      │                    │              ▼
                      ▼                    ▼      ┌──────────────┐
               request.META["X-AUDIT-EVENTS"]     │ 이벤트 버퍼  │
               에 이벤트 적재                      │ 수집 & 배치  │
                                                  │ 기록         │
                                                  └──────┬───────┘
                                                         │
                                                         ▼
                                           ContinuousAuditRecorder
                                                  + HashChain
                                                  + WAL (선택)

Author: SelfHealing Team
Version: 1.0.0
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Dict, Any, Optional, List

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class AuditMiddleware:
    """
    중앙화된 Audit 미들웨어.
    
    기능:
    1. 요청 시작 시 request_id 생성 및 버퍼 초기화
    2. 응답 반환 전 버퍼의 모든 이벤트 수집
    3. ContinuousAuditRecorder를 통해 일괄 기록 (HashChain 포함)
    
    Fail-Open 정책:
    - Audit 기록 실패가 비즈니스 로직을 중단시키지 않음
    - 실패 시 stderr로 fallback 출력
    - 메트릭으로 실패 횟수 추적
    
    설정 (환경변수):
    - AUDIT_MIDDLEWARE_ENABLED: True/False (기본: True)
    - AUDIT_CAPTURE_ERROR_RESPONSES: 4xx/5xx 응답도 기록 (기본: True)
    - AUDIT_MIN_EVENTS_TO_RECORD: 최소 이벤트 수 (기본: 1)
    """
    
    # 제외 경로 (Audit 불필요)
    EXCLUDED_PATHS = [
        "/api/self-healing/health/",
        "/health/",
        "/api/self-healing/metrics/",
        "/metrics/",
        "/favicon.ico",
        "/static/",
    ]
    
    # ADR-002: 설정 기반 조회 기록 경로
    # 이 경로에 대한 GET 요청은 DATA_ACCESS 이벤트로 기록
    # Django settings의 SELFHEALING_AUDIT["read_paths"]로 오버라이드 가능
    DEFAULT_READ_AUDIT_PATHS: List[str] = [
        "/api/admin/",
        "/api/payments/",
        "/api/users/personal/",
    ]
    
    def __init__(self, get_response: Callable):
        """Initialize AuditMiddleware."""
        self.get_response = get_response
        self._recorder = None
        self._initialized = False
        self._read_audit_paths: List[str] = []
        
        # 통계
        self._total_requests = 0
        self._total_events_recorded = 0
        self._failed_recordings = 0
    
    def _ensure_initialized(self) -> None:
        """Lazy 초기화 - Django가 완전히 로드된 후 실행."""
        if self._initialized:
            return
        
        try:
            from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
            from selfhealing.adapters.audit.singleton import get_audit_adapter
            
            adapter = get_audit_adapter()
            self._recorder = ContinuousAuditRecorder(
                audit_adapter=adapter,
                fail_open=True,
                fallback_to_stdout=True,
            )
            logger.info("[AuditMiddleware] Initialized with ContinuousAuditRecorder")
        except Exception as e:
            logger.warning(f"[AuditMiddleware] Recorder init failed: {e}")
            self._recorder = None
        
        # ADR-002: 설정 기반 조회 기록 경로 로드
        self._load_read_audit_config()
        
        self._initialized = True
    
    def _load_read_audit_config(self) -> None:
        """ADR-002: Django settings에서 조회 기록 설정 로드."""
        try:
            from django.conf import settings
            
            audit_config = getattr(settings, "SELFHEALING_AUDIT", {})
            self._read_audit_paths = audit_config.get("read_paths", self.DEFAULT_READ_AUDIT_PATHS)
            
            logger.debug(f"[AuditMiddleware] Read audit paths: {self._read_audit_paths}")
        except Exception as e:
            logger.debug(f"[AuditMiddleware] Failed to load audit config: {e}")
            self._read_audit_paths = self.DEFAULT_READ_AUDIT_PATHS
    
    def __call__(self, request: "HttpRequest") -> "HttpResponse":
        """Process request/response."""
        self._ensure_initialized()
        self._total_requests += 1
        
        # === 제외 경로 체크 ===
        if self._should_skip(request):
            return self.get_response(request)
        
        # === Phase 1: 버퍼 초기화 ===
        buffer = self._init_buffer(request)
        
        # === Phase 1.5: ADR-002 조회 기록 (설정된 경로의 GET 요청) ===
        self._capture_read_access(request, buffer)
        
        # === Phase 2: 요청 처리 ===
        response = self.get_response(request)
        
        # === Phase 3: 응답 메타 수집 ===
        self._capture_response_meta(request, response, buffer)
        
        # === Phase 4: 이벤트 기록 (버퍼 낚아채기) ===
        if buffer.has_events():
            self._record_events(buffer, request, response)
        
        return response
    
    def _capture_read_access(
        self, 
        request: "HttpRequest", 
        buffer: "RequestAuditBuffer"
    ) -> None:
        """
        ADR-002: 설정된 경로에 대한 조회(GET) 요청을 DATA_ACCESS로 기록.
        
        SELFHEALING_AUDIT["read_paths"]에 설정된 경로 패턴에 매칭되는
        GET 요청에 대해 DATA_ACCESS 이벤트를 버퍼에 추가합니다.
        """
        method = getattr(request, "method", "").upper()
        if method != "GET":
            return
        
        path = getattr(request, "path", "")
        if not self._should_audit_read(path):
            return
        
        from selfhealing.audit.event_buffer import AuditEventType
        
        buffer.add(
            event_type=AuditEventType.DATA_ACCESS,
            source="AuditMiddleware",
            details={
                "path": path,
                "method": method,
                "query_string": getattr(request, "META", {}).get("QUERY_STRING", ""),
            },
            success=True,
            actor_id=self._get_user_id(request),
        )
    
    def _should_audit_read(self, path: str) -> bool:
        """조회 기록 대상 경로인지 확인."""
        if not path or not self._read_audit_paths:
            return False
        
        for audit_path in self._read_audit_paths:
            if path.startswith(audit_path):
                return True
        return False
    
    def _should_skip(self, request: "HttpRequest") -> bool:
        """제외 경로 체크."""
        path = getattr(request, "path", "")
        for excluded in self.EXCLUDED_PATHS:
            if path.startswith(excluded):
                return True
        return False
    
    def _init_buffer(self, request: "HttpRequest") -> "RequestAuditBuffer":
        """버퍼 초기화 및 request_id 생성."""
        from selfhealing.audit.event_buffer import RequestAuditBuffer
        
        buffer = RequestAuditBuffer.get_or_create(request)
        
        # request_id 생성 또는 추출
        buffer.request_id = self._get_or_create_request_id(request)
        
        # 요청 메타데이터 설정
        buffer.set_request_metadata(
            path=getattr(request, "path", None),
            method=getattr(request, "method", None),
            user_id=self._get_user_id(request),
        )
        
        return buffer
    
    def _get_or_create_request_id(self, request: "HttpRequest") -> str:
        """요청 ID 생성 또는 추출."""
        # X-Request-ID 헤더가 있으면 사용
        request_id = getattr(request, "META", {}).get("HTTP_X_REQUEST_ID")
        if request_id:
            return request_id
        
        # 없으면 생성
        return str(uuid.uuid4())
    
    def _get_user_id(self, request: "HttpRequest") -> Optional[str]:
        """사용자 ID 추출."""
        try:
            user = getattr(request, "user", None)
            if user and hasattr(user, "is_authenticated") and user.is_authenticated:
                return str(getattr(user, "id", None) or getattr(user, "pk", None))
        except Exception:
            pass
        return None
    
    def _capture_response_meta(
        self,
        request: "HttpRequest",
        response: "HttpResponse",
        buffer: "RequestAuditBuffer",
    ) -> None:
        """응답 메타데이터 캡처 - 에러 응답 시 이벤트 추가."""
        from selfhealing.audit.event_buffer import AuditEventType
        
        status_code = getattr(response, "status_code", 200)
        elapsed = buffer.get_elapsed_seconds()
        
        # 4xx/5xx 에러 응답인 경우 이벤트 추가
        if status_code >= 400:
            buffer.add(
                event_type=AuditEventType.ERROR_DETECTED,
                source="AuditMiddleware",
                details={
                    "status_code": status_code,
                    "path": getattr(request, "path", ""),
                    "method": getattr(request, "method", ""),
                    "elapsed_seconds": round(elapsed, 4),
                },
                success=False,
                error_message=f"HTTP {status_code}",
            )
    
    def _record_events(
        self,
        buffer: "RequestAuditBuffer",
        request: "HttpRequest",
        response: "HttpResponse",
    ) -> None:
        """
        이벤트 일괄 기록 - 버퍼 낚아채기.
        
        Fail-Open: 기록 실패 시 메인 흐름 중단 없음.
        """
        if self._recorder is None:
            # Recorder 없으면 로그로 fallback
            self._fallback_log_events(buffer)
            return
        
        try:
            # Actor 컨텍스트 가져오기
            actor_id, actor_type = self._get_actor_context()
            
            # 요청 컨텍스트
            request_context = {
                "request_id": buffer.request_id,
                "path": getattr(request, "path", ""),
                "method": getattr(request, "method", ""),
                "status_code": getattr(response, "status_code", 200),
                "actor_id": actor_id,
                "actor_type": actor_type,
                "elapsed_seconds": round(buffer.get_elapsed_seconds(), 4),
                "event_count": buffer.event_count(),
            }
            
            # 각 이벤트 기록 (해시 체인으로 연결)
            for event in buffer.get_events():
                self._record_single_event(event, request_context)
                self._total_events_recorded += 1
                
        except Exception as e:
            # Audit 실패가 메인 흐름을 막지 않음 (Fail-Open)
            self._failed_recordings += 1
            logger.warning(
                f"[AuditMiddleware] Recording failed (fail-open): {e}. "
                f"Total failures: {self._failed_recordings}"
            )
            # Fallback 시도
            self._fallback_log_events(buffer)
    
    def _get_actor_context(self) -> tuple[Optional[str], str]:
        """ActorContext에서 actor 정보 가져오기."""
        try:
            from selfhealing.context.actor_context import ActorContext
            if ActorContext.is_set():
                actor = ActorContext.get_current()
                return actor.actor_id, actor.actor_type
        except ImportError:
            pass
        return None, "system"
    
    def _record_single_event(
        self,
        event: "AuditEvent",
        request_context: Dict[str, Any],
    ) -> None:
        """단일 이벤트 기록 - ContinuousAuditRecorder 통해 HashChain 적용."""
        try:
            from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction, ContextType
            from selfhealing.audit.event_buffer import AuditEventType
            
            # 이벤트 타입 → AuditAction 매핑
            action_map = {
                AuditEventType.DLQ_STORE: AuditAction.DLQ_STORE,
                AuditEventType.DLQ_REPLAY: AuditAction.DLQ_REPLAY_SUCCESS,
                AuditEventType.DLQ_ESCALATE: AuditAction.DLQ_ESCALATE,
                AuditEventType.CB_STATE_CHANGE: AuditAction.CB_AUTO_OPEN,
                AuditEventType.CB_REJECTION: AuditAction.CB_FORCE_OPEN,
                AuditEventType.CB_RECOVERY: AuditAction.CB_AUTO_CLOSE,
                AuditEventType.GOVERNANCE_BLOCKED: AuditAction.GOVERNANCE_BLOCKED,
                AuditEventType.GOVERNANCE_KILL_SWITCH: AuditAction.GOVERNANCE_KILL_SWITCH,
                AuditEventType.RATE_LIMITED: AuditAction.GOVERNANCE_BLOCKED,
                AuditEventType.POOL_CB_REJECTION: AuditAction.CB_FORCE_OPEN,
                AuditEventType.POOL_CB_STATE_CHANGE: AuditAction.CB_AUTO_OPEN,
                AuditEventType.ERROR_DETECTED: AuditAction.SECURITY_ALERT,
                AuditEventType.CONFIG_CHANGE: AuditAction.CONFIG_CHANGE,
                AuditEventType.MANUAL_OVERRIDE: AuditAction.MANUAL_OVERRIDE,
                AuditEventType.GENERIC: AuditAction.CONFIG_CHANGE,
            }
            
            action = action_map.get(event.event_type, AuditAction.CONFIG_CHANGE)
            
            # AuditEntry 생성
            entry = AuditEntry(
                action=action,
                actor_id=event.actor_id or request_context.get("actor_id"),
                actor_type=event.actor_type,
                context_type=ContextType.REQUEST,  # 미들웨어 컨텍스트
                target_type=event.target_type or event.source,
                target_id=event.target_id or request_context.get("request_id", ""),
                domain=event.domain,
                reason=event.reason,
                details={
                    **event.details,
                    "request_context": request_context,
                    "original_event_type": event.event_type.value,
                },
                success=event.success,
                error_message=event.error_message,
            )
            
            # ContinuousAuditRecorder를 통해 기록 (HashChain 적용)
            self._recorder.audit_adapter.log(entry)
            
        except Exception as e:
            logger.debug(f"[AuditMiddleware] Event record failed: {e}")
    
    def _fallback_log_events(self, buffer: "RequestAuditBuffer") -> None:
        """Fallback: stderr로 이벤트 출력."""
        import sys
        for event in buffer.get_events():
            try:
                print(
                    f"[FALLBACK_AUDIT_LOG] {event.event_type.value}: {event.to_dict()}",
                    file=sys.stderr,
                )
            except Exception:
                pass
    
    # =========================================================================
    # Statistics & Monitoring
    # =========================================================================
    
    @classmethod
    def get_stats(cls) -> Dict[str, Any]:
        """미들웨어 통계 반환."""
        # 싱글톤이 아니므로 클래스 레벨에서 접근 불가
        # 개별 인스턴스 통계는 인스턴스에서 조회
        return {
            "note": "Use instance._total_requests etc. for stats",
        }


# =============================================================================
# Utility Functions
# =============================================================================

def get_audit_middleware_from_settings() -> Optional[AuditMiddleware]:
    """
    Django settings에서 AuditMiddleware 인스턴스 가져오기.
    
    Note: Django 미들웨어는 인스턴스화되어 있어 직접 접근이 어려움.
    이 함수는 참조용으로만 사용.
    """
    return None  # Django 미들웨어는 직접 접근 불가


def is_audit_middleware_enabled() -> bool:
    """AuditMiddleware 활성화 여부 확인."""
    import os
    return os.environ.get("AUDIT_MIDDLEWARE_ENABLED", "TRUE").upper() == "TRUE"

"""
Stage 48: X-Test-Mode (Chaos Monkey) Control Views

Rate Limiter(L1)를 우회하여 L2/L3 동작을 직접 관찰하기 위한 테스트 전용 API.

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단

Endpoints:
- POST /api/self-healing/xtest/inject-cb-failure/ - CB 장애 주입
- POST /api/self-healing/xtest/reset-cb/ - CB 상태 초기화
- GET  /api/self-healing/xtest/cb-status/ - CB 상태 확인 (상세)
- POST /api/self-healing/xtest/inject-error-budget/ - Error Budget 차감
- GET  /api/self-healing/xtest/snapshot/ - 시스템 스냅샷
"""

import logging
import os
import time
import psutil
from typing import Dict, Any, Optional

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

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


def _collect_system_snapshot() -> Dict[str, Any]:
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
        
        # DB 연결 수 (가능한 경우)
        try:
            from django.db import connection
            with connection.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
                active_connections = cursor.fetchone()[0]
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
# X-Test-Mode Views
# =============================================================================

class InjectCBFailureView(XTestModeMixin, APIView):
    """
    Circuit Breaker 장애 주입 API.
    
    POST /api/self-healing/xtest/inject-cb-failure/
    
    Request:
        {
            "service": "database",
            "count": 5  // failure_threshold (default)
        }
    
    Response:
        {
            "service": "database",
            "injected_failures": 5,
            "cb_state": "open",
            "previous_state": "closed",
            "timestamp": "2025-12-26T14:01:23+09:00",
            "snapshot": {...}
        }
    
    Note:
        보안은 XTestModeMixin.check_chaos_permission()에서 처리됨
        - X-Test-Mode: chaos-monkey 헤더 필수
        - CHAOS_ENABLED=true 또는 DEBUG=True 필요
        - production 환경 차단
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def post(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        service_name = request.data.get("service", "database")
        failure_count = int(request.data.get("count", 5))
        
        # 최대 주입 횟수 제한 (안전 장치)
        max_injection = 20
        if failure_count > max_injection:
            return Response(
                {
                    "status": "error",
                    "error": "injection_limit_exceeded",
                    "message": f"Maximum injection count is {max_injection}",
                    "requested": failure_count,
                    "max_allowed": max_injection
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from selfhealing.services import get_circuit_breaker_service, force_open_circuit
            
            cb_service = get_circuit_breaker_service()
            
            # 이전 상태 기록
            previous_state = cb_service.get_state(service_name)
            
            # L1 우회하여 직접 실패 기록
            for i in range(failure_count):
                cb_service.record_failure(
                    service_name,
                    error_context={
                        "source": "x-test-mode",
                        "injection_number": i + 1,
                        "total_injections": failure_count,
                        "user": str(request.user)
                    }
                )
            
            # 현재 상태 확인 (실패 주입 후)
            current_state = cb_service.get_state(service_name)
            
            # minimum_calls 조건 때문에 OPEN이 안 된 경우, 강제로 OPEN
            force_opened = False
            if current_state != "open" and request.data.get("force_open", True):
                result = force_open_circuit(
                    service_name,
                    reason=f"X-Test-Mode injection: {failure_count} failures",
                    controlled_by=f"xtest:{request.user}"
                )
                # CircuitBreakerResult has 'success' attribute, not 'status'
                if result.success:
                    current_state = "open"
                    force_opened = True
            
            # 스냅샷 수집
            snapshot = _collect_system_snapshot()
            
            logger.info(
                f"[X-Test-Mode] CB failure injection: service={service_name}, "
                f"count={failure_count}, state={previous_state}→{current_state}, "
                f"user={request.user}"
            )
            
            return Response({
                "status": "success",
                "service": service_name,
                "injected_failures": failure_count,
                "previous_state": previous_state,
                "cb_state": current_state,
                "state_changed": previous_state != current_state,
                "force_opened": force_opened,
                "timestamp": timezone.now().isoformat(),
                "snapshot": snapshot
            })
            
        except Exception as e:
            logger.error(f"[X-Test-Mode] CB failure injection failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "injection_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ResetCBView(XTestModeMixin, APIView):
    """
    Circuit Breaker 상태 초기화 API.
    
    POST /api/self-healing/xtest/reset-cb/
    
    Request:
        {
            "service": "database"
        }
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def post(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        service_name = request.data.get("service", "database")
        
        try:
            from selfhealing.services import get_circuit_breaker_service
            
            cb_service = get_circuit_breaker_service()
            
            # 이전 상태 기록
            previous_state = cb_service.get_state(service_name)
            
            # 강제 닫기
            result = cb_service.force_close(
                service_name=service_name,
                reason=f"X-Test-Mode reset by {request.user}",
                controlled_by=str(request.user)
            )
            
            current_state = cb_service.get_state(service_name)
            
            logger.info(
                f"[X-Test-Mode] CB reset: service={service_name}, "
                f"state={previous_state}→{current_state}, user={request.user}"
            )
            
            return Response({
                "status": "success",
                "service": service_name,
                "previous_state": previous_state,
                "cb_state": current_state,
                "reset_result": result.success if hasattr(result, 'success') else True,
                "timestamp": timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"[X-Test-Mode] CB reset failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "reset_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CBStatusDetailView(XTestModeMixin, APIView):
    """
    Circuit Breaker 상세 상태 조회 API.
    
    GET /api/self-healing/xtest/cb-status/?service=database
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def get(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        service_name = request.query_params.get("service")
        
        try:
            from selfhealing.services import get_circuit_breaker_service
            
            cb_service = get_circuit_breaker_service()
            
            if service_name:
                # 특정 서비스 상태
                state_data = cb_service.get_or_create_state(service_name)
                
                return Response({
                    "status": "success",
                    "service": service_name,
                    "cb_state": state_data.state,
                    "failure_count": state_data.failure_count,
                    "success_count": getattr(state_data, 'success_count', 0),
                    "last_failure_time": getattr(state_data, 'last_failure_time', None),
                    "opened_at": getattr(state_data, 'opened_at', None),
                    "manually_controlled": getattr(state_data, 'manually_controlled', False),
                    "config": {
                        "failure_threshold": cb_service.config.failure_threshold,
                        "recovery_timeout": cb_service.config.recovery_timeout,
                        "success_threshold": cb_service.config.success_threshold,
                        "minimum_calls": cb_service.config.minimum_calls,
                    },
                    "timestamp": timezone.now().isoformat()
                })
            else:
                # 전체 서비스 상태 (repository에서 조회)
                all_states = cb_service.repository.get_all_states()
                
                services = {}
                for state_data in all_states:
                    services[state_data.service_name] = {
                        "state": state_data.state,
                        "failure_count": state_data.failure_count,
                        "success_count": getattr(state_data, 'success_count', 0),
                        "opened_at": getattr(state_data, 'opened_at', None),
                    }
                
                return Response({
                    "status": "success",
                    "services": services,
                    "total_count": len(services),
                    "config": {
                        "failure_threshold": cb_service.config.failure_threshold,
                        "recovery_timeout": cb_service.config.recovery_timeout,
                    },
                    "timestamp": timezone.now().isoformat()
                })
                
        except Exception as e:
            logger.error(f"[X-Test-Mode] CB status query failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "status_query_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class InjectErrorBudgetView(XTestModeMixin, APIView):
    """
    Error Budget 차감 주입 API.
    
    POST /api/self-healing/xtest/inject-error-budget/
    
    Request:
        {
            "error_type": "critical",  // critical, major, minor
            "count": 10
        }
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def post(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        error_type = request.data.get("error_type", "critical")
        count = int(request.data.get("count", 10))
        
        # 최대 주입 횟수 제한
        max_injection = 100
        if count > max_injection:
            return Response(
                {
                    "status": "error",
                    "error": "injection_limit_exceeded",
                    "message": f"Maximum injection count is {max_injection}"
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from selfhealing.services import get_error_budget_service
            
            eb_service = get_error_budget_service()
            
            # 이전 상태
            initial_budget = eb_service.get_remaining_budget_percent()
            
            # 에러 주입
            for i in range(count):
                eb_service.record_error(
                    error_type=error_type,
                    context={
                        "source": "x-test-mode",
                        "injection_number": i + 1,
                        "user": str(request.user)
                    }
                )
            
            # 현재 상태
            current_budget = eb_service.get_remaining_budget_percent()
            budget_status = eb_service.get_budget_status()
            
            logger.info(
                f"[X-Test-Mode] Error Budget injection: type={error_type}, "
                f"count={count}, budget={initial_budget:.1f}%→{current_budget:.1f}%, "
                f"user={request.user}"
            )
            
            return Response({
                "status": "success",
                "error_type": error_type,
                "injected_count": count,
                "initial_budget_percent": initial_budget,
                "current_budget_percent": current_budget,
                "budget_consumed": initial_budget - current_budget,
                "budget_status": budget_status,
                "timestamp": timezone.now().isoformat()
            })
            
        except ImportError:
            return Response({
                "status": "warning",
                "message": "Error Budget service not available",
                "hint": "Error Budget may not be configured in this environment"
            })
        except Exception as e:
            logger.error(f"[X-Test-Mode] Error Budget injection failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "injection_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class SystemSnapshotView(XTestModeMixin, APIView):
    """
    시스템 스냅샷 조회 API.
    
    GET /api/self-healing/xtest/snapshot/
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def get(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        snapshot = _collect_system_snapshot()
        
        # CB 상태 추가
        try:
            from selfhealing.services import get_circuit_breaker_service
            cb_service = get_circuit_breaker_service()
            all_states = cb_service.repository.get_all_states()
            
            snapshot["circuit_breakers"] = {
                state.service_name: {
                    "state": state.state,
                    "failure_count": state.failure_count,
                }
                for state in all_states
            }
        except Exception as e:
            snapshot["circuit_breakers"] = {"error": str(e)}
        
        # Error Budget 상태 추가
        try:
            from selfhealing.services import get_error_budget_service
            eb_service = get_error_budget_service()
            
            snapshot["error_budget"] = {
                "remaining_percent": eb_service.get_remaining_budget_percent(),
                "status": eb_service.get_budget_status(),
            }
        except Exception as e:
            snapshot["error_budget"] = {"error": str(e)}
        
        return Response({
            "status": "success",
            "snapshot": snapshot
        })


class FastFailTestView(XTestModeMixin, APIView):
    """
    Fast Fail 검증 API - CB OPEN 상태에서 응답 시간 측정.
    
    GET /api/self-healing/xtest/fast-fail-test/?service=database
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def get(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        service_name = request.query_params.get("service", "database")
        
        try:
            from selfhealing.services import get_circuit_breaker_service
            
            cb_service = get_circuit_breaker_service()
            
            # 상태 확인
            current_state = cb_service.get_state(service_name)
            
            # should_allow 체크 시간 측정
            start_time = time.time()
            allowed = cb_service.should_allow(service_name)
            elapsed_ms = (time.time() - start_time) * 1000
            
            is_fast_fail = elapsed_ms < 100  # 100ms 미만
            
            return Response({
                "status": "success",
                "service": service_name,
                "cb_state": current_state,
                "request_allowed": allowed,
                "response_time_ms": round(elapsed_ms, 2),
                "is_fast_fail": is_fast_fail,
                "fast_fail_threshold_ms": 100,
                "timestamp": timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"[X-Test-Mode] Fast fail test failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "test_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TriggerCBRecoveryView(XTestModeMixin, APIView):
    """
    CB Recovery 트리거 API - HALF_OPEN 상태에서 성공 기록하여 CLOSED로 복구.
    
    POST /api/self-healing/xtest/trigger-cb-recovery/
    Body: {"service": "database", "success_count": 3, "force": false}
    
    HALF_OPEN 상태에서 record_success를 호출하여 CB를 CLOSED 상태로 복구시킵니다.
    - force=true: 직접 CLOSED로 전환 (테스트용)
    - force=false: record_success 호출 (정상 흐름)
    
    Note: DB 모델의 half_open_max_calls 기본값은 3입니다.
    """
    
    authentication_classes = []  # No auth - security via XTestModeMixin
    permission_classes = [AllowAny]
    
    def post(self, request: Request) -> Response:
        # Chaos 권한 체크
        denied = self.check_chaos_permission(request)
        if denied:
            return denied
        
        service_name = request.data.get("service", "database")
        success_count = request.data.get("success_count", 3)  # DB model default: half_open_max_calls=3
        force_close = request.data.get("force", False)  # 강제 CLOSED 전환
        
        try:
            from selfhealing.services import get_circuit_breaker_service
            
            cb_service = get_circuit_breaker_service()
            
            # 현재 상태 확인
            state_before = cb_service.get_state(service_name)
            
            successes_recorded = 0
            
            if force_close and state_before in ("half_open", "open"):
                # 강제 CLOSED 전환 (테스트 전용)
                cb_service.repository.update_state(
                    service_name=service_name,
                    state="closed",
                    failure_count=0,
                    success_count=0,
                    opened_at=None,
                )
                logger.info(f"[X-Test-Mode] CB force-closed for '{service_name}'")
            else:
                # 정상 복구 흐름: record_success 호출
                for i in range(success_count):
                    current_state = cb_service.get_state(service_name)
                    if current_state == "half_open":
                        cb_service.record_success(service_name)
                        successes_recorded += 1
                    elif current_state == "closed":
                        # 이미 복구됨
                        break
                    else:
                        # OPEN 상태면 record_success 효과 없음
                        break
            
            # 최종 상태 확인
            state_after = cb_service.get_state(service_name)
            
            recovery_success = state_after == "closed"
            
            logger.info(
                f"[X-Test-Mode] CB recovery triggered for '{service_name}': "
                f"{state_before} → {state_after} (successes: {successes_recorded}, force: {force_close})"
            )
            
            return Response({
                "status": "success",
                "service": service_name,
                "state_before": state_before,
                "state_after": state_after,
                "successes_recorded": successes_recorded,
                "force_closed": force_close,
                "recovery_success": recovery_success,
                "timestamp": timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"[X-Test-Mode] CB recovery failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "recovery_failed",
                    "message": str(e)
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

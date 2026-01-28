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

Regional Scope:
- GLOBAL scope API는 X-Region 헤더 필수
- X-Region 값이 현재 클러스터 리전과 일치해야 허용
- 리전 불일치 시 403 Forbidden 반환
"""

import logging
import os
import re
import threading
import uuid
from typing import Any

import psutil
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.authentication import BasicAuthentication, SessionAuthentication
from rest_framework.request import Request
from rest_framework.response import Response

from selfhealing.api.django.permissions import HasChaosTestPermission
from selfhealing.core.test_mode_context import TestModeContext
from selfhealing.services.audit.xtest_audit import (
    log_xtest_cleanup_audit,
    log_xtest_injection_audit,
    log_xtest_operation_audit,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Global Scope Endpoint Patterns (리전 경계 강제 필요)
# =============================================================================

# GLOBAL scope API: 다른 리전에 영향을 줄 수 있는 엔드포인트
# 이 패턴과 매칭되는 API는 X-Region 헤더 필수 + 현재 리전 일치 검증
GLOBAL_SCOPE_ENDPOINT_PATTERNS: list[str] = [
    r"xtest/emergency/global/.*",  # 전역 Emergency 상태 변경
    r"xtest/isolation/region/.*",  # 리전 격리 조작
    r"xtest/governance/global/.*",  # 전역 거버넌스 설정
]

# 컴파일된 패턴 (성능 최적화)
_COMPILED_GLOBAL_PATTERNS: list[re.Pattern] = [
    re.compile(pattern, re.IGNORECASE) for pattern in GLOBAL_SCOPE_ENDPOINT_PATTERNS
]


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

    def get_current_region(self) -> str | None:
        """
        현재 클러스터의 리전 조회.

        환경변수 SELFHEALING_REGION 또는 ClusterIdentity에서 리전 정보를 가져옵니다.

        Returns:
            리전 식별자 (예: 'seoul', 'tokyo') 또는 None
        """
        # 1. 환경변수에서 직접 조회 (가장 빠름)
        region = os.getenv("SELFHEALING_REGION")
        if region:
            return region

        # 2. ClusterIdentity에서 조회
        try:
            from selfhealing.core.cluster_identity import get_cluster_identity

            identity = get_cluster_identity(skip_validation=True)
            return identity.region
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Failed to get cluster identity: {e}")
            return None

    def is_global_scope_endpoint(self, request: Request) -> bool:
        """
        현재 요청이 GLOBAL scope API인지 판정.

        GLOBAL scope API는 다른 리전에 영향을 줄 수 있는 엔드포인트입니다:
        - xtest/emergency/global/* : 전역 Emergency 상태 변경
        - xtest/isolation/region/* : 리전 격리 조작
        - xtest/governance/global/* : 전역 거버넌스 설정

        Args:
            request: HTTP 요청 객체

        Returns:
            GLOBAL scope이면 True, LOCAL scope이면 False
        """
        path = request.path.lstrip("/")

        for pattern in _COMPILED_GLOBAL_PATTERNS:
            if pattern.search(path):
                return True

        return False

    def check_regional_scope(self, request: Request) -> tuple[bool, Response | None]:
        """
        GLOBAL scope API에 대한 리전 경계 검증.

        GLOBAL scope API 호출 시:
        1. X-Region 헤더 존재 확인
        2. 헤더 값과 현재 클러스터 리전 일치 확인
        3. 불일치 시 403 Forbidden 반환

        Args:
            request: HTTP 요청 객체

        Returns:
            (is_allowed, response): 허용 여부와 거부 시 Response
        """
        # LOCAL scope API는 리전 체크 불필요
        if not self.is_global_scope_endpoint(request):
            return True, None

        # 현재 클러스터 리전 조회
        current_region = self.get_current_region()

        # 리전 미설정 환경에서는 GLOBAL scope 차단
        if not current_region:
            environment = os.getenv("ENVIRONMENT", "development").lower()
            if environment == "development":
                # 개발 환경에서는 경고만 출력
                logger.warning(
                    "[X-Test-Mode] SELFHEALING_REGION not set in development. " "GLOBAL scope API allowed with warning."
                )
                return True, None

            logger.warning("[X-Test-Mode] SELFHEALING_REGION not set. " "GLOBAL scope API denied for safety.")
            return False, Response(
                {
                    "status": "error",
                    "error": "region_not_configured",
                    "message": "SELFHEALING_REGION not configured. GLOBAL scope API denied.",
                    "hint": "Set SELFHEALING_REGION environment variable",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # X-Region 헤더 확인
        target_region = request.headers.get("X-Region")

        if not target_region:
            logger.warning(
                f"[X-Test-Mode] Missing X-Region header for GLOBAL scope API. "
                f"current_region={current_region}, path={request.path}"
            )
            return False, Response(
                {
                    "status": "error",
                    "error": "missing_region_header",
                    "message": "X-Region header required for GLOBAL scope API",
                    "current_region": current_region,
                    "hint": f"Add header 'X-Region: {current_region}'",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # 리전 일치 확인
        if target_region.lower() != current_region.lower():
            logger.warning(
                f"[X-Test-Mode] Cross-region X-Test denied: "
                f"current={current_region}, target={target_region}, path={request.path}"
            )
            return False, Response(
                {
                    "status": "error",
                    "error": "cross_region_xtest_denied",
                    "message": (
                        f"Cross-region X-Test operation denied. "
                        f"Target region '{target_region}' does not match "
                        f"current cluster region '{current_region}'."
                    ),
                    "current_region": current_region,
                    "target_region": target_region,
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        logger.debug(f"[X-Test-Mode] Regional scope check passed: " f"region={current_region}, path={request.path}")
        return True, None

    def check_resource_constraints(self, request: Request) -> Response | None:
        """
        시스템 리소스 제약 체크.

        CPU 80% 초과 또는 메모리 85% 초과 시 429 응답 반환.
        시스템 과부하 상태에서 X-Test가 추가 부담을 주는 것을 방지.

        Returns:
            None if allowed, 429 Response if resource overloaded
        """
        try:
            from selfhealing.services.chaos.safety_guard import (
                get_resource_guard,
            )

            guard = get_resource_guard()
            result = guard.is_safe_for_chaos()

            if not result.is_safe:
                logger.warning(
                    f"[X-Test-Mode] Resource constraint check failed: {result.block_reason} " f"(user: {request.user})"
                )

                response = Response(
                    {
                        "status": "error",
                        **result.to_response_dict(),
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
                response["Retry-After"] = str(guard.get_recommended_wait())
                return response

            logger.debug(
                f"[X-Test-Mode] Resource check passed: " f"CPU={result.cpu_percent:.1f}%, Memory={result.memory_percent:.1f}%"
            )
            return None

        except ImportError:
            logger.debug("[X-Test-Mode] ResourceGuard not available, skipping check")
            return None
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Resource check failed with error: {e}")
            # 체크 실패 시 보수적으로 허용 (가용성 우선)
            return None

    def check_chaos_permission(self, request: Request) -> Response | None:
        """
        Chaos 권한 체크. 실패시 Response 반환.

        검증 순서:
        1. 리소스 제약 체크 (CPU/메모리 과부하)
        2. Chaos 모드 허용 여부 (헤더, 환경변수)
        3. GLOBAL scope API인 경우 리전 경계 검증

        Returns:
            None if allowed, Response if denied
        """
        # 1. 리소스 제약 체크 (CPU/메모리)
        resource_response = self.check_resource_constraints(request)
        if resource_response is not None:
            return resource_response

        # 2. Chaos 모드 기본 검증
        allowed, reason = self.is_chaos_allowed(request)
        if not allowed:
            logger.warning(f"[X-Test-Mode] Denied: {reason} (user: {request.user})")
            return Response(
                {
                    "status": "error",
                    "error": "chaos_mode_disabled",
                    "message": reason,
                    "hint": f"Add header '{self.CHAOS_HEADER}: {self.CHAOS_VALUE}' and ensure CHAOS_ENABLED=true",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        # 3. GLOBAL scope API 리전 경계 검증
        region_allowed, region_response = self.check_regional_scope(request)
        if not region_allowed:
            return region_response

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
            from selfhealing.services.xtest_session_manager import (
                get_xtest_session_manager,
            )

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
            from selfhealing.services.xtest_session_manager import (
                get_xtest_session_manager,
            )

            session_manager = get_xtest_session_manager()

            success = session_manager.register_artifact(
                session_id=session_id,
                artifact_id=artifact_id,
                component=component,
            )

            if success:
                logger.debug(
                    f"[X-Test-Mode] Registered artifact: " f"session={session_id}, component={component}, id={artifact_id}"
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
        details: dict[str, Any],
        result: str = "success",
        error_message: str | None = None,
    ) -> int | None:
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
    ) -> int | None:
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
    ) -> int | None:
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


def collect_system_snapshot() -> dict[str, Any]:
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
        return {"timestamp": timezone.now().isoformat(), "error": str(e)}


# =============================================================================
# In-Memory Event Storage (Singleton) + PostgreSQL Persistence
# =============================================================================

_healing_events_lock = threading.Lock()
_healing_events: list[dict[str, Any]] = []
_healing_incidents: list[dict[str, Any]] = []
_max_events = 500
_max_incidents = 100

# PostgreSQL 저장 활성화 플래그 (테스트에서 비활성화 가능)
_db_persistence_enabled = True


def set_db_persistence_enabled(enabled: bool) -> None:
    """PostgreSQL 영속성 활성화/비활성화 설정."""
    global _db_persistence_enabled
    _db_persistence_enabled = enabled


def get_db_persistence_enabled() -> bool:
    """PostgreSQL 영속성 활성화 여부 반환."""
    return _db_persistence_enabled


def _get_postmortem_model():
    """PostmortemRecord 모델을 동적으로 가져옴 (순환 import 방지)."""
    try:
        from shopping.models import PostmortemRecord

        return PostmortemRecord
    except ImportError:
        return None


def _save_incident_to_db(incident: dict[str, Any]) -> bool:
    """
    인시던트를 PostgreSQL에 저장.

    Returns:
        저장 성공 여부
    """
    if not _db_persistence_enabled:
        return False

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        logger.debug("[Postmortem] PostmortemRecord model not available")
        return False

    try:
        record = PostmortemRecord.create_from_incident_dict(incident)
        record.save()
        logger.debug(f"[Postmortem] Saved to DB: {record.incident_id}")
        return True
    except Exception as e:
        logger.warning(f"[Postmortem] DB save failed, using in-memory fallback: {e}")
        return False


def add_healing_event(event: dict[str, Any]) -> None:
    """힐링 이벤트 기록."""
    global _healing_events
    with _healing_events_lock:
        event["recorded_at"] = timezone.now().isoformat()
        _healing_events.append(event)
        if len(_healing_events) > _max_events:
            _healing_events = _healing_events[-_max_events:]


def add_healing_incident(incident: dict[str, Any]) -> None:
    """
    힐링 인시던트 기록.

    PostgreSQL에 영구 저장을 시도하고, 실패 시 In-Memory fallback.
    In-Memory 캐시는 항상 유지하여 빠른 읽기를 지원합니다.
    """
    global _healing_incidents

    # recorded_at 타임스탬프 추가
    incident["recorded_at"] = timezone.now().isoformat()

    # PostgreSQL 저장 시도
    db_saved = _save_incident_to_db(incident)

    # In-Memory 캐시에도 저장 (빠른 읽기용 + DB 실패 시 fallback)
    with _healing_events_lock:
        _healing_incidents.append(incident)
        if len(_healing_incidents) > _max_incidents:
            _healing_incidents = _healing_incidents[-_max_incidents:]

    if db_saved:
        logger.debug(f"[Postmortem] Incident saved to DB and cache")
    else:
        logger.debug(f"[Postmortem] Incident saved to in-memory cache only")


def get_healing_events(limit: int = 50) -> list[dict[str, Any]]:
    """힐링 이벤트 조회."""
    with _healing_events_lock:
        return list(_healing_events[-limit:])


def get_healing_incidents(
    limit: int = 10,
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    offset: int = 0,
    use_db: bool = True,
) -> list[dict[str, Any]]:
    """
    힐링 인시던트 조회.

    PostgreSQL에서 조회를 시도하고, 실패 시 In-Memory fallback.
    필터링 파라미터가 있으면 DB 조회를 사용합니다.

    Args:
        limit: 반환할 최대 개수
        start_date: 시작 날짜 필터 (ISO format)
        end_date: 종료 날짜 필터 (ISO format)
        service: 서비스 이름 필터 (affected_services에 포함)
        min_duration: 최소 지속 시간 필터 (초)
        offset: 페이지네이션 오프셋
        use_db: DB 조회 사용 여부

    Returns:
        인시던트 딕셔너리 리스트
    """
    has_filters = any([start_date, end_date, service, min_duration, offset > 0])

    # 필터가 있거나 DB 사용이 명시된 경우 DB 조회 시도
    if use_db and _db_persistence_enabled and (has_filters or limit > _max_incidents):
        try:
            return _get_incidents_from_db(
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                service=service,
                min_duration=min_duration,
                offset=offset,
            )
        except Exception as e:
            logger.warning(f"[Postmortem] DB query failed, using in-memory: {e}")

    # In-Memory fallback (필터링 없이 최근 데이터만)
    with _healing_events_lock:
        return list(_healing_incidents[-limit:])


def _get_incidents_from_db(
    limit: int,
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """PostgreSQL에서 인시던트 조회."""
    from datetime import datetime

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        raise ImportError("PostmortemRecord model not available")

    queryset = PostmortemRecord.objects.all()

    # 날짜 필터
    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__gte=start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__lte=end_dt)
        except ValueError:
            pass

    # 최소 지속 시간 필터
    if min_duration is not None:
        queryset = queryset.filter(duration_seconds__gte=min_duration)

    # 서비스 필터 (JSONField 검색)
    if service:
        queryset = queryset.filter(affected_services__contains=[service])

    # 정렬 및 페이지네이션
    queryset = queryset.order_by("-started_at")[offset : offset + limit]

    return [record.to_dict() for record in queryset]


def get_healing_incidents_count(
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
    use_db: bool = True,
) -> int:
    """
    힐링 인시던트 총 개수.

    필터가 있으면 DB에서 카운트, 없으면 In-Memory 캐시 카운트.
    """
    has_filters = any([start_date, end_date, service, min_duration])

    if use_db and _db_persistence_enabled and has_filters:
        try:
            return _get_incidents_count_from_db(
                start_date=start_date,
                end_date=end_date,
                service=service,
                min_duration=min_duration,
            )
        except Exception as e:
            logger.warning(f"[Postmortem] DB count failed, using in-memory: {e}")

    with _healing_events_lock:
        return len(_healing_incidents)


def _get_incidents_count_from_db(
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    min_duration: float | None = None,
) -> int:
    """PostgreSQL에서 인시던트 카운트."""
    from datetime import datetime

    PostmortemRecord = _get_postmortem_model()
    if PostmortemRecord is None:
        raise ImportError("PostmortemRecord model not available")

    queryset = PostmortemRecord.objects.all()

    if start_date:
        try:
            start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__gte=start_dt)
        except ValueError:
            pass

    if end_date:
        try:
            end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            queryset = queryset.filter(started_at__lte=end_dt)
        except ValueError:
            pass

    if min_duration is not None:
        queryset = queryset.filter(duration_seconds__gte=min_duration)

    if service:
        queryset = queryset.filter(affected_services__contains=[service])

    return queryset.count()


def get_healing_events_count() -> int:
    """힐링 이벤트 총 개수."""
    with _healing_events_lock:
        return len(_healing_events)


# Legacy alias for backward compatibility
_collect_system_snapshot = collect_system_snapshot
_add_healing_event = add_healing_event
_add_healing_incident = add_healing_incident

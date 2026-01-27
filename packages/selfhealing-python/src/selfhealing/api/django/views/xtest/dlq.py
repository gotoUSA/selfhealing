"""
X-Test-Mode DLQ (Dead Letter Queue) Views

DLQ 동작을 X-Test-Mode 환경에서 테스트하기 위한 API.

Endpoints:
- POST /api/self-healing/xtest/dlq/inject/ - 테스트용 DLQ 항목 생성
- GET  /api/self-healing/xtest/dlq/status/ - DLQ 현황 조회
- POST /api/self-healing/xtest/dlq/force-status/ - DLQ 상태 강제 변경
- POST /api/self-healing/xtest/dlq/reset/ - X-Test-Mode 생성 항목 초기화

Security:
- X-Test-Mode: chaos-monkey 헤더 필수
- DEBUG 또는 CHAOS_ENABLED 환경 변수 필요
- production 환경에서는 완전 차단
"""

import logging
import uuid
from typing import Any, Dict, List, Optional

from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from .base import XTestModeMixin, collect_system_snapshot

logger = logging.getLogger(__name__)

# X-Test-Mode 메타데이터 식별자
XTEST_SOURCE = "x-test-mode"
MAX_INJECT_COUNT = 20


class InjectDLQEntryView(XTestModeMixin, APIView):
    """
    테스트용 DLQ 항목 생성 API.

    POST /api/self-healing/xtest/dlq/inject/

    Request:
        {
            "domain": "external_service",
            "failure_type": "TIMEOUT",
            "entity_type": "test",       // optional
            "entity_id": "test-001",     // optional
            "error_message": "Test error",  // optional
            "count": 1                   // optional, max 20
        }

    Response:
        {
            "status": "success",
            "created_count": 1,
            "dlq_ids": [123],
            "domain": "external_service",
            "xtest_session": "uuid-xxx",
            "snapshot": {...}
        }
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        domain = request.data.get("domain")
        failure_type = request.data.get("failure_type")

        if not domain or not failure_type:
            return Response(
                {
                    "status": "error",
                    "error": "missing_required_fields",
                    "message": "domain and failure_type are required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        entity_type = request.data.get("entity_type", "test")
        entity_id = request.data.get("entity_id", "")
        error_message = request.data.get("error_message", "X-Test-Mode injected failure")
        count = int(request.data.get("count", 1))

        # 최대 주입 횟수 제한 (안전 장치)
        if count > MAX_INJECT_COUNT:
            return Response(
                {
                    "status": "error",
                    "error": "injection_limit_exceeded",
                    "message": f"Maximum injection count is {MAX_INJECT_COUNT}",
                    "requested": count,
                    "max_allowed": MAX_INJECT_COUNT,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if count < 1:
            count = 1

        # X-Test-Mode 세션 식별자 생성
        xtest_session = str(uuid.uuid4())[:8]
        user_str = str(request.user) if request.user and request.user.is_authenticated else "anonymous"

        # DLQ 서비스를 통한 항목 생성
        from selfhealing.services.dlq import get_dlq_service

        dlq_service = get_dlq_service()
        created_ids: List[int] = []

        for i in range(count):
            # X-Test-Mode 메타데이터 추가
            metadata = {
                "source": XTEST_SOURCE,
                "created_by": user_str,
                "xtest_session": xtest_session,
                "injection_number": i + 1,
                "total_injections": count,
            }

            result = dlq_service.store_failure(
                domain=domain,
                failure_type=failure_type,
                entity_type=entity_type,
                entity_id=f"{entity_id}-{i + 1}" if entity_id else f"xtest-{xtest_session}-{i + 1}",
                error_code="XTEST_INJECTED",
                error_message=error_message,
                metadata=metadata,
                next_action_hint="X-Test-Mode injected entry for testing",
                recommended_action="test_verify",
            )

            if result.success:
                created_ids.append(result.dlq_id)

        # 스냅샷 수집
        snapshot = collect_system_snapshot()

        logger.info(
            f"[X-Test-Mode] DLQ injection: domain={domain}, "
            f"failure_type={failure_type}, count={len(created_ids)}, "
            f"session={xtest_session}, user={user_str}"
        )

        response_data = {
            "status": "success",
            "created_count": len(created_ids),
            "dlq_ids": created_ids,
            "domain": domain,
            "failure_type": failure_type,
            "xtest_session": xtest_session,
            "timestamp": timezone.now().isoformat(),
            "snapshot": snapshot,
        }

        # WAL Audit 기록
        self.log_xtest_injection(
            request=request,
            component="dlq",
            injection_type="create",
            count=len(created_ids),
            target_ids=[str(id) for id in created_ids],
        )

        return Response(response_data, status=status.HTTP_201_CREATED)


class DLQXTestStatusView(XTestModeMixin, APIView):
    """
    X-Test-Mode DLQ 현황 조회 API.

    GET /api/self-healing/xtest/dlq/status/

    Query Parameters:
        - domain: 필터링할 도메인 (optional)
        - status: 필터링할 상태 (optional)
        - limit: 최대 조회 수 (default 50)

    Response:
        {
            "total_count": 100,
            "by_status": {"pending": 80, "resolved": 20},
            "by_domain": {"external_service": 50, "internal_process": 50},
            "recent_entries": [
                {"id": 123, "status": "pending", "domain": "external_service", ...}
            ],
            "xtest_entries_count": 10
        }
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        domain_filter = request.query_params.get("domain")
        status_filter = request.query_params.get("status")
        limit = int(request.query_params.get("limit", 50))

        # 최대 조회 수 제한
        if limit > 200:
            limit = 200

        from selfhealing.services.dlq import get_dlq_service

        dlq_service = get_dlq_service()

        # 전체 통계 조회
        stats = dlq_service.get_stats()

        # 필터링된 항목 목록 조회
        filters: Dict[str, Any] = {}
        if domain_filter:
            filters["domain"] = domain_filter
        if status_filter:
            filters["status"] = status_filter

        # Repository를 통한 직접 조회
        recent_entries: List[Dict[str, Any]] = []
        xtest_entries_count = 0

        try:
            result = dlq_service.list_entries(filters=filters, page=1, page_size=limit)

            for entry in result.results:
                entry_dict = {
                    "id": entry.get("id"),
                    "status": entry.get("status"),
                    "domain": entry.get("domain"),
                    "failure_type": entry.get("failure_type"),
                    "created_at": entry.get("created_at"),
                    "error_message": entry.get("error_message", "")[:100],  # 요약
                }
                recent_entries.append(entry_dict)

                # X-Test-Mode 생성 항목 카운트
                metadata = entry.get("metadata", {})
                if isinstance(metadata, dict) and metadata.get("source") == XTEST_SOURCE:
                    xtest_entries_count += 1
        except Exception as e:
            logger.warning(f"[X-Test-Mode] DLQ status list failed: {e}")

        logger.info(
            f"[X-Test-Mode] DLQ status query: domain={domain_filter}, "
            f"status={status_filter}, total={stats.get('total', 0)}, user={request.user}"
        )

        response_data = {
            "status": "success",
            "total_count": stats.get("total", 0),
            "by_status": stats.get("by_status", {}),
            "by_domain": stats.get("by_domain", {}),
            "recent_entries": recent_entries,
            "xtest_entries_count": xtest_entries_count,
            "filters_applied": {
                "domain": domain_filter,
                "status": status_filter,
                "limit": limit,
            },
            "timestamp": timezone.now().isoformat(),
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="query_status",
            component="dlq",
            details={"total_count": stats.get("total", 0), "xtest_count": xtest_entries_count},
            result="success",
        )

        return Response(response_data)


class ForceStatusView(XTestModeMixin, APIView):
    """
    DLQ 상태 강제 변경 API.

    POST /api/self-healing/xtest/dlq/force-status/

    Request:
        {
            "dlq_id": 123,
            "new_status": "resolved",  // pending, reviewing, resolved, rejected
            "reason": "Test status change"  // optional
        }

    Response:
        {
            "status": "success",
            "dlq_id": 123,
            "previous_status": "pending",
            "new_status": "resolved",
            "changed_at": "2025-01-26T14:00:00+09:00"
        }
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    # 허용되는 상태 목록
    ALLOWED_STATUSES = ["pending", "reviewing", "resolved", "rejected", "requires_review"]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        dlq_id = request.data.get("dlq_id")
        new_status = request.data.get("new_status")
        reason = request.data.get("reason", "X-Test-Mode force status change")

        if not dlq_id or not new_status:
            return Response(
                {
                    "status": "error",
                    "error": "missing_required_fields",
                    "message": "dlq_id and new_status are required",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if new_status not in self.ALLOWED_STATUSES:
            return Response(
                {
                    "status": "error",
                    "error": "invalid_status",
                    "message": f"new_status must be one of: {', '.join(self.ALLOWED_STATUSES)}",
                    "provided": new_status,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from selfhealing.services.dlq import get_dlq_service

        dlq_service = get_dlq_service()

        # 기존 항목 조회
        entry = dlq_service.get_entry(int(dlq_id))
        if entry is None:
            return Response(
                {
                    "status": "error",
                    "error": "not_found",
                    "message": f"DLQ entry {dlq_id} not found",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        previous_status = entry.get("status")

        # Repository를 통한 직접 상태 변경
        try:
            if new_status == "resolved":
                # resolve_entry 메서드 사용
                dlq_service.resolve_entry(int(dlq_id), notes=reason)
            else:
                # Repository 직접 업데이트
                success = dlq_service.repository.update_status(int(dlq_id), new_status)
                if not success:
                    raise ValueError(f"Failed to update status for entry {dlq_id}")
        except Exception as e:
            logger.error(f"[X-Test-Mode] Force status change failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "update_failed",
                    "message": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        user_str = str(request.user) if request.user and request.user.is_authenticated else "anonymous"

        logger.info(
            f"[X-Test-Mode] DLQ force status: id={dlq_id}, "
            f"{previous_status}→{new_status}, reason={reason}, user={user_str}"
        )

        response_data = {
            "status": "success",
            "dlq_id": int(dlq_id),
            "previous_status": previous_status,
            "new_status": new_status,
            "reason": reason,
            "changed_at": timezone.now().isoformat(),
        }

        # WAL Audit 기록
        self.log_xtest_audit(
            request=request,
            action="force_status",
            component="dlq",
            details={"dlq_id": int(dlq_id), "previous_status": previous_status, "new_status": new_status},
            result="success",
        )

        return Response(response_data)


# =============================================================================
# DLQ Reset Helpers (Complexity Reduction)
# =============================================================================


def _find_xtest_entries(
    dlq_service,
    domain_filter: Optional[str],
    created_by_xtest: bool,
) -> List[int]:
    """X-Test 생성 DLQ 항목 ID 조회."""
    filters: Dict[str, Any] = {}
    if domain_filter:
        filters["domain"] = domain_filter

    result = dlq_service.list_entries(filters=filters, page=1, page_size=500)
    ids_to_delete: List[int] = []
    
    for entry in result.results:
        entry_id = entry.get("id")
        if entry_id is None:
            continue

        if created_by_xtest:
            metadata = entry.get("metadata", {})
            if isinstance(metadata, dict) and metadata.get("source") == XTEST_SOURCE:
                ids_to_delete.append(entry_id)
        else:
            ids_to_delete.append(entry_id)
    
    return ids_to_delete


def _delete_dlq_entries(dlq_service, entry_ids: List[int]) -> int:
    """DLQ 항목 삭제 실행. 삭제된 개수 반환."""
    deleted_count = 0
    for entry_id in entry_ids:
        try:
            dlq_service.repository.delete_by_id(entry_id)
            deleted_count += 1
        except Exception as e:
            logger.warning(f"[X-Test-Mode] Failed to delete DLQ entry {entry_id}: {e}")
    return deleted_count


class ResetDLQXTestView(XTestModeMixin, APIView):
    """
    X-Test-Mode 생성 DLQ 항목 초기화 API.

    POST /api/self-healing/xtest/dlq/reset/

    Request:
        {
            "domain": "external_service",  // optional, 특정 도메인만 초기화
            "created_by_xtest": true       // optional, default true (X-Test 항목만)
        }

    Response:
        {
            "status": "success",
            "deleted_count": 10,
            "domain_filter": "external_service",
            "xtest_only": true
        }
    """

    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request: Request) -> Response:
        denied = self.check_chaos_permission(request)
        if denied:
            return denied

        domain_filter = request.data.get("domain")
        created_by_xtest = request.data.get("created_by_xtest", True)

        from selfhealing.services.dlq import get_dlq_service
        dlq_service = get_dlq_service()

        try:
            # X-Test-Mode 생성 항목 조회
            ids_to_delete = _find_xtest_entries(dlq_service, domain_filter, created_by_xtest)
            
            # 삭제 실행
            deleted_count = _delete_dlq_entries(dlq_service, ids_to_delete)

        except Exception as e:
            logger.error(f"[X-Test-Mode] DLQ reset failed: {e}")
            return Response(
                {
                    "status": "error",
                    "error": "reset_failed",
                    "message": str(e),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        user_str = str(request.user) if request.user and request.user.is_authenticated else "anonymous"

        logger.info(
            f"[X-Test-Mode] DLQ reset: deleted={deleted_count}, "
            f"domain={domain_filter}, xtest_only={created_by_xtest}, user={user_str}"
        )

        response_data = {
            "status": "success",
            "deleted_count": deleted_count,
            "domain_filter": domain_filter,
            "xtest_only": created_by_xtest,
            "timestamp": timezone.now().isoformat(),
        }

        # WAL Audit 기록
        self.log_xtest_cleanup(
            request=request,
            component="dlq",
            cleaned_count=deleted_count,
            cleaned_ids=[str(id) for id in ids_to_delete],
        )

        return Response(response_data)

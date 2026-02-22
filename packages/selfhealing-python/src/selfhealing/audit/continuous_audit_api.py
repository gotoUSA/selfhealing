"""
Continuous Audit API Endpoints.

Provides REST API for continuous audit system:
- Audit log query/filter/export
- Integrity verification
- Raw data access (no report formatting)

Design Philosophy:
- Raw data만 제공 (각 조직에서 자체 포맷으로 가공)
- 완전한 필터링 및 익스포트 기능
- 무결성 검증 엔드포인트
"""

from datetime import datetime, timezone

import structlog
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views import View

logger = structlog.get_logger()


def _parse_datetime(value: str | None) -> datetime | None:
    """ISO 형식 문자열을 datetime으로 변환."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_recorder():
    """ContinuousAuditRecorder 인스턴스 반환."""
    import os

    from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter
    from selfhealing.audit.config import AuditConfig
    from selfhealing.audit.continuous_audit import ContinuousAuditRecorder

    # 싱글톤 패턴
    if not hasattr(_get_recorder, "_instance"):
        log_path = os.environ.get("AUDIT_LOG_PATH", "logs/continuous_audit.jsonl")
        adapter = FileAuditLogAdapter(log_path, rotate_daily=True)
        config = AuditConfig.get_default()
        _get_recorder._instance = ContinuousAuditRecorder(
            audit_adapter=adapter,
            config=config,
        )

    return _get_recorder._instance


class ContinuousAuditQueryView(View):
    """
    감사 로그 조회 엔드포인트.

    GET /api/self-healing/audit/logs/

    Query Parameters:
        action: 액션 유형 필터
        target_type: 대상 유형 필터
        target_id: 대상 ID 필터
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)
        limit: 최대 반환 개수 (기본: 100)
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """감사 로그 조회."""
        try:
            recorder = _get_recorder()

            # 쿼리 파라미터 파싱
            action = request.GET.get("action")
            target_type = request.GET.get("target_type")
            target_id = request.GET.get("target_id")
            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))
            limit = int(request.GET.get("limit", 100))

            # 액션을 AuditAction으로 변환 시도
            action_enum = None
            if action:
                try:
                    from selfhealing.interfaces.audit_adapter import AuditAction

                    action_enum = AuditAction(action)
                except ValueError:
                    pass  # 문자열로 유지

            entries = recorder.query(
                action=action_enum,
                target_type=target_type,
                target_id=target_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            return JsonResponse(
                {
                    "entries": entries,
                    "count": len(entries),
                    "filters": {
                        "action": action,
                        "target_type": target_type,
                        "target_id": target_id,
                        "start_time": start_time.isoformat() if start_time else None,
                        "end_time": end_time.isoformat() if end_time else None,
                        "limit": limit,
                    },
                }
            )

        except Exception as e:
            logger.exception(
                "continuous_audit_query_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ContinuousAuditDetailView(View):
    """
    특정 감사 로그 상세 조회.

    GET /api/self-healing/audit/logs/{id}/
    """

    def get(self, request: HttpRequest, log_id: str) -> JsonResponse:
        """특정 감사 로그 상세 조회."""
        try:
            recorder = _get_recorder()

            # ID에서 타임스탬프와 시퀀스 추출
            # 형식: audit-YYYYMMDDHHMMSS-NNNNNN
            parts = log_id.split("-")
            if len(parts) != 3 or parts[0] != "audit":
                return JsonResponse({"error": "Invalid log ID format"}, status=400)

            # 해당 시간 근처의 로그를 검색
            try:
                ts_str = parts[1]
                timestamp = datetime.strptime(ts_str, "%Y%m%d%H%M%S")
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            except ValueError:
                return JsonResponse({"error": "Invalid timestamp in log ID"}, status=400)

            # 시퀀스 번호로 찾기
            sequence = int(parts[2])

            entries = recorder.query(
                start_time=timestamp,
                limit=100,
            )

            # 시퀀스 번호 매칭
            for entry in entries:
                integrity = entry.get("details", {}).get("integrity", {})
                if integrity.get("sequence") == sequence:
                    return JsonResponse({"entry": entry})

            return JsonResponse({"error": f"Log entry '{log_id}' not found"}, status=404)

        except Exception as e:
            logger.exception(
                "continuous_audit_detail_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ContinuousAuditAutoTuningView(LoginRequiredMixin, View):
    """
    자율 조정 이력 조회 (인증 필요).

    GET /api/self-healing/audit/auto-tuning/

    Query Parameters:
        parameter: 파라미터 이름 필터
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)
        limit: 최대 반환 개수 (기본: 100)
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """자율 조정 이력 조회."""
        try:
            recorder = _get_recorder()

            parameter = request.GET.get("parameter")
            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))
            limit = int(request.GET.get("limit", 100))

            entries = recorder.query_auto_tuning_history(
                parameter=parameter,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            return JsonResponse(
                {
                    "entries": entries,
                    "count": len(entries),
                }
            )

        except Exception as e:
            logger.exception(
                "continuous_audit_auto_tuning_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


# ── 하위 호환 별칭 ──────────────────────────────────────────
AutoTuningHistoryView = ContinuousAuditAutoTuningView


class DriftHistoryView(View):
    """
    DNA Drift 이력 조회.

    GET /api/self-healing/audit/drift/

    Query Parameters:
        resource_id: 리소스 ID 필터
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)
        limit: 최대 반환 개수 (기본: 100)
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """Drift 이력 조회."""
        try:
            recorder = _get_recorder()

            resource_id = request.GET.get("resource_id")
            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))
            limit = int(request.GET.get("limit", 100))

            entries = recorder.query_drift_history(
                resource_id=resource_id,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            return JsonResponse(
                {
                    "entries": entries,
                    "count": len(entries),
                }
            )

        except Exception as e:
            logger.exception(
                "drift_history_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ComplianceHistoryView(View):
    """
    Compliance 검사 이력 조회.

    GET /api/self-healing/audit/compliance/

    Query Parameters:
        standard: 규정 필터 (예: DORA, PCI-DSS)
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)
        limit: 최대 반환 개수 (기본: 100)
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """Compliance 검사 이력 조회."""
        try:
            recorder = _get_recorder()

            standard = request.GET.get("standard")
            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))
            limit = int(request.GET.get("limit", 100))

            entries = recorder.query_compliance_history(
                standard=standard,
                start_time=start_time,
                end_time=end_time,
                limit=limit,
            )

            return JsonResponse(
                {
                    "entries": entries,
                    "count": len(entries),
                }
            )

        except Exception as e:
            logger.exception(
                "compliance_history_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class IntegrityVerifyView(View):
    """
    감사 로그 무결성 검증.

    GET /api/self-healing/audit/integrity/verify/
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """무결성 검증 실행."""
        try:
            recorder = _get_recorder()
            result = recorder.verify_integrity()

            status_code = 200 if result.get("verified", False) else 400

            return JsonResponse(result, status=status_code)

        except Exception as e:
            logger.exception(
                "integrity_verify_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ChainStateView(View):
    """
    해시 체인 상태 조회.

    GET /api/self-healing/audit/integrity/state/
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """해시 체인 상태 조회."""
        try:
            recorder = _get_recorder()
            state = recorder.get_chain_state()

            return JsonResponse(
                {
                    "chain_state": state,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        except Exception as e:
            logger.exception(
                "chain_state_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ExportJSONLView(View):
    """
    JSON Lines 형식 익스포트.

    GET /api/self-healing/audit/export/jsonl/

    Query Parameters:
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)
        actions: 액션 필터 (쉼표 구분)

    Response:
        application/x-ndjson 스트리밍 응답
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """JSON Lines 형식 익스포트."""
        try:
            recorder = _get_recorder()

            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))

            # 액션 필터 파싱
            action_filter = None
            actions_str = request.GET.get("actions")
            if actions_str:
                from selfhealing.interfaces.audit_adapter import AuditAction

                action_filter = []
                for action_name in actions_str.split(","):
                    try:
                        action_filter.append(AuditAction(action_name.strip()))
                    except ValueError:
                        pass

            def generate():
                for line in recorder.export_jsonl(
                    start_time=start_time,
                    end_time=end_time,
                    action_filter=action_filter,
                ):
                    yield line + "\n"

            response = StreamingHttpResponse(
                generate(),
                content_type="application/x-ndjson",
            )
            response["Content-Disposition"] = 'attachment; filename="audit_export.jsonl"'

            return response

        except Exception as e:
            logger.exception(
                "export_jsonl_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ExportCSVView(View):
    """
    CSV 호환 형식 익스포트.

    GET /api/self-healing/audit/export/csv/

    Query Parameters:
        start_time: 시작 시간 (ISO 8601)
        end_time: 종료 시간 (ISO 8601)

    Response:
        text/csv 스트리밍 응답
    """

    def get(self, request: HttpRequest) -> HttpResponse:
        """CSV 형식 익스포트."""
        try:
            import csv
            from io import StringIO

            recorder = _get_recorder()

            start_time = _parse_datetime(request.GET.get("start_time"))
            end_time = _parse_datetime(request.GET.get("end_time"))

            data = recorder.export_csv_compatible(
                start_time=start_time,
                end_time=end_time,
            )

            if not data:
                return HttpResponse(
                    "No data found",
                    content_type="text/plain",
                    status=204,
                )

            # 모든 필드 수집
            all_fields = set()
            for row in data:
                all_fields.update(row.keys())

            # 정렬된 필드 목록
            fieldnames = sorted(all_fields)

            # CSV 생성
            output = StringIO()
            writer = csv.DictWriter(output, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(data)

            response = HttpResponse(
                output.getvalue(),
                content_type="text/csv",
            )
            response["Content-Disposition"] = 'attachment; filename="audit_export.csv"'

            return response

        except Exception as e:
            logger.exception(
                "export_csv_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


class ConfigView(View):
    """
    감사 설정 조회.

    GET /api/self-healing/audit/config/
    """

    def get(self, request: HttpRequest) -> JsonResponse:
        """현재 감사 설정 조회."""
        try:
            recorder = _get_recorder()
            config = recorder.config.to_dict()

            return JsonResponse(
                {
                    "config": config,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )

        except Exception as e:
            logger.exception(
                "config_view.error",
                error=e,
            )
            return JsonResponse({"error": str(e)}, status=500)


# URL 패턴
def get_continuous_audit_urlpatterns():
    """Continuous Audit API URL 패턴 반환."""
    from django.urls import path

    return [
        # 로그 조회
        path("logs/", ContinuousAuditQueryView.as_view(), name="audit-logs"),
        path(
            "logs/<str:log_id>/",
            ContinuousAuditDetailView.as_view(),
            name="audit-log-detail",
        ),
        # 도메인별 이력
        path("auto-tuning/", ContinuousAuditAutoTuningView.as_view(), name="audit-auto-tuning"),
        path("drift/", DriftHistoryView.as_view(), name="audit-drift"),
        path("compliance/", ComplianceHistoryView.as_view(), name="audit-compliance"),
        # 무결성 검증
        path(
            "integrity/verify/",
            IntegrityVerifyView.as_view(),
            name="audit-integrity-verify",
        ),
        path("integrity/state/", ChainStateView.as_view(), name="audit-chain-state"),
        # 익스포트
        path("export/jsonl/", ExportJSONLView.as_view(), name="audit-export-jsonl"),
        path("export/csv/", ExportCSVView.as_view(), name="audit-export-csv"),
        # 설정
        path("config/", ConfigView.as_view(), name="audit-config"),
    ]

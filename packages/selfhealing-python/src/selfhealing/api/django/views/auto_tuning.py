"""
Auto Tuning API Views

자율 조정 제어 API 엔드포인트
"""

import structlog
from datetime import datetime

from rest_framework import status
from rest_framework.permissions import BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.api.django.permissions import IsSelfHealingAdmin, IsViewer

logger = structlog.get_logger()


def _get_auto_tuning_service():
    """AutoTuningService 인스턴스 가져오기 (Lazy Loading)"""
    try:
        from selfhealing.factory import get_auto_tuning_service
        from selfhealing.services.auto_tuning import AutoTuningService

        return get_auto_tuning_service()
    except ImportError:
        # Factory에서 제공되지 않으면 기본 인스턴스 생성
        return _create_default_service()


def _create_default_service():
    """기본 AutoTuningService 생성"""
    from selfhealing.factory import ProviderRegistry
    from selfhealing.services.auto_tuning import AutoTuningService

    # 기본 어댑터들
    class DummyMetricsAdapter:
        def fetch_current_metrics(self):
            return {
                "p99_latency_ms": 0,
                "error_rate": 0,
                "retry_exhausted_rate": 0,
                "throughput_rps": 0,
            }

    class DummyConfigProvider:
        def get(self, key, default=None):
            return default

    class DummyConfigApplier:
        def __init__(self):
            self._values = {}

        def get_current(self, parameter):
            return self._values.get(parameter, 0)

        def apply(self, parameter, value):
            self._values[parameter] = value
            return True

        def rollback(self, parameter, value):
            self._values[parameter] = value
            return True

    # ProviderRegistry에서 실제 Audit Adapter 획득
    # DummyAuditAdapter 대신 실제 audit 시스템과 연동
    try:
        audit_adapter = ProviderRegistry.get_audit_adapter()
    except (ValueError, ImportError):
        # Fallback: 실제 audit 헬퍼 함수를 사용하는 래퍼
        from selfhealing.services.audit import log_system_control_audit

        class AuditAdapterWrapper:
            """audit 헬퍼 함수를 사용하는 Audit Adapter 래퍼"""

            def log(self, entry):
                """AutoTuning 이벤트를 실제 audit 시스템에 기록"""
                log_system_control_audit(
                    action=entry.get("action", "auto_tuning"),
                    actor=entry.get("actor", "system"),
                    old_state=entry.get("old_state"),
                    new_state=entry.get("new_state"),
                    reason=entry.get("reason", str(entry)),
                )

        audit_adapter = AuditAdapterWrapper()

    # InternalMetricsAdapter: throttle_rate 포함 실 메트릭 수집 (DummyMetricsAdapter 대체)
    try:
        from selfhealing.adapters.metrics.auto_tuning_adapter import (
            InternalMetricsAdapter,
        )

        metrics_adapter = InternalMetricsAdapter()
    except ImportError:
        logger.warning("auto_tuning.internalmetricsadapter_import_failed_falling")
        metrics_adapter = DummyMetricsAdapter()

    # CompositeConfigApplier: ThrottleConfigApplier(SLA 전용) + DummyConfigApplier(fallback)
    try:
        from selfhealing.adapters.config_applier.composite import (
            CompositeConfigApplier,
        )
        from selfhealing.adapters.config_applier.throttle import (
            ThrottleConfigApplier,
        )

        config_applier = CompositeConfigApplier(
            [
                ThrottleConfigApplier(),  # throttle_sla_*, rate_limit_rps(No-op)
                DummyConfigApplier(),  # 나머지 모듈 (circuit_breaker, retry, jitter, timeout)
            ]
        )
    except ImportError:
        logger.warning("auto_tuning.throttleconfigapplier_import_failed_falling")
        config_applier = DummyConfigApplier()

    service = AutoTuningService(
        metrics_adapter=metrics_adapter,
        config_provider=DummyConfigProvider(),
        config_applier=config_applier,
        audit_adapter=audit_adapter,
    )

    # SLA 전용 DecisionEngine 규칙 주입
    try:
        from selfhealing.services.auto_tuning.throttle_sla_rules import (
            THROTTLE_SLA_RULES,
        )

        service.decision_engine.rules.extend(THROTTLE_SLA_RULES)
    except ImportError:
        logger.warning("auto_tuning.import_failed_sla_auto")

    return service


# 싱글톤 서비스 인스턴스
_service_instance = None


def get_service():
    """서비스 인스턴스 (싱글톤)"""
    global _service_instance
    if _service_instance is None:
        _service_instance = _get_auto_tuning_service()
    return _service_instance


class AutoTuningStatusView(APIView):
    """
    GET /api/self-healing/auto-tuning/status/

    자율 조정 시스템 상태 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        service = get_service()
        return Response(service.get_status())


class AutoTuningEnableView(APIView):
    """
    POST /api/self-healing/auto-tuning/enable/

    자율 조정 활성화
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request):
        service = get_service()
        reason = request.data.get("reason", "")
        mode = request.data.get("mode", "automatic")

        enabled_by = getattr(request.user, "email", str(request.user))

        result = service.enable(
            reason=reason,
            mode=mode,
            enabled_by=enabled_by,
        )

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningDisableView(APIView):
    """
    POST /api/self-healing/auto-tuning/disable/

    자율 조정 비활성화
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request):
        service = get_service()
        reason = request.data.get("reason", "")
        duration = request.data.get("duration_minutes")
        notify = request.data.get("notify", True)

        disabled_by = getattr(request.user, "email", str(request.user))

        result = service.disable(
            reason=reason,
            duration_minutes=duration,
            disabled_by=disabled_by,
            notify=notify,
        )

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningModuleEnableView(APIView):
    """
    POST /api/self-healing/auto-tuning/{module}/enable/

    특정 모듈 자율 조정 활성화
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request, module):
        service = get_service()
        reason = request.data.get("reason", "")

        enabled_by = getattr(request.user, "email", str(request.user))

        result = service.enable_module(
            module=module,
            reason=reason,
            enabled_by=enabled_by,
        )

        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningModuleDisableView(APIView):
    """
    POST /api/self-healing/auto-tuning/{module}/disable/

    특정 모듈 자율 조정 비활성화
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request, module):
        service = get_service()
        reason = request.data.get("reason", "")
        duration = request.data.get("duration_minutes")

        disabled_by = getattr(request.user, "email", str(request.user))

        result = service.disable_module(
            module=module,
            reason=reason,
            duration_minutes=duration,
            disabled_by=disabled_by,
        )

        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningBoundsView(APIView):
    """
    GET  /api/self-healing/auto-tuning/bounds/     (Viewer)
    PUT  /api/self-healing/auto-tuning/bounds/     (Admin)

    안전 한계 조회/수정
    """

    def get_permissions(self) -> list[BasePermission]:
        if self.request.method == "GET":
            return [IsViewer()]
        return [IsSelfHealingAdmin()]

    def get(self, request):
        service = get_service()
        return Response(service.get_bounds())

    def put(self, request):
        service = get_service()

        parameter = request.data.get("parameter")
        bounds = request.data.get("bounds", {})
        reason = request.data.get("reason", "")

        if not parameter:
            return Response({"error": "parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        updated_by = getattr(request.user, "email", str(request.user))

        result = service.update_bounds(
            parameter=parameter,
            bounds=bounds,
            reason=reason,
            updated_by=updated_by,
        )

        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningHistoryView(APIView):
    """
    GET /api/self-healing/auto-tuning/history/
    GET /api/self-healing/auto-tuning/history/{id}/

    조정 이력 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request, history_id: str | None = None):
        service = get_service()

        if history_id:
            # 특정 조정 상세
            limit = self._get_export_limit()
            records = service.adjustment_recorder.get_records(limit=limit)
            for record in records:
                if record.record_id == history_id:
                    return Response(record.to_dict())
            return Response({"error": "History record not found"}, status=status.HTTP_404_NOT_FOUND)

        # 목록 조회
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        parameter = request.query_params.get("parameter")
        page = int(request.query_params.get("page", 1))
        page_size = int(request.query_params.get("page_size", 20))

        start_dt = None
        end_dt = None

        if start_date:
            try:
                start_dt = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
            except ValueError:
                pass

        if end_date:
            try:
                end_dt = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
            except ValueError:
                pass

        result = service.get_history(
            start_date=start_dt,
            end_date=end_dt,
            parameter=parameter,
            page=page,
            page_size=page_size,
        )

        return Response(result)

    @staticmethod
    def _get_export_limit() -> int:
        """Settings에서 export_limit 조회."""
        try:
            from selfhealing.settings.api_view import get_api_view_settings

            return get_api_view_settings().auto_tuning_export_limit
        except Exception:
            return 1000  # 기본값


class AutoTuningOverrideView(APIView):
    """
    POST   /api/self-healing/auto-tuning/override/
    DELETE /api/self-healing/auto-tuning/override/{parameter}/

    수동 조정 (Override)
    """

    permission_classes = [IsSelfHealingAdmin]

    def post(self, request):
        service = get_service()

        parameter = request.data.get("parameter")
        value = request.data.get("value")
        reason = request.data.get("reason", "")
        duration = request.data.get("duration_minutes")
        disable_auto = request.data.get("disable_auto_tuning", True)

        if not parameter:
            return Response({"error": "parameter is required"}, status=status.HTTP_400_BAD_REQUEST)

        if value is None:
            return Response({"error": "value is required"}, status=status.HTTP_400_BAD_REQUEST)

        overridden_by = getattr(request.user, "email", str(request.user))

        result = service.override(
            parameter=parameter,
            value=float(value),
            reason=reason,
            duration_minutes=duration,
            disable_auto_tuning=disable_auto,
            overridden_by=overridden_by,
        )

        if "error" in result:
            return Response(result, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)

    def delete(self, request, parameter: str):
        service = get_service()

        cleared_by = getattr(request.user, "email", str(request.user))

        result = service.clear_override(
            parameter=parameter,
            cleared_by=cleared_by,
        )

        if "error" in result:
            return Response(result, status=status.HTTP_404_NOT_FOUND)

        return Response(result, status=status.HTTP_200_OK)


class AutoTuningMetricsView(APIView):
    """
    GET /api/self-healing/auto-tuning/metrics/

    현재 메트릭 조회 (Viewer)
    """

    permission_classes = [IsViewer]

    def get(self, request):
        service = get_service()
        return Response(service.get_current_metrics())


__all__ = [
    "AutoTuningStatusView",
    "AutoTuningEnableView",
    "AutoTuningDisableView",
    "AutoTuningModuleEnableView",
    "AutoTuningModuleDisableView",
    "AutoTuningBoundsView",
    "AutoTuningHistoryView",
    "AutoTuningOverrideView",
    "AutoTuningMetricsView",
]

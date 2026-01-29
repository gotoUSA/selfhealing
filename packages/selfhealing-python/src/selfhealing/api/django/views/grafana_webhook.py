"""
Grafana Alert Webhook 수신 엔드포인트.

Grafana Alerting에서 발생하는 Alert를 수신하여
UnifiedNotificationManager로 전달합니다.

Endpoints:
    POST /api/self-healing/webhook/grafana/alert/ - Grafana Alert 수신
    GET  /api/self-healing/webhook/grafana/test/  - Webhook 연결 테스트

Note:
    - DRF APIView 사용으로 selfhealing_exception_handler 자동 적용
    - Audit 버퍼 연동, Prometheus 메트릭 자동 기록
    - 인증 없이 접근 가능 (Grafana에서 Webhook 전송용)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from selfhealing.services.unified_notification import (
    NotificationCategory,
    NotificationPayload,
    NotificationPriority,
    UnifiedNotificationManager,
)

logger = logging.getLogger(__name__)


def _map_grafana_severity_to_priority(severity: str) -> NotificationPriority:
    """
    Grafana Alert severity를 NotificationPriority로 변환.

    Args:
        severity: Grafana Alert의 severity 라벨

    Returns:
        대응하는 NotificationPriority
    """
    severity_mapping = {
        "critical": NotificationPriority.CRITICAL,
        "high": NotificationPriority.HIGH,
        "warning": NotificationPriority.HIGH,
        "medium": NotificationPriority.MEDIUM,
        "low": NotificationPriority.LOW,
        "info": NotificationPriority.INFO,
    }
    return severity_mapping.get(severity.lower(), NotificationPriority.MEDIUM)


def _map_grafana_category_to_notification_category(category: str) -> NotificationCategory:
    """
    Grafana Alert category를 NotificationCategory로 변환.

    Args:
        category: Grafana Alert의 category 라벨

    Returns:
        대응하는 NotificationCategory
    """
    category_mapping = {
        "sla": NotificationCategory.SLA,
        "circuit_breaker": NotificationCategory.CIRCUIT_BREAKER,
        "security": NotificationCategory.SECURITY,
        "dlq": NotificationCategory.OPERATIONS,
        "system": NotificationCategory.OPERATIONS,
        "retry": NotificationCategory.OPERATIONS,
        "tiering": NotificationCategory.OPERATIONS,
    }
    return category_mapping.get(category.lower(), NotificationCategory.OPERATIONS)


def _extract_metadata_from_annotations(annotations: dict[str, Any]) -> dict[str, Any]:
    """
    Grafana Alert annotations에서 메타데이터 추출.

    Args:
        annotations: Grafana Alert의 annotations

    Returns:
        추출된 메타데이터 dict
    """
    metadata: dict[str, Any] = {}

    # 숫자 필드 추출
    if "current_latency_ms" in annotations:
        try:
            metadata["current_latency_ms"] = float(annotations["current_latency_ms"])
        except (ValueError, TypeError):
            pass

    if "threshold_ms" in annotations:
        try:
            metadata["threshold_ms"] = float(annotations["threshold_ms"])
        except (ValueError, TypeError):
            pass

    # 문자열 필드 추출
    string_fields = ["affected_service", "runbook_url", "dashboard_url"]
    for field in string_fields:
        if field in annotations:
            metadata[field] = annotations[field]

    return metadata


class GrafanaAlertWebhookView(APIView):
    """
    Grafana Alert Webhook 수신 뷰.

    Grafana Alerting의 Webhook contact point에서 전송되는 Alert를 수신합니다.
    DRF APIView 사용으로 selfhealing_exception_handler가 자동 적용됩니다.

    요청 형식 (Grafana Alert Webhook):
    {
        "alerts": [
            {
                "status": "firing" | "resolved",
                "labels": {
                    "alertname": "LatencyP95SlaCritical",
                    "severity": "critical",
                    "category": "sla",
                    ...
                },
                "annotations": {
                    "summary": "...",
                    "description": "...",
                    ...
                },
                "startsAt": "2024-01-01T00:00:00.000Z",
                "endsAt": "0001-01-01T00:00:00Z"
            }
        ],
        "commonLabels": {...},
        "commonAnnotations": {...},
        "externalURL": "http://grafana:3000",
        "version": "1",
        "groupKey": "...",
        "truncatedAlerts": 0,
        "orgId": 1,
        "title": "[FIRING:1]...",
        "state": "alerting",
        "message": "..."
    }
    """

    # Grafana에서 Webhook 전송 시 인증 없이 접근 가능
    permission_classes = [AllowAny]
    # CSRF 면제 (DRF가 기본적으로 처리)
    authentication_classes = []

    def post(self, request: Request) -> Response:
        """Grafana Alert Webhook 수신 및 처리."""
        # Alert 목록 추출 (DRF가 자동으로 JSON 파싱)
        alerts = request.data.get("alerts", [])
        if not alerts:
            logger.info("Grafana webhook: 빈 Alert 목록 수신")
            return Response(
                {"status": "ok", "message": "No alerts to process"},
                status=status.HTTP_200_OK,
            )

        # UnifiedNotificationManager 인스턴스
        notification_manager = UnifiedNotificationManager()
        processed_count = 0
        error_count = 0

        for alert in alerts:
            try:
                self._process_single_alert(alert, notification_manager)
                processed_count += 1
            except Exception as e:
                logger.error("Grafana webhook: Alert 처리 실패 - %s", str(e))
                error_count += 1

        logger.info(
            "Grafana webhook: %d개 Alert 처리 완료 (실패: %d개)",
            processed_count,
            error_count,
        )

        return Response(
            {
                "status": "ok",
                "processed": processed_count,
                "errors": error_count,
            },
            status=status.HTTP_200_OK,
        )

    def _process_single_alert(
        self,
        alert: dict[str, Any],
        notification_manager: UnifiedNotificationManager,
    ) -> None:
        """
        단일 Grafana Alert를 처리하여 UnifiedNotificationManager로 전달.

        Args:
            alert: Grafana Alert 데이터
            notification_manager: UnifiedNotificationManager 인스턴스
        """
        status = alert.get("status", "firing")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})

        # resolved 상태는 로깅만 하고 알림은 보내지 않음
        if status == "resolved":
            alertname = labels.get("alertname", "unknown")
            logger.info("Grafana webhook: Alert 해결됨 - %s", alertname)
            return

        # Alert 정보 추출
        alertname = labels.get("alertname", "Unknown Alert")
        severity = labels.get("severity", "warning")
        category = labels.get("category", "operations")

        summary = annotations.get("summary", alertname)
        description = annotations.get("description", "No description provided")

        # Priority 및 Category 매핑
        priority = _map_grafana_severity_to_priority(severity)
        notification_category = _map_grafana_category_to_notification_category(category)

        # 메타데이터 추출
        metadata = _extract_metadata_from_annotations(annotations)
        metadata["alertname"] = alertname
        metadata["status"] = status
        metadata["grafana_labels"] = labels

        # NotificationPayload 생성
        notification_payload = NotificationPayload(
            title=summary,
            message=description,
            priority=priority,
            category=notification_category,
            source="grafana_alerting",
            metadata=metadata,
            tags=[f"alert:{alertname}", f"severity:{severity}", f"category:{category}"],
            dedup_key=f"grafana_alert_{alertname}_{labels.get('service_name', 'unknown')}",
        )

        # UnifiedNotificationManager로 알림 전송
        result = notification_manager.notify(notification_payload)

        if result.success:
            logger.info(
                "Grafana webhook: Alert 알림 전송 성공 - %s (채널: %s)",
                alertname,
                result.channels_sent,
            )
        elif result.suppressed:
            logger.info(
                "Grafana webhook: Alert 알림 억제됨 - %s (사유: %s)",
                alertname,
                result.suppression_reason,
            )
        else:
            logger.warning(
                "Grafana webhook: Alert 알림 전송 실패 - %s (오류: %s)",
                alertname,
                result.error,
            )


class GrafanaAlertWebhookTestView(APIView):
    """
    Grafana Alert Webhook 테스트 뷰.

    Webhook 엔드포인트 연결 테스트 용도입니다.
    DRF APIView 사용으로 selfhealing_exception_handler가 자동 적용됩니다.
    """

    # Grafana에서 연결 테스트 시 인증 없이 접근 가능
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request: Request) -> Response:
        """Webhook 엔드포인트 상태 확인."""
        return Response(
            {
                "status": "ok",
                "endpoint": "grafana_alert_webhook",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            status=status.HTTP_200_OK,
        )

    def post(self, request: Request) -> Response:
        """테스트 Alert 전송."""
        # DRF가 자동으로 JSON 파싱 (request.data)
        payload = request.data if request.data else {}

        logger.info("Grafana webhook 테스트: 수신된 페이로드 - %s", payload)

        return Response(
            {
                "status": "ok",
                "message": "Test webhook received",
                "received_payload": payload,
            },
            status=status.HTTP_200_OK,
        )

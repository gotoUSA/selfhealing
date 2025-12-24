"""
Metric Sync API Serializers.

Serializers for Phase 1: Poll 제거 + Manual API
Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md

POST /api/self-healing/metrics/sync/ - 수동 메트릭 동기화
GET /api/self-healing/metrics/drift-report/ - Drift 상태 조회
"""

from rest_framework import serializers
from typing import Any, Dict, List


# =============================================================================
# Request Serializers
# =============================================================================


class MetricSyncRequestSerializer(serializers.Serializer):
    """
    POST /api/self-healing/metrics/sync/ 요청 Serializer.
    
    운영자가 수동으로 메트릭 동기화를 트리거할 때 사용.
    """
    
    domains = serializers.ListField(
        child=serializers.CharField(max_length=50),
        required=False,
        allow_empty=True,
        help_text="동기화할 도메인 목록. 미지정 시 전체 도메인.",
    )
    
    dry_run = serializers.BooleanField(
        required=False,
        default=False,
        help_text="True면 리포트만 생성, 실제 동기화는 수행하지 않음.",
    )
    
    reason = serializers.CharField(
        max_length=500,
        required=False,
        allow_blank=True,
        help_text="동기화 사유 (Audit 기록용).",
    )


# =============================================================================
# Response Serializers
# =============================================================================


class DriftDetailSerializer(serializers.Serializer):
    """단일 메트릭의 Drift 상세 정보."""
    
    before = serializers.FloatField(help_text="동기화 전 인메모리 값")
    after = serializers.FloatField(help_text="동기화 후 실제 값")
    drift = serializers.FloatField(help_text="차이 (after - before)")


class DomainSyncResultSerializer(serializers.Serializer):
    """도메인별 동기화 결과."""
    
    dlq_pending = DriftDetailSerializer(required=False)
    circuit_breaker_state = serializers.DictField(required=False)
    retry_success_rate = DriftDetailSerializer(required=False)


class SyncSummarySerializer(serializers.Serializer):
    """동기화 요약 정보."""
    
    total_drifts_detected = serializers.IntegerField(
        help_text="감지된 총 Drift 수"
    )
    total_drifts_corrected = serializers.IntegerField(
        help_text="보정된 총 Drift 수"
    )
    max_drift_percent = serializers.FloatField(
        required=False,
        help_text="최대 Drift 퍼센트"
    )


class MetricSyncResponseSerializer(serializers.Serializer):
    """
    POST /api/self-healing/metrics/sync/ 응답 Serializer.
    """
    
    status = serializers.ChoiceField(
        choices=["completed", "dry_run", "partial", "failed"],
        help_text="동기화 상태",
    )
    synced_at = serializers.DateTimeField(
        help_text="동기화 시각 (ISO 8601)"
    )
    actor = serializers.CharField(
        help_text="동기화 수행자 (사용자명)"
    )
    dry_run = serializers.BooleanField(
        help_text="드라이런 모드 여부"
    )
    results = serializers.DictField(
        child=DomainSyncResultSerializer(),
        help_text="도메인별 동기화 결과",
    )
    summary = SyncSummarySerializer(
        help_text="동기화 요약"
    )


# =============================================================================
# Drift Report Serializers
# =============================================================================


class MetricDriftItemSerializer(serializers.Serializer):
    """개별 메트릭의 Drift 정보."""
    
    in_memory = serializers.FloatField(
        help_text="현재 인메모리(Gauge) 값"
    )
    actual = serializers.FloatField(
        help_text="DB에서 조회한 실제 값"
    )
    drift = serializers.FloatField(
        help_text="차이 (actual - in_memory)"
    )
    drift_percent = serializers.FloatField(
        required=False,
        help_text="Drift 퍼센트"
    )
    is_critical = serializers.BooleanField(
        help_text="임계치 초과 여부"
    )


class DriftReportMetricsSerializer(serializers.Serializer):
    """메트릭 유형별 Drift 정보."""
    
    dlq_pending_count = serializers.DictField(
        child=MetricDriftItemSerializer(),
        required=False,
        help_text="도메인별 DLQ 대기 수 Drift",
    )
    circuit_breaker_state = serializers.DictField(
        child=MetricDriftItemSerializer(),
        required=False,
        help_text="서비스별 Circuit Breaker 상태 Drift",
    )
    retry_success_rate = serializers.DictField(
        child=MetricDriftItemSerializer(),
        required=False,
        help_text="도메인별 재시도 성공률 Drift",
    )


class DriftReportResponseSerializer(serializers.Serializer):
    """
    GET /api/self-healing/metrics/drift-report/ 응답 Serializer.
    
    현재 Drift 상태를 조회합니다 (읽기 전용).
    DB 조회는 수행하지만 Gauge 값은 변경하지 않습니다.
    """
    
    generated_at = serializers.DateTimeField(
        help_text="리포트 생성 시각 (ISO 8601)"
    )
    metrics = DriftReportMetricsSerializer(
        help_text="메트릭별 Drift 정보"
    )
    overall_health = serializers.ChoiceField(
        choices=["healthy", "warning", "critical", "incident"],
        help_text="전반적 상태",
    )
    max_drift_percent = serializers.FloatField(
        help_text="최대 Drift 퍼센트"
    )
    recommendation = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="권장 조치",
    )


__all__ = [
    # Request
    "MetricSyncRequestSerializer",
    # Response
    "MetricSyncResponseSerializer",
    "DriftReportResponseSerializer",
    # Nested
    "DriftDetailSerializer",
    "DomainSyncResultSerializer",
    "SyncSummarySerializer",
    "MetricDriftItemSerializer",
    "DriftReportMetricsSerializer",
]

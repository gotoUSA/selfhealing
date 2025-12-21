"""
Metric Reconciler for Lazy Synchronization.

Provides mechanisms to synchronize Gauge metrics with actual data sources
during server startup and on-demand.

Reference: docs/self_healing/13_METRIC_COLLECTION_STRATEGY.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from selfhealing.adapters.metrics.base import MetricSourceAdapter, NullMetricSourceAdapter
from selfhealing.adapters.metrics.factory import get_metric_adapter
from selfhealing.metrics.jitter import with_jitter, JitterConfig

if TYPE_CHECKING:
    from selfhealing.services.security_violation_service import SecurityViolationService

logger = logging.getLogger(__name__)


class DriftSeverity:
    """Drift 심각도 레벨."""

    NORMAL = "normal"  # < 5%: 정상 범위
    WARNING = "warning"  # 5~20%: 경고, 로그만 기록
    CRITICAL = "critical"  # 20~50%: 심각, 알림 발송
    INCIDENT = "incident"  # > 50%: 인시던트, 이벤트 유실 의심


@dataclass
class DriftResult:
    """Drift 계산 결과."""

    details: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    max_drift_percent: float = 0.0
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = DriftSeverity.NORMAL


@dataclass
class SyncResult:
    """동기화 결과."""

    synced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dlq_pending: Dict[str, int] = field(default_factory=dict)
    circuit_breaker_states: Dict[str, str] = field(default_factory=dict)
    retry_success_rates: Dict[str, float] = field(default_factory=dict)
    drift: Optional[DriftResult] = None


class MetricReconciler:
    """
    메트릭과 실제 데이터 소스 간의 정합성을 맞추는 Reconciler.

    Gauge 타입 메트릭만 동기화합니다.
    Counter와 Histogram은 Push 시점에 정확하므로 동기화 불필요.

    Note:
        모든 시간 처리는 timezone-aware datetime을 사용합니다.
        datetime.utcnow()는 Python 3.12에서 deprecated되었으므로
        datetime.now(timezone.utc)를 사용합니다.

    Example:
        >>> adapter = get_metric_adapter()
        >>> reconciler = MetricReconciler(adapter)
        >>> result = reconciler.sync_all_gauges()
        >>> print(f"Synced at: {result.synced_at}")
    """

    def __init__(
        self,
        adapter: Optional[MetricSourceAdapter] = None,
        domains: Optional[List[str]] = None,
        services: Optional[List[str]] = None,
        drift_config: Optional["DriftThresholdConfig"] = None,
        incident_service: Optional[Any] = None,
    ):
        """
        Initialize MetricReconciler.

        Args:
            adapter: 메트릭 소스 어댑터 (None이면 팩토리에서 가져옴)
            domains: 도메인 목록 (None이면 등록된 도메인 사용)
            services: 서비스 목록 (Circuit Breaker용)
            drift_config: Drift 임계값 설정
            incident_service: 인시던트 서비스 (선택)
        """
        self.adapter = adapter or get_metric_adapter()
        self._domains = domains
        self._services = services or ["toss_payment", "external_api", "notification"]
        self._drift_config = drift_config
        self.incident_service = incident_service
        self._last_sync: Optional[datetime] = None
        self._last_sync_result: Optional[SyncResult] = None

    def _get_domains(self) -> List[str]:
        """도메인 목록 반환."""
        if self._domains:
            return self._domains
        try:
            from selfhealing.metrics.prometheus import get_domains

            return get_domains()
        except ImportError:
            return ["external_service", "internal_process", "async_task"]

    def _get_metrics(self):
        """메트릭 인스턴스 반환."""
        try:
            from selfhealing.metrics.prometheus import get_metrics

            return get_metrics()
        except ImportError:
            return None

    def sync_all_gauges(self) -> SyncResult:
        """
        모든 Gauge 메트릭을 데이터 소스와 동기화.

        Returns:
            동기화 결과 (SyncResult)
        """
        result = SyncResult()
        metrics = self._get_metrics()

        # DLQ 대기 수 동기화
        for domain in self._get_domains():
            try:
                actual = self.adapter.get_dlq_pending_count(domain)
                # 음수 방어: 개수는 0 이상이어야 함
                safe_actual = max(0, actual)
                result.dlq_pending[domain] = safe_actual

                if metrics and hasattr(metrics, "dlq_pending_gauge"):
                    metrics.dlq_pending_gauge.labels(domain=domain).set(safe_actual)
            except Exception as e:
                logger.warning(f"[Reconciler] Failed to sync DLQ pending for {domain}: {e}")

        # Circuit Breaker 상태 동기화
        state_values = {"closed": 0, "open": 1, "half_open": 2}
        for service in self._services:
            try:
                state = self.adapter.get_circuit_breaker_state(service)
                result.circuit_breaker_states[service] = state

                if metrics and hasattr(metrics, "circuit_breaker_state"):
                    state_value = state_values.get(state, 0)
                    metrics.circuit_breaker_state.labels(service_name=service).set(state_value)
            except Exception as e:
                logger.warning(f"[Reconciler] Failed to sync CB state for {service}: {e}")

        # 재시도 성공률 동기화
        for domain in self._get_domains():
            try:
                rate = self.adapter.get_retry_success_rate(domain)
                # 0-100 범위 클램핑
                safe_rate = max(0.0, min(100.0, rate))
                result.retry_success_rates[domain] = safe_rate

                if metrics and hasattr(metrics, "retry_success_rate"):
                    metrics.retry_success_rate.labels(domain=domain).set(safe_rate)
            except Exception as e:
                logger.warning(f"[Reconciler] Failed to sync retry rate for {domain}: {e}")

        self._last_sync = datetime.now(timezone.utc)
        self._last_sync_result = result
        logger.info(f"[Reconciler] Metrics reconciled: domains={len(result.dlq_pending)}")

        return result

    @with_jitter(max_delay_seconds=60.0)
    def sync_all_gauges_with_jitter(self) -> SyncResult:
        """
        Jitter가 적용된 Gauge 동기화.

        서버 시작 시 이 메서드를 사용하여
        분산 환경에서 DB 부하를 분산시킵니다.

        Returns:
            동기화 결과
        """
        return self.sync_all_gauges()

    def sync_domain_gauges(self, domain: str) -> Dict[str, Any]:
        """
        특정 도메인의 Gauge만 동기화.

        Args:
            domain: 도메인 이름

        Returns:
            동기화 결과 딕셔너리
        """
        metrics = self._get_metrics()

        actual = self.adapter.get_dlq_pending_count(domain)
        # 음수 방어: 개수는 0 이상이어야 함
        safe_actual = max(0, actual)
        if metrics and hasattr(metrics, "dlq_pending_gauge"):
            metrics.dlq_pending_gauge.labels(domain=domain).set(safe_actual)

        rate = self.adapter.get_retry_success_rate(domain)
        # 0-100 범위 클램핑
        safe_rate = max(0.0, min(100.0, rate))
        if metrics and hasattr(metrics, "retry_success_rate"):
            metrics.retry_success_rate.labels(domain=domain).set(safe_rate)

        return {"domain": domain, "dlq_pending": safe_actual, "retry_rate": safe_rate}

    def sync_with_drift_detection(self) -> SyncResult:
        """
        동기화 시 Drift를 감지하고 심각도에 따라 대응.

        Returns:
            동기화 결과 (Drift 정보 포함)
        """
        before = self._capture_current_gauges()
        result = self.sync_all_gauges()
        after = self._capture_current_gauges()

        drift = self._calculate_drift(before, after)
        drift.severity = self._classify_drift_severity(drift)
        result.drift = drift

        # 심각도별 대응
        if drift.severity == DriftSeverity.INCIDENT:
            self._raise_incident(drift)
        elif drift.severity == DriftSeverity.CRITICAL:
            self._send_alert(drift)
        elif drift.severity == DriftSeverity.WARNING:
            logger.warning(f"[Reconciler] Metric drift detected: {drift.max_drift_percent:.1f}%")

        return result

    def _capture_current_gauges(self) -> Dict[str, Dict[str, Any]]:
        """현재 Gauge 값 캡처 (Drift 계산용)."""
        result: Dict[str, Dict[str, Any]] = {"dlq_pending": {}}
        metrics = self._get_metrics()

        if metrics and hasattr(metrics, "dlq_pending_gauge"):
            for domain in self._get_domains():
                try:
                    # Prometheus gauge에서 현재 값 읽기 시도
                    gauge = metrics.dlq_pending_gauge.labels(domain=domain)
                    # _value는 내부 속성이므로 접근이 어려울 수 있음
                    # 대안: 마지막 동기화 결과 사용
                    if self._last_sync_result:
                        result["dlq_pending"][domain] = self._last_sync_result.dlq_pending.get(domain, 0)
                    else:
                        result["dlq_pending"][domain] = 0
                except Exception:
                    result["dlq_pending"][domain] = 0

        return result

    def _calculate_drift(
        self,
        before: Dict[str, Dict[str, Any]],
        after: Dict[str, Dict[str, Any]],
    ) -> DriftResult:
        """Drift 계산."""
        drift_details: Dict[str, Dict[str, Any]] = {}
        max_drift_percent = 0.0

        for key in after.get("dlq_pending", {}):
            before_val = before.get("dlq_pending", {}).get(key, 0)
            after_val = after.get("dlq_pending", {}).get(key, 0)

            if before_val == 0 and after_val == 0:
                drift_percent = 0.0
            elif before_val == 0:
                drift_percent = 100.0  # 0에서 값이 생긴 경우
            else:
                drift_percent = abs(after_val - before_val) / before_val * 100

            drift_details[key] = {
                "before": before_val,
                "after": after_val,
                "diff": after_val - before_val,
                "drift_percent": round(drift_percent, 2),
            }
            max_drift_percent = max(max_drift_percent, drift_percent)

        return DriftResult(
            details=drift_details,
            max_drift_percent=round(max_drift_percent, 2),
        )

    def _get_drift_config(self) -> "DriftThresholdConfig":
        """동적으로 저장된 Drift 설정 로드."""
        if self._drift_config:
            return self._drift_config

        try:
            from selfhealing.models.drift_config import DriftThresholdConfig
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            data = backend.get("drift_threshold_config")

            if data:
                return DriftThresholdConfig.from_dict(data)
            return DriftThresholdConfig()
        except Exception:
            # 기본값 사용
            from selfhealing.models.drift_config import DriftThresholdConfig

            return DriftThresholdConfig()

    def _classify_drift_severity(self, drift: DriftResult) -> str:
        """동적 설정 기반 Drift 심각도 분류."""
        config = self._get_drift_config()
        max_drift = drift.max_drift_percent / 100  # % to decimal

        if max_drift > config.incident_threshold:
            return DriftSeverity.INCIDENT
        elif max_drift > config.critical_threshold:
            return DriftSeverity.CRITICAL
        elif max_drift > config.warning_threshold:
            return DriftSeverity.WARNING
        return DriftSeverity.NORMAL

    def _raise_incident(self, drift: DriftResult) -> None:
        """
        50% 이상 Drift: 인시던트 발생.

        이 수준의 Drift는 단순한 오차가 아니라
        '이벤트 유실'을 의미합니다.
        """
        logger.critical(f"[Reconciler] METRIC INTEGRITY INCIDENT: {drift.max_drift_percent}%")

        if self.incident_service:
            try:
                self.incident_service.create_incident(
                    title="메트릭 신뢰도 붕괴 감지",
                    severity="critical",
                    category="metric_drift",
                    details={
                        "drift_percent": drift.max_drift_percent,
                        "details": drift.details,
                        "detected_at": datetime.now(timezone.utc).isoformat(),
                        "possible_cause": "이벤트 핸들러 미호출 또는 시스템 장애",
                        "recommended_action": [
                            "이벤트 핸들러 호출 여부 확인",
                            "최근 배포 변경사항 검토",
                            "로그에서 누락된 이벤트 추적",
                        ],
                    },
                )
            except Exception as e:
                logger.error(f"[Reconciler] Failed to create incident: {e}")

    def _send_alert(self, drift: DriftResult) -> None:
        """20~50% Drift: 알림 발송."""
        logger.error(f"[Reconciler] Critical metric drift: {drift.max_drift_percent}%")
        # Prometheus Alertmanager 또는 자체 알림 시스템 연동
        # 구체적인 구현은 프로젝트에 따라 다름

    @property
    def last_sync_time(self) -> Optional[datetime]:
        """마지막 동기화 시간."""
        return self._last_sync


# 싱글톤 인스턴스
_reconciler_instance: Optional[MetricReconciler] = None


def get_reconciler(
    adapter: Optional[MetricSourceAdapter] = None,
) -> MetricReconciler:
    """
    MetricReconciler 싱글톤 인스턴스를 반환합니다.

    Args:
        adapter: 사용할 어댑터 (None이면 기본 어댑터 사용)

    Returns:
        MetricReconciler 인스턴스
    """
    global _reconciler_instance

    if _reconciler_instance is None:
        _reconciler_instance = MetricReconciler(adapter=adapter)

    return _reconciler_instance


def reset_reconciler() -> None:
    """Reconciler 인스턴스를 리셋합니다 (테스트용)."""
    global _reconciler_instance
    _reconciler_instance = None


__all__ = [
    "DriftSeverity",
    "DriftResult",
    "SyncResult",
    "MetricReconciler",
    "get_reconciler",
    "reset_reconciler",
]

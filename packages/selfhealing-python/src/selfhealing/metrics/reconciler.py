"""
Metric Reconciler for Lazy Synchronization.

Provides mechanisms to synchronize Gauge metrics with actual data sources
during server startup and on-demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.adapters.metrics.base import (
    MetricSourceAdapter,
)
from selfhealing.adapters.metrics.factory import get_metric_adapter
from selfhealing.metrics.safe_gauge import clamp_non_negative, clamp_percentage
from selfhealing.utils.jitter import with_jitter

if TYPE_CHECKING:
    from selfhealing.models.drift_config import DriftThresholdConfig

logger = structlog.get_logger()


class DriftSeverity:
    """Drift 심각도 레벨."""

    NORMAL = "normal"  # < 5%: 정상 범위
    WARNING = "warning"  # 5~20%: 경고, 로그만 기록
    CRITICAL = "critical"  # 20~50%: 심각, 알림 발송
    INCIDENT = "incident"  # > 50%: 인시던트, 이벤트 유실 의심


@dataclass
class DriftResult:
    """Drift 계산 결과."""

    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    max_drift_percent: float = 0.0
    calculated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = DriftSeverity.NORMAL


@dataclass
class SyncResult:
    """동기화 결과."""

    synced_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    dlq_pending: dict[str, int] = field(default_factory=dict)
    circuit_breaker_states: dict[str, str] = field(default_factory=dict)
    retry_success_rates: dict[str, float] = field(default_factory=dict)
    drift: DriftResult | None = None


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
        adapter: MetricSourceAdapter | None = None,
        domains: list[str] | None = None,
        services: list[str] | None = None,
        drift_config: DriftThresholdConfig | None = None,
        incident_service: Any | None = None,
    ):
        """
        Initialize MetricReconciler.

        Args:
            adapter: 메트릭 소스 어댑터 (None이면 팩토리에서 가져옴)
            domains: 도메인 목록 (None이면 등록된 도메인 사용)
            services: 서비스 목록 (Circuit Breaker용, None이면 설정에서 로드)
            drift_config: Drift 임계값 설정
            incident_service: 인시던트 서비스 (선택)
        """
        self.adapter = adapter or get_metric_adapter()
        self._domains = domains
        self._services = services  # Domain-free: 설정에서 로드하거나 명시적으로 전달
        self._drift_config = drift_config
        self.incident_service = incident_service
        self._last_sync: datetime | None = None
        self._last_sync_result: SyncResult | None = None

    def _get_domains(self) -> list[str]:
        """도메인 목록 반환."""
        if self._domains:
            return self._domains
        try:
            from selfhealing.metrics.prometheus import get_domains

            return get_domains()
        except ImportError:
            return ["external_service", "internal_process", "async_task"]

    def _get_services(self) -> list[str]:
        """
        서비스 목록 반환 (Circuit Breaker용).

        Domain-Free 설계:
        - 명시적으로 전달된 services가 있으면 사용
        - 없으면 설정에서 로드 시도
        - 설정도 없으면 빈 리스트 (동적 발견 가능)
        """
        if self._services:
            return self._services
        try:
            from django.conf import settings

            return getattr(settings, "SELFHEALING_CIRCUIT_BREAKER_SERVICES", [])
        except ImportError:
            return []

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
                # 음수 방어: clamp_non_negative 유틸리티 사용
                safe_actual = clamp_non_negative(actual, f"dlq_pending[{domain}]")
                result.dlq_pending[domain] = safe_actual

                if metrics and hasattr(metrics, "dlq_pending_gauge"):
                    metrics.dlq_pending_gauge.labels(domain=domain).set(safe_actual)
            except Exception as e:
                logger.warning(
                    "reconciler.failed_sync_dlq_pending",
                    domain=domain,
                    error=e,
                )

        # Circuit Breaker 상태 동기화
        state_values = {"closed": 0, "open": 1, "half_open": 2}
        for service in self._get_services():
            try:
                state = self.adapter.get_circuit_breaker_state(service)
                result.circuit_breaker_states[service] = state

                if metrics and hasattr(metrics, "circuit_breaker_state"):
                    from selfhealing.services.cell_topology.cb_namespace import (
                        parse_composite_cb_name,
                    )

                    base_service, cell_id = parse_composite_cb_name(service)
                    state_value = state_values.get(state, 0)
                    metrics.circuit_breaker_state.labels(service_name=base_service, cell_id=cell_id).set(state_value)
            except Exception as e:
                logger.warning(
                    "reconciler.failed_sync_cb_state",
                    service=service,
                    error=e,
                )

        # 재시도 성공률 동기화
        for domain in self._get_domains():
            try:
                rate = self.adapter.get_retry_success_rate(domain)
                # 0-100 범위 클램핑: clamp_percentage 유틸리티 사용
                safe_rate = clamp_percentage(rate, f"retry_success_rate[{domain}]")
                result.retry_success_rates[domain] = safe_rate

                if metrics and hasattr(metrics, "retry_success_rate"):
                    metrics.retry_success_rate.labels(domain=domain).set(safe_rate)
            except Exception as e:
                logger.warning(
                    "reconciler.failed_sync_retry_rate",
                    domain=domain,
                    error=e,
                )

        self._last_sync = datetime.now(timezone.utc)
        self._last_sync_result = result
        logger.info(
            "reconciler.metrics_reconciled",
            count=len(result.dlq_pending),
        )

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

    def sync_domain_gauges(self, domain: str) -> dict[str, Any]:
        """
        특정 도메인의 Gauge만 동기화.

        Args:
            domain: 도메인 이름

        Returns:
            동기화 결과 딕셔너리
        """
        metrics = self._get_metrics()

        actual = self.adapter.get_dlq_pending_count(domain)
        # 음수 방어: clamp_non_negative 유틸리티 사용
        safe_actual = clamp_non_negative(actual, f"dlq_pending[{domain}]")
        if metrics and hasattr(metrics, "dlq_pending_gauge"):
            metrics.dlq_pending_gauge.labels(domain=domain).set(safe_actual)

        rate = self.adapter.get_retry_success_rate(domain)
        # 0-100 범위 클램핑: clamp_percentage 유틸리티 사용
        safe_rate = clamp_percentage(rate, f"retry_success_rate[{domain}]")
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
            logger.warning(
                "reconciler.metric_drift_detected",
                drift=drift.max_drift_percent,
            )

        return result

    def _capture_current_gauges(self) -> dict[str, dict[str, Any]]:
        """현재 Gauge 값 캡처 (Drift 계산용)."""
        result: dict[str, dict[str, Any]] = {"dlq_pending": {}}
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
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
    ) -> DriftResult:
        """Drift 계산."""
        drift_details: dict[str, dict[str, Any]] = {}
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

    def _get_drift_config(self) -> DriftThresholdConfig:
        """동적으로 저장된 Drift 설정 로드."""
        if self._drift_config:
            return self._drift_config

        try:
            from selfhealing.core.state_backend import get_state_backend
            from selfhealing.models.drift_config import DriftThresholdConfig

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
        logger.critical(
            "reconciler.metric_integrity_incident",
            drift=drift.max_drift_percent,
        )

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
                logger.exception(
                    "reconciler.failed_create_incident",
                    error=e,
                )

    def _send_alert(self, drift: DriftResult) -> None:
        """20~50% Drift: 알림 발송."""
        logger.error(
            "reconciler.critical_metric_drift",
            drift=drift.max_drift_percent,
        )
        # Prometheus Alertmanager 또는 자체 알림 시스템 연동
        # 구체적인 구현은 프로젝트에 따라 다름

    @property
    def last_sync_time(self) -> datetime | None:
        """마지막 동기화 시간."""
        return self._last_sync


# 싱글톤 인스턴스
_reconciler_instance: MetricReconciler | None = None


def get_reconciler(
    adapter: MetricSourceAdapter | None = None,
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

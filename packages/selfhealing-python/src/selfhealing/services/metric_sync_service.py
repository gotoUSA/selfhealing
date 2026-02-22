"""
Metric Sync Service - 메트릭 동기화 서비스 레이어.

비침습적 Drift 관리를 위한 수동 동기화 서비스.
주기적 DB 폴링을 제거하고, 운영자의 명시적 요청만 허용합니다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.adapters.metrics.factory import get_metric_adapter
from selfhealing.metrics.reconciler import (
    MetricReconciler,
    get_reconciler,
)

if TYPE_CHECKING:
    from selfhealing.adapters.metrics.base import MetricSourceAdapter

logger = structlog.get_logger()


# =============================================================================
# Constants
# =============================================================================


class DriftThresholds:
    """Drift 심각도 임계값 (퍼센트)."""

    WARNING = 5.0  # 5% 이상: 경고
    CRITICAL = 20.0  # 20% 이상: 심각
    INCIDENT = 50.0  # 50% 이상: 인시던트


# =============================================================================
# Service Layer
# =============================================================================


class MetricSyncService:
    """
    메트릭 동기화 서비스.

    Reconciler를 래핑하여 API에서 필요한 형태로 결과를 변환합니다.
    """

    def __init__(
        self,
        reconciler: MetricReconciler | None = None,
        adapter: MetricSourceAdapter | None = None,
    ):
        self.reconciler = reconciler or get_reconciler(adapter)
        self.adapter = adapter or get_metric_adapter()

    def sync_metrics(
        self,
        domains: list[str] | None = None,
        dry_run: bool = False,
        actor: str = "unknown",
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        메트릭 동기화 수행.

        Args:
            domains: 동기화할 도메인 목록 (None이면 전체)
            dry_run: True면 리포트만 생성
            actor: 동기화 수행자
            reason: 동기화 사유

        Returns:
            동기화 결과 딕셔너리
        """
        now = datetime.now(timezone.utc)

        # 현재 상태 캡처 (동기화 전)
        before_state = self._capture_current_state(domains)

        if dry_run:
            # Dry run: 실제 동기화 없이 리포트만
            after_state = self._get_actual_state(domains)
            results = self._build_results(before_state, after_state, domains)
            summary = self._calculate_summary(results)

            return {
                "status": "dry_run",
                "synced_at": now.isoformat(),
                "actor": actor,
                "dry_run": True,
                "results": results,
                "summary": summary,
            }

        # 실제 동기화 수행
        if domains:
            for domain in domains:
                self.reconciler.sync_domain_gauges(domain)
        else:
            self.reconciler.sync_all_gauges()

        # 동기화 후 상태
        after_state = self._get_actual_state(domains)
        results = self._build_results(before_state, after_state, domains)
        summary = self._calculate_summary(results)

        # Audit 로깅
        self._log_sync_action(actor, domains, dry_run, reason, summary)

        return {
            "status": "completed",
            "synced_at": now.isoformat(),
            "actor": actor,
            "dry_run": False,
            "results": results,
            "summary": summary,
        }

    def get_drift_report(self) -> dict[str, Any]:
        """
        현재 Drift 상태 조회 (읽기 전용).

        DB 조회는 수행하지만 Gauge 값은 변경하지 않습니다.

        Returns:
            Drift 리포트 딕셔너리
        """
        now = datetime.now(timezone.utc)

        # 인메모리 상태
        in_memory_state = self._capture_current_state(None)

        # 실제 DB 상태
        actual_state = self._get_actual_state(None)

        # Drift 계산
        metrics = self._calculate_drift_metrics(in_memory_state, actual_state)
        max_drift_percent = self._get_max_drift_percent(metrics)
        overall_health = self._classify_health(max_drift_percent)
        recommendation = self._get_recommendation(overall_health)

        return {
            "generated_at": now.isoformat(),
            "metrics": metrics,
            "overall_health": overall_health,
            "max_drift_percent": max_drift_percent,
            "recommendation": recommendation,
        }

    def _capture_current_state(self, domains: list[str] | None) -> dict[str, dict[str, Any]]:
        """현재 인메모리(Gauge) 상태 캡처."""
        result: dict[str, dict[str, Any]] = {
            "dlq_pending": {},
            "circuit_breaker": {},
            "retry_rate": {},
        }

        target_domains = domains or self._get_all_domains()

        # 현재 Gauge 값 읽기 시도
        try:
            from selfhealing.metrics.prometheus import get_metrics

            metrics = get_metrics()

            if metrics and hasattr(metrics, "dlq_pending_gauge"):
                for domain in target_domains:
                    try:
                        gauge = metrics.dlq_pending_gauge.labels(domain=domain)
                        # Prometheus Gauge의 현재 값 접근
                        result["dlq_pending"][domain] = gauge._value.get()
                    except Exception:
                        result["dlq_pending"][domain] = 0
        except ImportError:
            # prometheus 모듈 없으면 0으로 가정
            for domain in target_domains:
                result["dlq_pending"][domain] = 0

        return result

    def _get_actual_state(self, domains: list[str] | None) -> dict[str, dict[str, Any]]:
        """DB에서 실제 상태 조회."""
        result: dict[str, dict[str, Any]] = {
            "dlq_pending": {},
            "circuit_breaker": {},
            "retry_rate": {},
        }

        target_domains = domains or self._get_all_domains()

        for domain in target_domains:
            try:
                result["dlq_pending"][domain] = self.adapter.get_dlq_pending_count(domain)
            except Exception as e:
                logger.warning(
                    "failed_get_dlq_pending",
                    domain=domain,
                    error=e,
                )
                result["dlq_pending"][domain] = 0

            try:
                result["retry_rate"][domain] = self.adapter.get_retry_success_rate(domain)
            except Exception as e:
                logger.warning(
                    "failed_get_retry_rate",
                    domain=domain,
                    error=e,
                )
                result["retry_rate"][domain] = 0.0

        return result

    def _get_all_domains(self) -> list[str]:
        """등록된 모든 도메인 목록 반환."""
        try:
            from selfhealing.metrics.prometheus import get_domains

            return get_domains()
        except ImportError:
            from selfhealing.services.metrics.registry import get_registered_domains

            return get_registered_domains()

    def _build_results(
        self,
        before: dict[str, dict[str, Any]],
        after: dict[str, dict[str, Any]],
        domains: list[str] | None,
    ) -> dict[str, dict[str, Any]]:
        """동기화 결과 빌드."""
        results: dict[str, dict[str, Any]] = {}
        target_domains = domains or self._get_all_domains()

        for domain in target_domains:
            before_dlq = before.get("dlq_pending", {}).get(domain, 0)
            after_dlq = after.get("dlq_pending", {}).get(domain, 0)

            results[domain] = {
                "dlq_pending": {
                    "before": before_dlq,
                    "after": after_dlq,
                    "drift": after_dlq - before_dlq,
                }
            }

        return results

    def _calculate_summary(self, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
        """동기화 요약 계산."""
        total_drifts = 0
        max_drift_percent = 0.0

        for domain, domain_result in results.items():
            dlq = domain_result.get("dlq_pending", {})
            drift = abs(dlq.get("drift", 0))
            before = dlq.get("before", 0)

            if drift > 0:
                total_drifts += 1
                if before > 0:
                    drift_pct = (drift / before) * 100
                    max_drift_percent = max(max_drift_percent, drift_pct)
                elif dlq.get("after", 0) > 0:
                    max_drift_percent = max(max_drift_percent, 100.0)

        return {
            "total_drifts_detected": total_drifts,
            "total_drifts_corrected": total_drifts,  # 동기화 후에는 모두 보정됨
            "max_drift_percent": round(max_drift_percent, 2),
        }

    def _calculate_drift_metrics(
        self,
        in_memory: dict[str, dict[str, Any]],
        actual: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Drift 메트릭 계산."""
        metrics: dict[str, dict[str, Any]] = {
            "dlq_pending_count": {},
        }

        for domain in in_memory.get("dlq_pending", {}):
            in_mem = in_memory["dlq_pending"].get(domain, 0)
            act = actual.get("dlq_pending", {}).get(domain, 0)
            drift = act - in_mem

            # Drift 퍼센트 계산
            if in_mem > 0:
                drift_percent = abs(drift / in_mem) * 100
            elif act > 0:
                drift_percent = 100.0
            else:
                drift_percent = 0.0

            metrics["dlq_pending_count"][domain] = {
                "in_memory": in_mem,
                "actual": act,
                "drift": drift,
                "drift_percent": round(drift_percent, 2),
                "is_critical": drift_percent >= DriftThresholds.CRITICAL,
            }

        return metrics

    def _get_max_drift_percent(self, metrics: dict[str, dict[str, Any]]) -> float:
        """최대 Drift 퍼센트 추출."""
        max_pct = 0.0

        for metric_type, domains in metrics.items():
            for domain, info in domains.items():
                pct = info.get("drift_percent", 0.0)
                max_pct = max(max_pct, pct)

        return round(max_pct, 2)

    def _classify_health(self, max_drift_percent: float) -> str:
        """Drift 기반 상태 분류."""
        if max_drift_percent >= DriftThresholds.INCIDENT:
            return "incident"
        elif max_drift_percent >= DriftThresholds.CRITICAL:
            return "critical"
        elif max_drift_percent >= DriftThresholds.WARNING:
            return "warning"
        return "healthy"

    def _get_recommendation(self, health: str) -> str:
        """상태 기반 권장 조치."""
        recommendations = {
            "healthy": "",
            "warning": "Drift가 감지되었습니다. 모니터링을 권장합니다.",
            "critical": "심각한 Drift입니다. POST /api/self-healing/metrics/sync/ 실행을 권장합니다.",
            "incident": "인시던트 수준의 Drift입니다. 즉시 동기화하고 이벤트 유실 여부를 확인하세요.",
        }
        return recommendations.get(health, "")

    def _log_sync_action(
        self,
        actor: str,
        domains: list[str] | None,
        dry_run: bool,
        reason: str | None,
        summary: dict[str, Any],
    ) -> None:
        """Audit 로깅."""
        try:
            from selfhealing.audit.logger import (
                AuditConfigChangeEvent,
                AuditLogger,
                ConfigAuditAction,
            )

            audit_logger = AuditLogger.get_instance()
            event = AuditConfigChangeEvent(
                config_type="metric_sync",
                config_key="manual_sync",
                action=ConfigAuditAction.APPLY,
                old_value=None,
                new_value={
                    "domains": domains or "all",
                    "dry_run": dry_run,
                    "drifts_corrected": summary.get("total_drifts_corrected", 0),
                },
                reason=reason or "Manual metric synchronization",
                user=actor,
                source="api",
                metadata={
                    "category": "metric_reconciliation",
                    "summary": summary,
                },
            )
            audit_logger.log(event)
            logger.info(
                "metric_sync.audit_logged",
                actor=actor,
                summary=summary,
            )
        except Exception as e:
            # Audit 실패해도 동기화는 계속
            logger.warning(
                "metric_sync.failed_log_audit",
                error=e,
            )


# =============================================================================
# Singleton Service Instance
# =============================================================================


_metric_sync_service: MetricSyncService | None = None


def get_metric_sync_service() -> MetricSyncService:
    """MetricSyncService 싱글톤 인스턴스 반환."""
    global _metric_sync_service

    if _metric_sync_service is None:
        _metric_sync_service = MetricSyncService()

    return _metric_sync_service


def reset_metric_sync_service() -> None:
    """MetricSyncService 인스턴스 리셋 (테스트용)."""
    global _metric_sync_service
    _metric_sync_service = None


__all__ = [
    "DriftThresholds",
    "MetricSyncService",
    "get_metric_sync_service",
    "reset_metric_sync_service",
]

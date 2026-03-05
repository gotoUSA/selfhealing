"""
Circuit Breaker Mesh Coordinator

하류 CB 상태 기반 상류 임계치 동적 조정을 위한 메쉬 조율자.

핵심 책임:
- 하류 장애 전파: CB OPEN 이벤트 → 상류 CB 임계치 오버라이드
- 프리엠티브 Fallback: _downstream_open_set을 통해 should_allow() pre-check 차단
- 순차 복구 조율: 하류 CLOSED → 상류 fast-recovery 적용
- TTL Heartbeat: 오버라이드 자동 갱신/만료 관리

설계 원칙:
- 기존 EventBus/BlastRadius/LoadShedding 인프라를 소비만 한다 (중복 구현 금지)
- CB 상태 자체를 변경하지 않는다 (failure_threshold, recovery_timeout 조정만)
- Hot Path(should_allow())에서 외부 I/O를 절대 발생시키지 않는다
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.core.timezone import now
from selfhealing.services.circuit_breaker.config import CircuitState
from selfhealing.services.circuit_mesh import ThresholdOverride

if TYPE_CHECKING:
    from selfhealing.services.circuit_breaker.blast_radius_integration import (
        ServiceDependencyGraph,
    )
    from selfhealing.services.circuit_breaker.service import CircuitBreakerService
    from selfhealing.services.circuit_mesh.store import MeshOverrideStore
    from selfhealing.services.event_bus.bus import SelfHealingEvent
    from selfhealing.settings.circuit_mesh import CircuitMeshSettings

logger = structlog.get_logger()


class MeshCoordinator:
    """CB 메쉬 조율자 — 하류 상태 기반 상류 임계치 동적 조정."""

    def __init__(
        self,
        dependency_graph: ServiceDependencyGraph,
        cb_service: CircuitBreakerService,
        override_store: MeshOverrideStore,
        settings: CircuitMeshSettings,
    ) -> None:
        self._graph = dependency_graph
        self._cb = cb_service
        self._store = override_store
        self._settings = settings
        self._recovery_queue: list[str] = []

        # 프리엠티브 Fallback을 위한 로컬 인메모리 Set.
        # Hot Path(should_allow())에서 O(1) 조회, 외부 I/O 없음.
        # EventBus(Redis Pub/Sub)를 통해 멀티 인스턴스 간 비동기 동기화.
        self._downstream_open_set: set[str] = set()

    def initialize(self) -> None:
        """
        MeshCoordinator 초기화: CB 서비스에 downstream checker 등록.

        이 메서드는 coordinator 생성 후 명시적으로 호출해야 한다.
        """
        self._cb.register_downstream_checker(self._check_downstream_health)
        logger.info("mesh_coordinator.initialized")

    def _check_downstream_health(self, service_name: str) -> bool:
        """
        service_name이 의존하는 하류 중 OPEN인 것이 있으면 False.

        O(1) set lookup만 수행, 외부 I/O 없음.
        """
        if not self._downstream_open_set:
            return True
        dependencies = self._graph.get_dependencies(service_name)
        return not any(dep in self._downstream_open_set for dep in dependencies)

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def on_downstream_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB OPEN 이벤트 핸들러.

        1. _downstream_open_set에 추가 (프리엠티브 Fallback 활성화)
        2. dependency graph에서 감쇠 전파로 상류 목록 조회
        3. depth별 차등 배율로 상류 CB 임계치 오버라이드 적용
        4. 순차 복구 대기열에 하류 서비스 추가
        """
        downstream = event.data.get("service_name", "")
        if not downstream:
            return

        self._downstream_open_set.add(downstream)

        current_overrides = self._store.get_all()
        if len(current_overrides) >= self._settings.max_concurrent_overrides:
            logger.warning(
                "mesh_coordinator.max_concurrent_overrides_reached",
                current=len(current_overrides),
                max=self._settings.max_concurrent_overrides,
            )
            return

        affected = self._graph.get_dependents_recursive(
            downstream,
            max_depth=self._settings.propagation_max_depth,
        )

        for upstream, depth in affected:
            damping = self._settings.propagation_damping_factor ** (depth - 1)
            effective_threshold_multiplier = 1.0 + (
                (self._settings.threshold_multiplier - 1.0) * damping
            )
            effective_recovery_multiplier = 1.0 + (
                (self._settings.recovery_timeout_multiplier - 1.0) * damping
            )

            current_config = self._cb.get_effective_config(upstream)
            override = ThresholdOverride(
                service_name=upstream,
                original_failure_threshold=current_config.failure_threshold,
                adjusted_failure_threshold=int(
                    current_config.failure_threshold * effective_threshold_multiplier
                ),
                original_recovery_timeout=current_config.recovery_timeout,
                adjusted_recovery_timeout=int(
                    current_config.recovery_timeout * effective_recovery_multiplier
                ),
                reason=f"downstream:{downstream} OPEN (depth={depth})",
                expires_at=now()
                + timedelta(seconds=self._settings.override_ttl_seconds),
                renewal_count=0,
            )
            self._store.set(upstream, override)
            self._cb.apply_threshold_override(upstream, override)

            logger.info(
                "mesh_coordinator.upstream_threshold_adjusted",
                upstream=upstream,
                downstream=downstream,
                depth=depth,
                damping=damping,
                adjusted_failure_threshold=override.adjusted_failure_threshold,
                adjusted_recovery_timeout=override.adjusted_recovery_timeout,
            )

            self._record_metric("override_applied")

        if downstream not in self._recovery_queue:
            self._recovery_queue.append(downstream)

    def on_downstream_half_opened(self, event: SelfHealingEvent) -> None:
        """
        하류 CB HALF_OPEN 이벤트 핸들러.

        HALF_OPEN 단계에서는 오버라이드와 _downstream_open_set 모두 유지.
        """
        service = event.data.get("service_name", "")
        logger.info(
            "mesh_coordinator.downstream_half_opened",
            service=service,
            action="maintain_upstream_overrides",
        )

    def on_downstream_closed(self, event: SelfHealingEvent) -> None:
        """
        하류 CB CLOSED 이벤트 핸들러.

        하류 복구 완료 시:
        1. _downstream_open_set에서 제거 (프리엠티브 Fallback 해제)
        2. 상류 오버라이드를 fast-recovery 값으로 교체
        3. 복구 대기열에서 제거
        """
        downstream = event.data.get("service_name", "")
        if not downstream:
            return

        self._downstream_open_set.discard(downstream)

        affected = self._graph.get_dependents_recursive(
            downstream,
            max_depth=self._settings.propagation_max_depth,
        )

        for upstream, depth in affected:
            existing = self._store.get(upstream)
            if existing is None:
                continue

            fast_recovery_override = ThresholdOverride(
                service_name=upstream,
                original_failure_threshold=existing.original_failure_threshold,
                adjusted_failure_threshold=existing.original_failure_threshold,
                original_recovery_timeout=existing.original_recovery_timeout,
                adjusted_recovery_timeout=self._settings.fast_recovery_timeout_seconds,
                reason=f"downstream:{downstream} RECOVERED → fast-recovery",
                expires_at=now()
                + timedelta(seconds=self._settings.fast_recovery_timeout_seconds + 10),
                renewal_count=0,
            )
            self._store.set(upstream, fast_recovery_override)
            self._cb.apply_threshold_override(upstream, fast_recovery_override)

            logger.info(
                "mesh_coordinator.fast_recovery_applied",
                upstream=upstream,
                downstream=downstream,
                fast_recovery_timeout=self._settings.fast_recovery_timeout_seconds,
            )

            self._record_metric("fast_recovery")

        if downstream in self._recovery_queue:
            self._recovery_queue.remove(downstream)

    def on_fast_recovery_completed(self, event: SelfHealingEvent) -> None:
        """
        상류 CB가 fast-recovery 후 CLOSED 전이 시 최종 오버라이드 해제.

        fast-recovery override의 짧은 TTL이 만료되면
        get_effective_config()에서 자동 정리되므로, 이 핸들러는
        즉시 정리를 위한 보조 역할이다.
        """
        service = event.data.get("service_name", "")
        existing = self._store.get(service)
        if existing and "fast-recovery" in existing.reason:
            self._store.remove(service)
            self._cb.remove_threshold_override(service)

            self._record_metric("override_released")

    # =========================================================================
    # TTL Heartbeat
    # =========================================================================

    def check_override_renewals(self) -> dict[str, Any]:
        """
        주기적 TTL 갱신 체크.
        Celery beat task로 snapshot_interval_seconds 주기에 실행.
        """
        all_overrides = self._store.get_all()
        renewed = 0
        expired = 0
        escalated = 0

        for service_name, override in list(all_overrides.items()):
            remaining = (override.expires_at - now()).total_seconds()
            if remaining > self._settings.renewal_check_threshold_seconds:
                continue

            downstream = self._extract_downstream_from_reason(override.reason)
            if downstream is None:
                continue

            downstream_state = self._cb.get_state(downstream)

            if downstream_state == CircuitState.OPEN:
                if override.renewal_count < self._settings.max_renewals:
                    override.expires_at = now() + timedelta(
                        seconds=self._settings.override_ttl_seconds
                    )
                    override.renewal_count += 1
                    self._store.set(service_name, override)
                    self._cb.apply_threshold_override(service_name, override)

                    logger.info(
                        "mesh_coordinator.override_renewed",
                        service=service_name,
                        downstream=downstream,
                        renewal_count=override.renewal_count,
                        max_renewals=self._settings.max_renewals,
                    )
                    renewed += 1

                    self._record_metric("override_renewed")
                else:
                    logger.warning(
                        "mesh_coordinator.max_renewals_exceeded",
                        service=service_name,
                        downstream=downstream,
                        renewal_count=override.renewal_count,
                        action="escalate_to_emergency_coordinator",
                    )
                    escalated += 1

                    self._record_metric("escalation")
            else:
                self._store.remove(service_name)
                self._cb.remove_threshold_override(service_name)
                expired += 1

                self._record_metric("override_released")

        return {
            "success": True,
            "renewed": renewed,
            "expired": expired,
            "escalated": escalated,
            "total_overrides": len(all_overrides),
        }

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_recovery_order(self) -> list[str]:
        """
        순차 복구 순서 반환.

        dependency graph의 위상 정렬(topological sort) 기반:
        하류(리프) → 상류(루트) 순서로 Half-Open 시도.
        """
        if not self._recovery_queue:
            return []

        return self._graph.topological_sort_subset(
            self._recovery_queue,
            direction="leaves_first",
        )

    def get_mesh_snapshot(self) -> dict[str, Any]:
        """현재 메쉬 상태 스냅샷 반환."""
        return {
            "timestamp": now().isoformat(),
            "downstream_open_set": sorted(self._downstream_open_set),
            "active_overrides": {
                name: {
                    "adjusted_failure_threshold": o.adjusted_failure_threshold,
                    "adjusted_recovery_timeout": o.adjusted_recovery_timeout,
                    "reason": o.reason,
                    "expires_at": o.expires_at.isoformat(),
                    "renewal_count": o.renewal_count,
                }
                for name, o in self._store.get_all().items()
            },
            "recovery_queue": list(self._recovery_queue),
            "recovery_order": self.get_recovery_order(),
        }

    # =========================================================================
    # Internal Helpers
    # =========================================================================

    @staticmethod
    def _extract_downstream_from_reason(reason: str) -> str | None:
        """reason 문자열에서 하류 서비스명 추출."""
        if not reason.startswith("downstream:"):
            return None
        return reason.split(":")[1].split()[0]

    @staticmethod
    def _record_metric(metric_type: str) -> None:
        """Prometheus 메트릭 기록 (graceful degradation)."""
        try:
            from selfhealing.metrics.prometheus import get_metrics

            metrics = get_metrics()
            if not metrics._initialized:
                return

            metric_map = {
                "override_applied": "mesh_override_applied_total",
                "override_released": "mesh_override_released_total",
                "override_renewed": "mesh_override_renewed_total",
                "fast_recovery": "mesh_fast_recovery_total",
                "escalation": "mesh_escalation_total",
                "preemptive_fallback": "mesh_preemptive_fallback_total",
            }
            attr = metric_map.get(metric_type)
            if attr and hasattr(metrics, attr):
                getattr(metrics, attr).inc()
        except Exception:
            pass


def register_mesh_handlers(coordinator: MeshCoordinator) -> None:
    """MeshCoordinator의 이벤트 핸들러를 EventBus에 등록."""
    from selfhealing.services.event_bus import EventType, get_event_bus

    bus = get_event_bus()
    bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, coordinator.on_downstream_opened)
    bus.subscribe(
        EventType.CIRCUIT_BREAKER_HALF_OPENED, coordinator.on_downstream_half_opened
    )
    bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, coordinator.on_downstream_closed)

    logger.info("mesh_coordinator.event_handlers_registered")


# =============================================================================
# Singleton
# =============================================================================

_coordinator: MeshCoordinator | None = None


def get_mesh_coordinator() -> MeshCoordinator | None:
    """현재 초기화된 MeshCoordinator 인스턴스 반환. 미초기화 시 None."""
    return _coordinator


def set_mesh_coordinator(coordinator: MeshCoordinator) -> None:
    """MeshCoordinator 싱글톤 등록."""
    global _coordinator
    _coordinator = coordinator


def reset_mesh_coordinator() -> None:
    """MeshCoordinator 싱글톤 초기화 (테스트용)."""
    global _coordinator
    _coordinator = None

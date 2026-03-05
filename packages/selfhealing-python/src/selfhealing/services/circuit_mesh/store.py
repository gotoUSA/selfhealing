"""
Mesh Override Store

오버라이드 상태 저장소 인터페이스와 구현.

L1 인메모리 + L2 Redis Two-Tier 패턴.
읽기 경로(Hot Path): L1 dict 조회 → O(1), 외부 I/O 없음.
쓰기 경로: L1 즉시 갱신 → L2 비동기 쓰기 → EventBus 무효화 이벤트 발행.

참조: adapters/memory/layered_repository/repository_operations.py
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

import structlog

from selfhealing.core.timezone import now
from selfhealing.services.circuit_mesh import ThresholdOverride

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface
    from selfhealing.services.event_bus.bus import SelfHealingEvent, SelfHealingEventBus

logger = structlog.get_logger()


@runtime_checkable
class MeshOverrideStore(Protocol):
    """오버라이드 상태 저장소 인터페이스."""

    def get(self, service_name: str) -> ThresholdOverride | None: ...
    def set(self, service_name: str, override: ThresholdOverride) -> None: ...
    def remove(self, service_name: str) -> None: ...
    def get_all(self) -> dict[str, ThresholdOverride]: ...


class InMemoryMeshOverrideStore:
    """
    인메모리 전용 오버라이드 저장소 (테스트/단일 인스턴스용).

    L1 dict만 사용하며 L2 동기화 없음.
    """

    def __init__(self) -> None:
        self._store: dict[str, ThresholdOverride] = {}

    def get(self, service_name: str) -> ThresholdOverride | None:
        override = self._store.get(service_name)
        if override and now() > override.expires_at:
            self._store.pop(service_name, None)
            return None
        return override

    def set(self, service_name: str, override: ThresholdOverride) -> None:
        self._store[service_name] = override

    def remove(self, service_name: str) -> None:
        self._store.pop(service_name, None)

    def get_all(self) -> dict[str, ThresholdOverride]:
        expired = [k for k, v in self._store.items() if now() > v.expires_at]
        for k in expired:
            self._store.pop(k, None)
        return dict(self._store)


class TwoTierMeshOverrideStore:
    """
    L1 로컬 dict + L2 Redis Hash 기반 Two-Tier 오버라이드 저장소.

    읽기 경로 (Hot Path):
        get()/get_all() → L1 dict 조회 → O(1), 외부 I/O 없음

    쓰기 경로:
        set()/remove() → L1 dict 즉시 갱신 → L2 Redis 비동기 쓰기
        → EventBus로 무효화 이벤트 발행 → 타 인스턴스 L1 갱신

    참조: adapters/memory/layered_repository/repository_operations.py
    """

    REDIS_KEY_PREFIX = "selfhealing:mesh:override:"

    def __init__(
        self,
        cache: CacheProviderInterface,
        event_bus: SelfHealingEventBus,
    ) -> None:
        self._l1: dict[str, ThresholdOverride] = {}
        self._cache = cache
        self._bus = event_bus

    def get(self, service_name: str) -> ThresholdOverride | None:
        """L1에서 즉시 반환. 외부 I/O 없음."""
        override = self._l1.get(service_name)
        if override and now() > override.expires_at:
            self._l1.pop(service_name, None)
            return None
        return override

    def set(self, service_name: str, override: ThresholdOverride) -> None:
        """L1 즉시 갱신 + L2 비동기 쓰기 + 무효화 이벤트 발행."""
        self._l1[service_name] = override
        self._sync_to_l2(service_name, override)
        self._publish_invalidation(service_name, "set")

    def remove(self, service_name: str) -> None:
        """L1 즉시 제거 + L2 비동기 삭제 + 무효화 이벤트 발행."""
        self._l1.pop(service_name, None)
        self._remove_from_l2(service_name)
        self._publish_invalidation(service_name, "remove")

    def get_all(self) -> dict[str, ThresholdOverride]:
        """L1 전체 반환. 외부 I/O 없음."""
        expired = [k for k, v in self._l1.items() if now() > v.expires_at]
        for k in expired:
            self._l1.pop(k, None)
        return dict(self._l1)

    def on_invalidation_event(self, event: SelfHealingEvent) -> None:
        """타 인스턴스의 변경 수신 → L2에서 최신 값을 가져와 L1 갱신."""
        service_name = event.data.get("service_name", "")
        action = event.data.get("action", "")
        if action == "remove":
            self._l1.pop(service_name, None)
        else:
            override = self._fetch_from_l2(service_name)
            if override:
                self._l1[service_name] = override

    def check_drift(self) -> dict[str, Any]:
        """L1↔L2 drift 감지. snapshot_interval_seconds 주기로 호출."""
        drift_count = 0
        try:
            for service_name in list(self._l1.keys()):
                l2_key = f"{self.REDIS_KEY_PREFIX}{service_name}"
                l2_data = self._cache.get(l2_key)
                if l2_data is None:
                    drift_count += 1
        except Exception as e:
            logger.warning("mesh_override_store.drift_check_failed", error=str(e))

        if drift_count > 0:
            try:
                from selfhealing.metrics.prometheus import get_metrics

                metrics = get_metrics()
                if metrics._initialized and hasattr(
                    metrics, "mesh_override_store_drift_total"
                ):
                    metrics.mesh_override_store_drift_total.inc(drift_count)
            except Exception:
                pass

        return {"drift_detected": drift_count > 0, "drift_count": drift_count}

    def _sync_to_l2(self, service_name: str, override: ThresholdOverride) -> None:
        """L2 Redis에 오버라이드 동기화 (per-key, 원자적 쓰기)."""
        try:
            data = {
                "service_name": override.service_name,
                "original_failure_threshold": override.original_failure_threshold,
                "adjusted_failure_threshold": override.adjusted_failure_threshold,
                "original_recovery_timeout": override.original_recovery_timeout,
                "adjusted_recovery_timeout": override.adjusted_recovery_timeout,
                "reason": override.reason,
                "expires_at": override.expires_at.isoformat(),
                "renewal_count": override.renewal_count,
            }
            l2_key = f"{self.REDIS_KEY_PREFIX}{service_name}"
            self._cache.set(l2_key, json.dumps(data))
        except Exception as e:
            logger.warning(
                "mesh_override_store.l2_sync_failed",
                service_name=service_name,
                error=str(e),
            )

    def _remove_from_l2(self, service_name: str) -> None:
        """L2 Redis에서 오버라이드 제거."""
        try:
            l2_key = f"{self.REDIS_KEY_PREFIX}{service_name}"
            self._cache.delete(l2_key)
        except Exception as e:
            logger.warning(
                "mesh_override_store.l2_remove_failed",
                service_name=service_name,
                error=str(e),
            )

    def _fetch_from_l2(self, service_name: str) -> ThresholdOverride | None:
        """L2 Redis에서 오버라이드 조회."""
        try:
            from datetime import datetime, timezone

            l2_key = f"{self.REDIS_KEY_PREFIX}{service_name}"
            raw = self._cache.get(l2_key)
            if not raw:
                return None
            data = json.loads(raw) if isinstance(raw, str) else raw
            return ThresholdOverride(
                service_name=data["service_name"],
                original_failure_threshold=data["original_failure_threshold"],
                adjusted_failure_threshold=data["adjusted_failure_threshold"],
                original_recovery_timeout=data["original_recovery_timeout"],
                adjusted_recovery_timeout=data["adjusted_recovery_timeout"],
                reason=data["reason"],
                expires_at=datetime.fromisoformat(data["expires_at"]).replace(
                    tzinfo=timezone.utc
                )
                if data["expires_at"]
                else now(),
                renewal_count=data.get("renewal_count", 0),
            )
        except Exception as e:
            logger.warning(
                "mesh_override_store.l2_fetch_failed",
                service_name=service_name,
                error=str(e),
            )
            return None

    def _publish_invalidation(self, service_name: str, action: str) -> None:
        """무효화 이벤트 발행."""
        try:
            self._bus.emit(
                event_type="mesh_override_invalidation",
                data={"service_name": service_name, "action": action},
                source="mesh_override_store",
            )
        except Exception as e:
            logger.warning(
                "mesh_override_store.invalidation_publish_failed",
                service_name=service_name,
                error=str(e),
            )

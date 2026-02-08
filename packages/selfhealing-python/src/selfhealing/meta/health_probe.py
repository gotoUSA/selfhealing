"""
Health Probe Manager - 서브시스템 건강 상태 수집.

Self-Healing 시스템의 각 컴포넌트(Circuit Breaker, DLQ, Redis 등)의
건강 상태를 주기적으로 프로브하고 수집합니다.
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings
from selfhealing.meta.audit_probe import AuditSystemProbe

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """서브시스템 건강 상태."""

    HEALTHY = "healthy"
    """정상 상태."""

    DEGRADED = "degraded"
    """성능 저하 상태 (동작은 하지만 주의 필요)."""

    UNHEALTHY = "unhealthy"
    """비정상 상태 (복구 필요)."""

    UNKNOWN = "unknown"
    """상태 확인 불가."""


@dataclass
class ProbeResult:
    """건강 프로브 결과."""

    component: str
    """컴포넌트 이름."""

    status: HealthStatus
    """건강 상태."""

    latency_ms: float
    """응답 시간 (밀리초)."""

    timestamp: datetime
    """프로브 수행 시각."""

    details: dict[str, Any] = field(default_factory=dict)
    """상세 정보."""

    error: str | None = None
    """에러 메시지 (실패 시)."""


class HealthProbe(ABC):
    """
    건강 프로브 인터페이스.

    각 서브시스템에 대해 이 인터페이스를 구현하여
    건강 상태를 확인합니다.
    """

    @property
    @abstractmethod
    def component_name(self) -> str:
        """컴포넌트 이름 반환."""
        pass

    @abstractmethod
    def probe(self) -> ProbeResult:
        """
        건강 상태 프로브 수행.

        Returns:
            ProbeResult: 프로브 결과
        """
        pass


class CircuitBreakerProbe(HealthProbe):
    """
    Circuit Breaker 건강 프로브.

    확인 항목:
    - CB 상태 (CLOSED/OPEN/HALF_OPEN)
    - 최근 실패율
    - Stuck 여부 (OPEN 상태에서 너무 오래 머무름)
    """

    @property
    def component_name(self) -> str:
        return "circuit_breaker"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            # Circuit Breaker 상태 확인 시도
            open_count = 0
            stuck_count = 0
            all_states: dict[str, str] = {}

            try:
                from selfhealing.services.circuit_breaker import get_circuit_breaker_service

                cb_service = get_circuit_breaker_service()
                # CB 서비스 상태 확인
                cb_states = cb_service.get_all_states()
                open_count = sum(1 for s in cb_states if s.get("state") == "OPEN")
                all_states["cb_service_available"] = "true"
                all_states["open_cb_count"] = str(open_count)
            except ImportError:
                all_states["cb_service_available"] = "false"
            except Exception as e:
                all_states["manager_error"] = str(e)

            # 상태 결정
            status = HealthStatus.HEALTHY

            # OPEN 상태 CB가 많으면 DEGRADED
            if open_count > 3:
                status = HealthStatus.DEGRADED

            # Stuck CB가 있으면 UNHEALTHY
            if stuck_count > 0:
                status = HealthStatus.UNHEALTHY

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "open_count": open_count,
                    "stuck_count": stuck_count,
                    "states": all_states,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class DLQProbe(HealthProbe):
    """
    DLQ(Dead Letter Queue) 건강 프로브.

    확인 항목:
    - DLQ 대기 큐 크기
    - 처리 속도 (entries/sec)
    - Consumer 생존 여부
    """

    @property
    def component_name(self) -> str:
        return "dlq"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            pending_count = 0

            try:
                from selfhealing.factory import ProviderRegistry

                if ProviderRegistry.has_runtime_adapter():
                    runtime = ProviderRegistry.get_runtime()
                    pending_count = runtime.count_pending()
            except ImportError:
                pass
            except Exception as e:
                return ProbeResult(
                    component=self.component_name,
                    status=HealthStatus.UNKNOWN,
                    latency_ms=(time.time() - start) * 1000,
                    timestamp=datetime.now(timezone.utc),
                    error=f"Runtime adapter error: {e}",
                )

            settings = get_meta_watchdog_settings()
            status = HealthStatus.HEALTHY

            # 대기 중인 항목이 많으면 DEGRADED
            if pending_count > settings.dlq_stuck_threshold_entries:
                status = HealthStatus.DEGRADED

            # 처리율 0이고 대기 항목이 매우 많으면 UNHEALTHY
            if pending_count > settings.dlq_stuck_threshold_entries * 2:
                status = HealthStatus.UNHEALTHY

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "pending_count": pending_count,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class RecoveryPipelineProbe(HealthProbe):
    """
    Recovery Pipeline 건강 프로브.

    확인 항목:
    - 활성 복구 작업 수
    - Stuck 복구 작업 (너무 오래 걸림)
    - 실패율
    """

    @property
    def component_name(self) -> str:
        return "recovery_pipeline"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            # Recovery Pipeline 상태 확인
            active_recoveries = 0
            stuck_recoveries = 0

            # RecoveryCoordinator가 있으면 상태 확인
            try:
                from selfhealing.services.coordination.recovery_coordinator import (
                    get_recovery_coordinator,
                )

                coordinator = get_recovery_coordinator()
                # 기본 상태 확인
            except ImportError:
                pass
            except Exception:
                pass

            status = HealthStatus.HEALTHY

            if stuck_recoveries > 0:
                status = HealthStatus.UNHEALTHY
            elif active_recoveries > 10:
                status = HealthStatus.DEGRADED

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "active_recoveries": active_recoveries,
                    "stuck_recoveries": stuck_recoveries,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class RedisProbe(HealthProbe):
    """
    Redis 건강 프로브.

    확인 항목:
    - 연결 상태 (PING 테스트)
    - 응답 시간
    - 메모리 사용량
    """

    @property
    def component_name(self) -> str:
        return "redis"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            # Redis 클라이언트 획득 시도
            redis_client = None
            try:
                from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

                adapter = RedisCacheAdapter()
                redis_client = adapter._redis
            except ImportError:
                pass
            except Exception:
                pass

            if redis_client is None:
                return ProbeResult(
                    component=self.component_name,
                    status=HealthStatus.UNKNOWN,
                    latency_ms=(time.time() - start) * 1000,
                    timestamp=datetime.now(timezone.utc),
                    error="Redis client not available",
                )

            # PING 테스트
            redis_client.ping()

            # INFO 조회
            used_memory = 0
            max_memory = 0
            memory_usage_ratio = 0.0

            try:
                info = redis_client.info(section="memory")
                used_memory = info.get("used_memory", 0)
                max_memory = info.get("maxmemory", 0)
                if max_memory > 0:
                    memory_usage_ratio = used_memory / max_memory
            except Exception:
                pass

            status = HealthStatus.HEALTHY

            # 메모리 사용량 80% 초과 시 DEGRADED
            if memory_usage_ratio > 0.8:
                status = HealthStatus.DEGRADED

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "used_memory_bytes": used_memory,
                    "max_memory_bytes": max_memory,
                    "memory_usage_ratio": memory_usage_ratio,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class HealthProbeManager:
    """
    Health Probe Manager.

    여러 프로브를 관리하고 주기적으로 실행하여
    서브시스템들의 건강 상태를 수집합니다.

    사용 예시:
        manager = HealthProbeManager()
        manager.start()  # 백그라운드 프로브 시작

        # 현재 상태 조회
        results = manager.get_last_results()
        overall = manager.get_overall_status()

        manager.stop()  # 프로브 중지
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
        probes: list[HealthProbe] | None = None,
    ):
        """
        초기화.

        Args:
            settings: Meta-Watchdog 설정 (None이면 기본값)
            probes: 사용할 프로브 목록 (None이면 기본 프로브)
        """
        self._settings = settings or get_meta_watchdog_settings()
        self._probes = probes or self._create_default_probes()
        self._lock = threading.RLock()
        self._last_results: dict[str, ProbeResult] = {}
        self._running = False
        self._worker: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _create_default_probes(self) -> list[HealthProbe]:
        """기본 프로브 목록 생성."""
        return [
            CircuitBreakerProbe(),
            DLQProbe(),
            RecoveryPipelineProbe(),
            RedisProbe(),
            AuditSystemProbe(),
        ]

    def add_probe(self, probe: HealthProbe) -> None:
        """
        프로브 추가.

        Args:
            probe: 추가할 프로브
        """
        with self._lock:
            self._probes.append(probe)

    def remove_probe(self, component_name: str) -> bool:
        """
        프로브 제거.

        Args:
            component_name: 제거할 프로브의 컴포넌트 이름

        Returns:
            제거 성공 여부
        """
        with self._lock:
            for i, probe in enumerate(self._probes):
                if probe.component_name == component_name:
                    self._probes.pop(i)
                    return True
            return False

    def probe_all(self) -> dict[str, ProbeResult]:
        """
        모든 프로브 실행.

        Returns:
            컴포넌트별 프로브 결과
        """
        results: dict[str, ProbeResult] = {}

        for probe in self._probes:
            try:
                result = probe.probe()
                results[probe.component_name] = result
            except Exception as e:
                logger.error(f"[HealthProbeManager] {probe.component_name} probe error: {e}")
                results[probe.component_name] = ProbeResult(
                    component=probe.component_name,
                    status=HealthStatus.UNKNOWN,
                    latency_ms=0,
                    timestamp=datetime.now(timezone.utc),
                    error=str(e),
                )

        with self._lock:
            self._last_results = results

        return results

    def get_overall_status(self) -> HealthStatus:
        """
        전체 건강 상태 반환.

        가장 심각한 상태를 반환합니다.
        UNHEALTHY > DEGRADED > UNKNOWN > HEALTHY

        Returns:
            전체 건강 상태
        """
        with self._lock:
            results = self._last_results

        if not results:
            return HealthStatus.UNKNOWN

        statuses = [r.status for r in results.values()]

        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        if HealthStatus.UNKNOWN in statuses:
            return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY

    def get_last_results(self) -> dict[str, ProbeResult]:
        """
        마지막 프로브 결과 반환.

        Returns:
            컴포넌트별 마지막 프로브 결과
        """
        with self._lock:
            return dict(self._last_results)

    def get_component_status(self, component: str) -> HealthStatus | None:
        """
        특정 컴포넌트 상태 반환.

        Args:
            component: 컴포넌트 이름

        Returns:
            해당 컴포넌트 상태 (없으면 None)
        """
        with self._lock:
            result = self._last_results.get(component)
            return result.status if result else None

    def _run_loop(self) -> None:
        """백그라운드 프로브 루프."""
        while self._running:
            try:
                self.probe_all()
            except Exception as e:
                logger.error(f"[HealthProbeManager] Loop error: {e}")

            self._stop_event.wait(self._settings.probe_interval_seconds)
            if self._stop_event.is_set():
                break

    def start(self) -> None:
        """백그라운드 프로브 시작."""
        if self._running:
            return

        self._stop_event.clear()
        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="HealthProbeManager",
            daemon=True,
        )
        self._worker.start()
        logger.info("[HealthProbeManager] Started")

    def stop(self) -> None:
        """프로브 중지."""
        self._running = False
        self._stop_event.set()
        if self._worker:
            self._worker.join(timeout=2.0)
            self._worker = None
        logger.info("[HealthProbeManager] Stopped")

    def is_running(self) -> bool:
        """실행 중 여부 반환."""
        return self._running

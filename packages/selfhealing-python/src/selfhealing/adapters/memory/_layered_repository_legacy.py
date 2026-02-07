"""
[DEPRECATED] Layered Circuit Breaker State Repository — 레거시 모놀리식 구현.

리팩토링된 구현은 adapters/memory/layered_repository/ 패키지를 사용하세요.
이 파일은 마이그레이션 기간 참조용으로만 보존됩니다.

하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).

설계 원칙:
- L1 (Local Memory): 모든 판정은 1차적으로 메모리에서 즉시 수행 (0.01ms)
- L2 (Shared Storage): Redis나 DB는 백그라운드에서 비동기적으로 동기화
- 타임아웃 적용: L2 응답이 늦으면 즉시 포기하고 L1만으로 동작 (Fail-Fast)
- Shadow Logging: L2 장애 시 발생한 변경사항을 로컬에 기록
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from selfhealing.adapters.memory.base import _now
from selfhealing.adapters.memory.drift_reconciliation import (
    DriftReconciler,
    DriftReconciliationResult,
    get_drift_reconciler,
)
from selfhealing.adapters.memory.shadow_logger import get_shadow_logger
from selfhealing.interfaces.repositories import (
    CircuitBreakerStateData,
    CircuitBreakerStateRepository,
)

# Avoid circular import - import InMemoryCircuitBreakerStateRepository lazily
if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


class LayeredCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """
    하이브리드 레이어드 저장소 (L1 Memory + L2 Shared Storage).

    장점:
    - 외부 의존성(Redis/DB)이 잠시 죽어도 시스템은 L1만으로 계속 동작
    - 분산 환경에서도 최종적으로 일관성 유지 (Eventual Consistency)
    - 호스트 DB에 침투하지 않음 (L2는 opt-in)

    Usage:
        # 메모리만 사용 (기본, 단일 서버)
        repo = LayeredCircuitBreakerStateRepository()

        # L2로 Redis 추가 (분산 환경)
        from selfhealing.adapters.redis import RedisCircuitBreakerStateRepository
        repo = LayeredCircuitBreakerStateRepository(
            l2_repo=RedisCircuitBreakerStateRepository(),
            sync_interval_seconds=5,
        )
    """

    # ThreadPoolExecutor for async L2 operations with timeout
    _executor: ThreadPoolExecutor | None = None
    _executor_lock = threading.Lock()

    @classmethod
    def _get_executor(cls) -> ThreadPoolExecutor:
        """Get or create shared ThreadPoolExecutor."""
        if cls._executor is None:
            with cls._executor_lock:
                if cls._executor is None:
                    cls._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="l2_sync")
        return cls._executor

    def __init__(
        self,
        l2_repo: CircuitBreakerStateRepository | None = None,
        sync_interval_seconds: float = 5.0,
        adapter_type: str = "unknown",
        drift_reconciler: DriftReconciler | None = None,
    ):
        """
        Args:
            l2_repo: L2 저장소 (Redis, Django DB 등). None이면 L1만 사용.
            sync_interval_seconds: L2 동기화 주기 (초)
            adapter_type: L2 어댑터 타입 (redis, django 등) - 타임아웃 결정에 사용
            drift_reconciler: 드리프트 복구 인스턴스. None이면 기본 인스턴스 사용.
        """
        # Lazy import to avoid circular dependency
        from selfhealing.adapters.memory.circuit_breaker import (
            InMemoryCircuitBreakerStateRepository,
        )

        self._l1 = InMemoryCircuitBreakerStateRepository()
        self._l2 = l2_repo
        self._sync_interval = sync_interval_seconds
        self._adapter_type = adapter_type
        self._last_sync_time: datetime | None = None
        self._lock = threading.RLock()
        self._shadow_logger = get_shadow_logger()
        self._drift_reconciler = drift_reconciler or get_drift_reconciler()

        # L2 연결 상태 추적
        self._l2_healthy = True
        self._l2_last_error_time: datetime | None = None
        self._l2_consecutive_failures = 0
        self._l2_was_unhealthy = False  # L2 복구 감지용

        # 메트릭 카운터 (Prometheus 연동 전 로컬 추적용)
        self._metrics = {
            "l2_timeout_count": 0,
            "l2_sync_failure_count": 0,
            "l2_sync_success_count": 0,
            "l2_latency_total_ms": 0.0,
            "l2_latency_count": 0,
            "drift_reconciliation_count": 0,
        }

        # L2가 있으면 초기 로드
        if self._l2:
            self._load_from_l2_with_timeout()

    # =========================================================================
    # Timeout & Config Methods
    # =========================================================================

    def _get_timeout_seconds(self) -> float:
        """어댑터 타입에 따른 타임아웃 반환 (초 단위)."""
        try:
            from selfhealing.config import get_l2_storage_runtime_config

            config = get_l2_storage_runtime_config()
            return config.get_timeout_for_adapter(self._adapter_type)
        except ImportError:
            # Config not available, use defaults
            timeouts = {
                "redis": 0.05,  # 50ms
                "database": 0.2,  # 200ms
                "django": 0.2,  # 200ms
            }
            return timeouts.get(self._adapter_type.lower(), 0.1)

    # =========================================================================
    # L2 Load Methods
    # =========================================================================

    def _load_from_l2_with_timeout(self) -> None:
        """L2에서 L1으로 초기 데이터 로드 (타임아웃 적용)."""
        if not self._l2:
            return

        timeout = self._get_timeout_seconds() * 2  # 초기 로드는 2배 타임아웃
        start_time = time.perf_counter()

        try:
            executor = self._get_executor()
            future = executor.submit(self._l2.get_all)
            all_states = future.result(timeout=timeout)

            for state in all_states:
                self._l1.get_or_create(state.service_name)
                self._l1.update_state(
                    service_name=state.service_name,
                    state=state.state,
                    failure_count=state.failure_count,
                    success_count=state.success_count,
                    opened_at=state.opened_at,
                )

            self._last_sync_time = _now()
            self._l2_healthy = True
            self._l2_consecutive_failures = 0

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._metrics["l2_latency_total_ms"] += elapsed_ms
            self._metrics["l2_latency_count"] += 1

            logger.info(f"[LayeredRepo] L2 initial load completed: " f"{len(all_states)} states loaded in {elapsed_ms:.1f}ms")

        except FuturesTimeoutError:
            self._handle_l2_timeout("initial_load", None)
            logger.warning(f"[LayeredRepo] L2 initial load timeout ({timeout*1000:.0f}ms). " f"Starting with empty L1.")
        except Exception as e:
            self._handle_l2_error("initial_load", None, e)
            logger.warning(f"[LayeredRepo] L2 initial load failed: {e}. " f"Starting with empty L1.")

    def _load_from_l2(self) -> None:
        """L2에서 L1으로 초기 데이터 로드 (레거시, 타임아웃 없음)."""
        self._load_from_l2_with_timeout()

    # =========================================================================
    # L2 Error Handling
    # =========================================================================

    def _handle_l2_timeout(self, operation: str, service_name: str | None) -> None:
        """L2 타임아웃 처리."""
        self._metrics["l2_timeout_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)

        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True

            # Audit 기록: L2 장애 발생
            self._log_l2_failure_audit(
                operation=operation,
                service_name=service_name,
                error_type="timeout",
                error_message=f"L2 timeout after {self._l2_consecutive_failures} consecutive failures",
            )

            # 알림 발송: 연속 실패 시
            self._send_l2_failure_notification(
                failure_type="timeout",
                consecutive_failures=self._l2_consecutive_failures,
            )

        try:
            from selfhealing.services.metrics.recorders import record_l2_timeout

            record_l2_timeout(self._adapter_type, operation)
        except ImportError:
            pass

    def _handle_l2_error(
        self,
        operation: str,
        service_name: str | None,
        error: Exception,
        intended_state: str = "",
    ) -> None:
        """L2 오류 처리 및 Shadow Log 기록."""
        self._metrics["l2_sync_failure_count"] += 1
        self._l2_consecutive_failures += 1
        self._l2_last_error_time = datetime.now(timezone.utc)

        if self._l2_consecutive_failures >= 3:
            self._l2_healthy = False
            self._l2_was_unhealthy = True

            # Audit 기록: L2 장애 발생
            self._log_l2_failure_audit(
                operation=operation,
                service_name=service_name,
                error_type=type(error).__name__,
                error_message=str(error)[:500],
            )

            # 알림 발송: 연속 실패 시
            self._send_l2_failure_notification(
                failure_type="error",
                consecutive_failures=self._l2_consecutive_failures,
                error_message=str(error)[:200],
            )

        if service_name and intended_state:
            self._shadow_logger.record_sync_failure(
                service_name=service_name,
                intended_state=intended_state,
                error=error,
                adapter_type=self._adapter_type,
                operation=operation,
            )

        try:
            from selfhealing.services.metrics.recorders import record_l2_sync_failure

            record_l2_sync_failure(self._adapter_type, operation)
        except ImportError:
            pass

    def _handle_l2_success(self, elapsed_ms: float) -> None:
        """L2 성공 처리 및 복구 감지."""
        was_unhealthy = not self._l2_healthy or self._l2_was_unhealthy

        self._metrics["l2_sync_success_count"] += 1
        self._metrics["l2_latency_total_ms"] += elapsed_ms
        self._metrics["l2_latency_count"] += 1
        self._l2_consecutive_failures = 0
        self._l2_healthy = True

        if was_unhealthy:
            self._l2_was_unhealthy = False
            logger.info(
                f"[LayeredRepo] L2 recovery detected after "
                f"{self._metrics.get('l2_sync_failure_count', 0)} failures. "
                f"Initiating drift reconciliation."
            )

            # Audit 기록: L2 복구
            self._log_l2_recovery_audit()

            # 알림 발송: L2 복구 완료
            self._send_l2_recovery_notification()

            self._schedule_drift_reconciliation()

        try:
            from selfhealing.services.metrics.recorders import record_l2_latency

            record_l2_latency(self._adapter_type, elapsed_ms / 1000.0)
        except ImportError:
            pass

    # =========================================================================
    # Drift Reconciliation
    # =========================================================================

    def _schedule_drift_reconciliation(self) -> None:
        """드리프트 복구를 백그라운드에서 스케줄."""

        def _run_reconciliation():
            try:
                jitter = self._drift_reconciler.get_jitter()
                if jitter > 0:
                    logger.debug(
                        f"[LayeredRepo] Drift reconciliation scheduled with "
                        f"{jitter:.2f}s jitter (Thundering Herd prevention)"
                    )
                    time.sleep(jitter)

                self._reconcile_all_drift()
            except Exception as e:
                logger.error(f"[LayeredRepo] Drift reconciliation error: {e}")

        try:
            executor = self._get_executor()
            executor.submit(_run_reconciliation)
        except Exception as e:
            logger.warning(f"[LayeredRepo] Failed to schedule drift reconciliation: {e}")

    def _reconcile_all_drift(self) -> dict[str, Any]:
        """모든 서비스의 L1/L2 드리프트 해결."""
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}

        reconciled_count = 0
        l1_wins_count = 0
        l2_wins_count = 0
        errors = []

        l1_states = self._l1.get_all()

        for l1_state in l1_states:
            try:
                timeout = self._get_timeout_seconds()
                executor = self._get_executor()
                future = executor.submit(self._l2.get_by_service_name, l1_state.service_name)

                try:
                    l2_state = future.result(timeout=timeout)
                except FuturesTimeoutError:
                    logger.warning(f"[LayeredRepo] Drift reconciliation timeout for " f"{l1_state.service_name}, skipping")
                    continue

                if l2_state is None:
                    self._sync_to_l2_with_timeout(l1_state.service_name, l1_state)
                    l1_wins_count += 1
                    reconciled_count += 1
                    continue

                winner_state, result = self._drift_reconciler.reconcile(
                    service_name=l1_state.service_name,
                    l1_state=l1_state.state,
                    l2_state=l2_state.state,
                    l1_updated_at=l1_state.updated_at,
                    l2_updated_at=l2_state.updated_at,
                )

                if result == DriftReconciliationResult.NO_DRIFT:
                    continue

                reconciled_count += 1

                if result in (
                    DriftReconciliationResult.L1_WINS,
                    DriftReconciliationResult.TIMESTAMP_L1,
                ):
                    self._sync_to_l2_with_timeout(l1_state.service_name, l1_state)
                    l1_wins_count += 1
                else:
                    self._l1.update_state(
                        service_name=l2_state.service_name,
                        state=l2_state.state,
                        failure_count=l2_state.failure_count,
                        success_count=l2_state.success_count,
                        opened_at=l2_state.opened_at,
                    )
                    l2_wins_count += 1

            except Exception as e:
                errors.append(
                    {
                        "service": l1_state.service_name,
                        "error": str(e),
                    }
                )
                logger.warning(f"[LayeredRepo] Drift reconciliation error for " f"{l1_state.service_name}: {e}")

        self._metrics["drift_reconciliation_count"] += reconciled_count

        if reconciled_count > 0:
            self._shadow_logger.mark_all_as_synced()

            # Audit 기록: 드리프트 복구 완료
            self._log_drift_reconciliation_audit(
                total_checked=len(l1_states),
                reconciled=reconciled_count,
                l1_wins=l1_wins_count,
                l2_wins=l2_wins_count,
                errors=errors,
            )

        result_dict = {
            "success": len(errors) == 0,
            "total_checked": len(l1_states),
            "reconciled": reconciled_count,
            "l1_wins": l1_wins_count,
            "l2_wins": l2_wins_count,
            "errors": errors,
        }

        logger.info(
            f"[LayeredRepo] Drift reconciliation completed: "
            f"{reconciled_count} reconciled, L1 wins={l1_wins_count}, L2 wins={l2_wins_count}"
        )

        return result_dict

    # =========================================================================
    # L2 Sync Methods
    # =========================================================================

    def _sync_to_l2_with_timeout(
        self,
        service_name: str,
        state: CircuitBreakerStateData,
    ) -> bool:
        """L2로 동기화 (타임아웃 적용)."""
        if not self._l2:
            return False

        timeout = self._get_timeout_seconds()
        start_time = time.perf_counter()

        def _do_sync():
            self._l2.get_or_create(service_name)
            self._l2.update_state(
                service_name=service_name,
                state=state.state,
                failure_count=state.failure_count,
                success_count=state.success_count,
                opened_at=state.opened_at,
            )

        try:
            executor = self._get_executor()
            future = executor.submit(_do_sync)
            future.result(timeout=timeout)

            elapsed_ms = (time.perf_counter() - start_time) * 1000
            self._handle_l2_success(elapsed_ms)
            return True

        except FuturesTimeoutError:
            self._handle_l2_timeout("sync", service_name)
            logger.warning(f"[LayeredRepo] L2 sync timeout for {service_name} " f"({timeout*1000:.0f}ms). L1 isolated.")
            return False

        except Exception as e:
            self._handle_l2_error("sync", service_name, e, state.state)
            return False

    def _sync_to_l2_async(self, service_name: str, state: CircuitBreakerStateData) -> None:
        """L2로 비동기 동기화 (백그라운드, 타임아웃 적용)."""
        if not self._l2:
            return

        def _sync():
            self._sync_to_l2_with_timeout(service_name, state)

        try:
            executor = self._get_executor()
            executor.submit(_sync)
        except Exception as e:
            logger.warning(f"[LayeredRepo] Failed to submit L2 sync task: {e}")

    def _sync_state_after_l1_change(self, service_name: str) -> None:
        """L1 상태 변경 후 L2로 동기화하는 공통 헬퍼."""
        updated = self._l1.get_by_service_name(service_name)
        if updated:
            self._sync_to_l2_async(service_name, updated)

    # =========================================================================
    # CircuitBreakerStateRepository Interface Implementation (L1 Priority)
    # =========================================================================

    def get_by_service_name(self, service_name: str) -> CircuitBreakerStateData | None:
        """L1에서 조회. L1에 없으면 L2 확인 후 L1에 캐시."""
        result = self._l1.get_by_service_name(service_name)

        if result is None and self._l2 and self._l2_healthy:
            timeout = self._get_timeout_seconds()
            start_time = time.perf_counter()

            try:
                executor = self._get_executor()
                future = executor.submit(self._l2.get_by_service_name, service_name)
                l2_result = future.result(timeout=timeout)

                if l2_result:
                    self._l1.get_or_create(service_name)
                    self._l1.update_state(
                        service_name=service_name,
                        state=l2_result.state,
                        failure_count=l2_result.failure_count,
                        success_count=l2_result.success_count,
                        opened_at=l2_result.opened_at,
                    )
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    self._handle_l2_success(elapsed_ms)
                    return self._l1.get_by_service_name(service_name)

            except FuturesTimeoutError:
                self._handle_l2_timeout("get", service_name)
            except Exception as e:
                self._handle_l2_error("get", service_name, e)

        return result

    def get_or_create(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 조회/생성. L2도 동기화."""
        result = self._l1.get_or_create(service_name)
        self._sync_to_l2_async(service_name, result)
        return result

    def update_state(
        self,
        service_name: str,
        state: str,
        failure_count: int | None = None,
        success_count: int | None = None,
        opened_at: datetime | None = None,
    ) -> bool:
        """L1 업데이트 후 L2 비동기 동기화."""
        result = self._l1.update_state(
            service_name=service_name,
            state=state,
            failure_count=failure_count,
            success_count=success_count,
            opened_at=opened_at,
        )
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def increment_failure_count(
        self,
        service_name: str,
        last_failure_at: datetime | None = None,
    ) -> int:
        """L1에서 카운트 증가 후 L2 동기화."""
        result = self._l1.increment_failure_count(service_name, last_failure_at)
        self._sync_state_after_l1_change(service_name)
        return result

    def reset_failure_count(self, service_name: str) -> bool:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.reset_failure_count(service_name)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def set_half_open(self, service_name: str) -> bool:
        """L1에서 half-open 설정 후 L2 동기화."""
        result = self._l1.set_half_open(service_name)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def set_open(
        self,
        service_name: str,
        opened_at: datetime | None = None,
    ) -> bool:
        """L1에서 open 설정 후 L2 동기화."""
        result = self._l1.set_open(service_name, opened_at)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def set_closed(self, service_name: str, reason: str | None = None) -> tuple:
        """L1에서 closed 설정 후 L2 동기화."""
        result = self._l1.set_closed(service_name, reason)
        if result[0]:
            self._sync_state_after_l1_change(service_name)
        return result

    def get_all_open(self) -> list[CircuitBreakerStateData]:
        """L1에서 open 상태 조회."""
        return self._l1.get_all_open()

    def get_all(self) -> list[CircuitBreakerStateData]:
        """L1에서 전체 조회."""
        return self._l1.get_all()

    def delete(self, service_name: str) -> bool:
        """L1에서 삭제. L2도 동기화."""
        result = self._l1.delete(service_name)

        if result and self._l2:
            try:
                self._l2.delete(service_name)
            except Exception:
                pass

        return result

    def clear(self) -> None:
        """L1 클리어. L2는 건드리지 않음 (테스트용)."""
        self._l1.clear()

    # =========================================================================
    # Additional Abstract Method Implementation (L1 Delegation)
    # =========================================================================

    def record_failure(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 실패 기록 후 L2 동기화."""
        result = self._l1.record_failure(service_name)
        self._sync_to_l2_async(service_name, result)
        return result

    def record_success(self, service_name: str) -> CircuitBreakerStateData:
        """L1에서 성공 기록 후 L2 동기화."""
        result = self._l1.record_success(service_name)
        self._sync_to_l2_async(service_name, result)
        return result

    def get_all_states(self) -> list[CircuitBreakerStateData]:
        """L1에서 전체 상태 조회."""
        return self._l1.get_all_states()

    def reset(self, service_name: str) -> bool:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.reset(service_name)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def atomic_force_open(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
        ttl_minutes: int = 90,
    ) -> tuple:
        """L1에서 강제 open 후 L2 동기화."""
        result = self._l1.atomic_force_open(service_name, reason, controlled_by_id, ttl_minutes)
        if result[0]:
            self._sync_state_after_l1_change(service_name)
        return result

    def atomic_force_close(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
    ) -> tuple:
        """L1에서 강제 close 후 L2 동기화."""
        result = self._l1.atomic_force_close(service_name, reason, controlled_by_id)
        if result[0]:
            self._sync_state_after_l1_change(service_name)
        return result

    def atomic_reset(
        self,
        service_name: str,
        reason: str = "",
        controlled_by_id: int | None = None,
    ) -> tuple:
        """L1에서 리셋 후 L2 동기화."""
        result = self._l1.atomic_reset(service_name, reason, controlled_by_id)
        if result[0]:
            self._sync_state_after_l1_change(service_name)
        return result

    def set_manual_control(
        self,
        service_name: str,
        controlled_by_id: int | None = None,
        reason: str = "",
        ttl_minutes: int = 90,
    ) -> bool:
        """L1에서 수동 제어 설정 후 L2 동기화."""
        result = self._l1.set_manual_control(service_name, controlled_by_id, reason, ttl_minutes)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    def clear_manual_control(self, service_name: str, reason: str = "") -> bool:
        """L1에서 수동 제어 해제 후 L2 동기화."""
        result = self._l1.clear_manual_control(service_name, reason)
        if result:
            self._sync_state_after_l1_change(service_name)
        return result

    # =========================================================================
    # Management & Monitoring Methods
    # =========================================================================

    def get_storage_info(self) -> dict[str, Any]:
        """저장소 정보 조회 (L2 상태 및 메트릭 포함)."""
        avg_latency_ms = 0.0
        if self._metrics["l2_latency_count"] > 0:
            avg_latency_ms = self._metrics["l2_latency_total_ms"] / self._metrics["l2_latency_count"]

        return {
            "l1_type": "memory",
            "l1_count": len(self._l1.get_all()),
            "l2_enabled": self._l2 is not None,
            "l2_type": type(self._l2).__name__ if self._l2 else None,
            "l2_adapter_type": self._adapter_type,
            "l2_healthy": self._l2_healthy,
            "l2_was_unhealthy": self._l2_was_unhealthy,
            "l2_consecutive_failures": self._l2_consecutive_failures,
            "l2_last_error_time": (self._l2_last_error_time.isoformat() if self._l2_last_error_time else None),
            "sync_interval_seconds": self._sync_interval,
            "last_sync_time": (self._last_sync_time.isoformat() if self._last_sync_time else None),
            "timeout_ms": self._get_timeout_seconds() * 1000,
            "metrics": {
                "timeout_count": self._metrics["l2_timeout_count"],
                "sync_failure_count": self._metrics["l2_sync_failure_count"],
                "sync_success_count": self._metrics["l2_sync_success_count"],
                "drift_reconciliation_count": self._metrics["drift_reconciliation_count"],
                "avg_latency_ms": round(avg_latency_ms, 2),
            },
            "shadow_log": self._shadow_logger.get_stats(),
            "drift_reconciler": self._drift_reconciler.get_stats(),
        }

    def get_l2_health(self) -> dict[str, Any]:
        """L2 헬스 상태 조회."""
        return {
            "healthy": self._l2_healthy,
            "was_unhealthy": self._l2_was_unhealthy,
            "consecutive_failures": self._l2_consecutive_failures,
            "last_error_time": (self._l2_last_error_time.isoformat() if self._l2_last_error_time else None),
            "adapter_type": self._adapter_type,
            "timeout_ms": self._get_timeout_seconds() * 1000,
        }

    def reset_l2_health(self) -> None:
        """L2 헬스 상태 리셋 (수동 복구 시)."""
        self._l2_healthy = True
        self._l2_was_unhealthy = False
        self._l2_consecutive_failures = 0
        self._l2_last_error_time = None
        logger.info("[LayeredRepo] L2 health status reset manually")

    def get_metrics(self) -> dict[str, Any]:
        """내부 메트릭 조회."""
        return dict(self._metrics)

    def reset_metrics(self) -> None:
        """메트릭 리셋 (테스트용)."""
        self._metrics = {
            "l2_timeout_count": 0,
            "l2_sync_failure_count": 0,
            "l2_sync_success_count": 0,
            "l2_latency_total_ms": 0.0,
            "l2_latency_count": 0,
            "drift_reconciliation_count": 0,
        }

    def force_sync_from_l2(self) -> bool:
        """L2에서 강제 동기화 (관리 목적)."""
        if not self._l2:
            return False

        try:
            self._load_from_l2_with_timeout()
            return True
        except Exception as e:
            logger.error(f"[LayeredRepo] Force sync from L2 failed: {e}")
            return False

    def force_sync_to_l2(self) -> dict[str, Any]:
        """L1의 모든 상태를 L2로 강제 동기화."""
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}

        all_states = self._l1.get_all()
        success_count = 0
        failure_count = 0

        for state in all_states:
            if self._sync_to_l2_with_timeout(state.service_name, state):
                success_count += 1
            else:
                failure_count += 1

        if success_count > 0:
            self._shadow_logger.mark_all_as_synced()

        return {
            "success": failure_count == 0,
            "total": len(all_states),
            "synced": success_count,
            "failed": failure_count,
        }

    def force_drift_reconciliation(self) -> dict[str, Any]:
        """수동으로 드리프트 복구 트리거."""
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}

        logger.info("[LayeredRepo] Manual drift reconciliation triggered")
        return self._reconcile_all_drift()

    def get_drift_reconciler_stats(self) -> dict[str, Any]:
        """드리프트 복구 통계 조회."""
        return self._drift_reconciler.get_stats()

    def get_drift_reconciliation_history(self) -> list[dict[str, Any]]:
        """드리프트 복구 기록 조회."""
        history = self._drift_reconciler.get_history()
        return [
            {
                "service_name": r.service_name,
                "l1_state": r.l1_state,
                "l2_state": r.l2_state,
                "l1_updated_at": (r.l1_updated_at.isoformat() if r.l1_updated_at else None),
                "l2_updated_at": (r.l2_updated_at.isoformat() if r.l2_updated_at else None),
                "winner": r.winner,
                "result": r.result.value,
                "reconciled_at": r.reconciled_at.isoformat(),
                "jitter_seconds": r.jitter_seconds,
            }
            for r in history
        ]

    def reconcile_single_service(self, service_name: str) -> dict[str, Any]:
        """특정 서비스의 드리프트만 복구."""
        if not self._l2:
            return {"success": False, "reason": "L2 not configured"}

        l1_state = self._l1.get_by_service_name(service_name)
        if l1_state is None:
            return {"success": False, "reason": "Service not found in L1"}

        try:
            timeout = self._get_timeout_seconds()
            executor = self._get_executor()
            future = executor.submit(self._l2.get_by_service_name, service_name)
            l2_state = future.result(timeout=timeout)
        except FuturesTimeoutError:
            return {"success": False, "reason": "L2 timeout"}
        except Exception as e:
            return {"success": False, "reason": str(e)}

        if l2_state is None:
            self._sync_to_l2_with_timeout(service_name, l1_state)
            return {
                "success": True,
                "action": "l1_to_l2",
                "reason": "L2 had no state, synced from L1",
            }

        winner_state, result = self._drift_reconciler.reconcile(
            service_name=service_name,
            l1_state=l1_state.state,
            l2_state=l2_state.state,
            l1_updated_at=l1_state.updated_at,
            l2_updated_at=l2_state.updated_at,
        )

        if result == DriftReconciliationResult.NO_DRIFT:
            return {
                "success": True,
                "action": "none",
                "reason": "No drift detected",
            }

        self._metrics["drift_reconciliation_count"] += 1

        if result in (
            DriftReconciliationResult.L1_WINS,
            DriftReconciliationResult.TIMESTAMP_L1,
        ):
            self._sync_to_l2_with_timeout(service_name, l1_state)
            return {
                "success": True,
                "action": "l1_to_l2",
                "winner": "l1",
                "result": result.value,
                "winner_state": winner_state,
            }
        else:
            self._l1.update_state(
                service_name=l2_state.service_name,
                state=l2_state.state,
                failure_count=l2_state.failure_count,
                success_count=l2_state.success_count,
                opened_at=l2_state.opened_at,
            )
            return {
                "success": True,
                "action": "l2_to_l1",
                "winner": "l2",
                "result": result.value,
                "winner_state": winner_state,
            }

    # =========================================================================
    # Audit & Notification Helpers
    # =========================================================================

    def _log_l2_failure_audit(
        self,
        operation: str,
        service_name: str | None,
        error_type: str,
        error_message: str,
    ) -> None:
        """L2 장애 발생 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import log_storage_failure_audit

            log_storage_failure_audit(
                storage_type="l2",
                adapter_type=self._adapter_type,
                operation=operation,
                service_name=service_name,
                error_type=error_type,
                error_message=error_message,
                consecutive_failures=self._l2_consecutive_failures,
            )
        except Exception as e:
            # Fail-Open: Audit 실패가 시스템을 중단시키지 않음
            logger.debug(f"[LayeredRepo] Audit logging failed (ignored): {e}")

    def _log_l2_recovery_audit(self) -> None:
        """L2 복구 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import log_storage_recovery_audit

            log_storage_recovery_audit(
                storage_type="l2",
                adapter_type=self._adapter_type,
                total_failures=self._metrics.get("l2_sync_failure_count", 0),
            )
        except Exception as e:
            logger.debug(f"[LayeredRepo] Audit logging failed (ignored): {e}")

    def _log_drift_reconciliation_audit(
        self,
        total_checked: int,
        reconciled: int,
        l1_wins: int,
        l2_wins: int,
        errors: list[dict[str, Any]],
    ) -> None:
        """드리프트 복구 완료 시 Audit 로그 기록. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.audit_helpers import (
                log_drift_reconciliation_audit,
            )

            log_drift_reconciliation_audit(
                adapter_type=self._adapter_type,
                total_checked=total_checked,
                reconciled=reconciled,
                l1_wins=l1_wins,
                l2_wins=l2_wins,
                error_count=len(errors),
            )
        except Exception as e:
            logger.debug(f"[LayeredRepo] Audit logging failed (ignored): {e}")

    def _send_l2_failure_notification(
        self,
        failure_type: str,
        consecutive_failures: int,
        error_message: str = "",
    ) -> None:
        """L2 연속 장애 시 알림 발송. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.unified_notification import (
                get_notification_service,
            )

            notification_service = get_notification_service()
            notification_service.send(
                level="warning",
                category="STORAGE_FAILURE",
                title=f"L2 Storage Failure ({self._adapter_type})",
                message=(
                    f"L2 storage has failed {consecutive_failures} consecutive times. "
                    f"Type: {failure_type}. System operating in L1-only mode. "
                    f"{error_message}"
                ),
                metadata={
                    "adapter_type": self._adapter_type,
                    "failure_type": failure_type,
                    "consecutive_failures": consecutive_failures,
                },
            )
        except Exception as e:
            logger.debug(f"[LayeredRepo] Notification failed (ignored): {e}")

    def _send_l2_recovery_notification(self) -> None:
        """L2 복구 완료 시 알림 발송. Fail-Open 원칙 적용."""
        try:
            from selfhealing.services.unified_notification import (
                get_notification_service,
            )

            notification_service = get_notification_service()
            notification_service.send(
                level="info",
                category="STORAGE_RECOVERY",
                title=f"L2 Storage Recovered ({self._adapter_type})",
                message=(
                    f"L2 storage has recovered after "
                    f"{self._metrics.get('l2_sync_failure_count', 0)} failures. "
                    f"Drift reconciliation initiated."
                ),
                metadata={
                    "adapter_type": self._adapter_type,
                    "total_failures": self._metrics.get("l2_sync_failure_count", 0),
                },
            )
        except Exception as e:
            logger.debug(f"[LayeredRepo] Notification failed (ignored): {e}")

"""
Drift Reconciliation Operations Mixin.

Provides methods for drift detection and reconciliation.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from selfhealing.adapters.memory.drift_reconciliation import DriftReconciliationResult

logger = logging.getLogger(__name__)


class DriftOperationsMixin:
    """Mixin providing drift reconciliation operations."""

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
            logger.warning(
                f"[LayeredRepo] Failed to schedule drift reconciliation: {e}"
            )

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
                future = executor.submit(
                    self._l2.get_by_service_name, l1_state.service_name
                )

                try:
                    l2_state = future.result(timeout=timeout)
                except FuturesTimeoutError:
                    logger.warning(
                        f"[LayeredRepo] Drift reconciliation timeout for "
                        f"{l1_state.service_name}, skipping"
                    )
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
                logger.warning(
                    f"[LayeredRepo] Drift reconciliation error for "
                    f"{l1_state.service_name}: {e}"
                )

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
                "l1_updated_at": (
                    r.l1_updated_at.isoformat() if r.l1_updated_at else None
                ),
                "l2_updated_at": (
                    r.l2_updated_at.isoformat() if r.l2_updated_at else None
                ),
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

"""
Audit System Failure Experiments.

Includes:
- AuditStorageFailureExperiment: Simulate audit storage failures
- ReplayFloodExperiment: Simulate replay attack/flood scenarios
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)


logger = logging.getLogger(__name__)


class AuditStorageFailureExperiment(ChaosExperiment):
    """
    Simulate audit storage failures.
    
    감사 로그 저장소 장애를 시뮬레이션하여 resilience 메커니즘을 테스트합니다.
    - Syslog fallback 동작 확인
    - In-memory buffer 작동 확인
    - Circuit breaker 트리거 확인
    
    Config parameters:
        - failure_type: Type of failure (write_error, connection_error, timeout)
        - failure_rate: Percentage of operations to fail (default: 100%)
        - trigger_fallback: Whether to trigger syslog fallback (default: True)
    """
    
    experiment_type = ExperimentType.AUDIT_STORAGE_FAILURE.value
    requires_approval = True  # Can affect audit integrity
    
    @property
    def failure_type(self) -> str:
        return self.config.parameters.get("failure_type", "write_error")
    
    @property
    def failure_rate(self) -> float:
        return self.config.parameters.get("failure_rate", 1.0)
    
    @property
    def trigger_fallback(self) -> bool:
        return self.config.parameters.get("trigger_fallback", True)
    
    def inject_chaos(self) -> bool:
        """Inject audit storage failure simulation."""
        logger.warning(
            f"[AuditStorageFailure] Injecting {self.failure_type} failures at "
            f"{self.failure_rate*100}% rate"
        )
        
        try:
            _apply_chaos_config({
                "audit_storage_failure": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "failure_type": self.failure_type,
                    "failure_rate": self.failure_rate,
                    "trigger_fallback": self.trigger_fallback,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[AuditStorageFailure] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove audit storage failure injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[AuditStorageFailure] Rollback already completed")
                return
            
            logger.info(f"[AuditStorageFailure] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "audit_storage_failure": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[AuditStorageFailure] Rollback failed: {e}")
    
    # =========================================================================
    # Resilience 검증 헬퍼 메서드
    # =========================================================================
    
    def verify_fallback_activated(self) -> Dict[str, Any]:
        """
        Verify that syslog fallback was activated.
        
        Returns:
            Dict with fallback status information
        """
        try:
            from selfhealing.audit.resilience import (
                get_resilience_manager,
            )
            
            manager = get_resilience_manager()
            status = manager.get_status()
            
            return {
                "degraded_mode": status.degraded_mode,
                "syslog_fallback_active": status.syslog_fallback_active,
                "buffer_size": status.buffer_size,
                "circuit_breaker_state": status.circuit_breaker_state,
            }
        except ImportError:
            logger.debug("[AuditStorageFailure] Resilience manager not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[AuditStorageFailure] Fallback verification failed: {e}")
            return {"available": False, "error": str(e)}
    
    def verify_buffer_behavior(self) -> Dict[str, Any]:
        """
        Verify in-memory buffer is working correctly.
        
        Returns:
            Dict with buffer status information
        """
        try:
            from selfhealing.audit.resilience import (
                get_resilience_manager,
            )
            
            manager = get_resilience_manager()
            buffer_status = manager.get_buffer_status()
            
            return {
                "buffer_enabled": buffer_status.enabled,
                "current_size": buffer_status.current_size,
                "max_size": buffer_status.max_size,
                "overflow_policy": buffer_status.overflow_policy,
                "oldest_entry_age_seconds": buffer_status.oldest_entry_age_seconds,
            }
        except ImportError:
            logger.debug("[AuditStorageFailure] Resilience manager not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[AuditStorageFailure] Buffer verification failed: {e}")
            return {"available": False, "error": str(e)}


class ReplayFloodExperiment(ChaosExperiment):
    """
    Simulate replay attack/flood scenarios.
    
    대량의 replay 요청을 시뮬레이션하여 시스템의 과부하 대응 능력을 테스트합니다.
    - DLQ 처리 능력 확인
    - Rate limiting 동작 확인
    - 백프레셔(backpressure) 메커니즘 확인
    
    Config parameters:
        - flood_rate: Number of replay requests per second (default: 100)
        - duration_seconds: Duration of flood (default: 30)
        - payload_size_bytes: Size of each replay payload (default: 1024)
        - target_queue: Target DLQ name (optional)
    """
    
    experiment_type = ExperimentType.REPLAY_FLOOD.value
    requires_approval = True  # Can affect system performance
    
    @property
    def flood_rate(self) -> int:
        return self.config.parameters.get("flood_rate", 100)
    
    @property
    def duration_seconds(self) -> int:
        return self.config.parameters.get("duration_seconds", 30)
    
    @property
    def payload_size_bytes(self) -> int:
        return self.config.parameters.get("payload_size_bytes", 1024)
    
    @property
    def target_queue(self) -> str:
        return self.config.parameters.get("target_queue", "")
    
    def inject_chaos(self) -> bool:
        """Inject replay flood simulation."""
        logger.warning(
            f"[ReplayFlood] Starting flood: {self.flood_rate}/s for "
            f"{self.duration_seconds}s (payload: {self.payload_size_bytes}B)"
        )
        
        try:
            _apply_chaos_config({
                "replay_flood": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "flood_rate": self.flood_rate,
                    "duration_seconds": self.duration_seconds,
                    "payload_size_bytes": self.payload_size_bytes,
                    "target_queue": self.target_queue,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ReplayFlood] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Stop replay flood simulation."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[ReplayFlood] Rollback already completed")
                return
            
            logger.info(f"[ReplayFlood] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "replay_flood": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ReplayFlood] Rollback failed: {e}")
    
    # =========================================================================
    # Flood 시뮬레이션 헬퍼
    # =========================================================================
    
    def _generate_flood_payloads(self, count: int) -> List[bytes]:
        """
        Generate flood payloads for simulation.
        
        Args:
            count: Number of payloads to generate
            
        Returns:
            List of payload bytes
        """
        import os
        
        payloads = []
        for _ in range(count):
            payloads.append(os.urandom(self.payload_size_bytes))
        return payloads
    
    def _simulate_flood_batch(self, payloads: List[bytes]) -> Dict[str, Any]:
        """
        Simulate a batch of flood requests.
        
        Args:
            payloads: List of payloads to send
            
        Returns:
            Dict with batch results
        """
        results = {
            "total": len(payloads),
            "accepted": 0,
            "rejected": 0,
            "rate_limited": 0,
        }
        
        try:
            from selfhealing.services.replay.replay_queue_service import (
                get_replay_queue_service,
            )
            
            queue_service = get_replay_queue_service()
            
            for payload in payloads:
                try:
                    # Attempt to queue replay
                    success = queue_service.enqueue_raw(
                        payload,
                        source=f"chaos_experiment:{self.experiment_id}",
                    )
                    if success:
                        results["accepted"] += 1
                    else:
                        results["rejected"] += 1
                except Exception as e:
                    if "rate_limit" in str(e).lower():
                        results["rate_limited"] += 1
                    else:
                        results["rejected"] += 1
        except ImportError:
            logger.debug("[ReplayFlood] Replay queue service not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[ReplayFlood] Flood batch failed: {e}")
            results["error"] = str(e)
        
        return results
    
    def verify_rate_limiting(self) -> Dict[str, Any]:
        """
        Verify that rate limiting is active.
        
        Returns:
            Dict with rate limiting status
        """
        try:
            from selfhealing.services.replay.replay_queue_service import (
                get_replay_queue_service,
            )
            
            queue_service = get_replay_queue_service()
            status = queue_service.get_rate_limit_status()
            
            return {
                "rate_limiting_enabled": status.enabled,
                "current_rate": status.current_rate,
                "limit": status.limit,
                "rejected_count": status.rejected_count,
                "window_seconds": status.window_seconds,
            }
        except ImportError:
            logger.debug("[ReplayFlood] Replay queue service not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[ReplayFlood] Rate limit verification failed: {e}")
            return {"available": False, "error": str(e)}
    
    def verify_backpressure(self) -> Dict[str, Any]:
        """
        Verify that backpressure mechanisms are working.
        
        Returns:
            Dict with backpressure status
        """
        try:
            from selfhealing.services.replay.replay_queue_service import (
                get_replay_queue_service,
            )
            
            queue_service = get_replay_queue_service()
            status = queue_service.get_backpressure_status()
            
            return {
                "backpressure_active": status.active,
                "queue_depth": status.queue_depth,
                "max_depth": status.max_depth,
                "consumer_lag": status.consumer_lag,
                "throttle_percent": status.throttle_percent,
            }
        except ImportError:
            logger.debug("[ReplayFlood] Replay queue service not available")
            return {"available": False, "reason": "module_not_available"}
        except Exception as e:
            logger.warning(f"[ReplayFlood] Backpressure verification failed: {e}")
            return {"available": False, "error": str(e)}


__all__ = ["AuditStorageFailureExperiment", "ReplayFloodExperiment"]

"""
Network-related Chaos Experiments.

Includes:
- PacketLossExperiment: Simulates network packet loss
- ConnectionResetExperiment: Simulates TCP RST (connection drops)
- NetworkBlackholeExperiment: Simulates complete traffic absorption (no response)
- ConnectionPartitionExperiment: Simulates network partitions
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from selfhealing.services.chaos.base import (
    ChaosExperiment,
    ExperimentType,
    _apply_chaos_config,
)
from selfhealing.services.chaos.experiments.hypothesis import (
    CONNECTION_PARTITION_HYPOTHESIS,
    NETWORK_BLACKHOLE_HYPOTHESIS,
)


logger = logging.getLogger(__name__)


class PacketLossExperiment(ChaosExperiment):
    """
    Simulate network packet loss.
    
    Simulates unreliable network conditions, dropped connections.
    
    Config parameters:
        - loss_rate: Percentage of packets to drop (default: 5%)
    """
    
    experiment_type = ExperimentType.PACKET_LOSS.value
    requires_approval = True  # Higher risk
    
    @property
    def loss_rate(self) -> float:
        return self.config.parameters.get("loss_rate", 0.05)  # 5%
    
    def inject_chaos(self) -> bool:
        """Inject packet loss with TTL."""
        logger.info(
            f"[PacketLoss] Injecting {self.loss_rate*100}% packet loss "
            f"to {self.config.target_service} (TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "packet_loss": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "loss_rate": self.loss_rate,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[PacketLoss] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove packet loss injection with idempotency."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[PacketLoss] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[PacketLoss] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "packet_loss": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[PacketLoss] Rollback failed: {e}")


class ConnectionResetExperiment(ChaosExperiment):
    """
    Simulate network connection reset (TCP RST).
    
    Simulates sudden connection drops, network instability.
    Tests retry/backoff logic and circuit breaker reaction.
    
    Config parameters:
        - reset_after_bytes: Bytes to send before reset (0=immediate)
        - reset_probability: Probability of reset per request (0-1)
    """
    
    experiment_type = ExperimentType.CONNECTION_RESET.value
    requires_approval = True  # Medium-High risk
    
    @property
    def reset_after_bytes(self) -> int:
        return self.config.parameters.get("reset_after_bytes", 0)
    
    @property
    def reset_probability(self) -> float:
        return self.config.parameters.get("reset_probability", 0.5)
    
    def inject_chaos(self) -> bool:
        """Inject connection reset behavior with TTL."""
        logger.info(
            f"[ConnectionReset] Injecting connection resets "
            f"to {self.config.target_service} at {self.reset_probability*100}% probability "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "connection_reset": {
                    "enabled": True,
                    "target_service": self.config.target_service,
                    "reset_after_bytes": self.reset_after_bytes,
                    "reset_probability": self.reset_probability,
                    "traffic_type": self.config.traffic_type,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[ConnectionReset] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """Remove connection reset injection."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[ConnectionReset] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[ConnectionReset] Rolling back {self.experiment_id}")
            
            try:
                _apply_chaos_config({
                    "connection_reset": {
                        "enabled": False,
                        "target_service": self.config.target_service,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
            except Exception as e:
                logger.error(f"[ConnectionReset] Rollback failed: {e}")


class NetworkBlackholeExperiment(ChaosExperiment):
    """
    네트워크 블랙홀 시뮬레이션 실험.
    
    특정 엔드포인트로의 트래픽 완전 차단 (응답 없음).
    타임아웃과 다르게 에러 응답도 없음.
    
    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • 트래픽 흡수 (응답 없음) 시 타임아웃 감지                   │
    │ • 60초 내에 Circuit Breaker OPEN                             │
    │                                                              │
    │ Blast Radius Hard Cap:                                       │
    │ → 최대 지속 시간: 300초 (5분)                                 │
    └─────────────────────────────────────────────────────────────┘
    
    Config parameters:
        - affected_endpoints: 블랙홀 처리할 엔드포인트 목록
        - duration_seconds: 지속 시간 (max: 300초)
    """
    
    experiment_type = ExperimentType.NETWORK_BLACKHOLE.value
    requires_approval = True  # 고위험
    
    # Blast Radius Hard Cap
    MAX_DURATION_SECONDS: int = 300
    
    failure_hypothesis = NETWORK_BLACKHOLE_HYPOTHESIS
    
    @property
    def affected_endpoints(self) -> List[str]:
        return self.config.parameters.get("affected_endpoints", [])
    
    @property
    def duration_seconds(self) -> int:
        raw = self.config.parameters.get("duration_seconds", 60)
        return min(raw, self.MAX_DURATION_SECONDS)
    
    def inject_chaos(self) -> bool:
        """네트워크 블랙홀 시뮬레이션 주입."""
        logger.warning(
            f"[NetworkBlackhole] Blackholing {len(self.affected_endpoints)} endpoints "
            f"for {self.duration_seconds}s (TTL: {self._effective_ttl}s)"
        )
        
        try:
            _apply_chaos_config({
                "network_blackhole": {
                    "enabled": True,
                    "affected_endpoints": self.affected_endpoints,
                    "duration_seconds": self.duration_seconds,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            return True
        except Exception as e:
            logger.error(f"[NetworkBlackhole] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """네트워크 블랙홀 시뮬레이션 해제."""
        with self._rollback_lock:
            if self._rollback_completed:
                return
            
            logger.info(f"[NetworkBlackhole] Rolling back {self.experiment_id}")
            _apply_chaos_config({
                "network_blackhole": {"enabled": False, "experiment_id": self.experiment_id}
            })
            self._rollback_completed = True


class ConnectionPartitionExperiment(ChaosExperiment):
    """
    네트워크 파티션 시뮬레이션 실험.
    
    실제 네트워크를 분리하지 않고 ConnectionHealthMonitor가
    partial/full partition 상태를 보고하도록 시뮬레이션.
    
    ┌─────────────────────────────────────────────────────────────┐
    │ FAILURE HYPOTHESIS (복구 기대 가설)                          │
    ├─────────────────────────────────────────────────────────────┤
    │ • 네트워크 파티션 시뮬레이션 시, partial partition 처리      │
    │ • 60초 내에 복구 완료                                        │
    │ • Fallback 활성화 필수                                       │
    │                                                              │
    │ LearningService 연동:                                        │
    │ → 실제 결과와 비교하여 "복구 성능 저하 추세" 자동 감지        │
    └─────────────────────────────────────────────────────────────┘
    
    Config parameters:
        - partition_type: "partial" or "full" (default: "partial")
        - db_available: DB 가용성 (default: False)
        - cache_available: Cache 가용성 (default: True)
    
    Usage:
        experiment = ConnectionPartitionExperiment(
            config=ExperimentConfig(
                target_service="payment",
                parameters={
                    "partition_type": "partial",
                    "db_available": False,
                    "cache_available": True,
                },
            )
        )
        result = experiment.execute()
    """
    
    experiment_type = ExperimentType.CONNECTION_PARTITION.value
    requires_approval = True  # 높은 위험 - 수동 승인 필요
    
    # 복구 기대 가설 (클래스 레벨)
    failure_hypothesis = CONNECTION_PARTITION_HYPOTHESIS
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._monitor_instance = None
    
    @property
    def partition_type(self) -> str:
        return self.config.parameters.get("partition_type", "partial")
    
    @property
    def db_available(self) -> bool:
        return self.config.parameters.get("db_available", False)
    
    @property
    def cache_available(self) -> bool:
        return self.config.parameters.get("cache_available", True)
    
    def inject_chaos(self) -> bool:
        """시뮬레이션 모드로 네트워크 파티션 상태 주입."""
        from selfhealing.core.connection_health import (
            DefaultConnectionHealthMonitor,
            PartitionState,
        )
        
        logger.info(
            f"[ConnectionPartition] Injecting simulated {self.partition_type} partition "
            f"(db={self.db_available}, cache={self.cache_available}) "
            f"(TTL: {self._effective_ttl}s)"
        )
        
        try:
            # 파티션 상태 생성
            partition_state = PartitionState(
                db_available=self.db_available,
                cache_available=self.cache_available,
                external_apis={},
            )
            
            # Monitor 인스턴스 생성 및 파티션 시뮬레이션 설정
            self._monitor_instance = DefaultConnectionHealthMonitor()
            self._monitor_instance.set_partition_simulation(
                partition_state=partition_state,
                experiment_id=self.experiment_id,
            )
            
            _apply_chaos_config({
                "connection_partition": {
                    "enabled": True,
                    "partition_type": self.partition_type,
                    "db_available": self.db_available,
                    "cache_available": self.cache_available,
                    "experiment_id": self.experiment_id,
                    "expires_at": self._expires_at.isoformat() if self._expires_at else "",
                    "ttl_seconds": self._effective_ttl,
                }
            })
            
            logger.info(
                f"[ConnectionPartition] Partition simulation set: "
                f"partial={partition_state.is_partial_partition}, "
                f"full={partition_state.is_full_partition}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[ConnectionPartition] Failed to inject: {e}")
            return False
    
    def rollback(self) -> None:
        """파티션 시뮬레이션 해제."""
        with self._rollback_lock:
            if self._rollback_completed:
                logger.info(f"[ConnectionPartition] Rollback already completed for {self.experiment_id}")
                return
            
            logger.info(f"[ConnectionPartition] Rolling back {self.experiment_id}")
            
            try:
                if self._monitor_instance:
                    self._monitor_instance.clear_all_simulation_overrides()
                
                _apply_chaos_config({
                    "connection_partition": {
                        "enabled": False,
                        "experiment_id": self.experiment_id,
                    }
                })
                self._rollback_completed = True
                logger.info("[ConnectionPartition] Partition simulation cleared")
            except Exception as e:
                logger.error(f"[ConnectionPartition] Rollback failed: {e}")


__all__ = [
    "PacketLossExperiment",
    "ConnectionResetExperiment",
    "NetworkBlackholeExperiment",
    "ConnectionPartitionExperiment",
]

"""
Recovery Strategy Selector - Phase 4.3

서비스별 복구 전략을 선택하고 관리합니다.
immediate vs canary 전략을 지원합니다.

Reference: docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md Section 4, 8

전략:
- immediate: HALF_OPEN 즉시 100% 트래픽 허용 (빠른 복구, 위험도 높음)
- canary: 점진적 트래픽 증가 (안전한 복구, 시간 소요)
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable

from selfhealing.services.circuit_breaker.models import (
    RecoveryStrategy,
    CanaryStage,
    ServiceConfig,
    OpenStrategy,
)
from selfhealing.services.circuit_breaker.canary_recovery import (
    CanaryRecoveryManager,
    CanaryRecoveryState,
    CanaryDecision,
    CanaryStageTransitionResult,
    get_canary_recovery_manager,
)
from selfhealing.services.circuit_breaker.stale_cache_integration import (
    CanaryWithStaleCacheService,
    CanaryWithStaleDecision,
    get_canary_stale_cache_service,
)
from selfhealing.services.circuit_breaker.service_config import (
    ServiceConfigManager,
    get_service_config_manager,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Recovery Strategy Selection Result
# =============================================================================


@dataclass
class RecoveryStrategySelection:
    """
    복구 전략 선택 결과.
    
    Attributes:
        service_id: 서비스 ID
        strategy_type: 선택된 전략 타입 ("immediate" | "canary")
        strategy: 상세 전략 설정
        reason: 선택 사유
        source: 전략 출처 ("service_config" | "criticality_based" | "default")
    """
    
    service_id: str
    strategy_type: str
    strategy: RecoveryStrategy
    reason: str = ""
    source: str = "default"
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "service_id": self.service_id,
            "strategy_type": self.strategy_type,
            "strategy": {
                "type": self.strategy.type,
                "strict_mode": self.strategy.strict_mode,
                "on_stage_failure": self.strategy.on_stage_failure,
                "stages_count": len(self.strategy.canary_stages),
            },
            "reason": self.reason,
            "source": self.source,
        }


# =============================================================================
# Recovery Decision
# =============================================================================


@dataclass
class RecoveryDecision:
    """
    복구 결정 결과 (immediate 또는 canary 통합).
    
    Attributes:
        allow_backend: 백엔드 호출 허용 여부
        is_canary_request: Canary 요청 여부
        use_stale_cache: Stale Cache 사용 여부
        stale_data: 캐시된 데이터
        strategy_type: 적용된 전략 타입
        current_stage: 현재 Canary 단계 (canary 전략일 때)
        traffic_percent: 현재 트래픽 비율
        reason: 결정 사유
        completed: 복구 완료 여부
    """
    
    allow_backend: bool = False
    is_canary_request: bool = False
    use_stale_cache: bool = False
    stale_data: Optional[Any] = None
    strategy_type: str = "immediate"
    current_stage: Optional[str] = None
    traffic_percent: float = 100.0
    reason: str = ""
    completed: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "allow_backend": self.allow_backend,
            "is_canary_request": self.is_canary_request,
            "use_stale_cache": self.use_stale_cache,
            "stale_data": str(self.stale_data)[:100] if self.stale_data else None,
            "strategy_type": self.strategy_type,
            "current_stage": self.current_stage,
            "traffic_percent": self.traffic_percent,
            "reason": self.reason,
            "completed": self.completed,
        }


# =============================================================================
# Recovery Strategy Selector
# =============================================================================


class RecoveryStrategySelector:
    """
    복구 전략 선택자.
    
    서비스별로 적절한 복구 전략(immediate/canary)을 선택하고,
    HALF_OPEN 상태에서의 요청 처리를 관리합니다.
    
    전략 선택 우선순위:
    1. 서비스 설정의 recovery_strategy
    2. criticality 기반 자동 선택
       - critical: canary + strict_mode
       - high: canary
       - medium: canary (빠른 설정)
       - low: immediate
    3. 기본 전략
    
    Usage:
        selector = RecoveryStrategySelector()
        
        # 전략 선택
        selection = selector.select_strategy("payment-api")
        
        # HALF_OPEN 진입 시 복구 시작
        selector.start_recovery("payment-api")
        
        # 요청 처리
        decision = selector.handle_half_open_request(
            service_id="payment-api",
            cache_key="payment:user123",
        )
        
        if decision.allow_backend:
            # 백엔드 호출
            result = call_backend()
            selector.record_success("payment-api")
        elif decision.use_stale_cache:
            # Stale Cache 반환
            return decision.stale_data
    """
    
    _instance: Optional[RecoveryStrategySelector] = None
    _lock: threading.Lock = threading.Lock()
    
    def __new__(cls) -> RecoveryStrategySelector:
        """싱글톤 패턴."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        default_strategy: Optional[RecoveryStrategy] = None,
        canary_manager: Optional[CanaryRecoveryManager] = None,
        stale_cache_service: Optional[CanaryWithStaleCacheService] = None,
        service_config_manager: Optional[ServiceConfigManager] = None,
    ):
        """
        초기화.
        
        Args:
            default_strategy: 기본 복구 전략
            canary_manager: Canary Recovery 매니저
            stale_cache_service: Stale Cache 서비스
            service_config_manager: 서비스 설정 매니저
        """
        if getattr(self, "_initialized", False):
            return
        
        self._default_strategy = default_strategy or RecoveryStrategy()
        self._canary_manager = canary_manager or get_canary_recovery_manager()
        self._stale_cache = stale_cache_service or get_canary_stale_cache_service()
        self._service_config = service_config_manager or get_service_config_manager()
        
        # Criticality별 기본 전략
        self._criticality_strategies: Dict[str, RecoveryStrategy] = {
            "critical": RecoveryStrategy(
                type="canary",
                strict_mode=True,
                on_stage_failure="restart",
                canary_stages=[
                    CanaryStage(traffic_percent=5.0, duration_seconds=10, required_success_rate=99.0,
                               description="Critical Stage 1: 5% for 10s"),
                    CanaryStage(traffic_percent=20.0, duration_seconds=10, required_success_rate=98.0,
                               description="Critical Stage 2: 20% for 10s"),
                    CanaryStage(traffic_percent=50.0, duration_seconds=10, required_success_rate=97.0,
                               description="Critical Stage 3: 50% for 10s"),
                    CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=95.0,
                               description="Critical Stage 4: 100%"),
                ],
            ),
            "high": RecoveryStrategy(
                type="canary",
                strict_mode=False,
                on_stage_failure="restart",
            ),
            "medium": RecoveryStrategy(
                type="canary",
                strict_mode=False,
                on_stage_failure="restart",
                canary_stages=[
                    CanaryStage(traffic_percent=20.0, duration_seconds=5, required_success_rate=90.0,
                               description="Medium Stage 1: 20% for 5s"),
                    CanaryStage(traffic_percent=50.0, duration_seconds=5, required_success_rate=85.0,
                               description="Medium Stage 2: 50% for 5s"),
                    CanaryStage(traffic_percent=100.0, duration_seconds=0, required_success_rate=80.0,
                               description="Medium Stage 3: 100%"),
                ],
            ),
            "low": RecoveryStrategy(
                type="immediate",
            ),
        }
        
        # 서비스별 활성 복구 상태
        self._active_recoveries: Dict[str, str] = {}  # service_id -> strategy_type
        self._state_lock = threading.RLock()
        
        self._initialized = True
    
    # =========================================================================
    # Strategy Selection
    # =========================================================================
    
    def select_strategy(self, service_id: str) -> RecoveryStrategySelection:
        """
        서비스에 적합한 복구 전략 선택.
        
        선택 우선순위:
        1. 서비스 설정의 recovery_strategy
        2. criticality 기반 자동 선택
        3. 기본 전략
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            RecoveryStrategySelection
        """
        # 1. 서비스 설정 확인
        service_config = self._service_config.get_service_config(service_id)
        
        if service_config and service_config.recovery_strategy:
            strategy = service_config.recovery_strategy
            return RecoveryStrategySelection(
                service_id=service_id,
                strategy_type=strategy.type,
                strategy=strategy,
                reason=f"service-specific configuration for {service_id}",
                source="service_config",
            )
        
        # 2. Criticality 기반 선택
        if service_config:
            criticality = service_config.criticality
            if criticality in self._criticality_strategies:
                strategy = self._criticality_strategies[criticality]
                return RecoveryStrategySelection(
                    service_id=service_id,
                    strategy_type=strategy.type,
                    strategy=strategy,
                    reason=f"criticality-based selection ({criticality})",
                    source="criticality_based",
                )
        
        # 3. 기본 전략
        return RecoveryStrategySelection(
            service_id=service_id,
            strategy_type=self._default_strategy.type,
            strategy=self._default_strategy,
            reason="default strategy (no service config found)",
            source="default",
        )
    
    def set_criticality_strategy(self, criticality: str, strategy: RecoveryStrategy) -> None:
        """
        Criticality별 기본 전략 설정.
        
        Args:
            criticality: "critical" | "high" | "medium" | "low"
            strategy: 복구 전략
        """
        self._criticality_strategies[criticality] = strategy
    
    def set_default_strategy(self, strategy: RecoveryStrategy) -> None:
        """기본 전략 설정."""
        self._default_strategy = strategy
    
    # =========================================================================
    # Recovery Lifecycle
    # =========================================================================
    
    def start_recovery(self, service_id: str) -> RecoveryStrategySelection:
        """
        서비스 복구 시작 (HALF_OPEN 진입 시 호출).
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            RecoveryStrategySelection: 선택된 전략
        """
        with self._state_lock:
            selection = self.select_strategy(service_id)
            
            if selection.strategy_type == "canary":
                # Canary 복구 시작
                self._canary_manager.start_canary_recovery(
                    service_id=service_id,
                    strategy=selection.strategy,
                )
                logger.info(
                    f"[RecoveryStrategy] {service_id}: Started canary recovery, "
                    f"strict_mode={selection.strategy.strict_mode}"
                )
            else:
                # Immediate 전략 - 즉시 100% 허용
                logger.info(
                    f"[RecoveryStrategy] {service_id}: Using immediate recovery"
                )
            
            self._active_recoveries[service_id] = selection.strategy_type
            return selection
    
    def stop_recovery(self, service_id: str, reason: str = "manual") -> bool:
        """
        서비스 복구 중단.
        
        Args:
            service_id: 서비스 ID
            reason: 중단 사유
            
        Returns:
            True if stopped
        """
        with self._state_lock:
            if service_id not in self._active_recoveries:
                return False
            
            strategy_type = self._active_recoveries.pop(service_id, None)
            
            if strategy_type == "canary":
                self._canary_manager.stop_canary_recovery(service_id, reason)
            
            logger.info(f"[RecoveryStrategy] {service_id}: Stopped recovery, reason={reason}")
            return True
    
    def is_in_recovery(self, service_id: str) -> bool:
        """서비스가 복구 중인지 확인."""
        with self._state_lock:
            return service_id in self._active_recoveries
    
    def get_recovery_type(self, service_id: str) -> Optional[str]:
        """서비스의 현재 복구 전략 타입 조회."""
        with self._state_lock:
            return self._active_recoveries.get(service_id)
    
    # =========================================================================
    # Request Handling
    # =========================================================================
    
    def handle_half_open_request(
        self,
        service_id: str,
        cache_key: Optional[str] = None,
        cb_state: str = "half_open",
    ) -> RecoveryDecision:
        """
        HALF_OPEN 상태에서 요청 처리.
        
        Args:
            service_id: 서비스 ID
            cache_key: 캐시 키 (Stale Cache 사용 시)
            cb_state: CB 상태
            
        Returns:
            RecoveryDecision
        """
        with self._state_lock:
            strategy_type = self._active_recoveries.get(service_id)
        
        if strategy_type is None:
            # 복구 중이 아니면 시작
            selection = self.start_recovery(service_id)
            strategy_type = selection.strategy_type
        
        if strategy_type == "immediate":
            # Immediate 전략: 즉시 100% 허용
            return RecoveryDecision(
                allow_backend=True,
                is_canary_request=False,
                strategy_type="immediate",
                traffic_percent=100.0,
                reason="immediate recovery - all requests allowed",
            )
        
        # Canary 전략: Stale Cache 통합 사용
        if cache_key:
            stale_decision = self._stale_cache.should_allow_with_fallback(
                service_id=service_id,
                cache_key=cache_key,
                cb_state=cb_state,
            )
            
            return RecoveryDecision(
                allow_backend=stale_decision.allow_backend,
                is_canary_request=stale_decision.is_canary_request,
                use_stale_cache=stale_decision.use_stale,
                stale_data=stale_decision.stale_data,
                strategy_type="canary",
                current_stage=stale_decision.current_stage.value if stale_decision.current_stage else None,
                traffic_percent=stale_decision.traffic_percent,
                reason=stale_decision.reason,
            )
        
        # cache_key 없이 Canary만 사용
        canary_decision = self._canary_manager.should_allow_request(service_id)
        
        return RecoveryDecision(
            allow_backend=canary_decision.allow_backend,
            is_canary_request=canary_decision.is_canary_request,
            use_stale_cache=canary_decision.use_stale_cache,
            strategy_type="canary",
            current_stage=canary_decision.current_stage.value if canary_decision.current_stage else None,
            traffic_percent=canary_decision.traffic_percent,
            reason=canary_decision.reason,
        )
    
    # =========================================================================
    # Metrics Recording
    # =========================================================================
    
    def record_success(self, service_id: str) -> Optional[CanaryStageTransitionResult]:
        """
        성공 기록.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            CanaryStageTransitionResult if stage transition occurred
        """
        with self._state_lock:
            strategy_type = self._active_recoveries.get(service_id)
        
        if strategy_type != "canary":
            return None
        
        result = self._canary_manager.record_success(service_id)
        
        # 복구 완료 확인
        if result and result.completed:
            with self._state_lock:
                self._active_recoveries.pop(service_id, None)
            logger.info(f"[RecoveryStrategy] {service_id}: Recovery completed")
        
        return result
    
    def record_failure(self, service_id: str) -> Optional[CanaryStageTransitionResult]:
        """
        실패 기록.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            CanaryStageTransitionResult if recovery failed
        """
        with self._state_lock:
            strategy_type = self._active_recoveries.get(service_id)
        
        if strategy_type != "canary":
            return None
        
        result = self._canary_manager.record_failure(service_id)
        
        # 복구 실패 확인
        if result and result.failed:
            with self._state_lock:
                self._active_recoveries.pop(service_id, None)
            logger.warning(f"[RecoveryStrategy] {service_id}: Recovery failed")
        
        return result
    
    # =========================================================================
    # Status & Diagnostics
    # =========================================================================
    
    def get_active_recoveries(self) -> Dict[str, str]:
        """활성 복구 목록 조회."""
        with self._state_lock:
            return dict(self._active_recoveries)
    
    def get_recovery_status(self, service_id: str) -> Optional[Dict[str, Any]]:
        """
        서비스의 복구 상태 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            복구 상태 딕셔너리
        """
        with self._state_lock:
            strategy_type = self._active_recoveries.get(service_id)
        
        if strategy_type is None:
            return None
        
        selection = self.select_strategy(service_id)
        
        result: Dict[str, Any] = {
            "service_id": service_id,
            "strategy_type": strategy_type,
            "strategy_selection": selection.to_dict(),
        }
        
        if strategy_type == "canary":
            canary_state = self._canary_manager.get_recovery_state(service_id)
            if canary_state:
                result["canary_state"] = canary_state.to_dict()
        
        return result
    
    def reset(self) -> None:
        """모든 상태 초기화."""
        with self._state_lock:
            self._active_recoveries.clear()
        logger.info("[RecoveryStrategy] All states reset")


# =============================================================================
# Module-level Singleton Functions
# =============================================================================


_selector_instance: Optional[RecoveryStrategySelector] = None
_selector_lock = threading.Lock()


def get_recovery_strategy_selector() -> RecoveryStrategySelector:
    """싱글톤 인스턴스 반환."""
    global _selector_instance
    if _selector_instance is None:
        with _selector_lock:
            if _selector_instance is None:
                _selector_instance = RecoveryStrategySelector()
    return _selector_instance


def reset_recovery_strategy_selector() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _selector_instance
    with _selector_lock:
        if _selector_instance is not None:
            _selector_instance.reset()
        _selector_instance = None
        RecoveryStrategySelector._instance = None


# =============================================================================
# Convenience Functions
# =============================================================================


def select_recovery_strategy(service_id: str) -> RecoveryStrategySelection:
    """서비스 복구 전략 선택."""
    return get_recovery_strategy_selector().select_strategy(service_id)


def start_service_recovery(service_id: str) -> RecoveryStrategySelection:
    """서비스 복구 시작."""
    return get_recovery_strategy_selector().start_recovery(service_id)


def stop_service_recovery(service_id: str, reason: str = "manual") -> bool:
    """서비스 복구 중단."""
    return get_recovery_strategy_selector().stop_recovery(service_id, reason)


def handle_half_open(
    service_id: str,
    cache_key: Optional[str] = None,
) -> RecoveryDecision:
    """HALF_OPEN 상태 요청 처리."""
    return get_recovery_strategy_selector().handle_half_open_request(
        service_id=service_id,
        cache_key=cache_key,
    )


def record_recovery_success(service_id: str) -> Optional[CanaryStageTransitionResult]:
    """복구 성공 기록."""
    return get_recovery_strategy_selector().record_success(service_id)


def record_recovery_failure(service_id: str) -> Optional[CanaryStageTransitionResult]:
    """복구 실패 기록."""
    return get_recovery_strategy_selector().record_failure(service_id)

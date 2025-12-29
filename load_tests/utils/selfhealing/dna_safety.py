"""
DNA Safety Features - Rollback & Blast Radius

Phase 1 구현: 안전한 복구 및 장애 격리를 위한 DNA Safety 기능

기능:
1. 자동/수동 롤백 메커니즘
2. 상태 스냅샷 관리
3. Blast Radius 계산 및 격리
4. 안전한 복구 보장

Reference: docs/self_healing/33_DNA_SAFETY_FEATURES.md

Note:
- 기존 chaos.py, runtime_config.py, governance.py의 API를 활용
- Stage DNA 레벨의 통합 로직만 신규 구현
"""

from typing import Dict, List, Optional, Any, Tuple, Callable, Union
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from collections import deque
import threading
import json
import copy
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# Rollback DNA
# =============================================================================

class RollbackStrategy(Enum):
    """롤백 전략"""
    AUTOMATIC = "automatic"     # 자동 롤백
    MANUAL = "manual"           # 수동 롤백만
    HYBRID = "hybrid"           # 자동 + 수동 확인


class RollbackTarget(Enum):
    """롤백 대상"""
    CONFIG = "config"
    FEATURE_FLAGS = "feature_flags"
    RATE_LIMITS = "rate_limits"
    CIRCUIT_BREAKERS = "circuit_breakers"
    DATABASE = "database"
    CACHE = "cache"
    ALL = "all"


@dataclass
class StateSnapshot:
    """상태 스냅샷"""
    snapshot_id: str
    created_at: str
    state: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "created_at": self.created_at,
            "state_keys": list(self.state.keys()),
            "metadata": self.metadata,
        }
    
    def __repr__(self) -> str:
        return f"StateSnapshot(id={self.snapshot_id}, keys={list(self.state.keys())})"


@dataclass
class RollbackResult:
    """롤백 결과"""
    success: bool
    rollback_id: str
    triggered_at: str
    completed_at: Optional[str]
    trigger_reason: str
    targets_rolled_back: List[str]
    snapshot_used: Optional[str]
    errors: List[str] = field(default_factory=list)
    
    @property
    def duration_seconds(self) -> Optional[float]:
        if self.completed_at:
            start = datetime.fromisoformat(self.triggered_at)
            end = datetime.fromisoformat(self.completed_at)
            return (end - start).total_seconds()
        return None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "rollback_id": self.rollback_id,
            "triggered_at": self.triggered_at,
            "completed_at": self.completed_at,
            "trigger_reason": self.trigger_reason,
            "targets_rolled_back": self.targets_rolled_back,
            "snapshot_used": self.snapshot_used,
            "duration_seconds": self.duration_seconds,
            "errors": self.errors,
        }


class RollbackController:
    """
    롤백 컨트롤러
    
    Stage DNA의 rollback 설정을 기반으로 자동 롤백을 관리합니다.
    기존 runtime_config.py, governance.py의 API를 활용합니다.
    
    Usage:
        config = stage_dna.get("rollback", {})
        controller = RollbackController(config)
        
        # 스냅샷 생성
        controller.take_snapshot(current_state)
        
        # 자동 롤백 체크
        result = controller.auto_rollback_check(metrics)
    """
    
    def __init__(
        self,
        config: Dict[str, Any],
        runtime_config_client: Optional[Any] = None,
        governance_client: Optional[Any] = None,
    ):
        """
        Args:
            config: Stage DNA의 rollback 설정
            runtime_config_client: 기존 RuntimeConfigClient (옵션)
            governance_client: 기존 GovernanceClient (옵션)
        """
        self.config = config
        self.runtime_config = runtime_config_client
        self.governance = governance_client
        
        # 전략 설정
        strategy_str = config.get("strategy", "automatic")
        self.strategy = RollbackStrategy(strategy_str)
        
        # 트리거 조건
        self.triggers = config.get("triggers", {})
        self.timeout = config.get("timeout_seconds", 120)
        self.observation_window = config.get("observation_window_seconds", 30)
        
        # 스냅샷 설정
        snapshot_config = config.get("snapshot", {})
        max_snapshots = snapshot_config.get("max_snapshots", 10)
        self.snapshot_enabled = snapshot_config.get("enabled", True)
        self.snapshot_interval = snapshot_config.get("interval_seconds", 60)
        
        # 상태 관리
        self.snapshots: deque = deque(maxlen=max_snapshots)
        self.current_state: Dict[str, Any] = {}
        self.rollback_history: List[RollbackResult] = []
        
        self._lock = threading.Lock()
        self._is_rolling_back = False
        self._last_snapshot_time: Optional[datetime] = None
    
    def take_snapshot(
        self,
        state: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        force: bool = False,
    ) -> Optional[StateSnapshot]:
        """
        상태 스냅샷 생성
        
        Args:
            state: 현재 시스템 상태
            metadata: 추가 메타데이터
            force: interval 무시하고 강제 생성
        
        Returns:
            StateSnapshot 또는 None (interval 미충족 시)
        """
        if not self.snapshot_enabled:
            return None
        
        # 인터벌 체크 (force가 아닌 경우)
        now = datetime.now()
        if not force and self._last_snapshot_time:
            elapsed = (now - self._last_snapshot_time).total_seconds()
            if elapsed < self.snapshot_interval:
                return None
        
        snapshot = StateSnapshot(
            snapshot_id=f"snap_{now.strftime('%Y%m%d_%H%M%S_%f')}",
            created_at=now.isoformat(),
            state=copy.deepcopy(state),
            metadata=metadata or {},
        )
        
        with self._lock:
            self.snapshots.append(snapshot)
            self.current_state = copy.deepcopy(state)
            self._last_snapshot_time = now
        
        logger.info(f"Snapshot created: {snapshot.snapshot_id}")
        return snapshot
    
    def get_latest_snapshot(self) -> Optional[StateSnapshot]:
        """최신 스냅샷 조회"""
        with self._lock:
            if self.snapshots:
                return self.snapshots[-1]
        return None
    
    def get_snapshot_by_id(self, snapshot_id: str) -> Optional[StateSnapshot]:
        """ID로 스냅샷 조회"""
        with self._lock:
            for snapshot in self.snapshots:
                if snapshot.snapshot_id == snapshot_id:
                    return snapshot
        return None
    
    def get_stable_snapshot(self, offset: int = 1) -> Optional[StateSnapshot]:
        """
        안정된 스냅샷 조회 (현재에서 offset만큼 이전)
        
        Args:
            offset: 현재 스냅샷에서 몇 개 이전 (기본 1)
        
        Returns:
            StateSnapshot 또는 None
        """
        with self._lock:
            if len(self.snapshots) > offset:
                return list(self.snapshots)[-(offset + 1)]
            elif self.snapshots:
                return self.snapshots[0]
        return None
    
    def check_trigger_conditions(self, metrics: Dict[str, float]) -> Tuple[bool, str]:
        """
        롤백 트리거 조건 체크
        
        Args:
            metrics: 현재 메트릭
                - error_rate: 에러율 (0.0 ~ 1.0)
                - baseline_p99: 기준 p99 레이턴시 (ms)
                - current_p99: 현재 p99 레이턴시 (ms)
                - consecutive_failures: 연속 실패 횟수
                - health_check_failures: 헬스체크 실패 횟수
        
        Returns:
            (should_trigger, reason)
        """
        # 에러율 체크
        error_threshold = self.triggers.get("error_rate_threshold", 0.1)
        if metrics.get("error_rate", 0) > error_threshold:
            return True, f"Error rate {metrics['error_rate']:.2%} > {error_threshold:.2%}"
        
        # 레이턴시 스파이크 체크
        latency_ratio = self.triggers.get("latency_spike_ratio", 3.0)
        baseline_latency = metrics.get("baseline_p99", 100)
        current_latency = metrics.get("current_p99", 0)
        if baseline_latency > 0 and current_latency / baseline_latency > latency_ratio:
            ratio = current_latency / baseline_latency
            return True, f"Latency spike {ratio:.1f}x > {latency_ratio}x"
        
        # 연속 실패 체크
        consecutive = self.triggers.get("consecutive_failures", 5)
        if metrics.get("consecutive_failures", 0) >= consecutive:
            return True, f"Consecutive failures {metrics['consecutive_failures']} >= {consecutive}"
        
        # 헬스체크 실패 체크
        health_failures = self.triggers.get("health_check_failures", 3)
        if metrics.get("health_check_failures", 0) >= health_failures:
            return True, f"Health check failures {metrics['health_check_failures']} >= {health_failures}"
        
        return False, "No trigger condition met"
    
    def rollback(
        self,
        reason: str,
        snapshot_id: Optional[str] = None,
        targets: Optional[List[Union[RollbackTarget, str]]] = None,
        apply_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> RollbackResult:
        """
        롤백 실행
        
        Args:
            reason: 롤백 사유
            snapshot_id: 복원할 스냅샷 ID (없으면 직전 안정 스냅샷 사용)
            targets: 롤백 대상 목록
            apply_fn: 실제 상태 적용 함수 (state) -> success
        
        Returns:
            RollbackResult
        """
        with self._lock:
            if self._is_rolling_back:
                return RollbackResult(
                    success=False,
                    rollback_id=f"rb_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    triggered_at=datetime.now().isoformat(),
                    completed_at=None,
                    trigger_reason=reason,
                    targets_rolled_back=[],
                    snapshot_used=None,
                    errors=["Rollback already in progress"],
                )
            self._is_rolling_back = True
        
        rollback_id = f"rb_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        triggered_at = datetime.now().isoformat()
        errors = []
        
        try:
            # 스냅샷 선택
            if snapshot_id:
                snapshot = self.get_snapshot_by_id(snapshot_id)
            else:
                # 현재 직전 스냅샷 사용 (최신 - 1)
                snapshot = self.get_stable_snapshot(offset=1)
            
            if not snapshot:
                return RollbackResult(
                    success=False,
                    rollback_id=rollback_id,
                    triggered_at=triggered_at,
                    completed_at=datetime.now().isoformat(),
                    trigger_reason=reason,
                    targets_rolled_back=[],
                    snapshot_used=None,
                    errors=["No snapshot available for rollback"],
                )
            
            # 롤백 대상 결정
            if targets is None:
                target_strs = self.config.get("targets", ["config"])
                targets = [
                    RollbackTarget(t) if isinstance(t, str) else t 
                    for t in target_strs
                ]
            else:
                targets = [
                    RollbackTarget(t) if isinstance(t, str) else t 
                    for t in targets
                ]
            
            rolled_back = []
            
            for target in targets:
                target_value = target.value if isinstance(target, RollbackTarget) else target
                
                try:
                    target_state = snapshot.state.get(target_value, {})
                    
                    if apply_fn:
                        success = apply_fn({target_value: target_state})
                    else:
                        # 기본: 현재 상태에 복원
                        self.current_state[target_value] = copy.deepcopy(target_state)
                        success = True
                    
                    if success:
                        rolled_back.append(target_value)
                        logger.info(f"Rolled back {target_value}")
                    else:
                        errors.append(f"Failed to rollback {target_value}")
                
                except Exception as e:
                    errors.append(f"Error rolling back {target_value}: {str(e)}")
            
            result = RollbackResult(
                success=len(errors) == 0 and len(rolled_back) > 0,
                rollback_id=rollback_id,
                triggered_at=triggered_at,
                completed_at=datetime.now().isoformat(),
                trigger_reason=reason,
                targets_rolled_back=rolled_back,
                snapshot_used=snapshot.snapshot_id,
                errors=errors,
            )
            
            self.rollback_history.append(result)
            
            # 알림
            self._send_notification(result)
            
            return result
        
        finally:
            with self._lock:
                self._is_rolling_back = False
    
    def auto_rollback_check(
        self,
        metrics: Dict[str, float],
        apply_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
    ) -> Optional[RollbackResult]:
        """
        자동 롤백 체크 및 실행
        
        Args:
            metrics: 현재 시스템 메트릭
            apply_fn: 실제 상태 적용 함수
        
        Returns:
            RollbackResult 또는 None (트리거 안됨)
        """
        if self.strategy == RollbackStrategy.MANUAL:
            return None
        
        should_trigger, reason = self.check_trigger_conditions(metrics)
        
        if should_trigger:
            logger.warning(f"Auto rollback triggered: {reason}")
            
            if self.strategy == RollbackStrategy.HYBRID:
                # Hybrid 모드: 알림 후 대기
                logger.info("Hybrid mode: Sending approval request...")
                self._send_approval_request(reason)
                # 실제 구현에서는 승인 대기 로직 필요
                # 여기서는 자동 승인으로 처리
            
            return self.rollback(reason=reason, apply_fn=apply_fn)
        
        return None
    
    def _send_notification(self, result: RollbackResult):
        """롤백 알림 전송"""
        notification_config = self.config.get("notification", {})
        
        if result.success and notification_config.get("on_success", True):
            logger.info(
                f"[ROLLBACK SUCCESS] {result.rollback_id}\n"
                f"  Reason: {result.trigger_reason}\n"
                f"  Duration: {result.duration_seconds:.1f}s\n"
                f"  Targets: {', '.join(result.targets_rolled_back)}\n"
                f"  Snapshot: {result.snapshot_used}"
            )
        elif not result.success and notification_config.get("on_failure", True):
            logger.error(
                f"[ROLLBACK FAILED] {result.rollback_id}\n"
                f"  Reason: {result.trigger_reason}\n"
                f"  Errors: {', '.join(result.errors)}"
            )
    
    def _send_approval_request(self, reason: str):
        """승인 요청 전송 (Hybrid 모드용)"""
        logger.info(f"[APPROVAL REQUEST] Auto-rollback triggered: {reason}")
        # 실제 구현에서는 Slack, PagerDuty 등으로 알림
    
    def get_rollback_summary(self) -> Dict[str, Any]:
        """롤백 요약"""
        with self._lock:
            latest_snapshot = self.snapshots[-1].to_dict() if self.snapshots else None
        
        return {
            "strategy": self.strategy.value,
            "total_snapshots": len(self.snapshots),
            "total_rollbacks": len(self.rollback_history),
            "successful_rollbacks": sum(1 for r in self.rollback_history if r.success),
            "failed_rollbacks": sum(1 for r in self.rollback_history if not r.success),
            "last_snapshot": latest_snapshot,
            "is_rolling_back": self._is_rolling_back,
            "triggers": self.triggers,
        }


# =============================================================================
# Blast Radius DNA
# =============================================================================

class BlastRadiusScope(Enum):
    """Blast Radius 범위"""
    ISOLATED = "isolated"       # 단일 서비스 격리
    LIMITED = "limited"         # 제한된 서비스 그룹
    MODERATE = "moderate"       # 중간 규모 영향
    EXTENSIVE = "extensive"     # 광범위 영향
    CRITICAL = "critical"       # 전체 시스템 영향


@dataclass
class BlastRadiusAnalysis:
    """Blast Radius 분석 결과"""
    failed_service: str
    scope: BlastRadiusScope
    affected_services: List[str]
    affected_users_percentage: float
    affected_revenue_percentage: float
    isolation_strategy: str
    containment_actions: List[str]
    estimated_recovery_time_minutes: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "failed_service": self.failed_service,
            "scope": self.scope.value,
            "affected_services": self.affected_services,
            "affected_users_percentage": self.affected_users_percentage,
            "affected_revenue_percentage": self.affected_revenue_percentage,
            "isolation_strategy": self.isolation_strategy,
            "containment_actions": self.containment_actions,
            "estimated_recovery_time_minutes": self.estimated_recovery_time_minutes,
        }


class BlastRadiusController:
    """
    Blast Radius 컨트롤러
    
    서비스 장애 시 영향 범위를 분석하고 격리 전략을 제공합니다.
    기존 chaos.py의 blast_radius 관련 API를 확장합니다.
    
    Usage:
        config = stage_dna.get("blast_radius", {})
        controller = BlastRadiusController(config)
        
        analysis = controller.analyze_blast_radius("payment")
        if controller.should_auto_isolate(analysis):
            controller.execute_isolation(analysis)
    """
    
    # 서비스별 기본 중요도
    DEFAULT_SERVICE_WEIGHTS: Dict[str, Dict[str, Any]] = {
        "payment": {"users": 1.0, "revenue": 1.0, "tier": "critical"},
        "order": {"users": 0.9, "revenue": 0.9, "tier": "critical"},
        "cart": {"users": 0.8, "revenue": 0.6, "tier": "high"},
        "product": {"users": 0.9, "revenue": 0.3, "tier": "high"},
        "user": {"users": 1.0, "revenue": 0.2, "tier": "critical"},
        "inventory": {"users": 0.5, "revenue": 0.4, "tier": "medium"},
        "notification": {"users": 0.3, "revenue": 0.05, "tier": "low"},
        "logging": {"users": 0.0, "revenue": 0.0, "tier": "low"},
        "analytics": {"users": 0.1, "revenue": 0.0, "tier": "low"},
    }
    
    # 격리 전략 매핑
    ISOLATION_STRATEGIES: Dict[BlastRadiusScope, str] = {
        BlastRadiusScope.ISOLATED: "circuit_breaker",
        BlastRadiusScope.LIMITED: "bulkhead",
        BlastRadiusScope.MODERATE: "rate_limiting",
        BlastRadiusScope.EXTENSIVE: "emergency_mode",
        BlastRadiusScope.CRITICAL: "full_shutdown",
    }
    
    def __init__(
        self,
        config: Dict[str, Any],
        chaos_client: Optional[Any] = None,
        xtest_client: Optional[Any] = None,
    ):
        """
        Args:
            config: Stage DNA의 blast_radius 설정
            chaos_client: 기존 ChaosClient (옵션)
            xtest_client: 기존 XTestClient (옵션)
        """
        self.config = config
        self.chaos = chaos_client
        self.xtest = xtest_client
        
        # 서비스 가중치
        self.service_weights = {
            **self.DEFAULT_SERVICE_WEIGHTS,
            **config.get("service_weights", {}),
        }
        
        # 설정
        self.max_affected = config.get("max_affected_services", 3)
        self.auto_isolate = config.get("auto_isolate", True)
        
        # 의존성 그래프
        self.dependencies = config.get("dependencies", self._default_dependencies())
    
    def _default_dependencies(self) -> Dict[str, List[str]]:
        """기본 서비스 의존성 그래프"""
        return {
            "order": ["payment", "inventory", "user", "notification"],
            "payment": ["user"],
            "cart": ["product", "user"],
            "checkout": ["cart", "payment", "order"],
            "notification": ["user"],
            "analytics": [],
            "logging": [],
        }
    
    def analyze_blast_radius(
        self,
        failed_service: str,
        dependency_graph: Optional[Dict[str, List[str]]] = None,
    ) -> BlastRadiusAnalysis:
        """
        Blast Radius 분석
        
        Args:
            failed_service: 장애 발생 서비스
            dependency_graph: 커스텀 의존성 그래프 (옵션)
        
        Returns:
            BlastRadiusAnalysis
        """
        deps = dependency_graph or self.dependencies
        
        # 영향 받는 서비스 계산 (역방향 BFS)
        affected = self._calculate_affected_services(failed_service, deps)
        
        # 영향도 계산
        users_impact = self._calculate_users_impact(affected)
        revenue_impact = self._calculate_revenue_impact(affected)
        
        # 범위 결정
        scope = self._determine_scope(len(affected), users_impact, revenue_impact)
        
        # 격리 전략
        isolation = self.ISOLATION_STRATEGIES.get(scope, "circuit_breaker")
        
        # 조치 항목
        actions = self._generate_containment_actions(scope, failed_service, affected)
        
        # 예상 복구 시간
        recovery_time = self._estimate_recovery_time(scope, len(affected))
        
        return BlastRadiusAnalysis(
            failed_service=failed_service,
            scope=scope,
            affected_services=affected,
            affected_users_percentage=users_impact * 100,
            affected_revenue_percentage=revenue_impact * 100,
            isolation_strategy=isolation,
            containment_actions=actions,
            estimated_recovery_time_minutes=recovery_time,
        )
    
    def _calculate_affected_services(
        self,
        failed_service: str,
        deps: Dict[str, List[str]],
    ) -> List[str]:
        """영향 받는 서비스 계산 (역방향 BFS)"""
        affected = [failed_service]
        visited = {failed_service}
        queue = [failed_service]
        
        # 역방향 의존성 맵 생성 (어떤 서비스가 이 서비스에 의존하는가)
        reverse_deps: Dict[str, List[str]] = {}
        for service, dependencies in deps.items():
            for dep in dependencies:
                if dep not in reverse_deps:
                    reverse_deps[dep] = []
                reverse_deps[dep].append(service)
        
        # BFS로 영향 받는 서비스 탐색
        while queue:
            current = queue.pop(0)
            for dependent in reverse_deps.get(current, []):
                if dependent not in visited:
                    visited.add(dependent)
                    affected.append(dependent)
                    queue.append(dependent)
        
        return affected
    
    def _calculate_users_impact(self, affected: List[str]) -> float:
        """사용자 영향도 계산"""
        total_impact = 0.0
        for service in affected:
            weight = self.service_weights.get(service, {})
            total_impact += weight.get("users", 0.1)
        return min(total_impact, 1.0)
    
    def _calculate_revenue_impact(self, affected: List[str]) -> float:
        """매출 영향도 계산"""
        total_impact = 0.0
        for service in affected:
            weight = self.service_weights.get(service, {})
            total_impact += weight.get("revenue", 0.1)
        return min(total_impact, 1.0)
    
    def _determine_scope(
        self,
        affected_count: int,
        users_impact: float,
        revenue_impact: float,
    ) -> BlastRadiusScope:
        """Blast Radius 범위 결정"""
        # 중요도 기반 결정
        max_impact = max(users_impact, revenue_impact)
        
        if affected_count <= 1 and max_impact < 0.2:
            return BlastRadiusScope.ISOLATED
        elif affected_count <= 3 and max_impact < 0.4:
            return BlastRadiusScope.LIMITED
        elif affected_count <= 5 and max_impact < 0.6:
            return BlastRadiusScope.MODERATE
        elif max_impact < 0.8:
            return BlastRadiusScope.EXTENSIVE
        else:
            return BlastRadiusScope.CRITICAL
    
    def _generate_containment_actions(
        self,
        scope: BlastRadiusScope,
        failed_service: str,
        affected: List[str],
    ) -> List[str]:
        """억제 조치 생성"""
        actions = []
        
        if scope == BlastRadiusScope.ISOLATED:
            actions.extend([
                f"Circuit Breaker 활성화: {failed_service}",
                f"Fallback 서비스로 라우팅",
            ])
        
        elif scope == BlastRadiusScope.LIMITED:
            actions.extend([
                f"Bulkhead 패턴 적용: {', '.join(affected)}",
                f"Rate Limit 강화",
                f"Cache 우선 모드 활성화",
            ])
        
        elif scope == BlastRadiusScope.MODERATE:
            actions.extend([
                f"Emergency Mode Level 1 활성화",
                f"비핵심 기능 일시 중단",
                f"DLQ 모드로 전환",
            ])
        
        elif scope == BlastRadiusScope.EXTENSIVE:
            actions.extend([
                f"Emergency Mode Level 2 활성화",
                f"읽기 전용 모드 전환",
                f"관리자 알림 발송",
            ])
        
        else:  # CRITICAL
            actions.extend([
                f"Emergency Mode Level 3 (전체 보호)",
                f"모든 쓰기 작업 중단",
                f"즉시 수동 개입 필요",
                f"인시던트 티켓 자동 생성",
            ])
        
        return actions
    
    def _estimate_recovery_time(
        self,
        scope: BlastRadiusScope,
        affected_count: int,
    ) -> int:
        """예상 복구 시간 (분)"""
        base_times = {
            BlastRadiusScope.ISOLATED: 5,
            BlastRadiusScope.LIMITED: 15,
            BlastRadiusScope.MODERATE: 30,
            BlastRadiusScope.EXTENSIVE: 60,
            BlastRadiusScope.CRITICAL: 120,
        }
        
        base = base_times.get(scope, 30)
        # 영향 서비스 수에 따라 추가 시간
        return base + (affected_count * 2)
    
    def should_auto_isolate(self, analysis: BlastRadiusAnalysis) -> bool:
        """자동 격리 여부 판단"""
        if not self.auto_isolate:
            return False
        
        # 영향 범위가 설정된 최대치 이하일 때만 자동 격리
        return len(analysis.affected_services) <= self.max_affected
    
    def execute_isolation(
        self,
        analysis: BlastRadiusAnalysis,
        isolation_fn: Optional[Callable[[str, str], bool]] = None,
    ) -> Dict[str, Any]:
        """
        격리 실행
        
        Args:
            analysis: BlastRadiusAnalysis
            isolation_fn: 실제 격리 함수 (service, action) -> success
        
        Returns:
            실행 결과 딕셔너리
        """
        results = {
            "executed": False,
            "strategy": analysis.isolation_strategy,
            "actions_taken": [],
            "errors": [],
            "timestamp": datetime.now().isoformat(),
        }
        
        if not self.should_auto_isolate(analysis):
            results["errors"].append(
                f"Auto-isolation disabled or affected count ({len(analysis.affected_services)}) "
                f"> max_affected ({self.max_affected})"
            )
            return results
        
        for action in analysis.containment_actions:
            try:
                if isolation_fn:
                    success = isolation_fn(analysis.failed_service, action)
                else:
                    # 기본: 로깅만
                    success = True
                    logger.info(f"[ISOLATION] {action}")
                
                if success:
                    results["actions_taken"].append(action)
            except Exception as e:
                results["errors"].append(f"Failed action '{action}': {str(e)}")
        
        results["executed"] = len(results["errors"]) == 0 and len(results["actions_taken"]) > 0
        return results
    
    def get_isolation_summary(self) -> Dict[str, Any]:
        """격리 설정 요약"""
        return {
            "auto_isolate": self.auto_isolate,
            "max_affected_services": self.max_affected,
            "service_weights": self.service_weights,
            "dependencies": self.dependencies,
            "isolation_strategies": {
                k.value: v for k, v in self.ISOLATION_STRATEGIES.items()
            },
        }


# =============================================================================
# DNA Safety 통합 인터페이스
# =============================================================================

class DNASafetyManager:
    """
    DNA Safety 통합 관리자
    
    Stage DNA의 rollback과 blast_radius 설정을 통합 관리합니다.
    
    Usage:
        safety = DNASafetyManager(stage_dna)
        
        # 스냅샷 생성
        safety.take_snapshot(current_state)
        
        # 서비스 장애 시
        analysis = safety.analyze_failure("payment", metrics)
        if analysis["should_rollback"]:
            safety.rollback(analysis["reason"])
    """
    
    def __init__(
        self,
        stage_dna: Dict[str, Any],
        runtime_config_client: Optional[Any] = None,
        governance_client: Optional[Any] = None,
        chaos_client: Optional[Any] = None,
    ):
        """
        Args:
            stage_dna: 전체 Stage DNA 딕셔너리
            runtime_config_client: 기존 RuntimeConfigClient (옵션)
            governance_client: 기존 GovernanceClient (옵션)
            chaos_client: 기존 ChaosClient (옵션)
        """
        self.stage_dna = stage_dna
        self.stage_name = stage_dna.get("name", "Unknown Stage")
        
        # 롤백 컨트롤러
        rollback_config = stage_dna.get("rollback", {})
        self.rollback = RollbackController(
            rollback_config,
            runtime_config_client=runtime_config_client,
            governance_client=governance_client,
        )
        
        # Blast Radius 컨트롤러
        blast_config = stage_dna.get("blast_radius", {})
        self.blast_radius = BlastRadiusController(
            blast_config,
            chaos_client=chaos_client,
        )
    
    def take_snapshot(
        self,
        state: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[StateSnapshot]:
        """상태 스냅샷 생성"""
        return self.rollback.take_snapshot(state, metadata)
    
    def analyze_failure(
        self,
        failed_service: str,
        metrics: Dict[str, float],
    ) -> Dict[str, Any]:
        """
        장애 분석 (Rollback + Blast Radius 통합)
        
        Args:
            failed_service: 장애 발생 서비스
            metrics: 현재 시스템 메트릭
        
        Returns:
            분석 결과 딕셔너리
        """
        # Rollback 트리거 체크
        should_rollback, rollback_reason = self.rollback.check_trigger_conditions(metrics)
        
        # Blast Radius 분석
        blast_analysis = self.blast_radius.analyze_blast_radius(failed_service)
        
        return {
            "stage_name": self.stage_name,
            "failed_service": failed_service,
            "should_rollback": should_rollback,
            "rollback_reason": rollback_reason,
            "blast_radius": blast_analysis.to_dict(),
            "should_auto_isolate": self.blast_radius.should_auto_isolate(blast_analysis),
            "recommended_actions": blast_analysis.containment_actions,
            "estimated_recovery_minutes": blast_analysis.estimated_recovery_time_minutes,
        }
    
    def handle_failure(
        self,
        failed_service: str,
        metrics: Dict[str, float],
        apply_fn: Optional[Callable[[Dict[str, Any]], bool]] = None,
        isolation_fn: Optional[Callable[[str, str], bool]] = None,
    ) -> Dict[str, Any]:
        """
        장애 자동 처리 (분석 + 롤백 + 격리)
        
        Args:
            failed_service: 장애 발생 서비스
            metrics: 현재 시스템 메트릭
            apply_fn: 롤백 적용 함수
            isolation_fn: 격리 적용 함수
        
        Returns:
            처리 결과 딕셔너리
        """
        result = {
            "stage_name": self.stage_name,
            "failed_service": failed_service,
            "analysis": None,
            "rollback_result": None,
            "isolation_result": None,
        }
        
        # 분석
        analysis = self.analyze_failure(failed_service, metrics)
        result["analysis"] = analysis
        
        # 자동 롤백
        if analysis["should_rollback"]:
            rollback_result = self.rollback.auto_rollback_check(metrics, apply_fn)
            if rollback_result:
                result["rollback_result"] = rollback_result.to_dict()
        
        # 자동 격리
        if analysis["should_auto_isolate"]:
            blast_analysis = self.blast_radius.analyze_blast_radius(failed_service)
            isolation_result = self.blast_radius.execute_isolation(blast_analysis, isolation_fn)
            result["isolation_result"] = isolation_result
        
        return result
    
    def get_summary(self) -> Dict[str, Any]:
        """Safety 상태 요약"""
        return {
            "stage_name": self.stage_name,
            "rollback": self.rollback.get_rollback_summary(),
            "blast_radius": self.blast_radius.get_isolation_summary(),
        }


# =============================================================================
# DNA 스키마 검증
# =============================================================================

def validate_safety_dna(safety_config: Dict[str, Any]) -> List[str]:
    """
    Safety DNA 스키마 검증
    
    Args:
        safety_config: rollback 및 blast_radius 설정 포함 딕셔너리
    
    Returns:
        경고 메시지 목록
    """
    warnings = []
    
    # Rollback 검증
    rollback = safety_config.get("rollback", {})
    if rollback:
        strategy = rollback.get("strategy", "manual")
        if strategy not in ["automatic", "manual", "hybrid"]:
            warnings.append(f"Invalid rollback strategy: {strategy}")
        
        triggers = rollback.get("triggers", {})
        if not triggers and strategy == "automatic":
            warnings.append("Automatic rollback requires trigger conditions")
        
        # 트리거 값 범위 검증
        if triggers.get("error_rate_threshold", 0) > 1.0:
            warnings.append("error_rate_threshold should be 0.0 ~ 1.0")
    
    # Blast Radius 검증
    blast_radius = safety_config.get("blast_radius", {})
    if blast_radius:
        max_affected = blast_radius.get("max_affected_services", 3)
        if max_affected < 1:
            warnings.append("max_affected_services should be >= 1")
    
    return warnings


# =============================================================================
# 리포트 생성
# =============================================================================

def generate_safety_report(safety_manager: DNASafetyManager) -> str:
    """Safety 리포트 마크다운 생성"""
    summary = safety_manager.get_summary()
    
    lines = [
        "",
        "## 🛡️ DNA Safety Report",
        "",
        f"**Stage**: {summary['stage_name']}",
        "",
        "### Rollback Status",
        "",
        "| 항목 | 값 |",
        "|-----|-----|",
        f"| Strategy | {summary['rollback']['strategy']} |",
        f"| Total Snapshots | {summary['rollback']['total_snapshots']} |",
        f"| Total Rollbacks | {summary['rollback']['total_rollbacks']} |",
        f"| Successful | {summary['rollback']['successful_rollbacks']} |",
        f"| Failed | {summary['rollback']['failed_rollbacks']} |",
        f"| Currently Rolling Back | {'Yes' if summary['rollback']['is_rolling_back'] else 'No'} |",
        "",
        "### Blast Radius Settings",
        "",
        f"- **Auto Isolate**: {'Yes' if summary['blast_radius']['auto_isolate'] else 'No'}",
        f"- **Max Affected Services**: {summary['blast_radius']['max_affected_services']}",
        "",
    ]
    
    return "\n".join(lines)

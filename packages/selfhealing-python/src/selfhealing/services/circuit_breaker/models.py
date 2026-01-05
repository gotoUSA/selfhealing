"""
Circuit Breaker Advanced Protection Models

데이터 모델 정의 - Phase 0.1
Reference: docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md

이 모듈은 Circuit Breaker 고급 보호 시스템의 모든 데이터 모델을 정의합니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


# =============================================================================
# Service Configuration
# =============================================================================


@dataclass
class ServiceConfig:
    """
    서비스 설정 - 사용자가 criticality를 직접 지정.
    
    Attributes:
        service_id: 서비스 고유 식별자
        criticality: 중요도 레벨 ("critical" | "high" | "medium" | "low")
        shed_priority: Load Shedding 우선순위 (높을수록 먼저 차단, 0=절대 차단 안 함)
        min_traffic_percentage: 최소 보장 트래픽 (0~100%)
        recovery_strategy: 서비스별 Recovery 전략 오버라이드
        failure_threshold: 서비스별 CB 실패 임계값 오버라이드
        window_seconds: 서비스별 CB 관찰 윈도우 오버라이드
        
    Example:
        >>> config = ServiceConfig(
        ...     service_id="payment-api",
        ...     criticality="critical",
        ...     shed_priority=0,  # 절대 차단 안 함
        ... )
    """
    
    service_id: str
    
    # Criticality 레벨 (사용자 지정 필수)
    criticality: str  # "critical" | "high" | "medium" | "low"
    
    # Load Shedding 우선순위 (높을수록 먼저 차단, 0=절대 차단 안 함)
    shed_priority: int = 0
    
    # 최소 보장 트래픽 (0~100%)
    min_traffic_percentage: float = 0.0
    
    # 서비스별 Recovery 전략 오버라이드
    recovery_strategy: Optional[RecoveryStrategy] = None
    
    # 서비스별 CB 설정 오버라이드
    failure_threshold: Optional[int] = None
    window_seconds: Optional[int] = None
    
    def __post_init__(self) -> None:
        """Validate criticality value."""
        valid_levels = {"critical", "high", "medium", "low"}
        if self.criticality not in valid_levels:
            raise ValueError(
                f"Invalid criticality: {self.criticality}. "
                f"Valid values: {valid_levels}"
            )
        if not (0.0 <= self.min_traffic_percentage <= 100.0):
            raise ValueError(
                f"min_traffic_percentage must be between 0 and 100, "
                f"got {self.min_traffic_percentage}"
            )
        if self.shed_priority < 0:
            raise ValueError(
                f"shed_priority must be non-negative, got {self.shed_priority}"
            )


# =============================================================================
# Load Shedding
# =============================================================================


@dataclass
class SheddingLevel:
    """
    개별 Shedding 단계.
    
    Attributes:
        error_rate: critical 서비스 에러율 임계값
        shed_criticality: 차단 대상 criticality 목록
        traffic_limit: 허용 트래픽 % (0=완전 차단, 100=제한 없음)
        description: 단계 설명
    """
    
    error_rate: float              # critical 서비스 에러율 임계값
    shed_criticality: List[str]    # 차단 대상 criticality 목록
    traffic_limit: float           # 허용 트래픽 % (0=완전 차단, 100=제한 없음)
    description: str = ""          # 단계 설명
    
    def __post_init__(self) -> None:
        """Validate shedding level values."""
        if not (0.0 <= self.error_rate <= 100.0):
            raise ValueError(f"error_rate must be between 0 and 100, got {self.error_rate}")
        if not (0.0 <= self.traffic_limit <= 100.0):
            raise ValueError(f"traffic_limit must be between 0 and 100, got {self.traffic_limit}")
        # critical은 절대 차단 대상에 포함될 수 없음
        if "critical" in self.shed_criticality:
            raise ValueError("'critical' cannot be included in shed_criticality")


@dataclass
class LoadSheddingPolicy:
    """
    Load Shedding 정책.
    
    핵심 서비스에 장애 조짐이 보이면, 비핵심 서비스 트래픽을 먼저 제한하여
    핵심 서비스에 리소스를 집중시킵니다.
    
    Attributes:
        enabled: Load Shedding 활성화 여부
        trigger_threshold: critical 서비스의 에러율이 이 값 초과 시 shedding 시작
        levels: 단계별 차단 정책 (기본 3단계, 확장 가능)
    """
    
    enabled: bool = True
    
    # 트리거 조건: critical 서비스의 에러율이 이 값 초과 시 shedding 시작
    trigger_threshold: float = 30.0
    
    # 단계별 차단 정책 (기본 3단계, 확장 가능)
    levels: List[SheddingLevel] = field(default_factory=lambda: [
        SheddingLevel(
            error_rate=30.0,
            shed_criticality=["low"],
            traffic_limit=50.0,
            description="Level 1: low criticality 50% 제한"
        ),
        SheddingLevel(
            error_rate=50.0,
            shed_criticality=["low", "medium"],
            traffic_limit=20.0,
            description="Level 2: low+medium 80% 제한"
        ),
        SheddingLevel(
            error_rate=70.0,
            shed_criticality=["low", "medium"],
            traffic_limit=0.0,
            description="Level 3: low+medium 완전 차단"
        ),
    ])


# =============================================================================
# Canary Recovery
# =============================================================================


@dataclass
class CanaryStage:
    """
    개별 Canary 단계.
    
    Attributes:
        traffic_percent: 허용 트래픽 비율 (0~100)
        duration_seconds: 이 단계 유지 시간 (0=즉시 다음 단계)
        required_success_rate: 다음 단계로 가려면 필요한 성공률
        description: 단계 설명
    """
    
    traffic_percent: float        # 허용 트래픽 비율 (0~100)
    duration_seconds: int         # 이 단계 유지 시간 (0=즉시 다음 단계)
    required_success_rate: float  # 다음 단계로 가려면 필요한 성공률
    description: str = ""         # 단계 설명
    
    def __post_init__(self) -> None:
        """Validate canary stage values."""
        if not (0.0 <= self.traffic_percent <= 100.0):
            raise ValueError(
                f"traffic_percent must be between 0 and 100, got {self.traffic_percent}"
            )
        if self.duration_seconds < 0:
            raise ValueError(
                f"duration_seconds must be non-negative, got {self.duration_seconds}"
            )
        if not (0.0 <= self.required_success_rate <= 100.0):
            raise ValueError(
                f"required_success_rate must be between 0 and 100, "
                f"got {self.required_success_rate}"
            )


@dataclass
class RecoveryStrategy:
    """
    HALF_OPEN → CLOSED 복구 전략.
    
    HALF_OPEN 상태에서 즉시 100% 트래픽을 보내는 대신,
    점진적으로 트래픽을 늘려 Thundering Herd를 방지합니다.
    
    Attributes:
        type: 전략 타입 ("immediate" | "canary")
        canary_stages: Canary 단계 설정 (기본 4단계)
        on_stage_failure: 단계 실패 시 동작 ("restart" | "abort")
        strict_mode: 결제 등 핵심 서비스용 엄격 모드 (100% 성공률 요구)
    """
    
    # 전략 타입
    type: str = "canary"  # "immediate" | "canary"
    
    # Canary 단계 설정 (기본 4단계)
    canary_stages: List[CanaryStage] = field(default_factory=lambda: [
        CanaryStage(
            traffic_percent=10.0,
            duration_seconds=5,
            required_success_rate=95.0,
            description="Stage 1: 10% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=30.0,
            duration_seconds=5,
            required_success_rate=95.0,
            description="Stage 2: 30% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=60.0,
            duration_seconds=5,
            required_success_rate=90.0,
            description="Stage 3: 60% 트래픽으로 5초간 관찰"
        ),
        CanaryStage(
            traffic_percent=100.0,
            duration_seconds=0,
            required_success_rate=90.0,
            description="Stage 4: 완전 복구"
        ),
    ])
    
    # 단계 실패 시 동작
    on_stage_failure: str = "restart"  # "restart" | "abort"
    
    # 결제 등 핵심 서비스용 엄격 모드
    strict_mode: bool = False  # True면 모든 단계 100% 성공률 요구
    
    def __post_init__(self) -> None:
        """Validate recovery strategy values."""
        valid_types = {"immediate", "canary"}
        if self.type not in valid_types:
            raise ValueError(f"Invalid type: {self.type}. Valid values: {valid_types}")
        
        valid_failure_actions = {"restart", "abort"}
        if self.on_stage_failure not in valid_failure_actions:
            raise ValueError(
                f"Invalid on_stage_failure: {self.on_stage_failure}. "
                f"Valid values: {valid_failure_actions}"
            )


# =============================================================================
# Adaptive Threshold
# =============================================================================


@dataclass
class ThresholdMultiplier:
    """
    임계값 배율.
    
    Attributes:
        failure: 실패 횟수 배율
        window: 관찰 윈도우 배율
        description: 배율 설명
    """
    
    failure: float   # 실패 횟수 배율
    window: float    # 관찰 윈도우 배율
    description: str = ""
    
    def __post_init__(self) -> None:
        """Validate multiplier values."""
        if self.failure < 0:
            raise ValueError(f"failure multiplier must be non-negative, got {self.failure}")
        if self.window < 0:
            raise ValueError(f"window multiplier must be non-negative, got {self.window}")


@dataclass
class AdaptiveThresholdPolicy:
    """
    Emergency Level에 따른 CB 임계값 자동 조정.
    
    시스템 Emergency Level에 따라 CB 임계값을 자동으로 조정합니다.
    위기 상황일수록 더 보수적(느슨하게) 설정하여 자가 유도 블랙아웃을 방지합니다.
    
    Attributes:
        enabled: Adaptive Threshold 활성화 여부
        base_failure_threshold: 기본 실패 횟수 임계값
        base_window_seconds: 기본 관찰 윈도우
        level_multipliers: Emergency Level별 배율
    """
    
    enabled: bool = True
    
    # 기본값
    base_failure_threshold: int = 5      # 기본 실패 횟수 임계값
    base_window_seconds: int = 60        # 기본 관찰 윈도우
    
    # Emergency Level별 배율
    level_multipliers: Dict[str, ThresholdMultiplier] = field(default_factory=lambda: {
        "NORMAL": ThresholdMultiplier(
            failure=1.0, 
            window=1.0,
            description="정상: 5회/60초"
        ),
        "ELEVATED": ThresholdMultiplier(
            failure=1.5, 
            window=1.5,
            description="주의: 7.5회/90초"
        ),
        "HIGH": ThresholdMultiplier(
            failure=2.0, 
            window=2.0,
            description="경고: 10회/120초"
        ),
        "CRITICAL": ThresholdMultiplier(
            failure=3.0, 
            window=3.0,
            description="위험: 15회/180초"
        ),
        "LOCKDOWN": ThresholdMultiplier(
            failure=float('inf'),  # 사실상 OPEN 금지
            window=float('inf'),
            description="잠금: 자동 OPEN 금지"
        ),
    })
    
    def get_adjusted_threshold(self, emergency_level: str) -> tuple[float, float]:
        """
        Emergency Level에 맞는 조정된 임계값 반환.
        
        Args:
            emergency_level: 현재 Emergency Level
            
        Returns:
            tuple[float, float]: (조정된 실패 임계값, 조정된 윈도우 초)
        """
        multiplier = self.level_multipliers.get(
            emergency_level, 
            self.level_multipliers["NORMAL"]
        )
        return (
            self.base_failure_threshold * multiplier.failure,
            self.base_window_seconds * multiplier.window
        )


# =============================================================================
# Open Strategy
# =============================================================================


@dataclass
class OpenStrategy:
    """
    CB OPEN 전략.
    
    Attributes:
        type: 전략 타입 ("immediate" | "graceful")
        drain_timeout_seconds: 진행중 요청 대기 최대 시간 (Graceful 전용)
        force_after_timeout: timeout 후 강제 OPEN (Graceful 전용)
        
    Note:
        Delayed 전략(N초 후 차단)은 안티패턴으로 지원하지 않습니다.
        - N초간 장애 서버에 요청 지속 → Thread 점유, 커넥션 풀 고갈
        - Cascading Failure의 주범
    """
    
    type: str = "immediate"  # "immediate" | "graceful"
    
    # Graceful 전용 설정
    drain_timeout_seconds: int = 30  # 진행중 요청 대기 최대 시간
    
    # Graceful 실패 시 fallback
    force_after_timeout: bool = True  # timeout 후 강제 OPEN
    
    def __post_init__(self) -> None:
        """Validate open strategy values."""
        valid_types = {"immediate", "graceful"}
        if self.type not in valid_types:
            raise ValueError(f"Invalid type: {self.type}. Valid values: {valid_types}")
        if self.drain_timeout_seconds < 0:
            raise ValueError(
                f"drain_timeout_seconds must be non-negative, "
                f"got {self.drain_timeout_seconds}"
            )


# =============================================================================
# Integrated Configuration
# =============================================================================


@dataclass
class CircuitBreakerAdvancedConfig:
    """
    Circuit Breaker 고급 보호 설정.
    
    모든 고급 보호 기능의 통합 설정을 관리합니다.
    
    Attributes:
        services: 서비스 등록 목록 (사용자 필수 설정)
        load_shedding: Load Shedding 정책
        adaptive_threshold: Adaptive Threshold 정책
        default_recovery: 기본 Recovery 전략
        default_open_strategy: 기본 Open 전략
        blast_radius_integration: Blast Radius 연동 활성화
        blast_radius_block_on_critical: CRITICAL 시 자동 OPEN 차단
        freeze_on_lockdown: LOCKDOWN 시 Freeze Mode 활성화
        allow_manual_override_in_lockdown: LOCKDOWN 중 수동 조작 허용
    """
    
    # 서비스 등록 (사용자 필수 설정)
    services: List[ServiceConfig] = field(default_factory=list)
    
    # Load Shedding 정책
    load_shedding: LoadSheddingPolicy = field(default_factory=LoadSheddingPolicy)
    
    # Adaptive Threshold 정책
    adaptive_threshold: AdaptiveThresholdPolicy = field(default_factory=AdaptiveThresholdPolicy)
    
    # 기본 Recovery 전략
    default_recovery: RecoveryStrategy = field(default_factory=RecoveryStrategy)
    
    # 기본 Open 전략
    default_open_strategy: OpenStrategy = field(default_factory=OpenStrategy)
    
    # Blast Radius 연동
    blast_radius_integration: bool = True
    blast_radius_block_on_critical: bool = True
    
    # Freeze Mode 설정
    freeze_on_lockdown: bool = True
    allow_manual_override_in_lockdown: bool = True
    
    def get_service_config(self, service_id: str) -> Optional[ServiceConfig]:
        """
        서비스 ID로 설정 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            ServiceConfig or None if not found
        """
        for service in self.services:
            if service.service_id == service_id:
                return service
        return None
    
    def get_services_by_criticality(self, criticality: str) -> List[ServiceConfig]:
        """
        criticality로 서비스 목록 조회.
        
        Args:
            criticality: 중요도 레벨
            
        Returns:
            해당 criticality의 서비스 목록
        """
        return [s for s in self.services if s.criticality == criticality]
    
    def get_shedding_targets(self, shed_criticality: List[str]) -> List[ServiceConfig]:
        """
        Load Shedding 대상 서비스 목록 조회.
        
        Args:
            shed_criticality: 차단 대상 criticality 목록
            
        Returns:
            차단 대상 서비스 목록 (shed_priority 순 정렬)
        """
        targets = [
            s for s in self.services 
            if s.criticality in shed_criticality and s.shed_priority > 0
        ]
        return sorted(targets, key=lambda s: s.shed_priority, reverse=True)


# =============================================================================
# Panic Threshold Configuration
# =============================================================================


@dataclass
class PanicThresholdConfig:
    """
    Panic Threshold 설정.
    
    전체 CB 중 70% 이상이 OPEN 상태이면 시스템 전체 붕괴로 판단하고
    자동 OPEN을 금지합니다.
    
    Attributes:
        enabled: Panic Threshold 활성화 여부
        threshold_percent: OPEN CB 비율 임계값 (기본 70%)
        action: 임계값 초과 시 동작 ("freeze" | "alert_only")
    """
    
    enabled: bool = True
    threshold_percent: float = 70.0  # 70% 이상 OPEN이면 Panic
    action: str = "freeze"  # "freeze" | "alert_only"
    
    def __post_init__(self) -> None:
        """Validate panic threshold values."""
        if not (0.0 <= self.threshold_percent <= 100.0):
            raise ValueError(
                f"threshold_percent must be between 0 and 100, "
                f"got {self.threshold_percent}"
            )
        valid_actions = {"freeze", "alert_only"}
        if self.action not in valid_actions:
            raise ValueError(
                f"Invalid action: {self.action}. Valid values: {valid_actions}"
            )


# =============================================================================
# Freeze Mode State
# =============================================================================


@dataclass
class FreezeModeState:
    """
    Freeze Mode 상태.
    
    LOCKDOWN 상태에서 현재 CB 상태를 그대로 동결합니다.
    
    Attributes:
        active: Freeze Mode 활성화 여부
        activated_at: 활성화 시간 (ISO format)
        reason: 활성화 사유
        activated_by: 활성화 주체 ("system" | "operator:username")
    """
    
    active: bool = False
    activated_at: Optional[str] = None  # ISO format timestamp
    reason: str = ""
    activated_by: str = ""  # "system" or "operator:<username>"

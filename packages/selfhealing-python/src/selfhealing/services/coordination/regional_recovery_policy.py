"""
Regional Recovery Policy.

네임스페이스(리전)별로 복구 정책을 차별화합니다.

Features:
- 리전별 복구 단계 커스터마이징
- 리전별 안정화 대기 시간 설정
- 수동 승인 필수 여부 설정
- 리전별 에러율 임계값 조정

Code reference:
    policy_engine.py (CoordinationPolicy 패턴)
    anti_flapping.py (리전별 설정 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.2
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [26] RegionalRecoveryPolicySettings 참조.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import structlog

from selfhealing.settings.regional_recovery_policy import (
    get_regional_recovery_policy_settings,
)

from .recovery_state import RecoveryStep, RecoveryStepType

logger = structlog.get_logger()


def _get_settings():
    """Get RegionalRecoveryPolicySettings."""
    return get_regional_recovery_policy_settings()


@dataclass
class RegionalRecoveryConfig:
    """
    리전별 복구 설정.

    각 네임스페이스(리전)의 중요도와 특성에 따라
    복구 정책을 차별화합니다.

    Code reference:
        policy_engine.py (CoordinationPolicy 패턴)
    """

    # 기본 정보
    namespace: str = ""
    """네임스페이스 (예: "seoul", "tokyo", "global")."""

    description: str = ""
    """리전 설명."""

    # 안정화 대기 설정
    stability_check_duration_minutes: int = 10
    """안정화 확인에 필요한 시간 (분)."""

    error_rate_threshold: float = 0.10
    """허용 에러율 임계값 (10% = 0.10)."""

    success_rate_threshold: float = 0.95
    """필요 성공률 임계값 (95% = 0.95)."""

    # 복구 단계 설정
    include_governance_normal: bool = True
    """GOVERNANCE_NORMAL 단계 포함 여부."""

    include_canary_resume: bool = True
    """CANARY_RESUME 단계 포함 여부."""

    health_check_wait_after_seconds: int = 0
    """HEALTH_CHECK 완료 후 대기 시간 (초)."""

    canary_resume_wait_after_seconds: int = 60
    """CANARY_RESUME 완료 후 대기 시간 (초)."""

    governance_normal_wait_after_seconds: int = 300
    """GOVERNANCE_NORMAL 완료 후 대기 시간 (초)."""

    # 승인 설정
    require_manual_approval: bool = False
    """
    수동 승인 필수 여부.

    True면 자동 복구 조건 충족 후에도 READY_TO_RESTORE 상태로 대기.
    중요 리전(결제 등)에서 사용.
    """

    approval_timeout_minutes: int = 60
    """수동 승인 타임아웃 (분). 초과 시 알림 재발송."""

    approval_escalation_intervals: list[int] = field(default_factory=lambda: [15, 30, 60])  # 15분, 30분, 60분에 알림
    """승인 대기 시 알림 발송 간격 (분)."""

    # 우선순위
    priority: int = 0
    """
    복구 우선순위.

    숫자가 높을수록 먼저 복구.
    예: payment(100) > order(50) > analytics(10)
    """

    # 메타데이터
    metadata: dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "namespace": self.namespace,
            "description": self.description,
            "stability_check_duration_minutes": self.stability_check_duration_minutes,
            "error_rate_threshold": self.error_rate_threshold,
            "success_rate_threshold": self.success_rate_threshold,
            "include_governance_normal": self.include_governance_normal,
            "include_canary_resume": self.include_canary_resume,
            "health_check_wait_after_seconds": self.health_check_wait_after_seconds,
            "canary_resume_wait_after_seconds": self.canary_resume_wait_after_seconds,
            "governance_normal_wait_after_seconds": self.governance_normal_wait_after_seconds,
            "require_manual_approval": self.require_manual_approval,
            "approval_timeout_minutes": self.approval_timeout_minutes,
            "approval_escalation_intervals": self.approval_escalation_intervals,
            "priority": self.priority,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RegionalRecoveryConfig:
        """딕셔너리에서 생성."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_settings(cls, namespace: str = "global", **overrides) -> RegionalRecoveryConfig:
        """
        RegionalRecoveryPolicySettings에서 기본값을 가져와 생성.

        Args:
            namespace: 네임스페이스 이름
            **overrides: 기본값을 덮어쓸 설정

        Returns:
            RegionalRecoveryConfig with defaults from settings
        """
        settings = _get_settings()
        return cls(
            namespace=namespace,
            stability_check_duration_minutes=overrides.get(
                "stability_check_duration_minutes",
                settings.stability_check_duration_minutes,
            ),
            error_rate_threshold=overrides.get("error_rate_threshold", settings.error_rate_threshold),
            success_rate_threshold=overrides.get("success_rate_threshold", settings.success_rate_threshold),
            approval_timeout_minutes=overrides.get("approval_timeout_minutes", settings.approval_timeout_minutes),
            approval_escalation_intervals=overrides.get(
                "approval_escalation_intervals",
                [
                    settings.escalation_interval_1,
                    settings.escalation_interval_2,
                    settings.escalation_interval_3,
                ],
            ),
            **{
                k: v
                for k, v in overrides.items()
                if k
                not in [
                    "stability_check_duration_minutes",
                    "error_rate_threshold",
                    "success_rate_threshold",
                    "approval_timeout_minutes",
                    "approval_escalation_intervals",
                ]
            },
        )


# =============================================================================
# 기본 리전별 설정 (settings 기반 동적 생성)
# =============================================================================


def _build_default_regional_configs() -> dict[str, RegionalRecoveryConfig]:
    """
    settings에서 기본값을 가져와 리전별 설정 생성.

    Returns:
        리전별 RegionalRecoveryConfig 딕셔너리
    """
    settings = _get_settings()

    return {
        # 서울 리전: 결제 비중이 커서 보수적 (CRITICAL)
        "seoul": RegionalRecoveryConfig(
            namespace="seoul",
            description="Seoul region - payment heavy, conservative recovery",
            stability_check_duration_minutes=10,  # CRITICAL은 더 긴 안정화
            error_rate_threshold=0.05,  # 더 엄격한 5%
            success_rate_threshold=0.98,  # 더 높은 98%
            require_manual_approval=True,  # 수동 승인 필수
            approval_timeout_minutes=30,
            approval_escalation_intervals=settings.get_escalation_intervals(),
            priority=100,  # 최우선
        ),
        # 도쿄 리전: 중간 중요도 (HIGH)
        "tokyo": RegionalRecoveryConfig(
            namespace="tokyo",
            description="Tokyo region - standard recovery",
            stability_check_duration_minutes=7,
            error_rate_threshold=settings.error_rate_threshold,
            success_rate_threshold=settings.success_rate_threshold,
            require_manual_approval=False,
            approval_timeout_minutes=settings.approval_timeout_minutes,
            approval_escalation_intervals=settings.get_escalation_intervals(),
            priority=50,
        ),
        # 오레곤 리전: 분석 위주, 느슨한 정책 (MEDIUM)
        "oregon": RegionalRecoveryConfig(
            namespace="oregon",
            description="Oregon region - analytics, relaxed recovery",
            stability_check_duration_minutes=5,
            error_rate_threshold=0.15,  # 더 느슨한 15%
            success_rate_threshold=0.90,  # 더 낮은 90%
            require_manual_approval=False,
            approval_timeout_minutes=settings.approval_timeout_minutes,
            approval_escalation_intervals=settings.get_escalation_intervals(),
            priority=10,
        ),
        # 글로벌: 기본 설정 (LOW)
        "global": RegionalRecoveryConfig.from_settings(
            namespace="global",
            description="Global namespace - default recovery",
            priority=0,
        ),
    }


# 초기화 시 사용할 기본 설정 (lazy loading)
_default_regional_configs: dict[str, RegionalRecoveryConfig] | None = None


def get_default_regional_configs() -> dict[str, RegionalRecoveryConfig]:
    """
    기본 리전별 설정 반환 (캐시됨).

    Returns:
        리전별 RegionalRecoveryConfig 딕셔너리
    """
    global _default_regional_configs
    if _default_regional_configs is None:
        _default_regional_configs = _build_default_regional_configs()
    return _default_regional_configs


def reset_default_regional_configs() -> None:
    """캐시 초기화 (테스트용)."""
    global _default_regional_configs
    _default_regional_configs = None


# 레거시 호환용 (deprecated, use get_default_regional_configs instead)
DEFAULT_REGIONAL_CONFIGS: dict[str, RegionalRecoveryConfig] = {
    "seoul": RegionalRecoveryConfig(
        namespace="seoul",
        description="Seoul region - payment heavy, conservative recovery",
        stability_check_duration_minutes=10,
        error_rate_threshold=0.05,
        success_rate_threshold=0.98,
        require_manual_approval=True,
        approval_timeout_minutes=30,
        priority=100,
    ),
    "tokyo": RegionalRecoveryConfig(
        namespace="tokyo",
        description="Tokyo region - standard recovery",
        stability_check_duration_minutes=7,
        error_rate_threshold=0.10,
        success_rate_threshold=0.95,
        require_manual_approval=False,
        priority=50,
    ),
    "oregon": RegionalRecoveryConfig(
        namespace="oregon",
        description="Oregon region - analytics, relaxed recovery",
        stability_check_duration_minutes=5,
        error_rate_threshold=0.15,
        success_rate_threshold=0.90,
        require_manual_approval=False,
        priority=10,
    ),
    "global": RegionalRecoveryConfig(
        namespace="global",
        description="Global namespace - default recovery",
        stability_check_duration_minutes=10,
        error_rate_threshold=0.10,
        success_rate_threshold=0.95,
        require_manual_approval=False,
        priority=0,
    ),
}


class RegionalRecoveryPolicyEngine:
    """
    리전별 복구 정책 엔진.

    네임스페이스에 따라 적절한 복구 정책을 반환하고,
    해당 정책에 맞는 복구 단계를 생성합니다.

    Usage:
        engine = RegionalRecoveryPolicyEngine()

        # 리전별 설정 조회
        config = engine.get_config("seoul")

        # 리전별 복구 단계 생성
        steps = engine.get_recovery_steps("seoul", "LEVEL_3")

        # 커스텀 설정 등록
        engine.register_config(RegionalRecoveryConfig(
            namespace="singapore",
            stability_check_duration_minutes=8,
            require_manual_approval=True,
        ))

    Reference:
        77_RECOVERY_COORDINATOR.md#8.2
    """

    def __init__(
        self,
        default_configs: dict[str, RegionalRecoveryConfig] | None = None,
    ):
        """
        Args:
            default_configs: 초기 리전별 설정 (없으면 settings 기반 기본값 사용)
        """
        self._configs: dict[str, RegionalRecoveryConfig] = dict(
            default_configs if default_configs is not None else get_default_regional_configs()
        )
        self._lock = threading.RLock()

    def get_config(self, namespace: str) -> RegionalRecoveryConfig:
        """
        리전별 설정 조회.

        등록되지 않은 네임스페이스는 "global" 설정을 사용합니다.

        Args:
            namespace: 네임스페이스

        Returns:
            해당 네임스페이스의 복구 설정
        """
        with self._lock:
            if namespace in self._configs:
                return self._configs[namespace]

            # 폴백: global 설정 사용
            if "global" in self._configs:
                logger.debug(
                    "regional_recovery_policy.using_global_config_unknown",
                    namespace=namespace,
                )
                return self._configs["global"]

            # 기본 설정 반환
            return RegionalRecoveryConfig(namespace=namespace)

    def register_config(self, config: RegionalRecoveryConfig) -> None:
        """
        리전별 설정 등록.

        Args:
            config: 복구 설정
        """
        with self._lock:
            self._configs[config.namespace] = config
            logger.info(
                "cell_registry.bulkheads_registered",
                recovery_policy_namespace=config.namespace,
            )

    def remove_config(self, namespace: str) -> bool:
        """
        리전별 설정 제거.

        Args:
            namespace: 네임스페이스

        Returns:
            제거 성공 여부
        """
        with self._lock:
            if namespace in self._configs:
                del self._configs[namespace]
                logger.info(
                    "regional_recovery_policy.removed_config",
                    namespace=namespace,
                )
                return True
            return False

    def list_configs(self) -> list[RegionalRecoveryConfig]:
        """
        모든 설정 목록 조회.

        Returns:
            모든 리전별 설정 목록 (우선순위 순)
        """
        with self._lock:
            configs = list(self._configs.values())
            # 우선순위 내림차순 정렬
            configs.sort(key=lambda c: c.priority, reverse=True)
            return configs

    def get_recovery_steps(
        self,
        namespace: str,
        trigger_level: str,
    ) -> list[RecoveryStep]:
        """
        리전별 복구 단계 생성.

        해당 네임스페이스의 설정에 따라 커스터마이즈된 복구 단계를 생성합니다.

        Args:
            namespace: 네임스페이스
            trigger_level: 복구 대상 Emergency 레벨

        Returns:
            복구 단계 목록
        """
        config = self.get_config(namespace)
        steps: list[RecoveryStep] = []
        order = 1

        # Step 1: BUDGET_RESET (항상 포함)
        steps.append(
            RecoveryStep(
                step_type=RecoveryStepType.BUDGET_RESET,
                order=order,
                wait_after_seconds=0,
                params={
                    "target_multiplier": 1.0,
                    "namespace": namespace,
                },
            )
        )
        order += 1

        # Step 2: HEALTH_CHECK (항상 포함)
        steps.append(
            RecoveryStep(
                step_type=RecoveryStepType.HEALTH_CHECK,
                order=order,
                wait_after_seconds=config.health_check_wait_after_seconds,
                params={
                    "duration_minutes": config.stability_check_duration_minutes,
                    "error_rate_threshold": config.error_rate_threshold,
                    "success_threshold": config.success_rate_threshold,
                },
            )
        )
        order += 1

        # Step 3: CANARY_RESUME (옵션)
        if config.include_canary_resume:
            steps.append(
                RecoveryStep(
                    step_type=RecoveryStepType.CANARY_RESUME,
                    order=order,
                    wait_after_seconds=config.canary_resume_wait_after_seconds,
                    params={
                        "resume_paused_only": True,
                    },
                )
            )
            order += 1

        # Step 4: GOVERNANCE_NORMAL (옵션, LEVEL_3만)
        if config.include_governance_normal and trigger_level == "LEVEL_3":
            steps.append(
                RecoveryStep(
                    step_type=RecoveryStepType.GOVERNANCE_NORMAL,
                    order=order,
                    wait_after_seconds=config.governance_normal_wait_after_seconds,
                    params={
                        "reason": f"[AUTO-RECOVERY] Stability confirmed for {namespace}",
                    },
                )
            )
            order += 1

        return steps

    def should_require_approval(self, namespace: str) -> bool:
        """
        수동 승인 필수 여부 확인.

        Args:
            namespace: 네임스페이스

        Returns:
            수동 승인 필수 여부
        """
        config = self.get_config(namespace)
        return config.require_manual_approval

    def get_approval_timeout(self, namespace: str) -> int:
        """
        승인 타임아웃 조회 (분).

        Args:
            namespace: 네임스페이스

        Returns:
            승인 타임아웃 (분)
        """
        config = self.get_config(namespace)
        return config.approval_timeout_minutes

    def get_namespaces_by_priority(self) -> list[str]:
        """
        우선순위 순으로 네임스페이스 목록 반환.

        Returns:
            우선순위 내림차순 네임스페이스 목록
        """
        configs = self.list_configs()
        return [c.namespace for c in configs]


# =============================================================================
# Singleton
# =============================================================================

_policy_engine: RegionalRecoveryPolicyEngine | None = None
_engine_lock = threading.Lock()


def get_regional_recovery_policy_engine() -> RegionalRecoveryPolicyEngine:
    """RegionalRecoveryPolicyEngine 싱글톤 반환."""
    global _policy_engine

    if _policy_engine is not None:
        return _policy_engine

    with _engine_lock:
        if _policy_engine is None:
            _policy_engine = RegionalRecoveryPolicyEngine()
        return _policy_engine


def reset_regional_recovery_policy_engine() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _policy_engine
    with _engine_lock:
        _policy_engine = None

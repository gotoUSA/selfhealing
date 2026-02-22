"""
Request-level Canary Feature Flag.

단일 클러스터 내에서 일부 요청에만 Canary 설정을 적용하는 기능.

설계 원칙:
- Self-Healing은 "설정 변경"만 다룹니다
- 서비스 배포 카나리(Pod v1→v2)는 Kubernetes/서비스 메시 영역
- 이 모듈은 동일 클러스터 내에서 트래픽 일부에 다른 설정을 적용

Use Cases:
- 새 Circuit Breaker 임계값을 10% 트래픽에만 먼저 적용
- 특정 사용자 그룹에게만 새 설정 적용 (A/B 테스트)
- 점진적으로 설정 적용 비율을 높이며 모니터링

Reference: docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md (Step 6)

Usage:
    from selfhealing.services.canary.feature_flag import (
        CanaryFeatureFlag,
        get_canary_feature_flag,
    )

    # 요청 컨텍스트에서 Canary 설정 적용 여부 판단
    feature_flag = get_canary_feature_flag()

    if feature_flag.should_use_canary_config(request, "circuit_breaker"):
        config = canary_config
    else:
        config = baseline_config

    # 또는 자동으로 효과적인 설정 가져오기
    effective_config = feature_flag.get_effective_config(
        request=request,
        config_type="circuit_breaker",
        baseline_config=baseline_config,
        canary_config=canary_config,
    )
"""

from __future__ import annotations

import hashlib
import structlog
import os
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from selfhealing.utils.time import utc_now

if TYPE_CHECKING:
    from django.http import HttpRequest

logger = structlog.get_logger()


# =============================================================================
# Enums and Data Classes
# =============================================================================


class CanarySelectionStrategy(str, Enum):
    """
    Canary 선택 전략.

    요청이 Canary 설정을 받을지 결정하는 방법.
    """

    RANDOM = "random"  # 무작위 확률 기반
    USER_ID_HASH = "user_id_hash"  # 사용자 ID 해시 기반 (일관성)
    HEADER_BASED = "header_based"  # 특정 헤더 기반
    IP_HASH = "ip_hash"  # IP 주소 해시 기반
    WHITELIST = "whitelist"  # 화이트리스트 기반


@dataclass
class CanaryFlagConfig:
    """
    Canary Feature Flag 설정.

    Attributes:
        config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
        enabled: 활성화 여부
        percentage: Canary 적용 비율 (0-100)
        strategy: 선택 전략
        rollout_id: 관련 Canary Rollout ID
        whitelist_user_ids: 화이트리스트 사용자 ID 목록
        whitelist_ips: 화이트리스트 IP 목록
        canary_header: Canary 요청 식별 헤더 (HEADER_BASED 전략용)
        baseline_config: 기준 설정값
        canary_config: Canary 설정값
        created_at: 생성 시간
        expires_at: 만료 시간 (선택)
    """

    config_type: str
    enabled: bool = True
    percentage: float = 10.0  # 10% 기본값
    strategy: CanarySelectionStrategy = CanarySelectionStrategy.USER_ID_HASH
    rollout_id: str | None = None
    whitelist_user_ids: set[str] = field(default_factory=set)
    whitelist_ips: set[str] = field(default_factory=set)
    canary_header: str = "X-Canary-Config"
    baseline_config: dict[str, Any] = field(default_factory=dict)
    canary_config: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None

    def is_expired(self) -> bool:
        """만료 여부 확인."""
        if self.expires_at is None:
            return False
        return utc_now() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "config_type": self.config_type,
            "enabled": self.enabled,
            "percentage": self.percentage,
            "strategy": self.strategy.value,
            "rollout_id": self.rollout_id,
            "whitelist_user_ids": list(self.whitelist_user_ids),
            "whitelist_ips": list(self.whitelist_ips),
            "canary_header": self.canary_header,
            "baseline_config": self.baseline_config,
            "canary_config": self.canary_config,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CanaryFlagConfig:
        """딕셔너리에서 생성."""
        expires_at = data.get("expires_at")
        if isinstance(expires_at, str):
            expires_at = datetime.fromisoformat(expires_at)

        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        else:
            created_at = utc_now()

        return cls(
            config_type=data["config_type"],
            enabled=data.get("enabled", True),
            percentage=data.get("percentage", 10.0),
            strategy=CanarySelectionStrategy(data.get("strategy", "user_id_hash")),
            rollout_id=data.get("rollout_id"),
            whitelist_user_ids=set(data.get("whitelist_user_ids", [])),
            whitelist_ips=set(data.get("whitelist_ips", [])),
            canary_header=data.get("canary_header", "X-Canary-Config"),
            baseline_config=data.get("baseline_config", {}),
            canary_config=data.get("canary_config", {}),
            created_at=created_at,
            expires_at=expires_at,
        )


@dataclass
class CanaryDecision:
    """
    Canary 적용 결정 결과.

    Attributes:
        use_canary: Canary 설정 사용 여부
        reason: 결정 사유
        strategy_used: 사용된 선택 전략
        effective_config: 최종 적용될 설정값
    """

    use_canary: bool
    reason: str
    strategy_used: str
    effective_config: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Request Context Extractors
# =============================================================================


class RequestContextExtractor:
    """
    요청에서 Canary 결정에 필요한 정보를 추출.

    Django HttpRequest에서 사용자 ID, IP 주소, 헤더 등을 추출합니다.
    """

    @staticmethod
    def get_user_id(request: HttpRequest) -> str | None:
        """요청에서 사용자 ID 추출."""
        if hasattr(request, "user") and request.user:
            if hasattr(request.user, "id") and request.user.id:
                return str(request.user.id)
            if hasattr(request.user, "username") and request.user.username:
                return request.user.username
        return None

    @staticmethod
    def get_client_ip(request: HttpRequest) -> str | None:
        """요청에서 클라이언트 IP 추출.

        Fail-Open: IP 추출 실패 시 None 반환.
        호출부 _evaluate_ip_hash는 None일 때 baseline 사용,
        _evaluate_whitelist는 None이면 whitelist 매칭을 건너뜀.
        """
        try:
            from selfhealing.utils.network import extract_client_ip

            return extract_client_ip(request)
        except Exception as e:
            logger.warning(
                "canary_context_extractor.failed_extract_client_ip",
                error=e,
            )
            return None

    @staticmethod
    def get_header(request: HttpRequest, header_name: str) -> str | None:
        """요청에서 특정 헤더 추출."""
        # Django에서 헤더는 HTTP_ 접두사와 대문자로 변환됨
        meta_key = f"HTTP_{header_name.upper().replace('-', '_')}"
        return request.META.get(meta_key)

    @staticmethod
    def get_session_id(request: HttpRequest) -> str | None:
        """요청에서 세션 ID 추출."""
        if hasattr(request, "session") and request.session:
            return request.session.session_key
        return None


# =============================================================================
# Hash Functions
# =============================================================================


def compute_stable_hash(value: str) -> int:
    """
    안정적인 해시 계산 (0-99 범위).

    동일 입력에 대해 항상 동일한 결과를 반환합니다.
    Canary 일관성을 위해 사용됩니다.

    Args:
        value: 해시할 문자열

    Returns:
        0-99 범위의 정수
    """
    # Security Hardening (214_SECURITY_VULNERABILITY_FIXES): MD5 → SHA-256
    hash_bytes = hashlib.sha256(value.encode()).digest()
    hash_int = int.from_bytes(hash_bytes[:4], byteorder="big")
    return hash_int % 100


# =============================================================================
# CanaryFeatureFlag
# =============================================================================


class CanaryFeatureFlag:
    """
    Request-level Canary Feature Flag.

    단일 클러스터 내에서 요청별로 Canary 설정 적용 여부를 결정합니다.

    주요 기능:
    - 확률 기반 Canary 선택 (일관성 보장)
    - 화이트리스트 기반 강제 적용
    - 헤더 기반 수동 오버라이드
    - 설정별 독립적인 Canary 관리

    Example:
        feature_flag = CanaryFeatureFlag()

        # Canary 설정 등록
        feature_flag.register_flag(CanaryFlagConfig(
            config_type="circuit_breaker",
            percentage=10,  # 10% 트래픽
            baseline_config={"failure_threshold": 5},
            canary_config={"failure_threshold": 3},
        ))

        # 요청별 Canary 적용 판단
        decision = feature_flag.evaluate(request, "circuit_breaker")

        if decision.use_canary:
            # Canary 설정 사용
            apply_config(decision.effective_config)
    """

    def __init__(
        self,
        context_extractor: RequestContextExtractor | None = None,
    ):
        """
        CanaryFeatureFlag 초기화.

        Args:
            context_extractor: 요청 컨텍스트 추출기
        """
        self.context_extractor = context_extractor or RequestContextExtractor()
        self._flags: dict[str, CanaryFlagConfig] = {}

    def register_flag(self, config: CanaryFlagConfig) -> None:
        """
        Canary Flag 등록.

        Args:
            config: Canary Flag 설정
        """
        self._flags[config.config_type] = config
        logger.info(
            f"[CanaryFeatureFlag] Registered: {config.config_type} "
            f"(percentage={config.percentage}%, strategy={config.strategy.value})"
        )

    def unregister_flag(self, config_type: str) -> bool:
        """
        Canary Flag 등록 해제.

        Args:
            config_type: 설정 유형

        Returns:
            해제 성공 여부
        """
        if config_type in self._flags:
            del self._flags[config_type]
            logger.info(
                "canary_feature_flag.unregistered",
                config_type=config_type,
            )
            return True
        return False

    def get_flag(self, config_type: str) -> CanaryFlagConfig | None:
        """Canary Flag 조회."""
        return self._flags.get(config_type)

    def list_flags(self) -> list[CanaryFlagConfig]:
        """모든 Canary Flag 목록 반환."""
        return list(self._flags.values())

    def should_use_canary_config(
        self,
        request: HttpRequest,
        config_type: str,
    ) -> bool:
        """
        요청에 Canary 설정을 적용해야 하는지 판단.

        Args:
            request: Django HTTP 요청
            config_type: 설정 유형

        Returns:
            Canary 설정 사용 여부
        """
        decision = self.evaluate(request, config_type)
        return decision.use_canary

    def get_effective_config(
        self,
        request: HttpRequest,
        config_type: str,
        baseline_config: dict[str, Any] | None = None,
        canary_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        요청에 적용될 효과적인 설정 반환.

        Args:
            request: Django HTTP 요청
            config_type: 설정 유형
            baseline_config: 기준 설정 (Flag에 없으면 사용)
            canary_config: Canary 설정 (Flag에 없으면 사용)

        Returns:
            적용될 설정값
        """
        flag = self._flags.get(config_type)

        # Flag가 없으면 baseline 반환
        if not flag:
            return baseline_config or {}

        # Flag의 설정이 있으면 우선 사용
        effective_baseline = flag.baseline_config or baseline_config or {}
        effective_canary = flag.canary_config or canary_config or {}

        decision = self.evaluate(request, config_type)

        if decision.use_canary:
            return effective_canary
        return effective_baseline

    def evaluate(
        self,
        request: HttpRequest,
        config_type: str,
    ) -> CanaryDecision:
        """
        Canary 적용 여부 평가.

        전략에 따라 요청이 Canary 그룹에 속하는지 결정합니다.

        Args:
            request: Django HTTP 요청
            config_type: 설정 유형

        Returns:
            CanaryDecision 결과
        """
        flag = self._flags.get(config_type)

        # Flag가 없음
        if not flag:
            return CanaryDecision(
                use_canary=False,
                reason="no_flag_registered",
                strategy_used="none",
            )

        # 비활성화됨
        if not flag.enabled:
            return CanaryDecision(
                use_canary=False,
                reason="flag_disabled",
                strategy_used="none",
                effective_config=flag.baseline_config,
            )

        # 만료됨
        if flag.is_expired():
            return CanaryDecision(
                use_canary=False,
                reason="flag_expired",
                strategy_used="none",
                effective_config=flag.baseline_config,
            )

        # 전략별 평가
        strategy = flag.strategy

        if strategy == CanarySelectionStrategy.HEADER_BASED:
            return self._evaluate_header_based(request, flag)

        if strategy == CanarySelectionStrategy.WHITELIST:
            return self._evaluate_whitelist(request, flag)

        if strategy == CanarySelectionStrategy.USER_ID_HASH:
            return self._evaluate_user_id_hash(request, flag)

        if strategy == CanarySelectionStrategy.IP_HASH:
            return self._evaluate_ip_hash(request, flag)

        if strategy == CanarySelectionStrategy.RANDOM:
            return self._evaluate_random(flag)

        # 기본값
        return CanaryDecision(
            use_canary=False,
            reason="unknown_strategy",
            strategy_used=strategy.value,
            effective_config=flag.baseline_config,
        )

    def _evaluate_header_based(
        self,
        request: HttpRequest,
        flag: CanaryFlagConfig,
    ) -> CanaryDecision:
        """헤더 기반 평가."""
        header_value = self.context_extractor.get_header(request, flag.canary_header)

        use_canary = header_value is not None and header_value.lower() in (
            "true",
            "1",
            "yes",
        )

        return CanaryDecision(
            use_canary=use_canary,
            reason="header_present" if use_canary else "header_absent",
            strategy_used="header_based",
            effective_config=flag.canary_config if use_canary else flag.baseline_config,
        )

    def _evaluate_whitelist(
        self,
        request: HttpRequest,
        flag: CanaryFlagConfig,
    ) -> CanaryDecision:
        """화이트리스트 기반 평가."""
        # 사용자 ID 확인
        user_id = self.context_extractor.get_user_id(request)
        if user_id and user_id in flag.whitelist_user_ids:
            return CanaryDecision(
                use_canary=True,
                reason="user_in_whitelist",
                strategy_used="whitelist",
                effective_config=flag.canary_config,
            )

        # IP 확인
        client_ip = self.context_extractor.get_client_ip(request)
        if client_ip and client_ip in flag.whitelist_ips:
            return CanaryDecision(
                use_canary=True,
                reason="ip_in_whitelist",
                strategy_used="whitelist",
                effective_config=flag.canary_config,
            )

        return CanaryDecision(
            use_canary=False,
            reason="not_in_whitelist",
            strategy_used="whitelist",
            effective_config=flag.baseline_config,
        )

    def _evaluate_user_id_hash(
        self,
        request: HttpRequest,
        flag: CanaryFlagConfig,
    ) -> CanaryDecision:
        """사용자 ID 해시 기반 평가 (일관성 보장)."""
        user_id = self.context_extractor.get_user_id(request)

        if not user_id:
            # 사용자 ID가 없으면 세션 ID나 IP 사용
            user_id = self.context_extractor.get_session_id(request)

        if not user_id:
            user_id = self.context_extractor.get_client_ip(request)

        if not user_id:
            # 식별자 없음 - baseline 사용
            return CanaryDecision(
                use_canary=False,
                reason="no_identifier",
                strategy_used="user_id_hash",
                effective_config=flag.baseline_config,
            )

        # 해시 계산
        hash_input = f"{flag.config_type}:{user_id}"
        hash_value = compute_stable_hash(hash_input)

        use_canary = hash_value < flag.percentage

        return CanaryDecision(
            use_canary=use_canary,
            reason=f"hash={hash_value}, threshold={flag.percentage}",
            strategy_used="user_id_hash",
            effective_config=flag.canary_config if use_canary else flag.baseline_config,
        )

    def _evaluate_ip_hash(
        self,
        request: HttpRequest,
        flag: CanaryFlagConfig,
    ) -> CanaryDecision:
        """IP 해시 기반 평가."""
        client_ip = self.context_extractor.get_client_ip(request)

        if not client_ip:
            return CanaryDecision(
                use_canary=False,
                reason="no_client_ip",
                strategy_used="ip_hash",
                effective_config=flag.baseline_config,
            )

        # 해시 계산
        hash_input = f"{flag.config_type}:{client_ip}"
        hash_value = compute_stable_hash(hash_input)

        use_canary = hash_value < flag.percentage

        return CanaryDecision(
            use_canary=use_canary,
            reason=f"hash={hash_value}, threshold={flag.percentage}",
            strategy_used="ip_hash",
            effective_config=flag.canary_config if use_canary else flag.baseline_config,
        )

    def _evaluate_random(self, flag: CanaryFlagConfig) -> CanaryDecision:
        """무작위 확률 기반 평가 (비일관성)."""
        random_value = random.uniform(0, 100)
        use_canary = random_value < flag.percentage

        return CanaryDecision(
            use_canary=use_canary,
            reason=f"random={random_value:.2f}, threshold={flag.percentage}",
            strategy_used="random",
            effective_config=flag.canary_config if use_canary else flag.baseline_config,
        )

    def update_percentage(self, config_type: str, new_percentage: float) -> bool:
        """
        Canary 적용 비율 업데이트.

        점진적으로 비율을 높이며 모니터링할 때 사용합니다.

        Args:
            config_type: 설정 유형
            new_percentage: 새 비율 (0-100)

        Returns:
            업데이트 성공 여부
        """
        if config_type not in self._flags:
            return False

        if not 0 <= new_percentage <= 100:
            raise ValueError("Percentage must be between 0 and 100")

        old_percentage = self._flags[config_type].percentage
        self._flags[config_type].percentage = new_percentage

        logger.info(
            "canary_feature_flag.updated_percentage",
            config_type=config_type,
            old_percentage=old_percentage,
            new_percentage=new_percentage,
        )

        return True


# =============================================================================
# Django Middleware Integration
# =============================================================================


class CanaryConfigMiddleware:
    """
    Django 미들웨어 - Request-level Canary 설정 적용.

    요청 시작 시 Canary 결정을 수행하고,
    request 객체에 결과를 첨부합니다.

    Usage (settings.py):
        MIDDLEWARE = [
            ...
            'selfhealing.services.canary.feature_flag.CanaryConfigMiddleware',
            ...
        ]

    Access in views:
        def my_view(request):
            canary_decisions = getattr(request, 'canary_decisions', {})
            if canary_decisions.get('circuit_breaker', {}).get('use_canary'):
                # Canary 설정 사용
                pass
    """

    def __init__(self, get_response: Callable):
        """미들웨어 초기화."""
        self.get_response = get_response
        self._feature_flag: CanaryFeatureFlag | None = None

    @property
    def feature_flag(self) -> CanaryFeatureFlag:
        """CanaryFeatureFlag 싱글톤."""
        if self._feature_flag is None:
            self._feature_flag = get_canary_feature_flag()
        return self._feature_flag

    def __call__(self, request: HttpRequest):
        """요청 처리."""
        # Canary 결정 수행
        request.canary_decisions = {}

        for flag in self.feature_flag.list_flags():
            decision = self.feature_flag.evaluate(request, flag.config_type)
            request.canary_decisions[flag.config_type] = {
                "use_canary": decision.use_canary,
                "reason": decision.reason,
                "strategy": decision.strategy_used,
            }

        response = self.get_response(request)

        # 응답 헤더에 Canary 정보 추가 (디버깅용)
        if os.environ.get("CANARY_DEBUG", "").lower() in ("true", "1"):
            for config_type, info in request.canary_decisions.items():
                header_name = f"X-Canary-{config_type}"
                header_value = "true" if info["use_canary"] else "false"
                response[header_name] = header_value

        return response


# =============================================================================
# Singleton Accessor Functions
# =============================================================================

_canary_feature_flag: CanaryFeatureFlag | None = None


def get_canary_feature_flag() -> CanaryFeatureFlag:
    """CanaryFeatureFlag 싱글톤 인스턴스 반환."""
    global _canary_feature_flag
    if _canary_feature_flag is None:
        _canary_feature_flag = CanaryFeatureFlag()
    return _canary_feature_flag


def reset_canary_feature_flag() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _canary_feature_flag
    _canary_feature_flag = None

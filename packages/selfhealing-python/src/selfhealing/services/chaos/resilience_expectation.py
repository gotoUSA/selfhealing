"""
Resilience Expectation - 가설 기반 자동 채점 시스템.

시스템이 Chaos 장애에 대해 어떻게 반응해야 하는지 정의하고,
실험 후 Resilience Score를 자동으로 계산합니다.

Usage:
    # 간단한 사용: CB OPEN 기대
    config = ExperimentConfig(
        target_service="payment-api",
        resilience_expectation=ResilienceExpectation.expect_cb_open(
            target_service="payment-api",
            within_seconds=10.0,
        ),
    )

    # 복잡한 사용: 다중 Assertion
    config = ExperimentConfig(
        resilience_expectation=ResilienceExpectation(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service="payment",
                    expected_within_seconds=10,
                ),
                ResilienceAssertion(
                    expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                    target_service="payment",
                ),
            ],
            require_all=True,
        ),
    )
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# Enums
# =============================================================================


class ExpectationType(str, Enum):
    """Resilience 기대 유형."""

    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    """CB가 OPEN 상태로 전환되어야 함."""

    CIRCUIT_BREAKER_HALF_OPEN = "circuit_breaker_half_open"
    """CB가 HALF_OPEN 상태로 전환되어야 함."""

    FALLBACK_ACTIVATED = "fallback_activated"
    """Fallback이 활성화되어야 함."""

    RETRY_TRIGGERED = "retry_triggered"
    """Retry가 발동되어야 함."""

    RATE_LIMIT_ACTIVATED = "rate_limit_activated"
    """Rate Limit이 활성화되어야 함."""

    LOAD_SHEDDING_TRIGGERED = "load_shedding_triggered"
    """Load Shedding이 발동되어야 함."""

    AUTO_SCALE_TRIGGERED = "auto_scale_triggered"
    """Auto-scaling이 발동되어야 함."""

    ALERT_FIRED = "alert_fired"
    """Alert이 발생해야 함."""

    EMERGENCY_MODE_ACTIVATED = "emergency_mode_activated"
    """Emergency Mode가 활성화되어야 함."""

    GRACEFUL_DEGRADATION = "graceful_degradation"
    """Graceful Degradation (복합 검증)."""

    CUSTOM = "custom"
    """사용자 정의 검증."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ResilienceAssertion:
    """
    단일 Resilience 기대 조건.

    예: "CB가 10초 내에 OPEN 되어야 함"

    Attributes:
        expectation_type: 기대 유형 (CB_OPEN, FALLBACK 등)
        target_service: 대상 서비스 (선택)
        expected_within_seconds: 기대 시간 내 발생 (초)
        expected_state: 기대 상태 (CB의 경우 "open", "half_open" 등)
        custom_validator: 사용자 정의 검증 함수 (CUSTOM 타입용)
        description: 사람이 읽을 수 있는 설명
    """

    expectation_type: ExpectationType
    """기대 유형."""

    target_service: str = ""
    """대상 서비스 (선택)."""

    expected_within_seconds: float = 30.0
    """기대 시간 내 발생 (초)."""

    expected_state: str = ""
    """기대 상태 (CB의 경우 "open", "half_open" 등)."""

    expected_count: int = 0
    """기대 발생 횟수 (예: retry 3회)."""

    custom_validator: Callable[[dict[str, Any]], bool] | None = None
    """사용자 정의 검증 함수 (CUSTOM 타입용)."""

    description: str = ""
    """사람이 읽을 수 있는 설명."""

    def __post_init__(self) -> None:
        if not self.description:
            self.description = self._generate_description()

    def _generate_description(self) -> str:
        """자동 설명 생성."""
        service_part = f" for {self.target_service}" if self.target_service else ""
        time_part = f" within {self.expected_within_seconds}s"

        descriptions = {
            ExpectationType.CIRCUIT_BREAKER_OPEN: f"CB should OPEN{service_part}{time_part}",
            ExpectationType.CIRCUIT_BREAKER_HALF_OPEN: f"CB should transition to HALF_OPEN{service_part}{time_part}",
            ExpectationType.FALLBACK_ACTIVATED: f"Fallback should activate{service_part}{time_part}",
            ExpectationType.RETRY_TRIGGERED: f"Retry should trigger{service_part}{time_part}",
            ExpectationType.RATE_LIMIT_ACTIVATED: f"Rate limit should activate{service_part}{time_part}",
            ExpectationType.LOAD_SHEDDING_TRIGGERED: f"Load shedding should trigger{service_part}{time_part}",
            ExpectationType.AUTO_SCALE_TRIGGERED: f"Auto-scaling should trigger{service_part}{time_part}",
            ExpectationType.ALERT_FIRED: f"Alert should fire{time_part}",
            ExpectationType.EMERGENCY_MODE_ACTIVATED: f"Emergency mode should activate{time_part}",
            ExpectationType.GRACEFUL_DEGRADATION: f"Graceful degradation{service_part}{time_part}",
        }

        return descriptions.get(
            self.expectation_type,
            f"{self.expectation_type.value} expected{service_part}",
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "expectation_type": self.expectation_type.value,
            "target_service": self.target_service,
            "expected_within_seconds": self.expected_within_seconds,
            "expected_state": self.expected_state,
            "expected_count": self.expected_count,
            "description": self.description,
        }


@dataclass
class ResilienceExpectation:
    """
    Chaos 실험의 Resilience 기대값 집합.

    "시스템이 이 장애에 대해 어떻게 반응해야 하는가?"를 정의.

    Attributes:
        assertions: 검증할 Assertion 목록
        require_all: True면 모든 Assertion 통과 필요, False면 하나라도 통과하면 성공
        description: 전체 기대값 설명
    """

    assertions: list[ResilienceAssertion] = field(default_factory=list)
    """검증할 Assertion 목록."""

    require_all: bool = True
    """True: 모든 Assertion 통과 필요, False: 하나라도 통과하면 성공."""

    description: str = ""
    """전체 기대값 설명."""

    # ==========================================================================
    # Factory Methods
    # ==========================================================================

    @classmethod
    def expect_cb_open(
        cls,
        target_service: str,
        within_seconds: float = 10.0,
    ) -> ResilienceExpectation:
        """
        편의 팩토리: CB OPEN 기대.

        Args:
            target_service: 대상 서비스
            within_seconds: CB OPEN 기대 시간 (초)

        Returns:
            ResilienceExpectation 인스턴스
        """
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                    expected_state="open",
                )
            ],
            description=f"Expect {target_service} CB to OPEN within {within_seconds}s",
        )

    @classmethod
    def expect_fallback(
        cls,
        target_service: str,
        within_seconds: float = 15.0,
    ) -> ResilienceExpectation:
        """
        편의 팩토리: Fallback 활성화 기대.

        Args:
            target_service: 대상 서비스
            within_seconds: Fallback 활성화 기대 시간 (초)

        Returns:
            ResilienceExpectation 인스턴스
        """
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                )
            ],
            description=f"Expect {target_service} fallback to activate within {within_seconds}s",
        )

    @classmethod
    def expect_graceful_degradation(
        cls,
        target_service: str,
        within_seconds: float = 30.0,
    ) -> ResilienceExpectation:
        """
        편의 팩토리: Graceful Degradation 기대 (CB + Fallback).

        Args:
            target_service: 대상 서비스
            within_seconds: 기대 시간 (초)

        Returns:
            ResilienceExpectation 인스턴스
        """
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                ),
                ResilienceAssertion(
                    expectation_type=ExpectationType.FALLBACK_ACTIVATED,
                    target_service=target_service,
                    expected_within_seconds=within_seconds,
                ),
            ],
            require_all=True,
            description=f"Expect graceful degradation for {target_service}",
        )

    @classmethod
    def expect_retry_then_cb_open(
        cls,
        target_service: str,
        retry_count: int = 3,
        cb_open_within_seconds: float = 10.0,
    ) -> ResilienceExpectation:
        """
        편의 팩토리: Retry 후 CB OPEN 기대.

        Args:
            target_service: 대상 서비스
            retry_count: 기대 재시도 횟수
            cb_open_within_seconds: CB OPEN 기대 시간 (초)

        Returns:
            ResilienceExpectation 인스턴스
        """
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.RETRY_TRIGGERED,
                    target_service=target_service,
                    expected_count=retry_count,
                    expected_within_seconds=cb_open_within_seconds,
                ),
                ResilienceAssertion(
                    expectation_type=ExpectationType.CIRCUIT_BREAKER_OPEN,
                    target_service=target_service,
                    expected_within_seconds=cb_open_within_seconds,
                    expected_state="open",
                ),
            ],
            require_all=True,
            description=f"Expect {retry_count} retries then CB OPEN for {target_service}",
        )

    @classmethod
    def expect_emergency_mode(
        cls,
        level: int = 1,
        within_seconds: float = 60.0,
    ) -> ResilienceExpectation:
        """
        편의 팩토리: Emergency Mode 활성화 기대.

        Args:
            level: Emergency Level (1-3)
            within_seconds: 활성화 기대 시간 (초)

        Returns:
            ResilienceExpectation 인스턴스
        """
        return cls(
            assertions=[
                ResilienceAssertion(
                    expectation_type=ExpectationType.EMERGENCY_MODE_ACTIVATED,
                    expected_within_seconds=within_seconds,
                    expected_state=f"level_{level}",
                )
            ],
            description=f"Expect Emergency Mode Level {level} within {within_seconds}s",
        )

    # ==========================================================================
    # Methods
    # ==========================================================================

    def add_assertion(self, assertion: ResilienceAssertion) -> ResilienceExpectation:
        """
        Assertion 추가 (빌더 패턴).

        Args:
            assertion: 추가할 Assertion

        Returns:
            self for chaining
        """
        self.assertions.append(assertion)
        return self

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "assertions": [a.to_dict() for a in self.assertions],
            "require_all": self.require_all,
            "description": self.description,
        }


@dataclass
class ResilienceValidationResult:
    """
    Resilience 검증 결과.

    Attributes:
        passed: 전체 통과 여부
        assertion_results: 개별 Assertion 결과
        total_assertions: 전체 Assertion 수
        passed_assertions: 통과한 Assertion 수
        failed_assertions: 실패한 Assertion 수
        resilience_score: 0.0 ~ 1.0 Resilience 점수
        summary: 결과 요약
        validation_time_seconds: 검증에 소요된 시간 (초)
    """

    passed: bool
    """전체 통과 여부."""

    assertion_results: list[dict[str, Any]] = field(default_factory=list)
    """개별 Assertion 결과."""

    total_assertions: int = 0
    passed_assertions: int = 0
    failed_assertions: int = 0

    resilience_score: float = 0.0
    """0.0 ~ 1.0 Resilience 점수."""

    summary: str = ""
    """결과 요약."""

    validation_time_seconds: float = 0.0
    """검증에 소요된 시간 (초)."""

    def __post_init__(self) -> None:
        if self.total_assertions > 0 and not self.summary:
            self.resilience_score = self.passed_assertions / self.total_assertions
            self.summary = (
                f"Resilience: {self.passed_assertions}/{self.total_assertions} "
                f"({self.resilience_score * 100:.0f}%)"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "passed": self.passed,
            "assertion_results": self.assertion_results,
            "total_assertions": self.total_assertions,
            "passed_assertions": self.passed_assertions,
            "failed_assertions": self.failed_assertions,
            "resilience_score": self.resilience_score,
            "summary": self.summary,
            "validation_time_seconds": self.validation_time_seconds,
        }

    @classmethod
    def skip(
        cls, reason: str = "No resilience expectation defined"
    ) -> ResilienceValidationResult:
        """
        검증 스킵 결과 생성.

        Args:
            reason: 스킵 사유

        Returns:
            passed=True인 빈 결과
        """
        return cls(
            passed=True,
            summary=f"Skipped: {reason}",
        )

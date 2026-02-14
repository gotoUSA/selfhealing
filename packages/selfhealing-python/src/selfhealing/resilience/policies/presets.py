"""
Preset Pipelines — 사전 정의된 resilience 파이프라인.

자주 사용되는 Policy 조합을 편의 함수로 제공한다.
소비자가 개별 Policy를 일일이 조합하지 않아도 되는 표준 파이프라인을 제공한다.

- standard_pipeline(): Retry + CircuitBreaker 기본 구성
- ha_pipeline(): 고가용성 (Hedging + Bulkhead 포함)
"""

from __future__ import annotations

from typing import Any, Callable, TypeVar

from selfhealing.resilience.policies.composer import PolicyComposer, compose
from selfhealing.resilience.policies.guards import (
    ErrorBudgetGuard,
    KillSwitchGuard,
)
from selfhealing.resilience.policies.hooks import AuditHook, MetricsHook
from selfhealing.resilience.policies.sinks import DLQSink

T = TypeVar("T")


def standard_pipeline(
    service_name: str,
    max_retries: int = 3,
    domain: str = "default",
) -> PolicyComposer:
    """
    표준 resilience 파이프라인 — Retry + Guard + Audit + DLQ.

    최소한의 설정으로 Retry + Kill Switch + ErrorBudget + Audit + DLQ를
    자동 구성한다.

    Args:
        service_name: 서비스 식별자 (메트릭/로깅에 사용)
        max_retries: 최대 재시도 횟수
        domain: 도메인 식별자 (RetryPolicyConfig.domain)

    Returns:
        PolicyComposer 인스턴스

    Usage::

        pipeline = standard_pipeline("payment_api")
        result = pipeline.execute(lambda: call_payment())
    """
    from selfhealing.services.retry_handler.models import RetryPolicyConfig
    from selfhealing.services.retry_handler.policy import RetryPolicy

    retry_config = RetryPolicyConfig(
        max_attempts=max_retries,
        domain=domain,
    )

    return (
        compose(
            RetryPolicy(config=retry_config),
        )
        .add_guard(KillSwitchGuard())
        .add_guard(ErrorBudgetGuard())
        .add_hook(AuditHook())
        .add_sink(DLQSink())
    )


def ha_pipeline(
    service_name: str,
    candidates: list[Callable[..., Any]],
    max_retries: int = 2,
    hedging_delay: float = 0.1,
    max_concurrent: int = 20,
    domain: str = "default",
) -> PolicyComposer:
    """
    고가용성 파이프라인 — Retry + Bulkhead + Hedging + Guard + Audit + Metrics + DLQ.

    Hedging(병렬 경쟁 실행)과 Bulkhead(리소스 격리)를 포함한
    고가용성 구성. 대규모 서비스에 적합하다.

    Args:
        service_name: 서비스 식별자
        candidates: Hedging 후보 함수 목록
        max_retries: 최대 재시도 횟수
        hedging_delay: Hedging 지연 시간 (초)
        max_concurrent: Bulkhead 최대 동시 실행 수
        domain: 도메인 식별자

    Returns:
        PolicyComposer 인스턴스

    Usage::

        pipeline = ha_pipeline("product_api", [fetch_b, fetch_c])
        result = pipeline.execute(lambda: fetch_a())
    """
    from selfhealing.core.hedging.config import HedgingConfig, HedgingMode
    from selfhealing.resilience.bulkhead.policy import bulkhead_policy
    from selfhealing.resilience.policies.hedging import HedgingPolicy
    from selfhealing.services.retry_handler.models import RetryPolicyConfig
    from selfhealing.services.retry_handler.policy import RetryPolicy

    retry_config = RetryPolicyConfig(
        max_attempts=max_retries,
        domain=domain,
    )

    bp = bulkhead_policy(
        name=f"{service_name}_bulkhead",
        max_concurrent=max_concurrent,
    )

    hedging_config = HedgingConfig(
        mode=HedgingMode.DELAYED,
        delay=hedging_delay,
    )

    return (
        compose(
            RetryPolicy(config=retry_config),
            bp,
            HedgingPolicy(
                candidates=candidates,
                config=hedging_config,
            ),
        )
        .add_guard(KillSwitchGuard())
        .add_guard(ErrorBudgetGuard())
        .add_hook(AuditHook())
        .add_hook(MetricsHook())
        .add_sink(DLQSink())
    )

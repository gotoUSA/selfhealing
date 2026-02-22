"""
Preset Pipelines — 사전 정의된 resilience 파이프라인.

자주 사용되는 Policy 조합을 편의 함수로 제공한다.
소비자가 개별 Policy를 일일이 조합하지 않아도 되는 표준 파이프라인을 제공한다.

- standard_pipeline(): Retry + CircuitBreaker 기본 구성
- ha_pipeline(): 고가용성 (Hedging + Bulkhead 포함)

Fallback 지원:
    두 프리셋 모두 fallback_chain / fallback_fn / fallback_default 선택적 파라미터를
    통해 FallbackPolicy를 파이프라인에 자동 주입할 수 있다.
    셋 중 하나라도 None이 아니면 FallbackPolicy가 compose() 마지막(가장 바깥쪽)에 배치된다.
    FallbackPolicy의 실행 순서: fallback_chain → fallback_fn → fallback_default.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from selfhealing.resilience.policies.composer import PolicyComposer, compose
from selfhealing.resilience.policies.fallback import FallbackPolicy
from selfhealing.resilience.policies.guards import (
    ErrorBudgetGuard,
    KillSwitchGuard,
)
from selfhealing.resilience.policies.hooks import AuditHook, MetricsHook
from selfhealing.resilience.policies.sinks import DLQSink

T = TypeVar("T")


def _build_fallback_policy(
    fallback_chain: list[Callable[[], Any]] | None,
    fallback_fn: Callable[[], Any] | None,
    fallback_default: Any,
) -> FallbackPolicy | None:
    """
    Fallback 파라미터로부터 FallbackPolicy 인스턴스를 생성한다.

    셋 중 하나라도 None이 아니면 FallbackPolicy를 반환하고,
    모두 None이면 None을 반환한다.
    """
    has_chain = fallback_chain is not None
    has_fn = fallback_fn is not None
    has_default = fallback_default is not None

    if not (has_chain or has_fn or has_default):
        return None

    return FallbackPolicy(
        fallback_chain=fallback_chain,
        fallback_fn=fallback_fn,
        default_value=fallback_default,
    )


def standard_pipeline(
    service_name: str,
    max_retries: int = 3,
    domain: str = "default",
    # --- Fallback 선택적 파라미터 (3단계) ---
    fallback_chain: list[Callable[[], Any]] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    fallback_default: Any = None,
) -> PolicyComposer:
    """
    표준 resilience 파이프라인 — Retry + Guard + Audit + DLQ.

    최소한의 설정으로 Retry + Kill Switch + ErrorBudget + Audit + DLQ를
    자동 구성한다.

    Fallback 활성화:
        fallback_chain, fallback_fn, fallback_default 중 하나라도 전달하면
        FallbackPolicy가 파이프라인 가장 바깥쪽에 자동 추가된다.
        compose() 인자 목록의 마지막 = reversed() 적용 시 가장 바깥쪽 래퍼.

    Args:
        service_name: 서비스 식별자 (메트릭/로깅에 사용)
        max_retries: 최대 재시도 횟수
        domain: 도메인 식별자 (RetryPolicyConfig.domain)
        fallback_chain: 순차 시도할 fallback 함수 리스트
            (partition_aware_chain() 등 고급 사용).
        fallback_fn: 단일 callable fallback 함수.
        fallback_default: 고정 기본값 (모든 fallback 실패 시).

    Returns:
        PolicyComposer 인스턴스

    Usage::

        # Fallback 없이 (기존 동작)
        pipeline = standard_pipeline("payment_api")
        result = pipeline.execute(lambda: call_payment())

        # Fallback 포함
        pipeline = standard_pipeline(
            "payment_api",
            fallback_default={"status": "degraded"},
        )
    """
    from selfhealing.services.retry_handler.models import RetryPolicyConfig
    from selfhealing.services.retry_handler.policy import RetryPolicy

    retry_config = RetryPolicyConfig(
        max_attempts=max_retries,
        domain=domain,
    )

    policies: list = [RetryPolicy(config=retry_config)]

    fallback_policy = _build_fallback_policy(
        fallback_chain,
        fallback_fn,
        fallback_default,
    )
    if fallback_policy is not None:
        policies.append(fallback_policy)

    return (
        compose(*policies).add_guard(KillSwitchGuard()).add_guard(ErrorBudgetGuard()).add_hook(AuditHook()).add_sink(DLQSink())
    )


def ha_pipeline(
    service_name: str,
    candidates: list[Callable[..., Any]],
    max_retries: int = 2,
    hedging_delay: float = 0.1,
    max_concurrent: int = 20,
    domain: str = "default",
    # --- Fallback 선택적 파라미터 (3단계) ---
    fallback_chain: list[Callable[[], Any]] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    fallback_default: Any = None,
) -> PolicyComposer:
    """
    고가용성 파이프라인 — Retry + Bulkhead + Hedging + Guard + Audit + Metrics + DLQ.

    Hedging(병렬 경쟁 실행)과 Bulkhead(리소스 격리)를 포함한
    고가용성 구성. 대규모 서비스에 적합하다.

    Fallback 활성화:
        fallback_chain, fallback_fn, fallback_default 중 하나라도 전달하면
        FallbackPolicy가 파이프라인 가장 바깥쪽에 자동 추가된다.
        compose() 인자 목록의 마지막 = reversed() 적용 시 가장 바깥쪽 래퍼.

    Args:
        service_name: 서비스 식별자
        candidates: Hedging 후보 함수 목록
        max_retries: 최대 재시도 횟수
        hedging_delay: Hedging 지연 시간 (초)
        max_concurrent: Bulkhead 최대 동시 실행 수
        domain: 도메인 식별자
        fallback_chain: 순차 시도할 fallback 함수 리스트
            (partition_aware_chain() 등 고급 사용).
        fallback_fn: 단일 callable fallback 함수.
        fallback_default: 고정 기본값 (모든 fallback 실패 시).

    Returns:
        PolicyComposer 인스턴스

    Usage::

        # Fallback 없이 (기존 동작)
        pipeline = ha_pipeline("product_api", [fetch_b, fetch_c])
        result = pipeline.execute(lambda: fetch_a())

        # partition_aware_chain과 함께
        from selfhealing.resilience.policies.fallback import partition_aware_chain
        pipeline = ha_pipeline(
            "product_api",
            [fetch_b, fetch_c],
            fallback_chain=partition_aware_chain(
                state_provider=lambda: health_monitor.get_state(),
                cache_fn=lambda: redis.get("product:123"),
                db_fn=lambda: Product.objects.get(id=123),
            ),
            fallback_default={"status": "degraded"},
        )
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

    policies: list = [
        RetryPolicy(config=retry_config),
        bp,
        HedgingPolicy(
            candidates=candidates,
            config=hedging_config,
        ),
    ]

    fallback_policy = _build_fallback_policy(
        fallback_chain,
        fallback_fn,
        fallback_default,
    )
    if fallback_policy is not None:
        policies.append(fallback_policy)

    return (
        compose(*policies)
        .add_guard(KillSwitchGuard())
        .add_guard(ErrorBudgetGuard())
        .add_hook(AuditHook())
        .add_hook(MetricsHook())
        .add_sink(DLQSink())
    )

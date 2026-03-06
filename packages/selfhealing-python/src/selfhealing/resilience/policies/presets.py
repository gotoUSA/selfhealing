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

import structlog

from selfhealing.resilience.policies.composer import PolicyComposer, compose
from selfhealing.resilience.policies.fallback import FallbackPolicy
from selfhealing.resilience.policies.guards import (
    ErrorBudgetGuard,
    KillSwitchGuard,
)
from selfhealing.resilience.policies.hooks import AuditHook, MetricsHook
from selfhealing.resilience.policies.sinks import DLQSink

logger = structlog.get_logger()

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
        compose(*policies)
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


def minimal_pipeline(
    service_name: str,
    audit_sampling_rate: float = 1.0,
) -> PolicyComposer:
    """
    경량 resilience 파이프라인 -- CB 체크 + 선택적 감사만 수행.

    Guard(ErrorBudget Redis 호출)와 Sink(DLQ 저장)를 제거하여
    오버헤드를 최소화한다. 읽기 전용/비필수 요청에 적합하다.

    감사 로깅:
    - audit_sampling_rate=1.0 (기본값): 100% 감사 (AuditHook)
    - audit_sampling_rate < 1.0: 샘플링 감사 (SampledAuditHook)
    - audit_sampling_rate=0.0: 감사 미수행

    Args:
        service_name: CB가 보호하는 서비스 식별자
        audit_sampling_rate: 감사 샘플링 비율 (1.0=100%)

    Returns:
        PolicyComposer 인스턴스

    Usage::

        pipeline = minimal_pipeline("product_api")
        result = pipeline.execute(lambda: get_product(id))

        pipeline = minimal_pipeline("search_api", audit_sampling_rate=0.01)
        result = pipeline.execute(lambda: search(query))
    """
    from selfhealing.services.circuit_breaker.policy import CircuitBreakerPolicy

    composer = compose(CircuitBreakerPolicy(service_name=service_name))

    if audit_sampling_rate >= 1.0:
        composer.add_hook(AuditHook())
    elif audit_sampling_rate > 0.0:
        composer.add_hook(SampledAuditHook(sample_rate=audit_sampling_rate))

    return composer


def adaptive_pipeline(
    service_name: str,
    tier_id: str | None = None,
    # --- standard_pipeline 파라미터 ---
    max_retries: int = 3,
    domain: str = "default",
    # --- Fallback 선택적 파라미터 ---
    fallback_chain: list[Callable[[], Any]] | None = None,
    fallback_fn: Callable[[], Any] | None = None,
    fallback_default: Any = None,
) -> PolicyComposer:
    """
    적응형 파이프라인 -- tier_id와 시스템 부하에 따라 자동 선택.

    동작 모드:
    1. adaptive_enabled=False (기본값): 항상 standard_pipeline 반환
    2. adaptive_enabled=True:
       - GracefulDegradation이 full_guards를 비활성화하면 minimal 반환
       - tier_id가 hot_path_tiers에 포함되면 minimal 반환
       - 그 외: standard_pipeline 반환

    Args:
        service_name: 서비스 식별자
        tier_id: 요청 tier ("critical" | "standard" | "non_essential")
        max_retries: standard_pipeline 최대 재시도 횟수
        domain: standard_pipeline 도메인
        fallback_chain: standard_pipeline fallback 체인
        fallback_fn: standard_pipeline fallback 함수
        fallback_default: standard_pipeline fallback 기본값

    Returns:
        PolicyComposer 인스턴스

    Usage::

        pipeline = adaptive_pipeline("product_api", tier_id="non_essential")
        result = pipeline.execute(lambda: get_product(id))

        pipeline = adaptive_pipeline(
            "payment_api",
            tier_id="critical",
            fallback_default={"status": "degraded"},
        )
        result = pipeline.execute(lambda: process_payment(data))
    """
    from selfhealing.settings.pipeline import get_pipeline_settings

    settings = get_pipeline_settings()

    if not settings.adaptive_enabled:
        return standard_pipeline(
            service_name=service_name,
            max_retries=max_retries,
            domain=domain,
            fallback_chain=fallback_chain,
            fallback_fn=fallback_fn,
            fallback_default=fallback_default,
        )

    # GracefulDegradation 연동: full_guards 비활성화 여부 확인
    degradation_active = False
    try:
        from selfhealing.scaling.graceful_degradation import (
            get_graceful_degradation,
        )

        degradation = get_graceful_degradation()
        if not degradation.is_enabled("full_guards"):
            degradation_active = True
    except ImportError:
        pass

    # minimal 파이프라인 반환 조건:
    # 1. GracefulDegradation이 full_guards를 비활성화함
    # 2. tier_id가 hot_path_tiers에 포함됨
    use_minimal = degradation_active or (
        tier_id is not None and tier_id in settings.hot_path_tiers
    )

    if use_minimal:
        return minimal_pipeline(
            service_name=service_name,
            audit_sampling_rate=settings.audit_sampling_rate,
        )

    return standard_pipeline(
        service_name=service_name,
        max_retries=max_retries,
        domain=domain,
        fallback_chain=fallback_chain,
        fallback_fn=fallback_fn,
        fallback_default=fallback_default,
    )

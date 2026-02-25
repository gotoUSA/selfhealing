"""
Traffic Shaper.

요청 비율과 동시 사용자를 조절하여 부하를 형성합니다.
SyntheticLoadGenerator와 함께 사용하여 정교한 부하 패턴을 생성합니다.

Related Modules:
- SyntheticLoadGenerator: services/chaos/synthetic_load.py
- TrafficType enum: services/chaos/base.py

Design Principle:
- 요청 비율 조절 (TrafficShaper)
- 동시 사용자 조절
- 트래픽 분배
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class ShapingMode(str, Enum):
    """트래픽 형성 모드."""

    RATE_LIMIT = "rate_limit"
    """RPS 기반 제한."""

    CONCURRENT_LIMIT = "concurrent_limit"
    """동시 요청 수 기반 제한."""

    ADAPTIVE = "adaptive"
    """응답 시간 기반 적응형 제한."""


class DistributionStrategy(str, Enum):
    """트래픽 분배 전략."""

    UNIFORM = "uniform"
    """균등 분배."""

    WEIGHTED = "weighted"
    """가중치 기반 분배."""

    ROUND_ROBIN = "round_robin"
    """라운드 로빈."""

    LEAST_CONN = "least_conn"
    """최소 연결 우선."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class ShapingConfig:
    """트래픽 형성 설정."""

    mode: ShapingMode = ShapingMode.RATE_LIMIT
    """형성 모드."""

    # Rate limiting
    target_rps: float = 100.0
    """목표 RPS."""

    burst_size: int = 10
    """버스트 허용 크기."""

    # Concurrency
    max_concurrent: int = 50
    """최대 동시 요청."""

    # Adaptive
    target_latency_ms: float = 200.0
    """목표 레이턴시 (적응형 모드)."""

    adjustment_interval_seconds: float = 5.0
    """조정 간격 (초)."""

    # Distribution
    distribution_strategy: DistributionStrategy = DistributionStrategy.UNIFORM
    """분배 전략."""

    endpoint_weights: dict[str, float] = field(default_factory=dict)
    """엔드포인트별 가중치."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "mode": self.mode.value,
            "target_rps": self.target_rps,
            "burst_size": self.burst_size,
            "max_concurrent": self.max_concurrent,
            "target_latency_ms": self.target_latency_ms,
            "adjustment_interval_seconds": self.adjustment_interval_seconds,
            "distribution_strategy": self.distribution_strategy.value,
            "endpoint_weights": self.endpoint_weights,
        }


@dataclass
class ShapingStats:
    """트래픽 형성 통계."""

    total_shaped: int = 0
    total_throttled: int = 0
    current_rps: float = 0.0
    current_concurrent: int = 0
    avg_latency_ms: float = 0.0
    adaptations_made: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_shaped": self.total_shaped,
            "total_throttled": self.total_throttled,
            "current_rps": round(self.current_rps, 2),
            "current_concurrent": self.current_concurrent,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "adaptations_made": self.adaptations_made,
        }


@dataclass
class TrafficDistribution:
    """트래픽 분배 결과."""

    endpoint: str
    allocated_rps: float
    weight: float
    request_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "endpoint": self.endpoint,
            "allocated_rps": round(self.allocated_rps, 2),
            "weight": round(self.weight, 2),
            "request_count": self.request_count,
        }


# =============================================================================
# Token Bucket (Rate Limiter)
# =============================================================================


class ChaosTokenBucket:
    """토큰 버킷 알고리즘 기반 레이트 리미터 (Chaos 전용)."""

    def __init__(
        self,
        rate: float,
        capacity: int,
    ):
        """
        Args:
            rate: 초당 토큰 생성율
            capacity: 버킷 용량
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = float(capacity)
        self._last_refill = time.time()
        self._lock = threading.Lock()

    def acquire(self, tokens: int = 1) -> bool:
        """
        토큰 획득 시도.

        Args:
            tokens: 필요한 토큰 수

        Returns:
            bool: 획득 성공 여부
        """
        with self._lock:
            self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True

            return False

    def _refill(self) -> None:
        """토큰 리필."""
        current_time = time.time()
        elapsed = current_time - self._last_refill

        # 경과 시간에 비례하여 토큰 추가
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = current_time

    def set_rate(self, rate: float) -> None:
        """레이트 조정."""
        with self._lock:
            self._rate = rate


# =============================================================================
# Traffic Shaper
# =============================================================================


class TrafficShaper:
    """
    트래픽 형성기.

    요청 비율과 동시성을 조절하여 원하는 부하 패턴을 형성합니다.

    Usage:
        shaper = TrafficShaper(experiment_id="exp-001")
        config = ShapingConfig(
            mode=ShapingMode.RATE_LIMIT,
            target_rps=100,
        )
        shaper.configure(config)

        if shaper.should_allow():
            # 요청 수행
            pass

        stats = shaper.get_stats()
    """

    def __init__(self, experiment_id: str):
        """
        Args:
            experiment_id: 실험 ID
        """
        self.experiment_id = experiment_id

        self._config: ShapingConfig | None = None
        self._stats = ShapingStats()
        self._token_bucket: ChaosTokenBucket | None = None

        # 동시성 추적
        self._concurrent_count = 0
        self._concurrent_lock = threading.Lock()

        # 적응형 모드 상태
        self._latency_samples: list[float] = []
        self._last_adaptation_time = 0.0
        self._current_rate_multiplier = 1.0

        # 분배 상태
        self._distributions: dict[str, TrafficDistribution] = {}
        self._round_robin_index = 0

        # 시작 시간
        self._start_time: float | None = None
        self._request_count = 0

    def configure(self, config: ShapingConfig) -> None:
        """
        형성기 설정.

        Args:
            config: 형성 설정
        """
        self._config = config
        self._token_bucket = ChaosTokenBucket(
            rate=config.target_rps,
            capacity=config.burst_size,
        )
        self._start_time = time.time()
        self._stats = ShapingStats()

        # 분배 설정
        if config.endpoint_weights:
            total_weight = sum(config.endpoint_weights.values())
            for endpoint, weight in config.endpoint_weights.items():
                normalized_weight = weight / total_weight if total_weight > 0 else 1.0
                self._distributions[endpoint] = TrafficDistribution(
                    endpoint=endpoint,
                    allocated_rps=config.target_rps * normalized_weight,
                    weight=normalized_weight,
                )

        logger.info(
            "traffic_shaper.configured",
            experiment_id=self.experiment_id,
            mode=config.mode.value,
            target_rps=config.target_rps,
        )

    def should_allow(self, endpoint: str | None = None) -> bool:
        """
        요청 허용 여부 결정.

        Args:
            endpoint: 대상 엔드포인트 (분배용)

        Returns:
            bool: 허용 여부
        """
        if not self._config:
            return True

        allowed = False

        if self._config.mode == ShapingMode.RATE_LIMIT:
            allowed = self._check_rate_limit()

        elif self._config.mode == ShapingMode.CONCURRENT_LIMIT:
            allowed = self._check_concurrent_limit()

        elif self._config.mode == ShapingMode.ADAPTIVE:
            allowed = self._check_adaptive_limit()

        if allowed:
            self._stats.total_shaped += 1
            self._request_count += 1

            if endpoint and endpoint in self._distributions:
                self._distributions[endpoint].request_count += 1
        else:
            self._stats.total_throttled += 1

        # 통계 업데이트
        self._update_stats()

        return allowed

    def acquire_concurrent(self) -> bool:
        """
        동시 슬롯 획득.

        Returns:
            bool: 획득 성공 여부
        """
        if not self._config:
            return True

        with self._concurrent_lock:
            if self._concurrent_count < self._config.max_concurrent:
                self._concurrent_count += 1
                self._stats.current_concurrent = self._concurrent_count
                return True

        return False

    def release_concurrent(self) -> None:
        """동시 슬롯 해제."""
        with self._concurrent_lock:
            self._concurrent_count = max(0, self._concurrent_count - 1)
            self._stats.current_concurrent = self._concurrent_count

    def record_latency(self, latency_ms: float) -> None:
        """
        레이턴시 기록 (적응형 모드용).

        Args:
            latency_ms: 응답 레이턴시 (밀리초)
        """
        self._latency_samples.append(latency_ms)

        # 최근 100개만 유지
        if len(self._latency_samples) > 100:
            self._latency_samples = self._latency_samples[-100:]

        if self._latency_samples:
            self._stats.avg_latency_ms = sum(self._latency_samples) / len(self._latency_samples)

    def select_endpoint(self) -> str | None:
        """
        분배 전략에 따라 엔드포인트 선택.

        Returns:
            Optional[str]: 선택된 엔드포인트
        """
        if not self._config or not self._distributions:
            return None

        strategy = self._config.distribution_strategy
        endpoints = list(self._distributions.keys())

        if not endpoints:
            return None

        if strategy == DistributionStrategy.UNIFORM:
            import random

            return random.choice(endpoints)

        elif strategy == DistributionStrategy.WEIGHTED:
            import random

            weights = [self._distributions[e].weight for e in endpoints]
            return random.choices(endpoints, weights=weights, k=1)[0]

        elif strategy == DistributionStrategy.ROUND_ROBIN:
            endpoint = endpoints[self._round_robin_index % len(endpoints)]
            self._round_robin_index += 1
            return endpoint

        elif strategy == DistributionStrategy.LEAST_CONN:
            # 가장 적은 요청 수를 가진 엔드포인트
            return min(
                endpoints,
                key=lambda e: self._distributions[e].request_count,
            )

        return endpoints[0]

    def get_stats(self) -> ShapingStats:
        """통계 반환."""
        return self._stats

    def get_distributions(self) -> list[TrafficDistribution]:
        """분배 상태 반환."""
        return list(self._distributions.values())

    def reset(self) -> None:
        """상태 초기화."""
        self._stats = ShapingStats()
        self._concurrent_count = 0
        self._latency_samples = []
        self._current_rate_multiplier = 1.0
        self._request_count = 0
        self._start_time = time.time()

        for dist in self._distributions.values():
            dist.request_count = 0

        if self._token_bucket and self._config:
            self._token_bucket.set_rate(self._config.target_rps)

    def _check_rate_limit(self) -> bool:
        """레이트 제한 확인."""
        if not self._token_bucket:
            return True

        return self._token_bucket.acquire()

    def _check_concurrent_limit(self) -> bool:
        """동시성 제한 확인."""
        if not self._config:
            return True

        return self._concurrent_count < self._config.max_concurrent

    def _check_adaptive_limit(self) -> bool:
        """적응형 제한 확인."""
        if not self._config or not self._token_bucket:
            return True

        # 주기적으로 레이트 조정
        current_time = time.time()
        if current_time - self._last_adaptation_time > self._config.adjustment_interval_seconds:
            self._adapt_rate()
            self._last_adaptation_time = current_time

        return self._token_bucket.acquire()

    def _adapt_rate(self) -> None:
        """적응형 레이트 조정."""
        if not self._config or not self._latency_samples:
            return

        avg_latency = sum(self._latency_samples) / len(self._latency_samples)
        target_latency = self._config.target_latency_ms

        if avg_latency > target_latency * 1.2:
            # 레이턴시가 높으면 레이트 감소
            self._current_rate_multiplier *= 0.9
        elif avg_latency < target_latency * 0.8:
            # 레이턴시가 낮으면 레이트 증가
            self._current_rate_multiplier = min(2.0, self._current_rate_multiplier * 1.1)

        # 새 레이트 적용
        new_rate = self._config.target_rps * self._current_rate_multiplier
        if self._token_bucket:
            self._token_bucket.set_rate(new_rate)

        self._stats.adaptations_made += 1

        logger.debug(
            "traffic_shaper.adapted_rate_rps_multiplier",
            new_rate=new_rate,
            current_rate_multiplier=self._current_rate_multiplier,
            avg_latency=avg_latency,
        )

    def _update_stats(self) -> None:
        """통계 업데이트."""
        if self._start_time:
            elapsed = time.time() - self._start_time
            if elapsed > 0:
                self._stats.current_rps = self._request_count / elapsed


# =============================================================================
# Singleton
# =============================================================================

_shapers: dict[str, TrafficShaper] = {}


def get_traffic_shaper(experiment_id: str) -> TrafficShaper:
    """
    싱글톤 방식으로 형성기 반환.

    Args:
        experiment_id: 실험 ID

    Returns:
        TrafficShaper: 형성기 인스턴스
    """
    if experiment_id not in _shapers:
        _shapers[experiment_id] = TrafficShaper(experiment_id)

    return _shapers[experiment_id]


def cleanup_shaper(experiment_id: str) -> None:
    """형성기 정리."""
    if experiment_id in _shapers:
        _shapers[experiment_id].reset()
        del _shapers[experiment_id]

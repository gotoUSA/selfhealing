"""
Synthetic Load Generator.

카오스 실험용 합성 트래픽 생성기.
실제 프로덕션 요청과 분리하여 SLA 통계에서 제외합니다.

Related Modules:
- XTestModeMixin 패턴: api/django/views/xtest/base.py
- TrafficType enum: services/chaos/base.py

Design Principle:
- Admin Deep Link 방식으로 거버넌스 유지
- 모든 조작이 감사(Audit) 기록
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import structlog

from selfhealing.core.timezone import now

logger = structlog.get_logger()


# =============================================================================
# Constants
# =============================================================================

SYNTHETIC_HEADER = "X-Self-Healing-Synthetic"
SYNTHETIC_VALUE = "chaos-experiment"


# =============================================================================
# Enums
# =============================================================================


class LoadPattern(str, Enum):
    """부하 패턴 종류."""

    CONSTANT = "constant"
    """일정한 부하 유지."""

    RAMP_UP = "ramp_up"
    """점진적 부하 증가."""

    RAMP_DOWN = "ramp_down"
    """점진적 부하 감소."""

    SPIKE = "spike"
    """급격한 부하 급증."""

    STEADY_STATE = "steady_state"
    """안정 상태 유지 (Constant와 유사하나 워밍업 포함)."""

    WAVE = "wave"
    """주기적 부하 패턴."""


class GeneratorState(str, Enum):
    """생성기 상태."""

    IDLE = "idle"
    """대기 중."""

    RUNNING = "running"
    """트래픽 생성 중."""

    STOPPING = "stopping"
    """정상 종료 중."""

    STOPPED = "stopped"
    """종료됨."""

    ERROR = "error"
    """오류 발생."""


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class SyntheticRequest:
    """합성 트래픽 요청 정보."""

    experiment_id: str
    target_service: str
    original_headers: dict[str, str]
    synthetic_headers: dict[str, str]
    excluded_from_sla: bool = True
    request_id: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "experiment_id": self.experiment_id,
            "target_service": self.target_service,
            "original_headers": self.original_headers,
            "synthetic_headers": self.synthetic_headers,
            "excluded_from_sla": self.excluded_from_sla,
            "request_id": self.request_id,
            "created_at": self.created_at,
        }


@dataclass
class LoadConfig:
    """부하 설정."""

    # 기본 설정
    target_rps: float = 10.0
    """목표 RPS (Requests Per Second)."""

    duration_seconds: int = 60
    """부하 유지 시간 (초)."""

    pattern: LoadPattern = LoadPattern.CONSTANT
    """부하 패턴."""

    # Ramp 설정
    ramp_up_seconds: int = 10
    """Ramp-up 시간 (초)."""

    ramp_down_seconds: int = 5
    """Ramp-down 시간 (초)."""

    # Spike 설정
    spike_multiplier: float = 3.0
    """Spike 시 RPS 배수."""

    spike_duration_seconds: int = 5
    """Spike 유지 시간 (초)."""

    # 동시성 설정
    max_concurrent: int = 50
    """최대 동시 요청 수."""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "target_rps": self.target_rps,
            "duration_seconds": self.duration_seconds,
            "pattern": self.pattern.value,
            "ramp_up_seconds": self.ramp_up_seconds,
            "ramp_down_seconds": self.ramp_down_seconds,
            "spike_multiplier": self.spike_multiplier,
            "spike_duration_seconds": self.spike_duration_seconds,
            "max_concurrent": self.max_concurrent,
        }


@dataclass
class GeneratorStats:
    """생성기 통계."""

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    current_rps: float = 0.0
    avg_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    min_latency_ms: float = 0.0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "failed_requests": self.failed_requests,
            "current_rps": round(self.current_rps, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "max_latency_ms": round(self.max_latency_ms, 2),
            "min_latency_ms": round(self.min_latency_ms, 2),
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }


# =============================================================================
# Synthetic Traffic Generator
# =============================================================================


class SyntheticTrafficGenerator:
    """
    합성 트래픽 생성기.

    카오스 실험 중 실제 프로덕션 요청과 분리된 합성 요청을 생성합니다.

    Usage:
        generator = SyntheticTrafficGenerator(experiment_id="exp-001")
        synthetic_req = generator.create_synthetic_request(
            target_service="payment-api",
            original_headers={"Content-Type": "application/json"},
        )
    """

    def __init__(self, experiment_id: str):
        """
        Args:
            experiment_id: 연결된 카오스 실험 ID
        """
        self.experiment_id = experiment_id
        self._generated_count = 0
        self._request_counter = 0

    def create_synthetic_request(
        self,
        target_service: str,
        original_headers: dict[str, str] | None = None,
    ) -> SyntheticRequest:
        """
        합성 요청 생성.

        Args:
            target_service: 대상 서비스
            original_headers: 원본 헤더 (복사)

        Returns:
            SyntheticRequest: 합성 요청 정보
        """

        original = original_headers or {}

        self._request_counter += 1
        request_id = f"{self.experiment_id}-req-{self._request_counter}"

        synthetic_headers = {
            **original,
            SYNTHETIC_HEADER: SYNTHETIC_VALUE,
            "X-Experiment-Id": self.experiment_id,
            "X-Traffic-Type": "synthetic",
            "X-Request-Id": request_id,
        }

        self._generated_count += 1

        logger.debug(
            "synthetic_traffic.created_synthetic_request_experiment",
            target_service=target_service,
            _self=self.experiment_id,
        )

        return SyntheticRequest(
            experiment_id=self.experiment_id,
            target_service=target_service,
            original_headers=original,
            synthetic_headers=synthetic_headers,
            excluded_from_sla=True,
            request_id=request_id,
            created_at=now().isoformat(),
        )

    @staticmethod
    def is_synthetic_request(headers: dict[str, str]) -> bool:
        """
        요청이 합성 트래픽인지 확인.

        SLA 통계, FinOps 비용 계산에서 필터링할 때 사용합니다.

        Args:
            headers: 요청 헤더

        Returns:
            bool: 합성 트래픽 여부
        """
        return headers.get(SYNTHETIC_HEADER) == SYNTHETIC_VALUE

    def get_stats(self) -> dict[str, Any]:
        """생성 통계 반환."""
        return {
            "experiment_id": self.experiment_id,
            "generated_count": self._generated_count,
        }


# =============================================================================
# Synthetic Load Generator
# =============================================================================


class SyntheticLoadGenerator:
    """
    합성 부하 생성기.

    다양한 부하 패턴을 지원하여 카오스 실험 중
    시스템에 합성 트래픽을 주입합니다.

    Usage:
        generator = SyntheticLoadGenerator(
            experiment_id="exp-001",
            target_service="payment-api",
        )

        config = LoadConfig(
            target_rps=100,
            duration_seconds=60,
            pattern=LoadPattern.RAMP_UP,
        )

        generator.start(config, request_handler=my_request_handler)
        # ...
        generator.stop()
    """

    def __init__(
        self,
        experiment_id: str,
        target_service: str,
    ):
        """
        Args:
            experiment_id: 실험 ID
            target_service: 대상 서비스
        """
        self.experiment_id = experiment_id
        self.target_service = target_service

        self._state = GeneratorState.IDLE
        self._config: LoadConfig | None = None
        self._stats = GeneratorStats()
        self._traffic_generator = SyntheticTrafficGenerator(experiment_id)

        # 실행 제어
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._start_time: float | None = None

        # 콜백
        self._request_handler: Callable[[SyntheticRequest], bool] | None = None

        # 레이턴시 추적
        self._latencies: list[float] = []

    @property
    def state(self) -> GeneratorState:
        """현재 상태 반환."""
        return self._state

    @property
    def stats(self) -> GeneratorStats:
        """현재 통계 반환."""
        return self._stats

    def start(
        self,
        config: LoadConfig,
        request_handler: Callable[[SyntheticRequest], bool] | None = None,
    ) -> bool:
        """
        부하 생성 시작.

        Args:
            config: 부하 설정
            request_handler: 요청 처리 콜백 (성공 시 True 반환)

        Returns:
            bool: 시작 성공 여부
        """
        if self._state == GeneratorState.RUNNING:
            logger.warning(
                "synthetic_load.generator_already_running",
                _self=self.experiment_id,
            )
            return False

        self._config = config
        self._request_handler = request_handler
        self._stop_event.clear()
        self._state = GeneratorState.RUNNING
        self._start_time = time.time()
        self._stats = GeneratorStats()
        self._latencies = []

        self._worker_thread = threading.Thread(
            target=self._run_generator,
            daemon=True,
            name=f"synthetic-load-{self.experiment_id}",
        )
        self._worker_thread.start()

        logger.info(
            "synthetic_load.started_generator_experiment_pattern",
            _self=self.target_service,
            self_1=self.experiment_id,
            pattern=config.pattern.value,
            config=config.target_rps,
        )

        return True

    def stop(self, graceful: bool = True) -> GeneratorStats:
        """
        부하 생성 중지.

        Args:
            graceful: True면 현재 요청 완료 후 종료

        Returns:
            GeneratorStats: 최종 통계
        """
        if self._state not in [GeneratorState.RUNNING, GeneratorState.STOPPING]:
            return self._stats

        self._state = GeneratorState.STOPPING
        self._stop_event.set()

        if graceful and self._worker_thread:
            self._worker_thread.join(timeout=5.0)

        self._state = GeneratorState.STOPPED

        logger.info(
            "synthetic_load.stopped_generator_stats",
            _self=self.experiment_id,
            self_1=self._stats.to_dict(),
        )

        return self._stats

    def get_current_rps(self) -> float:
        """현재 RPS 계산."""
        if self._start_time is None:
            return 0.0

        elapsed = time.time() - self._start_time
        if elapsed <= 0:
            return 0.0

        return self._stats.total_requests / elapsed

    def _run_generator(self) -> None:
        """부하 생성 루프."""
        if not self._config:
            return

        config = self._config
        interval = 1.0 / config.target_rps if config.target_rps > 0 else 1.0

        while not self._stop_event.is_set():
            elapsed = time.time() - (self._start_time or time.time())

            # Duration 체크
            if elapsed >= config.duration_seconds:
                break

            # 현재 RPS 계산 (패턴에 따라)
            current_target_rps = self._calculate_current_rps(elapsed, config)

            # 요청 생성
            request = self._traffic_generator.create_synthetic_request(
                target_service=self.target_service,
            )

            # 요청 처리
            start_latency = time.time()
            success = True

            if self._request_handler:
                try:
                    success = self._request_handler(request)
                except Exception as e:
                    logger.debug(
                        "synthetic_load.request_failed",
                        error=e,
                    )
                    success = False

            latency_ms = (time.time() - start_latency) * 1000
            self._latencies.append(latency_ms)

            # 통계 업데이트
            self._stats.total_requests += 1
            if success:
                self._stats.successful_requests += 1
            else:
                self._stats.failed_requests += 1

            self._update_latency_stats()
            self._stats.elapsed_seconds = elapsed
            self._stats.current_rps = current_target_rps

            # 다음 요청까지 대기
            if current_target_rps > 0:
                sleep_time = 1.0 / current_target_rps
                time.sleep(max(0.001, sleep_time - latency_ms / 1000))

        self._state = GeneratorState.STOPPED

    def _calculate_current_rps(
        self,
        elapsed_seconds: float,
        config: LoadConfig,
    ) -> float:
        """패턴에 따른 현재 RPS 계산."""
        target = config.target_rps

        if config.pattern == LoadPattern.CONSTANT:
            return target

        elif config.pattern == LoadPattern.RAMP_UP:
            if elapsed_seconds < config.ramp_up_seconds:
                # 선형 증가
                return target * (elapsed_seconds / config.ramp_up_seconds)
            return target

        elif config.pattern == LoadPattern.RAMP_DOWN:
            remaining = config.duration_seconds - elapsed_seconds
            if remaining < config.ramp_down_seconds:
                # 선형 감소
                return target * (remaining / config.ramp_down_seconds)
            return target

        elif config.pattern == LoadPattern.SPIKE:
            # 중간 지점에서 스파이크
            mid_point = config.duration_seconds / 2
            if abs(elapsed_seconds - mid_point) < config.spike_duration_seconds / 2:
                return target * config.spike_multiplier
            return target

        elif config.pattern == LoadPattern.STEADY_STATE:
            # 워밍업 포함
            warmup_seconds = min(10, config.duration_seconds * 0.1)
            if elapsed_seconds < warmup_seconds:
                return target * (elapsed_seconds / warmup_seconds)
            return target

        elif config.pattern == LoadPattern.WAVE:
            import math

            # 사인파 패턴
            period = 30  # 30초 주기
            amplitude = target * 0.5
            return target + amplitude * math.sin(2 * math.pi * elapsed_seconds / period)

        return target

    def _update_latency_stats(self) -> None:
        """레이턴시 통계 업데이트."""
        if not self._latencies:
            return

        # 최근 100개만 유지
        if len(self._latencies) > 100:
            self._latencies = self._latencies[-100:]

        self._stats.avg_latency_ms = sum(self._latencies) / len(self._latencies)
        self._stats.max_latency_ms = max(self._latencies)
        self._stats.min_latency_ms = min(self._latencies)


# =============================================================================
# Singleton
# =============================================================================

_generators: dict[str, SyntheticLoadGenerator] = {}


def get_synthetic_load_generator(
    experiment_id: str,
    target_service: str,
) -> SyntheticLoadGenerator:
    """
    싱글톤 방식으로 생성기 반환.

    Args:
        experiment_id: 실험 ID
        target_service: 대상 서비스

    Returns:
        SyntheticLoadGenerator: 생성기 인스턴스
    """
    key = f"{experiment_id}:{target_service}"

    if key not in _generators:
        _generators[key] = SyntheticLoadGenerator(
            experiment_id=experiment_id,
            target_service=target_service,
        )

    return _generators[key]


def cleanup_generator(experiment_id: str, target_service: str) -> None:
    """생성기 정리."""
    key = f"{experiment_id}:{target_service}"

    if key in _generators:
        generator = _generators[key]
        if generator.state == GeneratorState.RUNNING:
            generator.stop()
        del _generators[key]

"""
시나리오 기반 시계열 생성기.

단위 테스트 및 통합 테스트에서 사용할 합성(synthetic) 시계열 데이터를 생성한다.

지원 시나리오:
    - gradual_degradation: 점진적 악화 (latency 100→5000ms 등)
    - spike_and_recovery: 스파이크 후 복구 시나리오
    - seasonal_pattern: 일간 계절성 패턴 (Holt-Winters 검증용)
    - pool_exhaustion: 커넥션 풀 점진적 고갈
    - memory_leak: 메모리 누수 (OOM 예측 검증)
    - stable_noise: 안정적 값 + 가우시안 노이즈

MockMetricsAdapter (adapters/metrics/auto_tuning_adapter.py)의
simulate_degradation() 패턴을 시계열 생성으로 확장한 것이다.

Usage:
    from selfhealing.services.predictive_forecaster.scenario_generator import (
        TimeSeriesScenarioGenerator,
    )

    values = TimeSeriesScenarioGenerator.gradual_degradation(
        base=150.0, target=5000.0, steps=100,
    )
"""

from __future__ import annotations

import math
import random


class TimeSeriesScenarioGenerator:
    """
    시나리오 기반 합성 시계열 생성기.

    MockMetricsAdapter.simulate_degradation() 패턴을 확장하여
    다양한 장애/트래픽 시나리오의 시계열 데이터를 생성한다.
    """

    @staticmethod
    def gradual_degradation(
        base: float = 150.0,
        target: float = 5000.0,
        steps: int = 100,
        noise_ratio: float = 0.05,
        seed: int | None = 42,
    ) -> list[float]:
        """
        점진적 악화 시나리오.

        base에서 target까지 steps에 걸쳐 선형 증가 + 노이즈.

        Args:
            base: 시작값 (예: 정상 레이턴시 150ms).
            target: 목표값 (예: 악화된 레이턴시 5000ms).
            steps: 총 스텝 수.
            noise_ratio: 노이즈 비율 (값 대비 표준편차).
            seed: 난수 시드 (재현성).

        Returns:
            생성된 시계열 데이터.
        """
        rng = random.Random(seed)
        values = []
        for i in range(steps):
            progress = i / max(steps - 1, 1)
            value = base + (target - base) * progress
            noise = rng.gauss(0, value * noise_ratio)
            values.append(max(0.0, value + noise))
        return values

    @staticmethod
    def spike_and_recovery(
        base: float = 100.0,
        spike_value: float = 1000.0,
        spike_at: int = 50,
        recover_at: int = 70,
        steps: int = 100,
        noise_ratio: float = 0.03,
        seed: int | None = 42,
    ) -> list[float]:
        """
        스파이크 후 복구 시나리오.

        일정 구간에서 급격히 상승한 뒤 점진적으로 정상 복귀.

        Args:
            base: 정상 기준값.
            spike_value: 스파이크 최대값.
            spike_at: 스파이크 시작 스텝.
            recover_at: 복구 완료 스텝.
            steps: 총 스텝 수.
            noise_ratio: 노이즈 비율.
            seed: 난수 시드.

        Returns:
            생성된 시계열 데이터.
        """
        rng = random.Random(seed)
        values = []
        for i in range(steps):
            if i < spike_at:
                value = base
            elif i < recover_at:
                # 스파이크 → 복구 (지수 감소)
                spike_progress = (i - spike_at) / max(recover_at - spike_at, 1)
                value = spike_value - (spike_value - base) * spike_progress
            else:
                value = base
            noise = rng.gauss(0, value * noise_ratio)
            values.append(max(0.0, value + noise))
        return values

    @staticmethod
    def seasonal_pattern(
        base: float = 100.0,
        amplitude: float = 30.0,
        period: int = 1440,
        trend_slope: float = 0.0,
        steps: int = 4320,
        noise_ratio: float = 0.02,
        seed: int | None = 42,
    ) -> list[float]:
        """
        계절성(일간) 패턴 시나리오.

        정현파(sinusoidal) 기반의 주기적 패턴 + 선택적 트렌드.
        HoltWintersForecaster 검증에 사용.

        Args:
            base: 기준값.
            amplitude: 계절성 진폭.
            period: 시즌 길이 (일간 패턴 = 1440 if 60초 간격).
            trend_slope: 스텝당 트렌드 기울기 (0이면 트렌드 없음).
            steps: 총 스텝 수 (3일 = 4320 if 60초 간격).
            noise_ratio: 노이즈 비율.
            seed: 난수 시드.

        Returns:
            생성된 시계열 데이터.
        """
        rng = random.Random(seed)
        values = []
        for i in range(steps):
            seasonal = amplitude * math.sin(2 * math.pi * i / period)
            trend = trend_slope * i
            value = base + seasonal + trend
            noise = rng.gauss(0, max(abs(value), 1.0) * noise_ratio)
            values.append(max(0.0, value + noise))
        return values

    @staticmethod
    def pool_exhaustion(
        initial_usage: float = 30.0,
        growth_rate: float = 0.5,
        max_usage: float = 100.0,
        steps: int = 200,
        noise_ratio: float = 0.02,
        seed: int | None = 42,
    ) -> list[float]:
        """
        커넥션 풀 점진적 고갈 시나리오.

        ConnectionPoolMonitor.get_trend() 예측 검증에 사용.

        Args:
            initial_usage: 초기 사용률 (%).
            growth_rate: 스텝당 증가율 (%).
            max_usage: 최대 사용률 (%).
            steps: 총 스텝 수.
            noise_ratio: 노이즈 비율.
            seed: 난수 시드.

        Returns:
            생성된 사용률 시계열 데이터 (0~100%).
        """
        rng = random.Random(seed)
        values = []
        for i in range(steps):
            value = min(max_usage, initial_usage + growth_rate * i)
            noise = rng.gauss(0, max(value, 1.0) * noise_ratio)
            values.append(max(0.0, min(max_usage, value + noise)))
        return values

    @staticmethod
    def memory_leak(
        initial_mb: float = 500.0,
        leak_rate_mb: float = 2.0,
        max_mb: float = 1024.0,
        steps: int = 300,
        noise_ratio: float = 0.01,
        seed: int | None = 42,
    ) -> list[float]:
        """
        메모리 누수 시나리오.

        CgroupResourceMonitor OOM 예측 검증에 사용.

        Args:
            initial_mb: 초기 메모리 사용량 (MB).
            leak_rate_mb: 스텝당 누수량 (MB).
            max_mb: 최대 메모리 (MB) — OOM 발생 지점.
            steps: 총 스텝 수.
            noise_ratio: 노이즈 비율.
            seed: 난수 시드.

        Returns:
            생성된 메모리 사용량 시계열 데이터 (MB).
        """
        rng = random.Random(seed)
        values = []
        for i in range(steps):
            value = min(max_mb, initial_mb + leak_rate_mb * i)
            noise = rng.gauss(0, max(value, 1.0) * noise_ratio)
            values.append(max(0.0, value + noise))
        return values

    @staticmethod
    def stable_noise(
        base: float = 100.0,
        std_dev: float = 5.0,
        steps: int = 200,
        seed: int | None = 42,
    ) -> list[float]:
        """
        안정적인 값 + 가우시안 노이즈 시나리오.

        정상 운영 상태를 시뮬레이션. 이상 탐지의 False Positive 검증에 사용.

        Args:
            base: 기준값.
            std_dev: 표준편차.
            steps: 총 스텝 수.
            seed: 난수 시드.

        Returns:
            생성된 시계열 데이터.
        """
        rng = random.Random(seed)
        return [max(0.0, rng.gauss(base, std_dev)) for _ in range(steps)]

    @staticmethod
    def flash_sale_surge(
        base_rps: float = 1000.0,
        peak_rps: float = 5000.0,
        rampup_steps: int = 10,
        peak_duration: int = 30,
        cooldown_steps: int = 20,
        base_error_rate: float = 0.01,
        seed: int | None = 42,
    ) -> tuple[list[float], list[float], list[float]]:
        """
        Flash Sale(정상 급증) 시나리오 — SpikeClassifier 검증용.

        RPS 급증 + 에러율 안정 + 레이턴시 비례 증가 = HEALTHY_SURGE 패턴.

        Args:
            base_rps: 기본 RPS.
            peak_rps: 피크 RPS.
            rampup_steps: 상승 스텝 수.
            peak_duration: 피크 지속 스텝 수.
            cooldown_steps: 하강 스텝 수.
            base_error_rate: 기본 에러율.
            seed: 난수 시드.

        Returns:
            (rps_history, error_rate_history, latency_history) 튜플.
        """
        rng = random.Random(seed)
        total = rampup_steps + peak_duration + cooldown_steps
        rps_list: list[float] = []
        error_list: list[float] = []
        latency_list: list[float] = []

        for i in range(total):
            if i < rampup_steps:
                progress = i / max(rampup_steps - 1, 1)
                rps = base_rps + (peak_rps - base_rps) * progress
            elif i < rampup_steps + peak_duration:
                rps = peak_rps
            else:
                cool_progress = (i - rampup_steps - peak_duration) / max(cooldown_steps - 1, 1)
                rps = peak_rps - (peak_rps - base_rps) * cool_progress

            # 에러율 안정적 (정상 급증이므로)
            error = base_error_rate + rng.gauss(0, 0.002)
            error = max(0.0, min(1.0, error))

            # 레이턴시는 RPS에 비례
            latency = 50.0 + (rps / base_rps) * 50.0 + rng.gauss(0, 5.0)
            latency = max(10.0, latency)

            rps_list.append(rps + rng.gauss(0, rps * 0.02))
            error_list.append(error)
            latency_list.append(latency)

        return rps_list, error_list, latency_list

    @staticmethod
    def ddos_attack(
        base_rps: float = 1000.0,
        attack_rps: float = 10000.0,
        attack_start: int = 10,
        steps: int = 50,
        base_error_rate: float = 0.01,
        attack_error_rate: float = 0.3,
        seed: int | None = 42,
    ) -> tuple[list[float], list[float], list[float]]:
        """
        DDoS 공격(이상 급증) 시나리오 — SpikeClassifier 검증용.

        RPS 수직 상승 + 에러율 급등 + 레이턴시 불규칙 = ANOMALOUS_SPIKE 패턴.

        Args:
            base_rps: 기본 RPS.
            attack_rps: 공격 시 RPS.
            attack_start: 공격 시작 스텝.
            steps: 총 스텝 수.
            base_error_rate: 기본 에러율.
            attack_error_rate: 공격 시 에러율.
            seed: 난수 시드.

        Returns:
            (rps_history, error_rate_history, latency_history) 튜플.
        """
        rng = random.Random(seed)
        rps_list: list[float] = []
        error_list: list[float] = []
        latency_list: list[float] = []

        for i in range(steps):
            if i < attack_start:
                rps = base_rps + rng.gauss(0, base_rps * 0.02)
                error = base_error_rate + rng.gauss(0, 0.002)
                latency = 100.0 + rng.gauss(0, 5.0)
            else:
                # 공격 — 수직 상승
                rps = attack_rps + rng.gauss(0, attack_rps * 0.1)
                error = attack_error_rate + rng.gauss(0, 0.05)
                latency = 500.0 + rng.gauss(0, 200.0)

            rps_list.append(max(0.0, rps))
            error_list.append(max(0.0, min(1.0, error)))
            latency_list.append(max(10.0, latency))

        return rps_list, error_list, latency_list

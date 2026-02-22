"""
이상 탐지기: ZScoreDetector, IQRDetector.

ZScoreDetector:
    Z-Score 기반 이상 탐지. 현재 값이 이동 윈도우의 평균으로부터
    몇 표준편차 떨어져 있는지를 계산하여 이상 여부를 판정한다.
    기본 임계값 3.0 (99.7% 신뢰구간).

IQRDetector:
    IQR(사분위수 범위) 기반 이상 탐지. Z-Score보다 이상치에 강건(robust).
    정규분포 가정이 불필요하며, 극단적 이상치 영향을 적게 받는다.

외부 의존성 없이 Python 표준 라이브러리(math, collections)만 사용.

Usage:
    from selfhealing.services.predictive_forecaster.anomaly_detector import (
        ZScoreDetector,
        IQRDetector,
    )

    detector = ZScoreDetector(threshold=3.0, window=100)
    is_anomalous, z_score = detector.is_anomaly(value)

    iqr_detector = IQRDetector(multiplier=1.5)
    is_outlier, details = iqr_detector.is_anomaly(value)
"""

from __future__ import annotations

import collections
import math
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger()


# =============================================================================
# Z-Score 기반 이상 탐지
# =============================================================================


@dataclass
class ZScoreResult:
    """Z-Score 이상 탐지 결과."""

    is_anomaly: bool
    z_score: float
    mean: float
    std_dev: float
    value: float


class ZScoreDetector:
    """
    Z-Score 기반 이상 탐지기.

    현재 값의 Z-Score를 이동 윈도우 내 평균/표준편차로 계산하여
    임계값 초과 시 이상으로 판정한다.

    Z-Score = (value - mean) / std_dev

    표준편차가 0에 가까운 경우(모든 값이 동일) 이상으로 판정하지 않는다.

    Args:
        threshold: Z-Score 임계값 (기본 3.0 = 99.7% 신뢰구간).
        window: 이동 윈도우 크기 (기본 100개).
    """

    MIN_STD_DEV = 1e-10

    def __init__(self, threshold: float = 3.0, window: int = 100):
        if threshold <= 0:
            raise ValueError(f"threshold는 양수여야 합니다: {threshold}")
        if window < 3:
            raise ValueError(f"window는 3 이상이어야 합니다: {window}")

        self._threshold = threshold
        self._window = window
        self._values: collections.deque[float] = collections.deque(maxlen=window)

    def is_anomaly(self, value: float) -> tuple[bool, float]:
        """
        값이 이상인지 판정.

        Args:
            value: 검사할 값.

        Returns:
            (is_anomaly, z_score) 튜플.
            윈도우에 데이터가 3개 미만인 경우 (False, 0.0) 반환.
        """
        self._values.append(value)

        if len(self._values) < 3:
            return False, 0.0

        mean = sum(self._values) / len(self._values)
        variance = sum((v - mean) ** 2 for v in self._values) / len(self._values)
        std_dev = math.sqrt(variance)

        if std_dev < self.MIN_STD_DEV:
            return False, 0.0

        z_score = (value - mean) / std_dev
        return abs(z_score) > self._threshold, z_score

    def is_anomaly_detailed(self, value: float) -> ZScoreResult:
        """
        상세 결과를 포함한 이상 판정.

        Args:
            value: 검사할 값.

        Returns:
            ZScoreResult 상세 결과.
        """
        self._values.append(value)

        if len(self._values) < 3:
            return ZScoreResult(is_anomaly=False, z_score=0.0, mean=value, std_dev=0.0, value=value)

        mean = sum(self._values) / len(self._values)
        variance = sum((v - mean) ** 2 for v in self._values) / len(self._values)
        std_dev = math.sqrt(variance)

        if std_dev < self.MIN_STD_DEV:
            z_score = 0.0
        else:
            z_score = (value - mean) / std_dev

        return ZScoreResult(
            is_anomaly=abs(z_score) > self._threshold,
            z_score=z_score,
            mean=mean,
            std_dev=std_dev,
            value=value,
        )

    def get_statistics(self) -> dict[str, Any]:
        """현재 윈도우의 통계 정보 반환."""
        if not self._values:
            return {"count": 0, "mean": 0.0, "std_dev": 0.0}

        mean = sum(self._values) / len(self._values)
        variance = sum((v - mean) ** 2 for v in self._values) / len(self._values)
        std_dev = math.sqrt(variance)

        return {
            "count": len(self._values),
            "mean": mean,
            "std_dev": std_dev,
            "min": min(self._values),
            "max": max(self._values),
        }

    # ─── AnomalyDetectionStrategy Protocol 호환 ───

    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        """AnomalyDetectionStrategy.detect() — is_anomaly() 위임.

        context는 통계 기반 전략이므로 무시한다.
        """
        return self.is_anomaly(value)

    def update(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """AnomalyDetectionStrategy.update() — 윈도우에 값 추가.

        is_anomaly()가 내부적으로 값을 추가하므로
        별도 업데이트가 필요 없는 경우 이 메서드만 호출한다.
        """
        self._values.append(value)

    def get_feature_schema(self) -> dict[str, str] | None:
        """통계 기반 전략이므로 context 스키마 없음."""
        return None

    def reset(self) -> None:
        """윈도우 데이터 초기화."""
        self._values.clear()

    def to_dict(self) -> dict:
        """학습 상태를 dict로 직렬화한다.

        Returns:
            threshold, window, values를 포함하는 dict.
        """
        return {
            "values": list(self._values),
            "threshold": self._threshold,
            "window": self._window,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ZScoreDetector:
        """dict에서 인스턴스를 복원한다.

        __init__에서 deque(maxlen=window)가 생성되므로
        extend로 데이터를 주입해도 maxlen이 보존된다.

        Args:
            data: to_dict()로 생성된 dict.

        Returns:
            복원된 ZScoreDetector 인스턴스.
        """
        det = cls(threshold=data["threshold"], window=data["window"])
        det._values.extend(data["values"])
        return det


# =============================================================================
# IQR (사분위수 범위) 기반 이상 탐지
# =============================================================================


@dataclass
class IQRResult:
    """IQR 이상 탐지 결과."""

    is_anomaly: bool
    value: float
    q1: float
    q3: float
    iqr: float
    lower_bound: float
    upper_bound: float


class IQRDetector:
    """
    IQR(사분위수 범위) 기반 이상 탐지기.

    Z-Score보다 이상치에 강건(robust)한 방법.
    정규분포 가정이 불필요하며, 극단적 이상치의 영향을 적게 받는다.

    IQR = Q3 - Q1
    lower_bound = Q1 - multiplier * IQR
    upper_bound = Q3 + multiplier * IQR

    Args:
        multiplier: IQR 배수 (기본 1.5 = 일반적 이상치, 3.0 = 극단적 이상치).
        window: 이동 윈도우 크기 (기본 100개).
    """

    def __init__(self, multiplier: float = 1.5, window: int = 100):
        if multiplier <= 0:
            raise ValueError(f"multiplier는 양수여야 합니다: {multiplier}")
        if window < 4:
            raise ValueError(f"window는 4 이상이어야 합니다: {window}")

        self._multiplier = multiplier
        self._window = window
        self._values: collections.deque[float] = collections.deque(maxlen=window)

    @staticmethod
    def _percentile(sorted_data: list[float], p: float) -> float:
        """
        정렬된 데이터에서 백분위수를 계산 (선형 보간).

        Args:
            sorted_data: 정렬된 데이터 리스트.
            p: 백분위수 (0.0 ~ 1.0).

        Returns:
            해당 백분위수 값.
        """
        n = len(sorted_data)
        if n == 1:
            return sorted_data[0]

        k = (n - 1) * p
        floor_k = int(k)
        ceil_k = min(floor_k + 1, n - 1)
        frac = k - floor_k

        return sorted_data[floor_k] + frac * (sorted_data[ceil_k] - sorted_data[floor_k])

    def is_anomaly(self, value: float) -> tuple[bool, float]:
        """
        값이 이상인지 판정.

        Args:
            value: 검사할 값.

        Returns:
            (is_anomaly, iqr) 튜플.
            윈도우에 데이터가 4개 미만인 경우 (False, 0.0) 반환.
        """
        self._values.append(value)

        if len(self._values) < 4:
            return False, 0.0

        sorted_data = sorted(self._values)
        q1 = self._percentile(sorted_data, 0.25)
        q3 = self._percentile(sorted_data, 0.75)
        iqr = q3 - q1

        if iqr < 1e-10:
            return False, 0.0

        lower_bound = q1 - self._multiplier * iqr
        upper_bound = q3 + self._multiplier * iqr

        return value < lower_bound or value > upper_bound, iqr

    def is_anomaly_detailed(self, value: float) -> IQRResult:
        """
        상세 결과를 포함한 이상 판정.

        Args:
            value: 검사할 값.

        Returns:
            IQRResult 상세 결과.
        """
        self._values.append(value)

        if len(self._values) < 4:
            return IQRResult(
                is_anomaly=False,
                value=value,
                q1=0.0,
                q3=0.0,
                iqr=0.0,
                lower_bound=0.0,
                upper_bound=0.0,
            )

        sorted_data = sorted(self._values)
        q1 = self._percentile(sorted_data, 0.25)
        q3 = self._percentile(sorted_data, 0.75)
        iqr = q3 - q1

        lower_bound = q1 - self._multiplier * iqr
        upper_bound = q3 + self._multiplier * iqr

        return IQRResult(
            is_anomaly=value < lower_bound or value > upper_bound,
            value=value,
            q1=q1,
            q3=q3,
            iqr=iqr,
            lower_bound=lower_bound,
            upper_bound=upper_bound,
        )

    def get_bounds(self) -> dict[str, float] | None:
        """현재 윈도우의 IQR 경계 반환."""
        if len(self._values) < 4:
            return None

        sorted_data = sorted(self._values)
        q1 = self._percentile(sorted_data, 0.25)
        q3 = self._percentile(sorted_data, 0.75)
        iqr = q3 - q1

        return {
            "q1": q1,
            "q3": q3,
            "iqr": iqr,
            "lower_bound": q1 - self._multiplier * iqr,
            "upper_bound": q3 + self._multiplier * iqr,
        }

    # ─── AnomalyDetectionStrategy Protocol 호환 ───

    def detect(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> tuple[bool, float]:
        """AnomalyDetectionStrategy.detect() — is_anomaly() 위임.

        context는 통계 기반 전략이므로 무시한다.
        """
        return self.is_anomaly(value)

    def update(
        self,
        value: float,
        context: dict[str, Any] | None = None,
    ) -> None:
        """AnomalyDetectionStrategy.update() — 윈도우에 값 추가."""
        self._values.append(value)

    def get_feature_schema(self) -> dict[str, str] | None:
        """통계 기반 전략이므로 context 스키마 없음."""
        return None

    def reset(self) -> None:
        """윈도우 데이터 초기화."""
        self._values.clear()

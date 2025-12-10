"""
커스텀 메트릭 수집기

P99.9, Error Type 분리 등 확장 메트릭
"""

import time
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field
from collections import defaultdict
import statistics


@dataclass
class RequestMetric:
    """개별 요청 메트릭"""

    name: str
    method: str
    response_time: float  # ms
    status_code: Optional[int]
    error: Optional[str]
    timestamp: float
    stage: str = ""


@dataclass
class EndpointStats:
    """엔드포인트별 통계"""

    name: str
    count: int = 0
    success_count: int = 0
    failure_count: int = 0
    response_times: List[float] = field(default_factory=list)
    error_4xx_count: int = 0
    error_5xx_count: int = 0
    error_exception_count: int = 0

    @property
    def p50(self) -> float:
        """P50 (중간값)"""
        if not self.response_times:
            return 0.0
        return statistics.median(self.response_times)

    @property
    def p95(self) -> float:
        """P95"""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.95)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p99(self) -> float:
        """P99"""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.99)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def p999(self) -> float:
        """P99.9 - Tail Latency"""
        if not self.response_times:
            return 0.0
        sorted_times = sorted(self.response_times)
        idx = int(len(sorted_times) * 0.999)
        return sorted_times[min(idx, len(sorted_times) - 1)]

    @property
    def avg(self) -> float:
        """평균"""
        if not self.response_times:
            return 0.0
        return statistics.mean(self.response_times)

    @property
    def min_time(self) -> float:
        """최소"""
        if not self.response_times:
            return 0.0
        return min(self.response_times)

    @property
    def max_time(self) -> float:
        """최대"""
        if not self.response_times:
            return 0.0
        return max(self.response_times)

    @property
    def error_rate(self) -> float:
        """에러율"""
        if self.count == 0:
            return 0.0
        return self.failure_count / self.count

    @property
    def error_4xx_rate(self) -> float:
        """4xx 에러율"""
        if self.count == 0:
            return 0.0
        return self.error_4xx_count / self.count

    @property
    def error_5xx_rate(self) -> float:
        """5xx 에러율"""
        if self.count == 0:
            return 0.0
        return self.error_5xx_count / self.count

    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "name": self.name,
            "count": self.count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "p50": round(self.p50, 2),
            "p95": round(self.p95, 2),
            "p99": round(self.p99, 2),
            "p999": round(self.p999, 2),
            "avg": round(self.avg, 2),
            "min": round(self.min_time, 2),
            "max": round(self.max_time, 2),
            "error_rate": round(self.error_rate * 100, 2),
            "error_4xx_rate": round(self.error_4xx_rate * 100, 2),
            "error_5xx_rate": round(self.error_5xx_rate * 100, 2),
        }


class MetricsCollector:
    """
    커스텀 메트릭 수집기

    Usage:
        collector = MetricsCollector()
        collector.record_request(
            name="POST /api/payments/confirm/",
            method="POST",
            response_time=150.5,
            status_code=200,
            stage="Stage1",
        )

        stats = collector.get_stats()
        print(f"P99.9: {stats['POST /api/payments/confirm/'].p999}ms")
    """

    def __init__(self, max_samples: int = 10000):
        """
        Args:
            max_samples: 엔드포인트당 최대 샘플 수 (메모리 제한)
        """
        self.max_samples = max_samples
        self._stats: Dict[str, EndpointStats] = defaultdict(lambda: EndpointStats(name=""))
        self._stage_stats: Dict[str, Dict[str, EndpointStats]] = defaultdict(
            lambda: defaultdict(lambda: EndpointStats(name=""))
        )
        self._start_time = time.time()
        self._request_count = 0

    def record_request(
        self,
        name: str,
        method: str,
        response_time: float,
        status_code: Optional[int] = None,
        error: Optional[str] = None,
        stage: str = "",
    ):
        """
        요청 기록

        Args:
            name: 요청 이름 (예: "POST /api/payments/confirm/")
            method: HTTP 메서드
            response_time: 응답 시간 (ms)
            status_code: HTTP 상태 코드
            error: 에러 메시지
            stage: Stage 이름
        """
        self._request_count += 1

        # 전역 통계
        self._record_to_stats(self._stats[name], name, response_time, status_code, error)

        # Stage별 통계
        if stage:
            self._record_to_stats(self._stage_stats[stage][name], name, response_time, status_code, error)

    def _record_to_stats(
        self,
        stats: EndpointStats,
        name: str,
        response_time: float,
        status_code: Optional[int],
        error: Optional[str],
    ):
        """통계 객체에 기록"""
        stats.name = name
        stats.count += 1

        # 메모리 제한: 최대 샘플 수 초과 시 오래된 데이터 제거
        if len(stats.response_times) >= self.max_samples:
            stats.response_times = stats.response_times[-self.max_samples // 2 :]

        stats.response_times.append(response_time)

        if error:
            stats.failure_count += 1
            stats.error_exception_count += 1
        elif status_code:
            if status_code >= 500:
                stats.failure_count += 1
                stats.error_5xx_count += 1
            elif status_code >= 400:
                stats.failure_count += 1
                stats.error_4xx_count += 1
            else:
                stats.success_count += 1
        else:
            stats.success_count += 1

    def get_stats(self) -> Dict[str, EndpointStats]:
        """전역 통계 조회"""
        return dict(self._stats)

    def get_stage_stats(self, stage: str) -> Dict[str, EndpointStats]:
        """Stage별 통계 조회"""
        return dict(self._stage_stats.get(stage, {}))

    def get_all_stage_stats(self) -> Dict[str, Dict[str, EndpointStats]]:
        """모든 Stage 통계 조회"""
        return {stage: dict(stats) for stage, stats in self._stage_stats.items()}

    def get_summary(self) -> Dict[str, Any]:
        """전체 요약"""
        elapsed = time.time() - self._start_time

        total_success = sum(s.success_count for s in self._stats.values())
        total_failure = sum(s.failure_count for s in self._stats.values())

        return {
            "elapsed_seconds": round(elapsed, 2),
            "total_requests": self._request_count,
            "total_success": total_success,
            "total_failure": total_failure,
            "overall_error_rate": round(total_failure / max(self._request_count, 1) * 100, 2),
            "rps": round(self._request_count / max(elapsed, 1), 2),
            "endpoints": {name: stats.to_dict() for name, stats in self._stats.items()},
        }

    def print_summary(self):
        """요약 출력"""
        summary = self.get_summary()

        print("\n" + "=" * 80)
        print("[METRICS] CUSTOM METRICS SUMMARY")
        print("=" * 80)
        print(f"Elapsed: {summary['elapsed_seconds']}s")
        print(f"Total Requests: {summary['total_requests']}")
        print(f"RPS: {summary['rps']}")
        print(f"Error Rate: {summary['overall_error_rate']}%")
        print("-" * 80)
        print(f"{'Endpoint':<40} {'Count':>8} {'P95':>8} {'P99':>8} {'P99.9':>8} {'Err%':>6}")
        print("-" * 80)

        for name, stats in summary["endpoints"].items():
            print(
                f"{name[:40]:<40} "
                f"{stats['count']:>8} "
                f"{stats['p95']:>7.1f}ms "
                f"{stats['p99']:>7.1f}ms "
                f"{stats['p999']:>7.1f}ms "
                f"{stats['error_rate']:>5.1f}%"
            )

        print("=" * 80)

    def reset(self):
        """통계 초기화"""
        self._stats.clear()
        self._stage_stats.clear()
        self._start_time = time.time()
        self._request_count = 0


# 전역 수집기
_default_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """기본 MetricsCollector 인스턴스 반환"""
    global _default_collector
    if _default_collector is None:
        _default_collector = MetricsCollector()
    return _default_collector

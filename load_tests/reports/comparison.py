"""
Load Test Reports - Comparison Report.

두 테스트 결과를 비교하여 회귀를 감지하는 비교 리포트.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

from .base_report import BaseReport
from .report_config import ReportConfig, get_config


@dataclass
class RegressionAlarm:
    """회귀 감지 알람."""
    
    metric_name: str
    baseline_value: float
    current_value: float
    change_percent: float
    threshold_percent: float
    is_regression: bool
    direction: str = "higher"  # "higher" (증가가 나쁨) 또는 "lower" (감소가 나쁨)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "metric_name": self.metric_name,
            "baseline_value": self.baseline_value,
            "current_value": self.current_value,
            "change_percent": self.change_percent,
            "threshold_percent": self.threshold_percent,
            "is_regression": self.is_regression,
            "direction": self.direction,
        }


@dataclass
class ComparisonResult:
    """비교 결과 요약."""
    
    baseline_stage_id: str
    current_stage_id: str
    baseline_timestamp: str
    current_timestamp: str
    alarms: List[RegressionAlarm] = field(default_factory=list)
    has_regression: bool = False
    regression_count: int = 0
    improvement_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "baseline_stage_id": self.baseline_stage_id,
            "current_stage_id": self.current_stage_id,
            "baseline_timestamp": self.baseline_timestamp,
            "current_timestamp": self.current_timestamp,
            "has_regression": self.has_regression,
            "regression_count": self.regression_count,
            "improvement_count": self.improvement_count,
            "alarms": [a.to_dict() for a in self.alarms],
        }


class ComparisonReport:
    """
    두 테스트 결과 비교 보고서.
    
    baseline과 current 테스트 결과를 비교하여
    성능 회귀 또는 개선을 감지합니다.
    """
    
    def __init__(
        self,
        baseline: BaseReport,
        current: BaseReport,
        config: Optional[ReportConfig] = None,
    ):
        """
        ComparisonReport 초기화.
        
        Args:
            baseline: 기준 테스트 보고서
            current: 현재 테스트 보고서
            config: 보고서 설정 (선택)
        """
        self.baseline = baseline
        self.current = current
        self.config = config or get_config()
        self.alarms: List[RegressionAlarm] = []
        self._generated_at = datetime.now()
        
        # 분석 수행
        self._analyze()
    
    def _analyze(self) -> None:
        """회귀 분석 수행."""
        self.alarms = []
        
        # P99 응답시간 비교
        self._compare_metric(
            name="p99_response_ms",
            baseline_value=self.baseline.metrics.p99_response_ms,
            current_value=self.current.metrics.p99_response_ms,
            threshold_percent=self.config.REGRESSION_P99_THRESHOLD_PERCENT,
            direction="higher",
        )
        
        # P95 응답시간 비교
        self._compare_metric(
            name="p95_response_ms",
            baseline_value=self.baseline.metrics.p95_response_ms,
            current_value=self.current.metrics.p95_response_ms,
            threshold_percent=self.config.REGRESSION_P99_THRESHOLD_PERCENT,
            direction="higher",
        )
        
        # 평균 응답시간 비교
        self._compare_metric(
            name="avg_response_ms",
            baseline_value=self.baseline.metrics.avg_response_ms,
            current_value=self.current.metrics.avg_response_ms,
            threshold_percent=self.config.REGRESSION_P99_THRESHOLD_PERCENT,
            direction="higher",
        )
        
        # 에러율 비교
        self._compare_metric(
            name="error_rate_percent",
            baseline_value=self.baseline.metrics.error_rate_percent,
            current_value=self.current.metrics.error_rate_percent,
            threshold_percent=self.config.REGRESSION_ERROR_RATE_THRESHOLD_PERCENT,
            direction="higher",
        )
        
        # 처리량 비교 (낮아지면 회귀)
        self._compare_metric(
            name="throughput_rps",
            baseline_value=self.baseline.metrics.throughput_rps,
            current_value=self.current.metrics.throughput_rps,
            threshold_percent=self.config.REGRESSION_THROUGHPUT_THRESHOLD_PERCENT,
            direction="lower",
        )
    
    def _compare_metric(
        self,
        name: str,
        baseline_value: float,
        current_value: float,
        threshold_percent: float,
        direction: str = "higher",
    ) -> None:
        """
        단일 메트릭 비교.
        
        Args:
            name: 메트릭 이름
            baseline_value: 기준값
            current_value: 현재값
            threshold_percent: 회귀 임계값 (%)
            direction: "higher" (증가가 나쁨) 또는 "lower" (감소가 나쁨)
        """
        if baseline_value == 0:
            change_percent = 0.0 if current_value == 0 else 100.0
        else:
            change_percent = ((current_value - baseline_value) / baseline_value) * 100
        
        # 회귀 판정
        if direction == "higher":
            is_regression = change_percent > threshold_percent
        else:  # lower
            is_regression = change_percent < -threshold_percent
        
        alarm = RegressionAlarm(
            metric_name=name,
            baseline_value=baseline_value,
            current_value=current_value,
            change_percent=change_percent,
            threshold_percent=threshold_percent,
            is_regression=is_regression,
            direction=direction,
        )
        
        self.alarms.append(alarm)
    
    def has_regression(self) -> bool:
        """회귀 발생 여부."""
        return any(alarm.is_regression for alarm in self.alarms)
    
    def get_regressions(self) -> List[RegressionAlarm]:
        """회귀가 발생한 메트릭 목록."""
        return [alarm for alarm in self.alarms if alarm.is_regression]
    
    def get_improvements(self) -> List[RegressionAlarm]:
        """개선된 메트릭 목록."""
        improvements = []
        for alarm in self.alarms:
            if not alarm.is_regression:
                if alarm.direction == "higher" and alarm.change_percent < -alarm.threshold_percent:
                    improvements.append(alarm)
                elif alarm.direction == "lower" and alarm.change_percent > alarm.threshold_percent:
                    improvements.append(alarm)
        return improvements
    
    def get_result(self) -> ComparisonResult:
        """비교 결과 요약."""
        return ComparisonResult(
            baseline_stage_id=self.baseline.metrics.stage_id,
            current_stage_id=self.current.metrics.stage_id,
            baseline_timestamp=self.baseline.metrics.timestamp,
            current_timestamp=self.current.metrics.timestamp,
            alarms=self.alarms,
            has_regression=self.has_regression(),
            regression_count=len(self.get_regressions()),
            improvement_count=len(self.get_improvements()),
        )
    
    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용 딕셔너리."""
        return {
            "generated_at": self._generated_at.isoformat(),
            "comparison": self.get_result().to_dict(),
            "baseline_summary": {
                "stage_id": self.baseline.metrics.stage_id,
                "passed": self.baseline.metrics.passed,
                "p99_response_ms": self.baseline.metrics.p99_response_ms,
                "error_rate_percent": self.baseline.metrics.error_rate_percent,
                "throughput_rps": self.baseline.metrics.throughput_rps,
            },
            "current_summary": {
                "stage_id": self.current.metrics.stage_id,
                "passed": self.current.metrics.passed,
                "p99_response_ms": self.current.metrics.p99_response_ms,
                "error_rate_percent": self.current.metrics.error_rate_percent,
                "throughput_rps": self.current.metrics.throughput_rps,
            },
        }
    
    def to_markdown(self) -> str:
        """Markdown 비교 보고서."""
        result = self.get_result()
        
        # 헤더
        lines = [
            "# 📊 Performance Comparison Report",
            "",
            f"📅 **Generated**: {self._generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "---",
            "",
            "## 📋 Summary",
            "",
            "| Item | Baseline | Current |",
            "|------|----------|---------|",
            f"| Stage ID | {self.baseline.metrics.stage_id} | {self.current.metrics.stage_id} |",
            f"| Passed | {'✅' if self.baseline.metrics.passed else '❌'} | {'✅' if self.current.metrics.passed else '❌'} |",
            f"| P99 Response | {self.baseline.metrics.p99_response_ms:.2f}ms | {self.current.metrics.p99_response_ms:.2f}ms |",
            f"| Error Rate | {self.baseline.metrics.error_rate_percent:.2f}% | {self.current.metrics.error_rate_percent:.2f}% |",
            f"| Throughput | {self.baseline.metrics.throughput_rps:.2f} RPS | {self.current.metrics.throughput_rps:.2f} RPS |",
            "",
        ]
        
        # 회귀 상태
        if result.has_regression:
            lines.append(f"## ⚠️ Regression Detected ({result.regression_count} metrics)")
        else:
            lines.append("## ✅ No Regression Detected")
        
        lines.append("")
        
        # 상세 메트릭 비교
        lines.append("## 📈 Detailed Comparison")
        lines.append("")
        lines.append("| Metric | Baseline | Current | Change | Status |")
        lines.append("|--------|----------|---------|--------|--------|")
        
        for alarm in self.alarms:
            # 변화율 표시
            if alarm.change_percent > 0:
                change_str = f"+{alarm.change_percent:.1f}%"
            else:
                change_str = f"{alarm.change_percent:.1f}%"
            
            # 상태 아이콘
            if alarm.is_regression:
                status = "🔴 Regression"
            elif alarm.direction == "higher" and alarm.change_percent < -5:
                status = "🟢 Improved"
            elif alarm.direction == "lower" and alarm.change_percent > 5:
                status = "🟢 Improved"
            else:
                status = "⚪ Stable"
            
            lines.append(
                f"| {alarm.metric_name} | {alarm.baseline_value:.2f} | "
                f"{alarm.current_value:.2f} | {change_str} | {status} |"
            )
        
        lines.append("")
        
        # 개선 사항
        improvements = self.get_improvements()
        if improvements:
            lines.append("## 🎉 Improvements")
            lines.append("")
            for imp in improvements:
                lines.append(f"- **{imp.metric_name}**: {imp.change_percent:+.1f}%")
            lines.append("")
        
        # 회귀 사항
        regressions = self.get_regressions()
        if regressions:
            lines.append("## ⚠️ Regressions")
            lines.append("")
            for reg in regressions:
                lines.append(f"- **{reg.metric_name}**: {reg.change_percent:+.1f}% (threshold: {reg.threshold_percent}%)")
            lines.append("")
        
        lines.append("---")
        lines.append("")
        lines.append("📝 **Generated by Load Test Report Architecture v1.0.0**")
        
        return "\n".join(lines)
    
    def print_console(self) -> None:
        """콘솔에 비교 결과 출력."""
        result = self.get_result()
        
        print("\n" + "=" * 80)
        print("📊 PERFORMANCE COMPARISON REPORT")
        print("=" * 80)
        
        print(f"\n  Baseline: {self.baseline.metrics.stage_id}")
        print(f"  Current:  {self.current.metrics.stage_id}")
        
        print("\n" + "-" * 40)
        print("📈 Metric Comparison")
        print("-" * 40)
        
        for alarm in self.alarms:
            if alarm.change_percent > 0:
                change_str = f"+{alarm.change_percent:.1f}%"
            else:
                change_str = f"{alarm.change_percent:.1f}%"
            
            if alarm.is_regression:
                status = "🔴"
            elif alarm.direction == "higher" and alarm.change_percent < -5:
                status = "🟢"
            elif alarm.direction == "lower" and alarm.change_percent > 5:
                status = "🟢"
            else:
                status = "⚪"
            
            print(f"  {status} {alarm.metric_name}: {alarm.baseline_value:.2f} → {alarm.current_value:.2f} ({change_str})")
        
        print("\n" + "=" * 80)
        if result.has_regression:
            print(f"  ⚠️ REGRESSION DETECTED ({result.regression_count} metrics)")
        else:
            print("  ✅ NO REGRESSION DETECTED")
        print("=" * 80 + "\n")


def compare_reports(
    baseline: BaseReport,
    current: BaseReport,
    config: Optional[ReportConfig] = None,
) -> ComparisonReport:
    """
    두 보고서 비교 (편의 함수).
    
    Args:
        baseline: 기준 보고서
        current: 현재 보고서
        config: 설정 (선택)
        
    Returns:
        ComparisonReport: 비교 보고서
    """
    return ComparisonReport(baseline, current, config)

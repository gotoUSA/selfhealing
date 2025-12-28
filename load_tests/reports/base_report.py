"""
Load Test Base Report - 기본 보고서 클래스.

모든 보고서의 기반이 되는 추상 클래스입니다.
Executive Summary, Phase Analysis 등 공통 섹션을 제공합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
import os
import json
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, Any, Optional, List

from .schema import (
    BaseMetrics,
    PhaseMetrics,
    TimeseriesData,
    SCHEMA_VERSION,
)
from .report_config import ReportConfig, get_config


class BaseReport(ABC):
    """
    모든 보고서의 기반 클래스.
    
    Executive Summary, Phase Analysis 등 공통 보고서 섹션을 제공하며,
    JSON, Markdown, 콘솔 출력을 지원합니다.
    """
    
    def __init__(
        self,
        metrics: BaseMetrics,
        phases: Optional[List[PhaseMetrics]] = None,
        timeseries: Optional[TimeseriesData] = None,
        config: Optional[ReportConfig] = None,
    ):
        """
        BaseReport 초기화.
        
        Args:
            metrics: 기본 메트릭 데이터
            phases: 단계별 메트릭 (선택)
            timeseries: 시계열 데이터 (선택)
            config: 보고서 설정 (선택, 기본값 사용)
        """
        self.metrics = metrics
        self.phases = phases or []
        self.timeseries = timeseries
        self.config = config or get_config()
        self._generated_at = datetime.now()
    
    # ============================================================
    # 직렬화 메서드
    # ============================================================
    
    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용 딕셔너리 반환."""
        result = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": self._generated_at.isoformat(),
            "report_type": self.report_type,
            "base_metrics": self.metrics.to_dict(),
            "phases": [p.to_dict() for p in self.phases],
            "summary": self._generate_summary(),
        }
        
        if self.timeseries:
            result["timeseries"] = self.timeseries.to_dict()
        
        # 서브클래스 확장 데이터
        extended = self._get_extended_data()
        if extended:
            result.update(extended)
        
        return result
    
    def to_json(self, filepath: str) -> str:
        """
        JSON 파일 저장.
        
        Args:
            filepath: 저장할 파일 경로
            
        Returns:
            str: 저장된 파일 경로
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        
        return filepath
    
    def to_json_string(self) -> str:
        """JSON 문자열 반환."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)
    
    # ============================================================
    # Markdown 출력
    # ============================================================
    
    def to_markdown(self) -> str:
        """
        Markdown 문자열 반환.
        
        서브클래스에서 오버라이딩하여 확장 가능합니다.
        """
        sections = [
            self._render_header_markdown(),
            self._render_executive_summary_markdown(),
            self._render_phase_analysis_markdown(),
            self._render_response_time_markdown(),
            self._render_result_markdown(),
        ]
        
        # 서브클래스 확장 섹션
        extended_md = self._get_extended_markdown()
        if extended_md:
            sections.append(extended_md)
        
        return "\n".join(sections)
    
    def to_markdown_file(self, filepath: str) -> str:
        """
        Markdown 파일 저장.
        
        Args:
            filepath: 저장할 파일 경로
            
        Returns:
            str: 저장된 파일 경로
        """
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.to_markdown())
        
        return filepath
    
    # ============================================================
    # 콘솔 출력
    # ============================================================
    
    def print_console(self) -> None:
        """콘솔 출력."""
        print("\n" + "=" * 80)
        print(f"📊 {self.metrics.test_name.upper()}")
        print("=" * 80)
        
        # Executive Summary
        print("\n📋 Executive Summary")
        print("-" * 40)
        print(f"  Stage: {self.metrics.stage_id}")
        print(f"  Test Duration: {self.metrics.test_duration_sec}s")
        print(f"  Max Users: {self.metrics.max_users}")
        print(f"  Min Users: {self.metrics.min_users}")
        print(f"  Total Requests: {self.metrics.total_requests:,}")
        print(f"  Error Rate: {self.metrics.error_rate_percent:.2f}%")
        print(f"  Throughput: {self.metrics.throughput_rps:.2f} RPS")
        
        # Response Time
        print("\n⏱️ Response Time (ms)")
        print("-" * 40)
        print(f"  Average: {self.metrics.avg_response_ms:.2f}")
        print(f"  Min: {self.metrics.min_response_ms:.2f}")
        print(f"  Max: {self.metrics.max_response_ms:.2f}")
        print(f"  P50: {self.metrics.p50_response_ms:.2f}")
        print(f"  P95: {self.metrics.p95_response_ms:.2f}")
        print(f"  P99: {self.metrics.p99_response_ms:.2f}")
        
        # Phase Analysis
        if self.phases:
            print("\n📈 Phase Analysis")
            print("-" * 40)
            for phase in self.phases:
                if phase.requests > 0:
                    print(f"  {phase.phase_name.upper()}:")
                    print(f"    - Requests: {phase.requests:,}")
                    print(f"    - Error Rate: {phase.error_rate:.2f}%")
                    print(f"    - Avg Response: {phase.avg_response_ms:.2f}ms")
        
        # 서브클래스 확장 출력
        self._print_extended_console()
        
        # 결과
        print("\n" + "=" * 80)
        status_icon = "✅" if self.metrics.passed else "❌"
        status_text = "PASSED" if self.metrics.passed else "FAILED"
        print(f"  {status_icon} Final Result: {status_text}")
        
        if not self.metrics.passed and self.metrics.failure_reasons:
            print("\n  ❌ Failure Reasons:")
            for reason in self.metrics.failure_reasons:
                print(f"    - {reason}")
        
        print("=" * 80 + "\n")
    
    # ============================================================
    # Markdown 렌더링 헬퍼
    # ============================================================
    
    def _render_header_markdown(self) -> str:
        """Markdown 헤더 렌더링."""
        return f"""# {self.metrics.test_name}

📅 **Generated**: {self._generated_at.strftime('%Y-%m-%d %H:%M:%S')}  
🏷️ **Stage**: {self.metrics.stage_id}  
📝 **Schema Version**: {SCHEMA_VERSION}

---
"""
    
    def _render_executive_summary_markdown(self) -> str:
        """Executive Summary 렌더링."""
        return f"""## 📋 Executive Summary

| Metric | Value |
|--------|-------|
| Test Duration | {self.metrics.test_duration_sec}s |
| Max Users | {self.metrics.max_users} |
| Min Users | {self.metrics.min_users} |
| Total Requests | {self.metrics.total_requests:,} |
| Total Errors | {self.metrics.total_errors:,} |
| Error Rate | {self.metrics.error_rate_percent:.2f}% |
| Throughput | {self.metrics.throughput_rps:.2f} RPS |

"""
    
    def _render_phase_analysis_markdown(self) -> str:
        """Phase Analysis 렌더링."""
        if not self.phases:
            return ""
        
        lines = ["## 📈 Phase Analysis\n"]
        lines.append("| Phase | Requests | Errors | Error Rate | Avg Response |")
        lines.append("|-------|----------|--------|------------|--------------|")
        
        for phase in self.phases:
            if phase.requests > 0:
                lines.append(
                    f"| {phase.phase_name} | {phase.requests:,} | "
                    f"{phase.errors:,} | {phase.error_rate:.2f}% | "
                    f"{phase.avg_response_ms:.2f}ms |"
                )
        
        lines.append("")
        return "\n".join(lines)
    
    def _render_response_time_markdown(self) -> str:
        """Response Time 섹션 렌더링."""
        return f"""## ⏱️ Response Time (ms)

| Percentile | Value |
|------------|-------|
| Average | {self.metrics.avg_response_ms:.2f} |
| Min | {self.metrics.min_response_ms:.2f} |
| Max | {self.metrics.max_response_ms:.2f} |
| P50 | {self.metrics.p50_response_ms:.2f} |
| P95 | {self.metrics.p95_response_ms:.2f} |
| P99 | {self.metrics.p99_response_ms:.2f} |

"""
    
    def _render_result_markdown(self) -> str:
        """Result 섹션 렌더링."""
        status_icon = "✅" if self.metrics.passed else "❌"
        status_text = "PASSED" if self.metrics.passed else "FAILED"
        
        lines = [f"## 🎯 Result\n"]
        lines.append(f"**{status_icon} {status_text}**")
        
        if not self.metrics.passed and self.metrics.failure_reasons:
            lines.append("\n### Failure Reasons\n")
            for reason in self.metrics.failure_reasons:
                lines.append(f"- {reason}")
        
        lines.append("")
        return "\n".join(lines)
    
    # ============================================================
    # 추상 메서드 & 확장 포인트
    # ============================================================
    
    @property
    @abstractmethod
    def report_type(self) -> str:
        """보고서 타입 (예: 'base', 'selfhealing', 'platinum')."""
        pass
    
    def _generate_summary(self) -> Dict[str, Any]:
        """요약 정보 생성."""
        return {
            "passed": self.metrics.passed,
            "error_rate_percent": self.metrics.error_rate_percent,
            "p99_response_ms": self.metrics.p99_response_ms,
            "throughput_rps": self.metrics.throughput_rps,
            "failure_count": len(self.metrics.failure_reasons),
        }
    
    def _get_extended_data(self) -> Optional[Dict[str, Any]]:
        """
        서브클래스에서 확장 데이터 제공.
        
        to_dict() 결과에 병합됩니다.
        """
        return None
    
    def _get_extended_markdown(self) -> Optional[str]:
        """
        서브클래스에서 확장 Markdown 섹션 제공.
        
        to_markdown() 결과에 추가됩니다.
        """
        return None
    
    def _print_extended_console(self) -> None:
        """
        서브클래스에서 확장 콘솔 출력 제공.
        
        print_console()에서 호출됩니다.
        """
        pass


class SimpleReport(BaseReport):
    """단순 테스트용 기본 보고서 구현."""
    
    @property
    def report_type(self) -> str:
        return "base"

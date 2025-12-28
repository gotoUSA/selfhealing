"""
Load Test Platinum Report - Platinum Grade 보고서 클래스.

SLA Hard-Cap, Message Storm, Clock Skew, Cascading Failure 등
고급 테스트 시나리오 관련 보고서를 생성합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from typing import Dict, Any, Optional, List

from .selfhealing_report import SelfHealingReport
from .schema import (
    BaseMetrics,
    SelfHealingMetrics,
    PlatinumMetrics,
    PhaseMetrics,
    TimeseriesData,
)
from .report_config import ReportConfig


class PlatinumReport(SelfHealingReport):
    """
    Platinum Grade 스테이지용 보고서.
    
    SelfHealingReport를 상속하며, SLA Hard-Cap, Message Storm,
    Clock Skew, Cascading Failure 등 고급 테스트 섹션을 추가합니다.
    """
    
    def __init__(
        self,
        base_metrics: BaseMetrics,
        sh_metrics: SelfHealingMetrics,
        platinum_metrics: PlatinumMetrics,
        phases: Optional[List[PhaseMetrics]] = None,
        timeseries: Optional[TimeseriesData] = None,
        config: Optional[ReportConfig] = None,
    ):
        """
        PlatinumReport 초기화.
        
        Args:
            base_metrics: 기본 메트릭 데이터
            sh_metrics: Self-Healing 메트릭 데이터
            platinum_metrics: Platinum Grade 메트릭 데이터
            phases: 단계별 메트릭 (선택)
            timeseries: 시계열 데이터 (선택)
            config: 보고서 설정 (선택)
        """
        super().__init__(
            base_metrics=base_metrics,
            sh_metrics=sh_metrics,
            phases=phases,
            timeseries=timeseries,
            config=config,
        )
        self.platinum_metrics = platinum_metrics
    
    @property
    def report_type(self) -> str:
        return "platinum"
    
    # ============================================================
    # 확장 데이터
    # ============================================================
    
    def _get_extended_data(self) -> Dict[str, Any]:
        """Platinum 메트릭 데이터 포함."""
        parent_data = super()._get_extended_data()
        parent_data["platinum_metrics"] = self.platinum_metrics.to_dict()
        return parent_data
    
    def _generate_summary(self) -> Dict[str, Any]:
        """요약 정보에 Platinum 관련 정보 추가."""
        summary = super()._generate_summary()
        summary.update({
            "platinum_achieved": self.platinum_metrics.platinum_achieved,
            "sla_p99_passed": self.platinum_metrics.sla_p99_passed,
            "cascade_isolation_success": self.platinum_metrics.cascade_isolation_success,
            "retry_circuit_protected": self.platinum_metrics.retry_circuit_protected,
        })
        return summary
    
    # ============================================================
    # Markdown 출력 오버라이드
    # ============================================================
    
    def to_markdown(self) -> str:
        """Platinum Grade 전체 Markdown 보고서."""
        sections = [
            self._render_header_markdown(),
            self._render_platinum_grade_summary_markdown(),  # 먼저 Platinum 등급 표시
            self._render_executive_summary_markdown(),
            self._render_sla_hardcap_markdown(),
            self._render_message_storm_markdown(),
            self._render_clock_skew_markdown(),
            self._render_cascading_failure_markdown(),
            self._render_retry_storm_markdown(),
            self._render_v2_modules_markdown(),
            # Self-Healing 섹션
            self._render_circuit_breaker_markdown(),
            self._render_emergency_mode_markdown(),
            self._render_error_budget_markdown(),
            self._render_dlq_markdown(),
            self._render_chaos_markdown(),
            self._render_recovery_markdown(),
            # Phase Analysis
            self._render_phase_analysis_markdown(),
            self._render_response_time_markdown(),
            self._render_platinum_result_markdown(),
        ]
        
        return "\n".join(sections)
    
    def _render_platinum_grade_summary_markdown(self) -> str:
        """Platinum Grade 요약 섹션."""
        pm = self.platinum_metrics
        
        # 개별 판정
        sla_status = "✅ PASS" if pm.sla_p99_passed else "❌ FAIL"
        backpressure_status = "✅ PASS" if not pm.storm_main_logic_affected else "❌ FAIL"
        clock_status = "✅ PASS" if pm.clock_consistency_maintained else "❌ FAIL"
        cascade_status = "✅ PASS" if pm.cascade_isolation_success else "❌ FAIL"
        retry_status = "✅ PASS" if pm.retry_circuit_protected else "❌ FAIL"
        
        # 최종 등급
        grade = "**PLATINUM** 🏆" if pm.platinum_achieved else "**GOLD** 🥇"
        
        return f"""## 🏆 Platinum Grade Status

| 항목 | 결과 | 기준 |
|------|------|------|
| **SLA Hard-Cap** | {sla_status} | P99 ≤ {pm.sla_threshold_ms:.0f}ms |
| **Backpressure Control** | {backpressure_status} | 메인 로직 영향 없음 |
| **Clock Skew Resilience** | {clock_status} | 데이터 일관성 유지 |
| **Cascade Isolation** | {cascade_status} | 장애 격리 성공 |
| **Retry Storm Protection** | {retry_status} | 재시도 폭풍 방지 |
| **🏆 최종 등급** | {grade} | 모든 항목 PASS |

---
"""
    
    def _render_sla_hardcap_markdown(self) -> str:
        """SLA Hard-Cap 섹션."""
        pm = self.platinum_metrics
        sla_status = "✅ PASSED" if pm.sla_p99_passed else "❌ FAILED"
        
        return f"""## ⚖️ SLA Hard-Cap

| Metric | Value | Threshold |
|--------|-------|-----------|
| P99 Max | {pm.sla_p99_max_ms:.0f}ms | ≤ {pm.sla_threshold_ms:.0f}ms |
| P99 Violations | {pm.sla_p99_violations} | 0 |
| **Status** | {sla_status} | - |

"""
    
    def _render_message_storm_markdown(self) -> str:
        """Message Storm & Backpressure 섹션."""
        pm = self.platinum_metrics
        bp_status = "✅ Yes" if pm.storm_backpressure_activated else "❌ No"
        main_logic_status = "✅ No" if not pm.storm_main_logic_affected else "❌ Yes"
        
        return f"""## 📨 Message Storm & Backpressure

| Metric | Value |
|--------|-------|
| Messages Injected | {pm.storm_messages_injected} |
| Buffer Overflow | {pm.storm_buffer_overflow} |
| Backpressure Activated | {bp_status} |
| Main Logic Affected | {main_logic_status} |

"""
    
    def _render_clock_skew_markdown(self) -> str:
        """Clock Skew Attack 섹션."""
        pm = self.platinum_metrics
        consistency_status = "✅ Yes" if pm.clock_consistency_maintained else "❌ No"
        
        return f"""## ⏰ Clock Skew Attack

| Metric | Value |
|--------|-------|
| Attacks Executed | {pm.clock_attacks_executed} |
| Max Drift | {pm.clock_max_drift_sec:.2f}s |
| Consistency Maintained | {consistency_status} |

"""
    
    def _render_cascading_failure_markdown(self) -> str:
        """Cascading Failure 섹션."""
        pm = self.platinum_metrics
        isolation_status = "✅ Yes" if pm.cascade_isolation_success else "❌ No"
        
        return f"""## 🌊 Cascading Failure

| Metric | Value |
|--------|-------|
| Cascades Triggered | {pm.cascade_triggered} |
| Max Cascade Depth | {pm.cascade_max_depth} |
| Isolation Success | {isolation_status} |

"""
    
    def _render_retry_storm_markdown(self) -> str:
        """Retry Storm Prevention 섹션."""
        pm = self.platinum_metrics
        circuit_status = "✅ Yes" if pm.retry_circuit_protected else "❌ No"
        
        return f"""## 🔄 Retry Storm Prevention

| Metric | Value |
|--------|-------|
| Storms Detected | {pm.retry_storms_detected} |
| Circuit Protected | {circuit_status} |

"""
    
    def _render_v2_modules_markdown(self) -> str:
        """V2 Optimization Modules 섹션."""
        pm = self.platinum_metrics
        total_cache = pm.v2_cache_hits + pm.v2_cache_misses
        cache_hit_rate = (pm.v2_cache_hits / total_cache * 100) if total_cache > 0 else 0.0
        
        return f"""## 🚀 V2 Optimization Modules

| Metric | Value |
|--------|-------|
| Cache Hit Rate | {cache_hit_rate:.1f}% ({pm.v2_cache_hits}/{total_cache}) |
| Async Events | {pm.v2_async_events} |
| Jitter Applied | {pm.v2_jitter_applied} |

"""
    
    def _render_platinum_result_markdown(self) -> str:
        """Platinum 최종 결과 섹션."""
        pm = self.platinum_metrics
        
        status_icon = "✅" if self.metrics.passed else "❌"
        status_text = "PASSED" if self.metrics.passed else "FAILED"
        grade = "🏆 PLATINUM GRADE ACHIEVED" if pm.platinum_achieved else "🥇 GOLD GRADE"
        
        lines = [
            "## 🎯 Final Result\n",
            f"**{status_icon} {status_text}**\n",
            f"\n**Grade**: {grade}\n",
        ]
        
        if not self.metrics.passed and self.metrics.failure_reasons:
            lines.append("\n### Failure Reasons\n")
            for reason in self.metrics.failure_reasons:
                lines.append(f"- {reason}")
        
        lines.append("\n---\n")
        lines.append("📝 **Generated by Load Test Report Architecture v1.0.0**\n")
        
        return "\n".join(lines)
    
    # ============================================================
    # 콘솔 출력 확장
    # ============================================================
    
    def _print_extended_console(self) -> None:
        """Platinum + Self-Healing 콘솔 출력."""
        # Platinum Grade Summary 먼저 출력
        self._print_platinum_grade_console()
        
        # Self-Healing 섹션
        super()._print_extended_console()
    
    def _print_platinum_grade_console(self) -> None:
        """Platinum Grade 콘솔 출력."""
        pm = self.platinum_metrics
        
        print("\n" + "=" * 40)
        print("🏆 PLATINUM GRADE ANALYSIS")
        print("=" * 40)
        
        # SLA Hard-Cap
        print("\n⚖️ SLA Hard-Cap")
        print("-" * 40)
        print(f"  - P99 Max: {pm.sla_p99_max_ms:.0f}ms (Threshold: {pm.sla_threshold_ms:.0f}ms)")
        print(f"  - P99 Violations: {pm.sla_p99_violations}")
        sla_status = "✅ PASSED" if pm.sla_p99_passed else "❌ FAILED"
        print(f"  - Status: {sla_status}")
        
        # Message Storm & Backpressure
        print("\n📨 Message Storm & Backpressure")
        print("-" * 40)
        print(f"  - Messages Injected: {pm.storm_messages_injected}")
        print(f"  - Buffer Overflow: {pm.storm_buffer_overflow}")
        bp_status = "Yes" if pm.storm_backpressure_activated else "No"
        print(f"  - Backpressure Activated: {bp_status}")
        main_status = "❌ Yes" if pm.storm_main_logic_affected else "✅ No"
        print(f"  - Main Logic Affected: {main_status}")
        
        # Clock Skew Attack
        print("\n⏰ Clock Skew Attack")
        print("-" * 40)
        print(f"  - Attacks Executed: {pm.clock_attacks_executed}")
        print(f"  - Max Drift: {pm.clock_max_drift_sec:.2f}s")
        consistency = "✅ Yes" if pm.clock_consistency_maintained else "❌ No"
        print(f"  - Consistency Maintained: {consistency}")
        
        # Cascading Failure
        print("\n🌊 Cascading Failure")
        print("-" * 40)
        print(f"  - Cascades Triggered: {pm.cascade_triggered}")
        print(f"  - Max Cascade Depth: {pm.cascade_max_depth}")
        isolation = "✅ Yes" if pm.cascade_isolation_success else "❌ No"
        print(f"  - Isolation Success: {isolation}")
        
        # Retry Storm Prevention
        print("\n🔄 Retry Storm Prevention")
        print("-" * 40)
        print(f"  - Storms Detected: {pm.retry_storms_detected}")
        circuit = "✅ Yes" if pm.retry_circuit_protected else "❌ No"
        print(f"  - Circuit Protected: {circuit}")
        
        # V2 Modules
        print("\n🚀 V2 Optimization Modules")
        print("-" * 40)
        total_cache = pm.v2_cache_hits + pm.v2_cache_misses
        cache_hit_rate = (pm.v2_cache_hits / total_cache * 100) if total_cache > 0 else 0.0
        print(f"  - Cache Hit Rate: {cache_hit_rate:.1f}% ({pm.v2_cache_hits}/{total_cache})")
        print(f"  - Async Events: {pm.v2_async_events}")
        print(f"  - Jitter Applied: {pm.v2_jitter_applied}")
        
        # Final Grade
        print("\n" + "=" * 40)
        print("🏆 FINAL PLATINUM GRADE VERDICT")
        print("=" * 40)
        
        sla_pass = "✅ PASS" if pm.sla_p99_passed else "❌ FAIL"
        bp_pass = "✅ PASS" if not pm.storm_main_logic_affected else "❌ FAIL"
        clock_pass = "✅ PASS" if pm.clock_consistency_maintained else "❌ FAIL"
        cascade_pass = "✅ PASS" if pm.cascade_isolation_success else "❌ FAIL"
        retry_pass = "✅ PASS" if pm.retry_circuit_protected else "❌ FAIL"
        
        print(f"  SLA Hard-Cap: {sla_pass}")
        print(f"  Backpressure Control: {bp_pass}")
        print(f"  Clock Skew Resilience: {clock_pass}")
        print(f"  Cascade Isolation: {cascade_pass}")
        print(f"  Retry Storm Protection: {retry_pass}")
        
        grade = "🏆 PLATINUM GRADE ACHIEVED!" if pm.platinum_achieved else "🥇 GOLD GRADE (일부 미달성)"
        print(f"\n  {grade}")

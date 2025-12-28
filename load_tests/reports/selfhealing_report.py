"""
Load Test Self-Healing Report - Self-Healing 보고서 클래스.

Circuit Breaker, Emergency Mode, Error Budget, DLQ 등
자가 치유 기능 관련 보고서를 생성합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from typing import Dict, Any, Optional, List

from .base_report import BaseReport
from .schema import (
    BaseMetrics,
    SelfHealingMetrics,
    PhaseMetrics,
    TimeseriesData,
)
from .report_config import ReportConfig


class SelfHealingReport(BaseReport):
    """
    Self-Healing 스테이지용 보고서.
    
    BaseReport를 상속하며, Circuit Breaker, Emergency Mode,
    Error Budget, DLQ 등 자가 치유 관련 섹션을 추가합니다.
    """
    
    def __init__(
        self,
        base_metrics: BaseMetrics,
        sh_metrics: SelfHealingMetrics,
        phases: Optional[List[PhaseMetrics]] = None,
        timeseries: Optional[TimeseriesData] = None,
        config: Optional[ReportConfig] = None,
    ):
        """
        SelfHealingReport 초기화.
        
        Args:
            base_metrics: 기본 메트릭 데이터
            sh_metrics: Self-Healing 메트릭 데이터
            phases: 단계별 메트릭 (선택)
            timeseries: 시계열 데이터 (선택)
            config: 보고서 설정 (선택)
        """
        super().__init__(
            metrics=base_metrics,
            phases=phases,
            timeseries=timeseries,
            config=config,
        )
        self.sh_metrics = sh_metrics
    
    @property
    def report_type(self) -> str:
        return "selfhealing"
    
    # ============================================================
    # 확장 데이터
    # ============================================================
    
    def _get_extended_data(self) -> Dict[str, Any]:
        """Self-Healing 메트릭 데이터 반환."""
        return {
            "selfhealing_metrics": self.sh_metrics.to_dict(),
        }
    
    def _generate_summary(self) -> Dict[str, Any]:
        """요약 정보에 Self-Healing 관련 정보 추가."""
        summary = super()._generate_summary()
        summary.update({
            "cb_open_count": self.sh_metrics.cb_open_count,
            "emergency_max_level": self.sh_metrics.emergency_max_level,
            "error_budget_exhausted": self.sh_metrics.error_budget_exhausted,
            "recovery_sla_passed": self.sh_metrics.recovery_sla_passed,
        })
        return summary
    
    # ============================================================
    # 확장 Markdown
    # ============================================================
    
    def _get_extended_markdown(self) -> str:
        """Self-Healing 전용 Markdown 섹션."""
        sections = [
            self._render_circuit_breaker_markdown(),
            self._render_emergency_mode_markdown(),
            self._render_error_budget_markdown(),
            self._render_dlq_markdown(),
            self._render_chaos_markdown(),
            self._render_recovery_markdown(),
        ]
        return "\n".join(sections)
    
    def _render_circuit_breaker_markdown(self) -> str:
        """Circuit Breaker 섹션 렌더링."""
        sh = self.sh_metrics
        services = ", ".join(sh.cb_services_affected) if sh.cb_services_affected else "None"
        recovery = f"{sh.cb_recovery_ms:.0f}ms" if sh.cb_recovery_ms else "N/A"
        
        return f"""## 🔌 Circuit Breaker Analysis

| Metric | Value |
|--------|-------|
| Open Count | {sh.cb_open_count} |
| Close Count | {sh.cb_close_count} |
| Half-Open Count | {sh.cb_half_open_count} |
| Recovery Latency | {recovery} |
| Services Affected | {services} |

"""
    
    def _render_emergency_mode_markdown(self) -> str:
        """Emergency Mode 섹션 렌더링."""
        sh = self.sh_metrics
        
        return f"""## 🚨 Emergency Mode Analysis

| Metric | Value |
|--------|-------|
| Triggered Count | {sh.emergency_triggered_count} |
| Released Count | {sh.emergency_released_count} |
| Max Level Reached | LEVEL_{sh.emergency_max_level} |

"""
    
    def _render_error_budget_markdown(self) -> str:
        """Error Budget 섹션 렌더링."""
        sh = self.sh_metrics
        initial = f"{sh.error_budget_initial:.1f}%" if sh.error_budget_initial is not None else "N/A"
        min_val = f"{sh.error_budget_min:.1f}%" if sh.error_budget_min is not None else "N/A"
        exhausted = "✅ Yes" if sh.error_budget_exhausted else "❌ No"
        recovered = "✅ Yes" if sh.error_budget_recovered else "❌ No"
        
        return f"""## 💰 Error Budget Analysis

| Metric | Value |
|--------|-------|
| Initial Remaining | {initial} |
| Min Remaining | {min_val} |
| Exhausted | {exhausted} |
| Recovered | {recovered} |

"""
    
    def _render_dlq_markdown(self) -> str:
        """DLQ 섹션 렌더링."""
        sh = self.sh_metrics
        
        return f"""## 📥 DLQ (Dead Letter Queue) Analysis

| Metric | Value |
|--------|-------|
| Max Count | {sh.dlq_max_count} |
| Replay Success | {sh.dlq_replay_success} |
| Replay Fail | {sh.dlq_replay_fail} |

"""
    
    def _render_chaos_markdown(self) -> str:
        """Chaos Injection 섹션 렌더링."""
        sh = self.sh_metrics
        targets = ", ".join(sh.kill_switch_targets) if sh.kill_switch_targets else "None"
        
        return f"""## 💥 Chaos & Kill Switch Analysis

### Chaos Injection
| Metric | Value |
|--------|-------|
| Failures Injected | {sh.chaos_failures_injected} |
| CB Triggers | {sh.chaos_cb_triggers} |
| Recovery Triggers | {sh.chaos_recovery_triggers} |

### Kill Switch
| Metric | Value |
|--------|-------|
| Activated Count | {sh.kill_switch_activated} |
| Deactivated Count | {sh.kill_switch_deactivated} |
| Targets | {targets} |

"""
    
    def _render_recovery_markdown(self) -> str:
        """Recovery 섹션 렌더링."""
        sh = self.sh_metrics
        latency = f"{sh.recovery_latency_sec:.1f}s" if sh.recovery_latency_sec is not None else "N/A"
        sla_status = "✅ PASSED" if sh.recovery_sla_passed else "❌ FAILED"
        
        return f"""## 🔄 Recovery Analysis

| Metric | Value |
|--------|-------|
| Recovery Latency | {latency} |
| SLA (< 2min) | {sla_status} |

"""
    
    # ============================================================
    # 확장 콘솔 출력
    # ============================================================
    
    def _print_extended_console(self) -> None:
        """Self-Healing 콘솔 출력."""
        sh = self.sh_metrics
        
        # Circuit Breaker
        print("\n🔌 Circuit Breaker Analysis")
        print("-" * 40)
        print(f"  - Open Count: {sh.cb_open_count}")
        print(f"  - Close Count: {sh.cb_close_count}")
        print(f"  - Half-Open Count: {sh.cb_half_open_count}")
        services = ", ".join(sh.cb_services_affected) if sh.cb_services_affected else "None"
        print(f"  - Services Affected: {services}")
        if sh.cb_recovery_ms:
            print(f"  - Recovery Latency: {sh.cb_recovery_ms:.0f}ms")
        
        # Emergency Mode
        print("\n🚨 Emergency Mode Analysis")
        print("-" * 40)
        print(f"  - Triggered Count: {sh.emergency_triggered_count}")
        print(f"  - Released Count: {sh.emergency_released_count}")
        print(f"  - Max Level Reached: LEVEL_{sh.emergency_max_level}")
        
        # Error Budget
        print("\n💰 Error Budget Analysis")
        print("-" * 40)
        initial = f"{sh.error_budget_initial:.1f}%" if sh.error_budget_initial is not None else "N/A"
        min_val = f"{sh.error_budget_min:.1f}%" if sh.error_budget_min is not None else "N/A"
        print(f"  - Initial Remaining: {initial}")
        print(f"  - Min Remaining: {min_val}")
        print(f"  - Exhausted: {'Yes' if sh.error_budget_exhausted else 'No'}")
        print(f"  - Recovered: {'Yes' if sh.error_budget_recovered else 'No'}")
        
        # DLQ
        print("\n📥 DLQ Analysis")
        print("-" * 40)
        print(f"  - Max Count: {sh.dlq_max_count}")
        print(f"  - Replay Success: {sh.dlq_replay_success}")
        print(f"  - Replay Fail: {sh.dlq_replay_fail}")
        
        # Chaos Injection
        print("\n💥 Chaos Injection Analysis")
        print("-" * 40)
        print(f"  - Failures Injected: {sh.chaos_failures_injected}")
        print(f"  - CB Triggers: {sh.chaos_cb_triggers}")
        print(f"  - Recovery Triggers: {sh.chaos_recovery_triggers}")
        
        # Kill Switch
        print("\n🔌 Kill Switch Analysis")
        print("-" * 40)
        print(f"  - Activated Count: {sh.kill_switch_activated}")
        print(f"  - Deactivated Count: {sh.kill_switch_deactivated}")
        targets = ", ".join(sh.kill_switch_targets) if sh.kill_switch_targets else "None"
        print(f"  - Targets: {targets}")
        
        # Recovery
        print("\n🔄 Recovery Analysis")
        print("-" * 40)
        latency = f"{sh.recovery_latency_sec:.1f}s" if sh.recovery_latency_sec else "N/A"
        print(f"  - Recovery Latency: {latency}")
        sla_status = "PASSED" if sh.recovery_sla_passed else "FAILED"
        print(f"  - SLA (< 2min): {sla_status}")

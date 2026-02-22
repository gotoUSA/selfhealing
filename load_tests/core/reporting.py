"""
테스트 보고서 생성 유틸리티

JSON, Markdown, HTML 형식의 테스트 결과 보고서를 생성합니다.
"""
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional


class ReportGenerator:
    """
    통합 보고서 생성기.
    
    테스트 결과를 다양한 형식으로 저장합니다.
    
    Usage:
        reporter = ReportGenerator("stage13")
        reporter.save_json(stats.to_dict())
        reporter.save_markdown(stats.to_dict())
        reporter.save_html(stats.to_dict())
    """
    
    def __init__(
        self, 
        stage_name: str, 
        results_dir: str = "load_tests/results",
        auto_create_dirs: bool = True
    ):
        """
        Args:
            stage_name: 스테이지 이름 (예: "stage13", "stage12_spike")
            results_dir: 결과 저장 기본 디렉토리
            auto_create_dirs: 디렉토리 자동 생성 여부
        """
        self.stage_name = stage_name
        self.results_dir = Path(results_dir) / stage_name
        self.timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.date_str = datetime.now().strftime("%Y-%m-%d")
        
        if auto_create_dirs:
            self.results_dir.mkdir(parents=True, exist_ok=True)
            (self.results_dir / "archive").mkdir(exist_ok=True)
            (self.results_dir / "reports").mkdir(exist_ok=True)
    
    def save_json(
        self, 
        stats: Dict[str, Any],
        filename: Optional[str] = None,
        archive: bool = True
    ) -> str:
        """
        JSON 결과 저장.
        
        Args:
            stats: 통계 딕셔너리
            filename: 커스텀 파일명 (없으면 자동 생성)
            archive: 이전 파일 아카이브 여부
            
        Returns:
            저장된 파일 경로
        """
        if filename:
            path = self.results_dir / filename
        else:
            path = self.results_dir / f"{self.stage_name}_{self.timestamp}.json"
        
        # 메타데이터 추가
        stats_with_meta = {
            "_meta": {
                "stage": self.stage_name,
                "timestamp": datetime.now().isoformat(),
                "generator": "ReportGenerator v1.0",
            },
            **stats
        }
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(stats_with_meta, f, indent=2, ensure_ascii=False, default=str)
        
        # latest 심볼릭 링크/복사
        self._create_latest_link(path, "json")
        
        return str(path)
    
    def save_markdown(
        self, 
        stats: Dict[str, Any],
        template: Optional[str] = None,
        filename: Optional[str] = None
    ) -> str:
        """
        Markdown 보고서 저장.
        
        Args:
            stats: 통계 딕셔너리
            template: 커스텀 템플릿 (없으면 기본 템플릿 사용)
            filename: 커스텀 파일명
            
        Returns:
            저장된 파일 경로
        """
        if filename:
            path = self.results_dir / "reports" / filename
        else:
            path = self.results_dir / "reports" / f"{self.stage_name}_{self.date_str}.md"
        
        content = template or self._default_markdown_template(stats)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        
        return str(path)
    
    def save_html(
        self, 
        stats: Dict[str, Any],
        filename: Optional[str] = None,
        include_charts: bool = True
    ) -> str:
        """
        HTML 보고서 저장.
        
        Args:
            stats: 통계 딕셔너리
            filename: 커스텀 파일명
            include_charts: 차트 포함 여부
            
        Returns:
            저장된 파일 경로
        """
        if filename:
            path = self.results_dir / "reports" / filename
        else:
            path = self.results_dir / "reports" / f"{self.stage_name}_report.html"
        
        html = self._generate_html(stats, include_charts)
        
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        
        return str(path)
    
    def save_all(self, stats: Dict[str, Any]) -> Dict[str, str]:
        """
        모든 형식의 보고서 저장.
        
        Args:
            stats: 통계 딕셔너리
            
        Returns:
            {형식: 파일경로} 딕셔너리
        """
        return {
            "json": self.save_json(stats),
            "markdown": self.save_markdown(stats),
            "html": self.save_html(stats),
        }
    
    def _create_latest_link(self, source_path: Path, extension: str) -> None:
        """latest 파일 생성 (Windows 호환)."""
        latest_path = self.results_dir / f"latest.{extension}"
        
        # Windows에서는 심볼릭 링크 대신 복사
        try:
            import shutil
            if latest_path.exists():
                latest_path.unlink()
            shutil.copy2(source_path, latest_path)
        except Exception:
            pass
    
    def _default_markdown_template(self, stats: Dict[str, Any]) -> str:
        """기본 Markdown 템플릿."""
        stage_title = self.stage_name.replace("_", " ").title()
        
        # 요약 계산
        summary = stats.get("summary", {})
        total = summary.get("total_requests", stats.get("passed", 0) + stats.get("failed", 0))
        passed = summary.get("passed", stats.get("passed", 0))
        failed = summary.get("failed", stats.get("failed", 0))
        success_rate = summary.get("success_rate", 
                                  passed / max(1, total) * 100 if total else 0)
        avg_response = summary.get("avg_response_time_ms", 0)
        p95_response = summary.get("p95_response_time_ms", 0)
        p99_response = summary.get("p99_response_time_ms", 0)
        
        # 시나리오 테이블
        scenarios_table = self._format_scenarios_table(stats.get("scenarios", {}))
        
        # CB 통계 (있는 경우)
        cb_section = ""
        if "circuit_breaker" in stats:
            cb = stats["circuit_breaker"]
            cb_section = f"""
## 🔌 Circuit Breaker 통계

| 항목 | 값 |
|------|-----|
| 총 Open 횟수 | {cb.get('total_opens', 0)} |
| 총 Close 횟수 | {cb.get('total_closes', 0)} |
| Half-Open 횟수 | {cb.get('total_half_opens', 0)} |
| 영향받은 서비스 | {', '.join(cb.get('services_affected', [])) or 'N/A'} |
"""
        
        # Emergency 통계 (있는 경우)
        emergency_section = ""
        if "emergency" in stats:
            em = stats["emergency"]
            emergency_section = f"""
## 🚨 Emergency 통계

| 항목 | 값 |
|------|-----|
| 최대 레벨 도달 | {em.get('max_level_reached', 0)} |
| 복구 성공 | {em.get('recovery_successes', 0)} |
| 복구 실패 | {em.get('recovery_failures', 0)} |
"""
        
        # Chaos 통계 (있는 경우)
        chaos_section = ""
        if "chaos" in stats:
            chaos = stats["chaos"]
            chaos_section = f"""
## 💥 Chaos Engineering 통계

| 항목 | 값 |
|------|-----|
| 장애 주입 횟수 | {chaos.get('failures_injected', 0)} |
| 지연 주입 횟수 | {chaos.get('latency_injections', 0)} |
| Blast Radius 테스트 | {chaos.get('blast_radius_tests', 0)} |
"""
        
        # SLA 통계 (있는 경우)
        sla_section = ""
        if "sla" in stats:
            sla = stats["sla"]
            sla_section = f"""
## 📏 SLA 준수 현황

| 항목 | 위반 횟수 | 상태 |
|------|----------|------|
| P99 응답시간 | {sla.get('p99_breaches', 0)} | {'🔴' if sla.get('p99_breaches', 0) > 0 else '🟢'} |
| P95 응답시간 | {sla.get('p95_breaches', 0)} | {'🔴' if sla.get('p95_breaches', 0) > 0 else '🟢'} |
| 에러율 | {sla.get('error_rate_breaches', 0)} | {'🔴' if sla.get('error_rate_breaches', 0) > 0 else '🟢'} |
"""
        
        return f"""# {stage_title} 테스트 결과

📅 **테스트 일시**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
🏷️ **스테이지**: {self.stage_name}  
📊 **보고서 버전**: v1.0

---

## 📊 Summary

| 항목 | 값 |
|------|-----|
| 총 요청 수 | {total:,} |
| 성공 | {passed:,} |
| 실패 | {failed:,} |
| 성공률 | {success_rate:.1f}% |
| 평균 응답시간 | {avg_response:.1f}ms |
| P95 응답시간 | {p95_response:.1f}ms |
| P99 응답시간 | {p99_response:.1f}ms |

### 결과 판정

{'✅ **PASS** - 모든 테스트 통과' if failed == 0 else f'⚠️ **WARNING** - {failed}건 실패 발생' if success_rate >= 95 else f'❌ **FAIL** - 성공률 {success_rate:.1f}% (목표: 95%)'}

---

## 📋 시나리오별 결과

{scenarios_table}
{cb_section}{emergency_section}{chaos_section}{sla_section}
---

## 🔧 Healing Actions

{self._format_healing_actions(stats.get('healing_actions', {}))}

---

*Generated at {datetime.now().isoformat()} by ReportGenerator*
"""
    
    def _format_scenarios_table(self, scenarios: Dict) -> str:
        """시나리오 테이블 포맷팅."""
        if not scenarios:
            return "*데이터 없음*"
        
        rows = [
            "| 시나리오 | 요청 수 | 성공 | 실패 | 성공률 | 평균 응답(ms) | P95(ms) | P99(ms) |",
            "|----------|---------|------|------|--------|---------------|---------|---------|"
        ]
        
        for name, data in scenarios.items():
            total = data.get('count', data.get('success', 0) + data.get('failed', 0))
            success = data.get('success', 0)
            failed = data.get('failed', 0)
            rate = data.get('success_rate', success / max(1, total) * 100)
            avg_rt = data.get('avg_response_time_ms', 0)
            p95_rt = data.get('p95_response_time_ms', 0)
            p99_rt = data.get('p99_response_time_ms', 0)
            
            status_icon = "🟢" if rate >= 99 else "🟡" if rate >= 95 else "🔴"
            
            rows.append(
                f"| {name} | {total:,} | {success:,} | {failed:,} | "
                f"{status_icon} {rate:.1f}% | {avg_rt:.1f} | {p95_rt:.1f} | {p99_rt:.1f} |"
            )
        
        return "\n".join(rows)
    
    def _format_healing_actions(self, actions: Dict[str, int]) -> str:
        """힐링 액션 포맷팅."""
        if not actions:
            return "*힐링 액션 없음*"
        
        rows = ["| 액션 | 횟수 |", "|------|------|"]
        for action, count in sorted(actions.items(), key=lambda x: -x[1]):
            rows.append(f"| {action} | {count:,} |")
        
        return "\n".join(rows)
    
    def _generate_html(self, stats: Dict[str, Any], include_charts: bool = True) -> str:
        """HTML 보고서 생성."""
        stage_title = self.stage_name.replace("_", " ").title()
        
        # 요약 데이터
        summary = stats.get("summary", {})
        total = summary.get("total_requests", stats.get("passed", 0) + stats.get("failed", 0))
        passed = summary.get("passed", stats.get("passed", 0))
        failed = summary.get("failed", stats.get("failed", 0))
        success_rate = summary.get("success_rate", 
                                  passed / max(1, total) * 100 if total else 0)
        
        # 시나리오 테이블 HTML
        scenarios_html = self._generate_scenarios_html(stats.get("scenarios", {}))
        
        # 차트 스크립트 (Chart.js)
        chart_script = ""
        if include_charts:
            chart_script = self._generate_chart_script(stats)
        
        return f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{stage_title} Test Report</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --primary-color: #4CAF50;
            --danger-color: #f44336;
            --warning-color: #ff9800;
            --bg-color: #f5f5f5;
            --card-bg: #ffffff;
            --text-color: #333333;
        }}
        
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background-color: var(--bg-color);
            color: var(--text-color);
            line-height: 1.6;
            padding: 20px;
        }}
        
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        
        h1 {{
            color: var(--primary-color);
            margin-bottom: 10px;
            font-size: 2rem;
        }}
        
        .meta {{
            color: #666;
            margin-bottom: 20px;
        }}
        
        .card {{
            background: var(--card-bg);
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            padding: 20px;
            margin-bottom: 20px;
        }}
        
        .card h2 {{
            color: var(--primary-color);
            margin-bottom: 15px;
            font-size: 1.3rem;
            border-bottom: 2px solid var(--primary-color);
            padding-bottom: 10px;
        }}
        
        .stats-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}
        
        .stat-box {{
            text-align: center;
            padding: 15px;
            border-radius: 8px;
            background: var(--bg-color);
        }}
        
        .stat-box .value {{
            font-size: 2rem;
            font-weight: bold;
            color: var(--primary-color);
        }}
        
        .stat-box .label {{
            color: #666;
            font-size: 0.9rem;
        }}
        
        .stat-box.success .value {{ color: var(--primary-color); }}
        .stat-box.danger .value {{ color: var(--danger-color); }}
        .stat-box.warning .value {{ color: var(--warning-color); }}
        
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}
        
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        
        th {{
            background-color: var(--primary-color);
            color: white;
            font-weight: 600;
        }}
        
        tr:hover {{
            background-color: #f5f5f5;
        }}
        
        .status-pass {{ color: var(--primary-color); font-weight: bold; }}
        .status-fail {{ color: var(--danger-color); font-weight: bold; }}
        .status-warn {{ color: var(--warning-color); font-weight: bold; }}
        
        .chart-container {{
            max-width: 600px;
            margin: 20px auto;
        }}
        
        .result-badge {{
            display: inline-block;
            padding: 8px 16px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 1.1rem;
        }}
        
        .result-badge.pass {{
            background-color: #e8f5e9;
            color: var(--primary-color);
        }}
        
        .result-badge.fail {{
            background-color: #ffebee;
            color: var(--danger-color);
        }}
        
        .footer {{
            text-align: center;
            color: #666;
            margin-top: 30px;
            padding: 20px;
            border-top: 1px solid #ddd;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📊 {stage_title} Test Report</h1>
        <p class="meta">
            Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | 
            Stage: {self.stage_name}
        </p>
        
        <div class="card">
            <h2>📈 Summary</h2>
            <div class="stats-grid">
                <div class="stat-box">
                    <div class="value">{total:,}</div>
                    <div class="label">Total Requests</div>
                </div>
                <div class="stat-box success">
                    <div class="value">{passed:,}</div>
                    <div class="label">Passed</div>
                </div>
                <div class="stat-box danger">
                    <div class="value">{failed:,}</div>
                    <div class="label">Failed</div>
                </div>
                <div class="stat-box {'success' if success_rate >= 95 else 'warning' if success_rate >= 80 else 'danger'}">
                    <div class="value">{success_rate:.1f}%</div>
                    <div class="label">Success Rate</div>
                </div>
            </div>
            <div style="text-align: center; margin-top: 20px;">
                <span class="result-badge {'pass' if success_rate >= 95 else 'fail'}">
                    {'✅ PASS' if success_rate >= 95 else '❌ FAIL'}
                </span>
            </div>
        </div>
        
        <div class="card">
            <h2>📋 Scenarios</h2>
            {scenarios_html}
        </div>
        
        {'<div class="card"><h2>📊 Charts</h2><div class="chart-container"><canvas id="resultsChart"></canvas></div></div>' if include_charts else ''}
        
        <div class="footer">
            <p>Generated by ReportGenerator v1.0</p>
            <p>{datetime.now().isoformat()}</p>
        </div>
    </div>
    
    {chart_script}
</body>
</html>"""
    
    def _generate_scenarios_html(self, scenarios: Dict) -> str:
        """시나리오 HTML 테이블 생성."""
        if not scenarios:
            return "<p><em>No scenario data available</em></p>"
        
        rows = []
        for name, data in scenarios.items():
            total = data.get('count', data.get('success', 0) + data.get('failed', 0))
            success = data.get('success', 0)
            failed = data.get('failed', 0)
            rate = data.get('success_rate', success / max(1, total) * 100)
            avg_rt = data.get('avg_response_time_ms', 0)
            
            status_class = "status-pass" if rate >= 99 else "status-warn" if rate >= 95 else "status-fail"
            
            rows.append(f"""
                <tr>
                    <td>{name}</td>
                    <td>{total:,}</td>
                    <td>{success:,}</td>
                    <td>{failed:,}</td>
                    <td class="{status_class}">{rate:.1f}%</td>
                    <td>{avg_rt:.1f}ms</td>
                </tr>
            """)
        
        return f"""
            <table>
                <thead>
                    <tr>
                        <th>Scenario</th>
                        <th>Requests</th>
                        <th>Success</th>
                        <th>Failed</th>
                        <th>Success Rate</th>
                        <th>Avg Response</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(rows)}
                </tbody>
            </table>
        """
    
    def _generate_chart_script(self, stats: Dict[str, Any]) -> str:
        """Chart.js 스크립트 생성."""
        passed = stats.get("passed", 0)
        failed = stats.get("failed", 0)
        
        return f"""
    <script>
        const ctx = document.getElementById('resultsChart');
        if (ctx) {{
            new Chart(ctx, {{
                type: 'doughnut',
                data: {{
                    labels: ['Passed', 'Failed'],
                    datasets: [{{
                        data: [{passed}, {failed}],
                        backgroundColor: ['#4CAF50', '#f44336'],
                        borderWidth: 2,
                        borderColor: '#ffffff'
                    }}]
                }},
                options: {{
                    responsive: true,
                    plugins: {{
                        legend: {{
                            position: 'bottom'
                        }},
                        title: {{
                            display: true,
                            text: 'Test Results Distribution'
                        }}
                    }}
                }}
            }});
        }}
    </script>
        """

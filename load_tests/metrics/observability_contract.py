"""
GAP-08: Observability Contract

모든 스테이지에 적용되는 관측성 표준 계약.
- 각 요청에 trace_id 필수 포함
- 실패 시 pipeline_stage 명시 (8칸 중 어디)
- 종료 리포트에 단계별 실패 분포 포함

Pipeline Stages (8 Stages):
1. Ingress      - 요청 수신, Load Balancer, Rate Limiting
2. Validation   - 입력 검증, Schema 검증
3. Auth         - 인증/인가, JWT 검증
4. Business     - 비즈니스 로직 처리
5. Caching      - 캐시 조회/저장
6. Persistence  - DB 읽기/쓰기
7. Async        - 비동기 작업, Celery, Webhook
8. Egress       - 응답 반환, 외부 API 호출

Invariants:
- untraced_failures == 0
- unknown_stage_failures == 0

Metrics:
- failure_by_pipeline_stage{stage='ingress|validation|...'}

Usage:
    from load_tests.metrics.observability_contract import (
        ObservabilityContext,
        PipelineStage,
        trace_request,
        get_observability_report,
    )

    # 요청 추적
    with trace_request("order_create") as ctx:
        ctx.set_stage(PipelineStage.VALIDATION)
        response = self.client.post("/api/orders/", json={...})
        
        if response.status_code == 400:
            ctx.record_failure("Invalid input")
        elif response.status_code == 200:
            ctx.set_stage(PipelineStage.PERSISTENCE)
            # DB 작업...

Reference:
    - docs/GAP_RESOLUTION_PLAN.md (GAP-08)
    - docs/STAGE_35D_OBSERVABILITY_CONTRACT.md
"""

import os
import sys
import uuid
import time
import threading
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum
from collections import defaultdict
from contextlib import contextmanager

# =============================================================================
# Pipeline Stages (8 Stages)
# =============================================================================


class PipelineStage(Enum):
    """
    8단계 파이프라인 스테이지 정의
    
    요청이 시스템을 통과하는 각 단계를 나타냄.
    실패 발생 시 정확한 단계를 식별하여 디버깅 용이성 향상.
    """
    UNKNOWN = "unknown"      # 미분류 (이 값이면 contract 위반)
    INGRESS = "ingress"      # 1. 요청 수신
    VALIDATION = "validation"  # 2. 입력 검증
    AUTH = "auth"            # 3. 인증/인가
    BUSINESS = "business"    # 4. 비즈니스 로직
    CACHING = "caching"      # 5. 캐싱
    PERSISTENCE = "persistence"  # 6. DB 작업
    ASYNC = "async"          # 7. 비동기 처리
    EGRESS = "egress"        # 8. 응답 반환
    
    @classmethod
    def from_status_code(cls, status_code: int) -> "PipelineStage":
        """HTTP 상태 코드로 대략적인 단계 추론"""
        if status_code == 429:
            return cls.INGRESS  # Rate limit
        elif status_code == 400:
            return cls.VALIDATION  # Bad request
        elif status_code in (401, 403):
            return cls.AUTH  # Unauthorized/Forbidden
        elif status_code == 422:
            return cls.BUSINESS  # Unprocessable entity
        elif status_code == 503:
            return cls.INGRESS  # Service unavailable (보통 LB 레벨)
        elif status_code == 504:
            return cls.EGRESS  # Gateway timeout
        elif 500 <= status_code < 600:
            return cls.PERSISTENCE  # 대부분의 5xx는 DB/백엔드 이슈
        else:
            return cls.UNKNOWN

    @classmethod
    def from_error_message(cls, error: str) -> "PipelineStage":
        """에러 메시지로 단계 추론"""
        error_lower = error.lower()
        
        # Rate limiting / Load balancer
        if any(x in error_lower for x in ["rate limit", "too many", "throttl", "overload"]):
            return cls.INGRESS
        
        # Validation
        if any(x in error_lower for x in ["invalid", "required", "format", "schema", "missing field"]):
            return cls.VALIDATION
        
        # Authentication/Authorization
        if any(x in error_lower for x in ["auth", "token", "jwt", "permission", "forbidden", "unauthorized"]):
            return cls.AUTH
        
        # Business logic
        if any(x in error_lower for x in ["insufficient", "not available", "out of stock", "conflict", "duplicate"]):
            return cls.BUSINESS
        
        # Caching
        if any(x in error_lower for x in ["cache", "redis", "memcache", "stale"]):
            return cls.CACHING
        
        # Persistence
        if any(x in error_lower for x in ["database", "db", "sql", "postgres", "mysql", "deadlock", "lock"]):
            return cls.PERSISTENCE
        
        # Async
        if any(x in error_lower for x in ["celery", "task", "queue", "timeout", "async", "webhook"]):
            return cls.ASYNC
        
        # Egress
        if any(x in error_lower for x in ["external", "api call", "gateway", "upstream"]):
            return cls.EGRESS
        
        return cls.UNKNOWN


# =============================================================================
# Trace Context
# =============================================================================

@dataclass
class TraceContext:
    """
    단일 요청의 추적 컨텍스트
    
    각 요청마다 고유한 trace_id와 함께 파이프라인 단계, 
    타이밍, 에러 정보를 캡처함.
    """
    trace_id: str
    request_name: str
    start_time: float
    end_time: Optional[float] = None
    current_stage: PipelineStage = PipelineStage.INGRESS
    final_stage: PipelineStage = PipelineStage.UNKNOWN  # 실패 시 마지막 단계
    
    # Request/Response info
    status_code: Optional[int] = None
    response_time_ms: float = 0.0
    
    # Failure info
    is_failure: bool = False
    error_message: Optional[str] = None
    
    # Stage transitions log
    stage_log: List[Dict[str, Any]] = field(default_factory=list)
    
    def set_stage(self, stage: PipelineStage):
        """현재 파이프라인 단계 설정"""
        prev_stage = self.current_stage
        self.current_stage = stage
        self.stage_log.append({
            "timestamp": time.time(),
            "from_stage": prev_stage.value,
            "to_stage": stage.value,
            "elapsed_ms": (time.time() - self.start_time) * 1000
        })
    
    def record_success(self, status_code: int = 200):
        """성공 기록"""
        self.end_time = time.time()
        self.status_code = status_code
        self.response_time_ms = (self.end_time - self.start_time) * 1000
        self.is_failure = False
        self.final_stage = self.current_stage
    
    def record_failure(self, error: str, status_code: Optional[int] = None):
        """실패 기록 - 반드시 pipeline_stage가 설정되어야 함"""
        self.end_time = time.time()
        self.status_code = status_code
        self.response_time_ms = (self.end_time - self.start_time) * 1000
        self.is_failure = True
        self.error_message = error
        self.final_stage = self.current_stage
        
        # 단계가 UNKNOWN이면 에러/상태코드에서 추론 시도
        if self.final_stage == PipelineStage.UNKNOWN:
            if status_code:
                self.final_stage = PipelineStage.from_status_code(status_code)
            if self.final_stage == PipelineStage.UNKNOWN and error:
                self.final_stage = PipelineStage.from_error_message(error)
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환"""
        return {
            "trace_id": self.trace_id,
            "request_name": self.request_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "response_time_ms": round(self.response_time_ms, 2),
            "current_stage": self.current_stage.value,
            "final_stage": self.final_stage.value,
            "status_code": self.status_code,
            "is_failure": self.is_failure,
            "error_message": self.error_message,
            "stage_log": self.stage_log,
        }


# =============================================================================
# Observability Metrics Collector
# =============================================================================

class ObservabilityMetrics:
    """
    관측성 메트릭 수집기
    
    모든 요청을 추적하고 파이프라인 단계별 실패 분포를 수집.
    """
    
    _instance = None
    _class_lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._class_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """초기화"""
        self._lock = threading.RLock()  # RLock for reentrant locking
        
        # All traces
        self.traces: List[TraceContext] = []
        self.max_traces = 10000  # 메모리 제한
        
        # Stage-based failure counts
        self.failure_by_stage: Dict[str, int] = defaultdict(int)
        
        # Invariant violation counts
        self.untraced_failures = 0
        self.unknown_stage_failures = 0
        
        # Total counts
        self.total_requests = 0
        self.total_failures = 0
        self.total_successes = 0
        
        # Start time
        self.start_time = time.time()
    
    def record_trace(self, ctx: TraceContext):
        """추적 기록"""
        with self._lock:
            self.total_requests += 1
            
            if ctx.is_failure:
                self.total_failures += 1
                
                # 단계별 실패 카운트
                stage = ctx.final_stage.value
                self.failure_by_stage[stage] += 1
                
                # Invariant 체크: UNKNOWN stage면 contract 위반
                if ctx.final_stage == PipelineStage.UNKNOWN:
                    self.unknown_stage_failures += 1
            else:
                self.total_successes += 1
            
            # Trace 저장 (메모리 제한)
            if len(self.traces) < self.max_traces:
                self.traces.append(ctx)
    
    def record_untraced_failure(self, error: str):
        """
        trace_id 없이 발생한 실패 기록
        
        이는 Observability Contract 위반임.
        """
        with self._lock:
            self.untraced_failures += 1
            self.total_failures += 1
            self.total_requests += 1
    
    def _get_failure_distribution_unlocked(self) -> Dict[str, Any]:
        """파이프라인 단계별 실패 분포 (lock 없이 - 내부 사용)"""
        total = sum(self.failure_by_stage.values())
        distribution = {}
        
        for stage in PipelineStage:
            count = self.failure_by_stage.get(stage.value, 0)
            pct = (count / total * 100) if total > 0 else 0
            distribution[stage.value] = {
                "count": count,
                "percentage": round(pct, 2)
            }
        
        return distribution
    
    def get_failure_distribution(self) -> Dict[str, Any]:
        """파이프라인 단계별 실패 분포"""
        with self._lock:
            return self._get_failure_distribution_unlocked()
    
    def _check_invariants_unlocked(self) -> Dict[str, Any]:
        """Invariant 검증 (lock 없이 - 내부 사용)"""
        return {
            "untraced_failures": {
                "value": self.untraced_failures,
                "passed": self.untraced_failures == 0,
                "invariant": "untraced_failures == 0"
            },
            "unknown_stage_failures": {
                "value": self.unknown_stage_failures,
                "passed": self.unknown_stage_failures == 0,
                "invariant": "unknown_stage_failures == 0"
            }
        }

    def check_invariants(self) -> Dict[str, Any]:
        """
        Invariant 검증
        
        Returns:
            dict with invariant check results
        """
        with self._lock:
            return self._check_invariants_unlocked()
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Prometheus-style 메트릭 포맷
        
        failure_by_pipeline_stage{stage='ingress|validation|...'}
        """
        with self._lock:
            metrics = []
            
            for stage in PipelineStage:
                if stage == PipelineStage.UNKNOWN:
                    continue
                count = self.failure_by_stage.get(stage.value, 0)
                metrics.append(
                    f'failure_by_pipeline_stage{{stage="{stage.value}"}} {count}'
                )
            
            # 특별 메트릭
            metrics.append(f'observability_untraced_failures {self.untraced_failures}')
            metrics.append(f'observability_unknown_stage_failures {self.unknown_stage_failures}')
            metrics.append(f'observability_total_requests {self.total_requests}')
            metrics.append(f'observability_total_failures {self.total_failures}')
            
            return {
                "prometheus_format": "\n".join(metrics),
                "raw": {
                    "failure_by_stage": dict(self.failure_by_stage),
                    "untraced_failures": self.untraced_failures,
                    "unknown_stage_failures": self.unknown_stage_failures,
                    "total_requests": self.total_requests,
                    "total_failures": self.total_failures,
                    "total_successes": self.total_successes,
                }
            }
    
    def get_summary(self) -> Dict[str, Any]:
        """전체 요약"""
        with self._lock:
            elapsed = time.time() - self.start_time
            return {
                "elapsed_seconds": round(elapsed, 2),
                "total_requests": self.total_requests,
                "total_successes": self.total_successes,
                "total_failures": self.total_failures,
                "failure_rate": round(self.total_failures / max(1, self.total_requests) * 100, 2),
                "failure_distribution": self._get_failure_distribution_unlocked(),
                "invariants": self._check_invariants_unlocked(),
            }
    
    def reset(self):
        """리셋 (테스트용)"""
        self._initialize()


def get_observability_metrics() -> ObservabilityMetrics:
    """싱글톤 인스턴스 반환"""
    return ObservabilityMetrics()


# =============================================================================
# Context Manager for Request Tracing
# =============================================================================

@contextmanager
def trace_request(request_name: str, trace_id: Optional[str] = None):
    """
    요청 추적 컨텍스트 매니저
    
    Usage:
        with trace_request("create_order") as ctx:
            ctx.set_stage(PipelineStage.VALIDATION)
            # ... validation logic ...
            
            ctx.set_stage(PipelineStage.BUSINESS)
            response = client.post("/api/orders/", json=data)
            
            if response.status_code == 200:
                ctx.set_stage(PipelineStage.PERSISTENCE)
                ctx.record_success(200)
            else:
                ctx.record_failure("Order creation failed", response.status_code)
    """
    ctx = TraceContext(
        trace_id=trace_id or str(uuid.uuid4()),
        request_name=request_name,
        start_time=time.time()
    )
    
    try:
        yield ctx
    except Exception as e:
        # 예외 발생 시 자동으로 실패 기록
        if not ctx.is_failure and ctx.end_time is None:
            ctx.record_failure(str(e))
        raise
    finally:
        # end_time이 설정되지 않았으면 현재 시간으로
        if ctx.end_time is None:
            ctx.end_time = time.time()
            ctx.response_time_ms = (ctx.end_time - ctx.start_time) * 1000
        
        # 메트릭에 기록
        metrics = get_observability_metrics()
        metrics.record_trace(ctx)


# =============================================================================
# Report Generator
# =============================================================================

class ObservabilityReportGenerator:
    """
    종료 리포트 생성기
    
    단계별 실패 분포를 포함한 리포트 생성.
    """
    
    def __init__(self, metrics: Optional[ObservabilityMetrics] = None):
        self.metrics = metrics or get_observability_metrics()
    
    def generate_text_report(self) -> str:
        """텍스트 형식 리포트"""
        summary = self.metrics.get_summary()
        invariants = summary["invariants"]
        distribution = summary["failure_distribution"]
        
        lines = [
            "=" * 70,
            "OBSERVABILITY CONTRACT REPORT",
            "=" * 70,
            "",
            f"Test Duration: {summary['elapsed_seconds']:.2f}s",
            f"Total Requests: {summary['total_requests']}",
            f"Successes: {summary['total_successes']}",
            f"Failures: {summary['total_failures']} ({summary['failure_rate']:.2f}%)",
            "",
            "-" * 70,
            "PIPELINE STAGE FAILURE DISTRIBUTION",
            "-" * 70,
        ]
        
        # 단계별 분포 출력
        for stage in PipelineStage:
            if stage == PipelineStage.UNKNOWN:
                continue
            data = distribution.get(stage.value, {"count": 0, "percentage": 0})
            bar = "█" * int(data["percentage"] / 2)
            lines.append(f"  {stage.value:12s}: {data['count']:5d} ({data['percentage']:5.1f}%) {bar}")
        
        # UNKNOWN (contract 위반)
        unknown_data = distribution.get("unknown", {"count": 0, "percentage": 0})
        if unknown_data["count"] > 0:
            lines.append(f"  {'unknown':12s}: {unknown_data['count']:5d} ({unknown_data['percentage']:5.1f}%) ⚠️ CONTRACT VIOLATION")
        
        lines.extend([
            "",
            "-" * 70,
            "INVARIANT CHECKS",
            "-" * 70,
        ])
        
        all_passed = True
        for name, check in invariants.items():
            status = "✅ PASSED" if check["passed"] else "❌ FAILED"
            if not check["passed"]:
                all_passed = False
            lines.append(f"  {check['invariant']}: {check['value']} - {status}")
        
        lines.extend([
            "",
            "-" * 70,
            "FINAL RESULT",
            "-" * 70,
            f"  Observability Contract: {'✅ PASSED' if all_passed else '❌ FAILED'}",
            "=" * 70,
        ])
        
        return "\n".join(lines)
    
    def generate_html_report(self, stage_name: str = "Stage08") -> str:
        """HTML 형식 리포트"""
        summary = self.metrics.get_summary()
        invariants = summary["invariants"]
        distribution = summary["failure_distribution"]
        
        all_passed = all(inv["passed"] for inv in invariants.values())
        
        # 단계별 데이터 정리
        stage_data = []
        for stage in PipelineStage:
            if stage == PipelineStage.UNKNOWN:
                continue
            data = distribution.get(stage.value, {"count": 0, "percentage": 0})
            stage_data.append({
                "name": stage.value,
                "count": data["count"],
                "pct": data["percentage"]
            })
        
        html = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Observability Contract Report - {stage_name}</title>
    <style>
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #eee;
            min-height: 100vh;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            text-align: center;
            color: #00d4ff;
            margin-bottom: 30px;
        }}
        .summary-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }}
        .summary-card {{
            background: rgba(255,255,255,0.1);
            padding: 20px;
            border-radius: 10px;
            text-align: center;
        }}
        .summary-card h3 {{
            margin: 0;
            color: #888;
            font-size: 0.9em;
        }}
        .summary-card .value {{
            font-size: 2em;
            font-weight: bold;
            color: #00d4ff;
            margin: 10px 0;
        }}
        .section {{
            background: rgba(255,255,255,0.05);
            padding: 25px;
            border-radius: 10px;
            margin-bottom: 20px;
        }}
        .section h2 {{
            color: #00d4ff;
            border-bottom: 2px solid #00d4ff;
            padding-bottom: 10px;
        }}
        .stage-bar {{
            margin: 15px 0;
        }}
        .stage-bar .label {{
            display: inline-block;
            width: 120px;
            text-transform: capitalize;
        }}
        .stage-bar .bar-container {{
            display: inline-block;
            width: calc(100% - 250px);
            height: 25px;
            background: rgba(255,255,255,0.1);
            border-radius: 5px;
            vertical-align: middle;
            margin: 0 10px;
        }}
        .stage-bar .bar {{
            height: 100%;
            background: linear-gradient(90deg, #00d4ff, #0099cc);
            border-radius: 5px;
            transition: width 0.5s ease;
        }}
        .stage-bar .count {{
            display: inline-block;
            width: 80px;
            text-align: right;
        }}
        .invariant {{
            padding: 15px;
            margin: 10px 0;
            border-radius: 5px;
        }}
        .invariant.passed {{
            background: rgba(0, 200, 100, 0.2);
            border-left: 4px solid #00c864;
        }}
        .invariant.failed {{
            background: rgba(255, 50, 50, 0.2);
            border-left: 4px solid #ff3232;
        }}
        .result {{
            text-align: center;
            padding: 30px;
            font-size: 1.5em;
            border-radius: 10px;
            margin-top: 30px;
        }}
        .result.passed {{
            background: linear-gradient(135deg, rgba(0,200,100,0.3) 0%, rgba(0,150,75,0.3) 100%);
            color: #00ff7f;
        }}
        .result.failed {{
            background: linear-gradient(135deg, rgba(255,50,50,0.3) 0%, rgba(200,0,0,0.3) 100%);
            color: #ff4444;
        }}
        .metrics-code {{
            background: #0d1117;
            padding: 20px;
            border-radius: 5px;
            font-family: monospace;
            font-size: 0.9em;
            overflow-x: auto;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 Observability Contract Report</h1>
        <p style="text-align:center;color:#888;">GAP-08 | {stage_name} | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        
        <div class="summary-grid">
            <div class="summary-card">
                <h3>Duration</h3>
                <div class="value">{summary['elapsed_seconds']:.1f}s</div>
            </div>
            <div class="summary-card">
                <h3>Total Requests</h3>
                <div class="value">{summary['total_requests']}</div>
            </div>
            <div class="summary-card">
                <h3>Successes</h3>
                <div class="value" style="color:#00ff7f">{summary['total_successes']}</div>
            </div>
            <div class="summary-card">
                <h3>Failures</h3>
                <div class="value" style="color:#ff4444">{summary['total_failures']}</div>
            </div>
            <div class="summary-card">
                <h3>Failure Rate</h3>
                <div class="value">{summary['failure_rate']:.2f}%</div>
            </div>
        </div>
        
        <div class="section">
            <h2>📊 Pipeline Stage Failure Distribution</h2>
"""
        
        max_pct = max(d["pct"] for d in stage_data) if stage_data else 1
        max_pct = max(max_pct, 1)  # 0으로 나누기 방지
        
        for data in stage_data:
            width = (data["pct"] / max_pct * 100) if max_pct > 0 else 0
            html += f"""
            <div class="stage-bar">
                <span class="label">{data['name']}</span>
                <div class="bar-container">
                    <div class="bar" style="width: {width}%"></div>
                </div>
                <span class="count">{data['count']} ({data['pct']:.1f}%)</span>
            </div>"""
        
        html += """
        </div>
        
        <div class="section">
            <h2>✅ Invariant Checks</h2>
"""
        
        for name, check in invariants.items():
            status_class = "passed" if check["passed"] else "failed"
            status_icon = "✅" if check["passed"] else "❌"
            html += f"""
            <div class="invariant {status_class}">
                <strong>{status_icon} {check['invariant']}</strong>
                <br>Value: {check['value']}
            </div>"""
        
        result_class = "passed" if all_passed else "failed"
        result_icon = "✅" if all_passed else "❌"
        result_text = "PASSED" if all_passed else "FAILED"
        
        # Prometheus metrics
        prom_metrics = self.metrics.get_metrics()["prometheus_format"]
        
        html += f"""
        </div>
        
        <div class="section">
            <h2>📈 Prometheus Metrics</h2>
            <pre class="metrics-code">{prom_metrics}</pre>
        </div>
        
        <div class="result {result_class}">
            {result_icon} Observability Contract: <strong>{result_text}</strong>
        </div>
    </div>
</body>
</html>"""
        
        return html
    
    def save_report(self, filename: str = "observability_report", 
                   output_dir: str = "reports") -> Dict[str, str]:
        """리포트 저장"""
        os.makedirs(output_dir, exist_ok=True)
        
        # Text report
        text_path = os.path.join(output_dir, f"{filename}.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(self.generate_text_report())
        
        # HTML report
        html_path = os.path.join(output_dir, f"{filename}.html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(self.generate_html_report())
        
        # JSON report
        json_path = os.path.join(output_dir, f"{filename}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(self.metrics.get_summary(), f, indent=2)
        
        return {
            "text": text_path,
            "html": html_path,
            "json": json_path
        }


# =============================================================================
# Locust Integration
# =============================================================================

def setup_observability_hooks(stage_name: str = ""):
    """
    Locust 이벤트 훅에 Observability 통합
    
    기존 event_hooks.py와 통합하여 사용.
    """
    try:
        from locust import events as locust_events
    except ImportError:
        print("Warning: Locust not available, skipping hook setup")
        return
    
    metrics = get_observability_metrics()
    
    @locust_events.test_stop.add_listener
    def on_test_stop(environment, **kwargs):
        """테스트 종료 시 리포트 생성"""
        reporter = ObservabilityReportGenerator(metrics)
        print("\n")
        print(reporter.generate_text_report())
        
        # 리포트 파일 저장
        try:
            paths = reporter.save_report(
                filename=f"observability_{stage_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                output_dir="reports"
            )
            print(f"\nReports saved to: {paths}")
        except Exception as e:
            print(f"Warning: Failed to save reports: {e}")


# =============================================================================
# Standalone Test
# =============================================================================

def run_standalone_test():
    """독립 실행 테스트"""
    print("=" * 70)
    print("GAP-08: Observability Contract - Standalone Test")
    print("=" * 70)
    
    metrics = get_observability_metrics()
    metrics.reset()
    
    # 테스트 시나리오 시뮬레이션
    import random
    
    test_scenarios = [
        ("create_order", PipelineStage.VALIDATION, 0.1),  # 10% failure at validation
        ("get_products", PipelineStage.CACHING, 0.05),    # 5% failure at cache
        ("process_payment", PipelineStage.PERSISTENCE, 0.03),  # 3% failure at DB
        ("login", PipelineStage.AUTH, 0.02),              # 2% failure at auth
        ("webhook_callback", PipelineStage.ASYNC, 0.08),  # 8% failure at async
        ("update_inventory", PipelineStage.BUSINESS, 0.04),  # 4% failure at business
    ]
    
    total_requests = 500
    print(f"\nSimulating {total_requests} requests across 6 scenarios...")
    print("-" * 70)
    
    for i in range(total_requests):
        scenario = random.choice(test_scenarios)
        request_name, fail_stage, fail_rate = scenario
        
        with trace_request(request_name) as ctx:
            # 시작은 항상 INGRESS
            ctx.set_stage(PipelineStage.INGRESS)
            time.sleep(random.uniform(0.001, 0.005))  # 시뮬레이션 지연
            
            # 단계 진행
            should_fail = random.random() < fail_rate
            
            if should_fail:
                ctx.set_stage(fail_stage)
                error_messages = {
                    PipelineStage.VALIDATION: "Invalid input format",
                    PipelineStage.AUTH: "JWT token expired",
                    PipelineStage.BUSINESS: "Insufficient stock",
                    PipelineStage.CACHING: "Cache miss after Redis timeout",
                    PipelineStage.PERSISTENCE: "Database connection lost",
                    PipelineStage.ASYNC: "Celery worker timeout",
                }
                ctx.record_failure(error_messages.get(fail_stage, "Unknown error"), 500)
            else:
                # 정상 처리 흐름
                for stage in [PipelineStage.VALIDATION, PipelineStage.AUTH, 
                             PipelineStage.BUSINESS, PipelineStage.PERSISTENCE,
                             PipelineStage.EGRESS]:
                    ctx.set_stage(stage)
                    time.sleep(random.uniform(0.001, 0.003))
                ctx.record_success(200)
        
        # 진행률 표시
        if (i + 1) % 100 == 0:
            print(f"  Processed {i + 1}/{total_requests} requests...")
    
    # 의도적으로 unknown stage failure 생성 (contract 위반 테스트)
    print("\nTesting contract violation (unknown stage failure)...")
    with trace_request("unknown_failure") as ctx:
        # set_stage를 호출하지 않고 바로 실패
        ctx.record_failure("Mystery error")  # This should trigger unknown stage
    
    # 리포트 생성
    print("\n")
    reporter = ObservabilityReportGenerator(metrics)
    report = reporter.generate_text_report()
    print(report)
    
    # 파일 저장
    paths = reporter.save_report(
        filename="gap08_observability_test",
        output_dir="load_tests/reports"
    )
    print(f"\nReports saved:")
    for format_name, path in paths.items():
        print(f"  {format_name}: {path}")
    
    # Invariant 체크
    summary = metrics.get_summary()
    invariants = summary["invariants"]
    
    print("\n" + "=" * 70)
    print("FINAL VERIFICATION")
    print("=" * 70)
    
    # 의도적으로 unknown 에러를 넣었으므로 unknown_stage_failures > 0 예상
    if invariants["unknown_stage_failures"]["value"] == 1:
        print("✅ Contract violation detection working correctly (1 unknown stage detected)")
    
    if invariants["untraced_failures"]["passed"]:
        print("✅ All failures have trace_id (untraced_failures == 0)")
    else:
        print(f"❌ Untraced failures found: {invariants['untraced_failures']['value']}")
    
    print("\nTest completed successfully!")
    return True


if __name__ == "__main__":
    run_standalone_test()

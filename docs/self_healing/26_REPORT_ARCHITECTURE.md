# Load Test Report Architecture

📅 **작성일**: 2025-12-28  
🏷️ **버전**: 1.0.0  
🎯 **목적**: 모든 부하 테스트 스테이지에 일관된 보고서 형식 적용

---

## 1. 개요

### 1.1 배경
- 각 스테이지마다 다른 보고서 형식으로 인해 비교 분석이 어려움
- 1,500~2,000줄의 시나리오 파일에 보고서 생성 코드가 300~500줄 포함
- 리팩토링을 통해 코드 중복 제거 및 일관성 확보 필요

### 1.2 목표
- **코드 감소**: 각 스테이지에서 300~500줄 제거
- **일관성**: 모든 스테이지 동일한 Executive Summary
- **확장성**: 새 스테이지 추가 시 보고서 자동 적용
- **분석 용이성**: JSON 데이터로 스테이지 간 비교 분석

---

## 2. 아키텍처

### 2.1 계층 구조 (Inheritance)

```
BaseReport (공통)
├── 테스트 메타데이터 (duration, users, timestamp, environment_id)
├── Phase별 분석 (requests, errors, response times)
├── 표준 지표 (P50, P95, P99, throughput)
└── 최종 결과 (PASS/FAIL)

    └── SelfHealingReport (extends BaseReport)
        ├── Circuit Breaker 분석
        ├── Emergency Mode 분석
        ├── Error Budget 분석
        ├── DLQ 분석
        └── Recovery Latency

            └── PlatinumReport (extends SelfHealingReport)
                ├── SLA Hard-Cap
                ├── Message Storm & Backpressure
                ├── Clock Skew Attack
                ├── Cascading Failure
                └── Retry Storm Prevention
```

### 2.2 디렉토리 구조

```
load_tests/reports/
├── __init__.py
├── schema.py              # 표준 지표 정의 (데이터 헌법)
├── config.py              # 보고서 설정 (임계값 등)
├── base_report.py         # BaseReport 클래스
├── selfhealing_report.py  # SelfHealingReport (extends Base)
├── platinum_report.py     # PlatinumReport (extends SelfHealing)
├── adapters/              # 레거시 어댑터
│   ├── __init__.py
│   └── legacy_stats.py    # 기존 _extreme_stats → 새 형식 변환
├── dispatchers/           # 보고서 전송 (인터페이스만)
│   ├── __init__.py
│   ├── base.py            # DispatcherInterface
│   ├── local_file.py      # 로컬 저장 (구현)
│   ├── slack.py           # Slack 전송 (인터페이스만)
│   └── s3_uploader.py     # S3 업로드 (인터페이스만)
└── templates/             # 마크다운 템플릿
    ├── base.md
    ├── selfhealing.md
    └── platinum.md
```

---

## 3. 표준 지표 명세 (Schema)

### 3.1 스키마 버전
```python
SCHEMA_VERSION = "1.0.0"
```

### 3.2 지표 정의

```python
# load_tests/reports/schema.py

from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from enum import Enum

class MetricCategory(Enum):
    BASE = "base"
    SELFHEALING = "selfhealing"
    PLATINUM = "platinum"

# ============================================================
# Base Metrics (모든 스테이지 필수)
# ============================================================
@dataclass
class BaseMetrics:
    # 메타데이터
    test_name: str
    stage_id: str
    timestamp: str
    test_duration_sec: int
    max_users: int
    min_users: int
    environment_id: Optional[str] = None  # 테스트 환경 식별자
    chaos_intensity: Optional[float] = None  # 장애 주입 강도 (0.0~1.0)
    
    # 요청 통계
    total_requests: int = 0
    total_errors: int = 0
    error_rate_percent: float = 0.0
    
    # 응답시간 (ms)
    avg_response_ms: float = 0.0
    min_response_ms: float = 0.0
    max_response_ms: float = 0.0
    p50_response_ms: float = 0.0
    p95_response_ms: float = 0.0
    p99_response_ms: float = 0.0
    
    # 처리량
    throughput_rps: float = 0.0
    
    # 최종 결과
    passed: bool = True
    failure_reasons: List[str] = None

# ============================================================
# Self-Healing Metrics (Self-Healing 스테이지용)
# ============================================================
@dataclass
class SelfHealingMetrics:
    # Circuit Breaker
    cb_open_count: int = 0
    cb_close_count: int = 0
    cb_half_open_count: int = 0
    cb_recovery_ms: Optional[float] = None
    cb_services_affected: List[str] = None
    
    # Emergency Mode
    emergency_triggered_count: int = 0
    emergency_released_count: int = 0
    emergency_max_level: int = 0
    
    # Error Budget
    error_budget_initial: Optional[float] = None
    error_budget_min: Optional[float] = None
    error_budget_exhausted: bool = False
    error_budget_recovered: bool = False
    
    # DLQ
    dlq_max_count: int = 0
    dlq_replay_success: int = 0
    dlq_replay_fail: int = 0
    
    # Recovery
    recovery_latency_sec: Optional[float] = None
    recovery_sla_passed: bool = True  # < 2min

# ============================================================
# Platinum Metrics (Platinum Grade 스테이지용)
# ============================================================
@dataclass
class PlatinumMetrics:
    # SLA Hard-Cap
    sla_p99_max_ms: float = 0.0
    sla_p99_violations: int = 0
    sla_p99_passed: bool = True
    sla_threshold_ms: float = 250.0
    
    # Message Storm & Backpressure
    storm_messages_injected: int = 0
    storm_buffer_overflow: int = 0
    storm_backpressure_activated: bool = False
    storm_main_logic_affected: bool = False
    
    # Clock Skew
    clock_attacks_executed: int = 0
    clock_max_drift_sec: float = 0.0
    clock_consistency_maintained: bool = True
    
    # Cascading Failure
    cascade_triggered: int = 0
    cascade_max_depth: int = 0
    cascade_isolation_success: bool = True
    
    # Retry Storm
    retry_storms_detected: int = 0
    retry_circuit_protected: bool = True
    
    # Platinum Grade 최종 판정
    platinum_achieved: bool = False

# ============================================================
# 시계열 데이터 (Trend Analysis)
# ============================================================
@dataclass
class TimeseriesPoint:
    timestamp: str
    rps: float
    error_rate: float
    avg_response_ms: float
    event_type: Optional[str] = None  # CB_OPEN, EMERGENCY_L1, etc.

@dataclass
class TimeseriesData:
    points: List[TimeseriesPoint]
    interval_sec: int = 1  # 수집 간격
```

---

## 4. 설정 (Config)

```python
# load_tests/reports/config.py

class ReportConfig:
    """보고서 생성 설정"""
    
    # 스키마 버전
    SCHEMA_VERSION = "1.0.0"
    
    # SLA 임계값
    SLA_P99_THRESHOLD_MS = 250
    SLA_RECOVERY_THRESHOLD_SEC = 120  # 2분
    
    # 회귀 감지 임계값
    REGRESSION_P99_THRESHOLD_PERCENT = 10
    REGRESSION_ERROR_RATE_THRESHOLD_PERCENT = 5
    
    # 출력 형식
    OUTPUT_JSON = True
    OUTPUT_MARKDOWN = True
    OUTPUT_HTML = False  # 추후 확장
    
    # 시계열 데이터
    TIMESERIES_ENABLED = True
    TIMESERIES_INTERVAL_SEC = 1
    TIMESERIES_MAX_POINTS = 3600  # 최대 1시간
```

---

## 5. 클래스 설계

### 5.1 BaseReport

```python
# load_tests/reports/base_report.py

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from datetime import datetime
from .schema import BaseMetrics, TimeseriesData
from .config import ReportConfig

class BaseReport(ABC):
    """모든 보고서의 기반 클래스"""
    
    def __init__(self, metrics: BaseMetrics, timeseries: Optional[TimeseriesData] = None):
        self.metrics = metrics
        self.timeseries = timeseries
        self.config = ReportConfig()
    
    def to_dict(self) -> Dict[str, Any]:
        """JSON 직렬화용 딕셔너리 반환"""
        return {
            "schema_version": self.config.SCHEMA_VERSION,
            "generated_at": datetime.now().isoformat(),
            "metrics": self._metrics_to_dict(),
            "timeseries": self._timeseries_to_dict() if self.timeseries else None,
        }
    
    def to_json(self, filepath: str) -> str:
        """JSON 파일 저장"""
        # 구현
    
    def to_markdown(self) -> str:
        """Markdown 문자열 반환 (오버라이딩 가능)"""
        return self._render_base_markdown()
    
    def to_markdown_file(self, filepath: str) -> str:
        """Markdown 파일 저장"""
        # 구현
    
    def print_console(self) -> None:
        """콘솔 출력"""
        # 구현
    
    @abstractmethod
    def _metrics_to_dict(self) -> Dict[str, Any]:
        """서브클래스에서 구현"""
        pass
    
    def _render_base_markdown(self) -> str:
        """기본 Markdown 템플릿 렌더링"""
        # Executive Summary, Phase Analysis 등
        pass
```

### 5.2 SelfHealingReport

```python
# load_tests/reports/selfhealing_report.py

from .base_report import BaseReport
from .schema import BaseMetrics, SelfHealingMetrics

class SelfHealingReport(BaseReport):
    """Self-Healing 스테이지용 보고서"""
    
    def __init__(self, base_metrics: BaseMetrics, sh_metrics: SelfHealingMetrics, **kwargs):
        super().__init__(base_metrics, **kwargs)
        self.sh_metrics = sh_metrics
    
    def to_markdown(self) -> str:
        """Base + Self-Healing 섹션"""
        base_md = super().to_markdown()
        sh_md = self._render_selfhealing_markdown()
        return base_md + "\n" + sh_md
    
    def _render_selfhealing_markdown(self) -> str:
        """Self-Healing 전용 섹션"""
        # Circuit Breaker, Emergency Mode, DLQ 등
        pass
```

### 5.3 PlatinumReport

```python
# load_tests/reports/platinum_report.py

from .selfhealing_report import SelfHealingReport
from .schema import BaseMetrics, SelfHealingMetrics, PlatinumMetrics

class PlatinumReport(SelfHealingReport):
    """Platinum Grade 스테이지용 보고서"""
    
    def __init__(self, base_metrics: BaseMetrics, sh_metrics: SelfHealingMetrics, 
                 platinum_metrics: PlatinumMetrics, **kwargs):
        super().__init__(base_metrics, sh_metrics, **kwargs)
        self.platinum_metrics = platinum_metrics
    
    def to_markdown(self) -> str:
        """Base + SelfHealing + Platinum 섹션"""
        parent_md = super().to_markdown()
        platinum_md = self._render_platinum_markdown()
        return parent_md + "\n" + platinum_md
    
    def _render_platinum_markdown(self) -> str:
        """Platinum Grade 전용 섹션"""
        # SLA Hard-Cap, Message Storm, Clock Skew 등
        pass
```

---

## 6. Dispatcher 패턴

### 6.1 인터페이스

```python
# load_tests/reports/dispatchers/base.py

from abc import ABC, abstractmethod
from typing import Optional
from ..base_report import BaseReport

class DispatcherInterface(ABC):
    """보고서 전송 인터페이스"""
    
    @abstractmethod
    def dispatch(self, report: BaseReport, **kwargs) -> bool:
        """보고서 전송. 성공 시 True 반환."""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Dispatcher 사용 가능 여부"""
        pass
```

### 6.2 구현: LocalFileDispatcher

```python
# load_tests/reports/dispatchers/local_file.py

import os
from .base import DispatcherInterface
from ..base_report import BaseReport

class LocalFileDispatcher(DispatcherInterface):
    """로컬 파일 시스템에 저장"""
    
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
    
    def dispatch(self, report: BaseReport, **kwargs) -> bool:
        os.makedirs(self.base_dir, exist_ok=True)
        
        # JSON 저장
        json_path = os.path.join(self.base_dir, f"{report.metrics.stage_id}.json")
        report.to_json(json_path)
        
        # Markdown 저장
        md_path = os.path.join(self.base_dir, f"{report.metrics.stage_id}.md")
        report.to_markdown_file(md_path)
        
        return True
    
    def is_available(self) -> bool:
        return True
```

### 6.3 인터페이스: SlackDispatcher (미구현)

```python
# load_tests/reports/dispatchers/slack.py

from .base import DispatcherInterface
from ..base_report import BaseReport

class SlackDispatcher(DispatcherInterface):
    """Slack 웹훅으로 보고서 요약 전송 (인터페이스만)"""
    
    def __init__(self, webhook_url: str = None):
        self.webhook_url = webhook_url
    
    def dispatch(self, report: BaseReport, **kwargs) -> bool:
        # TODO: 구현 필요
        raise NotImplementedError("SlackDispatcher is not implemented yet")
    
    def is_available(self) -> bool:
        return self.webhook_url is not None
```

### 6.4 인터페이스: S3Dispatcher (미구현)

```python
# load_tests/reports/dispatchers/s3_uploader.py

from .base import DispatcherInterface
from ..base_report import BaseReport

class S3Dispatcher(DispatcherInterface):
    """AWS S3에 보고서 업로드 (인터페이스만)"""
    
    def __init__(self, bucket: str = None, prefix: str = "load-test-reports/"):
        self.bucket = bucket
        self.prefix = prefix
    
    def dispatch(self, report: BaseReport, **kwargs) -> bool:
        # TODO: 구현 필요
        raise NotImplementedError("S3Dispatcher is not implemented yet")
    
    def is_available(self) -> bool:
        return self.bucket is not None
```

---

## 7. 레거시 어댑터

```python
# load_tests/reports/adapters/legacy_stats.py

from typing import Dict, Any
from ..schema import BaseMetrics, SelfHealingMetrics, PlatinumMetrics

def adapt_extreme_stats(stats: Dict[str, Any], config: Dict[str, Any]) -> tuple:
    """
    기존 _extreme_stats 딕셔너리를 새 스키마로 변환.
    
    Returns:
        (BaseMetrics, SelfHealingMetrics, PlatinumMetrics)
    """
    # Base Metrics
    base = BaseMetrics(
        test_name=f"Stage {config.get('stage_id', 'unknown')}",
        stage_id=config.get('stage_id', 'unknown'),
        timestamp=config.get('timestamp', ''),
        test_duration_sec=config.get('test_duration', 0),
        max_users=config.get('max_users', 0),
        min_users=config.get('min_users', 0),
        # ... 나머지 필드 매핑
    )
    
    # Self-Healing Metrics
    cb = stats.get('circuit_breaker', {})
    em = stats.get('emergency', {})
    sh = SelfHealingMetrics(
        cb_open_count=cb.get('open_count', 0),
        cb_close_count=cb.get('close_count', 0),
        # ... 나머지 필드 매핑
    )
    
    # Platinum Metrics
    sla = stats.get('sla_hardcap', {})
    platinum = PlatinumMetrics(
        sla_p99_max_ms=sla.get('p99_max_ms', 0),
        sla_p99_passed=sla.get('sla_passed', True),
        # ... 나머지 필드 매핑
    )
    
    return base, sh, platinum
```

---

## 8. 비교 리포트 (Regression Detection)

```python
# load_tests/reports/comparison.py

from dataclasses import dataclass
from typing import List, Optional
from .base_report import BaseReport
from .config import ReportConfig

@dataclass
class RegressionAlarm:
    metric_name: str
    baseline_value: float
    current_value: float
    change_percent: float
    threshold_percent: float
    is_regression: bool

class ComparisonReport:
    """두 테스트 결과 비교"""
    
    def __init__(self, baseline: BaseReport, current: BaseReport):
        self.baseline = baseline
        self.current = current
        self.alarms: List[RegressionAlarm] = []
        self._analyze()
    
    def _analyze(self):
        """회귀 분석 수행"""
        config = ReportConfig()
        
        # P99 비교
        baseline_p99 = self.baseline.metrics.p99_response_ms
        current_p99 = self.current.metrics.p99_response_ms
        change = ((current_p99 - baseline_p99) / baseline_p99 * 100) if baseline_p99 > 0 else 0
        
        self.alarms.append(RegressionAlarm(
            metric_name="p99_response_ms",
            baseline_value=baseline_p99,
            current_value=current_p99,
            change_percent=change,
            threshold_percent=config.REGRESSION_P99_THRESHOLD_PERCENT,
            is_regression=change > config.REGRESSION_P99_THRESHOLD_PERCENT
        ))
        
        # Error Rate 비교
        # ... 추가 지표
    
    def has_regression(self) -> bool:
        """회귀 발생 여부"""
        return any(alarm.is_regression for alarm in self.alarms)
    
    def to_markdown(self) -> str:
        """비교 결과 Markdown"""
        pass
```

---

## 9. 적용 예시

### 9.1 스테이지에서 사용

```python
# stage12_spike_recovery.py

from load_tests.reports import PlatinumReport
from load_tests.reports.adapters.legacy_stats import adapt_extreme_stats
from load_tests.reports.dispatchers import LocalFileDispatcher

@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    # 기존 통계를 새 형식으로 변환
    base_metrics, sh_metrics, platinum_metrics = adapt_extreme_stats(
        _extreme_stats, 
        {"stage_id": "stage12", "max_users": MAX_USERS, ...}
    )
    
    # 보고서 생성
    report = PlatinumReport(base_metrics, sh_metrics, platinum_metrics)
    
    # 콘솔 출력
    report.print_console()
    
    # 파일 저장
    dispatcher = LocalFileDispatcher("load_tests/results/stage12")
    dispatcher.dispatch(report)
```

---

## 10. 구현 로드맵

| 단계 | 작업 | 파일 | 우선순위 |
|------|------|------|----------|
| 1 | 스키마 정의 | `schema.py` | 🔴 High |
| 2 | 설정 클래스 | `config.py` | 🔴 High |
| 3 | BaseReport 구현 | `base_report.py` | 🔴 High |
| 4 | SelfHealingReport 구현 | `selfhealing_report.py` | 🔴 High |
| 5 | PlatinumReport 리팩토링 | `platinum_report.py` | 🔴 High |
| 6 | 레거시 어댑터 | `adapters/legacy_stats.py` | 🟡 Medium |
| 7 | LocalFileDispatcher | `dispatchers/local_file.py` | 🟡 Medium |
| 8 | Slack/S3 인터페이스 | `dispatchers/slack.py`, `s3_uploader.py` | 🟢 Low |
| 9 | 비교 리포트 | `comparison.py` | 🟢 Low |
| 10 | 각 스테이지 적용 | `stage*.py` | 🟡 Medium |

---

## 11. 기대 효과

| 항목 | 현재 | 목표 |
|------|------|------|
| 스테이지당 보고서 코드 | 300~500줄 | 10~20줄 |
| 보고서 형식 일관성 | ❌ 없음 | ✅ 100% |
| 스테이지 간 비교 | ❌ 수동 | ✅ 자동화 |
| 새 스테이지 추가 시간 | 1시간 | 10분 |

---

📝 **Generated by Load Test Report Architecture Design**

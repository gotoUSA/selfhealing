"""
Load Test 보고서 생성 모듈 패키지.

각 스테이지별 보고서 생성 로직을 분리하여 코드 중복을 줄입니다.
새로운 Report Architecture v1.0.0을 포함합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""

# ============================================================
# Legacy Exports (기존 호환성 유지)
# ============================================================
from .platinum_report import (
    print_console_report,
    save_json_report,
    save_markdown_report,
    save_all_reports,
)

# ============================================================
# New Report Architecture v1.0.0
# ============================================================
# Schema
from .schema import (
    SCHEMA_VERSION,
    MetricCategory,
    BaseMetrics,
    SelfHealingMetrics,
    PlatinumMetrics,
    PhaseMetrics,
    TimeseriesPoint,
    TimeseriesData,
    ReportData,
)

# Config
from .report_config import (
    ReportConfig,
    get_config,
    create_custom_config,
    DEFAULT_CONFIG,
)

# Report Classes
from .base_report import BaseReport, SimpleReport
from .selfhealing_report import SelfHealingReport
from .platinum_report_v2 import PlatinumReport

# Comparison
from .comparison import (
    RegressionAlarm,
    ComparisonResult,
    ComparisonReport,
    compare_reports,
)

# Adapters
from .adapters import (
    adapt_extreme_stats,
    create_report_from_legacy_stats,
)

# Dispatchers
from .dispatchers import (
    DispatcherInterface,
    LocalFileDispatcher,
    SlackDispatcher,
    S3Dispatcher,
)

__all__ = [
    # Legacy
    "print_console_report",
    "save_json_report",
    "save_markdown_report",
    "save_all_reports",
    
    # Schema
    "SCHEMA_VERSION",
    "MetricCategory",
    "BaseMetrics",
    "SelfHealingMetrics",
    "PlatinumMetrics",
    "PhaseMetrics",
    "TimeseriesPoint",
    "TimeseriesData",
    "ReportData",
    
    # Config
    "ReportConfig",
    "get_config",
    "create_custom_config",
    "DEFAULT_CONFIG",
    
    # Reports
    "BaseReport",
    "SimpleReport",
    "SelfHealingReport",
    "PlatinumReport",
    
    # Comparison
    "RegressionAlarm",
    "ComparisonResult",
    "ComparisonReport",
    "compare_reports",
    
    # Adapters
    "adapt_extreme_stats",
    "create_report_from_legacy_stats",
    
    # Dispatchers
    "DispatcherInterface",
    "LocalFileDispatcher",
    "SlackDispatcher",
    "S3Dispatcher",
]

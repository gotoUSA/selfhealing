"""
Load Test Report Configuration - 보고서 생성 설정.

보고서 생성 시 사용되는 임계값, 출력 형식 등을 정의합니다.

📅 작성일: 2025-12-28
🏷️ 버전: 1.0.0
"""
from dataclasses import dataclass
from typing import Dict, Any


@dataclass
class ReportConfig:
    """보고서 생성 설정."""
    
    # ============================================================
    # 스키마 버전
    # ============================================================
    SCHEMA_VERSION: str = "1.0.0"
    
    # ============================================================
    # SLA 임계값
    # ============================================================
    SLA_P99_THRESHOLD_MS: float = 250.0  # P99 응답시간 임계값 (ms)
    SLA_RECOVERY_THRESHOLD_SEC: float = 120.0  # 복구 시간 임계값 (2분)
    SLA_ERROR_RATE_THRESHOLD_PERCENT: float = 5.0  # 에러율 임계값 (%)
    
    # ============================================================
    # 회귀 감지 임계값
    # ============================================================
    REGRESSION_P99_THRESHOLD_PERCENT: float = 10.0  # P99 회귀 임계값 (%)
    REGRESSION_ERROR_RATE_THRESHOLD_PERCENT: float = 5.0  # 에러율 회귀 임계값 (%)
    REGRESSION_THROUGHPUT_THRESHOLD_PERCENT: float = 10.0  # 처리량 회귀 임계값 (%)
    
    # ============================================================
    # Self-Healing 임계값
    # ============================================================
    SELFHEALING_CB_RECOVERY_THRESHOLD_MS: float = 30000.0  # CB 복구 임계값 (30초)
    SELFHEALING_EMERGENCY_MAX_LEVEL: int = 3  # 최대 비상 레벨
    SELFHEALING_ERROR_BUDGET_MIN_PERCENT: float = 10.0  # 최소 에러 버짓 (%)
    
    # ============================================================
    # Platinum Grade 임계값
    # ============================================================
    PLATINUM_SLA_STRICT_P99_MS: float = 200.0  # 엄격한 P99 임계값 (ms)
    PLATINUM_CASCADE_MAX_DEPTH: int = 3  # 최대 캐스케이드 깊이
    PLATINUM_CACHE_HIT_MIN_PERCENT: float = 80.0  # 최소 캐시 히트율 (%)
    
    # ============================================================
    # 출력 형식
    # ============================================================
    OUTPUT_JSON: bool = True
    OUTPUT_MARKDOWN: bool = True
    OUTPUT_HTML: bool = False  # 추후 확장
    OUTPUT_CONSOLE: bool = True
    
    # ============================================================
    # 시계열 데이터
    # ============================================================
    TIMESERIES_ENABLED: bool = True
    TIMESERIES_INTERVAL_SEC: int = 1
    TIMESERIES_MAX_POINTS: int = 3600  # 최대 1시간
    
    # ============================================================
    # 파일 경로 설정
    # ============================================================
    DEFAULT_OUTPUT_DIR: str = "load_tests/results"
    REPORT_FILE_PREFIX: str = "report_"
    
    def to_dict(self) -> Dict[str, Any]:
        """설정을 딕셔너리로 변환."""
        return {
            "schema_version": self.SCHEMA_VERSION,
            "sla": {
                "p99_threshold_ms": self.SLA_P99_THRESHOLD_MS,
                "recovery_threshold_sec": self.SLA_RECOVERY_THRESHOLD_SEC,
                "error_rate_threshold_percent": self.SLA_ERROR_RATE_THRESHOLD_PERCENT,
            },
            "regression": {
                "p99_threshold_percent": self.REGRESSION_P99_THRESHOLD_PERCENT,
                "error_rate_threshold_percent": self.REGRESSION_ERROR_RATE_THRESHOLD_PERCENT,
                "throughput_threshold_percent": self.REGRESSION_THROUGHPUT_THRESHOLD_PERCENT,
            },
            "selfhealing": {
                "cb_recovery_threshold_ms": self.SELFHEALING_CB_RECOVERY_THRESHOLD_MS,
                "emergency_max_level": self.SELFHEALING_EMERGENCY_MAX_LEVEL,
                "error_budget_min_percent": self.SELFHEALING_ERROR_BUDGET_MIN_PERCENT,
            },
            "platinum": {
                "sla_strict_p99_ms": self.PLATINUM_SLA_STRICT_P99_MS,
                "cascade_max_depth": self.PLATINUM_CASCADE_MAX_DEPTH,
                "cache_hit_min_percent": self.PLATINUM_CACHE_HIT_MIN_PERCENT,
            },
            "output": {
                "json": self.OUTPUT_JSON,
                "markdown": self.OUTPUT_MARKDOWN,
                "html": self.OUTPUT_HTML,
                "console": self.OUTPUT_CONSOLE,
            },
            "timeseries": {
                "enabled": self.TIMESERIES_ENABLED,
                "interval_sec": self.TIMESERIES_INTERVAL_SEC,
                "max_points": self.TIMESERIES_MAX_POINTS,
            },
        }


# 기본 설정 인스턴스
DEFAULT_CONFIG = ReportConfig()


def get_config() -> ReportConfig:
    """기본 설정 인스턴스 반환."""
    return DEFAULT_CONFIG


def create_custom_config(**kwargs) -> ReportConfig:
    """
    커스텀 설정 생성.
    
    Args:
        **kwargs: ReportConfig 필드 오버라이드
        
    Returns:
        ReportConfig: 커스텀 설정 인스턴스
        
    Example:
        >>> config = create_custom_config(SLA_P99_THRESHOLD_MS=300.0)
    """
    config = ReportConfig()
    for key, value in kwargs.items():
        if hasattr(config, key):
            setattr(config, key, value)
    return config

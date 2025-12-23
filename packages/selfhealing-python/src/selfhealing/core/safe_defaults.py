"""
Safe Default Values for Self-Healing Configuration.

모든 설정에 대해 안전한 기본값 정의.
설정 오류 시 이 값으로 폴백.

Phase 6: Fail-Safe Default 강화
Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Safe Default Values
# =============================================================================

SAFE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # Circuit Breaker - 보수적 설정 (더 빨리 열림, 시스템 보호 우선)
    "circuit_breaker": {
        "enabled": True,  # 항상 활성화
        "failure_threshold": 5,  # 낮게 유지
        "recovery_timeout": 60,  # 1분
        "success_threshold": 2,
        "half_open_max_calls": 3,
        "half_open_request_limit": 10,
        "rate_limit_cascade_threshold": 10,
        "rate_limit_cascade_window_seconds": 60,
        "self_ddos_protection_enabled": True,
        "self_ddos_request_threshold": 100,
        "self_ddos_window_seconds": 10,
        "self_ddos_backoff_multiplier": 2.0,
    },
    
    # DLQ - 보수적 설정 (더 오래 보관, 데이터 유실 방지)
    "dlq": {
        "enabled": True,
        "max_retries": 3,
        "retry_delay": 60,
        "expiry_hours": 72,
        "retention_days": 30,
        "batch_size": 10,
        "max_replay_attempts": 2,
    },
    
    # Retry - 보수적 설정 (덜 공격적, 백엔드 보호)
    "retry": {
        "max_attempts": 3,
        "backoff_strategy": "exponential",
        "backoff_base": 4,
        "base_delay": 1.0,
        "max_delay": 300.0,
        "min_delay": 1,
        "jitter": True,
        "jitter_percent": 25,
    },
    
    # Rate Limit - 합리적 제한
    "rate_limit": {
        "base_delay": 1.0,
        "max_delay": 60.0,
        "jitter_percent": 30.0,
        "default_retry_after": 5.0,
        "backoff_multiplier": 2.0,
        "control_api_rate_limit": 100,
        "control_api_window_seconds": 60,
        "emergency_rate_limit": 10,
        "emergency_window_seconds": 60,
    },
    
    # SLA - 합리적 기본값
    "sla": {
        "default_hours": 24,
    },
    
    # SLO - Google SRE 권장값
    "slo": {
        "default_window_days": 30,
        "default_target": 0.999,
        "default_fast_burn_rate": 14.4,
        "default_slow_burn_rate": 3.0,
    },
    
    # Security - 엄격한 설정
    "security": {
        "rate_limit_window_seconds": 60,
        "rate_limit_max_requests": 100,
        "temporary_ban_hours": 1,
        "permanent_ban_threshold": 5,
        "suspicious_ip_cache_timeout": 86400,
        "injection_ban_hours": 24,
        "failed_login_threshold": 5,
    },
    
    # Forensic - 보수적 크기 제한 (메모리 보호)
    "forensic": {
        "error_message_max_length": 500,
        "response_body_max_length": 5000,
        "user_agent_max_length": 500,
        "max_stack_frames": 50,
        "max_context_size_bytes": 65536,  # 64KB
        "include_local_variables": False,  # 보안상 비활성화
        "sanitize_sensitive_data": True,
    },
    
    # Logging - 기본 INFO 레벨
    "logging": {
        "dlq_log_level": "INFO",
        "circuit_breaker_log_level": "INFO",
        "replay_log_level": "INFO",
        "sla_log_level": "INFO",
        "forensic_log_level": "DEBUG",
        "emergency_log_level": "WARNING",
        "chaos_log_level": "INFO",
        "l2_storage_log_level": "INFO",
        "include_timestamps": True,
        "include_request_id": True,
        "include_user_info": False,  # 보안상 비활성화
        "console_output_enabled": True,
        "file_output_enabled": False,
        "structured_json": True,
    },
    
    # Notification - 합리적 제한
    "notification": {
        "enabled": True,
        "critical_threshold": 10,
        "warning_threshold": 5,
        "slack_block_text_limit": 3000,
        "description_max_length": 500,
        "action_taken_max_length": 200,
        "title_max_length": 150,
        "notification_timeout_seconds": 10,
    },
    
    # Metrics - 기본 활성화
    "metrics": {
        "enabled": True,
        "prefix": "selfhealing",
        "collection_interval": 60,
        "export_prometheus": True,
        "jitter_enabled": True,
        "jitter_max_delay_seconds": 60.0,
    },
    
    # Error Budget - Google SRE 권장값
    "error_budget": {
        "threshold_healthy": 75.0,
        "threshold_caution": 50.0,
        "threshold_warning": 20.0,
        "threshold_critical": 0.0,
        "burn_rate_fast_critical": 14.4,
        "burn_rate_fast_warning": 6.0,
        "burn_rate_slow_warning": 3.0,
        "burn_rate_slow_info": 1.0,
        "failsafe_alert_enabled": True,
        "failsafe_cooldown_seconds": 300,
        "heartbeat_enabled": True,
        "heartbeat_interval_seconds": 60,
        "heartbeat_timeout_seconds": 120,
        "recovery_alert_enabled": True,
        "recovery_alert_include_downtime": True,
        "escalation_enabled": True,
    },
    
    # Idempotency - 적절한 TTL
    "idempotency": {
        "default_cache_ttl": 60,
        "extended_cache_ttl": 300,
        "short_cache_ttl": 60,
        "clock_skew_tolerance_seconds": 5.0,
    },
    
    # Chaos - 보수적 설정 (안전 우선)
    "chaos": {
        "enabled": False,  # 기본 비활성화
        "max_blast_radius": 0.05,  # 5%로 제한
        "dry_run": True,  # 기본 Dry Run
        "failure_rate": 0.01,  # 1%
        "latency_max_ms": 1000,
    },
    
    # Emergency - 보수적 설정
    "emergency": {
        "auto_trigger_enabled": False,  # 수동 트리거만
        "auto_release_enabled": True,
        "gradual_recovery_steps": 5,
        "recovery_step_duration_seconds": 60,
    },
}


# =============================================================================
# Validation Rules
# =============================================================================

# 설정별 유효성 검증 규칙
VALIDATION_RULES: Dict[str, Dict[str, Tuple[Any, Any]]] = {
    "circuit_breaker": {
        "failure_threshold": (1, 100),
        "recovery_timeout": (1, 3600),
        "success_threshold": (1, 100),
        "half_open_max_calls": (1, 100),
        "half_open_request_limit": (1, 1000),
        "self_ddos_request_threshold": (1, 10000),
        "self_ddos_window_seconds": (1, 300),
        "self_ddos_backoff_multiplier": (1.0, 10.0),
    },
    "dlq": {
        "max_retries": (1, 20),
        "retry_delay": (1, 3600),
        "expiry_hours": (1, 720),
        "retention_days": (1, 365),
        "batch_size": (1, 1000),
        "max_replay_attempts": (1, 10),
    },
    "retry": {
        "max_attempts": (1, 20),
        "backoff_base": (1, 10),
        "base_delay": (0.1, 60.0),
        "max_delay": (1.0, 3600.0),
        "min_delay": (1, 60),
        "jitter_percent": (0, 100),
    },
    "rate_limit": {
        "base_delay": (0.1, 60.0),
        "max_delay": (1.0, 300.0),
        "jitter_percent": (0.0, 100.0),
        "default_retry_after": (0.1, 60.0),
        "backoff_multiplier": (1.0, 10.0),
        "control_api_rate_limit": (1, 10000),
        "emergency_rate_limit": (1, 100),
    },
    "security": {
        "rate_limit_window_seconds": (1, 3600),
        "rate_limit_max_requests": (1, 10000),
        "temporary_ban_hours": (1, 168),
        "permanent_ban_threshold": (1, 100),
        "injection_ban_hours": (1, 720),
        "failed_login_threshold": (1, 100),
    },
    "forensic": {
        "error_message_max_length": (50, 5000),
        "response_body_max_length": (100, 100000),
        "user_agent_max_length": (50, 2000),
        "max_stack_frames": (10, 200),
        "max_context_size_bytes": (1024, 1048576),
    },
    "notification": {
        "critical_threshold": (1, 100),
        "warning_threshold": (1, 100),
        "slack_block_text_limit": (100, 10000),
        "description_max_length": (50, 5000),
        "action_taken_max_length": (50, 1000),
        "title_max_length": (20, 500),
        "notification_timeout_seconds": (1, 60),
    },
    "metrics": {
        "collection_interval": (1, 3600),
        "jitter_max_delay_seconds": (0.0, 300.0),
    },
    "error_budget": {
        "threshold_healthy": (50.0, 100.0),
        "threshold_caution": (20.0, 80.0),
        "threshold_warning": (5.0, 50.0),
        "threshold_critical": (0.0, 20.0),
        "burn_rate_fast_critical": (10.0, 50.0),
        "burn_rate_fast_warning": (3.0, 15.0),
        "burn_rate_slow_warning": (1.0, 10.0),
        "burn_rate_slow_info": (0.5, 3.0),
        "failsafe_cooldown_seconds": (60, 3600),
        "heartbeat_interval_seconds": (10, 300),
        "heartbeat_timeout_seconds": (30, 600),
    },
    "idempotency": {
        "default_cache_ttl": (1, 3600),
        "extended_cache_ttl": (1, 86400),
        "short_cache_ttl": (1, 300),
        "clock_skew_tolerance_seconds": (0.0, 60.0),
    },
    "chaos": {
        "max_blast_radius": (0.0, 0.5),  # 50% 초과 불가
        "failure_rate": (0.0, 0.5),  # 50% 초과 불가
        "latency_max_ms": (0, 10000),
    },
    "sla": {
        "default_hours": (1, 720),
    },
    "slo": {
        "default_window_days": (1, 365),
        "default_target": (0.9, 1.0),
        "default_fast_burn_rate": (1.0, 100.0),
        "default_slow_burn_rate": (0.5, 50.0),
    },
}

# 유효한 로그 레벨
VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

# 유효한 backoff 전략
VALID_BACKOFF_STRATEGIES = {"exponential", "linear", "constant", "decorrelated_jitter"}


# =============================================================================
# Helper Functions
# =============================================================================


def get_safe_default(config_type: str, key: str) -> Optional[Any]:
    """
    안전한 기본값 반환.
    
    Args:
        config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
        key: 설정 키
        
    Returns:
        안전한 기본값 또는 None
    """
    defaults = SAFE_DEFAULTS.get(config_type, {})
    return defaults.get(key)


def get_safe_defaults_for_type(config_type: str) -> Dict[str, Any]:
    """
    특정 설정 유형의 모든 안전한 기본값 반환.
    
    Args:
        config_type: 설정 유형
        
    Returns:
        해당 설정 유형의 안전한 기본값 딕셔너리
    """
    return SAFE_DEFAULTS.get(config_type, {}).copy()


def is_valid_value(config_type: str, key: str, value: Any) -> bool:
    """
    값 유효성 검증.
    
    Args:
        config_type: 설정 유형
        key: 설정 키
        value: 검증할 값
        
    Returns:
        유효하면 True, 아니면 False
    """
    # None 체크
    if value is None:
        return False
    
    # 범위 검증 규칙이 있는 경우
    rules = VALIDATION_RULES.get(config_type, {})
    if key in rules:
        min_val, max_val = rules[key]
        try:
            if value < min_val or value > max_val:
                return False
        except TypeError:
            # 비교 불가능한 타입
            return False
    
    # 로그 레벨 검증
    if key.endswith("_log_level"):
        if value not in VALID_LOG_LEVELS:
            return False
    
    # backoff 전략 검증
    if key == "backoff_strategy":
        if value not in VALID_BACKOFF_STRATEGIES:
            return False
    
    # Boolean 검증
    if key.startswith("enabled") or key.endswith("_enabled"):
        if not isinstance(value, bool):
            return False
    
    return True


def validate_with_safe_fallback(
    config_type: str,
    values: Dict[str, Any],
    log_changes: bool = True
) -> Dict[str, Any]:
    """
    설정값 검증 후 안전한 값으로 폴백.
    
    잘못된 값은 Safe Default로 대체됩니다.
    
    Args:
        config_type: 설정 유형
        values: 검증할 설정값들
        log_changes: 변경 사항 로깅 여부
        
    Returns:
        검증 및 폴백된 설정값 딕셔너리
    """
    result = {}
    defaults = SAFE_DEFAULTS.get(config_type, {})
    
    for key, value in values.items():
        if not is_valid_value(config_type, key, value):
            safe_value = defaults.get(key)
            if safe_value is not None:
                if log_changes:
                    logger.warning(
                        f"[SafeDefault] Invalid {config_type}.{key}={value!r}, "
                        f"using safe default: {safe_value!r}"
                    )
                result[key] = safe_value
            else:
                # Safe default가 없으면 원래 값 유지하되 경고
                if log_changes:
                    logger.warning(
                        f"[SafeDefault] Invalid {config_type}.{key}={value!r}, "
                        f"no safe default available, keeping original"
                    )
                result[key] = value
        else:
            result[key] = value
    
    return result


def validate_all_with_safe_fallback(
    config_dict: Dict[str, Dict[str, Any]],
    log_changes: bool = True
) -> Dict[str, Dict[str, Any]]:
    """
    전체 설정 딕셔너리 검증 후 안전한 값으로 폴백.
    
    Args:
        config_dict: {config_type: {key: value, ...}, ...} 형태의 설정
        log_changes: 변경 사항 로깅 여부
        
    Returns:
        검증 및 폴백된 전체 설정 딕셔너리
    """
    result = {}
    for config_type, values in config_dict.items():
        result[config_type] = validate_with_safe_fallback(
            config_type, values, log_changes
        )
    return result


def apply_safe_defaults_to_missing(
    config_type: str,
    values: Dict[str, Any]
) -> Dict[str, Any]:
    """
    누락된 설정에 Safe Default 적용.
    
    기존 값은 유지하고 누락된 키에만 Safe Default 추가.
    
    Args:
        config_type: 설정 유형
        values: 현재 설정값들
        
    Returns:
        Safe Default가 채워진 설정값 딕셔너리
    """
    defaults = SAFE_DEFAULTS.get(config_type, {})
    result = defaults.copy()
    result.update(values)  # 기존 값이 우선
    return result


def get_validation_errors(
    config_type: str,
    values: Dict[str, Any]
) -> Dict[str, str]:
    """
    설정값 검증 후 오류 목록 반환.
    
    Args:
        config_type: 설정 유형
        values: 검증할 설정값들
        
    Returns:
        {key: error_message} 형태의 오류 딕셔너리
    """
    errors = {}
    rules = VALIDATION_RULES.get(config_type, {})
    
    for key, value in values.items():
        if value is None:
            errors[key] = "Value cannot be None"
            continue
        
        # 범위 검증
        if key in rules:
            min_val, max_val = rules[key]
            try:
                if value < min_val:
                    errors[key] = f"Value {value} is below minimum {min_val}"
                elif value > max_val:
                    errors[key] = f"Value {value} exceeds maximum {max_val}"
            except TypeError:
                errors[key] = f"Value {value!r} is not a valid number"
        
        # 로그 레벨 검증
        if key.endswith("_log_level") and value not in VALID_LOG_LEVELS:
            errors[key] = f"Invalid log level: {value}. Must be one of {VALID_LOG_LEVELS}"
        
        # backoff 전략 검증
        if key == "backoff_strategy" and value not in VALID_BACKOFF_STRATEGIES:
            errors[key] = f"Invalid backoff strategy: {value}. Must be one of {VALID_BACKOFF_STRATEGIES}"
    
    return errors


# =============================================================================
# Startup Validation
# =============================================================================


def validate_startup_config(config: Any, log_changes: bool = True) -> int:
    """
    시작 시 설정 검증 + Safe Default 적용.
    
    SelfHealingConfig 인스턴스의 모든 설정을 검증하고
    잘못된 값은 Safe Default로 대체합니다.
    
    Args:
        config: SelfHealingConfig 인스턴스
        log_changes: 변경 사항 로깅 여부
        
    Returns:
        수정된 설정 수
    """
    changes_count = 0
    
    # config_type -> config attribute 매핑
    config_mapping = {
        "circuit_breaker": "circuit_breaker",
        "dlq": "dlq",
        "retry": "retry",
        "sla": "sla",
        "security": "security",
        "forensic": "forensic",
        "metrics": "metrics",
        "notification": "notification",
        "rate_limit": "rate_limit",
        "idempotency": "idempotency",
    }
    
    for config_type, attr_name in config_mapping.items():
        sub_config = getattr(config, attr_name, None)
        if sub_config is None:
            continue
        
        defaults = SAFE_DEFAULTS.get(config_type, {})
        
        for key, safe_value in defaults.items():
            current = getattr(sub_config, key, None)
            
            if not is_valid_value(config_type, key, current):
                if log_changes:
                    logger.warning(
                        f"[Startup] Invalid {config_type}.{key}={current!r}, "
                        f"applying safe default: {safe_value!r}"
                    )
                try:
                    setattr(sub_config, key, safe_value)
                    changes_count += 1
                except AttributeError:
                    # frozen dataclass의 경우
                    if log_changes:
                        logger.warning(
                            f"[Startup] Cannot modify frozen {config_type}.{key}"
                        )
    
    if log_changes and changes_count > 0:
        logger.info(f"[Startup] Applied {changes_count} safe default(s)")
    
    return changes_count


# =============================================================================
# Chaos-Specific Safety Guards
# =============================================================================


def validate_chaos_config(values: Dict[str, Any]) -> Dict[str, Any]:
    """
    Chaos 설정 특별 검증.
    
    Chaos 엔지니어링은 특히 위험하므로 추가 안전 장치 적용.
    
    Args:
        values: Chaos 설정값들
        
    Returns:
        안전하게 검증된 설정값들
    """
    result = values.copy()
    
    # Blast Radius 강제 제한 (50% 초과 불가)
    if "max_blast_radius" in result:
        if result["max_blast_radius"] > 0.5:
            logger.warning(
                f"[SafeDefault] Chaos max_blast_radius={result['max_blast_radius']} "
                f"exceeds 50%, clamping to 0.5"
            )
            result["max_blast_radius"] = 0.5
        if result["max_blast_radius"] < 0:
            result["max_blast_radius"] = 0.0
    
    # Failure Rate 강제 제한
    if "failure_rate" in result:
        if result["failure_rate"] > 0.5:
            logger.warning(
                f"[SafeDefault] Chaos failure_rate={result['failure_rate']} "
                f"exceeds 50%, clamping to 0.5"
            )
            result["failure_rate"] = 0.5
        if result["failure_rate"] < 0:
            result["failure_rate"] = 0.0
    
    # Production 환경에서는 dry_run 강제
    import os
    if os.environ.get("DJANGO_SETTINGS_MODULE", "").endswith("production"):
        if not result.get("dry_run", True):
            logger.warning(
                "[SafeDefault] Chaos dry_run=False in production, forcing to True"
            )
            result["dry_run"] = True
    
    return result

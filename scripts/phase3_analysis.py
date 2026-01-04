"""
Compare __all__ exports with actual usage in tests and shopping
"""
import re
from collections import Counter
from pathlib import Path

# __all__에 정의된 심볼들 (services/__init__.py에서 추출)
ALL_EXPORTS = [
    # Configuration
    "SelfHealingConfig", "SLAThresholds", "IdempotencyConfig", "SecurityThresholds",
    "NotificationLimits", "SlackChannels", "RetrySettings", "CircuitBreakerSettings",
    "DLQSettings", "ForensicSettings", "get_config", "reload_config", "get_sla_thresholds",
    "get_idempotency_config", "get_security_thresholds", "get_notification_limits",
    "get_slack_channels", "get_retry_settings", "get_circuit_breaker_settings",
    "get_dlq_settings", "get_forensic_settings",
    # Retry
    "RetryHandler", "RetryConfig", "RetryResult", "RetryAction", "MaxRetriesExceededError",
    # Backoff
    "BackoffCalculator", "calculate_backoff",
    # Idempotency
    "IdempotencyService", "IdempotencyKey", "IdempotencyResult", "IdempotencyDomain",
    "get_idempotency_service",
    # Forensic
    "ForensicContext", "capture_forensic_context",
    # Forensic Advisor
    "AdvisoryLevel", "RecommendedAction", "FailurePattern", "ForensicAdvisory",
    "ForensicAdvisorService", "KNOWN_PATTERNS", "get_forensic_advisor",
    "analyze_failed_operation", "analyze_and_update_operation",
    # Chaos Context
    "ChaosExperimentType", "ChaosExperimentStatus", "ChaosExperimentContext",
    "is_chaos_experiment", "get_chaos_context", "attach_chaos_context",
    "resolve_chaos_experiment", "create_chaos_context",
    # Control API
    "ControlAPIService", "ControlRequest", "ControlResponse", "get_control_api_service",
    # DLQ
    "DLQService", "DLQConfig", "DLQEntryResult", "get_dlq_service", "store_to_dlq",
    # Replay
    "ReplayService", "ReplayResult", "BatchReplayResult", "ReplayHandler",
    "get_replay_handler", "get_replay_service", "replay_failed_operation",
    "batch_replay_by_failure_type",
    # Circuit Breaker
    "CircuitBreakerService", "CircuitBreakerConfig", "CircuitBreakerResult",
    "CircuitState", "get_circuit_breaker_service", "should_allow_request",
    "force_open_circuit", "force_close_circuit",
    # Rate Limit / Self-DDoS
    "RateLimitTracker", "get_rate_limit_tracker", "record_rate_limit",
    "should_allow_with_protection", "should_allow_with_ddos_protection", "get_protection_status",
    # Rate Limit Coordinator
    "RateLimitCoordinator", "RateLimitConfig", "RateLimitResult", "get_rate_limit_coordinator",
    # Metrics
    "register_domain", "get_registered_domains", "ALERTING_RULES",
    "record_dlq_item_created", "record_retry_attempt", "record_recovery_time",
    "record_sla_breach", "record_circuit_breaker_state_change",
    "record_circuit_breaker_open_duration", "record_replay_attempt",
    "update_dlq_pending_gauges", "update_dlq_status_gauges",
    "update_circuit_breaker_gauges", "update_retry_success_rates",
    "collect_all_metrics", "track_recovery_time", "track_replay",
    # Security Violation
    "SecurityViolationService", "SecurityViolationResult", "SecurityConfig",
    "ViolationType", "Severity", "SEVERITY_BY_VIOLATION_TYPE",
    "get_security_violation_service", "handle_security_violation",
    # Security Notification
    "SecurityNotificationService", "SecurityNotificationResult", "NotificationResult",
    "NotificationConfig", "NotificationChannel", "get_security_notification_service",
    "notify_security_incident", "send_alert",
    # Unified Notification
    "UnifiedNotificationManager", "NotificationPayload", "NotificationPriority",
    "NotificationCategory", "RoutingPolicy", "get_unified_notification_manager",
    "notify", "notify_security", "notify_sla", "notify_error",
    # Runtime Config
    "RuntimeConfigManager", "get_runtime_config_manager",
    # Health Check
    "HealthCheckService", "HealthStatus", "ReadinessStatus", "PoolHealthStatus",
    "DatabaseCheck", "PoolInfo", "get_health_check_service",
    # System Control
    "SystemControlManager", "SystemState", "get_system_control",
    "is_selfhealing_enabled", "is_dry_run", "should_execute_action",
    # Governance Checks
    "BlockReason", "GovernanceCheckResult", "is_system_enabled", "is_emergency_blocking",
    "is_error_budget_blocking", "check_all_governance", "invalidate_governance_cache",
    "require_system_enabled", "require_not_emergency", "require_error_budget",
    "require_governance", "GovernanceCheckMixin", "TTLCache",
    # Governance Service
    "GovernanceService", "ExpiryCheckResult", "GovernanceNotificationResult",
    "get_governance_service",
    # Chaos Execution
    "ChaosExecutionService", "ExperimentExecutionResult", "DailyReportResult",
    "ApprovalCleanupResult", "PendingApprovalCheckResult", "get_chaos_execution_service",
    # Config Apply
    "ConfigApplyService", "get_config_apply_service",
    # Factory
    "create_failed_operation_repository", "create_circuit_breaker_repository",
    "create_security_incident_repository", "create_dlq_service", "create_replay_service",
    "create_circuit_breaker_service", "create_security_violation_service",
    "get_dlq_service_with_di", "get_replay_service_with_di",
    "get_circuit_breaker_service_with_di", "get_security_violation_service_with_di",
    "reset_service_singletons",
]


def collect_imports(base_paths: list[str], exclude_tests: bool = False):
    """Collect all import symbols from given paths."""
    patterns = []
    
    for base_path in base_paths:
        base = Path(base_path)
        if not base.exists():
            continue
            
        for py_file in base.rglob('*.py'):
            if exclude_tests and 'tests' in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
                
            # Match 'from selfhealing.services import (...)' including multiline
            for match in re.finditer(r'from selfhealing\.services import \(([^)]+)\)', content, re.DOTALL):
                imports = match.group(1)
                for item in imports.split(','):
                    item = item.strip().split()[0] if item.strip() else ''
                    if item and not item.startswith('#'):
                        patterns.append(item)
            
            # Single line imports
            for match in re.finditer(r'from selfhealing\.services import ([A-Za-z_][A-Za-z0-9_\s,]+?)(?:\n|$)', content):
                imports = match.group(1)
                if '(' not in imports:
                    for item in imports.split(','):
                        item = item.strip().split()[0] if item.strip() else ''
                        if item and not item.startswith('#') and not item.startswith('('):
                            patterns.append(item)
    
    return Counter(patterns)


if __name__ == "__main__":
    # 사용 현황 수집
    test_imports = collect_imports(['tests/'])
    shopping_imports = collect_imports(['shopping/'], exclude_tests=True)
    all_used = set(test_imports.keys()) | set(shopping_imports.keys())
    
    print("=" * 80)
    print("Phase 3 분석: __all__ 심볼 사용 현황")
    print("=" * 80)
    print()
    
    # 사용되지 않는 심볼
    unused = set(ALL_EXPORTS) - all_used
    print(f"사용되지 않는 심볼 ({len(unused)}개):")
    print("-" * 80)
    for sym in sorted(unused):
        print(f"  - {sym}")
    print()
    
    # 사용되는 심볼 (유지 필요)
    used = set(ALL_EXPORTS) & all_used
    print(f"사용되는 심볼 ({len(used)}개) - 유지 필요:")
    print("-" * 80)
    for sym in sorted(used):
        test_count = test_imports.get(sym, 0)
        shop_count = shopping_imports.get(sym, 0)
        print(f"  ✓ {sym:50} (tests: {test_count:3}, shopping: {shop_count:2})")
    print()
    
    # __all__에 없지만 사용되는 심볼 (추가 필요하거나 다른 경로로 import)
    extra = all_used - set(ALL_EXPORTS)
    if extra:
        print(f"__all__에 없지만 사용되는 심볼 ({len(extra)}개) - 확인 필요:")
        print("-" * 80)
        for sym in sorted(extra):
            print(f"  ? {sym}")
    print()
    
    # 요약
    print("=" * 80)
    print("Phase 3 작업 요약")
    print("=" * 80)
    print(f"현재 __all__ 심볼 수: {len(ALL_EXPORTS)}")
    print(f"실제 사용 심볼 수: {len(used)}")
    print(f"제거 가능 심볼 수: {len(unused)}")
    print(f"축소 비율: {len(ALL_EXPORTS)} → {len(used)} ({(1 - len(used)/len(ALL_EXPORTS))*100:.1f}% 감소)")

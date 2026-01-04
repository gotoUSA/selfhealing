#!/usr/bin/env python3
"""
Phase 2 Import Refactoring Script

테스트 파일의 하위 패키지 __init__.py re-export import를
직접 import로 변환합니다.

Usage:
    python scripts/refactor_imports.py --dry-run  # 변경 예정 내용 확인
    python scripts/refactor_imports.py            # 실제 변환 수행
"""

import argparse
import os
import re
from pathlib import Path
from typing import Dict, List, Tuple

# =============================================================================
# 변환 매핑 정의
# =============================================================================

# tiering 패키지: 심볼 → 실제 모듈
TIERING_MAPPING = {
    # enums.py
    "TierFallbackReason": "enums",
    "PatternType": "enums",
    "OverrideIdentifierType": "enums",
    # models.py
    "TierResult": "models",
    "TierDefinition": "models",
    "TierMapping": "models",
    "TierOverride": "models",
    # defaults.py
    "STATIC_CRITICAL_PATHS": "defaults",
    "STATIC_CRITICAL_PREFIXES": "defaults",
    "DEFAULT_TIER_DEFINITIONS": "defaults",
    "DEFAULT_TIER_MAPPINGS": "defaults",
    "DEFAULT_TIER_OVERRIDES": "defaults",
    # circuit_breaker.py
    "TieringCircuitBreaker": "circuit_breaker",
    "get_tiering_circuit_breaker": "circuit_breaker",
    # validator.py
    "ValidationResult": "validator",
    "TierConfigValidator": "validator",
    # registry.py
    "TierRegistry": "registry",
    "get_tier_registry": "registry",
    # middleware.py
    "TieringMiddleware": "middleware",
}

# audit 패키지: 심볼 → 실제 모듈
AUDIT_MAPPING = {
    "FileAuditLogAdapter": "file_adapter",
    "StdoutAuditLogAdapter": "stdout_adapter",
    "NullAuditLogAdapter": "null_adapter",
    "get_audit_adapter": "__init__",  # __init__.py에 정의됨
    "reset_audit_adapter": "__init__",
    "set_audit_adapter": "__init__",
    # WORM adapters
    "WORMAdapter": "worm_adapters",
    "S3Config": "worm_adapters",
    "S3ObjectLockAdapter": "worm_adapters",
    "LokiConfig": "worm_adapters",
    "LokiAdapter": "worm_adapters",
    "HTTPWebhookAdapter": "worm_adapters",
    "SidecarConfig": "worm_adapters",
    "SidecarFileWatcher": "worm_adapters",
    "create_worm_adapter": "worm_adapters",
}

# metrics 패키지: 심볼 → 실제 모듈
METRICS_MAPPING = {
    # registry.py
    "get_or_create_counter": "registry",
    "get_or_create_gauge": "registry",
    "get_or_create_histogram": "registry",
    "register_domain": "registry",
    "get_registered_domains": "registry",
    "DEFAULT_DOMAINS": "registry",
    # definitions.py
    "dlq_items_total": "definitions",
    "dlq_pending_gauge": "definitions",
    "dlq_by_status_gauge": "definitions",
    "dlq_created_total": "definitions",
    "retry_attempts_histogram": "definitions",
    "retry_outcomes_total": "definitions",
    "retry_success_rate": "definitions",
    "recovery_time_seconds": "definitions",
    "sla_breach_total": "definitions",
    "human_review_queue_time": "definitions",
    "circuit_breaker_state": "definitions",
    "circuit_breaker_transitions": "definitions",
    "circuit_breaker_open_duration": "definitions",
    # recorders.py
    "record_dlq_item_created": "recorders",
    "record_sla_breach": "recorders",
    "record_retry_attempt": "recorders",
    "record_recovery_time": "recorders",
    "record_circuit_breaker_state_change": "recorders",
    "record_circuit_breaker_open_duration": "recorders",
    "record_l2_timeout": "recorders",
    "record_l2_sync_failure": "recorders",
    "record_l2_latency": "recorders",
    "record_replay_attempt": "recorders",
    "record_error_budget_status": "recorders",
    "record_deployment_freeze_status": "recorders",
    "record_freeze_decision": "recorders",
    "record_active_override": "recorders",
    "record_failsafe_triggered": "recorders",
    "record_failsafe_recovered": "recorders",
    "emit_heartbeat": "recorders",
    "record_override_escalation": "recorders",
    "record_recovery_alert": "recorders",
    # updaters.py
    "update_shadow_log_metrics": "updaters",
    "update_dlq_pending_gauges": "updaters",
    "update_dlq_status_gauges": "updaters",
    "update_circuit_breaker_gauges": "updaters",
    "update_retry_success_rates": "updaters",
    "track_recovery_time": "updaters",
    "track_replay": "updaters",
    "collect_all_metrics": "updaters",
    # alerting_rules.py
    "ALERTING_RULES": "alerting_rules",
}


def parse_import_line(line: str) -> Tuple[str, List[str]]:
    """Parse import line to extract package and symbols."""
    # from package import sym1, sym2
    match = re.match(r'from\s+([\w.]+)\s+import\s+(.+)', line.strip())
    if match:
        package = match.group(1)
        symbols_str = match.group(2)
        # Handle parentheses
        if symbols_str.startswith('('):
            symbols_str = symbols_str[1:]
        if symbols_str.endswith(')'):
            symbols_str = symbols_str[:-1]
        symbols = [s.strip().rstrip(',') for s in symbols_str.split(',') if s.strip()]
        return package, symbols
    return "", []


def group_symbols_by_module(symbols: List[str], mapping: Dict[str, str]) -> Dict[str, List[str]]:
    """Group symbols by their target module."""
    groups: Dict[str, List[str]] = {}
    for sym in symbols:
        module = mapping.get(sym, "__init__")
        if module not in groups:
            groups[module] = []
        groups[module].append(sym)
    return groups


def generate_new_imports(base_package: str, groups: Dict[str, List[str]]) -> str:
    """Generate new import statements."""
    lines = []
    for module, symbols in sorted(groups.items()):
        if module == "__init__":
            # Keep as-is for __init__.py definitions
            lines.append(f"from {base_package} import {', '.join(sorted(symbols))}")
        else:
            lines.append(f"from {base_package}.{module} import {', '.join(sorted(symbols))}")
    return '\n'.join(lines)


def process_file(filepath: Path, dry_run: bool = True) -> List[str]:
    """Process a single file and return list of changes made."""
    changes = []
    
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    original_content = content
    
    # Define patterns to transform
    patterns = [
        (r'from selfhealing\.api\.django\.tiering import', 
         'selfhealing.api.django.tiering', 
         TIERING_MAPPING),
        (r'from selfhealing\.adapters\.audit import',
         'selfhealing.adapters.audit',
         AUDIT_MAPPING),
        (r'from selfhealing\.services\.metrics import',
         'selfhealing.services.metrics',
         METRICS_MAPPING),
    ]
    
    for pattern, base_package, mapping in patterns:
        # Find multi-line imports
        multi_line_pattern = rf'{pattern}\s*\(([\s\S]*?)\)'
        
        def replace_multi_line(match):
            full_match = match.group(0)
            symbols_str = match.group(1)
            symbols = [s.strip().rstrip(',') for s in re.split(r'[,\n]', symbols_str) if s.strip()]
            
            groups = group_symbols_by_module(symbols, mapping)
            new_import = generate_new_imports(base_package, groups)
            
            if new_import != full_match:
                changes.append(f"  {filepath}: {len(symbols)} symbols → {len(groups)} modules")
            
            return new_import
        
        content = re.sub(multi_line_pattern, replace_multi_line, content)
        
        # Find single-line imports
        single_line_pattern = rf'{pattern}\s+([^(\n]+)'
        
        def replace_single_line(match):
            full_match = match.group(0)
            symbols_str = match.group(1)
            symbols = [s.strip().rstrip(',') for s in symbols_str.split(',') if s.strip()]
            
            groups = group_symbols_by_module(symbols, mapping)
            new_import = generate_new_imports(base_package, groups)
            
            if new_import != full_match:
                changes.append(f"  {filepath}: {symbols} → direct import")
            
            return new_import
        
        content = re.sub(single_line_pattern, replace_single_line, content)
    
    if content != original_content and not dry_run:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
    
    return changes


def main():
    parser = argparse.ArgumentParser(description='Refactor imports to direct paths')
    parser.add_argument('--dry-run', action='store_true', help='Show changes without applying')
    parser.add_argument('--path', default='tests/', help='Path to process')
    args = parser.parse_args()
    
    base_path = Path(args.path)
    all_changes = []
    
    for filepath in base_path.rglob('*.py'):
        changes = process_file(filepath, dry_run=args.dry_run)
        all_changes.extend(changes)
    
    if all_changes:
        print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Changes:")
        for change in all_changes:
            print(change)
        print(f"\nTotal: {len(all_changes)} changes")
    else:
        print("No changes needed.")
    
    if args.dry_run:
        print("\nRun without --dry-run to apply changes.")


if __name__ == '__main__':
    main()

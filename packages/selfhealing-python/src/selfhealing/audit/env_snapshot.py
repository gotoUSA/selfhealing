"""
Environment Variable Snapshot for Audit Trail.

Records a snapshot of Self-Healing related environment variables
at system startup for audit and forensic analysis.

Features:
- Automatic masking of sensitive values (SECRET, PASSWORD, TOKEN, KEY, CREDENTIAL)
- SHA256 hash for change detection
- Integration with AuditLogger via log_config_change

Usage:
    This module is called automatically by SelfHealingConfig.ready()
    via post_migrate signal.

Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1.md
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Self-Healing 관련 환경변수 prefix
TRACKED_PREFIXES: List[str] = [
    "SELFHEALING_",
    "CIRCUIT_BREAKER_",
    "DLQ_",
    "SLA_",
    "CHAOS_",
]

# 민감 키워드 (마스킹 대상)
SENSITIVE_KEYWORDS: List[str] = [
    "SECRET",
    "PASSWORD",
    "TOKEN",
    "KEY",
    "CREDENTIAL",
    "API_KEY",
    "PRIVATE",
]


def collect_env_snapshot() -> Dict[str, Any]:
    """
    Collect Self-Healing related environment variables snapshot.

    Sensitive values are automatically masked for security.

    Returns:
        dict: {
            "variables": {"SELFHEALING_DLQ_ENABLED": "true", ...},
            "hash": "sha256:abc123...",
            "count": 15
        }

    Example:
        >>> snapshot = collect_env_snapshot()
        >>> print(snapshot["count"])
        12
        >>> print(snapshot["hash"])
        sha256:a1b2c3d4e5f6...
    """
    variables: Dict[str, str] = {}

    for key, value in os.environ.items():
        # Prefix 매칭
        if any(key.startswith(prefix) for prefix in TRACKED_PREFIXES):
            # 민감 정보 마스킹
            if any(kw in key.upper() for kw in SENSITIVE_KEYWORDS):
                variables[key] = "***MASKED***"
            else:
                variables[key] = value

    # 변경 감지용 해시 (마스킹된 값 기준)
    sorted_items = sorted(variables.items())
    hash_input = str(sorted_items).encode("utf-8")
    config_hash = hashlib.sha256(hash_input).hexdigest()[:16]

    return {
        "variables": variables,
        "hash": f"sha256:{config_hash}",
        "count": len(variables),
    }


def log_env_snapshot_to_audit() -> bool:
    """
    Log environment variable snapshot to AuditService.

    Called from SelfHealingConfig.ready() via post_migrate signal.

    Returns:
        bool: True if successfully logged, False otherwise.

    Note:
        This is a best-effort operation. If logging fails,
        the system continues to start normally.
    """
    try:
        from selfhealing.audit import log_config_change

        snapshot = collect_env_snapshot()

        if snapshot["count"] == 0:
            logger.debug("[EnvAudit] No tracked environment variables found")
            return True

        # Log to audit system
        success = log_config_change(
            config_type="environment_variables",
            config_key="startup_snapshot",
            old_value=None,
            new_value=snapshot["variables"],
            user="system_startup",
            reason="Application startup - environment snapshot",
            metadata={
                "hash": snapshot["hash"],
                "variable_count": snapshot["count"],
                "source": "post_migrate_signal",
            },
        )

        logger.info(
            f"[EnvAudit] Snapshot recorded: "
            f"count={snapshot['count']}, hash={snapshot['hash']}"
        )

        return success

    except ImportError as e:
        # Audit module not available - skip gracefully
        logger.debug(f"[EnvAudit] Audit module not available: {e}")
        return False

    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(f"[EnvAudit] Failed to record snapshot: {e}")
        return False


def get_env_snapshot_summary() -> Dict[str, Any]:
    """
    Get a summary of the current environment snapshot.

    Useful for debugging and status endpoints.

    Returns:
        dict: Snapshot summary with hash and count.
    """
    snapshot = collect_env_snapshot()
    return {
        "hash": snapshot["hash"],
        "count": snapshot["count"],
        "tracked_prefixes": TRACKED_PREFIXES,
    }

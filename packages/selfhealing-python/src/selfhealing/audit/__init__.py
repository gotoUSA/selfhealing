"""
Self-Healing Audit Logging Package.

Provides comprehensive audit logging for configuration changes with:
- Privacy-compliant IP masking (GDPR/CCPA)
- Hash chain integrity for tamper detection
- Trace ID correlation
- Pluggable backends (local, cloud, WORM storage)

Usage:
    from selfhealing.audit import log_config_change, get_audit_logger

    # Simple usage
    log_config_change(
        config_type="RETRY_CONFIG",
        config_key="max_retries",
        old_value=3,
        new_value=5,
        user="admin",
        request=request,  # Django request object
    )

    # Advanced usage
    logger = get_audit_logger()
    logger.log_config_update(...)

    # Extended features are lazily loaded:
    from selfhealing.audit import CloudWatchBackend, S3WORMBackend
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from selfhealing.audit.integrity import HashChainManager

# =============================================================================
# CORE API - 직접 import (11개) - 가장 자주 사용되는 핵심 API
# NOTE: self_audit 함수는 서브모듈 이름과 동일하여 lazy import 불가,
#       직접 import 필요 (Python import 시스템 제약)
# =============================================================================
from selfhealing.audit.logger import (
    AuditLogger,
    get_audit_logger,
    log_config_change,
)
from selfhealing.audit.masking import (
    hash_for_audit,
    mask_email,
    mask_ip,
)

# self_audit 함수는 모듈명과 동일하여 __getattr__ lazy import 불가 - 직접 import
from selfhealing.audit.self_audit import self_audit as self_audit
from selfhealing.audit.trace import (
    generate_trace_id,
    get_trace_id,
    set_trace_id,
)

# =============================================================================
# LAZY IMPORTS - 106개 심볼
# =============================================================================
_LAZY_IMPORTS: dict[str, tuple[str, str]] = {
    # logger (추가)
    "AuditConfigChangeEvent": ("selfhealing.audit.logger", "AuditConfigChangeEvent"),
    "ConfigChangeEvent": ("selfhealing.audit.logger", "ConfigChangeEvent"),  # deprecated alias
    "ConfigAuditAction": ("selfhealing.audit.logger", "ConfigAuditAction"),
    "AuditAction": ("selfhealing.audit.logger", "AuditAction"),  # deprecated alias
    # masking (추가)
    "mask_sensitive_fields": ("selfhealing.audit.masking", "mask_sensitive_fields"),
    "extract_ip_from_request": ("selfhealing.audit.masking", "extract_ip_from_request"),
    # trace (추가)
    "TraceContext": ("selfhealing.audit.trace", "TraceContext"),
    "trace_id_middleware": ("selfhealing.audit.trace", "trace_id_middleware"),
    # integrity (추가)
    "HashChainVerifier": ("selfhealing.audit.integrity", "HashChainVerifier"),
    "verify_audit_log_integrity": (
        "selfhealing.audit.integrity",
        "verify_audit_log_integrity",
    ),
    # backends (13개)
    "AuditBackend": ("selfhealing.audit.backends", "AuditBackend"),
    "AsyncAuditBackend": ("selfhealing.audit.backends", "AsyncAuditBackend"),
    "BackendHealth": ("selfhealing.audit.backends", "BackendHealth"),
    "BackendStatus": ("selfhealing.audit.backends", "BackendStatus"),
    "BufferedBackend": ("selfhealing.audit.backends", "BufferedBackend"),
    "CloudWatchBackend": ("selfhealing.audit.backends", "CloudWatchBackend"),
    "CompositeBackend": ("selfhealing.audit.backends", "CompositeBackend"),
    "DatadogBackend": ("selfhealing.audit.backends", "DatadogBackend"),
    "LocalFileBackend": ("selfhealing.audit.backends", "LocalFileBackend"),
    "RemoteAuditBackend": ("selfhealing.audit.backends", "RemoteAuditBackend"),
    "S3WORMBackend": ("selfhealing.audit.backends", "S3WORMBackend"),
    "create_composite_backend": (
        "selfhealing.audit.backends",
        "create_composite_backend",
    ),
    "get_default_backend": ("selfhealing.audit.backends", "get_default_backend"),
    # resilience (15개)
    "CircuitBreaker": ("selfhealing.audit.resilience", "CircuitBreaker"),
    "AuditCircuitBreakerConfig": ("selfhealing.audit.resilience", "AuditCircuitBreakerConfig"),
    "CircuitBreakerRegistry": (
        "selfhealing.audit.resilience",
        "CircuitBreakerRegistry",
    ),
    "CircuitBreakerSnapshot": ("selfhealing.audit.resilience", "CircuitBreakerSnapshot"),
    "CircuitState": ("selfhealing.audit.resilience", "CircuitState"),
    "AuditMetrics": ("selfhealing.audit.resilience", "AuditMetrics"),
    "SyslogFallback": ("selfhealing.audit.resilience", "SyslogFallback"),
    "DegradedModeManager": ("selfhealing.audit.resilience", "DegradedModeManager"),
    "InMemoryAuditBuffer": ("selfhealing.audit.resilience", "InMemoryAuditBuffer"),
    "get_circuit_breaker": ("selfhealing.audit.resilience", "get_circuit_breaker"),
    "get_audit_metrics": ("selfhealing.audit.resilience", "get_audit_metrics"),
    "get_syslog_fallback": ("selfhealing.audit.resilience", "get_syslog_fallback"),
    "get_degraded_mode_manager": (
        "selfhealing.audit.resilience",
        "get_degraded_mode_manager",
    ),
    "get_inmemory_audit_buffer": (
        "selfhealing.audit.resilience",
        "get_inmemory_audit_buffer",
    ),
    "log_critical_to_syslog": (
        "selfhealing.audit.resilience",
        "log_critical_to_syslog",
    ),
    # env_snapshot (5개)
    "collect_env_snapshot": ("selfhealing.audit.env_snapshot", "collect_env_snapshot"),
    "log_env_snapshot_to_audit": (
        "selfhealing.audit.env_snapshot",
        "log_env_snapshot_to_audit",
    ),
    "get_env_snapshot_summary": (
        "selfhealing.audit.env_snapshot",
        "get_env_snapshot_summary",
    ),
    "TRACKED_PREFIXES": ("selfhealing.audit.env_snapshot", "TRACKED_PREFIXES"),
    "SENSITIVE_KEYWORDS": ("selfhealing.audit.env_snapshot", "SENSITIVE_KEYWORDS"),
    # config (3개)
    "AuditConfig": ("selfhealing.audit.config", "AuditConfig"),
    "COMPLIANCE_RETENTION_DAYS": (
        "selfhealing.audit.config",
        "COMPLIANCE_RETENTION_DAYS",
    ),
    "get_recommended_retention": (
        "selfhealing.audit.config",
        "get_recommended_retention",
    ),
    # continuous_audit (1개)
    "ContinuousAuditRecorder": (
        "selfhealing.audit.continuous_audit",
        "ContinuousAuditRecorder",
    ),
    # ring_buffer (3개)
    "RingBuffer": ("selfhealing.audit.ring_buffer", "RingBuffer"),
    "RingBufferStats": ("selfhealing.audit.ring_buffer", "RingBufferStats"),
    "BackpressureStrategy": ("selfhealing.scaling.config", "BackpressureStrategy"),
    # self_audit (3개) - self_audit 함수는 직접 import (모듈명 충돌)
    "SelfAuditLogger": ("selfhealing.audit.self_audit", "SelfAuditLogger"),
    "SelfAuditEvent": ("selfhealing.audit.self_audit", "SelfAuditEvent"),
    "SelfAuditStats": ("selfhealing.audit.self_audit", "SelfAuditStats"),
    # checksum (10개)
    "compute_crc32": ("selfhealing.audit.checksum", "compute_crc32"),
    "compute_sha256": ("selfhealing.audit.checksum", "compute_sha256"),
    "verify_crc32": ("selfhealing.audit.checksum", "verify_crc32"),
    "verify_sha256": ("selfhealing.audit.checksum", "verify_sha256"),
    "compute_checksum": ("selfhealing.audit.checksum", "compute_checksum"),
    "verify_checksum": ("selfhealing.audit.checksum", "verify_checksum"),
    "ChecksumResult": ("selfhealing.audit.checksum", "ChecksumResult"),
    "checksum_dict": ("selfhealing.audit.checksum", "checksum_dict"),
    "checksum_file": ("selfhealing.audit.checksum", "checksum_file"),
    "verify_file_checksum": ("selfhealing.audit.checksum", "verify_file_checksum"),
    # resilient_recorder (2개)
    "ResilientContinuousAuditRecorder": (
        "selfhealing.audit.resilient_recorder",
        "ResilientContinuousAuditRecorder",
    ),
    "ResilientRecorderConfig": (
        "selfhealing.audit.resilient_recorder",
        "ResilientRecorderConfig",
    ),
    # wal (8개)
    "WriteAheadLog": ("selfhealing.audit.wal", "WriteAheadLog"),
    "WALConfig": ("selfhealing.audit.wal", "WALConfig"),
    "WALEntry": ("selfhealing.audit.wal", "WALEntry"),
    "WALError": ("selfhealing.audit.wal", "WALError"),
    "WALCorruptionError": ("selfhealing.audit.wal", "WALCorruptionError"),
    "WALState": ("selfhealing.audit.wal", "WALState"),
    "WALStats": ("selfhealing.audit.wal", "WALStats"),
    "create_wal": ("selfhealing.audit.wal", "create_wal"),
    # audit_watchdog (9개)
    "AuditWatchdog": ("selfhealing.audit.audit_watchdog", "AuditWatchdog"),
    "AuditWatchdogConfig": ("selfhealing.audit.audit_watchdog", "AuditWatchdogConfig"),
    "AuditWatchdogStatus": ("selfhealing.audit.audit_watchdog", "AuditWatchdogStatus"),
    "WatchdogStats": ("selfhealing.audit.audit_watchdog", "WatchdogStats"),
    "HeartbeatTarget": ("selfhealing.audit.audit_watchdog", "HeartbeatTarget"),
    "WatchdogChecker": ("selfhealing.audit.audit_watchdog", "WatchdogChecker"),
    "get_watchdog": ("selfhealing.audit.audit_watchdog", "get_watchdog"),
    "start_watchdog": ("selfhealing.audit.audit_watchdog", "start_watchdog"),
    "stop_watchdog": ("selfhealing.audit.audit_watchdog", "stop_watchdog"),
    # verify_audit_integrity (4개)
    "AuditIntegrityVerifier": (
        "selfhealing.audit.verify_audit_integrity",
        "AuditIntegrityVerifier",
    ),
    "VerificationResult": (
        "selfhealing.audit.verify_audit_integrity",
        "VerificationResult",
    ),
    "VerificationSummary": (
        "selfhealing.audit.verify_audit_integrity",
        "VerificationSummary",
    ),
    "OutputFormat": ("selfhealing.audit.verify_audit_integrity", "OutputFormat"),
    # audit_integration (10개)
    "EventSeverity": ("selfhealing.utils.async_logger", "EventSeverity"),
    "AsyncLoggerConfig": ("selfhealing.audit.audit_integration", "AsyncLoggerConfig"),
    "AsyncLoggerAdapter": ("selfhealing.audit.audit_integration", "AsyncLoggerAdapter"),
    "AuditObserverEventType": ("selfhealing.audit.audit_integration", "AuditObserverEventType"),
    "AuditEventData": ("selfhealing.audit.audit_integration", "AuditEventData"),
    "AuditEventObserver": ("selfhealing.audit.audit_integration", "AuditEventObserver"),
    "AsyncLoggerObserver": (
        "selfhealing.audit.audit_integration",
        "AsyncLoggerObserver",
    ),
    "IntegratedAuditRecorder": (
        "selfhealing.audit.audit_integration",
        "IntegratedAuditRecorder",
    ),
    "configure_integration": (
        "selfhealing.audit.audit_integration",
        "configure_integration",
    ),
    "create_command_center_callback": (
        "selfhealing.audit.audit_integration",
        "create_command_center_callback",
    ),
    # export (5개)
    "AuditExporter": ("selfhealing.audit.export", "AuditExporter"),
    "ExportFormat": ("selfhealing.audit.export", "ExportFormat"),
    "ExportTarget": ("selfhealing.audit.export", "ExportTarget"),
    "ExportOptions": ("selfhealing.audit.export", "ExportOptions"),
    "ExportStats": ("selfhealing.audit.export", "ExportStats"),
    # signed_manifest (5개)
    "MerkleTree": ("selfhealing.audit.signed_manifest", "MerkleTree"),
    "RFC3161Timestamp": ("selfhealing.audit.signed_manifest", "RFC3161Timestamp"),
    "RFC3161Client": ("selfhealing.audit.signed_manifest", "RFC3161Client"),
    "SignedManifest": ("selfhealing.audit.signed_manifest", "SignedManifest"),
    "ManifestEntry": ("selfhealing.audit.signed_manifest", "ManifestEntry"),
    # event_buffer (5개)
    "AuditEventType": ("selfhealing.audit.event_buffer", "AuditEventType"),
    "BufferEventType": ("selfhealing.audit.event_buffer", "AuditEventType"),  # backward-compat alias
    "AuditEvent": ("selfhealing.audit.event_buffer", "AuditEvent"),
    "RequestAuditBuffer": ("selfhealing.audit.event_buffer", "RequestAuditBuffer"),
    "add_audit_event": ("selfhealing.audit.event_buffer", "add_audit_event"),
    # checkpoint_manager (4개)
    "CheckpointManager": ("selfhealing.audit.checkpoint_manager", "CheckpointManager"),
    "CheckpointData": ("selfhealing.audit.checkpoint_manager", "CheckpointData"),
    "CheckpointError": ("selfhealing.audit.checkpoint_strategy", "CheckpointError"),
    "get_checkpoint_manager": ("selfhealing.audit.checkpoint_manager", "get_checkpoint_manager"),
}

# Cache for loaded symbols
_loaded_symbols: dict[str, object] = {}


def __getattr__(name: str) -> object:
    """Lazy import for audit symbols."""
    if name in _LAZY_IMPORTS:
        if name not in _loaded_symbols:
            module_path, attr_name = _LAZY_IMPORTS[name]
            module = importlib.import_module(module_path)
            _loaded_symbols[name] = getattr(module, attr_name)
        return _loaded_symbols[name]

    raise AttributeError(f"module 'selfhealing.audit' has no attribute '{name}'")


def __dir__() -> list[str]:
    """List available symbols for IDE autocompletion."""
    return list(__all__)


# TYPE_CHECKING block for IDE support
if TYPE_CHECKING:
    from selfhealing.audit.audit_integration import (
        AsyncLoggerAdapter,
        AsyncLoggerConfig,
        AsyncLoggerObserver,
        AuditEventData,
        AuditEventObserver,
        AuditObserverEventType,
        EventSeverity,
        IntegratedAuditRecorder,
        configure_integration,
        create_command_center_callback,
    )
    from selfhealing.audit.audit_watchdog import (
        AuditWatchdog,
        HeartbeatTarget,
        WatchdogChecker,
        AuditWatchdogConfig,
        AuditWatchdogStatus,
        WatchdogStats,
        get_watchdog,
        start_watchdog,
        stop_watchdog,
    )
    from selfhealing.audit.backends import (
        AsyncAuditBackend,
        AuditBackend,
        BackendHealth,
        BackendStatus,
        BufferedBackend,
        CloudWatchBackend,
        CompositeBackend,
        DatadogBackend,
        LocalFileBackend,
        RemoteAuditBackend,
        S3WORMBackend,
        create_composite_backend,
        get_default_backend,
    )
    from selfhealing.audit.checksum import (
        ChecksumResult,
        checksum_dict,
        checksum_file,
        compute_checksum,
        compute_crc32,
        compute_sha256,
        verify_checksum,
        verify_crc32,
        verify_file_checksum,
        verify_sha256,
    )
    from selfhealing.audit.config import (
        COMPLIANCE_RETENTION_DAYS,
        AuditConfig,
        get_recommended_retention,
    )
    from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
    from selfhealing.audit.env_snapshot import (
        SENSITIVE_KEYWORDS,
        TRACKED_PREFIXES,
        collect_env_snapshot,
        get_env_snapshot_summary,
        log_env_snapshot_to_audit,
    )
    from selfhealing.audit.event_buffer import (
        AuditEvent,
        AuditEventType,
        RequestAuditBuffer,
        add_audit_event,
    )
    from selfhealing.audit.export import (
        AuditExporter,
        ExportFormat,
        ExportOptions,
        ExportStats,
        ExportTarget,
    )
    from selfhealing.audit.integrity import (
        HashChainVerifier,
        verify_audit_log_integrity,
    )
    from selfhealing.audit.logger import ConfigAuditAction, AuditAction, AuditConfigChangeEvent, ConfigChangeEvent
    from selfhealing.audit.masking import extract_ip_from_request, mask_sensitive_fields
    from selfhealing.audit.resilience import (
        AuditMetrics,
        CircuitBreaker,
        AuditCircuitBreakerConfig,
        CircuitBreakerRegistry,
        CircuitBreakerSnapshot,
        CircuitState,
        DegradedModeManager,
        InMemoryAuditBuffer,
        SyslogFallback,
        get_audit_metrics,
        get_circuit_breaker,
        get_degraded_mode_manager,
        get_inmemory_audit_buffer,
        get_syslog_fallback,
        log_critical_to_syslog,
    )
    from selfhealing.audit.resilient_recorder import (
        ResilientContinuousAuditRecorder,
        ResilientRecorderConfig,
    )
    from selfhealing.audit.ring_buffer import (
        BackpressureStrategy,
        RingBuffer,
        RingBufferStats,
    )
    from selfhealing.audit.self_audit import (
        SelfAuditEvent,
        SelfAuditLogger,
        SelfAuditStats,
        self_audit,
    )
    from selfhealing.audit.signed_manifest import (
        ManifestEntry,
        MerkleTree,
        RFC3161Client,
        RFC3161Timestamp,
        SignedManifest,
    )
    from selfhealing.audit.trace import TraceContext, trace_id_middleware
    from selfhealing.audit.verify_audit_integrity import (
        AuditIntegrityVerifier,
        OutputFormat,
        VerificationResult,
        VerificationSummary,
    )
    from selfhealing.audit.wal import (
        WALConfig,
        WALCorruptionError,
        WALEntry,
        WALError,
        WALState,
        WALStats,
        WriteAheadLog,
        create_wal,
    )


__all__ = [
    # Main API (직접 import)
    "AuditLogger",
    "get_audit_logger",
    "log_config_change",
    "AuditConfigChangeEvent",
    "ConfigChangeEvent",  # deprecated alias
    "ConfigAuditAction",
    "AuditAction",  # deprecated alias
    # Masking utilities
    "mask_ip",
    "mask_email",
    "hash_for_audit",
    "mask_sensitive_fields",
    "extract_ip_from_request",
    # Integrity
    "HashChainManager",
    "HashChainVerifier",
    "verify_audit_log_integrity",
    # Trace ID
    "generate_trace_id",
    "get_trace_id",
    "set_trace_id",
    "TraceContext",
    "trace_id_middleware",
    # Backend base classes
    "AuditBackend",
    "AsyncAuditBackend",
    "BackendHealth",
    "BackendStatus",
    "BufferedBackend",
    "CompositeBackend",
    # Backend implementations
    "LocalFileBackend",
    "CloudWatchBackend",
    "DatadogBackend",
    "S3WORMBackend",
    "RemoteAuditBackend",
    # Factory functions
    "get_default_backend",
    "create_composite_backend",
    # Resilience
    "CircuitBreaker",
    "AuditCircuitBreakerConfig",
    "CircuitBreakerRegistry",
    "CircuitBreakerSnapshot",
    "CircuitState",
    "AuditMetrics",
    "SyslogFallback",
    "DegradedModeManager",
    "InMemoryAuditBuffer",
    "get_circuit_breaker",
    "get_audit_metrics",
    "get_syslog_fallback",
    "get_degraded_mode_manager",
    "get_inmemory_audit_buffer",
    "log_critical_to_syslog",
    # Environment Snapshot
    "collect_env_snapshot",
    "log_env_snapshot_to_audit",
    "get_env_snapshot_summary",
    "TRACKED_PREFIXES",
    "SENSITIVE_KEYWORDS",
    # Continuous Audit (Big 4 Style)
    "AuditConfig",
    "ContinuousAuditRecorder",
    "COMPLIANCE_RETENTION_DAYS",
    "get_recommended_retention",
    # Ring Buffer
    "RingBuffer",
    "RingBufferStats",
    "BackpressureStrategy",
    # Self-Audit
    "SelfAuditLogger",
    "SelfAuditEvent",
    "SelfAuditStats",
    "self_audit",
    # Checksum Utilities
    "compute_crc32",
    "compute_sha256",
    "verify_crc32",
    "verify_sha256",
    "compute_checksum",
    "verify_checksum",
    "ChecksumResult",
    "checksum_dict",
    "checksum_file",
    "verify_file_checksum",
    # Resilient Recorder
    "ResilientContinuousAuditRecorder",
    "ResilientRecorderConfig",
    # WAL (Write-Ahead Log)
    "WriteAheadLog",
    "WALConfig",
    "WALEntry",
    "WALError",
    "WALCorruptionError",
    "WALState",
    "WALStats",
    "create_wal",
    # Audit Watchdog (Dead Man's Switch)
    "AuditWatchdog",
    "AuditWatchdogConfig",
    "AuditWatchdogStatus",
    "WatchdogStats",
    "HeartbeatTarget",
    "WatchdogChecker",
    "get_watchdog",
    "start_watchdog",
    "stop_watchdog",
    # Audit Integrity Verifier (CLI Tool)
    "AuditIntegrityVerifier",
    "VerificationResult",
    "VerificationSummary",
    "OutputFormat",
    # Audit Integration (AsyncLogger + ContinuousAudit)
    "EventSeverity",
    "AsyncLoggerConfig",
    "AsyncLoggerAdapter",
    "AuditObserverEventType",
    "AuditEventData",
    "AuditEventObserver",
    "AsyncLoggerObserver",
    "IntegratedAuditRecorder",
    "configure_integration",
    "create_command_center_callback",
    # Export CLI Tool
    "AuditExporter",
    "ExportFormat",
    "ExportTarget",
    "ExportOptions",
    "ExportStats",
    # Signed Manifest (Merkle Tree + RFC 3161)
    "MerkleTree",
    "RFC3161Timestamp",
    "RFC3161Client",
    "SignedManifest",
    "ManifestEntry",
    # Event Buffer (RequestAuditBuffer for Gateway Pipeline)
    "AuditEvent",
    "BufferEventType",
    "RequestAuditBuffer",
    "add_audit_event",
    # Checkpoint Manager (WAL 처리 시퀀스 영속화)
    "CheckpointManager",
    "CheckpointData",
    "CheckpointError",
    "get_checkpoint_manager",
]

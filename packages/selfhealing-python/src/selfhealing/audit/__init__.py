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
"""

from selfhealing.audit.backends import (
    AuditBackend,
    AsyncAuditBackend,
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
from selfhealing.audit.integrity import (
    HashChainManager,
    HashChainVerifier,
    verify_audit_log_integrity,
)
from selfhealing.audit.logger import (
    AuditAction,
    AuditLogger,
    ConfigChangeEvent,
    get_audit_logger,
    log_config_change,
)
from selfhealing.audit.masking import (
    extract_ip_from_request,
    hash_for_audit,
    mask_email,
    mask_ip,
    mask_sensitive_fields,
)
from selfhealing.audit.trace import (
    TraceContext,
    generate_trace_id,
    get_trace_id,
    set_trace_id,
    trace_id_middleware,
)
from selfhealing.audit.resilience import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    CircuitState,
    AuditMetrics,
    SyslogFallback,
    DegradedModeManager,
    get_circuit_breaker,
    get_audit_metrics,
    get_syslog_fallback,
    get_degraded_mode_manager,
    log_critical_to_syslog,
)
from selfhealing.audit.env_snapshot import (
    collect_env_snapshot,
    log_env_snapshot_to_audit,
    get_env_snapshot_summary,
    TRACKED_PREFIXES,
    SENSITIVE_KEYWORDS,
)
from selfhealing.audit.config import (
    AuditConfig,
    COMPLIANCE_RETENTION_DAYS,
    get_recommended_retention,
)
from selfhealing.audit.continuous_audit import (
    ContinuousAuditRecorder,
)
from selfhealing.audit.ring_buffer import (
    RingBuffer,
    RingBufferStats,
    BackpressureStrategy,
)
from selfhealing.audit.self_audit import (
    SelfAuditLogger,
    SelfAuditEvent,
    SelfAuditStats,
    self_audit,
)
from selfhealing.audit.checksum import (
    compute_crc32,
    compute_sha256,
    verify_crc32,
    verify_sha256,
    compute_checksum,
    verify_checksum,
    ChecksumResult,
    checksum_dict,
    checksum_file,
    verify_file_checksum,
)
from selfhealing.audit.resilient_recorder import (
    ResilientContinuousAuditRecorder,
    ResilientRecorderConfig,
)
from selfhealing.audit.wal import (
    WriteAheadLog,
    WALConfig,
    WALEntry,
    WALError,
    WALCorruptionError,
    WALState,
    WALStats,
    create_wal,
)
from selfhealing.audit.audit_watchdog import (
    AuditWatchdog,
    WatchdogConfig,
    WatchdogState,
    WatchdogStats,
    HeartbeatTarget,
    WatchdogChecker,
    get_watchdog,
    start_watchdog,
    stop_watchdog,
)
from selfhealing.audit.verify_audit_integrity import (
    AuditIntegrityVerifier,
    VerificationResult,
    VerificationSummary,
    OutputFormat,
)
from selfhealing.audit.audit_integration import (
    EventSeverity,
    AsyncLoggerConfig,
    AsyncLoggerAdapter,
    AuditEventType,
    AuditEventData,
    AuditEventObserver,
    AsyncLoggerObserver,
    IntegratedAuditRecorder,
    configure_integration,
    create_command_center_callback,
)
from selfhealing.audit.export import (
    AuditExporter,
    ExportFormat,
    ExportTarget,
    ExportOptions,
    ExportStats,
)
from selfhealing.audit.signed_manifest import (
    MerkleTree,
    RFC3161Timestamp,
    RFC3161Client,
    SignedManifest,
    ManifestEntry,
)

__all__ = [
    # Main API
    "AuditLogger",
    "get_audit_logger",
    "log_config_change",
    "ConfigChangeEvent",
    "AuditAction",
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
    "CircuitBreakerConfig",
    "CircuitBreakerRegistry",
    "CircuitState",
    "AuditMetrics",
    "SyslogFallback",
    "DegradedModeManager",
    "get_circuit_breaker",
    "get_audit_metrics",
    "get_syslog_fallback",
    "get_degraded_mode_manager",
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
    "WatchdogConfig",
    "WatchdogState",
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
    "AuditEventType",
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
]

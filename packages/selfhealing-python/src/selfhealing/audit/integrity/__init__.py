"""
Integrity Package.

This package contains modular hash chain integrity implementations.
All classes are re-exported here for backward compatibility.
"""

from __future__ import annotations

# Daily Hash Anchor
from selfhealing.audit.integrity.anchor import (
    DailyHashAnchor,
)

# Cold Storage (Phase 6)
from selfhealing.audit.integrity.cold_storage import (
    AnchorColdStorage,
    ArchiveResult,
    LocalFileColdStorage,
)

# Factory
from selfhealing.audit.integrity.factory import (
    create_hash_chain_manager,
)

# Health Score (Phase 6)
from selfhealing.audit.integrity.health_score import (
    IntegrityHealthMetrics,
    IntegrityHealthScore,
    IntegrityRecoveryEvent,
    get_integrity_health_score,
    reset_integrity_health_score,
)

# Local Manager
from selfhealing.audit.integrity.local_manager import (
    HashChainManager,
)

# Models and core functions
from selfhealing.audit.integrity.models import (
    IntegrityInfo,
    compute_hash,
)

# Protocol / Interface
from selfhealing.audit.integrity.protocol import (
    HashChainManagerProtocol,
)

# Reconciler
from selfhealing.audit.integrity.reconciler import (
    HashChainReconciler,
)

# Redis Manager
from selfhealing.audit.integrity.redis_manager import (
    RedisHashChainManager,
)

# Pending Sequence Manager
from selfhealing.audit.integrity.sequence import (
    PendingSequenceManager,
)

# Startup Sync
from selfhealing.audit.integrity.sync import (
    StartupHashChainSync,
)

# Verifier
from selfhealing.audit.integrity.verifier import (
    HashChainVerifier,
    verify_audit_log_integrity,
)

__all__ = [
    # Models
    "IntegrityInfo",
    "compute_hash",
    # Protocol
    "HashChainManagerProtocol",
    # Verifier
    "HashChainVerifier",
    "verify_audit_log_integrity",
    # Managers
    "HashChainManager",
    "RedisHashChainManager",
    # Factory
    "create_hash_chain_manager",
    # Pending Sequence
    "PendingSequenceManager",
    # Anchor
    "DailyHashAnchor",
    # Sync
    "StartupHashChainSync",
    # Reconciler
    "HashChainReconciler",
    # Cold Storage
    "AnchorColdStorage",
    "LocalFileColdStorage",
    "ArchiveResult",
    # Health Score
    "IntegrityHealthScore",
    "IntegrityHealthMetrics",
    "IntegrityRecoveryEvent",
    "get_integrity_health_score",
    "reset_integrity_health_score",
]

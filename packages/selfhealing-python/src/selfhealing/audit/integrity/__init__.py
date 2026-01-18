"""
Integrity Package.

This package contains modular hash chain integrity implementations.
All classes are re-exported here for backward compatibility.
"""

from __future__ import annotations

# Models and core functions
from selfhealing.audit.integrity.models import (
    IntegrityInfo,
    compute_hash,
)

# Protocol / Interface
from selfhealing.audit.integrity.protocol import (
    HashChainManagerProtocol,
)

# Verifier
from selfhealing.audit.integrity.verifier import (
    HashChainVerifier,
    verify_audit_log_integrity,
)

# Local Manager
from selfhealing.audit.integrity.local_manager import (
    HashChainManager,
)

# Redis Manager
from selfhealing.audit.integrity.redis_manager import (
    RedisHashChainManager,
)

# Factory
from selfhealing.audit.integrity.factory import (
    create_hash_chain_manager,
)

# Pending Sequence Manager
from selfhealing.audit.integrity.sequence import (
    PendingSequenceManager,
)

# Daily Hash Anchor
from selfhealing.audit.integrity.anchor import (
    DailyHashAnchor,
)

# Startup Sync
from selfhealing.audit.integrity.sync import (
    StartupHashChainSync,
)

# Reconciler
from selfhealing.audit.integrity.reconciler import (
    HashChainReconciler,
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
]

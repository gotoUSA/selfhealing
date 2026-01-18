"""
Hash Chain Integrity Verification.

FACADE MODULE: This file now re-exports from the integrity package.
All implementations have been moved to selfhealing/audit/integrity/

Implements tamper-evident logging using cryptographic hash chains.
Each log entry includes the hash of the previous entry, making it
impossible to delete or modify entries without breaking the chain.

This is similar to blockchain technology but optimized for audit logs.

For new development, import directly from integrity package:
    from selfhealing.audit.integrity import (
        HashChainManager,
        HashChainVerifier,
        create_hash_chain_manager,
    )
"""

from __future__ import annotations

# Re-export everything from integrity package for backward compatibility
from selfhealing.audit.integrity import (
    # Models
    IntegrityInfo,
    compute_hash,
    # Protocol
    HashChainManagerProtocol,
    # Verifier
    HashChainVerifier,
    verify_audit_log_integrity,
    # Managers
    HashChainManager,
    RedisHashChainManager,
    # Factory
    create_hash_chain_manager,
    # Pending Sequence
    PendingSequenceManager,
    # Anchor
    DailyHashAnchor,
    # Sync
    StartupHashChainSync,
    # Reconciler
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

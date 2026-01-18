"""
Hash Chain Manager Factory.

Contains:
- create_hash_chain_manager: Factory function to create appropriate manager
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

from selfhealing.audit.integrity.protocol import HashChainManagerProtocol
from selfhealing.audit.integrity.local_manager import HashChainManager
from selfhealing.audit.integrity.redis_manager import RedisHashChainManager


logger = logging.getLogger(__name__)


def create_hash_chain_manager(
    distributed: bool = False,
    redis_client: Optional[Any] = None,
    key_prefix: str = "selfhealing:",
    state_file: Optional[Path] = None,
) -> HashChainManagerProtocol:
    """
    Factory function to create appropriate hash chain manager.
    
    Args:
        distributed: If True, create RedisHashChainManager
        redis_client: Redis client (required if distributed=True)
        key_prefix: Redis key prefix
        state_file: Local state file path (for fallback or standalone)
        
    Returns:
        HashChainManager or RedisHashChainManager instance
    """
    local_manager = HashChainManager(state_file=state_file)
    
    if distributed:
        if redis_client is None:
            logger.warning(
                "[HashChain] Distributed mode requested but no Redis client. "
                "Falling back to local mode."
            )
            return local_manager
        
        return RedisHashChainManager(
            redis_client=redis_client,
            key_prefix=key_prefix,
            fallback_manager=local_manager,
        )
    
    return local_manager


__all__ = ["create_hash_chain_manager"]

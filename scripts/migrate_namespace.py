#!/usr/bin/env python
"""
Migrate Redis keys to new namespace.

기존 Redis 데이터를 새 네임스페이스로 마이그레이션.

Usage:
    python scripts/migrate_namespace.py --target-namespace=seoul
    python scripts/migrate_namespace.py --target-namespace=seoul --dry-run
    python scripts/migrate_namespace.py --target-namespace=seoul --source-prefix=selfhealing:

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_redis_client(url: Optional[str] = None):
    """Redis 클라이언트 생성."""
    import redis
    
    redis_url = url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(redis_url, decode_responses=True)


def migrate_keys(
    source_prefix: str,
    target_prefix: str,
    dry_run: bool = True,
    batch_size: int = 100,
    redis_url: Optional[str] = None,
) -> dict:
    """
    Redis 키를 새 프리픽스로 마이그레이션.
    
    Args:
        source_prefix: 원본 키 프리픽스 (예: "selfhealing:")
        target_prefix: 대상 키 프리픽스 (예: "selfhealing:seoul:")
        dry_run: True면 실제 변경 없이 시뮬레이션만
        batch_size: 한 번에 처리할 키 수
        redis_url: Redis URL (없으면 환경변수 사용)
        
    Returns:
        마이그레이션 결과 통계
    """
    r = get_redis_client(redis_url)
    
    stats = {
        "scanned": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": 0,
        "keys": [],
    }
    
    # 이미 target_prefix로 시작하는 키는 제외
    if target_prefix.startswith(source_prefix):
        # 예: source=selfhealing:, target=selfhealing:seoul:
        # → selfhealing:seoul:로 시작하는 키는 제외해야 함
        exclude_prefix = target_prefix
    else:
        exclude_prefix = None
    
    logger.info(f"Scanning keys with prefix: {source_prefix}*")
    if exclude_prefix:
        logger.info(f"Excluding keys with prefix: {exclude_prefix}*")
    
    cursor = 0
    while True:
        cursor, keys = r.scan(
            cursor=cursor,
            match=f"{source_prefix}*",
            count=batch_size
        )
        
        for key in keys:
            stats["scanned"] += 1
            
            # 이미 마이그레이션된 키 건너뛰기
            if exclude_prefix and key.startswith(exclude_prefix):
                stats["skipped"] += 1
                continue
            
            # 새 키 이름 생성
            # selfhealing:cb:service -> selfhealing:seoul:cb:service
            suffix = key[len(source_prefix):]
            new_key = f"{target_prefix}{suffix}"
            
            if dry_run:
                logger.info(f"[DRY-RUN] Would migrate: {key} → {new_key}")
                stats["keys"].append({"old": key, "new": new_key, "status": "would_migrate"})
            else:
                try:
                    # 키 타입에 따라 다르게 처리
                    key_type = r.type(key)
                    
                    if key_type == "string":
                        value = r.get(key)
                        ttl = r.ttl(key)
                        r.set(new_key, value)
                        if ttl > 0:
                            r.expire(new_key, ttl)
                    elif key_type == "hash":
                        data = r.hgetall(key)
                        ttl = r.ttl(key)
                        r.hset(new_key, mapping=data)
                        if ttl > 0:
                            r.expire(new_key, ttl)
                    elif key_type == "list":
                        data = r.lrange(key, 0, -1)
                        ttl = r.ttl(key)
                        if data:
                            r.rpush(new_key, *data)
                        if ttl > 0:
                            r.expire(new_key, ttl)
                    elif key_type == "set":
                        data = r.smembers(key)
                        ttl = r.ttl(key)
                        if data:
                            r.sadd(new_key, *data)
                        if ttl > 0:
                            r.expire(new_key, ttl)
                    elif key_type == "zset":
                        data = r.zrange(key, 0, -1, withscores=True)
                        ttl = r.ttl(key)
                        if data:
                            r.zadd(new_key, dict(data))
                        if ttl > 0:
                            r.expire(new_key, ttl)
                    else:
                        logger.warning(f"Unknown key type: {key_type} for {key}")
                        stats["errors"] += 1
                        continue
                    
                    # 원본 삭제 (선택적)
                    # r.delete(key)
                    
                    logger.info(f"Migrated: {key} → {new_key} (type={key_type})")
                    stats["migrated"] += 1
                    stats["keys"].append({"old": key, "new": new_key, "status": "migrated"})
                    
                except Exception as e:
                    logger.error(f"Failed to migrate {key}: {e}")
                    stats["errors"] += 1
                    stats["keys"].append({"old": key, "new": new_key, "status": "error", "error": str(e)})
        
        if cursor == 0:
            break
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Migrate Redis keys to new namespace"
    )
    parser.add_argument(
        "--target-namespace",
        required=True,
        help="Target namespace (e.g., 'seoul', 'tokyo')"
    )
    parser.add_argument(
        "--source-prefix",
        default="selfhealing:",
        help="Source key prefix (default: 'selfhealing:')"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate migration without actual changes"
    )
    parser.add_argument(
        "--redis-url",
        help="Redis URL (default: REDIS_URL env var or redis://localhost:6379/0)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="Number of keys to process per batch (default: 100)"
    )
    
    args = parser.parse_args()
    
    # target_prefix 생성
    # selfhealing: -> selfhealing:seoul:
    if args.source_prefix.endswith(":"):
        base = args.source_prefix[:-1]  # selfhealing
    else:
        base = args.source_prefix
    
    target_prefix = f"{base}:{args.target_namespace}:"
    
    logger.info("=" * 60)
    logger.info("Namespace Migration Tool")
    logger.info("=" * 60)
    logger.info(f"Source prefix: {args.source_prefix}")
    logger.info(f"Target prefix: {target_prefix}")
    logger.info(f"Dry run: {args.dry_run}")
    logger.info("=" * 60)
    
    if not args.dry_run:
        confirm = input("This will modify Redis data. Continue? [y/N] ")
        if confirm.lower() != "y":
            logger.info("Migration cancelled.")
            sys.exit(0)
    
    stats = migrate_keys(
        source_prefix=args.source_prefix,
        target_prefix=target_prefix,
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        redis_url=args.redis_url,
    )
    
    logger.info("=" * 60)
    logger.info("Migration Summary")
    logger.info("=" * 60)
    logger.info(f"Keys scanned: {stats['scanned']}")
    logger.info(f"Keys migrated: {stats['migrated']}")
    logger.info(f"Keys skipped: {stats['skipped']}")
    logger.info(f"Errors: {stats['errors']}")
    
    if args.dry_run:
        logger.info("")
        logger.info("This was a dry run. No changes were made.")
        logger.info("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()

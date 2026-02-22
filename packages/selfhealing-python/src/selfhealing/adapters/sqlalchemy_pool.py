"""
SQLAlchemy Connection Pool Adapter.

SQLAlchemy 기반 커넥션 풀 정보를 조회하는 어댑터.
PrecomputedCache의 compute_pool_status에서 사용됩니다.

Usage:
    from selfhealing.adapters.sqlalchemy_pool import get_pool_info

    pool_info = get_pool_info()
    # {'pool_type': 'QueuePool', 'pool_size': 10, ...}
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


def get_pool_info() -> dict[str, Any]:
    """
    SQLAlchemy Pool 정보를 조회합니다.

    dj-db-conn-pool / SQLAlchemy 커넥션 풀의 상태 정보를 반환합니다.
    Django DB 연결에서 풀 객체를 탐색하여 정보를 수집합니다.

    Returns:
        풀 상태 정보 딕셔너리. 풀을 찾지 못하면 빈 dict.
    """
    try:
        from django.db import connections

        conn = connections["default"]
        conn.ensure_connection()

        # 방법 1: conn.connection._pool (dj_db_conn_pool 1.2.x)
        if hasattr(conn, "connection") and conn.connection is not None:
            raw_conn = conn.connection
            if hasattr(raw_conn, "_pool"):
                pool = raw_conn._pool
                return _extract_pool_info(pool)

        # 방법 2: pool_container 사용 (일부 버전)
        try:
            from dj_db_conn_pool.core.mixins.core import pool_container

            if pool_container.has("default"):
                pool = pool_container.get("default")
                return _extract_pool_info(pool)
        except ImportError:
            pass

        # 방법 3: conn.pool.pool (구버전)
        if hasattr(conn, "pool") and conn.pool is not None and hasattr(conn.pool, "pool"):
            pool = conn.pool.pool
            return _extract_pool_info(pool)

        return {}

    except Exception as e:
        logger.debug(
            "sqlalchemy_pool.retrieve_pool_info",
            error=e,
        )
        return {}


def _extract_pool_info(pool: Any) -> dict[str, Any]:
    """SQLAlchemy Pool 객체에서 정보를 추출합니다."""
    try:
        pool_size = pool.size()
        checkedout = pool.checkedout()
        checkedin = pool.checkedin()
        overflow = pool.overflow()
        max_overflow = getattr(pool, "_max_overflow", 0)

        return {
            "pool_type": type(pool).__name__,
            "pool_size": pool_size,
            "max_overflow": max_overflow,
            "checkedin": checkedin,
            "checkedout": checkedout,
            "overflow": overflow,
            "total_capacity": pool_size + max_overflow,
            "available": checkedin,
            "pool_exhausted": checkedin == 0 and checkedout >= pool_size,
        }
    except Exception as e:
        return {"pool_type": type(pool).__name__, "error": str(e)}


__all__ = [
    "get_pool_info",
]

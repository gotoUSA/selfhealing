"""
Self-Healing Pool Stress Test Endpoints.

이 엔드포인트들은 의도적으로 DB Connection Pool을 고갈시킵니다.
테스트 전용이며, 프로덕션에서는 절대 사용하지 마세요!
"""

import os
import time
import logging
from django.conf import settings
from django.http import JsonResponse
from django.db import connection, connections
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt

# SQLAlchemy Pool 상태 조회를 위한 import
try:
    from sqlalchemy.pool import QueuePool
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False
    SATimeoutError = Exception

logger = logging.getLogger(__name__)


def get_pool_info():
    """SQLAlchemy Pool 정보 조회"""
    try:
        conn = connections["default"]

        # 연결이 없으면 생성
        conn.ensure_connection()

        # 방법 1: conn.connection._pool (dj_db_conn_pool 1.2.x)
        if hasattr(conn, "connection") and conn.connection is not None:
            raw_conn = conn.connection
            if hasattr(raw_conn, "_pool"):
                pool = raw_conn._pool
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

        # 방법 2: pool_container 사용 (일부 버전)
        try:
            from dj_db_conn_pool.core.mixins.core import pool_container

            if pool_container.has("default"):
                pool = pool_container.get("default")
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
        except ImportError:
            pass

        # 방법 3: conn.pool.pool (구버전)
        if hasattr(conn, "pool") and conn.pool is not None and hasattr(conn.pool, "pool"):
            pool = conn.pool.pool
            return {
                "pool_type": type(pool).__name__,
                "pool_size": pool.size(),
                "checkedin": pool.checkedin(),
                "checkedout": pool.checkedout(),
                "overflow": pool.overflow(),
                "pool_exhausted": pool.checkedout() >= pool.size() + pool._max_overflow,
            }

        return {"pool_type": "django_default", "note": "No SQLAlchemy pool detected"}
    except Exception as e:
        return {"pool_type": "unknown", "error": str(e)}


@require_GET
def slow_query_5s(request):
    """
    5초 동안 DB 연결을 점유하는 느린 쿼리.

    GET /api/self-healing/stress/slow-5s/
    """
    start = time.time()
    try:
        with connection.cursor() as cursor:
            # PostgreSQL에서 5초 대기
            cursor.execute("SELECT pg_sleep(5)")
            cursor.fetchone()

        elapsed = time.time() - start
        return JsonResponse(
            {"status": "success", "elapsed_seconds": round(elapsed, 2), "message": "Connection held for 5 seconds"}
        )
    except SATimeoutError as e:
        # Pool 고갈!
        elapsed = time.time() - start
        logger.error(f"[PoolStress] POOL EXHAUSTED! slow_query_5s timeout after {elapsed:.2f}s: {e}")
        return JsonResponse(
            {
                "status": "pool_exhausted",
                "elapsed_seconds": round(elapsed, 2),
                "error": "Connection pool exhausted - no available connections",
                "error_type": "SQLAlchemy TimeoutError",
            },
            status=503,
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[PoolStress] slow_query_5s failed after {elapsed:.2f}s: {e}")
        return JsonResponse(
            {"status": "error", "elapsed_seconds": round(elapsed, 2), "error": str(e), "error_type": type(e).__name__},
            status=503,
        )


@require_GET
def slow_query_10s(request):
    """
    10초 동안 DB 연결을 점유하는 매우 느린 쿼리.

    GET /api/self-healing/stress/slow-10s/
    """
    start = time.time()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_sleep(10)")
            cursor.fetchone()

        elapsed = time.time() - start
        return JsonResponse(
            {"status": "success", "elapsed_seconds": round(elapsed, 2), "message": "Connection held for 10 seconds"}
        )
    except SATimeoutError as e:
        # Pool 고갈!
        elapsed = time.time() - start
        logger.error(f"[PoolStress] POOL EXHAUSTED! slow_query_10s timeout after {elapsed:.2f}s: {e}")
        return JsonResponse(
            {
                "status": "pool_exhausted",
                "elapsed_seconds": round(elapsed, 2),
                "error": "Connection pool exhausted - no available connections",
                "error_type": "SQLAlchemy TimeoutError",
            },
            status=503,
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[PoolStress] slow_query_10s failed after {elapsed:.2f}s: {e}")
        return JsonResponse(
            {"status": "error", "elapsed_seconds": round(elapsed, 2), "error": str(e), "error_type": type(e).__name__},
            status=503,
        )


@require_GET
def connection_leak_simulation(request):
    """
    의도적으로 연결을 '누수'시키는 시뮬레이션.
    연결을 열고 닫지 않은 채로 유지합니다.

    GET /api/self-healing/stress/leak/

    ⚠️ 테스트 전용! 프로덕션에서 절대 사용 금지!
    """
    hold_seconds = int(request.GET.get("seconds", 30))
    hold_seconds = min(hold_seconds, 60)  # 최대 60초

    start = time.time()
    try:
        # 연결을 열고 오래 유지
        conn = connections["default"]
        cursor = conn.cursor()

        # 연결 점유 (닫지 않음)
        cursor.execute("SELECT 1")

        # 의도적 지연
        time.sleep(hold_seconds)

        # 명시적으로 닫지 않음 (누수 시뮬레이션)
        # cursor.close()  # 의도적으로 주석 처리

        elapsed = time.time() - start
        return JsonResponse(
            {
                "status": "leak_simulated",
                "held_seconds": hold_seconds,
                "elapsed_seconds": round(elapsed, 2),
                "warning": "Connection intentionally not closed",
            }
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[PoolStress] leak simulation failed after {elapsed:.2f}s: {e}")
        return JsonResponse({"status": "error", "elapsed_seconds": round(elapsed, 2), "error": str(e)}, status=503)


@require_GET
def pool_status(request):
    """
    현재 Connection Pool 상태 조회.
    SQLAlchemy Pool 사용 시 실제 Pool 상태를, 아니면 PostgreSQL 통계를 반환.

    GET /api/self-healing/stress/pool-status/
    """
    try:
        # SQLAlchemy Pool 정보 먼저 시도
        pool_info = get_pool_info()

        conn = connections["default"]

        # PostgreSQL 연결 통계 조회
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    count(*) as total_connections,
                    count(*) FILTER (WHERE state = 'active') as active,
                    count(*) FILTER (WHERE state = 'idle') as idle,
                    count(*) FILTER (WHERE state = 'idle in transaction') as idle_in_tx
                FROM pg_stat_activity
                WHERE datname = current_database()
            """
            )
            row = cursor.fetchone()

        is_exhausted = pool_info.get("pool_exhausted", False)

        response_data = {
            "status": "exhausted" if is_exhausted else "healthy",
            "sqlalchemy_pool": pool_info,
            "pg_stats": {
                "total_connections": row[0],
                "active": row[1],
                "idle": row[2],
                "idle_in_transaction": row[3],
            },
            "connection_usable": conn.is_usable(),
            "use_connection_pool": os.getenv("USE_CONNECTION_POOL", "FALSE") == "TRUE",
        }

        # Pool 고갈 시 503 반환
        if is_exhausted:
            return JsonResponse(response_data, status=503)

        return JsonResponse(response_data)
    except SATimeoutError as e:
        # SQLAlchemy Pool Timeout = Pool 고갈!
        logger.error(f"[PoolStress] Pool exhausted! TimeoutError: {e}")
        return JsonResponse(
            {
                "status": "exhausted",
                "error": "Connection pool exhausted",
                "error_type": "SQLAlchemy TimeoutError",
                "detail": str(e),
            },
            status=503,
        )
    except Exception as e:
        logger.error(f"[PoolStress] pool_status failed: {e}")
        return JsonResponse({"status": "error", "error": str(e)}, status=503)


@require_GET
def heavy_concurrent_query(request):
    """
    여러 테이블을 JOIN하는 무거운 쿼리.

    GET /api/self-healing/stress/heavy-query/
    """
    start = time.time()
    try:
        with connection.cursor() as cursor:
            # ⚠️ STRESS TEST ONLY: This query uses shopping_product table as an example.
            # For standalone deployments without shopping app, replace with any available
            # table or configure via SELFHEALING_STRESS_TEST_TABLE setting.
            # The actual table doesn't matter - this is purely for connection pool testing.
            stress_table = getattr(settings, 'SELFHEALING_STRESS_TEST_TABLE', 'shopping_product')
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_products,
                    AVG(price) as avg_price,
                    MAX(price) as max_price,
                    MIN(price) as min_price
                FROM {stress_table}
                WHERE is_active = true
            """
            )
            row = cursor.fetchone()

            # 추가 지연 (1초)
            cursor.execute("SELECT pg_sleep(1)")
            cursor.fetchone()

        elapsed = time.time() - start
        return JsonResponse(
            {
                "status": "success",
                "elapsed_seconds": round(elapsed, 2),
                "stats": {
                    "total_products": row[0],
                    "avg_price": float(row[1]) if row[1] else 0,
                    "max_price": float(row[2]) if row[2] else 0,
                    "min_price": float(row[3]) if row[3] else 0,
                },
            }
        )
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[PoolStress] heavy_query failed after {elapsed:.2f}s: {e}")
        return JsonResponse({"status": "error", "elapsed_seconds": round(elapsed, 2), "error": str(e)}, status=503)

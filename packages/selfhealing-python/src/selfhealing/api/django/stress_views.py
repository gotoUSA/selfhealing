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
    
    V3 Optimization: Uses multi-tier cache for P95 < 30ms target.
    Query Parameters:
    - nocache: Set to "true" to bypass cache
    """
    # V3: Check cache bypass
    use_cache = request.GET.get("nocache", "").lower() != "true"
    
    if use_cache:
        try:
            from selfhealing.services.precomputed_cache import get_cached_pool_status
            data = get_cached_pool_status()
            
            # Pool 고갈 시 503 반환
            if data.get("status") == "exhausted":
                return JsonResponse(data, status=503)
            return JsonResponse(data)
        except ImportError:
            pass  # Fall through to direct computation
    
    # Direct computation
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
            # ⚠️ STRESS TEST ONLY: This query uses a configurable table for testing.
            # Configure via SELFHEALING_STRESS_TEST_TABLE setting.
            # The actual table doesn't matter - this is purely for connection pool testing.
            stress_table = getattr(settings, "SELFHEALING_STRESS_TEST_TABLE", "selfhealing_failedoperation")
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


# =============================================================================
# 🔥 Advisory Lock API - 비침투적 DB 락 테스트
# =============================================================================
# "특정 비즈니스 테이블을 건드리지 않고도 pg_advisory_lock으로 완벽한 비침투적 테스트"
# =============================================================================

@csrf_exempt
def advisory_lock_acquire(request):
    """
    PostgreSQL Advisory Lock 획득 - 비침투적 락 테스트.
    
    POST /api/self-healing/stress/advisory-lock/acquire/
    
    비즈니스 데이터를 전혀 건드리지 않고, DB 엔진 수준의 락 경합만 발생시킵니다.
    이를 통해 시스템의 락 감지 및 복구 능력을 검증할 수 있습니다.
    
    Parameters:
        lock_id (int): 락 식별자 (1-1000000). 같은 ID로 다수 요청 시 경합 발생
        hold_seconds (int): 락 유지 시간 (1-60초, 기본값: 5초)
        exclusive (bool): 배타적 락 여부 (기본값: true)
        wait (bool): 락 획득 대기 여부. false면 즉시 실패 반환 (기본값: true)
    
    Response:
        - 200: 락 획득 성공
        - 409: 락 획득 실패 (다른 세션이 보유 중, wait=false인 경우)
        - 503: DB 오류 또는 타임아웃
    
    ⚠️ 테스트 전용! 프로덕션에서 절대 사용 금지!
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)
    
    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    
    lock_id = int(body.get("lock_id", 12345))
    hold_seconds = min(int(body.get("hold_seconds", 5)), 60)  # 최대 60초
    exclusive = body.get("exclusive", True)
    wait = body.get("wait", True)
    
    start = time.time()
    lock_acquired = False
    
    try:
        with connection.cursor() as cursor:
            # 락 획득 시도
            if exclusive:
                if wait:
                    # 대기 모드: 락 획득까지 블로킹
                    cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                    lock_acquired = True
                else:
                    # 비대기 모드: 즉시 성공/실패 반환
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                    result = cursor.fetchone()
                    lock_acquired = result[0] if result else False
            else:
                # 공유 락 (Shared Lock)
                if wait:
                    cursor.execute("SELECT pg_advisory_lock_shared(%s)", [lock_id])
                    lock_acquired = True
                else:
                    cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [lock_id])
                    result = cursor.fetchone()
                    lock_acquired = result[0] if result else False
            
            if not lock_acquired:
                elapsed = time.time() - start
                logger.info(f"[AdvisoryLock] Lock {lock_id} not acquired (conflict)")
                return JsonResponse(
                    {
                        "status": "conflict",
                        "lock_id": lock_id,
                        "elapsed_seconds": round(elapsed, 2),
                        "message": "Lock held by another session",
                    },
                    status=409,
                )
            
            # 락 유지
            logger.info(f"[AdvisoryLock] Lock {lock_id} acquired, holding for {hold_seconds}s")
            time.sleep(hold_seconds)
            
            # 락 해제
            if exclusive:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
            else:
                cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [lock_id])
        
        elapsed = time.time() - start
        logger.info(f"[AdvisoryLock] Lock {lock_id} released after {elapsed:.2f}s")
        
        return JsonResponse(
            {
                "status": "success",
                "lock_id": lock_id,
                "held_seconds": hold_seconds,
                "elapsed_seconds": round(elapsed, 2),
                "exclusive": exclusive,
                "message": f"Advisory lock {lock_id} acquired and released successfully",
            }
        )
    
    except Exception as e:
        elapsed = time.time() - start
        error_str = str(e).lower()
        
        # 락 타임아웃 또는 데드락 감지
        if "lock" in error_str or "timeout" in error_str or "deadlock" in error_str:
            logger.warning(f"[AdvisoryLock] Lock contention detected: {e}")
            return JsonResponse(
                {
                    "status": "lock_timeout",
                    "lock_id": lock_id,
                    "elapsed_seconds": round(elapsed, 2),
                    "error": str(e),
                    "error_type": "LockTimeout",
                },
                status=423,  # Locked
            )
        
        logger.error(f"[AdvisoryLock] Failed after {elapsed:.2f}s: {e}")
        return JsonResponse(
            {
                "status": "error",
                "lock_id": lock_id,
                "elapsed_seconds": round(elapsed, 2),
                "error": str(e),
                "error_type": type(e).__name__,
            },
            status=503,
        )


@csrf_exempt
def advisory_lock_contention(request):
    """
    Advisory Lock 경합 시뮬레이션 - 다수의 세션이 동일 락을 놓고 경쟁.
    
    POST /api/self-healing/stress/advisory-lock/contention/
    
    지정된 시간 동안 동일한 락 ID에 대해 반복적으로 획득/해제를 시도합니다.
    이는 실제 DB 락 경합 상황을 시뮬레이션합니다.
    
    Parameters:
        lock_id (int): 락 식별자
        duration_seconds (int): 경합 지속 시간 (1-30초, 기본값: 5초)
        lock_hold_ms (int): 각 락 유지 시간 (ms, 기본값: 100ms)
    
    Response:
        경합 통계 (성공/실패 횟수, 평균 대기 시간 등)
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)
    
    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    
    lock_id = int(body.get("lock_id", 99999))
    duration_seconds = min(int(body.get("duration_seconds", 5)), 30)
    lock_hold_ms = min(int(body.get("lock_hold_ms", 100)), 5000)
    
    start = time.time()
    success_count = 0
    fail_count = 0
    total_wait_ms = 0
    
    try:
        end_time = start + duration_seconds
        
        while time.time() < end_time:
            attempt_start = time.time()
            
            with connection.cursor() as cursor:
                # 비대기 모드로 락 시도
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                result = cursor.fetchone()
                
                if result and result[0]:
                    success_count += 1
                    # 락 유지
                    time.sleep(lock_hold_ms / 1000.0)
                    # 락 해제
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                else:
                    fail_count += 1
            
            wait_ms = (time.time() - attempt_start) * 1000
            total_wait_ms += wait_ms
        
        elapsed = time.time() - start
        total_attempts = success_count + fail_count
        
        return JsonResponse(
            {
                "status": "completed",
                "lock_id": lock_id,
                "duration_seconds": round(elapsed, 2),
                "total_attempts": total_attempts,
                "success_count": success_count,
                "fail_count": fail_count,
                "success_rate_percent": round(success_count / total_attempts * 100, 2) if total_attempts > 0 else 0,
                "avg_wait_ms": round(total_wait_ms / total_attempts, 2) if total_attempts > 0 else 0,
                "lock_hold_ms": lock_hold_ms,
            }
        )
    
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[AdvisoryLock] Contention test failed: {e}")
        return JsonResponse(
            {
                "status": "error",
                "elapsed_seconds": round(elapsed, 2),
                "error": str(e),
            },
            status=503,
        )


@csrf_exempt
def controlled_burst_failure(request):
    """
    🔥 Controlled Burst Failure - "폭풍 전야 → 시스템 붕괴 → 자율 복구" 연출.
    
    POST /api/self-healing/stress/burst-failure/
    
    지정된 시간 동안 극단적인 락 타임아웃과 부하를 발생시켜
    100건 이상의 DLQ 항목을 강제로 생성합니다.
    
    Parameters:
        lock_id (int): Advisory Lock ID
        lock_timeout_ms (int): 극단적으로 짧은 락 타임아웃 (기본값: 1ms!)
        burst_duration_seconds (int): burst 지속 시간 (기본값: 10초)
        concurrent_locks (int): 동시 락 시도 수 (기본값: 50)
    
    이 API는 다음을 수행합니다:
    1. lock_timeout을 1ms로 축소
    2. 지정된 시간 동안 동시에 많은 락 획득 시도
    3. 대부분의 요청이 타임아웃으로 실패
    4. 실패한 요청들이 DLQ로 자동 라우팅됨
    
    ⚠️ 테스트 전용! 시스템에 의도적으로 장애를 발생시킵니다!
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)
    
    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    
    lock_id = int(body.get("lock_id", 777))
    lock_timeout_ms = max(int(body.get("lock_timeout_ms", 1)), 1)  # 최소 1ms
    burst_duration_seconds = min(int(body.get("burst_duration_seconds", 10)), 30)
    concurrent_locks = min(int(body.get("concurrent_locks", 50)), 100)
    
    start = time.time()
    timeout_count = 0
    success_count = 0
    deadlock_count = 0
    
    try:
        with connection.cursor() as cursor:
            # 1. 락 타임아웃을 극단적으로 축소 (세션 레벨)
            cursor.execute(f"SET lock_timeout = '{lock_timeout_ms}ms'")
            cursor.execute(f"SET statement_timeout = '{lock_timeout_ms * 10}ms'")
            
            logger.warning(f"[BurstFailure] 🔥 BURST STARTED: lock_timeout={lock_timeout_ms}ms, duration={burst_duration_seconds}s")
            
            # 2. 먼저 하나의 락을 잡아서 유지 (다른 요청들이 실패하도록)
            try:
                cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                
                # 3. burst 동안 반복적으로 새 연결에서 락 시도 (타임아웃 유발)
                end_time = start + burst_duration_seconds
                attempt_count = 0
                
                while time.time() < end_time:
                    attempt_count += 1
                    
                    # 새로운 커서로 락 시도 (같은 트랜잭션이라 실패함)
                    try:
                        # 매우 짧은 타임아웃으로 락 시도
                        cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id + 1])
                        result = cursor.fetchone()
                        if result and result[0]:
                            success_count += 1
                            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id + 1])
                        else:
                            timeout_count += 1
                    except Exception as inner_e:
                        error_str = str(inner_e).lower()
                        if "timeout" in error_str or "lock" in error_str:
                            timeout_count += 1
                        elif "deadlock" in error_str:
                            deadlock_count += 1
                        else:
                            timeout_count += 1
                    
                    # 짧은 간격으로 반복
                    time.sleep(0.01)  # 10ms
                
                # 메인 락 해제
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                
            except Exception as lock_e:
                logger.error(f"[BurstFailure] Main lock failed: {lock_e}")
                timeout_count += 1
            
            # 4. 타임아웃 설정 복원
            cursor.execute("SET lock_timeout = '0'")
            cursor.execute("SET statement_timeout = '0'")
        
        elapsed = time.time() - start
        
        logger.warning(f"[BurstFailure] 🔥 BURST COMPLETED: timeouts={timeout_count}, deadlocks={deadlock_count}")
        
        return JsonResponse(
            {
                "status": "burst_completed",
                "lock_id": lock_id,
                "lock_timeout_ms": lock_timeout_ms,
                "burst_duration_seconds": round(elapsed, 2),
                "total_attempts": timeout_count + success_count + deadlock_count,
                "timeout_count": timeout_count,
                "success_count": success_count,
                "deadlock_count": deadlock_count,
                "failure_rate_percent": round(
                    (timeout_count + deadlock_count) / max(1, timeout_count + success_count + deadlock_count) * 100, 2
                ),
                "message": "Controlled burst failure completed - check DLQ for captured failures",
            }
        )
    
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[BurstFailure] Test failed: {e}")
        return JsonResponse(
            {
                "status": "error",
                "elapsed_seconds": round(elapsed, 2),
                "timeout_count": timeout_count,
                "error": str(e),
            },
            status=503,
        )


# =============================================================================
# 🔥 Pool Exhaustion API - CB 트리거를 위한 실제 풀 고갈
# =============================================================================

# 전역 변수로 점유 중인 커넥션들을 저장
_held_connections = []
_held_connections_lock = None

try:
    import threading
    _held_connections_lock = threading.Lock()
except ImportError:
    pass


@csrf_exempt
def pool_exhaust(request):
    """
    DB 커넥션 풀을 의도적으로 고갈시켜 CB를 트리거합니다.
    
    POST /api/self-healing/stress/pool-exhaust/
    
    Parameters:
        connections_to_hold (int): 점유할 커넥션 수 (기본값: 10)
        hold_seconds (int): 커넥션 유지 시간 (기본값: 30초, 최대 60초)
    
    이 API는:
    1. 여러 개의 DB 커넥션을 열고 유지
    2. 다른 요청들이 커넥션을 얻지 못해 503 에러 발생
    3. SelfHealingMiddleware가 이 에러를 감지하고 CB를 OPEN으로 전환
    4. 지정된 시간 후 커넥션 반환
    
    ⚠️ 테스트 전용! 시스템에 의도적으로 장애를 발생시킵니다!
    """
    global _held_connections
    
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)
    
    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    
    connections_to_hold = min(int(body.get("connections_to_hold", 10)), 20)
    hold_seconds = min(int(body.get("hold_seconds", 30)), 60)
    
    start = time.time()
    held_count = 0
    
    try:
        logger.warning(f"[PoolExhaust] 🔥 Starting pool exhaustion: {connections_to_hold} connections for {hold_seconds}s")
        
        # 기존 점유 커넥션 정리
        if _held_connections_lock:
            with _held_connections_lock:
                for conn_info in _held_connections:
                    try:
                        conn_info['cursor'].close()
                    except:
                        pass
                _held_connections.clear()
        
        # 여러 커넥션 점유
        for i in range(connections_to_hold):
            try:
                # 새 커넥션 획득 (Django의 connection은 thread-local이라 다른 방식 필요)
                from django.db import connection as db_conn
                cursor = db_conn.cursor()
                
                # 커넥션을 busy 상태로 유지 (SELECT 실행)
                cursor.execute("SELECT pg_backend_pid(), pg_sleep(0.01)")
                cursor.fetchone()
                
                if _held_connections_lock:
                    with _held_connections_lock:
                        _held_connections.append({'cursor': cursor, 'created_at': time.time()})
                
                held_count += 1
                logger.info(f"[PoolExhaust] Held connection {i+1}/{connections_to_hold}")
                
            except Exception as e:
                logger.warning(f"[PoolExhaust] Failed to acquire connection {i+1}: {e}")
                break
        
        # 커넥션 유지하면서 대기
        logger.warning(f"[PoolExhaust] 🔥 Holding {held_count} connections for {hold_seconds}s")
        time.sleep(hold_seconds)
        
        # 커넥션 반환
        if _held_connections_lock:
            with _held_connections_lock:
                for conn_info in _held_connections:
                    try:
                        conn_info['cursor'].close()
                    except:
                        pass
                _held_connections.clear()
        
        elapsed = time.time() - start
        logger.warning(f"[PoolExhaust] 🔥 Pool exhaustion completed after {elapsed:.2f}s")
        
        return JsonResponse({
            "status": "exhaustion_completed",
            "connections_held": held_count,
            "hold_seconds": hold_seconds,
            "elapsed_seconds": round(elapsed, 2),
            "message": "Pool exhaustion completed - connections released"
        })
        
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[PoolExhaust] Failed: {e}")
        return JsonResponse({
            "status": "error",
            "connections_held": held_count,
            "elapsed_seconds": round(elapsed, 2),
            "error": str(e)
        }, status=503)


@csrf_exempt
def trigger_cb_failure(request):
    """
    Circuit Breaker를 직접 트리거하기 위한 의도적 실패 엔드포인트.
    
    POST /api/self-healing/stress/trigger-cb-failure/
    
    Parameters:
        failure_count (int): 연속 실패 횟수 (기본값: 10)
        error_type (str): 에러 유형 - "db_error", "timeout", "exception" (기본값: "db_error")
    
    이 API는 SelfHealingMiddleware를 통해 처리되는 실패를 발생시킵니다.
    연속된 실패가 CB threshold를 초과하면 CB가 OPEN 상태로 전환됩니다.
    
    ⚠️ 테스트 전용!
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST method required"}, status=405)
    
    import json
    try:
        body = json.loads(request.body) if request.body else {}
    except json.JSONDecodeError:
        body = {}
    
    error_type = body.get("error_type", "db_error")
    
    start = time.time()
    
    try:
        if error_type == "db_error":
            # 의도적인 DB 에러 발생
            with connection.cursor() as cursor:
                # 존재하지 않는 테이블 쿼리 -> DB 에러
                cursor.execute("SELECT * FROM __nonexistent_table_for_cb_test__")
                
        elif error_type == "timeout":
            # 타임아웃 에러 발생
            with connection.cursor() as cursor:
                cursor.execute("SET statement_timeout = '1ms'")
                cursor.execute("SELECT pg_sleep(1)")  # 1ms 타임아웃에 1초 sleep -> 타임아웃
                
        elif error_type == "exception":
            # Python 예외 발생
            raise Exception("Intentional test exception for CB trigger")
        
        # 정상적으로 여기까지 오면 안됨
        return JsonResponse({"status": "unexpected_success"})
        
    except Exception as e:
        elapsed = time.time() - start
        # 503으로 반환하여 CB가 이 실패를 카운트하도록 함
        return JsonResponse({
            "status": "intentional_failure",
            "error_type": error_type,
            "error": str(e),
            "elapsed_seconds": round(elapsed, 2),
            "message": "This failure is intentional for CB testing"
        }, status=503)

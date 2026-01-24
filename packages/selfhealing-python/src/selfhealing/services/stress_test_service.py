"""
Stress Test Service.

DB Connection Pool 스트레스 테스트를 위한 비즈니스 로직.

이 모듈은 테스트 전용이며, 프로덕션에서는 절대 사용하지 마세요!
비즈니스 로직을 View 레이어에서 분리하여 클린 아키텍처를 유지합니다.

Note:
- Views(stress_views.py)는 Request/Response 처리만 담당
- 실제 DB 연산, 락 테스트, 풀 관리 로직은 이 서비스에서 담당
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from django.conf import settings
from django.db import connection, connections

logger = logging.getLogger(__name__)

# SQLAlchemy Pool 상태 조회를 위한 import
try:
    from sqlalchemy.pool import QueuePool
    from sqlalchemy.exc import TimeoutError as SATimeoutError

    SQLALCHEMY_AVAILABLE = True
except ImportError:
    SQLALCHEMY_AVAILABLE = False
    SATimeoutError = Exception


# =============================================================================
# Data Classes for Stress Test Results
# =============================================================================


@dataclass
class StressTestResult:
    """스트레스 테스트 결과 데이터 클래스."""
    
    status: str
    elapsed_seconds: float = 0.0
    message: str = ""
    error: Optional[str] = None
    error_type: Optional[str] = None
    extra: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
        }
        if self.message:
            result["message"] = self.message
        if self.error:
            result["error"] = self.error
        if self.error_type:
            result["error_type"] = self.error_type
        result.update(self.extra)
        return result


@dataclass
class PoolStatusResult:
    """커넥션 풀 상태 결과."""
    
    status: str
    sqlalchemy_pool: dict = field(default_factory=dict)
    pg_stats: dict = field(default_factory=dict)
    connection_usable: bool = True
    use_connection_pool: bool = False
    error: Optional[str] = None
    error_type: Optional[str] = None
    
    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "sqlalchemy_pool": self.sqlalchemy_pool,
            "pg_stats": self.pg_stats,
            "connection_usable": self.connection_usable,
            "use_connection_pool": self.use_connection_pool,
        }
        if self.error:
            result["error"] = self.error
        if self.error_type:
            result["error_type"] = self.error_type
        return result


@dataclass
class LockContentionResult:
    """락 경합 테스트 결과."""
    
    status: str
    lock_id: int
    duration_seconds: float
    total_attempts: int = 0
    success_count: int = 0
    fail_count: int = 0
    success_rate_percent: float = 0.0
    avg_wait_ms: float = 0.0
    lock_hold_ms: int = 0
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "lock_id": self.lock_id,
            "duration_seconds": round(self.duration_seconds, 2),
        }
        if self.status == "completed":
            result.update({
                "total_attempts": self.total_attempts,
                "success_count": self.success_count,
                "fail_count": self.fail_count,
                "success_rate_percent": self.success_rate_percent,
                "avg_wait_ms": self.avg_wait_ms,
                "lock_hold_ms": self.lock_hold_ms,
            })
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class BurstFailureResult:
    """Burst 장애 테스트 결과."""
    
    status: str
    lock_id: int
    lock_timeout_ms: int
    burst_duration_seconds: float
    total_attempts: int = 0
    timeout_count: int = 0
    success_count: int = 0
    deadlock_count: int = 0
    failure_rate_percent: float = 0.0
    message: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        """결과를 딕셔너리로 변환."""
        result = {
            "status": self.status,
            "lock_id": self.lock_id,
            "lock_timeout_ms": self.lock_timeout_ms,
            "burst_duration_seconds": round(self.burst_duration_seconds, 2),
            "total_attempts": self.total_attempts,
            "timeout_count": self.timeout_count,
            "success_count": self.success_count,
            "deadlock_count": self.deadlock_count,
            "failure_rate_percent": self.failure_rate_percent,
        }
        if self.message:
            result["message"] = self.message
        if self.error:
            result["error"] = self.error
        return result


# =============================================================================
# Stress Test Service
# =============================================================================


class StressTestService:
    """
    스트레스 테스트 서비스.
    
    DB Connection Pool 관련 테스트 로직을 캡슐화합니다.
    """
    
    # 점유 중인 커넥션들을 저장하는 클래스 변수
    _held_connections: list = []
    _held_connections_lock: Optional[threading.Lock] = None
    
    def __init__(self):
        """서비스 초기화."""
        if StressTestService._held_connections_lock is None:
            StressTestService._held_connections_lock = threading.Lock()
    
    # =========================================================================
    # Pool Information
    # =========================================================================
    
    def get_pool_info(self) -> dict:
        """SQLAlchemy Pool 정보 조회."""
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
    
    def get_pool_status(self) -> PoolStatusResult:
        """현재 Connection Pool 상태 조회."""
        try:
            # SQLAlchemy Pool 정보 먼저 시도
            pool_info = self.get_pool_info()
            
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
            
            return PoolStatusResult(
                status="exhausted" if is_exhausted else "healthy",
                sqlalchemy_pool=pool_info,
                pg_stats={
                    "total_connections": row[0],
                    "active": row[1],
                    "idle": row[2],
                    "idle_in_transaction": row[3],
                },
                connection_usable=conn.is_usable(),
                use_connection_pool=os.getenv("USE_CONNECTION_POOL", "FALSE") == "TRUE",
            )
        except SATimeoutError as e:
            logger.error(f"[StressTestService] Pool exhausted! TimeoutError: {e}")
            return PoolStatusResult(
                status="exhausted",
                error="Connection pool exhausted",
                error_type="SQLAlchemy TimeoutError",
            )
        except Exception as e:
            logger.error(f"[StressTestService] pool_status failed: {e}")
            return PoolStatusResult(
                status="error",
                error=str(e),
            )
    
    # =========================================================================
    # Slow Query Tests
    # =========================================================================
    
    def execute_slow_query(self, seconds: int) -> StressTestResult:
        """지정된 시간 동안 DB 연결을 점유하는 느린 쿼리 실행."""
        start = time.time()
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT pg_sleep({seconds})")
                cursor.fetchone()
            
            elapsed = time.time() - start
            return StressTestResult(
                status="success",
                elapsed_seconds=elapsed,
                message=f"Connection held for {seconds} seconds",
            )
        except SATimeoutError as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] POOL EXHAUSTED! slow_query timeout after {elapsed:.2f}s: {e}")
            return StressTestResult(
                status="pool_exhausted",
                elapsed_seconds=elapsed,
                error="Connection pool exhausted - no available connections",
                error_type="SQLAlchemy TimeoutError",
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] slow_query failed after {elapsed:.2f}s: {e}")
            return StressTestResult(
                status="error",
                elapsed_seconds=elapsed,
                error=str(e),
                error_type=type(e).__name__,
            )
    
    def simulate_connection_leak(self, hold_seconds: int) -> StressTestResult:
        """의도적으로 연결을 '누수'시키는 시뮬레이션."""
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
            return StressTestResult(
                status="leak_simulated",
                elapsed_seconds=elapsed,
                extra={
                    "held_seconds": hold_seconds,
                    "warning": "Connection intentionally not closed",
                },
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] leak simulation failed after {elapsed:.2f}s: {e}")
            return StressTestResult(
                status="error",
                elapsed_seconds=elapsed,
                error=str(e),
            )
    
    def execute_heavy_query(self) -> StressTestResult:
        """무거운 쿼리 실행."""
        start = time.time()
        try:
            with connection.cursor() as cursor:
                # STRESS TEST ONLY: This query uses a configurable table for testing.
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
            return StressTestResult(
                status="success",
                elapsed_seconds=elapsed,
                extra={
                    "stats": {
                        "total_products": row[0],
                        "avg_price": float(row[1]) if row[1] else 0,
                        "max_price": float(row[2]) if row[2] else 0,
                        "min_price": float(row[3]) if row[3] else 0,
                    },
                },
            )
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] heavy_query failed after {elapsed:.2f}s: {e}")
            return StressTestResult(
                status="error",
                elapsed_seconds=elapsed,
                error=str(e),
            )
    
    # =========================================================================
    # Advisory Lock Operations
    # =========================================================================
    
    def _try_acquire_lock(self, cursor, lock_id: int, exclusive: bool, wait: bool) -> bool:
        """Try to acquire advisory lock. Returns True if acquired."""
        if exclusive:
            if wait:
                cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                return True
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
        else:
            if wait:
                cursor.execute("SELECT pg_advisory_lock_shared(%s)", [lock_id])
                return True
            cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [lock_id])
        
        result = cursor.fetchone()
        return result[0] if result else False
    
    def _release_lock(self, cursor, lock_id: int, exclusive: bool) -> None:
        """Release advisory lock."""
        if exclusive:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
        else:
            cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [lock_id])
    
    def acquire_advisory_lock(
        self,
        lock_id: int = 12345,
        hold_seconds: int = 5,
        exclusive: bool = True,
        wait: bool = True,
    ) -> StressTestResult:
        """PostgreSQL Advisory Lock 획득."""
        hold_seconds = min(hold_seconds, 60)
        start = time.time()
        
        try:
            with connection.cursor() as cursor:
                lock_acquired = self._try_acquire_lock(cursor, lock_id, exclusive, wait)
                
                if not lock_acquired:
                    elapsed = time.time() - start
                    logger.info(f"[StressTestService] Lock {lock_id} not acquired (conflict)")
                    return StressTestResult(
                        status="conflict",
                        elapsed_seconds=elapsed,
                        message="Lock held by another session",
                        extra={"lock_id": lock_id},
                    )
                
                logger.info(f"[StressTestService] Lock {lock_id} acquired, holding for {hold_seconds}s")
                time.sleep(hold_seconds)
                
                self._release_lock(cursor, lock_id, exclusive)
            
            elapsed = time.time() - start
            logger.info(f"[StressTestService] Lock {lock_id} released after {elapsed:.2f}s")
            
            return StressTestResult(
                status="success",
                elapsed_seconds=elapsed,
                message=f"Advisory lock {lock_id} acquired and released successfully",
                extra={
                    "lock_id": lock_id,
                    "held_seconds": hold_seconds,
                    "exclusive": exclusive,
                },
            )
        
        except Exception as e:
            elapsed = time.time() - start
            error_str = str(e).lower()
            
            # 락 타임아웃 또는 데드락 감지
            if "lock" in error_str or "timeout" in error_str or "deadlock" in error_str:
                logger.warning(f"[StressTestService] Lock contention detected: {e}")
                return StressTestResult(
                    status="lock_timeout",
                    elapsed_seconds=elapsed,
                    error=str(e),
                    error_type="LockTimeout",
                    extra={"lock_id": lock_id},
                )
            
            logger.error(f"[StressTestService] Failed after {elapsed:.2f}s: {e}")
            return StressTestResult(
                status="error",
                elapsed_seconds=elapsed,
                error=str(e),
                error_type=type(e).__name__,
                extra={"lock_id": lock_id},
            )
    
    def run_lock_contention(
        self,
        lock_id: int = 99999,
        duration_seconds: int = 5,
        lock_hold_ms: int = 100,
    ) -> LockContentionResult:
        """Advisory Lock 경합 시뮬레이션."""
        duration_seconds = min(duration_seconds, 30)
        lock_hold_ms = min(lock_hold_ms, 5000)
        
        start = time.time()
        success_count = 0
        fail_count = 0
        total_wait_ms = 0.0
        
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
            
            return LockContentionResult(
                status="completed",
                lock_id=lock_id,
                duration_seconds=elapsed,
                total_attempts=total_attempts,
                success_count=success_count,
                fail_count=fail_count,
                success_rate_percent=round(success_count / total_attempts * 100, 2) if total_attempts > 0 else 0,
                avg_wait_ms=round(total_wait_ms / total_attempts, 2) if total_attempts > 0 else 0,
                lock_hold_ms=lock_hold_ms,
            )
        
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] Contention test failed: {e}")
            return LockContentionResult(
                status="error",
                lock_id=lock_id,
                duration_seconds=elapsed,
                error=str(e),
            )
    
    def run_controlled_burst_failure(
        self,
        lock_id: int = 777,
        lock_timeout_ms: int = 1,
        burst_duration_seconds: int = 10,
        concurrent_locks: int = 50,
    ) -> BurstFailureResult:
        """Controlled Burst Failure - 폭풍 전야 → 시스템 붕괴 → 자율 복구 연출."""
        lock_timeout_ms = max(lock_timeout_ms, 1)  # 최소 1ms
        burst_duration_seconds = min(burst_duration_seconds, 30)
        concurrent_locks = min(concurrent_locks, 100)
        
        start = time.time()
        timeout_count = 0
        success_count = 0
        deadlock_count = 0
        
        try:
            with connection.cursor() as cursor:
                # 1. 락 타임아웃을 극단적으로 축소 (세션 레벨)
                cursor.execute(f"SET lock_timeout = '{lock_timeout_ms}ms'")
                cursor.execute(f"SET statement_timeout = '{lock_timeout_ms * 10}ms'")
                
                logger.warning(
                    f"[StressTestService] 🔥 BURST STARTED: lock_timeout={lock_timeout_ms}ms, "
                    f"duration={burst_duration_seconds}s"
                )
                
                # 2. 먼저 하나의 락을 잡아서 유지 (다른 요청들이 실패하도록)
                try:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                    
                    # 3. burst 동안 반복적으로 새 연결에서 락 시도 (타임아웃 유발)
                    end_time = start + burst_duration_seconds
                    
                    while time.time() < end_time:
                        try:
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
                        
                        time.sleep(0.01)  # 10ms
                    
                    # 메인 락 해제
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                    
                except Exception as lock_e:
                    logger.error(f"[StressTestService] Main lock failed: {lock_e}")
                    timeout_count += 1
                
                # 4. 타임아웃 설정 복원
                cursor.execute("SET lock_timeout = '0'")
                cursor.execute("SET statement_timeout = '0'")
            
            elapsed = time.time() - start
            total_attempts = timeout_count + success_count + deadlock_count
            
            logger.warning(
                f"[StressTestService] 🔥 BURST COMPLETED: timeouts={timeout_count}, "
                f"deadlocks={deadlock_count}"
            )
            
            return BurstFailureResult(
                status="burst_completed",
                lock_id=lock_id,
                lock_timeout_ms=lock_timeout_ms,
                burst_duration_seconds=elapsed,
                total_attempts=total_attempts,
                timeout_count=timeout_count,
                success_count=success_count,
                deadlock_count=deadlock_count,
                failure_rate_percent=round(
                    (timeout_count + deadlock_count) / max(1, total_attempts) * 100, 2
                ),
                message="Controlled burst failure completed - check DLQ for captured failures",
            )
        
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] Test failed: {e}")
            return BurstFailureResult(
                status="error",
                lock_id=lock_id,
                lock_timeout_ms=lock_timeout_ms,
                burst_duration_seconds=elapsed,
                timeout_count=timeout_count,
                error=str(e),
            )
    
    # =========================================================================
    # Pool Exhaustion Operations
    # =========================================================================
    
    def exhaust_pool(
        self,
        connections_to_hold: int = 10,
        hold_seconds: int = 30,
    ) -> StressTestResult:
        """DB 커넥션 풀을 의도적으로 고갈시킴."""
        connections_to_hold = min(connections_to_hold, 20)
        hold_seconds = min(hold_seconds, 60)
        
        start = time.time()
        held_count = 0
        
        try:
            logger.warning(
                f"[StressTestService] 🔥 Starting pool exhaustion: "
                f"{connections_to_hold} connections for {hold_seconds}s"
            )
            
            # 기존 점유 커넥션 정리
            if StressTestService._held_connections_lock:
                with StressTestService._held_connections_lock:
                    for conn_info in StressTestService._held_connections:
                        try:
                            conn_info['cursor'].close()
                        except:
                            pass
                    StressTestService._held_connections.clear()
            
            # 여러 커넥션 점유
            for i in range(connections_to_hold):
                try:
                    cursor = connection.cursor()
                    
                    # 커넥션을 busy 상태로 유지
                    cursor.execute("SELECT pg_backend_pid(), pg_sleep(0.01)")
                    cursor.fetchone()
                    
                    if StressTestService._held_connections_lock:
                        with StressTestService._held_connections_lock:
                            StressTestService._held_connections.append({
                                'cursor': cursor,
                                'created_at': time.time()
                            })
                    
                    held_count += 1
                    logger.info(f"[StressTestService] Held connection {i+1}/{connections_to_hold}")
                    
                except Exception as e:
                    logger.warning(f"[StressTestService] Failed to acquire connection {i+1}: {e}")
                    break
            
            # 커넥션 유지하면서 대기
            logger.warning(f"[StressTestService] 🔥 Holding {held_count} connections for {hold_seconds}s")
            time.sleep(hold_seconds)
            
            # 커넥션 반환
            if StressTestService._held_connections_lock:
                with StressTestService._held_connections_lock:
                    for conn_info in StressTestService._held_connections:
                        try:
                            conn_info['cursor'].close()
                        except:
                            pass
                    StressTestService._held_connections.clear()
            
            elapsed = time.time() - start
            logger.warning(f"[StressTestService] 🔥 Pool exhaustion completed after {elapsed:.2f}s")
            
            return StressTestResult(
                status="exhaustion_completed",
                elapsed_seconds=elapsed,
                message="Pool exhaustion completed - connections released",
                extra={
                    "connections_held": held_count,
                    "hold_seconds": hold_seconds,
                },
            )
            
        except Exception as e:
            elapsed = time.time() - start
            logger.error(f"[StressTestService] Failed: {e}")
            return StressTestResult(
                status="error",
                elapsed_seconds=elapsed,
                error=str(e),
                extra={"connections_held": held_count},
            )
    
    def trigger_cb_failure(self, error_type: str = "db_error") -> StressTestResult:
        """Circuit Breaker를 직접 트리거하기 위한 의도적 실패."""
        start = time.time()
        
        try:
            if error_type == "db_error":
                # 의도적인 DB 에러 발생
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM __nonexistent_table_for_cb_test__")
                    
            elif error_type == "timeout":
                # 타임아웃 에러 발생
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '1ms'")
                    cursor.execute("SELECT pg_sleep(1)")
                    
            elif error_type == "exception":
                # Python 예외 발생
                raise Exception("Intentional test exception for CB trigger")
            
            # 정상적으로 여기까지 오면 안됨
            return StressTestResult(status="unexpected_success")
            
        except Exception as e:
            elapsed = time.time() - start
            return StressTestResult(
                status="intentional_failure",
                elapsed_seconds=elapsed,
                error=str(e),
                message="This failure is intentional for CB testing",
                extra={"error_type": error_type},
            )


# =============================================================================
# Singleton Pattern
# =============================================================================

_stress_test_service: Optional[StressTestService] = None


def get_stress_test_service() -> StressTestService:
    """StressTestService 싱글톤 인스턴스 반환."""
    global _stress_test_service
    if _stress_test_service is None:
        _stress_test_service = StressTestService()
    return _stress_test_service

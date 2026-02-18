"""
PostgreSQL Repository.

PostgreSQL 전용 Raw SQL 쿼리를 캡슐화합니다.
Django ORM으로 대체할 수 없는 PostgreSQL 전용 기능들을 Repository Pattern으로 분리합니다.

Note:
- pg_stat_activity, pg_sleep, pg_advisory_lock 등은 PostgreSQL 전용 기능
- 이 Repository를 통해 Raw SQL을 한 곳에서 관리하고 테스트 가능성을 높입니다.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from django.db import connection, connections

logger = logging.getLogger(__name__)


@dataclass
class ConnectionStats:
    """PostgreSQL 연결 통계."""

    total_connections: int
    active: int
    idle: int
    idle_in_transaction: int


@dataclass
class AdvisoryLockResult:
    """Advisory Lock 작업 결과."""

    acquired: bool
    lock_id: int
    error: str | None = None


class PostgresRepository:
    """
    PostgreSQL 전용 Repository.

    PostgreSQL 전용 기능(pg_sleep, pg_advisory_lock, pg_stat_activity 등)을
    캡슐화하여 서비스 레이어에서 직접 Raw SQL을 사용하지 않도록 합니다.
    """

    def __init__(self, db_alias: str = "default"):
        """
        Repository 초기화.

        Args:
            db_alias: 사용할 DB 별칭 (default, replica 등)
        """
        self._db_alias = db_alias

    def _get_connection(self):
        """현재 DB 연결 반환."""
        return connections[self._db_alias]

    # =========================================================================
    # Connection & Health Check
    # =========================================================================

    def ping(self) -> bool:
        """
        DB 연결 상태 확인 (SELECT 1).

        Returns:
            bool: 연결 성공 여부
        """
        try:
            with self._get_connection().cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return True
        except Exception as e:
            logger.error(f"[PostgresRepository] ping failed: {e}")
            return False

    def get_connection_stats(self) -> ConnectionStats:
        """
        pg_stat_activity에서 연결 통계 조회.

        Returns:
            ConnectionStats: 연결 통계 정보
        """
        with self._get_connection().cursor() as cursor:
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

        return ConnectionStats(
            total_connections=row[0],
            active=row[1],
            idle=row[2],
            idle_in_transaction=row[3],
        )

    def get_active_connection_count(self) -> int:
        """
        활성 연결 수 조회.

        Returns:
            int: 활성 연결 수
        """
        with self._get_connection().cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE state = 'active'")
            result = cursor.fetchone()
        return result[0] if result else 0

    # =========================================================================
    # Sleep & Delay (Stress Test용)
    # =========================================================================

    def pg_sleep(self, seconds: float) -> None:
        """
        pg_sleep 실행 (지연 쿼리).

        Args:
            seconds: 대기 시간 (초)
        """
        with self._get_connection().cursor() as cursor:
            cursor.execute(f"SELECT pg_sleep({seconds})")
            cursor.fetchone()

    def execute_slow_query(self, seconds: int) -> None:
        """
        지정된 시간 동안 DB 연결을 점유하는 느린 쿼리 실행.

        Args:
            seconds: 점유 시간 (초)
        """
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT pg_sleep({seconds})")
            cursor.fetchone()

    def get_backend_pid_with_delay(self, delay_seconds: float = 0.01) -> int:
        """
        현재 백엔드 PID를 가져오면서 약간의 지연 발생.

        Args:
            delay_seconds: 지연 시간

        Returns:
            int: 백엔드 PID
        """
        with connection.cursor() as cursor:
            cursor.execute(f"SELECT pg_backend_pid(), pg_sleep({delay_seconds})")
            result = cursor.fetchone()
        return result[0] if result else 0

    # =========================================================================
    # Advisory Lock Operations
    # =========================================================================

    def acquire_advisory_lock(self, lock_id: int, wait: bool = True) -> bool:
        """
        Exclusive Advisory Lock 획득.

        Args:
            lock_id: 락 ID
            wait: True면 대기, False면 즉시 반환

        Returns:
            bool: 락 획득 성공 여부
        """
        with self._get_connection().cursor() as cursor:
            if wait:
                cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                return True
            else:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                result = cursor.fetchone()
                return result[0] if result else False

    def acquire_advisory_lock_shared(self, lock_id: int, wait: bool = True) -> bool:
        """
        Shared Advisory Lock 획득.

        Args:
            lock_id: 락 ID
            wait: True면 대기, False면 즉시 반환

        Returns:
            bool: 락 획득 성공 여부
        """
        with self._get_connection().cursor() as cursor:
            if wait:
                cursor.execute("SELECT pg_advisory_lock_shared(%s)", [lock_id])
                return True
            else:
                cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [lock_id])
                result = cursor.fetchone()
                return result[0] if result else False

    def release_advisory_lock(self, lock_id: int) -> bool:
        """
        Exclusive Advisory Lock 해제.

        Args:
            lock_id: 락 ID

        Returns:
            bool: 해제 성공 여부
        """
        with self._get_connection().cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
            result = cursor.fetchone()
        return result[0] if result else False

    def release_advisory_lock_shared(self, lock_id: int) -> bool:
        """
        Shared Advisory Lock 해제.

        Args:
            lock_id: 락 ID

        Returns:
            bool: 해제 성공 여부
        """
        with self._get_connection().cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [lock_id])
            result = cursor.fetchone()
        return result[0] if result else False

    def try_advisory_lock(self, lock_id: int) -> bool:
        """
        비대기 모드로 Advisory Lock 시도.

        Args:
            lock_id: 락 ID

        Returns:
            bool: 락 획득 성공 여부
        """
        return self.acquire_advisory_lock(lock_id, wait=False)

    # =========================================================================
    # Session Settings (Stress Test용)
    # =========================================================================

    def set_lock_timeout(self, timeout_ms: int) -> None:
        """
        세션 레벨 lock_timeout 설정.

        Args:
            timeout_ms: 타임아웃 (밀리초), 0이면 무제한
        """
        with self._get_connection().cursor() as cursor:
            if timeout_ms == 0:
                cursor.execute("SET lock_timeout = '0'")
            else:
                cursor.execute(f"SET lock_timeout = '{timeout_ms}ms'")

    def set_statement_timeout(self, timeout_ms: int) -> None:
        """
        세션 레벨 statement_timeout 설정.

        Args:
            timeout_ms: 타임아웃 (밀리초), 0이면 무제한
        """
        with self._get_connection().cursor() as cursor:
            if timeout_ms == 0:
                cursor.execute("SET statement_timeout = '0'")
            else:
                cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")

    def reset_timeouts(self) -> None:
        """lock_timeout과 statement_timeout을 기본값으로 복원."""
        with self._get_connection().cursor() as cursor:
            cursor.execute("SET lock_timeout = '0'")
            cursor.execute("SET statement_timeout = '0'")

    # =========================================================================
    # Stress Test용 특수 쿼리
    # =========================================================================

    def execute_aggregate_query(self, table_name: str) -> tuple[int, float, float, float]:
        """
        테이블에 대한 집계 쿼리 실행.

        Args:
            table_name: 테이블명

        Returns:
            Tuple: (total_count, avg_price, max_price, min_price)
        """
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    COUNT(*) as total_products,
                    AVG(price) as avg_price,
                    MAX(price) as max_price,
                    MIN(price) as min_price
                FROM {table_name}
                WHERE is_active = true
                """
            )
            row = cursor.fetchone()

        return (
            row[0],
            float(row[1]) if row[1] else 0.0,
            float(row[2]) if row[2] else 0.0,
            float(row[3]) if row[3] else 0.0,
        )

    def execute_nonexistent_table_query(self) -> None:
        """
        존재하지 않는 테이블 조회 (CB 테스트용 - 의도적 에러 발생).
        """
        with connection.cursor() as cursor:
            cursor.execute("SELECT * FROM __nonexistent_table_for_cb_test__")

    def execute_timeout_query(self, timeout_ms: int = 1, sleep_seconds: int = 1) -> None:
        """
        타임아웃 에러를 발생시키는 쿼리 (CB 테스트용).

        Args:
            timeout_ms: statement_timeout 설정값
            sleep_seconds: pg_sleep 대기 시간
        """
        with connection.cursor() as cursor:
            cursor.execute(f"SET statement_timeout = '{timeout_ms}ms'")
            cursor.execute(f"SELECT pg_sleep({sleep_seconds})")

    # =========================================================================
    # Context Managers for Complex Operations
    # =========================================================================

    @contextmanager
    def advisory_lock_context(self, lock_id: int, exclusive: bool = True, wait: bool = True) -> Generator[bool, None, None]:
        """
        Advisory Lock을 컨텍스트 매니저로 관리.

        사용 예:
            with repo.advisory_lock_context(12345) as acquired:
                if acquired:
                    # 락 유지 중 작업
                    pass
            # 자동 해제

        Args:
            lock_id: 락 ID
            exclusive: True면 exclusive, False면 shared
            wait: True면 대기, False면 즉시 반환

        Yields:
            bool: 락 획득 성공 여부
        """
        acquired = False
        cursor = None
        try:
            cursor = self._get_connection().cursor()
            if exclusive:
                if wait:
                    cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
                    acquired = True
                else:
                    cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
                    result = cursor.fetchone()
                    acquired = result[0] if result else False
            else:
                if wait:
                    cursor.execute("SELECT pg_advisory_lock_shared(%s)", [lock_id])
                    acquired = True
                else:
                    cursor.execute("SELECT pg_try_advisory_lock_shared(%s)", [lock_id])
                    result = cursor.fetchone()
                    acquired = result[0] if result else False

            yield acquired
        finally:
            if acquired and cursor:
                try:
                    if exclusive:
                        cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])
                    else:
                        cursor.execute("SELECT pg_advisory_unlock_shared(%s)", [lock_id])
                except Exception as e:
                    logger.warning(f"[PostgresRepository] Failed to release lock {lock_id}: {e}")
            if cursor:
                try:
                    cursor.close()
                except Exception:
                    pass

    @contextmanager
    def timeout_context(self, lock_timeout_ms: int = 0, statement_timeout_ms: int = 0) -> Generator[None, None, None]:
        """
        타임아웃 설정을 컨텍스트 매니저로 관리.

        DeadlineContext가 활성화된 경우, 남은 시간이 statement_timeout_ms보다
        짧으면 자동으로 축소하여 deadline 초과를 방지한다.

        사용 예:
            with repo.timeout_context(lock_timeout_ms=100, statement_timeout_ms=1000):
                # 타임아웃이 설정된 상태에서 작업
                pass
            # 자동으로 타임아웃 복원

        Args:
            lock_timeout_ms: lock_timeout (밀리초)
            statement_timeout_ms: statement_timeout (밀리초)
        """
        # DeadlineContext 남은 시간이 statement_timeout보다 짧으면 자동 축소
        try:
            from selfhealing.scaling.deadline_context import get_deadline_aware_statement_timeout

            deadline_timeout = get_deadline_aware_statement_timeout(
                default_db_timeout_ms=statement_timeout_ms if statement_timeout_ms > 0 else 30_000,
            )
            if deadline_timeout is not None:
                statement_timeout_ms = deadline_timeout
        except ImportError:
            pass

        try:
            if lock_timeout_ms > 0:
                self.set_lock_timeout(lock_timeout_ms)
            if statement_timeout_ms > 0:
                self.set_statement_timeout(statement_timeout_ms)
            yield
        finally:
            self.reset_timeouts()

    def create_cursor(self):
        """
        새 커서 생성 (풀 고갈 테스트용).

        Returns:
            cursor: DB 커서
        """
        return connection.cursor()

    def execute_with_cursor(self, cursor, query: str, params: list = None) -> Any:
        """
        주어진 커서로 쿼리 실행.

        Args:
            cursor: DB 커서
            query: 실행할 쿼리
            params: 쿼리 파라미터

        Returns:
            fetchone 결과
        """
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        return cursor.fetchone()


# 싱글톤 인스턴스 (선택적 사용)
_default_repository: PostgresRepository | None = None


def get_postgres_repository(db_alias: str = "default") -> PostgresRepository:
    """
    PostgresRepository 인스턴스 반환.

    Args:
        db_alias: DB 별칭

    Returns:
        PostgresRepository 인스턴스
    """
    global _default_repository

    if db_alias == "default":
        if _default_repository is None:
            _default_repository = PostgresRepository(db_alias)
        return _default_repository

    return PostgresRepository(db_alias)

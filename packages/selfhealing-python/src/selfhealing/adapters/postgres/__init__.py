"""
PostgreSQL Database Adapters.

Raw SQL 쿼리를 캡슐화하여 Repository Pattern을 구현합니다.
"""

from selfhealing.adapters.postgres.repository import PostgresRepository

__all__ = ["PostgresRepository"]

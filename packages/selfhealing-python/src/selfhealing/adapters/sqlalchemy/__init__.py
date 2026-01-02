"""
SQLAlchemy Adapters for Self-Healing System.

Provides SQLAlchemy-based implementations for non-Django projects
(FastAPI, Flask, etc.).

Reference: docs/self_healing/middleware_system/07_HYBRID_STORAGE_ARCHITECTURE.md
"""

from selfhealing.adapters.sqlalchemy.statistics import SQLAlchemyStatisticsAdapter

__all__ = [
    "SQLAlchemyStatisticsAdapter",
]

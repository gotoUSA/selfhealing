"""
Base utilities for SQLAlchemy Repository Implementations.

Common functions and patterns shared across all repositories.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker


def _now() -> datetime:
    """Get current UTC time with timezone."""
    return datetime.now(timezone.utc)


def create_session_factory(engine) -> sessionmaker:
    """
    Create a session factory from an engine.

    Usage:
        engine = create_engine("postgresql://...")
        session_factory = create_session_factory(engine)
        repo = SQLAlchemyFailedOperationRepository(session_factory)
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


class BaseRepository:
    """Base class for SQLAlchemy repositories with common session handling."""

    def __init__(self, session_factory: Callable[[], Session]):
        """
        Initialize repository with a session factory.

        Args:
            session_factory: Callable that returns a new SQLAlchemy Session
        """
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        """Create a new session from the factory."""
        return self._session_factory()

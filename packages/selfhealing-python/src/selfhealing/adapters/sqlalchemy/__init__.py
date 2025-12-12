"""
SQLAlchemy Adapter for Self-Healing System

Provides SQLAlchemy-based implementations of repository interfaces.
Compatible with FastAPI, Flask, or standalone Python applications.

Usage:
    from selfhealing.adapters.sqlalchemy import (
        # Models
        Base,
        FailedOperationModel,
        CircuitBreakerStateModel,
        SecurityIncidentModel,
        # Repositories
        SQLAlchemyFailedOperationRepository,
        SQLAlchemyCircuitBreakerStateRepository,
        SQLAlchemySecurityIncidentRepository,
        # Session management
        create_session_factory,
    )

    # Create engine and session factory
    from sqlalchemy import create_engine
    engine = create_engine("postgresql://user:pass@localhost/db")
    session_factory = create_session_factory(engine)

    # Create tables
    Base.metadata.create_all(engine)

    # Use repositories
    repo = SQLAlchemyFailedOperationRepository(session_factory)
"""

from selfhealing.adapters.sqlalchemy.models import (
    Base,
    FailedOperationModel,
    CircuitBreakerStateModel,
    SecurityIncidentModel,
)
from selfhealing.adapters.sqlalchemy.base import (
    create_session_factory,
    BaseRepository,
)
from selfhealing.adapters.sqlalchemy.failed_operation import (
    SQLAlchemyFailedOperationRepository,
)
from selfhealing.adapters.sqlalchemy.circuit_breaker import (
    SQLAlchemyCircuitBreakerStateRepository,
)
from selfhealing.adapters.sqlalchemy.security_incident import (
    SQLAlchemySecurityIncidentRepository,
)

__all__ = [
    # Base
    "Base",
    "BaseRepository",
    # Models
    "FailedOperationModel",
    "CircuitBreakerStateModel",
    "SecurityIncidentModel",
    # Repositories
    "SQLAlchemyFailedOperationRepository",
    "SQLAlchemyCircuitBreakerStateRepository",
    "SQLAlchemySecurityIncidentRepository",
    # Session management
    "create_session_factory",
]

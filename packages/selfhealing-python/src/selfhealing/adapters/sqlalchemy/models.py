"""
SQLAlchemy Models for Self-Healing System

Framework-agnostic database models using SQLAlchemy.
Compatible with FastAPI, Flask, or standalone Python.

Design Notes:
- Uses declarative_base for model definitions
- Column types match the abstract interface requirements
- JSON columns for flexible data storage
- Proper indexing for common query patterns
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    DateTime,
    Boolean,
    JSON,
    Index,
)
from sqlalchemy.orm import declarative_base
from sqlalchemy.sql import func

from selfhealing.interfaces.repositories import (
    FailedOperationStatus,
    CircuitBreakerStateEnum,
    SecurityIncidentStatus,
)

# Declarative base for all models
Base = declarative_base()


class FailedOperationModel(Base):
    """
    SQLAlchemy model for FailedOperation (DLQ entries).

    Maps to the FailedOperationData interface.
    """

    __tablename__ = "selfhealing_failed_operations"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Domain & Classification
    domain = Column(String(50), nullable=False, index=True)
    failure_type = Column(String(100), nullable=False, index=True)
    status = Column(
        String(50),
        default=FailedOperationStatus.PENDING.value,
        nullable=False,
        index=True,
    )

    # Entity Reference (Generic - no FK dependencies)
    entity_type = Column(String(100), nullable=True, index=True)
    entity_id = Column(String(100), nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    # Snapshot Data (JSON for flexibility)
    snapshot_data = Column(JSON, nullable=False, default=dict)

    # Error Information
    error_code = Column(String(50), nullable=False, default="")
    error_message = Column(Text, nullable=False, default="")

    # Retry Tracking
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=3)
    last_retry_at = Column(DateTime(timezone=True), nullable=True)

    # Forensic Context (JSON)
    request_data = Column(JSON, nullable=False, default=dict)
    response_data = Column(JSON, nullable=False, default=dict)
    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Resolution
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    resolved_by_id = Column(Integer, nullable=True)
    resolution_type = Column(String(50), nullable=False, default="")
    resolution_note = Column(Text, nullable=False, default="")

    # Recovery Hints
    next_action_hint = Column(String(200), nullable=False, default="")
    recommended_action = Column(String(200), nullable=False, default="")

    # Lifecycle Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    expires_at = Column(DateTime(timezone=True), nullable=True)

    # Composite indexes for common query patterns
    __table_args__ = (
        Index("ix_failed_ops_domain_status", "domain", "status"),
        Index("ix_failed_ops_status_retry", "status", "retry_count"),
        Index("ix_failed_ops_created_at", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<FailedOperation(id={self.id}, domain={self.domain}, status={self.status})>"


class CircuitBreakerStateModel(Base):
    """
    SQLAlchemy model for CircuitBreakerState.

    Maps to the CircuitBreakerStateData interface.
    """

    __tablename__ = "selfhealing_circuit_breaker_states"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Service identification (unique)
    service_name = Column(String(100), unique=True, nullable=False, index=True)

    # State
    state = Column(
        String(20),
        default=CircuitBreakerStateEnum.CLOSED.value,
        nullable=False,
        index=True,
    )
    failure_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)

    # Timing
    last_failure_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    opened_at = Column(DateTime(timezone=True), nullable=True)
    half_opened_at = Column(DateTime(timezone=True), nullable=True)

    # Manual Control
    manually_controlled = Column(Boolean, nullable=False, default=False)
    controlled_by_id = Column(Integer, nullable=True)
    control_reason = Column(Text, nullable=False, default="")
    manual_override_expires_at = Column(DateTime(timezone=True), nullable=True)

    # Half-Open Tracking
    half_open_request_count = Column(Integer, nullable=False, default=0)

    # Extra Metadata (JSON)
    extra_metadata = Column(JSON, nullable=False, default=dict)

    # Lifecycle Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    def __repr__(self) -> str:
        return f"<CircuitBreakerState(service={self.service_name}, state={self.state})>"


class SecurityIncidentModel(Base):
    """
    SQLAlchemy model for SecurityIncident.

    Maps to the SecurityIncidentData interface.
    Security incidents are NEVER auto-healed and require human intervention.
    """

    __tablename__ = "selfhealing_security_incidents"

    # Primary key
    id = Column(Integer, primary_key=True, autoincrement=True)

    # Classification
    incident_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    status = Column(
        String(20),
        default=SecurityIncidentStatus.OPEN.value,
        nullable=False,
        index=True,
    )

    # Source Information
    source_ip = Column(String(45), nullable=True, index=True)  # IPv6 max length
    user_agent = Column(Text, nullable=False, default="")
    user_id = Column(Integer, nullable=True, index=True)

    # Entity Reference (Generic - JSON for flexibility)
    entity_refs = Column(JSON, nullable=False, default=dict)

    # Details
    description = Column(Text, nullable=False, default="")
    raw_payload = Column(JSON, nullable=False, default=dict)

    # Investigation
    assigned_to_id = Column(Integer, nullable=True)
    investigation_notes = Column(Text, nullable=False, default="")
    resolved_at = Column(DateTime(timezone=True), nullable=True)

    # Lifecycle Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # Composite indexes for common query patterns
    __table_args__ = (
        Index("ix_security_type_severity", "incident_type", "severity"),
        Index("ix_security_status_created", "status", "created_at"),
        Index("ix_security_source_ip_created", "source_ip", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SecurityIncident(id={self.id}, type={self.incident_type}, severity={self.severity})>"

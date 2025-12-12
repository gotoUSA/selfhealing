"""
Integration Tests for SQLAlchemy Repository with PostgreSQL

This script tests the SQLAlchemy adapter against a real PostgreSQL database
using Docker Compose.

Usage:
    # Start PostgreSQL with Docker
    docker-compose -f docker-compose.test.yml up -d db

    # Run this test
    python scripts/test_sqlalchemy_postgres.py

    # Clean up
    docker-compose -f docker-compose.test.yml down
"""

import os
import sys
import time
from datetime import datetime, timezone

# Add packages to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "selfhealing-python", "src"))


def test_sqlalchemy_with_postgres():
    """Test SQLAlchemy repositories with PostgreSQL."""
    print("=" * 60)
    print("Stage 28-3: SQLAlchemy Adapter Integration Test")
    print("=" * 60)

    # Get database URL from environment or use default
    db_url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/test_selfhealing")

    print(f"\n📦 Database URL: {db_url}")

    try:
        from sqlalchemy import create_engine, text
        from selfhealing.adapters.sqlalchemy import (
            Base,
            SQLAlchemyFailedOperationRepository,
            SQLAlchemyCircuitBreakerStateRepository,
            SQLAlchemySecurityIncidentRepository,
            create_session_factory,
        )
        from selfhealing.interfaces.repositories import (
            FailedOperationStatus,
            CircuitBreakerStateEnum,
            SecurityIncidentType,
            SecuritySeverity,
        )
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Please install: pip install sqlalchemy psycopg2-binary")
        return False

    print("\n🔌 Connecting to PostgreSQL...")
    try:
        engine = create_engine(db_url, echo=False)

        # Test connection
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version()"))
            version = result.fetchone()[0]
            print(f"✅ Connected: {version[:50]}...")
    except Exception as e:
        print(f"❌ Connection failed: {e}")
        print("\n💡 Make sure PostgreSQL is running:")
        print("   docker-compose -f docker-compose.test.yml up -d db")
        return False

    # Create tables
    print("\n📋 Creating tables...")
    Base.metadata.create_all(engine)
    print("✅ Tables created successfully")

    # Create session factory
    session_factory = create_session_factory(engine)

    # Test FailedOperationRepository
    print("\n" + "-" * 40)
    print("Test 1: FailedOperationRepository")
    print("-" * 40)

    fo_repo = SQLAlchemyFailedOperationRepository(session_factory)

    # Create
    print("  Creating failed operation...")
    operation = fo_repo.create(
        domain="payment",
        failure_type="timeout",
        error_message="Connection timed out to payment gateway",
        error_code="TIMEOUT_001",
        order_id=12345,
        user_id=100,
        snapshot_data={"amount": 50000, "currency": "KRW"},
        max_retries=3,
    )
    print(f"  ✅ Created: ID={operation.id}, status={operation.status}")

    # Read
    print("  Reading by ID...")
    retrieved = fo_repo.get_by_id(operation.id)
    assert retrieved is not None
    assert retrieved.domain == "payment"
    print(f"  ✅ Retrieved: domain={retrieved.domain}, error={retrieved.error_code}")

    # Update status
    print("  Updating status...")
    fo_repo.update_status(
        operation.id,
        status=FailedOperationStatus.RESOLVED.value,
        resolution_type="auto_retry",
        resolution_note="Successfully retried",
    )
    updated = fo_repo.get_by_id(operation.id)
    assert updated.status == FailedOperationStatus.RESOLVED.value
    print(f"  ✅ Updated: status={updated.status}")

    # Statistics
    print("  Getting statistics...")
    stats = fo_repo.get_statistics()
    print(f"  ✅ Stats: total={stats['total']}, resolved={stats['resolved']}")

    # Test CircuitBreakerStateRepository
    print("\n" + "-" * 40)
    print("Test 2: CircuitBreakerStateRepository")
    print("-" * 40)

    cb_repo = SQLAlchemyCircuitBreakerStateRepository(session_factory)

    # Get or create
    print("  Creating circuit breaker state...")
    state = cb_repo.get_or_create("toss-payment-api")
    print(f"  ✅ Created: service={state.service_name}, state={state.state}")

    # Record failures
    print("  Recording failures...")
    for i in range(5):
        state = cb_repo.record_failure("toss-payment-api")
    print(f"  ✅ Failure count: {state.failure_count}")

    # Force open
    print("  Forcing circuit open...")
    success, prev, new = cb_repo.atomic_force_open(
        "toss-payment-api",
        reason="Manual intervention - API unstable",
        controlled_by_id=1,
    )
    print(f"  ✅ Force open: {prev} -> {new}")

    # Reset
    print("  Resetting circuit...")
    success, prev, new = cb_repo.atomic_reset(
        "toss-payment-api",
        reason="API recovered",
    )
    state = cb_repo.get_by_service_name("toss-payment-api")
    print(f"  ✅ Reset: failure_count={state.failure_count}, state={state.state}")

    # Test SecurityIncidentRepository
    print("\n" + "-" * 40)
    print("Test 3: SecurityIncidentRepository")
    print("-" * 40)

    si_repo = SQLAlchemySecurityIncidentRepository(session_factory)

    # Create incident
    print("  Creating security incident...")
    incident = si_repo.create(
        incident_type=SecurityIncidentType.WEBHOOK_SIGNATURE_INVALID.value,
        severity=SecuritySeverity.HIGH.value,
        description="Invalid HMAC signature on Toss webhook",
        source_ip="192.168.1.100",
        user_agent="curl/7.68.0",
        raw_payload={"orderId": "ORDER-123", "status": "DONE"},
    )
    print(f"  ✅ Created: ID={incident.id}, type={incident.incident_type}")

    # Get open incidents
    print("  Getting open incidents...")
    open_incidents = si_repo.get_open_incidents()
    print(f"  ✅ Open incidents: {len(open_incidents)}")

    # Resolve incident
    print("  Resolving incident...")
    si_repo.mark_as_resolved(incident.id, "False alarm - signature format changed")
    resolved = si_repo.get_by_id(incident.id)
    print(f"  ✅ Resolved: status={resolved.status}")

    # Clean up (optional)
    print("\n" + "-" * 40)
    print("Clean up")
    print("-" * 40)
    print("  Dropping tables...")
    Base.metadata.drop_all(engine)
    print("  ✅ Tables dropped")

    print("\n" + "=" * 60)
    print("🎉 All integration tests passed!")
    print("=" * 60)
    return True


def main():
    """Main entry point."""
    # Wait for PostgreSQL if needed
    retries = 3
    for i in range(retries):
        if test_sqlalchemy_with_postgres():
            sys.exit(0)
        if i < retries - 1:
            print(f"\n⏳ Retrying in 5 seconds... ({i + 1}/{retries})")
            time.sleep(5)

    sys.exit(1)


if __name__ == "__main__":
    main()

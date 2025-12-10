# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2025-12-10

### 🎉 Initial Release

This is the first release of the `selfhealing` package, extracted from the 
`django-shopping-mall` project's self-healing infrastructure.

### Added

#### Core Features
- **Circuit Breaker Service**: Prevents cascade failures with configurable thresholds
  - Supports `closed`, `open`, `half_open` states
  - Automatic state transitions based on failure rates
  - Per-service configuration

- **Dead Letter Queue (DLQ)**: Stores failed operations for later retry
  - Domain-based categorization (payment, order, notification, etc.)
  - Status tracking (pending, retrying, completed, failed, skipped)
  - Retention policy enforcement

- **Replay Service**: Manual and automatic retry of failed operations
  - Batch replay support
  - Selective replay by domain or status
  - Replay history tracking

- **Retry Handler**: Configurable retry strategies
  - Exponential backoff
  - Linear backoff
  - Constant backoff
  - Jitter support

- **Backoff Calculator**: Calculates retry delays
  - Configurable min/max delays
  - Multiple backoff algorithms

- **Idempotency Service**: Prevents duplicate operation execution
  - Operation fingerprinting
  - TTL-based cleanup

- **Security Violation Service**: Detects and handles security incidents
  - Replay attack detection
  - Anomaly detection
  - Incident logging

- **Security Notification Service**: Sends security alerts
  - Configurable notification channels
  - Rate limiting for notifications

#### Interfaces (Framework Agnostic)
- `FailedOperationRepository`: Abstract interface for DLQ storage
- `CircuitBreakerStateRepository`: Abstract interface for CB state storage
- `SecurityIncidentRepository`: Abstract interface for security incidents
- `ReplayResultRepository`: Abstract interface for replay results

#### Adapters
- **Django Adapter**: Django ORM implementations of all repositories
- In-memory adapter for testing (planned)

#### Metrics
- Prometheus metrics integration
  - `selfhealing_dlq_operations_total`: DLQ operation counter
  - `selfhealing_circuit_breaker_state`: CB state gauge
  - `selfhealing_replay_operations_total`: Replay counter
  - `selfhealing_retry_attempts_total`: Retry attempt counter

#### Configuration
- `SelfHealingConfig`: Centralized configuration class
- `DLQSettings`: DLQ-specific settings
- `SLAThresholds`: SLA monitoring thresholds
- `CircuitBreakerConfig`: CB configuration

### Changed
- Extracted from `shopping.services.self_healing` module
- Restructured to follow hexagonal architecture
- Made framework-agnostic with adapter pattern

### Technical Details
- **Python Version**: 3.10+
- **Dependencies**: Optional dependencies for Django, Celery, Prometheus
- **Testing**: pytest with 137+ test cases
- **Package Structure**:
  ```
  selfhealing/
  ├── core/          # Core business logic
  ├── interfaces/    # Abstract interfaces
  ├── adapters/      # Framework-specific implementations
  ├── services/      # Service layer
  ├── metrics/       # Observability
  └── api/           # REST API (Django)
  ```

### Migration from shopping.services.self_healing

```python
# Before
from shopping.services.self_healing.circuit_breaker_service import CircuitBreakerService
from shopping.services.self_healing.dlq_service import DLQService

# After
from selfhealing.services import CircuitBreakerService, DLQService
```

See [Migration Guide](docs/MIGRATION.md) for detailed instructions.

---

## [Unreleased]

### Planned
- FastAPI adapter
- Flask adapter
- Redis-based in-memory repositories
- AsyncIO support
- OpenTelemetry integration
- Enhanced documentation

---

*For more information, see the [README](README.md) and [documentation](docs/).*

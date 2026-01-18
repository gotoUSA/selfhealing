"""
OpenTelemetry Adapter 테스트 공통 설정.
"""

import pytest


@pytest.fixture
def otel_config():
    """OpenTelemetry config fixture."""
    from selfhealing.adapters.observability.opentelemetry.config import OpenTelemetryConfig
    return OpenTelemetryConfig(enabled=True, service_name="test", environment="test")


@pytest.fixture
def noop_adapter():
    """NoOp adapter fixture."""
    from selfhealing.adapters.observability.opentelemetry.noop import NoOpOpenTelemetryAdapter
    return NoOpOpenTelemetryAdapter()

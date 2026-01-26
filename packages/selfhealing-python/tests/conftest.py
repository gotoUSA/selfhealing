"""
Pytest configuration and fixtures for selfhealing tests.
"""

import os
import pytest
from datetime import datetime


# =============================================================================
# Django 설정 (DRF 테스트를 위해 필요)
# =============================================================================

def _configure_django_settings():
    """
    Django 설정을 최소한으로 구성.
    
    DRF (Django REST Framework) 예외 처리 테스트를 위해 필요.
    """
    try:
        import django
        from django.conf import settings
        
        if not settings.configured:
            settings.configure(
                DEBUG=True,
                DATABASES={},
                INSTALLED_APPS=[
                    "django.contrib.contenttypes",
                    "django.contrib.auth",
                    "rest_framework",
                ],
                REST_FRAMEWORK={},
                USE_TZ=True,
            )
            django.setup()
    except ImportError:
        pass


# Django 설정은 모듈 로드 시 즉시 실행
_configure_django_settings()


# =============================================================================
# Settings Singleton Reset Fixtures (테스트 격리용)
# =============================================================================

@pytest.fixture(autouse=True, scope="function")
def auto_reset_audit_settings():
    """
    모든 테스트 전후에 Audit 관련 Settings 싱글톤을 자동으로 리셋하는 fixture.
    
    Step 3 리팩토링 후 Audit 모듈이 Pydantic Settings를 사용하므로,
    환경변수 변경이 다른 테스트에 영향을 주지 않도록 격리합니다.
    
    리셋 대상 Settings:
    - HashChainSettings: AtomicMergeSwap, ShardedDateLock, IntegrityAuditTrail
    - AuditIntegritySettings: DailyHashAnchor, CrossClusterLinker, HealthScore, S3WORM
    - CascadeRetentionSettings: CascadeEventAuditor
    - ResilientRecorderSettings: InMemoryAuditBuffer
    - AuditSettings: get_recommended_retention
    - AuditWatchdogSettings: AuditWatchdog
    """
    # Setup: 테스트 전에 settings 리셋
    _reset_all_audit_settings()
    
    yield
    
    # Teardown: 테스트 후에도 리셋 (다음 테스트를 위해)
    _reset_all_audit_settings()


def _reset_all_audit_settings():
    """모든 Audit 관련 Settings 싱글톤을 리셋합니다."""
    try:
        from selfhealing.settings import hash_chain
        hash_chain.reset_hash_chain_settings()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.settings import audit_integrity
        audit_integrity.reset_audit_integrity_settings()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.settings import cascade_retention
        cascade_retention.reset_cascade_retention_settings()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.settings import resilient_recorder
        resilient_recorder.reset_resilient_recorder_settings()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.settings import audit_settings
        audit_settings.reset_audit_settings()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.settings import audit_watchdog
        audit_watchdog.reset_audit_watchdog_settings()
    except (ImportError, AttributeError):
        pass
    
    # Audit 모듈 싱글톤 리셋 (Settings 연동되어 있는 클래스들)
    try:
        from selfhealing.audit.resilience.buffer import InMemoryAuditBuffer
        InMemoryAuditBuffer.reset_instance()
    except (ImportError, AttributeError):
        pass
    
    try:
        from selfhealing.audit.cascade_auditor import reset_cascade_auditor
        reset_cascade_auditor()
    except (ImportError, AttributeError):
        pass


# =============================================================================
# Singleton Reset Fixtures (테스트 격리용)
# =============================================================================

@pytest.fixture(autouse=True, scope="function")
def auto_reset_watchdog_singleton():
    """
    모든 테스트 전에 AuditWatchdog 싱글톤을 자동으로 리셋하는 fixture.
    
    다른 테스트 파일에서 watchdog을 시작한 경우에도 격리를 보장합니다.
    """
    import selfhealing.audit.audit_watchdog as aw_module
    
    # Setup: 기존 싱글톤 정리
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
            # 스레드 완전 종료 대기
            if aw_module._watchdog_instance._thread and aw_module._watchdog_instance._thread.is_alive():
                aw_module._watchdog_instance._thread.join(timeout=1.0)
        except Exception:
            pass
        aw_module._watchdog_instance = None
    
    yield
    
    # Teardown: 테스트 후 정리 (다음 테스트를 위해)
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
            # 스레드 완전 종료 대기
            if aw_module._watchdog_instance._thread and aw_module._watchdog_instance._thread.is_alive():
                aw_module._watchdog_instance._thread.join(timeout=1.0)
        except Exception:
            pass
        aw_module._watchdog_instance = None


@pytest.fixture
def reset_watchdog_singleton():
    """
    AuditWatchdog 싱글톤을 테스트 전후로 리셋하는 fixture.
    
    Usage:
        def test_something(reset_watchdog_singleton):
            # 테스트 코드
    """
    import selfhealing.audit.audit_watchdog as aw_module
    from selfhealing.audit.audit_watchdog import WatchdogState
    
    # Setup: 기존 싱글톤 정리
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
            # 스레드 완전 종료 대기
            if aw_module._watchdog_instance._thread and aw_module._watchdog_instance._thread.is_alive():
                aw_module._watchdog_instance._thread.join(timeout=1.0)
        except Exception:
            pass
        aw_module._watchdog_instance = None
    
    yield
    
    # Teardown: 테스트 후 정리
    if aw_module._watchdog_instance is not None:
        try:
            aw_module._watchdog_instance.stop()
            # 스레드 완전 종료 대기
            if aw_module._watchdog_instance._thread and aw_module._watchdog_instance._thread.is_alive():
                aw_module._watchdog_instance._thread.join(timeout=1.0)
        except Exception:
            pass
        aw_module._watchdog_instance = None


# =============================================================================
# Audit Module Reload Fixture (테스트 격리용)
# =============================================================================

@pytest.fixture(autouse=True, scope="function")
def reset_audit_modules():
    """
    각 테스트 전후에 audit 관련 모듈을 sys.modules에서 제거하여 
    mock이 올바르게 적용되도록 함.
    
    이 fixture는 테스트 간 모듈 캐싱으로 인해 mock이 적용되지 않는 문제를 해결합니다.
    
    문제 원인:
    - Python에서 `from X import Y`로 import된 객체는 로컬 바인딩됨
    - 모듈이 이미 import된 상태에서 patch하면 원본 참조에 영향 없음
    - 테스트 간 모듈 캐싱으로 이전 테스트의 import 상태가 유지됨
    
    해결:
    - 테스트 전/후에 audit 관련 모듈을 sys.modules에서 제거
    - 각 테스트에서 fresh import + patch 적용 가능
    """
    import sys
    
    # 제거할 모듈 목록 (의존성 역순으로 정렬)
    modules_to_clear = [
        "selfhealing.services.audit_helpers",
        "selfhealing.services.audit",
        "selfhealing.services.audit.retry_audit",
        "selfhealing.services.audit.chaos_audit",
        "selfhealing.services.audit.dlq_audit",
        "selfhealing.services.audit.compliance_audit",
        "selfhealing.services.audit.storage_audit",
        "selfhealing.services.audit.cb_audit",
        "selfhealing.services.audit.base",
    ]
    
    def clear_modules():
        for mod_name in modules_to_clear:
            if mod_name in sys.modules:
                try:
                    del sys.modules[mod_name]
                except KeyError:
                    pass
    
    # Setup: 테스트 전에 모듈 캐시 정리
    clear_modules()
    
    # 테스트 실행
    yield
    
    # Teardown: 테스트 후에도 정리 (다음 테스트를 위해)
    clear_modules()


# =============================================================================
# DB 연결 필요 테스트 자동 Skip 설정
# =============================================================================

def pytest_collection_modifyitems(config, items):
    """
    DB 연결이 필요한 테스트들을 자동으로 skip 처리합니다.
    packages/selfhealing-python/tests 내에서 django_db 마커가 있는 테스트는
    DB가 없는 환경에서는 skip됩니다.
    """
    # DB 연결 가능 여부 확인
    db_available = os.environ.get("SELFHEALING_TEST_DB_AVAILABLE", "false").lower() == "true"
    
    if db_available:
        return  # DB가 있으면 skip하지 않음
    
    skip_db = pytest.mark.skip(reason="Database not available (set SELFHEALING_TEST_DB_AVAILABLE=true to run)")
    
    for item in items:
        if "django_db" in [marker.name for marker in item.iter_markers()]:
            item.add_marker(skip_db)


# =============================================================================
# Core Type Fixtures
# =============================================================================


@pytest.fixture
def sample_failed_operation_data():
    """Sample failed operation data for tests."""
    from selfhealing.core.types import FailedOperationData

    return FailedOperationData(
        id=1,
        domain="order",
        failure_type="network",
        status="pending",
        created_at=datetime.now(),
        context={"order_id": 123, "amount": 10000},
        error_message="Connection timeout",
        retry_count=0,
        max_retries=3,
    )


@pytest.fixture
def sample_circuit_breaker_data():
    """Sample circuit breaker state data for tests."""
    from selfhealing.core.types import CircuitBreakerStateData

    return CircuitBreakerStateData(
        service_name="external-gateway",
        state="closed",
        failure_count=0,
        success_count=10,
    )


@pytest.fixture
def sample_config():
    """Sample configuration for tests."""
    from selfhealing.core.config import SelfHealingConfig

    return SelfHealingConfig()


# =============================================================================
# Chaos Engineering Fixtures (imported from chaos/conftest.py)
# =============================================================================


@pytest.fixture
def failure_injector():
    """Provides a configurable failure injector for chaos tests."""
    from tests.chaos.conftest import FailureInjector

    return FailureInjector(failure_rate=0.3)


@pytest.fixture
def burst_failure_injector():
    """Provides a burst failure pattern injector."""
    from tests.chaos.conftest import BurstFailureInjector

    return BurstFailureInjector(burst_size=10, burst_interval=50)


@pytest.fixture
def latency_injector():
    """Provides a latency injector for slow degradation tests."""
    from tests.chaos.conftest import LatencyInjector

    return LatencyInjector(
        min_latency_ms=100,
        max_latency_ms=30000,
        degradation_rate=100,
    )


@pytest.fixture
def resource_simulator():
    """Provides a resource exhaustion simulator."""
    from tests.chaos.conftest import ResourceExhaustionSimulator

    return ResourceExhaustionSimulator(max_connections=100)


# =============================================================================
# Pluggable Architecture Fixtures
# =============================================================================


@pytest.fixture
def memory_cache_adapter():
    """Provides an in-memory cache adapter for testing."""
    from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

    adapter = InMemoryCacheAdapter(key_prefix="test:")
    yield adapter
    adapter.flush_all()


@pytest.fixture
def sync_queue_adapter():
    """Provides a synchronous task queue adapter for testing."""
    from selfhealing.adapters.queues.sync_adapter import SyncTaskAdapter

    return SyncTaskAdapter()


@pytest.fixture(autouse=False)
def test_provider_registry():
    """Setup and teardown provider registry for tests."""
    from selfhealing.factory import ProviderRegistry

    # Store original state
    original_instances = ProviderRegistry._instances.copy()

    # Set test defaults
    ProviderRegistry.set_defaults(
        cache="memory",
        queue="sync",
    )
    ProviderRegistry.clear_instances()

    yield ProviderRegistry

    # Restore original state
    ProviderRegistry._instances = original_instances

"""
Pytest configuration and fixtures for selfhealing tests.
"""

import atexit
import os
import sys
from datetime import datetime

import pytest

# =============================================================================
# Pytest Configuration - 테스트 환경 초기화
# =============================================================================


def pytest_configure(config):
    """
    pytest 시작 시 테스트 환경 설정.

    단위 테스트에서는 실제 DB/Redis 연결을 시도하지 않도록
    atexit 핸들러 등록을 방지하고, 관련 플래그를 비활성화합니다.
    """
    # 로그 노이즈 차단: selfhealing import 전에 설정해야 configure_structlog()이 존중
    os.environ.setdefault("SELFHEALING_TEST_LOG_LEVEL", "WARNING")

    # 테스트 환경 플래그 설정
    os.environ.setdefault("SELFHEALING_TEST_MODE", "true")

    # async_audit_lifecycle의 atexit 핸들러 등록 방지
    try:
        import selfhealing.audit.async_audit_lifecycle as lifecycle_module

        # 이미 등록된 것처럼 설정하여 추가 등록 방지
        lifecycle_module._shutdown_registered = True
    except ImportError:
        pass


def pytest_unconfigure(config):
    """
    pytest 종료 시 정리.

    atexit에 등록된 graceful_shutdown_audit_system 핸들러를 제거하여
    테스트 종료 시 실제 리소스 접근을 방지합니다.
    """
    try:
        from selfhealing.audit.async_audit_lifecycle import (
            graceful_shutdown_audit_system,
        )

        # atexit에서 핸들러 제거
        atexit.unregister(graceful_shutdown_audit_system)
    except (ImportError, AttributeError):
        pass


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
    """
    모든 Audit 관련 Settings 싱글톤을 리셋합니다.

    최적화: sys.modules를 먼저 확인하여, 실제로 로드된 모듈만 리셋합니다.
    로드되지 않은 모듈은 리셋할 필요가 없으므로 import 시도를 건너뜁니다.
    이를 통해 해당 모듈을 사용하지 않는 테스트(87%)에서 오버헤드를 제거합니다.
    """
    # 리셋 대상: (모듈 키, 리셋 함수/속성) 매핑
    _SETTINGS_RESETS = {
        "selfhealing.settings.hash_chain": "reset_hash_chain_settings",
        "selfhealing.settings.audit_integrity": "reset_audit_integrity_settings",
        "selfhealing.settings.cascade_retention": "reset_cascade_retention_settings",
        "selfhealing.settings.resilient_recorder": "reset_resilient_recorder_settings",
        "selfhealing.settings.audit_settings": "reset_audit_settings",
        "selfhealing.settings.audit_watchdog": "reset_audit_watchdog_settings",
    }

    # CausationContext 리셋 (병렬 테스트 격리용)
    ctx_mod = sys.modules.get("selfhealing.context.causation_context")
    if ctx_mod is not None:
        try:
            ctx_mod._current_causation.set(None)
        except (AttributeError, LookupError):
            pass

    # Settings 모듈 일괄 리셋
    for mod_key, reset_fn_name in _SETTINGS_RESETS.items():
        mod = sys.modules.get(mod_key)
        if mod is not None:
            try:
                getattr(mod, reset_fn_name)()
            except (AttributeError, TypeError):
                pass

    # Audit 모듈 싱글톤 리셋 (Settings 연동 클래스들)
    buf_mod = sys.modules.get("selfhealing.audit.resilience.buffer")
    if buf_mod is not None:
        try:
            buf_mod.InMemoryAuditBuffer.reset_instance()
        except (AttributeError, TypeError):
            pass

    cascade_mod = sys.modules.get("selfhealing.audit.cascade_auditor")
    if cascade_mod is not None:
        try:
            cascade_mod.reset_cascade_auditor()
        except (AttributeError, TypeError):
            pass

    # Error Budget Weight Map 리셋
    weight_mod = sys.modules.get("selfhealing.services.error_budget.exception_weights")
    if weight_mod is not None:
        try:
            weight_mod.reset_exception_weight_map()
        except (AttributeError, TypeError):
            pass

    # ClusterIdentity 싱글톤 리셋
    ci_mod = sys.modules.get("selfhealing.core.cluster_identity")
    if ci_mod is not None:
        try:
            ci_mod._identity = None
            ci_mod._quarantine_mode = False
        except AttributeError:
            pass

    # Service Factory 싱글톤 리셋
    factory_mod = sys.modules.get("selfhealing.services.factory.singleton")
    if factory_mod is not None:
        try:
            factory_mod.reset_service_singletons()
        except (AttributeError, TypeError):
            pass

    # RecoveryCoordinator 싱글톤 리셋
    rc_mod = sys.modules.get("selfhealing.services.coordination.recovery_coordinator")
    if rc_mod is not None:
        try:
            rc_mod.reset_recovery_coordinator()
        except (AttributeError, TypeError):
            pass

    # ProviderRegistry 인스턴스 캐시 리셋 (병렬 테스트 격리용)
    pr_mod = sys.modules.get("selfhealing.factory")
    if pr_mod is not None:
        try:
            pr_mod.ProviderRegistry.clear_instances()
        except (AttributeError, TypeError):
            pass

    # AdaptiveThrottle 싱글톤 리셋 (병렬 테스트 격리용)
    at_mod = sys.modules.get("selfhealing.services.throttle.adaptive")
    if at_mod is not None:
        try:
            at_mod.reset_adaptive_throttle()
        except (AttributeError, TypeError):
            pass

    # ErrorBudgetGate 싱글톤 리셋 (테스트 격리용)
    gate_mod = sys.modules.get("selfhealing.services.error_budget_gate.gate")
    if gate_mod is not None:
        try:
            gate_mod._gate_instance = None
        except AttributeError:
            pass


# =============================================================================
# Logging State Isolation (테스트 간 로거 오염 방지)
# =============================================================================


@pytest.fixture(autouse=True, scope="session")
def _isolate_logging_state_session():
    """
    세션 시작 시 selfhealing 로거의 propagate=True, 핸들러 제거를 1회 수행.

    근본 원인: django.setup()이 호출되면 Django LOGGING 설정이 적용되어
    selfhealing 로거에 propagate=False + 전용 StreamHandler가 설정된다.
    이로 인해 selfhealing.* 로거의 레코드가 루트 로거(caplog 핸들러 위치)까지
    전파되지 않아 caplog가 빈 결과를 반환하는 문제가 발생한다.

    성능: function scope → session scope로 변경.
    Python 3.12의 Logger.setLevel()은 _clear_cache()를 호출하여 전체 로거를
    순회한다. selfhealing 로거 165개 × 10,900 테스트 = 수억 회 연산이
    테스트 스위트를 3~4분에서 9분 이상으로 느리게 만든 근본 원인이었다.

    로그 레벨 오버라이드 (279_TEST_LOG_LEVEL_OVERRIDE):
    SELFHEALING_TEST_LOG_LEVEL 환경변수(기본 WARNING)로 root logger 레벨을
    제어하여 테스트 시 로그 노이즈를 90%+ 감소시킨다.
    디버깅 필요 시: SELFHEALING_TEST_LOG_LEVEL=DEBUG pytest ...
    """
    import logging as _logging

    root = _logging.getLogger()
    root_level = root.level
    root_handlers = list(root.handlers)

    # 테스트 환경 로그 레벨 오버라이드: 기본 WARNING으로 노이즈 차단
    test_log_level_name = os.environ.get("SELFHEALING_TEST_LOG_LEVEL", "WARNING")
    test_log_level = getattr(_logging, test_log_level_name.upper(), _logging.WARNING)
    root.setLevel(test_log_level)

    # 외부 라이브러리 노이즈 로거 차단: faker, urllib3 등이 DEBUG 로그를 대량 발생시킴
    _noisy_loggers = ("faker", "faker.factory", "urllib3", "asyncio", "parso")
    _noisy_saved: dict[str, int] = {}
    for _name in _noisy_loggers:
        _lg = _logging.getLogger(_name)
        _noisy_saved[_name] = _lg.level
        _lg.setLevel(test_log_level)

    # selfhealing 네임스페이스 로거 상태 저장 + propagate 강제 활성화 (1회만)
    _saved: dict[str, tuple[int, bool, list]] = {}
    for name, logger_obj in _logging.Logger.manager.loggerDict.items():
        if isinstance(logger_obj, _logging.Logger) and name.startswith("selfhealing"):
            _saved[name] = (logger_obj.level, logger_obj.propagate, list(logger_obj.handlers))
            logger_obj.propagate = True
            logger_obj.handlers = []

    yield

    # 세션 종료 시 복원
    root.setLevel(root_level)
    root.handlers = root_handlers
    for _name, _prev_level in _noisy_saved.items():
        _logging.getLogger(_name).setLevel(_prev_level)
    for name, (level, propagate, handlers) in _saved.items():
        logger_obj = _logging.getLogger(name)
        logger_obj.setLevel(level)
        logger_obj.propagate = propagate
        logger_obj.handlers = handlers


@pytest.fixture(autouse=True, scope="function")
def _ensure_selfhealing_propagates():
    """
    매 테스트마다 selfhealing 루트 로거의 propagate=True를 보장.

    django.setup() → dictConfig()가 selfhealing 로거에 propagate=False +
    console 핸들러를 재설정할 수 있다. 이를 매 테스트 전에 리셋한다.

    성능: 로거 1개의 속성 2개만 설정 → setLevel() 미호출,
    _clear_cache() 미발생, 오버헤드 무시 가능.
    """
    import logging as _logging

    sh = _logging.getLogger("selfhealing")
    sh.propagate = True
    sh.handlers = []
    yield


# =============================================================================
# Singleton Reset Fixtures (테스트 격리용)
# =============================================================================


@pytest.fixture(autouse=True, scope="function")
def auto_reset_watchdog_singleton():
    """
    모든 테스트 전에 AuditWatchdog 싱글톤을 자동으로 리셋하는 fixture.

    최적화: 모듈이 로드되지 않았거나 인스턴스가 없으면 즉시 반환합니다.
    대부분의 테스트(99.7%)에서 오버헤드 없이 통과합니다.
    """
    aw_module = sys.modules.get("selfhealing.audit.audit_watchdog")

    # Fast path: 모듈이 로드되지 않았으면 리셋 불필요
    if aw_module is None or getattr(aw_module, "_watchdog_instance", None) is None:
        yield
        # Teardown: 테스트 중 모듈이 로드되었을 수 있으므로 재확인
        aw_module = sys.modules.get("selfhealing.audit.audit_watchdog")
        if aw_module is not None and getattr(aw_module, "_watchdog_instance", None) is not None:
            _cleanup_watchdog(aw_module)
        return

    # Slow path: 실제 리셋 필요
    _cleanup_watchdog(aw_module)
    yield
    if getattr(aw_module, "_watchdog_instance", None) is not None:
        _cleanup_watchdog(aw_module)


def _cleanup_watchdog(aw_module):
    """watchdog 인스턴스 정리 헬퍼."""
    instance = getattr(aw_module, "_watchdog_instance", None)
    if instance is None:
        return
    try:
        instance.stop()
        thread = getattr(instance, "_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=0.2)
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
    aw_module = sys.modules.get("selfhealing.audit.audit_watchdog")
    if aw_module is None:
        import selfhealing.audit.audit_watchdog as aw_module

    _cleanup_watchdog(aw_module)
    yield
    _cleanup_watchdog(aw_module)


# =============================================================================
# Audit Module Reload Fixture (테스트 격리용)
# =============================================================================


# 제거할 audit 모듈 목록 (의존성 역순으로 정렬) — 모듈 레벨 상수로 정의
_AUDIT_MODULES_TO_CLEAR = frozenset(
    [
        "selfhealing.services.audit",
        "selfhealing.services.audit",
        "selfhealing.services.audit.retry_audit",
        "selfhealing.services.audit.chaos_audit",
        "selfhealing.services.audit.dlq_audit",
        "selfhealing.services.audit.compliance_audit",
        "selfhealing.services.audit.storage_audit",
        "selfhealing.services.audit.cb_audit",
        "selfhealing.services.audit.base",
    ]
)


@pytest.fixture(autouse=True, scope="function")
def reset_audit_modules():
    """
    각 테스트 전후에 audit 관련 모듈을 sys.modules에서 제거하여
    mock이 올바르게 적용되도록 함.

    최적화: 해당 모듈이 sys.modules에 하나도 없으면 즉시 반환합니다.
    대부분의 테스트(96.4%)에서 한 번의 set intersection으로 통과합니다.
    """
    loaded = _AUDIT_MODULES_TO_CLEAR & sys.modules.keys()
    if not loaded:
        yield
        # Teardown: 테스트 중 로드되었을 수 있으므로 재확인
        loaded = _AUDIT_MODULES_TO_CLEAR & sys.modules.keys()
        for mod_name in loaded:
            sys.modules.pop(mod_name, None)
        return

    # Slow path: 실제로 로드된 모듈이 있으면 정리
    for mod_name in loaded:
        sys.modules.pop(mod_name, None)

    yield

    loaded = _AUDIT_MODULES_TO_CLEAR & sys.modules.keys()
    for mod_name in loaded:
        sys.modules.pop(mod_name, None)


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
    from selfhealing.interfaces.repositories import FailedOperationData

    return FailedOperationData(
        id=1,
        domain="order",
        failure_type="network",
        status="pending",
        created_at=datetime.now(),
        metadata={"order_id": 123, "amount": 10000},
        error_message="Connection timeout",
        retry_count=0,
        max_retries=3,
    )


@pytest.fixture
def sample_circuit_breaker_data():
    """Sample circuit breaker state data for tests."""
    from selfhealing.interfaces.repositories import CircuitBreakerStateData

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
    from .chaos.conftest import FailureInjector

    return FailureInjector(failure_rate=0.3)


@pytest.fixture
def burst_failure_injector():
    """Provides a burst failure pattern injector."""
    from .chaos.conftest import BurstFailureInjector

    return BurstFailureInjector(burst_size=10, burst_interval=50)


@pytest.fixture
def latency_injector():
    """Provides a latency injector for slow degradation tests."""
    from .chaos.conftest import LatencyInjector

    return LatencyInjector(
        min_latency_ms=100,
        max_latency_ms=30000,
        degradation_rate=100,
    )


@pytest.fixture
def resource_simulator():
    """Provides a resource exhaustion simulator."""
    from .chaos.conftest import ResourceExhaustionSimulator

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

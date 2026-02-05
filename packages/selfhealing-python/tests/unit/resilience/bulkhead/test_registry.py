"""
BulkheadRegistry 단위 테스트.

격벽 레지스트리의 동작을 검증합니다:
- 기본 격벽 자동 등록
- ConnectionType 기반 조회
- 커스텀 도메인 지원
- DB alias/캐시 인스턴스별 격벽
"""

from __future__ import annotations

import pytest

from selfhealing.core.connection_health import ConnectionType
from selfhealing.resilience.bulkhead.base import BulkheadType
from selfhealing.resilience.bulkhead.registry import (
    BulkheadRegistry,
    get_bulkhead_registry,
    reset_bulkhead_registry,
)
from selfhealing.resilience.bulkhead.semaphore import SemaphoreBulkhead
from selfhealing.resilience.bulkhead.threadpool import ThreadPoolBulkhead
from selfhealing.settings.bulkhead import BulkheadSettings, reset_bulkhead_settings


@pytest.fixture(autouse=True)
def reset_singletons():
    """각 테스트 전후로 싱글톤 초기화."""
    reset_bulkhead_registry()
    reset_bulkhead_settings()
    yield
    reset_bulkhead_registry()
    reset_bulkhead_settings()


class TestBulkheadRegistryDefaultBulkheads:
    """기본 격벽 자동 등록 테스트."""

    def test_database_bulkhead_registered(self):
        """DATABASE 격벽이 자동 등록됨."""
        registry = BulkheadRegistry()

        bulkhead = registry.get(ConnectionType.DATABASE)
        assert bulkhead.name == "database"
        assert isinstance(bulkhead, SemaphoreBulkhead)

    def test_cache_bulkhead_registered(self):
        """CACHE 격벽이 자동 등록됨."""
        registry = BulkheadRegistry()

        bulkhead = registry.get(ConnectionType.CACHE)
        assert bulkhead.name == "cache"
        assert isinstance(bulkhead, SemaphoreBulkhead)

    def test_external_api_bulkhead_registered(self):
        """EXTERNAL_API 격벽이 자동 등록됨 (ThreadPool)."""
        registry = BulkheadRegistry()

        bulkhead = registry.get(ConnectionType.EXTERNAL_API)
        assert bulkhead.name == "external_api"
        assert isinstance(bulkhead, ThreadPoolBulkhead)

    def test_message_queue_bulkhead_registered(self):
        """MESSAGE_QUEUE 격벽이 자동 등록됨."""
        registry = BulkheadRegistry()

        bulkhead = registry.get(ConnectionType.MESSAGE_QUEUE)
        assert bulkhead.name == "message_queue"
        assert isinstance(bulkhead, SemaphoreBulkhead)

    def test_list_names_includes_all_defaults(self):
        """list_names가 모든 기본 격벽 포함."""
        registry = BulkheadRegistry()

        names = registry.list_names()
        assert "database" in names
        assert "cache" in names
        assert "external_api" in names
        assert "message_queue" in names


class TestBulkheadRegistryGet:
    """격벽 조회 테스트."""

    def test_get_by_connection_type(self):
        """ConnectionType으로 조회."""
        registry = BulkheadRegistry()

        bulkhead = registry.get(ConnectionType.DATABASE)
        assert bulkhead.name == "database"

    def test_get_by_string_name(self):
        """문자열 이름으로 조회."""
        registry = BulkheadRegistry()

        bulkhead = registry.get("database")
        assert bulkhead.name == "database"

    def test_get_unknown_raises_key_error(self):
        """등록되지 않은 이름 조회 시 KeyError."""
        registry = BulkheadRegistry()

        with pytest.raises(KeyError) as exc_info:
            registry.get("unknown_domain")

        assert "unknown_domain" in str(exc_info.value)


class TestBulkheadRegistryGetOrCreate:
    """격벽 조회 또는 생성 테스트."""

    def test_get_or_create_existing(self):
        """기존 격벽 반환."""
        registry = BulkheadRegistry()

        bulkhead1 = registry.get_or_create("database")
        bulkhead2 = registry.get_or_create("database")

        assert bulkhead1 is bulkhead2

    def test_get_or_create_new_semaphore(self):
        """새 세마포어 격벽 생성."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_or_create(
            "custom_domain",
            max_concurrent=15,
            bulkhead_type="semaphore",
        )

        assert bulkhead.name == "custom_domain"
        assert isinstance(bulkhead, SemaphoreBulkhead)
        state = bulkhead.get_state()
        assert state.max_concurrent == 15

    def test_get_or_create_new_threadpool(self):
        """새 스레드풀 격벽 생성."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_or_create(
            "custom_pool",
            max_concurrent=8,
            bulkhead_type="thread_pool",
        )

        assert bulkhead.name == "custom_pool"
        assert isinstance(bulkhead, ThreadPoolBulkhead)
        state = bulkhead.get_state()
        assert state.max_concurrent == 8


class TestBulkheadRegistryGetAsync:
    """비동기 격벽 조회 테스트."""

    def test_get_async_creates_async_bulkhead(self):
        """비동기 격벽 생성."""
        registry = BulkheadRegistry()

        async_bh = registry.get_async(ConnectionType.DATABASE)
        assert async_bh.name == "database"

    def test_get_async_cached(self):
        """비동기 격벽이 캐싱됨."""
        registry = BulkheadRegistry()

        async_bh1 = registry.get_async("database")
        async_bh2 = registry.get_async("database")

        assert async_bh1 is async_bh2


class TestBulkheadRegistryGetForDatabase:
    """DB alias별 격벽 테스트."""

    def test_get_for_database_default(self):
        """기본 DB alias 격벽."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_for_database("default")
        assert bulkhead.name == "database:default"

    def test_get_for_database_replica(self):
        """replica DB alias 격벽."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_for_database("replica")
        assert bulkhead.name == "database:replica"

    def test_database_bulkheads_are_separate(self):
        """각 DB alias 격벽이 독립적."""
        registry = BulkheadRegistry()

        default_bh = registry.get_for_database("default")
        replica_bh = registry.get_for_database("replica")

        assert default_bh is not replica_bh
        assert default_bh.name != replica_bh.name


class TestBulkheadRegistryGetForCache:
    """캐시 인스턴스별 격벽 테스트."""

    def test_get_for_cache_default(self):
        """기본 캐시 인스턴스 격벽."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_for_cache("default")
        assert bulkhead.name == "cache:default"

    def test_get_for_cache_session(self):
        """세션 캐시 인스턴스 격벽."""
        registry = BulkheadRegistry()

        bulkhead = registry.get_for_cache("session")
        assert bulkhead.name == "cache:session"


class TestBulkheadRegistryRegisterUnregister:
    """등록/해제 테스트."""

    def test_register_custom_bulkhead(self):
        """커스텀 격벽 등록."""
        registry = BulkheadRegistry()
        custom_bh = SemaphoreBulkhead("my_custom", max_concurrent=25)

        registry.register(custom_bh)

        retrieved = registry.get("my_custom")
        assert retrieved is custom_bh

    def test_unregister_bulkhead(self):
        """격벽 등록 해제."""
        registry = BulkheadRegistry()
        custom_bh = SemaphoreBulkhead("to_remove", max_concurrent=5)
        registry.register(custom_bh)

        result = registry.unregister("to_remove")
        assert result is True

        with pytest.raises(KeyError):
            registry.get("to_remove")

    def test_unregister_nonexistent_returns_false(self):
        """존재하지 않는 격벽 해제 시 False."""
        registry = BulkheadRegistry()

        result = registry.unregister("nonexistent")
        assert result is False


class TestBulkheadRegistryGetAllStates:
    """전체 상태 조회 테스트."""

    def test_get_all_states(self):
        """모든 격벽 상태 반환."""
        registry = BulkheadRegistry()

        states = registry.get_all_states()

        assert "database" in states
        assert "cache" in states
        assert "external_api" in states
        assert "message_queue" in states

        db_state = states["database"]
        assert db_state.bulkhead_type == BulkheadType.SEMAPHORE


class TestBulkheadRegistrySingleton:
    """싱글톤 테스트."""

    def test_get_bulkhead_registry_returns_same_instance(self):
        """싱글톤 인스턴스 반환."""
        registry1 = get_bulkhead_registry()
        registry2 = get_bulkhead_registry()

        assert registry1 is registry2

    def test_reset_clears_singleton(self):
        """reset으로 싱글톤 초기화."""
        registry1 = get_bulkhead_registry()
        reset_bulkhead_registry()
        registry2 = get_bulkhead_registry()

        assert registry1 is not registry2


class TestBulkheadRegistryWithCustomSettings:
    """커스텀 설정으로 레지스트리 테스트."""

    def test_custom_settings_applied(self):
        """커스텀 설정이 적용됨."""
        settings = BulkheadSettings(
            database_max_concurrent=25,
            cache_max_concurrent=50,
        )
        registry = BulkheadRegistry(settings=settings)

        db_bh = registry.get(ConnectionType.DATABASE)
        cache_bh = registry.get(ConnectionType.CACHE)

        assert db_bh.get_state().max_concurrent == 25
        assert cache_bh.get_state().max_concurrent == 50

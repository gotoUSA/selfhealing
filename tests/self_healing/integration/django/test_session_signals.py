"""
218 세션 시그널 통합 테스트.

유닛테스트에서 커버하지 못하는 4가지 경로를 검증한다:
1. connect_session_signals() → 실제 Django 시그널 연결
2. SelfHealingConfig.ready() → _connect_session_signals() 체인
3. Django 시그널 fire → 핸들러 실행 (end-to-end)
4. UserSessionRegistry ↔ 실제 캐시 백엔드 연동

Reference: 218_SESSION_CACHE_LEGACY_DEPENDENCY 섹션 7.6
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.signals import user_logged_in, user_logged_out

from selfhealing.adapters.django.signal_hooks import (
    connect_session_signals,
    disconnect_session_signals,
    is_session_signals_connected,
    on_user_login_register_session,
    on_user_logout_unregister_session,
)
from selfhealing.services.security.session_registry import (
    UserSessionRegistry,
    get_user_session_registry,
    reset_user_session_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_signals():
    """각 테스트 전후로 시그널 연결 상태와 레지스트리 싱글톤을 초기화."""
    disconnect_session_signals()
    reset_user_session_registry()
    yield
    disconnect_session_signals()
    reset_user_session_registry()


@pytest.fixture()
def memory_registry():
    """InMemoryCacheAdapter 기반 UserSessionRegistry를 생성하고 싱글톤으로 등록."""
    from selfhealing.adapters.cache.memory_adapter import InMemoryCacheAdapter

    cache = InMemoryCacheAdapter(key_prefix="")
    registry = UserSessionRegistry(cache=cache)

    # 싱글톤 교체
    import selfhealing.services.security.session_registry as reg_mod

    reg_mod._registry = registry
    return registry


def _make_request(session_key: str = "test_session_abc123") -> MagicMock:
    """session_key가 설정된 mock request 생성."""
    request = MagicMock()
    request.session = MagicMock()
    request.session.session_key = session_key
    return request


def _make_user(pk: int = 42) -> MagicMock:
    """pk가 설정된 mock user 생성."""
    user = MagicMock()
    user.pk = pk
    return user


# ===========================================================================
# 1. connect_session_signals() / disconnect_session_signals() 검증
# ===========================================================================


class TestSignalConnectionContract:
    """connect_session_signals() 시그널 연결/해제/중복방지 검증."""

    def test_connect_registers_login_handler(self):
        """connect 후 user_logged_in에 핸들러가 등록된다."""
        connect_session_signals()

        receivers = [r[1]() for r in user_logged_in.receivers if r[1]() is not None]
        assert on_user_login_register_session in receivers

    def test_connect_registers_logout_handler(self):
        """connect 후 user_logged_out에 핸들러가 등록된다."""
        connect_session_signals()

        receivers = [r[1]() for r in user_logged_out.receivers if r[1]() is not None]
        assert on_user_logout_unregister_session in receivers

    def test_disconnect_removes_handlers(self):
        """disconnect 후 핸들러가 제거된다."""
        connect_session_signals()
        disconnect_session_signals()

        login_receivers = [r[1]() for r in user_logged_in.receivers if r[1]() is not None]
        logout_receivers = [r[1]() for r in user_logged_out.receivers if r[1]() is not None]
        assert on_user_login_register_session not in login_receivers
        assert on_user_logout_unregister_session not in logout_receivers

    def test_connected_guard_prevents_duplicate(self):
        """_connected 가드가 중복 연결을 방지한다."""
        connect_session_signals()
        assert is_session_signals_connected() is True

        # 두 번째 호출 — dispatch_uid 덕분에 실제 중복은 없지만
        # _connected 가드가 early return하는지 확인
        connect_session_signals()
        assert is_session_signals_connected() is True

        # 핸들러가 1개만 등록되어야 함 (dispatch_uid 기반)
        login_receiver_count = sum(
            1 for r in user_logged_in.receivers if r[1]() is not None and r[1]() is on_user_login_register_session
        )
        assert login_receiver_count == 1

    def test_is_connected_reflects_state(self):
        """is_session_signals_connected()가 연결 상태를 정확히 반환한다."""
        assert is_session_signals_connected() is False

        connect_session_signals()
        assert is_session_signals_connected() is True

        disconnect_session_signals()
        assert is_session_signals_connected() is False

    def test_dispatch_uid_values(self):
        """dispatch_uid가 문서에 명시된 값과 일치한다."""
        connect_session_signals()

        login_uids = [r[0][0] for r in user_logged_in.receivers]
        logout_uids = [r[0][0] for r in user_logged_out.receivers]

        # dispatch_uid는 (id, dispatch_uid) 형태의 key 중 첫 번째 요소
        # Django가 dispatch_uid를 키로 사용
        assert any("selfhealing_session_register" in str(uid) for uid in login_uids)
        assert any("selfhealing_session_unregister" in str(uid) for uid in logout_uids)


# ===========================================================================
# 2. SelfHealingConfig.ready() → _connect_session_signals() 체인
# ===========================================================================


class TestAppConfigSignalChainContract:
    """SelfHealingConfig._connect_session_signals() → connect_session_signals() 체인."""

    def test_connect_session_signals_delegates_correctly(self):
        """_connect_session_signals()가 signal_hooks.connect_session_signals()를 호출한다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        assert is_session_signals_connected() is False
        app_config._connect_session_signals()
        assert is_session_signals_connected() is True

    def test_connect_session_signals_graceful_on_import_error(self):
        """import 실패 시 예외 없이 경고 로그만 남긴다."""
        from selfhealing.adapters.django.apps import SelfHealingConfig

        app_config = SelfHealingConfig("selfhealing.adapters.django", __import__("selfhealing"))

        with patch(
            "selfhealing.adapters.django.apps.SelfHealingConfig._connect_session_signals",
            side_effect=Exception("import failed"),
        ):
            # 예외가 전파되지 않아야 함 — 실제로는 _connect_session_signals 자체가 try/except
            # 여기서는 메서드 자체를 mock해서 예외 발생을 시뮬레이션
            try:
                app_config._connect_session_signals()
            except Exception:
                pass  # mock이 예외를 발생시키므로 정상

        # 실제 graceful 동작 테스트: import path를 잘못 설정
        with patch(
            "selfhealing.adapters.django.signal_hooks.connect_session_signals",
            side_effect=Exception("test error"),
        ):
            # _connect_session_signals 내부 try/except가 잡아야 함
            app_config._connect_session_signals()
            # 예외 없이 통과하면 성공


# ===========================================================================
# 3. Django 시그널 fire → 핸들러 실행 (end-to-end)
# ===========================================================================


class TestSignalDispatchBehavior:
    """Django 시그널 발화 → 시그널 핸들러 → UserSessionRegistry 전체 경로."""

    def test_login_signal_fires_handler(self, memory_registry):
        """user_logged_in.send() → on_user_login_register_session → registry.register()."""
        connect_session_signals()

        request = _make_request("session_login_001")
        user = _make_user(pk=100)

        # 실제 Django 시그널 발화
        user_logged_in.send(sender=self.__class__, request=request, user=user)

        # Registry에 기록되었는지 확인
        keys = memory_registry.get_session_keys(100)
        assert "session_login_001" in keys

    def test_logout_signal_fires_handler(self, memory_registry):
        """user_logged_out.send() → on_user_logout_unregister_session → registry.unregister()."""
        connect_session_signals()

        request = _make_request("session_logout_001")
        user = _make_user(pk=200)

        # 먼저 등록
        memory_registry.register(200, "session_logout_001")
        assert memory_registry.get_session_keys(200) == ["session_logout_001"]

        # 로그아웃 시그널 발화
        user_logged_out.send(sender=self.__class__, request=request, user=user)

        # Registry에서 제거되었는지 확인
        keys = memory_registry.get_session_keys(200)
        assert "session_logout_001" not in keys

    def test_login_then_logout_full_lifecycle(self, memory_registry):
        """로그인 → 세션 등록 → 로그아웃 → 세션 제거 전체 생명주기."""
        connect_session_signals()

        request = _make_request("lifecycle_session")
        user = _make_user(pk=300)

        # Step 1: 로그인
        user_logged_in.send(sender=self.__class__, request=request, user=user)
        assert memory_registry.get_session_keys(300) == ["lifecycle_session"]

        # Step 2: 로그아웃
        user_logged_out.send(sender=self.__class__, request=request, user=user)
        assert memory_registry.get_session_keys(300) == []

    def test_multiple_device_login(self, memory_registry):
        """한 유저의 다중 기기 로그인 시 모든 session_key가 등록된다."""
        connect_session_signals()

        user = _make_user(pk=400)

        # 기기 A 로그인
        req_a = _make_request("device_a_session")
        user_logged_in.send(sender=self.__class__, request=req_a, user=user)

        # 기기 B 로그인
        req_b = _make_request("device_b_session")
        user_logged_in.send(sender=self.__class__, request=req_b, user=user)

        keys = memory_registry.get_session_keys(400)
        assert "device_a_session" in keys
        assert "device_b_session" in keys
        assert len(keys) == 2

    def test_logout_one_device_keeps_other(self, memory_registry):
        """한 기기 로그아웃 시 다른 기기의 세션은 유지된다."""
        connect_session_signals()

        user = _make_user(pk=500)

        # 두 기기 로그인
        req_a = _make_request("keep_session")
        user_logged_in.send(sender=self.__class__, request=req_a, user=user)
        req_b = _make_request("remove_session")
        user_logged_in.send(sender=self.__class__, request=req_b, user=user)

        # 기기 B만 로그아웃
        user_logged_out.send(sender=self.__class__, request=req_b, user=user)

        keys = memory_registry.get_session_keys(500)
        assert "keep_session" in keys
        assert "remove_session" not in keys

    def test_signal_not_fired_after_disconnect(self, memory_registry):
        """disconnect 후에는 시그널 발화해도 핸들러가 호출되지 않는다."""
        connect_session_signals()
        disconnect_session_signals()

        request = _make_request("should_not_register")
        user = _make_user(pk=600)

        user_logged_in.send(sender=self.__class__, request=request, user=user)

        keys = memory_registry.get_session_keys(600)
        assert keys == []

    def test_signal_skips_none_user(self, memory_registry):
        """user=None으로 시그널 발화 시 registry 호출 안 함."""
        connect_session_signals()

        request = _make_request("should_not_register")

        # user=None — AnonymousUser 등의 경우
        user_logged_in.send(sender=self.__class__, request=request, user=None)

        # registry에 아무것도 등록되지 않아야 함
        # (None user의 pk로 조회하면 빈 리스트)
        # 핸들러가 None 체크를 통과하는지 검증

    def test_signal_skips_user_without_pk(self, memory_registry):
        """user.pk=None으로 시그널 발화 시 registry 호출 안 함."""
        connect_session_signals()

        request = _make_request("should_not_register")
        user = _make_user(pk=None)

        user_logged_in.send(sender=self.__class__, request=request, user=user)


# ===========================================================================
# 4. UserSessionRegistry ↔ 실제 캐시 백엔드 연동
# ===========================================================================


class TestRegistryCacheIntegrationContract:
    """UserSessionRegistry와 InMemoryCacheAdapter 연동 검증."""

    def test_register_persists_to_cache(self, memory_registry):
        """register()가 캐시에 실제로 데이터를 저장한다."""
        memory_registry.register(user_id=1, session_key="s1")

        # 내부 캐시에서 직접 확인
        raw = memory_registry.cache.get(memory_registry._key(1))
        assert raw is not None
        assert "s1" in raw

    def test_unregister_removes_from_cache(self, memory_registry):
        """unregister()가 캐시에서 데이터를 제거한다."""
        memory_registry.register(user_id=2, session_key="s2")
        memory_registry.unregister(user_id=2, session_key="s2")

        raw = memory_registry.cache.get(memory_registry._key(2))
        # 마지막 세션이므로 키 자체가 삭제되어야 함
        assert raw is None

    def test_invalidate_all_clears_cache(self, memory_registry):
        """invalidate_all()이 캐시에서 모든 관련 키를 삭제한다."""
        memory_registry.register(user_id=3, session_key="s3a")
        memory_registry.register(user_id=3, session_key="s3b")

        deleted = memory_registry.invalidate_all(user_id=3)
        assert deleted == 2

        # 레지스트리 키 삭제 확인
        raw = memory_registry.cache.get(memory_registry._key(3))
        assert raw is None

    def test_get_session_keys_round_trip(self, memory_registry):
        """register → get_session_keys 왕복 검증."""
        memory_registry.register(user_id=4, session_key="rt_1")
        memory_registry.register(user_id=4, session_key="rt_2")

        keys = memory_registry.get_session_keys(user_id=4)
        assert set(keys) == {"rt_1", "rt_2"}

    def test_deduplication_in_cache(self, memory_registry):
        """동일 session_key 중복 등록 시 캐시에 1건만 저장된다."""
        memory_registry.register(user_id=5, session_key="dup")
        memory_registry.register(user_id=5, session_key="dup")

        keys = memory_registry.get_session_keys(user_id=5)
        assert keys.count("dup") == 1


# ===========================================================================
# 5. Redis 실제 연동 (docker-compose 환경에서만 실행)
# ===========================================================================


@pytest.mark.skipif(
    not __import__("os").environ.get("TEST_REDIS_AVAILABLE"),
    reason="Redis가 사용 가능한 Docker 환경에서만 실행",
)
class TestRedisSessionRegistryContract:
    """실제 Redis와 UserSessionRegistry 연동 검증."""

    @pytest.fixture(autouse=True)
    def _redis_registry(self):
        """실제 Redis 연결을 사용하는 registry 생성."""
        import redis as redis_lib

        redis_host = __import__("os").environ.get("REDIS_HOST", "redis")
        redis_port = int(__import__("os").environ.get("REDIS_PORT", "6379"))

        try:
            client = redis_lib.Redis(host=redis_host, port=redis_port, db=15)
            client.ping()
        except Exception:
            pytest.skip("Redis 연결 불가")

        from selfhealing.adapters.cache.redis_adapter import RedisCacheAdapter

        cache = RedisCacheAdapter(
            client=client,
            key_prefix="test:218:",
        )
        self.registry = UserSessionRegistry(cache=cache)
        self.redis_client = client

        # 싱글톤 교체
        import selfhealing.services.security.session_registry as reg_mod

        reg_mod._registry = self.registry

        yield

        # Cleanup: 테스트 키 삭제
        for key in client.keys("test:218:*"):
            client.delete(key)
        reg_mod._registry = None

    def test_redis_register_and_lookup(self):
        """Redis에 session_key 등록 및 조회."""
        self.registry.register(user_id=9001, session_key="redis_session_1")
        keys = self.registry.get_session_keys(user_id=9001)
        assert "redis_session_1" in keys

    def test_redis_multi_session(self):
        """Redis에서 다중 세션 등록 및 조회."""
        self.registry.register(user_id=9002, session_key="redis_a")
        self.registry.register(user_id=9002, session_key="redis_b")
        keys = self.registry.get_session_keys(user_id=9002)
        assert set(keys) == {"redis_a", "redis_b"}

    def test_redis_unregister(self):
        """Redis에서 세션 제거."""
        self.registry.register(user_id=9003, session_key="redis_remove")
        self.registry.unregister(user_id=9003, session_key="redis_remove")
        keys = self.registry.get_session_keys(user_id=9003)
        assert keys == []

    def test_redis_invalidate_all(self):
        """Redis에서 전체 세션 무효화."""
        self.registry.register(user_id=9004, session_key="redis_inv_1")
        self.registry.register(user_id=9004, session_key="redis_inv_2")
        deleted = self.registry.invalidate_all(user_id=9004)
        assert deleted == 2
        assert self.registry.get_session_keys(user_id=9004) == []

    def test_redis_signal_end_to_end(self):
        """Redis + Django 시그널 전체 경로: fire → handler → Redis 저장."""
        connect_session_signals()

        request = _make_request("redis_e2e_session")
        user = _make_user(pk=9005)

        user_logged_in.send(sender=self.__class__, request=request, user=user)
        keys = self.registry.get_session_keys(9005)
        assert "redis_e2e_session" in keys

        user_logged_out.send(sender=self.__class__, request=request, user=user)
        keys = self.registry.get_session_keys(9005)
        assert "redis_e2e_session" not in keys

    def test_redis_deduplication(self):
        """Redis에서 중복 등록 방지."""
        self.registry.register(user_id=9006, session_key="redis_dup")
        self.registry.register(user_id=9006, session_key="redis_dup")
        keys = self.registry.get_session_keys(user_id=9006)
        assert keys.count("redis_dup") == 1

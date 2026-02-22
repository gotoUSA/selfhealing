"""
X-Test Cross-Region Conflict 통합 테스트.

실제 Redis를 사용하여 AtomicStateQuery Lua 스크립트 동작을 검증합니다.
단위 테스트와 달리 Mock을 사용하지 않고 실제 Redis Lua 스크립트를 실행합니다.

검증 항목:
- Lua 스크립트 우선순위 로직 (Global STRICT > Regional)
- Race Condition 방지 (원자적 상태 조회)
- Admin Override 우선순위

Requirements:
- Docker Compose: docker-compose -f docker-compose.test.yml up -d
- Redis 연결 필수

Reference: docs/self_healing/middleware_system/144_XTEST_CROSS_REGION_SCENARIO.md
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

# Redis 필수 마커
pytestmark = pytest.mark.requires_redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()

from selfhealing.services.namespace_emergency.atomic_query import (
    AtomicStateQuery,
)


class TestAtomicStateQueryLuaScript:
    """
    AtomicStateQuery Lua 스크립트 통합 테스트.

    실제 Redis에서 Lua 스크립트가 올바르게 동작하는지 검증합니다.
    """

    @pytest.fixture
    def redis_client(self):
        """실제 Redis 클라이언트 (decode_responses=False)."""
        import redis

        # docker-compose.test.yml: 16379 포트
        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "16379")),
            db=0,
            decode_responses=False,  # Lua 스크립트는 bytes 반환
        )

        try:
            client.ping()
        except redis.ConnectionError:
            pytest.skip("Redis not available. Run: docker-compose -f docker-compose.test.yml up -d")

        yield client

        # Cleanup: 테스트 키 삭제
        for key in client.keys(b"selfhealing:*"):
            client.delete(key)

    @pytest.fixture
    def atomic_query(self, redis_client):
        """AtomicStateQuery 인스턴스."""
        return AtomicStateQuery(redis_client=redis_client)

    def _set_state(
        self,
        redis_client,
        namespace: str,
        governance_mode: str,
        emergency_level: int,
        is_global: bool = False,
    ):
        """Redis에 상태 저장."""
        if is_global:
            key = "selfhealing:governance:emergency_state"
            scope = "global"
        else:
            key = f"selfhealing:{namespace}:governance:emergency_state"
            scope = "regional"

        state = {
            "namespace": namespace if not is_global else "global",
            "scope": scope,
            "governance_mode": governance_mode,
            "emergency_level": emergency_level,
        }
        redis_client.set(key, json.dumps(state))

    def _clear_states(self, redis_client):
        """모든 상태 삭제."""
        for key in redis_client.keys(b"selfhealing:*"):
            redis_client.delete(key)

    # =========================================================================
    # 우선순위 로직 테스트 (Lua 스크립트 검증)
    # =========================================================================

    def test_lua_script_global_normal_regional_normal_returns_regional(self, redis_client, atomic_query):
        """
        Global NORMAL + Regional NORMAL → Regional 반환.

        둘 다 NORMAL이면 Regional 상태를 반환해야 합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "REGIONAL_DEFAULT"
        assert state["governance_mode"] == "NORMAL"
        assert state["namespace"] == "seoul" or state["scope"] == "regional"

    def test_lua_script_global_normal_regional_strict_returns_regional_strict(self, redis_client, atomic_query):
        """
        Global NORMAL + Regional STRICT → Regional STRICT.

        Regional만 STRICT면 Regional 상태를 반환해야 합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
        self._set_state(redis_client, "seoul", "STRICT", 2, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "REGIONAL_STRICT"
        assert state["governance_mode"] == "STRICT"

    def test_lua_script_global_strict_regional_normal_returns_global_strict(self, redis_client, atomic_query):
        """
        Global STRICT + Regional NORMAL → Global STRICT (오버라이드).

        Global STRICT는 모든 리전을 오버라이드합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "GLOBAL_OVERRIDE"
        assert state["governance_mode"] == "STRICT"
        assert state["scope"] == "global"

    def test_lua_script_global_strict_regional_strict_returns_global(self, redis_client, atomic_query):
        """
        Global STRICT + Regional STRICT → Global STRICT (더 넓은 범위).

        둘 다 STRICT면 Global 상태를 반환해야 합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)
        self._set_state(redis_client, "seoul", "STRICT", 2, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "GLOBAL_OVERRIDE"
        assert state["scope"] == "global"

    def test_lua_script_admin_override_ignores_global_strict(self, redis_client, atomic_query):
        """
        Admin Override 시 Global STRICT 무시.

        precedence=ADMIN_OVERRIDE면 Regional 상태를 반환해야 합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul", precedence="ADMIN_OVERRIDE")

        assert decision_type == "ADMIN_OVERRIDE"
        assert state["governance_mode"] == "NORMAL"

    # =========================================================================
    # Race Condition 테스트 (원자적 실행 검증)
    # =========================================================================

    def test_concurrent_state_queries_are_atomic(self, redis_client, atomic_query):
        """
        동시 상태 조회가 원자적으로 실행되는지 검증.

        여러 스레드가 동시에 상태를 조회해도 일관된 결과를 반환해야 합니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)

        results = []
        errors = []

        def query_state():
            try:
                state, decision_type, _ = atomic_query.query_effective_state("seoul")
                results.append((state["governance_mode"], decision_type))
            except Exception as e:
                errors.append(str(e))

        # 10개 스레드로 동시 조회
        threads = [threading.Thread(target=query_state) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10

        # 모든 결과가 동일해야 함 (원자적 실행)
        for mode, decision in results:
            assert mode == "STRICT"
            assert decision == "GLOBAL_OVERRIDE"

    def test_state_change_during_concurrent_queries(self, redis_client, atomic_query):
        """
        상태 변경 중 동시 조회 시 일관성 유지.

        Lua 스크립트는 원자적이므로 중간 상태를 반환하지 않습니다.
        """
        self._clear_states(redis_client)
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)

        results = []
        change_count = [0]

        def query_and_check():
            state, decision_type, _ = atomic_query.query_effective_state("seoul")
            mode = state["governance_mode"]
            results.append(mode)
            return mode

        def change_state():
            for i in range(5):
                time.sleep(0.01)
                if i % 2 == 0:
                    self._set_state(redis_client, "global", "STRICT", 3, is_global=True)
                else:
                    self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
                change_count[0] += 1

        # 상태 변경과 조회를 동시에 실행
        with ThreadPoolExecutor(max_workers=5) as executor:
            change_future = executor.submit(change_state)
            query_futures = [executor.submit(query_and_check) for _ in range(20)]

            change_future.result()
            for f in as_completed(query_futures):
                f.result()

        # 모든 결과가 STRICT 또는 NORMAL (중간 상태 없음)
        for mode in results:
            assert mode in ("STRICT", "NORMAL"), f"Unexpected mode: {mode}"

    # =========================================================================
    # 키 없음 (기본값) 테스트
    # =========================================================================

    def test_lua_script_no_global_key_uses_default(self, redis_client, atomic_query):
        """
        Global 키 없을 때 기본값 사용.

        Global 상태가 없으면 NORMAL로 간주합니다.
        """
        self._clear_states(redis_client)
        # Global 키 없음, Regional만 설정
        self._set_state(redis_client, "seoul", "STRICT", 2, is_global=False)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "REGIONAL_STRICT"
        assert state["governance_mode"] == "STRICT"

    def test_lua_script_no_regional_key_uses_default(self, redis_client, atomic_query):
        """
        Regional 키 없을 때 기본값 사용.

        Regional 상태가 없으면 NORMAL로 간주합니다.
        """
        self._clear_states(redis_client)
        # Regional 키 없음, Global만 설정
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "GLOBAL_OVERRIDE"
        assert state["governance_mode"] == "STRICT"

    def test_lua_script_no_keys_uses_default(self, redis_client, atomic_query):
        """
        키 없을 때 모두 기본값 사용.

        둘 다 없으면 Regional NORMAL 기본값을 반환합니다.
        """
        self._clear_states(redis_client)

        state, decision_type, reason = atomic_query.query_effective_state("seoul")

        assert decision_type == "REGIONAL_DEFAULT"
        assert state["governance_mode"] == "NORMAL"


class TestFullScenarioWithRealRedis:
    """
    144 문서 8단계 시나리오 전체 통합 테스트.

    실제 Redis를 사용하여 시나리오를 실행합니다.
    """

    @pytest.fixture
    def redis_client(self):
        """실제 Redis 클라이언트."""
        import redis

        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "16379")),
            db=0,
            decode_responses=False,
        )

        try:
            client.ping()
        except redis.ConnectionError:
            pytest.skip("Redis not available. Run: docker-compose -f docker-compose.test.yml up -d")

        yield client

        # Cleanup
        for key in client.keys(b"selfhealing:*"):
            client.delete(key)

    @pytest.fixture
    def atomic_query(self, redis_client):
        """AtomicStateQuery 인스턴스."""
        return AtomicStateQuery(redis_client=redis_client)

    def _set_state(
        self,
        redis_client,
        namespace: str,
        governance_mode: str,
        emergency_level: int,
        is_global: bool = False,
    ):
        """Redis에 상태 저장."""
        if is_global:
            key = "selfhealing:governance:emergency_state"
            scope = "global"
        else:
            key = f"selfhealing:{namespace}:governance:emergency_state"
            scope = "regional"

        state = {
            "namespace": namespace if not is_global else "global",
            "scope": scope,
            "governance_mode": governance_mode,
            "emergency_level": emergency_level,
        }
        redis_client.set(key, json.dumps(state))

    def test_full_8_step_scenario_with_real_redis(self, redis_client, atomic_query):
        """
        144 문서 8단계 시나리오 전체 실행.

        Step 1: 초기 상태 확인 (Global: NORMAL, Regional: NORMAL)
        Step 2: Regional STRICT 설정
        Step 3: get_effective_state() → Regional STRICT
        Step 4: Global STRICT 설정
        Step 5: get_effective_state() → Global STRICT (오버라이드)
        Step 6: Admin Override 설정
        Step 7: get_effective_state() → Regional NORMAL (Admin 승리)
        Step 8: 상태 원복
        """
        target_region = "seoul"

        # Step 1: 초기 상태 설정
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
        self._set_state(redis_client, target_region, "NORMAL", 0, is_global=False)

        state, decision_type, _ = atomic_query.query_effective_state(target_region)
        assert state["governance_mode"] == "NORMAL"
        assert decision_type == "REGIONAL_DEFAULT"

        # Step 2: Regional STRICT 설정
        self._set_state(redis_client, target_region, "STRICT", 2, is_global=False)

        # Step 3: Regional STRICT 확인
        state, decision_type, _ = atomic_query.query_effective_state(target_region)
        assert state["governance_mode"] == "STRICT"
        assert decision_type == "REGIONAL_STRICT"

        # Step 4: Global STRICT 설정
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)

        # Step 5: Global STRICT 오버라이드 확인
        state, decision_type, _ = atomic_query.query_effective_state(target_region)
        assert state["governance_mode"] == "STRICT"
        assert decision_type == "GLOBAL_OVERRIDE"
        assert state["scope"] == "global"

        # Step 6: Regional을 NORMAL로 변경 (Admin Override 준비)
        self._set_state(redis_client, target_region, "NORMAL", 0, is_global=False)

        # Step 7: Admin Override로 Regional 우선
        state, decision_type, _ = atomic_query.query_effective_state(target_region, precedence="ADMIN_OVERRIDE")
        assert state["governance_mode"] == "NORMAL"
        assert decision_type == "ADMIN_OVERRIDE"

        # Step 8: 상태 원복
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)
        self._set_state(redis_client, target_region, "NORMAL", 0, is_global=False)

        state, decision_type, _ = atomic_query.query_effective_state(target_region)
        assert state["governance_mode"] == "NORMAL"
        assert decision_type == "REGIONAL_DEFAULT"


class TestMultiRegionIsolation:
    """
    다중 리전 격리 통합 테스트.

    특정 리전만 격리되고 다른 리전은 정상인지 검증합니다.
    """

    @pytest.fixture
    def redis_client(self):
        """실제 Redis 클라이언트."""
        import redis

        client = redis.Redis(
            host=os.environ.get("REDIS_HOST", "localhost"),
            port=int(os.environ.get("REDIS_PORT", "16379")),
            db=0,
            decode_responses=False,
        )

        try:
            client.ping()
        except redis.ConnectionError:
            pytest.skip("Redis not available. Run: docker-compose -f docker-compose.test.yml up -d")

        yield client

        # Cleanup
        for key in client.keys(b"selfhealing:*"):
            client.delete(key)

    @pytest.fixture
    def atomic_query(self, redis_client):
        """AtomicStateQuery 인스턴스."""
        return AtomicStateQuery(redis_client=redis_client)

    def _set_state(
        self,
        redis_client,
        namespace: str,
        governance_mode: str,
        emergency_level: int,
        is_global: bool = False,
    ):
        """Redis에 상태 저장."""
        if is_global:
            key = "selfhealing:governance:emergency_state"
            scope = "global"
        else:
            key = f"selfhealing:{namespace}:governance:emergency_state"
            scope = "regional"

        state = {
            "namespace": namespace if not is_global else "global",
            "scope": scope,
            "governance_mode": governance_mode,
            "emergency_level": emergency_level,
        }
        redis_client.set(key, json.dumps(state))

    def test_only_target_region_is_isolated(self, redis_client, atomic_query):
        """
        타겟 리전만 격리되고 다른 리전은 정상.

        seoul을 STRICT로 격리해도 tokyo는 NORMAL 유지.
        """
        # Global NORMAL
        self._set_state(redis_client, "global", "NORMAL", 0, is_global=True)

        # Seoul STRICT, Tokyo NORMAL
        self._set_state(redis_client, "seoul", "STRICT", 2, is_global=False)
        self._set_state(redis_client, "tokyo", "NORMAL", 0, is_global=False)

        # Seoul 확인: STRICT
        seoul_state, seoul_decision, _ = atomic_query.query_effective_state("seoul")
        assert seoul_state["governance_mode"] == "STRICT"
        assert seoul_decision == "REGIONAL_STRICT"

        # Tokyo 확인: NORMAL
        tokyo_state, tokyo_decision, _ = atomic_query.query_effective_state("tokyo")
        assert tokyo_state["governance_mode"] == "NORMAL"
        assert tokyo_decision == "REGIONAL_DEFAULT"

    def test_global_strict_affects_all_regions(self, redis_client, atomic_query):
        """
        Global STRICT는 모든 리전에 영향.

        Global STRICT면 seoul, tokyo 모두 STRICT.
        """
        # Global STRICT
        self._set_state(redis_client, "global", "STRICT", 3, is_global=True)

        # Regional 모두 NORMAL
        self._set_state(redis_client, "seoul", "NORMAL", 0, is_global=False)
        self._set_state(redis_client, "tokyo", "NORMAL", 0, is_global=False)
        self._set_state(redis_client, "oregon", "NORMAL", 0, is_global=False)

        # 모든 리전 확인: STRICT
        for region in ["seoul", "tokyo", "oregon"]:
            state, decision, _ = atomic_query.query_effective_state(region)
            assert state["governance_mode"] == "STRICT"
            assert decision == "GLOBAL_OVERRIDE"

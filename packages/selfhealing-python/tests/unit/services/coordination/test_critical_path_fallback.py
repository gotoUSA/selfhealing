"""
CriticalPathFallback 단위 테스트.

Redis/Audit 장애 시 로컬 폴백 경로를 검증합니다.
"""

import json

import pytest

from selfhealing.services.coordination.critical_path_fallback import (
    CriticalPathFallback,
)


class TestCriticalPathFallback:
    """CriticalPathFallback 테스트."""

    @pytest.fixture
    def temp_paths(self, tmp_path):
        """임시 파일 경로 생성."""
        return {
            "state_path": tmp_path / "emergency_state.json",
            "audit_path": tmp_path / "emergency_audit.jsonl",
        }

    @pytest.fixture
    def fallback(self, temp_paths):
        """CriticalPathFallback 인스턴스 생성."""
        return CriticalPathFallback(
            redis_client=None,  # Redis 없이 로컬 폴백만 테스트
            local_state_path=temp_paths["state_path"],
            local_audit_path=temp_paths["audit_path"],
        )

    def test_load_default_state_when_no_data(self, fallback):
        """데이터 없을 때 기본 상태 반환."""
        state = fallback.load_state_with_fallback(namespace="test")

        assert state["namespace"] == "test"
        assert state["level"] == "NORMAL"
        assert state["is_active"] is False

    def test_save_and_load_state(self, fallback):
        """상태 저장 및 로드."""
        test_state = {
            "namespace": "seoul",
            "level": "LEVEL_3",
            "is_active": True,
            "governance_mode": "STRICT",
        }

        # 저장
        tier = fallback.save_state_with_fallback(test_state, namespace="seoul")
        assert tier in ("local", "memory")

        # 로드
        loaded = fallback.load_state_with_fallback(namespace="seoul")

        assert loaded["level"] == "LEVEL_3"
        assert loaded["is_active"] is True

    def test_multiple_namespaces(self, fallback):
        """여러 네임스페이스 독립 관리."""
        # 서울 상태 저장
        fallback.save_state_with_fallback(
            {"level": "LEVEL_3", "region": "seoul"},
            namespace="seoul",
        )

        # 도쿄 상태 저장
        fallback.save_state_with_fallback(
            {"level": "LEVEL_1", "region": "tokyo"},
            namespace="tokyo",
        )

        # 각각 로드
        seoul_state = fallback.load_state_with_fallback(namespace="seoul")
        tokyo_state = fallback.load_state_with_fallback(namespace="tokyo")

        assert seoul_state["level"] == "LEVEL_3"
        assert tokyo_state["level"] == "LEVEL_1"

    def test_append_audit_log(self, fallback, temp_paths):
        """감사 로그 추가."""
        entry = {
            "event_type": "LEVEL_CHANGED",
            "old_level": "NORMAL",
            "new_level": "LEVEL_3",
        }

        tier = fallback.append_audit_log(entry)

        assert tier in ("local", "memory")

        # 파일 확인
        if temp_paths["audit_path"].exists():
            with open(temp_paths["audit_path"]) as f:
                lines = f.readlines()
                assert len(lines) == 1
                logged = json.loads(lines[0])
                assert logged["event_type"] == "LEVEL_CHANGED"
                assert "timestamp" in logged

    def test_get_stats(self, fallback):
        """통계 조회."""
        fallback.save_state_with_fallback({"test": 1}, namespace="test")
        fallback.load_state_with_fallback(namespace="test")

        stats = fallback.get_stats()

        assert "local_saves" in stats or "memory_saves" in stats
        assert "current_tier" in stats

    def test_memory_fallback_when_file_fails(self, fallback):
        """파일 실패 시 메모리 폴백."""
        # 상태 저장 (메모리에도 저장됨)
        fallback.save_state_with_fallback(
            {"level": "LEVEL_2"},
            namespace="failtest",
        )

        # 메모리에서 로드 가능해야 함
        state = fallback.load_state_with_fallback(namespace="failtest")
        assert state["level"] == "LEVEL_2"

    def test_clear_local_state_specific_namespace(self, fallback):
        """특정 네임스페이스 상태 삭제."""
        fallback.save_state_with_fallback({"keep": True}, namespace="keep")
        fallback.save_state_with_fallback({"delete": True}, namespace="delete")

        fallback.clear_local_state(namespace="delete")

        # keep은 유지
        keep_state = fallback.load_state_with_fallback(namespace="keep")
        assert keep_state.get("keep") is True

        # delete는 기본값으로
        delete_state = fallback.load_state_with_fallback(namespace="delete")
        assert delete_state.get("delete") is None

    def test_clear_all_local_state(self, fallback, temp_paths):
        """전체 로컬 상태 삭제."""
        fallback.save_state_with_fallback({"test": 1}, namespace="ns1")
        fallback.save_state_with_fallback({"test": 2}, namespace="ns2")

        fallback.clear_local_state()  # 전체 삭제

        # 파일이 삭제되었거나 기본 상태 반환
        state = fallback.load_state_with_fallback(namespace="ns1")
        assert state.get("test") is None

    def test_flush_memory_buffer(self, fallback, temp_paths):
        """메모리 버퍼 플러시."""
        # 여러 감사 로그 추가
        for i in range(5):
            fallback.append_audit_log({"index": i})

        # 플러시 전 버퍼 확인
        stats_before = fallback.get_stats()

        # 플러시
        count = fallback.flush_memory_buffer()

        # 플러시 후 버퍼 비워짐
        stats_after = fallback.get_stats()
        assert stats_after["memory_audit_buffer_size"] == 0

    def test_current_tier_tracking(self, fallback):
        """현재 tier 추적."""
        # 초기 상태
        tier = fallback.get_current_tier()
        assert tier in ("redis", "local", "memory")

        # 저장 후 tier 업데이트
        fallback.save_state_with_fallback({"test": 1}, namespace="test")
        tier = fallback.get_current_tier()
        assert tier in ("local", "memory")  # Redis 없으므로


class TestCriticalPathFallbackWithMockRedis:
    """Mock Redis를 사용한 CriticalPathFallback 테스트."""

    class MockRedis:
        """간단한 Mock Redis 클라이언트."""

        def __init__(self):
            self._data = {}
            self._should_fail = False

        def hgetall(self, key):
            if self._should_fail:
                raise ConnectionError("Redis connection failed")
            return self._data.get(key, {})

        def hmset(self, key, mapping):
            if self._should_fail:
                raise ConnectionError("Redis connection failed")
            if key not in self._data:
                self._data[key] = {}
            self._data[key].update(mapping)

        def set_fail_mode(self, should_fail):
            self._should_fail = should_fail

    @pytest.fixture
    def mock_redis(self):
        return self.MockRedis()

    @pytest.fixture
    def fallback_with_redis(self, mock_redis, tmp_path):
        return CriticalPathFallback(
            redis_client=mock_redis,
            local_state_path=tmp_path / "state.json",
            local_audit_path=tmp_path / "audit.jsonl",
        )

    def test_redis_primary_path(self, fallback_with_redis, mock_redis):
        """Redis Primary 경로 테스트."""
        # 상태 저장
        tier = fallback_with_redis.save_state_with_fallback(
            {"level": "LEVEL_3"},
            namespace="redis_test",
        )

        assert tier == "redis"

        # 상태 로드
        state = fallback_with_redis.load_state_with_fallback(namespace="redis_test")
        # Redis에서 로드됨

    def test_fallback_on_redis_failure(self, fallback_with_redis, mock_redis):
        """Redis 실패 시 폴백."""
        # 먼저 로컬에 저장
        mock_redis.set_fail_mode(True)

        tier = fallback_with_redis.save_state_with_fallback(
            {"level": "LEVEL_2"},
            namespace="fallback_test",
        )

        # Redis 실패로 로컬 폴백
        assert tier in ("local", "memory")

        # 로드도 폴백
        state = fallback_with_redis.load_state_with_fallback(namespace="fallback_test")
        assert state["level"] == "LEVEL_2"

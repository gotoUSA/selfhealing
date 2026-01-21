"""
AtomicLevelTransition 단위 테스트.

Lua 스크립트 기반 원자적 레벨 전환을 검증합니다.
Redis Mock을 사용한 순수 단위 테스트입니다.
"""

import pytest

from selfhealing.services.coordination.atomic_transition import (
    AtomicLevelTransition,
    ATOMIC_TRANSITION_SCRIPT,
    CONDITIONAL_TRANSITION_SCRIPT,
    ESCALATE_ONLY_SCRIPT,
)


class MockRedis:
    """
    Lua eval을 시뮬레이션하는 Mock Redis.
    
    실제 Lua 스크립트 실행 대신 Python으로 동일 로직 구현.
    """
    
    def __init__(self):
        self._data = {}
    
    def hgetall(self, key):
        return self._data.get(key, {})
    
    def hmset(self, key, mapping):
        if key not in self._data:
            self._data[key] = {}
        self._data[key].update(mapping)
    
    def hget(self, key, field):
        data = self._data.get(key, {})
        return data.get(field)
    
    def eval(self, script, numkeys, *args):
        """Lua 스크립트 시뮬레이션."""
        key = args[0]
        
        if script == ATOMIC_TRANSITION_SCRIPT:
            return self._eval_atomic_transition(key, *args[1:])
        elif script == CONDITIONAL_TRANSITION_SCRIPT:
            return self._eval_conditional_transition(key, *args[1:])
        elif script == ESCALATE_ONLY_SCRIPT:
            return self._eval_escalate_only(key, *args[1:])
        else:
            raise ValueError("Unknown script")
    
    def _eval_atomic_transition(self, key, expected_level, new_level, new_mode, causation_id, updated_at):
        """ATOMIC_TRANSITION_SCRIPT 시뮬레이션."""
        current_level = self.hget(key, "level")
        
        # Optimistic Lock 확인
        if current_level and current_level != expected_level:
            return [0, "level_mismatch", current_level]
        
        # 상태 업데이트
        self.hmset(key, {
            "level": new_level,
            "governance_mode": new_mode,
            "causation_id": causation_id,
            "updated_at": updated_at,
        })
        
        return [1, "success", new_level]
    
    def _eval_conditional_transition(self, key, required_level, new_level, new_mode, causation_id, updated_at, reason):
        """CONDITIONAL_TRANSITION_SCRIPT 시뮬레이션."""
        current_level = self.hget(key, "level")
        
        # 조건 확인
        if current_level and current_level != required_level:
            return [0, "condition_not_met", current_level]
        
        # 상태 업데이트
        self.hmset(key, {
            "level": new_level,
            "governance_mode": new_mode,
            "causation_id": causation_id,
            "updated_at": updated_at,
            "reason": reason,
        })
        
        return [1, "success", new_level]
    
    def _eval_escalate_only(self, key, new_value_str, new_level, new_mode, causation_id, updated_at):
        """ESCALATE_ONLY_SCRIPT 시뮬레이션."""
        level_map = {
            "NORMAL": 0,
            "LEVEL_1": 1,
            "LEVEL_2": 2,
            "LEVEL_3": 3,
        }
        
        current_level_str = self.hget(key, "level")
        current_value = level_map.get(current_level_str, 0)
        new_value = int(new_value_str)
        
        # 상승만 허용
        if new_value <= current_value:
            return [0, "level_not_escalating", current_level_str or "NORMAL"]
        
        # 상태 업데이트
        self.hmset(key, {
            "level": new_level,
            "governance_mode": new_mode,
            "causation_id": causation_id,
            "updated_at": updated_at,
        })
        
        return [1, "success", new_level]


class TestAtomicLevelTransition:
    """AtomicLevelTransition 테스트."""
    
    @pytest.fixture
    def mock_redis(self):
        return MockRedis()
    
    @pytest.fixture
    def transition(self, mock_redis):
        return AtomicLevelTransition(
            redis_client=mock_redis,
            key_prefix="test",
        )
    
    def test_transition_success(self, transition, mock_redis):
        """정상 전환 성공."""
        success, message, level = transition.transition(
            namespace="seoul",
            expected_level="NORMAL",  # 초기 상태 없음
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="trigger-001",
        )
        
        assert success is True
        assert message == "success"
        assert level == "LEVEL_3"
        
        # 상태 확인
        state = transition.get_current_state("seoul")
        assert state["level"] == "LEVEL_3"
        assert state["governance_mode"] == "STRICT"
    
    def test_transition_fail_level_mismatch(self, transition, mock_redis):
        """레벨 불일치 시 실패."""
        # 먼저 LEVEL_1로 설정
        transition.transition(
            namespace="seoul",
            expected_level="NORMAL",
            new_level="LEVEL_1",
            new_mode="NORMAL",
            causation_id="init",
        )
        
        # NORMAL → LEVEL_3 시도 (실제는 LEVEL_1이므로 실패)
        success, message, level = transition.transition(
            namespace="seoul",
            expected_level="NORMAL",  # 잘못된 예상
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="trigger-002",
        )
        
        assert success is False
        assert message == "level_mismatch"
        assert level == "LEVEL_1"
    
    def test_conditional_transition_success(self, transition, mock_redis):
        """조건부 전환 성공."""
        # LEVEL_3로 설정
        transition.transition(
            namespace="seoul",
            expected_level="NORMAL",
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="escalate",
        )
        
        # LEVEL_3 → NORMAL 조건부 전환
        success, message, level = transition.transition_conditional(
            namespace="seoul",
            required_level="LEVEL_3",
            new_level="NORMAL",
            new_mode="NORMAL",
            causation_id="recovery",
            reason="Error rate stabilized",
        )
        
        assert success is True
        assert level == "NORMAL"
    
    def test_conditional_transition_fail_condition_not_met(self, transition, mock_redis):
        """조건 불충족 시 조건부 전환 실패."""
        # LEVEL_2로 설정
        transition.transition(
            namespace="seoul",
            expected_level="NORMAL",
            new_level="LEVEL_2",
            new_mode="NORMAL",
            causation_id="init",
        )
        
        # LEVEL_3일 때만 NORMAL로 전환 시도
        success, message, level = transition.transition_conditional(
            namespace="seoul",
            required_level="LEVEL_3",  # 실제는 LEVEL_2
            new_level="NORMAL",
            new_mode="NORMAL",
            causation_id="recovery",
        )
        
        assert success is False
        assert message == "condition_not_met"
        assert level == "LEVEL_2"
    
    def test_escalate_only_success(self, transition, mock_redis):
        """상승 전용 전환 성공."""
        success, message, level = transition.escalate_only(
            namespace="seoul",
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="emergency",
        )
        
        assert success is True
        assert level == "LEVEL_3"
    
    def test_escalate_only_fail_not_escalating(self, transition, mock_redis):
        """하락 시 상승 전용 전환 실패."""
        # LEVEL_3로 설정
        transition.escalate_only(
            namespace="seoul",
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="escalate",
        )
        
        # LEVEL_2로 하락 시도
        success, message, level = transition.escalate_only(
            namespace="seoul",
            new_level="LEVEL_2",
            new_mode="NORMAL",
            causation_id="downgrade",
        )
        
        assert success is False
        assert message == "level_not_escalating"
        assert level == "LEVEL_3"
    
    def test_escalate_same_level_fail(self, transition, mock_redis):
        """동일 레벨로 상승 시도 실패."""
        # LEVEL_2로 설정
        transition.escalate_only(
            namespace="seoul",
            new_level="LEVEL_2",
            new_mode="NORMAL",
            causation_id="init",
        )
        
        # 동일 레벨로 시도
        success, message, level = transition.escalate_only(
            namespace="seoul",
            new_level="LEVEL_2",
            new_mode="STRICT",
            causation_id="same",
        )
        
        assert success is False
    
    def test_get_current_state(self, transition, mock_redis):
        """현재 상태 조회."""
        transition.transition(
            namespace="tokyo",
            expected_level="NORMAL",
            new_level="LEVEL_1",
            new_mode="NORMAL",
            causation_id="test",
        )
        
        state = transition.get_current_state("tokyo")
        
        assert state is not None
        assert state["level"] == "LEVEL_1"
        assert state["causation_id"] == "test"
    
    def test_get_current_state_not_exists(self, transition, mock_redis):
        """존재하지 않는 상태 조회."""
        state = transition.get_current_state("nonexistent")
        
        assert state is None
    
    def test_multiple_namespaces_isolated(self, transition, mock_redis):
        """여러 네임스페이스 격리."""
        transition.transition(
            namespace="seoul",
            expected_level="NORMAL",
            new_level="LEVEL_3",
            new_mode="STRICT",
            causation_id="seoul-trigger",
        )
        
        transition.transition(
            namespace="tokyo",
            expected_level="NORMAL",
            new_level="LEVEL_1",
            new_mode="NORMAL",
            causation_id="tokyo-trigger",
        )
        
        seoul_state = transition.get_current_state("seoul")
        tokyo_state = transition.get_current_state("tokyo")
        
        assert seoul_state["level"] == "LEVEL_3"
        assert tokyo_state["level"] == "LEVEL_1"

"""
ParameterBlacklist 및 LearningService 확장 테스트

순위 5.5, 5.7 구현 테스트:
- ParameterBlacklist (Escape Strategy - 위험 파라미터 블랙리스트)
- StateBackend 기반 영속성
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.services.learning.models import (
    BlacklistedParameter,
    BlacklistReason,
)
from selfhealing.services.learning.service import (
    LearningService,
    ParameterBlacklist,
)

# =============================================================================
# BlacklistedParameter Model Tests
# =============================================================================


class TestBlacklistedParameterModel:
    """BlacklistedParameter 모델 테스트."""

    def test_to_dict(self):
        """
        Purpose:
            to_dict가 올바르게 직렬화하는지 확인.
        """
        entry = BlacklistedParameter(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1", "0.2"},
            reason=BlacklistReason.RECOVERY_LOOP,
            registered_at=datetime(2026, 1, 8, 10, 0, 0),
            registered_by="system",
            incident_id=123,
            expires_at=datetime(2026, 1, 15, 10, 0, 0),
        )

        result = entry.to_dict()

        assert result["module"] == "circuit_breaker"
        assert result["parameter"] == "threshold"
        assert set(result["blocked_values"]) == {"0.1", "0.2"}
        assert result["reason"] == "recovery_loop"
        assert result["registered_by"] == "system"
        assert result["incident_id"] == 123
        assert "2026-01-08" in result["registered_at"]
        assert "2026-01-15" in result["expires_at"]

    def test_from_dict(self):
        """
        Purpose:
            from_dict가 올바르게 역직렬화하는지 확인.
        """
        data = {
            "module": "retry",
            "parameter": "max_attempts",
            "blocked_values": ["5", "10"],
            "reason": "flapping",
            "registered_at": "2026-01-08T10:00:00",
            "registered_by": "admin",
            "incident_id": 456,
            "expires_at": "2026-01-15T10:00:00",
        }

        entry = BlacklistedParameter.from_dict(data)

        assert entry.module == "retry"
        assert entry.parameter == "max_attempts"
        assert entry.blocked_values == {"5", "10"}
        assert entry.reason == BlacklistReason.FLAPPING
        assert entry.registered_by == "admin"
        assert entry.incident_id == 456
        assert entry.expires_at is not None


# =============================================================================
# ParameterBlacklist Tests (순위 5.5)
# =============================================================================


class TestParameterBlacklist:
    """ParameterBlacklist 테스트."""

    @pytest.fixture
    def blacklist(self):
        """ParameterBlacklist 인스턴스 (메모리 모드)."""
        bl = ParameterBlacklist()
        bl._backend = None  # StateBackend 비활성화
        yield bl
        bl._blacklist.clear()

    def test_register_parameter(self, blacklist):
        """
        Purpose:
            파라미터 등록이 올바르게 동작하는지 확인.
        """
        entry = blacklist.register(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )

        assert entry.module == "circuit_breaker"
        assert entry.parameter == "threshold"
        assert entry.blocked_values == {"0.1"}
        assert entry.reason == BlacklistReason.RECOVERY_LOOP

    def test_is_blocked_returns_true(self, blacklist):
        """
        Purpose:
            블랙리스트된 값이 차단되는지 확인.
        """
        blacklist.register(
            module="retry",
            parameter="max_attempts",
            blocked_values={"100", "50"},
            reason=BlacklistReason.FLAPPING,
        )

        is_blocked, entry = blacklist.is_blocked("retry", "max_attempts", "100")

        assert is_blocked is True
        assert entry is not None
        assert entry.reason == BlacklistReason.FLAPPING

    def test_is_blocked_returns_false_for_allowed_value(self, blacklist):
        """
        Purpose:
            차단되지 않은 값은 허용되는지 확인.
        """
        blacklist.register(
            module="retry",
            parameter="max_attempts",
            blocked_values={"100"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )

        is_blocked, entry = blacklist.is_blocked("retry", "max_attempts", "5")

        assert is_blocked is False
        assert entry is None

    def test_is_blocked_returns_false_for_unknown_param(self, blacklist):
        """
        Purpose:
            등록되지 않은 파라미터는 허용되는지 확인.
        """
        is_blocked, entry = blacklist.is_blocked("unknown", "param", "any")

        assert is_blocked is False
        assert entry is None

    def test_expired_entry_is_not_blocked(self, blacklist):
        """
        Purpose:
            만료된 항목은 차단하지 않는지 확인.
        """
        # 과거 만료 시간으로 직접 등록 (timezone-aware)
        entry = BlacklistedParameter(
            module="test",
            parameter="param",
            blocked_values={"blocked"},
            reason=BlacklistReason.MANUAL_BLOCK,
            registered_at=datetime.now(timezone.utc) - timedelta(days=10),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        blacklist._blacklist["test:param"] = entry

        is_blocked, _ = blacklist.is_blocked("test", "param", "blocked")

        assert is_blocked is False

    def test_get_all_removes_expired(self, blacklist):
        """
        Purpose:
            get_all이 만료된 항목을 제거하는지 확인.
        """
        # 유효한 항목
        blacklist.register(
            module="valid",
            parameter="param",
            blocked_values={"value"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )

        # 만료된 항목 (timezone-aware)
        expired_entry = BlacklistedParameter(
            module="expired",
            parameter="param",
            blocked_values={"value"},
            reason=BlacklistReason.FLAPPING,
            registered_at=datetime.now(timezone.utc) - timedelta(days=10),
            expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        blacklist._blacklist["expired:param"] = expired_entry

        result = blacklist.get_all()

        assert len(result) == 1
        assert result[0].module == "valid"

    def test_unregister(self, blacklist):
        """
        Purpose:
            unregister가 항목을 제거하는지 확인.
        """
        blacklist.register(
            module="test",
            parameter="param",
            blocked_values={"value"},
            reason=BlacklistReason.MANUAL_BLOCK,
        )

        result = blacklist.unregister("test", "param")

        assert result is True
        is_blocked, _ = blacklist.is_blocked("test", "param", "value")
        assert is_blocked is False

    def test_unregister_nonexistent_returns_false(self, blacklist):
        """
        Purpose:
            존재하지 않는 항목 unregister가 False를 반환하는지 확인.
        """
        result = blacklist.unregister("nonexistent", "param")
        assert result is False


# =============================================================================
# ParameterBlacklist StateBackend Persistence Tests (순위 5.7)
# =============================================================================


class TestParameterBlacklistPersistence:
    """ParameterBlacklist StateBackend 영속성 테스트."""

    def test_save_to_storage_called_on_register(self):
        """
        Purpose:
            register 시 _save_to_storage가 호출되는지 확인.
        """
        blacklist = ParameterBlacklist()
        mock_backend = MagicMock()
        blacklist._backend = mock_backend

        blacklist.register(
            module="test",
            parameter="param",
            blocked_values={"value"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )

        mock_backend.set.assert_called()

    def test_load_from_storage_on_init(self):
        """
        Purpose:
            초기화 시 StateBackend에서 로드하는지 확인.
        """
        stored_data = {
            "circuit_breaker:threshold": {
                "module": "circuit_breaker",
                "parameter": "threshold",
                "blocked_values": ["0.1"],
                "reason": "recovery_loop",
                "registered_at": "2026-01-08T10:00:00",
                "registered_by": "system",
                "incident_id": None,
                "expires_at": None,
            }
        }

        with patch("selfhealing.core.state_backend.get_state_backend") as mock_get_backend:
            mock_backend = MagicMock()
            mock_backend.get.return_value = stored_data
            mock_get_backend.return_value = mock_backend

            blacklist = ParameterBlacklist()

            assert "circuit_breaker:threshold" in blacklist._blacklist
            assert blacklist._blacklist["circuit_breaker:threshold"].blocked_values == {"0.1"}


# =============================================================================
# LearningService Extension Tests (순위 5.5)
# =============================================================================


class TestLearningServiceBlacklist:
    """LearningService 블랙리스트 확장 테스트."""

    @pytest.fixture
    def service(self):
        """LearningService 인스턴스."""
        # 싱글톤 초기화
        LearningService._instance = None
        svc = LearningService()
        svc._parameter_blacklist._backend = None  # StateBackend 비활성화
        yield svc
        svc.clear()
        LearningService._instance = None

    def test_register_dangerous_parameter(self, service):
        """
        Purpose:
            register_dangerous_parameter가 블랙리스트에 등록하는지 확인.
        """
        entry = service.register_dangerous_parameter(
            module="circuit_breaker",
            parameter="threshold",
            blocked_values={"0.1"},
            reason=BlacklistReason.RECOVERY_LOOP,
            incident_id=123,
        )

        assert entry.module == "circuit_breaker"
        assert entry.blocked_values == {"0.1"}
        assert entry.reason == BlacklistReason.RECOVERY_LOOP

    def test_register_dangerous_parameter_also_learns_pattern(self, service):
        """
        Purpose:
            register_dangerous_parameter가 패턴도 학습하는지 확인.
        """
        service.register_dangerous_parameter(
            module="retry",
            parameter="max_attempts",
            blocked_values={"100"},
            reason=BlacklistReason.FLAPPING,
        )

        patterns = service.get_patterns()
        pattern_names = [p.name for p in patterns]

        assert "DangerousParameter:retry:max_attempts" in pattern_names

    def test_is_parameter_blocked(self, service):
        """
        Purpose:
            is_parameter_blocked가 올바르게 동작하는지 확인.
        """
        service.register_dangerous_parameter(
            module="test",
            parameter="param",
            blocked_values={"blocked_value"},
            reason=BlacklistReason.MANUAL_BLOCK,
        )

        is_blocked, entry = service.is_parameter_blocked("test", "param", "blocked_value")

        assert is_blocked is True
        assert entry is not None

    def test_get_blocked_parameters(self, service):
        """
        Purpose:
            get_blocked_parameters가 모든 항목을 반환하는지 확인.
        """
        service.register_dangerous_parameter(
            module="mod1",
            parameter="param1",
            blocked_values={"v1"},
            reason=BlacklistReason.RECOVERY_LOOP,
        )
        service.register_dangerous_parameter(
            module="mod2",
            parameter="param2",
            blocked_values={"v2"},
            reason=BlacklistReason.FLAPPING,
        )

        result = service.get_blocked_parameters()

        assert len(result) == 2

    def test_unblock_parameter(self, service):
        """
        Purpose:
            unblock_parameter가 블랙리스트에서 제거하는지 확인.
        """
        service.register_dangerous_parameter(
            module="test",
            parameter="param",
            blocked_values={"value"},
            reason=BlacklistReason.MANUAL_BLOCK,
        )

        result = service.unblock_parameter("test", "param")

        assert result is True
        is_blocked, _ = service.is_parameter_blocked("test", "param", "value")
        assert is_blocked is False


# =============================================================================
# LearningService Manual Only Mode Tests
# =============================================================================


class TestLearningServiceManualOnlyMode:
    """LearningService Manual Only 모드 테스트."""

    @pytest.fixture
    def service(self):
        """LearningService 인스턴스."""
        LearningService._instance = None
        svc = LearningService()
        svc._parameter_blacklist._backend = None
        yield svc
        svc.clear()
        LearningService._instance = None

    def test_set_manual_only_mode(self, service):
        """
        Purpose:
            set_manual_only_mode가 모드를 설정하는지 확인.
        """
        service.set_manual_only_mode("circuit_breaker", enabled=True)

        assert service.is_manual_only_mode("circuit_breaker") is True

    def test_set_manual_only_mode_disabled(self, service):
        """
        Purpose:
            set_manual_only_mode(enabled=False)가 모드를 해제하는지 확인.
        """
        service.set_manual_only_mode("circuit_breaker", enabled=True)
        service.set_manual_only_mode("circuit_breaker", enabled=False)

        assert service.is_manual_only_mode("circuit_breaker") is False

    def test_is_manual_only_mode_returns_false_by_default(self, service):
        """
        Purpose:
            기본적으로 Manual Only 모드가 아닌지 확인.
        """
        assert service.is_manual_only_mode("unknown_module") is False

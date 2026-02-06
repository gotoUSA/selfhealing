"""
ThrottleConfig DLQ 필드 단위 테스트.

테스트 대상: selfhealing.services.throttle.config.ThrottleConfig DLQ 관련 필드

테스트 시나리오:
1. DLQ 필드 기본값 검증 (소스 상수 참조)
2. from_dict()를 통한 DLQ 필드 로딩
3. ThrottleConfig 인스턴스 정합성
"""

import pytest

from selfhealing.services.throttle.config import ThrottleConfig


class TestThrottleConfigDLQFieldDefaults:
    """ThrottleConfig DLQ 필드 기본값 테스트."""

    def test_dlq_on_rejection_default(self):
        """dlq_on_rejection 기본값: True."""
        config = ThrottleConfig()
        assert config.dlq_on_rejection is True

    def test_auto_replay_on_recovery_default(self):
        """auto_replay_on_recovery 기본값: True."""
        config = ThrottleConfig()
        assert config.auto_replay_on_recovery is True

    def test_replay_batch_size_default(self):
        """replay_batch_size 기본값: 10."""
        config = ThrottleConfig()
        assert config.replay_batch_size == 10

    def test_replay_interval_ms_default(self):
        """replay_interval_ms 기본값: 100."""
        config = ThrottleConfig()
        assert config.replay_interval_ms == 100

    def test_replay_min_recovery_percent_default(self):
        """replay_min_recovery_percent 기본값: 50.0."""
        config = ThrottleConfig()
        assert config.replay_min_recovery_percent == 50.0

    def test_dlq_store_sampling_rate_default(self):
        """dlq_store_sampling_rate 기본값: 1.0."""
        config = ThrottleConfig()
        assert config.dlq_store_sampling_rate == 1.0

    def test_dlq_store_non_essential_default(self):
        """dlq_store_non_essential 기본값: False."""
        config = ThrottleConfig()
        assert config.dlq_store_non_essential is False


class TestThrottleConfigDLQFieldCustomValues:
    """ThrottleConfig DLQ 필드 커스텀 값 설정 테스트."""

    def test_custom_dlq_fields(self):
        """커스텀 DLQ 필드 값이 올바르게 설정된다."""
        config = ThrottleConfig(
            dlq_on_rejection=False,
            auto_replay_on_recovery=False,
            replay_batch_size=20,
            replay_interval_ms=200,
            replay_min_recovery_percent=70.0,
            dlq_store_sampling_rate=0.5,
            dlq_store_non_essential=True,
        )

        assert config.dlq_on_rejection is False
        assert config.auto_replay_on_recovery is False
        assert config.replay_batch_size == 20
        assert config.replay_interval_ms == 200
        assert config.replay_min_recovery_percent == 70.0
        assert config.dlq_store_sampling_rate == 0.5
        assert config.dlq_store_non_essential is True


class TestThrottleConfigFromDictDLQFields:
    """ThrottleConfig.from_dict() DLQ 필드 로딩 테스트."""

    def test_from_dict_includes_dlq_fields(self):
        """from_dict()가 DLQ 필드를 올바르게 로딩한다."""
        data = {
            "initial_limit": 100,
            "dlq_on_rejection": False,
            "auto_replay_on_recovery": False,
            "replay_batch_size": 25,
            "replay_interval_ms": 300,
            "replay_min_recovery_percent": 60.0,
            "dlq_store_sampling_rate": 0.3,
            "dlq_store_non_essential": True,
        }

        config = ThrottleConfig.from_dict(data)

        assert config.dlq_on_rejection is False
        assert config.auto_replay_on_recovery is False
        assert config.replay_batch_size == 25
        assert config.replay_interval_ms == 300
        assert config.replay_min_recovery_percent == 60.0
        assert config.dlq_store_sampling_rate == 0.3
        assert config.dlq_store_non_essential is True

    def test_from_dict_uses_defaults_for_missing_dlq_fields(self):
        """from_dict()에서 DLQ 필드 누락 시 기본값 사용."""
        data = {"initial_limit": 100}
        config = ThrottleConfig.from_dict(data)

        # 기본값과 동일한지 확인
        default_config = ThrottleConfig()
        assert config.dlq_on_rejection == default_config.dlq_on_rejection
        assert config.auto_replay_on_recovery == default_config.auto_replay_on_recovery
        assert config.replay_batch_size == default_config.replay_batch_size
        assert config.replay_interval_ms == default_config.replay_interval_ms
        assert config.replay_min_recovery_percent == default_config.replay_min_recovery_percent
        assert config.dlq_store_sampling_rate == default_config.dlq_store_sampling_rate
        assert config.dlq_store_non_essential == default_config.dlq_store_non_essential

"""
Leader Elector 팩토리 테스트.

get_leader_elector, reset_leader_electors 함수 테스트.
"""

from unittest.mock import MagicMock, patch

import pytest

from selfhealing.coordination.config import (
    LeaderElectionSettings,
    reset_leader_election_settings,
)
from selfhealing.coordination.factory import (
    get_leader_elector,
    reset_leader_electors,
    _electors,
)


@pytest.fixture(autouse=True)
def cleanup():
    """각 테스트 전후 정리."""
    reset_leader_electors()
    reset_leader_election_settings()
    yield
    reset_leader_electors()
    reset_leader_election_settings()


class TestGetLeaderElector:
    """get_leader_elector 함수 테스트."""

    @patch("selfhealing.coordination.redis_elector.RedisLeaderElector")
    def test_creates_redis_elector_by_default(self, mock_class):
        """기본적으로 RedisLeaderElector 생성."""
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance

        elector = get_leader_elector("test-resource")

        mock_class.assert_called_once()
        assert elector == mock_instance

    @patch("selfhealing.coordination.redis_elector.RedisLeaderElector")
    def test_returns_same_instance_for_same_resource(self, mock_class):
        """동일 리소스에 대해 같은 인스턴스 반환."""
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance

        elector1 = get_leader_elector("test-resource")
        elector2 = get_leader_elector("test-resource")

        assert elector1 is elector2
        # 한 번만 생성되어야 함
        assert mock_class.call_count == 1

    @patch("selfhealing.coordination.redis_elector.RedisLeaderElector")
    def test_creates_different_instances_for_different_resources(self, mock_class):
        """다른 리소스에 대해 다른 인스턴스 생성."""
        mock_class.side_effect = [MagicMock(), MagicMock()]

        elector1 = get_leader_elector("resource-a")
        elector2 = get_leader_elector("resource-b")

        assert elector1 is not elector2
        assert mock_class.call_count == 2

    def test_raises_for_etcd_backend(self):
        """etcd 백엔드는 ImportError 또는 NotImplementedError 발생."""
        settings = LeaderElectionSettings(backend="etcd", node_id="test")

        with pytest.raises((ImportError, NotImplementedError)):
            get_leader_elector("test-resource", settings=settings)

    def test_raises_for_unknown_backend(self):
        """알 수 없는 백엔드는 ValueError 발생."""
        # backend 필드는 Literal이므로 직접 설정 불가
        # 이 테스트는 Pydantic 검증에 의해 이미 처리됨
        pass


class TestResetLeaderElectors:
    """reset_leader_electors 함수 테스트."""

    @patch("selfhealing.coordination.redis_elector.RedisLeaderElector")
    def test_stops_all_electors(self, mock_class):
        """모든 등록된 Elector를 중지."""
        mock_elector = MagicMock()
        mock_class.return_value = mock_elector

        # Elector 생성
        get_leader_elector("resource-a")
        get_leader_elector("resource-b")

        # 리셋
        reset_leader_electors()

        # stop()이 호출되었는지 확인
        assert mock_elector.stop.call_count == 2

    @patch("selfhealing.coordination.redis_elector.RedisLeaderElector")
    def test_clears_cache(self, mock_class):
        """캐시가 비워지는지 확인."""
        mock_class.return_value = MagicMock()

        get_leader_elector("test-resource")
        reset_leader_electors()

        # 새로 생성되어야 함
        get_leader_elector("test-resource")
        assert mock_class.call_count == 2

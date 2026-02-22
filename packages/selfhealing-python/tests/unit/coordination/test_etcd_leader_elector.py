"""
EtcdLeaderElector 단위 테스트.

packages/selfhealing-python/tests/unit/coordination/test_etcd_leader_elector.py

etcd는 선택적 의존성이므로 Mock을 사용하여 테스트합니다.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Mock을 사용하여 etcd3 없이도 테스트 가능하게 함
with patch.dict("sys.modules", {"etcd3": MagicMock()}):
    from selfhealing.coordination.etcd_elector import (
        EtcdLeaderElector,
        is_etcd_available,
    )
from selfhealing.coordination.base import LeadershipState

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_etcd():
    """etcd Mock."""
    mock = MagicMock()

    # Lease Mock
    mock_lease = MagicMock()
    mock_lease.id = 12345
    mock_lease.refresh = MagicMock()
    mock_lease.revoke = MagicMock()
    mock.lease.return_value = mock_lease

    # get Mock - 기본적으로 None 반환
    mock.get.return_value = (None, None)

    # transaction Mock
    mock.transaction.return_value = (True, [])
    mock.transactions = MagicMock()
    mock.transactions.create = MagicMock(return_value=MagicMock())
    mock.transactions.put = MagicMock()

    # put/delete Mock
    mock.put = MagicMock()
    mock.delete = MagicMock()

    # close Mock
    mock.close = MagicMock()

    return mock


@pytest.fixture
def mock_settings():
    """설정 Mock."""
    settings = MagicMock()
    settings.enabled = True
    settings.backend = "etcd"
    settings.etcd_endpoints = "localhost:2379"
    settings.etcd_key_prefix = "/selfhealing/leader/"
    settings.lease_ttl_seconds = 10
    settings.renew_interval_seconds = 3
    settings.retry_interval_seconds = 1
    settings.retry_jitter_factor = 0.1
    settings.max_retry_attempts = 3
    settings.self_fencing_enabled = True
    settings.get_node_id.return_value = "test-node-1"
    settings.get_effective_renew_interval.return_value = 3.0
    return settings


@pytest.fixture
def elector(mock_etcd, mock_settings):
    """EtcdLeaderElector 인스턴스."""
    with (
        patch("selfhealing.coordination.etcd_elector.ETCD_AVAILABLE", True),
        patch(
            "selfhealing.coordination.etcd_elector.get_leader_election_settings",
            return_value=mock_settings,
        ),
    ):
        elec = EtcdLeaderElector(
            resource_name="test-resource",
            settings=mock_settings,
            etcd_client=mock_etcd,
        )
        yield elec

        # Cleanup
        try:
            elec._running = False
            if elec._worker:
                elec._worker.join(timeout=1)
        except Exception:
            pass


# =============================================================================
# 가용성 테스트
# =============================================================================


class TestEtcdAvailability:
    """etcd 가용성 테스트."""

    def test_should_report_etcd_availability(self):
        """etcd 라이브러리 가용성을 보고해야 한다."""
        # is_etcd_available는 ETCD_AVAILABLE 상수를 반환
        # 실제 환경에서는 etcd3 설치 여부에 따라 결정됨
        result = is_etcd_available()
        assert isinstance(result, bool)


# =============================================================================
# 초기화 테스트
# =============================================================================


class TestEtcdLeaderElectorInitialization:
    """초기화 테스트."""

    def test_should_initialize_with_resource_name(self, mock_etcd, mock_settings):
        """리소스 이름으로 초기화되어야 한다."""
        with patch("selfhealing.coordination.etcd_elector.ETCD_AVAILABLE", True):
            elec = EtcdLeaderElector(
                resource_name="my-resource",
                settings=mock_settings,
                etcd_client=mock_etcd,
            )

        assert elec.resource_name == "my-resource"

    def test_should_use_node_id_from_settings(self, mock_etcd, mock_settings):
        """설정에서 노드 ID를 사용해야 한다."""
        mock_settings.get_node_id.return_value = "custom-node-id"

        with patch("selfhealing.coordination.etcd_elector.ETCD_AVAILABLE", True):
            elec = EtcdLeaderElector(
                resource_name="my-resource",
                settings=mock_settings,
                etcd_client=mock_etcd,
            )

        assert elec._node_id == "custom-node-id"

    def test_should_construct_leader_key(self, mock_etcd, mock_settings):
        """리더 키를 구성해야 한다."""
        mock_settings.etcd_key_prefix = "/leaders/"

        with patch("selfhealing.coordination.etcd_elector.ETCD_AVAILABLE", True):
            elec = EtcdLeaderElector(
                resource_name="dlq-consumer",
                settings=mock_settings,
                etcd_client=mock_etcd,
            )

        assert elec._key == "/leaders/dlq-consumer"


# =============================================================================
# 상태 테스트
# =============================================================================


class TestEtcdLeaderElectorState:
    """상태 테스트."""

    def test_should_have_not_started_state_initially(self, elector):
        """초기 상태는 NOT_STARTED여야 한다."""
        assert elector.state.value == LeadershipState.NOT_STARTED.value

    def test_should_not_be_leader_initially(self, elector):
        """초기에는 리더가 아니어야 한다."""
        assert not elector.is_leader()


# =============================================================================
# 리더 정보 조회 테스트
# =============================================================================


class TestEtcdLeaderElectorGetLeader:
    """리더 정보 조회 테스트."""

    def test_should_return_none_when_no_leader(self, elector, mock_etcd):
        """리더가 없을 때 None을 반환해야 한다."""
        mock_etcd.get.return_value = (None, None)

        result = elector.get_leader()

        assert result is None

    def test_should_return_leader_info_when_leader_exists(self, elector, mock_etcd):
        """리더가 있을 때 리더 정보를 반환해야 한다."""
        leader_data = {
            "node_id": "leader-node",
            "elected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_metadata = MagicMock()
        mock_metadata.lease_id = 12345
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            mock_metadata,
        )

        # TTL 조회 Mock
        mock_etcd.get_lease_info.return_value = MagicMock(TTL=30)

        result = elector.get_leader()

        assert result is not None
        assert result.node_id == "leader-node"

    def test_should_mark_is_self_correctly(self, elector, mock_etcd):
        """is_self를 올바르게 표시해야 한다."""
        leader_data = {
            "node_id": "test-node-1",  # 자신의 노드 ID
            "elected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_metadata = MagicMock()
        mock_metadata.lease_id = 12345
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            mock_metadata,
        )
        mock_etcd.get_lease_info.return_value = MagicMock(TTL=30)

        result = elector.get_leader()

        assert result.is_self is True


# =============================================================================
# 리더 획득 테스트
# =============================================================================


class TestEtcdLeaderElectorAcquisition:
    """리더 획득 테스트."""

    def test_should_acquire_when_no_leader(self, elector, mock_etcd):
        """리더가 없을 때 획득해야 한다."""
        # 트랜잭션 성공
        mock_etcd.transaction.return_value = (True, [])

        result = elector._try_acquire()

        assert result is True
        mock_etcd.lease.assert_called()

    def test_should_fail_acquire_when_leader_exists(self, elector, mock_etcd):
        """리더가 이미 있을 때 획득 실패해야 한다."""
        # 트랜잭션 실패
        mock_etcd.transaction.return_value = (False, [])

        result = elector._try_acquire()

        assert result is False

    def test_should_increment_fencing_token_on_acquire(self, elector, mock_etcd):
        """획득 시 Fencing Token을 증가시켜야 한다."""
        mock_etcd.transaction.return_value = (True, [])
        mock_etcd.get.return_value = (b"5", None)  # 현재 토큰

        initial_token = elector.get_fencing_token()
        elector._try_acquire()

        # 토큰이 증가했어야 함
        assert elector.get_fencing_token() > initial_token


# =============================================================================
# Lease 갱신 테스트
# =============================================================================


class TestEtcdLeaderElectorLeaseRenewal:
    """Lease 갱신 테스트."""

    def test_should_renew_lease_when_leader(self, elector, mock_etcd):
        """리더일 때 Lease를 갱신해야 한다."""
        # 먼저 획득
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()

        result = elector._renew_lease()

        assert result is True
        assert elector._lease.refresh.called

    def test_should_fail_renew_when_no_lease(self, elector):
        """Lease가 없을 때 갱신 실패해야 한다."""
        result = elector._renew_lease()

        assert result is False


# =============================================================================
# 리더십 반납 테스트
# =============================================================================


class TestEtcdLeaderElectorRelease:
    """리더십 반납 테스트."""

    def test_should_release_leadership(self, elector, mock_etcd):
        """리더십을 반납해야 한다."""
        # 먼저 획득
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()

        # 현재 리더 정보 반환
        leader_data = {"node_id": "test-node-1"}
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            None,
        )

        elector._release_leadership()

        mock_etcd.delete.assert_called_once()

    def test_should_revoke_lease_on_release(self, elector, mock_etcd):
        """반납 시 Lease를 취소해야 한다."""
        # 먼저 획득
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()

        # Lease가 설정되었는지 확인
        assert elector._lease is not None

        leader_data = {"node_id": "test-node-1"}
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            None,
        )

        # 반납 전에 lease 참조 저장
        lease_mock = elector._lease

        elector._release_leadership()

        # lease.revoke()가 호출되었는지 확인
        lease_mock.revoke.assert_called()


# =============================================================================
# 콜백 테스트
# =============================================================================


class TestEtcdLeaderElectorCallbacks:
    """콜백 테스트."""

    def test_should_register_on_become_leader_callback(self, elector):
        """on_become_leader 콜백을 등록해야 한다."""
        callback = MagicMock()

        elector.on_become_leader(callback)

        assert callback in elector._on_become_callbacks

    def test_should_register_on_lose_leader_callback(self, elector):
        """on_lose_leader 콜백을 등록해야 한다."""
        callback = MagicMock()

        elector.on_lose_leader(callback)

        assert callback in elector._on_lose_callbacks

    def test_should_invoke_on_become_leader_callback(self, elector):
        """리더가 될 때 on_become_leader 콜백을 호출해야 한다."""
        callback = MagicMock()
        elector.on_become_leader(callback)

        elector._become_leader()

        time.sleep(0.1)  # 비동기 콜백 대기
        callback.assert_called_once()

    def test_should_invoke_on_lose_leader_callback(self, elector):
        """리더십을 잃을 때 on_lose_leader 콜백을 호출해야 한다."""
        callback = MagicMock()
        elector.on_lose_leader(callback)

        # 먼저 리더가 됨
        elector._become_leader()
        time.sleep(0.1)

        elector._lose_leader(reason="test")

        time.sleep(0.1)
        callback.assert_called_once()


# =============================================================================
# 시작/중지 테스트
# =============================================================================


class TestEtcdLeaderElectorStartStop:
    """시작/중지 테스트."""

    def test_should_start_when_enabled(self, elector, mock_settings):
        """활성화되면 시작해야 한다."""
        mock_settings.enabled = True

        elector.start()

        assert elector._running is True
        assert elector._worker is not None

        elector.stop()

    def test_should_not_start_when_disabled(self, mock_etcd, mock_settings):
        """비활성화되면 시작하지 않아야 한다."""
        mock_settings.enabled = False

        with patch("selfhealing.coordination.etcd_elector.ETCD_AVAILABLE", True):
            elec = EtcdLeaderElector(
                resource_name="test",
                settings=mock_settings,
                etcd_client=mock_etcd,
            )

        elec.start()

        assert elec._running is False

    def test_should_stop_running_loop(self, elector):
        """stop() 호출 시 루프가 중지되어야 한다."""
        elector.start()
        assert elector._running is True

        elector.stop()

        assert elector._running is False
        assert elector.state.value == LeadershipState.STOPPED.value

    def test_should_release_leadership_on_stop(self, elector, mock_etcd):
        """stop() 호출 시 리더십을 반납해야 한다."""
        # 리더 획득
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()
        elector._become_leader()

        leader_data = {"node_id": "test-node-1"}
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            None,
        )

        elector.stop()

        mock_etcd.delete.assert_called()

    def test_should_invoke_on_lose_callback_on_stop(self, elector, mock_etcd):
        """stop() 호출 시 on_lose 콜백이 호출되어야 한다."""
        callback = MagicMock()
        elector.on_lose_leader(callback)

        # 리더가 됨 (실제 상태 변경)
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()
        elector._become_leader()

        time.sleep(0.1)

        leader_data = {"node_id": "test-node-1"}
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            None,
        )

        elector.stop()

        time.sleep(0.3)  # 비동기 콜백 대기
        callback.assert_called()

    def test_should_close_etcd_connection_on_stop(self, elector, mock_etcd):
        """stop() 호출 시 etcd 연결을 닫아야 한다."""
        elector.start()
        elector.stop()

        mock_etcd.close.assert_called()


# =============================================================================
# Self-Fencing 테스트
# =============================================================================


class TestEtcdLeaderElectorSelfFencing:
    """Self-Fencing 테스트."""

    def test_should_lose_leadership_on_renew_failure_with_self_fencing(self, elector, mock_etcd, mock_settings):
        """self_fencing_enabled=True일 때 갱신 실패 시 리더십을 잃어야 한다."""
        mock_settings.self_fencing_enabled = True

        # 리더가 됨
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()
        elector._become_leader()

        # 갱신 실패 시뮬레이션
        elector._lease.refresh.side_effect = Exception("갱신 실패")

        result = elector._renew_lease()

        assert result is False


# =============================================================================
# Lease 유효성 테스트
# =============================================================================


class TestEtcdLeaderElectorLeaseValidity:
    """Lease 유효성 테스트."""

    def test_should_return_false_when_not_leader(self, elector):
        """리더가 아닐 때 False를 반환해야 한다."""
        assert elector.is_lease_valid() is False

    def test_should_return_true_when_leader_and_lease_valid(self, elector, mock_etcd):
        """리더이고 Lease가 유효할 때 True를 반환해야 한다."""
        # 리더가 됨
        mock_etcd.transaction.return_value = (True, [])
        elector._try_acquire()
        elector._become_leader()

        # get_leader가 자신을 반환하도록 설정
        leader_data = {
            "node_id": "test-node-1",
            "elected_at": datetime.now(timezone.utc).isoformat(),
        }
        mock_metadata = MagicMock()
        mock_metadata.lease_id = 12345
        mock_etcd.get.return_value = (
            json.dumps(leader_data).encode(),
            mock_metadata,
        )
        mock_etcd.get_lease_info.return_value = MagicMock(TTL=30)

        assert elector.is_lease_valid() is True

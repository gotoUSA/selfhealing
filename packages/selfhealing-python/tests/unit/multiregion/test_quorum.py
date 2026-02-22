"""
Quorum Witness 테스트.

테스트 대상:
- QuorumLease: 쿼럼 리스 데이터클래스
- InMemoryQuorumWitness: 인메모리 쿼럼 위트니스 (테스트용)
"""

import time

from selfhealing.multiregion.quorum import (
    InMemoryQuorumWitness,
    QuorumLease,
)


class TestQuorumLease:
    """QuorumLease 데이터클래스 테스트."""

    def test_create_lease(self) -> None:
        """리스 생성."""
        now = time.time()
        lease = QuorumLease(
            region="ap-northeast-2",
            acquired_at=now,
            expires_at=now + 60,
            lease_id="test-lease-123",
        )

        assert lease.region == "ap-northeast-2"
        assert lease.lease_id == "test-lease-123"

    def test_is_valid_true(self) -> None:
        """유효한 리스."""
        now = time.time()
        lease = QuorumLease(
            region="ap-northeast-2",
            acquired_at=now,
            expires_at=now + 60,  # 60초 후 만료
            lease_id="test-lease",
        )

        assert lease.is_valid() is True

    def test_is_valid_false(self) -> None:
        """만료된 리스."""
        now = time.time()
        lease = QuorumLease(
            region="ap-northeast-2",
            acquired_at=now - 120,
            expires_at=now - 60,  # 이미 만료
            lease_id="test-lease",
        )

        assert lease.is_valid() is False


class TestInMemoryQuorumWitness:
    """InMemoryQuorumWitness 테스트."""

    def test_acquire_primary_first_time(self) -> None:
        """처음 Primary 락 획득."""
        InMemoryQuorumWitness.reset()

        witness = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )

        success = witness.try_acquire_primary()

        assert success is True
        assert witness.is_primary() is True
        InMemoryQuorumWitness.reset()

    def test_acquire_primary_by_different_region(self) -> None:
        """다른 리전의 Primary 락 획득 시도."""
        InMemoryQuorumWitness.reset()

        witness_kr = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness_us = InMemoryQuorumWitness(
            region="us-east-1",
            cluster_id="prod-us-1",
        )

        # 한국 리전이 먼저 획득
        result_kr = witness_kr.try_acquire_primary()
        assert result_kr is True

        # 미국 리전이 시도 → 실패
        success = witness_us.try_acquire_primary()

        assert success is False
        assert witness_us.is_primary() is False
        InMemoryQuorumWitness.reset()

    def test_renew_lease(self) -> None:
        """리스 갱신."""
        InMemoryQuorumWitness.reset()

        witness = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness.try_acquire_primary()

        success = witness.renew_lease()

        assert success is True
        InMemoryQuorumWitness.reset()

    def test_renew_lease_wrong_region(self) -> None:
        """다른 리전이 갱신 시도 → 실패."""
        InMemoryQuorumWitness.reset()

        witness_kr = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness_us = InMemoryQuorumWitness(
            region="us-east-1",
            cluster_id="prod-us-1",
        )

        witness_kr.try_acquire_primary()
        success = witness_us.renew_lease()

        assert success is False
        InMemoryQuorumWitness.reset()

    def test_release_lease(self) -> None:
        """리스 해제."""
        InMemoryQuorumWitness.reset()

        witness = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness.try_acquire_primary()
        assert witness.is_primary() is True

        witness.release_lease()

        assert witness.is_primary() is False
        assert witness.get_current_primary() is None
        InMemoryQuorumWitness.reset()

    def test_release_wrong_region(self) -> None:
        """다른 리전이 해제 시도 → 무시됨."""
        InMemoryQuorumWitness.reset()

        witness_kr = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness_us = InMemoryQuorumWitness(
            region="us-east-1",
            cluster_id="prod-us-1",
        )

        witness_kr.try_acquire_primary()
        witness_us.release_lease()  # 다른 리전이 해제 시도

        # 한국 리전 리스 유지
        assert witness_kr.is_primary() is True
        InMemoryQuorumWitness.reset()

    def test_get_current_primary(self) -> None:
        """현재 Primary 리전 조회."""
        InMemoryQuorumWitness.reset()

        witness = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness.try_acquire_primary()

        primary = witness.get_current_primary()

        assert primary == "ap-northeast-2"
        InMemoryQuorumWitness.reset()

    def test_is_primary(self) -> None:
        """Primary 여부 확인."""
        InMemoryQuorumWitness.reset()

        witness_kr = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness_us = InMemoryQuorumWitness(
            region="us-east-1",
            cluster_id="prod-us-1",
        )

        witness_kr.try_acquire_primary()

        assert witness_kr.is_primary() is True
        assert witness_us.is_primary() is False
        InMemoryQuorumWitness.reset()

    def test_reset(self) -> None:
        """전역 상태 리셋."""
        InMemoryQuorumWitness.reset()

        witness = InMemoryQuorumWitness(
            region="ap-northeast-2",
            cluster_id="prod-kr-1",
        )
        witness.try_acquire_primary()
        assert witness.get_current_primary() == "ap-northeast-2"

        InMemoryQuorumWitness.reset()

        assert witness.get_current_primary() is None

"""
create_from_failure() metadata["region"] 자동 주입 Django 통합 테스트.

221 설계 §3A.1: FailedOperation 생성 시 ClusterIdentity.region을
metadata에 자동 주입하는 동작을 실제 Django DB로 검증.

실행 조건: Docker 환경 (DB/Redis 필요)
실행 방법:
    docker-compose -f docker-compose.test.yml run --rm test-hybrid-storage \
        python -m pytest tests/self_healing/django/test_create_from_failure_region.py -v
"""

import pytest
from unittest.mock import patch, MagicMock

from shopping.models.failed_operation import FailedOperation


# =============================================================================
# 동작 검증: metadata["region"] 자동 주입
# =============================================================================


@pytest.mark.django_db
class TestCreateFromFailureRegionInjectionBehavior:
    """FailedOperation.create_from_failure() region 주입 동작 (Django DB)."""

    def test_region_injected_into_metadata(self):
        """ClusterIdentity.region이 있으면 metadata["region"]에 자동 주입."""
        mock_identity = MagicMock()
        mock_identity.region = "seoul"

        with patch(
            "shopping.models.failed_operation.get_cluster_identity",
            create=True,
            return_value=mock_identity,
        ):
            entry = FailedOperation.create_from_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
                error_message="Connection timed out",
                metadata={"debug": "test"},
            )

        assert entry.metadata["region"] == "seoul"
        assert entry.metadata["debug"] == "test"

    def test_existing_region_not_overwritten(self):
        """이미 metadata["region"]이 있으면 덮어쓰지 않음."""
        mock_identity = MagicMock()
        mock_identity.region = "seoul"

        with patch(
            "shopping.models.failed_operation.get_cluster_identity",
            create=True,
            return_value=mock_identity,
        ):
            entry = FailedOperation.create_from_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
                metadata={"region": "tokyo"},
            )

        assert entry.metadata["region"] == "tokyo"

    def test_no_region_when_identity_unavailable(self):
        """get_cluster_identity 예외 시 Fail-Open (region 미주입)."""
        with patch(
            "shopping.models.failed_operation.get_cluster_identity",
            create=True,
            side_effect=RuntimeError("unavailable"),
        ):
            entry = FailedOperation.create_from_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
            )

        assert "region" not in entry.metadata

    def test_no_region_when_identity_region_is_none(self):
        """ClusterIdentity.region=None이면 metadata에 region 미주입."""
        mock_identity = MagicMock()
        mock_identity.region = None

        with patch(
            "shopping.models.failed_operation.get_cluster_identity",
            create=True,
            return_value=mock_identity,
        ):
            entry = FailedOperation.create_from_failure(
                domain="payment",
                failure_type="PG_TIMEOUT",
            )

        assert "region" not in entry.metadata

"""
Phase 4 Tests - Domain Priority Policy (도메인별 차등 정책).

Tests for:
1. ReplayAutomationConfig domain priority fields
2. ReplayService priority-based entry retrieval
3. Domain-specific max_retries override
4. RuntimeConfig integration for priority settings
5. ReplayAutomationConfigSerializer Phase 4 fields
6. BatchReplayResult priority_used field

Reference: docs/self_healing/middleware_system/19_DLQ_AUTOMATION_BLUEPRINT.md §7
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# =============================================================================
# ReplayAutomationConfig Phase 4 Field Tests
# =============================================================================


class TestReplayAutomationConfigPhase4:
    """ReplayAutomationConfig Phase 4 필드 테스트."""

    def test_priority_fields_default_values(self):
        """Phase 4 필드 기본값 확인."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig()

        assert config.priority_enabled is False
        assert config.domain_priorities == {}
        assert config.domain_max_retries == {}
        assert config.domain_on_circuit_close == {}

    def test_priority_fields_custom_values(self):
        """Phase 4 필드 커스텀 값 설정."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig(
            priority_enabled=True,
            domain_priorities={"payment": "critical", "notification": "low"},
            domain_max_retries={"payment": 10, "notification": 3},
            domain_on_circuit_close={"payment": True, "analytics": False},
        )

        assert config.priority_enabled is True
        assert config.domain_priorities == {"payment": "critical", "notification": "low"}
        assert config.domain_max_retries == {"payment": 10, "notification": 3}
        assert config.domain_on_circuit_close == {"payment": True, "analytics": False}

    def test_config_to_dict_includes_phase4_fields(self):
        """model_dump()가 Phase 4 필드를 포함하는지 확인."""
        from selfhealing.core.config import ReplayAutomationConfig

        config = ReplayAutomationConfig(
            priority_enabled=True,
            domain_priorities={"payment": "critical"},
        )

        config_dict = config.model_dump()

        assert "priority_enabled" in config_dict
        assert "domain_priorities" in config_dict
        assert "domain_max_retries" in config_dict
        assert "domain_on_circuit_close" in config_dict
        assert config_dict["priority_enabled"] is True
        assert config_dict["domain_priorities"] == {"payment": "critical"}


# =============================================================================
# ReplayService Priority Mode Tests
# =============================================================================


class TestReplayServicePriorityMode:
    """ReplayService 우선순위 모드 테스트."""

    @pytest.fixture
    def mock_repository(self):
        """Mock repository fixture."""
        mock = MagicMock()
        mock.get_pending_entries.return_value = []
        mock.try_acquire_for_replay.return_value = None
        mock.get_by_id.return_value = None
        return mock

    @pytest.fixture
    def replay_service(self, mock_repository):
        """ReplayService with mock repository."""
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService(repository=mock_repository)
        return service

    def test_is_priority_enabled_default_false(self, replay_service):
        """priority_enabled 기본값 False 확인."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.return_value = {}

            result = replay_service._is_priority_enabled()
            assert result is False

    def test_is_priority_enabled_from_runtime_config(self, replay_service):
        """RuntimeConfig에서 priority_enabled 읽기."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.return_value = {"priority_enabled": True}

            result = replay_service._is_priority_enabled()
            assert result is True

    def test_is_priority_enabled_handles_exception(self, replay_service):
        """RuntimeConfig 예외 시 False 반환."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.side_effect = Exception("Config error")

            result = replay_service._is_priority_enabled()
            assert result is False

    def test_get_domain_priorities_from_runtime_config(self, replay_service):
        """RuntimeConfig에서 domain_priorities 읽기."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.return_value = {
                "domain_priorities": {"payment": "critical", "order": "normal"}
            }

            result = replay_service._get_domain_priorities()
            assert result == {"payment": "critical", "order": "normal"}

    def test_get_domain_priorities_handles_exception(self, replay_service):
        """RuntimeConfig 예외 시 빈 딕셔너리 반환."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.side_effect = Exception("Config error")

            result = replay_service._get_domain_priorities()
            assert result == {}

    def test_get_domain_max_retries(self, replay_service):
        """도메인별 max_retries 조회."""
        with patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:
            mock_mgr.return_value.get_config.return_value = {
                "domain_max_retries": {"payment": 10, "notification": 3}
            }

            assert replay_service._get_domain_max_retries("payment") == 10
            assert replay_service._get_domain_max_retries("notification") == 3
            assert replay_service._get_domain_max_retries("unknown") is None


# =============================================================================
# Priority-Based Entry Retrieval Tests
# =============================================================================


class TestGetEntriesByPriority:
    """_get_entries_by_priority 메서드 테스트."""

    @pytest.fixture
    def mock_repository(self):
        """Mock repository fixture."""
        mock = MagicMock()
        return mock

    @pytest.fixture
    def replay_service(self, mock_repository):
        """ReplayService with mock repository."""
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService(repository=mock_repository)
        return service

    def test_priority_order_critical_first(self, replay_service, mock_repository):
        """Critical 도메인이 먼저 처리되는지 확인."""
        from selfhealing.interfaces.repositories import FailedOperationData

        # Setup mock entries
        payment_entry = MagicMock()
        payment_entry.id = 1
        payment_entry.domain = "payment"

        order_entry = MagicMock()
        order_entry.id = 2
        order_entry.domain = "order"

        notification_entry = MagicMock()
        notification_entry.id = 3
        notification_entry.domain = "notification"

        # Setup repository to return different entries for different domains
        def get_pending_side_effect(domain=None, failure_type=None, max_retry_count=None, limit=None):
            if domain == "payment":
                return [payment_entry]
            elif domain == "order":
                return [order_entry]
            elif domain == "notification":
                return [notification_entry]
            return []

        mock_repository.get_pending_entries.side_effect = get_pending_side_effect

        with patch.object(replay_service, "_get_domain_priorities") as mock_priorities, \
             patch.object(replay_service, "_get_domain_max_retries") as mock_max_retries:

            mock_priorities.return_value = {
                "payment": "critical",
                "order": "normal",
                "notification": "low",
            }
            mock_max_retries.return_value = None

            entries, domains = replay_service._get_entries_by_priority(
                failure_type=None,
                max_replays=5,
                limit=10,
            )

            # Check order: critical > normal > low
            assert len(entries) == 3
            assert entries[0].domain == "payment"
            assert entries[1].domain == "order"
            assert entries[2].domain == "notification"
            assert domains == ["payment", "order", "notification"]

    def test_priority_respects_limit(self, replay_service, mock_repository):
        """limit 파라미터가 존중되는지 확인."""
        payment_entries = [MagicMock(id=i, domain="payment") for i in range(5)]

        def get_pending_side_effect(domain=None, failure_type=None, max_retry_count=None, limit=None):
            if domain == "payment":
                return payment_entries[:limit] if limit else payment_entries
            return []

        mock_repository.get_pending_entries.side_effect = get_pending_side_effect

        with patch.object(replay_service, "_get_domain_priorities") as mock_priorities, \
             patch.object(replay_service, "_get_domain_max_retries") as mock_max_retries:

            mock_priorities.return_value = {"payment": "critical"}
            mock_max_retries.return_value = None

            entries, domains = replay_service._get_entries_by_priority(
                failure_type=None,
                max_replays=5,
                limit=3,
            )

            assert len(entries) == 3
            assert domains == ["payment"]

    def test_domain_specific_max_retries_applied(self, replay_service, mock_repository):
        """도메인별 max_retries 오버라이드가 적용되는지 확인."""
        def get_pending_side_effect(domain=None, failure_type=None, max_retry_count=None, limit=None):
            # Capture the max_retry_count used
            if domain == "payment":
                assert max_retry_count == 10, "payment should use domain-specific max_retries=10"
            elif domain == "notification":
                assert max_retry_count == 3, "notification should use domain-specific max_retries=3"
            elif domain == "order":
                assert max_retry_count == 5, "order should use default max_retries=5"
            return []

        mock_repository.get_pending_entries.side_effect = get_pending_side_effect

        with patch.object(replay_service, "_get_domain_priorities") as mock_priorities, \
             patch.object(replay_service, "_get_domain_max_retries") as mock_max_retries:

            mock_priorities.return_value = {
                "payment": "critical",
                "order": "normal",
                "notification": "low",
            }
            mock_max_retries.side_effect = lambda d: {"payment": 10, "notification": 3}.get(d)

            replay_service._get_entries_by_priority(
                failure_type=None,
                max_replays=5,
                limit=100,
            )

    def test_unconfigured_domains_processed_last(self, replay_service, mock_repository):
        """설정되지 않은 도메인은 마지막에 처리."""
        payment_entry = MagicMock(id=1, domain="payment")
        unconfigured_entry = MagicMock(id=2, domain="unconfigured_service")

        def get_pending_side_effect(domain=None, failure_type=None, max_retry_count=None, limit=None):
            if domain == "payment":
                return [payment_entry]
            elif domain is None:
                # Return all entries when fetching unconfigured domains
                return [payment_entry, unconfigured_entry]
            return []

        mock_repository.get_pending_entries.side_effect = get_pending_side_effect

        with patch.object(replay_service, "_get_domain_priorities") as mock_priorities, \
             patch.object(replay_service, "_get_domain_max_retries") as mock_max_retries:

            mock_priorities.return_value = {"payment": "critical"}
            mock_max_retries.return_value = None

            entries, domains = replay_service._get_entries_by_priority(
                failure_type=None,
                max_replays=5,
                limit=10,
            )

            # payment (critical) should be first, unconfigured_service last
            assert len(entries) == 2
            assert entries[0].domain == "payment"
            assert entries[1].domain == "unconfigured_service"


# =============================================================================
# replay_batch with Priority Mode Tests
# =============================================================================


class TestReplayBatchPriorityMode:
    """replay_batch 우선순위 모드 통합 테스트."""

    @pytest.fixture
    def mock_repository(self):
        """Mock repository fixture."""
        mock = MagicMock()
        mock.get_pending_entries.return_value = []
        mock.try_acquire_for_replay.return_value = None
        return mock

    @pytest.fixture
    def replay_service(self, mock_repository):
        """ReplayService with mock repository."""
        from selfhealing.services.replay_service import ReplayService

        service = ReplayService(repository=mock_repository)
        return service

    def test_replay_batch_uses_priority_when_enabled(self, replay_service):
        """priority_enabled=True 시 우선순위 모드 사용."""
        with patch("selfhealing.services.replay_service.check_all_governance") as mock_gov, \
             patch.object(replay_service, "_is_priority_enabled") as mock_priority, \
             patch.object(replay_service, "_is_adaptive_enabled") as mock_adaptive, \
             patch.object(replay_service, "_get_entries_by_priority") as mock_get_priority:

            mock_gov.return_value = MagicMock(allowed=True)
            mock_priority.return_value = True
            mock_adaptive.return_value = False
            mock_get_priority.return_value = ([], ["payment", "order"])

            result = replay_service.replay_batch()

            mock_get_priority.assert_called_once()
            assert result.priority_used is True
            assert result.domains_processed == ["payment", "order"]

    def test_replay_batch_skips_priority_when_domain_specified(self, replay_service, mock_repository):
        """domain 파라미터 지정 시 우선순위 모드 무시."""
        with patch("selfhealing.services.replay_service.check_all_governance") as mock_gov, \
             patch.object(replay_service, "_is_priority_enabled") as mock_priority, \
             patch.object(replay_service, "_is_adaptive_enabled") as mock_adaptive:

            mock_gov.return_value = MagicMock(allowed=True)
            mock_priority.return_value = True
            mock_adaptive.return_value = False

            result = replay_service.replay_batch(domain="payment")

            # Should use normal mode when domain is specified
            mock_repository.get_pending_entries.assert_called_once()
            assert result.priority_used is False
            assert result.domains_processed is None

    def test_replay_batch_override_use_priority_param(self, replay_service):
        """use_priority 파라미터로 우선순위 모드 오버라이드."""
        with patch("selfhealing.services.replay_service.check_all_governance") as mock_gov, \
             patch.object(replay_service, "_get_entries_by_priority") as mock_get_priority, \
             patch.object(replay_service, "_is_adaptive_enabled") as mock_adaptive:

            mock_gov.return_value = MagicMock(allowed=True)
            mock_adaptive.return_value = False
            mock_get_priority.return_value = ([], [])

            # Force priority mode even if RuntimeConfig says disabled
            result = replay_service.replay_batch(use_priority=True)

            mock_get_priority.assert_called_once()
            assert result.priority_used is True

    def test_replay_batch_priority_disabled_uses_normal_mode(self, replay_service, mock_repository):
        """priority_enabled=False 시 일반 모드 사용."""
        with patch("selfhealing.services.replay_service.check_all_governance") as mock_gov, \
             patch.object(replay_service, "_is_priority_enabled") as mock_priority, \
             patch.object(replay_service, "_is_adaptive_enabled") as mock_adaptive:

            mock_gov.return_value = MagicMock(allowed=True)
            mock_priority.return_value = False
            mock_adaptive.return_value = False

            result = replay_service.replay_batch()

            mock_repository.get_pending_entries.assert_called_once()
            assert result.priority_used is False


# =============================================================================
# BatchReplayResult Phase 4 Field Tests
# =============================================================================


class TestBatchReplayResultPhase4:
    """BatchReplayResult Phase 4 필드 테스트."""

    def test_default_values(self):
        """Phase 4 필드 기본값 확인."""
        from selfhealing.services.replay_service import BatchReplayResult

        result = BatchReplayResult()

        assert result.priority_used is False
        assert result.domains_processed is None

    def test_custom_values(self):
        """Phase 4 필드 커스텀 값 설정."""
        from selfhealing.services.replay_service import BatchReplayResult

        result = BatchReplayResult(
            total=10,
            success_count=8,
            failed_count=2,
            priority_used=True,
            domains_processed=["payment", "order", "notification"],
        )

        assert result.priority_used is True
        assert result.domains_processed == ["payment", "order", "notification"]


# =============================================================================
# Serializer Phase 4 Field Tests
# =============================================================================


class TestReplayAutomationSerializerPhase4:
    """ReplayAutomationConfigSerializer Phase 4 필드 테스트."""

    def test_priority_fields_validation(self):
        """Phase 4 필드 유효성 검사."""
        from selfhealing.api.django.serializers.config import ReplayAutomationConfigSerializer

        serializer = ReplayAutomationConfigSerializer(data={
            "priority_enabled": True,
            "domain_priorities": {"payment": "critical", "order": "normal"},
            "domain_max_retries": {"payment": 10},
            "domain_on_circuit_close": {"payment": True},
        })

        assert serializer.is_valid(), serializer.errors
        assert serializer.validated_data["priority_enabled"] is True
        assert serializer.validated_data["domain_priorities"] == {"payment": "critical", "order": "normal"}
        assert serializer.validated_data["domain_max_retries"] == {"payment": 10}
        assert serializer.validated_data["domain_on_circuit_close"] == {"payment": True}

    def test_invalid_priority_value_rejected(self):
        """잘못된 priority 값 거부."""
        from selfhealing.api.django.serializers.config import ReplayAutomationConfigSerializer

        serializer = ReplayAutomationConfigSerializer(data={
            "domain_priorities": {"payment": "invalid_priority"}
        })

        assert not serializer.is_valid()
        assert "domain_priorities" in serializer.errors

    def test_valid_priority_values(self):
        """유효한 priority 값들 허용."""
        from selfhealing.api.django.serializers.config import ReplayAutomationConfigSerializer

        serializer = ReplayAutomationConfigSerializer(data={
            "domain_priorities": {
                "payment": "critical",
                "order": "normal",
                "notification": "low",
            }
        })

        assert serializer.is_valid(), serializer.errors

    def test_domain_max_retries_validation(self):
        """domain_max_retries 범위 검증."""
        from selfhealing.api.django.serializers.config import ReplayAutomationConfigSerializer

        # Valid range (1-20)
        serializer = ReplayAutomationConfigSerializer(data={
            "domain_max_retries": {"payment": 10}
        })
        assert serializer.is_valid(), serializer.errors

        # Invalid: > 20
        serializer = ReplayAutomationConfigSerializer(data={
            "domain_max_retries": {"payment": 25}
        })
        assert not serializer.is_valid()

        # Invalid: < 1
        serializer = ReplayAutomationConfigSerializer(data={
            "domain_max_retries": {"payment": 0}
        })
        assert not serializer.is_valid()


# =============================================================================
# Thread Safety Tests
# =============================================================================


class TestPriorityModeThreadSafety:
    """우선순위 모드 스레드 안전성 테스트."""

    def test_concurrent_priority_calls(self):
        """동시 호출 시 스레드 안전성 확인."""
        import threading
        from selfhealing.services.replay_service import ReplayService

        mock_repo = MagicMock()
        mock_repo.get_pending_entries.return_value = []
        service = ReplayService(repository=mock_repo)

        results = []
        errors = []

        def call_get_entries():
            try:
                with patch.object(service, "_get_domain_priorities") as mock_priorities, \
                     patch.object(service, "_get_domain_max_retries") as mock_max_retries:

                    mock_priorities.return_value = {"payment": "critical"}
                    mock_max_retries.return_value = None

                    entries, domains = service._get_entries_by_priority(
                        failure_type=None,
                        max_replays=5,
                        limit=10,
                    )
                    results.append((entries, domains))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=call_get_entries) for _ in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"
        assert len(results) == 10


# =============================================================================
# Integration Tests
# =============================================================================


class TestPhase4Integration:
    """Phase 4 통합 테스트."""

    def test_full_priority_flow(self):
        """전체 우선순위 플로우 테스트."""
        from selfhealing.services.replay_service import ReplayService

        mock_repo = MagicMock()

        # Create mock entries for different domains
        payment_entry = MagicMock(id=1, domain="payment", status="pending")
        order_entry = MagicMock(id=2, domain="order", status="pending")
        notification_entry = MagicMock(id=3, domain="notification", status="pending")

        def get_pending_side_effect(domain=None, failure_type=None, max_retry_count=None, limit=None):
            if domain == "payment":
                return [payment_entry]
            elif domain == "order":
                return [order_entry]
            elif domain == "notification":
                return [notification_entry]
            elif domain is None:
                return [payment_entry, order_entry, notification_entry]
            return []

        mock_repo.get_pending_entries.side_effect = get_pending_side_effect
        mock_repo.try_acquire_for_replay.return_value = None

        service = ReplayService(repository=mock_repo)

        with patch("selfhealing.services.replay_service.check_all_governance") as mock_gov, \
             patch("selfhealing.services.runtime_config.get_runtime_config_manager") as mock_mgr:

            mock_gov.return_value = MagicMock(allowed=True)
            mock_mgr.return_value.get_config.return_value = {
                "priority_enabled": True,
                "domain_priorities": {
                    "payment": "critical",
                    "order": "normal",
                    "notification": "low",
                },
                "domain_max_retries": {"payment": 10},
                "adaptive_enabled": False,
            }

            result = service.replay_batch(max_items=10)

            assert result.priority_used is True
            assert result.total == 3
            assert result.domains_processed == ["payment", "order", "notification"]

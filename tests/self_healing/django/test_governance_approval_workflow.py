"""
Governance Approval Workflow 구현 테스트.

테스트 대상:
1. L2StorageConfig RuntimeConfigManager 통합
2. ChaosConfig 데이터클래스
3. 4-Eyes Approval Workflow
4. ApprovalRequestListView API
5. ApprovalRequestApproveView API
6. ApprovalRequestRejectView API
7. L2StorageConfigManagedView API
"""



# =============================================================================
# L2StorageConfig Tests
# =============================================================================


class TestL2StorageConfig:
    """Tests for L2StorageConfig (Pydantic BaseSettings)."""

    def test_default_values(self):
        """Test default values are set correctly."""
        from selfhealing.core.config import L2StorageConfig

        config = L2StorageConfig()

        # Updated defaults from L2StorageSettings
        assert config.redis_timeout_ms == 1000
        assert config.database_timeout_ms == 200
        assert config.fallback_timeout_ms == 100
        assert config.shadow_log_enabled is True
        assert config.shadow_log_max_entries == 1000

    def test_custom_values(self):
        """Test custom values can be set."""
        from selfhealing.core.config import L2StorageConfig

        config = L2StorageConfig(
            redis_timeout_ms=100,
            database_timeout_ms=500,
            shadow_log_enabled=False,
        )

        assert config.redis_timeout_ms == 100
        assert config.database_timeout_ms == 500
        assert config.shadow_log_enabled is False

    def test_dataclass_conversion(self):
        """Test config can be converted to dict (uses Pydantic model_dump)."""
        from selfhealing.core.config import L2StorageConfig

        config = L2StorageConfig()
        config_dict = config.model_dump()

        assert "redis_timeout_ms" in config_dict
        assert "shadow_log_enabled" in config_dict
        assert config_dict["redis_timeout_ms"] == 1000


# =============================================================================
# ChaosConfig Tests
# =============================================================================


class TestChaosConfig:
    """Tests for ChaosConfig dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        from selfhealing.core.config import ChaosConfig

        config = ChaosConfig()

        assert config.max_blast_radius == 0.10
        assert config.max_failure_rate == 0.20
        assert config.auto_rollback_enabled is True
        assert config.dry_run_default is True
        assert config.experiment_timeout_seconds == 300

    def test_custom_values(self):
        """Test custom values can be set."""
        from selfhealing.core.config import ChaosConfig

        config = ChaosConfig(
            max_blast_radius=0.05,
            require_approval=True,
        )

        assert config.max_blast_radius == 0.05
        assert config.require_approval is True


# =============================================================================
# ApprovalRequest Tests
# =============================================================================


class TestApprovalRequest:
    """Tests for ApprovalRequest dataclass."""

    def test_default_values(self):
        """Test default values are set correctly."""
        from selfhealing.core.config import ApprovalRequest

        request = ApprovalRequest()

        assert request.id == ""
        assert request.request_type == ""
        assert request.status == "PENDING"
        assert request.payload == {}

    def test_custom_values(self):
        """Test custom values can be set."""
        from selfhealing.core.config import ApprovalRequest

        request = ApprovalRequest(
            id="test-123",
            request_type="config_change",
            requested_by="admin_a",
            status="PENDING",
        )

        assert request.id == "test-123"
        assert request.request_type == "config_change"
        assert request.requested_by == "admin_a"


# =============================================================================
# RuntimeConfigManager L2Storage Tests
# =============================================================================


class TestRuntimeConfigManagerL2Storage:
    """Tests for L2Storage in RuntimeConfigManager."""

    def test_l2_storage_in_storage_keys(self):
        """Test l2_storage key is in STORAGE_KEYS."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "l2_storage" in STORAGE_KEYS
        assert STORAGE_KEYS["l2_storage"] == "runtime_config:l2_storage"

    def test_l2_storage_in_config_classes(self):
        """Test L2StorageConfig is in CONFIG_CLASSES."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES
        from selfhealing.core.config import L2StorageConfig

        assert "l2_storage" in CONFIG_CLASSES
        assert CONFIG_CLASSES["l2_storage"] == L2StorageConfig

    def test_get_l2_storage_config_method_exists(self):
        """Test get_l2_storage_config method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "get_l2_storage_config")

    def test_update_l2_storage_config_method_exists(self):
        """Test update_l2_storage_config method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "update_l2_storage_config")

    def test_reset_l2_storage_config_method_exists(self):
        """Test reset_l2_storage_config method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "reset_l2_storage_config")


# =============================================================================
# 4-Eyes Approval Workflow Tests
# =============================================================================


class TestApprovalWorkflow:
    """Tests for 4-Eyes Approval Workflow."""

    def test_approval_requests_in_storage_keys(self):
        """Test approval_requests key is in STORAGE_KEYS."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "approval_requests" in STORAGE_KEYS

    def test_create_approval_request_method_exists(self):
        """Test create_approval_request method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "create_approval_request")

    def test_get_approval_requests_method_exists(self):
        """Test get_approval_requests method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "get_approval_requests")

    def test_approve_request_method_exists(self):
        """Test approve_request method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "approve_request")

    def test_reject_request_method_exists(self):
        """Test reject_request method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "reject_request")

    def test_expire_old_requests_method_exists(self):
        """Test expire_old_requests method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "expire_old_requests")

    def test_get_pending_requests_for_user_method_exists(self):
        """Test get_pending_requests_for_user method exists."""
        from selfhealing.services.runtime_config import RuntimeConfigManager

        assert hasattr(RuntimeConfigManager, "get_pending_requests_for_user")


# =============================================================================
# API View Tests
# =============================================================================


class TestApprovalRequestListView:
    """Tests for ApprovalRequestListView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import ApprovalRequestListView

        assert ApprovalRequestListView is not None

    def test_view_permission_class(self):
        """Test view has correct permission class."""
        from selfhealing.api.django.views.governance import ApprovalRequestListView
        from selfhealing.api.django.permissions import IsSelfHealingAdmin

        view = ApprovalRequestListView()
        assert IsSelfHealingAdmin in view.permission_classes


class TestApprovalRequestApproveView:
    """Tests for ApprovalRequestApproveView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import ApprovalRequestApproveView

        assert ApprovalRequestApproveView is not None


class TestApprovalRequestRejectView:
    """Tests for ApprovalRequestRejectView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import ApprovalRequestRejectView

        assert ApprovalRequestRejectView is not None


class TestL2StorageConfigManagedView:
    """Tests for L2StorageConfigManagedView API."""

    def test_view_exists(self):
        """Test view class exists."""
        from selfhealing.api.django.views.governance import L2StorageConfigManagedView

        assert L2StorageConfigManagedView is not None

    def test_view_permission_class(self):
        """Test view has correct permission class."""
        from selfhealing.api.django.views.governance import L2StorageConfigManagedView
        from selfhealing.api.django.permissions import IsSelfHealingAdmin

        view = L2StorageConfigManagedView()
        assert IsSelfHealingAdmin in view.permission_classes


# =============================================================================
# URL Registration Tests
# =============================================================================


class TestURLRegistration:
    """Tests for URL registration."""

    def test_approval_requests_url_registered(self):
        """Test approval-requests/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns

        url_names = [p.name for p in urlpatterns if hasattr(p, "name")]
        assert "approval-requests-list" in url_names

    def test_approval_approve_url_registered(self):
        """Test approval-requests/<id>/approve/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns

        url_names = [p.name for p in urlpatterns if hasattr(p, "name")]
        assert "approval-request-approve" in url_names

    def test_approval_reject_url_registered(self):
        """Test approval-requests/<id>/reject/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns

        url_names = [p.name for p in urlpatterns if hasattr(p, "name")]
        assert "approval-request-reject" in url_names

    def test_l2_storage_config_url_registered(self):
        """Test config/l2-storage/ URL is registered."""
        from selfhealing.api.django.urls import urlpatterns

        url_names = [p.name for p in urlpatterns if hasattr(p, "name")]
        assert "config-l2-storage" in url_names


# =============================================================================
# Integration Tests
# =============================================================================


class TestGovernanceIntegration:
    """Governance 기능 통합 테스트."""

    def test_chaos_config_in_storage_keys(self):
        """Test chaos config is in storage keys."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "chaos" in STORAGE_KEYS

    def test_all_governance_storage_keys_present(self):
        """RuntimeConfigManager에 등록된 governance 관련 storage key 확인."""
        from selfhealing.services.runtime_config.constants import STORAGE_KEYS

        assert "l2_storage" in STORAGE_KEYS
        assert "chaos" in STORAGE_KEYS
        assert "approval_requests" in STORAGE_KEYS

    def test_all_governance_config_classes_present(self):
        """RuntimeConfigManager에 등록된 governance 관련 config class 확인."""
        from selfhealing.services.runtime_config.constants import CONFIG_CLASSES
        from selfhealing.core.config import L2StorageConfig, ChaosConfig

        assert CONFIG_CLASSES["l2_storage"] == L2StorageConfig
        assert CONFIG_CLASSES["chaos"] == ChaosConfig

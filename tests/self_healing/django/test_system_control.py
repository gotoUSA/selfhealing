"""
Tests for System Control API (Kill Switch & Dry Run).

Tests:
1. SystemState dataclass
2. StateBackend implementations (File, Memory)
3. SystemControlManager
4. Dry run mode
5. API endpoints
"""

import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# Configure Django settings before importing Django-dependent modules
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
try:
    django.setup()
except Exception:
    pass  # May fail in some test environments


class TestSystemState:
    """Tests for SystemState dataclass."""
    
    def test_default_state(self):
        """Default state should be enabled with dry_run off."""
        from selfhealing.api.django.views.system_control import SystemState
        
        state = SystemState()
        
        assert state.enabled is True
        assert state.dry_run is False
        assert state.disabled_at is None
        assert state.dry_run_enabled_at is None
    
    def test_to_dict(self):
        """State should serialize to dict."""
        from selfhealing.api.django.views.system_control import SystemState
        
        state = SystemState(enabled=False, dry_run=True, disabled_by="admin")
        result = state.to_dict()
        
        assert result["enabled"] is False
        assert result["dry_run"] is True
        assert result["disabled_by"] == "admin"
    
    def test_from_dict(self):
        """State should deserialize from dict."""
        from selfhealing.api.django.views.system_control import SystemState
        
        data = {
            "enabled": False,
            "dry_run": True,
            "disabled_by": "admin",
            "unknown_field": "ignored",  # Should be ignored
        }
        state = SystemState.from_dict(data)
        
        assert state.enabled is False
        assert state.dry_run is True
        assert state.disabled_by == "admin"


class TestFileStateBackend:
    """Tests for FileStateBackend."""
    
    def test_set_and_get(self):
        """Should persist and retrieve state."""
        from selfhealing.core.state_backend import FileStateBackend
        
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStateBackend(tmpdir)
            
            backend.set("test_key", {"enabled": True, "value": 123})
            result = backend.get("test_key")
            
            assert result == {"enabled": True, "value": 123}
    
    def test_get_nonexistent(self):
        """Should return default for nonexistent key."""
        from selfhealing.core.state_backend import FileStateBackend
        
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStateBackend(tmpdir)
            
            result = backend.get("nonexistent", default={"default": True})
            
            assert result == {"default": True}
    
    def test_delete(self):
        """Should delete existing key."""
        from selfhealing.core.state_backend import FileStateBackend
        
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStateBackend(tmpdir)
            backend.set("test_key", {"data": "value"})
            
            result = backend.delete("test_key")
            
            assert result is True
            assert backend.get("test_key") is None
    
    def test_exists(self):
        """Should check key existence."""
        from selfhealing.core.state_backend import FileStateBackend
        
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStateBackend(tmpdir)
            
            assert backend.exists("test_key") is False
            backend.set("test_key", {"data": "value"})
            assert backend.exists("test_key") is True
    
    def test_atomic_write(self):
        """Should write atomically (no partial writes)."""
        from selfhealing.core.state_backend import FileStateBackend
        
        with tempfile.TemporaryDirectory() as tmpdir:
            backend = FileStateBackend(tmpdir)
            
            # Write large data
            large_data = {"items": list(range(10000))}
            backend.set("large_key", large_data)
            
            # No .tmp file should remain
            tmp_files = list(Path(tmpdir).glob("*.tmp"))
            assert len(tmp_files) == 0
            
            # Data should be intact
            result = backend.get("large_key")
            assert result == large_data


class TestMemoryStateBackend:
    """Tests for MemoryStateBackend."""
    
    def test_basic_operations(self):
        """Should support all basic operations."""
        from selfhealing.core.state_backend import MemoryStateBackend
        
        backend = MemoryStateBackend()
        
        # Set
        backend.set("key1", {"value": 1})
        
        # Get
        assert backend.get("key1") == {"value": 1}
        
        # Exists
        assert backend.exists("key1") is True
        assert backend.exists("key2") is False
        
        # Delete
        assert backend.delete("key1") is True
        assert backend.get("key1") is None
    
    def test_thread_safety(self):
        """Should be thread-safe."""
        from selfhealing.core.state_backend import MemoryStateBackend
        
        backend = MemoryStateBackend()
        errors = []
        
        def writer(n):
            try:
                for i in range(100):
                    backend.set(f"key_{n}_{i}", {"value": i})
            except Exception as e:
                errors.append(e)
        
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        assert len(errors) == 0


class TestSystemControlManager:
    """Tests for SystemControlManager."""
    
    @pytest.fixture(autouse=True)
    def reset_singleton(self, monkeypatch):
        """Reset singleton before each test with complete isolation."""
        from selfhealing.api.django.views import system_control as view_module
        from selfhealing.services import system_control as service_module
        from selfhealing.core import state_backend
        from selfhealing.core.state_backend import MemoryStateBackend
        
        # Create a fresh MemoryStateBackend for this test
        fresh_backend = MemoryStateBackend()
        
        # Mock get_state_backend in ALL modules that import it
        monkeypatch.setattr(
            state_backend,
            "get_state_backend",
            lambda: fresh_backend
        )
        monkeypatch.setattr(
            service_module,
            "get_state_backend",
            lambda: fresh_backend
        )
        
        # Also set the backend instance directly
        state_backend._backend_instance = fresh_backend
        
        # Reset ALL singletons
        view_module._system_control = None
        service_module._system_control = None
        service_module.SystemControlManager._instance = None
        
        yield
        
        # Cleanup after test
        view_module._system_control = None
        service_module._system_control = None
        service_module.SystemControlManager._instance = None
        state_backend._backend_instance = None
    
    def test_default_enabled(self):
        """Should be enabled by default."""
        from selfhealing.api.django.views.system_control import (
            SystemControlManager,
            get_system_control,
        )
        from selfhealing.api.django.views import system_control
        
        # Ensure fresh singleton
        SystemControlManager._instance = None
        system_control._system_control = None
        
        manager = get_system_control()
        
        assert manager.is_enabled() is True
        assert manager.is_dry_run() is False
    
    def test_disable_enable(self):
        """Should toggle enabled state."""
        from selfhealing.api.django.views.system_control import (
            SystemControlManager,
        )
        
        # Reset singleton
        SystemControlManager._instance = None
        manager = SystemControlManager()
        
        # Disable
        manager.disable(actor="test", reason="testing")
        assert manager.is_enabled() is False
        
        # Enable
        manager.enable(actor="test")
        assert manager.is_enabled() is True
    
    def test_dry_run_mode(self):
        """Should toggle dry run mode."""
        from selfhealing.api.django.views.system_control import (
            SystemControlManager,
        )
        
        # Reset singleton
        SystemControlManager._instance = None
        manager = SystemControlManager()
        
        # Default: not dry run
        assert manager.is_dry_run() is False
        
        # Enable dry run
        state = manager.enable_dry_run(actor="test")
        assert state.dry_run is True
        assert manager.is_dry_run() is True
        
        # Disable dry run
        state = manager.disable_dry_run(actor="test")
        assert state.dry_run is False
        assert manager.is_dry_run() is False
    
    def test_should_execute_action(self):
        """should_execute_action should check both enabled and dry_run."""
        from selfhealing.api.django.views.system_control import (
            SystemControlManager,
            should_execute_action,
            get_system_control,
        )
        from selfhealing.api.django.views import system_control
        
        # Reset singletons
        SystemControlManager._instance = None
        system_control._system_control = None
        
        manager = get_system_control()
        
        # Enabled + not dry run = execute
        assert should_execute_action() is True
        
        # Enabled + dry run = don't execute
        manager.enable_dry_run()
        assert should_execute_action() is False
        
        # Disabled = don't execute regardless of dry run
        manager.disable(reason="test")
        assert should_execute_action() is False
    
    def test_state_persistence(self):
        """State should persist across manager instances."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {
                "SELFHEALING_STATE_BACKEND": "file",
                "SELFHEALING_STATE_DIR": tmpdir,
            }):
                from selfhealing.api.django.views.system_control import SystemControlManager
                from selfhealing.core.state_backend import reset_state_backend
                from selfhealing.api.django.views import system_control
                
                # First instance
                reset_state_backend()
                SystemControlManager._instance = None
                system_control._system_control = None
                
                manager1 = SystemControlManager()
                manager1.disable(actor="test", reason="persistence test")
                manager1.enable_dry_run(actor="test")
                
                # Second instance (simulating restart)
                reset_state_backend()
                SystemControlManager._instance = None
                system_control._system_control = None
                
                manager2 = SystemControlManager()
                
                # State should be restored
                assert manager2.is_enabled() is False
                assert manager2.is_dry_run() is True


class TestDryRunUsagePattern:
    """Tests for dry run usage patterns in healing logic."""
    
    def test_dry_run_logging_pattern(self):
        """Demonstrate dry run logging pattern."""
        from selfhealing.api.django.views.system_control import (
            is_dry_run,
            should_execute_action,
        )
        
        actions_taken = []
        actions_logged = []
        
        def simulate_healing_action(service_name: str):
            """Simulated healing action with dry run support."""
            if not should_execute_action():
                if is_dry_run():
                    actions_logged.append(f"[DRY RUN] Would open circuit for {service_name}")
                return
            
            actions_taken.append(f"Opened circuit for {service_name}")
        
        # This pattern allows testing without mocking
        # In real usage, is_dry_run() would be True/False based on system state


class TestBackendInfo:
    """Tests for backend information."""
    
    def test_get_backend_info(self):
        """Should return backend information."""
        with patch.dict("os.environ", {"SELFHEALING_STATE_BACKEND": "memory"}):
            from selfhealing.api.django.views.system_control import SystemControlManager
            from selfhealing.core.state_backend import reset_state_backend
            
            reset_state_backend()
            SystemControlManager._instance = None
            
            manager = SystemControlManager()
            info = manager.get_backend_info()
            
            assert "backend_type" in info
            assert info["backend_type"] == "MemoryStateBackend"

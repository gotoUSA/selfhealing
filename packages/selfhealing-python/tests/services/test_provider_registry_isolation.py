"""
Tests for ProviderRegistry Test Isolation Features.

Tests for:
- override_provider: 단일 Provider 임시 교체
- isolated_test_context: 완전 격리된 테스트 컨텍스트
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock


# 직접 모듈에서 import하여 순환 참조 방지
def get_provider_registry():
    """ProviderRegistry를 lazy import하여 반환."""
    from selfhealing.services.factory.registry import ProviderRegistry
    return ProviderRegistry


class TestOverrideProvider:
    """Tests for ProviderRegistry.override_provider context manager."""
    
    def setup_method(self):
        """각 테스트 전 Registry 초기화."""
        ProviderRegistry = get_provider_registry()
        ProviderRegistry.reset()
        ProviderRegistry.configure_for_testing()
    
    def teardown_method(self):
        """각 테스트 후 Registry 정리."""
        ProviderRegistry = get_provider_registry()
        ProviderRegistry.reset()
    
    def test_override_cache_provider_replaces_instance(self):
        """override_provider로 cache를 mock으로 교체할 수 있어야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given: 기존 cache 인스턴스 확보
        original_cache = ProviderRegistry.get_cache()
        
        # Given: Mock cache 생성
        mock_cache = MagicMock()
        mock_cache.get.return_value = "mocked_value"
        mock_cache.health_check.return_value = True
        
        # When: override_provider로 교체
        with ProviderRegistry.override_provider("cache", mock_cache):
            current_cache = ProviderRegistry.get_cache()
            
            # Then: 블록 내에서는 mock 사용
            assert current_cache is mock_cache
            assert current_cache.get("any_key") == "mocked_value"
        
        # Then: 블록 종료 후 원래 인스턴스 복원
        restored_cache = ProviderRegistry.get_cache()
        assert restored_cache is original_cache
        assert restored_cache is not mock_cache
    
    def test_override_queue_provider_replaces_instance(self):
        """override_provider로 queue를 mock으로 교체할 수 있어야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given: 기존 queue 인스턴스 확보
        original_queue = ProviderRegistry.get_queue()
        
        # Given: Mock queue 생성
        mock_queue = MagicMock()
        mock_queue.enqueue.return_value = "task_id_123"
        
        # When: override_provider로 교체
        with ProviderRegistry.override_provider("queue", mock_queue):
            current_queue = ProviderRegistry.get_queue()
            
            # Then: 블록 내에서는 mock 사용
            assert current_queue is mock_queue
            assert current_queue.enqueue("task") == "task_id_123"
        
        # Then: 블록 종료 후 원래 인스턴스 복원
        restored_queue = ProviderRegistry.get_queue()
        assert restored_queue is original_queue
    
    def test_override_with_invalid_provider_type_raises_error(self):
        """잘못된 provider_type은 ValueError 발생."""
        ProviderRegistry = get_provider_registry()
        mock = MagicMock()
        
        with pytest.raises(ValueError) as exc_info:
            with ProviderRegistry.override_provider("invalid", mock):
                pass
        
        assert "Unknown provider_type" in str(exc_info.value)
        assert "invalid" in str(exc_info.value)
    
    def test_override_restores_even_on_exception(self):
        """예외 발생해도 원래 상태로 복원되어야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given
        original_cache = ProviderRegistry.get_cache()
        mock_cache = MagicMock()
        
        # When: 예외 발생
        try:
            with ProviderRegistry.override_provider("cache", mock_cache):
                assert ProviderRegistry.get_cache() is mock_cache
                raise RuntimeError("Intentional error")
        except RuntimeError:
            pass
        
        # Then: 예외에도 불구하고 복원
        assert ProviderRegistry.get_cache() is original_cache
    
    def test_override_with_no_previous_instance(self):
        """기존 인스턴스가 없어도 override 동작해야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given: 깨끗한 상태
        ProviderRegistry.reset()
        ProviderRegistry.register_cache("memory", MagicMock)
        ProviderRegistry.set_defaults(cache="memory")
        
        # cache 인스턴스가 아직 생성되지 않은 상태
        mock_cache = MagicMock()
        
        # When: override
        with ProviderRegistry.override_provider("cache", mock_cache):
            assert ProviderRegistry.get_cache() is mock_cache
        
        # Then: 블록 종료 후 캐시가 비어있어야 함 (이전 인스턴스 없었으므로)
        # 새로 get_cache 호출하면 새 인스턴스 생성
        new_cache = ProviderRegistry.get_cache()
        assert new_cache is not mock_cache


class TestIsolatedTestContext:
    """Tests for ProviderRegistry.isolated_test_context context manager."""
    
    def setup_method(self):
        """각 테스트 전 Registry 초기화."""
        ProviderRegistry = get_provider_registry()
        ProviderRegistry.reset()
        ProviderRegistry.configure_for_testing()
    
    def teardown_method(self):
        """각 테스트 후 Registry 정리."""
        ProviderRegistry = get_provider_registry()
        ProviderRegistry.reset()
    
    def test_isolated_context_starts_with_empty_instances(self):
        """isolated_test_context는 빈 인스턴스로 시작해야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given: 기존 인스턴스 생성
        original_cache = ProviderRegistry.get_cache()
        assert original_cache is not None
        
        # When: 격리된 컨텍스트 진입
        with ProviderRegistry.isolated_test_context() as registry:
            # Then: 인스턴스가 비어있음
            assert len(registry._cache_instances) == 0
            assert len(registry._queue_instances) == 0
        
        # Then: 컨텍스트 종료 후 원래 인스턴스 복원
        restored_cache = ProviderRegistry.get_cache()
        assert restored_cache is original_cache
    
    def test_isolated_context_changes_dont_leak(self):
        """격리된 컨텍스트 내 변경은 외부에 영향 없어야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given: 원래 기본값 저장
        original_defaults = ProviderRegistry.get_defaults()
        
        # When: 격리된 컨텍스트에서 설정 변경
        with ProviderRegistry.isolated_test_context() as registry:
            registry.set_defaults(cache="test_cache", queue="test_queue")
            
            # Then: 컨텍스트 내에서는 변경됨
            assert registry.get_defaults()["cache"] == "test_cache"
            assert registry.get_defaults()["queue"] == "test_queue"
        
        # Then: 컨텍스트 종료 후 원래 값 복원
        restored_defaults = ProviderRegistry.get_defaults()
        assert restored_defaults["cache"] == original_defaults["cache"]
        assert restored_defaults["queue"] == original_defaults["queue"]
    
    def test_isolated_context_restores_on_exception(self):
        """예외 발생해도 원래 상태로 복원되어야 함."""
        ProviderRegistry = get_provider_registry()
        
        # Given
        original_cache = ProviderRegistry.get_cache()
        
        # When: 예외 발생
        try:
            with ProviderRegistry.isolated_test_context() as registry:
                registry.set_defaults(cache="error_cache")
                raise ValueError("Intentional error")
        except ValueError:
            pass
        
        # Then: 복원
        assert ProviderRegistry.get_cache() is original_cache
    
    def test_isolated_context_returns_registry_class(self):
        """isolated_test_context는 ProviderRegistry 클래스를 반환해야 함."""
        ProviderRegistry = get_provider_registry()
        
        with ProviderRegistry.isolated_test_context() as registry:
            assert registry is ProviderRegistry


class TestProviderRegistryIntegration:
    """ProviderRegistry 전체 통합 테스트."""
    
    def test_nested_contexts_work_correctly(self):
        """중첩된 컨텍스트가 올바르게 동작해야 함."""
        ProviderRegistry = get_provider_registry()
        ProviderRegistry.reset()
        ProviderRegistry.configure_for_testing()
        
        # 최외곽 상태 저장
        outer_cache = ProviderRegistry.get_cache()
        
        mock1 = MagicMock(name="mock1")
        mock2 = MagicMock(name="mock2")
        
        with ProviderRegistry.override_provider("cache", mock1):
            assert ProviderRegistry.get_cache() is mock1
            
            # 중첩 override
            with ProviderRegistry.override_provider("cache", mock2):
                assert ProviderRegistry.get_cache() is mock2
            
            # 내부 컨텍스트 종료 후
            assert ProviderRegistry.get_cache() is mock1
        
        # 외부 컨텍스트 종료 후
        assert ProviderRegistry.get_cache() is outer_cache
        
        ProviderRegistry.reset()

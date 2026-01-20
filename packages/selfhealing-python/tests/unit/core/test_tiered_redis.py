"""
Tiered Redis Provider Unit Tests.

TieredRedisProvider 및 관련 함수들을 테스트합니다.

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import os
import pytest
from unittest.mock import patch, MagicMock


class TestTieredRedisProviderInit:
    """TieredRedisProvider 초기화 테스트."""
    
    def setup_method(self):
        from selfhealing.core.tiered_redis import reset_tiered_redis_provider
        reset_tiered_redis_provider()
    
    def teardown_method(self):
        from selfhealing.core.tiered_redis import reset_tiered_redis_provider
        reset_tiered_redis_provider()
    
    def test_default_urls(self):
        """기본 URL 설정."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        with patch.dict(os.environ, {}, clear=False):
            # 환경변수 없을 때 기본값
            provider = TieredRedisProvider()
            assert "localhost" in provider.local_url
            assert provider.global_url == provider.local_url
    
    def test_custom_urls(self):
        """커스텀 URL 설정."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        assert provider.local_url == "redis://local:6379/0"
        assert provider.global_url == "redis://global:6379/0"
    
    @patch.dict(os.environ, {
        "REDIS_URL": "redis://env-local:6379/0",
        "REDIS_GLOBAL_URL": "redis://env-global:6379/0",
    }, clear=False)
    def test_env_var_urls(self):
        """환경변수에서 URL 로드."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        provider = TieredRedisProvider()
        
        assert provider.local_url == "redis://env-local:6379/0"
        assert provider.global_url == "redis://env-global:6379/0"
    
    @patch.dict(os.environ, {
        "REDIS_URL": "redis://env-local:6379/0",
    }, clear=False)
    def test_global_fallback_to_local(self):
        """REDIS_GLOBAL_URL 없으면 REDIS_URL 사용."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        # REDIS_GLOBAL_URL 환경변수 제거
        env = os.environ.copy()
        env.pop("REDIS_GLOBAL_URL", None)
        
        with patch.dict(os.environ, env, clear=True):
            provider = TieredRedisProvider()
            assert provider.local_url == provider.global_url


class TestRedisScope:
    """RedisScope Enum 테스트."""
    
    def test_scope_values(self):
        """RedisScope 값 확인."""
        from selfhealing.core.tiered_redis import RedisScope
        
        assert RedisScope.LOCAL.value == "local"
        assert RedisScope.GLOBAL.value == "global"


class TestTieredRedisProviderIsTiered:
    """is_tiered 속성 테스트."""
    
    def test_is_tiered_same_url(self):
        """동일 URL일 때 is_tiered=False."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        provider = TieredRedisProvider(
            local_url="redis://same:6379/0",
            global_url="redis://same:6379/0",
        )
        
        assert provider.is_tiered is False
    
    def test_is_tiered_different_url(self):
        """다른 URL일 때 is_tiered=True."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        assert provider.is_tiered is True


class TestTieredRedisProviderSingleton:
    """TieredRedisProvider 싱글톤 테스트."""
    
    def setup_method(self):
        from selfhealing.core.tiered_redis import reset_tiered_redis_provider
        reset_tiered_redis_provider()
    
    def teardown_method(self):
        from selfhealing.core.tiered_redis import reset_tiered_redis_provider
        reset_tiered_redis_provider()
    
    def test_singleton_returns_same_instance(self):
        """싱글톤이 같은 인스턴스 반환."""
        from selfhealing.core.tiered_redis import (
            get_tiered_redis_provider,
            reset_tiered_redis_provider,
        )
        
        reset_tiered_redis_provider()
        p1 = get_tiered_redis_provider()
        p2 = get_tiered_redis_provider()
        assert p1 is p2
    
    def test_reset_clears_singleton(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.core.tiered_redis import (
            get_tiered_redis_provider,
            reset_tiered_redis_provider,
        )
        
        p1 = get_tiered_redis_provider()
        reset_tiered_redis_provider()
        p2 = get_tiered_redis_provider()
        assert p1 is not p2


class TestTieredRedisProviderGetRedis:
    """get_redis 메서드 테스트 (모킹)."""
    
    def test_get_redis_local_scope(self):
        """LOCAL scope로 클라이언트 가져오기."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_from_url.return_value = mock_client
            
            client = provider.get_redis(RedisScope.LOCAL)
            
            mock_from_url.assert_called_with("redis://local:6379/0")
            assert client is mock_client
    
    def test_get_redis_global_scope(self):
        """GLOBAL scope로 클라이언트 가져오기."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_from_url.return_value = mock_client
            
            client = provider.get_redis(RedisScope.GLOBAL)
            
            mock_from_url.assert_called_with("redis://global:6379/0")
            assert client is mock_client
    
    def test_global_reuses_local_when_same_url(self):
        """동일 URL일 때 GLOBAL이 LOCAL 클라이언트 재사용."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url="redis://same:6379/0",
            global_url="redis://same:6379/0",
        )
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_from_url.return_value = mock_client
            
            local_client = provider.get_redis(RedisScope.LOCAL)
            global_client = provider.get_redis(RedisScope.GLOBAL)
            
            # from_url은 한 번만 호출되어야 함
            assert mock_from_url.call_count == 1
            assert local_client is global_client
    
    def test_lazy_initialization(self):
        """지연 초기화 확인."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        with patch("redis.from_url") as mock_from_url:
            provider = TieredRedisProvider(
                local_url="redis://local:6379/0",
                global_url="redis://global:6379/0",
            )
            
            # 생성 시점에는 호출 안 됨
            mock_from_url.assert_not_called()
            
            # get_redis 호출 시 초기화
            provider.get_redis(RedisScope.LOCAL)
            assert mock_from_url.call_count == 1


class TestTieredRedisProviderHealthCheck:
    """health_check 메서드 테스트."""
    
    def test_health_check_both_healthy(self):
        """양쪽 모두 정상일 때."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_from_url.return_value = mock_client
            
            result = provider.health_check()
            
            assert result["local"]["status"] == "healthy"
            assert result["global"]["status"] == "healthy"
    
    def test_health_check_local_only(self):
        """LOCAL만 체크."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider()
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_client.ping.return_value = True
            mock_from_url.return_value = mock_client
            
            result = provider.health_check(scope=RedisScope.LOCAL)
            
            assert "local" in result
            assert "global" not in result
    
    def test_health_check_failure(self):
        """연결 실패 시."""
        from selfhealing.core.tiered_redis import TieredRedisProvider
        
        provider = TieredRedisProvider()
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_client.ping.side_effect = Exception("Connection refused")
            mock_from_url.return_value = mock_client
            
            result = provider.health_check()
            
            assert result["local"]["status"] == "unhealthy"
            assert "Connection refused" in result["local"]["error"]


class TestTieredRedisProviderClose:
    """close 메서드 테스트."""
    
    def test_close_both_clients(self):
        """양쪽 클라이언트 모두 닫기."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider(
            local_url="redis://local:6379/0",
            global_url="redis://global:6379/0",
        )
        
        with patch("redis.from_url") as mock_from_url:
            mock_local = MagicMock()
            mock_global = MagicMock()
            mock_from_url.side_effect = [mock_local, mock_global]
            
            # 클라이언트 생성
            provider.get_redis(RedisScope.LOCAL)
            provider.get_redis(RedisScope.GLOBAL)
            
            # 닫기
            provider.close()
            
            mock_local.close.assert_called_once()
            mock_global.close.assert_called_once()
    
    def test_close_handles_errors(self):
        """close 중 에러 발생해도 예외 발생 안 함."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        
        provider = TieredRedisProvider()
        
        with patch("redis.from_url") as mock_from_url:
            mock_client = MagicMock()
            mock_client.close.side_effect = Exception("Close error")
            mock_from_url.return_value = mock_client
            
            provider.get_redis(RedisScope.LOCAL)
            
            # 예외 없이 완료
            provider.close()

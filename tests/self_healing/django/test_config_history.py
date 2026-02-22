"""
Config History & Rollback Tests.

설정 변경 이력 관리 및 롤백 기능 테스트.
버전 관리, 이력 조회, 롤백 처리를 검증합니다.
"""

import pytest
import time
import json
from unittest.mock import patch, MagicMock

from selfhealing.services.config_history import (
    ConfigVersion,
    ConfigHistoryService,
    get_config_history_service,
    reset_config_history_service,
    CONFIG_HISTORY_KEY,
    CONFIG_VERSION_COUNTER_KEY,
    CONFIG_CURRENT_KEY,
    MAX_HISTORY_ENTRIES,
)


# =============================================================================
# ConfigVersion Tests
# =============================================================================


class TestConfigVersion:
    """ConfigVersion 데이터클래스 테스트."""
    
    def test_config_version_creation(self):
        """ConfigVersion 생성 테스트."""
        version = ConfigVersion(
            version=1,
            timestamp=1703318400.0,
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="admin",
            reason="Increase threshold",
            hash="abc123",
        )
        
        assert version.version == 1
        assert version.timestamp == 1703318400.0
        assert version.config_type == "circuit_breaker"
        assert version.values == {"failure_threshold": 10}
        assert version.changed_by == "admin"
        assert version.reason == "Increase threshold"
        assert version.hash == "abc123"
    
    def test_config_version_to_dict(self):
        """ConfigVersion to_dict 변환 테스트."""
        version = ConfigVersion(
            version=1,
            timestamp=1703318400.0,
            config_type="dlq",
            values={"max_retries": 3},
            changed_by="system",
            reason="Auto adjustment",
            hash="def456",
        )
        
        data = version.to_dict()
        
        assert isinstance(data, dict)
        assert data["version"] == 1
        assert data["config_type"] == "dlq"
        assert data["values"] == {"max_retries": 3}
    
    def test_config_version_from_dict(self):
        """ConfigVersion from_dict 변환 테스트."""
        data = {
            "version": 5,
            "timestamp": 1703318500.0,
            "config_type": "retry",
            "values": {"max_attempts": 5},
            "changed_by": "admin",
            "reason": "Retry policy update",
            "hash": "ghi789",
        }
        
        version = ConfigVersion.from_dict(data)
        
        assert version.version == 5
        assert version.config_type == "retry"
        assert version.values == {"max_attempts": 5}
    
    def test_config_version_roundtrip(self):
        """to_dict -> from_dict 왕복 변환 테스트."""
        original = ConfigVersion(
            version=10,
            timestamp=time.time(),
            config_type="rate_limit",
            values={"max_requests_per_minute": 100, "window_seconds": 60},
            changed_by="operator",
            reason="Rate limit adjustment",
            hash="xyz000",
        )
        
        data = original.to_dict()
        restored = ConfigVersion.from_dict(data)
        
        assert restored.version == original.version
        assert restored.config_type == original.config_type
        assert restored.values == original.values
        assert restored.hash == original.hash


# =============================================================================
# ConfigHistoryService Unit Tests (Redis Mocked)
# =============================================================================


class TestConfigHistoryServiceValidation:
    """ConfigHistoryService 유효성 검사 테스트."""
    
    def test_is_valid_config_type_valid(self):
        """유효한 config_type 검사."""
        service = ConfigHistoryService()
        
        assert service.is_valid_config_type("circuit_breaker") is True
        assert service.is_valid_config_type("dlq") is True
        assert service.is_valid_config_type("retry") is True
        assert service.is_valid_config_type("rate_limit") is True
    
    def test_is_valid_config_type_invalid(self):
        """유효하지 않은 config_type 검사."""
        service = ConfigHistoryService()
        
        assert service.is_valid_config_type("invalid_type") is False
        assert service.is_valid_config_type("") is False
        assert service.is_valid_config_type("CIRCUIT_BREAKER") is False  # 대소문자
    
    def test_supported_config_types_list(self):
        """지원되는 config_type 목록 확인."""
        service = ConfigHistoryService()
        
        expected_types = [
            "circuit_breaker",
            "dlq",
            "retry",
            "sla",
            "slo",
            "rate_limit",
            "security",
            "idempotency",
            "notification",
            "forensic",
            "metrics",
            "error_budget",
        ]
        
        for config_type in expected_types:
            assert service.is_valid_config_type(config_type) is True


class TestConfigHistoryServiceHashComputation:
    """ConfigHistoryService 해시 계산 테스트."""
    
    def test_compute_hash_consistent(self):
        """동일한 값은 동일한 해시를 생성해야 함."""
        service = ConfigHistoryService()
        
        values = {"key1": "value1", "key2": 100}
        
        hash1 = service._compute_hash(values)
        hash2 = service._compute_hash(values)
        
        assert hash1 == hash2
    
    def test_compute_hash_different_values(self):
        """다른 값은 다른 해시를 생성해야 함."""
        service = ConfigHistoryService()
        
        values1 = {"key1": "value1"}
        values2 = {"key1": "value2"}
        
        hash1 = service._compute_hash(values1)
        hash2 = service._compute_hash(values2)
        
        assert hash1 != hash2
    
    def test_compute_hash_order_independent(self):
        """키 순서와 관계없이 동일한 해시를 생성해야 함."""
        service = ConfigHistoryService()
        
        values1 = {"a": 1, "b": 2, "c": 3}
        values2 = {"c": 3, "a": 1, "b": 2}
        
        hash1 = service._compute_hash(values1)
        hash2 = service._compute_hash(values2)
        
        assert hash1 == hash2
    
    def test_compute_hash_length(self):
        """해시 길이는 16자여야 함."""
        service = ConfigHistoryService()
        
        hash_value = service._compute_hash({"test": "value"})
        
        assert len(hash_value) == 16


class TestConfigHistoryServiceRedisUnavailable:
    """Redis 불가 시 Graceful Degradation 테스트."""
    
    def test_save_version_returns_none_when_redis_unavailable(self):
        """Redis 불가 시 save_version은 None 반환."""
        service = ConfigHistoryService()
        service._redis_client = None
        
        result = service.save_version(
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="admin",
        )
        
        assert result is None
    
    def test_get_history_returns_empty_when_redis_unavailable(self):
        """Redis 불가 시 get_history는 빈 목록 반환."""
        service = ConfigHistoryService()
        service._redis_client = None
        
        result = service.get_history("circuit_breaker")
        
        assert result == []
    
    def test_get_current_version_returns_none_when_redis_unavailable(self):
        """Redis 불가 시 get_current_version은 None 반환."""
        service = ConfigHistoryService()
        service._redis_client = None
        
        result = service.get_current_version("circuit_breaker")
        
        assert result is None
    
    def test_rollback_returns_none_when_redis_unavailable(self):
        """Redis 불가 시 rollback은 None 반환."""
        service = ConfigHistoryService()
        service._redis_client = None
        
        result = service.rollback(
            config_type="circuit_breaker",
            target_version=1,
            rolled_back_by="admin",
        )
        
        assert result is None
    
    def test_get_version_count_returns_zero_when_redis_unavailable(self):
        """Redis 불가 시 get_version_count는 0 반환."""
        service = ConfigHistoryService()
        service._redis_client = None
        
        result = service.get_version_count("circuit_breaker")
        
        assert result == 0


class TestConfigHistoryServiceWithMockedRedis:
    """Mock Redis를 사용한 ConfigHistoryService 테스트."""
    
    def test_save_version_success(self):
        """버전 저장 성공 테스트."""
        mock_redis = MagicMock()
        mock_redis.incr.return_value = 1
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.save_version(
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="admin",
            reason="Test update",
        )
        
        assert result is not None
        assert result.version == 1
        assert result.config_type == "circuit_breaker"
        assert result.values == {"failure_threshold": 10}
        assert result.changed_by == "admin"
        assert result.reason == "Test update"
        
        # Redis 호출 확인
        mock_redis.incr.assert_called_once()
        mock_pipe.lpush.assert_called_once()
        mock_pipe.ltrim.assert_called_once()
        mock_pipe.set.assert_called_once()
        mock_pipe.execute.assert_called_once()
    
    def test_save_version_invalid_config_type(self):
        """유효하지 않은 config_type 저장 시도."""
        service = ConfigHistoryService()
        
        result = service.save_version(
            config_type="invalid_type",
            values={"key": "value"},
            changed_by="admin",
        )
        
        assert result is None
    
    def test_get_history_success(self):
        """히스토리 조회 성공 테스트."""
        mock_redis = MagicMock()
        
        # Redis lrange 반환값 설정
        entries = [
            json.dumps({
                "version": 2,
                "timestamp": 1703318500.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 15},
                "changed_by": "admin",
                "reason": "Update 2",
                "hash": "hash2",
            }).encode('utf-8'),
            json.dumps({
                "version": 1,
                "timestamp": 1703318400.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 10},
                "changed_by": "admin",
                "reason": "Update 1",
                "hash": "hash1",
            }).encode('utf-8'),
        ]
        mock_redis.lrange.return_value = entries
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_history("circuit_breaker", limit=10)
        
        assert len(result) == 2
        assert result[0].version == 2
        assert result[1].version == 1
    
    def test_get_version_found(self):
        """특정 버전 조회 성공 테스트."""
        mock_redis = MagicMock()
        
        entries = [
            json.dumps({
                "version": 3,
                "timestamp": 1703318600.0,
                "config_type": "dlq",
                "values": {"max_retries": 5},
                "changed_by": "admin",
                "reason": "Update 3",
                "hash": "hash3",
            }).encode('utf-8'),
            json.dumps({
                "version": 2,
                "timestamp": 1703318500.0,
                "config_type": "dlq",
                "values": {"max_retries": 3},
                "changed_by": "admin",
                "reason": "Update 2",
                "hash": "hash2",
            }).encode('utf-8'),
        ]
        mock_redis.lrange.return_value = entries
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_version("dlq", 2)
        
        assert result is not None
        assert result.version == 2
        assert result.values == {"max_retries": 3}
    
    def test_get_version_not_found(self):
        """존재하지 않는 버전 조회."""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = []
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_version("dlq", 999)
        
        assert result is None
    
    def test_compare_versions_success(self):
        """버전 비교 성공 테스트."""
        mock_redis = MagicMock()
        
        entries = [
            json.dumps({
                "version": 2,
                "timestamp": 1703318500.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 15, "recovery_timeout": 60},
                "changed_by": "admin",
                "reason": "Update 2",
                "hash": "hash2",
            }).encode('utf-8'),
            json.dumps({
                "version": 1,
                "timestamp": 1703318400.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 10, "recovery_timeout": 30},
                "changed_by": "admin",
                "reason": "Update 1",
                "hash": "hash1",
            }).encode('utf-8'),
        ]
        mock_redis.lrange.return_value = entries
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.compare_versions("circuit_breaker", 1, 2)
        
        assert result is not None
        assert result["version_a"] == 1
        assert result["version_b"] == 2
        assert "failure_threshold" in result["changes"]
        assert result["changes"]["failure_threshold"]["from"] == 10
        assert result["changes"]["failure_threshold"]["to"] == 15
    
    def test_compare_versions_not_found(self):
        """버전 비교 시 버전 없음."""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = []
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.compare_versions("circuit_breaker", 1, 2)
        
        assert result is None
    
    def test_get_current_version_success(self):
        """현재 버전 조회 성공."""
        mock_redis = MagicMock()
        
        current_data = json.dumps({
            "version": 5,
            "timestamp": 1703318800.0,
            "config_type": "retry",
            "values": {"max_attempts": 5},
            "changed_by": "operator",
            "reason": "Latest update",
            "hash": "hash5",
        }).encode('utf-8')
        mock_redis.get.return_value = current_data
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_current_version("retry")
        
        assert result is not None
        assert result.version == 5
        assert result.config_type == "retry"
    
    def test_get_version_count(self):
        """버전 수 조회."""
        mock_redis = MagicMock()
        mock_redis.llen.return_value = 25
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_version_count("circuit_breaker")
        
        assert result == 25
    
    def test_clear_history(self):
        """히스토리 삭제."""
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.clear_history("circuit_breaker")
        
        assert result is True
        assert mock_pipe.delete.call_count == 3
        mock_pipe.execute.assert_called_once()


class TestConfigHistoryServiceRollback:
    """롤백 기능 테스트."""
    
    def test_rollback_success(self):
        """롤백 성공 테스트."""
        mock_redis = MagicMock()
        
        # get_version 호출 시 반환값
        entries = [
            json.dumps({
                "version": 3,
                "timestamp": 1703318600.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 20},
                "changed_by": "admin",
                "reason": "Update 3",
                "hash": "hash3",
            }).encode('utf-8'),
            json.dumps({
                "version": 2,
                "timestamp": 1703318500.0,
                "config_type": "circuit_breaker",
                "values": {"failure_threshold": 10},
                "changed_by": "admin",
                "reason": "Update 2",
                "hash": "hash2",
            }).encode('utf-8'),
        ]
        mock_redis.lrange.return_value = entries
        mock_redis.incr.return_value = 4
        mock_pipe = MagicMock()
        mock_redis.pipeline.return_value = mock_pipe
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.rollback(
            config_type="circuit_breaker",
            target_version=2,
            rolled_back_by="operator",
        )
        
        assert result is not None
        assert result.version == 4  # 새 버전 번호
        assert result.values == {"failure_threshold": 10}  # 롤백된 값
        assert "Rollback to version 2" in result.reason
    
    def test_rollback_version_not_found(self):
        """존재하지 않는 버전으로 롤백 시도."""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = []
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.rollback(
            config_type="circuit_breaker",
            target_version=999,
            rolled_back_by="operator",
        )
        
        assert result is None


# =============================================================================
# Singleton Tests
# =============================================================================


class TestConfigHistoryServiceSingleton:
    """싱글톤 패턴 테스트."""
    
    def test_get_config_history_service_returns_singleton(self):
        """get_config_history_service는 동일한 인스턴스를 반환해야 함."""
        reset_config_history_service()
        
        service1 = get_config_history_service()
        service2 = get_config_history_service()
        
        assert service1 is service2
    
    def test_reset_config_history_service(self):
        """reset_config_history_service는 새 인스턴스를 생성해야 함."""
        service1 = get_config_history_service()
        reset_config_history_service()
        service2 = get_config_history_service()
        
        assert service1 is not service2


# =============================================================================
# Redis Key Pattern Tests
# =============================================================================


class TestRedisKeyPatterns:
    """Redis 키 패턴 테스트."""
    
    def test_history_key_format(self):
        """히스토리 키 포맷 확인."""
        key = CONFIG_HISTORY_KEY.format(config_type="circuit_breaker")
        assert key == "selfhealing:config:history:circuit_breaker"
    
    def test_version_counter_key_format(self):
        """버전 카운터 키 포맷 확인."""
        key = CONFIG_VERSION_COUNTER_KEY.format(config_type="dlq")
        assert key == "selfhealing:config:version:dlq"
    
    def test_current_key_format(self):
        """현재 설정 키 포맷 확인."""
        key = CONFIG_CURRENT_KEY.format(config_type="retry")
        assert key == "selfhealing:config:current:retry"
    
    def test_max_history_entries(self):
        """최대 히스토리 항목 수 확인."""
        assert MAX_HISTORY_ENTRIES == 50


# =============================================================================
# API View Tests (Unit Tests with Mocking - No DB Required)
# =============================================================================


class TestConfigHistoryAPIViewsUnit:
    """Config History API View 단위 테스트 (DB 불필요, View 직접 호출)."""
    
    @pytest.fixture
    def mock_request_factory(self):
        """Mock Request Factory."""
        from rest_framework.test import APIRequestFactory
        return APIRequestFactory()
    
    @pytest.fixture
    def mock_user(self):
        """Mock 관리자 사용자."""
        user = MagicMock()
        user.username = "test_admin"
        user.is_authenticated = True
        user.is_superuser = True
        user.has_perm = MagicMock(return_value=True)
        return user
    
    def test_config_history_view_invalid_config_type(self, mock_request_factory, mock_user):
        """유효하지 않은 config_type 조회."""
        from selfhealing.api.django.views.config_history import ConfigHistoryView
        
        request = mock_request_factory.get("/api/self-healing/config/invalid_type/history/")
        request.user = mock_user
        
        view = ConfigHistoryView.as_view()
        response = view(request, config_type="invalid_type")
        
        assert response.status_code == 400
        assert "Invalid config_type" in response.data.get("error", "")
    
    def test_config_history_view_valid_config_type(self, mock_request_factory, mock_user):
        """유효한 config_type 조회."""
        from selfhealing.api.django.views.config_history import ConfigHistoryView
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.get_history.return_value = []
            mock_service.get_current_version.return_value = None
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.get("/api/self-healing/config/circuit_breaker/history/")
            request.user = mock_user
            
            view = ConfigHistoryView.as_view()
            response = view(request, config_type="circuit_breaker")
            
            assert response.status_code == 200
            assert response.data["status"] == "success"
            assert response.data["config_type"] == "circuit_breaker"
    
    def test_config_rollback_view_missing_version(self, mock_request_factory, mock_user):
        """버전 누락 시 롤백 요청."""
        from selfhealing.api.django.views.config_history import ConfigRollbackView
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.post(
                "/api/self-healing/config/circuit_breaker/rollback/",
                data={},
                format="json",
            )
            request.user = mock_user
            
            view = ConfigRollbackView.as_view()
            response = view(request, config_type="circuit_breaker")
            
            assert response.status_code == 400
            assert "version is required" in response.data.get("error", "")
    
    def test_config_rollback_view_version_not_found(self, mock_request_factory, mock_user):
        """존재하지 않는 버전으로 롤백."""
        from selfhealing.api.django.views.config_history import ConfigRollbackView
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.get_version.return_value = None
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.post(
                "/api/self-healing/config/circuit_breaker/rollback/",
                data={"version": 999},
                format="json",
            )
            request.user = mock_user
            
            view = ConfigRollbackView.as_view()
            response = view(request, config_type="circuit_breaker")
            
            assert response.status_code == 404
    
    def test_config_rollback_view_success(self, mock_request_factory, mock_user):
        """롤백 성공."""
        from selfhealing.api.django.views.config_history import ConfigRollbackView
        
        target_version = ConfigVersion(
            version=2,
            timestamp=1703318500.0,
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="admin",
            reason="Original",
            hash="hash2",
        )
        
        rolled_back_version = ConfigVersion(
            version=5,
            timestamp=time.time(),
            config_type="circuit_breaker",
            values={"failure_threshold": 10},
            changed_by="test_admin",
            reason="Rollback to version 2",
            hash="hash2",
        )
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.get_version.return_value = target_version
            mock_service.rollback.return_value = rolled_back_version
            mock_get_service.return_value = mock_service
            
            with patch("selfhealing.api.django.views.config_history.get_runtime_config_manager") as mock_get_manager:
                mock_manager = MagicMock()
                mock_get_manager.return_value = mock_manager
                
                request = mock_request_factory.post(
                    "/api/self-healing/config/circuit_breaker/rollback/",
                    data={"version": 2},
                    format="json",
                )
                request.user = mock_user
                
                view = ConfigRollbackView.as_view()
                response = view(request, config_type="circuit_breaker")
                
                assert response.status_code == 200
                assert response.data["status"] == "success"
                assert response.data["rolled_back_to"] == 2
                assert response.data["new_version"] == 5
    
    def test_config_compare_view_missing_params(self, mock_request_factory, mock_user):
        """버전 비교 시 파라미터 누락."""
        from selfhealing.api.django.views.config_history import ConfigCompareView
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.get(
                "/api/self-healing/config/circuit_breaker/compare/"
            )
            request.user = mock_user
            
            view = ConfigCompareView.as_view()
            response = view(request, config_type="circuit_breaker")
            
            assert response.status_code == 400
            assert "version_a and version_b" in response.data.get("error", "")
    
    def test_config_compare_view_success(self, mock_request_factory, mock_user):
        """버전 비교 성공."""
        from selfhealing.api.django.views.config_history import ConfigCompareView
        
        comparison = {
            "version_a": 1,
            "version_b": 3,
            "config_type": "circuit_breaker",
            "changes": {
                "failure_threshold": {"from": 5, "to": 15},
            },
        }
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.compare_versions.return_value = comparison
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.get(
                "/api/self-healing/config/circuit_breaker/compare/?version_a=1&version_b=3"
            )
            request.user = mock_user
            
            view = ConfigCompareView.as_view()
            response = view(request, config_type="circuit_breaker")
            
            assert response.status_code == 200
            assert response.data["status"] == "success"
            assert response.data["comparison"]["changes"]["failure_threshold"]["from"] == 5
    
    def test_config_version_detail_view_success(self, mock_request_factory, mock_user):
        """특정 버전 상세 조회 성공."""
        from selfhealing.api.django.views.config_history import ConfigVersionDetailView
        
        version = ConfigVersion(
            version=3,
            timestamp=1703318600.0,
            config_type="dlq",
            values={"max_retries": 5, "expiry_hours": 72},
            changed_by="admin",
            reason="DLQ update",
            hash="hash3",
        )
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.get_version.return_value = version
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.get("/api/self-healing/config/dlq/history/3/")
            request.user = mock_user
            
            view = ConfigVersionDetailView.as_view()
            response = view(request, config_type="dlq", version=3)
            
            assert response.status_code == 200
            assert response.data["status"] == "success"
            assert response.data["version"]["version"] == 3
            assert response.data["version"]["values"]["max_retries"] == 5
    
    def test_config_version_detail_view_not_found(self, mock_request_factory, mock_user):
        """존재하지 않는 버전 상세 조회."""
        from selfhealing.api.django.views.config_history import ConfigVersionDetailView
        
        with patch("selfhealing.api.django.views.config_history.get_config_history_service") as mock_get_service:
            mock_service = MagicMock()
            mock_service.is_valid_config_type.return_value = True
            mock_service.get_version.return_value = None
            mock_get_service.return_value = mock_service
            
            request = mock_request_factory.get("/api/self-healing/config/dlq/history/999/")
            request.user = mock_user
            
            view = ConfigVersionDetailView.as_view()
            response = view(request, config_type="dlq", version=999)
            
            assert response.status_code == 404


# =============================================================================
# Error Handling Tests
# =============================================================================


class TestConfigHistoryErrorHandling:
    """에러 처리 테스트."""
    
    def test_redis_exception_on_save(self):
        """Redis 예외 발생 시 save_version 처리."""
        mock_redis = MagicMock()
        mock_redis.incr.side_effect = Exception("Redis connection error")
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.save_version(
            config_type="circuit_breaker",
            values={"key": "value"},
            changed_by="admin",
        )
        
        assert result is None
    
    def test_redis_exception_on_get_history(self):
        """Redis 예외 발생 시 get_history 처리."""
        mock_redis = MagicMock()
        mock_redis.lrange.side_effect = Exception("Redis timeout")
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_history("circuit_breaker")
        
        assert result == []
    
    def test_invalid_json_in_history(self):
        """히스토리에 잘못된 JSON이 있는 경우."""
        mock_redis = MagicMock()
        mock_redis.lrange.return_value = [
            b"invalid json",
            json.dumps({
                "version": 1,
                "timestamp": 1703318400.0,
                "config_type": "circuit_breaker",
                "values": {"key": "value"},
                "changed_by": "admin",
                "reason": "Test",
                "hash": "hash1",
            }).encode('utf-8'),
        ]
        
        service = ConfigHistoryService()
        service._redis_client = mock_redis
        
        result = service.get_history("circuit_breaker")
        
        # 잘못된 항목은 건너뛰고 유효한 항목만 반환
        assert len(result) == 1
        assert result[0].version == 1

"""
Unit tests for HealthCheckService.

Phase 3 - Health Check 비즈니스 로직 서비스 테스트.
"""

import pytest
from unittest.mock import MagicMock, patch

from selfhealing.services.health_check import (
    HealthCheckService,
    HealthStatus,
    ReadinessStatus,
    PoolHealthStatus,
    DatabaseCheck,
    PoolInfo,
    get_health_check_service,
)


class TestHealthCheckService:
    """HealthCheckService 단위 테스트."""

    def setup_method(self):
        """각 테스트 전에 서비스 인스턴스 생성."""
        self.service = HealthCheckService()

    # =========================================================================
    # check_database Tests
    # =========================================================================

    @patch("django.db.connections")
    def test_check_database_success(self, mock_connections):
        """정상 DB 연결 확인."""
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_conn.cursor.return_value = mock_cursor
        mock_connections.__getitem__.return_value = mock_conn

        result = self.service.check_database("default")

        assert isinstance(result, DatabaseCheck)
        assert result.alias == "default"
        assert result.vendor == "postgresql"
        assert result.is_connected is True
        assert result.is_usable is True
        assert result.error is None
        assert result.latency_ms is not None
        assert result.latency_ms >= 0

    @patch("django.db.connections")
    def test_check_database_connection_failure(self, mock_connections):
        """DB 연결 실패 확인."""
        mock_connections.__getitem__.side_effect = Exception("Connection refused")

        result = self.service.check_database("default")

        assert isinstance(result, DatabaseCheck)
        assert result.alias == "default"
        assert result.is_connected is False
        assert result.is_usable is False
        assert result.error == "Connection refused"

    # =========================================================================
    # check_all_databases Tests
    # =========================================================================

    @patch("django.db.connections")
    def test_check_all_databases(self, mock_connections):
        """모든 DB 연결 확인."""
        mock_connections.__iter__ = MagicMock(return_value=iter(["default", "replica"]))
        
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_conn.cursor.return_value = mock_cursor
        mock_connections.__getitem__.return_value = mock_conn

        results = self.service.check_all_databases()

        assert len(results) == 2
        assert all(isinstance(r, DatabaseCheck) for r in results)
        assert results[0].alias == "default"
        assert results[1].alias == "replica"

    # =========================================================================
    # check_connection_pool Tests
    # =========================================================================

    @patch("django.db.connections")
    def test_check_connection_pool_healthy(self, mock_connections):
        """정상 커넥션 풀 확인."""
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_connections.__getitem__.return_value = mock_conn

        result = self.service.check_connection_pool("default")

        assert isinstance(result, PoolInfo)
        assert result.alias == "default"
        assert result.vendor == "postgresql"
        assert result.is_usable is True
        assert result.status == "healthy"
        assert result.error is None

    @patch("django.db.connections")
    def test_check_connection_pool_degraded(self, mock_connections):
        """비정상 커넥션 풀 확인."""
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = False
        mock_connections.__getitem__.return_value = mock_conn

        result = self.service.check_connection_pool("default")

        assert isinstance(result, PoolInfo)
        assert result.is_usable is False
        assert result.status == "degraded"

    @patch("django.db.connections")
    def test_check_connection_pool_error(self, mock_connections):
        """커넥션 풀 오류 확인."""
        mock_connections.__getitem__.side_effect = Exception("Pool error")

        result = self.service.check_connection_pool("default")

        assert isinstance(result, PoolInfo)
        assert result.is_usable is False
        assert result.status == "error"
        assert result.error == "Pool error"

    # =========================================================================
    # get_pool_health Tests
    # =========================================================================

    @patch("django.db.connections")
    def test_get_pool_health_healthy(self, mock_connections):
        """정상 풀 헬스."""
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_connections.__getitem__.return_value = mock_conn

        result = self.service.get_pool_health()

        assert isinstance(result, PoolHealthStatus)
        assert result.status == "healthy"
        assert result.error is None

    @patch("django.db.connections")
    def test_get_pool_health_error(self, mock_connections):
        """풀 헬스 오류."""
        mock_connections.__getitem__.side_effect = Exception("Pool error")

        result = self.service.get_pool_health()

        assert isinstance(result, PoolHealthStatus)
        assert result.status == "error"
        assert result.error == "Pool error"

    # =========================================================================
    # get_readiness Tests
    # =========================================================================

    @patch("django.db.connections")
    def test_get_readiness_ready(self, mock_connections):
        """모든 DB 정상일 때 ready."""
        mock_connections.__iter__ = MagicMock(return_value=iter(["default"]))
        
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_conn.cursor.return_value = mock_cursor
        mock_connections.__getitem__.return_value = mock_conn

        result = self.service.get_readiness()

        assert isinstance(result, ReadinessStatus)
        assert result.status == "ready"
        assert result.is_ready is True
        assert result.checks["database_default"] == "ready"

    @patch("django.db.connections")
    def test_get_readiness_not_ready(self, mock_connections):
        """DB 연결 실패 시 not_ready."""
        mock_connections.__iter__ = MagicMock(return_value=iter(["default"]))
        mock_connections.__getitem__.side_effect = Exception("Connection refused")

        result = self.service.get_readiness()

        assert isinstance(result, ReadinessStatus)
        assert result.status == "not_ready"
        assert result.is_ready is False
        assert result.checks["database_default"] == "not_ready"

    # =========================================================================
    # get_overall_health Tests
    # =========================================================================

    @patch("django.utils.timezone")
    @patch.object(HealthCheckService, "_get_circuit_breaker_model")
    @patch.object(HealthCheckService, "check_database")
    def test_get_overall_health_healthy(self, mock_check_db, mock_get_model, mock_timezone):
        """전체 헬스 체크 - 정상."""
        mock_check_db.return_value = DatabaseCheck(
            alias="default", vendor="postgresql", is_connected=True, is_usable=True
        )
        
        mock_model = MagicMock()
        mock_model.objects.count.return_value = 5
        mock_get_model.return_value = mock_model
        
        mock_timezone.now.return_value.isoformat.return_value = "2025-12-19T00:00:00Z"

        result = self.service.get_overall_health()

        assert isinstance(result, HealthStatus)
        assert result.status == "healthy"
        assert result.checks["database"] == "healthy"
        assert result.checks["circuit_breaker"] == "enabled"
        assert result.services_count == 5

    @patch("django.utils.timezone")
    @patch.object(HealthCheckService, "check_database")
    def test_get_overall_health_degraded(self, mock_check_db, mock_timezone):
        """전체 헬스 체크 - 저하."""
        mock_check_db.return_value = DatabaseCheck(
            alias="default", is_connected=False, is_usable=False, error="Connection refused"
        )
        mock_timezone.now.return_value.isoformat.return_value = "2025-12-19T00:00:00Z"

        result = self.service.get_overall_health()

        assert isinstance(result, HealthStatus)
        assert result.status == "degraded"
        assert result.checks["database"] == "unhealthy"
        assert result.services_count == 0

    # =========================================================================
    # Liveness/Readiness Helper Tests
    # =========================================================================

    def test_is_alive_always_true(self):
        """is_alive는 항상 True."""
        assert self.service.is_alive() is True

    @patch("django.db.connections")
    def test_is_ready_true(self, mock_connections):
        """is_ready - 정상."""
        mock_connections.__iter__ = MagicMock(return_value=iter(["default"]))
        
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.vendor = "postgresql"
        mock_conn.is_usable.return_value = True
        mock_conn.cursor.return_value = mock_cursor
        mock_connections.__getitem__.return_value = mock_conn

        assert self.service.is_ready() is True

    @patch("django.db.connections")
    def test_is_ready_false(self, mock_connections):
        """is_ready - 실패."""
        mock_connections.__iter__ = MagicMock(return_value=iter(["default"]))
        mock_connections.__getitem__.side_effect = Exception("Connection refused")

        assert self.service.is_ready() is False


class TestGetHealthCheckService:
    """get_health_check_service 팩토리 함수 테스트."""

    def test_returns_singleton(self):
        """싱글톤 인스턴스 반환."""
        # Reset singleton
        import selfhealing.services.health_check as module
        module._health_check_service = None

        service1 = get_health_check_service()
        service2 = get_health_check_service()

        assert service1 is service2
        assert isinstance(service1, HealthCheckService)


class TestDataClasses:
    """데이터 클래스 테스트."""

    def test_database_check_to_dict(self):
        """DatabaseCheck.to_dict()."""
        check = DatabaseCheck(
            alias="default",
            vendor="postgresql",
            is_connected=True,
            is_usable=True,
            latency_ms=1.5,
        )
        
        result = check.to_dict()
        
        assert result["alias"] == "default"
        assert result["vendor"] == "postgresql"
        assert result["is_connected"] is True
        assert result["latency_ms"] == 1.5

    def test_health_status_to_dict(self):
        """HealthStatus.to_dict()."""
        status = HealthStatus(
            status="healthy",
            checks={"database": "healthy"},
            services_count=5,
            timestamp="2025-12-19T00:00:00Z",
        )
        
        result = status.to_dict()
        
        assert result["status"] == "healthy"
        assert result["checks"]["database"] == "healthy"
        assert result["services_count"] == 5

    def test_readiness_status_to_dict(self):
        """ReadinessStatus.to_dict()."""
        status = ReadinessStatus(
            status="ready",
            checks={"database_default": "ready"},
            is_ready=True,
        )
        
        result = status.to_dict()
        
        assert result["status"] == "ready"
        assert result["is_ready"] is True

    def test_pool_health_status_to_dict(self):
        """PoolHealthStatus.to_dict()."""
        status = PoolHealthStatus(
            status="healthy",
            pool_info={"alias": "default"},
        )
        
        result = status.to_dict()
        
        assert result["status"] == "healthy"
        assert result["pool_info"]["alias"] == "default"

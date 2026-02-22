"""
Regional Isolation Gate Integration Tests.

실제 Redis 연결이 필요한 통합 테스트.

Requirements:
- Docker Compose for Redis
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_regional_gate_integration.py -v

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import os
import pytest

# 이 파일의 모든 테스트는 Redis 필요
pytestmark = pytest.mark.requires_redis

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

from selfhealing.services.isolation.regional_gate import (
    RegionalIsolationGate,
    reset_regional_isolation_gate,
)
from selfhealing.core.cluster_identity import reset_cluster_identity


class TestRegionalIsolationGateRedisIntegration:
    """
    RegionalIsolationGate Redis 통합 테스트.
    
    실제 Redis 연결 시도로 인해 unit 테스트에서 분리됨.
    """
    
    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        reset_regional_isolation_gate()
        reset_cluster_identity()
    
    def teardown_method(self):
        """각 테스트 후에 싱글톤 리셋."""
        reset_regional_isolation_gate()
        reset_cluster_identity()
    
    def test_isolate_region_no_redis(self):
        """
        Redis 없이 리전 격리 시 False 반환.
        
        이 테스트는 global_redis=None을 전달하지만,
        _ensure_initialized()에서 실제 Redis 연결을 시도합니다.
        Redis 서버가 없으면 연결 타임아웃이 발생하여 느려집니다.
        
        Note: 이 테스트는 integration 테스트로 분류됩니다.
        Redis 서버가 실행 중이 아니면 4초+ 지연이 발생할 수 있습니다.
        """
        gate = RegionalIsolationGate(global_redis=None)
        
        result = gate.isolate_region("tokyo", reason="Test", duration_seconds=60)
        
        # Redis 없으면 False 반환
        assert result is False


class TestCrossClusterAuditLinkerRedisIntegration:
    """
    CrossClusterAuditLinker Redis 통합 테스트.
    
    실제 Redis 연결 시도로 인해 unit 테스트에서 분리됨.
    """
    
    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        from selfhealing.audit.integrity.cross_cluster_linker import reset_cross_cluster_audit_linker
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cross_cluster_audit_linker()
        reset_cluster_identity()
    
    def teardown_method(self):
        """각 테스트 후에 싱글톤 리셋."""
        from selfhealing.audit.integrity.cross_cluster_linker import reset_cross_cluster_audit_linker
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cross_cluster_audit_linker()
        reset_cluster_identity()
    
    def test_create_local_anchor_no_redis(self):
        """
        Redis 없이 로컬 앵커 생성 시 None 반환.
        
        이 테스트는 local_redis=None, global_redis=None을 전달하지만,
        내부에서 실제 Redis 연결을 시도합니다.
        Redis 서버가 없으면 연결 타임아웃이 발생하여 느려집니다.
        """
        from selfhealing.audit.integrity.cross_cluster_linker import CrossClusterAuditLinker
        
        linker = CrossClusterAuditLinker(local_redis=None, global_redis=None)
        
        anchor = linker.create_local_anchor()
        
        assert anchor is None


class TestRedisAuditBufferRedisIntegration:
    """
    RedisAuditBuffer Redis 통합 테스트.
    
    실제 Redis 연결 시도로 인해 unit 테스트에서 분리됨.
    """
    
    def test_factory_function_no_redis(self):
        """
        Redis 없을 때 팩토리 함수.
        
        존재하지 않는 Redis URL로 연결 시도 시 None 반환.
        실제 연결 시도로 인해 타임아웃 지연이 발생합니다.
        """
        from selfhealing.adapters.audit.redis_buffer import create_redis_audit_buffer
        
        # 존재하지 않는 Redis URL
        result = create_redis_audit_buffer("redis://nonexistent:6379")
        
        # Redis 연결 실패 시 None 반환
        assert result is None

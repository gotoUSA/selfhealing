"""
Cluster Identity Unit Tests.

ClusterIdentity 및 관련 함수들을 테스트합니다.

Reference: docs/self_healing/middleware_system/70_MULTI_CLUSTER_ARCHITECTURE.md
"""

import os
import pytest
from unittest.mock import patch


class TestClusterIdentity:
    """ClusterIdentity 테스트."""
    
    def setup_method(self):
        """각 테스트 전에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def teardown_method(self):
        """각 테스트 후에 싱글톤 리셋."""
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def test_basic_creation(self):
        """기본 ClusterIdentity 생성."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="seoul-prod-01",
            region="seoul",
            environment="production",
        )
        
        assert identity.cluster_id == "seoul-prod-01"
        assert identity.region == "seoul"
        assert identity.environment == "production"
        assert identity.tenant is None
    
    def test_namespace_priority_region(self):
        """namespace 속성: region 우선."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region="seoul",
            tenant="tenant123",
            environment="staging",
        )
        
        assert identity.namespace == "seoul"
    
    def test_namespace_priority_tenant(self):
        """namespace 속성: tenant 차선."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region=None,
            tenant="tenant123",
            environment="staging",
        )
        
        assert identity.namespace == "tenant123"
    
    def test_namespace_priority_environment(self):
        """namespace 속성: environment 최하위."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region=None,
            tenant=None,
            environment="staging",
        )
        
        assert identity.namespace == "staging"
    
    def test_full_prefix(self):
        """full_prefix 속성."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region="tokyo",
        )
        
        assert identity.full_prefix == "selfhealing:tokyo:"
    
    def test_trace_id_prefix(self):
        """trace_id_prefix 속성."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region="seoul",
            environment="production",
        )
        
        # 리전 앞 3글자 + 환경 앞 1글자
        assert identity.trace_id_prefix == "seop"
    
    def test_trace_id_prefix_no_region(self):
        """trace_id_prefix: 리전 없을 때."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region=None,
            environment="development",
        )
        
        # "unk" + "d"
        assert identity.trace_id_prefix == "unkd"
    
    def test_validate_valid_cluster_id(self):
        """유효한 cluster_id 검증."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="my-cluster-01",
            region="seoul",
        )
        
        # fail_fast=False로 호출해서 sys.exit 방지
        assert identity.validate(fail_fast=False) is True
    
    def test_validate_default_cluster_id(self):
        """기본값 cluster_id는 유효하지 않음."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="default",
        )
        
        assert identity.validate(fail_fast=False) is False
    
    def test_validate_unknown_cluster_id(self):
        """unknown cluster_id는 유효하지 않음."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="unknown",
        )
        
        assert identity.validate(fail_fast=False) is False
    
    def test_validate_empty_cluster_id(self):
        """빈 cluster_id는 유효하지 않음."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="",
        )
        
        assert identity.validate(fail_fast=False) is False
    
    def test_validate_missing_region_fails(self):
        """region 누락 시 검증 실패 (Phase 1 FailFastClusterIdentity)."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="seoul-prod-01",
            region=None,  # 누락
        )
        
        # region 필수 - 검증 실패
        assert identity.validate(fail_fast=False) is False
    
    def test_validate_valid_cluster_and_region(self):
        """cluster_id와 region 모두 유효할 때 통과."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="seoul-prod-01",
            region="seoul",
        )
        
        assert identity.validate(fail_fast=False) is True
    
    def test_validate_fail_fast_exits_on_missing_region(self):
        """fail_fast=True일 때 region 누락 시 SystemExit."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="seoul-prod-01",
            region=None,
        )
        
        with pytest.raises(SystemExit) as exc_info:
            identity.validate(fail_fast=True)
        assert exc_info.value.code == 1
    
    def test_validate_fail_fast_exits_on_invalid_cluster_id(self):
        """fail_fast=True일 때 cluster_id 무효 시 SystemExit."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="default",
            region="seoul",
        )
        
        with pytest.raises(SystemExit) as exc_info:
            identity.validate(fail_fast=True)
        assert exc_info.value.code == 1
    
    def test_validate_multiple_errors_reported(self):
        """cluster_id와 region 모두 무효할 때 두 에러 모두 보고."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        import logging
        
        identity = ClusterIdentity(
            cluster_id="default",
            region=None,
        )
        
        # fail_fast=False로 에러 수집
        result = identity.validate(fail_fast=False)
        assert result is False
    
    def test_immutable(self):
        """ClusterIdentity는 불변."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(
            cluster_id="test",
            region="seoul",
        )
        
        with pytest.raises(Exception):  # FrozenInstanceError
            identity.cluster_id = "changed"


class TestClusterIdentitySingleton:
    """ClusterIdentity 싱글톤 테스트."""
    
    def setup_method(self):
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def teardown_method(self):
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def test_singleton_returns_same_instance(self):
        """싱글톤이 같은 인스턴스 반환."""
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        i1 = get_cluster_identity(skip_validation=True)
        i2 = get_cluster_identity(skip_validation=True)
        assert i1 is i2
    
    def test_reset_clears_singleton(self):
        """reset 후 새 인스턴스 생성."""
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            reset_cluster_identity,
        )
        
        i1 = get_cluster_identity(skip_validation=True)
        reset_cluster_identity()
        i2 = get_cluster_identity(skip_validation=True)
        assert i1 is not i2
    
    @patch.dict(os.environ, {
        "SELFHEALING_CLUSTER_ID": "env-cluster",
        "SELFHEALING_REGION": "busan",
        "SELFHEALING_ENV": "staging",
    }, clear=False)
    def test_env_var_loading(self):
        """환경변수에서 설정 로드."""
        from selfhealing.core.cluster_identity import (
            get_cluster_identity,
            reset_cluster_identity,
        )
        
        reset_cluster_identity()
        identity = get_cluster_identity(skip_validation=True)
        
        assert identity.cluster_id == "env-cluster"
        assert identity.region == "busan"
        assert identity.environment == "staging"


class TestClusterIdentityPodId:
    """ClusterIdentity pod_id 테스트."""
    
    def setup_method(self):
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    def teardown_method(self):
        from selfhealing.core.cluster_identity import reset_cluster_identity
        reset_cluster_identity()
    
    @patch.dict(os.environ, {"HOSTNAME": "pod-abc123"}, clear=False)
    def test_pod_id_from_hostname(self):
        """pod_id가 HOSTNAME 환경변수에서 로드됨."""
        from selfhealing.core.cluster_identity import ClusterIdentity
        
        identity = ClusterIdentity(cluster_id="test")
        assert identity.pod_id == "pod-abc123"

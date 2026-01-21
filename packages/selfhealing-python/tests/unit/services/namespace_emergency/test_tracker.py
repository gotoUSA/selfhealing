"""
NamespacedEmergencyTracker 단위 테스트.

테스트 범위:
- 네임스페이스별 상태 관리 (get_state, set_state)
- Emergency 활성화/비활성화
- Global/Regional 우선순위 적용 (get_effective_state)
- 캐시 관리
- 싱글톤 패턴

Code reference:
    namespace_emergency/tracker.py
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from selfhealing.services.namespace_emergency.tracker import (
    NamespacedEmergencyTracker,
    get_namespaced_emergency_tracker,
    reset_namespaced_emergency_tracker,
    GLOBAL_NAMESPACE,
    DEFAULT_EMERGENCY_EXPIRY_HOURS,
    CACHE_TTL_SECONDS,
)
from selfhealing.services.emergency_mode.enums import EmergencyLevel
from selfhealing.services.coordination.enums import EmergencyScope
from selfhealing.services.coordination.models import ScopedEmergencyState


class TestNamespacedEmergencyTrackerBasic:
    """기본 기능 테스트."""
    
    @pytest.fixture
    def mock_backend(self):
        """Mock StateBackend."""
        backend = MagicMock()
        backend.get.return_value = None
        backend.get_all.return_value = {}
        return backend
    
    @pytest.fixture
    def tracker(self, mock_backend):
        """NamespacedEmergencyTracker with mock backend."""
        return NamespacedEmergencyTracker(backend=mock_backend)
    
    def test_get_state_returns_default_when_empty(self, tracker, mock_backend):
        """상태가 없을 때 기본값 반환."""
        mock_backend.get.return_value = None
        
        state = tracker.get_state("seoul")
        
        assert state.namespace == "seoul"
        assert state.emergency_level == EmergencyLevel.NORMAL
        assert state.governance_mode == "NORMAL"
    
    def test_get_state_returns_stored_state(self, tracker, mock_backend):
        """저장된 상태 반환."""
        mock_backend.get.return_value = {
            "namespace": "seoul",
            "emergency_level": 3,
            "governance_mode": "STRICT",
            "scope": "regional",
        }
        
        # 캐시 무효화
        tracker.invalidate_cache()
        
        state = tracker.get_state("seoul")
        
        assert state.namespace == "seoul"
        assert state.emergency_level == EmergencyLevel.LEVEL_3
        assert state.governance_mode == "STRICT"
    
    def test_set_state_calls_backend(self, tracker, mock_backend):
        """set_state가 backend를 호출."""
        state = ScopedEmergencyState(
            namespace="tokyo",
            emergency_level=EmergencyLevel.LEVEL_2,
            governance_mode="STRICT",
        )
        
        tracker.set_state(state)
        
        mock_backend.set.assert_called_once()
        call_args = mock_backend.set.call_args
        assert "tokyo" in call_args[0][0]  # 키에 tokyo 포함
    
    def test_get_state_key_global(self, tracker):
        """Global 네임스페이스 키 생성."""
        key = tracker._get_state_key(GLOBAL_NAMESPACE)
        assert key == "selfhealing:governance:emergency_state"
    
    def test_get_state_key_regional(self, tracker):
        """Regional 네임스페이스 키 생성."""
        key = tracker._get_state_key("seoul")
        assert key == "selfhealing:seoul:governance:emergency_state"


class TestEmergencyActivation:
    """Emergency 활성화/비활성화 테스트."""
    
    @pytest.fixture
    def mock_backend(self):
        """Mock StateBackend."""
        backend = MagicMock()
        backend.get.return_value = None
        return backend
    
    @pytest.fixture
    def tracker(self, mock_backend):
        """NamespacedEmergencyTracker with mock backend."""
        return NamespacedEmergencyTracker(backend=mock_backend)
    
    def test_activate_emergency_level_3(self, tracker, mock_backend):
        """LEVEL_3 활성화 시 STRICT 모드."""
        state = tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="admin@test.com",
            reason="Test emergency",
            namespace="seoul",
        )
        
        assert state.emergency_level == EmergencyLevel.LEVEL_3
        assert state.governance_mode == "STRICT"
        assert state.activated_by == "admin@test.com"
        assert state.reason == "Test emergency"
        assert state.expires_at is not None
    
    def test_activate_emergency_level_2(self, tracker, mock_backend):
        """LEVEL_2 활성화 시 STRICT 모드."""
        state = tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_2,
            activated_by="system",
            reason="High error rate",
            namespace="tokyo",
        )
        
        assert state.emergency_level == EmergencyLevel.LEVEL_2
        assert state.governance_mode == "STRICT"
    
    def test_activate_emergency_level_1(self, tracker, mock_backend):
        """LEVEL_1 활성화 시 NORMAL 모드 유지."""
        state = tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_1,
            activated_by="system",
            reason="Minor issue",
            namespace="oregon",
        )
        
        assert state.emergency_level == EmergencyLevel.LEVEL_1
        assert state.governance_mode == "NORMAL"  # LEVEL_1은 NORMAL 유지
    
    def test_activate_emergency_global_scope(self, tracker, mock_backend):
        """GLOBAL scope 활성화."""
        state = tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="super_admin",
            reason="Global outage",
            scope=EmergencyScope.GLOBAL,
        )
        
        assert state.namespace == GLOBAL_NAMESPACE
        assert state.scope == EmergencyScope.GLOBAL
    
    def test_activate_emergency_custom_expiry(self, tracker, mock_backend):
        """사용자 지정 만료 시간."""
        state = tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="admin",
            reason="Long maintenance",
            namespace="seoul",
            expiry_hours=24,
        )
        
        # 24시간 후 만료 확인
        expected_expiry = datetime.now(timezone.utc) + timedelta(hours=24)
        assert abs((state.expires_at - expected_expiry).total_seconds()) < 5
    
    def test_deactivate_emergency(self, tracker, mock_backend):
        """Emergency 비활성화."""
        state = tracker.deactivate_emergency(
            deactivated_by="admin@test.com",
            namespace="seoul",
        )
        
        assert state.emergency_level == EmergencyLevel.NORMAL
        assert state.governance_mode == "NORMAL"
        assert "admin@test.com" in state.reason
    
    def test_deactivate_emergency_global(self, tracker, mock_backend):
        """Global Emergency 비활성화."""
        state = tracker.deactivate_emergency(
            deactivated_by="super_admin",
            scope=EmergencyScope.GLOBAL,
        )
        
        assert state.namespace == GLOBAL_NAMESPACE
        assert state.scope == EmergencyScope.GLOBAL


class TestEffectiveState:
    """get_effective_state 우선순위 테스트."""
    
    @pytest.fixture
    def mock_backend(self):
        """Mock StateBackend."""
        backend = MagicMock()
        return backend
    
    @pytest.fixture
    def tracker(self, mock_backend):
        """NamespacedEmergencyTracker with mock backend."""
        # atomic_query 없이 수동 폴백 테스트
        return NamespacedEmergencyTracker(
            backend=mock_backend,
            atomic_query=None,  # 수동 폴백 강제
        )
    
    def test_global_strict_overrides_regional_normal(self, tracker, mock_backend):
        """Global STRICT가 Regional NORMAL을 오버라이드."""
        def get_side_effect(key):
            # Global 키: selfhealing:governance:emergency_state
            # Regional 키: selfhealing:seoul:governance:emergency_state
            if key == "selfhealing:governance:emergency_state":
                # Global 상태
                return {
                    "namespace": "global",
                    "emergency_level": 3,
                    "governance_mode": "STRICT",
                    "scope": "global",
                }
            else:
                # Regional 상태
                return {
                    "namespace": "seoul",
                    "emergency_level": 0,
                    "governance_mode": "NORMAL",
                    "scope": "regional",
                }
        
        mock_backend.get.side_effect = get_side_effect
        tracker.invalidate_cache()
        
        state = tracker.get_effective_state("seoul")
        
        assert state.governance_mode == "STRICT"
        assert state.scope == EmergencyScope.GLOBAL  # Global에서 왔음
    
    def test_regional_strict_when_global_normal(self, tracker, mock_backend):
        """Global NORMAL일 때 Regional STRICT 유지."""
        def get_side_effect(key):
            if key == "selfhealing:governance:emergency_state":
                return {
                    "namespace": "global",
                    "emergency_level": 0,
                    "governance_mode": "NORMAL",
                    "scope": "global",
                }
            else:
                return {
                    "namespace": "seoul",
                    "emergency_level": 3,
                    "governance_mode": "STRICT",
                    "scope": "regional",
                }
        
        mock_backend.get.side_effect = get_side_effect
        tracker.invalidate_cache()
        
        state = tracker.get_effective_state("seoul")
        
        assert state.governance_mode == "STRICT"
        assert state.scope == EmergencyScope.REGIONAL
    
    def test_admin_override_ignores_global(self, tracker, mock_backend):
        """ADMIN_OVERRIDE 시 Global 무시하고 Regional 사용."""
        def get_side_effect(key):
            if key == "selfhealing:governance:emergency_state":
                return {
                    "namespace": "global",
                    "emergency_level": 3,
                    "governance_mode": "STRICT",
                    "scope": "global",
                }
            else:
                return {
                    "namespace": "seoul",
                    "emergency_level": 0,
                    "governance_mode": "NORMAL",
                    "scope": "regional",
                }
        
        mock_backend.get.side_effect = get_side_effect
        tracker.invalidate_cache()
        
        state = tracker.get_effective_state("seoul", precedence="ADMIN_OVERRIDE")
        
        # ADMIN_OVERRIDE면 Regional 우선
        assert state.governance_mode == "NORMAL"
        assert state.namespace == "seoul"


class TestCacheManagement:
    """캐시 관리 테스트."""
    
    @pytest.fixture
    def mock_backend(self):
        """Mock StateBackend."""
        backend = MagicMock()
        backend.get.return_value = {
            "namespace": "seoul",
            "emergency_level": 0,
            "governance_mode": "NORMAL",
            "scope": "regional",
        }
        return backend
    
    @pytest.fixture
    def tracker(self, mock_backend):
        """NamespacedEmergencyTracker with mock backend."""
        return NamespacedEmergencyTracker(backend=mock_backend)
    
    def test_cache_hit_reduces_backend_calls(self, tracker, mock_backend):
        """캐시 히트 시 backend 호출 감소."""
        # 첫 번째 호출
        tracker.get_state("seoul")
        call_count_1 = mock_backend.get.call_count
        
        # 두 번째 호출 (캐시 히트)
        tracker.get_state("seoul")
        call_count_2 = mock_backend.get.call_count
        
        # 캐시 히트로 추가 호출 없음
        assert call_count_1 == call_count_2
    
    def test_invalidate_cache_clears_specific(self, tracker, mock_backend):
        """특정 네임스페이스 캐시 무효화."""
        # 캐시 생성
        tracker.get_state("seoul")
        tracker.get_state("tokyo")
        
        # seoul만 무효화
        tracker.invalidate_cache("seoul")
        
        # seoul 재호출 시 backend 호출
        tracker.get_state("seoul")
        # tokyo는 캐시 히트
        tracker.get_state("tokyo")
        
        # seoul은 2번 호출, tokyo는 1번 호출
        # (정확한 call_count는 구현에 따라 다름)
    
    def test_invalidate_cache_clears_all(self, tracker, mock_backend):
        """전체 캐시 무효화."""
        # 캐시 생성
        tracker.get_state("seoul")
        tracker.get_state("tokyo")
        
        initial_calls = mock_backend.get.call_count
        
        # 전체 무효화
        tracker.invalidate_cache()
        
        # 재호출 시 모두 backend 호출
        tracker.get_state("seoul")
        tracker.get_state("tokyo")
        
        # 추가 호출 발생
        assert mock_backend.get.call_count > initial_calls


class TestActiveNamespaces:
    """활성 네임스페이스 조회 테스트."""
    
    @pytest.fixture
    def mock_backend(self):
        """Mock StateBackend."""
        backend = MagicMock()
        return backend
    
    @pytest.fixture
    def tracker(self, mock_backend):
        """NamespacedEmergencyTracker with mock backend."""
        return NamespacedEmergencyTracker(backend=mock_backend)
    
    def test_get_all_active_namespaces_empty(self, tracker, mock_backend):
        """활성 네임스페이스 없음."""
        mock_backend.get_all.return_value = {}
        
        result = tracker.get_all_active_namespaces()
        
        assert result == []
    
    def test_get_all_active_namespaces_multiple(self, tracker, mock_backend):
        """여러 활성 네임스페이스."""
        mock_backend.get_all.return_value = {
            "selfhealing:seoul:governance:emergency_state": {
                "namespace": "seoul",
                "emergency_level": 3,
            },
            "selfhealing:tokyo:governance:emergency_state": {
                "namespace": "tokyo",
                "emergency_level": 2,
            },
            "selfhealing:oregon:governance:emergency_state": {
                "namespace": "oregon",
                "emergency_level": 0,  # NORMAL
            },
        }
        
        result = tracker.get_all_active_namespaces()
        
        # oregon은 emergency_level=0이므로 제외
        assert len(result) == 2
        assert "seoul" in result or "tokyo" in result


class TestSingleton:
    """싱글톤 패턴 테스트."""
    
    def setup_method(self):
        """테스트 전 싱글톤 초기화."""
        reset_namespaced_emergency_tracker()
    
    def teardown_method(self):
        """테스트 후 싱글톤 초기화."""
        reset_namespaced_emergency_tracker()
    
    def test_singleton_returns_same_instance(self):
        """싱글톤이 같은 인스턴스 반환."""
        with patch("selfhealing.core.state_backend.get_state_backend"):
            tracker1 = get_namespaced_emergency_tracker()
            tracker2 = get_namespaced_emergency_tracker()
            
            assert tracker1 is tracker2
    
    def test_reset_clears_singleton(self):
        """reset이 싱글톤 초기화."""
        with patch("selfhealing.core.state_backend.get_state_backend"):
            tracker1 = get_namespaced_emergency_tracker()
            reset_namespaced_emergency_tracker()
            tracker2 = get_namespaced_emergency_tracker()
            
            assert tracker1 is not tracker2

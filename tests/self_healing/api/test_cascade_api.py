"""
Cascade Event API Views 단위 테스트.

Phase 8: API 엔드포인트 테스트 (CascadeEventListView, CascadeEventDetailView, 
CascadeChainVerifyView, CausationTraceView, CascadeCheckpointView).

Tests:
- CascadeEventListView: 목록 조회, 필터링, 페이지네이션
- CascadeEventDetailView: 상세 조회, 404 처리
- CascadeChainVerifyView: 무결성 검증
- CausationTraceView: 인과관계 추적
- CascadeCheckpointView: 체크포인트 조회/생성

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

import os
import django

# Django 설정 초기화
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


# =============================================================================
# CascadeEventListView Tests
# =============================================================================


class TestCascadeEventListView:
    """CascadeEventListView 테스트."""
    
    def test_list_events_returns_event_list(self):
        """이벤트 목록 조회 성공."""
        from selfhealing.api.django.views.cascade import CascadeEventListView
        from selfhealing.audit.cascade_event import (
            CascadeEvent, CascadeTrigger, CascadeEffect
        )
        
        # Mock 이벤트 생성
        mock_event = CascadeEvent(
            id="cascade-abc123",
            trigger=CascadeTrigger(
                trigger_type="EMERGENCY_LEVEL_CHANGED",
                event_id="evt-001",
                details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
                triggered_by="system",
            ),
            effects=[
                CascadeEffect(
                    action_type="governance_strict",
                    event_id="effect-001",
                    success=True,
                    caused_by="evt-001",
                    details={},
                    executed_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
            namespace="seoul",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        
        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [mock_event]
        mock_auditor.get_event_count.return_value = 1
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "limit": "100"}
            
            response = view.get(mock_request)
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["id"] == "cascade-abc123"
            assert response.data["events"][0]["trigger_type"] == "EMERGENCY_LEVEL_CHANGED"
    
    def test_list_events_with_trigger_type_filter(self):
        """트리거 유형 필터링 테스트."""
        from selfhealing.api.django.views.cascade import CascadeEventListView
        from selfhealing.audit.cascade_event import CascadeEvent, CascadeTrigger
        
        mock_event1 = CascadeEvent(
            id="cascade-001",
            trigger=CascadeTrigger(
                trigger_type="EMERGENCY_LEVEL_CHANGED",
                event_id="evt-001",
                details={},
            ),
            effects=[],
            namespace="seoul",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        mock_event2 = CascadeEvent(
            id="cascade-002",
            trigger=CascadeTrigger(
                trigger_type="MANUAL_ACTIVATION",
                event_id="evt-002",
                details={},
            ),
            effects=[],
            namespace="seoul",
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        
        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = [mock_event1, mock_event2]
        mock_auditor.get_event_count.return_value = 2
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {
                "namespace": "seoul",
                "trigger_type": "EMERGENCY_LEVEL_CHANGED",
            }
            
            response = view.get(mock_request)
            
            assert response.status_code == 200
            assert len(response.data["events"]) == 1
            assert response.data["events"][0]["trigger_type"] == "EMERGENCY_LEVEL_CHANGED"
    
    def test_list_events_pagination(self):
        """페이지네이션 테스트."""
        from selfhealing.api.django.views.cascade import CascadeEventListView
        
        mock_auditor = MagicMock()
        mock_auditor.get_recent_events.return_value = []
        mock_auditor.get_event_count.return_value = 0
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventListView()
            mock_request = MagicMock()
            mock_request.query_params = {
                "namespace": "seoul",
                "limit": "50",
                "offset": "10",
            }
            
            response = view.get(mock_request)
            
            assert response.status_code == 200
            assert response.data["limit"] == 50
            assert response.data["offset"] == 10


# =============================================================================
# CascadeEventDetailView Tests
# =============================================================================


class TestCascadeEventDetailView:
    """CascadeEventDetailView 테스트."""
    
    def test_get_event_detail_success(self):
        """이벤트 상세 조회 성공."""
        from selfhealing.api.django.views.cascade import CascadeEventDetailView
        from selfhealing.audit.cascade_event import (
            CascadeEvent, CascadeTrigger, CascadeEffect
        )
        
        mock_event = CascadeEvent(
            id="cascade-abc123",
            trigger=CascadeTrigger(
                trigger_type="EMERGENCY_LEVEL_CHANGED",
                event_id="evt-001",
                details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
                triggered_by="system",
            ),
            effects=[
                CascadeEffect(
                    action_type="governance_strict",
                    event_id="effect-001",
                    success=True,
                    caused_by="evt-001",
                    details={},
                    executed_at=datetime.now(timezone.utc).isoformat(),
                )
            ],
            namespace="seoul",
            timestamp=datetime.now(timezone.utc).isoformat(),
            previous_hash="prev-hash",
            current_hash="curr-hash",
        )
        
        mock_auditor = MagicMock()
        mock_auditor.get_cascade_event.return_value = mock_event
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventDetailView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}
            
            response = view.get(mock_request, cascade_id="cascade-abc123")
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert response.data["event"]["id"] == "cascade-abc123"
            assert response.data["event"]["trigger"]["type"] == "EMERGENCY_LEVEL_CHANGED"
            assert len(response.data["event"]["effects"]) == 1
    
    def test_get_event_detail_not_found(self):
        """이벤트 없음 404 처리."""
        from selfhealing.api.django.views.cascade import CascadeEventDetailView
        
        mock_auditor = MagicMock()
        mock_auditor.get_cascade_event.return_value = None
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeEventDetailView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}
            
            response = view.get(mock_request, cascade_id="nonexistent")
            
            assert response.status_code == 404
            assert response.data["success"] is False
            assert response.data["error"] == "not_found"


# =============================================================================
# CascadeChainVerifyView Tests
# =============================================================================


class TestCascadeChainVerifyView:
    """CascadeChainVerifyView 테스트."""
    
    def test_verify_chain_integrity_valid(self):
        """Hash Chain 무결성 검증 성공."""
        from selfhealing.api.django.views.cascade import CascadeChainVerifyView
        
        mock_auditor = MagicMock()
        mock_auditor.verify_chain_integrity_from_checkpoint.return_value = {
            "valid": True,
            "verified_count": 100,
            "errors": [],
        }
        mock_auditor.get_checkpoint.return_value = {
            "timestamp": "2026-01-22T00:00:00Z",
            "last_hash": "abc123",
        }
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeChainVerifyView()
            mock_request = MagicMock()
            mock_request.data = {"namespace": "seoul", "from_checkpoint": True}
            
            response = view.post(mock_request)
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert response.data["valid"] is True
            assert response.data["verified_count"] == 100
            assert response.data["errors"] == []
    
    def test_verify_chain_integrity_invalid(self):
        """Hash Chain 무결성 위반 감지."""
        from selfhealing.api.django.views.cascade import CascadeChainVerifyView
        
        mock_auditor = MagicMock()
        mock_auditor.verify_chain_integrity_from_checkpoint.return_value = {
            "valid": False,
            "verified_count": 50,
            "errors": [
                {
                    "cascade_id": "cascade-xyz",
                    "error": "hash_mismatch",
                    "expected": "abc",
                    "actual": "def",
                }
            ],
        }
        mock_auditor.get_checkpoint.return_value = None
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeChainVerifyView()
            mock_request = MagicMock()
            mock_request.data = {"namespace": "seoul"}
            
            response = view.post(mock_request)
            
            assert response.status_code == 200
            assert response.data["valid"] is False
            assert len(response.data["errors"]) == 1


# =============================================================================
# CausationTraceView Tests
# =============================================================================


class TestCausationTraceView:
    """CausationTraceView 테스트."""
    
    def test_trace_causation_success(self):
        """인과관계 추적 성공."""
        from selfhealing.api.django.views.cascade import CausationTraceView
        
        mock_trace = [
            {
                "event_id": "evt-001",
                "type": "trigger",
                "cascade_id": "cascade-abc123",
            },
            {
                "event_id": "effect-001",
                "type": "effect",
                "action_type": "governance_strict",
                "caused_by": "evt-001",
            },
        ]
        
        mock_auditor = MagicMock()
        mock_auditor.trace_causation.return_value = mock_trace
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CausationTraceView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul", "direction": "ancestors"}
            
            response = view.get(mock_request, event_id="effect-001")
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert len(response.data["trace"]) == 2
            assert response.data["cascade_id"] == "cascade-abc123"
    
    def test_trace_causation_invalid_direction(self):
        """잘못된 direction 파라미터 처리."""
        from selfhealing.api.django.views.cascade import CausationTraceView
        
        view = CausationTraceView()
        mock_request = MagicMock()
        mock_request.query_params = {"namespace": "seoul", "direction": "invalid"}
        
        response = view.get(mock_request, event_id="effect-001")
        
        assert response.status_code == 400
        assert response.data["error"] == "invalid_direction"
    
    def test_trace_causation_not_found(self):
        """이벤트 없음 404 처리."""
        from selfhealing.api.django.views.cascade import CausationTraceView
        
        mock_auditor = MagicMock()
        mock_auditor.trace_causation.return_value = []
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CausationTraceView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}
            
            response = view.get(mock_request, event_id="nonexistent")
            
            assert response.status_code == 404


# =============================================================================
# CascadeCheckpointView Tests
# =============================================================================


class TestCascadeCheckpointView:
    """CascadeCheckpointView 테스트."""
    
    def test_get_checkpoint_success(self):
        """체크포인트 조회 성공."""
        from selfhealing.api.django.views.cascade import CascadeCheckpointView
        
        mock_checkpoint = {
            "timestamp": "2026-01-22T00:00:00Z",
            "last_hash": "abc123",
            "event_count": 1500,
        }
        
        mock_auditor = MagicMock()
        mock_auditor.get_checkpoint.return_value = mock_checkpoint
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeCheckpointView()
            mock_request = MagicMock()
            mock_request.query_params = {"namespace": "seoul"}
            
            response = view.get(mock_request)
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert response.data["checkpoint"]["last_hash"] == "abc123"
    
    def test_create_checkpoint_success(self):
        """체크포인트 생성 성공."""
        from selfhealing.api.django.views.cascade import CascadeCheckpointView
        
        mock_checkpoint = {
            "timestamp": "2026-01-23T00:00:00Z",
            "last_hash": "def456",
            "event_count": 1650,
        }
        
        mock_auditor = MagicMock()
        mock_auditor.create_checkpoint.return_value = mock_checkpoint
        
        with patch(
            "selfhealing.api.django.views.cascade._get_cascade_auditor",
            return_value=mock_auditor,
        ):
            view = CascadeCheckpointView()
            mock_request = MagicMock()
            mock_request.data = {"namespace": "seoul"}
            
            response = view.post(mock_request)
            
            assert response.status_code == 200
            assert response.data["success"] is True
            mock_auditor.create_checkpoint.assert_called_once_with("seoul")


# =============================================================================
# CascadeLoadSheddingStatusView Tests
# =============================================================================


class TestCascadeLoadSheddingStatusView:
    """CascadeLoadSheddingStatusView 테스트."""
    
    def test_get_load_shedding_status(self):
        """Load Shedding 상태 조회."""
        from selfhealing.api.django.views.cascade import CascadeLoadSheddingStatusView
        
        mock_status = {
            "enabled": True,
            "current_load": 0.75,
            "threshold_start": 0.7,
            "threshold_stop": 0.5,
            "dropped_count": {"LOW": 100, "MEDIUM": 50},
            "total_dropped": 150,
        }
        
        mock_load_shedding = MagicMock()
        mock_load_shedding.get_status.return_value = mock_status
        
        with patch(
            "selfhealing.audit.cascade_load_shedding.get_cascade_load_shedding",
            return_value=mock_load_shedding,
        ):
            view = CascadeLoadSheddingStatusView()
            mock_request = MagicMock()
            
            response = view.get(mock_request)
            
            assert response.status_code == 200
            assert response.data["success"] is True
            assert response.data["enabled"] is True
            assert response.data["current_load"] == 0.75

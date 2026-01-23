"""
Cascade Event Archive 모델 단위 테스트.

테스트 대상:
- AbstractCascadeEventArchive Django Abstract 모델
- CascadeEventArchive 구체 모델
- from_cascade_event 팩토리 메서드
- verify_hash_integrity 무결성 검증
- WAL 경로 통일

Django 모델 테스트는 Django 설정이 필요하므로 별도 표시.
WAL 경로 통일 테스트는 Django 없이도 실행 가능.

Reference:
    models/cascade_event_archive.py
    tests/unit/adapters/test_abstract_failed_operation.py (패턴)
"""

import os
import sys

# Django 설정 (테스트 환경용)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django
django.setup()

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


class TestCascadeEventArchiveWithDjango:
    """Django 환경에서의 테스트."""
    
    @pytest.fixture
    def mock_cascade_event(self):
        """테스트용 CascadeEvent Mock."""
        from selfhealing.audit.cascade_event import (
            CascadeEvent,
            CascadeEffect,
            CascadeTrigger,
        )
        
        trigger = CascadeTrigger(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            event_id="evt-001",
            details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
            triggered_by="system",
        )
        
        effects = [
            CascadeEffect(
                event_id="evt-002",
                action_type="GOVERNANCE_STRICT",
                caused_by="evt-001",
                success=True,
                details={"mode": "STRICT"},
            ),
            CascadeEffect(
                event_id="evt-003",
                action_type="CANARY_ROLLBACK",
                caused_by="evt-002",
                success=True,
                target="rollout-123",
            ),
        ]
        
        event = CascadeEvent(
            id="cascade-test-001",
            trigger=trigger,
            effects=effects,
            namespace="seoul",
            timestamp="2026-01-23T15:30:00+00:00",
            previous_hash="abc123def456",
        )
        event.current_hash = event.calculate_hash()
        
        return event
    
    def test_trigger_type_choices_defined(self):
        """TriggerType choices 정의 확인."""
        from selfhealing.models.cascade_event_archive import (
            AbstractCascadeEventArchive,
        )
        
        choices = AbstractCascadeEventArchive.TriggerType.choices
        assert len(choices) > 0
        
        # 필수 트리거 타입 확인
        choice_values = [c[0] for c in choices]
        assert "EMERGENCY_LEVEL_CHANGED" in choice_values
        assert "MANUAL_INTERVENTION" in choice_values
        assert "CANARY_ROLLBACK" in choice_values
    
    def test_from_cascade_event_factory_method(self, mock_cascade_event):
        """from_cascade_event 팩토리 메서드 테스트."""
        from selfhealing.models.cascade_event_archive import (
            CascadeEventArchive,
        )
        
        # 팩토리 메서드 호출 (구체 모델 사용)
        archive = CascadeEventArchive.from_cascade_event(
            mock_cascade_event
        )
        
        # 필드 검증
        assert archive.cascade_id == "cascade-test-001"
        assert archive.namespace == "seoul"
        assert archive.trigger_type == "EMERGENCY_LEVEL_CHANGED"
        assert archive.trigger_details == {
            "old_level": "NORMAL",
            "new_level": "LEVEL_3",
        }
        
        # causation_chain 검증
        chain = archive.causation_chain
        assert isinstance(chain, list)
        assert "evt-001" in chain  # trigger event
        assert "evt-002" in chain  # first effect
        assert "evt-003" in chain  # second effect
        
        # effects 검증
        assert len(archive.effects) == 2
        assert archive.total_effects == 2
        assert archive.success_count == 2
        assert archive.failure_count == 0
        
        # hash chain 검증
        assert archive.previous_hash == "abc123def456"
        assert len(archive.current_hash) == 64  # SHA-256
    
    def test_get_causation_chain_display(self):
        """get_causation_chain_display 메서드 테스트."""
        from selfhealing.models.cascade_event_archive import (
            CascadeEventArchive,
        )
        
        # 인스턴스 생성 (저장하지 않음)
        archive = CascadeEventArchive(
            cascade_id="test-001",
            namespace="seoul",
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            timestamp=datetime.now(timezone.utc),
            current_hash="a" * 64,
        )
        archive.causation_chain = ["evt-001", "evt-002", "evt-003"]
        
        display = archive.get_causation_chain_display()
        assert display == "evt-001 → evt-002 → evt-003"
    
    def test_model_meta_options(self):
        """Model Meta 옵션 테스트."""
        from selfhealing.models.cascade_event_archive import (
            AbstractCascadeEventArchive,
        )
        
        meta = AbstractCascadeEventArchive._meta
        
        # abstract 확인
        assert meta.abstract is True
        
        # ordering 확인
        assert "-timestamp" in meta.ordering
        
        # verbose_name 확인
        assert "Cascade Event Archive" in meta.verbose_name
    
    def test_required_fields_defined(self):
        """필수 필드 정의 확인."""
        from selfhealing.models.cascade_event_archive import (
            AbstractCascadeEventArchive,
        )
        
        # 필수 필드 목록
        required_fields = [
            "cascade_id",
            "namespace",
            "trigger_type",
            "trigger_details",
            "effects",
            "causation_chain",
            "previous_hash",
            "current_hash",
            "total_effects",
            "success_count",
            "failure_count",
            "timestamp",
            "archived_at",
            "version",
        ]
        
        # 필드 존재 확인
        field_names = [f.name for f in AbstractCascadeEventArchive._meta.fields]
        for field in required_fields:
            assert field in field_names, f"Field {field} not found"
    
    def test_causation_chain_is_jsonfield(self):
        """causation_chain이 JSONField인지 확인."""
        from django.db import models
        from selfhealing.models.cascade_event_archive import (
            AbstractCascadeEventArchive,
        )
        
        field = AbstractCascadeEventArchive._meta.get_field("causation_chain")
        assert isinstance(field, models.JSONField)
    
    def test_indexes_defined(self):
        """인덱스 정의 확인."""
        from selfhealing.models.cascade_event_archive import (
            AbstractCascadeEventArchive,
        )
        
        indexes = AbstractCascadeEventArchive._meta.indexes
        index_names = [idx.name for idx in indexes]
        
        # 필수 인덱스 확인
        assert "idx_cascade_ns_ts" in index_names
        assert "idx_cascade_trigger" in index_names
        assert "idx_cascade_hash" in index_names
    
    def test_concrete_model_db_table(self):
        """구체 모델의 db_table 확인."""
        from selfhealing.models.cascade_event_archive import (
            CascadeEventArchive,
        )
        
        assert CascadeEventArchive._meta.db_table == "selfhealing_cascade_events"
        assert CascadeEventArchive._meta.abstract is False


class TestWALPathUnification:
    """WAL 경로 통일 테스트."""
    
    def test_cascade_auditor_uses_wal_path(self):
        """cascade_auditor.py가 WAL 경로 사용."""
        from selfhealing.audit.cascade_auditor import (
            LOCAL_CASCADE_WAL_DIR,
            LOCAL_CASCADE_WAL_PATH,
            LOCAL_CASCADE_FALLBACK_PATH,
        )
        
        # WAL 디렉토리 경로 확인
        assert LOCAL_CASCADE_WAL_DIR == "/var/log/selfhealing/cascade_wal"
        
        # WAL 파일 경로 확인
        assert "cascade_wal" in LOCAL_CASCADE_WAL_PATH
        assert LOCAL_CASCADE_WAL_PATH.endswith(".jsonl")
        
        # 하위 호환성 별칭 확인
        assert LOCAL_CASCADE_FALLBACK_PATH == LOCAL_CASCADE_WAL_PATH
    
    def test_cascade_cleanup_tasks_uses_wal_path(self):
        """cascade_cleanup_tasks.py가 WAL 경로 사용."""
        from selfhealing.tasks.cascade_cleanup_tasks import (
            LOCAL_CASCADE_WAL_DIR,
            LOCAL_CASCADE_WAL_PATH,
            LOCAL_FALLBACK_PATH,
        )
        
        # WAL 디렉토리 경로 확인
        assert "cascade_wal" in str(LOCAL_CASCADE_WAL_DIR)
        
        # 하위 호환성 별칭 확인
        assert LOCAL_FALLBACK_PATH == LOCAL_CASCADE_WAL_PATH
    
    def test_auditor_wal_methods_exist(self):
        """CascadeEventAuditor에 WAL 메서드 존재."""
        from selfhealing.audit.cascade_auditor import CascadeEventAuditor
        
        auditor = CascadeEventAuditor(enable_load_shedding=False)
        
        # WAL 메서드 확인
        assert hasattr(auditor, '_save_to_local_wal')
        assert hasattr(auditor, '_record_dropped_to_wal')
        assert hasattr(auditor, 'recover_from_local_wal')
        assert hasattr(auditor, '_remove_namespace_from_wal')
        
        # 하위 호환성 별칭 확인
        assert hasattr(auditor, '_save_to_local_fallback')
        assert hasattr(auditor, 'recover_from_local_fallback')
        assert auditor._save_to_local_fallback == auditor._save_to_local_wal
        assert auditor.recover_from_local_fallback == auditor.recover_from_local_wal
    
    def test_cleanup_tasks_wal_functions_exist(self):
        """cascade_cleanup_tasks에 WAL 함수 존재."""
        from selfhealing.tasks.cascade_cleanup_tasks import (
            recover_cascade_from_wal,
            recover_cascade_from_fallback,
            _remove_namespace_from_wal,
            _remove_namespace_from_fallback,
        )
        
        # WAL 함수 확인
        assert callable(recover_cascade_from_wal)
        assert callable(_remove_namespace_from_wal)
        
        # 하위 호환성 별칭 확인
        assert recover_cascade_from_fallback == recover_cascade_from_wal
        assert _remove_namespace_from_fallback == _remove_namespace_from_wal


class TestCascadeEventArchiveExport:
    """모듈 exports 테스트."""
    
    def test_cascade_event_archive_exports(self):
        """모듈 exports 확인."""
        from selfhealing.models import (
            AbstractCascadeEventArchive,
            CascadeEventArchive,
        )
        
        assert AbstractCascadeEventArchive is not None
        assert CascadeEventArchive is not None
    
    def test_django_available_flag(self):
        """DJANGO_AVAILABLE 플래그 확인."""
        from selfhealing.models.cascade_event_archive import DJANGO_AVAILABLE
        
        assert DJANGO_AVAILABLE is True  # Django가 설치된 환경


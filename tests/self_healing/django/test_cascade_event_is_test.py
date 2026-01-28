"""
CascadeEvent is_test 필드 Django 통합 테스트.

테스트 대상:
- AbstractCascadeEventArchive.is_test 필드 존재
- is_test 기본값 False
- is_test db_index 설정
- idx_cascade_test_ts 복합 인덱스
- from_cascade_event()에서 is_test 복사

실행 방법:
    docker-compose -f docker-compose.test.yml exec web pytest tests/self_healing/django/test_cascade_event_is_test.py -v
"""

import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")
django.setup()

import pytest

from selfhealing.models.cascade_event_archive import (
    AbstractCascadeEventArchive,
    CascadeEventArchive,
    DJANGO_AVAILABLE,
)
from selfhealing.audit.cascade_event import (
    CascadeEvent,
    CascadeEffect,
    CascadeTrigger,
)
from selfhealing.core.test_mode_context import TestModeContext


@pytest.fixture
def sample_trigger():
    """샘플 트리거 fixture."""
    return CascadeTrigger(
        trigger_type="EMERGENCY_LEVEL_CHANGED",
        event_id="evt-001",
        details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
        triggered_by="system",
    )


@pytest.fixture
def sample_effects():
    """샘플 효과 목록 fixture."""
    return [
        CascadeEffect(
            event_id="evt-002",
            action_type="GOVERNANCE_STRICT",
            caused_by="evt-001",
            success=True,
            details={"mode": "STRICT"},
        ),
    ]


@pytest.fixture
def cascade_event_is_test_true(sample_trigger, sample_effects):
    """is_test=True인 CascadeEvent fixture."""
    event = CascadeEvent(
        id="cascade-test-is-test-true",
        trigger=sample_trigger,
        effects=sample_effects,
        namespace="seoul",
        timestamp="2026-01-27T15:30:00+00:00",
        is_test=True,
    )
    event.current_hash = event.calculate_hash()
    return event


@pytest.fixture
def cascade_event_is_test_false(sample_trigger, sample_effects):
    """is_test=False인 CascadeEvent fixture."""
    event = CascadeEvent(
        id="cascade-test-is-test-false",
        trigger=sample_trigger,
        effects=sample_effects,
        namespace="seoul",
        timestamp="2026-01-27T15:30:00+00:00",
        is_test=False,
    )
    event.current_hash = event.calculate_hash()
    return event


class TestCascadeEventArchiveIsTestField:
    """AbstractCascadeEventArchive.is_test 필드 통합 테스트."""

    def test_django_available(self):
        """Django 사용 가능 확인."""
        assert DJANGO_AVAILABLE is True

    def test_abstract_model_has_is_test_field(self):
        """AbstractCascadeEventArchive에 is_test 필드가 있는지 확인."""
        field_names = [f.name for f in AbstractCascadeEventArchive._meta.get_fields()]
        assert "is_test" in field_names

    def test_is_test_field_is_boolean(self):
        """is_test 필드가 BooleanField인지 확인."""
        from django.db import models

        is_test_field = AbstractCascadeEventArchive._meta.get_field("is_test")
        assert isinstance(is_test_field, models.BooleanField)

    def test_is_test_default_false(self):
        """is_test 필드의 기본값이 False인지 확인."""
        is_test_field = AbstractCascadeEventArchive._meta.get_field("is_test")
        assert is_test_field.default is False

    def test_is_test_has_db_index(self):
        """is_test 필드에 db_index가 설정되어 있는지 확인."""
        is_test_field = AbstractCascadeEventArchive._meta.get_field("is_test")
        assert is_test_field.db_index is True

    def test_is_test_has_help_text(self):
        """is_test 필드에 help_text가 있는지 확인."""
        is_test_field = AbstractCascadeEventArchive._meta.get_field("is_test")
        assert is_test_field.help_text
        assert "테스트" in is_test_field.help_text or "X-Test" in is_test_field.help_text


class TestCascadeEventArchiveCompositeIndex:
    """복합 인덱스 테스트."""

    def test_has_idx_cascade_test_ts_index(self):
        """Meta.indexes에 idx_cascade_test_ts 인덱스가 있는지 확인."""
        index_names = [idx.name for idx in AbstractCascadeEventArchive._meta.indexes]
        assert "idx_cascade_test_ts" in index_names

    def test_idx_cascade_test_ts_fields(self):
        """idx_cascade_test_ts 인덱스의 필드가 (is_test, -timestamp)인지 확인."""
        for idx in AbstractCascadeEventArchive._meta.indexes:
            if idx.name == "idx_cascade_test_ts":
                assert idx.fields == ["is_test", "-timestamp"]
                break
        else:
            pytest.fail("idx_cascade_test_ts index not found")

    def test_all_existing_indexes_preserved(self):
        """기존 인덱스들이 유지되는지 확인."""
        index_names = [idx.name for idx in AbstractCascadeEventArchive._meta.indexes]

        # 기존 인덱스 확인
        assert "idx_cascade_ns_ts" in index_names
        assert "idx_cascade_trigger" in index_names
        assert "idx_cascade_hash" in index_names


class TestCascadeEventArchiveFromCascadeEvent:
    """from_cascade_event() 메서드 is_test 복사 테스트."""

    def test_from_cascade_event_copies_is_test_true(self, cascade_event_is_test_true):
        """is_test=True가 Archive로 복사되는지 확인."""
        archive = CascadeEventArchive.from_cascade_event(cascade_event_is_test_true)

        assert archive.is_test is True

    def test_from_cascade_event_copies_is_test_false(self, cascade_event_is_test_false):
        """is_test=False가 Archive로 복사되는지 확인."""
        archive = CascadeEventArchive.from_cascade_event(cascade_event_is_test_false)

        assert archive.is_test is False

    def test_from_cascade_event_preserves_other_fields(self, cascade_event_is_test_true):
        """is_test 외 다른 필드들도 정상 복사되는지 확인."""
        archive = CascadeEventArchive.from_cascade_event(cascade_event_is_test_true)

        assert archive.cascade_id == cascade_event_is_test_true.id
        assert archive.namespace == cascade_event_is_test_true.namespace
        assert archive.trigger_type == cascade_event_is_test_true.trigger.trigger_type
        assert archive.current_hash == cascade_event_is_test_true.current_hash


class TestCascadeEventArchiveQueryFilter:
    """is_test 필터링 쿼리 테스트."""

    @pytest.mark.skip(reason="selfhealing_cascade_events 테이블 마이그레이션 필요")
    @pytest.mark.django_db
    def test_filter_production_events_only(self, cascade_event_is_test_true, cascade_event_is_test_false):
        """is_test=False 필터로 운영 데이터만 조회."""
        # Archive 생성 및 저장
        archive_test = CascadeEventArchive.from_cascade_event(cascade_event_is_test_true)
        archive_prod = CascadeEventArchive.from_cascade_event(cascade_event_is_test_false)

        archive_test.save()
        archive_prod.save()

        try:
            # 운영 데이터만 조회
            production_events = CascadeEventArchive.objects.filter(is_test=False)
            assert production_events.count() >= 1

            # 테스트 데이터 제외 확인
            for event in production_events:
                assert event.is_test is False
        finally:
            # Cleanup
            CascadeEventArchive.objects.filter(cascade_id__in=[archive_test.cascade_id, archive_prod.cascade_id]).delete()

    @pytest.mark.skip(reason="selfhealing_cascade_events 테이블 마이그레이션 필요")
    @pytest.mark.django_db
    def test_filter_test_events_only(self, cascade_event_is_test_true, cascade_event_is_test_false):
        """is_test=True 필터로 테스트 데이터만 조회."""
        # Archive 생성 및 저장
        archive_test = CascadeEventArchive.from_cascade_event(cascade_event_is_test_true)
        archive_prod = CascadeEventArchive.from_cascade_event(cascade_event_is_test_false)

        archive_test.save()
        archive_prod.save()

        try:
            # 테스트 데이터만 조회
            test_events = CascadeEventArchive.objects.filter(is_test=True)
            assert test_events.count() >= 1

            # 운영 데이터 제외 확인
            for event in test_events:
                assert event.is_test is True
        finally:
            # Cleanup
            CascadeEventArchive.objects.filter(cascade_id__in=[archive_test.cascade_id, archive_prod.cascade_id]).delete()


class TestCascadeEventWithTestModeContext:
    """TestModeContext 연동 통합 테스트."""

    def test_event_created_in_test_mode_has_is_test_true(self, sample_trigger, sample_effects):
        """TestModeContext 내에서 생성된 이벤트의 is_test=True 확인."""
        with TestModeContext.start(session_id="xtest-django-001"):
            event = CascadeEvent(
                id="cascade-django-test-001",
                trigger=sample_trigger,
                effects=sample_effects,
                namespace="tokyo",
                timestamp="2026-01-27T16:00:00+00:00",
                is_test=TestModeContext.is_synthetic(),
            )
            event.current_hash = event.calculate_hash()

            archive = CascadeEventArchive.from_cascade_event(event)

            assert archive.is_test is True

    def test_event_created_outside_test_mode_has_is_test_false(self, sample_trigger, sample_effects):
        """TestModeContext 외부에서 생성된 이벤트의 is_test=False 확인."""
        event = CascadeEvent(
            id="cascade-django-prod-001",
            trigger=sample_trigger,
            effects=sample_effects,
            namespace="tokyo",
            timestamp="2026-01-27T16:00:00+00:00",
            is_test=TestModeContext.is_synthetic(),
        )
        event.current_hash = event.calculate_hash()

        archive = CascadeEventArchive.from_cascade_event(event)

        assert archive.is_test is False

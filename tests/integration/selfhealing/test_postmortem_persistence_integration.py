"""
Postmortem Persistence Integration Tests (문서 132).

PostgreSQL 영속성 기능 통합 테스트.
실제 Django 환경에서 Post-mortem 저장, 조회, 필터링 전체 흐름 검증.

Requirements:
- Docker Compose for Redis, DB
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_postmortem_persistence_integration.py -v
"""

import os
import pytest
import uuid
from datetime import datetime, timezone as dt_timezone, timedelta

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


@pytest.fixture(autouse=True)
def clean_postmortem_records():
    """각 테스트 전후 PostmortemRecord 정리."""
    from shopping.models import PostmortemRecord
    from selfhealing.api.django.views.xtest.base import (
        _healing_incidents,
        _healing_events_lock,
        set_db_persistence_enabled,
    )

    # 테스트 전 정리
    PostmortemRecord.objects.all().delete()
    with _healing_events_lock:
        _healing_incidents.clear()
    set_db_persistence_enabled(True)

    yield

    # 테스트 후 정리
    PostmortemRecord.objects.all().delete()
    with _healing_events_lock:
        _healing_incidents.clear()
    set_db_persistence_enabled(True)


@pytest.mark.django_db(transaction=True)
class TestPostmortemModelPersistence:
    """PostmortemRecord 모델 영속성 테스트."""

    def test_create_postmortem_record_directly(self):
        """PostmortemRecord를 직접 생성하고 저장."""
        from shopping.models import PostmortemRecord

        record = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"test-direct-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            resolved_at=datetime.now(dt_timezone.utc) + timedelta(minutes=30),
            duration_seconds=1800.0,
            affected_services=["payment", "order"],
            timeline=[{"time": "10:00", "event": "Error detected"}],
            auto_actions=[{"action": "circuit_breaker_open"}],
            recommendations=["Scale service"],
            system_snapshot={"cpu_percent": 85.0},
            source=PostmortemRecord.Source.AUTO,
        )
        record.save()

        # DB에서 조회
        saved = PostmortemRecord.objects.get(incident_id=record.incident_id)
        assert saved.duration_seconds == 1800.0
        assert "payment" in saved.affected_services
        assert saved.source == "auto"

    def test_create_from_incident_dict(self):
        """인시던트 딕셔너리에서 PostmortemRecord 생성."""
        from shopping.models import PostmortemRecord

        incident_data = {
            "incident_id": f"test-dict-{uuid.uuid4().hex[:8]}",
            "started_at": "2026-01-28T10:00:00+00:00",
            "resolved_at": "2026-01-28T10:30:00+00:00",
            "duration_seconds": 1800.0,
            "affected_services": ["payment"],
            "timeline": [],
            "auto_actions": [],
            "recommendations": [],
            "system_snapshot": {},
            "source": "manual",
        }

        record = PostmortemRecord.create_from_incident_dict(incident_data)
        record.save()

        saved = PostmortemRecord.objects.get(incident_id=incident_data["incident_id"])
        assert saved.source == "manual"
        assert saved.duration_seconds == 1800.0

    def test_to_dict_contains_all_fields(self):
        """to_dict()가 모든 필드를 반환."""
        from shopping.models import PostmortemRecord

        record = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"test-todict-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            duration_seconds=600.0,
            affected_services=["test"],
            source=PostmortemRecord.Source.AUTO,
        )
        record.save()

        data = record.to_dict()

        expected_fields = [
            "id",
            "incident_id",
            "started_at",
            "resolved_at",
            "duration_seconds",
            "affected_services",
            "timeline",
            "auto_actions",
            "recommendations",
            "system_snapshot",
            "created_at",
            "source",
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"

    def test_unique_incident_id_constraint(self):
        """incident_id가 고유해야 함."""
        from shopping.models import PostmortemRecord
        from django.db import IntegrityError

        incident_id = f"test-unique-{uuid.uuid4().hex[:8]}"

        record1 = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=incident_id,
            started_at=datetime.now(dt_timezone.utc),
        )
        record1.save()

        record2 = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=incident_id,  # 중복
            started_at=datetime.now(dt_timezone.utc),
        )

        with pytest.raises(IntegrityError):
            record2.save()


@pytest.mark.django_db(transaction=True)
class TestAddHealingIncidentWithDbPersistence:
    """add_healing_incident() DB 영속성 테스트."""

    def test_add_incident_saves_to_db(self):
        """인시던트 추가 시 DB에 저장."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        incident_id = f"test-add-db-{uuid.uuid4().hex[:8]}"
        add_healing_incident(
            {
                "incident_id": incident_id,
                "started_at": datetime.now(dt_timezone.utc).isoformat(),
                "affected_services": ["payment"],
                "duration_seconds": 300.0,
            }
        )

        # DB에서 조회
        assert PostmortemRecord.objects.filter(incident_id=incident_id).exists()

    def test_add_incident_also_saves_to_memory(self):
        """인시던트 추가 시 메모리 캐시에도 저장."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
            _healing_incidents,
            _healing_events_lock,
        )

        set_db_persistence_enabled(True)

        incident_id = f"test-add-mem-{uuid.uuid4().hex[:8]}"
        add_healing_incident(
            {
                "incident_id": incident_id,
                "started_at": datetime.now(dt_timezone.utc).isoformat(),
            }
        )

        # 메모리에서 확인
        with _healing_events_lock:
            found = any(i.get("incident_id") == incident_id for i in _healing_incidents)
        assert found

    def test_add_incident_adds_recorded_at(self):
        """인시던트 추가 시 recorded_at 필드 추가."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(True)

        incident = {
            "incident_id": f"test-recorded-{uuid.uuid4().hex[:8]}",
            "started_at": datetime.now(dt_timezone.utc).isoformat(),
        }
        add_healing_incident(incident)

        assert "recorded_at" in incident

    def test_add_incident_fallback_to_memory_when_db_fails(self):
        """DB 저장 실패 시 메모리에 저장."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
            _healing_incidents,
            _healing_events_lock,
            _get_postmortem_model,
        )
        from unittest.mock import patch

        set_db_persistence_enabled(True)

        incident_id = f"test-fallback-{uuid.uuid4().hex[:8]}"

        # DB 모델을 None으로 반환하여 DB 저장 실패 시뮬레이션
        with patch(
            "selfhealing.api.django.views.xtest.base._get_postmortem_model",
            return_value=None,
        ):
            add_healing_incident(
                {
                    "incident_id": incident_id,
                    "started_at": datetime.now(dt_timezone.utc).isoformat(),
                }
            )

        # 메모리에 저장되었는지 확인
        with _healing_events_lock:
            found = any(i.get("incident_id") == incident_id for i in _healing_incidents)
        assert found


@pytest.mark.django_db(transaction=True)
class TestGetHealingIncidentsFromDb:
    """get_healing_incidents() DB 조회 테스트."""

    def test_get_incidents_from_db_with_limit(self):
        """limit 파라미터로 DB에서 조회."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            get_healing_incidents,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(True)

        # 5개 인시던트 추가
        for i in range(5):
            add_healing_incident(
                {
                    "incident_id": f"test-limit-{i}-{uuid.uuid4().hex[:8]}",
                    "started_at": datetime.now(dt_timezone.utc).isoformat(),
                }
            )

        # limit=3으로 조회
        incidents = get_healing_incidents(limit=3, use_db=True)
        assert len(incidents) <= 3

    def test_get_incidents_with_date_filter(self):
        """날짜 필터로 DB 조회."""
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        # 과거 인시던트 직접 생성
        old_record = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"old-{uuid.uuid4().hex[:8]}",
            started_at=datetime(2025, 1, 1, tzinfo=dt_timezone.utc),
        )
        old_record.save()

        # 최근 인시던트 생성
        new_record = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"new-{uuid.uuid4().hex[:8]}",
            started_at=datetime(2026, 1, 28, tzinfo=dt_timezone.utc),
        )
        new_record.save()

        # 2026년 이후만 조회
        incidents = get_healing_incidents(
            limit=10,
            start_date="2026-01-01T00:00:00+00:00",
            use_db=True,
        )

        incident_ids = [i["incident_id"] for i in incidents]
        assert new_record.incident_id in incident_ids
        assert old_record.incident_id not in incident_ids

    def test_get_incidents_with_min_duration_filter(self):
        """최소 지속 시간 필터로 DB 조회."""
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        # 짧은 인시던트
        short = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"short-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            duration_seconds=60.0,
        )
        short.save()

        # 긴 인시던트
        long = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"long-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            duration_seconds=600.0,
        )
        long.save()

        # 300초 이상만 조회
        incidents = get_healing_incidents(
            limit=10,
            min_duration=300.0,
            use_db=True,
        )

        incident_ids = [i["incident_id"] for i in incidents]
        assert long.incident_id in incident_ids
        assert short.incident_id not in incident_ids

    def test_get_incidents_with_service_filter(self):
        """서비스 필터로 DB 조회."""
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        # payment 서비스 인시던트
        payment = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"payment-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            affected_services=["payment", "order"],
        )
        payment.save()

        # inventory 서비스 인시던트
        inventory = PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"inventory-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            affected_services=["inventory"],
        )
        inventory.save()

        # payment 서비스만 조회
        incidents = get_healing_incidents(
            limit=10,
            service="payment",
            use_db=True,
        )

        incident_ids = [i["incident_id"] for i in incidents]
        assert payment.incident_id in incident_ids
        assert inventory.incident_id not in incident_ids

    def test_get_incidents_with_pagination(self):
        """페이지네이션으로 DB 조회."""
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        # 10개 인시던트 생성
        for i in range(10):
            PostmortemRecord(
                id=uuid.uuid4(),
                incident_id=f"page-{i}-{uuid.uuid4().hex[:8]}",
                started_at=datetime.now(dt_timezone.utc) - timedelta(hours=i),
            ).save()

        # 첫 페이지
        page1 = get_healing_incidents(limit=3, offset=0, use_db=True)
        # 두 번째 페이지
        page2 = get_healing_incidents(limit=3, offset=3, use_db=True)

        # 서로 다른 결과
        page1_ids = {i["incident_id"] for i in page1}
        page2_ids = {i["incident_id"] for i in page2}
        assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.django_db(transaction=True)
class TestGetHealingIncidentsCount:
    """get_healing_incidents_count() 테스트."""

    def test_count_all_incidents(self):
        """전체 인시던트 카운트."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            get_healing_incidents_count,
            set_db_persistence_enabled,
        )

        set_db_persistence_enabled(True)

        # 3개 추가
        for i in range(3):
            add_healing_incident(
                {
                    "incident_id": f"count-{i}-{uuid.uuid4().hex[:8]}",
                    "started_at": datetime.now(dt_timezone.utc).isoformat(),
                }
            )

        count = get_healing_incidents_count(use_db=False)  # 메모리 카운트
        assert count >= 3

    def test_count_with_filter(self):
        """필터 적용 카운트."""
        from selfhealing.api.django.views.xtest.base import (
            get_healing_incidents_count,
            set_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)

        # 다양한 duration으로 생성
        PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"short-cnt-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            duration_seconds=60.0,
        ).save()

        PostmortemRecord(
            id=uuid.uuid4(),
            incident_id=f"long-cnt-{uuid.uuid4().hex[:8]}",
            started_at=datetime.now(dt_timezone.utc),
            duration_seconds=600.0,
        ).save()

        # min_duration 필터로 카운트
        count = get_healing_incidents_count(min_duration=300.0, use_db=True)
        assert count >= 1


@pytest.mark.django_db(transaction=True)
class TestDbPersistenceToggle:
    """DB 영속성 활성화/비활성화 테스트."""

    def test_disable_db_persistence(self):
        """DB 영속성 비활성화 시 메모리만 사용."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
            get_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(False)
        assert get_db_persistence_enabled() is False

        incident_id = f"no-db-{uuid.uuid4().hex[:8]}"
        add_healing_incident(
            {
                "incident_id": incident_id,
                "started_at": datetime.now(dt_timezone.utc).isoformat(),
            }
        )

        # DB에는 저장되지 않음
        assert not PostmortemRecord.objects.filter(incident_id=incident_id).exists()

    def test_enable_db_persistence(self):
        """DB 영속성 활성화 시 DB에 저장."""
        from selfhealing.api.django.views.xtest.base import (
            add_healing_incident,
            set_db_persistence_enabled,
            get_db_persistence_enabled,
        )
        from shopping.models import PostmortemRecord

        set_db_persistence_enabled(True)
        assert get_db_persistence_enabled() is True

        incident_id = f"with-db-{uuid.uuid4().hex[:8]}"
        add_healing_incident(
            {
                "incident_id": incident_id,
                "started_at": datetime.now(dt_timezone.utc).isoformat(),
            }
        )

        # DB에 저장됨
        assert PostmortemRecord.objects.filter(incident_id=incident_id).exists()

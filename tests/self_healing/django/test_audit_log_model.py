"""
Django Audit Log 모델 테스트.

테스트 범위:
1. AuditLog 모델 생성
2. audit_event_id Unique 제약
3. insert_ignore_conflict() 중복 처리
4. bulk_insert_ignore_conflict() PostgreSQL ON CONFLICT DO NOTHING
5. 해시 체인 필드 저장

Docker Compose로 실행:
    docker-compose -f docker-compose.test.yml run --rm test \
        pytest tests/self_healing/django/test_audit_log_model.py -v
"""

from datetime import datetime, timezone

import pytest


@pytest.mark.django_db(transaction=True)
class TestAuditLogModel:
    """AuditLog 모델 테스트."""

    def test_create_audit_log(self):
        """AuditLog 생성 테스트."""
        from shopping.models import AuditLog

        log = AuditLog.objects.create(
            audit_event_id="test:create:001",
            action="AUTO_TUNING_ADJUSTMENT",
            timestamp=datetime.now(timezone.utc),
            actor_id="test_user",
            actor_type="user",
            target_type="runtime_config",
            target_id="timeout_ms",
            service_name="payment",
            reason="P99 레이턴시 증가",
            details={"before": {"value": 5000}, "after": {"value": 6000}},
            success=True,
        )

        assert log.pk is not None
        assert log.audit_event_id == "test:create:001"
        assert log.action == "AUTO_TUNING_ADJUSTMENT"

    def test_audit_event_id_unique_constraint(self):
        """audit_event_id Unique 제약 테스트."""
        from django.db import IntegrityError
        from shopping.models import AuditLog

        # 첫 번째 생성
        AuditLog.objects.create(
            audit_event_id="test:unique:001",
            action="CB_FORCE_OPEN",
            timestamp=datetime.now(timezone.utc),
        )

        # 같은 audit_event_id로 생성 시도 → IntegrityError
        with pytest.raises(IntegrityError):
            AuditLog.objects.create(
                audit_event_id="test:unique:001",
                action="CB_FORCE_CLOSE",
                timestamp=datetime.now(timezone.utc),
            )

    def test_insert_ignore_conflict_new_record(self):
        """insert_ignore_conflict - 새 레코드 삽입."""
        from shopping.models import AuditLog

        log, created = AuditLog.insert_ignore_conflict(
            audit_event_id="test:ignore:new:001",
            action="DLQ_STORE",
            timestamp=datetime.now(timezone.utc),
            service_name="order",
        )

        assert created is True
        assert log.pk is not None
        assert log.audit_event_id == "test:ignore:new:001"

    def test_insert_ignore_conflict_duplicate_record(self):
        """insert_ignore_conflict - 중복 레코드 무시."""
        from shopping.models import AuditLog

        # 첫 번째 삽입
        log1, created1 = AuditLog.insert_ignore_conflict(
            audit_event_id="test:ignore:dup:001",
            action="DLQ_STORE",
            timestamp=datetime.now(timezone.utc),
            service_name="order",
        )

        # 같은 ID로 두 번째 삽입 시도
        log2, created2 = AuditLog.insert_ignore_conflict(
            audit_event_id="test:ignore:dup:001",
            action="DLQ_REPLAY",  # 다른 action
            timestamp=datetime.now(timezone.utc),
            service_name="payment",  # 다른 service
        )

        assert created1 is True
        assert created2 is False  # 중복 → created=False
        assert log1.pk == log2.pk  # 같은 레코드
        assert log2.action == "DLQ_STORE"  # 원본 유지

    def test_hash_chain_fields(self):
        """해시 체인 필드 저장 테스트."""
        from shopping.models import AuditLog

        log = AuditLog.objects.create(
            audit_event_id="test:hashchain:001",
            action="CONFIG_CHANGE",
            timestamp=datetime.now(timezone.utc),
            integrity_hash="sha256:abc123def456",
            previous_hash="sha256:000000000000",
            sequence_number=42,
        )

        assert log.integrity_hash == "sha256:abc123def456"
        assert log.previous_hash == "sha256:000000000000"
        assert log.sequence_number == 42

    def test_ordering_by_sequence_number(self):
        """sequence_number 내림차순 정렬 테스트."""
        from shopping.models import AuditLog

        # 순서 뒤섞어서 생성
        for seq in [3, 1, 5, 2, 4]:
            AuditLog.objects.create(
                audit_event_id=f"test:order:{seq}",
                action="TEST",
                timestamp=datetime.now(timezone.utc),
                sequence_number=seq,
            )

        # 기본 정렬 확인 (sequence_number 내림차순)
        logs = list(AuditLog.objects.filter(audit_event_id__startswith="test:order:"))

        sequences = [log.sequence_number for log in logs]
        assert sequences == [5, 4, 3, 2, 1]


@pytest.mark.django_db(transaction=True)
class TestAuditLogBulkInsert:
    """AuditLog 벌크 삽입 테스트."""

    def test_bulk_insert_ignore_conflict_all_new(self):
        """bulk_insert_ignore_conflict - 모든 레코드 새로 삽입."""
        from shopping.models import AuditLog

        entries = [
            {
                "audit_event_id": f"test:bulk:new:{i}",
                "action": "BULK_TEST",
                "timestamp": datetime.now(timezone.utc),
                "sequence_number": i,
            }
            for i in range(1, 6)
        ]

        inserted, skipped = AuditLog.bulk_insert_ignore_conflict(entries)

        assert inserted == 5
        assert skipped == 0
        assert AuditLog.objects.filter(action="BULK_TEST").count() == 5

    def test_bulk_insert_ignore_conflict_with_duplicates(self):
        """bulk_insert_ignore_conflict - 일부 중복 포함."""
        from shopping.models import AuditLog

        # 미리 일부 레코드 생성
        AuditLog.objects.create(
            audit_event_id="test:bulk:dup:1",
            action="PRE_EXISTING",
            timestamp=datetime.now(timezone.utc),
        )
        AuditLog.objects.create(
            audit_event_id="test:bulk:dup:3",
            action="PRE_EXISTING",
            timestamp=datetime.now(timezone.utc),
        )

        # 벌크 삽입 (1, 3은 중복)
        entries = [
            {
                "audit_event_id": f"test:bulk:dup:{i}",
                "action": "BULK_NEW",
                "timestamp": datetime.now(timezone.utc),
            }
            for i in range(1, 6)
        ]

        inserted, skipped = AuditLog.bulk_insert_ignore_conflict(entries)

        assert inserted == 3  # 2, 4, 5만 삽입
        assert skipped == 2  # 1, 3은 스킵

        # 원본 레코드 유지 확인
        log1 = AuditLog.objects.get(audit_event_id="test:bulk:dup:1")
        assert log1.action == "PRE_EXISTING"

    def test_bulk_insert_empty_list(self):
        """bulk_insert_ignore_conflict - 빈 리스트."""
        from shopping.models import AuditLog

        inserted, skipped = AuditLog.bulk_insert_ignore_conflict([])

        assert inserted == 0
        assert skipped == 0


@pytest.mark.django_db(transaction=True)
class TestWALRecoveryDeduplication:
    """WAL 복구 시 중복 제거 시나리오 테스트."""

    def test_wal_recovery_duplicate_prevention(self):
        """WAL 복구 시 audit_event_id로 중복 방지."""
        from shopping.models import AuditLog

        # WAL 복구 형식의 audit_event_id
        wal_event_id = "wal:12345:pg_insert"

        # 첫 번째 복구 시도 (성공)
        log1, created1 = AuditLog.insert_ignore_conflict(
            audit_event_id=wal_event_id,
            action="AUTO_TUNING_ADJUSTMENT",
            timestamp=datetime.now(timezone.utc),
            details={"parameter": "timeout_ms", "before": 5000, "after": 6000},
        )

        # 프로세스 재기동 후 같은 WAL 엔트리 복구 시도 (무시됨)
        log2, created2 = AuditLog.insert_ignore_conflict(
            audit_event_id=wal_event_id,
            action="AUTO_TUNING_ADJUSTMENT",
            timestamp=datetime.now(timezone.utc),
            details={"parameter": "timeout_ms", "before": 5000, "after": 6000},
        )

        assert created1 is True
        assert created2 is False
        assert log1.pk == log2.pk

        # 전체 레코드 수 확인 (중복 없음)
        count = AuditLog.objects.filter(audit_event_id=wal_event_id).count()
        assert count == 1

    def test_wal_recovery_batch_with_mixed_duplicates(self):
        """WAL 복구 배치에서 일부만 중복인 경우."""
        from shopping.models import AuditLog

        # 이전에 성공적으로 복구된 엔트리
        AuditLog.objects.create(
            audit_event_id="wal:100:pg_insert",
            action="CB_FORCE_OPEN",
            timestamp=datetime.now(timezone.utc),
        )
        AuditLog.objects.create(
            audit_event_id="wal:102:pg_insert",
            action="CONFIG_CHANGE",
            timestamp=datetime.now(timezone.utc),
        )

        # WAL 복구 배치 (100, 101, 102, 103, 104)
        entries = [
            {
                "audit_event_id": f"wal:{seq}:pg_insert",
                "action": "RECOVERY_TEST",
                "timestamp": datetime.now(timezone.utc),
            }
            for seq in range(100, 105)
        ]

        inserted, skipped = AuditLog.bulk_insert_ignore_conflict(entries)

        # 100, 102는 이미 존재 → 스킵
        # 101, 103, 104는 새로 삽입
        assert inserted == 3
        assert skipped == 2

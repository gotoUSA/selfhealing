"""
Django Audit Log Adapter 테스트.

테스트 범위:
1. DjangoAuditLogAdapter 기본 동작
2. AuditEntry → AuditLog 변환
3. log() 중복 무시
4. log_batch() 벌크 삽입
5. query() 필터링
6. ContinuousAuditRecorder 연동

Docker Compose로 실행:
    docker-compose -f docker-compose.test.yml run --rm test \
        pytest tests/self_healing/django/test_django_audit_adapter.py -v
"""

from datetime import datetime, timezone, timedelta

import pytest

from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry


@pytest.mark.django_db(transaction=True)
class TestDjangoAuditLogAdapter:
    """DjangoAuditLogAdapter 테스트."""
    
    @pytest.fixture
    def adapter(self):
        """DjangoAuditLogAdapter 인스턴스."""
        from selfhealing.adapters.audit.django_adapter import DjangoAuditLogAdapter
        from shopping.models import AuditLog
        
        return DjangoAuditLogAdapter(model_class=AuditLog)
    
    def test_log_single_entry(self, adapter):
        """단일 AuditEntry 로깅."""
        from shopping.models import AuditLog
        
        entry = AuditEntry(
            action=AuditAction.AUTO_TUNING_ADJUSTMENT,
            actor_id="test_user",
            actor_type="user",
            target_type="runtime_config",
            target_id="timeout_ms",
            service_name="payment",
            reason="P99 증가",
            details={
                "audit_event_id": "adapter:test:001",
                "parameter": "timeout_ms",
                "before": {"value": 5000},
                "after": {"value": 6000},
            },
        )
        
        adapter.log(entry)
        
        # DB 확인
        log = AuditLog.objects.get(audit_event_id="adapter:test:001")
        assert log.action == "auto_tuning_adjustment"  # AuditAction enum value는 소문자
        assert log.actor_id == "test_user"
        assert log.service_name == "payment"
    
    def test_log_duplicate_ignored(self, adapter):
        """중복 AuditEntry 무시."""
        from shopping.models import AuditLog
        
        entry1 = AuditEntry(
            action=AuditAction.CB_FORCE_OPEN,
            details={"audit_event_id": "adapter:dup:001"},
        )
        
        entry2 = AuditEntry(
            action=AuditAction.CB_FORCE_CLOSE,  # 다른 action
            details={"audit_event_id": "adapter:dup:001"},  # 같은 ID
        )
        
        adapter.log(entry1)
        adapter.log(entry2)  # 중복 무시
        
        # 첫 번째 레코드만 존재
        log = AuditLog.objects.get(audit_event_id="adapter:dup:001")
        assert log.action == "cb_force_open"  # AuditAction enum value는 소문자
        
        # 레코드 수 확인
        count = AuditLog.objects.filter(audit_event_id="adapter:dup:001").count()
        assert count == 1
    
    def test_log_with_wal_sequence(self, adapter):
        """WAL sequence 기반 audit_event_id 생성."""
        from shopping.models import AuditLog
        
        entry = AuditEntry(
            action=AuditAction.DLQ_STORE,
            details={
                "wal_sequence": 12345,
                "operation": "pg_insert",
            },
        )
        
        adapter.log(entry)
        
        # WAL 형식 ID로 저장됨
        log = AuditLog.objects.get(audit_event_id="wal:12345:pg_insert")
        assert log.action == "dlq_store"  # AuditAction enum value는 소문자
    
    def test_log_auto_generate_event_id(self, adapter):
        """audit_event_id 자동 생성."""
        from shopping.models import AuditLog
        
        entry = AuditEntry(
            action=AuditAction.CONFIG_CHANGE,
            details={},  # audit_event_id, wal_sequence 없음
        )
        
        adapter.log(entry)
        
        # auto: 접두사로 생성됨
        log = AuditLog.objects.filter(audit_event_id__startswith="auto:").first()
        assert log is not None
        assert log.action == "config_change"  # AuditAction enum value는 소문자
    
    def test_log_batch_success(self, adapter):
        """배치 로깅 성공."""
        from shopping.models import AuditLog
        
        entries = [
            AuditEntry(
                action=AuditAction.AUTO_TUNING_ADJUSTMENT,
                details={"audit_event_id": f"batch:test:{i}"},
            )
            for i in range(1, 6)
        ]
        
        inserted, skipped = adapter.log_batch(entries)
        
        assert inserted == 5
        assert skipped == 0
        assert AuditLog.objects.filter(
            audit_event_id__startswith="batch:test:"
        ).count() == 5
    
    def test_log_batch_with_duplicates(self, adapter):
        """배치 로깅 - 중복 포함."""
        from shopping.models import AuditLog
        
        # 미리 일부 생성
        entry_pre = AuditEntry(
            action=AuditAction.CB_FORCE_OPEN,
            details={"audit_event_id": "batch:dup:2"},
        )
        adapter.log(entry_pre)
        
        # 배치 (2는 중복)
        entries = [
            AuditEntry(
                action=AuditAction.AUTO_TUNING_ADJUSTMENT,
                details={"audit_event_id": f"batch:dup:{i}"},
            )
            for i in range(1, 4)
        ]
        
        inserted, skipped = adapter.log_batch(entries)
        
        assert inserted == 2  # 1, 3
        assert skipped == 1   # 2


@pytest.mark.django_db(transaction=True)
class TestDjangoAuditLogAdapterQuery:
    """DjangoAuditLogAdapter 쿼리 테스트."""
    
    @pytest.fixture
    def adapter_with_data(self):
        """테스트 데이터가 있는 어댑터."""
        from selfhealing.adapters.audit.django_adapter import DjangoAuditLogAdapter
        from shopping.models import AuditLog
        
        adapter = DjangoAuditLogAdapter(model_class=AuditLog)
        
        # 테스트 데이터 생성
        now = datetime.now(timezone.utc)
        entries = [
            AuditEntry(
                action=AuditAction.AUTO_TUNING_ADJUSTMENT,
                target_type="runtime_config",
                target_id="timeout_ms",
                timestamp=now - timedelta(hours=2),
                details={"audit_event_id": "query:test:1"},
            ),
            AuditEntry(
                action=AuditAction.CB_FORCE_OPEN,
                target_type="circuit_breaker",
                target_id="payment",
                timestamp=now - timedelta(hours=1),
                details={"audit_event_id": "query:test:2"},
            ),
            AuditEntry(
                action=AuditAction.AUTO_TUNING_ADJUSTMENT,
                target_type="runtime_config",
                target_id="retry_count",
                timestamp=now,
                details={"audit_event_id": "query:test:3"},
            ),
        ]
        
        for entry in entries:
            adapter.log(entry)
        
        return adapter
    
    def test_query_by_action(self, adapter_with_data):
        """action으로 쿼리."""
        results = adapter_with_data.query(
            action=AuditAction.AUTO_TUNING_ADJUSTMENT
        )
        
        assert len(results) == 2
        for entry in results:
            assert entry.action == "auto_tuning_adjustment"  # AuditAction enum value는 소문자
    
    def test_query_by_target(self, adapter_with_data):
        """target_type, target_id로 쿼리."""
        results = adapter_with_data.query(
            target_type="circuit_breaker",
            target_id="payment",
        )
        
        assert len(results) == 1
        assert results[0].action == "cb_force_open"  # AuditAction enum value는 소문자
    
    def test_query_by_time_range(self, adapter_with_data):
        """시간 범위로 쿼리."""
        now = datetime.now(timezone.utc)
        
        results = adapter_with_data.query(
            start_time=now - timedelta(hours=1, minutes=30),
            end_time=now + timedelta(minutes=1),
        )
        
        # 1시간 전, 현재 레코드만 포함
        assert len(results) == 2
    
    def test_query_with_limit(self, adapter_with_data):
        """limit 적용."""
        results = adapter_with_data.query(limit=1)
        
        assert len(results) == 1


@pytest.mark.django_db(transaction=True)
class TestDjangoAuditLogAdapterIntegration:
    """ContinuousAuditRecorder 연동 테스트."""
    
    def test_with_continuous_audit_recorder(self):
        """ContinuousAuditRecorder와 연동."""
        from selfhealing.adapters.audit.django_adapter import DjangoAuditLogAdapter
        from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
        from shopping.models import AuditLog
        
        adapter = DjangoAuditLogAdapter(model_class=AuditLog)
        recorder = ContinuousAuditRecorder(
            audit_adapter=adapter,
            wal_enabled=False,
        )
        
        # Auto Tuning 기록
        audit_id = recorder.record_auto_tuning(
            parameter="max_connections",
            old_value=100,
            new_value=150,
            reason="연결 부족",
            confidence=0.9,
            metrics_snapshot={"active_connections": 95},
            safety_check={"within_bounds": True},
        )
        
        assert audit_id is not None
        
        # DB 확인
        log = AuditLog.objects.filter(action="auto_tuning_adjustment").first()
        assert log is not None
        assert log.details["parameter"] == "max_connections"
        assert log.details["before"]["value"] == 100
        assert log.details["after"]["value"] == 150

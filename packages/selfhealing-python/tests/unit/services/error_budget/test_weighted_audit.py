"""
WeightedBudgetAuditEntry Unit Tests.

가중치 근거 포함 무결성 로그 스키마 테스트.

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.6
"""

import pytest
from datetime import datetime

from selfhealing.services.error_budget.weighted_audit import (
    WeightedBudgetAuditEntry,
    WeightedAuditRecorder,
    get_weighted_audit_recorder,
    reset_weighted_audit_recorder,
)


# =============================================================================
# WeightedBudgetAuditEntry Tests
# =============================================================================

class TestWeightedBudgetAuditEntry:
    """WeightedBudgetAuditEntry 테스트."""
    
    # -------------------------------------------------------------------------
    # Default Values Tests
    # -------------------------------------------------------------------------
    
    def test_default_values(self):
        """기본값 확인."""
        entry = WeightedBudgetAuditEntry()
        
        assert entry.raw_consumption_minutes == 0.0
        assert entry.weighted_consumption_minutes == 0.0
        assert entry.level_multiplier == 1.0
        assert entry.domain_multiplier == 1.0
        assert entry.final_multiplier == 1.0
        assert entry.emergency_id is None
    
    def test_audit_id_generated(self):
        """audit_id 자동 생성."""
        entry = WeightedBudgetAuditEntry()
        
        assert entry.audit_id.startswith("wba_")
        assert len(entry.audit_id) > 10
    
    def test_recorded_at_set(self):
        """recorded_at 자동 설정."""
        entry = WeightedBudgetAuditEntry()
        
        assert entry.recorded_at is not None
        assert isinstance(entry.recorded_at, datetime)
    
    # -------------------------------------------------------------------------
    # to_dict Tests
    # -------------------------------------------------------------------------
    
    def test_to_dict(self):
        """딕셔너리 변환."""
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
            level_multiplier=5.0,
            emergency_id="emg_123",
            namespace="seoul",
        )
        
        data = entry.to_dict()
        
        assert data["raw_consumption_minutes"] == 1.0
        assert data["weighted_consumption_minutes"] == 5.0
        assert data["level_multiplier"] == 5.0
        assert data["emergency_id"] == "emg_123"
        assert data["namespace"] == "seoul"
    
    def test_to_dict_contains_all_fields(self):
        """to_dict에 모든 필드 포함."""
        entry = WeightedBudgetAuditEntry()
        
        data = entry.to_dict()
        
        expected_fields = [
            "audit_id", "recorded_at",
            "raw_consumption_minutes", "weighted_consumption_minutes",
            "level_multiplier", "domain_multiplier", "final_multiplier",
            "emergency_id", "emergency_level",
            "crisis_domain", "error_domain", "hop_distance",
            "namespace", "service_name", "error_type",
        ]
        
        for field in expected_fields:
            assert field in data
    
    # -------------------------------------------------------------------------
    # to_hash_chain_entry Tests
    # -------------------------------------------------------------------------
    
    def test_to_hash_chain_entry(self):
        """Hash Chain 엔트리 변환."""
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
            level_multiplier=5.0,
            emergency_id="emg_123",
        )
        
        chain_entry = entry.to_hash_chain_entry()
        
        assert chain_entry["type"] == "weighted_budget_consumption"
        assert "multipliers" in chain_entry
        assert "evidence" in chain_entry
        assert chain_entry["multipliers"]["level"] == 5.0
        assert chain_entry["evidence"]["emergency_id"] == "emg_123"
    
    def test_to_hash_chain_entry_structure(self):
        """Hash Chain 엔트리 구조 확인."""
        entry = WeightedBudgetAuditEntry()
        
        chain_entry = entry.to_hash_chain_entry()
        
        assert "type" in chain_entry
        assert "audit_id" in chain_entry
        assert "timestamp" in chain_entry
        assert "consumption" in chain_entry
        assert "multipliers" in chain_entry
        assert "evidence" in chain_entry
        assert "context" in chain_entry
    
    # -------------------------------------------------------------------------
    # from_dict Tests
    # -------------------------------------------------------------------------
    
    def test_from_dict(self):
        """딕셔너리에서 생성."""
        data = {
            "audit_id": "wba_test123",
            "recorded_at": "2026-01-22T10:00:00",
            "raw_consumption_minutes": 2.0,
            "weighted_consumption_minutes": 6.0,
            "level_multiplier": 3.0,
            "emergency_id": "emg_456",
        }
        
        entry = WeightedBudgetAuditEntry.from_dict(data)
        
        assert entry.audit_id == "wba_test123"
        assert entry.raw_consumption_minutes == 2.0
        assert entry.weighted_consumption_minutes == 6.0
        assert entry.level_multiplier == 3.0
        assert entry.emergency_id == "emg_456"
    
    def test_from_dict_roundtrip(self):
        """to_dict -> from_dict 왕복 테스트."""
        original = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.5,
            weighted_consumption_minutes=7.5,
            level_multiplier=5.0,
            domain_multiplier=2.0,
            final_multiplier=10.0,
            emergency_id="emg_789",
            namespace="tokyo",
        )
        
        data = original.to_dict()
        restored = WeightedBudgetAuditEntry.from_dict(data)
        
        assert restored.raw_consumption_minutes == original.raw_consumption_minutes
        assert restored.weighted_consumption_minutes == original.weighted_consumption_minutes
        assert restored.level_multiplier == original.level_multiplier
        assert restored.emergency_id == original.emergency_id


# =============================================================================
# WeightedAuditRecorder Tests
# =============================================================================

class TestWeightedAuditRecorder:
    """WeightedAuditRecorder 테스트."""
    
    def test_record_entry(self):
        """엔트리 기록."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        entry = WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
        )
        
        recorder.record(entry)
        
        entries = recorder.get_entries()
        
        assert len(entries) == 1
        assert entries[0].raw_consumption_minutes == 1.0
    
    def test_record_multiple_entries(self):
        """여러 엔트리 기록."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        for i in range(5):
            entry = WeightedBudgetAuditEntry(
                raw_consumption_minutes=float(i),
            )
            recorder.record(entry)
        
        entries = recorder.get_entries()
        
        assert len(entries) == 5
    
    def test_get_entries_with_limit(self):
        """limit 적용."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        for i in range(10):
            entry = WeightedBudgetAuditEntry()
            recorder.record(entry)
        
        entries = recorder.get_entries(limit=5)
        
        assert len(entries) == 5
    
    def test_get_entries_with_namespace_filter(self):
        """namespace 필터."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        recorder.record(WeightedBudgetAuditEntry(namespace="seoul"))
        recorder.record(WeightedBudgetAuditEntry(namespace="tokyo"))
        recorder.record(WeightedBudgetAuditEntry(namespace="seoul"))
        
        seoul_entries = recorder.get_entries(namespace="seoul")
        
        assert len(seoul_entries) == 2
        assert all(e.namespace == "seoul" for e in seoul_entries)
    
    def test_get_total_consumption(self):
        """총 소진량 통계."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        recorder.record(WeightedBudgetAuditEntry(
            raw_consumption_minutes=1.0,
            weighted_consumption_minutes=5.0,
        ))
        recorder.record(WeightedBudgetAuditEntry(
            raw_consumption_minutes=2.0,
            weighted_consumption_minutes=6.0,
        ))
        
        stats = recorder.get_total_consumption()
        
        assert stats["raw_total"] == 3.0
        assert stats["weighted_total"] == 11.0
        assert stats["entry_count"] == 2
    
    def test_get_total_consumption_average_multiplier(self):
        """평균 가중치 계산."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        # 2분 * 2.0x = 4분, 3분 * 4.0x = 12분
        # 총: raw=5분, weighted=16분, avg=3.2x
        recorder.record(WeightedBudgetAuditEntry(
            raw_consumption_minutes=2.0,
            weighted_consumption_minutes=4.0,
        ))
        recorder.record(WeightedBudgetAuditEntry(
            raw_consumption_minutes=3.0,
            weighted_consumption_minutes=12.0,
        ))
        
        stats = recorder.get_total_consumption()
        
        assert stats["average_multiplier"] == 16.0 / 5.0  # 3.2
    
    def test_get_total_consumption_empty(self):
        """빈 상태에서 통계."""
        recorder = WeightedAuditRecorder(enable_hash_chain=False)
        
        stats = recorder.get_total_consumption()
        
        assert stats["raw_total"] == 0.0
        assert stats["weighted_total"] == 0.0
        assert stats["average_multiplier"] == 1.0
        assert stats["entry_count"] == 0


# =============================================================================
# Singleton Tests
# =============================================================================

class TestWeightedAuditRecorderSingleton:
    """싱글톤 팩토리 테스트."""
    
    def setup_method(self):
        """각 테스트 전 싱글톤 리셋."""
        reset_weighted_audit_recorder()
    
    def teardown_method(self):
        """각 테스트 후 싱글톤 리셋."""
        reset_weighted_audit_recorder()
    
    def test_get_returns_singleton(self):
        """get_weighted_audit_recorder는 같은 인스턴스 반환."""
        recorder1 = get_weighted_audit_recorder()
        recorder2 = get_weighted_audit_recorder()
        
        assert recorder1 is recorder2
    
    def test_reset_clears_singleton(self):
        """reset_weighted_audit_recorder는 싱글톤 초기화."""
        recorder1 = get_weighted_audit_recorder()
        
        reset_weighted_audit_recorder()
        
        recorder2 = get_weighted_audit_recorder()
        
        assert recorder2 is not recorder1

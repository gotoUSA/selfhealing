"""
Forensic Bridge 테스트 공통 설정.

이 패키지의 모든 테스트에서 사용하는 fixtures와 mocks.
"""

import pytest
import tempfile
from typing import Any, Dict, List


# =============================================================================
# Mock Classes
# =============================================================================


class MockAuditAdapter:
    """테스트용 Audit 어댑터."""
    
    def __init__(self):
        self.events: List[Dict[str, Any]] = []
    
    def log_event(
        self,
        event_type: str,
        source: str,
        details: Dict[str, Any],
    ) -> None:
        self.events.append({
            "event_type": event_type,
            "source": source,
            "details": details,
        })
    
    def get_events_by_type(self, event_type: str) -> List[Dict[str, Any]]:
        return [e for e in self.events if e["event_type"] == event_type]
    
    def clear(self) -> None:
        self.events.clear()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_audit_adapter():
    """Fresh MockAuditAdapter for each test."""
    return MockAuditAdapter()


@pytest.fixture
def temp_wal_dir():
    """임시 WAL 디렉토리."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def sample_audit_event():
    """샘플 감사 이벤트."""
    return {
        "event_type": "test_event",
        "source": "test_source",
        "details": {"key": "value"},
    }

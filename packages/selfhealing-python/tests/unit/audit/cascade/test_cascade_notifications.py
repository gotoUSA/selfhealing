"""
Cascade Event 알림 템플릿 단위 테스트.

Phase 9: 알림 메시지 생성 테스트.

Tests:
- cascade_integrity_alert: Hash Chain 무결성 위반 알림
- cascade_depth_alert: 체인 깊이 초과 알림
- cascade_load_shedding_alert: Load Shedding 활성화/비활성화 알림
- cascade_summary: 일일 요약 알림
- cascade_fallback_recovery_alert: 로컬 폴백 복구 알림

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

# =============================================================================
# Integrity Alert Tests
# =============================================================================


class TestCascadeIntegrityAlert:
    """Hash Chain 무결성 위반 알림 테스트."""

    def test_integrity_alert_basic(self):
        """기본 무결성 위반 알림 생성."""
        from selfhealing.audit.cascade_notifications import cascade_integrity_alert

        errors = [
            {
                "cascade_id": "cascade-xyz",
                "error": "hash_mismatch",
                "expected": "abc",
                "actual": "def",
            }
        ]

        alert = cascade_integrity_alert(
            namespace="seoul",
            errors=errors,
            verified_count=100,
        )

        assert alert["severity"] == "critical"
        assert "seoul" in alert["title"]
        assert "무결성 위반" in alert["message"]
        assert alert["details"]["namespace"] == "seoul"
        assert alert["details"]["verified_count"] == 100
        assert alert["details"]["error_count"] == 1

    def test_integrity_alert_limits_errors_to_5(self):
        """오류 목록은 최대 5개로 제한."""
        from selfhealing.audit.cascade_notifications import cascade_integrity_alert

        errors = [{"cascade_id": f"cascade-{i}"} for i in range(10)]

        alert = cascade_integrity_alert(
            namespace="seoul",
            errors=errors,
            verified_count=100,
        )

        # details에는 최대 5개만 포함
        assert len(alert["details"]["errors"]) == 5

    def test_integrity_alert_has_actions(self):
        """알림에 액션 링크 포함."""
        from selfhealing.audit.cascade_notifications import cascade_integrity_alert

        alert = cascade_integrity_alert(
            namespace="seoul",
            errors=[{"cascade_id": "test"}],
            verified_count=100,
        )

        assert len(alert["actions"]) == 2
        assert "상세 조사" in alert["actions"][0]["label"]
        assert "체크포인트 복원" in alert["actions"][1]["label"]


# =============================================================================
# Depth Alert Tests
# =============================================================================


class TestCascadeDepthAlert:
    """체인 깊이 초과 알림 테스트."""

    def test_depth_alert_critical_at_max(self):
        """최대 깊이 도달 시 critical 알림."""
        from selfhealing.audit.cascade_notifications import cascade_depth_alert

        alert = cascade_depth_alert(
            namespace="seoul",
            cascade_id="cascade-abc123",
            current_depth=10,
            max_depth=10,
        )

        assert alert["severity"] == "critical"
        assert "🔴" in alert["title"]
        assert alert["details"]["current_depth"] == 10
        assert alert["details"]["max_depth"] == 10

    def test_depth_alert_warning_before_max(self):
        """최대 깊이 미만 시 warning 알림."""
        from selfhealing.audit.cascade_notifications import cascade_depth_alert

        alert = cascade_depth_alert(
            namespace="seoul",
            cascade_id="cascade-abc123",
            current_depth=8,
            max_depth=10,
        )

        assert alert["severity"] == "warning"
        assert "🟡" in alert["title"]

    def test_depth_alert_includes_cascade_link(self):
        """Cascade 상세 링크 포함."""
        from selfhealing.audit.cascade_notifications import cascade_depth_alert

        alert = cascade_depth_alert(
            namespace="seoul",
            cascade_id="cascade-abc123",
            current_depth=10,
            max_depth=10,
        )

        assert len(alert["actions"]) == 1
        assert "cascade-abc123" in alert["actions"][0]["url"]


# =============================================================================
# Load Shedding Alert Tests
# =============================================================================


class TestCascadeLoadSheddingAlert:
    """Load Shedding 알림 테스트."""

    def test_load_shedding_enabled_alert(self):
        """Load Shedding 활성화 알림."""
        from selfhealing.audit.cascade_notifications import cascade_load_shedding_alert

        alert = cascade_load_shedding_alert(
            enabled=True,
            current_load=0.85,
            threshold=0.7,
            dropped_count=100,
        )

        assert alert["severity"] == "warning"
        assert "활성화" in alert["title"]
        assert "85.0%" in alert["message"]
        assert alert["details"]["enabled"] is True

    def test_load_shedding_disabled_alert(self):
        """Load Shedding 비활성화 알림."""
        from selfhealing.audit.cascade_notifications import cascade_load_shedding_alert

        alert = cascade_load_shedding_alert(
            enabled=False,
            current_load=0.45,
            threshold=0.7,
            dropped_count=150,
        )

        assert alert["severity"] == "info"
        assert "비활성화" in alert["title"]
        assert "정상화" in alert["message"]
        assert alert["details"]["enabled"] is False


# =============================================================================
# Daily Summary Tests
# =============================================================================


class TestCascadeSummary:
    """일일 요약 알림 테스트."""

    def test_summary_basic(self):
        """기본 일일 요약 알림 생성."""
        from selfhealing.audit.cascade_notifications import cascade_summary

        alert = cascade_summary(
            namespace="seoul",
            date="2026-01-23",
            total_events=150,
            events_by_trigger={
                "EMERGENCY_LEVEL_CHANGED": 100,
                "MANUAL_ACTIVATION": 50,
            },
            effects_by_action={
                "governance_strict": {"success": 95, "failure": 5},
                "canary_rollback": {"success": 48, "failure": 2},
            },
            integrity_valid=True,
            max_chain_depth=5,
        )

        assert alert["severity"] == "info"
        assert "일일 요약" in alert["title"]
        assert "2026-01-23" in alert["title"]
        assert "✅ 정상" in alert["message"]
        assert alert["details"]["total_events"] == 150
        assert alert["details"]["integrity_valid"] is True

    def test_summary_integrity_invalid(self):
        """무결성 위반 시 요약."""
        from selfhealing.audit.cascade_notifications import cascade_summary

        alert = cascade_summary(
            namespace="seoul",
            date="2026-01-23",
            total_events=100,
            events_by_trigger={},
            effects_by_action={},
            integrity_valid=False,
            max_chain_depth=3,
        )

        assert "❌ 위반" in alert["message"]
        assert alert["details"]["integrity_valid"] is False


# =============================================================================
# Fallback Recovery Alert Tests
# =============================================================================


class TestCascadeFallbackRecoveryAlert:
    """로컬 폴백 복구 알림 테스트."""

    def test_recovery_success(self):
        """복구 성공 알림."""
        from selfhealing.audit.cascade_notifications import (
            cascade_fallback_recovery_alert,
        )

        alert = cascade_fallback_recovery_alert(
            recovered_count=50,
            failed_count=0,
            fallback_path="/tmp/cascade_fallback.jsonl",
        )

        assert alert["severity"] == "info"
        assert "성공" in alert["title"]
        assert alert["details"]["recovered_count"] == 50
        assert alert["details"]["failed_count"] == 0

    def test_recovery_partial_success(self):
        """부분 성공 알림."""
        from selfhealing.audit.cascade_notifications import (
            cascade_fallback_recovery_alert,
        )

        alert = cascade_fallback_recovery_alert(
            recovered_count=45,
            failed_count=5,
            fallback_path="/tmp/cascade_fallback.jsonl",
        )

        assert alert["severity"] == "warning"
        assert "부분 성공" in alert["title"]
        assert alert["details"]["failed_count"] == 5

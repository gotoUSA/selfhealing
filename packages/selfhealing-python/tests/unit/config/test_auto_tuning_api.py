"""
Auto Tuning Service Tests

AutoTuningService의 모든 기능을 테스트합니다.

Reference: docs/self_healing/38_AUTO_TUNING_API.md
"""

import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import Mock, MagicMock, patch

from selfhealing.services.auto_tuning import (
    AutoTuningService,
    TuningMode,
    ModuleState,
    AdjustmentRecord,
)


class MockMetricsAdapter:
    """테스트용 메트릭 어댑터"""
    
    def __init__(self):
        self.metrics = {
            "p99_latency_ms": 2000,
            "error_rate": 0.02,
            "retry_exhausted_rate": 0.05,
            "throughput_rps": 100,
        }
    
    def fetch_current_metrics(self):
        return self.metrics


class MockConfigProvider:
    """테스트용 설정 제공자"""
    
    def __init__(self):
        self.configs = {}
    
    def get(self, key, default=None):
        return self.configs.get(key, default)


class MockConfigApplier:
    """테스트용 설정 적용기"""
    
    def __init__(self):
        self._values = {
            "timeout_ms": 5000,
            "retry_count": 3,
            "circuit_breaker_threshold": 0.5,
            "jitter_range": 0.1,
            "rate_limit_rps": 1000,
        }
    
    def get_current(self, parameter):
        return self._values.get(parameter, 0)
    
    def apply(self, parameter, value):
        self._values[parameter] = value
        return True
    
    def rollback(self, parameter, value):
        self._values[parameter] = value
        return True


class MockAuditAdapter:
    """테스트용 감사 어댑터"""
    
    def __init__(self):
        self.logs = []
    
    def log(self, entry):
        self.logs.append(entry)


@pytest.fixture
def service():
    """AutoTuningService 인스턴스"""
    return AutoTuningService(
        metrics_adapter=MockMetricsAdapter(),
        config_provider=MockConfigProvider(),
        config_applier=MockConfigApplier(),
        audit_adapter=MockAuditAdapter(),
        enabled=True,
    )


class TestAutoTuningServiceStatus:
    """상태 조회 테스트"""
    
    def test_get_status_returns_enabled(self, service):
        """활성화 상태 확인"""
        status = service.get_status()
        
        assert status["enabled"] is True
        assert status["mode"] == "automatic"
        assert "modules" in status
    
    def test_get_status_contains_all_modules(self, service):
        """모든 모듈 상태 포함 확인"""
        status = service.get_status()
        
        modules = status["modules"]
        assert "circuit_breaker" in modules
        assert "retry" in modules
        assert "jitter" in modules
        assert "rate_limit" in modules
        assert "timeout" in modules
    
    def test_get_status_statistics(self, service):
        """통계 정보 확인"""
        status = service.get_status()
        
        assert "statistics" in status
        assert "total_adjustments_24h" in status["statistics"]
        assert "adjustments_by_type" in status["statistics"]


class TestAutoTuningEnableDisable:
    """활성화/비활성화 테스트"""
    
    def test_enable_returns_status(self, service):
        """활성화 결과 확인"""
        # 먼저 비활성화
        service.disable(reason="test")
        
        result = service.enable(
            reason="test enable",
            mode="automatic",
            enabled_by="test_user",
        )
        
        assert result["status"] == "enabled"
        assert result["mode"] == "automatic"
        assert result["enabled_by"] == "test_user"
        assert "audit_id" in result
    
    def test_disable_returns_status(self, service):
        """비활성화 결과 확인"""
        result = service.disable(
            reason="test disable",
            disabled_by="test_user",
        )
        
        assert result["status"] == "disabled"
        assert result["disabled_by"] == "test_user"
        assert result["reason"] == "test disable"
        assert "audit_id" in result
    
    def test_disable_with_duration(self, service):
        """자동 재활성화 시간 설정"""
        result = service.disable(
            reason="test",
            duration_minutes=60,
            disabled_by="test_user",
        )
        
        assert result["auto_enable_at"] is not None
        
        # 타이머 정리
        if service._auto_enable_timer:
            service._auto_enable_timer.cancel()
    
    def test_enable_changes_mode(self, service):
        """모드 변경 확인"""
        service.disable(reason="test")
        
        result = service.enable(mode="dry_run")
        
        assert result["mode"] == "dry_run"
        assert service._mode == TuningMode.DRY_RUN


class TestAutoTuningModuleControl:
    """모듈별 제어 테스트"""
    
    def test_enable_module(self, service):
        """모듈 활성화"""
        # 먼저 비활성화
        service.disable_module("retry", reason="test")
        
        result = service.enable_module(
            module="retry",
            reason="test enable",
            enabled_by="test_user",
        )
        
        assert result["status"] == "enabled"
        assert result["module"] == "retry"
        assert service._module_states["retry"] == ModuleState.ENABLED
    
    def test_disable_module(self, service):
        """모듈 비활성화"""
        result = service.disable_module(
            module="circuit_breaker",
            reason="test disable",
            disabled_by="test_user",
        )
        
        assert result["status"] == "disabled"
        assert result["module"] == "circuit_breaker"
        assert service._module_states["circuit_breaker"] == ModuleState.DISABLED
    
    def test_disable_module_with_duration(self, service):
        """모듈 자동 재활성화 시간 설정"""
        result = service.disable_module(
            module="jitter",
            reason="test",
            duration_minutes=30,
            disabled_by="test_user",
        )
        
        assert result["auto_enable_at"] is not None
        
        # 타이머 정리
        if "jitter" in service._module_auto_enable_timers:
            service._module_auto_enable_timers["jitter"].cancel()
    
    def test_unknown_module_returns_error(self, service):
        """알 수 없는 모듈 에러"""
        result = service.enable_module("unknown_module", reason="test")
        
        assert "error" in result
        assert "valid_modules" in result


class TestAutoTuningBounds:
    """안전 한계 관리 테스트"""
    
    def test_get_bounds(self, service):
        """한계 조회"""
        result = service.get_bounds()
        
        assert "bounds" in result
        assert "timeout_ms" in result["bounds"]
        assert "min" in result["bounds"]["timeout_ms"]
        assert "max" in result["bounds"]["timeout_ms"]
    
    def test_update_bounds(self, service):
        """한계 수정"""
        result = service.update_bounds(
            parameter="timeout_ms",
            bounds={"min": 200, "max": 20000},
            reason="test update",
            updated_by="test_user",
        )
        
        assert result["status"] == "updated"
        assert result["parameter"] == "timeout_ms"
        assert result["current"]["min"] == 200
        assert result["current"]["max"] == 20000
    
    def test_update_bounds_preserves_max_change(self, service):
        """max_change_per_cycle 보존"""
        original = service.safety_bounds.get_bounds("timeout_ms")
        original_max_change = original["max_change_per_cycle"]
        
        result = service.update_bounds(
            parameter="timeout_ms",
            bounds={"min": 200, "max": 20000},
            reason="test",
        )
        
        assert result["current"]["max_change_per_cycle"] == original_max_change


class TestAutoTuningHistory:
    """조정 이력 조회 테스트"""
    
    def test_get_history_empty(self, service):
        """빈 이력 조회"""
        result = service.get_history()
        
        assert "total" in result
        assert "items" in result
        assert isinstance(result["items"], list)
    
    def test_get_history_with_pagination(self, service):
        """페이지네이션"""
        result = service.get_history(page=1, page_size=10)
        
        assert result["page"] == 1
        assert result["page_size"] == 10
    
    def test_get_history_after_adjustment(self, service):
        """조정 후 이력 확인"""
        # 조정 기록
        service.adjustment_recorder.record(
            parameter="timeout_ms",
            old_value=5000,
            new_value=6000,
            reason="test",
            triggered_by="system",
        )
        
        result = service.get_history()
        
        assert result["total"] >= 1
        assert len(result["items"]) >= 1


class TestAutoTuningOverride:
    """수동 조정 (Override) 테스트"""
    
    def test_override_applies_value(self, service):
        """값 적용 확인"""
        result = service.override(
            parameter="timeout_ms",
            value=8000,
            reason="test override",
            overridden_by="test_user",
        )
        
        assert result["status"] == "applied"
        assert result["new_value"] == 8000
        assert result["parameter"] == "timeout_ms"
    
    def test_override_stores_previous_value(self, service):
        """이전 값 저장 확인"""
        result = service.override(
            parameter="timeout_ms",
            value=8000,
            reason="test",
        )
        
        assert result["previous_value"] is not None
    
    def test_override_with_duration(self, service):
        """자동 롤백 시간 설정"""
        result = service.override(
            parameter="retry_count",
            value=5,
            duration_minutes=30,
            reason="test",
        )
        
        assert result["auto_rollback_at"] is not None
        
        # 타이머 정리
        if "retry_count" in service._override_timers:
            service._override_timers["retry_count"].cancel()
    
    def test_clear_override(self, service):
        """오버라이드 해제"""
        # 먼저 오버라이드 설정
        service.override(
            parameter="timeout_ms",
            value=8000,
            reason="test",
        )
        
        result = service.clear_override(
            parameter="timeout_ms",
            cleared_by="test_user",
        )
        
        assert result["status"] == "cleared"
        assert result["parameter"] == "timeout_ms"
    
    def test_clear_override_not_found(self, service):
        """없는 오버라이드 해제"""
        result = service.clear_override(parameter="nonexistent")
        
        assert "error" in result
    
    def test_override_outside_bounds_fails(self, service):
        """안전 한계 초과 시 실패"""
        result = service.override(
            parameter="timeout_ms",
            value=999999,  # 한계 초과
            reason="test",
        )
        
        assert "error" in result
        assert "bounds" in result


class TestAutoTuningMetrics:
    """메트릭 조회 테스트"""
    
    def test_get_current_metrics(self, service):
        """현재 메트릭 조회"""
        result = service.get_current_metrics()
        
        assert "collected_at" in result
        assert "metrics" in result
        assert "thresholds" in result
    
    def test_metrics_contains_expected_values(self, service):
        """예상 메트릭 값 포함 확인"""
        result = service.get_current_metrics()
        
        metrics = result["metrics"]
        assert "p99_latency_ms" in metrics
        assert "error_rate" in metrics


class TestAutoTuningServiceIntegration:
    """통합 테스트"""
    
    def test_start_and_stop(self, service):
        """시작/중지 테스트"""
        assert service.start() is True
        assert service.stop() is True
    
    def test_pause_and_resume(self, service):
        """일시 정지/재개 테스트"""
        service.start()
        
        assert service.pause("test") is True
        assert service.resume() is True
        
        service.stop()
    
    def test_get_adjustment_history(self, service):
        """조정 이력 조회 (레거시 호환)"""
        result = service.get_adjustment_history(limit=10)
        
        assert isinstance(result, list)
    
    def test_update_safety_bounds_legacy(self, service):
        """안전 한계 업데이트 (레거시 호환)"""
        result = service.update_safety_bounds(
            parameter="timeout_ms",
            config={"min_value": 100, "max_value": 20000, "max_change_per_cycle": 0.3}
        )
        
        assert result is True


class TestAutoTuningModes:
    """모드 테스트"""
    
    def test_automatic_mode(self, service):
        """자동 모드"""
        service.enable(mode="automatic")
        
        assert service._mode == TuningMode.AUTOMATIC
    
    def test_manual_mode(self, service):
        """수동 모드"""
        service.disable(reason="test")
        
        assert service._mode == TuningMode.MANUAL
    
    def test_dry_run_mode(self, service):
        """Dry Run 모드"""
        service.enable(mode="dry_run")
        
        assert service._mode == TuningMode.DRY_RUN


class TestAutoTuningAudit:
    """감사 로그 테스트"""
    
    def test_enable_creates_audit(self, service):
        """활성화 시 감사 로그 생성"""
        result = service.enable(reason="test")
        
        assert "audit_id" in result
        assert result["audit_id"].startswith("audit-")
    
    def test_disable_creates_audit(self, service):
        """비활성화 시 감사 로그 생성"""
        result = service.disable(reason="test")
        
        assert "audit_id" in result
    
    def test_override_creates_audit(self, service):
        """오버라이드 시 감사 로그 생성"""
        result = service.override(
            parameter="timeout_ms",
            value=6000,
            reason="test",
        )
        
        assert "audit_id" in result
    
    def test_bounds_update_creates_audit(self, service):
        """한계 수정 시 감사 로그 생성"""
        result = service.update_bounds(
            parameter="timeout_ms",
            bounds={"min": 200},
            reason="test",
        )
        
        assert "audit_id" in result

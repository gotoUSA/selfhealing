"""
Phase B Tests - Event Bus & ErrorBudgetGate Integration.

Tests for:
1. SelfHealingEventBus - 이벤트 발행/구독 시스템
2. EmergencyManager 이벤트 발행
3. ErrorBudgetGate 이벤트 발행
4. RetryHandler ErrorBudgetGate 체크
5. Conditional Replay ErrorBudgetGate 체크

Reference: docs/self_healing/17_SYSTEM_ARCHITECTURE_DIAGRAM.md (Phase B)
"""

import pytest
import threading
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import List, Dict, Any


# =============================================================================
# Event Bus Tests
# =============================================================================


class TestSelfHealingEventBus:
    """SelfHealingEventBus 테스트."""
    
    def setup_method(self):
        """테스트 전 이벤트 버스 리셋."""
        from selfhealing.services.event_bus import get_event_bus
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        """테스트 후 이벤트 버스 리셋."""
        self.bus.reset()
    
    def test_event_bus_singleton(self):
        """이벤트 버스가 싱글톤으로 동작하는지 확인."""
        from selfhealing.services.event_bus import get_event_bus, SelfHealingEventBus
        
        bus1 = get_event_bus()
        bus2 = get_event_bus()
        bus3 = SelfHealingEventBus()
        
        assert bus1 is bus2
        assert bus1 is bus3
    
    def test_subscribe_and_publish(self):
        """이벤트 구독 및 발행 테스트."""
        from selfhealing.services.event_bus import (
            EventType,
            SelfHealingEvent,
            EventPriority,
        )
        
        received_events: List[SelfHealingEvent] = []
        
        def handler(event: SelfHealingEvent):
            received_events.append(event)
        
        # 구독
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        # 발행
        event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            data={"level": 3, "previous_level": 0},
            source="test",
        )
        handlers_called = self.bus.publish(event)
        
        assert handlers_called == 1
        assert len(received_events) == 1
        assert received_events[0].data["level"] == 3
    
    def test_emit_convenience_method(self):
        """emit 간편 메서드 테스트."""
        from selfhealing.services.event_bus import EventType
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler)
        
        # emit 메서드 사용
        handlers_called = self.bus.emit(
            event_type=EventType.ERROR_BUDGET_CRITICAL,
            data={"budget_percent": 15.0, "threshold": 20.0},
            source="test",
        )
        
        assert handlers_called == 1
        assert received_events[0].data["budget_percent"] == 15.0
    
    def test_unsubscribe(self):
        """구독 해제 테스트."""
        from selfhealing.services.event_bus import EventType
        
        call_count = 0
        
        def handler(event):
            nonlocal call_count
            call_count += 1
        
        # 구독 → 발행 → 해제 → 발행
        self.bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, handler)
        self.bus.emit(EventType.CIRCUIT_BREAKER_CLOSED, {}, "test")
        assert call_count == 1
        
        self.bus.unsubscribe(EventType.CIRCUIT_BREAKER_CLOSED, handler)
        self.bus.emit(EventType.CIRCUIT_BREAKER_CLOSED, {}, "test")
        assert call_count == 1  # 더 이상 호출 안 됨
    
    def test_multiple_handlers_priority(self):
        """다중 핸들러 우선순위 테스트."""
        from selfhealing.services.event_bus import EventType, EventPriority
        
        call_order = []
        
        def low_handler(event):
            call_order.append("low")
        
        def high_handler(event):
            call_order.append("high")
        
        def critical_handler(event):
            call_order.append("critical")
        
        # 다른 순서로 등록하지만 우선순위 순으로 실행
        self.bus.subscribe(EventType.EMERGENCY_ACTIVATED, low_handler, EventPriority.LOW)
        self.bus.subscribe(EventType.EMERGENCY_ACTIVATED, critical_handler, EventPriority.CRITICAL)
        self.bus.subscribe(EventType.EMERGENCY_ACTIVATED, high_handler, EventPriority.HIGH)
        
        self.bus.emit(EventType.EMERGENCY_ACTIVATED, {}, "test")
        
        # CRITICAL → HIGH → LOW 순으로 실행
        assert call_order == ["critical", "high", "low"]
    
    def test_handler_exception_isolation(self):
        """핸들러 예외가 다른 핸들러에 영향 안 주는지 테스트."""
        from selfhealing.services.event_bus import EventType
        
        call_count = 0
        
        def failing_handler(event):
            raise Exception("Handler failed!")
        
        def success_handler(event):
            nonlocal call_count
            call_count += 1
        
        self.bus.subscribe(EventType.CONFIG_UPDATED, failing_handler)
        self.bus.subscribe(EventType.CONFIG_UPDATED, success_handler)
        
        # 예외 발생해도 다른 핸들러는 실행됨
        handlers_called = self.bus.emit(EventType.CONFIG_UPDATED, {}, "test")
        
        assert handlers_called == 1  # failing_handler는 실패
        assert call_count == 1  # success_handler는 성공
    
    def test_event_history(self):
        """이벤트 히스토리 기록 테스트."""
        from selfhealing.services.event_bus import EventType
        
        self.bus.emit(EventType.EMERGENCY_LEVEL_CHANGED, {"level": 1}, "test1")
        self.bus.emit(EventType.ERROR_BUDGET_CRITICAL, {"budget": 10}, "test2")
        self.bus.emit(EventType.CIRCUIT_BREAKER_OPENED, {"service": "api"}, "test3")
        
        history = self.bus.get_history()
        assert len(history) == 3
        
        # 특정 이벤트 타입만 필터링
        filtered = self.bus.get_history(EventType.EMERGENCY_LEVEL_CHANGED)
        assert len(filtered) == 1
        assert filtered[0]["data"]["level"] == 1
    
    def test_disable_and_enable(self):
        """이벤트 버스 비활성화/활성화 테스트."""
        from selfhealing.services.event_bus import EventType
        
        call_count = 0
        
        def handler(event):
            nonlocal call_count
            call_count += 1
        
        self.bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, handler)
        
        # 비활성화 시 이벤트 무시
        self.bus.disable()
        assert not self.bus.is_enabled()
        self.bus.emit(EventType.KILL_SWITCH_ACTIVATED, {}, "test")
        assert call_count == 0
        
        # 활성화 후 정상 동작
        self.bus.enable()
        assert self.bus.is_enabled()
        self.bus.emit(EventType.KILL_SWITCH_ACTIVATED, {}, "test")
        assert call_count == 1
    
    def test_stats(self):
        """이벤트 버스 통계 테스트."""
        from selfhealing.services.event_bus import EventType
        
        def handler(event):
            pass
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        self.bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler)
        self.bus.emit(EventType.EMERGENCY_LEVEL_CHANGED, {}, "test")
        
        stats = self.bus.get_stats()
        
        assert stats["enabled"] is True
        assert stats["subscriptions_count"] == 2
        assert stats["event_types_with_subscribers"] == 2
        assert stats["history_count"] == 1
    
    def test_thread_safety(self):
        """스레드 안전성 테스트."""
        from selfhealing.services.event_bus import EventType
        
        call_count = 0
        lock = threading.Lock()
        
        def handler(event):
            nonlocal call_count
            with lock:
                call_count += 1
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        # 여러 스레드에서 동시 발행
        threads = []
        for _ in range(10):
            t = threading.Thread(
                target=lambda: self.bus.emit(EventType.EMERGENCY_LEVEL_CHANGED, {}, "test")
            )
            threads.append(t)
            t.start()
        
        for t in threads:
            t.join()
        
        assert call_count == 10


# =============================================================================
# EmergencyManager Event Emission Tests
# =============================================================================


class TestEmergencyManagerEventEmission:
    """EmergencyManager 이벤트 발행 테스트."""
    
    def setup_method(self):
        """테스트 전 설정."""
        from selfhealing.services.event_bus import get_event_bus
        from selfhealing.services.emergency_mode import get_emergency_manager
        
        self.bus = get_event_bus()
        self.bus.reset()
        
        self.manager = get_emergency_manager()
        self.manager.reset()
    
    def teardown_method(self):
        """테스트 후 정리."""
        self.bus.reset()
        self.manager.reset()
    
    def test_activate_manual_emits_event(self):
        """수동 활성화 시 이벤트 발행 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.emergency_mode import EmergencyLevel
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        # 비상 모드 활성화
        self.manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Test activation",
            activated_by="test_user",
        )
        
        assert len(received_events) == 1
        event = received_events[0]
        assert event.data["level"] == 2
        assert event.data["previous_level"] == 0
        assert event.data["is_escalation"] is True
        assert event.data["reason"] == "Test activation"
    
    def test_deactivate_emits_event(self):
        """비활성화 시 이벤트 발행 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.emergency_mode import EmergencyLevel
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        # 먼저 활성화
        self.manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="Setup",
            activated_by="test",
        )
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        # 비활성화
        self.manager.deactivate(deactivated_by="test", force=True)
        
        assert len(received_events) == 1
        event = received_events[0]
        assert event.data["level"] == 0  # NORMAL
        assert event.data["previous_level"] == 2
        assert event.data["is_escalation"] is False
    
    def test_activate_auto_emits_event(self):
        """자동 활성화 시 이벤트 발행 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.emergency_mode import EmergencyLevel
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        # 자동 활성화
        self.manager.activate_auto(
            level=EmergencyLevel.LEVEL_1,
            reason="High error rate detected",
            duration_minutes=30,
        )
        
        assert len(received_events) == 1
        assert received_events[0].data["level"] == 1


# =============================================================================
# ErrorBudgetGate Event Emission Tests
# =============================================================================


class TestErrorBudgetGateEventEmission:
    """ErrorBudgetGate 이벤트 발행 테스트."""
    
    def setup_method(self):
        """테스트 전 설정."""
        from selfhealing.services.event_bus import get_event_bus
        
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        """테스트 후 정리."""
        self.bus.reset()
    
    def test_critical_budget_emits_event(self):
        """에러 예산 임계치 도달 시 이벤트 발행 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler)
        
        # Gate 설정 - 낮은 임계치
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=20.0,
            warning_threshold_percent=40.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # 직접 _evaluate 호출 (budget < critical)
        result = gate._evaluate(budget_percent=15.0)
        
        assert result.allowed is False
        assert len(received_events) == 1
        assert received_events[0].data["budget_percent"] == 15.0
        assert received_events[0].data["status"] == "critical"
    
    def test_warning_budget_emits_event(self):
        """에러 예산 경고 시 이벤트 발행 확인."""
        from selfhealing.services.event_bus import EventType
        from selfhealing.services.error_budget_gate import (
            ErrorBudgetGate,
            ErrorBudgetGateConfig,
        )
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.ERROR_BUDGET_WARNING, handler)
        
        config = ErrorBudgetGateConfig(
            enabled=True,
            critical_threshold_percent=20.0,
            warning_threshold_percent=40.0,
        )
        gate = ErrorBudgetGate(config=config)
        
        # _evaluate 호출 (critical < budget < warning)
        result = gate._evaluate(budget_percent=30.0)
        
        assert result.allowed is True
        assert len(received_events) == 1
        assert received_events[0].data["status"] == "warning"


# =============================================================================
# RetryHandler ErrorBudgetGate Integration Tests
# =============================================================================


class TestRetryHandlerErrorBudgetGate:
    """RetryHandler ErrorBudgetGate 체크 테스트."""
    
    def test_retry_blocked_when_error_budget_low(self):
        """에러 예산 부족 시 재시도 차단."""
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig
        
        # Mock ErrorBudgetGate
        mock_gate_result = MagicMock()
        mock_gate_result.allowed = False
        mock_gate_result.error_budget_percent = 10.0
        mock_gate_result.threshold_percent = 20.0
        
        with patch(
            "selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate",
            return_value=mock_gate_result,
        ):
            handler = RetryHandler(domain="test")
            
            def failing_func():
                raise Exception("Should not be called")
            
            result = handler.execute(failing_func)
            
            assert result.success is False
            assert result.attempt == 0  # 시도조차 안 함
            assert "Error budget critically low" in str(result.error)
    
    def test_retry_allowed_when_error_budget_healthy(self):
        """에러 예산 충분 시 재시도 허용."""
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig, RetryAction
        
        # Mock ErrorBudgetGate
        mock_gate_result = MagicMock()
        mock_gate_result.allowed = True
        
        with patch(
            "selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate",
            return_value=mock_gate_result,
        ):
            handler = RetryHandler(
                config=RetryConfig(max_attempts=1, enable_dlq=False),
                domain="test",
            )
            
            def success_func():
                return "success"
            
            result = handler.execute(success_func)
            
            assert result.success is True
            assert result.action == RetryAction.SUCCESS
    
    def test_retry_continues_when_gate_unavailable(self):
        """Gate 사용 불가 시에도 재시도 진행."""
        from selfhealing.services.retry_handler import RetryHandler, RetryConfig, RetryAction
        
        # Gate가 None 반환 (unavailable)
        with patch(
            "selfhealing.services.retry_handler.RetryHandler._check_error_budget_gate",
            return_value=None,
        ):
            handler = RetryHandler(
                config=RetryConfig(max_attempts=1, enable_dlq=False),
                domain="test",
            )
            
            def success_func():
                return "success"
            
            result = handler.execute(success_func)
            
            assert result.success is True


# =============================================================================
# Conditional Replay ErrorBudgetGate Integration Tests  
# =============================================================================


class TestConditionalReplayErrorBudgetGate:
    """Conditional Replay ErrorBudgetGate 체크 테스트."""
    
    def test_conditional_replay_blocked_when_budget_low(self):
        """에러 예산 부족 시 조건부 Replay 차단."""
        from selfhealing.adapters.celery.tasks import conditional_replay_on_circuit_close
        
        # Mock ErrorBudgetGate
        mock_gate_result = MagicMock()
        mock_gate_result.allowed = False
        mock_gate_result.error_budget_percent = 10.0
        mock_gate_result.threshold_percent = 20.0
        
        with patch(
            "selfhealing.services.error_budget_gate.check_automation_allowed",
            return_value=mock_gate_result,
        ):
            # Celery task를 직접 함수로 호출 (.run() 사용)
            result = conditional_replay_on_circuit_close.run(
                service_name="test_service",
                max_items=10,
            )
            
            assert result["success"] is False
            assert result["error"] == "automation_blocked"
            assert result["error_budget_percent"] == 10.0
            assert result["total"] == 0


# =============================================================================
# Convenience Functions Tests
# =============================================================================


class TestEventBusConvenienceFunctions:
    """이벤트 버스 간편 함수 테스트."""
    
    def setup_method(self):
        from selfhealing.services.event_bus import get_event_bus
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        self.bus.reset()
    
    def test_emit_emergency_level_changed(self):
        """emit_emergency_level_changed 간편 함수 테스트."""
        from selfhealing.services.event_bus import (
            emit_emergency_level_changed,
            EventType,
        )
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.EMERGENCY_LEVEL_CHANGED, handler)
        
        emit_emergency_level_changed(
            level=2,
            previous_level=0,
            reason="Test",
        )
        
        assert len(received_events) == 1
        assert received_events[0].data["level"] == 2
        assert received_events[0].data["is_escalation"] is True
    
    def test_emit_error_budget_critical(self):
        """emit_error_budget_critical 간편 함수 테스트."""
        from selfhealing.services.event_bus import (
            emit_error_budget_critical,
            EventType,
        )
        
        received_events = []
        
        def handler(event):
            received_events.append(event)
        
        self.bus.subscribe(EventType.ERROR_BUDGET_CRITICAL, handler)
        
        emit_error_budget_critical(
            budget_percent=10.0,
            threshold=20.0,
        )
        
        assert len(received_events) == 1
        assert received_events[0].data["budget_percent"] == 10.0
    
    def test_emit_circuit_breaker_state_changed(self):
        """emit_circuit_breaker_state_changed 간편 함수 테스트."""
        from selfhealing.services.event_bus import (
            emit_circuit_breaker_state_changed,
            EventType,
        )
        
        received_closed = []
        received_opened = []
        
        def closed_handler(event):
            received_closed.append(event)
        
        def opened_handler(event):
            received_opened.append(event)
        
        self.bus.subscribe(EventType.CIRCUIT_BREAKER_CLOSED, closed_handler)
        self.bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, opened_handler)
        
        # CLOSED 이벤트
        emit_circuit_breaker_state_changed(
            service_name="payment",
            new_state="CLOSED",
            previous_state="OPEN",
        )
        
        assert len(received_closed) == 1
        assert received_closed[0].data["service_name"] == "payment"
        
        # OPEN 이벤트
        emit_circuit_breaker_state_changed(
            service_name="inventory",
            new_state="OPEN",
            previous_state="CLOSED",
        )
        
        assert len(received_opened) == 1
        assert received_opened[0].data["service_name"] == "inventory"


# =============================================================================
# Default Handlers Registration Tests
# =============================================================================


class TestDefaultHandlersRegistration:
    """기본 핸들러 등록 테스트."""
    
    def setup_method(self):
        from selfhealing.services.event_bus import get_event_bus
        self.bus = get_event_bus()
        self.bus.reset()
    
    def teardown_method(self):
        self.bus.reset()
    
    def test_register_default_handlers(self):
        """기본 핸들러 등록 테스트."""
        from selfhealing.services.event_bus import (
            register_default_handlers,
            EventType,
        )
        
        register_default_handlers()
        
        subscriptions = self.bus.get_subscriptions()
        
        # 기본 핸들러가 등록되었는지 확인
        handler_names = [s["handler_name"] for s in subscriptions]
        
        assert "_on_emergency_level_changed" in handler_names
        assert "_on_error_budget_critical" in handler_names
        assert "_on_circuit_breaker_closed" in handler_names
    
    def test_default_handlers_not_duplicated(self):
        """기본 핸들러 중복 등록 방지 테스트."""
        from selfhealing.services.event_bus import register_default_handlers
        
        register_default_handlers()
        count1 = len(self.bus.get_subscriptions())
        
        register_default_handlers()
        count2 = len(self.bus.get_subscriptions())
        
        assert count1 == count2  # 중복 등록 안 됨


# =============================================================================
# TTL Cache (Check on Use) Pattern Tests
# =============================================================================


class TestEmergencyManagerTTLCache:
    """EmergencyManager TTL 캐시 테스트 (Check on Use 패턴)."""
    
    def setup_method(self):
        """테스트 전 초기화."""
        from selfhealing.services.emergency_mode import (
            GracefulDegradationManager,
            EmergencyLevel,
        )
        
        # 싱글톤 리셋
        GracefulDegradationManager._instance = None
        self.manager = GracefulDegradationManager()
    
    def teardown_method(self):
        """테스트 후 정리."""
        from selfhealing.services.emergency_mode import GracefulDegradationManager
        GracefulDegradationManager._instance = None
    
    def test_cache_ttl_default_value(self):
        """캐시 TTL 기본값 확인 (30초)."""
        assert self.manager._cache_ttl_seconds == 30
    
    def test_cache_valid_within_ttl(self):
        """TTL 내에서는 캐시가 유효한지 확인."""
        from datetime import datetime, timezone
        
        # 캐시 로드 시간 설정
        self.manager._last_load_time = datetime.now(timezone.utc)
        
        assert self.manager._is_cache_valid() is True
    
    def test_cache_invalid_after_ttl(self):
        """TTL 만료 후 캐시가 무효한지 확인."""
        from datetime import datetime, timezone, timedelta
        
        # 31초 전 로드 시간 설정
        self.manager._last_load_time = datetime.now(timezone.utc) - timedelta(seconds=31)
        
        assert self.manager._is_cache_valid() is False
    
    def test_cache_invalid_when_never_loaded(self):
        """로드된 적 없으면 캐시가 무효한지 확인."""
        self.manager._last_load_time = None
        
        assert self.manager._is_cache_valid() is False
    
    def test_invalidate_cache(self):
        """캐시 무효화 테스트."""
        from datetime import datetime, timezone
        
        # 캐시 설정
        self.manager._last_load_time = datetime.now(timezone.utc)
        assert self.manager._is_cache_valid() is True
        
        # 캐시 무효화
        self.manager._invalidate_cache()
        
        assert self.manager._last_load_time is None
        assert self.manager._is_cache_valid() is False
    
    def test_get_current_level_refreshes_on_stale_cache(self):
        """get_current_level() 호출 시 만료된 캐시면 StateBackend 재조회."""
        from datetime import datetime, timezone, timedelta
        from unittest.mock import patch
        
        # 캐시를 만료 상태로 설정
        self.manager._last_load_time = datetime.now(timezone.utc) - timedelta(seconds=31)
        
        with patch.object(self.manager, '_load_state') as mock_load:
            self.manager.get_current_level()
            mock_load.assert_called_once()
    
    def test_get_current_level_no_refresh_on_valid_cache(self):
        """get_current_level() 호출 시 유효한 캐시면 StateBackend 조회 안 함."""
        from datetime import datetime, timezone
        from unittest.mock import patch
        
        # 캐시를 유효 상태로 설정
        self.manager._last_load_time = datetime.now(timezone.utc)
        
        with patch.object(self.manager, '_load_state') as mock_load:
            self.manager.get_current_level()
            mock_load.assert_not_called()
    
    def test_external_event_invalidates_cache(self):
        """외부 이벤트 수신 시 캐시 무효화 확인."""
        from datetime import datetime, timezone
        from selfhealing.services.event_bus import SelfHealingEvent, EventType
        
        # 캐시 설정
        self.manager._last_load_time = datetime.now(timezone.utc)
        
        # 외부 이벤트 생성 (source가 다름)
        external_event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="external_process",
            data={"old_level": "NORMAL", "new_level": "LEVEL_1"},
        )
        
        # 핸들러 직접 호출
        self.manager._on_external_level_changed(external_event)
        
        # 캐시가 무효화됨
        assert self.manager._last_load_time is None
    
    def test_own_event_does_not_invalidate_cache(self):
        """자신이 발행한 이벤트는 캐시 무효화 안 함."""
        from datetime import datetime, timezone
        from selfhealing.services.event_bus import SelfHealingEvent, EventType
        
        # 캐시 설정
        original_time = datetime.now(timezone.utc)
        self.manager._last_load_time = original_time
        
        # 자신의 이벤트 생성 (source가 emergency_manager)
        own_event = SelfHealingEvent(
            event_type=EventType.EMERGENCY_LEVEL_CHANGED,
            source="emergency_manager",
            data={"old_level": "NORMAL", "new_level": "LEVEL_1"},
        )
        
        # 핸들러 직접 호출
        self.manager._on_external_level_changed(own_event)
        
        # 캐시가 유지됨
        assert self.manager._last_load_time == original_time

"""
Recovery Shutdown 단위 테스트.

11.4 E.6 구현 검증:
- RecoveryAwareShutdownConfig
- RecoveryAwareShutdownHook
- Recovery 완료 대기 로직

selfhealing 패키지만 import하는 순수 단위 테스트입니다.

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.3
"""

import pytest
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from selfhealing.services.coordination.recovery_shutdown import (
    RecoveryAwareShutdownConfig,
    RecoveryShutdownStats,
    RecoveryAwareShutdownHook,
    create_recovery_aware_shutdown_hook,
)


class TestRecoveryAwareShutdownConfig:
    """RecoveryAwareShutdownConfig 테스트."""
    
    def test_default_values(self):
        """기본값 확인."""
        config = RecoveryAwareShutdownConfig()
        
        assert config.default_drain_timeout_seconds == 30.0
        assert config.recovery_extension_seconds == 300.0
        assert config.max_shutdown_wait_seconds == 600.0
        assert config.recovery_check_interval_seconds == 5.0
        assert config.allow_force_shutdown is True
    
    def test_custom_values(self):
        """커스텀 값 설정."""
        config = RecoveryAwareShutdownConfig(
            default_drain_timeout_seconds=60.0,
            recovery_extension_seconds=120.0,
            max_shutdown_wait_seconds=300.0,
            recovery_check_interval_seconds=2.0,
            allow_force_shutdown=False,
        )
        
        assert config.default_drain_timeout_seconds == 60.0
        assert config.recovery_extension_seconds == 120.0
        assert config.allow_force_shutdown is False


class TestRecoveryShutdownStats:
    """RecoveryShutdownStats 테스트."""
    
    def test_default_values(self):
        """기본값 확인."""
        stats = RecoveryShutdownStats()
        
        assert stats.shutdown_requested is False
        assert stats.recovery_active_at_start is False
        assert stats.waited_for_recovery is False
        assert stats.force_shutdown is False
        assert stats.recovery_completed is False
    
    def test_with_values(self):
        """값 설정."""
        now = datetime.now(timezone.utc)
        
        stats = RecoveryShutdownStats(
            shutdown_requested=True,
            recovery_active_at_start=True,
            waited_for_recovery=True,
            wait_started_at=now,
            total_wait_seconds=10.5,
            recovery_completed=True,
        )
        
        assert stats.shutdown_requested is True
        assert stats.recovery_active_at_start is True
        assert stats.total_wait_seconds == 10.5


class TestRecoveryAwareShutdownHook:
    """RecoveryAwareShutdownHook 테스트."""
    
    def test_create_hook(self):
        """Hook 생성."""
        check_func = lambda: False
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        assert hook.is_shutdown_requested() is False
        assert hook.is_shutdown_safe() is True
    
    def test_shutdown_without_recovery(self):
        """Recovery가 없을 때 즉시 진행."""
        check_func = lambda: False
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        # Shutdown 시작
        hook.on_shutdown_start()
        
        stats = hook.get_stats()
        assert stats.shutdown_requested is True
        assert stats.recovery_active_at_start is False
        assert stats.waited_for_recovery is False
    
    def test_shutdown_with_recovery_completes_quickly(self):
        """Recovery가 빠르게 완료되는 경우."""
        call_count = [0]
        
        def check_func():
            call_count[0] += 1
            # 첫 번째 호출에서만 True, 이후 False
            return call_count[0] <= 1
        
        config = RecoveryAwareShutdownConfig(
            recovery_check_interval_seconds=0.1,
            max_shutdown_wait_seconds=10.0,
        )
        
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=check_func,
            config=config,
        )
        
        # Shutdown 시작 (별도 스레드에서 실행해야 함)
        start_time = time.time()
        hook.on_shutdown_start()
        elapsed = time.time() - start_time
        
        stats = hook.get_stats()
        assert stats.shutdown_requested is True
        assert stats.recovery_active_at_start is True
        assert stats.waited_for_recovery is True
        assert stats.recovery_completed is True
        assert elapsed < 5.0  # 빠르게 완료되어야 함
    
    def test_is_shutdown_safe(self):
        """is_shutdown_safe 확인."""
        check_results = [True, True, False]
        check_index = [0]
        
        def check_func():
            result = check_results[check_index[0]]
            check_index[0] = min(check_index[0] + 1, len(check_results) - 1)
            return result
        
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        # 처음에는 안전하지 않음 (recovery 진행 중)
        assert hook.is_shutdown_safe() is False
        
        # 다시 확인
        assert hook.is_shutdown_safe() is False
        
        # 세 번째 확인에서 안전
        assert hook.is_shutdown_safe() is True
    
    def test_get_effective_drain_timeout_without_recovery(self):
        """Recovery 없을 때 기본 타임아웃."""
        check_func = lambda: False
        config = RecoveryAwareShutdownConfig(
            default_drain_timeout_seconds=30.0,
            recovery_extension_seconds=300.0,
        )
        
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=check_func,
            config=config,
        )
        
        timeout = hook.get_effective_drain_timeout()
        assert timeout == 30.0
    
    def test_get_effective_drain_timeout_with_recovery(self):
        """Recovery 진행 중일 때 추가 타임아웃."""
        check_func = lambda: True
        config = RecoveryAwareShutdownConfig(
            default_drain_timeout_seconds=30.0,
            recovery_extension_seconds=300.0,
        )
        
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=check_func,
            config=config,
        )
        
        timeout = hook.get_effective_drain_timeout()
        assert timeout == 330.0  # 30 + 300
    
    def test_on_drain_complete(self):
        """Drain 완료 콜백."""
        check_func = lambda: False
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        hook.on_drain_complete()
        
        # shutdown_complete 이벤트가 설정됨
        assert hook.wait_for_shutdown_complete(timeout=0.1) is True
    
    def test_on_force_shutdown_without_recovery(self):
        """Recovery 없이 강제 종료."""
        check_func = lambda: False
        force_callback_called = [False]
        
        def force_callback():
            force_callback_called[0] = True
        
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=check_func,
            on_force_shutdown=force_callback,
        )
        
        hook.on_force_shutdown(pending_requests=None)
        
        assert force_callback_called[0] is True
        assert hook.wait_for_shutdown_complete(timeout=0.1) is True
    
    def test_on_force_shutdown_with_recovery(self):
        """Recovery 진행 중 강제 종료."""
        check_func = lambda: True
        
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        hook.on_force_shutdown(pending_requests=[MagicMock()])
        
        stats = hook.get_stats()
        assert stats.force_shutdown is True
    
    def test_callbacks_invoked(self):
        """콜백 호출 확인."""
        call_count = [0]
        
        def check_func():
            call_count[0] += 1
            return call_count[0] <= 1
        
        recovery_complete_called = [False]
        
        def on_recovery_complete():
            recovery_complete_called[0] = True
        
        config = RecoveryAwareShutdownConfig(
            recovery_check_interval_seconds=0.1,
            max_shutdown_wait_seconds=5.0,
        )
        
        hook = RecoveryAwareShutdownHook(
            recovery_session_checker=check_func,
            config=config,
            on_recovery_complete=on_recovery_complete,
        )
        
        hook.on_shutdown_start()
        
        assert recovery_complete_called[0] is True


class TestCreateRecoveryAwareShutdownHook:
    """create_recovery_aware_shutdown_hook 팩토리 함수 테스트."""
    
    def test_create_hook_with_defaults(self):
        """기본값으로 Hook 생성."""
        # RecoveryCoordinator import가 실패해도 동작해야 함
        hook = create_recovery_aware_shutdown_hook(namespace="global")
        
        assert hook is not None
        # import 실패 시 False 반환
        assert hook.is_shutdown_safe() is True
    
    def test_create_hook_with_custom_config(self):
        """커스텀 설정으로 Hook 생성."""
        config = RecoveryAwareShutdownConfig(
            max_shutdown_wait_seconds=120.0,
        )
        
        hook = create_recovery_aware_shutdown_hook(
            namespace="test",
            config=config,
        )
        
        assert hook is not None
        assert hook._config.max_shutdown_wait_seconds == 120.0


class TestIntegrationWithShutdownCoordinator:
    """GracefulShutdownCoordinator와의 통합 테스트."""
    
    def test_hook_interface_compatible(self):
        """ShutdownHandler 인터페이스 호환성."""
        check_func = lambda: False
        hook = RecoveryAwareShutdownHook(recovery_session_checker=check_func)
        
        # ShutdownHandler 인터페이스 메서드 존재 확인
        assert hasattr(hook, "on_shutdown_start")
        assert hasattr(hook, "on_drain_complete")
        assert hasattr(hook, "on_force_shutdown")
        
        # 메서드 호출 가능
        assert callable(hook.on_shutdown_start)
        assert callable(hook.on_drain_complete)
        assert callable(hook.on_force_shutdown)

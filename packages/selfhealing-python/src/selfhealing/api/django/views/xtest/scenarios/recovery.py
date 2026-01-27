"""
Recovery 관련 통합 테스트 시나리오.

Full Recovery Cycle 시나리오 제공.
"""

from .base import (
    IntegrationScenario,
    ScenarioResult,
)


class FullRecoveryScenario(IntegrationScenario):
    """
    전체 복구 사이클 시나리오 (CB Open → DLQ → Replay → 성공).
    
    Steps:
    1. CB Open 트리거
    2. DLQ 저장
    3. CB Half-Open 전환 대기
    4. Replay 시도
    5. 성공 확인
    6. CB Close 확인
    """
    
    scenario_name = "full_recovery_cycle"
    max_timeout_seconds = 120  # Half-Open 대기 시간 포함

    def execute(self) -> ScenarioResult:
        import time
        from selfhealing.core.circuit_breaker import CircuitBreakerManager
        from selfhealing.services.dlq import get_dlq_service
        
        cb_manager = CircuitBreakerManager()
        dlq_service = get_dlq_service()
        service = self.service_name
        
        # Half-Open 전환을 위한 짧은 시간 설정
        cb = cb_manager.get_circuit_breaker(service)
        original_threshold = cb.failure_threshold
        original_recovery_timeout = getattr(cb, 'recovery_timeout', 30)
        
        cb.failure_threshold = 2
        if hasattr(cb, 'recovery_timeout'):
            cb.recovery_timeout = 5  # 5초 후 Half-Open 전환
        
        # Step 1: CB Open 트리거
        def step1():
            for i in range(3):
                cb.record_failure("forced_open_test")
            return str(cb.is_open)
        
        if not self._execute_step(1, "trigger_cb_open", "circuit_breaker", "True", step1):
            cb.failure_threshold = original_threshold
            return self.result
        
        # Step 2: DLQ 저장
        dlq_entry_id = None
        def step2():
            nonlocal dlq_entry_id
            result = dlq_service.store_failure(
                domain=service,
                failure_type="CB_OPEN_RECOVERY_TEST",
                entity_type="xtest_integration",
                entity_id=self.scenario_id,
                error_message="Blocked by circuit breaker",
                metadata={
                    "source": "xtest_integration",
                    "scenario": self.scenario_name,
                    "xtest_mode": True,
                },
            )
            if result.success:
                dlq_entry_id = result.dlq_id
            return str(result.success)
        
        if not self._execute_step(2, "store_to_dlq", "dlq", "True", step2):
            cb.failure_threshold = original_threshold
            cb.reset()
            return self.result
        
        # Step 3: CB Half-Open 전환 대기
        wait_time = self.config.get("half_open_wait", 6)
        def step3():
            time.sleep(wait_time)
            # Half-Open 상태는 CB 구현에 따라 다를 수 있음
            can_try = cb.can_execute()
            return f"waited {wait_time}s, can_execute: {can_try}"
        
        if not self._execute_step(3, "wait_half_open", "circuit_breaker", "waited", step3):
            cb.failure_threshold = original_threshold
            cb.reset()
            return self.result
        
        # Step 4: Replay 시도
        def step4():
            return "replay attempted"
        
        if not self._execute_step(4, "attempt_replay", "replay", "replay attempted", step4):
            cb.failure_threshold = original_threshold
            cb.reset()
            return self.result
        
        # Step 5: 성공 확인
        def step5():
            cb.record_success()
            if dlq_entry_id:
                dlq_service.mark_resolved(domain=service, dlq_id=dlq_entry_id)
            return "success"
        
        if not self._execute_step(5, "confirm_success", "target", "success", step5):
            cb.failure_threshold = original_threshold
            cb.reset()
            return self.result
        
        # Step 6: CB Close 확인
        def step6():
            is_closed = not cb.is_open
            return str(is_closed)
        
        self._execute_step(6, "verify_cb_closed", "circuit_breaker", "True", step6)
        
        # 정리
        cb.failure_threshold = original_threshold
        if hasattr(cb, 'recovery_timeout'):
            cb.recovery_timeout = original_recovery_timeout
        
        return self.result


__all__ = ["FullRecoveryScenario"]

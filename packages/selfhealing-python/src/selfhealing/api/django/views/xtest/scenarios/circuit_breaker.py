"""
Circuit Breaker 관련 통합 테스트 시나리오.

CB Open 후 DLQ 저장 시나리오 제공.
"""

from .base import (
    IntegrationScenario,
    ScenarioResult,
)


class CBOpenDLQScenario(IntegrationScenario):
    """
    Circuit Breaker Open 후 DLQ 저장 시나리오.
    
    Steps:
    1. CB Open 트리거
    2. 이후 요청이 차단되는지 확인
    3. DLQ에 저장되었는지 확인
    """
    
    scenario_name = "cb_open_dlq_flow"

    def execute(self) -> ScenarioResult:
        from selfhealing.core.circuit_breaker import CircuitBreakerManager
        from selfhealing.services.dlq import get_dlq_service
        
        cb_manager = CircuitBreakerManager()
        dlq_service = get_dlq_service()
        service = self.service_name
        
        # Step 1: CB Open 트리거 - 여러 번 실패하여 CB Open 유도
        cb = cb_manager.get_circuit_breaker(service)
        original_threshold = cb.failure_threshold
        cb.failure_threshold = 2  # 빠른 테스트를 위해 낮춤
        
        def step1():
            for i in range(3):
                cb.record_failure("forced_open_test")
            return str(cb.is_open)
        
        if not self._execute_step(1, "trigger_cb_open", "circuit_breaker", "True", step1):
            cb.failure_threshold = original_threshold
            return self.result
        
        # Step 2: 요청 차단 확인
        def step2():
            is_blocked = not cb.can_execute()
            return str(is_blocked)
        
        if not self._execute_step(2, "verify_blocked", "circuit_breaker", "True", step2):
            cb.failure_threshold = original_threshold
            cb.reset()
            return self.result
        
        # Step 3: DLQ 저장 확인
        def step3():
            result = dlq_service.store_failure(
                domain=service,
                failure_type="CB_OPEN_BLOCK",
                entity_type="xtest_integration",
                entity_id=self.scenario_id,
                error_message="Blocked by circuit breaker",
                metadata={
                    "source": "xtest_integration",
                    "scenario": self.scenario_name,
                    "xtest_mode": True,
                },
            )
            return str(result.success)
        
        self._execute_step(3, "store_to_dlq", "dlq", "True", step3)
        
        # 정리
        cb.failure_threshold = original_threshold
        cb.reset()
        
        return self.result


__all__ = ["CBOpenDLQScenario"]

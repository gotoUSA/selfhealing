"""
Stage 5: L2/L3 Self-Healing 직접 테스트
=====================================

Rate Limiter를 우회하여 Circuit Breaker와 Error Budget을 직접 테스트합니다.

테스트 목표:
- L2: Circuit Breaker OPEN → HALF_OPEN → CLOSED 전체 사이클 검증
- L3: Error Budget 고갈 → 격리 모드 → 복구 검증
"""

from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone

from shopping.models.failed_external_request import CircuitBreakerState


# =============================================================================
# L2 테스트: Circuit Breaker 직접 테스트
# =============================================================================

class TestCircuitBreakerL2(TestCase):
    """L2: Circuit Breaker 상태 전환 직접 테스트"""

    def setUp(self):
        """테스트용 Circuit Breaker 상태 생성"""
        self.cb_state, _ = CircuitBreakerState.objects.get_or_create(
            service_name="test_l2_service",
            defaults={
                "state": "closed",
                "failure_count": 0,
                "success_count": 0,
            }
        )
        # 초기화
        self.cb_state.state = "closed"
        self.cb_state.failure_count = 0
        self.cb_state.success_count = 0
        self.cb_state.opened_at = None
        self.cb_state.manually_controlled = False
        self.cb_state.save()

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 5,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 60,
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,
            }
        }
    )
    def test_cb_open_via_failures(self):
        """
        테스트 1: 연속 실패로 CB OPEN 전환
        
        시나리오:
        - 5회 연속 실패 주입
        - CB 상태가 CLOSED → OPEN으로 전환 확인
        """
        self.assertEqual(self.cb_state.state, "closed", "초기 상태는 CLOSED여야 함")
        
        # 5회 실패 주입
        for i in range(5):
            self.cb_state.record_failure()
        
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "open", f"5회 실패 후 OPEN이어야 함 (현재: {self.cb_state.state})")
        self.assertGreaterEqual(self.cb_state.failure_count, 5, "실패 카운트 확인")
        self.assertIsNotNone(self.cb_state.opened_at, "OPEN 시각이 기록되어야 함")

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 5,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 1,  # 1초로 짧게
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,
            }
        }
    )
    def test_cb_half_open_transition(self):
        """
        테스트 2: OPEN → HALF_OPEN 자동 전환
        
        시나리오:
        - CB를 OPEN 상태로 설정
        - recovery_timeout 경과
        - should_allow_request() 호출 시 HALF_OPEN 전환
        """
        # OPEN 상태로 강제 전환
        self.cb_state.state = "open"
        self.cb_state.opened_at = timezone.now() - timedelta(seconds=2)  # 2초 전에 OPEN됨
        self.cb_state.save()
        
        # recovery_timeout(1초) 경과 후 요청 허용 확인
        allowed = self.cb_state.should_allow_request()
        
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "half_open", f"timeout 후 HALF_OPEN이어야 함 (현재: {self.cb_state.state})")
        self.assertTrue(allowed, "HALF_OPEN에서는 요청이 허용되어야 함")

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 5,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 60,
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,
            }
        }
    )
    def test_cb_closed_via_success(self):
        """
        테스트 3: HALF_OPEN → CLOSED 복구
        
        시나리오:
        - CB를 HALF_OPEN 상태로 설정
        - 2회 연속 성공 주입
        - CLOSED로 복구 확인
        """
        # HALF_OPEN 상태로 설정
        self.cb_state.state = "half_open"
        self.cb_state.success_count = 0
        self.cb_state.save()
        
        # 2회 성공 주입
        self.cb_state.record_success()
        self.cb_state.record_success()
        
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "closed", f"2회 성공 후 CLOSED여야 함 (현재: {self.cb_state.state})")
        self.assertEqual(self.cb_state.failure_count, 0, "실패 카운터 초기화 확인")
        self.assertIsNone(self.cb_state.opened_at, "opened_at 초기화 확인")

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 5,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 1,
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,
            }
        }
    )
    def test_cb_full_lifecycle(self):
        """
        테스트 4: 전체 생명주기 (CLOSED → OPEN → HALF_OPEN → CLOSED)
        
        시나리오:
        1. CLOSED 상태에서 5회 실패 → OPEN
        2. 대기 후 HALF_OPEN 전환
        3. 2회 성공 → CLOSED 복구
        """
        # Phase 1: CLOSED → OPEN
        self.assertEqual(self.cb_state.state, "closed")
        for _ in range(5):
            self.cb_state.record_failure()
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "open", "Phase 1: OPEN 전환 실패")
        
        # Phase 2: OPEN → HALF_OPEN
        self.cb_state.opened_at = timezone.now() - timedelta(seconds=2)
        self.cb_state.save()
        self.cb_state.should_allow_request()
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "half_open", "Phase 2: HALF_OPEN 전환 실패")
        
        # Phase 3: HALF_OPEN → CLOSED
        self.cb_state.record_success()
        self.cb_state.record_success()
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "closed", "Phase 3: CLOSED 복구 실패")

    def test_cb_force_open(self):
        """
        테스트 5: 수동 강제 OPEN
        """
        self.cb_state.force_open(controlled_by_id=1, reason="Manual test")
        self.cb_state.refresh_from_db()
        
        self.assertEqual(self.cb_state.state, "open")
        self.assertTrue(self.cb_state.manually_controlled)
        self.assertEqual(self.cb_state.control_reason, "Manual test")
        self.assertIsNotNone(self.cb_state.manual_override_expires_at)

    def test_cb_force_close(self):
        """
        테스트 6: 수동 강제 CLOSE
        """
        # 먼저 OPEN
        self.cb_state.force_open(controlled_by_id=1, reason="Test")
        
        # 강제 CLOSE
        self.cb_state.force_close(controlled_by_id=1, reason="Recovery complete")
        self.cb_state.refresh_from_db()
        
        self.assertEqual(self.cb_state.state, "closed")
        self.assertEqual(self.cb_state.failure_count, 0)
        self.assertIsNone(self.cb_state.opened_at)


# =============================================================================
# L3 테스트: Error Budget 직접 테스트
# =============================================================================

class TestErrorBudgetL3(TestCase):
    """L3: Error Budget 고갈 및 복구 테스트"""

    def setUp(self):
        """테스트용 Error Budget 서비스 생성"""
        from selfhealing.services.error_budget.service import ErrorBudgetService
        from selfhealing.slo import SLOConfig
        
        # SLOConfig.default_config() 사용
        config = SLOConfig.default_config()
        
        self.service = ErrorBudgetService(slo_config=config)
        self.service.reset_simulated_stats()

    def test_error_recording(self):
        """
        테스트 1: 에러 기록 기능
        """
        result = self.service.record_error(error_count=10, error_type="test")
        
        self.assertEqual(result["recorded_errors"], 10)
        self.assertEqual(result["total_simulated_errors"], 10)
        
        stats = self.service.get_simulated_stats()
        self.assertEqual(stats["simulated_errors"], 10)

    def test_budget_exhaustion_simulation(self):
        """
        테스트 2: Budget 고갈 시뮬레이션
        """
        # 0%까지 고갈
        result = self.service.simulate_budget_exhaustion(target_remaining_percent=0.0)
        
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(result["target_remaining_percent"], 0.0)

    def test_budget_partial_exhaustion(self):
        """
        테스트 3: 부분 고갈 (Critical 임계점)
        """
        # 15%까지 고갈 (Critical < 20%)
        result = self.service.simulate_budget_exhaustion(target_remaining_percent=15.0)
        
        self.assertEqual(result["target_remaining_percent"], 15.0)

    def test_stats_reset(self):
        """
        테스트 4: 통계 초기화
        """
        # 에러 추가
        self.service.record_error(error_count=50)
        
        # 리셋
        result = self.service.reset_simulated_stats()
        
        self.assertTrue(result["reset"])
        self.assertEqual(result["previous_stats"]["simulated_errors"], 50)
        
        stats = self.service.get_simulated_stats()
        self.assertEqual(stats["simulated_errors"], 0)

    def test_deployment_verdict_normal(self):
        """
        테스트 5: 정상 상태 배포 판정
        """
        self.service.reset_simulated_stats()
        verdict = self.service.get_deployment_verdict()
        
        self.assertIsNotNone(verdict)

    def test_deployment_verdict_after_exhaustion(self):
        """
        테스트 6: 고갈 후 배포 판정
        """
        # Budget 고갈
        self.service.simulate_budget_exhaustion(target_remaining_percent=0.0)
        
        verdict = self.service.get_deployment_verdict()
        self.assertIsNotNone(verdict)


# =============================================================================
# 통합 테스트: L2 + L3 연동
# =============================================================================

class TestSelfHealingIntegration(TestCase):
    """L2 + L3 통합 테스트"""

    def setUp(self):
        """테스트용 CB 상태"""
        self.cb_state, _ = CircuitBreakerState.objects.get_or_create(
            service_name="integration_test",
            defaults={"state": "closed", "failure_count": 0}
        )
        self.cb_state.state = "closed"
        self.cb_state.failure_count = 0
        self.cb_state.save()

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 3,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 1,
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 2,
            }
        }
    )
    def test_cascading_failure_protection(self):
        """
        통합 테스트 1: 연쇄 실패 보호
        
        시나리오:
        - 연속 실패로 CB OPEN
        - OPEN 상태에서 요청 차단 확인
        - 시스템 보호 동작 검증
        """
        # 3회 실패로 OPEN
        for _ in range(3):
            self.cb_state.record_failure()
        
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "open")
        
        # OPEN 상태에서 요청 차단
        allowed = self.cb_state.should_allow_request()
        self.assertFalse(allowed, "OPEN 상태에서는 요청이 차단되어야 함")

    @override_settings(
        SELF_HEALING={
            "CIRCUIT_BREAKER": {
                "CIRCUIT_BREAKER_ENABLED": True,
                "CIRCUIT_BREAKER_FAILURE_THRESHOLD": 3,
                "CIRCUIT_BREAKER_RECOVERY_TIMEOUT": 1,
                "CIRCUIT_BREAKER_SUCCESS_THRESHOLD": 1,
            }
        }
    )
    def test_gradual_recovery(self):
        """
        통합 테스트 2: 점진적 복구
        
        시나리오:
        - CB OPEN
        - timeout 후 HALF_OPEN
        - 성공 시 CLOSED 복구
        """
        # OPEN 상태로 설정
        self.cb_state.state = "open"
        self.cb_state.opened_at = timezone.now() - timedelta(seconds=2)
        self.cb_state.save()
        
        # HALF_OPEN 전환
        self.cb_state.should_allow_request()
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "half_open")
        
        # 성공으로 복구
        self.cb_state.record_success()
        self.cb_state.refresh_from_db()
        self.assertEqual(self.cb_state.state, "closed")

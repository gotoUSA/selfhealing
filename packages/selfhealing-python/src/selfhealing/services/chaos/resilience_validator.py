"""
Resilience Validator - Resilience 기대값 자동 검증.

Chaos 실험 후 시스템이 예상대로 반응했는지 자동으로 검증합니다.
CB 상태, Fallback 이벤트, Retry 이벤트 등을 수집하여 Resilience Score를 계산합니다.

Reference: 31_CHAOS_EXPERIMENT_EXPANSION.md §8.3 (Phase 6: 구현 계획)

Usage:
    validator = ResilienceValidator()
    result = validator.validate(
        expectation=ResilienceExpectation.expect_cb_open("payment", within_seconds=10),
        experiment_duration_seconds=30,
    )
    
    if result.passed:
        print(f"✅ Resilience Pass: {result.summary}")
    else:
        print(f"❌ Resilience Fail: Score {result.resilience_score:.0%}")
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Protocol

from selfhealing.core.timezone import now
from selfhealing.services.chaos.resilience_expectation import (
    ExpectationType,
    ResilienceAssertion,
    ResilienceExpectation,
    ResilienceValidationResult,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Protocols for Dependency Injection
# =============================================================================


class CircuitBreakerStatusProviderProtocol(Protocol):
    """CB 상태 조회 프로토콜."""
    
    def get_status(self, service_name: str) -> Dict[str, Any]:
        """
        서비스의 CB 상태 조회.
        
        Returns:
            {"state": "open|closed|half_open", "opened_at": datetime, ...}
        """
        ...


class EventCollectorProtocol(Protocol):
    """이벤트 수집 프로토콜."""
    
    def get_events(
        self,
        event_type: str,
        service_name: str,
        since: datetime,
    ) -> List[Dict[str, Any]]:
        """
        특정 이벤트 조회.
        
        Args:
            event_type: 이벤트 유형 (fallback, retry, rate_limit 등)
            service_name: 서비스명
            since: 이 시간 이후 이벤트만 조회
        
        Returns:
            이벤트 목록
        """
        ...


class EmergencyModeProviderProtocol(Protocol):
    """Emergency Mode 상태 조회 프로토콜."""
    
    def get_current_level(self) -> int:
        """현재 Emergency Level 반환 (0 = 정상)."""
        ...
    
    def is_active(self) -> bool:
        """Emergency Mode 활성화 여부."""
        ...


# =============================================================================
# Default Implementations (No-Op)
# =============================================================================


class NoOpCircuitBreakerStatusProvider:
    """기본 구현: CB 상태 항상 closed."""
    
    def get_status(self, service_name: str) -> Dict[str, Any]:
        return {"state": "closed", "service": service_name}


class NoOpEventCollector:
    """기본 구현: 빈 이벤트."""
    
    def get_events(
        self,
        event_type: str,
        service_name: str,
        since: datetime,
    ) -> List[Dict[str, Any]]:
        return []


class NoOpEmergencyModeProvider:
    """기본 구현: Emergency Mode 비활성."""
    
    def get_current_level(self) -> int:
        return 0
    
    def is_active(self) -> bool:
        return False


# =============================================================================
# Real Implementations (Integration with existing system)
# =============================================================================


class DefaultCircuitBreakerStatusProvider:
    """실제 CB 상태 조회 (CircuitBreakerService 연동)."""
    
    def get_status(self, service_name: str) -> Dict[str, Any]:
        try:
            from selfhealing.services.circuit_breaker_service import get_cb_status
            return get_cb_status(service_name)
        except ImportError:
            logger.debug("[ResilienceValidator] CB service not available")
            return {"state": "unknown", "service": service_name}
        except Exception as e:
            logger.warning(f"[ResilienceValidator] Failed to get CB status: {e}")
            return {"state": "unknown", "service": service_name, "error": str(e)}


class DefaultEventCollector:
    """실제 이벤트 수집 (EventBus 연동)."""
    
    def __init__(self) -> None:
        self._collected_events: Dict[str, List[Dict[str, Any]]] = {}
    
    def get_events(
        self,
        event_type: str,
        service_name: str,
        since: datetime,
    ) -> List[Dict[str, Any]]:
        """수집된 이벤트 중 조건에 맞는 것 반환."""
        key = f"{event_type}:{service_name}"
        events = self._collected_events.get(key, [])
        
        return [
            e for e in events
            if e.get("timestamp", now()) >= since
        ]
    
    def record_event(
        self,
        event_type: str,
        service_name: str,
        data: Dict[str, Any],
    ) -> None:
        """이벤트 기록 (테스트/연동용)."""
        key = f"{event_type}:{service_name}"
        if key not in self._collected_events:
            self._collected_events[key] = []
        
        self._collected_events[key].append({
            **data,
            "event_type": event_type,
            "service_name": service_name,
            "timestamp": data.get("timestamp", now()),
        })


class DefaultEmergencyModeProvider:
    """실제 Emergency Mode 상태 조회."""
    
    def get_current_level(self) -> int:
        try:
            from selfhealing.services.emergency_mode.manager import (
                get_emergency_manager,
            )
            manager = get_emergency_manager()
            return manager.current_level
        except ImportError:
            return 0
        except Exception as e:
            logger.warning(f"[ResilienceValidator] Failed to get emergency level: {e}")
            return 0
    
    def is_active(self) -> bool:
        return self.get_current_level() > 0


# =============================================================================
# Resilience Validator
# =============================================================================


class ResilienceValidator:
    """
    Resilience 기대값 자동 검증기.
    
    Chaos 실험 후 시스템이 예상대로 반응했는지 검증합니다.
    각 ExpectationType에 대해 적절한 검증 로직을 실행하고
    Resilience Score를 계산합니다.
    
    Usage:
        validator = ResilienceValidator()
        result = validator.validate(
            expectation=ResilienceExpectation.expect_cb_open("payment"),
            experiment_start_time=experiment.started_at,
        )
    """
    
    def __init__(
        self,
        cb_provider: Optional[CircuitBreakerStatusProviderProtocol] = None,
        event_collector: Optional[EventCollectorProtocol] = None,
        emergency_provider: Optional[EmergencyModeProviderProtocol] = None,
    ):
        """
        초기화.
        
        Args:
            cb_provider: CB 상태 조회 provider
            event_collector: 이벤트 수집기
            emergency_provider: Emergency Mode 상태 조회 provider
        """
        self._cb_provider = cb_provider or DefaultCircuitBreakerStatusProvider()
        self._event_collector = event_collector or DefaultEventCollector()
        self._emergency_provider = emergency_provider or DefaultEmergencyModeProvider()
        
        # Validator 함수 맵핑
        self._validators: Dict[ExpectationType, Callable] = {
            ExpectationType.CIRCUIT_BREAKER_OPEN: self._validate_cb_open,
            ExpectationType.CIRCUIT_BREAKER_HALF_OPEN: self._validate_cb_half_open,
            ExpectationType.FALLBACK_ACTIVATED: self._validate_fallback,
            ExpectationType.RETRY_TRIGGERED: self._validate_retry,
            ExpectationType.RATE_LIMIT_ACTIVATED: self._validate_rate_limit,
            ExpectationType.LOAD_SHEDDING_TRIGGERED: self._validate_load_shedding,
            ExpectationType.EMERGENCY_MODE_ACTIVATED: self._validate_emergency_mode,
            ExpectationType.ALERT_FIRED: self._validate_alert,
            ExpectationType.GRACEFUL_DEGRADATION: self._validate_graceful_degradation,
            ExpectationType.CUSTOM: self._validate_custom,
        }
    
    def validate(
        self,
        expectation: Optional[ResilienceExpectation],
        experiment_start_time: Optional[datetime] = None,
        experiment_duration_seconds: float = 30.0,
        poll_interval_seconds: float = 1.0,
    ) -> ResilienceValidationResult:
        """
        Resilience 기대값 검증.
        
        Args:
            expectation: 검증할 기대값 (None이면 스킵)
            experiment_start_time: 실험 시작 시간
            experiment_duration_seconds: 실험 지속 시간 (초)
            poll_interval_seconds: 상태 확인 간격 (초)
        
        Returns:
            ResilienceValidationResult
        """
        start_time = time.time()
        
        if expectation is None or len(expectation.assertions) == 0:
            return ResilienceValidationResult.skip()
        
        experiment_start = experiment_start_time or now()
        assertion_results: List[Dict[str, Any]] = []
        passed_count = 0
        
        for assertion in expectation.assertions:
            result = self._validate_assertion(
                assertion=assertion,
                experiment_start=experiment_start,
                poll_interval_seconds=poll_interval_seconds,
            )
            assertion_results.append(result)
            
            if result["passed"]:
                passed_count += 1
        
        total = len(expectation.assertions)
        failed_count = total - passed_count
        
        # require_all에 따라 전체 통과 여부 결정
        if expectation.require_all:
            overall_passed = (failed_count == 0)
        else:
            overall_passed = (passed_count > 0)
        
        validation_time = time.time() - start_time
        
        return ResilienceValidationResult(
            passed=overall_passed,
            assertion_results=assertion_results,
            total_assertions=total,
            passed_assertions=passed_count,
            failed_assertions=failed_count,
            validation_time_seconds=validation_time,
        )
    
    def _validate_assertion(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
        poll_interval_seconds: float,
    ) -> Dict[str, Any]:
        """개별 Assertion 검증."""
        result = {
            "expectation_type": assertion.expectation_type.value,
            "target_service": assertion.target_service,
            "description": assertion.description,
            "passed": False,
            "actual_state": None,
            "detected_at": None,
            "elapsed_seconds": None,
            "error": None,
        }
        
        validator = self._validators.get(assertion.expectation_type)
        if validator is None:
            result["error"] = f"No validator for {assertion.expectation_type}"
            return result
        
        try:
            passed, actual_state = validator(assertion, experiment_start)
            result["passed"] = passed
            result["actual_state"] = actual_state
            
            if passed:
                result["detected_at"] = now().isoformat()
                result["elapsed_seconds"] = (now() - experiment_start).total_seconds()
                logger.info(
                    f"[ResilienceValidator] ✅ {assertion.description}: PASSED"
                )
            else:
                logger.warning(
                    f"[ResilienceValidator] ❌ {assertion.description}: FAILED "
                    f"(actual: {actual_state})"
                )
                
        except Exception as e:
            result["error"] = str(e)
            logger.error(f"[ResilienceValidator] Validation error: {e}")
        
        return result
    
    # =========================================================================
    # Individual Validators
    # =========================================================================
    
    def _validate_cb_open(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """CB OPEN 상태 검증."""
        status = self._cb_provider.get_status(assertion.target_service)
        state = status.get("state", "unknown").lower()
        
        return state == "open", state
    
    def _validate_cb_half_open(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """CB HALF_OPEN 상태 검증."""
        status = self._cb_provider.get_status(assertion.target_service)
        state = status.get("state", "unknown").lower()
        
        return state == "half_open", state
    
    def _validate_fallback(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Fallback 활성화 검증."""
        events = self._event_collector.get_events(
            event_type="fallback",
            service_name=assertion.target_service,
            since=experiment_start,
        )
        
        return len(events) > 0, f"{len(events)} fallback events"
    
    def _validate_retry(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Retry 발동 검증."""
        events = self._event_collector.get_events(
            event_type="retry",
            service_name=assertion.target_service,
            since=experiment_start,
        )
        
        actual_count = len(events)
        expected_count = assertion.expected_count or 1
        
        return actual_count >= expected_count, f"{actual_count} retries (expected: {expected_count})"
    
    def _validate_rate_limit(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Rate Limit 활성화 검증."""
        events = self._event_collector.get_events(
            event_type="rate_limit",
            service_name=assertion.target_service,
            since=experiment_start,
        )
        
        return len(events) > 0, f"{len(events)} rate limit events"
    
    def _validate_load_shedding(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Load Shedding 발동 검증."""
        events = self._event_collector.get_events(
            event_type="load_shedding",
            service_name=assertion.target_service,
            since=experiment_start,
        )
        
        return len(events) > 0, f"{len(events)} load shedding events"
    
    def _validate_emergency_mode(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Emergency Mode 활성화 검증."""
        current_level = self._emergency_provider.get_current_level()
        is_active = self._emergency_provider.is_active()
        
        # expected_state가 "level_N" 형식이면 특정 레벨 검증
        if assertion.expected_state.startswith("level_"):
            try:
                expected_level = int(assertion.expected_state.split("_")[1])
                return current_level >= expected_level, f"level_{current_level}"
            except (IndexError, ValueError):
                pass
        
        return is_active, f"level_{current_level}"
    
    def _validate_alert(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Alert 발생 검증."""
        events = self._event_collector.get_events(
            event_type="alert",
            service_name=assertion.target_service or "*",
            since=experiment_start,
        )
        
        return len(events) > 0, f"{len(events)} alerts"
    
    def _validate_graceful_degradation(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """Graceful Degradation (CB + Fallback) 검증."""
        cb_passed, cb_state = self._validate_cb_open(assertion, experiment_start)
        fallback_passed, fallback_state = self._validate_fallback(assertion, experiment_start)
        
        passed = cb_passed and fallback_passed
        return passed, f"cb={cb_state}, fallback={fallback_state}"
    
    def _validate_custom(
        self,
        assertion: ResilienceAssertion,
        experiment_start: datetime,
    ) -> tuple[bool, str]:
        """사용자 정의 검증."""
        if assertion.custom_validator is None:
            return False, "No custom validator provided"
        
        try:
            context = {
                "experiment_start": experiment_start,
                "target_service": assertion.target_service,
                "expected_within_seconds": assertion.expected_within_seconds,
            }
            result = assertion.custom_validator(context)
            return result, "custom validation"
        except Exception as e:
            return False, f"custom validator error: {e}"


# =============================================================================
# Factory Function
# =============================================================================


def get_resilience_validator(
    use_real_providers: bool = True,
) -> ResilienceValidator:
    """
    ResilienceValidator 팩토리.
    
    Args:
        use_real_providers: True면 실제 시스템 연동, False면 No-Op
    
    Returns:
        ResilienceValidator 인스턴스
    """
    if use_real_providers:
        return ResilienceValidator(
            cb_provider=DefaultCircuitBreakerStatusProvider(),
            event_collector=DefaultEventCollector(),
            emergency_provider=DefaultEmergencyModeProvider(),
        )
    else:
        return ResilienceValidator(
            cb_provider=NoOpCircuitBreakerStatusProvider(),
            event_collector=NoOpEventCollector(),
            emergency_provider=NoOpEmergencyModeProvider(),
        )

"""
Failure Hypothesis (복구 기대 가설) 정의.

카오스 실험의 복구 기대 가설을 정의합니다.
LearningService가 실제 결과와 비교하여
"시스템 복구 성능 저하 추세"를 자동 감지하게 함.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class FailureHypothesis:
    """
    카오스 실험의 복구 기대 가설.
    
    LearningService가 실제 결과와 비교하여
    "시스템 복구 성능 저하 추세"를 자동 감지하게 함.
    
    Example:
        hypothesis = FailureHypothesis(
            description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
            expected_recovery_time_seconds=60.0,
            expected_canary_stage="canary_1",
            expected_cb_state_after="half_open",
        )
        
        passed, violations = hypothesis.validate(
            actual_recovery_time=45.0,
            actual_canary_stage="canary_1",
            actual_cb_state="half_open",
        )
    """
    
    # 복구 관련 기대
    expected_recovery_time_seconds: float = 30.0
    """기대 복구 시간 (초). 이 시간 내에 복구되어야 함."""
    
    expected_canary_stage: Optional[str] = None
    """기대 Canary 단계. 예: "canary_1" (10% 트래픽)."""
    
    expected_canary_start_within_seconds: Optional[float] = None
    """Canary 시작까지 기대 시간 (초)."""
    
    # CB 관련 기대
    expected_cb_state_after: Optional[str] = None
    """실험 후 기대 CB 상태. 예: "open", "half_open"."""
    
    expected_cb_transition_within_seconds: Optional[float] = None
    """CB 상태 전환까지 기대 시간 (초)."""
    
    # Fallback 관련 기대
    expected_fallback_activated: bool = False
    """Fallback 활성화 기대 여부."""
    
    expected_fallback_type: Optional[str] = None
    """기대 Fallback 유형. 예: "cache", "dlq", "default"."""
    
    # 메타데이터
    description: str = ""
    """사람이 읽을 수 있는 가설 설명."""
    
    tolerance_percent: float = 20.0
    """허용 오차 (%). 기대 시간의 ±20% 내면 정상."""
    
    def validate(
        self,
        actual_recovery_time: float,
        actual_canary_stage: Optional[str] = None,
        actual_cb_state: Optional[str] = None,
        actual_fallback_activated: Optional[bool] = None,
        actual_fallback_type: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        가설 검증.
        
        Args:
            actual_recovery_time: 실제 복구 시간 (초)
            actual_canary_stage: 실제 Canary 단계
            actual_cb_state: 실제 CB 상태
            actual_fallback_activated: 실제 Fallback 활성화 여부
            actual_fallback_type: 실제 Fallback 유형
        
        Returns:
            (passed, violations) 튜플
            - passed: True이면 가설 검증 통과
            - violations: 위반 사항 목록
        """
        violations: List[str] = []
        tolerance_factor = 1 + (self.tolerance_percent / 100)
        
        # 복구 시간 검증
        max_allowed_time = self.expected_recovery_time_seconds * tolerance_factor
        if actual_recovery_time > max_allowed_time:
            violations.append(
                f"Recovery time {actual_recovery_time:.1f}s > "
                f"expected {self.expected_recovery_time_seconds:.1f}s "
                f"(+{self.tolerance_percent}% tolerance = {max_allowed_time:.1f}s)"
            )
        
        # Canary 단계 검증
        if self.expected_canary_stage and actual_canary_stage != self.expected_canary_stage:
            violations.append(
                f"Canary stage '{actual_canary_stage}' != expected '{self.expected_canary_stage}'"
            )
        
        # CB 상태 검증
        if self.expected_cb_state_after and actual_cb_state != self.expected_cb_state_after:
            violations.append(
                f"CB state '{actual_cb_state}' != expected '{self.expected_cb_state_after}'"
            )
        
        # Fallback 활성화 검증
        if self.expected_fallback_activated:
            if actual_fallback_activated is False:
                violations.append(
                    f"Fallback expected to be activated but was not"
                )
            # Fallback 유형 검증 (Fallback이 활성화된 경우에만)
            if (
                self.expected_fallback_type
                and actual_fallback_activated
                and actual_fallback_type != self.expected_fallback_type
            ):
                violations.append(
                    f"Fallback type '{actual_fallback_type}' != expected '{self.expected_fallback_type}'"
                )
        
        return len(violations) == 0, violations
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "expected_recovery_time_seconds": self.expected_recovery_time_seconds,
            "expected_canary_stage": self.expected_canary_stage,
            "expected_canary_start_within_seconds": self.expected_canary_start_within_seconds,
            "expected_cb_state_after": self.expected_cb_state_after,
            "expected_cb_transition_within_seconds": self.expected_cb_transition_within_seconds,
            "expected_fallback_activated": self.expected_fallback_activated,
            "expected_fallback_type": self.expected_fallback_type,
            "description": self.description,
            "tolerance_percent": self.tolerance_percent,
        }


# =============================================================================
# 실험별 기대 가설 정의 (클래스 레벨 상수)
# =============================================================================

# CircuitBreakerOpenExperiment
CB_OPEN_HYPOTHESIS = FailureHypothesis(
    description="CB Open 실험 시, 30초 내에 Canary Stage 1이 시작되어야 함",
    expected_recovery_time_seconds=60.0,
    expected_canary_stage="canary_1",
    expected_canary_start_within_seconds=30.0,
    expected_cb_state_after="half_open",
    expected_cb_transition_within_seconds=30.0,
    expected_fallback_activated=True,
    expected_fallback_type="cache",
)

# LatencyInjectionExperiment
LATENCY_INJECTION_HYPOTHESIS = FailureHypothesis(
    description="500ms 지연 주입 시, CB가 10초 내에 OPEN되어야 함",
    expected_recovery_time_seconds=45.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=10.0,
    expected_fallback_activated=False,
)

# Error5xxExperiment
ERROR_5XX_HYPOTHESIS = FailureHypothesis(
    description="503 에러 주입 시, 5초 내에 CB OPEN 및 Fallback 활성화",
    expected_recovery_time_seconds=30.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=5.0,
    expected_fallback_activated=True,
)

# Pool Exhaustion
POOL_EXHAUSTION_HYPOTHESIS = FailureHypothesis(
    description="Pool 고갈 시뮬레이션 시, 시스템이 graceful degradation 수행해야 함",
    expected_recovery_time_seconds=120.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=30.0,
    expected_fallback_activated=True,
    expected_fallback_type="cache",
)

# Connection Partition
CONNECTION_PARTITION_HYPOTHESIS = FailureHypothesis(
    description="네트워크 파티션 시뮬레이션 시, 시스템이 partial partition 처리해야 함",
    expected_recovery_time_seconds=60.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=15.0,
    expected_fallback_activated=True,
)

# Certificate Expiry
CERTIFICATE_EXPIRY_HYPOTHESIS = FailureHypothesis(
    description="인증서 만료 시뮬레이션 시, 알림이 즉시 발생해야 함",
    expected_recovery_time_seconds=30.0,
    expected_fallback_activated=False,  # 알림만 트리거
    tolerance_percent=50.0,  # 알림 지연 허용
)

# Clock Skew
CLOCK_SKEW_HYPOTHESIS = FailureHypothesis(
    description="시간 왜곡 시뮬레이션 시, 시간 의존 로직이 graceful하게 처리해야 함",
    expected_recovery_time_seconds=60.0,
    tolerance_percent=30.0,
)

# DNS Failure
DNS_FAILURE_HYPOTHESIS = FailureHypothesis(
    description="DNS 장애 시, ConnectionHealthMonitor가 10초 내에 UNHEALTHY 보고해야 함",
    expected_recovery_time_seconds=30.0,
    expected_cb_state_after="open",
    expected_cb_transition_within_seconds=10.0,
)

# Network Blackhole
NETWORK_BLACKHOLE_HYPOTHESIS = FailureHypothesis(
    description="네트워크 블랙홀 시, 타임아웃 후 CB가 OPEN 상태로 전환해야 함",
    expected_recovery_time_seconds=60.0,
    expected_cb_state_after="open",
)

# Simulated Disk I/O
SIMULATED_DISK_IO_HYPOTHESIS = FailureHypothesis(
    description="디스크 I/O 장애 시, DLQ가 메모리 폴백을 활성화해야 함",
    expected_recovery_time_seconds=15.0,
    expected_fallback_activated=True,
    expected_fallback_type="memory",
)

# Simulated TLS Failure
SIMULATED_TLS_FAILURE_HYPOTHESIS = FailureHypothesis(
    description="TLS 핸드셰이크 실패 시, 적절한 에러 처리 및 보안 로깅이 수행되어야 함",
    expected_recovery_time_seconds=30.0,
    expected_cb_state_after="open",
)

# Audit Storage Failure
AUDIT_STORAGE_FAILURE_HYPOTHESIS = FailureHypothesis(
    description="Audit 저장소 장애 시, L1→L2→L3 폴백이 자동 활성화되어야 함",
    expected_recovery_time_seconds=10.0,
    expected_fallback_activated=True,
    expected_fallback_type="memory",
)

# Replay Flood
REPLAY_FLOOD_HYPOTHESIS = FailureHypothesis(
    description="DLQ Replay 폭풍 시, 쓰로틀링이 활성화되어야 함",
    expected_recovery_time_seconds=30.0,
    expected_fallback_activated=True,
    expected_fallback_type="throttle",
)


__all__ = [
    "FailureHypothesis",
    # Hypothesis constants
    "CB_OPEN_HYPOTHESIS",
    "LATENCY_INJECTION_HYPOTHESIS",
    "ERROR_5XX_HYPOTHESIS",
    "POOL_EXHAUSTION_HYPOTHESIS",
    "CONNECTION_PARTITION_HYPOTHESIS",
    "CERTIFICATE_EXPIRY_HYPOTHESIS",
    "CLOCK_SKEW_HYPOTHESIS",
    "DNS_FAILURE_HYPOTHESIS",
    "NETWORK_BLACKHOLE_HYPOTHESIS",
    "SIMULATED_DISK_IO_HYPOTHESIS",
    "SIMULATED_TLS_FAILURE_HYPOTHESIS",
    "AUDIT_STORAGE_FAILURE_HYPOTHESIS",
    "REPLAY_FLOOD_HYPOTHESIS",
]

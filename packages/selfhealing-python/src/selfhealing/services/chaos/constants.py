"""
Chaos Experiment Constants

안전 메커니즘 관련 상수 정의.
"""

from typing import Dict, Any


# === 가상 격리 (Virtual Isolation) ===
CHAOS_DOMAIN_PREFIX: str = "chaos_test:"
"""DLQ 도메인 접두어 - 실험 데이터 격리용"""

CHAOS_METADATA_FLAGS: Dict[str, Any] = {
    "is_synthetic": True,
    "is_chaos_experiment": True,
}
"""DLQ 메타데이터 필수 플래그 - 통계/SLA에서 자동 제외"""


class ExperimentHardCaps:
    """
    실험별 최대 허용 범위 (Blast Radius Hard Caps).
    
    이 값들은 실험 구성에서 상한선으로 적용됩니다.
    사용자가 더 큰 값을 지정해도 이 값으로 제한됩니다.
    """
    
    # === SimulatedDiskIOExperiment ===
    DISK_IO_MAX_LATENCY_MS: int = 2000
    """디스크 I/O 최대 지연 시간 (ms) - 2초 초과 불가"""
    
    DISK_IO_MAX_FAILURE_RATE: float = 0.30
    """디스크 I/O 최대 실패율 - 30% 초과 불가"""
    
    # === ReplayFloodExperiment ===
    REPLAY_FLOOD_MAX_ENTRIES: int = 5000
    """Replay Flood 최대 생성 엔트리 수"""
    
    REPLAY_FLOOD_MAX_RATE: int = 500
    """Replay Flood 최대 초당 생성 속도"""
    
    # === ClockSkewExperiment ===
    CLOCK_SKEW_MAX_SECONDS: int = 86400
    """Clock Skew 최대 오차 (초) - 1일 초과 불가"""
    
    # === NetworkBlackholeExperiment ===
    BLACKHOLE_MAX_DURATION_SECONDS: int = 300
    """Network Blackhole 최대 지속 시간 (초) - 5분 초과 불가"""
    
    # === PoolExhaustionExperiment ===
    POOL_EXHAUSTION_MAX_DURATION_SECONDS: int = 120
    """Connection Pool 고갈 최대 지속 시간 (초) - 2분 초과 불가"""
    
    POOL_EXHAUSTION_MAX_PERCENTAGE: float = 0.50
    """Connection Pool 고갈 최대 비율 - 50% 초과 불가"""
    
    # === SimulatedTLSFailureExperiment ===
    TLS_FAILURE_MAX_DURATION_SECONDS: int = 180
    """TLS 실패 최대 지속 시간 (초) - 3분 초과 불가"""
    
    TLS_FAILURE_MAX_RATE: float = 0.25
    """TLS 실패 최대 발생률 - 25% 초과 불가"""
    
    @classmethod
    def apply_cap(cls, experiment_type: str, field: str, value: float) -> float:
        """
        실험 타입과 필드에 대한 하드캡 적용.
        
        Args:
            experiment_type: 실험 타입 (예: "simulated_disk_io")
            field: 필드명 (예: "latency_ms", "failure_rate")
            value: 사용자 지정 값
        
        Returns:
            하드캡이 적용된 값 (min(value, cap))
        """
        caps = {
            "simulated_disk_io": {
                "latency_ms": cls.DISK_IO_MAX_LATENCY_MS,
                "failure_rate": cls.DISK_IO_MAX_FAILURE_RATE,
            },
            "replay_flood": {
                "entries_count": cls.REPLAY_FLOOD_MAX_ENTRIES,
                "rate_per_second": cls.REPLAY_FLOOD_MAX_RATE,
            },
            "clock_skew": {
                "skew_seconds": cls.CLOCK_SKEW_MAX_SECONDS,
            },
            "network_blackhole": {
                "duration_seconds": cls.BLACKHOLE_MAX_DURATION_SECONDS,
            },
            "pool_exhaustion": {
                "duration_seconds": cls.POOL_EXHAUSTION_MAX_DURATION_SECONDS,
                "exhaust_percentage": cls.POOL_EXHAUSTION_MAX_PERCENTAGE,
            },
            "simulated_tls_failure": {
                "duration_seconds": cls.TLS_FAILURE_MAX_DURATION_SECONDS,
                "failure_rate": cls.TLS_FAILURE_MAX_RATE,
            },
        }
        
        experiment_caps = caps.get(experiment_type, {})
        cap = experiment_caps.get(field)
        
        if cap is not None:
            return min(value, cap)
        return value

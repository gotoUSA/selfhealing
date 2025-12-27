"""
Safe Defaults - 사령탑 연결 실패 시 사용할 안전한 기본값.

패키지 내장 기본값으로 외부 파일/서비스 의존 없이 이식 가능.
Degraded Mode 인지 로깅 및 HealthBridge 연동점 제공.

Usage:
    from load_tests.utils.selfhealing.defaults import SafeDefaults
    
    # 기본값 사용
    config = SafeDefaults.get_cb_config()
    
    # 오버라이드 (선택적)
    SafeDefaults.set('CB_FAILURE_THRESHOLD', 5)
    
    # 환경변수로도 가능
    # SELFHEALING_CB_FAILURE_THRESHOLD=5
"""

import os
import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class SafeDefaults:
    """
    사령탑 연결 실패 시 사용할 안전한 기본값.
    
    특징:
    - 패키지 내장 (외부 파일 의존 없음)
    - Thread-Safe
    - Degraded Mode 인지 로깅
    - 런타임 오버라이드 지원
    """
    
    _lock = threading.RLock()
    _degraded_warned = False
    _is_degraded = False
    _degraded_since: Optional[float] = None
    
    # 기본 설정값 (보수적)
    _defaults: Dict[str, Any] = {
        # Circuit Breaker
        'CB_FAILURE_THRESHOLD': 3,          # 3회 실패 시 Open
        'CB_RECOVERY_TIMEOUT': 60,          # 60초 후 Half-Open
        'CB_HALF_OPEN_MAX_CALLS': 1,        # Half-Open에서 1회만 테스트
        
        # Rate Limit
        'RATE_LIMIT_PER_MINUTE': 100,       # 분당 100회
        'RATE_LIMIT_BURST': 10,             # 버스트 10회
        
        # Timeout
        'DEFAULT_TIMEOUT_MS': 5000,         # 5초
        'HEALTH_CHECK_TIMEOUT_MS': 1000,    # 헬스체크 1초
        
        # Retry
        'MAX_RETRY_ATTEMPTS': 3,            # 최대 3회 재시도
        'RETRY_BACKOFF_BASE_MS': 100,       # 백오프 기본값 100ms
        
        # Recovery SLA (ms)
        'RECOVERY_SLA_BRONZE_P99': 2000,
        'RECOVERY_SLA_SILVER_P99': 500,
        'RECOVERY_SLA_GOLD_P99': 250,
        'RECOVERY_SLA_PLATINUM_P99': 100,
        
        # Jitter
        'JITTER_MIN_MS': 0,
        'JITTER_MAX_MS': 500,
    }
    
    # 런타임 오버라이드 저장소
    _overrides: Dict[str, Any] = {}
    
    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Thread-Safe하게 설정값 조회.
        
        우선순위: 환경변수 > 런타임 오버라이드 > 기본값
        """
        with cls._lock:
            # 1. 환경변수 확인
            env_key = f"SELFHEALING_{key}"
            env_value = os.environ.get(env_key)
            if env_value is not None:
                return cls._parse_value(env_value)
            
            # 2. 런타임 오버라이드 확인
            if key in cls._overrides:
                return cls._overrides[key]
            
            # 3. 기본값 반환
            return cls._defaults.get(key, default)
    
    @classmethod
    def set(cls, key: str, value: Any):
        """Thread-Safe하게 설정값 오버라이드."""
        with cls._lock:
            cls._overrides[key] = value
            logger.debug(f"SafeDefaults override: {key}={value}")
    
    @classmethod
    def set_bulk(cls, overrides: Dict[str, Any]):
        """여러 설정 일괄 오버라이드."""
        with cls._lock:
            cls._overrides.update(overrides)
            logger.debug(f"SafeDefaults bulk override: {len(overrides)} settings")
    
    @classmethod
    def reset_overrides(cls):
        """오버라이드 초기화."""
        with cls._lock:
            cls._overrides.clear()
    
    @classmethod
    def get_cb_config(cls) -> Dict[str, Any]:
        """CB 설정 일괄 조회."""
        return {
            'failure_threshold': cls.get('CB_FAILURE_THRESHOLD'),
            'recovery_timeout': cls.get('CB_RECOVERY_TIMEOUT'),
            'half_open_max_calls': cls.get('CB_HALF_OPEN_MAX_CALLS'),
        }
    
    @classmethod
    def get_rate_limit_config(cls) -> Dict[str, Any]:
        """Rate Limit 설정 일괄 조회."""
        return {
            'per_minute': cls.get('RATE_LIMIT_PER_MINUTE'),
            'burst': cls.get('RATE_LIMIT_BURST'),
        }
    
    @classmethod
    def get_timeout_config(cls) -> Dict[str, Any]:
        """Timeout 설정 일괄 조회."""
        return {
            'default_ms': cls.get('DEFAULT_TIMEOUT_MS'),
            'health_check_ms': cls.get('HEALTH_CHECK_TIMEOUT_MS'),
        }
    
    @classmethod
    def get_retry_config(cls) -> Dict[str, Any]:
        """Retry 설정 일괄 조회."""
        return {
            'max_attempts': cls.get('MAX_RETRY_ATTEMPTS'),
            'backoff_base_ms': cls.get('RETRY_BACKOFF_BASE_MS'),
        }
    
    @classmethod
    def get_sla_target(cls, tier: str) -> int:
        """
        SLA 티어별 P99 목표 조회.
        
        Args:
            tier: BRONZE, SILVER, GOLD, PLATINUM
            
        Returns:
            P99 목표 (ms)
        """
        key = f'RECOVERY_SLA_{tier.upper()}_P99'
        return cls.get(key, 2000)  # 기본 BRONZE
    
    @classmethod
    def get_jitter_range(cls) -> tuple:
        """Jitter 범위 조회."""
        return (cls.get('JITTER_MIN_MS'), cls.get('JITTER_MAX_MS'))
    
    @classmethod
    def enter_degraded_mode(cls):
        """
        Degraded Mode 진입.
        
        - 사령탑 연결 실패 시 호출
        - 경고 로그 1회만 출력 (Singleton)
        """
        import time
        
        with cls._lock:
            if not cls._is_degraded:
                cls._degraded_since = time.time()
            
            cls._is_degraded = True
            
            if not cls._degraded_warned:
                logger.warning(
                    "⚠️ [SELFHEALING] System is running in DEGRADED MODE "
                    "with local defaults. Command center connection failed."
                )
                cls._degraded_warned = True
    
    @classmethod
    def exit_degraded_mode(cls):
        """Degraded Mode 해제 (사령탑 연결 복구 시)."""
        with cls._lock:
            if cls._is_degraded:
                import time
                duration = time.time() - cls._degraded_since if cls._degraded_since else 0
                logger.info(f"✅ [SELFHEALING] Exited DEGRADED MODE after {duration:.1f}s. Command center reconnected.")
            
            cls._is_degraded = False
            cls._degraded_warned = False
            cls._degraded_since = None
    
    @classmethod
    def is_degraded(cls) -> bool:
        """현재 Degraded Mode 여부."""
        with cls._lock:
            return cls._is_degraded
    
    @classmethod
    def get_degraded_duration(cls) -> float:
        """Degraded Mode 지속 시간 (초)."""
        import time
        with cls._lock:
            if cls._is_degraded and cls._degraded_since:
                return time.time() - cls._degraded_since
            return 0
    
    @classmethod
    def get_health_response(cls) -> Dict[str, Any]:
        """
        HealthBridge 연동점.
        
        Degraded Mode일 때 반환할 헬스 응답
        사용하는 쪽에서 이 메서드를 호출하여 연동.
        """
        return {
            'status': 'degraded' if cls._is_degraded else 'healthy',
            'is_degraded': cls._is_degraded,
            'degraded_duration_s': cls.get_degraded_duration(),
            'source': 'local_defaults' if cls._is_degraded else 'command_center',
            'config': {
                'cb': cls.get_cb_config(),
                'rate_limit': cls.get_rate_limit_config(),
                'timeout': cls.get_timeout_config(),
                'retry': cls.get_retry_config(),
            }
        }
    
    @classmethod
    def get_all_defaults(cls) -> Dict[str, Any]:
        """모든 기본값 조회 (디버깅용)."""
        with cls._lock:
            return cls._defaults.copy()
    
    @classmethod
    def get_all_overrides(cls) -> Dict[str, Any]:
        """모든 오버라이드 조회 (디버깅용)."""
        with cls._lock:
            return cls._overrides.copy()
    
    @classmethod
    def _parse_value(cls, value: str) -> Any:
        """환경변수 문자열을 적절한 타입으로 변환."""
        # 불리언
        if value.lower() in ('true', '1', 'yes'):
            return True
        if value.lower() in ('false', '0', 'no'):
            return False
        
        # 정수
        try:
            return int(value)
        except ValueError:
            pass
        
        # 실수
        try:
            return float(value)
        except ValueError:
            pass
        
        # 문자열 그대로
        return value

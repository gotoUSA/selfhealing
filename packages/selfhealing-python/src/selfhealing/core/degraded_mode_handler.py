# packages/selfhealing-python/src/selfhealing/core/degraded_mode_handler.py
"""
DegradedModeHandler - 사령탑 연결 실패 시 Degraded Mode 관리 (Platinum SLA 최적화)

런타임 Fallback 기본값 + Degraded Mode 상태 관리
사령탑 장애 시에도 0ms 즉시 보호 로직 가동

Note: 정적 설정 기본값은 safe_defaults.py 참조
"""

import os
import threading
from typing import Any

import structlog

__all__ = ["DegradedModeHandler"]

logger = structlog.get_logger()


class DegradedModeHandler:
    """
    사령탑 연결 실패 시 Degraded Mode 관리 및 런타임 Fallback

    특징:
    - 패키지 내장 (외부 파일 의존 없음)
    - Thread-Safe
    - Degraded Mode 인지 로깅
    - 런타임 오버라이드 지원

    Usage:
        # 기본값 사용
        config = DegradedModeHandler.get_cb_config()

        # 오버라이드 (선택적)
        DegradedModeHandler.set('CB_FAILURE_THRESHOLD', 5)

        # 환경변수로도 가능
        # SELFHEALING_CB_FAILURE_THRESHOLD=5
    """

    _lock = threading.RLock()
    _degraded_warned = False
    _is_degraded = False

    # 기본 설정값 (보수적)
    _defaults: dict[str, Any] = {
        # Circuit Breaker
        "CB_FAILURE_THRESHOLD": 3,  # 3회 실패 시 Open
        "CB_RECOVERY_TIMEOUT": 60,  # 60초 후 Half-Open
        "CB_HALF_OPEN_MAX_CALLS": 1,  # Half-Open에서 1회만 테스트
        # Rate Limit
        "RATE_LIMIT_PER_MINUTE": 100,  # 분당 100회
        "RATE_LIMIT_BURST": 10,  # 버스트 10회
        # Timeout
        "DEFAULT_TIMEOUT_MS": 5000,  # 5초
        "HEALTH_CHECK_TIMEOUT_MS": 1000,  # 헬스체크 1초
        # Retry
        "MAX_RETRY_ATTEMPTS": 3,  # 최대 3회 재시도
        "RETRY_BACKOFF_BASE_MS": 100,  # 백오프 기본값 100ms
    }

    # 런타임 오버라이드 저장소
    _overrides: dict[str, Any] = {}

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        """
        Thread-Safe하게 설정값 조회

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
    def set(cls, key: str, value: Any) -> None:
        """Thread-Safe하게 설정값 오버라이드"""
        with cls._lock:
            cls._overrides[key] = value

    @classmethod
    def get_all_keys(cls) -> list:
        """사용 가능한 모든 설정 키 목록"""
        with cls._lock:
            return list(cls._defaults.keys())

    @classmethod
    def get_cb_config(cls) -> dict[str, Any]:
        """CB 설정 일괄 조회"""
        return {
            "failure_threshold": cls.get("CB_FAILURE_THRESHOLD"),
            "recovery_timeout": cls.get("CB_RECOVERY_TIMEOUT"),
            "half_open_max_calls": cls.get("CB_HALF_OPEN_MAX_CALLS"),
        }

    @classmethod
    def get_rate_limit_config(cls) -> dict[str, Any]:
        """Rate Limit 설정 일괄 조회"""
        return {
            "per_minute": cls.get("RATE_LIMIT_PER_MINUTE"),
            "burst": cls.get("RATE_LIMIT_BURST"),
        }

    @classmethod
    def get_timeout_config(cls) -> dict[str, Any]:
        """Timeout 설정 일괄 조회"""
        return {
            "default_timeout_ms": cls.get("DEFAULT_TIMEOUT_MS"),
            "health_check_timeout_ms": cls.get("HEALTH_CHECK_TIMEOUT_MS"),
        }

    @classmethod
    def get_retry_config(cls) -> dict[str, Any]:
        """Retry 설정 일괄 조회"""
        return {
            "max_retry_attempts": cls.get("MAX_RETRY_ATTEMPTS"),
            "retry_backoff_base_ms": cls.get("RETRY_BACKOFF_BASE_MS"),
        }

    @classmethod
    def enter_degraded_mode(cls) -> None:
        """
        Degraded Mode 진입

        - 사령탑 연결 실패 시 호출
        - 경고 로그 1회만 출력 (Singleton)
        """
        with cls._lock:
            cls._is_degraded = True

            if not cls._degraded_warned:
                logger.warning(
                    "⚠️ [SELFHEALING] System is running in DEGRADED MODE "
                    "with local defaults. Command center connection failed."
                )
                cls._degraded_warned = True

    @classmethod
    def exit_degraded_mode(cls) -> None:
        """Degraded Mode 해제 (사령탑 연결 복구 시)"""
        with cls._lock:
            if cls._is_degraded:
                logger.info(
                    "✅ [SELFHEALING] Exited DEGRADED MODE. Command center reconnected."
                )
            cls._is_degraded = False
            cls._degraded_warned = False

    @classmethod
    def is_degraded(cls) -> bool:
        """현재 Degraded Mode 여부"""
        with cls._lock:
            return cls._is_degraded

    @classmethod
    def get_health_response(cls) -> dict[str, Any]:
        """
        HealthBridge 연동점

        Degraded Mode일 때 반환할 헬스 응답
        사용하는 쪽(쇼핑몰 등)에서 이 메서드를 호출하여 연동
        """
        with cls._lock:
            return {
                "status": "degraded" if cls._is_degraded else "healthy",
                "is_degraded": cls._is_degraded,
                "source": "local_defaults" if cls._is_degraded else "command_center",
                "config": {
                    "cb": cls.get_cb_config(),
                    "rate_limit": cls.get_rate_limit_config(),
                    "timeout": cls.get_timeout_config(),
                    "retry": cls.get_retry_config(),
                },
            }

    @classmethod
    def _parse_value(cls, value: str) -> Any:
        """환경변수 문자열을 적절한 타입으로 변환"""
        # 불리언
        if value.lower() in ("true", "1", "yes"):
            return True
        if value.lower() in ("false", "0", "no"):
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

    @classmethod
    def reset(cls) -> None:
        """상태 초기화 (테스트용)"""
        with cls._lock:
            cls._overrides.clear()
            cls._is_degraded = False
            cls._degraded_warned = False

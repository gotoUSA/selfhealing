"""
Hedging Exceptions - 헷징 전략 관련 예외 클래스.

모든 후보 실패, 타임아웃, 재시도 불가 에러 등 헷징 실행 중 발생하는
예외 상황을 정의합니다.
"""

from __future__ import annotations


class HedgingError(Exception):
    """헷징 기본 예외 클래스."""

    pass


class HedgingAllFailedError(HedgingError):
    """
    모든 헷징 후보가 실패한 경우 발생하는 예외.

    병렬 실행된 모든 후보 함수가 실패했을 때 발생합니다.
    """

    def __init__(self, candidates_tried: int, errors: list[str]):
        """
        Args:
            candidates_tried: 시도된 후보 수
            errors: 각 후보별 에러 메시지 목록
        """
        self.candidates_tried = candidates_tried
        self.errors = errors
        super().__init__(f"All {candidates_tried} candidates failed: {errors}")


class HedgingTimeoutError(HedgingError):
    """
    헷징 실행이 타임아웃된 경우 발생하는 예외.

    설정된 타임아웃 내에 어떤 후보도 성공하지 못했을 때 발생합니다.
    """

    def __init__(self, timeout: float):
        """
        Args:
            timeout: 설정된 타임아웃 값 (초)
        """
        self.timeout = timeout
        super().__init__(f"Hedging timed out after {timeout}s")


class NonRetryableHedgingError(HedgingError):
    """
    재시도 불가 에러 - 즉시 실패 처리.

    PermissionError, ValueError, HTTP 4xx 등 재시도해도 결과가 변하지 않는
    확정적 에러가 발생했을 때 사용합니다. 다른 후보를 기다리지 않고
    즉시 실패 처리합니다.

    사용 시나리오:
        - PermissionError (403): 권한 없음 → 재시도 무의미
        - KeyError/ValueError: 잘못된 요청 → 재시도 무의미
        - HTTP 4xx 에러: 클라이언트 오류 → 재시도 무의미
    """

    def __init__(self, message: str, original_error: Exception | None = None):
        """
        Args:
            message: 에러 메시지
            original_error: 원본 예외 (선택)
        """
        self.original_error = original_error
        super().__init__(message)


class HedgingDisabledError(HedgingError):
    """
    헷징이 비활성화된 경우 발생하는 예외.

    Backpressure 레벨이 높아 헷징이 비활성화되었거나,
    설정에 의해 헷징이 꺼진 경우 발생합니다.
    """

    def __init__(self, load_level: str):
        """
        Args:
            load_level: 현재 부하 레벨 (예: "high", "critical")
        """
        self.load_level = load_level
        super().__init__(f"Hedging disabled due to high load: {load_level}")


# ── Deprecated aliases (하위 호환성) ──────────────────────────
HedgingException = HedgingError
HedgingAllFailedException = HedgingAllFailedError
HedgingTimeoutException = HedgingTimeoutError

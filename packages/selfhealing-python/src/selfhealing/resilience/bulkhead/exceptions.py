"""
Bulkhead Exceptions - 리소스 격리 관련 예외 클래스.

격벽 패턴에서 발생할 수 있는 예외들을 정의합니다:
- BulkheadFullError: 동시 실행 제한 초과
- BulkheadTimeoutError: 작업 실행 타임아웃
"""

from __future__ import annotations


class BulkheadError(Exception):
    """Bulkhead 기본 예외 클래스."""

    pass


class BulkheadFullError(BulkheadError):
    """
    격벽이 가득 차서 요청이 거부됨.

    동시 실행 허용 수를 초과하여 새 요청을 받을 수 없을 때 발생.
    """

    def __init__(
        self,
        bulkhead_name: str,
        max_concurrent: int,
        active_count: int,
    ):
        """
        Args:
            bulkhead_name: 격벽 이름
            max_concurrent: 최대 동시 실행 허용 수
            active_count: 현재 활성 실행 수
        """
        self.bulkhead_name = bulkhead_name
        self.max_concurrent = max_concurrent
        self.active_count = active_count
        super().__init__(f"Bulkhead '{bulkhead_name}' is full: " f"{active_count}/{max_concurrent} active")


class BulkheadTimeoutError(BulkheadError):
    """
    격벽 작업 타임아웃.

    ThreadPoolBulkhead에서 작업 실행이 지정된 시간 내에 완료되지 않을 때 발생.
    """

    def __init__(self, bulkhead_name: str, timeout: float):
        """
        Args:
            bulkhead_name: 격벽 이름
            timeout: 설정된 타임아웃 (초)
        """
        self.bulkhead_name = bulkhead_name
        self.timeout = timeout
        super().__init__(f"Bulkhead '{bulkhead_name}' timed out after {timeout}s")


# ── Deprecated aliases (하위 호환성) ──────────────────────────
BulkheadException = BulkheadError
BulkheadFullException = BulkheadFullError
BulkheadTimeoutException = BulkheadTimeoutError

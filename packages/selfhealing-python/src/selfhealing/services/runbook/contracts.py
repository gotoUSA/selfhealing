"""
Runbook 보상 No-op 안전성 계약(CompensationContract).

register_compensate() 시점에 Fail-fast 검증으로
No-op 안전하지 않은 보상 함수의 등록을 원천 차단한다.

No-op 안전 컨벤션:
    보상 Primitive는 대상 리소스가 이미 정리되었으면 에러가 아닌 success=True를 반환한다.
    동일 파라미터로 재시도되어도 항상 동일한 결과를 반환한다.
    이것이 In-doubt(타임아웃) 보상의 전제 조건이다.

올바른 예:
    def compensate_circuit_breaker(service_name: str, **kw) -> dict:
        current = repo.get_state(service_name)
        if current == CircuitState.CLOSED:
            return {"success": True, "noop": True}
        repo.atomic_force_close(service_name)
        return {"success": True, "noop": False}

잘못된 예:
    def bad_compensate(service_name: str):  # **kwargs 누락 → 등록 시 ValueError
        lock = get_lock(service_name)
        lock.release()                      # 락이 없으면 예외 발생 → No-op 불안전

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §6.4
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any


class CompensationContract:
    """보상 Primitive No-op 안전성 계약.

    register_compensate() 시점에 Fail-fast 검증으로
    No-op 안전하지 않은 보상 함수의 등록을 원천 차단한다.

    RunbookRegistry.register() Fail-fast 패턴 / _validate_all_step_params() 패턴과 동일.
    """

    @staticmethod
    def validate_noop_safety(
        compensate_fn: Callable,
        action_name: str,
        test_params: dict[str, Any] | None = None,
    ) -> None:
        """보상 함수가 No-op 안전한지 등록 시점에 검증.

        검증 조건:
            함수 시그니처에 **kwargs 허용 여부
            (미래 파라미터 확장 대비 및 In-doubt 보상 파라미터 호환성 보장)

        Args:
            compensate_fn: 검증할 보상 함수
            action_name: 보상 대상 Action 이름 (에러 메시지용)
            test_params: (예약됨) 향후 실제 호출 테스트 시 사용

        Raises:
            ValueError: **kwargs 없는 경우
        """
        sig = inspect.signature(compensate_fn)

        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if not has_var_keyword:
            raise ValueError(
                f"보상 함수 '{action_name}'은 **kwargs를 허용해야 한다. "
                f"미래 파라미터 확장 호환성과 In-doubt 보상 안전성을 위한 강제 조건이다. "
                f"시그니처: {sig}"
            )

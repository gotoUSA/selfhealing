"""
Runbook Executor 변수 치환(ParamResolver) 모듈.

Step params에 포함된 "${step.<name>.<field>}" / "${trigger.<field>}" 형태의 변수를
실제 값으로 치환한다.

설계 원칙 (273번 설계 결정 계승):
- 문자열 보간(interpolation)이 아닌 객체 참조(object graph traversal) 방식
  → Python 원시 타입(float, int, list 등)을 그대로 보존
- "prefix_${...}_suffix" 같은 부분 치환은 지원하지 않는다.
  값 전체가 "${...}" 형태인 경우만 객체 참조로 처리한다.
- jmespath / jinja2 외부 의존성을 사용하지 않는다.
  (selfhealing 패키지 의존성: redis, pydantic, pydantic-settings, structlog만)
- ParamResolver Protocol은 미래 SafeExpressionResolver 등을 위한 확장점으로만 예약

Reference:
    docs/self_healing/middleware_system/275_RUNBOOK_EXECUTOR.md §9
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from selfhealing.services.runbook.execution_models import RunbookExecutionContext


# =============================================================================
# ParamResolver Protocol — 확장점 예약
# =============================================================================


@runtime_checkable
class ParamResolver(Protocol):
    """변수 치환 확장 프로토콜.

    현재 DotPathResolver만 사용하지만, 향후 필요 시
    SafeExpressionResolver 등을 추가할 수 있는 확장점이다.

    구현체는 expression 문자열을 받아 ctx에서 실제 값을 반환한다.
    값을 찾지 못하면 None을 반환한다.
    """

    def resolve(self, expression: str, ctx: RunbookExecutionContext) -> Any:
        """표현식을 실행 컨텍스트에서 해석하여 실제 값으로 반환."""
        ...


# =============================================================================
# DotPathResolver — 기본 구현
# =============================================================================


class DotPathResolver:
    """${trigger.*} / ${step.*} dot-path 식 객체 참조 구현.

    Python 원시 타입을 그대로 반환한다.
    부분 치환은 지원하지 않는다 — 값 전체가 "${...}" 형태인 경우에만 치환한다.
    """

    def resolve(self, expression: str, ctx: RunbookExecutionContext) -> Any:
        """dot-path 표현식을 실행 컨텍스트에서 해석.

        Args:
            expression: "${...}" 안의 내용 (예: "trigger.service_name", "step.check.result_data.value")
            ctx: 현재 실행 컨텍스트

        Returns:
            해석된 값. 경로를 찾지 못하면 None.
        """
        parts = expression.split(".")

        if not parts:
            return None

        root = parts[0]

        if root == "trigger":
            return self._traverse(ctx.trigger_event, parts[1:])

        if root == "step":
            if len(parts) < 2:
                return None
            step_result = ctx.step_results.get(parts[1])
            if step_result is None:
                return None
            return self._traverse(step_result, parts[2:])

        return None

    @staticmethod
    def _traverse(obj: Any, parts: list[str]) -> Any:
        """obj에서 parts 경로를 따라가며 값을 반환.

        dict이면 .get(), 그 외이면 getattr()로 접근한다.
        """
        for part in parts:
            if obj is None:
                return None
            if isinstance(obj, dict):
                obj = obj.get(part)
            else:
                obj = getattr(obj, part, None)
        return obj


# =============================================================================
# 유틸리티 함수
# =============================================================================


def resolve_params(
    params: dict[str, Any],
    ctx: RunbookExecutionContext,
    resolver: ParamResolver | None = None,
) -> dict[str, Any]:
    """Step params의 변수 참조를 실제 값으로 전부 치환하여 새 dict 반환.

    변환 규칙:
    - 값 전체가 "${...}" 형태: 객체 참조로 치환
    - 그 외: 리터럴 그대로 전달

    Args:
        params: 원본 파라미터 dict (변경하지 않는다)
        ctx: 실행 컨텍스트
        resolver: ParamResolver 구현체 (None이면 DotPathResolver 사용)

    Returns:
        치환 완료된 새 dict. 원본 params는 변경되지 않는다.
    """
    _resolver = resolver or DotPathResolver()
    resolved: dict[str, Any] = {}

    for key, value in params.items():
        if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
            expression = value[2:-1]  # "${...}" → "..."
            resolved[key] = _resolver.resolve(expression, ctx)
        else:
            resolved[key] = value

    return resolved

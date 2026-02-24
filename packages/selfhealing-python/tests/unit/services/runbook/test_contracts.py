"""
CompensationContract 단위 테스트.

테스트 대상:
    selfhealing.services.runbook.contracts

계약 검증 클래스 (Test*Contract):
    - TestCompensationContractValidationContract — 검증 조건 설계 계약

동작 검증 클래스 (Test*Behavior):
    - TestCompensationContractValidationBehavior — validate_noop_safety 동작
    - TestActionPrimitiveRegistryCompensateBehavior — register_compensate + get_compensate
"""

from __future__ import annotations

import pytest

from selfhealing.services.runbook.contracts import CompensationContract
from selfhealing.services.runbook.runbook_registry import ActionPrimitiveRegistry


# =============================================================================
# 계약 검증 — CompensationContract 검증 조건
# =============================================================================


class TestCompensationContractValidationContract:
    """CompensationContract 설계 계약 검증.

    §6.4: **kwargs를 가진 함수만 등록 허용.
    """

    def test_function_with_kwargs_passes_validation(self):
        """**kwargs를 가진 함수는 검증을 통과해야 한다."""

        def valid_compensate(service_name: str, **kw) -> dict:
            return {"success": True}

        # 예외 없이 통과해야 한다
        CompensationContract.validate_noop_safety(valid_compensate, "enable_cb")

    def test_function_without_kwargs_raises_value_error(self):
        """**kwargs 없는 함수는 ValueError를 발생시켜야 한다."""

        def invalid_compensate(service_name: str):  # **kwargs 누락
            pass

        with pytest.raises(ValueError, match="\\*\\*kwargs"):
            CompensationContract.validate_noop_safety(invalid_compensate, "enable_cb")

    def test_error_message_includes_action_name(self):
        """에러 메시지에 action_name이 포함되어야 한다."""

        def invalid_fn(x: int):
            pass

        with pytest.raises(ValueError, match="my_action"):
            CompensationContract.validate_noop_safety(invalid_fn, "my_action")

    def test_lambda_with_kwargs_passes(self):
        """lambda에 **kwargs가 있으면 통과해야 한다."""
        fn = lambda service, **kw: {"success": True, "noop": True}
        CompensationContract.validate_noop_safety(fn, "test_action")


# =============================================================================
# 동작 검증 — register_compensate + get_compensate
# =============================================================================


class TestActionPrimitiveRegistryCompensateBehavior:
    """ActionPrimitiveRegistry.register_compensate / get_compensate 동작 검증."""

    def _fresh_registry(self) -> ActionPrimitiveRegistry:
        """새 레지스트리 인스턴스 (테스트 간 격리)."""
        return ActionPrimitiveRegistry()

    def test_register_and_retrieve_compensate_fn(self):
        """등록한 보상 함수를 get_compensate로 조회할 수 있어야 한다."""
        # Given
        registry = self._fresh_registry()
        fn = lambda svc, **kw: {"success": True}
        # When
        registry.register_compensate("enable_cb", fn, validate=False)
        # Then
        assert registry.get_compensate("enable_cb") is fn

    def test_get_compensate_returns_none_for_unknown_action(self):
        """등록되지 않은 action은 None을 반환해야 한다."""
        registry = self._fresh_registry()
        assert registry.get_compensate("nonexistent_action") is None

    def test_register_compensate_validates_kwargs_by_default(self):
        """validate=True(기본값)이면 **kwargs 없는 함수를 거부해야 한다."""
        registry = self._fresh_registry()
        bad_fn = lambda svc: None  # **kwargs 없음

        with pytest.raises(ValueError):
            registry.register_compensate("bad_action", bad_fn)

    def test_register_compensate_skips_validation_when_disabled(self):
        """validate=False이면 **kwargs 없는 함수도 등록해야 한다 (테스트 전용)."""
        registry = self._fresh_registry()
        bad_fn = lambda svc: None  # **kwargs 없음 — 실제 환경에서는 금지

        # 예외 없이 등록 성공
        registry.register_compensate("bad_action", bad_fn, validate=False)
        assert registry.get_compensate("bad_action") is bad_fn

    def test_register_compensate_overwrites_existing_entry(self):
        """동일 action_name으로 재등록하면 이전 함수를 덮어써야 한다."""
        registry = self._fresh_registry()
        fn_v1 = lambda svc, **kw: {"version": 1}
        fn_v2 = lambda svc, **kw: {"version": 2}

        registry.register_compensate("action_x", fn_v1, validate=False)
        registry.register_compensate("action_x", fn_v2, validate=False)

        assert registry.get_compensate("action_x") is fn_v2

    def test_compensate_handlers_independent_from_execute_handlers(self):
        """보상 핸들러는 실행 핸들러와 독립적으로 관리되어야 한다."""
        registry = self._fresh_registry()
        from selfhealing.services.runbook.runbook_registry import RunbookStepContext

        exec_fn = lambda ctx: {"success": True}
        comp_fn = lambda svc, **kw: {"reverted": True}

        registry.register("my_action", exec_fn)
        registry.register_compensate("my_action", comp_fn, validate=False)

        assert registry.get("my_action") is exec_fn
        assert registry.get_compensate("my_action") is comp_fn

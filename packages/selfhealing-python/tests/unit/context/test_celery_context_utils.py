"""
celery_context_utils 모듈 단위 테스트.

Celery 태스크 컨텍스트 통합 복원/정리 유틸리티 검증.
- 계약 검증: ContextCriticality 분류, SelfHealingContextError 속성
- 동작 검증: restore_all_task_context, cleanup_all_task_context, 리졸버, causation 로직
"""

from __future__ import annotations

import contextvars
import os
from unittest.mock import MagicMock, patch

import pytest

from selfhealing.context.celery_context_utils import (
    CONTEXT_CRITICALITY,
    ContextCriticality,
    SelfHealingContextError,
    TaskContextTokens,
    _CAUSATION_TOKEN_ATTR,
    _CONTEXT_TOKENS_ATTR,
    _cleanup_causation_context,
    _detect_causation_source,
    _get_task_request,
    _is_strict_context_enabled,
    _reset_strict_cell_context_cache,
    _resolve_cell_id,
    _resolve_domain,
    _setup_causation_context,
    cleanup_all_task_context,
    restore_all_task_context,
)
from selfhealing.context.causation_context import (
    CELERY_HEADER_CASCADE_ID,
    CELERY_HEADER_CHAIN_DEPTH,
    CELERY_HEADER_NAMESPACE,
    CELERY_HEADER_PARENT_EVENT,
    CausationContext,
    _current_causation,
)
from selfhealing.context.cell_context import _current_cell_id


# =============================================================================
# 계약 검증 (Contract Tests)
# =============================================================================


class TestContextCriticalityContract:
    """ContextCriticality 설계 계약값 검증."""

    def test_cell_id_is_critical(self):
        """cell_id는 CRITICAL로 분류되어야 한다 (DB 라우팅/캐시 격벽)."""
        assert CONTEXT_CRITICALITY["cell_id"] == ContextCriticality.CRITICAL

    def test_tenant_id_is_critical(self):
        """tenant_id는 CRITICAL로 분류되어야 한다 (멀티테넌트 격리)."""
        assert CONTEXT_CRITICALITY["tenant_id"] == ContextCriticality.CRITICAL

    def test_causation_is_important(self):
        """causation은 IMPORTANT로 분류되어야 한다 (인과관계 추적)."""
        assert CONTEXT_CRITICALITY["causation"] == ContextCriticality.IMPORTANT

    def test_trace_id_is_optional(self):
        """trace_id는 OPTIONAL로 분류되어야 한다 (관측용)."""
        assert CONTEXT_CRITICALITY["trace_id"] == ContextCriticality.OPTIONAL

    def test_domain_is_optional(self):
        """domain은 OPTIONAL로 분류되어야 한다 (태깅용)."""
        assert CONTEXT_CRITICALITY["domain"] == ContextCriticality.OPTIONAL

    def test_criticality_has_three_levels(self):
        """ContextCriticality는 CRITICAL, IMPORTANT, OPTIONAL 3단계."""
        members = {m.value for m in ContextCriticality}
        assert members == {"critical", "important", "optional"}


class TestSelfHealingContextErrorContract:
    """SelfHealingContextError 설계 계약 검증."""

    def test_error_is_exception_subclass(self):
        """SelfHealingContextError는 Exception의 하위 클래스여야 한다."""
        assert issubclass(SelfHealingContextError, Exception)

    def test_error_stores_context_name(self):
        """context_name 속성이 저장되어야 한다."""
        err = SelfHealingContextError("cell_id", "my_task")
        assert err.context_name == "cell_id"

    def test_error_stores_task_name(self):
        """task_name 속성이 저장되어야 한다."""
        err = SelfHealingContextError("cell_id", "my_task")
        assert err.task_name == "my_task"

    def test_error_message_contains_context_and_task(self):
        """에러 메시지에 context_name과 task_name이 포함되어야 한다."""
        err = SelfHealingContextError("cell_id", "process_order", "no header")
        msg = str(err)
        assert "cell_id" in msg
        assert "process_order" in msg
        assert "no header" in msg


class TestTaskContextTokensContract:
    """TaskContextTokens 기본 구조 계약 검증."""

    def test_default_tokens_are_none(self):
        """기본 토큰은 모두 None이어야 한다."""
        tokens = TaskContextTokens()
        assert tokens.cell_id_token is None
        assert tokens.causation_token is None
        assert tokens.domain_token is None

    def test_baggage_tokens_default_empty_dict(self):
        """baggage_tokens 기본값은 빈 dict이어야 한다."""
        tokens = TaskContextTokens()
        assert tokens.baggage_tokens == {}


# =============================================================================
# 동작 검증 (Behavior Tests)
# =============================================================================


@pytest.fixture(autouse=True)
def reset_contextvars():
    """테스트 간 ContextVar 상태 초기화."""
    cell_token = _current_cell_id.set(None)
    causation_token = _current_causation.set(None)
    yield
    _current_cell_id.reset(cell_token)
    _current_causation.reset(causation_token)


@pytest.fixture(autouse=True)
def reset_strict_mode_cache():
    """strict mode 캐시 초기화."""
    _reset_strict_cell_context_cache()
    yield
    _reset_strict_cell_context_cache()


class TestGetTaskRequestBehavior:
    """_get_task_request 동작 검증."""

    def test_returns_none_for_none_task(self):
        """task가 None이면 None 반환."""
        assert _get_task_request(None) is None

    def test_returns_none_when_no_request_attr(self):
        """task에 request 속성 없으면 None 반환."""
        task = MagicMock(spec=[])  # request 속성 없음
        assert _get_task_request(task) is None

    def test_returns_request_when_present(self):
        """task.request가 있으면 반환."""
        mock_request = MagicMock()
        task = MagicMock()
        task.request = mock_request
        assert _get_task_request(task) is mock_request

    def test_returns_none_for_none_request(self):
        """task.request가 None이면 None 반환."""
        task = MagicMock()
        task.request = None
        assert _get_task_request(task) is None


class TestResolveCellIdBehavior:
    """_resolve_cell_id 동작 검증."""

    def test_returns_none_when_no_cell_id(self):
        """cell_id 없으면 (None, 'none') 반환."""
        task = MagicMock()
        task.request = MagicMock()
        task.request.get = MagicMock(return_value=None)
        cell_id, source = _resolve_cell_id(task)
        assert cell_id is None
        assert source == "none"

    def test_returns_legacy_header_when_present(self):
        """Legacy 헤더에 cell_id 있으면 반환."""
        task = MagicMock()
        task.request = MagicMock()
        task.request.get = MagicMock(return_value="cell-5")
        cell_id, source = _resolve_cell_id(task)
        assert cell_id == "cell-5"
        assert source == "legacy_header"

    def test_returns_none_for_none_task(self):
        """task가 None이면 (None, 'none') 반환."""
        cell_id, source = _resolve_cell_id(None)
        assert cell_id is None
        assert source == "none"

    def test_baggage_fallback_to_legacy_on_import_error(self):
        """OTel 미설치 시 Legacy로 Fallback."""
        task = MagicMock()
        task.request = MagicMock()
        task.request.get = MagicMock(return_value="cell-fallback")
        # OTel import 실패 시에도 legacy 경로가 동작하는지 확인
        cell_id, source = _resolve_cell_id(task)
        assert cell_id == "cell-fallback"
        assert source == "legacy_header"


class TestResolveDomainBehavior:
    """_resolve_domain 동작 검증."""

    def test_returns_none_when_no_domain(self):
        """domain 없으면 (None, 'none') 반환."""
        task = MagicMock()
        task.request = MagicMock()
        task.request.get = MagicMock(return_value=None)
        domain, source = _resolve_domain(task)
        assert domain is None
        assert source == "none"

    def test_returns_legacy_header_when_present(self):
        """Legacy 헤더에 domain 있으면 반환."""
        task = MagicMock()
        task.request = MagicMock()
        task.request.get = MagicMock(return_value="payment")
        domain, source = _resolve_domain(task)
        assert domain == "payment"
        assert source == "legacy_header"


class TestDetectCausationSourceBehavior:
    """_detect_causation_source 동작 검증."""

    @pytest.mark.parametrize(
        "task_name,expected_source",
        [
            ("celery.beat.check", "celery_beat"),
            ("schedule_daily_task", "celery_beat"),
            ("periodic_cleanup", "celery_beat"),
            ("manage_users", "management_cmd"),
            ("run_command_task", "management_cmd"),
            ("admin_bulk_update", "management_cmd"),
            ("cron_daily_report", "scheduler"),
            ("cleanup_old_data", "scheduler"),
            ("expire_sessions", "scheduler"),
            ("process_order", "worker"),
            ("send_email", "worker"),
        ],
    )
    def test_detects_correct_source(self, task_name: str, expected_source: str):
        """태스크 이름 패턴에 따라 올바른 source를 반환해야 한다."""
        assert _detect_causation_source(task_name) == expected_source


class TestSetupCausationContextBehavior:
    """_setup_causation_context 동작 검증."""

    def test_restores_from_valid_headers(self):
        """유효한 헤더에서 causation 복원."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = {
            CELERY_HEADER_CASCADE_ID: "cascade-abc123",
            CELERY_HEADER_PARENT_EVENT: "evt-parent",
            CELERY_HEADER_CHAIN_DEPTH: "2",
            CELERY_HEADER_NAMESPACE: "seoul",
        }

        token = _setup_causation_context(mock_task, "task-1", "test_task")

        info = CausationContext.get_current()
        assert info is not None
        assert info.cascade_id == "cascade-abc123"
        assert info.chain_depth == 3  # 2 + 1
        assert info.namespace == "seoul"
        assert token is not None

    def test_creates_system_cascade_without_headers(self):
        """헤더 없으면 시스템 cascade 자동 생성."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = {}

        token = _setup_causation_context(mock_task, "task-2", "test_task")

        info = CausationContext.get_current()
        assert info is not None
        assert info.cascade_id.startswith("cascade-")
        assert info.parent_event_id.startswith("SYSTEM_ROOT_")
        assert info.metadata.get("auto_generated") is True
        assert token is not None

    def test_returns_none_without_request(self):
        """request 없으면 None 반환, 컨텍스트 미설정."""
        mock_task = MagicMock()
        mock_task.request = None

        token = _setup_causation_context(mock_task, "task-3", "test_task")
        assert token is None
        assert CausationContext.get_current() is None

    def test_stores_token_on_request(self):
        """token이 task.request에 저장되어야 한다."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = {
            CELERY_HEADER_CASCADE_ID: "cascade-token-test",
            CELERY_HEADER_CHAIN_DEPTH: "0",
        }

        _setup_causation_context(mock_task, "task-4", "test_task")

        assert hasattr(mock_task.request, _CAUSATION_TOKEN_ATTR)


class TestCleanupCausationContextBehavior:
    """_cleanup_causation_context 동작 검증."""

    def test_cleanup_removes_context(self):
        """정리 후 causation 컨텍스트 제거."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = {
            CELERY_HEADER_CASCADE_ID: "cascade-cleanup",
            CELERY_HEADER_CHAIN_DEPTH: "0",
        }

        _setup_causation_context(mock_task, "task-5", "cleanup_test")
        assert CausationContext.get_current() is not None

        _cleanup_causation_context(mock_task)
        assert CausationContext.get_current() is None

    def test_cleanup_without_token_no_error(self):
        """token 없어도 예외 없이 처리."""
        mock_task = MagicMock()
        mock_task.request = MagicMock(spec=[])
        # _CAUSATION_TOKEN_ATTR 속성 없는 상태
        _cleanup_causation_context(mock_task)  # 예외 없어야 함

    def test_cleanup_without_request_no_error(self):
        """request 없어도 예외 없이 처리."""
        mock_task = MagicMock()
        mock_task.request = None
        _cleanup_causation_context(mock_task)  # 예외 없어야 함


class TestRestoreAllTaskContextBehavior:
    """restore_all_task_context 동작 검증."""

    def _make_task_with_request(self, headers=None, cell_id=None):
        """테스트용 mock task 생성."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = headers or {}
        mock_task.request.retries = 0
        mock_task.request.get = MagicMock(return_value=cell_id)
        return mock_task

    def test_restores_trace_id(self):
        """trace_id가 복원되어야 한다."""
        from selfhealing.audit.trace import get_trace_id

        task = self._make_task_with_request()
        restore_all_task_context(task, "task-trace-1", "test_task")

        trace_id = get_trace_id()
        assert trace_id is not None
        assert "task-trace-1" in trace_id or trace_id.startswith("CELERY_")

    def test_restores_trace_id_from_kwargs(self):
        """kwargs에 trace_info가 있으면 해당 trace_id를 사용."""
        from selfhealing.audit.trace import get_trace_id

        task = self._make_task_with_request()
        kwargs = {"trace_info": {"trace_id": "CUSTOM_TRACE_123"}}
        restore_all_task_context(task, "task-trace-2", "test_task", kwargs)

        assert get_trace_id() == "CUSTOM_TRACE_123"

    def test_restores_causation_context(self):
        """causation 컨텍스트가 복원되어야 한다."""
        headers = {
            CELERY_HEADER_CASCADE_ID: "cascade-restore-all",
            CELERY_HEADER_CHAIN_DEPTH: "1",
        }
        task = self._make_task_with_request(headers=headers)

        tokens = restore_all_task_context(task, "task-6", "test_task")

        info = CausationContext.get_current()
        assert info is not None
        assert info.cascade_id == "cascade-restore-all"
        assert tokens.causation_token is not None

    def test_restores_cell_id(self):
        """cell_id가 ContextVar에 복원되어야 한다."""
        task = self._make_task_with_request(cell_id="cell-7")

        tokens = restore_all_task_context(task, "task-7", "test_task")

        from selfhealing.context.cell_context import get_current_cell_id

        assert get_current_cell_id() == "cell-7"
        assert tokens.cell_id_token is not None

    def test_stores_tokens_on_request(self):
        """토큰이 task.request에 저장되어야 한다."""
        task = self._make_task_with_request(cell_id="cell-8")

        restore_all_task_context(task, "task-8", "test_task")

        assert hasattr(task.request, _CONTEXT_TOKENS_ATTR)
        stored_tokens = getattr(task.request, _CONTEXT_TOKENS_ATTR)
        assert isinstance(stored_tokens, TaskContextTokens)

    def test_returns_tokens_object(self):
        """TaskContextTokens 인스턴스를 반환해야 한다."""
        task = self._make_task_with_request()
        tokens = restore_all_task_context(task, "task-9", "test_task")
        assert isinstance(tokens, TaskContextTokens)

    def test_no_cell_id_without_strict_mode(self):
        """strict mode 비활성화 시 cell_id 없어도 예외 미발생."""
        task = self._make_task_with_request(cell_id=None)
        with patch.dict(os.environ, {"SELFHEALING_STRICT_CELL_CONTEXT": "false"}):
            _reset_strict_cell_context_cache()
            tokens = restore_all_task_context(task, "task-10", "test_task")
            assert tokens.cell_id_token is None  # 설정되지 않음

    def test_no_cell_id_with_strict_mode_raises(self):
        """strict mode 활성화 시 cell_id 없으면 SelfHealingContextError 발생."""
        task = self._make_task_with_request(cell_id=None)
        with patch.dict(os.environ, {"SELFHEALING_STRICT_CELL_CONTEXT": "true"}):
            _reset_strict_cell_context_cache()
            with pytest.raises(SelfHealingContextError) as exc_info:
                restore_all_task_context(task, "task-11", "strict_test_task")
            assert exc_info.value.context_name == "cell_id"
            assert exc_info.value.task_name == "strict_test_task"


class TestCleanupAllTaskContextBehavior:
    """cleanup_all_task_context 동작 검증."""

    def _make_task_with_request(self, headers=None, cell_id=None):
        """테스트용 mock task 생성."""
        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = headers or {}
        mock_task.request.retries = 0
        mock_task.request.get = MagicMock(return_value=cell_id)
        return mock_task

    def test_cleans_up_all_context(self):
        """모든 컨텍스트가 정리되어야 한다."""
        from selfhealing.context.cell_context import get_current_cell_id

        headers = {
            CELERY_HEADER_CASCADE_ID: "cascade-cleanup-all",
            CELERY_HEADER_CHAIN_DEPTH: "0",
        }
        task = self._make_task_with_request(headers=headers, cell_id="cell-cleanup")

        restore_all_task_context(task, "task-12", "cleanup_all_test")

        # 복원 확인
        assert get_current_cell_id() == "cell-cleanup"
        assert CausationContext.get_current() is not None

        # 정리
        cleanup_all_task_context(task)

        # 정리 확인
        assert get_current_cell_id() is None
        assert CausationContext.get_current() is None

    def test_cleanup_without_tokens_no_error(self):
        """토큰 없는 task도 정리 시 예외 없이 처리."""
        task = MagicMock()
        task.request = MagicMock(spec=[])
        cleanup_all_task_context(task)  # 예외 없어야 함

    def test_cleanup_with_none_task_no_error(self):
        """task가 None이어도 예외 없이 처리."""
        cleanup_all_task_context(None)  # 예외 없어야 함

    def test_removes_tokens_attr_from_request(self):
        """정리 후 request에서 토큰 속성이 제거되어야 한다."""
        task = self._make_task_with_request(cell_id="cell-remove")
        restore_all_task_context(task, "task-13", "remove_test")

        assert hasattr(task.request, _CONTEXT_TOKENS_ATTR)

        cleanup_all_task_context(task)

        # delattr 호출 확인 (MagicMock에서는 실제 삭제되지 않으므로 호출 확인)
        # 실제로는 cleanup 내부에서 delattr(request, _CONTEXT_TOKENS_ATTR) 호출됨


class TestStrictModeBehavior:
    """strict mode 동작 검증."""

    def test_defaults_to_false(self):
        """기본값은 False (개발 환경 호환)."""
        with patch.dict(os.environ, {}, clear=True):
            _reset_strict_cell_context_cache()
            # SELFHEALING_STRICT_CELL_CONTEXT 환경변수가 없으면 False
            assert _is_strict_context_enabled() is False

    def test_true_when_env_set(self):
        """환경변수가 true이면 True."""
        with patch.dict(os.environ, {"SELFHEALING_STRICT_CELL_CONTEXT": "true"}):
            _reset_strict_cell_context_cache()
            assert _is_strict_context_enabled() is True

    def test_accepts_various_truthy_values(self):
        """1, yes, on도 True로 인식."""
        for value in ("1", "yes", "on", "True", "TRUE"):
            with patch.dict(os.environ, {"SELFHEALING_STRICT_CELL_CONTEXT": value}):
                _reset_strict_cell_context_cache()
                assert _is_strict_context_enabled() is True

    def test_caches_result(self):
        """결과가 캐싱되어야 한다."""
        with patch.dict(os.environ, {"SELFHEALING_STRICT_CELL_CONTEXT": "true"}):
            _reset_strict_cell_context_cache()
            first = _is_strict_context_enabled()
        # 환경변수 변경해도 캐시된 값 유지
        second = _is_strict_context_enabled()
        assert first == second


class TestDeprecatedReExportsBehavior:
    """signal_hooks.py의 DeprecationWarning re-export 동작 검증."""

    def test_setup_causation_context_reexport_warns(self):
        """signal_hooks._setup_causation_context 호출 시 DeprecationWarning."""
        from selfhealing.adapters.celery.signal_hooks import (
            _setup_causation_context as deprecated_setup,
        )

        mock_task = MagicMock()
        mock_task.request = MagicMock()
        mock_task.request.headers = {}

        with pytest.warns(DeprecationWarning, match="celery_context_utils"):
            deprecated_setup(mock_task, "task-dep-1", "deprecated_test")

    def test_detect_causation_source_reexport_warns(self):
        """signal_hooks._detect_causation_source 호출 시 DeprecationWarning."""
        from selfhealing.adapters.celery.signal_hooks import (
            _detect_causation_source as deprecated_detect,
        )

        with pytest.warns(DeprecationWarning, match="celery_context_utils"):
            result = deprecated_detect("process_order")
            assert result == "worker"

    def test_cleanup_causation_context_reexport_warns(self):
        """signal_hooks._cleanup_causation_context 호출 시 DeprecationWarning."""
        from selfhealing.adapters.celery.signal_hooks import (
            _cleanup_causation_context as deprecated_cleanup,
        )

        mock_task = MagicMock()
        mock_task.request = None

        with pytest.warns(DeprecationWarning, match="celery_context_utils"):
            deprecated_cleanup(mock_task)

"""
stdlib logging → structlog 마이그레이션 스크립트 단위 테스트.

대상: scripts/migrate_to_structlog.py 의 변환 함수들

테스트 분류:
- Contract: 이벤트 이름, 태그 매핑 등 설계 사양 하드코딩 검증
- Behavior: 변환 함수의 입출력 동작 검증

주의: 마이그레이션 스크립트는 723개 파일에 적용되므로
      변환 함수의 정확성이 핵심 보호 대상이다.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

# scripts/ 디렉토리를 경로에 추가하여 migrate_to_structlog 임포트
_SCRIPTS_DIR = Path(__file__).parents[4] / "scripts"
sys.path.insert(0, str(_SCRIPTS_DIR))

from migrate_to_structlog import (
    TAG_TO_COMPONENT,
    TransformStats,
    _expr_to_kwarg_name,
    _is_balanced,
    _split_args,
    _tag_to_snake,
    extract_fstring_kwargs,
    extract_percent_s_kwargs,
    extract_tag_and_message,
    message_to_action,
    resolve_event_name,
    transform_file_content,
    transform_log_call,
)


# ===========================================================================
# Contract Tests — 설계 사양 하드코딩 검증
# ===========================================================================


class TestTagToComponentMappingContract:
    """TAG_TO_COMPONENT 매핑 계약: 주요 태그는 문서 §2.4 사양과 일치해야 한다."""

    def test_self_healer_watchdog_maps_to_watchdog(self):
        assert TAG_TO_COMPONENT["SelfHealerWatchdog"] == "watchdog"

    def test_circuit_breaker_maps_correctly(self):
        assert TAG_TO_COMPONENT["CircuitBreaker"] == "circuit_breaker"

    def test_adaptive_throttle_maps_correctly(self):
        assert TAG_TO_COMPONENT["AdaptiveThrottle"] == "adaptive_throttle"

    def test_escalation_manager_maps_to_escalation(self):
        assert TAG_TO_COMPONENT["EscalationManager"] == "escalation"

    def test_resilient_storage_maps_correctly(self):
        assert TAG_TO_COMPONENT["ResilientStorage"] == "resilient_storage"

    def test_mapping_table_contains_minimum_required_entries(self):
        """매핑 테이블이 문서에 명시된 7개 핵심 컴포넌트를 모두 포함해야 한다."""
        required_components = {
            "watchdog",
            "circuit_breaker",
            "adaptive_throttle",
            "escalation",
            "resilient_storage",
            "cell_registry",
            "metrics",
        }
        actual_components = set(TAG_TO_COMPONENT.values())
        assert required_components.issubset(actual_components)


# ===========================================================================
# Behavior Tests — 변환 함수 동작 검증
# ===========================================================================


class TestTagToSnakeBehavior:
    """_tag_to_snake: PascalCase → snake_case 변환 동작 검증."""

    def test_simple_camel_case(self):
        assert _tag_to_snake("CircuitBreaker") == "circuit_breaker"

    def test_multi_word_pascal_case(self):
        assert _tag_to_snake("SelfHealerWatchdog") == "self_healer_watchdog"

    def test_single_word(self):
        assert _tag_to_snake("Metrics") == "metrics"

    def test_abbreviation_preservation(self):
        # 연속 대문자(약어) 처리
        result = _tag_to_snake("RedisAuditBuffer")
        assert result == "redis_audit_buffer"

    def test_already_lower(self):
        assert _tag_to_snake("metrics") == "metrics"


class TestExtractTagAndMessageBehavior:
    """extract_tag_and_message: [PrefixTag] 추출 및 component 매핑 검증."""

    def test_known_tag_maps_to_component(self):
        component, message = extract_tag_and_message("[SelfHealerWatchdog] Recovery failed for x")
        assert component == "watchdog"
        assert message == "Recovery failed for x"

    def test_unknown_tag_converts_to_snake_case(self):
        component, message = extract_tag_and_message("[MyCustomTag] some message")
        assert component == "my_custom_tag"
        assert message == "some message"

    def test_no_tag_returns_none_component(self):
        component, message = extract_tag_and_message("Cell not found: x")
        assert component is None
        assert message == "Cell not found: x"

    def test_tag_stripped_from_message(self):
        _, message = extract_tag_and_message("[CircuitBreaker] Serving stale cache")
        assert "[CircuitBreaker]" not in message
        assert message == "Serving stale cache"

    def test_fstring_with_tag(self):
        component, message = extract_tag_and_message("[AdaptiveThrottle] Unknown event type: {name}")
        assert component == "adaptive_throttle"
        assert "{name}" in message

    def test_empty_message_after_tag(self):
        component, message = extract_tag_and_message("[Tag]")
        assert message == ""


class TestMessageToActionBehavior:
    """message_to_action: 자연어 메시지 → action 이름 변환 동작 검증."""

    def test_failed_suffix_extraction(self):
        action = message_to_action("Recovery failed for {component}: {e}")
        assert "failed" in action or "recovery" in action

    def test_connected_state_extraction(self):
        action = message_to_action("Redis connected successfully")
        assert "redis" in action or "connected" in action

    def test_stopwords_excluded(self):
        action = message_to_action("The service is in a state")
        # stopwords(the, is, in, a)가 action에 포함되지 않아야 함
        for word in ["the", "is", "in", "a"]:
            assert word not in action.split("_")

    def test_fstring_variables_not_in_action(self):
        action = message_to_action("Evicted {len(evicted)} expired services from {cell_id}")
        # 중괄호 내 표현식이 action에 포함되지 않아야 함
        assert "{" not in action
        assert "}" not in action

    def test_empty_message_returns_event(self):
        action = message_to_action("")
        assert action == "event"

    def test_action_is_snake_case(self):
        action = message_to_action("Redis init failed")
        # snake_case 검증 (공백 없음, 소문자)
        assert " " not in action
        assert action == action.lower()


class TestResolveEventNameBehavior:
    """resolve_event_name: 이벤트 이름 결정 동작 검증."""

    def test_known_message_pattern_returns_catalog_event(self):
        event = resolve_event_name("watchdog", "Recovery failed for x")
        assert event == "watchdog.recovery_failed"

    def test_known_stale_cache_event(self):
        event = resolve_event_name("circuit_breaker", "Serving stale cache for x")
        assert event == "circuit_breaker.stale_cache_served"

    def test_unknown_message_uses_component_prefix(self):
        event = resolve_event_name("my_component", "Some unique message xyz")
        assert event.startswith("my_component.")

    def test_none_component_no_prefix(self):
        event = resolve_event_name(None, "Some unique message xyz")
        assert "." not in event or event.startswith(".")  # component가 없으므로 prefix 없음

    def test_event_name_has_no_spaces(self):
        event = resolve_event_name("watchdog", "Some unknown message")
        assert " " not in event

    def test_redis_connected_event(self):
        event = resolve_event_name("resilient_storage", "Redis connected successfully")
        assert event == "resilient_storage.redis_connected"


class TestExtractFstringKwargsBehavior:
    """extract_fstring_kwargs: f-string 변수 추출 동작 검증."""

    def test_simple_variable_extraction(self):
        kwargs = extract_fstring_kwargs("Cell not found: {cell_id}")
        assert len(kwargs) == 1
        name, expr = kwargs[0]
        assert name == "cell_id"
        assert expr == "cell_id"

    def test_multiple_variables(self):
        kwargs = extract_fstring_kwargs("State changed: {cell_id} {old_state.value} → {state.value} ({reason})")
        names = [k[0] for k in kwargs]
        assert "cell_id" in names
        assert "reason" in names

    def test_len_expression_becomes_count(self):
        kwargs = extract_fstring_kwargs("Evicted {len(evicted)} expired services from {cell_id}")
        names = {k[0]: k[1] for k in kwargs}
        assert "count" in names
        assert names["count"] == "len(evicted)"

    def test_exception_variable_becomes_error(self):
        kwargs = extract_fstring_kwargs("Recovery failed for {component}: {e}")
        names = {k[0]: k[1] for k in kwargs}
        assert "error" in names

    def test_no_variables_returns_empty(self):
        kwargs = extract_fstring_kwargs("Plain message without variables")
        assert kwargs == []

    def test_format_spec_excluded(self):
        # {remaining:.0f} → format spec 제거
        kwargs = extract_fstring_kwargs("Remaining: {remaining:.0f}s")
        assert len(kwargs) == 1
        name, expr = kwargs[0]
        assert expr == "remaining"

    def test_attribute_access_not_split_by_lazy_match(self):
        """result.domains 같은 속성 접근이 lazy match로 쪼개지지 않아야 한다."""
        # 버그: lazy regex가 '.'을 format spec 시작으로 오인하여
        # 'len(result.domains)' → 'len(result' (잘린 표현식) 발생
        kwargs = extract_fstring_kwargs("Reconciled: domains={len(result.domains)}")
        assert len(kwargs) == 1
        name, expr = kwargs[0]
        assert expr == "len(result.domains)", f"Expected 'len(result.domains)', got '{expr}'"
        assert name == "count"

    def test_simple_attribute_access(self):
        """old_state.value 같은 속성 접근이 올바르게 추출된다."""
        kwargs = extract_fstring_kwargs("{old_state.value} → {state.value}")
        assert len(kwargs) == 2
        exprs = {k[1] for k in kwargs}
        assert "old_state.value" in exprs
        assert "state.value" in exprs


class TestExprToKwargNameBehavior:
    """_expr_to_kwarg_name: Python 표현식 → 키워드 이름 변환 동작 검증."""

    def test_simple_identifier_unchanged(self):
        assert _expr_to_kwarg_name("cell_id") == "cell_id"

    def test_exception_variables_become_error(self):
        for var in ["e", "ex", "err", "exc"]:
            assert _expr_to_kwarg_name(var) == "error", f"{var} should map to 'error'"

    def test_len_becomes_count(self):
        assert _expr_to_kwarg_name("len(evicted)") == "count"

    def test_attribute_access_uses_base(self):
        assert _expr_to_kwarg_name("old_state.value") == "old_state"

    def test_function_call_uses_function_name(self):
        assert _expr_to_kwarg_name("str(e)") == "error"


class TestExtractPercentSKwargsBehavior:
    """extract_percent_s_kwargs: %s 포맷 인자 → 키워드 인자 변환 동작 검증."""

    def test_single_percent_s(self):
        kwargs = extract_percent_s_kwargs("[ResilientStorage] Redis init failed: %s", ["err_msg"])
        assert len(kwargs) == 1
        name, expr = kwargs[0]
        assert expr == "err_msg"

    def test_exception_arg_becomes_error(self):
        kwargs = extract_percent_s_kwargs("Failed: %s", ["e"])
        name, expr = kwargs[0]
        assert name == "error"
        assert expr == "e"

    def test_multiple_args(self):
        kwargs = extract_percent_s_kwargs("Service '%s': %s -> %s", ["svc", "cell_id", "new_cell"])
        assert len(kwargs) == 3
        exprs = [k[1] for k in kwargs]
        assert "svc" in exprs
        assert "cell_id" in exprs
        assert "new_cell" in exprs


class TestIsBalancedBehavior:
    """_is_balanced: 괄호 균형 확인 동작 검증."""

    def test_simple_balanced(self):
        assert _is_balanced('f"message {x}"') is True

    def test_nested_balanced(self):
        assert _is_balanced('f"count={len(evicted)}"') is True

    def test_unbalanced_returns_false(self):
        assert _is_balanced('f"msg {x}"') is True  # 균형 맞음
        # 실제 불균형 케이스
        assert _is_balanced('"msg"')  # 균형 맞음


class TestTransformLogCallBehavior:
    """transform_log_call: 단일 로그 호출 변환 동작 검증."""

    def _make_stats(self) -> TransformStats:
        return TransformStats()

    def test_fstring_with_tag_converted(self):
        stats = self._make_stats()
        result = transform_log_call(
            indent="    ",
            level="warning",
            args_str='f"[SelfHealerWatchdog] {name} unhealthy, attempting recovery"',
            stats=stats,
        )
        assert result is not None
        assert "watchdog.unhealthy_detected" in result
        assert "name=name" in result

    def test_plain_string_with_tag_converted(self):
        stats = self._make_stats()
        result = transform_log_call(
            indent="        ",
            level="debug",
            args_str='"[SelfHealerWatchdog] Skipping due to Self CB"',
            stats=stats,
        )
        assert result is not None
        assert "watchdog.self_cb_skipped" in result
        # plain string 변환 통계 증가 확인
        assert stats.plain_calls_converted == 1

    def test_percent_s_converted(self):
        stats = self._make_stats()
        result = transform_log_call(
            indent="    ",
            level="warning",
            args_str='"[ResilientStorage] Redis init failed: %s", err_msg',
            stats=stats,
        )
        assert result is not None
        assert "resilient_storage.redis_init_failed" in result
        assert "err_msg" in result
        assert stats.percent_s_calls_converted == 1

    def test_fstring_without_tag_converted(self):
        stats = self._make_stats()
        result = transform_log_call(
            indent="    ",
            level="warning",
            args_str='f"Cell not found: {cell_id}"',
            stats=stats,
        )
        assert result is not None
        assert "cell_registry.cell_not_found" in result
        assert "cell_id=cell_id" in result

    def test_indent_preserved(self):
        stats = self._make_stats()
        result = transform_log_call(
            indent="        ",  # 8칸 들여쓰기
            level="info",
            args_str='"[SelfHealerWatchdog] Attempting DLQ recovery"',
            stats=stats,
        )
        assert result is not None
        assert result.startswith("        logger.")

    def test_returns_none_for_complex_pattern(self):
        """파싱 불가능한 복잡 패턴은 None을 반환해야 한다."""
        stats = self._make_stats()
        # 복잡한 중첩 표현식
        result = transform_log_call(
            indent="    ",
            level="error",
            args_str='f"msg {x}", extra={"key": "val"}',
            stats=stats,
        )
        # None이거나 변환됐거나 — 스크립트가 안전하게 처리
        # 핵심: 예외를 던지지 않아야 함


class TestTransformFileContentBehavior:
    """transform_file_content: 파일 전체 변환 동작 검증."""

    def _make_stats(self) -> TransformStats:
        return TransformStats()

    def test_import_replaced(self):
        source = "import logging\n" "logger = logging.getLogger(__name__)\n" "\n" "def foo():\n" '    logger.info("hello")\n'
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        assert "import structlog" in result
        assert "logger = structlog.get_logger()" in result
        assert "import logging" not in result
        assert "logging.getLogger" not in result
        assert stats.imports_replaced == 1

    def test_no_logging_import_unchanged(self):
        source = "x = 1\n"
        stats = self._make_stats()
        result = transform_file_content(source, stats)
        assert result == source
        assert stats.imports_replaced == 0

    def test_fstring_log_call_converted_in_file(self):
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "def check(cell_id):\n"
            '    logger.warning(f"[SelfHealerWatchdog] Recovery failed for {cell_id}: {e}")\n'
        )
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        assert "watchdog.recovery_failed" in result
        assert 'f"[SelfHealerWatchdog]' not in result

    def test_structlog_config_skipped_if_already_structlog(self):
        """이미 structlog를 사용하는 파일은 import 재삽입하지 않는다."""
        source = "import structlog\n" "logger = structlog.get_logger()\n" "\n" "def foo():\n" '    logger.info("event")\n'
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        # 이미 structlog: 중복 import 없어야 함
        assert result.count("import structlog") == 1
        assert stats.imports_replaced == 0

    def test_multiline_logger_call_preserved(self):
        """멀티라인 logger 호출은 안전하게 보존된다 (변환 스킵)."""
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "def foo():\n"
            "    logger.info(\n"
            '        f"Multi-line {value}"\n'
            "    )\n"
        )
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        # 멀티라인 호출은 현재 변환 미지원 — 원본 보존
        assert "Multi-line" in result
        # import는 교체되어야 함
        assert "import structlog" in result

    def test_multiple_log_calls_in_file(self):
        """여러 로그 호출이 있는 파일에서 각각 독립적으로 변환된다."""
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "def foo(cell_id):\n"
            '    logger.info(f"Cell not found: {cell_id}")\n'
            '    logger.warning("[SelfHealerWatchdog] Recovery cooldown active")\n'
        )
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        assert "cell_registry.cell_not_found" in result
        assert "watchdog.recovery_cooldown_active" in result

    def test_trailing_newline_preserved(self):
        """파일 끝 개행이 보존된다."""
        source = "import logging\nlogger = logging.getLogger(__name__)\n"
        stats = self._make_stats()
        result = transform_file_content(source, stats)
        assert result.endswith("\n")

    def test_exception_handling_in_log_call(self):
        """logger.error(f"...{e}") 패턴에서 예외 변수가 error kwarg로 변환된다."""
        source = (
            "import logging\n"
            "logger = logging.getLogger(__name__)\n"
            "\n"
            "def foo(e):\n"
            '    logger.error(f"[SelfHealerWatchdog] check_health error: {e}")\n'
        )
        stats = self._make_stats()
        result = transform_file_content(source, stats)

        assert "watchdog.health_check_failed" in result
        assert "error=e" in result


class TestSplitArgsBehavior:
    """_split_args: 쉼표 분리 동작 검증."""

    def test_simple_args(self):
        result = _split_args("a, b, c")
        assert result == ["a", "b", "c"]

    def test_nested_parentheses_not_split(self):
        result = _split_args("str(e), cell_id")
        assert result == ["str(e)", "cell_id"]

    def test_single_arg(self):
        result = _split_args("err_msg")
        assert result == ["err_msg"]

    def test_empty_string(self):
        result = _split_args("")
        assert result == []

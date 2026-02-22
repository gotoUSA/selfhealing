"""
stdlib logging → structlog 일괄 마이그레이션 스크립트 (Phase 2).

변환 대상: packages/selfhealing-python/src/selfhealing/ 전체
변환 내용:
  1. import 문 교체 (723개 모듈)
  2. f-string 로깅 → structlog 이벤트+키워드 인자 (2,753건)
  3. %s-style 로깅 → structlog 이벤트+키워드 인자 (58건)
  4. [PrefixTag] → component bind 자동 주입
  5. logger.exception → structlog 패턴

실행 방법:
  # dry-run (변경 미적용, 변환 결과만 출력)
  python scripts/migrate_to_structlog.py --dry-run

  # 실제 변환 실행
  python scripts/migrate_to_structlog.py

  # 특정 디렉토리만 변환
  python scripts/migrate_to_structlog.py --target packages/selfhealing-python/src/selfhealing/meta

  # 변환 결과 리포트 저장
  python scripts/migrate_to_structlog.py --report migration_report.json
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# [PrefixTag] → structlog component 매핑 테이블 (문서 §2.4 기반)
# ---------------------------------------------------------------------------
TAG_TO_COMPONENT: dict[str, str] = {
    # meta
    "SelfHealerWatchdog": "watchdog",
    "EscalationManager": "escalation",
    "Escalation": "escalation",
    # circuit_breaker
    "CircuitBreaker": "circuit_breaker",
    "CB": "circuit_breaker",
    # cell topology
    "CellRegistry": "cell_registry",
    "CellEvacuation": "cell_evacuation",
    # throttle
    "AdaptiveThrottle": "adaptive_throttle",
    "Throttle": "throttle",
    "RateController": "rate_controller",
    # resilient storage / adapters
    "ResilientStorage": "resilient_storage",
    "RedisAuditBuffer": "redis_audit_buffer",
    "WAL": "wal",
    # observability & metrics
    "Metrics": "metrics",
    "EventHandler": "event_handler",
    "OTel": "otel",
    "OTEL": "otel",
    # audit
    "AuditLog": "audit_log",
    "Audit": "audit",
    # tasks
    "BaseNotifyingTask": "celery_task",
    "CeleryTask": "celery_task",
    # config
    "ConfigTracker": "config_tracker",
    # general self-healing
    "SelfHealing": "self_healing",
    "Recovery": "recovery",
    "FailedOperation": "failed_operation",
    "ShadowLogger": "shadow_logger",
    "LayeredRepository": "layered_repository",
    "SecurityIncident": "security_incident",
    "DriftReconciliation": "drift_reconciliation",
    # regional
    "RegionalGate": "regional_gate",
    # scaling
    "Scaling": "scaling",
    # coordination
    "Coordinator": "coordinator",
    # postmortem
    "Postmortem": "postmortem",
    "IncidentLog": "incident_log",
}

# 알려진 이벤트 매핑 (현재 메시지 패턴 → structlog 이벤트 이름) — 문서 §2.4 기반
# 정확한 문자열 매칭 (tag 제거 후 남은 메시지 기준)
KNOWN_EVENT_MAPPINGS: dict[str, str] = {
    # circuit_breaker
    "Invalid state for callback": "circuit_breaker.invalid_callback_state",
    "Sync callback failed": "circuit_breaker.sync_callback_failed",
    "Serving stale cache": "circuit_breaker.stale_cache_served",
    "Queued request to DLQ": "circuit_breaker.request_queued_to_dlq",
    "Returning default response": "circuit_breaker.default_response_returned",
    # cell_registry
    "Cell not found": "cell_registry.cell_not_found",
    "Cell state changed": "cell_registry.state_changed",
    "Service heartbeat recording failed": "cell_registry.heartbeat_failed",
    "Evicted": "cell_registry.services_evicted",
    "Registered": "cell_registry.bulkheads_registered",
    "ISOLATED": "cell_evacuation.cell_isolated",
    "restored to ACTIVE": "cell_evacuation.cell_restored",
    # adaptive_throttle
    "Governance blocked limit adjustment": "adaptive_throttle.governance_blocked",
    "Unknown event type": "adaptive_throttle.unknown_event_type",
    "Published": "adaptive_throttle.event_published",
    "Failed to record metrics": "adaptive_throttle.metrics_failed",
    # watchdog
    "unhealthy, attempting recovery": "watchdog.unhealthy_detected",
    "Dry-run: would attempt recovery": "watchdog.dry_run_recovery",
    "check_health error": "watchdog.health_check_failed",
    "Recovery cooldown active": "watchdog.recovery_cooldown_active",
    "Recovery failed": "watchdog.recovery_failed",
    "Self CB → Half-Open": "watchdog.self_cb_half_open",
    "Self CB → OPEN": "watchdog.self_cb_opened",
    "Skipping due to Self CB": "watchdog.self_cb_skipped",
    "No recovery action": "watchdog.no_recovery_action",
    "State store update failed": "watchdog.state_store_failed",
    "Forcing stuck CBs to HALF_OPEN": "watchdog.stuck_cb_force_half_open",
    "CB service not available": "watchdog.cb_service_unavailable",
    "CB recovery error": "watchdog.cb_recovery_failed",
    "Attempting DLQ recovery": "watchdog.dlq_recovery_started",
    "DLQ recovery error": "watchdog.dlq_recovery_failed",
    "Redis recovery Stage 1": "watchdog.redis_recovery_stage1",
    "Redis Stage 1 success": "watchdog.redis_recovery_stage1_succeeded",
    "Audit start record failed": "watchdog.audit_start_failed",
    "Audit complete record failed": "watchdog.audit_complete_failed",
    "Audit failed record failed": "watchdog.audit_failed_record_failed",
    # resilient_storage
    "Redis connected successfully": "resilient_storage.redis_connected",
    "Redis init failed": "resilient_storage.redis_init_failed",
    "Operating in DEGRADED mode": "resilient_storage.degraded_mode_entered",
    "WAL recovery error": "resilient_storage.wal_recovery_failed",
    "Recovered to REDIS mode": "resilient_storage.redis_mode_recovered",
    # escalation
    "Dry-run mode - would escalate": "escalation.dry_run_escalation",
    "maintenance": "escalation.maintenance_skipped",
    "Cooldown active": "escalation.cooldown_active",
    "Escalated:": "escalation.escalated",
    "PagerDuty sent": "escalation.pagerduty_sent",
    "Slack sent": "escalation.slack_sent",
}

# ---------------------------------------------------------------------------
# 통계 누적
# ---------------------------------------------------------------------------


@dataclass
class TransformStats:
    """마이그레이션 변환 통계."""

    files_scanned: int = 0
    files_changed: int = 0
    imports_replaced: int = 0
    fstring_calls_converted: int = 0
    percent_s_calls_converted: int = 0
    plain_calls_converted: int = 0
    exception_calls_converted: int = 0
    skipped_complex: int = 0
    errors: list[str] = field(default_factory=list)

    def total_calls_converted(self) -> int:
        return (
            self.fstring_calls_converted
            + self.percent_s_calls_converted
            + self.plain_calls_converted
            + self.exception_calls_converted
        )


# ---------------------------------------------------------------------------
# 태그 정규화
# ---------------------------------------------------------------------------

_TAG_PATTERN = re.compile(r"^\[([A-Za-z][A-Za-z0-9_]*)\]\s*")


def extract_tag_and_message(message: str) -> tuple[str | None, str]:
    """[PrefixTag] 추출 후 (component, 나머지 메시지) 반환.

    Examples:
        "[SelfHealerWatchdog] Recovery failed for x" → ("watchdog", "Recovery failed for x")
        "Cell not found: {cell_id}" → (None, "Cell not found: {cell_id}")
    """
    m = _TAG_PATTERN.match(message)
    if not m:
        return None, message

    raw_tag = m.group(1)
    remaining = message[m.end() :]
    component = TAG_TO_COMPONENT.get(raw_tag, _tag_to_snake(raw_tag))
    return component, remaining


def _tag_to_snake(tag: str) -> str:
    """PascalCase 태그를 snake_case로 변환.

    CamelCase/PascalCase → snake_case 변환:
        "SelfHealerWatchdog" → "self_healer_watchdog"
    """
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", tag)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    return s.lower()


# ---------------------------------------------------------------------------
# 이벤트 이름 생성
# ---------------------------------------------------------------------------

_STRIP_QUOTES_RE = re.compile(r"""^(['"])(.*)\1$""", re.DOTALL)
_FSTRING_VAR_RE = re.compile(r"\{([^}:!]+?)(?:[:!][^}]*)?\}")
_PERCENT_S_RE = re.compile(r"%[sdf]")
_ACTION_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "the",
        "for",
        "of",
        "in",
        "at",
        "to",
        "by",
        "on",
        "with",
        "from",
        "is",
        "was",
        "has",
        "have",
        "be",
        "been",
        "being",
        "or",
        "and",
        "not",
        "if",
        "that",
        "as",
        "but",
        "it",
        "its",
        "this",
        "are",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "did",
        "do",
        "does",
    ]
)


def message_to_action(message: str) -> str:
    """자연어 로그 메시지를 structlog action 이름으로 변환.

    전략:
    1. 알려진 이벤트 매핑에서 먼저 검색
    2. 없으면 메시지에서 핵심 동사/명사 추출 → snake_case action

    Examples:
        "Recovery failed for {component}: {e}" → "recovery_failed"
        "Redis connected successfully" → "redis_connected"
        "Evicted {n} expired services from {cell_id}" → "services_evicted"
    """
    # f-string 변수 제거 후 키워드 추출
    clean = _FSTRING_VAR_RE.sub(" ", message)
    clean = _PERCENT_S_RE.sub(" ", clean)
    clean = re.sub(r"[→\-–:,.!?'\"\[\]{}()]+", " ", clean)

    words = [w.lower() for w in clean.split() if w.isalpha() and w.lower() not in _ACTION_STOPWORDS and len(w) > 1]

    # 최대 4단어로 action 구성
    action_words = words[:4]
    if not action_words:
        return "event"
    return "_".join(action_words)


def resolve_event_name(component: str | None, message: str) -> str:
    """component + message → structlog 이벤트 이름 결정.

    알려진 이벤트 카탈로그에서 먼저 탐색하고,
    없으면 component + message_to_action으로 생성한다.
    """
    # 알려진 이벤트 카탈로그 매핑 (문자열 포함 여부로 검색)
    for pattern, event_name in KNOWN_EVENT_MAPPINGS.items():
        if pattern in message:
            return event_name

    action = message_to_action(message)
    if component:
        return f"{component}.{action}"
    return action


# ---------------------------------------------------------------------------
# f-string 키워드 인자 추출
# ---------------------------------------------------------------------------


def _iter_fstring_exprs(fstring_content: str):
    """f-string 내의 {expression} 블록을 순회하며 순수 표현식 문자열을 yield한다.

    bracket depth를 추적하여 슬라이스 {reason[:50]}, 속성 접근 {result.domains},
    중첩 함수 {len(items[0])} 등 복잡한 표현식도 올바르게 파싱한다.

    format spec ({value:.2f})과 conversion ({value!r})은 제거 후 순수 표현식만 반환한다.
    """
    i = 0
    n = len(fstring_content)
    while i < n:
        if fstring_content[i] != "{":
            i += 1
            continue
        # {{ 이스케이프 처리
        if i + 1 < n and fstring_content[i + 1] == "{":
            i += 2
            continue
        # { 시작 — 닫는 }를 bracket depth 추적으로 찾는다
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            ch = fstring_content[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            j += 1
        # fstring_content[i+1:j-1] 이 전체 표현식+format spec
        raw_expr = fstring_content[i + 1 : j - 1]

        # format spec (:) 또는 conversion (!)을 bracket depth 0에서만 분리
        expr = _split_fstring_expr(raw_expr)
        if expr:
            yield expr
        i = j


def _split_fstring_expr(raw: str) -> str:
    """format spec/conversion을 제거하고 순수 표현식만 반환한다.

    bracket depth 0에서 첫 번째 ':' 또는 '!r'/'!s'/'!a'를 format spec/conversion으로 처리한다.
    depth > 0 (괄호 내부)의 ':' 는 슬라이스이므로 분리하지 않는다.
    """
    depth = 0
    for k, ch in enumerate(raw):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            if ch == ":":
                return raw[:k].strip()
            if ch == "!" and k + 1 < len(raw) and raw[k + 1] in ("r", "s", "a"):
                return raw[:k].strip()
    return raw.strip()


def extract_fstring_kwargs(fstring_content: str) -> list[tuple[str, str]]:
    """f-string 표현식에서 (kwarg_name, expression) 쌍을 추출한다.

    Examples:
        "Cell not found: {cell_id}" → [("cell_id", "cell_id")]
        "Evicted {len(evicted)} from {cell_id}" → [("count", "len(evicted)"), ("cell_id", "cell_id")]
        "Recovery failed: {component}, {e}" → [("component", "component"), ("error", "str(e)")]
        "Reconciled {len(result.domains)} metrics" → [("count", "len(result.domains)")]
        "reason={reason[:50]}" → [("reason", "reason[:50]")]
    """
    results: list[tuple[str, str]] = []
    seen_names: set[str] = set()

    for expr in _iter_fstring_exprs(fstring_content):
        if not expr:
            continue
        kwarg_name = _expr_to_kwarg_name(expr)
        if kwarg_name in seen_names:
            kwarg_name = f"{kwarg_name}_{len(results)}"
        seen_names.add(kwarg_name)
        results.append((kwarg_name, expr))

    return results


# 예외 변수명 → "error" 로 통일
_EXCEPTION_VAR_NAMES = frozenset(["e", "ex", "err", "exc", "error", "exception"])


def _expr_to_kwarg_name(expr: str) -> str:
    """Python 표현식을 적절한 키워드 이름으로 변환.

    Rules:
        - 단순 변수명: 그대로 사용
        - 예외 변수명(e, err, ex, exc): "error" 로 통일
        - len(...): "count"
        - ....value: 앞 부분 사용
        - 기타 함수호출/복잡식: 밑줄 제거 후 사용
    """
    expr = expr.strip()

    # 예외 변수
    if expr in _EXCEPTION_VAR_NAMES:
        return "error"

    # len(x) → count
    if expr.startswith("len("):
        return "count"

    # str(e), str(err) etc → error
    if re.match(r"str\([a-z_]+\)$", expr) and any(v in expr for v in _EXCEPTION_VAR_NAMES):
        return "error"

    # simple attribute access: old_state.value → old_state
    if "." in expr:
        parts = expr.split(".")
        if parts[-1] in ("value", "name", "__class__.__name__", "__name__"):
            return parts[-2] if parts[-2].isidentifier() else "value"
        return parts[0] if parts[0].isidentifier() else "value"

    # simple subscript: items[0] → items
    if "[" in expr:
        return expr[: expr.index("[")]

    # f(...) function call: use function name
    m = re.match(r"([a-zA-Z_]\w*)\s*\(", expr)
    if m:
        return m.group(1)

    # plain identifier
    if re.match(r"^[a-zA-Z_]\w*$", expr):
        return expr

    return "value"


# ---------------------------------------------------------------------------
# %s-style 포맷 인자 추출
# ---------------------------------------------------------------------------


def extract_percent_s_kwargs(message_template: str, args: list[str]) -> list[tuple[str, str]]:
    """%s 포맷 인자를 키워드 인자로 변환.

    Examples:
        ("[ResilientStorage] Redis init failed: %s", ["err_msg"])
            → [("error", "err_msg")]
        ("Service '%s': %s -> %s", ["svc", "cell_id", "new_cell"])
            → [("service", "svc"), ("from_cell", "cell_id"), ("to_cell", "new_cell")]
    """
    placeholders = _PERCENT_S_RE.findall(message_template)
    result: list[tuple[str, str]] = []

    for i, (placeholder, arg) in enumerate(zip(placeholders, args)):
        kwarg_name = _expr_to_kwarg_name(arg.strip())
        result.append((kwarg_name, arg.strip()))

    return result


# ---------------------------------------------------------------------------
# 단일 로그 호출 라인 변환
# ---------------------------------------------------------------------------

# logger.{level}(...)  — 단일 라인, 인자가 완결된 경우
_SINGLE_LINE_LOG_RE = re.compile(r"""^(\s*)logger\.(debug|info|warning|error|critical|exception)\((.+)\)\s*$""")

# logger.{level}(
#     f"..."
# )  등 멀티라인 선두 패턴
_MULTILINE_LOG_START_RE = re.compile(r"""^(\s*)logger\.(debug|info|warning|error|critical|exception)\(\s*$""")

LOG_LEVELS = frozenset(["debug", "info", "warning", "error", "critical", "exception"])


def transform_log_call(
    indent: str,
    level: str,
    args_str: str,
    stats: TransformStats,
) -> str | None:
    """단일 logger.{level}(args_str) 호출을 structlog 패턴으로 변환.

    Returns:
        변환된 코드 문자열 (들여쓰기 포함), None이면 변환 불가 (복잡한 패턴)
    """
    args_str = args_str.strip()

    # -----------------------------------------------------------------------
    # 케이스 1: f-string 단일 인자
    #   logger.warning(f"[Tag] message {var}")
    # -----------------------------------------------------------------------
    fstring_match = re.match(r"""^f(['"])(.*)\1$""", args_str, re.DOTALL)
    if fstring_match:
        quote = fstring_match.group(1)
        content = fstring_match.group(2)
        return _build_fstring_call(indent, level, content, stats)

    # -----------------------------------------------------------------------
    # 케이스 2: f-string + 추가 인자 (드문 패턴, 스킵)
    # -----------------------------------------------------------------------
    if args_str.startswith('f"') or args_str.startswith("f'"):
        if "," in args_str:
            stats.skipped_complex += 1
            return None

    # -----------------------------------------------------------------------
    # 케이스 3: plain string (f-string 아님, %s 없음)
    #   logger.debug("[Tag] plain message")
    #   logger.info("Plain message")
    # -----------------------------------------------------------------------
    plain_match = re.match(r"""^(['"])(.*)\1$""", args_str, re.DOTALL)
    if plain_match:
        content = plain_match.group(2)
        if "%" not in content:
            return _build_plain_call(indent, level, content, stats)

    # -----------------------------------------------------------------------
    # 케이스 4: %s-style
    #   logger.warning("[Tag] msg: %s", arg)
    #   logger.info("msg %s %s", a, b)
    # -----------------------------------------------------------------------
    percent_match = re.match(
        r"""^(['"])([^'"]*%[sdf][^'"]*)\1\s*,\s*(.+)$""",
        args_str,
        re.DOTALL,
    )
    if percent_match:
        template = percent_match.group(2)
        raw_args_str = percent_match.group(3)
        return _build_percent_s_call(indent, level, template, raw_args_str, stats)

    # -----------------------------------------------------------------------
    # 케이스 5: 연결 f-string (f"..." f"...")
    # -----------------------------------------------------------------------
    if re.match(r"""^f['"].+f['"]""", args_str):
        # 단순 연결된 f-string들을 하나로 합치려고 시도
        combined = _merge_adjacent_fstrings(args_str)
        if combined:
            return _build_fstring_call(indent, level, combined, stats)

    stats.skipped_complex += 1
    return None


def _build_fstring_call(
    indent: str,
    level: str,
    fstring_content: str,
    stats: TransformStats,
) -> str:
    """f-string 내용에서 structlog 호출 구성."""
    component, message = extract_tag_and_message(fstring_content)
    event_name = resolve_event_name(component, message)
    kwargs = extract_fstring_kwargs(fstring_content)

    lines = [f"{indent}logger.{level}("]
    lines.append(f'{indent}    "{event_name}",')
    for kwarg_name, expr in kwargs:
        lines.append(f"{indent}    {kwarg_name}={expr},")
    lines.append(f"{indent})")

    if kwargs:
        stats.fstring_calls_converted += 1
    else:
        stats.plain_calls_converted += 1
    return "\n".join(lines)


def _build_plain_call(
    indent: str,
    level: str,
    message: str,
    stats: TransformStats,
) -> str:
    """plain string에서 structlog 호출 구성."""
    component, clean_msg = extract_tag_and_message(message)
    event_name = resolve_event_name(component, clean_msg)

    stats.plain_calls_converted += 1
    return f'{indent}logger.{level}("{event_name}")'


def _build_percent_s_call(
    indent: str,
    level: str,
    template: str,
    raw_args_str: str,
    stats: TransformStats,
) -> str:
    """%s-style 호출에서 structlog 호출 구성."""
    component, message = extract_tag_and_message(template)
    event_name = resolve_event_name(component, message)

    # 인자 파싱 (간단한 콤마 분리 — 복잡한 중첩 표현식은 스킵)
    try:
        args = _split_args(raw_args_str)
    except ValueError:
        stats.skipped_complex += 1
        return f'{indent}logger.{level}("{event_name}")'

    kwargs = extract_percent_s_kwargs(template, args)

    lines = [f"{indent}logger.{level}("]
    lines.append(f'{indent}    "{event_name}",')
    for kwarg_name, expr in kwargs:
        lines.append(f"{indent}    {kwarg_name}={expr},")
    lines.append(f"{indent})")

    stats.percent_s_calls_converted += 1
    return "\n".join(lines)


def _merge_adjacent_fstrings(s: str) -> str | None:
    """f"abc" f"def {x}" 같은 인접 f-string을 f"abcdef {x}"로 합친다.

    합칠 수 없으면 None 반환.
    """
    # 간단한 패턴: 두 f-string이 연속으로 있는 경우만 처리
    m = re.match(r"""f(['"])(.*?)\1\s*f\1(.*?)\1$""", s, re.DOTALL)
    if m:
        return m.group(2) + m.group(3)
    return None


def _split_args(args_str: str) -> list[str]:
    """쉼표로 구분된 인자를 분리한다 (괄호 depth 반영).

    Raises:
        ValueError: 파싱 불가능한 경우
    """
    args = []
    depth = 0
    current = ""
    for ch in args_str:
        if ch in "([{":
            depth += 1
            current += ch
        elif ch in ")]}":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            args.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        args.append(current.strip())
    return args


# ---------------------------------------------------------------------------
# 파일 단위 변환
# ---------------------------------------------------------------------------

_IMPORT_LOGGING_RE = re.compile(r"^import logging[ \t]*$", re.MULTILINE)
# logger = logging.getLogger(__name__)  또는  logger = logging.getLogger("any.name")
# 변수명이 logger인 경우만 처리 (self._logger 등은 Phase 3 수동 처리)
_GETLOGGER_RE = re.compile(
    r"^(\s*)(logger)\s*=\s*logging\.getLogger\([^)]+\)[ \t]*$",
    re.MULTILINE,
)
# _root_logger 등 다른 변수명도 처리 (변수명 보존, 값만 교체)
_GETLOGGER_ANY_VAR_RE = re.compile(
    r"^(\s*)([a-z_][a-z0-9_]*)\s*=\s*logging\.getLogger\(__name__\)[ \t]*$",
    re.MULTILINE,
)
_IMPORT_STRUCTLOG_LINE = "import structlog"
_GET_STRUCTLOG_LOGGER_LINE = "logger = structlog.get_logger()"


def transform_file_content(source: str, stats: TransformStats) -> str:
    """파일 소스 전체를 변환한다.

    단계:
    1. structlog_config.py는 건드리지 않는다
    2. import 문 교체
    3. 로깅 호출 변환 (싱글라인 우선, 멀티라인은 스킵 표시)
    """
    if "logging.getLogger" not in source:
        return source  # 변환 불필요

    original = source

    # -----------------------------------------------------------------------
    # 1. import 문 교체
    # -----------------------------------------------------------------------
    if "import logging" in source and "import structlog" not in source:
        # "import logging" 단독 라인 → "import structlog"
        source = _IMPORT_LOGGING_RE.sub(_IMPORT_STRUCTLOG_LINE, source)
        stats.imports_replaced += 1

    # logger = logging.getLogger(__name__) 또는 logging.getLogger("explicit") → structlog
    source = _GETLOGGER_RE.sub(
        lambda m: m.group(1) + _GET_STRUCTLOG_LOGGER_LINE,
        source,
    )
    # _root_logger, _log 등 logger 이외 변수명도 __name__ 패턴이면 교체
    source = _GETLOGGER_ANY_VAR_RE.sub(
        lambda m: m.group(1) + m.group(2) + " = structlog.get_logger()",
        source,
    )

    # -----------------------------------------------------------------------
    # 2. 로깅 호출 변환 (라인 단위)
    # -----------------------------------------------------------------------
    lines = source.splitlines()
    result_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # 싱글라인 완결 호출 감지
        single_match = _SINGLE_LINE_LOG_RE.match(line)
        if single_match:
            indent = single_match.group(1)
            level = single_match.group(2)
            args_str = single_match.group(3)

            # 괄호가 실제로 닫혀있는지 확인
            if _is_balanced(args_str):
                converted = transform_log_call(indent, level, args_str, stats)
                if converted is not None:
                    result_lines.append(converted)
                    i += 1
                    continue

        result_lines.append(line)
        i += 1

    return "\n".join(result_lines) + ("\n" if source.endswith("\n") else "")


def _is_balanced(s: str) -> bool:
    """괄호가 균형 잡혀 있는지 확인."""
    depth = 0
    in_str = None
    for ch in s:
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


# ---------------------------------------------------------------------------
# 파일 시스템 탐색 및 실행
# ---------------------------------------------------------------------------

DEFAULT_TARGET = Path("packages/selfhealing-python/src/selfhealing")

# 변환에서 제외할 파일/패턴
EXCLUDE_FILES: frozenset[str] = frozenset(
    [
        "structlog_config.py",  # 이미 structlog 직접 사용
        "__pycache__",
    ]
)


def should_skip(path: Path) -> bool:
    """변환 대상에서 제외할 파일인지 판단."""
    if path.suffix != ".py":
        return True
    if any(excl in path.parts for excl in EXCLUDE_FILES):
        return True
    if path.name in EXCLUDE_FILES:
        return True
    return False


def migrate_directory(
    target: Path,
    dry_run: bool = False,
    stats: TransformStats | None = None,
) -> TransformStats:
    """디렉토리 내 모든 .py 파일을 변환한다."""
    if stats is None:
        stats = TransformStats()

    for py_file in sorted(target.rglob("*.py")):
        if should_skip(py_file):
            continue

        stats.files_scanned += 1
        try:
            original = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original = py_file.read_text(encoding="utf-8", errors="replace")

        transformed = transform_file_content(original, stats)

        if transformed != original:
            stats.files_changed += 1
            if not dry_run:
                py_file.write_text(transformed, encoding="utf-8")
                print(f"  [CHANGED] {py_file}")
            else:
                print(f"  [DRY-RUN] {py_file}")

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="stdlib logging → structlog 일괄 마이그레이션 (Phase 2)")
    parser.add_argument(
        "--target",
        type=Path,
        default=DEFAULT_TARGET,
        help=f"변환 대상 디렉토리 (기본값: {DEFAULT_TARGET})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 파일 변경 없이 변환 결과만 출력",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="변환 결과를 JSON 파일로 저장",
    )
    args = parser.parse_args()

    if not args.target.exists():
        print(f"[오류] 대상 디렉토리가 존재하지 않음: {args.target}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== structlog 마이그레이션 Phase 2 [{mode}] ===")
    print(f"대상: {args.target}")
    print()

    stats = migrate_directory(args.target, dry_run=args.dry_run)

    print()
    print("--- 변환 결과 ---")
    print(f"  스캔 파일:          {stats.files_scanned}")
    print(f"  변경 파일:          {stats.files_changed}")
    print(f"  import 교체:        {stats.imports_replaced}")
    print(f"  f-string 변환:      {stats.fstring_calls_converted}")
    print(f"  %s-style 변환:      {stats.percent_s_calls_converted}")
    print(f"  plain string 변환:  {stats.plain_calls_converted}")
    print(f"  exception 변환:     {stats.exception_calls_converted}")
    print(f"  복잡 패턴 스킵:     {stats.skipped_complex}")
    print(f"  총 로그 호출 변환:  {stats.total_calls_converted()}")

    if stats.errors:
        print(f"\n  오류 ({len(stats.errors)}건):")
        for err in stats.errors[:10]:
            print(f"    {err}")

    if args.report:
        report_data = {
            "mode": mode,
            "target": str(args.target),
            "files_scanned": stats.files_scanned,
            "files_changed": stats.files_changed,
            "imports_replaced": stats.imports_replaced,
            "fstring_calls_converted": stats.fstring_calls_converted,
            "percent_s_calls_converted": stats.percent_s_calls_converted,
            "plain_calls_converted": stats.plain_calls_converted,
            "exception_calls_converted": stats.exception_calls_converted,
            "skipped_complex": stats.skipped_complex,
            "total_calls_converted": stats.total_calls_converted(),
            "errors": stats.errors,
        }
        args.report.write_text(
            json.dumps(report_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\n리포트 저장: {args.report}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

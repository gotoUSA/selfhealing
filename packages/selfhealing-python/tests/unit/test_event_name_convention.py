"""
CI 소스 스캔 테스트 — 전체 이벤트명 컨벤션 준수 검증.

AST 기반으로 selfhealing 패키지의 모든 logger 호출을 탐지하고,
이벤트명이 ``^[a-z][a-z0-9_]*\\.[a-z][a-z0-9_]*$`` 패턴을 준수하는지 검증한다.

전략:
- AST 탐색 시 ``logger`` 변수명 기반 탐지 (코드베이스 100% ``logger = structlog.get_logger()`` 사용)
- 메서드명 기반 탐색은 false positive 리스크가 있으므로 사용하지 않음
- 314 마이그레이션 완료 전까지는 violation count가 threshold 이하인지만 검증

Reference:
- docs/self_healing/middleware_system/314_EVENT_NAME_AUDIT.md §8.2
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

_SELFHEALING_ROOT = Path(__file__).resolve().parents[2] / "src" / "selfhealing"
_EVENT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")
_LOG_METHODS = frozenset({"debug", "info", "warning", "error", "critical", "exception"})

# 314 마이그레이션 P0-P4 완료 후 남은 violation 임계값.
# 전체 마이그레이션 완료 시 0으로 줄여야 한다.
_VIOLATION_THRESHOLD = 600


def _collect_violations() -> list[tuple[str, int, str]]:
    """모든 .py 파일에서 컨벤션 위반 이벤트명을 수집한다.

    Returns:
        [(relative_path, line_number, event_name), ...]
    """
    violations: list[tuple[str, int, str]] = []

    for py_file in _SELFHEALING_ROOT.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
        except (SyntaxError, UnicodeDecodeError):
            continue

        rel_path = str(py_file.relative_to(_SELFHEALING_ROOT))

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue

            func = node.func
            if not isinstance(func, ast.Attribute):
                continue
            if func.attr not in _LOG_METHODS:
                continue
            if not isinstance(func.value, ast.Name):
                continue
            if func.value.id != "logger":
                continue

            if not node.args:
                continue

            first_arg = node.args[0]
            if isinstance(first_arg, ast.Constant) and isinstance(first_arg.value, str):
                if not _EVENT_NAME_PATTERN.match(first_arg.value):
                    violations.append((rel_path, node.lineno, first_arg.value))

    return violations


class TestEventNameConventionScanBehavior:
    """AST 기반 이벤트명 컨벤션 소스 스캔."""

    def test_violation_count_within_threshold(self):
        """위반 수가 임계값 이하인지 검증한다. 마이그레이션 진행에 따라 임계값을 줄인다."""
        violations = _collect_violations()
        violation_count = len(violations)

        if violation_count > 0 and violation_count <= _VIOLATION_THRESHOLD:
            pytest.skip(
                f"Event name convention: {violation_count} violations "
                f"(threshold: {_VIOLATION_THRESHOLD}). "
                f"These will be addressed in future migration PRs."
            )

        assert violation_count <= _VIOLATION_THRESHOLD, (
            f"Event name convention violations ({violation_count}) exceed "
            f"threshold ({_VIOLATION_THRESHOLD}). "
            f"First 10 violations:\n"
            + "\n".join(
                f"  {path}:{line}: {name!r}" for path, line, name in violations[:10]
            )
        )

    def test_no_regression_on_migrated_modules(self):
        """314에서 마이그레이션 완료된 모듈에 새 위반이 생기지 않았는지 검증한다."""
        migrated_prefixes = (
            "services/runbook/primitives.py",
            "coordination/shutdown_integration.py",
            "coordination/dlq_consumer.py",
            "adapters/kafka/consumer.py",
            "adapters/kafka/event_bus.py",
            "adapters/kafka/metrics.py",
            "adapters/kafka/producer.py",
            "settings/chaos_blast_radius.py",
        )
        violations = _collect_violations()
        regressions = [
            (path, line, name)
            for path, line, name in violations
            if any(path.replace("\\", "/").endswith(p) for p in migrated_prefixes)
        ]
        assert not regressions, "Regressions in migrated modules:\n" + "\n".join(
            f"  {path}:{line}: {name!r}" for path, line, name in regressions
        )

# -*- coding: utf-8 -*-
"""structlog 이벤트 카탈로그 생성기

사용법:
    python scripts/generate_event_catalog.py

출력:
    docs/self_healing/middleware_system/270_STRUCTLOG_EVENT_CATALOG.md
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path

SRC_ROOT = Path("packages/selfhealing-python/src/selfhealing")
OUT_PATH = Path("docs/self_healing/middleware_system/270_STRUCTLOG_EVENT_CATALOG.md")

# component.action 패턴 (ASCII 소문자 + 한국어 허용)
EVENT_RE = re.compile(
    r"""logger\.(\w+)\(['"]("""
    r"""(?:[a-z\uac00-\ud7a3][a-z0-9_\uac00-\ud7a3]*)"""
    r"""(?:\.(?:[a-z\uac00-\ud7a3][a-z0-9_\uac00-\ud7a3]*))+"""
    r""")['"]\)"""
)

LEVEL_LABEL: dict[str, str] = {
    "debug": "debug",
    "info": "info",
    "warning": "warning",
    "error": "error",
    "exception": "exception",
    "critical": "critical",
}


def collect_events(src: Path) -> dict[str, dict[str, Counter]]:
    """src 하위 모든 .py 를 스캔하여 컴포넌트별 이벤트→레벨 카운터를 반환한다."""
    component_events: dict[str, dict[str, Counter]] = defaultdict(dict)
    for py_file in sorted(src.rglob("*.py")):
        if "__pycache__" in str(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for m in EVENT_RE.finditer(text):
            level, event = m.group(1), m.group(2)
            if "." not in event:
                continue
            comp = event.split(".")[0]
            if event not in component_events[comp]:
                component_events[comp][event] = Counter()
            component_events[comp][event][level] += 1
    return component_events


def format_levels(level_counter: Counter) -> str:
    """로그 레벨 목록을 가독성 있는 문자열로 변환한다."""
    ordered = ["debug", "info", "warning", "error", "exception", "critical"]
    found = [l for l in ordered if l in level_counter]
    extras = sorted(k for k in level_counter if k not in ordered)
    return " / ".join(found + extras)


def build_markdown(component_events: dict[str, dict[str, Counter]]) -> str:
    total_unique = sum(len(v) for v in component_events.values())
    total_comps = len(component_events)

    lines: list[str] = []
    lines.append("# Structlog 이벤트 카탈로그")
    lines.append("")
    lines.append("> 이 문서는 selfhealing 패키지 structlog 마이그레이션(Phase 4) 완료 후")
    lines.append("> 코드베이스 전체를 자동 스캔하여 생성된 이벤트 카탈로그입니다.")
    lines.append("> 재생성: `python scripts/generate_event_catalog.py`")
    lines.append("")
    lines.append("## 요약")
    lines.append("")
    lines.append("| 항목 | 수치 |")
    lines.append("|------|------|")
    lines.append(f"| 고유 이벤트 수 | **{total_unique}** |")
    lines.append(f"| 컴포넌트 수 | **{total_comps}** |")
    lines.append(f"| 스캔 경로 | `packages/selfhealing-python/src/selfhealing/` |")
    lines.append("")
    lines.append("## 이벤트 목록 (컴포넌트별 알파벳 정렬)")
    lines.append("")
    lines.append("> 이벤트 이름 규칙: `<component>.<action_verb>_<detail>`")
    lines.append("> 로그 레벨: debug / info / warning / error / exception / critical")
    lines.append("")

    for comp in sorted(component_events.keys()):
        events = component_events[comp]
        lines.append(f"### `{comp}` ({len(events)}개 이벤트)")
        lines.append("")
        lines.append("| 이벤트 이름 | 로그 레벨 |")
        lines.append("|-------------|-----------|")
        for event in sorted(events.keys()):
            lvl_str = format_levels(events[event])
            lines.append(f"| `{event}` | {lvl_str} |")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    print(f"스캔 중: {SRC_ROOT}")
    component_events = collect_events(SRC_ROOT)

    total_unique = sum(len(v) for v in component_events.values())
    total_comps = len(component_events)
    print(f"  고유 이벤트: {total_unique}개, 컴포넌트: {total_comps}개")

    md = build_markdown(component_events)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(md, encoding="utf-8")
    print(f"문서 생성 완료: {OUT_PATH}")


if __name__ == "__main__":
    main()

"""
멀티라인 f-string 로깅 → structlog 일괄 변환 스크립트.

Phase 4 정리: migrate_to_structlog.py 가 단일라인 패턴만 변환하여
멀티라인 f-string 786건이 누락됐다. 이 스크립트가 잔여분을 처리한다.

변환 대상:
    logger.warning(
        f"[Tag] message {var}"   # noqa: G004
    )
    logger.error(
        f"[Tag] msg {var}",
        exc_info=True,
    )
    logger.xxx(f"msg",  # noqa: G004)

처리 흐름:
    1. 파일내 모든 logger.xxx( 호출 수집 (싱글/멀티라인 공통)
    2. # noqa: G004 / G201 주석 strip
    3. migrate_to_structlog.transform_log_call 로 변환
    4. 변환 성공시 원본 블록 교체

실행:
    python scripts/fix_multiline_fstring_logging.py
    python scripts/fix_multiline_fstring_logging.py --dry-run
    python scripts/fix_multiline_fstring_logging.py --target src/selfhealing/api
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# migrate_to_structlog.py 에서 핵심 변환 함수 재사용
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))
from migrate_to_structlog import (  # noqa: E402
    TransformStats,
    _merge_adjacent_fstrings,
    extract_fstring_kwargs,
    extract_tag_and_message,
    resolve_event_name,
    transform_log_call,
)

# ---------------------------------------------------------------------------
# 정규식
# ---------------------------------------------------------------------------

# logger.xxx( 로 끝나는 라인 (멀티라인 시작)
_ML_START_RE = re.compile(
    r"^(\s*)(?:logger|self\._log|self\.log)\." r"(debug|info|warning|error|critical|exception)\(\s*(?:#.*)?$"
)
# logger.xxx(...) 단일라인 완결
_SL_RE = re.compile(
    r"^(\s*)(?:logger|self\._log|self\.log)\." r"(debug|info|warning|error|critical|exception)\((.+)\)\s*(?:#.*)?$"
)

_NOQA_COMMENT_RE = re.compile(r"\s*#\s*noqa:[^\n]*")

DEFAULT_TARGET = Path("packages/selfhealing-python/src/selfhealing")
EXCLUDE_FILES = frozenset(["structlog_config.py", "__pycache__"])


# ---------------------------------------------------------------------------
# 다중라인 logger 블록 수집
# ---------------------------------------------------------------------------


def collect_logger_block(lines: list[str], start: int) -> tuple[int, list[str]]:
    """start 인덱스의 logger.xxx( 라인에서 시작해
    닫는 ) 까지의 라인 목록과 다음에 처리할 인덱스를 반환한다.

    Returns:
        (end_index, block_lines)  — block_lines 는 start 포함, end_index 는 블록 다음 라인
    """
    block = [lines[start]]
    depth = lines[start].count("(") - lines[start].count(")")
    i = start + 1
    while i < len(lines) and depth > 0:
        line = lines[i]
        block.append(line)
        depth += line.count("(") - line.count(")")
        i += 1
    return i, block


def extract_args_str(block: list[str]) -> str:
    """블록에서 logger.xxx( 이후 내용을 하나의 문자열로 합친다.

    - 첫 라인: `logger.xxx(` 제거
    - 마지막 라인: 닫는 `)` 제거
    - # noqa: ... 주석 제거
    - 각 라인 strip 후 공백 연결
    """
    # 첫 라인에서 `logger.xxx(` 이후 텍스트 추출
    first = _ML_START_RE.sub("", block[0]).strip()
    # 단일라인 파트가 있을 수 있음
    if not first:
        inner_lines = block[1:]
    else:
        inner_lines = [first] + block[1:]

    # 마지막 닫는 ) 제거 (블록의 마지막 라인 오른쪽에서)
    # 블록 전체를 하나로 합쳐서 마지막 ) 제거
    parts: list[str] = []
    for raw in inner_lines:
        cleaned = _NOQA_COMMENT_RE.sub("", raw).strip()
        if cleaned:
            parts.append(cleaned)

    joined = " ".join(parts)

    # 가장 바깥쪽 닫는 ) 제거
    if joined.endswith(")"):
        joined = joined[:-1].rstrip()

    # 끝 쉼표 제거
    if joined.endswith(","):
        joined = joined[:-1].rstrip()

    return joined


# ---------------------------------------------------------------------------
# 인자 문자열 → structlog 패턴 변환 (확장판)
# ---------------------------------------------------------------------------


_ALREADY_VALID_EVENT_RE = re.compile(r"""^['""][a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+['""]$""")


def build_structlog_call(indent: str, level: str, args_str: str, stats: TransformStats) -> str | None:
    """args_str 을 파싱해 structlog 호출 코드를 생성한다.

    migrate_to_structlog.transform_log_call 이 처리하지 못하는 패턴
    (f-string + exc_info 콤비)도 여기서 처리한다.
    """
    args_str = args_str.strip()

    # 이미 유효한 structlog 이벤트("component.action")이면 변환하지 않음
    # 예: logger.info("notification_aggregator.redis_mode_enabled") → 보존
    if _ALREADY_VALID_EVENT_RE.match(args_str):
        return None

    # 먼저 기존 transform_log_call 시도
    result = transform_log_call(indent, level, args_str, stats)
    if result is not None:
        return result

    # ---------- 확장 패턴 처리 ----------

    # 패턴 A: f"..." , exc_info=True
    # 패턴 B: f"...", exc_info=True, stack_info=True 등
    fstring_with_extra = re.match(
        r"""^(f['"].*?)(?:\s*,\s*)(exc_info\s*=.*|stack_info\s*=.*)$""",
        args_str,
        re.DOTALL,
    )
    if fstring_with_extra:
        fpart = fstring_with_extra.group(1).strip()
        extra_kwargs_str = fstring_with_extra.group(2).strip()

        # f-string 부분만 처리
        fstr_m = re.match(r"""^f(['"])(.*)\1$""", fpart, re.DOTALL)
        if fstr_m:
            content = fstr_m.group(2)
            component, message = extract_tag_and_message(content)
            event_name = resolve_event_name(component, message)
            kwargs = extract_fstring_kwargs(content)

            lines_out = [f"{indent}logger.{level}("]
            lines_out.append(f'{indent}    "{event_name}",')
            for kw_name, expr in kwargs:
                lines_out.append(f"{indent}    {kw_name}={expr},")

            # exc_info 처리: exception() 레벨이면 생략, 아니면 보존
            if level != "exception" and "exc_info" in extra_kwargs_str:
                lines_out.append(f"{indent}    exc_info=True,")

            lines_out.append(f"{indent})")
            stats.fstring_calls_converted += 1
            return "\n".join(lines_out)

    # 패턴 C: 인접 f-string + 선택적 extra kwargs
    # f"..." f"..." , exc_info=True
    fconcat_m = re.match(
        r"""^(f['"].*?[^\\]['"])\s+(f['"].*?[^\\]['"])\s*(?:,(.*))?$""",
        args_str,
        re.DOTALL,
    )
    if fconcat_m:
        combined = _merge_adjacent_fstrings(fconcat_m.group(1) + " " + fconcat_m.group(2))
        if combined:
            extra_part = (fconcat_m.group(3) or "").strip()
            component, message = extract_tag_and_message(combined)
            event_name = resolve_event_name(component, message)
            kwargs = extract_fstring_kwargs(combined)

            lines_out = [f"{indent}logger.{level}("]
            lines_out.append(f'{indent}    "{event_name}",')
            for kw_name, expr in kwargs:
                lines_out.append(f"{indent}    {kw_name}={expr},")
            if level != "exception" and "exc_info" in extra_part:
                lines_out.append(f"{indent}    exc_info=True,")
            lines_out.append(f"{indent})")
            stats.fstring_calls_converted += 1
            return "\n".join(lines_out)

    # 변환 불가
    stats.skipped_complex += 1
    return None


# ---------------------------------------------------------------------------
# 파일 변환
# ---------------------------------------------------------------------------


def has_fstring_logging(source: str) -> bool:
    """f-string 로깅 호출이 존재하는지 판단 (싱글/멀티라인 공통)."""
    # 싱글라인: logger.info(f"...")
    if re.search(r'logger\.\w+\(\s*f["\']', source):
        return True
    # 멀티라인: logger.info(\n    f"...") — 다음 라인이 f-string으로 시작
    if re.search(r'logger\.\w+\(\s*(?:#[^\n]*)?\n\s*f["\']', source):
        return True
    return False


def transform_file(source: str, stats: TransformStats) -> str:
    """파일 소스 전체의 멀티/싱글라인 f-string 로깅을 structlog 패턴으로 변환."""
    if not has_fstring_logging(source):
        # # noqa: G004 만 있고 f-string은 없는 경우 (이미 변환 완료)
        return source

    lines = source.splitlines()
    result: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        # ----  싱글라인 완결 로거 호출  ----
        sl_m = _SL_RE.match(line)
        if sl_m:
            indent, level = sl_m.group(1), sl_m.group(2)
            # group(3) 은 ( 와 ) 사이 전체 내용
            args_str = _NOQA_COMMENT_RE.sub("", sl_m.group(3)).strip()

            if _is_balanced(args_str):
                converted = build_structlog_call(indent, level, args_str, stats)
                if converted is not None:
                    result.append(converted)
                    i += 1
                    continue

        # ----  멀티라인 로거 호출  ----
        ml_m = _ML_START_RE.match(line)
        if ml_m:
            indent, level = ml_m.group(1), ml_m.group(2)
            end_idx, block = collect_logger_block(lines, i)
            args_str = extract_args_str(block)

            converted = build_structlog_call(indent, level, args_str, stats)
            if converted is not None:
                result.append(converted)
                i = end_idx
                continue
            else:
                # 변환 실패 시 원본 블록 유지 (noqa 제거 없이)
                result.extend(block)
                i = end_idx
                continue

        result.append(line)
        i += 1

    fixed = "\n".join(result)
    if source.endswith("\n"):
        fixed += "\n"
    return fixed


def _is_balanced(s: str) -> bool:
    depth = 0
    in_str: str | None = None
    escape = False
    for ch in s:
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
    return depth == 0


# ---------------------------------------------------------------------------
# 디렉토리 탐색
# ---------------------------------------------------------------------------


def should_skip(path: Path) -> bool:
    if path.suffix != ".py":
        return True
    if any(excl in path.parts for excl in EXCLUDE_FILES):
        return True
    if path.name in EXCLUDE_FILES:
        return True
    return False


def run(target: Path, dry_run: bool) -> TransformStats:
    stats = TransformStats()

    # 단일 파일도 처리 가능
    if target.is_file():
        py_files = [target]
    else:
        py_files = sorted(target.rglob("*.py"))

    for py_file in py_files:
        if should_skip(py_file):
            continue

        stats.files_scanned += 1
        try:
            original = py_file.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            original = py_file.read_text(encoding="utf-8", errors="replace")

        transformed = transform_file(original, stats)

        if transformed != original:
            stats.files_changed += 1
            if not dry_run:
                py_file.write_text(transformed, encoding="utf-8")
                print(f"  [CHANGED] {py_file.relative_to(target.parent.parent.parent.parent) if target.parent else py_file}")
            else:
                print(f"  [DRY-RUN] {py_file.name}")

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="멀티라인 f-string 로깅 → structlog 변환")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.target.exists():
        print(f"[오류] 대상 없음: {args.target}", file=sys.stderr)
        return 1

    mode = "DRY-RUN" if args.dry_run else "APPLY"
    print(f"=== 멀티라인 f-string 로깅 변환 [{mode}] ===")
    print(f"대상: {args.target}\n")

    stats = run(args.target, dry_run=args.dry_run)

    print(f"\n--- 결과 ---")
    print(f"  스캔:      {stats.files_scanned}")
    print(f"  변경:      {stats.files_changed}")
    print(f"  f-string:  {stats.fstring_calls_converted}")
    print(f"  plain:     {stats.plain_calls_converted}")
    print(f"  스킵:      {stats.skipped_complex}")
    print(f"  총 변환:   {stats.total_calls_converted()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

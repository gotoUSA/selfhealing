"""
Load test 보고서 생성 모듈 패키지.

각 스테이지별 보고서 생성 로직을 분리하여 코드 중복을 줄입니다.
"""

from .platinum_report import (
    print_console_report,
    save_json_report,
    save_markdown_report,
    save_all_reports,
)

__all__ = [
    "print_console_report",
    "save_json_report",
    "save_markdown_report",
    "save_all_reports",
]

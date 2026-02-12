"""
Pre-flight Check for Self-Healing Configuration.

CI/CD 파이프라인에서 배포 전 설정 검증에 사용.

Usage:
    python manage.py check_selfhealing_config
    python manage.py check_selfhealing_config --strict  # Fatal 위반 시 exit 1
    python manage.py check_selfhealing_config --json    # JSON 출력

Exit Codes:
    0: 모든 설정 유효 (또는 non-fatal 경고만 있음)
    1: Fatal 설정 위반 감지 (--strict 모드)
    2: 실행 오류

Governance:
    - Phase 6: Fail-Safe Default
    - Fatal 설정: security, chaos, error_budget 관련 필수 설정
    - Non-fatal 설정: Safe Default로 대체 가능

Reference:
    docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
"""

import json
import sys
from typing import Any, Dict

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Self-Healing 설정 Pre-flight 검증 (CI/CD용)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--strict",
            action="store_true",
            help="Fatal 설정 위반 시 exit 1 반환 (CI/CD Hard Block)",
        )
        parser.add_argument(
            "--json",
            action="store_true",
            help="결과를 JSON 형식으로 출력",
        )

    def handle(self, *args, **options):
        strict_mode = options["strict"]
        json_output = options["json"]
        verbose = options.get("verbosity", 1)

        try:
            from selfhealing.core.safe_defaults import (
                validate_config_preflight,
                get_all_fatal_configs,
                FATAL_CONFIGS,
            )
            from selfhealing.settings import get_config
        except ImportError as e:
            if json_output:
                self._output_json(
                    {
                        "status": "error",
                        "message": f"Failed to import selfhealing modules: {e}",
                    }
                )
            else:
                self.stderr.write(self.style.ERROR(f"❌ Failed to import selfhealing modules: {e}"))
            sys.exit(2)

        try:
            config = get_config()
            result = validate_config_preflight(config)
        except Exception as e:
            if json_output:
                self._output_json(
                    {
                        "status": "error",
                        "message": f"Failed to validate config: {e}",
                    }
                )
            else:
                self.stderr.write(self.style.ERROR(f"❌ Failed to validate config: {e}"))
            sys.exit(2)

        output_data = {
            "status": "valid" if result.is_valid else "invalid",
            "fatal_violations": result.fatal_violations,
            "non_fatal_warnings": result.non_fatal_warnings,
            "fatal_violation_count": sum(len(keys) for keys in result.fatal_violations.values()),
            "warning_count": sum(len(keys) for keys in result.non_fatal_warnings.values()),
        }

        if json_output:
            self._output_json(output_data)
        else:
            self._output_text(output_data, verbose, FATAL_CONFIGS)

        if result.has_fatal_violations and strict_mode:
            sys.exit(1)
        else:
            sys.exit(0)

    def _output_json(self, data: Dict[str, Any]):
        """JSON 형식으로 출력."""
        self.stdout.write(json.dumps(data, indent=2, ensure_ascii=False))

    def _output_text(self, data: Dict[str, Any], verbose: int, fatal_configs: Dict[str, set]):
        """텍스트 형식으로 출력."""
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write(self.style.HTTP_INFO("  Self-Healing Configuration Pre-flight Check"))
        self.stdout.write("=" * 60)
        self.stdout.write("")

        if data["fatal_violations"]:
            self.stdout.write(self.style.ERROR(f"❌ FATAL VIOLATIONS ({data['fatal_violation_count']})"))
            self.stdout.write(self.style.WARNING("   These MUST be fixed before deployment!"))
            self.stdout.write("")

            for config_type, violations in data["fatal_violations"].items():
                self.stdout.write(f"   [{config_type}]")
                for key, msg in violations.items():
                    self.stdout.write(f"      • {key}: {msg}")
            self.stdout.write("")

        if data["non_fatal_warnings"]:
            self.stdout.write(self.style.WARNING(f"⚠️  NON-FATAL WARNINGS ({data['warning_count']})"))
            self.stdout.write("   These will be replaced with Safe Defaults at runtime.")
            self.stdout.write("")

            if verbose >= 1:
                for config_type, warnings in data["non_fatal_warnings"].items():
                    self.stdout.write(f"   [{config_type}]")
                    for key, msg in warnings.items():
                        self.stdout.write(f"      • {key}: {msg}")
                self.stdout.write("")

        self.stdout.write("-" * 60)

        if data["status"] == "valid":
            if data["warning_count"] > 0:
                self.stdout.write(self.style.WARNING(f"⚠️  Config valid with {data['warning_count']} warning(s)"))
            else:
                self.stdout.write(self.style.SUCCESS("✅ All configurations are valid!"))
        else:
            self.stdout.write(self.style.ERROR(f"❌ FAILED: {data['fatal_violation_count']} fatal violation(s) detected"))
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("   → Use --strict flag to block CI/CD pipeline"))
            self.stdout.write(self.style.WARNING("   → Fix fatal violations before deployment"))

        self.stdout.write("")

        if verbose >= 2:
            self.stdout.write("-" * 60)
            self.stdout.write(self.style.HTTP_INFO("Fatal Configuration Keys:"))
            for config_type, keys in fatal_configs.items():
                self.stdout.write(f"   {config_type}: {', '.join(sorted(keys))}")
            self.stdout.write("")

"""
Self-Healing 설정 검사 CLI.

Pydantic Settings 기반 설정의 자가 문서화 및 검사 도구.

Usage:
    python manage.py selfhealing_config --inspect
    python manage.py selfhealing_config --inspect --format json
    python manage.py selfhealing_config --validate
    python manage.py selfhealing_config --export > config_schema.json
    python manage.py selfhealing_config --sources

Reference: docs/self_healing/middleware_system/40_PYDANTIC_CONFIG_MIGRATION.md §8.5
"""

import json
from typing import Any

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Self-Healing Pydantic 설정 검사 및 문서화"

    def add_arguments(self, parser):
        parser.add_argument(
            "--inspect",
            action="store_true",
            help="현재 로드된 모든 설정 출력",
        )
        parser.add_argument(
            "--format",
            choices=["text", "json", "table"],
            default="text",
            help="출력 형식 (기본: text)",
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="설정 유효성 검증",
        )
        parser.add_argument(
            "--export",
            action="store_true",
            help="JSON Schema 내보내기 (stdout)",
        )
        parser.add_argument(
            "--sources",
            action="store_true",
            help="각 설정값의 출처(ENV/DEFAULT/RUNTIME) 표시",
        )
        parser.add_argument(
            "--config-type",
            type=str,
            default=None,
            help="특정 설정 타입만 검사 (e.g., circuit_breaker, retry)",
        )
        parser.add_argument(
            "--secrets",
            action="store_true",
            help="SecretsSettings 상태 확인 (값은 마스킹됨)",
        )

    def handle(self, *args, **options):
        if options["inspect"]:
            self._inspect_config(options["format"], options.get("config_type"))
        elif options["validate"]:
            self._validate_config(options.get("config_type"))
        elif options["export"]:
            self._export_schema(options.get("config_type"))
        elif options["sources"]:
            self._show_sources(options.get("config_type"))
        elif options["secrets"]:
            self._show_secrets()
        else:
            self.stdout.write(
                self.style.WARNING("옵션을 지정해주세요. --help로 사용법을 확인하세요.")
            )

    def _get_root_settings(self):
        """SelfHealingSettings 로드."""
        try:
            from selfhealing.settings import get_config

            return get_config()
        except ImportError as e:
            raise CommandError(f"selfhealing.settings를 로드할 수 없습니다: {e}")

    def _inspect_config(self, fmt: str, config_type: str = None):
        """모든 설정값 출력."""
        config = self._get_root_settings()

        if config_type:
            if not hasattr(config, config_type):
                raise CommandError(f"알 수 없는 설정 타입: {config_type}")
            sub_config = getattr(config, config_type)
            data = self._model_to_dict(sub_config)
            self._output_data({config_type: data}, fmt)
        else:
            data = self._model_to_dict(config)
            self._output_data(data, fmt)

    def _model_to_dict(self, model) -> dict[str, Any]:
        """Pydantic 모델을 dict로 변환 (중첩 처리).

        Root settings uses to_full_dict() to include cached_property groups.
        """
        if hasattr(model, "to_full_dict"):
            return model.to_full_dict()
        if hasattr(model, "model_dump"):
            return model.model_dump()
        return dict(model)

    def _output_data(self, data: dict[str, Any], fmt: str):
        """포맷에 따라 출력."""
        if fmt == "json":
            self.stdout.write(
                json.dumps(data, indent=2, default=str, ensure_ascii=False)
            )
        elif fmt == "table":
            self._output_table(data)
        else:
            self._output_text(data)

    def _output_text(self, data: dict[str, Any], prefix: str = ""):
        """텍스트 형식 출력."""
        for key, value in data.items():
            full_key = f"{prefix}{key}" if prefix else key
            if isinstance(value, dict):
                self.stdout.write(self.style.SUCCESS(f"\n[{full_key}]"))
                self._output_text(value, prefix="  ")
            else:
                self.stdout.write(f"  {key}: {value}")

    def _output_table(self, data: dict[str, Any], prefix: str = ""):
        """테이블 형식 출력."""
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(f"{'Key':<40} {'Value':<30}")
        self.stdout.write("=" * 70)
        self._output_table_rows(data, prefix)

    def _output_table_rows(self, data: dict[str, Any], prefix: str = ""):
        """테이블 행 출력 (재귀)."""
        for key, value in data.items():
            full_key = f"{prefix}{key}"
            if isinstance(value, dict):
                self.stdout.write(self.style.WARNING(f"\n[{full_key}]"))
                self._output_table_rows(value, prefix=f"{full_key}.")
            else:
                value_str = str(value)
                if len(value_str) > 30:
                    value_str = value_str[:27] + "..."
                self.stdout.write(f"{full_key:<40} {value_str:<30}")

    def _validate_config(self, config_type: str = None):
        """설정 유효성 검증."""
        self.stdout.write(self.style.SUCCESS("=== Self-Healing 설정 검증 ===\n"))

        try:
            config = self._get_root_settings()

            errors = []
            warnings = []

            if not config.circuit_breaker.enabled:
                warnings.append("Circuit Breaker가 비활성화되어 있습니다")

            if config.circuit_breaker.failure_threshold > 50:
                warnings.append(
                    f"failure_threshold({config.circuit_breaker.failure_threshold})가 "
                    "높습니다. 50 이하를 권장합니다."
                )

            if config.retry.max_retries > 10:
                warnings.append(
                    f"max_retries({config.retry.max_retries})가 높습니다. "
                    "10 이하를 권장합니다."
                )

            if config.dlq.max_items > 50000:
                warnings.append(
                    f"DLQ max_items({config.dlq.max_items})가 매우 높습니다."
                )

            if errors:
                for err in errors:
                    self.stdout.write(self.style.ERROR(f"❌ ERROR: {err}"))

            if warnings:
                for warn in warnings:
                    self.stdout.write(self.style.WARNING(f"⚠️  WARNING: {warn}"))

            if not errors and not warnings:
                self.stdout.write(self.style.SUCCESS("✅ 모든 설정이 정상입니다."))
            elif not errors:
                self.stdout.write(
                    self.style.SUCCESS(f"\n✅ 검증 완료 (경고 {len(warnings)}개)")
                )
            else:
                raise CommandError(f"검증 실패: {len(errors)}개 오류")

        except ImportError as e:
            raise CommandError(f"설정을 로드할 수 없습니다: {e}")

    def _export_schema(self, config_type: str = None):
        """JSON Schema 내보내기."""
        try:
            from selfhealing.settings.root import SelfHealingSettings

            if config_type:
                settings_map = self._get_settings_map()
                if config_type not in settings_map:
                    raise CommandError(f"알 수 없는 설정 타입: {config_type}")
                schema = settings_map[config_type].model_json_schema()
            else:
                schema = SelfHealingSettings.model_json_schema()

            self.stdout.write(json.dumps(schema, indent=2, ensure_ascii=False))

        except ImportError as e:
            raise CommandError(f"설정 모듈을 로드할 수 없습니다: {e}")

    def _get_settings_map(self) -> dict[str, type]:
        """설정 타입 → 클래스 매핑."""
        from selfhealing.settings import (
            ChaosSettings,
            CircuitBreakerSettings,
            DLQSettings,
            DriftThresholdSettings,
            ErrorBudgetSettings,
            ForensicSettings,
            GovernanceSettings,
            IdempotencySettings,
            L2StorageSettings,
            LoggingSettings,
            MetricsSettings,
            NotificationSettings,
            RateLimitSettings,
            RetrySettings,
            SecuritySettings,
            SLASettings,
        )

        return {
            "circuit_breaker": CircuitBreakerSettings,
            "dlq": DLQSettings,
            "retry": RetrySettings,
            "rate_limit": RateLimitSettings,
            "security": SecuritySettings,
            "sla": SLASettings,
            "idempotency": IdempotencySettings,
            "forensic": ForensicSettings,
            "metrics": MetricsSettings,
            "notification": NotificationSettings,
            "governance": GovernanceSettings,
            "error_budget": ErrorBudgetSettings,
            "chaos": ChaosSettings,
            "drift_threshold": DriftThresholdSettings,
            "l2_storage": L2StorageSettings,
            "logging": LoggingSettings,
        }

    def _show_sources(self, config_type: str = None):
        """각 설정값의 출처 표시."""
        self.stdout.write(self.style.SUCCESS("=== 설정값 출처 분석 ===\n"))

        try:
            from selfhealing.settings.layered_provider import (
                get_config_with_sources,
            )

            settings_map = self._get_settings_map()
            types_to_check = [config_type] if config_type else list(settings_map.keys())

            for ct in types_to_check:
                if ct not in settings_map:
                    self.stdout.write(self.style.ERROR(f"알 수 없는 타입: {ct}"))
                    continue

                self.stdout.write(self.style.SUCCESS(f"\n[{ct}]"))
                try:
                    info = get_config_with_sources(settings_map[ct], ct)
                    for field_name, details in info.items():
                        source = details["source"]
                        value = details["value"]

                        if source == "ENV":
                            style = self.style.WARNING
                        elif source == "RUNTIME":
                            style = self.style.NOTICE
                        elif source == "REQUEST":
                            style = self.style.ERROR
                        else:
                            style = lambda x: x

                        value_str = str(value)
                        if len(value_str) > 40:
                            value_str = value_str[:37] + "..."

                        self.stdout.write(
                            f"  {field_name:<30} {value_str:<40} [{style(source)}]"
                        )
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  분석 실패: {e}"))

        except ImportError as e:
            raise CommandError(f"layered_provider를 로드할 수 없습니다: {e}")

    def _show_secrets(self):
        """SecretsSettings 상태 표시 (값은 마스킹)."""
        self.stdout.write(self.style.SUCCESS("=== Secrets 설정 상태 ===\n"))

        try:
            from selfhealing.settings.secrets import get_secrets

            secrets = get_secrets()
            summary = secrets.get_masked_summary()

            for name, is_set in summary.items():
                if is_set:
                    self.stdout.write(
                        self.style.SUCCESS(f"  ✅ {name}: 설정됨 (마스킹됨)")
                    )
                else:
                    self.stdout.write(self.style.WARNING(f"  ⚠️  {name}: 미설정"))

            self.stdout.write(
                self.style.NOTICE("\n참고: 실제 값은 보안상 표시되지 않습니다.")
            )

        except ImportError as e:
            raise CommandError(f"secrets 모듈을 로드할 수 없습니다: {e}")

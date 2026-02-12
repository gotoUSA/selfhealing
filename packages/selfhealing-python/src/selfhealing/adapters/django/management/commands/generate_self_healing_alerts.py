"""
Generate Prometheus alerting rules from ALERTING_RULES constant.

This command ensures the Prometheus alert YAML file stays in sync with
the ALERTING_RULES defined in the metrics module, preventing drift
between code and configuration.

Usage:
    python manage.py generate_self_healing_alerts
    python manage.py generate_self_healing_alerts --output /path/to/alerts.yml
    python manage.py generate_self_healing_alerts --dry-run

Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
"""

import os
from pathlib import Path

import yaml
from django.conf import settings
from django.core.management.base import BaseCommand

from selfhealing.services import ALERTING_RULES
from selfhealing.services.metrics.registry import DEFAULT_DOMAINS


class Command(BaseCommand):
    help = "Generate Prometheus alerting rules YAML from ALERTING_RULES constant"

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Output file path. Defaults to scripts/prometheus/self_healing_alerts.yml",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the generated YAML without writing to file.",
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="Validate existing file matches ALERTING_RULES.",
        )

    def handle(self, *args, **options):
        output_path = options["output"]
        dry_run = options["dry_run"]
        validate = options["validate"]

        if output_path is None:
            base_dir = getattr(settings, "BASE_DIR", Path.cwd())
            output_path = os.path.join(base_dir, "scripts", "prometheus", "self_healing_alerts.yml")

        yaml_content = self._generate_yaml()

        if validate:
            self._validate_existing(output_path, yaml_content)
            return

        if dry_run:
            self.stdout.write(yaml_content)
            self.stdout.write(self.style.SUCCESS("\n--- Dry run complete. No file written. ---"))
            return

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(yaml_content)

        self.stdout.write(self.style.SUCCESS(f"Generated alerting rules: {output_path}"))
        self.stdout.write(f"  - Total rules: {len(ALERTING_RULES)}")
        self.stdout.write(f"  - Domains: {', '.join(DEFAULT_DOMAINS)}")

    def _generate_yaml(self) -> str:
        """Generate Prometheus alerting rules YAML from ALERTING_RULES."""
        groups = {
            "self_healing_dlq": [],
            "self_healing_retry": [],
            "self_healing_recovery": [],
            "self_healing_circuit_breaker": [],
            "self_healing_replay": [],
        }

        for rule_name, rule_config in ALERTING_RULES.items():
            if "DLQ" in rule_name:
                group_name = "self_healing_dlq"
            elif "Retry" in rule_name:
                group_name = "self_healing_retry"
            elif "Recovery" in rule_name or "SLA" in rule_name or "Human" in rule_name:
                group_name = "self_healing_recovery"
            elif "CircuitBreaker" in rule_name:
                group_name = "self_healing_circuit_breaker"
            elif "Replay" in rule_name:
                group_name = "self_healing_replay"
            else:
                group_name = "self_healing_dlq"

            alert_rule = {
                "alert": rule_name,
                "expr": rule_config["expr"],
                "labels": {
                    "severity": rule_config["severity"],
                    "team": rule_config.get("team", "ops"),
                },
                "annotations": {
                    "summary": rule_config["summary"],
                    "description": rule_config["description"],
                },
            }

            if "for" in rule_config:
                alert_rule["for"] = rule_config["for"]

            if "runbook_url" in rule_config:
                alert_rule["annotations"]["runbook_url"] = rule_config["runbook_url"]

            groups[group_name].append(alert_rule)

        prometheus_groups = []
        for group_name, rules in groups.items():
            if rules:
                prometheus_groups.append(
                    {
                        "name": group_name,
                        "rules": rules,
                    }
                )

        output = {
            "groups": prometheus_groups,
        }

        header = """# L3 Self-Healing System Alerting Rules
# Auto-generated from ALERTING_RULES in selfhealing.services.metrics
# DO NOT EDIT MANUALLY - Run: python manage.py generate_self_healing_alerts
#
# Reference: docs/L3_SELF_HEALING_OPERATIONS.md §7 (Observability & Metrics)
# Domains: {domains}
# Generated rules: {count}

""".format(
            domains=", ".join(DEFAULT_DOMAINS), count=len(ALERTING_RULES)
        )

        yaml_output = yaml.dump(output, default_flow_style=False, sort_keys=False, allow_unicode=True)

        return header + yaml_output

    def _validate_existing(self, file_path: str, expected_content: str) -> None:
        """Validate that existing file matches expected content."""
        if not os.path.exists(file_path):
            self.stdout.write(self.style.ERROR(f"File not found: {file_path}"))
            self.stdout.write("Run without --validate to generate the file.")
            return

        with open(file_path, "r", encoding="utf-8") as f:
            existing_content = f.read()

        if existing_content.strip() == expected_content.strip():
            self.stdout.write(self.style.SUCCESS("✓ File is in sync with ALERTING_RULES"))
        else:
            self.stdout.write(self.style.WARNING("✗ File differs from ALERTING_RULES"))
            self.stdout.write("Run without --validate to regenerate the file.")
            self.stdout.write("\n--- Expected content ---\n")
            self.stdout.write(expected_content[:500] + "...")

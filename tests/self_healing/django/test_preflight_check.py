"""
Unit tests for check_selfhealing_config management command.

Pre-flight Check 명령어 테스트.
CI/CD 파이프라인에서 배포 전 설정 검증에 사용.
"""

import json
from io import StringIO
from unittest.mock import MagicMock



class TestCheckSelfhealingConfigCommand:
    """Test check_selfhealing_config management command."""

    def test_command_exists(self):
        """check_selfhealing_config 명령어가 존재해야 함."""
        from django.core.management import get_commands
        
        commands = get_commands()
        assert "check_selfhealing_config" in commands

    def test_command_help_text(self):
        """명령어 help가 올바르게 표시되어야 함."""
        from shopping.management.commands.check_selfhealing_config import Command
        
        cmd = Command()
        assert "Pre-flight" in cmd.help or "설정" in cmd.help

    def test_command_has_strict_option(self):
        """--strict 옵션이 존재해야 함."""
        from shopping.management.commands.check_selfhealing_config import Command
        import argparse
        
        cmd = Command()
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        
        # Check that --strict is a valid option
        args = parser.parse_args(["--strict"])
        assert args.strict is True

    def test_command_has_json_option(self):
        """--json 옵션이 존재해야 함."""
        from shopping.management.commands.check_selfhealing_config import Command
        import argparse
        
        cmd = Command()
        parser = argparse.ArgumentParser()
        cmd.add_arguments(parser)
        
        args = parser.parse_args(["--json"])
        assert args.json is True


class TestCheckSelfhealingConfigLogic:
    """Test command logic with mocked dependencies."""

    def test_output_json_format(self):
        """_output_json 메서드가 JSON 형식으로 출력해야 함."""
        from shopping.management.commands.check_selfhealing_config import Command
        
        cmd = Command()
        out = StringIO()
        cmd.stdout = out
        
        data = {"status": "valid", "fatal_violations": {}}
        cmd._output_json(data)
        
        output = out.getvalue()
        parsed = json.loads(output)
        assert parsed["status"] == "valid"

    def test_output_text_valid_config(self):
        """유효한 설정 시 성공 메시지 출력."""
        from shopping.management.commands.check_selfhealing_config import Command
        
        cmd = Command()
        out = StringIO()
        cmd.stdout = out
        cmd.style = MagicMock()
        cmd.style.SUCCESS = lambda x: f"[SUCCESS] {x}"
        cmd.style.ERROR = lambda x: f"[ERROR] {x}"
        cmd.style.WARNING = lambda x: f"[WARNING] {x}"
        cmd.style.HTTP_INFO = lambda x: f"[INFO] {x}"
        
        data = {
            "status": "valid",
            "fatal_violations": {},
            "non_fatal_warnings": {},
            "fatal_violation_count": 0,
            "warning_count": 0,
        }
        
        cmd._output_text(data, 1, {})
        output = out.getvalue()
        
        assert "valid" in output.lower() or "SUCCESS" in output

    def test_output_text_with_violations(self):
        """위반 사항 있을 때 에러 메시지 출력."""
        from shopping.management.commands.check_selfhealing_config import Command
        
        cmd = Command()
        out = StringIO()
        cmd.stdout = out
        cmd.style = MagicMock()
        cmd.style.SUCCESS = lambda x: f"[SUCCESS] {x}"
        cmd.style.ERROR = lambda x: f"[ERROR] {x}"
        cmd.style.WARNING = lambda x: f"[WARNING] {x}"
        cmd.style.HTTP_INFO = lambda x: f"[INFO] {x}"
        
        data = {
            "status": "invalid",
            "fatal_violations": {"security": {"key": "error"}},
            "non_fatal_warnings": {},
            "fatal_violation_count": 1,
            "warning_count": 0,
        }
        
        cmd._output_text(data, 1, {"security": {"key"}})
        output = out.getvalue()
        
        assert "FATAL" in output or "security" in output


class TestCheckSelfhealingConfigIntegration:
    """Integration tests that don't require DB."""

    def test_command_module_imports(self):
        """명령어 모듈이 정상적으로 import되어야 함."""
        from shopping.management.commands import check_selfhealing_config
        
        assert hasattr(check_selfhealing_config, "Command")

    def test_command_handle_method_exists(self):
        """handle 메서드가 존재해야 함."""
        from shopping.management.commands.check_selfhealing_config import Command
        
        cmd = Command()
        assert hasattr(cmd, "handle")
        assert callable(cmd.handle)

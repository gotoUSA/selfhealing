"""
Stage DNA Strict Mode 테스트
"""
import pytest
from load_tests.utils.selfhealing.stage_dna import (
    validate_stage_dna,
    StrictModeLevel,
    DNAValidationError,
    ValidationResult,
    StageType,
    ALL_AVAILABLE_MODULES,
)


class TestStrictModeLevel:
    """StrictModeLevel Enum 테스트"""
    
    def test_strict_mode_values(self):
        """모든 strict mode 값 확인"""
        assert StrictModeLevel.OFF.value == "off"
        assert StrictModeLevel.WARN.value == "warn"
        assert StrictModeLevel.BLOCK.value == "block"
        assert StrictModeLevel.FATAL.value == "fatal"
    
    def test_strict_mode_from_string(self):
        """문자열에서 StrictModeLevel 생성"""
        assert StrictModeLevel("off") == StrictModeLevel.OFF
        assert StrictModeLevel("warn") == StrictModeLevel.WARN
        assert StrictModeLevel("block") == StrictModeLevel.BLOCK
        assert StrictModeLevel("fatal") == StrictModeLevel.FATAL


class TestDNAValidationError:
    """DNAValidationError 예외 테스트"""
    
    def test_exception_message(self):
        """예외 메시지 포함 확인"""
        error = DNAValidationError("Test error message")
        assert "Test error message" in str(error)
    
    def test_exception_inherits_from_exception(self):
        """Exception 상속 확인"""
        assert issubclass(DNAValidationError, Exception)


class TestValidateStageDNAStrictModeOff:
    """strict_mode=OFF 테스트"""
    
    def test_unknown_module_off_mode_passes(self):
        """OFF 모드: unknown module이 있어도 통과"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health", "unknown_module"],
        }
        
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.OFF)
        
        # 경고만 추가되고 예외 없이 통과
        assert result.is_valid  # core 모듈은 있으므로 valid
        assert any("unknown_module" in w for w in result.warnings)
    
    def test_off_mode_no_exception(self):
        """OFF 모드: 예외 발생 안함"""
        stage_dna = {
            "name": "Test Stage",
            "type": "chaos",
            "required_modules": ["fake_module_1", "fake_module_2"],
        }
        
        # 예외 없이 실행됨
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.OFF)
        assert result is not None


class TestValidateStageDNAStrictModeWarn:
    """strict_mode=WARN 테스트"""
    
    def test_unknown_module_warn_mode_passes_with_warning(self):
        """WARN 모드: unknown module에 경고 로그"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health", "unknown_xyz"],
        }
        
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.WARN)
        
        assert result.is_valid
        assert any("unknown_xyz" in w for w in result.warnings)
        assert result.strict_mode == StrictModeLevel.WARN
    
    def test_warn_mode_default(self):
        """WARN 모드가 기본값임"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health"],
        }
        
        result = validate_stage_dna(stage_dna)
        assert result.strict_mode == StrictModeLevel.WARN


class TestValidateStageDNAStrictModeBlock:
    """strict_mode=BLOCK 테스트"""
    
    def test_unknown_module_block_mode_raises_exception(self):
        """BLOCK 모드: unknown module 있으면 DNAValidationError 발생"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "blocked_unknown_module"],
        }
        
        with pytest.raises(DNAValidationError) as exc_info:
            validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.BLOCK)
        
        assert "BLOCKED" in str(exc_info.value)
        assert "blocked_unknown_module" in str(exc_info.value)
        assert "해결 방법" in str(exc_info.value)
    
    def test_block_mode_no_unknown_module_passes(self):
        """BLOCK 모드: unknown module 없으면 정상 통과"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health"],
        }
        
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.BLOCK)
        
        assert result.is_valid
        assert not any("Unknown" in w for w in result.warnings)
    
    def test_block_mode_exception_contains_resolution_steps(self):
        """BLOCK 모드: 예외에 해결 방법 포함"""
        stage_dna = {
            "name": "Stage 99 - Unknown",
            "type": "integration",
            "required_modules": ["my_custom_module"],
        }
        
        with pytest.raises(DNAValidationError) as exc_info:
            validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.BLOCK)
        
        error_msg = str(exc_info.value)
        assert "27_SELFHEALING_SCENARIO_MAPPING" in error_msg
        assert "ALL_AVAILABLE_MODULES" in error_msg


class TestValidateStageDNAWithMultipleUnknownModules:
    """복수의 unknown module 테스트"""
    
    def test_multiple_unknown_modules_in_warning(self):
        """여러 unknown module이 경고에 포함됨"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["health", "unknown_a", "unknown_b", "unknown_c"],
        }
        
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.WARN)
        
        # 모든 unknown module이 경고에 포함
        warning_text = " ".join(result.warnings)
        assert "unknown_a" in warning_text
        assert "unknown_b" in warning_text
        assert "unknown_c" in warning_text
    
    def test_block_mode_with_multiple_unknown_modules(self):
        """BLOCK 모드: 여러 unknown module이 예외 메시지에 포함"""
        stage_dna = {
            "name": "Test Stage",
            "type": "chaos",
            "required_modules": ["health", "fake_1", "fake_2"],
        }
        
        with pytest.raises(DNAValidationError) as exc_info:
            validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.BLOCK)
        
        error_msg = str(exc_info.value)
        assert "fake_1" in error_msg
        assert "fake_2" in error_msg


class TestValidateStageDNAWithOptionalModules:
    """optional_modules의 unknown module 테스트"""
    
    def test_unknown_in_optional_modules_warns(self):
        """optional_modules의 unknown module도 경고"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health"],
            "optional_modules": ["unknown_optional"],
        }
        
        result = validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.WARN)
        
        assert any("unknown_optional" in w for w in result.warnings)
    
    def test_unknown_in_optional_block_mode_raises(self):
        """BLOCK 모드: optional_modules의 unknown도 예외 발생"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health"],
            "optional_modules": ["blocked_optional"],
        }
        
        with pytest.raises(DNAValidationError) as exc_info:
            validate_stage_dna(stage_dna, strict_mode=StrictModeLevel.BLOCK)
        
        assert "blocked_optional" in str(exc_info.value)


class TestValidationResultWithStrictMode:
    """ValidationResult에 strict_mode 포함 테스트"""
    
    def test_result_contains_strict_mode(self):
        """결과에 strict_mode 필드 포함"""
        stage_dna = {
            "name": "Test Stage",
            "type": "load",
            "required_modules": ["circuit_breaker", "error_budget", "health"],
        }
        
        for mode in StrictModeLevel:
            if mode == StrictModeLevel.FATAL:
                continue  # FATAL은 sys.exit 호출하므로 스킵
            if mode == StrictModeLevel.BLOCK:
                result = validate_stage_dna(stage_dna, strict_mode=mode)
            else:
                result = validate_stage_dna(stage_dna, strict_mode=mode)
            
            assert result.strict_mode == mode


class TestAllAvailableModules:
    """ALL_AVAILABLE_MODULES 검증"""
    
    def test_core_modules_in_available(self):
        """핵심 모듈들이 ALL_AVAILABLE_MODULES에 포함됨"""
        core_modules = [
            "circuit_breaker", "error_budget", "emergency", "dlq",
            "health", "auth", "config", "chaos", "xtest",
        ]
        
        for module in core_modules:
            assert module in ALL_AVAILABLE_MODULES, f"{module} not in ALL_AVAILABLE_MODULES"
    
    def test_observability_modules_in_available(self):
        """관측성 모듈들이 포함됨"""
        obs_modules = ["observability", "dashboard", "alerts", "async_logger"]
        
        for module in obs_modules:
            assert module in ALL_AVAILABLE_MODULES, f"{module} not in ALL_AVAILABLE_MODULES"

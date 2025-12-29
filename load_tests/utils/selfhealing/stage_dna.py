"""
Stage DNA - Self-Healing 모듈 의존성 선언 및 검증

각 Stage 파일 상단에 REQUIRED_MODULES를 선언하면,
테스트 실행 전 해당 Stage가 가이드라인의 필수 조건(Core)을 충족하는지 자동으로 체크합니다.

사용법:
    # Stage 파일 상단에 추가
    STAGE_DNA = {
        "name": "Stage 12 - Payment Load Test",
        "type": "load",  # smoke, load, chaos, integration, platinum
        "required_modules": ["circuit_breaker", "error_budget", "health"],
        "optional_modules": ["state_cache", "adaptive_jitter"],
    }
    
    # 검증 실행
    from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
    validate_stage_dna(STAGE_DNA)

참조 문서:
    - docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
    - docs/self_healing/28_HIDDEN_FEATURES_DISCOVERY.md
"""
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging
import sys

logger = logging.getLogger(__name__)


# =============================================================================
# Phase 1: Unknown Module Strict Mode
# Reference: docs/self_healing/30_DNA_DRIFT_DISCOVERY.md
# =============================================================================

class StrictModeLevel(Enum):
    """Strict 모드 레벨 (Unknown Module 처리 방식)"""
    OFF = "off"           # 경고만 (기존 동작)
    WARN = "warn"         # 경고 + 로그 기록
    BLOCK = "block"       # 경고 + 테스트 차단 (Exception)
    FATAL = "fatal"       # 경고 + 프로세스 종료


class DNAValidationError(Exception):
    """
    DNA 검증 실패 예외
    
    Strict Mode가 BLOCK일 때 발생합니다.
    해결 방법:
    1. 27번 매핑 가이드에 새 모듈 추가
    2. 해당 모듈의 테스트 케이스 작성
    3. ALL_AVAILABLE_MODULES에 등록
    """
    pass


class StageType(Enum):
    """시나리오 유형"""
    SMOKE = "smoke"
    LOAD = "load"
    CHAOS = "chaos"
    INTEGRATION = "integration"
    PLATINUM = "platinum"


@dataclass
class ValidationResult:
    """검증 결과"""
    is_valid: bool
    stage_name: str
    stage_type: StageType
    missing_core: List[str]
    missing_recommended: List[str]
    warnings: List[str]
    strict_mode: StrictModeLevel = field(default=StrictModeLevel.WARN)
    
    def __str__(self) -> str:
        if self.is_valid:
            status = "✅ PASS"
        else:
            status = "❌ FAIL"
        
        lines = [
            f"{status} - {self.stage_name} ({self.stage_type.value})",
        ]
        
        if self.missing_core:
            lines.append(f"  🔴 Missing Core Modules: {', '.join(self.missing_core)}")
        
        if self.missing_recommended:
            lines.append(f"  🟡 Missing Recommended: {', '.join(self.missing_recommended)}")
        
        if self.warnings:
            for warning in self.warnings:
                lines.append(f"  ⚠️ {warning}")
        
        return "\n".join(lines)


# =============================================================================
# 27번 문서 기반 - 시나리오 유형별 필수/권장 모듈 정의
# Reference: docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md
# =============================================================================

# Core 모듈 (모든 시나리오에 기본 적용)
UNIVERSAL_CORE_MODULES: Set[str] = {
    "health",
    "auth", 
    "config",
}

# 시나리오 유형별 필수 모듈
REQUIRED_BY_TYPE: Dict[StageType, Set[str]] = {
    StageType.SMOKE: {
        "health",
    },
    StageType.LOAD: {
        "circuit_breaker",
        "error_budget",
        "health",
    },
    StageType.CHAOS: {
        "circuit_breaker",
        "chaos",
        "xtest",
        "emergency",
        "dlq",
        "health",
    },
    StageType.INTEGRATION: {
        "circuit_breaker",
        "dlq",
        "observability",
        "health",
    },
    StageType.PLATINUM: {
        # Platinum은 전체 스택 필요
        "circuit_breaker",
        "error_budget",
        "emergency",
        "dlq",
        "chaos",
        "xtest",
        "observability",
        "health",
    },
}

# 시나리오 유형별 권장 모듈
RECOMMENDED_BY_TYPE: Dict[StageType, Set[str]] = {
    StageType.SMOKE: set(),
    StageType.LOAD: {
        "state_cache",
        "adaptive_jitter",
        "throttle",
        "rate_limiter",
    },
    StageType.CHAOS: {
        "observability",
        "corruption_shield",
    },
    StageType.INTEGRATION: {
        "reconciliation",
        "governance",
        "l2_storage",
    },
    StageType.PLATINUM: {
        "state_cache",
        "adaptive_jitter",
        "throttle",
        "corruption_shield",
        "reconciliation",
        "governance",
        "l2_storage",
        "runtime_config",
        "tiering",
    },
}

# 모든 사용 가능한 모듈 목록
ALL_AVAILABLE_MODULES: Set[str] = {
    # Core
    "circuit_breaker", "error_budget", "emergency", "dlq", 
    "health", "auth", "base", "config",
    # Observability
    "observability", "dashboard", "alerts", "async_logger",
    # Chaos
    "chaos", "xtest", "corruption_shield",
    # Optimization
    "state_cache", "adaptive_jitter", "rate_limiter", "throttle", "defaults",
    # Configuration
    "runtime_config", "system", "tiering",
    # Advanced
    "governance", "reconciliation", "l2_storage",
    # Controller
    "controller",
}


# =============================================================================
# V6: Stage DNA 자동 튜닝 설정 (Self-Tuning Infrastructure)
# 500M USD 가치 증명: "테스트 파일만 봐도 시스템이 자가 튜닝됩니다"
# =============================================================================

@dataclass
class StageTuningConfig:
    """Stage 유형별 자동 튜닝 설정"""
    auto_whitelist: bool = False  # Rate Limit 바이패스
    recovery_priority: str = "normal"  # normal, high, critical
    xtest_mode: bool = False  # X-Test-Mode 자동 활성화
    rate_limit_multiplier: float = 1.0  # Rate Limit 배율
    timeout_multiplier: float = 1.0  # Timeout 배율
    max_concurrent_requests: int = 100  # 최대 동시 요청
    chaos_safe_mode: bool = True  # Chaos 안전 모드
    gradient_throttle: bool = False  # Netflix Gradient 스로틀링


# 시나리오 유형별 자동 튜닝 설정
TUNING_BY_TYPE: Dict[StageType, StageTuningConfig] = {
    StageType.SMOKE: StageTuningConfig(
        auto_whitelist=False,
        recovery_priority="normal",
        xtest_mode=False,
        rate_limit_multiplier=1.0,
        max_concurrent_requests=10,
        chaos_safe_mode=True,
    ),
    StageType.LOAD: StageTuningConfig(
        auto_whitelist=False,
        recovery_priority="normal",
        xtest_mode=False,
        rate_limit_multiplier=2.0,  # 부하 테스트는 2배
        max_concurrent_requests=100,
        chaos_safe_mode=True,
        gradient_throttle=True,
    ),
    StageType.CHAOS: StageTuningConfig(
        auto_whitelist=True,  # Chaos는 Rate Limit 바이패스
        recovery_priority="high",
        xtest_mode=True,  # X-Test-Mode 자동 활성화
        rate_limit_multiplier=5.0,
        timeout_multiplier=2.0,  # 타임아웃 2배
        max_concurrent_requests=50,
        chaos_safe_mode=False,  # Chaos는 안전 모드 해제
    ),
    StageType.INTEGRATION: StageTuningConfig(
        auto_whitelist=True,  # Integration도 바이패스
        recovery_priority="high",
        xtest_mode=True,
        rate_limit_multiplier=3.0,
        max_concurrent_requests=50,
        gradient_throttle=True,
    ),
    StageType.PLATINUM: StageTuningConfig(
        auto_whitelist=True,  # Platinum은 무조건 바이패스
        recovery_priority="critical",  # 최고 우선순위
        xtest_mode=True,
        rate_limit_multiplier=10.0,  # 10배
        timeout_multiplier=3.0,
        max_concurrent_requests=200,
        chaos_safe_mode=False,
        gradient_throttle=True,
    ),
}


def get_tuning_config(stage_type: str) -> StageTuningConfig:
    """
    Stage 유형에 맞는 자동 튜닝 설정 반환
    
    Args:
        stage_type: 시나리오 유형 문자열
    
    Returns:
        StageTuningConfig: 튜닝 설정
    """
    try:
        st = StageType(stage_type.lower())
        return TUNING_BY_TYPE.get(st, StageTuningConfig())
    except ValueError:
        return StageTuningConfig()


def get_http_headers_for_stage(stage_dna: Dict[str, Any]) -> Dict[str, str]:
    """
    Stage DNA 기반 HTTP 헤더 자동 생성
    
    테스트 유형에 따라 Rate Limit 바이패스, 우선순위 등을
    자동으로 HTTP 헤더에 설정합니다.
    
    Args:
        stage_dna: Stage DNA 딕셔너리
    
    Returns:
        Dict[str, str]: HTTP 헤더
    """
    stage_type_str = stage_dna.get("type", "load")
    tuning = get_tuning_config(stage_type_str)
    extreme_config = stage_dna.get("extreme_config", {})
    
    headers = {}
    
    # X-Test-Mode 헤더
    if tuning.xtest_mode or extreme_config.get("auto_whitelist"):
        headers["X-Test-Mode"] = "true"
        headers["X-Test-Bypass-RateLimit"] = "true"
    
    # 우선순위 헤더
    headers["X-Recovery-Priority"] = extreme_config.get(
        "recovery_priority", tuning.recovery_priority
    )
    
    # Stage 식별 헤더
    headers["X-Stage-Name"] = stage_dna.get("name", "Unknown")
    headers["X-Stage-Type"] = stage_type_str
    
    # Rate Limit 배율 힌트
    headers["X-RateLimit-Multiplier"] = str(tuning.rate_limit_multiplier)
    
    return headers


def apply_stage_tuning(stage_dna: Dict[str, Any]) -> Dict[str, Any]:
    """
    Stage DNA에 자동 튜닝 설정 적용
    
    Stage DNA 딕셔너리를 받아 tuning_config를 추가하고,
    필요한 런타임 설정을 자동으로 생성합니다.
    
    Args:
        stage_dna: 원본 Stage DNA
    
    Returns:
        Dict[str, Any]: 튜닝이 적용된 Stage DNA
    """
    stage_type_str = stage_dna.get("type", "load")
    tuning = get_tuning_config(stage_type_str)
    
    # 튜닝 설정 병합
    enhanced_dna = stage_dna.copy()
    enhanced_dna["tuning_config"] = {
        "auto_whitelist": tuning.auto_whitelist,
        "recovery_priority": tuning.recovery_priority,
        "xtest_mode": tuning.xtest_mode,
        "rate_limit_multiplier": tuning.rate_limit_multiplier,
        "timeout_multiplier": tuning.timeout_multiplier,
        "max_concurrent_requests": tuning.max_concurrent_requests,
        "chaos_safe_mode": tuning.chaos_safe_mode,
        "gradient_throttle": tuning.gradient_throttle,
    }
    
    # extreme_config 병합 (있으면 우선)
    if "extreme_config" in stage_dna:
        for key, value in stage_dna["extreme_config"].items():
            if key in enhanced_dna["tuning_config"]:
                enhanced_dna["tuning_config"][key] = value
    
    # HTTP 헤더 자동 생성
    enhanced_dna["http_headers"] = get_http_headers_for_stage(enhanced_dna)
    
    return enhanced_dna


def validate_stage_dna(
    stage_dna: Dict[str, Any],
    strict: bool = False,
    auto_fix: bool = False,
    strict_mode: StrictModeLevel = StrictModeLevel.WARN,
) -> ValidationResult:
    """
    Stage DNA 검증 (Phase 1: Strict Mode 지원)
    
    Args:
        stage_dna: Stage DNA 딕셔너리
            - name: Stage 이름
            - type: 시나리오 유형 (smoke, load, chaos, integration, platinum)
            - required_modules: 선언된 필수 모듈
            - optional_modules: 선언된 선택 모듈 (옵션)
        strict: True이면 권장 모듈 누락도 실패로 처리
        auto_fix: True이면 누락된 Core 모듈 자동 추가 제안
        strict_mode: Unknown Module 처리 방식
            - OFF: 경고만 (기존)
            - WARN: 경고 + 로그 기록
            - BLOCK: 경고 + DNAValidationError 발생
            - FATAL: 경고 + 프로세스 종료
    
    Returns:
        ValidationResult: 검증 결과
    
    Raises:
        DNAValidationError: strict_mode가 BLOCK이고 unknown module이 있을 때
    """
    stage_name = stage_dna.get("name", "Unknown Stage")
    stage_type_str = stage_dna.get("type", "load")
    declared_modules = set(stage_dna.get("required_modules", []))
    optional_modules = set(stage_dna.get("optional_modules", []))
    
    # Stage 유형 파싱
    try:
        stage_type = StageType(stage_type_str.lower())
    except ValueError:
        stage_type = StageType.LOAD
    
    # 해당 유형의 필수/권장 모듈 조회
    required = REQUIRED_BY_TYPE.get(stage_type, set())
    recommended = RECOMMENDED_BY_TYPE.get(stage_type, set())
    
    # 검증
    all_declared = declared_modules | optional_modules
    missing_core = required - all_declared
    missing_recommended = recommended - all_declared
    
    warnings = []
    
    # 알 수 없는 모듈 경고 (Phase 1: Strict Mode 처리)
    unknown = all_declared - ALL_AVAILABLE_MODULES
    if unknown:
        message = f"Unknown modules: {', '.join(sorted(unknown))}"
        warnings.append(message)
        
        # Strict Mode 레벨에 따른 처리
        if strict_mode == StrictModeLevel.BLOCK:
            raise DNAValidationError(
                f"[BLOCKED] {message}\n\n"
                f"Stage: {stage_name}\n"
                f"해결 방법:\n"
                f"  1. docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md에 새 모듈 추가\n"
                f"  2. 해당 모듈의 테스트 케이스 작성\n"
                f"  3. stage_dna.py의 ALL_AVAILABLE_MODULES에 등록\n\n"
                f"또는 STAGE_DNA에서 해당 모듈을 제거하세요."
            )
        elif strict_mode == StrictModeLevel.FATAL:
            logger.critical(
                f"[FATAL] {message}\n"
                f"Stage: {stage_name}\n"
                f"프로세스를 종료합니다."
            )
            sys.exit(1)
        elif strict_mode == StrictModeLevel.WARN:
            logger.warning(
                f"[WARN] {message}\n"
                f"Stage: {stage_name}\n"
                f"테스트는 계속되지만 해당 모듈은 검증되지 않습니다."
            )
        # OFF는 warnings에 추가만 하고 아무 동작 안함
    
    # Platinum인데 controller 없으면 경고
    if stage_type == StageType.PLATINUM and "controller" not in all_declared:
        warnings.append("Platinum Stage에서는 SelfHealingController 사용 권장")
    
    # auto_fix 제안
    if auto_fix and missing_core:
        warnings.append(f"Add to required_modules: {list(missing_core)}")
    
    is_valid = len(missing_core) == 0
    if strict:
        is_valid = is_valid and len(missing_recommended) == 0
    
    return ValidationResult(
        is_valid=is_valid,
        stage_name=stage_name,
        stage_type=stage_type,
        missing_core=list(missing_core),
        missing_recommended=list(missing_recommended),
        warnings=warnings,
        strict_mode=strict_mode,
    )


def validate_stage_file(
    file_path: str,
    strict: bool = False,
    strict_mode: StrictModeLevel = StrictModeLevel.WARN,
) -> Optional[ValidationResult]:
    """
    Stage 파일에서 STAGE_DNA를 읽어 검증
    
    Args:
        file_path: Stage 파일 경로
        strict: 엄격 모드 여부
        strict_mode: Unknown Module 처리 방식
    
    Returns:
        ValidationResult 또는 None (STAGE_DNA 없는 경우)
    
    Raises:
        DNAValidationError: strict_mode가 BLOCK이고 unknown module이 있을 때
    """
    import ast
    
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        
        # AST로 STAGE_DNA 변수 찾기
        tree = ast.parse(content)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "STAGE_DNA":
                        # eval로 딕셔너리 추출 (안전한 리터럴만)
                        try:
                            stage_dna = ast.literal_eval(node.value)
                            return validate_stage_dna(
                                stage_dna, 
                                strict=strict,
                                strict_mode=strict_mode
                            )
                        except (ValueError, TypeError):
                            logger.warning(f"Failed to parse STAGE_DNA in {file_path}")
                            return None
        
        # STAGE_DNA 없음
        return None
        
    except Exception as e:
        logger.error(f"Error reading {file_path}: {e}")
        return None


def validate_all_stages(
    stages_dir: str = "load_tests/scenarios",
    strict: bool = False,
    verbose: bool = True,
    strict_mode: StrictModeLevel = StrictModeLevel.WARN,
) -> Dict[str, ValidationResult]:
    """
    모든 Stage 파일 검증
    
    Args:
        stages_dir: Stage 파일 디렉토리
        strict: 엄격 모드 (권장 모듈 누락도 실패)
        verbose: 상세 출력
        strict_mode: Unknown Module 처리 방식
    
    Returns:
        Dict[파일경로, ValidationResult]
    
    Raises:
        DNAValidationError: strict_mode가 BLOCK이고 unknown module이 있을 때
    """
    import os
    import glob
    
    results = {}
    pattern = os.path.join(stages_dir, "**", "stage*.py")
    
    for file_path in glob.glob(pattern, recursive=True):
        result = validate_stage_file(file_path, strict=strict, strict_mode=strict_mode)
        
        if result:
            results[file_path] = result
            if verbose:
                print(result)
                print()
    
    # 요약
    if verbose:
        total = len(results)
        passed = sum(1 for r in results.values() if r.is_valid)
        failed = total - passed
        
        print("=" * 60)
        print(f"Stage DNA Validation Summary")
        print(f"  Total: {total}")
        print(f"  ✅ Passed: {passed}")
        print(f"  ❌ Failed: {failed}")
        print(f"  Strict Mode: {strict_mode.value}")
        print("=" * 60)
    
    return results


def get_required_modules_for_type(stage_type: str) -> Set[str]:
    """특정 시나리오 유형의 필수 모듈 조회"""
    try:
        st = StageType(stage_type.lower())
        return REQUIRED_BY_TYPE.get(st, set())
    except ValueError:
        return set()


def get_recommended_modules_for_type(stage_type: str) -> Set[str]:
    """특정 시나리오 유형의 권장 모듈 조회"""
    try:
        st = StageType(stage_type.lower())
        return RECOMMENDED_BY_TYPE.get(st, set())
    except ValueError:
        return set()


# =============================================================================
# Stage DNA 템플릿 생성
# =============================================================================

def generate_stage_dna_template(
    stage_name: str,
    stage_type: str,
    include_optional: bool = True,
) -> str:
    """
    Stage DNA 템플릿 문자열 생성
    
    Args:
        stage_name: Stage 이름
        stage_type: 시나리오 유형
        include_optional: 선택 모듈 포함 여부
    
    Returns:
        Python 코드 문자열
    """
    required = get_required_modules_for_type(stage_type)
    recommended = get_recommended_modules_for_type(stage_type)
    
    lines = [
        '"""',
        f'{stage_name}',
        '',
        'Self-Healing Stage DNA 정의',
        'Reference: docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md',
        '"""',
        '',
        'STAGE_DNA = {',
        f'    "name": "{stage_name}",',
        f'    "type": "{stage_type}",',
        f'    "required_modules": {sorted(list(required))},',
    ]
    
    if include_optional and recommended:
        lines.append(f'    "optional_modules": {sorted(list(recommended))},')
    
    lines.extend([
        '}',
        '',
        '# DNA 검증 (테스트 시작 전 자동 체크)',
        'from load_tests.utils.selfhealing.stage_dna import validate_stage_dna',
        'validation_result = validate_stage_dna(STAGE_DNA)',
        'if not validation_result.is_valid:',
        '    import warnings',
        '    warnings.warn(str(validation_result))',
        '',
    ])
    
    return "\n".join(lines)


# =============================================================================
# CLI 인터페이스
# =============================================================================

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Stage DNA Validator")
    parser.add_argument(
        "--dir",
        default="load_tests/scenarios",
        help="Stage 파일 디렉토리",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="권장 모듈 누락도 실패로 처리",
    )
    parser.add_argument(
        "--strict-mode",
        type=str,
        choices=["off", "warn", "block", "fatal"],
        default="warn",
        help="Unknown Module 처리 방식 (off, warn, block, fatal)",
    )
    parser.add_argument(
        "--generate",
        type=str,
        help="DNA 템플릿 생성 (stage_type 지정)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Stage XX",
        help="생성할 Stage 이름",
    )
    
    args = parser.parse_args()
    
    if args.generate:
        template = generate_stage_dna_template(args.name, args.generate)
        print(template)
    else:
        strict_mode = StrictModeLevel(args.strict_mode)
        try:
            validate_all_stages(
                args.dir, 
                strict=args.strict,
                strict_mode=strict_mode
            )
        except DNAValidationError as e:
            print(f"\n❌ DNA Validation Failed:\n{e}")
            sys.exit(1)

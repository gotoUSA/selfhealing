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
from dataclasses import dataclass
from enum import Enum
import logging

logger = logging.getLogger(__name__)


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


def validate_stage_dna(
    stage_dna: Dict[str, Any],
    strict: bool = False,
    auto_fix: bool = False,
) -> ValidationResult:
    """
    Stage DNA 검증
    
    Args:
        stage_dna: Stage DNA 딕셔너리
            - name: Stage 이름
            - type: 시나리오 유형 (smoke, load, chaos, integration, platinum)
            - required_modules: 선언된 필수 모듈
            - optional_modules: 선언된 선택 모듈 (옵션)
        strict: True이면 권장 모듈 누락도 실패로 처리
        auto_fix: True이면 누락된 Core 모듈 자동 추가 제안
    
    Returns:
        ValidationResult: 검증 결과
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
    
    # 알 수 없는 모듈 경고
    unknown = all_declared - ALL_AVAILABLE_MODULES
    if unknown:
        warnings.append(f"Unknown modules: {', '.join(unknown)}")
    
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
    )


def validate_stage_file(file_path: str, strict: bool = False) -> Optional[ValidationResult]:
    """
    Stage 파일에서 STAGE_DNA를 읽어 검증
    
    Args:
        file_path: Stage 파일 경로
        strict: 엄격 모드 여부
    
    Returns:
        ValidationResult 또는 None (STAGE_DNA 없는 경우)
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
                            return validate_stage_dna(stage_dna, strict=strict)
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
) -> Dict[str, ValidationResult]:
    """
    모든 Stage 파일 검증
    
    Args:
        stages_dir: Stage 파일 디렉토리
        strict: 엄격 모드
        verbose: 상세 출력
    
    Returns:
        Dict[파일경로, ValidationResult]
    """
    import os
    import glob
    
    results = {}
    pattern = os.path.join(stages_dir, "**", "stage*.py")
    
    for file_path in glob.glob(pattern, recursive=True):
        result = validate_stage_file(file_path, strict=strict)
        
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
        validate_all_stages(args.dir, strict=args.strict)

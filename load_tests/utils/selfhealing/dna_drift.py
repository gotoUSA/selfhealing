"""
DNA Drift Detection - 코드베이스 스캔 및 Stage DNA 매핑 검증

Phase 2 구현: 코드 변경 시 DNA 자동 감지 및 테스트 누락 방지

기능:
1. 코드베이스 전체 스캔 (Service, View, Task, Middleware 등)
2. 기존 Stage DNA와 비교하여 누락된 항목 리포트
3. 신규 기능에 대한 DNA 제안 자동 생성
4. CI/CD 통합을 위한 CLI 인터페이스

참조:
- docs/self_healing/30_DNA_DRIFT_DISCOVERY.md
- docs/self_healing/29_STAGE_DNA_EVOLUTION_MASTER.md

Note:
- 기존 l2_storage.py의 has_drift()는 **런타임 데이터 드리프트** 감지
- 이 모듈은 **Stage DNA 선언 드리프트** 감지 (다른 목적!)
"""

import os
import re
import ast
import glob
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# 스캔 패턴 정의
# =============================================================================

SCAN_PATTERNS = {
    "services": [
        r"class\s+(\w+Service)\s*\(",
        r"class\s+(\w+Handler)\s*\(",
        r"class\s+(\w+Manager)\s*\(",
    ],
    "views": [
        r"class\s+(\w+View)\s*\(",
        r"class\s+(\w+ViewSet)\s*\(",
        r"class\s+(\w+APIView)\s*\(",
    ],
    "tasks": [
        r"@shared_task",
        r"@app\.task",
        r"@celery_app\.task",
    ],
    "middleware": [
        r"class\s+(\w+Middleware)\s*\(",
    ],
    "signals": [
        r"@receiver\s*\(",
        r"Signal\s*\(",
    ],
}


# =============================================================================
# 데이터 클래스
# =============================================================================

class FeatureType(Enum):
    """기능 유형"""
    SERVICE = "service"
    HANDLER = "handler"
    MANAGER = "manager"
    VIEW = "view"
    VIEWSET = "viewset"
    TASK = "task"
    MIDDLEWARE = "middleware"
    SIGNAL = "signal"
    UNKNOWN = "unknown"


@dataclass
class DiscoveredFeature:
    """발견된 기능"""
    name: str
    feature_type: FeatureType
    file_path: str
    line_number: int
    docstring: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    is_mapped_to_dna: bool = False
    suggested_modules: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "feature_type": self.feature_type.value,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "is_mapped_to_dna": self.is_mapped_to_dna,
            "suggested_modules": self.suggested_modules,
        }


@dataclass
class DriftReport:
    """DNA Drift 리포트"""
    scan_timestamp: str
    total_features_found: int
    mapped_features: int
    unmapped_features: int
    new_features: List[DiscoveredFeature]
    deprecated_dna: List[str]  # DNA에는 있지만 코드에 없는 것
    recommendations: List[str]
    scan_dirs: List[str]
    stage_dirs: List[str]
    
    @property
    def drift_percentage(self) -> float:
        """드리프트 비율 (누락된 기능 비율)"""
        if self.total_features_found == 0:
            return 0.0
        return (self.unmapped_features / self.total_features_found) * 100
    
    @property
    def is_clean(self) -> bool:
        """드리프트가 없는지 여부"""
        return self.unmapped_features == 0 and len(self.deprecated_dna) == 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_timestamp": self.scan_timestamp,
            "total_features_found": self.total_features_found,
            "mapped_features": self.mapped_features,
            "unmapped_features": self.unmapped_features,
            "drift_percentage": self.drift_percentage,
            "is_clean": self.is_clean,
            "new_features": [f.to_dict() for f in self.new_features],
            "deprecated_dna": self.deprecated_dna,
            "recommendations": self.recommendations,
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
    
    def to_markdown(self) -> str:
        """마크다운 리포트 생성"""
        lines = [
            "# DNA Drift Report",
            "",
            f"📅 **Scan Time**: {self.scan_timestamp}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Features Found | {self.total_features_found} |",
            f"| Mapped to DNA | {self.mapped_features} |",
            f"| Unmapped (Drift) | {self.unmapped_features} |",
            f"| Drift Percentage | {self.drift_percentage:.1f}% |",
            f"| Status | {'✅ Clean' if self.is_clean else '⚠️ Drift Detected'} |",
            "",
        ]
        
        if self.new_features:
            lines.extend([
                "## Unmapped Features (Action Required)",
                "",
            ])
            for feature in self.new_features:
                lines.append(
                    f"- **{feature.name}** ({feature.feature_type.value}) "
                    f"@ `{feature.file_path}:{feature.line_number}`"
                )
                if feature.suggested_modules:
                    lines.append(f"  - Suggested Modules: {', '.join(feature.suggested_modules)}")
            lines.append("")
        
        if self.deprecated_dna:
            lines.extend([
                "## Deprecated DNA (Code Removed)",
                "",
            ])
            for dna in self.deprecated_dna:
                lines.append(f"- 🗑️ {dna}")
            lines.append("")
        
        if self.recommendations:
            lines.extend([
                "## Recommendations",
                "",
            ])
            for rec in self.recommendations:
                lines.append(f"- {rec}")
        
        return "\n".join(lines)


# =============================================================================
# DNA Drift Detector
# =============================================================================

class DNADriftDetector:
    """
    DNA Drift 감지기
    
    코드베이스의 Service, View, Task 등을 스캔하고
    Stage DNA에 매핑되지 않은 기능을 찾아냅니다.
    """
    
    # 기능 유형별 기본 권장 모듈
    DEFAULT_MODULES_BY_TYPE = {
        FeatureType.SERVICE: ["circuit_breaker", "error_budget", "health"],
        FeatureType.HANDLER: ["circuit_breaker", "dlq", "health"],
        FeatureType.MANAGER: ["circuit_breaker", "health"],
        FeatureType.VIEW: ["rate_limiter", "auth", "health"],
        FeatureType.VIEWSET: ["rate_limiter", "auth", "health"],
        FeatureType.TASK: ["dlq", "observability", "health"],
        FeatureType.MIDDLEWARE: ["health"],
        FeatureType.SIGNAL: ["observability"],
    }
    
    def __init__(
        self,
        code_dirs: List[str] = None,
        stage_dirs: List[str] = None,
        exclude_patterns: List[str] = None,
        project_root: str = ".",
    ):
        """
        초기화
        
        Args:
            code_dirs: 스캔할 코드 디렉토리 (기본: ["shopping/"])
            stage_dirs: Stage 파일 디렉토리 (기본: ["load_tests/scenarios/"])
            exclude_patterns: 제외 패턴 (glob)
            project_root: 프로젝트 루트 디렉토리
        """
        self.project_root = project_root
        self.code_dirs = code_dirs or ["shopping/"]
        self.stage_dirs = stage_dirs or ["load_tests/scenarios/"]
        self.exclude_patterns = exclude_patterns or [
            "**/migrations/**",
            "**/tests/**",
            "**/__pycache__/**",
            "**/test_*.py",
            "**/*_test.py",
        ]
        
        self.discovered_features: List[DiscoveredFeature] = []
        self.existing_dna_mappings: Dict[str, str] = {}  # name -> stage_file
    
    def scan_codebase(self) -> List[DiscoveredFeature]:
        """
        코드베이스 전체 스캔
        
        Returns:
            발견된 기능 목록
        """
        features = []
        
        for code_dir in self.code_dirs:
            full_dir = os.path.join(self.project_root, code_dir)
            if not os.path.exists(full_dir):
                logger.warning(f"Directory not found: {full_dir}")
                continue
            
            for file_path in glob.glob(
                os.path.join(full_dir, "**/*.py"),
                recursive=True
            ):
                if self._should_exclude(file_path):
                    continue
                
                try:
                    file_features = self._scan_file(file_path)
                    features.extend(file_features)
                except Exception as e:
                    logger.warning(f"Failed to scan {file_path}: {e}")
        
        # 권장 모듈 추가
        for feature in features:
            feature.suggested_modules = list(
                self.DEFAULT_MODULES_BY_TYPE.get(feature.feature_type, ["health"])
            )
        
        self.discovered_features = features
        logger.info(f"Discovered {len(features)} features in codebase")
        return features
    
    def _should_exclude(self, file_path: str) -> bool:
        """제외 패턴 체크"""
        for pattern in self.exclude_patterns:
            if glob.fnmatch.fnmatch(file_path, pattern):
                return True
            # 상대 경로로도 체크
            rel_path = os.path.relpath(file_path, self.project_root)
            if glob.fnmatch.fnmatch(rel_path, pattern):
                return True
        return False
    
    def _scan_file(self, file_path: str) -> List[DiscoveredFeature]:
        """단일 파일 스캔"""
        features = []
        
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            logger.warning(f"Syntax error in {file_path}: {e}")
            return []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                feature = self._analyze_class(node, file_path)
                if feature:
                    features.append(feature)
            
            elif isinstance(node, ast.FunctionDef):
                feature = self._analyze_function(node, file_path, content)
                if feature:
                    features.append(feature)
        
        return features
    
    def _analyze_class(
        self,
        node: ast.ClassDef,
        file_path: str
    ) -> Optional[DiscoveredFeature]:
        """클래스 분석"""
        name = node.name
        feature_type = None
        
        # 클래스 이름 패턴으로 유형 판별
        if name.endswith("Service"):
            feature_type = FeatureType.SERVICE
        elif name.endswith("Handler"):
            feature_type = FeatureType.HANDLER
        elif name.endswith("Manager"):
            feature_type = FeatureType.MANAGER
        elif name.endswith("ViewSet"):
            feature_type = FeatureType.VIEWSET
        elif name.endswith("View") or name.endswith("APIView"):
            feature_type = FeatureType.VIEW
        elif name.endswith("Middleware"):
            feature_type = FeatureType.MIDDLEWARE
        elif name.endswith("Task"):
            feature_type = FeatureType.TASK
        
        if not feature_type:
            # 부모 클래스로 추가 판별
            for base in node.bases:
                base_name = self._get_name(base)
                if base_name in ("View", "APIView", "GenericAPIView"):
                    feature_type = FeatureType.VIEW
                    break
                elif base_name in ("ViewSet", "ModelViewSet", "GenericViewSet"):
                    feature_type = FeatureType.VIEWSET
                    break
        
        if not feature_type:
            return None
        
        # Docstring 추출
        docstring = ast.get_docstring(node)
        
        return DiscoveredFeature(
            name=name,
            feature_type=feature_type,
            file_path=file_path,
            line_number=node.lineno,
            docstring=docstring,
        )
    
    def _get_name(self, node: ast.expr) -> str:
        """AST 노드에서 이름 추출"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return node.attr
        return ""
    
    def _analyze_function(
        self,
        node: ast.FunctionDef,
        file_path: str,
        content: str
    ) -> Optional[DiscoveredFeature]:
        """함수 분석 (Celery Task 등)"""
        for decorator in node.decorator_list:
            decorator_name = ""
            if isinstance(decorator, ast.Name):
                decorator_name = decorator.id
            elif isinstance(decorator, ast.Attribute):
                decorator_name = decorator.attr
            elif isinstance(decorator, ast.Call):
                if isinstance(decorator.func, ast.Name):
                    decorator_name = decorator.func.id
                elif isinstance(decorator.func, ast.Attribute):
                    decorator_name = decorator.func.attr
            
            if decorator_name in ["shared_task", "task", "celery_task"]:
                return DiscoveredFeature(
                    name=node.name,
                    feature_type=FeatureType.TASK,
                    file_path=file_path,
                    line_number=node.lineno,
                    docstring=ast.get_docstring(node),
                )
            
            if decorator_name == "receiver":
                return DiscoveredFeature(
                    name=node.name,
                    feature_type=FeatureType.SIGNAL,
                    file_path=file_path,
                    line_number=node.lineno,
                    docstring=ast.get_docstring(node),
                )
        
        return None
    
    def load_existing_dna(self) -> Dict[str, str]:
        """
        기존 Stage DNA에서 테스트 대상 로드
        
        Returns:
            이름 -> Stage 파일 경로 매핑
        """
        mappings = {}
        
        for stage_dir in self.stage_dirs:
            full_dir = os.path.join(self.project_root, stage_dir)
            if not os.path.exists(full_dir):
                continue
            
            for file_path in glob.glob(
                os.path.join(full_dir, "**/*.py"),
                recursive=True
            ):
                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    
                    # STAGE_DNA 딕셔너리 찾기
                    if "STAGE_DNA" not in content:
                        continue
                    
                    # 테스트 대상 패턴 추출
                    # test_targets 또는 테스트 함수에서 클래스/함수 이름 추출
                    target_patterns = [
                        r"test_targets['\"]?\s*:\s*\[([^\]]+)\]",
                        r"class\s+(\w+Service|\w+Handler|\w+View)",
                        r"client\.(get|post|put|delete)\s*\(['\"]([^'\"]+)",
                    ]
                    
                    for pattern in target_patterns:
                        for match in re.finditer(pattern, content):
                            if match.lastindex:
                                target = match.group(match.lastindex)
                                # 정리
                                for name in re.findall(r"['\"](\w+)['\"]", target):
                                    mappings[name] = file_path
                
                except Exception as e:
                    logger.warning(f"Failed to load DNA from {file_path}: {e}")
        
        self.existing_dna_mappings = mappings
        logger.info(f"Loaded {len(mappings)} DNA mappings")
        return mappings
    
    def detect_drift(self) -> DriftReport:
        """
        DNA Drift 감지
        
        Returns:
            DriftReport: 드리프트 리포트
        """
        if not self.discovered_features:
            self.scan_codebase()
        
        if not self.existing_dna_mappings:
            self.load_existing_dna()
        
        # 매핑 확인
        unmapped = []
        mapped_count = 0
        
        for feature in self.discovered_features:
            if feature.name in self.existing_dna_mappings:
                feature.is_mapped_to_dna = True
                mapped_count += 1
            else:
                feature.is_mapped_to_dna = False
                unmapped.append(feature)
        
        # Deprecated DNA 확인 (코드에 없는 DNA)
        discovered_names = {f.name for f in self.discovered_features}
        deprecated = [
            name for name in self.existing_dna_mappings
            if name not in discovered_names
        ]
        
        # 추천사항 생성
        recommendations = self._generate_recommendations(unmapped, deprecated)
        
        return DriftReport(
            scan_timestamp=datetime.now().isoformat(),
            total_features_found=len(self.discovered_features),
            mapped_features=mapped_count,
            unmapped_features=len(unmapped),
            new_features=unmapped,
            deprecated_dna=deprecated,
            recommendations=recommendations,
            scan_dirs=self.code_dirs,
            stage_dirs=self.stage_dirs,
        )
    
    def _generate_recommendations(
        self,
        unmapped: List[DiscoveredFeature],
        deprecated: List[str]
    ) -> List[str]:
        """추천사항 생성"""
        recommendations = []
        
        if unmapped:
            recommendations.append(
                f"⚠️ {len(unmapped)}개의 기능이 Stage DNA에 매핑되지 않았습니다."
            )
            # 유형별 그룹화
            by_type: Dict[FeatureType, List[DiscoveredFeature]] = {}
            for feature in unmapped:
                by_type.setdefault(feature.feature_type, []).append(feature)
            
            for ftype, features in by_type.items():
                recommendations.append(
                    f"  - {ftype.value}: {len(features)}개 "
                    f"({', '.join(f.name for f in features[:3])}{'...' if len(features) > 3 else ''})"
                )
        
        if deprecated:
            recommendations.append(
                f"🗑️ {len(deprecated)}개의 DNA가 더 이상 코드에 존재하지 않습니다."
            )
            for name in deprecated[:5]:
                recommendations.append(f"  - {name}")
            if len(deprecated) > 5:
                recommendations.append(f"  ... 외 {len(deprecated) - 5}개")
        
        if not unmapped and not deprecated:
            recommendations.append("✅ 모든 기능이 Stage DNA에 정상 매핑되어 있습니다.")
        
        return recommendations
    
    def generate_dna_suggestion(self, feature: DiscoveredFeature) -> str:
        """
        새 기능에 대한 Stage DNA 제안 생성
        
        Args:
            feature: 발견된 기능
            
        Returns:
            DNA 제안 코드
        """
        stage_type = "integration"  # 기본값
        required_modules = list(feature.suggested_modules)
        
        # 유형별 Stage 타입 결정
        if feature.feature_type in (FeatureType.TASK,):
            stage_type = "integration"
        elif feature.feature_type in (FeatureType.VIEW, FeatureType.VIEWSET):
            stage_type = "load"
        elif feature.feature_type == FeatureType.SERVICE:
            stage_type = "integration"
        
        template = f'''"""
자동 생성된 Stage DNA 제안

Target: {feature.name}
File: {feature.file_path}:{feature.line_number}
Type: {feature.feature_type.value}
"""

STAGE_DNA = {{
    "name": "Stage XX - {feature.name} Test",
    "type": "{stage_type}",
    "version": "2.0",
    "required_modules": {sorted(set(required_modules))},
    "optional_modules": ["observability"],
    
    # Phase 1: Safety
    "blast_radius": "isolated",
    "rollback_strategy": "automatic",
    
    # Test Targets
    "test_targets": ["{feature.name}"],
}}

# 검증 실행
from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
_dna_result = validate_stage_dna(STAGE_DNA)
if not _dna_result.is_valid:
    import warnings
    warnings.warn(str(_dna_result))
'''
        return template


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="DNA Drift Detector - Stage DNA 누락 감지"
    )
    parser.add_argument(
        "--code-dir",
        nargs="+",
        default=["shopping/"],
        help="스캔할 코드 디렉토리 (복수 가능)",
    )
    parser.add_argument(
        "--stage-dir",
        nargs="+",
        default=["load_tests/scenarios/"],
        help="Stage 파일 디렉토리 (복수 가능)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="리포트 출력 파일 (JSON)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "text"],
        default="text",
        help="출력 형식",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="누락된 기능에 대한 DNA 제안 생성",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="드리프트 발견 시 exit code 1 반환",
    )
    
    args = parser.parse_args()
    
    detector = DNADriftDetector(
        code_dirs=args.code_dir,
        stage_dirs=args.stage_dir,
    )
    
    report = detector.detect_drift()
    
    # 출력
    if args.format == "json":
        output = report.to_json()
    elif args.format == "markdown":
        output = report.to_markdown()
    else:
        output = "\n".join([
            "",
            "=" * 60,
            "📊 DNA Drift Report",
            "=" * 60,
            f"스캔 시간: {report.scan_timestamp}",
            f"발견된 기능: {report.total_features_found}",
            f"매핑된 기능: {report.mapped_features}",
            f"누락된 기능: {report.unmapped_features}",
            f"Drift 비율: {report.drift_percentage:.1f}%",
            "",
            *report.recommendations,
        ])
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Report saved to {args.output}")
    else:
        print(output)
    
    # DNA 제안 생성
    if args.suggest and report.new_features:
        print("\n" + "=" * 60)
        print("💡 DNA Suggestions")
        print("=" * 60)
        for feature in report.new_features[:3]:
            suggestion = detector.generate_dna_suggestion(feature)
            print(suggestion)
    
    # Strict 모드
    if args.strict and not report.is_clean:
        sys.exit(1)


if __name__ == "__main__":
    import sys
    main()

"""
Discovery Stage - 테스트 커버리지 갭 분석 및 Dead Code 감지

Phase 2 구현: 정기적으로 시스템을 탐험하여 테스트되지 않는 코드 발견

기능:
1. 모든 API 엔드포인트 수집
2. Stage DNA와 테스트 대상 매핑
3. 테스트되지 않는 엔드포인트 리스트 생성
4. Dead Code 후보 감지
5. 자동 테스트 케이스 생성 제안

참조:
- docs/self_healing/30_DNA_DRIFT_DISCOVERY.md (Section 4)
- docs/self_healing/29_STAGE_DNA_EVOLUTION_MASTER.md
"""

import os
import re
import ast
import glob
import json
from typing import Dict, List, Set, Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import logging

logger = logging.getLogger(__name__)


# =============================================================================
# 데이터 클래스
# =============================================================================

class HTTPMethod(Enum):
    """HTTP 메서드"""
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"
    HEAD = "HEAD"
    ALL = "*"


@dataclass
class APIEndpoint:
    """API 엔드포인트"""
    path: str
    method: HTTPMethod
    view_name: str
    view_type: str  # function, class, viewset
    file_path: str
    line_number: int
    url_name: Optional[str] = None
    is_tested: bool = False
    test_stages: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "method": self.method.value,
            "view_name": self.view_name,
            "view_type": self.view_type,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "url_name": self.url_name,
            "is_tested": self.is_tested,
            "test_stages": self.test_stages,
        }
    
    @property
    def normalized_path(self) -> str:
        """정규화된 경로 (변수 부분 * 처리)"""
        # /api/v1/orders/123/ → /api/v1/orders/*/
        path = re.sub(r"<[^>]+>", "*", self.path)  # Django URL 변수
        path = re.sub(r"\{[^}]+\}", "*", path)  # OpenAPI 스타일
        path = re.sub(r"/\d+/", "/*/", path)  # 숫자 ID
        return path


@dataclass
class DeadCodeCandidate:
    """Dead Code 후보"""
    name: str
    file_path: str
    line_number: int
    code_type: str  # function, class, method
    reason: str
    confidence: float  # 0.0 ~ 1.0
    last_modified: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "file_path": self.file_path,
            "line_number": self.line_number,
            "code_type": self.code_type,
            "reason": self.reason,
            "confidence": self.confidence,
        }


@dataclass
class DiscoveryReport:
    """Discovery 결과 리포트"""
    discovery_timestamp: str
    total_endpoints: int
    tested_endpoints: int
    untested_endpoints: int
    dead_code_candidates: List[DeadCodeCandidate]
    coverage_percentage: float
    recommendations: List[str]
    endpoint_details: List[APIEndpoint]
    
    @property
    def is_healthy(self) -> bool:
        """테스트 커버리지가 90% 이상인지"""
        return self.coverage_percentage >= 90.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "discovery_timestamp": self.discovery_timestamp,
            "total_endpoints": self.total_endpoints,
            "tested_endpoints": self.tested_endpoints,
            "untested_endpoints": self.untested_endpoints,
            "coverage_percentage": self.coverage_percentage,
            "is_healthy": self.is_healthy,
            "dead_code_candidates": [d.to_dict() for d in self.dead_code_candidates],
            "recommendations": self.recommendations,
            "endpoint_details": [e.to_dict() for e in self.endpoint_details],
        }
    
    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
    
    def to_markdown(self) -> str:
        """마크다운 리포트 생성"""
        lines = [
            "# Discovery Stage Report",
            "",
            f"📅 **Discovery Time**: {self.discovery_timestamp}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|--------|-------|",
            f"| Total Endpoints | {self.total_endpoints} |",
            f"| Tested | {self.tested_endpoints} |",
            f"| Untested | {self.untested_endpoints} |",
            f"| Coverage | {self.coverage_percentage:.1f}% |",
            f"| Status | {'✅ Healthy' if self.is_healthy else '⚠️ Needs Attention'} |",
            "",
        ]
        
        # Untested Endpoints
        untested = [e for e in self.endpoint_details if not e.is_tested]
        if untested:
            lines.extend([
                "## Untested Endpoints",
                "",
                "| Path | Method | View | File |",
                "|------|--------|------|------|",
            ])
            for ep in untested[:20]:
                lines.append(
                    f"| `{ep.path}` | {ep.method.value} | {ep.view_name} | {ep.file_path} |"
                )
            if len(untested) > 20:
                lines.append(f"| ... | ... | ... | (외 {len(untested) - 20}개) |")
            lines.append("")
        
        # Dead Code Candidates
        if self.dead_code_candidates:
            lines.extend([
                "## Dead Code Candidates",
                "",
            ])
            for dc in self.dead_code_candidates:
                confidence_icon = "🔴" if dc.confidence > 0.8 else "🟡" if dc.confidence > 0.5 else "🟢"
                lines.append(
                    f"- {confidence_icon} **{dc.name}** ({dc.code_type}) "
                    f"@ `{dc.file_path}:{dc.line_number}`"
                )
                lines.append(f"  - Reason: {dc.reason}")
            lines.append("")
        
        # Recommendations
        if self.recommendations:
            lines.extend([
                "## Recommendations",
                "",
            ])
            for rec in self.recommendations:
                lines.append(f"- {rec}")
        
        return "\n".join(lines)


# =============================================================================
# Discovery Stage
# =============================================================================

class DiscoveryStage:
    """
    Discovery Stage - 시스템 탐험 및 테스트 갭 분석
    
    정기적으로 실행하여:
    1. 모든 API 엔드포인트 발견
    2. 테스트 커버리지 확인
    3. Dead Code 후보 감지
    """
    
    def __init__(
        self,
        project_root: str = ".",
        app_dirs: List[str] = None,
        urls_patterns: List[str] = None,
        stage_dirs: List[str] = None,
        exclude_apps: List[str] = None,
    ):
        """
        초기화
        
        Args:
            project_root: 프로젝트 루트 디렉토리
            app_dirs: Django 앱 디렉토리 목록
            urls_patterns: URL 설정 파일 패턴
            stage_dirs: Stage 파일 디렉토리
            exclude_apps: 제외할 앱 (admin, auth 등)
        """
        self.project_root = project_root
        self.app_dirs = app_dirs or ["shopping/"]
        self.urls_patterns = urls_patterns or ["**/urls.py"]
        self.stage_dirs = stage_dirs or ["load_tests/", "tests/"]
        self.exclude_apps = exclude_apps or ["admin", "auth", "contenttypes", "sessions"]
        
        self.endpoints: List[APIEndpoint] = []
        self.tested_paths: Set[str] = set()
        self.dead_code_candidates: List[DeadCodeCandidate] = []
    
    def discover_endpoints(self) -> List[APIEndpoint]:
        """
        모든 API 엔드포인트 발견
        
        Returns:
            발견된 엔드포인트 목록
        """
        endpoints = []
        
        for app_dir in self.app_dirs:
            full_dir = os.path.join(self.project_root, app_dir)
            if not os.path.exists(full_dir):
                continue
            
            for pattern in self.urls_patterns:
                for file_path in glob.glob(
                    os.path.join(full_dir, pattern),
                    recursive=True
                ):
                    try:
                        file_endpoints = self._parse_urls_file(file_path)
                        endpoints.extend(file_endpoints)
                    except Exception as e:
                        logger.warning(f"Failed to parse {file_path}: {e}")
        
        self.endpoints = endpoints
        logger.info(f"Discovered {len(endpoints)} endpoints")
        return endpoints
    
    def _parse_urls_file(self, file_path: str) -> List[APIEndpoint]:
        """urls.py 파일 파싱"""
        endpoints = []
        
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        # path() 패턴 매칭
        path_patterns = [
            # path("api/v1/orders/", OrderListView.as_view())
            r"path\s*\(\s*['\"]([^'\"]+)['\"].*?(\w+)\.as_view\s*\(",
            # path("api/v1/orders/", views.order_list)
            r"path\s*\(\s*['\"]([^'\"]+)['\"].*?views\.(\w+)",
            # path("api/v1/orders/", order_list, name="order-list")
            r"path\s*\(\s*['\"]([^'\"]+)['\"].*?(\w+)\s*,",
        ]
        
        for pattern in path_patterns:
            for match in re.finditer(pattern, content, re.MULTILINE | re.DOTALL):
                path = match.group(1)
                view_name = match.group(2)
                line_number = content[:match.start()].count("\n") + 1
                
                # URL name 추출
                url_name = None
                name_match = re.search(
                    r"name\s*=\s*['\"]([^'\"]+)['\"]",
                    match.group(0)
                )
                if name_match:
                    url_name = name_match.group(1)
                
                endpoints.append(APIEndpoint(
                    path=path,
                    method=HTTPMethod.ALL,
                    view_name=view_name,
                    view_type="class" if ".as_view" in match.group(0) else "function",
                    file_path=file_path,
                    line_number=line_number,
                    url_name=url_name,
                ))
        
        # router.register() 패턴 (DRF)
        router_pattern = re.compile(
            r"router\.register\s*\(\s*['\"]([^'\"]+)['\"].*?(\w+)",
            re.MULTILINE | re.DOTALL
        )
        
        for match in router_pattern.finditer(content):
            path = match.group(1)
            viewset = match.group(2)
            line_number = content[:match.start()].count("\n") + 1
            
            endpoints.append(APIEndpoint(
                path=path,
                method=HTTPMethod.ALL,
                view_name=viewset,
                view_type="viewset",
                file_path=file_path,
                line_number=line_number,
            ))
        
        return endpoints
    
    def collect_tested_paths(self) -> Set[str]:
        """
        테스트에서 호출하는 경로 수집
        
        Returns:
            테스트된 경로 집합
        """
        tested = set()
        
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
                    
                    # API 호출 패턴 매칭
                    patterns = [
                        # self.client.get("/api/v1/orders/")
                        r'self\.client\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']',
                        # response = client.get("/api/v1/orders/")
                        r'client\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']',
                        # requests.get("http://localhost/api/v1/orders/")
                        r'requests\.(get|post|put|patch|delete)\s*\([^)]*["\']([^"\']+)["\']',
                        # APIClient().get("/api/v1/orders/")
                        r'APIClient\s*\(\s*\)\.(get|post|put|patch|delete)\s*\(\s*["\']([^"\']+)["\']',
                        # reverse("order-list")
                        r'reverse\s*\(\s*["\']([^"\']+)["\']',
                    ]
                    
                    for pattern in patterns:
                        for match in re.finditer(pattern, content):
                            if match.lastindex:
                                path = match.group(match.lastindex)
                                # 정규화
                                normalized = self._normalize_path(path)
                                tested.add(normalized)
                                
                                # 원본 경로도 추가
                                tested.add(path)
                
                except Exception as e:
                    logger.warning(f"Failed to analyze {file_path}: {e}")
        
        self.tested_paths = tested
        logger.info(f"Collected {len(tested)} tested paths")
        return tested
    
    def _normalize_path(self, path: str) -> str:
        """경로 정규화"""
        # URL에서 호스트 제거
        path = re.sub(r"https?://[^/]+", "", path)
        # 변수 부분 * 처리
        path = re.sub(r"<[^>]+>", "*", path)
        path = re.sub(r"\{[^}]+\}", "*", path)
        path = re.sub(r"/\d+/?", "/*/", path)
        # 쿼리스트링 제거
        path = re.sub(r"\?.*$", "", path)
        return path
    
    def detect_dead_code(self) -> List[DeadCodeCandidate]:
        """
        Dead Code 후보 감지
        
        Returns:
            Dead Code 후보 목록
        """
        candidates = []
        
        for app_dir in self.app_dirs:
            full_dir = os.path.join(self.project_root, app_dir)
            if not os.path.exists(full_dir):
                continue
            
            for file_path in glob.glob(
                os.path.join(full_dir, "**/*.py"),
                recursive=True
            ):
                if self._should_skip_file(file_path):
                    continue
                
                try:
                    file_candidates = self._analyze_file_for_dead_code(file_path)
                    candidates.extend(file_candidates)
                except Exception as e:
                    logger.warning(f"Failed to analyze {file_path}: {e}")
        
        self.dead_code_candidates = candidates
        return candidates
    
    def _should_skip_file(self, file_path: str) -> bool:
        """파일 스킵 여부"""
        skip_patterns = ["migrations", "__pycache__", "tests", "test_"]
        return any(p in file_path for p in skip_patterns)
    
    def _analyze_file_for_dead_code(self, file_path: str) -> List[DeadCodeCandidate]:
        """파일에서 Dead Code 후보 분석"""
        candidates = []
        
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # 프라이빗 함수 중 사용되지 않는 것
                if node.name.startswith("_") and not node.name.startswith("__"):
                    # 파일 내에서 호출 여부 확인
                    call_pattern = rf"\b{node.name}\s*\("
                    calls = len(re.findall(call_pattern, content)) - 1  # 정의 제외
                    
                    if calls == 0:
                        candidates.append(DeadCodeCandidate(
                            name=node.name,
                            file_path=file_path,
                            line_number=node.lineno,
                            code_type="function",
                            reason="Private function with no internal calls",
                            confidence=0.6,
                        ))
            
            elif isinstance(node, ast.ClassDef):
                # TODO: 주석된 클래스 감지
                # deprecated 어노테이션 체크
                for decorator in node.decorator_list:
                    dec_name = ""
                    if isinstance(decorator, ast.Name):
                        dec_name = decorator.id
                    elif isinstance(decorator, ast.Attribute):
                        dec_name = decorator.attr
                    
                    if dec_name.lower() in ["deprecated", "obsolete"]:
                        candidates.append(DeadCodeCandidate(
                            name=node.name,
                            file_path=file_path,
                            line_number=node.lineno,
                            code_type="class",
                            reason="Marked as deprecated",
                            confidence=0.9,
                        ))
        
        return candidates
    
    def analyze_coverage(self) -> DiscoveryReport:
        """
        커버리지 분석 실행
        
        Returns:
            DiscoveryReport: 분석 결과
        """
        if not self.endpoints:
            self.discover_endpoints()
        
        if not self.tested_paths:
            self.collect_tested_paths()
        
        # 엔드포인트별 테스트 여부 확인
        for endpoint in self.endpoints:
            normalized = endpoint.normalized_path
            if (normalized in self.tested_paths or 
                endpoint.path in self.tested_paths or
                endpoint.url_name in self.tested_paths):
                endpoint.is_tested = True
        
        # Dead Code 감지
        if not self.dead_code_candidates:
            self.detect_dead_code()
        
        # 통계 계산
        tested_count = sum(1 for e in self.endpoints if e.is_tested)
        untested_count = len(self.endpoints) - tested_count
        coverage = (tested_count / len(self.endpoints) * 100) if self.endpoints else 100.0
        
        # 추천사항 생성
        recommendations = self._generate_recommendations(
            tested_count, untested_count, coverage
        )
        
        return DiscoveryReport(
            discovery_timestamp=datetime.now().isoformat(),
            total_endpoints=len(self.endpoints),
            tested_endpoints=tested_count,
            untested_endpoints=untested_count,
            coverage_percentage=coverage,
            dead_code_candidates=self.dead_code_candidates,
            recommendations=recommendations,
            endpoint_details=self.endpoints,
        )
    
    def _generate_recommendations(
        self,
        tested: int,
        untested: int,
        coverage: float
    ) -> List[str]:
        """추천사항 생성"""
        recommendations = []
        
        if coverage < 50:
            recommendations.append(
                f"🔴 테스트 커버리지가 {coverage:.1f}%로 매우 낮습니다. "
                f"즉시 테스트 추가가 필요합니다."
            )
        elif coverage < 80:
            recommendations.append(
                f"🟡 테스트 커버리지가 {coverage:.1f}%입니다. "
                f"80% 이상을 목표로 테스트를 추가하세요."
            )
        elif coverage < 90:
            recommendations.append(
                f"🟢 테스트 커버리지 {coverage:.1f}%로 양호합니다. "
                f"90% 이상 달성을 목표로 하세요."
            )
        else:
            recommendations.append(
                f"✅ 테스트 커버리지 {coverage:.1f}%로 우수합니다!"
            )
        
        if untested > 0:
            recommendations.append(
                f"⚠️ {untested}개의 엔드포인트가 테스트되지 않았습니다."
            )
        
        if self.dead_code_candidates:
            high_confidence = [c for c in self.dead_code_candidates if c.confidence > 0.7]
            if high_confidence:
                recommendations.append(
                    f"🗑️ {len(high_confidence)}개의 Dead Code 후보가 발견되었습니다. "
                    f"검토 후 제거를 고려하세요."
                )
        
        return recommendations
    
    def generate_test_suggestions(
        self,
        max_suggestions: int = 5
    ) -> List[str]:
        """
        테스트 케이스 생성 제안
        
        Args:
            max_suggestions: 최대 제안 개수
            
        Returns:
            테스트 코드 제안 목록
        """
        untested = [e for e in self.endpoints if not e.is_tested][:max_suggestions]
        suggestions = []
        
        for endpoint in untested:
            method = "get" if endpoint.method == HTTPMethod.ALL else endpoint.method.value.lower()
            suggestion = f'''
def test_{endpoint.view_name.lower()}_{method}(self):
    """
    Test {endpoint.view_name} - {endpoint.path}
    Auto-generated by Discovery Stage
    """
    response = self.client.{method}("{endpoint.path}")
    self.assertIn(response.status_code, [200, 201, 204])
'''
            suggestions.append(suggestion)
        
        return suggestions


# =============================================================================
# Metrics 수집용 클래스
# =============================================================================

@dataclass
class DiscoveryMetrics:
    """Discovery Stage 메트릭"""
    timestamp: str
    total_endpoints: int
    coverage_percentage: float
    dead_code_count: int
    untested_critical_count: int
    
    def to_prometheus_format(self) -> str:
        """Prometheus 형식 메트릭"""
        lines = [
            f'discovery_total_endpoints {self.total_endpoints}',
            f'discovery_coverage_percentage {self.coverage_percentage}',
            f'discovery_dead_code_count {self.dead_code_count}',
            f'discovery_untested_critical_count {self.untested_critical_count}',
        ]
        return "\n".join(lines)


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Discovery Stage - 테스트 커버리지 갭 분석"
    )
    parser.add_argument(
        "--app-dir",
        nargs="+",
        default=["shopping/"],
        help="Django 앱 디렉토리 (복수 가능)",
    )
    parser.add_argument(
        "--stage-dir",
        nargs="+",
        default=["load_tests/", "tests/"],
        help="테스트 파일 디렉토리 (복수 가능)",
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
        "--suggest-tests",
        type=int,
        default=0,
        help="테스트 케이스 제안 개수",
    )
    parser.add_argument(
        "--min-coverage",
        type=float,
        default=80.0,
        help="최소 커버리지 (미달 시 exit code 1)",
    )
    
    args = parser.parse_args()
    
    discovery = DiscoveryStage(
        app_dirs=args.app_dir,
        stage_dirs=args.stage_dir,
    )
    
    report = discovery.analyze_coverage()
    
    # 출력
    if args.format == "json":
        output = report.to_json()
    elif args.format == "markdown":
        output = report.to_markdown()
    else:
        output = "\n".join([
            "",
            "=" * 60,
            "🔍 Discovery Stage Report",
            "=" * 60,
            f"발견 시간: {report.discovery_timestamp}",
            f"총 엔드포인트: {report.total_endpoints}",
            f"테스트된 엔드포인트: {report.tested_endpoints}",
            f"미테스트 엔드포인트: {report.untested_endpoints}",
            f"커버리지: {report.coverage_percentage:.1f}%",
            "",
            *report.recommendations,
        ])
    
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Report saved to {args.output}")
    else:
        print(output)
    
    # 테스트 제안
    if args.suggest_tests > 0:
        suggestions = discovery.generate_test_suggestions(args.suggest_tests)
        if suggestions:
            print("\n" + "=" * 60)
            print("💡 Test Suggestions")
            print("=" * 60)
            for suggestion in suggestions:
                print(suggestion)
    
    # 최소 커버리지 체크
    if report.coverage_percentage < args.min_coverage:
        print(f"\n❌ Coverage {report.coverage_percentage:.1f}% is below minimum {args.min_coverage}%")
        sys.exit(1)


if __name__ == "__main__":
    import sys
    main()

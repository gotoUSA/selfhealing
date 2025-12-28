# DNA Drift Detection & Discovery Stage

📅 **작성일**: 2025-12-29  
🎯 **목적**: 코드 변경 시 DNA 자동 감지 및 테스트 누락 방지  
📋 **버전**: v1.0.0  
📌 **관련 문서**: [29_STAGE_DNA_EVOLUTION_MASTER.md](29_STAGE_DNA_EVOLUTION_MASTER.md)

---

## ⚠️ 기존 구현 현황 (중복 주의!)

### ✅ Drift 관련 기존 구현

| 기능 | 파일 | 함수 | 비고 |
|------|------|------|------|
| Drift Report 조회 | `governance.py` | `get_drift_report()` | GET /metrics/drift-report/ |
| Drift Stats 조회 | `l2_storage.py` | `get_drift_stats()` | GET /l2-storage/drift/stats/ |
| Drift 히스토리 | `l2_storage.py` | `get_drift_history()` | GET /l2-storage/drift/history/ |
| Drift 발생 여부 | `l2_storage.py` | `has_drift()` | Boolean 반환 |
| Drift 조정 | `l2_storage.py` | `trigger_reconcile()` | POST /l2-storage/drift/reconcile/ |
| Drift 임계값 | `runtime_config.py` | `get_drift_thresholds()` | GET /config/drift-thresholds/ |
| Dashboard Drift | `dashboard.py` | `get_drift_report()` | Deprecated |

### 🆕 이 문서의 신규 기능

| 기능 | 기존 대비 차이 | 구현 필요 |
|------|---------------|----------|
| **Unknown Module Strict Mode** | 기존: 경고만 출력 → 신규: 테스트 차단 | ✅ 필요 |
| **Discovery Stage** | 완전 신규 - 테스트 누락 자동 탐지 | ✅ 필요 |
| **DNA Drift (테스트 레벨)** | 기존은 API Drift → 신규는 Stage DNA Drift | ✅ 필요 |

> 💡 **구현 시 주의**: Drift 관련 API 호출은 기존 `l2_storage.py`, `governance.py`를 사용하고,
> **Stage DNA 레벨의 Drift 감지 로직만 신규 구현**하세요.

### 📁 구현 위치 가이드

이 문서의 기능들은 **테스트 전용**입니다 (런타임 코드 불필요):

| 기능 | 테스트 코드 | 연계 모듈 | 용도 |
|------|----------|----------|------|
| **Unknown Module Strict Mode** | `load_tests/utils/selfhealing/stage_dna.py` (확장) | 없음 | DNA 검증 시 테스트 차단 |
| **DNA Drift (테스트 레벨)** | `load_tests/utils/selfhealing/dna_drift.py` | `governance.py`, `l2_storage.py` | Stage DNA 누락 감지 |
| **Discovery Stage** | `load_tests/utils/selfhealing/dna_discovery.py` | 없음 | 테스트 누락 자동 탐지 |

#### 기존 Drift API vs 신규 DNA Drift

```
┌─────────────────────────────────────────────────────────────────┐
│  기존 Drift API (l2_storage.py, governance.py)                  │
│  → 런타임 데이터 드리프트 감지 (L2 저장소 동기화 오류 등)       │
│  → 예: has_drift(), get_drift_stats(), trigger_reconcile()       │
├─────────────────────────────────────────────────────────────────┤
│  신규 DNA Drift (dna_drift.py) - 테스트 전용                    │
│  → Stage DNA 선언 vs 코드베이스 불일치 감지                     │
│  → 예: 새 Service 추가되었는데 Stage DNA에 없음 → 경고        │
└─────────────────────────────────────────────────────────────────┘
```

> ℹ️ 기존 `l2_storage.py`의 `has_drift()`는 **런타임 데이터 드리프트**를 감지하고,
> 신규 `dna_drift.py`는 **Stage DNA 선언 드리프트**를 감지합니다. 다른 목적!

---

## 1. 개요

### 1.1 DNA Drift란?

**DNA Drift**는 코드베이스에 새로운 기능(Service, Middleware, Task 등)이 추가되었지만, 해당 기능을 테스트하는 Stage DNA가 없는 상태를 말합니다.

```
┌─────────────────────────────────────────────────────────────────┐
│                        DNA Drift 발생 시나리오                    │
├─────────────────────────────────────────────────────────────────┤
│ 1. 개발자가 새로운 PaymentWebhookHandler 추가                    │
│ 2. 기존 Stage DNA에는 이 핸들러에 대한 정의 없음                 │
│ 3. 테스트 실행 시 해당 핸들러는 검증되지 않음                    │
│ 4. 프로덕션에서 장애 발생 → "왜 테스트에서 못 잡았지?"           │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 목표

| 목표 | 설명 | 비즈니스 가치 |
|-----|------|-------------|
| **테스트 누락률 0%** | 모든 기능이 DNA에 매핑됨 | 품질 보증 |
| **자동 감지** | 수동 검토 불필요 | 개발 속도 향상 |
| **강제 차단** | DNA 없으면 CI/CD 실패 | 프로세스 강제화 |

---

## 2. Unknown Module 가드레일 (Phase 1)

### 2.1 현재 상태

```python
# stage_dna.py 현재 로직
if unknown:
    warnings.append(f"Unknown modules: {', '.join(unknown)}")
```

**문제**: 경고만 출력하고 테스트는 계속 진행됨

### 2.2 Strict Mode 구현

```python
# stage_dna.py 개선된 로직
class StrictModeLevel(Enum):
    """Strict 모드 레벨"""
    OFF = "off"           # 경고만 (기존)
    WARN = "warn"         # 경고 + 로그 기록
    BLOCK = "block"       # 경고 + 테스트 차단
    FATAL = "fatal"       # 경고 + 프로세스 종료


def validate_stage_dna(
    stage_dna: Dict[str, Any],
    strict_mode: StrictModeLevel = StrictModeLevel.WARN,
) -> ValidationResult:
    """
    Stage DNA 검증 (Strict Mode 지원)
    """
    # ... 기존 검증 로직 ...
    
    # Unknown module 처리
    unknown = all_declared - ALL_AVAILABLE_MODULES
    if unknown:
        message = f"Unknown modules: {', '.join(unknown)}"
        warnings.append(message)
        
        if strict_mode == StrictModeLevel.BLOCK:
            raise DNAValidationError(
                f"[BLOCKED] {message}\n"
                f"해결 방법:\n"
                f"1. 27번 매핑 가이드에 새 모듈 추가\n"
                f"2. 해당 모듈의 테스트 케이스 작성\n"
                f"3. ALL_AVAILABLE_MODULES에 등록"
            )
        elif strict_mode == StrictModeLevel.FATAL:
            logger.critical(f"[FATAL] {message}")
            sys.exit(1)
    
    return result


class DNAValidationError(Exception):
    """DNA 검증 실패 예외"""
    pass
```

### 2.3 CI/CD 통합

```yaml
# .github/workflows/dna-validation.yml
name: DNA Validation

on:
  pull_request:
    paths:
      - 'shopping/**'
      - 'load_tests/**'

jobs:
  validate-dna:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Validate Stage DNA
        run: |
          python -m load_tests.utils.selfhealing.stage_dna \
            --dir load_tests/scenarios \
            --strict-mode block
        
      - name: Check for Unknown Modules
        run: |
          python -c "
          from load_tests.utils.selfhealing.stage_dna import validate_all_stages
          results = validate_all_stages(strict=True)
          failed = [r for r in results.values() if not r.is_valid]
          if failed:
              print(f'❌ {len(failed)} stages failed validation')
              for r in failed:
                  print(f'  - {r.stage_name}: {r.warnings}')
              exit(1)
          print('✅ All stages passed DNA validation')
          "
```

---

## 3. DNA Drift 감지 (Phase 2)

### 3.1 스캔 대상

```python
# dna_drift.py

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
```

### 3.2 구현 코드

```python
# load_tests/utils/selfhealing/dna_drift.py
"""
DNA Drift Detection - 코드베이스 스캔 및 DNA 매핑 검증

기능:
1. 코드베이스 전체 스캔
2. Service, View, Task, Middleware 자동 감지
3. 기존 DNA와 비교하여 누락된 항목 리포트
4. 신규 기능 테스트 케이스 생성 제안
"""

import os
import re
import ast
import glob
from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredFeature:
    """발견된 기능"""
    name: str
    feature_type: str  # service, view, task, middleware, signal
    file_path: str
    line_number: int
    docstring: Optional[str] = None
    dependencies: List[str] = field(default_factory=list)
    is_mapped_to_dna: bool = False


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
    
    @property
    def drift_percentage(self) -> float:
        if self.total_features_found == 0:
            return 0.0
        return (self.unmapped_features / self.total_features_found) * 100


class DNADriftDetector:
    """DNA Drift 감지기"""
    
    def __init__(
        self,
        code_dirs: List[str] = None,
        stage_dirs: List[str] = None,
        exclude_patterns: List[str] = None,
    ):
        self.code_dirs = code_dirs or ["shopping/"]
        self.stage_dirs = stage_dirs or ["load_tests/scenarios/"]
        self.exclude_patterns = exclude_patterns or [
            "**/migrations/**",
            "**/tests/**",
            "**/__pycache__/**",
        ]
        
        self.discovered_features: List[DiscoveredFeature] = []
        self.existing_dna_mappings: Set[str] = set()
    
    def scan_codebase(self) -> List[DiscoveredFeature]:
        """코드베이스 전체 스캔"""
        features = []
        
        for code_dir in self.code_dirs:
            for pattern in ["**/*.py"]:
                for file_path in glob.glob(
                    os.path.join(code_dir, pattern),
                    recursive=True
                ):
                    if self._should_exclude(file_path):
                        continue
                    
                    file_features = self._scan_file(file_path)
                    features.extend(file_features)
        
        self.discovered_features = features
        return features
    
    def _should_exclude(self, file_path: str) -> bool:
        """제외 패턴 체크"""
        for pattern in self.exclude_patterns:
            if glob.fnmatch.fnmatch(file_path, pattern):
                return True
        return False
    
    def _scan_file(self, file_path: str) -> List[DiscoveredFeature]:
        """단일 파일 스캔"""
        features = []
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            tree = ast.parse(content)
            
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    feature = self._analyze_class(node, file_path)
                    if feature:
                        features.append(feature)
                
                elif isinstance(node, ast.FunctionDef):
                    feature = self._analyze_function(node, file_path)
                    if feature:
                        features.append(feature)
        
        except Exception as e:
            logger.warning(f"Failed to scan {file_path}: {e}")
        
        return features
    
    def _analyze_class(
        self,
        node: ast.ClassDef,
        file_path: str
    ) -> Optional[DiscoveredFeature]:
        """클래스 분석"""
        name = node.name
        
        # 타입 판별
        feature_type = None
        if name.endswith("Service"):
            feature_type = "service"
        elif name.endswith("Handler"):
            feature_type = "handler"
        elif name.endswith("View") or name.endswith("ViewSet"):
            feature_type = "view"
        elif name.endswith("Middleware"):
            feature_type = "middleware"
        elif name.endswith("Task"):
            feature_type = "task"
        
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
    
    def _analyze_function(
        self,
        node: ast.FunctionDef,
        file_path: str
    ) -> Optional[DiscoveredFeature]:
        """함수 분석 (Celery Task 등)"""
        # 데코레이터 확인
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
                    feature_type="task",
                    file_path=file_path,
                    line_number=node.lineno,
                    docstring=ast.get_docstring(node),
                )
        
        return None
    
    def load_existing_dna(self) -> Set[str]:
        """기존 DNA 매핑 로드"""
        mappings = set()
        
        for stage_dir in self.stage_dirs:
            for file_path in glob.glob(
                os.path.join(stage_dir, "**/*.py"),
                recursive=True
            ):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # STAGE_DNA 파싱
                    if "STAGE_DNA" in content:
                        tree = ast.parse(content)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Assign):
                                for target in node.targets:
                                    if (isinstance(target, ast.Name) and 
                                        target.id == "STAGE_DNA"):
                                        # DNA에서 테스트 대상 추출
                                        # (실제 구현에서는 더 정교한 파싱 필요)
                                        pass
                except Exception as e:
                    logger.warning(f"Failed to load DNA from {file_path}: {e}")
        
        self.existing_dna_mappings = mappings
        return mappings
    
    def detect_drift(self) -> DriftReport:
        """DNA Drift 감지"""
        if not self.discovered_features:
            self.scan_codebase()
        
        if not self.existing_dna_mappings:
            self.load_existing_dna()
        
        # 매핑 확인
        unmapped = []
        for feature in self.discovered_features:
            if feature.name not in self.existing_dna_mappings:
                feature.is_mapped_to_dna = False
                unmapped.append(feature)
            else:
                feature.is_mapped_to_dna = True
        
        # Deprecated DNA 확인 (코드에 없는 DNA)
        discovered_names = {f.name for f in self.discovered_features}
        deprecated = [
            name for name in self.existing_dna_mappings
            if name not in discovered_names
        ]
        
        # 추천사항 생성
        recommendations = []
        if unmapped:
            recommendations.append(
                f"⚠️ {len(unmapped)}개의 기능이 DNA에 매핑되지 않았습니다."
            )
            for feature in unmapped[:5]:  # 상위 5개만
                recommendations.append(
                    f"  - {feature.name} ({feature.feature_type}) "
                    f"@ {feature.file_path}:{feature.line_number}"
                )
        
        if deprecated:
            recommendations.append(
                f"🗑️ {len(deprecated)}개의 DNA가 더 이상 코드에 없습니다."
            )
        
        from datetime import datetime
        
        return DriftReport(
            scan_timestamp=datetime.now().isoformat(),
            total_features_found=len(self.discovered_features),
            mapped_features=len(self.discovered_features) - len(unmapped),
            unmapped_features=len(unmapped),
            new_features=unmapped,
            deprecated_dna=deprecated,
            recommendations=recommendations,
        )
    
    def generate_dna_suggestion(
        self,
        feature: DiscoveredFeature
    ) -> str:
        """새 기능에 대한 DNA 제안 생성"""
        stage_type = "integration"  # 기본값
        required_modules = ["health"]
        
        # 타입별 기본 모듈 추천
        if feature.feature_type == "service":
            required_modules.extend(["circuit_breaker", "error_budget"])
        elif feature.feature_type == "handler":
            required_modules.extend(["circuit_breaker", "dlq"])
        elif feature.feature_type == "task":
            required_modules.extend(["dlq", "observability"])
            stage_type = "integration"
        elif feature.feature_type == "view":
            required_modules.extend(["rate_limiter", "auth"])
        
        template = f'''
# 자동 생성된 DNA 제안 - {feature.name}
# 파일: {feature.file_path}:{feature.line_number}

STAGE_DNA = {{
    "name": "Stage XX - {feature.name} Test",
    "type": "{stage_type}",
    "required_modules": {sorted(set(required_modules))},
    "optional_modules": ["observability"],
    
    # 안전 기능
    "blast_radius": "isolated",
    "rollback_strategy": "automatic",
}}
'''
        return template


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(description="DNA Drift Detector")
    parser.add_argument(
        "--code-dir",
        default="shopping/",
        help="스캔할 코드 디렉토리",
    )
    parser.add_argument(
        "--stage-dir",
        default="load_tests/scenarios/",
        help="Stage 파일 디렉토리",
    )
    parser.add_argument(
        "--output",
        default="drift_report.json",
        help="리포트 출력 파일",
    )
    parser.add_argument(
        "--suggest",
        action="store_true",
        help="누락된 기능에 대한 DNA 제안 생성",
    )
    
    args = parser.parse_args()
    
    detector = DNADriftDetector(
        code_dirs=[args.code_dir],
        stage_dirs=[args.stage_dir],
    )
    
    report = detector.detect_drift()
    
    print("\n" + "=" * 60)
    print("📊 DNA Drift Report")
    print("=" * 60)
    print(f"스캔 시간: {report.scan_timestamp}")
    print(f"발견된 기능: {report.total_features_found}")
    print(f"매핑된 기능: {report.mapped_features}")
    print(f"누락된 기능: {report.unmapped_features}")
    print(f"Drift 비율: {report.drift_percentage:.1f}%")
    print()
    
    for rec in report.recommendations:
        print(rec)
    
    if args.suggest and report.new_features:
        print("\n" + "=" * 60)
        print("💡 DNA Suggestions")
        print("=" * 60)
        for feature in report.new_features[:3]:
            suggestion = detector.generate_dna_suggestion(feature)
            print(suggestion)


if __name__ == "__main__":
    main()
```

---

## 4. Discovery Stage (Phase 2)

### 4.1 개념

**Discovery Stage**는 정기적으로 시스템을 탐험하여 테스트되지 않는 "버려진 코드"를 찾아내는 특수 Stage입니다.

```
┌─────────────────────────────────────────────────────────────────┐
│                     Discovery Stage 동작 흐름                    │
├─────────────────────────────────────────────────────────────────┤
│ 1. 모든 API 엔드포인트 수집                                      │
│ 2. 모든 Stage DNA에서 테스트 대상 수집                           │
│ 3. 엔드포인트 vs DNA 매핑 비교                                   │
│ 4. 테스트되지 않는 엔드포인트 리스트 생성                        │
│ 5. 자동 테스트 케이스 생성 제안                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 구현 코드

```python
# load_tests/utils/selfhealing/dna_discovery.py
"""
Discovery Stage - 테스트 커버리지 갭 분석

기능:
1. API 엔드포인트 전수 조사
2. 테스트 커버리지 매핑
3. Dead Code 감지
4. 자동 테스트 생성
"""

import os
import re
import ast
import json
import glob
from typing import Dict, List, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class APIEndpoint:
    """API 엔드포인트"""
    path: str
    method: str  # GET, POST, PUT, DELETE, PATCH
    view_class: str
    view_function: str
    file_path: str
    line_number: int
    is_tested: bool = False
    test_stages: List[str] = field(default_factory=list)


@dataclass
class DiscoveryReport:
    """Discovery 결과 리포트"""
    discovery_timestamp: str
    total_endpoints: int
    tested_endpoints: int
    untested_endpoints: int
    dead_code_candidates: List[str]
    coverage_percentage: float
    recommendations: List[str]
    endpoint_details: List[APIEndpoint]


class DiscoveryStage:
    """
    Discovery Stage - 시스템 탐험 및 갭 분석
    """
    
    def __init__(
        self,
        project_root: str = ".",
        urls_patterns: List[str] = None,
        stage_dirs: List[str] = None,
    ):
        self.project_root = project_root
        self.urls_patterns = urls_patterns or [
            "**/urls.py",
            "**/router.py",
        ]
        self.stage_dirs = stage_dirs or ["load_tests/scenarios/"]
        
        self.endpoints: List[APIEndpoint] = []
        self.tested_paths: Set[str] = set()
    
    def discover_endpoints(self) -> List[APIEndpoint]:
        """모든 API 엔드포인트 발견"""
        endpoints = []
        
        for pattern in self.urls_patterns:
            for file_path in glob.glob(
                os.path.join(self.project_root, pattern),
                recursive=True
            ):
                file_endpoints = self._parse_urls_file(file_path)
                endpoints.extend(file_endpoints)
        
        self.endpoints = endpoints
        return endpoints
    
    def _parse_urls_file(self, file_path: str) -> List[APIEndpoint]:
        """urls.py 파일 파싱"""
        endpoints = []
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()
            
            # path(), re_path() 패턴 매칭
            path_pattern = re.compile(
                r"path\s*\(\s*['\"]([^'\"]+)['\"].*?(\w+)\.as_view\(\)",
                re.MULTILINE | re.DOTALL
            )
            
            for match in path_pattern.finditer(content):
                path = match.group(1)
                view_class = match.group(2)
                
                endpoints.append(APIEndpoint(
                    path=path,
                    method="*",  # 실제 메서드는 View에서 확인 필요
                    view_class=view_class,
                    view_function="",
                    file_path=file_path,
                    line_number=content[:match.start()].count("\n") + 1,
                ))
            
            # router.register() 패턴 (DRF)
            router_pattern = re.compile(
                r"router\.register\s*\(\s*['\"]([^'\"]+)['\"].*?(\w+)",
                re.MULTILINE
            )
            
            for match in router_pattern.finditer(content):
                path = match.group(1)
                viewset = match.group(2)
                
                endpoints.append(APIEndpoint(
                    path=path,
                    method="*",
                    view_class=viewset,
                    view_function="",
                    file_path=file_path,
                    line_number=content[:match.start()].count("\n") + 1,
                ))
        
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
        
        return endpoints
    
    def collect_tested_paths(self) -> Set[str]:
        """Stage에서 테스트하는 경로 수집"""
        tested = set()
        
        for stage_dir in self.stage_dirs:
            for file_path in glob.glob(
                os.path.join(stage_dir, "**/*.py"),
                recursive=True
            ):
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    
                    # API 호출 패턴 매칭
                    patterns = [
                        r'self\.client\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                        r'response\s*=.*\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']',
                        r'requests\.(get|post|put|delete|patch)\s*\([^)]*["\']([^"\']+)["\']',
                    ]
                    
                    for pattern in patterns:
                        for match in re.finditer(pattern, content):
                            path = match.group(2)
                            # 경로 정규화
                            path = re.sub(r'\{[^}]+\}', '*', path)  # {id} → *
                            path = re.sub(r'\d+', '*', path)  # 123 → *
                            tested.add(path)
                
                except Exception as e:
                    logger.warning(f"Failed to analyze {file_path}: {e}")
        
        self.tested_paths = tested
        return tested
    
    def analyze_coverage(self) -> DiscoveryReport:
        """커버리지 분석"""
        if not self.endpoints:
            self.discover_endpoints()
        
        if not self.tested_paths:
            self.collect_tested_paths()
        
        # 매핑 분석
        untested = []
        for endpoint in self.endpoints:
            # 경로 정규화
            normalized_path = re.sub(r'<[^>]+>', '*', endpoint.path)
            
            if normalized_path in self.tested_paths or endpoint.path in self.tested_paths:
                endpoint.is_tested = True
            else:
                endpoint.is_tested = False
                untested.append(endpoint)
        
        tested_count = len(self.endpoints) - len(untested)
        coverage = (tested_count / max(len(self.endpoints), 1)) * 100
        
        # Dead code 후보
        dead_code = [
            e.view_class for e in untested
            if not any(x in e.view_class.lower() for x in ["admin", "debug", "internal"])
        ]
        
        # 추천사항
        recommendations = []
        if untested:
            recommendations.append(
                f"⚠️ {len(untested)}개의 엔드포인트가 테스트되지 않습니다."
            )
            recommendations.append("다음 엔드포인트에 대한 Stage DNA를 추가하세요:")
            for ep in untested[:5]:
                recommendations.append(f"  - {ep.method} {ep.path} ({ep.view_class})")
        
        if coverage < 80:
            recommendations.append(
                f"🎯 테스트 커버리지가 {coverage:.1f}%입니다. 80% 이상을 권장합니다."
            )
        
        return DiscoveryReport(
            discovery_timestamp=datetime.now().isoformat(),
            total_endpoints=len(self.endpoints),
            tested_endpoints=tested_count,
            untested_endpoints=len(untested),
            dead_code_candidates=dead_code,
            coverage_percentage=coverage,
            recommendations=recommendations,
            endpoint_details=self.endpoints,
        )
    
    def generate_stage_skeleton(
        self,
        endpoint: APIEndpoint,
        stage_type: str = "integration"
    ) -> str:
        """테스트되지 않은 엔드포인트에 대한 Stage 스켈레톤 생성"""
        
        stage_name = f"stage_auto_{endpoint.view_class.lower()}"
        
        return f'''"""
Auto-generated Stage for {endpoint.view_class}

Target Endpoint: {endpoint.method} {endpoint.path}
Generated: {datetime.now().isoformat()}

TODO: 이 스켈레톤을 실제 테스트로 완성하세요.
"""

STAGE_DNA = {{
    "name": "Stage Auto - {endpoint.view_class} Test",
    "type": "{stage_type}",
    "required_modules": ["health", "circuit_breaker"],
    "optional_modules": ["observability"],
    
    # 자동 생성 표시
    "auto_generated": True,
    "source_endpoint": "{endpoint.path}",
}}

from load_tests.utils.selfhealing.stage_dna import validate_stage_dna
_dna_result = validate_stage_dna(STAGE_DNA)

from locust import HttpUser, task, between

class {endpoint.view_class}TestUser(HttpUser):
    """Auto-generated test user for {endpoint.view_class}"""
    
    wait_time = between(1, 3)
    
    @task
    def test_endpoint(self):
        """TODO: 실제 테스트 로직 구현"""
        # self.client.get("{endpoint.path}")
        pass
'''


# =============================================================================
# 정기 실행 스케줄러
# =============================================================================

class DiscoveryScheduler:
    """Discovery 정기 실행 스케줄러"""
    
    def __init__(self, discovery: DiscoveryStage):
        self.discovery = discovery
        self.history: List[DiscoveryReport] = []
    
    def run_weekly_discovery(self) -> DiscoveryReport:
        """주간 Discovery 실행"""
        report = self.discovery.analyze_coverage()
        self.history.append(report)
        
        # 추이 분석
        if len(self.history) >= 2:
            prev = self.history[-2]
            curr = self.history[-1]
            
            diff = curr.coverage_percentage - prev.coverage_percentage
            if diff < 0:
                report.recommendations.append(
                    f"📉 커버리지가 {abs(diff):.1f}% 감소했습니다!"
                )
            elif diff > 0:
                report.recommendations.append(
                    f"📈 커버리지가 {diff:.1f}% 증가했습니다."
                )
        
        return report
    
    def save_report(self, report: DiscoveryReport, path: str):
        """리포트 저장"""
        data = {
            "timestamp": report.discovery_timestamp,
            "summary": {
                "total": report.total_endpoints,
                "tested": report.tested_endpoints,
                "untested": report.untested_endpoints,
                "coverage": report.coverage_percentage,
            },
            "dead_code": report.dead_code_candidates,
            "recommendations": report.recommendations,
        }
        
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# =============================================================================
# CLI 인터페이스
# =============================================================================

def main():
    """CLI 엔트리포인트"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Discovery Stage")
    parser.add_argument(
        "--project-root",
        default=".",
        help="프로젝트 루트 디렉토리",
    )
    parser.add_argument(
        "--output",
        default="discovery_report.json",
        help="리포트 출력 파일",
    )
    parser.add_argument(
        "--generate-skeletons",
        action="store_true",
        help="누락된 엔드포인트에 대한 Stage 스켈레톤 생성",
    )
    
    args = parser.parse_args()
    
    discovery = DiscoveryStage(project_root=args.project_root)
    report = discovery.analyze_coverage()
    
    print("\n" + "=" * 60)
    print("🔍 Discovery Stage Report")
    print("=" * 60)
    print(f"발견 시간: {report.discovery_timestamp}")
    print(f"총 엔드포인트: {report.total_endpoints}")
    print(f"테스트됨: {report.tested_endpoints}")
    print(f"테스트 안됨: {report.untested_endpoints}")
    print(f"커버리지: {report.coverage_percentage:.1f}%")
    print()
    
    for rec in report.recommendations:
        print(rec)
    
    if args.generate_skeletons:
        print("\n" + "=" * 60)
        print("📝 Generated Skeletons")
        print("=" * 60)
        for endpoint in report.endpoint_details:
            if not endpoint.is_tested:
                skeleton = discovery.generate_stage_skeleton(endpoint)
                print(skeleton)
                print("-" * 40)


if __name__ == "__main__":
    main()
```

---

## 5. 통합 워크플로우

### 5.1 CI/CD 파이프라인

```yaml
# .github/workflows/dna-full-check.yml
name: DNA Full Check

on:
  schedule:
    - cron: '0 0 * * 0'  # 매주 일요일 00:00
  push:
    branches: [main, develop]

jobs:
  dna-drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: DNA Drift Detection
        run: |
          python -m load_tests.utils.selfhealing.dna_drift \
            --code-dir shopping/ \
            --output drift_report.json
      
      - name: Check Drift Threshold
        run: |
          python -c "
          import json
          with open('drift_report.json') as f:
              report = json.load(f)
          drift = report['unmapped_features'] / max(report['total_features'], 1) * 100
          if drift > 10:
              print(f'❌ Drift {drift:.1f}% exceeds 10% threshold')
              exit(1)
          print(f'✅ Drift {drift:.1f}% is acceptable')
          "
  
  discovery:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      
      - name: Discovery Stage
        run: |
          python -m load_tests.utils.selfhealing.dna_discovery \
            --project-root . \
            --output discovery_report.json
      
      - name: Check Coverage
        run: |
          python -c "
          import json
          with open('discovery_report.json') as f:
              report = json.load(f)
          coverage = report['summary']['coverage']
          if coverage < 80:
              print(f'⚠️ Coverage {coverage:.1f}% below 80% target')
              # 경고만, 실패는 아님
          else:
              print(f'✅ Coverage {coverage:.1f}%')
          "
      
      - name: Upload Reports
        uses: actions/upload-artifact@v4
        with:
          name: dna-reports
          path: |
            drift_report.json
            discovery_report.json
```

### 5.2 Pre-commit Hook

```bash
#!/bin/bash
# .git/hooks/pre-commit

echo "🧬 Running DNA Validation..."

# Strict mode로 DNA 검증
python -m load_tests.utils.selfhealing.stage_dna \
    --dir load_tests/scenarios \
    --strict

if [ $? -ne 0 ]; then
    echo "❌ DNA Validation failed. Commit blocked."
    exit 1
fi

echo "✅ DNA Validation passed."
```

---

## 6. 요약

| 기능 | 파일 | 상태 | 비즈니스 가치 |
|-----|------|------|-------------|
| Unknown Module Guard | `stage_dna.py` | Phase 1 | 코드 품질 보장 |
| DNA Drift Detection | `dna_drift.py` | Phase 2 | 테스트 누락 방지 |
| Discovery Stage | `dna_discovery.py` | Phase 2 | 커버리지 갭 분석 |
| Auto Skeleton Gen | `dna_discovery.py` | Phase 2 | 개발 속도 향상 |

---

## 7. 다음 단계

1. [31번 문서](31_DNA_ADVANCED_FEATURES.md) - Mutation DNA, Dependency Graph
2. [32번 문서](32_DNA_ENTERPRISE_FEATURES.md) - FinOps, Compliance
3. [33번 문서](33_DNA_SAFETY_FEATURES.md) - Rollback, Blast Radius

---

**작성자**: GitHub Copilot (Claude Opus 4.5)  
**검토자**: System Architect  
**승인일**: 2025-12-29

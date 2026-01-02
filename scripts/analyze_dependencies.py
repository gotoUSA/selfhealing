#!/usr/bin/env python3
"""
Self-Healing 코드 의존성 분석 스크립트
- 모듈 간 import 관계 분석
- 고아 모듈 식별
- 가장 많이 참조되는 모듈/의존성이 많은 모듈 계산
"""

import ast
import os
from pathlib import Path
from collections import defaultdict
import json

# selfhealing 패키지 경로
PACKAGE_ROOT = Path(__file__).parent.parent / "packages" / "selfhealing-python" / "src" / "selfhealing"


def get_module_name(filepath: Path) -> str:
    """파일 경로를 모듈명으로 변환"""
    rel_path = filepath.relative_to(PACKAGE_ROOT)
    parts = list(rel_path.parts)
    # .py 확장자 제거
    if parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # __init__ 처리
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) if parts else "__init__"


def extract_imports(filepath: Path) -> list:
    """AST를 사용하여 import문 추출"""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
    except:
        return []

    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    return imports


def normalize_import(imp: str) -> str:
    """selfhealing 패키지 내부 import를 정규화"""
    # selfhealing.xxx -> xxx
    if imp.startswith("selfhealing."):
        return imp[12:]
    # 상대 import는 이미 처리됨
    return imp


def is_internal_import(imp: str) -> bool:
    """selfhealing 내부 import인지 확인"""
    return (
        imp.startswith("selfhealing.")
        or not "." in imp
        or imp.split(".")[0]
        in [
            "adapters",
            "api",
            "audit",
            "config",
            "context",
            "core",
            "factory",
            "interfaces",
            "metrics",
            "models",
            "resilience",
            "services",
            "tasks",
            "utils",
            "config_tracker",
            "slo",
        ]
    )


def main():
    # 모든 Python 파일 수집
    all_files = list(PACKAGE_ROOT.rglob("*.py"))
    all_files = [f for f in all_files if "__pycache__" not in str(f)]

    print(f"📊 분석 대상: {len(all_files)}개 파일")

    # 모듈 이름 -> 파일 경로 매핑
    modules = {}
    for f in all_files:
        mod_name = get_module_name(f)
        modules[mod_name] = f

    print(f"📦 전체 모듈 수: {len(modules)}개")

    # 의존성 그래프 구축
    dependencies = defaultdict(set)  # module -> [imports...]
    reverse_deps = defaultdict(set)  # module -> [imported by...]

    for mod_name, filepath in modules.items():
        imports = extract_imports(filepath)
        for imp in imports:
            norm_imp = normalize_import(imp)
            # 내부 모듈만 추적
            if is_internal_import(imp):
                # 최상위 모듈로 정규화
                parts = norm_imp.split(".")
                # 다양한 길이로 매칭 시도
                for i in range(len(parts), 0, -1):
                    candidate = ".".join(parts[:i])
                    if candidate in modules and candidate != mod_name:
                        dependencies[mod_name].add(candidate)
                        reverse_deps[candidate].add(mod_name)
                        break

    # 의존성 있는 모듈 수
    has_deps = sum(1 for m in modules if dependencies[m])
    print(f"🔗 의존성이 있는 모듈: {has_deps}개")

    # 고아 모듈 (다른 모듈에서 import되지 않음)
    orphans = [m for m in modules if not reverse_deps[m]]
    print(f"🏝️  고아 모듈: {len(orphans)}개")

    # 가장 많이 참조되는 모듈 (Top 20)
    ref_counts = [(m, len(reverse_deps[m])) for m in modules if reverse_deps[m]]
    ref_counts.sort(key=lambda x: -x[1])

    print("\n🏆 가장 많이 참조되는 모듈 (Top 20):")
    print("-" * 50)
    for i, (mod, count) in enumerate(ref_counts[:20], 1):
        print(f"  {i:2}. {mod:<45} {count}회")

    # 가장 많은 의존성을 가진 모듈 (Top 15)
    dep_counts = [(m, len(dependencies[m])) for m in modules if dependencies[m]]
    dep_counts.sort(key=lambda x: -x[1])

    print("\n📈 가장 많은 의존성을 가진 모듈 (Top 15):")
    print("-" * 50)
    for i, (mod, count) in enumerate(dep_counts[:15], 1):
        print(f"  {i:2}. {mod:<45} {count}개")

    # 고아 모듈 분류
    print("\n⚠️  고아 모듈 분류:")
    print("-" * 50)

    # 카테고리별 분류
    entry_points = []
    migrations = []
    admin_apps = []
    tests = []
    check_needed = []

    for m in sorted(orphans):
        if "migrations" in m:
            migrations.append(m)
        elif "admin" in m or "apps" in m:
            admin_apps.append(m)
        elif "urls" in m or "middleware" in m or "routes" in m:
            entry_points.append(m)
        elif "tasks" in m:
            entry_points.append(m)
        elif any(x in m for x in ["null_adapter", "stdout_adapter", "file_adapter"]):
            check_needed.append(m)
        else:
            check_needed.append(m)

    print(f"\n  📍 엔트리포인트/미들웨어: {len(entry_points)}개")
    for m in entry_points[:10]:
        print(f"      - {m}")
    if len(entry_points) > 10:
        print(f"      ... 외 {len(entry_points) - 10}개")

    print(f"\n  📋 Django Admin/Apps: {len(admin_apps)}개")
    for m in admin_apps:
        print(f"      - {m}")

    print(f"\n  🔄 마이그레이션: {len(migrations)}개")

    print(f"\n  ⚠️  검토 필요: {len(check_needed)}개")
    for m in check_needed:
        print(f"      - {m}")

    # JSON 출력 (문서 생성용)
    result = {
        "total_modules": len(modules),
        "has_dependencies": has_deps,
        "orphan_count": len(orphans),
        "top_referenced": ref_counts[:20],
        "top_dependencies": dep_counts[:15],
        "orphans": sorted(orphans),
        "orphan_categories": {
            "entry_points": entry_points,
            "migrations": migrations,
            "admin_apps": admin_apps,
            "check_needed": check_needed,
        },
    }

    output_path = Path(__file__).parent.parent / "docs" / "self_healing" / "dependency_analysis_result.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\n✅ 결과 저장: {output_path}")


if __name__ == "__main__":
    main()

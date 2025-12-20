# Code Analysis Tools

프로젝트에서 사용하는 코드 분석 도구 목록 및 사용법.

---

## Formatting

### Black (Code Formatter)
```bash
# 포맷팅 적용
black shopping/

# 확인만 (수정 안함)
black --check shopping/
```

### isort (Import Sorter)
```bash
# import 정렬 적용
isort shopping/

# 확인만
isort --check-only shopping/
```

---

## Linting

### Ruff (Fast Linter)
```bash
# 린팅 체크
ruff check shopping/

# 자동 수정
ruff check --fix shopping/
```

### mypy (Type Checker)
```bash
# 타입 체크 (selfhealing 패키지)
mypy packages/selfhealing-python/src/selfhealing/
```

---

## Security

### Bandit (Security Linter)
```bash
# 보안 취약점 스캔
bandit -r shopping/ -c pyproject.toml
```

---

## Code Quality

### Radon (Complexity Analysis)
```bash
# 순환 복잡도 분석
radon cc shopping/ -a -s

# 유지보수성 지수
radon mi shopping/ -s
```

### Vulture (Dead Code Detection)
```bash
# 사용되지 않는 코드 탐지
vulture shopping/ --min-confidence 80
```

---

## Testing

### pytest
```bash
# 전체 테스트
pytest

# 특정 마커
pytest -m unit
pytest -m integration
pytest -m "not slow"

# 커버리지 포함
pytest --cov=shopping --cov-report=html
```

---

## 설정 파일

모든 도구 설정은 `pyproject.toml`에 통합되어 있음.

| 도구 | 설정 섹션 |
|------|----------|
| Black | `[tool.black]` |
| isort | `[tool.isort]` |
| pytest | `[tool.pytest.ini_options]` |
| coverage | `[tool.coverage.*]` |
| Radon | `[tool.radon]` |
| Bandit | `[tool.bandit]` |
| Vulture | `[tool.vulture]` |
| Ruff | `[tool.ruff]` (selfhealing 패키지) |
| mypy | `[tool.mypy]` (selfhealing 패키지) |

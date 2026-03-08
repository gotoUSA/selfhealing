# 327. CI/CD and Deployment — 배포 전략 및 CI 분리

> **Status**: Planning
> **Severity**: P2 (MEDIUM)
> **Target**: `.github/workflows/` (양쪽 repo)
> **References**:
> - 319 — Repo Separation Overview (Step 8)
> - 326 — Repo Split Execution

---

## 1. CI 분리

### 1.1 selfhealing repo CI

```yaml
# .github/workflows/ci.yml
name: selfhealing CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install ruff mypy
      - run: ruff check src/ tests/
      - run: ruff format --check src/ tests/
      - run: mypy src/selfhealing/

  unit-test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: pip install -e ".[dev,django,celery,prometheus]"
      - run: pytest tests/unit/ -x --tb=short
        env:
          DJANGO_SETTINGS_MODULE: tests.testapp.settings

  integration-test:
    runs-on: ubuntu-latest
    services:
      redis:
        image: redis:7
        ports:
          - 6379:6379
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev,django,celery,prometheus,redis]"
      - run: pytest tests/integration/ -x --tb=short -m "not kafka"
        env:
          DJANGO_SETTINGS_MODULE: tests.testapp.settings
          SELFHEALING_REDIS_URL: redis://localhost:6379/0

  wiring-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e ".[dev]"
      - run: python scripts/verify_wiring.py
```

### 1.2 shopping repo CI

```yaml
# .github/workflows/ci.yml
name: shopping CI

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:15
        env:
          POSTGRES_DB: testdb
          POSTGRES_USER: testuser
          POSTGRES_PASSWORD: testpass
        ports:
          - 5432:5432
      redis:
        image: redis:7
        ports:
          - 6379:6379
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install -e "."
      - run: pytest shopping/tests/ tests/hybrid/ -x --tb=short
        env:
          DJANGO_SETTINGS_MODULE: myproject.settings.test
          DATABASE_URL: postgres://testuser:testpass@localhost:5432/testdb
```

---

## 2. 배포 전략 (단계별)

### 2.1 Phase 1: Git Dependency (즉시 적용)

```toml
# shopping/pyproject.toml
[project]
dependencies = [
    "selfhealing[django,celery,prometheus] @ git+https://github.com/USER/selfhealing-python.git@main",
]
```

**특정 버전 고정**:
```toml
    "selfhealing[...] @ git+https://github.com/USER/selfhealing-python.git@v0.1.0",
```

**장점**: 설정 간단, 즉시 사용 가능
**단점**: pip install 시 git clone 필요 (느림), 캐시 어려움

### 2.2 Phase 2: GitHub Releases + Wheel (안정화 후)

```bash
# selfhealing repo에서 빌드 + 릴리스
pip install build
python -m build  # dist/selfhealing-0.1.0-py3-none-any.whl 생성
gh release create v0.1.0 dist/*.whl --title "v0.1.0" --notes "Initial release"
```

```toml
# shopping/pyproject.toml
[project]
dependencies = [
    "selfhealing[django,celery,prometheus] @ https://github.com/USER/selfhealing-python/releases/download/v0.1.0/selfhealing-0.1.0-py3-none-any.whl",
]
```

**장점**: 빠른 설치 (wheel 다운로드만), 버전 고정 명확
**단점**: 수동 릴리스 프로세스

### 2.3 Phase 3: Private PyPI (필요 시)

```bash
# TestPyPI 또는 self-hosted PyPI
pip install twine
twine upload --repository testpypi dist/*
```

```toml
# shopping/pyproject.toml
[project]
dependencies = [
    "selfhealing[django,celery,prometheus]>=0.1.0",
]

# pip.conf 또는 pyproject.toml
[[tool.uv.index]]
url = "https://test.pypi.org/simple/"
```

---

## 3. 버전 관리

### 3.1 Semantic Versioning

```
MAJOR.MINOR.PATCH

MAJOR: Public API 호환 깨짐 (__init__.py exports 변경)
MINOR: 새 기능 추가 (하위 호환)
PATCH: 버그 수정
```

### 3.2 자동 릴리스 (GitHub Actions)

```yaml
# .github/workflows/release.yml
name: Release

on:
  push:
    tags:
      - "v*"

jobs:
  build-and-release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install build
      - run: python -m build
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
```

---

## 4. 로컬 개발 워크플로

selfhealing과 shopping을 동시에 개발할 때:

```bash
# 1. selfhealing repo 클론
cd ~/projects
git clone https://github.com/USER/selfhealing-python.git

# 2. shopping repo에서 selfhealing을 editable로 설치
cd ~/projects/shopping
pip install -e ../selfhealing-python[django,celery,prometheus]

# 3. selfhealing 코드 수정 → shopping에서 즉시 반영 (editable)
```

**docker-compose에서 로컬 개발**:
```yaml
# docker-compose.override.yml (gitignore)
services:
  web:
    volumes:
      - ../selfhealing-python/src:/code/selfhealing-src
    environment:
      - PYTHONPATH=/code:/code/selfhealing-src
```

---

## 5. 호환성 테스트 자동화

selfhealing 릴리스 시 shopping CI에서 호환성 테스트 자동 트리거:

```yaml
# shopping/.github/workflows/compat-test.yml
name: Compatibility Test

on:
  repository_dispatch:
    types: [selfhealing-release]
  workflow_dispatch:
    inputs:
      selfhealing_version:
        description: "selfhealing version to test"
        required: true

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install "selfhealing[django,celery]==${{ github.event.client_payload.version || github.event.inputs.selfhealing_version }}"
      - run: pytest shopping/tests/ tests/hybrid/ -x
```

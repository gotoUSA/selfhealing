# 326. Repo Split Execution — 물리적 분리 실행

> **Status**: Planning
> **Severity**: P1 (HIGH)
> **Target**: 2개 독립 repo 생성
> **References**:
> - 319 — Repo Separation Overview (Step 7)
> - 320~325 — 선행 작업 완료 후 실행

---

## 1. 전제 조건

이 문서의 작업은 **320~325 모두 완료 후** 실행한다.

체크리스트:
- [ ] 320: AppConfig auto-config 구현 완료
- [ ] 321: Celery Beat schedule 내부화 완료
- [ ] 322: Gunicorn hook helper 구현 완료
- [ ] 323: Public API 정의 완료
- [ ] 324: testapp 생성 + shopping import 37파일 전환 완료
- [ ] 325: examples/ 디렉토리 생성 완료
- [ ] 전체 테스트 통과 확인

---

## 2. 분리 방법: 새 Repo 생성 (권장)

### 2.1 왜 git filter-branch를 안 쓰는가

- 1인 개발, pre-production → git 히스토리 보존 가치 낮음
- filter-branch는 복잡하고 실수 위험 높음
- 새 repo 시작이 깔끔하고 안전

> **ADR**: 팀 스케일업(2인 이상) 또는 첫 프로덕션 배포 시점에
> `git filter-repo`를 활용한 히스토리 포함 마이그레이션을 재검토한다.
> 해당 시점에 새 repo의 자체 커밋이 충분히 쌓여 있으면 불필요.

### 2.2 실행 절차

> **Safety Protocol**: Step 1~검증 완료까지 모노레포에 어떠한 변경도 가하지 않는다.
> Step 2(모노레포 정리) 실행 전에 `git tag pre-repo-split`으로 롤백 지점을 확보한다.

#### Step 1: selfhealing-python repo 생성

```bash
# 1. GitHub에 새 repo 생성 (selfhealing-python)
# 2. 로컬에서 새 디렉토리 생성
mkdir ~/selfhealing-python && cd ~/selfhealing-python
git init

# 3. 현재 monorepo에서 파일 복사
cp -r ../myproject/packages/selfhealing-python/* .

# 4. docs 이동
cp -r ../myproject/docs/ ./docs/

# 5. scripts 이동
mkdir scripts
cp ../myproject/scripts/analyze_dependencies.py scripts/
cp ../myproject/scripts/analyze_test_imports.py scripts/
cp ../myproject/scripts/benchmark_storage.py scripts/
cp ../myproject/scripts/verify_wiring.py scripts/
cp ../myproject/scripts/wiring_allowlist.yaml scripts/

# 6. examples 복사 (325에서 이미 생성됨)
# examples/는 이미 packages/selfhealing-python/examples/에 있을 것

# 7. CI 파일 생성
mkdir -p .github/workflows
# ci.yml 작성 (327 참조)

# 8. 최상위 파일
# README.md, LICENSE, .gitignore, CLAUDE.md 등

# 9. Initial commit
git add .
git commit -m "feat: initial selfhealing-python standalone library

Extracted from monorepo (myproject).
Includes src, tests (1018 unit + integration), docs, examples, scripts."

# 10. Remote 추가 및 push
git remote add origin https://github.com/USER/selfhealing-python.git
git push -u origin main
```

#### Step 2: shopping repo 정리

```bash
cd ~/myproject  # 현재 monorepo

# 0. 롤백 지점 확보
git tag pre-repo-split

# 1. packages/ 디렉토리 제거
rm -rf packages/

# 2. docs/ 제거 (selfhealing repo로 이동됨)
rm -rf docs/

# 3. scripts/ 정리 (selfhealing 전용 스크립트 제거)
rm scripts/analyze_dependencies.py
rm scripts/analyze_test_imports.py
rm scripts/benchmark_storage.py
rm scripts/verify_wiring.py
rm scripts/wiring_allowlist.yaml

# 4. tests/ 정리
rm -rf tests/self_healing/
rm -rf tests/integration/selfhealing/
rm -rf tests/api/
rm -rf tests/load/
# tests/hybrid/ 유지, tests/conftest.py 유지

# 5. pyproject.toml 수정 (selfhealing dependency 추가)
# 6. Dockerfile 수정 (packages/ 제거)
# 7. docker-compose.yml 수정 (PYTHONPATH 단순화)
# 8. myproject/celery.py 수정 (320, 321 적용)
# 9. myproject/settings/base.py 수정 (320 적용)
# 10. gunicorn.conf.py 수정 (322 적용)

# 11. Commit
git add -A
git commit -m "refactor: extract selfhealing to standalone library

selfhealing is now installed as pip dependency.
See: https://github.com/USER/selfhealing-python"
```

---

## 3. 파일 이동 상세 매핑

### 3.1 selfhealing repo 최종 구조

```
selfhealing-python/
├── .github/
│   └── workflows/
│       ├── ci.yml                    # lint + unit test
│       └── wiring-check.yml         # 서비스 와이어링 검증
├── src/selfhealing/                  # 소스코드 (변경 최소)
├── tests/
│   ├── unit/                         # 기존 1,018 유닛 테스트
│   ├── integration/                  # tests/self_healing/ + tests/api/ 이동
│   ├── chaos/                        # tests/self_healing/chaos/ 분리
│   ├── load/                         # tests/load/ 이동
│   └── testapp/                      # 최소 Django 테스트용 앱
├── docs/                             # 전체 문서
│   ├── laws/
│   ├── self_healing/
│   └── ...
├── examples/                         # 참고용 설정 예시
│   ├── django-setup/
│   ├── k8s/
│   ├── monitoring/
│   └── docker/
├── scripts/                          # 분석 스크립트
├── pyproject.toml                    # Hatchling (기존)
├── CLAUDE.md                         # AI 어시스턴트 지침 (수정)
├── README.md
├── LICENSE
└── .gitignore
```

### 3.2 shopping repo 최종 구조

```
shopping/                             # (현재 myproject 이름 유지 또는 변경)
├── .github/workflows/ci.yml
├── shopping/                         # 앱 코드 (변경 없음)
├── myproject/
│   ├── settings/                     # 수정됨 (320 적용)
│   ├── celery.py                     # 수정됨 (321 적용)
│   ├── urls.py                       # 변경 없음
│   ├── wsgi.py                       # 수정됨 (get_config 제거)
│   └── middleware/                   # 변경 없음
├── tests/
│   ├── hybrid/                       # shopping+selfhealing 결합 테스트
│   ├── conftest.py
│   └── factories/
├── k8s/                              # 배포 매니페스트 (변경 없음)
├── docker/                           # 모니터링 설정 (변경 없음)
├── selfhealing/                      # Django services (healing_events_store.py)
├── Dockerfile                        # 수정됨 (packages/ 제거)
├── docker-compose.yml                # 수정됨 (PYTHONPATH 단순화)
├── gunicorn.conf.py                  # 수정됨 (322 적용)
├── pyproject.toml                    # 수정됨 (selfhealing dependency 추가)
├── .env.example                      # 변경 없음
└── Makefile
```

---

## 4. pyproject.toml 수정

### 4.1 shopping repo pyproject.toml

```toml
[project]
name = "shopping"
dependencies = [
    # selfhealing 라이브러리 — Phase 1: Git dependency (임시)
    # Phase 2(GitHub Releases .whl) / Phase 3(Private PyPI)으로 전환 예정, 327 참조
    "selfhealing[django,celery,prometheus] @ git+https://github.com/USER/selfhealing-python.git@v0.1.0",
    # 기존 dependencies...
    "django>=5.2",
    "djangorestframework>=3.15",
    "celery>=5.4",
    # ...
]

[tool.pytest.ini_options]
testpaths = ["shopping/tests", "tests"]
# packages/selfhealing-python 경로 제거

[tool.coverage.run]
source = ["shopping", "myproject"]
# selfhealing coverage 제거 (별도 repo에서 측정)
```

### 4.2 Dockerfile 수정

```dockerfile
# Before
COPY packages/ /code/packages/
RUN pip install --no-cache-dir -e /code/packages/selfhealing-python
ENV PYTHONPATH=/code:/code/packages/selfhealing-python/src

# After
RUN pip install --no-cache-dir -e .
ENV PYTHONPATH=/code
```

### 4.3 docker-compose.yml 수정

```yaml
# Before
environment:
  - PYTHONPATH=/code:/code/packages/selfhealing-python/src

# After
environment:
  - PYTHONPATH=/code
```

### 4.4 docker-compose.override.yml (로컬 개발 전용)

분리 후 로컬에서 selfhealing 코드 수정을 즉시 반영하려면,
두 repo를 같은 상위 폴더에 배치하고 override로 Volume Mount한다.
(327 Local Development 섹션 참조)

```yaml
# docker-compose.override.yml (.gitignore에 추가)
services:
  web:
    volumes:
      - ../selfhealing-python/src:/code/selfhealing-src:ro
    environment:
      - PYTHONPATH=/code:/code/selfhealing-src
```

---

## 5. CLAUDE.md 수정

### 5.1 selfhealing repo CLAUDE.md

기존 CLAUDE.md에서 shopping 관련 내용 제거, 라이브러리 관점으로 수정.

### 5.2 shopping repo CLAUDE.md

selfhealing을 외부 dependency로 참조하도록 수정.

---

## 6. 검증 체크리스트

분리 완료 후 확인:

- [ ] selfhealing repo: `pytest tests/unit/` 전체 통과
- [ ] selfhealing repo: `pytest tests/integration/` 전체 통과 (testapp 사용)
- [ ] shopping repo: `pip install selfhealing[django,celery]` 성공
- [ ] shopping repo: `pytest shopping/tests/` 전체 통과
- [ ] shopping repo: `pytest tests/hybrid/` 전체 통과
- [ ] shopping repo: Docker 빌드 성공
- [ ] shopping repo: docker-compose up 정상 동작
- [ ] selfhealing import 경로 변경 없음 (`from selfhealing import ...`)
- [ ] selfhealing repo: CI가 GitHub Secrets 없이 전체 테스트 통과 (라이브러리 독립성 검증)
- [ ] shopping repo: 기존 GitHub Secrets (`DJANGO_SECRET_KEY`, `ENCRYPTION_KEY`) 유지 확인

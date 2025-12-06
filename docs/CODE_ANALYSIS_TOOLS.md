# 코드 분석 도구 가이드

이 문서는 프로젝트에 설치된 코드 분석 도구들의 사용법과 Best Practice를 설명합니다.

---

## 📋 목차

1. [도구 개요](#도구-개요)
2. [실행 환경 (로컬 vs Docker)](#실행-환경-로컬-vs-docker)
3. [pygount (코드 통계)](#1-pygount-코드-통계)
4. [pydeps (의존성 시각화)](#2-pydeps-의존성-시각화)
5. [radon (복잡도 분석)](#3-radon-복잡도-분석)
6. [bandit (보안 분석)](#4-bandit-보안-분석)
7. [vulture (죽은 코드 탐지)](#5-vulture-죽은-코드-탐지)
8. [pipdeptree (의존성 트리)](#6-pipdeptree-의존성-트리)
9. [pip-audit (의존성 보안)](#7-pip-audit-의존성-보안)
10. [mutmut (Mutation Testing)](#8-mutmut-mutation-testing)
11. [통합 스크립트](#통합-스크립트)
12. [CI/CD 통합](#cicd-통합)

---

## 도구 개요

| 도구 | 용도 | 실행 빈도 |
|------|------|----------|
| pygount | 코드 라인 수 통계 | 릴리즈 전 |
| pydeps | 모듈 의존성 시각화 | 리팩토링 전 |
| radon | 코드 복잡도 분석 | 매 PR |
| bandit | 보안 취약점 탐지 | 매 PR |
| vulture | 죽은 코드 탐지 | 주 1회 |
| pipdeptree | pip 의존성 트리 | 패키지 추가 시 |
| pip-audit | 의존성 보안 검사 | 주 1회 |
| mutmut | 테스트 품질 검증 | 핵심 로직 변경 시 |

---

## 실행 환경 (로컬 vs Docker)

### 환경 비교

| 환경 | 장점 | 단점 |
|------|------|------|
| **로컬 (venv)** | 빠른 실행, 파일 직접 생성 | Windows 호환성 이슈 가능 |
| **Docker** | 일관된 환경, 팀 공유 용이 | 약간 느림, 볼륨 마운트 필요 |

### Docker에서 분석 도구 실행 방법

```bash
# 방법 1: 실행 중인 web 컨테이너에서 실행
docker-compose exec web <명령어>

# 방법 2: 새 컨테이너로 일회성 실행
docker-compose run --rm web <명령어>

# 예시: radon 복잡도 분석
docker-compose exec web radon cc shopping -a -s --min B

# 예시: bandit 보안 분석
docker-compose exec web bandit -r shopping -x shopping/tests

# 예시: vulture 죽은 코드 탐지
docker-compose exec web vulture shopping --min-confidence 80
```

### Docker 전용 명령어 요약

| 도구 | Docker 명령어 |
|------|--------------|
| pygount | `docker-compose exec web pygount --format=summary shopping/` |
| radon | `docker-compose exec web radon cc shopping -a -s` |
| bandit | `docker-compose exec web bandit -r shopping -x shopping/tests` |
| vulture | `docker-compose exec web vulture shopping --min-confidence 80` |
| pipdeptree | `docker-compose exec web pipdeptree --warn fail` |
| pip-audit | `docker-compose exec web pip-audit` |
| mutmut | `docker-compose exec web mutmut run` | # 절대 금지

### Docker에서 파일 출력 시 주의사항

```bash
# 리포트 파일 생성 시 볼륨 마운트된 경로 사용
docker-compose exec web bandit -r shopping -f html -o /code/reports/security.html

# pydeps는 Docker 내부에 Graphviz 필요 (Dockerfile에 이미 포함)
docker-compose exec web pydeps shopping -o /code/docs/deps.png -T png --no-show
```

---

## 1. pygount (코드 통계)

> cloc의 Python 대안. 프로젝트 규모 측정에 사용.

### 기본 사용법 (로컬)

```bash
# 전체 프로젝트 통계
pygount --format=summary .

# shopping 앱만 분석
pygount --format=summary shopping/

# 특정 파일 형식만 (Python)
pygount --format=summary --suffix=py shopping/

# 상세 출력 (파일별)
pygount --format=cloc-xml shopping/ > code_stats.xml

# 제외 패턴 지정
pygount --format=summary --folders-to-skip="venv,htmlcov,staticfiles,media,migrations,__pycache__" .
```

### Docker에서 실행

```bash
# 기본 통계
docker-compose exec web pygount --format=summary shopping/

# 상세 출력 (파일로 저장)
docker-compose exec web pygount --format=cloc-xml shopping/ > /code/reports/code_stats.xml
```

### 출력 예시

```
┏━━━━━━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━┳━━━━━━━━┳━━━━━━━━━━┓
┃ Language     ┃ Files  ┃ % Files ┃  Code  ┃ % Code   ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━╇━━━━━━━━╇━━━━━━━━━━┩
│ Python       │    120 │  85.7%  │  8,500 │   90.2%  │
│ HTML         │     15 │  10.7%  │    800 │    8.5%  │
│ YAML         │      5 │   3.6%  │    120 │    1.3%  │
└──────────────┴────────┴─────────┴────────┴──────────┘
```

---

## 2. pydeps (의존성 시각화)

> 모듈 간 의존성을 그래프로 시각화. 순환 참조 탐지에 유용.

### 출력 형식 비교

| 형식 | 장점 | 단점 | 권장 상황 |
|------|------|------|----------|
| **SVG** | 확대해도 선명, 파일 작음, 텍스트 검색 가능 | 브라우저로 열어야 함 | 문서화, 상세 분석 |
| **PNG** | 어디서나 바로 열림, 공유 쉬움 | 확대 시 깨짐, 파일 큼 | 슬랙/팀 공유, 프레젠테이션 |
| **PDF** | 인쇄용, 고품질 | 편집 어려움 | 보고서 첨부 |

### 사전 요구사항 (로컬 - Windows)

```bash
# 1. Graphviz 설치
winget install Graphviz.Graphviz

# 2. PATH 영구 설정 (Git Bash)
echo 'export PATH="$PATH:/c/Program Files/Graphviz/bin"' >> ~/.bashrc
source ~/.bashrc

# 3. 설치 확인
dot -V
```

### 기본 사용법 (로컬)

```bash
# SVG 형식 (기본, 확대해도 선명)
pydeps shopping --max-bacon=2 -o docs/shopping_deps.svg

# PNG 형식 (이미지 뷰어에서 바로 열림)
pydeps shopping --max-bacon=2 -o docs/shopping_deps.png -T png

# 특정 모듈만
pydeps shopping.services --max-bacon=2 -o docs/services_deps.png -T png

# 순환 의존성만 표시
pydeps shopping --show-cycles -o docs/cycles.png -T png

# 외부 패키지 제외 (내부 의존성만)
pydeps shopping --no-show -o docs/internal_deps.png -T png

# 클러스터링 (패키지별 그룹화)
pydeps shopping --cluster -o docs/clustered_deps.png -T png
```

### Docker에서 실행

```bash
# Docker 컨테이너에는 Graphviz가 이미 설치되어 있음

# PNG 형식으로 생성
docker-compose exec web pydeps shopping --no-show -o /code/docs/deps.png -T png

# SVG 형식으로 생성
docker-compose exec web pydeps shopping --no-show -o /code/docs/deps.svg

# 순환 의존성 검사
docker-compose exec web pydeps shopping --show-cycles --no-show -o /code/docs/cycles.png -T png

# services 모듈만
docker-compose exec web pydeps shopping.services --max-bacon=3 --cluster --no-show -o /code/docs/services.png -T png
```

### 옵션 설명

| 옵션 | 설명 |
|------|------|
| `--max-bacon=N` | 깊이 제한 (2-3 권장) |
| `--no-show` | 그래프 자동 열기 비활성화 |
| `--show-cycles` | 순환 참조만 표시 |
| `--cluster` | 패키지별 그룹화 |
| `--exclude` | 제외할 패키지 패턴 |
| `-T png/svg/pdf` | 출력 형식 |

### 권장 명령어

```bash
# 로컬: 일상적 분석 (services 모듈 중심, PNG)
pydeps shopping.services --max-bacon=3 --cluster --no-show -o docs/services_architecture.png -T png

# 로컬: 순환 참조 검사
pydeps shopping --show-cycles --no-show -o docs/circular_deps.png -T png

# Docker: services 아키텍처
docker-compose exec web pydeps shopping.services --max-bacon=3 --cluster --no-show -o /code/docs/services_architecture.png -T png

# Docker: 순환 참조 검사
docker-compose exec web pydeps shopping --show-cycles --no-show -o /code/docs/circular_deps.png -T png
```

---

## 3. radon (복잡도 분석)

> Cyclomatic Complexity(순환 복잡도) 측정. 리팩토링 우선순위 결정에 활용.

### 복잡도 등급

| 등급 | CC 범위 | 의미 | 권장 조치 |
|------|---------|------|----------|
| A | 1-5 | 낮음 | 유지 |
| B | 6-10 | 보통 | 모니터링 |
| C | 11-20 | 높음 | 리팩토링 고려 |
| D | 21-30 | 매우 높음 | 리팩토링 필요 |
| E | 31-40 | 위험 | 즉시 리팩토링 |
| F | 41+ | 매우 위험 | 분할 필수 |

### 기본 사용법 (로컬)

```bash
# Cyclomatic Complexity (CC) 분석
radon cc shopping -a -s

# 복잡도 C 이상만 표시 (문제 있는 코드만)
radon cc shopping -a -s --min C

# JSON 출력
radon cc shopping -a -s --json > radon_report.json

# 특정 파일만
radon cc shopping/services/payment_service.py -s

# Maintainability Index (유지보수성 지수)
radon mi shopping -s

# Raw 메트릭 (LOC, LLOC, SLOC, 주석 등)
radon raw shopping -s
```

### Docker에서 실행

```bash
# 복잡도 분석
docker-compose exec web radon cc shopping -a -s

# 복잡도 C 이상만
docker-compose exec web radon cc shopping -a -s --min C

# JSON 출력 (파일 저장)
docker-compose exec web radon cc shopping -a -s --json > reports/radon_report.json

# 유지보수성 분석
docker-compose exec web radon mi shopping -s
```

### 출력 예시

```
shopping/services/payment_service.py
    M 45:4 process_payment - C (15)
    M 78:4 handle_refund - B (8)
    M 120:4 validate_payment - A (3)

Average complexity: B (7.5)
```

### 권장 명령어

```bash
# 로컬: 전체 분석 + 평균
radon cc shopping -a -s --min B

# 로컬: 유지보수성 분석
radon mi shopping -s --min B

# 로컬: CI용 (실패 기준 설정)
radon cc shopping -a -s --min C --total-average

# Docker: 전체 분석
docker-compose exec web radon cc shopping -a -s --min B

# Docker: 유지보수성 분석
docker-compose exec web radon mi shopping -s --min B
```

---

## 4. bandit (보안 분석)

> Python 코드의 보안 취약점 탐지. OWASP 가이드라인 기반.

### 심각도 수준

| 수준 | 의미 | 예시 |
|------|------|------|
| HIGH | 즉시 수정 필요 | SQL Injection, 하드코딩된 비밀번호 |
| MEDIUM | 검토 필요 | exec() 사용, 안전하지 않은 YAML 로드 |
| LOW | 참고 | assert 문 사용, 빈 except |

### 기본 사용법 (로컬)

```bash
# 전체 분석
bandit -r shopping

# HTML 리포트 생성
bandit -r shopping -f html -o reports/security_report.html

# JSON 출력
bandit -r shopping -f json -o reports/security_report.json

# 심각도 필터링 (HIGH만)
bandit -r shopping -ll

# 특정 테스트만 실행
bandit -r shopping -t B101,B102,B103

# 특정 테스트 제외
bandit -r shopping -s B101,B311

# 신뢰도 필터링 (HIGH 신뢰도만)
bandit -r shopping -iii
```

### Docker에서 실행

```bash
# 전체 분석
docker-compose exec web bandit -r shopping

# HTML 리포트 생성
docker-compose exec web bandit -r shopping -f html -o /code/reports/security_report.html

# JSON 출력
docker-compose exec web bandit -r shopping -f json -o /code/reports/security_report.json

# 테스트 제외하고 분석
docker-compose exec web bandit -r shopping -x shopping/tests

# HIGH 심각도만
docker-compose exec web bandit -r shopping -x shopping/tests -ll
```

### 주요 검사 항목 (자주 발생)

| 코드 | 설명 | 해결 방법 |
|------|------|----------|
| B101 | assert 사용 | 프로덕션에서 제거 또는 예외 처리로 변경 |
| B105 | 하드코딩된 비밀번호 | 환경변수 사용 |
| B106 | 하드코딩된 비밀번호 인자 | 환경변수 사용 |
| B107 | 하드코딩된 비밀번호 기본값 | 환경변수 사용 |
| B311 | random 모듈 사용 | 보안용이면 secrets 모듈 사용 |
| B608 | SQL Injection 가능성 | 파라미터화된 쿼리 사용 |

### 권장 명령어

```bash
# 로컬: 일상적 검사 (테스트 제외)
bandit -r shopping -x shopping/tests --format txt

# 로컬: PR 검사용
bandit -r shopping -x shopping/tests -ll -ii --format json

# 로컬: 전체 리포트
bandit -r shopping -x shopping/tests -f html -o reports/security_$(date +%Y%m%d).html

# Docker: 일상적 검사
docker-compose exec web bandit -r shopping -x shopping/tests --format txt

# Docker: HTML 리포트
docker-compose exec web bandit -r shopping -x shopping/tests -f html -o /code/reports/security.html
```

### pyproject.toml 설정

```toml
[tool.bandit]
exclude_dirs = ["tests", "migrations", "venv", "env", "htmlcov", "load_tests"]
skips = ["B101", "B311"]  # B101: assert 사용, B311: random (보안용 아닌 경우)
targets = ["shopping"]
```

---

## 5. vulture (죽은 코드 탐지)

> 사용되지 않는 코드(함수, 변수, import) 탐지.

### 기본 사용법 (로컬)

```bash
# 기본 분석
vulture shopping

# 신뢰도 80% 이상만 (오탐 감소)
vulture shopping --min-confidence 80

# 정렬 (크기순)
vulture shopping --sort-by-size

# 화이트리스트 생성 (오탐 관리)
vulture shopping --make-whitelist > vulture_whitelist.py

# 화이트리스트 적용
vulture shopping vulture_whitelist.py
```

### Docker에서 실행

```bash
# 기본 분석
docker-compose exec web vulture shopping

# 신뢰도 80% 이상만
docker-compose exec web vulture shopping --min-confidence 80

# 정렬 (크기순)
docker-compose exec web vulture shopping --sort-by-size

# 화이트리스트 생성
docker-compose exec web vulture shopping --make-whitelist > vulture_whitelist.py
```

### 출력 예시

```
shopping/services/old_payment.py:45: unused function 'legacy_process' (60% confidence)
shopping/views/cart_views.py:23: unused import 'json' (100% confidence)
shopping/models/user.py:78: unused variable 'temp_data' (90% confidence)
```

### 화이트리스트 관리

Django 프로젝트에서 자주 오탐되는 패턴:

```python
# vulture_whitelist.py
from shopping.models import *

# Django 자동 사용 패턴
_.objects  # Manager
_.Meta  # Model Meta class
_.clean  # Form/Model clean method
_.save  # Model save method
_.delete  # Model delete method
_.get_queryset  # ViewSet method
_.perform_create  # DRF ViewSet
_.perform_update  # DRF ViewSet
_.get_serializer_class  # DRF ViewSet
```

### 권장 명령어

```bash
# 로컬: 일상적 분석
vulture shopping --min-confidence 80 --sort-by-size

# 로컬: 화이트리스트와 함께
vulture shopping vulture_whitelist.py --min-confidence 80

# Docker: 일상적 분석
docker-compose exec web vulture shopping --min-confidence 80 --sort-by-size

# Docker: 화이트리스트와 함께
docker-compose exec web vulture shopping vulture_whitelist.py --min-confidence 80
```

---

## 6. pipdeptree (의존성 트리)

> pip 패키지 의존성 트리 시각화. 충돌 탐지에 유용.

### 기본 사용법 (로컬)

```bash
# 전체 의존성 트리
pipdeptree

# 역방향 (특정 패키지가 어디서 필요한지)
pipdeptree -r -p django

# JSON 출력
pipdeptree --json-tree > deps.json

# 충돌 검사 (경고 시 실패)
pipdeptree --warn fail

# 특정 패키지 의존성만
pipdeptree -p celery

# 그래프 형식 (Graphviz용)
pipdeptree --graph-output png > deps.png
```

### Docker에서 실행

```bash
# 전체 의존성 트리
docker-compose exec web pipdeptree

# 충돌 검사
docker-compose exec web pipdeptree --warn fail

# 특정 패키지 추적
docker-compose exec web pipdeptree -r -p django

# JSON 출력
docker-compose exec web pipdeptree --json-tree > deps.json
```

### 출력 예시

```
Django==5.2.4
├── asgiref [required: >=3.7.0,<4, installed: 3.9.1]
├── sqlparse [required: >=0.3.1, installed: 0.5.3]
└── tzdata [required: Any, installed: 2025.2]
celery==5.5.3
├── billiard [required: >=4.2.0,<5.0, installed: 4.2.1]
├── click [required: >=8.1.2,<9.0, installed: 8.2.1]
...
```

### 권장 명령어

```bash
# 로컬: 충돌 검사 (CI용)
pipdeptree --warn fail

# 로컬: 특정 패키지 추적
pipdeptree -r -p requests

# Docker: 충돌 검사
docker-compose exec web pipdeptree --warn fail

# Docker: 특정 패키지 추적
docker-compose exec web pipdeptree -r -p requests
```

---

## 7. pip-audit (의존성 보안)

> 설치된 패키지의 알려진 보안 취약점(CVE) 검사. PyPI 공식 보안 도구.

### safety vs pip-audit

| 도구 | 장점 | 단점 |
|------|------|------|
| **safety** | 상세한 리포트 | 계정 등록 필요, deprecated |
| **pip-audit** ✓ | 무료, 계정 불필요, PyPI 공식 | - |

> ⚠️ `safety check` 명령어는 2024년 6월부터 deprecated되었습니다. `pip-audit`를 권장합니다.

### 기본 사용법 (로컬)

```bash
# 기본 검사
pip-audit

# requirements.txt 검사
pip-audit -r requirements.txt

# JSON 출력
pip-audit --format json > audit_report.json

# 특정 취약점 무시
pip-audit --ignore-vuln CVE-2024-12345

# 수정 버전 자동 적용 (dry-run)
pip-audit --fix --dry-run

# 실제 수정 적용
pip-audit --fix
```

### Docker에서 실행

```bash
# 기본 검사
docker-compose exec web pip-audit

# requirements.txt 검사
docker-compose exec web pip-audit -r requirements.txt

# JSON 출력
docker-compose exec web pip-audit --format json > audit_report.json

# 수정 버전 확인 (dry-run)
docker-compose exec web pip-audit --fix --dry-run
```

### 출력 예시

```
Found 5 known vulnerabilities in 1 package
Name   Version ID             Fix Versions
------ ------- -------------- -------------------
django 5.2.4   CVE-2025-57833 4.2.24,5.1.12,5.2.6
django 5.2.4   CVE-2025-59681 4.2.25,5.1.13,5.2.7
django 5.2.4   CVE-2025-59682 4.2.25,5.1.13,5.2.7
django 5.2.4   CVE-2025-64458 4.2.26,5.1.14,5.2.8
django 5.2.4   CVE-2025-64459 4.2.26,5.1.14,5.2.8
```

### 권장 명령어

```bash
# 로컬: 주간 검사
pip-audit

# 로컬: CI용 (JSON 출력)
pip-audit --format json

# Docker: 주간 검사
docker-compose exec web pip-audit

# Docker: CI용
docker-compose exec web pip-audit --format json
```

---

## 8. mutmut (Mutation Testing)

> ⚠️ **현재 프로젝트에서는 아직 도입하지 않음** - 핵심 로직 안정화 후 도입 예정
> 
> 코드에 의도적 버그를 삽입하여 테스트 품질 검증. **돈 관련 서비스에만 적용.**

### 언제 도입할 것인가?

| 조건 | 상태 |
|------|------|
| 핵심 비즈니스 로직 안정화 | ⏳ 진행 중 |
| pytest 커버리지 80% 이상 | ⏳ 확인 필요 |
| CI/CD 파이프라인 구축 | ⏳ 구축 예정 |

### 개념

```
원본: if amount > 100:
뮤턴트1: if amount >= 100:  → 테스트가 잡으면 KILLED ✓
뮤턴트2: if amount < 100:   → 테스트가 못잡으면 SURVIVED ✗
```

- **KILLED**: 테스트가 뮤턴트를 잡음 → 좋음
- **SURVIVED**: 테스트가 뮤턴트를 놓침 → 테스트 보강 필요

### 대상 파일 (돈 관련)

pyproject.toml에 설정된 대상:

```
shopping/services/payment_service.py    # 결제 처리
shopping/services/point_service.py      # 포인트 적립/사용
shopping/services/point_query_service.py # 포인트 조회
shopping/services/cart_service.py       # 장바구니 (가격 계산)
shopping/services/order_service.py      # 주문 처리
shopping/services/return_service.py     # 반품/환불
shopping/services/toss_webhook_service.py # 토스 웹훅 처리
```

### 사전 요구사항

```bash
# 1. Docker 컨테이너 실행 (PostgreSQL, Redis 필요)
docker-compose up -d db redis

# 2. 별도 pytest 설정 파일 사용 (pytest_mutmut.ini)
#    - pyproject.toml의 coverage, xdist 설정과 충돌 방지
```

### 기본 사용법 (로컬)

```bash
# 전체 실행 (setup.cfg 설정 사용)
PYTHONIOENCODING=utf-8 mutmut run

# 특정 파일만 (설정 무시)
PYTHONIOENCODING=utf-8 mutmut run --paths-to-mutate=shopping/services/payment_service.py

# 결과 확인
mutmut results

# 생존한 뮤턴트 상세 보기
mutmut show <mutant_id>

# 특정 뮤턴트 적용해서 테스트 (디버깅용)
mutmut apply <mutant_id>

# HTML 리포트 생성
mutmut html
# 결과: html/index.html

# 뮤턴트 초기화
mutmut reset
```

### Docker에서 실행

```bash
# 컨테이너 실행 확인 (PostgreSQL, Redis 필요)
docker-compose up -d db redis web

# 전체 실행
docker-compose exec web mutmut run

# 특정 파일만
docker-compose exec web mutmut run --paths-to-mutate=shopping/services/payment_service.py

# 결과 확인
docker-compose exec web mutmut results

# 생존 뮤턴트 상세 보기
docker-compose exec web mutmut show <mutant_id>

# HTML 리포트 생성
docker-compose exec web mutmut html
```

### 실행 예시

```bash
$ mutmut run --paths-to-mutate=shopping/services/payment_service.py

⠋ Running mutation testing...
━━━━━━━━━━━━━━━━━━━━━━ 45/45 ━━━━━━━━━━━━━━━━━━━━━━
Killed: 42  Survived: 3  Timeout: 0

$ mutmut results
Survived mutants:
   3: shopping/services/payment_service.py:67
   7: shopping/services/payment_service.py:89
  12: shopping/services/payment_service.py:134

$ mutmut show 3
--- shopping/services/payment_service.py
+++ shopping/services/payment_service.py
@@ -67 @@
-    if order.total_amount > 0:
+    if order.total_amount >= 0:  # 경계값 테스트 누락!
```

### 권장 워크플로우

```bash
# === 로컬 환경 ===
# 1. 결제 서비스 테스트 (가장 중요)
mutmut run --paths-to-mutate=shopping/services/payment_service.py

# 2. 결과 분석
mutmut results

# 3. 생존 뮤턴트 확인 및 테스트 보강
mutmut show <id>

# 4. 테스트 추가 후 재실행
mutmut run --paths-to-mutate=shopping/services/payment_service.py

# 5. HTML 리포트 생성
mutmut html

# === Docker 환경 ===
# 1. 결제 서비스 테스트
docker-compose exec web mutmut run --paths-to-mutate=shopping/services/payment_service.py

# 2. 결과 분석
docker-compose exec web mutmut results

# 3. 생존 뮤턴트 확인
docker-compose exec web mutmut show <id>

# 4. HTML 리포트 생성
docker-compose exec web mutmut html
```

### 목표 지표

| 지표 | 목표 | 현재 상태 |
|------|------|----------|
| Mutation Score | > 80% | 측정 필요 |
| 결제 서비스 | > 90% | - |
| 포인트 서비스 | > 90% | - |

```
Mutation Score = KILLED / (KILLED + SURVIVED) × 100
```

### 시간 단축 팁

```bash
# 병렬 실행 (CPU 코어 수에 맞게)
mutmut run --runners=4

# 특정 테스트만 실행
mutmut run --runner="python -m pytest shopping/tests/services/test_payment.py -x"

# 캐시 활용 (이전 결과 재사용)
mutmut run --no-progress
```

---

## 통합 스크립트

모든 도구를 한 번에 실행하는 스크립트:

### `scripts/analyze_code.sh`

```bash
#!/bin/bash
set -e

echo "=================================="
echo "   코드 분석 도구 실행"
echo "=================================="

REPORT_DIR="reports/$(date +%Y%m%d)"
mkdir -p $REPORT_DIR

echo ""
echo "1. 코드 통계 (pygount)..."
pygount --format=summary shopping/ --folders-to-skip="migrations,__pycache__"

echo ""
echo "2. 복잡도 분석 (radon)..."
radon cc shopping -a -s --min B | tee $REPORT_DIR/complexity.txt

echo ""
echo "3. 유지보수성 분석 (radon mi)..."
radon mi shopping -s --min B | tee $REPORT_DIR/maintainability.txt

echo ""
echo "4. 보안 분석 (bandit)..."
bandit -r shopping -x shopping/tests -f html -o $REPORT_DIR/security.html
echo "   → $REPORT_DIR/security.html 생성"

echo ""
echo "5. 죽은 코드 탐지 (vulture)..."
vulture shopping --min-confidence 80 | tee $REPORT_DIR/dead_code.txt

echo ""
echo "6. 의존성 충돌 검사 (pipdeptree)..."
pipdeptree --warn fail

echo ""
echo "7. 의존성 보안 검사 (pip-audit)..."
pip-audit | tee $REPORT_DIR/audit.txt

echo ""
echo "=================================="
echo "   분석 완료! 리포트: $REPORT_DIR"
echo "=================================="
```

### `scripts/mutation_test.sh`

```bash
#!/bin/bash
set -e

echo "=================================="
echo "   Mutation Testing (돈 관련 서비스)"
echo "=================================="

echo ""
echo "대상 파일:"
echo "  - payment_service.py"
echo "  - point_service.py"
echo "  - cart_service.py"
echo "  - order_service.py"
echo ""

# 이전 결과 초기화
mutmut reset 2>/dev/null || true

# 실행
mutmut run

# 결과 출력
echo ""
echo "=================================="
echo "   결과 요약"
echo "=================================="
mutmut results

# HTML 리포트 생성
mutmut html
echo ""
echo "HTML 리포트: html/index.html"
```

---

## CI/CD 통합

### GitHub Actions 예시

```yaml
# .github/workflows/code-quality.yml
name: Code Quality

on:
  pull_request:
    branches: [main, develop]

jobs:
  analysis:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -r requirements-dev.txt

      - name: Complexity Check (radon)
        run: |
          radon cc shopping -a -s --min D
          # 복잡도 D 이상이면 경고

      - name: Security Check (bandit)
        run: |
          bandit -r shopping -x shopping/tests -ll
          # HIGH 심각도만 실패 처리

      - name: Dependency Check (pip-audit)
        run: |
          pip-audit
        continue-on-error: true  # 경고만 (선택)

      - name: Dead Code Check (vulture)
        run: |
          vulture shopping --min-confidence 90
        continue-on-error: true  # 경고만

  mutation-test:
    runs-on: ubuntu-latest
    if: contains(github.event.pull_request.labels.*.name, 'mutation-test')
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: |
          pip install -r requirements-dev.txt

      - name: Run Mutation Tests
        run: |
          mutmut run --paths-to-mutate=shopping/services/payment_service.py
          mutmut results
```

---

## 문제 해결

### pydeps 실행 시 Graphviz 오류

```bash
# 오류: "dot" not found
# 해결: Graphviz 설치 및 PATH 설정
winget install Graphviz.Graphviz
# 재시작 후 확인
dot -V
```

### mutmut 느린 실행

```bash
# 원인: 전체 테스트 스위트 실행
# 해결: 관련 테스트만 실행
mutmut run --runner="python -m pytest shopping/tests/services/ -x --tb=no -q"
```

### vulture 오탐 과다

```bash
# 해결: 화이트리스트 생성
vulture shopping --make-whitelist > vulture_whitelist.py
# 이후 함께 실행
vulture shopping vulture_whitelist.py
```

---

## 참고 링크

- [radon 문서](https://radon.readthedocs.io/)
- [bandit 문서](https://bandit.readthedocs.io/)
- [vulture 문서](https://github.com/jendrikseipp/vulture)
- [mutmut 문서](https://mutmut.readthedocs.io/)
- [pydeps 문서](https://github.com/thebjorn/pydeps)
- [pip-audit 문서](https://github.com/pypa/pip-audit)

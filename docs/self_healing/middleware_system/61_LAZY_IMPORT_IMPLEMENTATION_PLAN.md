# 61. Views __init__.py Lazy Import 구현 계획서

| 항목 | 내용 |
|-----|------|
| 버전 | 1.0 |
| 작성일 | 2026-01-19 |
| 우선순위 | 🔴 즉시 |
| 예상 효과 | Django 앱 startup 시간 ~30% 감소 |

---

## 1. 현황 분석

### 1.1 문제점

`selfhealing/api/django/views/__init__.py` 파일이 Django 앱 로딩 시 **90개 이상의 View 클래스**를 즉시 import하고 있음.

**영향받는 모듈:**
- `circuit_breaker` (11개)
- `dlq` (8개)
- `dashboard` (1개)
- `health` (6개)
- `system_control` (9개)
- `config` (14개)
- `drift_threshold` (2개)
- `error_budget` (7개)
- `config_history` (4개)
- `chaos` (16개)
- `governance` (9개)
- `xtest_mode` (7개)
- `auto_tuning` (9개)

**총합: 103개 심볼**

### 1.2 실제 사용 패턴 (코드 근거)

| 사용처 | Import 방식 | 용도 |
|--------|------------|------|
| `urls.py` | `from selfhealing.api.django.views import ...` | URL 라우팅 |
| `selfhealing/api/django/__init__.py` | `from selfhealing.api.django.views import ...` | 패키지 진입점 |
| 테스트 코드 | 대부분 직접 서브모듈 import | 개별 View 테스트 |

**핵심 발견:**
- `urls.py`가 유일하게 `views/__init__.py`를 통해 대량 import
- 테스트 코드는 이미 직접 경로 사용 (`from selfhealing.api.django.views.system_control import ...`)

### 1.3 기존 성공 사례 (코드 근거)

**chaos/__init__.py** - 이미 Lazy Import 적용됨:
- 21개 View 클래스를 `_LAZY_IMPORTS` 딕셔너리로 관리
- `__getattr__` 함수로 on-demand 로딩
- `TYPE_CHECKING` 블록으로 IDE 지원 유지

**performance/__init__.py** - Lazy Import 적용됨:
- 핵심 API 1개만 직접 import
- 나머지 8개 클래스는 lazy load

---

## 2. 구현 전략

### 2.1 Lazy Import 패턴 적용 대상

| 모듈 | 현재 직접 import 수 | Lazy 전환 수 | 직접 유지 수 |
|------|------------------|-------------|-------------|
| circuit_breaker | 11 | 11 | 0 |
| dlq | 8 | 8 | 0 |
| dashboard | 1 | 1 | 0 |
| health | 6 | 6 | 0 |
| system_control | 9 | 9 | 0 |
| config | 14 | 14 | 0 |
| drift_threshold | 2 | 2 | 0 |
| error_budget | 7 | 7 | 0 |
| config_history | 4 | 4 | 0 |
| chaos | 16 | 0 | 16 (이미 lazy) |
| governance | 9 | 9 | 0 |
| xtest_mode | 7 | 7 | 0 |
| auto_tuning | 9 | 9 | 0 |
| **합계** | 103 | 87 | 16 |

### 2.2 구현 단계

**Step 1: `_LAZY_IMPORTS` 딕셔너리 정의**
- 87개 심볼을 `(모듈경로, 심볼명)` 튜플로 매핑
- 카테고리별 주석으로 구분

**Step 2: `__getattr__` 함수 구현**
- `chaos/__init__.py`의 패턴 재사용
- `importlib.import_module` 사용

**Step 3: `__dir__` 함수 구현**
- 자동완성 지원을 위해 모든 심볼 이름 반환

**Step 4: `TYPE_CHECKING` 블록 추가**
- IDE 타입 힌트를 위한 정적 import
- 런타임에는 실행되지 않음

**Step 5: `__all__` 유지**
- 기존 `__all__` 목록 그대로 유지
- 하위 호환성 보장

### 2.3 하위 호환성 보장

| 기존 코드 | 변경 후 동작 |
|----------|------------|
| `from views import ControlActionView` | ✅ 동일하게 작동 |
| `from views import *` | ✅ `__all__` 기반으로 작동 |
| `views.ControlActionView` | ✅ `__getattr__`로 해결 |

---

## 3. 예상 효과

### 3.1 정량적 효과

| 지표 | Before | After | 개선율 |
|------|--------|-------|-------|
| 모듈 초기화 시 import 수 | 103개 | 16개 | -84% |
| 서브모듈 로딩 | 13개 전체 | 1개 (chaos) | -92% |

### 3.2 정성적 효과

- **Startup 시간 감소**: 불필요한 View 로딩 제거
- **메모리 효율**: 사용하지 않는 View는 로딩되지 않음
- **테스트 격리**: 특정 View만 import 시 다른 View 영향 없음

---

## 4. 위험 요소 및 완화

### 4.1 잠재 위험

| 위험 | 가능성 | 영향 | 완화 방안 |
|------|-------|------|----------|
| IDE 자동완성 미작동 | 낮음 | 중간 | `TYPE_CHECKING` 블록 적용 |
| 순환 import 발생 | 낮음 | 높음 | 함수 내부 import 사용 |
| 기존 테스트 실패 | 낮음 | 중간 | Docker 테스트로 검증 |

### 4.2 검증 계획

1. **단위 검증**: 각 View 개별 import 테스트
2. **통합 검증**: `urls.py` 로딩 테스트
3. **E2E 검증**: Django runserver 정상 작동 확인

---

## 5. 구현 체크리스트

- [ ] `_LAZY_IMPORTS` 딕셔너리 정의 (87개 심볼)
- [ ] `__getattr__` 함수 구현
- [ ] `__dir__` 함수 구현
- [ ] `TYPE_CHECKING` 블록 추가
- [ ] 기존 직접 import 문 제거
- [ ] docstring 업데이트
- [ ] Docker 테스트 통과 확인
- [ ] Git 커밋

---

## 6. 참고 자료

| 문서 | 경로 |
|------|------|
| chaos/__init__.py (참조 구현) | `selfhealing/api/django/views/chaos/__init__.py` |
| performance/__init__.py (참조 구현) | `selfhealing/audit/performance/__init__.py` |
| Phase 2 리팩토링 계획서 | `60_REFACTORING_PLAN_PHASE2.md` |

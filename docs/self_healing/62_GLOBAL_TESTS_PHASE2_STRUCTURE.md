# 62. Phase 2: 구조 정리

> **선행 문서**: 61_GLOBAL_TESTS_PHASE1_INFRASTRUCTURE.md  
> **목표**: Unit 테스트 이동, conftest.py 정리, 폴더 구조 최적화  
> **예상 소요**: 3-4시간

---

## 1. 목표

1. `tests/self_healing/unit/` (61개 파일) → 패키지 폴더로 이동
2. `tests/self_healing/conftest.py` autouse Mock 제거
3. 하위 conftest.py 중복 제거
4. 불필요한 폴더 정리

---

## 2. 작업 목록

### 2.1 Unit 테스트 이동

**위치**: `tests/self_healing/unit/` → `packages/selfhealing-python/tests/unit/`

**현재 상태**:
- 61개 Python 파일
- selfhealing 패키지의 Unit 테스트
- 전역 tests 폴더에 잘못 위치

**이동 대상 폴더**:

| 원본 | 이동 대상 |
|------|----------|
| `tests/self_healing/unit/core/` | `packages/.../tests/unit/core/` (신규 생성 필요) |
| `tests/self_healing/unit/selfhealing/` | `packages/.../tests/unit/selfhealing/` |
| `tests/self_healing/unit/services/` | `packages/.../tests/unit/services/` |

**주의사항**:
- import 경로 확인 필요
- conftest.py 의존성 확인
- 기존 패키지 테스트와 충돌 확인

---

### 2.2 tests/self_healing/conftest.py 수정

**위치**: `tests/self_healing/conftest.py`

**현재 상태**:
- `mock_external_services` fixture: `autouse=True`
- `reset_singletons` fixture: `autouse=True`
- 모든 테스트에 자동 적용 → 통합 테스트에서도 Mock 사용

**수정 사항**:

| fixture | 변경 전 | 변경 후 |
|---------|--------|--------|
| `mock_external_services` | `autouse=True` | `autouse=False` |
| `reset_singletons` | `autouse=True` | `autouse=True` (유지) |

**Mock이 필요한 테스트**:
- 개별 테스트에서 명시적으로 `mock_external_services` fixture 요청
- 또는 클래스 레벨 `@pytest.mark.usefixtures("mock_external_services")`

---

### 2.3 tests/self_healing/chaos/conftest.py 확인

**위치**: `tests/self_healing/chaos/conftest.py`

**확인 사항**:
- 상위 conftest.py와 중복 fixture 존재 여부
- 자체 Mock 정의 여부
- 정리 필요 여부 판단

---

### 2.4 폴더 구조 최종 형태

**정리 후 구조**:

```
tests/                              # 통합 테스트 전용
├── api/                            # API 통합 테스트
├── hybrid/                         # Celery 비동기 통합 테스트
├── integration/                    # selfhealing 통합 테스트
├── self_healing/
│   ├── api/                        # self-healing API 통합 테스트
│   ├── chaos/                      # Chaos Engineering 테스트
│   ├── e2e/                        # E2E 테스트
│   ├── integration/                # 통합 테스트
│   ├── services/                   # 서비스 통합 테스트
│   └── conftest.py                 # autouse=False로 수정
├── factories/                      # Factory/Builder 패키지
├── load/                           # 부하 테스트
└── conftest.py                     # 전역 fixture
```

**삭제될 폴더**:
- `tests/self_healing/unit/` (이동 후 삭제)
- `tests/_unclassified/` (확인 후 정리)

---

## 3. 상세 작업

### 3.1 Unit 테스트 이동 절차

**Step 1**: 현재 파일 목록 확인
```bash
find tests/self_healing/unit -name "*.py" | grep -v __pycache__
```

**Step 2**: 대상 폴더 생성 (없으면)
```bash
mkdir -p packages/selfhealing-python/tests/unit/core
```

**Step 3**: 파일 이동
```bash
mv tests/self_healing/unit/core/* packages/selfhealing-python/tests/unit/core/
mv tests/self_healing/unit/selfhealing/* packages/selfhealing-python/tests/unit/selfhealing/
# ... 각 폴더별 이동
```

**Step 4**: import 경로 확인
- 이동한 파일에서 `from tests.self_healing...` import가 있으면 수정

**Step 5**: 빈 폴더 삭제
```bash
rm -rf tests/self_healing/unit
```

---

### 3.2 conftest.py 수정 상세

**tests/self_healing/conftest.py 수정**:

변경할 부분:
- `@pytest.fixture(autouse=True, scope="function")` → `@pytest.fixture(scope="function")`
- `def mock_external_services():` 유지
- 사용이 필요한 테스트에서 명시적 요청

**영향받는 테스트**:
- `tests/self_healing/api/` 전체
- `tests/self_healing/services/` 전체

**마이그레이션 방법**:
- 각 테스트 클래스에 `@pytest.mark.usefixtures("mock_external_services")` 추가
- 또는 개별 테스트 함수에 fixture 파라미터 추가

---

### 3.3 _unclassified 폴더 처리

**위치**: `tests/_unclassified/`

**확인 사항**:
- 어떤 테스트가 있는지 확인
- 적절한 위치로 이동 또는 삭제

---

## 4. 검증 방법

### 4.1 이동 후 테스트 실행

```bash
# 이동된 unit 테스트 실행
cd packages/selfhealing-python
python -m pytest tests/unit/ -v --tb=short

# 전역 통합 테스트 실행
cd ../../
python -m pytest tests/self_healing/ -v --override-ini=addopts=
```

### 4.2 Mock 제거 확인

```bash
# mock_external_services 사용 현황 확인
grep -r "mock_external_services" tests/self_healing/
```

---

## 5. 완료 기준

- [ ] `tests/self_healing/unit/` 61개 파일 패키지로 이동
- [ ] 이동된 파일 테스트 통과
- [ ] `tests/self_healing/conftest.py` autouse=False로 수정
- [ ] 영향받는 테스트에 fixture 명시적 추가
- [ ] `tests/_unclassified/` 정리
- [ ] 전체 테스트 실행 성공

---

## 6. 주의사항

### 6.1 import 경로 충돌

이동하는 파일에서 다음 패턴 확인:
- `from tests.self_healing.unit...` → 수정 필요
- `from tests.factories...` → 유지 (전역 factories 참조)

### 6.2 conftest.py 상속

`tests/self_healing/unit/` 내부에 conftest.py가 있으면:
- 함께 이동
- 또는 패키지 conftest.py와 병합

### 6.3 순환 의존성

패키지 tests와 전역 tests 간 상호 참조 주의:
- 패키지 tests → 전역 factories: OK
- 전역 tests → 패키지 factories: 피해야 함

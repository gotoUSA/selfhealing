# 60. 전역 Tests 폴더 통합 테스트 리팩토링 마스터 플랜

> **범위**: `tests/` (전역 테스트 폴더, 173개 파일)  
> **목표**: 통합 테스트 전용으로 정리, Factory/Builder 패턴 적용, Docker 연결 표준화  
> **작성일**: 2026-01-20  
> **관련 문서**: 57_TEST_REFACTORING_PLAN.md, 58_TEST_FACTORY_IMPLEMENTATION.md

---

## 1. 개요

### 1.1 범위 구분

| 폴더 | 역할 | 현재 문제 |
|------|------|----------|
| `tests/` (전역) | **통합 테스트 전용** | Unit 테스트 혼재, Mock 남용 |
| `packages/selfhealing-python/tests/` | **패키지 Unit 테스트** | 별도 문서(57번)에서 다룸 |

### 1.2 핵심 원칙

1. **전역 tests 폴더 = 통합 테스트만** (실제 Docker 서비스 연결)
2. **Unit 테스트 = 패키지 폴더로 이동** (`packages/selfhealing-python/tests/unit/`)
3. **Mock 최소화** (통합 테스트에서는 실제 연결 사용)
4. **하드코딩 제거** (constants.py 활용)

---

## 2. 현재 상태 분석 (코드 기반)

### 2.1 수치 요약

| 항목 | 개수 | 위치 |
|------|------|------|
| 테스트 파일 | 173개 | `tests/` 전체 |
| Mock 사용 (@patch, MagicMock) | 925곳 | 전체 파일 |
| try/except 우회 패턴 | 195곳 | 주로 연결 실패 우회 |
| pytest.skip 사용 | 20+곳 | 인프라 부재 시 skip |
| 하드코딩된 localhost | 18+곳 | Redis, DB 연결 |
| 하드코딩된 포트 (6379/5432) | 15+곳 | 포트 불일치 원인 |

### 2.2 폴더별 파일 수

| 폴더 | 파일 수 | 성격 |
|------|--------|------|
| `tests/hybrid/` | 14개 | Celery 비동기 테스트 |
| `tests/self_healing/chaos/` | 20+개 | Chaos Engineering |
| `tests/self_healing/unit/` | 61개 | ❌ Unit 테스트 (이동 필요) |
| `tests/self_healing/api/` | 10개 | API 통합 테스트 |
| `tests/self_healing/integration/` | 5+개 | 통합 테스트 |
| `tests/integration/` | 6개 | selfhealing 통합 테스트 |
| `tests/api/` | 2개 | Canary/Governance API |

---

## 3. 발견된 문제점

### 3.1 포트 불일치 문제

**위치**: `docker-compose.yml` vs `docker-compose.test.yml` vs 테스트 파일

| 파일 | Redis 포트 |
|------|-----------|
| `docker-compose.yml` | 6379 |
| `docker-compose.test.yml` | 16379 |
| `tests/conftest.py` | 16379 (환경변수 기본값) |
| `tests/integration/.../test_canary_integration.py` | 6379 (하드코딩) |
| `tests/integration/test_hybrid_storage_integration.py` | 6379 (하드코딩) |

**영향**: 테스트 실행 환경에 따라 연결 실패 발생

### 3.2 자동 Mock 문제

**위치**: `tests/self_healing/conftest.py`

- `autouse=True` 로 모든 테스트에 Mock 자동 적용
- 통합 테스트에서도 실제 연결이 Mock으로 대체됨
- 실제 Docker 서비스 테스트 불가

### 3.3 Unit 테스트 위치 오류

**위치**: `tests/self_healing/unit/` (61개 파일)

- 전역 tests 폴더에 unit 테스트가 존재
- `packages/selfhealing-python/tests/unit/`으로 이동해야 함

### 3.4 Docker Compose 불완전

**위치**: `docker-compose.test.yml`

- Celery worker 서비스 없음
- 실제 비동기 태스크 테스트 불가
- `tests/hybrid/` 테스트들이 eager 모드로만 실행됨

---

## 4. 리팩토링 Phase 개요

| Phase | 이름 | 설명 | 문서 |
|-------|------|------|------|
| Phase 1 | 인프라 정리 | Docker Compose, 포트 통일 | 61번 문서 |
| Phase 2 | 구조 정리 | Unit 테스트 이동, conftest 정리 | 62번 문서 |
| Phase 3 | Factory 확장 | 추가 Builder/Factory 구현 | 63번 문서 |
| Phase 4 | 통합 테스트 리팩토링 | 폴더별 실제 리팩토링 | 64번 문서 |

---

## 5. 구현 완료 현황

### 5.1 완료된 항목 ✅

| 항목 | 위치 | 설명 |
|------|------|------|
| constants.py | `tests/factories/constants.py` | Domains, Services, Status 등 상수 |
| builders.py | `tests/factories/builders.py` | CircuitBreakerStateBuilder, FailedOperationBuilder |
| data_factory.py | `tests/factories/data_factory.py` | TestDataFactory |
| integration.py | `tests/factories/integration.py` | RealRedisClientFactory, RealDatabaseFactory |
| conftest.py 정리 | `tests/conftest.py` | Mock 관련 fixture 제거 |

### 5.2 삭제된 항목 ❌

| 항목 | 이유 |
|------|------|
| `tests/factories/mocks.py` | 통합 테스트에 Mock 불필요 |
| `tests/factories/fixtures.py` | mocks.py 의존 |
| `tests/unit/` | 패키지 폴더로 이동 완료 |

---

## 6. 다음 단계

각 Phase의 상세 계획은 다음 문서를 참조:

1. **61_GLOBAL_TESTS_PHASE1_INFRASTRUCTURE.md** - 인프라 정리
2. **62_GLOBAL_TESTS_PHASE2_STRUCTURE.md** - 구조 정리  
3. **63_GLOBAL_TESTS_PHASE3_FACTORY_EXTENSION.md** - Factory 확장
4. **64_GLOBAL_TESTS_PHASE4_REFACTORING.md** - 통합 테스트 리팩토링

---

## 7. 참조 파일 목록

### 7.1 Docker 관련
- `docker-compose.yml`
- `docker-compose.test.yml`
- `docker-compose.stage16.yml`
- `Dockerfile`

### 7.2 Factory 패키지
- `tests/factories/__init__.py`
- `tests/factories/constants.py`
- `tests/factories/builders.py`
- `tests/factories/data_factory.py`
- `tests/factories/integration.py`

### 7.3 주요 conftest.py
- `tests/conftest.py`
- `tests/self_healing/conftest.py`
- `tests/self_healing/chaos/conftest.py`

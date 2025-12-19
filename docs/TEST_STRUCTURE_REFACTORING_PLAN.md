# 테스트 구조 리팩토링 계획

> 작성일: 2025-12-19  
> 상태: 계획 수립 완료, 1단계 적용 완료

---

## 📊 현재 상태 분석

### 문제점

1. **테스트 분류 혼란**
   - `tests/self_healing/unit/` 내 테스트들이 실제로는 DB 필요 (integration 수준)
   - `load_tests/scenarios/`에 Load + Chaos + Integration 테스트 혼재 (70개+)
   - `tests/hybrid/` 폴더 목적 불명확

2. **인프라 의존성 불명확**
   - 어떤 테스트가 DB/Redis 필요한지 파일 열어봐야 알 수 있음
   - CI에서 DB 없이 실행하면 129개 에러 발생

3. **빈 폴더 및 중복**
   - `tests/e2e/` 빈 폴더 (삭제 완료 ✅)
   - `tests/_out_of_scope/` 불필요 (삭제 완료 ✅)
   - `tests/self_healing/load/` 빈 폴더 (삭제 완료 ✅)

---

## 🎯 목표 구조

```
myproject/
├── tests/                           # pytest 기반 테스트
│   ├── unit/                        # 순수 단위 테스트 (DB/외부 의존성 없음)
│   │   ├── self_healing/
│   │   ├── payment/
│   │   └── ...
│   │
│   ├── integration/                 # 통합 테스트 (Mock 또는 In-Memory)
│   │   ├── self_healing/            # ← Mock 변환 완료
│   │   ├── payment/
│   │   └── ...
│   │
│   ├── e2e/                         # End-to-End (실제 DB + 전체 플로우)
│   │   └── self_healing/
│   │
│   ├── chaos/                       # 카오스 엔지니어링 (pytest 기반)
│   │   └── self_healing/
│   │
│   └── conftest.py                  # 전역 fixtures + 자동 skip 로직
│
├── load_tests/                      # Locust 부하 테스트 (별도 유지)
│   ├── scenarios/
│   │   ├── load/                    # 순수 부하 테스트 (stage1, stage3, ...)
│   │   ├── chaos/                   # Locust 기반 카오스 (stage6, stage39, ...)
│   │   └── hybrid/                  # 혼합 시나리오 (stage12, ...)
│   ├── chaos/                       # 카오스 도구 (fault_injector, toxiproxy)
│   └── ...
│
└── pyproject.toml                   # pytest 마커 정의
```

---

## 📋 단계별 작업 계획

### 1단계: 마커 시스템 정비 ✅ 완료

- [x] pyproject.toml에 마커 추가 (`unit`, `e2e`, `requires_db`, `requires_redis`, `flaky`)
- [x] skip → 마커 변환 (8개 파일)
- [x] 빈 폴더 삭제
- [x] testpaths에 `tests` 추가
- [x] addopts에 `not e2e and not requires_redis and not requires_db and not flaky` 추가

### 2단계: 자동 Skip 로직 (즉시 적용 가능) 🔄

**파일**: `tests/conftest.py`

```python
import pytest

@pytest.fixture(scope="session", autouse=True)
def check_db_connection():
    """DB 연결 확인 - 실패 시 requires_db 테스트 자동 skip"""
    try:
        from django.db import connection
        connection.ensure_connection()
    except Exception:
        pytest.skip("Database not available", allow_module_level=True)
```

### 3단계: Unit 테스트 분류 (중간 우선순위)

**작업 내용**: `tests/self_healing/unit/` 내 `@pytest.mark.django_db` 사용 파일에 `@pytest.mark.requires_db` 추가

**대상 파일** (129개 에러 발생 원인):
- `test_circuit_breaker_service.py`
- `test_manual_override_policy.py`
- `test_cost_aware_recovery.py`
- `test_dlq_retention.py`
- `test_metrics_dlq_pending.py`
- `test_observability_tasks.py`
- `test_retry_persistence.py`
- `test_security_violation_service.py`
- `test_security_notification_service.py`
- `test_retry_configuration.py` (일부)

**명령어**:
```bash
# django_db 마커가 있는 파일 찾기
grep -r "@pytest.mark.django_db" tests/self_healing/unit/ --include="*.py" -l
```

### 4단계: Load Test 구조 정리 (낮은 우선순위)

**현재**: `load_tests/scenarios/` 70개+ 파일 혼재

**목표**:
```
load_tests/scenarios/
├── load/                    # 순수 부하 테스트
│   ├── stage0_smoke.py
│   ├── stage1_happy_load.py
│   ├── stage3_latency.py
│   ├── stage9_soak.py
│   └── stage11_ramp_threshold.py
│
├── chaos/                   # 카오스 + 복구 테스트
│   ├── stage6_chaos_random.py
│   ├── stage39_k8s_runtime_chaos.py
│   ├── stage42_compound_failure_chaos.py
│   └── stage43_operator_race_chaos.py
│
├── integration/             # DB/API 통합 시나리오
│   ├── stage14_dlq_replay.py
│   ├── stage15_cb_transitions.py
│   └── stage16_db_lock_recovery.py
│
└── hybrid/                  # 혼합 (spike + recovery)
    ├── stage12_spike_recovery.py
    └── stage13_repeated_spike.py
```

**분류 기준**:
| 파일명 패턴 | 분류 |
|------------|------|
| `_chaos`, `_random` | chaos/ |
| `_load`, `_happy`, `_latency`, `_soak` | load/ |
| `_dlq`, `_cb_`, `_db_` | integration/ |
| `_spike`, `_recovery` | hybrid/ |

### 5단계: Hybrid 폴더 정리 (낮은 우선순위)

**현재 `tests/hybrid/`** (14개 파일):
- Celery 관련: `test_celery_*.py` (4개)
- Idempotency 관련: `test_idempotency_*.py` (3개)
- SLA/Timing 관련: `test_sla_*.py`, `test_*_timing.py` (4개)
- 기타: `test_toss_timeout_retry.py` 등

**방안**:
1. **유지**: 현재 위치 유지 + `requires_db` 마커 추가
2. **이동**: 각각 `tests/integration/{domain}/`으로 분산

**권장**: 1번 (유지) - 이동 시 import 경로 문제 발생 가능

---

## 🔧 실행 방법 가이드

### 로컬 개발 (DB 없이 빠른 피드백)
```bash
# Mock 기반 테스트만 실행 (기본 설정)
pytest tests/ --no-cov -n6

# 특정 도메인만
pytest tests/self_healing/ --no-cov -n6
```

### CI 파이프라인 설정 예시

```yaml
# .github/workflows/test.yml
jobs:
  fast-tests:
    runs-on: ubuntu-latest
    steps:
      - run: pytest -m "not requires_db and not requires_redis" -n6 --no-cov
      # 예상 소요: 1-2분

  integration-tests:
    runs-on: ubuntu-latest
    needs: fast-tests
    services:
      postgres:
        image: postgres:15
      redis:
        image: redis:7
    steps:
      - run: pytest -m "requires_db or requires_redis" --no-cov
      # 예상 소요: 5-10분

  e2e-tests:
    runs-on: ubuntu-latest
    needs: integration-tests
    steps:
      - run: docker-compose -f docker-compose.test.yml up -d
      - run: pytest -m "e2e" --no-cov
      # 예상 소요: 10-15분
```

### E2E 테스트 (Docker 필요)
```bash
# Docker Compose로 인프라 띄우기
docker-compose -f docker-compose.test.yml up -d

# E2E 테스트 실행
pytest -m "e2e or requires_db" --no-cov

# 정리
docker-compose -f docker-compose.test.yml down
```

### 부하 테스트 (Locust)
```bash
# Stage 1: 기본 부하
locust -f load_tests/scenarios/stage1_happy_load.py \
    --host=http://localhost:8000 \
    --users=100 --spawn-rate=20 --run-time=3m --headless

# 카오스 테스트
CHAOS_ENABLED=true locust -f load_tests/scenarios/stage6_chaos_random.py \
    --host=http://localhost:8000 \
    --users=100 --spawn-rate=20 --run-time=5m --headless
```

---

## 📊 마커 참조표

| 마커 | 의미 | 기본 실행 |
|------|------|----------|
| `unit` | 순수 단위 테스트 | ✅ 포함 |
| `integration` | 통합 테스트 | ✅ 포함 |
| `e2e` | End-to-End | ❌ 제외 |
| `requires_db` | DB 필요 | ❌ 제외 |
| `requires_redis` | Redis 필요 | ❌ 제외 |
| `requires_celery` | Celery Worker 필요 | ❌ 제외 |
| `flaky` | 불안정 (수정 필요) | ❌ 제외 |
| `slow` | 느린 테스트 | ❌ 제외 |
| `chaos` | 카오스 엔지니어링 | ❌ 제외 |
| `tier1` | PR 검증용 (< 2분) | ✅ 포함 |
| `tier2` | 머지 검증용 (< 10분) | 조건부 |
| `tier3_chaos` | 야간 실행 | ❌ 제외 |
| `tier4_load` | 릴리즈 전 | ❌ 제외 |

---

## ✅ 완료된 작업

- [x] pyproject.toml 마커 정의
- [x] skip → 마커 변환 (8개 파일)
- [x] 빈 폴더 삭제 (3개)
- [x] Mock 변환 완료 (test_l3_self_healing.py, test_cold_start_recovery.py, test_observability_metrics.py)
- [x] testpaths 업데이트
- [x] 자동 skip 로직 추가 (conftest.py)
- [x] unit 테스트 requires_db 마커 추가

---

## 📅 예상 작업량

| 단계 | 작업량 | 우선순위 | 예상 시간 |
|------|--------|----------|----------|
| 1단계 | 마커 시스템 | ✅ 완료 | - |
| 2단계 | 자동 skip | ✅ 완료 | - |
| 3단계 | Unit 분류 | ✅ 완료 | - |
| 4단계 | Load Test 정리 | 낮음 | 2-3시간 |
| 5단계 | Hybrid 정리 | 낮음 | 1시간 |

---

## 🔗 관련 문서

- [INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md](./INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md)
- [EXIT_CHECKLIST.md](./EXIT_CHECKLIST.md)

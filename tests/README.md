# 테스트 가이드

> 최종 업데이트: 2025-12-19

---

## 🚀 빠른 시작

```bash
# 기본 실행 (DB/Redis 없이 실행 가능한 테스트만)
pytest --no-cov

# 병렬 실행 (6개 워커)
pytest --no-cov -n6

# 특정 도메인만
pytest tests/self_healing/ --no-cov -n6

# DB 필요한 테스트 포함
TEST_DB_AVAILABLE=true pytest -m "requires_db" --no-cov

# 전체 테스트 (DB + Redis)
TEST_DB_AVAILABLE=true TEST_REDIS_AVAILABLE=true pytest --no-cov
```

---

## 📁 폴더 구조

```
tests/
├── conftest.py              # 전역 fixtures + 자동 skip 로직
├── README.md                # 이 파일
│
├── self_healing/            # Self-Healing 시스템 테스트
│   ├── unit/                # 단위 테스트 (일부 DB 필요 → requires_db 마커)
│   └── integration/         # 통합 테스트 (Mock 기반, DB 불필요)
│
├── hybrid/                  # Celery/타이밍 관련 테스트
│   ├── test_celery_*.py     # Celery 워커 테스트
│   ├── test_idempotency_*.py # 멱등성 테스트
│   └── test_sla_*.py        # SLA/타이밍 테스트
│
├── load/                    # 부하 테스트 (pytest 기반)
│
└── _unclassified/           # 미분류 테스트 (정리 예정)
```

---

## 🏷️ 마커 (Markers)

### 인프라 의존성 마커

| 마커 | 의미 | 기본 실행 | 환경변수 |
|------|------|----------|----------|
| `requires_db` | PostgreSQL 필요 | ❌ 제외 | `TEST_DB_AVAILABLE=true` |
| `requires_redis` | Redis 필요 | ❌ 제외 | `TEST_REDIS_AVAILABLE=true` |
| `requires_celery` | Celery Worker 필요 | ❌ 제외 | - |
| `e2e` | End-to-End (전체 인프라) | ❌ 제외 | - |

### 실행 특성 마커

| 마커 | 의미 | 기본 실행 |
|------|------|----------|
| `unit` | 순수 단위 테스트 | ✅ 포함 |
| `integration` | 통합 테스트 | ✅ 포함 |
| `slow` | 느린 테스트 (>10초) | ❌ 제외 |
| `flaky` | 불안정 (수정 필요) | ❌ 제외 |

### CI 티어 마커

| 마커 | 용도 | 예상 시간 |
|------|------|----------|
| `tier1` | PR 검증용 | < 2분 |
| `tier2` | 머지 검증용 | < 10분 |
| `tier3_chaos` | 야간 실행 | 30분+ |
| `tier4_load` | 릴리즈 전 | 1시간+ |

---

## ⚙️ 자동 Skip 로직

`tests/conftest.py`에서 인프라 의존성 마커가 있는 테스트를 자동으로 skip합니다:

```python
# 환경변수가 없으면 자동 skip
TEST_DB_AVAILABLE=true    # requires_db 테스트 실행
TEST_REDIS_AVAILABLE=true # requires_redis 테스트 실행
```

### 작동 방식

1. `pytest` 실행 시 `pyproject.toml`의 `addopts` 설정으로 기본 제외
2. `conftest.py`의 `pytest_collection_modifyitems` 훅으로 추가 제어
3. 환경변수로 선택적 활성화

---

## 🔧 주요 실행 시나리오

### 로컬 개발 (빠른 피드백)

```bash
# Mock 기반 테스트만 (DB 불필요)
pytest tests/self_healing/integration/ --no-cov -n6
```

### Docker Compose로 인프라 띄우고 테스트

```bash
# 인프라 시작
docker-compose -f docker-compose.test.yml up -d

# DB 필요한 테스트 실행
TEST_DB_AVAILABLE=true pytest -m "requires_db" --no-cov

# 정리
docker-compose -f docker-compose.test.yml down
```

### CI 파이프라인 예시

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
    env:
      TEST_DB_AVAILABLE: "true"
      TEST_REDIS_AVAILABLE: "true"
    steps:
      - run: pytest -m "requires_db or requires_redis" --no-cov
      # 예상 소요: 5-10분
```

---

## 📂 Self-Healing 테스트 상세

### unit/ (단위 테스트)

DB 필요 여부가 `pytestmark = pytest.mark.requires_db`로 표시됨:

| 파일 | 설명 | DB 필요 |
|------|------|---------|
| `test_circuit_breaker_service.py` | Circuit Breaker 서비스 | ✅ |
| `test_circuit_breaker_ttl.py` | CB TTL 만료 | ✅ |
| `test_cost_aware_recovery.py` | 비용 인식 복구 | ✅ |
| `test_dlq_retention.py` | DLQ 보존 정책 | ✅ |
| `test_manual_override_policy.py` | 수동 오버라이드 | ✅ |
| `test_metrics_dlq_pending.py` | DLQ 메트릭 | ✅ |
| `test_observability_tasks.py` | 관측성 태스크 | ✅ |
| `test_retry_configuration.py` | 재시도 설정 | ❌ |
| `test_retry_persistence.py` | 재시도 영속성 | ✅ |
| `test_security_*.py` | 보안 서비스 | ✅ |

### integration/ (통합 테스트)

**Mock 기반으로 DB 없이 실행 가능** (변환 완료):

| 파일 | 설명 |
|------|------|
| `test_l3_self_healing.py` | L3 Self-Healing 전체 플로우 |
| `test_cold_start_recovery.py` | Cold Start 복구 |
| `test_observability_metrics.py` | 관측성 메트릭 |
| `test_accountability_audit.py` | 감사 로그 |
| `test_cost_governance.py` | 비용 거버넌스 |
| ... | |

---

## 📊 Load Tests (부하 테스트)

별도 폴더 `load_tests/`에서 Locust 기반으로 실행:

```bash
# Stage 1: 기본 부하
locust -f load_tests/scenarios/stage1_happy_load.py \
    --host=http://localhost:8000 \
    --users=100 --spawn-rate=20 --run-time=3m --headless
```

### 시나리오 분류 (74개)

| 카테고리 | 파일 수 | 설명 |
|---------|---------|------|
| **load/** | 5개 | 순수 부하 (stage0, 1, 3, 9, 11) |
| **chaos/** | 19개 | 카오스 엔지니어링 (stage6, 16, 18, 19, ...) |
| **integration/** | 39개 | 통합 시나리오 (stage2, 5, 7, 8, ...) |
| **hybrid/** | 10개 | 혼합 (stage4, 12, 13, 21, ...) |

---

## 🛠️ 트러블슈팅

### "Database not available" 에러

```bash
# 해결: 환경변수 설정
TEST_DB_AVAILABLE=true pytest -m "requires_db"

# 또는 Docker로 DB 띄우기
docker-compose -f docker-compose.test.yml up -d postgres
```

### 129개 테스트 실패

DB 없이 `requires_db` 테스트 실행 시 발생. 기본 설정으로 자동 skip됨.

### Celery 관련 테스트 실패

```bash
# Celery 워커 필요
celery -A myproject worker -l info &
pytest tests/hybrid/test_celery_*.py
```

---

## 🔗 관련 문서

- [INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md](../docs/INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md) - Mock 변환 가이드
- [EXIT_CHECKLIST.md](../docs/EXIT_CHECKLIST.md) - 릴리즈 체크리스트

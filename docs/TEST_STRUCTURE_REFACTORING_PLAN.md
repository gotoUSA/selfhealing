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

**현재**: `load_tests/scenarios/` 74개 파일 혼재

**분류 결과** (전체 파일 분석 완료):

#### load/ (순수 부하 - 5개)
| 파일 | 설명 |
|------|------|
| `stage0_smoke.py` | 스모크 테스트 - 환경/로그인/기본 플로우 검증 |
| `stage1_happy_load.py` | Happy Path 부하 테스트 - 정상 성능 측정 (Baseline) |
| `stage3_latency.py` | 레이턴시/타임아웃 주입 테스트 - PG 지연 시뮬레이션 |
| `stage9_soak.py` | Soak 테스트 - 장시간 부하로 리소스 누수 탐지 |
| `stage11_ramp_threshold.py` | 램프업 임계점 탐색 - 시스템 한계점 발견 |

#### chaos/ (카오스 엔지니어링 - 19개)
| 파일 | 설명 |
|------|------|
| `stage6_chaos_random.py` | 랜덤 장애 주입 테스트 - 3%~15% 확률 실패 |
| `stage9_worker_crash.py` | Worker 강제 종료 복구 테스트 - in-flight 작업 복구 |
| `stage16_db_lock_recovery.py` | DB Lock/Deadlock 복구 테스트 |
| `stage16_db_lock_recovery_locust.py` | DB Lock 복구 Locust 버전 |
| `stage18_chain_failure.py` | 체인 장애 전파 테스트 (Auth→Order→Payment) |
| `stage19_rollback_failure.py` | 롤백 실패 (이중 장애) 테스트 |
| `stage24_partial_partition.py` | 부분 네트워크 단절 테스트 |
| `stage25_tls_failure.py` | TLS/인증서 실패 테스트 |
| `stage26_connection_pool.py` | Connection Pool 고갈 테스트 |
| `stage26_extreme_pool_test.py` | 극한 Pool 고갈 테스트 |
| `stage27_graceful_shutdown.py` | Graceful Shutdown 테스트 - 요청 유실 방지 |
| `stage28_multi_region.py` | 멀티 리전 레이턴시/장애 테스트 |
| `stage34_db_deadlock.py` | DB Deadlock Locust 대규모 동시성 테스트 |
| `stage38_multi_region_failover.py` | 멀티 리전 Failover 카오스 테스트 |
| `stage38_multi_region_failover_locust.py` | 멀티 리전 Failover Locust 버전 |
| `stage39_k8s_runtime_chaos.py` | K8s 런타임 카오스 테스트 - 컨테이너 장애 시뮬레이션 |
| `stage42_compound_failure_chaos.py` | 복합 장애 카오스 테스트 |
| `stage42_compound_failure_chaos_locust.py` | 복합 장애 카오스 Locust 버전 |
| `stage43_operator_race_chaos.py` | 운영자 경쟁 조건 카오스 테스트 |

#### integration/ (통합 시나리오 - 39개)
| 파일 | 설명 |
|------|------|
| `stage2_idempotent.py` | 멱등성 테스트 - 중복 결제 방지 검증 |
| `stage5_rollback.py` | 롤백 검증 테스트 - 재고/포인트 복원 |
| `stage7_race_conflict.py` | Race Condition 테스트 - 동시 접근 검증 |
| `stage8_webhook.py` | Webhook 신뢰성 테스트 - 중복/순서역전/지연 |
| `stage08_observability.py` | Observability Contract HTTP 통합 테스트 |
| `stage10_self_healing.py` | Self-Healing Control API 테스트 |
| `stage14_dlq_replay.py` | DLQ 리플레이 검증 테스트 |
| `stage14_outbox.py` | Outbox 패턴 검증 테스트 |
| `stage14_outbox_http.py` | Outbox 패턴 HTTP 통합 테스트 |
| `stage15_cb_transitions.py` | Circuit Breaker 자동 상태 전이 테스트 |
| `stage17_cache_ttl_race.py` | 캐시 TTL Race 테스트 - 캐시/DB 일관성 |
| `stage17_cache_ttl_race_locust.py` | 캐시 TTL Race Locust 버전 |
| `stage17_event_invalidation.py` | 이벤트 기반 캐시 무효화 테스트 |
| `stage17_event_invalidation_locust.py` | 이벤트 기반 캐시 무효화 Locust 버전 |
| `stage20_delayed_webhook.py` | 지연/순서역전 Webhook 테스트 |
| `stage20_delayed_webhook_locust.py` | 지연 Webhook Docker Locust 테스트 |
| `stage23_clock_skew.py` | Clock Skew/NTP 드리프트 테스트 |
| `stage24_cache_dead_protection.py` | Redis 장애 시 DB 과부하 방지 테스트 |
| `stage29_backpressure.py` | 백프레셔 정책 테스트 |
| `stage29_bulk_dlq_replay.py` | 대량 DLQ 리플레이 테스트 (10만건) |
| `stage30_schedule_drift.py` | 스케줄 드리프트 테스트 (CRON/Celery Beat) |
| `stage35_cache_poison.py` | 캐시 오염 탐지 테스트 |
| `stage35_cache_poison_http.py` | 캐시 오염 HTTP 통합 테스트 |
| `stage35_cache_stampede.py` | 캐시 스탬피드 방지 테스트 |
| `stage35_distributed_test.py` | Redis 분산 락 멀티워커 테스트 |
| `stage35_distributed_test_v2.py` | Redis 분산 락 v2 테스트 |
| `stage35_redis_stampede.py` | Redis 분산 락 스탬피드 방지 테스트 |
| `stage37_schema_compat.py` | 스키마 호환성 테스트 (Rolling Update) |
| `stage37_schema_compat_http.py` | 스키마 호환성 HTTP 통합 테스트 |
| `stage39_k8s_governance_boundaries.py` | K8s 거버넌스 경계 검증 테스트 |
| `stage40_idempotency_clock_skew.py` | 멱등성 + Clock Skew 테스트 |
| `stage41_tls_certificate_expiry.py` | TLS 인증서 만료 테스트 (단일) |
| `stage41_tls_certificate_expiry_fleet.py` | TLS 인증서 만료 테스트 (Fleet 레벨) |
| `stage42_compound_failure_deterministic.py` | 복합 장애 결정론적 테스트 |
| `stage43_operator_race_deterministic.py` | 운영자 경쟁 결정론적 테스트 |
| `stage44_zero_downtime_migration.py` | 제로 다운타임 마이그레이션 테스트 |
| `stage45_external_trust_audit.py` | 외부 신뢰/감사 준비도 테스트 (Part 1) |
| `stage45_external_trust_reports.py` | 외부 신뢰/감사 보고서 테스트 (Part 2) |
| `visibility_self_healing_locust.py` | Self-Healing 가시성 검증 Locust 테스트 |

#### hybrid/ (혼합 시나리오 - 10개)
| 파일 | 설명 |
|------|------|
| `stage4_cancel_storm.py` | 취소 폭주 테스트 - confirm 후 즉시 cancel |
| `stage12_spike_recovery.py` | 스파이크 후 복구 테스트 - 급격한 트래픽 증가 후 안정화 |
| `stage13_repeated_spike.py` | 반복 스파이크 테스트 - 백오프 누적 방지 검증 |
| `stage21_false_positive.py` | False Positive 탐지 테스트 - Self-Healing 오탐 방지 |
| `stage22_rate_limit_conflict.py` | Self-Healing + Rate Limit 충돌 테스트 - Self-DDoS 방지 |
| `stage31_cascade_extended.py` | 확장 Cascade 장애 테스트 (Redis→DB→CB) |
| `stage32_retry_storm_extended.py` | 확장 Retry Storm 테스트 - 메모리 누수/DLQ 폭발 방지 |
| `stage33_jwt_cascade.py` | JWT 만료 스탬피드 테스트 - Auth 서버 Failover |
| `stage36_memory_pressure.py` | 메모리 압박 테스트 - OOM 방지 |
| `stage36_real_http.py` | 실제 HTTP 메모리 압박 테스트 |

#### 제외 (유틸리티 - 1개)
| 파일 | 설명 |
|------|------|
| `__init__.py` | 패키지 초기화 파일 |

**분류 요약**:
| 카테고리 | 파일 수 |
|---------|---------|
| load/ | 5개 |
| chaos/ | 19개 |
| integration/ | 39개 |
| hybrid/ | 10개 |
| 제외 | 1개 |
| **총계** | **74개** |

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
| 4단계 | Load Test 정리 (74개 분류 완료) | 낮음 | 1-2시간 (폴더 이동만) |
| 5단계 | Hybrid 정리 | 낮음 | 1시간 |

---

## 🔗 관련 문서

- [INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md](./INTEGRATION_TEST_MOCK_CONVERSION_GUIDE.md)
- [EXIT_CHECKLIST.md](./EXIT_CHECKLIST.md)

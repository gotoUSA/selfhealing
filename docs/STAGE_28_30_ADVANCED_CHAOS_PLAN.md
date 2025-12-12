# Stage 28-30: Advanced Chaos Engineering 테스트 계획서

> 작성일: 2025-12-12
> 목적: 현재 셀프힐링 테스트의 Gap을 보완하는 고급 Chaos 테스트 설계

---

## 📋 개요

### 배경
현재 Stage 0-27까지 셀프힐링 테스트가 구현되어 있으나, 프로덕션 환경에서 발생할 수 있는 **복합 장애 시나리오**에 대한 테스트가 부족합니다.

### Gap 분석 요약

| 요구사항 | 현재 상태 | 필요 조치 |
|----------|----------|-----------|
| Cascade Failure (연쇄 장애) | Stage 18 부분 커버 | 확장 필요 |
| Retry Storm Simulation | Stage 22 부분 커버 | 확장 필요 |
| Multi-Region Latency | ❌ 없음 | **Stage 28 신규** |
| Partial DB Commit + Deadlock | Stage 16 부분 커버 | 확장 필요 |
| JWT/Token Cascade | Stage 23 부분 커버 | 확장 필요 |
| Bulk Replay (DLQ 10만건) | ❌ 없음 | **Stage 29 신규** |
| Schedule Drift (CRON) | ❌ 없음 | **Stage 30 신규** |

---

## 🆕 Stage 28: Multi-Region Latency Test

### 목적
클라우드 멀티 리전 환경에서 네트워크 이상 시 복원력 검증

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Region Architecture                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────────┐         ┌──────────────┐                 │
│   │   Zone A     │ ◄─────► │   Zone B     │                 │
│   │   (정상)     │   WAN   │  (90% 손실)  │                 │
│   │              │         │              │                 │
│   │  ┌────────┐  │         │  ┌────────┐  │                 │
│   │  │   DB   │  │         │  │   DB   │  │                 │
│   │  │ Primary│  │         │  │Replica │  │                 │
│   │  └────────┘  │         │  └────────┘  │                 │
│   └──────────────┘         └──────────────┘                 │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-28-1: Region 간 비대칭 장애
```python
"""
시나리오:
  - Region A: 정상 동작 (latency < 10ms)
  - Region B: 90% 패킷 손실

검증:
  - Region B 요청이 A로 자동 fallback
  - Fallback 시 데이터 일관성 유지
  - 복구 후 B로 다시 라우팅
"""
```

#### TC-28-2: 단일 리전 Clock Skew
```python
"""
시나리오:
  - Region A: 서버 시계 정상
  - Region B: 서버 시계 +5분 drift

검증:
  - Cross-region 트랜잭션 타임스탬프 충돌 처리
  - Idempotency key 중복 방지
  - 분산 락 정확성 유지
"""
```

#### TC-28-3: Cross-Region Fallback Consistency
```python
"""
시나리오:
  1. Region A에서 주문 생성 시작
  2. Region A 장애 발생
  3. Region B로 failover
  4. 동일 주문 처리 시도

검증:
  - 중복 주문 방지
  - 결제 중복 방지
  - 재고 일관성
"""
```

### 구현 설계

```python
# load_tests/scenarios/stage28_multi_region.py

class RegionConfig:
    """리전 설정"""
    REGION_A = {
        "name": "ap-northeast-2a",
        "latency_ms": 5,
        "packet_loss": 0.0,
        "clock_offset_sec": 0
    }
    REGION_B = {
        "name": "ap-northeast-2b",
        "latency_ms": 50,
        "packet_loss": 0.9,  # 90% 손실
        "clock_offset_sec": 300  # 5분 drift
    }

class MultiRegionUser(HttpUser):
    """멀티 리전 사용자"""

    @task
    def cross_region_order(self):
        """리전 간 주문 테스트"""
        # 1. Region A에서 주문 시작
        # 2. 장애 주입
        # 3. Region B fallback 확인
        # 4. 일관성 검증
        pass
```

### Toxiproxy 설정

```yaml
# docker-compose.stage28.yml
services:
  toxiproxy:
    image: ghcr.io/shopify/toxiproxy
    ports:
      - "8474:8474"

  region_a_proxy:
    # Region A 프록시 (정상)

  region_b_proxy:
    # Region B 프록시 (90% 패킷 손실)
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| Failover 시간 | < 5초 | Region 장애 감지 → fallback 완료 |
| 데이터 일관성 | 100% | 중복 주문/결제 0건 |
| Clock Skew 허용 | ±30초 | 30초 이상 drift 시 거부 |
| 복구 후 라우팅 | < 10초 | 정상화 감지 → 원래 리전 복귀 |

---

## 🆕 Stage 29: Bulk DLQ Replay Test

### 목적
대량 DLQ 재처리 시 시스템 안정성 검증 (10만 건 시나리오)

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                     DLQ Bulk Replay Flow                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   ┌──────────┐    100,000    ┌──────────┐                   │
│   │   DLQ    │ ───────────►  │  Replay  │                   │
│   │  Queue   │    messages   │  Worker  │                   │
│   └──────────┘               └────┬─────┘                   │
│        ▲                          │                          │
│        │ 재실패                    ▼                          │
│        │                    ┌──────────┐                    │
│   ┌────┴─────┐              │ Circuit  │                    │
│   │  Re-DLQ  │◄─────────────│ Breaker  │                    │
│   │  Queue   │   CB Open    └──────────┘                    │
│   └──────────┘                    │                          │
│                                   ▼                          │
│                            ┌──────────┐                     │
│                            │ Success  │                     │
│                            │  Queue   │                     │
│                            └──────────┘                     │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-29-1: Replay 속도 조절 (Throttling)
```python
"""
시나리오:
  - DLQ에 100,000건 메시지 적재
  - Replay 속도: 1,000 TPS → 500 TPS → 100 TPS 조절

검증:
  - 백프레셔 정상 동작
  - 메모리 사용량 안정
  - 처리 지연 없음
"""
```

#### TC-29-2: Replay 중 Circuit Breaker 상태 변화
```python
"""
시나리오:
  - 50,000건 처리 중 외부 API 장애
  - CB OPEN 발생
  - 잔여 50,000건 처리 방식

검증:
  - CB OPEN 시 replay 일시 중단
  - CB HALF-OPEN 시 점진적 재개
  - CB CLOSED 후 정상 처리
"""
```

#### TC-29-3: 재처리 실패 → 재DLQ
```python
"""
시나리오:
  - 100,000건 중 10% 재실패
  - 재실패 메시지 별도 Re-DLQ로 이동

검증:
  - Re-DLQ 정확한 건수 (10,000건)
  - 원본 메시지 손실 없음
  - Retry 횟수 정확히 기록
"""
```

#### TC-29-4: Queue Size Spike Limit
```python
"""
시나리오:
  - DLQ 사이즈 급증 (100K → 500K)
  - 메모리 한계 도달

검증:
  - Queue size 경고 알림
  - 자동 throttling 발동
  - OOM 방지
"""
```

### 구현 설계

```python
# load_tests/scenarios/stage29_bulk_dlq_replay.py

BULK_DLQ_CONFIG = {
    "total_messages": 100_000,
    "batch_size": 1_000,
    "initial_tps": 1_000,
    "min_tps": 100,
    "max_queue_size": 500_000,
    "memory_limit_mb": 1024,
    "re_dlq_max_retries": 3,
}

class BulkDLQStats:
    """대량 DLQ 처리 통계"""

    def __init__(self):
        self.total_messages = 0
        self.processed = 0
        self.succeeded = 0
        self.failed = 0
        self.re_dlq_count = 0
        self.cb_open_events = 0
        self.throttle_events = 0
        self.memory_peaks = []
        self.tps_history = []

class BulkDLQReplayUser(HttpUser):
    """대량 DLQ 재처리 사용자"""

    @task
    def replay_batch(self):
        """배치 재처리"""
        pass

    @task
    def monitor_cb_state(self):
        """CB 상태 모니터링"""
        pass

    @task
    def check_memory_usage(self):
        """메모리 사용량 체크"""
        pass
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| 처리 완료율 | > 99% | 100,000건 중 99,000건 이상 |
| 메모리 사용량 | < 1GB | 피크 시에도 1GB 미만 |
| Throttle 반응 시간 | < 1초 | Queue 급증 감지 → throttle 발동 |
| Re-DLQ 정확성 | 100% | 실패 건수 = Re-DLQ 건수 |
| CB 상태 전이 | 정상 | OPEN → HALF-OPEN → CLOSED 정상 전이 |

---

## 🆕 Stage 30: Schedule Drift Test (CRON / Celery Beat)

### 목적
스케줄러 시간 드리프트 시 시스템 복원력 검증

### 시나리오

```
┌─────────────────────────────────────────────────────────────┐
│                   Schedule Drift Scenarios                   │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  정상 상태:                                                   │
│  ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐                         │
│  │Job1│ │Job2│ │Job3│ │Job4│ │Job5│                         │
│  └────┘ └────┘ └────┘ └────┘ └────┘                         │
│    ▼      ▼      ▼      ▼      ▼                            │
│   10:00  10:01  10:02  10:03  10:04                         │
│                                                              │
│  Drift 발생 후:                                               │
│  ┌────┐                                                      │
│  │Job1│ (10분 지연)                                          │
│  └────┘                                                      │
│    │    ┌────┐┌────┐┌────┐┌────┐┌────┐                      │
│    │    │Job2││Job3││Job4││Job5││Job6│ (동시 실행)          │
│    │    └────┘└────┘└────┘└────┘└────┘                      │
│    ▼      ▼                                                  │
│   10:10  10:10                                               │
│                                                              │
│  → Replay Storm 발생!                                        │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 테스트 케이스

#### TC-30-1: Celery Beat Drift
```python
"""
시나리오:
  - Celery Beat 스케줄러 10분 지연
  - 지연된 10개 작업이 동시 실행 시도

검증:
  - 동시 실행 제한 (max_concurrent)
  - 우선순위 기반 처리
  - 이전 작업 스킵 여부 결정
"""
```

#### TC-30-2: Delayed Job 연쇄 몰림
```python
"""
시나리오:
  - Job A 지연 → Job B, C, D 대기
  - Job A 완료 후 B, C, D 동시 실행

검증:
  - 순차 실행 보장 (의존성 있는 경우)
  - 병렬 실행 최적화 (독립적인 경우)
  - 리소스 경합 방지
"""
```

#### TC-30-3: Worker Restart → Replay Storm
```python
"""
시나리오:
  - Worker 비정상 종료
  - 재시작 시 미완료 작업 감지
  - 재시작 작업 + 신규 작업 동시 처리

검증:
  - 중복 실행 방지
  - 재시작 작업 우선 처리
  - Backlog 처리 속도 조절
"""
```

#### TC-30-4: 스케줄 실행 시간 어긋남
```python
"""
시나리오:
  - 매시 정각 실행 작업
  - 서버 시계 5분 느림
  - 다음 정각 작업과 충돌

검증:
  - 중복 실행 방지
  - 실행 시간 보정
  - 알림 발생
"""
```

### 구현 설계

```python
# load_tests/scenarios/stage30_schedule_drift.py

SCHEDULE_DRIFT_CONFIG = {
    "drift_minutes": 10,
    "max_concurrent_jobs": 5,
    "job_timeout_seconds": 60,
    "backlog_throttle_tps": 10,
    "skip_stale_jobs_after_minutes": 30,
}

class ScheduleDriftStats:
    """스케줄 드리프트 통계"""

    def __init__(self):
        self.scheduled_jobs = 0
        self.executed_jobs = 0
        self.skipped_jobs = 0
        self.concurrent_peaks = []
        self.drift_events = []
        self.duplicate_prevented = 0
        self.worker_restarts = 0
        self.replay_storm_events = 0

class MockCeleryBeat:
    """Celery Beat 시뮬레이션"""

    def inject_drift(self, minutes: int):
        """드리프트 주입"""
        pass

    def trigger_worker_restart(self):
        """Worker 재시작 트리거"""
        pass

    def get_pending_jobs(self) -> List[Job]:
        """대기 중인 작업 목록"""
        pass

class ScheduleDriftUser(HttpUser):
    """스케줄 드리프트 테스트 사용자"""

    @task
    def simulate_drift(self):
        """드리프트 시뮬레이션"""
        pass

    @task
    def monitor_job_queue(self):
        """작업 큐 모니터링"""
        pass

    @task
    def trigger_worker_restart(self):
        """Worker 재시작 트리거"""
        pass
```

### Celery 설정 예시

```python
# celery_config.py (테스트용)

CELERY_BEAT_SCHEDULE = {
    'payment-reconciliation': {
        'task': 'tasks.reconcile_payments',
        'schedule': crontab(minute='0', hour='*'),  # 매시 정각
        'options': {
            'expires': 1800,  # 30분 후 만료
            'max_retries': 3,
        }
    },
    'dlq-cleanup': {
        'task': 'tasks.cleanup_dlq',
        'schedule': crontab(minute='*/5'),  # 5분마다
        'options': {
            'expires': 300,  # 5분 후 만료
        }
    },
}

# 드리프트 방지 설정
CELERY_BEAT_MAX_LOOP_INTERVAL = 5  # 최대 5초 간격 체크
CELERY_WORKER_MAX_TASKS_PER_CHILD = 1000  # 메모리 누수 방지
```

### 검증 기준

| 메트릭 | 기준값 | 설명 |
|--------|--------|------|
| 중복 실행 | 0건 | 동일 작업 2회 이상 실행 없음 |
| 최대 동시 실행 | ≤ 5 | max_concurrent 준수 |
| 지연 작업 처리 | < 5분 | Backlog 해소 시간 |
| Worker 재시작 복구 | < 30초 | 재시작 후 정상화 시간 |
| Stale 작업 스킵 | 정확 | 30분 이상 지연 작업 스킵 |

---

## 🔧 기존 Stage 확장 계획

### Stage 18 확장: Cascade Failure 추가 시나리오

```python
# 추가할 시나리오

class CascadeFailureExtended:
    """확장된 연쇄 장애 시나리오"""

    @scenario
    def redis_down_db_surge_cb_malfunction(self):
        """
        Redis Down → DB Connection Surge → CB 오작동

        Flow:
        1. Redis 연결 끊김
        2. 캐시 미스 → DB 직접 조회 폭주
        3. DB Connection Pool 고갈
        4. CB가 DB를 장애로 오인
        5. 정상 요청도 거부
        """
        pass

    @scenario
    def payment_api_down_retry_storm_rate_limit(self):
        """
        Payment API Down → Retry Storm → Rate Limit Deadlock

        Flow:
        1. 토스 API 장애
        2. 재시도 폭주
        3. Rate Limit 도달
        4. Rate Limit 해제 대기 중 추가 재시도
        5. Deadlock 상태
        """
        pass

    @scenario
    def health_check_delay_wrong_decision(self):
        """
        Health Check Delay → 잘못된 Allow/Block 판단

        Flow:
        1. Health Check 응답 지연 (5초)
        2. 서비스 DOWN으로 판단
        3. 트래픽 차단
        4. 실제로는 정상 (느린 것뿐)
        """
        pass
```

### Stage 22 확장: Retry Storm 심화

```python
# 추가할 검증 항목

class RetryStormExtended:
    """확장된 Retry Storm 검증"""

    def monitor_memory_leak_during_backoff(self):
        """백오프 증가 중 메모리 누수 모니터링"""
        # - Retry 객체 누적 확인
        # - GC 정상 동작 확인
        pass

    def verify_dlq_not_exploding(self):
        """DLQ 폭주 방지 확인"""
        # - DLQ 입력 속도 제한
        # - Queue size 경고
        pass

    def payment_webhook_replay_safety(self):
        """Payment Confirm webhook 반복 호출 안전성"""
        # - Idempotency 보장
        # - 중복 결제 방지
        pass

    def retry_timestamp_skew_compound_error(self):
        """재시도 시 timestamp skew 복합 오류"""
        # - 시간 기반 검증 실패
        # - Retry + Clock Skew 동시 발생
        pass
```

### Stage 23 확장: JWT Cascade

```python
# 추가할 시나리오

class JWTCascadeExtended:
    """JWT 만료 연쇄 장애"""

    @scenario
    def jwt_expiry_stampede(self):
        """
        JWT 만료 순간 요청 몰림

        Flow:
        1. 1000명 사용자 동시 접속 중
        2. JWT 일괄 만료 (같은 시간에 발급됨)
        3. 1000개 재발급 요청 동시 발생
        4. 인증 서버 과부하
        """
        pass

    @scenario
    def auth_server_fallback(self):
        """인증 서버 fallback"""
        # Primary → Secondary 전환
        pass

    @scenario
    def re_auth_storm(self):
        """재인증 폭주"""
        # 재인증 요청 throttling
        pass

    @scenario
    def replay_queue_growth(self):
        """Replay queue 증가"""
        # 인증 실패 요청 대기열 관리
        pass
```

---

## 📊 실행 계획

### Phase 1: 신규 Stage 구현 (1주)

| 일차 | 작업 | 담당 |
|------|------|------|
| Day 1-2 | Stage 28 구현 (Multi-Region) | - |
| Day 3-4 | Stage 29 구현 (Bulk DLQ) | - |
| Day 5-6 | Stage 30 구현 (Schedule Drift) | - |
| Day 7 | 통합 테스트 및 문서화 | - |

### Phase 2: 기존 Stage 확장 (3일)

| 일차 | 작업 |
|------|------|
| Day 1 | Stage 18 확장 (Cascade Failure) |
| Day 2 | Stage 22, 23 확장 (Retry Storm, JWT) |
| Day 3 | Stage 16 확장 (DB Deadlock Locust) |

### Phase 3: Docker Compose 및 CI/CD 통합 (2일)

| 작업 | 설명 |
|------|------|
| docker-compose.stage28.yml | Multi-Region 테스트 환경 |
| docker-compose.stage29.yml | Bulk DLQ 테스트 환경 |
| docker-compose.stage30.yml | Celery Beat 테스트 환경 |
| CI/CD 파이프라인 | GitHub Actions 통합 |

---

## 🗂️ 파일 구조

```
load_tests/
├── scenarios/
│   ├── stage28_multi_region.py          # 신규
│   ├── stage29_bulk_dlq_replay.py       # 신규
│   ├── stage30_schedule_drift.py        # 신규
│   ├── stage18_chain_failure.py         # 확장
│   ├── stage22_rate_limit_conflict.py   # 확장
│   └── stage23_clock_skew.py            # 확장
├── chaos/
│   ├── multi_region_simulator.py        # 신규
│   ├── celery_drift_injector.py         # 신규
│   └── bulk_dlq_generator.py            # 신규
├── fixtures/
│   └── dlq_100k_messages.json           # 10만건 테스트 데이터
└── docker/
    ├── docker-compose.stage28.yml
    ├── docker-compose.stage29.yml
    └── docker-compose.stage30.yml

docs/
├── STAGE_28_MULTI_REGION.md
├── STAGE_29_BULK_DLQ_REPLAY.md
└── STAGE_30_SCHEDULE_DRIFT.md
```

---

## ✅ 완료 기준

### Stage 28 (Multi-Region)
- [ ] Region A/B 비대칭 장애 시뮬레이션 구현
- [ ] Cross-region failover 자동화
- [ ] 데이터 일관성 검증 통과
- [ ] Toxiproxy 통합 완료

### Stage 29 (Bulk DLQ)
- [ ] 10만건 DLQ 생성 스크립트
- [ ] Throttling 메커니즘 구현
- [ ] CB 상태 변화 대응 로직
- [ ] Re-DLQ 로직 구현
- [ ] 메모리 사용량 모니터링

### Stage 30 (Schedule Drift)
- [ ] Celery Beat 드리프트 주입 가능
- [ ] Worker 재시작 시뮬레이션
- [ ] 동시 실행 제한 검증
- [ ] Stale 작업 스킵 로직

### 기존 Stage 확장
- [ ] Stage 18: 3개 추가 시나리오
- [ ] Stage 22: 4개 추가 검증 항목
- [ ] Stage 23: 4개 추가 시나리오
- [ ] Stage 16: Locust 통합

---

## 📚 참고 자료

- [Netflix Chaos Engineering](https://netflix.github.io/chaosmonkey/)
- [AWS Multi-Region Best Practices](https://aws.amazon.com/solutions/implementations/multi-region-application-architecture/)
- [Celery Beat Documentation](https://docs.celeryq.dev/en/stable/userguide/periodic-tasks.html)
- [Toxiproxy GitHub](https://github.com/Shopify/toxiproxy)

---

## 📝 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|-----------|
| 1.0 | 2025-12-12 | 초안 작성 |

# 🏗️ 시스템 아키텍처 문서

> **요약**: 이 문서는 myproject의 Self-Healing(자동 복구) 시스템이 어떻게 작동하는지, 장애 발생 시 어떻게 복구되는지를 설명합니다.

---

## 📋 목차

1. [시스템 개요](#1-시스템-개요)
2. [핵심 복구 메커니즘](#2-핵심-복구-메커니즘)
3. [장애 처리 흐름](#3-장애-처리-흐름)
4. [컴포넌트별 상세 설명](#4-컴포넌트별-상세-설명)
5. [운영자 가이드](#5-운영자-가이드)
6. [테스트 아키텍처](#6-테스트-아키텍처)

---

## 1. 시스템 개요

### 1.1 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              User Request                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Django REST Framework                                │
│                    (shopping 앱 - 주문/결제/포인트)                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
           │   Payment    │ │    Order     │ │    Point     │
           │   Service    │ │   Service    │ │   Service    │
           └──────────────┘ └──────────────┘ └──────────────┘
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     L3 Self-Healing Layer (selfhealing 패키지)               │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐        │
│  │   Retry     │  │  Circuit    │  │    DLQ      │  │   Replay    │        │
│  │  Handler    │  │  Breaker    │  │  Service    │  │   Service   │        │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
           ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
           │  PostgreSQL  │ │    Redis     │ │   Celery     │
           │   (DB)       │ │   (Cache)    │ │  (Workers)   │
           └──────────────┘ └──────────────┘ └──────────────┘
```

### 1.2 기술 스택

| 컴포넌트 | 기술 | 용도 |
|---------|-----|------|
| Web Framework | Django 5.2.4 + DRF 3.16.0 | REST API |
| Database | PostgreSQL 15 | 메인 데이터 저장 |
| Cache/Broker | Redis 7 | 세션, 캐시, Celery 브로커 |
| Task Queue | Celery 5.x | 비동기 작업 처리 |
| Container | Docker Compose | 개발/테스트 환경 |
| Payment | Toss Payments API | 결제 처리 |

### 1.3 주요 모델

```
shopping/models/
├── order.py           # 주문 관리
├── payment.py         # 결제 정보
├── point.py           # 포인트 시스템
├── product.py         # 상품 관리
├── user.py            # 사용자 관리
├── failed_operation.py # DLQ (실패 작업 큐)
├── failed_external_request.py # 외부 API 실패 기록
└── security_incident.py # 보안 사고 기록
```

---

## 2. 핵심 복구 메커니즘

### 2.1 Retry with Exponential Backoff (지수 백오프 재시도)

**목적**: 일시적 장애(네트워크 오류, 타임아웃) 자동 복구

**동작 방식**:
```
실패 → 4초 대기 → 재시도 → 16초 대기 → 재시도 → 64초 대기 → 재시도 → DLQ로 이동
```

**코드 위치**:
- [shopping/services/self_healing/retry_handler.py](shopping/services/self_healing/retry_handler.py)
- [packages/selfhealing-python/src/selfhealing/services/retry_handler.py](packages/selfhealing-python/src/selfhealing/services/retry_handler.py)

**설정**:
```python
SELF_HEALING = {
    "RETRY": {
        "MAX_ATTEMPTS": 3,      # 최대 3회 재시도
        "BACKOFF_BASE": 4,      # 4^n 초 대기
        "BACKOFF_MAX": 180,     # 최대 3분 대기
        "JITTER": True,         # ±25% 랜덤 분산
    }
}
```

**Jitter(지터)**: 여러 요청이 동시에 실패했을 때 같은 시간에 재시도하면 서버 과부하가 발생합니다. Jitter는 재시도 시간을 랜덤하게 분산시켜 이를 방지합니다.

---

### 2.2 Dead Letter Queue (DLQ)

**목적**: 복구 불가능한 실패를 안전하게 저장하고 추적

**DLQ로 이동하는 경우**:
1. 최대 재시도 횟수 초과
2. 재시도 불가능한 오류 (잘못된 카드번호, 잔액 부족 등)
3. SLA 타임아웃 (5분 초과)
4. Circuit Breaker가 열린 상태

**저장 정보**:
```python
FailedOperation:
    ├── domain           # payment, point, inventory, webhook
    ├── failure_type     # PG_TIMEOUT, AMOUNT_MISMATCH 등
    ├── status           # pending → reviewing → resolved/rejected
    ├── order, payment   # 원본 데이터 참조
    ├── error_code/message
    ├── snapshot_data    # 실패 당시 상태 스냅샷
    ├── retry_count      # 재시도 횟수
    └── metadata         # 디버깅용 추가 정보
```

**코드 위치**:
- [shopping/services/self_healing/dlq_service.py](shopping/services/self_healing/dlq_service.py)
- [shopping/models/failed_operation.py](shopping/models/failed_operation.py)

---

### 2.3 Circuit Breaker (회로 차단기)

**목적**: 외부 서비스(PG사) 장애 시 시스템 보호

**상태 전환**:
```
[Closed] ──(연속 5회 실패)──► [Open] ──(60초 후)──► [Half-Open]
    ▲                            │                       │
    │                            │                       │
    └──────(2회 연속 성공)────────┴───────(실패)──────────┘
```

| 상태 | 요청 허용 | 설명 |
|------|----------|------|
| Closed | ✅ 허용 | 정상 상태 |
| Open | ❌ 차단 | 장애 감지, 모든 요청 차단 |
| Half-Open | ⚠️ 일부 허용 | 복구 테스트 중 |

**Toggle 기반 설계**: 기본적으로 OFF 상태이며, 운영자가 필요시 수동으로 활성화합니다.

**코드 위치**:
- [packages/selfhealing-python/src/selfhealing/services/circuit_breaker/](packages/selfhealing-python/src/selfhealing/services/circuit_breaker/)

---

### 2.4 Replay Service (재처리 서비스)

**목적**: DLQ에 저장된 실패 항목을 재처리

**재처리 유형**:
1. **수동 재처리**: 운영자가 개별 항목 선택
2. **배치 재처리**: 필터 조건에 맞는 여러 항목 일괄 처리
3. **조건부 재처리**: Circuit Breaker가 닫힐 때 자동 재처리

**코드 위치**:
- [packages/selfhealing-python/src/selfhealing/services/replay_service.py](packages/selfhealing-python/src/selfhealing/services/replay_service.py)
- [shopping/tasks/dlq_replay_tasks.py](shopping/tasks/dlq_replay_tasks.py)

---

## 3. 장애 처리 흐름

### 3.1 결제 실패 시나리오

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           결제 요청 (Payment Request)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
                    ┌───────────────────────────────┐
                    │   Circuit Breaker 상태 확인   │
                    └───────────────────────────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
            [Open/Half-Open]                   [Closed]
                    │                               │
                    ▼                               ▼
            ┌─────────────┐              ┌─────────────────────┐
            │ 즉시 DLQ로  │              │   Toss API 호출     │
            │ 이동        │              │   (/confirm 등)     │
            └─────────────┘              └─────────────────────┘
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                               [성공]                           [실패]
                                    │                               │
                                    ▼                               ▼
                            ┌─────────────┐         ┌─────────────────────────┐
                            │  완료! ✅   │         │   RetryHandler 처리     │
                            └─────────────┘         └─────────────────────────┘
                                                                │
                                            ┌───────────────────┴───────────────────┐
                                            ▼                                       ▼
                                    [재시도 가능 오류]                      [재시도 불가 오류]
                                    (TIMEOUT, 5xx)                     (INVALID_CARD, 잔액부족)
                                            │                                       │
                                            ▼                                       ▼
                                ┌─────────────────────┐                   ┌─────────────┐
                                │ Backoff 후 재시도   │                   │ 즉시 DLQ로  │
                                │ (최대 3회)          │                   │ 이동        │
                                └─────────────────────┘                   └─────────────┘
                                            │
                        ┌───────────────────┴───────────────────┐
                        ▼                                       ▼
                   [재시도 성공]                          [최대 횟수 초과]
                        │                                       │
                        ▼                                       ▼
                ┌─────────────┐                         ┌─────────────┐
                │  완료! ✅   │                         │   DLQ로     │
                └─────────────┘                         │   이동      │
                                                        └─────────────┘
```

### 3.2 DLQ 항목 생명주기

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   PENDING   │ ──► │  REVIEWING  │ ──► │  REPLAYED   │ ──► │  RESOLVED   │
│  (대기중)   │     │  (검토중)   │     │ (재처리중)  │     │  (해결됨)   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
       │                   │                                        │
       │                   ▼                                        │
       │            ┌─────────────┐                                 │
       └──────────► │  REJECTED   │ ◄───────────────────────────────┘
                    │ (거부됨-   │
                    │ 복구불가)   │
                    └─────────────┘
                           │
                           ▼
                    ┌─────────────┐
                    │  ARCHIVED   │ (30일 후 자동)
                    └─────────────┘
```

---

## 4. 컴포넌트별 상세 설명

### 4.1 RetryHandler (재시도 처리기)

```python
from shopping.services.self_healing.retry_handler import RetryHandler

handler = RetryHandler(domain="payment")
result = handler.execute(
    my_payment_function,
    arg1, arg2,
    context={"order_id": 123, "user_id": 456}
)

if result.success:
    print(f"성공! 시도 횟수: {result.attempt}")
else:
    print(f"실패. DLQ ID: {result.dlq_id}")
```

**주요 기능**:
- `should_retry(exception, attempt)`: 재시도 가능 여부 판단
- `get_next_delay(attempt)`: 다음 재시도까지 대기 시간 계산
- `execute(func, *args, **kwargs)`: 함수 실행 + 자동 재시도

---

### 4.2 DLQService (DLQ 서비스)

```python
from shopping.services.self_healing.dlq_service import DLQService

service = DLQService()

# 실패 저장
result = service.store_failure(
    domain="payment",
    failure_type="PG_TIMEOUT",
    order=order,
    error_message="Connection timed out",
)

# 재처리 가능 항목 조회
entries = service.get_replayable_entries(domain="payment", limit=50)
```

---

### 4.3 CircuitBreakerService (회로 차단기 서비스)

```python
from selfhealing.services.circuit_breaker import (
    CircuitBreakerService,
    force_open_circuit,
    force_close_circuit,
)

service = CircuitBreakerService()

# 요청 허용 여부 확인
if service.should_allow("toss_payment"):
    # 결제 API 호출
    pass

# 수동 Open (장애 감지 시)
force_open_circuit("toss_payment", reason="PG 장애", controlled_by_id=admin_id)

# 수동 Close (복구 확인 후)
force_close_circuit("toss_payment", reason="복구 확인", trigger_replay=True)
```

---

### 4.4 ReplayService (재처리 서비스)

```python
from selfhealing.services import get_replay_service

service = get_replay_service()

# 단건 재처리
result = service.replay_single(dlq_id=123)

# 배치 재처리
batch_result = service.batch_replay(
    domain="payment",
    failure_type="PG_TIMEOUT",
    limit=50
)

# Circuit Breaker 닫힘 시 자동 재처리
result = service.replay_on_circuit_close(
    service_name="toss_payment",
    max_items=50
)
```

---

## 5. 운영자 가이드

### 5.1 Django Admin에서 DLQ 관리

1. `/admin/shopping/failedoperation/` 접속
2. `status=pending` 필터로 미처리 항목 확인
3. 개별 항목 선택 후 "Retry" 액션 실행

### 5.2 Circuit Breaker 수동 제어

```bash
# 터미널에서 Celery Task 실행
python manage.py shell

>>> from shopping.tasks import reset_circuit_breaker
>>> reset_circuit_breaker.delay(
...     service_name="toss_payment",
...     action="open",
...     reason="긴급 점검",
...     controlled_by_id=1
... )
```

### 5.3 DLQ 배치 재처리

```bash
# dlq_replay_tasks Celery Task 실행
python manage.py shell

>>> from shopping.tasks.dlq_replay_tasks import batch_replay_task
>>> batch_replay_task.delay(domain="payment", max_items=100)
```

### 5.4 모니터링 체크리스트

- [ ] DLQ pending 항목 수 확인 (급증 시 알림)
- [ ] Circuit Breaker 상태 확인
- [ ] Celery Worker 상태 확인
- [ ] Retry 성공률 모니터링
- [ ] 평균 복구 시간(MTTR) 추적

---

## 6. 테스트 아키텍처

### 6.1 테스트 구조

```
load_tests/scenarios/
├── chaos/          # 카오스 테스트 (19개)
│   ├── stage6_*    # DB Lock Recovery
│   ├── stage16_*   # Deadlock Recovery
│   ├── stage18_*   # Chain Failure
│   └── stage24-43  # 다양한 장애 시나리오
│
├── hybrid/         # 복합 테스트 (10개)
│   ├── stage4_*    # Stress + Recovery
│   ├── stage12_*   # Spike & Recovery
│   └── stage21-41  # JWT Cascade, Retry Storm 등
│
├── integration/    # 통합 테스트 (39개)
│   ├── stage2_*    # Payment Flow
│   ├── stage14_*   # DLQ Replay
│   ├── stage15_*   # Circuit Breaker
│   └── stage35-45  # Webhook, Idempotency 등
│
└── load/           # 부하 테스트 (5개)
    ├── stage0_*    # Smoke Test
    ├── stage1_*    # Happy Path Load
    └── stage9-11   # Latency, Soak, Ramp 테스트
```

### 6.2 테스트 실행

```bash
# Docker 환경에서 전체 테스트
docker-compose exec web pytest -v

# 특정 마커 테스트
docker-compose exec web pytest -m "self_healing" -v

# 부하 테스트 (Locust)
locust -f load_tests/scenarios/load/stage0_smoke_locust.py
```

---

## 📚 관련 문서

- [README.md](README.md) - 빠른 시작 가이드
- [tests/README.md](tests/README.md) - 테스트 실행 가이드
- [packages/selfhealing-python/docs/active/L3_SELF_HEALING_SYSTEM.md](packages/selfhealing-python/docs/active/L3_SELF_HEALING_SYSTEM.md) - Self-Healing 상세 문서
- [load_tests/docs/SELF_HEALING_LOAD_TEST_PLAN.md](load_tests/docs/SELF_HEALING_LOAD_TEST_PLAN.md) - 부하 테스트 계획

---

*최종 업데이트: 2025-12-19*

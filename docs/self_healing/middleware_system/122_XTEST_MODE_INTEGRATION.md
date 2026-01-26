# X-Test-Mode 통합 테스트 시나리오 설계

**문서 번호:** 122  
**작성일:** 2026-01-26  
**상태:** 설계 완료  
**선행 문서:** 116-121 전체

---

## 1. 목적

개별 Self-Healing 컴포넌트(CB, EB, DLQ, Replay, Retry, Rate Limiter, Idempotency)가 상호 연동되는 시나리오를 X-Test-Mode 환경에서 검증할 수 있는 통합 테스트 API 설계.

### 1.1 통합 테스트 목표

| 목표 | 설명 |
|------|------|
| 엔드투엔드 플로우 | CB → DLQ → Replay 전체 사이클 |
| 컴포넌트 상호작용 | Retry + Rate Limiter 연동 |
| 실패 전파 | 한 컴포넌트 실패가 다른 컴포넌트에 미치는 영향 |
| 복구 시나리오 | Self-Healing 자동 복구 검증 |

### 1.2 아키텍처 참조 (코드 기준)

`docs/self_healing/02_ARCHITECTURE.md`에서:

```
요청 → Rate Limiter → Idempotency → CB → 서비스 호출
       ↓                                    ↓
   Rate Limit 초과                      실패
       ↓                                    ↓
   Retry(백오프)                        Retry
       ↓                                    ↓
   최대 재시도 초과                    최대 재시도 초과
       ↓                                    ↓
   DLQ 저장 ←←←←←←←←←←←←←←←←←←←←← DLQ 저장
       ↓
   Replay
       ↓
   EB 소진 체크
```

---

## 2. 통합 시나리오 API

### 2.1 시나리오 실행

**엔드포인트:** `POST /api/self-healing/xtest/integration/run-scenario/`

**목적:** 사전 정의된 통합 시나리오 실행

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| scenario | string | O | 시나리오 식별자 |
| service_name | string | O | 테스트 대상 서비스 |
| config | object | X | 시나리오별 설정 |

**사전 정의 시나리오:**

| scenario | 설명 |
|----------|------|
| `cb_open_dlq_flow` | CB Open → DLQ 저장 플로우 |
| `retry_exhaust_dlq` | Retry 소진 → DLQ 플로우 |
| `rate_limit_retry` | Rate Limit → Retry 백오프 |
| `dlq_replay_success` | DLQ → Replay 성공 |
| `dlq_replay_failure` | DLQ → Replay 실패 → 재DLQ |
| `full_recovery_cycle` | 전체 장애 → 복구 사이클 |
| `idempotent_replay` | Replay 멱등성 보장 |

**응답:**

| 필드 | 설명 |
|------|------|
| scenario_id | 실행 ID |
| status | 실행 상태 |
| steps | 단계별 결과 |
| timeline | 이벤트 타임라인 |
| snapshot | 실행 후 시스템 스냅샷 |

### 2.2 시나리오 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/integration/scenario/{scenario_id}/`

**목적:** 진행 중 또는 완료된 시나리오 조회

**응답:**

| 필드 | 설명 |
|------|------|
| scenario_id | 실행 ID |
| scenario | 시나리오 이름 |
| started_at | 시작 시간 |
| completed_at | 완료 시간 |
| status | 진행 상태 |
| steps | 단계별 상세 |
| errors | 발생 오류 |

### 2.3 전체 시스템 스냅샷

**엔드포인트:** `GET /api/self-healing/xtest/integration/full-snapshot/`

**목적:** 모든 Self-Healing 컴포넌트 상태 통합 조회

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| service_name | string | 서비스 필터 |
| include_history | bool | 히스토리 포함 여부 |

**응답:**

| 필드 | 설명 |
|------|------|
| circuit_breakers | CB 상태 (119 참조) |
| error_budget | EB 상태 (116 참조) |
| dlq | DLQ 통계 (117 참조) |
| retry | Retry 통계 (119 참조) |
| rate_limiter | Rate Limiter 상태 (120 참조) |
| idempotency | Idempotency 상태 (121 참조) |
| timestamp | 스냅샷 시간 |

### 2.4 시스템 초기화 (테스트용)

**엔드포인트:** `POST /api/self-healing/xtest/integration/reset/`

**목적:** 테스트 전 시스템 상태 초기화

**요청 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| components | array | 초기화할 컴포넌트 목록 |
| service_name | string | 서비스 범위 |
| xtest_only | bool | X-Test 생성 데이터만 |

**components 값:**

| 값 | 설명 |
|----|------|
| `circuit_breakers` | CB 상태 초기화 |
| `error_budget` | EB 초기화 |
| `dlq` | DLQ 테스트 항목 삭제 |
| `rate_limiter` | Rate Limit 카운터 초기화 |
| `idempotency` | Idempotency 키 삭제 |
| `all` | 전체 초기화 |

---

## 3. 사전 정의 시나리오 상세

### 3.1 CB Open → DLQ 플로우 (`cb_open_dlq_flow`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | CB Closed 확인 | state: CLOSED |
| 2 | 실패 주입 (failure_threshold 초과) | - |
| 3 | CB Open 확인 | state: OPEN |
| 4 | 요청 전송 | CircuitOpenException |
| 5 | DLQ 저장 확인 | DLQ 항목 생성 |
| 6 | DLQ 항목 상세 확인 | error_type: circuit_open |

### 3.2 Retry 소진 → DLQ (`retry_exhaust_dlq`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | Retry 설정 조회 | max_retries 확인 |
| 2 | 실패 응답 주입 | 첫 번째 재시도 |
| 3 | (max_retries 반복) | 재시도 증가 |
| 4 | 최대 재시도 초과 | RetryExhausted |
| 5 | DLQ 저장 확인 | retry_count: max |
| 6 | Retry 통계 확인 | failed_count 증가 |

### 3.3 Rate Limit → Retry (`rate_limit_retry`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | Rate Limit 임계치 설정 | limit: 5/분 |
| 2 | 5회 요청 전송 | 모두 성공 |
| 3 | 6번째 요청 | 429 응답 |
| 4 | Retry 백오프 확인 | exponential 대기 |
| 5 | 백오프 후 재시도 | 성공 |
| 6 | Rate Limiter 통계 확인 | throttled_count: 1 |

### 3.4 DLQ → Replay 성공 (`dlq_replay_success`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | DLQ 테스트 항목 생성 | entry_id 반환 |
| 2 | CB 정상 확인 | state: CLOSED |
| 3 | Replay 실행 | replay_id 반환 |
| 4 | Replay 상태 조회 | status: COMPLETED |
| 5 | DLQ 항목 상태 확인 | status: REPLAYED |
| 6 | 타겟 시스템 확인 | 처리 완료 |

### 3.5 DLQ → Replay 실패 (`dlq_replay_failure`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | DLQ 테스트 항목 생성 | entry_id 반환 |
| 2 | 타겟 실패 주입 | - |
| 3 | Replay 실행 | replay_id 반환 |
| 4 | Replay 상태 조회 | status: FAILED |
| 5 | DLQ 항목 확인 | replay_count 증가 |
| 6 | 재시도 대기 확인 | next_retry_at 설정 |

### 3.6 전체 복구 사이클 (`full_recovery_cycle`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | 초기 상태 스냅샷 | 모든 정상 |
| 2 | 대량 실패 주입 | - |
| 3 | CB Open 확인 | state: OPEN |
| 4 | EB 소진 확인 | remaining < 0 |
| 5 | DLQ 누적 확인 | pending_count 증가 |
| 6 | 서비스 복구 시뮬 | - |
| 7 | CB Half-Open | state: HALF_OPEN |
| 8 | 성공 요청 | CB Closed |
| 9 | DLQ Replay 배치 | batch_replay |
| 10 | EB 회복 확인 | remaining 증가 |
| 11 | 최종 스냅샷 | 모든 정상 |

### 3.7 Replay 멱등성 (`idempotent_replay`)

**단계:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | DLQ 항목 생성 | idempotency_key 포함 |
| 2 | Replay 실행 | 첫 번째 처리 |
| 3 | Idempotency 키 확인 | 등록됨 |
| 4 | 동일 항목 재Replay | 중복 감지 |
| 5 | 결과 확인 | 이전 결과 반환 |
| 6 | 실제 처리 횟수 | 1회만 처리 |

---

## 4. 구현 순서

### Step 1: 시나리오 정의 파일

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `IntegrationScenario` | 기본 시나리오 클래스 |
| 1-2 | `CBOpenDLQScenario` | CB → DLQ 시나리오 |
| 1-3 | `RetryExhaustScenario` | Retry 소진 시나리오 |
| 1-4 | `RateLimitRetryScenario` | Rate Limit 시나리오 |
| 1-5 | `DLQReplayScenario` | DLQ → Replay 시나리오 |
| 1-6 | `FullRecoveryScenario` | 전체 복구 시나리오 |
| 1-7 | `IdempotentReplayScenario` | 멱등성 시나리오 |

### Step 2: View 파일 생성

**파일:** `api/django/views/xtest/integration.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 2-1 | `RunScenarioView` | 시나리오 실행 |
| 2-2 | `ScenarioStatusView` | 상태 조회 |
| 2-3 | `FullSnapshotView` | 통합 스냅샷 |
| 2-4 | `ResetView` | 시스템 초기화 |

### Step 3: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 3-1 | `xtest/integration/run-scenario/` | `RunScenarioView` |
| 3-2 | `xtest/integration/scenario/<id>/` | `ScenarioStatusView` |
| 3-3 | `xtest/integration/full-snapshot/` | `FullSnapshotView` |
| 3-4 | `xtest/integration/reset/` | `ResetView` |

### Step 4: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가

### Step 5: 테스트 작성

**파일:** `tests/unit/api/xtest/test_integration_views.py`

| 테스트 케이스 |
|--------------|
| 각 시나리오별 실행 테스트 |
| full-snapshot 테스트 |
| reset 테스트 |

---

## 5. 타임라인 형식

시나리오 실행 결과 timeline 형식:

| 필드 | 설명 |
|------|------|
| timestamp | ISO 8601 시간 |
| step | 단계 번호 |
| action | 수행 동작 |
| component | 관련 컴포넌트 |
| result | 동작 결과 |
| duration_ms | 소요 시간 |

예시:
```json
{
  "timeline": [
    {"timestamp": "...", "step": 1, "action": "check_cb_state", "component": "circuit_breaker", "result": "CLOSED", "duration_ms": 5},
    {"timestamp": "...", "step": 2, "action": "inject_failures", "component": "circuit_breaker", "result": "5 failures injected", "duration_ms": 100}
  ]
}
```

---

## 6. 보안 고려사항

### 6.1 기본 보안 (XTestModeMixin)

- `X-Test-Mode: chaos-monkey` 헤더 필수
- 프로덕션 환경 완전 차단

### 6.2 추가 보안

| 항목 | 조치 |
|------|------|
| reset 제한 | X-Test 생성 데이터만 삭제 |
| 시나리오 격리 | 테스트 서비스만 대상 |
| 타임아웃 | 시나리오별 최대 실행 시간 |

---

## 7. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `api/django/views/xtest/base.py` | XTestModeMixin |
| `api/django/views/xtest/circuit_breaker.py` | CB View 패턴 |
| `services/circuit_breaker_service.py` | CB Service |
| `services/dlq/__init__.py` | DLQ Service |
| `services/replay_service.py` | Replay Service |
| `services/retry_handler.py` | Retry Handler |
| `api/django/rate_limit.py` | Rate Limiter |
| `services/idempotency_service.py` | Idempotency Service |

---

## 8. 문서 시리즈 완료

| 문서 | 내용 |
|------|------|
| 116 | X-Test-Mode 개요 |
| 117 | DLQ 테스트 API |
| 118 | Replay 테스트 API |
| 119 | Retry 테스트 API |
| 120 | Rate Limiter 테스트 API |
| 121 | Idempotency 테스트 API |
| **122** | **통합 테스트 시나리오** |

---

**시리즈 완료**

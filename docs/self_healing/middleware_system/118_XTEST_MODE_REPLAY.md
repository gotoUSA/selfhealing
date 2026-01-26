# X-Test-Mode Replay 테스트 API 설계

**문서 번호:** 118  
**작성일:** 2026-01-26  
**상태:** 설계 완료  
**선행 문서:** 117_XTEST_MODE_DLQ.md

---

## 1. 목적

Replay 시스템 동작을 X-Test-Mode 환경에서 직접 관찰하고 테스트할 수 있는 API 설계.

### 1.1 테스트 목표

| 목표 | 설명 |
|------|------|
| 수동 재생 | 단일 DLQ 항목 재생 및 결과 확인 |
| 배치 재생 | 다수 항목 일괄 재생 |
| 조건부 재생 | CB 복구 시 자동 재생 트리거 확인 |
| 거버넌스 검증 | 재생 전 거버넌스 체크 동작 확인 |

### 1.2 Replay 유형 (코드 기준)

`services/replay_service.py`에서 제공하는 기능:

| 유형 | 메서드 | 설명 |
|------|--------|------|
| 단일 재생 | `replay_single()` | 개별 항목 재생 |
| 배치 재생 | `replay_batch()` | 다수 항목 재생 |
| 조건부 재생 | `replay_on_circuit_close()` | CB 복구 시 자동 |

---

## 2. API 엔드포인트 설계

### 2.1 단일 항목 재생

**엔드포인트:** `POST /api/self-healing/xtest/replay/single/`

**목적:** 단일 DLQ 항목을 재생하고 결과 관찰

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| dlq_id | int | O | 재생할 DLQ 항목 ID |
| dry_run | bool | X | 실제 실행 없이 검증만 (기본 false) |
| skip_governance | bool | X | 거버넌스 체크 스킵 (기본 false) |

**응답:**

| 필드 | 설명 |
|------|------|
| success | 재생 성공 여부 |
| dlq_id | 대상 DLQ ID |
| message | 결과 메시지 |
| governance_result | 거버넌스 체크 결과 |
| replay_duration_ms | 재생 소요 시간 |
| snapshot | 시스템 스냅샷 |

### 2.2 배치 재생

**엔드포인트:** `POST /api/self-healing/xtest/replay/batch/`

**목적:** 필터 조건에 맞는 다수 항목 재생

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| domain | string | X | 도메인 필터 |
| status | string | X | 상태 필터 (기본 pending) |
| batch_size | int | X | 배치 크기 (기본 10, 최대 50) |
| dry_run | bool | X | 검증만 (기본 false) |

**응답:**

| 필드 | 설명 |
|------|------|
| total | 전체 처리 대상 수 |
| success_count | 성공 수 |
| failed_count | 실패 수 |
| skipped_count | 스킵 수 |
| governance_blocked | 거버넌스 차단 여부 |
| results | 개별 결과 목록 (요약) |

### 2.3 조건부 재생 트리거

**엔드포인트:** `POST /api/self-healing/xtest/replay/trigger-on-cb-close/`

**목적:** CB 복구 시 자동 재생 동작 시뮬레이션

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| service_name | string | O | CB 서비스 이름 |
| simulate_close | bool | X | CB CLOSE 시뮬레이션 (기본 true) |

**응답:**

| 필드 | 설명 |
|------|------|
| triggered | 재생 트리거 여부 |
| eligible_count | 재생 대상 항목 수 |
| replayed_count | 실제 재생된 수 |
| cb_previous_state | CB 이전 상태 |
| cb_current_state | CB 현재 상태 |

### 2.4 재생 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/replay/status/`

**목적:** 현재 재생 가능한 항목 및 상태 확인

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 도메인 필터 |

**응답:**

| 필드 | 설명 |
|------|------|
| pending_count | 대기 중 항목 수 |
| by_domain | 도메인별 대기 수 |
| governance_status | 현재 거버넌스 상태 |
| cb_states | 관련 CB 상태 목록 |

---

## 3. 서비스 레이어 연동

### 3.1 기존 Replay 서비스 활용

| 서비스 메서드 | 위치 | 용도 |
|-------------|------|------|
| `ReplayService.replay_single()` | `services/replay_service.py` L200+ | 단일 재생 |
| `ReplayService.replay_batch()` | `services/replay_service.py` L300+ | 배치 재생 |
| `check_all_governance()` | `services/governance_checks.py` | 거버넌스 체크 |

### 3.2 ReplayResult / BatchReplayResult

`services/replay_service.py`에 정의된 결과 클래스:

| 클래스 | 용도 |
|--------|------|
| `ReplayResult` | 단일 재생 결과 |
| `BatchReplayResult` | 배치 재생 결과 |

---

## 4. 구현 순서

### Step 1: View 파일 생성

**파일:** `api/django/views/xtest/replay.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `ReplaySingleView` | 단일 재생 |
| 1-2 | `ReplayBatchView` | 배치 재생 |
| 1-3 | `TriggerReplayOnCBCloseView` | 조건부 재생 |
| 1-4 | `ReplayStatusView` | 상태 조회 |

### Step 2: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 2-1 | `xtest/replay/single/` | `ReplaySingleView` |
| 2-2 | `xtest/replay/batch/` | `ReplayBatchView` |
| 2-3 | `xtest/replay/trigger-on-cb-close/` | `TriggerReplayOnCBCloseView` |
| 2-4 | `xtest/replay/status/` | `ReplayStatusView` |

### Step 3: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가
- 엔드포인트 문서 업데이트

### Step 4: 테스트 작성

**파일:** `tests/unit/api/xtest/test_replay_views.py`

| 테스트 케이스 |
|--------------|
| single 성공 테스트 |
| single dry_run 테스트 |
| batch 재생 테스트 |
| trigger-on-cb-close 테스트 |
| 거버넌스 차단 테스트 |

---

## 5. 테스트 시나리오

### 5.1 DLQ → Replay 사이클

```
1. POST /xtest/dlq/inject/ (domain=external_service, count=3)
2. GET /xtest/replay/status/ (domain=external_service)
   → pending_count: 3
3. POST /xtest/replay/single/ (dlq_id=X)
   → success: true
4. POST /xtest/replay/batch/ (domain=external_service)
   → success_count: 2
```

### 5.2 CB 연동 시나리오

```
1. POST /xtest/inject-cb-failure/ (service=database) → CB OPEN
2. POST /xtest/dlq/inject/ (domain=external_service, count=5)
3. POST /xtest/trigger-cb-recovery/ → CB CLOSED
4. POST /xtest/replay/trigger-on-cb-close/ (service_name=database)
   → triggered: true, replayed_count: 5
```

### 5.3 거버넌스 체크 시나리오

```
1. 시스템 Kill Switch 활성화 상태 시뮬레이션
2. POST /xtest/replay/single/ (dlq_id=X)
   → success: false, governance_result.blocked: true
```

---

## 6. 거버넌스 체크 통합

### 6.1 체크 항목 (governance_checks.py 기준)

| 체크 | 설명 |
|------|------|
| Kill Switch | 시스템 비활성화 여부 |
| CB 상태 | 관련 CB가 OPEN 상태인지 |
| Error Budget | 에러 버짓 소진 여부 |
| Rate Limit | 재생 속도 제한 |

### 6.2 응답에 포함

```json
{
    "governance_result": {
        "allowed": true|false,
        "checks_passed": ["kill_switch", "cb_state"],
        "checks_failed": [],
        "block_reason": null
    }
}
```

---

## 7. dry_run 모드

실제 재생 없이 검증만 수행:

| 항목 | 동작 |
|------|------|
| 거버넌스 체크 | 수행 |
| DLQ 상태 변경 | 수행 안 함 |
| 실제 작업 실행 | 수행 안 함 |
| 결과 예측 | 반환 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/replay_service.py` | ReplayService, ReplayResult, BatchReplayResult |
| `services/governance_checks.py` | check_all_governance, GovernanceCheckResult |
| `services/dlq/replay_operations.py` | DLQ 재생 관련 mixin |
| `api/django/views/dlq.py` | DLQReplayView 패턴 참조 |
| `api/django/views/xtest/base.py` | XTestModeMixin 패턴 |

---

**다음 문서:** 119_XTEST_MODE_RETRY.md

# X-Test-Mode DLQ 테스트 API 설계

**문서 번호:** 117
**작성일:** 2026-01-26
**상태:** ✅ 구현 완료
**선행 문서:** 116_XTEST_MODE_OVERVIEW.md

---

## 구현 정보

| 항목 | 내용 |
|------|------|
| 구현 일자 | 2026-01-26 |
| View 파일 | `selfhealing/api/django/views/xtest/dlq.py` |
| 테스트 파일 | `tests/self_healing/api/test_xtest_dlq_views.py` |
| 테스트 결과 | 29 passed |
| Import 패턴 | 직접 import (`from selfhealing.api.django.views.xtest import ...`) |

### Import 가이드

```python
# ✅ 권장 (직접 import)
from selfhealing.api.django.views.xtest import (
    InjectDLQEntryView,
    DLQXTestStatusView,
    ForceStatusView,
    ResetDLQXTestView,
)

# ✅ 패키지 레벨 import
from selfhealing.api.django.views import (
    InjectDLQEntryView,
    DLQXTestStatusView,
    ForceStatusView,
    ResetDLQXTestView,
)

# ⚠️ 비권장 - 레거시 re-export (DEPRECATED)
from selfhealing.api.django.views.xtest_mode import ...
```

---

## 1. 목적

DLQ(Dead Letter Queue) 동작을 X-Test-Mode 환경에서 직접 관찰하고 테스트할 수 있는 API 설계.

### 1.1 테스트 목표

| 목표 | 설명 |
|------|------|
| DLQ 저장 | 장애 발생 시 DLQ에 정상 저장 확인 |
| 상태 조회 | pending/reviewing/resolved 상태 추적 |
| 통계 확인 | 도메인별, 상태별 집계 |
| CB 연동 | CB OPEN 시 DLQ 저장 자동화 확인 |

### 1.2 기존 DLQ API와의 차이

| 기존 API | X-Test-Mode API |
|----------|----------------|
| 운영용 (`/dlq/*`) | 테스트용 (`/xtest/dlq/*`) |
| 권한 필요 (Admin/Operator) | 헤더 기반 (`X-Test-Mode`) |
| 실제 데이터 조작 | 테스트 데이터 + 관찰 |

---

## 2. API 엔드포인트 설계

### 2.1 DLQ 테스트 항목 생성

**엔드포인트:** `POST /api/self-healing/xtest/dlq/inject/`

**목적:** 테스트용 DLQ 항목 생성 (기존 `DLQTestCreateView` 통합)

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| domain | string | O | 도메인 (external_service, internal_process 등) |
| failure_type | string | O | 실패 유형 |
| entity_type | string | X | 엔티티 타입 |
| entity_id | string | X | 엔티티 ID |
| error_message | string | X | 에러 메시지 |
| count | int | X | 생성 개수 (기본 1, 최대 20) |

**응답:**

| 필드 | 설명 |
|------|------|
| created_count | 생성된 항목 수 |
| dlq_ids | 생성된 DLQ ID 목록 |
| domain | 도메인 |
| snapshot | 시스템 스냅샷 |

### 2.2 DLQ 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/dlq/status/`

**목적:** 테스트 환경의 DLQ 현황 조회

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 필터링할 도메인 |
| status | string | 필터링할 상태 |
| limit | int | 최대 조회 수 (기본 50) |

**응답:**

| 필드 | 설명 |
|------|------|
| total_count | 전체 항목 수 |
| by_status | 상태별 집계 |
| by_domain | 도메인별 집계 |
| recent_entries | 최근 항목 목록 (ID, status, created_at) |

### 2.3 DLQ 상태 강제 변경

**엔드포인트:** `POST /api/self-healing/xtest/dlq/force-status/`

**목적:** 테스트용 상태 강제 전환

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| dlq_id | int | O | 대상 DLQ ID |
| new_status | string | O | 변경할 상태 (pending, reviewing, resolved, rejected) |
| reason | string | X | 변경 사유 |

### 2.4 DLQ 초기화

**엔드포인트:** `POST /api/self-healing/xtest/dlq/reset/`

**목적:** 테스트 데이터 정리 (X-Test-Mode로 생성된 항목만)

**요청 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 특정 도메인만 초기화 (선택) |
| created_by_xtest | bool | X-Test-Mode 생성 항목만 (기본 true) |

---

## 3. 서비스 레이어 연동

### 3.1 기존 DLQ 서비스 활용

| 서비스 메서드 | 위치 | 용도 |
|-------------|------|------|
| `store_to_dlq()` | `services/dlq/__init__.py` | DLQ 저장 |
| `DLQService.get_stats()` | `services/dlq/query_operations.py` | 통계 조회 |
| `DLQService.create_test_entry()` | `services/dlq/entry_operations.py` | 테스트 항목 생성 |

### 3.2 신규 메서드 필요

| 메서드 | 위치 | 용도 |
|--------|------|------|
| `get_xtest_entries()` | 신규 또는 query_operations 확장 | X-Test 생성 항목 조회 |
| `reset_xtest_entries()` | 신규 또는 entry_operations 확장 | X-Test 항목 초기화 |

---

## 4. 구현 순서

### Step 1: View 파일 생성

**파일:** `api/django/views/xtest/dlq.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `InjectDLQEntryView` | DLQ 항목 주입 |
| 1-2 | `DLQXTestStatusView` | 상태 조회 |
| 1-3 | `ForceStatusView` | 상태 강제 변경 |
| 1-4 | `ResetDLQXTestView` | 초기화 |

### Step 2: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 2-1 | `xtest/dlq/inject/` | `InjectDLQEntryView` |
| 2-2 | `xtest/dlq/status/` | `DLQXTestStatusView` |
| 2-3 | `xtest/dlq/force-status/` | `ForceStatusView` |
| 2-4 | `xtest/dlq/reset/` | `ResetDLQXTestView` |

### Step 3: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가
- 엔드포인트 문서 업데이트

### Step 4: 서비스 확장 (선택)

**파일:** `services/dlq/query_operations.py` 또는 `entry_operations.py`

- X-Test 전용 필터 메서드 추가 (필요시)

### Step 5: 테스트 작성

**파일:** `tests/unit/api/xtest/test_dlq_views.py`

| 테스트 케이스 |
|--------------|
| inject 성공 테스트 |
| inject 권한 없음 테스트 |
| status 조회 테스트 |
| force-status 변경 테스트 |
| reset 테스트 |

---

## 5. 테스트 시나리오

### 5.1 DLQ 저장 → 조회 사이클

```
1. POST /xtest/dlq/inject/ (domain=external_service, count=5)
2. GET /xtest/dlq/status/ (domain=external_service)
3. 검증: total_count >= 5, by_status.pending >= 5
```

### 5.2 CB 연동 시나리오

```
1. POST /xtest/inject-cb-failure/ (service=database, count=5) → CB OPEN
2. POST /xtest/dlq/inject/ (domain=external_service, failure_type=CB_OPEN)
3. GET /xtest/dlq/status/
4. POST /xtest/trigger-cb-recovery/ → CB CLOSED
5. 검증: DLQ 항목 존재, CB 상태 변경 확인
```

---

## 6. 보안 고려사항

### 6.1 기본 보안 (XTestModeMixin)

- `X-Test-Mode: chaos-monkey` 헤더 필수
- 프로덕션 환경 완전 차단

### 6.2 추가 보안

| 항목 | 조치 |
|------|------|
| 항목 수 제한 | count 최대 20개 |
| reset 범위 | X-Test-Mode 생성 항목만 |
| 감사 로깅 | 모든 조작 기록 |

---

## 7. 메타데이터 표시

X-Test-Mode로 생성된 DLQ 항목 식별:

| 필드 | 값 |
|------|-----|
| metadata.source | `x-test-mode` |
| metadata.created_by | 사용자 정보 |
| metadata.xtest_session | 세션 식별자 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/dlq/__init__.py` | DLQService 클래스 구조 |
| `services/dlq/store_operations.py` | store_failure 메서드 |
| `services/dlq/query_operations.py` | get_stats, query 메서드 |
| `services/dlq/entry_operations.py` | create_test_entry 메서드 |
| `api/django/views/dlq.py` | DLQTestCreateView 패턴 참조 |
| `api/django/views/xtest/base.py` | XTestModeMixin 패턴 |

---

**다음 문서:** 118_XTEST_MODE_REPLAY.md

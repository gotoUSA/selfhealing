# X-Test-Mode Idempotency 테스트 API 설계

**문서 번호:** 121  
**작성일:** 2026-01-26  
**상태:** ✅ 구현 완료  
**구현일:** 2026-01-27  
**선행 문서:** 120_XTEST_MODE_RATE_LIMITER.md

---

## 구현 완료 내역

| 항목 | 상태 | 파일 |
|------|------|------|
| View 파일 | ✅ 완료 | `selfhealing/api/django/views/xtest/idempotency.py` |
| URL 라우팅 | ✅ 완료 | `selfhealing/api/django/urls.py` |
| __init__.py | ✅ 완료 | `selfhealing/api/django/views/xtest/__init__.py` |
| 단위 테스트 | ✅ 완료 (32 passed) | `tests/self_healing/api/test_xtest_idempotency_views.py` |

### 구현된 View 클래스

| 클래스 | 엔드포인트 |
|--------|------------|
| `GenerateKeyView` | `POST /api/self-healing/xtest/idempotency/generate-key/` |
| `CheckDuplicateView` | `POST /api/self-healing/xtest/idempotency/check-duplicate/` |
| `IdempotencyStatusView` | `GET /api/self-healing/xtest/idempotency/status/` |
| `RegisterKeyView` | `POST /api/self-healing/xtest/idempotency/register/` |
| `ClearKeysView` | `POST /api/self-healing/xtest/idempotency/clear/` |

---

## 1. 목적

Idempotency Service의 멱등성 보장 동작을 X-Test-Mode 환경에서 관찰할 수 있는 API 설계.

### 1.1 테스트 목표

| 목표 | 설명 |
|------|------|
| 키 생성 | IdempotencyKey 생성 및 해시 확인 |
| 중복 감지 | 동일 키 중복 요청 감지 |
| TTL 관리 | 키 만료 동작 확인 |
| 도메인별 동작 | IdempotencyDomain별 격리 확인 |

### 1.2 Idempotency Service 구조 (코드 기준)

`services/idempotency_service.py`에서:

| 컴포넌트 | 설명 |
|----------|------|
| `IdempotencyKey` | 멱등성 키 생성 (entity_type, entity_id, action) |
| `IdempotencyDomain` | 도메인 열거형 (EXTERNAL_SERVICE, ASYNC_TASK 등) |
| `IdempotencyService` | 체크/저장 서비스 |

---

## 2. API 엔드포인트 설계

### 2.1 Idempotency 키 생성 테스트

**엔드포인트:** `POST /api/self-healing/xtest/idempotency/generate-key/`

**목적:** IdempotencyKey 생성 및 해시값 미리보기

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| entity_type | string | O | 엔티티 타입 |
| entity_id | string | O | 엔티티 ID |
| action | string | O | 액션 (process, execute 등) |
| domain | string | X | 도메인 (기본 EXTERNAL_SERVICE) |

**응답:**

| 필드 | 설명 |
|------|------|
| key_string | 생성된 키 문자열 |
| key_hash | SHA256 해시 |
| domain | 적용 도메인 |
| ttl_seconds | TTL 설정값 |

### 2.2 중복 감지 시뮬레이션

**엔드포인트:** `POST /api/self-healing/xtest/idempotency/check-duplicate/`

**목적:** 중복 요청 감지 동작 테스트

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| key | string | O | 테스트할 키 |
| domain | string | X | 도메인 |
| register | bool | X | 키 등록 여부 (기본 false) |

**응답:**

| 필드 | 설명 |
|------|------|
| is_duplicate | 중복 여부 |
| first_seen_at | 최초 등록 시간 (중복 시) |
| ttl_remaining | 남은 TTL |
| registered | 등록 수행 여부 |

### 2.3 Idempotency 상태 조회

**엔드포인트:** `GET /api/self-healing/xtest/idempotency/status/`

**목적:** 현재 등록된 Idempotency 키 상태

**쿼리 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| domain | string | 도메인 필터 |
| prefix | string | 키 프리픽스 필터 |
| limit | int | 조회 개수 (기본 50) |

**응답:**

| 필드 | 설명 |
|------|------|
| total_keys | 등록된 전체 키 수 |
| by_domain | 도메인별 집계 |
| recent_keys | 최근 등록 키 목록 |
| cache_backend | 사용 중인 캐시 백엔드 |

### 2.4 Idempotency 키 등록

**엔드포인트:** `POST /api/self-healing/xtest/idempotency/register/`

**목적:** 테스트용 Idempotency 키 수동 등록

**요청 파라미터:**

| 필드 | 타입 | 필수 | 설명 |
|------|------|------|------|
| key | string | O | 등록할 키 |
| domain | string | X | 도메인 |
| ttl_seconds | int | X | TTL (기본: 설정값) |
| result_data | object | X | 저장할 결과 데이터 |

**응답:**

| 필드 | 설명 |
|------|------|
| registered | 등록 성공 여부 |
| key | 등록된 키 |
| expires_at | 만료 시간 |

### 2.5 Idempotency 키 삭제 (테스트용)

**엔드포인트:** `POST /api/self-healing/xtest/idempotency/clear/`

**목적:** 테스트 키 정리

**요청 파라미터:**

| 필드 | 타입 | 설명 |
|------|------|------|
| key | string | 특정 키 삭제 |
| domain | string | 도메인 전체 삭제 |
| clear_all_xtest | bool | X-Test 생성 키만 삭제 |

---

## 3. 서비스 레이어 연동

### 3.1 기존 서비스 활용

| 메서드 | 위치 | 용도 |
|--------|------|------|
| `IdempotencyKey.for_operation()` | `services/idempotency_service.py` | 키 생성 |
| `IdempotencyService.check()` | `services/idempotency_service.py` | 중복 체크 |
| `IdempotencyService.register()` | `services/idempotency_service.py` | 키 등록 |
| `IdempotencyService.get_stats()` | `services/idempotency_service.py` | 통계 조회 |

### 3.2 IdempotencyDomain 열거형

`services/idempotency_service.py`에 정의:

| 값 | 용도 |
|----|------|
| `EXTERNAL_SERVICE` | 외부 서비스 호출 |
| `INTERNAL_PROCESS` | 내부 프로세스 |
| `ASYNC_TASK` | 비동기 작업 |
| `EVENT` | 이벤트 처리 |
| `CHAOS_EXPERIMENT` | 카오스 실험 |
| `CONFIG_CHANGE` | 설정 변경 |

---

## 4. 구현 순서

### Step 1: View 파일 생성

**파일:** `api/django/views/xtest/idempotency.py`

| 순서 | 클래스 | 설명 |
|------|--------|------|
| 1-1 | `GenerateKeyView` | 키 생성 |
| 1-2 | `CheckDuplicateView` | 중복 감지 |
| 1-3 | `IdempotencyStatusView` | 상태 조회 |
| 1-4 | `RegisterKeyView` | 키 등록 |
| 1-5 | `ClearKeysView` | 키 삭제 |

### Step 2: URL 라우팅

**파일:** `api/django/urls.py`

| 순서 | 경로 | View |
|------|------|------|
| 2-1 | `xtest/idempotency/generate-key/` | `GenerateKeyView` |
| 2-2 | `xtest/idempotency/check-duplicate/` | `CheckDuplicateView` |
| 2-3 | `xtest/idempotency/status/` | `IdempotencyStatusView` |
| 2-4 | `xtest/idempotency/register/` | `RegisterKeyView` |
| 2-5 | `xtest/idempotency/clear/` | `ClearKeysView` |

### Step 3: __init__.py 업데이트

**파일:** `api/django/views/xtest/__init__.py`

- 신규 View export 추가

### Step 4: 테스트 작성

**파일:** `tests/unit/api/xtest/test_idempotency_views.py`

| 테스트 케이스 |
|--------------|
| generate-key 테스트 |
| check-duplicate 첫 요청 테스트 |
| check-duplicate 중복 요청 테스트 |
| register + check 사이클 테스트 |
| clear 테스트 |

---

## 5. 테스트 시나리오

### 5.1 키 생성 및 중복 감지

```
1. POST /xtest/idempotency/generate-key/
   {entity_type: "order", entity_id: "123", action: "process"}
   → key_string: "order:123:process"
   → key_hash: "abc123..."

2. POST /xtest/idempotency/check-duplicate/
   {key: "order:123:process", register: true}
   → is_duplicate: false
   → registered: true

3. POST /xtest/idempotency/check-duplicate/
   {key: "order:123:process"}
   → is_duplicate: true
   → first_seen_at: "2026-01-26T10:00:00Z"
```

### 5.2 도메인별 격리 테스트

```
1. POST /xtest/idempotency/register/
   {key: "test:1", domain: "EXTERNAL_SERVICE"}

2. POST /xtest/idempotency/check-duplicate/
   {key: "test:1", domain: "ASYNC_TASK"}
   → is_duplicate: false (다른 도메인)

3. POST /xtest/idempotency/check-duplicate/
   {key: "test:1", domain: "EXTERNAL_SERVICE"}
   → is_duplicate: true (같은 도메인)
```

### 5.3 TTL 동작 테스트

```
1. POST /xtest/idempotency/register/
   {key: "ttl-test", ttl_seconds: 5}

2. (즉시) POST /xtest/idempotency/check-duplicate/
   {key: "ttl-test"}
   → is_duplicate: true
   → ttl_remaining: 4

3. (5초 후) POST /xtest/idempotency/check-duplicate/
   {key: "ttl-test"}
   → is_duplicate: false (만료됨)
```

---

## 6. 메타데이터 표시

X-Test-Mode로 등록된 키 식별:

| 필드 | 값 |
|------|-----|
| metadata.source | `x-test-mode` |
| metadata.created_by | 사용자 정보 |

---

## 7. 보안 고려사항

### 7.1 기본 보안 (XTestModeMixin)

- `X-Test-Mode: chaos-monkey` 헤더 필수
- 프로덕션 환경 완전 차단

### 7.2 추가 보안

| 항목 | 조치 |
|------|------|
| clear_all_xtest | X-Test 생성 키만 삭제 |
| 키 개수 제한 | status 조회 시 최대 50개 |
| 실제 키 노출 | 해시값으로 대체 옵션 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/idempotency_service.py` | IdempotencyService, IdempotencyKey, IdempotencyDomain |
| `core/time_provider.py` | TimeProvider (TTL 계산) |
| `settings/idempotency.py` | IdempotencySettings |
| `api/django/views/xtest/base.py` | XTestModeMixin 패턴 |

---

**다음 문서:** 122_XTEST_MODE_INTEGRATION.md

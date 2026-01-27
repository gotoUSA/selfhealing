# 135. Exception Handler 고도화 설계

> **Version**: 1.0.0  
> **Created**: 2026-01-27  
> **Status**: Draft  
> **Parent**: [110_EXCEPTION_HANDLER_OVERVIEW.md](110_EXCEPTION_HANDLER_OVERVIEW.md)

## 1. 개요

이 문서는 Exception Handler 시스템의 고도화 설계를 다룹니다:

1. **Causation Chain 전파** - request_id → causation_id 연동으로 API-Celery 인과관계 추적
2. **Role-based Masking** - 역할에 따른 계층화된 민감정보 마스킹

---

## 2. Causation Chain 전파 설계

### 2.1 배경

현재 시스템 상태:
- `CausationContext`가 contextvars 기반으로 구현됨 (`context/causation_context.py`)
- `get_causation_for_celery()`, `restore_causation_from_celery()` 함수 존재
- 예외 핸들러의 `request_id`와 연동되지 않음

### 2.2 설계 결정: request_id → causation_id 전파

**trace_id 별도 추가 대신 기존 request_id를 causation_id로 전파하는 이유:**

| 기준 | trace_id 추가 | request_id 전파 |
|------|--------------|-----------------|
| 기존 인프라 활용 | ❌ 신규 필드 | ✅ CausationContext 재활용 |
| 명명 충돌 | ⚠️ OpenTelemetry와 충돌 | ✅ 독립적 |
| 구현 복잡도 | 중간 | 낮음 |
| 76번 문서 호환 | 추가 매핑 필요 | ✅ 직접 호환 |

### 2.3 구현 범위

#### Phase 1: 예외 핸들러 → CausationContext 연동

**수정 파일:** `api/django/exceptions/handler.py`

```
selfhealing_exception_handler() 함수에서:
1. request_id 추출 (기존 _extract_request_id 활용)
2. CausationContext.is_set() 확인
3. 미설정 시 request_id를 trigger_event_id로 사용하여 CausationContext 설정
4. Audit 이벤트에 cascade_id 포함
```

**코드 근거:**
- `_extract_request_id()` 함수: handler.py#L269-286
- `CausationContext.start_cascade()`: causation_context.py#L176-211
- `CELERY_HEADER_CASCADE_ID` 상수: causation_context.py#L52

#### Phase 2: ResponseMeta에 causation_id 추가

**수정 파일:** `api/django/exceptions/response.py`

```
ResponseMeta 데이터클래스에 추가:
- causation_id: Optional[str] = None  # 인과관계 추적용

to_dict()에서:
- causation_id가 있으면 meta에 포함
```

**코드 근거:**
- 현재 ResponseMeta 구조: response.py#L75-103

#### Phase 3: Celery 자동 전파 (before_task_publish)

**신규 파일:** `context/celery_propagation.py`

```
Celery 시그널 핸들러:
- before_task_publish: headers에 causation 정보 자동 주입
- task_prerun: causation 복원
- task_postrun: causation 정리
```

**코드 근거:**
- `get_causation_for_celery()`: causation_context.py#L337-361
- `restore_causation_from_celery()`: causation_context.py#L364-403
- Celery 컨텍스트 패턴: trace.py#L301-331

### 2.4 데이터 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│ API Request (request_id: "req-abc123")                               │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ ExceptionHandler                                                     │
│   1. _extract_request_id() → "req-abc123"                           │
│   2. CausationContext.start_cascade(trigger_event_id="req-abc123")  │
│   3. cascade_id = "cascade-{uuid}" 생성                              │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ StandardErrorResponse.meta                                           │
│   - request_id: "req-abc123"                                         │
│   - causation_id: "cascade-{uuid}"  ◀─ NEW                          │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼ (Celery Task 호출 시)
┌─────────────────────────────────────────────────────────────────────┐
│ before_task_publish Signal                                           │
│   headers["x-selfhealing-cascade-id"] = "cascade-{uuid}"            │
│   headers["x-selfhealing-parent-event"] = "req-abc123"              │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ Celery Task                                                          │
│   task_prerun: restore_causation_from_celery(headers)               │
│   → CausationContext 복원, chain_depth 증가                          │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 System-initiated Causation (Celery Beat / Management Command)

**목적**: API 요청이 아닌 시스템 자동화 작업의 인과관계 추적

**문제 상황**:
- Celery Beat 스케줄러, Management Command에서 시작되는 작업은 부모 `request_id`가 없음
- `CausationContext.get_current()`가 `None` 반환
- 예외 발생 시 인과관계 추적 불가

**현재 코드 상태**:
- `CausationContext.start_cascade()`: `trigger_event_id` 미전달 시 `evt-{uuid}` 자동 생성 (causation_context.py#L193)
- `SYSTEM_ACTOR`: `actor_id="system"`, `source="internal"` 정의됨 (actor_context.py#L110-115)
- Celery Beat 테스트: `SYSTEM_ACTOR`로 기록되는지 검증 존재 (test_rbac_audit_flow.py#L164-197)

**구현 설계**:

| 항목 | 설명 |
|------|------|
| **Root ID 형식** | `SYSTEM_ROOT_{source}_{UUID}` |
| **source 유형** | `celery_beat`, `management_cmd`, `cron`, `scheduler` |
| **적용 위치** | `CausationContext.start_cascade()` 호출 시 |

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `context/causation_context.py` | `start_cascade()` | `trigger_event_id` 미전달 시 `SYSTEM_ROOT_{source}_{uuid}` 형식 생성 |
| `context/causation_context.py` | 신규 함수 | `start_system_cascade(source: str)` - 시스템 트리거용 |
| `adapters/celery/signal_hooks.py` | `on_task_prerun()` | `CausationContext.is_set()` 확인 후 미설정 시 `start_system_cascade("celery_beat")` 호출 |

**코드 근거**:
- `Actor.source` 필드: `"internal"`, `"celery"`, `"management_command"` 등 구분 (actor_context.py#L68)
- `start_cascade()` 현재 구현: causation_context.py#L176-211
- Celery Beat 태스크 시뮬레이션: test_rbac_audit_flow.py#L179

**데이터 흐름 (System-initiated)**:

```
┌─────────────────────────────────────────────────────────────────────┐
│ Celery Beat / Management Command (부모 request_id 없음)         │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ task_prerun Signal                                                  │
│   1. CausationContext.is_set() → False                            │
│   2. start_system_cascade(source="celery_beat")                    │
│   3. trigger_event_id = "SYSTEM_ROOT_celery_beat_{uuid}"           │
└─────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ 예외 발생 시 Audit 로그                                           │
│   - cascade_id: "cascade-{uuid}"                                   │
│   - trigger_event_id: "SYSTEM_ROOT_celery_beat_{uuid}"             │
│   - actor_id: "system" (SYSTEM_ACTOR)                              │
│   → "사람이 건드리지 않은 자동화 작업" 명확히 구분                   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Role-based Masking 설계 (RBAC 연동)

### 3.1 배경

**기존 RBAC 시스템:**
- 위치: `context/actor_context.py#L52-57`
- `RBAC_ROLE_PRIORITY` 딕셔너리 정의: `selfhealing_admin(3)`, `selfhealing_operator(2)`, `selfhealing_viewer(1)`
- `Actor.roles` 필드: RBAC 역할 목록 저장 (`actor_context.py#L83`)
- `Actor.highest_role` property: 가장 높은 권한 역할 반환 (`actor_context.py#L85-93`)

**현재 마스킹 시스템:**
- `_mask_error_message()`: handler.py#L396-426 - 단일 레벨 마스킹 (`[MASKED]` 치환)
- `hash_for_audit()`: masking.py#L89-116 - SHA-256 해시 (미사용)
- `mask_sensitive_fields()`: masking.py#L122-169 - 딕셔너리 필드 마스킹

**문제점:**
- RBAC 역할에 따른 차등 마스킹 미적용
- 클라이언트 응답과 내부 Audit 로그에 동일한 마스킹 적용
- 법적 조사 시 원본 데이터 추적 불가
- 에러 패턴 분석 시 동일 사용자 식별 불가

### 3.2 마스킹 레벨과 RBAC 역할 매핑

**MaskingLevel ↔ RBAC 역할 매핑:**

| MaskingLevel | 필요 RBAC 역할 | 마스킹 방식 | 동일성 확인 | 용도 |
|--------------|---------------|------------|------------|------|
| `CLIENT` | (모든 사용자) | `***REDACTED***` 치환 | ❌ 불가 | API 응답 노출 |
| `AUDIT` | `selfhealing_operator` 이상 | SHA-256 해시 (16자) | ✅ 가능 | 내부 패턴 분석 |
| `FORENSIC` | `selfhealing_admin` | 암호화 저장 | ✅ 복원 가능 | 법적 조사 |

**RBAC 우선순위 기반 결정 (actor_context.py#L52-57):**

| RBAC 역할 | 우선순위 값 | 접근 가능 MaskingLevel |
|-----------|------------|------------------------|
| `selfhealing_admin` | 3 | CLIENT, AUDIT, FORENSIC |
| `selfhealing_operator` | 2 | CLIENT, AUDIT |
| `selfhealing_viewer` | 1 | CLIENT |

### 3.3 구현 범위

#### Phase 1: MaskingLevel Enum 추가

**수정 파일:** `audit/masking.py`

```
class MaskingLevel(Enum):
    CLIENT = "client"      # 클라이언트 응답용 - 완전 치환
    AUDIT = "audit"        # 내부 감사용 - 해시화 (동일성 확인 가능)
    FORENSIC = "forensic"  # 법적 조사용 - 암호화 저장

def mask_with_level(value: str, level: MaskingLevel, salt: Optional[str] = None) -> str:
    """레벨에 따른 마스킹 적용."""
```

**코드 근거:**
- 기존 `hash_for_audit()`: masking.py#L89-116
- 기존 `mask_sensitive_fields()`: masking.py#L122-169

#### Phase 2: ActorContext RBAC 연동

**수정 파일:** `audit/masking.py`

**구현 위치:** `get_masking_level_for_context()` 함수

**RBAC 연동 로직:**
1. `ActorContext.get_current()` 호출하여 현재 Actor 조회
2. `Actor.highest_role` property로 가장 높은 RBAC 역할 확인
3. `RBAC_ROLE_PRIORITY` 값에 따라 MaskingLevel 결정

**역할 판단 규칙:**
- `selfhealing_admin` (우선순위 3) → FORENSIC 레벨 접근 허용
- `selfhealing_operator` (우선순위 2) → AUDIT 레벨까지 허용
- `selfhealing_viewer` (우선순위 1) → CLIENT 레벨만 허용
- 역할 없음 → CLIENT 레벨 (기본값)

**코드 근거:**
- `ActorContext.get_current()`: actor_context.py#L189-209
- `Actor.highest_role`: actor_context.py#L85-93
- `RBAC_ROLE_PRIORITY`: actor_context.py#L52-57
- `Actor.roles` 필드: actor_context.py#L83

#### Phase 3: 예외 핸들러 연동

**수정 파일:** `api/django/exceptions/handler.py`

```
_mask_error_message() 수정:
1. 클라이언트 응답용: MaskingLevel.CLIENT 적용
2. Audit 버퍼 기록용: MaskingLevel.AUDIT 적용
   - hash_for_audit() 사용으로 동일성 확인 가능
```

**코드 근거:**
- 현재 `_mask_error_message()`: handler.py#L396-426
- `_record_audit_event()`: handler.py#L307-360

### 3.4 마스킹 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│ 예외 발생: ValueError("user_email=admin@example.com is invalid")     │
└─────────────────────────────────────────────────────────────────────┘
         │
         ├──────────────────────────────────────────┐
         ▼                                          ▼
┌─────────────────────────────┐    ┌─────────────────────────────────┐
│ 클라이언트 응답              │    │ Audit 로그                       │
│ MaskingLevel.CLIENT          │    │ MaskingLevel.AUDIT               │
│                              │    │                                  │
│ error.detail:                │    │ error_message:                   │
│ "[MASKED] Error message may  │    │ "sha256:a1b2c3d4e5f6..."         │
│  contain sensitive data"     │    │                                  │
│                              │    │ → 동일 이메일이면 동일 해시      │
│ → 완전히 숨김                │    │ → DoS 패턴 분석 가능              │
└─────────────────────────────┘    └─────────────────────────────────┘
```

### 3.5 ID-Preserving 마스킹 활용

**해시 기반 마스킹의 가치:**

```
에러 로그 분석 시나리오:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[15:30:01] VALIDATION_ERROR | user_hash: sha256:a1b2c3d4
[15:30:02] VALIDATION_ERROR | user_hash: sha256:a1b2c3d4  ← 동일 사용자
[15:30:03] VALIDATION_ERROR | user_hash: sha256:a1b2c3d4
[15:30:04] VALIDATION_ERROR | user_hash: sha256:a1b2c3d4
[15:30:05] VALIDATION_ERROR | user_hash: sha256:a1b2c3d4
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

분석: 동일 해시(sha256:a1b2c3d4)가 5회 연속 에러 유발
     → DoS 공격 또는 악의적 사용자 의심
     → 원본 정보 없이도 패턴 파악 가능
```

---

## 4. 구현 체크리스트

### Phase 1: Causation Chain (우선순위: 높음)

- [ ] `api/django/exceptions/handler.py`
  - [ ] `_init_causation_context()` 함수 추가
  - [ ] `selfhealing_exception_handler()`에서 CausationContext 초기화
  - [ ] Audit 이벤트에 `cascade_id` 포함

- [ ] `api/django/exceptions/response.py`
  - [ ] `ResponseMeta`에 `causation_id` 필드 추가
  - [ ] `to_dict()`에서 causation_id 직렬화

- [ ] `context/celery_propagation.py` (신규)
  - [ ] `before_task_publish` 시그널 핸들러
  - [ ] `task_prerun` 시그널 핸들러
  - [ ] `task_postrun` 시그널 핸들러
  - [ ] `setup_celery_causation_propagation()` 초기화 함수

- [ ] `context/causation_context.py` (System-initiated 지원)
  - [ ] `start_system_cascade(source: str)` 함수 추가
  - [ ] `trigger_event_id` 미전달 시 `SYSTEM_ROOT_{source}_{uuid}` 형식 생성
  - [ ] `on_task_prerun()`에서 `CausationContext.is_set()` 확인 후 자동 생성

### Phase 2: Role-based Masking (우선순위: 중간)

- [ ] `audit/masking.py`
  - [ ] `MaskingLevel` Enum 추가
  - [ ] `mask_with_level()` 함수 추가
  - [ ] `get_masking_level_for_context()` 함수 추가

- [ ] `api/django/exceptions/handler.py`
  - [ ] `_mask_error_message()` 리팩토링
  - [ ] 클라이언트 응답용 마스킹 분리
  - [ ] Audit 기록용 마스킹 분리

### Phase 3: 테스트

- [ ] `tests/api/exceptions/test_causation_propagation.py`
  - [ ] request_id → causation_id 전파 테스트
  - [ ] Celery 헤더 주입 테스트
  - [ ] Celery 복원 테스트

- [ ] `tests/context/test_system_initiated_causation.py`
  - [ ] Celery Beat 태스크에서 SYSTEM_ROOT_celery_beat 형식 생성 검증
  - [ ] Management Command에서 SYSTEM_ROOT_management_cmd 형식 생성 검증
  - [ ] CausationContext 미설정 시 자동 생성 검증

- [ ] `tests/audit/test_role_based_masking.py`
  - [ ] MaskingLevel별 출력 검증
  - [ ] hash_for_audit 동일성 확인 테스트
  - [ ] ActorContext 기반 레벨 결정 테스트

---

## 5. 마이그레이션 가이드

### 5.1 하위 호환성

**응답 포맷 변경:**
- `meta.causation_id` 필드 추가 (Optional)
- 기존 클라이언트는 무시 가능

**Celery 헤더 추가:**
- 기존 Task는 헤더 없이 동작 (graceful degradation)
- `restore_causation_from_celery()`가 헤더 없으면 None 반환

### 5.2 설정 추가

```python
# settings.py (선택적)

SELFHEALING_MASKING = {
    "default_level": "audit",  # CLIENT, AUDIT, FORENSIC
    "system_salt": os.environ.get("SELFHEALING_MASKING_SALT", "default-salt"),
}
```

---

## 6. 참조 문서

| 문서 | 관련 내용 |
|------|----------|
| [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md) | Causation Chain 구조 |
| [110_EXCEPTION_HANDLER_OVERVIEW.md](110_EXCEPTION_HANDLER_OVERVIEW.md) | 예외 핸들러 아키텍처 |
| [111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md](111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md) | Audit 연동 |
| [115_EXCEPTION_HANDLER_CHECKLIST.md](115_EXCEPTION_HANDLER_CHECKLIST.md) | 구현 체크리스트 |

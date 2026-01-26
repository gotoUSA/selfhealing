# 111. Exception Handler와 Audit 시스템 통합

## 1. 기존 Audit 시스템 분석

### 1.1 핵심 인터페이스: AuditEntry

`interfaces/audit_adapter.py`에 정의된 표준 Audit 엔트리 구조:

| 필드 | 타입 | 설명 |
|------|------|------|
| action | AuditAction | 수행된 액션 유형 |
| timestamp | datetime | 이벤트 발생 시간 |
| actor_id | str | 수행자 ID (ActorContext에서 자동 추출) |
| actor_type | str | user/system/api 등 |
| actor_roles | list | 수행자 권한 목록 |
| context_type | ContextType | REQUEST/TASK/SYSTEM/WEBHOOK/CLI |
| target_type | str | 대상 유형 |
| target_id | str | 대상 ID |
| service_name | str | 서비스명 |
| domain | str | 도메인명 |
| reason | str | 사유 |
| details | dict | 상세 정보 |
| success | bool | 성공 여부 |
| error_message | str | 에러 메시지 |

### 1.2 이벤트 버퍼 시스템

`audit/event_buffer.py`의 RequestAuditBuffer:

- 요청별로 이벤트를 버퍼링
- request.META에 저장되어 미들웨어 체인 전체에서 접근
- AuditMiddleware가 응답 직전에 일괄 수집

**기존 AuditEventType 목록** (관련 항목):
- ERROR_DETECTED - 일반 에러 감지
- GOVERNANCE_BLOCKED - 거버넌스 차단
- SECURITY_VIOLATION - 보안 위반

### 1.3 ContextType 활용

`interfaces/audit_adapter.py`에 정의:

| 값 | 설명 | 예외 핸들러 사용 |
|----|------|------------------|
| REQUEST | HTTP 요청 처리 중 | ✅ API 예외 |
| TASK | 백그라운드 태스크 | Celery 태스크 예외 |
| SYSTEM | 시스템 자동화 | 스케줄러 예외 |
| WEBHOOK | 외부 웹훅 | 웹훅 처리 예외 |
| CLI | CLI 명령 | 커맨드라인 예외 |

---

## 2. 통합 설계

### 2.1 신규 AuditEventType 추가

`audit/event_buffer.py`에 추가 필요:

| EventType | 설명 | 사용 시점 |
|-----------|------|-----------|
| API_EXCEPTION | API 예외 발생 | DRF 예외 핸들러 |
| API_VALIDATION_ERROR | 입력 검증 실패 | Serializer 에러 |
| API_PERMISSION_DENIED | 권한 거부 | PermissionDenied 예외 |
| API_NOT_FOUND | 리소스 없음 | Http404, NotFound 예외 |
| API_THROTTLED | 요청 제한 | Throttled 예외 |

### 2.2 AuditAction 확장

`interfaces/audit_adapter.py`의 AuditAction에 추가 고려:

| Action | 설명 |
|--------|------|
| API_ERROR | API 처리 중 에러 발생 |
| VALIDATION_FAILED | 입력 검증 실패 |
| AUTHORIZATION_DENIED | 인가 거부 |

### 2.3 예외 핸들러 → Audit 버퍼 연동 흐름

```
1. View에서 예외 발생
   │
   ▼
2. DRF Exception Handler 호출
   │
   ▼
3. SelfHealingExceptionHandler 진입
   │
   ├─► 예외 분류 (ExceptionClassifier)
   │   - exception_type: validation/permission/not_found/internal/...
   │   - is_retryable: bool
   │   - error_code: 표준 에러 코드
   │
   ├─► RequestAuditBuffer.get_or_create(request)
   │
   ├─► buffer.add(
   │       event_type=AuditEventType.API_EXCEPTION,
   │       source="ExceptionHandler",
   │       details={
   │           "exception_class": type(exc).__name__,
   │           "error_code": "...",
   │           "path": request.path,
   │           "method": request.method,
   │       },
   │       success=False,
   │       error_message=str(exc),
   │   )
   │
   └─► Response 반환
       │
       ▼
4. AuditMiddleware가 버퍼 수집
   │
   ▼
5. ContinuousAuditRecorder로 해시 체인 기록
```

---

## 3. 기존 AuditMiddleware와의 관계

### 3.1 현재 AuditMiddleware 동작

`api/django/audit_middleware.py` 분석:

1. **요청 시작**: request_id 생성, 버퍼 초기화
2. **요청 처리**: View 실행
3. **응답 캡처**: 4xx/5xx 시 ERROR_DETECTED 이벤트 추가
4. **버퍼 수집**: 모든 이벤트를 ContinuousAuditRecorder로 기록

### 3.2 예외 핸들러와의 역할 분담

| 역할 | AuditMiddleware | ExceptionHandler |
|------|-----------------|------------------|
| 버퍼 초기화 | ✅ | - |
| 요청 메타데이터 | ✅ | - |
| 4xx/5xx 감지 | ✅ (상태 코드 기반) | - |
| 예외 상세 정보 | ❌ | ✅ (예외 객체 접근) |
| 예외 분류 | ❌ | ✅ |
| 에러 코드 | ❌ | ✅ |
| 버퍼 수집/기록 | ✅ | - |

**결론**: 두 시스템은 **상호 보완적**이며, 중복 기록을 방지해야 함.

### 3.3 중복 기록 방지 전략

ExceptionHandler가 버퍼에 이벤트를 추가할 때:
```python
buffer.add(
    event_type=AuditEventType.API_EXCEPTION,
    source="ExceptionHandler",  # 소스 명시
    ...
)
```

AuditMiddleware의 `_capture_response_meta`에서:
```python
# ExceptionHandler가 이미 기록했으면 스킵
if buffer.has_event_from_source("ExceptionHandler"):
    return  # ERROR_DETECTED 추가하지 않음
```

---

## 4. 표준 에러 응답과 Audit 필드 매핑

### 4.1 표준 에러 응답 포맷

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_ERROR",
        "message": "입력값이 올바르지 않습니다.",
        "detail": "amount must be positive",
        "field": "amount"
    },
    "request_id": "uuid-...",
    "timestamp": "2024-01-26T12:00:00Z"
}
```

### 4.2 AuditEntry 매핑

| 응답 필드 | AuditEntry 필드 |
|-----------|-----------------|
| error.code | details["error_code"] |
| error.message | reason |
| error.detail | error_message |
| request_id | (RequestAuditBuffer에서 관리) |
| timestamp | timestamp |

### 4.3 details 필드 표준 구조

```python
details = {
    "error_code": "VALIDATION_ERROR",
    "exception_class": "ValidationError",
    "path": "/api/payments/",
    "method": "POST",
    "status_code": 400,
    "is_retryable": False,
    "field": "amount",  # 필드 관련 에러 시
}
```

---

## 5. ActorContext 활용

### 5.1 자동 Actor 추출

`interfaces/audit_adapter.py`의 `_get_default_actor()`:

- ActorContext가 설정되어 있으면 자동으로 actor 정보 추출
- 예외 핸들러에서 별도로 actor를 지정하지 않아도 됨

### 5.2 예외 핸들러에서의 활용

```python
# ExceptionHandler 내부
entry = AuditEntry(
    action=AuditAction.API_ERROR,
    context_type=ContextType.REQUEST,
    # actor_id, actor_type은 자동 채워짐
    target_type="api_endpoint",
    target_id=request.path,
    ...
)
```

---

## 6. 기존 감사 경로와의 일관성

### 6.1 WAL 기반 누락 0 보장

`services/audit/base.py` 분석:
- 모든 audit 이벤트는 WAL에 먼저 기록
- 이후 중앙 저장소에 기록 시도
- Background Sync Worker가 동기화

예외 핸들러의 이벤트도 동일한 경로를 거침:
1. RequestAuditBuffer에 추가
2. AuditMiddleware가 수집
3. ContinuousAuditRecorder로 기록
4. WAL → 중앙 저장소

### 6.2 해시 체인 무결성

ContinuousAuditRecorder가 모든 이벤트를 해시 체인으로 연결:
- 예외 이벤트도 체인에 포함
- 조작 시 무결성 검증으로 감지

---

## 7. 고려사항

### 7.1 성능

| 항목 | 고려 |
|------|------|
| 버퍼 추가 | O(1), 메모리 기반 |
| 예외 분류 | O(1), isinstance 체크 |
| JSON 직렬화 | 응답 생성 시 1회 |

### 7.2 에러 전파 방지

예외 핸들러 자체에서 에러 발생 시:
- try-except로 감싸서 폴백 응답 반환
- SelfAuditLogger로 자체 에러 기록

### 7.3 민감정보 마스킹

- `audit/masking.py`의 `mask_sensitive_fields` 활용
- 예외 메시지에 포함된 민감정보 마스킹

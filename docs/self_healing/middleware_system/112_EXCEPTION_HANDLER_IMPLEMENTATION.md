# 112. Exception Handler 구현 가이드

## 1. 파일 구조

```
selfhealing/api/django/
├── exceptions/
│   ├── __init__.py           # 공개 API
│   ├── classifier.py         # 예외 분류기
│   ├── response.py           # 표준 응답 생성기
│   ├── codes.py              # 에러 코드 정의
│   └── handler.py            # DRF 예외 핸들러
└── exception_handler.py      # 레거시 호환 (handler.py 재수출)
```

---

## 2. 에러 코드 체계

### 2.1 코드 구조

형식: `{CATEGORY}_{SUBCATEGORY}_{DETAIL}`

| 카테고리 | 설명 | 예시 |
|----------|------|------|
| VALIDATION | 입력 검증 | VALIDATION_FIELD_REQUIRED |
| AUTH | 인증 | AUTH_TOKEN_EXPIRED |
| AUTHZ | 인가 | AUTHZ_PERMISSION_DENIED |
| RESOURCE | 리소스 | RESOURCE_NOT_FOUND |
| RATE | 요청 제한 | RATE_LIMIT_EXCEEDED |
| CONFIG | 설정 | CONFIG_LOCKED |
| SYSTEM | 시스템 | SYSTEM_INTERNAL_ERROR |
| SERVICE | 외부 서비스 | SERVICE_UNAVAILABLE |

### 2.2 기존 뷰별 에러와 매핑

| 기존 패턴 | 표준 코드 |
|-----------|-----------|
| ConfigLockError | CONFIG_LOCKED |
| ValueError | VALIDATION_INVALID_VALUE |
| "not found" | RESOURCE_NOT_FOUND |
| 일반 Exception | SYSTEM_INTERNAL_ERROR |

---

## 3. 표준 응답 포맷

### 3.1 에러 응답 구조

```json
{
    "success": false,
    "error": {
        "code": "VALIDATION_FIELD_REQUIRED",
        "message": "필수 필드가 누락되었습니다.",
        "detail": "The 'amount' field is required.",
        "field": "amount",
        "retryable": false
    },
    "meta": {
        "request_id": "abc-123",
        "timestamp": "2024-01-26T12:00:00Z",
        "path": "/api/payments/",
        "method": "POST"
    }
}
```

### 3.2 필드 설명

| 필드 | 필수 | 설명 |
|------|------|------|
| success | ✅ | 항상 false |
| error.code | ✅ | 표준 에러 코드 |
| error.message | ✅ | 사용자 친화적 메시지 |
| error.detail | 선택 | 기술적 상세 정보 |
| error.field | 선택 | 필드 관련 에러 시 |
| error.retryable | 선택 | 재시도 가능 여부 |
| meta.request_id | ✅ | 요청 추적 ID |
| meta.timestamp | ✅ | 에러 발생 시간 |

---

## 4. 예외 분류기 설계

### 4.1 분류 기준

| 분류 | 예외 유형 | HTTP 상태 |
|------|-----------|-----------|
| VALIDATION | ValidationError, ValueError, Serializer 에러 | 400 |
| AUTH | AuthenticationFailed | 401 |
| AUTHZ | PermissionDenied | 403 |
| NOT_FOUND | Http404, NotFound | 404 |
| CONFLICT | ConfigLockError, IntegrityError | 409 |
| RATE_LIMIT | Throttled | 429 |
| INTERNAL | Exception (기타) | 500 |
| SERVICE | 외부 서비스 에러 | 503 |

### 4.2 커스텀 예외 매핑

selfhealing 패키지 내 커스텀 예외:

| 예외 클래스 | 분류 | 코드 |
|-------------|------|------|
| ConfigLockError | CONFLICT | CONFIG_LOCKED |
| GovernanceBlockedError | FORBIDDEN | AUTHZ_GOVERNANCE_BLOCKED |
| CircuitBreakerOpenError | SERVICE | SERVICE_CIRCUIT_OPEN |
| DLQStorageError | INTERNAL | SYSTEM_DLQ_ERROR |

### 4.3 재시도 가능 여부 판단

| 분류 | 재시도 가능 | 이유 |
|------|-------------|------|
| VALIDATION | ❌ | 입력 수정 필요 |
| AUTH | ❌ | 재인증 필요 |
| AUTHZ | ❌ | 권한 없음 |
| NOT_FOUND | ❌ | 리소스 없음 |
| CONFLICT | 조건부 | 락 해제 후 가능 |
| RATE_LIMIT | ✅ | 대기 후 재시도 |
| INTERNAL | 조건부 | 일시적 오류일 수 있음 |
| SERVICE | ✅ | 서비스 복구 후 가능 |

---

## 5. DRF 통합

### 5.1 settings.py 설정

```python
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'selfhealing.api.django.exceptions.handler.selfhealing_exception_handler',
    # ... 기타 설정
}
```

### 5.2 핸들러 시그니처

```python
def selfhealing_exception_handler(
    exc: Exception,
    context: dict,
) -> Response | None:
    """
    DRF 커스텀 예외 핸들러.
    
    Args:
        exc: 발생한 예외
        context: DRF 컨텍스트 (view, request, format, args, kwargs)
    
    Returns:
        Response 또는 None (None이면 예외 재발생)
    """
```

### 5.3 DRF 기본 핸들러 활용

```python
from rest_framework.views import exception_handler as drf_exception_handler

def selfhealing_exception_handler(exc, context):
    # DRF 기본 핸들러 먼저 호출
    response = drf_exception_handler(exc, context)
    
    if response is None:
        # DRF가 처리하지 않는 예외
        response = handle_non_drf_exception(exc, context)
    
    # 표준화 및 Audit 기록
    return standardize_and_audit(response, exc, context)
```

---

## 6. Audit 버퍼 연동

### 6.1 이벤트 적재 위치

```python
def selfhealing_exception_handler(exc, context):
    request = context.get('request')
    
    # ... 예외 처리 로직 ...
    
    # Audit 버퍼에 이벤트 적재
    if request:
        try:
            from selfhealing.audit.event_buffer import (
                RequestAuditBuffer, 
                AuditEventType
            )
            
            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(
                event_type=AuditEventType.API_EXCEPTION,
                source="ExceptionHandler",
                details={
                    "error_code": error_code,
                    "exception_class": type(exc).__name__,
                    "path": request.path,
                    "method": request.method,
                    "status_code": response.status_code,
                },
                success=False,
                error_message=str(exc)[:500],  # 길이 제한
            )
        except Exception:
            pass  # Audit 실패가 응답을 막지 않음
    
    return response
```

### 6.2 AuditEventType 확장

`audit/event_buffer.py`에 추가:

```python
class AuditEventType(Enum):
    # ... 기존 ...
    
    # API Exception 관련 (112_EXCEPTION_HANDLER_IMPLEMENTATION)
    API_EXCEPTION = "api_exception"
    """API 요청 처리 중 예외 발생."""
    
    API_VALIDATION_ERROR = "api_validation_error"
    """입력값 검증 실패."""
    
    API_AUTH_ERROR = "api_auth_error"
    """인증/인가 실패."""
```

---

## 7. 마이그레이션 전략

### 7.0 핵심 원칙: 기존 코드는 그대로 동작함

> ⚠️ **중요**: Exception Handler 구현 후에도 **기존 코드 수정은 선택 사항**입니다.

| 상황 | 동작 | 수정 필요 |
|------|------|-----------|
| 기존 try-except로 Response 반환 | 그대로 동작 (핸들러 호출 안됨) | ❌ 불필요 |
| try-except 없이 예외 발생 | 핸들러가 처리 | ❌ 불필요 |
| 새로운 뷰 작성 | 핸들러 자동 적용 | ❌ 불필요 |

**왜?**
- DRF Exception Handler는 **뷰에서 처리되지 않은 예외**만 처리
- 기존 try-except로 Response를 반환하면 예외가 발생하지 않음
- 따라서 기존 코드와 **공존 가능**

### 7.1 점진적 적용 (권장)

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 1: Handler 구현 및 설정 (즉시)                             │
│   - Exception Handler 구현                                      │
│   - settings.py에 EXCEPTION_HANDLER 설정                        │
│   - 기존 코드 수정 없이 배포 가능                                │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 2: 신규 코드에만 적용 (진행 중)                            │
│   - 새로운 뷰: try-except 없이 작성                              │
│   - 기존 뷰: 그대로 유지 (선택적 리팩토링)                       │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3: 점진적 리팩토링 (장기)                                  │
│   - 기존 뷰 수정 시 함께 리팩토링                                │
│   - 일괄 마이그레이션 불필요                                     │
└─────────────────────────────────────────────────────────────────┘
```

### 7.2 기존 뷰 호환성

**기존 코드 (수정 불필요 - 그대로 동작)**:
```python
def post(self, request):
    try:
        result = service.do_something()
        return Response(result)
    except ValueError as e:
        # 이 Response가 반환되면 예외 핸들러는 호출되지 않음
        return Response({"error": str(e)}, status=400)
```

**신규 코드 (권장 패턴)**:
```python
def post(self, request):
    result = service.do_something()  # ValueError 발생 시 핸들러가 처리
    return Response(result)
```

### 7.3 리팩토링이 필요한 경우

**선택적 리팩토링을 고려할 상황**:

| 상황 | 리팩토링 이점 |
|------|--------------|
| 뷰 코드가 try-except로 복잡 | 코드 간소화 |
| Audit 로깅이 누락됨 | 자동 Audit 기록 |
| 응답 포맷 표준화 필요 | 일관된 에러 응답 |
| 에러 코드 체계 도입 | 클라이언트 처리 개선 |

**리팩토링 예시**:

Before (기존):
```python
def post(self, request):
    try:
        data = request.data
        if not data.get("amount"):
            return Response({"error": "amount is required"}, status=400)
        result = service.process(data)
        return Response(result)
    except ValueError as e:
        return Response({"error": str(e)}, status=400)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return Response({"error": str(e)}, status=500)
```

After (리팩토링):
```python
def post(self, request):
    serializer = MySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)  # ValidationError → 핸들러
    result = service.process(serializer.validated_data)  # 예외 → 핸들러
    return Response(result)
```

### 7.4 커스텀 응답이 필요한 경우

특정 뷰에서 표준 포맷과 다른 응답이 필요할 때:

```python
class SpecialView(APIView):
    def post(self, request):
        try:
            # 비즈니스 로직
        except SpecialError as e:
            # 직접 처리 (핸들러 우회)
            # 주의: 이 경우 Audit 수동 기록 필요
            self._record_audit(request, e)
            return Response({"special": "format"}, status=400)
```

### 7.5 일괄 마이그레이션 (비권장)

> ⚠️ 기존 코드 일괄 수정은 **리스크가 높아 권장하지 않음**

일괄 수정 시 고려사항:
- 테스트 커버리지가 충분한가?
- 클라이언트가 기존 응답 포맷에 의존하는가?
- 롤백 계획이 있는가?

**권장**: 기능 수정/추가 시 해당 뷰만 점진적 리팩토링

---

## 8. 테스트 전략

### 8.1 단위 테스트

| 테스트 | 검증 내용 |
|--------|-----------|
| ExceptionClassifier | 예외 → 분류 매핑 정확성 |
| StandardErrorResponse | 응답 포맷 일관성 |
| handler | DRF 통합 동작 |

### 8.2 통합 테스트

| 테스트 | 검증 내용 |
|--------|-----------|
| AuditMiddleware 연동 | 예외 이벤트가 버퍼에 기록 |
| 해시 체인 포함 | 예외 이벤트가 체인에 포함 |
| 중복 방지 | ERROR_DETECTED와 중복 안됨 |

---

## 9. 모니터링

### 9.1 메트릭

| 메트릭 | 설명 |
|--------|------|
| selfhealing_api_exception_total | 예외 발생 총 횟수 |
| selfhealing_api_exception_by_code | 코드별 예외 횟수 |
| selfhealing_api_exception_by_path | 경로별 예외 횟수 |

### 9.2 대시보드 연동

Grafana 대시보드에서 조회 가능한 정보:
- 실시간 에러율
- 에러 코드 분포
- 에러 발생 경로 Top 10

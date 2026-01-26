# 114. Audit 시스템 구조 분석

## 1. 계층 구조

```
selfhealing/
├── interfaces/
│   └── audit_adapter.py              # 추상 인터페이스 계층
│       ├── AuditAction               # 감사 액션 유형
│       ├── ContextType               # 컨텍스트 유형
│       ├── AuditEntry                # 감사 엔트리 데이터 클래스
│       └── AuditLogAdapter           # 어댑터 추상 클래스
│
├── audit/
│   ├── logger.py                     # 설정 변경 감사 로거
│   │   ├── AuditAction (별도 enum)
│   │   ├── ConfigChangeEvent
│   │   └── AuditLogger
│   │
│   ├── self_audit.py                 # 감사 시스템 자체 모니터링
│   │   ├── SelfAuditEvent
│   │   ├── SelfAuditStats
│   │   └── SelfAuditLogger
│   │
│   ├── event_buffer.py               # 요청별 이벤트 버퍼
│   │   ├── AuditEventType
│   │   ├── BufferedEvent
│   │   └── RequestAuditBuffer
│   │
│   ├── audit_integration.py          # 통합 연결 모듈
│   │   ├── AsyncLoggerAdapter
│   │   ├── IntegratedAuditRecorder
│   │   └── AuditEventObserver
│   │
│   ├── continuous_audit.py           # 연속 감사 기록
│   │   └── ContinuousAuditRecorder
│   │
│   ├── wal.py                        # Write-Ahead Log
│   │   └── WriteAheadLog
│   │
│   ├── masking.py                    # 민감정보 마스킹
│   ├── trace.py                      # Trace ID 관리
│   ├── reconciler.py                 # 감사 로그 조정
│   └── hash_chain_safety.py          # 해시 체인 무결성
│
├── api/django/
│   └── audit_middleware.py           # Django 미들웨어
│       └── AuditMiddleware
│
├── services/audit/
│   └── base.py                       # 서비스 레이어 공통
│       └── WAL 헬퍼 함수들
│
└── adapters/audit/
    ├── file_adapter.py               # 파일 기반 어댑터
    ├── redis_buffer.py               # Redis 버퍼
    └── singleton.py                  # 어댑터 싱글톤
```

---

## 2. 주요 컴포넌트 역할

### 2.1 interfaces/audit_adapter.py

**핵심 인터페이스 정의 계층**

| 컴포넌트 | 역할 |
|----------|------|
| AuditAction | 감사 액션 유형 enum (CB_FORCE_OPEN, DLQ_STORE 등) |
| ContextType | 이벤트 발생 컨텍스트 (REQUEST, TASK, SYSTEM 등) |
| AuditEntry | 감사 로그 엔트리 데이터 클래스 |
| AuditLogAdapter | 감사 로그 어댑터 추상 클래스 |

**특징**:
- 사용자가 구현할 인터페이스 정의
- ActorContext 자동 연동
- 표준화된 필드 구조

### 2.2 audit/event_buffer.py

**요청별 이벤트 버퍼링**

| 컴포넌트 | 역할 |
|----------|------|
| AuditEventType | 이벤트 유형 enum (DLQ_STORE, CB_STATE_CHANGE 등) |
| BufferedEvent | 버퍼된 단일 이벤트 |
| RequestAuditBuffer | 요청별 버퍼 관리 |

**특징**:
- request.META에 저장
- 미들웨어 체인 전체에서 접근 가능
- AuditMiddleware가 응답 직전에 수집

### 2.3 audit/logger.py

**설정 변경 전용 감사 로거**

| 컴포넌트 | 역할 |
|----------|------|
| AuditAction | 설정 변경 액션 (CREATE, UPDATE, DELETE 등) |
| ConfigChangeEvent | 설정 변경 이벤트 데이터 클래스 |
| AuditLogger | 싱글톤 로거 |

**특징**:
- IP 마스킹 지원
- 민감 필드 자동 마스킹
- 멀티 백엔드 지원
- 해시 체인 무결성

### 2.4 audit/self_audit.py

**감사 시스템 자체 모니터링**

| 컴포넌트 | 역할 |
|----------|------|
| SelfAuditEvent | 자체 이벤트 유형 (STARTUP, WAL_WRITE_FAILED 등) |
| SelfAuditStats | 통계 데이터 클래스 |
| SelfAuditLogger | 자체 모니터링 로거 |

**특징**:
- 순환 의존 없음 (독립적)
- 최소 의존성 (표준 라이브러리만)
- 항상 성공하도록 설계

### 2.5 api/django/audit_middleware.py

**Django 미들웨어**

| 기능 | 설명 |
|------|------|
| 버퍼 초기화 | 요청 시작 시 RequestAuditBuffer 생성 |
| 요청 ID | X-Request-ID 생성/추출 |
| 조회 기록 | 설정된 경로의 GET 요청 기록 |
| 응답 캡처 | 4xx/5xx 응답 시 이벤트 추가 |
| 버퍼 수집 | 모든 이벤트를 ContinuousAuditRecorder로 전달 |

**특징**:
- MIDDLEWARE 리스트 가장 마지막에 위치
- Fail-Open 정책 (실패가 비즈니스를 막지 않음)

---

## 3. 데이터 흐름

### 3.1 일반 요청 처리

```
1. Request 수신
   │
   ▼
2. AuditMiddleware._init_buffer()
   - RequestAuditBuffer 생성
   - request_id 설정
   │
   ▼
3. 미들웨어 체인 / View 처리
   - 각 컴포넌트가 buffer.add() 호출
   │
   ▼
4. AuditMiddleware._capture_response_meta()
   - 4xx/5xx 시 ERROR_DETECTED 추가
   │
   ▼
5. AuditMiddleware._record_events()
   - buffer.get_all_events() 수집
   - ContinuousAuditRecorder.record_batch() 호출
   │
   ▼
6. ContinuousAuditRecorder
   - WAL에 먼저 기록
   - 중앙 저장소에 기록 시도
   - 해시 체인 연결
```

### 3.2 예외 발생 시 (현재)

```
1. View에서 예외 발생
   │
   ▼
2. View 내 try-except 처리
   - Response 직접 반환
   - (Audit 기록 누락 가능)
   │
   ▼
3. AuditMiddleware
   - status_code >= 400 감지
   - ERROR_DETECTED 이벤트 추가
   - (예외 상세 정보 없음)
```

### 3.3 예외 발생 시 (목표)

```
1. View에서 예외 발생
   │
   ▼
2. DRF Exception Handler 호출
   │
   ▼
3. SelfHealingExceptionHandler
   - 예외 분류
   - 표준 응답 생성
   - buffer.add(API_EXCEPTION, ...) ← 상세 정보 포함
   │
   ▼
4. AuditMiddleware
   - ExceptionHandler 이벤트 확인
   - 중복 기록 방지
   - 버퍼 수집 및 기록
```

---

## 4. AuditAction vs AuditEventType

### 4.1 두 Enum의 차이

| 항목 | AuditAction | AuditEventType |
|------|-------------|----------------|
| 위치 | interfaces/audit_adapter.py | audit/event_buffer.py |
| 용도 | 최종 저장용 액션 유형 | 버퍼링 중 이벤트 유형 |
| 사용 | AuditEntry.action | RequestAuditBuffer.add() |

### 4.2 매핑 관계

BufferedEvent → AuditEntry 변환 시 매핑:

| AuditEventType | AuditAction |
|----------------|-------------|
| DLQ_STORE | DLQ_STORE |
| CB_STATE_CHANGE | CB_AUTO_OPEN / CB_FORCE_OPEN |
| ERROR_DETECTED | (custom) |
| API_EXCEPTION (신규) | API_ERROR (신규) |

---

## 5. 통합 포인트

### 5.1 RequestAuditBuffer.add() 시그니처

```python
def add(
    self,
    event_type: AuditEventType,
    source: str,
    details: dict = None,
    success: bool = True,
    error_message: str = None,
    actor_id: str = None,
) -> None:
```

### 5.2 AuditEntry 생성 시 자동 채우기

```python
@dataclass
class AuditEntry:
    # actor_id가 None이면 ActorContext에서 자동 추출
    def __post_init__(self):
        if self.actor_id is None:
            actor_id, actor_type, roles = _get_default_actor()
            # 자동 채우기
```

### 5.3 예외 핸들러 연동 예시

```python
# SelfHealingExceptionHandler 내부
buffer = RequestAuditBuffer.get_or_create(request)
buffer.add(
    event_type=AuditEventType.API_EXCEPTION,
    source="ExceptionHandler",
    details={
        "error_code": error_code,
        "exception_class": type(exc).__name__,
        "path": request.path,
        "method": request.method,
        "status_code": status_code,
    },
    success=False,
    error_message=str(exc)[:500],
    # actor_id는 ActorContext에서 자동 추출
)
```

---

## 6. 확장 필요 항목

### 6.1 AuditEventType 추가

```python
# audit/event_buffer.py에 추가
class AuditEventType(Enum):
    # ... 기존 ...
    
    # API Exception 관련
    API_EXCEPTION = "api_exception"
    API_VALIDATION_ERROR = "api_validation_error"
    API_AUTH_ERROR = "api_auth_error"
```

### 6.2 AuditAction 추가 (선택)

```python
# interfaces/audit_adapter.py에 추가
class AuditAction(str, Enum):
    # ... 기존 ...
    
    # API 관련
    API_ERROR = "api_error"
    VALIDATION_FAILED = "validation_failed"
```

### 6.3 중복 방지 로직

```python
# audit_middleware.py의 _capture_response_meta에 추가
def _capture_response_meta(self, request, response, buffer):
    # ExceptionHandler가 이미 기록했으면 스킵
    if buffer.has_event_from_source("ExceptionHandler"):
        return
    
    # 기존 ERROR_DETECTED 로직
    if status_code >= 400:
        buffer.add(event_type=AuditEventType.ERROR_DETECTED, ...)
```

# 110. Exception Handler 통합 설계 Overview

## 📚 문서 읽기 순서 및 구현 가이드

### 문서 구조

| 순서 | 문서 | 목적 | 읽기 대상 |
|------|------|------|-----------|
| 1 | **110 (본 문서)** | 전체 개요 및 구현 로드맵 | 모든 개발자 |
| 2 | 113_EXCEPTION_PATTERN_INVENTORY.md | 현재 문제점 상세 분석 | 이해 필요 시 |
| 3 | 114_AUDIT_SYSTEM_STRUCTURE.md | Audit 시스템 구조 파악 | 이해 필요 시 |
| 4 | **112_EXCEPTION_HANDLER_IMPLEMENTATION.md** | 구현 상세 가이드 | 구현 시 참조 |
| 5 | 111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md | Audit 연동 상세 | 구현 시 참조 |
| 6 | 115_EXCEPTION_HANDLER_CHECKLIST.md | 완료 검증 체크리스트 | 검증 시 |

### 🚀 구현 로드맵

```
┌─────────────────────────────────────────────────────────────────┐
│ Step 1: 기반 구조 생성 (api/django/exceptions/)                  │
│   ├── codes.py        → 에러 코드 enum 정의                     │
│   ├── classifier.py   → 예외 분류 로직                          │
│   ├── response.py     → 표준 응답 포맷                          │
│   └── handler.py      → DRF 예외 핸들러                         │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 2: Audit 연동 (audit/event_buffer.py)                       │
│   ├── AuditEventType에 API_EXCEPTION 추가                       │
│   └── handler.py에서 RequestAuditBuffer.add() 호출              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 3: 중복 방지 (audit_middleware.py)                          │
│   └── ExceptionHandler 이벤트 있으면 ERROR_DETECTED 스킵        │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Step 4: 테스트 및 검증                                           │
│   ├── 단위 테스트 작성                                          │
│   └── 통합 테스트 (AuditMiddleware 연동)                        │
└─────────────────────────────────────────────────────────────────┘
```

### 📋 Step별 상세 구현 가이드

#### Step 1: 기반 구조 생성

**1-1. codes.py 생성**
- 참조: 112 문서 "2. 에러 코드 체계"
- 작업: ErrorCode enum, HTTP 상태 코드 매핑

**1-2. classifier.py 생성**
- 참조: 112 문서 "4. 예외 분류기 설계"
- 작업: DRF/Django/커스텀 예외 분류 로직

**1-3. response.py 생성**
- 참조: 112 문서 "3. 표준 응답 포맷"
- 작업: StandardErrorResponse 데이터 클래스

**1-4. handler.py 생성**
- 참조: 112 문서 "5. DRF 통합"
- 작업: selfhealing_exception_handler 함수

#### Step 2: Audit 연동

**2-1. AuditEventType 확장**
- 참조: 111 문서 "2.1 신규 AuditEventType 추가"
- 파일: `audit/event_buffer.py`
- 작업: API_EXCEPTION, API_VALIDATION_ERROR 추가

**2-2. handler.py에서 버퍼 적재**
- 참조: 111 문서 "2.3 예외 핸들러 → Audit 버퍼 연동 흐름"
- 작업: RequestAuditBuffer.add() 호출 로직 추가

#### Step 3: 중복 방지

**3-1. RequestAuditBuffer 확장**
- 참조: 111 문서 "3.3 중복 기록 방지 전략"
- 파일: `audit/event_buffer.py`
- 작업: has_event_from_source() 메서드 추가

**3-2. AuditMiddleware 수정**
- 참조: 111 문서 "3.3 중복 기록 방지 전략"
- 파일: `api/django/audit_middleware.py`
- 작업: _capture_response_meta에서 중복 체크

#### Step 4: 테스트

- 참조: 115 문서 "Phase 3: 설정 및 테스트"
- 작업: 단위/통합 테스트 작성

---

## 1. 문제 정의

### 1.1 현재 상태: 예외 처리 패턴 불일치

views 폴더 분석 결과, **6가지 이상의 상이한 예외 응답 포맷**이 혼용되고 있음.

| 패턴 | 응답 구조 | 사용 영역 |
|------|-----------|-----------|
| A | `{"error": str(e)}` | finops, rollback, learning, compliance_dna, blast_radius |
| B | `{"status": "error", "error": str(e)}` | canary, health, tiering, l2_storage_config, dashboard, drift_threshold, dlq, governance/ |
| C | `{"success": False, "error": str(e), "message": "..."}` | cascade, emergency, system_control |
| D | `{"error": "...", "detail": str(e)}` | health 일부 |
| E | `{"reconciliation_result": "failed", "error": str(e)}` | governance/control_views |
| F | `{"status": "error", "error": "code", "message": str(e)}` | xtest/observability |

### 1.2 추가 불일치 항목

| 문제 | 설명 |
|------|------|
| HTTP 상태 코드 | 동일 예외에 400 vs 500 혼용 |
| error_type 필드 | 일부 뷰만 포함 (canary의 ConfigLockError) |
| success vs status | 불리언 vs 문자열 혼용 |
| 다국어 메시지 | 한국어/영어 혼합 사용 |
| 로깅 일관성 | 일부만 logger.exception 사용 |

### 1.3 Audit 시스템과의 연계 필요성

현재 예외 발생 시:
- 각 뷰에서 개별적으로 Response 반환
- Audit 로깅이 일부 뷰에서만 수동 호출
- AuditMiddleware는 4xx/5xx 응답만 감지 (상세 예외 정보 누락)

**목표**: 예외 발생 시 자동으로 Audit 로그 기록 + 표준화된 응답 반환

---

## 2. 솔루션 아키텍처

### 2.1 전체 흐름

```
Request
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ Django Middleware Chain                                  │
│  ┌───────────────────┐                                  │
│  │ AuditMiddleware   │ (가장 마지막 - 버퍼 수집)        │
│  └───────────────────┘                                  │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ DRF View Processing                                      │
│  ┌───────────────────┐                                  │
│  │ View.dispatch()   │                                  │
│  │     │             │                                  │
│  │     ▼             │                                  │
│  │ 예외 발생!        │                                  │
│  │     │             │                                  │
│  │     ▼             │                                  │
│  │ DRF Exception     │ ◄── 커스텀 핸들러 연결점         │
│  │ Handler           │                                  │
│  └───────────────────┘                                  │
└─────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────┐
│ SelfHealingExceptionHandler                              │
│  1. 예외 분류 (ExceptionClassifier)                     │
│  2. 표준 응답 생성 (StandardErrorResponse)              │
│  3. Audit 버퍼에 이벤트 적재 (RequestAuditBuffer)       │
│  4. 메트릭 업데이트 (선택)                              │
└─────────────────────────────────────────────────────────┘
    │
    ▼
Response (표준화된 에러 포맷)
```

### 2.2 핵심 컴포넌트

| 컴포넌트 | 책임 | 위치 |
|----------|------|------|
| SelfHealingExceptionHandler | DRF 예외 핸들러 진입점 | api/django/exception_handler.py |
| ExceptionClassifier | 예외 유형 분류 | api/django/exceptions/classifier.py |
| StandardErrorResponse | 표준 응답 포맷 생성 | api/django/exceptions/response.py |
| AuditEventType.API_EXCEPTION | 예외 이벤트 타입 | audit/event_buffer.py |

---

## 3. 구현 범위

### Phase 1: 기반 구조 (필수)
- 표준 에러 응답 포맷 정의
- DRF 커스텀 예외 핸들러 구현
- RequestAuditBuffer 연동

### Phase 2: 예외 분류 (권장)
- 비즈니스 예외 vs 시스템 예외 분류
- 재시도 가능 여부 판단
- 에러 코드 체계 정립

### Phase 3: 고급 기능 (선택)
- 클라이언트별 응답 포맷 (Accept-Language 기반)
- Rate Limit 기반 에러 응답 압축
- 실시간 에러 집계 대시보드 연동

---

## 4. 관련 문서

| 문서 | 내용 |
|------|------|
| 111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md | Audit 시스템 연동 상세 |
| 112_EXCEPTION_HANDLER_IMPLEMENTATION.md | 구현 가이드 |
| 56_AUDIT_MIDDLEWARE_DESIGN.md | AuditMiddleware 설계 |
| 85_AUDIT_INTEGRATION_OVERVIEW.md | Audit 통합 개요 |

---

## 5. 의존성 분석

### 5.1 기존 Audit 시스템 구조

```
selfhealing/
├── interfaces/
│   └── audit_adapter.py          # AuditEntry, AuditAction, ContextType
├── audit/
│   ├── event_buffer.py           # RequestAuditBuffer, AuditEventType
│   ├── logger.py                 # AuditLogger, ConfigChangeEvent
│   ├── self_audit.py             # SelfAuditLogger
│   └── audit_integration.py      # IntegratedAuditRecorder
├── api/django/
│   └── audit_middleware.py       # AuditMiddleware
└── services/audit/
    └── base.py                   # WAL 기반 감사
```

### 5.2 활용해야 할 기존 구성 요소

| 구성 요소 | 역할 | 활용 방안 |
|-----------|------|-----------|
| RequestAuditBuffer | 요청별 이벤트 버퍼 | 예외 이벤트 적재 |
| AuditEventType | 이벤트 유형 enum | API_EXCEPTION 추가 |
| ContextType.REQUEST | 컨텍스트 구분 | 예외가 요청 처리 중 발생함 표시 |
| AuditMiddleware | 버퍼 수집 및 기록 | 예외 이벤트 포함하여 기록 |
| ActorContext | 사용자 정보 | 예외 발생 시점의 actor 정보 |

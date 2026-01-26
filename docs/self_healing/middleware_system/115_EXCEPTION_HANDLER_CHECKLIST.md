# 115. Exception Handler 구현 체크리스트

## Phase 1: 기반 구조 (필수)

### 1.1 파일 생성

- [x] `api/django/exceptions/__init__.py`
- [x] `api/django/exceptions/codes.py` - 에러 코드 정의
- [x] `api/django/exceptions/classifier.py` - 예외 분류기
- [x] `api/django/exceptions/response.py` - 표준 응답 생성
- [x] `api/django/exceptions/handler.py` - DRF 예외 핸들러

### 1.2 에러 코드 정의 (codes.py)

- [x] ErrorCode enum 정의
  - [x] VALIDATION_* 카테고리
  - [x] AUTH_* 카테고리
  - [x] AUTHZ_* 카테고리
  - [x] RESOURCE_* 카테고리
  - [x] RATE_* 카테고리
  - [x] CONFIG_* 카테고리
  - [x] SYSTEM_* 카테고리
  - [x] SERVICE_* 카테고리
- [x] 에러 코드별 기본 메시지 매핑
- [x] 에러 코드별 HTTP 상태 코드 매핑

### 1.3 예외 분류기 (classifier.py)

- [x] ExceptionClassifier 클래스
- [x] DRF 예외 분류
  - [x] ValidationError
  - [x] AuthenticationFailed
  - [x] NotAuthenticated
  - [x] PermissionDenied
  - [x] NotFound
  - [x] Throttled
- [x] Django 예외 분류
  - [x] Http404
  - [x] PermissionDenied
- [x] selfhealing 커스텀 예외 분류
  - [x] ConfigLockError
  - [x] AutomationBlockedError
- [x] Python 기본 예외 분류
  - [x] ValueError
  - [x] KeyError
  - [x] TypeError
- [x] 재시도 가능 여부 판단 로직

### 1.4 표준 응답 생성 (response.py)

- [x] StandardErrorResponse 데이터 클래스
- [x] 응답 포맷 생성 함수
- [ ] 다국어 메시지 지원 (선택)
- [ ] 민감정보 마스킹 연동

### 1.5 DRF 예외 핸들러 (handler.py)

- [x] selfhealing_exception_handler 함수
- [x] DRF 기본 핸들러 호출
- [x] 비-DRF 예외 처리
- [x] 표준 응답으로 변환
- [x] Audit 버퍼 연동

---

## Phase 2: Audit 연동

### 2.1 AuditEventType 확장

- [x] `audit/event_buffer.py` 수정
  - [x] API_EXCEPTION 추가
  - [x] API_VALIDATION_ERROR 추가
  - [x] API_AUTH_ERROR 추가

### 2.2 예외 핸들러에서 버퍼 적재

- [x] RequestAuditBuffer.get_or_create() 호출
- [x] buffer.add() 호출
  - [x] event_type 설정
  - [x] source="ExceptionHandler" 설정
  - [x] details 구성
  - [x] success=False 설정
  - [x] error_message 설정

### 2.3 중복 기록 방지

- [x] RequestAuditBuffer에 has_event_from_source() 메서드 추가
- [x] AuditMiddleware._capture_response_meta() 수정
  - [x] ExceptionHandler 이벤트 확인
  - [x] 있으면 ERROR_DETECTED 스킵

---

## Phase 3: 설정 및 테스트

### 3.1 DRF 설정

- [x] settings.py에 EXCEPTION_HANDLER 설정 가이드 문서화
- [x] 테스트 프로젝트에서 설정 검증

### 3.2 단위 테스트

- [x] tests/api/exceptions/ 폴더 생성
- [x] test_codes.py
- [x] test_classifier.py
- [x] test_response.py
- [x] test_handler.py

### 3.3 통합 테스트

- [x] AuditMiddleware 연동 테스트
- [ ] 해시 체인 포함 검증
- [x] 중복 기록 방지 검증

---

## Phase 4: 마이그레이션 (선택)

### 4.1 기존 뷰 리팩토링 가이드

- [ ] 패턴별 마이그레이션 예시 문서
- [ ] 점진적 적용 전략

### 4.2 하위 호환성

- [ ] 기존 try-except 패턴 지원 검증
- [ ] 커스텀 응답 필요 시 가이드

---

## 검증 기준

### 기능 검증

- [x] 모든 예외 유형이 분류되는가?
- [x] 표준 응답 포맷이 생성되는가?
- [x] Audit 버퍼에 이벤트가 적재되는가?
- [x] AuditMiddleware가 이벤트를 수집하는가?

### 성능 검증

- [ ] 예외 처리 오버헤드 < 1ms
- [ ] 메모리 증가 최소화

### 안정성 검증

- [x] 예외 핸들러 자체 에러 시 폴백 동작
- [x] Audit 실패 시 응답은 정상 반환

---

## 참고 문서

| 문서 | 내용 |
|------|------|
| 110_EXCEPTION_HANDLER_OVERVIEW.md | 전체 개요 |
| 111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md | Audit 연동 상세 |
| 112_EXCEPTION_HANDLER_IMPLEMENTATION.md | 구현 가이드 |
| 113_EXCEPTION_PATTERN_INVENTORY.md | 기존 패턴 분석 |
| 114_AUDIT_SYSTEM_STRUCTURE.md | Audit 시스템 구조 |

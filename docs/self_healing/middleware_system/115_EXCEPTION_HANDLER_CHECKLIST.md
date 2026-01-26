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
- [x] 민감정보 마스킹 연동 (`_mask_error_message` 함수 구현)

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
  - [x] API_NOT_FOUND 추가
  - [x] API_THROTTLED 추가

### 2.1.1 AuditAction 확장

- [x] `interfaces/audit_adapter.py` 수정
  - [x] API_ERROR 추가
  - [x] VALIDATION_FAILED 추가
  - [x] AUTHORIZATION_DENIED 추가

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
- [x] settings/base.py에 selfhealing_exception_handler 설정 적용

### 3.1.1 레거시 호환성

- [x] `api/django/exception_handler.py` 레거시 re-export 파일 생성

### 3.1.2 Prometheus 메트릭 연동

- [x] `_init_metrics()` 함수 구현 (Counter: exception_handler_errors_total)
- [x] `_record_metrics()` 함수 구현

### 3.1.3 Pool Timeout 처리

- [x] `_is_pool_timeout()` 함수 구현 (SQLAlchemy QueuePool, connection timeout 감지)
- [x] Pool Timeout 시 503 Service Unavailable 반환

### 3.2 단위 테스트

- [x] tests/api/exceptions/ 폴더 생성
- [x] test_codes.py
- [x] test_classifier.py
- [x] test_response.py
- [x] test_handler.py

### 3.3 통합 테스트

- [x] AuditMiddleware 연동 테스트
- [x] 해시 체인 포함 검증 (`test_exception_handler_hash_chain.py` - 15개 테스트)
- [x] 중복 기록 방지 검증

---

## Phase 4: 마이그레이션 [완료: 2026-01-26]

### 4.1 기존 뷰 리팩토링 가이드

- [x] 패턴별 마이그레이션 예시 문서
- [x] 점진적 적용 전략

### 4.2 마이그레이션 완료 내역

#### Phase 4.2.1 - 초기 마이그레이션 (2025-01-26)

총 **10개 try-except 패턴** 제거 (3개 파일):

| 파일 | 변경 수 | 변경 유형 |
|------|---------|-----------|
| `rollback.py` | 1 | RollbackExecuteView.post - try-except 제거 |
| `learning.py` | 1 | LearningSessionView.post - try-except 제거, ValueError/Http404 raise 패턴 적용 |
| `dlq.py` | 8 | DLQCleanupStatsView.get, DLQArchiveView.post, DLQPurgeView.post, DLQListView.get, DLQDetailView.get, DLQRetryView.post, DLQResolveView.post, DLQTestCreateView.post - try-except 제거 |

#### Phase 4.2.2 - 대규모 마이그레이션 (2026-01-26)

총 **51개 try-except 패턴** 제거 (13개 파일):

| 파일 | 변경 수 | 변경 유형 |
|------|---------|-----------|
| `canary.py` | 3 | stages parsing, rollout creation, action handler |
| `health.py` | 5 | Metrics GET, GateHealth GET, GateConfig GET/PUT, GateReset POST |
| `tiering.py` | 10 | TierDefinitions GET/PUT, TierMappings GET/PUT, TierOverrides GET/PUT, DryRun, Reset, Export, Import, Resolve |
| `cascade.py` | 7 | EventList, EventDetail, ChainVerify, CausationTrace, Checkpoint GET/POST, LoadShedding |
| `config.py` | 8 | AllConfig, ResetConfig, PendingChanges, CancelPendingChange, BaseConfig GET/PUT, SLOConfig PUT/DELETE |
| `emergency.py` | 1 | EmergencyConfigView PUT |
| `governance/config_views.py` | 4 | GovernanceConfig GET/PUT, L2StorageConfig GET/PUT |
| `governance/control_views.py` | 3 | Reconcile POST, Mode POST/GET |
| `governance/status_views.py` | 2 | MetricStatus GET, RBACStatus GET |
| `governance/approval_views.py` | 4 | ApprovalList GET/POST, Approve POST, Reject POST |
| `error_budget/reconciliation.py` | 12 | Status, FailSafePeriods, ShadowBudgets GET/POST, Detail, Approve, Reject, ExcludedPeriods GET/POST, ExcludedPeriodDetail DELETE, Config GET/PUT |
| `xtest/observability.py` | 3 | HealingTimeline GET, BlastRadius POST, MultiBlastRadius POST |
| `chaos/report_views.py` | 1 | DryRunAnalysis POST |

### 4.3 마이그레이션 제외 항목 (의도적 유지)

#### 4.3.1 부수적 작업 fallback (Audit 미기록 - 정상)

이 패턴들은 **핵심 비즈니스 로직이 아닌 부수적 작업**이므로 실패해도 API가 계속 작동해야 합니다:

| 위치 | 패턴 | 이유 | Audit 기록 |
|------|------|------|------------|
| `tiering.py: _log_change()` | 로깅 실패 시 warning만 | 로깅 실패가 API 실패로 이어지면 안 됨 | ❌ 미기록 |
| `config.py: put()` | 로그 포맷팅 실패 시 fallback | 로깅 실패가 API 실패로 이어지면 안 됨 | ❌ 미기록 |
| `xtest/observability.py: _get_timeline_default_limit()` | 설정 조회 실패 시 기본값 | 부수적 설정, 기본값으로 충분 | ❌ 미기록 |
| `circuit_breaker.py: AuditLogsView.get()` | Audit 조회 실패 시 빈 결과 | 읽기 전용, 실패해도 시스템 영향 없음 | ❌ 미기록 |
| `chaos/safety_views.py: KillAllView.post()` | TTL config clear 실패 | 클린업 실패는 치명적이지 않음 | ⚠️ 로그만 |

#### 4.3.2 모듈 로드 fallback

| 위치 | 패턴 | 이유 |
|------|------|------|
| `auto_tuning.py` | ImportError fallback | 모듈 로드 시 폴백 처리 |
| `finops.py` | ImportError fallback | 모듈 로드 시 폴백 처리 |
| `compliance_dna.py` | ImportError fallback | 모듈 로드 시 폴백 처리 |
| `blast_radius.py` | ImportError fallback | 모듈 로드 시 폴백 처리 |

### 4.4 하위 호환성

- [x] 기존 try-except 패턴 지원 검증
- [x] 커스텀 응답 필요 시 가이드

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

## 테스트 실행 결과

### 최종 테스트 (2025-01-26)

```
테스트 위치: tests/api/exceptions/
실행 명령: docker-compose -f docker-compose.test.yml run --rm test-global sh -c "python -m pytest tests/api/exceptions/ -v --tb=short --no-cov"
결과: 137 passed, 14 warnings
```

#### 테스트 파일별 커버리지

| 파일 | 테스트 수 | 상태 |
|------|-----------|------|
| test_exception_codes.py | 26개 | ✅ 통과 |
| test_exception_classifier.py | 24개 | ✅ 통과 |
| test_exception_response.py | 22개 | ✅ 통과 |
| test_exception_handler.py | 50개 | ✅ 통과 |
| test_exception_handler_hash_chain.py | 15개 | ✅ 통과 |

#### 주요 테스트 항목

- ErrorCode enum HTTP 상태 매핑 (400, 401, 403, 404, 409, 429, 500, 503, 504)
- 재시도 가능 여부 플래그 검증
- DRF 예외 분류 (ValidationError, AuthenticationFailed, NotAuthenticated, PermissionDenied, NotFound, Throttled, ParseError)
- Django 예외 분류 (Http404, PermissionDenied, ValidationError)
- 커스텀 예외 분류 (ConfigLockError, AutomationBlockedError)
- Python 예외 분류 (ValueError, TypeError, KeyError, TimeoutError, ConnectionError)
- 표준 응답 포맷 생성 및 직렬화
- Audit 버퍼 이벤트 적재 (API_EXCEPTION, API_VALIDATION_ERROR, API_AUTH_ERROR, API_NOT_FOUND, API_THROTTLED)
- 중복 기록 방지 (has_event_from_source 메서드)
- 예외 핸들러 실패 시 폴백 동작 (fail-open)
- **Pool Timeout 감지 및 503 응답 처리**
- **민감정보 마스킹 (password, token, api_key 등)**
- **Prometheus 메트릭 초기화 및 기록**
- **해시 체인 무결성 정보 포함 (sequence, previous_hash, current_hash)**
- **해시 체인 연결 검증 (previous_hash → current_hash 링크)**
- **변조 감지 (compute_hash 재계산으로 무결성 확인)**

---

## Phase 4 마이그레이션 가이드

### 마이그레이션 전 패턴 (제거됨)

```python
# ❌ 기존 패턴 - try-except로 수동 에러 응답
class SomeView(APIView):
    def post(self, request):
        try:
            # 비즈니스 로직
            return Response({"success": True})
        except Exception as e:
            return Response({"error": str(e)}, status=500)
```

### 마이그레이션 후 패턴

```python
# ✅ 새 패턴 - DRF 예외 핸들러가 처리
class SomeView(APIView):
    def post(self, request):
        # 비즈니스 로직
        return Response({"success": True})
        # 예외 발생 시 selfhealing_exception_handler가 자동 처리
```

### Serializer 검증 패턴

```python
# ❌ 기존 패턴
if not serializer.is_valid():
    return Response(serializer.errors, status=400)

# ✅ 새 패턴
serializer.is_valid(raise_exception=True)
# ValidationError가 발생하면 핸들러가 처리
```

---

## 참고 문서

| 문서 | 내용 |
|------|------|
| 110_EXCEPTION_HANDLER_OVERVIEW.md | 전체 개요 |
| 111_EXCEPTION_HANDLER_AUDIT_INTEGRATION.md | Audit 연동 상세 |
| 112_EXCEPTION_HANDLER_IMPLEMENTATION.md | 구현 가이드 |
| 113_EXCEPTION_PATTERN_INVENTORY.md | 기존 패턴 분석 |
| 114_AUDIT_SYSTEM_STRUCTURE.md | Audit 시스템 구조 |

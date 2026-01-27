# 136. Exception Handler 6대 보완 구현 설계

> **Version**: 1.0.0  
> **Created**: 2026-01-27  
> **Status**: Draft  
> **Parent**: [110_EXCEPTION_HANDLER_OVERVIEW.md](110_EXCEPTION_HANDLER_OVERVIEW.md), [135_EXCEPTION_HANDLER_ENHANCEMENT.md](135_EXCEPTION_HANDLER_ENHANCEMENT.md)

## 1. 개요

이 문서는 Exception Handler 시스템의 6가지 보완 사항 구현 설계를 다룹니다.

| 보완 | 설명 | 우선순위 |
|------|------|----------|
| **Q2** | RequestAuditBuffer MAX_EVENTS 제한 | HIGH |
| **Q3** | Audit 쓰기 비동기 오프로딩 | MEDIUM |
| **Q7** | hash_for_audit 연동 (ID-Preserving) | HIGH |
| **Q8** | ResponseMeta에 region 필드 추가 | LOW |
| **Q9** | Auto-Causation Propagation | MEDIUM |
| **Q12** | ClassifiedError에 budget_impact_weight 추가 | LOW |

---

## 2. 구현 순서

의존성과 우선순위를 고려한 구현 순서:

```
Phase 1: 기반 확장 (의존성 없음)
├── Q2: MAX_EVENTS_PER_REQUEST (독립)
├── Q7: hash_for_audit 연동 (독립)
└── Q8: region 필드 추가 (독립)

Phase 2: 컨텍스트 통합 (Phase 1 완료 후)
└── Q9: Auto-Causation Propagation (signal_hooks.py 수정)

Phase 3: 성능 개선 (Phase 1, 2 완료 후)
└── Q3: Async Offloading (WAL 이후 DB 저장 비동기화)

Phase 4: Budget 연동 (독립, 장기)
└── Q12: budget_impact_weight (별도 매핑 테이블 권장)
```

---

## 3. Phase 1: 기반 확장

### 3.1 Q2: MAX_EVENTS_PER_REQUEST 제한

**목적**: 단일 요청에서 무제한 이벤트 누적으로 인한 메모리 폭발 방지

**현재 상태 분석**:
- 위치: `audit/event_buffer.py#L275-400`
- `RequestAuditBuffer` 클래스에 이벤트 개수 제한 없음
- `add_event()` 메서드가 무조건 `self.events.append()` 호출

**관련 참조 코드**:
- `cascade_config.py#L256`: `max_events_per_second = 1000` (초당 제한 참조)
- 설정 패턴: `settings/error_budget.py`의 `ErrorBudgetSettings` 구조 참조

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `audit/event_buffer.py` | `RequestAuditBuffer.__init__` | `max_events: int = 100` 인스턴스 변수 추가 |
| `audit/event_buffer.py` | `RequestAuditBuffer.add_event` | 이벤트 추가 전 `len(self.events) >= self.max_events` 검사 |
| `audit/event_buffer.py` | `RequestAuditBuffer.add` | 동일 검사 추가 |
| `settings/audit.py` | 설정 섹션 | `MAX_EVENTS_PER_REQUEST = 100` 환경변수 지원 |

**동작 정의**:
- 한도 초과 시 `_truncated_count` 카운터 증가
- 마지막 이벤트에 `truncated: true`, `truncated_count: N` 메타데이터 추가
- 로그 경고 출력

---

### 3.2 Q7: hash_for_audit 연동 (ID-Preserving Masking)

**목적**: Audit 로그에서 민감정보를 해시로 마스킹하여 동일성 확인 가능하게 함

**현재 상태 분석**:
- 위치: `audit/masking.py#L89-116`
- `hash_for_audit(value: str, salt: Optional[str] = None) -> str` 함수 존재
- 반환 형식: `"sha256:{hash[:16]}"`
- **예외 핸들러에서 사용되지 않음**

**관련 참조 코드**:
- `handler.py#L396-426`: `_mask_error_message()` - 현재 단순 `[MASKED]` 치환
- RBAC 역할: `actor_context.py#L52-57`의 `RBAC_ROLE_PRIORITY`

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `audit/masking.py` | 신규 추가 | `MaskingLevel` Enum 정의 (CLIENT, AUDIT, FORENSIC) |
| `audit/masking.py` | 신규 추가 | `get_masking_level_for_context()` - RBAC 역할 기반 레벨 결정 |
| `audit/masking.py` | 신규 추가 | `mask_with_level(value, level, salt)` - 레벨별 마스킹 적용 |
| `handler.py` | `_mask_error_message()` | `MaskingLevel.CLIENT` 적용 (클라이언트 응답) |
| `handler.py` | `_record_audit_event()` | `MaskingLevel.AUDIT` 적용 (hash_for_audit 사용) |

**MaskingLevel 정의**:

| 레벨 | 사용 위치 | 마스킹 방식 |
|------|----------|------------|
| `CLIENT` | API 응답 | `***REDACTED***` 완전 치환 |
| `AUDIT` | 내부 Audit 로그 | `hash_for_audit()` - SHA-256 해시 (16자) |
| `FORENSIC` | 법적 조사용 | 암호화 저장 (별도 스토리지) |

**RBAC 연동 규칙**:
- `ActorContext.get_current().roles` 조회
- `selfhealing_admin` → FORENSIC 접근 가능
- `selfhealing_operator` → AUDIT 레벨까지
- `selfhealing_viewer` → CLIENT 레벨만

---

### 3.3 Q8: ResponseMeta에 region 필드 추가

**목적**: 멀티 리전 환경에서 에러 발생 리전 식별

**현재 상태 분석**:
- 위치: `response.py#L76-112`
- `ResponseMeta` 데이터클래스: `request_id`, `timestamp`, `path`, `method` 필드
- **region 필드 없음**

**관련 참조 코드**:
- `cluster_identity.py#L164`: `SELFHEALING_REGION` 환경변수 사용
- `cluster_identity.py#L128-140`: `get_cluster_identity()` 함수

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `response.py` | `ResponseMeta` 데이터클래스 | `region: Optional[str] = None` 필드 추가 |
| `response.py` | `ResponseMeta.to_dict()` | region 직렬화 추가 |
| `response.py` | `StandardErrorResponse.create()` | `cluster_identity.get_region()` 호출하여 region 설정 |
| `response.py` | 모듈 임포트 | `from selfhealing.core.cluster_identity import get_cluster_identity` |

**동작 정의**:
- 환경변수 `SELFHEALING_REGION` 값 사용
- 미설정 시 `None` (응답에서 생략)

---

## 4. Phase 2: 컨텍스트 통합

### 4.1 Q9: Auto-Causation Propagation

**목적**: Celery Task 호출 시 causation_id 자동 전파

**현재 상태 분석**:
- 위치: `context/causation_context.py#L337-403`
- `get_causation_for_celery()` 함수 존재 - Celery 헤더 생성
- `restore_causation_from_celery()` 함수 존재 - 헤더에서 복원
- **수동 호출 필요** - 자동 전파 안 됨

**Celery 시그널 핸들러 존재**:
- 위치: `adapters/celery/signal_hooks.py#L43-49`
- `task_prerun`, `task_postrun` 시그널 핸들러 존재
- **`before_task_publish` 시그널 핸들러 없음**

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `adapters/celery/signal_hooks.py` | 시그널 임포트 | `from celery.signals import before_task_publish` 추가 |
| `adapters/celery/signal_hooks.py` | 신규 함수 | `on_before_task_publish()` - causation 헤더 자동 주입 |
| `adapters/celery/signal_hooks.py` | `on_task_prerun()` | `restore_causation_from_celery()` 호출 추가 |
| `adapters/celery/signal_hooks.py` | `on_task_postrun()` | `CausationContext.clear()` 호출 추가 |
| `adapters/celery/signal_hooks.py` | `setup_selfhealing_signals()` | `before_task_publish.connect()` 등록 |

**동작 정의**:
- `before_task_publish`: `CausationContext.is_set()` 확인 후 `get_causation_for_celery()` 호출
- 헤더 키: `CELERY_HEADER_CASCADE_ID`, `CELERY_HEADER_PARENT_EVENT` 등 (causation_context.py#L52-58)
- `task_prerun`: 헤더에서 causation 복원, `chain_depth` 증가
- `task_postrun`: causation 정리

---

## 5. Phase 3: 성능 개선

### 5.1 Q3: Audit 쓰기 비동기 오프로딩

**목적**: Audit DB 쓰기를 비동기로 처리하여 API 응답 지연 최소화

**현재 상태 분석**:
- WAL 쓰기: 동기 (필수 - Zero-Loss 보장)
- PostgreSQL 쓰기: `ContinuousAuditRecorder`가 백그라운드 스레드에서 처리

**관련 참조 코드**:
- `audit/wal.py#L89-145`: `write_entry()` - 동기 WAL 쓰기
- `audit/recorder.py`: `ContinuousAuditRecorder` - 백그라운드 DB 저장

**현재 구조**:
```
요청 → ExceptionHandler → WAL 쓰기(동기) → 응답 반환
                              ↓
                   ContinuousAuditRecorder(비동기)
                              ↓
                         PostgreSQL
```

**분석 결과**:
- **DB 쓰기는 이미 비동기** (`ContinuousAuditRecorder`)
- WAL 쓰기는 Zero-Loss 보장을 위해 **동기 유지 필수**
- 추가 최적화 여지: WAL 배치 쓰기 (현재 단건 쓰기)

**수정 파일 및 위치** (선택적):

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `audit/wal.py` | `WALWriter` 클래스 | `batch_write_entries()` 메서드 추가 (배치 쓰기) |
| `audit/middleware.py` | 요청 완료 시 | 배치 쓰기 호출 (개별 이벤트 대신) |

**주의사항**:
- WAL 쓰기는 fsync 포함하여 **동기 유지**
- 배치 사이즈 제한: `MAX_EVENTS_PER_REQUEST` (Q2)와 연동

---

## 6. Phase 4: Budget 연동

### 6.1 Q12: budget_impact_weight 추가

**목적**: 예외 유형별 Error Budget 소모 가중치 정의

**현재 상태 분석**:
- 위치: `classifier.py#L56-89`
- `ClassifiedError` 데이터클래스에 `category`, `code`, `http_status` 등 존재
- **`budget_impact_weight` 또는 `multiplier_weight` 필드 없음**

**관련 참조 코드**:
- `services/error_budget/multiplier.py#L273-323`: `CrisisMultiplierProvider.get_current_multiplier()`
- `services/error_budget/reconciliation/shadow_calculator.py#L28-75`: 심각도별 가중치 정의

**설계 선택지**:

| 옵션 | 장점 | 단점 |
|------|------|------|
| **A**: `ClassifiedError`에 필드 추가 | 단순 | 분류기와 Budget 결합도 증가 |
| **B**: 별도 매핑 테이블 | 느슨한 결합 | 추가 조회 필요 |
| **C**: 설정 파일 기반 | 런타임 변경 가능 | 코드 외부 의존 |

**권장: 옵션 B (별도 매핑 테이블)**

**근거**:
- `shadow_calculator.py#L28-75`의 심각도별 가중치가 이미 분리됨
- `ErrorCode` → `budget_weight` 매핑은 운영 정책이지 예외 분류가 아님
- 런타임에 가중치 조정 가능해야 함 (정책 변경)

**수정 파일 및 위치**:

| 파일 | 수정 위치 | 변경 내용 |
|------|----------|----------|
| `services/error_budget/exception_weights.py` | 신규 파일 | `ExceptionBudgetWeightMap` 클래스 정의 |
| `services/error_budget/exception_weights.py` | 신규 | `ErrorCode` → `weight` 매핑 딕셔너리 |
| `services/error_budget/exception_weights.py` | 신규 | `get_weight_for_error_code(code: ErrorCode)` 함수 |
| `settings/error_budget.py` | 설정 추가 | `EXCEPTION_BUDGET_WEIGHTS` 환경변수 지원 |

**기본 가중치 매핑**:

| ErrorCode 카테고리 | 가중치 | 근거 |
|-------------------|--------|------|
| `SYSTEM_*` (내부 오류) | 1.0 | 시스템 결함 - 최대 영향 |
| `SERVICE_*` (외부 서비스) | 0.5 | 부분 책임 |
| `VALIDATION_*` | 0.1 | 클라이언트 오류 |
| `AUTH_*` | 0.2 | 보안 관련 |
| `RESOURCE_*` | 0.3 | 리소스 문제 |

---

## 7. 구현 체크리스트

### Phase 1 (기반 확장) - 1주차

- [ ] **Q2: MAX_EVENTS_PER_REQUEST**
  - [ ] `event_buffer.py`: `max_events` 인스턴스 변수 추가
  - [ ] `event_buffer.py`: `add_event()` 제한 로직 추가
  - [ ] `event_buffer.py`: `add()` 제한 로직 추가
  - [ ] `event_buffer.py`: `_truncated_count` 카운터 추가
  - [ ] 테스트: `test_event_buffer_max_events.py`

- [ ] **Q7: hash_for_audit 연동**
  - [ ] `masking.py`: `MaskingLevel` Enum 추가
  - [ ] `masking.py`: `get_masking_level_for_context()` 함수 추가
  - [ ] `masking.py`: `mask_with_level()` 함수 추가
  - [ ] `handler.py`: `_mask_error_message()` 리팩토링
  - [ ] `handler.py`: Audit 기록 시 `MaskingLevel.AUDIT` 적용
  - [ ] 테스트: `test_role_based_masking.py`

- [ ] **Q8: region 필드 추가**
  - [ ] `response.py`: `ResponseMeta.region` 필드 추가
  - [ ] `response.py`: `to_dict()` 수정
  - [ ] `response.py`: `create()` 메서드에서 region 설정
  - [ ] 테스트: `test_response_meta_region.py`

### Phase 2 (컨텍스트 통합) - 2주차

- [ ] **Q9: Auto-Causation Propagation**
  - [ ] `signal_hooks.py`: `before_task_publish` 시그널 임포트
  - [ ] `signal_hooks.py`: `on_before_task_publish()` 핸들러 구현
  - [ ] `signal_hooks.py`: `on_task_prerun()` causation 복원 추가
  - [ ] `signal_hooks.py`: `on_task_postrun()` causation 정리 추가
  - [ ] `signal_hooks.py`: `setup_selfhealing_signals()` 등록 추가
  - [ ] 테스트: `test_celery_causation_propagation.py`

### Phase 3 (성능 개선) - 3주차

- [ ] **Q3: Audit 배치 쓰기** (선택적)
  - [ ] `wal.py`: `batch_write_entries()` 메서드 추가
  - [ ] 성능 벤치마크 수행
  - [ ] 테스트: `test_wal_batch_write.py`

### Phase 4 (Budget 연동) - 4주차

- [ ] **Q12: budget_impact_weight**
  - [ ] `exception_weights.py` 신규 파일 생성
  - [ ] `ExceptionBudgetWeightMap` 클래스 구현
  - [ ] `get_weight_for_error_code()` 함수 구현
  - [ ] `settings/error_budget.py` 설정 추가
  - [ ] 테스트: `test_exception_budget_weights.py`

---

## 8. 테스트 전략

### 8.1 단위 테스트

| 보완 | 테스트 파일 | 검증 항목 |
|------|-----------|----------|
| Q2 | `test_event_buffer_max_events.py` | 100개 초과 시 truncation |
| Q7 | `test_role_based_masking.py` | 레벨별 마스킹 출력 검증 |
| Q8 | `test_response_meta_region.py` | region 필드 직렬화 |
| Q9 | `test_celery_causation_propagation.py` | 헤더 자동 주입/복원 |
| Q12 | `test_exception_budget_weights.py` | ErrorCode별 가중치 조회 |

### 8.2 통합 테스트

| 시나리오 | 검증 항목 |
|----------|----------|
| API 요청 → 예외 → Audit 기록 | hash_for_audit 적용 확인 |
| API 요청 → Celery Task → 완료 | causation_id 전파 확인 |
| 대량 이벤트 발생 요청 | MAX_EVENTS 제한 동작 |

---

## 9. 참조 문서

| 문서 | 관련 내용 |
|------|----------|
| [91_CONFIG_INVENTORY.md](91_CONFIG_INVENTORY.md) | RBAC 역할 우선순위, 설정 상수 |
| [110_EXCEPTION_HANDLER_OVERVIEW.md](110_EXCEPTION_HANDLER_OVERVIEW.md) | 예외 핸들러 아키텍처 |
| [135_EXCEPTION_HANDLER_ENHANCEMENT.md](135_EXCEPTION_HANDLER_ENHANCEMENT.md) | Causation Chain, Role-based Masking 설계 |
| [76_CASCADE_EVENT_AUDIT.md](76_CASCADE_EVENT_AUDIT.md) | Causation Chain 구조 |
| [75_CRISIS_MULTIPLIER.md](75_CRISIS_MULTIPLIER.md) | CrisisMultiplierProvider 설계 |

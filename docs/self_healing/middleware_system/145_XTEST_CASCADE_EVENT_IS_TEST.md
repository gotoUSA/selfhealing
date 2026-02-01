# X-Test CascadeEvent.is_test Field

**문서 번호:** 145
**작성일:** 2026-01-27
**상태:** 구현 완료
**선행 문서:** 144_XTEST_CROSS_REGION_SCENARIO.md, 76_CASCADE_EVENT.md
**최종 수정:** 2026-02-01

---

## 구현 완료 현황

| Step | 항목 | 파일 | 상태 |
|------|------|------|------|
| 1 | Dataclass `is_test` 필드 | `audit/cascade_event.py` | ✅ |
| 2 | Django Model `is_test` BooleanField | `models/cascade_event_archive.py` | ✅ |
| 3 | 복합 인덱스 `idx_cascade_test_ts` | `models/cascade_event_archive.py` | ✅ |
| 4-1 | `to_dict()` is_test 포함 | `audit/cascade_event.py` | ✅ |
| 4-2 | `from_dict()` is_test 파싱 | `audit/cascade_event.py` | ✅ |
| 4-3 | `from_dataclass()` is_test 전달 | `models/cascade_event_archive.py` | ✅ |
| 5 | `TestModeContext.is_synthetic()` 연동 | `audit/cascade_auditor.py` | ✅ |
| 6 | 마이그레이션 생성 | `selfhealing/adapters/django/migrations/0001_initial.py` | ✅ |
| 8 (Sec 6) | API 응답 `is_test` 포함 | `api/django/views/cascade.py` | ✅ |
| 8 (Sec 6) | API `?is_test` 필터 | `api/django/views/cascade.py` | ✅ |

### 마이그레이션 아키텍처 변경 (2026-02-01)

selfhealing 패키지가 자체 마이그레이션을 관리하도록 리팩토링되었습니다:

| 구분 | 이전 | 현재 |
|------|------|------|
| 마이그레이션 위치 | `shopping/migrations/` | `selfhealing/adapters/django/migrations/` |
| 포함 내용 | - | RBAC 그룹, PostmortemRecord, CascadeEventArchive (is_test 포함) |
| 재사용성 | 다른 시스템 사용 시 DB 수정 필요 | `INSTALLED_APPS`에 추가만으로 사용 가능 |

**사용법:**
```python
# settings.py
INSTALLED_APPS = [
    ...
    "selfhealing.adapters.django",  # DB 마이그레이션 자동 포함
]
```

---

## 1. 목적

CascadeEvent 모델에 `is_test` 필드를 명시적으로 추가하여, 대시보드에서 테스트 데이터를 쉽게 필터링하고 시각적으로 구분할 수 있게 한다.

### 1.1 현재 문제

| 문제 | 현재 상태 |
|------|----------|
| 직접 필터 필드 없음 | 메타데이터 조회 필요 |
| 인덱스 없음 | 테스트 데이터 쿼리 비효율 |
| UI 구분 어려움 | 별도 로직 필요 |

### 1.2 목표

| 항목 | 목표 |
|------|------|
| 필드 추가 | `is_test: bool` |
| 인덱스 추가 | `(is_test, -timestamp)` 복합 인덱스 |
| 필터링 | 클릭 한 번으로 테스트 데이터 숨기기 |

---

## 2. 현재 상태 분석

### 2.1 CascadeEvent 모델 위치

| 파일 | 유형 | 용도 |
|------|------|------|
| `models/cascade_event.py` | Dataclass | 런타임 데이터 모델 (Redis) |
| `models/cascade_event.py` | Django Model | PostgreSQL 영속 저장 |

### 2.2 Dataclass 현재 필드

| 필드 | 타입 | 설명 |
|------|------|------|
| `id` | str | 이벤트 ID |
| `trigger` | CascadeTrigger | 트리거 정보 |
| `effects` | List[CascadeEffect] | 효과 목록 |
| `namespace` | str | 네임스페이스 |
| `timestamp` | str | 생성 시간 |
| `previous_hash` | Optional[str] | 이전 해시 |
| `current_hash` | Optional[str] | 현재 해시 |
| `external_trace` | Optional[ExternalTraceContext] | 외부 추적 |
| `version` | str | 버전 |

### 2.3 Django Model 현재 필드

| 필드 | db_index |
|------|----------|
| `namespace` | ✅ |
| `timestamp` | ✅ |
| `trigger_type` | ✅ |
| `current_hash` | ✅ |
| `previous_hash` | ✅ |

### 2.4 기존 BooleanField 패턴

| 파일 | 필드 | 용도 |
|------|------|------|
| `models/recovery_models.py` | `requires_approval` | 승인 필요 여부 |
| `models/cascade_event.py` | - | BooleanField 없음 |

---

## 3. 설계

### 3.1 Dataclass 필드 추가

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `is_test` | bool | False | 테스트 환경 이벤트 여부 |

### 3.2 Django Model 필드 추가

| 필드 | 타입 | 옵션 |
|------|------|------|
| `is_test` | BooleanField | `default=False`, `db_index=True` |

### 3.3 복합 인덱스 추가

| 인덱스 | 필드 | 용도 |
|--------|------|------|
| `idx_cascade_test_ts` | `(is_test, -timestamp)` | 테스트 데이터 시간순 조회 |

### 3.4 직렬화/역직렬화 수정

| 메서드 | 수정 내용 |
|--------|----------|
| `to_dict()` | `is_test` 포함 |
| `from_dict()` | `is_test` 파싱 (기본값 False) |
| `from_dataclass()` | `is_test` 전달 |

---

## 4. 구현 순서

### Step 1: Dataclass 수정

**파일:** `models/cascade_event.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `is_test: bool = False` 필드 추가 |
| 1-2 | docstring 업데이트 |

### Step 2: Django Model 수정

**파일:** `models/cascade_event.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `is_test = models.BooleanField(...)` 추가 |
| 2-2 | `help_text` 추가 |
| 2-3 | `db_index=True` 설정 |

### Step 3: 인덱스 추가

**파일:** `models/cascade_event.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `Meta.indexes`에 복합 인덱스 추가 |
| 3-2 | 인덱스 이름: `idx_cascade_test_ts` |

### Step 4: 직렬화 메서드 수정

**파일:** `models/cascade_event.py`

| 순서 | 항목 |
|------|------|
| 4-1 | `to_dict()` 수정 |
| 4-2 | `from_dict()` 수정 |
| 4-3 | `from_dataclass()` 수정 |

### Step 5: 이벤트 생성 로직 수정

**파일:** `services/cascade_recorder.py` 또는 관련 서비스

| 순서 | 항목 |
|------|------|
| 5-1 | `TestModeContext.is_synthetic()` 확인 |
| 5-2 | `is_test=True` 설정 |

### Step 6: 마이그레이션 생성

**명령어:** `python manage.py makemigrations`

| 순서 | 항목 |
|------|------|
| 6-1 | 필드 추가 마이그레이션 |
| 6-2 | 인덱스 추가 마이그레이션 |
| 6-3 | 기존 데이터: `is_test=False` (기본값) |

---

## 5. 기존 데이터 처리

### 5.1 마이그레이션 전략

| 전략 | 설명 |
|------|------|
| 기본값 False | 기존 이벤트는 운영 데이터로 간주 |
| 후처리 불필요 | 신규 이벤트부터 적용 |

### 5.2 후속 데이터 정리 (선택)

| 작업 | 설명 |
|------|------|
| `source="x-test-mode"` 탐지 | 기존 테스트 데이터 식별 |
| `is_test=True` 업데이트 | 일괄 업데이트 (선택적) |

---

## 6. API 응답 수정

### 6.1 CascadeEvent 조회 API

| 엔드포인트 | 변경 |
|-----------|------|
| `GET /api/self-healing/cascade-events/` | `is_test` 필드 포함 |
| 쿼리 파라미터 | `?is_test=false` 필터 추가 |

### 6.2 필터링 예시

| 필터 | 용도 |
|------|------|
| `?is_test=false` | 운영 데이터만 |
| `?is_test=true` | 테스트 데이터만 |
| (파라미터 없음) | 전체 |

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_dataclass_is_test_field` | Dataclass 필드 존재 |
| `test_model_is_test_field` | Django Model 필드 |
| `test_to_dict_includes_is_test` | 직렬화 포함 |
| `test_from_dict_parses_is_test` | 역직렬화 파싱 |
| `test_default_is_test_false` | 기본값 False |

### 7.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| X-Test에서 이벤트 생성 → `is_test=True` 확인 |
| 일반 요청에서 이벤트 생성 → `is_test=False` 확인 |
| API 필터 `?is_test=false` → 테스트 데이터 제외 확인 |

---

## 8. UI 연동 (참고)

### 8.1 대시보드 필터

| UI 요소 | 동작 |
|---------|------|
| "테스트 데이터 숨기기" 토글 | `is_test=false` 필터 |
| "테스트만 보기" 토글 | `is_test=true` 필터 |

### 8.2 시각적 구분

| 조건 | 표시 |
|------|------|
| `is_test=true` | 배경색 변경 또는 아이콘 |
| `is_test=false` | 기본 스타일 |

---

## 9. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `models/cascade_event.py` | CascadeEvent Dataclass 및 Model |
| `models/recovery_models.py` | BooleanField 패턴 (`requires_approval`) |
| `services/cascade_recorder.py` | 이벤트 생성 로직 |
| `core/test_mode_context.py` | `TestModeContext.is_synthetic()` (137번) |

---

## 10. 문서 시리즈 요약

| 문서 | 제목 |
|------|------|
| 137 | TestModeContext 기반 글로벌 태깅 |
| 138 | XTestPermission 2중 보안 장치 |
| 139 | XTestArtifactCleaner (Auto-Cleanup) |
| 140 | Regional Boundary (리전 스코프 강제) |
| 141 | Emergency Recovery Flow 시나리오 |
| 142 | Color-coded Causation ID |
| 143 | Resource-Aware Chaos Interlock |
| 144 | Cross-Region Conflict Scenario |
| 145 | CascadeEvent.is_test Field |

---

**시리즈 완료**

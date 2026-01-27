# X-Test Color-coded Causation ID

**문서 번호:** 142  
**작성일:** 2026-01-27  
**상태:** 구현 완료 ✅  
**선행 문서:** 141_XTEST_EMERGENCY_RECOVERY_SCENARIO.md

---

## 1. 목적

X-Test-Mode에서 생성되는 모든 causation_id에 `XTC-` (X-Test-Causation) 프리픽스를 적용하여, 로그와 대시보드에서 테스트 요청을 즉시 식별할 수 있게 한다.

### 1.1 현재 문제

| 문제 | 현재 상태 |
|------|----------|
| 시각적 구분 없음 | `cascade-a1b2c3d4e5f6` (운영과 동일) |
| 로그 필터링 어려움 | 별도 메타데이터 조회 필요 |
| 장애 분석 혼란 | 테스트 vs 실제 장애 구분 어려움 |

### 1.2 목표

| 항목 | 목표 |
|------|------|
| Cascade ID | `XTC-cascade-a1b2c3d4e5f6` |
| Event ID | `XTC-evt-a1b2c3d4` |
| 로그 필터링 | `grep "XTC-"` 한 줄로 테스트 요청 추출 |

---

## 2. 현재 상태 분석

### 2.1 ID 생성 위치

| 파일 | 함수/메서드 | ID 유형 |
|------|------------|---------|
| `core/causation_context.py` | `start_cascade()` | cascade_id |
| `core/causation_context.py` | `_generate_event_id()` | event_id |
| `core/causation_propagation.py` | `start_new_cascade()` | cascade_id |
| `adapters/celery/celery_causation.py` | `_inject_causation_context()` | 태스크 ID |

### 2.2 현재 ID 형식

| ID 유형 | 현재 형식 |
|---------|----------|
| Cascade ID | `cascade-{uuid[:12]}` |
| Event ID | `evt-{uuid[:8]}` |
| System Root | `SYSTEM_ROOT_{source}_{uuid[:8]}` |

### 2.3 기존 프리픽스 패턴

| 위치 | 프리픽스 | 용도 |
|------|---------|------|
| Redis 키 | `selfhealing:`, `xtest:` | 네임스페이스 |
| 메트릭 | `selfhealing_` | Prometheus |
| API 경로 | `/api/self-healing/` | URL |

---

## 3. 설계

### 3.1 XTC- 프리픽스 규칙

| ID 유형 | 운영 | X-Test |
|---------|------|--------|
| Cascade ID | `cascade-a1b2c3` | `XTC-cascade-a1b2c3` |
| Event ID | `evt-a1b2c3` | `XTC-evt-a1b2c3` |
| System Root | `SYSTEM_ROOT_beat_a1b2` | `XTC-SYSTEM_ROOT_beat_a1b2` |

### 3.2 컨텍스트 기반 분기

| 조건 | 프리픽스 |
|------|---------|
| `TestModeContext.is_synthetic() == True` | `XTC-` |
| `TestModeContext.is_synthetic() == False` | (없음) |

### 3.3 ID 생성 함수 수정

| 파일 | 수정 대상 |
|------|----------|
| `core/causation_context.py` | `_generate_cascade_id()` |
| `core/causation_context.py` | `_generate_event_id()` |
| `core/causation_propagation.py` | `_make_cascade_id()` |

---

## 4. 구현 순서

### Step 1: ID 생성 헬퍼 함수 추가

**파일:** `core/causation_context.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `_get_id_prefix()` 함수 추가 |
| 1-2 | `TestModeContext.is_synthetic()` 확인 |
| 1-3 | 조건부 `XTC-` 반환 |

### Step 2: Cascade ID 생성 수정

**파일:** `core/causation_context.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `start_cascade()` 내부에서 `_get_id_prefix()` 호출 |
| 2-2 | `cascade_id = f"{prefix}cascade-{uuid[:12]}"` |

### Step 3: Event ID 생성 수정

**파일:** `core/causation_context.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `propagate_to_child()` 내부에서 `_get_id_prefix()` 호출 |
| 3-2 | `event_id = f"{prefix}evt-{uuid[:8]}"` |

### Step 4: Celery 태스크 ID 수정

**파일:** `adapters/celery/celery_causation.py`

| 순서 | 항목 |
|------|------|
| 4-1 | 태스크 시작 시 컨텍스트 확인 |
| 4-2 | 프리픽스 적용 |

### Step 5: causation_propagation.py 수정

**파일:** `core/causation_propagation.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `start_new_cascade()` 수정 |
| 5-2 | 일관된 프리픽스 적용 |

### Step 6: __init__.py 업데이트

**파일:** `core/__init__.py`

| 순서 | 항목 |
|------|------|
| 6-1 | `_get_id_prefix` export (필요 시) |

---

## 5. 역호환성

### 5.1 기존 ID 파싱

| 상황 | 처리 |
|------|------|
| `XTC-` 프리픽스 있음 | 제거 후 파싱 |
| 프리픽스 없음 | 기존 로직 유지 |

### 5.2 ID 정규화 함수

| 함수 | 역할 |
|------|------|
| `normalize_cascade_id()` | `XTC-` 제거 후 순수 ID 반환 |
| `is_xtest_id()` | `XTC-` 프리픽스 여부 확인 |

---

## 6. 로그 필터링 예시

### 6.1 테스트 요청만 추출

| 명령어 | 용도 |
|--------|------|
| `grep "XTC-"` | 모든 테스트 요청 |
| `grep "XTC-cascade-"` | 테스트 Cascade만 |
| `grep -v "XTC-"` | 운영 요청만 |

### 6.2 Elasticsearch 쿼리

| 필터 | 용도 |
|------|------|
| `cascade_id: XTC-*` | 테스트 요청 |
| `NOT cascade_id: XTC-*` | 운영 요청 |

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 케이스 | 검증 항목 | 상태 |
|--------------|----------|------|
| `test_xtest_cascade_id_has_prefix` | X-Test 시 `XTC-` 포함 | ✅ 통과 |
| `test_xtest_event_id_has_prefix` | Event ID에도 적용 | ✅ 통과 |
| `test_normal_cascade_id_no_prefix` | 일반 요청 시 프리픽스 없음 | ✅ 통과 |
| `test_normal_event_id_no_prefix` | Event ID에 프리픽스 없음 | ✅ 통과 |
| `test_xtest_system_cascade_has_prefix` | 시스템 Cascade에 프리픽스 | ✅ 통과 |
| `test_normal_system_cascade_no_prefix` | 운영 시스템 Cascade 프리픽스 없음 | ✅ 통과 |
| `test_is_xtest_id` | 프리픽스 판별 함수 | ✅ 통과 |
| `test_normalize_causation_id` | 프리픽스 제거 함수 | ✅ 통과 |

### 7.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| X-Test API 호출 → 로그에 `XTC-` 포함 확인 |
| Celery 태스크 → 태스크 ID에 프리픽스 확인 |
| Cascade 전파 → 자식 이벤트도 프리픽스 유지 |

---

## 8. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `context/causation_context.py` | `CausationContext`, `_get_xtest_id_prefix()`, `is_xtest_id()`, `normalize_causation_id()` |
| `context/celery_propagation.py` | `ensure_causation_context_for_task()` |
| `core/test_mode_context.py` | `TestModeContext.is_synthetic()` |

---

## 9. 구현 결과

### 9.1 구현 완료 항목

| 순서 | 항목 | 파일 | 상태 |
|------|------|------|------|
| Step 1 | `_get_xtest_id_prefix()` 함수 추가 | `context/causation_context.py` | ✅ |
| Step 2 | `start_cascade()` XTC- 프리픽스 적용 | `context/causation_context.py` | ✅ |
| Step 3 | Event ID XTC- 프리픽스 적용 | `context/causation_context.py` | ✅ |
| Step 4 | `start_system_cascade()` XTC- 프리픽스 적용 | `context/causation_context.py` | ✅ |
| Step 5 | `ensure_causation_context_for_task()` 프리픽스 적용 | `context/celery_propagation.py` | ✅ |
| Step 6 | 역호환성 함수 추가 | `context/causation_context.py` | ✅ |
| Step 7 | `__init__.py` export 업데이트 | `context/__init__.py` | ✅ |

### 9.2 테스트 결과

- **테스트 파일:** `tests/unit/core/test_xtest_causation_id_prefix.py`
- **테스트 수:** 26개
- **결과:** ✅ 모두 통과

### 9.3 API 변경 사항

| 함수/상수 | 모듈 | 설명 |
|----------|------|------|
| `XTEST_CAUSATION_PREFIX` | `selfhealing.context` | XTC- 프리픽스 상수 |
| `is_xtest_id(causation_id)` | `selfhealing.context` | XTC- 프리픽스 여부 확인 |
| `normalize_causation_id(causation_id)` | `selfhealing.context` | XTC- 프리픽스 제거 |

---

**다음 문서:** 143_XTEST_RESOURCE_AWARE_INTERLOCK.md

# 125. Postmortem 도메인 프리 변환

**문서 버전:** 1.0
**작성일:** 2026-01-27
**선행 문서:** [124_POSTMORTEM_ENHANCEMENT_OVERVIEW.md](124_POSTMORTEM_ENHANCEMENT_OVERVIEW.md)
**상태:** 구현 준비

---

## 1. 목적

`observability.py`의 도메인 종속적인 하드코딩 값을 제거하여 범용 Self-Healing 라이브러리로 사용 가능하게 변환

---

## 2. 변경 대상

### 2.1 BlastRadiusTestView (L118-132)

| 항목 | 현재 | 변경 후 |
|------|------|---------|
| Docstring 예시 | `"payment"`, `"product"`, `"cart"`, `"auth"` | `"service_a"`, `"service_b"`, `"service_c"` |
| `affected_service` 기본값 | `"payment"` | `None` (필수 파라미터) |
| `check_services` 기본값 | `["database", "product", "cart"]` | `[]` (동적 조회) |

**변경 방향:**
- `affected_service`가 없으면 400 Bad Request 반환
- `check_services`가 비어있으면 CB 서비스에서 등록된 모든 서비스 조회

### 2.2 MultiServiceBlastRadiusView (L216-229)

| 항목 | 현재 | 변경 후 |
|------|------|---------|
| Docstring 예시 | `["database", "payment", "external_api"]` | `["service_a", "service_b", "service_c"]` |
| `test_services` 기본값 | `["database", "payment", "external_api", "cache"]` | `[]` (동적 조회) |
| `expected_isolation` 메시지 (L279) | `"database affects all, others should be isolated"` | 동적 생성 |

**변경 방향:**
- `test_services`가 비어있으면 CB 저장소에서 모든 서비스 조회
- `expected_isolation` 메시지 제거 또는 동적 생성

### 2.3 RecordHealingEventView (L423)

| 항목 | 현재 | 변경 후 |
|------|------|---------|
| Docstring 예시 | `"database"` | `"my_service"` |

---

## 3. 동적 서비스 조회 구현

### 3.1 활용 가능한 기존 코드

`observability.py`의 `_collect_service_states()` 함수에서 이미 CB 저장소를 통해 모든 서비스를 조회하고 있음:

**참조 위치:** `observability.py` L288-292

이 패턴을 `BlastRadiusTestView`와 `MultiServiceBlastRadiusView`에 적용

### 3.2 CB 저장소 접근

**참조 위치:** `services/circuit_breaker_service.py`

`cb_service.repository.get_all_states()` 메서드로 등록된 모든 서비스 조회 가능

---

## 4. Settings 확장 (선택적)

### 4.1 기본 서비스 목록 설정

**파일:** `settings/api_view.py`

새 설정 추가 고려:

| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `xtest_default_test_services` | `List[str]` | `[]` | Blast Radius 테스트 기본 서비스 목록 |

### 4.2 환경 변수

```
SELFHEALING_API_VIEW_XTEST_DEFAULT_TEST_SERVICES=["service_a","service_b"]
```

---

## 5. 구현 체크리스트

### 5.1 BlastRadiusTestView

- [ ] `affected_service` 파라미터 필수화
- [ ] `affected_service` 누락 시 400 응답
- [ ] `check_services` 빈 배열 시 동적 조회
- [ ] Docstring 예시 범용화

### 5.2 MultiServiceBlastRadiusView

- [ ] `test_services` 빈 배열 시 동적 조회
- [ ] `expected_isolation` 메시지 동적 생성 또는 제거
- [ ] Docstring 예시 범용화

### 5.3 RecordHealingEventView

- [ ] Docstring 예시 범용화

### 5.4 테스트

- [ ] 기존 테스트 업데이트 (필수 파라미터 추가)
- [ ] 동적 조회 테스트 추가
- [ ] 400 응답 테스트 추가

---

## 6. 영향 분석

### 6.1 Breaking Changes

| 항목 | 영향 | 대응 |
|------|------|------|
| `affected_service` 필수화 | 기존 호출 코드 수정 필요 | 마이그레이션 가이드 작성 |
| Default 값 제거 | 파라미터 없이 호출 시 동작 변경 | 문서화 |

### 6.2 영향받는 파일

- `api/django/views/xtest/observability.py` - 주요 변경
- `settings/api_view.py` - 선택적 확장
- `load_tests/utils/selfhealing/xtest.py` - 테스트 유틸리티 업데이트 가능

---

## 7. 검증 방법

1. 파라미터 없이 BlastRadiusTestView 호출 → 400 응답 확인
2. `check_services=[]`로 호출 → CB 저장소에서 동적 조회 확인
3. 기존 테스트 통과 확인

---

## 8. 다음 단계

이 문서 완료 후 → [126_POSTMORTEM_DURATION_CALC.md](126_POSTMORTEM_DURATION_CALC.md)

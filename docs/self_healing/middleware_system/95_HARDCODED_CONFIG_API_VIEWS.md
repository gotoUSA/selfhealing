# 95. API 뷰 설정 외부화

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94 (개요)
- **대상 파일**: `api/django/` 하위 뷰 파일들

---

## 1. 대상 파일 목록

| 파일 | 하드코딩 항목 수 | 우선순위 |
|------|----------------|----------|
| `api/django/stress_views.py` | 12건 | 높음 |
| `api/django/views/canary.py` | 2건 | 중간 |
| `api/django/views/auto_tuning.py` | 2건 | 중간 |
| `api/django/views/cascade.py` | 1건 | 낮음 |
| `api/django/views/xtest/observability.py` | 3건 | 낮음 |
| `api/django/views/xtest/base.py` | 4건 | 낮음 |

---

## 2. stress_views.py 상세

### 2.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 79 | `connection_leak_simulation` | `30` | hold_seconds 기본값 |
| 153 | `_parse_lock_request_body` | `12345` | lock_id 기본값 |
| 154 | `_parse_lock_request_body` | `5`, `60` | hold_seconds 기본값, 최대값 |
| 155 | `_parse_lock_request_body` | `True`, `True` | exclusive, wait 기본값 |
| 232 | `lock_contention_test` | `99999` | lock_id 기본값 |
| 233 | `lock_contention_test` | `5` | duration_seconds 기본값 |
| 234 | `lock_contention_test` | `100` | lock_hold_ms 기본값 |
| 281 | `controlled_burst_failure` | `777` | lock_id 기본값 |
| 282 | `controlled_burst_failure` | `1` | lock_timeout_ms 기본값 |
| 283 | `controlled_burst_failure` | `10` | burst_duration_seconds 기본값 |
| 284 | `controlled_burst_failure` | `50` | concurrent_locks 기본값 |
| 332 | `pool_exhaust` | `10` | connections_to_hold 기본값 |
| 333 | `pool_exhaust` | `30` | hold_seconds 기본값 |

### 2.2 구현 순서

1. **Step 1**: `settings/stress_test.py` 생성
   - 환경 변수 접두사: `SELFHEALING_STRESS_TEST_`
   - 모든 기본값 정의
   - Pydantic 검증 규칙 추가

2. **Step 2**: `stress_views.py` 수정
   - Settings import 추가
   - 각 함수에서 Settings 기본값 참조
   - 기존 함수 시그니처 유지

### 2.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_STRESS_TEST_DEFAULT_HOLD_SECONDS` | `30` | 기본 락 유지 시간 |
| `SELFHEALING_STRESS_TEST_MAX_HOLD_SECONDS` | `60` | 최대 락 유지 시간 |
| `SELFHEALING_STRESS_TEST_DEFAULT_LOCK_ID` | `12345` | 기본 락 ID |
| `SELFHEALING_STRESS_TEST_DEFAULT_DURATION_SECONDS` | `5` | 기본 테스트 지속 시간 |
| `SELFHEALING_STRESS_TEST_DEFAULT_LOCK_HOLD_MS` | `100` | 락 유지 밀리초 |
| `SELFHEALING_STRESS_TEST_DEFAULT_LOCK_TIMEOUT_MS` | `1` | 락 타임아웃 밀리초 |
| `SELFHEALING_STRESS_TEST_DEFAULT_BURST_DURATION_SECONDS` | `10` | 버스트 지속 시간 |
| `SELFHEALING_STRESS_TEST_DEFAULT_CONCURRENT_LOCKS` | `50` | 동시 락 수 |
| `SELFHEALING_STRESS_TEST_DEFAULT_CONNECTIONS_TO_HOLD` | `10` | 유지할 커넥션 수 |

---

## 3. views/canary.py 상세

### 3.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 177 | `list` 메서드 | `20` | completed rollouts limit |
| 584 | `history` 메서드 | `20` | 쿼리 파라미터 limit 기본값 |
| 222 | `create` 메서드 | `0`, `5` | percentage, duration_minutes 기본값 |

### 3.2 구현 순서

1. **Step 1**: 기존 `settings/canary.py` 확인 (있으면 확장, 없으면 생성)
2. **Step 2**: 누락된 설정 항목 추가
3. **Step 3**: `views/canary.py`에서 Settings 참조

### 3.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CANARY_DEFAULT_HISTORY_LIMIT` | `20` | 히스토리 조회 기본 limit |
| `SELFHEALING_CANARY_DEFAULT_DURATION_MINUTES` | `5` | 스테이지 기본 지속 시간 |

---

## 4. views/auto_tuning.py 상세

### 4.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 287 | `export_csv` | `1000` | records limit |
| 300 | `list` 메서드 | `1` | page 기본값 |
| 301 | `list` 메서드 | `20` | page_size 기본값 |

### 4.2 구현 순서

1. **Step 1**: `settings/auto_tuning.py` 생성 또는 확장
2. **Step 2**: 페이지네이션 설정 추가
3. **Step 3**: `views/auto_tuning.py`에서 Settings 참조

### 4.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_AUTO_TUNING_EXPORT_LIMIT` | `1000` | CSV 내보내기 최대 레코드 |
| `SELFHEALING_AUTO_TUNING_DEFAULT_PAGE_SIZE` | `20` | 기본 페이지 크기 |

---

## 5. views/cascade.py 상세

### 5.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 75 | `list` 메서드 | `100`, `1000` | limit 기본값, 최대값 |

### 5.2 구현 순서

1. **Step 1**: 기존 `settings/cascade_retention.py` 확장
2. **Step 2**: API 관련 설정 추가
3. **Step 3**: `views/cascade.py`에서 Settings 참조

### 5.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CASCADE_API_DEFAULT_LIMIT` | `100` | 조회 기본 limit |
| `SELFHEALING_CASCADE_API_MAX_LIMIT` | `1000` | 조회 최대 limit |

---

## 6. views/xtest/observability.py 상세

### 6.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 54 | `get` 메서드 | `50` | limit 기본값 |
| 131 | `post` 메서드 | `5` | failure_count 기본값 |
| 236 | `post` 메서드 | `5` | failure_count 기본값 |
| 398 | `get` 메서드 | `100` | history limit |
| 491 | `get` 메서드 | `10` | incidents limit |

### 6.2 구현 순서

1. **Step 1**: xtest 전용 Settings 생성 또는 기존 확장
2. **Step 2**: observability 관련 설정 추가
3. **Step 3**: `views/xtest/observability.py`에서 Settings 참조

---

## 7. views/xtest/base.py 상세

### 7.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 135 | `_max_events` | `500` | 최대 이벤트 수 |
| 136 | `_max_incidents` | `100` | 최대 인시던트 수 |
| 159 | `get_healing_events` | `50` | limit 기본값 |
| 165 | `get_healing_incidents` | `10` | limit 기본값 |

### 7.2 구현 순서

1. **Step 1**: xtest Settings에 항목 추가
2. **Step 2**: 모듈 레벨 상수를 Settings 참조로 변경
3. **Step 3**: 함수 기본값을 Settings에서 로드

---

## 8. 전체 구현 순서 요약

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1 | `settings/stress_test.py` 생성 | 30분 |
| 2 | `stress_views.py` 리팩토링 | 45분 |
| 3 | Canary Settings 확장 | 15분 |
| 4 | `views/canary.py` 리팩토링 | 20분 |
| 5 | Auto-tuning Settings 생성 | 15분 |
| 6 | `views/auto_tuning.py` 리팩토링 | 15분 |
| 7 | Cascade Settings 확장 | 10분 |
| 8 | `views/cascade.py` 리팩토링 | 10분 |
| 9 | xtest Settings 생성 | 20분 |
| 10 | `views/xtest/*.py` 리팩토링 | 30분 |
| 11 | 테스트 실행 및 검증 | 30분 |
| **총계** | | **약 4시간** |

---

## 9. 검증 체크리스트

- [ ] `settings/stress_test.py` 생성됨
- [ ] 모든 환경 변수 문서화됨
- [ ] `stress_views.py` 하드코딩 12건 제거됨
- [ ] `views/canary.py` 하드코딩 2건 제거됨
- [ ] `views/auto_tuning.py` 하드코딩 2건 제거됨
- [ ] `views/cascade.py` 하드코딩 1건 제거됨
- [ ] `views/xtest/*.py` 하드코딩 7건 제거됨
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

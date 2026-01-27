# 126. Postmortem Duration 계산 구현

**문서 버전:** 1.0  
**작성일:** 2026-01-27  
**선행 문서:** [125_POSTMORTEM_DOMAIN_FREE.md](125_POSTMORTEM_DOMAIN_FREE.md)  
**상태:** 구현 준비

---

## 1. 목적

현재 `None`으로 고정된 `duration_seconds` 필드를 실제 인시던트 지속 시간으로 계산

---

## 2. 현재 상태

### 2.1 문제 위치

**파일:** `observability.py` → `_generate_postmortem_data()` (L326-357)

| 필드 | 현재 값 | 문제 |
|------|---------|------|
| `started_at` | 타임라인 첫 이벤트 시각 | ✅ 정상 |
| `resolved_at` | `timezone.now()` | ⚠️ API 호출 시점 |
| `duration_seconds` | `None` | ❌ 미계산 |

### 2.2 타임라인 구조

**파일:** `observability.py` → `_build_timeline()` (L296-324)

타임라인 이벤트에 `timestamp` 필드가 포함되어 있음

---

## 3. 계산 로직

### 3.1 기본 공식

```
duration_seconds = resolved_at - started_at
```

### 3.2 시작 시점 결정

| 우선순위 | 기준 | 설명 |
|----------|------|------|
| 1 | 타임라인 첫 번째 CB OPEN 이벤트 | 가장 정확 |
| 2 | 타임라인 첫 번째 이벤트 | 대체 방법 |
| 3 | `None` | 타임라인이 비어있는 경우 |

### 3.3 종료 시점 결정

| 우선순위 | 기준 | 설명 |
|----------|------|------|
| 1 | 마지막 CB CLOSED 이벤트 | 가장 정확 |
| 2 | API 호출 시점 (`timezone.now()`) | 대체 방법 |

---

## 4. 구현 위치

### 4.1 헬퍼 함수 추가

**파일:** `observability.py`

`_generate_postmortem_data()` 함수 앞에 새 헬퍼 함수 추가:

**함수명:** `_calculate_incident_duration()`

**입력:**
- `timeline`: 정렬된 이벤트 리스트
- `resolved_at`: ISO 형식 문자열 (선택적)

**출력:**
- `tuple[str, str, Optional[float]]` → `(started_at, resolved_at, duration_seconds)`

### 4.2 기존 함수 수정

**파일:** `observability.py` → `_generate_postmortem_data()` (L326-357)

새 헬퍼 함수를 호출하여 3개 필드 값 설정

---

## 5. 이벤트 타입 필터링

### 5.1 CB 상태 변경 이벤트 식별

**참조:** `_build_timeline()` (L300-307)

현재 CB 이벤트 필터링 로직:

```
"circuit_breaker" in event_type.lower()
또는
event.data.state_change 존재
```

### 5.2 OPEN 이벤트 식별

**참조:** `services/event_bus.py` → `EventType`

| EventType | 용도 |
|-----------|------|
| `CIRCUIT_BREAKER_OPENED` | 인시던트 시작 |
| `CIRCUIT_BREAKER_CLOSED` | 인시던트 종료 |
| `CIRCUIT_BREAKER_HALF_OPENED` | 복구 시도 중 |

---

## 6. 구현 체크리스트

### 6.1 헬퍼 함수

- [ ] `_calculate_incident_duration()` 함수 생성
- [ ] 타임라인에서 첫 OPEN 이벤트 찾기
- [ ] 타임라인에서 마지막 CLOSED 이벤트 찾기
- [ ] 시간 차이 계산 (초 단위)
- [ ] ISO 형식 파싱 처리

### 6.2 _generate_postmortem_data 수정

- [ ] 헬퍼 함수 호출
- [ ] `started_at` 필드 업데이트
- [ ] `resolved_at` 필드 업데이트
- [ ] `duration_seconds` 필드 계산값 설정

### 6.3 테스트

- [ ] 정상 케이스: OPEN → CLOSED 존재
- [ ] 예외 케이스: CLOSED 없음 (진행 중 인시던트)
- [ ] 예외 케이스: 빈 타임라인
- [ ] 시간 계산 정확도 확인

---

## 7. 예상 결과

### 7.1 변경 전

```json
{
  "started_at": "2026-01-27T14:01:23+09:00",
  "resolved_at": "2026-01-27T14:02:25+09:00",
  "duration_seconds": null
}
```

### 7.2 변경 후

```json
{
  "started_at": "2026-01-27T14:01:23+09:00",
  "resolved_at": "2026-01-27T14:02:25+09:00",
  "duration_seconds": 62.0
}
```

---

## 8. 에러 처리

| 상황 | 처리 |
|------|------|
| 타임라인 비어있음 | `duration_seconds = None` |
| OPEN 이벤트 없음 | 첫 번째 이벤트를 시작점으로 |
| CLOSED 이벤트 없음 | 현재 시각을 종료점으로 (진행 중) |
| 시간 파싱 실패 | `duration_seconds = None`, 로그 경고 |

---

## 9. 다음 단계

이 문서 완료 후 → [127_POSTMORTEM_ACTION_ITEMS.md](127_POSTMORTEM_ACTION_ITEMS.md)

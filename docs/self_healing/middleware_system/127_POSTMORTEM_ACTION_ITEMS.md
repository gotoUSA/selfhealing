# 127. Postmortem 동적 Action Items 생성

**문서 버전:** 1.0
**작성일:** 2026-01-27
**선행 문서:** [126_POSTMORTEM_DURATION_CALC.md](126_POSTMORTEM_DURATION_CALC.md)
**상태:** ✅ 구현 완료

---

## 1. 목적

현재 하드코딩된 `auto_actions` 리스트를 실제 발생한 이벤트 기반으로 동적 생성

---

## 2. 현재 상태

### 2.1 문제 위치

**파일:** `observability.py` → `_generate_postmortem_data()` (L346-356)

| 필드 | 현재 값 | 문제 |
|------|---------|------|
| `auto_actions` | 하드코딩된 4개 문자열 | 실제 수행 여부와 무관 |
| `recommendations` | 하드코딩된 3개 문자열 | 동적 분석 없음 |

### 2.2 현재 하드코딩 내용

```
auto_actions:
- "✅ Circuit Breaker 자동 감지"
- "✅ Fast Fail 활성화"
- "✅ 연쇄 장애 차단 (Blast Radius 격리)"
- "✅ 자동 복구 시도"

recommendations:
- "장애 근본 원인 분석 필요"
- "복구 시간 개선 검토"
- "모니터링 알림 설정 확인"
```

---

## 3. 참조 패턴

### 3.1 Chaos Reports의 Action Items

**파일:** `services/chaos/reports.py` → `_generate_action_items()` (L644-678)

이미 구현된 패턴:

| 필드 | 타입 | 설명 |
|------|------|------|
| `title` | `str` | 액션 제목 |
| `description` | `str` | 상세 설명 |
| `priority` | `str` | `"high"`, `"medium"`, `"low"` |
| `service` | `str` | 대상 서비스명 |

### 3.2 Action Items 생성 기준 (Chaos Reports)

| 조건 | 생성되는 Action Item |
|------|---------------------|
| 실험 실패 | "Investigate failure: {service}" |
| 느린 복구 | "Improve recovery time: {service}" |

---

## 4. Postmortem용 Action Items 설계

### 4.1 이벤트 기반 Action Items

| 이벤트 타입 | 생성되는 Action |
|------------|----------------|
| `CIRCUIT_BREAKER_OPENED` | "CB OPEN 발생 - {service}" |
| `CIRCUIT_BREAKER_CLOSED` | "CB 복구 완료 - {service}" |
| `ERROR_BUDGET_CRITICAL` | "Error Budget 임계치 초과" |
| `EMERGENCY_ACTIVATED` | "비상 모드 활성화됨" |
| `DLQ_ITEM_ADDED` | "DLQ에 {count}건 적재됨" |

### 4.2 분석 기반 Recommendations

| 조건 | 생성되는 Recommendation |
|------|------------------------|
| `duration_seconds` > 120 | "복구 시간이 2분을 초과함 - SLA 검토 필요" |
| `affected_services` 개수 > 3 | "다중 서비스 장애 - 공통 원인 분석 필요" |
| Fast Fail 미발생 | "Fast Fail 미동작 - CB 설정 점검 필요" |

---

## 5. 구현 위치

### 5.1 새 헬퍼 함수

**파일:** `observability.py`

**함수명:** `_generate_dynamic_actions()`

**입력:**
- `timeline`: 이벤트 리스트
- `affected_services`: 영향받은 서비스 리스트
- `duration_seconds`: 인시던트 지속 시간

**출력:**
- `tuple[list, list]` → `(auto_actions, recommendations)`

### 5.2 Action 구조

Google SRE 표준에 맞춘 구조:

| 필드 | 타입 | 설명 |
|------|------|------|
| `action` | `str` | 수행된 조치 |
| `status` | `str` | `"completed"`, `"in_progress"`, `"pending"` |
| `timestamp` | `str` | 수행 시각 (ISO 형식) |
| `service` | `str` | 대상 서비스 (선택적) |

---

## 6. 이벤트 타입 매핑

### 6.1 EventType Enum 참조

**파일:** `services/event_bus.py` → `EventType` (L60-95)

| EventType | Action 메시지 |
|-----------|--------------|
| `CIRCUIT_BREAKER_OPENED` | "Circuit Breaker OPEN 전환" |
| `CIRCUIT_BREAKER_HALF_OPENED` | "Circuit Breaker 복구 시도 (HALF_OPEN)" |
| `CIRCUIT_BREAKER_CLOSED` | "Circuit Breaker 정상 복구 (CLOSED)" |
| `ERROR_BUDGET_CRITICAL` | "Error Budget 임계치 경고" |
| `ERROR_BUDGET_WARNING` | "Error Budget 경고 수준 도달" |
| `EMERGENCY_ACTIVATED` | "비상 모드 활성화" |
| `KILL_SWITCH_ACTIVATED` | "Kill Switch 활성화" |
| `DLQ_ITEM_ADDED` | "DLQ에 항목 적재됨" |
| `DLQ_REPLAY_BLOCKED` | "DLQ Replay 차단됨" |

---

## 7. 구현 체크리스트

### 7.1 헬퍼 함수

- [x] `_generate_dynamic_actions()` 함수 생성
- [x] 타임라인에서 이벤트 타입별 분류
- [x] 이벤트 → Action 매핑
- [x] 조건 기반 Recommendation 생성

### 7.2 _generate_postmortem_data 수정

- [x] 헬퍼 함수 호출
- [x] `auto_actions` 필드에 동적 리스트 설정
- [x] `recommendations` 필드에 동적 리스트 설정

### 7.3 하드코딩 제거

- [x] 기존 하드코딩된 `auto_actions` 제거
- [x] 기존 하드코딩된 `recommendations` 제거

### 7.4 테스트

- [x] CB OPEN 이벤트 있을 때 Action 생성 확인
- [x] 빈 타임라인일 때 빈 리스트 반환
- [x] 느린 복구 시 Recommendation 생성 확인
- [x] DLQ_ITEM_ADDED 이벤트 Action 생성 확인
- [x] DLQ_REPLAY_BLOCKED 이벤트 Action 생성 확인
- [x] Fast Fail 미발생 시 Recommendation 생성 확인
- [x] CB OPEN 후 복구 시 Fast Fail 경고 없음 확인
- [x] ERROR_BUDGET_WARNING 이벤트 Action 생성 확인

---

## 8. 예상 결과

### 8.1 변경 전 (하드코딩)

```json
{
  "auto_actions": [
    "✅ Circuit Breaker 자동 감지",
    "✅ Fast Fail 활성화",
    "✅ 연쇄 장애 차단 (Blast Radius 격리)",
    "✅ 자동 복구 시도"
  ]
}
```

### 8.2 변경 후 (동적 생성)

```json
{
  "auto_actions": [
    {
      "action": "Circuit Breaker OPEN 전환",
      "status": "completed",
      "timestamp": "2026-01-27T14:01:24+09:00",
      "service": "database"
    },
    {
      "action": "Circuit Breaker 복구 시도 (HALF_OPEN)",
      "status": "completed",
      "timestamp": "2026-01-27T14:02:24+09:00",
      "service": "database"
    },
    {
      "action": "Circuit Breaker 정상 복구 (CLOSED)",
      "status": "completed",
      "timestamp": "2026-01-27T14:02:25+09:00",
      "service": "database"
    }
  ],
  "recommendations": [
    "복구 시간이 61초로 SLA 목표(60초) 초과 - 개선 검토 필요"
  ]
}
```

---

## 9. 호환성 고려

### 9.1 응답 구조 변경

| 필드 | 이전 타입 | 새 타입 | Breaking Change |
|------|----------|---------|-----------------|
| `auto_actions` | `List[str]` | `List[Dict]` | ⚠️ Yes |
| `recommendations` | `List[str]` | `List[str]` | No |

### 9.2 마이그레이션 옵션

1. **옵션 A:** 새 필드 `actions_detailed` 추가, 기존 필드 유지
2. **옵션 B:** 버전 파라미터로 응답 형식 선택
3. **옵션 C:** Breaking Change로 진행 (권장)

---

## 10. 다음 단계

이 문서 완료 후 → [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md)

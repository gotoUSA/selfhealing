# 128. Postmortem 자동 트리거 구현

**문서 버전:** 1.0
**작성일:** 2026-01-27
**선행 문서:** [127_POSTMORTEM_ACTION_ITEMS.md](127_POSTMORTEM_ACTION_ITEMS.md)
**상태:** ✅ 구현 완료 (2026-01-28)

---

## 1. 목적

CB CLOSED 이벤트 발생 시 자동으로 Post-mortem 리포트를 생성하여 수동 API 호출 없이 인시던트 기록

---

## 2. 현재 시스템 분석

### 2.1 기존 CB CLOSED 핸들러

**파일:** `services/event_bus.py` → `_on_circuit_breaker_closed()` (L619-667)

현재 동작:
1. CB CLOSED 이벤트 수신
2. RuntimeConfig에서 `track1_enabled` 확인
3. 활성화 시 `conditional_replay_on_circuit_close` Celery 태스크 트리거

### 2.2 기존 핸들러 등록

**파일:** `services/event_bus.py` → `register_default_handlers()` (L674-710)

CB CLOSED 이벤트에 `_on_circuit_breaker_closed` 핸들러가 등록됨

### 2.3 활용 가능한 구조

| 구성요소 | 위치 | 용도 |
|---------|------|------|
| `EventType.CIRCUIT_BREAKER_CLOSED` | `event_bus.py` | 이벤트 타입 |
| `bus.subscribe()` | `event_bus.py` | 핸들러 등록 |
| `add_healing_incident()` | `base.py` | 인시던트 저장 |

---

## 3. 설계

### 3.1 새 핸들러 추가

**파일:** `services/event_bus.py`

**함수명:** `_on_circuit_breaker_closed_postmortem()`

**동작:**
1. CB CLOSED 이벤트 수신
2. Settings에서 `auto_postmortem_enabled` 확인
3. 활성화 시 Post-mortem 생성 로직 호출
4. `add_healing_incident()` 호출하여 저장

### 3.2 Settings 확장

**파일:** `settings/api_view.py`

새 설정 추가:

| 설정명 | 타입 | 기본값 | 설명 |
|--------|------|--------|------|
| `xtest_auto_postmortem_enabled` | `bool` | `False` | 자동 Post-mortem 생성 활성화 |
| `xtest_auto_postmortem_min_duration` | `int` | `30` | 최소 인시던트 지속 시간 (초) |

### 3.3 환경 변수

```
SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED=true
SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_MIN_DURATION=30
```

---

## 4. 핸들러 등록

### 4.1 register_default_handlers 수정

**파일:** `services/event_bus.py` → `register_default_handlers()`

기존 CB CLOSED 핸들러 다음에 새 핸들러 등록:

| 순서 | 핸들러 | Priority | 용도 |
|------|--------|----------|------|
| 1 | `_on_circuit_breaker_closed` | NORMAL | Track 1 Replay |
| 2 | `_on_circuit_breaker_closed_postmortem` | LOW | 자동 Post-mortem |

Priority를 LOW로 설정하여 Replay 처리 후 실행

---

## 5. 인시던트 추적

### 5.1 문제

CB CLOSED 이벤트만으로는 인시던트 시작 시점(CB OPEN)을 알 수 없음

### 5.2 해결 방안

**방안 A:** EventBus 히스토리에서 가장 최근 CB OPEN 이벤트 조회

**참조:** `event_bus.py` → `bus.get_history(limit=N)`

**방안 B:** CB OPEN 시점에 인시던트 시작 기록, CLOSED 시점에 완료 처리

**방안 C:** 타임라인 기반 계산 (현재 `_build_timeline()` 활용)

**권장:** 방안 A (기존 코드 재활용)

---

## 6. 구현 위치

### 6.1 핸들러 함수

**파일:** `services/event_bus.py`

`_on_circuit_breaker_closed()` 함수 아래에 새 함수 추가

### 6.2 Post-mortem 생성 로직

**옵션 1:** `observability.py`의 로직을 재사용 가능한 함수로 추출

**옵션 2:** 핸들러에서 직접 로직 구현

**권장:** 옵션 1 - 코드 중복 방지

### 6.3 로직 추출

**파일:** `observability.py`

`PostmortemGeneratorView.post()` 메서드의 핵심 로직을 별도 함수로 추출:

**함수명:** `generate_postmortem_for_service()`

**입력:**
- `service_name`: 대상 서비스
- `incident_id`: 인시던트 ID (선택적, 자동 생성)

**출력:**
- `dict`: Post-mortem 데이터

---

## 7. 중복 방지

### 7.1 문제

여러 서비스가 동시에 CLOSED되면 여러 Post-mortem 생성될 수 있음

### 7.2 해결 방안

| 방안 | 설명 |
|------|------|
| 디바운스 | 일정 시간(예: 5초) 내 여러 CLOSED 이벤트를 하나로 통합 |
| 서비스별 분리 | 각 서비스별로 별도 Post-mortem 생성 (현재 권장) |
| 인시던트 그룹화 | 같은 시간대 이벤트를 하나의 인시던트로 묶음 |

---

## 8. 구현 체크리스트

### 8.1 Settings 확장

- [x] `xtest_auto_postmortem_enabled` 설정 추가
- [x] `xtest_auto_postmortem_min_duration` 설정 추가
- [x] 환경 변수 문서화

### 8.2 로직 추출

- [x] `_generate_postmortem_data()` 함수 추출 (observability.py)
- [x] `PostmortemGeneratorView.post()`에서 추출 함수 호출하도록 리팩토링

### 8.3 핸들러 구현

- [x] `_on_circuit_breaker_closed_postmortem()` 함수 생성
- [x] Settings에서 활성화 여부 확인
- [x] 최소 duration 확인 (선택적)
- [x] Post-mortem 생성 및 저장

### 8.4 핸들러 등록

- [x] `register_default_handlers()`에 새 핸들러 등록
- [x] Priority LOW로 설정

### 8.5 테스트

- [x] 설정 비활성화 시 Post-mortem 미생성 확인
- [x] 설정 활성화 시 자동 생성 확인
- [x] 저장된 인시던트 조회 확인

**테스트 파일:** `packages/selfhealing-python/tests/unit/resilience/test_postmortem_auto_trigger.py`
**테스트 결과:** 10개 테스트 전체 통과

---

## 9. 로그

### 9.1 예상 로그 메시지

| 상황 | 로그 레벨 | 메시지 |
|------|----------|--------|
| 자동 Post-mortem 비활성화 | DEBUG | `"[EventHandler] Auto postmortem disabled, skipping"` |
| Post-mortem 생성 성공 | INFO | `"[EventHandler] Auto postmortem generated: {incident_id}"` |
| 생성 실패 | ERROR | `"[EventHandler] Failed to generate auto postmortem: {error}"` |

---

## 10. 영향 분석

### 10.1 성능

| 항목 | 영향 |
|------|------|
| CB CLOSED 처리 시간 | 약간 증가 (Post-mortem 생성) |
| 메모리 사용 | `_healing_incidents` 리스트 증가 |

### 10.2 기존 기능

| 기능 | 영향 |
|------|------|
| 기존 CB CLOSED 핸들러 | 영향 없음 (별도 핸들러) |
| 수동 Post-mortem API | 영향 없음 (병행 가능) |

---

## 11. 다음 단계

이 문서 완료 후 → [129_POSTMORTEM_ROOT_CAUSE.md](129_POSTMORTEM_ROOT_CAUSE.md) (선택적)

---

## 12. 추가 고려사항

### 12.1 알림 연동 (향후)

Post-mortem 생성 시 Slack/Email 알림 발송 가능

**참조:** `_on_circuit_breaker_opened_notify()` (L553-615)

### 12.2 영속성 (향후)

현재 In-Memory 저장소를 DB 또는 Redis로 변경 가능

**참조:** `base.py` → `_healing_incidents` 리스트

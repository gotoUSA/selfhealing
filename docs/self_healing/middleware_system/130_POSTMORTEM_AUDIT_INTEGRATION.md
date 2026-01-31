# 130. Postmortem Audit 통합

**문서 버전:** 1.0
**작성일:** 2026-01-27
**선행 문서:** [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md)
**상태:** ✅ 구현 완료

---

## 1. 현재 구현 분석

### 1.1 Post-mortem 기능의 위치적 문제

| 항목 | 현재 상태 |
|------|----------|
| 파일 위치 | `api/django/views/xtest/observability.py` |
| 상속 | `PostmortemGeneratorView(XTestModeMixin, APIView)` |
| 헤더 검증 | `check_chaos_permission()` 호출 |

**문제:** Post-mortem은 **실제 장애 상황**에 대한 사후 분석 기록인데, 현재 X-Test-Mode(카오스 테스트) 모듈 안에 구현되어 있음

### 1.2 두 가지 Post-mortem 생성 경로

| 경로 | 파일 | X-Test 헤더 필요 | 용도 |
|------|------|-----------------|------|
| 수동 API | `observability.py` → `PostmortemGeneratorView.post()` | ✅ 필요 | 테스트 목적 |
| 자동 트리거 | `event_bus.py` → `_on_circuit_breaker_closed_postmortem()` | ❌ 불필요 | 실제 장애 |

**분석:**
- 수동 API는 X-Test-Mode 전용 (테스트 환경에서만 동작)
- 자동 트리거는 EventBus 핸들러로 실제 CB CLOSED 이벤트에 반응
- 자동 트리거는 Settings 기반 (`xtest_auto_postmortem_enabled`)으로 제어되며, X-Test 헤더와 무관

### 1.3 Audit 로깅 현황

**다른 X-Test View의 Audit 패턴:**
- `replay.py`, `dlq.py`, `circuit_breaker.py`, `idempotency.py`, `rate_limit.py` 등
- 모두 `self.log_xtest_audit()` 호출하여 WAL에 기록

**observability.py의 Audit 현황:**
- `PostmortemGeneratorView` - Audit 호출 없음
- `BlastRadiusTestView` - Audit 호출 없음
- `RecordHealingEventView` - Audit 호출 없음
- `HealingTimelineView` - Audit 호출 없음 (조회 전용)
- `GetHealingIncidentsView` - Audit 호출 없음 (조회 전용)

---

## 2. Audit 필요성 분석

### 2.1 수동 API (X-Test View)

X-Test-Mode에서 수동으로 Post-mortem을 생성할 때는 다른 X-Test 작업과 동일하게 Audit 기록 필요

| View | 작업 유형 | Audit 필요 여부 |
|------|----------|----------------|
| `PostmortemGeneratorView` | 생성 | ✅ 필요 |
| `BlastRadiusTestView` | 테스트 | ✅ 필요 |
| `MultiServiceBlastRadiusView` | 테스트 | ✅ 필요 |
| `RecordHealingEventView` | 생성 | ✅ 필요 |
| `HealingTimelineView` | 조회 | ⚪ 선택적 |
| `GetHealingIncidentsView` | 조회 | ⚪ 선택적 |

### 2.2 자동 트리거

자동 트리거는 X-Test와 무관하게 실제 장애 상황에서 동작하므로, 별도의 Audit 방식 필요

| 항목 | 권장 방식 |
|------|----------|
| Audit 함수 | `log_xtest_operation_audit()` 대신 일반 `_write_to_wal()` 사용 |
| Event Type | `XTEST_OPERATION` 대신 `POSTMORTEM_GENERATED` |
| Source | `XTest.observability` 대신 `SelfHealing.Postmortem` |

---

## 3. 구현 방안

### 3.1 수동 API Audit 추가

**대상 파일:** `observability.py`

**추가 대상 View:**

| View | action 값 | component 값 |
|------|----------|-------------|
| `PostmortemGeneratorView` | `generate_postmortem` | `observability` |
| `BlastRadiusTestView` | `blast_radius_test` | `observability` |
| `MultiServiceBlastRadiusView` | `multi_blast_radius_test` | `observability` |
| `RecordHealingEventView` | `record_healing_event` | `observability` |

### 3.2 자동 트리거 Audit 추가

**대상 파일:** `event_bus.py` → `_on_circuit_breaker_closed_postmortem()`

**Audit 기록 시점:**
1. Post-mortem 생성 성공 시
2. 최소 duration 미달로 스킵 시 (선택적)

**Audit 내용:**
- `event_type`: `POSTMORTEM_AUTO_GENERATED`
- `source`: `EventHandler.Postmortem`
- `details`: incident_id, service_name, duration_seconds, affected_services

---

## 4. 아키텍처 권장사항

### 4.1 장기적 분리 권장

| 현재 | 권장 |
|------|------|
| `views/xtest/observability.py` | `views/postmortem/generator.py` (실제 용) |
| - | `views/xtest/postmortem_test.py` (테스트 용) |

**이유:**
- Post-mortem은 실제 장애 분석 도구
- X-Test는 카오스 테스트 도구
- 목적이 다르므로 분리하는 것이 적절

### 4.2 단기적 조치 (현재 구조 유지)

현재 구조를 유지하면서 Audit만 추가하는 경우:
- X-Test View는 `self.log_xtest_audit()` 사용
- 자동 트리거는 별도 Audit 함수 사용

---

## 5. 구현 체크리스트

### 5.1 수동 API

- [x] `PostmortemGeneratorView.post()` 끝에 Audit 로깅 추가
  - **참고:** 문서 133에 따라 `views/postmortem.py`로 분리됨 (XTest 분리)
  - `_log_postmortem_audit()` 정적 메서드 사용, `_write_to_wal()` 호출
  - event_type: `POSTMORTEM_MANUAL_GENERATED`, source: `API.Postmortem`
- [x] `BlastRadiusTestView.post()` 끝에 `self.log_xtest_audit()` 추가
- [x] `MultiServiceBlastRadiusView.post()` 끝에 `self.log_xtest_audit()` 추가
- [x] `RecordHealingEventView.post()` 끝에 `self.log_xtest_audit()` 추가

### 5.2 자동 트리거

- [x] `_on_circuit_breaker_closed_postmortem()`에 Audit 로깅 추가
- [x] Audit 함수 선택 (xtest용 vs 일반용) → `_write_to_wal()` 사용, event_type=`POSTMORTEM_AUTO_GENERATED`

---

## 6. 관련 문서

- [123_XTEST_AUDIT_INTEGRATION.md](123_XTEST_AUDIT_INTEGRATION.md) - X-Test Audit 통합 전체
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - 자동 트리거 구현

# 124. Postmortem 자동 생성 기능 확장 개요

**문서 버전:** 1.0  
**작성일:** 2026-01-27  
**작성자:** GitHub Copilot (Claude Opus 4.5)  
**상태:** 구현 준비

---

## 1. 현재 시스템 분석

### 1.1 구현 완료된 기능

| 기능 | 파일 위치 | 상태 |
|------|----------|------|
| Post-mortem 생성 API | `api/django/views/xtest/observability.py` → `PostmortemGeneratorView` | ✅ 구현됨 |
| 힐링 타임라인 조회 | `api/django/views/xtest/observability.py` → `HealingTimelineView` | ✅ 구현됨 |
| 힐링 이벤트 기록 | `api/django/views/xtest/observability.py` → `RecordHealingEventView` | ✅ 구현됨 |
| 인시던트 목록 조회 | `api/django/views/xtest/observability.py` → `GetHealingIncidentsView` | ✅ 구현됨 |
| Blast Radius 테스트 | `api/django/views/xtest/observability.py` → `BlastRadiusTestView` | ✅ 구현됨 |
| 시스템 스냅샷 수집 | `api/django/views/xtest/base.py` → `collect_system_snapshot()` | ✅ 구현됨 |
| In-Memory 이벤트 저장 | `api/django/views/xtest/base.py` → `_healing_events`, `_healing_incidents` | ✅ 구현됨 |
| Settings 기반 설정 | `settings/api_view.py` → `xtest_postmortem_history_limit` 등 | ✅ 구현됨 |

### 1.2 이미 존재하는 연관 기능

| 기능 | 파일 위치 | 활용 가능성 |
|------|----------|------------|
| Action Items 생성 | `services/chaos/reports.py` → `_generate_action_items()` | ✅ 패턴 재사용 |
| CB 상태 변경 이벤트 | `services/event_bus.py` → `emit_circuit_breaker_state_changed()` | ✅ 트리거 가능 |
| CB CLOSED 핸들러 | `services/event_bus.py` → `_on_circuit_breaker_closed()` | ✅ 확장 지점 |
| EventBus Subscribe | `services/event_bus.py` → `bus.subscribe()` | ✅ 자동 트리거용 |

### 1.3 현재 코드의 한계

| 항목 | 현재 상태 | 문제점 |
|------|----------|--------|
| `duration_seconds` | `None`으로 고정 | 복구 소요 시간 미계산 |
| `auto_actions` | 하드코딩된 문자열 리스트 | 실제 수행된 액션이 아님 |
| 자동 트리거 | 없음 (수동 API 호출) | CB CLOSED 시 자동 생성 불가 |
| 영속성 | In-Memory (`_healing_incidents`) | 재시작 시 데이터 손실 |
| 도메인 종속성 | Default 값에 이커머스 용어 포함 | 범용성 저하 |

---

## 2. 도메인 종속성 분석

### 2.1 종속 위치 (observability.py)

| 라인 | 현재 값 | 문제 |
|------|--------|------|
| L118 (docstring) | `"payment"`, `"product"`, `"cart"`, `"auth"` | 예시 문서에 하드코딩 |
| L131 | `affected_service` 기본값 `"payment"` | 이커머스 종속 |
| L132 | `check_services` 기본값 `["database", "product", "cart"]` | 이커머스 종속 |
| L216 (docstring) | `["database", "payment", "external_api"]` | 예시 문서에 하드코딩 |
| L229 | `test_services` 기본값 `["database", "payment", "external_api", "cache"]` | 이커머스 종속 |
| L279 | `expected_isolation` 메시지 | 이커머스 종속 |
| L423 (docstring) | `"database"` 예시 | 예시 문서에 하드코딩 |

### 2.2 도메인 프리 변환 전략

- Default 값을 제거하고 필수 파라미터로 변경
- 또는 CB 서비스에서 동적으로 등록된 서비스 목록 조회
- Docstring 예시는 `"service_a"`, `"service_b"` 등 범용 용어로 변경

---

## 3. 업계 표준 대비 Gap

### 3.1 Google SRE Postmortem 구조

| 항목 | Google SRE | 현재 구현 | Gap |
|------|-----------|----------|-----|
| Summary | ✅ | ✅ `summary` 필드 | - |
| Impact | ✅ | ✅ `affected_services` | - |
| Root Cause | ✅ | ❌ 없음 | 추가 필요 |
| Trigger | ✅ | ❌ 없음 | 추가 필요 |
| Resolution | ✅ | ❌ 없음 | 추가 필요 |
| Detection | ✅ | ❌ 없음 | 추가 필요 |
| Action Items | ✅ (추적 가능) | ❌ 하드코딩 | 동적 생성 필요 |
| Lessons Learned | ✅ | ❌ 없음 | 선택적 |
| Timeline | ✅ | ✅ `timeline` 필드 | - |

### 3.2 PagerDuty 자동화

| 항목 | PagerDuty | 현재 구현 | Gap |
|------|----------|----------|-----|
| Automated Timeline | ✅ | ✅ `_build_timeline()` | - |
| Sev-1/2 자동 생성 | ✅ | ❌ 수동 API | 자동 트리거 필요 |
| Collaborative Editing | ✅ | N/A | 범위 외 |

---

## 4. 확장 로드맵

### Phase 1: 도메인 프리 변환 (124-A)

**문서:** [125_POSTMORTEM_DOMAIN_FREE.md](125_POSTMORTEM_DOMAIN_FREE.md)

- Default 값 제거 또는 범용화
- Docstring 예시 범용화
- Settings에서 기본 서비스 목록 설정 가능하게

### Phase 2: duration_seconds 계산 (124-B)

**문서:** [126_POSTMORTEM_DURATION_CALC.md](126_POSTMORTEM_DURATION_CALC.md)

- 타임라인 첫 이벤트와 마지막 이벤트 간 시간 계산
- 인시던트 시작/종료 시점 정확히 기록

### Phase 3: 동적 Action Items (124-C)

**문서:** [127_POSTMORTEM_ACTION_ITEMS.md](127_POSTMORTEM_ACTION_ITEMS.md)

- `services/chaos/reports.py`의 `_generate_action_items()` 패턴 활용
- 실제 발생한 이벤트 기반 액션 아이템 생성

### Phase 4: 자동 트리거 (124-D)

**문서:** [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md)

- CB CLOSED 이벤트 시 자동 Post-mortem 생성
- `_on_circuit_breaker_closed()` 핸들러 확장
- Settings에서 자동 생성 활성화/비활성화

### Phase 5: Root Cause 필드 추가 (124-E) - 선택적

**문서:** [129_POSTMORTEM_ROOT_CAUSE.md](129_POSTMORTEM_ROOT_CAUSE.md)

- 타임라인에서 최초 장애 이벤트 추출
- CB OPEN 원인 분석

---

## 5. 구현 우선순위

| 순서 | Phase | 난이도 | 영향도 | 권장 |
|------|-------|--------|--------|------|
| 1 | Phase 1 (도메인 프리) | 쉬움 | 높음 | ✅ 필수 |
| 2 | Phase 2 (duration) | 쉬움 | 중간 | ✅ 필수 |
| 3 | Phase 3 (Action Items) | 중간 | 높음 | ✅ 권장 |
| 4 | Phase 4 (자동 트리거) | 중간 | 높음 | ✅ 권장 |
| 5 | Phase 5 (Root Cause) | 중간 | 중간 | ⚪ 선택 |

---

## 6. 관련 문서

- [116_XTEST_MODE_OVERVIEW.md](116_XTEST_MODE_OVERVIEW.md) - X-Test Mode 전체 개요
- [123_XTEST_AUDIT_INTEGRATION.md](123_XTEST_AUDIT_INTEGRATION.md) - X-Test Audit 통합
- [19_CHAOS_PROOF_ROADMAP.md](../19_CHAOS_PROOF_ROADMAP.md) - 원본 로드맵 문서

---

## 7. 변경 이력

| 버전 | 일자 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0 | 2026-01-27 | 초안 작성 | GitHub Copilot |

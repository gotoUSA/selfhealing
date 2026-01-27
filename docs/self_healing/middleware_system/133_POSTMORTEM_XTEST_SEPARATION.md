# 133. Postmortem X-Test 분리 계획

**문서 버전:** 1.0  
**작성일:** 2026-01-27  
**선행 문서:** [130_POSTMORTEM_AUDIT_INTEGRATION.md](130_POSTMORTEM_AUDIT_INTEGRATION.md)  
**상태:** 계획 수립

---

## 1. 배경

### 1.1 문제 정의

Post-mortem은 **실제 장애**에 대한 사후 분석 리포트이나, 현재 X-Test-Mode(카오스 테스트) 모듈에 구현되어 있음

### 1.2 업계 표준 (Google SRE)

| 항목 | Google SRE 표준 |
|------|----------------|
| 목적 | 실제 인시던트 문서화 및 재발 방지 |
| 트리거 조건 | User-visible downtime, data loss, on-call intervention, resolution time 초과 등 |
| 테스트 활용 | "Wheel of Misfortune" - 과거 실제 Post-mortem으로 훈련 |

### 1.3 현재 구현 상태

| 구성요소 | 위치 | 문제점 |
|---------|------|--------|
| PostmortemGeneratorView | `views/xtest/observability.py` | X-Test 헤더 필요 |
| 자동 트리거 핸들러 | `services/event_bus.py` | X-Test 헤더 불필요 (이미 분리) |
| 저장소 함수 | `views/xtest/base.py` | X-Test 모듈에 위치 |
| Settings | `settings/api_view.py` | `xtest_` 접두사 사용 |
| URL 경로 | `/xtest/generate-postmortem/` | X-Test 경로 사용 |

---

## 2. 목표

| 목표 | 설명 |
|------|------|
| Post-mortem을 실제 장애 전용으로 분리 | X-Test 헤더 없이 접근 가능 |
| 네이밍 정규화 | `xtest_` 접두사 제거 |
| URL 경로 변경 | `/postmortem/` 경로로 이동 |
| 저장소 분리 | X-Test 모듈에서 독립 |

---

## 3. 변경 대상 분석

### 3.1 파일별 변경 사항

| 파일 | 현재 | 변경 후 |
|------|------|--------|
| `views/xtest/observability.py` | Post-mortem View 포함 | Post-mortem View 제거 |
| `views/postmortem.py` | 없음 | **신규 생성** |
| `views/xtest/base.py` | 저장소 함수 포함 | 저장소 함수 제거 |
| `services/postmortem_store.py` | 없음 | **신규 생성** (저장소) |
| `services/event_bus.py` | xtest 모듈 import | postmortem 모듈 import |
| `settings/api_view.py` | `xtest_auto_postmortem_*` | `auto_postmortem_*` |
| `api/django/urls.py` | `/xtest/generate-postmortem/` | `/postmortem/generate/` |

### 3.2 이동 대상 View

| View | 현재 위치 | 이동 후 |
|------|----------|--------|
| `PostmortemGeneratorView` | `views/xtest/observability.py` | `views/postmortem.py` |
| `GetHealingIncidentsView` | `views/xtest/observability.py` | `views/postmortem.py` |

### 3.3 X-Test에 유지할 View

| View | 이유 |
|------|------|
| `HealingTimelineView` | 테스트용 타임라인 조회 |
| `BlastRadiusTestView` | 테스트용 격리 검증 |
| `MultiServiceBlastRadiusView` | 테스트용 매트릭스 |
| `RecordHealingEventView` | 테스트용 이벤트 주입 |

---

## 4. 저장소 분리

### 4.1 현재 저장소 위치

| 변수/함수 | 현재 위치 |
|----------|----------|
| `_healing_events` | `views/xtest/base.py` |
| `_healing_incidents` | `views/xtest/base.py` |
| `add_healing_event()` | `views/xtest/base.py` |
| `add_healing_incident()` | `views/xtest/base.py` |
| `get_healing_events()` | `views/xtest/base.py` |
| `get_healing_incidents()` | `views/xtest/base.py` |

### 4.2 분리 전략

| 항목 | 이동 후 |
|------|--------|
| `_healing_incidents` | `services/postmortem_store.py` |
| `add_healing_incident()` | `services/postmortem_store.py` |
| `get_healing_incidents()` | `services/postmortem_store.py` |
| `_healing_events` | X-Test에 유지 (테스트용) |

### 4.3 의존성 수정

| 파일 | 현재 import | 변경 후 |
|------|------------|--------|
| `event_bus.py` | `from views.xtest.base import add_healing_incident` | `from services.postmortem_store import add_healing_incident` |
| `observability.py` | `from .base import get_healing_incidents` | X-Test에서 제거됨 |

---

## 5. Settings 변경

### 5.1 네이밍 변경

| 현재 | 변경 후 |
|------|--------|
| `xtest_auto_postmortem_enabled` | `auto_postmortem_enabled` |
| `xtest_auto_postmortem_min_duration` | `auto_postmortem_min_duration` |
| `xtest_postmortem_history_limit` | `postmortem_history_limit` |

### 5.2 환경 변수 변경

| 현재 | 변경 후 |
|------|--------|
| `SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_ENABLED` | `SELFHEALING_API_VIEW_AUTO_POSTMORTEM_ENABLED` |
| `SELFHEALING_API_VIEW_XTEST_AUTO_POSTMORTEM_MIN_DURATION` | `SELFHEALING_API_VIEW_AUTO_POSTMORTEM_MIN_DURATION` |

---

## 6. URL 변경

### 6.1 엔드포인트 변경

| 현재 | 변경 후 |
|------|--------|
| `POST /xtest/generate-postmortem/` | `POST /postmortem/generate/` |
| `GET /xtest/healing-incidents/` | `GET /postmortem/incidents/` |

### 6.2 Deprecated 경로 (호환성)

| Deprecated 경로 | 리다이렉트 대상 | 제거 예정 |
|----------------|---------------|----------|
| `/xtest/generate-postmortem/` | `/postmortem/generate/` | 3개월 후 |
| `/xtest/healing-incidents/` | `/postmortem/incidents/` | 3개월 후 |

---

## 7. 보안 변경

### 7.1 현재 보안

| 항목 | 현재 |
|------|------|
| 인증 | `AllowAny` (X-Test 헤더로 대체) |
| X-Test 헤더 | 필수 |
| 프로덕션 차단 | O |

### 7.2 변경 후 보안

| 항목 | 변경 후 |
|------|--------|
| 인증 | `IsAuthenticated` 또는 Admin 권한 |
| X-Test 헤더 | 불필요 |
| 프로덕션 | 허용 (실제 장애용) |

---

## 8. 구현 Phase

### Phase 1: 저장소 분리

1. `services/postmortem_store.py` 생성
2. 인시던트 저장/조회 함수 이동
3. `event_bus.py` import 경로 수정

### Phase 2: View 분리

1. `views/postmortem.py` 생성
2. `PostmortemGeneratorView` 이동 (XTestModeMixin 제거)
3. `GetHealingIncidentsView` 이동
4. URL 경로 추가

### Phase 3: Settings 정규화

1. Settings 필드명 변경 (`xtest_` 접두사 제거)
2. 환경 변수명 변경
3. 기존 변수명 deprecated 처리

### Phase 4: X-Test 정리

1. `observability.py`에서 Post-mortem View 제거
2. Deprecated URL 경로 추가 (호환성)
3. 문서 업데이트

---

## 9. 마이그레이션 체크리스트

### 9.1 Phase 1

- [ ] `services/postmortem_store.py` 생성
- [ ] `_healing_incidents` 리스트 이동
- [ ] `add_healing_incident()` 함수 이동
- [ ] `get_healing_incidents()` 함수 이동
- [ ] `get_healing_incidents_count()` 함수 이동
- [ ] `event_bus.py` import 경로 수정
- [ ] 테스트 통과 확인

### 9.2 Phase 2

- [ ] `views/postmortem.py` 생성
- [ ] `PostmortemGeneratorView` 이동
- [ ] `XTestModeMixin` 제거
- [ ] 권한 클래스 변경 (`IsAuthenticated`)
- [ ] `GetHealingIncidentsView` 이동
- [ ] `urls.py`에 새 경로 추가
- [ ] 테스트 통과 확인

### 9.3 Phase 3

- [ ] Settings 필드명 변경
- [ ] 기존 필드명 deprecated alias 추가
- [ ] 환경 변수명 변경 문서화
- [ ] 테스트 통과 확인

### 9.4 Phase 4

- [ ] `observability.py`에서 View 제거
- [ ] `xtest/__init__.py` export 정리
- [ ] Deprecated URL 경로 추가
- [ ] 마이그레이션 가이드 문서화

---

## 10. 영향 분석

### 10.1 기존 기능

| 기능 | 영향 |
|------|------|
| 자동 Post-mortem 생성 | import 경로만 변경 |
| X-Test Blast Radius | 영향 없음 |
| X-Test 타임라인 | 영향 없음 |

### 10.2 API 호환성

| 항목 | 처리 |
|------|------|
| 기존 `/xtest/` 경로 | Deprecated 유지 (3개월) |
| 기존 Settings 변수명 | Deprecated alias 유지 |
| 기존 환경 변수 | Deprecated 유지 |

---

## 11. 관련 문서

- [116_XTEST_MODE_OVERVIEW.md](116_XTEST_MODE_OVERVIEW.md) - X-Test Mode 전체 개요
- [128_POSTMORTEM_AUTO_TRIGGER.md](128_POSTMORTEM_AUTO_TRIGGER.md) - 자동 트리거 구현
- [132_POSTMORTEM_PERSISTENCE.md](132_POSTMORTEM_PERSISTENCE.md) - 영속성 계획

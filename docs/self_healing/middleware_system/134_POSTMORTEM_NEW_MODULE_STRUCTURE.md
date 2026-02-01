# 134. Postmortem 신규 모듈 구조

**문서 버전:** 1.1
**작성일:** 2026-01-27
**수정일:** 2026-02-01
**선행 문서:** [133_POSTMORTEM_XTEST_SEPARATION.md](133_POSTMORTEM_XTEST_SEPARATION.md)
**상태:** ✅ 구현 완료

---

## 1. 목적

X-Test에서 분리된 Post-mortem 기능의 신규 모듈 구조 정의

---

## 2. 신규 파일 구조

```
packages/selfhealing-python/src/selfhealing/
├── api/django/views/
│   ├── postmortem.py           ← 신규: Post-mortem API Views
│   └── xtest/
│       └── observability.py    ← Post-mortem 관련 코드 제거됨
│
├── services/
│   └── postmortem_store.py     ← 신규: Post-mortem 저장소
│
└── settings/
    └── postmortem.py           ← 신규: Post-mortem Settings
```

---

## 3. 신규 파일 상세

### 3.1 services/postmortem_store.py

**목적:** Post-mortem 데이터 저장 및 조회

**이동 대상 (현재 위치: views/xtest/base.py):**

| 함수/변수 | 설명 |
|----------|------|
| `_healing_incidents` | 인시던트 리스트 |
| `_healing_incidents_lock` | Thread Lock |
| `add_healing_incident()` | 인시던트 추가 |
| `get_healing_incidents()` | 인시던트 조회 |
| `get_healing_incidents_count()` | 인시던트 개수 |

**추가 함수:**

| 함수 | 설명 |
|------|------|
| `get_incident_by_id()` | ID로 단일 조회 |
| `clear_incidents()` | 테스트용 초기화 |

### 3.2 api/django/views/postmortem.py

**목적:** Post-mortem 생성 및 조회 API

**이동 대상 (현재 위치: views/xtest/observability.py):**

| View | 설명 |
|------|------|
| `PostmortemGeneratorView` | Post-mortem 생성 |
| `GetHealingIncidentsView` | 인시던트 목록 조회 |

**변경 사항:**

| 항목 | 현재 | 변경 후 |
|------|------|--------|
| 상속 | `XTestModeMixin, APIView` | `APIView` |
| 권한 | `AllowAny` + X-Test 헤더 | `IsAuthenticated` |
| 헤더 검증 | `check_chaos_permission()` | 제거 |

**추가 View:**

| View | 설명 |
|------|------|
| `PostmortemDetailView` | 단일 Post-mortem 상세 조회 |

### 3.3 settings/postmortem.py

**목적:** Post-mortem 전용 Settings

**이동 대상 (현재 위치: settings/api_view.py):**

| 필드 | 현재 이름 | 변경 후 |
|------|----------|--------|
| history_limit | `xtest_postmortem_history_limit` | `history_limit` |
| auto_enabled | `xtest_auto_postmortem_enabled` | `auto_enabled` |
| auto_min_duration | `xtest_auto_postmortem_min_duration` | `auto_min_duration` |

**환경 변수 접두사:** `SELFHEALING_POSTMORTEM_`

**예시:**
```
SELFHEALING_POSTMORTEM_AUTO_ENABLED=true
SELFHEALING_POSTMORTEM_AUTO_MIN_DURATION=30
SELFHEALING_POSTMORTEM_HISTORY_LIMIT=100
```

---

## 4. 헬퍼 함수 이동

### 4.1 이동 완료: services/postmortem_store.py

| 함수 | 상태 | 설명 |
|------|------|------|
| `collect_service_states()` | ✅ 완료 | CB 상태에서 affected/unaffected 서비스 수집 |
| `build_timeline()` | ✅ 완료 | 이벤트 히스토리와 로컬 이벤트로 타임라인 구성 |
| `generate_postmortem_data()` | ✅ 완료 | Post-mortem 데이터 구조 생성 |

**Deprecated Aliases (하위 호환성):**
- `_collect_service_states` → `collect_service_states`
- `_build_timeline` → `build_timeline`
- `_generate_postmortem_data` → `generate_postmortem_data`

### 4.2 이동 이유

| 이유 | 설명 |
|------|------|
| 재사용성 | `event_bus.py` 자동 트리거에서도 사용 |
| 단일 책임 | View에서 비즈니스 로직 분리 |
| 테스트 용이성 | View 없이 함수 단위 테스트 가능 |

### 4.3 import 경로 변경

**views/postmortem.py, xtest/observability.py:**
```python
from selfhealing.services.postmortem_store import (
    collect_service_states as _collect_service_states,
    build_timeline as _build_timeline,
    generate_postmortem_data as _generate_postmortem_data,
)
```

**event_bus.py:**
```python
from selfhealing.services.postmortem_store import (
    add_healing_incident,
    build_timeline as _build_timeline,
    collect_service_states as _collect_service_states,
    generate_postmortem_data as _generate_postmortem_data,
)
```

---

## 5. URL 구조

### 5.1 신규 URL 경로

| 메서드 | 경로 | View | 설명 |
|--------|------|------|------|
| POST | `/postmortem/generate/` | `PostmortemGeneratorView` | Post-mortem 생성 |
| GET | `/postmortem/incidents/` | `GetHealingIncidentsView` | 목록 조회 |
| GET | `/postmortem/incidents/{id}/` | `PostmortemDetailView` | 상세 조회 |

### 5.2 URL 패턴 등록 위치

**파일:** `api/django/urls.py`

**추가 위치:** X-Test 경로와 별도 섹션

---

## 6. 의존성 변경

### 6.1 event_bus.py

**현재:**
- `from selfhealing.api.django.views.xtest.observability import ...`
- `from selfhealing.api.django.views.xtest.base import add_healing_incident`

**변경 후:**
- `from selfhealing.services.postmortem_store import ...`

### 6.2 views/xtest/__init__.py

**현재:** `PostmortemGeneratorView`, `GetHealingIncidentsView` export

**변경 후:** 해당 export 제거

---

## 7. 권한 설계

### 7.1 View별 권한

| View | 권한 | 이유 |
|------|------|------|
| `PostmortemGeneratorView` | `IsAuthenticated` | 실제 장애 기록 생성 |
| `GetHealingIncidentsView` | `IsAuthenticated` | 민감 정보 포함 가능 |
| `PostmortemDetailView` | `IsAuthenticated` | 상세 정보 조회 |

### 7.2 프로덕션 동작

| 항목 | X-Test (현재) | Post-mortem (변경 후) |
|------|--------------|---------------------|
| 프로덕션 접근 | 차단 | 허용 |
| 인증 | 헤더 기반 | 토큰/세션 기반 |
| 용도 | 테스트 | 실제 운영 |

---

## 8. 자동 트리거 수정

### 8.1 현재 코드 (event_bus.py)

**import 경로:**
```
from selfhealing.api.django.views.xtest.observability import (
    _collect_service_states,
    _build_timeline,
    _generate_postmortem_data,
)
from selfhealing.api.django.views.xtest.base import (
    add_healing_incident,
    collect_system_snapshot,
    get_healing_events,
)
```

### 8.2 변경 후

**import 경로:**
```
from selfhealing.services.postmortem_store import (
    add_healing_incident,
    generate_postmortem,
)
```

### 8.3 Settings 참조 변경

**현재:**
```
settings.xtest_auto_postmortem_enabled
settings.xtest_auto_postmortem_min_duration
```

**변경 후:**
```
from selfhealing.settings.postmortem import get_postmortem_settings
settings = get_postmortem_settings()
settings.auto_enabled
settings.auto_min_duration
```

---

## 9. 하위 호환성

### 9.1 Deprecated Alias

**Settings:**

| 신규 이름 | Deprecated Alias | 동작 |
|----------|-----------------|------|
| `auto_enabled` | `xtest_auto_postmortem_enabled` | 로그 경고 + 값 반환 |
| `auto_min_duration` | `xtest_auto_postmortem_min_duration` | 로그 경고 + 값 반환 |

### 9.2 Deprecated URL

| 신규 경로 | Deprecated 경로 | 제거 예정 |
|----------|----------------|----------|
| `/postmortem/generate/` | `/xtest/generate-postmortem/` | v4.0 |
| `/postmortem/incidents/` | `/xtest/healing-incidents/` | v4.0 |

---

## 10. 구현 순서

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|------|
| 1 | Settings 생성 | `settings/postmortem.py` | ✅ 완료 |
| 2 | 저장소 생성 | `services/postmortem_store.py` | ✅ 완료 |
| 3 | 헬퍼 함수 이동 | `services/postmortem_store.py` | ✅ 완료 |
| 4 | View 생성 | `views/postmortem.py` | ✅ 완료 |
| 5 | URL 등록 | `urls.py` | ✅ 완료 |
| 6 | event_bus.py 수정 | import 경로 변경 | ✅ 완료 |
| 7 | X-Test 정리 | export 제거, deprecated URL | ✅ 완료 |
| 8 | 테스트 작성 | 단위/통합 테스트 | ✅ 완료 (317 tests passed) |

---

## 11. 관련 문서

- [133_POSTMORTEM_XTEST_SEPARATION.md](133_POSTMORTEM_XTEST_SEPARATION.md) - 분리 계획
- [132_POSTMORTEM_PERSISTENCE.md](132_POSTMORTEM_PERSISTENCE.md) - 영속성 (다음 단계)
- [131_POSTMORTEM_NOTIFICATION.md](131_POSTMORTEM_NOTIFICATION.md) - 알림 연동

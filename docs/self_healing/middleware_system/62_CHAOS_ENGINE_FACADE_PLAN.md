# 62. Chaos Engine Facade 도입 계획서

| 항목 | 내용 |
|-----|------|
| 버전 | 1.1 |
| 작성일 | 2026-01-19 |
| 완료일 | 2026-01-19 |
| 상태 | ✅ 완료 |
| 우선순위 | 🟡 단기 |
| 예상 효과 | Chaos 서브시스템 결합도 감소, 테스트 용이성 향상 |

---

## 1. 현황 분석

### 1.1 Chaos 패키지 규모

| 항목 | 수치 |
|------|------|
| 전체 파일 수 | 30개 |
| 전체 라인 수 | 13,581줄 |
| 서브패키지 수 | 4개 (base, experiments, scheduler, safety_guard) |
| 대형 파일 (800줄+) | 4개 |

### 1.2 현재 진입점 분산 (코드 근거)

외부에서 Chaos 서브시스템 접근 시 **6개의 분산된 진입점** 사용:

| 진입점 함수 | 정의 위치 | 반환 타입 | 외부 호출 횟수 |
|------------|----------|----------|--------------|
| `get_chaos_scheduler` | scheduler/helpers.py | ChaosSchedulerService | 30+ |
| `get_safety_guard` | safety_guard/helpers.py | SafetyGuard | 15+ |
| `get_blast_radius_manager` | blast_radius.py | BlastRadiusManager | 12+ |
| `get_report_generator` | reports.py | ResilienceReportGenerator | 5+ |
| `get_blast_radius_analyzer` | blast_radius_analyzer.py | BlastRadiusAnalyzer | 8+ |
| `create_experiment` | experiments/__init__.py | ChaosExperiment | 내부 전용 |

### 1.3 외부 사용 패턴 (코드 근거)

**API Views (chaos/config_views.py):**
- 단일 View에서 3개의 서로 다른 get_* 함수 호출
- `get_safety_guard()`, `get_blast_radius_manager()`, `get_chaos_scheduler()`

**API Views (chaos/schedule_views.py):**
- ScheduleExecuteView에서 2개의 manager 동시 사용
- `get_blast_radius_manager()`, `get_chaos_scheduler()`

**API Views (chaos/safety_views.py):**
- SafetyCheckView에서 2개의 서비스 조합
- `get_safety_guard()`, `get_chaos_scheduler()`

### 1.4 내부 의존성 (코드 근거)

**ChaosSchedulerService (scheduler/service.py):**
- `get_blast_radius_manager()` 호출 (3곳)
- `get_safety_guard()` 호출 (3곳)
- `create_experiment()` 호출 (내부)

**결론:** `ChaosSchedulerService`가 이미 **사실상 Facade 역할**을 수행하고 있으나, 외부에서는 여전히 개별 진입점을 직접 호출함.

---

## 2. 문제점

### 2.1 API 일관성 부재

| 문제 | 현상 | 영향 |
|------|------|------|
| 진입점 분산 | 6개의 get_* 함수 | 어떤 것을 사용해야 할지 혼란 |
| Import 복잡성 | View마다 2-3개 import | 코드 중복 |
| Mock 어려움 | 테스트 시 여러 함수 patch 필요 | 테스트 복잡도 증가 |

### 2.2 실제 코드 예시 (문제 상황)

**현재 (config_views.py 패턴):**
- SafetyGuardConfigView: `get_safety_guard()` import 필요
- BlastRadiusPolicyView: `get_blast_radius_manager()` import 필요
- SchedulerConfigView: `get_chaos_scheduler()` import 필요

동일한 Chaos 도메인임에도 불구하고 **3개의 다른 모듈에서 import 필요**

### 2.3 테스트 시 Mock 복잡도

**현재 테스트 코드 (test_chaos_scheduler.py):**
- `patch('selfhealing.services.chaos.safety_guard.get_safety_guard')`
- `patch('selfhealing.services.chaos.blast_radius.get_blast_radius_manager')`
- `patch('selfhealing.services.chaos.scheduler.get_chaos_scheduler')`

**3개의 개별 patch 필요**

---

## 3. 해결 방안

### 3.1 선택지 비교

| 선택지 | 장점 | 단점 | 권장 |
|--------|------|------|------|
| A. 새 ChaosFacade 클래스 | 깔끔한 API | 기존 코드 변경 필요 | ⭐⭐ |
| B. ChaosSchedulerService 확장 | 기존 구조 활용 | 책임 과다 | ⭐ |
| C. 통합 get_chaos_engine() | 최소 변경 | 객체 남용 가능 | ⭐⭐⭐ |

### 3.2 권장안: C. 통합 get_chaos_engine() 함수

**이유:**
1. 기존 `ChaosSchedulerService`가 이미 내부 조율 수행
2. 새 클래스 없이 기존 구조 활용
3. 점진적 마이그레이션 가능

### 3.3 구현 방향

**services/chaos/__init__.py에 추가:**
- `get_chaos_engine()` 함수 신규
- 내부에서 lazy하게 모든 서브시스템 조합
- 단일 객체로 모든 Chaos 기능 접근 가능

**특성:**
- Scheduler 속성으로 실험 CRUD/실행
- Safety 속성으로 안전 검증
- BlastRadius 속성으로 폭발 반경 관리
- Reports 속성으로 리포트 생성

---

## 4. 상세 설계

### 4.1 ChaosEngine 인터페이스

| 속성/메서드 | 반환 타입 | 내부 호출 |
|------------|----------|----------|
| `.scheduler` | ChaosSchedulerService | `get_chaos_scheduler()` |
| `.safety_guard` | SafetyGuard | `get_safety_guard()` |
| `.blast_radius` | BlastRadiusManager | `get_blast_radius_manager()` |
| `.reports` | ResilienceReportGenerator | `get_report_generator()` |
| `.analyzer` | BlastRadiusAnalyzer | `get_blast_radius_analyzer()` |

### 4.2 Lazy 로딩 전략

각 속성은 **최초 접근 시에만 로딩**:
- `_scheduler: Optional[ChaosSchedulerService] = None`
- property getter에서 None 체크 후 생성

### 4.3 싱글톤 보장

- 모듈 레벨 `_chaos_engine_instance` 변수
- `get_chaos_engine()` 함수로 단일 인스턴스 반환
- `reset_chaos_engine()` 함수로 테스트 시 초기화

---

## 5. 마이그레이션 계획

### 5.1 Phase 1: Facade 추가 (Breaking Change 없음)

1. `ChaosEngine` 클래스 구현
2. `get_chaos_engine()` 함수 추가
3. 기존 개별 get_* 함수 **그대로 유지**

### 5.2 Phase 2: 점진적 마이그레이션 (선택적)

| 대상 | 변경 전 | 변경 후 |
|------|--------|--------|
| config_views.py | 3개 개별 import | 1개 Facade import |
| schedule_views.py | 2개 개별 import | 1개 Facade import |
| safety_views.py | 2개 개별 import | 1개 Facade import |

### 5.3 Phase 3: Deprecation (장기)

- 개별 get_* 함수에 DeprecationWarning 추가
- 마이그레이션 가이드 문서화

---

## 6. 예상 효과

### 6.1 정량적 효과

| 지표 | Before | After | 개선율 |
|------|--------|-------|-------|
| 외부 진입점 수 | 6개 | 1개 | -83% |
| View당 import 수 | 2-3개 | 1개 | -50~67% |
| 테스트 Mock 수 | 3개 | 1개 | -67% |

### 6.2 정성적 효과

- **API 일관성**: 단일 진입점으로 Chaos 도메인 접근
- **학습 곡선 감소**: 어떤 함수를 써야 할지 명확
- **테스트 단순화**: 단일 Facade mock으로 충분
- **확장성**: 새 기능 추가 시 Facade 속성만 추가

---

## 7. 위험 요소 및 완화

### 7.1 잠재 위험

| 위험 | 가능성 | 영향 | 완화 방안 |
|------|-------|------|----------|
| God Object 안티패턴 | 중간 | 중간 | 속성별 lazy 로딩, 책임 분리 유지 |
| 기존 코드 호환성 | 낮음 | 낮음 | 개별 함수 유지 (Phase 1) |
| 순환 의존성 | 낮음 | 높음 | Lazy import 패턴 적용 |

### 7.2 검증 계획

1. **단위 테스트**: ChaosEngine 각 속성 접근 테스트
2. **통합 테스트**: API View에서 Facade 사용 테스트
3. **회귀 테스트**: 기존 get_* 함수 동작 확인

---

## 8. 구현 체크리스트

- [x] `ChaosEngine` 클래스 구현
- [x] `get_chaos_engine()` 함수 구현
- [x] `reset_chaos_engine()` 함수 구현
- [x] `services/chaos/__init__.py`에 export 추가
- [x] 단위 테스트 작성 (12개 테스트)
- [x] 통합 테스트 작성 (4개 테스트)
- [x] 문서 업데이트
- [x] Git 커밋

---

## 9. 구현 결과

### 9.1 변경된 파일

| 파일 | 변경 내용 |
|------|----------|
| `packages/selfhealing-python/src/selfhealing/services/chaos/__init__.py` | ChaosEngine Facade 구현 |
| `tests/self_healing/chaos/test_chaos_engine_facade.py` | 테스트 코드 추가 |

### 9.2 구현 상세

**ChaosEngine 클래스:**
- 5개 서브시스템 속성 (scheduler, safety_guard, blast_radius, reports, analyzer)
- 모든 속성 lazy loading 적용
- `reset()` 메서드로 내부 상태 초기화

**싱글톤 함수:**
- `get_chaos_engine()`: 싱글톤 인스턴스 반환
- `reset_chaos_engine()`: 테스트용 초기화

### 9.3 테스트 결과

| 테스트 카테고리 | 결과 |
|----------------|------|
| 싱글톤 동작 | ✅ PASS |
| 속성 존재 확인 | ✅ PASS |
| Lazy loading 검증 | ✅ PASS |
| Scheduler 로딩 | ✅ PASS |
| SafetyGuard 로딩 | ✅ PASS |
| BlastRadiusManager 로딩 | ✅ PASS |
| ReportGenerator 로딩 | ✅ PASS |
| Analyzer 로딩 | ✅ PASS |
| Reset 기능 | ✅ PASS |
| 내부 Reset 기능 | ✅ PASS |
| 하위 호환성 | ✅ PASS |
| __all__ 내보내기 | ✅ PASS |
| 싱글톤 일관성 (통합) | ✅ 4개 PASS |

---

## 10. 참고 자료

| 문서 | 경로 |
|------|------|
| ChaosSchedulerService | `services/chaos/scheduler/service.py` |
| SafetyGuard | `services/chaos/safety_guard/guard.py` |
| BlastRadiusManager | `services/chaos/blast_radius.py` |
| Chaos Views | `api/django/views/chaos/*.py` |
| Phase 2 리팩토링 계획서 | `60_REFACTORING_PLAN_PHASE2.md` |

---

## 11. 부록: 기존 ChaosSchedulerService의 Facade 역할 증거

**scheduler/service.py 내부 의존성:**

| 메서드 | 호출하는 서브시스템 |
|--------|-------------------|
| `_check_blast_radius()` | `get_blast_radius_manager()` |
| `_validate_before_schedule()` | `get_safety_guard()` |
| `_validate_pre_flight()` | `get_safety_guard()`, `get_blast_radius_manager()` |

이미 `ChaosSchedulerService`가 3개의 서브시스템을 내부적으로 조율하고 있음.
→ `ChaosEngine`은 이를 **외부 API로 명시화**하는 역할

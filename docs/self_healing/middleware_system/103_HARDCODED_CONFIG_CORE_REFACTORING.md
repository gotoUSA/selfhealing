# 103. Core 모듈 하드코딩된 설정값 리팩토링 계획

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **관련 문서**: 102_HARDCODED_CONFIG_FINAL_AUDIT.md
- **대상 디렉토리**: `packages/selfhealing-python/src/selfhealing/core/`

---

## 1. 개요

Core 모듈에서 발견된 하드코딩된 설정값들을 Pydantic Settings 체계로 마이그레이션하는 상세 계획.

---

## 2. 대상 파일 및 설정값

### 2.1 runtime_feedback.py

**위치**: L86-90  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `MAX_CONSECUTIVE_FAILURES` | 3 | 최대 연속 실패 횟수 | `SELFHEALING_RUNTIME_MAX_CONSECUTIVE_FAILURES` |
| `POST_ROLLBACK_COOLDOWN` | 120 | 롤백 후 쿨다운 (초) | `SELFHEALING_RUNTIME_ROLLBACK_COOLDOWN` |
| `POST_ADJUSTMENT_WAIT` | 30 | 조정 후 대기 시간 (초) | `SELFHEALING_RUNTIME_ADJUSTMENT_WAIT` |

**구현 방안**:
- `settings/runtime_feedback.py` 신규 생성
- Pydantic BaseSettings 클래스로 정의
- 기존 클래스 상수를 settings getter로 교체

---

### 2.2 auto_rollback_guard.py

**위치**: L135-142  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `ERROR_RATE_MAJOR` | 0.1 | Major 등급 에러율 | `SELFHEALING_ROLLBACK_ERROR_RATE_MAJOR` |
| `ERROR_RATE_CRITICAL` | 0.3 | Critical 등급 에러율 | `SELFHEALING_ROLLBACK_ERROR_RATE_CRITICAL` |
| `LATENCY_MAJOR_MS` | 5000 | Major 레이턴시 (ms) | `SELFHEALING_ROLLBACK_LATENCY_MAJOR_MS` |
| `LATENCY_CRITICAL_MS` | 10000 | Critical 레이턴시 (ms) | `SELFHEALING_ROLLBACK_LATENCY_CRITICAL_MS` |
| `CONSECUTIVE_FAILURES_ALERT` | 3 | 알림 발생 실패 횟수 | `SELFHEALING_ROLLBACK_FAILURES_ALERT` |
| `CONSECUTIVE_FAILURES_EMERGENCY` | 5 | 긴급상태 실패 횟수 | `SELFHEALING_ROLLBACK_FAILURES_EMERGENCY` |

**구현 방안**:
- `settings/auto_rollback.py` 신규 생성
- 기존 상수를 property로 변경하여 settings에서 값을 가져옴

---

### 2.3 adaptive_jitter.py

**위치**: L43-46  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `ERROR_BUDGET_DANGER_THRESHOLD` | 0.2 | 에러 버짓 위험 임계값 | `SELFHEALING_JITTER_BUDGET_DANGER_THRESHOLD` |
| `ERROR_BUDGET_SAFE_THRESHOLD` | 0.5 | 에러 버짓 안전 임계값 | `SELFHEALING_JITTER_BUDGET_SAFE_THRESHOLD` |
| `LOAD_HIGH_THRESHOLD` | 0.8 | 고부하 임계값 | `SELFHEALING_JITTER_LOAD_HIGH_THRESHOLD` |
| `LOAD_LOW_THRESHOLD` | 0.3 | 저부하 임계값 | `SELFHEALING_JITTER_LOAD_LOW_THRESHOLD` |

**구현 방안**:
- 기존 `settings/jitter.py` 확장
- 새로운 threshold 필드 추가

---

### 2.4 safety_bounds.py

**위치**: L53-90  
**현재 구조**: 딕셔너리 기반 바운드 정의

| 설정 그룹 | min | max | max_change_per_cycle |
|----------|-----|-----|---------------------|
| timeout_ms | 100 | 30000 | 0.3 |
| max_retries | 0 | 10 | 0.5 |
| failure_threshold | 0.1 | 0.9 | 0.2 |
| backoff_factor | 0.01 | 1.0 | 0.5 |
| batch_size | 10 | 10000 | 0.2 |
| concurrency | 10 | 5000 | 0.3 |
| half_open_timeout_ms | 1000 | 60000 | 0.3 |
| success_threshold | 1 | 100 | 0.2 |

**구현 방안**:
- `settings/safety_bounds.py` 신규 생성
- 중첩된 Pydantic 모델로 각 바운드 그룹 정의
- 환경변수: `SELFHEALING_BOUNDS_{GROUP}_{FIELD}` 형식

---

### 2.5 state_cache.py

**위치**: L38-39  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `BASE_TTL` | 5.0 | 기본 TTL (초) | `SELFHEALING_STATE_CACHE_BASE_TTL` |
| `JITTER_RANGE` | 0.5 | 랜덤 지터 범위 | `SELFHEALING_STATE_CACHE_JITTER_RANGE` |

**구현 방안**:
- `settings/state_cache.py` 신규 생성

---

### 2.6 resource_monitor.py

**위치**: L41  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_SAFETY_MARGIN` | 0.15 | 기본 안전 마진 | `SELFHEALING_RESOURCE_SAFETY_MARGIN` |

**구현 방안**:
- 기존 core 관련 settings 모듈에 추가

---

### 2.7 apply_strategy.py

**위치**: L101-126  
**현재 구조**: ApplyPlan 객체 내 기본값

| 전략 | delay_seconds | 용도 |
|-----|--------------|------|
| IMMEDIATE | 10 | 즉시 적용 |
| GRADUAL | 10 | 점진적 적용 |
| CANARY_FIRST | 30 | 카나리 우선 |
| PEAK_AVOIDANCE | 30 | 피크 회피 |
| MAINTENANCE_WINDOW | 60 | 유지보수 윈도우 |
| STAGED_ROLLOUT | 30 | 단계적 롤아웃 |

**구현 방안**:
- `settings/apply_strategy.py` 신규 생성
- 전략별 딜레이 값을 환경변수로 설정 가능하게 함

---

### 2.8 decision_engine.py

**위치**: L119, L244-267  
**현재 구조**: 클래스 상수 및 조건문 내 매직 넘버

| 항목 | 현재 값 | 용도 |
|-----|--------|------|
| `MIN_CHANGE_RATIO` | 0.05 | 최소 변경 비율 |
| sample_confidence 매핑 | 0.3~0.9 | 샘플 수 기반 신뢰도 |
| stability_factor 매핑 | 0.7~1.0 | CV 기반 안정성 계수 |

**구현 방안**:
- `settings/decision_engine.py` 신규 생성
- confidence/stability 매핑은 nested settings로 정의

---

## 3. 신규 Settings 모듈 구조

```
settings/
├── runtime_feedback.py     # NEW
├── auto_rollback.py        # NEW
├── safety_bounds.py        # NEW
├── state_cache.py          # NEW
├── apply_strategy.py       # NEW
├── decision_engine.py      # NEW
├── jitter.py               # EXTEND
└── ... (기존 파일들)
```

---

## 4. 구현 순서

### Step 1: Settings 모듈 생성 (1-2일)
1. `settings/runtime_feedback.py` 생성
2. `settings/auto_rollback.py` 생성
3. `settings/safety_bounds.py` 생성
4. `settings/state_cache.py` 생성
5. `settings/apply_strategy.py` 생성
6. `settings/decision_engine.py` 생성

### Step 2: 기존 Settings 확장 (0.5일)
7. `settings/jitter.py`에 threshold 필드 추가

### Step 3: Core 모듈 리팩토링 (2-3일)
8. `core/runtime_feedback.py` - settings 연동
9. `core/auto_rollback_guard.py` - settings 연동
10. `core/adaptive_jitter.py` - settings 연동
11. `core/safety_bounds.py` - settings 연동
12. `core/state_cache.py` - settings 연동
13. `core/resource_monitor.py` - settings 연동
14. `core/apply_strategy.py` - settings 연동
15. `core/decision_engine.py` - settings 연동

### Step 4: 테스트 업데이트 (1일)
16. 단위 테스트에 환경변수 모킹 추가
17. 통합 테스트 검증

### Step 5: 문서화 (0.5일)
18. 환경변수 문서 업데이트
19. 마이그레이션 가이드 작성

---

## 5. 예상 소요 시간

| 단계 | 예상 소요 |
|-----|----------|
| Settings 모듈 생성 | 1-2일 |
| 기존 Settings 확장 | 0.5일 |
| Core 모듈 리팩토링 | 2-3일 |
| 테스트 업데이트 | 1일 |
| 문서화 | 0.5일 |
| **총계** | **5-7일** |

---

## 6. 위험 요소 및 완화 방안

| 위험 | 영향도 | 완화 방안 |
|-----|-------|----------|
| 기존 테스트 실패 | 높음 | 기본값을 현재 하드코딩된 값과 동일하게 설정 |
| 순환 임포트 | 중간 | settings는 최소 의존성으로 유지 |
| 런타임 오버헤드 | 낮음 | settings 인스턴스 캐싱 활용 |
| 환경변수 충돌 | 낮음 | `SELFHEALING_` 네임스페이스 일관 적용 |

---

## 7. 검증 체크리스트

- [x] 모든 신규 settings 모듈이 Pydantic v2 호환 (2026-01-25 완료)
- [x] 환경변수 없이 기본값으로 정상 동작 (2026-01-25 완료)
- [x] 환경변수 설정 시 값 오버라이드 확인 (2026-01-25 완료)
- [x] 신규 settings 단위 테스트 26개 통과 (2026-01-25 완료)
- [x] 기존 단위 테스트 100% 통과 (2026-01-25 완료)
- [x] 통합 테스트 통과 (2026-01-25 완료) - 121개 테스트 PASSED
- [x] mypy 타입 체크 통과 (2026-01-25 완료) - 신규 Settings 모듈 7개 에러 없음

---

## 8. 완료 내역

### Step 1 완료 (2026-01-25)

신규 Settings 모듈 6개 생성:

| 파일 | 환경변수 prefix | 주요 설정 |
|-----|----------------|----------|
| `settings/runtime_feedback.py` | `SELFHEALING_RUNTIME_` | max_consecutive_failures, rollback_cooldown, adjustment_wait |
| `settings/auto_rollback.py` | `SELFHEALING_ROLLBACK_` | error_rate_major/critical, latency_major/critical_ms, failures_alert/emergency |
| `settings/safety_bounds.py` | `SELFHEALING_BOUNDS_` | 8개 파라미터별 min/max/max_change |
| `settings/state_cache.py` | `SELFHEALING_STATE_CACHE_` | base_ttl, jitter_range |
| `settings/resource_monitor.py` | `SELFHEALING_RESOURCE_` | safety_margin, cpu_margin |
| `settings/apply_strategy.py` | `SELFHEALING_APPLY_` | config 타입별 delay, default_grace_timeout |
| `settings/decision_engine.py` | `SELFHEALING_DECISION_` | min_change_ratio, 신뢰도/안정성 매핑 |

### Step 2 완료 (2026-01-25)

기존 `settings/jitter.py` 확장:

| 추가 필드 | 기본값 | 설명 |
|----------|-------|------|
| `error_budget_danger_threshold` | 0.2 | 에러 버짓 위험 임계값 |
| `error_budget_safe_threshold` | 0.5 | 에러 버짓 안전 임계값 |
| `load_high_threshold` | 0.8 | 고부하 임계값 |
| `load_low_threshold` | 0.3 | 저부하 임계값 |

### 테스트 결과

- 테스트 파일: `packages/selfhealing-python/tests/unit/settings/test_core_module_settings.py`
- 총 26개 테스트 PASSED

### Step 3 완료 (2026-01-25)

Core 모듈 8개 리팩토링 완료:

| Core 모듈 | Settings 모듈 | 리팩토링 내용 |
|----------|--------------|--------------|
| `core/runtime_feedback.py` | `settings/runtime_feedback.py` | MAX_CONSECUTIVE_FAILURES, POST_ROLLBACK_COOLDOWN, POST_ADJUSTMENT_WAIT를 property로 변경 |
| `core/auto_rollback_guard.py` | `settings/auto_rollback.py` | ERROR_RATE_*, LATENCY_*_MS, CONSECUTIVE_FAILURES_* 상수를 property로 변경 |
| `core/adaptive_jitter.py` | `settings/jitter.py` | 임계값 상수를 classmethod로 변경하여 settings에서 조회 |
| `core/safety_bounds.py` | `settings/safety_bounds.py` | DEFAULT_BOUNDS 딕셔너리를 classmethod로 변경하여 settings에서 동적 로드 |
| `core/state_cache.py` | `settings/state_cache.py` | BASE_TTL, JITTER_RANGE를 classmethod로 변경 |
| `core/resource_monitor.py` | `settings/resource_monitor.py` | DEFAULT_SAFETY_MARGIN을 classmethod로 변경 (별도 settings 파일 분리) |
| `core/apply_strategy.py` | `settings/apply_strategy.py` | DEFAULT_APPLY_STRATEGIES를 함수로 변경하여 settings에서 delay 로드 |
| `core/decision_engine.py` | `settings/decision_engine.py` | MIN_CHANGE_RATIO를 property로, _calculate_confidence를 settings 메서드 활용으로 변경 |

### Step 3 테스트 결과

- 테스트 파일: `packages/selfhealing-python/tests/unit/core/test_core_settings_integration.py`
- 총 20개 테스트 PASSED (resource_monitor 환경변수 오버라이드 테스트 1개 추가)
- 기존 settings 테스트 26개도 여전히 PASSED

### Step 3 추가 리팩토링 (2026-01-25)

`resource_monitor.py`용 settings를 `state_cache.py`에서 분리:

**이유**:
- 의미적 결합도 없음 (캐시 TTL ↔ 메모리 안전 마진)
- 환경변수 혼란 방지 (`SELFHEALING_STATE_CACHE_RESOURCE_*` → `SELFHEALING_RESOURCE_*`)
- 향후 CPU/디스크 마진 등 확장 용이

| 신규 파일 | 환경변수 prefix | 설정 |
|----------|----------------|------|
| `settings/resource_monitor.py` | `SELFHEALING_RESOURCE_` | safety_margin (0.15), cpu_margin (0.10) |

### Step 4 완료 (2026-01-25)

통합 테스트 추가 및 검증 완료:

**신규 통합 테스트 파일**:
- `packages/selfhealing-python/tests/integration/test_core_settings_env_override.py`

**테스트 범위 (19개 테스트)**:

| 테스트 클래스 | 검증 내용 |
|-------------|---------|
| `TestRuntimeFeedbackEnvOverride` | MAX_CONSECUTIVE_FAILURES, ROLLBACK_COOLDOWN 환경변수 오버라이드 → RuntimeFeedbackLoop 반영 |
| `TestAutoRollbackGuardEnvOverride` | ERROR_RATE_*, LATENCY_*, FAILURES_* 환경변수 오버라이드 → AutoRollbackGuard 반영 |
| `TestAdaptiveJitterEnvOverride` | error_budget_*_threshold, load_*_threshold 환경변수 → jitter 범위 선택 로직 변경 |
| `TestSafetyBoundsEnvOverride` | BOUNDS_TIMEOUT_MS_* 환경변수 → clamp_to_bounds 동작 변경 |
| `TestStateCacheEnvOverride` | STATE_CACHE_BASE_TTL, JITTER_RANGE 환경변수 → TTL 계산 범위 변경 |
| `TestResourceMonitorEnvOverride` | RESOURCE_SAFETY_MARGIN 환경변수 → 메모리 계산 반영 |
| `TestApplyStrategyEnvOverride` | APPLY_*_DELAY 환경변수 → get_default_apply_config 반영 |
| `TestDecisionEngineEnvOverride` | DECISION_MIN_CHANGE_RATIO, CONFIDENCE_*, STABILITY_* 환경변수 → 결정 로직 반영 |
| `TestMultipleCoreModulesEnvOverride` | 8개 Settings 모듈 독립 동작 검증, 교차 오염 없음 확인 |

**버그 수정**:
- `core/safety_bounds.py`: `reset_to_defaults()` 메서드에서 `DEFAULT_BOUNDS` → `_get_default_bounds()` 수정

**전체 테스트 결과**:
- 통합 테스트: 75개 PASSED (기존 56개 + 신규 19개)
- 단위 테스트: 46개 PASSED
- 합계: 121개 PASSED

# X-Test Emergency Recovery Flow 시나리오

**문서 번호:** 141  
**작성일:** 2026-01-27  
**상태:** 구현 완료 ✅  
**구현일:** 2026-01-28  
**선행 문서:** 140_XTEST_REGIONAL_BOUNDARY.md, 77_RECOVERY_COORDINATOR.md, 74_CANARY_SAFETY_INTERLOCK.md

---

## 1. 목적

Emergency LEVEL_3 상황에서 SafetyInterlock에 의한 Canary 롤백과 RecoveryCoordinator의 4단계 역순 복구가 정상 동작하는지 X-Test 통합 시나리오로 검증한다.

### 1.1 검증 대상

| 문서 | 컴포넌트 | 검증 항목 |
|------|---------|----------|
| 74번 | `CanarySafetyInterlock` | LEVEL_3 시 자동 ROLLBACK |
| 77번 | `RecoveryCoordinator` | 4단계 역순 복구 |

### 1.2 4단계 역순 복구

| 순서 | Step Type | 설명 |
|------|-----------|------|
| 1 | BUDGET_RESET | Budget Multiplier 5.0x → 1.0x |
| 2 | HEALTH_CHECK | 5분간 안정화 확인 |
| 3 | CANARY_RESUME | 일시 중지된 롤아웃 재개 |
| 4 | GOVERNANCE_NORMAL | STRICT → NORMAL 전환 |

---

## 2. 현재 상태 분석

### 2.1 기존 통합 시나리오

| 파일 | 시나리오 | 설명 |
|------|---------|------|
| `integration_scenarios.py` | `cb_open_dlq_flow` | CB Open → DLQ |
| `integration_scenarios.py` | `full_recovery_cycle` | CB 기반 복구 (Emergency 미포함) |

### 2.2 RecoveryCoordinator 메서드

| 파일 | 메서드 | 역할 |
|------|--------|------|
| `services/recovery_coordinator.py` | `start_recovery()` | 복구 세션 시작 |
| `services/recovery_coordinator.py` | `execute_next_step()` | 다음 단계 실행 |
| `services/recovery_coordinator.py` | `_handle_budget_reset()` | Step 1 핸들러 |
| `services/recovery_coordinator.py` | `_handle_health_check()` | Step 2 핸들러 |
| `services/recovery_coordinator.py` | `_handle_canary_resume()` | Step 3 핸들러 |
| `services/recovery_coordinator.py` | `_handle_governance_normal()` | Step 4 핸들러 |

### 2.3 SafetyInterlock 메서드

| 파일 | 메서드 | 역할 |
|------|--------|------|
| `services/safety_interlock.py` | `check()` | Emergency Level 기반 체크 |
| `services/safety_interlock.py` | `check_and_apply()` | 체크 후 자동 적용 |

### 2.4 Emergency Level 정책

| Level | InterlockAction |
|-------|-----------------|
| NORMAL (0) | ALLOW |
| LEVEL_1 (1) | ALLOW_WITH_WARNING |
| LEVEL_2 (2) | PAUSE |
| LEVEL_3 (3) | ROLLBACK |

---

## 3. 설계

### 3.1 시나리오 정의

**시나리오 ID:** `full_emergency_recovery_flow`

**단계 구성:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | 초기 상태 확인 | Emergency: NORMAL |
| 2 | Emergency LEVEL_3 주입 | state: LEVEL_3 |
| 3 | SafetyInterlock 체크 | action: ROLLBACK |
| 4 | Canary ROLLBACK 확인 | rollback_triggered: true |
| 5 | RecoveryCoordinator.start_recovery() | session_id 반환 |
| 6 | Step 1: BUDGET_RESET 실행 | multiplier: 1.0x |
| 7 | Step 2: HEALTH_CHECK 실행 | health_passed: true |
| 8 | Step 3: CANARY_RESUME 실행 | canary_resumed: true |
| 9 | Step 4: GOVERNANCE_NORMAL 실행 | governance: NORMAL |
| 10 | 최종 상태 확인 | 모든 컴포넌트 정상 |

### 3.2 시간 시뮬레이션

| 실제 대기 | X-Test 옵션 |
|----------|------------|
| HEALTH_CHECK 5분 | `skip_wait: true` 또는 `wait_seconds: 5` |
| Step 간 대기 | 최소화 (테스트 속도 향상) |

### 3.3 추가 시나리오

**시나리오 ID:** `safety_interlock_canary_rollback`

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | Canary 롤아웃 진행 중 시뮬레이션 | canary_active: true |
| 2 | Emergency LEVEL_2 주입 | state: LEVEL_2 |
| 3 | SafetyInterlock.check_and_apply() | action: PAUSE |
| 4 | Canary 일시 중지 확인 | canary_paused: true |
| 5 | Emergency LEVEL_3 에스컬레이션 | state: LEVEL_3 |
| 6 | SafetyInterlock.check_and_apply() | action: ROLLBACK |
| 7 | Canary 롤백 확인 | canary_rollback: true |

---

## 4. 구현 순서

### Step 1: 시나리오 클래스 추가

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `FullEmergencyRecoveryScenario` 클래스 생성 |
| 1-2 | `IntegrationScenario` 상속 |
| 1-3 | 10단계 시나리오 로직 구현 |

### Step 2: 시간 시뮬레이션 옵션

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `skip_wait` 파라미터 추가 |
| 2-2 | `wait_seconds` 커스텀 대기 시간 |
| 2-3 | HEALTH_CHECK 핸들러에 옵션 전달 |

### Step 3: SafetyInterlock 연동

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `CanarySafetyInterlock` import |
| 3-2 | `check_and_apply()` 호출 |
| 3-3 | 결과 타임라인에 기록 |

### Step 4: RecoveryCoordinator 연동

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 4-1 | `RecoveryCoordinator` import |
| 4-2 | `start_recovery()` 호출 |
| 4-3 | `execute_next_step()` 4회 호출 |
| 4-4 | 각 단계 결과 검증 |

### Step 5: 시나리오 레지스트리 등록

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `SCENARIO_REGISTRY`에 추가 |
| 5-2 | `full_emergency_recovery_flow` 키 |
| 5-3 | `safety_interlock_canary_rollback` 키 |

### Step 6: 테스트 작성

**파일:** `tests/self_healing/api/test_xtest_integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 6-1 | `test_full_emergency_recovery_flow` |
| 6-2 | `test_safety_interlock_canary_rollback` |
| 6-3 | Mock 설정 (RecoveryCoordinator, SafetyInterlock) |

---

## 5. 요청/응답 형식

### 5.1 요청

| 필드 | 타입 | 설명 |
|------|------|------|
| `scenario` | string | `full_emergency_recovery_flow` |
| `service_name` | string | 테스트 대상 서비스 |
| `config.skip_wait` | bool | HEALTH_CHECK 대기 스킵 |
| `config.wait_seconds` | int | 커스텀 대기 시간 |

### 5.2 응답

| 필드 | 설명 |
|------|------|
| `scenario_id` | 실행 ID |
| `status` | `completed` / `failed` |
| `steps` | 10단계 상세 결과 |
| `timeline` | 이벤트 타임라인 |
| `recovery_session_id` | RecoveryCoordinator 세션 ID |
| `snapshot` | 최종 시스템 상태 |

---

## 6. 테스트 계획

### 6.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_level3_triggers_rollback` | LEVEL_3 → ROLLBACK 액션 |
| `test_recovery_steps_in_order` | 4단계 순차 실행 |
| `test_skip_wait_option` | 대기 스킵 동작 |

### 6.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 전체 10단계 시나리오 실행 → 성공 |
| Step 중간 실패 → 적절한 에러 반환 |
| 복구 세션 중복 시작 → 기존 세션 반환 |

---

## 7. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/recovery_coordinator.py` | `RecoveryCoordinator` 클래스 |
| `services/safety_interlock.py` | `CanarySafetyInterlock` 클래스 |
| `services/namespace_emergency/tracker.py` | Emergency Level 관리 |
| `api/django/views/xtest/integration_scenarios.py` | 기존 시나리오 패턴 |

---

## 8. 구현 결과

### 8.1 구현 파일

| 파일 | 내용 |
|------|------|
| `api/django/views/xtest/integration_scenarios.py` | `FullEmergencyRecoveryScenario`, `SafetyInterlockCanaryRollbackScenario` 클래스 추가 |
| `tests/unit/api/test_xtest_emergency_recovery_scenario.py` | 26개 단위 테스트 |

### 8.2 테스트 결과

```
===================================== 26 passed in 3.35s ======================================
```

| 테스트 클래스 | 테스트 케이스 수 | 결과 |
|--------------|----------------|------|
| `TestFullEmergencyRecoveryScenario` | 11 | ✅ 통과 |
| `TestSafetyInterlockCanaryRollbackScenario` | 8 | ✅ 통과 |
| `TestScenarioRegistry` | 3 | ✅ 통과 |
| `TestInterlockActionMapping` | 2 | ✅ 통과 |
| `TestRecoveryStepTypes` | 2 | ✅ 통과 |

### 8.3 구현된 시나리오

| 시나리오 ID | 설명 | 단계 수 |
|------------|------|--------|
| `full_emergency_recovery_flow` | Emergency LEVEL_3 → SafetyInterlock 롤백 → 4단계 역순 복구 | 10 |
| `safety_interlock_canary_rollback` | LEVEL_2→PAUSE→LEVEL_3→ROLLBACK 에스컬레이션 | 7 |

---

**다음 문서:** 142_XTEST_CAUSATION_ID_PREFIX.md

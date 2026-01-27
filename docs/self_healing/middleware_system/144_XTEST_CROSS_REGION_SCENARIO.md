# X-Test Cross-Region Conflict Scenario

**문서 번호:** 144  
**작성일:** 2026-01-27  
**상태:** 설계 완료  
**선행 문서:** 143_XTEST_RESOURCE_AWARE_INTERLOCK.md, 73_REGION_ISOLATION.md

---

## 1. 목적

특정 리전만 STRICT 모드인 상황을 X-Test로 시뮬레이션하고, `get_effective_state()` 함수가 Regional 우선순위를 정확히 채택하는지 검증하는 통합 시나리오를 추가한다.

### 1.1 검증 대상

| 문서 | 컴포넌트 | 검증 항목 |
|------|---------|----------|
| 73번 | `AtomicStateQuery` | Global vs Regional 우선순위 |
| 73번 | `get_effective_state()` | 상태 결정 로직 |
| 73번 | Admin Override | ADMIN_OVERRIDE 시 Regional 우선 |

### 1.2 우선순위 규칙 (73번 문서)

| 상황 | 결과 |
|------|------|
| Global STRICT | 모든 리전 STRICT (오버라이드) |
| Global NORMAL + Regional STRICT | Regional STRICT 유지 |
| Global STRICT + Regional ADMIN_OVERRIDE | Regional 우선 (Admin 승리) |

---

## 2. 현재 상태 분석

### 2.1 기존 단위 테스트

| 파일 | 테스트 | 검증 내용 |
|------|--------|----------|
| `test_governance_storage.py` | `test_global_strict_overrides_regional` | Global STRICT 오버라이드 |
| `test_governance_storage.py` | `test_regional_strict_takes_priority` | Regional STRICT 우선 |
| `test_atomic_state_query.py` | `test_admin_override_regional` | Admin Override |

### 2.2 기존 X-Test 통합 시나리오

| 파일 | 현재 상태 |
|------|----------|
| `integration_scenarios.py` | 리전 관련 시나리오 없음 |

### 2.3 관련 코드

| 파일 | 함수/클래스 | 역할 |
|------|------------|------|
| `services/governance/storage.py` | `get_effective_state()` | 상태 결정 |
| `services/governance/atomic_state_query.py` | `AtomicStateQuery` | 원자적 상태 조회 |
| `settings/namespace.py` | `SELFHEALING_REGION` | 리전 설정 |

---

## 3. 설계

### 3.1 시나리오 정의

**시나리오 ID:** `regional_override_conflict`

**단계 구성:**

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | 초기 상태 확인 | Global: NORMAL, Regional: NORMAL |
| 2 | Regional STRICT 설정 | Regional: STRICT |
| 3 | `get_effective_state()` 호출 | STRICT (Regional 우선) |
| 4 | Global STRICT 설정 | Global: STRICT |
| 5 | `get_effective_state()` 호출 | STRICT (Global 오버라이드) |
| 6 | Regional ADMIN_OVERRIDE 설정 | Regional: ADMIN_OVERRIDE |
| 7 | `get_effective_state()` 호출 | NORMAL (Admin 승리) |
| 8 | 상태 원복 | 모든 상태 NORMAL |

### 3.2 추가 시나리오

**시나리오 ID:** `multi_region_isolation_test`

| Step | 동작 | 예상 결과 |
|------|------|----------|
| 1 | 현재 리전 확인 | region: seoul |
| 2 | seoul 리전 STRICT 설정 | seoul: STRICT |
| 3 | 다른 리전 상태 확인 (mock) | tokyo: NORMAL |
| 4 | RegionalIsolationGate 체크 | seoul만 격리됨 |
| 5 | 격리 해제 | seoul: NORMAL |

---

## 4. 구현 순서

### Step 1: 시나리오 클래스 추가

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 1-1 | `RegionalOverrideConflictScenario` 클래스 생성 |
| 1-2 | `IntegrationScenario` 상속 |
| 1-3 | 8단계 시나리오 로직 구현 |

### Step 2: 상태 설정 헬퍼

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 2-1 | `_set_global_state()` 헬퍼 메서드 |
| 2-2 | `_set_regional_state()` 헬퍼 메서드 |
| 2-3 | `_set_admin_override()` 헬퍼 메서드 |

### Step 3: get_effective_state() 연동

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 3-1 | `AtomicStateQuery` import |
| 3-2 | `get_effective_state()` 호출 |
| 3-3 | 결과 검증 및 타임라인 기록 |

### Step 4: 시나리오 레지스트리 등록

**파일:** `api/django/views/xtest/integration_scenarios.py`

| 순서 | 항목 |
|------|------|
| 4-1 | `SCENARIO_REGISTRY`에 추가 |
| 4-2 | `regional_override_conflict` 키 |
| 4-3 | `multi_region_isolation_test` 키 |

### Step 5: 테스트 작성

**파일:** `tests/self_healing/api/test_xtest_regional_scenarios.py`

| 순서 | 항목 |
|------|------|
| 5-1 | `test_regional_override_conflict` |
| 5-2 | `test_admin_override_wins` |
| 5-3 | Mock 설정 (Redis, RegionalGate) |

---

## 5. 요청/응답 형식

### 5.1 요청

| 필드 | 타입 | 설명 |
|------|------|------|
| `scenario` | string | `regional_override_conflict` |
| `service_name` | string | 테스트 대상 서비스 |
| `config.target_region` | string | 타겟 리전 (기본: 현재 리전) |

### 5.2 응답

| 필드 | 설명 |
|------|------|
| `scenario_id` | 실행 ID |
| `status` | `completed` / `failed` |
| `steps` | 8단계 상세 결과 |
| `timeline` | 이벤트 타임라인 |
| `state_transitions` | 상태 전환 이력 |
| `snapshot` | 최종 시스템 상태 |

---

## 6. 상태 전환 매트릭스

### 6.1 검증 매트릭스

| Global | Regional | Admin Override | 예상 결과 |
|--------|----------|----------------|----------|
| NORMAL | NORMAL | OFF | NORMAL |
| NORMAL | STRICT | OFF | STRICT |
| STRICT | NORMAL | OFF | STRICT |
| STRICT | STRICT | OFF | STRICT |
| STRICT | NORMAL | ON | NORMAL |
| STRICT | STRICT | ON | STRICT |

### 6.2 시나리오 커버리지

| 매트릭스 케이스 | 시나리오 |
|----------------|----------|
| Global NORMAL + Regional STRICT | Step 2-3 |
| Global STRICT 오버라이드 | Step 4-5 |
| Admin Override 승리 | Step 6-7 |

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 테스트 케이스 | 검증 항목 |
|--------------|----------|
| `test_regional_strict_priority` | Regional STRICT 우선 |
| `test_global_strict_override` | Global STRICT 오버라이드 |
| `test_admin_override_wins` | Admin Override 승리 |
| `test_state_restoration` | 상태 원복 |

### 7.2 통합 테스트

| 테스트 시나리오 |
|----------------|
| 전체 8단계 시나리오 실행 → 성공 |
| 각 상태 전환 시점에서 `get_effective_state()` 정확성 확인 |
| 상태 원복 후 모든 리전 NORMAL 확인 |

---

## 8. 모니터링

### 8.1 메트릭

| 메트릭 | 설명 |
|--------|------|
| `xtest_regional_scenario_runs_total` | 리전 시나리오 실행 횟수 |
| `xtest_state_transitions_total` | 상태 전환 횟수 |

### 8.2 Audit 로깅

| 이벤트 | 로그 내용 |
|--------|----------|
| 상태 변경 | previous_state, new_state, region |
| 시나리오 완료 | steps_passed, steps_failed |

---

## 9. 관련 코드 참조

| 파일 | 참조 내용 |
|------|----------|
| `services/governance/storage.py` | `get_effective_state()` |
| `services/governance/atomic_state_query.py` | `AtomicStateQuery` |
| `services/isolation/regional_gate.py` | `RegionalIsolationGate` |
| `test_governance_storage.py` | 단위 테스트 패턴 |
| `api/django/views/xtest/integration_scenarios.py` | 기존 시나리오 패턴 |

---

**다음 문서:** 145_XTEST_CASCADE_EVENT_IS_TEST.md

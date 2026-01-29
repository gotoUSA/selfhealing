# 154. Adaptive Throttling - Emergency Mode 연동 설계

## 개요

Adaptive Throttling과 Emergency Mode Manager를 연동하여 비상 상황 시 트래픽을 자동으로 제한합니다.
Emergency Level이 상승하면 Throttle limit을 단계적으로 감소시켜 시스템 부하를 줄이고,
Level이 정상화되면 limit을 복구합니다.

## 현재 Emergency Mode 구조 분석

### EmergencyLevel 정의

파일 위치: `selfhealing/services/emergency_mode/enums.py`

| 레벨 | 값 | 설명 |
|-----|---|------|
| `NORMAL` | 0 | 정상 운영 (모든 트래픽 허용) |
| `LEVEL_1` | 1 | 경미한 장애 - Non-Essential 차단 |
| `LEVEL_2` | 2 | 중간 장애 - Standard 10%만, Non-Essential 차단 |
| `LEVEL_3` | 3 | 심각한 장애 - Critical 50%만 허용 |

### EMERGENCY_LEVEL_RULES

각 레벨별 티어 트래픽 배율:

| 레벨 | critical | standard | non_essential |
|-----|----------|----------|---------------|
| NORMAL | 1.0 | 1.0 | 1.0 |
| LEVEL_1 | 1.0 | 1.0 | 0.0 |
| LEVEL_2 | 1.0 | 0.1 | 0.0 |
| LEVEL_3 | 0.5 | 0.0 | 0.0 |

---

## GracefulDegradationManager 핵심 기능

파일 위치: `selfhealing/services/emergency_mode/manager.py`

### 주요 메서드

| 메서드 | 기능 | Throttle 연동 가능성 |
|--------|------|-------------------|
| `get_state()` | 현재 상태 조회 | 상태 확인 후 limit 결정 |
| `get_current_level()` | 현재 레벨 조회 | 레벨별 limit 배율 적용 |
| `is_active()` | 활성화 여부 | 활성 시 limit 제한 |
| `get_tier_multiplier(tier_id)` | 티어별 배율 조회 | 배율 기반 limit 계산 |
| `activate_manual()` | 수동 활성화 | 즉시 limit 감소 |
| `activate_auto()` | 자동 활성화 | 자동 limit 감소 |
| `deactivate()` | 비활성화 | limit 복구 |

### 상태 캐싱 및 동기화

| 기능 | 설명 |
|-----|------|
| Check on Use 패턴 | TTL 기반 캐시, 필요시 StateBackend 재조회 |
| 캐시 TTL | 30초 (`_cache_ttl_seconds`) |
| Drift Detection | 캐시와 백엔드 상태 불일치 감지 |
| 이벤트 기반 무효화 | `_on_external_level_changed()` 핸들러 |

### Before Mutation Snapshot

상태 변경 전 스냅샷 저장 (롤백 지원):
- `_save_previous_state(action)` 메서드
- 최대 10개 스냅샷 유지
- `rollback_to_previous(index)` 메서드로 롤백

---

## EventBus 연동 현황

### 발행되는 이벤트

Emergency Mode가 발행하는 이벤트:

| 이벤트 | 발행 시점 | 데이터 |
|-------|---------|-------|
| `EMERGENCY_LEVEL_CHANGED` | 레벨 변경 시 | `new_level`, `previous_level`, `reason` |
| `EMERGENCY_ACTIVATED` | 활성화 시 | 상태 정보 |
| `EMERGENCY_DEACTIVATED` | 비활성화 시 | 상태 정보 |
| `EMERGENCY_RECOVERY_STARTED` | 복구 시작 | 복구 정보 |
| `EMERGENCY_RECOVERY_COMPLETED` | 복구 완료 | 결과 정보 |

### 이벤트 발행 코드 위치

`_emit_level_changed_event()` 메서드:
- `activate_manual()` 내에서 호출
- `activate_auto()` 내에서 호출
- `deactivate()` 내에서 호출

---

## Throttle ↔ Emergency Mode 연동 설계

### Emergency Level별 Throttle 배율

| Emergency Level | Throttle Limit 배율 | 계산식 |
|-----------------|-------------------|--------|
| NORMAL (0) | 1.0 | `initial_limit` |
| LEVEL_1 (1) | 0.8 | `initial_limit × 0.8` |
| LEVEL_2 (2) | 0.5 | `initial_limit × 0.5` |
| LEVEL_3 (3) | min_limit 고정 | `min_limit` |

### 배율 결정 로직

기존 `EMERGENCY_LEVEL_RULES`의 티어 배율 활용:

| Emergency Level | 티어 배율 평균 | Throttle 적용 배율 |
|-----------------|--------------|------------------|
| NORMAL | (1.0 + 1.0 + 1.0) / 3 = 1.0 | 1.0 |
| LEVEL_1 | (1.0 + 1.0 + 0.0) / 3 = 0.67 | 0.8 (보수적) |
| LEVEL_2 | (1.0 + 0.1 + 0.0) / 3 = 0.37 | 0.5 (보수적) |
| LEVEL_3 | (0.5 + 0.0 + 0.0) / 3 = 0.17 | min_limit |

---

## 연동 지점

### 1. 이벤트 핸들러 방식 (권장)

EventBus를 통한 느슨한 결합:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      Event-Driven Integration                        │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────┐    publish    ┌──────────────────┐            │
│  │ Emergency Mode   │──────────────►│    EventBus      │            │
│  │ Manager          │               │                  │            │
│  └──────────────────┘               └────────┬─────────┘            │
│                                              │                       │
│                                              │ EMERGENCY_LEVEL_CHANGED
│                                              │                       │
│                                              ▼                       │
│                                     ┌──────────────────┐            │
│                                     │ _on_emergency_   │            │
│                                     │ level_changed_   │            │
│                                     │ throttle()       │            │
│                                     └────────┬─────────┘            │
│                                              │                       │
│                                              ▼                       │
│                                     ┌──────────────────┐            │
│                                     │ AdaptiveThrottle │            │
│                                     │ adjust_for_      │            │
│                                     │ emergency()      │            │
│                                     └──────────────────┘            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2. 직접 조회 방식 (Fallback)

EventBus 사용 불가 시:

| 시점 | 조회 메서드 | 동작 |
|-----|-----------|------|
| Throttle 초기화 | `get_emergency_manager()` | 현재 레벨 확인 |
| `check()` 호출 시 | `get_current_level()` | 레벨 기반 limit 조정 |
| `record_response()` 후 | `is_active()` | 활성 시 추가 제한 |

---

## 핸들러 구현 명세

### `_on_emergency_level_changed_throttle()` 핸들러

| 항목 | 내용 |
|-----|------|
| 구독 이벤트 | `EMERGENCY_LEVEL_CHANGED` |
| 우선순위 | `HIGH` |
| 동작 | Emergency Level에 따라 Throttle limit 조정 |

핸들러가 받는 이벤트 데이터:

| 필드 | 타입 | 설명 |
|-----|------|------|
| `new_level` | int | 새 Emergency Level (0-3) |
| `previous_level` | int | 이전 Emergency Level |
| `reason` | str | 변경 사유 |
| `is_chaos_experiment` | bool | 카오스 실험 여부 (metadata) |

### 핸들러 동작 로직

```
1. 이벤트에서 new_level 추출
2. level → limit 배율 매핑
3. 현재 Throttle limit 조회
4. 새 limit 계산: base_limit × 배율
5. min_limit ~ max_limit 범위 제한
6. limit 적용
7. 감사 로그 기록
```

---

## Chaos-Aware 처리

### 카오스 실험 시 예외 처리

Emergency Mode metadata에 `is_chaos_experiment` 플래그 존재:

| 상황 | Throttle 동작 |
|-----|-------------|
| 일반 Emergency | limit 즉시 조정 |
| Chaos Experiment | limit 조정 + 별도 태깅 |
| Chaos 종료 | 이전 limit으로 복구 |

### 메타데이터 활용

Emergency State의 metadata 필드:

| 필드 | 설명 |
|-----|------|
| `is_chaos_experiment` | 카오스 실험 여부 |
| `experiment_id` | 관련 실험 ID |
| `classification` | `chaos_induced_test` 또는 `infrastructure_incident` |

---

## 설계 결정사항

### 1. LEVEL_3에서 Gradient 알고리즘 동작 방식

**결정: 백그라운드 계산 유지 + 적용만 차단 (Freeze)**

| 항목 | 내용 |
|-----|------|
| 결정 이유 | Freeze 해제 시 최신 RTT 데이터가 없으면 잘못된 limit으로 시작 |
| 근거 코드 | `RecoveryGate.check_recovery_allowed()`가 메트릭 수집은 유지하면서 복구 허용만 결정 |
| 업계 관행 | Netflix, Google SRE 모두 "계산은 유지, 적용만 차단" 방식 사용 |

구현 방식:
1. `_gradient_frozen = True` 플래그 설정
2. `_maybe_adjust_limit()`에서 frozen 체크: limit 변경 스킵
3. `GradientCalculator.add_sample()` 호출은 계속 (RTT 데이터 수집)

### 2. Effective Limit 계산 (Hard-Cap)

**결정: Emergency 배율은 Gradient limit에 최종적으로 곱해지는 Hard-Cap**

공식:
$$EffectiveLimit_{tier} = \min(GradientLimit, CB_{min}) \times EmergencyMultiplier_{tier}$$

| 근거 | 설명 |
|-----|------|
| 기존 코드 | `EMERGENCY_LEVEL_RULES`에 티어별 배율 정의 |
| 안전성 | Gradient가 RTT 좋다고 올려도 Emergency 배율로 최종 제한 |

### 3. Admin Override 우선순위

**결정: Admin Override는 min() 계산의 한 인자로만 취급 (Emergency보다 낮음)**

| 근거 | 설명 |
|-----|------|
| 기존 코드 | `escalation_audit.py`에서 `ADMIN_OVERRIDE`도 `SAFETY_MAX`보다 낮은 우선순위 |
| 예외 | `force=True` 파라미터로 명시적 우회 가능 (현재 `deactivate()` 패턴) |
| 업계 관행 | Google SRE: "사람의 실수가 가장 큰 장애 원인" - 자동 안전장치 > 수동 Override |

### 4. min_limit=0 (Full Stop) 트리거 조건

**결정: 3중 조건 충족 시 Full Stop**

| 조건 | 설명 |
|-----|------|
| LEVEL_3 | Emergency 최고 단계 |
| DB CB OPEN | 핵심 데이터베이스 접근 불가 |
| Error Budget Exhausted | 에러 예산 소진 |

근거: 기존 `KILL_SWITCH_ACTIVATED` 이벤트 패턴 활용

---

## 복구 시나리오

### 자동 복구 흐름

```
LEVEL_3 ──(expires_at 도달)──► 자동 비활성화
                                    │
                                    ▼
                            EMERGENCY_DEACTIVATED 발행
                                    │
                                    ▼
                            Throttle limit 복구 (Dampening 적용)
```

### 수동 복구 흐름

```
LEVEL_3 ──(operator 개입)──► deactivate() 호출
                                    │
                                    ▼
                            EMERGENCY_DEACTIVATED 발행
                                    │
                                    ▼
                            Throttle limit 복구 (Dampening 적용)
```

### Gradual Recovery (Recovery Dampening 연동)

Emergency Mode의 Recovery Gate 연동 + Dampening:

| 복구 단계 | 비율 | 대기 시간 | 근거 |
|---------|------|---------|------|
| RecoveryGate 통과 | 80% | 0초 | Thundering Herd 방지 |
| 안정화 확인 | 90% | 30초 | 메트릭 모니터링 |
| 완전 복구 | 100% | 60초 | 정상 운영 복귀 |

---

## 구현 체크리스트

### Phase 1: 이벤트 핸들러
- [x] `_on_emergency_level_changed_throttle()` 핸들러 구현
- [x] `_on_emergency_deactivated_throttle()` 핸들러 구현
- [x] `register_default_handlers()`에 등록

### Phase 2: Throttle 확장
- [x] `AdaptiveThrottle.adjust_for_emergency(level)` 메서드 추가
- [x] Emergency Level → limit 배율 매핑 테이블
- [x] `_emergency_mode_active` 플래그 추가
- [x] **`_gradient_frozen` 플래그 추가 (LEVEL_3 시 Freeze)**

### Phase 3: Hard-Cap 로직
- [x] `_apply_emergency_cap(gradient_limit)` 메서드 구현
- [x] Effective Limit 공식 적용: `min(gradient, cb_min) × em_multiplier`
- [x] 티어별 배율 캐싱 (EMERGENCY_LEVEL_RULES 참조)

### Phase 4: Full Stop 조건
- [x] 3중 조건 체크 로직 구현 (LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED)
- [x] `KILL_SWITCH_ACTIVATED` 이벤트 연동
- [x] min_limit=0 적용 시 경고 로그 + 알림

### Phase 5: 상태 동기화
- [x] Throttle 초기화 시 현재 Emergency Level 확인
- [x] Check on Use 패턴 적용 (TTL 캐싱)
- [x] Drift 감지 및 자동 동기화

### Phase 6: 복구 로직
- [x] Emergency 비활성화 시 limit 복구
- [x] **Recovery Dampening 적용 (80% → 90% → 100%)**
- [x] Gradual Recovery 지원
- [x] 롤백 시나리오 처리

### Phase 7: 테스트
- [x] Level 변경 → limit 조정 테스트
- [x] LEVEL_3 Gradient Freeze 테스트
- [x] Hard-Cap 적용 테스트
- [x] Full Stop 조건 테스트
- [x] Recovery Dampening 테스트
- [x] Chaos Experiment 시나리오 테스트

---

## 상태 전이 다이어그램

```
┌─────────────────────────────────────────────────────────────────────┐
│                 Emergency Level ↔ Throttle Limit                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   Emergency Level          Throttle Limit                           │
│                                                                      │
│   ┌─────────┐              ┌─────────────────┐                      │
│   │ NORMAL  │─────────────►│ limit × 1.0     │                      │
│   │ (0)     │              │ (full capacity) │                      │
│   └────┬────┘              └─────────────────┘                      │
│        │                                                             │
│        │ 장애 감지                                                   │
│        ▼                                                             │
│   ┌─────────┐              ┌─────────────────┐                      │
│   │ LEVEL_1 │─────────────►│ limit × 0.8     │                      │
│   │ (1)     │              │ (80% capacity)  │                      │
│   └────┬────┘              └─────────────────┘                      │
│        │                                                             │
│        │ 장애 심화                                                   │
│        ▼                                                             │
│   ┌─────────┐              ┌─────────────────┐                      │
│   │ LEVEL_2 │─────────────►│ limit × 0.5     │                      │
│   │ (2)     │              │ (50% capacity)  │                      │
│   └────┬────┘              └─────────────────┘                      │
│        │                                                             │
│        │ 심각한 장애                                                 │
│        ▼                                                             │
│   ┌─────────┐              ┌─────────────────┐                      │
│   │ LEVEL_3 │─────────────►│ limit = min     │  ← Gradient Freeze  │
│   │ (3)     │              │ (minimum only)  │                      │
│   └─────────┘              └─────────────────┘                      │
│                                                                      │
│   ┌─────────────────────────────────────────────────────────────┐   │
│   │ Full Stop 조건: LEVEL_3 + DB_CB_OPEN + BUDGET_EXHAUSTED     │   │
│   │ → min_limit = 0 (완전 차단)                                  │   │
│   └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 참조 파일

| 파일 | 역할 |
|-----|------|
| `services/emergency_mode/manager.py` | GracefulDegradationManager |
| `services/emergency_mode/enums.py` | EmergencyLevel, EMERGENCY_LEVEL_RULES |
| `services/emergency_mode/models.py` | EmergencyState |
| `services/emergency_mode/recovery_gate.py` | RecoveryGate |
| `services/event_bus.py` | EventType, SelfHealingEventBus |
| `services/throttle/adaptive.py` | AdaptiveThrottle |
| `services/namespace_emergency/escalation_audit.py` | Admin Override 우선순위 패턴 |

---

**작성일**: 2026-01-29
**수정일**: 2026-01-29 (Gradient Freeze, Hard-Cap, Full Stop, Recovery Dampening 추가)
**관련 문서**: 23_ADAPTIVE_THROTTLING.md, 72_EMERGENCY_COORDINATION_LAYER.md

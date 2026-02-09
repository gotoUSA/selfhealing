# 207. AdaptiveThrottle 연동 대상 시스템 총괄 분석

> **상태**: 📋 분석 완료
> **목적**: 5개 P3 시스템과 `AdaptiveThrottle` 연동 여부를 코드 근거로 결정한다.
> **기준일**: 2026-02-09

---

## 1. 분석 대상

| # | 시스템 | 키워드 | 소스 위치 |
|---|--------|--------|-----------|
| 1 | Load Shedding | 트래픽 우선순위 | `services/circuit_breaker/load_shedding/` (6파일) |
| 2 | Adaptive Replay | 공통 Gradient 로직 | `services/adaptive_replay.py` (320줄) |
| 3 | Runtime Feedback | 자동 튜닝 | `core/runtime_feedback.py` (580줄), `services/auto_tuning/service.py` (1022줄) |
| 4 | Canary Rollout | 점진적 배포 | `services/canary/` (17파일) |
| 5 | Governance Checks | 안전 장치 | `services/governance/checks.py` (863줄) |

---

## 2. AdaptiveThrottle 현재 구조 (코드 근거)

**파일**: `services/throttle/adaptive.py` (2287줄)

```
AdaptiveThrottle(ThrottleDLQReplayMixin, SlidingWindowThrottle)
├── GradientCalculator         — RTT EMA 기반 gradient (L396-470)
├── Emergency Mode 연동        — Level 0~3 배율, Full Stop 3중 조건 (L1553-1818)
├── 429 Rate Limit 연동        — EventBus RATE_LIMIT_429 구독 (L603-733)
├── Error Budget 연동          — EventBus ERROR_BUDGET_* 구독 (L735-1000)
├── Recovery Dampening         — 80% → 90% → 100% 점진 복구 (L1949-2107)
├── DLQ Replay Mixin           — 거부 시 DLQ 저장 + Recovery Replay (L502 상속)
├── Conservative Limit         — Min-Winner 정책 (L1095-1115)
└── tier_id 기반 분류          — critical/standard/non_essential (L1434 check())
```

**현재 연동 상태**:

| 외부 시스템 | 연동 방식 | 코드 위치 |
|-------------|-----------|-----------|
| EventBus | ✅ 직접 구독 | `_subscribe_rate_limit_events()` L603, `_subscribe_error_budget_events()` L735 |
| Emergency Mode | ✅ Check-on-Use | `check_and_sync_emergency_state()` L1877 |
| DLQ Service | ✅ Mixin 상속 | `ThrottleDLQReplayMixin` L502 |
| CircuitBreaker | ✅ Full Stop 조건 | `_check_db_circuit_breaker_open()` L1827 |
| Prometheus | ✅ 메트릭 기록 | `_record_throttle_metrics()` L170 |

---

## 3. 연동 vs 유지 비교 분석

### 3-1. Load Shedding — ✅ 연동 추천

**현재 코드 상태**: 양쪽에 상호 참조 없음 (독립 동작)

| 항목 | 연동할 경우 | 그대로 둘 경우 |
|------|------------|---------------|
| **장점** | Load Shedding의 `criticality` (critical/low/medium)와 Throttle의 `tier_id` (critical/standard/non_essential) 매핑으로 **통합 우선순위 체계** 구축 | 각 시스템이 독립적으로 단순함 |
| **장점** | Shedding 활성화 시 Throttle limit에 자동 반영 → 이중 차단 방지 | 변경 범위 없음 |
| **단점** | 두 시스템 간 커플링 발생 | **이중 트래픽 제한**: 같은 요청이 Load Shedding에서 50% 차단 + Throttle에서 limit 초과로 추가 차단 가능 |
| **단점** | Load Shedding Manager 의존성 추가 | **우선순위 불일치**: Load Shedding은 `criticality=low`를 먼저 차단, Throttle은 `tier_id=non_essential`을 먼저 차단 → 같은 목적이나 다른 기준 |

**코드 근거**:
- `LoadSheddingManager.evaluate_shedding()` (manager.py L210): `criticality` 기반 허용 트래픽 비율 계산
- `AdaptiveThrottle.check()` (adaptive.py L1434): `tier_id` 파라미터로 요청 우선순위 분류
- `PROTECTED_TIERS_ON_429` (adaptive.py L498): `{"critical"}` — Throttle이 이미 tier 보호 개념 보유
- `_apply_emergency_cap()` (adaptive.py L1724): tier별 배율 적용 인프라 이미 존재
- 두 시스템 모두 `service_id`/`service_name` 기반이나 **매핑 테이블 없음**

**추천 이유**: 독립 동작 시 동일 요청에 대한 이중 차단이 발생하며, 운영자가 각 시스템의 차단 기준을 별도 관리해야 함. `tier_id`↔`criticality` 매핑과 Shedding 상태의 limit 반영으로 해결 가능.

→ **상세 계획**: [208_LOAD_SHEDDING_THROTTLE_INTEGRATION.md](208_LOAD_SHEDDING_THROTTLE_INTEGRATION.md)

---

### 3-2. Governance Checks — ✅ 연동 추천

**현재 코드 상태**: Emergency/Error Budget은 EventBus로 간접 연동. 직접 governance check 없음.

| 항목 | 연동할 경우 | 그대로 둘 경우 |
|------|------------|---------------|
| **장점** | `check_all_governance()` 단일 함수로 Kill Switch + Emergency + Error Budget **일괄 체크** — AutoTuningService와 동일한 패턴 (service.py L149) | EventBus 기반 간접 연동으로 느슨한 결합 유지 |
| **장점** | `@require_governance` 데코레이터 또는 `GovernanceCheckMixin` 재사용 가능 (checks.py L700, L738) | 변경 없음 |
| **장점** | Break Glass 지원 자동 획득 (checks.py L466) — 현재 AdaptiveThrottle에는 Break Glass 미구현 | — |
| **단점** | `check()` 호출마다 governance 체크 시 성능 오버헤드 (TTLCache 30초이므로 실제 부하 적음) | **Kill Switch 반영 지연**: Kill Switch 활성화 시 Throttle은 EventBus 이벤트가 올 때까지 모름 |
| **단점** | governance 패키지 의존성 추가 | **Break Glass 미지원**: 장애 시 Throttle만 우회 불가능 |
| **단점** | — | **안전 체크 불일치**: AutoTuningService는 `check_all_governance()` 호출, AdaptiveThrottle은 자체 Emergency sync만 수행 → 동일 수준의 안전장치 아님 |

**코드 근거**:
- `AdaptiveThrottle.check_and_sync_emergency_state()` (adaptive.py L1877): Emergency Level만 체크, Kill Switch/Error Budget Gate 미체크
- `AutoTuningService._check_governance_before_adjustment()` (service.py L149): 조정 전 `check_all_governance()` 호출
- `GovernanceCheckMixin.check_governance()` (checks.py L790): 재사용 가능한 믹스인
- `TTLCache(default_ttl=30.0)` (checks.py L260): 30초 캐시로 성능 부하 최소화
- `BlockReason.RATE_LIMITED` (checks.py L175): 이미 Rate Limit 차단 사유가 enum에 정의됨

**추천 이유**: AdaptiveThrottle이 Emergency Mode만 자체 sync하는 것은 governance의 3단계 체크(Kill Switch → Emergency → Error Budget) 중 일부만 커버. `GovernanceCheckMixin` 또는 `check_all_governance()` 통합으로 AutoTuningService와 동일한 안전 수준 달성.

→ **상세 계획**: [209_GOVERNANCE_THROTTLE_INTEGRATION.md](209_GOVERNANCE_THROTTLE_INTEGRATION.md)

---

### 3-3. Runtime Feedback — ⚠️ 선택적 연동

**현재 코드 상태**: AutoTuningService.MODULES에 `"rate_limit"` 포함되나 AdaptiveThrottle과 직접 연동 없음.

| 항목 | 연동할 경우 | 그대로 둘 경우 |
|------|------------|---------------|
| **장점** | Throttle의 **정적 설정**(`sla_warning_ms`, `sla_critical_ms`, `initial_limit`)을 메트릭 기반 자동 튜닝 가능 | AdaptiveThrottle이 이미 RTT 기반 자체 adaptive 로직 보유 |
| **장점** | `ConfigApplier` 프로토콜 (runtime_feedback.py L64)로 Throttle 설정을 표준화된 방식으로 조정 | 이중 조정 위험 없음 |
| **장점** | 실패 시 자동 롤백 (`POST_ROLLBACK_COOLDOWN=120s`) 안전장치 획득 | 단순성 유지 |
| **단점** | **이중 조정 위험**: RuntimeFeedback이 `sla_warning_ms`를 조정 + AdaptiveThrottle이 RTT gradient로 limit 조정 → 두 시스템이 **서로 간섭** 가능 | `rate_limit_rps` 파라미터 (service.py L971)가 AdaptiveThrottle의 `current_limit`과 매핑되지 않아 AutoTuning의 rate_limit 모듈이 사실상 미동작 |
| **단점** | 조정 주기 충돌: RuntimeFeedback 60초 주기 vs AdaptiveThrottle `sample_interval_ms` 실시간 | — |
| **단점** | 어떤 시스템이 limit을 결정하는지 불명확 (주도권 문제) | — |

**코드 근거**:
- `AutoTuningService.MODULES` (service.py L71): `["circuit_breaker", "retry", "jitter", "rate_limit", "timeout"]`
- `_get_last_module_adjustment()` (service.py L883): `"rate_limit": ["rate_limit_rps"]` — 파라미터 정의만 존재
- `ConfigApplier` 프로토콜 (runtime_feedback.py L64): `get_current()`, `apply()`, `rollback()` — AdaptiveThrottle 어댑팅 가능
- `RuntimeFeedbackLoop.interval_seconds` (runtime_feedback.py L121): 60초 주기
- `AdaptiveThrottle._maybe_adjust_limit()` (adaptive.py L1173): `sample_interval_ms` 기반 실시간 조정

**판단**: 연동 시 이중 조정 위험이 핵심 문제. **정적 설정만 튜닝 대상으로 제한**하면 가능하나, 복잡도 대비 효용이 낮음.

→ **상세 분석**: [210_RUNTIME_FEEDBACK_THROTTLE_ANALYSIS.md](210_RUNTIME_FEEDBACK_THROTTLE_ANALYSIS.md)

---

### 3-4. Adaptive Replay — ❌ 연동 비추천

**현재 코드 상태**: `ThrottleDLQReplayMixin` 상속으로 DLQ Replay 이미 연동됨. Gradient 로직은 별도 구현.

| 항목 | 공통화할 경우 | 그대로 둘 경우 |
|------|-------------|---------------|
| **장점** | `decrease_ratio`, `increase_step` 등 공통 파라미터 추출 가능 | 각 도메인에 최적화된 파라미터 유지 |
| **장점** | — | DLQ Replay는 `ThrottleDLQReplayMixin`으로 이미 연동 완료 |
| **단점** | **도메인 불일치**: GradientCalculator는 RTT EMA 기반 (adaptive.py L396), AdaptiveReplayManager는 배치 성공률 기반 (adaptive_replay.py L186) → 입력 데이터 형식이 완전히 다름 | decrease_ratio=0.8이 두 곳에 중복 (하드코딩) |
| **단점** | 추상화 비용 > 코드 절약: Gradient 추상 클래스 도입 시 두 시스템 모두 수정 필요 | — |
| **단점** | `GradientCalculator`는 EMA 스무딩 + 실시간 양방향 조정, `AdaptiveReplayManager`는 단순 비율 기반 단방향 조정 → 알고리즘 자체가 다름 | — |

**코드 근거**:
- `GradientCalculator.add_sample()` (adaptive.py L422): `rtt_ms: float` 입력 → EMA 스무딩 → gradient 계산
- `AdaptiveReplayManager.record_batch_result()` (adaptive_replay.py L152): `total: int, success: int, failures: int` 입력 → 비율 기반 조정
- `GradientCalculator.get_gradient()` (adaptive.py L440): `(smoothed - previous) / previous` — 연속 비율 변화
- `AdaptiveReplayManager._adjust_max_items()` (adaptive_replay.py L186): `failure_rate >= threshold` → 이산 판단
- `ThrottleDLQReplayMixin._calculate_adaptive_replay_interval()` — 이미 DLQ 연동에서 capacity_ratio 기반 간격 조정 구현 (테스트 test_adaptive_dlq_replay_mixin.py L393)

**판단**: Gradient 로직 공통화는 추상화 비용이 실질 이득을 초과함. DLQ 연동은 이미 Mixin으로 완료된 상태.

→ **상세 분석**: [211_ADAPTIVE_REPLAY_GRADIENT_ANALYSIS.md](211_ADAPTIVE_REPLAY_GRADIENT_ANALYSIS.md)

---

### 3-5. Canary Rollout — ❌ 연동 비추천

**현재 코드 상태**: 양쪽에 상호 참조 없음.

| 항목 | 연동할 경우 | 그대로 둘 경우 |
|------|------------|---------------|
| **장점** | Throttle 설정 변경을 `config_type="throttle"`로 단계별 배포 가능 | 단순성 유지 |
| **장점** | `PassCriteria` (models.py L93)의 `error_rate_absolute_max`, `latency_p95_delta_ms`로 프로모션 판단 가능 | — |
| **단점** | **자기 모순적**: AdaptiveThrottle은 RTT gradient 기반 **실시간 자동 조정** 시스템 → Canary의 단계별 정적 배포와 목적 충돌 | Throttle 설정 변경 시 Canary 없이 전체 배포 |
| **단점** | Canary는 `CanaryStage.percentage` 기반 클러스터별 롤아웃 (service.py L180) → Throttle은 인스턴스별 싱글톤 `get_adaptive_throttle()` → 클러스터별 설정 분기 인프라 없음 | — |
| **단점** | Recovery Dampening (80→90→100%)이 이미 Canary와 유사한 점진적 적용을 수행 | — |

**코드 근거**:
- `CanaryRolloutService.create_rollout()` (service.py L188): `config_type: str, new_values: dict` — 정적 설정 변경 대상
- `AdaptiveThrottle._maybe_adjust_limit()` (adaptive.py L1173): RTT 기반 **실시간** limit 변경 — 정적 설정이 아닌 동적 상태
- `CanaryStage.clusters` (models.py 사용처): 클러스터별 적용 → `get_adaptive_throttle()` (adaptive.py L2265): 전역 싱글톤으로 클러스터 개념 없음
- `RECOVERY_DAMPENING_MULTIPLIERS = (0.8, 0.9, 1.0)` (adaptive.py L1946): 이미 Canary 유사 점진적 패턴 내장

**판단**: AdaptiveThrottle의 핵심(실시간 자동조정)과 Canary의 핵심(정적 설정 단계적 배포)이 목적 충돌. 연동 시 Canary가 설정을 고정하면 AdaptiveThrottle의 자동조정을 방해할 수 있음.

→ **상세 분석**: [212_CANARY_ROLLOUT_THROTTLE_ANALYSIS.md](212_CANARY_ROLLOUT_THROTTLE_ANALYSIS.md)

---

## 4. 최종 추천 요약

| 시스템 | 판정 | 핵심 근거 | 상세 문서 |
|--------|------|-----------|-----------|
| **Load Shedding** | ✅ 연동 추천 | 독립 동작 시 이중 트래픽 차단, tier/criticality 통합 필요 | 208 |
| **Governance Checks** | ✅ 연동 추천 | Kill Switch/Break Glass 미반영, AutoTuningService와 안전 수준 불일치 | 209 |
| **Runtime Feedback** | ⚠️ 선택적 | 이중 조정 위험, 정적 설정만 대상 시 가능하나 복잡도 대비 효용 낮음 | 210 |
| **Adaptive Replay** | ❌ 비추천 | DLQ 이미 Mixin 완료, Gradient 로직은 도메인/알고리즘 불일치 | 211 |
| **Canary Rollout** | ❌ 비추천 | 실시간 자동조정 vs 정적 단계별 배포 목적 충돌, 클러스터 인프라 없음 | 212 |

---

## 5. 문서 맵

```
207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md          ← 본 문서 (총괄)
├── 208_LOAD_SHEDDING_THROTTLE_INTEGRATION.md          ← ✅ 연동 계획
├── 209_GOVERNANCE_THROTTLE_INTEGRATION.md             ← ✅ 연동 계획
├── 210_RUNTIME_FEEDBACK_THROTTLE_ANALYSIS.md          ← ⚠️ 선택적 분석
├── 211_ADAPTIVE_REPLAY_GRADIENT_ANALYSIS.md           ← ❌ 비추천 근거
└── 212_CANARY_ROLLOUT_THROTTLE_ANALYSIS.md            ← ❌ 비추천 근거
```

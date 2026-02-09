# 207. AdaptiveThrottle 연동 대상 시스템 총괄 분석

> **상태**: ✅ Section 5 (Load Shedding ↔ AdaptiveThrottle) 구현 완료
> **목적**: 5개 P3 시스템과 `AdaptiveThrottle` 연동 여부를 코드 근거로 결정한다.
> **기준일**: 2026-02-09
> **구현일**: 2026-02-10

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

## 5. Load Shedding ↔ Throttle 연동 시 구현 필수 보완사항

> **목적**: 208 문서 구현 전, 코드 분석으로 확인된 6가지 구조적 문제와 해결 방안을 정의한다.

### 5-1. 식별자 Granularity 불일치 해결 — `context`를 통한 `service_id` 전달

**문제**: `AdaptiveThrottle`은 전역 싱글톤 `get_adaptive_throttle()` (adaptive.py L2265)이며, `check(key, ...)` 호출 시 `key`는 IP/User 등 요청 식별자이다. Shedding 이벤트가 `affected_services=["order-api"]`로 도달해도, Throttle 내부에서 어떤 key가 어느 서비스 소속인지 식별할 수 없다. 이 상태에서 `_current_limit`을 전역으로 깎으면 **무관한 서비스까지 일괄 제한**된다.

**코드 근거**:
- `get_adaptive_throttle()` (adaptive.py L2265): 전역 싱글톤, 서비스별 인스턴스 아님
- `check(key, tier_id, context, ...)` (adaptive.py L1395-L1401): `context`는 현재 DLQ 저장용으로만 사용 (`_auto_store_rejection_to_dlq(context, ...)` L1458)
- `SlidingWindowThrottle.check(key)` (base.py L207): `self._current_limit`을 직접 참조하여 permit 판단

**해결 방안**: `check()` 시그니처를 변경하지 않고, 기존 `context` 파라미터에 `service_id`를 주입한다.

```python
# 호출부 (미들웨어/라우터)
throttle.check(
    key="192.168.1.10",
    tier_id="standard",
    context={"service_id": "order-api", "domain": "shopping"}
)
```

```python
# adaptive.py — check() 내부 (429 CRITICAL 보호 패턴과 동일한 임시 swap 적용)
def check(self, key, tier_id="standard", context=None, store_rejection=True):
    service_id = context.get("service_id") if context else None

    # Shedding 대상 서비스의 요청에만 제한적 limit 적용
    if service_id and service_id in self._shedding_affected_services:
        # 429 CRITICAL 보호 패턴 (adaptive.py L1464-L1470)과 동일한 임시 swap
        original_limit = self._current_limit
        self._current_limit = min(self._current_limit, self._shedding_suggested_limit)
        result = super().check(key)
        self._current_limit = original_limit
    else:
        result = super().check(key)
```

**임시 swap이 필요한 이유**: `SlidingWindowThrottle.check(key)`가 `self._current_limit`을 직접 참조하므로 (base.py L213: `if current_count >= self._current_limit`), 외부에서 limit을 분기해도 `super().check()`에 전달되지 않는다. 기존 429 보호에서도 동일한 패턴을 사용한다:

```python
# 기존 429 CRITICAL 보호 코드 (adaptive.py L1464-L1470)
if self._429_reduction_active and tier_id in PROTECTED_TIERS_ON_429:
    original_limit = self._current_limit
    self._current_limit = self._limit_before_429  # 임시 swap
    result = super().check(key)
    self._current_limit = original_limit           # 복원
```

**신규 변수**:
- `_shedding_affected_services: set[str]` — Shedding 이벤트의 `affected_services` 저장
- 기존 네이밍 패턴: `_rate_limit_keys: dict[str, float]` (adaptive.py L570), `_error_budget_limit_reduction_active: bool` (adaptive.py L582)

---

### 5-2. 이중 차단(Double Shedding) 보정 — Limit 보상 로직

**문제**: 방안 B 유지 시, `LoadSheddingMiddleware.process()`가 확률적으로 50% 차단 (manager.py L291: `allow = random.random() * 100 < allowed_percent`) + Throttle이 limit을 50%로 감소 → 실질 생존율 ≈ 25%. 목표 대비 과도한 트래픽 드랍.

**코드 근거**:
- `LoadSheddingManager.should_allow_request()` (manager.py L255-L301): `allowed_percent`를 기반으로 `random.random() * 100 < allowed_percent`로 확률적 차단
- Middleware는 차단 후 남은 요청만 Throttle로 전달
- Throttle이 `_shedding_suggested_limit = max_limit * (traffic_limit / 100.0)`으로 설정하면 이미 절반된 트래픽에 또 절반 limit 적용

**해결 방안**: Throttle 측 Shedding Limit 계산 시 **보상 계수(compensation factor)**를 적용하여 이중 차단을 완화한다. 하드코딩이 아닌 `ThrottleSettings`에 설정화한다.

```python
# settings/throttle.py — ThrottleSettings에 추가
shedding_compensation_factor: float = Field(
    default=1.5,
    ge=1.0,
    le=3.0,
    description="Load Shedding 이중 차단 방지 보상 계수. "
                "Middleware가 이미 차단한 비율을 감안하여 Throttle limit 감소를 완화.",
)
```

```python
# adaptive.py — 이벤트 핸들러 내부
def _handle_shedding_changed(self, event):
    event_data = event.data if hasattr(event, "data") else event
    traffic_limit = event_data.get("traffic_limit", 100.0)

    raw_limit = int(self.config.max_limit * (traffic_limit / 100.0))
    compensated = min(
        self.config.max_limit,
        int(raw_limit * self.config.shedding_compensation_factor),
    )
    self._shedding_suggested_limit = max(compensated, self.config.min_limit)
```

**기존 패턴과의 일관성**: `ThrottleSettings`에는 이미 유사한 설정 패턴이 존재한다:
- `static_safe_limit_percent: float = 0.5` (settings/throttle.py L273) — Safe-Open 시 정적 limit 비율
- `recovery_jitter_max_seconds` 등 계수형 설정

**보상 계수 1.5의 의미**:
- Shedding `traffic_limit=50%` → `raw_limit = max_limit * 0.5`
- 보상 적용: `raw_limit * 1.5 = max_limit * 0.75`
- 실질 생존율: 0.5(Middleware) × (0.75/1.0)(Throttle 여유) ≈ 실제 트래픽 제한이 목표 50%에 근접

---

### 5-3. Shedding 해제 시 Recovery Dampening 연동

**문제**: Recovery Dampening의 호출 지점 4곳 중 Shedding 해제에 대한 연동은 없다.

| 호출 지점 | 코드 위치 | 조건 |
|-----------|-----------|------|
| Emergency 해제 (Level→0) | adaptive.py L1571 | `level == 0` |
| Full Stop 해제 | adaptive.py L1934 | `deactivate_full_stop()` |
| 429 Cooldown 종료 | adaptive.py L741 | `_handle_cooldown_end()` |
| Error Budget 회복 | adaptive.py L978 | `_handle_error_budget_recovered()` |
| **(미구현) Shedding 해제** | — | — |

**코드 근거**:
- Emergency 활성화 시 Recovery Dampening 중단: `self._recovery_dampening_active = False` (adaptive.py L1584)
- Error Budget 회복 시 Jitter 적용: `self.start_recovery_dampening(apply_jitter=True)` (adaptive.py L978)
- `start_recovery_dampening(apply_jitter=False)` 호출 시 동기 실행 (`_do_start_recovery_dampening()` 직접 호출)
- `start_recovery_dampening(apply_jitter=True)` 호출 시 daemon 스레드로 지연: `_schedule_dampening_start()` (adaptive.py L2047-L2053)

**해결 방안**:

```python
def _handle_shedding_changed(self, event):
    event_data = event.data if hasattr(event, "data") else event
    new_level = event_data.get("new_level", -1)

    if new_level < 0:  # Shedding 해제 (SHEDDING_DEACTIVATED)
        self._shedding_affected_services.clear()
        self._shedding_suggested_limit = self.config.max_limit

        # 다른 제한이 활성화 상태가 아닐 때만 Dampening 시작
        if not self._emergency_mode_active and not self._429_reduction_active:
            # Jitter 적용 (Thundering Herd 방지) — Error Budget 회복과 동일 패턴
            self.start_recovery_dampening(apply_jitter=True)
    else:
        # Shedding 활성화 처리 (Limit 감소)
        ...
```

**조건부 가드의 근거**: Emergency 활성화 시 `self._recovery_dampening_active = False`로 Dampening을 중단하는 패턴 (adaptive.py L1584)이 이미 존재한다. Shedding 해제 시에도 Emergency가 활성 중이면 Dampening 시작이 무의미하다.

**`apply_jitter=True` 적용 이유**:
1. Error Budget 회복에서 동일하게 jitter 적용 (adaptive.py L978)
2. `SelfHealingEventBus.publish()`가 동기식 (bus.py L376: `subscription.handler(event)`) → `apply_jitter=True`일 때 daemon 스레드로 지연 실행되므로 핸들러가 즉시 반환됨 (5-5절 참조)

---

### 5-4. tier ↔ criticality 매핑 유틸리티 신설 — `tier_mapping.py`

**문제**: `criticality`와 `tier_id`를 호출자가 매번 하드코딩으로 전달하며, 오타/누락 방어가 없다.

**코드 근거**:
- `ServiceConfig.criticality` (models.py L44): `"critical" | "high" | "medium" | "low"` — `__post_init__`에서 검증 (models.py L60: `valid_levels = {"critical", "high", "medium", "low"}`)
- `AdaptiveThrottle.check(tier_id)` (adaptive.py L1398): `"critical" | "standard" | "non_essential"` — 검증 없이 문자열 비교만 수행

**해결 방안**: `services/throttle/tier_mapping.py` 신설 (기존 `services/throttle/` 디렉토리 내 파일과 동일 레벨).

```python
# services/throttle/tier_mapping.py

"""
tier_id ↔ criticality 매핑 유틸리티.

Load Shedding의 ServiceConfig.criticality 값과
AdaptiveThrottle.check()의 tier_id 파라미터 간 변환을 제공한다.
"""

import logging

logger = logging.getLogger(__name__)

# ServiceConfig.criticality → AdaptiveThrottle tier_id
# 근거: models.py L60 valid_levels = {"critical", "high", "medium", "low"}
CRITICALITY_TO_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "critical",       # high도 critical tier로 보호
    "medium": "standard",
    "low": "non_essential",
}

# AdaptiveThrottle tier_id → ServiceConfig.criticality
TIER_TO_CRITICALITY: dict[str, str] = {
    "critical": "critical",
    "standard": "medium",
    "non_essential": "low",
}

# tier_id 유효값 (adaptive.py L1398, L1430, L498에서 사용되는 값)
VALID_TIER_IDS: set[str] = {"critical", "standard", "non_essential"}

_DEFAULT_TIER: str = "standard"
_DEFAULT_CRITICALITY: str = "medium"


def get_tier_from_criticality(criticality: str) -> str:
    """
    ServiceConfig.criticality를 AdaptiveThrottle tier_id로 변환.

    Args:
        criticality: "critical" | "high" | "medium" | "low"

    Returns:
        tier_id: "critical" | "standard" | "non_essential"
    """
    tier = CRITICALITY_TO_TIER.get(criticality.lower(), _DEFAULT_TIER)
    if criticality.lower() not in CRITICALITY_TO_TIER:
        logger.warning(
            f"[TierMapping] Unknown criticality '{criticality}', "
            f"falling back to '{_DEFAULT_TIER}'"
        )
    return tier


def get_criticality_from_tier(tier_id: str) -> str:
    """
    AdaptiveThrottle tier_id를 ServiceConfig.criticality로 변환.

    Args:
        tier_id: "critical" | "standard" | "non_essential"

    Returns:
        criticality: "critical" | "medium" | "low"
    """
    criticality = TIER_TO_CRITICALITY.get(tier_id.lower(), _DEFAULT_CRITICALITY)
    if tier_id.lower() not in TIER_TO_CRITICALITY:
        logger.warning(
            f"[TierMapping] Unknown tier_id '{tier_id}', "
            f"falling back to '{_DEFAULT_CRITICALITY}'"
        )
    return criticality
```

**파일명 `tier_mapping.py` 선택 이유**: `priority_mapping.py`는 208 문서에서 제안되었으나, `services/throttle/registry.py`가 이미 존재하여 "priority" + "registry"가 혼동될 수 있다. `tier_mapping`이 역할을 더 정확하게 반영한다.

**`"high"` criticality 포함 근거**: `ServiceConfig.__post_init__()` (models.py L60)에서 `valid_levels = {"critical", "high", "medium", "low"}`로 `"high"`를 유효 값으로 허용한다. Load Shedding Manager에서는 `"high"`를 직접 사용하지 않지만, `ServiceConfig`의 유효 값이므로 매핑에 포함해야 누락이 발생하지 않는다.

---

### 5-5. EventBus 동기식 핸들러 최소 연산 보장

**문제**: `SelfHealingEventBus.publish()`는 동기식이다 (bus.py L376: `subscription.handler(event)` — 같은 스레드에서 순차 실행). Throttle 이벤트 핸들러에서 Lock 획득이나 I/O 작업을 수행하면, 이벤트 발행자(`LoadSheddingManager`)의 스레드까지 Blocking된다.

**코드 근거** — 기존 핸들러의 패턴 분석:

| 핸들러 | Lock 사용 | I/O | 패턴 |
|--------|-----------|-----|------|
| `_handle_rate_limit_429` (L625) | ❌ | ❌ | 변수 할당 + 로깅 + 메트릭 |
| `_handle_cooldown_end` (L722) | ❌ | ❌ | 변수 할당 + `start_recovery_dampening()` |
| `_handle_error_budget_warning` (L794) | ❌ | ❌ | 변수 할당 + 메트릭 + 감사 로깅 |
| `_handle_error_budget_critical` (L855) | ❌ | ❌ | 변수 할당 + uuid 생성 + 메트릭 |
| `_handle_error_budget_recovered` (L939) | ❌ | ⚠️ | `start_recovery_dampening(apply_jitter=True)` → daemon 스레드 |

모든 기존 핸들러는 **변수 할당 + 메트릭/감사 로깅**만 수행하고 즉시 반환한다. `_handle_error_budget_recovered`에서 `start_recovery_dampening(apply_jitter=True)` 호출 시에도 `_schedule_dampening_start()` (adaptive.py L2047)가 daemon 스레드를 생성하여 즉시 반환한다.

**구현 규칙**: `_handle_shedding_changed` 핸들러는 다음만 수행한다:
1. `event.data`에서 값 추출
2. `_shedding_affected_services`, `_shedding_suggested_limit` 변수 할당
3. Shedding 해제 시 `start_recovery_dampening(apply_jitter=True)` — daemon 스레드로 비동기 실행

```python
def _handle_shedding_changed(self, event):
    """Load Shedding 상태 변경 이벤트 처리 — 최소 연산 보장."""
    # 변수 할당만 수행 (동기식 EventBus 보호)
    event_data = event.data if hasattr(event, "data") else event
    new_level = event_data.get("new_level", -1)
    traffic_limit = event_data.get("traffic_limit", 100.0)
    affected = event_data.get("affected_services", [])

    if new_level < 0:
        self._shedding_affected_services = set()
        self._shedding_suggested_limit = self.config.max_limit
        if not self._emergency_mode_active and not self._429_reduction_active:
            self.start_recovery_dampening(apply_jitter=True)  # daemon 스레드
    else:
        self._shedding_affected_services = set(affected)
        raw = int(self.config.max_limit * (traffic_limit / 100.0))
        self._shedding_suggested_limit = min(
            self.config.max_limit,
            max(int(raw * self.config.shedding_compensation_factor), self.config.min_limit),
        )
```

**Lock 미사용 근거**: `_shedding_affected_services`는 Python `set` 객체 참조의 교체(`=`)이며, GIL 하에서 원자적이다. 기존 핸들러(`_handle_rate_limit_429` 등)에서도 `_rate_limit_keys`, `_429_reduction_active` 등을 Lock 없이 할당한다.

---

### 5-6. `_shedding_suggested_limit` 초기값 — `max_limit`

**문제**: `conservative_limit` 속성 (adaptive.py L1082-L1101)이 `min(rtt, 429, error_budget, shedding)` 연산을 수행하므로, `_shedding_suggested_limit`의 초기값이 0이면 시작 즉시 모든 트래픽이 차단된다.

**코드 근거** — 기존 유사 변수 초기화 패턴:

| 변수 | 초기값 | 코드 위치 | 이유 |
|------|--------|-----------|------|
| `_rtt_suggested_limit` | `self.config.initial_limit` | adaptive.py L576 | RTT 데이터 수집 전까지 기본 limit |
| `_429_suggested_limit` | `self.config.max_limit` | adaptive.py L577 | **429 미발생 시 min()에 영향 없도록** |
| `_error_budget_multiplier` | `1.0` | adaptive.py L585 | 감소 미적용 상태 |

`_shedding_suggested_limit`은 `_429_suggested_limit`과 동일한 역할("이벤트 미발생 시 min()에 영향 없음")이므로 `self.config.max_limit`이 정확한 초기값이다.

**구현 위치**: `__init__()` (adaptive.py L502 이후 초기화 블록) 및 `reset_all()` (adaptive.py L1737)

```python
# __init__() 내 (Conservative Limit 상태 블록 L575-L577 이후)
self._shedding_suggested_limit: int = self.config.max_limit
self._shedding_affected_services: set[str] = set()
```

```python
# reset_all() 내 (L1737-L1760, 기존 초기화 코드 뒤에 추가)
self._shedding_suggested_limit = self.config.max_limit
self._shedding_affected_services = set()
```

```python
# conservative_limit 속성 (adaptive.py L1082-L1101) 수정
@property
def conservative_limit(self) -> int:
    if not self._conservative_enabled:
        return self._current_limit

    error_budget_limit = self.config.max_limit
    if self._error_budget_limit_reduction_active:
        error_budget_limit = int(
            self._limit_before_error_budget_reduction * self._error_budget_multiplier
        )

    return min(
        self._rtt_suggested_limit,
        self._429_suggested_limit,
        error_budget_limit,
        self._shedding_suggested_limit,  # 평상시 max_limit → min()에 영향 없음
    )
```

---

### 5-7. 보완사항 영향 범위 요약

| 파일 | 변경 내용 | 영향도 |
|------|-----------|--------|
| `services/throttle/adaptive.py` | `__init__` — `_shedding_suggested_limit`, `_shedding_affected_services` 초기화 | 낮음 |
| `services/throttle/adaptive.py` | `check()` — `context.service_id` 기반 임시 swap 분기 | 중간 |
| `services/throttle/adaptive.py` | `conservative_limit` — `_shedding_suggested_limit` 참여 | 낮음 |
| `services/throttle/adaptive.py` | `_subscribe_load_shedding_events()`, `_handle_shedding_changed()` 신규 | 중간 |
| `services/throttle/adaptive.py` | `reset_all()` — Shedding 상태 초기화 추가 | 낮음 |
| `settings/throttle.py` | `shedding_compensation_factor` 설정 추가 | 낮음 |
| (신규) `services/throttle/tier_mapping.py` | `get_tier_from_criticality()`, `get_criticality_from_tier()` | 낮음 |
| `services/circuit_breaker/load_shedding/manager.py` | `update_shedding_state()` — EventBus 발행 추가 | 중간 |
| `services/event_bus/bus.py` | `EventType.LOAD_SHEDDING_LEVEL_CHANGED` 추가 | 낮음 |

---

### 5-8. 구현 순서 제약

```
1. EventType.LOAD_SHEDDING_LEVEL_CHANGED 추가        (event_bus/bus.py)
2. tier_mapping.py 신설                               (throttle/tier_mapping.py)
3. ThrottleSettings.shedding_compensation_factor 추가  (settings/throttle.py)
4. __init__, reset_all, conservative_limit 수정        (throttle/adaptive.py)
5. _subscribe_load_shedding_events 신규                (throttle/adaptive.py)
6. _handle_shedding_changed 신규                       (throttle/adaptive.py)
7. check() 내 service_id 분기 추가                     (throttle/adaptive.py)
8. update_shedding_state() EventBus 발행 추가          (load_shedding/manager.py)
```

1~3은 독립 작업. 4~7은 adaptive.py 내부 순차 의존. 8은 4 이후 언제든 가능.

---

## 6. 문서 맵

```
207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md          ← 본 문서 (총괄)
├── 208_LOAD_SHEDDING_THROTTLE_INTEGRATION.md          ← ✅ 연동 계획
├── 209_GOVERNANCE_THROTTLE_INTEGRATION.md             ← ✅ 연동 계획
├── 210_RUNTIME_FEEDBACK_THROTTLE_ANALYSIS.md          ← ⚠️ 선택적 분석
├── 211_ADAPTIVE_REPLAY_GRADIENT_ANALYSIS.md           ← ❌ 비추천 근거
└── 212_CANARY_ROLLOUT_THROTTLE_ANALYSIS.md            ← ❌ 비추천 근거
```

---

## 7. 구현 이력

### 7-1. Section 5 (Load Shedding ↔ AdaptiveThrottle) — 2026-02-10

**구현 범위**: 5-1 ~ 5-8 전체 (라인 1-571)

| 순서 | 작업 | 대상 파일 | 상태 |
|------|------|-----------|------|
| 1 | `EventType.LOAD_SHEDDING_LEVEL_CHANGED` 추가 | `services/event_bus/bus.py` | ✅ |
| 2 | `tier_mapping.py` 신설 (criticality ↔ tier_id 양방향 매핑) | `services/throttle/tier_mapping.py` | ✅ |
| 3 | `ThrottleSettings.shedding_compensation_factor` 추가 | `settings/throttle.py` | ✅ |
| 4 | `__init__`, `reset_all`, `conservative_limit` 수정 | `services/throttle/adaptive.py` | ✅ |
| 5 | `_subscribe_load_shedding_events` 신규 | `services/throttle/adaptive.py` | ✅ |
| 6 | `_handle_shedding_changed` 신규 | `services/throttle/adaptive.py` | ✅ |
| 7 | `check()` 내 `service_id` 분기 추가 | `services/throttle/adaptive.py` | ✅ |
| 8 | `update_shedding_state()` EventBus 발행 추가 | `load_shedding/manager.py` | ✅ |

**테스트 파일** (63 tests, all passed):

| 파일 | 테스트 수 | 커버리지 대상 |
|------|-----------|--------------|
| `tests/unit/throttle/test_tier_mapping.py` | 25 | tier_mapping.py |
| `tests/unit/throttle/test_throttle_load_shedding_integration.py` | 20 | adaptive.py shedding 통합 |
| `tests/unit/throttle/test_load_shedding_manager_eventbus.py` | 7 | manager.py EventBus 발행 |
| `tests/unit/throttle/test_load_shedding_event_type_and_settings.py` | 9+2 | EventType + ThrottleSettings |

**회귀 테스트**: 기존 throttle 207 tests + load shedding 110 tests 전체 통과 확인

# 212. Canary Rollout ↔ AdaptiveThrottle 분석

> **상태**: ❌ 비추천
> **목적**: CanaryRolloutService로 AdaptiveThrottle 설정을 단계 배포할 필요성을 코드 근거로 판단한다.
> **근거 문서**: [207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md](207_ADAPTIVE_THROTTLE_INTEGRATION_OVERVIEW.md)

---

## 1. 현재 코드 구조

### 1-1. CanaryRolloutService

**파일**: `services/canary/service.py` (1096줄)

```python
# service.py L187-195
def create_rollout(
    self,
    config_type: str,           # "circuit_breaker", "dlq", "retry" 등
    new_values: dict[str, Any], # {"failure_threshold": 3}
    stages: list[CanaryStage],  # [CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10)]
    created_by: str,
    reason: str = "",
    force_during_chaos: bool = False,
) -> CanaryRollout:
```

**Canary 핵심 개념**:
- `config_type`: 정적 설정의 종류 (circuit_breaker, dlq, retry 등)
- `CanaryStage.clusters`: 클러스터 목록 (ex. `["seoul-canary", "seoul", "tokyo"]`)
- `CanaryStage.percentage`: 적용 비율 (10% → 50% → 100%)
- 사전에 정해진 새 설정값 (`new_values`)을 클러스터별로 단계 적용

**상태 전이** (models.py L13-18):
```
CREATED → CANARY → PROMOTING → COMPLETED
  │         │         │
  │         ▼         ▼
  │      PAUSED    ROLLED_BACK
  │         │
  ▼         ▼
CANCELLED  FAILED
```

**PassCriteria** (models.py L93-103):
```python
@dataclass
class PassCriteria:
    error_rate_absolute_max: float = 0.05       # 5%
    error_rate_increase_max: float = 0.01       # 1%
    latency_p95_delta_ms: float = 50.0          # p95 50ms
    latency_p99_delta_pct: float = 0.2          # p99 20%
    error_budget_drain_rate_max: float = 1.2    # 1.2x
    error_budget_remaining_min: float = 0.1     # 10%
```

**Governance 연동** (service.py L361-380): `promote()` 시 `check_all_governance()` 호출 (Kill Switch, Emergency, Error Budget 체크)

### 1-2. AdaptiveThrottle의 자체 "점진적 복구" 메커니즘

#### Recovery Dampening

```python
# adaptive.py L562-565
self._recovery_dampening_active: bool = False
self._recovery_dampening_step: int = 0      # 0=80%, 1=90%, 2=100%
self._recovery_dampening_last_time: float = 0.0
self._recovery_dampening_interval_seconds: float = 30.0
```

```python
# adaptive.py L1946 (해당 패턴에서 참조)
RECOVERY_DAMPENING_MULTIPLIERS = (0.8, 0.9, 1.0)
```

- **0단계**: limit × 0.8 (80%) → 30초 대기
- **1단계**: limit × 0.9 (90%) → 30초 대기
- **2단계**: limit × 1.0 (100%) → 복구 완료

#### 사용처

| 트리거 | 코드 위치 |
|--------|-----------|
| Emergency 해제 후 | `_handle_rate_limit_recovery()` → `start_recovery_dampening()` (L741) |
| LEVEL_2→0 전환 시 | `adjust_for_emergency(0)` → `start_recovery_dampening(apply_jitter=True)` (L971) |
| Full Stop 해제 후 | `deactivate_full_stop()` → `start_recovery_dampening()` (L1934) |

**Recovery Dampening은 이미 Canary의 "단계별 적용" 패턴과 동일한 목적**:
- 급격한 limit 복원을 방지
- 80% → 90% → 100%으로 점진적 적용
- 각 단계에서 30초 대기 후 다음 단계 진행

---

## 2. Canary 배포가 적합한 대상 vs 부적합한 대상

### 2-1. Canary가 필요한 조건

| 조건 | Canary 가치 | AdaptiveThrottle 적합? |
|------|------------|----------------------|
| 정적 설정 변경 (사전 결정) | 높음 | ❌ limit는 실시간 자동 조정 |
| 클러스터별 독립 인스턴스 | 높음 | ❌ `get_adaptive_throttle()` 싱글턴 (L2265) |
| 변경 후 수동 모니터링 필요 | 높음 | ❌ Gradient이 자동 모니터링+조정 |
| 롤백 비용 높음 | 높음 | ❌ Gradient이 자동 복구 |

### 2-2. AdaptiveThrottle의 "자동 조정" 특성과 Canary 충돌

**시나리오: `initial_limit=100` → `150`을 Canary로 배포**

```
T=0    Canary Stage 1: seoul-canary 클러스터에 initial_limit=150 적용 (10%)
T=0.5s Gradient: RTT 변화 감지 → current_limit=135 (gradient > 0이면 자동 감소)
T=60s  Canary: PassCriteria 체크 → error_rate OK → promote
T=60s  Canary Stage 2: seoul, tokyo에 initial_limit=150 적용 (100%)
T=60.5s Gradient: 각 클러스터에서 독립 조정 시작...
```

**문제**:
1. `initial_limit`은 초기값일 뿐, `current_limit`은 Gradient이 즉시 조정
2. Canary의 PassCriteria가 관찰하는 메트릭은 `initial_limit` 변경의 효과가 아닌, Gradient 자동 조정의 결과
3. **인과관계 혼재**: Canary가 검증하려는 "새 설정의 안전성"을 Gradient이 자동으로 덮어씀

### 2-3. 클러스터 인프라 부재

```python
# adaptive.py L2265-2270
_adaptive_throttle_instance: AdaptiveThrottle | None = None

def get_adaptive_throttle(config: ThrottleConfig | None = None, ...) -> AdaptiveThrottle:
    """Thread-safe singleton."""
    global _adaptive_throttle_instance
    ...
```

- 싱글턴 패턴: 프로세스당 하나의 인스턴스
- `CanaryStage.clusters` 개념이 없음 — AdaptiveThrottle에는 클러스터 ID 필드 없음
- Redis 기반 클러스터 설정 (`CLUSTER_CONFIG_KEY`, service.py L104)과 연결되는 구조 부재

---

## 3. 연동 장단점

### 3-1. 연동 시 장점

| 장점 | 조건 |
|------|------|
| `sla_warning_ms`, `sla_critical_ms` 등 정적 설정의 안전한 변경 | Canary Stage가 프로세스 수준으로 분할 가능한 경우에만 |
| Audit Trail 통합 | CanaryRollout.id로 변경 추적 가능 |
| Governance 체크 내장 | promote() 시 check_all_governance() 이미 호출 (service.py L361) |

### 3-2. 연동 시 단점

| 단점 | 코드 근거 |
|------|-----------|
| 클러스터 인프라 부재 | `get_adaptive_throttle()` 싱글턴 — 클러스터별 독립 인스턴스 구조 없음 |
| 자동 조정과 충돌 | Gradient이 Canary 배포 효과를 즉시 덮어씀 (2-2절 참조) |
| Recovery Dampening 중복 | 이미 80%→90%→100% 단계적 복구 내장 (adaptive.py L562-565) |
| PassCriteria 검증 무의미 | 검증 대상 메트릭이 "설정 변경 효과"가 아닌 "Gradient 자동 조정 결과" |
| 구현 복잡도 높음 | 싱글턴→클러스터별 인스턴스 전환 필요 → 대규모 리팩토링 |

### 3-3. 분리 유지 시 장점

| 장점 | 코드 근거 |
|------|-----------|
| 아키텍처 정합성 | Canary: 정적 설정 단계 배포, Throttle: 실시간 자동 조정 — 목적 상이 |
| Recovery Dampening 충분 | `RECOVERY_DAMPENING_MULTIPLIERS = (0.8, 0.9, 1.0)` + 30초 간격 — 자체 점진적 복구 |
| 운영 단순 | Throttle은 자율 시스템, Canary 개입 불필요 |
| 정적 설정은 210번 문서(RuntimeFeedback)에서 처리 | AutoTuningService로 SLA 값 조정이 더 적합 |

### 3-4. 분리 유지 시 단점

| 단점 | 크기 |
|------|------|
| 정적 설정(SLA 임계값 등) 변경 시 단계 배포 불가 | 210번 문서의 RuntimeFeedback 연동으로 대체 가능 |

---

## 4. 기존 Canary config_type과의 비교

### 4-1. 현재 지원 config_type (코드에서 확인)

```python
# service.py L187 create_rollout()
# config_type: str  — 입력값 제한 없음 (문자열)
# 사용 예시 (docstring):
config_type="circuit_breaker"
```

| config_type | Canary 적합도 | 이유 |
|-------------|--------------|------|
| `circuit_breaker` | ✅ | 정적 threshold 변경 → 클러스터별 독립 동작 |
| `dlq` | ✅ | 정적 설정 → 단계 배포 가치 있음 |
| `retry` | ✅ | 정적 횟수/간격 → 클러스터별 독립 |
| `throttle` (가상) | ❌ | 자동 조정 시스템 → Canary 검증이 의미 없음 |

### 4-2. AdaptiveThrottle이 다른 config_type과 다른 근본적 이유

| 특성 | circuit_breaker | AdaptiveThrottle |
|------|-----------------|------------------|
| 설정 변경 방식 | 사전 결정 (ex. threshold=3→5) | 실시간 자동 (Gradient) |
| 변경 주기 | 수동/드문 변경 | 500ms마다 자동 조정 |
| 롤백 필요성 | 필요 (수동/Canary) | 불필요 (Gradient 자동 복구) |
| 클러스터 독립성 | 있음 (각 클러스터 별도 설정) | 없음 (싱글턴) |
| 검증 대상 명확성 | 명확 (설정 변경 → 직접 효과) | 불명확 (설정 변경 → Gradient 보정 → 간접 효과) |

---

## 5. 결론

### 비추천 근거 요약

1. **자동 조정 시스템에 단계 배포는 부적합**: AdaptiveThrottle의 Gradient 알고리즘이 매 500ms마다 limit를 자동 조정 → Canary의 "설정 변경 효과 검증"이 무의미
2. **Recovery Dampening이 이미 Canary 역할 수행**: 80% → 90% → 100% 단계적 복구 (adaptive.py L562-565) — 30초 간격
3. **클러스터 인프라 부재**: `get_adaptive_throttle()` 싱글턴 → `CanaryStage.clusters` 적용 불가
4. **정적 설정 변경은 210번 문서(RuntimeFeedback)에서 처리**: SLA 임계값 등의 조정은 AutoTuningService가 ConfigApplier를 통해 수행하는 것이 더 적합

### 권장 액션

| 액션 | 유형 | 내용 |
|------|------|------|
| Canary 연동 안 함 | 현행 유지 | AdaptiveThrottle을 `config_type`으로 추가하지 않음 |
| 정적 설정 조정 | 210번 문서 참조 | SLA 값 등은 AutoTuningService + ConfigApplier로 처리 |
| Recovery Dampening 강화 고려 | 선택적 개선 | 필요 시 3단계(80/90/100) → 5단계(60/70/80/90/100) 확장 |

---

## 6. 검증 항목

- [x] CanaryRolloutService에서 `config_type="throttle"` 사용처 없음 확인 (grep_search)
- [x] `get_adaptive_throttle()` 싱글턴 패턴 확인 (adaptive.py L2265)
- [x] Recovery Dampening 3단계 (0.8, 0.9, 1.0) + 30초 간격 확인 (adaptive.py L562-565)
- [x] Canary promote() 시 check_all_governance() 호출 확인 (service.py L361-380)
- [x] PassCriteria가 error_rate/latency/error_budget 기반 확인 (models.py L93-103)

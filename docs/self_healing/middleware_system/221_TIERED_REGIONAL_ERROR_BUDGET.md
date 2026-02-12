# 221. 티어별 차등 임계치 / 리전별 버짓 분리 설계

> **상태**: 📋 설계 완료 → 🔄 리뷰 반영 완료 (구현 대기)
> **목적**: 현재 단일 글로벌 임계치(10%)로 동작하는 Error Budget Gate를 티어별 차등 임계치와 리전별 버짓 분리로 확장한다.
> **기준일**: 2026-02-12
> **리뷰 반영일**: 2026-02-12
> **선행 문서**: 12_ERROR_BUDGET.md, 70_MULTI_CLUSTER_ARCHITECTURE.md, 74_CANARY_SAFETY_INTERLOCK.md

---

## 1. 현재 상태 분석 (코드 근거)

### 1.1 Error Budget Gate — 단일 글로벌 임계치

**파일**: `settings/error_budget_gate.py` L55-L70

```python
critical_threshold_percent: float = Field(
    default=10.0,
    ge=0.0,
    le=100.0,
    description="이 값 미만이면 자동화 차단 (%)",
)
warning_threshold_percent: float = Field(
    default=20.0,
    ge=0.0,
    le=100.0,
    description="이 값 미만이면 경고 표시 (%)",
)
```

**문제**: `critical_threshold_percent=10.0`이 **모든 서비스에 동일하게** 적용됨. 결제(critical) 서비스와 분석(non_essential) 서비스가 동일한 임계치로 판정.

### 1.2 Gate `_evaluate()` — 티어/리전 분기 없음

**파일**: `services/error_budget_gate/gate.py` L269-L315

```python
def _evaluate(self, budget_percent: float) -> GateCheckResult:
    critical_recovery = (
        self._config.critical_threshold_percent
        + self._config.threshold_hysteresis_buffer_percent
    )
    warning_recovery = (
        self._config.warning_threshold_percent
        + self._config.threshold_hysteresis_buffer_percent
    )
    # ... 히스테리시스 상태 전이
    if budget_percent < self._config.critical_threshold_percent:
        new_status = GateStatus.BLOCKED
```

**문제**: `_evaluate()`가 단일 `budget_percent`만 받으며 **tier 또는 region 컨텍스트 파라미터가 전혀 없음**.

### 1.3 `_get_error_budget_percent()` — 글로벌 단일 조회

**파일**: `services/error_budget_gate/gate.py` L120-L165

```python
def _get_error_budget_percent(self) -> float | None:
    service = get_error_budget_service()
    status = service.get_current_status()
    # ...
    if hasattr(status, "budget_remaining_percent"):
        return status.budget_remaining_percent
```

**문제**: `get_error_budget_service()`는 싱글톤이며 `get_current_status()`에 **region/tier 파라미터 없음**. 리전별 분리 계산 불가.

### 1.4 ErrorBudgetService — 리전/티어 파라미터 없음

**파일**: `services/error_budget/service.py` L86

```python
def get_budget_status(self, slo_name: str = "availability") -> ErrorBudgetStatus:
    """Error Budget 상태 조회."""
    return self.calculator.calculate_budget_status(slo_name)
```

**문제**: `slo_name` 기반 단일 계산만 가능. `region`/`tier` 파라미터 부재.

### 1.5 ErrorBudgetCalculator — 리전/티어 파라미터 없음

**파일**: `services/error_budget/calculator.py` L63-L73

```python
def calculate_budget_status(
    self,
    slo_name: str = "availability",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    exclude_chaos: bool | None = None,
    exclude_synthetic: bool = True,
) -> ErrorBudgetStatus:
```

**문제**: 계산 범위가 글로벌. 리전별 에러 통계 분리 계산 불가.

### 1.6 ErrorBudgetStatus — 리전/티어 필드 없음

**파일**: `services/error_budget/models.py` L23-L47

```python
@dataclass
class ErrorBudgetStatus:
    slo_name: str
    slo_target: float
    window_days: int
    budget_total_minutes: float
    budget_consumed_minutes: float
    budget_remaining_minutes: float
    budget_remaining_percent: float
    burn_rate_1h: float = 0.0
    burn_rate_6h: float = 0.0
    measured_at: datetime = field(default_factory=now)
    error_count_window: int = 0
    total_requests_window: int = 0
```

**문제**: `region`/`tier` 필드 부재. 어떤 리전/티어의 버짓인지 식별 불가.

### 1.7 SLO — 리전/티어 필드 없음

**파일**: `slo.py` L66-L97

```python
@dataclass
class SLO:
    name: str
    sli: SLI
    target: float         # e.g., 0.999
    window_days: int = 30
    description: str | None = None
    service_name: str | None = None
    domain: str | None = None
```

**문제**: `service_name`과 `domain`은 존재하나 `region`/`tier` 필드 없음. 리전별/티어별 SLO 목표 차등 설정 불가.

### 1.8 Prometheus 메트릭 — 리전/티어 레이블 없음

**파일**: `services/metrics/definitions.py` L181-L185

```python
error_budget_remaining_percent = get_or_create_gauge(
    "error_budget_remaining_percent",
    "Error budget remaining as percentage (0-100)",
    ["slo_name", "is_synthetic"],
)
```

**파일**: `services/metrics/definitions.py` L441-L444

```python
canary_governance_blocked_total = get_or_create_counter(
    "selfhealing_canary_governance_blocked_total",
    "Total canary promotions blocked by governance",
    ["block_reason"],
)
```

**문제**: 두 메트릭 모두 `region`/`tier` 레이블 없음. 리전별·티어별 관측성 제로.

### 1.9 `record_error_budget_status()` — 리전/티어 파라미터 없음

**파일**: `services/metrics/recorders.py` L274-L305

```python
def record_error_budget_status(
    slo_name: str,
    remaining_percent: float,
    remaining_minutes: float,
    burn_rate_1h_value: float,
    burn_rate_6h_value: float,
) -> None:
    # ...
    error_budget_remaining_percent.labels(
        slo_name=slo_name,
        is_synthetic=is_synthetic,
    ).set(remaining_percent)
```

**문제**: `region`/`tier` 파라미터 및 레이블 없음.

### 1.10 PassCriteria — 고정 수치, 티어/리전 분기 없음

**파일**: `services/canary/models.py` L82-L101

```python
@dataclass
class PassCriteria:
    error_rate_absolute_max: float = 0.05
    error_rate_increase_max: float = 0.01
    latency_p95_delta_ms: float = 50.0
    latency_p99_delta_pct: float = 0.2
    error_budget_drain_rate_max: float = 1.2   # 고정
    error_budget_remaining_min: float = 0.1    # 고정 10%
    min_requests_required: int = 100
    evaluation_window_seconds: int = 300
```

**문제**: `error_budget_drain_rate_max`와 `error_budget_remaining_min`이 **고정값**. 티어별 차등 기준 불가.

### 1.11 `check_all_governance()` — 리전/티어 파라미터 없음

**파일**: `services/governance/checks.py` L421-L435

```python
def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    service_name: str | None = None,
    domain: str | None = None,
    audit_on_block: bool = True,
) -> GovernanceCheckResult:
```

**문제**: `service_name`/`domain`은 Audit 로깅 전용. **Error Budget 판정에 tier/region 컨텍스트가 전파되지 않음**.

### 1.12 `is_error_budget_blocking()` — 무파라미터 글로벌 판정

**파일**: `services/governance/checks.py` L390-L416

```python
def is_error_budget_blocking() -> tuple[bool, float, float]:
    gate_result = check_automation_allowed()
    result = (
        not gate_result.allowed,
        gate_result.error_budget_percent,
        gate_result.threshold_percent,
    )
```

**문제**: 파라미터 **zero**. 글로벌 단일 판정만 반환.

### 1.13 GateCheckResult — 리전/티어 컨텍스트 없음

**파일**: `services/error_budget_gate/config.py` L53-L80

```python
@dataclass
class GateCheckResult:
    allowed: bool
    status: GateStatus
    error_budget_percent: float | None = None
    threshold_percent: float = 10.0
    reason: str = ""
    recommendation: str = ""
    checked_at: datetime = field(...)
    fail_open_triggered: bool = False
```

**문제**: 결과에 어떤 tier/region에 대한 판정인지 식별 필드 없음.

---

## 2. 기존 인프라 — 확장 가능한 코드 자산

### 2.1 Tier 시스템 (Throttle 계층)

**파일**: `services/throttle/tier_mapping.py` L27-L42

```python
CRITICALITY_TO_TIER: dict[str, str] = {
    "critical": "critical",
    "high": "critical",
    "medium": "standard",
    "low": "non_essential",
}

VALID_TIER_IDS: set[str] = {"critical", "standard", "non_essential"}
```

**활용점**: 3-tier 체계(`critical` / `standard` / `non_essential`)가 이미 정의되어 있음. Error Budget Gate에 동일 체계 적용 가능.

### 2.2 ClusterIdentity — 리전 필드 존재

**파일**: `core/cluster_identity.py` L60-L65

```python
region: str | None = Field(
    default=None,
    validation_alias=AliasChoices(
        "SELFHEALING_NAMESPACE_REGION",
        "region",
    ),
)
```

**활용점**: `SELFHEALING_NAMESPACE_REGION` 환경변수를 통해 리전 식별이 이미 가능. `validate()` 메서드(L114-L145)에서 region 필수 검증도 존재.

### 2.3 RegionalRecoveryPolicy — 리전별 차등 정책 선례

**파일**: `services/coordination/regional_recovery_policy.py` L197-L248

```python
"seoul": RegionalRecoveryConfig(
    error_rate_threshold=0.05,   # 엄격한 5%
    success_rate_threshold=0.98, # 높은 98%
    require_manual_approval=True,
    priority=100,
),
"tokyo": RegionalRecoveryConfig(
    error_rate_threshold=0.10,   # 표준 10%
    success_rate_threshold=0.95,
    priority=50,
),
"oregon": RegionalRecoveryConfig(
    error_rate_threshold=0.15,   # 느슨한 15%
    success_rate_threshold=0.90,
    priority=10,
),
```

**활용점**: 리전별 차등 정책의 **기존 패턴**이 복구 정책에 이미 존재. Error Budget에 동일 패턴 적용 가능. `priority` 필드가 사실상 티어 역할.

### 2.4 RegionalIsolationGate — 리전별 제어 선례

**파일**: `services/isolation/regional_gate.py` L1-L90

```python
class RegionalIsolationGate:
    GATE_KEY_TEMPLATE = "selfhealing:global:isolation:{region}"
    ISOLATION_LIST_KEY = "selfhealing:global:isolation:list"
```

**활용점**: 리전 단위 게이트 제어 패턴 존재. Error Budget Gate에서 리전별 건/block 판정 시 참조 가능.

### 2.5 RegionalInterlockPolicy — 리전별 행동 정책 선례

**파일**: `services/canary/regional.py` L79-L146

```python
class RegionalInterlockPolicy:
    default_behavior: RegionalInterlockBehavior = RegionalInterlockBehavior.PAUSE_ALL
    allow_isolated_rollback: bool = False
    max_affected_regions_for_isolated_rollback: int = 1

    def get_behavior_for_situation(
        self,
        affected_regions: list,
        total_regions: list,
        is_region_isolated_deployment: bool = False,
    ) -> RegionalInterlockBehavior:
```

**활용점**: 리전별 상황 판단 로직 패턴 존재. 리전별 Error Budget 상태에 따른 행동 결정에 동일 패턴 적용 가능.

---

## 3. 갭 분석 종합

| 구성 요소 | 파일 | region 지원 | tier 지원 | 현재 상태 |
|-----------|------|:----------:|:--------:|----------|
| `ErrorBudgetGateSettings` | `settings/error_budget_gate.py` | ❌ | ❌ | 단일 `critical_threshold_percent=10.0` |
| `ErrorBudgetGate._evaluate()` | `services/error_budget_gate/gate.py` | ❌ | ❌ | 글로벌 히스테리시스만 |
| `ErrorBudgetGate._get_error_budget_percent()` | `services/error_budget_gate/gate.py` | ❌ | ❌ | 글로벌 단일 조회 |
| `ErrorBudgetService.get_budget_status()` | `services/error_budget/service.py` | ❌ | ❌ | `slo_name` 기반 단일 |
| `ErrorBudgetCalculator.calculate_budget_status()` | `services/error_budget/calculator.py` | ❌ | ❌ | 글로벌 계산 |
| `ErrorBudgetStatus` | `services/error_budget/models.py` | ❌ | ❌ | region/tier 필드 없음 |
| `SLO` | `slo.py` | ❌ | ❌ | `service_name`, `domain`만 |
| `error_budget_remaining_percent` 메트릭 | `services/metrics/definitions.py` | ❌ | ❌ | `["slo_name", "is_synthetic"]` |
| `canary_governance_blocked_total` 메트릭 | `services/metrics/definitions.py` | ❌ | ❌ | `["block_reason"]` |
| `record_error_budget_status()` | `services/metrics/recorders.py` | ❌ | ❌ | 파라미터 없음 |
| `PassCriteria` | `services/canary/models.py` | ❌ | ❌ | 고정 수치만 |
| `check_all_governance()` | `services/governance/checks.py` | ❌ | ❌ | region/tier 무전파 |
| `is_error_budget_blocking()` | `services/governance/checks.py` | ❌ | ❌ | 무파라미터 |
| `GateCheckResult` | `services/error_budget_gate/config.py` | ❌ | ❌ | 판정 컨텍스트 없음 |
| **ClusterIdentity** | `core/cluster_identity.py` | ✅ | ❌ | `region` 필드 존재 |
| **VALID_TIER_IDS** | `services/throttle/tier_mapping.py` | — | ✅ | 3-tier 체계 정의 |
| **RegionalRecoveryConfig** | `services/coordination/regional_recovery_policy.py` | ✅ | ✅(priority) | 리전별 차등 정책 선례 |
| **RegionalIsolationGate** | `services/isolation/regional_gate.py` | ✅ | ❌ | 리전별 제어 패턴 |

---

## 3A. Phase 0: 선행 작업 — 데이터 소스 리전 확장 ⚠️ Blocker

> **상태**: 🚨 수정 필수 (Blocker)
> **근거**: 원천 데이터(FailedOperation, 콜백 파라미터, Redis 키)에 리전 정보가 없으므로, Calculator/Gate 로직만 확장해서는 리전별 버짓 산출이 불가능.

### 3A.1 FailedOperation — metadata에 region 저장

**파일**: `shopping/models/failed_operation.py` L186-L190

```python
metadata = models.JSONField(
    default=dict,
    blank=True,
    verbose_name="Metadata",
    help_text="Additional debug info: timing, retry history, state snapshots",
)
```

`metadata` JSONField가 **이미 존재**하므로 스키마 변경(마이그레이션) 없이 `metadata["region"]` 키 추가만으로 해결 가능.

**기존 선례**: `services/unified_notification/formatters.py` L191

```python
region = metadata.get("region")
```

이미 `metadata["region"]` 접근 패턴이 실 코드에서 사용 중.

**구현**: `create_from_failure()` (adapters/django/models.py L541-L600) 호출부에서 metadata에 region 주입:

```python
from selfhealing.core.cluster_identity import get_cluster_identity

identity = get_cluster_identity()
metadata = metadata or {}
if identity.region:
    metadata.setdefault("region", identity.region)

# 기존 create 호출
return cls.objects.create(
    ...
    metadata=metadata,
    ...
)
```

**FailedOperationData DTO**: `interfaces/repositories.py` L103 — `FailedOperationData.metadata: dict[str, Any]`는 generic dict이므로 DTO 변경 불필요. `metadata.get("region")`으로 접근.

### 3A.2 콜백 확장: region 파라미터 추가

**파일**: `services/error_budget/calculator.py` L113-L140

현재 콜백 시그니처:

```python
stats = self._get_failed_operation_stats(
    start_time=window_start,
    end_time=current_time,
    exclude_synthetic=exclude_synthetic,
)
```

확장 후:

```python
stats = self._get_failed_operation_stats(
    start_time=window_start,
    end_time=current_time,
    exclude_synthetic=exclude_synthetic,
    region=region,                          # Phase 0에서 추가
)
```

**하위 호환**: Calculator가 이미 `TypeError` 기반 polyfill을 사용 중 (L118-L135). 동일 패턴으로 `region` 파라미터도 graceful 처리:

```python
try:
    stats = self._get_failed_operation_stats(
        start_time=window_start,
        end_time=current_time,
        exclude_synthetic=exclude_synthetic,
        region=region,
    )
except TypeError:
    # region 미지원 콜백 — 글로벌 통계 반환
    stats = self._get_failed_operation_stats(
        start_time=window_start,
        end_time=current_time,
        exclude_synthetic=exclude_synthetic,
    )
```

### 3A.3 Redis 키 마이그레이션

**파일**: `services/error_budget_gate/redis_flag.py` L18-L20

현재:

```python
BUDGET_EXHAUSTED_FLAG_KEY = "selfhealing:error_budget:exhausted"
BUDGET_EXHAUSTED_BY_SLO_KEY = "selfhealing:error_budget:exhausted:{slo_name}"
BUDGET_STATUS_KEY = "selfhealing:error_budget:status:{slo_name}"
```

확장:

```python
# 기존 키 (하위 호환 유지)
BUDGET_EXHAUSTED_FLAG_KEY = "selfhealing:error_budget:exhausted"
BUDGET_EXHAUSTED_BY_SLO_KEY = "selfhealing:error_budget:exhausted:{slo_name}"
BUDGET_STATUS_KEY = "selfhealing:error_budget:status:{slo_name}"

# 리전별 키 (Phase 0 추가)
BUDGET_EXHAUSTED_BY_SLO_REGION_KEY = "selfhealing:error_budget:exhausted:{slo_name}:{region}"
BUDGET_STATUS_REGION_KEY = "selfhealing:error_budget:status:{slo_name}:{region}"
```

**키 패턴 근거**: `RegionalIsolationGate`의 `selfhealing:global:isolation:{region}` (services/isolation/regional_gate.py L77)과 동일한 `:{region}` 접미 패턴.

**하위 호환**: `region=None`이면 기존 키(`{slo_name}`) 사용, `region`이 있으면 확장 키(`{slo_name}:{region}`) 사용:

```python
def _get_slo_key(self, slo_name: str, region: str | None = None) -> str:
    if region:
        return BUDGET_EXHAUSTED_BY_SLO_REGION_KEY.format(
            slo_name=slo_name, region=region
        )
    return BUDGET_EXHAUSTED_BY_SLO_KEY.format(slo_name=slo_name)
```

### 3A.4 Phase 0 변경 영향 파일 목록

| # | 파일 | 변경 내용 |
|---|------|----------|
| P0-1 | `adapters/django/models.py` | `create_from_failure()`에서 `metadata["region"]` 자동 주입 |
| P0-2 | `shopping/models/failed_operation.py` | `create_from_failure()`에서 `metadata["region"]` 자동 주입 |
| P0-3 | `services/error_budget/calculator.py` | 콜백 호출 시 `region` 파라미터 전달 + TypeError polyfill |
| P0-4 | `services/error_budget_gate/redis_flag.py` | 리전별 키 상수 추가, `_get_slo_key()` 메서드 추가 |
| P0-5 | `services/error_budget/service.py` | `_simulation_stats_callback`에 `region` 파라미터 수용 |

---

## 4. 구현 설계: 티어별 차등 임계치

### 4.1 `ErrorBudgetGateSettings` 확장

**대상 파일**: `settings/error_budget_gate.py`

기존 단일 `critical_threshold_percent` / `warning_threshold_percent`에 티어별 오버라이드 맵 추가.

```python
# --- 추가할 필드 (L70 이후) ---

tier_thresholds: dict[str, dict[str, float]] = Field(
    default={
        "critical": {
            "critical_threshold_percent": 15.0,   # 더 보수적
            "warning_threshold_percent": 30.0,
        },
        "standard": {
            "critical_threshold_percent": 10.0,   # 기본값 유지
            "warning_threshold_percent": 20.0,
        },
        "non_essential": {
            "critical_threshold_percent": 5.0,    # 더 관대
            "warning_threshold_percent": 10.0,
        },
    },
    description="티어별 차등 임계치 오버라이드. VALID_TIER_IDS 기반.",
)

tier_thresholds_enabled: bool = Field(
    default=False,
    description="티어별 차등 임계치 활성화 여부 (False면 기존 글로벌 임계치 사용)",
)
```

**근거**: `VALID_TIER_IDS = {"critical", "standard", "non_essential"}` (tier_mapping.py L42)의 키 체계 그대로 사용.

**환경변수 예시**:
```bash
SELFHEALING_ERROR_BUDGET_GATE_TIER_THRESHOLDS_ENABLED=true
SELFHEALING_ERROR_BUDGET_GATE_TIER_THRESHOLDS='{"critical":{"critical_threshold_percent":15.0,"warning_threshold_percent":30.0},"standard":{"critical_threshold_percent":10.0,"warning_threshold_percent":20.0},"non_essential":{"critical_threshold_percent":5.0,"warning_threshold_percent":10.0}}'
```

### 4.2 임계치 해석 메서드 추가

**대상 파일**: `settings/error_budget_gate.py`

```python
def get_thresholds_for_tier(self, tier_id: str) -> tuple[float, float]:
    """
    티어별 (critical_threshold, warning_threshold) 반환.

    tier_thresholds_enabled=False면 글로벌 임계치 반환.

    Args:
        tier_id: "critical" | "standard" | "non_essential"

    Returns:
        (critical_threshold_percent, warning_threshold_percent)
    """
    if not self.tier_thresholds_enabled:
        return self.critical_threshold_percent, self.warning_threshold_percent

    tier_config = self.tier_thresholds.get(tier_id)
    if tier_config is None:
        return self.critical_threshold_percent, self.warning_threshold_percent

    return (
        tier_config.get("critical_threshold_percent", self.critical_threshold_percent),
        tier_config.get("warning_threshold_percent", self.warning_threshold_percent),
    )
```

### 4.3 `ErrorBudgetGate._evaluate()` 확장

**대상 파일**: `services/error_budget_gate/gate.py` L269

```python
# --- 기존 ---
def _evaluate(self, budget_percent: float) -> GateCheckResult:

# --- 변경 ---
def _evaluate(
    self,
    budget_percent: float,
    tier_id: str | None = None,
) -> GateCheckResult:
```

내부 로직 변경:

```python
# --- 기존 ---
critical_recovery = (
    self._config.critical_threshold_percent
    + self._config.threshold_hysteresis_buffer_percent
)
warning_recovery = (
    self._config.warning_threshold_percent
    + self._config.threshold_hysteresis_buffer_percent
)

# --- 변경 ---
critical_threshold, warning_threshold = self._config.get_thresholds_for_tier(
    tier_id or "standard"
)
critical_recovery = critical_threshold + self._config.threshold_hysteresis_buffer_percent
warning_recovery = warning_threshold + self._config.threshold_hysteresis_buffer_percent
```

**하위 호환**: `tier_id=None`이면 기존 글로벌 임계치 사용 (fallback to `"standard"`).

**Caller 책임 원칙**: Gate는 싱글톤이므로 서비스 컨텍스트를 알 수 없음. **Caller가 `tier_id`를 전달**하는 방식을 채택.

- **근거**: 기존 `TierRegistry.resolve_tier(path, client_ip, ...)` (api/django/tiering/registry.py L314)도 Caller(미들웨어)가 컨텍스트를 전달하는 동일 패턴.
- **점진적 마이그레이션**: `tier_id=None` 기본값으로 5곳 호출부(`governance/checks.py`, `retry_handler/handler.py`, `chaos/scheduler/service.py`, `celery/tasks/circuit_breaker.py`, `backoff_calculator/calculator.py`)를 순차 전환.
- **Resolver 연계**: Caller가 tier를 모르고 region만 아는 경우, `resolve_tier_from_region(region)` 유틸리티(§9 참조)로 tier를 취득 후 전달.

### 4.4 `check()` / `require()` 시그니처 확장

**대상 파일**: `services/error_budget_gate/gate.py`

```python
# --- 기존 ---
def check(self, force_refresh: bool = False) -> GateCheckResult:

# --- 변경 ---
def check(
    self,
    force_refresh: bool = False,
    tier_id: str | None = None,
) -> GateCheckResult:
```

캐시 키도 `tier_id`를 포함하도록 확장 필요. 현재 `self._cache: GateCheckResult | None` (L57)이 단일 캐시이므로:

캐시 키 상수 정의 (매직 스트링 제거):

```python
# --- 상수 추가 (gate.py 상단) ---
CACHE_KEY_GLOBAL = "__global__"
"""티어/리전 미지정 시 기본 캐시 키. throttle/registry.py의 '__default__' 패턴 참조."""
```

```python
# --- 기존 ---
self._cache: GateCheckResult | None = None

# --- 변경 ---
self._cache: dict[str, GateCheckResult] = {}  # key: f"{region or CACHE_KEY_GLOBAL}:{tier_id or CACHE_KEY_GLOBAL}"
```

캐시 키 생성 헬퍼:

```python
@staticmethod
def _cache_key(region: str | None, tier_id: str | None) -> str:
    return f"{region or CACHE_KEY_GLOBAL}:{tier_id or CACHE_KEY_GLOBAL}"
```

### 4.5 `GateCheckResult` 확장

**대상 파일**: `services/error_budget_gate/config.py`

```python
# --- 추가할 필드 ---
tier_id: str | None = None
"""판정 대상 티어 (None이면 글로벌)."""
```

### 4.6 `check_all_governance()` 확장

**대상 파일**: `services/governance/checks.py` L421

```python
# --- 기존 ---
def check_all_governance(
    ...
    service_name: str | None = None,
    domain: str | None = None,
    ...
) -> GovernanceCheckResult:

# --- 변경 ---
def check_all_governance(
    ...
    service_name: str | None = None,
    domain: str | None = None,
    tier_id: str | None = None,          # 추가
    ...
) -> GovernanceCheckResult:
```

내부에서 `is_error_budget_blocking(tier_id=tier_id)` 호출로 전파.

### 4.7 `is_error_budget_blocking()` 확장

**대상 파일**: `services/governance/checks.py` L390

```python
# --- 기존 ---
def is_error_budget_blocking() -> tuple[bool, float, float]:

# --- 변경 ---
def is_error_budget_blocking(
    tier_id: str | None = None,
) -> tuple[bool, float, float]:
```

내부에서 `check_automation_allowed(tier_id=tier_id)` 전파.

### 4.8 `PassCriteria` 확장

**대상 파일**: `services/canary/models.py` L82

```python
# --- 추가할 class method ---
@classmethod
def for_tier(cls, tier_id: str) -> PassCriteria:
    """
    티어별 PassCriteria 반환.

    tier_mapping.py VALID_TIER_IDS 기반.
    """
    TIER_PASS_CRITERIA: dict[str, dict] = {
        "critical": {
            "error_budget_drain_rate_max": 0.8,
            "error_budget_remaining_min": 0.15,
            "error_rate_absolute_max": 0.03,
        },
        "standard": {
            "error_budget_drain_rate_max": 1.2,
            "error_budget_remaining_min": 0.10,
            "error_rate_absolute_max": 0.05,
        },
        "non_essential": {
            "error_budget_drain_rate_max": 2.0,
            "error_budget_remaining_min": 0.05,
            "error_rate_absolute_max": 0.10,
        },
    }
    overrides = TIER_PASS_CRITERIA.get(tier_id, {})
    return cls(**overrides)
```

### 4.9 `apply_tier_floor()` — 티어별 최소 보안 기준 강제

> **리뷰 반영**: 현재 PassCriteria는 사용자 설정이 절대적이며 Tier 강제(Hard Limit)가 없음.

**정의 위치**: `services/canary/models.py` (PassCriteria.for_tier() 옆)
**호출 위치**: `services/canary/service.py`의 evaluate 로직 직전

사용자가 PassCriteria를 임의로 완화해도 Tier별 Hard Limit을 강제하는 함수:

```python
def apply_tier_floor(user_criteria: PassCriteria, tier_id: str) -> PassCriteria:
    """
    사용자 설정과 Tier 최소 기준 중 더 엄격한 값을 적용.

    - 'max' 계열 필드: min(user, tier) — 더 작은(엄격한) 값 채택
    - 'min' 계열 필드: max(user, tier) — 더 큰(엄격한) 값 채택

    Args:
        user_criteria: 사용자/스테이지가 설정한 PassCriteria
        tier_id: "critical" | "standard" | "non_essential"

    Returns:
        Tier 최소 기준이 강제된 PassCriteria
    """
    tier_floor = PassCriteria.for_tier(tier_id)
    return PassCriteria(
        # max 계열: 더 작은(엄격한) 값
        error_rate_absolute_max=min(
            user_criteria.error_rate_absolute_max,
            tier_floor.error_rate_absolute_max,
        ),
        error_rate_increase_max=min(
            user_criteria.error_rate_increase_max,
            tier_floor.error_rate_increase_max,
        ),
        error_budget_drain_rate_max=min(
            user_criteria.error_budget_drain_rate_max,
            tier_floor.error_budget_drain_rate_max,
        ),
        # min 계열: 더 큰(엄격한) 값
        error_budget_remaining_min=max(
            user_criteria.error_budget_remaining_min,
            tier_floor.error_budget_remaining_min,
        ),
        min_requests_required=max(
            user_criteria.min_requests_required,
            tier_floor.min_requests_required,
        ),
        # 나머지: 사용자 설정 유지
        latency_p95_delta_ms=user_criteria.latency_p95_delta_ms,
        latency_p99_delta_pct=user_criteria.latency_p99_delta_pct,
        evaluation_window_seconds=user_criteria.evaluation_window_seconds,
    )
```

**service.py 호출 예시**:

```python
# services/canary/service.py — evaluate 직전
if tier_id:
    effective_criteria = apply_tier_floor(stage.pass_criteria, tier_id)
else:
    effective_criteria = stage.pass_criteria

passed, failure_reason = effective_criteria.evaluate(metrics)
```

---

## 5. 구현 설계: 리전별 버짓 분리

### 5.1 `ErrorBudgetStatus` 확장

**대상 파일**: `services/error_budget/models.py`

```python
# --- 추가할 필드 ---
region: str | None = None
"""버짓이 속한 리전 (None이면 글로벌 집계)."""

tier_id: str | None = None
"""버짓이 속한 티어 (None이면 전체)."""
```

### 5.2 `SLO` 확장

**대상 파일**: `slo.py` L66

```python
# --- 추가할 필드 ---
region: str | None = None
"""SLO가 적용되는 리전 (None이면 전역)."""
```

기존 `service_name`/`domain`과 동일 패턴으로 선택적 필드 추가.

### 5.3 `ErrorBudgetCalculator.calculate_budget_status()` 확장

**대상 파일**: `services/error_budget/calculator.py` L63

```python
# --- 기존 ---
def calculate_budget_status(
    self,
    slo_name: str = "availability",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    exclude_synthetic: bool = True,
) -> ErrorBudgetStatus:

# --- 변경 ---
def calculate_budget_status(
    self,
    slo_name: str = "availability",
    window_start: datetime | None = None,
    window_end: datetime | None = None,
    exclude_synthetic: bool = True,
    region: str | None = None,              # 추가
) -> ErrorBudgetStatus:
```

리전 파라미터를 `_get_failed_operation_stats()`와 `_get_request_stats()` 콜백에 전달:

```python
stats = self._get_failed_operation_stats(
    start_time=window_start,
    end_time=current_time,
    exclude_synthetic=exclude_synthetic,
    region=region,           # 추가 전달
)
```

반환 시 region 필드 설정:

```python
return ErrorBudgetStatus(
    slo_name=slo.name,
    # ... 기존 필드 ...
    region=region,           # 추가
)
```

### 5.4 `ErrorBudgetService` 확장

**대상 파일**: `services/error_budget/service.py` L86

**`region=None` 자동 해석**: `ClusterIdentity.region`을 사용하여 현재 리전으로 자동 해석.

- **근거**: `ClusterIdentity.namespace` property (core/cluster_identity.py L90-L92) — `return self.region or self.tenant or self.environment` — `None`이면 현재 인스턴스의 식별자를 사용하는 기존 패턴.
- **`multiplier.py`, `backfill.py`의 `namespace=None`도 "현재 인스턴스" 의미**.
- **"전체 합산(Aggregation)"은 현 단계에서 제외** — 단일 클러스터 배포이므로 cross-region 집계 인프라 불필요.

```python
# --- 기존 ---
def get_budget_status(self, slo_name: str = "availability") -> ErrorBudgetStatus:

# --- 변경 ---
def get_budget_status(
    self,
    slo_name: str = "availability",
    region: str | None = None,
) -> ErrorBudgetStatus:
    """
    Error Budget 상태 조회.

    Args:
        slo_name: SLO 이름
        region: 리전 식별자.
                None이면 ClusterIdentity.region (현재 리전) 자동 사용.
                명시적 "global" 문자열은 미사용 — None이 글로벌/현재 리전 의미.
    """
    if region is None:
        from selfhealing.core.cluster_identity import get_cluster_identity
        identity = get_cluster_identity()
        region = identity.region  # None 유지 가능 (region 미설정 환경)
    return self.calculator.calculate_budget_status(slo_name, region=region)
```

리전별 조회를 위한 편의 메서드 추가 (Phase 6 선택적 확장):

```python
def get_all_region_statuses(
    self,
    slo_name: str = "availability",
    regions: list[str] | None = None,
) -> dict[str, ErrorBudgetStatus]:
    """
    전체 리전별 Error Budget 상태 조회.

    Args:
        slo_name: SLO 이름
        regions: 조회할 리전 목록 (None이면 ClusterIdentity.region 단일 조회)

    Returns:
        {region: ErrorBudgetStatus} 딕셔너리
    """
    if regions is None:
        from selfhealing.core.cluster_identity import get_cluster_identity
        identity = get_cluster_identity()
        regions = [identity.region] if identity.region else []

    return {
        r: self.calculator.calculate_budget_status(slo_name, region=r)
        for r in regions
    }
```

### 5.5 `ErrorBudgetGate._get_error_budget_percent()` 확장 + 버그 수정

**대상 파일**: `services/error_budget_gate/gate.py` L120

⚠️ **기존 버그 수정 포함**: L136의 `service.get_current_status()`는 **ErrorBudgetService에 존재하지 않는 메서드**. 실제 메서드는 `get_budget_status()`. 현재는 try-except에 감싸져 있어 항상 `None` 반환 → Fail-Open 동작. 사실상 **Error Budget을 조회하지 않는 상태**.
(§12.1 참조 → Phase 3 구현 과제 [12a]로 편입)

```python
# --- 기존 (버그) ---
def _get_error_budget_percent(self) -> float | None:
    service = get_error_budget_service()
    status = service.get_current_status()    # ← 존재하지 않는 메서드!

# --- 변경 (버그 수정 + 리전 확장 + Fallback) ---
def _get_error_budget_percent(
    self,
    region: str | None = None,
) -> float | None:
    service = get_error_budget_service()
    status = service.get_budget_status(region=region)  # 실제 존재하는 메서드

    # Fallback: 리전 데이터 Missing 시 글로벌 버짓으로 대체
    if status is None and region is not None:
        logger.warning(
            f"[ErrorBudgetGate] Region '{region}' data missing, "
            "falling back to global budget"
        )
        status = service.get_budget_status(region=None)
```

**Fallback 정책 근거** (§12.5 상세):
- `redis_flag.py` L103: `# 3. Fail-Open: 소진되지 않은 것으로 가정` — Fail-Open 철학
- `gate.py` L39: `Fail-open 설계 (게이트 장애 시 자동화 허용)`
- Global Fallback은 완전 무방비(100%)가 아닌 최소한의 보호(글로벌 임계치 적용) 제공
- `RegionalRecoveryPolicy`의 `"global"` config가 default fallback 역할을 하는 기존 선례와 일치

### 5.6 리전별 Settings 확장

**대상 파일**: `settings/error_budget_gate.py`

```python
# --- 추가할 필드 ---

regional_thresholds: dict[str, dict[str, float]] = Field(
    default={},
    description=(
        "리전별 임계치 오버라이드. "
        "키는 ClusterIdentity.region 값과 일치해야 함. "
        "예: {'seoul': {'critical_threshold_percent': 15.0}}"
    ),
)

regional_thresholds_enabled: bool = Field(
    default=False,
    description="리전별 임계치 오버라이드 활성화 여부",
)
```

해석 메서드:

```python
def get_thresholds_for_region(self, region: str) -> tuple[float, float]:
    """
    리전별 (critical_threshold, warning_threshold) 반환.

    regional_thresholds_enabled=False면 글로벌 임계치 반환.

    Args:
        region: 리전 식별자 (e.g., "seoul", "tokyo")

    Returns:
        (critical_threshold_percent, warning_threshold_percent)
    """
    if not self.regional_thresholds_enabled:
        return self.critical_threshold_percent, self.warning_threshold_percent

    region_config = self.regional_thresholds.get(region)
    if region_config is None:
        return self.critical_threshold_percent, self.warning_threshold_percent

    return (
        region_config.get("critical_threshold_percent", self.critical_threshold_percent),
        region_config.get("warning_threshold_percent", self.warning_threshold_percent),
    )
```

### 5.7 Prometheus 메트릭 확장

**대상 파일**: `services/metrics/definitions.py` L181

```python
# --- 기존 ---
error_budget_remaining_percent = get_or_create_gauge(
    "error_budget_remaining_percent",
    "Error budget remaining as percentage (0-100)",
    ["slo_name", "is_synthetic"],
)

# --- 변경 ---
error_budget_remaining_percent = get_or_create_gauge(
    "error_budget_remaining_percent",
    "Error budget remaining as percentage (0-100)",
    ["slo_name", "is_synthetic", "region", "tier"],
)
```

**대상 파일**: `services/metrics/definitions.py` L441

```python
# --- 기존 ---
canary_governance_blocked_total = get_or_create_counter(
    "selfhealing_canary_governance_blocked_total",
    "Total canary promotions blocked by governance",
    ["block_reason"],
)

# --- 변경 ---
canary_governance_blocked_total = get_or_create_counter(
    "selfhealing_canary_governance_blocked_total",
    "Total canary promotions blocked by governance",
    ["block_reason", "region", "tier"],
)
```

### 5.8 `record_error_budget_status()` 확장

**대상 파일**: `services/metrics/recorders.py` L274

```python
# --- 기존 ---
def record_error_budget_status(
    slo_name: str,
    remaining_percent: float,
    remaining_minutes: float,
    burn_rate_1h_value: float,
    burn_rate_6h_value: float,
) -> None:

# --- 변경 ---
def record_error_budget_status(
    slo_name: str,
    remaining_percent: float,
    remaining_minutes: float,
    burn_rate_1h_value: float,
    burn_rate_6h_value: float,
    region: str = "",
    tier: str = "",
) -> None:
```

내부:

```python
error_budget_remaining_percent.labels(
    slo_name=slo_name,
    is_synthetic=is_synthetic,
    region=region,
    tier=tier,
).set(remaining_percent)
```

---

## 6. 결합 행렬: 티어 × 리전

티어 차등 임계치와 리전 분리가 동시에 활성화될 때의 우선순위 결정 로직:

```
우선순위: regional_thresholds > tier_thresholds > global_thresholds
```

**해석 메서드** (Settings에 추가):

```python
def get_effective_thresholds(
    self,
    tier_id: str | None = None,
    region: str | None = None,
) -> tuple[float, float]:
    """
    최종 적용 임계치 반환.

    우선순위:
    1. regional_thresholds[region] (리전 명시 오버라이드)
    2. tier_thresholds[tier_id] (티어별 기본값)
    3. global (critical_threshold_percent, warning_threshold_percent)

    Returns:
        (critical_threshold_percent, warning_threshold_percent)
    """
    # 1단계: 리전 오버라이드 확인
    if region and self.regional_thresholds_enabled:
        region_config = self.regional_thresholds.get(region)
        if region_config:
            return (
                region_config.get("critical_threshold_percent", self.critical_threshold_percent),
                region_config.get("warning_threshold_percent", self.warning_threshold_percent),
            )

    # 2단계: 티어별 확인
    if tier_id and self.tier_thresholds_enabled:
        return self.get_thresholds_for_tier(tier_id)

    # 3단계: 글로벌 기본값
    return self.critical_threshold_percent, self.warning_threshold_percent
```

---

## 7. 하위 호환 보장 전략

| 항목 | 전략 | 근거 |
|------|------|------|
| `tier_thresholds_enabled=False` (기본값) | 기존 글로벌 임계치 사용 | 모든 기존 호출자 영향 없음 |
| `regional_thresholds_enabled=False` (기본값) | 기존 글로벌 버짓 사용 | 모든 기존 호출자 영향 없음 |
| `_evaluate(budget_percent)` | `tier_id=None` 기본값 | 기존 `check()` 호출 시그니처 유지 |
| `calculate_budget_status(slo_name)` | `region=None` 기본값 | 기존 `get_budget_status()` 호출 유지 |
| `is_error_budget_blocking()` | `tier_id=None` 기본값 | 기존 무파라미터 호출 유지 |
| `record_error_budget_status(...)` | `region=""`, `tier=""` 기본값 | 기존 호출자에 빈 레이블 전달 |
| `GateCheckResult.tier_id` | `None` 기본값 | 기존 `to_dict()` 출력 변화 최소 |
| `ErrorBudgetStatus.region` | `None` 기본값 | 기존 `to_dict()` 출력 호환 |

---

## 8. 변경 영향 파일 목록

### 8.0 선행 변경 (Phase 0 — Blocker)

| # | 파일 | 변경 내용 |
|---|------|----------|
| P0-1 | `adapters/django/models.py` | `create_from_failure()`에서 `metadata["region"]` 자동 주입 |
| P0-2 | `shopping/models/failed_operation.py` | `create_from_failure()`에서 `metadata["region"]` 자동 주입 |
| P0-3 | `services/error_budget/calculator.py` | 콜백 호출 시 `region` 파라미터 전달 + TypeError polyfill |
| P0-4 | `services/error_budget_gate/redis_flag.py` | 리전별 키 상수 추가, `_get_slo_key()` 메서드 추가 |
| P0-5 | `services/error_budget/service.py` | `_simulation_stats_callback`에 `region` 파라미터 수용 |

### 8.1 필수 변경 (Core)

| # | 파일 | 변경 내용 |
|---|------|----------|
| 1 | `settings/error_budget_gate.py` | `tier_thresholds`, `regional_thresholds` 필드 추가, 해석 메서드 추가 |
| 2 | `services/error_budget_gate/gate.py` | `_evaluate()`, `_get_error_budget_percent()`, `check()` 시그니처 확장, 캐시 구조 변경, `get_current_status()` 버그 수정, Fallback 로직 |
| 3 | `services/error_budget_gate/config.py` | `GateCheckResult`에 `tier_id`, `region` 필드 추가 |
| 4 | `services/error_budget/models.py` | `ErrorBudgetStatus`에 `region`, `tier_id` 필드 추가 |
| 5 | `services/error_budget/calculator.py` | `calculate_budget_status()`에 `region` 파라미터 추가 |
| 6 | `services/error_budget/service.py` | `get_budget_status()`에 `region` 추가 + `region=None` 자동 해석 |
| 7 | `slo.py` | `SLO`에 `region` 필드 추가 |

### 8.2 연계 변경 (Integration)

| # | 파일 | 변경 내용 |
|---|------|----------|
| 8 | `services/governance/checks.py` | `check_all_governance()`, `is_error_budget_blocking()`에 `tier_id`/`region` 파라미터 추가 |
| 9 | `services/canary/models.py` | `PassCriteria.for_tier()` 클래스 메서드 + `apply_tier_floor()` 함수 추가 |
| 9a | `services/canary/service.py` | evaluate 직전 `apply_tier_floor()` 호출 삽입 |
| 10 | `services/metrics/definitions.py` | `error_budget_remaining_percent`, `canary_governance_blocked_total` 레이블 확장 |
| 11 | `services/metrics/recorders.py` | `record_error_budget_status()` 파라미터 확장 |
| 11a | `services/error_budget_gate/region_tier_resolver.py` | `resolve_tier_from_region()` 함수 (신규 파일, §9 참조) |

### 8.3 테스트 영향

| # | 테스트 파일 | 영향 |
|---|------------|------|
| P0-T | `tests/unit/services/error_budget/` | Phase 0: 콜백 region 파라미터 전달 테스트, Redis 키 리전별 분리 테스트 |
| 12 | `tests/unit/services/error_budget_gate/` | `_evaluate()` 시그니처 변경, `get_current_status()` 버그 수정, Fallback 로직 테스트 |
| 13 | `tests/unit/services/error_budget/` | `calculate_budget_status()` region 파라미터 테스트 추가 |
| 14 | `tests/unit/services/governance/` | `check_all_governance()` tier_id 전파 테스트 추가 |
| 15 | `tests/unit/services/canary/` | `PassCriteria.for_tier()`, `apply_tier_floor()` 테스트 추가 |
| 16 | `tests/unit/services/metrics/` | 레이블 확장에 따른 메트릭 기록 테스트 수정 |

---

## 9. RegionalRecoveryPolicy 연동

`RegionalRecoveryConfig`의 `priority` 필드(L128)가 사실상 티어 역할을 수행:

| 리전 | priority | 암시적 티어 | error_rate_threshold |
|------|----------|-----------|---------------------|
| seoul | 100 | critical | 0.05 |
| tokyo | 50 | standard | 0.10 |
| oregon | 10 | non_essential | 0.15 |
| global | 0 | non_essential | 0.10 |

리전→티어 매핑 유틸리티. **Caller가 tier를 모르고 region만 아는 경우 필수** → Phase 2 (Settings 확장) 단계에서 구현.

**모듈 위치 선택**: `services/error_budget_gate/region_tier_resolver.py` (신규 파일)

- ❌ ~~`services/throttle/tier_mapping.py` 확장~~ — `tier_mapping.py`는 `services/throttle/` 하위이며, `regional_recovery_policy`(coordination 패키지)에 대한 의존이 생기면 cross-service 의존 발생.
- ✅ `services/error_budget_gate/region_tier_resolver.py` — error_budget_gate는 상위 통합 레이어이므로 coordination 패키지 의존이 자연스러움.
- **네이밍 충돌 없음**: `resolve_tier_from_region`은 코드베이스 미존재. 기존 `TierRegistry.resolve_tier()`(api/django/tiering/registry.py L314)는 API path 기반이므로 **용도가 다름**.

```python
# services/error_budget_gate/region_tier_resolver.py (신규)

from selfhealing.services.coordination.regional_recovery_policy import (
    get_default_regional_configs,
)
from selfhealing.services.throttle.tier_mapping import VALID_TIER_IDS

# priority 기반 자동 매핑
PRIORITY_TO_TIER: dict[range, str] = {
    range(70, 200): "critical",       # priority >= 70
    range(30, 70): "standard",        # 30 <= priority < 70
    range(0, 30): "non_essential",    # priority < 30
}

def resolve_tier_from_region(region: str) -> str:
    """
    리전에서 티어를 추론.

    RegionalRecoveryConfig.priority 기반.
    TierRegistry.resolve_tier()와 다름:
    - TierRegistry: API 요청(path/IP)에서 tier 결정
    - 이 함수: 리전 자체의 중요도에서 tier 결정
    """
    configs = get_default_regional_configs()
    config = configs.get(region)
    if config is None:
        return "standard"

    for priority_range, tier_id in PRIORITY_TO_TIER.items():
        if config.priority in priority_range:
            return tier_id

    return "standard"
```

---

## 10. Prometheus/Grafana 대시보드 영향

### 10.1 기존 Alerting Rule 영향

**파일**: `services/metrics/alerting_rules.py` L122, L131

```python
"expr": "error_budget_remaining_percent < 20",
"expr": "error_budget_remaining_percent < 50",
```

리전/티어 레이블 추가 후 PromQL 수정 필요:

```promql
# 기존 (글로벌)
error_budget_remaining_percent < 20

# 확장 (리전별)
error_budget_remaining_percent{region="seoul"} < 20

# 확장 (티어별)
error_budget_remaining_percent{tier="critical"} < 30
error_budget_remaining_percent{tier="standard"} < 20
error_budget_remaining_percent{tier="non_essential"} < 10
```

### 10.2 신규 Grafana 패널

| 패널 | PromQL | 용도 |
|------|--------|------|
| 리전별 버짓 잔여율 | `error_budget_remaining_percent{region=~".+"}` | 리전 간 버짓 비교 |
| 티어별 거버넌스 차단 | `rate(selfhealing_canary_governance_blocked_total{tier=~".+"}[5m])` | 티어별 차단 빈도 |
| 리전×티어 히트맵 | `error_budget_remaining_percent` group by `region`, `tier` | 전체 매트릭스 뷰 |

---

## 11. 구현 순서 (의존성 기반)

```
Phase 0: 선행 작업 — 데이터 소스 리전 확장 ⚠️ Blocker
  ├─ [P0-1] FailedOperation.create_from_failure()에 metadata["region"] 자동 주입
  ├─ [P0-2] ErrorBudgetCalculator 콜백에 region 파라미터 추가 + TypeError polyfill
  ├─ [P0-3] redis_flag.py 리전별 키 상수 및 _get_slo_key() 추가
  ├─ [P0-4] _simulation_stats_callback에 region 파라미터 수용
  └─ [P0-5] Phase 0 단위 테스트

Phase 1: 모델 확장 (하위 호환, 기능 OFF)
  ├─ [1] ErrorBudgetStatus에 region/tier_id 필드 추가
  ├─ [2] SLO에 region 필드 추가
  ├─ [3] GateCheckResult에 tier_id/region 필드 추가
  └─ [4] 기존 테스트 통과 확인

Phase 2: Settings 확장 + RegionTierResolver
  ├─ [5] ErrorBudgetGateSettings에 tier_thresholds 추가
  ├─ [6] ErrorBudgetGateSettings에 regional_thresholds 추가
  ├─ [7] get_thresholds_for_tier(), get_thresholds_for_region(),
  │      get_effective_thresholds() 메서드 추가
  ├─ [8] Settings 단위 테스트
  └─ [8a] region_tier_resolver.py 신규 (Caller가 tier 모를 때 필수)

Phase 3: 핵심 로직 확장
  ├─ [9] ErrorBudgetCalculator.calculate_budget_status() region 파라미터
  ├─ [10] ErrorBudgetService.get_budget_status() region 파라미터 + region=None 자동 해석
  ├─ [11] ErrorBudgetGate._evaluate() tier_id 파라미터
  ├─ [12] ErrorBudgetGate._get_error_budget_percent() region 파라미터
  ├─ [12a] gate.py L136: get_current_status() → get_budget_status() 버그 수정 ⚠️
  ├─ [12b] 리전 데이터 Missing 시 Global Fallback 로직 구현
  ├─ [13] ErrorBudgetGate.check() tier_id/region 파라미터 + 캐시 구조 변경
  │       (CACHE_KEY_GLOBAL 상수, _cache_key() 헬퍼)
  └─ [14] gate 단위 테스트

Phase 4: 거버넌스 통합
  ├─ [15] is_error_budget_blocking() tier_id 파라미터
  ├─ [16] check_all_governance() tier_id/region 파라미터
  ├─ [17] PassCriteria.for_tier() 클래스 메서드
  ├─ [17a] apply_tier_floor() 함수 (models.py 정의, service.py 호출)
  └─ [18] 거버넌스/카나리 단위 테스트

Phase 5: 관측성
  ├─ [19] error_budget_remaining_percent 레이블 확장
  ├─ [20] canary_governance_blocked_total 레이블 확장
  ├─ [21] record_error_budget_status() 파라미터 확장
  ├─ [22] alerting_rules.py PromQL 수정
  └─ [23] 메트릭 단위 테스트

Phase 6: 선택적 확장
  ├─ [24] ErrorBudgetService.get_all_region_statuses() 추가
  └─ [25] 통합 테스트
```

---

## 12. 특이 사항 및 주의점

### 12.1 `get_current_status()` 미정의 문제 → ⚠️ Phase 3 [12a]로 편입

**파일**: `services/error_budget_gate/gate.py` L136

```python
status = service.get_current_status()   # ← 존재하지 않는 메서드!
```

`ErrorBudgetService` (service.py)에는 `get_current_status()` 메서드가 **존재하지 않음**. 실제로는 `get_budget_status(slo_name="availability")`이 대응 메서드.

**현재 동작**: try-except(L130)에 감싸져 있어 예외가 먹히고 `None` 반환 → Fail-Open. 사실상 **Error Budget을 한번도 조회하지 않는 상태**.

**수정 방법**: Phase 3 [12a]에서 `service.get_current_status()` → `service.get_budget_status(region=region)` 1줄 교체. Alias 메서드 생성 안 함 (`RecoveryCoordinator.get_current_status()`와 혼동 방지).

> ⚠️ 이 항목은 단순 "주의점"이 아닌 **런타임 버그**이므로 §11 Phase 3 구현 과제 [12a]로 격상됨.

### 12.2 캐시 카디널리티 증가

현재 단일 `GateCheckResult` 캐시 → `tier_id × region` 조합만큼 캐시 엔트리 증가. 3 tier × 4 region = 12 엔트리로 제한적이나, `cache_ttl_seconds=30` (L80)과 함께 메모리 영향 최소.

### 12.3 Prometheus 레이블 카디널리티

`region`(4개) × `tier`(3개) × `slo_name`(1~3개) × `is_synthetic`(2개) = 최대 72개 시계열. Prometheus 권장 한도(10만) 대비 무시 가능.

### 12.4 ClusterIdentity에 tier 필드 없음

`ClusterIdentity`에는 `region`만 있고 `tier`는 없음. 티어 정보는 `ServiceConfig.criticality` → `tier_mapping.py` 변환 경로로 취득. **런타임에 tier는 서비스 config에서, region은 ClusterIdentity에서** 각각 취득하는 이원 구조.

### 12.5 리전 데이터 Missing 시 Fallback 정책

특정 리전의 버짓 데이터가 없을 때(예: 신규 리전, 데이터 수집 지연) 행동 정책:

| 선택지 | Fail-Open 일관성 | 안전성 | 채택 |
|--------|:-:|:-:|:-:|
| Error 발생 (차단) | ❌ Fail-Open 위반 | 높음 | ❌ |
| **Global 값 Fallback** | ⭕ | 중간 | **✅ 채택** |
| Pass(100%) 간주 | ⭕ Fail-Open 최대 | 낮음 | ❌ |

**채택 근거**:
1. **Fail-Open 철학 유지**: `redis_flag.py` L103 (`Fail-Open: 소진되지 않은 것으로 가정`), `gate.py` L39 (`Fail-open 설계`)
2. **완전 무방비(100%) 아닌 최소 보호**: 글로벌 임계치가 방어선 역할
3. **기존 패턴**: `RegionalRecoveryPolicy`의 `"global"` config가 default fallback 역할 (regional_recovery_policy.py L244)

**구현** (§5.5 참조):

```python
status = service.get_budget_status(region=region)
if status is None and region is not None:
    logger.warning(
        f"[ErrorBudgetGate] Region '{region}' data missing, "
        "falling back to global budget"
    )
    status = service.get_budget_status(region=None)
```

### 12.6 `region=None`의 의미 정의

| 컨텍스트 | `region=None` 의미 | 근거 |
|----------|-------------------|------|
| `ErrorBudgetService.get_budget_status()` | 현재 리전 (`ClusterIdentity.region`) | `multiplier.py`, `backfill.py`의 `namespace=None` = "현재 인스턴스" 패턴 |
| `ErrorBudgetGate._get_error_budget_percent()` | 현재 리전 (Service에서 자동 해석) | 동일 |
| `SLO.region` | 전역 적용 (리전 무관) | `SLO.service_name=None` = "전체 서비스" 기존 패턴 |
| `ErrorBudgetStatus.region` | 미분류 (리전 정보 없는 레거시 데이터) | Phase 0 이전 데이터 호환 |
| Redis 키 | 기존 글로벌 키 사용 | `BUDGET_EXHAUSTED_BY_SLO_KEY` (region 세그먼트 없는 레거시 키) |

---

## 부록 A. 전체 코드 근거 인덱스

| 코드 참조 | 파일 경로 | 라인 |
|-----------|----------|------|
| `ErrorBudgetGateSettings` | `packages/selfhealing-python/src/selfhealing/settings/error_budget_gate.py` | L30-L176 |
| `ErrorBudgetGate._evaluate()` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py` | L269-L315 |
| `ErrorBudgetGate._get_error_budget_percent()` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py` | L120-L165 |
| `ErrorBudgetGate.check()` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/gate.py` | L234-L267 |
| `GateCheckResult` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/config.py` | L53-L100 |
| `GateStatus` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/config.py` | L25-L50 |
| `ErrorBudgetStatus` | `packages/selfhealing-python/src/selfhealing/services/error_budget/models.py` | L23-L47 |
| `ErrorBudgetCalculator.calculate_budget_status()` | `packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py` | L63-L173 |
| `ErrorBudgetService.get_budget_status()` | `packages/selfhealing-python/src/selfhealing/services/error_budget/service.py` | L86-L88 |
| `SLO` | `packages/selfhealing-python/src/selfhealing/slo.py` | L66-L97 |
| `SLOConfig` | `packages/selfhealing-python/src/selfhealing/slo.py` | L252 |
| `CRITICALITY_TO_TIER` / `VALID_TIER_IDS` | `packages/selfhealing-python/src/selfhealing/services/throttle/tier_mapping.py` | L27-L42 |
| `ClusterIdentity.region` | `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py` | L60-L65 |
| `RegionalRecoveryConfig` | `packages/selfhealing-python/src/selfhealing/services/coordination/regional_recovery_policy.py` | L46-L128 |
| `RegionalIsolationGate` | `packages/selfhealing-python/src/selfhealing/services/isolation/regional_gate.py` | L75-L440 |
| `RegionalInterlockPolicy` | `packages/selfhealing-python/src/selfhealing/services/canary/regional.py` | L79-L146 |
| `error_budget_remaining_percent` 메트릭 | `packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py` | L181-L185 |
| `canary_governance_blocked_total` 메트릭 | `packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py` | L441-L444 |
| `record_error_budget_status()` | `packages/selfhealing-python/src/selfhealing/services/metrics/recorders.py` | L274-L310 |
| `PassCriteria` | `packages/selfhealing-python/src/selfhealing/services/canary/models.py` | L82-L101 |
| `check_all_governance()` | `packages/selfhealing-python/src/selfhealing/services/governance/checks.py` | L421-L545 |
| `is_error_budget_blocking()` | `packages/selfhealing-python/src/selfhealing/services/governance/checks.py` | L390-L416 |
| alerting rules | `packages/selfhealing-python/src/selfhealing/services/metrics/alerting_rules.py` | L122, L131 |
| `_build_default_regional_configs()` | `packages/selfhealing-python/src/selfhealing/services/coordination/regional_recovery_policy.py` | L186-L260 |
| **Phase 0 추가 근거** | | |
| `FailedOperation.metadata` (JSONField) | `shopping/models/failed_operation.py` | L186-L190 |
| `create_from_failure()` (Django adapter) | `packages/selfhealing-python/src/selfhealing/adapters/django/models.py` | L541-L600 |
| `create_from_failure()` (Shopping) | `shopping/models/failed_operation.py` | L470-L525 |
| `FailedOperationData.metadata` (DTO) | `packages/selfhealing-python/src/selfhealing/interfaces/repositories.py` | L103-L180 |
| `metadata.get("region")` 선례 | `packages/selfhealing-python/src/selfhealing/services/unified_notification/formatters.py` | L191 |
| `_simulation_stats_callback` | `packages/selfhealing-python/src/selfhealing/services/error_budget/service.py` | L47-L62 |
| TypeError polyfill 패턴 | `packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py` | L118-L135 |
| `BUDGET_EXHAUSTED_BY_SLO_KEY` | `packages/selfhealing-python/src/selfhealing/services/error_budget_gate/redis_flag.py` | L18-L20 |
| **리뷰 보완 추가 근거** | | |
| `TierRegistry.resolve_tier()` | `packages/selfhealing-python/src/selfhealing/api/django/tiering/registry.py` | L314-L345 |
| `TierResult` | `packages/selfhealing-python/src/selfhealing/api/django/tiering/models.py` | L20-L27 |
| `ServiceThrottleConfig.__default__` | `packages/selfhealing-python/src/selfhealing/services/throttle/registry.py` | L101 |
| `RecoveryCoordinator.get_current_status()` | `packages/selfhealing-python/src/selfhealing/services/coordination/recovery_coordinator.py` | L1283 |
| `ClusterIdentity.namespace` property | `packages/selfhealing-python/src/selfhealing/core/cluster_identity.py` | L90-L92 |

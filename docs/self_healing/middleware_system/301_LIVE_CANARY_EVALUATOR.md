# 301. Live Canary Evaluator — promote() 시점의 실시간 메트릭 기반 평가

> **Status**: Proposed
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/services/config_shadow/evaluators/live_canary.py` — 신규
> - `packages/selfhealing-python/src/selfhealing/services/canary/service.py` — `promote()` 연동
> - `packages/selfhealing-python/src/selfhealing/settings/config_shadow.py` — Settings 추가
> **References**:
> - [299_CONFIG_SHADOW_EVALUATOR.md](299_CONFIG_SHADOW_EVALUATOR.md) — 시뮬레이션 엔진
> - [300_CANARY_SHADOW_GATE.md](300_CANARY_SHADOW_GATE.md) — start_rollout() Shadow Gate
> - `services/canary/models.py` — PassCriteria, CanaryMetrics
> - `services/config_shadow/evaluators/__init__.py` — ConfigEvaluator Protocol
> - `services/config_shadow/metrics_provider.py` — TimeSeriesMetricsProvider Protocol

---

## 1. 목적

300번 문서의 Shadow Evaluation은 **과거 이벤트 리플레이** 기반의 "사전 안전 검증"이다.
그러나 `promote()` 시점에는 Canary 노드에서 **실제 트래픽이 이미 흐르고 있다**.
이 시점에서 과거 시뮬레이션 결과를 다시 확인하는 것은 무의미하다.

Live Canary Evaluator는 `ConfigEvaluator` Protocol을 구현하되,
**EventJournal 이벤트가 아닌 실시간 메트릭(Prometheus/Datadog)**을 소스로 사용하여
Canary 노드의 실제 동작을 평가한다.

**설계 원칙**:
- Shadow Evaluation(300) = 과거 데이터 → `start_rollout()` 전 검증
- Live Canary Evaluation(301) = 실시간 데이터 → `promote()` 전 검증
- 두 평가자 모두 동일한 `ConfigEvaluator` Protocol을 따른다

---

## 2. 기존 코드 근거

### 2.1 ConfigEvaluator Protocol

`services/config_shadow/evaluators/__init__.py:15-45`:

```python
@runtime_checkable
class ConfigEvaluator(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def event_types(self) -> list[str]: ...

    def evaluate(
        self,
        events: list[JournalEntry],
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult: ...
```

Live Canary Evaluator는 이 Protocol의 `evaluate()` 시그니처를 따르되,
`events` 파라미터를 **Prometheus에서 실시간 조회한 메트릭 이벤트**로 채운다.

### 2.2 TimeSeriesMetricsProvider Protocol

`services/config_shadow/metrics_provider.py:15-53`:

```python
@runtime_checkable
class TimeSeriesMetricsProvider(Protocol):
    def query_error_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]: ...

    def query_request_rate(
        self, service_name: str, start: datetime, end: datetime,
        step_seconds: int = 60,
    ) -> list[tuple[datetime, float]]: ...
```

향후 `PrometheusTimeSeriesProvider`, `DatadogTimeSeriesProvider` 구현이 예고되어 있다
(`metrics_provider.py:20-22`).

### 2.3 PassCriteria — Canary 헬스 기준

`services/canary/models.py:82-166`:

```python
class PassCriteria:
    error_rate_absolute_max: float = 0.05       # 5%
    error_rate_increase_max: float = 0.01       # 1%
    latency_p95_delta_ms: float = 50.0
    latency_p99_delta_pct: float = 0.2          # 20%
    error_budget_drain_rate_max: float = 1.2
    error_budget_remaining_min: float = 0.1     # 10%
    min_requests_required: int = 100
    evaluation_window_seconds: int = 300        # 5분
```

PassCriteria는 "Canary 노드가 건강한가?"를 판단한다.
Live Canary Evaluator는 이 기준을 **재활용**하여 일관성을 유지한다.

### 2.4 promote()의 자동 승격 트리거

`tasks/canary_watchdog.py:624-651`:

- Celery Beat: 매 1분 실행
- 조건: `auto_promote=True` + `elapsed >= stage.duration_minutes` + 메트릭 통과

현재 promote()의 메트릭 검증은 `PassCriteria.evaluate(metrics)`로 수행된다.
Live Canary Evaluator는 이 검증을 **Config Shadow 프레임워크와 통합**하여
일관된 `EvaluatorResult` 형태로 결과를 반환한다.

---

## 3. LiveCanaryEvaluator 구현

### 3.1 클래스 정의

```python
# services/config_shadow/evaluators/live_canary.py

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from selfhealing.services.config_shadow.evaluators import ConfigEvaluator
from selfhealing.services.config_shadow.metrics_provider import TimeSeriesMetricsProvider
from selfhealing.services.config_shadow.models import EvaluatorResult

logger = logging.getLogger(__name__)


class LiveCanaryEvaluator:
    """
    Canary 노드의 실시간 메트릭을 기반으로 설정 변경의 실제 영향을 평가한다.

    ConfigEvaluator Protocol을 구현하되, events 대신
    TimeSeriesMetricsProvider에서 실시간 데이터를 조회한다.
    """

    def __init__(
        self,
        metrics_provider: TimeSeriesMetricsProvider,
        evaluation_window_seconds: int = 300,
        min_requests_required: int = 100,
    ) -> None:
        self._metrics = metrics_provider
        self._window_seconds = evaluation_window_seconds
        self._min_requests = min_requests_required

    @property
    def name(self) -> str:
        return "live_canary"

    @property
    def event_types(self) -> list[str]:
        return ["canary_metrics"]

    def evaluate(
        self,
        events: list,
        baseline_config: dict[str, Any],
        candidate_config: dict[str, Any],
    ) -> EvaluatorResult:
        """
        실시간 메트릭을 조회하여 baseline(기존 클러스터)과
        candidate(Canary 클러스터)의 동작을 비교한다.

        Args:
            events: 사용하지 않음 (Protocol 호환을 위해 유지).
                    대신 metrics_provider에서 실시간 조회.
            baseline_config: 기존 클러스터 식별 정보를 포함한 설정.
                예: {"service_name": "order-service", "cluster": "prod-1"}
            candidate_config: Canary 클러스터 식별 정보를 포함한 설정.
                예: {"service_name": "order-service", "cluster": "canary-1"}
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(seconds=self._window_seconds)
        warnings: list[str] = []

        baseline_service = baseline_config.get("service_name", "")
        candidate_service = candidate_config.get("service_name", baseline_service)
        baseline_cluster = baseline_config.get("cluster", "baseline")
        candidate_cluster = candidate_config.get("cluster", "canary")

        # 1. 실시간 에러율 조회
        baseline_errors = self._metrics.query_error_rate(
            service_name=f"{baseline_service}:{baseline_cluster}",
            start=start, end=now, step_seconds=60,
        )
        candidate_errors = self._metrics.query_error_rate(
            service_name=f"{candidate_service}:{candidate_cluster}",
            start=start, end=now, step_seconds=60,
        )

        # 2. 실시간 요청률 조회
        baseline_rps = self._metrics.query_request_rate(
            service_name=f"{baseline_service}:{baseline_cluster}",
            start=start, end=now, step_seconds=60,
        )
        candidate_rps = self._metrics.query_request_rate(
            service_name=f"{candidate_service}:{candidate_cluster}",
            start=start, end=now, step_seconds=60,
        )

        # 3. 메트릭 집계
        baseline_avg_error = self._avg(baseline_errors)
        candidate_avg_error = self._avg(candidate_errors)
        baseline_total_rps = self._sum(baseline_rps)
        candidate_total_rps = self._sum(candidate_rps)
        error_delta = candidate_avg_error - baseline_avg_error

        # 4. 데이터 충분성 → confidence
        confidence, conf_warnings = self._calculate_confidence(
            candidate_total_rps, candidate_errors,
        )
        warnings.extend(conf_warnings)

        # 5. 통과 판정
        passed = True
        details_parts: list[str] = []

        # 5a. 에러율 절대 임계값 (PassCriteria 기본값 재활용)
        error_rate_max = baseline_config.get("error_rate_absolute_max", 0.05)
        if candidate_avg_error > error_rate_max:
            passed = False
            details_parts.append(
                f"Canary error rate {candidate_avg_error:.3f} > "
                f"threshold {error_rate_max:.3f}"
            )

        # 5b. 에러율 증가 임계값
        error_increase_max = baseline_config.get("error_rate_increase_max", 0.01)
        if error_delta > error_increase_max:
            passed = False
            details_parts.append(
                f"Error rate increase {error_delta:.3f} > "
                f"threshold {error_increase_max:.3f}"
            )

        if passed:
            details_parts.append(
                f"Canary healthy: error_rate={candidate_avg_error:.3f}, "
                f"delta={error_delta:+.3f}, rps={candidate_total_rps:.1f}"
            )

        return EvaluatorResult(
            evaluator_name=self.name,
            passed=passed,
            confidence_score=confidence,
            baseline_metrics={
                "avg_error_rate": baseline_avg_error,
                "total_rps": baseline_total_rps,
            },
            candidate_metrics={
                "avg_error_rate": candidate_avg_error,
                "total_rps": candidate_total_rps,
            },
            delta={
                "error_rate_delta": error_delta,
            },
            details="; ".join(details_parts),
            warnings=warnings,
        )

    def _calculate_confidence(
        self,
        total_rps: float,
        error_series: list[tuple[datetime, float]],
    ) -> tuple[float, list[str]]:
        """요청량과 데이터 포인트 수로 신뢰도를 계산한다."""
        warnings: list[str] = []
        data_points = len(error_series)

        if data_points < 3:
            warnings.append(
                f"Insufficient data points ({data_points}). "
                f"Need at least {self._window_seconds // 60} minutes of data."
            )
            return 0.2, warnings

        if total_rps < self._min_requests:
            warnings.append(
                f"Low request volume ({total_rps:.0f} < {self._min_requests}). "
                f"Confidence reduced."
            )
            return 0.4, warnings

        if total_rps < self._min_requests * 5:
            return 0.7, warnings

        return 0.95, warnings

    @staticmethod
    def _avg(series: list[tuple[datetime, float]]) -> float:
        if not series:
            return 0.0
        return sum(v for _, v in series) / len(series)

    @staticmethod
    def _sum(series: list[tuple[datetime, float]]) -> float:
        return sum(v for _, v in series)
```

### 3.2 설계 결정

**ConfigEvaluator Protocol 재활용 이유**:
- Shadow Evaluation(과거)과 Live Evaluation(실시간)의 **결과 형식이 동일** (`EvaluatorResult`)
- `ShadowEvaluatorService`의 오케스트레이션 로직을 그대로 활용 가능
- 테스트에서 `MockTimeSeriesProvider`를 주입하여 단위 테스트 가능

**events 파라미터를 무시하는 이유**:
Protocol 호환을 위해 시그니처는 유지하되, 실제 데이터는 `TimeSeriesMetricsProvider`에서 조회한다.
이는 Protocol의 `evaluate()` 계약을 위반하지 않으면서도 다른 데이터 소스를 사용하는 **Adapter 패턴**이다.
향후 Protocol을 확장(예: `evaluate_live()` 추가)할 수 있으나,
현 단계에서는 기존 Protocol과의 호환을 우선한다.

---

## 4. promote() 연동

### 4.1 promote()에서의 활용

Live Canary Evaluator는 `promote()` 내부에서 **기존 PassCriteria 검증을 보완**한다.
기존 `PassCriteria.evaluate(metrics)`는 단순 임계값 비교이고,
Live Canary Evaluator는 시계열 추세 분석까지 수행한다.

```python
# services/canary/service.py — promote() 내부에 추가

def _check_live_canary_evaluation(
    self,
    rollout: CanaryRollout,
    current_stage: CanaryStage,
) -> bool | None:
    """
    Canary 노드의 실시간 메트릭을 평가한다.

    Returns:
        True: 통과 (승격 가능)
        False: 차단 (승격 불가)
        None: 비활성화 또는 오류 (체크 생략)
    """
    try:
        from selfhealing.settings.config_shadow import get_config_shadow_settings
        settings = get_config_shadow_settings()
        if not settings.live_evaluation_enabled:
            return None
    except ImportError:
        return None

    try:
        from selfhealing.services.config_shadow.evaluators.live_canary import (
            LiveCanaryEvaluator,
        )
        from selfhealing.services.config_shadow.metrics_provider import (
            get_metrics_provider,
        )

        evaluator = LiveCanaryEvaluator(
            metrics_provider=get_metrics_provider(),
            evaluation_window_seconds=current_stage.pass_criteria.evaluation_window_seconds,
            min_requests_required=current_stage.pass_criteria.min_requests_required,
        )

        result = evaluator.evaluate(
            events=[],
            baseline_config={
                "service_name": rollout.config_type,
                "cluster": self._get_baseline_cluster(rollout),
                "error_rate_absolute_max": current_stage.pass_criteria.error_rate_absolute_max,
                "error_rate_increase_max": current_stage.pass_criteria.error_rate_increase_max,
            },
            candidate_config={
                "service_name": rollout.config_type,
                "cluster": current_stage.clusters[0] if current_stage.clusters else "canary",
            },
        )

        if result.passed:
            logger.info(
                "canary_promote.live_evaluation_passed",
                rollout_id=rollout.rollout_id,
                confidence=result.confidence_score,
            )
            return True

        logger.warning(
            "canary_promote.live_evaluation_failed",
            rollout_id=rollout.rollout_id,
            details=result.details,
            confidence=result.confidence_score,
        )
        return False

    except ImportError:
        return None
    except Exception as e:
        logger.warning("canary_promote.live_evaluation_error", error=e)
        return None  # Fail-Open
```

### 4.2 promote()에서의 삽입 위치

```python
def promote(self, rollout_id, force=False, bypass_governance=False, ...):
    ...
    # 기존: PassCriteria 기반 메트릭 검증
    metrics = self._collect_stage_metrics(rollout, current_stage)
    passed, reason = current_stage.pass_criteria.evaluate(metrics)
    if not passed and not force:
        return False

    # === 새로 추가: Live Canary Evaluation ===
    if not force:
        live_check = self._check_live_canary_evaluation(rollout, current_stage)
        if live_check is not None and not live_check:
            return False

    # 기존: 거버넌스 체크
    if not bypass_governance:
        governance = check_all_governance(...)
    ...
```

**삽입 위치 결정 이유**:
PassCriteria(단순 임계값)와 Live Evaluation(시계열 분석)은 모두 "Canary 노드 건강 상태"를 판단한다.
따라서 거버넌스 체크(시스템 전체 건강) **이전에** 배치하여,
Canary 노드가 불건강하면 시스템 레벨 체크까지 가지 않고 조기 반환한다.

---

## 5. Settings

### 5.1 ConfigShadowSettings 확장

```python
# settings/config_shadow.py에 추가

live_evaluation_enabled: bool = Field(
    default=False,
    description=(
        "promote() 시 Live Canary Evaluation 활성화 여부. "
        "TimeSeriesMetricsProvider 구현체가 등록된 후 True로 전환."
    ),
)
```

**`live_evaluation_enabled=False` 기본값 이유**:
Live Canary Evaluator는 `TimeSeriesMetricsProvider`의 실제 구현체
(PrometheusTimeSeriesProvider 등)가 있어야 동작한다.
Mock만 있는 상태에서 True로 켜면 무의미하므로,
운영 환경에서 Provider를 등록한 후 명시적으로 활성화한다.

---

## 6. Shadow Evaluation(300)과의 역할 분담

| 관점 | Shadow Evaluation (300) | Live Canary Evaluation (301) |
|------|------------------------|------------------------------|
| **시점** | start_rollout() 전 | promote() 전 |
| **데이터 소스** | EventJournal (과거 이벤트 리플레이) | Prometheus/Datadog (실시간 메트릭) |
| **질문** | "이 설정으로 바꾸면 과거에 어땠을까?" | "지금 Canary에서 실제로 어떤가?" |
| **Protocol** | ConfigEvaluator | ConfigEvaluator (동일) |
| **Fail 모드** | Fail-Open | Fail-Open |
| **bypass** | bypass_shadow + reason | force=True (기존 promote 패턴) |

---

## 7. 전체 플로우 (300 + 301 통합)

```
운영자                    Canary Service              Shadow/Live Evaluator
  │                           │                            │
  ├─ create_rollout() ───────▶│                            │
  │                           │                            │
  ├─ evaluate_for_rollout() ──┼───────── Shadow(300) ─────▶│
  │   (과거 이벤트 리플레이)  │                            ├─ EventJournal 조회
  │◀── passed ────────────────┼────────────────────────────│
  │                           │                            │
  ├─ start_rollout() ────────▶│                            │
  │                           ├─ Shadow Gate(300) 체크     │
  │                           ├─ 클러스터 적용             │
  │                           │                            │
  │   [bake time 경과]        │                            │
  │                           │                            │
  ├─ promote() ──────────────▶│                            │
  │   (또는 auto-promote)     ├─ PassCriteria 검증         │
  │                           ├─ Live Evaluation(301) ────▶│
  │                           │   (실시간 메트릭)          ├─ Prometheus 조회
  │                           │◀── passed ─────────────────│
  │                           ├─ Governance 체크           │
  │                           ├─ 다음 단계 적용            │
  │◀── 결과 ──────────────────│                            │
```

---

## 8. 선행 조건

| 조건 | 상태 | 비고 |
|------|------|------|
| ConfigEvaluator Protocol | ✅ 구현 완료 | evaluators/__init__.py:15-45 |
| TimeSeriesMetricsProvider Protocol | ✅ 정의 완료 | metrics_provider.py:15-53 |
| PrometheusTimeSeriesProvider | ❌ 미구현 | 별도 구현 필요 |
| PassCriteria | ✅ 구현 완료 | canary/models.py:82-166 |
| Shadow Gate (300) | ❌ 구현 예정 | 300번 문서 |

**구현 순서**: 300 (Shadow Gate) → Prometheus Provider → 301 (Live Canary Evaluator)

---

## 9. 파일 변경 요약

| 파일 | 변경 유형 | 내용 |
|------|-----------|------|
| `services/config_shadow/evaluators/live_canary.py` | 신규 | LiveCanaryEvaluator 구현 |
| `services/canary/service.py` | 수정 | `promote()`에 `_check_live_canary_evaluation()` 추가 |
| `settings/config_shadow.py` | 수정 | `live_evaluation_enabled` 설정 추가 |

기존 `promote()` 호출부는 `force=True`로 Live Evaluation을 우회할 수 있으므로
**호출부 변경 불필요**.

# 189. Prometheus Metrics - AdaptiveThrottle 모니터링 연동 구현

> **문서 버전**: 1.0.0
> **최종 수정일**: 2026-02-06
> **작성 근거**: `selfhealing/services/metrics/definitions.py`, `selfhealing/services/throttle/adaptive.py`, `selfhealing/metrics/prometheus.py`

## 1. 개요

본 문서는 `AdaptiveThrottle`의 Prometheus 메트릭 통합 및 모니터링 대시보드 구성을 정의합니다.

### 1.1 문제 정의

현재 `AdaptiveThrottle`은 **부분적인 메트릭만 기록**:
- `_record_throttle_metrics()` 헬퍼 함수로 기본 메트릭 기록
- 일부 메트릭 정의는 있으나 통합 대시보드 부재

**문제점**: 운영 가시성 부족, 알람 규칙 미정의

---

## 2. 현재 구현 분석

### 2.1 기존 메트릭 헬퍼 함수

**코드 위치**: [adaptive.py](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py) (Line 101-150)

```python
def _record_throttle_metrics(
    service: str,
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    denied_reason: str | None = None,
    emergency_level: int | None = None,
    cb_state: str | None = None,
) -> None:
    """
    Throttle 관련 Prometheus 메트릭 기록.

    메트릭:
    - selfhealing_throttle_limit: 현재 limit 값
    - selfhealing_throttle_rtt_ms: RTT 히스토그램
    - selfhealing_throttle_gradient: 현재 gradient 값
    - selfhealing_throttle_denied_total: 거부된 요청 카운터
    - selfhealing_throttle_emergency_adjustments_total: Emergency 조정 카운터
    - selfhealing_throttle_cb_adjustments_total: CB 조정 카운터
    """
    try:
        from selfhealing.services.metrics.definitions import (
            throttle_current_limit,
            throttle_rtt_ms as throttle_rtt_histogram,
            throttle_gradient as throttle_gradient_gauge,
            throttle_denied_total,
            throttle_emergency_adjustments_total,
            throttle_cb_adjustments_total,
        )

        if limit is not None:
            throttle_current_limit.labels(service=service).set(limit)

        if rtt_ms is not None:
            throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

        if gradient is not None:
            throttle_gradient_gauge.labels(service=service).set(gradient)

        if denied_reason is not None:
            throttle_denied_total.labels(service=service, reason=denied_reason).inc()

        if emergency_level is not None:
            throttle_emergency_adjustments_total.labels(level=str(emergency_level)).inc()

        if cb_state is not None:
            throttle_cb_adjustments_total.labels(service=service, cb_state=cb_state).inc()

    except ImportError:
        logger.debug("[AdaptiveThrottle] Metrics module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record metrics: {e}")
```

### 2.2 기존 메트릭 정의

**코드 위치**: [definitions.py](../../packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py) (Line 268-310)

```python
# =============================================================================
# Adaptive Throttle Metrics
# =============================================================================

throttle_current_limit = get_or_create_gauge(
    "selfhealing_throttle_limit",
    "Current throttle limit value",
    ["service"],
)

throttle_rtt_ms = get_or_create_histogram(
    "selfhealing_throttle_rtt_ms",
    "Response time (RTT) in milliseconds",
    ["service"],
    buckets=(10, 25, 50, 100, 200, 500, 1000, 2000, 5000),
)

throttle_gradient = get_or_create_gauge(
    "selfhealing_throttle_gradient",
    "Current RTT gradient (positive=slowing, negative=improving)",
    ["service"],
)

throttle_denied_total = get_or_create_counter(
    "selfhealing_throttle_denied_total",
    "Total requests denied by throttle",
    ["service", "reason"],
)

throttle_emergency_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_emergency_adjustments_total",
    "Total throttle limit adjustments due to emergency mode",
    ["level"],
)

throttle_cb_adjustments_total = get_or_create_counter(
    "selfhealing_throttle_cb_adjustments_total",
    "Total throttle limit adjustments due to circuit breaker state",
    ["service", "cb_state"],
)
```

---

## 3. 추가 메트릭 설계

### 3.1 메트릭 분류

| 카테고리 | 메트릭 | 타입 | 설명 |
|----------|--------|------|------|
| **Core** | `throttle_limit` | Gauge | 현재 limit 값 |
| **Core** | `throttle_rtt_ms` | Histogram | RTT 분포 |
| **Core** | `throttle_gradient` | Gauge | RTT 기울기 |
| **Request** | `throttle_requests_total` | Counter | 총 요청 수 |
| **Request** | `throttle_denied_total` | Counter | 거부된 요청 |
| **Request** | `throttle_allowed_total` | Counter | 허용된 요청 |
| **SLA** | `throttle_sla_warnings_total` | Counter | SLA Warning 횟수 |
| **SLA** | `throttle_sla_criticals_total` | Counter | SLA Critical 횟수 |
| **Emergency** | `throttle_emergency_level` | Gauge | 현재 Emergency Level |
| **Emergency** | `throttle_emergency_adjustments_total` | Counter | Emergency 조정 횟수 |
| **Recovery** | `throttle_recovery_dampening_active` | Gauge | Recovery Dampening 활성화 |
| **Recovery** | `throttle_recovery_dampening_step` | Gauge | Recovery 단계 (0-2) |
| **Full Stop** | `throttle_full_stop_active` | Gauge | Full Stop 활성화 |

### 3.2 메트릭 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        Prometheus 메트릭 수집 아키텍처                           │
├─────────────────────────────────────────────────────────────────────────────────┤
│                                                                                 │
│  ┌───────────────────┐                                                          │
│  │ AdaptiveThrottle  │                                                          │
│  │                   │                                                          │
│  │  check()          │──► throttle_requests_total                               │
│  │  record_response()│──► throttle_rtt_ms, throttle_gradient                    │
│  │  adjust_for_      │──► throttle_emergency_level,                             │
│  │    emergency()    │    throttle_emergency_adjustments_total                  │
│  └───────────────────┘                                                          │
│            │                                                                    │
│            ▼                                                                    │
│  ┌───────────────────┐    scrape     ┌───────────────────┐                      │
│  │ Prometheus Client │ ◄──────────── │   Prometheus      │                      │
│  │ (metrics endpoint)│               │   Server          │                      │
│  │ /metrics          │               └─────────┬─────────┘                      │
│  └───────────────────┘                         │                                │
│                                                │ query                          │
│                                                ▼                                │
│                                    ┌───────────────────┐                        │
│                                    │     Grafana       │                        │
│                                    │                   │                        │
│                                    │  ┌─────────────┐  │                        │
│                                    │  │ Throttle    │  │                        │
│                                    │  │ Dashboard   │  │                        │
│                                    │  └─────────────┘  │                        │
│                                    └───────────────────┘                        │
│                                                                                 │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 구현 코드

### 4.1 추가 메트릭 정의

**수정 위치**: `services/metrics/definitions.py`

```python
# =============================================================================
# Adaptive Throttle Extended Metrics
# =============================================================================

# Request Metrics
throttle_requests_total = get_or_create_counter(
    "selfhealing_throttle_requests_total",
    "Total requests processed by throttle",
    ["service", "result"],  # result: allowed, denied
)

throttle_allowed_total = get_or_create_counter(
    "selfhealing_throttle_allowed_total",
    "Total requests allowed by throttle",
    ["service"],
)

# SLA Metrics
throttle_sla_warnings_total = get_or_create_counter(
    "selfhealing_throttle_sla_warnings_total",
    "Total SLA warning threshold breaches",
    ["service"],
)

throttle_sla_criticals_total = get_or_create_counter(
    "selfhealing_throttle_sla_criticals_total",
    "Total SLA critical threshold breaches",
    ["service"],
)

throttle_sla_breach_duration_seconds = get_or_create_histogram(
    "selfhealing_throttle_sla_breach_duration_seconds",
    "Duration of SLA breach periods",
    ["service", "severity"],
    buckets=(60, 300, 600, 1800, 3600),
)

# Emergency Metrics
throttle_emergency_level = get_or_create_gauge(
    "selfhealing_throttle_emergency_level",
    "Current emergency level (0-3)",
    ["service"],
)

throttle_gradient_frozen = get_or_create_gauge(
    "selfhealing_throttle_gradient_frozen",
    "Whether gradient adjustment is frozen (1=yes, 0=no)",
    ["service"],
)

# Recovery Metrics
throttle_recovery_dampening_active = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_active",
    "Whether recovery dampening is active (1=yes, 0=no)",
    ["service"],
)

throttle_recovery_dampening_step = get_or_create_gauge(
    "selfhealing_throttle_recovery_dampening_step",
    "Current recovery dampening step (0=80%, 1=90%, 2=100%)",
    ["service"],
)

throttle_recovery_completed_total = get_or_create_counter(
    "selfhealing_throttle_recovery_completed_total",
    "Total recovery dampening completions",
    ["service"],
)

# Full Stop Metrics
throttle_full_stop_active = get_or_create_gauge(
    "selfhealing_throttle_full_stop_active",
    "Whether full stop is active (1=yes, 0=no)",
    ["service"],
)

throttle_full_stop_activations_total = get_or_create_counter(
    "selfhealing_throttle_full_stop_activations_total",
    "Total full stop activations",
    ["service", "reason"],
)

# Limit Change Metrics
throttle_limit_changes_total = get_or_create_counter(
    "selfhealing_throttle_limit_changes_total",
    "Total throttle limit changes",
    ["service", "direction", "trigger"],  # direction: up, down; trigger: gradient, sla, emergency, cb, 429
)

throttle_limit_change_magnitude = get_or_create_histogram(
    "selfhealing_throttle_limit_change_magnitude",
    "Magnitude of limit changes (percentage)",
    ["service", "direction"],
    buckets=(5, 10, 20, 30, 50, 70, 100),
)
```

### 4.2 확장된 메트릭 기록 함수

**수정 위치**: `adaptive.py`

```python
def _record_throttle_metrics_extended(
    service: str = "default",
    # Core metrics
    limit: int | None = None,
    rtt_ms: float | None = None,
    gradient: float | None = None,
    # Request metrics
    request_result: str | None = None,  # "allowed" | "denied"
    denied_reason: str | None = None,
    # SLA metrics
    sla_event: str | None = None,  # "warning" | "critical"
    # Emergency metrics
    emergency_level: int | None = None,
    gradient_frozen: bool | None = None,
    # Recovery metrics
    recovery_dampening_active: bool | None = None,
    recovery_dampening_step: int | None = None,
    # Full Stop metrics
    full_stop_active: bool | None = None,
    full_stop_reason: str | None = None,
    # Limit change metrics
    limit_change_direction: str | None = None,  # "up" | "down"
    limit_change_trigger: str | None = None,
    limit_change_percent: float | None = None,
) -> None:
    """
    확장된 Throttle Prometheus 메트릭 기록.

    기존 _record_throttle_metrics()를 확장하여 더 세분화된 메트릭 제공.
    """
    try:
        from selfhealing.services.metrics.definitions import (
            # Core
            throttle_current_limit,
            throttle_rtt_ms as throttle_rtt_histogram,
            throttle_gradient as throttle_gradient_gauge,
            # Request
            throttle_requests_total,
            throttle_allowed_total,
            throttle_denied_total,
            # SLA
            throttle_sla_warnings_total,
            throttle_sla_criticals_total,
            # Emergency
            throttle_emergency_level as throttle_emergency_level_gauge,
            throttle_gradient_frozen as throttle_gradient_frozen_gauge,
            throttle_emergency_adjustments_total,
            # Recovery
            throttle_recovery_dampening_active as recovery_active_gauge,
            throttle_recovery_dampening_step as recovery_step_gauge,
            # Full Stop
            throttle_full_stop_active as full_stop_gauge,
            throttle_full_stop_activations_total,
            # Limit Change
            throttle_limit_changes_total,
            throttle_limit_change_magnitude,
        )

        # Core metrics
        if limit is not None:
            throttle_current_limit.labels(service=service).set(limit)

        if rtt_ms is not None:
            throttle_rtt_histogram.labels(service=service).observe(rtt_ms)

        if gradient is not None:
            throttle_gradient_gauge.labels(service=service).set(gradient)

        # Request metrics
        if request_result is not None:
            throttle_requests_total.labels(service=service, result=request_result).inc()
            if request_result == "allowed":
                throttle_allowed_total.labels(service=service).inc()
            elif request_result == "denied":
                throttle_denied_total.labels(service=service, reason=denied_reason or "unknown").inc()

        # SLA metrics
        if sla_event == "warning":
            throttle_sla_warnings_total.labels(service=service).inc()
        elif sla_event == "critical":
            throttle_sla_criticals_total.labels(service=service).inc()

        # Emergency metrics
        if emergency_level is not None:
            throttle_emergency_level_gauge.labels(service=service).set(emergency_level)
            throttle_emergency_adjustments_total.labels(level=str(emergency_level)).inc()

        if gradient_frozen is not None:
            throttle_gradient_frozen_gauge.labels(service=service).set(1 if gradient_frozen else 0)

        # Recovery metrics
        if recovery_dampening_active is not None:
            recovery_active_gauge.labels(service=service).set(1 if recovery_dampening_active else 0)

        if recovery_dampening_step is not None:
            recovery_step_gauge.labels(service=service).set(recovery_dampening_step)

        # Full Stop metrics
        if full_stop_active is not None:
            full_stop_gauge.labels(service=service).set(1 if full_stop_active else 0)

        if full_stop_reason is not None:
            throttle_full_stop_activations_total.labels(service=service, reason=full_stop_reason).inc()

        # Limit change metrics
        if limit_change_direction is not None and limit_change_trigger is not None:
            throttle_limit_changes_total.labels(
                service=service,
                direction=limit_change_direction,
                trigger=limit_change_trigger,
            ).inc()

            if limit_change_percent is not None:
                throttle_limit_change_magnitude.labels(
                    service=service,
                    direction=limit_change_direction,
                ).observe(abs(limit_change_percent))

    except ImportError:
        logger.debug("[AdaptiveThrottle] Extended metrics module not available")
    except Exception as e:
        logger.debug(f"[AdaptiveThrottle] Failed to record extended metrics: {e}")
```

### 4.3 AdaptiveThrottle 메트릭 통합

**수정 위치**: `adaptive.py`

```python
class AdaptiveThrottle(SlidingWindowThrottle):
    """AdaptiveThrottle with comprehensive metrics."""

    def check(self, key: str) -> ThrottleResult:
        """Check if request is allowed with metrics recording."""
        # 기존 로직...
        result = super().check(key)

        # 메트릭 기록
        _record_throttle_metrics_extended(
            service="default",
            request_result="allowed" if result.allowed else "denied",
            denied_reason=result.reason if not result.allowed else None,
        )

        return result

    def record_response(self, rtt_ms: float) -> None:
        """Record response time with metrics."""
        # 기존 로직...
        previous_limit = self._current_limit
        self._gradient_calculator.add_sample(rtt_ms)
        self._maybe_adjust_limit(rtt_ms)

        # 확장 메트릭 기록
        gradient = self._gradient_calculator.get_gradient()
        _record_throttle_metrics_extended(
            service="default",
            limit=self._current_limit,
            rtt_ms=rtt_ms,
            gradient=gradient,
            emergency_level=self._emergency_level,
            gradient_frozen=self._gradient_frozen,
            recovery_dampening_active=self._recovery_dampening_active,
            recovery_dampening_step=self._recovery_dampening_step if self._recovery_dampening_active else None,
            full_stop_active=self._full_stop_active,
        )

        # Limit 변경 메트릭
        if self._current_limit != previous_limit:
            direction = "up" if self._current_limit > previous_limit else "down"
            change_percent = abs(self._current_limit - previous_limit) / previous_limit * 100
            _record_throttle_metrics_extended(
                service="default",
                limit_change_direction=direction,
                limit_change_trigger="gradient",
                limit_change_percent=change_percent,
            )

    def _maybe_adjust_limit(self, rtt_ms: float) -> None:
        """Adjust limit with SLA metrics."""
        # 기존 로직...

        if rtt_ms >= self.config.sla_critical_ms:
            # SLA Critical 메트릭
            _record_throttle_metrics_extended(
                service="default",
                sla_event="critical",
                limit_change_direction="down",
                limit_change_trigger="sla_critical",
                limit_change_percent=30,
            )
            return

        if rtt_ms >= self.config.sla_warning_ms:
            # SLA Warning 메트릭
            _record_throttle_metrics_extended(
                service="default",
                sla_event="warning",
                limit_change_direction="down",
                limit_change_trigger="sla_warning",
            )
```

---

## 5. Alerting Rules

### 5.1 Prometheus Alerting Rules

**파일**: `docker/prometheus/rules/throttle_alerts.yml`

```yaml
groups:
  - name: throttle_alerts
    rules:
      # SLA Critical 알람
      - alert: ThrottleSLACritical
        expr: increase(selfhealing_throttle_sla_criticals_total[5m]) > 0
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "Throttle SLA Critical threshold breached"
          description: "SLA Critical threshold has been breached {{ $value }} times in the last 5 minutes"

      # SLA Warning 알람 (5분간 3회 이상)
      - alert: ThrottleSLAWarningFrequent
        expr: increase(selfhealing_throttle_sla_warnings_total[5m]) >= 3
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "Frequent SLA warnings detected"
          description: "SLA Warning threshold breached {{ $value }} times in 5 minutes"

      # Full Stop 활성화 알람
      - alert: ThrottleFullStopActive
        expr: selfhealing_throttle_full_stop_active == 1
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "Throttle Full Stop is ACTIVE"
          description: "All requests are being blocked due to Full Stop condition"

      # Limit 급격한 감소 알람
      - alert: ThrottleLimitDropped
        expr: |
          (selfhealing_throttle_limit - selfhealing_throttle_limit offset 5m)
          / selfhealing_throttle_limit offset 5m < -0.5
        for: 1m
        labels:
          severity: warning
        annotations:
          summary: "Throttle limit dropped by more than 50%"
          description: "Current limit: {{ $value }}"

      # 높은 거부율 알람
      - alert: ThrottleHighDenialRate
        expr: |
          rate(selfhealing_throttle_denied_total[5m])
          / (rate(selfhealing_throttle_requests_total[5m]) + 0.001) > 0.3
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High throttle denial rate (>30%)"
          description: "{{ $value | humanizePercentage }} of requests are being denied"

      # Emergency Level 3 알람
      - alert: ThrottleEmergencyLevel3
        expr: selfhealing_throttle_emergency_level >= 3
        for: 0m
        labels:
          severity: critical
        annotations:
          summary: "Throttle Emergency Level 3 active"
          description: "Maximum emergency level is active, gradient adjustments are frozen"

      # RTT 지속적 상승 알람
      - alert: ThrottleRTTIncreasing
        expr: selfhealing_throttle_gradient > 0.2
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "RTT gradient consistently positive"
          description: "Response times have been increasing for 5+ minutes (gradient: {{ $value }})"

      # Recovery Dampening 장기화 알람
      - alert: ThrottleRecoveryStuck
        expr: selfhealing_throttle_recovery_dampening_active == 1
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "Recovery dampening taking too long"
          description: "Recovery dampening has been active for more than 10 minutes"
```

---

## 6. Grafana Dashboard

### 6.1 Dashboard JSON

```json
{
  "title": "Adaptive Throttle Monitoring",
  "uid": "adaptive-throttle",
  "panels": [
    {
      "title": "Current Throttle Limit",
      "type": "stat",
      "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_limit{service=\"default\"}",
          "legendFormat": "Limit"
        }
      ]
    },
    {
      "title": "Emergency Level",
      "type": "stat",
      "gridPos": {"h": 4, "w": 3, "x": 6, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_emergency_level{service=\"default\"}",
          "legendFormat": "Level"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "thresholds": {
            "steps": [
              {"color": "green", "value": 0},
              {"color": "yellow", "value": 1},
              {"color": "orange", "value": 2},
              {"color": "red", "value": 3}
            ]
          }
        }
      }
    },
    {
      "title": "Full Stop Status",
      "type": "stat",
      "gridPos": {"h": 4, "w": 3, "x": 9, "y": 0},
      "targets": [
        {
          "expr": "selfhealing_throttle_full_stop_active{service=\"default\"}",
          "legendFormat": "Full Stop"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "mappings": [
            {"type": "value", "options": {"0": {"text": "OFF", "color": "green"}}},
            {"type": "value", "options": {"1": {"text": "ACTIVE", "color": "red"}}}
          ]
        }
      }
    },
    {
      "title": "Throttle Limit Over Time",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 0, "y": 4},
      "targets": [
        {
          "expr": "selfhealing_throttle_limit{service=\"default\"}",
          "legendFormat": "Current Limit"
        }
      ]
    },
    {
      "title": "RTT Distribution (p50, p90, p99)",
      "type": "timeseries",
      "gridPos": {"h": 8, "w": 12, "x": 12, "y": 4},
      "targets": [
        {
          "expr": "histogram_quantile(0.5, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p50"
        },
        {
          "expr": "histogram_quantile(0.9, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p90"
        },
        {
          "expr": "histogram_quantile(0.99, rate(selfhealing_throttle_rtt_ms_bucket[5m]))",
          "legendFormat": "p99"
        }
      ]
    },
    {
      "title": "RTT Gradient",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 0, "y": 12},
      "targets": [
        {
          "expr": "selfhealing_throttle_gradient{service=\"default\"}",
          "legendFormat": "Gradient"
        }
      ],
      "fieldConfig": {
        "defaults": {
          "custom": {
            "thresholdsStyle": {"mode": "line"}
          },
          "thresholds": {
            "steps": [
              {"color": "green", "value": -0.1},
              {"color": "yellow", "value": 0},
              {"color": "red", "value": 0.1}
            ]
          }
        }
      }
    },
    {
      "title": "Request Rate (Allowed vs Denied)",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 12, "y": 12},
      "targets": [
        {
          "expr": "rate(selfhealing_throttle_requests_total{result=\"allowed\"}[5m])",
          "legendFormat": "Allowed"
        },
        {
          "expr": "rate(selfhealing_throttle_requests_total{result=\"denied\"}[5m])",
          "legendFormat": "Denied"
        }
      ]
    },
    {
      "title": "SLA Events",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 0, "y": 18},
      "targets": [
        {
          "expr": "increase(selfhealing_throttle_sla_warnings_total[5m])",
          "legendFormat": "Warnings"
        },
        {
          "expr": "increase(selfhealing_throttle_sla_criticals_total[5m])",
          "legendFormat": "Criticals"
        }
      ]
    },
    {
      "title": "Limit Changes by Trigger",
      "type": "timeseries",
      "gridPos": {"h": 6, "w": 12, "x": 12, "y": 18},
      "targets": [
        {
          "expr": "increase(selfhealing_throttle_limit_changes_total[5m])",
          "legendFormat": "{{direction}} - {{trigger}}"
        }
      ]
    }
  ]
}
```

---

## 7. 테스트

### 7.1 메트릭 기록 테스트

```python
class TestThrottleMetrics:
    """Throttle Prometheus 메트릭 테스트."""

    def test_check_records_request_metrics(self):
        """check() 호출 시 요청 메트릭 기록 확인."""
        throttle = AdaptiveThrottle()

        # 요청 실행
        result = throttle.check("test_key")

        # 메트릭 검증 (prometheus_client 사용)
        from prometheus_client import REGISTRY

        requests_total = REGISTRY.get_sample_value(
            "selfhealing_throttle_requests_total",
            {"service": "default", "result": "allowed"}
        )
        assert requests_total >= 1

    def test_sla_critical_records_metrics(self):
        """SLA Critical 시 메트릭 기록 확인."""
        throttle = AdaptiveThrottle(config=ThrottleConfig(
            sla_critical_ms=100,
        ))

        # SLA Critical 트리거
        throttle.record_response(150)  # > 100ms

        from prometheus_client import REGISTRY

        criticals_total = REGISTRY.get_sample_value(
            "selfhealing_throttle_sla_criticals_total",
            {"service": "default"}
        )
        assert criticals_total >= 1
```

---

## 8. 참조

- [메트릭 정의 소스](../../packages/selfhealing-python/src/selfhealing/services/metrics/definitions.py)
- [AdaptiveThrottle 소스](../../packages/selfhealing-python/src/selfhealing/services/throttle/adaptive.py)
- [Prometheus 메트릭 소스](../../packages/selfhealing-python/src/selfhealing/metrics/prometheus.py)
- [156_OTEL_OBSERVABILITY_OVERVIEW.md](156_OTEL_OBSERVABILITY_OVERVIEW.md)
- [159_GRAFANA_STACK_INTEGRATION.md](159_GRAFANA_STACK_INTEGRATION.md)

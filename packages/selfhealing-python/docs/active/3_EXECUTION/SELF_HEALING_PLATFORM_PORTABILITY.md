# Platform Portability Guide

> Migrate Self-Healing observability to any monitoring platform with minimal code changes.

---

## Overview

The current Self-Healing system uses **Prometheus + Grafana** for metrics and alerting.
However, the abstraction layer design allows easy migration to other platforms.

---

## Current Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │  record_dlq_item_created()                               │   │
│  │  record_retry_attempt()                                  │   │
│  │  record_circuit_breaker_state_change()                   │   │
│  │  record_recovery_time()                                  │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │            Metrics Abstraction Layer                     │   │
│  │  (shopping/services/self_healing/metrics.py)             │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              ↓                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │           prometheus_client library                      │   │
│  │  Counter, Gauge, Histogram                               │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                               ↓
                    Prometheus Server → Grafana
```

---

## Platform Compatibility Matrix

| Platform | Portability | Migration Effort | Notes |
|----------|-------------|------------------|-------|
| **Datadog** | 🟢 Easy | ~2 hours | `ddtrace` + StatsD adapter |
| **New Relic** | 🟢 Easy | ~2 hours | OpenTelemetry exporter |
| **AWS CloudWatch** | 🟢 Easy | ~3 hours | CloudWatch client wrapper |
| **Elastic APM** | 🟢 Easy | ~2 hours | OpenTelemetry exporter |
| **Splunk** | 🟡 Medium | ~4 hours | HEC or OpenTelemetry |
| **OpenTelemetry** | 🟢 Native | ~1 hour | Industry standard, recommended |
| **Azure Monitor** | 🟢 Easy | ~3 hours | OpenTelemetry exporter |
| **Google Cloud Monitoring** | 🟢 Easy | ~2 hours | OpenTelemetry exporter |

---

## Migration Strategy

### Option 1: Direct Backend Replacement

Replace `prometheus_client` calls directly with target platform SDK.

```python
# Current (Prometheus)
from prometheus_client import Counter
dlq_items_total = Counter("dlq_items_total", ...)
dlq_items_total.labels(domain="payment").inc()

# Datadog
from datadog import statsd
statsd.increment("dlq_items_total", tags=["domain:payment"])

# AWS CloudWatch
import boto3
cloudwatch = boto3.client('cloudwatch')
cloudwatch.put_metric_data(
    Namespace='SelfHealing',
    MetricData=[{
        'MetricName': 'dlq_items_total',
        'Dimensions': [{'Name': 'domain', 'Value': 'payment'}],
        'Value': 1,
        'Unit': 'Count'
    }]
)
```

### Option 2: OpenTelemetry Abstraction (Recommended)

Use OpenTelemetry as the universal abstraction layer.

```python
# metrics_adapter.py
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)

# Configure exporter based on environment
METRICS_BACKEND = os.getenv("METRICS_BACKEND", "prometheus")

if METRICS_BACKEND == "datadog":
    from opentelemetry.exporter.datadog import DatadogMetricExporter
    exporter = DatadogMetricExporter()
elif METRICS_BACKEND == "cloudwatch":
    from opentelemetry.exporter.cloudwatch import CloudWatchMetricExporter
    exporter = CloudWatchMetricExporter()
else:
    from opentelemetry.exporter.prometheus import PrometheusMetricReader
    exporter = PrometheusMetricReader()

# Set up meter provider
reader = PeriodicExportingMetricReader(exporter)
provider = MeterProvider(metric_readers=[reader])
metrics.set_meter_provider(provider)

# Create meter
meter = metrics.get_meter("self_healing")

# Create instruments
dlq_items_counter = meter.create_counter(
    "dlq_items_total",
    description="Total DLQ items created"
)
```

---

## Backend-Specific Migration Guides

### Datadog Migration

1. Install dependencies:
```bash
pip install ddtrace datadog
```

2. Replace metrics module:
```python
# shopping/services/self_healing/metrics_datadog.py
from datadog import initialize, statsd

initialize(statsd_host="localhost", statsd_port=8125)

def record_dlq_item_created(domain: str, failure_type: str) -> None:
    statsd.increment(
        "dlq.items.total",
        tags=[f"domain:{domain}", f"failure_type:{failure_type}"]
    )

def record_circuit_breaker_state_change(
    service: str, from_state: str, to_state: str
) -> None:
    statsd.increment(
        "circuit_breaker.transitions",
        tags=[
            f"service:{service}",
            f"from_state:{from_state}",
            f"to_state:{to_state}"
        ]
    )
    # Also set gauge for current state
    state_value = {"closed": 0, "open": 1, "half_open": 2}.get(to_state, 0)
    statsd.gauge(f"circuit_breaker.state.{service}", state_value)
```

3. Configure Datadog agent with appropriate tags

### AWS CloudWatch Migration

1. Install dependencies:
```bash
pip install boto3
```

2. Replace metrics module:
```python
# shopping/services/self_healing/metrics_cloudwatch.py
import boto3
from functools import lru_cache

@lru_cache()
def get_cloudwatch_client():
    return boto3.client('cloudwatch', region_name='ap-northeast-2')

NAMESPACE = "SelfHealing"

def record_dlq_item_created(domain: str, failure_type: str) -> None:
    client = get_cloudwatch_client()
    client.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{
            'MetricName': 'DLQItemsCreated',
            'Dimensions': [
                {'Name': 'Domain', 'Value': domain},
                {'Name': 'FailureType', 'Value': failure_type}
            ],
            'Value': 1,
            'Unit': 'Count'
        }]
    )

def record_circuit_breaker_state_change(
    service: str, from_state: str, to_state: str
) -> None:
    client = get_cloudwatch_client()
    state_value = {"closed": 0, "open": 1, "half_open": 2}.get(to_state, 0)
    client.put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{
            'MetricName': 'CircuitBreakerState',
            'Dimensions': [{'Name': 'Service', 'Value': service}],
            'Value': state_value,
            'Unit': 'None'
        }]
    )
```

### New Relic Migration

1. Install dependencies:
```bash
pip install newrelic opentelemetry-exporter-newrelic
```

2. Use OpenTelemetry exporter:
```python
from opentelemetry.exporter.newrelic import NewRelicMetricExporter

exporter = NewRelicMetricExporter(
    api_key=os.getenv("NEW_RELIC_API_KEY"),
    service_name="self-healing"
)
```

---

## Abstraction Layer Implementation

For maximum portability, implement a backend-agnostic interface:

```python
# shopping/services/self_healing/metrics_base.py
from abc import ABC, abstractmethod
from typing import Protocol
import os

class MetricsBackend(Protocol):
    """Protocol for metrics backend implementations"""
    
    def counter_inc(self, name: str, labels: dict, value: int = 1) -> None: ...
    def gauge_set(self, name: str, labels: dict, value: float) -> None: ...
    def histogram_observe(self, name: str, labels: dict, value: float) -> None: ...


class PrometheusBackend:
    """Prometheus implementation"""
    
    def __init__(self):
        from prometheus_client import Counter, Gauge, Histogram
        self._counters = {}
        self._gauges = {}
        self._histograms = {}
    
    def counter_inc(self, name: str, labels: dict, value: int = 1) -> None:
        # Implementation
        pass


class DatadogBackend:
    """Datadog implementation"""
    
    def __init__(self):
        from datadog import statsd
        self._statsd = statsd
    
    def counter_inc(self, name: str, labels: dict, value: int = 1) -> None:
        tags = [f"{k}:{v}" for k, v in labels.items()]
        self._statsd.increment(name, value=value, tags=tags)


class CloudWatchBackend:
    """AWS CloudWatch implementation"""
    
    def __init__(self, namespace: str = "SelfHealing"):
        import boto3
        self._client = boto3.client('cloudwatch')
        self._namespace = namespace
    
    def counter_inc(self, name: str, labels: dict, value: int = 1) -> None:
        dimensions = [{'Name': k, 'Value': v} for k, v in labels.items()]
        self._client.put_metric_data(
            Namespace=self._namespace,
            MetricData=[{
                'MetricName': name,
                'Dimensions': dimensions,
                'Value': value,
                'Unit': 'Count'
            }]
        )


def get_metrics_backend() -> MetricsBackend:
    """Factory function to get configured metrics backend"""
    backend = os.getenv("METRICS_BACKEND", "prometheus")
    
    if backend == "datadog":
        return DatadogBackend()
    elif backend == "cloudwatch":
        return CloudWatchBackend()
    elif backend == "opentelemetry":
        return OpenTelemetryBackend()
    else:
        return PrometheusBackend()


# Global backend instance
_backend = None

def get_backend() -> MetricsBackend:
    global _backend
    if _backend is None:
        _backend = get_metrics_backend()
    return _backend
```

---

## Alert Rule Migration

### From Prometheus to Datadog

```yaml
# Prometheus Alert Rule
- alert: DLQPendingHigh
  expr: dlq_pending_count > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: DLQ pending count is high
```

```json
// Datadog Monitor (Terraform)
resource "datadog_monitor" "dlq_pending_high" {
  name    = "DLQ Pending High"
  type    = "metric alert"
  query   = "avg(last_5m):avg:dlq.pending.count{*} > 10"
  message = "DLQ pending count is high"
  tags    = ["severity:warning", "team:ops"]
}
```

### From Prometheus to CloudWatch

```yaml
# CloudWatch Alarm (CloudFormation)
DLQPendingHighAlarm:
  Type: AWS::CloudWatch::Alarm
  Properties:
    AlarmName: DLQPendingHigh
    MetricName: DLQPendingCount
    Namespace: SelfHealing
    Statistic: Average
    Period: 300
    EvaluationPeriods: 1
    Threshold: 10
    ComparisonOperator: GreaterThanThreshold
    AlarmActions:
      - !Ref SNSTopicArn
```

---

## Dashboard Migration

### Grafana to Datadog

| Grafana Panel | Datadog Equivalent |
|---------------|-------------------|
| Time Series | Timeseries widget |
| Stat | Query Value widget |
| Gauge | Gauge widget |
| Table | Table widget |
| Heatmap | Heatmap widget |

### Key Queries Translation

| Metric | Prometheus (Grafana) | Datadog |
|--------|---------------------|---------|
| DLQ Pending | `dlq_pending_count{domain="payment"}` | `avg:dlq.pending.count{domain:payment}` |
| CB State | `circuit_breaker_state{service="toss"}` | `avg:circuit_breaker.state{service:toss}` |
| Retry Rate | `rate(retry_outcomes_total[5m])` | `sum:retry.outcomes.total{*}.as_rate()` |

---

## Testing Migration

1. **Parallel Running**: Run both old and new backends simultaneously during migration
2. **Metric Comparison**: Verify metric values match between platforms
3. **Alert Verification**: Trigger test alerts on both platforms
4. **Dashboard Validation**: Compare visualizations for accuracy

---

## Environment Variables

```bash
# Select metrics backend
METRICS_BACKEND=prometheus  # Options: prometheus, datadog, cloudwatch, opentelemetry

# Backend-specific configuration
# Datadog
DATADOG_API_KEY=your_api_key
DATADOG_HOST=localhost
DATADOG_PORT=8125

# AWS CloudWatch
AWS_REGION=ap-northeast-2
CLOUDWATCH_NAMESPACE=SelfHealing

# New Relic
NEW_RELIC_API_KEY=your_api_key
NEW_RELIC_SERVICE_NAME=self-healing
```

---

## References

- [OpenTelemetry Python](https://opentelemetry.io/docs/instrumentation/python/)
- [Datadog Python Client](https://docs.datadoghq.com/developers/dogstatsd/?tab=python)
- [AWS CloudWatch Metrics](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/publishingMetrics.html)
- [Prometheus Python Client](https://github.com/prometheus/client_python)

---

*Created: 2025-12-09*
*Author: Self-Healing Infrastructure Team*

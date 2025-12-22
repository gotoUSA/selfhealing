# Self-Healing 메트릭 수집 전략 - Advanced

> **문서 분할 안내**: 메트릭 수집 문서가 크기 때문에 2개로 분할되었습니다.
> - [14_METRIC_COLLECTION_CORE.md](14_METRIC_COLLECTION_CORE.md) - 수집 전략 및 구현
> - [15_METRIC_COLLECTION_ADVANCED.md](15_METRIC_COLLECTION_ADVANCED.md) - Audit, Drift 감지, 설정 (현재 문서)

---

## Audit 통합

### Gauge Sync 시점 Audit 로깅

Gauge 동기화는 "값이 바뀌는 시점"이므로 추적 대상입니다.

```python
# selfhealing/metrics/reconciler.py

from datetime import datetime, timezone
from selfhealing.services.audit import AuditService


class MetricReconciler:
    def sync_all_gauges(self, actor: str = "system") -> dict:
        # ... 동기화 로직 ...

        # Audit 로깅
        AuditService.log_action(
            action="gauge_sync",
            actor=actor,
            details={
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "metrics_synced": result,
                "trigger": "manual" if actor != "system" else "startup",
            },
            category="metric_reconciliation",
        )

        return result
```

### Audit 로그 예시

```json
{
  "timestamp": "2024-01-20T10:30:00.123456Z",
  "action": "gauge_sync",
  "actor": "admin",
  "category": "metric_reconciliation",
  "details": {
    "synced_at": "2024-01-20T10:30:00.123456Z",
    "metrics_synced": {
      "dlq_pending": {"payment": 5, "point": 2, "inventory": 0},
      "circuit_breaker_states": {"toss_payment": "closed"},
      "retry_success_rates": {"payment": 98.5, "point": 99.2}
    },
    "trigger": "manual"
  }
}
```

---

## Drift 감지 및 알림

### Drift란?

**Drift**는 인메모리 메트릭 값과 실제 DB 값 사이의 불일치입니다.

| 원인 | 설명 | 영향도 |
|------|------|--------|
| 서버 재시작 | Gauge 값 초기화 | 높음 |
| 메트릭 이벤트 누락 | 코드 버그, 예외 발생 | 중간 |
| 동시성 이슈 | 레이스 컨디션 | 낮음 |
| 메모리 누수 | 장기 운영 시 축적 | 낮음 |

### Drift 탐지 전략

```
┌─────────────────────────────────────────────────────────────┐
│                    Drift 탐지 흐름                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐           ┌─────────────┐                 │
│  │  인메모리    │   비교    │   DB 실제   │                 │
│  │  Gauge=10   │◀────────▶│   값=15     │                 │
│  └─────────────┘           └─────────────┘                 │
│         │                         │                        │
│         │         Drift=5         │                        │
│         └────────────────────────┘                         │
│                    │                                        │
│                    ▼                                        │
│         ┌─────────────────────┐                            │
│         │ Drift > Threshold?  │                            │
│         └─────────────────────┘                            │
│              │          │                                   │
│           Yes│          │No                                 │
│              ▼          ▼                                   │
│         ┌────────┐  ┌────────┐                             │
│         │ Alert  │  │ Log    │                             │
│         │& Sync  │  │ Only   │                             │
│         └────────┘  └────────┘                             │
└─────────────────────────────────────────────────────────────┘
```

### Drift Detector 구현

```python
# selfhealing/metrics/drift_detector.py

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from selfhealing.adapters.metrics.base import MetricSourceAdapter
from selfhealing.config import get_settings

logger = logging.getLogger(__name__)


@dataclass
class DriftResult:
    """Drift 감지 결과"""
    domain: str
    metric_name: str
    in_memory_value: float
    actual_value: float
    drift: float
    drift_percentage: float
    is_critical: bool
    detected_at: datetime


class DriftDetector:
    """
    메트릭 Drift 감지기.

    인메모리 Gauge 값과 실제 DB 값을 비교하여
    임계값 초과 시 알림을 발생시킵니다.
    """

    def __init__(
        self,
        adapter: MetricSourceAdapter,
        threshold_absolute: int = 10,
        threshold_percentage: float = 20.0,
    ):
        """
        Args:
            adapter: 메트릭 소스 어댑터
            threshold_absolute: 절대값 임계치 (기본: 10개 차이)
            threshold_percentage: 백분율 임계치 (기본: 20% 차이)
        """
        self.adapter = adapter
        self.threshold_absolute = threshold_absolute
        self.threshold_percentage = threshold_percentage

    def detect_dlq_drift(self, domain: str) -> Optional[DriftResult]:
        """DLQ pending 메트릭의 Drift 감지"""
        from selfhealing.metrics.prometheus import dlq_pending_count

        # 인메모리 값 조회 (Prometheus Gauge에서)
        try:
            # Prometheus client의 내부 값 접근
            in_memory = dlq_pending_count.labels(domain=domain)._value.get()
        except Exception:
            in_memory = 0.0

        # 실제 DB 값 조회
        actual = float(self.adapter.get_dlq_pending_count(domain))

        # Drift 계산
        drift = abs(in_memory - actual)
        drift_pct = (drift / actual * 100) if actual > 0 else (100 if drift > 0 else 0)

        # 임계값 판단
        is_critical = (
            drift >= self.threshold_absolute or
            drift_pct >= self.threshold_percentage
        )

        if drift > 0:
            result = DriftResult(
                domain=domain,
                metric_name="dlq_pending_count",
                in_memory_value=in_memory,
                actual_value=actual,
                drift=drift,
                drift_percentage=drift_pct,
                is_critical=is_critical,
                detected_at=datetime.now(timezone.utc),
            )

            if is_critical:
                self._alert_critical_drift(result)
            else:
                logger.info(f"Minor drift detected: {result}")

            return result

        return None

    def detect_all_drifts(self) -> list[DriftResult]:
        """모든 도메인의 Drift 감지"""
        from selfhealing.core.domains import DOMAINS

        results = []
        for domain in DOMAINS:
            drift = self.detect_dlq_drift(domain)
            if drift:
                results.append(drift)

        return results

    def _alert_critical_drift(self, drift: DriftResult) -> None:
        """심각한 Drift 알림 발송"""
        logger.warning(
            f"CRITICAL DRIFT DETECTED: {drift.metric_name} "
            f"for {drift.domain} - "
            f"in_memory={drift.in_memory_value}, "
            f"actual={drift.actual_value}, "
            f"drift={drift.drift} ({drift.drift_percentage:.1f}%)"
        )

        # Slack/PagerDuty 알림 (옵션)
        settings = get_settings()
        if settings.alert_on_drift:
            from selfhealing.notifications import notify_drift
            notify_drift(drift)
```

### Drift 자동 보정 정책

```python
# selfhealing/metrics/drift_policy.py

from enum import Enum


class DriftCorrectionPolicy(Enum):
    """Drift 보정 정책"""

    ALERT_ONLY = "alert_only"           # 알림만 (기본값)
    AUTO_CORRECT = "auto_correct"       # 자동 보정
    MANUAL_REVIEW = "manual_review"     # 수동 검토 후 보정


class DriftPolicyHandler:
    """Drift 정책 핸들러"""

    def __init__(self, policy: DriftCorrectionPolicy = DriftCorrectionPolicy.ALERT_ONLY):
        self.policy = policy

    def handle_drift(self, drift_result: DriftResult, reconciler) -> dict:
        """Drift 발생 시 정책에 따른 처리"""

        if self.policy == DriftCorrectionPolicy.ALERT_ONLY:
            return {
                "action": "alert_sent",
                "auto_corrected": False,
                "drift": drift_result.drift,
            }

        elif self.policy == DriftCorrectionPolicy.AUTO_CORRECT:
            # 자동 보정
            reconciler.sync_domain_gauges(drift_result.domain)
            return {
                "action": "auto_corrected",
                "auto_corrected": True,
                "corrected_value": drift_result.actual_value,
            }

        elif self.policy == DriftCorrectionPolicy.MANUAL_REVIEW:
            # 수동 검토 대기열에 추가
            from selfhealing.services.review_queue import add_to_review
            ticket = add_to_review(
                type="metric_drift",
                details=drift_result.__dict__,
            )
            return {
                "action": "pending_review",
                "ticket_id": ticket.id,
            }
```

---

## Drift Threshold 동적 조정 API

운영 중 Drift 임계값을 동적으로 조정할 수 있는 API입니다.

```python
# selfhealing/api/views/drift_config.py

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser

from selfhealing.services.runtime_config import RuntimeConfig


class DriftThresholdView(APIView):
    """
    Drift 임계값 동적 조정 API.

    GET  /api/self-healing/config/drift-threshold/ - 현재 설정 조회
    POST /api/self-healing/config/drift-threshold/ - 설정 변경
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        config = RuntimeConfig()
        return Response({
            "absolute_threshold": config.get("drift_threshold_absolute", default=10),
            "percentage_threshold": config.get("drift_threshold_percentage", default=20.0),
            "correction_policy": config.get("drift_correction_policy", default="alert_only"),
        })

    def post(self, request):
        config = RuntimeConfig()

        # 변경 전 값 기록 (Audit용)
        old_values = {
            "absolute": config.get("drift_threshold_absolute"),
            "percentage": config.get("drift_threshold_percentage"),
            "policy": config.get("drift_correction_policy"),
        }

        # 값 업데이트
        if "absolute_threshold" in request.data:
            config.set("drift_threshold_absolute", request.data["absolute_threshold"])
        if "percentage_threshold" in request.data:
            config.set("drift_threshold_percentage", request.data["percentage_threshold"])
        if "correction_policy" in request.data:
            config.set("drift_correction_policy", request.data["correction_policy"])

        # Audit 로깅
        new_values = {
            "absolute": config.get("drift_threshold_absolute"),
            "percentage": config.get("drift_threshold_percentage"),
            "policy": config.get("drift_correction_policy"),
        }

        from selfhealing.services.audit import AuditService
        AuditService.log_action(
            action="drift_threshold_changed",
            actor=request.user.username,
            details={
                "old_values": old_values,
                "new_values": new_values,
            },
            category="config_change",
        )

        return Response({
            "status": "updated",
            "new_values": new_values,
        })
```

### API 사용 예시

```bash
# 현재 설정 조회
curl -X GET https://api.example.com/api/self-healing/config/drift-threshold/ \
  -H "Authorization: Bearer $TOKEN"

# 응답
{
  "absolute_threshold": 10,
  "percentage_threshold": 20.0,
  "correction_policy": "alert_only"
}

# 임계값 변경 (더 엄격하게)
curl -X POST https://api.example.com/api/self-healing/config/drift-threshold/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "absolute_threshold": 5,
    "percentage_threshold": 10.0,
    "correction_policy": "auto_correct"
  }'
```

---

## 메트릭 신뢰도 레벨

### 신뢰도 정의

메트릭 값의 신뢰 수준을 명시적으로 표현합니다.

| 레벨 | 설명 | 예시 |
|------|------|------|
| **EXACT** | 100% 정확 | Counter, Histogram |
| **NEAR_EXACT** | 99%+ 정확 | 동기화 직후 Gauge |
| **APPROXIMATE** | 90%+ 정확 | 일반 운영 중 Gauge |
| **STALE** | 오래된 값 | 동기화 실패 시 |

```python
# selfhealing/metrics/reliability.py

from enum import Enum
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta


class MetricReliability(Enum):
    EXACT = "exact"              # Counter, Histogram
    NEAR_EXACT = "near_exact"    # 동기화 직후 Gauge
    APPROXIMATE = "approximate"  # 일반 운영 중 Gauge
    STALE = "stale"              # 마지막 동기화 1시간 초과


@dataclass
class MetricWithReliability:
    """신뢰도 정보가 포함된 메트릭 값"""
    name: str
    value: float
    reliability: MetricReliability
    last_sync: datetime | None


class ReliabilityTracker:
    """메트릭 신뢰도 추적기"""

    SYNC_FRESHNESS_THRESHOLD = timedelta(hours=1)

    def get_reliability(self, metric_name: str, last_sync: datetime | None) -> MetricReliability:
        # Counter, Histogram은 항상 EXACT
        if self._is_counter_or_histogram(metric_name):
            return MetricReliability.EXACT

        if last_sync is None:
            return MetricReliability.APPROXIMATE

        age = datetime.now(timezone.utc) - last_sync

        if age < timedelta(minutes=5):
            return MetricReliability.NEAR_EXACT
        elif age < self.SYNC_FRESHNESS_THRESHOLD:
            return MetricReliability.APPROXIMATE
        else:
            return MetricReliability.STALE

    def _is_counter_or_histogram(self, name: str) -> bool:
        counter_suffixes = ["_total", "_count", "_created"]
        histogram_suffixes = ["_bucket", "_sum"]

        return any(name.endswith(s) for s in counter_suffixes + histogram_suffixes)
```

### 신뢰도 노출 API

```python
# selfhealing/api/views/metrics.py

class MetricReliabilityView(APIView):
    """
    메트릭 신뢰도 조회 API.

    GET /api/self-healing/metrics/reliability/
    """

    def get(self, request):
        from selfhealing.metrics.reliability import ReliabilityTracker
        from selfhealing.metrics.reconciler import MetricReconciler

        tracker = ReliabilityTracker()
        reconciler = MetricReconciler(get_adapter())

        # 주요 Gauge 메트릭의 신뢰도 조회
        gauges = [
            "dlq_pending_count",
            "circuit_breaker_state",
            "retry_success_rate",
        ]

        result = {}
        for gauge_name in gauges:
            result[gauge_name] = {
                "reliability": tracker.get_reliability(
                    gauge_name,
                    reconciler.last_sync_time
                ).value,
                "last_sync": reconciler.last_sync_time.isoformat()
                    if reconciler.last_sync_time else None,
            }

        return Response(result)
```

---

## Timezone 무결성 (Timezone-Aware)

### 시간 처리 원칙

| 원칙 | 설명 | 예시 |
|------|------|------|
| **항상 UTC 저장** | DB, 로그, 메트릭 모두 UTC | `2024-01-20T10:30:00Z` |
| **Timezone-aware datetime 사용** | naive datetime 금지 | `datetime.now(timezone.utc)` |
| **표시 시 변환** | UI/API 응답에서 로컬 타임존으로 변환 | `Asia/Seoul` |

```python
# 올바른 예시
from datetime import datetime, timezone

# ✅ 권장
now = datetime.now(timezone.utc)
synced_at = datetime.now(timezone.utc)

# ❌ 금지 (Python 3.12 deprecated)
now = datetime.utcnow()  # naive datetime
```

### Django Settings

```python
# settings.py

USE_TZ = True  # 필수!
TIME_ZONE = 'UTC'  # 서버 기본 타임존

# 표시용 타임존 (선택)
DISPLAY_TIME_ZONE = 'Asia/Seoul'
```

---

## 설정 (Configuration)

### 환경 변수

| 환경 변수 | 설명 | 기본값 |
|-----------|------|--------|
| `SELFHEALING_METRIC_SYNC_ON_STARTUP` | 서버 시작 시 동기화 여부 | `true` |
| `SELFHEALING_METRIC_SYNC_JITTER_MAX` | Jitter 최대값 (초) | `60` |
| `SELFHEALING_DRIFT_THRESHOLD_ABSOLUTE` | Drift 절대값 임계치 | `10` |
| `SELFHEALING_DRIFT_THRESHOLD_PERCENTAGE` | Drift 백분율 임계치 | `20.0` |
| `SELFHEALING_DRIFT_CORRECTION_POLICY` | Drift 보정 정책 | `alert_only` |

### Python Settings

```python
# selfhealing/config.py

from pydantic_settings import BaseSettings
from typing import Literal


class MetricCollectionSettings(BaseSettings):
    """메트릭 수집 설정"""

    # 동기화 설정
    sync_on_startup: bool = True
    sync_jitter_max_seconds: float = 60.0

    # Drift 감지 설정
    drift_threshold_absolute: int = 10
    drift_threshold_percentage: float = 20.0
    drift_correction_policy: Literal[
        "alert_only", "auto_correct", "manual_review"
    ] = "alert_only"

    # 알림 설정
    alert_on_drift: bool = True

    class Config:
        env_prefix = "SELFHEALING_"
```

---

## ADR (Architecture Decision Records)

### ADR-001: Push 위주 Hybrid 방식 채택

**상태**: 승인됨

**맥락**:
- Self-Healing 시스템은 사용자 DB에 직접 의존하면 안 됨
- 메트릭은 관측용이므로 100% 정확도보다 가용성이 중요

**결정**:
- Counter/Histogram은 Push Only
- Gauge는 Push + Lazy Sync

**결과**:
- DB 부하 제로 (정상 운영 시)
- 서버 재시작 시에만 동기화 쿼리 발생

### ADR-002: Adapter 패턴으로 DB 결합도 제거

**상태**: 승인됨

**맥락**:
- 다양한 사용자 환경 지원 필요 (Django, SQLAlchemy, Raw SQL 등)
- 캐시 사용 여부도 사용자 선택

**결정**:
- `MetricSourceAdapter` 인터페이스 정의
- 사용자가 자신의 환경에 맞게 구현

**결과**:
- 프레임워크 중립성 확보
- 테스트 용이성 향상

### ADR-003: Jitter로 Thundering Herd 방지

**상태**: 승인됨

**맥락**:
- K8s 배포 시 수십~수백 개 Pod 동시 시작
- 모든 Pod가 동시에 DB 쿼리 시 서비스 마비

**결정**:
- 서버 시작 시 0~60초 랜덤 지연 적용

**결과**:
- DB 부하 분산
- 설정 가능한 Jitter 범위 제공

### ADR-004: Drift 감지 + 선택적 자동 보정

**상태**: 승인됨

**맥락**:
- 장기 운영 시 미세 Drift 축적 가능
- 자동 보정은 편하지만 부작용 우려

**결정**:
- Drift 감지 기능 필수 구현
- 보정 정책은 설정 가능 (기본: 알림만)

**결과**:
- 운영자가 위험 수준에 따라 정책 선택 가능

### ADR-005: Gauge 음수 방지를 위한 SafeGauge 래퍼 도입

**상태**: 승인됨

**맥락**:
- 서버 재시작 후 Gauge 값이 0으로 초기화됨
- 이 상태에서 `dec()` 호출 시 음수 발생
- Prometheus는 음수 Gauge를 허용하지만 비즈니스 의미상 오류

**결정**:
- `SafeGauge` 래퍼 클래스 도입
- 음수 방지 + 동기화 헬퍼 제공

**결과**:
- 음수 Gauge 완전 방지
- 동기화 로직 단순화

### ADR-006: Timezone-Aware 시간 처리 필수

**상태**: 승인됨

**맥락**:
- 글로벌 서비스에서 시간대 혼동 방지 필요
- Python 3.12에서 `datetime.utcnow()` deprecated

**결정**:
- 모든 시간 처리는 `datetime.now(timezone.utc)` 사용
- naive datetime 사용 금지

**결과**:
- 시간 무결성 보장
- Python 3.12+ 호환성 확보

---

## 관련 문서

- [14_METRIC_COLLECTION_CORE.md](14_METRIC_COLLECTION_CORE.md) - 수집 전략 및 구현
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 정의 및 Prometheus 설정
- [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 전체 설정 개요
- [07_CONTROL_API.md](07_CONTROL_API.md) - API 보안 및 Audit

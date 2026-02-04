# 177. Self-Healing Meta-Watchdog 구현 가이드

> **버전**: 1.0.0
> **작성일**: 2026-02-04
> **의존성**: [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md)
> **예상 소요**: 5-7일
> **예상 코드량**: ~1,500줄

---

## 0. 문서 목적

이 문서는 **"치료사가 아플 때"** 문제를 해결하기 위한 Self-Healing Meta-Watchdog 구현 가이드입니다.

**핵심 질문**: "Self-Healing 시스템 자체가 장애 나면 누가 고치나?"

**핵심 가치**: "Self-Healing 시스템 자체의 건강 상태를 모니터링하고, 장애 시 자동 복구 또는 인간 에스컬레이션"

---

## 1. 현재 상황 분석 (코드 근거)

### 1.1 기존 Watchdog - Audit 시스템 전용

**파일**: `packages/selfhealing-python/src/selfhealing/audit/audit_watchdog.py`
**라인**: 1-50

```python
class AuditWatchdog:
    """
    Audit Watchdog - Dead Man's Switch Pattern.

    감사 시스템 생존 확인:
    - 주기적으로 heartbeat 전송
    - 외부 모니터링 시스템이 heartbeat 감시
    - heartbeat 누락 시 알림
    """
```

**한계점**: Audit 시스템 heartbeat만 담당, Self-Healing 전체 시스템(Circuit Breaker, DLQ, Recovery Pipeline 등) 모니터링 없음

### 1.2 Canary Watchdog - 배포 전용

**파일**: `packages/selfhealing-python/src/selfhealing/tasks/canary_watchdog.py`

```python
class RolloutWatchdog:
    """Canary 배포 롤아웃 모니터링."""
```

**한계점**: Canary 배포 전용, Self-Healing 시스템 전반 모니터링 아님

### 1.3 누락된 기능

| 기능 | 현재 상태 | 필요 |
|------|----------|------|
| Circuit Breaker 상태 모니터링 | ❌ | CB가 stuck 되었는지 확인 |
| Recovery Pipeline 상태 | ❌ | 복구 작업이 멈췄는지 확인 |
| DLQ Consumer 상태 | ❌ | Consumer가 살아있는지 확인 |
| Self-Healing CB | ❌ | Self-Healing 과부하 시 자기 보호 |
| 인간 에스컬레이션 | ❌ | 자동 복구 실패 시 PagerDuty 알림 |

---

## 2. 구현 목표

### 2.1 목표 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Meta-Watchdog Layer                          │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                 SelfHealerWatchdog                        │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐   │  │
│  │  │ HealthProbe │  │ StuckDetect │  │ CircuitBreaker  │   │  │
│  │  │   Manager   │  │     or      │  │  for Watchdog   │   │  │
│  │  └──────┬──────┘  └──────┬──────┘  └────────┬────────┘   │  │
│  │         │                │                   │            │  │
│  │         ▼                ▼                   ▼            │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │              Escalation Manager                      │  │  │
│  │  │   PagerDuty  │  Slack  │  Email  │  Webhook         │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                  Self-Healing System                       │  │
│  │    Circuit Breaker  │  DLQ  │  Recovery  │  Replay        │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 핵심 컴포넌트

| 컴포넌트 | 역할 |
|----------|------|
| `HealthProbeManager` | 각 서브시스템 건강 상태 수집 |
| `StuckDetector` | Pipeline/Queue stuck 감지 |
| `SelfHealingCircuitBreaker` | Self-Healing 과부하 시 자기 보호 |
| `EscalationManager` | 인간 개입 요청 (PagerDuty, Slack) |
| `SelfHealerWatchdog` | 통합 Watchdog |

---

## 3. 상세 구현 명세

### 3.1 파일 구조

```
packages/selfhealing-python/src/selfhealing/meta/
├── __init__.py
├── config.py              # 설정
├── health_probe.py        # 건강 상태 프로브
├── stuck_detector.py      # Stuck 감지
├── self_circuit_breaker.py # Self-Healing용 CB
├── escalation.py          # 에스컬레이션 관리
└── watchdog.py            # SelfHealerWatchdog (메인)
```

### 3.2 설정 (config.py)

```python
# packages/selfhealing-python/src/selfhealing/meta/config.py
"""
Meta-Watchdog 설정.

기존 코드 참조:
- audit/audit_watchdog.py: WatchdogConfig 패턴
- settings/*.py: Pydantic Settings 패턴
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class MetaWatchdogSettings(BaseSettings):
    """
    Meta-Watchdog 설정.

    환경변수:
    - SELFHEALING_META_*
    """

    # 활성화
    enabled: bool = Field(
        default=True,
        description="Meta-Watchdog 활성화",
    )

    # Health Probe 설정
    probe_interval_seconds: float = Field(
        default=30.0,
        description="헬스 프로브 주기 (초)",
    )
    probe_timeout_seconds: float = Field(
        default=10.0,
        description="헬스 프로브 타임아웃 (초)",
    )

    # Stuck Detection 설정
    stuck_threshold_seconds: float = Field(
        default=300.0,
        description="Stuck 감지 임계치 (초, 기본 5분)",
    )
    dlq_stuck_threshold_entries: int = Field(
        default=1000,
        description="DLQ Stuck 감지 임계치 (엔트리 수)",
    )

    # Self-Healing Circuit Breaker 설정
    self_cb_enabled: bool = Field(
        default=True,
        description="Self-Healing용 CB 활성화",
    )
    self_cb_failure_threshold: int = Field(
        default=5,
        description="CB Open 실패 횟수",
    )
    self_cb_recovery_timeout_seconds: float = Field(
        default=60.0,
        description="CB Half-Open 전환 대기 시간",
    )

    # Escalation 설정
    escalation_enabled: bool = Field(
        default=True,
        description="에스컬레이션 활성화",
    )
    escalation_delay_seconds: float = Field(
        default=180.0,
        description="에스컬레이션 지연 시간 (자동 복구 대기)",
    )
    escalation_cooldown_seconds: float = Field(
        default=3600.0,
        description="에스컬레이션 쿨다운 (1시간)",
    )

    # PagerDuty 설정
    pagerduty_routing_key: str | None = Field(
        default=None,
        description="PagerDuty Routing Key",
    )
    pagerduty_severity: Literal["critical", "error", "warning", "info"] = Field(
        default="critical",
        description="PagerDuty 심각도",
    )

    # Slack 설정
    slack_webhook_url: str | None = Field(
        default=None,
        description="Slack Webhook URL",
    )

    class Config:
        env_prefix = "SELFHEALING_META_"
        env_file = ".env"


@lru_cache(maxsize=1)
def get_meta_watchdog_settings() -> MetaWatchdogSettings:
    """설정 싱글톤 반환."""
    return MetaWatchdogSettings()


def reset_meta_watchdog_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_meta_watchdog_settings.cache_clear()
```

### 3.3 Health Probe Manager (health_probe.py)

```python
# packages/selfhealing-python/src/selfhealing/meta/health_probe.py
"""
Health Probe Manager - 서브시스템 건강 상태 수집.

기존 코드 참조:
- adapters/health_checker.py: Redis Health Check 패턴
- core/connection_health.py: 연결 상태 확인 패턴
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """건강 상태."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ProbeResult:
    """프로브 결과."""

    component: str
    """컴포넌트 이름."""

    status: HealthStatus
    """건강 상태."""

    latency_ms: float
    """응답 시간 (ms)."""

    timestamp: datetime
    """프로브 시각."""

    details: dict[str, Any] = field(default_factory=dict)
    """상세 정보."""

    error: str | None = None
    """에러 메시지."""


class HealthProbe(ABC):
    """건강 프로브 인터페이스."""

    @property
    @abstractmethod
    def component_name(self) -> str:
        """컴포넌트 이름."""
        pass

    @abstractmethod
    def probe(self) -> ProbeResult:
        """건강 상태 프로브."""
        pass


class CircuitBreakerProbe(HealthProbe):
    """
    Circuit Breaker 건강 프로브.

    확인 항목:
    - CB 상태 (CLOSED/OPEN/HALF_OPEN)
    - 최근 실패율
    - Stuck 여부 (OPEN 상태에서 오래 머무름)
    """

    @property
    def component_name(self) -> str:
        return "circuit_breaker"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            from selfhealing.services.circuit_breaker.state_manager import (
                get_circuit_breaker_state_manager,
            )

            manager = get_circuit_breaker_state_manager()

            # 모든 CB 상태 조회
            all_states = {}
            open_count = 0
            stuck_count = 0

            # 기본 상태 확인
            status = HealthStatus.HEALTHY

            # OPEN 상태 CB가 많으면 DEGRADED
            if open_count > 3:
                status = HealthStatus.DEGRADED

            # Stuck CB가 있으면 UNHEALTHY
            if stuck_count > 0:
                status = HealthStatus.UNHEALTHY

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "open_count": open_count,
                    "stuck_count": stuck_count,
                    "states": all_states,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class DLQProbe(HealthProbe):
    """
    DLQ 건강 프로브.

    확인 항목:
    - DLQ 큐 크기
    - 처리 속도 (entries/sec)
    - Consumer 생존 여부
    """

    @property
    def component_name(self) -> str:
        return "dlq"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            from selfhealing.factory import ProviderRegistry

            if not ProviderRegistry.has_runtime_adapter():
                return ProbeResult(
                    component=self.component_name,
                    status=HealthStatus.UNKNOWN,
                    latency_ms=(time.time() - start) * 1000,
                    timestamp=datetime.now(timezone.utc),
                    error="No runtime adapter",
                )

            runtime = ProviderRegistry.get_runtime()

            # DLQ 통계 조회
            pending_count = runtime.count_pending()

            status = HealthStatus.HEALTHY

            # 대기 중인 항목이 많으면 DEGRADED
            settings = get_meta_watchdog_settings()
            if pending_count > settings.dlq_stuck_threshold_entries:
                status = HealthStatus.DEGRADED

            # 처리율 0이고 대기 항목 많으면 UNHEALTHY
            if pending_count > settings.dlq_stuck_threshold_entries * 2:
                status = HealthStatus.UNHEALTHY

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "pending_count": pending_count,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class RecoveryPipelineProbe(HealthProbe):
    """
    Recovery Pipeline 건강 프로브.

    확인 항목:
    - 활성 복구 작업 수
    - Stuck 복구 작업 (너무 오래 걸림)
    - 실패율
    """

    @property
    def component_name(self) -> str:
        return "recovery_pipeline"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            # Recovery Pipeline 상태 확인
            # TODO: RecoveryCoordinator 연동

            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.HEALTHY,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "active_recoveries": 0,
                    "stuck_recoveries": 0,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNKNOWN,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class RedisProbe(HealthProbe):
    """
    Redis 건강 프로브.

    확인 항목:
    - 연결 상태
    - 응답 시간
    - 메모리 사용량
    """

    @property
    def component_name(self) -> str:
        return "redis"

    def probe(self) -> ProbeResult:
        start = time.time()
        try:
            from selfhealing.adapters.cache import get_redis_client

            redis = get_redis_client()

            # PING 테스트
            redis.ping()

            # INFO 조회
            info = redis.info(section="memory")
            used_memory = info.get("used_memory", 0)

            status = HealthStatus.HEALTHY

            # 메모리 사용량 80% 초과 시 DEGRADED
            max_memory = info.get("maxmemory", 0)
            if max_memory > 0 and used_memory / max_memory > 0.8:
                status = HealthStatus.DEGRADED

            return ProbeResult(
                component=self.component_name,
                status=status,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                details={
                    "used_memory_bytes": used_memory,
                    "memory_usage_ratio": used_memory / max_memory if max_memory > 0 else 0,
                },
            )
        except Exception as e:
            return ProbeResult(
                component=self.component_name,
                status=HealthStatus.UNHEALTHY,
                latency_ms=(time.time() - start) * 1000,
                timestamp=datetime.now(timezone.utc),
                error=str(e),
            )


class HealthProbeManager:
    """
    Health Probe Manager.

    여러 프로브를 관리하고 주기적으로 실행.
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
        probes: list[HealthProbe] | None = None,
    ):
        """초기화."""
        self._settings = settings or get_meta_watchdog_settings()
        self._probes = probes or self._create_default_probes()
        self._lock = threading.RLock()
        self._last_results: dict[str, ProbeResult] = {}
        self._running = False
        self._worker: threading.Thread | None = None

    def _create_default_probes(self) -> list[HealthProbe]:
        """기본 프로브 생성."""
        return [
            CircuitBreakerProbe(),
            DLQProbe(),
            RecoveryPipelineProbe(),
            RedisProbe(),
        ]

    def add_probe(self, probe: HealthProbe) -> None:
        """프로브 추가."""
        with self._lock:
            self._probes.append(probe)

    def probe_all(self) -> dict[str, ProbeResult]:
        """모든 프로브 실행."""
        results = {}
        for probe in self._probes:
            try:
                result = probe.probe()
                results[probe.component_name] = result
            except Exception as e:
                logger.error(f"[HealthProbe] {probe.component_name} error: {e}")
                results[probe.component_name] = ProbeResult(
                    component=probe.component_name,
                    status=HealthStatus.UNKNOWN,
                    latency_ms=0,
                    timestamp=datetime.now(timezone.utc),
                    error=str(e),
                )

        with self._lock:
            self._last_results = results

        return results

    def get_overall_status(self) -> HealthStatus:
        """전체 건강 상태 반환."""
        with self._lock:
            results = self._last_results

        if not results:
            return HealthStatus.UNKNOWN

        statuses = [r.status for r in results.values()]

        if HealthStatus.UNHEALTHY in statuses:
            return HealthStatus.UNHEALTHY
        if HealthStatus.DEGRADED in statuses:
            return HealthStatus.DEGRADED
        if HealthStatus.UNKNOWN in statuses:
            return HealthStatus.DEGRADED

        return HealthStatus.HEALTHY

    def get_last_results(self) -> dict[str, ProbeResult]:
        """마지막 프로브 결과 반환."""
        with self._lock:
            return dict(self._last_results)

    def _run_loop(self) -> None:
        """프로브 루프."""
        while self._running:
            try:
                self.probe_all()
            except Exception as e:
                logger.error(f"[HealthProbe] Loop error: {e}")

            time.sleep(self._settings.probe_interval_seconds)

    def start(self) -> None:
        """백그라운드 프로브 시작."""
        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="HealthProbeManager",
            daemon=True,
        )
        self._worker.start()
        logger.info("[HealthProbe] Started")

    def stop(self) -> None:
        """프로브 중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=5.0)
        logger.info("[HealthProbe] Stopped")
```

### 3.4 Escalation Manager (escalation.py)

```python
# packages/selfhealing-python/src/selfhealing/meta/escalation.py
"""
Escalation Manager - 인간 개입 요청.

기존 코드 참조:
- adapters/alert/alert_adapter.py: 알림 패턴
- audit/audit_watchdog.py: Heartbeat 전송 패턴
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings

logger = logging.getLogger(__name__)


class EscalationLevel(Enum):
    """에스컬레이션 레벨."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class EscalationEvent:
    """에스컬레이션 이벤트."""

    level: EscalationLevel
    title: str
    description: str
    component: str
    details: dict[str, Any]
    timestamp: datetime


class EscalationManager:
    """
    Escalation Manager.

    자동 복구 실패 시 인간에게 에스컬레이션.

    지원 채널:
    - PagerDuty (CRITICAL)
    - Slack (WARNING, ERROR)
    - Webhook (커스텀)
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
    ):
        """초기화."""
        self._settings = settings or get_meta_watchdog_settings()
        self._lock = threading.RLock()
        self._last_escalation: dict[str, float] = {}

    def _can_escalate(self, component: str) -> bool:
        """쿨다운 확인."""
        with self._lock:
            last_time = self._last_escalation.get(component, 0)
            return time.time() - last_time > self._settings.escalation_cooldown_seconds

    def _record_escalation(self, component: str) -> None:
        """에스컬레이션 기록."""
        with self._lock:
            self._last_escalation[component] = time.time()

    def escalate(self, event: EscalationEvent) -> bool:
        """
        에스컬레이션 실행.

        Args:
            event: 에스컬레이션 이벤트

        Returns:
            성공 여부
        """
        if not self._settings.escalation_enabled:
            logger.debug("[Escalation] Disabled, skipping")
            return False

        if not self._can_escalate(event.component):
            logger.debug(f"[Escalation] Cooldown for {event.component}")
            return False

        success = False

        # CRITICAL → PagerDuty
        if event.level == EscalationLevel.CRITICAL:
            success = self._send_pagerduty(event) or success

        # WARNING 이상 → Slack
        if event.level in (EscalationLevel.WARNING, EscalationLevel.ERROR, EscalationLevel.CRITICAL):
            success = self._send_slack(event) or success

        if success:
            self._record_escalation(event.component)

        return success

    def _send_pagerduty(self, event: EscalationEvent) -> bool:
        """PagerDuty 알림."""
        if not self._settings.pagerduty_routing_key:
            logger.debug("[Escalation] PagerDuty not configured")
            return False

        try:
            payload = {
                "routing_key": self._settings.pagerduty_routing_key,
                "event_action": "trigger",
                "dedup_key": f"selfhealing-{event.component}-{event.title}",
                "payload": {
                    "summary": f"[Self-Healing] {event.title}",
                    "severity": self._settings.pagerduty_severity,
                    "source": "selfhealing-meta-watchdog",
                    "component": event.component,
                    "custom_details": {
                        "description": event.description,
                        **event.details,
                    },
                },
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                "https://events.pagerduty.com/v2/enqueue",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 202:
                    logger.info(f"[Escalation] PagerDuty sent: {event.title}")
                    return True

            return False
        except Exception as e:
            logger.error(f"[Escalation] PagerDuty error: {e}")
            return False

    def _send_slack(self, event: EscalationEvent) -> bool:
        """Slack 알림."""
        if not self._settings.slack_webhook_url:
            logger.debug("[Escalation] Slack not configured")
            return False

        try:
            emoji = {
                EscalationLevel.INFO: "ℹ️",
                EscalationLevel.WARNING: "⚠️",
                EscalationLevel.ERROR: "❌",
                EscalationLevel.CRITICAL: "🚨",
            }.get(event.level, "❓")

            payload = {
                "text": f"{emoji} *[Self-Healing]* {event.title}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"{emoji} *[Self-Healing]* {event.title}\n\n{event.description}",
                        },
                    },
                    {
                        "type": "context",
                        "elements": [
                            {
                                "type": "mrkdwn",
                                "text": f"*Component:* {event.component} | *Level:* {event.level.value} | *Time:* {event.timestamp.isoformat()}",
                            },
                        ],
                    },
                ],
            }

            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self._settings.slack_webhook_url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )

            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if resp.status == 200:
                    logger.info(f"[Escalation] Slack sent: {event.title}")
                    return True

            return False
        except Exception as e:
            logger.error(f"[Escalation] Slack error: {e}")
            return False
```

### 3.5 SelfHealerWatchdog (watchdog.py)

```python
# packages/selfhealing-python/src/selfhealing/meta/watchdog.py
"""
SelfHealerWatchdog - Self-Healing 시스템 자체 모니터링.

"치료사가 아플 때" 문제 해결.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from selfhealing.meta.config import MetaWatchdogSettings, get_meta_watchdog_settings
from selfhealing.meta.escalation import (
    EscalationEvent,
    EscalationLevel,
    EscalationManager,
)
from selfhealing.meta.health_probe import (
    HealthProbeManager,
    HealthStatus,
    ProbeResult,
)

logger = logging.getLogger(__name__)


@dataclass
class WatchdogState:
    """Watchdog 상태."""

    overall_status: HealthStatus
    component_statuses: dict[str, HealthStatus]
    last_check: datetime
    escalation_pending: bool
    escalation_count: int


class SelfHealerWatchdog:
    """
    Self-Healing 시스템 자체 모니터링 Watchdog.

    기능:
    - 모든 서브시스템 건강 상태 모니터링
    - Stuck 감지 및 자동 복구 시도
    - 자동 복구 실패 시 인간 에스컬레이션
    - Self-Healing용 Circuit Breaker (자기 보호)

    Usage:
        watchdog = SelfHealerWatchdog()
        watchdog.start()

        # 상태 확인
        state = watchdog.get_state()
        print(f"Overall: {state.overall_status}")

        # 종료
        watchdog.stop()
    """

    def __init__(
        self,
        settings: MetaWatchdogSettings | None = None,
        probe_manager: HealthProbeManager | None = None,
        escalation_manager: EscalationManager | None = None,
    ):
        """초기화."""
        self._settings = settings or get_meta_watchdog_settings()
        self._probe_manager = probe_manager or HealthProbeManager(settings=self._settings)
        self._escalation_manager = escalation_manager or EscalationManager(settings=self._settings)

        self._lock = threading.RLock()
        self._running = False
        self._worker: threading.Thread | None = None

        # 상태
        self._last_check: datetime | None = None
        self._consecutive_failures: dict[str, int] = {}
        self._escalation_count = 0

        # Self-Healing Circuit Breaker 상태
        self._self_cb_open = False
        self._self_cb_open_time: float = 0

    def _should_skip_due_to_self_cb(self) -> bool:
        """Self-Healing CB 상태 확인."""
        if not self._settings.self_cb_enabled:
            return False

        if not self._self_cb_open:
            return False

        # Half-Open 전환 확인
        elapsed = time.time() - self._self_cb_open_time
        if elapsed > self._settings.self_cb_recovery_timeout_seconds:
            logger.info("[SelfHealerWatchdog] Self CB → Half-Open")
            self._self_cb_open = False
            return False

        return True

    def _open_self_cb(self) -> None:
        """Self-Healing CB 열기."""
        if self._settings.self_cb_enabled and not self._self_cb_open:
            logger.warning("[SelfHealerWatchdog] Self CB → OPEN (overload protection)")
            self._self_cb_open = True
            self._self_cb_open_time = time.time()

    def check_health(self) -> WatchdogState:
        """
        건강 상태 확인 및 필요 시 조치.

        Returns:
            현재 Watchdog 상태
        """
        # Self CB 확인
        if self._should_skip_due_to_self_cb():
            logger.debug("[SelfHealerWatchdog] Skipping due to Self CB")
            return WatchdogState(
                overall_status=HealthStatus.UNKNOWN,
                component_statuses={},
                last_check=self._last_check or datetime.now(timezone.utc),
                escalation_pending=False,
                escalation_count=self._escalation_count,
            )

        # 프로브 실행
        results = self._probe_manager.probe_all()
        overall_status = self._probe_manager.get_overall_status()
        self._last_check = datetime.now(timezone.utc)

        # 컴포넌트별 상태 확인
        component_statuses = {name: r.status for name, r in results.items()}
        escalation_pending = False

        for name, result in results.items():
            if result.status == HealthStatus.UNHEALTHY:
                # 연속 실패 카운트
                self._consecutive_failures[name] = self._consecutive_failures.get(name, 0) + 1

                # 임계치 초과 시 자동 복구 시도
                if self._consecutive_failures[name] >= self._settings.self_cb_failure_threshold:
                    logger.warning(f"[SelfHealerWatchdog] {name} unhealthy, attempting recovery")

                    # 자동 복구 시도
                    recovered = self._attempt_recovery(name, result)

                    if not recovered:
                        # 에스컬레이션
                        escalation_pending = True
                        self._escalate(name, result)
            else:
                # 정상화되면 카운터 리셋
                self._consecutive_failures[name] = 0

        # 과부하 감지 (모든 컴포넌트가 문제)
        unhealthy_count = sum(1 for s in component_statuses.values() if s == HealthStatus.UNHEALTHY)
        if unhealthy_count >= len(component_statuses) - 1:
            self._open_self_cb()

        return WatchdogState(
            overall_status=overall_status,
            component_statuses=component_statuses,
            last_check=self._last_check,
            escalation_pending=escalation_pending,
            escalation_count=self._escalation_count,
        )

    def _attempt_recovery(self, component: str, result: ProbeResult) -> bool:
        """
        자동 복구 시도.

        Args:
            component: 컴포넌트 이름
            result: 프로브 결과

        Returns:
            복구 성공 여부
        """
        try:
            if component == "circuit_breaker":
                # Stuck CB 강제 리셋
                return self._recover_circuit_breaker(result)
            elif component == "dlq":
                # DLQ Consumer 재시작
                return self._recover_dlq(result)
            elif component == "redis":
                # Redis 연결 재시도
                return self._recover_redis(result)
            else:
                logger.debug(f"[SelfHealerWatchdog] No recovery action for {component}")
                return False
        except Exception as e:
            logger.error(f"[SelfHealerWatchdog] Recovery failed for {component}: {e}")
            return False

    def _recover_circuit_breaker(self, result: ProbeResult) -> bool:
        """Circuit Breaker 복구."""
        try:
            from selfhealing.services.circuit_breaker.state_manager import (
                get_circuit_breaker_state_manager,
            )

            manager = get_circuit_breaker_state_manager()

            # Stuck CB 강제 HALF_OPEN 전환
            stuck_count = result.details.get("stuck_count", 0)
            if stuck_count > 0:
                logger.info("[SelfHealerWatchdog] Forcing stuck CBs to HALF_OPEN")
                # TODO: 실제 구현

            return True
        except Exception as e:
            logger.error(f"[SelfHealerWatchdog] CB recovery error: {e}")
            return False

    def _recover_dlq(self, result: ProbeResult) -> bool:
        """DLQ 복구."""
        # DLQ Consumer 상태 확인 및 재시작
        logger.info("[SelfHealerWatchdog] Attempting DLQ recovery")
        # TODO: Celery Worker 재시작 트리거
        return False

    def _recover_redis(self, result: ProbeResult) -> bool:
        """Redis 연결 복구."""
        try:
            from selfhealing.adapters.cache import reset_redis_client

            # 연결 풀 리셋
            reset_redis_client()
            logger.info("[SelfHealerWatchdog] Redis connection reset")
            return True
        except Exception as e:
            logger.error(f"[SelfHealerWatchdog] Redis recovery error: {e}")
            return False

    def _escalate(self, component: str, result: ProbeResult) -> None:
        """인간에게 에스컬레이션."""
        event = EscalationEvent(
            level=EscalationLevel.CRITICAL,
            title=f"Self-Healing {component} Failure",
            description=(
                f"Component '{component}' is unhealthy and automatic recovery failed.\n"
                f"Error: {result.error or 'Unknown'}\n"
                f"Manual intervention required."
            ),
            component=component,
            details=result.details,
            timestamp=datetime.now(timezone.utc),
        )

        if self._escalation_manager.escalate(event):
            self._escalation_count += 1
            logger.warning(f"[SelfHealerWatchdog] Escalated: {component}")

    def _run_loop(self) -> None:
        """Watchdog 루프."""
        while self._running:
            try:
                self.check_health()
            except Exception as e:
                logger.error(f"[SelfHealerWatchdog] Loop error: {e}")

            time.sleep(self._settings.probe_interval_seconds)

    def start(self) -> None:
        """Watchdog 시작."""
        if not self._settings.enabled:
            logger.info("[SelfHealerWatchdog] Disabled")
            return

        if self._running:
            return

        self._running = True
        self._worker = threading.Thread(
            target=self._run_loop,
            name="SelfHealerWatchdog",
            daemon=True,
        )
        self._worker.start()
        logger.info("[SelfHealerWatchdog] Started")

    def stop(self) -> None:
        """Watchdog 중지."""
        self._running = False
        if self._worker:
            self._worker.join(timeout=10.0)
        logger.info("[SelfHealerWatchdog] Stopped")

    def get_state(self) -> WatchdogState:
        """현재 상태 반환."""
        with self._lock:
            results = self._probe_manager.get_last_results()
            return WatchdogState(
                overall_status=self._probe_manager.get_overall_status(),
                component_statuses={name: r.status for name, r in results.items()},
                last_check=self._last_check or datetime.now(timezone.utc),
                escalation_pending=False,
                escalation_count=self._escalation_count,
            )


# =============================================================================
# Singleton
# =============================================================================

_watchdog: SelfHealerWatchdog | None = None
_watchdog_lock = threading.Lock()


def get_selfhealer_watchdog() -> SelfHealerWatchdog:
    """SelfHealerWatchdog 싱글톤 반환."""
    global _watchdog
    if _watchdog is None:
        with _watchdog_lock:
            if _watchdog is None:
                _watchdog = SelfHealerWatchdog()
    return _watchdog


def reset_selfhealer_watchdog() -> None:
    """Watchdog 리셋 (테스트용)."""
    global _watchdog
    with _watchdog_lock:
        if _watchdog is not None:
            _watchdog.stop()
            _watchdog = None
```

---

## 4. 사용 예시

### 4.1 Django AppConfig 통합

```python
# selfhealing/apps.py

class SelfHealingConfig(AppConfig):
    """Self-Healing Django App Configuration."""

    def ready(self):
        # Meta-Watchdog 시작
        if os.environ.get("SELFHEALING_META_ENABLED", "true").lower() == "true":
            from selfhealing.meta.watchdog import get_selfhealer_watchdog

            watchdog = get_selfhealer_watchdog()
            watchdog.start()
```

### 4.2 Health Endpoint

```python
# api/health.py

from selfhealing.meta.watchdog import get_selfhealer_watchdog

@api_view(["GET"])
def selfhealing_health(request):
    """Self-Healing 시스템 건강 상태."""
    watchdog = get_selfhealer_watchdog()
    state = watchdog.get_state()

    return Response({
        "status": state.overall_status.value,
        "components": {
            name: status.value
            for name, status in state.component_statuses.items()
        },
        "last_check": state.last_check.isoformat(),
        "escalation_count": state.escalation_count,
    })
```

---

## 5. 설정 예시

```bash
# .env

# Meta-Watchdog 기본 설정
SELFHEALING_META_ENABLED=true
SELFHEALING_META_PROBE_INTERVAL_SECONDS=30
SELFHEALING_META_STUCK_THRESHOLD_SECONDS=300

# Self-Healing Circuit Breaker
SELFHEALING_META_SELF_CB_ENABLED=true
SELFHEALING_META_SELF_CB_FAILURE_THRESHOLD=5

# Escalation
SELFHEALING_META_ESCALATION_ENABLED=true
SELFHEALING_META_PAGERDUTY_ROUTING_KEY=your-routing-key
SELFHEALING_META_SLACK_WEBHOOK_URL=https://hooks.slack.com/services/xxx
```

---

## 6. 구현 체크리스트

- [ ] `meta/__init__.py` 생성
- [ ] `meta/config.py` 구현
- [ ] `meta/health_probe.py` 구현
- [ ] `meta/stuck_detector.py` 구현
- [ ] `meta/escalation.py` 구현
- [ ] `meta/watchdog.py` 구현
- [ ] Django AppConfig 통합
- [ ] Health Endpoint 추가
- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성

---

## 7. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [audit_watchdog.py](../../packages/selfhealing-python/src/selfhealing/audit/audit_watchdog.py) - 기존 Audit Watchdog

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |

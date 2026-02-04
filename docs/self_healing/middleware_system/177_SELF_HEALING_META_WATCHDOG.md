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

## 6. 고급 기능 구현 명세

### 6.1 전용 RBAC 정의 (K8s 권한)

**파일**: `k8s/selfhealing-watchdog-rbac.yaml` (신규)

**코드 근거**: `k8s/deployment-correlator-rbac.yaml` 패턴 참조

```yaml
# k8s/selfhealing-watchdog-rbac.yaml
# Meta-Watchdog 전용 RBAC - 복구 작업을 위한 K8s 권한
#
# 기존 deployment-correlator-rbac.yaml 패턴 확장
# 복구에 필요한 patch, delete 권한 추가
#
# 적용 방법:
#   kubectl apply -f k8s/selfhealing-watchdog-rbac.yaml

apiVersion: v1
kind: ServiceAccount
metadata:
  name: selfhealing-watchdog
  namespace: selfhealing
  labels:
    app.kubernetes.io/name: selfhealing-watchdog
    app.kubernetes.io/component: meta-watchdog
    app.kubernetes.io/part-of: selfhealing

---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: selfhealing-watchdog
  labels:
    app.kubernetes.io/name: selfhealing-watchdog
    app.kubernetes.io/component: meta-watchdog
rules:
  # Deployment 관리 - 복구를 위한 patch/scale 권한
  - apiGroups: ["apps"]
    resources: ["deployments", "deployments/scale"]
    verbs: ["get", "list", "patch"]

  # Pod 관리 - 상태 확인 및 강제 삭제
  - apiGroups: [""]
    resources: ["pods"]
    verbs: ["get", "list", "delete"]

  # Pod 로그 조회 - 진단용
  - apiGroups: [""]
    resources: ["pods/log"]
    verbs: ["get"]

  # Events 조회 - 장애 분석용
  - apiGroups: [""]
    resources: ["events"]
    verbs: ["get", "list"]

---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: selfhealing-watchdog
  labels:
    app.kubernetes.io/name: selfhealing-watchdog
subjects:
  - kind: ServiceAccount
    name: selfhealing-watchdog
    namespace: selfhealing
roleRef:
  kind: ClusterRole
  name: selfhealing-watchdog
  apiGroup: rbac.authorization.k8s.io
```

### 6.2 복구 인프라 어댑터 (RecoveryInfrastructureAdapter)

**파일**: `packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py` (신규)

**코드 근거**: `adapters/deployment/kubernetes.py`, `adapters/deployment/base.py` 패턴 참조

```python
# packages/selfhealing-python/src/selfhealing/meta/recovery_adapter.py
"""
Recovery Infrastructure Adapter.

환경별(K8s/Docker/Local) 복구 전략 추상화.

코드 근거:
- adapters/deployment/base.py: ExternalDeploymentAdapter 패턴
- adapters/deployment/kubernetes.py: KubernetesDeploymentAdapter 패턴
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class RecoveryAction(Enum):
    """복구 액션 유형."""

    RESTART_WORKER = "restart_worker"
    SCALE_DEPLOYMENT = "scale_deployment"
    DELETE_POD = "delete_pod"
    RESET_CONNECTION = "reset_connection"


@dataclass
class RecoveryResult:
    """복구 결과."""

    action: RecoveryAction
    success: bool
    target: str
    message: str
    timestamp: datetime
    details: dict[str, Any] | None = None


class RecoveryInfrastructureAdapter(ABC):
    """
    복구 인프라 어댑터 인터페이스.

    환경에 따라 다른 복구 전략을 사용합니다:
    - K8s: Pod 삭제, Deployment scale
    - Docker Compose: Container restart
    - Local: Process signal
    """

    @abstractmethod
    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """워커 재시작."""
        pass

    @abstractmethod
    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """Deployment 스케일 조정."""
        pass

    @abstractmethod
    def delete_pod(self, pod_name: str, namespace: str) -> RecoveryResult:
        """Pod 강제 삭제."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """어댑터 사용 가능 여부."""
        pass


class KubernetesRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    Kubernetes 복구 어댑터.

    K8s API를 통해 Pod/Deployment 복구 수행.
    RBAC 권한 필요: selfhealing-watchdog ServiceAccount

    코드 근거: adapters/deployment/kubernetes.py
    """

    def __init__(self, namespace: str = "selfhealing"):
        self._namespace = namespace
        self._apps_v1 = None
        self._core_v1 = None
        self._is_available = False
        self._initialize_client()

    def _initialize_client(self) -> None:
        """K8s 클라이언트 초기화."""
        try:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
                logger.info("[K8sRecoveryAdapter] Loaded in-cluster config")
            except config.ConfigException:
                config.load_kube_config()
                logger.info("[K8sRecoveryAdapter] Loaded kubeconfig")

            self._apps_v1 = client.AppsV1Api()
            self._core_v1 = client.CoreV1Api()
            self._is_available = True
        except ImportError:
            logger.warning("[K8sRecoveryAdapter] kubernetes package not installed")
        except Exception as e:
            logger.warning(f"[K8sRecoveryAdapter] Init failed: {e}")

    def is_available(self) -> bool:
        return self._is_available

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """Celery Worker Pod 재시작 (Rolling restart via annotation)."""
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            # Deployment에 annotation 추가로 rolling restart 트리거
            patch = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "selfhealing.watchdog/restartedAt": datetime.now(
                                    timezone.utc
                                ).isoformat()
                            }
                        }
                    }
                }
            }
            self._apps_v1.patch_namespaced_deployment(
                name=worker_name,
                namespace=self._namespace,
                body=patch,
            )
            logger.info(f"[K8sRecoveryAdapter] Triggered restart: {worker_name}")
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=True,
                target=worker_name,
                message="Rolling restart triggered",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            logger.error(f"[K8sRecoveryAdapter] Restart failed: {e}")
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """Deployment replicas 조정."""
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            self._apps_v1.patch_namespaced_deployment_scale(
                name=name,
                namespace=self._namespace,
                body={"spec": {"replicas": replicas}},
            )
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=True,
                target=name,
                message=f"Scaled to {replicas} replicas",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def delete_pod(self, pod_name: str, namespace: str | None = None) -> RecoveryResult:
        """Pod 강제 삭제 (ReplicaSet이 재생성)."""
        ns = namespace or self._namespace
        if not self._is_available:
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=False,
                target=pod_name,
                message="K8s client not available",
                timestamp=datetime.now(timezone.utc),
            )

        try:
            self._core_v1.delete_namespaced_pod(name=pod_name, namespace=ns)
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=True,
                target=pod_name,
                message="Pod deleted, will be recreated by ReplicaSet",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.DELETE_POD,
                success=False,
                target=pod_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )


class DockerComposeRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    Docker Compose 복구 어댑터.

    로컬 개발 환경용. docker-compose 명령 사용.
    """

    def is_available(self) -> bool:
        """docker-compose 사용 가능 여부."""
        import shutil

        return shutil.which("docker-compose") is not None or shutil.which("docker") is not None

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        """Docker container 재시작."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker-compose", "restart", worker_name],
                capture_output=True,
                text=True,
                timeout=60,
            )
            success = result.returncode == 0
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=success,
                target=worker_name,
                message=result.stdout if success else result.stderr,
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.RESTART_WORKER,
                success=False,
                target=worker_name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        """Docker Compose scale."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker-compose", "up", "-d", "--scale", f"{name}={replicas}"],
                capture_output=True,
                text=True,
                timeout=120,
            )
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=result.returncode == 0,
                target=name,
                message=f"Scaled to {replicas}",
                timestamp=datetime.now(timezone.utc),
            )
        except Exception as e:
            return RecoveryResult(
                action=RecoveryAction.SCALE_DEPLOYMENT,
                success=False,
                target=name,
                message=str(e),
                timestamp=datetime.now(timezone.utc),
            )

    def delete_pod(self, pod_name: str, namespace: str = "") -> RecoveryResult:
        """Docker container 삭제 (scale down 효과)."""
        return self.restart_worker(pod_name)  # Docker에서는 restart로 대체


class NoOpRecoveryAdapter(RecoveryInfrastructureAdapter):
    """
    No-Op 복구 어댑터.

    테스트/드라이런 환경용. 실제 복구 수행 안함.
    """

    def is_available(self) -> bool:
        return True

    def restart_worker(self, worker_name: str) -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would restart: {worker_name}")
        return RecoveryResult(
            action=RecoveryAction.RESTART_WORKER,
            success=True,
            target=worker_name,
            message="No-op (dry run)",
            timestamp=datetime.now(timezone.utc),
        )

    def scale_deployment(self, name: str, replicas: int) -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would scale {name} to {replicas}")
        return RecoveryResult(
            action=RecoveryAction.SCALE_DEPLOYMENT,
            success=True,
            target=name,
            message=f"No-op (would scale to {replicas})",
            timestamp=datetime.now(timezone.utc),
        )

    def delete_pod(self, pod_name: str, namespace: str = "") -> RecoveryResult:
        logger.info(f"[NoOpRecoveryAdapter] Would delete pod: {pod_name}")
        return RecoveryResult(
            action=RecoveryAction.DELETE_POD,
            success=True,
            target=pod_name,
            message="No-op (dry run)",
            timestamp=datetime.now(timezone.utc),
        )


def get_recovery_adapter() -> RecoveryInfrastructureAdapter:
    """
    환경에 맞는 복구 어댑터 반환.

    환경변수 SELFHEALING_RECOVERY_ADAPTER로 선택:
    - kubernetes (기본, K8s 환경)
    - docker (Docker Compose 환경)
    - noop (테스트/드라이런)
    """
    adapter_type = os.environ.get("SELFHEALING_RECOVERY_ADAPTER", "kubernetes").lower()

    if adapter_type == "noop":
        return NoOpRecoveryAdapter()
    elif adapter_type == "docker":
        return DockerComposeRecoveryAdapter()
    else:
        adapter = KubernetesRecoveryAdapter()
        if adapter.is_available():
            return adapter
        # K8s 불가 시 Docker로 폴백
        docker_adapter = DockerComposeRecoveryAdapter()
        if docker_adapter.is_available():
            logger.info("[RecoveryAdapter] K8s unavailable, falling back to Docker")
            return docker_adapter
        # 최종 폴백: NoOp
        logger.warning("[RecoveryAdapter] No adapter available, using NoOp")
        return NoOpRecoveryAdapter()
```

### 6.3 Blast Radius 연동 및 Root Cause 억제

**파일**: `packages/selfhealing-python/src/selfhealing/meta/dependency_analyzer.py` (신규)

**코드 근거**:
- `services/circuit_breaker/blast_radius_integration.py`: BlastRadiusIntegration 패턴
- `load_tests/utils/selfhealing/dna_graph.py`: DependencyGraph 패턴

```python
# packages/selfhealing-python/src/selfhealing/meta/dependency_analyzer.py
"""
Dependency Analyzer - 의존성 분석 및 Root Cause 억제.

복구 전 Blast Radius 평가, 연쇄 장애 시 Root Cause만 알림.

코드 근거:
- services/circuit_breaker/blast_radius_integration.py#L256-370
- load_tests/utils/selfhealing/dna_graph.py#L100-120
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# =============================================================================
# 컴포넌트 의존성 맵 (Meta-Watchdog 전용)
# =============================================================================

# Redis를 의존하는 컴포넌트들
COMPONENT_DEPENDENCIES: dict[str, list[str]] = {
    "redis": ["circuit_breaker", "dlq", "recovery_pipeline"],
    "database": ["recovery_pipeline"],
    "celery_broker": ["dlq"],
}

# 역방향 의존성 (component -> root cause)
REVERSE_DEPENDENCIES: dict[str, str] = {}
for root, deps in COMPONENT_DEPENDENCIES.items():
    for dep in deps:
        REVERSE_DEPENDENCIES[dep] = root


@dataclass
class RecoveryImpactAssessment:
    """복구 영향 평가 결과."""

    component: str
    """대상 컴포넌트."""

    can_proceed: bool
    """복구 진행 가능 여부."""

    blast_radius_level: str
    """영향 범위 레벨 (MINIMAL/MODERATE/EXTENSIVE/CRITICAL)."""

    affected_components: list[str]
    """영향받는 컴포넌트 목록."""

    block_reason: str | None = None
    """차단 사유 (can_proceed=False 시)."""

    warnings: list[str] = field(default_factory=list)
    """경고 메시지 목록."""


@dataclass
class SuppressionResult:
    """알림 억제 결과."""

    component: str
    """대상 컴포넌트."""

    suppressed: bool
    """억제 여부."""

    root_cause: str | None
    """Root Cause 컴포넌트."""

    reason: str
    """억제/비억제 사유."""


class DependencyAnalyzer:
    """
    의존성 분석기.

    기능:
    1. 복구 전 Blast Radius 평가
    2. Root Cause 기반 알림 억제
    3. 복구 우선순위 결정

    코드 근거:
        BlastRadiusIntegration.assess_impact() 패턴
    """

    def __init__(self):
        self._dependencies = COMPONENT_DEPENDENCIES
        self._reverse_deps = REVERSE_DEPENDENCIES

    def assess_recovery_impact(
        self,
        component: str,
        failing_components: set[str] | None = None,
    ) -> RecoveryImpactAssessment:
        """
        복구 전 영향 평가.

        Args:
            component: 복구 대상 컴포넌트
            failing_components: 현재 실패 중인 컴포넌트 집합

        Returns:
            RecoveryImpactAssessment
        """
        failing = failing_components or set()

        # 해당 컴포넌트를 의존하는 컴포넌트 수집
        affected = self._dependencies.get(component, [])
        affected_count = len(affected)

        # 레벨 결정
        if affected_count >= 5:
            level = "CRITICAL"
            can_proceed = False
            block_reason = f"Too many dependent components ({affected_count})"
        elif affected_count >= 3:
            level = "EXTENSIVE"
            can_proceed = True
            block_reason = None
        elif affected_count >= 1:
            level = "MODERATE"
            can_proceed = True
            block_reason = None
        else:
            level = "MINIMAL"
            can_proceed = True
            block_reason = None

        # 경고 생성
        warnings = []
        if level in ("EXTENSIVE", "CRITICAL"):
            warnings.append(f"Recovery may affect {affected_count} components")

        # 이미 실패 중인 컴포넌트와 겹치면 추가 경고
        overlap = set(affected) & failing
        if overlap:
            warnings.append(f"Already failing components affected: {overlap}")

        return RecoveryImpactAssessment(
            component=component,
            can_proceed=can_proceed,
            blast_radius_level=level,
            affected_components=affected,
            block_reason=block_reason,
            warnings=warnings,
        )

    def should_suppress_alert(
        self,
        component: str,
        failed_components: set[str],
    ) -> SuppressionResult:
        """
        Root Cause 기반 알림 억제 판단.

        예: Redis 실패 시 CB/DLQ 알림 억제

        Args:
            component: 알림 대상 컴포넌트
            failed_components: 현재 실패 중인 모든 컴포넌트

        Returns:
            SuppressionResult
        """
        # 이 컴포넌트의 root cause 확인
        root_cause = self._reverse_deps.get(component)

        if root_cause and root_cause in failed_components:
            # Root cause도 실패 중이면 이 컴포넌트 알림 억제
            return SuppressionResult(
                component=component,
                suppressed=True,
                root_cause=root_cause,
                reason=f"Suppressed: {root_cause} is the root cause",
            )

        return SuppressionResult(
            component=component,
            suppressed=False,
            root_cause=None,
            reason="No root cause detected, alert should proceed",
        )

    def get_recovery_priority(
        self,
        failed_components: set[str],
    ) -> list[str]:
        """
        복구 우선순위 결정.

        Root cause (인프라)를 먼저 복구해야 의존 컴포넌트도 복구됨.

        Args:
            failed_components: 실패 컴포넌트 집합

        Returns:
            우선순위 정렬된 컴포넌트 목록
        """
        priority = []

        # 1순위: Root cause 컴포넌트 (redis, database 등)
        root_causes = set(self._dependencies.keys())
        for root in root_causes:
            if root in failed_components:
                priority.append(root)

        # 2순위: 나머지 컴포넌트
        for comp in failed_components:
            if comp not in priority:
                priority.append(comp)

        return priority


# 싱글톤
_analyzer: DependencyAnalyzer | None = None


def get_dependency_analyzer() -> DependencyAnalyzer:
    """DependencyAnalyzer 싱글톤."""
    global _analyzer
    if _analyzer is None:
        _analyzer = DependencyAnalyzer()
    return _analyzer
```

### 6.4 Stuck Detector (Zero-variance 감지)

**파일**: `packages/selfhealing-python/src/selfhealing/meta/stuck_detector.py` (신규)

**코드 근거**:
- `services/corruption_shield/validators.py#L419`: variance 계산 패턴
- `tasks/intelligence_tasks.py#L615-620`: high_variance 감지 패턴

```python
# packages/selfhealing-python/src/selfhealing/meta/stuck_detector.py
"""
Stuck Detector - Zero-variance 기반 Stuck 감지.

메트릭의 분산이 0에 가깝고 에러율이 높으면 Stuck으로 판단.

코드 근거:
- services/corruption_shield/validators.py#L419-420: variance 계산
- tasks/intelligence_tasks.py#L615-620: high_variance 패턴
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricSample:
    """메트릭 샘플."""

    value: float
    timestamp: float
    error: bool = False


@dataclass
class MetricWindow:
    """메트릭 윈도우."""

    samples: deque[MetricSample]
    max_size: int = 20

    def add(self, value: float, error: bool = False) -> None:
        """샘플 추가."""
        self.samples.append(
            MetricSample(value=value, timestamp=time.time(), error=error)
        )
        while len(self.samples) > self.max_size:
            self.samples.popleft()

    def variance(self) -> float:
        """분산 계산."""
        if len(self.samples) < 2:
            return float("inf")  # 샘플 부족 시 무한대

        values = [s.value for s in self.samples]
        n = len(values)
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        return variance

    def error_rate(self) -> float:
        """에러율 계산."""
        if not self.samples:
            return 0.0
        error_count = sum(1 for s in self.samples if s.error)
        return error_count / len(self.samples)

    def is_stuck(
        self,
        variance_threshold: float = 0.001,
        error_rate_threshold: float = 0.5,
    ) -> bool:
        """
        Stuck 여부 판단.

        조건: 분산 ≈ 0 AND 에러율 > 50%

        Args:
            variance_threshold: 분산 임계치 (기본 0.001)
            error_rate_threshold: 에러율 임계치 (기본 50%)

        Returns:
            Stuck 여부
        """
        if len(self.samples) < 5:
            return False  # 최소 샘플 필요

        var = self.variance()
        err_rate = self.error_rate()

        # 분산이 매우 낮고 에러율이 높으면 Stuck
        return var < variance_threshold and err_rate > error_rate_threshold


@dataclass
class StuckDetectionResult:
    """Stuck 감지 결과."""

    component: str
    is_stuck: bool
    variance: float
    error_rate: float
    sample_count: int
    duration_seconds: float
    details: dict[str, Any] = field(default_factory=dict)


class StuckDetector:
    """
    Stuck 감지기.

    각 컴포넌트의 메트릭을 추적하고 Zero-variance 상태를 감지합니다.

    Zero-variance Stuck:
        특정 메트릭 X가 일정 시간 동안 σ²(X) ≈ 0 (변화량 없음)이면서
        에러율이 높은 경우, 시스템이 논리적으로 멈춘 것으로 판단.

    Usage:
        detector = StuckDetector()

        # 메트릭 기록
        detector.record("dlq", pending_count=100, error=False)
        detector.record("dlq", pending_count=100, error=True)  # 에러

        # Stuck 확인
        result = detector.check("dlq")
        if result.is_stuck:
            trigger_recovery()
    """

    def __init__(
        self,
        window_size: int = 20,
        variance_threshold: float = 0.001,
        error_rate_threshold: float = 0.5,
    ):
        """
        Args:
            window_size: 샘플 윈도우 크기
            variance_threshold: 분산 임계치
            error_rate_threshold: 에러율 임계치
        """
        self._window_size = window_size
        self._variance_threshold = variance_threshold
        self._error_rate_threshold = error_rate_threshold

        self._windows: dict[str, MetricWindow] = {}
        self._first_sample_time: dict[str, float] = {}
        self._lock = threading.RLock()

    def record(
        self,
        component: str,
        value: float,
        error: bool = False,
    ) -> None:
        """
        메트릭 기록.

        Args:
            component: 컴포넌트 이름
            value: 메트릭 값 (예: pending_count, queue_size)
            error: 이 샘플이 에러 상태인지
        """
        with self._lock:
            if component not in self._windows:
                self._windows[component] = MetricWindow(
                    samples=deque(maxlen=self._window_size),
                    max_size=self._window_size,
                )
                self._first_sample_time[component] = time.time()

            self._windows[component].add(value=value, error=error)

    def check(self, component: str) -> StuckDetectionResult:
        """
        Stuck 여부 확인.

        Args:
            component: 컴포넌트 이름

        Returns:
            StuckDetectionResult
        """
        with self._lock:
            if component not in self._windows:
                return StuckDetectionResult(
                    component=component,
                    is_stuck=False,
                    variance=float("inf"),
                    error_rate=0.0,
                    sample_count=0,
                    duration_seconds=0.0,
                )

            window = self._windows[component]
            first_time = self._first_sample_time.get(component, time.time())
            duration = time.time() - first_time

            is_stuck = window.is_stuck(
                variance_threshold=self._variance_threshold,
                error_rate_threshold=self._error_rate_threshold,
            )

            return StuckDetectionResult(
                component=component,
                is_stuck=is_stuck,
                variance=window.variance(),
                error_rate=window.error_rate(),
                sample_count=len(window.samples),
                duration_seconds=duration,
                details={
                    "variance_threshold": self._variance_threshold,
                    "error_rate_threshold": self._error_rate_threshold,
                },
            )

    def check_all(self) -> dict[str, StuckDetectionResult]:
        """모든 컴포넌트 Stuck 확인."""
        with self._lock:
            return {comp: self.check(comp) for comp in self._windows}

    def clear(self, component: str | None = None) -> None:
        """
        메트릭 초기화.

        Args:
            component: 특정 컴포넌트만 초기화 (None이면 전체)
        """
        with self._lock:
            if component:
                self._windows.pop(component, None)
                self._first_sample_time.pop(component, None)
            else:
                self._windows.clear()
                self._first_sample_time.clear()


# 싱글톤
_stuck_detector: StuckDetector | None = None


def get_stuck_detector() -> StuckDetector:
    """StuckDetector 싱글톤."""
    global _stuck_detector
    if _stuck_detector is None:
        _stuck_detector = StuckDetector()
    return _stuck_detector
```

### 6.5 Redis 기반 상태 저장소 (분산 환경 지원)

**파일**: `packages/selfhealing-python/src/selfhealing/meta/state_store.py` (신규)

**코드 근거**:
- `services/coordination/critical_path_fallback.py`: Redis → Local → Memory 폴백
- `services/coordination/distributed_recovery_lock.py`: Redis SET NX 패턴

```python
# packages/selfhealing-python/src/selfhealing/meta/state_store.py
"""
Meta-Watchdog 상태 저장소.

Pod 재시작 시에도 상태 유지를 위한 Redis 기반 저장소.

코드 근거:
- services/coordination/critical_path_fallback.py: 폴백 체인 패턴
- services/coordination/distributed_recovery_lock.py: Redis 키 패턴
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


class WatchdogStateStore:
    """
    Watchdog 상태 저장소.

    Redis 기반으로 다음 상태를 영속화:
    - consecutive_failures: 컴포넌트별 연속 실패 횟수
    - escalation_cooldowns: 에스컬레이션 쿨다운 시각
    - last_check: 마지막 체크 시각

    Pod 재시작 후에도 상태를 이어받아 즉시 에스컬레이션 가능.
    """

    # Redis 키 패턴
    KEY_PREFIX = "selfhealing:meta:watchdog"
    FAILURES_KEY = f"{KEY_PREFIX}:failures"  # Hash
    COOLDOWNS_KEY = f"{KEY_PREFIX}:cooldowns"  # Hash
    LAST_CHECK_KEY = f"{KEY_PREFIX}:last_check"  # String
    LAST_LOOP_KEY = f"{KEY_PREFIX}:last_loop_timestamp"  # String (Liveness용)

    # TTL (7일 - 오래된 데이터 자동 정리)
    STATE_TTL_SECONDS = 7 * 24 * 60 * 60

    def __init__(self, redis_client: Any | None = None):
        """
        Args:
            redis_client: Redis 클라이언트 (None이면 자동 획득)
        """
        self._redis = redis_client
        self._local_failures: dict[str, int] = {}  # 폴백용
        self._local_cooldowns: dict[str, float] = {}
        self._lock = threading.RLock()

    def _get_redis(self) -> Any | None:
        """Redis 클라이언트 획득."""
        if self._redis is not None:
            return self._redis

        try:
            from selfhealing.adapters.cache import get_redis_client

            return get_redis_client()
        except Exception:
            return None

    # =========================================================================
    # Consecutive Failures
    # =========================================================================

    def get_failure_count(self, component: str) -> int:
        """연속 실패 횟수 조회."""
        redis = self._get_redis()
        if redis:
            try:
                count = redis.hget(self.FAILURES_KEY, component)
                if count:
                    return int(count)
            except Exception as e:
                logger.debug(f"[WatchdogStateStore] Redis get failed: {e}")

        # 폴백: 로컬 메모리
        with self._lock:
            return self._local_failures.get(component, 0)

    def increment_failure_count(self, component: str) -> int:
        """연속 실패 횟수 증가."""
        redis = self._get_redis()
        if redis:
            try:
                new_count = redis.hincrby(self.FAILURES_KEY, component, 1)
                redis.expire(self.FAILURES_KEY, self.STATE_TTL_SECONDS)
                return new_count
            except Exception as e:
                logger.debug(f"[WatchdogStateStore] Redis incr failed: {e}")

        # 폴백
        with self._lock:
            self._local_failures[component] = self._local_failures.get(component, 0) + 1
            return self._local_failures[component]

    def reset_failure_count(self, component: str) -> None:
        """연속 실패 횟수 리셋."""
        redis = self._get_redis()
        if redis:
            try:
                redis.hdel(self.FAILURES_KEY, component)
            except Exception:
                pass

        with self._lock:
            self._local_failures.pop(component, None)

    # =========================================================================
    # Escalation Cooldowns
    # =========================================================================

    def get_last_escalation_time(self, component: str) -> float:
        """마지막 에스컬레이션 시각 조회."""
        redis = self._get_redis()
        if redis:
            try:
                ts = redis.hget(self.COOLDOWNS_KEY, component)
                if ts:
                    return float(ts)
            except Exception:
                pass

        with self._lock:
            return self._local_cooldowns.get(component, 0)

    def record_escalation(self, component: str) -> None:
        """에스컬레이션 기록."""
        now = time.time()
        redis = self._get_redis()
        if redis:
            try:
                redis.hset(self.COOLDOWNS_KEY, component, str(now))
                redis.expire(self.COOLDOWNS_KEY, self.STATE_TTL_SECONDS)
            except Exception:
                pass

        with self._lock:
            self._local_cooldowns[component] = now

    def can_escalate(self, component: str, cooldown_seconds: float) -> bool:
        """쿨다운 확인."""
        last_time = self.get_last_escalation_time(component)
        return time.time() - last_time > cooldown_seconds

    # =========================================================================
    # Last Loop Timestamp (Liveness용)
    # =========================================================================

    def update_last_loop_timestamp(self) -> None:
        """마지막 루프 타임스탬프 갱신."""
        now = datetime.now(timezone.utc).isoformat()
        redis = self._get_redis()
        if redis:
            try:
                redis.set(self.LAST_LOOP_KEY, now, ex=300)  # 5분 TTL
            except Exception:
                pass

    def get_last_loop_timestamp(self) -> datetime | None:
        """마지막 루프 타임스탬프 조회."""
        redis = self._get_redis()
        if redis:
            try:
                ts = redis.get(self.LAST_LOOP_KEY)
                if ts:
                    if isinstance(ts, bytes):
                        ts = ts.decode("utf-8")
                    return datetime.fromisoformat(ts)
            except Exception:
                pass
        return None

    def get_last_loop_age_seconds(self) -> float:
        """마지막 루프 이후 경과 시간."""
        last = self.get_last_loop_timestamp()
        if last is None:
            return float("inf")
        return (datetime.now(timezone.utc) - last).total_seconds()

    # =========================================================================
    # 분산 락 (중복 에스컬레이션 방지)
    # =========================================================================

    def acquire_escalation_lock(
        self,
        component: str,
        lock_ttl_seconds: int = 30,
    ) -> bool:
        """
        에스컬레이션 락 획득.

        멀티 인스턴스 환경에서 동일 컴포넌트에 대해
        하나의 인스턴스만 에스컬레이션하도록 보장.

        코드 근거: adapters/cache/redis_adapter.py#L80-110
        """
        redis = self._get_redis()
        if not redis:
            return True  # Redis 없으면 락 없이 진행

        lock_key = f"{self.KEY_PREFIX}:escalation:lock:{component}"
        try:
            # SET NX EX: 키가 없을 때만 설정 + TTL
            acquired = redis.set(lock_key, "1", nx=True, ex=lock_ttl_seconds)
            return bool(acquired)
        except Exception as e:
            logger.debug(f"[WatchdogStateStore] Lock acquire failed: {e}")
            return True  # 실패 시 진행 허용

    def release_escalation_lock(self, component: str) -> None:
        """에스컬레이션 락 해제."""
        redis = self._get_redis()
        if redis:
            lock_key = f"{self.KEY_PREFIX}:escalation:lock:{component}"
            try:
                redis.delete(lock_key)
            except Exception:
                pass


# 싱글톤
_state_store: WatchdogStateStore | None = None


def get_watchdog_state_store() -> WatchdogStateStore:
    """WatchdogStateStore 싱글톤."""
    global _state_store
    if _state_store is None:
        _state_store = WatchdogStateStore()
    return _state_store
```

### 6.6 에스컬레이션 폴백 (로컬 디스크 기록)

**파일**: `packages/selfhealing-python/src/selfhealing/meta/fallback_escalation.py` (신규)

**코드 근거**:
- `services/coordination/critical_path_fallback.py#L30`: DEFAULT_AUDIT_PATH 패턴
- `services/namespace_emergency/escalation_audit.py#L555-565`: _persist_to_fallback 패턴

```python
# packages/selfhealing-python/src/selfhealing/meta/fallback_escalation.py
"""
Fallback Escalation Handler.

Slack/PagerDuty 전송 실패 시 로컬 디스크에 기록.

코드 근거:
- services/coordination/critical_path_fallback.py#L30-32: 경로 패턴
- services/namespace_emergency/escalation_audit.py#L555-565: persist 패턴
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 폴백 로그 경로 (176 문서 DiskBuffer와 동일 위치)
DEFAULT_ESCALATION_LOG_PATH = Path(
    os.environ.get(
        "SELFHEALING_EMERGENCY_ESCALATION_LOG",
        "/var/log/selfhealing/emergency_escalation.jsonl",
    )
)


class FallbackEscalationHandler:
    """
    폴백 에스컬레이션 핸들러.

    Slack/PagerDuty API 장애 시 로컬 디스크에 기록.
    나중에 drain하거나 수동으로 처리 가능.

    코드 근거:
        critical_path_fallback.py (3단계 Fallback 패턴)
    """

    def __init__(self, log_path: Path | None = None):
        self._log_path = log_path or DEFAULT_ESCALATION_LOG_PATH
        self._lock = threading.Lock()
        self._memory_buffer: list[dict[str, Any]] = []
        self._max_buffer_size = 1000

    def _ensure_directory(self) -> bool:
        """디렉토리 생성."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            logger.error(f"[FallbackEscalation] Cannot create directory: {e}")
            return False

    def record_failed_escalation(
        self,
        component: str,
        title: str,
        description: str,
        level: str,
        details: dict[str, Any],
        failed_channels: list[str],
        error_message: str,
    ) -> bool:
        """
        실패한 에스컬레이션 기록.

        Args:
            component: 컴포넌트 이름
            title: 에스컬레이션 제목
            description: 설명
            level: 심각도 레벨
            details: 상세 정보
            failed_channels: 실패한 채널 목록
            error_message: 에러 메시지

        Returns:
            기록 성공 여부
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "EMERGENCY_ESCALATION_FAILED",
            "component": component,
            "title": title,
            "description": description,
            "level": level,
            "details": details,
            "failed_channels": failed_channels,
            "error": error_message,
            "requires_manual_review": True,
        }

        # 1. 파일에 기록 시도
        if self._write_to_file(entry):
            return True

        # 2. 메모리 버퍼에 저장 (폴백)
        return self._write_to_memory(entry)

    def _write_to_file(self, entry: dict[str, Any]) -> bool:
        """파일에 기록."""
        if not self._ensure_directory():
            return False

        try:
            with self._lock:
                with open(self._log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            logger.warning(
                f"[EMERGENCY_ESCALATION_LOG] Recorded: {entry['component']} - {entry['title']}"
            )
            return True
        except Exception as e:
            logger.error(f"[FallbackEscalation] File write failed: {e}")
            return False

    def _write_to_memory(self, entry: dict[str, Any]) -> bool:
        """메모리 버퍼에 저장."""
        with self._lock:
            self._memory_buffer.append(entry)
            if len(self._memory_buffer) > self._max_buffer_size:
                self._memory_buffer = self._memory_buffer[-self._max_buffer_size:]

        logger.warning(
            f"[FallbackEscalation] Stored in memory: {entry['component']} "
            f"(buffer size: {len(self._memory_buffer)})"
        )
        return True

    def get_pending_escalations(self) -> list[dict[str, Any]]:
        """대기 중인 에스컬레이션 조회."""
        entries = []

        # 파일에서 읽기
        if self._log_path.exists():
            try:
                with open(self._log_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            entries.append(json.loads(line))
            except Exception as e:
                logger.error(f"[FallbackEscalation] File read failed: {e}")

        # 메모리 버퍼 추가
        with self._lock:
            entries.extend(self._memory_buffer)

        return entries

    def get_pending_count(self) -> int:
        """대기 중인 에스컬레이션 수."""
        count = 0

        if self._log_path.exists():
            try:
                with open(self._log_path, encoding="utf-8") as f:
                    count = sum(1 for _ in f)
            except Exception:
                pass

        with self._lock:
            count += len(self._memory_buffer)

        return count

    def clear_file(self) -> None:
        """파일 초기화 (처리 완료 후)."""
        try:
            if self._log_path.exists():
                self._log_path.unlink()
        except Exception as e:
            logger.error(f"[FallbackEscalation] Clear failed: {e}")


# 싱글톤
_handler: FallbackEscalationHandler | None = None


def get_fallback_escalation_handler() -> FallbackEscalationHandler:
    """FallbackEscalationHandler 싱글톤."""
    global _handler
    if _handler is None:
        _handler = FallbackEscalationHandler()
    return _handler
```

### 6.7 Watchdog Liveness Endpoint

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/views/meta_watchdog.py` (신규)

**코드 근거**: `api/django/views/health.py#L98-110`: LivenessView 패턴

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/meta_watchdog.py
"""
Meta-Watchdog API Views.

Kubernetes Liveness Probe용 엔드포인트.

코드 근거:
- api/django/views/health.py#L98-110: LivenessView 패턴
"""

from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class MetaWatchdogLivenessView(APIView):
    """
    Meta-Watchdog Liveness Probe.

    GET /api/self-healing/health/meta-watchdog/

    Watchdog 루프가 stuck 되지 않았는지 확인.
    K8s가 이 엔드포인트를 통해 Watchdog Pod를 재시작할 수 있음.

    코드 근거: api/django/views/health.py#L98-110
    """

    permission_classes = []  # Public for K8s

    def get(self, request):
        """
        Watchdog liveness 확인.

        last_loop_timestamp가 임계치(probe_interval * 3) 이내인지 확인.
        """
        try:
            from selfhealing.meta.config import get_meta_watchdog_settings
            from selfhealing.meta.state_store import get_watchdog_state_store

            settings = get_meta_watchdog_settings()
            store = get_watchdog_state_store()

            # 마지막 루프 경과 시간
            age_seconds = store.get_last_loop_age_seconds()

            # 임계치: probe_interval의 3배
            max_age = settings.probe_interval_seconds * 3

            if age_seconds > max_age:
                logger.warning(
                    f"[MetaWatchdog] Liveness check FAILED: "
                    f"age={age_seconds:.1f}s > max={max_age:.1f}s"
                )
                return Response(
                    {
                        "status": "stuck",
                        "last_loop_age_seconds": age_seconds,
                        "max_age_seconds": max_age,
                        "message": "Watchdog loop appears to be stuck",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )

            return Response(
                {
                    "status": "alive",
                    "last_loop_age_seconds": age_seconds,
                    "max_age_seconds": max_age,
                }
            )

        except ImportError:
            # Meta-Watchdog 모듈 없음
            return Response(
                {"status": "unavailable", "message": "Meta-Watchdog not installed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            logger.error(f"[MetaWatchdog] Liveness check error: {e}")
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MetaWatchdogStatusView(APIView):
    """
    Meta-Watchdog 상태 조회.

    GET /api/self-healing/meta/status/
    """

    permission_classes = []  # Public

    def get(self, request):
        """Watchdog 상태 조회."""
        try:
            from selfhealing.meta.watchdog import get_selfhealer_watchdog

            watchdog = get_selfhealer_watchdog()
            state = watchdog.get_state()

            return Response(
                {
                    "overall_status": state.overall_status.value,
                    "components": {
                        name: status.value
                        for name, status in state.component_statuses.items()
                    },
                    "last_check": state.last_check.isoformat(),
                    "escalation_count": state.escalation_count,
                    "escalation_pending": state.escalation_pending,
                }
            )

        except ImportError:
            return Response(
                {"status": "unavailable", "message": "Meta-Watchdog not installed"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as e:
            return Response(
                {"status": "error", "message": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
```

### 6.8 유지보수 모드 (Dry-run Mode)

**파일 수정**: `config.py`에 설정 추가

**코드 근거**: `services/security_notification/service.py`: dry_run 패턴

```python
# config.py에 추가할 설정

class MetaWatchdogSettings(BaseSettings):
    # ... 기존 설정 ...

    # 유지보수/Dry-run 모드
    dry_run_mode: bool = Field(
        default=False,
        description="프로브만 수행, 복구/에스컬레이션 미수행 (관찰 모드)",
    )

    maintenance_components: list[str] = Field(
        default_factory=list,
        description="유지보수 중인 컴포넌트 목록 (해당 컴포넌트 알림 억제)",
    )
```

### 6.9 Audit Recorder 연동

**watchdog.py 수정**: `_attempt_recovery` 전후에 Audit 기록

**코드 근거**:
- `services/coordination/recovery_audit.py#L287-400`: RecoveryAuditRecorder
- `services/coordination/recovery_audit.py#L399-450`: record_dangerous_force_recovery

```python
# watchdog.py의 _attempt_recovery 메서드 수정

def _attempt_recovery(self, component: str, result: ProbeResult) -> bool:
    """자동 복구 시도 (Audit 연동)."""
    from selfhealing.services.coordination.recovery_audit import (
        RecoveryAuditEventType,
        get_recovery_audit_recorder,
    )

    recorder = get_recovery_audit_recorder()
    session_id = f"meta-watchdog-{component}-{int(time.time())}"

    # 복구 시작 Audit
    recorder.record_recovery_event(
        event_type=RecoveryAuditEventType.RECOVERY_STARTED,
        session_id=session_id,
        namespace="meta-watchdog",
        step_type=f"recover_{component}",
        executed_by="meta-watchdog",
        metadata={"component": component, "probe_result": result.details},
    )

    start_time = time.time()
    success = False

    try:
        if component == "circuit_breaker":
            success = self._recover_circuit_breaker(result)
        elif component == "dlq":
            success = self._recover_dlq(result)
        elif component == "redis":
            success = self._recover_redis(result)

        duration_ms = (time.time() - start_time) * 1000

        # 복구 완료 Audit
        recorder.record_recovery_event(
            event_type=(
                RecoveryAuditEventType.RECOVERY_COMPLETED
                if success
                else RecoveryAuditEventType.RECOVERY_STEP_FAILED
            ),
            session_id=session_id,
            namespace="meta-watchdog",
            step_type=f"recover_{component}",
            executed_by="meta-watchdog",
            success=success,
            duration_ms=duration_ms,
        )

        return success

    except Exception as e:
        duration_ms = (time.time() - start_time) * 1000

        # 실패 Audit
        recorder.record_recovery_event(
            event_type=RecoveryAuditEventType.RECOVERY_STEP_FAILED,
            session_id=session_id,
            namespace="meta-watchdog",
            step_type=f"recover_{component}",
            executed_by="meta-watchdog",
            success=False,
            error_message=str(e),
            duration_ms=duration_ms,
        )

        logger.error(f"[SelfHealerWatchdog] Recovery failed for {component}: {e}")
        return False
```

---

## 7. URL 라우팅 설정

```python
# selfhealing/api/django/urls.py에 추가

from selfhealing.api.django.views.meta_watchdog import (
    MetaWatchdogLivenessView,
    MetaWatchdogStatusView,
)

urlpatterns = [
    # ... 기존 URL ...

    # Meta-Watchdog
    path(
        "health/meta-watchdog/",
        MetaWatchdogLivenessView.as_view(),
        name="meta_watchdog_liveness",
    ),
    path(
        "meta/status/",
        MetaWatchdogStatusView.as_view(),
        name="meta_watchdog_status",
    ),
]
```

---

## 8. 구현 체크리스트

### 8.1 코어 모듈

- [ ] `meta/__init__.py` 생성
- [ ] `meta/config.py` 구현 (dry_run_mode, maintenance_components 포함)
- [ ] `meta/health_probe.py` 구현
- [ ] `meta/stuck_detector.py` 구현 (Zero-variance 감지)
- [ ] `meta/escalation.py` 구현
- [ ] `meta/watchdog.py` 구현

### 8.2 고급 기능

- [ ] `meta/recovery_adapter.py` 구현 (K8s/Docker/NoOp)
- [ ] `meta/dependency_analyzer.py` 구현 (Blast Radius, Root Cause 억제)
- [ ] `meta/state_store.py` 구현 (Redis 기반 상태 저장)
- [ ] `meta/fallback_escalation.py` 구현 (디스크 폴백)

### 8.3 API & 인프라

- [ ] `api/django/views/meta_watchdog.py` 구현 (Liveness Endpoint)
- [ ] URL 라우팅 설정
- [ ] `k8s/selfhealing-watchdog-rbac.yaml` 생성
- [ ] Django AppConfig 통합

### 8.4 테스트

- [ ] 단위 테스트 작성
- [ ] 통합 테스트 작성
- [ ] K8s 환경 테스트

---

## 9. 관련 문서

- [174_MISSING_SYSTEMS_MASTER_PLAN.md](174_MISSING_SYSTEMS_MASTER_PLAN.md) - 마스터 플랜
- [176_DISK_PERSISTENT_BUFFER.md](176_DISK_PERSISTENT_BUFFER.md) - 디스크 버퍼 (폴백 경로 연동)
- [audit_watchdog.py](../../packages/selfhealing-python/src/selfhealing/audit/audit_watchdog.py) - 기존 Audit Watchdog
- [blast_radius_integration.py](../../packages/selfhealing-python/src/selfhealing/services/circuit_breaker/blast_radius_integration.py) - Blast Radius 패턴
- [distributed_recovery_lock.py](../../packages/selfhealing-python/src/selfhealing/services/coordination/distributed_recovery_lock.py) - 분산 락 패턴
- [critical_path_fallback.py](../../packages/selfhealing-python/src/selfhealing/services/coordination/critical_path_fallback.py) - 폴백 체인 패턴

---

## 10. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-02-04 | 초안 작성 |
| 1.1.0 | 2026-02-04 | 고급 기능 추가 (RBAC, 분산 락, Blast Radius, Stuck Detector 등) |

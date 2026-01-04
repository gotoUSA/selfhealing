# DLQ Replay 자동화 구현 블루프린트

> **문서 작성일**: 2026-01-04  
> **상태**: 설계 완료 - 구현 대기  
> **관련 문서**: 
> - [17_SYSTEM_ARCHITECTURE_DIAGRAM.md](17_SYSTEM_ARCHITECTURE_DIAGRAM.md)
> - [18_PHASE1_DOCUMENT_REVIEW_PART1.md](18_PHASE1_DOCUMENT_REVIEW_PART1.md)

---

## 1. 개요

### 1.0 Retry vs Replay - 명확한 구분

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Retry vs Replay 구분                              │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────┐      ┌────────────────────────┐             │
│  │        RETRY           │      │        REPLAY          │             │
│  │   (RetryHandler)       │      │   (ReplayService)      │             │
│  ├────────────────────────┤      ├────────────────────────┤             │
│  │ 시점: 즉시 (동기)       │      │ 시점: 나중에 (비동기)   │             │
│  │ 위치: 메모리 내         │      │ 위치: DB/Redis 영구저장 │             │
│  │ 횟수: max_attempts (3)  │      │ 횟수: max_retries (5)  │             │
│  │ 트리거: 자동 (코드 내)   │      │ 트리거: Beat/CB/수동   │             │
│  │ 실패시: DLQ 저장        │      │ 실패시: REQUIRES_REVIEW│             │
│  │ Audit: 암시적           │      │ Audit: 명시적 기록      │             │
│  └────────────────────────┘      └────────────────────────┘             │
│                                                                          │
│                  요청 실패                                                │
│                      │                                                   │
│                      ▼                                                   │
│              ┌───────────────┐                                           │
│              │  RetryHandler  │ ← 동일 프로세스에서 즉시 재시도           │
│              │  (1-3회 시도)   │   exponential backoff 적용              │
│              └───────┬───────┘                                           │
│                      │                                                   │
│           ┌─────────┴─────────┐                                         │
│           │                   │                                         │
│        [성공]             [3회 모두 실패]                                │
│           │                   │                                         │
│           ▼                   ▼                                         │
│        [완료]            ┌─────────┐                                     │
│                          │   DLQ   │ ← 영구 저장 (pending 상태)          │
│                          │  저장   │   log_dlq_store_audit() 호출       │
│                          └────┬────┘                                     │
│                               │                                         │
│                               ▼                                         │
│                      ┌───────────────┐                                   │
│                      │ ReplayService │ ← Beat(5분)/CB복구/수동 트리거    │
│                      │  (1-5회 시도)  │   log_dlq_replay_audit() 호출   │
│                      └───────┬───────┘                                   │
│                              │                                           │
│                  ┌───────────┴───────────┐                               │
│                  │                       │                               │
│             [Replay 성공]          [5회 모두 실패]                        │
│                  │                       │                               │
│                  ▼                       ▼                               │
│          [상태: resolved]      [상태: requires_review]                   │
│                                 (수동 개입 필요)                          │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**자동화 범위:**
| 단계 | 현재 상태 | 자동화 필요? | 비고 |
|-----|---------|------------|------|
| Retry (즉시) | ✅ 자동 | ❌ | `RetryHandler.execute()` |
| DLQ 저장 | ✅ 자동 | ❌ | Retry 소진 시 자동 저장 |
| Replay (Beat) | ✅ 자동 | ❌ | 5분마다 `replay_failed_operations` |
| Replay (CB 복구) | ⚠️ 수동 | ✅ | **Track 1 자동화 대상** |
| Replay (트래픽) | ❌ 없음 | ✅ | **Track 3 신규 구현** |

### 1.1 현재 상태 (Semi-Automatic)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                      현재 DLQ Replay 흐름                                 │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  요청 실패 ────────────────────────────────────────> DLQ 자동 저장       │
│      │                                                    (자동)         │
│      ▼                                                                   │
│  Beat Schedule ───(5분 주기)───> replay_batch() ────> DLQ Replay        │
│      │                               (자동)              (자동)          │
│      ▼                                                                   │
│  CB CLOSED ───────────────────────────────────────> Replay 트리거       │
│      │                                                    (수동!)        │
│      │                                                                   │
│      └─── trigger_replay=False (기본값)                                 │
│           운영자가 force_close(trigger_replay=True) 호출 필요            │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.2 목표 상태 (Fully Automatic)

```
┌──────────────────────────────────────────────────────────────────────────┐
│                     자동화된 DLQ Replay 흐름                              │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────────┐ │
│  │                         9 Layer Safety                              │ │
│  │  Kill Switch → Emergency → Error Budget → Rate Limit →             │ │
│  │  Atomic Acquisition → Max Retry → Internal Protections             │ │
│  └─────────────────────────────────────────────────────────────────────┘ │
│                              ▲                                           │
│                              │ (모든 작업 전 체크)                        │
│                              │                                           │
│  ╔═══════════════════════════╧═══════════════════════════════════════╗  │
│  ║                                                                    ║  │
│  ║  Track 1: Event-Driven (CB CLOSED → 즉시 Replay)                  ║  │
│  ║      └─> EventBus.subscribe(CIRCUIT_BREAKER_CLOSED)               ║  │
│  ║                                                                    ║  │
│  ║  Track 2: Scheduled Batch (5분 Beat → 배치 Replay)                ║  │
│  ║      └─> 현재와 동일, 변경 없음                                   ║  │
│  ║                                                                    ║  │
│  ║  Track 3: Traffic-Aware (트래픽 정상화 시 자동 Replay)            ║  │
│  ║      └─> Health Monitor + Conditional Trigger                     ║  │
│  ║                                                                    ║  │
│  ╚════════════════════════════════════════════════════════════════════╝  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 안전 메커니즘 (9단계 보호)

### 2.0 Audit Trail (기록 시스템)

**모든 자동화 작업은 기록됩니다.** 현재 구현된 기록 메커니즘:

| 이벤트 | 함수/클래스 | 기록 내용 |
|-------|------------|----------|
| DLQ 저장 | `log_dlq_store_audit()` | id, domain, failure_type, error_message |
| Replay 결과 | `log_dlq_replay_audit()` | id, domain, success, actor_id, error_message |
| Replay 메트릭 | `ReplayEventHandler` | 시작/완료, 도메인, 성공여부, 소요시간 |
| CB 상태 변경 | `log_config_change()` | service, old_state, new_state |
| 거버넌스 차단 | `RequestAuditBuffer` | event_type, reason, blocked_by |

**기록 위치:**
```
┌──────────────────────────────────────────────────────────────────────────┐
│                         Audit Trail 저장소                                │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. 파일 로그: logs/audit.jsonl (FileAuditLogAdapter)                    │
│     → JSONL 형식, 해시 체인으로 무결성 보장                               │
│                                                                          │
│  2. Prometheus 메트릭:                                                   │
│     → selfhealing_replay_attempts_total{domain, replay_type}            │
│     → selfhealing_replay_outcomes_total{domain, outcome}                │
│     → selfhealing_replay_duration_seconds{domain}                       │
│                                                                          │
│  3. EventBus 이벤트:                                                     │
│     → DLQ_REPLAY_COMPLETED (구독자에게 알림)                             │
│     → DLQ_REPLAY_BLOCKED (차단 시 알림)                                  │
│                                                                          │
│  4. 표준 로그 (fallback):                                                │
│     → [DLQAudit] STORE | id=123 | domain=payment | failure_type=TIMEOUT │
│     → [DLQAudit] REPLAY_SUCCESS | id=123 | domain=payment               │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**코드 참조:**
- `services/audit_helpers.py`: `log_dlq_store_audit()`, `log_dlq_replay_audit()`
- `metrics/event_handlers.py`: `ReplayEventHandler.on_replay_completed()`
- `audit/event_buffer.py`: `AuditEventType.DLQ_STORE`, `AuditEventType.DLQ_REPLAY`

### 2.1 6단계 메인 안전장치

| 순서 | 이름 | 파일 | 동작 |
|-----|------|------|------|
| 1 | Kill Switch | `system_control.py` | `is_enabled() == False` → 전체 차단 |
| 2 | Emergency Mode | `emergency_mode/enums.py` | `LEVEL_2+` → 자동화 차단 |
| 3 | Error Budget Gate | `error_budget_gate/gate.py` | `budget < 5%` → 차단 |
| 4 | Rate Limit Coordinator | `rate_limit_coordinator.py` | 429 감지 → 글로벌 쿨다운 |
| 5 | Atomic Acquisition | `dlq.py` | `try_acquire_for_replay()` 동시성 방지 |
| 6 | Max Retry Limit | `dlq.py` | `retry_count >= max_retries` → REQUIRES_REVIEW |

### 2.2 3단계 내부 보호 (ErrorBudgetGate)

| 순서 | 이름 | 동작 |
|-----|------|------|
| 7 | Fail-Open Rate Limiter | Gate 과부하 시 안전한 방향으로 통과 |
| 8 | Gate Fault Detector | Gate 장애 시 보수적으로 차단 |
| 9 | Alert Manager | 반복 차단 시 알림 발송 |

### 2.3 안전 메커니즘 동작 원리

```python
# governance_checks.py - check_all_governance() 구현
def check_all_governance() -> GovernanceResult:
    """
    모든 거버넌스 체크를 순차적으로 수행.
    첫 번째 실패 시 조기 반환 (Early Return).
    """
    # 1. Kill Switch (마지막 수단이지만 먼저 체크)
    if not SystemControlManager.is_enabled():
        return GovernanceResult(passed=False, reason="System disabled (Kill Switch)")
    
    # 2. Emergency Mode
    level = get_emergency_level()
    if level >= EmergencyLevel.LEVEL_2:
        return GovernanceResult(passed=False, reason=f"Emergency mode: {level.name}")
    
    # 3. Error Budget Gate
    gate = get_error_budget_gate()
    if not gate.is_replay_allowed():
        return GovernanceResult(passed=False, reason="Error budget insufficient")
    
    # 4. Rate Limit Check (Self-DDoS Prevention)
    if RateLimitCoordinator.is_in_cooldown():
        return GovernanceResult(passed=False, reason="Global rate limit cooldown")
    
    return GovernanceResult(passed=True)
```

> **핵심 통찰**: 코드에서 Kill Switch가 먼저 체크되지만, 이것은 "사용 빈도"가 아닌 "체크 순서"입니다.  
> Kill Switch는 운영자가 수동으로 활성화하는 최후의 수단이며, 나머지 8개는 정책 기반 자동 방어입니다.

---

## 3. Track 1: Event-Driven Replay (CB CLOSED)

### 3.1 현재 상태

```python
# services/event_bus.py - 현재 핸들러 (로깅만 수행)
def _on_circuit_breaker_closed(event: SelfHealingEvent):
    """CB 복구 시 조건부 Replay 트리거."""
    service_name = event.data.get("service_name", "unknown")
    
    logger.info(
        f"[EventHandler] Circuit breaker closed for {service_name}, "
        f"conditional replay may be triggered"  # ← "may be" = 실제로 안함
    )
```

### 3.2 구현 방안

```python
# services/event_bus.py - 수정 후
def _on_circuit_breaker_closed(event: SelfHealingEvent):
    """CB 복구 시 자동 Replay 트리거."""
    service_name = event.data.get("service_name", "unknown")
    
    # RuntimeConfig에서 자동 replay 설정 확인
    config = get_replay_automation_config()
    
    if not config.get("track1_enabled", True):
        logger.info(f"[EventHandler] Track 1 disabled, skipping replay for {service_name}")
        return
    
    max_items = config.get("track1_max_items", 50)
    
    try:
        from selfhealing.adapters.celery.tasks import conditional_replay_on_circuit_close
        conditional_replay_on_circuit_close.delay(
            service_name=service_name,
            max_items=max_items,
        )
        logger.info(f"[EventHandler] Triggered Track 1 replay for {service_name}")
    except Exception as e:
        logger.error(f"[EventHandler] Failed to trigger replay: {e}")
```

### 3.3 구현 난이도: 🟢 쉬움

**변경 필요 파일**:
- `services/event_bus.py`: 핸들러 수정
- `services/runtime_config/base.py`: `replay_automation` config 타입 추가

---

## 4. Track 2: Scheduled Batch Replay (현재 유지)

### 4.1 현재 구현

```python
# adapters/celery/beat_schedule.py
SELFHEALING_BEAT_SCHEDULE = {
    "replay-failed-operations": {
        "task": "selfhealing.adapters.celery.tasks.replay_failed_operations",
        "schedule": crontab(minute="*/5"),  # 5분마다
        "options": {"queue": "selfhealing"},
    },
}
```

### 4.2 변경 사항: 없음

Track 2는 현재 상태 그대로 유지. 이미 안전하게 작동 중.

---

## 5. Track 3: Traffic-Aware Replay (신규)

### 5.1 설계

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Track 3: Traffic-Aware Replay                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│   DLQ Entry (PENDING) ──── 대기 중 ────────────────────────────────────> │
│                                                                          │
│   ┌─────────────────────────────────────────────────────────────────┐   │
│   │                  Traffic Health Monitor                         │   │
│   │                  (1분마다 Beat Task 실행)                        │   │
│   │                                                                 │   │
│   │  Check 1: Circuit Breaker State == CLOSED?                     │   │
│   │  Check 2: Error Rate < threshold (5%)?                         │   │
│   │  Check 3: Response Time < SLA Warning (200ms)?                 │   │
│   │  Check 4: Error Budget > critical_threshold (20%)?             │   │
│   │                                                                 │   │
│   └─────────────────────────────────────────────────────────────────┘   │
│                              │                                           │
│                         모두 통과?                                       │
│                         /        \                                      │
│                       Yes         No                                    │
│                        │           │                                    │
│                        ▼           ▼                                    │
│                 Replay 시작    다음 주기까지 대기                         │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2 구현

```python
# tasks/traffic_aware_replay.py (신규 파일)

from dataclasses import dataclass
from celery import shared_task

@dataclass
class TrafficHealthStatus:
    is_healthy: bool
    reason: str
    checks: dict[str, bool] = None


def check_traffic_health(domain: str = None) -> TrafficHealthStatus:
    """트래픽 건강 상태 확인."""
    checks = {}
    
    # Check 1: Circuit Breaker State
    if domain:
        from selfhealing.services.circuit_breaker import get_circuit_breaker_service
        cb = get_circuit_breaker_service()
        cb_state = cb.get_state(domain)
        checks["circuit_breaker"] = cb_state.state == "closed"
        if not checks["circuit_breaker"]:
            return TrafficHealthStatus(
                is_healthy=False,
                reason=f"Circuit breaker is {cb_state.state}",
                checks=checks,
            )
    
    # Check 2: Error Budget
    from selfhealing.services.error_budget_gate import get_error_budget_gate
    gate = get_error_budget_gate()
    checks["error_budget"] = gate.is_replay_allowed()
    if not checks["error_budget"]:
        return TrafficHealthStatus(
            is_healthy=False,
            reason="Error budget insufficient",
            checks=checks,
        )
    
    # Check 3: Governance
    from selfhealing.services.governance_checks import check_all_governance
    governance = check_all_governance()
    checks["governance"] = governance.passed
    if not checks["governance"]:
        return TrafficHealthStatus(
            is_healthy=False,
            reason=governance.reason,
            checks=checks,
        )
    
    return TrafficHealthStatus(
        is_healthy=True,
        reason="All checks passed",
        checks=checks,
    )


@shared_task(name="selfhealing.tasks.traffic_aware_replay")
def traffic_aware_replay(domain: str = None, max_items: int = None):
    """
    트래픽 상태가 정상일 때만 DLQ Replay 수행.
    
    Beat Schedule: 매 1분마다 체크
    """
    # RuntimeConfig에서 설정 로드
    config = get_replay_automation_config()
    
    if not config.get("track3_enabled", False):
        return {"status": "disabled", "reason": "Track 3 is disabled"}
    
    effective_max_items = max_items or config.get("track3_max_items", 30)
    
    # Traffic Health Check
    health_status = check_traffic_health(domain)
    
    if not health_status.is_healthy:
        logger.info(
            f"[TrafficAwareReplay] Skipping - traffic unhealthy: "
            f"{health_status.reason}"
        )
        return {
            "status": "skipped",
            "reason": health_status.reason,
            "checks": health_status.checks,
        }
    
    # Replay 실행
    from selfhealing.services.replay_service import get_replay_service
    service = get_replay_service()
    result = service.replay_batch(domain=domain, max_items=effective_max_items)
    
    return {
        "status": "completed",
        "total": result.total,
        "success": result.success_count,
        "failed": result.failed_count,
    }
```

### 5.3 Beat Schedule 추가

```python
# beat_schedule.py에 추가
SELFHEALING_BEAT_SCHEDULE["traffic-aware-replay"] = {
    "task": "selfhealing.tasks.traffic_aware_replay",
    "schedule": crontab(minute="*"),  # 매 1분
    "kwargs": {},  # RuntimeConfig에서 동적으로 로드
    "options": {"queue": "selfhealing"},
}
```

### 5.4 구현 난이도: 🟡 중간

**변경 필요 파일**:
- `tasks/traffic_aware_replay.py`: 신규 생성
- `adapters/celery/beat_schedule.py`: 스케줄 추가

---

## 6. Adaptive max_items (동적 조정)

### 6.1 기존 인프라 참고

`AdaptiveThrottle` (services/throttle/adaptive.py):
- Netflix Gradient 알고리즘 기반
- RTT 증가 시 limit 감소, RTT 감소 시 limit 증가

### 6.2 Adaptive Replay Manager 설계

```python
# services/adaptive_replay.py (신규 파일)

from dataclasses import dataclass, field
import threading
import time


@dataclass
class AdaptiveReplayConfig:
    """Adaptive Replay batch size configuration."""
    
    # Bounds
    min_items: int = 10
    max_items: int = 100
    initial_items: int = 50
    
    # Adjustment ratios
    decrease_ratio: float = 0.8   # 실패 시 20% 감소
    increase_step: int = 5        # 성공 시 5개 증가
    
    # Triggers
    failure_threshold: float = 0.2    # 20% 이상 실패 시 감소
    success_streak_required: int = 3  # 3연속 성공 시 증가


class AdaptiveReplayManager:
    """Dynamically adjusts replay batch size based on success rate."""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance
    
    def _init(self):
        self.config = AdaptiveReplayConfig()
        self._current_items = self.config.initial_items
        self._success_streak = 0
        self._history = []  # Recent batch results
        
    def get_current_max_items(self) -> int:
        """현재 권장 max_items 반환."""
        return self._current_items
    
    def record_batch_result(self, total: int, success: int, failures: int):
        """배치 결과 기록 및 max_items 조정."""
        if total == 0:
            return
        
        failure_rate = failures / total
        
        # Record history
        self._history.append({
            "timestamp": time.time(),
            "total": total,
            "success": success,
            "failures": failures,
            "failure_rate": failure_rate,
        })
        
        # Keep only recent history
        if len(self._history) > 100:
            self._history = self._history[-100:]
        
        # Adjust based on results
        if failure_rate >= self.config.failure_threshold:
            # Too many failures → reduce batch size
            new_items = int(self._current_items * self.config.decrease_ratio)
            self._current_items = max(self.config.min_items, new_items)
            self._success_streak = 0
            logger.info(
                f"[AdaptiveReplay] High failure rate ({failure_rate:.1%}), "
                f"reduced to {self._current_items} items"
            )
            
        elif failures == 0:
            # Perfect batch → count toward increase
            self._success_streak += 1
            if self._success_streak >= self.config.success_streak_required:
                new_items = self._current_items + self.config.increase_step
                self._current_items = min(self.config.max_items, new_items)
                self._success_streak = 0
                logger.info(
                    f"[AdaptiveReplay] {self.config.success_streak_required} consecutive successes, "
                    f"increased to {self._current_items} items"
                )
        else:
            # Some failures but below threshold
            self._success_streak = 0
    
    def get_stats(self) -> dict:
        """현재 상태 및 통계 반환."""
        return {
            "current_max_items": self._current_items,
            "success_streak": self._success_streak,
            "config": {
                "min_items": self.config.min_items,
                "max_items": self.config.max_items,
                "failure_threshold": self.config.failure_threshold,
            },
            "recent_batches": len(self._history),
        }


def get_adaptive_replay_manager() -> AdaptiveReplayManager:
    """Singleton getter."""
    return AdaptiveReplayManager()
```

### 6.3 통합 방법

```python
# replay_service.py 수정
def replay_batch(self, domain: str = None, max_items: int = None) -> BatchReplayResult:
    # Adaptive 모드 확인
    config = get_replay_automation_config()
    
    if config.get("adaptive_enabled", False):
        from selfhealing.services.adaptive_replay import get_adaptive_replay_manager
        manager = get_adaptive_replay_manager()
        effective_max_items = manager.get_current_max_items()
    else:
        effective_max_items = max_items or config.get("default_max_items", 50)
    
    # ... 기존 replay 로직 ...
    
    # 결과 기록 (Adaptive 모드일 때만)
    if config.get("adaptive_enabled", False):
        manager.record_batch_result(
            total=result.total,
            success=result.success_count,
            failures=result.failed_count,
        )
    
    return result
```

---

## 7. 도메인별 차등 정책

### 7.1 기존 인프라

`domain_configs` (core/config.py#L548):
```python
@dataclass
class SelfHealingConfig:
    # ... 기본 설정 ...
    
    # Domain-specific overrides
    domain_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
```

### 7.2 Replay 정책 확장

> ⚠️ **아래는 예시입니다.** 도메인 이름은 자유롭게 정의할 수 있습니다.  
> `domain_configs`의 키는 어떤 문자열이든 사용 가능하며, 시스템에 종속되지 않습니다.

```yaml
# settings.py 또는 selfhealing_config.yaml

SELFHEALING_CONFIG:
  # 기본 정책 (모든 도메인에 적용)
  replay:
    default_max_items: 50
    adaptive_enabled: true
  
  # ──────────────────────────────────────────────────────────
  # 도메인별 차등 정책 (예시)
  # 
  # 도메인 이름은 사용자가 자유롭게 정의합니다.
  # 예: "external-api", "payment-gateway", "user-service", 
  #     "toss", "kakao", "naver", "internal-batch" 등
  # ──────────────────────────────────────────────────────────
  domain_configs:
    # 예시 1: 외부 결제 게이트웨이 (최고 중요도)
    "external-payment":
      priority: critical
      replay:
        on_circuit_close: true    # CB 복구 시 즉시 replay
        batch_priority: 1         # 배치에서 가장 먼저 처리
        max_retries: 10           # 기본 5 → 10
        backoff_multiplier: 0.5   # 더 짧은 대기
      retry:
        max_attempts: 5           # 기본 3 → 5
    
    # 예시 2: 내부 마이크로서비스 (중간 중요도)
    "internal-service-a":
      priority: normal
      replay:
        on_circuit_close: true
        batch_priority: 2
      # retry, max_retries 등은 기본값 사용
    
    # 예시 3: 비핵심 외부 API (낮은 중요도)
    "third-party-analytics":
      priority: low
      replay:
        on_circuit_close: false   # 배치 스케줄로만 처리
        batch_priority: 3         # 마지막에 처리
        max_retries: 3            # 기본 5 → 3
      retry:
        max_attempts: 2           # 기본 3 → 2
        backoff_base: 10          # 더 긴 대기
```

**도메인 정의 원칙:**
| 구분 | 설명 | 예시 |
|-----|------|------|
| 외부 서비스명 | 연동하는 외부 API 이름 | `toss`, `stripe`, `sendgrid` |
| 서비스 역할 | 기능 기반 명명 | `payment-gateway`, `email-sender` |
| 팀/조직 기반 | 담당 조직 기반 | `team-alpha-api`, `platform-core` |
| 혼합 방식 | 위 방식 조합 | `toss-payment`, `kakao-notification` |

### 7.3 우선순위 기반 배치 처리

```python
def get_entries_by_priority(limit: int) -> list[DLQEntry]:
    """도메인 우선순위에 따라 정렬된 DLQ 항목 조회."""
    
    config = get_config()
    
    # 우선순위별 도메인 그룹화
    priority_groups = {
        "critical": [],
        "normal": [],
        "low": [],
    }
    
    for domain, domain_config in config.domain_configs.items():
        priority = domain_config.get("priority", "normal")
        priority_groups[priority].append(domain)
    
    # 우선순위 순서대로 조회
    all_entries = []
    remaining = limit
    
    for priority in ["critical", "normal", "low"]:
        if remaining <= 0:
            break
            
        for domain in priority_groups[priority]:
            if remaining <= 0:
                break
            entries = repository.get_pending(domain=domain, limit=remaining)
            all_entries.extend(entries)
            remaining -= len(entries)
    
    return all_entries
```

---

## 8. 런타임 설정 API (RuntimeConfig 통합)

### 8.1 기존 인프라

**ApplyStrategy** (core/apply_strategy.py):
```python
class ApplyStrategy(Enum):
    IMMEDIATE = "immediate"   # 즉시 적용
    DELAYED = "delayed"       # N초 후 적용 (취소 가능)
    GRACEFUL = "graceful"     # 진행 중인 작업 완료 후 적용
```

**RuntimeConfigManager** (services/runtime_config/):
- `update_with_strategy()`: 전략 기반 설정 변경
- `get_pending_changes()`: 대기 중인 변경 조회
- `cancel_pending_change()`: 대기 중인 변경 취소

**Config API** (api/django/views/config.py):
- `GET /api/self-healing/config/{type}/`: 설정 조회
- `PUT /api/self-healing/config/{type}/`: 설정 변경 (strategy 지원)
- `GET /api/self-healing/config/pending/`: 대기 중인 변경 목록
- `POST /api/self-healing/config/pending/{id}/cancel/`: 변경 취소

### 8.2 Replay Automation Config 추가

```python
# services/runtime_config/base.py에 추가

REPLAY_AUTOMATION_DEFAULTS = {
    # Track 1: Event-Driven
    "track1_enabled": True,
    "track1_max_items": 50,
    
    # Track 2: Scheduled (기존)
    "track2_enabled": True,
    "track2_max_items": 50,
    
    # Track 3: Traffic-Aware
    "track3_enabled": False,  # 기본값: 비활성화
    "track3_max_items": 30,
    "track3_health_checks": ["circuit_breaker", "error_budget", "governance"],
    
    # Adaptive Mode
    "adaptive_enabled": False,
    "adaptive_min_items": 10,
    "adaptive_max_items": 100,
    "adaptive_failure_threshold": 0.2,
    
    # Domain Priorities
    "domain_priorities": {
        "payment": "critical",
        "inventory": "normal",
        "notification": "low",
    },
}
```

### 8.3 새로운 API 엔드포인트

```python
# api/django/views/config.py에 추가

class ReplayAutomationConfigView(BaseConfigView):
    """
    Replay Automation Configuration API.
    
    GET: 현재 설정 조회
    PUT: 설정 변경 (strategy 지원: immediate, delayed, graceful)
    
    Example PUT request:
    {
        "track1_enabled": true,
        "track3_enabled": true,
        "adaptive_enabled": true,
        "apply_strategy": "delayed",
        "delay_seconds": 30
    }
    """
    serializer_class = ReplayAutomationConfigSerializer
    config_name = "replay_automation"
```

### 8.4 API URL 추가

```python
# urls.py에 추가
path(
    "config/replay-automation/",
    ReplayAutomationConfigView.as_view(),
    name="config-replay-automation",
),
```

### 8.5 Serializer 구현

```python
# api/django/serializers.py에 추가

class ReplayAutomationConfigSerializer(BaseConfigSerializer):
    """Replay Automation configuration serializer with validation."""
    
    # Track 1
    track1_enabled = serializers.BooleanField(required=False)
    track1_max_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=500,
    )
    
    # Track 3
    track3_enabled = serializers.BooleanField(required=False)
    track3_max_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=200,
    )
    
    # Adaptive
    adaptive_enabled = serializers.BooleanField(required=False)
    adaptive_min_items = serializers.IntegerField(
        required=False,
        min_value=1,
        max_value=50,
    )
    adaptive_max_items = serializers.IntegerField(
        required=False,
        min_value=10,
        max_value=500,
    )
    adaptive_failure_threshold = serializers.FloatField(
        required=False,
        min_value=0.05,
        max_value=0.5,
    )
    
    # Apply Strategy (from BaseConfigSerializer)
    apply_strategy = serializers.ChoiceField(
        choices=["immediate", "delayed", "graceful"],
        required=False,
        default="immediate",
    )
    delay_seconds = serializers.IntegerField(
        required=False,
        min_value=5,
        max_value=3600,
    )
    reason = serializers.CharField(required=False, max_length=500)
```

---

## 9. 적용 전략 상세

### 9.1 기존 구현 (코드 기반)

**config_apply.py**:
```python
@shared_task(name="selfhealing.apply_graceful_config_change")
def apply_graceful_config_change(self, pending_id: str, max_wait_seconds: int = 60):
    """
    Apply a configuration change gracefully.
    
    Waits for in-progress operations to complete before applying.
    """
    service = get_config_apply_service()
    result = service.apply_graceful_change(pending_id, max_wait_seconds)
    
    if result.get("status") == "retry":
        # 진행 중인 작업이 있으면 재시도
        raise self.retry(countdown=min(5 * (self.request.retries + 1), 30))
```

### 9.2 적용 시나리오

| 전략 | 사용 시나리오 | 동작 |
|-----|-------------|------|
| `immediate` | 긴급 변경, 문제 해결 | 즉시 적용 |
| `delayed` | 계획된 변경, 검토 시간 필요 | N초 후 적용, 취소 가능 |
| `graceful` | 운영 중 안전 변경 | 진행 중인 작업 완료 대기 후 적용 |

### 9.3 API 사용 예시

```bash
# 1. 즉시 적용
curl -X PUT /api/self-healing/config/replay-automation/ \
  -H "Content-Type: application/json" \
  -d '{
    "track3_enabled": true,
    "apply_strategy": "immediate"
  }'

# 2. 30초 후 적용 (취소 가능)
curl -X PUT /api/self-healing/config/replay-automation/ \
  -H "Content-Type: application/json" \
  -d '{
    "adaptive_enabled": true,
    "apply_strategy": "delayed",
    "delay_seconds": 30,
    "reason": "테스트 후 적용 예정"
  }'

# 3. Graceful 적용 (진행 중인 replay 완료 후)
curl -X PUT /api/self-healing/config/replay-automation/ \
  -H "Content-Type: application/json" \
  -d '{
    "track1_max_items": 100,
    "apply_strategy": "graceful",
    "reason": "운영 중 안전 변경"
  }'

# 4. 대기 중인 변경 조회
curl /api/self-healing/config/pending/?config_type=replay_automation

# 5. 대기 중인 변경 취소
curl -X POST /api/self-healing/config/pending/{pending_id}/cancel/
```

---

## 10. 구현 로드맵

### Phase 1: Track 1 활성화 ✅ 완료 (2026-01-04)

| 작업 | 파일 | 변경 내용 | 상태 |
|-----|------|----------|------|
| 1 | `core/config.py` | `ReplayAutomationConfig` dataclass 추가 | ✅ |
| 2 | `services/runtime_config/constants.py` | STORAGE_KEY, CONFIG_CLASS 추가 | ✅ |
| 3 | `services/event_bus.py` | `_on_circuit_breaker_closed` 핸들러 수정 | ✅ |
| 4 | `api/django/serializers/config.py` | `ReplayAutomationConfigSerializer` 추가 | ✅ |
| 5 | `api/django/views/config.py` | `ReplayAutomationConfigView` 추가 | ✅ |
| 6 | `api/django/urls.py` | URL 라우트 추가 (`/config/replay-automation/`) | ✅ |

**구현 상세:**
- `ReplayAutomationConfig`: Track 1/2/3 및 Adaptive 모드 설정을 위한 dataclass
- CB CLOSED 이벤트 시 RuntimeConfig에서 `track1_enabled` 확인 후 자동 replay 트리거
- API를 통한 런타임 설정 변경 지원 (immediate/delayed/graceful 전략)

**테스트:** `tests/self_healing/unit/test_replay_automation_phase1.py` - 17개 테스트 통과

### Phase 2: Track 3 구현 ✅ 완료 (2026-01-04)

| 작업 | 파일 | 변경 내용 | 상태 |
|-----|------|----------|------|
| 1 | `tasks/traffic_aware_replay.py` | 신규 생성 | ✅ |
| 2 | `adapters/celery/beat_schedule.py` | 스케줄 추가 (include_traffic_aware 파라미터) | ✅ |
| 3 | `tasks/__init__.py` | exports 추가 | ✅ |

**구현 상세:**
- `TrafficHealthStatus`: 트래픽 건강 상태 결과 dataclass
- `check_traffic_health()`: CB 상태, Error Budget, Governance 체크 함수
- `TrafficAwareReplayTask`: 매 1분마다 트래픽 정상 시 DLQ Replay 수행
- Beat Schedule: `traffic-aware-replay` (매 1분, dlq 큐)

**Health Checks:**
1. Circuit Breaker State == CLOSED (도메인 지정 시)
2. Error Budget > critical_threshold
3. Governance 체크 통과 (Kill Switch, Emergency Mode)

**테스트:** `tests/self_healing/unit/test_replay_automation_phase2.py` - 25개 테스트 통과

### Phase 3: Adaptive max_items (2-3일)

| 작업 | 파일 | 변경 내용 |
|-----|------|----------|
| 1 | `services/adaptive_replay.py` | 신규 생성 |
| 2 | `services/replay_service.py` | Adaptive 통합 |

### Phase 4: 도메인 정책 (1-2일)

| 작업 | 파일 | 변경 내용 |
|-----|------|----------|
| 1 | `services/replay_service.py` | 우선순위 기반 조회 |
| 2 | `core/config.py` | 도메인 정책 스키마 확장 |

---

## 11. 모니터링 및 검증

### 11.1 Grafana 대시보드 추가 메트릭

```python
# 추가할 메트릭
selfhealing_replay_track1_triggered_total
selfhealing_replay_track2_triggered_total
selfhealing_replay_track3_triggered_total
selfhealing_replay_track3_skipped_total{reason="..."}
selfhealing_replay_adaptive_current_max_items
selfhealing_replay_domain_priority_order
```

### 11.2 알림 규칙

```yaml
# Track 3 연속 스킵 알림
- alert: Track3ReplayConsecutiveSkips
  expr: increase(selfhealing_replay_track3_skipped_total[15m]) > 10
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "Track 3 replay가 15분간 10회 이상 스킵됨"

# Adaptive max_items 급감 알림
- alert: AdaptiveMaxItemsLow
  expr: selfhealing_replay_adaptive_current_max_items < 20
  for: 10m
  labels:
    severity: warning
  annotations:
    summary: "Adaptive replay max_items가 20 미만으로 감소"
```

### 11.3 알림 시스템 연동 (Notification Integration)

**기존 알림 인프라 재사용** - 별도 구현 불필요

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   알림 시스템 아키텍처 (이미 구현됨)                        │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │      DLQ Replay Automation (Track 1, 2, 3)                        │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                              ▼                                           │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │         SecurityNotificationService.send_alert()                   │  │
│  │         services/security_notification_service.py                  │  │
│  │                                                                    │  │
│  │  severity="critical" → Slack + Email + SMS + PagerDuty            │  │
│  │  severity="warning"  → Slack + Email                              │  │
│  │  severity="info"     → Slack only                                 │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                              │                                           │
│                  ┌───────────┼───────────┬───────────┐                  │
│                  ▼           ▼           ▼           ▼                  │
│             ┌────────┐  ┌────────┐  ┌────────┐  ┌──────────┐            │
│             │ Slack  │  │ Email  │  │  SMS   │  │PagerDuty │            │
│             │        │  │        │  │        │  │          │            │
│             │ (외부) │  │ (SMTP) │  │ (외부) │  │  (외부)  │            │
│             └────────┘  └────────┘  └────────┘  └──────────┘            │
│                  ▲           ▲           ▲           ▲                  │
│                  │           │           │           │                  │
│                  └───────────┴───────────┴───────────┘                  │
│                              │                                           │
│                    ⚠️ 사용자가 직접 설정 필요                             │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

#### 11.3.1 사용자 설정 필요 항목 (외부 시스템 연결)

| 채널 | 설정 항목 | 설정 방법 | 필수 여부 |
|-----|----------|----------|----------|
| **Slack** | `slack_webhook_url` | Slack App → Incoming Webhooks → URL 생성 | ⚠️ 권장 |
| **Email** | Django SMTP 설정 | `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER` 등 | 선택 |
| **SMS** | 외부 SMS API 연동 | 사용자 직접 구현 필요 | 선택 |
| **PagerDuty** | `pagerduty_service_key` | PagerDuty → Service → Integration Key | 선택 |

#### 11.3.2 설정 방법

**방법 1: Django settings.py (권장)**

```python
# myproject/settings/production.py

SELFHEALING_NOTIFICATION = {
    # Slack 설정 (가장 기본적인 알림 채널)
    "slack_webhook_url": os.environ.get("SLACK_WEBHOOK_URL", ""),
    "critical_channel": "#critical-alerts",
    "high_channel": "#ops-alerts",
    "medium_channel": "#dev-alerts",
    
    # Email 설정 (Django SMTP 설정 사용)
    "email_critical_recipients": ["oncall@company.com"],
    "email_high_recipients": ["ops-team@company.com"],
    
    # SMS 설정 (critical only)
    "sms_critical_recipients": ["+82-10-xxxx-xxxx"],
    
    # PagerDuty 설정
    "pagerduty_service_key": os.environ.get("PAGERDUTY_SERVICE_KEY", ""),
    "pagerduty_enabled": True,
    
    # 일반 설정
    "enabled": True,
    "dry_run": False,  # True면 실제 발송 없이 로그만
}
```

**방법 2: SelfHealingConfig 직접 설정**

```python
# core/config.py의 NotificationConfig 활용

from selfhealing.core.config import NotificationConfig, SelfHealingConfig

config = SelfHealingConfig(
    notification=NotificationConfig(
        enabled=True,
        channels=["slack", "email"],
        critical_channel="#critical-alerts",
        high_channel="#ops-alerts",
        medium_channel="#dev-alerts",
    )
)
```

**방법 3: 환경변수**

```bash
# .env 파일

# Slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T00000/B00000/XXXXXXX

# PagerDuty
PAGERDUTY_SERVICE_KEY=your-pagerduty-integration-key
PAGERDUTY_ENABLED=true

# 알림 제한
SELFHEALING_NOTIFICATION_TIMEOUT=10
SELFHEALING_SLACK_BLOCK_TEXT_LIMIT=3000
```

#### 11.3.3 DLQ 자동화에서 알림 발송 (코드 예시)

```python
# Track 1, 3에서 알림 발송 시 사용
from selfhealing.services.security_notification_service import (
    get_security_notification_service,
)

def send_replay_notification(
    title: str,
    message: str,
    severity: str = "info",  # "info", "warning", "critical"
    domain: str = None,
    count: int = 0,
):
    """DLQ Replay 관련 알림 발송."""
    service = get_security_notification_service()
    
    service.send_alert(
        title=title,
        message=message,
        severity=severity,
        channels=["slack"],  # 또는 ["slack", "email"]
        metadata={
            "domain": domain,
            "count": count,
            "source": "dlq_automation",
        },
    )

# 사용 예시
send_replay_notification(
    title="[DLQ] Track 1 - CB 복구 후 자동 Replay 시작",
    message=f"도메인: external-payment, 대상: 25건",
    severity="info",
    domain="external-payment",
    count=25,
)
```

#### 11.3.4 이미 연동된 컴포넌트

다음 컴포넌트들은 이미 `SecurityNotificationService`를 사용 중:

| 컴포넌트 | 파일 위치 | 연동 방식 |
|---------|----------|----------|
| `GateAlertManager` | `services/error_budget_gate/alert_manager.py` | Fail-Open, Rate Limit 초과 알림 |
| `BaseNotifyingTask` | `tasks/base.py` | Celery Task 실행 전/후 알림 |
| `UnifiedNotificationManager` | `services/unified_notification.py` | 통합 알림 라우팅 |
| `drift_detection` | `tasks/drift_detection.py` | SLA Drift 알림 |

#### 11.3.5 알림 미설정 시 동작

**Slack Webhook URL 미설정 시:**
```python
# security_notification_service.py L384-390
if not self.config.slack_webhook_url:
    return NotificationResult(
        channel="slack",
        success=False,
        error="Slack webhook URL not configured",
    )
```

→ 에러 로그만 남기고 시스템은 정상 작동 (Graceful Degradation)

**알림 전체 비활성화:**
```python
# dry_run=True 설정 시: 로그만 출력, 실제 발송 안 함
# enabled=False 설정 시: 알림 로직 자체 스킵
```

---

## 12. 결론

### 12.1 핵심 요약

| 항목 | 현재 | 목표 | 방법 | 상태 |
|-----|------|------|------|------|
| CB 복구 시 Replay | 수동 | 자동 | EventBus 핸들러 수정 | ✅ Phase 1 완료 |
| RuntimeConfig API | 없음 | 있음 | ReplayAutomationConfig | ✅ Phase 1 완료 |
| 트래픽 상태 인식 | 없음 | 있음 | Traffic Health Monitor | ✅ Phase 2 완료 |
| max_items | 고정 | 동적 | AdaptiveReplayManager | ⏳ Phase 3 |
| 도메인 정책 | 일괄 | 차등 | domain_configs 활용 | ⏳ Phase 4 |

### 12.2 안전성 보장

**9단계 안전 메커니즘이 이미 존재**하며, 모든 자동화 작업에 선행 적용됩니다.

> 모든 9개 안전장치가 동시에 실패해야만 시스템 폭주가 발생하며,  
> 그 경우에도 Kill Switch (수동)로 즉시 차단 가능합니다.

### 12.3 다음 단계

1. ✅ 문서 작성 완료
2. ✅ Phase 1 구현 완료 (Track 1 활성화 + API 엔드포인트)
   - `ReplayAutomationConfig` dataclass
   - `_on_circuit_breaker_closed` 핸들러 자동화
   - `/api/self-healing/config/replay-automation/` API
   - 17개 단위 테스트 통과
3. ✅ Phase 2 구현 완료 (Track 3 Traffic-Aware Replay)
   - `TrafficAwareReplayTask` 태스크 구현
   - `check_traffic_health()` 건강 체크 함수
   - Beat Schedule 통합 (매 1분마다)
   - 25개 단위 테스트 통과
4. ⏳ Phase 3: Adaptive max_items 구현
5. ⏳ Phase 4: 도메인별 차등 정책 구현
6. ⏳ Grafana 대시보드 업데이트

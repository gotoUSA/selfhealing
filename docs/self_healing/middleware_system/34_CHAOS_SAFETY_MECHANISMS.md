# 34. Chaos 실험 안전 메커니즘 상세 구현

> **작성일**: 2026-01-14  
> **상태**: 구현 계획  
> **관련 문서**: [33_CHAOS_INDUSTRY_EXPERIMENTS.md](33_CHAOS_INDUSTRY_EXPERIMENTS.md)

---

## 1. 개요

### 1.1 목적

이 문서는 33번 문서의 리뷰 피드백을 반영하여, Chaos 실험의 **안전성을 보장하는 메커니즘**을 상세히 정의합니다.

### 1.2 핵심 메커니즘

| # | 메커니즘 | 해결하는 문제 | 구현 상태 |
|---|----------|--------------|-----------|
| 1 | **Monotonic TTL** | Clock Skew 실험이 TTL 타이머를 교란하는 문제 | 🔴 구현 필요 |
| 2 | **가상 격리 (Virtual Isolation)** | ReplayFlood가 운영 DLQ를 오염시키는 문제 | ✅ 기반 구현 완료 |
| 3 | **Blast Radius 하드캡** | 실험이 통제 불능 상태가 되는 문제 | ✅ 기반 구현 완료 |
| 4 | **ContextVar 전파 무결성** | Celery/멀티스레드에서 컨텍스트 유실 | ✅ 이미 구현됨 |
| 5 | **Zombie Hunter** | 워커 크래시 시 고아 실험 방치 문제 | 🔴 구현 필요 |

---

## 2. Monotonic TTL 상세 구현

### 2.1 문제 정의

**시나리오**: `ClockSkewExperiment`가 시스템 시간을 100년 뒤로 돌림

**결과**: 
- `timezone.now()`가 2126년 반환
- TTL 체크 `now() > expires_at`가 항상 True
- 실험이 시작 즉시 만료로 판정되거나, 반대로 영원히 만료되지 않음

### 2.2 해결책: Monotonic Clock

`time.monotonic()`은 시스템 시간 변경에 영향받지 않는 **단조 증가 시계**입니다.

```python
# 기존 사용 증거: metrics/decorators.py:86-88
start_time = time.monotonic()
# ... 작업 수행 ...
duration = time.monotonic() - start_time  # 실제 경과 시간
```

### 2.3 구현 코드

#### 2.3.1 ChaosExperiment 베이스 클래스 확장

```python
# services/chaos/base.py 확장

import time

class ChaosExperiment(abc.ABC):
    """Base class for all chaos experiments."""
    
    # 기존 속성들...
    
    # === Monotonic TTL 관련 속성 ===
    _monotonic_start: float = 0.0
    _use_monotonic_ttl: bool = False  # 기본값: 기존 방식 사용
    
    def _start_monotonic_timer(self) -> None:
        """
        Monotonic clock 기반 TTL 타이머 시작.
        
        ClockSkewExperiment 등 시간 관련 실험에서 사용.
        time.monotonic()은 시스템 시간 변경에 영향받지 않음.
        """
        self._monotonic_start = time.monotonic()
        self._use_monotonic_ttl = True
        logger.debug(
            f"[ChaosExperiment] Monotonic timer started: {self._monotonic_start:.2f}"
        )
    
    def is_expired(self) -> bool:
        """
        Check if experiment has expired based on TTL.
        
        Monotonic TTL이 활성화된 경우 time.monotonic() 사용,
        그렇지 않으면 기존 timezone.now() 사용.
        """
        if self._use_monotonic_ttl:
            return self._is_expired_monotonic()
        
        # 기존 로직
        if self._expires_at is None:
            return False
        return now() > self._expires_at
    
    def _is_expired_monotonic(self) -> bool:
        """Monotonic clock 기반 TTL 만료 확인."""
        if self._monotonic_start == 0.0:
            return False
        elapsed = time.monotonic() - self._monotonic_start
        return elapsed >= self._effective_ttl
    
    def get_elapsed_monotonic(self) -> float:
        """Monotonic clock 기반 경과 시간 반환 (초)."""
        if self._monotonic_start == 0.0:
            return 0.0
        return time.monotonic() - self._monotonic_start
```

#### 2.3.2 ClockSkewExperiment 적용

```python
# services/chaos/experiment_impl.py

class ClockSkewExperiment(ChaosExperiment):
    """
    Simulate system clock skew/drift.
    
    SAFETY: Uses time.monotonic() for TTL to prevent recursive failure.
    """
    
    experiment_type = "clock_skew"
    requires_approval = True
    
    def inject_chaos(self) -> bool:
        """Inject clock skew with monotonic TTL protection."""
        # 핵심: Monotonic 타이머 활성화
        self._start_monotonic_timer()
        
        logger.warning(
            f"[ClockSkew] Skewing time by {self.skew_seconds}s "
            f"(TTL: {self._effective_ttl}s, protected by monotonic clock)"
        )
        
        # ... 나머지 로직
```

### 2.4 ChaosScheduler 적용

```python
# tasks/chaos_scheduler.py 확장

def _check_experiment_ttl(experiment: ChaosExperiment) -> bool:
    """
    실험 TTL 만료 확인 (Monotonic 지원).
    
    Returns:
        True if expired, False otherwise
    """
    if experiment._use_monotonic_ttl:
        # Monotonic clock 사용 (ClockSkewExperiment 등)
        elapsed = experiment.get_elapsed_monotonic()
        is_expired = elapsed >= experiment._effective_ttl
        
        if is_expired:
            logger.info(
                f"[ChaosScheduler] Experiment {experiment.experiment_id} "
                f"expired (monotonic elapsed: {elapsed:.2f}s)"
            )
        return is_expired
    
    # 기존 방식 (timezone.now() 사용)
    return experiment.is_expired()
```

---

## 3. 가상 격리 (Virtual Isolation) 상세 구현

### 3.1 문제 정의

**시나리오**: `ReplayFloodExperiment`가 5,000건의 DLQ 엔트리 생성

**결과**:
- 운영 DLQ 통계 오염 (Error Budget 계산 왜곡)
- SLA 지표 오염
- 실험 종료 후 잔여 데이터 존재

### 3.2 해결책: 3중 격리

1. **도메인 접두어**: `chaos_test:` prefix
2. **메타데이터 플래그**: `is_synthetic`, `is_chaos_experiment`
3. **자동 정리**: rollback 시 자동 purge

### 3.3 구현 코드

#### 3.3.1 격리 상수 정의

```python
# services/chaos/constants.py (신규 파일)

"""
Chaos Experiment Constants

안전 메커니즘 관련 상수 정의.
Reference: 34_CHAOS_SAFETY_MECHANISMS.md
"""

# === 가상 격리 (Virtual Isolation) ===
CHAOS_DOMAIN_PREFIX: str = "chaos_test:"
"""DLQ 도메인 접두어 - 실험 데이터 격리용"""

CHAOS_METADATA_FLAGS: dict = {
    "is_synthetic": True,
    "is_chaos_experiment": True,
}
"""DLQ 메타데이터 필수 플래그 - 통계/SLA에서 자동 제외"""

# === Blast Radius 하드캡 ===
class ExperimentHardCaps:
    """실험별 최대 허용 범위."""
    
    # SimulatedDiskIOExperiment
    DISK_IO_MAX_LATENCY_MS: int = 2000
    DISK_IO_MAX_FAILURE_RATE: float = 0.30
    
    # ReplayFloodExperiment
    REPLAY_FLOOD_MAX_ENTRIES: int = 5000
    REPLAY_FLOOD_MAX_RATE: int = 500
    
    # ClockSkewExperiment
    CLOCK_SKEW_MAX_SECONDS: int = 86400  # 최대 1일
    
    # NetworkBlackholeExperiment
    BLACKHOLE_MAX_DURATION_SECONDS: int = 300  # 최대 5분
```

#### 3.3.2 격리된 DLQ 저장 헬퍼

```python
# services/chaos/isolation_helpers.py (신규 파일)

"""
Chaos Experiment Isolation Helpers

가상 격리 패턴 구현 헬퍼 함수.
Reference: 34_CHAOS_SAFETY_MECHANISMS.md §3
"""

from typing import Any, Dict, Optional
import logging

from .constants import CHAOS_DOMAIN_PREFIX, CHAOS_METADATA_FLAGS

logger = logging.getLogger(__name__)


def get_isolated_domain(base_domain: str) -> str:
    """
    도메인에 chaos 접두어 추가.
    
    Args:
        base_domain: 원본 도메인 (예: "payment")
    
    Returns:
        격리된 도메인 (예: "chaos_test:payment")
    """
    if base_domain.startswith(CHAOS_DOMAIN_PREFIX):
        return base_domain
    return f"{CHAOS_DOMAIN_PREFIX}{base_domain}"


def get_isolation_metadata(
    experiment_id: str,
    experiment_type: str,
    additional: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    가상 격리 메타데이터 생성.
    
    Args:
        experiment_id: 실험 ID
        experiment_type: 실험 타입
        additional: 추가 메타데이터
    
    Returns:
        격리 플래그가 포함된 메타데이터
    """
    metadata = {
        **CHAOS_METADATA_FLAGS,
        "chaos_experiment_id": experiment_id,
        "chaos_experiment_type": experiment_type,
        "chaos_domain_prefix": CHAOS_DOMAIN_PREFIX,
    }
    
    if additional:
        metadata.update(additional)
    
    return metadata


def cleanup_chaos_entries(
    experiment_id: str,
    isolated_domain: str,
    max_entries: int = 10000,
) -> int:
    """
    실험 종료 후 카오스 엔트리 자동 정리.
    
    Args:
        experiment_id: 정리할 실험 ID
        isolated_domain: 격리된 도메인
        max_entries: 최대 정리 개수
    
    Returns:
        삭제된 엔트리 수
    """
    try:
        from selfhealing.services.dlq import get_dlq_service
        
        dlq = get_dlq_service()
        repo = dlq.repository
        
        # chaos_test: 도메인 엔트리 조회
        chaos_entries = repo.query(
            domain=isolated_domain,
            limit=max_entries,
        )
        
        purged_count = 0
        for entry in chaos_entries:
            # is_chaos_experiment 플래그 확인 (이중 검증)
            if entry.metadata and entry.metadata.get("is_chaos_experiment"):
                # 해당 실험 ID 확인
                if entry.metadata.get("chaos_experiment_id") == experiment_id:
                    repo.delete(entry.id)
                    purged_count += 1
        
        logger.info(
            f"[ChaosIsolation] Purged {purged_count} entries "
            f"for experiment {experiment_id}"
        )
        
        # === Self-Cleanup: FinOps 비용 환불 (리뷰 피드백) ===
        # "실험이 끝나면 자원만 치우는 게 아니라, 재무적 비용 지표까지도 정확히 원복시킨다"
        if purged_count > 0:
            _record_cleanup_cost_refund(experiment_id, purged_count)
        
        return purged_count
        
    except Exception as e:
        logger.error(f"[ChaosIsolation] Cleanup failed: {e}")
        return 0


def _record_cleanup_cost_refund(
    experiment_id: str,
    cleaned_entries: int,
) -> None:
    """
    Self-Cleanup: 정리된 엔트리에 대한 비용을 FinOps에 마이너스 기록.
    
    "우리는 실험이 끝나면 자원만 치우는 게 아니라, 
    재무적 비용 지표까지도 정확히 원복시킨다"
    
    Reference: FinOpsService.record_cost()는 Decimal 타입이므로 음수 기록 가능
    코드 근거: finops/service.py:97-130
    
    Args:
        experiment_id: 정리된 실험 ID
        cleaned_entries: 삭제된 엔트리 수
    """
    try:
        from decimal import Decimal
        from selfhealing.services.finops.service import FinOpsService
        
        finops = FinOpsService()
        
        # 엔트리당 비용 (DLQ 엔트리 생성 비용)
        per_entry_cost = finops._operation_costs.get("dlq_enqueue", Decimal("0.005"))
        refund_amount = per_entry_cost * cleaned_entries
        
        finops.record_cost(
            operation="chaos_cleanup_refund",
            stage_name=finops.CHAOS_BUDGET_STAGE_NAME,
            cost=-refund_amount,  # 마이너스 = 환불 (Self-Cleanup)
            success=True,
            metadata={
                "experiment_id": experiment_id,
                "cleaned_entries": cleaned_entries,
                "self_cleanup": True,
            },
        )
        logger.info(
            f"[FinOps] Self-Cleanup refund: -${refund_amount} "
            f"for {cleaned_entries} entries"
        )
    except Exception as e:
        logger.debug(f"[FinOps] Cleanup refund skipped: {e}")
```

#### 3.3.3 Error Budget 통합 (기존 코드 확인)

> **기존 구현 확인**: `chaos_context.py:220-240`의 `attach_chaos_context()` 함수에서 `is_chaos_experiment=True` 플래그 자동 설정

```python
# 기존 코드 (chaos_context.py:263-268)
def attach_chaos_context(
    operation: FailedOperationProtocol,
    context: ChaosExperimentContext,
) -> None:
    # ...
    # Add CHAOS flag indicator for easy filtering
    operation.metadata["is_chaos_experiment"] = True  # ← 이미 구현됨
```

---

## 4. ContextVar 전파 무결성

### 4.1 현재 상태: 이미 구현됨

프로젝트에서 ContextVar가 이미 광범위하게 사용되고 있습니다:

| ContextVar | 파일 | 용도 |
|------------|------|------|
| `_is_chaos_request` | `services/http_client.py:19` | Chaos 요청 플래그 |
| `_current_actor` | `context/actor_context.py:48` | 현재 작업자 정보 |
| `_trace_id_var` | `audit/trace.py:18` | 추적 ID |
| `_celery_context_var` | `audit/trace.py:214` | Celery 컨텍스트 |

### 4.2 Celery 워커 전파

```python
# audit/trace.py:250-267 (기존 구현)

def set_celery_context(
    task_id: str,
    task_name: str,
    retries: int = 0,
) -> None:
    """
    현재 Celery Task 컨텍스트를 설정합니다.
    
    task_prerun 시그널에서 호출되어 Task 실행 동안 유지됩니다.
    """
    context = {
        "task_id": task_id,
        "task_name": task_name,
        "retries": retries,
    }
    _celery_context_var.set(context)
    
    # trace_id도 함께 설정
    trace_id = generate_celery_trace_id(task_id)
    set_trace_id(trace_id)
```

### 4.3 Clock Skew ContextVar 확장

```python
# core/timezone.py 확장

from contextvars import ContextVar
from datetime import timedelta

# Clock Skew 시뮬레이션용 ContextVar (특정 요청에만 적용 가능)
_clock_skew_seconds: ContextVar[int] = ContextVar("clock_skew_seconds", default=0)


def set_clock_skew_for_request(skew_seconds: int) -> None:
    """현재 요청에만 Clock Skew 적용 (스레드 안전)."""
    _clock_skew_seconds.set(skew_seconds)


def clear_clock_skew_for_request() -> None:
    """현재 요청의 Clock Skew 해제."""
    _clock_skew_seconds.set(0)


def now() -> datetime:
    """현재 시간 반환 (카오스 실험 시 skew 적용)."""
    current_time = get_time_provider().now()
    
    # ContextVar 기반 skew (특정 요청에만 적용)
    skew = _clock_skew_seconds.get()
    if skew != 0:
        current_time = current_time + timedelta(seconds=skew)
        return current_time
    
    # 전역 카오스 설정 확인 (폴백)
    try:
        chaos_config = _get_current_chaos_config()
        clock_skew = chaos_config.get("clock_skew", {})
        
        if clock_skew.get("enabled"):
            skew_seconds = clock_skew.get("skew_seconds", 0)
            current_time = current_time + timedelta(seconds=skew_seconds)
    except Exception:
        pass
    
    return current_time
```

---

## 5. Zombie Hunter (고아 실험 정리)

### 5.1 문제 정의

**시나리오**: `DNSFailureExperiment` 시작 직후 Celery 워커 크래시

**결과**:
- TTL이 만료되어도 `rollback()`이 호출되지 않음
- DNS 장애가 **영구적으로 운영 환경에 남음**
- 실험 상태가 `RUNNING`으로 영원히 유지

### 5.2 해결책: Zombie Hunter 태스크

매 1분마다 `RUNNING` 상태 실험 중 TTL 만료된 것을 찾아 강제 정리:

```
┌─────────────────────────────────────────────────────────────┐
│                    Zombie Hunter Flow                        │
├─────────────────────────────────────────────────────────────┤
│  1. Celery Beat (1분 간격)                                    │
│         ↓                                                    │
│  2. RUNNING 상태 실험 조회                                    │
│         ↓                                                    │
│  3. TTL 만료 체크 (Monotonic 지원)                           │
│         ↓                                                    │
│  4. 분산 락 획득 (레이스 컨디션 방지)                        │
│         ↓                                                    │
│  5. 강제 rollback() + status → ABORTED                       │
│         ↓                                                    │
│  6. 락 해제 + 인스턴스 등록 해제                             │
└─────────────────────────────────────────────────────────────┘
```

### 5.3 분산 락 필요성

| 시나리오 | 락 없음 | 분산 락 사용 |
|---------|--------|-------------|
| 여러 스케줄러 동시 실행 | 같은 좀비를 중복 rollback 시도 | 한 스케줄러만 처리 |
| 성능 영향 | - | 1분에 1회 → **오버헤드 무시 가능** |
| 안정성 | 예측 불가능한 동작 | 확정적 동작 |

> **기존 분산 락 구현**: `IdempotencyService.acquire_lock()` 메서드 활용
> - Reference: `services/idempotency_service.py:279-283`

### 5.4 구현 코드

```python
# shopping/tasks/self_healing_tasks.py 추가

@shared_task(bind=True, name="chaos.hunt_zombie_experiments")
def hunt_zombie_experiments(self):
    """
    Zombie Hunter: 고아 실험 정리 태스크.
    
    Celery Beat: 매 1분마다 실행
    
    RUNNING 상태인데 TTL이 만료된 실험 = 워커 크래시로 간주
    → 분산 락 획득 후 강제 rollback → ABORTED 처리
    
    Reference: 34_CHAOS_SAFETY_MECHANISMS.md §5
    """
    from selfhealing.services.chaos import get_chaos_scheduler
    from selfhealing.services.chaos.base import ExperimentStatus
    from selfhealing.services.idempotency_service import (
        IdempotencyService,
        IdempotencyKey,
        IdempotencyDomain,  # Enum import
    )
    
    logger.info("[ZombieHunter] Starting zombie experiment hunt")
    
    scheduler = get_chaos_scheduler()
    idempotency = IdempotencyService()
    
    # RUNNING 상태 실험 조회
    running_experiments = scheduler.get_experiments_by_status(
        ExperimentStatus.RUNNING.value
    )
    
    hunted = 0
    skipped = 0
    errors = []
    
    for experiment in running_experiments:
        exp_id = getattr(experiment, 'experiment_id', 'unknown')
        
        try:
            # TTL 만료 체크 (Monotonic 지원)
            is_expired = False
            if hasattr(experiment, '_use_monotonic_ttl') and experiment._use_monotonic_ttl:
                is_expired = experiment._is_expired_monotonic()
            elif hasattr(experiment, 'is_expired'):
                is_expired = experiment.is_expired()
            
            if not is_expired:
                continue  # TTL 아직 유효 → 스킵
            
            # === 분산 락 획득 (레이스 컨디션 방지) ===
            lock_key = IdempotencyKey(
                domain=IdempotencyDomain.CHAOS_ZOMBIE_HUNTER,  # Enum 확장 필요
                key=f"zombie_rollback:{exp_id}",
                components={"experiment_id": exp_id},
            )
            
            if not idempotency.acquire_lock(lock_key, ttl_seconds=120):
                # 다른 스케줄러가 이미 처리 중
                skipped += 1
                logger.debug(f"[ZombieHunter] {exp_id} already being handled")
                continue
            
            try:
                logger.warning(f"[ZombieHunter] Zombie detected: {exp_id}")
                
                # 강제 rollback
                if hasattr(experiment, 'rollback'):
                    experiment.rollback()
                
                # 상태 변경
                experiment.status = ExperimentStatus.ABORTED
                
                # 스케줄러에서 등록 해제
                scheduler.unregister_experiment_instance(exp_id)
                
                hunted += 1
                logger.info(f"[ZombieHunter] Aborted zombie experiment {exp_id}")
                
            finally:
                # 락 해제
                idempotency.release_lock(lock_key)
                
        except Exception as e:
            logger.error(f"[ZombieHunter] Failed to abort {exp_id}: {e}")
            errors.append({"experiment_id": exp_id, "error": str(e)})
    
    result = {
        "success": True,
        "hunted": hunted,
        "skipped": skipped,
        "errors": errors,
    }
    
    if hunted > 0:
        logger.warning(f"[ZombieHunter] Hunted {hunted} zombie experiments")
    
    return result
```

### 5.5 Celery Beat 스케줄 추가

```python
# shopping/celery.py 또는 myproject/celery.py

app.conf.beat_schedule.update({
    "chaos-hunt-zombie-experiments": {
        "task": "chaos.hunt_zombie_experiments",
        "schedule": 60.0,  # 매 1분
        "options": {"queue": "chaos"},
    },
})
```

### 5.6 Fail-Safe 선언

> **"우리 Chaos 엔진은 엔진 자체가 고장 나더라도, 주입된 장애가 운영 환경에 남지 않도록 Fail-Safe Rollback을 보장한다."**

| 장애 시나리오 | 보호 메커니즘 | 결과 |
|--------------|-------------|------|
| 워커 크래시 | Zombie Hunter | 1분 내 자동 정리 |
| Clock Skew | Monotonic TTL | 실제 경과 시간으로 판정 |
| 데이터 오염 | Virtual Isolation | chaos_test: 도메인 격리 |
| 통제 불능 | Blast Radius 하드캡 | 상한선 강제 적용 |

---

## 6. 구현 순서

### Phase 1: 기반 인프라 (Day 1-2) ✅ 완료

| 순서 | 작업 | 파일 | 상태 |
|------|------|------|------|
| 1-1 | 상수 파일 생성 | `services/chaos/constants.py` | ✅ 완료 |
| 1-2 | 격리 헬퍼 생성 | `services/chaos/isolation_helpers.py` | ✅ 완료 |
| 1-3 | 패키지 __init__.py | `services/chaos/__init__.py` | ✅ 완료 |
| 1-4 | Monotonic TTL 메서드 추가 | `services/chaos/base.py` | 🔴 구현 필요 |

### Phase 2: Zombie Hunter 구현 (Day 3-4)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 2-1 | `CHAOS_ZOMBIE_HUNTER` 도메인 추가 | `services/idempotency_service.py` | 없음 |
| 2-2 | Zombie Hunter 태스크 | `shopping/tasks/self_healing_tasks.py` | Phase 2-1 |
| 2-3 | Celery Beat 스케줄 추가 | `shopping/celery.py` | Phase 2-2 |
| 2-4 | Self-Cleanup FinOps 환불 함수 | `services/chaos/isolation_helpers.py` | Phase 1-2 |

### Phase 3: 기존 코드 통합 (Day 5-6)

| 순서 | 작업 | 파일 | 의존성 |
|------|------|------|--------|
| 3-1 | ContextVar 확장 | `core/timezone.py` | Phase 1 완료 |
| 3-2 | ChaosScheduler TTL 체크 수정 | `services/chaos/scheduler.py` | Phase 1-4 |

### Phase 4: 실험 클래스 적용 (Day 7-9)

| 순서 | 작업 | 실험 | 의존성 |
|------|------|------|--------|
| 4-1 | ClockSkewExperiment (Monotonic TTL) | - | Phase 1-4 |
| 4-2 | ReplayFloodExperiment (가상 격리) | - | Phase 1-2 |
| 4-3 | SimulatedDiskIOExperiment (하드캡) | - | Phase 1-1 |

### Phase 5: 테스트 및 문서화 (Day 10-12)

| 순서 | 작업 | 파일 |
|------|------|------|
| 5-1 | Zombie Hunter 테스트 | `tests/self_healing/chaos/test_zombie_hunter.py` |
| 5-2 | 안전 메커니즘 통합 테스트 | `tests/self_healing/chaos/test_safety_mechanisms.py` |
| 5-3 | Celery 전파 테스트 | `tests/unit/selfhealing/test_chaos_contextvar_propagation.py` |
| 5-4 | 문서 업데이트 | 33, 34번 문서 |

---

## 7. 검증 체크리스트

### 7.1 Monotonic TTL

- [ ] ClockSkewExperiment가 시스템 시간을 100년 뒤로 돌려도 TTL 정상 작동
- [ ] ChaosScheduler가 Monotonic TTL 실험을 올바르게 만료 처리
- [ ] 롤백이 실제 경과 시간 기준으로 실행

### 7.2 Zombie Hunter

- [ ] 워커 크래시 후 1분 내 고아 실험 감지
- [ ] 분산 락으로 중복 rollback 방지
- [ ] ABORTED 상태로 정상 전환
- [ ] 스케줄러에서 인스턴스 등록 해제

### 7.3 가상 격리

- [ ] ReplayFlood 생성 엔트리가 `chaos_test:` 도메인 사용
- [ ] `is_chaos_experiment=True` 플래그 설정
- [ ] Error Budget 계산에서 자동 제외
- [ ] rollback 시 자동 정리 (purge)

### 7.4 ContextVar 전파

- [ ] Celery 워커에서 Clock Skew ContextVar 전파
- [ ] 멀티스레드 환경에서 컨텍스트 격리
- [ ] 요청 종료 시 자동 정리

---

## 관련 문서

| 문서 | 설명 |
|------|------|
| [33_CHAOS_INDUSTRY_EXPERIMENTS.md](33_CHAOS_INDUSTRY_EXPERIMENTS.md) | 업계 표준 실험 계획 |
| [32_CHAOS_SYSTEM_INTEGRATION.md](32_CHAOS_SYSTEM_INTEGRATION.md) | 힐링 시스템 연동 |
| [31_CHAOS_EXPERIMENT_EXPANSION.md](31_CHAOS_EXPERIMENT_EXPANSION.md) | 미구현 실험 타입 |

---

## 버전 정보

- **현재 버전**: 1.1.0
- **마지막 업데이트**: 2026-01-14
- **변경 이력**:
  - 1.1.0 (2026-01-14): Zombie Hunter 섹션 추가, 분산 락 포함, Phase 1 기반 코드 완료
  - 1.0.0 (2026-01-14): 초기 버전
- **담당자**: SelfHealing Team

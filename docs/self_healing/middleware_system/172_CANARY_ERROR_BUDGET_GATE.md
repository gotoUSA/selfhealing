# 172. Canary 자동 프로모션 Error Budget Gate 적용

> **작성일**: 2026-01-31
> **상태**: ✅ 구현 완료
> **구현일**: 2025-02-12
> **관련 문서**: [71_CANARY_CONFIG_ROLLOUT.md](71_CANARY_CONFIG_ROLLOUT.md), [12_ERROR_BUDGET.md](../12_ERROR_BUDGET.md)

---

## 1. 개요

### 1.1 문제 정의

현재 `canary_watchdog.auto_promote_eligible()` 함수는 글로벌 에러 예산 체크 없이 자동 프로모션을 수행합니다.

**현재 코드** (`tasks/canary_watchdog.py` Lines 405-455):
```python
def auto_promote_eligible(self) -> WatchdogResult:
    """
    자동 프로모션 조건을 충족한 롤아웃 프로모션.

    조건:
    - auto_promote=True인 단계
    - duration_minutes 경과
    - 메트릭 검증 통과  # ← Canary 자체 메트릭만 체크
    """
    result = WatchdogResult()

    if not self.config.enable_auto_promote:
        return result

    # ❌ check_all_governance() 또는 check_automation_allowed() 호출 없음

    for rollout in active_rollouts:
        # ...
        success = self.service.promote(rollout.id, force=False)
```

**`promote()` 내부 체크** (`services/canary/service.py` Lines 345-355):
```python
def promote(self, rollout_id: str, force: bool = False) -> bool:
    # ...
    if not force:
        metrics = self._collect_stage_metrics(rollout)
        is_healthy, failure_reason = self._is_stage_healthy(stage, metrics)
        # ✅ Canary 메트릭 체크 (로컬 뷰)
        # ❌ 글로벌 에러 예산 체크 없음
```

### 1.2 문서 설계 의도 vs 현재 구현

**문서 설계** (`71_CANARY_CONFIG_ROLLOUT.md` Lines 1208-1211):
```python
# Error Budget 관련
error_budget_drain_rate_max: float = 1.2   # 1.2x 소진률 한계
error_budget_remaining_min: float = 0.1    # 10% 이상 남아있어야 함
```

**현재 구현**: `error_budget_remaining_min` 체크가 **코드에 없음**

### 1.3 발생 가능한 시나리오

| 상황 | Canary 메트릭 | 글로벌 에러 예산 | 현재 동작 | 위험 |
|------|---------------|------------------|-----------|------|
| A | 정상 (0.5%) | 정상 (80%) | ✅ 프로모션 | 없음 |
| B | 정상 (0.5%) | **고갈 (5%)** | ⚠️ **프로모션** | **높음** |
| C | 비정상 (10%) | 정상 (80%) | ❌ 차단 | 없음 |

**시나리오 B의 위험:**
1. 글로벌 에러 예산이 5% (CRITICAL 상태)
2. Canary 단계의 에러율만 정상
3. 더 많은 클러스터에 새 설정 적용
4. 새 설정이 에러를 유발할 경우 → **에러 예산 완전 고갈**
5. 시스템 전체가 FREEZE 상태 진입

---

## 2. 해결 방안

### 2.1 설계 원칙

**`12_ERROR_BUDGET.md` 문서 근거** (Lines 200-230):

| 상태 | Budget 잔여 | feature 배포 | 프로모션 허용 |
|------|-------------|--------------|---------------|
| PROCEED | ≥ 75% | ✅ | ✅ |
| CAUTION | 50-75% | ✅ | ✅ |
| WARNING | 20-50% | ❌ | ⚠️ 주의 |
| FREEZE | < 20% | ❌ | ❌ |

**Canary 프로모션 = 신규 설정을 더 많은 클러스터에 적용 = feature 배포와 동등한 리스크**

### 2.2 아키텍처 다이어그램 근거

**`71_CANARY_CONFIG_ROLLOUT.md` Line 2011**:
```
│              - 글로벌 앵커 (Error Budget, Governance)           │
```

→ Error Budget이 "글로벌 앵커"로 명시됨 → 모든 자동화가 이 앵커를 참조해야 함

---

## 3. 구현 계획

### 3.1 수정 대상 파일

| 파일 | 수정 내용 |
|------|----------|
| `tasks/canary_watchdog.py` | `auto_promote_eligible()`에 governance check 추가 |
| `services/canary/service.py` | `promote()`에 governance check 추가 (선택적) |
| `services/canary/audit.py` | `governance_blocked` 액션 추가 |

### 3.2 구현 코드

#### 3.2.1 `canary_watchdog.py` 수정

**현재 코드** (Lines 405-455):
```python
def auto_promote_eligible(self) -> WatchdogResult:
    result = WatchdogResult()

    if not self.config.enable_auto_promote:
        return result

    try:
        from selfhealing.services.canary import CanaryState

        active_rollouts = self.service.get_active_rollouts()
        result.scanned_count = len(active_rollouts)
        now = utc_now()

        for rollout in active_rollouts:
            # ... 조건 확인
            success = self.service.promote(rollout.id, force=False)
```

**수정 후 코드**:
```python
def auto_promote_eligible(self) -> WatchdogResult:
    """
    자동 프로모션 조건을 충족한 롤아웃 프로모션.

    조건:
    - auto_promote=True인 단계
    - duration_minutes 경과
    - 메트릭 검증 통과
    - ✅ 글로벌 에러 예산 체크 (신규)

    Returns:
        WatchdogResult: 프로모션 결과
    """
    result = WatchdogResult()

    if not self.config.enable_auto_promote:
        return result

    # ============================================================
    # [신규] 글로벌 에러 예산 체크
    # ============================================================
    try:
        from selfhealing.services.governance_checks import check_all_governance

        governance = check_all_governance(
            check_kill_switch=True,
            check_emergency=True,
            emergency_min_level=2,  # LEVEL_2+ 시 차단
            check_error_budget=True,
            operation_name="auto_promote_canary",
            service_name="RolloutWatchdog",
            domain="canary",
            audit_on_block=True,  # 차단 시 자동 Audit
        )

        if not governance.allowed:
            logger.warning(
                f"[Watchdog] Auto promotion blocked by governance: "
                f"{governance.block_message}"
            )
            result.governance_blocked = True
            result.governance_block_reason = governance.block_message
            return result

    except ImportError:
        logger.debug("[Watchdog] GovernanceChecks not available, skipping")
        # Fail-Closed: Import 실패 시에도 차단 (보수적 정책)
        result.governance_blocked = True
        result.governance_block_reason = "GovernanceChecks module not available"
        return result
    except Exception as e:
        logger.warning(f"[Watchdog] Governance check failed: {e}")
        # ============================================================
        # Fail-Closed 정책 (리뷰 ① 반영)
        # ============================================================
        # 이유: Canary 프로모션은 "변경 작업"이므로 런타임 트래픽 처리와 달리
        #       예산 확인 실패 시 안전하게 차단하는 것이 적절함.
        # 근거: CanaryInterlock도 fail_closed=True 기본값 사용
        #       (74_CANARY_SAFETY_INTERLOCK.md:377-389)
        # 오버헤드: Watchdog은 Background Task → 50-100ms 지연 무시 가능
        result.governance_blocked = True
        result.governance_block_reason = f"Governance check error: {e}"
        return result
    # ============================================================

    try:
        from selfhealing.services.canary import CanaryState

        active_rollouts = self.service.get_active_rollouts()
        result.scanned_count = len(active_rollouts)
        now = utc_now()

        for rollout in active_rollouts:
            if rollout.state != CanaryState.CANARY:
                continue

            stage = rollout.current_stage
            if not stage or not stage.auto_promote:
                continue

            # duration 경과 확인
            elapsed = (now - rollout.created_at).total_seconds() / 60
            if elapsed < stage.duration_minutes:
                continue

            # 메트릭 검증 후 프로모션
            try:
                success = self.service.promote(rollout.id, force=False)
                if success:
                    result.promote_count += 1
                    logger.info(
                        f"[Watchdog] Auto promoted: {rollout.id} "
                        f"(stage: {stage.name})"
                    )
            except Exception as e:
                result.errors.append(f"{rollout.id}: {e}")

    except Exception as e:
        logger.exception("[Watchdog] Auto promote scan failed")
        result.success = False
        result.errors.append(str(e))

    return result
```

#### 3.2.2 `WatchdogResult` 확장

**현재 코드** (`canary_watchdog.py` Lines 115-155):
```python
@dataclass
class WatchdogResult:
    success: bool = True
    scanned_count: int = 0
    zombie_count: int = 0
    rollback_count: int = 0
    promote_count: int = 0
    zombies: list[ZombieRollout] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
```

**수정 후 코드**:
```python
@dataclass
class WatchdogResult:
    """
    Watchdog 실행 결과.

    Attributes:
        success: 성공 여부
        scanned_count: 검사한 롤아웃 수
        zombie_count: 감지된 Zombie 수
        rollback_count: 자동 롤백된 수
        promote_count: 자동 프로모션된 수
        governance_blocked: 거버넌스에 의해 차단됨
        governance_block_reason: 차단 사유
        zombies: Zombie 롤아웃 목록
        errors: 에러 목록
    """

    success: bool = True
    scanned_count: int = 0
    zombie_count: int = 0
    rollback_count: int = 0
    promote_count: int = 0
    governance_blocked: bool = False  # ✅ 신규
    governance_block_reason: str = ""  # ✅ 신규
    zombies: list[ZombieRollout] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "success": self.success,
            "scanned_count": self.scanned_count,
            "zombie_count": self.zombie_count,
            "rollback_count": self.rollback_count,
            "promote_count": self.promote_count,
            "governance_blocked": self.governance_blocked,  # ✅ 신규
            "governance_block_reason": self.governance_block_reason,  # ✅ 신규
            "zombies": [
                {
                    "rollout_id": z.rollout_id,
                    "config_type": z.config_type,
                    "state": z.state,
                    "stuck_minutes": z.stuck_minutes,
                    "action_taken": z.action_taken,
                }
                for z in self.zombies
            ],
            "errors": self.errors,
        }
```

#### 3.2.3 Audit 로그 확장 (선택적)

**`services/canary/audit.py`에 새 액션 추가**:

현재 `CANARY_ACTIONS` (Lines 36-48):
```python
CANARY_ACTIONS = [
    "create",
    "start",
    "promote",
    "rollback",
    "pause",
    "resume",
    "complete",
    "panic_rollback",
    "cancel",
    "force_promote",
]
```

**수정 후**:
```python
CANARY_ACTIONS = [
    "create",
    "start",
    "promote",
    "rollback",
    "pause",
    "resume",
    "complete",
    "panic_rollback",
    "cancel",
    "force_promote",
    "governance_blocked",  # ✅ 신규: 거버넌스 차단
]
```

---

## 4. 테스트 계획

### 4.1 단위 테스트

```python
# tests/tasks/test_canary_watchdog_governance.py

import pytest
from unittest.mock import patch, MagicMock
from selfhealing.tasks.canary_watchdog import RolloutWatchdog, WatchdogConfig


class TestAutoPromoteGovernance:
    """auto_promote_eligible 거버넌스 체크 테스트."""

    @pytest.fixture
    def watchdog(self):
        config = WatchdogConfig(enable_auto_promote=True)
        return RolloutWatchdog(config)

    def test_blocked_by_error_budget(self, watchdog):
        """에러 예산 부족 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import GovernanceCheckResult

        blocked_result = GovernanceCheckResult.blocked_by_error_budget(
            budget_percent=5.0,
            threshold_percent=10.0,
        )

        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "error_budget" in result.governance_block_reason.lower()
            assert result.promote_count == 0

    def test_blocked_by_emergency_mode(self, watchdog):
        """비상 모드 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import GovernanceCheckResult

        blocked_result = GovernanceCheckResult.blocked_by_emergency(
            level_name="LEVEL_2",
            message="Emergency mode active",
        )

        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "emergency" in result.governance_block_reason.lower()

    def test_blocked_by_kill_switch(self, watchdog):
        """Kill Switch 활성화 시 자동 프로모션 차단."""
        from selfhealing.services.governance_checks import GovernanceCheckResult

        blocked_result = GovernanceCheckResult.blocked_by_kill_switch()

        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov:
            mock_gov.return_value = blocked_result

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True
            assert "kill_switch" in result.governance_block_reason.lower()

    def test_allowed_when_governance_passes(self, watchdog):
        """거버넌스 통과 시 정상 프로모션."""
        from selfhealing.services.governance_checks import GovernanceCheckResult

        allowed_result = GovernanceCheckResult.allowed_result()

        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov, patch.object(
            watchdog.service, "get_active_rollouts"
        ) as mock_rollouts:
            mock_gov.return_value = allowed_result
            mock_rollouts.return_value = []  # 활성 롤아웃 없음

            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is False
            mock_gov.assert_called_once()

    def test_fail_open_on_governance_error(self, watchdog):
        """거버넌스 체크 실패 시 Fail-open."""
        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov, patch.object(
            watchdog.service, "get_active_rollouts"
        ) as mock_rollouts:
            mock_gov.side_effect = Exception("Redis connection failed")
            mock_rollouts.return_value = []

            result = watchdog.auto_promote_eligible()

            # Fail-open: 에러 시에도 진행
            assert result.governance_blocked is False
```

### 4.2 통합 테스트

```python
# tests/integration/test_canary_governance_integration.py

import pytest
from unittest.mock import patch


class TestCanaryGovernanceIntegration:
    """Canary + Governance 통합 테스트."""

    def test_error_budget_critical_blocks_promotion(self):
        """에러 예산 CRITICAL 상태에서 프로모션 차단 확인."""
        from selfhealing.tasks.canary_watchdog import get_rollout_watchdog

        # ErrorBudgetGate를 CRITICAL 상태로 설정
        with patch(
            "selfhealing.services.error_budget_gate.check_automation_allowed"
        ) as mock_gate:
            mock_gate.return_value = (False, 5.0, 10.0, "CRITICAL")

            watchdog = get_rollout_watchdog()
            result = watchdog.auto_promote_eligible()

            assert result.governance_blocked is True

    def test_audit_logged_on_governance_block(self):
        """거버넌스 차단 시 Audit 로그 기록 확인."""
        from selfhealing.services.governance_checks import GovernanceCheckResult
        from selfhealing.tasks.canary_watchdog import get_rollout_watchdog

        blocked_result = GovernanceCheckResult.blocked_by_error_budget(5.0, 10.0)

        with patch(
            "selfhealing.services.governance_checks.check_all_governance"
        ) as mock_gov, patch(
            "selfhealing.services.governance_checks._log_governance_blocked"
        ) as mock_audit:
            mock_gov.return_value = blocked_result

            watchdog = get_rollout_watchdog()
            watchdog.auto_promote_eligible()

            # check_all_governance 내부에서 audit 호출 확인
            mock_gov.assert_called_once()
            # audit_on_block=True이므로 내부에서 _log_governance_blocked 호출됨
```

---

## 5. 일관성 검증

### 5.1 다른 자동화와 비교

| 자동화 | 거버넌스 체크 | 코드 위치 |
|--------|--------------|-----------|
| `replay_single()` | ✅ `check_all_governance(check_error_budget=True)` | `replay_service.py:336` |
| `replay_batch()` | ✅ `check_all_governance(check_error_budget=True)` | `replay_service.py:452` |
| `run_scheduled_experiments()` | ✅ `check_all_governance(check_error_budget=True)` | `execution_services.py:196` |
| `traffic_aware_replay` | ✅ `check_all_governance()` | `traffic_aware_replay.py:122` |
| `auto_promote_eligible()` | ❌ **없음** | `canary_watchdog.py:405` |

**수정 후**:
| 자동화 | 거버넌스 체크 |
|--------|--------------|
| `auto_promote_eligible()` | ✅ `check_all_governance(check_error_budget=True)` |

---

## 6. 마이그레이션 영향

### 6.1 하위 호환성

| 항목 | 영향 |
|------|------|
| **API 변경** | ❌ 없음 - `WatchdogResult`에 필드 추가만 |
| **동작 변경** | ⚠️ 있음 - 에러 예산 부족 시 프로모션 차단 |
| **설정 변경** | ❌ 없음 - 기존 설정 그대로 사용 |

### 6.2 Rollback 계획

차단 동작이 문제가 되면:
1. `check_error_budget=False`로 변경하여 에러 예산 체크만 비활성화
2. 또는 전체 governance check 블록을 `try/except`로 감싸 fail-open 유지

---

## 7. 구현 로드맵

```
Phase 1: 코드 수정 ⏳
├── WatchdogResult에 governance 필드 추가
├── auto_promote_eligible()에 check_all_governance 추가
└── CANARY_ACTIONS에 governance_blocked 추가

Phase 2: 테스트 ⏳
├── 단위 테스트 추가
├── 통합 테스트 추가
└── 기존 테스트 통과 확인

Phase 3: 상태 추적 확장 ⏳ (리뷰 ② 반영)
├── CanaryRollout에 pause_reason, pause_triggered_by 필드 추가
├── pause() 시그니처 확장
└── pause_triggered_by="error_budget" 값 정의

Phase 4: RecoveryCoordinator 연동 ⏳ (리뷰 ③ 반영)
├── resume_paused_rollouts()에 triggered_by_filter 파라미터 추가
├── CANARY_RESUME step params에 triggered_by_filter 추가
└── 77_RECOVERY_COORDINATOR.md 연동 섹션 명시

Phase 5: 문서 ⏳
├── 71_CANARY_CONFIG_ROLLOUT.md 업데이트
└── CHANGELOG 업데이트
```

---

## 8. 상태 추적 확장 (리뷰 ② 반영)

### 8.1 문제점

현재 `CanaryRollout`이 `PAUSED` 상태가 되어도 **왜 멈췄는지** 알 수 없습니다.

### 8.2 코드 근거

**74_CANARY_SAFETY_INTERLOCK.md에 이미 설계됨** (Lines 794-810):
```python
# packages/selfhealing-python/src/selfhealing/services/canary/models.py (수정)

@dataclass
class CanaryRollout:
    # 신규: PAUSED 상태 사유 추적
    pause_reason: Optional[str] = None
    """일시 중지 사유."""

    pause_triggered_by: Optional[str] = None
    """
    일시 중지 트리거 유형.
    Values:
    - "interlock": Safety Interlock에 의해 자동 중지
    - "manual": 운영자 수동 중지
    - "chaos_guard": Chaos Guard에 의해 중지
    - "metrics": 메트릭 악화로 인한 중지
    - "error_budget": 에러 예산 부족으로 중지  # ✅ 신규
    """
```

**그러나 현재 `models.py` 실제 구현에는 없음** (확인 완료):
- `pause_reason`, `pause_triggered_by` 필드 미구현

### 8.3 구현 계획

#### 8.3.1 CanaryRollout 필드 추가

```python
# packages/selfhealing-python/src/selfhealing/services/canary/models.py

@dataclass
class CanaryRollout:
    # ... 기존 필드 ...

    # ✅ 신규: PAUSED 상태 사유 추적 (리뷰 ② 반영)
    pause_reason: str | None = None
    """일시 중지 사유 (예: 'Error budget below threshold (5.0% < 10.0%)')."""

    pause_triggered_by: str | None = None
    """
    일시 중지 트리거 유형.

    Values:
    - "interlock": Safety Interlock에 의해 자동 중지
    - "manual": 운영자 수동 중지
    - "chaos_guard": Chaos Guard에 의해 중지
    - "metrics": 메트릭 악화로 인한 중지
    - "error_budget": 에러 예산 부족으로 중지
    """

    paused_at: datetime | None = None
    """일시 중지 시각."""
```

#### 8.3.2 pause() 시그니처 확장

```python
# packages/selfhealing-python/src/selfhealing/services/canary/service.py

def pause(
    self,
    rollout_id: str,
    reason: str = "",
    triggered_by: str = "manual",  # ✅ 신규
) -> bool:
    """
    롤아웃 일시 중지 (사유 추적 확장).

    Args:
        rollout_id: 롤아웃 ID
        reason: 일시 중지 사유
        triggered_by: 트리거 유형 (manual, interlock, chaos_guard, metrics, error_budget)

    Returns:
        성공 여부
    """
    rollout = self.get_rollout(rollout_id)
    if not rollout or rollout.state != CanaryState.CANARY:
        return False

    rollout.state = CanaryState.PAUSED
    rollout.pause_reason = reason  # ✅ 신규
    rollout.pause_triggered_by = triggered_by  # ✅ 신규
    rollout.paused_at = utc_now()  # ✅ 신규
    self._save_rollout(rollout)

    log_canary_action(
        action="pause",
        rollout=rollout,
        additional_context={
            "pause_reason": reason,
            "pause_triggered_by": triggered_by,
        },
    )

    logger.info(
        f"[CanaryRollout] Paused: id={rollout_id}, "
        f"triggered_by={triggered_by}, reason={reason}"
    )
    return True
```

#### 8.3.3 Watchdog에서 pause 호출 (거버넌스 차단 시)

```python
# 거버넌스 차단 시 PAUSED 상태로 전환 + 사유 기록
if not governance.allowed:
    # 활성 롤아웃들을 PAUSED 상태로 전환
    for rollout in active_rollouts:
        if rollout.state == CanaryState.CANARY:
            self.service.pause(
                rollout.id,
                reason=governance.block_message,
                triggered_by="error_budget",
            )

    result.governance_blocked = True
    result.governance_block_reason = governance.block_message
    return result
```

---

## 9. RecoveryCoordinator 연동 (리뷰 ③ 반영)

### 9.1 문제점

현재 `CANARY_RESUME` 단계는 **모든** PAUSED 롤아웃을 재개합니다.
리뷰 제안: `pause_triggered_by="error_budget"`인 롤아웃만 우선 재개.

### 9.2 코드 근거

**현재 구현** (`recovery_coordinator.py` Lines 771-783):
```python
resume_paused_only = step.params.get("resume_paused_only", True)

if resume_paused_only:
    resumed = service.resume_paused_rollouts(session.namespace)  # 모든 PAUSED 대상
else:
    resumed = service.resume_all_rollouts(session.namespace)
```

**`resume_paused_rollouts` 메서드 미구현** (grep 결과 확인):
- 호출만 존재, 실제 구현 없음
- 77번 문서에만 설계 명시

### 9.3 구현 계획

#### 9.3.1 resume_paused_rollouts 시그니처 확장

```python
# packages/selfhealing-python/src/selfhealing/services/canary/service.py

def resume_paused_rollouts(
    self,
    namespace: str | None = None,
    triggered_by_filter: str | None = None,  # ✅ 신규
) -> list[str]:
    """
    PAUSED 상태의 롤아웃 재개.

    Args:
        namespace: 네임스페이스 필터 (None이면 전체)
        triggered_by_filter: pause_triggered_by 필터 (예: "error_budget")
            None이면 모든 PAUSED 롤아웃 대상

    Returns:
        재개된 롤아웃 ID 목록
    """
    resumed = []
    for rollout in self.get_active_rollouts():
        if rollout.state != CanaryState.PAUSED:
            continue

        # namespace 필터
        if namespace and rollout.namespace != namespace:
            continue

        # triggered_by 필터 (리뷰 ③ 핵심)
        if triggered_by_filter:
            if rollout.pause_triggered_by != triggered_by_filter:
                continue

        if self.resume(rollout.id):
            resumed.append(rollout.id)

    return resumed
```

#### 9.3.2 CANARY_RESUME step params 확장

**현재** (`regional_recovery_policy.py` Lines 486-491):
```python
RecoveryStep(
    step_type=RecoveryStepType.CANARY_RESUME,
    order=order,
    wait_after_seconds=config.canary_resume_wait_after_seconds,
    params={
        "resume_paused_only": True,
    },
)
```

**수정 후**:
```python
RecoveryStep(
    step_type=RecoveryStepType.CANARY_RESUME,
    order=order,
    wait_after_seconds=config.canary_resume_wait_after_seconds,
    params={
        "resume_paused_only": True,
        "triggered_by_filter": "error_budget",  # ✅ 신규: 예산 때문에 멈춘 롤아웃만
    },
)
```

#### 9.3.3 _handle_canary_resume 수정

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/recovery_coordinator.py

def _handle_canary_resume(
    self,
    session: RecoverySession,
    step: RecoveryStep,
) -> dict[str, Any]:
    """Canary 롤아웃 재개."""
    resume_paused_only = step.params.get("resume_paused_only", True)
    triggered_by_filter = step.params.get("triggered_by_filter", None)  # ✅ 신규

    try:
        from selfhealing.services.canary import get_canary_service

        service = get_canary_service()

        if resume_paused_only:
            # ✅ triggered_by_filter 전달
            resumed = service.resume_paused_rollouts(
                namespace=session.namespace,
                triggered_by_filter=triggered_by_filter,
            )
        else:
            resumed = service.resume_all_rollouts(session.namespace)

        return {
            "success": True,
            "resumed_count": len(resumed) if resumed else 0,
            "triggered_by_filter": triggered_by_filter,  # ✅ 감사 로그용
        }
    except (ImportError, AttributeError):
        return {"success": True, "resumed_count": 0, "skipped": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

### 9.4 77번 문서 연동 섹션 추가

`77_RECOVERY_COORDINATOR.md`에 다음 내용 추가 필요:

```markdown
### 11.8 Canary Error Budget Gate 연동 (172번 문서)

Error Budget Gate로 인해 PAUSED된 롤아웃은 `CANARY_RESUME` 단계에서
`triggered_by_filter="error_budget"` 파라미터를 통해 선별적으로 재개됩니다.

**인과관계:**
1. Error Budget 고갈 → Canary 프로모션 차단 → PAUSED (triggered_by="error_budget")
2. Error Budget 회복 → RecoveryCoordinator 시작 → CANARY_RESUME 단계
3. `triggered_by_filter="error_budget"` → 예산 때문에 멈춘 롤아웃만 재개

**Reference**: [172_CANARY_ERROR_BUDGET_GATE.md](172_CANARY_ERROR_BUDGET_GATE.md)
```

---

## 10. Fail-Safe 정책 정리 (리뷰 ① 반영)

### 10.1 정책 결정

| 컴포넌트 | 정책 | 근거 |
|---------|------|------|
| 런타임 트래픽 처리 (Throttling) | **Fail-Open** | 가용성 우선 |
| **Canary 프로모션 (변경 작업)** | **Fail-Closed** | 안전성 우선 |

### 10.2 코드 근거

**CanaryInterlock도 Fail-Closed 사용** (`74_CANARY_SAFETY_INTERLOCK.md` Lines 377-389):
```python
def __init__(
    self,
    backend: StateBackend | None = None,
    fail_closed: bool = True,  # ← 기본값 True
):
    self.fail_closed = fail_closed
```

```python
# Fail-Closed: 백엔드 장애 시 LEVEL_3로 간주
if self.fail_closed:
    return InterlockResult.fail_closed(error_message=str(e))
```

### 10.3 기술 실사 관점

> "$500M 엑싯을 위한 기술 실사에서 '변경 관리 리스크를 가장 보수적으로 통제한다'는 평가"

**Fail-Closed 채택 근거:**
1. Canary 프로모션 = 설정 변경 = 위험 작업
2. Watchdog은 Background Task → 50-100ms 지연 무시 가능
3. `CanaryInterlock`과 일관된 정책

---

## 11. 스코프 외 항목

### 11.1 티어별 차등 임계치

**현재 상태**: Error Budget에는 단일 임계값(`critical_threshold_percent=10.0`)만 존재

**Phase 2 확장 사항으로 분리 권장**:
- 복잡도 증가 (설정 구조 변경)
- 먼저 Global Gate 동작 검증 후 세분화

### 11.2 리전별 버짓 분리

**현재 상태**:
- `RegionalRecoveryPolicy` → 리전별 **복구 정책** 존재
- 리전별 **Error Budget 분리** → 미구현

**Phase 2 확장 사항으로 분리 권장**:
- 별도 문서에서 설계 필요
- 172는 Global Gate에 집중

---

## 12. 참조

### 12.1 관련 코드

| 파일 | 역할 |
|------|------|
| `tasks/canary_watchdog.py` | Canary 자동 프로모션 태스크 |
| `services/canary/service.py` | Canary 롤아웃 서비스 |
| `services/canary/models.py` | Canary 데이터 모델 |
| `services/governance_checks.py` | 공통 거버넌스 체크 |
| `services/error_budget_gate/gate.py` | 에러 예산 게이트 |
| `services/coordination/recovery_coordinator.py` | 복구 조율자 |

### 12.2 관련 문서

| 문서 | 내용 |
|------|------|
| [71_CANARY_CONFIG_ROLLOUT.md](71_CANARY_CONFIG_ROLLOUT.md) | Canary 설계 문서 |
| [74_CANARY_SAFETY_INTERLOCK.md](74_CANARY_SAFETY_INTERLOCK.md) | Canary Safety Interlock (pause_reason, Fail-Closed 패턴) |
| [77_RECOVERY_COORDINATOR.md](77_RECOVERY_COORDINATOR.md) | RecoveryCoordinator (CANARY_RESUME 단계) |
| [12_ERROR_BUDGET.md](../12_ERROR_BUDGET.md) | 에러 예산 정책 |
| [41_WRAPPER_REFACTORING_PART4.md](41_WRAPPER_REFACTORING_PART4.md) | automation_gate 설계 |

---

## 13. 추가 구현 요구사항 (리뷰 반영)

> **작성일**: 2026-02-04
> **리뷰 기반**: 9가지 필수/권장 사항

---

### 13.1 Zombie 판정 제외 로직 (Q12) - 🔴 필수

#### 13.1.1 문제점

현재 Zombie 판정 로직은 PAUSED 상태의 **사유를 구분하지 않고** 일괄 Zombie로 판정합니다.

**현재 코드** (`tasks/canary_watchdog.py` Lines 293-298):
```python
elif rollout.state == CanaryState.PAUSED:
    # PAUSED 상태: zombie_threshold 초과
    if stuck_minutes > self.config.zombie_threshold_minutes:
        is_zombie = True
        reason = f"Paused for {stuck_minutes:.1f} min (threshold: {self.config.zombie_threshold_minutes})"
```

**위험:** Error Budget 때문에 PAUSED된 롤아웃이 30분 후 Zombie로 오판되어 강제 롤백됩니다.

#### 13.1.2 해결 방안

```python
# tasks/canary_watchdog.py - _check_zombie 메서드 수정

# 설정으로 분리 (확장 가능)
ZOMBIE_EXEMPT_TRIGGERS: list[str] = ["error_budget", "governance"]

def _check_zombie(
    self,
    rollout: CanaryRollout,
    now: datetime,
) -> ZombieRollout | None:
    """롤아웃이 Zombie인지 확인."""
    from selfhealing.services.canary import CanaryState

    stage = rollout.current_stage
    stage_duration = stage.duration_minutes if stage else 5
    stuck_since = rollout.created_at
    stuck_minutes = (now - stuck_since).total_seconds() / 60

    is_zombie = False
    reason = ""

    if rollout.state == CanaryState.CANARY:
        threshold = stage_duration * 2
        if stuck_minutes > threshold:
            is_zombie = True
            reason = f"Stuck in CANARY for {stuck_minutes:.1f} min (threshold: {threshold})"

    elif rollout.state == CanaryState.PAUSED:
        # ============================================================
        # [신규] Error Budget 대기는 정상적인 대기이므로 Zombie 제외
        # ============================================================
        # pause_triggered_by가 ZOMBIE_EXEMPT_TRIGGERS에 포함되면 제외
        triggered_by = getattr(rollout, "pause_triggered_by", None)

        if triggered_by in ZOMBIE_EXEMPT_TRIGGERS:
            # 정상적인 대기 상태 - Zombie 아님
            logger.debug(
                f"[Watchdog] Rollout {rollout.id} excluded from zombie check: "
                f"triggered_by={triggered_by}"
            )
            return None

        # 그 외 PAUSED는 기존 로직 적용
        if stuck_minutes > self.config.zombie_threshold_minutes:
            is_zombie = True
            reason = f"Paused for {stuck_minutes:.1f} min (threshold: {self.config.zombie_threshold_minutes})"
        # ============================================================

    elif rollout.state == CanaryState.PROMOTING:
        if stuck_minutes > 5:
            is_zombie = True
            reason = "Stuck in PROMOTING state"

    if not is_zombie:
        return None

    return ZombieRollout(
        rollout_id=rollout.id,
        config_type=rollout.config_type,
        state=rollout.state.value,
        stuck_since=stuck_since,
        stuck_minutes=stuck_minutes,
        created_by=rollout.created_by,
        affected_clusters=rollout.affected_clusters,
        reason=reason,
    )
```

#### 13.1.3 설정 확장

```python
# settings/canary_watchdog.py

class CanaryWatchdogSettings(BaseSettings):
    # ... 기존 설정 ...

    zombie_exempt_triggers: list[str] = Field(
        default=["error_budget", "governance"],
        description="Zombie 판정에서 제외할 pause_triggered_by 값 목록",
    )
```

---

### 13.2 Break Glass 비상 탈출구 (Q6) - 🔴 필수

#### 13.2.1 문제점

거버넌스 체크 서비스(Redis 등)가 장애 시 Fail-Closed 정책으로 인해 긴급 패치조차 배포할 수 없습니다.

#### 13.2.2 기존 패턴 확인

**이미 존재하는 Break Glass 패턴** (`services/canary/override.py`):
```python
@dataclass
class EmergencyOverrideRequest:
    """Emergency Override (Break Glass) 요청."""
    reason: str  # 최소 10자 이상
    requested_by: str
    ticket_id: str | None = None
    approval_token: str | None = None  # LEVEL_3에서 필수

@dataclass
class EmergencyOverridePolicy:
    """Emergency Override 정책."""
    enabled: bool = True
    min_reason_length: int = 10
    require_ticket_id: bool = True
    pir_required: bool = True  # Post-Incident Review 필수
```

#### 13.2.3 해결 방안: 환경변수 + 기존 패턴 통합

```python
# settings/governance.py

class GovernanceSettings(BaseSettings):
    # ... 기존 설정 ...

    # ============================================================
    # Break Glass (비상 탈출구)
    # ============================================================
    break_glass_enabled: bool = Field(
        default=False,
        description="긴급 상황 시 모든 거버넌스 체크 우회 (PIR 필수)",
    )

    break_glass_audit_required: bool = Field(
        default=True,
        description="Break Glass 사용 시 Audit 로그 필수",
    )

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_GOVERNANCE_",
        # SELFHEALING_GOVERNANCE_BREAK_GLASS_ENABLED=true
    )
```

```python
# services/governance_checks.py - check_all_governance 수정

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
    """모든 거버넌스 체크를 순차적으로 수행."""

    # ============================================================
    # [신규] Break Glass 체크 (최상단)
    # ============================================================
    try:
        from selfhealing.settings.governance import get_governance_settings
        settings = get_governance_settings()

        if settings.break_glass_enabled:
            logger.warning(
                f"[GovernanceChecks] BREAK GLASS ACTIVE - "
                f"bypassing all checks for {operation_name}"
            )

            # Audit 기록 (필수)
            if settings.break_glass_audit_required:
                _log_governance_blocked(
                    block_reason="break_glass_bypass",
                    operation_name=operation_name,
                    details={
                        "action": "BYPASSED",
                        "warning": "PIR required after incident",
                    },
                    service_name=service_name,
                    domain=domain,
                )

            return GovernanceCheckResult.allowed_result()
    except Exception as e:
        logger.debug(f"[GovernanceChecks] Break glass check failed: {e}")
    # ============================================================

    # ... 기존 체크 로직 ...
```

#### 13.2.4 운영 가이드

```markdown
## Break Glass 사용 절차

1. **활성화**: `SELFHEALING_GOVERNANCE_BREAK_GLASS_ENABLED=true`
2. **배포 진행**: 모든 거버넌스 체크 우회됨
3. **비활성화**: 환경변수 제거 또는 `false` 설정
4. **PIR 작성**: 48시간 내 Post-Incident Review 필수

⚠️ 경고: Break Glass는 장애 복구용으로만 사용. 남용 시 감사 추적됨.
```

---

### 13.3 Resume 대상 엄격한 필터링 (Q2) - 🔴 필수

#### 13.3.1 문제점

운영자가 수동으로 멈춘 배포(`pause_triggered_by="manual"`)가 에러 예산 회복 시 자동 재개되면 사고 발생 가능.

#### 13.3.2 해결 방안: Whitelist 방식

```python
# services/canary/service.py

def resume_paused_rollouts(
    self,
    namespace: str | None = None,
    triggered_by_whitelist: list[str] | None = None,  # ✅ Whitelist 방식
) -> list[str]:
    """
    PAUSED 상태의 롤아웃 재개 (Whitelist 필터링).

    Args:
        namespace: 네임스페이스 필터 (None이면 전체)
        triggered_by_whitelist: 재개 허용 목록 (예: ["error_budget"])
            - None이면 모든 PAUSED 대상 (기존 동작, 비권장)
            - 빈 리스트면 아무것도 재개 안 함
            - ["error_budget"]이면 해당 사유로 멈춘 롤아웃만 재개

    Returns:
        재개된 롤아웃 ID 목록

    Warning:
        triggered_by_whitelist=None은 기존 호환성을 위해 유지되나,
        명시적 Whitelist 사용을 강력 권장합니다.
    """
    resumed = []

    for rollout in self.get_active_rollouts():
        if rollout.state != CanaryState.PAUSED:
            continue

        # namespace 필터
        if namespace and getattr(rollout, "namespace", None) != namespace:
            continue

        # ============================================================
        # Whitelist 필터링 (핵심)
        # ============================================================
        triggered_by = getattr(rollout, "pause_triggered_by", None)

        if triggered_by_whitelist is not None:
            # Whitelist가 명시된 경우: 해당 사유만 재개
            if triggered_by not in triggered_by_whitelist:
                logger.debug(
                    f"[CanaryRollout] Skipping resume for {rollout.id}: "
                    f"triggered_by={triggered_by} not in whitelist"
                )
                continue
        else:
            # Whitelist=None: 기존 동작 (모든 PAUSED 재개)
            # ⚠️ 경고 로그 추가
            logger.warning(
                f"[CanaryRollout] Resuming {rollout.id} without whitelist filter. "
                f"Consider using triggered_by_whitelist for safety."
            )
        # ============================================================

        if self.resume(rollout.id):
            resumed.append(rollout.id)

    return resumed
```

#### 13.3.3 RecoveryCoordinator 연동

```python
# services/coordination/recovery_coordinator.py - _handle_canary_resume 수정

def _handle_canary_resume(
    self,
    session: RecoverySession,
    step: RecoveryStep,
) -> dict[str, Any]:
    """Canary 롤아웃 재개."""
    resume_paused_only = step.params.get("resume_paused_only", True)

    # ============================================================
    # [신규] Whitelist 기반 필터링
    # ============================================================
    # 기본값: error_budget만 재개 (안전)
    triggered_by_whitelist = step.params.get(
        "triggered_by_whitelist",
        ["error_budget"]  # 기본값: 예산 때문에 멈춘 것만
    )
    # ============================================================

    try:
        from selfhealing.services.canary import get_canary_service
        service = get_canary_service()

        if resume_paused_only:
            resumed = service.resume_paused_rollouts(
                namespace=session.namespace,
                triggered_by_whitelist=triggered_by_whitelist,  # ✅ Whitelist 전달
            )
        else:
            resumed = service.resume_all_rollouts(session.namespace)

        return {
            "success": True,
            "resumed_count": len(resumed) if resumed else 0,
            "triggered_by_whitelist": triggered_by_whitelist,
        }
    except (ImportError, AttributeError):
        return {"success": True, "resumed_count": 0, "skipped": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

---

### 13.4 수동 프로모션 Gate 적용 (Q4) - 🟡 권장

#### 13.4.1 문제점

"자동은 막히는데 수동은 뚫린다"는 정책은 일관성이 없습니다.

**현재 코드** (`services/canary/service.py` Lines 321-377):
```python
def promote(self, rollout_id: str, force: bool = False) -> bool:
    # force는 메트릭 검증만 건너뜀
    if not force:
        metrics = self._collect_stage_metrics(rollout)
    # governance check 없음!
```

#### 13.4.2 해결 방안: 파라미터 분리

```python
# services/canary/service.py

def promote(
    self,
    rollout_id: str,
    force: bool = False,  # 메트릭 검증 건너뜀 (기존)
    bypass_governance: bool = False,  # ✅ 신규: 거버넌스 검증 건너뜀
    bypass_reason: str = "",  # bypass 시 사유 필수
    requested_by: str = "",  # 요청자 (Audit용)
) -> bool:
    """
    다음 단계로 프로모션.

    Args:
        rollout_id: 롤아웃 ID
        force: 메트릭 검증 무시 (기존)
        bypass_governance: 거버넌스 검증 무시 (신규, Audit 필수)
        bypass_reason: bypass 시 사유 (bypass_governance=True일 때 필수)
        requested_by: 요청자 (Audit 로깅용)

    Returns:
        성공 여부
    """
    rollout = self.get_rollout(rollout_id)
    if not rollout:
        return False

    if rollout.state not in (CanaryState.CANARY, CanaryState.PAUSED):
        logger.warning(f"[CanaryRollout] Cannot promote: state={rollout.state}")
        return False

    # ============================================================
    # [신규] 거버넌스 체크 (수동 프로모션에도 적용)
    # ============================================================
    if not bypass_governance:
        try:
            from selfhealing.services.governance_checks import check_all_governance

            governance = check_all_governance(
                check_kill_switch=True,
                check_emergency=True,
                check_error_budget=True,
                operation_name="manual_promote_canary",
                service_name="CanaryRolloutService",
                domain="canary",
                audit_on_block=True,
            )

            if not governance.allowed:
                logger.warning(
                    f"[CanaryRollout] Promotion blocked by governance: "
                    f"{governance.block_message}"
                )
                return False

        except Exception as e:
            logger.warning(f"[CanaryRollout] Governance check failed: {e}")
            # Fail-Closed: 체크 실패 시 차단
            return False
    else:
        # bypass_governance=True: Audit 로그 필수
        if not bypass_reason or len(bypass_reason) < 10:
            logger.error("[CanaryRollout] bypass_reason required (min 10 chars)")
            return False

        log_canary_action(
            action="governance_bypass",
            rollout=rollout,
            additional_context={
                "bypass_reason": bypass_reason,
                "requested_by": requested_by,
                "warning": "PIR may be required",
            },
        )
        logger.warning(
            f"[CanaryRollout] Governance bypassed: rollout={rollout_id}, "
            f"reason={bypass_reason}, by={requested_by}"
        )
    # ============================================================

    # 메트릭 검증 (force가 아니면)
    if not force:
        metrics = self._collect_stage_metrics(rollout)
        is_healthy, failure_reason = self._is_stage_healthy(
            rollout.current_stage,
            metrics,
        )

        if not is_healthy:
            logger.warning(f"[CanaryRollout] Promotion blocked: {failure_reason}")
            return False

    # ... 기존 프로모션 로직 ...
```

#### 13.4.3 API View 수정

```python
# api/django/views/canary.py - _handle_promote 수정

def _handle_promote(
    service: CanaryRolloutService,
    rollout_id: str,
    rollout: CanaryRollout,
    request: Request,
) -> tuple[bool, str | None]:
    """프로모션 핸들러."""
    force = request.data.get("force", False)
    bypass_governance = request.data.get("bypass_governance", False)
    bypass_reason = request.data.get("bypass_reason", "")

    # bypass_governance 사용 시 사유 필수
    if bypass_governance and len(bypass_reason) < 10:
        return False, "bypass_reason required (min 10 chars) when bypass_governance=True"

    success = service.promote(
        rollout_id,
        force=force,
        bypass_governance=bypass_governance,
        bypass_reason=bypass_reason,
        requested_by=_get_username(request),
    )

    return success, None if success else "Promotion blocked"
```

---

### 13.5 멈춤 사유 우선순위 (Q8) - 🟡 권장

#### 13.5.1 문제점

메트릭 악화(직접적 장애)와 에러 예산 고갈이 동시 발생 시, 무엇이 근본 원인인지 파악 어려움.

#### 13.5.2 해결 방안: 우선순위 Enum

```python
# services/canary/models.py

from enum import IntEnum

class PauseTriggerPriority(IntEnum):
    """
    Pause 트리거 우선순위.

    높은 값이 더 우선 (근본 원인에 가까움).
    동시 발생 시 가장 높은 우선순위 사유만 기록.
    """
    METRICS = 100       # 직접적 장애 (에러율/레이턴시)
    INTERLOCK = 90      # Safety Interlock
    ERROR_BUDGET = 80   # 거버넌스 (에러 예산)
    CHAOS_GUARD = 70    # Chaos 실험 충돌
    MANUAL = 10         # 수동 중지


# triggered_by 값 → 우선순위 매핑
TRIGGER_PRIORITY_MAP: dict[str, int] = {
    "metrics": PauseTriggerPriority.METRICS,
    "interlock": PauseTriggerPriority.INTERLOCK,
    "error_budget": PauseTriggerPriority.ERROR_BUDGET,
    "chaos_guard": PauseTriggerPriority.CHAOS_GUARD,
    "manual": PauseTriggerPriority.MANUAL,
}
```

```python
# services/canary/service.py

def pause(
    self,
    rollout_id: str,
    reason: str = "",
    triggered_by: str = "manual",
) -> bool:
    """롤아웃 일시 중지 (우선순위 기반 사유 기록)."""
    from selfhealing.services.canary.models import TRIGGER_PRIORITY_MAP

    rollout = self.get_rollout(rollout_id)
    if not rollout or rollout.state != CanaryState.CANARY:
        return False

    # ============================================================
    # [신규] 우선순위 기반 사유 덮어쓰기
    # ============================================================
    existing_trigger = getattr(rollout, "pause_triggered_by", None)
    existing_priority = TRIGGER_PRIORITY_MAP.get(existing_trigger, 0)
    new_priority = TRIGGER_PRIORITY_MAP.get(triggered_by, 0)

    # 더 높은 우선순위인 경우에만 덮어쓰기
    if new_priority >= existing_priority:
        rollout.pause_reason = reason
        rollout.pause_triggered_by = triggered_by
        rollout.paused_at = utc_now()
    else:
        logger.debug(
            f"[CanaryRollout] Keeping existing trigger: "
            f"{existing_trigger} (priority {existing_priority}) > "
            f"{triggered_by} (priority {new_priority})"
        )
    # ============================================================

    rollout.state = CanaryState.PAUSED
    self._save_rollout(rollout)

    log_canary_action(
        action="pause",
        rollout=rollout,
        additional_context={
            "pause_reason": reason,
            "pause_triggered_by": triggered_by,
            "priority": new_priority,
        },
    )

    return True
```

---

### 13.6 알림 및 대시보드 강화 (Q10 & Q11) - 🟡 권장

#### 13.6.1 Slack 알림 추가

```python
# tasks/canary_watchdog.py - auto_promote_eligible 수정

def auto_promote_eligible(self) -> WatchdogResult:
    """자동 프로모션 조건을 충족한 롤아웃 프로모션."""
    result = WatchdogResult()

    if not self.config.enable_auto_promote:
        return result

    # ... 거버넌스 체크 ...

    if not governance.allowed:
        logger.warning(
            f"[Watchdog] Auto promotion blocked by governance: "
            f"{governance.block_message}"
        )

        # ============================================================
        # [신규] Slack 알림 발송
        # ============================================================
        self._send_governance_blocked_notification(
            block_reason=governance.block_reason.value if governance.block_reason else "unknown",
            block_message=governance.block_message,
            pending_count=len(self.service.get_active_rollouts()),
        )
        # ============================================================

        result.governance_blocked = True
        result.governance_block_reason = governance.block_message
        return result

    # ... 기존 로직 ...


def _send_governance_blocked_notification(
    self,
    block_reason: str,
    block_message: str,
    pending_count: int,
) -> None:
    """거버넌스 차단 시 Slack 알림."""
    try:
        from selfhealing.services.unified_notification import (
            get_notification_service,
        )

        service = get_notification_service()

        message = (
            f"⏸️ [Canary Watchdog] 프로모션 대기 중\n"
            f"• 차단 사유: {block_reason}\n"
            f"• 상세: {block_message}\n"
            f"• 대기 중인 롤아웃: {pending_count}개\n"
            f"• 조치: 에러 예산 회복 시 자동 재개됨"
        )

        service.send_slack_message(
            message=message,
            channel=self.config.slack_channel,
            severity="warning",
        )

    except Exception as e:
        logger.warning(f"[Watchdog] Governance blocked notification failed: {e}")
```

#### 13.6.2 Prometheus 메트릭 추가

```python
# services/metrics/definitions.py

# =============================================================================
# Canary Governance Metrics (신규)
# =============================================================================

canary_governance_blocked_total = get_or_create_counter(
    "selfhealing_canary_governance_blocked_total",
    "Total canary promotions blocked by governance",
    ["block_reason"],  # kill_switch, emergency_mode, error_budget
)

canary_pending_promotion_gauge = get_or_create_gauge(
    "selfhealing_canary_pending_promotion",
    "Number of canary rollouts pending promotion due to governance",
    ["reason"],  # error_budget, emergency, etc.
)

canary_governance_bypass_total = get_or_create_counter(
    "selfhealing_canary_governance_bypass_total",
    "Total governance bypasses (Break Glass usage)",
    ["requested_by"],
)
```

```python
# tasks/canary_watchdog.py - 메트릭 기록 추가

def auto_promote_eligible(self) -> WatchdogResult:
    # ... 거버넌스 체크 후 ...

    if not governance.allowed:
        # ============================================================
        # [신규] Prometheus 메트릭 기록
        # ============================================================
        try:
            from selfhealing.services.metrics.definitions import (
                canary_governance_blocked_total,
                canary_pending_promotion_gauge,
            )

            block_reason = governance.block_reason.value if governance.block_reason else "unknown"
            canary_governance_blocked_total.labels(block_reason=block_reason).inc()

            pending_count = len(self.service.get_active_rollouts())
            canary_pending_promotion_gauge.labels(reason=block_reason).set(pending_count)
        except Exception as e:
            logger.debug(f"[Watchdog] Metrics recording failed: {e}")
        # ============================================================

        result.governance_blocked = True
        result.governance_block_reason = governance.block_message
        return result
```

#### 13.6.3 Grafana 대시보드 쿼리

```promql
# 거버넌스 차단 횟수 (시간당)
sum(rate(selfhealing_canary_governance_blocked_total[1h])) by (block_reason)

# 현재 대기 중인 롤아웃 수
selfhealing_canary_pending_promotion

# Break Glass 사용 추이
sum(increase(selfhealing_canary_governance_bypass_total[24h])) by (requested_by)
```

---

### 13.7 재개 시 쓰로틀링 (Q9) - 🟡 권장

#### 13.7.1 문제점

에러 예산 회복 시 수십 개의 롤아웃이 동시 재개되면 Thundering Herd 문제 발생.

#### 13.7.2 해결 방안: 배치 기반 순차 재개

```python
# settings/canary_watchdog.py

class CanaryWatchdogSettings(BaseSettings):
    # ... 기존 설정 ...

    # ============================================================
    # Resume 쓰로틀링 설정
    # ============================================================
    resume_max_batch_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description="한 번에 재개할 최대 롤아웃 수",
    )

    resume_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="배치 간 대기 시간 (초)",
    )

    resume_staggered_enabled: bool = Field(
        default=True,
        description="순차 재개 활성화",
    )
```

```python
# services/canary/service.py

import time

def resume_paused_rollouts_staggered(
    self,
    namespace: str | None = None,
    triggered_by_whitelist: list[str] | None = None,
    max_batch_size: int = 5,
    interval_seconds: int = 60,
) -> list[str]:
    """
    PAUSED 상태의 롤아웃 순차 재개 (Thundering Herd 방지).

    Args:
        namespace: 네임스페이스 필터
        triggered_by_whitelist: 재개 허용 목록
        max_batch_size: 한 배치당 최대 재개 수
        interval_seconds: 배치 간 대기 시간

    Returns:
        재개된 롤아웃 ID 목록
    """
    # 재개 대상 수집
    candidates = []
    for rollout in self.get_active_rollouts():
        if rollout.state != CanaryState.PAUSED:
            continue
        if namespace and getattr(rollout, "namespace", None) != namespace:
            continue
        triggered_by = getattr(rollout, "pause_triggered_by", None)
        if triggered_by_whitelist is not None:
            if triggered_by not in triggered_by_whitelist:
                continue
        candidates.append(rollout.id)

    if not candidates:
        return []

    resumed = []

    # 배치 단위로 순차 재개
    for i in range(0, len(candidates), max_batch_size):
        batch = candidates[i:i + max_batch_size]

        for rollout_id in batch:
            if self.resume(rollout_id):
                resumed.append(rollout_id)

        # 마지막 배치가 아니면 대기
        if i + max_batch_size < len(candidates):
            logger.info(
                f"[CanaryRollout] Resumed batch {i // max_batch_size + 1}, "
                f"waiting {interval_seconds}s before next batch"
            )
            time.sleep(interval_seconds)

    logger.info(
        f"[CanaryRollout] Staggered resume complete: "
        f"{len(resumed)}/{len(candidates)} rollouts resumed"
    )

    return resumed
```

#### 13.7.3 RecoveryCoordinator 연동

```python
# services/coordination/recovery_coordinator.py

def _handle_canary_resume(
    self,
    session: RecoverySession,
    step: RecoveryStep,
) -> dict[str, Any]:
    """Canary 롤아웃 재개 (쓰로틀링 지원)."""
    resume_paused_only = step.params.get("resume_paused_only", True)
    triggered_by_whitelist = step.params.get("triggered_by_whitelist", ["error_budget"])

    # ============================================================
    # [신규] 쓰로틀링 파라미터
    # ============================================================
    staggered_enabled = step.params.get("staggered_enabled", True)
    max_batch_size = step.params.get("max_batch_size", 5)
    interval_seconds = step.params.get("interval_seconds", 60)
    # ============================================================

    try:
        from selfhealing.services.canary import get_canary_service
        service = get_canary_service()

        if resume_paused_only:
            if staggered_enabled:
                # 순차 재개
                resumed = service.resume_paused_rollouts_staggered(
                    namespace=session.namespace,
                    triggered_by_whitelist=triggered_by_whitelist,
                    max_batch_size=max_batch_size,
                    interval_seconds=interval_seconds,
                )
            else:
                # 일괄 재개 (기존)
                resumed = service.resume_paused_rollouts(
                    namespace=session.namespace,
                    triggered_by_whitelist=triggered_by_whitelist,
                )
        else:
            resumed = service.resume_all_rollouts(session.namespace)

        return {
            "success": True,
            "resumed_count": len(resumed) if resumed else 0,
            "staggered": staggered_enabled,
            "max_batch_size": max_batch_size,
        }
    except (ImportError, AttributeError):
        return {"success": True, "resumed_count": 0, "skipped": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
```

---

### 13.8 Redis 데이터 하위 호환성 (Q1) - 🔴 필수

#### 13.8.1 현재 패턴 확인

**이미 `.get()` 패턴 사용 중** (`services/canary/service.py` Lines 830-853):
```python
def _deserialize_rollout(self, data: dict[str, Any]) -> CanaryRollout:
    return CanaryRollout(
        # ...
        duration_minutes=s.get("duration_minutes", 5),  # ✅ 기본값
        auto_promote=s.get("auto_promote", True),       # ✅ 기본값
        rollback_reason=data.get("rollback_reason"),    # ✅ None 허용
    )
```

#### 13.8.2 신규 필드 추가 (호환성 유지)

```python
# services/canary/service.py - _deserialize_rollout 수정

def _deserialize_rollout(self, data: dict[str, Any]) -> CanaryRollout:
    """딕셔너리에서 CanaryRollout 복원 (하위 호환성 보장)."""
    return CanaryRollout(
        id=data["id"],
        config_type=data["config_type"],
        previous_values=data["previous_values"],
        new_values=data["new_values"],
        state=CanaryState(data["state"]),
        current_stage_index=data["current_stage_index"],
        stages=[
            CanaryStage(
                name=s["name"],
                clusters=s["clusters"],
                percentage=s["percentage"],
                duration_minutes=s.get("duration_minutes", 5),
                auto_promote=s.get("auto_promote", True),
                error_rate_threshold=s.get("error_rate_threshold", 0.05),
                latency_increase_threshold=s.get("latency_increase_threshold", 0.5),
            )
            for s in data["stages"]
        ],
        created_by=data["created_by"],
        created_at=datetime.fromisoformat(data["created_at"]),
        reason=data["reason"],
        completed_at=(
            datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at") else None
        ),
        rollback_reason=data.get("rollback_reason"),

        # ============================================================
        # [신규] 하위 호환성 필드 (없으면 None)
        # ============================================================
        pause_reason=data.get("pause_reason"),
        pause_triggered_by=data.get("pause_triggered_by"),
        paused_at=(
            datetime.fromisoformat(data["paused_at"])
            if data.get("paused_at") else None
        ),
        # ============================================================
    )


def _serialize_rollout(self, rollout: CanaryRollout) -> dict[str, Any]:
    """CanaryRollout을 딕셔너리로 직렬화."""
    return {
        "id": rollout.id,
        "config_type": rollout.config_type,
        # ... 기존 필드 ...

        # ============================================================
        # [신규] pause 관련 필드
        # ============================================================
        "pause_reason": rollout.pause_reason,
        "pause_triggered_by": rollout.pause_triggered_by,
        "paused_at": (
            rollout.paused_at.isoformat()
            if rollout.paused_at else None
        ),
        # ============================================================
    }
```

#### 13.8.3 테스트 케이스

```python
# tests/services/canary/test_backward_compatibility.py

class TestRedisBackwardCompatibility:
    """Redis 데이터 하위 호환성 테스트."""

    def test_deserialize_without_pause_fields(self):
        """구버전 데이터 (pause 필드 없음) 역직렬화."""
        old_data = {
            "id": "test-123",
            "config_type": "circuit_breaker",
            "state": "paused",
            # pause_reason, pause_triggered_by, paused_at 없음
        }

        service = CanaryRolloutService()
        rollout = service._deserialize_rollout(old_data)

        assert rollout.pause_reason is None
        assert rollout.pause_triggered_by is None
        assert rollout.paused_at is None

    def test_deserialize_with_pause_fields(self):
        """신버전 데이터 (pause 필드 있음) 역직렬화."""
        new_data = {
            "id": "test-456",
            "config_type": "circuit_breaker",
            "state": "paused",
            "pause_reason": "Error budget low",
            "pause_triggered_by": "error_budget",
            "paused_at": "2026-02-04T10:00:00+00:00",
        }

        service = CanaryRolloutService()
        rollout = service._deserialize_rollout(new_data)

        assert rollout.pause_reason == "Error budget low"
        assert rollout.pause_triggered_by == "error_budget"
        assert rollout.paused_at is not None
```

---

## 14. 구현 우선순위 정리

| 우선순위 | 항목 | 섹션 | 예상 공수 |
|---------|------|------|----------|
| 🔴 P0 | Redis 호환성 | §13.8 | 0.5일 |
| 🔴 P0 | Zombie 판정 제외 | §13.1 | 0.5일 |
| 🔴 P0 | Resume 필터링 | §13.3 | 1일 |
| 🔴 P1 | Break Glass | §13.2 | 1일 |
| 🟡 P2 | 수동 프로모션 Gate | §13.4 | 1일 |
| 🟡 P2 | 멈춤 사유 우선순위 | §13.5 | 0.5일 |
| 🟡 P2 | 알림/대시보드 | §13.6 | 1일 |
| 🟡 P3 | 재개 쓰로틀링 | §13.7 | 1일 |

**총 예상 공수:** 6.5일

---

## 15. 체크리스트

### 15.1 필수 구현 (P0)

- [x] `CanaryRollout` 모델에 `pause_reason`, `pause_triggered_by`, `paused_at` 필드 추가
- [x] `_deserialize_rollout`에 `.get()` 기본값 처리 추가
- [x] `_serialize_rollout`에 신규 필드 추가
- [x] `_check_zombie`에 `ZOMBIE_EXEMPT_TRIGGERS` 제외 로직 추가
- [x] `resume_paused_rollouts`에 `triggered_by_whitelist` 파라미터 추가
- [x] `_handle_canary_resume`에 whitelist 기본값 `["error_budget"]` 적용
- [x] `GovernanceSettings`에 `break_glass_enabled` 필드 추가
- [x] `check_all_governance` 최상단에 Break Glass 체크 추가

### 15.2 권장 구현 (P2-P3)

- [x] `promote()`에 `bypass_governance`, `bypass_reason` 파라미터 추가
- [x] `PauseTriggerPriority` Enum 및 우선순위 로직 추가
- [x] `_send_governance_blocked_notification` 메서드 추가
- [x] `canary_governance_blocked_total` Counter 메트릭 추가
- [x] `canary_pending_promotion_gauge` Gauge 메트릭 추가
- [x] `resume_paused_rollouts_staggered` 메서드 추가
- [x] RecoveryCoordinator에 `staggered_enabled`, `max_batch_size` 파라미터 추가

### 15.3 테스트

- [x] `test_zombie_exempt_error_budget` - Error Budget PAUSED는 Zombie 아님
- [x] `test_resume_whitelist_filter` - Whitelist 외 사유는 재개 안 됨
- [x] `test_break_glass_bypass` - Break Glass 시 모든 체크 우회
- [x] `test_redis_backward_compatibility` - 구버전 데이터 역직렬화
- [x] `test_staggered_resume` - 순차 재개 동작 확인

---

## 16. 추가 보완 제안

> **목적**: 172 문서 구현의 완성도 및 운영 안정성 강화

---

### 16.1 테스트 커버리지 강화

#### 16.1.1 현재 상태

§15.3에 기본 테스트 케이스가 정의되어 있으나, **Zombie 판정 경계값 테스트**가 부족합니다.

#### 16.1.2 추가 테스트 케이스

**코드 근거** (`tasks/canary_watchdog.py` - §13.1 구현):
```python
ZOMBIE_EXEMPT_TRIGGERS: list[str] = ["error_budget", "governance"]
```

```python
# tests/unit/tasks/test_canary_watchdog_zombie.py

class TestZombieExemption:
    """Zombie 판정 제외 테스트 - §13.1 검증."""

    def test_error_budget_paused_not_zombie(self, watchdog, canary_service):
        """error_budget 사유로 PAUSED된 롤아웃은 Zombie 아님."""
        rollout = canary_service.create_rollout(
            config_type="circuit_breaker",
            reason="Test",
        )
        # pause_triggered_by가 면제 목록에 있음
        canary_service.pause(rollout.id, triggered_by="error_budget")

        # 40분 경과 (zombie_threshold_minutes=30 초과)
        with freeze_time(datetime.utcnow() + timedelta(minutes=40)):
            result = watchdog._check_zombie(rollout, datetime.utcnow())

        assert result is None  # Zombie 아님

    def test_manual_paused_is_zombie(self, watchdog, canary_service):
        """manual 사유로 PAUSED된 롤아웃은 Zombie 맞음."""
        rollout = canary_service.create_rollout(
            config_type="circuit_breaker",
            reason="Test",
        )
        # manual은 면제 목록에 없음
        canary_service.pause(rollout.id, triggered_by="manual")

        # 40분 경과
        with freeze_time(datetime.utcnow() + timedelta(minutes=40)):
            result = watchdog._check_zombie(rollout, datetime.utcnow())

        assert result is not None  # Zombie 맞음
        assert "Paused for" in result.reason

    def test_metrics_paused_is_zombie(self, watchdog, canary_service):
        """metrics 사유로 PAUSED된 롤아웃은 Zombie 맞음.

        메트릭 악화로 인한 PAUSED는 문제 상황이므로 Zombie 처리 필요.
        장시간 방치 시 수동 개입 필요.
        """
        rollout = canary_service.create_rollout(
            config_type="circuit_breaker",
            reason="Test",
        )
        # metrics는 면제 목록에 없음
        canary_service.pause(rollout.id, triggered_by="metrics")

        # 40분 경과
        with freeze_time(datetime.utcnow() + timedelta(minutes=40)):
            result = watchdog._check_zombie(rollout, datetime.utcnow())

        assert result is not None  # Zombie 맞음

    def test_governance_paused_not_zombie(self, watchdog, canary_service):
        """governance 사유로 PAUSED된 롤아웃은 Zombie 아님.

        거버넌스 체크 실패(Kill Switch, Emergency 등)로 인한 PAUSED는
        정상적인 대기 상태이므로 Zombie 아님.
        """
        rollout = canary_service.create_rollout(
            config_type="circuit_breaker",
            reason="Test",
        )
        canary_service.pause(rollout.id, triggered_by="governance")

        # 40분 경과
        with freeze_time(datetime.utcnow() + timedelta(minutes=40)):
            result = watchdog._check_zombie(rollout, datetime.utcnow())

        assert result is None  # Zombie 아님
```

---

### 16.2 설정 통합 (CanaryGovernanceSettings)

#### 16.2.1 현재 상태

§13 구현 코드에서 하드코딩된 값들이 분산되어 있습니다:
- `ZOMBIE_EXEMPT_TRIGGERS = ["error_budget", "governance"]` (§13.1)
- `triggered_by_whitelist=["error_budget"]` 기본값 (§13.3)
- `bypass_governance` 기본값 `False` (§13.4)
- `PauseTriggerPriority` 우선순위 값 (§13.5)

#### 16.2.2 기존 Settings 패턴

**코드 근거** (`settings/error_budget.py`):
```python
class ErrorBudgetSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ERRORBUDGET_",
        env_file=".env",
        extra="ignore",
    )

    threshold_healthy: float = Field(default=75.0, ge=50.0, le=100.0)
    # ...
```

#### 16.2.3 신규 Settings 파일 제안

```python
# settings/canary_governance.py (신규)

"""
Canary Governance Settings.

Centralizes all governance-related settings for Canary rollouts.
Extracted from document 172_CANARY_ERROR_BUDGET_GATE.

Environment Variables:
    SELFHEALING_CANARY_GOV_ZOMBIE_EXEMPT_TRIGGERS='["error_budget","governance"]'
    SELFHEALING_CANARY_GOV_RESUME_WHITELIST_TRIGGERS='["error_budget"]'
    SELFHEALING_CANARY_GOV_MANUAL_PROMOTE_GOVERNANCE_CHECK=true
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CanaryGovernanceSettings(BaseSettings):
    """
    Canary Governance 설정.

    §172 문서의 모든 하드코딩 값을 중앙 집중화.
    """

    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_CANARY_GOV_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )

    # ==========================================================================
    # §13.1 Zombie 판정 제외 설정
    # ==========================================================================
    zombie_exempt_triggers: list[str] = Field(
        default=["error_budget", "governance"],
        description="Zombie 판정에서 제외할 pause_triggered_by 값 목록",
    )

    # ==========================================================================
    # §13.3 Resume Whitelist 설정
    # ==========================================================================
    resume_whitelist_triggers: list[str] = Field(
        default=["error_budget"],
        description="자동 재개 허용 pause_triggered_by 목록",
    )

    # ==========================================================================
    # §13.4 수동 프로모션 거버넌스 체크 설정
    # ==========================================================================
    governance_check_on_manual_promote: bool = Field(
        default=True,
        description="수동 프로모션 시에도 거버넌스 체크 적용 여부",
    )

    # ==========================================================================
    # §13.5 Pause 트리거 우선순위
    # ==========================================================================
    pause_trigger_priority: dict[str, int] = Field(
        default={
            "metrics": 100,       # 직접적 장애 (에러율/레이턴시)
            "interlock": 90,      # Safety Interlock
            "error_budget": 80,   # 거버넌스 (에러 예산)
            "chaos_guard": 70,    # Chaos 실험 충돌
            "manual": 10,         # 수동 중지
        },
        description="pause_triggered_by 값별 우선순위 (높을수록 우선)",
    )

    # ==========================================================================
    # §13.7 Resume 쓰로틀링 설정
    # ==========================================================================
    resume_max_batch_size: int = Field(
        default=5,
        ge=1,
        le=50,
        description="한 번에 재개할 최대 롤아웃 수",
    )
    resume_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="배치 간 대기 시간 (초)",
    )
    resume_staggered_enabled: bool = Field(
        default=True,
        description="순차 재개 활성화",
    )


# Singleton
_settings: CanaryGovernanceSettings | None = None


def get_canary_governance_settings() -> CanaryGovernanceSettings:
    """CanaryGovernanceSettings 싱글톤 반환."""
    global _settings
    if _settings is None:
        _settings = CanaryGovernanceSettings()
    return _settings
```

#### 16.2.4 마이그레이션 가이드

1. 기존 하드코딩 값을 Settings로 교체:
```python
# Before (§13.1)
ZOMBIE_EXEMPT_TRIGGERS = ["error_budget", "governance"]

# After
from selfhealing.settings.canary_governance import get_canary_governance_settings
settings = get_canary_governance_settings()
exempt_triggers = settings.zombie_exempt_triggers
```

2. 환경변수로 런타임 조정 가능:
```bash
# 예: governance도 Zombie로 판정하려면
SELFHEALING_CANARY_GOV_ZOMBIE_EXEMPT_TRIGGERS='["error_budget"]'
```

---

### 16.3 감사(Audit) 강화 - Break Glass 기록

#### 16.3.1 현재 상태

**코드 근거** (`services/canary/audit.py` Lines 36-48):
```python
CANARY_ACTIONS = [
    "create",
    "start",
    "promote",
    "rollback",
    "pause",
    "resume",
    "complete",
    "panic_rollback",
    "cancel",
    "force_promote",
]
```

§13.2에서 Break Glass(거버넌스 우회) 기능을 추가했으나, `CANARY_ACTIONS`에 해당 액션이 없습니다.

#### 16.3.2 해결 방안

```python
# services/canary/audit.py - CANARY_ACTIONS 확장

CANARY_ACTIONS = [
    "create",
    "start",
    "promote",
    "rollback",
    "pause",
    "resume",
    "complete",
    "panic_rollback",
    "cancel",
    "force_promote",
    # ============================================================
    # [신규] §172 거버넌스 관련 액션
    # ============================================================
    "governance_blocked",   # 거버넌스 체크로 차단됨
    "governance_bypass",    # Break Glass로 우회함 (PIR 필수)
    # ============================================================
]
```

#### 16.3.3 Audit 로그 스키마

```python
# Break Glass 사용 시 Audit 로그 예시

log_canary_action(
    action="governance_bypass",  # 신규 액션
    rollout=rollout,
    additional_context={
        "bypass_reason": "긴급 보안 패치 배포",
        "requested_by": "admin@company.com",
        "ticket_id": "INC-12345",
        "bypassed_checks": ["error_budget", "kill_switch"],
        "pir_required": True,  # Post-Incident Review 필수
        "break_glass_activated_at": "2026-02-04T10:30:00Z",
    },
)
```

#### 16.3.4 컴플라이언스 요구사항

| 필드 | 필수 | 설명 |
|------|------|------|
| `bypass_reason` | ✅ | 최소 10자 이상 사유 |
| `requested_by` | ✅ | 요청자 이메일/ID |
| `ticket_id` | 🟡 | 인시던트 티켓 (정책에 따라) |
| `pir_required` | ✅ | PIR 필요 여부 (항상 true) |
| `break_glass_activated_at` | ✅ | Break Glass 활성화 시각 |

---

## 17. 최종 체크리스트 (추가 보완 포함)

### 17.1 추가 테스트 (§16.1)

- [x] `TestZombieExemption` 클래스 추가
- [x] `test_error_budget_paused_not_zombie` 구현
- [x] `test_manual_paused_is_zombie` 구현
- [x] `test_metrics_paused_is_zombie` 구현
- [x] `test_governance_paused_not_zombie` 구현

### 17.2 설정 통합 (§16.2)

- [x] `settings/canary_governance.py` 파일 생성
- [x] `CanaryGovernanceSettings` 클래스 구현
- [x] 기존 하드코딩 값 마이그레이션
- [x] 환경변수 문서화

### 17.3 Audit 강화 (§16.3)

- [x] `CANARY_ACTIONS`에 `governance_blocked` 추가
- [x] `CANARY_ACTIONS`에 `governance_bypass` 추가
- [x] Break Glass Audit 스키마 구현
- [x] 컴플라이언스 검증 테스트 추가

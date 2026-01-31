# 172. Canary 자동 프로모션 Error Budget Gate 적용

> **작성일**: 2026-01-31
> **상태**: 📋 구현 예정
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

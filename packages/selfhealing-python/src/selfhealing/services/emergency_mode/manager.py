"""
Graceful Degradation Manager.

Main emergency mode manager with activation/deactivation and gradual recovery.
"""

from __future__ import annotations

import structlog
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from .enums import EMERGENCY_LEVEL_RULES, EmergencyLevel
from .models import EmergencyState, RecoveryGateConfig
from .recovery_gate import RecoveryGate

# Drift Detection 메트릭
try:
    from selfhealing.metrics.drift_metrics import (
        record_emergency_cache_drift,
        record_emergency_cache_load,
        record_emergency_cache_stale,
        update_emergency_cache_age,
    )

    HAS_DRIFT_METRICS = True
except ImportError:
    HAS_DRIFT_METRICS = False
    record_emergency_cache_stale = lambda: None
    record_emergency_cache_drift = lambda: None
    update_emergency_cache_age = lambda *args: None
    record_emergency_cache_load = lambda *args: None

logger = structlog.get_logger()


class GracefulDegradationManager:
    """
    비상 모드 관리자 - 단계별 비상 모드 진입/해제.

    Thread-safe 싱글톤으로 구현.

    Usage:
        manager = GracefulDegradationManager()

        # 수동 비상 모드 활성화
        manager.activate_manual(
            level=EmergencyLevel.LEVEL_2,
            reason="High error rate",
            activated_by="admin",
            duration_minutes=30,
        )

        # 현재 상태 확인
        state = manager.get_state()

        # 비상 모드 해제
        manager.deactivate(deactivated_by="admin")
    """

    _instance: GracefulDegradationManager | None = None
    _lock = threading.Lock()

    def __new__(cls) -> GracefulDegradationManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    instance = super().__new__(cls)
                    instance._init()
                    cls._instance = instance
        return cls._instance

    def _init(self):
        """초기화."""
        self._state_lock = threading.RLock()
        self._state = EmergencyState()
        self._recovery_gate = RecoveryGate()
        self._recovery_thread: threading.Thread | None = None
        self._stop_recovery = threading.Event()
        self._history: list[dict[str, Any]] = []

        # Before Mutation Snapshot: 롤백용 이전 상태 저장 (최대 10개)
        self._previous_states: list[dict[str, Any]] = []

        # TTL 기반 캐시 설정 (Check on Use 패턴)
        self._cache_ttl_seconds: int = 30  # 캐시 유효 시간
        self._last_load_time: datetime | None = None

        # 이벤트 버스 구독 등록
        self._register_event_handlers()

        # 백엔드에서 상태 로드 시도
        self._load_state(reason="startup")

    def _register_event_handlers(self):
        """이벤트 버스 핸들러 등록 (캐시 무효화용)."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            # 다른 프로세스/인스턴스에서 발생한 이벤트 수신 시 캐시 즉시 무효화
            bus.subscribe(
                EventType.EMERGENCY_LEVEL_CHANGED,
                self._on_external_level_changed,
            )
        except Exception as e:
            logger.debug(
                "emergency_mode.event_bus_registration_skipped",
                error=e,
            )

    def _on_external_level_changed(self, event) -> None:
        """외부 이벤트 수신 시 캐시 무효화."""
        # 자신이 발행한 이벤트가 아닌 경우에만 캐시 무효화
        if event.source != "emergency_manager":
            self._invalidate_cache()
            # v6.3.0: 다음 로드 시 reason 설정을 위해 플래그 설정 필요 없음
            # _load_state에서 "invalidated" reason으로 로드됨
            logger.debug("emergency_mode.cache_invalidated_external_event")

    def _invalidate_cache(self) -> None:
        """캐시 무효화 (다음 조회 시 StateBackend 재조회)."""
        with self._state_lock:
            self._last_load_time = None

    def _is_cache_valid(self) -> bool:
        """캐시 유효성 확인."""
        if self._last_load_time is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._last_load_time).total_seconds()
        # v6.3.0: 캐시 age 메트릭 업데이트
        update_emergency_cache_age(elapsed)
        return elapsed < self._cache_ttl_seconds

    def _ensure_fresh_state(self) -> None:
        """캐시 TTL 확인 후 필요시 StateBackend 재조회 (Check on Use 패턴)."""
        if not self._is_cache_valid():
            # v6.3.0: Stale 메트릭 기록
            if self._last_load_time is not None:
                record_emergency_cache_stale()
            self._load_state()

    def _check_cache_drift(self) -> bool:
        """
        캐시와 백엔드 상태 비교 후 drift 감지.

        v6.3.0: Drift Detection 구현

        Returns:
            True if drift detected, False otherwise
        """
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            backend_data = backend.get("emergency_mode")

            if backend_data is None:
                return False

            backend_state = EmergencyState.from_dict(backend_data)

            # 캐시된 상태와 백엔드 상태 비교
            if self._state.level != backend_state.level:
                record_emergency_cache_drift()
                logger.warning(
                    f"[EmergencyMode] Drift detected: "
                    f"cached={self._state.level.name}, backend={backend_state.level.name}"
                )
                # 자동 동기화: 백엔드 상태로 업데이트
                self._state = backend_state
                self._last_load_time = datetime.now(timezone.utc)
                return True

            if self._state.is_active != backend_state.is_active:
                record_emergency_cache_drift()
                logger.warning(
                    f"[EmergencyMode] Drift detected: "
                    f"cached.is_active={self._state.is_active}, "
                    f"backend.is_active={backend_state.is_active}"
                )
                self._state = backend_state
                self._last_load_time = datetime.now(timezone.utc)
                return True

        except Exception as e:
            logger.debug(
                "emergency_mode.drift_check_failed",
                error=e,
            )

        return False

    def _load_state(self, reason: str = "expired"):
        """백엔드에서 상태 로드."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            data = backend.get("emergency_mode")
            if data:
                self._state = EmergencyState.from_dict(data)
                logger.info(
                    f"[EmergencyMode] Loaded state: level={self._state.level.name}, "
                    f"is_active={self._state.is_active}"
                )
            # 로드 시간 기록 (TTL 캐시용)
            self._last_load_time = datetime.now(timezone.utc)
            # v6.3.0: 로드 메트릭 기록
            record_emergency_cache_load(reason)
        except Exception as e:
            logger.warning(
                "emergency_mode.load_state",
                error=e,
            )

    def _save_state(self):
        """상태 저장."""
        try:
            from selfhealing.core.state_backend import get_state_backend

            backend = get_state_backend()
            backend.set("emergency_mode", self._state.to_dict())
        except Exception as e:
            logger.error(
                "emergency_mode.failed_save_state",
                error=e,
            )

    # -------------------------------------------------------------------------
    # State Access
    # -------------------------------------------------------------------------

    def get_state(self) -> EmergencyState:
        """현재 상태 조회."""
        with self._state_lock:
            # v6.3.0: 캐시 age 메트릭 업데이트
            if self._last_load_time:
                age = (
                    datetime.now(timezone.utc) - self._last_load_time
                ).total_seconds()
                update_emergency_cache_age(age)

            # TTL 확인 및 필요시 StateBackend 재조회 (Check on Use 패턴)
            self._ensure_fresh_state()
            # 만료 확인
            self._check_expiration()
            return EmergencyState.from_dict(self._state.to_dict())

    def get_current_level(self) -> EmergencyLevel:
        """현재 비상 모드 레벨 조회."""
        with self._state_lock:
            # TTL 확인 및 필요시 StateBackend 재조회 (Check on Use 패턴)
            self._ensure_fresh_state()
            self._check_expiration()
            return self._state.level

    def is_active(self) -> bool:
        """비상 모드 활성화 여부."""
        with self._state_lock:
            self._check_expiration()
            return self._state.is_active

    def _check_expiration(self):
        """만료 확인 및 자동 해제."""
        if not self._state.is_active or not self._state.expires_at:
            return

        try:
            expires_at = datetime.fromisoformat(self._state.expires_at)
            if datetime.now(timezone.utc) > expires_at:
                logger.info("emergency_mode.auto_expired_deactivating")
                self._do_deactivate("system", "Auto-expired")
        except Exception as e:
            logger.error(
                "emergency_mode.expiration_check_failed",
                error=e,
            )

    # -------------------------------------------------------------------------
    # Before Mutation Snapshot (롤백 지원)
    # -------------------------------------------------------------------------

    def _save_previous_state(self, action: str):
        """
        상태 변경 전 스냅샷 저장.

        롤백 가능하도록 이전 상태를 저장합니다.
        최대 10개까지 유지합니다.
        """
        snapshot = {
            "state": self._state.to_dict(),
            "action": action,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._previous_states.append(snapshot)

        # 최대 10개 유지
        if len(self._previous_states) > 10:
            self._previous_states = self._previous_states[-10:]

        logger.debug(
            "emergency_mode.saved_pre_mutation_snapshot",
            action=action,
        )

    def get_previous_states(self) -> list[dict[str, Any]]:
        """
        이전 상태 스냅샷 목록 조회.

        Returns:
            스냅샷 목록 (최신순)
        """
        with self._state_lock:
            return list(reversed(self._previous_states))

    def rollback_to_previous(self, index: int = 0) -> EmergencyState | None:
        """
        이전 상태로 롤백.

        Args:
            index: 롤백할 스냅샷 인덱스 (0=가장 최근, 1=그 이전...)

        Returns:
            롤백된 상태, 실패 시 None
        """
        with self._state_lock:
            if not self._previous_states:
                logger.warning("emergency_mode.no_previous_state_rollback")
                return None

            # 역순 인덱스 (0=가장 최근)
            actual_index = len(self._previous_states) - 1 - index
            if actual_index < 0:
                logger.warning(
                    "emergency_mode.invalid_rollback_index",
                    index=index,
                )
                return None

            snapshot = self._previous_states[actual_index]
            old_state_dict = snapshot["state"]

            # 현재 상태를 스냅샷에 저장 (롤백의 롤백 가능)
            self._save_previous_state("rollback")

            # 상태 복원
            self._state = EmergencyState.from_dict(old_state_dict)
            self._save_state()

            logger.warning(
                f"[EmergencyMode] Rolled back to snapshot at {snapshot['timestamp']}, "
                f"original action={snapshot['action']}"
            )

            return self.get_state()

    # -------------------------------------------------------------------------
    # Tier Multiplier
    # -------------------------------------------------------------------------

    def get_tier_multiplier(self, tier_id: str) -> float:
        """
        현재 비상 모드 레벨에 따른 티어 배율 반환.

        Args:
            tier_id: 티어 ID (critical, standard, non_essential)

        Returns:
            배율 (0.0 ~ 1.0)
        """
        with self._state_lock:
            self._check_expiration()
            level = self._state.level
            rules = EMERGENCY_LEVEL_RULES.get(
                level, EMERGENCY_LEVEL_RULES[EmergencyLevel.NORMAL]
            )
            return rules.get(tier_id, 1.0)

    # -------------------------------------------------------------------------
    # Manual Activation/Deactivation
    # -------------------------------------------------------------------------

    def activate_manual(
        self,
        level: EmergencyLevel,
        reason: str,
        activated_by: str,
        duration_minutes: int | None = None,
        # Chaos-Aware 메타데이터
        is_chaos_experiment: bool = False,
        experiment_id: str | None = None,
    ) -> EmergencyState:
        """
        수동 비상 모드 활성화.

        Args:
            level: 비상 모드 레벨
            reason: 활성화 사유 (필수)
            activated_by: 활성화한 사용자
            duration_minutes: 자동 만료 시간 (분), None이면 수동 해제 필요
            is_chaos_experiment: 카오스 실험에 의한 활성화 여부
            experiment_id: 관련 카오스 실험 ID

        Returns:
            새 상태
        """
        if not reason:
            raise ValueError("reason is required")

        if level == EmergencyLevel.NORMAL:
            # NORMAL로 설정하는 것은 비활성화와 동일
            return self.deactivate(activated_by)

        with self._state_lock:
            now = datetime.now(timezone.utc)

            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_state("activate_manual")

            old_state = EmergencyState.from_dict(self._state.to_dict())

            self._state.level = level
            self._state.is_active = True
            self._state.activated_at = now.isoformat()
            self._state.activated_by = activated_by
            self._state.activation_reason = reason
            self._state.is_auto_triggered = False
            self._state.deactivated_at = None
            self._state.deactivated_by = None

            # Chaos-Aware 메타데이터 설정
            if is_chaos_experiment:
                self._state.metadata = {
                    "is_chaos_experiment": True,
                    "experiment_id": experiment_id,
                    "classification": "chaos_induced_test",
                }
            else:
                self._state.metadata = {
                    "is_chaos_experiment": False,
                    "classification": "infrastructure_incident",
                }

            if duration_minutes:
                self._state.expires_at = (
                    now + timedelta(minutes=duration_minutes)
                ).isoformat()
            else:
                self._state.expires_at = None

            self._save_state()
            self._log_history("ACTIVATED", old_state, self._state)
            self._log_audit("activate", activated_by, reason)

            # ConfigHistory에 저장
            self._save_state_to_config_history(
                action="ACTIVATED",
                state=self._state,
                changed_by=activated_by,
                reason=reason,
            )

            logger.warning(
                f"[EmergencyMode] ACTIVATED by {activated_by}: "
                f"level={level.name}, reason={reason}, "
                f"expires_at={self._state.expires_at or 'manual'}"
                f"{', chaos_experiment=True' if is_chaos_experiment else ''}"
            )

            # Event Bus 발행: 다른 컴포넌트에 알림
            self._emit_level_changed_event(
                new_level=level,
                previous_level=old_state.level,
                reason=reason,
            )

            return self.get_state()

    def activate_auto(
        self,
        level: EmergencyLevel,
        reason: str,
        duration_minutes: int = 30,
    ) -> EmergencyState:
        """
        자동 비상 모드 활성화 (시스템에 의해 트리거).

        Args:
            level: 비상 모드 레벨
            reason: 자동 감지 사유
            duration_minutes: 자동 만료 시간 (기본 30분)

        Returns:
            새 상태
        """
        with self._state_lock:
            now = datetime.now(timezone.utc)

            # 이미 더 높은 레벨이면 무시
            if self._state.is_active and self._state.level.value >= level.value:
                logger.info(
                    f"[EmergencyMode] Auto-trigger ignored: "
                    f"current level {self._state.level.name} >= requested {level.name}"
                )
                return self.get_state()

            # Before Mutation Snapshot: 변경 전 상태 저장
            self._save_previous_state("activate_auto")

            old_state = EmergencyState.from_dict(self._state.to_dict())

            self._state.level = level
            self._state.is_active = True
            self._state.activated_at = now.isoformat()
            self._state.activated_by = "system"
            self._state.activation_reason = reason
            self._state.is_auto_triggered = True
            self._state.expires_at = (
                now + timedelta(minutes=duration_minutes)
            ).isoformat()

            self._save_state()
            self._log_history("AUTO_ACTIVATED", old_state, self._state)
            self._log_audit("auto_activate", "system", reason)

            # ConfigHistory에 저장
            self._save_state_to_config_history(
                action="AUTO_ACTIVATED",
                state=self._state,
                changed_by="system",
                reason=reason,
            )

            logger.warning(
                f"[EmergencyMode] AUTO-ACTIVATED: level={level.name}, "
                f"reason={reason}, expires_in={duration_minutes}min"
            )

            # Event Bus 발행: 다른 컴포넌트에 알림
            self._emit_level_changed_event(
                new_level=level,
                previous_level=old_state.level,
                reason=reason,
            )

            return self.get_state()

    def deactivate(
        self,
        deactivated_by: str,
        reason: str = "",
        force: bool = False,
    ) -> EmergencyState:
        """
        비상 모드 해제.

        Args:
            deactivated_by: 해제한 사용자
            reason: 해제 사유 (선택)
            force: 복구 조건 무시하고 강제 해제

        Returns:
            새 상태
        """
        with self._state_lock:
            if not self._state.is_active:
                return self.get_state()

            # 복구 조건 확인 (force가 아닌 경우)
            if not force and self._recovery_gate.config.require_metrics_stable:
                allowed, check_reason = self._recovery_gate.check_recovery_allowed()
                if not allowed:
                    logger.warning(
                        f"[EmergencyMode] Deactivation blocked: {check_reason}. "
                        f"Use force=True to override."
                    )
                    raise ValueError(f"Recovery not allowed: {check_reason}")

            return self._do_deactivate(deactivated_by, reason or "Manual deactivation")

    def _do_deactivate(self, deactivated_by: str, reason: str) -> EmergencyState:
        """실제 비상 모드 해제 수행."""
        # Before Mutation Snapshot: 변경 전 상태 저장
        self._save_previous_state("deactivate")

        now = datetime.now(timezone.utc)
        old_state = EmergencyState.from_dict(self._state.to_dict())

        self._state.level = EmergencyLevel.NORMAL
        self._state.is_active = False
        self._state.deactivated_at = now.isoformat()
        self._state.deactivated_by = deactivated_by
        self._state.is_recovering = False
        self._state.recovery_started_at = None
        self._state.target_level = None

        self._save_state()
        self._log_history("DEACTIVATED", old_state, self._state)
        self._log_audit("deactivate", deactivated_by, reason)

        # ConfigHistory에 저장
        self._save_state_to_config_history(
            action="DEACTIVATED",
            state=self._state,
            changed_by=deactivated_by,
            reason=reason,
        )

        logger.info(
            "emergency_mode.deactivated",
            deactivated_by=deactivated_by,
            reason=reason,
        )

        # Event Bus 발행: 다른 컴포넌트에 알림
        self._emit_level_changed_event(
            new_level=EmergencyLevel.NORMAL,
            previous_level=old_state.level,
            reason=reason,
        )

        return self.get_state()

    # -------------------------------------------------------------------------
    # Gradual Recovery
    # -------------------------------------------------------------------------

    def start_gradual_recovery(
        self,
        initiated_by: str,
        target_level: EmergencyLevel = EmergencyLevel.NORMAL,
    ) -> EmergencyState:
        """
        점진적 복구 시작.

        현재 레벨에서 목표 레벨까지 단계적으로 완화.
        각 단계마다 메트릭 확인 후 진행.

        Args:
            initiated_by: 복구 시작한 사용자
            target_level: 목표 레벨 (기본: NORMAL)

        Returns:
            현재 상태
        """
        with self._state_lock:
            if not self._state.is_active:
                raise ValueError("Emergency mode is not active")

            if self._state.is_recovering:
                raise ValueError("Gradual recovery already in progress")

            if self._state.level.value <= target_level.value:
                raise ValueError(
                    f"Target level {target_level.name} must be lower than "
                    f"current level {self._state.level.name}"
                )

            self._state.is_recovering = True
            self._state.recovery_started_at = datetime.now(timezone.utc).isoformat()
            self._state.target_level = target_level
            self._save_state()

            logger.info(
                f"[EmergencyMode] Gradual recovery started by {initiated_by}: "
                f"{self._state.level.name} → {target_level.name}"
            )

        # 복구 스레드 시작
        self._stop_recovery.clear()
        self._recovery_thread = threading.Thread(
            target=self._gradual_recovery_worker,
            daemon=True,
        )
        self._recovery_thread.start()

        return self.get_state()

    def stop_gradual_recovery(
        self,
        stopped_by: str,
        reason: str = "",
    ) -> EmergencyState:
        """점진적 복구 중지."""
        self._stop_recovery.set()

        with self._state_lock:
            if self._state.is_recovering:
                self._state.is_recovering = False
                self._save_state()
                logger.info(
                    f"[EmergencyMode] Gradual recovery stopped by {stopped_by}: "
                    f"{reason or 'Manual stop'}"
                )

        return self.get_state()

    def _gradual_recovery_worker(self):
        """점진적 복구 워커 스레드."""
        config = self._recovery_gate.config

        while not self._stop_recovery.is_set():
            with self._state_lock:
                if not self._state.is_recovering:
                    break

                current_level = self._state.level
                target_level = self._state.target_level or EmergencyLevel.NORMAL

                if current_level.value <= target_level.value:
                    # 목표 도달
                    self._state.is_recovering = False
                    self._save_state()
                    logger.info(
                        f"[EmergencyMode] Gradual recovery complete: "
                        f"reached {current_level.name}"
                    )
                    break

            # 안정화 대기
            logger.info(
                f"[EmergencyMode] Waiting {config.stabilization_period_seconds}s "
                f"for stabilization before next step..."
            )
            if self._stop_recovery.wait(config.stabilization_period_seconds):
                break  # 중지 요청됨

            # 메트릭 확인
            allowed, reason = self._recovery_gate.check_recovery_allowed()

            if not allowed:
                if config.auto_rollback_on_failure:
                    logger.warning(
                        f"[EmergencyMode] Recovery check failed: {reason}. "
                        "Stopping gradual recovery."
                    )
                    with self._state_lock:
                        self._state.is_recovering = False
                        self._save_state()
                    break
                else:
                    logger.warning(
                        f"[EmergencyMode] Recovery check failed: {reason}. "
                        f"Retrying in {config.health_check_interval_seconds}s..."
                    )
                    if self._stop_recovery.wait(config.health_check_interval_seconds):
                        break
                    continue

            # 다음 레벨로 진행
            next_level = self._recovery_gate.get_next_recovery_level(current_level)

            if next_level is None:
                with self._state_lock:
                    self._state.is_recovering = False
                    self._save_state()
                break

            with self._state_lock:
                old_level = self._state.level
                self._state.level = next_level
                self._save_state()

                logger.info(
                    f"[EmergencyMode] Gradual recovery step: "
                    f"{old_level.name} → {next_level.name}"
                )

                if next_level == EmergencyLevel.NORMAL:
                    self._state.is_active = False
                    self._state.is_recovering = False
                    self._state.deactivated_at = datetime.now(timezone.utc).isoformat()
                    self._state.deactivated_by = "gradual_recovery"
                    self._save_state()

                    logger.info("emergency_mode.gradual_recovery_complete_normal")
                    break

            # 다음 단계 전 대기
            if self._stop_recovery.wait(config.level_step_delay_seconds):
                break

    # -------------------------------------------------------------------------
    # Event Bus
    # -------------------------------------------------------------------------

    def _emit_level_changed_event(
        self,
        new_level: EmergencyLevel,
        previous_level: EmergencyLevel,
        reason: str = "",
    ) -> None:
        """
        비상 모드 레벨 변경 이벤트 발행.

        다른 컴포넌트(CB, DLQ, Replay 등)가 이 이벤트를 구독하여
        비상 상황에 맞게 동작을 조정합니다.
        """
        try:
            from selfhealing.services.event_bus import (
                EventPriority,
                EventType,
                get_event_bus,
            )

            bus = get_event_bus()
            bus.emit(
                event_type=EventType.EMERGENCY_LEVEL_CHANGED,
                data={
                    "level": new_level.value,
                    "previous_level": previous_level.value,
                    "level_name": new_level.name,
                    "previous_level_name": previous_level.name,
                    "reason": reason,
                    "is_escalation": new_level.value > previous_level.value,
                    "is_active": new_level != EmergencyLevel.NORMAL,
                },
                source="emergency_manager",
                priority=EventPriority.HIGH,
            )
        except Exception as e:
            # 이벤트 발행 실패해도 비상 모드 동작에는 영향 없음
            logger.warning(
                "emergency_mode.failed_emit_event",
                error=e,
            )

    # -------------------------------------------------------------------------
    # History & Audit
    # -------------------------------------------------------------------------

    def get_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """변경 이력 조회."""
        with self._state_lock:
            return list(self._history[-limit:])

    def _log_history(
        self,
        action: str,
        old_state: EmergencyState,
        new_state: EmergencyState,
    ):
        """이력 기록."""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "old_level": old_state.level.name,
            "new_level": new_state.level.name,
            "old_active": old_state.is_active,
            "new_active": new_state.is_active,
        }
        self._history.append(entry)

        # 최근 100개만 유지
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def _log_audit(self, action: str, user: str, reason: str):
        """
        Audit 기록.

        audit_helpers 통합:
        - 기존: selfhealing.audit.log_config_change 직접 호출
        - 변경: log_emergency_mode_audit 헬퍼 사용 (WAL + 해시 체인 연결)
        """
        try:
            from selfhealing.services.audit_helpers import log_emergency_mode_audit

            log_emergency_mode_audit(
                action=action,
                level=self._state.level.name,
                is_active=self._state.is_active,
                activated_by=user if action in ("activate", "auto_activate") else None,
                deactivated_by=user if action == "deactivate" else None,
                reason=reason,
                is_auto_triggered=self._state.is_auto_triggered,
                expires_at=self._state.expires_at,
            )
        except Exception as e:
            logger.error(
                "emergency_mode.audit_log_failed",
                error=e,
            )

    def _save_state_to_config_history(
        self,
        action: str,
        state: EmergencyState,
        changed_by: str,
        reason: str,
    ) -> None:
        """
        Emergency 상태를 ConfigHistory에 저장.

        Args:
            action: 수행된 액션 (ACTIVATED, AUTO_ACTIVATED, DEACTIVATED)
            state: 현재 상태
            changed_by: 변경한 사용자
            reason: 변경 사유
        """
        try:
            from selfhealing.services.config_history import get_config_history_service

            history_service = get_config_history_service()

            history_service.save_version(
                config_type="emergency",
                values=state.to_dict(),
                changed_by=changed_by,
                reason=f"Emergency {action}: {reason}",
            )
            logger.debug(
                "emergency_mode.saved_confighistory",
                action=action,
            )
        except Exception as e:
            # Graceful degradation - 히스토리 저장 실패해도 상태 변경은 성공
            logger.warning(
                "emergency_mode.failed_save_confighistory",
                error=e,
            )

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------

    def set_recovery_gate_config(
        self,
        config: RecoveryGateConfig,
        changed_by: str = "system",
    ):
        """복구 게이트 설정 변경."""
        with self._state_lock:
            self._recovery_gate.config = config
            logger.info(
                "emergency_mode.recovery_gate_config_updated",
                changed_by=changed_by,
            )

    def get_recovery_gate_config(self) -> RecoveryGateConfig:
        """현재 복구 게이트 설정 조회."""
        with self._state_lock:
            return RecoveryGateConfig(**self._recovery_gate.config.to_dict())

    # -------------------------------------------------------------------------
    # Reset (Testing)
    # -------------------------------------------------------------------------

    def reset(self):
        """상태 초기화 (테스트용)."""
        with self._state_lock:
            self._stop_recovery.set()
            self._state = EmergencyState()
            self._history.clear()
            self._save_state()
            logger.info("emergency_mode.state_reset_defaults")

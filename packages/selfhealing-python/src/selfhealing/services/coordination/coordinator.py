"""
Emergency Coordinator.

Emergency Coordination Layer의 중앙 조율자.
Emergency 이벤트를 수신하고 정책에 따라 연계 액션을 실행합니다.

Phase 7 Integration:
- CascadeEventAuditor를 주입받아 Emergency Level 변경 시 Cascade Event 자동 기록
- 인과관계 추적으로 연계 액션 감사 완전성 보장

Code reference:
    governance.py#L307 (check_expiry_status 패턴)
    locking.py#L177 (Lua 스크립트 원자적 처리 패턴)

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from selfhealing.services.emergency_mode.enums import EmergencyLevel

from .anti_flapping import AntiFlappingGuard
from .enums import ActionType
from .models import (
    CoordinationActionResult,
    CoordinationAction,
    CoordinationResult,
    OverrideTTLConfig,
    ScopedEmergencyState,
)

if TYPE_CHECKING:
    from selfhealing.audit.cascade_auditor import CascadeEventAuditor

logger = logging.getLogger(__name__)


class DryRunAuditLogger:
    """
    Dry-Run 모드에서 "수행되었을 액션"을 감사 로그에 기록.

    Code reference:
        urls.py#L301-302 (dry-run 엔드포인트)
    """

    def log_would_execute(
        self,
        action: CoordinationAction,
        namespace: str,
        context: dict[str, Any],
    ) -> str:
        """
        수행되었을 액션을 Audit 로그에 기록.

        Args:
            action: 실행할 액션
            namespace: 대상 네임스페이스
            context: 실행 컨텍스트

        Returns:
            생성된 이벤트 ID
        """
        event_id = f"dry-run-{uuid.uuid4().hex[:12]}"

        entry = {
            "event_type": "DRY_RUN_ACTION",
            "event_id": event_id,
            "action_type": action.type.value,
            "namespace": namespace,
            "params": action.params,
            "ttl_minutes": action.ttl_minutes,
            "would_execute": True,
            "actual_execution": False,
            "context": context,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        logger.info(f"[DryRun] Would execute {action.type.value} on {namespace}: " f"params={action.params}")

        # 실제 audit 시스템 연동 시 여기서 기록
        # from selfhealing.services.audit_helpers import log_config_change
        # log_config_change(...)

        return event_id


class EmergencyCoordinator:
    """
    Emergency Coordination Layer의 중앙 조율자.

    역할:
    - Emergency 이벤트 수신 및 라우팅
    - 정책 기반 연계 액션 실행
    - 인과관계 추적 및 감사 로깅 (CascadeEventAuditor 연동)
    - 플래핑 방지 (AntiFlappingGuard)

    Phase 7 Integration:
    - cascade_auditor 주입으로 Emergency Level 변경 시 Cascade Event 자동 기록
    - 연계 액션의 인과관계 완전 추적

    Code reference:
        governance.py#L307-375 (check_expiry_status 패턴)
        docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    """

    def __init__(
        self,
        anti_flapping_guard: AntiFlappingGuard | None = None,
        ttl_config: OverrideTTLConfig | None = None,
        dry_run_mode: bool = False,
        cascade_auditor: CascadeEventAuditor | None = None,
    ):
        """
        Args:
            anti_flapping_guard: 플래핑 방지 가드
            ttl_config: TTL 강제 설정
            dry_run_mode: 전역 Dry-Run 모드
            cascade_auditor: Cascade Event 감사기 (Phase 7)
        """
        self._anti_flapping_guard = anti_flapping_guard or AntiFlappingGuard()
        self._ttl_config = ttl_config or OverrideTTLConfig()
        self._dry_run_mode = dry_run_mode
        self._dry_run_logger = DryRunAuditLogger()
        self._cascade_auditor = cascade_auditor

        # 네임스페이스별 상태
        self._namespace_states: dict[str, ScopedEmergencyState] = {}

        # 이벤트 핸들러 등록
        self._event_handlers: dict[str, list[Callable]] = {}

        # 마지막 전환 시각 (네임스페이스별)
        self._last_transition_at: dict[str, datetime] = {}

    def get_state(self, namespace: str) -> ScopedEmergencyState:
        """
        네임스페이스별 상태 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            해당 네임스페이스의 Emergency 상태
        """
        if namespace not in self._namespace_states:
            self._namespace_states[namespace] = ScopedEmergencyState(namespace=namespace)
        return self._namespace_states[namespace]

    def on_emergency_level_changed(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        trigger_event_id: str,
        actions: list[CoordinationAction] | None = None,
        force: bool = False,
        request: Any | None = None,
    ) -> CoordinationResult:
        """
        Emergency Level 변경 시 연계 액션 실행 및 Cascade Event 기록.

        Phase 7: CascadeEventAuditor를 통해 Emergency Level 변경과
        연계 액션을 인과관계와 함께 기록합니다.

        Args:
            old_level: 이전 레벨
            new_level: 새 레벨
            namespace: 대상 네임스페이스
            trigger_event_id: 트리거 이벤트 ID
            actions: 실행할 액션 목록 (없으면 기본 정책 사용)
            force: 강제 실행 (플래핑 가드 무시)
            request: Django HttpRequest (W3C Trace Context 추출용)

        Returns:
            조율 결과
        """
        cascade_event_id = f"cascade-{uuid.uuid4().hex[:12]}"

        # 1. 플래핑 가드 확인 (force=True면 건너뜀)
        if not force:
            # 쿨다운 확인
            last_at = self._last_transition_at.get(namespace)
            cooldown_ok, cooldown_reason = self._anti_flapping_guard.check_cooldown_elapsed(last_transition_at=last_at)
            if not cooldown_ok:
                logger.warning(f"[Coordinator] Level change blocked by cooldown: {cooldown_reason}")
                return CoordinationResult(
                    success=False,
                    cascade_event_id=cascade_event_id,
                    executed_actions=[],
                    trigger_type="EMERGENCY_LEVEL_CHANGED",
                    namespace=namespace,
                )

            # 플래핑 확인
            flap_ok, flap_reason = self._anti_flapping_guard.check_transition_allowed()
            if not flap_ok:
                logger.warning(f"[Coordinator] Level change blocked by flapping guard: {flap_reason}")
                return CoordinationResult(
                    success=False,
                    cascade_event_id=cascade_event_id,
                    executed_actions=[],
                    trigger_type="EMERGENCY_LEVEL_CHANGED",
                    namespace=namespace,
                )

        # 2. 전환 기록
        now = datetime.now(timezone.utc)
        self._anti_flapping_guard.record_transition(now)
        self._last_transition_at[namespace] = now

        # 3. 상태 업데이트
        state = self.get_state(namespace)
        state.emergency_level = new_level
        state.activated_at = now

        logger.info(f"[Coordinator] Level changed: {old_level.name} -> {new_level.name} " f"on namespace={namespace}")

        # 4. 액션이 없으면 빈 결과 반환 (Cascade 기록 없음)
        if not actions:
            # Cascade Event 기록: 액션 없이 레벨 변경만 기록
            self._record_cascade_event(
                old_level=old_level,
                new_level=new_level,
                namespace=namespace,
                results=[],
                request=request,
            )
            return CoordinationResult(
                success=True,
                cascade_event_id=cascade_event_id,
                executed_actions=[],
                trigger_type="EMERGENCY_LEVEL_CHANGED",
                namespace=namespace,
            )

        # 5. 액션 실행 (부분 실패 시 명시적 로깅 + immediate 액션 fail-fast)
        results = []
        for action in actions:
            result = self._execute_action(action, namespace, trigger_event_id)
            results.append(result)

            if not result.success:
                # 부분 실패 시 이미 실행된 액션 상태를 명시적으로 로깅
                succeeded = [r for r in results if r.success]
                failed = [r for r in results if not r.success]
                logger.warning(
                    f"[Coordinator] Partial failure during level change: "
                    f"namespace={namespace}, "
                    f"succeeded=[{', '.join(r.action_type.value for r in succeeded)}], "
                    f"failed=[{', '.join(r.action_type.value for r in failed)}], "
                    f"remaining={len(actions) - len(results)} actions skipped"
                )

                # immediate 액션 실패 시 나머지 액션 중단 (안전 우선)
                if action.immediate:
                    logger.error(f"[Coordinator] Immediate action failed, " f"aborting remaining actions: {action.type.value}")
                    break

        # 6. Cascade Event 기록 (Phase 7)
        self._record_cascade_event(
            old_level=old_level,
            new_level=new_level,
            namespace=namespace,
            results=results,
            request=request,
        )

        # 7. 결과 반환
        return CoordinationResult(
            success=all(r.success for r in results),
            cascade_event_id=cascade_event_id,
            executed_actions=results,
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            namespace=namespace,
        )

    def _record_cascade_event(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        results: list[CoordinationActionResult],
        request: Any | None = None,
    ) -> None:
        """
        Cascade Event 기록 (Phase 7).

        Emergency Level 변경 트리거와 연계 액션 결과를
        CascadeEventAuditor를 통해 인과관계와 함께 기록합니다.

        Args:
            old_level: 이전 Emergency Level
            new_level: 새 Emergency Level
            namespace: 네임스페이스
            results: 액션 실행 결과 목록
            request: Django HttpRequest (W3C Trace Context 추출용)
        """
        if not self._cascade_auditor:
            # cascade_auditor가 주입되지 않으면 기록 생략
            logger.debug("[Coordinator] Cascade audit skipped: no auditor configured")
            return

        try:
            # 트리거 상세 정보
            trigger_details = {
                "old_level": old_level.name,
                "new_level": new_level.name,
                "transition_type": self._get_transition_type(old_level, new_level),
            }

            # 효과 목록 (액션 실행 결과)
            effects = []
            for result in results:
                effect = {
                    "action_type": result.action_type.value,
                    "success": result.success,
                    "details": result.details or {},
                }
                if result.error:
                    effect["error_message"] = result.error
                if result.was_dry_run:
                    effect["details"]["dry_run"] = True
                effects.append(effect)

            # Cascade Event 기록
            if request:
                self._cascade_auditor.record_with_external_trace(
                    trigger_type="EMERGENCY_LEVEL_CHANGED",
                    trigger_details=trigger_details,
                    effects=effects,
                    namespace=namespace,
                    request=request,
                    triggered_by="system",
                )
            else:
                self._cascade_auditor.record(
                    trigger_type="EMERGENCY_LEVEL_CHANGED",
                    trigger_details=trigger_details,
                    effects=effects,
                    namespace=namespace,
                    triggered_by="system",
                )

            logger.debug(
                f"[Coordinator] Cascade event recorded: " f"{old_level.name} -> {new_level.name}, effects={len(effects)}"
            )
        except Exception as e:
            # Cascade 기록 실패는 Emergency 처리를 중단시키면 안 됨
            logger.warning(f"[Coordinator] Failed to record cascade event: {e}")

    def _get_transition_type(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
    ) -> str:
        """
        Emergency Level 전환 유형 결정.

        Args:
            old_level: 이전 레벨
            new_level: 새 레벨

        Returns:
            전환 유형: ESCALATION, DE_ESCALATION, ACTIVATION, DEACTIVATION
        """
        if old_level == EmergencyLevel.NORMAL:
            return "ACTIVATION"
        elif new_level == EmergencyLevel.NORMAL:
            return "DEACTIVATION"
        elif new_level.value > old_level.value:
            return "ESCALATION"
        else:
            return "DE_ESCALATION"

    def _execute_action(
        self,
        action: CoordinationAction,
        namespace: str,
        trigger_event_id: str,
    ) -> CoordinationActionResult:
        """
        개별 액션 실행.

        Args:
            action: 실행할 액션
            namespace: 대상 네임스페이스
            trigger_event_id: 트리거 이벤트 ID

        Returns:
            액션 실행 결과
        """
        event_id = f"action-{uuid.uuid4().hex[:12]}"

        # Dry-Run 모드 확인
        if self._dry_run_mode or action.is_dry_run:
            dry_run_event_id = self._dry_run_logger.log_would_execute(
                action=action,
                namespace=namespace,
                context={"trigger_event_id": trigger_event_id},
            )
            return CoordinationActionResult(
                success=True,
                action_type=action.type,
                event_id=dry_run_event_id,
                namespace=namespace,
                was_dry_run=True,
            )

        # TTL 강제 적용
        effective_ttl = action.ttl_minutes
        if action.type in (ActionType.GOVERNANCE_STRICT, ActionType.GOVERNANCE_NORMAL):
            effective_ttl = self._ttl_config.enforce_ttl(
                requested_ttl=action.ttl_minutes,
                role=action.params.get("role", "operator"),
            )

        # 실제 액션 실행
        try:
            self._execute_action_impl(action, namespace, effective_ttl)

            logger.info(f"[Coordinator] Action executed: {action.type.value} " f"on {namespace}, ttl={effective_ttl}min")

            return CoordinationActionResult(
                success=True,
                action_type=action.type,
                event_id=event_id,
                namespace=namespace,
                was_dry_run=False,
                details={"effective_ttl_minutes": effective_ttl},
            )
        except Exception as e:
            logger.error(f"[Coordinator] Action failed: {action.type.value} " f"on {namespace}: {e}")
            return CoordinationActionResult(
                success=False,
                action_type=action.type,
                event_id=event_id,
                namespace=namespace,
                was_dry_run=False,
                error=str(e),
            )

    def _execute_action_impl(
        self,
        action: CoordinationAction,
        namespace: str,
        effective_ttl: int | None,
    ) -> None:
        """
        실제 액션 구현.

        Phase 1에서는 로깅만 수행하고, Phase 3에서 실제 연동 구현.
        """
        # Phase 1: 로깅만 수행
        logger.info(
            f"[Coordinator] Executing {action.type.value} on {namespace}: " f"params={action.params}, ttl={effective_ttl}min"
        )

        # 상태 업데이트
        state = self.get_state(namespace)

        if action.type == ActionType.GOVERNANCE_STRICT:
            state.governance_mode = "STRICT"
        elif action.type == ActionType.GOVERNANCE_NORMAL:
            state.governance_mode = "NORMAL"

        # Phase 3에서 실제 서비스 호출 추가:
        # - GOVERNANCE_STRICT: governance_tracker.activate_strict()
        # - CANARY_ROLLBACK: canary_service.panic_rollback()
        # - BUDGET_MULTIPLIER: error_budget.set_multiplier()

    def set_dry_run_mode(self, enabled: bool) -> None:
        """전역 Dry-Run 모드 설정."""
        self._dry_run_mode = enabled
        logger.info(f"[Coordinator] Dry-run mode: {enabled}")

    def is_dry_run_mode(self) -> bool:
        """전역 Dry-Run 모드 여부."""
        return self._dry_run_mode

    def get_anti_flapping_status(self) -> dict:
        """플래핑 가드 상태 조회."""
        return self._anti_flapping_guard.get_status()

    def clear_flapping_lockout(self) -> None:
        """플래핑 잠금 수동 해제."""
        self._anti_flapping_guard.clear_lockout()

    # =========================================================================
    # Cascade Auditor Methods (Phase 7)
    # =========================================================================

    def set_cascade_auditor(self, auditor: CascadeEventAuditor) -> None:
        """
        CascadeEventAuditor 설정.

        런타임에 CascadeEventAuditor를 주입합니다.
        생성자에서 주입하지 않은 경우 이 메서드로 설정할 수 있습니다.

        Args:
            auditor: CascadeEventAuditor 인스턴스
        """
        self._cascade_auditor = auditor
        logger.info("[Coordinator] Cascade auditor configured")

    def get_cascade_auditor(self) -> CascadeEventAuditor | None:
        """
        현재 설정된 CascadeEventAuditor 반환.

        Returns:
            CascadeEventAuditor 또는 None
        """
        return self._cascade_auditor

    def has_cascade_auditor(self) -> bool:
        """
        CascadeEventAuditor 설정 여부.

        Returns:
            True면 auditor가 설정됨
        """
        return self._cascade_auditor is not None

"""
Canary Rollout Service.

설정 변경의 점진적 배포를 관리합니다.

Features:
    - 단계별 롤아웃 생성 (create_rollout)
    - 롤아웃 시작/중지 (start_rollout, pause, resume)
    - 자동/수동 프로모션 (promote)
    - 메트릭 기반 자동 롤백 (rollback)
    - 클러스터별 상태 추적

Reference:
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md
    - services/config_history.py (버전 관리)

Usage:
    from selfhealing.services.canary import get_canary_rollout_service

    service = get_canary_rollout_service()

    # 롤아웃 생성
    rollout = service.create_rollout(
        config_type="circuit_breaker",
        new_values={"failure_threshold": 3},
        stages=[
            CanaryStage(name="canary", clusters=["seoul-canary"], percentage=10),
            CanaryStage(name="full", clusters=["seoul", "tokyo"], percentage=100),
        ],
        created_by="admin@example.com",
        reason="Reduce failure threshold",
    )

    # 롤아웃 시작
    service.start_rollout(rollout.id)

    # 프로모션 (다음 단계)
    service.promote(rollout.id)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.services.canary.audit import log_canary_action
from selfhealing.services.canary.chaos_guard import (
    CanaryChaosGuard,
)
from selfhealing.services.canary.locking import CanaryConfigLock, ConfigLockError
from selfhealing.services.canary.models import (
    CanaryMetrics,
    CanaryRollout,
    CanaryStage,
    CanaryState,
)
from selfhealing.settings.namespace import get_key_prefix
from selfhealing.utils.time import utc_now

if TYPE_CHECKING:
    from selfhealing.services.config_history import ConfigVersion

logger = structlog.get_logger()


def _config_hash(config: dict) -> str:
    """설정 딕셔너리의 결정론적 해시를 생성한다."""

    def _sanitize(obj):
        if isinstance(obj, dict):
            return {k: _sanitize(v) for k, v in sorted(obj.items())}
        if isinstance(obj, (list, tuple)):
            return [_sanitize(v) for v in obj]
        if isinstance(obj, (int, float, bool, str, type(None))):
            return obj
        return str(obj)

    return hashlib.sha256(json.dumps(_sanitize(config)).encode()).hexdigest()[:16]


class CanaryRolloutService:
    """
    Canary Rollout 관리 서비스.

    설정 변경을 단계적으로 배포하고, 문제 발생 시 자동 롤백합니다.

    Features:
        - 단계별 롤아웃 생성
        - 동시성 제어 (Config Lock)
        - 카오스 실험 충돌 방어
        - 메트릭 기반 자동 프로모션/롤백
        - 클러스터별 상태 추적
        - Audit 로깅

    Example:
        service = get_canary_rollout_service()

        # 롤아웃 생성
        rollout = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"failure_threshold": 3},
            stages=[...],
            created_by="admin@example.com",
        )

        # 시작 → 프로모션 → 완료
        service.start_rollout(rollout.id)
        service.promote(rollout.id)  # 다음 단계
        service.promote(rollout.id)  # 완료
    """

    # Redis 키 패턴
    ROLLOUT_KEY = "{prefix}canary:rollout:{rollout_id}"
    ACTIVE_ROLLOUTS_KEY = "{prefix}canary:active"
    CLUSTER_CONFIG_KEY = "{prefix}canary:cluster:{cluster_id}:config:{config_type}"

    def __init__(self):
        """CanaryRolloutService 초기화."""
        self._redis_client = None
        self._config_history = None
        self._config_lock = None
        self._chaos_guard = None
        self._rollout_ttl_days: int | None = None

    # =========================================================================
    # Properties (Lazy Loading)
    # =========================================================================

    @property
    def rollout_ttl_days(self) -> int:
        """롤아웃 데이터 보관 기간 (Settings에서 로드)."""
        if self._rollout_ttl_days is None:
            try:
                from selfhealing.settings.canary import get_canary_settings

                self._rollout_ttl_days = get_canary_settings().rollout_ttl_days
            except ImportError:
                self._rollout_ttl_days = 7  # 기본값
        return self._rollout_ttl_days

    @property
    def redis_client(self):
        """Redis 클라이언트 (Lazy loading)."""
        if self._redis_client is None:
            try:
                from django.core.cache import caches

                cache = caches.get("default")
                if cache:
                    self._redis_client = cache.client.get_client()
            except Exception as e:
                logger.warning(
                    "canary_rollout.redis_available",
                    error=e,
                )
        return self._redis_client

    @property
    def config_history(self):
        """ConfigHistoryService (Lazy loading)."""
        if self._config_history is None:
            from selfhealing.services.config_history import get_config_history_service

            self._config_history = get_config_history_service()
        return self._config_history

    @property
    def config_lock(self) -> CanaryConfigLock:
        """CanaryConfigLock (Lazy loading)."""
        if self._config_lock is None and self.redis_client:
            self._config_lock = CanaryConfigLock(self.redis_client)
        return self._config_lock

    @property
    def chaos_guard(self) -> CanaryChaosGuard:
        """CanaryChaosGuard (Lazy loading)."""
        if self._chaos_guard is None:
            self._chaos_guard = CanaryChaosGuard()
        return self._chaos_guard

    # =========================================================================
    # Public Methods - Rollout Lifecycle
    # =========================================================================

    def create_rollout(
        self,
        config_type: str,
        new_values: dict[str, Any],
        stages: list[CanaryStage],
        created_by: str,
        reason: str = "",
        force_during_chaos: bool = False,
    ) -> CanaryRollout:
        """
        새 Canary 롤아웃 생성.

        Args:
            config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
            new_values: 새 설정값
            stages: 롤아웃 단계 정의
            created_by: 생성자 (이메일 또는 사용자명)
            reason: 변경 사유
            force_during_chaos: 카오스 실험 중에도 강제 진행

        Returns:
            생성된 CanaryRollout

        Raises:
            ConfigLockError: 동일 config_type에 대해 롤아웃 진행 중일 때
            ValueError: stages가 비어있을 때
        """
        if not stages:
            raise ValueError("At least one stage is required")

        # 1. 락 확인
        if self.config_lock and self.config_lock.is_locked(config_type):
            current_owner = self.config_lock.get_lock_owner(config_type)
            raise ConfigLockError(
                f"Config '{config_type}' is already in rollout. "
                f"Current rollout: {current_owner}",
                config_type=config_type,
                current_owner=current_owner,
            )

        # 2. 현재 설정 가져오기
        current_version = self._get_current_config(config_type)
        previous_values = current_version.values if current_version else {}

        # 3. 카오스 충돌 검사 (사전 경고용)
        all_clusters = []
        for stage in stages:
            all_clusters.extend(stage.clusters)

        chaos_result = self.chaos_guard.check_conflict(
            target_clusters=all_clusters,
            force_during_chaos=force_during_chaos,
        )

        if not chaos_result.can_proceed:
            raise ValueError(chaos_result.warning_message)

        # 4. 롤아웃 생성
        rollout = CanaryRollout(
            id=str(uuid.uuid4())[:8],
            config_type=config_type,
            previous_values=previous_values,
            new_values=new_values,
            stages=stages,
            created_by=created_by,
            reason=reason,
        )

        # 5. 락 획득
        if self.config_lock:
            if not self.config_lock.acquire(config_type, rollout.id):
                raise ConfigLockError(
                    f"Failed to acquire lock for {config_type}",
                    config_type=config_type,
                )

        # 6. Redis에 저장
        self._save_rollout(rollout)
        self._add_to_active(rollout.id)

        # 7. Audit 로그
        log_canary_action(
            action="create",
            rollout=rollout,
            safety_check_result={
                "chaos_guard": chaos_result.policy_applied.value,
                "chaos_warning": chaos_result.warning_message,
            },
        )

        logger.info(
            "canary_rollout.created",
            rollout_id=rollout.id,
            config_type=config_type,
            stages_count=len(stages),
        )

        return rollout

    def start_rollout(
        self,
        rollout_id: str,
        force_during_chaos: bool = False,
        bypass_shadow: bool = False,
        bypass_shadow_reason: str = "",
    ) -> bool:
        """
        Canary 롤아웃 시작 (첫 번째 단계 적용).

        Args:
            rollout_id: 롤아웃 ID
            force_during_chaos: 카오스 실험 중에도 강제 진행
            bypass_shadow: Shadow Evaluation 실패 시 bypass 여부
            bypass_shadow_reason: bypass 시 사유 (최소 길이: settings.bypass_min_reason_length)

        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            logger.warning(
                "canary_rollout.found",
                rollout_id=rollout_id,
            )
            return False

        if rollout.state != CanaryState.CREATED:
            logger.warning(
                "canary_rollout.cannot_start",
                rollout_state=rollout.state,
            )
            return False

        # 카오스 충돌 검사
        first_stage = rollout.stages[0]
        chaos_result = self.chaos_guard.check_conflict(
            target_clusters=first_stage.clusters,
            force_during_chaos=force_during_chaos,
        )

        if not chaos_result.can_proceed:
            logger.warning(
                "canary_rollout.start_blocked_chaos_guard",
                chaos_result=chaos_result.warning_message,
            )
            return False

        # Shadow Evaluation Gate
        shadow_check = self._check_shadow_evaluation(
            rollout=rollout,
            bypass_shadow=bypass_shadow,
            bypass_shadow_reason=bypass_shadow_reason,
        )
        if shadow_check is not None and not shadow_check:
            return False

        # 첫 번째 단계 클러스터에 적용 (안전한 클러스터만)
        self._apply_to_clusters(rollout, chaos_result.safe_clusters)

        rollout.state = CanaryState.CANARY
        rollout.current_stage_index = 0
        self._save_rollout(rollout)

        # Audit 로그
        log_canary_action(
            action="start",
            rollout=rollout,
            safety_check_result={
                "chaos_guard": chaos_result.policy_applied.value,
                "applied_clusters": chaos_result.safe_clusters,
            },
        )

        logger.info(
            "canary_rollout.started",
            rollout_id=rollout_id,
            first_stage=first_stage.name,
            chaos_result=chaos_result.safe_clusters,
        )

        return True

    def promote(
        self,
        rollout_id: str,
        force: bool = False,
        bypass_governance: bool = False,
        bypass_reason: str = "",
        requested_by: str = "",
        tier_id: str | None = None,
    ) -> bool:
        """
        다음 단계로 프로모션.

        Args:
            rollout_id: 롤아웃 ID
            force: 메트릭 검증 무시 (기존)
            bypass_governance: 거버넌스 검증 무시 (Audit 필수)
            bypass_reason: bypass 시 사유 (bypass_governance=True일 때 필수, 최소 10자)
            requested_by: 요청자 (Audit 로깅용)
            tier_id: 티어 ID (apply_tier_floor 적용용, None이면 미적용)

        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False

        if rollout.state not in (CanaryState.CANARY, CanaryState.PAUSED):
            logger.warning(
                "canary_rollout.cannot_promote",
                rollout_state=rollout.state,
            )
            return False

        # 거버넌스 체크 (수동 프로모션에도 적용)
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
                        "canary_rollout.promotion_blocked_governance",
                        governance=governance.block_message,
                    )
                    return False

            except ImportError:
                logger.debug("canary_rollout.governancechecks_available_skipping")
            except Exception as e:
                logger.warning(
                    "canary_rollout.governance_check_failed",
                    error=e,
                )
                # Fail-Closed: 체크 실패 시 차단
                return False
        else:
            # bypass_governance=True: Audit 로그 필수
            if not bypass_reason or len(bypass_reason) < 10:
                logger.error("canary_rollout.required_min_chars")
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
                "canary_rollout.governance_bypassed",
                rollout_id=rollout_id,
                bypass_reason=bypass_reason,
                requested_by=requested_by,
            )

        # 현재 단계 메트릭 검증 (force가 아니면)
        if not force:
            metrics = self._collect_stage_metrics(rollout)
            is_healthy, failure_reason = self._is_stage_healthy(
                rollout.current_stage,
                metrics,
                tier_id=tier_id,
            )

            if not is_healthy:
                logger.warning(
                    "canary_rollout.promotion_blocked",
                    failure_reason=failure_reason,
                )
                return False

        # 다음 단계로 이동
        next_index = rollout.current_stage_index + 1

        if next_index >= len(rollout.stages):
            # 모든 단계 완료
            rollout.state = CanaryState.COMPLETED
            rollout.completed_at = utc_now()
            self._remove_from_active(rollout.id)

            # 락 해제
            if self.config_lock:
                self.config_lock.release(rollout.config_type, rollout.id)

            action = "complete"
        else:
            # 다음 단계 적용
            stage = rollout.stages[next_index]
            self._apply_to_clusters(rollout, stage.clusters)
            rollout.current_stage_index = next_index
            rollout.state = CanaryState.CANARY
            action = "force_promote" if force else "promote"

        self._save_rollout(rollout)

        # Audit 로그
        log_canary_action(action=action, rollout=rollout)

        logger.info(
            "canary_rollout.promoted",
            rollout_id=rollout_id,
            rollout_stage_index=rollout.current_stage_index,
        )

        return True

    def rollback(
        self,
        rollout_id: str,
        reason: str = "",
    ) -> bool:
        """
        롤백 수행.

        적용된 모든 클러스터를 이전 설정으로 복원합니다.

        Args:
            rollout_id: 롤아웃 ID
            reason: 롤백 사유

        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False

        if rollout.is_terminal:
            logger.warning(
                "canary_rollout.cannot_rollback_terminal_state",
                rollout_state=rollout.state,
            )
            return False

        # 적용된 모든 클러스터에 이전 설정 복원
        for cluster in rollout.affected_clusters:
            self._apply_config_to_cluster(
                cluster,
                rollout.config_type,
                rollout.previous_values,
            )

        rollout.state = CanaryState.ROLLED_BACK
        rollout.rollback_reason = reason
        rollout.completed_at = utc_now()
        self._save_rollout(rollout)
        self._remove_from_active(rollout.id)

        # 락 해제
        if self.config_lock:
            self.config_lock.release(rollout.config_type, rollout.id)

        # Audit 로그
        log_canary_action(
            action="rollback",
            rollout=rollout,
            additional_context={"rollback_reason": reason},
        )

        logger.warning(
            "canary_rollout.rolled_back",
            rollout_id=rollout_id,
            reason=reason,
        )

        return True

    def pause(
        self,
        rollout_id: str,
        reason: str = "",
        triggered_by: str = "manual",
    ) -> bool:
        """
        롤아웃 일시 중지 (사유 추적 확장).

        Args:
            rollout_id: 롤아웃 ID
            reason: 일시 중지 사유
            triggered_by: 트리거 유형 (manual, interlock, chaos_guard, metrics, error_budget, governance)

        Returns:
            성공 여부
        """
        from selfhealing.services.canary.models import TRIGGER_PRIORITY_MAP

        rollout = self.get_rollout(rollout_id)
        if not rollout or rollout.state != CanaryState.CANARY:
            return False

        # 우선순위 기반 사유 덮어쓰기 (높은 우선순위만)
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
                "canary_rollout.keeping_existing_trigger_priority",
                existing_trigger=existing_trigger,
                existing_priority=existing_priority,
                triggered_by=triggered_by,
                new_priority=new_priority,
            )

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

        logger.info(
            "canary_rollout.paused",
            rollout_id=rollout_id,
            triggered_by=triggered_by,
            reason=reason,
        )
        return True

    def resume(self, rollout_id: str) -> bool:
        """
        일시 중지된 롤아웃 재개.

        Args:
            rollout_id: 롤아웃 ID

        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout or rollout.state != CanaryState.PAUSED:
            return False

        rollout.state = CanaryState.CANARY
        # pause 관련 필드 초기화
        rollout.pause_reason = None
        rollout.pause_triggered_by = None
        rollout.paused_at = None
        self._save_rollout(rollout)

        log_canary_action(action="resume", rollout=rollout)

        logger.info(
            "canary_rollout.resumed",
            rollout_id=rollout_id,
        )
        return True

    def resume_paused_rollouts(
        self,
        namespace: str | None = None,
        triggered_by_whitelist: list[str] | None = None,
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

            # Whitelist 필터링
            triggered_by = getattr(rollout, "pause_triggered_by", None)

            if triggered_by_whitelist is not None:
                # Whitelist가 명시된 경우: 해당 사유만 재개
                if triggered_by not in triggered_by_whitelist:
                    logger.debug(
                        "canary_rollout.skipping_resume_whitelist",
                        rollout_id=rollout.id,
                        triggered_by=triggered_by,
                    )
                    continue
            else:
                # Whitelist=None: 기존 동작 (모든 PAUSED 재개) + 경고
                logger.warning(
                    "canary_rollout.resuming_without_whitelist_filter",
                    rollout_id=rollout.id,
                )

            if self.resume(rollout.id):
                resumed.append(rollout.id)

        return resumed

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
        import time

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
            batch = candidates[i : i + max_batch_size]

            for rollout_id in batch:
                if self.resume(rollout_id):
                    resumed.append(rollout_id)

            # 마지막 배치가 아니면 대기
            if i + max_batch_size < len(candidates):
                logger.info(
                    "canary_rollout.resumed_batch_waiting_before",
                    batch_number=i // max_batch_size + 1,
                    interval_seconds=interval_seconds,
                )
                time.sleep(interval_seconds)

        logger.info(
            "canary_rollout.staggered_resume_complete_rollouts",
            resumed_count=len(resumed),
            candidates_count=len(candidates),
        )

        return resumed

    def cancel(self, rollout_id: str, reason: str = "") -> bool:
        """
        롤아웃 취소 (시작 전 상태에서만).

        Args:
            rollout_id: 롤아웃 ID
            reason: 취소 사유

        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False

        if rollout.state != CanaryState.CREATED:
            logger.warning(
                "canary_rollout.cannot_cancel_after_start",
                rollout_state=rollout.state,
            )
            return False

        rollout.state = CanaryState.CANCELLED
        rollout.rollback_reason = reason
        rollout.completed_at = utc_now()
        self._save_rollout(rollout)
        self._remove_from_active(rollout.id)

        # 락 해제
        if self.config_lock:
            self.config_lock.release(rollout.config_type, rollout.id)

        log_canary_action(
            action="cancel",
            rollout=rollout,
            additional_context={"cancel_reason": reason},
        )

        logger.info(
            "canary_rollout.cancelled",
            rollout_id=rollout_id,
        )
        return True

    # =========================================================================
    # Public Methods - Query
    # =========================================================================

    def get_rollout(self, rollout_id: str) -> CanaryRollout | None:
        """
        롤아웃 조회.

        Args:
            rollout_id: 롤아웃 ID

        Returns:
            CanaryRollout 또는 None
        """
        if not self.redis_client:
            return None

        key = self._make_rollout_key(rollout_id)
        data = self.redis_client.get(key)

        if data:
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            return self._deserialize_rollout(json.loads(data))
        return None

    def get_active_rollouts(self) -> list[CanaryRollout]:
        """
        활성 롤아웃 목록 조회.

        Returns:
            활성 상태의 CanaryRollout 목록
        """
        if not self.redis_client:
            return []

        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        rollout_ids = self.redis_client.smembers(key)

        rollouts = []
        for rid in rollout_ids:
            if isinstance(rid, bytes):
                rid = rid.decode("utf-8")
            rollout = self.get_rollout(rid)
            if rollout:
                rollouts.append(rollout)

        return rollouts

    def get_rollout_for_config(self, config_type: str) -> CanaryRollout | None:
        """
        특정 config_type의 활성 롤아웃 조회.

        Args:
            config_type: 설정 유형

        Returns:
            활성 롤아웃 또는 None
        """
        for rollout in self.get_active_rollouts():
            if rollout.config_type == config_type:
                return rollout
        return None

    def get_completed_rollouts(self, limit: int = 20) -> list[CanaryRollout]:
        """
        완료된 롤아웃 목록 조회.

        완료/롤백/실패/취소된 롤아웃을 최근 순으로 반환합니다.

        Args:
            limit: 최대 조회 개수 (기본 20)

        Returns:
            완료된 CanaryRollout 목록
        """
        if not self.redis_client:
            return []

        # 모든 롤아웃 키 패턴 검색
        pattern = self.ROLLOUT_KEY.format(
            prefix=get_key_prefix(),
            rollout_id="*",
        )

        completed_rollouts = []

        try:
            # SCAN 사용 (KEYS보다 안전)
            cursor = 0
            while True:
                cursor, keys = self.redis_client.scan(cursor, match=pattern, count=100)

                for key in keys:
                    if isinstance(key, bytes):
                        key = key.decode("utf-8")

                    data = self.redis_client.get(key)
                    if data:
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        rollout = self._deserialize_rollout(json.loads(data))

                        # 완료 상태만 포함
                        if rollout.is_terminal:
                            completed_rollouts.append(rollout)

                if cursor == 0:
                    break
        except Exception as e:
            logger.warning(
                "canary_rollout.failed_scan_completed_rollouts",
                error=e,
            )
            return []

        # 완료 시간 역순 정렬
        completed_rollouts.sort(
            key=lambda r: r.completed_at or r.created_at,
            reverse=True,
        )

        return completed_rollouts[:limit]

    def collect_metrics(self, rollout_id: str) -> list[CanaryMetrics]:
        """
        롤아웃 메트릭 수집 (Public API).

        Args:
            rollout_id: 롤아웃 ID

        Returns:
            CanaryMetrics 목록
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return []

        return self._collect_stage_metrics(rollout)

    # =========================================================================
    # Private Methods - Shadow Evaluation Gate
    # =========================================================================

    def _check_shadow_evaluation(
        self,
        rollout: CanaryRollout,
        bypass_shadow: bool,
        bypass_shadow_reason: str,
    ) -> bool | None:
        """Shadow Evaluation 결과를 확인한다.

        Returns:
            True: 통과 (시작 가능)
            False: 차단 (시작 불가)
            None: Shadow Evaluation 미실행 또는 비활성화 (체크 생략)
        """
        try:
            from selfhealing.settings.config_shadow import get_config_shadow_settings

            settings = get_config_shadow_settings()
            if not settings.gate_enabled:
                return None
        except ImportError:
            return None

        try:
            from selfhealing.services.config_shadow import get_shadow_evaluator_service

            service = get_shadow_evaluator_service()
            evaluation = service.get_latest_for_rollout(rollout.id)
        except ImportError:
            logger.debug("canary_rollout.shadow_evaluator_not_available")
            return None
        except Exception as e:
            logger.warning("canary_rollout.shadow_check_error", error=e)
            return None

        if evaluation is None:
            if settings.require_evaluation:
                logger.warning(
                    "canary_rollout.shadow_evaluation_required_not_found",
                    rollout_id=rollout.id,
                )
                return False
            return None

        from selfhealing.services.config_shadow.models import EvaluationStatus

        if evaluation.status in (EvaluationStatus.PENDING, EvaluationStatus.RUNNING):
            logger.warning(
                "canary_rollout.shadow_evaluation_in_progress",
                rollout_id=rollout.id,
                evaluation_id=evaluation.evaluation_id,
                status=evaluation.status.value,
            )
            return False

        if evaluation.completed_at:
            age = datetime.now(timezone.utc) - evaluation.completed_at
            max_age = timedelta(hours=settings.evaluation_ttl_hours)
            if age > max_age:
                logger.warning(
                    "canary_rollout.shadow_evaluation_stale",
                    evaluation_id=evaluation.evaluation_id,
                    age_hours=age.total_seconds() / 3600,
                    max_age_hours=settings.evaluation_ttl_hours,
                )
                return False

        if evaluation.candidate_config and rollout.new_values:
            eval_hash = _config_hash(evaluation.candidate_config)
            current_hash = _config_hash(rollout.new_values)
            if eval_hash != current_hash:
                logger.warning(
                    "canary_rollout.shadow_config_mismatch",
                    evaluation_id=evaluation.evaluation_id,
                    evaluation_hash=eval_hash,
                    current_hash=current_hash,
                )
                return False

        if evaluation.report and evaluation.report.passed:
            if evaluation.report.confidence_score < settings.min_confidence:
                logger.warning(
                    "canary_rollout.shadow_evaluation_low_confidence",
                    evaluation_id=evaluation.evaluation_id,
                    confidence=evaluation.report.confidence_score,
                    min_confidence=settings.min_confidence,
                )
                log_canary_action(
                    action="shadow_evaluation_low_confidence",
                    rollout=rollout,
                    safety_check_result={
                        "evaluation_id": evaluation.evaluation_id,
                        "confidence_score": evaluation.report.confidence_score,
                        "min_confidence": settings.min_confidence,
                    },
                )
                if settings.block_on_low_confidence:
                    return False
            logger.info(
                "canary_rollout.shadow_evaluation_passed",
                evaluation_id=evaluation.evaluation_id,
                confidence=evaluation.report.confidence_score,
            )
            return True

        if bypass_shadow:
            if (
                not bypass_shadow_reason
                or len(bypass_shadow_reason) < settings.bypass_min_reason_length
            ):
                logger.error(
                    "canary_rollout.shadow_bypass_reason_required",
                    min_chars=settings.bypass_min_reason_length,
                )
                return False

            log_canary_action(
                action="shadow_evaluation_bypass",
                rollout=rollout,
                safety_check_result={
                    "evaluation_id": evaluation.evaluation_id,
                    "bypass_reason": bypass_shadow_reason,
                    "evaluation_summary": evaluation.report.summary
                    if evaluation.report
                    else "",
                    "confidence_score": evaluation.report.confidence_score
                    if evaluation.report
                    else 0,
                },
            )

            logger.warning(
                "canary_rollout.shadow_evaluation_bypassed",
                rollout_id=rollout.id,
                evaluation_id=evaluation.evaluation_id,
                bypass_reason=bypass_shadow_reason,
            )
            return True

        logger.warning(
            "canary_rollout.shadow_evaluation_failed_blocking",
            rollout_id=rollout.id,
            evaluation_id=evaluation.evaluation_id,
            summary=evaluation.report.summary if evaluation.report else "",
        )
        return False

    # =========================================================================
    # Private Methods - Redis Operations
    # =========================================================================

    def _make_rollout_key(self, rollout_id: str) -> str:
        """롤아웃 Redis 키 생성."""
        return self.ROLLOUT_KEY.format(
            prefix=get_key_prefix(),
            rollout_id=rollout_id,
        )

    def _save_rollout(self, rollout: CanaryRollout) -> None:
        """롤아웃 저장."""
        if not self.redis_client:
            return

        key = self._make_rollout_key(rollout.id)
        data = self._serialize_rollout(rollout)
        ttl = 86400 * self.rollout_ttl_days
        self.redis_client.set(key, json.dumps(data), ex=ttl)

    def _add_to_active(self, rollout_id: str) -> None:
        """활성 목록에 추가."""
        if not self.redis_client:
            return

        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        self.redis_client.sadd(key, rollout_id)

    def _remove_from_active(self, rollout_id: str) -> None:
        """활성 목록에서 제거."""
        if not self.redis_client:
            return

        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        self.redis_client.srem(key, rollout_id)

    # =========================================================================
    # Private Methods - Config Operations
    # =========================================================================

    def _get_current_config(self, config_type: str) -> ConfigVersion | None:
        """현재 설정 버전 조회."""
        try:
            return self.config_history.get_current_version(config_type)
        except Exception as e:
            logger.warning(
                "canary_rollout.failed_get_config",
                error=e,
            )
            return None

    def _apply_to_clusters(
        self,
        rollout: CanaryRollout,
        clusters: list[str],
    ) -> None:
        """클러스터들에 설정 적용."""
        for cluster in clusters:
            self._apply_config_to_cluster(
                cluster,
                rollout.config_type,
                rollout.new_values,
            )

    def _apply_config_to_cluster(
        self,
        cluster: str,
        config_type: str,
        values: dict[str, Any],
    ) -> None:
        """
        특정 클러스터에 설정 적용.

        실제 구현은 클러스터 동기화 방식에 따라 달라짐:
        - 같은 Redis: 네임스페이스 기반 키에 저장
        - 별도 Redis: 클러스터별 Redis에 연결하여 저장
        - API 기반: 클러스터 API 호출
        """
        if not self.redis_client:
            logger.warning(
                "canary_rollout.redis_available_skipping_apply",
                cluster=cluster,
            )
            return

        # 클러스터별 설정 키에 저장
        key = self.CLUSTER_CONFIG_KEY.format(
            prefix=get_key_prefix(),
            cluster_id=cluster,
            config_type=config_type,
        )

        self.redis_client.set(
            key,
            json.dumps(values),
            ex=86400 * self.rollout_ttl_days,
        )

        logger.info(
            "canary_rollout.applied",
            cluster=cluster,
            config_type=config_type,
        )

    # =========================================================================
    # Private Methods - Metrics & Health
    # =========================================================================

    def _collect_stage_metrics(
        self,
        rollout: CanaryRollout,
    ) -> list[CanaryMetrics]:
        """
        현재 단계의 메트릭 수집.

        TODO: Prometheus/메트릭 시스템 연동
        """
        # 현재는 빈 목록 반환 (메트릭 시스템 연동 필요)
        return []

    def _is_stage_healthy(
        self,
        stage: CanaryStage | None,
        metrics: list[CanaryMetrics],
        tier_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """
        단계 건강 상태 판정.

        Args:
            stage: 현재 Canary 단계
            metrics: 수집된 메트릭 목록
            tier_id: 티어 ID (설정 시 apply_tier_floor 적용)

        Returns:
            (건강 여부, 실패 사유)
        """
        if not stage:
            return True, None

        if not metrics:
            # 메트릭 없으면 통과 (샘플 부족)
            return True, None

        # 티어별 최소 보안 기준 강제 (221 설계 §4.9)
        if tier_id:
            from selfhealing.services.canary.models import apply_tier_floor

            effective_criteria = apply_tier_floor(stage.pass_criteria, tier_id)
        else:
            effective_criteria = stage.pass_criteria

        # PassCriteria 사용
        for m in metrics:
            passed, reason = effective_criteria.evaluate(m)
            if not passed:
                return False, reason

        return True, None

    # =========================================================================
    # Private Methods - Serialization
    # =========================================================================

    def _serialize_rollout(self, rollout: CanaryRollout) -> dict[str, Any]:
        """CanaryRollout을 딕셔너리로 직렬화."""
        return {
            "id": rollout.id,
            "config_type": rollout.config_type,
            "previous_values": rollout.previous_values,
            "new_values": rollout.new_values,
            "state": rollout.state.value,
            "current_stage_index": rollout.current_stage_index,
            "stages": [
                {
                    "name": s.name,
                    "clusters": s.clusters,
                    "percentage": s.percentage,
                    "duration_minutes": s.duration_minutes,
                    "auto_promote": s.auto_promote,
                    "error_rate_threshold": s.error_rate_threshold,
                    "latency_increase_threshold": s.latency_increase_threshold,
                }
                for s in rollout.stages
            ],
            "created_by": rollout.created_by,
            "created_at": rollout.created_at.isoformat(),
            "reason": rollout.reason,
            "completed_at": (
                rollout.completed_at.isoformat() if rollout.completed_at else None
            ),
            "rollback_reason": rollout.rollback_reason,
            # pause 관련 필드
            "pause_reason": rollout.pause_reason,
            "pause_triggered_by": rollout.pause_triggered_by,
            "paused_at": (rollout.paused_at.isoformat() if rollout.paused_at else None),
        }

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
                if data.get("completed_at")
                else None
            ),
            rollback_reason=data.get("rollback_reason"),
            # 하위 호환성: pause 관련 필드 (없으면 None)
            pause_reason=data.get("pause_reason"),
            pause_triggered_by=data.get("pause_triggered_by"),
            paused_at=(
                datetime.fromisoformat(data["paused_at"])
                if data.get("paused_at")
                else None
            ),
        )


# =============================================================================
# Singleton & Factory
# =============================================================================

_service: CanaryRolloutService | None = None


def get_canary_rollout_service() -> CanaryRolloutService:
    """
    CanaryRolloutService 싱글톤 반환.

    Returns:
        CanaryRolloutService 인스턴스
    """
    global _service
    if _service is None:
        _service = CanaryRolloutService()
    return _service


def reset_canary_rollout_service() -> None:
    """
    싱글톤 리셋 (테스트용).

    Warning:
        프로덕션에서는 사용하지 마세요.
    """
    global _service
    _service = None

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

import json
import logging
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from selfhealing.utils.time import utc_now

from selfhealing.services.canary.models import (
    CanaryRollout,
    CanaryStage,
    CanaryState,
    CanaryMetrics,
)
from selfhealing.services.canary.locking import CanaryConfigLock, ConfigLockError
from selfhealing.services.canary.chaos_guard import CanaryChaosGuard, ChaosConflictPolicy
from selfhealing.services.canary.audit import log_canary_action, log_canary_error
from selfhealing.settings.namespace import get_key_prefix

if TYPE_CHECKING:
    from selfhealing.services.config_history import ConfigVersion

logger = logging.getLogger(__name__)


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

    # 기본 설정
    ROLLOUT_TTL_DAYS = 7  # 롤아웃 데이터 보관 기간

    def __init__(self):
        """CanaryRolloutService 초기화."""
        self._redis_client = None
        self._config_history = None
        self._config_lock = None
        self._chaos_guard = None

    # =========================================================================
    # Properties (Lazy Loading)
    # =========================================================================

    @property
    def redis_client(self):
        """Redis 클라이언트 (Lazy loading)."""
        if self._redis_client is None:
            try:
                from django.core.cache import caches
                cache = caches.get('default')
                if cache:
                    self._redis_client = cache.client.get_client()
            except Exception as e:
                logger.warning(f"[CanaryRollout] Redis not available: {e}")
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
        new_values: Dict[str, Any],
        stages: List[CanaryStage],
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
            f"[CanaryRollout] Created: id={rollout.id}, "
            f"config={config_type}, stages={len(stages)}"
        )
        
        return rollout

    def start_rollout(
        self,
        rollout_id: str,
        force_during_chaos: bool = False,
    ) -> bool:
        """
        Canary 롤아웃 시작 (첫 번째 단계 적용).
        
        Args:
            rollout_id: 롤아웃 ID
            force_during_chaos: 카오스 실험 중에도 강제 진행
        
        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            logger.warning(f"[CanaryRollout] Not found: {rollout_id}")
            return False
        
        if rollout.state != CanaryState.CREATED:
            logger.warning(
                f"[CanaryRollout] Cannot start: state={rollout.state}"
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
                f"[CanaryRollout] Start blocked by chaos guard: "
                f"{chaos_result.warning_message}"
            )
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
            f"[CanaryRollout] Started: id={rollout_id}, "
            f"stage={first_stage.name}, clusters={chaos_result.safe_clusters}"
        )
        
        return True

    def promote(
        self,
        rollout_id: str,
        force: bool = False,
    ) -> bool:
        """
        다음 단계로 프로모션.
        
        Args:
            rollout_id: 롤아웃 ID
            force: 메트릭 검증 무시
        
        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False
        
        if rollout.state not in (CanaryState.CANARY, CanaryState.PAUSED):
            logger.warning(
                f"[CanaryRollout] Cannot promote: state={rollout.state}"
            )
            return False
        
        # 현재 단계 메트릭 검증 (force가 아니면)
        if not force:
            metrics = self._collect_stage_metrics(rollout)
            is_healthy, failure_reason = self._is_stage_healthy(
                rollout.current_stage,
                metrics,
            )
            
            if not is_healthy:
                logger.warning(
                    f"[CanaryRollout] Promotion blocked: {failure_reason}"
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
            f"[CanaryRollout] Promoted: id={rollout_id}, "
            f"stage_index={rollout.current_stage_index}"
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
                f"[CanaryRollout] Cannot rollback terminal state: "
                f"{rollout.state}"
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
            f"[CanaryRollout] Rolled back: id={rollout_id}, reason={reason}"
        )
        
        return True

    def pause(self, rollout_id: str) -> bool:
        """
        롤아웃 일시 중지.
        
        Args:
            rollout_id: 롤아웃 ID
        
        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout or rollout.state != CanaryState.CANARY:
            return False
        
        rollout.state = CanaryState.PAUSED
        self._save_rollout(rollout)
        
        log_canary_action(action="pause", rollout=rollout)
        
        logger.info(f"[CanaryRollout] Paused: id={rollout_id}")
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
        self._save_rollout(rollout)
        
        log_canary_action(action="resume", rollout=rollout)
        
        logger.info(f"[CanaryRollout] Resumed: id={rollout_id}")
        return True

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
                f"[CanaryRollout] Cannot cancel after start: state={rollout.state}"
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
        
        logger.info(f"[CanaryRollout] Cancelled: id={rollout_id}")
        return True

    # =========================================================================
    # Public Methods - Query
    # =========================================================================

    def get_rollout(self, rollout_id: str) -> Optional[CanaryRollout]:
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
                data = data.decode('utf-8')
            return self._deserialize_rollout(json.loads(data))
        return None

    def get_active_rollouts(self) -> List[CanaryRollout]:
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
                rid = rid.decode('utf-8')
            rollout = self.get_rollout(rid)
            if rollout:
                rollouts.append(rollout)
        
        return rollouts

    def get_rollout_for_config(self, config_type: str) -> Optional[CanaryRollout]:
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

    def get_completed_rollouts(self, limit: int = 20) -> List[CanaryRollout]:
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
                        key = key.decode('utf-8')
                    
                    data = self.redis_client.get(key)
                    if data:
                        if isinstance(data, bytes):
                            data = data.decode('utf-8')
                        rollout = self._deserialize_rollout(json.loads(data))
                        
                        # 완료 상태만 포함
                        if rollout.is_terminal:
                            completed_rollouts.append(rollout)
                
                if cursor == 0:
                    break
        except Exception as e:
            logger.warning(f"[CanaryRollout] Failed to scan completed rollouts: {e}")
            return []
        
        # 완료 시간 역순 정렬
        completed_rollouts.sort(
            key=lambda r: r.completed_at or r.created_at,
            reverse=True,
        )
        
        return completed_rollouts[:limit]

    def collect_metrics(self, rollout_id: str) -> List[CanaryMetrics]:
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
        ttl = 86400 * self.ROLLOUT_TTL_DAYS
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

    def _get_current_config(self, config_type: str) -> Optional["ConfigVersion"]:
        """현재 설정 버전 조회."""
        try:
            return self.config_history.get_current_version(config_type)
        except Exception as e:
            logger.warning(f"[CanaryRollout] Failed to get config: {e}")
            return None

    def _apply_to_clusters(
        self,
        rollout: CanaryRollout,
        clusters: List[str],
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
        values: Dict[str, Any],
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
                f"[CanaryRollout] Redis not available, skipping apply to {cluster}"
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
            ex=86400 * self.ROLLOUT_TTL_DAYS,
        )
        
        logger.info(
            f"[CanaryRollout] Applied to cluster={cluster}, config={config_type}"
        )

    # =========================================================================
    # Private Methods - Metrics & Health
    # =========================================================================

    def _collect_stage_metrics(
        self,
        rollout: CanaryRollout,
    ) -> List[CanaryMetrics]:
        """
        현재 단계의 메트릭 수집.
        
        TODO: Prometheus/메트릭 시스템 연동
        """
        # 현재는 빈 목록 반환 (메트릭 시스템 연동 필요)
        return []

    def _is_stage_healthy(
        self,
        stage: Optional[CanaryStage],
        metrics: List[CanaryMetrics],
    ) -> tuple[bool, Optional[str]]:
        """
        단계 건강 상태 판정.
        
        Returns:
            (건강 여부, 실패 사유)
        """
        if not stage:
            return True, None
        
        if not metrics:
            # 메트릭 없으면 통과 (샘플 부족)
            return True, None
        
        # PassCriteria 사용
        for m in metrics:
            passed, reason = stage.pass_criteria.evaluate(m)
            if not passed:
                return False, reason
        
        return True, None

    # =========================================================================
    # Private Methods - Serialization
    # =========================================================================

    def _serialize_rollout(self, rollout: CanaryRollout) -> Dict[str, Any]:
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
                rollout.completed_at.isoformat()
                if rollout.completed_at else None
            ),
            "rollback_reason": rollout.rollback_reason,
        }

    def _deserialize_rollout(self, data: Dict[str, Any]) -> CanaryRollout:
        """딕셔너리에서 CanaryRollout 복원."""
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
        )


# =============================================================================
# Singleton & Factory
# =============================================================================

_service: Optional[CanaryRolloutService] = None


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

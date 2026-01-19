# Canary Config Rollout

> 설정 변경의 점진적 배포 및 자동 롤백 시스템

---

## 1. 개요

### 1.1 목적

다중 클러스터 환경에서 설정 변경을 안전하게 배포하기 위한 Canary Rollout 시스템입니다.

### 1.2 왜 필요한가?

**다중 클러스터에서 Canary 없이 설정 변경 시:**

```
설정 변경 (failure_threshold: 5 → 3)
         ↓
┌─────────┬─────────┬─────────┐
│ Seoul   │ Tokyo   │ Singapore│
│ 즉시적용 │ 즉시적용 │ 즉시적용 │
└─────────┴─────────┴─────────┘
         ↓
    문제 발생 시 전체 리전 영향
```

**Canary 적용 시:**

```
설정 변경 (failure_threshold: 5 → 3)
         ↓
┌─────────┬─────────┬─────────┐
│ Seoul   │ Tokyo   │ Singapore│
│ 10% 적용│ 대기    │ 대기     │
└─────────┴─────────┴─────────┘
         ↓
    5분 모니터링 → 문제 없음
         ↓
┌─────────┬─────────┬─────────┐
│ Seoul   │ Tokyo   │ Singapore│
│ 100%    │ 50%     │ 대기     │
└─────────┴─────────┴─────────┘
         ↓
    문제 발생 → Tokyo만 롤백
    Seoul은 유지, Singapore는 배포 중단
```

### 1.3 현재 코드 기반

#### ConfigHistoryService (config_history.py#L72-87)

```python
class ConfigHistoryService:
    """
    설정 변경 이력 관리 서비스.
    
    Features:
    - 변경 시 자동 버전 저장
    - 최근 N개 버전 유지
    - 특정 버전으로 롤백
    - Redis 장애 시 Graceful Degradation
    """
```

#### RuntimeConfigService (runtime_config.py)

```python
class RuntimeConfigService:
    """런타임 설정 변경 서비스."""
    
    def update_circuit_breaker(self, **kwargs):
        """Circuit Breaker 설정 업데이트."""
        pass
```

---

## 2. 아키텍처

### 2.1 전체 구조

```
┌─────────────────────────────────────────────────────────────────┐
│                      Canary Rollout System                       │
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │ Config API   │───▶│CanaryManager │───▶│ Cluster Sync │       │
│  └──────────────┘    └──────────────┘    └──────────────┘       │
│                             │                    │               │
│                             ▼                    ▼               │
│                      ┌──────────────┐    ┌──────────────┐       │
│                      │MetricMonitor │    │ HealthCheck  │       │
│                      └──────────────┘    └──────────────┘       │
│                             │                    │               │
│                             ▼                    ▼               │
│                      ┌──────────────────────────────┐           │
│                      │      Auto Rollback Engine    │           │
│                      └──────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 상태 전이

```
CREATED ──▶ CANARY ──▶ PROMOTING ──▶ COMPLETED
   │          │           │
   │          ▼           ▼
   │       PAUSED     ROLLED_BACK
   │          │
   ▼          ▼
CANCELLED  FAILED
```

---

## 3. 데이터 모델

### 3.1 CanaryRollout

```python
# packages/selfhealing-python/src/selfhealing/services/canary/models.py
"""
Canary Rollout Data Models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class CanaryState(str, Enum):
    """Canary 롤아웃 상태."""
    CREATED = "created"
    CANARY = "canary"          # Canary 단계 (일부 클러스터만 적용)
    PROMOTING = "promoting"    # 프로모션 중
    PAUSED = "paused"          # 일시 중지
    COMPLETED = "completed"    # 완료 (모든 클러스터 적용)
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class CanaryStage:
    """Canary 단계 정의."""
    name: str
    clusters: List[str]  # 이 단계에 포함된 클러스터들
    percentage: float    # 전체 중 몇 %인지
    duration_minutes: int = 5  # 이 단계 유지 시간
    
    # 자동 프로모션 조건
    auto_promote: bool = True
    error_rate_threshold: float = 0.05  # 5% 초과 시 중단
    latency_increase_threshold: float = 0.5  # 50% 증가 시 중단


@dataclass
class CanaryRollout:
    """
    Canary 롤아웃 정보.
    
    하나의 설정 변경에 대한 전체 롤아웃 계획 및 상태.
    """
    id: str
    config_type: str  # circuit_breaker, dlq, retry 등
    
    # 설정값
    previous_values: Dict[str, Any]
    new_values: Dict[str, Any]
    
    # 상태
    state: CanaryState = CanaryState.CREATED
    current_stage_index: int = 0
    
    # 단계 정의
    stages: List[CanaryStage] = field(default_factory=list)
    
    # 메타데이터
    created_by: str = ""
    created_at: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""
    
    # 결과
    completed_at: Optional[datetime] = None
    rollback_reason: Optional[str] = None
    
    @property
    def current_stage(self) -> Optional[CanaryStage]:
        """현재 단계 반환."""
        if 0 <= self.current_stage_index < len(self.stages):
            return self.stages[self.current_stage_index]
        return None
    
    @property
    def affected_clusters(self) -> List[str]:
        """현재까지 적용된 클러스터 목록."""
        clusters = []
        for i in range(self.current_stage_index + 1):
            if i < len(self.stages):
                clusters.extend(self.stages[i].clusters)
        return clusters


@dataclass
class CanaryMetrics:
    """Canary 단계의 메트릭."""
    cluster: str
    stage_name: str
    
    # 에러율
    error_rate_before: float = 0.0
    error_rate_after: float = 0.0
    
    # 레이턴시
    latency_p50_before: float = 0.0
    latency_p50_after: float = 0.0
    latency_p99_before: float = 0.0
    latency_p99_after: float = 0.0
    
    # 트래픽
    requests_total: int = 0
    errors_total: int = 0
    
    # 판정
    is_healthy: bool = True
    unhealthy_reason: Optional[str] = None
```

### 3.2 Redis 키 구조

```python
# Canary 롤아웃 상태
# selfhealing:{namespace}:canary:rollout:{rollout_id}

# 클러스터별 적용 상태
# selfhealing:{namespace}:canary:cluster:{cluster_id}:config:{config_type}

# 메트릭 히스토리
# selfhealing:{namespace}:canary:metrics:{rollout_id}:{stage_name}
```

---

## 4. 핵심 서비스

### 4.1 CanaryRolloutService

```python
# packages/selfhealing-python/src/selfhealing/services/canary/service.py
"""
Canary Rollout Service.

설정 변경의 점진적 배포를 관리합니다.
"""
import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from selfhealing.services.canary.models import (
    CanaryRollout,
    CanaryStage,
    CanaryState,
    CanaryMetrics,
)
from selfhealing.services.config_history import (
    get_config_history_service,
    ConfigVersion,
)
from selfhealing.settings.namespace import get_key_prefix

logger = logging.getLogger(__name__)


class CanaryRolloutService:
    """
    Canary Rollout 관리 서비스.
    
    Features:
    - 단계별 롤아웃 생성
    - 자동/수동 프로모션
    - 메트릭 기반 자동 롤백
    - 클러스터별 상태 추적
    """
    
    ROLLOUT_KEY = "{prefix}canary:rollout:{rollout_id}"
    ACTIVE_ROLLOUTS_KEY = "{prefix}canary:active"
    
    def __init__(self):
        self._redis_client = None
        self._config_history = get_config_history_service()
    
    @property
    def redis_client(self):
        """Redis 클라이언트 (Lazy loading)."""
        if self._redis_client is None:
            from django.core.cache import caches
            cache = caches.get('default')
            if cache:
                self._redis_client = cache.client.get_client()
        return self._redis_client
    
    def create_rollout(
        self,
        config_type: str,
        new_values: Dict[str, Any],
        stages: List[CanaryStage],
        created_by: str,
        reason: str = "",
    ) -> CanaryRollout:
        """
        새 Canary 롤아웃 생성.
        
        Args:
            config_type: 설정 유형 (circuit_breaker, dlq 등)
            new_values: 새 설정값
            stages: 롤아웃 단계 정의
            created_by: 생성자
            reason: 변경 사유
            
        Returns:
            생성된 CanaryRollout
        """
        # 현재 설정 가져오기
        current_version = self._get_current_config(config_type)
        previous_values = current_version.values if current_version else {}
        
        rollout = CanaryRollout(
            id=str(uuid.uuid4())[:8],
            config_type=config_type,
            previous_values=previous_values,
            new_values=new_values,
            stages=stages,
            created_by=created_by,
            reason=reason,
        )
        
        # Redis에 저장
        self._save_rollout(rollout)
        self._add_to_active(rollout.id)
        
        logger.info(
            f"[CanaryRollout] Created: id={rollout.id}, "
            f"config={config_type}, stages={len(stages)}"
        )
        
        return rollout
    
    def start_rollout(self, rollout_id: str) -> bool:
        """
        Canary 롤아웃 시작 (첫 번째 단계 적용).
        
        Args:
            rollout_id: 롤아웃 ID
            
        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
            return False
        
        if rollout.state != CanaryState.CREATED:
            logger.warning(f"[CanaryRollout] Cannot start: state={rollout.state}")
            return False
        
        # 첫 번째 단계 클러스터에 적용
        stage = rollout.stages[0]
        self._apply_to_clusters(rollout, stage.clusters)
        
        rollout.state = CanaryState.CANARY
        rollout.current_stage_index = 0
        self._save_rollout(rollout)
        
        logger.info(
            f"[CanaryRollout] Started: id={rollout_id}, "
            f"stage={stage.name}, clusters={stage.clusters}"
        )
        
        return True
    
    def promote(self, rollout_id: str, force: bool = False) -> bool:
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
            logger.warning(f"[CanaryRollout] Cannot promote: state={rollout.state}")
            return False
        
        # 현재 단계 메트릭 검증 (force가 아니면)
        if not force:
            metrics = self._collect_stage_metrics(rollout)
            if not self._is_stage_healthy(rollout.current_stage, metrics):
                logger.warning(
                    f"[CanaryRollout] Promotion blocked: unhealthy metrics"
                )
                return False
        
        # 다음 단계로 이동
        next_index = rollout.current_stage_index + 1
        
        if next_index >= len(rollout.stages):
            # 모든 단계 완료
            rollout.state = CanaryState.COMPLETED
            rollout.completed_at = datetime.utcnow()
            self._remove_from_active(rollout.id)
        else:
            # 다음 단계 적용
            stage = rollout.stages[next_index]
            self._apply_to_clusters(rollout, stage.clusters)
            rollout.current_stage_index = next_index
            rollout.state = CanaryState.CANARY
        
        self._save_rollout(rollout)
        
        logger.info(
            f"[CanaryRollout] Promoted: id={rollout_id}, "
            f"stage_index={rollout.current_stage_index}"
        )
        
        return True
    
    def rollback(self, rollout_id: str, reason: str = "") -> bool:
        """
        롤백 수행.
        
        적용된 모든 클러스터를 이전 설정으로 복원.
        
        Args:
            rollout_id: 롤아웃 ID
            reason: 롤백 사유
            
        Returns:
            성공 여부
        """
        rollout = self.get_rollout(rollout_id)
        if not rollout:
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
        rollout.completed_at = datetime.utcnow()
        self._save_rollout(rollout)
        self._remove_from_active(rollout.id)
        
        logger.warning(
            f"[CanaryRollout] Rolled back: id={rollout_id}, reason={reason}"
        )
        
        return True
    
    def pause(self, rollout_id: str) -> bool:
        """롤아웃 일시 중지."""
        rollout = self.get_rollout(rollout_id)
        if not rollout or rollout.state != CanaryState.CANARY:
            return False
        
        rollout.state = CanaryState.PAUSED
        self._save_rollout(rollout)
        
        logger.info(f"[CanaryRollout] Paused: id={rollout_id}")
        return True
    
    def resume(self, rollout_id: str) -> bool:
        """일시 중지된 롤아웃 재개."""
        rollout = self.get_rollout(rollout_id)
        if not rollout or rollout.state != CanaryState.PAUSED:
            return False
        
        rollout.state = CanaryState.CANARY
        self._save_rollout(rollout)
        
        logger.info(f"[CanaryRollout] Resumed: id={rollout_id}")
        return True
    
    def get_rollout(self, rollout_id: str) -> Optional[CanaryRollout]:
        """롤아웃 조회."""
        if not self.redis_client:
            return None
        
        key = self._make_rollout_key(rollout_id)
        data = self.redis_client.get(key)
        
        if data:
            return self._deserialize_rollout(json.loads(data))
        return None
    
    def get_active_rollouts(self) -> List[CanaryRollout]:
        """활성 롤아웃 목록 조회."""
        if not self.redis_client:
            return []
        
        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        rollout_ids = self.redis_client.smembers(key)
        
        rollouts = []
        for rid in rollout_ids:
            rollout = self.get_rollout(rid)
            if rollout:
                rollouts.append(rollout)
        
        return rollouts
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _make_rollout_key(self, rollout_id: str) -> str:
        return self.ROLLOUT_KEY.format(
            prefix=get_key_prefix(),
            rollout_id=rollout_id,
        )
    
    def _save_rollout(self, rollout: CanaryRollout) -> None:
        key = self._make_rollout_key(rollout.id)
        data = self._serialize_rollout(rollout)
        self.redis_client.set(key, json.dumps(data), ex=86400 * 7)  # 7일 보관
    
    def _add_to_active(self, rollout_id: str) -> None:
        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        self.redis_client.sadd(key, rollout_id)
    
    def _remove_from_active(self, rollout_id: str) -> None:
        key = self.ACTIVE_ROLLOUTS_KEY.format(prefix=get_key_prefix())
        self.redis_client.srem(key, rollout_id)
    
    def _get_current_config(self, config_type: str) -> Optional[ConfigVersion]:
        """현재 설정 버전 조회."""
        history = self._config_history.get_history(config_type, limit=1)
        return history[0] if history else None
    
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
        # TODO: 클러스터 동기화 방식에 따른 구현
        logger.info(
            f"[CanaryRollout] Applied to cluster={cluster}, "
            f"config={config_type}"
        )
    
    def _collect_stage_metrics(
        self,
        rollout: CanaryRollout,
    ) -> List[CanaryMetrics]:
        """현재 단계의 메트릭 수집."""
        # TODO: Prometheus/메트릭 시스템 연동
        return []
    
    def _is_stage_healthy(
        self,
        stage: Optional[CanaryStage],
        metrics: List[CanaryMetrics],
    ) -> bool:
        """단계 건강 상태 판정."""
        if not stage or not metrics:
            return True  # 메트릭 없으면 통과
        
        for m in metrics:
            # 에러율 검사
            error_rate_increase = m.error_rate_after - m.error_rate_before
            if error_rate_increase > stage.error_rate_threshold:
                return False
            
            # 레이턴시 검사
            if m.latency_p99_before > 0:
                latency_increase = (
                    (m.latency_p99_after - m.latency_p99_before) /
                    m.latency_p99_before
                )
                if latency_increase > stage.latency_increase_threshold:
                    return False
        
        return True
    
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
                    duration_minutes=s["duration_minutes"],
                    auto_promote=s["auto_promote"],
                    error_rate_threshold=s["error_rate_threshold"],
                    latency_increase_threshold=s["latency_increase_threshold"],
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


# Singleton
_service: Optional[CanaryRolloutService] = None


def get_canary_rollout_service() -> CanaryRolloutService:
    """CanaryRolloutService 싱글톤 반환."""
    global _service
    if _service is None:
        _service = CanaryRolloutService()
    return _service
```

---

## 5. API 엔드포인트

### 5.1 Views

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/canary.py
"""
Canary Rollout API Views.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from selfhealing.api.django.permissions import IsSelfHealingAdmin
from selfhealing.services.canary.service import get_canary_rollout_service
from selfhealing.services.canary.models import CanaryStage


class CanaryRolloutListView(APIView):
    """
    GET /api/self-healing/canary/rollouts/
    POST /api/self-healing/canary/rollouts/
    """
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def get(self, request):
        """활성 롤아웃 목록 조회."""
        service = get_canary_rollout_service()
        rollouts = service.get_active_rollouts()
        
        return Response({
            "count": len(rollouts),
            "rollouts": [
                {
                    "id": r.id,
                    "config_type": r.config_type,
                    "state": r.state.value,
                    "current_stage": r.current_stage.name if r.current_stage else None,
                    "affected_clusters": r.affected_clusters,
                    "created_by": r.created_by,
                    "created_at": r.created_at.isoformat(),
                }
                for r in rollouts
            ]
        })
    
    def post(self, request):
        """새 롤아웃 생성."""
        service = get_canary_rollout_service()
        
        config_type = request.data.get("config_type")
        new_values = request.data.get("new_values", {})
        stages_data = request.data.get("stages", [])
        reason = request.data.get("reason", "")
        
        stages = [
            CanaryStage(
                name=s["name"],
                clusters=s["clusters"],
                percentage=s.get("percentage", 0),
                duration_minutes=s.get("duration_minutes", 5),
                auto_promote=s.get("auto_promote", True),
            )
            for s in stages_data
        ]
        
        rollout = service.create_rollout(
            config_type=config_type,
            new_values=new_values,
            stages=stages,
            created_by=request.user.username,
            reason=reason,
        )
        
        return Response({
            "id": rollout.id,
            "state": rollout.state.value,
            "stages": [s.name for s in rollout.stages],
        }, status=201)


class CanaryRolloutDetailView(APIView):
    """
    GET /api/self-healing/canary/rollouts/{rollout_id}/
    """
    permission_classes = [IsAuthenticated]
    
    def get(self, request, rollout_id):
        service = get_canary_rollout_service()
        rollout = service.get_rollout(rollout_id)
        
        if not rollout:
            return Response({"error": "Rollout not found"}, status=404)
        
        return Response({
            "id": rollout.id,
            "config_type": rollout.config_type,
            "state": rollout.state.value,
            "current_stage_index": rollout.current_stage_index,
            "previous_values": rollout.previous_values,
            "new_values": rollout.new_values,
            "stages": [
                {
                    "name": s.name,
                    "clusters": s.clusters,
                    "percentage": s.percentage,
                }
                for s in rollout.stages
            ],
            "affected_clusters": rollout.affected_clusters,
            "created_by": rollout.created_by,
            "created_at": rollout.created_at.isoformat(),
            "reason": rollout.reason,
        })


class CanaryRolloutActionView(APIView):
    """
    POST /api/self-healing/canary/rollouts/{rollout_id}/{action}/
    
    Actions: start, promote, rollback, pause, resume
    """
    permission_classes = [IsAuthenticated, IsSelfHealingAdmin]
    
    def post(self, request, rollout_id, action):
        service = get_canary_rollout_service()
        
        if action == "start":
            success = service.start_rollout(rollout_id)
        elif action == "promote":
            force = request.data.get("force", False)
            success = service.promote(rollout_id, force=force)
        elif action == "rollback":
            reason = request.data.get("reason", "Manual rollback")
            success = service.rollback(rollout_id, reason=reason)
        elif action == "pause":
            success = service.pause(rollout_id)
        elif action == "resume":
            success = service.resume(rollout_id)
        else:
            return Response({"error": f"Unknown action: {action}"}, status=400)
        
        if not success:
            return Response({"error": "Action failed"}, status=400)
        
        rollout = service.get_rollout(rollout_id)
        return Response({
            "id": rollout_id,
            "action": action,
            "new_state": rollout.state.value if rollout else "unknown",
        })
```

### 5.2 URL 설정

```python
# urls.py에 추가
from selfhealing.api.django.views.canary import (
    CanaryRolloutListView,
    CanaryRolloutDetailView,
    CanaryRolloutActionView,
)

urlpatterns += [
    path(
        "canary/rollouts/",
        CanaryRolloutListView.as_view(),
        name="canary-rollout-list",
    ),
    path(
        "canary/rollouts/<str:rollout_id>/",
        CanaryRolloutDetailView.as_view(),
        name="canary-rollout-detail",
    ),
    path(
        "canary/rollouts/<str:rollout_id>/<str:action>/",
        CanaryRolloutActionView.as_view(),
        name="canary-rollout-action",
    ),
]
```

---

## 6. 사용 예시

### 6.1 3단계 롤아웃 생성

```bash
# 1. 롤아웃 생성
POST /api/self-healing/canary/rollouts/
{
    "config_type": "circuit_breaker",
    "new_values": {
        "failure_threshold": 3,
        "recovery_timeout": 30
    },
    "stages": [
        {
            "name": "canary",
            "clusters": ["seoul-canary"],
            "percentage": 10,
            "duration_minutes": 5
        },
        {
            "name": "regional",
            "clusters": ["seoul-main", "tokyo"],
            "percentage": 60,
            "duration_minutes": 10
        },
        {
            "name": "global",
            "clusters": ["singapore", "frankfurt"],
            "percentage": 100,
            "duration_minutes": 0
        }
    ],
    "reason": "Reduce failure threshold for faster detection"
}

# Response
{
    "id": "abc123",
    "state": "created",
    "stages": ["canary", "regional", "global"]
}
```

### 6.2 롤아웃 진행

```bash
# 2. 시작 (첫 번째 단계)
POST /api/self-healing/canary/rollouts/abc123/start/

# 3. 5분 후 프로모션 (두 번째 단계)
POST /api/self-healing/canary/rollouts/abc123/promote/

# 4. 문제 발생 시 롤백
POST /api/self-healing/canary/rollouts/abc123/rollback/
{
    "reason": "Error rate increased by 10%"
}
```

### 6.3 자동 프로모션 (향후 구현)

```python
# 백그라운드 태스크
@celery_app.task
def check_canary_rollouts():
    """
    활성 롤아웃의 자동 프로모션/롤백 검사.
    
    주기: 1분마다 실행
    """
    service = get_canary_rollout_service()
    
    for rollout in service.get_active_rollouts():
        if rollout.state != CanaryState.CANARY:
            continue
        
        stage = rollout.current_stage
        if not stage or not stage.auto_promote:
            continue
        
        # 단계 시작 후 duration 경과 확인
        # 메트릭 검증
        # 자동 프로모션 또는 롤백
```

---

## 7. 테스트 계획

### 7.1 단위 테스트

```python
# tests/unit/services/canary/test_service.py

class TestCanaryRolloutService:
    def test_create_rollout(self):
        """롤아웃 생성."""
        service = CanaryRolloutService()
        
        rollout = service.create_rollout(
            config_type="circuit_breaker",
            new_values={"failure_threshold": 3},
            stages=[
                CanaryStage(name="canary", clusters=["seoul"], percentage=10),
            ],
            created_by="admin",
        )
        
        assert rollout.state == CanaryState.CREATED
        assert len(rollout.stages) == 1
    
    def test_start_applies_to_first_stage(self):
        """시작 시 첫 번째 단계 클러스터에만 적용."""
        # ...
    
    def test_promote_to_next_stage(self):
        """프로모션 시 다음 단계로 이동."""
        # ...
    
    def test_rollback_restores_previous_values(self):
        """롤백 시 이전 값으로 복원."""
        # ...
```

### 7.2 통합 테스트

```python
# tests/integration/canary/test_e2e_rollout.py

class TestCanaryE2E:
    def test_full_rollout_flow(self, redis_client):
        """전체 롤아웃 플로우 (생성 → 시작 → 프로모션 → 완료)."""
        pass
    
    def test_rollback_on_error_rate_increase(self, redis_client):
        """에러율 증가 시 자동 롤백."""
        pass
```

---

## 8. 관련 문서

- [70_MULTI_CLUSTER_ARCHITECTURE.md](70_MULTI_CLUSTER_ARCHITECTURE.md) - 다중 클러스터 아키텍처
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](../16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Config Versioning

---

## 9. 체크리스트

### Phase 1: 데이터 모델
- [ ] `services/canary/models.py` 생성
- [ ] 단위 테스트 작성

### Phase 2: 핵심 서비스
- [ ] `services/canary/service.py` 생성
- [ ] ConfigHistoryService 연동
- [ ] 단위 테스트 작성

### Phase 3: API
- [ ] `api/django/views/canary.py` 생성
- [ ] URL 등록
- [ ] API 테스트

### Phase 4: 자동화
- [ ] Celery 태스크 (자동 프로모션/롤백)
- [ ] 메트릭 연동 (Prometheus)
- [ ] 알림 연동 (Slack)

### Phase 5: 클러스터 동기화
- [ ] 같은 Redis 기반 동기화
- [ ] 별도 Redis 연결 (선택)
- [ ] API 기반 동기화 (선택)

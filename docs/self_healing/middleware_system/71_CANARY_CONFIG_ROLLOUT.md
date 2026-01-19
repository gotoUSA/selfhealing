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

## 9. 보완 설계

> 설계 리뷰를 통해 도출된 추가 요구사항

### 9.1 동시성 제어 (Config Lock)

**문제**: 동일 `config_type`에 대해 여러 운영자가 동시에 롤아웃을 시작하면 레이스 컨디션 발생

**해결**: `RedisDistributedLock` 활용 (기존 패턴 재사용)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/locking.py
"""
Canary Rollout Locking.

동시성 제어를 위한 Config Lock 메커니즘.
Reference: adapters/cache/redis_adapter.py#L34-155 (RedisDistributedLock)
"""
from datetime import timedelta
from typing import Optional
import logging

from selfhealing.settings.namespace import get_key_prefix

logger = logging.getLogger(__name__)


class ConfigLockError(Exception):
    """설정 락 획득 실패."""
    pass


class CanaryConfigLock:
    """
    Canary 롤아웃을 위한 설정 락.

    특정 config_type에 대해 하나의 롤아웃만 진행되도록 보장.
    """

    LOCK_KEY = "{prefix}canary:lock:{config_type}"
    LOCK_TIMEOUT = timedelta(minutes=30)  # 롤아웃 최대 시간

    def __init__(self, redis_client):
        self._redis = redis_client

    def acquire(self, config_type: str, rollout_id: str) -> bool:
        """
        설정 락 획득.

        Args:
            config_type: 설정 유형
            rollout_id: 롤아웃 ID (소유자 식별)

        Returns:
            락 획득 성공 여부
        """
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock

        lock_key = self.LOCK_KEY.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        lock = RedisDistributedLock(
            redis_client=self._redis,
            name=lock_key,
            timeout=self.LOCK_TIMEOUT,
        )

        if lock.acquire(blocking=False):
            logger.info(f"[CanaryLock] Acquired: config={config_type}, rollout={rollout_id}")
            return True

        # 이미 락이 있는 경우 - 누가 소유하고 있는지 확인
        current_owner = self._redis.get(f"lock:{lock_key}")
        logger.warning(
            f"[CanaryLock] Failed to acquire: config={config_type}, "
            f"current_owner={current_owner}"
        )
        return False

    def release(self, config_type: str, rollout_id: str) -> bool:
        """설정 락 해제."""
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock

        lock_key = self.LOCK_KEY.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )

        lock = RedisDistributedLock(
            redis_client=self._redis,
            name=lock_key,
            timeout=self.LOCK_TIMEOUT,
        )

        try:
            lock.release()
            logger.info(f"[CanaryLock] Released: config={config_type}, rollout={rollout_id}")
            return True
        except Exception as e:
            logger.warning(f"[CanaryLock] Release failed: {e}")
            return False

    def is_locked(self, config_type: str) -> bool:
        """락 상태 확인."""
        lock_key = self.LOCK_KEY.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )
        return self._redis.exists(f"lock:{lock_key}") > 0

    def get_lock_owner(self, config_type: str) -> Optional[str]:
        """현재 락 소유자 조회."""
        lock_key = self.LOCK_KEY.format(
            prefix=get_key_prefix(),
            config_type=config_type,
        )
        owner = self._redis.get(f"lock:{lock_key}")
        if owner and isinstance(owner, bytes):
            owner = owner.decode('utf-8')
        return owner
```

**서비스 통합:**

```python
# CanaryRolloutService.create_rollout() 수정
def create_rollout(self, config_type: str, ...) -> CanaryRollout:
    # 락 획득 시도
    lock = CanaryConfigLock(self.redis_client)

    if lock.is_locked(config_type):
        owner = lock.get_lock_owner(config_type)
        raise ConfigLockError(
            f"Config '{config_type}' is already in rollout. "
            f"Current owner: {owner}"
        )

    # 롤아웃 생성
    rollout = CanaryRollout(...)

    # 락 획득
    if not lock.acquire(config_type, rollout.id):
        raise ConfigLockError(f"Failed to acquire lock for {config_type}")

    # 저장 및 반환
    self._save_rollout(rollout)
    return rollout
```

---

### 9.2 건강 지표 정량화 (Pass Criteria)

**문제**: 자동 프로모션/롤백 판단 기준이 추상적

**해결**: 정량적 합격 기준 스키마 정의

```python
# packages/selfhealing-python/src/selfhealing/services/canary/models.py 추가
@dataclass
class PassCriteria:
    """
    자동 프로모션을 위한 합격 기준.

    모든 조건을 만족해야 프로모션 허용.
    Reference: SafetyGuard 패턴 (chaos/safety_guard/guard.py)
    """

    # 에러율 관련
    error_rate_absolute_max: float = 0.05      # 5% 절대 한계
    error_rate_increase_max: float = 0.01      # 1% 증가 한계

    # 레이턴시 관련
    latency_p95_delta_ms: float = 50.0         # p95 50ms 증가 한계
    latency_p99_delta_pct: float = 0.2         # p99 20% 증가 한계

    # Error Budget 관련
    error_budget_drain_rate_max: float = 1.2   # 1.2x 소진률 한계
    error_budget_remaining_min: float = 0.1    # 10% 이상 남아있어야 함

    # 평가 기간
    min_requests_required: int = 100           # 최소 샘플 수
    evaluation_window_seconds: int = 300       # 5분 윈도우

    def evaluate(self, metrics: "CanaryMetrics") -> Tuple[bool, Optional[str]]:
        """
        메트릭 평가.

        Returns:
            (합격 여부, 실패 사유)
        """
        # 최소 샘플 수 확인
        if metrics.requests_total < self.min_requests_required:
            return True, None  # 샘플 부족 - 통과 (보수적)

        # 에러율 절대값 검사
        if metrics.error_rate_after > self.error_rate_absolute_max:
            return False, f"Error rate {metrics.error_rate_after:.2%} exceeds {self.error_rate_absolute_max:.2%}"

        # 에러율 증가분 검사
        error_increase = metrics.error_rate_after - metrics.error_rate_before
        if error_increase > self.error_rate_increase_max:
            return False, f"Error rate increased by {error_increase:.2%} (max: {self.error_rate_increase_max:.2%})"

        # p99 레이턴시 검사
        if metrics.latency_p99_before > 0:
            latency_pct = (metrics.latency_p99_after - metrics.latency_p99_before) / metrics.latency_p99_before
            if latency_pct > self.latency_p99_delta_pct:
                return False, f"p99 latency increased by {latency_pct:.1%} (max: {self.latency_p99_delta_pct:.1%})"

        return True, None
```

**CanaryStage 확장:**

```python
@dataclass
class CanaryStage:
    """Canary 단계 정의."""
    name: str
    clusters: List[str]
    percentage: float
    duration_minutes: int = 5

    # 자동 프로모션 조건
    auto_promote: bool = True
    pass_criteria: PassCriteria = field(default_factory=PassCriteria)  # 확장
```

---

### 9.3 Zombie Rollout Watchdog

**문제**: Celery 워커 장애 시 롤아웃이 특정 단계에 멈춤

**해결**: 주기적 스캔 태스크 (기존 ChaosExperimentCleaner 패턴)

```python
# packages/selfhealing-python/src/selfhealing/tasks/canary_watchdog.py
"""
Canary Rollout Watchdog.

Zombie 롤아웃 감지 및 자동 롤백.
Reference: tasks/drift_detection.py (ChaosExperimentCleaner 패턴)
"""
import logging
from datetime import datetime, timedelta
from typing import List

from celery import shared_task

from selfhealing.services.canary.service import get_canary_rollout_service
from selfhealing.services.canary.models import CanaryState, CanaryRollout

logger = logging.getLogger(__name__)


class RolloutWatchdog:
    """
    Zombie 롤아웃 감지 및 처리.

    Zombie 판정 기준:
    - CANARY 상태에서 duration_minutes * 2 초과
    - PROMOTING 상태에서 5분 초과 (정상 프로모션은 수초)
    """

    ZOMBIE_MULTIPLIER = 2.0  # duration의 2배 초과 시 Zombie
    PROMOTING_TIMEOUT_MINUTES = 5

    def __init__(self):
        self._service = get_canary_rollout_service()

    def scan_and_cleanup(self) -> List[str]:
        """
        Zombie 롤아웃 스캔 및 정리.

        Returns:
            롤백된 rollout_id 목록
        """
        cleaned = []

        for rollout in self._service.get_active_rollouts():
            if self._is_zombie(rollout):
                logger.warning(
                    f"[Watchdog] Zombie detected: id={rollout.id}, "
                    f"state={rollout.state}, stage={rollout.current_stage_index}"
                )

                # 자동 롤백
                success = self._service.rollback(
                    rollout.id,
                    reason=f"Zombie rollout auto-cleanup (watchdog)"
                )

                if success:
                    cleaned.append(rollout.id)
                    self._notify_zombie_cleanup(rollout)

        return cleaned

    def _is_zombie(self, rollout: CanaryRollout) -> bool:
        """Zombie 여부 판정."""
        if rollout.state == CanaryState.CANARY:
            stage = rollout.current_stage
            if not stage:
                return True  # 단계 정보 없음 - 비정상

            max_age = timedelta(minutes=stage.duration_minutes * self.ZOMBIE_MULTIPLIER)
            # stage_started_at 필드 필요 (모델 확장)
            stage_age = datetime.utcnow() - rollout.created_at  # 임시: 생성 시간 기준
            return stage_age > max_age

        if rollout.state == CanaryState.PROMOTING:
            max_age = timedelta(minutes=self.PROMOTING_TIMEOUT_MINUTES)
            age = datetime.utcnow() - rollout.created_at
            return age > max_age

        return False

    def _notify_zombie_cleanup(self, rollout: CanaryRollout) -> None:
        """Zombie 정리 알림."""
        try:
            from selfhealing.services.unified_notification import get_notification_service
            service = get_notification_service()
            service.notify(
                channel="slack",
                level="warning",
                title="Zombie Rollout Auto-Cleanup",
                message=f"Rollout {rollout.id} ({rollout.config_type}) was auto-rolled back by watchdog",
                details={
                    "rollout_id": rollout.id,
                    "config_type": rollout.config_type,
                    "last_state": rollout.state.value,
                    "created_by": rollout.created_by,
                }
            )
        except Exception as e:
            logger.warning(f"[Watchdog] Notification failed: {e}")


@shared_task(name="canary.watchdog.scan")
def scan_zombie_rollouts() -> dict:
    """
    Celery Beat 태스크: Zombie 롤아웃 스캔.

    주기: 1분마다 실행 권장

    Returns:
        스캔 결과
    """
    watchdog = RolloutWatchdog()
    cleaned = watchdog.scan_and_cleanup()

    return {
        "scanned_at": datetime.utcnow().isoformat(),
        "zombies_cleaned": len(cleaned),
        "cleaned_ids": cleaned,
    }
```

**Celery Beat 설정:**

```python
# celery_app.py
CELERY_BEAT_SCHEDULE = {
    "canary-watchdog": {
        "task": "canary.watchdog.scan",
        "schedule": 60.0,  # 1분마다
    },
}
```

---

### 9.4 Optimistic Locking + Audit 로그

**문제**: 롤백 중 다른 운영자가 수동 변경 시 충돌 감지 불가

**해결**: Version 기반 Optimistic Locking + 충돌 Audit

```python
# packages/selfhealing-python/src/selfhealing/services/canary/versioning.py
"""
Config Version Conflict Handling.

Optimistic Locking을 통한 버전 충돌 감지.
Reference: config_history.py#L316-349 (rollback 로직)
"""
import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

from selfhealing.audit.self_audit import SelfAuditLogger, SelfAuditEvent

logger = logging.getLogger(__name__)


class VersionConflictError(Exception):
    """설정 버전 충돌."""

    def __init__(
        self,
        expected_version: int,
        actual_version: int,
        conflicting_operator: str,
        config_type: str,
    ):
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.conflicting_operator = conflicting_operator
        self.config_type = config_type

        super().__init__(
            f"Version conflict: expected v{expected_version}, "
            f"actual v{actual_version} (modified by {conflicting_operator})"
        )


@dataclass
class VersionCheck:
    """버전 체크 결과."""
    is_valid: bool
    expected_version: int
    actual_version: int
    conflicting_operator: Optional[str] = None


def check_version_and_rollback(
    config_type: str,
    target_version: int,
    expected_current_version: int,
    rolled_back_by: str,
) -> "ConfigVersion":
    """
    버전 확인 후 롤백 수행.

    충돌 시 VersionConflictError 발생 + Audit 로그.

    Args:
        config_type: 설정 유형
        target_version: 롤백할 버전
        expected_current_version: 예상 현재 버전 (낙관적 락)
        rolled_back_by: 롤백 수행자

    Returns:
        새로 생성된 롤백 버전

    Raises:
        VersionConflictError: 버전 충돌 시
    """
    from selfhealing.services.config_history import get_config_history_service

    service = get_config_history_service()
    current = service.get_current_version(config_type)

    if current and current.version != expected_current_version:
        # 충돌 감지 - Audit 로그
        _log_version_conflict(
            config_type=config_type,
            expected_version=expected_current_version,
            actual_version=current.version,
            conflicting_operator=current.changed_by,
            attempted_by=rolled_back_by,
        )

        raise VersionConflictError(
            expected_version=expected_current_version,
            actual_version=current.version,
            conflicting_operator=current.changed_by,
            config_type=config_type,
        )

    # 버전 일치 - 롤백 수행
    return service.rollback(config_type, target_version, rolled_back_by)


def _log_version_conflict(
    config_type: str,
    expected_version: int,
    actual_version: int,
    conflicting_operator: str,
    attempted_by: str,
) -> None:
    """버전 충돌 Audit 로그."""
    # SelfAuditEvent에 CONFIG_CONFLICT 추가 필요
    # 임시로 기존 이벤트 사용
    from selfhealing.audit.self_audit import self_audit

    self_audit().log(
        event_type=SelfAuditEvent.REDIS_WRITE_FAILED,  # TODO: CONFIG_CONFLICT 추가
        message=f"Config rollback version conflict on {config_type}",
        details={
            "config_type": config_type,
            "expected_version": expected_version,
            "actual_version": actual_version,
            "conflicting_operator": conflicting_operator,
            "attempted_by": attempted_by,
            "conflict_time": datetime.utcnow().isoformat(),
            "action": "rollback_blocked",
        }
    )

    logger.warning(
        f"[VersionConflict] {config_type}: expected v{expected_version}, "
        f"actual v{actual_version} by {conflicting_operator}"
    )
```

---

### 9.5 Panic Rollback + RBAC

**문제**: 긴급 상황에서 모든 클러스터를 즉시 롤백해야 하나, 권한 체계 필요

**해결**: 전용 권한 클래스 + 4-Eyes 승인 강제

```python
# packages/selfhealing-python/src/selfhealing/api/django/permissions.py 추가
class IsPanicRollbackAuthorized(BasePermission):
    """
    Panic Rollback 전용 권한.

    조건:
    - IsSelfHealingAdmin 필수
    - 4-Eyes Dual Approval 강제 (임계값 무시)
    - Emergency reason 필수

    Reference: EmergencyEscalationPermission 패턴
    """

    message = "Panic Rollback은 Admin 2인 승인이 필요합니다."

    def has_permission(self, request: Request, view: APIView) -> bool:
        # 1. Admin 권한 확인
        if not IsSelfHealingAdmin().has_permission(request, view):
            return False

        # 2. 사유 필수
        reason = request.data.get("reason", "").strip()
        if not reason:
            self.message = "Panic Rollback 시 reason(사유)는 필수입니다."
            logger.warning(f"[RBAC] Panic rollback denied - reason required")
            return False

        # 3. 4-Eyes 승인 강제
        approval_id = request.data.get("approval_id")
        if not approval_id:
            self.message = (
                "Panic Rollback은 4-Eyes 듀얼 승인이 필요합니다. "
                "다른 Admin에게 승인을 요청하세요."
            )
            self._notify_panic_rollback_requested(request.user, reason)
            logger.warning(f"[RBAC] Panic rollback requires dual approval")
            return False

        # 4. 승인 검증
        return self._verify_dual_approval(approval_id)

    def _notify_panic_rollback_requested(self, user, reason: str) -> None:
        """Panic Rollback 승인 요청 알림."""
        try:
            from selfhealing.services.unified_notification import get_notification_service
            service = get_notification_service()
            service.notify(
                channel="slack",
                level="critical",
                title="🚨 Panic Rollback 승인 요청",
                message=f"{user}가 Panic Rollback을 요청했습니다. 사유: {reason}",
            )
        except Exception as e:
            logger.warning(f"[RBAC] Notification failed: {e}")

    def _verify_dual_approval(self, approval_id: str) -> bool:
        """승인 검증 (DualApprovalPermission 패턴)."""
        # 기존 _check_dual_approval_required 로직 재사용
        try:
            from selfhealing.services.runtime_config import get_approval_service
            approval = get_approval_service().get_approval(approval_id)
            return approval and approval.get("status") == "approved"
        except Exception as e:
            logger.warning(f"[RBAC] Approval verification failed: {e}")
            return False
```

**API View 적용:**

```python
# views/canary.py
class CanaryRolloutActionView(APIView):
    def get_permissions(self):
        action = self.kwargs.get("action", "")

        if action == "panic_rollback":
            return [IsAuthenticated(), IsPanicRollbackAuthorized()]

        return [IsAuthenticated(), IsSelfHealingAdmin()]
```

---

### 9.6 Chaos 실험 충돌 방어 (Smart 정책)

**문제**: 카오스 실험 중 Canary 롤아웃 시 원인 분석 불가

**해결**: Smart 정책 기본 + force_during_chaos 옵션

```python
# packages/selfhealing-python/src/selfhealing/services/canary/chaos_guard.py
"""
Chaos Experiment Guard for Canary Rollouts.

카오스 실험과의 충돌 방어.
Reference: chaos/safety_guard/guard.py
"""
import logging
from typing import List, Set, Optional
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class ChaosConflictPolicy(str, Enum):
    """카오스 충돌 정책."""
    STRICT = "strict"    # 카오스 실험 중 Canary 완전 차단
    SMART = "smart"      # 카오스 중인 클러스터만 제외
    LOOSE = "loose"      # 경고 후 진행


@dataclass
class ChaosConflictResult:
    """충돌 검사 결과."""
    has_conflict: bool
    chaos_clusters: List[str]
    safe_clusters: List[str]
    policy_applied: ChaosConflictPolicy
    can_proceed: bool
    warning_message: Optional[str] = None


class CanaryChaosGuard:
    """
    Canary Rollout과 Chaos 실험 간 충돌 방어.

    기본 정책: SMART (실험 중인 클러스터만 제외)
    """

    DEFAULT_POLICY = ChaosConflictPolicy.SMART

    def __init__(self, policy: ChaosConflictPolicy = None):
        self._policy = policy or self.DEFAULT_POLICY

    def check_conflict(
        self,
        target_clusters: List[str],
        force_during_chaos: bool = False,
    ) -> ChaosConflictResult:
        """
        카오스 실험 충돌 검사.

        Args:
            target_clusters: 롤아웃 대상 클러스터
            force_during_chaos: 강제 진행 플래그

        Returns:
            충돌 검사 결과
        """
        chaos_clusters = self._get_clusters_with_active_chaos()
        target_set = set(target_clusters)
        conflict_set = target_set & chaos_clusters
        safe_set = target_set - chaos_clusters

        has_conflict = len(conflict_set) > 0

        if not has_conflict:
            return ChaosConflictResult(
                has_conflict=False,
                chaos_clusters=[],
                safe_clusters=target_clusters,
                policy_applied=self._policy,
                can_proceed=True,
            )

        # 충돌 있음 - 정책 적용
        if force_during_chaos:
            # 강제 진행 - 경고만
            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=target_clusters,  # 전체 진행
                policy_applied=ChaosConflictPolicy.LOOSE,
                can_proceed=True,
                warning_message=(
                    f"FORCE: Proceeding despite active chaos on {conflict_set}"
                ),
            )

        if self._policy == ChaosConflictPolicy.STRICT:
            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=[],
                policy_applied=self._policy,
                can_proceed=False,
                warning_message="STRICT: Blocked due to active chaos experiments",
            )

        if self._policy == ChaosConflictPolicy.SMART:
            if not safe_set:
                # 모든 클러스터가 카오스 중
                return ChaosConflictResult(
                    has_conflict=True,
                    chaos_clusters=list(conflict_set),
                    safe_clusters=[],
                    policy_applied=self._policy,
                    can_proceed=False,
                    warning_message="SMART: All target clusters have active chaos",
                )

            return ChaosConflictResult(
                has_conflict=True,
                chaos_clusters=list(conflict_set),
                safe_clusters=list(safe_set),  # 안전한 클러스터만
                policy_applied=self._policy,
                can_proceed=True,
                warning_message=(
                    f"SMART: Excluding chaos clusters {conflict_set}, "
                    f"proceeding with {safe_set}"
                ),
            )

        # LOOSE
        return ChaosConflictResult(
            has_conflict=True,
            chaos_clusters=list(conflict_set),
            safe_clusters=target_clusters,
            policy_applied=self._policy,
            can_proceed=True,
            warning_message=f"LOOSE: Warning - active chaos on {conflict_set}",
        )

    def _get_clusters_with_active_chaos(self) -> Set[str]:
        """활성 카오스 실험 중인 클러스터 조회."""
        try:
            from selfhealing.services.chaos.scheduler import get_scheduler_service
            from selfhealing.services.chaos_context import ChaosExperimentStatus

            scheduler = get_scheduler_service()
            active_experiments = scheduler.get_experiments_by_status(
                ChaosExperimentStatus.ACTIVE
            )

            # 실험에서 클러스터 추출 (실험 메타데이터 기반)
            clusters = set()
            for exp in active_experiments:
                if hasattr(exp, 'target_cluster'):
                    clusters.add(exp.target_cluster)

            return clusters

        except Exception as e:
            logger.warning(f"[ChaosGuard] Failed to get active chaos: {e}")
            return set()
```

---

### 9.7 Audit 필수 필드

**문제**: Canary 롤아웃 액션에 대한 포렌식 추적 미흡

**해결**: 표준 Audit 필드 정의 및 로깅

```python
# packages/selfhealing-python/src/selfhealing/services/canary/audit.py
"""
Canary Rollout Audit Logging.

모든 Canary 액션에 대한 포렌식 로그.
Reference: audit/self_audit.py, services/audit_helpers.py
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def log_canary_action(
    action: str,
    rollout: "CanaryRollout",
    safety_check_result: Optional[Dict] = None,
    additional_context: Optional[Dict] = None,
) -> None:
    """
    Canary Rollout 액션 Audit 로그.

    Args:
        action: 액션 유형 (start, promote, rollback, pause, resume, panic_rollback)
        rollout: 롤아웃 정보
        safety_check_result: 사전 검사 결과 (카오스 충돌 등)
        additional_context: 추가 컨텍스트
    """
    from selfhealing.audit import get_audit_logger

    # 해시 계산
    def compute_hash(values: Dict) -> str:
        sorted_str = json.dumps(values, sort_keys=True)
        return hashlib.sha256(sorted_str.encode()).hexdigest()[:16]

    audit_entry = {
        # 필수 필드
        "canary_rollout_id": rollout.id,
        "previous_version_hash": compute_hash(rollout.previous_values),
        "new_version_hash": compute_hash(rollout.new_values),
        "safety_check_result": safety_check_result or {"checked": False},

        # 컨텍스트
        "action": action,
        "config_type": rollout.config_type,
        "current_stage": rollout.current_stage.name if rollout.current_stage else None,
        "current_stage_index": rollout.current_stage_index,
        "affected_clusters": rollout.affected_clusters,
        "initiated_by": rollout.created_by,
        "reason": rollout.reason,
        "timestamp": datetime.utcnow().isoformat(),
    }

    if additional_context:
        audit_entry["additional_context"] = additional_context

    # 로깅
    try:
        audit_logger = get_audit_logger()
        audit_logger.log_config_change(
            config_type=f"canary:{rollout.config_type}",
            action=f"canary_{action}",
            old_value=rollout.previous_values,
            new_value=rollout.new_values,
            changed_by=rollout.created_by,
            reason=rollout.reason,
            extra=audit_entry,
        )
    except Exception as e:
        logger.warning(f"[CanaryAudit] Audit log failed: {e}")

    # 표준 로그
    logger.info(
        f"[CanaryAudit] {action}: rollout={rollout.id}, "
        f"config={rollout.config_type}, stage={rollout.current_stage_index}, "
        f"clusters={rollout.affected_clusters}"
    )
```

---

### 9.8 Request-level Canary (Phase 2)

**문제**: 단일 클러스터 내 10% 트래픽만 적용 필요

**해결**: Feature Flag 기반 요청 분기 (서비스 메시 없이)

```python
# packages/selfhealing-python/src/selfhealing/services/canary/feature_flag.py
"""
Request-level Canary Feature Flag.

서비스 메시 없이 단일 클러스터 내 부분 적용 지원.
Consistent Hashing 기반으로 동일 요청은 항상 같은 설정 사용.
"""
import hashlib
import logging
from typing import Any, Dict, Optional

from selfhealing.services.canary.models import CanaryRollout

logger = logging.getLogger(__name__)


class CanaryFeatureFlag:
    """
    Request-level Canary Feature Flag.

    Usage:
        flag = CanaryFeatureFlag()

        if flag.should_use_canary_config(request_id, rollout):
            config = rollout.new_values
        else:
            config = rollout.previous_values
    """

    def should_use_canary_config(
        self,
        request_id: str,
        rollout: CanaryRollout,
    ) -> bool:
        """
        요청별 Canary 설정 적용 여부 결정.

        Consistent Hashing으로 동일 request_id는 항상 같은 결과.

        Args:
            request_id: 요청 고유 ID (예: X-Request-ID 헤더)
            rollout: 롤아웃 정보

        Returns:
            Canary 설정 사용 여부
        """
        if not rollout.current_stage:
            return False

        percentage = rollout.current_stage.percentage

        # Consistent Hashing
        hash_input = f"{rollout.id}:{request_id}"
        hash_value = int(hashlib.md5(hash_input.encode()).hexdigest()[:8], 16)
        bucket = hash_value % 100

        return bucket < percentage

    def get_effective_config(
        self,
        request_id: str,
        config_type: str,
    ) -> Dict[str, Any]:
        """
        요청에 대한 유효 설정 반환.

        활성 Canary가 있으면 비율에 따라 분기,
        없으면 현재 설정 반환.
        """
        from selfhealing.services.canary.service import get_canary_rollout_service
        from selfhealing.services.config_history import get_config_history_service

        canary_service = get_canary_rollout_service()
        config_service = get_config_history_service()

        # 활성 롤아웃 확인
        for rollout in canary_service.get_active_rollouts():
            if rollout.config_type != config_type:
                continue

            if self.should_use_canary_config(request_id, rollout):
                logger.debug(
                    f"[FeatureFlag] Canary config for {request_id}: "
                    f"rollout={rollout.id}"
                )
                return rollout.new_values

        # 기본 설정
        current = config_service.get_current_version(config_type)
        return current.values if current else {}
```

**Note**: 이 방식은 서비스 메시(Istio, Linkerd) 없이도 동작하며, 추가 인프라 의존성이 없습니다.

---

### 9.9 대기업/멀티클러스터 환경 배포 가이드

**기존 구현 활용:**

| 컴포넌트 | 파일 | 역할 |
|----------|------|------|
| `NamespaceSettings` | `settings/namespace.py` | 클러스터별 Redis 키 분리 |
| `ClusterIdentity` | `core/cluster_identity.py` | 클러스터 식별 (SSOT) |
| `TieredRedisProvider` | `core/tiered_redis.py` | LOCAL/GLOBAL Redis 분리 |
| `GlobalConfigPropagator` | `services/config/propagator.py` | 크로스 클러스터 설정 전파 |

**대규모 환경 아키텍처:**

```
┌─────────────────────────────────────────────────────────────────┐
│              Global Redis (AWS ElastiCache Global Datastore)    │
│              - 설정 전파 (Pub/Sub)                               │
│              - 글로벌 앵커 (Error Budget, Governance)           │
└─────────────────────────────────────────────────────────────────┘
         ↑ GlobalConfigPropagator (Pub/Sub)           ↑
         │                                            │
┌────────┴────────┐  ┌────────────────┐  ┌───────────┴────────┐
│ Seoul Cluster   │  │ Tokyo Cluster  │  │ Singapore Cluster  │
│ ┌─────────────┐ │  │ ┌────────────┐ │  │ ┌────────────────┐ │
│ │ LOCAL Redis │ │  │ │ LOCAL Redis│ │  │ │ LOCAL Redis    │ │
│ │ - CB 상태   │ │  │ │ - CB 상태   │ │  │ │ - CB 상태      │ │
│ │ - DLQ       │ │  │ │ - DLQ      │ │  │ │ - DLQ          │ │
│ │ - 메트릭    │ │  │ │ - 메트릭    │ │  │ │ - 메트릭       │ │
│ └─────────────┘ │  │ └────────────┘ │  │ └────────────────┘ │
└─────────────────┘  └────────────────┘  └────────────────────┘
```

**Kubernetes 배포 예시:**

```yaml
# configmap.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: selfhealing-config
data:
  SELFHEALING_NAMESPACE_ENABLED: "true"
  SELFHEALING_NAMESPACE: "seoul"
  SELFHEALING_CLUSTER_ID: "seoul-prod-1"
  REDIS_URL: "redis://local-redis.selfhealing:6379/0"
  REDIS_GLOBAL_URL: "redis://global-redis.selfhealing:6379/0"
```

**서비스 메시 없이 Request-level Canary:**

위 섹션 9.8의 `CanaryFeatureFlag`는 서비스 메시(Istio 등) **없이** 동작합니다.
- 추가 인프라 설치: ❌ 불필요
- Python 코드만으로 구현
- Consistent Hashing 기반 트래픽 분기

---

## 10. 구현 순서 및 체크리스트

> **중요**: 아래 순서는 의존 관계를 반영한 것입니다. Step 순서대로 진행해야 합니다.

### 구현 순서 요약

```
┌─────────────────────────────────────────────────────────────────────┐
│ Step 1: 데이터 모델                                                  │
│ └── models.py (CanaryState, CanaryStage, CanaryRollout, PassCriteria)│
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 2: 핵심 서비스                                                  │
│ ├── 2A. 서브 컴포넌트 (병렬 가능)                                    │
│ │   ├── locking.py (Config Lock)                                    │
│ │   ├── versioning.py (Optimistic Lock)                             │
│ │   ├── chaos_guard.py (Chaos 충돌 방어)                            │
│ │   └── audit.py (Audit 로깅)                                       │
│ └── 2B. 메인 서비스 (2A 완료 후)                                     │
│     └── service.py (CanaryRolloutService)                           │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 3: API                                                          │
│ ├── permissions.py (IsPanicRollbackAuthorized)                      │
│ ├── views/canary.py (List, Detail, Action Views)                    │
│ └── urls.py (URL 등록)                                               │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 4: 자동화                                                       │
│ ├── tasks/canary_watchdog.py (Zombie 방어)                          │
│ ├── Celery Beat 설정                                                 │
│ ├── Prometheus 메트릭                                                │
│ └── Slack 알림                                                       │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 5: 클러스터 동기화 (대부분 추가 구현 불필요)                     │
│ ├── A/B/C. 독립 운영 → 환경변수 설정만                               │
│ └── D. 크로스 클러스터 알림 (선택) → cross_cluster.py                │
└─────────────────────────────────────────────────────────────────────┘
                                    ↓
┌─────────────────────────────────────────────────────────────────────┐
│ Step 6: Request-level Canary (선택)                                  │
│ └── feature_flag.py (단일 클러스터 내 부분 적용 필요시)              │
└─────────────────────────────────────────────────────────────────────┘
```

---

### Step 1: 데이터 모델 (필수 선행) ✅ 완료

> 다른 모든 컴포넌트가 의존하는 기반 모델

- [x] `services/canary/__init__.py` 생성
- [x] `services/canary/models.py` 생성
  - [x] `CanaryState` enum
  - [x] `CanaryStage` dataclass
  - [x] `CanaryRollout` dataclass
  - [x] `CanaryMetrics` dataclass
  - [x] `PassCriteria` dataclass
- [x] 단위 테스트: `tests/unit/services/canary/test_models.py` (30 tests passed)

### Step 2: 핵심 서비스 (Step 1 완료 후)

> 서브 컴포넌트들은 병렬로 개발 가능. service.py는 모든 서브 컴포넌트 완료 후.

**2A. 서브 컴포넌트 (병렬 개발 가능):**

- [ ] `services/canary/locking.py` (Config Lock)
  - [ ] `ConfigLockError` 예외
  - [ ] `CanaryConfigLock` 클래스
  - [ ] 단위 테스트

- [ ] `services/canary/versioning.py` (Optimistic Lock)
  - [ ] `VersionConflictError` 예외
  - [ ] `check_version_and_rollback()` 함수
  - [ ] 단위 테스트

- [ ] `services/canary/chaos_guard.py` (Chaos 충돌 방어)
  - [ ] `ChaosConflictPolicy` enum
  - [ ] `CanaryChaosGuard` 클래스
  - [ ] 단위 테스트

- [ ] `services/canary/audit.py` (Audit 로깅)
  - [ ] `log_canary_action()` 함수
  - [ ] 단위 테스트

**2B. 메인 서비스 (2A 모두 완료 후):**

- [ ] `services/canary/service.py` (CanaryRolloutService)
  - [ ] `create_rollout()` - locking.py 연동
  - [ ] `start_rollout()` - chaos_guard.py 연동
  - [ ] `promote()` - versioning.py 연동
  - [ ] `rollback()` - audit.py 연동
  - [ ] `pause()`, `resume()`
  - [ ] ConfigHistoryService 연동
- [ ] 통합 테스트: `tests/integration/canary/test_service.py`

### Step 3: API (Step 2 완료 후)

- [ ] `api/django/permissions.py`에 `IsPanicRollbackAuthorized` 추가
- [ ] `api/django/views/canary.py` 생성
  - [ ] `CanaryRolloutListView`
  - [ ] `CanaryRolloutDetailView`
  - [ ] `CanaryRolloutActionView`
- [ ] `api/django/urls.py`에 URL 등록
- [ ] API 테스트: `tests/api/canary/test_views.py`

### Step 4: 자동화 (Step 3 완료 후)

- [ ] `tasks/canary_watchdog.py` 생성
  - [ ] `RolloutWatchdog` 클래스
  - [ ] `scan_zombie_rollouts` Celery 태스크
- [ ] Celery Beat 설정 추가
- [ ] Prometheus 메트릭 연동
  - [ ] `canary_rollout_total` counter
  - [ ] `canary_rollout_duration_seconds` histogram
- [ ] Slack 알림 연동
- [ ] 통합 테스트

### Step 5: 클러스터 동기화 (환경에 따라 선택)

> **대부분의 경우 추가 구현 불필요**. 환경변수 설정만으로 동작.

#### 클러스터 운영 방식별 선택

| 운영 방식 | Redis 구성 | 추가 구현 |
|-----------|------------|:---------:|
| **A. 단일 클러스터** | 단일 Redis | ❌ 없음 |
| **B. 완전 독립 멀티클러스터** | 각 클러스터 자체 Redis | ❌ 없음 |
| **C. 같은 리전 내 멀티클러스터** | 같은 Redis 공유 (키 분리) | ❌ 없음 |
| **D. 크로스 클러스터 설정 전파** | LOCAL + GLOBAL Redis | ⭐ 필요 |

#### A/B/C. 독립 운영 (대부분의 경우)

각 클러스터가 독립적으로 설정을 관리하는 경우:

```bash
# 예시: Cluster A (실제 환경에 맞게 네이밍)
SELFHEALING_NAMESPACE_ENABLED=true
SELFHEALING_NAMESPACE=cluster-a
REDIS_URL=redis://redis.cluster-a:6379/0

# 예시: Cluster B (완전 독립)
SELFHEALING_NAMESPACE_ENABLED=true
SELFHEALING_NAMESPACE=cluster-b
REDIS_URL=redis://redis.cluster-b:6379/0  # 자체 Redis
```

> **Note**: 위 예시의 클러스터명(cluster-a, cluster-b)은 실제 환경에 맞게 변경하세요.

**추가 구현 불필요**. 환경변수만 설정하면 됨.

- [ ] 환경변수 설정 확인
- [ ] 멀티 클러스터 테스트

#### D. 크로스 클러스터 알림 및 정책 동기화 (선택)

> **업계 표준**: 설정 자동 전파는 Blast Radius(폭발 반경) 위험으로 **권장하지 않음**.
> 대신 알림 + 수동 승인 방식 사용.

**자동화 가능한 것:**

| 기능 | 설명 | 자동화 |
|------|------|:------:|
| **알림 전파** | "Cluster A에서 설정 변경됨, 검토 필요" | ✅ 자동 |
| **정책 동기화** | 거버넌스 규칙 (상한선/하한선) | ✅ 자동 |
| **설정 전파 요청** | "이 설정을 적용하시겠습니까?" | ❌ 수동 승인 |
| **긴급 대응 전파** | Emergency Mode 등 | ❌ 각 클러스터 개별 판단 |

**권장 구현:**

```python
# services/canary/cross_cluster.py
"""
Cross-Cluster 알림 및 정책 동기화.

업계 표준 방식:
- 설정 자동 전파 ❌ (Blast Radius 위험)
- 알림 + 수동 승인 ✅ (안전)
"""

class CrossClusterNotifier:
    """크로스 클러스터 알림."""
    
    def notify_config_change(
        self,
        source_cluster: str,
        change: "ConfigChange",
    ) -> None:
        """
        다른 클러스터 담당자에게 설정 변경 알림.
        
        자동 적용하지 않고, 정보만 공유.
        """
        for cluster in self.other_clusters:
            self.send_notification(
                channel="slack",
                message=(
                    f"📢 [{source_cluster}] 설정 변경 알림\n"
                    f"• 설정: {change.config_type}\n"
                    f"• 변경: {change.previous_value} → {change.new_value}\n"
                    f"• 결과: 성공 (Canary 통과)\n"
                    f"• 액션: 필요시 {cluster}에서 별도 적용"
                ),
            )


class CrossClusterPropagationRequest:
    """크로스 클러스터 설정 전파 요청 (수동 승인)."""
    
    def request_propagation(
        self,
        source_cluster: str,
        target_clusters: List[str],
        change: "ConfigChange",
    ) -> str:
        """
        다른 클러스터에 설정 전파 요청 생성.
        
        Returns:
            요청 ID (대상 클러스터 담당자가 승인해야 적용됨)
        """
        request_id = str(uuid.uuid4())[:8]
        
        for cluster in target_clusters:
            self.create_pending_request(
                request_id=request_id,
                target_cluster=cluster,
                change=change,
                status="pending_approval",  # 승인 대기
                source_cluster=source_cluster,
            )
            
            # 승인 요청 알림
            self.send_notification(
                channel="slack",
                message=(
                    f"🔔 [{cluster}] 설정 전파 승인 요청\n"
                    f"• 출처: {source_cluster}\n"
                    f"• 설정: {change.config_type}\n"
                    f"• 요청 ID: {request_id}\n"
                    f"• 액션: `/approve {request_id}` 또는 `/reject {request_id}`"
                ),
            )
        
        return request_id


class GovernancePolicySync:
    """거버넌스 정책 동기화 (자동)."""
    
    def sync_policy(self, policy: "GovernancePolicy") -> None:
        """
        거버넌스 정책만 자동 동기화.
        
        예시:
        - retry_max_attempts <= 5 (상한선)
        - failure_threshold >= 2 (하한선)
        """
        # 정책은 자동 동기화 (실제 설정값 아님)
        for cluster in self.all_clusters:
            self.update_policy(cluster, policy)
```

**체크리스트:**

- [ ] `services/canary/cross_cluster.py` 생성
  - [ ] `CrossClusterNotifier` 클래스
  - [ ] `CrossClusterPropagationRequest` 클래스
  - [ ] `GovernancePolicySync` 클래스
- [ ] Slack 알림 연동
- [ ] 승인/거절 API 엔드포인트
- [ ] 단위 테스트

### Step 6: Request-level Canary (선택)

> 단일 클러스터 내 **일부 요청에만** 설정 적용이 필요한 경우

**주의**: Self-Healing은 **설정 변경**만 다룹니다. 서비스 배포 카나리(Pod v1→v2)는 Kubernetes/서비스 메시 영역이며, Self-Healing과 무관합니다.

- [ ] `services/canary/feature_flag.py` 생성
  - [ ] `CanaryFeatureFlag` 클래스
  - [ ] `should_use_canary_config()` 메서드
  - [ ] `get_effective_config()` 메서드
- [ ] Django 미들웨어 연동 (선택)
- [ ] 단위 테스트

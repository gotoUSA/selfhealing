"""
Multi-Region Active-Active 아키텍처 구현 패키지.

리전 전체가 죽어도 다른 리전에서 즉시 서비스 가능한 Active-Active 아키텍처를 지원합니다.

핵심 컴포넌트:
- RegionConfig: 리전 설정 관리
- RegionHealthMonitor: 리전 건강 상태 모니터링
- RegionReplicator: 리전 간 데이터 복제
- ConflictResolver: 충돌 해결 (CRDT/LWW)
- RegionFailover: 자동 페일오버
- ServiceLocalityRouter: 크로스 리전 라우팅
- QuorumWitness: Split-brain 방지
- RegionHeartbeat: TTL 기반 리전 생존 신호
- MultiRegionShutdownHandler: 정상 종료 시 피어 리전 통보
"""

from __future__ import annotations

from selfhealing.multiregion.config import (
    MultiRegionSettings,
    RegionEndpoint,
    get_multiregion_settings,
    reset_multiregion_settings,
)
from selfhealing.multiregion.conflict import (
    ConflictKey,
    ConflictMetrics,
    ConflictResolver,
    CRDTGCounter,
    CRDTLWWRegister,
    CRDTResolver,
    LastWriteWinsResolver,
    get_conflict_resolver,
    reset_conflict_resolver,
)
from selfhealing.multiregion.failover import (
    FailoverEvent,
    FailoverState,
    RegionFailover,
)
from selfhealing.multiregion.health_monitor import (
    RegionHealth,
    RegionHealthMonitor,
    RegionHealthStatus,
)
from selfhealing.multiregion.heartbeat import (
    MultiRegionShutdownHandler,
    RegionHeartbeat,
)
from selfhealing.multiregion.quorum import QuorumLease, QuorumWitness
from selfhealing.multiregion.replicator import (
    RedisReplicationTarget,
    RegionReplicator,
    ReplicationEvent,
    ReplicationEventType,
    ReplicationFilter,
)
from selfhealing.multiregion.router import (
    LocalityRule,
    ServiceLocalityRouter,
    get_locality_router,
    reset_locality_router,
)
from selfhealing.multiregion.secure_client import SecureRedisClient
from selfhealing.multiregion.time_sync import (
    TimeSyncChecker,
    TimeSyncStatus,
    get_time_sync_checker,
)

__all__ = [
    # config
    "MultiRegionSettings",
    "RegionEndpoint",
    "get_multiregion_settings",
    "reset_multiregion_settings",
    # health_monitor
    "RegionHealthStatus",
    "RegionHealth",
    "RegionHealthMonitor",
    # replicator
    "ReplicationEventType",
    "ReplicationEvent",
    "ReplicationFilter",
    "RedisReplicationTarget",
    "RegionReplicator",
    # conflict
    "ConflictKey",
    "ConflictMetrics",
    "ConflictResolver",
    "LastWriteWinsResolver",
    "CRDTGCounter",
    "CRDTLWWRegister",
    "CRDTResolver",
    "get_conflict_resolver",
    "reset_conflict_resolver",
    # failover
    "FailoverState",
    "FailoverEvent",
    "RegionFailover",
    # heartbeat
    "RegionHeartbeat",
    "MultiRegionShutdownHandler",
    # quorum
    "QuorumLease",
    "QuorumWitness",
    # router
    "LocalityRule",
    "ServiceLocalityRouter",
    "get_locality_router",
    "reset_locality_router",
    # secure_client
    "SecureRedisClient",
    # time_sync
    "TimeSyncStatus",
    "TimeSyncChecker",
    "get_time_sync_checker",
]

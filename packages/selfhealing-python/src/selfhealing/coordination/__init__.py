"""
Coordination 패키지 - Global Leader Election.

분산 환경에서 단일 리더를 선출하기 위한 모듈.

주요 컴포넌트:
- LeaderElector: 리더 선출 인터페이스
- RedisLeaderElector: Redis 기반 구현
- EtcdLeaderElector: etcd 기반 구현
- LeaderElectionSettings: 설정
- DLQConsumerCoordinator: DLQ 처리 조율
- LeaderScheduler: 분산 스케줄러

Usage:
    from selfhealing.coordination import get_leader_elector

    elector = get_leader_elector("dlq-consumer")

    @elector.on_become_leader
    def start_processing():
        print("리더가 되었습니다!")

    @elector.on_lose_leader
    def stop_processing():
        print("리더십을 잃었습니다")

    elector.start()
    # ...
    elector.stop()
"""

from selfhealing.coordination.base import (
    LeaderCallback,
    LeaderElector,
    LeaderInfo,
    LeadershipState,
)
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
    reset_leader_election_settings,
)
from selfhealing.coordination.dlq_consumer import DLQConsumerCoordinator
from selfhealing.coordination.etcd_elector import (
    EtcdLeaderElector,
    is_etcd_available,
)
from selfhealing.coordination.factory import (
    get_leader_elector,
    reset_leader_electors,
)
from selfhealing.coordination.redis_elector import RedisLeaderElector
from selfhealing.coordination.scheduler import LeaderScheduler, ScheduledJob
from selfhealing.coordination.shutdown_integration import (
    integrate_with_shutdown_coordinator,
    register_for_graceful_shutdown,
    unregister_from_graceful_shutdown,
)

__all__ = [
    # 기본 인터페이스
    "LeaderElector",
    "LeaderCallback",
    "LeaderInfo",
    "LeadershipState",
    # 설정
    "LeaderElectionSettings",
    "get_leader_election_settings",
    "reset_leader_election_settings",
    # 팩토리
    "get_leader_elector",
    "reset_leader_electors",
    # Redis 구현
    "RedisLeaderElector",
    # etcd 구현
    "EtcdLeaderElector",
    "is_etcd_available",
    # DLQ Consumer
    "DLQConsumerCoordinator",
    # Scheduler
    "LeaderScheduler",
    "ScheduledJob",
    # Graceful Shutdown
    "register_for_graceful_shutdown",
    "unregister_from_graceful_shutdown",
    "integrate_with_shutdown_coordinator",
]

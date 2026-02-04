"""
Leader Elector 팩토리.

리소스별 LeaderElector 싱글톤 인스턴스 관리.
"""

from __future__ import annotations

import threading

from selfhealing.coordination.base import LeaderElector
from selfhealing.coordination.config import (
    LeaderElectionSettings,
    get_leader_election_settings,
)

_electors: dict[str, LeaderElector] = {}
_lock = threading.Lock()


def get_leader_elector(
    resource_name: str,
    settings: LeaderElectionSettings | None = None,
) -> LeaderElector:
    """
    Leader Elector 싱글톤 반환.

    동일한 resource_name에 대해 항상 같은 인스턴스 반환.

    Args:
        resource_name: 리소스 이름 (예: "dlq-consumer", "scheduler")
        settings: 설정 (None이면 기본 설정 사용)

    Returns:
        LeaderElector 인스턴스

    Raises:
        NotImplementedError: 지원하지 않는 백엔드 지정 시
        ValueError: 알 수 없는 백엔드 지정 시

    Usage:
        elector = get_leader_elector("dlq-consumer")
        elector.start()
    """
    global _electors

    if resource_name in _electors:
        return _electors[resource_name]

    with _lock:
        # Double-check locking
        if resource_name in _electors:
            return _electors[resource_name]

        settings = settings or get_leader_election_settings()

        if settings.backend == "redis":
            from selfhealing.coordination.redis_elector import RedisLeaderElector

            elector = RedisLeaderElector(resource_name, settings)
        elif settings.backend == "etcd":
            raise NotImplementedError("etcd 백엔드는 아직 구현되지 않았습니다")
        else:
            raise ValueError(f"알 수 없는 백엔드: {settings.backend}")

        _electors[resource_name] = elector
        return elector


def reset_leader_electors() -> None:
    """
    모든 Elector 리셋 (테스트용).

    모든 활성 Elector를 중지하고 캐시를 비웁니다.
    """
    global _electors

    with _lock:
        for elector in _electors.values():
            try:
                elector.stop()
            except Exception:
                pass
        _electors.clear()

"""
Leader Election 인터페이스.

분산 환경에서 단일 리더를 선출하기 위한 추상 인터페이스.
리더 상태 관리, 콜백 등록, 리더 정보 조회 기능 제공.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

import structlog

logger = structlog.get_logger()


class LeadershipState(str, Enum):
    """리더십 상태."""

    NOT_STARTED = "not_started"
    """선출 프로세스 시작 전."""

    FOLLOWER = "follower"
    """팔로워 상태 (리더 아님)."""

    LEADER = "leader"
    """리더 상태."""

    STOPPING = "stopping"
    """종료 진행 중."""

    STOPPED = "stopped"
    """종료 완료."""


@dataclass
class LeaderInfo:
    """현재 리더 정보."""

    node_id: str
    """리더 노드 ID."""

    elected_at: datetime
    """선출 시각."""

    lease_expires_at: datetime
    """Lease 만료 시각."""

    fencing_token: int = 0
    """Fencing Token (단조 증가, Split-brain 방지)."""

    region_priority: int = 100
    """리전 우선순위."""

    is_self: bool = False
    """자신이 리더인지 여부."""


class LeaderCallback(Protocol):
    """리더십 변경 콜백 프로토콜."""

    def on_become_leader(self) -> None:
        """리더가 되었을 때 호출."""
        ...

    def on_lose_leader(self) -> None:
        """리더십을 잃었을 때 호출."""
        ...


class LeaderElector(ABC):
    """
    Leader Elector 추상 인터페이스.

    리더 선출 및 리더십 유지를 위한 기본 인터페이스.

    Usage:
        elector = RedisLeaderElector("dlq-consumer")

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

    @property
    @abstractmethod
    def resource_name(self) -> str:
        """리소스 이름 (리더십 대상 식별자)."""
        pass

    @property
    @abstractmethod
    def state(self) -> LeadershipState:
        """현재 리더십 상태."""
        pass

    @abstractmethod
    def is_leader(self) -> bool:
        """현재 리더인지 확인."""
        pass

    @abstractmethod
    def get_leader(self) -> LeaderInfo | None:
        """현재 리더 정보 조회."""
        pass

    @abstractmethod
    def get_fencing_token(self) -> int:
        """
        현재 Fencing Token 반환.

        외부 시스템에 쓰기 시 이 토큰을 함께 전달하여
        stale leader의 쓰기를 방지합니다.
        """
        pass

    @abstractmethod
    def is_lease_valid(self) -> bool:
        """
        현재 Lease가 유효한지 확인 (Self-Fencing).

        장기 실행 작업 중간에 호출하여 stale leader 감지.
        """
        pass

    @abstractmethod
    def start(self) -> None:
        """리더 선출 프로세스 시작."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """리더 선출 프로세스 중지 (리더십 반납)."""
        pass

    @abstractmethod
    def on_become_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """
        리더가 되었을 때 콜백 등록.

        데코레이터로 사용 가능:
            @elector.on_become_leader
            def handle_become_leader():
                pass
        """
        pass

    @abstractmethod
    def on_lose_leader(self, callback: Callable[[], None]) -> Callable[[], None]:
        """
        리더십을 잃었을 때 콜백 등록.

        데코레이터로 사용 가능:
            @elector.on_lose_leader
            def handle_lose_leader():
                pass
        """
        pass

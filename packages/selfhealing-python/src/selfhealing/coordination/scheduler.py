"""
Leader Election 기반 스케줄러.

분산 환경에서 단일 노드만 스케줄된 작업을 실행하도록 보장.
Leader Election을 통해 여러 Pod 중 하나만 스케줄러로 동작합니다.

Usage:
    from selfhealing.coordination.scheduler import LeaderScheduler, ScheduledJob

    scheduler = LeaderScheduler("my-scheduler")

    @scheduler.job(interval_seconds=60)
    def cleanup_job():
        print("Cleanup running...")

    scheduler.start()
    # ...
    scheduler.stop()
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from selfhealing.coordination.factory import get_leader_elector
from selfhealing.coordination.shutdown_integration import (
    register_for_graceful_shutdown,
)

logger = structlog.get_logger()

# 기본 스케줄러 리소스 이름
DEFAULT_SCHEDULER_RESOURCE = "scheduler"


@dataclass
class ScheduledJob:
    """
    스케줄된 작업 정의.

    Attributes:
        name: 작업 이름
        func: 실행할 함수
        interval_seconds: 실행 주기 (초)
        enabled: 활성화 여부
        last_run: 마지막 실행 시각
        run_count: 실행 횟수
        error_count: 오류 횟수
    """

    name: str
    func: Callable[[], None]
    interval_seconds: float
    enabled: bool = True
    last_run: datetime | None = field(default=None)
    run_count: int = field(default=0)
    error_count: int = field(default=0)

    def should_run(self) -> bool:
        """실행해야 하는지 확인."""
        if not self.enabled:
            return False
        if self.last_run is None:
            return True

        elapsed = (datetime.now(timezone.utc) - self.last_run).total_seconds()
        return elapsed >= self.interval_seconds

    def mark_run(self, success: bool = True) -> None:
        """실행 완료 표시."""
        self.last_run = datetime.now(timezone.utc)
        self.run_count += 1
        if not success:
            self.error_count += 1


class LeaderScheduler:
    """
    Leader Election 기반 스케줄러.

    분산 환경에서 리더 노드만 스케줄된 작업을 실행합니다.
    리더가 아닌 노드는 대기 상태로 유지됩니다.

    Features:
    - 단일 리더만 작업 실행
    - 자동 Failover (리더 장애 시 다른 노드가 인수)
    - 작업별 개별 주기 설정
    - Graceful Shutdown 지원

    Attributes:
        resource_name: 리소스 이름 (Leader Election 키)
        jobs: 등록된 작업 목록
    """

    def __init__(
        self,
        resource_name: str = DEFAULT_SCHEDULER_RESOURCE,
        tick_interval_seconds: float = 1.0,
    ):
        """
        초기화.

        Args:
            resource_name: 리소스 이름 (리더 선출 키)
            tick_interval_seconds: 스케줄러 틱 주기 (초)
        """
        self._resource_name = resource_name
        self._tick_interval = tick_interval_seconds

        self._elector = get_leader_elector(resource_name)
        self._jobs: dict[str, ScheduledJob] = {}

        self._running = False
        self._scheduler_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 콜백 등록
        self._elector.on_become_leader(self._on_become_leader)
        self._elector.on_lose_leader(self._on_lose_leader)

        # Graceful Shutdown 등록
        register_for_graceful_shutdown(self._elector)

    @property
    def is_leader(self) -> bool:
        """현재 리더인지 여부."""
        return self._elector.is_leader()

    @property
    def jobs(self) -> dict[str, ScheduledJob]:
        """등록된 작업 목록."""
        return self._jobs.copy()

    def register_leader_callbacks(
        self,
        on_become: Callable[[], None] | None = None,
        on_lose: Callable[[], None] | None = None,
    ) -> None:
        """
        리더 전환 이벤트에 외부 콜백 등록.

        LeaderElector의 on_become_leader/on_lose_leader에 콜백을 추가합니다.
        스케줄러 내부 콜백과 별도로 실행됩니다.

        Args:
            on_become: 리더 획득 시 호출할 콜백
            on_lose: 리더십 상실 시 호출할 콜백
        """
        if on_become is not None:
            self._elector.on_become_leader(on_become)
        if on_lose is not None:
            self._elector.on_lose_leader(on_lose)

    def job(
        self,
        interval_seconds: float,
        name: str | None = None,
        enabled: bool = True,
    ) -> Callable[[Callable[[], None]], Callable[[], None]]:
        """
        스케줄된 작업 등록 데코레이터.

        Args:
            interval_seconds: 실행 주기 (초)
            name: 작업 이름 (None이면 함수 이름 사용)
            enabled: 활성화 여부

        Returns:
            데코레이터 함수

        Usage:
            @scheduler.job(interval_seconds=60)
            def my_job():
                print("Running...")
        """

        def decorator(func: Callable[[], None]) -> Callable[[], None]:
            job_name = name or func.__name__
            self.add_job(
                name=job_name,
                func=func,
                interval_seconds=interval_seconds,
                enabled=enabled,
            )
            return func

        return decorator

    def add_job(
        self,
        name: str,
        func: Callable[[], None],
        interval_seconds: float,
        enabled: bool = True,
    ) -> ScheduledJob:
        """
        스케줄된 작업 추가.

        Args:
            name: 작업 이름
            func: 실행할 함수
            interval_seconds: 실행 주기 (초)
            enabled: 활성화 여부

        Returns:
            ScheduledJob 인스턴스
        """
        job = ScheduledJob(
            name=name,
            func=func,
            interval_seconds=interval_seconds,
            enabled=enabled,
        )
        self._jobs[name] = job
        logger.info(
            "scheduler.작업_등록",
            name=name,
            interval_seconds=interval_seconds,
        )
        return job

    def remove_job(self, name: str) -> bool:
        """
        스케줄된 작업 제거.

        Args:
            name: 작업 이름

        Returns:
            제거 성공 여부
        """
        if name in self._jobs:
            del self._jobs[name]
            logger.info(
                "scheduler.작업_제거",
                name=name,
            )
            return True
        return False

    def enable_job(self, name: str) -> bool:
        """작업 활성화."""
        if name in self._jobs:
            self._jobs[name].enabled = True
            return True
        return False

    def disable_job(self, name: str) -> bool:
        """작업 비활성화."""
        if name in self._jobs:
            self._jobs[name].enabled = False
            return True
        return False

    def start(self) -> None:
        """스케줄러 시작."""
        if self._running:
            return

        logger.info(
            "scheduler.시작",
            self=self._resource_name,
        )
        self._stop_event.clear()
        self._running = True

        # Leader Election 시작
        self._elector.start()

    def stop(self) -> None:
        """스케줄러 중지."""
        logger.info(
            "scheduler.중지",
            self=self._resource_name,
        )
        self._running = False
        self._stop_event.set()

        # 스케줄러 스레드 종료 대기
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=5.0)

        # Leader Election 중지
        self._elector.stop()
        logger.info(
            "scheduler.중지됨",
            self=self._resource_name,
        )

    def _on_become_leader(self) -> None:
        """리더가 되었을 때 스케줄러 루프 시작."""
        logger.info("scheduler")
        self._start_scheduler_loop()

    def _on_lose_leader(self) -> None:
        """리더십을 잃었을 때 스케줄러 루프 중단."""
        logger.info("scheduler")

    def _start_scheduler_loop(self) -> None:
        """스케줄러 루프 시작 (별도 스레드)."""
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            return

        self._scheduler_thread = threading.Thread(
            target=self._scheduler_loop,
            name=f"Scheduler-{self._resource_name}",
            daemon=True,
        )
        self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        """스케줄러 메인 루프."""
        logger.info("scheduler")

        while self._running and not self._stop_event.is_set():
            try:
                # 리더십 확인
                if not self._elector.is_leader():
                    logger.debug("scheduler")
                    self._stop_event.wait(timeout=self._tick_interval)
                    continue

                # 실행 대상 작업 확인
                for job in self._jobs.values():
                    if not self._running or not self._elector.is_leader():
                        break

                    if job.should_run():
                        self._execute_job(job)

                # 다음 틱까지 대기
                self._stop_event.wait(timeout=self._tick_interval)

            except Exception as e:
                logger.exception(
                    "scheduler.루프_오류",
                    error=e,
                )
                self._stop_event.wait(timeout=self._tick_interval)

        logger.info("scheduler")

    def _execute_job(self, job: ScheduledJob) -> None:
        """
        작업 실행.

        Args:
            job: 실행할 작업
        """
        try:
            logger.debug(
                "scheduler.작업_실행",
                job=job.name,
            )
            job.func()
            job.mark_run(success=True)
            logger.info(
                "scheduler.작업_완료",
                job=job.name,
                job_1=job.run_count,
            )

        except Exception as e:
            job.mark_run(success=False)
            logger.exception(
                "scheduler.작업_실패",
                job=job.name,
                error=e,
            )

    def get_job_stats(self) -> dict[str, dict]:
        """
        모든 작업 통계 반환.

        Returns:
            작업별 통계 딕셔너리
        """
        return {
            name: {
                "enabled": job.enabled,
                "interval_seconds": job.interval_seconds,
                "last_run": job.last_run.isoformat() if job.last_run else None,
                "run_count": job.run_count,
                "error_count": job.error_count,
            }
            for name, job in self._jobs.items()
        }


# 싱글톤 인스턴스 캐시
_scheduler_cache: dict[str, LeaderScheduler] = {}
_scheduler_lock = threading.Lock()


def get_leader_scheduler(
    resource_name: str = DEFAULT_SCHEDULER_RESOURCE,
) -> LeaderScheduler:
    """
    LeaderScheduler 싱글톤 반환.

    Args:
        resource_name: 리소스 이름

    Returns:
        LeaderScheduler 인스턴스
    """
    global _scheduler_cache

    if resource_name in _scheduler_cache:
        return _scheduler_cache[resource_name]

    with _scheduler_lock:
        if resource_name in _scheduler_cache:
            return _scheduler_cache[resource_name]

        scheduler = LeaderScheduler(resource_name=resource_name)
        _scheduler_cache[resource_name] = scheduler
        return scheduler


def reset_schedulers() -> None:
    """모든 스케줄러 리셋 (테스트용)."""
    global _scheduler_cache

    with _scheduler_lock:
        for scheduler in _scheduler_cache.values():
            try:
                scheduler.stop()
            except Exception:
                pass
        _scheduler_cache.clear()

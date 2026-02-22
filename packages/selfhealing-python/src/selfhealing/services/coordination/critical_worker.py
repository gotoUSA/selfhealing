"""
Critical Path Dedicated Worker Configuration.

P0 우선순위 태스크를 전용 Worker에서 처리하기 위한 설정입니다.

Features:
- CriticalTaskPriority: 태스크 우선순위 정의
- CriticalPathDedicatedWorkerConfig: 전용 Worker 설정
- 태스크 라우팅 설정 생성
- Worker 실행 명령어 생성

일반 Worker 그룹 외에, 오직 P0 전용 태스크만 처리하는
Small-size 전용 Worker를 별도로 운영합니다.

Code reference:
    celery_adapter.py (Priority 매핑 패턴)
    critical_path_fallback.py (CriticalPathFallback 패턴)
    shutdown_coordinator.py (Shutdown 조율 패턴)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

import structlog

from selfhealing.settings import get_critical_worker_settings

logger = structlog.get_logger()


# =============================================================================
# Enums
# =============================================================================


class CriticalTaskPriority(IntEnum):
    """
    Critical Path 태스크 우선순위.

    P0: 즉시 실행 (전용 Worker)
    P1-P2: 고우선순위 Worker
    P3+: 일반 Worker

    Code reference:
        TaskPriority (celery_adapter.py)
    """

    ABORT = 0
    """복구 중단, Kill Switch - 즉시 실행."""

    ESCALATION = 1
    """긴급 레벨 상승 - 최우선."""

    RECOVERY = 2
    """복구 단계 실행 - 고우선순위."""

    ALERT = 3
    """알림 발송 - 중간 우선순위."""

    BUDGET_RESET = 4
    """버짓 리셋 - 중간 우선순위."""

    AUDIT = 5
    """감사 기록 - 낮은 우선순위."""

    ARCHIVE = 6
    """로그 아카이빙 - 최저 우선순위."""


# =============================================================================
# Data Models
# =============================================================================


@dataclass
class WorkerQueueConfig:
    """
    Worker 큐 설정.
    """

    queue_name: str = ""
    """큐 이름."""

    worker_count: int = 1
    """Worker 수."""

    concurrency: int = 2
    """동시성 (Worker당 처리량)."""

    prefetch_multiplier: int = 1
    """프리페치 배수."""

    priority_range: tuple | None = None
    """우선순위 범위 (min, max)."""

    description: str = ""
    """설명."""


# =============================================================================
# Critical Path Dedicated Worker Config
# =============================================================================


@dataclass
class CriticalPathDedicatedWorkerConfig:
    """
    Critical Path 전용 Worker 설정.

    일반 Worker 그룹 외에, 오직 P0 전용 태스크만 처리하는
    Small-size 전용 Worker를 별도로 운영합니다.

    Code reference:
        CriticalPathFallback (critical_path_fallback.py)
        GracefulShutdownCoordinator (shutdown_coordinator.py)
    """

    # ==========================================================================
    # Queue Names
    # ==========================================================================

    critical_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().critical_queue_name
    )
    """P0 전용 큐 (Abort, Kill Switch 전용)."""

    high_priority_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().high_priority_queue_name
    )
    """P1-P2 고우선순위 큐 (Escalation, Recovery)."""

    default_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().default_queue_name
    )
    """P3+ 일반 큐 (Alert, Audit, Archive)."""

    recovery_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().recovery_queue_name
    )
    """복구 전용 큐."""

    notification_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().notification_queue_name
    )
    """알림 전용 큐."""

    maintenance_queue_name: str = field(
        default_factory=lambda: get_critical_worker_settings().maintenance_queue_name
    )
    """유지보수 태스크 큐."""

    # ==========================================================================
    # Worker Counts
    # ==========================================================================

    critical_worker_count: int = field(
        default_factory=lambda: get_critical_worker_settings().critical_worker_count
    )
    """전용 Worker 수 (최소 1개 보장)."""

    high_priority_worker_count: int = field(
        default_factory=lambda: get_critical_worker_settings().high_priority_worker_count
    )
    """고우선순위 Worker 수."""

    default_worker_count: int = field(
        default_factory=lambda: get_critical_worker_settings().default_worker_count
    )
    """일반 Worker 수."""

    # ==========================================================================
    # Task Definitions
    # ==========================================================================

    # P0 태스크 목록 (전용 큐)
    critical_tasks: set[str] = field(
        default_factory=lambda: {
            "selfhealing.abort_recovery",
            "selfhealing.tasks.abort_recovery",
            "selfhealing.kill_switch",
            "selfhealing.tasks.kill_switch",
            "selfhealing.force_escalation",
            "selfhealing.tasks.force_escalation",
        }
    )
    """P0 태스크: 전용 Worker에서 즉시 처리."""

    # P1-P2 태스크 목록 (고우선순위 큐)
    high_priority_tasks: set[str] = field(
        default_factory=lambda: {
            "selfhealing.execute_recovery_step",
            "selfhealing.tasks.execute_recovery_step",
            "selfhealing.escalate_emergency",
            "selfhealing.tasks.escalate_emergency",
            "selfhealing.reset_budget",
            "selfhealing.tasks.reset_budget",
            "selfhealing.check_recovery_trigger",
            "selfhealing.monitor_recovery_health",
        }
    )
    """P1-P2 태스크: 고우선순위 Worker에서 처리."""

    # P3 알림 태스크
    notification_tasks: set[str] = field(
        default_factory=lambda: {
            "selfhealing.check_stale_pending_recoveries",
            "selfhealing.send_alert",
            "selfhealing.tasks.send_alert",
            "selfhealing.send_notification",
        }
    )
    """알림 태스크: 알림 큐에서 처리."""

    # P4+ 유지보수 태스크
    maintenance_tasks: set[str] = field(
        default_factory=lambda: {
            "selfhealing.cleanup_old_recovery_sessions",
            "selfhealing.archive_logs",
            "selfhealing.tasks.archive_logs",
            "selfhealing.cleanup_expired_data",
        }
    )
    """유지보수 태스크: 일반 큐에서 처리."""

    # ==========================================================================
    # Queue Configurations (settings로부터 동적 로드)
    # ==========================================================================

    queue_configs: dict[str, WorkerQueueConfig] = field(
        default_factory=lambda: {
            "critical": WorkerQueueConfig(
                queue_name=get_critical_worker_settings().critical_queue_name,
                worker_count=get_critical_worker_settings().critical_worker_count,
                concurrency=get_critical_worker_settings().critical_concurrency,
                prefetch_multiplier=get_critical_worker_settings().critical_prefetch_multiplier,
                priority_range=(0, 0),
                description="P0 전용 - Abort, Kill Switch",
            ),
            "high": WorkerQueueConfig(
                queue_name=get_critical_worker_settings().high_priority_queue_name,
                worker_count=get_critical_worker_settings().high_priority_worker_count,
                concurrency=get_critical_worker_settings().high_priority_concurrency,
                prefetch_multiplier=get_critical_worker_settings().high_priority_prefetch_multiplier,
                priority_range=(1, 2),
                description="P1-P2 고우선순위 - Escalation, Recovery",
            ),
            "recovery": WorkerQueueConfig(
                queue_name=get_critical_worker_settings().recovery_queue_name,
                worker_count=get_critical_worker_settings().high_priority_worker_count,
                concurrency=get_critical_worker_settings().high_priority_concurrency,
                prefetch_multiplier=get_critical_worker_settings().high_priority_prefetch_multiplier,
                priority_range=(2, 3),
                description="복구 전용",
            ),
            "notifications": WorkerQueueConfig(
                queue_name=get_critical_worker_settings().notification_queue_name,
                worker_count=2,  # 알림 전용은 2개로 유지
                concurrency=get_critical_worker_settings().default_concurrency // 2,
                prefetch_multiplier=get_critical_worker_settings().default_prefetch_multiplier,
                priority_range=(3, 4),
                description="알림 전용",
            ),
            "default": WorkerQueueConfig(
                queue_name=get_critical_worker_settings().default_queue_name,
                worker_count=get_critical_worker_settings().default_worker_count,
                concurrency=get_critical_worker_settings().default_concurrency,
                prefetch_multiplier=get_critical_worker_settings().default_prefetch_multiplier,
                priority_range=(5, 10),
                description="일반 + 유지보수",
            ),
        }
    )

    def get_task_queue(self, task_name: str) -> str:
        """
        태스크에 맞는 큐 반환.

        Args:
            task_name: 태스크 이름

        Returns:
            큐 이름
        """
        if task_name in self.critical_tasks:
            return self.critical_queue_name
        elif task_name in self.high_priority_tasks:
            return self.high_priority_queue_name
        elif task_name in self.notification_tasks:
            return self.notification_queue_name
        elif task_name in self.maintenance_tasks:
            return self.maintenance_queue_name
        return self.default_queue_name

    def get_task_priority(self, task_name: str) -> CriticalTaskPriority:
        """
        태스크의 우선순위 반환.

        Args:
            task_name: 태스크 이름

        Returns:
            우선순위
        """
        if task_name in self.critical_tasks:
            return CriticalTaskPriority.ABORT
        elif task_name in self.high_priority_tasks:
            if "escalat" in task_name.lower():
                return CriticalTaskPriority.ESCALATION
            return CriticalTaskPriority.RECOVERY
        elif task_name in self.notification_tasks:
            return CriticalTaskPriority.ALERT
        elif task_name in self.maintenance_tasks:
            return CriticalTaskPriority.ARCHIVE
        return CriticalTaskPriority.AUDIT

    def get_celery_task_routes(self) -> dict[str, dict[str, str]]:
        """
        Celery CELERY_TASK_ROUTES 설정 생성.

        Returns:
            Celery task routing 설정

        Example:
            >>> config = CriticalPathDedicatedWorkerConfig()
            >>> routes = config.get_celery_task_routes()
            >>> # settings.py에 적용:
            >>> # CELERY_TASK_ROUTES = routes
        """
        routes: dict[str, dict[str, str]] = {}

        for task in self.critical_tasks:
            routes[task] = {"queue": self.critical_queue_name}

        for task in self.high_priority_tasks:
            routes[task] = {"queue": self.high_priority_queue_name}

        for task in self.notification_tasks:
            routes[task] = {"queue": self.notification_queue_name}

        for task in self.maintenance_tasks:
            routes[task] = {"queue": self.maintenance_queue_name}

        return routes

    def get_celery_task_queues(self) -> list[dict[str, Any]]:
        """
        Celery CELERY_TASK_QUEUES 설정 생성.

        Returns:
            큐 정의 목록
        """
        return [
            {"name": self.critical_queue_name, "priority": 0},
            {"name": self.high_priority_queue_name, "priority": 1},
            {"name": self.recovery_queue_name, "priority": 2},
            {"name": self.notification_queue_name, "priority": 3},
            {"name": self.default_queue_name, "priority": 4},
            {"name": self.maintenance_queue_name, "priority": 5},
        ]

    def get_worker_commands(self) -> dict[str, str]:
        """
        Celery Worker 실행 명령어 생성.

        Returns:
            {"critical": "...", "high": "...", "default": "..."}
        """
        return {
            "critical": (
                f"celery -A myproject worker "
                f"-Q {self.critical_queue_name} "
                f"-c {self.critical_worker_count} "
                f"--prefetch-multiplier=1 "
                f"--hostname=critical@%h "
                f"-l INFO"
            ),
            "high": (
                f"celery -A myproject worker "
                f"-Q {self.high_priority_queue_name},{self.recovery_queue_name} "
                f"-c {self.high_priority_worker_count} "
                f"--prefetch-multiplier=2 "
                f"--hostname=high@%h "
                f"-l INFO"
            ),
            "notifications": (
                f"celery -A myproject worker "
                f"-Q {self.notification_queue_name} "
                f"-c 4 "
                f"--prefetch-multiplier=4 "
                f"--hostname=notifications@%h "
                f"-l INFO"
            ),
            "default": (
                f"celery -A myproject worker "
                f"-Q {self.default_queue_name},{self.maintenance_queue_name} "
                f"-c {self.default_worker_count} "
                f"--prefetch-multiplier=4 "
                f"--hostname=default@%h "
                f"-l INFO"
            ),
        }

    def get_docker_compose_services(self) -> dict[str, dict[str, Any]]:
        """
        Docker Compose 서비스 정의 생성.

        Returns:
            Docker Compose services 정의
        """
        base_env = [
            "CELERY_BROKER_URL=${CELERY_BROKER_URL}",
            "CELERY_RESULT_BACKEND=${CELERY_RESULT_BACKEND}",
        ]

        return {
            "celery-critical": {
                "build": ".",
                "command": self.get_worker_commands()["critical"],
                "environment": base_env,
                "deploy": {
                    "replicas": 2,
                    "resources": {
                        "limits": {"cpus": "0.5", "memory": "512M"},
                        "reservations": {"cpus": "0.1", "memory": "256M"},
                    },
                },
            },
            "celery-high": {
                "build": ".",
                "command": self.get_worker_commands()["high"],
                "environment": base_env,
                "deploy": {
                    "replicas": 2,
                    "resources": {
                        "limits": {"cpus": "1", "memory": "1G"},
                        "reservations": {"cpus": "0.25", "memory": "512M"},
                    },
                },
            },
            "celery-default": {
                "build": ".",
                "command": self.get_worker_commands()["default"],
                "environment": base_env,
                "deploy": {
                    "replicas": 4,
                    "resources": {
                        "limits": {"cpus": "2", "memory": "2G"},
                        "reservations": {"cpus": "0.5", "memory": "1G"},
                    },
                },
            },
        }

    def validate(self) -> list[str]:
        """
        설정 유효성 검사.

        Returns:
            오류 메시지 목록
        """
        errors: list[str] = []

        if self.critical_worker_count < 1:
            errors.append("critical_worker_count must be at least 1")

        if self.high_priority_worker_count < 1:
            errors.append("high_priority_worker_count must be at least 1")

        if self.default_worker_count < 1:
            errors.append("default_worker_count must be at least 1")

        # 큐 이름 중복 확인
        queues = [
            self.critical_queue_name,
            self.high_priority_queue_name,
            self.default_queue_name,
            self.notification_queue_name,
            self.maintenance_queue_name,
        ]
        if len(queues) != len(set(queues)):
            errors.append("Queue names must be unique")

        return errors


# =============================================================================
# Singleton Access
# =============================================================================

_worker_config: CriticalPathDedicatedWorkerConfig | None = None


def get_critical_worker_config() -> CriticalPathDedicatedWorkerConfig:
    """
    CriticalPathDedicatedWorkerConfig 싱글톤 반환.

    Returns:
        CriticalPathDedicatedWorkerConfig 인스턴스
    """
    global _worker_config

    if _worker_config is None:
        _worker_config = CriticalPathDedicatedWorkerConfig()

    return _worker_config


def get_task_queue(task_name: str) -> str:
    """
    태스크에 맞는 큐 이름 반환 (편의 함수).

    Args:
        task_name: 태스크 이름

    Returns:
        큐 이름
    """
    return get_critical_worker_config().get_task_queue(task_name)

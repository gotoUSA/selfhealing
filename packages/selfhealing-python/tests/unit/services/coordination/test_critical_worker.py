"""
Critical Worker 단위 테스트.

11.4 E.3 구현 검증:
- CriticalTaskPriority enum
- CriticalPathDedicatedWorkerConfig
- 태스크 라우팅 설정

selfhealing 패키지만 import하는 순수 단위 테스트입니다.

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#11.2
"""


import pytest

from selfhealing.services.coordination.critical_worker import (
    CriticalPathDedicatedWorkerConfig,
    CriticalTaskPriority,
    WorkerQueueConfig,
    get_critical_worker_config,
    get_task_queue,
)


class TestCriticalTaskPriority:
    """CriticalTaskPriority enum 테스트."""

    def test_priority_ordering(self):
        """우선순위 순서 확인 (낮은 숫자 = 높은 우선순위)."""
        assert CriticalTaskPriority.ABORT < CriticalTaskPriority.ESCALATION
        assert CriticalTaskPriority.ESCALATION < CriticalTaskPriority.RECOVERY
        assert CriticalTaskPriority.RECOVERY < CriticalTaskPriority.ALERT
        assert CriticalTaskPriority.ALERT < CriticalTaskPriority.BUDGET_RESET
        assert CriticalTaskPriority.BUDGET_RESET < CriticalTaskPriority.AUDIT
        assert CriticalTaskPriority.AUDIT < CriticalTaskPriority.ARCHIVE

    def test_priority_values(self):
        """우선순위 값 확인."""
        assert CriticalTaskPriority.ABORT == 0
        assert CriticalTaskPriority.ESCALATION == 1
        assert CriticalTaskPriority.RECOVERY == 2
        assert CriticalTaskPriority.ALERT == 3
        assert CriticalTaskPriority.BUDGET_RESET == 4
        assert CriticalTaskPriority.AUDIT == 5
        assert CriticalTaskPriority.ARCHIVE == 6


class TestWorkerQueueConfig:
    """WorkerQueueConfig 모델 테스트."""

    def test_create_config(self):
        """설정 생성."""
        config = WorkerQueueConfig(
            queue_name="test.queue",
            worker_count=4,
            concurrency=8,
            prefetch_multiplier=2,
            priority_range=(0, 2),
            description="테스트 큐",
        )

        assert config.queue_name == "test.queue"
        assert config.worker_count == 4
        assert config.concurrency == 8
        assert config.priority_range == (0, 2)


class TestCriticalPathDedicatedWorkerConfig:
    """CriticalPathDedicatedWorkerConfig 테스트."""

    @pytest.fixture
    def config(self):
        """테스트용 설정 인스턴스."""
        return CriticalPathDedicatedWorkerConfig()

    # =========================================================================
    # Queue Names 테스트
    # =========================================================================

    def test_queue_names_defined(self, config):
        """큐 이름이 정의되어 있는지 확인."""
        assert config.critical_queue_name == "selfhealing.critical"
        assert config.high_priority_queue_name == "selfhealing.high"
        assert config.default_queue_name == "selfhealing.default"
        assert config.notification_queue_name == "selfhealing.notifications"
        assert config.maintenance_queue_name == "selfhealing.maintenance"

    # =========================================================================
    # get_task_queue 테스트
    # =========================================================================

    def test_get_task_queue_critical_tasks(self, config):
        """Critical 태스크는 critical 큐로 라우팅."""
        critical_tasks = [
            "selfhealing.abort_recovery",
            "selfhealing.tasks.abort_recovery",
            "selfhealing.kill_switch",
            "selfhealing.force_escalation",
        ]

        for task in critical_tasks:
            queue = config.get_task_queue(task)
            assert queue == config.critical_queue_name, f"Expected critical queue for {task}"

    def test_get_task_queue_high_priority_tasks(self, config):
        """고우선순위 태스크는 high 큐로 라우팅."""
        high_tasks = [
            "selfhealing.execute_recovery_step",
            "selfhealing.escalate_emergency",
            "selfhealing.reset_budget",
            "selfhealing.check_recovery_trigger",
        ]

        for task in high_tasks:
            queue = config.get_task_queue(task)
            assert queue == config.high_priority_queue_name, f"Expected high queue for {task}"

    def test_get_task_queue_notification_tasks(self, config):
        """알림 태스크는 notifications 큐로 라우팅."""
        notification_tasks = [
            "selfhealing.check_stale_pending_recoveries",
            "selfhealing.send_alert",
            "selfhealing.send_notification",
        ]

        for task in notification_tasks:
            queue = config.get_task_queue(task)
            assert queue == config.notification_queue_name, f"Expected notifications queue for {task}"

    def test_get_task_queue_maintenance_tasks(self, config):
        """유지보수 태스크는 maintenance 큐로 라우팅."""
        maintenance_tasks = [
            "selfhealing.cleanup_old_recovery_sessions",
            "selfhealing.archive_logs",
            "selfhealing.cleanup_expired_data",
        ]

        for task in maintenance_tasks:
            queue = config.get_task_queue(task)
            assert queue == config.maintenance_queue_name, f"Expected maintenance queue for {task}"

    def test_get_task_queue_unknown_task(self, config):
        """알 수 없는 태스크는 default 큐로 라우팅."""
        queue = config.get_task_queue("some.unknown.task")
        assert queue == config.default_queue_name

    # =========================================================================
    # get_task_priority 테스트
    # =========================================================================

    def test_get_task_priority_abort(self, config):
        """Abort 태스크는 ABORT 우선순위."""
        priority = config.get_task_priority("selfhealing.abort_recovery")
        assert priority == CriticalTaskPriority.ABORT

    def test_get_task_priority_escalation(self, config):
        """Escalation 태스크는 ESCALATION 우선순위."""
        priority = config.get_task_priority("selfhealing.escalate_emergency")
        assert priority == CriticalTaskPriority.ESCALATION

    def test_get_task_priority_recovery(self, config):
        """Recovery 태스크는 RECOVERY 우선순위."""
        priority = config.get_task_priority("selfhealing.execute_recovery_step")
        assert priority == CriticalTaskPriority.RECOVERY

    def test_get_task_priority_alert(self, config):
        """Alert 태스크는 ALERT 우선순위."""
        priority = config.get_task_priority("selfhealing.send_alert")
        assert priority == CriticalTaskPriority.ALERT

    # =========================================================================
    # get_celery_task_routes 테스트
    # =========================================================================

    def test_get_celery_task_routes(self, config):
        """Celery task routes 생성."""
        routes = config.get_celery_task_routes()

        # Critical 태스크 라우팅 확인
        assert "selfhealing.abort_recovery" in routes
        assert routes["selfhealing.abort_recovery"]["queue"] == "selfhealing.critical"

        # High priority 태스크 라우팅 확인
        assert "selfhealing.execute_recovery_step" in routes
        assert routes["selfhealing.execute_recovery_step"]["queue"] == "selfhealing.high"

        # Notification 태스크 라우팅 확인
        assert "selfhealing.check_stale_pending_recoveries" in routes
        assert routes["selfhealing.check_stale_pending_recoveries"]["queue"] == "selfhealing.notifications"

    # =========================================================================
    # get_celery_task_queues 테스트
    # =========================================================================

    def test_get_celery_task_queues(self, config):
        """Celery task queues 정의."""
        queues = config.get_celery_task_queues()

        assert len(queues) >= 5

        queue_names = [q["name"] for q in queues]
        assert "selfhealing.critical" in queue_names
        assert "selfhealing.high" in queue_names
        assert "selfhealing.default" in queue_names

    # =========================================================================
    # get_worker_commands 테스트
    # =========================================================================

    def test_get_worker_commands(self, config):
        """Worker 실행 명령어 생성."""
        commands = config.get_worker_commands()

        assert "critical" in commands
        assert "high" in commands
        assert "default" in commands

        # Critical worker 명령어에 critical 큐 포함
        assert "selfhealing.critical" in commands["critical"]
        assert "celery" in commands["critical"]

        # High worker 명령어에 high 큐 포함
        assert "selfhealing.high" in commands["high"]

    # =========================================================================
    # get_docker_compose_services 테스트
    # =========================================================================

    def test_get_docker_compose_services(self, config):
        """Docker Compose 서비스 정의."""
        services = config.get_docker_compose_services()

        assert "celery-critical" in services
        assert "celery-high" in services
        assert "celery-default" in services

        # Critical 서비스 설정 확인
        critical_service = services["celery-critical"]
        assert critical_service["deploy"]["replicas"] == 2

    # =========================================================================
    # validate 테스트
    # =========================================================================

    def test_validate_valid_config(self, config):
        """유효한 설정은 에러 없음."""
        errors = config.validate()
        assert len(errors) == 0

    def test_validate_invalid_worker_count(self):
        """잘못된 worker count 검증."""
        config = CriticalPathDedicatedWorkerConfig()
        config.critical_worker_count = 0

        errors = config.validate()
        assert len(errors) > 0
        assert any("critical_worker_count" in e for e in errors)


class TestConvenienceFunctions:
    """편의 함수 테스트."""

    def test_get_task_queue_function(self):
        """get_task_queue 편의 함수."""
        queue = get_task_queue("selfhealing.abort_recovery")
        assert queue == "selfhealing.critical"

        queue = get_task_queue("selfhealing.execute_recovery_step")
        assert queue == "selfhealing.high"

        queue = get_task_queue("unknown.task")
        assert queue == "selfhealing.default"


class TestSingletonAccess:
    """싱글톤 접근 테스트."""

    def test_get_critical_worker_config_returns_same_instance(self):
        """싱글톤이 동일 인스턴스를 반환하는지 확인."""
        config1 = get_critical_worker_config()
        config2 = get_critical_worker_config()

        assert config1 is config2

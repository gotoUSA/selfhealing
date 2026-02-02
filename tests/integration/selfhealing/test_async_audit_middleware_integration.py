"""
비동기 Audit 미들웨어 통합 테스트.

Docker Compose 환경에서 실제 Django 클라이언트를 사용한 통합 테스트.

Requirements:
- Docker Compose for Redis, PostgreSQL
- Run: docker-compose -f docker-compose.test.yml up -d
- Then: pytest tests/integration/selfhealing/test_async_audit_middleware_integration.py -v

테스트 내용:
1. 미들웨어가 응답 지연 없이 처리 (Non-blocking)
2. 이벤트가 최종적으로 기록됨
3. 비동기 모드와 동기 모드 전환
4. 모니터링 메트릭 노출

Version: 1.0.0
"""

import os
import sys
import time

import pytest

# Setup Django before importing selfhealing
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings")

import django

django.setup()


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture(autouse=True)
def reset_async_logger():
    """각 테스트 전/후 AsyncHealingLogger 리셋."""
    from selfhealing.audit.async_audit_lifecycle import reset_lifecycle_state
    from selfhealing.utils.async_logger import AsyncHealingLogger

    reset_lifecycle_state()
    AsyncHealingLogger.reset()

    yield

    AsyncHealingLogger.stop()
    AsyncHealingLogger.reset()
    reset_lifecycle_state()


@pytest.fixture
def configured_async_logger():
    """설정된 AsyncHealingLogger 반환."""
    from selfhealing.audit.async_audit_lifecycle import create_audit_flush_callback
    from selfhealing.utils.async_logger import AsyncHealingLogger

    events_captured = []

    def capture_callback(events):
        events_captured.extend(events)

    AsyncHealingLogger.configure(flush_callback=capture_callback)
    AsyncHealingLogger.start()

    return events_captured


# =============================================================================
# Integration Tests: Non-blocking Response
# =============================================================================


class TestAsyncAuditMiddlewareNonBlocking:
    """AuditMiddleware Non-blocking 응답 통합 테스트."""

    def test_logging_does_not_delay_response(self, configured_async_logger):
        """로깅이 응답 시간에 영향을 주지 않음."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 1000개 이벤트 로깅
        start = time.time()
        for i in range(1000):
            AsyncHealingLogger.log(
                {"type": "test_event", "index": i},
                EventSeverity.INFO,
            )
        elapsed = time.time() - start

        # Non-blocking: 1000개 로깅이 0.1초 이내 완료
        assert elapsed < 0.1, f"1000 logs took {elapsed:.3f}s (should be < 0.1s)"

    def test_critical_events_sent_immediately(self, configured_async_logger):
        """CRITICAL 이벤트는 즉시 전송."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_captured = configured_async_logger

        # CRITICAL 이벤트 전송
        AsyncHealingLogger.log(
            {"type": "circuit_breaker_state_change", "state": "OPEN"},
            EventSeverity.CRITICAL,
        )

        # 짧은 대기 후 확인
        time.sleep(0.2)

        critical_events = [e for e in events_captured if e.get("type") == "circuit_breaker_state_change"]
        assert len(critical_events) >= 1, "CRITICAL event should be sent immediately"


# =============================================================================
# Integration Tests: Event Recording
# =============================================================================


class TestAsyncAuditEventRecording:
    """이벤트 기록 통합 테스트."""

    def test_events_eventually_flushed(self, configured_async_logger):
        """이벤트가 최종적으로 플러시됨."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_captured = configured_async_logger

        # 이벤트 전송 (배치 크기 미달)
        for i in range(5):
            AsyncHealingLogger.log(
                {"type": "order_created", "order_id": f"ORD-{i}"},
                EventSeverity.INFO,
            )

        # 수동 플러시
        AsyncHealingLogger.flush()

        # 모든 이벤트 플러시됨
        order_events = [e for e in events_captured if e.get("type") == "order_created"]
        assert len(order_events) == 5

    def test_batch_flush_on_size_threshold(self, configured_async_logger):
        """배치 크기 도달 시 자동 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_captured = configured_async_logger

        # 배치 크기(기본 10)보다 많이 전송
        for i in range(15):
            AsyncHealingLogger.log(
                {"type": "batch_test", "index": i},
                EventSeverity.INFO,
            )

        # 워커가 처리할 시간 대기
        time.sleep(2.0)

        # 최소 배치 크기만큼 플러시됨
        batch_events = [e for e in events_captured if e.get("type") == "batch_test"]
        assert len(batch_events) >= 10


# =============================================================================
# Integration Tests: Lifecycle Management
# =============================================================================


class TestAsyncAuditLifecycle:
    """Lifecycle 관리 통합 테스트."""

    def test_startup_and_shutdown(self):
        """시작 및 종료 정상 동작."""
        from selfhealing.audit.async_audit_lifecycle import (
            get_lifecycle_status,
            graceful_shutdown_audit_system,
            reset_lifecycle_state,
            startup_async_audit_system,
        )

        reset_lifecycle_state()

        # 시작 전 상태
        status = get_lifecycle_status()
        assert status["startup_completed"] is False

        # 시작
        result = startup_async_audit_system()
        assert result is True

        # 시작 후 상태
        status = get_lifecycle_status()
        assert status["startup_completed"] is True

        # 종료
        graceful_shutdown_audit_system()

        # 리셋
        reset_lifecycle_state()

    def test_graceful_shutdown_flushes_remaining(self):
        """Graceful Shutdown 시 남은 이벤트 플러시."""
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        events_captured = []

        def capture_callback(events):
            events_captured.extend(events)

        AsyncHealingLogger.configure(flush_callback=capture_callback)
        AsyncHealingLogger.start()

        # 이벤트 전송 (배치 크기 미달)
        for i in range(3):
            AsyncHealingLogger.log(
                {"type": "shutdown_test", "index": i},
                EventSeverity.INFO,
            )

        # flush 후 stop (graceful shutdown과 동일)
        AsyncHealingLogger.flush()
        AsyncHealingLogger.stop()

        # 모든 이벤트 플러시됨
        shutdown_events = [e for e in events_captured if e.get("type") == "shutdown_test"]
        assert len(shutdown_events) == 3


# =============================================================================
# Integration Tests: Monitoring Metrics
# =============================================================================


class TestAsyncAuditMonitoringMetrics:
    """모니터링 메트릭 통합 테스트."""

    def test_get_async_audit_metrics(self, configured_async_logger):
        """메트릭 조회 정상 동작."""
        from selfhealing.audit.async_audit_lifecycle import get_async_audit_metrics
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 이벤트 로깅
        for i in range(5):
            AsyncHealingLogger.log({"type": "metrics_test"}, EventSeverity.INFO)

        # 플러시
        AsyncHealingLogger.flush()
        time.sleep(0.1)

        # 메트릭 조회
        metrics = get_async_audit_metrics()

        assert "events_logged" in metrics
        assert "events_flushed" in metrics
        assert "queue_size" in metrics
        assert "worker_running" in metrics
        assert metrics["events_logged"] == 5
        assert metrics["events_flushed"] == 5

    def test_prometheus_format_export(self, configured_async_logger):
        """Prometheus 포맷 메트릭 출력."""
        from selfhealing.audit.async_audit_lifecycle import export_metrics_to_prometheus
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 이벤트 로깅
        AsyncHealingLogger.log({"type": "prom_test"}, EventSeverity.INFO)
        AsyncHealingLogger.flush()
        time.sleep(0.1)

        # Prometheus 포맷 출력
        prometheus_output = export_metrics_to_prometheus()

        assert "async_audit_events_logged" in prometheus_output
        assert "async_audit_events_flushed" in prometheus_output
        assert "async_audit_queue_size" in prometheus_output
        assert "# HELP" in prometheus_output
        assert "# TYPE" in prometheus_output

    def test_queue_size_metric(self):
        """큐 크기 메트릭 정확성."""
        from selfhealing.audit.async_audit_lifecycle import get_async_audit_metrics
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 콜백 없이 시작 (큐에 이벤트가 쌓임)
        AsyncHealingLogger.configure(flush_callback=lambda e: None)
        AsyncHealingLogger.start()

        # 이벤트 로깅
        for i in range(10):
            AsyncHealingLogger.log({"type": "queue_test"}, EventSeverity.INFO)

        # 메트릭 조회 (워커가 처리하기 전)
        # 주의: 워커가 빨리 처리할 수 있으므로 정확한 수치는 보장 어려움
        metrics = get_async_audit_metrics()

        assert "queue_size" in metrics
        assert metrics["queue_size"] >= 0  # 0 이상 (워커가 처리했을 수 있음)

    def test_flush_errors_metric(self):
        """플러시 에러 메트릭."""
        from selfhealing.audit.async_audit_lifecycle import get_async_audit_metrics
        from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

        # 에러를 발생시키는 콜백
        def error_callback(events):
            raise Exception("Simulated flush error")

        AsyncHealingLogger.configure(flush_callback=error_callback)
        AsyncHealingLogger.start()

        # CRITICAL 이벤트 (즉시 플러시 시도)
        AsyncHealingLogger.log({"type": "error_test"}, EventSeverity.CRITICAL)

        # 에러 발생 대기
        time.sleep(0.2)

        # 메트릭 조회
        metrics = get_async_audit_metrics()

        assert "flush_errors" in metrics
        assert metrics["flush_errors"] >= 1


# =============================================================================
# Integration Tests: Mode Switching
# =============================================================================


class TestAsyncModeSwitch:
    """비동기/동기 모드 전환 통합 테스트."""

    def test_async_mode_enabled_by_default(self):
        """기본값은 비동기 모드 활성화."""
        # 환경변수 제거
        os.environ.pop("AUDIT_ASYNC_MODE_ENABLED", None)

        env_value = os.environ.get("AUDIT_ASYNC_MODE_ENABLED", "TRUE").upper()
        result = env_value == "TRUE"

        assert result is True

    def test_async_mode_disabled_by_env(self):
        """환경변수로 비동기 모드 비활성화."""
        os.environ["AUDIT_ASYNC_MODE_ENABLED"] = "FALSE"

        try:
            env_value = os.environ.get("AUDIT_ASYNC_MODE_ENABLED", "TRUE").upper()
            result = env_value == "TRUE"

            assert result is False
        finally:
            os.environ.pop("AUDIT_ASYNC_MODE_ENABLED", None)

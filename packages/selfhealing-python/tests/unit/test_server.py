"""
selfhealing.server 단위 테스트.

Gunicorn Hook Helper 함수들의 동작 검증.
모든 외부 의존성은 Mock으로 대체.
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from selfhealing.server import (
    _SHUTDOWN_BUDGET_MARGIN_SECONDS,
    _emergency_dump,
    _reseed_rng,
    _reset_kafka,
    _reset_mmap,
    _reset_otel,
    _reset_redis,
    _shutdown_audit_system,
    _shutdown_leader_electors,
    _stop_background_threads,
    post_fork_reset,
    post_worker_init_start,
    worker_exit_cleanup,
)


def _make_worker(pid=12345, timeout=60):
    """Gunicorn worker mock 생성."""
    worker = MagicMock()
    worker.pid = pid
    worker.cfg = SimpleNamespace(timeout=timeout)
    return worker


# =============================================================================
# Contract Tests — 설계 문서의 상수/구조 검증
# =============================================================================


class TestServerContract:
    """server.py 설계 계약값 검증."""

    def test_shutdown_budget_margin_is_ten_seconds(self):
        """종료 예산 마진은 10초. (§4.3 Time Budget 패턴)"""
        assert _SHUTDOWN_BUDGET_MARGIN_SECONDS == 10

    def test_post_fork_reset_calls_all_five_reset_functions(self):
        """post_fork_reset()는 Redis/Kafka/OTEL/mmap/RNG 5개 리셋 함수를 호출."""
        worker = _make_worker()
        with (
            patch("selfhealing.server._reset_redis", autospec=True) as m_redis,
            patch("selfhealing.server._reset_kafka", autospec=True) as m_kafka,
            patch("selfhealing.server._reset_otel", autospec=True) as m_otel,
            patch("selfhealing.server._reset_mmap", autospec=True) as m_mmap,
            patch("selfhealing.server._reseed_rng", autospec=True) as m_rng,
        ):
            post_fork_reset(worker)

        m_redis.assert_called_once_with(worker)
        m_kafka.assert_called_once_with(worker)
        m_otel.assert_called_once_with(worker)
        m_mmap.assert_called_once_with(worker)
        m_rng.assert_called_once_with(worker)

    def test_post_fork_reset_continues_after_individual_failure(self):
        """개별 reset 실패 시 나머지 reset이 계속 실행된다. (§4.7 에러 격리)"""
        worker = _make_worker()
        with (
            patch(
                "selfhealing.server._reset_redis",
                autospec=True,
                side_effect=RuntimeError("Redis fail"),
            ),
            patch(
                "selfhealing.server._reset_kafka",
                autospec=True,
                side_effect=RuntimeError("Kafka fail"),
            ),
            patch("selfhealing.server._reset_otel", autospec=True) as m_otel,
            patch("selfhealing.server._reset_mmap", autospec=True) as m_mmap,
            patch("selfhealing.server._reseed_rng", autospec=True) as m_rng,
        ):
            post_fork_reset(worker)

        # Redis, Kafka 실패에도 나머지 3개는 실행됨
        m_otel.assert_called_once_with(worker)
        m_mmap.assert_called_once_with(worker)
        m_rng.assert_called_once_with(worker)

    def test_worker_exit_shutdown_order_is_bg_then_leader_then_audit(self):
        """worker_exit_cleanup()는 background stop → leader elector → audit system 순서로 종료."""
        worker = _make_worker()
        call_order = []

        with (
            patch(
                "selfhealing.server._stop_background_threads",
                autospec=True,
                side_effect=lambda w: call_order.append("background"),
            ),
            patch(
                "selfhealing.server._shutdown_leader_electors",
                autospec=True,
                side_effect=lambda w: call_order.append("leader"),
            ),
            patch(
                "selfhealing.server._shutdown_audit_system",
                autospec=True,
                side_effect=lambda w: call_order.append("audit"),
            ),
            patch("selfhealing.server._emergency_dump", autospec=True),
        ):
            worker_exit_cleanup(worker)

        assert call_order == ["background", "leader", "audit"]


# =============================================================================
# Behavior Tests — 동작 검증
# =============================================================================


class TestPostWorkerInitStartBehavior:
    """post_worker_init_start() 동작 검증."""

    def test_sets_gunicorn_worker_env_var(self):
        """GUNICORN_WORKER 환경변수를 '1'로 설정."""
        worker = _make_worker()
        with patch(
            "selfhealing.server.SelfHealingConfig",
            create=True,
        ):
            # SelfHealingConfig import를 mock — 모듈 레벨이 아닌 함수 내 lazy import
            with patch.dict(os.environ, {}, clear=False):
                with patch(
                    "selfhealing.adapters.django.apps.SelfHealingConfig",
                    autospec=True,
                ):
                    post_worker_init_start(worker)
                assert os.environ.get("GUNICORN_WORKER") == "1"

    def test_calls_start_background_threads(self):
        """SelfHealingConfig.start_background_threads()를 호출."""
        worker = _make_worker()
        with patch(
            "selfhealing.adapters.django.apps.SelfHealingConfig",
            autospec=True,
        ) as mock_config:
            post_worker_init_start(worker)
            mock_config.start_background_threads.assert_called_once()

    def test_import_error_logs_warning(self):
        """selfhealing 미설치 시 warning 로그 기록."""
        worker = _make_worker()
        with (
            patch(
                "selfhealing.server.logger",
                autospec=True,
            ) as mock_logger,
            patch.dict(
                "sys.modules",
                {"selfhealing.adapters.django.apps": None},
            ),
        ):
            post_worker_init_start(worker)
            mock_logger.warning.assert_called()
            assert "not installed" in str(mock_logger.warning.call_args)

    def test_general_exception_logs_warning(self):
        """start_background_threads() 예외 시 warning 로그 기록."""
        worker = _make_worker()
        with (
            patch("selfhealing.server.logger", autospec=True) as mock_logger,
            patch(
                "selfhealing.adapters.django.apps.SelfHealingConfig",
                autospec=True,
            ) as mock_config,
        ):
            mock_config.start_background_threads.side_effect = RuntimeError("test")
            post_worker_init_start(worker)
            mock_logger.warning.assert_called()
            assert "failed" in str(mock_logger.warning.call_args)


class TestWorkerExitCleanupBehavior:
    """worker_exit_cleanup() 동작 검증."""

    def test_budget_calculation_uses_gunicorn_timeout_minus_margin(self):
        """예산 = gunicorn_timeout - 마진."""
        worker = _make_worker(timeout=60)

        with (
            patch("selfhealing.server._stop_background_threads", autospec=True),
            patch("selfhealing.server._shutdown_leader_electors", autospec=True),
            patch("selfhealing.server._shutdown_audit_system", autospec=True),
            patch("selfhealing.server._emergency_dump", autospec=True) as m_dump,
            patch("selfhealing.server.time") as mock_time,
        ):
            # monotonic: initial, after bg, after leader, after audit, final check
            mock_time.monotonic.side_effect = [0.0, 0.0, 0.0, 0.0, 0.0]
            worker_exit_cleanup(worker)
            # Budget = 60 - 10 = 50, deadline = 50.0
            # remaining at check = 50.0 - 0.0 = 50.0 > 0 → no emergency dump
            m_dump.assert_not_called()

    def test_emergency_dump_called_when_budget_exceeded(self):
        """예산 초과 시 _emergency_dump()가 호출된다."""
        worker = _make_worker(timeout=20)

        with (
            patch("selfhealing.server._stop_background_threads", autospec=True),
            patch("selfhealing.server._shutdown_leader_electors", autospec=True),
            patch("selfhealing.server._shutdown_audit_system", autospec=True),
            patch("selfhealing.server._emergency_dump", autospec=True) as m_dump,
            patch("selfhealing.server.time") as mock_time,
        ):
            # Budget = 20 - 10 = 10, deadline = 10.0
            # monotonic: initial=0, after bg=0, after leader=0, after audit=0, final=11.0
            mock_time.monotonic.side_effect = [0.0, 0.0, 0.0, 0.0, 11.0]
            worker_exit_cleanup(worker)
            m_dump.assert_called_once_with(worker)

    def test_default_timeout_used_when_cfg_missing(self):
        """worker.cfg.timeout이 없으면 기본값 60을 사용."""
        worker = MagicMock()
        worker.pid = 12345
        worker.cfg = SimpleNamespace()  # timeout 속성 없음

        with (
            patch("selfhealing.server._stop_background_threads", autospec=True),
            patch("selfhealing.server._shutdown_leader_electors", autospec=True),
            patch("selfhealing.server._shutdown_audit_system", autospec=True),
            patch("selfhealing.server._emergency_dump", autospec=True) as m_dump,
        ):
            worker_exit_cleanup(worker)
            # 기본 timeout=60, budget=50 → 정상 범위이므로 dump 미호출
            m_dump.assert_not_called()


class TestStopBackgroundThreadsBehavior:
    """_stop_background_threads() 동작 검증."""

    def test_calls_stop_background_threads_on_config(self):
        """SelfHealingConfig.stop_background_threads()를 호출."""
        worker = _make_worker()
        # autospec 미사용: stop_background_threads()는 아직 구현되지 않은 계획된 메서드
        with patch(
            "selfhealing.adapters.django.apps.SelfHealingConfig",
        ) as mock_config:
            _stop_background_threads(worker)
            mock_config.stop_background_threads.assert_called_once()

    def test_exception_logged_as_warning(self):
        """예외 발생 시 warning 로그 기록 (크래시하지 않음)."""
        worker = _make_worker()
        with (
            patch(
                "selfhealing.adapters.django.apps.SelfHealingConfig",
            ) as mock_config,
            patch("selfhealing.server.logger", autospec=True) as mock_logger,
        ):
            mock_config.stop_background_threads.side_effect = RuntimeError("test")
            _stop_background_threads(worker)
            mock_logger.warning.assert_called()


class TestShutdownLeaderElectorsBehavior:
    """_shutdown_leader_electors() 동작 검증."""

    def test_calls_shutdown_all_electors(self):
        """shutdown_all_electors()를 호출."""
        worker = _make_worker()
        with patch(
            "selfhealing.coordination.shutdown_integration.shutdown_all_electors",
            autospec=True,
        ) as mock_shutdown:
            _shutdown_leader_electors(worker)
            mock_shutdown.assert_called_once()

    def test_exception_logged_as_warning(self):
        """예외 발생 시 warning 로그 기록."""
        worker = _make_worker()
        with (
            patch(
                "selfhealing.coordination.shutdown_integration.shutdown_all_electors",
                autospec=True,
                side_effect=RuntimeError("test"),
            ),
            patch("selfhealing.server.logger", autospec=True) as mock_logger,
        ):
            _shutdown_leader_electors(worker)
            mock_logger.warning.assert_called()


class TestShutdownAuditSystemBehavior:
    """_shutdown_audit_system() 동작 검증."""

    def test_calls_graceful_shutdown_audit_system(self):
        """graceful_shutdown_audit_system()을 호출."""
        worker = _make_worker()
        with patch(
            "selfhealing.audit.async_audit_lifecycle.graceful_shutdown_audit_system",
            autospec=True,
        ) as mock_shutdown:
            _shutdown_audit_system(worker)
            mock_shutdown.assert_called_once()

    def test_exception_logged_as_warning(self):
        """예외 발생 시 warning 로그 기록."""
        worker = _make_worker()
        with (
            patch(
                "selfhealing.audit.async_audit_lifecycle.graceful_shutdown_audit_system",
                autospec=True,
                side_effect=RuntimeError("test"),
            ),
            patch("selfhealing.server.logger", autospec=True) as mock_logger,
        ):
            _shutdown_audit_system(worker)
            mock_logger.warning.assert_called()


class TestResetRedisBehavior:
    """_reset_redis() 동작 검증."""

    def test_invalidates_provider_registry_singleton(self):
        """ProviderRegistry._instances에서 'cache:redis' 엔트리를 제거."""
        worker = _make_worker()

        from selfhealing.factory import ProviderRegistry

        original = ProviderRegistry._instances.copy()
        try:
            ProviderRegistry._instances["cache:redis"] = "stale_singleton"
            ProviderRegistry._instances["cache:memory"] = "keep"

            _reset_redis(worker)

            assert "cache:redis" not in ProviderRegistry._instances
            assert "cache:memory" in ProviderRegistry._instances
        finally:
            ProviderRegistry._instances.clear()
            ProviderRegistry._instances.update(original)

    def test_no_error_when_no_singleton_exists(self):
        """singleton이 없는 상태에서도 에러 없이 실행."""
        worker = _make_worker()

        from selfhealing.factory import ProviderRegistry

        original = ProviderRegistry._instances.copy()
        try:
            ProviderRegistry._instances.pop("cache:redis", None)
            _reset_redis(worker)  # KeyError 없이 정상 실행
        finally:
            ProviderRegistry._instances.clear()
            ProviderRegistry._instances.update(original)


class TestResetKafkaBehavior:
    """_reset_kafka() 동작 검증."""

    def test_kafka_configured_calls_fork_safe_reset(self):
        """Kafka가 설정되어 있으면 reset_kafka_producer_after_fork()를 호출."""
        worker = _make_worker()
        mock_settings = MagicMock()
        mock_settings.bootstrap_servers = "kafka:9092"

        with (
            patch(
                "selfhealing.adapters.kafka.config.get_kafka_settings",
                autospec=True,
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.adapters.kafka.producer.reset_kafka_producer_after_fork",
                autospec=True,
            ) as mock_reset,
        ):
            _reset_kafka(worker)
            mock_reset.assert_called_once()

    def test_kafka_not_configured_skips_reset(self):
        """bootstrap_servers가 비어있으면 리셋을 건너뛴다."""
        worker = _make_worker()
        mock_settings = MagicMock()
        mock_settings.bootstrap_servers = ""

        with (
            patch(
                "selfhealing.adapters.kafka.config.get_kafka_settings",
                autospec=True,
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.adapters.kafka.producer.reset_kafka_producer_after_fork",
                autospec=True,
            ) as mock_reset,
        ):
            _reset_kafka(worker)
            mock_reset.assert_not_called()


class TestResetOtelBehavior:
    """_reset_otel() 동작 검증."""

    def test_calls_reset_opentelemetry(self):
        """reset_opentelemetry()를 호출."""
        worker = _make_worker()
        with patch(
            "selfhealing.observability.reset_opentelemetry",
            autospec=True,
        ) as mock_reset:
            _reset_otel(worker)
            mock_reset.assert_called_once()


class TestResetMmapBehavior:
    """_reset_mmap() 동작 검증."""

    def test_resets_then_recreates_as_reader(self):
        """reset_cb_state_snapshot() 후 get_cb_state_snapshot(is_writer=False) 호출."""
        worker = _make_worker()
        call_order = []

        with (
            patch(
                "selfhealing.adapters.ipc.reset_cb_state_snapshot",
                autospec=True,
                side_effect=lambda: call_order.append("reset"),
            ),
            patch(
                "selfhealing.adapters.ipc.get_cb_state_snapshot",
                autospec=True,
                side_effect=lambda **kw: call_order.append(
                    f"get(is_writer={kw.get('is_writer')})"
                ),
            ),
        ):
            _reset_mmap(worker)

        assert call_order == ["reset", "get(is_writer=False)"]


class TestReseedRngBehavior:
    """_reseed_rng() 동작 검증."""

    def test_calls_random_seed(self):
        """random.seed()를 호출하여 RNG를 재설정."""
        worker = _make_worker()
        with patch("random.seed", autospec=True) as mock_seed:
            _reseed_rng(worker)
            mock_seed.assert_called_once()


class TestEmergencyDumpBehavior:
    """_emergency_dump() 동작 검증."""

    def test_creates_json_file_with_correct_structure(self, tmp_path):
        """JSON 파일이 올바른 구조로 생성된다."""
        worker = _make_worker(pid=99999)

        with patch.dict(os.environ, {"SELFHEALING_EMERGENCY_DUMP_DIR": str(tmp_path)}):
            _emergency_dump(worker)

        # 생성된 파일 찾기
        files = list(tmp_path.glob("worker_99999_*.json"))
        assert len(files) == 1

        data = json.loads(files[0].read_text())
        assert data["worker_pid"] == 99999
        assert data["reason"] == "shutdown_budget_exceeded"
        assert "timestamp" in data

    def test_write_failure_logs_error(self, tmp_path):
        """파일 쓰기 실패 시 error 로그 기록."""
        worker = _make_worker()

        with (
            patch.dict(
                os.environ,
                {"SELFHEALING_EMERGENCY_DUMP_DIR": str(tmp_path)},
            ),
            patch("selfhealing.server.logger", autospec=True) as mock_logger,
            patch("pathlib.Path.write_text", side_effect=PermissionError("denied")),
        ):
            _emergency_dump(worker)
            mock_logger.error.assert_called()

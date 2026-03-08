"""
317: SelfHealingConfig orphan service wiring 단위 테스트.

테스트 대상:
- _initialize_orphan_services: 위상 정렬 순서 초기화
- _init_* 개별 서비스 초기화 메서드 (Fail-Open 동작)
- _start_correlation_engine_loop: 중복 방지 가드 + 활성화 분기
- _start_capacity_reservation: 중복 방지 가드 + 활성화 분기
- _reset_all_background_state: 317 추가 가드 리셋
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from selfhealing.adapters.django.apps import SelfHealingConfig

# =============================================================================
# Contract: _reset_all_background_state 317 추가 가드 리셋
# =============================================================================


class TestResetBackgroundState317Contract:
    """317: _reset_all_background_state가 신규 가드도 리셋하는지 계약 검증."""

    def test_resets_correlation_loop_guard(self):
        """_correlation_loop_started가 리셋된다."""
        SelfHealingConfig._correlation_loop_started = True
        SelfHealingConfig._reset_all_background_state()
        assert SelfHealingConfig._correlation_loop_started is False

    def test_resets_capacity_reservation_guard(self):
        """_capacity_reservation_started가 리셋된다."""
        SelfHealingConfig._capacity_reservation_started = True
        SelfHealingConfig._reset_all_background_state()
        assert SelfHealingConfig._capacity_reservation_started is False


# =============================================================================
# Behavior: _start_correlation_engine_loop 중복 방지
# =============================================================================


class TestStartCorrelationEngineLoopBehavior:
    """317: _start_correlation_engine_loop 동작 검증."""

    def setup_method(self):
        """각 테스트 전 가드 리셋."""
        SelfHealingConfig._correlation_loop_started = False

    def test_skips_when_settings_disabled(self):
        """설정 비활성화 시 시작하지 않음."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        mock_settings = MagicMock()
        mock_settings.enabled = False

        with patch(
            "selfhealing.settings.correlation.get_correlation_settings",
            return_value=mock_settings,
        ):
            config._start_correlation_engine_loop()

        assert SelfHealingConfig._correlation_loop_started is False

    def test_duplicate_start_prevented(self):
        """이미 시작된 상태에서 중복 호출 방지."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)
        SelfHealingConfig._correlation_loop_started = True

        mock_settings = MagicMock()
        mock_settings.enabled = True

        with patch(
            "selfhealing.settings.correlation.get_correlation_settings",
            return_value=mock_settings,
        ):
            config._start_correlation_engine_loop()

    def test_import_error_handled_gracefully(self):
        """모듈 import 실패 시 예외 없이 통과."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        with patch(
            "selfhealing.settings.correlation.get_correlation_settings",
            side_effect=ImportError("no module"),
        ):
            config._start_correlation_engine_loop()

        assert SelfHealingConfig._correlation_loop_started is False

    def test_generic_exception_handled_gracefully(self):
        """일반 예외 시 경고 후 통과."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        mock_settings = MagicMock()
        mock_settings.enabled = True

        with (
            patch(
                "selfhealing.settings.correlation.get_correlation_settings",
                return_value=mock_settings,
            ),
            patch(
                "selfhealing.services.correlation_engine.service.CorrelationEngineService",
                side_effect=RuntimeError("engine broken"),
            ),
        ):
            config._start_correlation_engine_loop()


# =============================================================================
# Behavior: _start_capacity_reservation 중복 방지
# =============================================================================


class TestStartCapacityReservationBehavior:
    """317: _start_capacity_reservation 동작 검증."""

    def setup_method(self):
        """각 테스트 전 가드 리셋."""
        SelfHealingConfig._capacity_reservation_started = False

    def test_skips_when_settings_disabled(self):
        """설정 비활성화 시 시작하지 않음."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        mock_settings = MagicMock()
        mock_settings.enabled = False

        with patch(
            "selfhealing.settings.capacity_reservation.get_capacity_reservation_settings",
            return_value=mock_settings,
        ):
            config._start_capacity_reservation()

        assert SelfHealingConfig._capacity_reservation_started is False

    def test_duplicate_start_prevented(self):
        """이미 시작된 상태에서 중복 호출 방지."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)
        SelfHealingConfig._capacity_reservation_started = True

        mock_settings = MagicMock()
        mock_settings.enabled = True

        with patch(
            "selfhealing.settings.capacity_reservation.get_capacity_reservation_settings",
            return_value=mock_settings,
        ):
            config._start_capacity_reservation()

    def test_import_error_handled_gracefully(self):
        """모듈 import 실패 시 예외 없이 통과."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        with patch(
            "selfhealing.settings.capacity_reservation.get_capacity_reservation_settings",
            side_effect=ImportError("no module"),
        ):
            config._start_capacity_reservation()

        assert SelfHealingConfig._capacity_reservation_started is False


# =============================================================================
# Behavior: _init_* 개별 서비스 Fail-Open 동작
# =============================================================================


class TestInitOrphanServiceFailOpenBehavior:
    """317: 개별 _init_* 메서드의 Fail-Open 동작 검증."""

    def test_init_event_journal_import_error(self):
        """EventJournal import 실패 시 예외 없이 통과."""
        with patch(
            "selfhealing.settings.event_journal.EventJournalSettings",
            side_effect=ImportError("no module"),
        ):
            SelfHealingConfig._init_event_journal()

    def test_init_event_journal_disabled(self):
        """EventJournal 비활성화 시 초기화 스킵."""
        mock_settings = MagicMock()
        mock_settings.enabled = False

        with patch(
            "selfhealing.settings.event_journal.EventJournalSettings",
            return_value=mock_settings,
        ):
            SelfHealingConfig._init_event_journal()

    def test_init_correlation_engine_import_error(self):
        """CorrelationEngine import 실패 시 예외 없이 통과."""
        with patch(
            "selfhealing.settings.correlation.get_correlation_settings",
            side_effect=ImportError("no module"),
        ):
            SelfHealingConfig._init_correlation_engine()

    def test_init_capacity_reservation_import_error(self):
        """CapacityReservation import 실패 시 예외 없이 통과."""
        with patch(
            "selfhealing.settings.capacity_reservation.get_capacity_reservation_settings",
            side_effect=ImportError("no module"),
        ):
            SelfHealingConfig._init_capacity_reservation()

    def test_init_saga_autodiscover_import_error(self):
        """Celery import 실패 시 예외 없이 통과."""
        with patch(
            "celery.current_app",
            side_effect=ImportError("no celery"),
            create=True,
        ):
            SelfHealingConfig._init_saga_autodiscover()

    def test_init_config_propagator_import_error(self):
        """ConfigPropagator import 실패 시 예외 없이 통과."""
        with patch(
            "selfhealing.services.config.propagator.get_global_config_propagator",
            side_effect=ImportError("no module"),
        ):
            SelfHealingConfig._init_config_propagator()

    def test_init_runbook_import_error(self):
        """Runbook import 실패 시 예외 없이 통과."""
        with patch(
            "selfhealing.settings.runbook.get_runbook_settings",
            side_effect=ImportError("no module"),
        ):
            SelfHealingConfig._init_runbook()

    def test_init_runbook_disabled(self):
        """Runbook 비활성화 시 초기화 스킵."""
        mock_settings = MagicMock()
        mock_settings.enabled = False

        with patch(
            "selfhealing.settings.runbook.get_runbook_settings",
            return_value=mock_settings,
        ):
            SelfHealingConfig._init_runbook()


# =============================================================================
# Behavior: _initialize_orphan_services 통합 흐름
# =============================================================================


class TestInitializeOrphanServicesBehavior:
    """317: _initialize_orphan_services 통합 흐름 검증."""

    def test_calls_all_six_initializers(self):
        """6개 초기화 메서드가 모두 호출된다."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)
        call_order = []

        def make_tracker(name):
            def tracker():
                call_order.append(name)

            return tracker

        with (
            patch.object(config, "_init_event_journal", make_tracker("event_journal")),
            patch.object(
                config, "_init_correlation_engine", make_tracker("correlation_engine")
            ),
            patch.object(
                config,
                "_init_capacity_reservation",
                make_tracker("capacity_reservation"),
            ),
            patch.object(config, "_init_saga_autodiscover", make_tracker("saga")),
            patch.object(config, "_init_config_propagator", make_tracker("config")),
            patch.object(config, "_init_runbook", make_tracker("runbook")),
        ):
            config._initialize_orphan_services()

        assert len(call_order) == 6
        assert set(call_order) == {
            "event_journal",
            "correlation_engine",
            "capacity_reservation",
            "saga",
            "config",
            "runbook",
        }

    def test_exception_in_graph_does_not_crash(self):
        """ServiceDependencyGraph 예외 시 전체 초기화가 중단되지 않음."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        with patch(
            "selfhealing.core.dependency_graph.ServiceDependencyGraph",
            side_effect=RuntimeError("graph broken"),
        ):
            config._initialize_orphan_services()


# =============================================================================
# Behavior: _start_all_background_threads 317 추가 호출
# =============================================================================


class TestStartAllBackgroundThreads317Behavior:
    """317: _start_all_background_threads가 317 추가 메서드를 호출하는지 검증."""

    def test_calls_correlation_and_capacity_methods(self):
        """_start_all_background_threads가 correlation/capacity 메서드 호출."""
        config = SelfHealingConfig.__new__(SelfHealingConfig)

        with (
            patch.object(config, "_schedule_gauge_hydration"),
            patch.object(config, "_start_precomputed_cache_worker"),
            patch.object(config, "_start_system_metrics_cache"),
            patch.object(config, "_start_meta_watchdog"),
            patch.object(config, "_start_correlation_engine_loop") as mock_corr,
            patch.object(config, "_start_capacity_reservation") as mock_cap,
        ):
            config._start_all_background_threads()

        mock_corr.assert_called_once()
        mock_cap.assert_called_once()

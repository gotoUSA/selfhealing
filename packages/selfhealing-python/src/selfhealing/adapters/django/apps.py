"""
Django App Configuration for Self-Healing.

This allows the selfhealing.adapters.django module to be used
as a Django app in INSTALLED_APPS.

Lifecycle Hooks:
    1. ready() - Called on every server start
       - Environment variable snapshot logging (audit trail)
       - Metric Gauge hydration (Startup Hydration)
       - Runs every time because env vars can change between restarts

    2. post_migrate signal - Called only after migrations
       - RBAC group creation (DB schema initialization)
       - Runs only when DB schema changes (efficient)

RBAC Groups:
    - selfhealing_viewer: Read-only access (dashboard, status, audit logs)
    - selfhealing_operator: Operational tasks (DLQ replay, archive)
    - selfhealing_admin: Full access (CB control, system enable/disable, config)

Design Rationale:
    - Environment variables = Process lifecycle (ready)
    - Database schema = Data lifecycle (post_migrate)
    - Metric Gauges = Server lifecycle (ready, with jitter)
    - Industry standard: 12-Factor App, Spring Boot ApplicationReadyEvent

Note:
    As of v2.0.0, Redis is the default storage backend.
    This app config still provides:
    - RBAC group auto-creation
    - Environment variable audit
    - Startup hydration for Prometheus gauges
    - Pre-computed cache worker
"""

from __future__ import annotations

import os
import random
import threading
from typing import TYPE_CHECKING

import structlog
from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

# RBAC group definitions
SELFHEALING_GROUPS: list[str] = [
    "selfhealing_viewer",
    "selfhealing_operator",
    "selfhealing_admin",
]


def create_selfhealing_groups(sender, **kwargs):
    """
    Create RBAC groups for Self-Healing system.

    Called via post_migrate signal - runs only after migrations complete.
    Uses get_or_create for idempotency.

    Note: Environment variable snapshot is logged in ready() instead,
    because env vars can change on every restart (not just migrations).
    """
    try:
        from django.contrib.auth.models import Group

        created_groups = []
        existing_groups = []

        for group_name in SELFHEALING_GROUPS:
            group, created = Group.objects.get_or_create(name=group_name)
            if created:
                created_groups.append(group_name)
            else:
                existing_groups.append(group_name)

        if created_groups:
            logger.info(
                "self_healing.rbac_groups_created",
                created_groups=created_groups,
            )

        if existing_groups and created_groups:
            logger.debug(
                "self_healing.rbac_groups_already_existed",
                existing_groups=existing_groups,
            )

    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(
            "self_healing.failed_create_rbac_groups",
            error=e,
        )


class SelfHealingConfig(AppConfig):
    """Django app configuration for self-healing."""

    name = "selfhealing.adapters.django"
    label = "selfhealing"
    verbose_name = "Self-Healing System"
    default_auto_field = "django.db.models.BigAutoField"

    # Startup Hydration - 중복 실행 방지
    _hydration_done: bool = False
    _hydration_lock: threading.Lock = threading.Lock()

    # V3: Pre-computed Cache Worker - 중복 실행 방지
    _cache_worker_started: bool = False
    _cache_worker_lock: threading.Lock = threading.Lock()

    # System Metrics Cache - 중복 실행 방지
    _metrics_cache_started: bool = False
    _metrics_cache_lock: threading.Lock = threading.Lock()

    def ready(self):
        """
        Called when the app is ready (every server start).

        Responsibilities:
        1. Connect post_migrate signal for RBAC group creation
        2. Log environment variable snapshot for audit trail
        3. Validate config with Safe Defaults (Fail-Safe Default 강화)
        4. Hydrate metric gauges with jitter (Startup Hydration)
        5. Start pre-computed cache worker (V3 Optimization)

        Note: Environment snapshot is logged here (not in post_migrate) because
        env vars can change on every restart, not just during migrations.
        This aligns with 12-Factor App principles and Spring Boot patterns.
        """
        # Connect post_migrate signal for RBAC group creation
        # sender=self ensures it only runs when this app's migrations complete
        post_migrate.connect(
            create_selfhealing_groups,
            sender=self,
            dispatch_uid="selfhealing_create_rbac_groups",
        )

        # Connect session signal handlers (user_logged_in / user_logged_out)
        # UserSessionRegistry에 session_key 역방향 매핑을 자동 관리
        self._connect_session_signals()

        # Celery autodiscover: selfhealing.celery_tasks 등록 (223 Host App Decoupling)
        self._autodiscover_celery_tasks()

        # Log environment variable snapshot (환경변수 Audit)
        # This runs on every server start because env vars can change
        # between restarts (e.g., Docker container restart with new env)
        self._log_env_snapshot()

        # Sync hash chain state (Redis ↔ Local file)
        # Ensures consistency after server restarts or Redis recovery
        self._sync_hash_chain_on_startup()

        # Validate config with Safe Defaults (Fail-Safe Default 강화)
        self._validate_startup_config()

        # 317: Orphan service wiring — pure-memory initialization (Category A)
        self._initialize_orphan_services()

        # Background threads: Gauge hydration, Precomputed Cache, System Metrics, Watchdog.
        # In Gunicorn preload mode, ready() runs in Master — background threads
        # die after fork(). post_worker_init calls start_background_threads() instead.
        # In dev server (manage.py runserver), start threads directly here.
        if self._should_start_background_threads():
            self._start_all_background_threads()

        # Validate required secrets (Security Hardening)
        self._validate_secrets()

        # Register JWT blacklist hook for session invalidation
        self._register_jwt_blacklist_hook()

    @staticmethod
    def _connect_session_signals():
        """Django 세션 시그널 핸들러 연결."""
        try:
            from selfhealing.adapters.django.signal_hooks import (
                connect_session_signals,
            )

            connect_session_signals()
        except Exception as e:
            logger.warning(
                "self_healing.failed_connect_session_signals",
                error=e,
            )

    @staticmethod
    def _autodiscover_celery_tasks():
        """
        Celery autodiscover: selfhealing.celery_tasks 모듈 자동 등록.

        호스트 앱(shopping)에서 수동으로 import 하던 셀러리 태스크를
        패키지 자체에서 autodiscover 하여 등록한다.
        Celery가 설치되지 않은 환경에서는 조용히 건너뛴다.
        """
        try:
            from celery import current_app

            current_app.autodiscover_tasks(["selfhealing.celery_tasks"])
            logger.info("self_healing.celery_tasks_autodiscovered_selfhealing")
        except ImportError:
            logger.debug("self_healing.celery_installed_skipping_task")
        except Exception as e:
            logger.warning(
                "self_healing.failed_autodiscover_celery_tasks",
                error=e,
            )

    def _log_env_snapshot(self):
        """
        Log environment variable snapshot for audit trail.

        Best-effort: If logging fails, system continues normally.
        This is critical for compliance (enterprise audit requirements).
        """
        try:
            from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit

            log_env_snapshot_to_audit()
        except ImportError:
            logger.debug("self_healing.module_available")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(
                "self_healing.failed_log_env_snapshot",
                error=e,
            )

    def _sync_hash_chain_on_startup(self):
        """
        Synchronize hash chain state between Redis and local files on startup.

        Handles recovery scenarios:
        - Redis behind file: Sync Redis to file state
        - File behind Redis: Normal, some writes may be pending
        - PENDING sequences: Cleanup from previous crashes

        Best-effort: If sync fails, system continues normally.
        Hash chain will be recovered on next successful write.

        Reference:
            docs/self_healing/middleware_system/43_DISTRIBUTED_HASH_CHAIN_ENHANCED.md
        """
        try:
            # Check if distributed hash chain is enabled
            if not getattr(settings, "SELFHEALING_DISTRIBUTED_HASH_CHAIN", False):
                logger.debug("self_healing.distributed_hash_chain_enabled")
                return

            from pathlib import Path

            from selfhealing.audit.integrity import StartupHashChainSync

            # Get Redis client
            redis_client = self._get_redis_client_for_hash_chain()
            if redis_client is None:
                logger.debug("self_healing.redis_client_available_hash")
                return

            # Get log directory from settings
            log_dir = Path(getattr(settings, "SELFHEALING_AUDIT_LOG_DIR", "logs/audit"))

            # Perform sync
            sync = StartupHashChainSync(
                redis_client=redis_client,
                log_dir=log_dir,
                key_prefix=getattr(
                    settings, "SELFHEALING_REDIS_KEY_PREFIX", "selfhealing:"
                ),
            )
            result = sync.sync()

            # Log result
            if result.get("status") == "success":
                action = result.get("action", "none")
                pending_cleaned = result.get("pending_cleaned", 0)

                if action == "synced_redis_to_file":
                    logger.warning(
                        "self_healing.hash_chain_sync_redis",
                        file_sequence=result.get("file_sequence"),
                    )
                elif action == "fresh_start":
                    logger.info("self_healing.hash_chain_sync_fresh")
                else:
                    logger.info(
                        "self_healing.hash_chain_sync",
                        sync_action=action,
                    )

                if pending_cleaned > 0:
                    logger.info(
                        "self_healing.hash_chain_sync_cleaned",
                        pending_cleaned=pending_cleaned,
                    )
            else:
                logger.warning(
                    "self_healing.hash_chain_sync_failed",
                    sync_error=result.get("error", "unknown"),
                )

        except ImportError:
            logger.debug("self_healing.integrity_module_available_hash")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(
                "self_healing.failed_sync_hash_chain",
                error=e,
            )

    def _get_redis_client_for_hash_chain(self):
        """
        Get Redis client for hash chain operations.

        Attempts multiple strategies:
        1. From ResilientStorageBackend if available
        2. From django_redis cache
        3. Direct redis-py connection

        Returns:
            Redis client instance or None if unavailable
        """
        try:
            # Strategy 1: Try ResilientStorageBackend
            try:
                from selfhealing.adapters.resilient.backend import (
                    ResilientStorageBackend,
                )

                backend = ResilientStorageBackend()
                return backend.get_redis_client()
            except (ImportError, Exception):
                pass

            # Strategy 2: Try django_redis
            try:
                from django_redis import get_redis_connection

                return get_redis_connection("default")
            except (ImportError, Exception):
                pass

            # Strategy 3: Try direct redis connection from settings
            try:
                import redis

                redis_url = getattr(settings, "SELFHEALING_REDIS_URL", None)
                if redis_url:
                    return redis.from_url(redis_url)
            except (ImportError, Exception):
                pass

            return None

        except Exception:
            return None

    def _validate_startup_config(self):
        """
        Validate config with Safe Defaults on startup.

        Fail-Safe Default 강화:
        - Non-fatal 설정: Safe Default 적용 후 계속 운영
        - Fatal 설정 위반: Quarantine Mode (LEVEL_3) 활성화
        """
        try:
            from selfhealing.core.safe_defaults import (
                ENABLE_QUARANTINE_ON_FATAL,
                FatalConfigError,
                validate_startup_config,
            )

            try:
                changes = validate_startup_config(
                    log_changes=True, raise_on_fatal=False
                )

                if changes > 0:
                    logger.info(
                        "self_healing.startup_config_validation_applied",
                        changes=changes,
                    )
                else:
                    logger.debug("self_healing.startup_config_validation_all")

            except FatalConfigError as e:
                # Fatal 설정 위반 시 Quarantine Mode 활성화
                if ENABLE_QUARANTINE_ON_FATAL:
                    self._activate_quarantine_mode(e)
                else:
                    logger.critical(
                        "self_healing.fatal_config_error_quarantine",
                        error=e,
                    )

        except ImportError:
            logger.debug("self_healing.module_available")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(
                "self_healing.failed_validate_startup_config",
                error=e,
            )

    def _activate_quarantine_mode(self, error: Exception):
        """
        Fatal 설정 위반 시 Quarantine Mode (LEVEL_3) 활성화.

        Quarantine Mode:
        - EmergencyLevel.LEVEL_3 활성화 (Critical 트래픽만 50% 허용)
        - 시스템은 시작하지만 격리된 상태로 운영
        - 수동 개입 필요 (설정 수정 후 재시작)

        Args:
            error: FatalConfigError 인스턴스
        """
        try:
            from selfhealing.services.emergency_mode import (
                EmergencyLevel,
                GracefulDegradationManager,
            )

            manager = GracefulDegradationManager()

            # Quarantine Mode (LEVEL_3) 활성화
            manager.activate(
                level=EmergencyLevel.LEVEL_3,
                reason=f"Config Quarantine: {str(error)[:200]}",
                activated_by="system:config_validation",
                ttl_seconds=None,  # 무기한 (수동 해제 필요)
            )

            logger.critical("quarantine.system_started_quarantine_mode")

        except ImportError:
            logger.warning("self_healing.module_available_quarantine")
        except Exception as e:
            logger.exception(
                "self_healing.failed_activate_quarantine_mode",
                error=e,
            )

    # =========================================================================
    # Startup Hydration - 시작 시 Prometheus Gauge 초기화
    # =========================================================================

    def _should_hydrate(self) -> bool:
        """
        Hydration 실행 여부 판단.

        중복 실행 방지 + 설정 체크.

        Returns:
            bool: True면 hydration 실행
        """
        # 설정에서 비활성화된 경우
        if not getattr(settings, "SELFHEALING_SYNC_ON_STARTUP", True):
            logger.debug("self_healing.gauge_hydration_disabled_settings")
            return False

        # 중복 실행 방지
        with self._hydration_lock:
            if self._hydration_done:
                logger.debug("self_healing.gauge_hydration_already_scheduled")
                return False
            SelfHealingConfig._hydration_done = True
            return True

    def _schedule_gauge_hydration(self):
        """
        Gauge 초기화를 Jitter 적용하여 스케줄링.

        서버 시작 직후 바로 DB 조회하지 않고,
        랜덤 지연 후 백그라운드에서 초기화합니다.

        Jitter 목적:
        - 분산 환경에서 여러 서버가 동시 재시작 시 DB 부하 분산
        - Thundering Herd 방지
        """
        if not self._should_hydrate():
            return

        # Jitter 계산 (0 ~ max_delay 초)
        jitter_max = getattr(settings, "SELFHEALING_SYNC_JITTER_MAX", 60)
        jitter = random.uniform(0, jitter_max)

        # 백그라운드에서 초기화 (서버 시작 블로킹 방지)
        timer = threading.Timer(jitter, self._hydrate_gauges)
        timer.daemon = True  # 메인 스레드 종료 시 함께 종료
        timer.start()

        logger.info(
            "self_healing.gauge_hydration_scheduled",
            jitter=jitter,
            jitter_max=jitter_max,
        )

    def _hydrate_gauges(self):
        """
        Gauge 초기화 (1회만 실행).

        Redis에서 실제 값을 조회하여 Prometheus Gauge를 초기화합니다.
        v2.0.0부터 Redis 기반이므로 Redis에서 조회합니다.

        Graceful Degradation:
        - 실패해도 서버 기동은 계속
        - 경고 로그만 남기고 정상 운영
        """
        try:
            # Reconciler를 통해 Gauge 동기화
            from selfhealing.metrics.reconciler import get_reconciler

            reconciler = get_reconciler()
            result = reconciler.sync_all_gauges()

            logger.info(
                "self_healing.gauge_hydration_completed",
                dlq_pending_count=len(result.dlq_pending),
                cb_states_count=len(result.circuit_breaker_states),
            )

        except ImportError:
            logger.debug("self_healing.reconciler_module_available")
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                "self_healing.gauge_hydration_failed_non",
                error=e,
            )

    # =========================================================================
    # V3: Pre-computed Cache Worker
    # =========================================================================

    def _start_precomputed_cache_worker(self):
        """
        Start pre-computed cache worker for L3 observability endpoints.

        V3 최적화: L3 엔드포인트 P95 < 50ms 달성을 위한 사전 계산 캐시.
        - /health/ - 7-9ms (was 92ms)
        - /error-budget/status/ - 7-9ms (was 111ms)
        - /stress/pool-status/ - 13-41ms (was 168ms)

        Graceful Degradation:
        - 실패해도 서버 기동은 계속 (캐시 없이 직접 계산으로 fallback)
        """
        # 설정에서 비활성화된 경우
        if not getattr(settings, "SELFHEALING_PRECOMPUTED_CACHE_ENABLED", True):
            logger.debug("self_healing.pre_computed_cache_disabled")
            return

        # 중복 실행 방지
        with self._cache_worker_lock:
            if self._cache_worker_started:
                logger.debug("self_healing.pre_computed_cache_worker")
                return
            SelfHealingConfig._cache_worker_started = True

        try:
            from selfhealing.services.precomputed_cache import (
                register_default_compute_functions,
                start_precomputed_cache,
            )

            # Register compute functions for L3 endpoints
            register_default_compute_functions()

            # Start background worker
            start_precomputed_cache()

            logger.info("self_healing.pre_computed_cache_worker")

        except ImportError:
            logger.debug("self_healing.module_available")
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                "self_healing.failed_start_pre_computed",
                error=e,
            )

    # =========================================================================
    # System Metrics Cache - psutil CPU/Memory 백그라운드 캐시
    # =========================================================================

    def _start_system_metrics_cache(self):
        """
        시스템 메트릭 캐시 워커 시작.

        psutil CPU/Memory를 1초마다 백그라운드에서 캐시하여
        collect_system_snapshot(), ResourceGuard 등의 100ms 블로킹을 제거.
        실패 시 모든 소비자가 직접 psutil 호출로 fallback.
        """
        from selfhealing.settings.system_metrics_cache import (
            get_system_metrics_cache_settings,
        )

        settings = get_system_metrics_cache_settings()
        if not settings.enabled:
            logger.debug("self_healing.system_metrics_cache_disabled")
            return

        with self._metrics_cache_lock:
            if self._metrics_cache_started:
                return
            SelfHealingConfig._metrics_cache_started = True

        try:
            from selfhealing.services.system_metrics_cache import (
                get_system_metrics_cache,
                start_system_metrics_cache,
            )

            cache = get_system_metrics_cache()
            cache._refresh_interval = settings.refresh_interval
            cache._sample_interval = settings.sample_interval
            cache._max_age_seconds = settings.max_age_seconds

            start_system_metrics_cache()

            logger.info(
                "self_healing.system_metrics_cache_started",
                settings=settings.refresh_interval,
                sample_interval=settings.sample_interval,
            )

        except ImportError:
            logger.debug("self_healing.module_available")
        except Exception as e:
            logger.warning(
                "self_healing.failed_start_system_metrics",
                error=e,
            )

    # =========================================================================
    # Meta-Watchdog - Self-Healing 시스템 자체 모니터링
    # =========================================================================

    # Meta-Watchdog 중복 실행 방지
    _meta_watchdog_started: bool = False
    _meta_watchdog_lock: threading.Lock = threading.Lock()

    def _start_meta_watchdog(self):
        """
        Start Meta-Watchdog for Self-Healing system self-monitoring.

        Meta-Watchdog는 Self-Healing 시스템 자체의 건강 상태를 모니터링하고,
        장애 시 자동 복구 또는 인간 에스컬레이션을 수행합니다.

        "치료사가 아플 때" 문제 해결:
        - Circuit Breaker, DLQ, Redis 등 서브시스템 모니터링
        - Stuck 감지 및 자동 복구 시도
        - 자동 복구 실패 시 PagerDuty/Slack 에스컬레이션

        Graceful Degradation:
        - 실패해도 서버 기동은 계속 (메인 Self-Healing 시스템은 정상 동작)

        Reference:
            docs/self_healing/middleware_system/177_SELF_HEALING_META_WATCHDOG.md
        """

        # 환경변수로 비활성화 가능
        if os.environ.get("SELFHEALING_META_ENABLED", "true").lower() != "true":
            logger.debug("self_healing.meta_watchdog_disabled_environment")
            return

        # Django settings에서 비활성화된 경우
        if not getattr(settings, "SELFHEALING_META_WATCHDOG_ENABLED", True):
            logger.debug("self_healing.meta_watchdog_disabled_django")
            return

        # 중복 실행 방지
        with self._meta_watchdog_lock:
            if self._meta_watchdog_started:
                logger.debug("self_healing.meta_watchdog_already_started")
                return
            SelfHealingConfig._meta_watchdog_started = True

        try:
            from selfhealing.meta.watchdog import get_selfhealer_watchdog

            watchdog = get_selfhealer_watchdog()
            watchdog.start()

            logger.info("self_healing.meta_watchdog_started_monitoring")

        except ImportError:
            logger.debug("self_healing.meta_watchdog_module_available")
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                "self_healing.failed_start_meta_watchdog",
                error=e,
            )

    @classmethod
    def reset_meta_watchdog_state(cls):
        """
        Meta-Watchdog 상태 리셋 (테스트용).

        단위 테스트에서 중복 실행 방지 플래그를 리셋합니다.
        """
        with cls._meta_watchdog_lock:
            cls._meta_watchdog_started = False

    # =========================================================================
    # 317: Correlation Engine Analysis Loop (Category B — background thread)
    # =========================================================================

    _correlation_loop_started: bool = False
    _correlation_loop_lock: threading.Lock = threading.Lock()

    def _start_correlation_engine_loop(self):
        """317: CorrelationEngine 분석 루프 시작 (LeaderScheduler 스레드)."""
        try:
            from selfhealing.settings.correlation import get_correlation_settings

            settings = get_correlation_settings()
            if not settings.enabled:
                return

            with self._correlation_loop_lock:
                if self._correlation_loop_started:
                    return
                SelfHealingConfig._correlation_loop_started = True

            from selfhealing.services.correlation_engine.service import (
                CorrelationEngineService,
            )

            engine = CorrelationEngineService.get_instance()
            engine.start_analysis_loop()
            logger.info("self_healing.correlation_engine_loop_started")

        except ImportError:
            logger.debug("self_healing.correlation_engine_module_not_available")
        except Exception as e:
            logger.warning(
                "self_healing.failed_start_correlation_engine_loop",
                error=e,
            )

    # =========================================================================
    # 317: Capacity Reservation Scheduler (Category B — background thread)
    # =========================================================================

    _capacity_reservation_started: bool = False
    _capacity_reservation_lock: threading.Lock = threading.Lock()

    def _start_capacity_reservation(self):
        """317: CapacityReservationService 스케줄러 스레드 시작."""
        try:
            from selfhealing.settings.capacity_reservation import (
                get_capacity_reservation_settings,
            )

            settings = get_capacity_reservation_settings()
            if not settings.enabled:
                return

            with self._capacity_reservation_lock:
                if self._capacity_reservation_started:
                    return
                SelfHealingConfig._capacity_reservation_started = True

            from selfhealing.services.capacity_reservation.service import (
                CapacityReservationService,
            )

            service = CapacityReservationService()
            service.start()
            logger.info("self_healing.capacity_reservation_started")

        except ImportError:
            logger.debug("self_healing.capacity_reservation_module_not_available")
        except Exception as e:
            logger.warning(
                "self_healing.failed_start_capacity_reservation",
                error=e,
            )

    # =========================================================================
    # Secrets Validation
    # =========================================================================

    def _validate_secrets(self):
        """
        핵심 시크릿 검증.

        동작 모드:
        - Non-production: best-effort (검증 실패해도 시스템 시작 계속)
        - Production + CRITICAL 시크릿 미설정: RuntimeError 재발생으로 시작 차단

        Note: _validate_startup_config()은 모든 예외를 warning 처리(best-effort)하지만,
        이 메서드는 프로덕션 CRITICAL 시크릿에 한해 의도적으로 시작을 차단함.
        보안 시크릿 미설정 상태로 운영하는 것은 용납할 수 없기 때문.
        """
        try:
            from selfhealing.settings.secrets import validate_required_secrets

            result = validate_required_secrets()

            critical_count = len(result.get("critical", []))
            warning_count = len(result.get("warning", []))

            if critical_count > 0:
                logger.error(
                    "self_healing.critical_secrets_configured_check",
                    critical_count=critical_count,
                )
            elif warning_count > 0:
                logger.warning(
                    "self_healing.important_secrets_configured_check",
                    warning_count=warning_count,
                )
            else:
                logger.info("self_healing.all_secrets_validated_successfully")

        except RuntimeError as e:
            # 프로덕션에서 CRITICAL 시크릿 미설정 → 재발생으로 시작 차단
            # secrets.py가 이미 개별 시크릿별 ERROR/WARNING을 로깅하지만,
            # traceback과 해결 방법(환경변수 설정 가이드)은 제공하지 않음.
            # 이 블록에서 보완하여 운영자가 즉시 조치할 수 있도록 함.
            logger.critical(
                "self_healing.secrets_validation_failed_resolution",
                error=e,
            )
            raise
        except Exception as e:
            # 기타 오류 → best-effort로 시작 계속
            logger.warning(
                "self_healing.secrets_validation_failed",
                error=e,
            )

    # =========================================================================
    # JWT Blacklist Hook Registration
    # =========================================================================

    def _register_jwt_blacklist_hook(self):
        """
        JWT 블랙리스트 콜백 등록.

        rest_framework_simplejwt.token_blacklist가 INSTALLED_APPS에 있을 때만
        콜백을 등록합니다. 보안 위반(TOKEN_FORGED) 감지 시 해당 사용자의
        모든 OutstandingToken을 블랙리스트에 추가합니다.
        """
        try:
            from django.apps import apps

            if not apps.is_installed("rest_framework_simplejwt.token_blacklist"):
                logger.debug("self_healing.installed_skipping_jwt_hook")
                return

            from selfhealing.services.security.hooks import (
                register_session_invalidation_hook,
            )

            def blacklist_user_jwt(user_id: int) -> str:
                """사용자의 모든 OutstandingToken을 블랙리스트에 추가."""
                from rest_framework_simplejwt.token_blacklist.models import (
                    BlacklistedToken,
                    OutstandingToken,
                )

                tokens = OutstandingToken.objects.filter(user_id=user_id)
                count = 0
                for token in tokens:
                    _, created = BlacklistedToken.objects.get_or_create(token=token)
                    if created:
                        count += 1
                return f"jwt_blacklisted({count})" if count > 0 else ""

            register_session_invalidation_hook(blacklist_user_jwt)
            logger.info("self_healing.jwt_blacklist_hook_registered")

            # DONE(#217): OutstandingToken 정리 Celery Beat 등록 완료
            # 태스크: selfhealing/tasks/cleanup_tasks.py flush_expired_jwt_tokens
            # 스케줄: get_cleanup_beat_schedule() + myproject/celery.py (매일 02:30)

        except ImportError as e:
            logger.debug(
                "self_healing.jwt_hook_registration_skipped",
                error=e,
            )
        except Exception as e:
            logger.warning(
                "self_healing.jwt_hook_registration_failed",
                error=e,
            )

    # =========================================================================
    # 317: Orphan Service Wiring — Pure-Memory Initialization (Category A)
    # =========================================================================

    def _initialize_orphan_services(self):
        """317: 고아 서비스 초기화 — ServiceDependencyGraph 위상 정렬 순서 보장."""
        try:
            from selfhealing.core.dependency_graph import ServiceDependencyGraph

            graph = ServiceDependencyGraph()

            graph.register_service("event_journal")
            graph.register_service("config_shadow", depends_on=["event_journal"])
            graph.register_service("event_bus")
            graph.register_service("rate_controller")
            graph.register_service("bulkhead")
            graph.register_service("correlation_engine", depends_on=["event_bus"])
            graph.register_service(
                "capacity_reservation",
                depends_on=["rate_controller", "bulkhead"],
            )
            graph.register_service("saga")
            graph.register_service("config")
            graph.register_service("runbook")

            init_order = graph.topological_sort_subset(
                services=[
                    "event_journal",
                    "correlation_engine",
                    "capacity_reservation",
                    "saga",
                    "config",
                    "runbook",
                ],
                direction="leaves_first",
            )

            initializers = {
                "event_journal": self._init_event_journal,
                "correlation_engine": self._init_correlation_engine,
                "capacity_reservation": self._init_capacity_reservation,
                "saga": self._init_saga_autodiscover,
                "config": self._init_config_propagator,
                "runbook": self._init_runbook,
            }

            for service_name in init_order:
                if service_name in initializers:
                    initializers[service_name]()

            logger.info(
                "self_healing.orphan_services_initialized",
                init_order=init_order,
            )

        except Exception as e:
            logger.warning(
                "self_healing.failed_initialize_orphan_services",
                error=e,
            )

    @staticmethod
    def _init_event_journal():
        """317: EventJournal 초기화 — EventBus 구독 등록."""
        try:
            from selfhealing.settings.event_journal import EventJournalSettings

            settings = EventJournalSettings()
            if not settings.enabled:
                logger.debug("self_healing.event_journal_disabled")
                return

            from selfhealing.services.event_journal import init_event_journal

            init_event_journal()
            logger.info("self_healing.event_journal_initialized")
        except ImportError:
            logger.debug("self_healing.event_journal_module_not_available")
        except Exception as e:
            logger.warning("self_healing.failed_init_event_journal", error=e)

    @staticmethod
    def _init_correlation_engine():
        """317: CorrelationEngine 순수 메모리 초기화 (EventBus 구독, 전략 등록)."""
        try:
            from selfhealing.settings.correlation import get_correlation_settings

            settings = get_correlation_settings()
            if not settings.enabled:
                logger.debug("self_healing.correlation_engine_disabled")
                return

            from selfhealing.services.correlation_engine.service import (
                CorrelationEngineService,
            )

            engine = CorrelationEngineService.get_instance()
            engine.initialize()
            logger.info("self_healing.correlation_engine_initialized")
        except ImportError:
            logger.debug("self_healing.correlation_engine_module_not_available")
        except Exception as e:
            logger.warning("self_healing.failed_init_correlation_engine", error=e)

    @staticmethod
    def _init_capacity_reservation():
        """317: CapacityReservationService DI 초기화."""
        try:
            from selfhealing.settings.capacity_reservation import (
                get_capacity_reservation_settings,
            )

            settings = get_capacity_reservation_settings()
            if not settings.enabled:
                logger.debug("self_healing.capacity_reservation_disabled")
                return

            from selfhealing.services.capacity_reservation.service import (
                CapacityReservationService,
            )

            service = CapacityReservationService()
            service.initialize()
            logger.info("self_healing.capacity_reservation_initialized")
        except ImportError:
            logger.debug("self_healing.capacity_reservation_module_not_available")
        except Exception as e:
            logger.warning("self_healing.failed_init_capacity_reservation", error=e)

    @staticmethod
    def _init_saga_autodiscover():
        """317: Saga 정의 자동 등록."""
        try:
            from celery import current_app

            current_app.autodiscover_tasks(["selfhealing.services.saga"])
            logger.info("self_healing.saga_tasks_autodiscovered")
        except ImportError:
            logger.debug("self_healing.saga_autodiscover_skipped_no_celery")
        except Exception as e:
            logger.warning("self_healing.failed_saga_autodiscover", error=e)

    @staticmethod
    def _init_config_propagator():
        """317: GlobalConfigPropagator 초기화."""
        try:
            from selfhealing.services.config.propagator import (
                get_global_config_propagator,
            )

            get_global_config_propagator()
            logger.info("self_healing.config_propagator_initialized")
        except ImportError:
            logger.debug("self_healing.config_propagator_module_not_available")
        except Exception as e:
            logger.warning("self_healing.failed_init_config_propagator", error=e)

    @staticmethod
    def _init_runbook():
        """317: Runbook 시스템 명시적 초기화."""
        try:
            from selfhealing.settings.runbook import get_runbook_settings

            settings = get_runbook_settings()
            if not settings.enabled:
                logger.debug("self_healing.runbook_disabled")
                return

            from selfhealing.services.runbook.service import (
                initialize_runbook_system,
            )

            initialize_runbook_system()
            logger.info("self_healing.runbook_system_initialized")
        except ImportError:
            logger.debug("self_healing.runbook_module_not_available")
        except Exception as e:
            logger.warning("self_healing.failed_init_runbook", error=e)

    # =========================================================================
    # Fork-Safety: Background Thread Lifecycle (Section 5.2)
    # =========================================================================

    @staticmethod
    def _should_start_background_threads() -> bool:
        """Determine if background threads should start in this process.

        Returns True when running in a dev server (manage.py runserver) or
        when this process is a Gunicorn worker (GUNICORN_WORKER env set by
        post_worker_init hook). Returns False in Gunicorn Master to prevent
        spawning threads that die after fork().
        """
        is_gunicorn_worker = os.environ.get("GUNICORN_WORKER") == "1"
        is_gunicorn_master = (
            "gunicorn" in os.environ.get("SERVER_SOFTWARE", "")
            and not is_gunicorn_worker
        )

        if is_gunicorn_master:
            logger.info(
                "self_healing.skipping_background_threads_gunicorn_master",
            )
            return False

        # After the guard above, is_gunicorn_master is always False here.
        return True

    def _start_all_background_threads(self):
        """Start all background threads (gauge hydration, cache, metrics, watchdog)."""
        self._schedule_gauge_hydration()
        self._start_precomputed_cache_worker()
        self._start_system_metrics_cache()
        self._start_meta_watchdog()
        self._start_correlation_engine_loop()
        self._start_capacity_reservation()

    @classmethod
    def start_background_threads(cls):
        """Public entry point for Gunicorn post_worker_init hook.

        Resets duplicate-start guards so threads can be started fresh
        in each forked Worker, then starts all background threads.
        """
        cls._reset_all_background_state()

        try:
            from django.apps import apps

            app_config = apps.get_app_config("selfhealing")
            app_config._start_all_background_threads()
        except Exception as exc:
            logger.warning(
                "self_healing.background_thread_startup_failed",
                error=exc,
            )

    @classmethod
    def _reset_all_background_state(cls):
        """Reset all duplicate-start guards for a fresh Worker."""
        with cls._hydration_lock:
            cls._hydration_done = False
        with cls._cache_worker_lock:
            cls._cache_worker_started = False
        with cls._metrics_cache_lock:
            cls._metrics_cache_started = False
        with cls._meta_watchdog_lock:
            cls._meta_watchdog_started = False
        with cls._correlation_loop_lock:
            cls._correlation_loop_started = False
        with cls._capacity_reservation_lock:
            cls._capacity_reservation_started = False

    # =========================================================================
    # Test Helpers
    # =========================================================================

    @classmethod
    def reset_cache_worker_state(cls):
        """
        Pre-computed cache worker 상태 리셋 (테스트용).

        단위 테스트에서 중복 실행 방지 플래그를 리셋합니다.
        """
        with cls._cache_worker_lock:
            cls._cache_worker_started = False

    @classmethod
    def reset_hydration_state(cls):
        """
        Hydration 상태 리셋 (테스트용).

        단위 테스트에서 중복 실행 방지 플래그를 리셋합니다.
        """
        with cls._hydration_lock:
            cls._hydration_done = False

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

import logging
import random
import threading
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

if TYPE_CHECKING:
    from typing import List

logger = logging.getLogger(__name__)

# RBAC group definitions
SELFHEALING_GROUPS: List[str] = [
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
            logger.info(f"[SelfHealing] RBAC groups created: {created_groups}")

        if existing_groups and created_groups:
            logger.debug(f"[SelfHealing] RBAC groups already existed: {existing_groups}")

    except Exception as e:
        # Best-effort: 실패해도 시스템은 시작
        logger.warning(f"[SelfHealing] Failed to create RBAC groups: {e}")


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

        # Log environment variable snapshot (환경변수 Audit)
        # This runs on every server start because env vars can change
        # between restarts (e.g., Docker container restart with new env)
        self._log_env_snapshot()

        # Sync hash chain state (Redis ↔ Local file)
        # Ensures consistency after server restarts or Redis recovery
        self._sync_hash_chain_on_startup()

        # Validate config with Safe Defaults (Fail-Safe Default 강화)
        self._validate_startup_config()

        # Hydrate metric gauges with jitter (Startup Hydration)
        self._schedule_gauge_hydration()

        # V3: Start pre-computed cache worker for L3 observability endpoints
        self._start_precomputed_cache_worker()

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
            logger.debug("[SelfHealing] env_snapshot module not available")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(f"[SelfHealing] Failed to log env snapshot: {e}")

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
                logger.debug("[SelfHealing] Distributed hash chain not enabled, skipping sync")
                return

            from pathlib import Path
            from selfhealing.audit.integrity import StartupHashChainSync

            # Get Redis client
            redis_client = self._get_redis_client_for_hash_chain()
            if redis_client is None:
                logger.debug("[SelfHealing] Redis client not available for hash chain sync")
                return

            # Get log directory from settings
            log_dir = Path(getattr(settings, "SELFHEALING_AUDIT_LOG_DIR", "logs/audit"))

            # Perform sync
            sync = StartupHashChainSync(
                redis_client=redis_client,
                log_dir=log_dir,
                key_prefix=getattr(settings, "SELFHEALING_REDIS_KEY_PREFIX", "selfhealing:"),
            )
            result = sync.sync()

            # Log result
            if result.get("status") == "success":
                action = result.get("action", "none")
                pending_cleaned = result.get("pending_cleaned", 0)

                if action == "synced_redis_to_file":
                    logger.warning(
                        f"[SelfHealing] Hash chain sync: Redis was behind, "
                        f"synced to file (seq {result.get('file_sequence')})"
                    )
                elif action == "fresh_start":
                    logger.info("[SelfHealing] Hash chain sync: Fresh start (no prior state)")
                else:
                    logger.info(f"[SelfHealing] Hash chain sync: {action}")

                if pending_cleaned > 0:
                    logger.info(
                        f"[SelfHealing] Hash chain sync: Cleaned {pending_cleaned} "
                        "pending sequences from previous crash"
                    )
            else:
                logger.warning(
                    f"[SelfHealing] Hash chain sync failed: {result.get('error', 'unknown')}"
                )

        except ImportError:
            logger.debug("[SelfHealing] integrity module not available for hash chain sync")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(f"[SelfHealing] Failed to sync hash chain on startup: {e}")

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
                from selfhealing.adapters.resilient.backend import ResilientStorageBackend

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
                validate_startup_config,
                FatalConfigError,
                ENABLE_QUARANTINE_ON_FATAL,
            )

            try:
                changes = validate_startup_config(log_changes=True, raise_on_fatal=False)

                if changes > 0:
                    logger.info(
                        f"[SelfHealing] Startup config validation: "
                        f"applied {changes} safe default(s)"
                    )
                else:
                    logger.debug("[SelfHealing] Startup config validation: all settings valid")

            except FatalConfigError as e:
                # Fatal 설정 위반 시 Quarantine Mode 활성화
                if ENABLE_QUARANTINE_ON_FATAL:
                    self._activate_quarantine_mode(e)
                else:
                    logger.critical(
                        f"[SelfHealing] Fatal config error (Quarantine disabled): {e}"
                    )

        except ImportError:
            logger.debug("[SelfHealing] safe_defaults module not available")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(f"[SelfHealing] Failed to validate startup config: {e}")

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
                GracefulDegradationManager,
                EmergencyLevel,
            )

            manager = GracefulDegradationManager()

            # Quarantine Mode (LEVEL_3) 활성화
            manager.activate(
                level=EmergencyLevel.LEVEL_3,
                reason=f"Config Quarantine: {str(error)[:200]}",
                activated_by="system:config_validation",
                ttl_seconds=None,  # 무기한 (수동 해제 필요)
            )

            logger.critical(
                "[QUARANTINE] System started in Quarantine Mode (LEVEL_3) "
                "due to fatal config violations. Manual intervention required."
            )

        except ImportError:
            logger.warning("[SelfHealing] emergency_mode module not available for Quarantine")
        except Exception as e:
            logger.error(f"[SelfHealing] Failed to activate Quarantine Mode: {e}")

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
            logger.debug("[SelfHealing] Gauge hydration disabled by settings")
            return False

        # 중복 실행 방지
        with self._hydration_lock:
            if self._hydration_done:
                logger.debug("[SelfHealing] Gauge hydration already scheduled")
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
            f"[SelfHealing] Gauge hydration scheduled in {jitter:.1f}s "
            f"(max_jitter={jitter_max}s)"
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
                f"[SelfHealing] Gauge hydration completed: "
                f"dlq_domains={len(result.dlq_pending)}, "
                f"cb_services={len(result.circuit_breaker_states)}"
            )

        except ImportError:
            logger.debug("[SelfHealing] reconciler module not available")
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                f"[SelfHealing] Gauge hydration failed (non-fatal): {e}. "
                f"Gauges will be updated on next event or manual sync."
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
            logger.debug("[SelfHealing] Pre-computed cache disabled by settings")
            return

        # 중복 실행 방지
        with self._cache_worker_lock:
            if self._cache_worker_started:
                logger.debug("[SelfHealing] Pre-computed cache worker already started")
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

            logger.info(
                "[SelfHealing] Pre-computed cache worker started "
                "(L3 observability endpoints: health, error-budget, pool-status)"
            )

        except ImportError:
            logger.debug("[SelfHealing] precomputed_cache module not available")
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                f"[SelfHealing] Failed to start pre-computed cache worker (non-fatal): {e}. "
                f"L3 endpoints will compute on-demand."
            )

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

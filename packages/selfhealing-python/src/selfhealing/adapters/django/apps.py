"""
Django App Configuration for Self-Healing.

This allows the selfhealing.adapters.django module to be used
as a Django app in INSTALLED_APPS.

Lifecycle Hooks:
    1. ready() - Called on every server start
       - Environment variable snapshot logging (audit trail)
       - Metric Gauge hydration (Phase 2: Startup Hydration)
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

Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md (Phase 2)
"""

import logging
import random
import threading

from django.apps import AppConfig
from django.conf import settings
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)

# RBAC group definitions
SELFHEALING_GROUPS = [
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
            f"[SelfHealing] RBAC groups created: {created_groups}"
        )
    
    if existing_groups and created_groups:
        logger.debug(
            f"[SelfHealing] RBAC groups already existed: {existing_groups}"
        )


class SelfHealingConfig(AppConfig):
    """Django app configuration for self-healing."""

    name = "selfhealing.adapters.django"
    label = "selfhealing"
    verbose_name = "Self-Healing System"
    default_auto_field = "django.db.models.BigAutoField"
    
    # Phase 2: Startup Hydration - 중복 실행 방지
    _hydration_done = False
    _hydration_lock = threading.Lock()

    def ready(self):
        """
        Called when the app is ready (every server start).
        
        Responsibilities:
        1. Register admin classes
        2. Connect post_migrate signal for RBAC group creation
        3. Log environment variable snapshot for audit trail
        4. Validate config with Safe Defaults (Phase 6)
        5. Hydrate metric gauges with jitter (Phase 2)
        
        Note: Environment snapshot is logged here (not in post_migrate) because
        env vars can change on every restart, not just during migrations.
        This aligns with 12-Factor App principles and Spring Boot patterns.
        
        Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
        """
        # Import admin to register admin classes
        try:
            from selfhealing.adapters.django import admin  # noqa: F401
        except ImportError:
            pass
        
        # Connect post_migrate signal for RBAC group creation
        # sender=self ensures it only runs when this app's migrations complete
        post_migrate.connect(
            create_selfhealing_groups,
            sender=self,
            dispatch_uid="selfhealing_create_rbac_groups",
        )
        
        # Log environment variable snapshot (Phase 2: 환경변수 Audit)
        # This runs on every server start because env vars can change
        # between restarts (e.g., Docker container restart with new env)
        self._log_env_snapshot()
        
        # Validate config with Safe Defaults (Phase 6: Fail-Safe Default 강화)
        # Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
        self._validate_startup_config()
        
        # Hydrate metric gauges with jitter (Phase 2: Startup Hydration)
        # Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
        self._schedule_gauge_hydration()
    
    def _log_env_snapshot(self):
        """
        Log environment variable snapshot for audit trail.
        
        Best-effort: If logging fails, system continues normally.
        This is critical for compliance (Big 4 audit requirements).
        """
        try:
            from selfhealing.audit.env_snapshot import log_env_snapshot_to_audit
            log_env_snapshot_to_audit()
        except ImportError:
            logger.debug("[SelfHealing] env_snapshot module not available")
        except Exception as e:
            # Best-effort: 실패해도 시스템은 시작
            logger.warning(f"[SelfHealing] Failed to log env snapshot: {e}")

    def _validate_startup_config(self):
        """
        Validate config with Safe Defaults on startup.
        
        Phase 6: Fail-Safe Default 강화
        - Non-fatal 설정: Safe Default 적용 후 계속 운영
        - Fatal 설정 위반: Quarantine Mode (LEVEL_3) 활성화
        
        Reference: docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART2.md
        """
        try:
            from selfhealing.core.safe_defaults import (
                validate_startup_config,
                FatalConfigError,
                ENABLE_QUARANTINE_ON_FATAL,
            )
            from selfhealing.adapters.django.config_provider import get_config
            
            config = get_config()
            
            try:
                changes = validate_startup_config(config, log_changes=True, raise_on_fatal=False)
                
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
                f"[QUARANTINE] System started in Quarantine Mode (LEVEL_3) "
                f"due to fatal config violations. Manual intervention required."
            )
            
        except ImportError:
            logger.warning("[SelfHealing] emergency_mode module not available for Quarantine")
        except Exception as e:
            logger.error(f"[SelfHealing] Failed to activate Quarantine Mode: {e}")

    # =========================================================================
    # Phase 2: Startup Hydration
    # Reference: docs/self_healing/18_METRIC_DRIFT_STRATEGY.md
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
        
        DB에서 실제 값을 조회하여 Prometheus Gauge를 초기화합니다.
        
        Graceful Degradation:
        - 실패해도 서버 기동은 계속
        - 경고 로그만 남기고 정상 운영
        """
        try:
            # DB 연결 확인
            from django.db import connection
            connection.ensure_connection()
            
            # Reconciler를 통해 Gauge 동기화
            from selfhealing.metrics.reconciler import get_reconciler
            
            reconciler = get_reconciler()
            result = reconciler.sync_all_gauges()
            
            logger.info(
                f"[SelfHealing] Gauge hydration completed: "
                f"dlq_domains={len(result.dlq_pending)}, "
                f"cb_services={len(result.circuit_breaker_states)}"
            )
            
        except Exception as e:
            # Graceful Degradation: 실패해도 서버 기동은 계속
            logger.warning(
                f"[SelfHealing] Gauge hydration failed (non-fatal): {e}. "
                f"Gauges will be updated on next event or manual sync."
            )

    @classmethod
    def reset_hydration_state(cls):
        """
        Hydration 상태 리셋 (테스트용).
        
        단위 테스트에서 중복 실행 방지 플래그를 리셋합니다.
        """
        with cls._hydration_lock:
            cls._hydration_done = False

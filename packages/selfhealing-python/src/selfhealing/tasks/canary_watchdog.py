"""
Canary Rollout Watchdog Tasks.

Zombie 롤아웃 감지 및 정리, 자동 프로모션/롤백 처리.

Tasks:
    - scan_zombie_rollouts: 정체된 롤아웃 감지 및 알림/자동 롤백
    - auto_promote_eligible: 조건을 충족한 롤아웃 자동 프로모션
    - collect_canary_metrics: 활성 롤아웃의 메트릭 수집 및 건강도 평가

Thin Task, Fat Service 원칙:
- 이 파일의 함수들은 단순 위임자 역할만 수행
- 모든 비즈니스 로직은 RolloutWatchdog 클래스에서 처리

Reference:
    docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Celery Beat 설정 예시:
    CELERY_BEAT_SCHEDULE = {
        'scan-zombie-rollouts': {
            'task': 'selfhealing.tasks.canary_watchdog.scan_zombie_rollouts',
            'schedule': crontab(minute='*/5'),  # 5분마다
        },
        'auto-promote-eligible': {
            'task': 'selfhealing.tasks.canary_watchdog.auto_promote_eligible',
            'schedule': crontab(minute='*/1'),  # 1분마다
        },
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from selfhealing.utils.time import utc_now

if TYPE_CHECKING:
    from selfhealing.services.canary import CanaryRollout
    from selfhealing.settings.canary_watchdog import CanaryWatchdogSettings

logger = structlog.get_logger()


# =============================================================================
# Watchdog Configuration
# =============================================================================


@dataclass
class CanaryWatchdogConfig:
    """
    Watchdog 설정.

    Attributes:
        zombie_threshold_minutes: 정체로 간주하는 시간 (기본 30분)
        auto_rollback_after_minutes: 자동 롤백까지 대기 시간 (기본 60분)
        max_stage_duration_minutes: 단계별 최대 체류 시간 (기본 15분)
        enable_auto_promote: 자동 프로모션 활성화
        enable_auto_rollback: Zombie 자동 롤백 활성화
        notification_enabled: Slack 알림 활성화
        slack_channel: 알림 Slack 채널
    """

    zombie_threshold_minutes: int = 30
    auto_rollback_after_minutes: int = 60
    max_stage_duration_minutes: int = 15
    enable_auto_promote: bool = True
    enable_auto_rollback: bool = True
    notification_enabled: bool = True
    slack_channel: str = "#selfhealing-alerts"

    @classmethod
    def from_settings(
        cls,
        settings: CanaryWatchdogSettings | None = None,
        **overrides,
    ) -> CanaryWatchdogConfig:
        """
        Settings에서 CanaryWatchdogConfig 인스턴스 생성.

        Args:
            settings: CanaryWatchdogSettings 인스턴스 (없으면 싱글톤 사용)
            **overrides: 개별 필드 오버라이드

        Returns:
            CanaryWatchdogConfig: Settings 기반 인스턴스
        """
        from selfhealing.settings.canary_watchdog import get_canary_watchdog_settings

        s = settings or get_canary_watchdog_settings()
        return cls(
            zombie_threshold_minutes=overrides.get("zombie_threshold_minutes", s.zombie_threshold_minutes),
            auto_rollback_after_minutes=overrides.get("auto_rollback_after_minutes", s.auto_rollback_after_minutes),
            max_stage_duration_minutes=overrides.get("max_stage_duration_minutes", s.max_stage_duration_minutes),
            enable_auto_promote=overrides.get("enable_auto_promote", s.enable_auto_promote),
            enable_auto_rollback=overrides.get("enable_auto_rollback", s.enable_auto_rollback),
            notification_enabled=overrides.get("notification_enabled", s.notification_enabled),
            slack_channel=overrides.get("slack_channel", s.slack_channel),
        )


@dataclass
class ZombieRollout:
    """
    Zombie 롤아웃 정보.

    정체 시간이 임계값을 초과한 롤아웃.
    """

    rollout_id: str
    config_type: str
    state: str
    stuck_since: datetime
    stuck_minutes: float
    created_by: str
    affected_clusters: list[str]
    reason: str = ""
    action_taken: str = ""  # "notified", "auto_rolled_back", "none"


@dataclass
class WatchdogResult:
    """
    Watchdog 실행 결과.

    Attributes:
        success: 성공 여부
        scanned_count: 검사한 롤아웃 수
        zombie_count: 감지된 Zombie 수
        rollback_count: 자동 롤백된 수
        promote_count: 자동 프로모션된 수
        governance_blocked: 거버넌스에 의해 차단됨
        governance_block_reason: 차단 사유
        zombies: Zombie 롤아웃 목록
        errors: 에러 목록
    """

    success: bool = True
    scanned_count: int = 0
    zombie_count: int = 0
    rollback_count: int = 0
    promote_count: int = 0
    governance_blocked: bool = False
    governance_block_reason: str = ""
    zombies: list[ZombieRollout] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "success": self.success,
            "scanned_count": self.scanned_count,
            "zombie_count": self.zombie_count,
            "rollback_count": self.rollback_count,
            "promote_count": self.promote_count,
            "governance_blocked": self.governance_blocked,
            "governance_block_reason": self.governance_block_reason,
            "zombies": [
                {
                    "rollout_id": z.rollout_id,
                    "config_type": z.config_type,
                    "state": z.state,
                    "stuck_minutes": z.stuck_minutes,
                    "action_taken": z.action_taken,
                }
                for z in self.zombies
            ],
            "errors": self.errors,
        }


# =============================================================================
# RolloutWatchdog Service
# =============================================================================


class RolloutWatchdog:
    """
    Canary Rollout Watchdog.

    정체된(Zombie) 롤아웃을 감지하고 자동 조치를 수행합니다.

    Zombie 판정 기준:
    - CANARY 상태에서 stage.duration_minutes의 2배 이상 경과
    - PAUSED 상태에서 zombie_threshold_minutes 이상 경과
    - PROMOTING 상태에서 5분 이상 경과 (전이 실패)

    자동 조치:
    1. zombie_threshold 도달: Slack 알림
    2. auto_rollback_after 도달: 자동 롤백 + 알림

    Example:
        watchdog = RolloutWatchdog()
        result = watchdog.scan_and_handle()

        if result.zombie_count > 0:
            print(f"Found {result.zombie_count} zombie rollouts")
    """

    def __init__(self, config: CanaryWatchdogConfig = None):
        """
        RolloutWatchdog 초기화.

        Args:
            config: Watchdog 설정 (기본값 사용 시 None)
        """
        self.config = config or CanaryWatchdogConfig()
        self._service = None

    @property
    def service(self):
        """CanaryRolloutService (Lazy loading)."""
        if self._service is None:
            from selfhealing.services.canary import get_canary_rollout_service

            self._service = get_canary_rollout_service()
        return self._service

    def scan_and_handle(self) -> WatchdogResult:
        """
        Zombie 롤아웃 스캔 및 처리.

        Returns:
            WatchdogResult: 스캔 및 처리 결과
        """
        result = WatchdogResult()

        try:
            active_rollouts = self.service.get_active_rollouts()
            result.scanned_count = len(active_rollouts)

            now = utc_now()

            for rollout in active_rollouts:
                zombie = self._check_zombie(rollout, now)
                if zombie:
                    result.zombies.append(zombie)
                    result.zombie_count += 1

                    # 자동 조치 수행
                    action = self._handle_zombie(zombie, rollout)
                    zombie.action_taken = action

                    if action == "auto_rolled_back":
                        result.rollback_count += 1

            logger.info(
                "watchdog.scan_complete",
                result=result.scanned_count,
                zombie_count=result.zombie_count,
                rollback_count=result.rollback_count,
            )

        except Exception as e:
            logger.exception("watchdog")
            result.success = False
            result.errors.append(str(e))

        return result

    def _check_zombie(
        self,
        rollout: CanaryRollout,
        now: datetime,
    ) -> ZombieRollout | None:
        """
        롤아웃이 Zombie인지 확인.

        Args:
            rollout: 확인할 롤아웃
            now: 현재 시간

        Returns:
            ZombieRollout 또는 None
        """
        from selfhealing.services.canary import CanaryState
        from selfhealing.services.canary.models import ZOMBIE_EXEMPT_TRIGGERS

        # 상태별 체류 시간 계산
        stage = rollout.current_stage
        stage_duration = stage.duration_minutes if stage else 5

        # 마지막 상태 변경 시간 추정 (created_at 기준)
        stuck_since = rollout.created_at
        stuck_minutes = (now - stuck_since).total_seconds() / 60

        # Zombie 판정
        is_zombie = False
        reason = ""

        if rollout.state == CanaryState.CANARY:
            # CANARY 상태: duration의 2배 초과
            threshold = stage_duration * 2
            if stuck_minutes > threshold:
                is_zombie = True
                reason = f"Stuck in CANARY for {stuck_minutes:.1f} min (threshold: {threshold})"

        elif rollout.state == CanaryState.PAUSED:
            # Error Budget/Governance로 인한 PAUSED는 정상적인 대기 상태이므로 Zombie 제외
            triggered_by = getattr(rollout, "pause_triggered_by", None)

            if triggered_by in ZOMBIE_EXEMPT_TRIGGERS:
                # 정상적인 대기 상태 - Zombie 아님
                logger.debug(
                    "watchdog.rollout_excluded_zombie_check",
                    rollout=rollout.id,
                    triggered_by=triggered_by,
                )
                return None

            # 그 외 PAUSED는 기존 로직 적용
            if stuck_minutes > self.config.zombie_threshold_minutes:
                is_zombie = True
                reason = f"Paused for {stuck_minutes:.1f} min (threshold: {self.config.zombie_threshold_minutes})"

        elif rollout.state == CanaryState.PROMOTING:
            # PROMOTING 상태: 5분 초과 (전이 실패)
            if stuck_minutes > 5:
                is_zombie = True
                reason = "Stuck in PROMOTING state"

        if not is_zombie:
            return None

        return ZombieRollout(
            rollout_id=rollout.id,
            config_type=rollout.config_type,
            state=rollout.state.value,
            stuck_since=stuck_since,
            stuck_minutes=stuck_minutes,
            created_by=rollout.created_by,
            affected_clusters=rollout.affected_clusters,
            reason=reason,
        )

    def _handle_zombie(
        self,
        zombie: ZombieRollout,
        rollout: CanaryRollout,
    ) -> str:
        """
        Zombie 롤아웃 처리.

        Args:
            zombie: Zombie 정보
            rollout: 롤아웃 객체

        Returns:
            수행된 액션 ("notified", "auto_rolled_back", "none")
        """
        # 자동 롤백 조건: auto_rollback_after_minutes 초과
        if self.config.enable_auto_rollback and zombie.stuck_minutes > self.config.auto_rollback_after_minutes:
            try:
                success = self.service.rollback(
                    rollout.id,
                    reason=f"[Watchdog] Auto rollback - {zombie.reason}",
                )
                if success:
                    self._send_notification(zombie, "auto_rolled_back")
                    logger.warning(
                        "watchdog.auto_rolled_back",
                        rollout=rollout.id,
                        zombie=zombie.reason,
                    )
                    return "auto_rolled_back"
            except Exception as e:
                logger.exception(
                    "watchdog.auto_rollback_failed",
                    error=e,
                )
                return "rollback_failed"

        # 알림만 전송
        if self.config.notification_enabled:
            self._send_notification(zombie, "zombie_detected")
            return "notified"

        return "none"

    def _send_notification(self, zombie: ZombieRollout, event_type: str) -> None:
        """
        Slack 알림 전송.

        Args:
            zombie: Zombie 정보
            event_type: 이벤트 유형 ("zombie_detected", "auto_rolled_back")
        """
        try:
            from selfhealing.services.unified_notification import (
                get_notification_service,
            )

            service = get_notification_service()

            if event_type == "zombie_detected":
                message = (
                    f"⚠️ [Canary Watchdog] Zombie Rollout Detected\n"
                    f"• Rollout ID: `{zombie.rollout_id}`\n"
                    f"• Config: {zombie.config_type}\n"
                    f"• State: {zombie.state}\n"
                    f"• Stuck: {zombie.stuck_minutes:.0f} min\n"
                    f"• Reason: {zombie.reason}\n"
                    f"• Created by: {zombie.created_by}\n"
                    f"• Action: Manual intervention required"
                )
            elif event_type == "auto_rolled_back":
                message = (
                    f"🔄 [Canary Watchdog] Auto Rollback Executed\n"
                    f"• Rollout ID: `{zombie.rollout_id}`\n"
                    f"• Config: {zombie.config_type}\n"
                    f"• Stuck: {zombie.stuck_minutes:.0f} min\n"
                    f"• Reason: {zombie.reason}\n"
                    f"• Affected Clusters: {', '.join(zombie.affected_clusters)}\n"
                    f"• Action: Previous config restored"
                )
            else:
                return

            service.send_slack_message(
                message=message,
                channel=self.config.slack_channel,
                severity="warning",
            )

        except Exception as e:
            logger.warning(
                "watchdog.notification_failed",
                error=e,
            )

    def auto_promote_eligible(self) -> WatchdogResult:
        """
        자동 프로모션 조건을 충족한 롤아웃 프로모션.

        조건:
        - auto_promote=True인 단계
        - duration_minutes 경과
        - 메트릭 검증 통과
        - 글로벌 에러 예산 체크 (신규)

        Returns:
            WatchdogResult: 프로모션 결과
        """
        result = WatchdogResult()

        if not self.config.enable_auto_promote:
            return result

        # 글로벌 에러 예산 체크 (Fail-Closed 정책)
        try:
            from selfhealing.services.governance_checks import check_all_governance

            governance = check_all_governance(
                check_kill_switch=True,
                check_emergency=True,
                emergency_min_level=2,  # LEVEL_2+ 시 차단
                check_error_budget=True,
                operation_name="auto_promote_canary",
                service_name="RolloutWatchdog",
                domain="canary",
                audit_on_block=True,
            )

            if not governance.allowed:
                logger.warning(
                    "watchdog.auto_promotion_blocked_governance",
                    governance=governance.block_message,
                )

                # Prometheus 메트릭 기록
                self._record_governance_blocked_metrics(governance)

                result.governance_blocked = True
                result.governance_block_reason = governance.block_message
                return result

        except ImportError:
            logger.debug("watchdog")
            # Fail-Closed: Import 실패 시에도 차단 (보수적 정책)
            result.governance_blocked = True
            result.governance_block_reason = "GovernanceChecks module not available"
            return result
        except Exception as e:
            logger.warning(
                "watchdog.governance_check_failed",
                error=e,
            )
            # Fail-Closed: 에러 시에도 차단 (변경 작업은 보수적으로)
            result.governance_blocked = True
            result.governance_block_reason = f"Governance check error: {e}"
            return result

        try:
            from selfhealing.services.canary import CanaryState

            active_rollouts = self.service.get_active_rollouts()
            result.scanned_count = len(active_rollouts)
            now = utc_now()

            for rollout in active_rollouts:
                if rollout.state != CanaryState.CANARY:
                    continue

                stage = rollout.current_stage
                if not stage or not stage.auto_promote:
                    continue

                # duration 경과 확인
                elapsed = (now - rollout.created_at).total_seconds() / 60
                if elapsed < stage.duration_minutes:
                    continue

                # 메트릭 검증 후 프로모션
                try:
                    success = self.service.promote(rollout.id, force=False)
                    if success:
                        result.promote_count += 1
                        logger.info(
                            "watchdog.auto_promoted_stage",
                            rollout=rollout.id,
                            stage=stage.name,
                        )
                except Exception as e:
                    result.errors.append(f"{rollout.id}: {e}")

        except Exception as e:
            logger.exception("watchdog")
            result.success = False
            result.errors.append(str(e))

        return result

    def _record_governance_blocked_metrics(self, governance) -> None:
        """거버넌스 차단 시 Prometheus 메트릭 기록."""
        try:
            from selfhealing.services.metrics.definitions import (
                canary_governance_blocked_total,
                canary_pending_promotion_gauge,
            )

            block_reason = governance.block_reason.value if governance.block_reason else "unknown"
            canary_governance_blocked_total.labels(block_reason=block_reason).inc()

            pending_count = len(self.service.get_active_rollouts())
            canary_pending_promotion_gauge.labels(reason=block_reason).set(pending_count)
        except Exception as e:
            logger.debug(
                "watchdog.metrics_recording_failed",
                error=e,
            )


# =============================================================================
# Singleton
# =============================================================================


_watchdog: RolloutWatchdog | None = None


def get_rollout_watchdog(config: CanaryWatchdogConfig = None) -> RolloutWatchdog:
    """
    RolloutWatchdog 싱글톤 반환.

    Args:
        config: Watchdog 설정 (첫 호출 시에만 적용)

    Returns:
        RolloutWatchdog 인스턴스
    """
    global _watchdog
    if _watchdog is None:
        _watchdog = RolloutWatchdog(config)
    return _watchdog


def reset_watchdog() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _watchdog
    _watchdog = None


# =============================================================================
# Thin Task Wrappers (Celery Tasks)
# =============================================================================


def scan_zombie_rollouts() -> dict[str, Any]:
    """
    Zombie 롤아웃 스캔 및 처리.

    정체된 Canary 롤아웃을 감지하고:
    1. Slack 알림 전송
    2. 임계값 초과 시 자동 롤백

    Celery Beat 스케줄 권장: 5분마다

    Returns:
        dict: {
            "success": bool,
            "scanned_count": int,
            "zombie_count": int,
            "rollback_count": int,
            "zombies": [...],
        }
    """
    try:
        watchdog = get_rollout_watchdog()
        result = watchdog.scan_and_handle()
        return result.to_dict()

    except Exception as e:
        logger.exception(
            "canary_watchdog.failed",
            error=e,
        )
        raise


def auto_promote_eligible() -> dict[str, Any]:
    """
    자동 프로모션 조건을 충족한 롤아웃 프로모션.

    - auto_promote=True인 단계
    - duration_minutes 경과
    - 메트릭 검증 통과

    Celery Beat 스케줄 권장: 1분마다

    Returns:
        dict: {
            "success": bool,
            "scanned_count": int,
            "promote_count": int,
        }
    """
    try:
        watchdog = get_rollout_watchdog()
        result = watchdog.auto_promote_eligible()
        return result.to_dict()

    except Exception as e:
        logger.exception(
            "canary_watchdog.failed",
            error=e,
        )
        raise


def collect_canary_metrics() -> dict[str, Any]:
    """
    활성 롤아웃의 메트릭 수집.

    TODO: Prometheus/메트릭 시스템 연동 시 구현

    Returns:
        dict: {
            "success": bool,
            "rollout_count": int,
            "metrics_collected": int,
        }
    """
    try:
        from selfhealing.services.canary import get_canary_rollout_service

        service = get_canary_rollout_service()
        active_rollouts = service.get_active_rollouts()

        metrics_count = 0
        for rollout in active_rollouts:
            metrics = service.collect_metrics(rollout.id)
            metrics_count += len(metrics)

        return {
            "success": True,
            "rollout_count": len(active_rollouts),
            "metrics_collected": metrics_count,
        }

    except Exception as e:
        logger.exception(
            "canary_watchdog.failed",
            error=e,
        )
        raise


# =============================================================================
# Celery Beat Schedule
# =============================================================================


def get_canary_watchdog_beat_schedule() -> dict[str, Any]:
    """
    Canary Watchdog Celery Beat 스케줄 설정.

    Returns:
        Dict[str, Any]: Celery Beat 스케줄 설정

    Usage:
        from selfhealing.tasks.canary_watchdog import get_canary_watchdog_beat_schedule

        CELERY_BEAT_SCHEDULE = {
            **get_canary_watchdog_beat_schedule(),
        }
    """
    from celery.schedules import crontab

    return {
        # Zombie 롤아웃 스캔 (5분마다)
        "canary-scan-zombie-rollouts": {
            "task": "selfhealing.tasks.canary_watchdog.scan_zombie_rollouts",
            "schedule": crontab(minute="*/5"),
            "options": {
                "queue": "maintenance",
                "expires": 240,  # 4분 내 처리 안되면 만료
            },
        },
        # 자동 프로모션 체크 (1분마다)
        "canary-auto-promote-eligible": {
            "task": "selfhealing.tasks.canary_watchdog.auto_promote_eligible",
            "schedule": crontab(minute="*/1"),
            "options": {
                "queue": "realtime",
                "expires": 50,  # 50초 내 처리 안되면 만료
            },
        },
        # 메트릭 수집 (2분마다)
        "canary-collect-metrics": {
            "task": "selfhealing.tasks.canary_watchdog.collect_canary_metrics",
            "schedule": crontab(minute="*/2"),
            "options": {
                "queue": "metrics",
                "expires": 90,
            },
        },
    }

"""
AdaptiveThrottle DLQ Replay 연동 모듈.

Throttle 거부 요청을 DLQ에 저장하고, Recovery 시 자동 Replay하는 기능 제공.

주요 기능:
- Throttle 거부 시 DLQ 저장 (Hedging 필터, tier_id 샘플링, trace_id 보존)
- Recovery 이벤트 수신 시 자동 Replay 트리거
- Adaptive Pacing: capacity_ratio 기반 간격/배치 동적 조정
- Death Spiral 방지: get_replayable_entries() + can_retry 가드
"""

from __future__ import annotations

import random
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from selfhealing.services.dlq import DLQService
    from selfhealing.services.throttle.config import ThrottleConfig

logger = structlog.get_logger()


# =============================================================================
# Prometheus 메트릭 (Fail-Open: import 실패 시 기록 생략)
# =============================================================================

_DLQ_METRICS_AVAILABLE = False
_throttle_rejection_dlq_stored_total = None
_throttle_rejection_sampled_out_total = None
_throttle_rejection_hedged_skipped_total = None
_throttle_recovery_replay_total = None
_throttle_replay_adaptive_interval_ms = None
_throttle_replay_permanently_failed_total = None

try:
    from selfhealing.services.metrics.definitions import (
        throttle_recovery_replay_total as _throttle_recovery_replay_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_rejection_dlq_stored_total as _throttle_rejection_dlq_stored_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_rejection_hedged_skipped_total as _throttle_rejection_hedged_skipped_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_rejection_sampled_out_total as _throttle_rejection_sampled_out_total,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_replay_adaptive_interval_ms as _throttle_replay_adaptive_interval_ms,
    )
    from selfhealing.services.metrics.definitions import (
        throttle_replay_permanently_failed_total as _throttle_replay_permanently_failed_total,
    )

    _DLQ_METRICS_AVAILABLE = True
except ImportError:
    pass


def _record_dlq_replay_metric(
    metric,
    labels: dict[str, str],
    value: float | None = None,
) -> None:
    """Prometheus 메트릭 기록 (Fail-Open). Counter는 inc, Gauge는 set."""
    if not _DLQ_METRICS_AVAILABLE or metric is None:
        return
    try:
        if value is not None:
            metric.labels(**labels).set(value)
        else:
            metric.labels(**labels).inc()
    except Exception:
        pass


class ThrottleDLQReplayMixin:
    """
    AdaptiveThrottle에 DLQ 거부 저장 및 Recovery Replay 기능을 추가하는 Mixin.

    사용법:
        AdaptiveThrottle 클래스에서 이 Mixin의 메서드를 호출하여
        거부 요청을 DLQ에 저장하고, Recovery 시 자동 Replay를 수행.
    """

    def _init_dlq_replay_integration(self) -> None:
        """
        DLQ Replay 연동 초기화.

        DLQ 서비스 로딩 및 Recovery 이벤트 구독.
        Fail-Open: DLQ 불가 시에도 Throttle 본연의 기능에 영향 없음.
        """
        self._dlq_service: DLQService | None = None
        self._dlq_replay_enabled: bool = getattr(self.config, "dlq_on_rejection", True)
        self._auto_replay_on_recovery: bool = getattr(self.config, "auto_replay_on_recovery", True)
        self._replay_batch_size: int = getattr(self.config, "replay_batch_size", 10)
        self._replay_interval_ms: int = getattr(self.config, "replay_interval_ms", 100)
        self._replay_min_recovery_percent: float = getattr(self.config, "replay_min_recovery_percent", 50.0)

        if self._dlq_replay_enabled:
            self._load_dlq_service()

        if self._auto_replay_on_recovery:
            self._subscribe_recovery_for_dlq_replay()

    def _load_dlq_service(self) -> None:
        """DLQ 서비스 로드 (Fail-Open: import 실패 시 None)."""
        try:
            from selfhealing.services.dlq import get_dlq_service

            self._dlq_service = get_dlq_service()
        except Exception:
            self._dlq_service = None
            logger.debug("adaptive_throttle.dlq_service_available_rejection")

    # =========================================================================
    # Throttle 거부 시 DLQ 저장
    # =========================================================================

    def store_throttle_rejection_to_dlq(
        self,
        context: dict[str, Any],
        rejection_reason: str,
    ) -> None:
        """
        Throttle 거부된 요청을 DLQ에 저장.

        필터링 순서:
        1. Hedging 보조 요청 필터 (hedged=True → 저장 스킵)
        2. tier_id 기반 샘플링 (critical=100%, standard=sampling_rate, non_essential=스킵)

        저장 시 metadata에 throttle_state, original_trace_id, tier_id 포함.

        Args:
            context: 요청 컨텍스트 (domain, tier_id, hedged, trace_id 등)
            rejection_reason: 거부 사유 (full_stop, emergency_level_N, capacity_exceeded 등)
        """
        if not self._dlq_service:
            return

        # Hedging 보조 요청 필터 (HedgingResult.hedged=True인 보조 요청 제외)
        if context.get("hedged", False):
            logger.debug("adaptive_throttle.skipping_dlq_store_hedged")
            _record_dlq_replay_metric(
                _throttle_rejection_hedged_skipped_total,
                {"domain": context.get("domain", "throttle_rejection")},
            )
            return

        # tier_id 기반 샘플링
        tier_id = context.get("tier_id", "standard")

        if tier_id == "non_essential":
            logger.debug("adaptive_throttle.skipping_dlq_store_tier")
            _record_dlq_replay_metric(
                _throttle_rejection_sampled_out_total,
                {"tier_id": "non_essential", "reason": "non_essential"},
            )
            return

        if tier_id == "standard":
            sampling_rate = getattr(self.config, "dlq_store_sampling_rate", 1.0)
            if random.random() > sampling_rate:
                logger.debug(
                    "adaptive_throttle.sampled_out_dlq_store",
                    sampling_rate=sampling_rate,
                )
                _record_dlq_replay_metric(
                    _throttle_rejection_sampled_out_total,
                    {"tier_id": "standard", "reason": "sampling_rate"},
                )
                return

        # DLQ 저장 실행
        try:
            self._dlq_service.store_failure(
                domain=context.get("domain", "throttle_rejection"),
                failure_type="throttle_rejected",
                entity_type=context.get("entity_type"),
                entity_id=context.get("entity_id"),
                error_code=f"THROTTLE_{rejection_reason.upper()}",
                error_message=f"Request rejected by AdaptiveThrottle: {rejection_reason}",
                request_data=context.get("request_data", {}),
                metadata={
                    "throttle_state": {
                        "current_limit": self._current_limit,
                        "initial_limit": self.config.initial_limit,
                        "emergency_level": self._emergency_level,
                        "full_stop_active": self._full_stop_active,
                        "rejection_reason": rejection_reason,
                    },
                    "original_trace_id": context.get("trace_id"),
                    "tier_id": tier_id,
                    "request_id": context.get("request_id"),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                recommended_action="auto_replay",
            )

            # 메트릭 기록: DLQ 저장 성공
            _record_dlq_replay_metric(
                _throttle_rejection_dlq_stored_total,
                {"reason": rejection_reason, "domain": context.get("domain", "throttle_rejection")},
            )

            # EventBus 이벤트 발행
            self._emit_rejection_stored_event(
                entry_domain=context.get("domain", "throttle_rejection"),
                rejection_reason=rejection_reason,
                tier_id=tier_id,
            )

        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_store_rejection_dlq",
                error=e,
            )

    def _emit_rejection_stored_event(
        self,
        entry_domain: str,
        rejection_reason: str,
        tier_id: str,
    ) -> None:
        """Throttle 거부 DLQ 저장 이벤트 발행 (Fail-Open)."""
        try:
            from selfhealing.services.throttle.adaptive import _emit_throttle_event

            _emit_throttle_event(
                "THROTTLE_REJECTION_STORED",
                {
                    "domain": entry_domain,
                    "reason": rejection_reason,
                    "tier_id": tier_id,
                },
            )
        except Exception:
            pass

    def get_rejection_reason(self) -> str:
        """현재 Throttle 상태 기반 거부 사유 결정."""
        if self._full_stop_active:
            return "full_stop"
        if self._emergency_level >= 3:
            return f"emergency_level_{self._emergency_level}"
        if self._current_limit <= 0:
            return "limit_exhausted"
        return "capacity_exceeded"

    # =========================================================================
    # Recovery 시 자동 Replay
    # =========================================================================

    def _subscribe_recovery_for_dlq_replay(self) -> None:
        """THROTTLE_LIMIT_RECOVERED 이벤트 구독하여 자동 DLQ Replay 트리거."""
        try:
            from selfhealing.services.event_bus import EventType, get_event_bus

            bus = get_event_bus()
            bus.subscribe(
                EventType.THROTTLE_LIMIT_RECOVERED,
                self._on_recovery_trigger_dlq_replay,
            )
            logger.info("adaptive_throttle.subscribed_dlq_auto_replay")
        except ImportError:
            logger.debug("adaptive_throttle.eventbus_available_auto_replay")
        except Exception as e:
            logger.warning(
                "adaptive_throttle.failed_subscribe_recovery_replay",
                error=e,
            )

    def _on_recovery_trigger_dlq_replay(self, event) -> None:
        """
        Recovery 이벤트 수신 시 DLQ Replay 트리거.

        replay_min_recovery_percent 미만이면 Replay 스킵.
        데몬 스레드에서 비동기로 실행하여 EventBus 블록을 방지.
        """
        if not self._dlq_service:
            return

        event_data = event.data if hasattr(event, "data") else event
        previous_limit = event_data.get("previous_limit", 0)
        new_limit = event_data.get("new_limit", 0)

        # Recovery percent 계산 (initial_limit 기준)
        if self.config.initial_limit > 0:
            recovery_percent = (new_limit / self.config.initial_limit) * 100
        else:
            recovery_percent = 0

        if recovery_percent < self._replay_min_recovery_percent:
            logger.debug(
                "adaptive_throttle.recovery_skipping_dlq_replay",
                recovery_percent=recovery_percent,
                self=self._replay_min_recovery_percent,
            )
            return

        # 비동기 Replay (EventBus 핸들러 블록 방지)
        thread = threading.Thread(
            target=self._execute_dlq_replay_on_recovery,
            kwargs={"recovery_percent": recovery_percent},
            daemon=True,
        )
        thread.start()

    def _execute_dlq_replay_on_recovery(self, recovery_percent: float) -> None:
        """
        Throttle Recovery 후 DLQ Replay 실행.

        get_replayable_entries()로 retry_count < max_retries 엔트리만 조회.
        Throttle 건강 상태 확인 및 Adaptive Pacing 적용.
        """
        if not self._dlq_service:
            return

        try:
            from selfhealing.services.throttle.adaptive import _emit_throttle_event

            _emit_throttle_event(
                "THROTTLE_REJECTION_REPLAY_STARTED",
                {"recovery_percent": recovery_percent},
            )
        except Exception:
            pass

        # get_replayable_entries() 사용 (retry_count < max_retries 자동 필터)
        pending_entries = self._dlq_service.get_replayable_entries(
            domain="throttle_rejection",
            limit=self._replay_batch_size,
        )

        if not pending_entries:
            return

        logger.info(
            "adaptive_throttle.starting_dlq_replay_entries",
            count=len(pending_entries),
            recovery_percent=recovery_percent,
        )

        replayed = 0
        failed = 0

        for entry in pending_entries:
            # Throttle 건강 상태 재확인
            if not self._is_healthy_for_dlq_replay():
                logger.warning(
                    "adaptive_throttle.throttle_health_degraded_pausing",
                    replayed=replayed,
                )
                break

            # can_retry 소진 확인 (FailedOperationData.can_retry)
            if not entry.can_retry:
                logger.warning(
                    "adaptive_throttle.entry_exhausted_retries_marking",
                    entry=entry.id,
                    entry_1=entry.retry_count,
                    entry_2=entry.max_retries,
                )
                try:
                    self._dlq_service.resolve_entry(entry.id, notes="permanently_failed")
                except Exception:
                    pass
                _record_dlq_replay_metric(
                    _throttle_replay_permanently_failed_total,
                    {"domain": entry.domain},
                )
                failed += 1
                continue

            # Replay 실행 (replay_throttle_aware)
            result = self._dlq_service.replay_throttle_aware(
                entry_id=entry.id,
                throttle=self,
            )

            if result.success:
                replayed += 1
                _record_dlq_replay_metric(
                    _throttle_recovery_replay_total,
                    {"domain": entry.domain, "result": "succeeded"},
                )
            else:
                failed += 1
                _record_dlq_replay_metric(
                    _throttle_recovery_replay_total,
                    {"domain": entry.domain, "result": "failed"},
                )

            # Adaptive Pacing: 배치 크기마다 용량 비율 기반 대기
            if (replayed + failed) % self._replay_batch_size == 0:
                adaptive_interval = self._calculate_adaptive_replay_interval()
                _record_dlq_replay_metric(
                    _throttle_replay_adaptive_interval_ms,
                    {"service": getattr(self, "_service_name", "unknown")},
                    value=adaptive_interval,
                )
                time.sleep(adaptive_interval / 1000)

        # Replay 완료 이벤트
        try:
            from selfhealing.services.throttle.adaptive import _emit_throttle_event

            _emit_throttle_event(
                "THROTTLE_REJECTION_REPLAY_COMPLETED",
                {
                    "replayed": replayed,
                    "failed": failed,
                    "remaining": len(pending_entries) - replayed - failed,
                },
            )
        except Exception:
            pass

    def _is_healthy_for_dlq_replay(self) -> bool:
        """Replay 계속 가능 여부 확인 (Full Stop, Emergency, 50% 미만 capacity 시 중단)."""
        if self._full_stop_active:
            return False
        if self._emergency_level > 0:
            return False
        capacity_ratio = self._current_limit / max(self.config.initial_limit, 1)
        if capacity_ratio < 0.5:
            return False
        return True

    # =========================================================================
    # Adaptive Pacing (capacity_ratio 기반 동적 간격)
    # =========================================================================

    def _calculate_adaptive_replay_interval(self) -> float:
        """
        Throttle capacity_ratio 기반 동적 Replay 간격 계산.

        90%+ → 기본 간격, 70~90% → 2배, 50~70% → 4배, 50% 미만 → 10배.

        Returns:
            밀리초 단위 간격
        """
        capacity_ratio = self._current_limit / max(self.config.initial_limit, 1)

        if capacity_ratio >= 0.9:
            return self._replay_interval_ms
        elif capacity_ratio >= 0.7:
            return self._replay_interval_ms * 2
        elif capacity_ratio >= 0.5:
            return self._replay_interval_ms * 4
        else:
            return self._replay_interval_ms * 10

    def _calculate_adaptive_batch_size(self) -> int:
        """
        ThrottleResult.remaining 기반 동적 배치 크기 계산.

        남은 permit의 50% 이내에서 배치 크기 결정.

        Returns:
            동적 배치 크기 (최소 1)
        """
        try:
            check_result = self.check(
                key=f"{self.config.key_prefix}:replay_probe",
                tier_id="standard",
            )
            adaptive_size = max(1, check_result.remaining // 2)
            return min(adaptive_size, self._replay_batch_size)
        except Exception:
            return self._replay_batch_size

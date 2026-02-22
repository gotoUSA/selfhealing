"""
X-Test Artifact Cleanup Service

X-Test 세션 종료 후 남겨진 테스트 아티팩트를 자동으로 정리하는 서비스.

정리 대상:
- Circuit Breaker: xtest_mode=True 상태를 CLOSED로 원복
- DLQ: source="x-test-mode" 항목 삭제
- Idempotency: xtest:idempotency:* 키 삭제
- Rate Limiter: xtest:rate_limit:* 카운터 삭제
- Scenario Results: 인메모리 _scenario_results 정리

Thin Task, Fat Service 원칙:
- Task는 단순 위임자 역할
- 모든 비즈니스 로직은 이 서비스에서 처리
"""

from __future__ import annotations

import structlog
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from selfhealing.services.audit.xtest_audit import log_xtest_cleanup_audit

logger = structlog.get_logger()


# X-Test-Mode 데이터 식별자
XTEST_SOURCE = "x-test-mode"
XTEST_IDEMPOTENCY_PREFIX = "xtest:idempotency:"
XTEST_RATE_LIMIT_PREFIX = "xtest:rate_limit:"


@dataclass
class XTestCleanupResult:
    """X-Test 정리 작업 결과."""

    success: bool
    sessions_cleaned: int = 0
    cb_states_restored: int = 0
    dlq_entries_purged: int = 0
    idempotency_keys_cleared: int = 0
    rate_limit_counters_reset: int = 0
    scenario_results_cleared: int = 0
    errors: list[str] = field(default_factory=list)
    cleaned_session_ids: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        """직렬화용 딕셔너리 변환."""
        return {
            "success": self.success,
            "sessions_cleaned": self.sessions_cleaned,
            "cb_states_restored": self.cb_states_restored,
            "dlq_entries_purged": self.dlq_entries_purged,
            "idempotency_keys_cleared": self.idempotency_keys_cleared,
            "rate_limit_counters_reset": self.rate_limit_counters_reset,
            "scenario_results_cleared": self.scenario_results_cleared,
            "errors": self.errors,
            "cleaned_session_ids": self.cleaned_session_ids,
            "timestamp": self.timestamp,
        }


class XTestCleanupService:
    """
    X-Test 아티팩트 자동 정리 서비스.

    만료된 X-Test 세션의 테스트 아티팩트를 자동으로 정리하여
    시스템 오염을 방지합니다.
    """

    def __init__(self, redis_client: Any | None = None):
        """
        Args:
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._redis = redis_client
        self._settings = None
        self._session_manager = None

    @property
    def settings(self):
        """설정 lazy loading."""
        if self._settings is None:
            from selfhealing.settings.xtest_cleanup import get_xtest_cleanup_settings

            self._settings = get_xtest_cleanup_settings()
        return self._settings

    @property
    def redis(self):
        """Redis 클라이언트 lazy loading."""
        if self._redis is None:
            try:
                from selfhealing.adapters.redis import get_redis_client

                self._redis = get_redis_client()
            except ImportError:
                logger.warning("x_test_cleanup.redis_adapter_available")
                self._redis = None
        return self._redis

    @property
    def session_manager(self):
        """세션 매니저 lazy loading."""
        if self._session_manager is None:
            from selfhealing.services.xtest_session_manager import (
                get_xtest_session_manager,
            )

            self._session_manager = get_xtest_session_manager()
        return self._session_manager

    def cleanup_expired_sessions(self) -> XTestCleanupResult:
        """
        만료된 X-Test 세션 및 관련 아티팩트 정리.

        Returns:
            정리 결과
        """
        result = XTestCleanupResult(success=True)

        try:
            # 만료된 세션 목록 조회
            expired_sessions = self.session_manager.get_expired_sessions()

            if not expired_sessions:
                logger.debug("x_test_cleanup.no_expired_sessions_found")
                return result

            logger.info(
                "x_test_cleanup.found_expired_sessions",
                count=len(expired_sessions),
            )

            for session in expired_sessions:
                try:
                    # 세션별 아티팩트 정리
                    self._cleanup_session_artifacts(session, result)

                    # 세션 삭제
                    self.session_manager.delete_session(session.session_id)
                    result.sessions_cleaned += 1
                    result.cleaned_session_ids.append(session.session_id)

                    logger.info(
                        "x_test_cleanup.cleaned_session",
                        session=session.session_id,
                    )

                except Exception as e:
                    error_msg = f"Failed to clean session {session.session_id}: {e}"
                    logger.error(
                        "x_test_cleanup.event",
                        error_msg=error_msg,
                    )
                    result.errors.append(error_msg)

            # 전역 X-Test 아티팩트 정리 (세션과 무관한 항목들)
            self._cleanup_orphaned_artifacts(result)

            # Audit 로깅
            log_xtest_cleanup_audit(
                session_id="system",
                component="cleanup_service",
                cleaned_count=result.sessions_cleaned,
                cleaned_ids=result.cleaned_session_ids[:20],
                user="system",
            )

        except Exception as e:
            error_msg = f"Cleanup failed: {e}"
            logger.error(f"[XTestCleanup] {error_msg}", exc_info=True)
            result.success = False
            result.errors.append(error_msg)

        return result

    def _cleanup_session_artifacts(
        self,
        session,
        result: XTestCleanupResult,
    ) -> None:
        """세션 관련 아티팩트 정리."""
        # CB 상태 원복
        if self.settings.cb_auto_restore:
            restored = self.restore_cb_states(session.components)
            result.cb_states_restored += restored

        # DLQ 항목 삭제
        if self.settings.dlq_auto_purge:
            purged = self.purge_dlq_entries(session.artifacts)
            result.dlq_entries_purged += purged

        # Idempotency 키 삭제
        if self.settings.idempotency_auto_clear:
            cleared = self.clear_idempotency_keys(session.session_id)
            result.idempotency_keys_cleared += cleared

        # Rate Limit 카운터 초기화
        if self.settings.rate_limit_auto_reset:
            reset = self.reset_rate_limit_counters(session.session_id)
            result.rate_limit_counters_reset += reset

    def _cleanup_orphaned_artifacts(self, result: XTestCleanupResult) -> None:
        """세션과 무관한 고아 아티팩트 정리."""
        # 시나리오 결과 정리
        cleared = self.clear_scenario_results()
        result.scenario_results_cleared = cleared

    def restore_cb_states(self, components: list[str] | None = None) -> int:
        """
        X-Test 모드로 변경된 Circuit Breaker 상태를 CLOSED로 원복.

        Args:
            components: 원복 대상 컴포넌트 목록 (None이면 전체)

        Returns:
            원복된 CB 수
        """
        restored_count = 0

        try:
            from selfhealing.services.circuit_breaker_service import (
                CircuitState,
                get_circuit_breaker_service,
            )

            cb_service = get_circuit_breaker_service()

            # 모든 CB 상태 조회
            all_states = cb_service.get_all_states()

            for service_name, state_info in all_states.items():
                # xtest_mode 플래그가 있는 경우만 원복
                if state_info.get("xtest_mode", False):
                    try:
                        cb_service.reset_circuit(service_name)
                        restored_count += 1
                        logger.debug(
                            "x_test_cleanup.restored_cb",
                            service_name=service_name,
                        )
                    except Exception as e:
                        logger.warning(
                            "x_test_cleanup.failed_restore_cb",
                            service_name=service_name,
                            error=e,
                        )

        except ImportError:
            logger.debug("x_test_cleanup.circuit_breaker_service_available")
        except Exception as e:
            logger.error(
                "x_test_cleanup.cb_restore_failed",
                error=e,
            )

        return restored_count

    def purge_dlq_entries(self, artifact_ids: list[str] | None = None) -> int:
        """
        X-Test 모드로 생성된 DLQ 항목 삭제.

        Args:
            artifact_ids: 삭제 대상 항목 ID 목록 (None이면 source로 필터)

        Returns:
            삭제된 항목 수
        """
        purged_count = 0

        try:
            from selfhealing.services.dlq import get_dlq_service

            dlq_service = get_dlq_service()

            # source가 x-test-mode인 항목 삭제
            if hasattr(dlq_service, "delete_by_source"):
                purged_count = dlq_service.delete_by_source(XTEST_SOURCE)
            elif artifact_ids:
                # artifact_ids로 개별 삭제
                for entry_id in artifact_ids:
                    try:
                        if hasattr(dlq_service, "delete_entry"):
                            dlq_service.delete_entry(entry_id)
                            purged_count += 1
                    except Exception:
                        pass

            if purged_count > 0:
                logger.info(
                    "x_test_cleanup.purged_dlq_entries",
                    purged_count=purged_count,
                )

        except ImportError:
            logger.debug("x_test_cleanup.dlq_service_available")
        except Exception as e:
            logger.error(
                "x_test_cleanup.dlq_purge_failed",
                error=e,
            )

        return purged_count

    def clear_idempotency_keys(self, session_id: str | None = None) -> int:
        """
        X-Test 모드로 생성된 Idempotency 키 삭제.

        Args:
            session_id: 세션 ID (None이면 전체 xtest 키)

        Returns:
            삭제된 키 수
        """
        cleared_count = 0

        if not self.redis:
            return 0

        try:
            # 패턴으로 키 검색
            pattern = f"{XTEST_IDEMPOTENCY_PREFIX}*"
            if session_id:
                pattern = f"{XTEST_IDEMPOTENCY_PREFIX}{session_id}:*"

            keys = self.redis.keys(pattern)

            if keys:
                self.redis.delete(*keys)
                cleared_count = len(keys)
                logger.info(
                    "x_test_cleanup.cleared_idempotency_keys",
                    cleared_count=cleared_count,
                )

        except Exception as e:
            logger.error(
                "x_test_cleanup.idempotency_clear_failed",
                error=e,
            )

        return cleared_count

    def reset_rate_limit_counters(self, session_id: str | None = None) -> int:
        """
        X-Test 모드로 사용된 Rate Limit 카운터 초기화.

        Args:
            session_id: 세션 ID (None이면 전체 xtest 카운터)

        Returns:
            초기화된 카운터 수
        """
        reset_count = 0

        if not self.redis:
            return 0

        try:
            # 패턴으로 키 검색
            pattern = f"{XTEST_RATE_LIMIT_PREFIX}*"
            if session_id:
                pattern = f"{XTEST_RATE_LIMIT_PREFIX}{session_id}:*"

            keys = self.redis.keys(pattern)

            if keys:
                self.redis.delete(*keys)
                reset_count = len(keys)
                logger.info(
                    "x_test_cleanup.reset_rate_limit_counters",
                    reset_count=reset_count,
                )

        except Exception as e:
            logger.error(
                "x_test_cleanup.rate_limit_reset_failed",
                error=e,
            )

        return reset_count

    def clear_scenario_results(self) -> int:
        """
        인메모리 시나리오 결과 정리.

        Returns:
            정리된 결과 수
        """
        cleared_count = 0

        try:
            from selfhealing.api.django.views.xtest.scenarios import (
                clear_scenario_results,
            )

            cleared_count = clear_scenario_results()
            if cleared_count > 0:
                logger.info(
                    "x_test_cleanup.cleared_scenario_results",
                    cleared_count=cleared_count,
                )

        except ImportError:
            logger.debug("x_test_cleanup.scenario_module_available")
        except Exception as e:
            logger.error(
                "x_test_cleanup.scenario_clear_failed",
                error=e,
            )

        return cleared_count

    def get_cleanup_stats(self) -> dict[str, Any]:
        """
        현재 정리 대상 통계 조회.

        Returns:
            정리 대상 통계
        """
        stats = {
            "active_sessions": 0,
            "expired_sessions": 0,
            "pending_cb_restores": 0,
            "pending_dlq_purges": 0,
            "pending_idempotency_clears": 0,
            "pending_rate_limit_resets": 0,
        }

        try:
            # 세션 통계
            stats["active_sessions"] = self.session_manager.get_sessions_count()
            stats["expired_sessions"] = len(self.session_manager.get_expired_sessions())

            # Redis 키 통계
            if self.redis:
                idempotency_keys = self.redis.keys(f"{XTEST_IDEMPOTENCY_PREFIX}*")
                rate_limit_keys = self.redis.keys(f"{XTEST_RATE_LIMIT_PREFIX}*")
                stats["pending_idempotency_clears"] = len(idempotency_keys) if idempotency_keys else 0
                stats["pending_rate_limit_resets"] = len(rate_limit_keys) if rate_limit_keys else 0

        except Exception as e:
            logger.error(
                "x_test_cleanup.stats_collection_failed",
                error=e,
            )

        return stats


# =============================================================================
# Factory Function
# =============================================================================

_xtest_cleanup_service: XTestCleanupService | None = None


def get_xtest_cleanup_service() -> XTestCleanupService:
    """
    XTestCleanupService 싱글톤 인스턴스 반환.

    Returns:
        XTestCleanupService 인스턴스
    """
    global _xtest_cleanup_service
    if _xtest_cleanup_service is None:
        _xtest_cleanup_service = XTestCleanupService()
    return _xtest_cleanup_service


def reset_xtest_cleanup_service() -> None:
    """서비스 캐시 초기화 (테스트용)."""
    global _xtest_cleanup_service
    _xtest_cleanup_service = None


__all__ = [
    "XTEST_SOURCE",
    "XTEST_IDEMPOTENCY_PREFIX",
    "XTEST_RATE_LIMIT_PREFIX",
    "XTestCleanupResult",
    "XTestCleanupService",
    "get_xtest_cleanup_service",
    "reset_xtest_cleanup_service",
]

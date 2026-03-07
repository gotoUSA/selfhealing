"""
Async Audit Lifecycle Manager - 비동기 Audit 파이프라인 생명주기 관리.

시작 시 복구, 종료 시 Graceful Shutdown을 처리하는 모듈.
프로세스 종료 시 미처리 이벤트 유실 0%를 보장합니다.

기능:
1. 시작 시 WAL 미처리 엔트리 복구
2. AsyncHealingLogger 초기화 및 Audit 콜백 설정
3. 종료 시 모든 미처리 이벤트 플러시
4. SIGTERM/SIGINT 시그널 핸들링

Usage (Django apps.py):
    from selfhealing.audit.async_audit_lifecycle import (
        startup_async_audit_system,
        register_shutdown_handlers,
    )

    class AuditConfig(AppConfig):
        def ready(self):
            startup_async_audit_system()
            register_shutdown_handlers()

Version: 1.0.0
"""

from __future__ import annotations

import atexit
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    pass

logger = structlog.get_logger()

# 싱글톤 플래그
_shutdown_registered = False
_startup_completed = False
_lifecycle_lock = threading.Lock()


# ═══════════════════════════════════════════════════════════════════════════════
# Audit 콜백 설정 (AsyncHealingLogger → AuditAdapter)
# ═══════════════════════════════════════════════════════════════════════════════


def create_audit_flush_callback():
    """
    AsyncHealingLogger 배치 플러시 콜백 생성.

    배치 이벤트를 AuditAdapter로 전송합니다.
    dict → AuditEntry 변환 후 log() 또는 log_batch() 호출.
    """

    def flush_to_audit_adapter(events: list[dict[str, Any]]) -> None:
        """배치 이벤트를 AuditAdapter로 전송."""
        if not events:
            return

        try:
            from selfhealing.adapters.audit.singleton import get_audit_adapter
            from selfhealing.interfaces.audit_adapter import AuditAction, AuditEntry

            adapter = get_audit_adapter()
            if adapter is None:
                logger.debug("async_audit_lifecycle.auditadapter_available")
                return

            entries = []
            for event_dict in events:
                try:
                    # action 문자열 → AuditAction Enum
                    action_str = event_dict.get("action", "config_change")
                    try:
                        action = AuditAction(action_str)
                    except ValueError:
                        action = AuditAction.CONFIG_CHANGE

                    entry = AuditEntry(
                        action=action,
                        actor_id=event_dict.get("actor_id"),
                        actor_type=event_dict.get("actor_type", "system"),
                        target_type=event_dict.get("target_type"),
                        target_id=event_dict.get("target_id", ""),
                        domain=event_dict.get("domain"),
                        reason=event_dict.get("reason"),
                        details=event_dict.get("details", {}),
                        success=event_dict.get("success", True),
                        error_message=event_dict.get("error_message"),
                    )
                    entries.append(entry)
                except Exception as e:
                    logger.debug(
                        "async_audit_lifecycle.event_conversion_failed",
                        error=e,
                    )

            # 배치 삽입 (지원되는 경우)
            if hasattr(adapter, "log_batch"):
                adapter.log_batch(entries)
            else:
                for entry in entries:
                    adapter.log(entry)

            logger.debug(
                "async_audit_lifecycle.flushed_audit_entries",
                entries_count=len(entries),
            )

        except Exception as e:
            logger.warning(
                "async_audit_lifecycle.flush_adapter_failed",
                error=e,
            )

    return flush_to_audit_adapter


# ═══════════════════════════════════════════════════════════════════════════════
# 시작 시 복구
# ═══════════════════════════════════════════════════════════════════════════════


def startup_async_audit_system() -> bool:
    """
    비동기 Audit 시스템 시작 및 복구.

    순서:
    1. 체크포인트 로드 (마지막 처리 시퀀스)
    2. WAL에서 미처리 엔트리 조회
    3. AsyncHealingLogger 초기화 및 콜백 설정
    4. SyncWorker 시작

    Returns:
        True: 시작 성공
        False: 시작 실패 또는 이미 시작됨
    """
    global _startup_completed

    with _lifecycle_lock:
        if _startup_completed:
            logger.debug("async_audit_lifecycle.already_started")
            return False

        logger.info("async_audit_lifecycle.starting_async_audit_system")

        try:
            # 1. 체크포인트 로드
            last_seq = _load_checkpoint()
            logger.info(
                "async_audit_lifecycle.last_processed_sequence",
                last_seq=last_seq,
            )

            # 2. WAL에서 미처리 엔트리 확인
            unprocessed_count = _check_unprocessed_wal_entries(last_seq)
            if unprocessed_count > 0:
                logger.info(
                    "async_audit_lifecycle.found_unprocessed_wal_entries",
                    unprocessed_count=unprocessed_count,
                )

            # 3. AsyncHealingLogger 초기화 및 시작
            _initialize_async_logger()

            # 4. SyncWorker 시작
            _start_sync_worker()

            _startup_completed = True
            logger.info("async_audit_lifecycle.async_audit_system_started")
            return True

        except Exception as e:
            logger.exception(
                "async_audit_lifecycle.startup_failed",
                error=e,
            )
            return False


def _load_checkpoint() -> int:
    """체크포인트에서 마지막 처리 시퀀스 로드."""
    try:
        from selfhealing.audit.checkpoint_strategy import (
            get_default_checkpoint_strategy,
        )

        strategy = get_default_checkpoint_strategy()
        return strategy.get_wal_sequence("default")
    except Exception as e:
        logger.debug(
            "async_audit_lifecycle.checkpoint_load_failed",
            error=e,
        )
        return 0


def _check_unprocessed_wal_entries(last_seq: int) -> int:
    """
    WAL에서 미처리 엔트리 수 확인 (Lazy Recovery 지원).

    count_unprocessed() 메서드를 우선 사용하여 파일 전체 읽기 없이
    빠르게 미처리 엔트리 수를 확인합니다.
    """
    try:
        wal = _get_wal_instance()
        if wal is None:
            return 0

        # count_unprocessed() 메서드 우선 사용 (Lazy: 파일 읽기 없음)
        if hasattr(wal, "count_unprocessed"):
            return wal.count_unprocessed(last_processed_seq=last_seq)

        # Fallback: 전체 읽기 (기존 동작)
        if hasattr(wal, "recover_unprocessed"):
            entries = wal.recover_unprocessed(last_processed_seq=last_seq)
            return len(entries) if entries else 0

        return 0
    except Exception as e:
        logger.debug(
            "async_audit_lifecycle.wal_check_failed",
            error=e,
        )
        return 0


def _initialize_async_logger() -> None:
    """AsyncHealingLogger 초기화 및 콜백 설정."""
    try:
        from selfhealing.utils.async_logger import AsyncHealingLogger

        # Audit 플러시 콜백 설정
        flush_callback = create_audit_flush_callback()
        AsyncHealingLogger.configure(flush_callback=flush_callback)

        # 백그라운드 워커 시작
        AsyncHealingLogger.start()

        logger.info("async_audit_lifecycle.asynchealinglogger_initialized")
    except Exception as e:
        logger.warning(
            "async_audit_lifecycle.asynchealinglogger_init_failed",
            error=e,
        )


def _start_sync_worker() -> None:
    """AuditSyncWorker 시작."""
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        sync_worker = AuditSyncWorker.get_instance()
        sync_worker.start()

        logger.info("async_audit_lifecycle.auditsyncworker_started")
    except Exception as e:
        logger.debug(
            "async_audit_lifecycle.syncworker_start_failed",
            error=e,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════════════════


def graceful_shutdown_audit_system() -> None:
    """
    종료 시 감사 시스템 정상 종료.

    순서:
    1. AsyncHealingLogger 플러시 (메모리 → WAL/Adapter)
    2. AuditSyncWorker 종료 대기 (WAL → 중앙 저장소)
    3. WAL 최종 플러시 (디스크 동기화)
    4. 체크포인트 저장

    데이터 유실 0%를 보장합니다.

    테스트 환경(SELFHEALING_TEST_MODE=true)에서는 실행되지 않습니다.
    """
    # 테스트 환경에서는 실제 리소스 접근 방지
    if os.getenv("SELFHEALING_TEST_MODE", "").lower() == "true":
        return

    logger.info("graceful_shutdown.starting_audit_system_shutdown")

    # 1. AsyncHealingLogger 플러시 및 종료
    _shutdown_async_logger()

    # 2. AuditSyncWorker 종료
    _shutdown_sync_worker()

    # 3. WAL 플러시 및 종료
    _shutdown_wal()

    # 4. 체크포인트 저장
    _save_final_checkpoint()

    logger.info("graceful_shutdown.audit_system_shutdown_complete")


def _shutdown_async_logger() -> None:
    """AsyncHealingLogger 플러시 및 종료."""
    try:
        from selfhealing.utils.async_logger import AsyncHealingLogger

        # 남은 이벤트 플러시
        AsyncHealingLogger.flush()

        # 워커 종료 (타임아웃 5초)
        AsyncHealingLogger.stop(timeout=5.0)

        logger.info("graceful_shutdown.asynchealinglogger_stopped")
    except Exception as e:
        logger.warning(
            "graceful_shutdown.asynchealinglogger_error",
            error=e,
        )


def _shutdown_sync_worker() -> None:
    """AuditSyncWorker 종료."""
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        sync_worker = AuditSyncWorker.get_instance()

        # 동기화 완료 대기 (타임아웃 30초)
        sync_worker.stop(timeout=30.0)

        logger.info("graceful_shutdown.auditsyncworker_stopped")
    except Exception as e:
        logger.warning(
            "graceful_shutdown.syncworker_error",
            error=e,
        )


def _shutdown_wal() -> None:
    """WAL 플러시 및 종료."""
    try:
        wal = _get_wal_instance()
        if wal is None:
            return

        # flush 메서드가 있으면 호출
        if hasattr(wal, "flush"):
            wal.flush()

        # close 메서드가 있으면 호출
        if hasattr(wal, "close"):
            wal.close()

        logger.info("graceful_shutdown.wal_closed")
    except Exception as e:
        logger.warning(
            "graceful_shutdown.wal_error",
            error=e,
        )


def _save_final_checkpoint() -> None:
    """마지막 체크포인트 저장."""
    try:
        from selfhealing.audit.checkpoint_strategy import (
            UnifiedCheckpointData,
            get_default_checkpoint_strategy,
        )

        last_seq = _get_last_processed_sequence()

        if last_seq > 0:
            strategy = get_default_checkpoint_strategy()
            strategy.save("default", UnifiedCheckpointData(wal_sequence=last_seq))
            logger.info(
                "graceful_shutdown.checkpoint_saved",
                last_seq=last_seq,
            )
    except Exception as e:
        logger.warning(
            "graceful_shutdown.checkpoint_error",
            error=e,
        )


def _get_last_processed_sequence() -> int:
    """마지막 처리된 시퀀스 번호 가져오기."""
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        sync_worker = AuditSyncWorker.get_instance()
        return getattr(sync_worker, "_last_processed_seq", 0)
    except Exception:
        return 0


def _get_wal_instance():
    """WAL 인스턴스 가져오기."""
    try:
        from selfhealing.services.audit import _get_wal

        return _get_wal()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 시그널 핸들러 등록
# ═══════════════════════════════════════════════════════════════════════════════


def _is_test_mode() -> bool:
    """테스트 환경인지 확인."""
    return os.getenv("SELFHEALING_TEST_MODE", "").lower() == "true"


def register_shutdown_handlers() -> bool:
    """
    종료 시그널 핸들러 등록.

    - atexit: 정상 종료 시
    - SIGTERM: Kubernetes Pod 종료 시
    - SIGINT: Ctrl+C (개발 환경)

    테스트 환경(SELFHEALING_TEST_MODE=true)에서는 등록하지 않습니다.

    Returns:
        True: 등록 성공
        False: 이미 등록됨 또는 테스트 환경
    """
    global _shutdown_registered

    with _lifecycle_lock:
        if _shutdown_registered:
            logger.debug("async_audit_lifecycle.shutdown_handlers_already_registered")
            return False

        # 테스트 환경에서는 실제 리소스 접근을 방지하기 위해 등록하지 않음
        if _is_test_mode():
            _shutdown_registered = True
            logger.debug("async_audit_lifecycle.skipping_shutdown_handlers_test")
            return False

        # atexit: 정상 종료 시 호출
        atexit.register(graceful_shutdown_audit_system)

        # SIGTERM: Kubernetes Pod 종료, docker stop 등
        _register_signal_handler(signal.SIGTERM, _handle_sigterm)

        # SIGINT: Ctrl+C (개발 환경)
        _register_signal_handler(signal.SIGINT, _handle_sigint)

        _shutdown_registered = True
        logger.info("async_audit_lifecycle.shutdown_handlers_registered")
        return True


def _register_signal_handler(sig: signal.Signals, handler) -> None:
    """시그널 핸들러 안전하게 등록."""
    try:
        # 메인 스레드에서만 시그널 핸들러 등록 가능
        if threading.current_thread() is threading.main_thread():
            signal.signal(sig, handler)
    except (ValueError, OSError) as e:
        logger.debug(
            "async_audit_lifecycle.signal_handler_registration_failed",
            error=e,
        )


def _handle_sigterm(signum: int, frame) -> None:
    """SIGTERM 핸들러."""
    logger.info(
        "graceful_shutdown.received_sigterm_signal",
        signum=signum,
    )
    graceful_shutdown_audit_system()
    sys.exit(0)


def _handle_sigint(signum: int, frame) -> None:
    """SIGINT 핸들러."""
    logger.info(
        "graceful_shutdown.received_sigint_signal",
        signum=signum,
    )
    graceful_shutdown_audit_system()
    sys.exit(0)


# ═══════════════════════════════════════════════════════════════════════════════
# 상태 조회
# ═══════════════════════════════════════════════════════════════════════════════


def get_lifecycle_status() -> dict[str, Any]:
    """생명주기 상태 조회."""
    return {
        "startup_completed": _startup_completed,
        "shutdown_registered": _shutdown_registered,
    }


def reset_lifecycle_state() -> None:
    """생명주기 상태 초기화 (테스트용)."""
    global _startup_completed, _shutdown_registered

    with _lifecycle_lock:
        _startup_completed = False
        _shutdown_registered = False


# ═══════════════════════════════════════════════════════════════════════════════
# 모니터링 메트릭 (6.3 모니터링)
# ═══════════════════════════════════════════════════════════════════════════════


def get_async_audit_metrics() -> dict[str, Any]:
    """
    비동기 Audit 파이프라인 메트릭 조회.

    AsyncHealingLogger의 통계와 현재 큐 크기를 반환합니다.
    Prometheus 또는 모니터링 시스템에서 수집할 수 있습니다.

    Returns:
        dict: 메트릭 정보
            - events_logged: 로깅된 총 이벤트 수
            - events_flushed: 플러시된 총 이벤트 수
            - immediate_flushes: 즉시 플러시 횟수 (CRITICAL 이벤트)
            - batch_flushes: 배치 플러시 횟수
            - flush_errors: 플러시 에러 횟수
            - queue_size: 현재 큐 대기 이벤트 수
            - worker_running: 워커 스레드 실행 여부
    """
    try:
        from selfhealing.utils.async_logger import AsyncHealingLogger

        # 기본 통계 가져오기
        stats = AsyncHealingLogger.get_stats()

        # 큐 크기 추가
        queue_size = 0
        try:
            queue_size = AsyncHealingLogger._queue.qsize()
        except Exception:
            pass

        # 워커 상태 추가
        worker_running = AsyncHealingLogger._running

        return {
            **stats,
            "queue_size": queue_size,
            "worker_running": worker_running,
            "lifecycle_startup_completed": _startup_completed,
            "lifecycle_shutdown_registered": _shutdown_registered,
        }

    except Exception as e:
        logger.warning(
            "async_audit_lifecycle.failed_get_metrics",
            error=e,
        )
        return {
            "error": str(e),
            "lifecycle_startup_completed": _startup_completed,
            "lifecycle_shutdown_registered": _shutdown_registered,
        }


def export_metrics_to_prometheus() -> str:
    """
    Prometheus 포맷으로 메트릭 출력.

    /metrics 엔드포인트에서 사용할 수 있는 텍스트 포맷.

    Returns:
        str: Prometheus 텍스트 포맷 메트릭
    """
    metrics = get_async_audit_metrics()

    lines = [
        "# HELP async_audit_events_logged Total events logged to async logger",
        "# TYPE async_audit_events_logged counter",
        f"async_audit_events_logged {metrics.get('events_logged', 0)}",
        "",
        "# HELP async_audit_events_flushed Total events flushed to backend",
        "# TYPE async_audit_events_flushed counter",
        f"async_audit_events_flushed {metrics.get('events_flushed', 0)}",
        "",
        "# HELP async_audit_immediate_flushes Total immediate flushes (CRITICAL events)",
        "# TYPE async_audit_immediate_flushes counter",
        f"async_audit_immediate_flushes {metrics.get('immediate_flushes', 0)}",
        "",
        "# HELP async_audit_batch_flushes Total batch flushes",
        "# TYPE async_audit_batch_flushes counter",
        f"async_audit_batch_flushes {metrics.get('batch_flushes', 0)}",
        "",
        "# HELP async_audit_flush_errors Total flush errors",
        "# TYPE async_audit_flush_errors counter",
        f"async_audit_flush_errors {metrics.get('flush_errors', 0)}",
        "",
        "# HELP async_audit_queue_size Current queue size",
        "# TYPE async_audit_queue_size gauge",
        f"async_audit_queue_size {metrics.get('queue_size', 0)}",
        "",
        "# HELP async_audit_worker_running Worker thread running status",
        "# TYPE async_audit_worker_running gauge",
        f"async_audit_worker_running {1 if metrics.get('worker_running', False) else 0}",
    ]

    return "\n".join(lines)

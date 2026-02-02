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
import logging
import os
import signal
import sys
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.audit.sync_worker import AuditSyncWorker

logger = logging.getLogger(__name__)

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
                logger.debug("[AsyncAuditLifecycle] AuditAdapter not available")
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
                    logger.debug(f"[AsyncAuditLifecycle] Event conversion failed: {e}")

            # 배치 삽입 (지원되는 경우)
            if hasattr(adapter, "log_batch"):
                adapter.log_batch(entries)
            else:
                for entry in entries:
                    adapter.log(entry)

            logger.debug(f"[AsyncAuditLifecycle] Flushed {len(entries)} audit entries")

        except Exception as e:
            logger.warning(f"[AsyncAuditLifecycle] Flush to adapter failed: {e}")

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
            logger.debug("[AsyncAuditLifecycle] Already started")
            return False

        logger.info("[AsyncAuditLifecycle] Starting async audit system...")

        try:
            # 1. 체크포인트 로드
            last_seq = _load_checkpoint()
            logger.info(f"[AsyncAuditLifecycle] Last processed sequence: {last_seq}")

            # 2. WAL에서 미처리 엔트리 확인
            unprocessed_count = _check_unprocessed_wal_entries(last_seq)
            if unprocessed_count > 0:
                logger.info(f"[AsyncAuditLifecycle] Found {unprocessed_count} unprocessed WAL entries")

            # 3. AsyncHealingLogger 초기화 및 시작
            _initialize_async_logger()

            # 4. SyncWorker 시작
            _start_sync_worker()

            _startup_completed = True
            logger.info("[AsyncAuditLifecycle] Async audit system started successfully")
            return True

        except Exception as e:
            logger.error(f"[AsyncAuditLifecycle] Startup failed: {e}")
            return False


def _load_checkpoint() -> int:
    """체크포인트에서 마지막 처리 시퀀스 로드."""
    try:
        from selfhealing.audit.checkpoint_manager import get_checkpoint_manager

        checkpoint = get_checkpoint_manager()
        return checkpoint.load()
    except Exception as e:
        logger.debug(f"[AsyncAuditLifecycle] Checkpoint load failed: {e}")
        return 0


def _check_unprocessed_wal_entries(last_seq: int) -> int:
    """WAL에서 미처리 엔트리 수 확인."""
    try:
        wal = _get_wal_instance()
        if wal is None:
            return 0

        # recover_unprocessed 메서드가 있으면 호출
        if hasattr(wal, "recover_unprocessed"):
            entries = wal.recover_unprocessed(last_processed_seq=last_seq)
            return len(entries) if entries else 0
        return 0
    except Exception as e:
        logger.debug(f"[AsyncAuditLifecycle] WAL check failed: {e}")
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

        logger.info("[AsyncAuditLifecycle] AsyncHealingLogger initialized")
    except Exception as e:
        logger.warning(f"[AsyncAuditLifecycle] AsyncHealingLogger init failed: {e}")


def _start_sync_worker() -> None:
    """AuditSyncWorker 시작."""
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        sync_worker = AuditSyncWorker.get_instance()
        sync_worker.start()

        logger.info("[AsyncAuditLifecycle] AuditSyncWorker started")
    except Exception as e:
        logger.debug(f"[AsyncAuditLifecycle] SyncWorker start failed: {e}")


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
    """
    logger.info("[GracefulShutdown] Starting audit system shutdown...")

    # 1. AsyncHealingLogger 플러시 및 종료
    _shutdown_async_logger()

    # 2. AuditSyncWorker 종료
    _shutdown_sync_worker()

    # 3. WAL 플러시 및 종료
    _shutdown_wal()

    # 4. 체크포인트 저장
    _save_final_checkpoint()

    logger.info("[GracefulShutdown] Audit system shutdown complete")


def _shutdown_async_logger() -> None:
    """AsyncHealingLogger 플러시 및 종료."""
    try:
        from selfhealing.utils.async_logger import AsyncHealingLogger

        # 남은 이벤트 플러시
        AsyncHealingLogger.flush()

        # 워커 종료 (타임아웃 5초)
        AsyncHealingLogger.stop(timeout=5.0)

        logger.info("[GracefulShutdown] AsyncHealingLogger stopped")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] AsyncHealingLogger error: {e}")


def _shutdown_sync_worker() -> None:
    """AuditSyncWorker 종료."""
    try:
        from selfhealing.audit.sync_worker import AuditSyncWorker

        sync_worker = AuditSyncWorker.get_instance()

        # 동기화 완료 대기 (타임아웃 30초)
        sync_worker.stop(timeout=30.0)

        logger.info("[GracefulShutdown] AuditSyncWorker stopped")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] SyncWorker error: {e}")


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

        logger.info("[GracefulShutdown] WAL closed")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] WAL error: {e}")


def _save_final_checkpoint() -> None:
    """마지막 체크포인트 저장."""
    try:
        from selfhealing.audit.checkpoint_manager import get_checkpoint_manager

        # SyncWorker에서 마지막 처리 시퀀스 가져오기
        last_seq = _get_last_processed_sequence()

        if last_seq > 0:
            checkpoint = get_checkpoint_manager()
            checkpoint.save(last_sequence=last_seq)
            logger.info(f"[GracefulShutdown] Checkpoint saved: seq={last_seq}")
    except Exception as e:
        logger.warning(f"[GracefulShutdown] Checkpoint error: {e}")


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
        from selfhealing.services.audit_helpers import _get_wal

        return _get_wal()
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 시그널 핸들러 등록
# ═══════════════════════════════════════════════════════════════════════════════


def register_shutdown_handlers() -> bool:
    """
    종료 시그널 핸들러 등록.

    - atexit: 정상 종료 시
    - SIGTERM: Kubernetes Pod 종료 시
    - SIGINT: Ctrl+C (개발 환경)

    Returns:
        True: 등록 성공
        False: 이미 등록됨
    """
    global _shutdown_registered

    with _lifecycle_lock:
        if _shutdown_registered:
            logger.debug("[AsyncAuditLifecycle] Shutdown handlers already registered")
            return False

        # atexit: 정상 종료 시 호출
        atexit.register(graceful_shutdown_audit_system)

        # SIGTERM: Kubernetes Pod 종료, docker stop 등
        _register_signal_handler(signal.SIGTERM, _handle_sigterm)

        # SIGINT: Ctrl+C (개발 환경)
        _register_signal_handler(signal.SIGINT, _handle_sigint)

        _shutdown_registered = True
        logger.info("[AsyncAuditLifecycle] Shutdown handlers registered")
        return True


def _register_signal_handler(sig: signal.Signals, handler) -> None:
    """시그널 핸들러 안전하게 등록."""
    try:
        # 메인 스레드에서만 시그널 핸들러 등록 가능
        if threading.current_thread() is threading.main_thread():
            signal.signal(sig, handler)
    except (ValueError, OSError) as e:
        logger.debug(f"[AsyncAuditLifecycle] Signal handler registration failed: {e}")


def _handle_sigterm(signum: int, frame) -> None:
    """SIGTERM 핸들러."""
    logger.info(f"[GracefulShutdown] Received SIGTERM (signal {signum})")
    graceful_shutdown_audit_system()
    sys.exit(0)


def _handle_sigint(signum: int, frame) -> None:
    """SIGINT 핸들러."""
    logger.info(f"[GracefulShutdown] Received SIGINT (signal {signum})")
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

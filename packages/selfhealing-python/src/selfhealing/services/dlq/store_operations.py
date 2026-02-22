"""
DLQ Store Operations Mixin.

Provides methods for storing failed operations in the DLQ.
Includes local file fallback for zero data loss guarantee.

Fallback Strategy:
    1. Primary: Repository (PostgreSQL/Redis)
    2. Fallback: Local JSON file (/tmp/selfhealing_dlq_fallback.jsonl)

Code reference:
    services/coordination/critical_path_fallback.py (CriticalPathFallback 패턴)
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


# DLQ Fallback 경로 (selfhealing 소유, 상대 시스템 비침투)
DLQ_FALLBACK_PATH = Path("/tmp/selfhealing_dlq_fallback.jsonl")


class StoreOperationsMixin:
    """Mixin providing DLQ store operations."""

    def store_failure(
        self,
        domain: str,
        failure_type: str,
        entity_type: str | None = None,
        entity_id: str | None = None,
        user_id: int | None = None,
        error_code: str = "",
        error_message: str = "",
        snapshot_data: dict[str, Any] | None = None,
        request_data: dict[str, Any] | None = None,
        response_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        next_action_hint: str = "",
        recommended_action: str = "",
        request: Any = None,
    ):
        """
        Store a failed operation in the DLQ.

        하이브리드 로직:
        - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
        - request가 없으면 → 직접 adapter 호출 (Celery 등 비동기 컨텍스트)

        Args:
            domain: Business domain (payment, point, inventory, webhook, notification)
            failure_type: Specific failure type (e.g., PG_TIMEOUT, AMOUNT_MISMATCH)
            entity_type: Type of related entity (e.g., "order", "payment", "product")
            entity_id: ID of related entity
            user_id: Related User ID
            error_code: Error code from external system
            error_message: Human-readable error message
            snapshot_data: State snapshot for recovery
            request_data: Original request payload
            response_data: External system response
            metadata: Additional debug context
            next_action_hint: Guidance for operators
            recommended_action: Suggested action (replay, manual_check, etc.)
            request: Django HttpRequest 객체 (있으면 버퍼에 적재)

        Returns:
            DLQEntryResult with creation status
        """
        from selfhealing.services.dlq.models import DLQEntryResult

        if not self.is_enabled:
            logger.debug("dlq_service.dlq_disabled_skipping_storage")
            return DLQEntryResult.failed("DLQ is disabled")

        try:
            failed_op = self.repository.create(
                domain=domain,
                failure_type=failure_type,
                entity_type=entity_type,
                entity_id=entity_id,
                user_id=user_id,
                error_code=error_code,
                error_message=error_message,
                snapshot_data=snapshot_data,
                request_data=request_data,
                response_data=response_data,
                metadata=metadata,
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
            )

            logger.info(
                "dlq_service.created_dlq_entry",
                failed_op=failed_op.id,
                domain=domain,
                failure_type=failure_type,
            )

            # Push 이벤트 - Gauge 증가 (SafeGauge 사용)
            try:
                from selfhealing.metrics.event_handlers import DLQMetricEventHandler

                DLQMetricEventHandler.on_item_created(domain, failure_type)
            except ImportError:
                pass  # Metrics not available

            # Audit 로깅: DLQ 저장 기록 (버퍼 패턴 지원)
            self._log_dlq_audit(
                action="store",
                dlq_id=failed_op.id,
                domain=domain,
                failure_type=failure_type,
                error_message=error_message,
                success=True,
                request=request,
            )

            return DLQEntryResult.created(failed_op.id)

        except Exception as e:
            logger.exception(
                "dlq_service.failed_store_dlq",
                error=e,
            )

            # Local Fallback: 무손실 보장
            entry_data = {
                "domain": domain,
                "failure_type": failure_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "user_id": user_id,
                "error_code": error_code,
                "error_message": error_message,
                "snapshot_data": snapshot_data,
                "request_data": request_data,
                "response_data": response_data,
                "metadata": metadata,
                "next_action_hint": next_action_hint,
                "recommended_action": recommended_action,
            }

            fallback_path = self._write_to_local_fallback(entry_data, str(e))
            if fallback_path:
                logger.warning(
                    "dlq_service.fallback_local_file",
                    fallback_path=fallback_path,
                )
                return DLQEntryResult.fallback(str(e), fallback_path)

            return DLQEntryResult.failed(str(e))

    # =========================================================================
    # Local Fallback (Zero Data Loss)
    # =========================================================================

    _fallback_lock = threading.Lock()

    def _write_to_local_fallback(
        self,
        entry_data: dict[str, Any],
        original_error: str,
    ) -> str | None:
        """
        3단계 Fallback 체인으로 DLQ 데이터 무손실 보장.

        1차: DiskPersistentBuffer (LMDB) - CRC32 무결성, Group Commit, Pod 재시작 내구성
        2차: JSONL 파일 - DiskPersistentBuffer 불가 시
        3차: stderr 출력 - 모든 Fallback 실패 시 최소한의 기록

        Args:
            entry_data: DLQ 엔트리 데이터
            original_error: 원래 발생한 에러

        Returns:
            저장된 경로 (성공 시), None (실패 시)
        """
        # 1차: DiskPersistentBuffer (LMDB 기반, audit/persistence/disk_buffer.py)
        try:
            from selfhealing.audit.persistence.disk_buffer import DiskBufferAdapter

            buffer = DiskBufferAdapter.get_instance()
            buffer.put(
                {
                    "category": "dlq_fallback",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_error": original_error,
                    "entry_data": entry_data,
                    "pending_reconciliation": True,
                }
            )
            logger.info(
                "dlq_service.fallback_saved_diskpersistentbuffer",
                entry_data=entry_data.get('domain'),
            )
            # Fallback 채널 메트릭 기록 (Fail-Open)
            try:
                from selfhealing.services.metrics.definitions import (
                    throttle_dlq_fallback_total,
                )

                throttle_dlq_fallback_total.labels(channel="disk_persistent_buffer").inc()
            except Exception:
                pass
            return "disk_persistent_buffer://dlq_fallback"
        except ImportError:
            logger.debug("dlq_service.diskpersistentbuffer_available")
        except Exception as e:
            logger.warning(
                "dlq_service.diskpersistentbuffer_failed",
                error=e,
            )

        # 2차: JSONL 파일 (기존 방식)
        try:
            with self._fallback_lock:
                DLQ_FALLBACK_PATH.parent.mkdir(parents=True, exist_ok=True)

                fallback_entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "original_error": original_error,
                    "entry_data": entry_data,
                    "pending_reconciliation": True,
                }

                with open(DLQ_FALLBACK_PATH, "a", encoding="utf-8") as f:
                    f.write(json.dumps(fallback_entry, default=str) + "\n")

                logger.info(
                    "dlq_service.fallback_entry_saved_jsonl",
                    entry_data=entry_data.get('domain'),
                )
                # Fallback 채널 메트릭 기록 (Fail-Open)
                try:
                    from selfhealing.services.metrics.definitions import (
                        throttle_dlq_fallback_total,
                    )

                    throttle_dlq_fallback_total.labels(channel="jsonl").inc()
                except Exception:
                    pass
                return str(DLQ_FALLBACK_PATH)

        except Exception as fallback_error:
            # 3차: stderr 출력 (최후 수단)
            import sys

            print(
                f"[DLQ CRITICAL] All fallbacks failed. "
                f"DB: {original_error}, JSONL: {fallback_error}. "
                f"Data: {json.dumps(entry_data, default=str)[:500]}",
                file=sys.stderr,
            )
            logger.critical(
                "dlq_service.critical_all_fallback_methods",
                original_error=original_error,
                fallback_error=fallback_error,
            )
            # Fallback 채널 메트릭 기록 (Fail-Open)
            try:
                from selfhealing.services.metrics.definitions import (
                    throttle_dlq_fallback_total,
                )

                throttle_dlq_fallback_total.labels(channel="stderr").inc()
            except Exception:
                pass
            return None

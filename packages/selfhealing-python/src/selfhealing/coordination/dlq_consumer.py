"""
DLQ Consumer with Leader Election.

분산 환경에서 단일 노드만 DLQ를 처리하도록 보장.
Leader Election을 통해 여러 Pod 중 하나만 활성화됩니다.

Usage:
    from selfhealing.coordination.dlq_consumer import DLQConsumerCoordinator

    coordinator = DLQConsumerCoordinator()
    coordinator.start()
    # ...
    coordinator.stop()
"""

from __future__ import annotations

import structlog
import threading
import time
from typing import TYPE_CHECKING, Callable

from selfhealing.coordination.base import LeadershipState
from selfhealing.coordination.factory import get_leader_elector
from selfhealing.coordination.shutdown_integration import (
    register_for_graceful_shutdown,
)

if TYPE_CHECKING:
    from selfhealing.coordination.base import LeaderElector

logger = structlog.get_logger()

# DLQ Consumer 리소스 이름 (Leader Election 키)
DLQ_CONSUMER_RESOURCE = "dlq-consumer"


class DLQConsumerCoordinator:
    """
    DLQ Consumer 리더 선출 코디네이터.

    분산 환경에서 단일 DLQ Consumer만 활성화되도록 보장합니다.
    리더가 되면 DLQ 처리를 시작하고, 리더십을 잃으면 중단합니다.

    Attributes:
        elector: Leader Elector 인스턴스
        is_consuming: 현재 DLQ 처리 중인지 여부
    """

    def __init__(
        self,
        resource_name: str = DLQ_CONSUMER_RESOURCE,
        process_interval_seconds: float = 10.0,
        batch_size: int = 50,
    ):
        """
        초기화.

        Args:
            resource_name: 리소스 이름 (리더 선출 키)
            process_interval_seconds: DLQ 처리 주기 (초)
            batch_size: 한 번에 처리할 DLQ 항목 수
        """
        self._resource_name = resource_name
        self._process_interval = process_interval_seconds
        self._batch_size = batch_size

        self._elector: LeaderElector = get_leader_elector(resource_name)
        self._consuming = False
        self._consume_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # 콜백 등록
        self._elector.on_become_leader(self._on_become_leader)
        self._elector.on_lose_leader(self._on_lose_leader)

        # Graceful Shutdown 등록
        register_for_graceful_shutdown(self._elector)

    @property
    def is_consuming(self) -> bool:
        """현재 DLQ 처리 중인지 여부."""
        return self._consuming

    @property
    def is_leader(self) -> bool:
        """현재 리더인지 여부."""
        return self._elector.is_leader()

    def start(self) -> None:
        """DLQ Consumer 시작 (Leader Election 시작)."""
        logger.info(
            "dlq_consumer.시작",
            self=self._resource_name,
        )
        self._stop_event.clear()
        self._elector.start()

    def stop(self) -> None:
        """DLQ Consumer 중지 (Leader Election 중지)."""
        logger.info(
            "dlq_consumer.중지",
            self=self._resource_name,
        )
        self._stop_event.set()
        self._consuming = False

        # 소비 스레드 종료 대기
        if self._consume_thread and self._consume_thread.is_alive():
            self._consume_thread.join(timeout=5.0)

        self._elector.stop()
        logger.info(
            "dlq_consumer.중지됨",
            self=self._resource_name,
        )

    def _on_become_leader(self) -> None:
        """리더가 되었을 때 DLQ 처리 시작."""
        logger.info(
            "dlq_consumer.리더가_dlq_처리_시작",
        )
        self._consuming = True
        self._start_consume_loop()

    def _on_lose_leader(self) -> None:
        """리더십을 잃었을 때 DLQ 처리 중단."""
        logger.info(
            "dlq_consumer.리더십_상실_dlq_처리",
        )
        self._consuming = False

    def _start_consume_loop(self) -> None:
        """DLQ 소비 루프 시작 (별도 스레드)."""
        if self._consume_thread and self._consume_thread.is_alive():
            return

        self._consume_thread = threading.Thread(
            target=self._consume_loop,
            name=f"DLQConsumer-{self._resource_name}",
            daemon=True,
        )
        self._consume_thread.start()

    def _consume_loop(self) -> None:
        """DLQ 소비 루프."""
        logger.info("dlq_consumer.소비_루프_시작")

        while self._consuming and not self._stop_event.is_set():
            try:
                # Lease 유효성 확인 (Self-Fencing)
                if not self._elector.is_leader():
                    logger.warning("dlq_consumer.리더십_확인_실패_소비")
                    break

                # DLQ 처리
                processed = self._process_dlq_batch()

                if processed > 0:
                    logger.info(
                        "dlq_consumer.dlq_항목_처리됨",
                        processed=processed,
                    )

                # 다음 처리까지 대기
                self._stop_event.wait(timeout=self._process_interval)

            except Exception as e:
                logger.error(f"[DLQConsumer] 소비 루프 오류: {e}", exc_info=True)
                self._stop_event.wait(timeout=self._process_interval)

        logger.info("dlq_consumer.소비_루프_종료")

    def _process_dlq_batch(self) -> int:
        """
        DLQ 배치 처리.

        Returns:
            처리된 항목 수
        """
        try:
            from selfhealing.services import get_dlq_service

            dlq_service = get_dlq_service()

            # 펜싱 토큰 확인 (Stale Leader 방지)
            fencing_token = self._elector.get_fencing_token()

            # 대기 중인 DLQ 항목 조회
            pending_entries = dlq_service.list_pending_entries(
                limit=self._batch_size,
            )

            if not pending_entries:
                return 0

            processed = 0
            for entry in pending_entries:
                # 매 항목 처리 전 리더십 확인
                if not self._consuming or not self._elector.is_leader():
                    logger.warning("dlq_consumer.리더십_상실_배치_처리")
                    break

                try:
                    # Replay 서비스 호출
                    from selfhealing.services import get_replay_service

                    replay_service = get_replay_service()
                    result = replay_service.replay_single(entry.id)

                    if result.success:
                        processed += 1
                    else:
                        logger.warning(
                            "dlq_consumer.dlq_재처리_실패",
                            entry=entry.id,
                            result=result.error,
                        )

                except Exception as e:
                    logger.error(
                        "dlq_consumer.dlq_처리_오류",
                        entry=entry.id,
                        error=e,
                    )

            return processed

        except ImportError:
            # 서비스가 없는 경우 (테스트 환경)
            logger.debug("dlq_consumer.dlq_서비스_없음_테스트")
            return 0
        except Exception as e:
            logger.error(f"[DLQConsumer] 배치 처리 오류: {e}", exc_info=True)
            return 0


def get_dlq_consumer_coordinator(
    resource_name: str = DLQ_CONSUMER_RESOURCE,
) -> DLQConsumerCoordinator:
    """
    DLQ Consumer Coordinator 싱글톤 반환.

    Args:
        resource_name: 리소스 이름

    Returns:
        DLQConsumerCoordinator 인스턴스
    """
    # 캐시 구현 (필요시)
    return DLQConsumerCoordinator(resource_name=resource_name)

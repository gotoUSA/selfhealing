"""
Non-Blocking Retry 핸들러.

실패한 메시지를 Retry 토픽으로 전송하여 순서 보장과 함께
재시도를 수행합니다. 최대 재시도 횟수 초과 시 DLQ로 이동합니다.

토폴로지:
    Main Topic -> Retry-1 (1분) -> Retry-2 (5분) -> Retry-3 (15분) -> DLQ

핵심 특징:
- 비블로킹 재시도: 메인 토픽 처리 차단 없음
- 지수 백오프: 재시도 간격 점진적 증가
- 순서 보장: 같은 키는 같은 파티션으로

Usage:
    from selfhealing.adapters.kafka.retry import (
        RetryTopicConfig,
        NonBlockingRetryHandler,
    )

    config = RetryTopicConfig(
        main_topic="audit.events",
        retry_delays=[60, 300, 900],  # 1분, 5분, 15분
    )

    handler = NonBlockingRetryHandler(config, producer)

    # 실패 시 재시도 토픽으로 전송
    handler.handle_failure(message, retry_count=0, error=exception)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from selfhealing.adapters.kafka.producer import KafkaAuditProducer

logger = logging.getLogger(__name__)


@dataclass
class RetryTopicConfig:
    """
    Retry 토픽 설정.

    각 재시도 단계별 토픽과 지연 시간을 정의합니다.
    """

    main_topic: str
    """메인 토픽 이름 (프리픽스 제외)."""

    retry_delays: list[int] = field(default_factory=lambda: [60, 300, 900])
    """재시도 지연 시간 목록 (초). 기본: 1분, 5분, 15분"""

    dlq_topic: str | None = None
    """DLQ 토픽 이름. None이면 자동 생성."""

    max_retries: int | None = None
    """최대 재시도 횟수. None이면 retry_delays 길이 사용."""

    def __post_init__(self) -> None:
        """설정 검증."""
        # 빈 리스트인 경우 먼저 기본값 설정
        if not self.retry_delays:
            self.retry_delays = [60]  # 최소 1회

        # max_retries는 retry_delays 길이 기반으로 계산
        if self.max_retries is None:
            self.max_retries = len(self.retry_delays)

    @property
    def retry_topics(self) -> list[str]:
        """Retry 토픽 목록 생성."""
        return [f"{self.main_topic}.retry.{i + 1}" for i in range(len(self.retry_delays))]

    @property
    def final_dlq_topic(self) -> str:
        """최종 DLQ 토픽."""
        return self.dlq_topic or f"{self.main_topic}.dlq"


# =============================================================================
# 헤더 상수
# =============================================================================

RETRY_COUNT_HEADER = "x-retry-count"
RETRY_DELAY_HEADER = "x-retry-delay-ms"
ORIGINAL_TOPIC_HEADER = "x-original-topic"
DLQ_REASON_HEADER = "x-dlq-reason"
FIRST_FAILURE_HEADER = "x-first-failure-time"


class NonBlockingRetryHandler:
    """
    Non-Blocking Retry 핸들러.

    실패한 메시지를 Retry 토픽으로 전송하여
    메인 토픽 처리를 차단하지 않고 재시도합니다.
    """

    def __init__(
        self,
        config: RetryTopicConfig,
        producer: KafkaAuditProducer,
    ):
        """
        NonBlockingRetryHandler 초기화.

        Args:
            config: Retry 토픽 설정
            producer: Kafka Producer 인스턴스
        """
        self._config = config
        self._producer = producer
        self._stats = {
            "retries_sent": 0,
            "dlq_sent": 0,
            "errors": 0,
        }

    def handle_failure(
        self,
        message: dict[str, Any],
        retry_count: int,
        error: Exception,
        key: str | None = None,
    ) -> bool:
        """
        실패 메시지 처리.

        재시도 가능하면 다음 Retry 토픽으로 전송하고,
        최대 횟수 초과 시 DLQ로 전송합니다.

        Args:
            message: 원본 메시지 데이터
            retry_count: 현재 재시도 횟수 (0부터 시작)
            error: 발생한 예외
            key: 파티션 키 (순서 보장용)

        Returns:
            전송 성공 여부
        """
        max_retries = self._config.max_retries or len(self._config.retry_delays)

        if retry_count >= max_retries:
            # 최대 재시도 초과 → DLQ
            return self._send_to_dlq(message, error, key, retry_count)
        else:
            # 다음 Retry 토픽으로 전송
            return self._send_to_retry(message, retry_count, error, key)

    def _send_to_retry(
        self,
        message: dict[str, Any],
        retry_count: int,
        error: Exception,
        key: str | None,
    ) -> bool:
        """Retry 토픽으로 전송."""
        try:
            retry_topic = self._config.retry_topics[retry_count]
            delay_ms = self._config.retry_delays[retry_count] * 1000

            # 헤더 구성
            headers = {
                RETRY_COUNT_HEADER.encode(): str(retry_count + 1).encode(),
                RETRY_DELAY_HEADER.encode(): str(delay_ms).encode(),
                ORIGINAL_TOPIC_HEADER.encode(): self._config.main_topic.encode(),
            }

            # 첫 실패 시간 기록
            if retry_count == 0:
                headers[FIRST_FAILURE_HEADER.encode()] = str(time.time()).encode()

            # 에러 정보 추가
            message["_retry_info"] = {
                "retry_count": retry_count + 1,
                "delay_seconds": self._config.retry_delays[retry_count],
                "error_message": str(error),
                "error_type": type(error).__name__,
            }

            success = self._producer.publish(
                topic=retry_topic,
                event=message,
                key=key,
                headers=headers,
            )

            if success:
                self._stats["retries_sent"] += 1
                logger.info(f"[RetryHandler] Retry 토픽으로 전송: " f"topic={retry_topic}, count={retry_count + 1}")
            else:
                self._stats["errors"] += 1
                logger.error(f"[RetryHandler] Retry 토픽 전송 실패: topic={retry_topic}")

            return success

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"[RetryHandler] Retry 전송 오류: {e}")
            return False

    def _send_to_dlq(
        self,
        message: dict[str, Any],
        error: Exception,
        key: str | None,
        retry_count: int,
    ) -> bool:
        """DLQ로 전송."""
        try:
            # 헤더 구성
            headers = {
                DLQ_REASON_HEADER.encode(): str(error).encode()[:500],  # 최대 500바이트
                ORIGINAL_TOPIC_HEADER.encode(): self._config.main_topic.encode(),
                RETRY_COUNT_HEADER.encode(): str(retry_count).encode(),
            }

            # DLQ 메타데이터 추가
            message["_dlq_info"] = {
                "original_topic": self._config.main_topic,
                "retry_count": retry_count,
                "error_message": str(error),
                "error_type": type(error).__name__,
                "dlq_timestamp": time.time(),
            }

            success = self._producer.publish(
                topic=self._config.final_dlq_topic,
                event=message,
                key=key,
                headers=headers,
            )

            if success:
                self._stats["dlq_sent"] += 1
                logger.warning(
                    f"[RetryHandler] DLQ로 이동: "
                    f"topic={self._config.final_dlq_topic}, "
                    f"retries={retry_count}, error={error}"
                )
            else:
                self._stats["errors"] += 1
                logger.error(f"[RetryHandler] DLQ 전송 실패: " f"topic={self._config.final_dlq_topic}")

            return success

        except Exception as e:
            self._stats["errors"] += 1
            logger.error(f"[RetryHandler] DLQ 전송 오류: {e}")
            return False

    def get_stats(self) -> dict[str, int]:
        """처리 통계 반환."""
        return dict(self._stats)

    def extract_retry_count(self, headers: dict[str, bytes]) -> int:
        """
        헤더에서 재시도 횟수 추출.

        Args:
            headers: 메시지 헤더

        Returns:
            재시도 횟수 (없으면 0)
        """
        retry_header = headers.get(RETRY_COUNT_HEADER.encode())
        if retry_header:
            try:
                return int(retry_header.decode())
            except (ValueError, UnicodeDecodeError):
                pass
        return 0

    def is_retry_message(self, headers: dict[str, bytes]) -> bool:
        """
        재시도 메시지 여부 확인.

        Args:
            headers: 메시지 헤더

        Returns:
            재시도 메시지 여부
        """
        return ORIGINAL_TOPIC_HEADER.encode() in headers

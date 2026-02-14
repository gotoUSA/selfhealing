"""
ThrottleDLQSink — Throttle 거부 요청을 DLQ에 저장하는 Sink.

AdaptiveThrottle.check()에서 거부 시 _auto_store_rejection_to_dlq()로
호출되던 DLQ 저장 로직을 독립 컴포넌트로 분리한다.

FailureSink Protocol과의 관계:
    FailureSink.handle_failure(error, context, policy_result)는
    파이프라인 종단의 "실행 실패" 처리를 위한 인터페이스이다.
    Throttle 거부는 "실행 이전의 정책 결정"이므로 실행 실패가 아니다.
    따라서 ThrottleDLQSink는 FailureSink를 구현하지 않고
    handle_rejection(context, reason)이라는 거부 전용 메서드를 제공한다.

    이는 기존 AdaptiveThrottle의 동작과 일치한다:
    - _auto_store_rejection_to_dlq(context, reason) — 거부 전용 저장
    - ThrottleDLQReplayMixin.store_throttle_rejection_to_dlq() 위임

Fail-Open 원칙:
    DLQ 서비스 미사용 또는 예외 시 무시하고 Throttle 동작을 계속한다.

사용 예시::

    sink = ThrottleDLQSink()
    sink.handle_rejection(
        context={"order_id": "123", "domain": "payment"},
        reason="rate_limit_exceeded",
    )
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ThrottleDLQSink:
    """
    Throttle 거부 요청을 DLQ에 저장하는 Sink.

    AdaptiveThrottle의 ThrottleDLQReplayMixin과
    _auto_store_rejection_to_dlq()를 독립 컴포넌트로 분리한 것이다.

    Fail-Open: DLQ 모듈 미설치 또는 저장 실패 시 무시한다.
    """

    def __init__(self, dlq_integration: Any | None = None) -> None:
        """
        초기화.

        Args:
            dlq_integration: ThrottleDLQIntegration 인스턴스.
                None이면 lazy import로 글로벌 인스턴스를 사용한다.
        """
        self._dlq = dlq_integration

    def handle_rejection(
        self,
        context: dict[str, Any],
        reason: str,
    ) -> None:
        """
        거부된 요청을 DLQ에 저장.

        AdaptiveThrottle._auto_store_rejection_to_dlq()과 동일한 동작.
        context에서 domain, order_id, user_id 등을 추출하여 DLQ에 기록한다.

        Args:
            context: 요청 컨텍스트 (domain, order_id, tier_id 등)
            reason: 거부 사유 ("rate_limit_exceeded" 등)
        """
        try:
            integration = self._get_dlq_integration()
            if integration is None:
                return

            integration.store_denied_request(
                service_name=context.get("service_name", "unknown"),
                domain=context.get("domain", ""),
                tier_id=context.get("tier_id", "standard"),
                reason=reason,
                request_data=context,
            )
        except Exception as e:
            logger.debug(
                "[ThrottleDLQSink] DLQ storage failed (fail-open): %s",
                e,
            )

    def _get_dlq_integration(self) -> Any | None:
        """DLQ integration 인스턴스 획득 (lazy import, Fail-Open)."""
        if self._dlq is not None:
            return self._dlq

        try:
            from selfhealing.services.throttle.dlq_integration import (
                get_throttle_dlq_integration,
            )

            self._dlq = get_throttle_dlq_integration()
            return self._dlq
        except ImportError:
            logger.debug(
                "[ThrottleDLQSink] DLQ integration not available (fail-open)",
            )
            return None
        except Exception as e:
            logger.debug(
                "[ThrottleDLQSink] DLQ integration init failed " "(fail-open): %s",
                e,
            )
            return None

"""
Retry Policy Sinks — DLQ(Dead Letter Queue) 최종 실패 처리.

모든 Policy가 소진된 후 최종 실패를 DLQ에 저장하는 Sink 구현.
should_dlq 플래그 기반 Dumb Sink: 저장 여부 판단은 RetryPolicy가 담당한다.
"""

from __future__ import annotations

from typing import Any

import structlog

from selfhealing.interfaces.resilience_policy import PolicyContext, PolicyResult

logger = structlog.get_logger()


class DLQSink:
    """
    DLQ(Dead Letter Queue)에 최종 실패를 저장하는 Sink.

    PolicyResult.metadata["should_dlq"] 플래그만 확인하고,
    True이면 저장, False이면 스킵한다 (Dumb Sink 패턴).
    저장 여부 판단 로직은 RetryPolicy가 config.enable_dlq로 마킹한다.
    """

    def handle_failure(
        self,
        error: Exception,
        context: PolicyContext | None,
        policy_result: PolicyResult,
    ) -> str | None:
        """
        최종 실패를 DLQ에 저장.

        Args:
            error: 최종 실패 예외
            context: PolicyContext (order_id, user_id 등)
            policy_result: 파이프라인 전체 결과

        Returns:
            DLQ 레코드 ID 문자열, 또는 None (저장하지 않은 경우)
        """
        if not policy_result.metadata.get("should_dlq", False):
            return None

        return self._store_to_dlq(error, context, policy_result)

    @staticmethod
    def _build_dlq_metadata(policy_result: PolicyResult) -> tuple[dict[str, Any], str]:
        """DLQ 저장을 위한 메타데이터와 도메인을 구성."""
        domain = policy_result.metadata.get("domain", "default")
        metadata: dict[str, Any] = {
            "retry_history": policy_result.metadata.get("retry_history", []),
            "max_attempts": policy_result.metadata.get("max_attempts"),
            "domain": domain,
            "final_attempt": policy_result.total_attempts,
            "executed_policies": policy_result.executed_policies,
        }
        return metadata, domain

    @staticmethod
    def _extract_context_fields(context: PolicyContext | None) -> dict[str, Any]:
        """Context에서 비즈니스 식별자와 데이터를 추출."""
        extra = context.extra if context and context.extra else {}
        user_id_raw = extra.get("user_id")
        return {
            "entity_id": context.order_id if context else None,
            "user_id": int(user_id_raw) if user_id_raw is not None else None,
            "snapshot_data": extra.get("snapshot_data", {}),
            "request_data": extra.get("request_data", {}),
            "response_data": extra.get("response_data", {}),
        }

    def _store_to_dlq(
        self,
        error: Exception,
        context: PolicyContext | None,
        policy_result: PolicyResult,
    ) -> str | None:
        """DLQ 서비스를 호출하여 실패를 저장."""
        try:
            from selfhealing.services.dlq import store_to_dlq

            error_type = type(error).__name__ if error else "Unknown"
            metadata, domain = self._build_dlq_metadata(policy_result)
            ctx_fields = self._extract_context_fields(context)

            result = store_to_dlq(
                domain=domain,
                failure_type=f"MAX_RETRIES_{error_type.upper()}",
                entity_id=ctx_fields["entity_id"],
                user_id=ctx_fields["user_id"],
                error_code=error_type,
                error_message=str(error)[:1000] if error else "",
                snapshot_data=ctx_fields["snapshot_data"],
                request_data=ctx_fields["request_data"],
                response_data=ctx_fields["response_data"],
                metadata=metadata,
                next_action_hint="Review error and retry if transient",
                recommended_action="manual_check",
            )

            if result.success:
                logger.info(
                    "dlq_sink.created_dlq_entry",
                    result=result.dlq_id,
                )
                return str(result.dlq_id) if result.dlq_id is not None else None
            else:
                logger.error(
                    "dlq_sink.failed_create_dlq_entry",
                    result=result.error,
                )
                return None

        except Exception as dlq_error:
            logger.exception(
                "dlq_sink.failed_create_dlq_entry",
                dlq_error=dlq_error,
            )
            return None

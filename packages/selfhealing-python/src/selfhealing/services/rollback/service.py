"""
Rollback DNA Service - 롤백 관리 서비스
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from threading import Lock

import structlog

from .models import (
    RollbackPolicy,
    RollbackRequest,
    RollbackResult,
    RollbackState,
    RollbackStrategy,
)

logger = structlog.get_logger()


class RollbackService:
    """
    Rollback DNA 서비스

    안전한 자동 롤백을 관리합니다.
    """

    _instance: RollbackService | None = None
    _lock = Lock()

    def __new__(cls) -> RollbackService:
        """싱글톤 패턴"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._policies: dict[str, RollbackPolicy] = {}
        self._requests: dict[str, RollbackRequest] = {}
        self._results: dict[str, RollbackResult] = {}
        self._rollback_handlers: dict[str, Callable] = {}
        self._enabled = True
        self._initialized = True

        logger.info("rollback_service.initialized")

    def set_policy(
        self,
        stage_name: str,
        strategy: RollbackStrategy = RollbackStrategy.AUTOMATIC,
        timeout_seconds: int = 120,
        max_retries: int = 3,
        require_approval: bool = False,
    ) -> RollbackPolicy:
        """
        롤백 정책 설정

        Args:
            stage_name: Stage 이름
            strategy: 롤백 전략
            timeout_seconds: 타임아웃 (초)
            max_retries: 최대 재시도 횟수
            require_approval: 승인 필요 여부

        Returns:
            RollbackPolicy: 설정된 정책
        """
        policy_id = str(uuid.uuid4())[:8]
        policy = RollbackPolicy(
            policy_id=policy_id,
            stage_name=stage_name,
            strategy=strategy,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            require_approval=require_approval,
        )
        self._policies[stage_name] = policy
        logger.info(
            "rollback_policy_set",
            stage_name=stage_name,
            strategy=strategy.value,
        )
        return policy

    def get_policy(self, stage_name: str) -> RollbackPolicy | None:
        """정책 조회"""
        return self._policies.get(stage_name)

    def register_handler(
        self,
        component: str,
        handler: Callable[[RollbackRequest], bool],
    ) -> None:
        """
        롤백 핸들러 등록

        Args:
            component: 컴포넌트 이름
            handler: 롤백 실행 함수
        """
        self._rollback_handlers[component] = handler
        logger.info(
            "rollback_handler_registered",
            component=component,
        )

    def _log_audit(
        self,
        request_id: str,
        stage_name: str,
        state: str,
        triggered_by: str,
        reason: str | None = None,
        source_version: str | None = None,
        target_version: str | None = None,
        affected_components: list[str] | None = None,
        errors: list[str] | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """
        롤백 이벤트를 Audit 로그에 기록.

        Fail-Open 원칙: Audit 실패가 롤백 로직을 중단시키지 않음.
        """
        try:
            from selfhealing.services.audit import log_rollback_audit

            log_rollback_audit(
                request_id=request_id,
                stage_name=stage_name,
                state=state,
                triggered_by=triggered_by,
                reason=reason,
                source_version=source_version,
                target_version=target_version,
                affected_components=affected_components,
                errors=errors,
                duration_seconds=duration_seconds,
            )
        except Exception as e:
            # Fail-Open: Audit 실패가 롤백을 중단시키지 않음
            logger.debug(
                "rollback_service.audit_logging_failed_ignored",
                error=e,
            )

    def request_rollback(
        self,
        stage_name: str,
        reason: str,
        triggered_by: str = "system",
        source_version: str = "",
        target_version: str = "",
        metadata: dict | None = None,
    ) -> RollbackRequest:
        """
        롤백 요청 생성

        Args:
            stage_name: Stage 이름
            reason: 롤백 이유
            triggered_by: 트리거 주체
            source_version: 원본 버전
            target_version: 대상 버전
            metadata: 추가 메타데이터

        Returns:
            RollbackRequest: 생성된 요청
        """
        request_id = str(uuid.uuid4())[:8]
        request = RollbackRequest(
            request_id=request_id,
            stage_name=stage_name,
            reason=reason,
            triggered_by=triggered_by,
            source_version=source_version,
            target_version=target_version,
            metadata=metadata or {},
        )
        self._requests[request_id] = request

        # 결과 초기화
        self._results[request_id] = RollbackResult(
            request_id=request_id,
            state=RollbackState.PENDING,
        )

        logger.info(
            "rollback_requested",
            request_id=request_id,
            stage_name=stage_name,
        )

        # Audit 기록: 롤백 요청
        self._log_audit(
            request_id=request_id,
            stage_name=stage_name,
            state="pending",
            triggered_by=triggered_by,
            reason=reason,
            source_version=source_version,
            target_version=target_version,
        )

        # 자동 실행 체크
        policy = self._policies.get(stage_name)
        if policy and policy.strategy == RollbackStrategy.AUTOMATIC:
            if not policy.require_approval:
                self.execute_rollback(request_id)

        return request

    def execute_rollback(
        self,
        request_id: str,
        components: list[str] | None = None,
    ) -> RollbackResult:
        """
        롤백 실행

        Args:
            request_id: 요청 ID
            components: 롤백할 컴포넌트 (None이면 전체)

        Returns:
            RollbackResult: 롤백 결과
        """
        request = self._requests.get(request_id)
        if not request:
            return RollbackResult(
                request_id=request_id,
                state=RollbackState.FAILED,
                message="Request not found",
            )

        result = self._results[request_id]
        result.state = RollbackState.IN_PROGRESS
        result.started_at = datetime.now(timezone.utc)

        policy = self._policies.get(request.stage_name)
        timeout = policy.timeout_seconds if policy else 120

        logger.info(
            "executing_rollback",
            request_id=request_id,
        )

        try:
            start_time = time.time()
            errors = []
            affected = []

            # 핸들러 실행
            handlers_to_run = components or list(self._rollback_handlers.keys())

            for component in handlers_to_run:
                if component in self._rollback_handlers:
                    try:
                        handler = self._rollback_handlers[component]
                        success = handler(request)
                        if success:
                            affected.append(component)
                        else:
                            errors.append(f"{component}: handler returned False")
                    except Exception as e:
                        errors.append(f"{component}: {str(e)}")

                # 타임아웃 체크
                if time.time() - start_time > timeout:
                    errors.append("Rollback timeout exceeded")
                    break

            result.duration_seconds = time.time() - start_time
            result.affected_components = affected
            result.errors = errors

            if errors and affected:
                result.state = RollbackState.PARTIALLY_COMPLETED
                result.message = f"Rollback partially completed: " f"{len(affected)} succeeded, {len(errors)} failed"
            elif errors:
                result.state = RollbackState.FAILED
                result.message = f"Rollback failed with {len(errors)} errors"
            else:
                result.state = RollbackState.COMPLETED
                result.message = "Rollback completed successfully"

        except Exception as e:
            result.state = RollbackState.FAILED
            result.message = str(e)
            result.errors.append(str(e))
            logger.exception(
                "rollback_failed",
                error=e,
            )

        finally:
            result.completed_at = datetime.now(timezone.utc)

            # Audit 기록: 롤백 완료/실패
            self._log_audit(
                request_id=request_id,
                stage_name=request.stage_name,
                state=(result.state.value if hasattr(result.state, "value") else str(result.state)),
                triggered_by=request.triggered_by,
                reason=request.reason,
                source_version=request.source_version,
                target_version=request.target_version,
                affected_components=result.affected_components,
                errors=result.errors,
                duration_seconds=result.duration_seconds,
            )

        return result

    def cancel_rollback(self, request_id: str) -> bool:
        """
        롤백 취소

        Args:
            request_id: 요청 ID

        Returns:
            bool: 성공 여부
        """
        result = self._results.get(request_id)
        if result and result.state == RollbackState.PENDING:
            result.state = RollbackState.CANCELLED
            result.message = "Rollback cancelled"
            result.completed_at = datetime.now(timezone.utc)
            logger.info(
                "rollback_cancelled",
                request_id=request_id,
            )
            return True
        return False

    def get_result(self, request_id: str) -> RollbackResult | None:
        """롤백 결과 조회"""
        return self._results.get(request_id)

    def get_pending_requests(self, stage_name: str | None = None) -> list[RollbackRequest]:
        """대기 중인 요청 조회"""
        pending = [
            req
            for req_id, req in self._requests.items()
            if self._results.get(req_id) and self._results[req_id].state == RollbackState.PENDING
        ]
        if stage_name:
            pending = [r for r in pending if r.stage_name == stage_name]
        return pending

    def get_history(
        self,
        stage_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """
        롤백 이력 조회

        Args:
            stage_name: Stage 이름 필터
            limit: 최대 개수

        Returns:
            List[Dict]: 이력 목록
        """
        history = []
        for req_id, request in list(self._requests.items())[-limit:]:
            if stage_name and request.stage_name != stage_name:
                continue
            result = self._results.get(req_id)
            history.append(
                {
                    "request": request.to_dict(),
                    "result": result.to_dict() if result else None,
                }
            )
        return history

    def enable(self) -> None:
        """서비스 활성화"""
        self._enabled = True

    def disable(self) -> None:
        """서비스 비활성화"""
        self._enabled = False

    def clear(self) -> None:
        """모든 데이터 초기화 (테스트용)"""
        self._policies.clear()
        self._requests.clear()
        self._results.clear()

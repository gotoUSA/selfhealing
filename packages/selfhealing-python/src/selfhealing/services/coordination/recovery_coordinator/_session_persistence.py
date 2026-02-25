"""
SessionPersistenceMixin for RecoveryCoordinator.

이 모듈은 selfhealing.services.coordination.recovery_coordinator 패키지의 내부 구현입니다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import structlog

from selfhealing.settings.recovery_coordinator import get_recovery_coordinator_settings

from ..enums import CompensationStatus, RecoveryStatus
from ..recovery_state import CompensationResult, RecoverySession
from . import SESSION_CAS_SCRIPT, SessionVersionConflictError, StepTimeoutError

logger = structlog.get_logger()


class SessionPersistenceMixin:
    """세션 영속화 Mixin.

    세션 저장, 상태 관리, DLQ 저장, 보상 로직을 제공합니다."""

    def _save_session(self, session: RecoverySession) -> None:
        """세션 저장 (OCC 적용).

        저장 시 version을 +1 증가시키고, Redis에서 현재 version과
        일치할 때만 저장한다. 불일치 시 SessionVersionConflictError.

        좀비 스레드의 뒤늦은 저장 시도를 차단한다.
        """
        expected_version = session.version
        session.version += 1  # 저장 전 version 증가

        backend = self._get_backend()
        key = self.SESSION_KEY.format(
            namespace=session.namespace,
            session_id=session.id,
        )

        # RedisStateBackend인 경우 CAS 적용
        if hasattr(backend, "_client"):
            try:
                data = json.dumps(session.to_dict(), default=str)
                result = backend._client.eval(
                    SESSION_CAS_SCRIPT,
                    1,
                    backend._make_key(key),
                    data,
                    str(expected_version),
                )
                if result == 0:
                    session.version = expected_version  # 롤백
                    raise SessionVersionConflictError(
                        session.id,
                        expected_version,
                        session.version,
                    )
            except SessionVersionConflictError:
                raise
            except Exception:
                # CAS 실패 시 Fail-Open: 기존 방식으로 폴백
                backend.set(key, session.to_dict())
        else:
            # InMemory/File 백엔드: 단순 저장 (self._lock으로 이미 보호)
            backend.set(key, session.to_dict())

    def _set_active_session(
        self,
        namespace: str,
        session_id: str,
    ) -> None:
        """활성 세션 설정."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.set(key, session_id)

    def _clear_active_session(self, namespace: str) -> None:
        """활성 세션 클리어."""
        backend = self._get_backend()
        key = self.ACTIVE_SESSION_KEY.format(namespace=namespace)
        backend.delete(key)

    def _complete_session(self, session: RecoverySession) -> None:
        """세션 완료 처리."""
        session.status = RecoveryStatus.COMPLETED
        session.completed_at = datetime.now(timezone.utc).isoformat()

        # Phase 5.3: 복구 완료 감사 기록
        self._record_recovery_completed(session)

        self._save_session(session)
        self._clear_active_session(session.namespace)

        # 락 해제
        self._recovery_lock.release(session.namespace, session.id)

        # EMERGENCY_RECOVERY_COMPLETED 이벤트 발행 (Postmortem 자동 생성 트리거)
        self._publish_emergency_recovery_completed_event(session)

        logger.info(
            "recovery.completed",
            recovery_session_id=session.id,
            namespace=session.namespace,
        )

    def _fail_session(
        self,
        session: RecoverySession,
        error: str,
    ) -> None:
        """세션 실패 처리 + 등록된 compensate 핸들러 역순 실행 + DLQ 자동 저장.

        ACTIVE_SESSION_KEY를 유지하여 resume_recovery()가
        실패 세션을 조회할 수 있도록 한다.
        start_recovery()의 중복 체크에서 FAILED 상태는
        차단 대상이 아니므로 새 복구 시작에 영향 없음.

        보상 핸들러가 session.abort_reason으로 실패 원인을 참조할 수 있도록
        abort_reason을 보상 루프 전에 설정한다.

        실행 순서:
        1. abort_reason 설정 → 보상 시도
        2. FAILED 상태 영속화
        3. logger.error() — SIGKILL 방어 (Lock 해제 전에 로그 남김)
        4. Lock 해제 — DLQ 저장 지연 시 Lock 보유 시간 최소화
        5. DLQ 저장 — Lock-free 구간, Fail-Open (실패해도 세션 처리 무중단)
        """
        # abort_reason을 보상 루프 전에 설정
        # compensate 핸들러가 session.abort_reason으로 실패 원인 참조 가능
        session.abort_reason = error

        # COMPENSATING 상태로 전환 (보상 진행 중 표시)
        session.status = RecoveryStatus.COMPENSATING
        self._save_session(session)

        # 이미 완료된 Step 역순 보상 시도
        comp_result = self._attempt_compensation(session)

        # 보상 실패 Step이 있으면 로깅
        if not comp_result.all_compensated:
            logger.warning(
                "recovery.compensation_incomplete",
                recovery_session_id=session.id,
                failed_steps_count=len(comp_result.failed_steps),
                skipped_steps_count=len(comp_result.skipped_steps),
            )

        # 최종 실패 상태 설정
        session.status = RecoveryStatus.FAILED
        session.completed_at = datetime.now(timezone.utc).isoformat()
        self._save_session(session)
        # ACTIVE_SESSION_KEY 유지: resume_recovery()가 실패 세션을 조회할 수 있도록
        # start_recovery()가 _set_active_session()으로 기존 키를 덮어쓰므로
        # 새 복구 시작 시 자연스럽게 교체됨

        # 로그 선행 — SIGKILL 방어 (Lock 해제 전)
        # Lock 해제 후 DLQ 저장 전에 프로세스가 죽어도
        # 최소한 로그 시스템에 흔적이 남음
        logger.error(
            "recovery.failed",
            recovery_session_id=session.id,
            error=error,
        )

        # 락 해제 — DLQ 저장보다 먼저 (Lock 보유 시간 최소화)
        # 세션이 이미 FAILED 상태로 영속화되었으므로,
        # 다른 Worker가 Lock을 획득해도 execute_next_step()에서
        # IN_PROGRESS/HEALTH_CHECK 상태 체크에 의해 차단됨
        self._recovery_lock.release(session.namespace, session.id)

        # DLQ 자동 저장 (Lock-free 구간, Fail-Open)
        # comp_result를 함께 전달하여 보상 실패를 1건으로 Aggregation
        self._store_failure_to_dlq(session, error, comp_result)

    def _store_failure_to_dlq(
        self,
        session: RecoverySession,
        error: str,
        comp_result: CompensationResult | None = None,
    ) -> None:
        """복구 실패 정보를 DLQ에 1건으로 저장.

        보상 실패 목록(comp_result.failed_steps)을 Aggregation하여
        세션 실패 DLQ 1건에 통합 저장한다.
        보상 실패마다 개별 DLQ를 생성하지 않으므로 알림 폭주를 방지한다.

        Fail-Open: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음.
        DLQ 자체의 3단계 Fallback(LMDB → JSONL → stderr)이 무손실을 보장함.

        Args:
            session: 실패한 RecoverySession
            error: 에러 메시지
            comp_result: 보상 실행 결과 (None이면 보상 정보 미포함)
        """
        try:
            from selfhealing.audit.masking import mask_sensitive_fields
            from selfhealing.services.dlq import store_to_dlq

            # --- Failed Step 탐색 (status 기반) ---
            # DLQ는 사후 분석 용도이므로 current_step_index 커서가 아닌
            # 실제 결과(status)를 기준으로 실패 Step을 탐색한다.
            failed_step = next(
                (s for s in session.steps if s.status == RecoveryStatus.FAILED),
                None,
            )
            failed_step_info = None
            if failed_step:
                failed_step_info = {
                    "step_type": failed_step.step_type.value,
                    "order": failed_step.order,
                    "started_at": failed_step.started_at,
                    "error_message": failed_step.error_message,
                    "params": failed_step.params,
                }

            # --- 완료된 Step 목록 ---
            completed_steps = [
                {
                    "step_type": step.step_type.value,
                    "order": step.order,
                    "completed_at": step.completed_at,
                }
                for step in session.steps
                if step.status == RecoveryStatus.COMPLETED
            ]

            # --- 보상 실패 Aggregation ---
            # CompensationResult.failed_steps를 DLQ 1건에 리스트로 집약.
            # 네트워크 단절로 5개 보상이 모두 실패해도 알림은 1건만 발생한다.
            compensation_failures = None
            compensation_summary = None
            has_compensation_failures = False

            if comp_result and comp_result.failed_steps:
                has_compensation_failures = True
                compensation_failures = [
                    {
                        "step_type": step.step_type.value,
                        "order": step.order,
                        "compensation_error": err,
                        "forward_result": step.result_data,
                    }
                    for step, err in comp_result.failed_steps
                ]
                # 요약 문자열: 운영자가 JSON을 펼치지 않고 심각도를 파악
                compensation_summary = ", ".join(
                    f"{step.step_type.value} ({err[:60]})" for step, err in comp_result.failed_steps
                )

            # --- PII 마스킹 ---
            # mask_sensitive_fields()는 새 dict를 반환하므로 deepcopy 불필요.
            # session.to_dict()도 새 dict를 생성하므로 원본 객체에 영향 없음.
            snapshot = mask_sensitive_fields(session.to_dict())

            # --- recommended_action 동적 분기 ---
            # 보상 실패가 있으면 데이터 불일치 가능성 → 더 강한 액션 권고
            recommended_action = "manual_consistency_check" if has_compensation_failures else "manual_review"

            # --- next_action_hint 구체화 ---
            # result_data의 키를 포함하여 운영자가 무엇을 확인해야 하는지 안내
            if has_compensation_failures:
                affected_details = []
                for step, _err in comp_result.failed_steps:
                    keys = list(step.result_data.keys()) if step.result_data else []
                    keys_str = f" (affected: {', '.join(keys)})" if keys else ""
                    affected_details.append(f"{step.step_type.value}{keys_str}")
                next_action_hint = (
                    f"Recovery session {session.id} failed. "
                    f"Automatic compensation failed for: "
                    f"{', '.join(affected_details)}. "
                    f"Verify affected state manually before resume_recovery()."
                )
            else:
                step_name = failed_step.step_type.value if failed_step else "unknown"
                next_action_hint = (
                    f"Recovery session {session.id} failed at step {step_name}. "
                    f"Check session state and consider resume_recovery()."
                )

            # --- metadata 구성 ---
            metadata = {
                "namespace": session.namespace,
                "trigger_level": session.trigger_level,
                "initiated_by": session.initiated_by,
                "failed_step": failed_step_info,
                "completed_steps": completed_steps,
                "completed_count": len(completed_steps),
                "total_steps": len(session.steps),
            }

            # 보상 실패 정보 (Aggregation)
            if compensation_failures:
                metadata["compensation_failures"] = compensation_failures
                metadata["compensation_summary"] = compensation_summary

            store_to_dlq(
                domain="selfhealing",
                failure_type="RECOVERY_SESSION_FAILED",
                entity_type="recovery_session",
                entity_id=session.id,
                error_message=error,
                snapshot_data={
                    "session": snapshot,
                },
                metadata=metadata,
                next_action_hint=next_action_hint,
                recommended_action=recommended_action,
            )

            logger.info(
                "recovery.failure_stored_dlq",
                recovery_session_id=session.id,
                compensation_failures_count=len(compensation_failures or []),
            )

        except Exception as e:
            # Fail-Open: DLQ 저장 실패가 복구 실패 처리를 중단시키지 않음
            logger.warning(
                "recovery.dlq_store_failed_ignored",
                recovery_session_id=session.id,
                error=e,
            )

    def _attempt_compensation(
        self,
        session: RecoverySession,
    ) -> CompensationResult:
        """
        완료된 Step을 역순으로 보상 시도.

        보상 핸들러에도 타임아웃을 적용한다.
        Forward 핸들러가 외부 API 타임아웃으로 실패했다면,
        동일 API를 호출하는 보상 핸들러도 hang될 수 있으므로
        별도의 compensation_step_timeout_seconds를 적용한다.

        설계 원칙:
        - Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음.
        - At-least-once: 보상은 최소 1회 실행을 보장.
          compensate 핸들러는 반드시 멱등성(Idempotency)을 가져야 한다.
        - compensate 핸들러가 등록되지 않은 Step은 건너뜀.

        Returns:
            CompensationResult — 보상 성공/실패/건너뜀 Step 목록.
        """
        result = CompensationResult()

        completed_steps = [step for step in session.steps if step.status == RecoveryStatus.COMPLETED]

        # 역순 정렬 (order 기준 내림차순)
        completed_steps.sort(key=lambda s: s.order, reverse=True)

        settings = get_recovery_coordinator_settings()
        comp_timeout = settings.compensation_step_timeout_seconds

        for step in completed_steps:
            # 매 Step 보상 전 Lock TTL 연장 (하트비트)
            self._recovery_lock.extend(
                session.namespace,
                session.id,
                additional_seconds=300,  # 5분 연장
            )

            compensate_handler = self._compensate_handlers.get(step.step_type)
            if compensate_handler is None:
                logger.debug(
                    "recovery.no_compensate_handler_skipping",
                    step_type=step.step_type.value,
                )
                result.skipped_steps.append(step)
                continue

            # 이미 보상 완료된 Step은 건너뜀 (재시작 안전성)
            if step.compensation_status == CompensationStatus.COMPENSATED:
                logger.debug(
                    "recovery.already_compensated_skipping",
                    step_type=step.step_type.value,
                )
                result.compensated_steps.append(step)
                continue

            try:
                # 보상에도 타임아웃 적용 (Forward와 독립적 설정)
                handler_fn = lambda s=step: compensate_handler(session, s)
                handler_result = self._execute_with_timeout(
                    handler_fn,
                    comp_timeout,
                    step,
                    session,
                )

                if handler_result.get("success"):
                    # 보상 성공 즉시 상태 저장 (서버 재시작 대비)
                    step.compensation_status = CompensationStatus.COMPENSATED
                    self._save_session(session)

                    result.compensated_steps.append(step)
                    logger.info(
                        "recovery.compensated",
                        step_type=step.step_type.value,
                        recovery_session_id=session.id,
                    )
                else:
                    error_msg = handler_result.get("error", "Unknown error")
                    step.compensation_status = CompensationStatus.COMPENSATE_FAILED
                    self._save_session(session)

                    result.failed_steps.append((step, error_msg))
                    logger.warning(
                        "recovery.compensation_failed",
                        step_type=step.step_type.value,
                        error_msg=error_msg,
                    )
            except StepTimeoutError as e:
                # 보상 타임아웃도 COMPENSATE_FAILED 처리
                step.compensation_status = CompensationStatus.COMPENSATE_FAILED
                self._save_session(session)
                result.failed_steps.append((step, str(e)))
                logger.warning(
                    "recovery.compensation_timeout",
                    step_type=step.step_type.value,
                    comp_timeout=comp_timeout,
                )
            except Exception as e:
                step.compensation_status = CompensationStatus.COMPENSATE_FAILED
                self._save_session(session)

                result.failed_steps.append((step, str(e)))
                logger.warning(
                    "recovery.compensation_exception",
                    step_type=step.step_type.value,
                    error=e,
                )
                # Fail-Open: 보상 실패가 세션 실패 처리를 중단시키지 않음

        return result

    # =========================================================================
    # Status & History Methods
    # =========================================================================

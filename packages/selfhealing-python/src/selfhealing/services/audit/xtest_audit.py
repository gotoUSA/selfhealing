"""
X-Test-Mode Audit Helpers

X-Test-Mode에서 수행된 모든 작업을 WAL 기반 Audit 시스템에 기록.
세션 단위 추적 및 테스트 증적 보존을 제공합니다.

Usage:
    from selfhealing.services.audit.xtest_audit import (
        log_xtest_operation_audit,
        log_xtest_scenario_audit,
        log_xtest_session_start_audit,
        log_xtest_session_end_audit,
    )
"""

from __future__ import annotations

import logging
from typing import Any

from selfhealing.services.audit.base import _write_to_wal

logger = logging.getLogger(__name__)


def log_xtest_operation_audit(
    session_id: str,
    action: str,
    component: str,
    details: dict[str, Any],
    result: str,
    user: str = "anonymous",
    trace_id: str | None = None,
    error_message: str | None = None,
) -> int | None:
    """
    X-Test 단일 작업을 WAL Audit 로그에 기록.

    Args:
        session_id: X-Test 세션 식별자 (X-Test-Session 헤더 또는 자동 생성)
        action: 수행 작업 (inject_dlq, force_status, reset, run_scenario 등)
        component: 대상 컴포넌트 (dlq, cb, idempotency, rate_limit, replay, retry 등)
        details: 작업 상세 정보 (요청/응답 데이터)
        result: 결과 상태 (success, failed, error)
        user: 수행자 (인증된 사용자 또는 anonymous)
        trace_id: 분산 추적 ID
        error_message: 실패 시 에러 메시지

    Returns:
        WAL 시퀀스 번호 (성공 시), None (실패 시)
    """
    audit_details = {
        "session_id": session_id,
        "action": action,
        "component": component,
        "result": result,
        "user": user,
        **details,
    }

    success = result in ("success", "completed")

    wal_seq = _write_to_wal(
        event_type="XTEST_OPERATION",
        source=f"XTest.{component}",
        details=audit_details,
        success=success,
        error_message=error_message,
        domain="xtest",
        target_id=session_id,
        trace_id=trace_id,
    )

    if wal_seq:
        logger.debug(
            f"[X-Test-Audit] {action} | component={component} | "
            f"session={session_id} | result={result} | wal_seq={wal_seq}"
        )

    return wal_seq


def log_xtest_scenario_audit(
    scenario_id: str,
    scenario_name: str,
    service_name: str,
    status: str,
    steps_total: int,
    steps_completed: int,
    errors: list[str],
    duration_ms: float,
    session_id: str | None = None,
    user: str = "anonymous",
) -> int | None:
    """
    X-Test 통합 시나리오 실행 결과를 WAL Audit 로그에 기록.

    Args:
        scenario_id: 시나리오 실행 ID
        scenario_name: 시나리오 이름 (dlq_injection_and_replay, cb_trip_and_recovery 등)
        service_name: 대상 서비스 이름
        status: 실행 상태 (completed, failed, partial)
        steps_total: 전체 스텝 수
        steps_completed: 완료된 스텝 수
        errors: 발생한 에러 목록
        duration_ms: 실행 시간 (밀리초)
        session_id: X-Test 세션 ID
        user: 수행자

    Returns:
        WAL 시퀀스 번호 (성공 시), None (실패 시)
    """
    details = {
        "scenario_id": scenario_id,
        "scenario_name": scenario_name,
        "service_name": service_name,
        "status": status,
        "steps_total": steps_total,
        "steps_completed": steps_completed,
        "errors": errors[:10] if errors else [],  # 최대 10개만 저장
        "duration_ms": duration_ms,
        "session_id": session_id or scenario_id,
        "user": user,
    }

    success = status == "completed" and not errors
    error_message = errors[0] if errors else None

    wal_seq = _write_to_wal(
        event_type="XTEST_SCENARIO",
        source="XTest.Integration",
        details=details,
        success=success,
        error_message=error_message,
        domain="xtest",
        target_id=scenario_id,
    )

    if wal_seq:
        logger.info(
            f"[X-Test-Audit] Scenario completed | name={scenario_name} | "
            f"service={service_name} | status={status} | "
            f"steps={steps_completed}/{steps_total} | duration={duration_ms}ms"
        )

    return wal_seq


def log_xtest_session_start_audit(
    session_id: str,
    user: str = "anonymous",
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """
    X-Test 세션 시작을 WAL Audit 로그에 기록.

    Args:
        session_id: X-Test 세션 식별자
        user: 수행자
        metadata: 추가 메타데이터 (테스트 목적, 환경 정보 등)

    Returns:
        WAL 시퀀스 번호
    """
    details = {
        "session_id": session_id,
        "user": user,
        "event": "session_start",
        "metadata": metadata or {},
    }

    return _write_to_wal(
        event_type="XTEST_SESSION",
        source="XTest.Session",
        details=details,
        success=True,
        domain="xtest",
        target_id=session_id,
    )


def log_xtest_session_end_audit(
    session_id: str,
    operations_count: int,
    scenarios_count: int,
    duration_seconds: float,
    user: str = "anonymous",
    summary: dict[str, Any] | None = None,
) -> int | None:
    """
    X-Test 세션 종료를 WAL Audit 로그에 기록.

    Args:
        session_id: X-Test 세션 식별자
        operations_count: 세션 동안 수행된 작업 수
        scenarios_count: 세션 동안 실행된 시나리오 수
        duration_seconds: 세션 지속 시간 (초)
        user: 수행자
        summary: 세션 요약 정보

    Returns:
        WAL 시퀀스 번호
    """
    details = {
        "session_id": session_id,
        "user": user,
        "event": "session_end",
        "operations_count": operations_count,
        "scenarios_count": scenarios_count,
        "duration_seconds": duration_seconds,
        "summary": summary or {},
    }

    return _write_to_wal(
        event_type="XTEST_SESSION",
        source="XTest.Session",
        details=details,
        success=True,
        domain="xtest",
        target_id=session_id,
    )


def log_xtest_injection_audit(
    session_id: str,
    component: str,
    injection_type: str,
    count: int,
    target_ids: list[str],
    user: str = "anonymous",
) -> int | None:
    """
    X-Test 데이터 주입(Injection)을 WAL Audit 로그에 기록.

    DLQ 항목 생성, Idempotency 키 추가, Rate Limit 카운터 조작 등
    테스트 데이터 주입 작업을 추적합니다.

    Args:
        session_id: X-Test 세션 식별자
        component: 대상 컴포넌트 (dlq, idempotency, rate_limit 등)
        injection_type: 주입 유형 (create, override, increment 등)
        count: 주입된 항목 수
        target_ids: 생성/변경된 ID 목록
        user: 수행자

    Returns:
        WAL 시퀀스 번호
    """
    details = {
        "session_id": session_id,
        "component": component,
        "injection_type": injection_type,
        "count": count,
        "target_ids": target_ids[:20] if target_ids else [],  # 최대 20개만 저장
        "user": user,
    }

    return _write_to_wal(
        event_type="XTEST_INJECTION",
        source=f"XTest.{component}",
        details=details,
        success=True,
        domain="xtest",
        target_id=session_id,
    )


def log_xtest_cleanup_audit(
    session_id: str,
    component: str,
    cleaned_count: int,
    cleaned_ids: list[str],
    user: str = "anonymous",
) -> int | None:
    """
    X-Test 정리(Cleanup/Reset)를 WAL Audit 로그에 기록.

    X-Test-Mode로 생성된 데이터 삭제/초기화 작업을 추적합니다.

    Args:
        session_id: X-Test 세션 식별자
        component: 대상 컴포넌트
        cleaned_count: 정리된 항목 수
        cleaned_ids: 정리된 ID 목록
        user: 수행자

    Returns:
        WAL 시퀀스 번호
    """
    details = {
        "session_id": session_id,
        "component": component,
        "action": "cleanup",
        "cleaned_count": cleaned_count,
        "cleaned_ids": cleaned_ids[:20] if cleaned_ids else [],
        "user": user,
    }

    return _write_to_wal(
        event_type="XTEST_CLEANUP",
        source=f"XTest.{component}",
        details=details,
        success=True,
        domain="xtest",
        target_id=session_id,
    )

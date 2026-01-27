"""
Recovery Notification Templates.

복구 프로세스 관련 Slack/Teams 알림 템플릿 모듈.

Phase 5.6 구현:
- recovery_started_notification: 복구 시작 알림
- recovery_completed_notification: 복구 완료 알림
- recovery_failed_notification: 복구 실패 알림
- recovery_approval_required_notification: 수동 승인 필요 알림
- recovery_stale_approval_reminder: 방치된 승인 리마인더
- recovery_circuit_breaker_trip_notification: 서킷 브레이커 트립 알림

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#5.6
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# =============================================================================
# Recovery Started Notification
# =============================================================================


def recovery_started_notification(
    session_id: str,
    namespace: str,
    trigger_level: str,
    initiated_by: str,
    total_steps: int,
    step_names: list[str],
) -> dict[str, Any]:
    """
    복구 시작 알림 메시지 생성.

    Args:
        session_id: 세션 ID
        namespace: 네임스페이스
        trigger_level: 트리거 레벨
        initiated_by: 시작 주체
        total_steps: 총 단계 수
        step_names: 단계 이름 목록

    Returns:
        알림 메시지 딕셔너리:
        - title: 제목
        - severity: 심각도 (info, warning, critical)
        - message: 본문
        - details: 상세 정보
        - actions: 가능한 액션 목록
    """
    steps_str = "\n".join([f"  {i+1}. {name}" for i, name in enumerate(step_names)])

    return {
        "title": f"🔄 [INFO] 복구 프로세스 시작 - {namespace}",
        "severity": "info",
        "message": (
            f"네임스페이스 '{namespace}'에서 복구 프로세스가 시작되었습니다.\n\n"
            f"**세션 ID**: `{session_id}`\n"
            f"**트리거 레벨**: {trigger_level}\n"
            f"**시작 주체**: {initiated_by}\n"
            f"**총 단계**: {total_steps}개\n\n"
            f"**복구 단계**:\n{steps_str}"
        ),
        "details": {
            "session_id": session_id,
            "namespace": namespace,
            "trigger_level": trigger_level,
            "initiated_by": initiated_by,
            "total_steps": total_steps,
            "step_names": step_names,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "복구 상태 확인",
                "url": f"/api/self-healing/recovery/status/?namespace={namespace}",
            },
            {
                "label": "복구 중단",
                "url": "/api/self-healing/recovery/abort/",
                "method": "POST",
                "data": {"session_id": session_id},
            },
        ],
    }


# =============================================================================
# Recovery Completed Notification
# =============================================================================


def recovery_completed_notification(
    session_id: str,
    namespace: str,
    trigger_level: str,
    duration_seconds: int,
    steps_completed: int,
    total_steps: int,
) -> dict[str, Any]:
    """
    복구 완료 알림 메시지 생성.

    Args:
        session_id: 세션 ID
        namespace: 네임스페이스
        trigger_level: 트리거 레벨
        duration_seconds: 소요 시간 (초)
        steps_completed: 완료된 단계 수
        total_steps: 총 단계 수

    Returns:
        알림 메시지 딕셔너리
    """
    # 시간 포맷팅
    if duration_seconds >= 3600:
        duration_str = (
            f"{duration_seconds // 3600}시간 {(duration_seconds % 3600) // 60}분"
        )
    elif duration_seconds >= 60:
        duration_str = f"{duration_seconds // 60}분 {duration_seconds % 60}초"
    else:
        duration_str = f"{duration_seconds}초"

    return {
        "title": f"✅ [SUCCESS] 복구 완료 - {namespace}",
        "severity": "info",
        "message": (
            f"네임스페이스 '{namespace}'에서 복구가 성공적으로 완료되었습니다.\n\n"
            f"**세션 ID**: `{session_id}`\n"
            f"**트리거 레벨**: {trigger_level}\n"
            f"**소요 시간**: {duration_str}\n"
            f"**완료 단계**: {steps_completed}/{total_steps}\n\n"
            f"시스템이 정상 상태로 복구되었습니다. 🎉"
        ),
        "details": {
            "session_id": session_id,
            "namespace": namespace,
            "trigger_level": trigger_level,
            "duration_seconds": duration_seconds,
            "steps_completed": steps_completed,
            "total_steps": total_steps,
            "status": "completed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "복구 이력 확인",
                "url": f"/api/self-healing/recovery/history/?namespace={namespace}",
            },
        ],
    }


# =============================================================================
# Recovery Failed Notification
# =============================================================================


def recovery_failed_notification(
    session_id: str,
    namespace: str,
    trigger_level: str,
    failed_step: str,
    error_message: str,
    steps_completed: int,
    total_steps: int,
    retry_count: int = 0,
) -> dict[str, Any]:
    """
    복구 실패 알림 메시지 생성.

    Args:
        session_id: 세션 ID
        namespace: 네임스페이스
        trigger_level: 트리거 레벨
        failed_step: 실패한 단계
        error_message: 에러 메시지
        steps_completed: 완료된 단계 수
        total_steps: 총 단계 수
        retry_count: 재시도 횟수

    Returns:
        알림 메시지 딕셔너리
    """
    return {
        "title": f"❌ [FAILURE] 복구 실패 - {namespace}",
        "severity": "critical",
        "message": (
            f"⚠️ 네임스페이스 '{namespace}'에서 복구가 실패했습니다.\n\n"
            f"**세션 ID**: `{session_id}`\n"
            f"**트리거 레벨**: {trigger_level}\n"
            f"**실패 단계**: {failed_step}\n"
            f"**진행 상태**: {steps_completed}/{total_steps} 단계 완료\n"
            f"**재시도 횟수**: {retry_count}회\n\n"
            f"**에러 메시지**:\n```\n{error_message}\n```\n\n"
            f"즉각적인 수동 개입이 필요합니다."
        ),
        "details": {
            "session_id": session_id,
            "namespace": namespace,
            "trigger_level": trigger_level,
            "failed_step": failed_step,
            "error_message": error_message,
            "steps_completed": steps_completed,
            "total_steps": total_steps,
            "retry_count": retry_count,
            "status": "failed",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "세션 상세 확인",
                "url": f"/api/self-healing/recovery/status/?namespace={namespace}",
            },
            {
                "label": "수동 복구 시작",
                "url": "/api/self-healing/recovery/start/",
                "method": "POST",
                "data": {"namespace": namespace, "force": True},
            },
        ],
        "mention": ["@oncall", "@sre-team"],
    }


# =============================================================================
# Recovery Aborted Notification
# =============================================================================


def recovery_aborted_notification(
    session_id: str,
    namespace: str,
    trigger_level: str,
    abort_reason: str,
    aborted_by: str,
    steps_completed: int,
    total_steps: int,
) -> dict[str, Any]:
    """
    복구 중단 알림 메시지 생성.

    Args:
        session_id: 세션 ID
        namespace: 네임스페이스
        trigger_level: 트리거 레벨
        abort_reason: 중단 사유
        aborted_by: 중단 주체
        steps_completed: 완료된 단계 수
        total_steps: 총 단계 수

    Returns:
        알림 메시지 딕셔너리
    """
    return {
        "title": f"⏹️ [ABORTED] 복구 중단됨 - {namespace}",
        "severity": "warning",
        "message": (
            f"네임스페이스 '{namespace}'에서 복구가 중단되었습니다.\n\n"
            f"**세션 ID**: `{session_id}`\n"
            f"**트리거 레벨**: {trigger_level}\n"
            f"**중단 주체**: {aborted_by}\n"
            f"**진행 상태**: {steps_completed}/{total_steps} 단계 완료\n\n"
            f"**중단 사유**:\n{abort_reason}"
        ),
        "details": {
            "session_id": session_id,
            "namespace": namespace,
            "trigger_level": trigger_level,
            "abort_reason": abort_reason,
            "aborted_by": aborted_by,
            "steps_completed": steps_completed,
            "total_steps": total_steps,
            "status": "aborted",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "복구 재시작",
                "url": "/api/self-healing/recovery/start/",
                "method": "POST",
                "data": {"namespace": namespace},
            },
        ],
    }


# =============================================================================
# Approval Required Notification
# =============================================================================


def recovery_approval_required_notification(
    request_id: str,
    namespace: str,
    trigger_level: str,
    requested_by: str,
    timeout_minutes: int,
    stability_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    수동 승인 필요 알림 메시지 생성.

    Args:
        request_id: 승인 요청 ID
        namespace: 네임스페이스
        trigger_level: 트리거 레벨
        requested_by: 요청 주체
        timeout_minutes: 만료 시간 (분)
        stability_info: 안정화 정보 (선택)

    Returns:
        알림 메시지 딕셔너리
    """
    stability_str = ""
    if stability_info:
        error_rate = stability_info.get("error_rate", 0) * 100
        stable_duration = stability_info.get("stable_duration_minutes", 0)
        stability_str = (
            f"\n**현재 에러율**: {error_rate:.1f}%\n"
            f"**안정화 지속 시간**: {stable_duration}분"
        )

    return {
        "title": f"🔔 [ACTION REQUIRED] 복구 승인 필요 - {namespace}",
        "severity": "warning",
        "message": (
            f"네임스페이스 '{namespace}'에서 복구가 준비되었습니다.\n"
            f"수동 승인이 필요합니다.\n\n"
            f"**요청 ID**: `{request_id}`\n"
            f"**트리거 레벨**: {trigger_level}\n"
            f"**요청 주체**: {requested_by}\n"
            f"**만료 시간**: {timeout_minutes}분"
            f"{stability_str}\n\n"
            f"⏰ 승인하지 않으면 {timeout_minutes}분 후 자동 만료됩니다."
        ),
        "details": {
            "request_id": request_id,
            "namespace": namespace,
            "trigger_level": trigger_level,
            "requested_by": requested_by,
            "timeout_minutes": timeout_minutes,
            "stability_info": stability_info,
            "status": "ready_to_restore",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "✅ 승인",
                "url": "/api/self-healing/recovery/approve/",
                "method": "POST",
                "data": {"request_id": request_id},
                "style": "primary",
            },
            {
                "label": "❌ 거부",
                "url": "/api/self-healing/recovery/reject/",
                "method": "POST",
                "data": {"request_id": request_id, "reason": "Manual rejection"},
                "style": "danger",
            },
            {
                "label": "상세 정보",
                "url": "/api/self-healing/recovery/pending-approvals/",
            },
        ],
        "mention": ["@approvers", "@sre-team"],
    }


# =============================================================================
# Stale Approval Reminder
# =============================================================================


def recovery_stale_approval_reminder(
    request_id: str,
    namespace: str,
    waiting_minutes: int,
    timeout_minutes: int,
    reminder_count: int,
) -> dict[str, Any]:
    """
    방치된 승인 리마인더 메시지 생성.

    Args:
        request_id: 승인 요청 ID
        namespace: 네임스페이스
        waiting_minutes: 대기 시간 (분)
        timeout_minutes: 만료까지 남은 시간 (분)
        reminder_count: 리마인더 횟수

    Returns:
        알림 메시지 딕셔너리
    """
    urgency = "🔴" if timeout_minutes < 30 else "🟡"

    return {
        "title": f"{urgency} [REMINDER #{reminder_count}] 복구 승인 대기 중 - {namespace}",
        "severity": "warning" if timeout_minutes >= 30 else "critical",
        "message": (
            f"복구 승인 요청이 {waiting_minutes}분 동안 대기 중입니다.\n\n"
            f"**요청 ID**: `{request_id}`\n"
            f"**네임스페이스**: {namespace}\n"
            f"**대기 시간**: {waiting_minutes}분\n"
            f"**만료까지**: {timeout_minutes}분 남음\n"
            f"**리마인더 횟수**: {reminder_count}회\n\n"
            f"⚠️ 승인하지 않으면 요청이 만료됩니다."
        ),
        "details": {
            "request_id": request_id,
            "namespace": namespace,
            "waiting_minutes": waiting_minutes,
            "timeout_minutes": timeout_minutes,
            "reminder_count": reminder_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "✅ 지금 승인",
                "url": "/api/self-healing/recovery/approve/",
                "method": "POST",
                "data": {"request_id": request_id},
                "style": "primary",
            },
        ],
        "mention": ["@oncall"],
    }


# =============================================================================
# Circuit Breaker Trip Notification
# =============================================================================


def recovery_circuit_breaker_trip_notification(
    namespace: str,
    trip_count: int,
    error_rate: float,
    threshold: float,
    is_permanent: bool,
    should_re_escalate: bool,
) -> dict[str, Any]:
    """
    서킷 브레이커 트립 알림 메시지 생성.

    Args:
        namespace: 네임스페이스
        trip_count: 트립 횟수
        error_rate: 현재 에러율
        threshold: 임계값
        is_permanent: 영구 차단 여부
        should_re_escalate: 재에스컬레이션 필요 여부

    Returns:
        알림 메시지 딕셔너리
    """
    severity = "critical" if is_permanent else "warning"
    emoji = "🛑" if is_permanent else "⚡"

    permanent_str = (
        "\n\n🚨 **영구 차단 상태**: 수동 개입이 필요합니다." if is_permanent else ""
    )

    escalate_str = (
        "\n\n⬆️ **재에스컬레이션**: Emergency 모드로 재진입 권고"
        if should_re_escalate
        else ""
    )

    return {
        "title": f"{emoji} [CIRCUIT BREAKER] 복구 서킷 브레이커 트립 - {namespace}",
        "severity": severity,
        "message": (
            f"복구 중 서킷 브레이커가 트립되었습니다.\n\n"
            f"**네임스페이스**: {namespace}\n"
            f"**트립 횟수**: {trip_count}회\n"
            f"**현재 에러율**: {error_rate * 100:.1f}%\n"
            f"**임계값**: {threshold * 100:.1f}%"
            f"{permanent_str}"
            f"{escalate_str}"
        ),
        "details": {
            "namespace": namespace,
            "trip_count": trip_count,
            "error_rate": error_rate,
            "threshold": threshold,
            "is_permanent": is_permanent,
            "should_re_escalate": should_re_escalate,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "서킷 브레이커 리셋",
                "url": "/api/self-healing/recovery/circuit-breaker/reset/",
                "method": "POST",
                "data": {"namespace": namespace},
            },
            {
                "label": "시스템 상태 확인",
                "url": f"/api/self-healing/recovery/status/?namespace={namespace}",
            },
        ],
        "mention": ["@sre-team", "@oncall"],
    }


# =============================================================================
# Step Progress Notification
# =============================================================================


def recovery_step_progress_notification(
    session_id: str,
    namespace: str,
    step_name: str,
    step_order: int,
    total_steps: int,
    status: str,
    duration_seconds: float | None = None,
) -> dict[str, Any]:
    """
    단계 진행 알림 메시지 생성 (디버그/상세 모드용).

    Args:
        session_id: 세션 ID
        namespace: 네임스페이스
        step_name: 단계 이름
        step_order: 단계 순서
        total_steps: 총 단계 수
        status: 단계 상태
        duration_seconds: 소요 시간 (초)

    Returns:
        알림 메시지 딕셔너리
    """
    progress_bar = _make_progress_bar(step_order, total_steps)

    duration_str = ""
    if duration_seconds is not None:
        duration_str = f" ({duration_seconds:.1f}s)"

    status_emoji = {
        "started": "🔄",
        "completed": "✅",
        "failed": "❌",
        "skipped": "⏭️",
    }.get(status, "🔄")

    return {
        "title": f"{status_emoji} 복구 단계 {step_order}/{total_steps} - {namespace}",
        "severity": "info" if status != "failed" else "warning",
        "message": (
            f"**단계**: {step_name}\n"
            f"**상태**: {status}{duration_str}\n\n"
            f"**진행률**: {progress_bar}"
        ),
        "details": {
            "session_id": session_id,
            "namespace": namespace,
            "step_name": step_name,
            "step_order": step_order,
            "total_steps": total_steps,
            "status": status,
            "duration_seconds": duration_seconds,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "thread_id": session_id,  # 스레드 그룹화용
    }


def _make_progress_bar(current: int, total: int, width: int = 10) -> str:
    """진행률 막대 생성."""
    if total == 0:
        return "[" + "░" * width + "] 0%"

    filled = int((current / total) * width)
    empty = width - filled
    percent = int((current / total) * 100)

    return f"[{'█' * filled}{'░' * empty}] {percent}%"


# =============================================================================
# Daily Summary Notification
# =============================================================================


def recovery_daily_summary_notification(
    date: str,
    stats: dict[str, Any],
) -> dict[str, Any]:
    """
    일일 복구 요약 알림 메시지 생성.

    Args:
        date: 날짜 (YYYY-MM-DD)
        stats: 통계 딕셔너리

    Returns:
        알림 메시지 딕셔너리
    """
    total = stats.get("total_sessions", 0)
    completed = stats.get("completed", 0)
    failed = stats.get("failed", 0)
    aborted = stats.get("aborted", 0)
    avg_duration = stats.get("average_duration_seconds", 0)
    success_rate = stats.get("success_rate", 0)

    # 시간 포맷팅
    if avg_duration >= 60:
        avg_str = f"{avg_duration // 60}분 {avg_duration % 60}초"
    else:
        avg_str = f"{avg_duration}초"

    # 상태 이모지
    if success_rate >= 90:
        status_emoji = "🟢"
    elif success_rate >= 70:
        status_emoji = "🟡"
    else:
        status_emoji = "🔴"

    return {
        "title": f"📊 일일 복구 요약 - {date}",
        "severity": "info",
        "message": (
            f"**{date}** 복구 통계\n\n"
            f"**총 복구 세션**: {total}건\n"
            f"  • ✅ 완료: {completed}건\n"
            f"  • ❌ 실패: {failed}건\n"
            f"  • ⏹️ 중단: {aborted}건\n\n"
            f"**성공률**: {status_emoji} {success_rate:.1f}%\n"
            f"**평균 소요 시간**: {avg_str}"
        ),
        "details": {
            "date": date,
            "stats": stats,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "상세 이력 보기",
                "url": "/api/self-healing/recovery/history/",
            },
        ],
    }

"""
Cascade Event Audit 알림 템플릿.

Cascade Event 관련 알림 메시지 생성 함수.

Templates:
- cascade_integrity_alert: Hash Chain 무결성 위반 알림
- cascade_depth_alert: 체인 깊이 초과 알림
- cascade_load_shedding_alert: Load Shedding 활성화 알림
- cascade_summary: 일일 Cascade Event 요약

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def cascade_integrity_alert(
    namespace: str,
    errors: List[Dict[str, Any]],
    verified_count: int,
) -> Dict[str, Any]:
    """
    Hash Chain 무결성 위반 알림 메시지 생성.
    
    Args:
        namespace: 네임스페이스
        errors: 무결성 오류 목록
        verified_count: 검증된 이벤트 수
    
    Returns:
        알림 메시지 딕셔너리:
        - title: 제목
        - severity: 심각도 (critical, warning, info)
        - message: 본문
        - details: 상세 정보
    """
    error_count = len(errors)
    
    return {
        "title": f"🔴 [CRITICAL] Cascade Hash Chain 무결성 위반 - {namespace}",
        "severity": "critical",
        "message": (
            f"네임스페이스 '{namespace}'에서 Hash Chain 무결성 위반이 감지되었습니다.\n"
            f"• 검증 이벤트 수: {verified_count}\n"
            f"• 오류 개수: {error_count}\n\n"
            "즉각적인 조사가 필요합니다. Cascade Event 로그가 위변조되었을 수 있습니다."
        ),
        "details": {
            "namespace": namespace,
            "verified_count": verified_count,
            "error_count": error_count,
            "errors": errors[:5],  # 최대 5개만 포함
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "상세 조사",
                "url": f"/api/self-healing/cascade/events/?namespace={namespace}",
            },
            {
                "label": "체크포인트 복원",
                "url": f"/api/self-healing/cascade/checkpoint/?namespace={namespace}",
            },
        ],
    }


def cascade_depth_alert(
    namespace: str,
    cascade_id: str,
    current_depth: int,
    max_depth: int,
) -> Dict[str, Any]:
    """
    체인 깊이 초과 알림 메시지 생성.
    
    Args:
        namespace: 네임스페이스
        cascade_id: Cascade Event ID
        current_depth: 현재 체인 깊이
        max_depth: 최대 허용 깊이
    
    Returns:
        알림 메시지 딕셔너리
    """
    severity = "critical" if current_depth >= max_depth else "warning"
    emoji = "🔴" if severity == "critical" else "🟡"
    
    return {
        "title": f"{emoji} [{severity.upper()}] Cascade 체인 깊이 임계치 도달 - {namespace}",
        "severity": severity,
        "message": (
            f"네임스페이스 '{namespace}'에서 Cascade 체인 깊이가 임계치에 도달했습니다.\n"
            f"• Cascade ID: {cascade_id}\n"
            f"• 현재 깊이: {current_depth}\n"
            f"• 최대 허용: {max_depth}\n\n"
            "순환 참조 또는 무한 루프 가능성이 있습니다."
        ),
        "details": {
            "namespace": namespace,
            "cascade_id": cascade_id,
            "current_depth": current_depth,
            "max_depth": max_depth,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "actions": [
            {
                "label": "Cascade 상세 보기",
                "url": f"/api/self-healing/cascade/events/{cascade_id}/?namespace={namespace}",
            },
        ],
    }


def cascade_load_shedding_alert(
    enabled: bool,
    current_load: float,
    threshold: float,
    dropped_count: int,
) -> Dict[str, Any]:
    """
    Load Shedding 활성화/비활성화 알림 메시지 생성.
    
    Args:
        enabled: Load Shedding 활성화 여부
        current_load: 현재 부하율 (0.0~1.0)
        threshold: 활성화 임계치
        dropped_count: 드랍된 이벤트 수
    
    Returns:
        알림 메시지 딕셔너리
    """
    if enabled:
        return {
            "title": "🟡 [WARNING] Cascade Audit Load Shedding 활성화",
            "severity": "warning",
            "message": (
                f"시스템 부하로 인해 Cascade Audit Load Shedding이 활성화되었습니다.\n"
                f"• 현재 부하율: {current_load * 100:.1f}%\n"
                f"• 활성화 임계치: {threshold * 100:.1f}%\n"
                f"• 드랍된 이벤트: {dropped_count}개\n\n"
                "낮은 우선순위 이벤트가 드랍되고 있습니다."
            ),
            "details": {
                "enabled": enabled,
                "current_load": current_load,
                "threshold": threshold,
                "dropped_count": dropped_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }
    else:
        return {
            "title": "🟢 [INFO] Cascade Audit Load Shedding 비활성화",
            "severity": "info",
            "message": (
                f"시스템 부하가 정상화되어 Load Shedding이 비활성화되었습니다.\n"
                f"• 현재 부하율: {current_load * 100:.1f}%\n"
                f"• 드랍된 총 이벤트: {dropped_count}개"
            ),
            "details": {
                "enabled": enabled,
                "current_load": current_load,
                "dropped_count": dropped_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        }


def cascade_summary(
    namespace: str,
    date: str,
    total_events: int,
    events_by_trigger: Dict[str, int],
    effects_by_action: Dict[str, Dict[str, int]],
    integrity_valid: bool,
    max_chain_depth: int,
) -> Dict[str, Any]:
    """
    일일 Cascade Event 요약 알림 메시지 생성.
    
    Args:
        namespace: 네임스페이스
        date: 요약 날짜 (YYYY-MM-DD)
        total_events: 총 Cascade Event 수
        events_by_trigger: 트리거별 이벤트 수
        effects_by_action: 액션별 효과 수 (success/failure)
        integrity_valid: Hash Chain 무결성 상태
        max_chain_depth: 최대 체인 깊이
    
    Returns:
        알림 메시지 딕셔너리
    """
    integrity_status = "✅ 정상" if integrity_valid else "❌ 위반"
    
    # 트리거별 요약 문자열
    trigger_lines = []
    for trigger, count in sorted(events_by_trigger.items(), key=lambda x: -x[1]):
        trigger_lines.append(f"  • {trigger}: {count}건")
    
    # 액션별 요약 문자열
    action_lines = []
    for action, stats in sorted(effects_by_action.items()):
        success = stats.get("success", 0)
        failure = stats.get("failure", 0)
        total = success + failure
        success_rate = (success / total * 100) if total > 0 else 0
        action_lines.append(f"  • {action}: {total}건 (성공률 {success_rate:.1f}%)")
    
    return {
        "title": f"📊 [DAILY] Cascade Event 일일 요약 - {namespace} ({date})",
        "severity": "info",
        "message": (
            f"네임스페이스 '{namespace}'의 {date} Cascade Event 요약입니다.\n\n"
            f"📈 **총 이벤트**: {total_events}건\n"
            f"🔗 **최대 체인 깊이**: {max_chain_depth}\n"
            f"🔒 **Hash Chain 무결성**: {integrity_status}\n\n"
            f"**트리거별 분포**:\n" + "\n".join(trigger_lines) + "\n\n"
            f"**액션별 결과**:\n" + "\n".join(action_lines)
        ),
        "details": {
            "namespace": namespace,
            "date": date,
            "total_events": total_events,
            "events_by_trigger": events_by_trigger,
            "effects_by_action": effects_by_action,
            "integrity_valid": integrity_valid,
            "max_chain_depth": max_chain_depth,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }


def cascade_fallback_recovery_alert(
    recovered_count: int,
    failed_count: int,
    fallback_path: str,
) -> Dict[str, Any]:
    """
    로컬 폴백 복구 완료 알림 메시지 생성.
    
    Args:
        recovered_count: 복구된 이벤트 수
        failed_count: 복구 실패 이벤트 수
        fallback_path: 폴백 파일 경로
    
    Returns:
        알림 메시지 딕셔너리
    """
    if failed_count == 0:
        severity = "info"
        emoji = "🟢"
        status = "성공"
    else:
        severity = "warning"
        emoji = "🟡"
        status = "부분 성공"
    
    return {
        "title": f"{emoji} [{severity.upper()}] Cascade 로컬 폴백 복구 {status}",
        "severity": severity,
        "message": (
            f"로컬 폴백에 저장된 Cascade Event가 복구되었습니다.\n"
            f"• 복구 성공: {recovered_count}건\n"
            f"• 복구 실패: {failed_count}건\n"
            f"• 폴백 경로: {fallback_path}"
        ),
        "details": {
            "recovered_count": recovered_count,
            "failed_count": failed_count,
            "fallback_path": fallback_path,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }

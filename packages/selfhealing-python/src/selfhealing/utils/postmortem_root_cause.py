"""
Postmortem Root Cause 분석 유틸리티.

Google SRE 표준에 맞춰 trigger, detection, resolution, root_cause_hypothesis 필드를 생성하는 순수 함수들.
Django 의존성 없이 사용 가능.

참조: 129_POSTMORTEM_ROOT_CAUSE.md
"""

from __future__ import annotations


def extract_trigger_info(timeline: list) -> dict | None:
    """
    타임라인에서 장애 트리거 정보 추출.

    첫 번째 CB OPEN 이벤트에서 트리거 정보를 수집합니다.

    Args:
        timeline: 이벤트 타임라인 리스트

    Returns:
        트리거 정보 딕셔너리, 데이터 없으면 None
    """
    if not timeline:
        return None

    # 첫 번째 CB OPEN 이벤트 찾기 (half_open은 제외)
    for event in timeline:
        event_type = event.get("event_type", "").lower()
        # half_open/half-open 제외하고 opened/open 찾기
        if "half_open" in event_type or "half-open" in event_type:
            continue
        if "opened" in event_type or "open" in event_type:
            details = event.get("details", {})
            error_context = details.get("error_context") or {}

            return {
                "event_type": event.get("event_type"),
                "service": details.get("service_name") or details.get("service"),
                "timestamp": event.get("timestamp"),
                "error_context": (
                    {
                        "error_type": error_context.get("error_type"),
                        "message": error_context.get("message"),
                    }
                    if error_context
                    else None
                ),
            }

    return None


def extract_detection_info(timeline: list, threshold_config: dict | None = None) -> dict | None:
    """
    타임라인에서 장애 감지 정보 추출.

    CB threshold 초과 시점 및 감지 방법 정보를 수집합니다.

    Args:
        timeline: 이벤트 타임라인 리스트
        threshold_config: CB threshold 설정 정보 (선택적)

    Returns:
        감지 정보 딕셔너리, 데이터 없으면 None
    """
    if not timeline:
        return None

    # 첫 번째 CB OPEN 이벤트 찾기 (OPEN = 감지 시점, half_open 제외)
    for event in timeline:
        event_type = event.get("event_type", "").lower()
        # half_open/half-open 제외
        if "half_open" in event_type or "half-open" in event_type:
            continue
        if "opened" in event_type or "open" in event_type:
            details = event.get("details", {})

            # failure_count, threshold 추출 시도
            failure_count = details.get("failure_count")
            threshold = details.get("threshold") or details.get("failure_threshold")

            # threshold_config에서 가져오기 (details에 없는 경우)
            if threshold is None and threshold_config:
                threshold = threshold_config.get("failure_threshold")

            result = {
                "method": "circuit_breaker_threshold",
                "detected_at": event.get("timestamp"),
                "detector": "CircuitBreakerService",
            }

            # threshold 정보가 있으면 추가
            if failure_count is not None or threshold is not None:
                result["threshold_exceeded"] = {}
                if failure_count is not None:
                    result["threshold_exceeded"]["failure_count"] = failure_count
                if threshold is not None:
                    result["threshold_exceeded"]["threshold"] = threshold

            return result

    return None


def extract_resolution_info(timeline: list) -> dict | None:
    """
    타임라인에서 해결 정보 추출.

    CB CLOSED 이벤트에서 복구 정보를 수집합니다.

    Args:
        timeline: 이벤트 타임라인 리스트

    Returns:
        해결 정보 딕셔너리, 데이터 없으면 None
    """
    if not timeline:
        return None

    # CB 상태 변경 순서 추적
    state_changes = []
    resolved_at = None

    for event in timeline:
        event_type = event.get("event_type", "").lower()

        # half_open/half-open 먼저 체크 (opened보다 우선)
        if "half_open" in event_type or "half-open" in event_type:
            state_changes.append("HALF_OPEN")
        elif "opened" in event_type:
            state_changes.append("OPEN")
        elif "closed" in event_type:
            state_changes.append("CLOSED")
            resolved_at = event.get("timestamp")

    if not resolved_at:
        return None

    # 복구 경로 생성
    recovery_path = " → ".join(state_changes) if state_changes else None

    return {
        "method": "automatic_recovery",
        "resolved_at": resolved_at,
        "recovery_path": recovery_path,
        "manual_intervention": False,
    }


def generate_root_cause_hypothesis(
    timeline: list,
    affected_services: list,
) -> str | None:
    """
    타임라인과 영향받은 서비스를 기반으로 근본 원인 가설 생성.

    패턴 기반 분류:
    - 단일 서비스 OPEN → "단일 서비스 장애: {service}"
    - 다중 서비스 OPEN → "인프라 전체 장애 가능성 - 공통 원인 분석 필요"
    - DB 관련 에러 → "데이터베이스 연결 문제"
    - Timeout 에러 → "네트워크 지연 또는 서비스 과부하"

    Args:
        timeline: 이벤트 타임라인 리스트
        affected_services: 영향받은 서비스 리스트

    Returns:
        근본 원인 가설 문자열, 가설 생성 불가시 None
    """
    if not timeline and not affected_services:
        return None

    # 에러 컨텍스트 수집 (첫 번째 OPEN 이벤트에서)
    error_type = None
    error_message = None
    first_service = None

    for event in timeline:
        event_type = event.get("event_type", "").lower()
        if "opened" in event_type or "open" in event_type:
            details = event.get("details", {})
            error_context = details.get("error_context") or {}
            error_type = error_context.get("error_type", "")
            error_message = error_context.get("message", "")
            first_service = details.get("service_name") or details.get("service")
            break

    # 다중 서비스 장애 판단
    if len(affected_services) > 1:
        return "인프라 전체 장애 가능성 - 공통 원인 분석 필요"

    # 에러 타입 기반 가설 생성
    error_type_lower = (error_type or "").lower()
    error_message_lower = (error_message or "").lower()

    # DB 관련 에러 패턴
    db_keywords = ["database", "db", "connection", "sql", "postgresql", "mysql", "redis"]
    if any(kw in error_type_lower or kw in error_message_lower for kw in db_keywords):
        service_info = f": {first_service}" if first_service else ""
        return f"데이터베이스 연결 문제{service_info}"

    # Timeout 에러 패턴
    timeout_keywords = ["timeout", "timed out", "timeouterror"]
    if any(kw in error_type_lower or kw in error_message_lower for kw in timeout_keywords):
        service_info = f": {first_service}" if first_service else ""
        return f"네트워크 지연 또는 서비스 과부하{service_info}"

    # 단일 서비스 장애
    service_name = first_service or (affected_services[0] if affected_services else "unknown")
    error_info = f" - {error_type} 감지" if error_type else ""

    return f"단일 서비스 장애: {service_name}{error_info}"


def build_postmortem_root_cause_fields(
    timeline: list,
    affected_services: list,
    threshold_config: dict | None = None,
) -> dict:
    """
    Post-mortem에 추가할 root cause 관련 필드들을 생성.

    Args:
        timeline: 이벤트 타임라인 리스트
        affected_services: 영향받은 서비스 리스트
        threshold_config: CB threshold 설정 정보 (선택적)

    Returns:
        root cause 관련 필드들의 딕셔너리
    """
    return {
        "trigger": extract_trigger_info(timeline),
        "detection": extract_detection_info(timeline, threshold_config),
        "resolution": extract_resolution_info(timeline),
        "root_cause_hypothesis": generate_root_cause_hypothesis(timeline, affected_services),
    }

"""
Config Version Conflict Handling.

Optimistic Locking을 통한 버전 충돌 감지.
롤백 중 다른 운영자가 수동 변경 시 충돌을 감지하고 Audit 로그를 남깁니다.

Reference:
    - config_history.py#L316-349 (rollback 로직)
    - docs/self_healing/middleware_system/71_CANARY_CONFIG_ROLLOUT.md

Usage:
    from selfhealing.services.canary.versioning import (
        check_version_and_rollback,
        VersionConflictError,
    )

    try:
        new_version = check_version_and_rollback(
            config_type="circuit_breaker",
            target_version=5,
            expected_current_version=8,
            rolled_back_by="admin@example.com",
        )
    except VersionConflictError as e:
        # 충돌 처리
        logger.warning(f"Conflict: {e.conflicting_operator} modified config")
"""

import logging
from typing import TYPE_CHECKING

from selfhealing.utils.time import utc_now

if TYPE_CHECKING:
    from selfhealing.services.config_history import ConfigVersion

logger = logging.getLogger(__name__)


class VersionConflictError(Exception):
    """
    설정 버전 충돌.

    Optimistic Locking 검사에서 예상 버전과 실제 버전이 다를 때 발생.

    Attributes:
        expected_version: 예상했던 현재 버전
        actual_version: 실제 현재 버전
        conflicting_operator: 충돌 원인이 된 변경자
        config_type: 설정 유형
    """

    def __init__(
        self,
        expected_version: int,
        actual_version: int,
        conflicting_operator: str,
        config_type: str,
    ):
        self.expected_version = expected_version
        self.actual_version = actual_version
        self.conflicting_operator = conflicting_operator
        self.config_type = config_type

        super().__init__(
            f"Version conflict on {config_type}: expected v{expected_version}, "
            f"actual v{actual_version} (modified by {conflicting_operator})"
        )


class VersionChecker:
    """
    설정 버전 검사기.

    Optimistic Locking 패턴으로 동시 수정 충돌을 감지합니다.

    Example:
        checker = VersionChecker()

        # 버전 확인
        is_valid, info = checker.check(
            config_type="circuit_breaker",
            expected_version=8,
        )

        if not is_valid:
            raise VersionConflictError(
                expected_version=8,
                actual_version=info["actual_version"],
                conflicting_operator=info["changed_by"],
                config_type="circuit_breaker",
            )
    """

    def check(
        self,
        config_type: str,
        expected_version: int,
    ) -> tuple[bool, dict]:
        """
        버전 일치 여부 확인.

        Args:
            config_type: 설정 유형
            expected_version: 예상 현재 버전

        Returns:
            (일치 여부, 상세 정보 dict)
            상세 정보: {"actual_version": int, "changed_by": str}
        """
        from selfhealing.services.config_history import get_config_history_service

        service = get_config_history_service()
        current = service.get_current_version(config_type)

        if current is None:
            # 설정이 없는 경우 - 충돌 아님 (새 설정)
            return True, {"actual_version": 0, "changed_by": ""}

        if current.version == expected_version:
            return True, {
                "actual_version": current.version,
                "changed_by": current.changed_by,
            }

        # 버전 불일치
        return False, {
            "actual_version": current.version,
            "changed_by": current.changed_by,
        }

    def get_current_version(self, config_type: str) -> int:
        """
        현재 버전 번호 조회.

        Args:
            config_type: 설정 유형

        Returns:
            현재 버전 번호 (없으면 0)
        """
        from selfhealing.services.config_history import get_config_history_service

        service = get_config_history_service()
        current = service.get_current_version(config_type)

        return current.version if current else 0


def check_version_and_rollback(
    config_type: str,
    target_version: int,
    expected_current_version: int,
    rolled_back_by: str,
) -> "ConfigVersion":
    """
    버전 확인 후 롤백 수행.

    Optimistic Locking으로 충돌을 감지하고, 충돌이 없으면 롤백을 수행합니다.
    충돌 시 VersionConflictError를 발생시키고 Audit 로그를 남깁니다.

    Args:
        config_type: 설정 유형 (circuit_breaker, dlq 등)
        target_version: 롤백할 대상 버전
        expected_current_version: 예상 현재 버전 (낙관적 락)
        rolled_back_by: 롤백 수행자 (이메일 또는 사용자명)

    Returns:
        새로 생성된 롤백 버전 (ConfigVersion)

    Raises:
        VersionConflictError: 버전 충돌 시
        ValueError: 대상 버전이 존재하지 않을 때

    Example:
        try:
            new_version = check_version_and_rollback(
                config_type="circuit_breaker",
                target_version=5,
                expected_current_version=8,
                rolled_back_by="admin@example.com",
            )
            print(f"Rolled back to v{target_version}, new version: v{new_version.version}")
        except VersionConflictError as e:
            print(f"Conflict: {e.conflicting_operator} modified while you were working")
    """
    from selfhealing.services.config_history import get_config_history_service

    service = get_config_history_service()
    current = service.get_current_version(config_type)

    # 버전 확인 (Optimistic Lock)
    if current and current.version != expected_current_version:
        # 충돌 감지 - Audit 로그
        _log_version_conflict(
            config_type=config_type,
            expected_version=expected_current_version,
            actual_version=current.version,
            conflicting_operator=current.changed_by,
            attempted_by=rolled_back_by,
        )

        raise VersionConflictError(
            expected_version=expected_current_version,
            actual_version=current.version,
            conflicting_operator=current.changed_by,
            config_type=config_type,
        )

    # 버전 일치 - 롤백 수행
    return service.rollback(config_type, target_version, rolled_back_by)


def _log_version_conflict(
    config_type: str,
    expected_version: int,
    actual_version: int,
    conflicting_operator: str,
    attempted_by: str,
) -> None:
    """
    버전 충돌 Audit 로그.

    포렌식 및 문제 분석을 위해 충돌 상황을 상세히 기록합니다.
    """
    conflict_details = {
        "event_type": "config_version_conflict",
        "config_type": config_type,
        "expected_version": expected_version,
        "actual_version": actual_version,
        "conflicting_operator": conflicting_operator,
        "attempted_by": attempted_by,
        "conflict_time": utc_now().isoformat(),
        "action": "rollback_blocked",
    }

    logger.warning(
        f"[VersionConflict] {config_type}: expected v{expected_version}, "
        f"actual v{actual_version} by {conflicting_operator}, "
        f"attempted by {attempted_by}"
    )

    # Audit 시스템 연동 (가능한 경우)
    try:
        from selfhealing.services.audit import log_system_control_audit

        log_system_control_audit(
            action="canary_rollback_conflict",
            target=config_type,
            result="blocked",
            operator=attempted_by,
            details=conflict_details,
        )
    except ImportError:
        logger.debug("[VersionConflict] Audit system not available")
    except Exception as e:
        logger.debug(f"[VersionConflict] Audit log failed: {e}")

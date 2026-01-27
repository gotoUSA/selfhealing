"""
Governance Checks - 공통 거버넌스 체크 로직

서비스 레이어에서 재사용할 수 있는 공통 안전 체크 유틸리티 및 믹스인.

Check on Use 패턴:
    모든 체크는 TTL 캐시 기반으로 동작하며, 서비스 메서드 실행 시점에
    상태를 조회합니다. 이벤트 버스를 통한 즉시 캐시 무효화도 지원합니다.

Audit Logging 연동:
    차단 발생 시 자동으로 AuditLogAdapter에 기록됩니다.
    "왜 이때 작업이 안 됐지?"라는 질문에 명확한 답변을 제공합니다.
    예: "사령탑이 LEVEL_3 비상이라 차단했습니다"

사용법:
    # 데코레이터 방식 (권장)
    @require_system_enabled
    @require_not_emergency(min_level=EmergencyLevel.LEVEL_2)
    def my_automation_method(self):
        ...

    # 믹스인 방식
    class MyService(GovernanceCheckMixin):
        def my_method(self):
            if not self.is_automation_allowed():
                return OperationResult.blocked("automation not allowed")
            ...

    # 직접 호출 방식
    from selfhealing.services.governance_checks import (
        is_system_enabled,
        is_emergency_blocking,
        is_error_budget_blocking,
    )

    if not is_system_enabled():
        return {"error": "Kill Switch active"}

아키텍처 다이어그램에 기반한 거버넌스 체크.
"""

from __future__ import annotations

import functools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, TypeVar

if TYPE_CHECKING:
    from selfhealing.interfaces.audit_adapter import AuditLogAdapter

logger = logging.getLogger(__name__)


# =============================================================================
# Audit Log Integration
# =============================================================================


def _get_audit_adapter() -> AuditLogAdapter | None:
    """
    AuditLogAdapter 인스턴스를 가져옵니다.

    사용자가 AuditLogAdapter를 등록하지 않았으면 None 반환.
    """
    try:
        from selfhealing.factory import ProviderRegistry

        return ProviderRegistry.get_audit_adapter()
    except (ImportError, ValueError, AttributeError):
        # Adapter not registered - audit logging is optional
        return None


def _log_governance_blocked(
    block_reason: str,
    operation_name: str,
    details: dict | None = None,
    service_name: str | None = None,
    domain: str | None = None,
    request: Any = None,
) -> None:
    """
    거버넌스 차단을 Audit Log에 기록.

    이 함수는 차단이 발생할 때마다 호출되어
    "왜 이때 작업이 안 됐지?"라는 질문에 대한 기록을 남깁니다.

    변경 사항:
    - request가 있으면 → RequestAuditBuffer에 적재 (AuditMiddleware에서 일괄 기록)
    - request가 없으면 → 기존 방식 유지 (직접 로깅)

    Args:
        block_reason: 차단 사유 (kill_switch, emergency_mode, error_budget)
        operation_name: 차단된 작업 이름
        details: 추가 상세 정보
        service_name: 관련 서비스 이름
        domain: 도메인
        request: Django HttpRequest 객체 (있으면 버퍼에 적재)
    """
    # === 버퍼 패턴 우선 ===
    if request is not None:
        try:
            from selfhealing.audit.event_buffer import (
                AuditEventType,
                RequestAuditBuffer,
            )

            buffer = RequestAuditBuffer.get_or_create(request)
            buffer.add(
                event_type=AuditEventType.GOVERNANCE_BLOCKED,
                source="GovernanceGuard",
                details={
                    "block_reason": block_reason,
                    "operation_name": operation_name,
                    "service_name": service_name,
                    **(details or {}),
                },
                success=False,
                error_message=block_reason,
                domain=domain,
            )
            return  # 버퍼에 추가됨 - AuditMiddleware에서 기록
        except ImportError:
            pass  # event_buffer 사용 불가 - fallback

    # === Fallback: 기존 방식 ===
    adapter = _get_audit_adapter()
    if adapter is None:
        # Audit adapter not configured - just log to standard logger
        logger.info(f"[GovernanceAudit] BLOCKED | reason={block_reason} | " f"operation={operation_name} | details={details}")
        return

    try:
        adapter.log_governance_blocked(
            block_reason=block_reason,
            operation_name=operation_name,
            details=details,
            service_name=service_name,
            domain=domain,
        )
    except Exception as e:
        # Audit logging should never break the main flow
        logger.warning(f"[GovernanceAudit] Failed to log: {e}")


# =============================================================================
# Result Types
# =============================================================================


class BlockReason(str, Enum):
    """자동화가 차단된 이유."""

    KILL_SWITCH = "kill_switch"
    """Kill Switch 활성화됨."""

    EMERGENCY_MODE = "emergency_mode"
    """비상 모드 활성화 (LEVEL_2+)."""

    ERROR_BUDGET = "error_budget"
    """에러 예산 고갈."""

    RATE_LIMITED = "rate_limited"
    """Rate Limit 초과."""

    MANUALLY_BLOCKED = "manually_blocked"
    """관리자에 의해 수동 차단됨."""


@dataclass
class GovernanceCheckResult:
    """거버넌스 체크 결과."""

    allowed: bool
    """실행이 허용되는지 여부."""

    block_reason: BlockReason | None = None
    """차단된 경우 사유."""

    block_message: str = ""
    """사람이 읽을 수 있는 메시지."""

    # 상세 정보 (디버깅/로깅용)
    emergency_level: str = "UNKNOWN"
    error_budget_percent: float = 100.0
    threshold_percent: float = 0.0

    @classmethod
    def allowed_result(cls) -> GovernanceCheckResult:
        """허용된 결과 팩토리."""
        return cls(allowed=True)

    @classmethod
    def blocked_by_kill_switch(cls) -> GovernanceCheckResult:
        """Kill Switch에 의해 차단된 결과."""
        return cls(
            allowed=False,
            block_reason=BlockReason.KILL_SWITCH,
            block_message="Kill Switch is active: self-healing system is disabled",
        )

    @classmethod
    def blocked_by_emergency(
        cls,
        level_name: str,
        message: str = "",
    ) -> GovernanceCheckResult:
        """비상 모드에 의해 차단된 결과."""
        return cls(
            allowed=False,
            block_reason=BlockReason.EMERGENCY_MODE,
            block_message=message or f"Emergency mode {level_name} is active",
            emergency_level=level_name,
        )

    @classmethod
    def blocked_by_error_budget(
        cls,
        budget_percent: float,
        threshold_percent: float,
    ) -> GovernanceCheckResult:
        """에러 예산 부족으로 차단된 결과."""
        return cls(
            allowed=False,
            block_reason=BlockReason.ERROR_BUDGET,
            block_message=f"Error budget critically low ({budget_percent:.1f}%): manual mode enforced",
            error_budget_percent=budget_percent,
            threshold_percent=threshold_percent,
        )

    def to_dict(self) -> dict:
        """딕셔너리로 변환."""
        return {
            "allowed": self.allowed,
            "block_reason": self.block_reason.value if self.block_reason else None,
            "block_message": self.block_message,
            "emergency_level": self.emergency_level,
            "error_budget_percent": self.error_budget_percent,
            "threshold_percent": self.threshold_percent,
        }


# =============================================================================
# TTL Cache for Check on Use Pattern
# =============================================================================


class TTLCache:
    """
    Thread-safe TTL 기반 캐시.

    Check on Use 패턴 구현을 위한 경량 캐시.
    이벤트 버스를 통해 invalidate()를 호출하면 즉시 무효화됩니다.
    """

    def __init__(self, default_ttl: float = 30.0):
        """
        Args:
            default_ttl: 기본 TTL (초). 기본값 30초.
        """
        self._cache: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()
        self._default_ttl = default_ttl

    def get(self, key: str) -> Any | None:
        """캐시에서 값 조회. 만료된 경우 None 반환."""
        with self._lock:
            if key in self._cache:
                value, expires_at = self._cache[key]
                if time.time() < expires_at:
                    return value
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, value: Any, ttl: float | None = None) -> None:
        """캐시에 값 저장."""
        with self._lock:
            expires_at = time.time() + (ttl or self._default_ttl)
            self._cache[key] = (value, expires_at)

    def invalidate(self, key: str) -> None:
        """특정 키 무효화."""
        with self._lock:
            self._cache.pop(key, None)

    def invalidate_all(self) -> None:
        """모든 캐시 무효화."""
        with self._lock:
            self._cache.clear()


def _create_governance_cache() -> TTLCache:
    """거버넌스 캐시 생성. Settings에서 TTL을 가져옵니다."""
    try:
        from selfhealing.settings.governance import get_governance_settings

        _settings = get_governance_settings()
        return TTLCache(default_ttl=_settings.cache_ttl)
    except Exception:
        # 설정 로드 실패 시 기본값 사용
        return TTLCache(default_ttl=30.0)


# Global cache instance
_governance_cache = _create_governance_cache()


def invalidate_governance_cache() -> None:
    """
    거버넌스 캐시 무효화.

    이벤트 버스에서 상태 변경 시 호출하세요:
        event_bus.subscribe("EmergencyLevelChanged", invalidate_governance_cache)
        event_bus.subscribe("SystemControlChanged", invalidate_governance_cache)
    """
    _governance_cache.invalidate_all()
    logger.debug("[GovernanceChecks] Cache invalidated")


# =============================================================================
# Individual Check Functions
# =============================================================================


def is_system_enabled() -> bool:
    """
    Self-healing 시스템이 활성화되어 있는지 확인 (Kill Switch 체크).

    Returns:
        True if enabled, False if Kill Switch is active
    """
    cached = _governance_cache.get("system_enabled")
    if cached is not None:
        return cached

    try:
        from selfhealing.services.system_control import SystemControlManager

        manager = SystemControlManager()
        result = manager.is_enabled()
        _governance_cache.set("system_enabled", result)
        return result
    except Exception as e:
        logger.warning(f"[GovernanceChecks] Could not check system status: {e}")
        # Fail-open: 시스템 상태 확인 실패 시 활성화 가정
        return True


def is_emergency_blocking(min_level: int | None = None) -> tuple[bool, str]:
    """
    비상 모드로 인해 작업이 차단되어야 하는지 확인.

    Args:
        min_level: 차단을 트리거하는 최소 비상 레벨 (None이면 Settings에서 로드)

    Returns:
        (is_blocked, level_name) 튜플
    """
    if min_level is None:
        from selfhealing.settings.governance import get_governance_settings

        min_level = get_governance_settings().emergency_min_level

    cache_key = f"emergency_blocking_{min_level}"
    cached = _governance_cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        from selfhealing.services.emergency_mode import get_emergency_manager

        manager = get_emergency_manager()
        level = manager.get_current_level()

        is_blocked = level.value >= min_level
        result = (is_blocked, level.name)
        _governance_cache.set(cache_key, result)
        return result
    except Exception as e:
        logger.warning(f"[GovernanceChecks] Could not check emergency mode: {e}")
        # Fail-open: 비상 모드 확인 실패 시 허용
        return False, "UNKNOWN"


def is_error_budget_blocking() -> tuple[bool, float, float]:
    """
    에러 예산 부족으로 자동화가 차단되어야 하는지 확인.

    Returns:
        (is_blocked, current_budget_percent, threshold_percent) 튜플
    """
    cached = _governance_cache.get("error_budget_blocking")
    if cached is not None:
        return cached

    try:
        from selfhealing.services.error_budget_gate import check_automation_allowed

        gate_result = check_automation_allowed()
        result = (
            not gate_result.allowed,
            gate_result.error_budget_percent,
            gate_result.threshold_percent,
        )
        _governance_cache.set("error_budget_blocking", result)
        return result
    except Exception as e:
        logger.warning(f"[GovernanceChecks] Could not check error budget: {e}")
        # Fail-open: 에러 예산 확인 실패 시 허용
        return False, 100.0, 0.0


def check_all_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str = "unknown_operation",
    service_name: str | None = None,
    domain: str | None = None,
    audit_on_block: bool = True,
) -> GovernanceCheckResult:
    """
    모든 거버넌스 체크를 순차적으로 수행.

    체크 순서:
    1. Kill Switch (enabled일 때)
    2. Emergency Level (enabled일 때)
    3. Error Budget (enabled일 때)

    첫 번째 실패에서 조기 반환합니다.
    차단 발생 시 AuditLog에 자동 기록됩니다 (audit_on_block=True).

    Args:
        check_kill_switch: Kill Switch 체크 여부
        check_emergency: 비상 모드 체크 여부
        emergency_min_level: 비상 모드 차단 최소 레벨 (None이면 Settings에서 로드)
        check_error_budget: 에러 예산 체크 여부
        operation_name: 작업 이름 (Audit 로깅용)
        service_name: 서비스 이름 (Audit 로깅용)
        domain: 도메인 (Audit 로깅용)
        audit_on_block: 차단 시 Audit Log 기록 여부

    Returns:
        GovernanceCheckResult
    """
    # emergency_min_level이 None이면 Settings에서 로드
    if emergency_min_level is None:
        from selfhealing.settings.governance import get_governance_settings

        emergency_min_level = get_governance_settings().emergency_min_level

    # 1. Kill Switch
    if check_kill_switch and not is_system_enabled():
        logger.warning("[GovernanceChecks] Blocked by Kill Switch")

        if audit_on_block:
            _log_governance_blocked(
                block_reason="kill_switch",
                operation_name=operation_name,
                details={"reason": "Self-healing system disabled via Kill Switch"},
                service_name=service_name,
                domain=domain,
            )

        return GovernanceCheckResult.blocked_by_kill_switch()

    # 2. Emergency Mode
    if check_emergency:
        is_blocked, level_name = is_emergency_blocking(min_level=emergency_min_level)
        if is_blocked:
            logger.warning(f"[GovernanceChecks] Blocked by Emergency Mode: {level_name}")

            if audit_on_block:
                _log_governance_blocked(
                    block_reason="emergency_mode",
                    operation_name=operation_name,
                    details={
                        "emergency_level": level_name,
                        "min_blocking_level": emergency_min_level,
                    },
                    service_name=service_name,
                    domain=domain,
                )

            return GovernanceCheckResult.blocked_by_emergency(
                level_name=level_name,
                message=f"Emergency mode {level_name} is active: operations blocked",
            )

    # 3. Error Budget
    if check_error_budget:
        is_blocked, budget_pct, threshold_pct = is_error_budget_blocking()
        if is_blocked:
            logger.warning(f"[GovernanceChecks] Blocked by Error Budget: {budget_pct:.1f}%")

            if audit_on_block:
                _log_governance_blocked(
                    block_reason="error_budget",
                    operation_name=operation_name,
                    details={
                        "error_budget_percent": budget_pct,
                        "threshold_percent": threshold_pct,
                    },
                    service_name=service_name,
                    domain=domain,
                )

            return GovernanceCheckResult.blocked_by_error_budget(
                budget_percent=budget_pct,
                threshold_percent=threshold_pct,
            )

    return GovernanceCheckResult.allowed_result()


# =============================================================================
# Decorators
# =============================================================================


F = TypeVar("F", bound=Callable[..., Any])


def require_system_enabled(func: F) -> F:
    """
    Kill Switch 체크 데코레이터.

    시스템이 비활성화되어 있으면 GovernanceCheckResult.blocked 반환.
    차단 시 AuditLog에 기록됩니다.

    Usage:
        @require_system_enabled
        def my_automation(self):
            ...
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if not is_system_enabled():
            logger.warning(f"[GovernanceChecks] {func.__name__} blocked: Kill Switch active")
            result = GovernanceCheckResult.blocked_by_kill_switch()
            _log_governance_blocked(
                block_reason=result.block_reason,
                operation_name=func.__name__,
                details={"decorator": "require_system_enabled"},
            )
            return result
        return func(*args, **kwargs)

    return wrapper  # type: ignore


def require_not_emergency(min_level: int = 2) -> Callable[[F], F]:
    """
    비상 모드 체크 데코레이터 팩토리.

    지정된 레벨 이상의 비상 모드에서는 차단.
    차단 시 AuditLog에 기록됩니다.

    Args:
        min_level: 차단을 트리거하는 최소 비상 레벨 (기본: 2)

    Usage:
        @require_not_emergency(min_level=2)
        def my_automation(self):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            is_blocked, level_name = is_emergency_blocking(min_level=min_level)
            if is_blocked:
                logger.warning(f"[GovernanceChecks] {func.__name__} blocked: " f"Emergency mode {level_name}")
                result = GovernanceCheckResult.blocked_by_emergency(
                    level_name=level_name,
                    message=f"Emergency mode {level_name} blocks this operation",
                )
                _log_governance_blocked(
                    block_reason=result.block_reason,
                    operation_name=func.__name__,
                    details={
                        "decorator": "require_not_emergency",
                        "emergency_level": level_name,
                        "min_level": min_level,
                    },
                )
                return result
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def require_error_budget() -> Callable[[F], F]:
    """
    에러 예산 체크 데코레이터.

    에러 예산이 임계값 이하면 차단.
    차단 시 AuditLog에 기록됩니다.

    Usage:
        @require_error_budget()
        def my_automation(self):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            is_blocked, budget_pct, threshold_pct = is_error_budget_blocking()
            if is_blocked:
                logger.warning(f"[GovernanceChecks] {func.__name__} blocked: " f"Error budget {budget_pct:.1f}%")
                result = GovernanceCheckResult.blocked_by_error_budget(
                    budget_percent=budget_pct,
                    threshold_percent=threshold_pct,
                )
                _log_governance_blocked(
                    block_reason=result.block_reason,
                    operation_name=func.__name__,
                    details={
                        "decorator": "require_error_budget",
                        "budget_percent": budget_pct,
                        "threshold_percent": threshold_pct,
                    },
                )
                return result
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


def require_governance(
    check_kill_switch: bool = True,
    check_emergency: bool = True,
    emergency_min_level: int | None = None,
    check_error_budget: bool = True,
    operation_name: str | None = None,
    audit_on_block: bool = True,
) -> Callable[[F], F]:
    """
    통합 거버넌스 체크 데코레이터 팩토리.

    여러 거버넌스 체크를 한 번에 적용.
    차단 시 AuditLog에 기록됩니다.
    emergency_min_level이 None이면 Settings에서 로드합니다.

    Args:
        check_kill_switch: Kill Switch 체크 여부
        check_emergency: 비상 모드 체크 여부
        emergency_min_level: 비상 모드 차단 최소 레벨
        check_error_budget: 에러 예산 체크 여부
        operation_name: 작업 이름 (생략 시 함수 이름 사용)
        audit_on_block: 차단 시 Audit Log 기록 여부

    Usage:
        @require_governance(check_error_budget=True)
        def my_automation(self):
            ...
    """

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            op_name = operation_name or func.__name__
            result = check_all_governance(
                check_kill_switch=check_kill_switch,
                check_emergency=check_emergency,
                emergency_min_level=emergency_min_level,
                check_error_budget=check_error_budget,
                operation_name=op_name,
                audit_on_block=audit_on_block,
            )
            if not result.allowed:
                logger.warning(
                    f"[GovernanceChecks] {func.__name__} blocked: "
                    f"{result.block_reason.value if result.block_reason else 'unknown'}"
                )
                return result
            return func(*args, **kwargs)

        return wrapper  # type: ignore

    return decorator


# =============================================================================
# Mixin Class
# =============================================================================


class GovernanceCheckMixin:
    """
    거버넌스 체크 메서드를 제공하는 믹스인.

    서비스 클래스에서 상속받아 사용하세요:

        class MyService(GovernanceCheckMixin):
            def do_something(self):
                check = self.check_governance(operation_name="do_something")
                if not check.allowed:
                    return {"error": check.block_message}
                ...

    Audit Logging:
        차단 발생 시 자동으로 AuditLog에 기록됩니다.
        operation_name을 명시하면 더 의미있는 기록이 남습니다.
    """

    # 서브클래스에서 오버라이드 가능
    _governance_service_name: str | None = None
    _governance_domain: str | None = None

    def is_automation_allowed(
        self,
        check_emergency: bool = True,
        emergency_min_level: int | None = None,
        check_error_budget: bool = True,
        operation_name: str = "automation_check",
    ) -> bool:
        """
        자동화가 허용되는지 빠르게 체크.

        Args:
            emergency_min_level: None이면 Settings에서 로드

        Returns:
            True if allowed, False otherwise
        """
        result = self.check_governance(
            check_emergency=check_emergency,
            emergency_min_level=emergency_min_level,
            check_error_budget=check_error_budget,
            operation_name=operation_name,
        )
        return result.allowed

    def check_governance(
        self,
        check_kill_switch: bool = True,
        check_emergency: bool = True,
        emergency_min_level: int | None = None,
        check_error_budget: bool = True,
        operation_name: str = "governance_check",
        audit_on_block: bool = True,
    ) -> GovernanceCheckResult:
        """
        거버넌스 체크 수행.

        Args:
            check_kill_switch: Kill Switch 체크 여부
            check_emergency: 비상 모드 체크 여부
            emergency_min_level: 비상 모드 차단 최소 레벨 (None이면 Settings에서 로드)
            check_error_budget: 에러 예산 체크 여부
            operation_name: 작업 이름 (Audit 로깅용)
            audit_on_block: 차단 시 Audit Log 기록 여부

        Returns:
            GovernanceCheckResult with detailed information
        """
        return check_all_governance(
            check_kill_switch=check_kill_switch,
            check_emergency=check_emergency,
            emergency_min_level=emergency_min_level,
            check_error_budget=check_error_budget,
            operation_name=operation_name,
            service_name=self._governance_service_name,
            domain=self._governance_domain,
            audit_on_block=audit_on_block,
        )

    def require_automation_allowed(
        self,
        check_emergency: bool = True,
        emergency_min_level: int | None = None,
        check_error_budget: bool = True,
        operation_name: str = "require_automation",
    ) -> GovernanceCheckResult | None:
        """
        자동화가 허용되지 않으면 차단 결과 반환.

        허용되면 None, 차단되면 GovernanceCheckResult 반환.

        Usage:
            blocked = self.require_automation_allowed(operation_name="replay_dlq")
            if blocked:
                return {"error": blocked.block_message}
            # proceed with automation
        """
        result = self.check_governance(
            check_kill_switch=True,
            check_emergency=check_emergency,
            emergency_min_level=emergency_min_level,
            check_error_budget=check_error_budget,
            operation_name=operation_name,
        )
        if not result.allowed:
            return result
        return None


# =============================================================================
# Export for package __init__.py
# =============================================================================

__all__ = [
    # Result types
    "BlockReason",
    "GovernanceCheckResult",
    # Functions
    "is_system_enabled",
    "is_emergency_blocking",
    "is_error_budget_blocking",
    "check_all_governance",
    "invalidate_governance_cache",
    # Decorators
    "require_system_enabled",
    "require_not_emergency",
    "require_error_budget",
    "require_governance",
    # Mixin
    "GovernanceCheckMixin",
    # Cache
    "TTLCache",
]

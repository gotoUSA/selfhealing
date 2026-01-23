"""
Emergency Coordination Enums.

긴급 상황 조율 레이어에서 사용되는 열거형 정의.

Reference:
    docs/self_healing/middleware_system/72_EMERGENCY_COORDINATION_LAYER.md
"""

from __future__ import annotations

from enum import Enum, IntEnum


class EmergencyScope(str, Enum):
    """
    긴급 모드 적용 범위.
    
    REGIONAL: 특정 리전/네임스페이스에만 적용
    GLOBAL: 모든 클러스터에 적용
    """
    
    REGIONAL = "regional"
    GLOBAL = "global"


class ActionType(str, Enum):
    """
    연계 액션 유형.
    
    Emergency 상황에서 Coordinator가 실행할 수 있는 액션 종류.
    """
    
    GOVERNANCE_STRICT = "governance_strict"
    """Governance 모드를 STRICT로 전환."""
    
    GOVERNANCE_NORMAL = "governance_normal"
    """Governance 모드를 NORMAL로 복원."""
    
    CANARY_PAUSE = "canary_pause"
    """진행 중인 Canary 롤아웃 일시 중지."""
    
    CANARY_ROLLBACK = "canary_rollback"
    """진행 중인 Canary 롤아웃 롤백."""
    
    CANARY_RESUME = "canary_resume"
    """일시 중지된 Canary 롤아웃 재개."""
    
    BUDGET_MULTIPLIER = "budget_multiplier"
    """Error Budget 가중치 설정."""
    
    BUDGET_RESET = "budget_reset"
    """Error Budget 가중치 초기화."""
    
    NOTIFICATION = "notification"
    """알림 발송."""


class CommandPrecedence(IntEnum):
    """
    명령 우선순위.
    
    숫자가 높을수록 우선. 운영자 명령 vs 시스템 감지 충돌 시 해결에 사용.
    
    Code reference:
        circuit_breaker.py#L567 (manual_override TTL 패턴)
    """
    
    SYSTEM_AUTO = 1
    """시스템 자동 감지 (기본)."""
    
    OPERATOR_COMMAND = 2
    """운영자 수동 명령."""
    
    ADMIN_OVERRIDE = 3
    """Admin 강제 오버라이드 (TTL 필수)."""
    
    MAINTENANCE_MODE = 4
    """
    유지보수 모드.
    
    운영자가 긴급 점검을 위해 의도적으로 에러를 발생시키는
    상황에서 시스템 자동 대응을 일시 중지.
    반드시 TTL이 설정되어야 하며, 만료 시 자동 해제.
    """
    
    KILL_SWITCH = 5
    """최우선: Kill Switch (모든 자동화 중지)."""


class RecoveryStatus(str, Enum):
    """
    복구 상태.
    
    Emergency 복구 프로세스의 현재 단계를 나타냄.
    """
    
    NORMAL = "normal"
    """정상 상태 (복구 불필요)."""
    
    EMERGENCY = "emergency"
    """긴급 상태 (복구 대기)."""
    
    NOT_STARTED = "not_started"
    """복구 시작 전."""
    
    IN_PROGRESS = "in_progress"
    """복구 진행 중."""
    
    RECOVERING = "recovering"
    """복구 진행 중 (alias for IN_PROGRESS)."""
    
    HEALTH_CHECK = "health_check"
    """헬스 체크 수행 중."""
    
    READY_TO_RESTORE = "ready_to_restore"
    """
    수동 승인 대기 상태.
    
    8시간 자동 만료 조건 충족 + requires_manual_acknowledgement=True일 때,
    즉시 NORMAL 복구 대신 이 상태로 전환.
    
    Code reference:
        governance.py#L404 (acknowledge_warning 패턴)
    """
    
    COMPLETED = "completed"
    """복구 완료."""
    
    FAILED = "failed"
    """복구 실패."""
    
    ABORTED = "aborted"
    """복구 중단됨."""

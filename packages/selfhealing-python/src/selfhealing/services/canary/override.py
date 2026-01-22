"""
Emergency Override (Break Glass) 관련 모델 및 정책.

장애 복구를 위한 긴급 설정 변경 시 Safety Interlock을 우회하는 기능.

주요 기능:
- EmergencyOverrideRequest: 우회 요청 데이터 모델
- EmergencyOverridePolicy: 우회 정책 및 검증

주의사항:
- 장애 복구용으로만 사용해야 합니다.
- 사용 시 PIR(Post-Incident Review)가 필수입니다.
- LEVEL_3에서는 사전 승인 토큰이 필요합니다.

Reference:
    docs/self_healing/middleware_system/74_CANARY_SAFETY_INTERLOCK.md §3.8
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EmergencyOverrideRequest:
    """
    Emergency Override (Break Glass) 요청.
    
    장애 복구를 위한 긴급 설정 변경 시 Safety Interlock을 우회합니다.
    
    Attributes:
        reason: 우회 사유 (최소 10자 이상)
        requested_by: 요청자 (이메일 또는 사용자명)
        ticket_id: 관련 티켓/인시던트 ID (강력 권장)
        expires_at: 우회 만료 시각 (기본 1시간)
        approval_token: 사전 승인 토큰 (LEVEL_3에서 필수)
        acknowledged_risks: 인지한 위험 목록
    """
    
    reason: str
    """우회 사유 (필수, 최소 10자 이상)."""
    
    requested_by: str
    """요청자 (이메일 또는 사용자명)."""
    
    ticket_id: Optional[str] = None
    """관련 티켓/인시던트 ID (강력 권장)."""
    
    expires_at: Optional[str] = None
    """우회 만료 시각 ISO 형식 (None이면 기본값 1시간)."""
    
    approval_token: Optional[str] = None
    """사전 승인 토큰 (LEVEL_3에서 필수)."""
    
    acknowledged_risks: list = field(default_factory=list)
    """인지한 위험 목록."""
    
    def is_valid(self) -> bool:
        """
        요청 유효성 검증.
        
        Returns:
            True: 필수 필드가 올바르게 설정됨
            False: 필수 필드 누락 또는 유효하지 않음
        """
        if not self.reason or len(self.reason.strip()) < 10:
            return False
        if not self.requested_by or len(self.requested_by.strip()) < 1:
            return False
        return True
    
    def requires_approval_token(self, level_value: int) -> bool:
        """
        현재 레벨에서 승인 토큰이 필요한지 확인.
        
        Args:
            level_value: Emergency 레벨 값 (3 = LEVEL_3)
        
        Returns:
            True: LEVEL_3에서는 승인 토큰 필요
        """
        return level_value >= 3


@dataclass
class EmergencyOverridePolicy:
    """
    Emergency Override 정책.
    
    어떤 조건에서 Break Glass 우회를 허용할지 정의합니다.
    
    Attributes:
        enabled: Emergency Override 기능 활성화 여부
        min_reason_length: 최소 사유 길이
        require_ticket_id: 티켓/인시던트 ID 필수 여부
        require_approval_on_level_3: LEVEL_3에서 사전 승인 필수 여부
        default_ttl_minutes: 기본 우회 만료 시간 (분)
        max_ttl_minutes: 최대 우회 만료 시간 (분)
        allowed_roles: 우회 가능 역할 목록
        pir_required: Post-Incident Review 필수 여부
    """
    
    enabled: bool = True
    """Emergency Override 기능 활성화 여부."""
    
    min_reason_length: int = 10
    """최소 사유 길이."""
    
    require_ticket_id: bool = True
    """티켓/인시던트 ID 필수 여부."""
    
    require_approval_on_level_3: bool = True
    """LEVEL_3에서 사전 승인 필수 여부."""
    
    default_ttl_minutes: int = 60
    """기본 우회 만료 시간 (분)."""
    
    max_ttl_minutes: int = 240
    """최대 우회 만료 시간 (분) - 4시간."""
    
    allowed_roles: list = field(default_factory=lambda: ["admin", "sre", "on_call"])
    """우회 가능 역할 목록."""
    
    pir_required: bool = True
    """Post-Incident Review 필수 여부."""
    
    def validate_request(
        self,
        override: EmergencyOverrideRequest,
        level_value: int,
    ) -> tuple:
        """
        Override 요청 유효성 검증.
        
        Args:
            override: Override 요청
            level_value: 현재 Emergency 레벨 값
        
        Returns:
            (is_valid: bool, error_message: Optional[str])
        """
        if not self.enabled:
            return False, "Emergency Override is disabled"
        
        if not override.is_valid():
            return False, "Invalid override request: reason and requested_by are required"
        
        if len(override.reason) < self.min_reason_length:
            return False, f"Reason must be at least {self.min_reason_length} characters"
        
        if self.require_ticket_id and not override.ticket_id:
            return False, "Ticket ID is required for emergency override"
        
        if (
            self.require_approval_on_level_3
            and level_value >= 3
            and not override.approval_token
        ):
            return False, "Approval token is required for LEVEL_3 override"
        
        return True, None

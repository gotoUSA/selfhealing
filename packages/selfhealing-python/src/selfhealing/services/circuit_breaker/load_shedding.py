"""
Load Shedding (부분적 차단)

핵심 서비스에 장애 조짐이 보이면, 비핵심 서비스 트래픽을 먼저 제한하여
핵심 서비스에 리소스를 집중시킵니다.

사용 예시:
    manager = LoadSheddingManager()
    
    # 서비스 트래픽 허용 비율 조회
    allowed_traffic = manager.evaluate_shedding("review-api")  # Returns 50.0 (%)
    
    # Shedding 활성화 여부 확인
    if manager.is_shedding_active():
        print(f"Current shedding level: {manager.get_current_level()}")
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Any, Callable

from selfhealing.services.circuit_breaker.models import (
    LoadSheddingPolicy,
    SheddingLevel,
    ServiceConfig,
)

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


class SheddingState(str, Enum):
    """Load Shedding 상태."""
    
    INACTIVE = "inactive"       # Shedding 비활성화
    LEVEL_1 = "level_1"         # 1단계 (low 50% 제한)
    LEVEL_2 = "level_2"         # 2단계 (low+medium 80% 제한)
    LEVEL_3 = "level_3"         # 3단계 (low+medium 완전 차단)
    CUSTOM = "custom"           # 사용자 정의 레벨


@dataclass
class SheddingDecision:
    """
    Load Shedding 결정 결과.
    
    Attributes:
        allow_request: 요청 허용 여부
        allowed_traffic_percent: 해당 서비스에 허용된 트래픽 비율 (0~100)
        is_shed: Shedding 대상 여부
        reason: 결정 사유
        current_level: 현재 Shedding 레벨
        service_criticality: 서비스 criticality
    """
    
    allow_request: bool = True
    allowed_traffic_percent: float = 100.0
    is_shed: bool = False
    reason: str = ""
    current_level: Optional[str] = None
    service_criticality: Optional[str] = None


@dataclass
class SheddingStatus:
    """
    Load Shedding 현재 상태.
    
    Attributes:
        active: Shedding 활성화 여부
        current_state: 현재 Shedding 상태
        current_level_index: 현재 레벨 인덱스 (0-based, -1=inactive)
        critical_error_rate: critical 서비스 평균 에러율
        shed_services: 현재 Shedding 적용 중인 서비스 목록
        timestamp: 상태 조회 시간
    """
    
    active: bool = False
    current_state: SheddingState = SheddingState.INACTIVE
    current_level_index: int = -1
    current_level_description: str = ""
    critical_error_rate: float = 0.0
    shed_services: List[str] = field(default_factory=list)
    shed_criticality: List[str] = field(default_factory=list)
    traffic_limit: float = 100.0
    timestamp: str = ""
    activated_at: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "active": self.active,
            "current_state": self.current_state.value,
            "current_level_index": self.current_level_index,
            "current_level_description": self.current_level_description,
            "critical_error_rate": self.critical_error_rate,
            "shed_services": self.shed_services,
            "shed_criticality": self.shed_criticality,
            "traffic_limit": self.traffic_limit,
            "timestamp": self.timestamp,
            "activated_at": self.activated_at,
        }


@dataclass
class SheddingAuditEntry:
    """
    Load Shedding Audit 로그 엔트리.
    
    Attributes:
        event_type: 이벤트 타입
        timestamp: 이벤트 시간
        previous_level: 이전 레벨
        new_level: 새 레벨
        critical_error_rate: critical 에러율
        affected_services: 영향받는 서비스
        reason: 변경 사유
    """
    
    event_type: str  # SHEDDING_ACTIVATED, SHEDDING_LEVEL_CHANGED, SHEDDING_DEACTIVATED
    timestamp: str
    previous_level: int = -1
    new_level: int = -1
    critical_error_rate: float = 0.0
    affected_services: List[str] = field(default_factory=list)
    reason: str = ""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "previous_level": self.previous_level,
            "new_level": self.new_level,
            "critical_error_rate": self.critical_error_rate,
            "affected_services": self.affected_services,
            "reason": self.reason,
        }


# =============================================================================
# Error Rate Provider Interface
# =============================================================================


class ErrorRateProvider:
    """
    서비스별 에러율을 제공하는 인터페이스.
    
    기본 구현은 메모리 기반. 실제 환경에서는 메트릭 시스템 연동.
    """
    
    def __init__(self):
        self._error_rates: Dict[str, float] = {}
        self._success_counts: Dict[str, int] = {}
        self._failure_counts: Dict[str, int] = {}
    
    def get_error_rate(self, service_id: str) -> float:
        """
        서비스의 현재 에러율 조회 (0~100%).
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            에러율 (0~100)
        """
        return self._error_rates.get(service_id, 0.0)
    
    def set_error_rate(self, service_id: str, error_rate: float) -> None:
        """
        서비스의 에러율 설정 (테스트용).
        
        Args:
            service_id: 서비스 ID
            error_rate: 에러율 (0~100)
        """
        if not (0.0 <= error_rate <= 100.0):
            raise ValueError(f"error_rate must be between 0 and 100, got {error_rate}")
        self._error_rates[service_id] = error_rate
    
    def record_success(self, service_id: str) -> None:
        """성공 기록."""
        self._success_counts[service_id] = self._success_counts.get(service_id, 0) + 1
        self._update_error_rate(service_id)
    
    def record_failure(self, service_id: str) -> None:
        """실패 기록."""
        self._failure_counts[service_id] = self._failure_counts.get(service_id, 0) + 1
        self._update_error_rate(service_id)
    
    def _update_error_rate(self, service_id: str) -> None:
        """에러율 재계산."""
        success = self._success_counts.get(service_id, 0)
        failure = self._failure_counts.get(service_id, 0)
        total = success + failure
        if total > 0:
            self._error_rates[service_id] = (failure / total) * 100.0
    
    def reset(self, service_id: Optional[str] = None) -> None:
        """에러율 초기화."""
        if service_id:
            self._error_rates.pop(service_id, None)
            self._success_counts.pop(service_id, None)
            self._failure_counts.pop(service_id, None)
        else:
            self._error_rates.clear()
            self._success_counts.clear()
            self._failure_counts.clear()


# =============================================================================
# Load Shedding Manager
# =============================================================================


class LoadSheddingManager:
    """
    Load Shedding 관리자.
    
    핵심 서비스 에러율을 모니터링하고, 임계값 초과 시
    비핵심 서비스 트래픽을 단계적으로 제한합니다.
    
    Attributes:
        policy: Load Shedding 정책
        error_rate_provider: 에러율 제공자
        service_configs: 서비스 설정 목록
        
    Usage:
        manager = LoadSheddingManager()
        
        # 서비스 등록
        manager.register_service(ServiceConfig(
            service_id="review-api",
            criticality="low",
            shed_priority=10,
        ))
        
        # 에러율 설정 (테스트용 또는 외부 메트릭 연동)
        manager.set_error_rate("payment-api", 45.0)
        
        # Shedding 평가
        allowed = manager.evaluate_shedding("review-api")  # Returns 50.0
        
        # 요청 허용 여부 확인
        decision = manager.should_allow_request("review-api")
        if decision.allow_request:
            # 요청 처리
            pass
        
    Reference:
        docs/self_healing/middleware_system/21_CB_ADVANCED_PROTECTION.md
        Section 3 - Load Shedding (부분적 차단)
    """
    
    _instance: Optional["LoadSheddingManager"] = None
    
    def __new__(cls, *args, **kwargs):
        """싱글톤 패턴."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(
        self,
        policy: Optional[LoadSheddingPolicy] = None,
        error_rate_provider: Optional[ErrorRateProvider] = None,
    ):
        if getattr(self, '_initialized', False):
            return
        
        self._policy = policy or LoadSheddingPolicy()
        self._error_rate_provider = error_rate_provider or ErrorRateProvider()
        self._service_configs: Dict[str, ServiceConfig] = {}
        self._current_level_index: int = -1  # -1 = inactive
        self._activated_at: Optional[str] = None
        self._audit_callback: Optional[Callable[[SheddingAuditEntry], None]] = None
        self._initialized = True
        
        logger.debug("[LoadSheddingManager] Initialized")
    
    @classmethod
    def reset_instance(cls) -> None:
        """싱글톤 인스턴스 초기화 (테스트용)."""
        cls._instance = None
    
    # =========================================================================
    # Configuration
    # =========================================================================
    
    @property
    def policy(self) -> LoadSheddingPolicy:
        """현재 정책."""
        return self._policy
    
    def set_policy(self, policy: LoadSheddingPolicy) -> None:
        """정책 설정."""
        self._policy = policy
        logger.info(f"[LoadSheddingManager] Policy updated: enabled={policy.enabled}")
    
    def set_audit_callback(
        self, 
        callback: Callable[[SheddingAuditEntry], None],
    ) -> None:
        """
        Audit 콜백 설정.
        
        Args:
            callback: Audit 엔트리를 받는 콜백 함수
        """
        self._audit_callback = callback
    
    # =========================================================================
    # Service Registration
    # =========================================================================
    
    def register_service(self, config: ServiceConfig) -> bool:
        """
        서비스 등록.
        
        Args:
            config: 서비스 설정
            
        Returns:
            bool: 등록 성공 여부
        """
        self._service_configs[config.service_id] = config
        logger.debug(
            f"[LoadSheddingManager] Service registered: {config.service_id} "
            f"(criticality={config.criticality})"
        )
        return True
    
    def register_services(self, configs: List[ServiceConfig]) -> int:
        """여러 서비스 일괄 등록."""
        count = 0
        for config in configs:
            if self.register_service(config):
                count += 1
        return count
    
    def unregister_service(self, service_id: str) -> bool:
        """서비스 등록 해제."""
        if service_id in self._service_configs:
            del self._service_configs[service_id]
            return True
        return False
    
    def clear_services(self) -> None:
        """모든 서비스 등록 해제."""
        self._service_configs.clear()
    
    def get_service_config(self, service_id: str) -> Optional[ServiceConfig]:
        """서비스 설정 조회."""
        return self._service_configs.get(service_id)
    
    # =========================================================================
    # Error Rate Management
    # =========================================================================
    
    def set_error_rate(self, service_id: str, error_rate: float) -> None:
        """
        서비스 에러율 설정.
        
        Args:
            service_id: 서비스 ID
            error_rate: 에러율 (0~100)
        """
        self._error_rate_provider.set_error_rate(service_id, error_rate)
    
    def get_error_rate(self, service_id: str) -> float:
        """
        서비스 에러율 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            에러율 (0~100)
        """
        return self._error_rate_provider.get_error_rate(service_id)
    
    def record_success(self, service_id: str) -> None:
        """성공 기록."""
        self._error_rate_provider.record_success(service_id)
    
    def record_failure(self, service_id: str) -> None:
        """실패 기록."""
        self._error_rate_provider.record_failure(service_id)
    
    # =========================================================================
    # Critical Services Error Rate
    # =========================================================================
    
    def _get_critical_services(self) -> List[ServiceConfig]:
        """critical 서비스 목록 조회."""
        return [
            config for config in self._service_configs.values()
            if config.criticality == "critical"
        ]
    
    def get_critical_services_error_rate(self) -> float:
        """
        critical 서비스들의 평균 에러율 계산.
        
        Returns:
            float: 평균 에러율 (0~100), critical 서비스 없으면 0
        """
        critical_services = self._get_critical_services()
        if not critical_services:
            return 0.0
        
        total_error_rate = sum(
            self._error_rate_provider.get_error_rate(s.service_id)
            for s in critical_services
        )
        return total_error_rate / len(critical_services)
    
    # =========================================================================
    # Shedding Evaluation
    # =========================================================================
    
    def evaluate_shedding(self, service_id: str) -> float:
        """
        서비스에 대한 현재 허용 트래픽 비율 계산.
        
        critical 서비스들의 평균 에러율을 기준으로 Shedding 레벨을 결정하고,
        해당 서비스의 criticality에 따라 허용 트래픽 비율을 반환합니다.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            float: 허용 트래픽 비율 (0.0 ~ 100.0)
            
        Example:
            >>> manager.evaluate_shedding("review-api")
            50.0  # 50% 트래픽만 허용
        """
        if not self._policy.enabled:
            return 100.0
        
        service_config = self.get_service_config(service_id)
        if service_config is None:
            # 미등록 서비스는 100% 허용
            return 100.0
        
        # critical 서비스는 항상 100%
        if service_config.criticality == "critical":
            return 100.0
        
        # critical 서비스들의 평균 에러율 계산
        critical_error_rate = self.get_critical_services_error_rate()
        
        # 현재 적용할 shedding level 찾기
        applicable_level = self._find_applicable_level(
            critical_error_rate, 
            service_config.criticality,
        )
        
        if applicable_level is None:
            return 100.0
        
        # 최소 보장 트래픽 적용
        return max(
            applicable_level.traffic_limit,
            service_config.min_traffic_percentage
        )
    
    def _find_applicable_level(
        self, 
        critical_error_rate: float, 
        service_criticality: str,
    ) -> Optional[SheddingLevel]:
        """
        현재 에러율과 서비스 criticality에 맞는 Shedding 레벨 찾기.
        
        Args:
            critical_error_rate: critical 서비스 평균 에러율
            service_criticality: 서비스 criticality
            
        Returns:
            적용할 SheddingLevel 또는 None
        """
        # 에러율 높은 레벨부터 확인 (역순)
        for level in sorted(self._policy.levels, key=lambda l: l.error_rate, reverse=True):
            if critical_error_rate >= level.error_rate:
                if service_criticality in level.shed_criticality:
                    return level
        return None
    
    def should_allow_request(self, service_id: str) -> SheddingDecision:
        """
        요청 허용 여부 결정.
        
        확률적으로 트래픽을 제한합니다. 예: 50% 허용이면 50% 확률로 허용.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            SheddingDecision: 허용 여부 및 상세 정보
        """
        allowed_percent = self.evaluate_shedding(service_id)
        service_config = self.get_service_config(service_id)
        
        # 100% 허용 → 무조건 허용
        if allowed_percent >= 100.0:
            return SheddingDecision(
                allow_request=True,
                allowed_traffic_percent=100.0,
                is_shed=False,
                reason="No shedding applied",
                service_criticality=service_config.criticality if service_config else None,
            )
        
        # 0% 허용 → 무조건 차단
        if allowed_percent <= 0.0:
            current_level = self._get_current_level_description()
            return SheddingDecision(
                allow_request=False,
                allowed_traffic_percent=0.0,
                is_shed=True,
                reason=f"Fully shed - {current_level}",
                current_level=current_level,
                service_criticality=service_config.criticality if service_config else None,
            )
        
        # 확률적 허용 (0 < allowed_percent < 100)
        allow = random.random() * 100 < allowed_percent
        current_level = self._get_current_level_description()
        
        return SheddingDecision(
            allow_request=allow,
            allowed_traffic_percent=allowed_percent,
            is_shed=True,
            reason=f"Probabilistic shedding - {current_level}" if not allow else "Request allowed",
            current_level=current_level,
            service_criticality=service_config.criticality if service_config else None,
        )
    
    def _get_current_level_description(self) -> str:
        """현재 Shedding 레벨 설명 조회."""
        critical_error_rate = self.get_critical_services_error_rate()
        
        for i, level in enumerate(sorted(self._policy.levels, key=lambda l: l.error_rate, reverse=True)):
            if critical_error_rate >= level.error_rate:
                return level.description or f"Level {len(self._policy.levels) - i}"
        
        return "No shedding"
    
    # =========================================================================
    # Level Management
    # =========================================================================
    
    def get_current_level_index(self) -> int:
        """
        현재 Shedding 레벨 인덱스 조회.
        
        Returns:
            int: 레벨 인덱스 (0-based), -1이면 inactive
        """
        critical_error_rate = self.get_critical_services_error_rate()
        
        for i, level in enumerate(self._policy.levels):
            if critical_error_rate >= level.error_rate:
                # 가장 높은 매칭 레벨 찾기
                highest_index = i
                for j, lvl in enumerate(self._policy.levels[i:], start=i):
                    if critical_error_rate >= lvl.error_rate:
                        highest_index = j
                return highest_index
        
        return -1
    
    def update_shedding_state(self) -> Optional[SheddingAuditEntry]:
        """
        Shedding 상태 업데이트 및 레벨 변화 감지.
        
        주기적으로 호출하여 레벨 변화를 감지하고 Audit 기록을 생성합니다.
        
        Returns:
            레벨 변화가 있으면 SheddingAuditEntry, 없으면 None
        """
        new_level_index = self.get_current_level_index()
        previous_level_index = self._current_level_index
        
        if new_level_index == previous_level_index:
            return None
        
        # 레벨 변화 감지
        self._current_level_index = new_level_index
        timestamp = datetime.now(timezone.utc).isoformat()
        
        # Audit 엔트리 생성
        if previous_level_index == -1 and new_level_index >= 0:
            # 활성화
            event_type = "SHEDDING_ACTIVATED"
            self._activated_at = timestamp
        elif previous_level_index >= 0 and new_level_index == -1:
            # 비활성화
            event_type = "SHEDDING_DEACTIVATED"
            self._activated_at = None
        else:
            # 레벨 변화
            event_type = "SHEDDING_LEVEL_CHANGED"
        
        affected_services = self._get_affected_services(new_level_index)
        
        audit_entry = SheddingAuditEntry(
            event_type=event_type,
            timestamp=timestamp,
            previous_level=previous_level_index,
            new_level=new_level_index,
            critical_error_rate=self.get_critical_services_error_rate(),
            affected_services=[s.service_id for s in affected_services],
            reason=f"Critical error rate: {self.get_critical_services_error_rate():.1f}%",
        )
        
        # Audit 콜백 호출
        if self._audit_callback:
            try:
                self._audit_callback(audit_entry)
            except Exception as e:
                logger.error(f"[LoadSheddingManager] Audit callback failed: {e}")
        
        logger.info(
            f"[LoadSheddingManager] {event_type}: "
            f"level {previous_level_index} → {new_level_index}, "
            f"affected services: {len(affected_services)}"
        )
        
        return audit_entry
    
    def _get_affected_services(self, level_index: int) -> List[ServiceConfig]:
        """현재 레벨에서 영향받는 서비스 목록."""
        if level_index < 0 or level_index >= len(self._policy.levels):
            return []
        
        level = self._policy.levels[level_index]
        return [
            config for config in self._service_configs.values()
            if config.criticality in level.shed_criticality and config.shed_priority > 0
        ]
    
    # =========================================================================
    # Status
    # =========================================================================
    
    def is_shedding_active(self) -> bool:
        """Shedding 활성화 여부."""
        return self.get_current_level_index() >= 0
    
    def get_status(self) -> SheddingStatus:
        """
        현재 Shedding 상태 조회.
        
        Returns:
            SheddingStatus: 현재 상태 정보
        """
        level_index = self.get_current_level_index()
        active = level_index >= 0
        
        # 상태 결정
        if not active:
            state = SheddingState.INACTIVE
        elif level_index == 0:
            state = SheddingState.LEVEL_1
        elif level_index == 1:
            state = SheddingState.LEVEL_2
        elif level_index == 2:
            state = SheddingState.LEVEL_3
        else:
            state = SheddingState.CUSTOM
        
        # 현재 레벨 정보
        level_description = ""
        shed_criticality: List[str] = []
        traffic_limit = 100.0
        if active and level_index < len(self._policy.levels):
            level = self._policy.levels[level_index]
            level_description = level.description
            shed_criticality = level.shed_criticality
            traffic_limit = level.traffic_limit
        
        # 영향받는 서비스
        shed_services = [
            config.service_id 
            for config in self._service_configs.values()
            if config.criticality in shed_criticality and config.shed_priority > 0
        ]
        
        return SheddingStatus(
            active=active,
            current_state=state,
            current_level_index=level_index,
            current_level_description=level_description,
            critical_error_rate=self.get_critical_services_error_rate(),
            shed_services=shed_services,
            shed_criticality=shed_criticality,
            traffic_limit=traffic_limit,
            timestamp=datetime.now(timezone.utc).isoformat(),
            activated_at=self._activated_at,
        )
    
    # =========================================================================
    # Manual Control
    # =========================================================================
    
    def force_activate(
        self, 
        level_index: int = 0, 
        reason: str = "manual_activation",
    ) -> bool:
        """
        Shedding 강제 활성화 (테스트/운영용).
        
        Args:
            level_index: 활성화할 레벨 인덱스
            reason: 활성화 사유
            
        Returns:
            bool: 활성화 성공 여부
        """
        if level_index < 0 or level_index >= len(self._policy.levels):
            logger.warning(
                f"[LoadSheddingManager] Invalid level index: {level_index}"
            )
            return False
        
        # 해당 레벨의 error_rate를 강제로 설정
        target_level = self._policy.levels[level_index]
        critical_services = self._get_critical_services()
        
        for service in critical_services:
            self.set_error_rate(service.service_id, target_level.error_rate + 1.0)
        
        # 상태 업데이트
        self.update_shedding_state()
        
        logger.info(
            f"[LoadSheddingManager] Force activated at level {level_index}: {reason}"
        )
        return True
    
    def force_deactivate(self, reason: str = "manual_deactivation") -> bool:
        """
        Shedding 강제 비활성화.
        
        Args:
            reason: 비활성화 사유
            
        Returns:
            bool: 비활성화 성공 여부
        """
        # 모든 critical 서비스 에러율을 0으로
        critical_services = self._get_critical_services()
        
        for service in critical_services:
            self.set_error_rate(service.service_id, 0.0)
        
        # 상태 업데이트
        self.update_shedding_state()
        
        logger.info(f"[LoadSheddingManager] Force deactivated: {reason}")
        return True
    
    def reset(self) -> None:
        """전체 상태 초기화 (테스트용)."""
        self._error_rate_provider.reset()
        self._current_level_index = -1
        self._activated_at = None
        logger.debug("[LoadSheddingManager] Reset complete")


# =============================================================================
# Load Shedding Middleware
# =============================================================================


class LoadSheddingMiddleware:
    """
    Load Shedding Middleware.
    
    요청 처리 전에 Load Shedding 정책을 적용하여 트래픽을 제한합니다.
    
    Usage:
        middleware = LoadSheddingMiddleware(manager)
        
        # Django middleware처럼 사용
        def process_request(service_id, request):
            decision = middleware.process(service_id)
            if not decision.allow_request:
                return Response(status=503, detail=decision.reason)
            # 정상 처리
    """
    
    def __init__(
        self, 
        manager: Optional[LoadSheddingManager] = None,
        on_shed_callback: Optional[Callable[[str, SheddingDecision], None]] = None,
    ):
        """
        초기화.
        
        Args:
            manager: LoadSheddingManager 인스턴스 (없으면 싱글톤 사용)
            on_shed_callback: Shedding 발생 시 콜백 (메트릭, 로깅 등)
        """
        self._manager = manager
        self._on_shed_callback = on_shed_callback
    
    @property
    def manager(self) -> LoadSheddingManager:
        """Manager 인스턴스."""
        if self._manager is None:
            self._manager = get_load_shedding_manager()
        return self._manager
    
    def process(self, service_id: str) -> SheddingDecision:
        """
        요청에 대한 Shedding 결정.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            SheddingDecision: 허용 여부 및 상세 정보
        """
        decision = self.manager.should_allow_request(service_id)
        
        if decision.is_shed and self._on_shed_callback:
            try:
                self._on_shed_callback(service_id, decision)
            except Exception as e:
                logger.error(f"[LoadSheddingMiddleware] Callback failed: {e}")
        
        return decision
    
    def record_result(self, service_id: str, success: bool) -> None:
        """
        요청 결과 기록.
        
        Args:
            service_id: 서비스 ID
            success: 성공 여부
        """
        if success:
            self.manager.record_success(service_id)
        else:
            self.manager.record_failure(service_id)


# =============================================================================
# Load Shedding Dashboard API
# =============================================================================


class LoadSheddingDashboard:
    """
    Load Shedding 대시보드 API.
    
    운영자가 Shedding 상태를 조회하고 제어할 수 있는 API를 제공합니다.
    
    Usage:
        dashboard = LoadSheddingDashboard(manager)
        
        # 현재 상태 조회
        status = dashboard.get_status()
        
        # 수동 활성화
        dashboard.activate(level=1, reason="Planned maintenance")
        
        # 수동 비활성화
        dashboard.deactivate(reason="Recovery confirmed")
    """
    
    def __init__(self, manager: Optional[LoadSheddingManager] = None):
        """초기화."""
        self._manager = manager
    
    @property
    def manager(self) -> LoadSheddingManager:
        """Manager 인스턴스."""
        if self._manager is None:
            self._manager = get_load_shedding_manager()
        return self._manager
    
    def get_status(self) -> Dict[str, Any]:
        """
        현재 Shedding 상태 조회.
        
        Returns:
            상태 딕셔너리
        """
        return self.manager.get_status().to_dict()
    
    def get_service_status(self, service_id: str) -> Dict[str, Any]:
        """
        특정 서비스의 Shedding 상태 조회.
        
        Args:
            service_id: 서비스 ID
            
        Returns:
            서비스별 상태 딕셔너리
        """
        allowed_percent = self.manager.evaluate_shedding(service_id)
        config = self.manager.get_service_config(service_id)
        
        return {
            "service_id": service_id,
            "allowed_traffic_percent": allowed_percent,
            "is_shed": allowed_percent < 100.0,
            "criticality": config.criticality if config else "unknown",
            "shed_priority": config.shed_priority if config else 0,
            "min_traffic_percentage": config.min_traffic_percentage if config else 0.0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    
    def get_all_services_status(self) -> List[Dict[str, Any]]:
        """
        모든 서비스의 Shedding 상태 조회.
        
        Returns:
            서비스별 상태 리스트
        """
        return [
            self.get_service_status(service_id)
            for service_id in self.manager._service_configs.keys()
        ]
    
    def activate(
        self, 
        level: int = 0, 
        reason: str = "manual_activation",
        operator: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Shedding 수동 활성화.
        
        Args:
            level: 활성화할 레벨 (0-based)
            reason: 활성화 사유
            operator: 운영자 ID
            
        Returns:
            결과 딕셔너리
        """
        success = self.manager.force_activate(level, f"{reason} (by {operator})")
        
        return {
            "success": success,
            "action": "activate",
            "level": level,
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_status": self.get_status(),
        }
    
    def deactivate(
        self, 
        reason: str = "manual_deactivation",
        operator: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Shedding 수동 비활성화.
        
        Args:
            reason: 비활성화 사유
            operator: 운영자 ID
            
        Returns:
            결과 딕셔너리
        """
        success = self.manager.force_deactivate(f"{reason} (by {operator})")
        
        return {
            "success": success,
            "action": "deactivate",
            "reason": reason,
            "operator": operator,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "current_status": self.get_status(),
        }
    
    def get_policy(self) -> Dict[str, Any]:
        """
        현재 정책 조회.
        
        Returns:
            정책 딕셔너리
        """
        policy = self.manager.policy
        return {
            "enabled": policy.enabled,
            "trigger_threshold": policy.trigger_threshold,
            "levels": [
                {
                    "error_rate": level.error_rate,
                    "shed_criticality": level.shed_criticality,
                    "traffic_limit": level.traffic_limit,
                    "description": level.description,
                }
                for level in policy.levels
            ],
        }


# =============================================================================
# Module-level Convenience Functions
# =============================================================================


_manager: Optional[LoadSheddingManager] = None
_middleware: Optional[LoadSheddingMiddleware] = None
_dashboard: Optional[LoadSheddingDashboard] = None


def get_load_shedding_manager() -> LoadSheddingManager:
    """
    LoadSheddingManager 싱글톤 인스턴스 반환.
    
    Returns:
        LoadSheddingManager: 싱글톤 인스턴스
    """
    global _manager
    if _manager is None:
        _manager = LoadSheddingManager()
    return _manager


def reset_load_shedding_manager() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _manager, _middleware, _dashboard
    if _manager is not None:
        _manager.reset()
    _manager = None
    _middleware = None
    _dashboard = None
    LoadSheddingManager.reset_instance()


def get_load_shedding_middleware() -> LoadSheddingMiddleware:
    """
    LoadSheddingMiddleware 인스턴스 반환.
    
    Returns:
        LoadSheddingMiddleware 인스턴스
    """
    global _middleware
    if _middleware is None:
        _middleware = LoadSheddingMiddleware(get_load_shedding_manager())
    return _middleware


def get_load_shedding_dashboard() -> LoadSheddingDashboard:
    """
    LoadSheddingDashboard 인스턴스 반환.
    
    Returns:
        LoadSheddingDashboard 인스턴스
    """
    global _dashboard
    if _dashboard is None:
        _dashboard = LoadSheddingDashboard(get_load_shedding_manager())
    return _dashboard


# =============================================================================
# Convenience Functions
# =============================================================================


def register_load_shedding_service(config: ServiceConfig) -> bool:
    """
    서비스 등록.
    
    Args:
        config: 서비스 설정
        
    Returns:
        bool: 등록 성공 여부
    """
    return get_load_shedding_manager().register_service(config)


def evaluate_shedding(service_id: str) -> float:
    """
    서비스 트래픽 허용 비율 조회.
    
    Args:
        service_id: 서비스 ID
        
    Returns:
        float: 허용 트래픽 비율 (0~100)
    """
    return get_load_shedding_manager().evaluate_shedding(service_id)


def should_allow_shedding_request(service_id: str) -> SheddingDecision:
    """
    요청 허용 여부 결정.
    
    Args:
        service_id: 서비스 ID
        
    Returns:
        SheddingDecision: 허용 여부 및 상세 정보
    """
    return get_load_shedding_manager().should_allow_request(service_id)


def is_shedding_active() -> bool:
    """
    Shedding 활성화 여부.
    
    Returns:
        bool: 활성화 여부
    """
    return get_load_shedding_manager().is_shedding_active()


def get_shedding_status() -> SheddingStatus:
    """
    현재 Shedding 상태 조회.
    
    Returns:
        SheddingStatus: 현재 상태
    """
    return get_load_shedding_manager().get_status()


def set_service_error_rate(service_id: str, error_rate: float) -> None:
    """
    서비스 에러율 설정 (테스트/외부 메트릭 연동용).
    
    Args:
        service_id: 서비스 ID
        error_rate: 에러율 (0~100)
    """
    get_load_shedding_manager().set_error_rate(service_id, error_rate)


def update_shedding_state() -> Optional[SheddingAuditEntry]:
    """
    Shedding 상태 업데이트.
    
    Returns:
        레벨 변화 시 SheddingAuditEntry, 없으면 None
    """
    return get_load_shedding_manager().update_shedding_state()

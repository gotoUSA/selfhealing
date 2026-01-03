"""
Tiering Middleware.

Django Middleware for Emergency Mode Traffic Control.
Controls traffic based on API tier during emergency mode.
"""

from __future__ import annotations

import logging
import random
from typing import Optional

from .registry import get_tier_registry

logger = logging.getLogger(__name__)


class TieringMiddleware:
    """
    Django Middleware for Emergency Mode Traffic Control.
    
    비상 모드(Emergency Mode)에서 API Tier에 따라 트래픽을 제어합니다.
    
    동작 방식:
    1. EmergencyManager에서 현재 비상 모드 레벨 확인
    2. 요청 경로의 Tier 확인 (TierRegistry 사용)
    3. Tier의 multiplier에 따라 확률적으로 요청 허용/차단
    4. 차단 시 503 Service Unavailable 응답
    
    Emergency Level별 동작:
    - NORMAL (0): 모든 요청 허용
    - LEVEL_1 (1): non_essential 차단
    - LEVEL_2 (2): standard 90% 차단, non_essential 100% 차단
    - LEVEL_3 (3): critical 50% 차단, standard/non_essential 100% 차단
    
    Configuration:
        # settings.py
        MIDDLEWARE = [
            ...
            'selfhealing.api.django.tiering.TieringMiddleware',
            ...
        ]
        
        # Optional: Disable middleware
        SELFHEALING_TIERING_MIDDLEWARE_ENABLED = True
    
    Reference:
    - docs/self_healing/16_GOVERNANCE_IMPLEMENTATION_PART1A.md (Section 3)
    - Netflix Hystrix Load Shedding
    - Google SRE "Handling Overload"
    """
    
    def __init__(self, get_response):
        """
        Initialize middleware.
        
        Args:
            get_response: Django's get_response callable
        """
        self.get_response = get_response
        self._registry = get_tier_registry()
        self._random = random.Random()
        
        self._enabled = self._check_enabled()
        
        if self._enabled:
            logger.info("[TieringMiddleware] Initialized and enabled")
        else:
            logger.info("[TieringMiddleware] Initialized but DISABLED")
    
    def _check_enabled(self) -> bool:
        """Check if middleware is enabled via settings."""
        try:
            from django.conf import settings
            return getattr(settings, 'SELFHEALING_TIERING_MIDDLEWARE_ENABLED', True)
        except Exception:
            return True
    
    def __call__(self, request):
        """
        Process the request.
        
        Args:
            request: Django HttpRequest
            
        Returns:
            HttpResponse
        """
        if not self._enabled:
            return self.get_response(request)
        
        try:
            from selfhealing.services.emergency_mode import get_emergency_manager
            from selfhealing.services.emergency_mode.enums import (
                EmergencyLevel,
                EMERGENCY_LEVEL_RULES,
            )
            
            manager = get_emergency_manager()
            
            if not manager.is_active():
                return self.get_response(request)
            
            current_level = manager.get_current_level()
            
            if current_level == EmergencyLevel.NORMAL:
                return self.get_response(request)
            
            path = request.path
            client_ip = self._get_client_ip(request)
            user_id = self._get_user_id(request)
            
            tier_result = self._registry.resolve_tier_with_fallback(
                path=path,
                client_ip=client_ip,
                user_id=str(user_id) if user_id else None,
            )
            
            level_rules = EMERGENCY_LEVEL_RULES.get(current_level, {})
            multiplier = level_rules.get(tier_result.tier_id, 0.0)
            
            if not self._should_allow_request(multiplier):
                return self._create_load_shedding_response(
                    request=request,
                    tier_id=tier_result.tier_id,
                    multiplier=multiplier,
                    emergency_level=current_level,
                )
            
            return self.get_response(request)
            
        except Exception as e:
            logger.error(f"[TieringMiddleware] Error: {e}, allowing request")
            return self.get_response(request)
    
    def _get_client_ip(self, request) -> Optional[str]:
        """Extract client IP from request."""
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            return x_forwarded_for.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR')
    
    def _get_user_id(self, request) -> Optional[int]:
        """Extract user ID from request."""
        if hasattr(request, 'user') and request.user.is_authenticated:
            return request.user.id
        return None
    
    def _should_allow_request(self, multiplier: float) -> bool:
        """
        Determine if request should be allowed based on multiplier.
        
        Args:
            multiplier: Traffic multiplier (0.0 = block all, 1.0 = allow all)
            
        Returns:
            True if request should be allowed
        """
        if multiplier >= 1.0:
            return True
        if multiplier <= 0.0:
            return False
        
        return self._random.random() < multiplier
    
    def _create_load_shedding_response(
        self,
        request,
        tier_id: str,
        multiplier: float,
        emergency_level,
    ):
        """
        Create a 503 Load Shedding response.
        """
        from django.http import JsonResponse
        
        logger.warning(
            f"[TieringMiddleware] Load shedding: "
            f"path={request.path}, tier={tier_id}, "
            f"multiplier={multiplier}, level={emergency_level.name}"
        )
        
        self._record_load_shedding_metrics(tier_id, emergency_level)
        
        response = JsonResponse(
            {
                "error": "Service Temporarily Unavailable",
                "code": "LOAD_SHEDDING",
                "message": (
                    f"시스템 부하 관리를 위해 요청이 일시적으로 제한되었습니다. "
                    f"잠시 후 다시 시도해주세요."
                ),
                "tier": tier_id,
                "emergency_level": emergency_level.name,
                "retry_after": 30,
            },
            status=503,
        )
        response['Retry-After'] = '30'
        
        return response
    
    def _record_load_shedding_metrics(self, tier_id: str, emergency_level):
        """Record load shedding metrics to Prometheus."""
        try:
            from prometheus_client import Counter
            
            counter = Counter(
                'selfhealing_tiering_load_shedding_total',
                'Total load shedding events by tier and level',
                ['tier_id', 'emergency_level'],
                registry=None,
            )
            counter.labels(
                tier_id=tier_id,
                emergency_level=emergency_level.name,
            ).inc()
        except Exception:
            pass  # Best-effort metrics

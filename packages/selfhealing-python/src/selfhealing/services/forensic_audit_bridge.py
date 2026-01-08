"""
Forensic-Audit 브릿지 모듈.

Forensic 캡처 이벤트를 Audit 시스템에 연결합니다.

Part 2: 27_IMPROVEMENT_PART2_AUDIT_INTEGRATION.md

Phase 3: Forensic 민감정보 마스킹 연동
- mask_sensitive_fields() 사용하여 실제 마스킹 수행
- ForensicSettings.sensitive_field_patterns 연동
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 기본 민감 필드 패턴 (ForensicSettings가 없을 때 사용)
DEFAULT_SENSITIVE_PATTERNS = [
    "password",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "credential",
    "private_key",
    "credit_card",
    "ssn",
    "social_security",
]


class ForensicAuditBridge:
    """
    Forensic 캡처를 Audit 시스템에 연결.
    
    Forensic 캡처 시점:
    1. 예외 발생 시 스택 트레이스 캡처
    2. 주기적 메모리 스냅샷
    3. 이상 패턴 감지 시 컨텍스트 캡처
    
    Phase 3 개선:
    - 실제 민감정보 마스킹 수행 (GDPR/ISMS 준수)
    - ForensicSettings.sensitive_field_patterns 연동
    """
    
    def __init__(self, audit_adapter=None, sensitive_patterns: Optional[List[str]] = None):
        """
        ForensicAuditBridge 초기화.
        
        Args:
            audit_adapter: Audit 어댑터 (이벤트 기록용)
            sensitive_patterns: 민감 필드 패턴 목록 (None이면 기본값 사용)
        """
        self._audit_adapter = audit_adapter
        self._sensitive_patterns = sensitive_patterns
    
    def _get_sensitive_patterns(self) -> List[str]:
        """
        민감 필드 패턴 목록 반환.
        
        우선순위:
        1. 생성자에서 주입된 패턴
        2. ForensicSettings.sensitive_field_patterns
        3. 기본 패턴
        """
        if self._sensitive_patterns is not None:
            return self._sensitive_patterns
        
        # ForensicSettings에서 패턴 가져오기 시도
        try:
            from selfhealing.config import get_forensic_settings
            settings = get_forensic_settings()
            if hasattr(settings, 'sensitive_field_patterns') and settings.sensitive_field_patterns:
                return list(settings.sensitive_field_patterns)
        except ImportError:
            pass
        except Exception as e:
            logger.debug(f"[ForensicAuditBridge] Failed to get ForensicSettings: {e}")
        
        return DEFAULT_SENSITIVE_PATTERNS
    
    def _mask_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        컨텍스트의 민감 정보 마스킹.
        
        Phase 3: masking.py의 mask_sensitive_fields() 사용
        
        Args:
            context: 원본 컨텍스트
            
        Returns:
            마스킹된 컨텍스트
        """
        try:
            from selfhealing.audit.masking import mask_sensitive_fields
            patterns = self._get_sensitive_patterns()
            return mask_sensitive_fields(context, patterns)
        except ImportError:
            logger.debug("[ForensicAuditBridge] masking module not available, using fallback")
            return self._fallback_mask(context)
        except Exception as e:
            logger.debug(f"[ForensicAuditBridge] Masking failed: {e}")
            return self._fallback_mask(context)
    
    def _fallback_mask(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """mask_sensitive_fields 사용 불가 시 폴백 마스킹."""
        patterns = self._get_sensitive_patterns()
        result = {}
        for key, value in context.items():
            key_lower = key.lower()
            is_sensitive = any(p in key_lower for p in patterns)
            if is_sensitive:
                result[key] = "***REDACTED***"
            elif isinstance(value, dict):
                result[key] = self._fallback_mask(value)
            else:
                result[key] = value
        return result
    
    def on_exception_captured(
        self,
        exception: Exception,
        stack_trace: str,
        context: Dict[str, Any],
        sanitized: bool = True,
    ) -> None:
        """
        예외 캡처 시 Audit 기록.
        
        Phase 3 개선: sanitized=True일 때 실제 마스킹 수행
        
        Args:
            exception: 캡처된 예외
            stack_trace: 스택 트레이스 (ForensicSettings.max_stack_depth 적용됨)
            context: 실행 컨텍스트
            sanitized: 민감 정보 마스킹 여부
        """
        # Phase 3: 실제 마스킹 수행
        masked_context = context
        if sanitized:
            masked_context = self._mask_context(context)
        
        self._record_audit(
            event_type="FORENSIC_CAPTURE_COMPLETED",
            details={
                "exception_type": type(exception).__name__,
                "exception_message": str(exception)[:500],
                "stack_depth": len(stack_trace.split("\n")),
                "context_keys": list(masked_context.keys()),
                "sanitized": sanitized,
                "capture_reason": "exception",
            },
        )
    
    def on_anomaly_detected(
        self,
        anomaly_type: str,
        score: float,
        threshold: float,
        context: Dict[str, Any],
    ) -> None:
        """
        이상 패턴 감지 시 Audit 기록.
        
        Args:
            anomaly_type: 이상 유형 (statistical, behavioral 등)
            score: 이상 점수
            threshold: 감지 임계값
            context: 관련 컨텍스트
        """
        self._record_audit(
            event_type="FORENSIC_ANOMALY_DETECTED",
            details={
                "anomaly_type": anomaly_type,
                "score": score,
                "threshold": threshold,
                "exceeded_by": score - threshold,
                "context_summary": self._summarize_context(context),
            },
        )
    
    def on_memory_snapshot(
        self,
        snapshot_id: str,
        memory_mb: float,
        object_count: int,
    ) -> None:
        """
        메모리 스냅샷 시 Audit 기록 (설정에서 활성화된 경우).
        
        Args:
            snapshot_id: 스냅샷 ID
            memory_mb: 메모리 사용량 (MB)
            object_count: 객체 수
        """
        self._record_audit(
            event_type="FORENSIC_CAPTURE_STARTED",
            details={
                "capture_type": "memory_snapshot",
                "snapshot_id": snapshot_id,
                "memory_mb": memory_mb,
                "object_count": object_count,
            },
        )
    
    def _summarize_context(self, context: Dict[str, Any]) -> Dict[str, str]:
        """컨텍스트 요약 (크기 제한)."""
        summary = {}
        for key, value in context.items():
            value_str = str(value)
            if len(value_str) > 100:
                summary[key] = f"{value_str[:100]}... (truncated)"
            else:
                summary[key] = value_str
        return summary
    
    def _record_audit(self, event_type: str, details: Dict[str, Any]) -> None:
        """Audit 시스템에 기록."""
        if self._audit_adapter:
            try:
                self._audit_adapter.log_event(
                    event_type=event_type,
                    source="ForensicCapture",
                    details=details,
                )
            except Exception as e:
                logger.debug(f"[ForensicAuditBridge] Audit recording failed: {e}")


# Singleton 패턴
_bridge_instance: Optional[ForensicAuditBridge] = None


def get_forensic_audit_bridge(audit_adapter=None) -> ForensicAuditBridge:
    """
    ForensicAuditBridge 싱글톤 인스턴스 반환.
    
    Args:
        audit_adapter: Audit 어댑터 (최초 호출 시에만 사용)
        
    Returns:
        ForensicAuditBridge 인스턴스
    """
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = ForensicAuditBridge(audit_adapter)
    return _bridge_instance

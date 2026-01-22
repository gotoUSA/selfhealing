"""
Cascade Event Auditor - 연계 이벤트 감사기.

연계 이벤트를 생성, 저장, 조회하고 해시 체인 무결성을 검증합니다.

Features:
- Cascade Event 생성 및 저장
- Hash Chain 연결
- 무결성 검증
- 인과관계 조회

Usage:
    from selfhealing.audit.cascade_auditor import get_cascade_event_auditor
    
    auditor = get_cascade_event_auditor()
    
    cascade_event = auditor.record(
        trigger_type="EMERGENCY_LEVEL_CHANGED",
        trigger_details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
        effects=[
            {"action_type": "GOVERNANCE_STRICT", "success": True},
            {"action_type": "CANARY_ROLLBACK", "success": True, "target": "rollout-123"},
        ],
        namespace="seoul",
        triggered_by="system",
    )
    
    # 조회
    event = auditor.get_cascade_event("cascade-abc123", "seoul")
    events = auditor.get_recent_events("seoul", limit=100)
    
    # 무결성 검증
    result = auditor.verify_chain_integrity("seoul")
    if result["valid"]:
        print("Hash chain is valid")

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from selfhealing.audit.cascade_event import (
    CascadeEffect,
    CascadeEvent,
    CascadeTrigger,
    ExternalTraceContext,
    ManualInterventionEffect,
    generate_cascade_id,
    generate_event_id,
    get_current_timestamp,
)

logger = logging.getLogger(__name__)


class CascadeEventAuditor:
    """
    Cascade Event 감사기.
    
    연계 이벤트를 생성, 저장, 조회하고 해시 체인 무결성을 검증합니다.
    
    Features:
    - Cascade Event 생성 및 저장
    - Hash Chain 연결
    - 무결성 검증
    - 인과관계 조회
    """
    
    # Redis 키 패턴
    CASCADE_KEY = "selfhealing:{namespace}:audit:cascade:{cascade_id}"
    CASCADE_INDEX_KEY = "selfhealing:{namespace}:audit:cascade_index"
    LAST_HASH_KEY = "selfhealing:{namespace}:audit:cascade_last_hash"
    
    # 인덱스 최대 크기
    MAX_INDEX_SIZE = 10000
    
    def __init__(self) -> None:
        self._lock = threading.RLock()
    
    def _get_backend(self):
        """State backend 획득."""
        from selfhealing.core.state_backend import get_state_backend
        return get_state_backend()
    
    def record(
        self,
        trigger_type: str,
        trigger_details: Dict[str, Any],
        effects: List[Dict[str, Any]],
        namespace: str,
        triggered_by: Optional[str] = None,
        external_trace: Optional[ExternalTraceContext] = None,
    ) -> CascadeEvent:
        """
        Cascade Event 기록.
        
        Args:
            trigger_type: 트리거 유형 (EMERGENCY_LEVEL_CHANGED, MANUAL_ACTIVATION 등)
            trigger_details: 트리거 상세 정보
            effects: 연쇄 효과 목록 (각 항목은 action_type, success 등 포함)
            namespace: 네임스페이스
            triggered_by: 트리거 주체 (user, system)
            external_trace: 외부 분산 추적 컨텍스트 (선택)
        
        Returns:
            생성된 CascadeEvent
        """
        with self._lock:
            # 1. ID 생성
            cascade_id = generate_cascade_id()
            trigger_event_id = generate_event_id()
            now = get_current_timestamp()
            
            # 2. 트리거 생성
            trigger = CascadeTrigger(
                trigger_type=trigger_type,
                event_id=trigger_event_id,
                details=trigger_details,
                triggered_by=triggered_by,
            )
            
            # 3. 효과 생성
            cascade_effects = self._create_effects(effects, trigger_event_id, now)
            
            # 4. 이전 해시 조회
            previous_hash = self._get_last_hash(namespace)
            
            # 5. Cascade Event 생성
            cascade_event = CascadeEvent(
                id=cascade_id,
                trigger=trigger,
                effects=cascade_effects,
                namespace=namespace,
                timestamp=now,
                previous_hash=previous_hash,
                external_trace=external_trace,
            )
            
            # 6. 해시 계산 및 설정
            cascade_event.current_hash = cascade_event.calculate_hash()
            
            # 7. 저장
            self._save_cascade_event(cascade_event)
            self._update_last_hash(namespace, cascade_event.current_hash)
            self._add_to_index(namespace, cascade_id)
            
            logger.info(
                f"[CascadeAudit] Recorded: id={cascade_id}, "
                f"trigger={trigger_type}, effects={len(cascade_effects)}, "
                f"namespace={namespace}"
            )
            
            return cascade_event
    
    def _create_effects(
        self,
        effects_data: List[Dict[str, Any]],
        trigger_event_id: str,
        timestamp: str,
    ) -> List[CascadeEffect]:
        """
        효과 목록 생성.
        
        각 효과의 caused_by가 명시되지 않으면 이전 이벤트 ID를 사용합니다.
        """
        cascade_effects: List[CascadeEffect] = []
        previous_event_id = trigger_event_id
        
        for effect_data in effects_data:
            effect_event_id = generate_event_id()
            
            # ManualInterventionEffect 여부 확인
            if effect_data.get("intervention_type"):
                effect = ManualInterventionEffect(
                    event_id=effect_event_id,
                    action_type=effect_data.get("action_type", "UNKNOWN"),
                    caused_by=effect_data.get("caused_by", previous_event_id),
                    success=effect_data.get("success", True),
                    target=effect_data.get("target"),
                    details=effect_data.get("details", {}),
                    error_message=effect_data.get("error_message"),
                    executed_at=timestamp,
                    intervention_type=effect_data.get("intervention_type"),
                    overridden_decision=effect_data.get("overridden_decision"),
                    justification=effect_data.get("justification"),
                    approved_by=effect_data.get("approved_by"),
                    related_cascade_id=effect_data.get("related_cascade_id"),
                )
            else:
                effect = CascadeEffect(
                    event_id=effect_event_id,
                    action_type=effect_data.get("action_type", "UNKNOWN"),
                    caused_by=effect_data.get("caused_by", previous_event_id),
                    success=effect_data.get("success", True),
                    target=effect_data.get("target"),
                    details=effect_data.get("details", {}),
                    error_message=effect_data.get("error_message"),
                    executed_at=timestamp,
                )
            
            cascade_effects.append(effect)
            previous_event_id = effect_event_id
        
        return cascade_effects
    
    def get_cascade_event(
        self,
        cascade_id: str,
        namespace: str,
    ) -> Optional[CascadeEvent]:
        """
        Cascade Event 조회.
        
        Args:
            cascade_id: Cascade Event ID
            namespace: 네임스페이스
        
        Returns:
            CascadeEvent 또는 None
        """
        backend = self._get_backend()
        key = self.CASCADE_KEY.format(namespace=namespace, cascade_id=cascade_id)
        data = backend.get(key)
        
        if data:
            return CascadeEvent.from_dict(data)
        return None
    
    def get_recent_events(
        self,
        namespace: str,
        limit: int = 100,
    ) -> List[CascadeEvent]:
        """
        최근 Cascade Event 목록 조회.
        
        Args:
            namespace: 네임스페이스
            limit: 최대 개수
        
        Returns:
            CascadeEvent 목록 (최신순)
        """
        backend = self._get_backend()
        index_key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        
        # 인덱스에서 최근 ID 목록 조회
        index_data = backend.get(index_key)
        if not index_data:
            return []
        
        cascade_ids = index_data if isinstance(index_data, list) else index_data.get("ids", [])
        cascade_ids = cascade_ids[:limit]
        
        events = []
        for cascade_id in cascade_ids:
            event = self.get_cascade_event(cascade_id, namespace)
            if event:
                events.append(event)
        
        return events
    
    def verify_chain_integrity(
        self,
        namespace: str,
        limit: int = 1000,
    ) -> Dict[str, Any]:
        """
        Hash Chain 무결성 검증.
        
        Args:
            namespace: 네임스페이스
            limit: 검증할 최대 이벤트 수
        
        Returns:
            검증 결과 딕셔너리:
            - valid: 무결성 유효 여부
            - checked: 검증한 이벤트 수
            - errors: 오류 목록
        """
        events = self.get_recent_events(namespace, limit)
        
        if not events:
            return {"valid": True, "checked": 0, "errors": []}
        
        errors = []
        
        for i, event in enumerate(events):
            # 1. 해시 재계산
            recalculated_hash = event.calculate_hash()
            if recalculated_hash != event.current_hash:
                errors.append({
                    "cascade_id": event.id,
                    "error": "hash_mismatch",
                    "expected": event.current_hash,
                    "actual": recalculated_hash,
                })
            
            # 2. 체인 연결 확인 (마지막 제외)
            # 최신순 정렬이므로 i=0이 최신, i+1이 이전 이벤트
            if i < len(events) - 1:
                older_event = events[i + 1]
                if event.previous_hash != older_event.current_hash:
                    errors.append({
                        "cascade_id": event.id,
                        "error": "chain_broken",
                        "expected_previous": older_event.current_hash,
                        "actual_previous": event.previous_hash,
                    })
        
        return {
            "valid": len(errors) == 0,
            "checked": len(events),
            "errors": errors,
        }
    
    def find_by_trigger_event(
        self,
        trigger_event_id: str,
        namespace: str,
    ) -> Optional[CascadeEvent]:
        """
        트리거 이벤트 ID로 Cascade Event 조회.
        
        Args:
            trigger_event_id: 트리거 이벤트 ID
            namespace: 네임스페이스
        
        Returns:
            CascadeEvent 또는 None
        """
        events = self.get_recent_events(namespace, limit=1000)
        
        for event in events:
            if event.trigger.event_id == trigger_event_id:
                return event
        
        return None
    
    def get_causation_trace(
        self,
        effect_event_id: str,
        namespace: str,
    ) -> List[Dict[str, Any]]:
        """
        효과 이벤트의 인과관계 추적.
        
        특정 효과가 왜 발생했는지 역추적합니다.
        
        Args:
            effect_event_id: 효과 이벤트 ID
            namespace: 네임스페이스
        
        Returns:
            인과관계 추적 결과 (트리거까지 역추적)
        """
        events = self.get_recent_events(namespace, limit=1000)
        
        for cascade in events:
            for effect in cascade.effects:
                if effect.event_id == effect_event_id:
                    # 인과관계 역추적
                    trace = []
                    current_id = effect_event_id
                    
                    while True:
                        # 현재 ID에 해당하는 효과 찾기
                        found = False
                        for e in cascade.effects:
                            if e.event_id == current_id:
                                trace.append({
                                    "event_id": e.event_id,
                                    "action_type": e.action_type,
                                    "caused_by": e.caused_by,
                                })
                                current_id = e.caused_by
                                found = True
                                break
                        
                        if not found:
                            # 트리거에 도달
                            if current_id == cascade.trigger.event_id:
                                trace.append({
                                    "event_id": cascade.trigger.event_id,
                                    "action_type": cascade.trigger.trigger_type,
                                    "caused_by": None,
                                })
                            break
                    
                    return list(reversed(trace))
        
        return []
    
    def record_with_external_trace(
        self,
        trigger_type: str,
        trigger_details: Dict[str, Any],
        effects: List[Dict[str, Any]],
        namespace: str,
        request: Optional[Any] = None,
        triggered_by: Optional[str] = None,
    ) -> CascadeEvent:
        """
        외부 Trace Context를 포함하여 Cascade Event 기록.
        
        Django HttpRequest에서 W3C Trace Context를 추출합니다.
        """
        external_trace = None
        if request:
            headers = {}
            meta = getattr(request, "META", {})
            
            # HTTP_ 접두사를 제거하고 소문자로 변환
            header_mappings = {
                "HTTP_TRACEPARENT": "traceparent",
                "HTTP_TRACESTATE": "tracestate",
                "HTTP_BAGGAGE": "baggage",
                "HTTP_X_AMZN_TRACE_ID": "x-amzn-trace-id",
                "HTTP_X_REQUEST_ID": "x-request-id",
                "HTTP_X_CORRELATION_ID": "x-correlation-id",
            }
            
            for meta_key, header_key in header_mappings.items():
                if meta_key in meta:
                    headers[header_key] = meta[meta_key]
            
            if headers:
                external_trace = ExternalTraceContext.from_headers(headers)
        
        return self.record(
            trigger_type=trigger_type,
            trigger_details=trigger_details,
            effects=effects,
            namespace=namespace,
            triggered_by=triggered_by,
            external_trace=external_trace,
        )
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _get_last_hash(self, namespace: str) -> Optional[str]:
        """마지막 해시 조회."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        data = backend.get(key)
        if data:
            return data.get("hash") if isinstance(data, dict) else data
        return None
    
    def _update_last_hash(self, namespace: str, hash_value: str) -> None:
        """마지막 해시 업데이트."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        backend.set(key, {"hash": hash_value})
    
    def _save_cascade_event(self, event: CascadeEvent) -> None:
        """Cascade Event 저장."""
        backend = self._get_backend()
        key = self.CASCADE_KEY.format(
            namespace=event.namespace,
            cascade_id=event.id,
        )
        backend.set(key, event.to_dict())
    
    def _add_to_index(self, namespace: str, cascade_id: str) -> None:
        """인덱스에 추가 (최신순)."""
        backend = self._get_backend()
        key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        
        # 기존 인덱스 조회
        index_data = backend.get(key)
        if index_data:
            ids = index_data if isinstance(index_data, list) else index_data.get("ids", [])
        else:
            ids = []
        
        # 맨 앞에 추가
        ids.insert(0, cascade_id)
        
        # 최대 크기 유지
        if len(ids) > self.MAX_INDEX_SIZE:
            ids = ids[:self.MAX_INDEX_SIZE]
        
        backend.set(key, {"ids": ids})


# =============================================================================
# Singleton
# =============================================================================


_cascade_auditor: Optional[CascadeEventAuditor] = None
_auditor_lock = threading.Lock()


def get_cascade_event_auditor() -> CascadeEventAuditor:
    """CascadeEventAuditor 싱글톤 반환."""
    global _cascade_auditor
    
    if _cascade_auditor is not None:
        return _cascade_auditor
    
    with _auditor_lock:
        if _cascade_auditor is None:
            _cascade_auditor = CascadeEventAuditor()
        return _cascade_auditor


def reset_cascade_auditor() -> None:
    """CascadeEventAuditor 싱글톤 리셋. 테스트 용도."""
    global _cascade_auditor
    with _auditor_lock:
        _cascade_auditor = None

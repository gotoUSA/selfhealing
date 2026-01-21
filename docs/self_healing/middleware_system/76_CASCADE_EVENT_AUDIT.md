# 76. Cascade Event Audit (연계 이벤트 감사 추적)

> **Version**: 1.0.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 Audit 시스템은 **개별 이벤트**만 기록합니다:

```
Audit Log (현재):
├── [15:30:00] Emergency LEVEL_3 activated
├── [15:30:01] Governance mode → STRICT
├── [15:30:02] Canary rollout-123 rolled back
└── [15:30:03] Error budget multiplier set to 5x
```

**문제**:
- 이벤트 간 **인과관계**가 기록되지 않음
- "왜 Canary가 롤백되었는가?"에 대한 추적 어려움
- 연계 액션의 **전체 흐름**을 파악하기 어려움

### 1.2 해결책 (TO-BE)

**CascadeEvent** 도입으로 인과관계 묶음 기록:

```
Cascade Event (새로운):
├── Cascade ID: cascade-evt-abc123
├── Trigger: LEVEL_3 Detected (evt-001)
├── Causation Chain: [evt-001] → [evt-002] → [evt-003] → [evt-004]
└── Effects:
    ├── [evt-002] GOVERNANCE_STRICT (caused by evt-001)
    ├── [evt-003] CANARY_ROLLBACK (caused by evt-002)
    └── [evt-004] BUDGET_MULTIPLIER (caused by evt-001)
```

---

## 2. 아키텍처

### 2.1 Cascade Event 구조

```
┌─────────────────────────────────────────────────────────────────────┐
│                       CascadeEvent Structure                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                      CascadeEvent                             │  │
│  │                                                               │  │
│  │  id: "cascade-evt-abc123"                                    │  │
│  │  timestamp: "2026-01-21T15:30:00Z"                           │  │
│  │  namespace: "seoul"                                          │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Trigger                                                 │  │  │
│  │  │                                                         │  │  │
│  │  │ type: "EMERGENCY_LEVEL_CHANGED"                        │  │  │
│  │  │ event_id: "evt-001"                                    │  │  │
│  │  │ details: {                                             │  │  │
│  │  │   "old_level": "NORMAL",                               │  │  │
│  │  │   "new_level": "LEVEL_3",                              │  │  │
│  │  │   "activated_by": "system"                             │  │  │
│  │  │ }                                                       │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Effects (연쇄 결과)                                     │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 1: GOVERNANCE_STRICT                      │   │  │  │
│  │  │ │ event_id: "evt-002"                              │   │  │  │
│  │  │ │ caused_by: "evt-001"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 2: CANARY_ROLLBACK                        │   │  │  │
│  │  │ │ event_id: "evt-003"                              │   │  │  │
│  │  │ │ caused_by: "evt-002"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ │ details: {"rollouts": ["rollout-123"]}           │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  │ ┌──────────────────────────────────────────────────┐   │  │  │
│  │  │ │ Effect 3: BUDGET_MULTIPLIER                      │   │  │  │
│  │  │ │ event_id: "evt-004"                              │   │  │  │
│  │  │ │ caused_by: "evt-001"                             │   │  │  │
│  │  │ │ success: true                                    │   │  │  │
│  │  │ │ details: {"multiplier": 5.0}                     │   │  │  │
│  │  │ └──────────────────────────────────────────────────┘   │  │  │
│  │  │                                                         │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Causation Chain                                         │  │  │
│  │  │                                                         │  │  │
│  │  │ evt-001 ──▶ evt-002 ──▶ evt-003                        │  │  │
│  │  │     │                                                   │  │  │
│  │  │     └────▶ evt-004                                     │  │  │
│  │  │                                                         │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  │  ┌────────────────────────────────────────────────────────┐  │  │
│  │  │ Hash Chain (위변조 방지)                                │  │  │
│  │  │                                                         │  │  │
│  │  │ previous_hash: "abc123..."                             │  │  │
│  │  │ current_hash: "def456..."                              │  │  │
│  │  │ signature: "sig789..."                                 │  │  │
│  │  └────────────────────────────────────────────────────────┘  │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Hash Chain 연결

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Cascade Event Hash Chain                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Cascade #1  │     │ Cascade #2  │     │ Cascade #3  │          │
│  │             │     │             │     │             │          │
│  │ prev: null  │────▶│ prev: h1    │────▶│ prev: h2    │          │
│  │ hash: h1    │     │ hash: h2    │     │ hash: h3    │          │
│  │             │     │             │     │             │          │
│  │ LEVEL_3     │     │ RECOVERY    │     │ LEVEL_2     │          │
│  │ detected    │     │ started     │     │ detected    │          │
│  └─────────────┘     └─────────────┘     └─────────────┘          │
│                                                                      │
│  위변조 시도 시:                                                     │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐          │
│  │ Cascade #1  │  ✗  │ Cascade #2  │     │ Cascade #3  │          │
│  │             │  │  │ (modified)  │     │             │          │
│  │ prev: null  │  │  │ prev: h1    │──✗──│ prev: h2    │          │
│  │ hash: h1    │  │  │ hash: h2'   │     │ hash: h3    │          │
│  │             │  │  │    ↑       │     │     ↑      │          │
│  └─────────────┘  │  │ 변조됨!    │     │ 불일치!    │          │
│                   │  └─────────────┘     └─────────────┘          │
│                   │                                                 │
│                   └─── h2' ≠ h2 (체인 무결성 위반)                   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 CascadeEvent 모델

```python
# packages/selfhealing-python/src/selfhealing/audit/cascade_event.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import hashlib
import json
import uuid


@dataclass
class CascadeEffect:
    """연쇄 효과 (Cascade Event 내 개별 액션)."""
    
    event_id: str
    """이벤트 고유 ID."""
    
    action_type: str
    """액션 유형 (GOVERNANCE_STRICT, CANARY_ROLLBACK, etc.)."""
    
    caused_by: str
    """원인 이벤트 ID."""
    
    success: bool
    """성공 여부."""
    
    target: Optional[str] = None
    """대상 (롤아웃 ID, 서비스 이름 등)."""
    
    details: Dict[str, Any] = field(default_factory=dict)
    """상세 정보."""
    
    error_message: Optional[str] = None
    """실패 시 에러 메시지."""
    
    executed_at: Optional[str] = None
    """실행 시각."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "event_id": self.event_id,
            "action_type": self.action_type,
            "caused_by": self.caused_by,
            "success": self.success,
            "target": self.target,
            "details": self.details,
            "error_message": self.error_message,
            "executed_at": self.executed_at,
        }


@dataclass
class CascadeTrigger:
    """연쇄 트리거 (Cascade Event의 시작점)."""
    
    trigger_type: str
    """트리거 유형 (EMERGENCY_LEVEL_CHANGED, MANUAL_ACTIVATION, etc.)."""
    
    event_id: str
    """트리거 이벤트 ID."""
    
    details: Dict[str, Any] = field(default_factory=dict)
    """트리거 상세 정보."""
    
    triggered_by: Optional[str] = None
    """트리거한 주체 (user, system)."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "trigger_type": self.trigger_type,
            "event_id": self.event_id,
            "details": self.details,
            "triggered_by": self.triggered_by,
        }


@dataclass
class CascadeEvent:
    """
    연쇄 이벤트.
    
    하나의 트리거로 인해 발생한 모든 연계 액션을 묶어서 기록합니다.
    
    Features:
    - 인과관계 추적 (causation chain)
    - 위변조 방지 (hash chain)
    - 전체 흐름 시각화
    
    Reference:
    - docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    """
    
    id: str
    """Cascade Event 고유 ID."""
    
    trigger: CascadeTrigger
    """트리거 정보."""
    
    effects: List[CascadeEffect]
    """연쇄 효과 목록."""
    
    namespace: str
    """네임스페이스."""
    
    timestamp: str
    """생성 시각 (ISO format)."""
    
    # Hash Chain
    previous_hash: Optional[str] = None
    """이전 CascadeEvent의 해시."""
    
    current_hash: Optional[str] = None
    """현재 CascadeEvent의 해시."""
    
    # 메타데이터
    version: str = "1.0"
    """스키마 버전."""
    
    total_effects: int = 0
    """총 효과 수."""
    
    success_count: int = 0
    """성공한 효과 수."""
    
    failure_count: int = 0
    """실패한 효과 수."""
    
    def __post_init__(self):
        """초기화 후처리."""
        self.total_effects = len(self.effects)
        self.success_count = sum(1 for e in self.effects if e.success)
        self.failure_count = self.total_effects - self.success_count
    
    def get_causation_chain(self) -> List[str]:
        """인과관계 체인 반환."""
        chain = [self.trigger.event_id]
        for effect in self.effects:
            if effect.event_id not in chain:
                chain.append(effect.event_id)
        return chain
    
    def calculate_hash(self) -> str:
        """현재 이벤트의 해시 계산."""
        content = {
            "id": self.id,
            "trigger": self.trigger.to_dict(),
            "effects": [e.to_dict() for e in self.effects],
            "namespace": self.namespace,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
        }
        content_str = json.dumps(content, sort_keys=True)
        return hashlib.sha256(content_str.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "id": self.id,
            "trigger": self.trigger.to_dict(),
            "effects": [e.to_dict() for e in self.effects],
            "causation_chain": self.get_causation_chain(),
            "namespace": self.namespace,
            "timestamp": self.timestamp,
            "previous_hash": self.previous_hash,
            "current_hash": self.current_hash,
            "version": self.version,
            "total_effects": self.total_effects,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CascadeEvent":
        """딕셔너리에서 생성."""
        trigger = CascadeTrigger(
            trigger_type=data["trigger"]["trigger_type"],
            event_id=data["trigger"]["event_id"],
            details=data["trigger"].get("details", {}),
            triggered_by=data["trigger"].get("triggered_by"),
        )
        
        effects = [
            CascadeEffect(
                event_id=e["event_id"],
                action_type=e["action_type"],
                caused_by=e["caused_by"],
                success=e["success"],
                target=e.get("target"),
                details=e.get("details", {}),
                error_message=e.get("error_message"),
                executed_at=e.get("executed_at"),
            )
            for e in data.get("effects", [])
        ]
        
        return cls(
            id=data["id"],
            trigger=trigger,
            effects=effects,
            namespace=data["namespace"],
            timestamp=data["timestamp"],
            previous_hash=data.get("previous_hash"),
            current_hash=data.get("current_hash"),
            version=data.get("version", "1.0"),
        )
```

### 3.2 CascadeEventAuditor

```python
class CascadeEventAuditor:
    """
    Cascade Event 감사기.
    
    연계 이벤트를 생성, 저장, 조회하고 해시 체인 무결성을 검증합니다.
    
    Features:
    - Cascade Event 생성 및 저장
    - Hash Chain 연결
    - 무결성 검증
    - 인과관계 조회
    
    Reference:
    - docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    """
    
    # Redis 키 패턴
    CASCADE_KEY = "selfhealing:{namespace}:audit:cascade:{cascade_id}"
    CASCADE_INDEX_KEY = "selfhealing:{namespace}:audit:cascade_index"
    LAST_HASH_KEY = "selfhealing:{namespace}:audit:cascade_last_hash"
    
    def __init__(self):
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
    ) -> CascadeEvent:
        """
        Cascade Event 기록.
        
        Args:
            trigger_type: 트리거 유형
            trigger_details: 트리거 상세 정보
            effects: 연쇄 효과 목록
            namespace: 네임스페이스
            triggered_by: 트리거 주체
        
        Returns:
            생성된 CascadeEvent
        """
        with self._lock:
            # 1. ID 생성
            cascade_id = f"cascade-{uuid.uuid4().hex[:12]}"
            trigger_event_id = f"evt-{uuid.uuid4().hex[:8]}"
            now = datetime.now(timezone.utc).isoformat()
            
            # 2. 트리거 생성
            trigger = CascadeTrigger(
                trigger_type=trigger_type,
                event_id=trigger_event_id,
                details=trigger_details,
                triggered_by=triggered_by,
            )
            
            # 3. 효과 생성
            cascade_effects = []
            previous_event_id = trigger_event_id
            
            for effect_data in effects:
                effect_event_id = f"evt-{uuid.uuid4().hex[:8]}"
                
                effect = CascadeEffect(
                    event_id=effect_event_id,
                    action_type=effect_data.get("action_type", "UNKNOWN"),
                    caused_by=effect_data.get("caused_by", previous_event_id),
                    success=effect_data.get("success", True),
                    target=effect_data.get("target"),
                    details=effect_data.get("details", {}),
                    error_message=effect_data.get("error_message"),
                    executed_at=now,
                )
                cascade_effects.append(effect)
                previous_event_id = effect_event_id
            
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
        cascade_ids = backend.lrange(index_key, 0, limit - 1)
        
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
            검증 결과
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
            if i < len(events) - 1:
                next_event = events[i + 1]
                if event.previous_hash != next_event.current_hash:
                    errors.append({
                        "cascade_id": event.id,
                        "error": "chain_broken",
                        "expected_previous": next_event.current_hash,
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
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _get_last_hash(self, namespace: str) -> Optional[str]:
        """마지막 해시 조회."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        return backend.get(key)
    
    def _update_last_hash(self, namespace: str, hash_value: str) -> None:
        """마지막 해시 업데이트."""
        backend = self._get_backend()
        key = self.LAST_HASH_KEY.format(namespace=namespace)
        backend.set(key, hash_value)
    
    def _save_cascade_event(self, event: CascadeEvent) -> None:
        """Cascade Event 저장."""
        backend = self._get_backend()
        key = self.CASCADE_KEY.format(
            namespace=event.namespace,
            cascade_id=event.id,
        )
        backend.set(key, event.to_dict())
    
    def _add_to_index(self, namespace: str, cascade_id: str) -> None:
        """인덱스에 추가."""
        backend = self._get_backend()
        key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        backend.lpush(key, cascade_id)
        # 최대 10000개 유지
        backend.ltrim(key, 0, 9999)


# =============================================================================
# Singleton
# =============================================================================

_cascade_auditor: Optional[CascadeEventAuditor] = None


def get_cascade_event_auditor() -> CascadeEventAuditor:
    """CascadeEventAuditor 싱글톤 반환."""
    global _cascade_auditor
    if _cascade_auditor is None:
        _cascade_auditor = CascadeEventAuditor()
    return _cascade_auditor
```

---

## 4. EmergencyCoordinator 연동

### 4.1 Cascade 기록 통합

```python
# packages/selfhealing-python/src/selfhealing/services/coordination/coordinator.py

class EmergencyCoordinator:
    """Emergency Coordination Layer 중앙 조율자."""
    
    def __init__(
        self,
        policy_engine: CoordinationPolicyEngine,
        cascade_auditor: CascadeEventAuditor,
    ):
        self.policy_engine = policy_engine
        self.cascade_auditor = cascade_auditor
    
    def on_emergency_level_changed(
        self,
        old_level: EmergencyLevel,
        new_level: EmergencyLevel,
        namespace: str,
        triggered_by: str = "system",
    ) -> CoordinationResult:
        """
        Emergency Level 변경 시 연계 액션 실행 및 Cascade 기록.
        """
        # 1. 정책 조회
        actions = self.policy_engine.get_actions_for_level_change(
            old_level=old_level,
            new_level=new_level,
            namespace=namespace,
        )
        
        # 2. 액션 실행
        effect_results = []
        for action in actions:
            result = self._execute_action(action, namespace)
            effect_results.append({
                "action_type": action.type.value,
                "success": result.success,
                "target": result.target,
                "details": result.details,
                "error_message": result.error_message,
            })
        
        # 3. Cascade Event 기록
        cascade_event = self.cascade_auditor.record(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            trigger_details={
                "old_level": old_level.name,
                "new_level": new_level.name,
            },
            effects=effect_results,
            namespace=namespace,
            triggered_by=triggered_by,
        )
        
        return CoordinationResult(
            success=all(e["success"] for e in effect_results),
            cascade_event_id=cascade_event.id,
            executed_actions=effect_results,
        )
```

---

## 5. API 엔드포인트

### 5.1 Cascade Event 조회 API

```python
# packages/selfhealing-python/src/selfhealing/api/django/views/cascade.py

class CascadeEventListView(APIView):
    """Cascade Event 목록 조회 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request) -> Response:
        """최근 Cascade Event 목록 조회."""
        namespace = request.query_params.get("namespace", "global")
        limit = int(request.query_params.get("limit", 50))
        
        auditor = get_cascade_event_auditor()
        events = auditor.get_recent_events(namespace, limit)
        
        return Response({
            "events": [e.to_dict() for e in events],
            "count": len(events),
            "namespace": namespace,
        })


class CascadeEventDetailView(APIView):
    """Cascade Event 상세 조회 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request, cascade_id: str) -> Response:
        """Cascade Event 상세 조회."""
        namespace = request.query_params.get("namespace", "global")
        
        auditor = get_cascade_event_auditor()
        event = auditor.get_cascade_event(cascade_id, namespace)
        
        if not event:
            return Response(
                {"error": "Cascade event not found"},
                status=404,
            )
        
        return Response(event.to_dict())


class CascadeChainVerifyView(APIView):
    """Hash Chain 무결성 검증 API."""
    
    permission_classes = [IsSelfHealingAdmin]
    
    def post(self, request: Request) -> Response:
        """Hash Chain 무결성 검증."""
        namespace = request.data.get("namespace", "global")
        limit = request.data.get("limit", 1000)
        
        auditor = get_cascade_event_auditor()
        result = auditor.verify_chain_integrity(namespace, limit)
        
        return Response(result)


class CausationTraceView(APIView):
    """인과관계 추적 API."""
    
    permission_classes = [IsViewer]
    
    def get(self, request: Request, event_id: str) -> Response:
        """효과 이벤트의 인과관계 추적."""
        namespace = request.query_params.get("namespace", "global")
        
        auditor = get_cascade_event_auditor()
        trace = auditor.get_causation_trace(event_id, namespace)
        
        if not trace:
            return Response(
                {"error": "Event not found or no causation trace"},
                status=404,
            )
        
        return Response({
            "event_id": event_id,
            "trace": trace,
            "depth": len(trace),
        })
```

---

## 6. 테스트

### 6.1 단위 테스트

```python
class TestCascadeEventAuditor:
    """CascadeEventAuditor 단위 테스트."""
    
    def test_record_cascade_event(self):
        """Cascade Event 기록."""
        auditor = CascadeEventAuditor()
        
        event = auditor.record(
            trigger_type="EMERGENCY_LEVEL_CHANGED",
            trigger_details={"old_level": "NORMAL", "new_level": "LEVEL_3"},
            effects=[
                {"action_type": "GOVERNANCE_STRICT", "success": True},
                {"action_type": "CANARY_ROLLBACK", "success": True},
            ],
            namespace="test",
            triggered_by="test_user",
        )
        
        assert event.id.startswith("cascade-")
        assert event.trigger.trigger_type == "EMERGENCY_LEVEL_CHANGED"
        assert len(event.effects) == 2
        assert event.total_effects == 2
        assert event.success_count == 2
    
    def test_hash_chain_integrity(self):
        """Hash Chain 무결성."""
        auditor = CascadeEventAuditor()
        
        # 2개의 Cascade Event 생성
        event1 = auditor.record(
            trigger_type="EVENT_1",
            trigger_details={},
            effects=[],
            namespace="test",
        )
        
        event2 = auditor.record(
            trigger_type="EVENT_2",
            trigger_details={},
            effects=[],
            namespace="test",
        )
        
        # event2의 previous_hash는 event1의 current_hash
        assert event2.previous_hash == event1.current_hash
        
        # 무결성 검증
        result = auditor.verify_chain_integrity("test", limit=10)
        assert result["valid"] is True
    
    def test_causation_trace(self):
        """인과관계 추적."""
        auditor = CascadeEventAuditor()
        
        event = auditor.record(
            trigger_type="LEVEL_3_DETECTED",
            trigger_details={},
            effects=[
                {"action_type": "GOVERNANCE_STRICT", "success": True},
                {"action_type": "CANARY_ROLLBACK", "success": True},
            ],
            namespace="test",
        )
        
        # 마지막 효과의 인과관계 추적
        last_effect_id = event.effects[-1].event_id
        trace = auditor.get_causation_trace(last_effect_id, "test")
        
        assert len(trace) >= 2
        assert trace[0]["action_type"] == "LEVEL_3_DETECTED"
        assert trace[-1]["event_id"] == last_effect_id
```

---

## 7. 모니터링

### 7.1 메트릭

```python
CASCADE_EVENTS_TOTAL = Counter(
    "selfhealing_cascade_events_total",
    "Total cascade events recorded",
    ["trigger_type", "namespace"],
)

CASCADE_EFFECTS_TOTAL = Counter(
    "selfhealing_cascade_effects_total",
    "Total cascade effects executed",
    ["action_type", "success", "namespace"],
)

CASCADE_CHAIN_INTEGRITY = Gauge(
    "selfhealing_cascade_chain_integrity",
    "Hash chain integrity status (1=valid, 0=invalid)",
    ["namespace"],
)
```

### 7.2 알림 템플릿

```
🔗 Cascade Event Recorded

Cascade ID: cascade-evt-abc123
Trigger: EMERGENCY_LEVEL_CHANGED (NORMAL → LEVEL_3)
Namespace: seoul
Time: 2026-01-21T15:30:00Z

Effects Executed:
✅ GOVERNANCE_STRICT → Mode changed to STRICT
✅ CANARY_ROLLBACK → 2 rollouts rolled back
✅ BUDGET_MULTIPLIER → Set to 5.0x

Causation Chain:
[evt-001] LEVEL_3_DETECTED
    └─▶ [evt-002] GOVERNANCE_STRICT
        └─▶ [evt-003] CANARY_ROLLBACK
    └─▶ [evt-004] BUDGET_MULTIPLIER

Hash Chain: ✅ Verified
Previous: abc123...
Current: def456...

View Details: https://dashboard/cascade/cascade-evt-abc123
```

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |

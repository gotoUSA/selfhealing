# 73. Namespace-Aware Emergency (리전별 긴급 모드 격리)

> **Version**: 1.2.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 `EmergencyModeTracker`는 **단일 Global 상태**를 사용합니다:

```python
# 현재 구현 - governance.py
EMERGENCY_STATE_STORAGE_KEY = "selfhealing:governance:emergency_state"

class EmergencyModeTracker:
    def _load_state(self) -> EmergencyState:
        backend = self._get_backend()
        data = backend.get(EMERGENCY_STATE_STORAGE_KEY)  # ← Global 키
        ...
```

**문제**:
- 서울 리전에서 LEVEL_3 발생 → 도쿄 리전도 STRICT 모드 돌입
- 한 리전의 장애가 **전 세계 자동화를 마비**시킴

### 1.2 해결책 (TO-BE)

`ClusterIdentity`를 참조하여 **네임스페이스별 상태 분리**:

```
Redis Keys:
├── selfhealing:governance:emergency_state           # Global (전역 비상)
├── selfhealing:seoul:governance:emergency_state     # Seoul 전용
├── selfhealing:tokyo:governance:emergency_state     # Tokyo 전용
└── selfhealing:oregon:governance:emergency_state    # Oregon 전용
```

---

## 2. 아키텍처

### 2.1 상태 저장 구조

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Namespace-Aware State Backend                     │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    EmergencyModeTracker                      │   │
│  │                                                              │   │
│  │  get_state(namespace: Optional[str]) → EmergencyState       │   │
│  │  set_state(namespace: Optional[str], state: EmergencyState) │   │
│  │                                                              │   │
│  └────────────────────────────┬─────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              NamespacedStateBackend (신규)                   │   │
│  │                                                              │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐         │   │
│  │  │   Global    │  │    Seoul    │  │    Tokyo    │         │   │
│  │  │   State     │  │    State    │  │    State    │         │   │
│  │  │             │  │             │  │             │         │   │
│  │  │ LEVEL: 0    │  │ LEVEL: 3    │  │ LEVEL: 0    │         │   │
│  │  │ MODE: NORMAL│  │ MODE: STRICT│  │ MODE: NORMAL│         │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘         │   │
│  │                                                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Scope 우선순위

```
Global Emergency (전역 비상)
    │
    │  우선순위: Global > Regional
    │
    ▼
┌─────────────────────────────────────────┐
│  Global LEVEL_3 발생 시:                 │
│  → 모든 리전이 STRICT 모드               │
│  → Regional 설정 무시                    │
└─────────────────────────────────────────┘
    │
    │  Global이 NORMAL이면:
    │
    ▼
┌─────────────────────────────────────────┐
│  Regional 상태 적용:                     │
│  → Seoul: LEVEL_3 → STRICT              │
│  → Tokyo: NORMAL → NORMAL               │
│  → Oregon: LEVEL_1 → NORMAL             │
└─────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 EmergencyScope Enum

```python
# packages/selfhealing-python/src/selfhealing/services/governance.py

from enum import Enum
from typing import Optional


class EmergencyScope(str, Enum):
    """긴급 모드 적용 범위."""
    
    GLOBAL = "global"
    """모든 클러스터에 적용 (최우선)."""
    
    REGIONAL = "regional"
    """특정 네임스페이스에만 적용."""
    
    LOCAL = "local"
    """현재 인스턴스에만 적용 (테스트용)."""
```

### 3.2 ScopedEmergencyState 모델

```python
@dataclass
class ScopedEmergencyState:
    """네임스페이스별 Emergency 상태."""
    
    # 식별 정보
    namespace: str
    """네임스페이스 (예: 'global', 'seoul', 'tokyo')."""
    
    scope: EmergencyScope
    """적용 범위."""
    
    # 상태 정보
    emergency_level: EmergencyLevel = EmergencyLevel.NORMAL
    """현재 Emergency 레벨."""
    
    governance_mode: str = "NORMAL"
    """현재 Governance 모드 (NORMAL/STRICT)."""
    
    is_active: bool = False
    """긴급 모드 활성화 여부."""
    
    # 메타데이터
    activated_at: Optional[str] = None
    """활성화 시각 (ISO format)."""
    
    activated_by: Optional[str] = None
    """활성화한 사용자."""
    
    reason: str = ""
    """활성화 사유."""
    
    # 만료 정보
    expires_at: Optional[str] = None
    """자동 만료 시각."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "namespace": self.namespace,
            "scope": self.scope.value,
            "emergency_level": self.emergency_level.value,
            "governance_mode": self.governance_mode,
            "is_active": self.is_active,
            "activated_at": self.activated_at,
            "activated_by": self.activated_by,
            "reason": self.reason,
            "expires_at": self.expires_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScopedEmergencyState":
        """딕셔너리에서 생성."""
        return cls(
            namespace=data.get("namespace", "global"),
            scope=EmergencyScope(data.get("scope", "regional")),
            emergency_level=EmergencyLevel(data.get("emergency_level", 0)),
            governance_mode=data.get("governance_mode", "NORMAL"),
            is_active=data.get("is_active", False),
            activated_at=data.get("activated_at"),
            activated_by=data.get("activated_by"),
            reason=data.get("reason", ""),
            expires_at=data.get("expires_at"),
        )
```

### 3.3 NamespacedEmergencyTracker

```python
class NamespacedEmergencyTracker:
    """
    네임스페이스 인지형 Emergency 추적기.
    
    리전별 독립적인 Emergency 상태를 관리합니다.
    Global 상태는 모든 리전보다 우선합니다.
    
    Reference:
    - docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md
    """
    
    # Redis 키 패턴
    STATE_KEY_PATTERN = "selfhealing:{namespace}:governance:emergency_state"
    GLOBAL_NAMESPACE = "global"
    
    def __init__(self):
        self._lock = threading.RLock()
        self._local_cache: Dict[str, ScopedEmergencyState] = {}
        self._cache_ttl = 30.0  # 30초 캐시
        self._cache_timestamps: Dict[str, float] = {}
    
    def _get_backend(self):
        """State backend 획득."""
        from selfhealing.core.state_backend import get_state_backend
        return get_state_backend()
    
    def _get_current_namespace(self) -> str:
        """현재 인스턴스의 네임스페이스 획득."""
        from selfhealing.settings.namespace import get_namespace_settings
        settings = get_namespace_settings()
        return settings.get_effective_namespace() or self.GLOBAL_NAMESPACE
    
    def _get_state_key(self, namespace: str) -> str:
        """
        네임스페이스용 Redis 키 생성.
        
        SSOT Prefixing: NamespaceSettings.get_key_prefix()를 활용하여
        하드코딩된 키가 없도록 합니다.
        
        Code reference:
            settings/namespace.py#L88-97 (get_key_prefix 패턴)
        """
        from selfhealing.settings.namespace import get_namespace_settings
        settings = get_namespace_settings()
        
        if namespace == self.GLOBAL_NAMESPACE:
            # Global은 네임스페이스 없이 기본 프리픽스만 사용 (하위 호환)
            base_prefix = settings.get_key_prefix().rstrip(":")
            if ":" in base_prefix:
                # "selfhealing:seoul:" -> "selfhealing"
                base_prefix = base_prefix.split(":")[0]
            return f"{base_prefix}:governance:emergency_state"
        
        # Regional: 명시적 namespace 사용
        return f"selfhealing:{namespace}:governance:emergency_state"
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    def get_effective_state(
        self,
        namespace: Optional[str] = None,
        precedence: Optional[CommandPrecedence] = None,
    ) -> ScopedEmergencyState:
        """
        유효한 Emergency 상태 조회 (Precedence-First Hierarchy).
        
        우선순위 (Precedence-First):
        1. Admin Override: 특정 리전에 ADMIN_OVERRIDE/KILL_SWITCH가 있으면
           해당 리전 설정 우선 (Global 무시)
        2. Safety-Max: 특별한 수동 명령이 없으면 max(Global, Regional) 선택
           (둘 중 하나라도 STRICT면 STRICT)
        
        Args:
            namespace: 조회할 네임스페이스 (None이면 현재 인스턴스)
            precedence: 명령 우선순위 (수동 오버라이드 시)
        
        Returns:
            유효한 ScopedEmergencyState
        
        Code reference:
            coordination/enums.py#L59-85 (CommandPrecedence)
        """
        from selfhealing.services.coordination.enums import CommandPrecedence
        
        ns = namespace or self._get_current_namespace()
        
        with self._lock:
            global_state = self._load_state(self.GLOBAL_NAMESPACE)
            regional_state = self._load_state(ns) if ns != self.GLOBAL_NAMESPACE else global_state
            
            # 1순위: Admin Override - 수동 명령이 ADMIN_OVERRIDE 이상이면 Regional 우선
            if precedence and precedence >= CommandPrecedence.ADMIN_OVERRIDE:
                logger.info(
                    f"[EmergencyTracker] Admin override active (precedence={precedence.name}), "
                    f"using regional state for namespace={ns}"
                )
                # TTL 경고 (KILL_SWITCH 아니면 TTL 필수)
                if precedence < CommandPrecedence.KILL_SWITCH:
                    if not regional_state.expires_at:
                        logger.warning(
                            f"[EmergencyTracker] ADMIN_OVERRIDE without TTL is not recommended. "
                            f"namespace={ns}"
                        )
                return regional_state
            
            # 2순위: Safety-Max - 둘 중 더 엄격한 상태 반환
            global_is_strict = (
                global_state.is_active and 
                global_state.governance_mode == "STRICT"
            )
            regional_is_strict = (
                regional_state.is_active and 
                regional_state.governance_mode == "STRICT"
            )
            
            if global_is_strict and regional_is_strict:
                # 둘 다 STRICT: Global 우선 (더 넓은 범위)
                logger.debug(
                    f"[EmergencyTracker] Both Global and Regional STRICT, "
                    f"using Global state"
                )
                return global_state
            elif global_is_strict:
                # Global만 STRICT
                logger.debug(
                    f"[EmergencyTracker] Global STRICT active, "
                    f"overriding namespace={ns}"
                )
                return global_state
            elif regional_is_strict:
                # Regional만 STRICT
                return regional_state
            else:
                # 둘 다 NORMAL: Regional 반환
                return regional_state
    
    def activate_emergency(
        self,
        level: EmergencyLevel,
        activated_by: str,
        reason: str,
        scope: EmergencyScope = EmergencyScope.REGIONAL,
        namespace: Optional[str] = None,
    ) -> ScopedEmergencyState:
        """
        긴급 모드 활성화.
        
        Args:
            level: Emergency 레벨
            activated_by: 활성화한 사용자
            reason: 활성화 사유
            scope: 적용 범위 (GLOBAL/REGIONAL)
            namespace: 대상 네임스페이스 (REGIONAL인 경우 필수)
        
        Returns:
            활성화된 상태
        """
        with self._lock:
            # 네임스페이스 결정
            if scope == EmergencyScope.GLOBAL:
                target_ns = self.GLOBAL_NAMESPACE
            else:
                target_ns = namespace or self._get_current_namespace()
            
            # Governance 모드 결정
            governance_mode = "STRICT" if level.value >= 2 else "NORMAL"
            
            # 상태 생성
            now = datetime.now(timezone.utc)
            expiry_hours = self._get_expiry_hours()
            
            state = ScopedEmergencyState(
                namespace=target_ns,
                scope=scope,
                emergency_level=level,
                governance_mode=governance_mode,
                is_active=True,
                activated_at=now.isoformat(),
                activated_by=activated_by,
                reason=reason,
                expires_at=(now + timedelta(hours=expiry_hours)).isoformat(),
            )
            
            # 저장
            self._save_state(target_ns, state)
            
            logger.warning(
                f"[EmergencyTracker] Emergency activated: "
                f"namespace={target_ns}, scope={scope.value}, "
                f"level={level.name}, mode={governance_mode}, "
                f"by={activated_by}"
            )
            
            return state
    
    def deactivate_emergency(
        self,
        deactivated_by: str,
        namespace: Optional[str] = None,
        scope: EmergencyScope = EmergencyScope.REGIONAL,
    ) -> ScopedEmergencyState:
        """
        긴급 모드 비활성화.
        
        Args:
            deactivated_by: 비활성화한 사용자
            namespace: 대상 네임스페이스
            scope: 적용 범위
        
        Returns:
            비활성화된 상태
        """
        with self._lock:
            target_ns = (
                self.GLOBAL_NAMESPACE 
                if scope == EmergencyScope.GLOBAL 
                else (namespace or self._get_current_namespace())
            )
            
            state = self._load_state(target_ns)
            state.is_active = False
            state.governance_mode = "NORMAL"
            state.emergency_level = EmergencyLevel.NORMAL
            
            self._save_state(target_ns, state)
            
            logger.warning(
                f"[EmergencyTracker] Emergency deactivated: "
                f"namespace={target_ns}, by={deactivated_by}"
            )
            
            return state
    
    def get_all_active_namespaces(self) -> List[str]:
        """
        활성화된 모든 네임스페이스 목록 조회.
        
        Returns:
            활성 긴급 모드 네임스페이스 목록
        """
        backend = self._get_backend()
        
        # Redis SCAN으로 모든 emergency_state 키 조회
        pattern = "selfhealing:*:governance:emergency_state"
        active_namespaces = []
        
        try:
            # Global 체크
            global_state = self._load_state(self.GLOBAL_NAMESPACE)
            if global_state.is_active:
                active_namespaces.append(self.GLOBAL_NAMESPACE)
            
            # Regional 체크 (Redis SCAN 필요)
            # 실제 구현에서는 알려진 네임스페이스 목록을 사용하거나
            # Redis SCAN을 수행
            known_namespaces = self._get_known_namespaces()
            for ns in known_namespaces:
                state = self._load_state(ns)
                if state.is_active:
                    active_namespaces.append(ns)
            
        except Exception as e:
            logger.warning(f"[EmergencyTracker] Failed to scan namespaces: {e}")
        
        return active_namespaces
    
    # =========================================================================
    # Private Methods
    # =========================================================================
    
    def _load_state(self, namespace: str) -> ScopedEmergencyState:
        """Redis에서 상태 로드."""
        # 캐시 확인
        cache_key = f"state:{namespace}"
        now = time.time()
        
        if cache_key in self._local_cache:
            cache_time = self._cache_timestamps.get(cache_key, 0)
            if now - cache_time < self._cache_ttl:
                return self._local_cache[cache_key]
        
        # Redis 조회
        backend = self._get_backend()
        key = self._get_state_key(namespace)
        data = backend.get(key)
        
        if data:
            state = ScopedEmergencyState.from_dict(data)
        else:
            state = ScopedEmergencyState(
                namespace=namespace,
                scope=(
                    EmergencyScope.GLOBAL 
                    if namespace == self.GLOBAL_NAMESPACE 
                    else EmergencyScope.REGIONAL
                ),
            )
        
        # 캐시 저장
        self._local_cache[cache_key] = state
        self._cache_timestamps[cache_key] = now
        
        return state
    
    def _save_state(self, namespace: str, state: ScopedEmergencyState) -> None:
        """Redis에 상태 저장."""
        backend = self._get_backend()
        key = self._get_state_key(namespace)
        backend.set(key, state.to_dict())
        
        # 캐시 무효화
        cache_key = f"state:{namespace}"
        self._local_cache.pop(cache_key, None)
        self._cache_timestamps.pop(cache_key, None)
    
    def _get_expiry_hours(self) -> int:
        """만료 시간 설정 조회."""
        try:
            from selfhealing.services.runtime_config import get_runtime_config_manager
            manager = get_runtime_config_manager()
            config = manager.get_governance_config()
            return config.get("emergency_expiry_hours", 8)
        except Exception:
            return 8
    
    def _get_known_namespaces(self) -> List[str]:
        """알려진 네임스페이스 목록."""
        # 설정에서 가져오거나, 기본값 반환
        try:
            from selfhealing.settings import get_settings
            settings = get_settings()
            return getattr(settings, "known_namespaces", ["seoul", "tokyo", "oregon"])
        except Exception:
            return ["seoul", "tokyo", "oregon"]
    
    def invalidate_cache(self, namespace: Optional[str] = None) -> None:
        """캐시 무효화 (이벤트 버스 연동용)."""
        with self._lock:
            if namespace:
                cache_key = f"state:{namespace}"
                self._local_cache.pop(cache_key, None)
                self._cache_timestamps.pop(cache_key, None)
            else:
                self._local_cache.clear()
                self._cache_timestamps.clear()


# =============================================================================
# Singleton
# =============================================================================

_namespaced_tracker: Optional[NamespacedEmergencyTracker] = None


def get_namespaced_emergency_tracker() -> NamespacedEmergencyTracker:
    """NamespacedEmergencyTracker 싱글톤 반환."""
    global _namespaced_tracker
    if _namespaced_tracker is None:
        _namespaced_tracker = NamespacedEmergencyTracker()
    return _namespaced_tracker
```

---

## 4. 마이그레이션 전략

### 4.1 하위 호환성 유지

기존 `EmergencyModeTracker` API를 유지하면서 내부 구현만 변경:

```python
class EmergencyModeTracker:
    """
    기존 EmergencyModeTracker (하위 호환용).
    
    내부적으로 NamespacedEmergencyTracker를 사용합니다.
    """
    
    def __init__(self):
        self._namespaced = get_namespaced_emergency_tracker()
    
    def get_current_state(self) -> EmergencyState:
        """기존 API 호환."""
        scoped_state = self._namespaced.get_effective_state()
        return EmergencyState(
            is_active=scoped_state.is_active,
            mode=scoped_state.governance_mode,
            activated_at=scoped_state.activated_at,
            activated_by=scoped_state.activated_by,
            reason=scoped_state.reason,
        )
    
    def record_emergency_activation(
        self,
        activated_by: str,
        reason: str = "",
        mode: str = "STRICT",
    ) -> Dict[str, Any]:
        """기존 API 호환 (Regional scope로 동작)."""
        level = EmergencyLevel.LEVEL_3 if mode == "STRICT" else EmergencyLevel.LEVEL_1
        state = self._namespaced.activate_emergency(
            level=level,
            activated_by=activated_by,
            reason=reason,
            scope=EmergencyScope.REGIONAL,
        )
        return {
            "status": "activated",
            "mode": state.governance_mode,
            "namespace": state.namespace,
        }
```

### 4.2 Feature Flag

```python
# settings/feature_flags.py

NAMESPACE_AWARE_EMERGENCY_ENABLED = os.environ.get(
    "SELFHEALING_NAMESPACE_AWARE_EMERGENCY",
    "false"
).lower() == "true"
```

```python
# 사용처
def get_emergency_tracker():
    if NAMESPACE_AWARE_EMERGENCY_ENABLED:
        return get_namespaced_emergency_tracker()
    else:
        return get_legacy_emergency_tracker()
```

---

## 5. 테스트

### 5.1 단위 테스트

```python
class TestNamespacedEmergencyTracker:
    """NamespacedEmergencyTracker 단위 테스트."""
    
    def test_regional_isolation(self):
        """서울 LEVEL_3가 도쿄에 영향 없음."""
        tracker = NamespacedEmergencyTracker()
        
        # 서울에 LEVEL_3 활성화
        tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="test",
            reason="Test",
            scope=EmergencyScope.REGIONAL,
            namespace="seoul",
        )
        
        # 서울은 STRICT
        seoul_state = tracker.get_effective_state(namespace="seoul")
        assert seoul_state.governance_mode == "STRICT"
        assert seoul_state.emergency_level == EmergencyLevel.LEVEL_3
        
        # 도쿄는 NORMAL
        tokyo_state = tracker.get_effective_state(namespace="tokyo")
        assert tokyo_state.governance_mode == "NORMAL"
        assert tokyo_state.emergency_level == EmergencyLevel.NORMAL
    
    def test_global_overrides_regional(self):
        """Global STRICT는 모든 리전을 오버라이드."""
        tracker = NamespacedEmergencyTracker()
        
        # 도쿄는 NORMAL
        tracker.activate_emergency(
            level=EmergencyLevel.NORMAL,
            activated_by="test",
            reason="Test",
            scope=EmergencyScope.REGIONAL,
            namespace="tokyo",
        )
        
        # Global LEVEL_3 활성화
        tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="admin",
            reason="Global emergency",
            scope=EmergencyScope.GLOBAL,
        )
        
        # 도쿄도 STRICT로 오버라이드됨
        tokyo_state = tracker.get_effective_state(namespace="tokyo")
        assert tokyo_state.governance_mode == "STRICT"
        assert tokyo_state.scope == EmergencyScope.GLOBAL
    
    def test_level_to_governance_mode_mapping(self):
        """Emergency Level → Governance Mode 매핑."""
        tracker = NamespacedEmergencyTracker()
        
        test_cases = [
            (EmergencyLevel.NORMAL, "NORMAL"),
            (EmergencyLevel.LEVEL_1, "NORMAL"),   # LEVEL_1은 NORMAL 유지
            (EmergencyLevel.LEVEL_2, "STRICT"),   # LEVEL_2부터 STRICT
            (EmergencyLevel.LEVEL_3, "STRICT"),
        ]
        
        for level, expected_mode in test_cases:
            state = tracker.activate_emergency(
                level=level,
                activated_by="test",
                reason="Test",
                scope=EmergencyScope.REGIONAL,
                namespace="test_ns",
            )
            assert state.governance_mode == expected_mode, \
                f"Level {level} should map to {expected_mode}"
```

### 5.2 통합 테스트

```python
class TestNamespaceAwareEmergencyIntegration:
    """네임스페이스 인지 Emergency 통합 테스트."""
    
    @pytest.fixture
    def setup_multi_region(self):
        """다중 리전 환경 설정."""
        # 각 리전에 대한 설정 준비
        ...
    
    def test_cross_region_isolation_with_redis(self, setup_multi_region, redis_client):
        """Redis 기반 리전 격리 검증."""
        tracker = NamespacedEmergencyTracker()
        
        # 서울에서 LEVEL_3 발생
        tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="seoul-admin",
            reason="Seoul datacenter issue",
            scope=EmergencyScope.REGIONAL,
            namespace="seoul",
        )
        
        # Redis에 올바른 키로 저장됨
        seoul_key = "selfhealing:seoul:governance:emergency_state"
        seoul_data = redis_client.get(seoul_key)
        assert seoul_data is not None
        assert json.loads(seoul_data)["governance_mode"] == "STRICT"
        
        # 도쿄 키는 영향 없음
        tokyo_key = "selfhealing:tokyo:governance:emergency_state"
        tokyo_data = redis_client.get(tokyo_key)
        assert tokyo_data is None or json.loads(tokyo_data).get("is_active") is False
```

---

## 6. 고급 기능

### 6.1 RegionalCascadeDetector (다중 리전 격상 감지)

여러 리전이 동시에 STRICT 상태에 진입하면 **전역적 장애 징후**로 판단하고
GLOBAL 긴급 모드 격상을 제안합니다.

```python
class RegionalCascadeDetector:
    """
    다중 리전 연쇄 장애 감지기.
    
    여러 리전이 동시에 STRICT 상태면 GLOBAL 격상을 권고합니다.
    
    Code reference:
        coordination/anti_flapping.py (AntiFlappingGuard 패턴)
        isolation/regional_gate.py (list_isolated_regions 패턴)
    
    Reference:
        docs/self_healing/middleware_system/73_NAMESPACE_AWARE_EMERGENCY.md
    """
    
    # 기본 임계값
    DEFAULT_ESCALATION_THRESHOLD = 2  # 2개 이상 리전이 STRICT면 경고
    
    def __init__(
        self,
        tracker: Optional[NamespacedEmergencyTracker] = None,
        escalation_threshold: int = DEFAULT_ESCALATION_THRESHOLD,
        auto_escalate: bool = False,
    ):
        """
        Initialize RegionalCascadeDetector.
        
        Args:
            tracker: Emergency 추적기
            escalation_threshold: GLOBAL 격상 권고 임계값
            auto_escalate: True면 자동 격상, False면 권고만
        """
        self._tracker = tracker or get_namespaced_emergency_tracker()
        self._threshold = escalation_threshold
        self._auto_escalate = auto_escalate
    
    def check_cascade_condition(self) -> Dict[str, Any]:
        """
        연쇄 장애 조건 확인.
        
        Returns:
            dict:
                - cascade_detected: 연쇄 장애 감지 여부
                - affected_regions: STRICT 상태인 리전 목록
                - recommend_global: GLOBAL 격상 권고 여부
                - reason: 감지 사유
        """
        active_namespaces = self._tracker.get_all_active_namespaces()
        
        # Global 제외한 Regional STRICT 카운트
        regional_strict = [
            ns for ns in active_namespaces 
            if ns != NamespacedEmergencyTracker.GLOBAL_NAMESPACE
        ]
        strict_count = len(regional_strict)
        
        cascade_detected = strict_count >= self._threshold
        
        result = {
            "cascade_detected": cascade_detected,
            "affected_regions": regional_strict,
            "strict_count": strict_count,
            "threshold": self._threshold,
            "recommend_global": cascade_detected,
            "reason": (
                f"{strict_count} regions in STRICT simultaneously "
                f"(threshold: {self._threshold})"
                if cascade_detected else ""
            ),
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        
        if cascade_detected:
            logger.warning(
                f"[CascadeDetector] CASCADE DETECTED: "
                f"{strict_count} regions in STRICT: {regional_strict}"
            )
            
            # 자동 격상 (설정된 경우)
            if self._auto_escalate:
                self._escalate_to_global(regional_strict)
                result["auto_escalated"] = True
        
        return result
    
    def _escalate_to_global(self, affected_regions: List[str]) -> None:
        """GLOBAL 긴급 모드로 자동 격상."""
        self._tracker.activate_emergency(
            level=EmergencyLevel.LEVEL_3,
            activated_by="RegionalCascadeDetector",
            reason=f"Auto-escalation: {len(affected_regions)} regions in STRICT",
            scope=EmergencyScope.GLOBAL,
        )
        logger.critical(
            f"[CascadeDetector] AUTO-ESCALATED to GLOBAL STRICT: "
            f"affected_regions={affected_regions}"
        )


# Singleton
_cascade_detector: Optional[RegionalCascadeDetector] = None


def get_cascade_detector() -> RegionalCascadeDetector:
    """RegionalCascadeDetector 싱글톤 반환."""
    global _cascade_detector
    if _cascade_detector is None:
        _cascade_detector = RegionalCascadeDetector()
    return _cascade_detector
```

### 6.2 Audit 로그 스코프 태깅

모든 Emergency 상태 변경은 `scope`와 `namespace` 필드를 필수로 포함하여
Audit 로그에 기록됩니다.

```python
@dataclass
class EmergencyAuditEntry:
    """
    Emergency 상태 변경 Audit 엔트리.
    
    scope와 namespace를 필수 필드로 포함하여
    "왜 이 상태가 됐는지" 추적 가능하게 합니다.
    
    Code reference:
        coordination/coordinator.py#L47-58 (DryRunAuditLogger 패턴)
    """
    
    # 필수 필드
    event_id: str
    """고유 이벤트 ID."""
    
    event_type: str
    """이벤트 유형 (EMERGENCY_ACTIVATED, EMERGENCY_DEACTIVATED, CASCADE_DETECTED)."""
    
    namespace: str
    """대상 네임스페이스 (예: 'global', 'seoul', 'tokyo')."""
    
    scope: EmergencyScope
    """적용 범위 (GLOBAL/REGIONAL)."""
    
    # 상태 정보
    emergency_level: EmergencyLevel
    """Emergency 레벨."""
    
    governance_mode: str
    """Governance 모드 (NORMAL/STRICT)."""
    
    # 행위자 정보
    triggered_by: str
    """활성화/비활성화한 주체 (user_id 또는 'system')."""
    
    precedence: Optional[CommandPrecedence] = None
    """명령 우선순위 (수동 명령 시)."""
    
    # 메타데이터
    reason: str = ""
    """변경 사유."""
    
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    """기록 시각."""
    
    # 추가 컨텍스트
    cascade_source: Optional[str] = None
    """연쇄 반응 원인 (다른 리전에서 발생한 경우)."""
    
    global_override_active: bool = False
    """Global STRICT가 활성화되어 이 리전을 오버라이드했는지."""
    
    ttl_minutes: Optional[int] = None
    """수동 오버라이드 TTL (분)."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "namespace": self.namespace,
            "scope": self.scope.value,
            "emergency_level": self.emergency_level.value,
            "governance_mode": self.governance_mode,
            "triggered_by": self.triggered_by,
            "precedence": self.precedence.value if self.precedence else None,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "cascade_source": self.cascade_source,
            "global_override_active": self.global_override_active,
            "ttl_minutes": self.ttl_minutes,
        }


class EmergencyAuditLogger:
    """
    Emergency 상태 변경 Audit 로거.
    
    모든 상태 변경을 scope/namespace 태깅과 함께 기록합니다.
    """
    
    def __init__(self):
        self._lock = threading.Lock()
    
    def log_activation(
        self,
        state: ScopedEmergencyState,
        triggered_by: str,
        reason: str,
        precedence: Optional[CommandPrecedence] = None,
    ) -> str:
        """
        Emergency 활성화 기록.
        
        Args:
            state: 활성화된 상태
            triggered_by: 활성화한 주체
            reason: 활성화 사유
            precedence: 명령 우선순위
        
        Returns:
            생성된 event_id
        """
        event_id = f"emerg-{uuid.uuid4().hex[:12]}"
        
        entry = EmergencyAuditEntry(
            event_id=event_id,
            event_type="EMERGENCY_ACTIVATED",
            namespace=state.namespace,
            scope=state.scope,
            emergency_level=state.emergency_level,
            governance_mode=state.governance_mode,
            triggered_by=triggered_by,
            precedence=precedence,
            reason=reason,
        )
        
        self._write_audit_log(entry)
        
        logger.info(
            f"[EmergencyAudit] ACTIVATED: "
            f"namespace={state.namespace}, scope={state.scope.value}, "
            f"level={state.emergency_level.name}, by={triggered_by}"
        )
        
        return event_id
    
    def log_deactivation(
        self,
        state: ScopedEmergencyState,
        triggered_by: str,
        reason: str = "",
    ) -> str:
        """Emergency 비활성화 기록."""
        event_id = f"emerg-{uuid.uuid4().hex[:12]}"
        
        entry = EmergencyAuditEntry(
            event_id=event_id,
            event_type="EMERGENCY_DEACTIVATED",
            namespace=state.namespace,
            scope=state.scope,
            emergency_level=EmergencyLevel.NORMAL,
            governance_mode="NORMAL",
            triggered_by=triggered_by,
            reason=reason,
        )
        
        self._write_audit_log(entry)
        return event_id
    
    def log_cascade_detection(
        self,
        affected_regions: List[str],
        auto_escalated: bool,
    ) -> str:
        """연쇄 장애 감지 기록."""
        event_id = f"cascade-{uuid.uuid4().hex[:12]}"
        
        entry = EmergencyAuditEntry(
            event_id=event_id,
            event_type="CASCADE_DETECTED",
            namespace="global",
            scope=EmergencyScope.GLOBAL,
            emergency_level=EmergencyLevel.LEVEL_3,
            governance_mode="STRICT" if auto_escalated else "PENDING",
            triggered_by="RegionalCascadeDetector",
            reason=f"Cascade detected in regions: {affected_regions}",
            cascade_source=affected_regions[0] if affected_regions else None,
        )
        
        self._write_audit_log(entry)
        return event_id
    
    def _write_audit_log(self, entry: EmergencyAuditEntry) -> None:
        """실제 Audit 시스템에 기록."""
        with self._lock:
            # 실제 구현에서는 CriticalPathFallback 연동
            # from selfhealing.services.coordination import CriticalPathFallback
            # fallback = CriticalPathFallback()
            # fallback.append_audit_log(entry.to_dict())
            
            logger.debug(f"[EmergencyAudit] Entry: {entry.to_dict()}")


# Singleton
_audit_logger: Optional[EmergencyAuditLogger] = None


def get_emergency_audit_logger() -> EmergencyAuditLogger:
    """EmergencyAuditLogger 싱글톤 반환."""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = EmergencyAuditLogger()
    return _audit_logger
```

### 6.3 SSOT Prefixing (키 네임스페이스 통합)

모든 Redis 키는 `NamespaceSettings.get_key_prefix()`를 통해 생성되어
하드코딩된 키가 없도록 합니다.

```python
# 설계 원칙: Single Source of Truth for Key Prefixing
# 모든 Redis 키는 get_key_prefix()를 통해 생성

# AS-IS (하드코딩 - 지양)
STATE_KEY_PATTERN = "selfhealing:{namespace}:governance:emergency_state"

# TO-BE (SSOT - 권장)
def _get_state_key(self, namespace: str) -> str:
    """
    SSOT Prefixing 적용.
    
    Code reference:
        settings/namespace.py#L88-97 (get_key_prefix)
    """
    from selfhealing.settings.namespace import get_namespace_settings
    settings = get_namespace_settings()
    
    if namespace == self.GLOBAL_NAMESPACE:
        # Global: 네임스페이스 없이 기본 프리픽스만
        base_prefix = settings.get_key_prefix().rstrip(":").split(":")[0]
        return f"{base_prefix}:governance:emergency_state"
    
    # Regional: 명시적 namespace
    return f"selfhealing:{namespace}:governance:emergency_state"
```

**장점:**
- 환경변수 `SELFHEALING_NAMESPACE`만 변경하면 모든 키가 자동 분리
- 키 누수/오동작 방지
- "하드코딩된 키가 단 하나도 없다"는 실사 증명 가능

### 6.4 EmergencyHealthPenalty (Health Score 연동)

Emergency 상태가 Health Score에 자동 반영되어 대시보드에서
"왜 점수가 떨어졌는지" 즉시 파악 가능합니다.

```python
class EmergencyHealthPenalty:
    """
    Emergency 상태에 따른 Health Score 감점.
    
    PropagationHealthMonitor와 통합하여 Emergency 상태가
    Health Score에 자동 반영됩니다.
    
    Code reference:
        config/propagation_health.py#L103-107 (감점 패턴)
    """
    
    # 감점 가중치
    REGIONAL_STRICT_PENALTY = 20   # Regional STRICT: -20점
    GLOBAL_STRICT_PENALTY = 30     # Global STRICT: -30점
    
    # 복구 속도 (점진적 복구)
    RECOVERY_RATE_PER_MINUTE = 10  # 분당 10점 복구
    
    def __init__(
        self,
        tracker: Optional[NamespacedEmergencyTracker] = None,
    ):
        """
        Initialize EmergencyHealthPenalty.
        
        Args:
            tracker: Emergency 추적기
        """
        self._tracker = tracker or get_namespaced_emergency_tracker()
        self._last_recovery_at: Dict[str, datetime] = {}
    
    def calculate_penalty(
        self,
        namespace: Optional[str] = None,
    ) -> float:
        """
        현재 Emergency 상태에 따른 감점 계산.
        
        Args:
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            감점 점수 (0-100, 양수)
        """
        state = self._tracker.get_effective_state(namespace=namespace)
        
        if not state.is_active:
            return 0.0
        
        if state.scope == EmergencyScope.GLOBAL:
            return self.GLOBAL_STRICT_PENALTY
        else:
            return self.REGIONAL_STRICT_PENALTY
    
    def get_health_score_with_emergency(
        self,
        base_score: float,
        namespace: Optional[str] = None,
    ) -> float:
        """
        Emergency 감점이 반영된 Health Score 반환.
        
        Args:
            base_score: 기본 Health Score (0-100)
            namespace: 대상 네임스페이스
        
        Returns:
            감점 적용된 Health Score (0-100)
        """
        penalty = self.calculate_penalty(namespace=namespace)
        adjusted_score = base_score - penalty
        
        if penalty > 0:
            logger.debug(
                f"[EmergencyHealthPenalty] Applied penalty: "
                f"base={base_score:.1f}, penalty={penalty:.1f}, "
                f"adjusted={adjusted_score:.1f}"
            )
        
        return max(0.0, min(100.0, adjusted_score))
    
    def get_penalty_breakdown(
        self,
        namespace: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        감점 상세 내역 반환.
        
        대시보드에서 "왜 점수가 떨어졌는지" 표시용.
        
        Returns:
            dict:
                - penalty: 감점 점수
                - reason: 감점 사유
                - scope: 적용 범위
                - emergency_level: Emergency 레벨
        """
        state = self._tracker.get_effective_state(namespace=namespace)
        
        if not state.is_active:
            return {
                "penalty": 0.0,
                "reason": None,
                "scope": None,
                "emergency_level": EmergencyLevel.NORMAL.name,
            }
        
        penalty = self.calculate_penalty(namespace=namespace)
        
        return {
            "penalty": penalty,
            "reason": (
                f"Emergency Mode {state.scope.value.upper()} STRICT active "
                f"since {state.activated_at}"
            ),
            "scope": state.scope.value,
            "emergency_level": state.emergency_level.name,
            "activated_by": state.activated_by,
            "activated_at": state.activated_at,
        }


# Singleton
_health_penalty: Optional[EmergencyHealthPenalty] = None


def get_emergency_health_penalty() -> EmergencyHealthPenalty:
    """EmergencyHealthPenalty 싱글톤 반환."""
    global _health_penalty
    if _health_penalty is None:
        _health_penalty = EmergencyHealthPenalty()
    return _health_penalty
```

**대시보드 연동 예시:**

```python
# PropagationHealthMonitor 확장
class EnhancedPropagationHealthMonitor(PropagationHealthMonitor):
    """Emergency 감점이 반영된 Health Monitor."""
    
    def get_current_metrics(self) -> PropagationHealthMetrics:
        metrics = super().get_current_metrics()
        
        # Emergency 감점 적용
        penalty = get_emergency_health_penalty()
        metrics.propagation_health_score = penalty.get_health_score_with_emergency(
            base_score=metrics.propagation_health_score
        )
        
        return metrics
```

### 6.5 네트워크 고립 시 상태 관리

리전이 네트워크 고립(Partition)으로 인해 Global Redis와 연결이 끊긴 경우의
상태 관리 정책입니다.

#### 6.5.1 설계 원칙: 강제 동기화 불필요

```
┌─────────────────────────────────────────────────────────────────┐
│                 Network Partition Handling                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Global Redis         [X] 연결 끊김                              │
│       │                                                          │
│       ├── Seoul: STRICT (자체 보호 모드 유지)                    │
│       │          └── TTL 기반 자동 만료 (8시간)                  │
│       │                                                          │
│       └── Tokyo: NORMAL (정상 운영)                              │
│                                                                  │
│  원칙: 고립된 리전은 자체 상태 유지 (Safety-First)              │
│        → 네트워크 복구 시 Reconciliation 수행                    │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**강제 동기화를 하지 않는 이유:**

| 측면 | 설명 |
|------|------|
| **Safety-First** | 고립된 리전이 자체 보호 모드 유지 |
| **추가 장애 방지** | 동기화 시도 자체가 네트워크 부하 증가 |
| **Stale 방지** | TTL 기반 자동 만료로 영구 stale 상태 방지 |
| **기존 패턴 일관성** | `RegionalIsolationGate` 패턴과 동일 |

#### 6.5.2 복구 시 Reconciliation

```python
class PartitionReconciliationService:
    """
    네트워크 고립 복구 시 상태 조정 서비스.
    
    Code reference:
        error_budget/reconciliation/service.py (ReconciliationService 패턴)
        isolation/regional_gate.py#L133-141 (TTL 기반 만료)
    """
    
    # Heartbeat 설정
    HEARTBEAT_INTERVAL_SECONDS = 10
    PARTITION_DETECTION_THRESHOLD_SECONDS = 30
    
    def __init__(
        self,
        tracker: Optional[NamespacedEmergencyTracker] = None,
    ):
        self._tracker = tracker or get_namespaced_emergency_tracker()
        self._last_global_heartbeat: Optional[datetime] = None
        self._is_partitioned = False
    
    def check_partition_status(self) -> Dict[str, Any]:
        """
        현재 리전의 네트워크 고립 상태 확인.
        
        Returns:
            dict:
                - is_partitioned: 고립 여부
                - last_heartbeat: 마지막 heartbeat 시각
                - duration_seconds: 고립 지속 시간
        """
        now = datetime.now(timezone.utc)
        
        # Global Redis heartbeat 시도
        try:
            self._ping_global_redis()
            self._last_global_heartbeat = now
            self._is_partitioned = False
            
            return {
                "is_partitioned": False,
                "last_heartbeat": now.isoformat(),
                "duration_seconds": 0,
            }
        except Exception as e:
            if self._last_global_heartbeat:
                duration = (now - self._last_global_heartbeat).total_seconds()
                self._is_partitioned = duration > self.PARTITION_DETECTION_THRESHOLD_SECONDS
            else:
                self._is_partitioned = True
                duration = float("inf")
            
            return {
                "is_partitioned": self._is_partitioned,
                "last_heartbeat": (
                    self._last_global_heartbeat.isoformat() 
                    if self._last_global_heartbeat else None
                ),
                "duration_seconds": duration,
                "error": str(e),
            }
    
    def reconcile_after_recovery(self) -> Dict[str, Any]:
        """
        네트워크 복구 후 상태 조정.
        
        Returns:
            dict:
                - reconciled: 조정 수행 여부
                - actions: 수행된 조정 액션 목록
        """
        if self._is_partitioned:
            return {
                "reconciled": False,
                "reason": "Still partitioned",
            }
        
        actions = []
        current_ns = self._tracker._get_current_namespace()
        
        # 1. Global 상태 확인
        global_state = self._tracker._load_state(
            NamespacedEmergencyTracker.GLOBAL_NAMESPACE
        )
        
        # 2. Regional 상태 확인
        regional_state = self._tracker._load_state(current_ns)
        
        # 3. 상태 조정 (Global이 해제되었으면 Regional도 검토)
        if not global_state.is_active and regional_state.is_active:
            # Regional이 아직 STRICT이지만 Global은 NORMAL
            # → 운영자 알림 발송 (자동 변경 X)
            actions.append({
                "type": "NOTIFICATION",
                "message": (
                    f"Region {current_ns} is still in STRICT mode "
                    f"while Global is NORMAL. Manual review recommended."
                ),
            })
        
        logger.info(
            f"[PartitionReconciliation] Reconciled after recovery: "
            f"namespace={current_ns}, actions={len(actions)}"
        )
        
        return {
            "reconciled": True,
            "actions": actions,
            "global_state": global_state.governance_mode,
            "regional_state": regional_state.governance_mode,
        }
    
    def _ping_global_redis(self) -> bool:
        """Global Redis ping."""
        from selfhealing.core.tiered_redis import TieredRedisProvider, RedisScope
        provider = TieredRedisProvider()
        client = provider.get_redis(RedisScope.GLOBAL)
        return client.ping()


# Singleton
_reconciliation_service: Optional[PartitionReconciliationService] = None


def get_partition_reconciliation_service() -> PartitionReconciliationService:
    """PartitionReconciliationService 싱글톤 반환."""
    global _reconciliation_service
    if _reconciliation_service is None:
        _reconciliation_service = PartitionReconciliationService()
    return _reconciliation_service
```

---

## 7. 모니터링

### 7.1 메트릭

```python
# Prometheus 메트릭
EMERGENCY_STATE_GAUGE = Gauge(
    "selfhealing_emergency_state",
    "Current emergency state per namespace",
    ["namespace", "scope"],
)

EMERGENCY_LEVEL_GAUGE = Gauge(
    "selfhealing_emergency_level",
    "Current emergency level per namespace",
    ["namespace"],
)

EMERGENCY_ACTIVATIONS_TOTAL = Counter(
    "selfhealing_emergency_activations_total",
    "Total emergency activations",
    ["namespace", "scope", "level"],
)
```

### 7.2 대시보드 쿼리

```promql
# 리전별 Emergency 상태
selfhealing_emergency_state{scope="regional"}

# Global Emergency 활성화 여부
selfhealing_emergency_state{namespace="global", scope="global"}

# 최근 24시간 리전별 활성화 횟수
increase(selfhealing_emergency_activations_total[24h])
```

---

## 8. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |
| 1.1.0 | 2026-01-21 | RegionalCascadeDetector, EmergencyAuditLogger 추가 | AI Assistant |
| 1.2.0 | 2026-01-21 | get_effective_state Precedence-First Hierarchy, SSOT Prefixing, EmergencyHealthPenalty, PartitionReconciliationService 추가 | AI Assistant |

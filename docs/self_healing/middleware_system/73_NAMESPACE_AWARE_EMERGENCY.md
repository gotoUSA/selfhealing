# 73. Namespace-Aware Emergency (리전별 긴급 모드 격리)

> **Version**: 1.0.0  
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
        """네임스페이스용 Redis 키 생성."""
        if namespace == self.GLOBAL_NAMESPACE:
            return "selfhealing:governance:emergency_state"  # 기존 호환
        return self.STATE_KEY_PATTERN.format(namespace=namespace)
    
    # =========================================================================
    # Public API
    # =========================================================================
    
    def get_effective_state(
        self,
        namespace: Optional[str] = None,
    ) -> ScopedEmergencyState:
        """
        유효한 Emergency 상태 조회.
        
        우선순위:
        1. Global 상태가 STRICT이면 → Global 상태 반환
        2. 아니면 → Regional 상태 반환
        
        Args:
            namespace: 조회할 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            유효한 ScopedEmergencyState
        """
        ns = namespace or self._get_current_namespace()
        
        with self._lock:
            # 1. Global 상태 확인
            global_state = self._load_state(self.GLOBAL_NAMESPACE)
            if global_state.is_active and global_state.governance_mode == "STRICT":
                logger.debug(
                    f"[EmergencyTracker] Global STRICT active, "
                    f"overriding namespace={ns}"
                )
                return global_state
            
            # 2. Regional 상태 확인
            if ns != self.GLOBAL_NAMESPACE:
                regional_state = self._load_state(ns)
                return regional_state
            
            return global_state
    
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

## 6. 모니터링

### 6.1 메트릭

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

### 6.2 대시보드 쿼리

```promql
# 리전별 Emergency 상태
selfhealing_emergency_state{scope="regional"}

# Global Emergency 활성화 여부
selfhealing_emergency_state{namespace="global", scope="global"}

# 최근 24시간 리전별 활성화 횟수
increase(selfhealing_emergency_activations_total[24h])
```

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |

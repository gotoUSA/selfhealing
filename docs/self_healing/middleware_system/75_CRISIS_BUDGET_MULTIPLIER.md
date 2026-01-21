# 75. Crisis Budget Multiplier (위기 가중치 버짓팅)

> **Version**: 1.0.0  
> **Created**: 2026-01-21  
> **Status**: Draft  
> **Parent**: [72_EMERGENCY_COORDINATION_LAYER.md](72_EMERGENCY_COORDINATION_LAYER.md)

## 1. 개요

### 1.1 문제점 (AS-IS)

현재 Error Budget 계산은 Emergency Level을 **무시**합니다:

```python
# 현재 구현 - error_budget/calculator.py
def calculate_budget_status(self) -> BudgetStatus:
    # Emergency Level과 관계없이 동일한 가중치
    consumed = self._get_consumed_budget()
    remaining = self.total_budget - consumed
    ...
```

**문제**:
- LEVEL_3 상황의 에러와 평상시 에러가 **동일한 가치**로 취급됨
- 시스템이 한계에 도달한 시점의 에러는 비즈니스에 **훨씬 더 치명적**
- 버짓 소진율이 위기 상황을 제대로 반영하지 못함

### 1.2 해결책 (TO-BE)

**Crisis Multiplier** 도입:

| Emergency Level | Multiplier | 의미 |
|----------------|------------|------|
| NORMAL | 1.0x | 기본 소진율 |
| LEVEL_1 | 1.5x | 경미한 위기 |
| LEVEL_2 | 3.0x | 중간 위기 |
| LEVEL_3 | 5.0x | 심각한 위기 |

**예시**:
- 평상시 에러 1건 = 버짓 1분 소진
- LEVEL_3 에러 1건 = 버짓 **5분** 소진

---

## 2. 아키텍처

### 2.1 Multiplier 적용 흐름

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Crisis Multiplier Flow                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                  Error Budget Calculator                      │  │
│  │                                                               │  │
│  │  record_error()  ──▶  get_multiplier()  ──▶  apply_weighted  │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              CrisisMultiplierProvider (신규)                  │  │
│  │                                                               │  │
│  │  ┌─────────────────────────────────────────────────────────┐ │  │
│  │  │ get_current_multiplier() -> float                       │ │  │
│  │  │                                                          │ │  │
│  │  │ 1. Get current EmergencyLevel                           │ │  │
│  │  │ 2. Lookup multiplier from config                        │ │  │
│  │  │ 3. Return multiplier value                              │ │  │
│  │  └─────────────────────────────────────────────────────────┘ │  │
│  │                                                               │  │
│  └───────────────────────────┬──────────────────────────────────┘  │
│                              │                                      │
│                              ▼                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │              Multiplier Configuration                         │  │
│  │                                                               │  │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐        │  │
│  │  │ NORMAL  │  │ LEVEL_1 │  │ LEVEL_2 │  │ LEVEL_3 │        │  │
│  │  │         │  │         │  │         │  │         │        │  │
│  │  │  1.0x   │  │  1.5x   │  │  3.0x   │  │  5.0x   │        │  │
│  │  └─────────┘  └─────────┘  └─────────┘  └─────────┘        │  │
│  │                                                               │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 버짓 소진 계산

```
                    기존 방식                      새로운 방식
                    ─────────                      ──────────
                    
    에러 발생        ───▶  1분 소진               에러 발생 ─┬─▶ NORMAL:  1분 × 1.0 = 1분
                                                             ├─▶ LEVEL_1: 1분 × 1.5 = 1.5분
                                                             ├─▶ LEVEL_2: 1분 × 3.0 = 3분
                                                             └─▶ LEVEL_3: 1분 × 5.0 = 5분

    ┌─────────────────────────────────────────────────────────────────┐
    │                    Budget Consumption Timeline                   │
    ├─────────────────────────────────────────────────────────────────┤
    │                                                                  │
    │  100% ┤                                                         │
    │       │  ╭────╮ Normal consumption                              │
    │   75% ┤  │    ╲                                                 │
    │       │  │     ╲    ╭── LEVEL_3 spike (5x)                     │
    │   50% ┤  │      ╲   │                                           │
    │       │  │       ╲──╯                                           │
    │   25% ┤  │           ╲                                          │
    │       │  │            ╲                                         │
    │    0% ┼──┴─────────────┴────────────────────────────────────▶  │
    │       0h              4h              8h              12h       │
    │                                                                  │
    │  Legend: ─── Normal consumption                                 │
    │          ─── Crisis multiplied consumption                      │
    │                                                                  │
    └─────────────────────────────────────────────────────────────────┘
```

---

## 3. 구현 상세

### 3.1 CrisisMultiplierConfig

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/multiplier.py

from dataclasses import dataclass, field
from typing import Dict, Optional
from selfhealing.services.emergency_mode.enums import EmergencyLevel


@dataclass
class CrisisMultiplierConfig:
    """
    위기 가중치 설정.
    
    Emergency Level별 Error Budget 소진 가중치를 정의합니다.
    
    Attributes:
        multipliers: Level별 가중치 매핑
        enabled: 기능 활성화 여부
        max_multiplier: 최대 허용 가중치 (과도한 소진 방지)
    """
    
    multipliers: Dict[EmergencyLevel, float] = field(default_factory=lambda: {
        EmergencyLevel.NORMAL: 1.0,
        EmergencyLevel.LEVEL_1: 1.5,
        EmergencyLevel.LEVEL_2: 3.0,
        EmergencyLevel.LEVEL_3: 5.0,
    })
    
    enabled: bool = True
    """Crisis Multiplier 활성화 여부."""
    
    max_multiplier: float = 10.0
    """최대 허용 가중치 (안전 제한)."""
    
    def get_multiplier(self, level: EmergencyLevel) -> float:
        """
        레벨에 해당하는 가중치 반환.
        
        Args:
            level: Emergency 레벨
        
        Returns:
            가중치 값 (기본 1.0)
        """
        if not self.enabled:
            return 1.0
        
        multiplier = self.multipliers.get(level, 1.0)
        return min(multiplier, self.max_multiplier)
    
    @classmethod
    def from_dict(cls, data: Dict) -> "CrisisMultiplierConfig":
        """딕셔너리에서 생성."""
        multipliers = {}
        for level_name, value in data.get("multipliers", {}).items():
            try:
                level = EmergencyLevel[level_name]
                multipliers[level] = float(value)
            except (KeyError, ValueError):
                continue
        
        return cls(
            multipliers=multipliers or cls().multipliers,
            enabled=data.get("enabled", True),
            max_multiplier=data.get("max_multiplier", 10.0),
        )
```

### 3.2 CrisisMultiplierProvider

```python
class CrisisMultiplierProvider:
    """
    위기 가중치 제공자.
    
    현재 Emergency Level에 따른 Error Budget 소진 가중치를 제공합니다.
    
    Features:
    - Emergency Level 기반 가중치 조회
    - 설정 가능한 가중치
    - TTL 캐시 (Check on Use 패턴)
    
    Reference:
    - docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md
    """
    
    def __init__(
        self,
        config: Optional[CrisisMultiplierConfig] = None,
    ):
        """
        CrisisMultiplierProvider 초기화.
        
        Args:
            config: 가중치 설정 (None이면 기본값 사용)
        """
        self.config = config or CrisisMultiplierConfig()
        self._emergency_tracker = None
        self._cache_ttl = 30.0  # 30초 캐시
        self._cached_multiplier: Optional[float] = None
        self._cache_timestamp: float = 0
    
    def _get_emergency_tracker(self):
        """EmergencyTracker 획득 (lazy)."""
        if self._emergency_tracker is None:
            from selfhealing.services.governance import get_namespaced_emergency_tracker
            self._emergency_tracker = get_namespaced_emergency_tracker()
        return self._emergency_tracker
    
    def get_current_multiplier(
        self,
        namespace: Optional[str] = None,
    ) -> float:
        """
        현재 Crisis Multiplier 조회.
        
        Args:
            namespace: 대상 네임스페이스 (None이면 현재 인스턴스)
        
        Returns:
            현재 가중치 값
        """
        import time
        
        # 캐시 확인
        now = time.time()
        if (
            self._cached_multiplier is not None
            and now - self._cache_timestamp < self._cache_ttl
        ):
            return self._cached_multiplier
        
        # Emergency Level 조회
        tracker = self._get_emergency_tracker()
        state = tracker.get_effective_state(namespace=namespace)
        level = state.emergency_level
        
        # 가중치 조회
        multiplier = self.config.get_multiplier(level)
        
        # 캐시 저장
        self._cached_multiplier = multiplier
        self._cache_timestamp = now
        
        return multiplier
    
    def invalidate_cache(self) -> None:
        """캐시 무효화 (이벤트 버스 연동용)."""
        self._cached_multiplier = None
        self._cache_timestamp = 0
    
    def set_multiplier_override(
        self,
        level: EmergencyLevel,
        multiplier: float,
    ) -> None:
        """
        특정 레벨의 가중치 오버라이드 (런타임 설정).
        
        Args:
            level: 대상 레벨
            multiplier: 새 가중치
        """
        self.config.multipliers[level] = min(
            multiplier, 
            self.config.max_multiplier
        )
        self.invalidate_cache()
        
        logger.info(
            f"[CrisisMultiplier] Override set: "
            f"level={level.name}, multiplier={multiplier}"
        )


# =============================================================================
# Singleton
# =============================================================================

_multiplier_provider: Optional[CrisisMultiplierProvider] = None


def get_crisis_multiplier_provider() -> CrisisMultiplierProvider:
    """CrisisMultiplierProvider 싱글톤 반환."""
    global _multiplier_provider
    if _multiplier_provider is None:
        _multiplier_provider = CrisisMultiplierProvider()
    return _multiplier_provider
```

### 3.3 ErrorBudgetCalculator 수정

```python
# packages/selfhealing-python/src/selfhealing/services/error_budget/calculator.py

class ErrorBudgetCalculator:
    """Error Budget 계산기."""
    
    def __init__(
        self,
        slo_config: Optional[SLOConfig] = None,
        crisis_multiplier_provider: Optional[CrisisMultiplierProvider] = None,
        enable_crisis_multiplier: bool = True,
    ):
        self.slo_config = slo_config or get_default_slo_config()
        self.crisis_multiplier_provider = (
            crisis_multiplier_provider or get_crisis_multiplier_provider()
        )
        self.enable_crisis_multiplier = enable_crisis_multiplier
    
    def record_error(
        self,
        error_type: str,
        service_name: str,
        duration_minutes: float = 1.0,
        namespace: Optional[str] = None,
    ) -> ErrorRecord:
        """
        에러 기록 및 버짓 소진.
        
        Crisis Multiplier가 적용된 가중치로 버짓을 소진합니다.
        
        Args:
            error_type: 에러 유형
            service_name: 서비스 이름
            duration_minutes: 에러 지속 시간 (분)
            namespace: 네임스페이스
        
        Returns:
            ErrorRecord: 기록된 에러 정보
        """
        # Crisis Multiplier 조회
        multiplier = 1.0
        if self.enable_crisis_multiplier:
            multiplier = self.crisis_multiplier_provider.get_current_multiplier(
                namespace=namespace
            )
        
        # 가중치 적용된 소진량 계산
        weighted_consumption = duration_minutes * multiplier
        
        # 에러 기록
        record = ErrorRecord(
            error_type=error_type,
            service_name=service_name,
            raw_duration_minutes=duration_minutes,
            weighted_duration_minutes=weighted_consumption,
            multiplier_applied=multiplier,
            recorded_at=utc_now(),
            namespace=namespace,
        )
        
        # 저장 및 버짓 소진
        self._save_error_record(record)
        self._consume_budget(weighted_consumption)
        
        # 로깅
        if multiplier > 1.0:
            logger.warning(
                f"[ErrorBudget] Crisis multiplier applied: "
                f"raw={duration_minutes}min, weighted={weighted_consumption}min, "
                f"multiplier={multiplier}x"
            )
        
        return record
    
    def calculate_budget_status(
        self,
        namespace: Optional[str] = None,
    ) -> BudgetStatus:
        """
        현재 버짓 상태 계산.
        
        현재 Crisis Multiplier도 포함하여 반환합니다.
        """
        consumed = self._get_consumed_budget()
        remaining = max(0, self.total_budget - consumed)
        remaining_percent = (remaining / self.total_budget) * 100
        
        # 현재 Crisis Multiplier
        current_multiplier = 1.0
        if self.enable_crisis_multiplier:
            current_multiplier = self.crisis_multiplier_provider.get_current_multiplier(
                namespace=namespace
            )
        
        return BudgetStatus(
            budget_total_minutes=self.total_budget,
            budget_consumed_minutes=consumed,
            budget_remaining_minutes=remaining,
            budget_remaining_percent=remaining_percent,
            current_crisis_multiplier=current_multiplier,
            verdict=self._get_verdict(remaining_percent),
        )
```

### 3.4 ErrorRecord 모델 확장

```python
@dataclass
class ErrorRecord:
    """에러 기록."""
    
    error_type: str
    """에러 유형."""
    
    service_name: str
    """서비스 이름."""
    
    raw_duration_minutes: float
    """원시 지속 시간 (분)."""
    
    weighted_duration_minutes: float
    """가중치 적용된 소진량 (분)."""
    
    multiplier_applied: float
    """적용된 Crisis Multiplier."""
    
    recorded_at: datetime
    """기록 시각."""
    
    namespace: Optional[str] = None
    """네임스페이스."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "error_type": self.error_type,
            "service_name": self.service_name,
            "raw_duration_minutes": self.raw_duration_minutes,
            "weighted_duration_minutes": self.weighted_duration_minutes,
            "multiplier_applied": self.multiplier_applied,
            "recorded_at": self.recorded_at.isoformat(),
            "namespace": self.namespace,
        }


@dataclass
class BudgetStatus:
    """버짓 상태."""
    
    budget_total_minutes: float
    """총 버짓 (분)."""
    
    budget_consumed_minutes: float
    """소진된 버짓 (분)."""
    
    budget_remaining_minutes: float
    """남은 버짓 (분)."""
    
    budget_remaining_percent: float
    """남은 버짓 비율 (%)."""
    
    current_crisis_multiplier: float = 1.0
    """현재 적용 중인 Crisis Multiplier."""
    
    verdict: str = "healthy"
    """상태 판정 (healthy, caution, warning, freeze)."""
```

---

## 4. 설정

### 4.1 crisis_multiplier.yaml

```yaml
# Crisis Multiplier Configuration
# Reference: docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md

version: "1.0"

# 기본 설정
defaults:
  enabled: true
  max_multiplier: 10.0  # 최대 10배까지 허용

# Emergency Level별 가중치
multipliers:
  NORMAL: 1.0      # 기본값
  LEVEL_1: 1.5     # 경미한 위기: 1.5배
  LEVEL_2: 3.0     # 중간 위기: 3배
  LEVEL_3: 5.0     # 심각한 위기: 5배

# 가중치 적용 옵션
options:
  # 가중치 적용 시 로깅
  log_on_multiplier_applied: true
  log_threshold: 1.5  # 이 값 이상일 때만 로깅
  
  # 알림 설정
  notification:
    enabled: true
    # 가중치가 이 값 이상이면 알림
    notify_threshold: 3.0
    channels: ["slack"]

# 버짓 소진율 경고 임계값 (가중치 적용 후)
budget_thresholds:
  warning: 50.0    # 50% 이하
  critical: 20.0   # 20% 이하
  freeze: 10.0     # 10% 이하 → 자동화 중단
```

---

## 5. 테스트

### 5.1 단위 테스트

```python
class TestCrisisMultiplierProvider:
    """CrisisMultiplierProvider 단위 테스트."""
    
    def test_normal_level_returns_1x(self):
        """NORMAL 레벨에서 1.0x 반환."""
        provider = CrisisMultiplierProvider()
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.NORMAL,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 1.0
    
    def test_level_3_returns_5x(self):
        """LEVEL_3에서 5.0x 반환."""
        provider = CrisisMultiplierProvider()
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 5.0
    
    def test_custom_multipliers(self):
        """커스텀 가중치 설정."""
        config = CrisisMultiplierConfig(
            multipliers={
                EmergencyLevel.NORMAL: 1.0,
                EmergencyLevel.LEVEL_3: 10.0,
            }
        )
        provider = CrisisMultiplierProvider(config=config)
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            assert multiplier == 10.0
    
    def test_max_multiplier_cap(self):
        """최대 가중치 제한."""
        config = CrisisMultiplierConfig(
            multipliers={EmergencyLevel.LEVEL_3: 100.0},
            max_multiplier=10.0,
        )
        provider = CrisisMultiplierProvider(config=config)
        
        with patch.object(
            provider, '_get_emergency_tracker'
        ) as mock_tracker:
            mock_state = ScopedEmergencyState(
                namespace="test",
                scope=EmergencyScope.REGIONAL,
                emergency_level=EmergencyLevel.LEVEL_3,
            )
            mock_tracker.return_value.get_effective_state.return_value = mock_state
            
            multiplier = provider.get_current_multiplier()
            
            # 100.0 요청했지만 max 10.0으로 제한
            assert multiplier == 10.0


class TestErrorBudgetCalculatorWithMultiplier:
    """Crisis Multiplier 적용된 ErrorBudgetCalculator 테스트."""
    
    def test_record_error_with_multiplier(self):
        """가중치 적용된 에러 기록."""
        mock_provider = MagicMock()
        mock_provider.get_current_multiplier.return_value = 5.0
        
        calculator = ErrorBudgetCalculator(
            crisis_multiplier_provider=mock_provider,
        )
        
        record = calculator.record_error(
            error_type="timeout",
            service_name="api",
            duration_minutes=1.0,
        )
        
        assert record.raw_duration_minutes == 1.0
        assert record.weighted_duration_minutes == 5.0
        assert record.multiplier_applied == 5.0
    
    def test_budget_status_includes_multiplier(self):
        """버짓 상태에 현재 가중치 포함."""
        mock_provider = MagicMock()
        mock_provider.get_current_multiplier.return_value = 3.0
        
        calculator = ErrorBudgetCalculator(
            crisis_multiplier_provider=mock_provider,
        )
        
        status = calculator.calculate_budget_status()
        
        assert status.current_crisis_multiplier == 3.0
```

### 5.2 통합 테스트

```python
class TestCrisisMultiplierIntegration:
    """Crisis Multiplier 통합 테스트."""
    
    def test_level_3_accelerates_budget_consumption(
        self, 
        error_budget_service,
        emergency_manager,
    ):
        """LEVEL_3에서 버짓 소진 가속화."""
        # Given: 초기 버짓 상태
        initial_status = error_budget_service.calculate_budget_status()
        initial_remaining = initial_status.budget_remaining_minutes
        
        # When: LEVEL_3 상황에서 에러 발생
        emergency_manager.activate_manual(
            level=EmergencyLevel.LEVEL_3,
            reason="Test",
        )
        
        error_budget_service.record_error(
            error_type="timeout",
            service_name="api",
            duration_minutes=1.0,
        )
        
        # Then: 5분이 소진됨 (1분 × 5배)
        new_status = error_budget_service.calculate_budget_status()
        consumed = initial_remaining - new_status.budget_remaining_minutes
        
        assert consumed == 5.0  # 1분 × 5배
        assert new_status.current_crisis_multiplier == 5.0
```

---

## 6. 모니터링

### 6.1 메트릭

```python
CRISIS_MULTIPLIER_GAUGE = Gauge(
    "selfhealing_crisis_multiplier_current",
    "Current crisis multiplier value",
    ["namespace"],
)

ERROR_BUDGET_WEIGHTED_CONSUMPTION = Counter(
    "selfhealing_error_budget_weighted_consumption_minutes_total",
    "Total weighted error budget consumption",
    ["namespace", "multiplier_range"],
)

CRISIS_MULTIPLIER_ACTIVATIONS = Counter(
    "selfhealing_crisis_multiplier_activations_total",
    "Number of times crisis multiplier was applied (>1.0)",
    ["emergency_level", "namespace"],
)
```

### 6.2 대시보드 쿼리

```promql
# 현재 Crisis Multiplier
selfhealing_crisis_multiplier_current

# 가중치 적용된 버짓 소진율 (최근 1시간)
rate(selfhealing_error_budget_weighted_consumption_minutes_total[1h])

# LEVEL_3 상황에서의 소진 비율
sum(selfhealing_error_budget_weighted_consumption_minutes_total{multiplier_range="5x"})
  /
sum(selfhealing_error_budget_weighted_consumption_minutes_total)
```

### 6.3 알림 템플릿

```
⚠️ Crisis Multiplier Activated

Current Level: LEVEL_3
Multiplier: 5.0x
Namespace: seoul

Impact:
• Error budget consumption is 5x faster
• Current budget: 45% remaining
• Projected exhaustion: 2 hours (at current rate)

Recommendation:
• Focus on resolving the emergency
• Consider pausing non-critical operations

View Dashboard: https://dashboard/error-budget
```

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 | 작성자 |
|------|------|----------|--------|
| 1.0.0 | 2026-01-21 | 초안 작성 | AI Assistant |

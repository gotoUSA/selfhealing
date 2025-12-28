# 23. Adaptive Throttling (Netflix Gradient Algorithm)

## 개요

Adaptive Throttling은 Netflix의 Adaptive Concurrency Limits 알고리즘을 기반으로 한 **동적 Rate Limiting** 시스템입니다. 기존의 고정 rate limit과 달리, 실시간 RTT(Round-Trip Time)를 분석하여 시스템 부하에 따라 자동으로 limit을 조절합니다.

## 문제점

기존 Rate Limiting의 한계:
- **고정 한도**: 시스템 상태와 무관하게 동일한 limit 적용
- **SLA 위반**: 과부하 상태에서도 limit이 유지되어 응답시간 저하
- **비효율성**: 여유 있을 때도 limit이 낮아 처리량 감소

## 해결책: Netflix Gradient Algorithm

```
gradient = (current_RTT - previous_RTT) / previous_RTT

if RTT >= SLA_CRITICAL:  limit = limit × 0.7   # 급격한 감소
elif RTT >= SLA_WARNING: limit = limit × 0.9   # 점진적 감소
elif gradient > 0.1:     limit = limit × 0.9   # RTT 증가 시 감소
elif gradient < -0.05:   limit = limit + 1     # RTT 감소 시 증가
```

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Adaptive Throttle                             │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐ │
│  │ SlidingWindow   │    │ GradientCalc    │    │ ThrottleConfig  │ │
│  │ Throttle        │◄───│ ulator          │◄───│                 │ │
│  └─────────────────┘    └─────────────────┘    └─────────────────┘ │
│          │                       ▲                                   │
│          │                       │                                   │
│          ▼                       │                                   │
│  ┌─────────────────┐    ┌─────────────────┐                         │
│  │ check(ident)    │    │ record_response │                         │
│  │ → allowed/denied│    │ (rtt_ms)        │                         │
│  └─────────────────┘    └─────────────────┘                         │
└─────────────────────────────────────────────────────────────────────┘
```

## 모듈 구조

```
packages/selfhealing-python/src/selfhealing/services/throttle/
├── __init__.py          # 모듈 exports
├── config.py            # ThrottleConfig, ThrottleResult
├── base.py              # BaseThrottle, SlidingWindowThrottle
└── adaptive.py          # AdaptiveThrottle, GradientCalculator
```

## 설정

### ThrottleConfig

```python
from dataclasses import dataclass

@dataclass
class ThrottleConfig:
    """Adaptive Throttle 설정."""
    
    # 한도 설정
    initial_limit: int = 100      # 초기 한도
    min_limit: int = 10           # 최소 한도 (안전 마진)
    max_limit: int = 500          # 최대 한도
    
    # 윈도우 설정
    window_seconds: int = 60      # 슬라이딩 윈도우 크기
    
    # SLA 임계값 (ms)
    sla_warning_ms: float = 200   # 경고 임계값
    sla_critical_ms: float = 500  # 위험 임계값
    
    # 조절 비율
    decrease_ratio: float = 0.9   # 한도 감소 비율 (×0.9 = -10%)
    increase_step: int = 1        # 한도 증가 스텝
    
    # Gradient 설정
    smoothing_factor: float = 0.2 # Exponential smoothing (EWMA)
```

## 사용법

### 기본 사용

```python
from selfhealing.services.throttle import get_adaptive_throttle

# 싱글톤 인스턴스 획득
throttle = get_adaptive_throttle()

# 요청 전: 허용 여부 확인
result = throttle.check(client_id="user_123")

if not result.allowed:
    return HttpResponse(status=429, headers={
        "Retry-After": str(int(result.reset_at - time.time())),
        "X-RateLimit-Limit": str(result.limit),
        "X-RateLimit-Remaining": str(result.remaining),
    })

# 요청 처리...
start = time.time()
response = process_request()
rtt_ms = (time.time() - start) * 1000

# 응답 후: RTT 기록
throttle.record_response(rtt_ms)
```

### Django REST Framework 통합

```python
# settings.py
REST_FRAMEWORK = {
    'DEFAULT_THROTTLE_CLASSES': [
        'selfhealing.api.django.throttle_adapter.AdaptiveDRFThrottle',
    ],
}
```

```python
# throttle_adapter.py
from rest_framework.throttling import BaseThrottle
from selfhealing.services.throttle import get_adaptive_throttle

class AdaptiveDRFThrottle(BaseThrottle):
    def allow_request(self, request, view):
        throttle = get_adaptive_throttle()
        ident = self.get_ident(request)
        result = throttle.check(ident)
        return result.allowed
    
    def get_ident(self, request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        return xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR')
```

### 커스텀 설정

```python
from selfhealing.services.throttle import AdaptiveThrottle, ThrottleConfig

config = ThrottleConfig(
    initial_limit=200,
    min_limit=20,
    max_limit=1000,
    sla_warning_ms=150,
    sla_critical_ms=300,
)

throttle = AdaptiveThrottle(config)
```

## Gradient 계산

### GradientCalculator

```python
class GradientCalculator:
    """Exponential Weighted Moving Average 기반 RTT Gradient 계산."""
    
    def __init__(self, smoothing_factor: float = 0.2):
        self.smoothing_factor = smoothing_factor
        self._smoothed_rtt: Optional[float] = None
        self._previous_rtt: Optional[float] = None
    
    def record(self, rtt_ms: float) -> float:
        """RTT 기록 및 gradient 반환."""
        if self._smoothed_rtt is None:
            self._smoothed_rtt = rtt_ms
            return 0.0
        
        self._previous_rtt = self._smoothed_rtt
        self._smoothed_rtt = (
            self.smoothing_factor * rtt_ms + 
            (1 - self.smoothing_factor) * self._smoothed_rtt
        )
        
        # Gradient: (current - previous) / previous
        if self._previous_rtt > 0:
            return (self._smoothed_rtt - self._previous_rtt) / self._previous_rtt
        return 0.0
```

## Limit 조절 로직

```python
def _adjust_limit(self, rtt_ms: float, gradient: float) -> None:
    """RTT와 gradient 기반으로 limit 조절."""
    
    # 1. SLA Critical 초과 → 급격한 감소
    if rtt_ms >= self.config.sla_critical_ms:
        new_limit = int(self._current_limit * 0.7)  # -30%
        logger.warning(f"[Throttle] CRITICAL RTT={rtt_ms}ms, limit: {self._current_limit} → {new_limit}")
    
    # 2. SLA Warning 초과 → 점진적 감소
    elif rtt_ms >= self.config.sla_warning_ms:
        new_limit = int(self._current_limit * self.config.decrease_ratio)  # -10%
    
    # 3. Gradient 양수 (RTT 증가) → 감소
    elif gradient > 0.1:
        new_limit = int(self._current_limit * self.config.decrease_ratio)
    
    # 4. Gradient 음수 (RTT 감소) → 증가
    elif gradient < -0.05:
        new_limit = self._current_limit + self.config.increase_step
    
    else:
        return  # 유지
    
    # 범위 제한
    self._current_limit = max(
        self.config.min_limit,
        min(self.config.max_limit, new_limit)
    )
```

## 통계 및 모니터링

### 통계 조회

```python
throttle = get_adaptive_throttle()
stats = throttle.get_stats()

# {
#     "current_limit": 85,
#     "total_requests": 10000,
#     "allowed": 9500,
#     "denied": 500,
#     "avg_rtt_ms": 150.5,
#     "gradient": 0.05,
#     "limit_adjustments": 50
# }
```

### Prometheus 메트릭

```python
from prometheus_client import Gauge, Counter

throttle_limit = Gauge('selfhealing_throttle_limit', 'Current throttle limit')
throttle_rtt = Gauge('selfhealing_throttle_rtt_ms', 'Average RTT in ms')
throttle_denied = Counter('selfhealing_throttle_denied_total', 'Total denied requests')
```

## API Endpoints

| Method | Endpoint | 설명 |
|--------|----------|------|
| GET | `/api/self-healing/throttle/status/` | 현재 상태 조회 |
| GET | `/api/self-healing/throttle/stats/` | 통계 조회 |
| POST | `/api/self-healing/throttle/check/` | 요청 허용 확인 |
| POST | `/api/self-healing/throttle/record/` | RTT 기록 |
| GET | `/api/self-healing/throttle/config/` | 설정 조회 |
| PUT | `/api/self-healing/throttle/config/` | 설정 변경 |
| POST | `/api/self-healing/throttle/reset/` | 상태 리셋 |

## 테스트

### 단위 테스트

```python
def test_limit_decreases_under_high_load():
    """고부하 시 limit 감소 검증."""
    throttle = AdaptiveThrottle(ThrottleConfig())
    initial_limit = throttle.current_limit
    
    # 고부하 시뮬레이션 (RTT > SLA_WARNING)
    for _ in range(10):
        throttle.check("test")
        throttle.record_response(rtt_ms=300)
    
    assert throttle.current_limit < initial_limit
```

### Hell Mode 테스트

```python
@task(3)
def test_adaptive_throttling(self):
    """Netflix Gradient Adaptive Throttling 테스트."""
    throttle = get_adaptive_throttle()
    
    # Phase 1: Normal load (50-100ms)
    # Phase 2: High load (200-400ms)
    # Phase 3: Critical load (500-800ms)
    # Phase 4: Recovery (50-100ms)
```

## 참고 자료

- [Netflix Adaptive Concurrency Limits](https://netflixtechblog.medium.com/performance-under-load-3e6fa9a60581)
- [TCP Vegas Algorithm](https://en.wikipedia.org/wiki/TCP_Vegas)
- [Gradient Descent for Rate Limiting](https://blog.cloudflare.com/counting-things-a-lot-of-different-things/)

---

**작성일**: 2024-12-28  
**작성자**: Self-Healing System Team

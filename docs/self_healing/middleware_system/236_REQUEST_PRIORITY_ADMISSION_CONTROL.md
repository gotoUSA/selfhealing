# 236. Request Priority / Admission Control 설계 문서

작성일: 2026-02-17
범위: `packages/selfhealing-python/src/selfhealing`
근거 기준: 코드 본문(추측 없음)
업계 참조: Envoy priority routing, gRPC admission control

---

## 1) 목적

현재 셀프힐링 시스템에 **요청 수준 우선순위 기반 트래픽 제어(Request Priority / Admission Control)**가 없다.
이 문서는 현재 시스템이 갖는 3가지 단점을 코드 근거로 확정하고,
기존 컴포넌트를 최대한 재사용하여 이를 완전히 커버하기 위한 3가지 작업을 정의한다.

---

## 2) 현재 시스템 단점 — 코드 근거

### 단점 1. 과부하 시 모든 요청이 동등하게 거부됨

`RateController.should_process()`는 priority 파라미터를 받지 않는다.
Token Bucket이 단일 인스턴스이므로 모든 요청이 동일한 확률로 거부된다.

**근거:**
- `scaling/rate_controller.py` — `should_process()` 시그니처:
  ```python
  def should_process(self) -> bool:  # priority 파라미터 없음
  ```
- `scaling/rate_controller.py` — 단일 Token Bucket:
  ```python
  self._token_bucket = TokenBucket(self._current_rate)
  ```
- `scaling/rate_controller.py` — 전략 분기(REJECT/THROTTLE)에 priority 조건 없음:
  ```python
  if strategy == BackpressureStrategy.REJECT:
      with self._lock:
          self._dropped_count += 1
      return False
  ```

`TieringMiddleware`가 tier별 차등 거부 로직을 갖고 있지만, Emergency Mode에서만 작동한다.

**근거:**
- `api/django/tiering/middleware.py`:
  ```python
  if not manager.is_active():
      return self.get_response(request)  # Emergency 아니면 그냥 통과
  if current_level == EmergencyLevel.NORMAL:
      return self.get_response(request)  # NORMAL이면 그냥 통과
  ```

→ 일반 과부하(큐 크기 증가, Backpressure 레벨 상승)에서는 tier 기반 차등 거부가 작동하지 않는다.

---

### 단점 2. 동일 서비스 내 엔드포인트별 차등 제어 불가

`LoadSheddingMiddleware.process()`와 `LoadSheddingManager.should_allow_request()`는
`service_id` 단위로만 동작한다. 같은 서비스 내 엔드포인트 간 차등 제어가 불가능하다.

**근거:**
- `services/circuit_breaker/load_shedding/shedding_middleware.py`:
  ```python
  def process(self, service_id: str) -> SheddingDecision:
      decision = self.manager.should_allow_request(service_id)
  ```
- `services/circuit_breaker/load_shedding/manager.py`:
  ```python
  def should_allow_request(self, service_id: str) -> SheddingDecision:
      allowed_percent = self.evaluate_shedding(service_id)
  ```
- `services/circuit_breaker/convenience.py`:
  ```python
  def should_allow_request(service_name: str) -> bool:
      return get_circuit_breaker_service().should_allow(service_name)
  ```

HTTP 요청에서 자동으로 엔드포인트를 분류하여 priority를 부여하는 로직이 없다.

**근거:**
- `core/request_context.py` — `RequestLifecycleContext`에 priority 필드 없음:
  ```python
  def __init__(self, tracker, request_id=None, endpoint="", method="", metadata=None):
      # priority 파라미터 없음
  ```
- `scaling/traffic_gate.py` — `should_allow(priority=0)`의 priority가 호출자에게 전가:
  ```python
  def should_allow(self, priority: int = 0, bulkhead_name: str | None = None, ...):
  ```

---

### 단점 3. RateLimit 전략이 무차별적

`RateController`의 Token Bucket이 단일 인스턴스이고, 전략 분기에서 priority 조건이 없다.
기존의 다른 컴포넌트(`TierRegistry`, `BulkheadRegistry`, `GracefulDegradation`)를 조합해도
이 문제를 우회할 수 없다.

**근거:**
- `scaling/rate_controller.py` — 전략 분기 전체에 priority 없음:
  ```python
  if strategy == BackpressureStrategy.REJECT:
      ...
      return False
  if strategy == BackpressureStrategy.THROTTLE:
      if self._token_bucket.wait_for_token(timeout=0.1):
          ...
          return True
      ...
      return False
  if strategy == BackpressureStrategy.DROP_OLDEST:
      return True
  if strategy == BackpressureStrategy.QUEUE:
      return True
  ```
- `scaling/graceful_degradation.py` — 기능 on/off만 지원, rate 차등 불가:
  ```python
  def is_enabled(self, name: str) -> bool:
      feature = self._features.get(name)
      if feature is None:
          return True
      return feature.enabled  # bool 반환, rate 제어 아님
  ```
- `audit/cascade_load_shedding.py` — Audit 이벤트 전용, HTTP 요청 rate limit 대체 불가:
  ```python
  def should_accept(self, trigger_type: str, buffer_size: int, buffer_capacity: int, ...):
  ```

---

## 3) 재사용 가능한 기존 컴포넌트

3가지 작업에서 아래 기존 컴포넌트를 재사용한다. 신규 생성이 아닌 **연결과 확장**이다.

### 3-A. TierRegistry — 엔드포인트 수준 우선순위 분류기 (이미 존재)

경로 → tier 매핑을 exact/wildcard/regex로 지원한다.

**근거:**
- `api/django/tiering/models.py`:
  ```python
  @dataclass
  class TierDefinition:
      id: str
      name: str
      multiplier: float  # 0.0 ~ 1.0
      priority: int = 0
  ```
- `api/django/tiering/defaults.py` — 3개 tier 사전 정의:
  ```python
  TierDefinition(id="critical", name="Mission Critical", multiplier=0.5, priority=100)
  TierDefinition(id="standard", name="Operational", multiplier=0.1, priority=50)
  TierDefinition(id="non_essential", name="Non-Essential", multiplier=0.0, priority=10)
  ```
- `api/django/tiering/defaults.py` — 경로별 매핑 사전 정의:
  ```python
  TierMapping(pattern="/api/self-healing/control/", tier_id="critical", priority=100)
  TierMapping(pattern="/api/self-healing/config/*", tier_id="standard", priority=50)
  TierMapping(pattern="/api/self-healing/dashboard/*", tier_id="non_essential", priority=10)
  ```
- `api/django/tiering/registry.py` — resolve 메서드:
  ```python
  class TierRegistry:
      def resolve_tier_with_fallback(self, path, client_ip, user_id) -> TierResult:
  ```

### 3-B. BulkheadRegistry — 커스텀 도메인 격벽 자유 생성 (이미 존재)

`get_or_create()`로 임의의 도메인별 격벽을 동적 생성할 수 있다.

**근거:**
- `resilience/bulkhead/registry.py`:
  ```python
  def get_or_create(self, name: str, max_concurrent: int | None = None,
                    bulkhead_type: str = "semaphore") -> Bulkhead:
  ```
- `resilience/bulkhead/registry.py` — DB alias/캐시별 세분화 격벽:
  ```python
  def get_for_database(self, alias: str = "default") -> Bulkhead:
      key = f"database:{alias}"
      return self.get_or_create(name=key, ...)
  ```

### 3-C. ServiceConfig — 4단계 criticality + shed_priority (이미 존재)

서비스 구성에 `criticality`, `shed_priority`, `min_traffic_percentage`가 있다.

**근거:**
- `services/circuit_breaker/models.py`:
  ```python
  @dataclass
  class ServiceConfig:
      service_id: str
      criticality: str  # "critical" | "high" | "medium" | "low"
      shed_priority: int = 0   # 높을수록 먼저 차단, 0=절대 차단 안 함
      min_traffic_percentage: float = 0.0  # 최소 보장 트래픽
  ```

### 3-D. EMERGENCY_LEVEL_RULES — tier별 배율 규칙 (이미 존재)

Emergency Level별로 tier에 트래픽 배율을 차등 적용하는 규칙이 존재한다.

**근거:**
- `services/emergency_mode/enums.py`:
  ```python
  EMERGENCY_LEVEL_RULES = {
      EmergencyLevel.NORMAL:  {"critical": 1.0, "standard": 1.0, "non_essential": 1.0},
      EmergencyLevel.LEVEL_1: {"critical": 1.0, "standard": 1.0, "non_essential": 0.0},
      EmergencyLevel.LEVEL_2: {"critical": 1.0, "standard": 0.1, "non_essential": 0.0},
      EmergencyLevel.LEVEL_3: {"critical": 0.5, "standard": 0.0, "non_essential": 0.0},
  }
  ```

### 3-E. TrafficGate — priority 파라미터가 이미 존재 (연결만 부재)

`should_allow(priority=n)` 시그니처가 이미 있다. 호출 측에서 자동 분류만 없을 뿐이다.

**근거:**
- `scaling/traffic_gate.py`:
  ```python
  def should_allow(self, priority: int = 0, bulkhead_name: str | None = None, ...):
  ```

### 3-F. TokenBucket — 범용 rate limiter (다중 인스턴스 생성 가능)

`TokenBucket` 클래스 자체는 범용이므로, priority별 인스턴스를 생성할 수 있다.

**근거:**
- `scaling/rate_controller.py`:
  ```python
  class TokenBucket:
      def __init__(self, rate: float, capacity: float | None = None):
      def consume(self, tokens: int = 1) -> bool:
      def set_rate(self, rate: float) -> None:
  ```

---

## 4) 작업 정의

### 작업 1. TieringMiddleware의 Backpressure 트리거 확장

**목표:** 단점 1 해결 — 일반 과부하에서도 tier 기반 차등 거부 활성화

**현재 상태:**
- `TieringMiddleware.__call__()`은 `manager.is_active()`가 True일 때만 작동 (Emergency 전용)
- 근거: `api/django/tiering/middleware.py`

**변경 내용:**

1. `TieringMiddleware.__call__()`에 Backpressure 레벨 확인 분기 추가:
   - `RateController.get_state().level`이 `BackpressureLevel.NONE`이 아닌 경우에도 tier 기반 차등 거부를 수행
   - Emergency Mode 로직은 기존대로 **유지** (제거 아님)

2. Backpressure 레벨별 tier 배율 규칙 추가 (EMERGENCY_LEVEL_RULES 패턴 재사용):
   ```python
   BACKPRESSURE_LEVEL_RULES: dict[BackpressureLevel, dict[str, float]] = {
       BackpressureLevel.NONE:     {"critical": 1.0, "standard": 1.0, "non_essential": 1.0},
       BackpressureLevel.LOW:      {"critical": 1.0, "standard": 1.0, "non_essential": 0.5},
       BackpressureLevel.MEDIUM:   {"critical": 1.0, "standard": 0.8, "non_essential": 0.2},
       BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.0},
       BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.0},
   }
   ```

3. 기존 `_should_allow_request(multiplier)` 메서드를 그대로 재사용:
   ```python
   # 이미 존재하는 확률적 거부 로직
   def _should_allow_request(self, multiplier: float) -> bool:
       if multiplier >= 1.0: return True
       if multiplier <= 0.0: return False
       return self._random.random() < multiplier
   ```

**수정 대상 파일:**
- `api/django/tiering/middleware.py` — `__call__()` 메서드에 분기 추가
- 신규 상수 파일 또는 `api/django/tiering/defaults.py`에 `BACKPRESSURE_LEVEL_RULES` 추가

**재사용 컴포넌트:**
- `TierRegistry.resolve_tier_with_fallback()` (3-A)
- `EMERGENCY_LEVEL_RULES` 패턴 (3-D)
- `RateController.get_state()` (기존)

---

### 작업 2. 요청-경로 → Priority 자동 분류 오케스트레이터

**목표:** 단점 2 해결 — HTTP 요청 경로에서 자동으로 priority를 분류하여 TrafficGate/LoadShedding에 전달

**현재 상태:**
- `TrafficGate.should_allow(priority=0)`의 priority는 호출자가 수동 지정
- `LoadSheddingMiddleware.process(service_id)`의 service_id도 호출자가 수동 지정
- HTTP 요청 경로 → priority 자동 변환 로직 없음

**변경 내용:**

1. `RequestLifecycleContext`에 `priority` 필드 추가:
   ```python
   class RequestLifecycleContext:
       def __init__(self, tracker, request_id=None, endpoint="",
                    method="", metadata=None, priority: int = 0):
           self._priority = priority
   ```
   - 수정 대상: `core/request_context.py`

2. 통합 미들웨어 신규 생성 — `AdmissionControlMiddleware`:
   - HTTP 요청 진입 시:
     a. `TierRegistry.resolve_tier_with_fallback(path)` 호출 → `TierResult.tier_id` 획득 (3-A 재사용)
     b. tier_id → priority 매핑 (critical=0, standard=50, non_essential=100)
     c. `TrafficGate.should_allow(priority=매핑값, bulkhead_name=tier_id)` 호출 (3-E 재사용)
     d. `LoadSheddingManager.should_allow_request(service_id=f"{tier_id}:{endpoint}")` 호출
   - 거부 시 503 응답 반환 (기존 `_create_load_shedding_response` 패턴 재사용)

3. `BulkheadRegistry`에 tier별 격벽 자동 등록:
   ```python
   registry.get_or_create("tier:critical", max_concurrent=100)
   registry.get_or_create("tier:standard", max_concurrent=50)
   registry.get_or_create("tier:non_essential", max_concurrent=20)
   ```
   - 3-B 재사용: `get_or_create()` 이미 동적 생성 지원

**수정 대상 파일:**
- `core/request_context.py` — priority 필드 추가
- 신규: `api/django/admission_control.py` — `AdmissionControlMiddleware`
- `resilience/bulkhead/registry.py` — tier별 기본 격벽 등록 추가 (선택)

**재사용 컴포넌트:**
- `TierRegistry` (3-A)
- `BulkheadRegistry.get_or_create()` (3-B)
- `TrafficGate.should_allow(priority, bulkhead_name)` (3-E)
- `LoadSheddingManager.should_allow_request()` (기존)

---

### 작업 3. RateController Priority-Aware 확장

**목표:** 단점 3 해결 — priority별 차등 rate limiting

**현재 상태:**
- `TokenBucket` 단일 인스턴스, `should_process()`에 priority 없음
- 전략 분기(REJECT/THROTTLE)에 priority 조건 없음

**변경 내용:**

1. `RateController`에 priority별 Token Bucket 추가:
   ```python
   class RateController:
       def __init__(self, ...):
           # 기존 단일 버킷 유지 (전체 rate limit)
           self._token_bucket = TokenBucket(self._current_rate)
           # priority별 예약 버킷 추가
           self._priority_buckets: dict[str, TokenBucket] = {
               "critical":      TokenBucket(rate=self._current_rate * 0.4),  # 40% 예약
               "standard":      TokenBucket(rate=self._current_rate * 0.35), # 35% 예약
               "non_essential": TokenBucket(rate=self._current_rate * 0.25), # 25% 예약
           }
   ```
   - 3-F 재사용: `TokenBucket` 클래스 그대로 인스턴스만 추가

2. `should_process()` 시그니처에 priority 추가:
   ```python
   def should_process(self, priority: str = "standard") -> bool:
       if not self._settings.backpressure_enabled:
           return True
       # 1) priority별 전용 버킷에서 시도
       bucket = self._priority_buckets.get(priority)
       if bucket and bucket.consume():
           ...
           return True
       # 2) critical은 전역 버킷에서 추가 시도 (보호)
       if priority == "critical" and self._token_bucket.consume():
           ...
           return True
       # 3) 전략별 처리 (기존 로직)
       ...
   ```

3. `_adjust_rate()` 변경 — priority별 버킷 비율도 함께 조절:
   ```python
   def _adjust_rate(self) -> None:
       ...
       with self._lock:
           self._current_rate = new_rate
           self._token_bucket.set_rate(new_rate)
           # priority별 버킷도 비율에 맞게 조절
           for tier, bucket in self._priority_buckets.items():
               bucket.set_rate(new_rate * PRIORITY_RATE_RATIOS[tier])
   ```

4. `TrafficGate._check_rate_controller()` 연동:
   - `should_allow(priority=n)` 에서 priority int를 tier 문자열로 매핑 후
     `self._rate_controller.should_process(priority=tier)` 호출

**수정 대상 파일:**
- `scaling/rate_controller.py` — `should_process()` 시그니처 변경, priority별 버킷 추가
- `scaling/traffic_gate.py` — RateController 호출 시 priority 전달

**재사용 컴포넌트:**
- `TokenBucket` 클래스 (3-F)
- `BackpressureSettings` 설정 (기존)
- `AIMD 패턴` `_adjust_rate()` (기존 로직 확장)

---

## 5) 아키텍처 — 변경 후 처리 흐름

```
HTTP Request
    │
    ▼
┌──────────────────────────────────────────────────┐
│  AdmissionControlMiddleware (작업 2 - 신규)       │
│                                                   │
│  1. TierRegistry.resolve_tier_with_fallback(path) │ ← 재사용 (3-A)
│     → tier_id (critical/standard/non_essential)   │
│                                                   │
│  2. TrafficGate.should_allow(                     │ ← 재사용 (3-E)
│         priority=tier.priority,                   │
│         bulkhead_name=f"tier:{tier_id}")           │
│     ├── Bulkhead(tier별 격리)                      │ ← 재사용 (3-B)
│     ├── CascadeLoadShedding(우선순위 필터)         │
│     └── RateController.should_process(            │
│             priority=tier_id)                     │ ← 작업 3 확장
│         ├── priority별 TokenBucket                │ ← 재사용 (3-F)
│         └── critical은 전역 버킷 fallback          │
│                                                   │
│  3. 거부 시 → 503 + Retry-After                    │
│     허용 시 → next middleware                      │
└──────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────────────────┐
│  TieringMiddleware (작업 1 - 확장)                │
│                                                   │
│  Emergency Mode 활성 시:                          │
│    → EMERGENCY_LEVEL_RULES 기반 차등 거부 (기존)   │
│                                                   │
│  Backpressure 레벨 상승 시: (신규 분기)            │
│    → BACKPRESSURE_LEVEL_RULES 기반 차등 거부       │
│    → _should_allow_request(multiplier) 재사용      │
└──────────────────────────────────────────────────┘
    │
    ▼
  Application (Django View)
```

---

## 6) 단점-작업 매핑 및 커버 판정

| 단점 | 작업 | 커버 방식 | 신규 코드 | 재사용 |
|------|------|-----------|-----------|--------|
| 1. 과부하 시 동등 거부 | 작업 1 | TieringMiddleware에 BackpressureLevel 트리거 추가 | BACKPRESSURE_LEVEL_RULES 상수, `__call__()` 분기 추가 | TierRegistry, _should_allow_request(), RateController.get_state() |
| 2. 엔드포인트별 차등 불가 | 작업 2 | AdmissionControlMiddleware가 경로→tier→priority 자동 분류 | AdmissionControlMiddleware 클래스, RequestLifecycleContext.priority 필드 | TierRegistry, BulkheadRegistry.get_or_create(), TrafficGate.should_allow() |
| 3. RateLimit 무차별 | 작업 3 | RateController에 priority별 TokenBucket 추가 | priority_buckets dict, should_process() 시그니처 변경 | TokenBucket 클래스, AIMD _adjust_rate() 로직 |

---

## 7) 성능 영향 분석

새로운 Request Priority / Admission Control 시스템을 **별도로 도입**하는 경우 대비,
기존 컴포넌트 재사용 방식의 성능 오버헤드 비교:

| 항목 | 별도 시스템 도입 | 기존 컴포넌트 재사용 (본 설계) |
|------|------------------|-------------------------------|
| 요청당 분류 비용 | 새로운 분류 엔진 + 새로운 매핑 테이블 조회 | `TierRegistry.resolve_tier_with_fallback()` 1회 호출 (이미 존재) |
| Rate Limit 조회 | 새로운 priority queue + admission controller | 기존 `TokenBucket.consume()` + priority별 추가 버킷 (동일 알고리즘) |
| 격벽 관리 | 새로운 격벽 시스템 | `BulkheadRegistry.get_or_create()` (이미 존재) |
| 메모리 | 새로운 싱글톤 + 새로운 설정 모델 | TokenBucket 인스턴스 2~3개 추가 (수십 바이트) |

→ 기존 컴포넌트 재사용이므로 **추가 성능 오버헤드가 최소화**된다.

---

## 8) 수정 대상 파일 목록

| # | 파일 | 변경 유형 | 작업 |
|---|------|-----------|------|
| 1 | `api/django/tiering/middleware.py` | 수정 | 작업 1 |
| 2 | `api/django/tiering/defaults.py` 또는 신규 상수 파일 | 추가 | 작업 1 |
| 3 | `core/request_context.py` | 수정 | 작업 2 |
| 4 | `api/django/admission_control.py` | 신규 | 작업 2 |
| 5 | `scaling/rate_controller.py` | 수정 | 작업 3 |
| 6 | `scaling/traffic_gate.py` | 수정 | 작업 3 |

---

## 9) 구현 순서

작업 간 의존성에 따른 권장 구현 순서:

1. **작업 3** (RateController priority-aware) — 다른 작업의 기반
2. **작업 2** (AdmissionControlMiddleware) — 작업 3의 priority-aware RateController 사용
3. **작업 1** (TieringMiddleware 확장) — 독립적이지만, 작업 2와 미들웨어 체인 순서 확정 후 진행

---

## 10) 참고 — 업계 패턴 대비

| 패턴 | Envoy | gRPC | 본 설계 |
|------|-------|------|---------|
| 요청 분류 | Route metadata priority | Call credentials + interceptor | TierRegistry.resolve_tier_with_fallback() |
| Admission Control | Global/local rate limit filter | Server-side interceptor | AdmissionControlMiddleware → TrafficGate |
| Priority Rate Limiting | Priority-based connection pool | Weighted max concurrent streams | Priority별 TokenBucket |
| Load Shedding | Overload manager | Graceful server shutdown | CascadeLoadShedding + LoadSheddingManager |
| Bulkhead | Circuit breaker + outlier detection | Channel isolation | BulkheadRegistry |

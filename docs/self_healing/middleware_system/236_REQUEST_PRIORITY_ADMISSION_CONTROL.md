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

---

## 11) 설계 리뷰 반영 — 코드 근거 기반 분석 및 수정 확정

리뷰일: 2026-02-17
리뷰 범위: 10개 질문(Q1~Q10)에 대한 평가 + 8개 수정 제안
원칙: 코드 근거 기반, 추측 금지

---

### 리뷰 1. RateController 알고리즘 — Watermark 방식 채택 (Q1, Q2)

**리뷰 판정: 동의 — 원안(독립 버킷)을 폐기하고 안 1(Watermark) 채택**

**리뷰 요약:**
- 원안의 독립 `TokenBucket` 3개(40%/35%/25%)는 버킷 간 토큰 공유 메커니즘이 없어 유휴 자원 낭비 발생
- critical fallback으로 전역 버킷을 추가 소비하면 전체 Rate Limit 초과 위험

**코드 근거 — 원안의 결함:**
- `scaling/rate_controller.py` — `TokenBucket`은 독립 인스턴스이며 공유 API 없음:
  ```python
  class TokenBucket:
      def __init__(self, rate: float, capacity: float | None = None):
          self._rate = rate
          self._capacity = capacity or rate
          self._tokens = self._capacity  # 독립 토큰 풀
  ```
- `TokenBucket.consume()`은 자신의 `_tokens`만 차감, 다른 버킷 참조 불가:
  ```python
  def consume(self, tokens: int = 1) -> bool:
      with self._lock:
          if self._tokens >= tokens:
              self._tokens -= tokens
              return True
          return False
  ```

**채택 이유 — 안 1(Watermark)이 현 시스템에 최적인 근거:**

1. **단일 버킷 유지**: 현재 `RateController`는 `self._token_bucket = TokenBucket(self._current_rate)` 단일 인스턴스. Watermark 방식은 이 구조를 그대로 유지하고 `should_process()` 분기만 추가하면 됨
2. **AIMD 패턴 호환**: 현재 `_adjust_rate()`는 `self._token_bucket.set_rate(new_rate)` 1회 호출로 rate를 조절. Watermark 방식은 버킷이 1개이므로 `_adjust_rate()` 변경이 불필요
3. **`BackpressureMiddleware`와의 호환**: 기존 `BackpressureMiddleware.__call__()`은 `self._controller.should_process()`를 호출하는데, 시그니처에 priority를 추가하면 기본값 `"standard"`로 하위 호환됨

**안 2(독립 버킷 보완) 비교:**

| 항목 | 안 1 (Watermark) | 안 2 (독립 버킷 보완) |
|------|-------------------|----------------------|
| 버킷 수 | 1개 (기존 유지) | 3~4개 (신규) |
| `_adjust_rate()` 변경 | 불필요 | priority별 `set_rate()` 필요 |
| 유휴 자원 낭비 | 없음 (공유 풀) | 여전히 가능 (예약 방식) |
| 전체 한도 초과 위험 | 없음 (단일 버킷) | 설계 복잡도 증가 |
| `BackpressureMiddleware` 호환 | `should_process("standard")` 기본값으로 무변경 | 동일 |
| 구현 복잡도 | 낮음 (분기 조건만 추가) | 높음 (다중 버킷 동기화) |

**확정 설계 — Watermark 방식 `should_process()` 로직:**

```python
# scaling/rate_controller.py

# priority별 토큰 비율 임계치 (Watermark)
PRIORITY_WATERMARKS: dict[str, float] = {
    "critical": 0.0,       # 토큰 0% 이상이면 허용 (항상 시도 가능)
    "standard": 0.3,       # 토큰 30% 이상일 때만 허용
    "non_essential": 0.6,  # 토큰 60% 이상일 때만 허용
}

class RateController:
    def should_process(self, priority: str = "standard") -> bool:
        if not self._settings.backpressure_enabled:
            return True

        # Watermark 확인: 현재 토큰 비율이 priority별 임계치 이상인지 체크
        watermark = PRIORITY_WATERMARKS.get(priority, 0.3)
        token_ratio = self._token_bucket.get_token_ratio()

        if token_ratio < watermark:
            with self._lock:
                self._dropped_count += 1
            return False

        # 토큰 소비 (단일 버킷)
        if self._token_bucket.consume():
            with self._lock:
                self._processed_count += 1
            return True

        # 토큰 부족 시 전략별 처리 (기존 로직 유지)
        strategy = self._settings.default_strategy
        # ... (기존 REJECT/THROTTLE/DROP_OLDEST/QUEUE 로직 그대로)
```

**TokenBucket에 추가할 메서드:**

```python
class TokenBucket:
    def get_token_ratio(self) -> float:
        """현재 토큰 잔량 비율 반환 (0.0 ~ 1.0)."""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_update
            # 충전 반영 (read-only, _last_update 갱신 안 함)
            current = min(self._capacity, self._tokens + elapsed * self._rate)
            return current / self._capacity if self._capacity > 0 else 0.0
```

**네이밍 검증:**
- `PRIORITY_WATERMARKS`: 기존 코드에 없는 이름. `audit/self_audit.py`의 `BUFFER_HIGH_WATERMARK`는 Enum 멤버이며 완전히 다른 컨텍스트
- `get_token_ratio()`: 기존 `TokenBucket`에 `get_rate()` 메서드 존재. `get_token_ratio()` 는 없음 — 충돌 없음

**수정 대상 파일 (원안 대비 변경):**

| 파일 | 원안 | 수정안 |
|------|------|--------|
| `scaling/rate_controller.py` | priority별 TokenBucket dict 추가, `should_process()` 대폭 변경 | `PRIORITY_WATERMARKS` 상수 + `get_token_ratio()` 메서드 + `should_process()` watermark 분기만 추가 |
| `scaling/traffic_gate.py` | priority int→str 매핑 + `should_process(priority=tier)` 호출 | 동일 (변경사항 유지) |

---

### 리뷰 2. 미들웨어 역할 분리 명확화 (Q3, Q4)

**리뷰 판정: 동의 — 역할 분리 원칙 확정 + Merge Strategy 추가**

**코드 근거 — 현재 중복 영역:**
- `BackpressureMiddleware`(`api/django/middleware/backpressure.py`): 이미 `RateController.should_process()`를 호출하여 503 반환
  ```python
  if not self._controller.should_process():
      return self._create_overload_response()
  ```
- `TieringMiddleware`(`api/django/tiering/middleware.py`): Emergency Mode에서만 tier별 확률적 거부
  ```python
  if not manager.is_active():
      return self.get_response(request)
  ```
- 신규 `AdmissionControlMiddleware`: `TrafficGate.should_allow(priority, bulkhead_name)` 호출

**중복 문제 — `BackpressureMiddleware` vs `AdmissionControlMiddleware`:**
- `BackpressureMiddleware`는 `should_process()` (priority 없음)를 호출
- `AdmissionControlMiddleware`는 `TrafficGate.should_allow()` → `should_process(priority)` 호출
- 둘 다 `RateController`를 거치므로, 두 미들웨어가 동시에 활성화되면 rate check가 이중으로 발생

**확정 정책:**

1. **`AdmissionControlMiddleware`가 `BackpressureMiddleware`를 대체**:
   - `AdmissionControlMiddleware`가 `TrafficGate` 파이프라인(Bulkhead → LoadShedding → RateController)을 모두 수행하므로, `BackpressureMiddleware`의 역할을 완전히 포함
   - `BackpressureMiddleware`는 제거하지 않되, `AdmissionControlMiddleware`를 사용하는 경우 비활성화 권고

2. **`TieringMiddleware`는 Emergency Mode 전용으로 유지** (Backpressure 트리거 추가 취소):
   - 원안 작업 1에서 `TieringMiddleware`에 Backpressure 분기를 추가하려 했으나, `AdmissionControlMiddleware`가 이미 Backpressure 기반 priority 차등 제어를 수행
   - `TieringMiddleware`에 Backpressure 분기까지 추가하면 동일 요청에 tier 기반 거부가 2회 발생 (AdmissionControl + Tiering)
   - **결정: 작업 1을 "Backpressure 트리거 확장"에서 "Merge Strategy 추가"로 변경**

3. **Merge Strategy — Most Restrictive Wins (min 적용):**
   - Emergency Mode와 Backpressure가 동시 활성화된 경우, `TieringMiddleware`에서 두 multiplier 중 더 낮은 값을 적용
   - 코드 근거: `EMERGENCY_LEVEL_RULES`(`services/emergency_mode/enums.py`)와 `BACKPRESSURE_LEVEL_RULES` 모두 `dict[Level, dict[str, float]]` 형태이므로 `min()` 적용이 자연스러움

**확정 설계 — 작업 1 수정안:**

```python
# api/django/tiering/middleware.py — __call__() 변경

def __call__(self, request):
    if not self._enabled:
        return self.get_response(request)

    try:
        from selfhealing.services.emergency_mode import get_emergency_manager
        from selfhealing.services.emergency_mode.enums import (
            EMERGENCY_LEVEL_RULES, EmergencyLevel,
        )
        from selfhealing.scaling.rate_controller import get_rate_controller
        from selfhealing.scaling.config import BackpressureLevel
        from selfhealing.api.django.tiering.defaults import BACKPRESSURE_TIER_RULES

        manager = get_emergency_manager()
        controller = get_rate_controller()

        emergency_active = manager.is_active()
        emergency_level = manager.get_current_level() if emergency_active else EmergencyLevel.NORMAL
        bp_level = controller.get_state().level

        # 둘 다 정상이면 통과
        if emergency_level == EmergencyLevel.NORMAL and bp_level == BackpressureLevel.NONE:
            return self.get_response(request)

        path = request.path
        client_ip = self._get_client_ip(request)
        user_id = self._get_user_id(request)

        tier_result = self._registry.resolve_tier_with_fallback(
            path=path, client_ip=client_ip,
            user_id=str(user_id) if user_id else None,
        )

        # Most Restrictive Wins: 두 규칙 중 더 낮은 multiplier 적용
        emergency_multiplier = EMERGENCY_LEVEL_RULES.get(
            emergency_level, {}
        ).get(tier_result.tier_id, 1.0)

        backpressure_multiplier = BACKPRESSURE_TIER_RULES.get(
            bp_level, {}
        ).get(tier_result.tier_id, 1.0)

        final_multiplier = min(emergency_multiplier, backpressure_multiplier)

        if not self._should_allow_request(final_multiplier):
            return self._create_load_shedding_response(
                request=request, tier_id=tier_result.tier_id,
                multiplier=final_multiplier, emergency_level=emergency_level,
            )

        return self.get_response(request)

    except Exception as e:
        logger.error(f"[TieringMiddleware] Error: {e}, allowing request")
        return self.get_response(request)
```

**`BACKPRESSURE_TIER_RULES` 상수 (신규):**

```python
# api/django/tiering/defaults.py 에 추가

from selfhealing.scaling.config import BackpressureLevel

BACKPRESSURE_TIER_RULES: dict[BackpressureLevel, dict[str, float]] = {
    BackpressureLevel.NONE:     {"critical": 1.0, "standard": 1.0, "non_essential": 1.0},
    BackpressureLevel.LOW:      {"critical": 1.0, "standard": 1.0, "non_essential": 0.5},
    BackpressureLevel.MEDIUM:   {"critical": 1.0, "standard": 0.8, "non_essential": 0.2},
    BackpressureLevel.HIGH:     {"critical": 1.0, "standard": 0.5, "non_essential": 0.0},
    BackpressureLevel.CRITICAL: {"critical": 0.8, "standard": 0.1, "non_essential": 0.0},
}
```

**네이밍 변경 근거:**
- 원안: `BACKPRESSURE_LEVEL_RULES` → 수정안: `BACKPRESSURE_TIER_RULES`
- 이유: `EMERGENCY_LEVEL_RULES`는 Emergency Level이 주어(레벨에 따라), `BACKPRESSURE_TIER_RULES`도 Backpressure Level에 따른 Tier 규칙이라는 의미. 기존 `LEVEL_RATE_MULTIPLIERS`(`settings/backpressure.py`)는 전역 rate 배율이므로 혼동 방지를 위해 `_TIER_`를 포함

**미들웨어 순서 확정:**

```python
# Django settings.py MIDDLEWARE
MIDDLEWARE = [
    # ... (Django 기본 미들웨어)
    'selfhealing.api.django.admission_control.AdmissionControlMiddleware',  # 유입 제어 (항상)
    'selfhealing.api.django.tiering.TieringMiddleware',                     # 비상 대응 (Emergency/BP 시)
    # 'selfhealing.api.django.middleware.backpressure.BackpressureMiddleware',  # 비활성화 (AdmissionControl이 대체)
    # ... (나머지 미들웨어)
]
```

---

### 리뷰 3. Path Matching 성능 — 캐싱 추가 (Q5)

**리뷰 판정: 동의 — dict 기반 캐시 추가**

**코드 근거:**
- `api/django/tiering/registry.py` — `get_tier_for_path()`는 매 호출마다 `self._mappings` 리스트를 순회:
  ```python
  def get_tier_for_path(self, path: str) -> TierDefinition | None:
      with self._data_lock:
          for mapping in self._mappings:
              if mapping.matches(path):
                  return self._tiers.get(mapping.tier_id)
      return None
  ```
- 현재 기본 매핑 수: 11개 (`defaults.py`)

**`functools.lru_cache` vs `dict` 캐시 선택:**

| 항목 | `lru_cache` | `dict` 캐시 |
|------|-------------|-------------|
| 무효화 | `cache_clear()` 호출 필요 | `dict.clear()` |
| `set_mappings()` 연동 | `_save_previous_config()` 후 수동 clear 필요 | 동일 |
| thread-safety | `lru_cache` 자체는 thread-safe | `self._data_lock` 재사용 |
| 인스턴스 메서드 호환 | 싱글톤이므로 가능하지만, `self` 파라미터 때문에 데코레이터에 별도 처리 필요 | 자연스러움 |

→ **`dict` 캐시 채택**: `TierRegistry`가 이미 `self._data_lock`을 사용하며, `set_mappings()` / `import_config()` / `reset_to_defaults()` 시 캐시 무효화를 `self._data_lock` 안에서 처리할 수 있음

**확정 설계:**

```python
# api/django/tiering/registry.py

class TierRegistry:
    def _init(self):
        # ... 기존 코드
        self._path_tier_cache: dict[str, TierDefinition | None] = {}
        self._PATH_CACHE_MAX_SIZE = 1024

    def get_tier_for_path(self, path: str) -> TierDefinition | None:
        with self._data_lock:
            if path in self._path_tier_cache:
                return self._path_tier_cache[path]

            result = None
            for mapping in self._mappings:
                if mapping.matches(path):
                    result = self._tiers.get(mapping.tier_id)
                    break

            if len(self._path_tier_cache) < self._PATH_CACHE_MAX_SIZE:
                self._path_tier_cache[path] = result
            return result

    def _invalidate_path_cache(self) -> None:
        """매핑 변경 시 캐시 무효화."""
        self._path_tier_cache.clear()
```

- `_invalidate_path_cache()` 호출 위치: `set_mappings()`, `set_tiers()`, `import_config()`, `reset_to_defaults()`, `rollback_to_previous()` 내 (모두 `self._data_lock` 안에서 실행)

**네이밍 검증:**
- `_path_tier_cache`: 기존 코드에 없음, 충돌 없음
- `_PATH_CACHE_MAX_SIZE`: 기존 코드에 없음, 충돌 없음
- `_invalidate_path_cache()`: 기존 코드에 없음, 충돌 없음

---

### 리뷰 4. Unmatched Path 기본값 (Q6)

**리뷰 판정: 동의 — 현행 유지 (Fail-Closed)**

**코드 근거:**
- `api/django/tiering/registry.py` — `_static_or_default_tier()`:
  ```python
  return TierResult(
      tier_id="non_essential",
      multiplier=0.0,
      is_fallback=True,
      fallback_reason=reason,
  )
  ```
- 미등록 경로 = `non_essential` (Fail-Closed)는 보안/안정성 관점에서 합리적
- `STATIC_CRITICAL_PATHS` / `STATIC_CRITICAL_PREFIXES`로 핵심 경로는 보호됨

**추가 조치 없음.**

---

### 리뷰 5. Context 전파 — Django request 객체 활용 (Q7)

**리뷰 판정: 동의 — `request` 객체에 속성 주입 방식 채택**

**`contextvars` vs `request` 객체 주입 선택:**

| 항목 | `contextvars.ContextVar` | `request` 객체 주입 |
|------|--------------------------|---------------------|
| Django 정석 여부 | 비표준 (Django 미들웨어는 request 객체 흐름) | 정석 (`request.META` 또는 속성 주입이 Django 관행) |
| 비동기 환경 | `ContextVar`는 asyncio에서도 작동 | `request` 객체도 async view에서 유효 |
| 기존 패턴 일치 | 프로젝트 내 `contextvars` 사용 패턴 없음 | 기존 `request.user`, `request.META` 패턴과 일치 |
| 추가 import | `contextvars` 모듈 필요 | 불필요 |
| 미들웨어 체인 전파 | 직접 코루틴 전파 세팅 필요 | 자동 (request가 체인을 타고 흐름) |

→ **`request` 객체 주입 채택**: Django 미들웨어 체인에서 `request` 객체가 자연스럽게 전파되므로 가장 간단하고 관행에 맞음

**확정 설계:**

```python
# api/django/admission_control.py — AdmissionControlMiddleware

class AdmissionControlMiddleware:
    def __call__(self, request):
        # ... tier 분류 후
        request._selfhealing_tier_id = tier_result.tier_id         # str
        request._selfhealing_tier_priority = tier_result.priority   # int (TierDefinition.priority)
        # ... TrafficGate 호출 등
```

- `_selfhealing_` prefix 사용: Django 관행상 비공개 속성에 `_` prefix
- 후속 미들웨어/뷰에서 `getattr(request, '_selfhealing_tier_id', 'standard')`로 참조

**`RequestLifecycleContext.priority` 필드 추가 취소:**
- `RequestLifecycleContext`는 Graceful Shutdown용 request tracking 컨텍스트이며 (`core/shutdown_coordinator.py`의 `RequestTracker` 연동), 미들웨어 체인 전파 용도가 아님
- priority 전파는 `request` 객체로 충분하므로 `RequestLifecycleContext` 수정 불필요

**네이밍 검증:**
- `_selfhealing_tier_id`, `_selfhealing_tier_priority`: 기존 코드베이스에 없음, 충돌 없음

---

### 리뷰 6. Bulkhead 설정 — 하드코딩 제거 (Q8)

**리뷰 판정: 동의 — `AdmissionControlSettings` 신규 생성**

**코드 근거 — 기존 설정 패턴:**
- 모든 설정 클래스가 `pydantic_settings.BaseSettings`를 상속하고 `env_prefix`로 환경변수 바인딩:
  ```python
  # settings/bulkhead.py
  class BulkheadSettings(BaseSettings):
      model_config = SettingsConfigDict(env_prefix="SELFHEALING_BULKHEAD_", ...)
      database_max_concurrent: int = Field(default=10, ...)
  ```
- `BulkheadRegistry.get_or_create()`에서 `max_concurrent=None`이면 `self._settings.default_max_concurrent` (기본값 10) 사용:
  ```python
  concurrent = max_concurrent or self._settings.default_max_concurrent
  ```

**확정 설계:**

```python
# settings/admission_control.py (신규)

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class AdmissionControlSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_ADMISSION_CONTROL_",
        env_file=".env", env_file_encoding="utf-8",
        extra="ignore", validate_default=True,
    )

    enabled: bool = Field(default=True, description="Admission Control 활성화 여부")

    # Tier별 Bulkhead max_concurrent
    tier_critical_max_concurrent: int = Field(
        default=100, ge=1, le=1000,
        description="critical tier 격벽 최대 동시 실행 수",
    )
    tier_standard_max_concurrent: int = Field(
        default=50, ge=1, le=500,
        description="standard tier 격벽 최대 동시 실행 수",
    )
    tier_non_essential_max_concurrent: int = Field(
        default=20, ge=1, le=200,
        description="non_essential tier 격벽 최대 동시 실행 수",
    )
```

**네이밍 검증:**
- `AdmissionControlSettings`: 기존 `settings/` 디렉토리의 모든 Settings 클래스와 충돌 없음 (검색 결과: `ApiRateLimitSettings`, `BackpressureSettings`, `BulkheadSettings` 등 존재하나 `AdmissionControl` 접두사 없음)
- 환경변수 prefix `SELFHEALING_ADMISSION_CONTROL_`: 기존 prefix와 충돌 없음

---

### 리뷰 7. 관측성 — 로그 세분화 (Q9)

**리뷰 판정: 동의 — `TrafficGate` 로그 메시지 세분화**

**코드 근거 — 현재 로깅 구조:**
- `TrafficDecision`이 이미 `reason`(str)과 `gate`(str) 필드를 가짐:
  ```python
  @dataclass
  class TrafficDecision:
      allowed: bool
      reason: str    # "Rate limit exceeded at level=..."
      gate: str      # "RateController", "Bulkhead", "CascadeLoadShedding"
  ```
- `RateController.should_process()`는 `bool`만 반환하므로, `TrafficGate`에서 세부 이유를 알 수 없음

**`should_process()` 반환 타입 변경 — 부담 수준 분석:**
- `should_process()`를 호출하는 곳:
  - `TrafficGate.should_allow()` (`scaling/traffic_gate.py`)
  - `BackpressureMiddleware.__call__()` (`api/django/middleware/backpressure.py`)
  - `AsyncBackpressureMiddleware.__call__()` (동일 파일)
- 3곳만 호출하므로 반환 타입 변경의 영향 범위가 작음

**그러나** `bool` 반환이 현재 시스템의 일관된 패턴이므로 (단순함 유지), 리뷰 조언대로 **기존 시그니처 유지 + 로그 세분화**로 진행:

```python
# scaling/rate_controller.py — should_process() 내부

def should_process(self, priority: str = "standard") -> bool:
    if not self._settings.backpressure_enabled:
        return True

    watermark = PRIORITY_WATERMARKS.get(priority, 0.3)
    token_ratio = self._token_bucket.get_token_ratio()

    if token_ratio < watermark:
        with self._lock:
            self._dropped_count += 1
        logger.info(
            f"[RateController] Rejected: priority={priority}, "
            f"reason=watermark_exceeded, "
            f"token_ratio={token_ratio:.2f}, watermark={watermark}"
        )
        return False

    if self._token_bucket.consume():
        with self._lock:
            self._processed_count += 1
        return True

    # 토큰 부족 시
    logger.info(
        f"[RateController] Rejected: priority={priority}, "
        f"reason=token_exhausted, "
        f"token_ratio={token_ratio:.2f}"
    )
    # ... 전략별 처리
```

**`TrafficGate` 로그 보완:**

```python
# scaling/traffic_gate.py — should_allow() 내 RateController 거부 시

if not self._rate_controller.should_process(priority=tier_str):
    return TrafficDecision(
        allowed=False,
        reason=f"Rate limit exceeded: priority={tier_str}, level={current_level.value}",
        level=current_level,
        gate="RateController",
        metadata={**(metadata or {}), "priority": tier_str},
    )
```

---

### 리뷰 8. shed_priority 무시 (Q10)

**리뷰 판정: 동의 — 이번 구현 범위에서 무시**

**코드 근거:**
- `ServiceConfig.shed_priority`: `services/circuit_breaker/models.py`에 정의, **서비스 단위** Load Shedding 순서 결정
  ```python
  shed_priority: int = 0   # 높을수록 먼저 차단, 0=절대 차단 안 함
  ```
- `TierDefinition.priority`: `api/django/tiering/models.py`에 정의, **경로 매핑 우선순위**
  ```python
  priority: int = 0  # higher = more important
  ```
- 두 속성은 완전히 다른 패키지에 속하며 (`services/circuit_breaker/` vs `api/django/tiering/`), 상호 import 없음

**추가 조치 없음.**

---

## 12) 수정 대상 파일 목록 (리뷰 반영 후)

| # | 파일 | 변경 유형 | 작업 | 리뷰 반영 사항 |
|---|------|-----------|------|----------------|
| 1 | `scaling/rate_controller.py` | 수정 | 작업 3 | **Watermark 방식으로 변경** — `PRIORITY_WATERMARKS` 상수, `get_token_ratio()`, `should_process(priority)` |
| 2 | `scaling/traffic_gate.py` | 수정 | 작업 3 | priority int→str 매핑 + `should_process(priority=tier)` 호출 + 로그 세분화 |
| 3 | `api/django/tiering/middleware.py` | 수정 | 작업 1 | **Merge Strategy 추가** — `min(emergency_multiplier, backpressure_multiplier)` |
| 4 | `api/django/tiering/defaults.py` | 추가 | 작업 1 | `BACKPRESSURE_TIER_RULES` 상수 추가 |
| 5 | `api/django/admission_control.py` | 신규 | 작업 2 | `AdmissionControlMiddleware` — request 객체에 tier 주입 |
| 6 | `settings/admission_control.py` | 신규 | 작업 2 | `AdmissionControlSettings` — tier별 bulkhead 설정 |
| 7 | `api/django/tiering/registry.py` | 수정 | 작업 2 | `_path_tier_cache` dict 캐시 + `_invalidate_path_cache()` |
| 8 | ~~`core/request_context.py`~~ | ~~수정~~ | ~~작업 2~~ | **삭제** — `RequestLifecycleContext` 수정 불필요 (request 객체로 전파) |

---

## 13) 구현 순서 (리뷰 반영 후)

1. **작업 3** — `scaling/rate_controller.py` Watermark 방식 + `scaling/traffic_gate.py` 연동
2. **작업 2** — `AdmissionControlMiddleware` + `AdmissionControlSettings` + `TierRegistry` 캐시
3. **작업 1** — `TieringMiddleware` Merge Strategy + `BACKPRESSURE_TIER_RULES`

---

## 14) 네이밍 일람 — 신규 식별자 충돌 검증

| 신규 식별자 | 위치 | 유형 | 기존 코드 충돌 여부 |
|---|---|---|---|
| `PRIORITY_WATERMARKS` | `scaling/rate_controller.py` | 상수 | 없음 (`BUFFER_HIGH_WATERMARK`는 `audit/self_audit.py`의 Enum 멤버, 다른 컨텍스트) |
| `get_token_ratio()` | `scaling/rate_controller.py` > `TokenBucket` | 메서드 | 없음 (기존: `get_rate()`, `consume()`, `set_rate()`, `wait_for_token()`) |
| `AdmissionControlMiddleware` | `api/django/admission_control.py` | 클래스 | 없음 (기존 미들웨어: `TieringMiddleware`, `BackpressureMiddleware`, `HybridRateLimitMiddleware`, `SelfHealingMiddleware` 등) |
| `AdmissionControlSettings` | `settings/admission_control.py` | 클래스 | 없음 (기존: `BackpressureSettings`, `BulkheadSettings` 등) |
| `BACKPRESSURE_TIER_RULES` | `api/django/tiering/defaults.py` | 상수 | 없음 (기존: `EMERGENCY_LEVEL_RULES`는 `services/emergency_mode/enums.py`, `LEVEL_RATE_MULTIPLIERS`는 `settings/backpressure.py`) |
| `_path_tier_cache` | `api/django/tiering/registry.py` > `TierRegistry` | 속성 | 없음 |
| `_invalidate_path_cache()` | `api/django/tiering/registry.py` > `TierRegistry` | 메서드 | 없음 |
| `_selfhealing_tier_id` | Django request 속성 | 속성 | 없음 |
| `_selfhealing_tier_priority` | Django request 속성 | 속성 | 없음 |

---

## 15) 작업 1 변경 사항 요약 (리뷰 반영)

**원안:**
- `TieringMiddleware`에 Backpressure 레벨 확인 분기를 추가하여, Emergency가 아닌 일반 과부하에서도 tier별 차등 거부 수행

**수정안:**
- `TieringMiddleware`에 Backpressure 분기를 독립적으로 추가하는 대신, **Emergency와 Backpressure의 Merge Strategy(Most Restrictive Wins)를 구현**
- `AdmissionControlMiddleware`가 이미 유입 제어를 담당하므로, `TieringMiddleware`는 Emergency Mode + Backpressure의 복합 상황에서 확률적 드랍 역할만 수행
- `BACKPRESSURE_TIER_RULES` 상수를 `defaults.py`에 추가하여 Backpressure 레벨별 tier 배율 정의

**변경 이유:**
- `AdmissionControlMiddleware`(작업 2)가 `TrafficGate` 파이프라인을 통해 Rate Limit + Bulkhead 기반 priority 차등 제어를 이미 수행
- `TieringMiddleware`에 동일한 Backpressure 기반 차등 거부를 추가하면 이중 실행
- Merge Strategy로 변경하면 Emergency Mode와 Backpressure가 동시 활성화된 경우에만 추가 보호 제공

---

## 16) 작업 3 변경 사항 요약 (리뷰 반영)

**원안:**
- `RateController`에 priority별 독립 `TokenBucket` dict (`_priority_buckets`) 추가
- `should_process(priority)` 에서 priority별 전용 버킷 → critical만 전역 버킷 fallback

**수정안:**
- 독립 버킷 구조 폐기, **Watermark 방식** 채택
- 기존 단일 `_token_bucket` 유지
- `TokenBucket`에 `get_token_ratio()` 메서드 추가
- `should_process(priority)` 에서 현재 토큰 비율이 priority별 watermark 이상인지 확인 후 `consume()`
- `PRIORITY_WATERMARKS` 상수로 임계치 정의

**변경 이유:**
- 단일 버킷이므로 `_adjust_rate()` 변경 불필요 (기존 AIMD 패턴 100% 호환)
- 유휴 자원 낭비 원천 해결 (공유 풀)
- 전체 Rate Limit 초과 위험 제거 (단일 버킷에서 `consume`)
- `BackpressureMiddleware` 하위 호환 (`should_process("standard")` 기본값)

---

## 17) 구현 완료 기록

구현일: 2026-02-17

### 구현 완료 파일 목록

| # | 파일 | 변경 유형 | 작업 | 내용 |
|---|------|-----------|------|------|
| 1 | `scaling/rate_controller.py` | 수정 | 작업 3 | `PRIORITY_WATERMARKS` 상수, `TokenBucket.get_token_ratio()`, `should_process(priority: str)` Watermark 분기, 거부 사유 로그 |
| 2 | `scaling/traffic_gate.py` | 수정 | 작업 3 | `_PRIORITY_TIER_THRESHOLDS`, `_map_priority_int_to_tier()`, `should_allow()` 내 priority→tier 매핑 + `should_process(priority=tier_str)` 호출, 거부 시 metadata에 priority 포함 |
| 3 | `settings/admission_control.py` | 신규 | 작업 2 | `AdmissionControlSettings` (Pydantic v2), tier별 Bulkhead max_concurrent 환경변수 바인딩 |
| 4 | `api/django/admission_control.py` | 신규 | 작업 2 | `AdmissionControlMiddleware`, `TIER_PRIORITY_MAP`, request 객체에 `_selfhealing_tier_id`/`_selfhealing_tier_priority` 주입, tier별 Bulkhead 자동 등록 |
| 5 | `api/django/tiering/registry.py` | 수정 | 작업 2 | `_path_tier_cache` dict 캐시 (최대 1024 엔트리), `_invalidate_path_cache()` 메서드, 모든 mutation 메서드에 캐시 무효화 호출 추가 |
| 6 | `api/django/tiering/defaults.py` | 수정 | 작업 1 | `BACKPRESSURE_TIER_RULES` 상수 (BackpressureLevel별 tier 트래픽 배율) |
| 7 | `api/django/tiering/middleware.py` | 수정 | 작업 1 | `__call__()` Most Restrictive Wins 병합: `min(emergency_multiplier, backpressure_multiplier)`, Emergency와 Backpressure 모두 정상이면 통과 |

### 하위 호환성 검증

- `should_process()` 기본값 `priority="standard"` → 기존 호출부 (BackpressureMiddleware, BackpressureGuard, backpressure_mixin 등) 무변경 동작 확인
- 기존 단위 테스트 1121개 전체 통과 (scaling + throttle)
- tiering/middleware 관련 테스트 38개 전체 통과

### 통합 테스트

AdmissionControlMiddleware → TieringMiddleware → Application 미들웨어 체인과 RateController ↔ TrafficGate priority 전파에 대한 통합 테스트가 필요하다. 기존 통합 테스트에는 해당 시나리오가 없으므로 별도 작성이 필요하다.

# Phase 5 결과 보고서 (1/3): 정상 요청 흐름도

> **생성일**: 2026-01-04
> **Phase**: 5 (전체 흐름도 작성)
> **상태**: ✅ 완료
> **문서 분류**: 정상 요청 흐름

---

## 📋 요약

이 문서는 Self-Healing 미들웨어 시스템의 **정상 요청 처리 흐름**을 코드 근거와 함께 설명합니다.

| 항목 | 내용 |
|------|------|
| 미들웨어 체인 길이 | 19개 |
| 정상 흐름 단계 | 5 Phase |
| 주요 코드 파일 | 7개 |

---

## 1. 전체 정상 요청 흐름도

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          정상 요청 흐름 (Happy Path)                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  HTTP Request 도착                                                          │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║ PHASE 1: 분산 추적 및 헬스체크 (DB 독립)                               ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║  [1] trace_id_middleware                                              ║  │
│  ║      → X-Request-ID 추출 또는 "req-{uuid8}" 생성                      ║  │
│  ║      → set_trace_id() → contextvars + thread_local 저장               ║  │
│  ║                                                                       ║  │
│  ║  [2] HealthBridgeMiddleware                                           ║  │
│  ║      → /health/l3, /health/bridge → 즉시 JsonResponse 반환            ║  │
│  ║      → 정상 경로 → _try_update_snapshot() 후 통과                     ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║ PHASE 2: 트래픽 제어 및 Self-Healing 진입                              ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║  [3] TieringMiddleware                                                ║  │
│  ║      → EmergencyManager.get_current_level() 확인                      ║  │
│  ║      → NORMAL 레벨 → 모든 요청 허용                                   ║  │
│  ║                                                                       ║  │
│  ║  [4] SelfHealingMiddleware                                            ║  │
│  ║      → _is_cb_open() = False → 정상 통과                              ║  │
│  ║      → _capture_request_data() → 요청 정보 캡처                       ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║ PHASE 3: 사용자 추적 및 Django Core                                    ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║  [5] ActorContextMiddleware                                           ║  │
│  ║      → ActorContext.set_actor_from_django_request(request)            ║  │
│  ║      → actor_id, actor_type, ip_address 추출                          ║  │
│  ║                                                                       ║  │
│  ║  [6-13] Django Core Middlewares                                       ║  │
│  ║      → SecurityMiddleware, SessionMiddleware, CommonMiddleware        ║  │
│  ║      → CsrfViewMiddleware, AuthenticationMiddleware 등                ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║ PHASE 4: Rate Limit 및 Pool 보호                                       ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║  [14] HybridRateLimitMiddleware                                       ║  │
│  ║      → /api/self-healing/* 경로만 체크                                ║  │
│  ║      → Redis sliding window 100 req/min 확인 → 통과                   ║  │
│  ║                                                                       ║  │
│  ║  [15] PoolCircuitBreakerMiddleware                                    ║  │
│  ║      → get_cached_pool_status() → is_exhausted=False                  ║  │
│  ║      → should_allow() = True → 통과                                   ║  │
│  ║                                                                       ║  │
│  ║  [16] PoolTimeoutMiddleware                                           ║  │
│  ║      → 요청 전달 (예외 감시 시작)                                     ║  │
│  ║                                                                       ║  │
│  ║  [17-18] ChaosMiddleware (비활성화 상태)                              ║  │
│  ║      → CHAOS_MIDDLEWARE_ENABLED=False → 바이패스                      ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║                         [VIEW 실행]                                   ║  │
│  ║                  비즈니스 로직 + DB 쿼리                               ║  │
│  ║                  → 정상 처리 → HTTP 200 응답                           ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  ╔═══════════════════════════════════════════════════════════════════════╗  │
│  ║ PHASE 5: 응답 처리 및 Audit                                            ║  │
│  ╠═══════════════════════════════════════════════════════════════════════╣  │
│  ║  [4] SelfHealingMiddleware (응답 단계)                                ║  │
│  ║      → response.status_code < 400 → _record_cb_success()              ║  │
│  ║                                                                       ║  │
│  ║  [19] AuditMiddleware (맨 마지막!)                                    ║  │
│  ║      → _capture_response_meta() → 응답 정보 수집                      ║  │
│  ║      → buffer.has_events() → ContinuousAuditRecorder 기록             ║  │
│  ║                                                                       ║  │
│  ║  [1] trace_id_middleware (응답 단계)                                  ║  │
│  ║      → response["X-Request-ID"] = trace_id                            ║  │
│  ║      → clear_trace_id() → 컨텍스트 정리                               ║  │
│  ╚═══════════════════════════════════════════════════════════════════════╝  │
│       │                                                                     │
│       ▼                                                                     │
│  HTTP Response 반환 (X-Request-ID 헤더 포함)                                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 단계별 상세 코드 근거

### 2.1 [Phase 1] trace_id_middleware

**파일**: `packages/selfhealing-python/src/selfhealing/audit/trace.py`

```python
# Line 16-17: 컨텍스트 변수 정의
_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_thread_local = threading.local()

# Line 23-28: trace_id 생성
def generate_trace_id() -> str:
    """Format: "req-{uuid4_short}" (e.g., "req-a1b2c3d4")"""
    return f"req-{uuid.uuid4().hex[:8]}"

# Line 60-68: 요청에서 trace_id 추출
def extract_trace_id_from_request(request) -> Optional[str]:
    """
    Checks common tracing headers in order:
    1. X-Request-ID
    2. X-Trace-ID
    3. X-Correlation-ID
    4. traceparent (W3C Trace Context)
    5. X-Amzn-Trace-Id (AWS X-Ray)
    """
    headers_to_check = [
        "HTTP_X_REQUEST_ID",
        "HTTP_X_TRACE_ID",
        "HTTP_X_CORRELATION_ID",
        "HTTP_TRACEPARENT",
        "HTTP_X_AMZN_TRACE_ID",
    ]
```

**동작 흐름**:
1. 요청 헤더에서 trace_id 추출 시도
2. 없으면 `req-{uuid8}` 형식으로 생성
3. `contextvars` + `thread_local` 양쪽에 저장 (async/sync 호환)
4. 응답 시 `X-Request-ID` 헤더에 추가

---

### 2.2 [Phase 1] HealthBridgeMiddleware

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/middleware.py`

```python
# Line 67-70: Bridge 대상 경로
BRIDGE_PATHS = [
    "/api/self-healing/health/l3/",
    "/api/self-healing/health/bridge/",
]

# Line 81-93: 요청 처리 로직
def __call__(self, request: "HttpRequest") -> "HttpResponse":
    from django.http import JsonResponse

    # === Phase 1: Early Return for Bridge Paths ===
    if request.path in self.BRIDGE_PATHS:
        return self._serve_bridge_response(request)

    # === Phase 2: Normal Request Processing ===
    response = self.get_response(request)

    # === Phase 3: Update CB Snapshot (best-effort) ===
    self._try_update_snapshot()

    return response

# Line 95-113: Bridge 응답 (DB 없이 즉시 반환)
def _serve_bridge_response(self, request: "HttpRequest") -> "HttpResponse":
    """Returns CB snapshot from memory - instant response even during DB blackout."""
    snapshot = self._get_snapshot()

    response_data = {
        "status": "bridge_active",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "circuit_breakers": snapshot.get("states", {}),
        "snapshot": {
            "last_updated": snapshot.get("last_updated"),
            "update_count": snapshot.get("update_count", 0),
            "age_seconds": self._calculate_snapshot_age(snapshot),
        },
        "note": "DB-independent health endpoint (Stage 50)",
    }

    return JsonResponse(response_data)
```

**설계 목적**:
- DB가 죽어도 `/health/l3` 응답 가능
- Worker Saturation 방지 (Kubernetes Probe용)

---

### 2.3 [Phase 2] TieringMiddleware

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/tiering/middleware.py`

```python
# Line 82-116: 요청 처리 로직
def __call__(self, request):
    if not self._enabled:
        return self.get_response(request)

    try:
        from selfhealing.services.emergency_mode import get_emergency_manager
        from selfhealing.services.emergency_mode.enums import (
            EmergencyLevel,
            EMERGENCY_LEVEL_RULES,
        )

        manager = get_emergency_manager()

        # 비상 모드 비활성화 시 통과
        if not manager.is_active():
            return self.get_response(request)

        current_level = manager.get_current_level()

        # NORMAL 레벨이면 모든 요청 허용
        if current_level == EmergencyLevel.NORMAL:
            return self.get_response(request)

        # Tier 확인 및 확률적 제어
        tier_result = self._registry.resolve_tier_with_fallback(...)
        level_rules = EMERGENCY_LEVEL_RULES.get(current_level, {})
        multiplier = level_rules.get(tier_result.tier_id, 0.0)

        if not self._should_allow_request(multiplier):
            return self._create_load_shedding_response(...)

        return self.get_response(request)

    except Exception as e:
        logger.error(f"[TieringMiddleware] Error: {e}, allowing request")
        return self.get_response(request)  # Fail-Open
```

**Emergency Level별 Tier 제어** (코드 근거: `enums.py`):

| Level | critical | standard | non_essential |
|:-----:|:--------:|:--------:|:-------------:|
| NORMAL | 100% | 100% | 100% |
| LEVEL_1 | 100% | 100% | 0% |
| LEVEL_2 | 100% | 10% | 0% |
| LEVEL_3 | 50% | 0% | 0% |

---

### 2.4 [Phase 2] SelfHealingMiddleware (요청 단계)

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/middleware.py`

```python
# Line 719-746: 요청 처리 시작
def __call__(self, request: "HttpRequest") -> "HttpResponse":
    from django.http import JsonResponse

    self._lazy_init()

    request_data = self._capture_request_data(request)
    db_error_context = None

    # v6.1.0: CB OPEN 상태에서 선제적 DLQ 적재
    if self._is_cb_open() and self._is_dlq_eligible(request):
        # ... 선제적 DLQ 적재 로직 (예외 흐름에서 설명)
        pass

    # 정상 흐름: 요청 전달
    try:
        response = self.get_response(request)
    except Exception as e:
        # ... 예외 처리 (예외 흐름에서 설명)
        pass

# Line 926-940: CB OPEN 상태 확인
def _is_cb_open(self) -> bool:
    """Check if CircuitBreaker is in OPEN state."""
    try:
        # 1. CircuitBreakerService 상태 확인
        if self._cb_service and self._cb_service.is_enabled:
            state = self._cb_service.get_state(self.CB_SERVICE_NAME)
            if state and state.lower() in ("open", "half_open"):
                return True

        # 2. PoolCircuitBreaker 상태 확인
        from selfhealing.api.django.pool_circuit_breaker import pool_circuit_breaker
        pool_state = pool_circuit_breaker.state
        if pool_state in ("OPEN", "HALF_OPEN"):
            return True

        return False
    except Exception as e:
        return False  # Fail-Open
```

**정상 흐름에서의 동작**:
1. `_is_cb_open()` = False → 선제적 DLQ 적재 스킵
2. `get_response(request)` 호출하여 다음 미들웨어로 전달
3. 응답 수신 후 status_code 확인

---

### 2.5 [Phase 3] ActorContextMiddleware

**파일**: `myproject/middleware/actor_middleware.py`

```python
# Line 64-77: 요청 처리 로직
def __call__(self, request: HttpRequest) -> HttpResponse:
    # 미들웨어 비활성화 시 바이패스
    if not self._enabled:
        return self.get_response(request)

    try:
        from selfhealing.context.actor_context import ActorContext
    except ImportError:
        return self.get_response(request)

    # Context Manager로 actor 정보 설정
    with ActorContext.set_actor_from_django_request(request):
        response = self.get_response(request)

    return response
```

**추출되는 Actor 정보**:
- `actor_id`: 사용자 ID 또는 이메일
- `actor_type`: user, admin, system, anonymous
- `ip_address`: 클라이언트 IP
- `session_id`: 세션 ID (있는 경우)

---

### 2.6 [Phase 4] HybridRateLimitMiddleware

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py`

```python
# Line 46-52: 기본 설정값
DEFAULT_RATE_LIMIT = 100  # requests per minute
DEFAULT_WINDOW_SECONDS = 60
EMERGENCY_RATE_LIMIT = 10  # requests per minute per pod (Redis 장애 시)
EMERGENCY_WINDOW_SECONDS = 60
CONTROL_API_PATH_PREFIX = "/api/self-healing/"

# Line 140-180: LocalMemoryRateLimiter (Fallback)
class LocalMemoryRateLimiter:
    """
    L1 로컬 메모리 기반 레이트 리미터.
    Redis 장애 시 활성화되는 비상 레이트 리미터.
    분산 환경에서 동기화되지 않으므로 10배 엄격한 제한 적용.
    """

    def __init__(
        self,
        max_requests: int = EMERGENCY_RATE_LIMIT,
        window_seconds: int = EMERGENCY_WINDOW_SECONDS,
    ):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> Tuple[bool, int]:
        """Check if request is allowed."""
        now = time.time()
        window_start = now - self.window_seconds

        with self._lock:
            # Keep only requests within window
            self._requests[key] = [
                ts for ts in self._requests[key]
                if ts > window_start
            ]

            current_count = len(self._requests[key])

            if current_count >= self.max_requests:
                return (False, 0)

            # Record this request
            self._requests[key].append(now)
            remaining = self.max_requests - current_count - 1

            return (True, remaining)
```

**Defense-in-Depth 전략**:

| Layer | 저장소 | 한도 | 상태 |
|:-----:|--------|:----:|------|
| L2 | Redis (Sliding Window) | 100 req/min | Primary |
| L1 | Local Memory | 10 req/min | Fallback |

---

### 2.7 [Phase 4] PoolCircuitBreakerMiddleware

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/pool_circuit_breaker.py`

```python
# Line 222-268: 캐시 기반 Pool 상태 조회 (Non-Blocking)
def get_cached_pool_status(self) -> dict:
    """
    캐시된 Pool 상태 반환 (Non-Blocking).
    v6.2.0: 매 요청에서 이 메서드를 호출하여 블로킹 방지.
    """
    with self._cache_lock:
        status = self._cached_pool_status.copy()
        self._stats["cache_hits"] += 1

    # 백그라운드 스레드 상태 확인 및 자동 재시작
    if not self._background_thread or not self._background_thread.is_alive():
        self._start_background_refresh()

    # Stale 캐시 처리
    cache_age_ms = (time.time() - status.get("_cache_time", 0)) * 1000

    if cache_age_ms > self._critical_stale_ms:
        # Critical Stale → 안전하게 CLOSED로 폴백 (요청 허용)
        return {
            "available": True,
            "is_exhausted": False,
            "_stale_fallback": True,
        }

    return status

# Line 477-520: 요청 허용 여부 결정
def should_allow(self) -> Tuple[bool, Optional[str]]:
    """요청을 허용할지 결정 (캐시 기반, Non-Blocking)"""
    self._stats["total_requests"] += 1

    # 캐시에서 Pool 상태 조회 (Non-Blocking)
    cached_status = self.get_cached_pool_status()

    # Pool 고갈 감지
    if cached_status.get("is_exhausted", False):
        self.record_failure()

    current_state = self._state

    if current_state == self.CLOSED:
        return (True, None)  # 정상 - 통과

    elif current_state == self.OPEN:
        # 복구 타임아웃 확인
        if self._open_time and (time.time() - self._open_time) >= self._recovery_timeout:
            self._set_state(self.HALF_OPEN)
            self._half_open_requests = 1
            return (True, "Testing recovery (HALF_OPEN)")

        # 여전히 차단
        return (False, f"Circuit OPEN - retry in {remaining:.1f}s")

    elif current_state == self.HALF_OPEN:
        # 복구 테스트 중
        if self._half_open_requests < self._half_open_max_requests:
            self._half_open_requests += 1
            return (True, "Testing recovery (HALF_OPEN)")
        else:
            return (False, "HALF_OPEN test in progress - wait")

    return (True, None)
```

**v6.2.0+ 캐시 기반 조회**:
- 매 요청마다 Pool 상태 직접 조회 → 캐시 기반 조회 (블로킹 제거)
- 백그라운드 스레드에서 100ms 주기로 갱신

---

### 2.8 [Phase 5] SelfHealingMiddleware (응답 단계)

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/middleware.py`

```python
# Line 847-855: 성공 응답 처리
# HTTP 5xx 응답이 아닌 경우
else:
    # 성공 응답일 경우 CircuitBreaker에 성공 기록
    if response.status_code < 400:
        self._record_cb_success()

return response

# Line 1012-1020: CB 성공 기록
def _record_cb_success(self) -> None:
    """Record success to CircuitBreaker."""
    try:
        if self._cb_service and self._cb_service.is_enabled:
            self._cb_service.record_success(self.CB_SERVICE_NAME)
    except Exception as e:
        pass  # Success recording failure should not affect response
```

**성공 기록의 의미**:
- `HALF_OPEN` 상태에서 성공 횟수 증가
- `success_threshold` 도달 시 → `CLOSED`로 전환 (자동 복구)

---

### 2.9 [Phase 5] AuditMiddleware

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py`

```python
# Line 85-99: 제외 경로
EXCLUDED_PATHS = [
    "/api/self-healing/health/",
    "/health/",
    "/api/self-healing/metrics/",
    "/metrics/",
    "/favicon.ico",
    "/static/",
]

# Line 116-143: 요청 처리 로직
def __call__(self, request: "HttpRequest") -> "HttpResponse":
    self._ensure_initialized()
    self._total_requests += 1

    # === 제외 경로 체크 ===
    if self._should_skip(request):
        return self.get_response(request)

    # === Phase 1: 버퍼 초기화 ===
    buffer = self._init_buffer(request)

    # === Phase 2: 요청 처리 ===
    response = self.get_response(request)

    # === Phase 3: 응답 메타 수집 ===
    self._capture_response_meta(request, response, buffer)

    # === Phase 4: 이벤트 기록 (버퍼 낚아채기) ===
    if buffer.has_events():
        self._record_events(buffer, request, response)

    return response
```

**"낚시꾼" 설계 원칙** (56_AUDIT_MIDDLEWARE_DESIGN.md):
1. AuditMiddleware가 맨 마지막에 위치
2. 앞에서 발생한 모든 이벤트 (CB 오픈, RateLimit 차단, DLQ 적재)를 수집
3. `ContinuousAuditRecorder`를 통해 해시 체인으로 기록

---

## 3. 정상 흐름 시퀀스 다이어그램

```mermaid
sequenceDiagram
    participant Client
    participant trace as trace_id_middleware
    participant health as HealthBridgeMiddleware
    participant tier as TieringMiddleware
    participant sh as SelfHealingMiddleware
    participant actor as ActorContextMiddleware
    participant django as Django Core (6-13)
    participant rate as HybridRateLimitMiddleware
    participant pool as PoolCircuitBreakerMiddleware
    participant view as View
    participant audit as AuditMiddleware

    Client->>trace: HTTP Request
    trace->>trace: generate_trace_id() → "req-a1b2c3d4"
    trace->>trace: set_trace_id()
    trace->>health: request + trace_id

    health->>health: path ∉ BRIDGE_PATHS
    health->>tier: pass through

    tier->>tier: EmergencyLevel == NORMAL
    tier->>sh: pass through

    sh->>sh: _is_cb_open() = False
    sh->>sh: _capture_request_data()
    sh->>actor: pass through

    actor->>actor: ActorContext.set_actor_from_django_request()
    actor->>django: pass through

    django->>rate: Security, Session, Auth...

    rate->>rate: path.startswith("/api/self-healing/") ?
    Note right of rate: Yes → Redis check<br/>No → pass through
    rate->>pool: pass through

    pool->>pool: get_cached_pool_status()
    pool->>pool: is_exhausted = False
    pool->>pool: should_allow() = (True, None)
    pool->>view: pass through

    view->>view: Business Logic + DB Query
    view-->>pool: HTTP 200 Response

    pool-->>sh: response
    sh->>sh: status_code < 400
    sh->>sh: _record_cb_success()
    sh-->>audit: response

    audit->>audit: _capture_response_meta()
    audit->>audit: buffer.has_events() → record

    audit-->>trace: response
    trace->>trace: response["X-Request-ID"] = trace_id
    trace->>trace: clear_trace_id()
    trace-->>Client: HTTP 200 + X-Request-ID
```

---

## 4. 정상 흐름 요약

| Phase | 미들웨어 | 핵심 동작 | 코드 위치 |
|:-----:|----------|----------|-----------|
| 1 | trace_id | trace_id 생성/설정 | `trace.py:23-28` |
| 1 | HealthBridge | CB 스냅샷 갱신 | `middleware.py:81-93` |
| 2 | Tiering | Emergency Level 확인 | `tiering/middleware.py:82-116` |
| 2 | SelfHealing | CB 상태 확인 | `middleware.py:926-940` |
| 3 | ActorContext | Actor 정보 추출 | `actor_middleware.py:64-77` |
| 3 | Django Core | 세션, 인증, CSRF | Django 내장 |
| 4 | RateLimit | Redis/Local 체크 | `rate_limit.py:140-180` |
| 4 | PoolCB | 캐시 기반 Pool 체크 | `pool_circuit_breaker.py:222-268` |
| 5 | SelfHealing | CB 성공 기록 | `middleware.py:1012-1020` |
| 5 | Audit | 이벤트 수집/기록 | `audit_middleware.py:116-143` |
| 5 | trace_id | 헤더 추가/정리 | `trace.py:60-68` |

---

## 📚 관련 문서

- [15_PHASE5_EXCEPTION_HANDLING_FLOW.md](15_PHASE5_EXCEPTION_HANDLING_FLOW.md) - 예외 발생 흐름도
- [16_PHASE5_AUTO_RECOVERY_FLOW.md](16_PHASE5_AUTO_RECOVERY_FLOW.md) - 자동 복구 흐름도
- [11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md](11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md) - 미들웨어 분석
- [13_PHASE3_DEPENDENCY_ANALYSIS_RESULT.md](13_PHASE3_DEPENDENCY_ANALYSIS_RESULT.md) - 의존성 분석

---

*이 문서는 Phase 5 전체 흐름도 작성의 결과물입니다. (1/3)*

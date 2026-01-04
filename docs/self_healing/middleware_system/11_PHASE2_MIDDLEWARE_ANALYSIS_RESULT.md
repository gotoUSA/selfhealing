# Phase 2 결과 보고서: 미들웨어 코드 기반 분석

> **생성일**: 2026-01-04
> **Phase**: 2 (코드 기반 검증)
> **상태**: ✅ 완료

---

## 📋 요약

| 항목 | 수치 |
|------|:----:|
| 총 등록된 미들웨어 | **13개** |
| Self-Healing 패키지 미들웨어 | 7개 |
| myproject 로컬 미들웨어 | 3개 |
| Django Core 미들웨어 | 8개 |
| 환경변수로 토글 가능 | 6개 |

---

## 1. 미들웨어 완전 목록

### 1.1 등록된 미들웨어 (실행 순서)

| 순서 | 미들웨어 | 파일 위치 | 핵심 기능 | 토글 가능 |
|:---:|----------|-----------|-----------|:---------:|
| **1** | `trace_id_middleware` | `selfhealing/audit/trace.py` | 분산 추적 ID 생성 & 전파 | ❌ |
| **2** | `HealthBridgeMiddleware` | `selfhealing/api/django/middleware.py` | DB 독립 헬스체크 (/health/l3) | ❌ |
| **3** | `TieringMiddleware` | `selfhealing/api/django/tiering/middleware.py` | 비상 모드 Tier별 트래픽 제어 | ✅ |
| **4** | `SelfHealingMiddleware` | `selfhealing/api/django/middleware.py` | Circuit Breaker + DLQ 자동 적재 | ❌ |
| **5** | `ActorContextMiddleware` | `myproject/middleware/actor_middleware.py` | 사용자 추적 (누가 요청했는지) | ✅ |
| **6** | `SecurityMiddleware` | Django Core | HTTPS, HSTS 등 보안 | ❌ |
| **7** | `SessionMiddleware` | Django Core | 세션 처리 | ❌ |
| **8** | `CommonMiddleware` | Django Core | URL 정규화 | ❌ |
| **9** | `CsrfViewMiddleware` | Django Core | CSRF 보호 | ❌ |
| **10** | `AuthenticationMiddleware` | Django Core | 인증 처리 | ❌ |
| **11** | `MessageMiddleware` | Django Core | 메시지 프레임워크 | ❌ |
| **12** | `XFrameOptionsMiddleware` | Django Core | Clickjacking 방지 | ❌ |
| **13** | `AccountMiddleware` | allauth | 소셜 로그인 계정 처리 | ❌ |
| **14** | `HybridRateLimitMiddleware` | `selfhealing/api/django/rate_limit.py` | Redis + Local Memory Rate Limit | ❌ |
| **15** | `PoolCircuitBreakerMiddleware` | `selfhealing/api/django/pool_circuit_breaker.py` | DB Pool 고갈 시 503 Fail Fast | ✅ |
| **16** | `PoolTimeoutMiddleware` | `myproject/middleware/pool_timeout_middleware.py` | SQLAlchemy Pool Timeout 503 반환 | ✅ |
| **17** | `ChaosMiddleware` | `myproject/middleware/chaos_middleware.py` | HELLMODE 테스트용 장애 주입 | ✅ |
| **18** | `ConnectionPoolLimiterMiddleware` | `myproject/middleware/chaos_middleware.py` | HELLMODE 커넥션 풀 제한 | ✅ |
| **19** | `AuditMiddleware` | `selfhealing/api/django/audit_middleware.py` | 중앙화 Audit (맨 마지막 필수!) | ✅ |

---

## 2. 미들웨어 상세 분석

### 2.1 [1] trace_id_middleware (함수형)

```
📁 packages/selfhealing-python/src/selfhealing/audit/trace.py
```

| 항목 | 내용 |
|------|------|
| **타입** | 함수형 미들웨어 |
| **위치** | 최상단 (분산 추적 시작점) |
| **핵심 기능** | X-Request-ID 헤더에서 trace_id 추출 또는 생성, 응답에 추가 |
| **의존성** | 없음 (독립적) |

**동작 흐름**:
```
Request → trace_id 추출/생성 → request.trace_id 저장 → 
Response → X-Request-ID 헤더 추가 → clear_trace_id()
```

---

### 2.2 [2] HealthBridgeMiddleware

```
📁 packages/selfhealing-python/src/selfhealing/api/django/middleware.py
```

| 항목 | 내용 |
|------|------|
| **위치** | 최상단 (DB 엔진 로드 전) |
| **핵심 기능** | `/health/l3`, `/health/bridge/` 경로에서 DB 없이 즉시 응답 |
| **목적** | Kubernetes Probe용, Worker Saturation 방지 |
| **CB 스냅샷** | 클래스 변수로 CB 상태 캐시, 매 요청마다 갱신 |

**대상 경로**:
- `/api/self-healing/health/l3/`
- `/api/self-healing/health/bridge/`

---

### 2.3 [3] TieringMiddleware

```
📁 packages/selfhealing-python/src/selfhealing/api/django/tiering/middleware.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | Emergency Level에 따라 Tier별 트래픽 차등 제어 |
| **Emergency Levels** | NORMAL(0), LEVEL_1, LEVEL_2, LEVEL_3 |
| **Tier 분류** | critical, standard, non_essential |
| **토글** | `SELFHEALING_TIERING_MIDDLEWARE_ENABLED` |

**Emergency Level별 동작**:
| Level | critical | standard | non_essential |
|:-----:|:--------:|:--------:|:-------------:|
| NORMAL | 100% | 100% | 100% |
| LEVEL_1 | 100% | 100% | 0% |
| LEVEL_2 | 100% | 10% | 0% |
| LEVEL_3 | 50% | 0% | 0% |

---

### 2.4 [4] SelfHealingMiddleware

```
📁 packages/selfhealing-python/src/selfhealing/api/django/middleware.py (Line 578)
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | DB 오류/HTTP 5xx 감지 → CB 기록 + DLQ 자동 적재 |
| **감시 코드** | 502, 503, 504 |
| **감시 예외** | OperationalError, InterfaceError, DatabaseError |
| **의존성** | CircuitBreaker Service, AuditLogger |

**Stage 16 Healing Proof 기능**:
- DB 커넥션 풀 고갈 시 서킷 자동 오픈
- 502/503 에러 발생 시 DLQ 자동 적재
- 복구 후 자동 리플레이 트리거

---

### 2.5 [5] ActorContextMiddleware

```
📁 myproject/middleware/actor_middleware.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | 요청에서 사용자 정보 추출 (actor_id, actor_type, IP 등) |
| **용도** | Audit 로그에 "누가" 수행했는지 자동 기록 |
| **토글** | `SELFHEALING_ACTOR_MIDDLEWARE_ENABLED` |
| **의존성** | `selfhealing.context.ActorContext` |

---

### 2.6 [7] HybridRateLimitMiddleware

```
📁 packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | Defense-in-Depth Rate Limiting |
| **L2 (Primary)** | Redis 기반 sliding window (100 req/min) |
| **L1 (Fallback)** | Local memory (10 req/min, Redis 실패 시) |
| **대상** | `/api/self-healing/*` 경로 |

**설정 가능 값**:
- `control_api_rate_limit`: 정상 모드 (기본 100)
- `emergency_rate_limit`: 비상 모드 (기본 10)

---

### 2.7 [8] PoolCircuitBreakerMiddleware

```
📁 packages/selfhealing-python/src/selfhealing/api/django/pool_circuit_breaker.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | DB Pool 고갈 시 즉시 503 (Fail Fast) |
| **상태** | CLOSED → OPEN → HALF_OPEN → CLOSED |
| **토글** | `SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED` |

**v6.2.0+ 개선사항**:
- 캐시 기반 Pool 상태 조회 (블로킹 제거)
- 백그라운드 스레드 Pool 상태 갱신 (100ms)
- TTL 범위 검증 (50~1000ms)

**환경변수**:
| 변수 | 기본값 | 설명 |
|------|:------:|------|
| `POOL_CB_FAILURE_THRESHOLD` | 3 | OPEN 전환 실패 횟수 |
| `POOL_CB_SUCCESS_THRESHOLD` | 2 | CLOSED 전환 성공 횟수 |
| `POOL_CB_RECOVERY_TIMEOUT` | 10 | HALF_OPEN 대기 시간(초) |
| `POOL_CB_CACHE_INTERVAL_MS` | 100 | 캐시 갱신 주기(ms) |

---

### 2.8 [9] PoolTimeoutMiddleware

```
📁 myproject/middleware/pool_timeout_middleware.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | SQLAlchemy Pool Timeout 시 즉시 503 |
| **감지 패턴** | timeout, queuepool limit, pool exhausted 등 |
| **토글** | `SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED` |

---

### 2.9 [10] ChaosMiddleware & ConnectionPoolLimiterMiddleware

```
📁 myproject/middleware/chaos_middleware.py
```

| 항목 | 내용 |
|------|------|
| **핵심 기능** | HELLMODE 테스트용 장애 주입 |
| **활성화 조건** | X-Test-Mode: hellmode/chaos-monkey |
| **토글** | `CHAOS_MIDDLEWARE_ENABLED` (기본 False) |

**지원 Chaos 모드**:
| 헤더 | 동작 |
|------|------|
| `X-DB-Lock-Timeout` | DB lock_timeout 설정 |
| `X-DB-Statement-Timeout` | statement_timeout 설정 |
| `X-Chaos-Mode: deadlock` | 데드락 유발 |
| `X-Chaos-Mode: pool-starve` | 커넥션 풀 고갈 |
| `X-Chaos-Mode: slow-query` | 의도적 지연 쿼리 |

---

### 2.10 [11] AuditMiddleware (맨 마지막!)

```
📁 packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py
```

| 항목 | 내용 |
|------|------|
| **위치** | **반드시 맨 마지막!** |
| **핵심 기능** | 모든 미들웨어 이벤트를 단일 해시 체인으로 기록 |
| **토글** | `SELFHEALING_AUDIT_MIDDLEWARE_ENABLED` |

**왜 맨 마지막인가?**
- 앞의 모든 미들웨어에서 발생한 이벤트 수집
- CB 오픈, RateLimit 차단, DLQ 적재 이벤트 등 모두 "낚아채기"
- Big 4 감사 시 "단 하나의 로그도 누락되지 않음" 증명

**제외 경로**:
- `/api/self-healing/health/`, `/health/`
- `/api/self-healing/metrics/`, `/metrics/`
- `/favicon.ico`, `/static/`

---

## 3. 환경변수 (Feature Flag) 목록

| 환경변수 | 기본값 | 영향 미들웨어 |
|----------|:------:|---------------|
| `SELFHEALING_TIERING_MIDDLEWARE_ENABLED` | True | TieringMiddleware |
| `SELFHEALING_ACTOR_MIDDLEWARE_ENABLED` | True | ActorContextMiddleware |
| `SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED` | True | PoolCircuitBreakerMiddleware |
| `SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED` | True | PoolTimeoutMiddleware |
| `CHAOS_MIDDLEWARE_ENABLED` | False | ChaosMiddleware |
| `SELFHEALING_AUDIT_MIDDLEWARE_ENABLED` | True | AuditMiddleware |

---

## 4. 문서 vs 코드 차이점

### 4.1 문서에서 누락된 미들웨어

| 미들웨어 | 상태 |
|----------|------|
| `ChaosMiddleware` | 문서에 언급 없음 (테스트용이라 의도적일 수 있음) |
| `ConnectionPoolLimiterMiddleware` | 문서에 언급 없음 |
| `AccountMiddleware` (allauth) | 문서에 언급 없음 |

### 4.2 문서 순서 vs 실제 순서

| 항목 | 문서 (10_계획) | 실제 코드 |
|------|---------------|-----------|
| 미들웨어 개수 | 9개 | 13개 (커스텀) + 8개 (Django Core) |
| Pool Timeout | 9번째 | 16번째 |
| Chaos | 없음 | 17, 18번째 |
| Audit | 없음 | 19번째 (마지막) |

### 4.3 업데이트 필요 문서

- [10_MIDDLEWARE_INVESTIGATION_PLAN.md](10_MIDDLEWARE_INVESTIGATION_PLAN.md) - 미들웨어 개수/순서 갱신 필요
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 상세 분석 결과 반영 필요

---

## 5. 미들웨어 실행 파이프라인 (정상 흐름)

```
Request 도착
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [1] trace_id_middleware                                         │
│     → trace_id 생성/추출, request.trace_id 저장                 │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [2] HealthBridgeMiddleware                                      │
│     → /health/l3, /health/bridge → DB 없이 즉시 응답            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [3] TieringMiddleware                                           │
│     → Emergency Level 확인 → Tier별 허용/차단                   │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [4] SelfHealingMiddleware                                       │
│     → (요청 전) CB 상태 확인                                    │
│     → (응답 후) DB 오류/5xx 감지 → CB 기록, DLQ 적재            │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [5] ActorContextMiddleware                                      │
│     → 사용자 정보 추출 (actor_id, IP, session 등)               │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [6-13] Django Core Middlewares                                  │
│     → Security, Session, CSRF, Auth, Messages 등                │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [14] HybridRateLimitMiddleware                                  │
│     → /api/self-healing/* 경로 Rate Limit 체크                  │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [15] PoolCircuitBreakerMiddleware                               │
│     → Pool 상태 확인 → 고갈 시 503 즉시 반환                    │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [16] PoolTimeoutMiddleware                                      │
│     → Pool Timeout 예외 catch → 503 반환                        │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [17-18] ChaosMiddleware (HELLMODE only)                         │
│     → X-Test-Mode 헤더 시 장애 주입                             │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
╔═════════════════════════════════════════════════════════════════╗
║                        [VIEW 실행]                               ║
║              비즈니스 로직 + DB 쿼리                             ║
╚═════════════════════════════════════════════════════════════════╝
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ [19] AuditMiddleware (맨 마지막!)                               │
│     → 모든 이벤트 수집 → 해시 체인 기록                         │
└─────────────────────────────────────────────────────────────────┘
    │
    ▼
Response 반환 (X-Request-ID 포함)
```

---

## 6. 다음 단계 (Phase 4 추천)

Phase 2 완료 후 **Phase 4 (Feature Flag 정리)**를 진행하는 것을 추천합니다.

이유:
1. 환경변수 목록이 이미 일부 추출됨
2. 각 Flag의 영향 범위를 명확히 문서화
3. 비활성화 테스트로 의존성 파악 가능

---

## 📚 참고 파일

| 파일 | 설명 |
|------|------|
| [settings/base.py](../../../myproject/settings/base.py) | MIDDLEWARE 설정 |
| [trace.py](../../../packages/selfhealing-python/src/selfhealing/audit/trace.py) | trace_id_middleware |
| [middleware.py](../../../packages/selfhealing-python/src/selfhealing/api/django/middleware.py) | HealthBridge, SelfHealing |
| [tiering/middleware.py](../../../packages/selfhealing-python/src/selfhealing/api/django/tiering/middleware.py) | Tiering |
| [rate_limit.py](../../../packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py) | HybridRateLimit |
| [pool_circuit_breaker.py](../../../packages/selfhealing-python/src/selfhealing/api/django/pool_circuit_breaker.py) | PoolCircuitBreaker |
| [audit_middleware.py](../../../packages/selfhealing-python/src/selfhealing/api/django/audit_middleware.py) | AuditMiddleware |
| [actor_middleware.py](../../../myproject/middleware/actor_middleware.py) | ActorContext |
| [pool_timeout_middleware.py](../../../myproject/middleware/pool_timeout_middleware.py) | PoolTimeout |
| [chaos_middleware.py](../../../myproject/middleware/chaos_middleware.py) | Chaos, PoolLimiter |

---

*이 문서는 Phase 2 코드 기반 검증의 결과물입니다.*

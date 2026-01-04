# Phase 4 결과 보고서: Feature Flag 전체 정리

> **생성일**: 2026-01-04
> **Phase**: 4 (환경변수/Feature Flag 정리)
> **상태**: ✅ 완료

---

## 📋 요약

| 항목 | 수치 |
|------|:----:|
| **총 Feature Flag** | 35개 |
| 미들웨어 토글 | 7개 |
| 시스템 제어 | 6개 |
| Circuit Breaker 설정 | 8개 |
| Celery/Task 설정 | 5개 |
| Chaos Engineering | 9개 |

---

## 1. 카테고리별 Feature Flag

### 1.1 미들웨어 토글 (7개)

| 환경변수 | 기본값 | 영향 컴포넌트 | 설명 |
|----------|:------:|---------------|------|
| `SELFHEALING_TIERING_MIDDLEWARE_ENABLED` | `True` | TieringMiddleware | 비상 모드 Tier별 트래픽 제어 |
| `SELFHEALING_ACTOR_MIDDLEWARE_ENABLED` | `True` | ActorContextMiddleware | 사용자 추적 (누가 요청했는지) |
| `SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED` | `True` | PoolCircuitBreakerMiddleware | DB Pool 고갈 시 503 반환 |
| `SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED` | `True` | PoolTimeoutMiddleware | SQLAlchemy Pool Timeout 처리 |
| `SELFHEALING_AUDIT_MIDDLEWARE_ENABLED` | `True` | AuditMiddleware | 해시 체인 Audit 기록 |
| `CHAOS_MIDDLEWARE_ENABLED` | `False` | ChaosMiddleware | 테스트용 장애 주입 |
| `DISABLE_SELFHEALING_AUTH` | `False` | Permission Classes | 인증 바이패스 (테스트용) |

---

### 1.2 시스템 전체 제어 (6개)

| 환경변수 | 기본값 | 영향 컴포넌트 | 설명 |
|----------|:------:|---------------|------|
| `SELFHEALING_ENABLED` | `True` | Celery Signal Hooks | 전체 Self-Healing 활성화 |
| `SELFHEALING_CB_ENABLED` | `True` | Circuit Breaker 서비스 | CB 기록 활성화 |
| `SELFHEALING_DLQ_ENABLED` | `True` | DLQ 서비스 | DLQ 저장 활성화 |
| `SELFHEALING_DLQ_AUTO_ENQUEUE` | `True` | SelfHealingMiddleware | 자동 DLQ 적재 |
| `SELFHEALING_METRICS_ENABLED` | `True` | Metrics 수집기 | 메트릭 기록 활성화 |
| `SELFHEALING_FORENSICS_ENABLED` | `True` | Forensic Context | 포렌식 컨텍스트 캡처 |

---

### 1.3 Circuit Breaker 설정 (8개)

#### 1.3.1 일반 Circuit Breaker

| 환경변수 | 기본값 | 설명 |
|----------|:------:|------|
| `SELFHEALING_CB_FAILURE_THRESHOLD` | `5` | OPEN 전환 실패 횟수 |
| `SELFHEALING_CB_RECOVERY_TIMEOUT` | `60` | Half-Open 전환 대기 시간(초) |
| `SELFHEALING_CB_SUCCESS_THRESHOLD` | `2` | CLOSED 전환 성공 횟수 |
| `SELFHEALING_CIRCUIT_BREAKER_TIMEOUT` | `30` | CB 타임아웃(초) |

#### 1.3.2 Pool Circuit Breaker

| 환경변수 | 기본값 | 설명 |
|----------|:------:|------|
| `POOL_CB_FAILURE_THRESHOLD` | `3` | Pool CB OPEN 전환 실패 횟수 |
| `POOL_CB_SUCCESS_THRESHOLD` | `2` | Pool CB CLOSED 전환 성공 횟수 |
| `POOL_CB_RECOVERY_TIMEOUT` | `10` | Pool CB Half-Open 대기(초) |
| `POOL_CB_HALF_OPEN_MAX` | `3` | Half-Open 시 허용 요청 수 |
| `POOL_CB_CACHE_INTERVAL_MS` | `100` | Pool 상태 캐시 갱신 주기(ms) |
| `POOL_CB_STALE_MULTIPLIER` | `10` | Stale 캐시 임계값 배수 |
| `POOL_CB_CRITICAL_STALE_MS` | `5000` | Critical Stale 임계값(ms) |

---

### 1.4 인프라/스토리지 설정 (5개)

| 환경변수 | 기본값 | 영향 컴포넌트 | 설명 |
|----------|:------:|---------------|------|
| `SELFHEALING_REDIS_URL` | `redis://localhost:6379/0` | Redis 어댑터 | Redis 연결 URL |
| `SELFHEALING_WAL_DIR` | `/var/log/selfhealing/wal` | WAL 시스템 | Write-Ahead Log 저장 경로 |
| `SELFHEALING_ALLOW_MEMORY_ONLY` | `False` | 스토리지 백엔드 | 메모리 전용 모드 허용 |
| `SELFHEALING_HEALTH_CHECK_INTERVAL` | `5.0` | 헬스체크 | 헬스체크 주기(초) |
| `SELFHEALING_RECOVERY_JITTER_MAX` | `5.0` | 복구 시스템 | 복구 지터 최대값(초) |

---

### 1.5 Celery/Task 설정 (5개)

| 환경변수 | 기본값 | 영향 컴포넌트 | 설명 |
|----------|:------:|---------------|------|
| `SELFHEALING_TASK_DOMAIN_MAPPING` | `{}` | Signal Hooks | 태스크-도메인 매핑 JSON |
| `SELFHEALING_DLQ_MAX_RETRIES` | `5` | DLQ 리플레이 | 최대 재시도 횟수 |
| `SELFHEALING_DLQ_LOG_LEVEL` | `INFO` | DLQ 로깅 | DLQ 로그 레벨 |
| `SELFHEALING_METRICS_ADAPTER_TYPE` | `prometheus` | 메트릭 어댑터 | 메트릭 어댑터 타입 |
| `SELFHEALING_CORE_DOMAINS` | `[]` | 도메인 설정 | 핵심 도메인 목록 |

---

### 1.6 메트릭 Jitter 설정 (3개)

| 환경변수 | 기본값 | 설명 |
|----------|:------:|------|
| `SELFHEALING_METRICS_JITTER_ENABLED` | `True` | 메트릭 지터 활성화 |
| `SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS` | `60.0` | 지터 최대 지연(초) |
| `SELFHEALING_METRICS_JITTER_MIN_DELAY_SECONDS` | `0.0` | 지터 최소 지연(초) |

---

### 1.7 Chaos Engineering 설정 (9개)

| 환경변수 | 기본값 | 설명 |
|----------|:------:|------|
| `CHAOS_MODE` | `False` | Chaos 모드 전체 활성화 |
| `CHAOS_PAYMENT_CONFIRM_DELAY` | `False` | 결제 확인 지연 주입 |
| `CHAOS_PARTIAL_FAILURE` | `False` | 부분 실패 주입 |
| `CHAOS_RACE_AMPLIFICATION` | `False` | Race Condition 증폭 |
| `CHAOS_ASYNC_TASK_FAILURE` | `False` | 비동기 태스크 실패 주입 |
| `CHAOS_PAYMENT_CONFIRM_DELAY_MS` | `2000` | 결제 지연 시간(ms) |
| `CHAOS_RACE_DELAY_MS` | `500` | Race 지연 시간(ms) |
| `CHAOS_PARTIAL_FAILURE_PROB` | `0.3` | 부분 실패 확률 |
| `CHAOS_ASYNC_TASK_FAILURE_PROB` | `0.2` | 비동기 실패 확률 |
| `CHAOS_RACE_TRIGGER_PROB` | `0.4` | Race 트리거 확률 |

---

## 2. 환경별 권장 설정

### 2.1 프로덕션 환경

```bash
# 미들웨어 (모두 활성화)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED=true
SELFHEALING_ACTOR_MIDDLEWARE_ENABLED=true
SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED=true
SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED=true
SELFHEALING_AUDIT_MIDDLEWARE_ENABLED=true

# Chaos 비활성화 (필수!)
CHAOS_MIDDLEWARE_ENABLED=false
CHAOS_MODE=false
DISABLE_SELFHEALING_AUTH=false

# CB 설정 (프로덕션 권장)
SELFHEALING_CB_FAILURE_THRESHOLD=5
SELFHEALING_CB_RECOVERY_TIMEOUT=60
SELFHEALING_CB_SUCCESS_THRESHOLD=2

# Pool CB (프로덕션 권장)
POOL_CB_FAILURE_THRESHOLD=5
POOL_CB_RECOVERY_TIMEOUT=30
```

### 2.2 스테이징 환경

```bash
# 미들웨어 (모두 활성화)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED=true
SELFHEALING_ACTOR_MIDDLEWARE_ENABLED=true
SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED=true
SELFHEALING_POOL_TIMEOUT_MIDDLEWARE_ENABLED=true
SELFHEALING_AUDIT_MIDDLEWARE_ENABLED=true

# Chaos 활성화 가능 (테스트용)
CHAOS_MIDDLEWARE_ENABLED=true
CHAOS_MODE=true
DISABLE_SELFHEALING_AUTH=false

# CB 설정 (빠른 복구)
SELFHEALING_CB_FAILURE_THRESHOLD=3
SELFHEALING_CB_RECOVERY_TIMEOUT=30
```

### 2.3 개발/테스트 환경

```bash
# 미들웨어 (선택적)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED=false  # 개발 시 비활성화 가능
SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED=true
SELFHEALING_AUDIT_MIDDLEWARE_ENABLED=true

# 인증 바이패스 (테스트용)
DISABLE_SELFHEALING_AUTH=true

# Chaos 활성화 (HELLMODE 테스트)
CHAOS_MIDDLEWARE_ENABLED=true
CHAOS_MODE=true

# CB 설정 (빠른 테스트)
SELFHEALING_CB_FAILURE_THRESHOLD=2
SELFHEALING_CB_RECOVERY_TIMEOUT=5
POOL_CB_FAILURE_THRESHOLD=2
POOL_CB_RECOVERY_TIMEOUT=5
```

---

## 3. Feature Flag 의존성 관계

```
┌─────────────────────────────────────────────────────────────────┐
│                     Feature Flag 의존성                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  SELFHEALING_ENABLED=true                                       │
│       │                                                         │
│       ├──► SELFHEALING_CB_ENABLED=true                          │
│       │        └──► Circuit Breaker 서비스 활성화               │
│       │                                                         │
│       ├──► SELFHEALING_DLQ_ENABLED=true                         │
│       │        └──► DLQ 저장 & 리플레이 활성화                  │
│       │                                                         │
│       ├──► SELFHEALING_METRICS_ENABLED=true                     │
│       │        └──► Prometheus 메트릭 기록                      │
│       │                                                         │
│       └──► SELFHEALING_FORENSICS_ENABLED=true                   │
│                └──► 포렌식 컨텍스트 캡처                        │
│                                                                 │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  SELFHEALING_AUDIT_MIDDLEWARE_ENABLED=true                      │
│       │                                                         │
│       └──► SELFHEALING_ACTOR_MIDDLEWARE_ENABLED=true (권장)     │
│                └──► Audit 로그에 actor 정보 포함                │
│                                                                 │
│  ─────────────────────────────────────────────────────────────  │
│                                                                 │
│  CHAOS_MIDDLEWARE_ENABLED=true                                  │
│       │                                                         │
│       └──► X-Test-Mode 헤더 필수                                │
│                └──► hellmode, chaos-monkey, stress-test         │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 비활성화 시 영향 분석

| Feature Flag | 비활성화 시 영향 |
|--------------|------------------|
| `SELFHEALING_ENABLED` | 모든 Celery 태스크 Self-Healing 비활성화 |
| `SELFHEALING_CB_ENABLED` | CB 상태 변경 기록 안됨 (장애 감지 불가) |
| `SELFHEALING_DLQ_ENABLED` | 실패 요청 DLQ 저장 안됨 (복구 불가) |
| `SELFHEALING_AUDIT_MIDDLEWARE_ENABLED` | 해시 체인 Audit 비활성화 (감사 불가) |
| `SELFHEALING_TIERING_MIDDLEWARE_ENABLED` | 비상 모드 트래픽 제어 안됨 |
| `SELFHEALING_POOL_CB_MIDDLEWARE_ENABLED` | Pool 고갈 시 보호 없음 (시스템 멈춤 위험) |

---

## 5. 설정 파일 위치

| 파일 | 설정 유형 |
|------|----------|
| `myproject/settings/base.py` | 미들웨어 토글 (Django 설정) |
| `docker-compose.yml` | Chaos 환경변수 |
| `docker-compose.stage16.yml` | 테스트용 환경변수 |
| `packages/selfhealing-python/.../signal_hooks.py` | Celery 관련 환경변수 |
| `packages/selfhealing-python/.../pool_circuit_breaker.py` | Pool CB 환경변수 |

---

## 📚 관련 문서

- [11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md](11_PHASE2_MIDDLEWARE_ANALYSIS_RESULT.md) - 미들웨어 분석 결과
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [04_AUTONOMOUS_OPS.md](04_AUTONOMOUS_OPS.md) - 자율 운영 (Celery 설정)
- [05_RESILIENT_STORAGE_BACKEND.md](05_RESILIENT_STORAGE_BACKEND.md) - 스토리지 설정

---

*이 문서는 Phase 4 Feature Flag 정리의 결과물입니다.*

# Self-Healing 기능 시나리오 매핑 가이드

📅 **작성일**: 2025-12-28  
🎯 **목적**: 기존 시나리오 리팩토링 시 적용할 Self-Healing 기능 판단 기준 제공  
📋 **관련 문서**: [25_STAGE_REFACTORING_GUIDE.md](25_STAGE_REFACTORING_GUIDE.md)

---

## 1. Self-Healing 모듈 전체 목록 (27개)

`load_tests/utils/selfhealing/` 폴더 내 모든 모듈:

### 1.1 🔴 핵심 기능 (Core) - 필수
모든 시나리오에서 기본적으로 사용해야 하는 기능

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `circuit_breaker` | `CircuitBreakerClient` | 장애 격리 & 자동 복구 | `get_status()`, `force_open()`, `reset()` |
| `error_budget` | `ErrorBudgetClient` | 에러 허용량 관리 | `get_status()`, `consume()`, `reset()` |
| `emergency` | `EmergencyClient` | 비상 모드 관리 | `trigger()`, `release()`, `get_level()` |
| `dlq` | `DLQClient` | 실패 메시지 관리 | `stats()`, `list()`, `replay()`, `purge()` |
| `health` | `HealthClient` | 헬스 체크 | `ping()`, `liveness()`, `readiness()` |
| `auth` | `AuthClient` | 인증 관리 | `login()`, `refresh()`, `logout()` |
| `base` | `BaseClient` | HTTP 클라이언트 베이스 | `api_get()`, `api_post()`, `xtest_post()` |
| `config` | `SelfHealingConfig` | 전역 설정 관리 | `configure()`, `get_config()`, `reset_config()` |

### 1.2 🟠 관찰 기능 (Observability)
테스트 결과 분석 및 모니터링용

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `observability` | `ObservabilityClient` | 스냅샷, 타임라인, 포스트모템 | `snapshot()`, `timeline()`, `postmortem()` |
| `dashboard` | `DashboardClient` | 실시간 상태 대시보드, Pool CB | `get_summary()`, `control_action()`, `pool_cb_status()` |
| `alerts` | `AlertsClient` | 알림 시스템 | `list()`, `acknowledge()`, `configure()` |
| `async_logger` | `AsyncHealingLogger` | 비동기 로깅 | `log_event()`, `flush()`, `EventSeverity` |

### 1.3 🟡 장애 주입 (Chaos Engineering)
카오스 엔지니어링용

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `chaos` | `ChaosClient` | 장애 주입 API | `inject_failure()`, `inject_latency()`, `stop_all()` |
| `xtest` | `XTestClient` | X-Test-Mode 극한 테스트 | `inject_cb_failure()`, `fast_fail_test()`, `cascade_failure()` |
| `corruption_shield` | `CorruptionShieldClient` | 3계층 데이터 무결성 보호 (L1 스키마, L2 비즈니스, L3 이상탐지) | `validate()`, `get_stats()`, `set_layer_enabled()` |

### 1.4 🟢 최적화 (Optimization)
성능 및 안정성 향상용

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `state_cache` | `CBStateCache` | CB 상태 캐싱 (고빈도 조회 최적화) | `get_state()`, `invalidate()`, `warm_up()` |
| `adaptive_jitter` | `AdaptiveJitter` | 적응형 지터 (재시도 폭풍 방지) | `get_delay()`, `record_outcome()`, `SystemState` |
| `rate_limiter` | `RateLimiterClient` | 요청 제한 | `check()`, `set_limit()`, `get_stats()` |
| `throttle` | `AdaptiveThrottleClient` | Netflix Gradient Algorithm 적응형 스로틀링 | `check()`, `record_rtt()`, `get_status()` |
| `defaults` | `SafeDefaults` | 안전 기본값 제공 | 상수 값들 |

### 1.5 🔵 설정 관리 (Configuration)
동적 설정 및 런타임 관리용

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `runtime_config` | `RuntimeConfigClient` | CB, DLQ, Retry, SLA, Rate Limit 동적 설정 | `get_all()`, `set_circuit_breaker()`, `set_sla()` |
| `system` | `SystemClient` | 시스템 설정, 로그 레벨, 진단 | `get_config()`, `set_log_level()`, `run_diagnostics()` |
| `tiering` | `TieringClient` | API Tier 정의/매핑/오버라이드 | `get_definitions()`, `set_mappings()`, `dry_run()` |

### 1.6 🟣 고급 기능 (Advanced)
특수 시나리오용

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `governance` | `GovernanceClient` | 변경 관리, 거버넌스 정책, 감사 로그 | `create_change_request()`, `approve()`, `get_audit_logs()` |
| `reconciliation` | `ReconciliationClient` | Shadow Budget, FailSafe, Excluded Periods | `get_status()`, `add_failsafe_period()`, `exclude_period()` |
| `l2_storage` | `L2StorageClient` | L2 저장소, Shadow Log, Drift Reconciliation | `get_status()`, `sync()`, `list_shadow_logs()` |

### 1.7 ⚫ 통합 컨트롤러 (Controller)
극한 테스트용 통합 관리

| 모듈 | 클래스 | 역할 | API 예시 |
|------|--------|------|----------|
| `controller` | `SelfHealingController` | **모든 기능 통합 컨트롤러** | `inject_chaos_for_phase()`, `record_response_time()`, `escalate_emergency()` |

---

## 1.8 모듈 Import 가이드

```python
# === 방법 1: SelfHealingClient (권장 - 모든 기능 통합) ===
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient()
client.login("admin", "password")

# 각 기능에 접근
client.circuit_breaker.get_status("payment")
client.dlq.stats()
client.emergency.trigger("LEVEL_1", "test")
client.error_budget.get_status()
client.observability.snapshot()
client.chaos.inject_failure("payment", "timeout")

# === 방법 2: SelfHealingController (극한 테스트용) ===
from load_tests.utils.selfhealing import SelfHealingController, get_controller

controller = get_controller()
controller.inject_chaos_for_phase("spike", cycle=0)
controller.record_response_time(350.0, service="payment")

# === 방법 3: 개별 모듈 (선택적 사용) ===
from load_tests.utils.selfhealing import (
    CBStateCache,           # 캐싱
    AdaptiveJitter,         # 지터
    AsyncHealingLogger,     # 로깅
    SafeDefaults,           # 기본값
)
```

---

## 2. 시나리오 유형별 권장 조합

### 2.1 🟢 Smoke Test (Stage 0)
**목적**: 기본 기능 동작 확인

```python
# 필수 모듈
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient()

# 필수 체크
client.health.ping()
client.health.liveness()
client.health.readiness()

# 보고서
from load_tests.reports import SimpleReport, LocalFileDispatcher
```

**Self-Healing 체크리스트**:
- [ ] `health.ping()` 응답 확인
- [ ] `health.liveness()` / `health.readiness()` 확인
- [ ] 기본 API 응답 시간 측정

---

### 2.2 🔵 Load Test (Stage 1, 3, 9, 11)
**목적**: 부하 처리 능력 검증

```python
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient()

# 필수 모듈
client.health          # 헬스 체크
client.circuit_breaker # 장애 격리
client.error_budget    # 에러 허용량
client.rate_limiter    # 요청 제한

# 최적화 모듈
from load_tests.utils.selfhealing import (
    AdaptiveJitter,    # 재시도 폭풍 방지
    CBStateCache,      # CB 상태 캐싱
    AdaptiveThrottleClient,  # Netflix Gradient 스로틀링
)

# 보고서
from load_tests.reports import SelfHealingReport, LocalFileDispatcher
```

**Self-Healing 체크리스트**:
- [ ] `circuit_breaker.get_status()` - CB 상태 모니터링
- [ ] `error_budget.get_status()` - 에러 버짓 유지
- [ ] `rate_limiter.check()` - 요청 제한 동작
- [ ] `AdaptiveJitter.get_delay()` - 재시도 시 지터 적용
- [ ] P99 응답시간 SLA 달성
- [ ] 에러율 임계값 미달

---

### 2.3 🟠 Chaos Test (Stage 6, 16, 18, 19, 24-28, 34, 38-43, 47-51)
**목적**: 장애 상황 복구 능력 검증

```python
from load_tests.utils.selfhealing import SelfHealingController, get_controller

controller = get_controller()

# 필수 모듈 (Controller 내부에서 모두 사용)
# - circuit_breaker: CB 상태 전이
# - emergency: 비상 모드 활성화/해제
# - dlq: 실패 메시지 처리
# - chaos: 장애 주입
# - xtest: 극한 테스트 모드

# 또는 개별 클라이언트
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient(auth_mode="xtest")  # X-Test-Mode 활성화

client.chaos.inject_failure("payment", "timeout", duration=30)
client.xtest.inject_cb_failure("payment", failure_rate=0.5)
client.xtest.cascade_failure(["payment", "inventory", "shipping"])

client.emergency.trigger("LEVEL_1", "카오스 테스트")
client.emergency.trigger("LEVEL_2", "에스컬레이션")
client.emergency.release("복구 완료")

client.corruption_shield.validate(data)  # 데이터 무결성 검증

# 관찰 모듈
client.observability.snapshot("before_chaos")
client.observability.snapshot("after_chaos")
client.observability.postmortem("chaos_test_001")

# 보고서
from load_tests.reports import PlatinumReport, LocalFileDispatcher
```

**Self-Healing 체크리스트**:
- [ ] `circuit_breaker` - Open → Half-Open → Close 전이
- [ ] `emergency` - LEVEL_1/2/3 활성화/해제
- [ ] `dlq` - 아이템 적재 및 replay 성공
- [ ] `chaos` - 장애 주입 및 복구
- [ ] `xtest` - 극한 테스트 모드 동작
- [ ] `corruption_shield` - 데이터 무결성 유지
- [ ] `observability` - 스냅샷 및 포스트모템 생성
- [ ] 복구 시간 SLA (< 2분)

---

### 2.4 🟣 Integration Test (Stage 2, 5, 7, 8, 10, 14, 15, 17, 20, 23, 29-37, 44-46)
**목적**: 시스템 간 통합 검증

```python
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient()

# 필수 모듈
client.circuit_breaker    # 외부 서비스 호출
client.dlq                # 비동기 메시지 처리
client.observability      # 분석 및 포스트모템

# 시나리오별 선택 모듈

# 웹훅 테스트 (Stage 8, 20)
client.alerts             # 알림 검증
from load_tests.utils.selfhealing import AsyncHealingLogger

# 캐시 테스트 (Stage 17, 35)
from load_tests.utils.selfhealing import CBStateCache
client.runtime_config.set_cache()

# 스키마 호환성 (Stage 37)
client.reconciliation     # Shadow Budget, FailSafe
client.l2_storage         # L2 저장소 동기화

# 감사/거버넌스 (Stage 45, 46)
client.governance.create_change_request()
client.governance.get_audit_logs()

# Tier 관리 (API 계층화)
client.tiering.get_definitions()
client.tiering.set_mappings()

# 동적 설정 변경
client.runtime_config.set_circuit_breaker(failure_threshold=5)
client.runtime_config.set_sla(p99_threshold_ms=300)

# 시스템 진단
client.system.run_diagnostics()
client.system.get_config_history()

# 보고서
from load_tests.reports import SelfHealingReport, LocalFileDispatcher
```

**Self-Healing 체크리스트**:
- [ ] `circuit_breaker` - 외부 호출 격리
- [ ] `dlq` - 비동기 처리 실패 복구
- [ ] `reconciliation` - Shadow Budget 정확성
- [ ] `governance` - 변경 관리 감사
- [ ] `runtime_config` - 동적 설정 반영
- [ ] `l2_storage` - 저장소 동기화 확인

---

### 2.5 🔴 Platinum Grade Test (Stage 12+ EXTREME)
**목적**: 최고 수준 SLA 달성 검증

```python
# === 전체 스택 사용 ===
from load_tests.utils.selfhealing import (
    # 통합 컨트롤러 (권장)
    SelfHealingController,
    get_controller,
    
    # 또는 전체 클라이언트
    SelfHealingClient,
)

# === 통합 컨트롤러 방식 (권장) ===
controller = get_controller()

# 페이즈별 카오스 주입
controller.inject_chaos_for_phase("spike", cycle=0)
controller.inject_chaos_for_phase("sustain", cycle=1)
controller.inject_chaos_for_phase("recovery", cycle=2)

# 적응형 힐링 (응답시간 기반)
controller.record_response_time(350.0, service="payment")

# V2.8 Aggressive Healing
controller.aggressive_rate_cut(0.5)
controller.escalate_emergency("LEVEL_2")

# === 전체 클라이언트 방식 ===
client = SelfHealingClient(auth_mode="xtest")

# 핵심 모듈
client.circuit_breaker    # CB 상태 전이
client.error_budget       # 에러 버짓 관리
client.emergency          # 비상 모드
client.dlq                # DLQ 처리

# 장애 주입 모듈
client.chaos              # 기본 장애 주입
client.xtest              # 극한 테스트 모드
client.corruption_shield  # 데이터 무결성 보호

# 관찰 모듈
client.observability      # 스냅샷, 포스트모템
client.dashboard          # 실시간 대시보드
client.alerts             # 알림
from load_tests.utils.selfhealing import AsyncHealingLogger

# 최적화 모듈
from load_tests.utils.selfhealing import (
    CBStateCache,           # CB 상태 캐싱
    AdaptiveJitter,         # 적응형 지터
    AdaptiveThrottleClient, # Netflix Gradient 스로틀링
    SafeDefaults,           # 안전 기본값
)

# 설정 관리 모듈
client.runtime_config     # 동적 설정
client.system             # 시스템 진단
client.tiering            # API 계층화

# 고급 모듈
client.governance         # 거버넌스 정책
client.reconciliation     # Shadow Budget
client.l2_storage         # L2 저장소

# 보고서
from load_tests.reports import (
    PlatinumReport,
    LocalFileDispatcher,
    compare_reports,
)
```

**Self-Healing 체크리스트 (전체)**:
- [ ] `circuit_breaker` - Open/Close/Half-Open 전이 정상
- [ ] `error_budget` - 소진 및 복구 사이클
- [ ] `emergency` - LEVEL_1/2/3 에스컬레이션
- [ ] `dlq` - 적재/replay/purge 동작
- [ ] `chaos` + `xtest` - 장애 주입 및 복구
- [ ] `corruption_shield` - 데이터 무결성 100%
- [ ] `observability` - 스냅샷/타임라인/포스트모템
- [ ] `CBStateCache` - 캐시 히트율 > 80%
- [ ] `AdaptiveJitter` - 재시도 폭풍 방지
- [ ] `AdaptiveThrottleClient` - RTT 기반 스로틀링
- [ ] `runtime_config` - 동적 설정 변경 반영
- [ ] `governance` - 감사 로그 기록
- [ ] `reconciliation` - Shadow Budget 정확성
- [ ] SLA Hard-Cap (P99 ≤ 250ms)
- [ ] 복구 시간 < 2분

---

## 3. 의사결정 플로우차트 (완전판)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        시나리오 리팩토링 시작                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ [기본 설정]                                                                  │
│  ✅ SelfHealingClient 또는 SelfHealingController                             │
│  ✅ health.ping(), liveness(), readiness()                                   │
│  ✅ config (SelfHealingConfig)                                               │
│  ✅ auth (로그인/인증)                                                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q1. 외부 서비스/API 호출이 있는가?                                          │
│     YES → ✅ circuit_breaker (상태 모니터링, 강제 전이)                      │
│         → ✅ error_budget (에러 허용량 관리)                                 │
│         → 🔶 CBStateCache (고빈도 조회 최적화)                               │
│         → 🔶 AdaptiveJitter (재시도 시 지터)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q2. 비동기 작업/메시지 큐가 있는가?                                         │
│     YES → ✅ dlq (적재, 조회, replay, purge)                                 │
│         → 🔶 AsyncHealingLogger (비동기 로깅)                                │
│         → 🔶 l2_storage (Shadow Log, 동기화)                                 │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q3. 장애 주입이 필요한가? (Chaos Testing)                                    │
│     YES → ✅ chaos (inject_failure, inject_latency)                          │
│         → ✅ xtest (X-Test-Mode 극한 테스트)                                  │
│         → ✅ emergency (LEVEL_1/2/3 에스컬레이션)                             │
│         → 🔶 corruption_shield (데이터 무결성 검증)                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q4. SLA 검증이 필요한가?                                                     │
│     YES → ✅ error_budget (소진/복구 모니터링)                               │
│         → ✅ observability (스냅샷, 타임라인)                                 │
│         → ✅ PlatinumReport 또는 SelfHealingReport                           │
│         → 🔶 runtime_config (동적 SLA 설정)                                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q5. 고성능/대규모 테스트인가? (동시 사용자 100+)                             │
│     YES → ✅ CBStateCache (CB 상태 캐싱)                                     │
│         → ✅ AdaptiveJitter (재시도 폭풍 방지)                                │
│         → ✅ AdaptiveThrottleClient (Netflix Gradient 스로틀링)              │
│         → ✅ rate_limiter (요청 제한)                                         │
│         → ✅ AsyncHealingLogger (고성능 비동기 로깅)                          │
│         → 🔶 SafeDefaults (안전 기본값)                                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q6. 데이터 일관성이 중요한가?                                                │
│     YES → ✅ corruption_shield (L1 스키마, L2 비즈니스, L3 이상탐지)          │
│         → ✅ reconciliation (Shadow Budget, FailSafe)                        │
│         → 🔶 l2_storage (Drift Reconciliation)                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q7. 동적 설정 변경이 필요한가?                                               │
│     YES → ✅ runtime_config (CB, DLQ, Retry, SLA, Rate Limit 설정)            │
│         → ✅ system (시스템 설정, 로그 레벨, 진단)                            │
│         → 🔶 tiering (API Tier 정의/매핑)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q8. 거버넌스/감사가 필요한가?                                                │
│     YES → ✅ governance (변경 요청, 승인, 감사 로그)                          │
│         → ✅ dashboard (감사 로그 조회)                                       │
│         → 🔶 alerts (알림 구성/확인)                                          │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q9. 분석/디버깅이 필요한가?                                                  │
│     YES → ✅ observability (스냅샷, 타임라인, 포스트모템)                     │
│         → ✅ dashboard (대시보드 요약)                                        │
│         → ✅ system (진단 실행, 설정 이력)                                    │
│         → 🔶 AsyncHealingLogger (상세 로깅)                                   │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Q10. Platinum Grade 목표인가? (SLA Hard-Cap)                                 │
│      YES → ⭐ SelfHealingController (전체 스택 통합)                          │
│          → ✅ 위의 모든 모듈 활성화                                           │
│          → ✅ PlatinumReport 사용                                             │
│          → ✅ compare_reports() 회귀 분석                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           보고서 생성                                         │
│                                                                               │
│  Smoke Test      → SimpleReport                                              │
│  Load Test       → SelfHealingReport                                         │
│  Chaos Test      → PlatinumReport                                            │
│  Integration     → SelfHealingReport                                         │
│  Platinum        → PlatinumReport + compare_reports()                        │
└─────────────────────────────────────────────────────────────────────────────┘

Legend:
  ✅ 필수 (반드시 적용)
  🔶 권장 (상황에 따라 적용)
  ⭐ 통합 솔루션 (하나로 모든 것 해결)
```

---

## 4. 빠른 참조 테이블 (완전판)

### 4.1 시나리오별 모듈 매핑

| 시나리오 특성 | 필수 모듈 | 권장 모듈 | 보고서 타입 |
|--------------|----------|----------|------------|
| 기본 Smoke | `health`, `auth`, `config` | - | SimpleReport |
| 부하 Load | `cb`, `error_budget`, `cache`, `jitter` | `throttle`, `rate_limiter` | SelfHealingReport |
| 카오스 Chaos | `cb`, `chaos`, `xtest`, `emergency`, `dlq` | `observability`, `corruption_shield` | PlatinumReport |
| 통합 Integration | `cb`, `dlq`, `observability` | `reconciliation`, `governance` | SelfHealingReport |
| Platinum | `SelfHealingController` (전체) | `compare_reports()` | PlatinumReport |

### 4.2 모듈 → 클라이언트 매핑

| 모듈 파일 | 클라이언트 클래스 | 핵심 메서드 |
|----------|-----------------|------------|
| `adaptive_jitter.py` | `AdaptiveJitter` | `backoff()`, `reset()`, `stats()` |
| `alerts.py` | `AlertsClient` | `get_config()`, `check_enabled()` |
| `async_logger.py` | `AsyncHealingLogger` | `log()`, `close()` |
| `auth.py` | `AuthClient` | `login()`, `get_token()` |
| `base.py` | `SelfHealingClient` | (모든 하위 클라이언트 통합) |
| `chaos.py` | `ChaosClient` | `inject_failure()`, `inject_latency()`, `reset()` |
| `circuit_breaker.py` | `CircuitBreakerClient` | `get_states()`, `force_transition()`, `reset()` |
| `config.py` | `SelfHealingConfig` | (환경 변수 로드) |
| `controller.py` | `SelfHealingController` | (전체 스택 오케스트레이션) |
| `corruption_shield.py` | `CorruptionShieldClient` | `validate_l1()`, `validate_l2()`, `validate_l3()` |
| `dashboard.py` | `DashboardClient` | `get_summary()`, `audit_logs()` |
| `defaults.py` | `SafeDefaults`, `SafetyLevel` | (안전 기본값) |
| `dlq.py` | `DLQClient` | `enqueue()`, `list()`, `replay()`, `purge()` |
| `emergency.py` | `EmergencyClient` | `escalate()`, `get_current_level()`, `recover()` |
| `error_budget.py` | `ErrorBudgetClient` | `get_budget()`, `consume()`, `recover()` |
| `governance.py` | `GovernanceClient` | `request_change()`, `approve()`, `list_pending()` |
| `health.py` | `HealthClient` | `ping()`, `liveness()`, `readiness()`, `selfhealing()` |
| `l2_storage.py` | `L2StorageClient` | `push_shadow_log()`, `sync_drift()` |
| `observability.py` | `ObservabilityClient` | `snapshot()`, `timeline()`, `postmortem()` |
| `rate_limiter.py` | `RateLimiterClient` | `check()`, `reset()` |
| `reconciliation.py` | `ReconciliationClient` | `shadow_budget()`, `failsafe_integrity()` |
| `runtime_config.py` | `RuntimeConfigClient` | `get()`, `set()`, `list()`, `reset()` |
| `state_cache.py` | `CBStateCache` | `get()`, `invalidate()`, `hit_ratio()` |
| `system.py` | `SystemClient` | `info()`, `set_log_level()`, `run_diagnostics()` |
| `throttle.py` | `AdaptiveThrottleClient` | `update_rtt()`, `calculate_limit()`, `reset()` |
| `tiering.py` | `TieringClient` | `get_tier()`, `set_tier()`, `list_tiers()` |
| `xtest.py` | `XTestClient` | `start_mode()`, `stop_mode()`, `get_status()` |

### 4.3 자주 사용하는 조합

| 목적 | 조합 |
|-----|-----|
| **빠른 헬스체크** | `health` + `auth` |
| **표준 부하 테스트** | `cb` + `error_budget` + `cache` + `jitter` |
| **장애 복구 검증** | `chaos` + `emergency` + `cb` + `dlq` |
| **SLA 검증** | `error_budget` + `observability` + `PlatinumReport` |
| **고성능 테스트** | `cache` + `jitter` + `throttle` + `AsyncHealingLogger` |
| **데이터 무결성** | `corruption_shield` + `reconciliation` + `l2_storage` |
| **운영 거버넌스** | `governance` + `dashboard` + `system` + `alerts` |
| **완전 Platinum** | `SelfHealingController` (모든 것 포함) |

---

## 5. 마이그레이션 가이드

### 5.1 기존 시나리오 리팩토링 프로세스

```
┌─────────────────────────────────────────────────────────────┐
│                    리팩토링 워크플로우                       │
├─────────────────────────────────────────────────────────────┤
│ 1. 기존 시나리오 분석                                        │
│    - 어떤 API 호출하는지?                                    │
│    - 어떤 에러 처리하는지?                                   │
│    - 어떤 메트릭 수집하는지?                                 │
│                                                              │
│ 2. Self-Healing 모듈 선택 (플로우차트 기준)                  │
│    - 필수 모듈 목록 작성                                     │
│    - 권장 모듈 검토                                          │
│                                                              │
│ 3. 코드 리팩토링                                             │
│    - 기존 HTTP 클라이언트 → SelfHealingClient 교체           │
│    - 기존 에러 처리 → CB + DLQ 패턴 적용                     │
│    - 기존 로깅 → observability 스냅샷 적용                   │
│                                                              │
│ 4. 보고서 통합                                               │
│    - 새 Report Architecture 적용                             │
│    - compare_reports() 로 회귀 분석                          │
│                                                              │
│ 5. 검증                                                       │
│    - 모든 Self-Healing 기능 동작 확인                        │
│    - 리포트 생성 확인                                         │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 우선순위 기준

| 우선순위 | 시나리오 특성 | 이유 |
|---------|-------------|-----|
| P0 (즉시) | Platinum 테스트 (Stage42, Stage43) | SLA Hard-Cap 검증 핵심 |
| P1 (1주 내) | Chaos 테스트 (Stage6, Stage16, Stage38) | 장애 복구 검증 핵심 |
| P2 (2주 내) | 핵심 API 테스트 (Stage12, Stage7, Stage8) | 비즈니스 로직 검증 |
| P3 (1개월 내) | 나머지 Load 테스트 | 점진적 마이그레이션 |
| P4 (유지보수) | Smoke/Unit 테스트 | 필요시 적용 |

### 5.3 리팩토링 체크리스트

#### Before Refactoring
- [ ] 기존 시나리오 코드 백업 (git branch)
- [ ] 현재 테스트 결과 기록 (baseline)
- [ ] 필요한 Self-Healing 모듈 목록 작성

#### During Refactoring
- [ ] `SelfHealingClient` 또는 `SelfHealingController` 초기화
- [ ] 각 API 호출에 적절한 모듈 적용
- [ ] 에러 처리 패턴 통일 (CB + DLQ)
- [ ] 메트릭 수집 코드 통일 (observability)

#### After Refactoring
- [ ] 새 Report Architecture로 보고서 생성
- [ ] baseline과 비교 (`compare_reports()`)
- [ ] 회귀 없음 확인
- [ ] Self-Healing 기능 동작 확인
- [ ] 문서 업데이트

### 5.4 예제: Stage 리팩토링 템플릿

```python
"""
Stage XX Refactoring Template
리팩토링 전: 기본 HTTP 호출만 수행
리팩토링 후: Self-Healing 기능 통합
"""
from locust import HttpUser, task, between

# Self-Healing imports
from load_tests.utils.selfhealing import (
    SelfHealingConfig,
    SelfHealingClient,
    # 또는 전체 스택이 필요하면:
    # SelfHealingController,
)
from load_tests.reports import (
    SelfHealingReport,  # 또는 PlatinumReport
    SelfHealingMetrics,
    LocalFileDispatcher,
)


class RefactoredStageUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        # 1. Self-Healing 클라이언트 초기화
        self.config = SelfHealingConfig()
        self.client_sh = SelfHealingClient(self.config)
        
        # 2. 헬스체크
        assert self.client_sh.health.ping()
        
        # 3. 인증
        self.client_sh.auth.login("test@example.com", "password")
        
        # 4. 메트릭 수집 시작
        self.client_sh.observability.snapshot("start")
    
    @task
    def test_with_selfhealing(self):
        # Circuit Breaker 상태 확인
        cb_states = self.client_sh.circuit_breaker.get_states()
        
        # Error Budget 확인
        budget = self.client_sh.error_budget.get_budget("api_name")
        
        # 실제 API 호출 (Self-Healing 래핑)
        try:
            response = self.client.get("/api/endpoint")
            # ...
        except Exception as e:
            # DLQ에 적재
            self.client_sh.dlq.enqueue({
                "error": str(e),
                "endpoint": "/api/endpoint",
            })
    
    def on_stop(self):
        # 5. 최종 스냅샷
        self.client_sh.observability.snapshot("end")
        
        # 6. 보고서 생성
        metrics = SelfHealingMetrics(
            # ... 메트릭 수집
        )
        report = SelfHealingReport(metrics=metrics)
        
        dispatcher = LocalFileDispatcher(output_dir="./reports")
        dispatcher.dispatch(report)
```

---

## 6. 자주 묻는 질문 (FAQ)

### Q1. SelfHealingClient vs SelfHealingController?
- **SelfHealingClient**: 개별 모듈 선택 사용 (가볍고 유연)
- **SelfHealingController**: 전체 스택 통합 (Platinum Grade 목표)

### Q2. 어떤 보고서 타입을 사용해야 하나요?
- **SimpleReport**: Smoke, 기본 테스트
- **SelfHealingReport**: Load, Integration 테스트
- **PlatinumReport**: Chaos, Platinum (SLA Hard-Cap) 테스트

### Q3. compare_reports()는 언제 사용하나요?
- 이전 결과와 비교하여 성능 회귀 감지 시
- SLA 위반 여부 확인 시
- 리팩토링 전후 비교 시

### Q4. 모든 모듈을 사용해야 하나요?
- 아니요. 플로우차트를 따라 필요한 모듈만 선택
- 과도한 모듈 사용은 오히려 복잡도 증가

### Q5. 기존 테스트 결과와 호환되나요?
- 예. `adapters/legacy_stats.py`의 `adapt_extreme_stats()` 사용
- 기존 `_extreme_stats` 포맷 → 새 스키마 변환 지원

---

## 7. 백엔드 API vs 클라이언트 Gap 분석

### 7.1 분석 결과 요약 (완전 커버리지 달성 ✅)

| 항목 | 개수 | 비율 | 상태 |
|-----|-----|------|------|
| **총 백엔드 API** | ~150개 | 100% | - |
| **구현된 클라이언트 API** | ~150개 | **100%** | ✅ 완료 |
| **Gap (누락)** | **0개** | 0% | ✅ 없음 |
| **이전 추가 API** | 27개 | - | ✅ 추가됨 |

### 7.2 카테고리별 완전 커버리지

| 카테고리 | 백엔드 API | 클라이언트 파일 | 커버리지 |
|---------|-----------|---------------|----------|
| **Control API** | 7개 | `circuit_breaker.py`, `dashboard.py` | ✅ 100% |
| **Health & Metrics** | 7개 | `health.py`, `observability.py` | ✅ 100% |
| **DLQ** | 9개 | `dlq.py` | ✅ 100% |
| **Dashboard** | 1개 | `dashboard.py` | ✅ 100% |
| **Pool Circuit Breaker** | 2개 | `dashboard.py`, `circuit_breaker.py` | ✅ 100% |
| **System Control** | 5개 | `system.py` | ✅ 100% |
| **Runtime Config** | 34개 | `runtime_config.py` | ✅ 100% |
| **Governance** | 9개 | `governance.py` | ✅ 100% |
| **Emergency** | 8개 | `emergency.py` | ✅ 100% |
| **Error Budget & Deployment** | 11개 | `error_budget.py` | ✅ 100% |
| **Reconciliation** | 9개 | `reconciliation.py` | ✅ 100% |
| **Chaos Engineering** | 20개 | `chaos.py` | ✅ 100% |
| **L2 Storage** | 18개 | `l2_storage.py` | ✅ 100% |
| **X-Test Mode** | 13개 | `xtest.py` | ✅ 100% |
| **Tiering** | 8개 | `tiering.py` | ✅ 100% |

### 7.3 Gap 분석 수행 방법

백엔드 API와 클라이언트 모듈 간의 Gap을 체계적으로 분석하는 방법:

```
┌─────────────────────────────────────────────────────────────┐
│                    Gap 분석 프로세스                         │
├─────────────────────────────────────────────────────────────┤
│ 1. 백엔드 urls.py 파일 분석                                  │
│    - packages/selfhealing-python/src/selfhealing/           │
│      api/django/urls.py                                     │
│    - 모든 path() 패턴 추출 (~150개 엔드포인트)               │
│                                                              │
│ 2. 클라이언트 모듈 분석                                      │
│    - load_tests/utils/selfhealing/*.py (27개 파일)           │
│    - 각 클라이언트의 API 호출 메서드 확인                    │
│                                                              │
│ 3. 매핑 비교                                                 │
│    - 백엔드 URL ↔ 클라이언트 메서드 1:1 매핑                 │
│    - 누락된 API 식별                                         │
│                                                              │
│ 4. 클라이언트 확장 (완료)                                    │
│    - ChaosClient: 18개 메서드 추가                           │
│    - GovernanceClient: 9개 메서드 추가                       │
│                                                              │
│ 5. 검증: 100% 커버리지 달성                                  │
└─────────────────────────────────────────────────────────────┘
```

### 7.3 추가된 ChaosClient API (18개)

`chaos.py`에 다음 API가 추가되었습니다:

| 메서드 | 엔드포인트 | 설명 |
|--------|-----------|------|
| `get_safety_guard_config()` | `GET /chaos/config/safety-guard/` | 안전장치 설정 조회 |
| `set_safety_guard_config()` | `POST /chaos/config/safety-guard/` | 안전장치 설정 변경 |
| `get_blast_radius_policy()` | `GET /chaos/config/blast-radius/` | 폭발반경 정책 조회 |
| `set_blast_radius_policy()` | `POST /chaos/config/blast-radius/` | 폭발반경 정책 설정 |
| `get_scheduler_config()` | `GET /chaos/config/scheduler/` | 스케줄러 설정 조회 |
| `set_scheduler_config()` | `POST /chaos/config/scheduler/` | 스케줄러 설정 변경 |
| `get_report_config()` | `GET /chaos/config/reports/` | 리포트 설정 조회 |
| `set_report_config()` | `POST /chaos/config/reports/` | 리포트 설정 변경 |
| `get_stop_conditions_config()` | `GET /chaos/config/stop-conditions/` | 정지 조건 설정 조회 |
| `set_stop_conditions_config()` | `POST /chaos/config/stop-conditions/` | 정지 조건 설정 변경 |
| `get_ttl_config()` | `GET /chaos/config/ttl/` | TTL 설정 조회 |
| `set_ttl_config()` | `POST /chaos/config/ttl/` | TTL 설정 변경 |
| `get_dry_run_config()` | `GET /chaos/config/dry-run/` | 드라이런 설정 조회 |
| `set_dry_run_config()` | `POST /chaos/config/dry-run/` | 드라이런 설정 변경 |
| `approve_schedule()` | `POST /chaos/schedules/{id}/approve/` | 스케줄 승인 |
| `execute_schedule()` | `POST /chaos/schedules/{id}/execute/` | 스케줄 즉시 실행 |
| `get_chaos_kill_switch()` | `GET /chaos/kill-switch/` | 카오스 킬스위치 상태 |
| `set_chaos_kill_switch()` | `POST /chaos/kill-switch/` | 카오스 킬스위치 설정 |
| `kill_all_chaos()` | `POST /chaos/control/kill-all/` | 모든 카오스 중지 |
| `chaos_safety_check()` | `GET /chaos/safety-check/` | 안전 체크 조회 |
| `run_chaos_safety_check()` | `POST /chaos/safety-check/` | 안전 체크 실행 |
| `check_blast_radius()` | `POST /chaos/blast-radius/check/` | 폭발반경 체크 |
| `list_chaos_reports()` | `GET /chaos/reports/` | 리포트 목록 |
| `get_chaos_report()` | `GET /chaos/reports/{id}/` | 리포트 상세 |
| `generate_chaos_report()` | `POST /chaos/reports/generate/` | 리포트 생성 |
| `get_grade_history()` | `GET /chaos/reports/grades/` | 등급 이력 |
| `list_pending_approvals()` | `GET /chaos/pending-approvals/` | 대기중 승인 목록 |

### 7.4 추가된 GovernanceClient API (9개)

`governance.py`에 다음 API가 추가되었습니다:

| 메서드 | 엔드포인트 | 설명 |
|--------|-----------|------|
| `get_metrics_status()` | `GET /metrics/status/` | 메트릭 상태 조회 |
| `sync_metrics()` | `POST /metrics/sync/` | 메트릭 동기화 (Deprecated) |
| `get_drift_report()` | `GET /metrics/drift-report/` | 드리프트 리포트 (Deprecated) |
| `reconcile()` | `POST /governance/reconcile/` | 거버넌스 정합성 조정 |
| `get_governance_mode()` | `GET /governance/mode/` | 거버넌스 모드 조회 |
| `set_governance_mode()` | `POST /governance/mode/` | 거버넌스 모드 변경 |
| `get_governance_status()` | `GET /governance/status/` | RBAC 상태 조회 |
| `list_approval_requests()` | `GET /governance/approval-requests/` | 승인 요청 목록 |
| `approve_approval_request()` | `POST /governance/approval-requests/{id}/approve/` | 승인 요청 승인 |
| `reject_approval_request()` | `POST /governance/approval-requests/{id}/reject/` | 승인 요청 거부 |

### 7.5 미구현 API (선택사항)

| 카테고리 | API | 이유 |
|---------|-----|------|
| **Stress Test** | `/stress/*` | DEBUG 전용, 프로덕션 미사용 |

---

## 8. 상세 API 매핑표

### 8.1 Control API (7개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `POST /control/` | `control_action()`, `block_service()`, `allow_service()`, `quick_reset()` | `circuit_breaker.py`, `dashboard.py` |
| `GET /status/` | `get_all_statuses()` | `circuit_breaker.py`, `dashboard.py` |
| `GET /status/{service}/` | `get_status()` | `circuit_breaker.py`, `dashboard.py` |
| `GET /audit/` | `get_audit_logs()` | `dashboard.py`, `governance.py` |
| `POST /allow/{service}/` | `allow_service()` | `circuit_breaker.py` |
| `POST /block/{service}/` | `block_service()` | `circuit_breaker.py` |
| `POST /reset/{service}/` | `quick_reset()` | `circuit_breaker.py` |

### 8.2 Health & Metrics (7개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET /health/` | `health()`, `selfhealing()` | `health.py` |
| `GET /health/live/` | `liveness()` | `health.py` |
| `GET /health/ready/` | `readiness()` | `health.py` |
| `GET /health/pool/` | `pool()` | `health.py` |
| `GET /health/ping/` | `ping()` | `health.py` |
| `GET /health/gate/` | `gate()` | `health.py` |
| `GET /metrics/` | `metrics()` | `health.py`, `observability.py` |

### 8.3 DLQ (9개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `POST /dlq/replay/` | `replay()`, `replay_all()` | `dlq.py` |
| `GET /dlq/cleanup/stats/` | `cleanup_stats()` | `dlq.py` |
| `POST /dlq/cleanup/archive/` | `archive()` | `dlq.py` |
| `POST /dlq/cleanup/purge/` | `purge()` | `dlq.py` |
| `GET /dlq/list/` | `list()` | `dlq.py` |
| `GET /dlq/{pk}/` | `get()`, `detail()` | `dlq.py` |
| `POST /dlq/{pk}/retry/` | `retry()` | `dlq.py` |
| `POST /dlq/{pk}/resolve/` | `resolve()` | `dlq.py` |
| `POST /dlq/test/create/` | `test_create()` | `dlq.py` |

### 8.4 System Control (5개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET /system/status/` | `get_status()` | `system.py` |
| `POST /system/enable/` | `enable()` | `system.py` |
| `POST /system/disable/` | `disable()` | `system.py` |
| `POST /system/dry-run/enable/` | `enable_dry_run()` | `system.py` |
| `POST /system/dry-run/disable/` | `disable_dry_run()` | `system.py` |

### 8.5 Emergency Mode (8개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET /emergency/status/` | `get_status()` | `emergency.py` |
| `POST /emergency/trigger/` | `trigger()` | `emergency.py` |
| `POST /emergency/release/` | `release()` | `emergency.py` |
| `POST /emergency/gradual-recovery/` | `start_gradual_recovery()` | `emergency.py` |
| `POST /emergency/stop-recovery/` | `stop_gradual_recovery()` | `emergency.py` |
| `GET /emergency/history/` | `get_history()` | `emergency.py` |
| `GET/POST /emergency/config/` | `get_config()`, `set_config()` | `emergency.py` |
| `GET /emergency/levels/` | `get_levels()` | `emergency.py` |

### 8.6 Error Budget & Deployment (11개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `POST /gate/reset/` | `reset_gate()` | `error_budget.py` |
| `GET /error-budget/status/` | `get_status()` | `error_budget.py` |
| `GET /error-budget/history/` | `get_history()` | `error_budget.py` |
| `POST /error-budget/record/` | `record()` | `error_budget.py` |
| `POST /error-budget/exhaust/` | `exhaust()` | `error_budget.py` |
| `POST /error-budget/reset-simulation/` | `reset_simulation()` | `error_budget.py` |
| `GET /deployment-policy/verdict/` | `get_verdict()` | `error_budget.py` |
| `POST /deployment-policy/acknowledge/` | `acknowledge()` | `error_budget.py` |
| `POST /deployment-policy/override/` | `override()` | `error_budget.py` |
| `POST /deployment-policy/lift/` | `lift_freeze()` | `error_budget.py` |
| `GET /deployment-policy/active-override/` | `get_active_override()` | `error_budget.py` |

### 8.7 Reconciliation (9개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET /reconciliation/status/` | `get_status()` | `reconciliation.py` |
| `GET/POST /reconciliation/failsafe-periods/` | `list_failsafe_periods()`, `add_failsafe_period()` | `reconciliation.py` |
| `GET /reconciliation/shadow-budgets/` | `list_shadow_budgets()` | `reconciliation.py` |
| `GET /reconciliation/shadow-budgets/{id}/` | `get_shadow_budget()` | `reconciliation.py` |
| `POST /reconciliation/shadow-budgets/{id}/approve/` | `approve_shadow_budget()` | `reconciliation.py` |
| `POST /reconciliation/shadow-budgets/{id}/reject/` | `reject_shadow_budget()` | `reconciliation.py` |
| `GET/POST /reconciliation/excluded-periods/` | `list_excluded_periods()`, `add_excluded_period()` | `reconciliation.py` |
| `DELETE /reconciliation/excluded-periods/{id}/` | `remove_excluded_period()` | `reconciliation.py` |
| `GET/POST /reconciliation/config/` | `get_config()`, `set_config()` | `reconciliation.py` |

### 8.8 Chaos Engineering (20개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET/POST /chaos/config/safety-guard/` | `get_safety_guard_config()`, `set_safety_guard_config()` | `chaos.py` |
| `GET/POST /chaos/config/blast-radius/` | `get_blast_radius_policy()`, `set_blast_radius_policy()` | `chaos.py` |
| `GET/POST /chaos/config/scheduler/` | `get_scheduler_config()`, `set_scheduler_config()` | `chaos.py` |
| `GET/POST /chaos/config/reports/` | `get_report_config()`, `set_report_config()` | `chaos.py` |
| `GET/POST /chaos/config/stop-conditions/` | `get_stop_conditions_config()`, `set_stop_conditions_config()` | `chaos.py` |
| `GET/POST /chaos/config/ttl/` | `get_ttl_config()`, `set_ttl_config()` | `chaos.py` |
| `GET/POST /chaos/config/dry-run/` | `get_dry_run_config()`, `set_dry_run_config()` | `chaos.py` |
| `GET/POST /chaos/schedules/` | `list_schedules()`, `create_schedule()` | `chaos.py` |
| `GET/PUT/DELETE /chaos/schedules/{id}/` | `get_schedule()`, `update_schedule()`, `delete_schedule()` | `chaos.py` |
| `POST /chaos/schedules/{id}/approve/` | `approve_schedule()` | `chaos.py` |
| `POST /chaos/schedules/{id}/execute/` | `execute_schedule()` | `chaos.py` |
| `GET/POST /chaos/kill-switch/` | `get_chaos_kill_switch()`, `set_chaos_kill_switch()` | `chaos.py` |
| `POST /chaos/control/kill-all/` | `kill_all_chaos()` | `chaos.py` |
| `GET/POST /chaos/safety-check/` | `chaos_safety_check()`, `run_chaos_safety_check()` | `chaos.py` |
| `POST /chaos/blast-radius/check/` | `check_blast_radius()` | `chaos.py` |
| `GET /chaos/reports/` | `list_chaos_reports()` | `chaos.py` |
| `GET /chaos/reports/{id}/` | `get_chaos_report()` | `chaos.py` |
| `POST /chaos/reports/generate/` | `generate_chaos_report()` | `chaos.py` |
| `GET /chaos/reports/grades/` | `get_grade_history()` | `chaos.py` |
| `GET /chaos/pending-approvals/` | `list_pending_approvals()` | `chaos.py` |

### 8.9 L2 Storage (18개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET/POST /l2-storage/config/` | `get_config()`, `set_config()` | `l2_storage.py` |
| `POST /l2-storage/config/reset/` | `reset_config()` | `l2_storage.py` |
| `GET /l2-storage/status/` | `get_status()` | `l2_storage.py` |
| `GET /l2-storage/health/` | `get_health()` | `l2_storage.py` |
| `POST /l2-storage/health/reset/` | `reset_health()` | `l2_storage.py` |
| `GET /l2-storage/shadow-log/` | `list_shadow_logs()` | `l2_storage.py` |
| `GET /l2-storage/shadow-log/stats/` | `get_shadow_log_stats()` | `l2_storage.py` |
| `POST /l2-storage/shadow-log/clear/` | `clear_shadow_logs()` | `l2_storage.py` |
| `POST /l2-storage/shadow-log/analyze/` | `analyze_shadow_logs()` | `l2_storage.py` |
| `POST /l2-storage/shadow-log/replay/` | `replay_shadow_logs()` | `l2_storage.py` |
| `GET /l2-storage/shadow-log/service/{service}/` | `get_shadow_logs_by_service()` | `l2_storage.py` |
| `POST /l2-storage/sync/from-l2/` | `sync_from_l2()` | `l2_storage.py` |
| `POST /l2-storage/sync/to-l2/` | `sync_to_l2()` | `l2_storage.py` |
| `GET /l2-storage/drift/stats/` | `get_drift_stats()` | `l2_storage.py` |
| `GET /l2-storage/drift/history/` | `get_drift_history()` | `l2_storage.py` |
| `POST /l2-storage/drift/reconcile/` | `trigger_drift_reconciliation()` | `l2_storage.py` |
| `POST /l2-storage/drift/reconcile/{service}/` | `reconcile_service_drift()` | `l2_storage.py` |
| `GET /l2-storage/metrics/` | `get_metrics()` | `l2_storage.py` |

### 8.10 X-Test Mode (13개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `POST /xtest/inject-cb-failure/` | `inject_cb_failure()` | `xtest.py` |
| `POST /xtest/reset-cb/` | `reset_cb()` | `xtest.py` |
| `GET /xtest/cb-status/` | `get_cb_status()` | `xtest.py` |
| `POST /xtest/inject-error-budget/` | `inject_error_budget()` | `xtest.py` |
| `GET /xtest/snapshot/` | `get_snapshot()` | `xtest.py` |
| `POST /xtest/fast-fail-test/` | `fast_fail_test()` | `xtest.py` |
| `POST /xtest/trigger-cb-recovery/` | `trigger_cb_recovery()` | `xtest.py` |
| `GET /xtest/healing-timeline/` | `get_healing_timeline()` | `xtest.py` |
| `POST /xtest/blast-radius-test/` | `blast_radius_test()` | `xtest.py` |
| `POST /xtest/generate-postmortem/` | `generate_postmortem()` | `xtest.py` |
| `POST /xtest/record-healing-event/` | `record_healing_event()` | `xtest.py` |
| `GET /xtest/healing-incidents/` | `get_healing_incidents()` | `xtest.py` |
| `POST /xtest/multi-blast-radius/` | `multi_blast_radius()` | `xtest.py` |

### 8.11 Governance (9개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET /metrics/status/` | `get_metrics_status()` | `governance.py` |
| `POST /metrics/sync/` | `sync_metrics()` (Deprecated) | `governance.py` |
| `GET /metrics/drift-report/` | `get_drift_report()` (Deprecated) | `governance.py` |
| `POST /governance/reconcile/` | `reconcile()` | `governance.py` |
| `GET/POST /governance/mode/` | `get_governance_mode()`, `set_governance_mode()` | `governance.py` |
| `GET /governance/status/` | `get_governance_status()` | `governance.py` |
| `GET /governance/approval-requests/` | `list_approval_requests()` | `governance.py` |
| `POST /governance/approval-requests/{id}/approve/` | `approve_approval_request()` | `governance.py` |
| `POST /governance/approval-requests/{id}/reject/` | `reject_approval_request()` | `governance.py` |

### 8.12 Tiering (8개)
| 백엔드 API | 클라이언트 메서드 | 파일 |
|-----------|-----------------|------|
| `GET/POST /config/tiers/` | `get_definitions()`, `set_definitions()` | `tiering.py` |
| `POST /config/tiers/reset/` | `reset()` | `tiering.py` |
| `POST /config/tiers/dry-run/` | `dry_run()` | `tiering.py` |
| `GET /config/tiers/export/` | `export()` | `tiering.py` |
| `POST /config/tiers/import/` | `import_config()` | `tiering.py` |
| `POST /config/tiers/resolve/` | `resolve()` | `tiering.py` |
| `GET/POST /config/tier-mappings/` | `get_mappings()`, `set_mappings()` | `tiering.py` |
| `GET/POST /config/tier-overrides/` | `get_overrides()`, `set_overrides()` | `tiering.py` |

---

## 9. 시나리오별 Self-Healing 검증 체크리스트

### 9.1 Stage별 필수 검증 항목

| Stage | 유형 | 필수 검증 항목 | 검증 방법 |
|-------|------|--------------|----------|
| **Stage 0** | Smoke | 헬스체크, 인증 | `health.ping()`, `auth.login()` |
| **Stage 1-3** | Load | CB 상태, 에러 버짓 | `circuit_breaker.get_status()`, `error_budget.get_status()` |
| **Stage 6, 16** | Chaos | CB 전이, 비상 모드 | `chaos.inject_failure()`, `emergency.trigger()` |
| **Stage 7-8** | Integration | DLQ 처리 | `dlq.list()`, `dlq.replay()` |
| **Stage 12** | Platinum | 전체 스택 | `SelfHealingController` |
| **Stage 38-43** | Extreme | X-Test Mode | `xtest.inject_cb_failure()`, `xtest.cascade_failure()` |
| **Stage 47-51** | Advanced | L2 Storage, Drift | `l2_storage.get_status()`, `reconciliation.get_status()` |

### 9.2 Platinum Grade 완전 검증 체크리스트

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      Platinum Grade 검증 체크리스트                          │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🔴 Core Layer                                                                │
│   □ circuit_breaker: Open → Half-Open → Closed 전이 확인                    │
│   □ error_budget: 소진 임계값 도달 시 배포 차단 동작                         │
│   □ emergency: LEVEL_1 → LEVEL_2 → LEVEL_3 에스컬레이션                      │
│   □ dlq: 실패 메시지 적재/조회/재처리/삭제 사이클                            │
│   □ health: ping/liveness/readiness 모두 정상                               │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🟠 Observability Layer                                                       │
│   □ observability: 스냅샷 저장/타임라인 조회/포스트모템 생성                 │
│   □ dashboard: 실시간 상태 조회                                              │
│   □ alerts: 알림 발생/확인 동작                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🟡 Chaos Layer                                                               │
│   □ chaos: 장애 주입/안전 체크/폭발반경 제한 동작                            │
│   □ xtest: X-Test-Mode 활성화/CB 장애 주입/복구 테스트                       │
│   □ corruption_shield: L1/L2/L3 계층별 검증 통과                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🟢 Optimization Layer                                                        │
│   □ state_cache: 캐시 히트율 > 80%                                           │
│   □ adaptive_jitter: 재시도 폭풍 방지 동작                                   │
│   □ throttle: Netflix Gradient 알고리즘 스로틀링 적용                        │
│   □ rate_limiter: 요청 제한 동작                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🔵 Configuration Layer                                                       │
│   □ runtime_config: 동적 설정 변경 반영 (CB, SLA, DLQ 등)                    │
│   □ system: 시스템 활성화/비활성화/드라이런 모드 전환                        │
│   □ tiering: API 계층화 정책 적용                                            │
├─────────────────────────────────────────────────────────────────────────────┤
│ 🟣 Advanced Layer                                                            │
│   □ governance: 거버넌스 모드 전환/승인 요청 처리/감사 로그                  │
│   □ reconciliation: Shadow Budget 계산/FailSafe 기간 관리                    │
│   □ l2_storage: L1→L2 동기화/Shadow Log/Drift Reconciliation                 │
├─────────────────────────────────────────────────────────────────────────────┤
│ 📊 SLA Verification                                                          │
│   □ P99 응답시간 ≤ 250ms (Hard-Cap)                                          │
│   □ 에러율 < 0.1%                                                            │
│   □ 복구 시간 < 2분                                                          │
│   □ CB 오픈 → 복구 사이클 정상                                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 9.3 빠른 검증 스크립트 예시

```python
"""
Self-Healing 전체 기능 검증 스크립트
모든 API 카테고리의 기본 동작 확인
"""
from load_tests.utils.selfhealing import SelfHealingClient

def verify_all_selfhealing_features():
    client = SelfHealingClient()
    results = {}
    
    # 1. Core Layer
    print("🔴 Verifying Core Layer...")
    results["health"] = client.health.ping()
    results["cb_status"] = client.circuit_breaker.get_all_statuses()
    results["error_budget"] = client.error_budget.get_status()
    results["emergency"] = client.emergency.get_status()
    results["dlq_stats"] = client.dlq.cleanup_stats()
    
    # 2. Observability Layer
    print("🟠 Verifying Observability Layer...")
    results["dashboard"] = client.dashboard.get_summary()
    
    # 3. Chaos Layer
    print("🟡 Verifying Chaos Layer...")
    results["chaos_safety"] = client.chaos.chaos_safety_check()
    results["kill_switch"] = client.chaos.get_chaos_kill_switch()
    
    # 4. Configuration Layer
    print("🔵 Verifying Configuration Layer...")
    results["runtime_config"] = client.runtime_config.get_all()
    results["system_status"] = client.system.get_status()
    results["tiering"] = client.tiering.get_definitions()
    
    # 5. Advanced Layer
    print("🟣 Verifying Advanced Layer...")
    results["governance_mode"] = client.governance.get_governance_mode()
    results["reconciliation"] = client.reconciliation.get_status()
    results["l2_storage"] = client.l2_storage.get_status()
    
    # Summary
    print("\n" + "=" * 60)
    print("✅ Self-Healing Feature Verification Complete")
    print("=" * 60)
    
    for feature, result in results.items():
        status = "✅" if result else "❌"
        print(f"  {status} {feature}")
    
    return results

if __name__ == "__main__":
    verify_all_selfhealing_features()
```

---

📝 **Self-Healing Architecture Guide v1.1.0**
📅 **Updated**: 2025-12-28
📁 **Location**: `docs/self_healing/27_SELFHEALING_SCENARIO_MAPPING.md`
✅ **API Coverage**: 100% (~150 endpoints)

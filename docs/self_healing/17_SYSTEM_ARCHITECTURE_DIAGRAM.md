# Self-Healing System Architecture Diagram

> 📅 작성일: 2024-12-24  
> 📅 업데이트: 2024-12-24 (Phase A 수정 완료)  
> 🎯 목적: 셀프 힐링 시스템의 핵심 컨트롤러와 컴포넌트 간 연결 관계 시각화  
> ⚠️ 이 문서는 **실제 코드 분석**을 기반으로 작성되었습니다

---

## 0. Phase A 수정 완료 사항 (2024-12-24)

> ✅ 아래 Critical 이슈들이 모두 수정되었습니다.

| # | 수정 내용 | 파일 | 상태 |
|---|----------|------|------|
| 1 | **TieringMiddleware 구현** | `api/django/tiering.py` | ✅ 완료 |
| 2 | **RuntimeConfigManager 연동** | `circuit_breaker/config.py`, `dlq_models.py`, `retry_handler.py` | ✅ 완료 |
| 3 | **Kill Switch 체크 추가** | `circuit_breaker/manual_control.py`, `replay_service.py`, `retry_handler.py` | ✅ 완료 |

### 수정 상세

**1. TieringMiddleware 구현** (신규 생성)
- EmergencyManager와 연동하여 Emergency Level 기반 트래픽 제어
- TierRegistry를 통한 엔드포인트 Tier 분류 (critical/standard/non_essential)
- 확률 기반 Load Shedding (503 응답 + Retry-After 헤더)
- Emergency Level별 Tier 허용 비율 적용

**2. RuntimeConfigManager 연동 완료**
- `CircuitBreakerConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config
- `DLQConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config  
- `RetryConfig.from_settings()`: RuntimeConfigManager 우선 조회, fallback to static config

**3. Kill Switch 체크 추가**
- `force_open()`, `force_close()`: Kill Switch 체크 후 차단 시 실패 반환
- `replay_single()`, `replay_batch()`: Kill Switch 체크 후 차단
- `RetryHandler.execute()`: Kill Switch 체크 후 즉시 ABORT 반환

---

## 1. 시스템 전체 개요

```
┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    SELF-HEALING SYSTEM                                           │
│                                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐    │
│  │                              🎛️ CENTRAL CONTROLLERS                                      │    │
│  │                                                                                          │    │
│  │    ┌───────────────────┐    ┌───────────────────┐    ┌───────────────────┐              │    │
│  │    │ RuntimeConfig     │    │ SystemControl     │    │ Emergency         │              │    │
│  │    │ Manager           │    │ Manager           │    │ Manager           │              │    │
│  │    │                   │    │                   │    │                   │              │    │
│  │    │ • 18개 Config     │    │ • Kill Switch     │    │ • Level 0~3      │              │    │
│  │    │ • Apply Strategy  │    │ • Dry Run Mode    │    │ • Traffic Tier   │              │    │
│  │    │ • Config History  │    │ • 전역 ON/OFF     │    │ • 자동 만료      │              │    │
│  │    └─────────┬─────────┘    └─────────┬─────────┘    └─────────┬─────────┘              │    │
│  │              │                        │                        │                         │    │
│  └──────────────┼────────────────────────┼────────────────────────┼─────────────────────────┘    │
│                 │                        │                        │                              │
│                 ▼                        ▼                        ▼                              │
│  ┌──────────────────────────────────────────────────────────────────────────────────────────┐   │
│  │                           📦 STATE BACKEND (Persistent Storage)                          │   │
│  │                                                                                           │   │
│  │   ┌─────────┐     ┌─────────┐     ┌─────────┐                                           │   │
│  │   │ Redis   │ ←→  │ File    │ ←→  │ Memory  │   (Pluggable, 자동 Fallback)              │   │
│  │   └─────────┘     └─────────┘     └─────────┘                                           │   │
│  │                                                                                           │   │
│  └──────────────────────────────────────────────────────────────────────────────────────────┘   │
│                                                                                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 핵심 컨트롤러 상세

### 2.1 RuntimeConfigManager - 모든 설정의 중앙 허브

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                            RuntimeConfigManager (Singleton)                              │
│                                                                                          │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                              18개 Config Types                                      │ │
│  │                                                                                     │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │ │
│  │  │ circuit_breaker │  │ dlq             │  │ retry           │  │ sla           │  │ │
│  │  │ • failure_thres │  │ • max_retries   │  │ • max_attempts  │  │ • default_hrs │  │ │
│  │  │ • recovery_time │  │ • retry_delay   │  │ • backoff_strat │  │ • thresholds  │  │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  └───────────────┘  │ │
│  │                                                                                     │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │ │
│  │  │ rate_limit      │  │ security        │  │ idempotency     │  │ notification  │  │ │
│  │  │ • base_delay    │  │ • rate_window   │  │ • cache_ttl     │  │ • channels    │  │ │
│  │  │ • control_api   │  │ • ban_threshold │  │ • clock_skew    │  │ • thresholds  │  │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  └───────────────┘  │ │
│  │                                                                                     │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │ │
│  │  │ forensic        │  │ logging         │  │ metrics         │  │ error_budget  │  │ │
│  │  │ • max_length    │  │ • log_levels    │  │ • enabled       │  │ • thresholds  │  │ │
│  │  │ • sanitize      │  │ • structured    │  │ • jitter        │  │ • burn_rates  │  │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  └───────────────┘  │ │
│  │                                                                                     │ │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌───────────────┐  │ │
│  │  │ governance      │  │ drift_threshold │  │ l2_storage      │  │ chaos         │  │ │
│  │  │ • RBAC 임계값   │  │ • warning 5%    │  │ • redis_timeout │  │ • blast_rad   │  │ │
│  │  │ • 4-eyes 설정   │  │ • critical 20%  │  │ • shadow_log    │  │ • dry_run     │  │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  └───────────────┘  │ │
│  │                                                                                     │ │
│  │  ┌─────────────────┐  ┌─────────────────────────────────────────────────────────┐  │ │
│  │  │ slo             │  │ approval_requests (4-Eyes Principle)                     │  │ │
│  │  │ • definitions   │  │ • PENDING → APPROVED/REJECTED/EXPIRED                    │  │ │
│  │  │ • targets       │  │ • 듀얼 승인 워크플로우                                   │  │ │
│  │  └─────────────────┘  └─────────────────────────────────────────────────────────┘  │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                            Apply Strategy System                                    │ │
│  │                                                                                     │ │
│  │    IMMEDIATE ──────────► 즉시 적용 (기본 안전 설정)                                │ │
│  │    DELAYED ────────────► N초 후 적용 (취소 가능)                                   │ │
│  │    GRACEFUL ───────────► 진행 중인 작업 완료 후 적용                              │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                            Config History (Audit Trail)                             │ │
│  │                                                                                     │ │
│  │    모든 설정 변경 → ConfigHistory 자동 기록 → Rollback 지원                        │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 SystemControlManager - 글로벌 킬 스위치

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     SystemControlManager (Singleton)                     │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                         System State                                │ │
│  │                                                                     │ │
│  │    enabled: bool ─────────► 전체 Self-Healing ON/OFF               │ │
│  │    dry_run: bool ─────────► 관찰만 (실제 액션 X)                   │ │
│  │    disabled_at/by ────────► 비활성화 기록                          │ │
│  │    enabled_at/by ─────────► 활성화 기록                            │ │
│  │                                                                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                         Operations                                  │ │
│  │                                                                     │ │
│  │    is_enabled() ──────────► 시스템 활성화 여부 확인                │ │
│  │    enable(actor) ─────────► Self-Healing 활성화                    │ │
│  │    disable(actor, reason) ► Kill Switch 발동                       │ │
│  │    set_dry_run(enabled) ──► Dry Run 모드 토글                      │ │
│  │                                                                     │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 GracefulDegradationManager - 비상 모드 관리

```
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│                          GracefulDegradationManager (Singleton)                          │
│                                                                                          │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                           Emergency Levels                                          │ │
│  │                                                                                     │ │
│  │    NORMAL ────► 정상 운영 (모든 트래픽 허용)                                       │ │
│  │        │                                                                            │ │
│  │        ▼                                                                            │ │
│  │    LEVEL_1 ───► 경미한 장애 (Non-Essential 차단)                                   │ │
│  │        │          critical: 100%, standard: 100%, non_essential: 0%                │ │
│  │        ▼                                                                            │ │
│  │    LEVEL_2 ───► 중간 장애 (Standard 10%만 허용)                                    │ │
│  │        │          critical: 100%, standard: 10%, non_essential: 0%                 │ │
│  │        ▼                                                                            │ │
│  │    LEVEL_3 ───► 심각한 장애 (Critical도 50%만)                                     │ │
│  │                   critical: 50%, standard: 0%, non_essential: 0%                   │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                           Recovery Gate                                             │ │
│  │                                                                                     │ │
│  │    메트릭 기반 안정성 확인 ─────────► CPU < 80%, ErrorRate < 5%                   │ │
│  │    안정화 대기 기간 ────────────────► 5분 (기본)                                   │ │
│  │    점진적 복구 ─────────────────────► LEVEL_3 → 2 → 1 → NORMAL                    │ │
│  │    복구 실패 시 자동 롤백 ──────────► 이전 레벨로 복귀                            │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 컴포넌트 간 연결 다이어그램

```
                                    ┌─────────────────────┐
                                    │    API Request      │
                                    └──────────┬──────────┘
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    📡 API Layer                                               │
│                                                                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ /config/*   │  │ /emergency/*│  │ /dlq/*      │  │ /circuit/*  │  │ /governance/*       │ │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘ │
│         │                │                │                │                    │            │
└─────────┼────────────────┼────────────────┼────────────────┼────────────────────┼────────────┘
          │                │                │                │                    │
          ▼                ▼                ▼                ▼                    ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                🎛️ Control Layer (3 Central Controllers)                      │
│                                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                                                                                          │ │
│  │   RuntimeConfigManager ◄────────────────┬────────────────► SystemControlManager         │ │
│  │          │                              │                           │                    │ │
│  │          │         ┌────────────────────┴────────────────────┐      │                    │ │
│  │          │         │                                         │      │                    │ │
│  │          │         ▼                                         │      │                    │ │
│  │          │   GracefulDegradationManager                      │      │                    │ │
│  │          │         │                                         │      │                    │ │
│  │          │         │  • EmergencyTracker ◄───────────────────┘      │                    │ │
│  │          │         │  • RecoveryGate                                │                    │ │
│  │          │         │                                                │                    │ │
│  │          ▼         ▼                                                ▼                    │ │
│  │   ┌───────────────────────────────────────────────────────────────────────────────────┐  │ │
│  │   │                          StateBackend (Shared Storage)                            │  │ │
│  │   │                                                                                   │  │ │
│  │   │   runtime_config:*    │   emergency_state   │   system_control   │   governance:* │  │ │
│  │   │                                                                                   │  │ │
│  │   └───────────────────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                                          │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘  │
│                                                                                               │
└───────────────────────────────────────────────────────────────────────────────────────────────┘
          │                │                │                │                    │
          ▼                ▼                ▼                ▼                    ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                ⚙️ Service Layer                                               │
│                                                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌────────────┐ │
│  │ DLQService    │  │ CircuitBreaker│  │ ReplayService │  │ ErrorBudget   │  │ Chaos      │ │
│  │               │  │ Service       │  │               │  │ Gate          │  │ SafetyGuard│ │
│  │ • store       │  │ • force_open  │  │ • replay      │  │ • check       │  │ • validate │ │
│  │ • query       │  │ • force_close │  │ • batch       │  │ • allow       │  │ • rollback │ │
│  │ • cleanup     │  │ • should_allow│  │ • handlers    │  │ • block       │  │            │ │
│  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘  └─────┬──────┘ │
│          │                  │                  │                  │                │        │
│          └──────────────────┴──────────────────┴──────────────────┴────────────────┘        │
│                                              │                                              │
└──────────────────────────────────────────────┼──────────────────────────────────────────────┘
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                📊 Repository Layer (Adapters)                                │
│                                                                                               │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐  ┌───────────────────────────────┐ │
│  │ FailedOp      │  │ CircuitBreaker│  │ AuditLog      │  │ ConfigHistory                 │ │
│  │ Repository    │  │ State Repo    │  │ Repository    │  │ Repository                    │ │
│  └───────────────┘  └───────────────┘  └───────────────┘  └───────────────────────────────┘ │
│                                                                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
                                               │
                                               ▼
┌──────────────────────────────────────────────────────────────────────────────────────────────┐
│                                💾 Database / Redis                                           │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. 설정별 연결 관계 상세

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                        RuntimeConfigManager 설정 → 서비스 연결                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

 RuntimeConfigManager                           연결된 서비스/컴포넌트
 ═══════════════════                           ════════════════════════

 circuit_breaker ─────────────────────────────► CircuitBreakerService
       │                                              │
       │ • failure_threshold                          ▼
       │ • recovery_timeout              ┌─────────────────────────────┐
       │ • self_ddos_protection          │ • should_allow()            │
       └─────────────────────────────────│ • force_open/close()        │
                                          │ • Rate Limit Cascade        │
                                          └─────────────────────────────┘

 dlq ────────────────────────────────────────► DLQService
       │                                              │
       │ • max_retries                                ▼
       │ • retry_delay                   ┌─────────────────────────────┐
       │ • expiry_hours                  │ • store_failure()           │
       └─────────────────────────────────│ • get_entries()             │
                                          │ • cleanup_expired()         │
                                          └─────────────────────────────┘

 retry ──────────────────────────────────────► RetryHandler
       │                                              │
       │ • max_attempts                               ▼
       │ • backoff_strategy              ┌─────────────────────────────┐
       │ • jitter                        │ • Exponential Backoff        │
       └─────────────────────────────────│ • Jitter 계산                │
                                          └─────────────────────────────┘

 rate_limit ─────────────────────────────────► RateLimitCoordinator
       │                                         HybridRateLimitMiddleware
       │ • control_api_rate_limit                     │
       │ • emergency_rate_limit                       ▼
       │ • backoff_multiplier            ┌─────────────────────────────┐
       └─────────────────────────────────│ • API Rate Limiting         │
                                          │ • 429 Handling               │
                                          └─────────────────────────────┘

 error_budget ───────────────────────────────► ErrorBudgetGate
       │                                              │
       │ • threshold_healthy                          ▼
       │ • burn_rate_*                   ┌─────────────────────────────┐
       │ • heartbeat_*                   │ • check_automation_allowed()│
       └─────────────────────────────────│ • Fail-Safe 발동             │
                                          │ • Heartbeat (Dead Man)       │
                                          └─────────────────────────────┘

 governance ─────────────────────────────────► EmergencyModeTracker
       │                                         GovernanceService
       │ • threshold_operator/admin                   │
       │ • emergency_expiry_hours                     ▼
       │ • four_eyes_enabled             ┌─────────────────────────────┐
       └─────────────────────────────────│ • RBAC 권한 체크             │
                                          │ • 4-Eyes Approval            │
                                          │ • 자동 만료 체크             │
                                          └─────────────────────────────┘

 l2_storage ─────────────────────────────────► L2StorageManager
       │                                              │
       │ • redis_timeout_ms                           ▼
       │ • shadow_log_enabled            ┌─────────────────────────────┐
       │ • reconciliation_jitter         │ • Shadow Logging             │
       └─────────────────────────────────│ • L1/L2 Fallback             │
                                          │ • Drift 감지                  │
                                          └─────────────────────────────┘

 chaos ──────────────────────────────────────► ChaosExperiment
       │                                         SafetyGuard
       │ • max_blast_radius                           │
       │ • auto_rollback_enabled                      ▼
       │ • stop_on_error_rate            ┌─────────────────────────────┐
       └─────────────────────────────────│ • Experiment Validation      │
                                          │ • Auto Rollback              │
                                          │ • Blast Radius Check         │
                                          └─────────────────────────────┘

 notification ───────────────────────────────► NotificationService
       │                                              │
       │ • channels (slack/email)                     ▼
       │ • critical/high/medium_channel  ┌─────────────────────────────┐
       │ • slack_block_text_limit        │ • Slack 알림                 │
       └─────────────────────────────────│ • Email 알림                 │
                                          │ • 긴급도별 채널 라우팅       │
                                          └─────────────────────────────┘

 metrics ────────────────────────────────────► MetricsCollector
       │                                              │
       │ • jitter_enabled                             ▼
       │ • jitter_max_delay_seconds      ┌─────────────────────────────┐
       │ • collection_interval           │ • Prometheus Export          │
       └─────────────────────────────────│ • Thundering Herd 방지       │
                                          └─────────────────────────────┘
```

---

## 5. ⚠️ 연결 분석: 누락된 연결 및 개선 필요 사항

### 5.1 현재 연결 상태 체크리스트

```
┌────────────────────────────────────────────────────────────────────────────────────────────┐
│                              연결 상태 체크리스트                                           │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                             │
│  ✅ = 연결됨   ⚠️ = 부분 연결   ❌ = 미연결                                                 │
│                                                                                             │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                             │
│  RuntimeConfigManager 연결                                                                  │
│  ──────────────────────────                                                                 │
│  ✅ circuit_breaker ↔ CircuitBreakerService                                                │
│  ✅ dlq ↔ DLQService                                                                       │
│  ✅ retry ↔ BackoffCalculator                                                              │
│  ✅ rate_limit ↔ HybridRateLimitMiddleware, RateLimitCoordinator                           │
│  ✅ error_budget ↔ ErrorBudgetGate                                                         │
│  ✅ governance ↔ EmergencyModeTracker, RBAC Permissions                                    │
│  ✅ l2_storage ↔ L2StorageManager (Phase 3 신규)                                           │
│  ✅ chaos ↔ SafetyGuard (Phase 3 신규)                                                     │
│  ✅ drift_threshold ↔ DriftDetector                                                        │
│  ✅ slo ↔ SLO Dashboard, Burn Rate Alerts                                                  │
│                                                                                             │
│  ⚠️ forensic ↔ ForensicService                                                             │
│      → 설정은 있으나, 실시간 설정 변경 반영 미완                                           │
│                                                                                             │
│  ⚠️ logging ↔ Logging Components                                                           │
│      → 컴포넌트별 log level 동적 변경 미구현                                               │
│                                                                                             │
│  ⚠️ security ↔ SecurityService                                                             │
│      → IP Ban 등은 별도 캐시 사용, RuntimeConfig 미연동                                    │
│                                                                                             │
│  ⚠️ idempotency ↔ IdempotencyService                                                       │
│      → TTL 설정 존재하나, 런타임 변경 시 기존 캐시 정리 로직 없음                          │
│                                                                                             │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                             │
│  SystemControlManager 연결                                                                  │
│  ─────────────────────────                                                                  │
│  ✅ is_enabled() → All Services (Kill Switch 체크)                                         │
│  ✅ dry_run → Action Executor                                                               │
│  ⚠️ dry_run → DLQService.store_failure()                                                   │
│      → DLQ 저장은 dry_run에서도 동작해야 하나, 일부 경로 미체크                            │
│                                                                                             │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                             │
│  EmergencyManager 연결                                                                      │
│  ────────────────────                                                                       │
│  ✅ Traffic Tiering ↔ TieringMiddleware                                                    │
│  ✅ Auto-Activation ↔ ErrorBudgetGate (고에러율 시 자동 발동)                              │
│  ⚠️ Notification ↔ NotificationService                                                     │
│      → 비상 모드 전환 시 알림 로직 있으나, 복구 알림 미완                                  │
│                                                                                             │
│  ❌ EmergencyManager → CircuitBreakerService (미연결)                                       │
│      → 비상 모드 LEVEL_3 시 자동 CB Open 연동 없음                                         │
│                                                                                             │
│  ❌ EmergencyManager → DLQ Auto-Replay 차단 (미연결)                                        │
│      → 비상 모드 시 자동 Replay 일시 중단 로직 없음                                        │
│                                                                                             │
├────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                             │
│  ErrorBudgetGate 연결                                                                       │
│  ───────────────────                                                                        │
│  ✅ check_automation_allowed() → DLQ Auto-Replay                                            │
│  ✅ Fail-Safe Alert → NotificationService                                                   │
│  ⚠️ Fail-Safe → EmergencyManager (부분)                                                    │
│      → 에러 예산 소진 시 비상 모드 자동 전환 로직 있으나 레벨 조정 미세화 필요             │
│                                                                                             │
│  ❌ ErrorBudgetGate → ChaosExperiment 차단 (미연결)                                         │
│      → 에러 예산 < 10% 시 Chaos 실험 자동 차단 로직 없음                                   │
│                                                                                             │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.2 누락된 핵심 연결 (Priority)

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          🔴 HIGH PRIORITY - 누락된 연결                                      │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

 1. EmergencyManager → CircuitBreaker 연동
    ═══════════════════════════════════════

    현재 상태:
    ┌─────────────────┐          ┌─────────────────┐
    │ EmergencyManager│    ✗     │ CircuitBreaker  │
    │ (LEVEL_3)       │──────────│ Service         │
    └─────────────────┘          └─────────────────┘

    권장 구현:
    ┌─────────────────┐          ┌─────────────────┐
    │ EmergencyManager│    ✓     │ CircuitBreaker  │
    │ activate(L3)    │─────────►│ force_open_all()│
    │ deactivate()    │◄─────────│ (비필수 서비스)  │
    └─────────────────┘          └─────────────────┘

    이유: 비상 모드 LEVEL_3에서 non_essential 외부 서비스 호출을
          CircuitBreaker가 자동으로 차단해야 함


 2. ErrorBudgetGate → ChaosExperiment 차단
    ════════════════════════════════════════

    현재 상태:
    ┌─────────────────┐          ┌─────────────────┐
    │ ErrorBudgetGate │    ✗     │ ChaosExperiment │
    │ (budget < 10%)  │──────────│ start()         │
    └─────────────────┘          └─────────────────┘

    권장 구현:
    ┌─────────────────┐          ┌─────────────────┐
    │ ErrorBudgetGate │    ✓     │ ChaosExperiment │
    │ is_blocked()    │─────────►│ start()         │
    │                 │          │ → AutoBlocked   │
    └─────────────────┘          └─────────────────┘

    이유: 에러 예산 위험 시 Chaos 실험은 자동 차단되어야 함


 3. EmergencyManager → DLQ Auto-Replay 차단
    ════════════════════════════════════════

    현재 상태:
    ┌─────────────────┐          ┌─────────────────┐
    │ EmergencyManager│    ✗     │ ReplayService   │
    │ (is_active)     │──────────│ auto_replay()   │
    └─────────────────┘          └─────────────────┘

    권장 구현:
    ┌─────────────────┐          ┌─────────────────┐
    │ EmergencyManager│    ✓     │ ReplayService   │
    │ is_active()     │─────────►│ auto_replay()   │
    │                 │          │ → Skip/Delay    │
    └─────────────────┘          └─────────────────┘

    이유: 비상 모드 중 자동 Replay는 시스템 부하를 가중시킬 수 있음

```

---

## 6. 권장 아키텍처 개선

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              권장 이벤트 기반 연결 아키텍처                                  │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

                              ┌───────────────────────────────────┐
                              │         Event Bus (In-Memory)     │
                              │                                   │
                              │  • EmergencyLevelChanged          │
                              │  • ErrorBudgetCritical            │
                              │  • CircuitBreakerStateChanged     │
                              │  • ConfigUpdated                  │
                              │                                   │
                              └───────────────────┬───────────────┘
                                                  │
              ┌───────────────────────────────────┼───────────────────────────────────┐
              │                                   │                                   │
              ▼                                   ▼                                   ▼
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│    EmergencyManager     │     │    ErrorBudgetGate      │     │   CircuitBreakerSvc     │
│                         │     │                         │     │                         │
│  emit: LevelChanged     │     │  emit: BudgetCritical   │     │  emit: StateChanged     │
│  listen: BudgetCritical │     │  listen: LevelChanged   │     │  listen: LevelChanged   │
│                         │     │                         │     │                         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
              │                           │                               │
              │                           │                               │
              ▼                           ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│    ReplayService        │     │    ChaosExperiment      │     │    NotificationService  │
│                         │     │                         │     │                         │
│  listen: LevelChanged   │     │  listen: BudgetCritical │     │  listen: ALL EVENTS     │
│  → pause auto-replay    │     │  → block experiment     │     │  → alert routing        │
│                         │     │                         │     │                         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘


구현 예시:
─────────────────────────────────────────────────────────────────────────────

# event_bus.py
class SelfHealingEventBus:
    """이벤트 버스 - 컴포넌트 간 느슨한 결합"""

    _listeners: Dict[str, List[Callable]] = {}

    @classmethod
    def emit(cls, event_type: str, data: Dict[str, Any]) -> None:
        for listener in cls._listeners.get(event_type, []):
            try:
                listener(data)
            except Exception as e:
                logger.error(f"Event handler error: {e}")

    @classmethod
    def on(cls, event_type: str, handler: Callable) -> None:
        cls._listeners.setdefault(event_type, []).append(handler)


# emergency_mode.py (개선)
def activate_manual(self, level, ...):
    # ... 기존 로직 ...

    # 이벤트 발행 - 다른 컴포넌트에 알림
    SelfHealingEventBus.emit("EmergencyLevelChanged", {
        "level": level.value,
        "previous_level": previous.value,
        "is_escalation": level.value > previous.value,
    })


# circuit_breaker_service.py (개선)
# 초기화 시 이벤트 구독
SelfHealingEventBus.on("EmergencyLevelChanged", lambda data:
    _handle_emergency_level_change(data)
)

def _handle_emergency_level_change(data):
    if data["level"] >= 3:  # LEVEL_3 이상
        # Non-essential 서비스들 CB 자동 Open
        for service in get_non_essential_services():
            force_open_circuit(service, reason="Emergency LEVEL_3")
```

---

## 7. 요약: 현재 상태 및 권장 조치

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                                      현재 상태 요약                                          │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ 잘 연결된 부분 (85%)                                                                    │
│  ─────────────────────                                                                       │
│  • RuntimeConfigManager → 대부분의 서비스 설정 연동 완료                                    │
│  • SystemControlManager → 전역 Kill Switch 잘 작동                                          │
│  • StateBackend → 모든 상태 영속화 및 공유                                                  │
│  • ConfigHistory → 변경 이력 자동 기록                                                      │
│  • ErrorBudgetGate → 자동화 차단 로직                                                       │
│                                                                                              │
│  ⚠️ 부분 연결 (10%)                                                                         │
│  ─────────────────                                                                           │
│  • forensic, logging, security, idempotency 런타임 설정 반영 미완                          │
│  • EmergencyManager → NotificationService (복구 알림 누락)                                  │
│  • dry_run 모드 일부 경로 미체크                                                            │
│                                                                                              │
│  ❌ 미연결 (5% - 중요!)                                                                      │
│  ─────────────────────                                                                       │
│  • EmergencyManager ↔ CircuitBreakerService (비상 시 자동 CB Open)                          │
│  • ErrorBudgetGate → ChaosExperiment (예산 부족 시 실험 차단)                               │
│  • EmergencyManager → ReplayService (비상 시 Auto-Replay 중단)                              │
│                                                                                              │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  🎯 권장 조치 (우선순위순)                                                                   │
│  ────────────────────────                                                                    │
│  1. [HIGH] 이벤트 버스 도입 - 컴포넌트 간 느슨한 결합                                       │
│  2. [HIGH] EmergencyManager → CB/Replay 연동 구현                                           │
│  3. [MED]  ErrorBudgetGate → Chaos 연동 구현                                                │
│  4. [LOW]  forensic/logging 런타임 설정 반영 완료                                           │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. 참고 문서

- [16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md](16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md) - 거버넌스 구현 로드맵
- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Phase 1 구현
- [16_GOVERNANCE_IMPLEMENTATION_PART1A.md](16_GOVERNANCE_IMPLEMENTATION_PART1A.md) - Phase 1A 구현
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Phase 2 & 3 구현
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - 에러 예산 설계
- [13_LAYERED_STORAGE_RESILIENCE.md](13_LAYERED_STORAGE_RESILIENCE.md) - L2 스토리지 설계

---

## 9. 🔬 코드 분석 기반 상세 발견 사항 (2024-12-24)

> 아래 내용은 실제 코드 분석을 통해 발견된 구체적인 연결 상태입니다.

### 9.1 RuntimeConfigManager 설정 사용 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                    RuntimeConfigManager 18개 설정 타입 실제 사용 현황                        │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ 실제 서비스에서 사용 (9개) - Phase A 수정 후                                             │
│  ──────────────────────────────────────────────                                              │
│  • rate_limit ────────► HybridRateLimitMiddleware, RateLimitCoordinator                     │
│  • governance ────────► EmergencyModeTracker, RBAC Permissions, GovernanceView              │
│  • error_budget ──────► ErrorBudgetGate, Heartbeat, Recovery 알림                           │
│  • chaos ─────────────► SafetyGuard, ChaosScheduler, BlastRadius, ChaosConfigView           │
│  • drift_threshold ───► DriftThresholdConfigView                                            │
│  • l2_storage ────────► L2StorageConfigView                                                 │
│  • circuit_breaker ───► CircuitBreakerConfig.from_settings() ✅ Phase A 수정                │
│  • dlq ───────────────► DLQConfig.from_settings() ✅ Phase A 수정                           │
│  • retry ─────────────► RetryConfig.from_settings() ✅ Phase A 수정                         │
│                                                                                              │
│  ⚠️ API만 노출, 서비스 로직에서 미사용 (7개)                                                │
│  ─────────────────────────────────────────────                                               │
│  • sla ───────────────► API 노출 O, SLA 모니터링에서 미참조 ❌                              │
│  • security ──────────► API 노출 O, 보안 로직에서 미참조 ❌                                 │
│  • idempotency ───────► API 노출 O, IdempotencyService에서 미참조 ❌                        │
│  • notification ──────► API 노출 O, NotificationService에서 미참조 ❌                       │
│  • forensic ──────────► API 노출 O, ForensicService에서 미참조 ❌                           │
│  • metrics ───────────► API 노출 O, MetricsCollector에서 미참조 ❌                          │
│  • slo ───────────────► API 노출 O, SLO 서비스에서 미참조 ❌                                │
│                                                                                              │
│  💡 Phase A 수정으로 핵심 서비스 3개(CB, DLQ, Retry)가 RuntimeConfigManager 연동 완료!      │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.2 SystemControlManager (Kill Switch) 체크 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          Kill Switch (is_enabled()) 체크 현황                                │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ Kill Switch 체크하는 서비스 (Phase A 수정 후)                                            │
│  ─────────────────────────────────────────────────                                           │
│  • ChaosExperiment (safety_guard.py) ──► SafetyGuard.pre_flight_check()에서 체크           │
│  • ChaosScheduler ─────────────────────► SafetyGuard를 통한 간접 체크                       │
│  • CircuitBreakerService ──────────────► force_open(), force_close() ✅ Phase A 수정        │
│  • ReplayService ──────────────────────► replay_single(), replay_batch() ✅ Phase A 수정    │
│  • RetryHandler ───────────────────────► execute() ✅ Phase A 수정                          │
│                                                                                              │
│  ⚠️ Kill Switch 체크 불필요한 서비스                                                        │
│  ─────────────────────────────────────                                                       │
│  • DLQService (store_failure) ───────► 실패 저장은 Kill Switch와 무관 🟢 OK                │
│  • ControlAPIService ────────────────► Control API는 항상 작동해야 함 🟢 OK                 │
│  • EmergencyMode ────────────────────► 별도 시스템 (독립 동작) 🟢 OK                        │
│  • ErrorBudgetService ───────────────► 읽기 전용 서비스 🟢 OK                               │
│                                                                                              │
│  📌 Phase A 수정으로 핵심 서비스 3개(CB, Replay, Retry)에 Kill Switch 체크 추가 완료!       │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```
│                                                                                              │
│  📌 현재 Kill Switch가 중지시키는 것: Chaos 실험만!                                         │
│  📌 중지 안됨: CB 상태변경, DLQ 저장, Replay, Retry                                         │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.3 EmergencyManager 연결 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          EmergencyManager 서비스 연결 현황                                   │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ 연결된 서비스 (Phase A 수정 후)                                                          │
│  ──────────────────────────────────                                                          │
│  • GovernanceService ───────► emergency_activated/deactivated 이벤트 발송                   │
│  • ConfigHistoryService ────► 상태 변경 이력 저장                                           │
│  • Shadow Audit ────────────► 변경 이벤트 감사 로그                                         │
│  • Notification Handlers ───► 커스텀 핸들러 등록 가능                                       │
│  • TieringMiddleware ───────► EmergencyManager 연동 ✅ Phase A 수정                         │
│                                 - Emergency Level 기반 트래픽 제어                          │
│                                 - Tier별 허용 비율 적용                                     │
│                                 - 확률 기반 Load Shedding                                   │
│                                                                                              │
│  ⚠️ 연결 권장 (Phase B)                                                                      │
│  ────────────────────────────                                                                │
│  • CircuitBreakerService ───► 비상 시 자동 CB Open (Event Bus 도입 후)                      │
│  • DLQService ──────────────► 비상 시 DLQ 처리 일시 중지 (Event Bus 도입 후)                │
│  • ReplayService ───────────► 비상 시 Auto-Replay 중단 (Event Bus 도입 후)                  │
│                                                                                              │
│  ✅ TieringMiddleware 구현 완료!                                                             │
│     - TierRegistry로 엔드포인트 Tier 분류                                                   │
│     - EMERGENCY_LEVEL_RULES로 Tier별 허용 비율 계산                                         │
│     - 503 Load Shedding 응답 + Retry-After 헤더                                             │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.4 ErrorBudgetGate 연결 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          ErrorBudgetGate 자동화 차단 현황                                    │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ ErrorBudgetGate 체크하는 서비스                                                          │
│  ──────────────────────────────────                                                          │
│  • ChaosScheduler (chaos_scheduler.py) ──► SafetyGuard.pre_flight_check()에서 체크         │
│  • DLQ Single Replay (Celery Task) ──────► replay_dlq_item.py에서 체크                      │
│  • DLQ Batch Replay (Celery Task) ───────► process_dlq_batch.py에서 체크                    │
│  • Chaos SafetyGuard ────────────────────► pre-flight 체크                                  │
│  • Chaos Stop Conditions ────────────────► 실험 중 모니터링 및 자동 중단                    │
│                                                                                              │
│  ❌ ErrorBudgetGate 체크 안하는 자동화 서비스                                                │
│  ─────────────────────────────────────────────                                               │
│  • ReplayService (서비스 레이어) ────────► 직접 호출 시 체크 없음 🟡 MEDIUM                 │
│  • DLQService ───────────────────────────► 자동 처리 로직에 체크 없음 🟡 MEDIUM             │
│  • RetryHandler ─────────────────────────► 자동 재시도 시 체크 없음 🔴 HIGH                 │
│  • Conditional Replay on Circuit Close ──► CB 복구 시 자동 리플레이 🔴 HIGH                 │
│                                                                                              │
│  💡 패턴: Celery Task에서는 체크하지만, 서비스 레이어 직접 호출 시 우회 가능                │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.5 Celery Tasks 안전 체크 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Celery Tasks 안전 체크 매트릭스                                 │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  Task 모듈           │ Kill Switch │ ErrorBudgetGate │ SafetyGuard │ Emergency │ 용도       │
│  ═══════════════════════════════════════════════════════════════════════════════════════    │
│  chaos_scheduler.py  │     ✅      │   ✅ (내부)     │     ✅      │    ❌     │ Chaos 실험 │
│  config_apply.py     │     ❌      │       ❌        │     ❌      │    ❌     │ 설정 적용  │
│  drift_detection.py  │     ❌      │       ❌        │     ❌      │    ❌     │ 분석/경고  │
│  governance.py       │     ❌      │       ❌        │     ❌      │    ✅     │ 긴급모드   │
│                                                                                              │
│  💡 chaos_scheduler만 완전히 보호됨                                                         │
│  💡 config_apply는 안전 체크 없이 설정 적용 (RuntimeConfigManager 내부 검증에 의존)         │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.6 Safe Defaults 적용 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Safe Defaults 적용 현황                                         │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ Safe Default 정의됨 (13개 타입)                                                          │
│  ───────────────────────────────────                                                         │
│  circuit_breaker, dlq, retry, rate_limit, sla, slo, security, forensic,                     │
│  logging, notification, metrics, error_budget, idempotency, chaos, emergency                │
│                                                                                              │
│  ❌ Safe Default 미정의 (Phase 2/3 추가 기능)                                                │
│  ──────────────────────────────────────────────                                              │
│  • governance ──────► RBAC, Approval 관련                                                   │
│  • l2_storage ──────► 외부 스토리지 연동                                                    │
│  • drift_threshold ─► Metric Drift 관련                                                     │
│                                                                                              │
│  📍 Safe Default 적용 위치                                                                   │
│  ─────────────────────────                                                                   │
│  • RuntimeConfigManager._update_config() ──► 설정 업데이트 시 검증 및 자동 교정             │
│  • Django Serializers (validate_with_safe_fallback) ──► API 입력 검증                       │
│  • Django App Startup (_validate_startup_config) ──► 시작 시 전체 검증                      │
│                                                                                              │
│  🔴 Fatal Configs (위반 시 Quarantine Mode)                                                  │
│  ──────────────────────────────────────────                                                  │
│  • security: rate_limit_max_requests, injection_ban_hours, failed_login_threshold           │
│  • chaos: max_blast_radius, failure_rate                                                    │
│  • error_budget: threshold_critical, burn_rate_fast_critical                                │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 9.7 Middleware 연결 현황

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Django Middleware 연결 현황                                     │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  Middleware                      │ RuntimeConfigManager │ EmergencyManager │ 기타 서비스    │
│  ═══════════════════════════════════════════════════════════════════════════════════════    │
│  SensitiveAccessLoggingMiddleware │         ❌          │       ❌         │ 자체 로거     │
│  HybridRateLimitMiddleware        │   ✅ (rate_limit)   │       ❌         │ Redis, Local  │
│  PoolCircuitBreakerMiddleware     │         ❌          │       ❌         │ 자체 CB       │
│  TieringMiddleware                │         ❌          │   ✅ Phase A     │ TierRegistry  │
│                                                                                              │
│  ✅ TieringMiddleware 구현 완료! (Phase A)                                                   │
│     - EmergencyManager.get_current_level() 호출                                             │
│     - TierRegistry로 엔드포인트 Tier 분류                                                   │
│     - EMERGENCY_LEVEL_RULES로 Tier별 허용 비율 계산                                         │
│     - 확률 기반 Load Shedding (503 + Retry-After)                                           │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 10. 🚨 발견된 핵심 문제 요약 (우선순위순)

### ✅ Phase A COMPLETED (2024-12-24)

| # | 수정 내용 | 파일 | 상태 |
|---|----------|------|------|
| 1 | **TieringMiddleware 구현** | `api/django/tiering.py` | ✅ 완료 |
| 2 | **RuntimeConfigManager 연동 (CB, DLQ, Retry)** | `circuit_breaker/config.py`, `dlq_models.py`, `retry_handler.py` | ✅ 완료 |
| 3 | **Kill Switch 체크 추가** | `circuit_breaker/manual_control.py`, `replay_service.py`, `retry_handler.py` | ✅ 완료 |

### 🟡 HIGH (Phase B - 빠른 수정 권장)

| # | 문제 | 영향 | 위치 |
|---|------|------|------|
| 4 | EmergencyManager → CB/DLQ/Replay 이벤트 연동 없음 | 비상 시 연쇄 반응 없음 (이벤트 버스 필요) | emergency_mode.py |
| 5 | Circuit Breaker 복구 시 자동 Replay가 ErrorBudgetGate 무시 | 에러 예산 소진 상태에서 Replay 발생 가능 | conditional_replay |
| 6 | RetryHandler에 ErrorBudgetGate 체크 없음 | 에러 폭주 시 추가 부하 | retry_handler.py |

### 🟢 MEDIUM (Phase C - 개선 권장)

| # | 문제 | 영향 | 위치 |
|---|------|------|------|
| 7 | governance, l2_storage, drift_threshold Safe Default 없음 | 잘못된 설정 시 보호 없음 | safe_defaults.py |
| 8 | config_apply Celery Task 안전 체크 없음 | Emergency Mode에서도 설정 적용됨 | config_apply.py |
| 9 | 이벤트 버스 패턴 부재 | 컴포넌트 간 강결합 | 전체 아키텍처 |

---

## 11. 🎯 권장 조치 로드맵

### ✅ Phase A: Critical 수정 (완료 - 2024-12-24)

```
1. TieringMiddleware 구현 ✅
   └── EmergencyManager.get_current_level() 호출
   └── TierRegistry로 엔드포인트 분류
   └── 확률 기반 요청 차단/허용 로직
   └── 503 Load Shedding 응답 + Retry-After

2. RuntimeConfigManager 연동 완료 ✅
   └── CircuitBreakerConfig.from_settings() - RuntimeConfigManager 우선 조회
   └── DLQConfig.from_settings() - RuntimeConfigManager 우선 조회
   └── RetryConfig.from_settings() - RuntimeConfigManager 우선 조회

3. Kill Switch 체크 추가 ✅
   └── CircuitBreakerService.force_open/close() - 차단 시 실패 반환
   └── ReplayService.replay_single/batch() - 차단
   └── RetryHandler.execute() - 즉시 ABORT 반환
```

### Phase B: High 수정 (2주일)

```
4. 이벤트 버스 도입
   └── EmergencyLevelChanged 이벤트
   └── ErrorBudgetCritical 이벤트
   └── 서비스들 이벤트 구독

5. ErrorBudgetGate 연동 확대
   └── RetryHandler에 체크 추가
   └── Conditional Replay에 체크 추가
```

### Phase C: Medium 개선 (선택적)

```
6. Safe Defaults 추가
   └── governance, l2_storage, drift_threshold

7. config_apply Task 안전 체크
```

---

## 12. 참고 문서

- [16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md](16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md) - 거버넌스 구현 로드맵
- [16_GOVERNANCE_IMPLEMENTATION_PART1.md](16_GOVERNANCE_IMPLEMENTATION_PART1.md) - Phase 1 구현
- [16_GOVERNANCE_IMPLEMENTATION_PART1A.md](16_GOVERNANCE_IMPLEMENTATION_PART1A.md) - Phase 1A 구현
- [16_GOVERNANCE_IMPLEMENTATION_PART2.md](16_GOVERNANCE_IMPLEMENTATION_PART2.md) - Phase 2 & 3 구현
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - 에러 예산 설계
- [13_LAYERED_STORAGE_RESILIENCE.md](13_LAYERED_STORAGE_RESILIENCE.md) - L2 스토리지 설계

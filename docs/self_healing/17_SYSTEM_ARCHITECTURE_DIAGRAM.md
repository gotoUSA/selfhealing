# Self-Healing System Architecture Diagram

> 📅 작성일: 2024-12-24  
> 🎯 목적: 셀프 힐링 시스템의 핵심 컨트롤러와 컴포넌트 간 연결 관계 시각화

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
│  │    │ • 21개 Config     │    │ • Kill Switch     │    │ • Level 0~3      │              │    │
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
│  │                              21개 Config Types                                      │ │
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
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────────────┐  │ │
│  │  │ slo             │  │ emergency       │  │ approval_requests (4-Eyes)          │  │ │
│  │  │ • definitions   │  │ • auto_trigger  │  │ • PENDING → APPROVED/REJECTED       │  │ │
│  │  │ • targets       │  │ • recovery      │  │ • 듀얼 승인 워크플로우              │  │ │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────────────────────────┘  │ │
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
│  │                         Kill Switch 체크 위치                       │ │
│  │                                                                     │ │
│  │    • CircuitBreakerService.force_open/close()                      │ │
│  │    • ReplayService.replay_single/batch()                           │ │
│  │    • RetryHandler.execute()                                        │ │
│  │    • ChaosExperiment.start()                                       │ │
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
│  ┌────────────────────────────────────────────────────────────────────────────────────┐ │
│  │                       TTL 캐시 (Check on Use 패턴)                                  │ │
│  │                                                                                     │ │
│  │    • get_current_level() 호출 시 TTL 확인                                          │ │
│  │    • 캐시 만료 시 (기본 30초) StateBackend 재조회                                  │ │
│  │    • 이벤트 버스는 "즉시 캐시 무효화" 역할                                         │ │
│  │                                                                                     │ │
│  └────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                          │
└─────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 이벤트 기반 연결 아키텍처

```
                              ┌───────────────────────────────────┐
                              │         Event Bus (In-Memory)     │
                              │                                   │
                              │  • EmergencyLevelChanged          │
                              │  • ErrorBudgetCritical            │
                              │  • ErrorBudgetWarning             │
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
│  listen: BudgetCritical │     │  emit: BudgetWarning    │     │  listen: LevelChanged   │
│                         │     │  listen: LevelChanged   │     │                         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
              │                           │                               │
              ▼                           ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│    ReplayService        │     │    ChaosExperiment      │     │    NotificationService  │
│                         │     │                         │     │                         │
│  listen: LevelChanged   │     │  listen: BudgetCritical │     │  listen: ALL EVENTS     │
│  → pause auto-replay    │     │  → block experiment     │     │  → alert routing        │
│  (LEVEL_2+ 시 차단)     │     │                         │     │                         │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
              │                           │                               │
              ▼                           ▼                               ▼
                              ┌───────────────────────────────────────────────────┐
                              │                  AuditService                     │
                              │                                                   │
                              │  listen: ALL EVENTS (LevelChanged, StateChanged, │
                              │          ConfigUpdated, BudgetCritical, etc.)    │
                              │  → Persistent Logging to AuditLogRepository      │
                              │  → Compliance Trail (SOX, GDPR)                  │
                              │                                                   │
                              └───────────────────────────────────────────────────┘
```

---

## 4. 컴포넌트 간 연결 다이어그램

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
│                                🎛️ Middleware Layer                                            │
│                                                                                               │
│  ┌─────────────────────────────────────────────────────────────────────────────────────────┐ │
│  │  TieringMiddleware ──► EmergencyLevel 기반 트래픽 제어 (Load Shedding)                  │ │
│  │  HybridRateLimitMiddleware ──► Rate Limiting (Redis + Local)                            │ │
│  │  PoolCircuitBreakerMiddleware ──► Connection Pool Circuit Breaker                       │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
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
│  │   │   runtime_config:*    │   emergency_state   │   system_control   │   governance:* │  │ │
│  │   └───────────────────────────────────────────────────────────────────────────────────┘  │ │
│  │                                                                                          │ │
│  └─────────────────────────────────────────────────────────────────────────────────────────┘ │
│                                                                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
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

## 5. 설정별 연결 관계 상세

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                        RuntimeConfigManager 설정 → 서비스 연결                               │
└─────────────────────────────────────────────────────────────────────────────────────────────┘

 RuntimeConfigManager                           연결된 서비스/컴포넌트
 ═══════════════════                           ════════════════════════

 circuit_breaker ─────────────────────────────► CircuitBreakerService
       │                                        CircuitBreakerConfig.from_settings()
       │ • failure_threshold                          
       │ • recovery_timeout              
       │ • self_ddos_protection          

 dlq ────────────────────────────────────────► DLQService
       │                                        DLQConfig.from_settings()
       │ • max_retries                               
       │ • retry_delay                  
       │ • expiry_hours                 

 retry ──────────────────────────────────────► RetryHandler
       │                                        RetryConfig.from_settings()
       │ • max_attempts                              
       │ • backoff_strategy              
       │ • jitter                       

 rate_limit ─────────────────────────────────► RateLimitCoordinator
       │                                        HybridRateLimitMiddleware
       │ • control_api_rate_limit                    
       │ • emergency_rate_limit                      
       │ • backoff_multiplier            

 error_budget ───────────────────────────────► ErrorBudgetGate
       │                                              
       │ • threshold_healthy                         
       │ • burn_rate_*                  
       │ • heartbeat_*                  

 governance ─────────────────────────────────► EmergencyModeTracker
       │                                        GovernanceService
       │ • threshold_operator/admin                  
       │ • emergency_expiry_hours                    
       │ • four_eyes_enabled            

 l2_storage ─────────────────────────────────► L2StorageManager
       │                                             
       │ • redis_timeout_ms                          
       │ • shadow_log_enabled           
       │ • reconciliation_jitter        

 drift_threshold ────────────────────────────► DriftDetector
       │                                             
       │ • warning_percent                           
       │ • critical_percent             
       │ • check_interval_seconds       

 chaos ──────────────────────────────────────► ChaosExperiment
       │                                        SafetyGuard
       │ • max_blast_radius                          
       │ • auto_rollback_enabled                     
       │ • stop_on_error_rate           

 notification ───────────────────────────────► NotificationService
       │                                              
       │ • channels (slack/email)                    
       │ • critical/high/medium_channel 
       │ • slack_block_text_limit       

 metrics ────────────────────────────────────► MetricsCollector
       │                                              
       │ • jitter_enabled                            
       │ • jitter_max_delay_seconds     
       │ • collection_interval          
```

---

## 6. Safe Defaults 체계

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                              Safe Default 적용 체계                                          │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  📋 Safe Default 정의됨 (21개 타입)                                                          │
│  ───────────────────────────────────                                                         │
│  circuit_breaker, dlq, retry, rate_limit, sla, slo, security, forensic,                     │
│  logging, notification, metrics, error_budget, idempotency, chaos, emergency,               │
│  governance, l2_storage, drift_threshold                                                    │
│                                                                                              │
│  🔴 Fatal Configs (위반 시 Quarantine Mode)                                                  │
│  ──────────────────────────────────────────                                                  │
│  • security: rate_limit_max_requests, injection_ban_hours, failed_login_threshold           │
│  • chaos: max_blast_radius, failure_rate                                                    │
│  • error_budget: threshold_critical, burn_rate_fast_critical                                │
│                                                                                              │
│  📍 Safe Default 적용 위치                                                                   │
│  ─────────────────────────                                                                   │
│  • RuntimeConfigManager._update_config() ──► 설정 업데이트 시 검증 및 자동 교정             │
│  • Django Serializers (validate_with_safe_fallback) ──► API 입력 검증                       │
│  • Django App Startup (_validate_startup_config) ──► 시작 시 전체 검증                      │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. ErrorBudgetGate 자동화 차단 체계

```
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                          ErrorBudgetGate 자동화 차단 체계                                    │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                              │
│  ✅ ErrorBudgetGate 체크하는 자동화 서비스                                                   │
│  ──────────────────────────────────────────                                                  │
│  • ChaosScheduler ──────────► SafetyGuard.pre_flight_check()                                │
│  • DLQ Single Replay ───────► Celery Task                                                   │
│  • DLQ Batch Replay ────────► Celery Task                                                   │
│  • RetryHandler ────────────► execute() 시작 시 체크                                        │
│  • Conditional Replay ──────► CB 복구 시 자동 Replay 전 체크                                │
│                                                                                              │
│  📊 ErrorBudgetGate 이벤트 발행                                                              │
│  ──────────────────────────────                                                              │
│  • 예산 < critical → ERROR_BUDGET_CRITICAL 이벤트                                           │
│  • critical < 예산 < warning → ERROR_BUDGET_WARNING 이벤트                                  │
│                                                                                              │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 8. Celery Tasks 안전 체크 매트릭스

```
┌──────────────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                                    Celery Tasks 안전 체크 매트릭스                                            │
├──────────────────────────────────────────────────────────────────────────────────────────────────────────────┤
│                                                                                                               │
│  Task 모듈           │ Kill Switch │ ErrorBudgetGate │ Emergency │ 비고                                       │
│  ════════════════════════════════════════════════════════════════════════════════════════════════════════    │
│  chaos_scheduler.py  │     ✅      │   ✅ (SafetyGuard) │    ❌    │ Chaos 실험 스케줄링                      │
│  config_apply.py     │     ❌      │       ❌          │    ✅    │ 관리자 수동 조작 허용 (복구 경로 확보)   │
│  drift_detection.py  │     ❌      │       ❌          │    ❌    │ 분석/경고만 수행 (안전한 읽기 작업)      │
│  governance.py       │     ❌      │       ❌          │    ✅    │ 긴급모드 관리 (자체가 Emergency 관련)    │
│  dlq_replay.py       │     ✅      │       ✅          │    ✅    │ LEVEL_2↑ 시 자동 리플레이 차단          │
│                                                                                                               │
│  📋 Kill Switch 설계 철학                                                                                     │
│  ───────────────────────                                                                                      │
│  "자동화는 막되, 관리는 열어라" - Kill Switch가 활성화된 장애 상황에서도 관리자가                            │
│  설정을 변경하여 복구할 수 있는 퇴로를 확보함. 설정 변경까지 차단하면 데드락 상태 발생.                      │
│                                                                                                               │
│  📋 Thin Task, Fat Service 아키텍처 원칙 (2024-12-24 적용)                                                   │
│  ────────────────────────────────────────────────────                                                         │
│  • Celery Task (Thin): 오직 "언제(When)" 실행할지와 비동기 트리거 역할만 수행                                │
│    - 모든 Task는 service.method() 호출 한 줄로 유지                                                          │
│    - 비즈니스 로직, 거버넌스 체크 없음                                                                        │
│                                                                                                               │
│  • Service Layer (Fat): 모든 판단 로직(Check on Use) 포함                                                    │
│    - GovernanceService: 비상모드 만료, 자동 복구                                                             │
│    - ChaosExecutionService: 실험 실행, SafetyGuard 통합                                                      │
│    - ConfigApplyService: 설정 적용, Emergency 체크                                                           │
│    - ReplayService: DLQ 리플레이, 3단계 안전 체크                                                            │
│                                                                                                               │
│  • 공통 체크 로직: governance_checks.py                                                                       │
│    - require_system_enabled: Kill Switch 체크 데코레이터                                                     │
│    - require_not_emergency: Emergency Level 체크 데코레이터                                                  │
│    - require_error_budget: ErrorBudget 체크 데코레이터                                                       │
│    - GovernanceCheckMixin: 서비스에서 상속받아 사용                                                          │
│    - Audit Logging: 차단 발생 시 자동으로 AuditLogAdapter에 기록                                            │
│                                                                                                               │
│  📋 서비스 레이어 위치 (packages/selfhealing-python/src/selfhealing/services/)                               │
│  ────────────────────────────────────────────────────────────────────────────                                 │
│  • governance_checks.py: 공통 거버넌스 체크 (데코레이터, 믹스인, TTL 캐시)                                   │
│  • governance_service.py: GovernanceService (비상모드 관리)                                                  │
│  • execution_services.py: ChaosExecutionService, ConfigApplyService                                         │
│  • replay_service.py: ReplayService (DLQ 리플레이)                                                           │
│                                                                                                               │
│  📋 Audit Logging 연동 ("왜 이때 작업이 안 됐지?")                                                           │
│  ────────────────────────────────────────────────                                                             │
│  거버넌스 체크에서 차단이 발생하면 자동으로 AuditLogAdapter를 통해 기록됩니다:                               │
│  • AuditAction.GOVERNANCE_BLOCKED: 일반 차단                                                                 │
│  • AuditAction.GOVERNANCE_KILL_SWITCH: Kill Switch에 의한 차단                                              │
│  • AuditAction.GOVERNANCE_EMERGENCY: 비상 모드에 의한 차단                                                   │
│  • AuditAction.GOVERNANCE_ERROR_BUDGET: 에러 예산 고갈에 의한 차단                                           │
│                                                                                                               │
│  기록 정보: operation_name, service_name, domain, block_reason, details (레벨, 예산 등)                     │
│                                                                                                               │
└──────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Check on Use 패턴 (Celery-Free 상태 동기화)

```
┌────────────────────────────────────────────────────────────────────┐
│               Check on Use Pattern (Celery-Free)                   │
├────────────────────────────────────────────────────────────────────┤
│                                                                    │
│  서비스 A                           서비스 B                        │
│  ┌──────────────┐                 ┌──────────────┐                 │
│  │ 상태 변경    │                 │ 상태 조회    │                 │
│  │ activate()   │                 │ get_level()  │                 │
│  └──────┬───────┘                 └──────┬───────┘                 │
│         │                                │                         │
│         │  1. StateBackend 저장          │ 2-a. 이벤트 수신 시     │
│         ├──────────────────────────────► │     캐시 즉시 무효화    │
│         │                                │                         │
│         │  1-a. 이벤트 발행              │ 2-b. 또는 TTL 만료 시   │
│         ├─────────────[Event Bus]───────►│     StateBackend 재조회 │
│         │                                │                         │
│         ▼                                ▼                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    StateBackend (Source of Truth)            │  │
│  │                    Redis → File → Memory Fallback            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

---

## 참고 문서

- [16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md](16_GOVERNANCE_IMPLEMENTATION_ROADMAP.md) - 거버넌스 구현 로드맵
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - 에러 예산 설계
- [13_LAYERED_STORAGE_RESILIENCE.md](13_LAYERED_STORAGE_RESILIENCE.md) - L2 스토리지 설계
- [PHASE_ABC_CHANGELOG.md](PHASE_ABC_CHANGELOG.md) - Phase A/B/C 수정 이력

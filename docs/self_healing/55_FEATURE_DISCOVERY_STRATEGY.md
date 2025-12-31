# 55. 숨겨진 기능 탐색 및 발견 전략

> **문서 버전**: 1.1.0
> **생성일**: 2025-12-31
> **최종 수정**: 2025-12-31
> **목적**: 시스템의 모든 기능을 체계적으로 발견하고 연결 상태를 확인하는 전략

---

## 📋 목차

1. [개요](#1-개요)
2. [7가지 기본 탐색 전략](#2-7가지-기본-탐색-전략)
3. [6가지 추가 탐색 전략 (고급)](#3-6가지-추가-탐색-전략-고급)
   - [3.1 전략 8️⃣: 설정 vs 구현 비교](#31-전략-8️⃣-설정-vs-구현-비교)
   - [3.2 전략 9️⃣: Import 역추적](#32-전략-9️⃣-import-역추적)
   - [3.3 전략 🔟: Factory/Registry 패턴 확인](#33-전략--factoryregistry-패턴-확인)
   - [3.4 전략 1️⃣1️⃣: 시그널 및 이벤트 핸들러 추적](#34-전략-1️⃣1️⃣-시그널-및-이벤트-핸들러-추적)
   - [3.5 전략 1️⃣2️⃣: 환경 변수 기반 기능 토글](#35-전략-1️⃣2️⃣-환경-변수-기반-기능-토글)
   - [3.6 전략 1️⃣3️⃣: Management Commands 탐색](#36-전략-1️⃣3️⃣-management-commands-탐색)
   - [3.7 전략 1️⃣4️⃣: Context Manager 추적](#37-전략-1️⃣4️⃣-context-manager-및-protocol-추적)
   - [3.8 전략 1️⃣5️⃣: 추상 클래스 구현체 추적](#38-전략-1️⃣5️⃣-추상-클래스-및-인터페이스-구현체-추적)
   - [3.9 전략 1️⃣6️⃣: 싱글톤 및 전역 인스턴스 추적](#39-전략-1️⃣6️⃣-싱글톤-및-전역-인스턴스-추적)
4. [연결 상태 검증 전략](#4-연결-상태-검증-전략)
5. [자동화 스크립트](#5-자동화-스크립트)
6. [탐색 결과 예시](#6-탐색-결과-예시)

---

## 1. 개요

### 1.1 왜 기능 탐색이 필요한가?

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      기능 탐색의 필요성                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  1. 문서화 누락 발견                                                     │
│     - 코드는 있는데 문서가 없는 경우                                     │
│     - 새 기능 추가 후 문서 업데이트 누락                                 │
│                                                                          │
│  2. 미연결 기능 발견                                                     │
│     - 정의됐지만 MIDDLEWARE에 없는 미들웨어                              │
│     - 구현됐지만 URL에 연결 안 된 View                                   │
│     - 만들어졌지만 스케줄러에 없는 Task                                  │
│                                                                          │
│  3. 죽은 코드 발견                                                       │
│     - 더 이상 사용되지 않는 기능                                         │
│     - import하는 곳이 없는 모듈                                          │
│                                                                          │
│  4. 테스트 커버리지 갭 발견                                              │
│     - 기능은 있는데 테스트가 없는 경우                                   │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 탐색 전략 개요

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    기능 탐색 전략 맵                                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    기본 7가지 전략                               │    │
│  ├──────────────────────┬──────────────────────────────────────────┤    │
│  │ 1️⃣ URL 기반          │ urls.py → views.py → services           │    │
│  │ 2️⃣ 미들웨어          │ grep "class.*Middleware"                │    │
│  │ 3️⃣ Celery 태스크     │ grep "@shared_task|@app.task"           │    │
│  │ 4️⃣ 서비스 레이어     │ services/ 폴더 스캔                     │    │
│  │ 5️⃣ Core 컴포넌트     │ core/ 폴더 스캔                         │    │
│  │ 6️⃣ __init__.py       │ export 확인                              │    │
│  │ 7️⃣ 데코레이터        │ grep "@circuit_breaker|@rate_limit"     │    │
│  └──────────────────────┴──────────────────────────────────────────┘    │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                    고급 6가지 전략 (NEW)                         │    │
│  ├──────────────────────┬──────────────────────────────────────────┤    │
│  │ 8️⃣ 설정 비교         │ 정의 vs 설정 (MIDDLEWARE, CELERY_BEAT)  │    │
│  │ 9️⃣ Import 역추적     │ from xxx import YYY 검색                │    │
│  │ 🔟 Factory/Registry  │ 등록된 것 vs 생성되는 것                 │    │
│  │ 1️⃣1️⃣ 시그널/이벤트   │ @receiver, apps.py ready()              │    │
│  │ 1️⃣2️⃣ Feature Flags  │ os.getenv, getattr(settings)            │    │
│  │ 1️⃣3️⃣ Mgmt Commands  │ management/commands/ 스캔               │    │
│  │ 1️⃣4️⃣ Context Mgr    │ __enter__/__exit__, @contextmanager     │    │
│  │ 1️⃣5️⃣ 추상클래스구현 │ ABC, abstractmethod 구현체              │    │
│  │ 1️⃣6️⃣ 싱글톤/전역    │ __new__, _instance, @lru_cache          │    │
│  └──────────────────────┴──────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 7가지 기본 탐색 전략

### 2.1 전략 1️⃣: URL 기반 탐색

**목적**: API로 노출된 모든 기능 발견

**명령어**:
```bash
# 모든 URL 패턴 확인
grep -r "path\|re_path\|url" */urls.py --include="*.py"

# View 클래스/함수 확인
grep -r "class.*View\|def.*view\|@api_view" --include="*.py"

# 연결 관계 추적
# urls.py → views.py → services.py
```

**발견 가능한 것**:
| 카테고리 | 예시 |
|----------|------|
| REST API 엔드포인트 | `/api/orders/`, `/api/payments/` |
| Admin 페이지 | `/admin/` |
| Self-Healing API | `/api/self-healing/` |
| Health Check | `/health/`, `/api/self-healing/health/` |

**연결 확인**:
```bash
# URL이 정의됐지만 View가 없는 경우 찾기
# 1. urls.py에서 사용된 View 목록 추출
# 2. views.py에 실제 정의 확인
```

---

### 2.2 전략 2️⃣: 미들웨어 탐색

**목적**: 모든 미들웨어 클래스 발견 및 연결 상태 확인

**명령어**:
```bash
# 모든 Middleware 클래스 찾기
grep -r "class.*Middleware" --include="*.py"

# 현재 설정된 MIDDLEWARE 확인
grep -A50 "MIDDLEWARE\s*=" settings/*.py
```

**발견 결과 예시**:
```
발견된 미들웨어: 12개
├─ HealthBridgeMiddleware          ✅ 연결됨 (base.py)
├─ SelfHealingMiddleware           ✅ 연결됨 (base.py)
├─ HybridRateLimitMiddleware       ✅ 연결됨 (base.py)
├─ ChaosMiddleware                 ✅ 연결됨 (base.py)
├─ ConnectionPoolLimiterMiddleware ✅ 연결됨 (base.py)
├─ TieringMiddleware               ❌ 미연결 (선택적)
├─ SensitiveAccessLoggingMiddleware ❌ 미연결 (선택적)
├─ ActorContextMiddleware          ❌ 미연결 (선택적)
├─ PoolCircuitBreakerMiddleware    ⚠️ 제외됨 (블로킹 이슈)
├─ PoolTimeoutMiddleware           ⚠️ local.py만
├─ ShutdownMiddleware              🟢 FastAPI용
└─ SelfHealingMiddleware (FastAPI) 🟢 FastAPI용
```

---

### 2.3 전략 3️⃣: Celery 태스크 탐색

**목적**: 모든 비동기 태스크 발견 및 스케줄 연결 확인

**명령어**:
```bash
# 모든 Celery 태스크 찾기
grep -r "@shared_task\|@app.task" --include="*.py"

# 태스크 이름 추출
grep -r "name=\".*\"" --include="*.py" | grep -E "@shared_task|def.*task"

# Beat 스케줄 확인
grep -A100 "CELERY_BEAT_SCHEDULE" settings/*.py
```

**발견 결과 예시**:
```
발견된 태스크: 25개

shopping/tasks/
├─ cleanup_tasks.py
│   ├─ cleanup_expired_sessions      ✅ Beat 연결됨
│   ├─ cleanup_old_logs              ✅ Beat 연결됨
│   └─ vacuum_database               ✅ Beat 연결됨
├─ order_tasks.py
│   └─ process_order_async           🔄 수동 호출용
├─ payment_recovery_tasks.py
│   ├─ retry_failed_payment          🔄 수동 호출용
│   └─ bulk_payment_recovery         ✅ Beat 연결됨
├─ point_tasks.py
│   ├─ process_single_user_points    🔄 내부 호출
│   └─ cleanup_old_point_histories   ✅ Beat 연결됨
└─ self_healing_tasks.py
    ├─ apply_config_change           🔄 Control API에서 호출
    ├─ validate_pending_config       ✅ Beat 연결됨
    └─ sync_config_to_workers        ✅ Beat 연결됨

selfhealing/tasks/
├─ config_apply.py
│   ├─ apply_pending_config          ✅ Beat 연결됨
│   ├─ rollback_config_change        🔄 수동 호출용
│   └─ cleanup_expired_config_changes ✅ Beat 연결됨
└─ governance.py
    └─ run_governance_check          ✅ Beat 연결됨
```

---

### 2.4 전략 4️⃣: 서비스 레이어 탐색

**목적**: 비즈니스 로직 서비스 발견

**명령어**:
```bash
# 모든 Service 클래스 찾기
grep -r "class.*Service" --include="*.py"

# services/ 폴더 구조 확인
find . -path "*/services/*.py" -type f | head -50
```

**발견 결과 예시**:
```
packages/selfhealing-python/src/selfhealing/services/
├─ circuit_breaker_service.py      ✅ URL 연결
├─ dlq_service.py                  ✅ URL 연결
├─ replay_service.py               ✅ URL 연결
├─ dashboard_service.py            ✅ URL 연결
├─ error_budget_service.py         ✅ URL 연결
├─ forensic_advisor.py             ✅ URL 연결
├─ idempotency_service.py          ⚪ 테스트 전용 (도메인 프리)
├─ security_violation_service.py   ⚪ 테스트 전용 (도메인 프리)
├─ security_notification_service.py ⚪ 간접 사용
├─ auto_tuning/                    ✅ URL 연결
├─ blast_radius/                   ✅ URL 연결
├─ rollback/                       ✅ URL 연결
├─ chaos/                          ✅ URL 연결
├─ throttle/                       ✅ 내부 사용
└─ error_budget_gate/              ✅ 내부 사용

shopping/services/
├─ order_service.py                ✅ View에서 사용
├─ payment_service.py              ✅ View에서 사용
├─ user_service.py                 ✅ View에서 사용
└─ ...
```

---

### 2.5 전략 5️⃣: Core 컴포넌트 탐색

**목적**: 인프라 레벨 컴포넌트 발견

**명령어**:
```bash
# core/ 폴더 전체 스캔
ls -la packages/selfhealing-python/src/selfhealing/core/

# 각 모듈의 주요 클래스 확인
grep -r "^class " packages/selfhealing-python/src/selfhealing/core/*.py
```

**발견 결과 예시**:
```
selfhealing/core/
├─ backoff.py                 ✅ RetryHandler에서 사용
├─ cert_monitor.py            ⚪ 테스트 전용 (인프라용)
├─ circuit_breaker.py         ✅ 전역 사용
├─ config.py                  ✅ 전역 사용
├─ connection_health.py       ✅ PoolMonitor에서 사용
├─ decision_engine.py         ✅ 내부 사용
├─ forensic.py                ✅ ForensicAdvisor에서 사용
├─ hooks.py                   ✅ HookRegistry 전역 사용
├─ pool_monitor.py            ✅ 미들웨어에서 사용
├─ pool_watchdog.py           ✅ 내부 사용
├─ request_context.py         ✅ 미들웨어에서 사용
├─ runtime_feedback.py        ✅ AutoTuning에서 사용
├─ safe_defaults.py           ✅ 전역 사용
├─ safety_bounds.py           ✅ AutoTuning에서 사용
├─ shutdown_coordinator.py    ✅ FastAPI adapter에서 사용
├─ state_backend.py           ✅ 전역 사용
├─ state_cache.py             ✅ 내부 사용
├─ timezone.py                ✅ 전역 사용
├─ tls_handler.py             ⚪ 테스트 전용 (외부 API용)
└─ types.py                   ✅ 전역 사용
```

---

### 2.6 전략 6️⃣: __init__.py exports 확인

**목적**: 외부에서 사용 가능한 Public API 확인

**명령어**:
```bash
# __init__.py에서 export되는 항목 확인
grep -r "^from\|^__all__" packages/selfhealing-python/src/selfhealing/**/__init__.py
```

**예시**:
```python
# selfhealing/__init__.py
__all__ = [
    "SelfHealingMiddleware",
    "CircuitBreaker",
    "DLQService",
    "get_config",
    # ...
]

# selfhealing/api/django/tiering/__init__.py
__all__ = [
    "TieringMiddleware",
    "TierRegistry",
    "TierDefinition",
    "TierMapping",
    # ...
]
```

---

### 2.7 전략 7️⃣: 데코레이터 탐색

**목적**: 함수에 자동 적용되는 기능 발견

**명령어**:
```bash
# 커스텀 데코레이터 정의 찾기
grep -r "def.*decorator\|^def.*wrapper" --include="*.py"

# 데코레이터 사용처 찾기
grep -r "@circuit_breaker\|@rate_limit\|@with_retry\|@selfhealing" --include="*.py"
```

**발견된 데코레이터 예시**:
```
자체 정의 데코레이터:
├─ @with_retry            - 재시도 로직 적용
├─ @track_replay          - Replay 추적
├─ @automation_gate       - Error Budget Gate
├─ @require_not_emergency - Emergency 모드 체크
├─ @require_error_budget  - Error Budget 체크
├─ @register_bypass_hook  - Bypass Hook 등록
└─ @selfhealing_task      - Celery 태스크 래핑
```

---

## 3. 6가지 추가 탐색 전략 (고급)

### 3.1 전략 8️⃣: 설정 vs 구현 비교

**목적**: 정의됐지만 설정에서 빠진 기능 발견

**비교 대상**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│                     설정 vs 구현 비교                                    │
├────────────────────────┬────────────────────────────────────────────────┤
│ 비교 대상              │ 비교 방법                                      │
├────────────────────────┼────────────────────────────────────────────────┤
│ MIDDLEWARE 설정        │ settings.py MIDDLEWARE vs class.*Middleware   │
│ INSTALLED_APPS         │ settings.py vs 실제 앱 폴더                   │
│ CELERY_BEAT_SCHEDULE   │ 스케줄 설정 vs @shared_task 정의              │
│ URL patterns           │ urls.py vs views.py                           │
│ Admin 등록             │ admin.site.register vs models                 │
└────────────────────────┴────────────────────────────────────────────────┘
```

**스크립트**:
```bash
#!/bin/bash
# compare_middleware.sh

echo "=== 정의된 미들웨어 ==="
grep -r "class.*Middleware" --include="*.py" | grep -v test | grep -v "__pycache__"

echo ""
echo "=== 설정된 미들웨어 ==="
grep -A30 "MIDDLEWARE\s*=" myproject/settings/base.py
```

---

### 3.2 전략 9️⃣: Import 역추적

**목적**: 사용되지 않는 모듈 발견

**명령어**:
```bash
# 특정 모듈이 어디서 import되는지 확인
grep -r "from selfhealing.services.idempotency_service import" --include="*.py"
grep -r "from selfhealing.core.cert_monitor import" --include="*.py"

# import 횟수 카운트
grep -r "from selfhealing.core.cert_monitor import" --include="*.py" | wc -l
```

**분석 결과 해석**:
```
Import 횟수별 분류:
├─ 0회: 미사용 (죽은 코드 또는 의도적 미연결)
├─ 1~2회 (테스트만): 테스트 전용
├─ 3회 이상 (프로덕션 포함): 실제 사용 중
└─ 10회 이상: 핵심 컴포넌트
```

---

### 3.3 전략 🔟: Factory/Registry 패턴 확인

**목적**: 동적으로 등록/생성되는 컴포넌트 발견

**명령어**:
```bash
# Registry 패턴 찾기
grep -r "class.*Registry\|register\|get_registry" --include="*.py"

# Factory 패턴 찾기
grep -r "class.*Factory\|create_\|get_or_create" --include="*.py"
```

**발견되는 패턴**:
```
Registry 패턴:
├─ TierRegistry          - API Tier 등록
├─ HookRegistry          - Bypass Hook 등록
├─ HandlerRegistry       - Replay Handler 등록
└─ DNARegistry           - DNA 진단 규칙 등록

Factory 패턴:
├─ ServiceFactory        - 서비스 인스턴스 생성
├─ CircuitBreakerFactory - CB 인스턴스 관리
└─ CacheFactory          - 캐시 제공자 생성
```

---

### 3.4 전략 1️⃣1️⃣: 시그널 및 이벤트 핸들러 추적 (Decoupled Logic)

**목적**: 직접 호출(Import) 없이 특정 사건에 자동 실행되는 로직 발견

코드상에서 직접 호출하지 않아도 특정 사건이 발생하면 자동으로 실행됩니다.

**명령어**:
```bash
# Django Signals 찾기
grep -r "@receiver" --include="*.py"
grep -r "Signal\|post_save\|pre_save\|post_delete\|pre_delete" --include="*.py"

# 시그널이 등록되는 apps.py의 ready() 메서드 확인
grep -rA10 "def ready(self)" --include="*.py"

# 내부 Pub-Sub 이벤트 핸들러
grep -r "subscribe\|on_event\|add_handler\|add_listener" --include="*.py"
```

**발견 결과 예시 (현재 프로젝트)**:
```
shopping/signals.py:
├─ @receiver(pre_social_login)      ✅ 소셜 로그인 시 이메일 자동 인증
├─ @receiver(post_save, Order)      ✅ 주문 생성 시 주문번호 자동 생성
└─ @receiver(post_save, SocialAccount) ✅ 신규 소셜 계정 시 이메일 인증

shopping/models/product.py:
├─ @receiver([post_save, post_delete], Category) ✅ 카테고리 캐시 무효화
└─ @receiver([post_save, post_delete], Product)  ✅ 상품 캐시 무효화

apps.py의 ready():
├─ shopping.apps.ShoppingConfig.ready()    ✅ 시그널 import
└─ selfhealing.adapters.django.apps.ready() ✅ 설정 초기화
```

**주의사항**:
```
⚠️ 시그널은 import만으로 활성화됨!
   - apps.py의 ready()에서 signals 모듈을 import 해야 함
   - import 누락 시 시그널이 동작하지 않음
```

---

### 3.5 전략 1️⃣2️⃣: 환경 변수 기반 기능 토글 (Feature Flags)

**목적**: 환경 변수에 따라 조건부로 활성화되는 숨겨진 기능 발견

코드에 로직은 있지만 특정 환경 변수가 없으면 실행되지 않습니다.

**명령어**:
```bash
# 환경 변수 기반 분기 찾기
grep -r "os\.getenv\|os\.environ\|getattr(settings" --include="*.py"

# 특정 패턴
grep -r "ENABLE_\|DISABLE_\|FEATURE_\|DEBUG\|CHAOS_" --include="*.py" | grep -v test
```

**발견된 Feature Flags (현재 프로젝트)**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│                   환경 변수 기반 기능 토글 목록                          │
├──────────────────────────────────┬──────────────────────────────────────┤
│ 환경 변수                        │ 기능                                 │
├──────────────────────────────────┼──────────────────────────────────────┤
│ DISABLE_SELFHEALING_AUTH         │ Self-Healing API 인증 비활성화       │
│ ENABLE_RESILIENCE_TESTING        │ 복원력 테스트 모드 활성화            │
│ CHAOS_ENABLED                    │ Chaos Engineering 기능 활성화        │
│ CHAOS_MIDDLEWARE_ENABLED         │ Chaos 미들웨어 활성화                │
│ POOL_LIMITER_ENABLED             │ 커넥션 풀 리미터 활성화              │
│ ENABLE_STRESS_TESTS              │ 스트레스 테스트 URL 노출             │
│ USE_CONNECTION_POOL              │ 커넥션 풀 사용 여부                  │
│ DISABLE_RATE_LIMITING            │ Rate Limiting 비활성화               │
│ SELFHEALING_WAL_ENABLED          │ Write-Ahead Log 활성화               │
├──────────────────────────────────┼──────────────────────────────────────┤
│ AUDIT_HEARTBEAT_URL              │ Audit Watchdog 하트비트 URL          │
│ AUDIT_BUFFER_CAPACITY            │ Audit 버퍼 크기                      │
│ AUDIT_FLUSH_INTERVAL             │ Audit 플러시 간격                    │
├──────────────────────────────────┼──────────────────────────────────────┤
│ POOL_CB_FAILURE_THRESHOLD        │ Pool Circuit Breaker 실패 임계값     │
│ POOL_CB_RECOVERY_TIMEOUT         │ Pool Circuit Breaker 복구 타임아웃   │
│ SELFHEALING_THRESHOLD_OPERATOR   │ Operator 권한 임계값                 │
│ SELFHEALING_THRESHOLD_ADMIN      │ Admin 권한 임계값                    │
└──────────────────────────────────┴──────────────────────────────────────┘
```

---

### 3.6 전략 1️⃣3️⃣: Management Commands 탐색

**목적**: Django manage.py로 실행 가능한 커스텀 명령어 발견

**명령어**:
```bash
# 모든 Management Commands 찾기
find . -path "*/management/commands/*.py" -type f | grep -v __pycache__

# Command 클래스 확인
grep -r "class Command(BaseCommand)" --include="*.py"
```

**발견된 Commands (현재 프로젝트)**:
```
shopping/management/commands/
├─ cleanup_old_carts.py            # 오래된 장바구니 정리
├─ create_test_data.py             # 테스트 데이터 생성
├─ create_load_test_users.py       # 부하 테스트용 유저 생성
├─ delete_unverified_users.py      # 미인증 유저 삭제
├─ generate_self_healing_alerts.py # Self-Healing 알림 생성
├─ security_review.py              # 보안 리뷰 실행
└─ test_point_expiry.py            # 포인트 만료 테스트

selfhealing/management/commands/ (라이브러리)
├─ selfhealing_status.py           # 시스템 상태 확인
├─ selfhealing_config.py           # 설정 관리
└─ audit_export.py                 # Audit 로그 내보내기
```

**연결 확인**:
```bash
# crontab 또는 Celery Beat에서 호출되는지 확인
grep -r "call_command\|manage.py" --include="*.py"
```

---

### 3.7 전략 1️⃣4️⃣: Context Manager 및 Protocol 추적

**목적**: `with` 문으로 사용되는 컨텍스트 관리자 발견

**명령어**:
```bash
# Context Manager 찾기
grep -r "@contextmanager\|__enter__\|__exit__\|__aenter__\|__aexit__" --include="*.py"

# async context manager
grep -r "async with\|asynccontextmanager" --include="*.py"
```

**발견된 Context Managers**:
```
selfhealing/audit/wal.py:
├─ WriteAheadLog.__enter__/__exit__   # WAL 트랜잭션 관리

selfhealing/audit/resilient_recorder.py:
├─ ResilientRecorder.__enter__/__exit__ # 배경 플러시 스레드 관리

selfhealing/core/request_context.py:
├─ RequestContext.__enter__/__exit__  # 요청 컨텍스트 관리
└─ @contextmanager request_scope()    # 요청 스코프 헬퍼
```

---

### 3.8 전략 1️⃣5️⃣: 추상 클래스 및 인터페이스 구현체 추적

**목적**: 추상 클래스의 구현체가 모두 연결되었는지 확인

**명령어**:
```bash
# 추상 클래스 정의 찾기
grep -r "ABC\|abstractmethod\|Protocol\[" --include="*.py"

# 특정 추상 클래스의 구현체 찾기
grep -r "class.*(\(.*BaseAdapter\|.*Protocol\)" --include="*.py"
```

**발견된 추상 클래스와 구현체**:
```
┌─────────────────────────────────────────────────────────────────────────┐
│               추상 클래스 → 구현체 매핑                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ BaseWORMAdapter (worm_adapters.py)                                      │
│ ├─ S3ObjectLockAdapter      ⚪ 테스트 전용 (AWS S3)                     │
│ ├─ LokiAdapter              ⚪ 테스트 전용 (Grafana Loki)               │
│ └─ HTTPWebhookAdapter       ⚪ 테스트 전용 (Webhook)                    │
├─────────────────────────────────────────────────────────────────────────┤
│ BaseThrottleStrategy (throttle/base.py)                                 │
│ ├─ TokenBucketStrategy      ✅ 연결됨                                   │
│ ├─ LeakyBucketStrategy      ✅ 연결됨                                   │
│ └─ SlidingWindowStrategy    ✅ 연결됨                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ ReplayHandler (shopping/handlers/replay_handlers.py)                    │
│ ├─ OrderReplayHandler       ✅ 연결됨                                   │
│ ├─ PaymentReplayHandler     ✅ 연결됨                                   │
│ └─ PointReplayHandler       ✅ 연결됨                                   │
├─────────────────────────────────────────────────────────────────────────┤
│ PaymentRecoveryHandler (payment_recovery_service.py)                    │
│ ├─ TossPaymentRecoveryHandler ✅ 연결됨                                 │
│ └─ GenericPaymentRecoveryHandler ✅ 연결됨                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

### 3.9 전략 1️⃣6️⃣: 싱글톤 및 전역 인스턴스 추적

**목적**: 애플리케이션 전역에서 사용되는 싱글톤 인스턴스 발견

**명령어**:
```bash
# 싱글톤 패턴 찾기
grep -r "__new__\|_instance\|@singleton\|get_instance" --include="*.py"

# 전역 인스턴스 찾기
grep -r "^[a-z_]* = .*Service()\|^[a-z_]* = .*Manager()" --include="*.py"
```

**발견된 싱글톤/전역 인스턴스**:
```
selfhealing/services/blast_radius/service.py:
├─ BlastRadiusService (싱글톤)    ✅ 전역 사용

selfhealing/api/django/pool_circuit_breaker.py:
├─ PoolCircuitBreaker (싱글톤)    ✅ 미들웨어에서 사용

selfhealing/core/hooks.py:
├─ BypassRegistry (클래스 레벨)   ✅ 전역 사용

selfhealing/config.py:
├─ get_config() (lru_cache)       ✅ 전역 설정 캐시
├─ get_safety_bounds() (lru_cache) ✅ 안전 범위 캐시
└─ get_tiering_config() (lru_cache) ✅ 티어링 설정 캐시
```

---

## 4. 연결 상태 검증 전략

### 4.1 연결 상태 분류

```
┌─────────────────────────────────────────────────────────────────────────┐
│                     연결 상태 분류 기준                                  │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ✅ 완전 연결 (Fully Connected)                                         │
│     - 설정 파일에 등록됨                                                │
│     - 프로덕션 코드에서 사용됨                                          │
│     - 테스트 존재                                                        │
│                                                                          │
│  🟡 부분 연결 (Partially Connected)                                     │
│     - 특정 환경에서만 활성화 (local.py, staging.py)                     │
│     - 조건부 활성화                                                      │
│                                                                          │
│  ⚪ 의도적 미연결 (Intentionally Unconnected)                           │
│     - 테스트 전용 (도메인 프리 라이브러리 기능)                         │
│     - 문서화되어 있음                                                    │
│     - 라이브러리 사용자가 선택적으로 활성화                             │
│                                                                          │
│  ❌ 비의도적 미연결 (Unintentionally Unconnected)                       │
│     - 정의됐지만 설정에서 빠짐                                          │
│     - 문서화 없음                                                        │
│     - 연결 필요                                                          │
│                                                                          │
│  💀 죽은 코드 (Dead Code)                                               │
│     - 어디서도 import 안 됨                                             │
│     - 더 이상 필요 없음                                                 │
│     - 삭제 후보                                                          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 검증 체크리스트

```
□ 미들웨어 검증
  □ 정의된 모든 Middleware 목록화
  □ settings/base.py의 MIDDLEWARE 배열과 비교
  □ 미연결 항목에 대해:
    □ 문서화 여부 확인
    □ 의도적 미연결인지 판단
    □ 필요시 연결 또는 삭제 결정

□ Celery 태스크 검증
  □ 정의된 모든 @shared_task 목록화
  □ CELERY_BEAT_SCHEDULE과 비교
  □ 수동 호출용 vs 스케줄용 분류
  □ 미스케줄 항목 검토

□ 서비스 검증
  □ 정의된 모든 Service 클래스 목록화
  □ URL 연결 여부 확인
  □ View/Task에서 사용 여부 확인
  □ 미사용 서비스 검토

□ Core 컴포넌트 검증
  □ core/ 폴더 전체 스캔
  □ Import 역추적으로 사용 여부 확인
  □ 테스트 전용 vs 프로덕션 사용 분류
```

---

## 5. 자동화 스크립트

### 5.1 기능 탐색 스크립트

```python
#!/usr/bin/env python3
"""
feature_discovery.py

시스템의 모든 기능을 탐색하고 연결 상태를 확인하는 스크립트
"""

import os
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Set
from enum import Enum


class ConnectionStatus(Enum):
    CONNECTED = "✅ 연결됨"
    PARTIAL = "🟡 부분 연결"
    INTENTIONAL = "⚪ 의도적 미연결"
    MISSING = "❌ 연결 필요"
    DEAD = "💀 죽은 코드"


@dataclass
class Feature:
    name: str
    file_path: str
    feature_type: str  # middleware, service, task, core
    status: ConnectionStatus
    import_count: int
    notes: str = ""


def find_middlewares(root: Path) -> List[Feature]:
    """모든 Middleware 클래스 발견"""
    features = []
    pattern = re.compile(r"class\s+(\w+Middleware)")

    for py_file in root.rglob("*.py"):
        if "__pycache__" in str(py_file) or "test" in str(py_file).lower():
            continue

        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(content):
            name = match.group(1)
            import_count = count_imports(root, name)
            status = determine_status(name, import_count, "middleware")

            features.append(Feature(
                name=name,
                file_path=str(py_file.relative_to(root)),
                feature_type="middleware",
                status=status,
                import_count=import_count
            ))

    return features


def find_celery_tasks(root: Path) -> List[Feature]:
    """모든 Celery 태스크 발견"""
    features = []
    pattern = re.compile(r"@shared_task.*\ndef\s+(\w+)")

    for py_file in root.rglob("*.py"):
        if "__pycache__" in str(py_file):
            continue

        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(content):
            name = match.group(1)

            features.append(Feature(
                name=name,
                file_path=str(py_file.relative_to(root)),
                feature_type="task",
                status=ConnectionStatus.CONNECTED,  # 추후 Beat 스케줄 확인 필요
                import_count=0
            ))

    return features


def find_services(root: Path) -> List[Feature]:
    """모든 Service 클래스 발견"""
    features = []
    pattern = re.compile(r"class\s+(\w+Service)")

    for py_file in root.rglob("*.py"):
        if "__pycache__" in str(py_file) or "test" in str(py_file).lower():
            continue

        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for match in pattern.finditer(content):
            name = match.group(1)
            import_count = count_imports(root, name)
            status = determine_status(name, import_count, "service")

            features.append(Feature(
                name=name,
                file_path=str(py_file.relative_to(root)),
                feature_type="service",
                status=status,
                import_count=import_count
            ))

    return features


def count_imports(root: Path, class_name: str) -> int:
    """클래스가 import된 횟수 카운트"""
    result = subprocess.run(
        ["grep", "-r", f"import.*{class_name}", "--include=*.py", str(root)],
        capture_output=True, text=True
    )
    lines = [l for l in result.stdout.split("\n") if l and "test" not in l.lower()]
    return len(lines)


def determine_status(name: str, import_count: int, feature_type: str) -> ConnectionStatus:
    """연결 상태 결정"""
    # 의도적 미연결 목록 (도메인 프리)
    intentional_unconnected = {
        "IdempotencyService",
        "SecurityViolationService",
        "SecurityNotificationService",
        "CertificateExpiryMonitor",
        "TLSErrorClassifier",
    }

    if name in intentional_unconnected:
        return ConnectionStatus.INTENTIONAL

    if import_count == 0:
        return ConnectionStatus.DEAD
    elif import_count <= 2:
        return ConnectionStatus.PARTIAL
    else:
        return ConnectionStatus.CONNECTED


def generate_report(features: List[Feature]) -> str:
    """보고서 생성"""
    report = ["# 기능 탐색 결과 보고서\n"]

    # 타입별 분류
    by_type: Dict[str, List[Feature]] = {}
    for f in features:
        by_type.setdefault(f.feature_type, []).append(f)

    for ftype, items in by_type.items():
        report.append(f"\n## {ftype.upper()}\n")
        report.append("| 이름 | 파일 | 상태 | Import 횟수 |")
        report.append("|------|------|------|-------------|")

        for item in sorted(items, key=lambda x: x.name):
            report.append(
                f"| {item.name} | {item.file_path} | "
                f"{item.status.value} | {item.import_count} |"
            )

    return "\n".join(report)


if __name__ == "__main__":
    root = Path(".")

    print("🔍 기능 탐색 시작...")

    features = []
    features.extend(find_middlewares(root))
    features.extend(find_celery_tasks(root))
    features.extend(find_services(root))

    report = generate_report(features)
    print(report)

    # 파일로 저장
    Path("feature_discovery_report.md").write_text(report)
    print("\n✅ 보고서 저장: feature_discovery_report.md")
```

### 5.2 사용법

```bash
# 스크립트 실행
python scripts/feature_discovery.py

# 또는 개별 grep 명령
./scripts/find_unconnected_features.sh
```

---

## 6. 탐색 결과 예시

### 6.1 현재 프로젝트 탐색 결과 요약

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    탐색 결과 요약 (2025-12-31)                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  📊 전체 통계                                                            │
│  ─────────────                                                           │
│  • 미들웨어: 12개 (연결: 6, 선택적: 4, 플랫폼별: 2)                      │
│  • Celery 태스크: 25개 (Beat: 15, 수동: 10)                              │
│  • 서비스: 45개 (URL: 30, 내부: 10, 테스트전용: 5)                       │
│  • Core 컴포넌트: 30개 (사용중: 25, 테스트전용: 5)                       │
│                                                                          │
│  🔴 즉시 조치 필요: 0개                                                  │
│  🟡 검토 필요: 5개 (의도적 미연결 확인 필요)                             │
│  🟢 정상: 95%                                                            │
│                                                                          │
│  📝 문서화 필요                                                          │
│  ────────────                                                            │
│  • TLS/인증서 모니터링 (신규 문서 필요)                                  │
│  • Graceful Shutdown (문서 보완)                                         │
│  • 보안 위반 처리 (신규 문서 필요)                                       │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.2 관련 문서

- [53_UNCONNECTED_FEATURES_ANALYSIS.md](53_UNCONNECTED_FEATURES_ANALYSIS.md) - 미연결 기능 상세 분석
- [54_LIBRARY_INTEGRATION_GUIDE.md](54_LIBRARY_INTEGRATION_GUIDE.md) - 통합 가이드
- [28_HIDDEN_FEATURES_DISCOVERY.md](28_HIDDEN_FEATURES_DISCOVERY.md) - 기존 숨겨진 기능 문서

---

*문서 끝*

# Self-Healing 숨겨진 기능 발견 가이드

📅 **작성일**: 2025-12-28  
🎯 **목적**: HTTP API로 노출되지 않는 Self-Healing 내부 기능 발견 및 테스트 가이드  
📋 **관련 문서**: [27_SELFHEALING_SCENARIO_MAPPING.md](27_SELFHEALING_SCENARIO_MAPPING.md)

---

## 1. 개요

Self-Healing 시스템은 HTTP API 외에도 다양한 **숨겨진 기능**들이 있습니다:

| 카테고리 | 개수 | HTTP API | 테스트 접근 방식 |
|---------|-----|----------|-----------------|
| **Middleware** | 8개 | ❌ | Django settings |
| **Celery Tasks** | 18개+ | ❌ | 직접 호출 또는 자동 |
| **Core Services** | 15개+ | 일부 | 서비스 import |
| **Decorators** | 8개+ | ❌ | 코드에 적용 |
| **Management Commands** | 3개 | ❌ | CLI 실행 |

---

## 2. 숨겨진 기능 발견 방법

### 2.1 검색 명령어 모음

```bash
# 1. Middleware 찾기
grep -r "class.*Middleware" packages/selfhealing-python/src/selfhealing/ --include="*.py"

# 2. Celery Tasks 찾기
grep -r "@shared_task\|@app.task\|@celery_app.task" packages/selfhealing-python/src/selfhealing/ --include="*.py"

# 3. Services 찾기
grep -r "class.*Service" packages/selfhealing-python/src/selfhealing/ --include="*.py"

# 4. Decorators 찾기
grep -r "def .*decorator\|@wraps" packages/selfhealing-python/src/selfhealing/ --include="*.py"

# 5. Management Commands 찾기
find packages/selfhealing-python/src/selfhealing/management/commands/ -name "*.py" | xargs grep "class Command"

# 6. Signal Handlers 찾기
grep -r "@receiver\|Signal\|post_save\|pre_save" packages/selfhealing-python/src/selfhealing/ --include="*.py"
```

### 2.2 VS Code 검색 패턴

```
# Middleware
regex: class\s+\w+Middleware

# Celery Tasks
regex: @(shared_task|app\.task|celery_app\.task)

# Services
regex: class\s+\w+Service

# Decorators
regex: def\s+\w+\(.*\):\s*\n.*@wraps
```

---

## 3. Middleware 목록 (8개)

Django/FastAPI 미들웨어 - **설정으로 활성화**, 클라이언트 API 불필요

| 미들웨어 | 파일 | 역할 | 활성화 방법 |
|---------|-----|------|------------|
| `HealthBridgeMiddleware` | `health_bridge.py` | DB 죽어도 /health/l3에서 CB 스냅샷 제공 | `MIDDLEWARE` 설정 |
| `AuditMiddleware` | `audit.py` | 민감한 엔드포인트 접근 로깅 | `MIDDLEWARE` 설정 |
| `RateLimitMiddleware` | `rate_limit.py` | Redis/Local 하이브리드 Rate Limiting | `MIDDLEWARE` 설정 |
| `PoolCircuitBreakerMiddleware` | `pool_cb.py` | DB 커넥션 풀 Circuit Breaker | `MIDDLEWARE` 설정 |
| `LoadSheddingMiddleware` | `load_shedding.py` | 비상 모드 시 API Tier별 트래픽 제어 | `MIDDLEWARE` 설정 |
| `FastAPIMiddleware` | `fastapi_middleware.py` | Request 추적, Graceful Shutdown | FastAPI app.add_middleware |
| `GracefulShutdownMiddleware` | `graceful_shutdown.py` | Graceful Shutdown 처리 | FastAPI app.add_middleware |
| `ActorMiddleware` | `actor_middleware.py` | 요청별 Actor 컨텍스트 관리 | `MIDDLEWARE` 설정 |

### 테스트 방법
```python
# 미들웨어는 자동으로 동작하므로 별도 클라이언트 불필요
# 미들웨어 효과를 테스트하려면:

# 1. Rate Limit 테스트
for i in range(100):
    response = client.get("/api/endpoint")
    if response.status_code == 429:
        print(f"Rate limited after {i} requests")
        break

# 2. Load Shedding 테스트 (비상 모드 중)
client.emergency.trigger("LEVEL_2", "load shedding test")
response = client.get("/api/low-priority-endpoint")
# LEVEL_2에서는 priority=low 엔드포인트가 차단됨
```

---

## 4. Celery Tasks 목록 (18개+)

### 4.1 자동 스케줄 태스크 (클라이언트 불필요)

| 태스크 | 주기 | 역할 |
|--------|-----|------|
| `check_cb_recovery` | 60초 | CB OPEN→HALF_OPEN 전환 체크 |
| `cleanup_expired_overrides` | 5분 | 수동 CB 오버라이드 TTL 만료 처리 |
| `cleanup_resolved_dlq_items` | 일간 | 해결된 DLQ 정리 |
| `collect_metrics` | 60초 | 메트릭 수집 |
| `check_sla_violations` | 5분 | SLA 위반 체크 |
| `heartbeat_task` | 60초 | Dead Man's Snitch 하트비트 |
| `apply_pending_changes` | 5초 | 대기 중인 설정 변경 적용 |
| `cleanup_expired_pending` | 일간 | 만료된 대기 설정 정리 |
| `check_emergency_expiry` | 15분 | 비상 모드 만료 체크 + 자동 복구 |
| `run_scheduled_experiments` | 5분 | 스케줄된 Chaos 실험 실행 |
| `generate_resilience_report` | 매일 6AM UTC | 일일 복원력 보고서 생성 |
| `cleanup_expired_approvals` | 1시간 | 만료된 승인 요청 정리 |
| `check_pending_approvals` | 자동 | 대기 승인 체크 및 알림 |

### 4.2 수동 호출 가능 태스크 (테스트에서 사용 가능)

| 태스크 | 역할 | 테스트 사용 여부 |
|--------|-----|-----------------|
| `replay_on_recovery` | CB 회복 시 DLQ 조건부 Replay | ⚠️ 직접 호출 가능 |
| `force_open_cb` | CB 강제 Open (비동기) | ⚠️ 직접 호출 가능 |
| `force_close_cb_and_replay` | CB 강제 Close + Replay 트리거 | ⚠️ 직접 호출 가능 |
| `replay_dlq_item` | 단일 DLQ 항목 Replay | ⚠️ 직접 호출 가능 |
| `batch_replay_dlq` | 도메인별 DLQ 배치 Replay | ⚠️ 직접 호출 가능 |
| `apply_graceful_change` | Graceful 설정 변경 | ⚠️ 직접 호출 가능 |

### 4.3 Celery Task 직접 호출 방법

```python
# 방법 1: delay() (비동기 실행)
from selfhealing.tasks.circuit_breaker import force_open_cb
result = force_open_cb.delay(service_name="payment")
result.get(timeout=10)  # 결과 대기

# 방법 2: apply() (동기 실행 - 테스트용)
result = force_open_cb.apply(args=["payment"])
print(result.result)

# 방법 3: HTTP API 통해 트리거 (권장)
# 대부분의 태스크는 HTTP API에서 내부적으로 호출됨
client.circuit_breaker.force_open("payment")  # 내부에서 task 호출
```

---

## 5. Core Services 목록 (15개+)

### 5.1 HTTP API로 노출된 서비스 (✅ 클라이언트 있음)

| 서비스 | 역할 | 클라이언트 |
|--------|-----|-----------|
| `CircuitBreakerStateManager` | CB 상태 관리 | `circuit_breaker.py` |
| `DLQService` | DLQ CRUD 및 통계 | `dlq.py` |
| `HealthService` | 외부 서비스 헬스체크 | `health.py` |
| `DashboardService` | 대시보드 데이터 집계 | `dashboard.py` |
| `ControlService` | 제어 API 로직 | `circuit_breaker.py` |
| `ErrorBudgetService` | 에러 예산 계산 | `error_budget.py` |
| `PendingChangeManager` | 대기 설정 변경 관리 | `runtime_config.py` |
| `ConfigHistoryService` | 설정 변경 이력 관리 | `runtime_config.py` |

### 5.2 내부 전용 서비스 (⚠️ 테스트에서 직접 import 필요)

| 서비스 | 역할 | 직접 호출 방법 |
|--------|-----|---------------|
| `EmergencyModeService` | 비상 모드 만료 체크, 자동 복구 | `from selfhealing.services import EmergencyModeService` |
| `ChaosExperimentService` | Chaos 실험 실행, SafetyGuard | `from selfhealing.services import ChaosExperimentService` |
| `ConfigApplier` | 설정 변경 적용, Graceful Change | `from selfhealing.core import ConfigApplier` |
| `IdempotencyService` | 멱등성 키 관리 | 자동 적용 (클라이언트 불필요) |
| `ForensicService` | 장애 포렌식 분석 | `from selfhealing.services import ForensicService` |
| `SecurityAlertService` | 보안 위반 알림 발송 | 자동 트리거 (클라이언트 불필요) |
| `SecurityViolationTracker` | 보안 위반 기록 | 자동 처리 (클라이언트 불필요) |

### 5.3 내부 서비스 테스트 방법

```python
# 직접 import하여 사용 (Stage 39/40/41 패턴)
from selfhealing.services import EmergencyModeService

service = EmergencyModeService()

# 메서드 직접 호출
result = service.check_emergency_expiry()
result = service.auto_recover()
```

---

## 6. Decorators 목록 (8개+)

**코드에 직접 적용** - 클라이언트 API 불필요

| 데코레이터 | 파일 | 역할 |
|-----------|-----|------|
| `@track_dlq_creation` | `metrics/decorators.py` | DLQ 생성 메트릭 자동 추적 |
| `@track_dlq_resolution` | `metrics/decorators.py` | DLQ 해결 메트릭 자동 추적 |
| `@track_replay` | `metrics/decorators.py` | Replay 메트릭 자동 추적 |
| `@measure_time` | `metrics/decorators.py` | 함수 실행 시간 Histogram |
| `@count_calls` | `metrics/decorators.py` | 함수 호출 Counter |
| `@governance_check()` | `governance/decorators.py` | 거버넌스 체크 자동 수행 |
| `@with_retry()` | `core/retry.py` | 재시도 로직 자동 적용 |
| `@with_jitter()` | `core/jitter.py` | Jitter 자동 적용 |

### 사용 예시

```python
from selfhealing.core.retry import with_retry
from selfhealing.core.jitter import with_jitter

@with_retry(max_attempts=3, backoff=2.0)
@with_jitter(min_ms=100, max_ms=500)
def call_external_api():
    # 자동으로 재시도 + Jitter 적용
    return requests.get("https://external-api.com")
```

---

## 7. Management Commands 목록 (3개)

**CLI에서 직접 실행** - 클라이언트 API 불필요

| 커맨드 | 역할 | 사용처 |
|--------|-----|-------|
| `check_selfhealing_config` | CI/CD Pre-flight 설정 검증 | CI/CD Pipeline |
| `generate_self_healing_alerts` | Self-healing 알림 생성 | 운영 스크립트 |
| `cleanup_expired_tokens` | 만료 토큰 정리 | 정기 Cron 작업 |

### 실행 방법

```bash
# 설정 검증 (CI/CD에서 사용)
python manage.py check_selfhealing_config --strict

# 알림 생성
python manage.py generate_self_healing_alerts --type=critical

# 토큰 정리
python manage.py cleanup_expired_tokens --days=30
```

---

## 8. Core Infrastructure (코어 인프라)

**내부 자동 동작** - 클라이언트 API 불필요

| 컴포넌트 | 역할 |
|---------|-----|
| `GracefulShutdownCoordinator` | SIGTERM/SIGINT 시그널 핸들링 |
| `InFlightRequestTracker` | 진행 중 요청 추적 |
| `DegradedModeHandler` | 사령탑 연결 실패 시 Fallback 기본값 |
| `StateCache` | CB 상태 캐시 (TTL 기반) |
| `validate_config_on_startup` | 시작 시 설정 검증 |
| `preflight_check` | CI/CD용 설정 Pre-flight 검증 |

---

## 9. 테스트 시나리오별 접근 방법

### 9.1 HTTP API만 사용 (권장 - 이식성 높음)

```python
from load_tests.utils.selfhealing import SelfHealingClient

client = SelfHealingClient()
client.health.ping()
client.circuit_breaker.get_status("payment")
client.chaos.inject_failure("payment", "timeout")
```

### 9.2 HTTP API + 내부 서비스 (특수 테스트용)

```python
from load_tests.utils.selfhealing import SelfHealingClient

# HTTP API
client = SelfHealingClient()

# 내부 서비스 직접 import (Stage 39/40/41 패턴)
from selfhealing.services import EmergencyModeService, ChaosExperimentService

emergency_service = EmergencyModeService()
chaos_service = ChaosExperimentService()

# 내부 메서드 직접 호출
emergency_service.check_emergency_expiry()
chaos_service.validate_experiment(experiment_config)
```

### 9.3 Celery Task 직접 호출 (비동기 테스트용)

```python
from selfhealing.tasks.circuit_breaker import force_open_cb, force_close_cb_and_replay
from selfhealing.tasks.dlq import batch_replay_dlq

# 비동기 실행
result = force_open_cb.delay("payment")
result.get(timeout=30)

# 동기 실행 (테스트용)
result = batch_replay_dlq.apply(args=["payment"])
```

---

## 10. 클라이언트 추가 필요 여부 판단

### ❌ 클라이언트 추가 불필요

| 카테고리 | 이유 |
|---------|-----|
| **Middleware** | Django settings로 활성화, 자동 동작 |
| **자동 스케줄 Celery Tasks** | Celery Beat가 자동 실행 |
| **Decorators** | 코드에서 직접 적용 |
| **Core Infrastructure** | 내부 자동 동작 |
| **Management Commands** | CLI 실행 (테스트 아님) |

### ⚠️ 클라이언트 추가 고려

| 기능 | 사용 시나리오 | 현재 접근 방법 |
|-----|-------------|---------------|
| Celery Task 직접 호출 | 비동기 동작 검증 | `from selfhealing.tasks import ...` |
| 내부 Service 호출 | 유닛 테스트, 특수 시나리오 | `from selfhealing.services import ...` |

### ✅ 결론: 현재 상태로 충분

**모든 주요 기능은 HTTP API를 통해 접근 가능**합니다.

내부 서비스/태스크 직접 호출이 필요한 경우:
1. Stage 39/40/41처럼 직접 import 패턴 사용
2. 또는 별도 래퍼 유틸리티 작성 (필요시)

---

## 11. 숨겨진 기능 발견 체크리스트

새로운 Self-Healing 기능 추가 시 확인:

- [ ] `urls.py`에 HTTP API 추가했는가?
- [ ] `load_tests/utils/selfhealing/`에 클라이언트 메서드 추가했는가?
- [ ] Celery Task라면 스케줄 등록했는가?
- [ ] Middleware라면 문서화했는가?
- [ ] Service라면 API 노출 여부 결정했는가?

---

📝 **Hidden Features Discovery Guide v1.0.0**
📅 **Created**: 2025-12-28
📁 **Location**: `docs/self_healing/28_HIDDEN_FEATURES_DISCOVERY.md`

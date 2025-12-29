# Self-Healing 패키지 누락 테스트 리포트

> **생성일**: 2025-12-29  
> **분석 대상**: `packages/selfhealing-python/src/selfhealing/`

## 📊 요약 통계

| 구분 | 개수 |
|------|------|
| **총 소스 파일** | 232개 |
| **테스트 있음** | 105개 |
| **테스트 없음** | 127개 |
| **커버리지** | 45.3% |

---

## 🔴 우선순위 높음 - Core 모듈 (23개)

핵심 비즈니스 로직으로 반드시 테스트가 필요한 파일들입니다.

| 파일 | 설명 | 중요도 |
|------|------|--------|
| `core/action_executor.py` | 액션 실행기 | 🔴 높음 |
| `core/adaptive_jitter.py` | 적응형 지터 | 🟡 중간 |
| `core/apply_strategy.py` | 전략 적용 | 🔴 높음 |
| `core/auto_rollback_guard.py` | 자동 롤백 가드 | 🔴 높음 |
| `core/cert_monitor.py` | 인증서 모니터링 | 🟡 중간 |
| `core/config.py` | 코어 설정 | 🟡 중간 |
| `core/connection_health.py` | 연결 상태 확인 | 🔴 높음 |
| `core/constants.py` | 상수 정의 | 🟢 낮음 |
| `core/decision_engine.py` | 의사결정 엔진 | 🔴 높음 |
| `core/degraded_mode_handler.py` | 저하 모드 핸들러 | 🔴 높음 |
| `core/fallback_strategy.py` | 폴백 전략 | 🔴 높음 |
| `core/forensic.py` | 포렌식 분석 | 🔴 높음 |
| `core/pool_monitor.py` | 풀 모니터링 | 🔴 높음 |
| `core/pool_watchdog.py` | 풀 워치독 | 🔴 높음 |
| `core/request_context.py` | 요청 컨텍스트 | 🟡 중간 |
| `core/runtime_feedback.py` | 런타임 피드백 | 🔴 높음 |
| `core/safety_bounds.py` | 안전 한계 | 🔴 높음 |
| `core/shutdown_coordinator.py` | 종료 조정자 | 🔴 높음 |
| `core/state_backend.py` | 상태 백엔드 | 🟡 중간 |
| `core/state_cache.py` | 상태 캐시 | 🟡 중간 |
| `core/timezone.py` | 타임존 처리 | 🟢 낮음 |
| `core/time_provider.py` | 시간 제공자 | 🟡 중간 |
| `core/tls_handler.py` | TLS 핸들러 | 🔴 높음 |

---

## 🔴 우선순위 높음 - Services 모듈

### services/ (루트)

| 파일 | 설명 |
|------|------|
| `services/backoff_calculator.py` | 백오프 계산기 |
| `services/chaos_context.py` | 카오스 컨텍스트 |
| `services/circuit_breaker_service.py` | 서킷 브레이커 서비스 |
| `services/config_history.py` | 설정 이력 |
| `services/control_api_service.py` | 제어 API 서비스 |
| `services/dashboard_service.py` | 대시보드 서비스 |
| `services/dlq_models.py` | DLQ 모델 |
| `services/dlq_service.py` | DLQ 서비스 |
| `services/event_bus.py` | 이벤트 버스 |
| `services/execution_services.py` | 실행 서비스 |
| `services/factory.py` | 팩토리 |
| `services/forensic_advisor.py` | 포렌식 어드바이저 |
| `services/forensic_context.py` | 포렌식 컨텍스트 |
| `services/governance.py` | 거버넌스 |
| `services/governance_checks.py` | 거버넌스 체크 |
| `services/governance_service.py` | 거버넌스 서비스 |
| `services/health_check.py` | 헬스 체크 |
| `services/idempotency_service.py` | 멱등성 서비스 |
| `services/pending_config.py` | 대기 설정 |
| `services/precomputed_cache.py` | 사전 계산 캐시 |
| `services/rate_limit_coordinator.py` | 레이트 리밋 조정자 |
| `services/replay_service.py` | 리플레이 서비스 |
| `services/retry_handler.py` | 재시도 핸들러 |
| `services/security_notification_service.py` | 보안 알림 서비스 |
| `services/security_violation_service.py` | 보안 위반 서비스 |
| `services/system_control.py` | 시스템 제어 |

### services/auto_tuning/

| 파일 | 설명 |
|------|------|
| `adjustment_recorder.py` | 조정 기록기 |
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/blast_radius/

| 파일 | 설명 |
|------|------|
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/chaos/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `blast_radius.py` | 블라스트 반경 |
| `experiments.py` | 실험 |
| `experiment_impl.py` | 실험 구현 |
| `reports.py` | 보고서 |
| `safety_guard.py` | 안전 가드 |
| `scheduler.py` | 스케줄러 |
| `scheduler_models.py` | 스케줄러 모델 |

### services/circuit_breaker/

| 파일 | 설명 |
|------|------|
| `config.py` | 설정 |
| `convenience.py` | 편의 함수 |
| `manual_control.py` | 수동 제어 |
| `protection.py` | 보호 |
| `rate_limit_tracker.py` | 레이트 리밋 트래커 |
| `service.py` | 서비스 |

### services/compliance/

| 파일 | 설명 |
|------|------|
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/corruption_shield/

| 파일 | 설명 |
|------|------|
| `config.py` | 설정 |
| `shield.py` | 실드 |
| `validators.py` | 검증기 |

### services/emergency_mode/

| 파일 | 설명 |
|------|------|
| `enums.py` | 열거형 |
| `manager.py` | 매니저 |
| `models.py` | 모델 |
| `recovery_gate.py` | 복구 게이트 |

### services/error_budget/

| 파일 | 설명 |
|------|------|
| `advisor.py` | 어드바이저 |
| `calculator.py` | 계산기 |
| `enums.py` | 열거형 |
| `models.py` | 모델 |
| `recorder.py` | 기록기 |
| `service.py` | 서비스 |

### services/error_budget/reconciliation/

| 파일 | 설명 |
|------|------|
| `enums.py` | 열거형 |
| `models.py` | 모델 |
| `period_tracker.py` | 기간 추적기 |
| `service.py` | 서비스 |
| `shadow_calculator.py` | 섀도우 계산기 |

### services/error_budget_gate/

| 파일 | 설명 |
|------|------|
| `alert_manager.py` | 알림 매니저 |
| `config.py` | 설정 |
| `exceptions.py` | 예외 |
| `fault_detector.py` | 장애 감지기 |
| `gate.py` | 게이트 |
| `rate_limiter.py` | 레이트 리미터 |

### services/factory/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `registry.py` | 레지스트리 |
| `repository.py` | 레포지토리 |
| `service.py` | 서비스 |
| `singleton.py` | 싱글톤 |

### services/finops/

| 파일 | 설명 |
|------|------|
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/learning/

| 파일 | 설명 |
|------|------|
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/metrics/

| 파일 | 설명 |
|------|------|
| `alerting_rules.py` | 알림 규칙 |
| `definitions.py` | 정의 |
| `recorders.py` | 기록기 |
| `registry.py` | 레지스트리 |
| `updaters.py` | 업데이터 |

### services/rollback/

| 파일 | 설명 |
|------|------|
| `models.py` | 모델 |
| `service.py` | 서비스 |

### services/runtime_config/

| 파일 | 설명 |
|------|------|
| `advanced_configs.py` | 고급 설정 |
| `approval.py` | 승인 |
| `base.py` | 베이스 |
| `chaos_storage.py` | 카오스 스토리지 |
| `constants.py` | 상수 |
| `core_configs.py` | 코어 설정 |
| `strategy.py` | 전략 |

### services/throttle/

| 파일 | 설명 |
|------|------|
| `adaptive.py` | 적응형 |
| `base.py` | 베이스 |
| `config.py` | 설정 |

---

## 🟡 우선순위 중간 - Adapters 모듈

### adapters/ (루트)

| 파일 | 설명 |
|------|------|
| `django_repositories.py` | Django 레포지토리 |
| `health_checker.py` | 헬스 체커 |

### adapters/airgap/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `factory.py` | 팩토리 |
| `null_adapter.py` | Null 어댑터 |
| `redis_adapter.py` | Redis 어댑터 |

### adapters/alert/

| 파일 | 설명 |
|------|------|
| `file_adapter.py` | 파일 어댑터 |
| `null_adapter.py` | Null 어댑터 |
| `stdout_adapter.py` | Stdout 어댑터 |

### adapters/audit/

| 파일 | 설명 |
|------|------|
| `file_adapter.py` | 파일 어댑터 |
| `null_adapter.py` | Null 어댑터 |
| `stdout_adapter.py` | Stdout 어댑터 |

### adapters/cache/

| 파일 | 설명 |
|------|------|
| `memcached_adapter.py` | Memcached 어댑터 |
| `memory_adapter.py` | 메모리 어댑터 |
| `redis_adapter.py` | Redis 어댑터 |

### adapters/celery/

| 파일 | 설명 |
|------|------|
| `signal_hooks.py` | 시그널 훅 |
| `tasks.py` | 태스크 |

### adapters/django/

| 파일 | 설명 |
|------|------|
| `admin.py` | Admin |
| `apps.py` | Apps |
| `circuit_breaker_repository.py` | 서킷 브레이커 레포지토리 |
| `config_provider.py` | 설정 프로바이더 |
| `failed_operation_repository.py` | 실패 오퍼레이션 레포지토리 |
| `models.py` | 모델 |
| `repositories.py` | 레포지토리 |
| `security_incident_repository.py` | 보안 인시던트 레포지토리 |

### adapters/fastapi/

| 파일 | 설명 |
|------|------|
| `dependencies.py` | 의존성 |
| `middleware.py` | 미들웨어 |
| `routes.py` | 라우트 |

### adapters/frameworks/

| 파일 | 설명 |
|------|------|
| `fastapi_adapter.py` | FastAPI 어댑터 |

### adapters/memory/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `circuit_breaker.py` | 서킷 브레이커 |
| `drift_reconciliation.py` | 드리프트 재조정 |
| `failed_operation.py` | 실패 오퍼레이션 |
| `layered_repository.py` | 레이어드 레포지토리 |
| `security_incident.py` | 보안 인시던트 |
| `shadow_logger.py` | 섀도우 로거 |

### adapters/metrics/

| 파일 | 설명 |
|------|------|
| `auto_tuning_adapter.py` | Auto Tuning 어댑터 |
| `base.py` | 베이스 |
| `django_adapter.py` | Django 어댑터 |
| `factory.py` | 팩토리 |
| `redis_adapter.py` | Redis 어댑터 |

### adapters/observability/opentelemetry/

| 파일 | 설명 |
|------|------|
| `adapter.py` | 어댑터 |
| `config.py` | 설정 |
| `events.py` | 이벤트 |
| `noop.py` | Noop |
| `spans.py` | Spans |

### adapters/queues/

| 파일 | 설명 |
|------|------|
| `celery_adapter.py` | Celery 어댑터 |
| `rq_adapter.py` | RQ 어댑터 |
| `sync_adapter.py` | Sync 어댑터 |

### adapters/rate_limit/

| 파일 | 설명 |
|------|------|
| `database_adapter.py` | 데이터베이스 어댑터 |
| `factory.py` | 팩토리 |
| `memory_adapter.py` | 메모리 어댑터 |
| `redis_adapter.py` | Redis 어댑터 |

### adapters/sqlalchemy/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `circuit_breaker.py` | 서킷 브레이커 |
| `failed_operation.py` | 실패 오퍼레이션 |
| `models.py` | 모델 |
| `security_incident.py` | 보안 인시던트 |

---

## 🟡 우선순위 중간 - API 모듈

### api/django/

| 파일 | 설명 |
|------|------|
| `config_descriptions.py` | 설정 설명 |
| `middleware.py` | 미들웨어 |
| `permissions.py` | 권한 |
| `pool_circuit_breaker.py` | 풀 서킷 브레이커 |
| `rate_limit.py` | 레이트 리밋 |
| `reauthentication.py` | 재인증 |
| `serializers_legacy.py` | 레거시 시리얼라이저 |
| `stress_views.py` | 스트레스 뷰 |
| `throttle_adapter.py` | 쓰로틀 어댑터 |
| `urls.py` | URL |
| `views.py` | 뷰 |

### api/django/serializers/

| 파일 | 설명 |
|------|------|
| `chaos.py` | 카오스 |
| `config.py` | 설정 |
| `metric_sync.py` | 메트릭 동기화 |

### api/django/tiering/

| 파일 | 설명 |
|------|------|
| `circuit_breaker.py` | 서킷 브레이커 |
| `defaults.py` | 기본값 |
| `enums.py` | 열거형 |
| `middleware.py` | 미들웨어 |
| `models.py` | 모델 |
| `registry.py` | 레지스트리 |
| `validator.py` | 검증기 |

### api/django/views/

| 파일 | 설명 |
|------|------|
| `auto_tuning.py` | Auto Tuning |
| `blast_radius.py` | Blast Radius |
| `chaos.py` | Chaos |
| `circuit_breaker.py` | Circuit Breaker |
| `compliance_dna.py` | Compliance DNA |
| `config.py` | Config |
| `config_history.py` | Config History |
| `dashboard.py` | Dashboard |
| `dlq.py` | DLQ |
| `drift_threshold.py` | Drift Threshold |
| `emergency.py` | Emergency |
| `error_budget.py` | Error Budget |
| `finops.py` | FinOps |
| `health.py` | Health |
| `l2_storage.py` | L2 Storage |
| `l2_storage_config.py` | L2 Storage Config |
| `l2_storage_drift.py` | L2 Storage Drift |
| `l2_storage_shadow_log.py` | L2 Storage Shadow Log |
| `l2_storage_status.py` | L2 Storage Status |
| `l2_storage_utils.py` | L2 Storage Utils |
| `learning.py` | Learning |
| `metric_sync.py` | Metric Sync |
| `rollback.py` | Rollback |
| `system_control.py` | System Control |
| `tiering.py` | Tiering |
| `xtest_mode.py` | XTest Mode |

### api/django/views/error_budget/

| 파일 | 설명 |
|------|------|
| `deployment.py` | Deployment |
| `reconciliation.py` | Reconciliation |
| `status.py` | Status |

### api/django/views/governance/

| 파일 | 설명 |
|------|------|
| `approval_views.py` | Approval Views |
| `config_views.py` | Config Views |
| `control_views.py` | Control Views |
| `deprecated_views.py` | Deprecated Views |
| `service.py` | Service |
| `status_views.py` | Status Views |

### api/django/views/xtest/

| 파일 | 설명 |
|------|------|
| `base.py` | Base |
| `circuit_breaker.py` | Circuit Breaker |
| `error_budget.py` | Error Budget |
| `observability.py` | Observability |
| `snapshot.py` | Snapshot |

---

## 🟡 우선순위 중간 - Audit 모듈

| 파일 | 설명 |
|------|------|
| `audit/api.py` | API |
| `audit/config.py` | 설정 |
| `audit/continuous_audit_api.py` | Continuous Audit API |

### audit/backends/

| 파일 | 설명 |
|------|------|
| `base.py` | 베이스 |
| `cloudwatch.py` | CloudWatch |
| `datadog.py` | Datadog |
| `local.py` | Local |
| `remote.py` | Remote |
| `s3_worm.py` | S3 WORM |

---

## 🟢 우선순위 낮음 - 기타 모듈

### 루트 (selfhealing/)

| 파일 | 설명 |
|------|------|
| `config.py` | 설정 |
| `config_tracker.py` | 설정 트래커 |
| `factory.py` | 팩토리 |
| `slo.py` | SLO |

### context/

| 파일 | 설명 |
|------|------|
| `actor_context.py` | Actor 컨텍스트 |

### interfaces/

| 파일 | 설명 |
|------|------|
| `alert_adapter.py` | Alert 어댑터 인터페이스 |
| `audit_adapter.py` | Audit 어댑터 인터페이스 |
| `config_provider.py` | Config 프로바이더 인터페이스 |
| `notification.py` | 알림 인터페이스 |
| `rate_limit_storage.py` | Rate Limit 스토리지 인터페이스 |
| `repositories.py` | 레포지토리 인터페이스 |
| `web_framework.py` | 웹 프레임워크 인터페이스 |

### metrics/

| 파일 | 설명 |
|------|------|
| `event_handlers.py` | 이벤트 핸들러 |
| `prometheus.py` | Prometheus |
| `reconciler.py` | Reconciler |
| `reliability.py` | Reliability |
| `reliability_manager.py` | Reliability Manager |
| `safe_gauge.py` | Safe Gauge |
| `snapshot_storage.py` | Snapshot Storage |

### models/

| 파일 | 설명 |
|------|------|
| (모두 테스트 있음 또는 제외) | |

### utils/

| 파일 | 설명 |
|------|------|
| `async_logger.py` | Async Logger |
| `time.py` | Time 유틸 |

### tasks/

| 파일 | 설명 |
|------|------|
| `chaos_scheduler.py` | Chaos 스케줄러 |
| `config_apply.py` | Config 적용 |
| `drift_detection.py` | Drift 감지 |
| `governance.py` | Governance |

---

## 📋 권장 작업 순서

### Phase 1: 핵심 비즈니스 로직 (1-2주)
1. `core/decision_engine.py` - 의사결정 엔진 ⭐
2. `core/safety_bounds.py` - 안전 한계 ⭐
3. `core/auto_rollback_guard.py` - 자동 롤백 ⭐
4. `core/runtime_feedback.py` - 런타임 피드백 ⭐
5. `core/fallback_strategy.py` - 폴백 전략
6. `core/shutdown_coordinator.py` - 종료 조정자

### Phase 2: 서비스 레이어 (2-3주)
1. `services/circuit_breaker/` 전체
2. `services/error_budget/` 전체
3. `services/emergency_mode/` 전체
4. `services/chaos/` 전체

### Phase 3: 어댑터 및 API (2-3주)
1. `adapters/django/` 주요 레포지토리
2. `api/django/views/` 주요 뷰
3. `adapters/memory/` 메모리 레포지토리

### Phase 4: 보조 모듈 (1주)
1. `metrics/` 모듈
2. `utils/` 모듈
3. `tasks/` 모듈

---

## 📝 노트

- `__init__.py`, `conftest.py` 등 설정 파일은 분석에서 제외됨
- 인터페이스 파일(`interfaces/`)은 구현체 테스트로 커버 가능하므로 우선순위 낮음
- 일부 파일은 다른 테스트에서 간접적으로 테스트될 수 있음
- 테스트 작성 시 기존 `packages/selfhealing-python/tests/` 또는 `tests/self_healing/` 구조 활용 권장

---

## 🔗 관련 문서

- [Self-Healing Architecture](./SYSTEM_ARCHITECTURE.md)
- [Continuous Audit Implementation](./37_CONTINUOUS_AUDIT_FINAL_IMPL.md)
- [Auto Tuning API](./38_AUTO_TUNING_API.md)

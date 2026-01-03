# 모듈 통합 작업 계획서

> **Created**: 2026-01-03
> **Updated**: 2026-01-03 (AST 분석기 재실행 결과 반영)
> **Status**: 진행 전
> **참조**: [CODE_DEPENDENCY_ANALYSIS.md](CODE_DEPENDENCY_ANALYSIS.md)

---

## 📋 개요

이 문서는 102개 고아 모듈 중 **검토가 필요한 94개 모듈**의 통합 작업 계획입니다.
(8개는 엔트리포인트/미들웨어로 정상 고아)

### 작업 원칙

1. **통합 우선**: 기존 패키지에 통합 가능하면 통합
2. **re-export 활용**: 하위 모듈은 부모 `__init__.py`에서 노출
3. **코드 기반 검증**: 추측 없이 실제 코드 확인 후 작업
4. **세션 분리**: 토큰 한계로 인해 카테고리별 세션 분리

---

## 📊 작업 대상 요약

| 카테고리 | 모듈 수 | 작업 유형 | 예상 세션 |
|---------|--------|----------|----------|
| P1: services 패키지 | 45개 | `__init__.py` re-export | 3~4 세션 |
| P2: api 패키지 | 18개 | `__init__.py` re-export | 1~2 세션 |
| P3: adapters 패키지 | 19개 | `__init__.py` 또는 factory 등록 | 1~2 세션 |
| P4: 기타 (루트, interfaces, audit) | 7개 | 개별 처리 | 1 세션 |
| **합계** | **94개** | | **6~9 세션** |

---

## 🔴 P1: services 패키지 (45개)

가장 많은 고아 모듈이 있는 영역. 하위 패키지별로 분리하여 작업.

### 하위 패키지 목록

| 하위 패키지 | 고아 모듈 수 | 작업 내용 |
|------------|------------|----------|
| `services.circuit_breaker` | 6개 | `__init__.py` re-export |
| `services.emergency_mode` | 4개 | `__init__.py` re-export |
| `services.error_budget.reconciliation` | 5개 | `__init__.py` re-export |
| `services.factory` | 5개 | `__init__.py` re-export |
| `services.runtime_config` | 7개 | `__init__.py` re-export |
| `services.metrics` | 5개 | `__init__.py` re-export |
| `services.chaos` | 2개 | `__init__.py` re-export |
| `services.finops` | 2개 | `__init__.py` re-export |
| `services.blast_radius` | 1개 | `__init__.py` re-export |
| `services.learning` | 1개 | `__init__.py` re-export |
| `services.rollback` | 1개 | `__init__.py` re-export |
| 기타 루트 레벨 | 6개 | `services/__init__.py` 추가 |

### 세션 1-A: circuit_breaker, emergency_mode, error_budget

```
[세션 1-A] services 하위 패키지 연결 (Part 1)

대상:
1. services/circuit_breaker/ (6개 모듈)
   - config, convenience, manual_control, protection, rate_limit_tracker, service
2. services/emergency_mode/ (4개 모듈)
   - enums, manager, models, recovery_gate
3. services/error_budget/reconciliation/ (5개 모듈)
   - enums, models, period_tracker, service, shadow_calculator

작업:
1. 각 패키지의 __init__.py 확인
2. 하위 모듈 re-export 추가
3. 상위 패키지에서도 import 확인

추측 금지, 코드 확인 후 작업
```

### 세션 1-B: factory, runtime_config, metrics

```
[세션 1-B] services 하위 패키지 연결 (Part 2)

대상:
1. services/factory/ (5개 모듈)
   - base, registry, repository, service, singleton
2. services/runtime_config/ (7개 모듈)
   - advanced_configs, approval, base, chaos_storage, constants, core_configs, strategy
3. services/metrics/ (5개 모듈)
   - alerting_rules, definitions, recorders, registry, updaters

작업: 동일
```

### 세션 1-C: chaos, finops, blast_radius, learning, rollback + 루트 레벨

```
[세션 1-C] services 하위 패키지 연결 (Part 3)

대상:
1. services/chaos/ (2개): experiments, scheduler_models
2. services/finops/ (2개): models, service
3. services/blast_radius/ (1개): service
4. services/learning/ (1개): service
5. services/rollback/ (1개): service
6. services/ 루트 레벨 (6개):
   - backoff_calculator, chaos_context, corruption_shield,
   - forensic_context, idempotency_service, rate_limit_coordinator,
   - retry_handler, throttle, unified_notification

작업:
1. 각 하위 패키지 __init__.py re-export
2. services/__init__.py에 루트 레벨 모듈 추가

추측 금지, 코드 확인 후 작업
```

---

## 🟠 P2: api 패키지 (18개)

### 하위 패키지 목록

| 하위 패키지 | 고아 모듈 수 | 작업 내용 |
|------------|------------|----------|
| `api.django.tiering` | 6개 | `__init__.py` re-export |
| `api.django.views.error_budget` | 3개 | `__init__.py` re-export |
| `api.django.views.xtest` | 5개 | 테스트 전용 - 선택적 처리 |
| 기타 | 4개 | 개별 처리 |

### 세션 2: api 패키지 연결

```
[세션 2] api 패키지 연결

대상:
1. api/django/tiering/ (6개 모듈)
   - circuit_breaker, defaults, enums, models, registry, validator
2. api/django/views/error_budget/ (3개)
   - deployment, reconciliation, status
3. api/django/views/xtest/ (5개) - 테스트 전용
   - base, circuit_breaker, error_budget, observability, snapshot
4. 기타 (4개)
   - api/__init__.py, api/django/__init__.py
   - api/django/reauthentication, api/django/throttle_adapter

작업:
1. tiering, error_budget 하위 패키지 __init__.py re-export
2. xtest는 DEBUG 전용이므로 선택적 export 또는 그대로 유지
3. reauthentication, throttle_adapter는 View에서 직접 사용 - 문서화

추측 금지, 코드 확인 후 작업
```

---

## 🟡 P3: adapters 패키지 (19개)

### 하위 패키지 목록

| 하위 패키지 | 고아 모듈 수 | 작업 내용 |
|------------|------------|----------|
| `adapters.alert` | 3개 | `__init__.py` re-export |
| `adapters.audit` | 3개 | `__init__.py` re-export |
| `adapters.observability.opentelemetry` | 4개 | OTel 전용 - 선택적 처리 |
| `adapters.fastapi` | 2개 | `__init__.py` 확인 |
| `adapters.metrics` | 2개 | `__init__.py` 확인 |
| 기타 루트 레벨 | 5개 | 개별 처리 |

### 세션 3: adapters 패키지 연결

```
[세션 3] adapters 패키지 연결

대상:
1. adapters/alert/ (3개)
   - file_adapter, null_adapter, stdout_adapter
2. adapters/audit/ (3개)
   - null_adapter, stdout_adapter, worm_adapters
3. adapters/observability/opentelemetry/ (4개) - OTel 전용
   - config, events, noop, spans
4. adapters/fastapi/ (2개)
   - __init__, dependencies
5. adapters/metrics/ (2개)
   - __init__, auto_tuning_adapter
6. 기타 루트 레벨 (5개)
   - airgap, celery, frameworks, resilient, statistics

작업:
1. alert, audit 하위 패키지 __init__.py re-export
2. opentelemetry는 환경변수 기반 활성화 - 선택적
3. 기타 모듈 용도 확인 후 처리

추측 금지, 코드 확인 후 작업
```

---

## 🟢 P4: 기타 모듈 (7개)

### 대상 모듈

| 모듈 | 현재 상태 | 작업 내용 |
|------|----------|----------|
| `__init__` | 루트 패키지 | 정상 - 무시 |
| `config_tracker` | 루트 레벨 | 용도 확인 후 처리 |
| `context` | 패키지 | 용도 확인 후 처리 |
| `utils` | 패키지 | 내부 유틸리티 - 선택적 |
| `interfaces.notification` | 인터페이스 미노출 | `interfaces/__init__.py`에 추가 |
| `audit.api` | API 미연결 | urls.py 연결 확인 |
| `audit.continuous_audit_api` | API 미연결 | urls.py 연결 확인 |

### 세션 4: 기타 모듈 정리

```
[세션 4] 기타 모듈 정리

대상:
1. config_tracker.py - 용도 확인
2. context/ - 용도 확인
3. utils/ - 내부 유틸리티 확인
4. interfaces/notification.py - interfaces/__init__.py에 추가
5. audit/api.py - urls.py 연결 확인
6. audit/continuous_audit_api.py - urls.py 연결 확인

작업:
1. 각 모듈 용도 확인 (grep, docstring)
2. 외부 노출 필요시 re-export
3. API는 urls.py 연결 확인
4. 내부 전용이면 고아 허용 (문서화)

추측 금지, 코드 확인 후 작업
```

---

## 📈 전체 작업 흐름

```
┌─────────────────────────────────────────────────────────────┐
│                    작업 흐름                                 │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  [현재 상태]                                                 │
│  고아 모듈: 102개 (정상 8개, 검토 필요 94개)                  │
│                                                              │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 1-A: P1 services (Part 1) - 15개               │   │
│  │  - circuit_breaker(6), emergency_mode(4),           │   │
│  │    error_budget.reconciliation(5)                    │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 1-B: P1 services (Part 2) - 17개               │   │
│  │  - factory(5), runtime_config(7), metrics(5)        │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 1-C: P1 services (Part 3) - 13개               │   │
│  │  - chaos(2), finops(2), blast_radius(1),            │   │
│  │    learning(1), rollback(1), 루트레벨(6+)           │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 2: P2 api (18개)                                │   │
│  │  - tiering(6), error_budget views(3),               │   │
│  │    xtest(5), 기타(4)                                 │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 3: P3 adapters (19개)                           │   │
│  │  - alert(3), audit(3), opentelemetry(4),            │   │
│  │    fastapi(2), metrics(2), 기타(5)                   │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  세션 4: P4 기타 (7개)                                │   │
│  │  - config_tracker, context, utils,                  │   │
│  │    interfaces.notification, audit.api 등            │   │
│  └─────────────────────────────────────────────────────┘   │
│       ▼                                                      │
│  [검증] analyze_dependencies.py 재실행                      │
│       ▼                                                      │
│  [목표] 검토 필요 고아 모듈: 최소화                          │
│         (엔트리포인트 8개 + 선택적 모듈만 고아로 유지)       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## ✅ 완료 체크리스트

### 세션별 완료 상태

| 세션 | 대상 | 모듈 수 | 상태 | 완료일 |
|------|------|--------|------|--------|
| 세션 1-A | services (Part 1) | 15개 | ⬜ 대기 | - |
| 세션 1-B | services (Part 2) | 17개 | ⬜ 대기 | - |
| 세션 1-C | services (Part 3) | 13개 | ⬜ 대기 | - |
| 세션 2 | api | 18개 | ⬜ 대기 | - |
| 세션 3 | adapters | 19개 | ⬜ 대기 | - |
| 세션 4 | 기타 | 7개 | ⬜ 대기 | - |
| 검증 | 전체 | - | ⬜ 대기 | - |

### 최종 검증

- [ ] `python scripts/analyze_dependencies.py` 실행
- [ ] 검토 필요 고아 모듈 수 대폭 감소 확인
- [ ] `python -c "import selfhealing"` 오류 없음
- [ ] CODE_DEPENDENCY_ANALYSIS.md 최종 업데이트

---

## 📝 세션 요청 템플릿

각 세션 시작 시 아래 형식으로 요청:

```
[세션 N] {작업명}

대상: (파일 목록)

작업: (구체적 작업 내용)

추측 금지, 코드 확인 후 작업
```

---

## 참고 문서

- [CODE_DEPENDENCY_ANALYSIS.md](CODE_DEPENDENCY_ANALYSIS.md) - 의존성 분석 보고서
- [middleware_system/00_INDEX.md](middleware_system/00_INDEX.md) - 미들웨어 시스템 인덱스

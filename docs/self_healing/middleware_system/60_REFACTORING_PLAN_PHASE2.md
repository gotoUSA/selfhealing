# 60. 리팩토링 계획 Phase 2: 현실적 기준 적용

> 📅 작성일: 2026-01-19  
> 📋 상태: 계획 수립 완료  
> 🎯 목표: 1000줄/800줄 완화 기준으로 잔여 대형 파일 리팩토링

---

## 1. 업계 표준 조사 결과

### 1.1 유명 오픈소스 프로젝트 현황

| 프로젝트 | 최대 파일 줄 수 | 평균 파일 크기 | 특이사항 |
|---------|--------------|--------------|---------|
| **Django/django** | 2,000줄+ | 300~600줄 | 1,000줄 초과 파일 다수 존재 |
| **psf/requests** | 1,000줄+ | 200~400줄 | models.py 890줄, utils.py 900줄+ |
| **Flask** | 800줄+ | 200~400줄 | 핵심 app.py 800줄+ |
| **FastAPI** | 1,000줄+ | 300~500줄 | routing.py 1,000줄+ |

### 1.2 업계 코딩 표준 분석

| 표준/가이드 | 파일 줄 수 제한 | 함수 줄 수 제한 | 비고 |
|------------|--------------|--------------|-----|
| **Google Python Style Guide** | ❌ 명시 없음 | 40줄 권장 | 함수 단위 관리 중심 |
| **PEP 8** | ❌ 명시 없음 | ❌ 명시 없음 | 줄 길이(79/99자)만 규정 |
| **Black Formatter** | ❌ 명시 없음 | ❌ 명시 없음 | 88자 줄 길이 |
| **Clean Code (Robert Martin)** | 권장 200~500줄 | 권장 20~30줄 | 가이드라인, 강제 아님 |

### 1.3 현실적 결론

> **500줄/400줄 제한은 업계에서 엄격히 적용하지 않음**
> - 대부분의 대형 오픈소스 프로젝트는 1,000줄+ 파일 보유
> - 핵심은 **파일 크기보다 응집도와 단일 책임 원칙**
> - 1,000줄/800줄 기준이 현실적이고 관리 가능한 수준

---

## 2. 현행 코드베이스 현황

### 2.1 파일 크기 분포 (신규 기준 적용 시)

| 범위 | 소스 파일 | 테스트 파일 | 상태 |
|-----|---------|-----------|-----|
| 0-300줄 | 335개 (63%) | - | ✅ 양호 |
| 301-500줄 | 97개 (18%) | - | ✅ 양호 |
| 501-800줄 | 73개 (14%) | - | ✅ 양호 |
| **801-1000줄** | **21개 (4%)** | - | ⚠️ 주의 (테스트만 해당) |
| **1000줄+** | **7개 (1%)** | **4개** | 🔴 리팩토링 필요 |

### 2.2 리팩토링 필요 소스 파일 (1000줄 초과)

| # | 파일 경로 | 줄 수 | 클래스 수 | 분리 우선순위 |
|---|---------|------|---------|------------|
| 1 | audit/hash_chain_performance.py | 1,259 | 9개 | 🔴 높음 |
| 2 | services/security_notification_service.py | 1,170 | 5개 | 🟡 중간 |
| 3 | services/chaos/base/experiment.py | 1,167 | 1개 | ⚪ 낮음 (단일 책임) |
| 4 | api/django/views/chaos.py | 1,151 | 15개+ | 🔴 높음 |
| 5 | adapters/memory/layered_repository.py | 1,070 | 1개 | ⚪ 낮음 (단일 클래스) |
| 6 | adapters/redis/dlq.py | 1,013 | 1개 | ⚪ 낮음 (단일 클래스) |
| 7 | api/django/serializers/config.py | 1,004 | 14개+ | 🟡 중간 |

### 2.3 리팩토링 필요 테스트 파일 (800줄 초과)

| # | 파일 경로 | 줄 수 | 테스트 클래스 수 | 분리 우선순위 |
|---|---------|------|---------------|------------|
| 1 | test_cb_canary_recovery_strategy.py | 1,128 | 7개 | 🟡 중간 |
| 2 | test_cb_cascade_prevention.py | 1,077 | 10개 | 🟡 중간 |
| 3 | test_cb_load_shedding.py | 997 | 10개+ | 🟡 중간 |
| 4 | test_cb_e2e_integration.py | 925 | 9개 | 🟡 중간 |
| 5 | test_event_bus_error_budget_gate.py | 828 | - | ⚪ 낮음 |

---

## 3. 리팩토링 상세 계획

### 3.1 Phase 2-A: 소스 코드 리팩토링 (1000줄+ 파일)

#### 3.1.1 hash_chain_performance.py (1,259줄 → ~9개 모듈)

**현황 분석:**
- 9개 클래스가 하나의 파일에 존재
- 각 클래스가 독립적인 책임 보유

**분리 계획:**

| 신규 파일 | 이동 클래스 | 예상 줄 수 | 책임 |
|----------|-----------|---------|-----|
| performance/lua_atomic.py | LuaAtomicHashChain | ~280줄 | Lua 스크립트 기반 원자적 해시 체인 |
| performance/batch_query.py | PipelineBatchQuery | ~120줄 | 파이프라인 배치 쿼리 |
| performance/batch_writer.py | BatchFlushConfig, BatchFlushWriter | ~150줄 | 배치 플러시 설정 및 쓰기 |
| performance/async_writer.py | AsyncAuditWriter | ~165줄 | 비동기 감사 기록 |
| performance/sampling.py | SamplingConfig, SamplingVerifier | ~185줄 | 샘플링 설정 및 검증 |
| performance/watchdog.py | PendingSequenceWatchdog | ~165줄 | 대기 시퀀스 워치독 |
| performance/manager.py | HashChainPerformanceManager | ~140줄 | 통합 성능 관리자 |
| performance/__init__.py | (re-export) | ~50줄 | 공개 API |

#### 3.1.2 api/django/views/chaos.py (1,151줄 → ~4개 모듈)

**현황 분석:**
- 15개+ View 클래스가 하나의 파일에 존재
- 기능별 그룹화 가능

**분리 계획:**

| 신규 파일 | 이동 View 클래스 | 예상 줄 수 | 책임 |
|----------|---------------|---------|-----|
| views/chaos/config_views.py | SafetyGuardConfigView, BlastRadiusPolicyView, SchedulerConfigView, ReportConfigView | ~300줄 | 설정 관련 뷰 |
| views/chaos/schedule_views.py | ScheduleListView, ScheduleDetailView, ScheduleApprovalView, ScheduleExecuteView | ~300줄 | 스케줄 관련 뷰 |
| views/chaos/safety_views.py | KillSwitchView, SafetyCheckView, BlastRadiusCheckView | ~250줄 | 안전 관련 뷰 |
| views/chaos/report_views.py | ReportListView, ReportDetailView, ReportGenerateView, GradeHistoryView | ~250줄 | 리포트 관련 뷰 |

#### 3.1.3 api/django/serializers/config.py (1,004줄 → ~3개 모듈)

**현황 분석:**
- 14개+ Serializer 클래스가 하나의 파일에 존재
- 설정 도메인별 그룹화 가능

**분리 계획:**

| 신규 파일 | 이동 Serializer 클래스 | 예상 줄 수 | 책임 |
|----------|---------------------|---------|-----|
| serializers/config/core.py | ApplyStrategyMixin, CircuitBreakerConfigSerializer, DLQConfigSerializer, RetryConfigSerializer | ~300줄 | 핵심 설정 |
| serializers/config/sla.py | SLAConfigSerializer, SLODefinitionSerializer, SLOConfigSerializer, ErrorBudgetConfigSerializer | ~350줄 | SLA/SLO 설정 |
| serializers/config/operational.py | RateLimitConfigSerializer, SecurityConfigSerializer, IdempotencyConfigSerializer, NotificationConfigSerializer, ForensicConfigSerializer, MetricsConfigSerializer | ~350줄 | 운영 설정 |

#### 3.1.4 services/security_notification_service.py (1,170줄 → ~3개 모듈)

**현황 분석:**
- 5개 클래스 + 4개 모듈 함수
- SecurityNotificationService가 900줄+ (핵심 클래스)

**분리 계획:**

| 신규 파일 | 이동 대상 | 예상 줄 수 | 책임 |
|----------|---------|---------|-----|
| security/notification/models.py | NotificationChannel, NotificationConfig, NotificationResult, SecurityNotificationResult | ~100줄 | 데이터 모델 |
| security/notification/service.py | SecurityNotificationService | ~950줄 | 핵심 서비스 (추가 분리 검토) |
| security/notification/helpers.py | get_security_notification_service, send_alert, notify_security_incident_by_id, notify_security_incident | ~120줄 | 헬퍼 함수 |

### 3.2 Phase 2-B: 분리 제외 파일 (단일 책임 유지)

다음 파일들은 **단일 클래스**로 구성되어 있으며 **높은 응집도**를 가지므로 분리하지 않음:

| 파일 | 줄 수 | 클래스 수 | 제외 사유 |
|-----|------|---------|---------|
| services/chaos/base/experiment.py | 1,167 | 1개 | ChaosExperiment 추상 클래스 (베이스 클래스) |
| adapters/memory/layered_repository.py | 1,070 | 1개 | LayeredCircuitBreakerStateRepository (응집도 높음) |
| adapters/redis/dlq.py | 1,013 | 1개 | RedisDLQRepository (단일 책임) |

### 3.3 Phase 2-C: 테스트 코드 리팩토링 (800줄+ 파일)

테스트 파일은 **기능별 분리**보다 **현행 유지**를 권장:

**사유:**
1. 테스트 간 공유 fixture로 인한 의존성
2. 테스트 실행 시 컨텍스트 유지 필요
3. IDE에서 클래스 단위 탐색으로 관리 가능

**예외 (분리 권장):**
- 1,000줄 초과 + 7개 이상 테스트 클래스인 경우만 선택적 분리

---

## 4. 우선순위 및 일정

### 4.1 리팩토링 우선순위

| 우선순위 | 파일 | ROI | 예상 공수 |
|---------|-----|-----|---------|
| 🔴 P1 | hash_chain_performance.py | 높음 (9개 클래스 분리) | 2시간 |
| 🔴 P1 | api/django/views/chaos.py | 높음 (15개+ View 분리) | 2시간 |
| 🟡 P2 | api/django/serializers/config.py | 중간 (14개+ Serializer) | 1.5시간 |
| 🟡 P2 | security_notification_service.py | 중간 (5개 클래스) | 1시간 |
| ⚪ P3 | 테스트 파일 (선택적) | 낮음 | 필요시 |

### 4.2 예상 일정

| 단계 | 내용 | 예상 소요 |
|-----|-----|---------|
| Phase 2-A | 소스 코드 리팩토링 (4개 파일) | 6.5시간 |
| Phase 2-B | 분리 제외 파일 문서화 | 0.5시간 |
| Phase 2-C | 테스트 코드 선택적 리팩토링 | 필요시 |
| **총계** | | **~7시간** |

---

## 5. 리팩토링 가이드라인

### 5.1 분리 시 필수 체크리스트

- [ ] 기존 import 경로 유지 (__init__.py에서 re-export)
- [ ] 순환 참조 방지 확인
- [ ] 타입 힌트 정확성 유지
- [ ] 기존 테스트 통과 확인
- [ ] docstring 유지/갱신

### 5.2 패키지 구조 컨벤션

```
패키지명/
├── __init__.py          # 공개 API re-export
├── models.py            # 데이터 클래스, Enum
├── service.py           # 핵심 서비스 클래스
├── helpers.py           # 유틸리티 함수
└── exceptions.py        # 커스텀 예외 (필요시)
```

### 5.3 import 호환성 유지 예시

```python
# 기존: from selfhealing.audit.hash_chain_performance import HashChainPerformanceManager
# 신규: 동일 경로 유지

# audit/hash_chain_performance/__init__.py
from .manager import HashChainPerformanceManager
from .lua_atomic import LuaAtomicHashChain
from .sampling import SamplingConfig, SamplingVerifier
# ... 기타 re-export

__all__ = [
    "HashChainPerformanceManager",
    "LuaAtomicHashChain",
    "SamplingConfig",
    "SamplingVerifier",
    # ...
]
```

---

## 6. 완료 기준

### 6.1 정량적 기준

| 항목 | 목표 |
|-----|-----|
| 1000줄 초과 소스 파일 | 0개 (현재 7개 → 분리 또는 제외 처리) |
| 단일 클래스 1000줄+ 파일 | 허용 (높은 응집도 조건) |
| 신규 패키지 구조 | 모든 분리 파일에 __init__.py 포함 |

### 6.2 정성적 기준

| 항목 | 기준 |
|-----|-----|
| 기존 테스트 | 100% 통과 |
| import 호환성 | 기존 코드 수정 불필요 |
| 문서화 | 각 패키지 README 또는 docstring 포함 |

---

## 7. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|-----|-----|---------|
| 1.0 | 2026-01-19 | 초안 작성 |

---

## 부록 A: 현재 대형 파일 클래스 구조 상세

### A.1 hash_chain_performance.py (1,259줄)

| 클래스명 | 시작 줄 | 책임 |
|---------|-------|-----|
| LuaAtomicHashChain | 44 | Lua 스크립트 기반 원자적 해시 체인 연산 |
| PipelineBatchQuery | 325 | Redis 파이프라인 배치 쿼리 |
| BatchFlushConfig | 444 | 배치 플러시 설정 dataclass |
| BatchFlushWriter | 451 | 배치 플러시 기록기 |
| AsyncAuditWriter | 601 | 비동기 감사 기록기 |
| SamplingConfig | 765 | 샘플링 설정 dataclass |
| SamplingVerifier | 773 | 샘플링 검증기 |
| PendingSequenceWatchdog | 957 | 대기 시퀀스 워치독 |
| HashChainPerformanceManager | 1122 | 통합 성능 관리자 (파사드) |

### A.2 api/django/views/chaos.py (1,151줄)

| 클래스명 | 시작 줄 | 책임 |
|---------|-------|-----|
| SafetyGuardConfigView | 66 | 안전 가드 설정 API |
| BlastRadiusPolicyView | 113 | 블래스트 반경 정책 API |
| SchedulerConfigView | 160 | 스케줄러 설정 API |
| ReportConfigView | 207 | 리포트 설정 API |
| ScheduleListView | 259 | 스케줄 목록 API |
| ScheduleDetailView | 329 | 스케줄 상세 API |
| ScheduleApprovalView | 402 | 스케줄 승인 API |
| ScheduleExecuteView | 445 | 스케줄 실행 API |
| KillSwitchView | 479 | 킬 스위치 API |
| SafetyCheckView | 561 | 안전 검사 API |
| BlastRadiusCheckView | 594 | 블래스트 반경 검사 API |
| ReportListView | 630 | 리포트 목록 API |
| ReportDetailView | 657 | 리포트 상세 API |
| ReportGenerateView | 690 | 리포트 생성 API |
| GradeHistoryView | 723 | 등급 이력 API |

### A.3 api/django/serializers/config.py (1,004줄)

| 클래스명 | 시작 줄 | 책임 |
|---------|-------|-----|
| ApplyStrategyMixin | 18 | 적용 전략 믹스인 |
| CircuitBreakerConfigSerializer | 94 | 서킷 브레이커 설정 직렬화 |
| DLQConfigSerializer | 122 | DLQ 설정 직렬화 |
| RetryConfigSerializer | 145 | 재시도 설정 직렬화 |
| SLAConfigSerializer | 172 | SLA 설정 직렬화 |
| SLODefinitionSerializer | 193 | SLO 정의 직렬화 |
| SLOConfigSerializer | 309 | SLO 설정 직렬화 |
| RateLimitConfigSerializer | 368 | 요청 제한 설정 직렬화 |
| SecurityConfigSerializer | 389 | 보안 설정 직렬화 |
| IdempotencyConfigSerializer | 412 | 멱등성 설정 직렬화 |
| NotificationConfigSerializer | 432 | 알림 설정 직렬화 |
| ForensicConfigSerializer | 460 | 포렌식 설정 직렬화 |
| MetricsConfigSerializer | 507 | 메트릭 설정 직렬화 |
| ErrorBudgetConfigSerializer | 540 | 에러 버짓 설정 직렬화 |

---

## 부록 B: 분리 제외 파일 상세 사유

### B.1 services/chaos/base/experiment.py (1,167줄)

- **클래스 수:** 1개 (ChaosExperiment 추상 클래스)
- **제외 사유:** 
  - 모든 Chaos 실험의 베이스 클래스
  - 분리 시 상속 구조 복잡성 증가
  - 높은 응집도 (모든 메서드가 실험 생명주기 관련)

### B.2 adapters/memory/layered_repository.py (1,070줄)

- **클래스 수:** 1개 (LayeredCircuitBreakerStateRepository)
- **제외 사유:**
  - L1/L2 캐시 계층 구조를 단일 클래스에서 관리
  - 분리 시 캐시 일관성 유지 복잡성 증가
  - 메서드 간 강한 결합

### B.3 adapters/redis/dlq.py (1,013줄)

- **클래스 수:** 1개 (RedisDLQRepository)
- **제외 사유:**
  - Dead Letter Queue의 모든 연산을 단일 클래스에서 관리
  - Redis 트랜잭션 원자성 보장 필요
  - 분리 시 트랜잭션 경계 관리 복잡성 증가

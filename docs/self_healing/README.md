# Self-Healing System Documentation

이 폴더는 Self-Healing 시스템의 전체 문서를 포함합니다.

## 📚 문서 목록

### 개요 및 아키텍처

| 문서 | 설명 | 대상 |
|------|------|------|
| [01_OVERVIEW.md](01_OVERVIEW.md) | 시스템 개요 및 핵심 기능 | 모든 독자 |
| [02_ARCHITECTURE.md](02_ARCHITECTURE.md) | 시스템 아키텍처 및 컴포넌트 관계 | 개발자, 아키텍트 |

### 핵심 컴포넌트

| 문서 | 설명 | 대상 |
|------|------|------|
| [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) | Circuit Breaker 패턴 상세 | 개발자 |
| [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) | Dead Letter Queue 시스템 | 개발자, 운영자 |
| [05_RETRY_BACKOFF.md](05_RETRY_BACKOFF.md) | 재시도 및 백오프 전략 | 개발자 |
| [06_REPLAY_SYSTEM.md](06_REPLAY_SYSTEM.md) | 리플레이 시스템 | 개발자, 운영자 |

### 운영 및 인터페이스

| 문서 | 설명 | 대상 |
|------|------|------|
| [07_CONTROL_API.md](07_CONTROL_API.md) | Control API 레퍼런스 | 개발자, 운영자 |
| [08_OBSERVABILITY.md](08_OBSERVABILITY.md) | 메트릭 및 모니터링 | DevOps, 운영자 |

### 설정 및 운영 가이드

| 문서 | 설명 | 대상 |
|------|------|------|
| [09_CONFIGURATION.md](09_CONFIGURATION.md) | 설정 레퍼런스 | 개발자, DevOps |
| [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) | 운영 가이드 및 Runbook | 운영자 |

### 고급 기능

| 문서 | 설명 | 대상 |
|------|------|------|
| [11_FORENSIC_ADVISOR.md](11_FORENSIC_ADVISOR.md) | Forensic Advisor, Chaos Context, SLA Drift Detection | 개발자, 운영자 |
| [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) | Error Budget 관리 및 배포 동결 권고 시스템 | 개발자, 운영자, SRE |
| [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) | 자율 카오스 엔진 (Continuous Resilience Validation) | SRE, 개발자, 운영자 |

---

## 🚀 빠른 시작

### 개발자용

1. [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 이해
2. [02_ARCHITECTURE.md](02_ARCHITECTURE.md) - 아키텍처 파악
3. [09_CONFIGURATION.md](09_CONFIGURATION.md) - 설정 방법

### 운영자용

1. [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 이해
2. [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 절차
3. [07_CONTROL_API.md](07_CONTROL_API.md) - API 사용법
4. [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 모니터링

---

## 🔗 관련 코드

| 경로 | 설명 |
|------|------|
| `shopping/services/self_healing/` | Self-Healing 서비스 구현 |
| `shopping/services/self_healing/forensic_advisor.py` | Forensic Advisor (의사결정 지원) |
| `shopping/services/self_healing/chaos_context.py` | Chaos Experiment Context |
| `shopping/tasks/drift_detection_tasks.py` | SLA Drift Detection 태스크 |
| `shopping/models/failed_operation.py` | DLQ 모델 |
| `shopping/models/failed_external_request.py` | 외부 API DLQ 및 CB 상태 모델 |
| `shopping/tasks/self_healing_tasks.py` | Celery 태스크 |
| `shopping/views/self_healing/` | API 뷰 |
| `docker/prometheus/` | Prometheus 설정 |
| `docker/grafana/` | Grafana 대시보드 |
| **Error Budget 관련** | |
| `packages/selfhealing-python/src/selfhealing/services/error_budget_service.py` | Error Budget 서비스 |
| `packages/selfhealing-python/src/selfhealing/api/django/views/error_budget.py` | Error Budget API 뷰 |
| `packages/selfhealing-python/src/selfhealing/services/metrics.py` | Error Budget 메트릭 |
| **Chaos Engineering 관련** | |
| `packages/selfhealing-python/src/selfhealing/services/chaos/` | 카오스 엔진 핵심 모듈 |
| `packages/selfhealing-python/src/selfhealing/api/django/views/chaos.py` | Chaos API Views |
| `packages/selfhealing-python/src/selfhealing/tasks/chaos_scheduler.py` | Chaos Celery Tasks |

---

## 📝 문서 갱신 이력

| 날짜 | 버전 | 변경 내용 |
|------|------|----------|
| 2025-12-20 | 1.3 | 자율 카오스 엔진 추가 (13_CHAOS_ENGINEERING.md) |
| 2024-12-21 | 1.2 | Error Budget 관리 및 배포 동결 권고 시스템 추가 (12_ERROR_BUDGET.md) |
| 2024-12-20 | 1.1 | Forensic Advisor, Chaos Context, SLA Drift Detection 추가 |
| 2024-01-XX | 1.0 | 초기 문서 작성 |

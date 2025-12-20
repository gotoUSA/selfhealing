# Self-Healing System 개요

> 이 문서는 현재 코드베이스 기준으로 Self-Healing 시스템의 전체 구조를 설명합니다.

## 📋 목차

1. [시스템 목적](#1-시스템-목적)
2. [핵심 기능 요약](#2-핵심-기능-요약)
3. [주요 컴포넌트](#3-주요-컴포넌트)
4. [패키지 구조](#4-패키지-구조)
5. [관련 문서](#5-관련-문서)

---

## 1. 시스템 목적

Self-Healing 시스템은 외부 서비스 장애, 네트워크 오류, 일시적 실패 등에 대해 **자동 복구 및 장애 격리**를 제공하는 L3(Layer 3) 복원력 계층입니다.

### 🎯 주요 목표

| 목표 | 설명 |
|------|------|
| **연쇄 장애 방지** | Circuit Breaker로 외부 서비스 장애 격리 |
| **자동 재시도** | 지수 백오프 전략으로 일시적 오류 복구 |
| **데이터 손실 방지** | Dead Letter Queue로 실패한 작업 보존 |
| **수동 복구 지원** | Replay 시스템으로 운영자 개입 가능 |
| **완전한 관찰성** | Prometheus 메트릭으로 모니터링 |

### 📊 지원 도메인

```
┌─────────────────────────────────────────────────────────────┐
│                    Self-Healing Layer                        │
├─────────────────────────────────────────────────────────────┤
│  External   │   Point   │   Inventory   │   Webhook   │   Notification   │
│     (1h)    │   (4h)    │     (2h)      │    (8h)     │      (24h)       │
│             │           │               │             │                  │
│   외부 API  │  포인트   │    재고 관리   │  외부 알림  │    이메일/SMS    │
└─────────────────────────────────────────────────────────────┘
                           ↓ SLA 기준 (최대 복구 시간)
```

---

## 2. 핵심 기능 요약

### 2.1 Circuit Breaker (서킷 브레이커)

외부 서비스 장애 시 요청을 차단하여 연쇄 장애를 방지합니다.

```
상태 전환:
[Closed] ──(연속 5회 실패)──► [Open] ──(60초 후)──► [Half-Open]
    ▲                            │                       │
    │                            │                       │
    └──────(2회 연속 성공)────────┴───────(실패)──────────┘
```

**특징:**
- 기본적으로 **비활성화** (Toggle 기반)
- 운영자가 수동으로 `force_open()` / `force_close()` 호출 가능
- 자동 모드에서는 실패 임계값(5회) 도달 시 자동 Open

### 2.2 Dead Letter Queue (DLQ)

재시도 불가능하거나 최대 재시도 횟수 초과 시 실패한 작업을 보존합니다.

```
┌──────────────────────────────────────────────────────────┐
│                    DLQ 라이프사이클                       │
│                                                          │
│   [실패] → [PENDING] → [REVIEWING] → [RESOLVED/REJECTED] │
│              │                              ↓            │
│              └──── [REPLAYED] ─────────────►             │
└──────────────────────────────────────────────────────────┘
```

### 2.3 Retry with Exponential Backoff

지수 백오프 전략으로 일시적 오류를 자동 복구합니다.

```
재시도 간격:
- Attempt 1: 4초 (±1초 jitter)
- Attempt 2: 16초 (±4초 jitter)  
- Attempt 3: 64초 (±16초 jitter)
- Attempt 4+: 180초 (최대값)
```

### 2.4 Replay System

DLQ에 저장된 실패 작업을 수동 또는 자동으로 재실행합니다.

- **Manual Replay**: 운영자가 개별 항목 선택
- **Batch Replay**: 필터 기준으로 다건 처리
- **Conditional Replay**: Circuit Breaker Close 시 자동 트리거

### 2.5 Control API

Self-Healing 시스템을 제어하는 REST API입니다.

```
POST /api/self-healing/control/     # 서비스 제어
GET  /api/self-healing/status/      # 전체 상태 조회
POST /api/self-healing/block/{svc}/ # 서비스 차단
POST /api/self-healing/allow/{svc}/ # 서비스 허용
POST /api/self-healing/reset/{svc}/ # 서비스 리셋
GET  /api/self-healing/dlq/list/    # DLQ 목록
POST /api/self-healing/dlq/replay/  # DLQ 재실행
GET  /api/self-healing/metrics/     # 메트릭 조회
GET  /api/self-healing/health/      # 헬스 체크
```

---

## 3. 주요 컴포넌트

### 3.1 서비스 계층

| 서비스 | 위치 | 역할 |
|--------|------|------|
| `CircuitBreakerService` | `shopping/services/self_healing/circuit_breaker_service.py` | 서킷 브레이커 상태 관리 |
| `DLQService` | `shopping/services/self_healing/dlq_service.py` | Dead Letter Queue 관리 |
| `ReplayService` | `shopping/services/self_healing/replay_service.py` | DLQ 재실행 처리 |
| `RetryHandler` | `shopping/services/self_healing/retry_handler.py` | 재시도 로직 |
| `ControlAPIService` | `shopping/services/self_healing/control_api_service.py` | 제어 API 비즈니스 로직 |
| `IdempotencyService` | `shopping/services/self_healing/idempotency_service.py` | 멱등성 보장 |
| `BackoffCalculator` | `shopping/services/self_healing/backoff_calculator.py` | 백오프 계산 |

### 3.2 모델 계층

| 모델 | 위치 | 역할 |
|------|------|------|
| `FailedOperation` | `shopping/models/failed_operation.py` | DLQ 엔트리 (범용) |
| `FailedExternalRequest` | `shopping/models/failed_external_request.py` | 외부 API 전용 DLQ |
| `CircuitBreakerState` | `shopping/models/failed_external_request.py` | 서킷 브레이커 상태 |
| `SecurityIncident` | `shopping/models/security_incident.py` | 보안 위반 기록 |

### 3.3 Celery 태스크

| 태스크 | 위치 | 역할 |
|--------|------|------|
| `check_circuit_breaker_recovery` | `shopping/tasks/self_healing_tasks.py` | CB 상태 전환 체크 |
| `conditional_replay_on_circuit_close` | `shopping/tasks/self_healing_tasks.py` | CB Close 시 자동 리플레이 |
| `force_open_circuit_breaker` | `shopping/tasks/self_healing_tasks.py` | CB 강제 Open |
| `force_close_circuit_breaker` | `shopping/tasks/self_healing_tasks.py` | CB 강제 Close |

---

## 4. 패키지 구조

### 4.1 레거시 패키지 (Django 통합)

```
shopping/services/self_healing/
├── __init__.py              # 공개 API 익스포트
├── config.py                # 설정값 정의
├── circuit_breaker_service.py
├── dlq_service.py
├── replay_service.py
├── retry_handler.py
├── backoff_calculator.py
├── control_api_service.py
├── idempotency_service.py
├── forensic_context.py
├── metrics.py               # Prometheus 메트릭
├── adapters/
│   └── django_repositories.py  # Django ORM 어댑터
└── interfaces/
    └── repositories.py      # 추상 인터페이스
```

### 4.2 독립 패키지 (Framework-Agnostic)

```
packages/selfhealing-python/src/selfhealing/
├── __init__.py
├── core/                    # 핵심 타입 및 설정
│   ├── types.py
│   ├── config.py
│   └── backoff.py
├── services/                # 비즈니스 로직
│   ├── circuit_breaker/
│   ├── dlq_service.py
│   └── replay_service.py
├── adapters/                # 프레임워크별 어댑터
│   ├── django/
│   ├── celery/
│   └── memory/
├── interfaces/              # 추상 인터페이스
│   └── repositories.py
└── metrics/                 # 메트릭 수집
    └── prometheus.py
```

> **참고**: 레거시 패키지는 deprecated 경고를 발생시키며, 새로운 `selfhealing` 패키지로의 마이그레이션이 권장됩니다.

---

## 5. 관련 문서

| 문서 | 설명 |
|------|------|
| [02_ARCHITECTURE.md](02_ARCHITECTURE.md) | 시스템 아키텍처 상세 |
| [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) | Circuit Breaker 상세 |
| [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) | DLQ 시스템 상세 |
| [05_RETRY_BACKOFF.md](05_RETRY_BACKOFF.md) | 재시도 전략 상세 |
| [06_REPLAY_SYSTEM.md](06_REPLAY_SYSTEM.md) | Replay 시스템 상세 |
| [07_CONTROL_API.md](07_CONTROL_API.md) | Control API 상세 |
| [08_OBSERVABILITY.md](08_OBSERVABILITY.md) | 메트릭 및 모니터링 |
| [09_CONFIGURATION.md](09_CONFIGURATION.md) | 설정 참조 |
| [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) | 운영자 가이드 |
| [11_FORENSIC_ADVISOR.md](11_FORENSIC_ADVISOR.md) | Forensic Advisor 상세 |
| [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) | Error Budget 관리 |
| [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) | 자율 카오스 엔진 |

---

## 버전 정보

- **현재 버전**: 1.1.0
- **마지막 업데이트**: 2025-12-21
- **담당자**: SelfHealing Team

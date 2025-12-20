# Self-Healing Forensic Advisor & Decision Support

## 개요

Forensic Advisor는 Self-Healing 시스템의 **의사결정 지원(Decision Support)** 모듈입니다. DLQ(Dead Letter Queue)에 쌓인 실패 항목을 분석하여 운영자에게 **데이터 기반의 조언**을 제공합니다.

### 핵심 원칙

> **"시스템은 데이터를 제공하고, 최종 결정은 사람이 한다 (Human-in-the-loop)"**

이 모듈은 **절대로** 자동으로 설정을 변경하거나 액션을 실행하지 않습니다. 오직 분석 결과와 권장 사항만 제공하며, 실제 액션은 운영자가 Control API를 통해 직접 실행해야 합니다.

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Decision Support Architecture                        │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐   │
│  │  FailedOperation │────▶│ Forensic Advisor │────▶│  Advisory Hint   │   │
│  │    (DLQ Entry)   │     │    (Analyzer)    │     │ (Recommendation) │   │
│  └──────────────────┘     └──────────────────┘     └──────────────────┘   │
│          │                        │                        │               │
│          │                        │                        ▼               │
│          │                        │              ┌──────────────────┐     │
│          │                        │              │  next_action_hint│     │
│          │                        │              │  (Human readable)│     │
│          │                        │              └──────────────────┘     │
│          │                        ▼                                        │
│          │               ┌──────────────────┐                              │
│          │               │ Pattern Matching │                              │
│          │               │  (KNOWN_PATTERNS)│                              │
│          │               └──────────────────┘                              │
│          │                        │                                        │
│          ▼                        ▼                                        │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │                     metadata.forensic_advisory                    │     │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │     │
│  │  │ matched_pattern │  │   confidence    │  │ decision_factors│  │     │
│  │  │   (audit trail) │  │  (0.0 - 1.0)    │  │   (evidence)    │  │     │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘  │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                                                             │
│                            ⬇ HUMAN DECISION REQUIRED ⬇                     │
│                                                                             │
│  ┌──────────────────────────────────────────────────────────────────┐     │
│  │                         Control API                               │     │
│  │            (Operator executes action after review)                │     │
│  └──────────────────────────────────────────────────────────────────┘     │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 주요 기능

### 1. SLA 드리프트 감지 (Drift Detection)

설정된 SLA와 실제 운영 지표 간의 괴리를 선제적으로 파악합니다.

#### Celery 태스크

```python
from shopping.tasks.drift_detection_tasks import check_sla_drift

# 주기적 실행 (celery beat 권장: 매 15분)
result = check_sla_drift.delay()
```

#### 감지 조건

| 조건 | 심각도 | 메시지 |
|------|--------|--------|
| SLA 위반율 > 10% | Warning | 설정 검토 필요 |
| SLA 위반율 > 25% | Critical | 즉시 검토 필요 |
| 평균 복구시간 > SLA의 80% | Warning | SLA 위반 가능성 |
| Pending 항목 5개 이상 위험 | Warning | 즉시 확인 필요 |

#### 출력 예시

```json
{
  "success": true,
  "checked_at": "2024-12-20T10:00:00Z",
  "domains_checked": ["payment", "point", "inventory"],
  "warnings": [
    {
      "type": "SLA_BREACH_RATE_HIGH",
      "domain": "payment",
      "severity": "critical",
      "message": "[payment] SLA 위반율이 25.0%입니다. (임계값: 10%) 설정 검토가 필요합니다.",
      "recommendation": "[ACTION REQUIRED: 운영자 검토 필요]"
    }
  ],
  "metrics": {
    "payment": {
      "total_resolved": 40,
      "avg_recovery_seconds": 2400,
      "sla_threshold_seconds": 3600,
      "sla_breach_rate": 25.0
    }
  }
}
```

⚠️ **중요**: 이 태스크는 **경고만 생성**합니다. SLA 임계값을 자동으로 변경하지 않습니다.

---

### 2. 카오스 실험 컨텍스트 (Chaos Context)

실제 장애와 의도된 카오스 실험을 구분합니다.

#### 사용법

```python
from shopping.services.self_healing.chaos_context import (
    create_chaos_context,
    attach_chaos_context,
    is_chaos_experiment,
    resolve_chaos_experiment,
    ChaosExperimentType,
)

# 카오스 컨텍스트 생성
context = create_chaos_context(
    experiment_type=ChaosExperimentType.LATENCY_INJECTION,
    target_service="payment_gateway",
    target_domain="payment",
    duration_seconds=600,  # 10분
    initiated_by="admin",
    initiated_from="gameday_2024_Q4",
    approval_ticket="TICKET-1234",
)

# DLQ 항목에 연결
attach_chaos_context(failed_operation, context)

# 카오스 실험 여부 확인
if is_chaos_experiment(operation):
    # [CHAOS] 플래그가 표시됨
    pass

# 실험 완료 시 자동 해결
resolve_chaos_experiment(operation, "Experiment completed successfully")
```

#### 카오스 실험 유형

```python
class ChaosExperimentType(str, Enum):
    LATENCY_INJECTION = "latency_injection"
    ERROR_5XX = "error_5xx"
    ERROR_4XX = "error_4xx"
    TIMEOUT = "timeout"
    CONNECTION_RESET = "connection_reset"
    RATE_LIMIT = "rate_limit"
    CIRCUIT_BREAKER_OPEN = "circuit_breaker_open"
    RESOURCE_EXHAUSTION = "resource_exhaustion"
    PARTIAL_FAILURE = "partial_failure"
    CASCADING_FAILURE = "cascading_failure"
```

#### 메타데이터 구조

```json
{
  "chaos_experiment_context": {
    "experiment_id": "chaos-a1b2c3d4",
    "experiment_name": "Payment Gateway Latency Test",
    "experiment_type": "latency_injection",
    "started_at": "2024-12-20T10:00:00Z",
    "expires_at": "2024-12-20T10:10:00Z",
    "target_service": "payment_gateway",
    "target_domain": "payment",
    "injection_rate": 0.001,
    "injected_latency_ms": 500,
    "status": "active",
    "auto_resolve": true,
    "initiated_by": "admin",
    "initiated_from": "gameday_2024_Q4",
    "approval_ticket": "TICKET-1234"
  },
  "is_chaos_experiment": true
}
```

---

### 3. 포렌식 어드바이저 (Forensic Advisor)

DLQ 항목을 분석하여 권장 조치를 제안합니다.

#### 사용법

```python
from shopping.services.self_healing.forensic_advisor import (
    get_forensic_advisor,
    analyze_and_update_operation,
)

# 단일 분석
advisor = get_forensic_advisor()
advisory = advisor.analyze(failed_operation)

print(advisory.recommended_action)  # "replay"
print(advisory.advisory_message)    # "패턴 분석 결과: 일시적 네트워크 장애로..."
print(advisory.confidence)          # 0.85

# 분석 후 메타데이터 업데이트 (권장)
advisory = analyze_and_update_operation(failed_operation)
# → operation.next_action_hint가 자동 업데이트됨
# → operation.metadata["forensic_advisory"]에 상세 분석 저장
```

#### 알려진 패턴 (KNOWN_PATTERNS)

| 패턴 ID | 설명 | 권장 조치 |
|---------|------|----------|
| `TRANSIENT_NETWORK` | 일시적 네트워크 장애 | Replay 권장 |
| `RATE_LIMIT` | 레이트 리밋 초과 | 대기 후 재시도 |
| `AUTH_FAILURE` | 인증/권한 오류 | 보안 점검 필요 |
| `VALIDATION_ERROR` | 데이터 검증 실패 | 수동 확인 필요 |
| `SERVICE_UNAVAILABLE` | 외부 서비스 장애 | 대기 후 재시도 |
| `REPEATED_FAILURE` | 반복 실패 (3회 이상) | 에스컬레이션 |
| `DUPLICATE_REQUEST` | 중복 요청 | Archive 처리 |

#### 분석 결과 구조

```json
{
  "forensic_advisory": {
    "analyzed_at": "2024-12-20T10:05:00Z",
    "analyzer_version": "1.0.0",
    "matched_pattern_id": "TRANSIENT_NETWORK",
    "matched_pattern_name": "Transient Network Failure",
    "confidence": 0.85,
    "level": "info",
    "recommended_action": "replay",
    "advisory_message": "패턴 분석 결과: 일시적 네트워크 장애로 보입니다. Replay를 권장합니다.",
    "evidence": {
      "error_code": "ETIMEDOUT",
      "retry_count": 1,
      "avg_latency_ms": 3500
    },
    "decision_factors": [
      "Error code match: ETIMEDOUT",
      "Message pattern match confidence: 0.85"
    ]
  }
}
```

---

## 의사결정 추적 (Decision Traceability)

모든 조언과 결정은 감사 추적을 위해 기록됩니다.

#### 결정 기록

```python
from shopping.tasks.drift_detection_tasks import record_advisory_decision

# 운영자가 결정 후 기록
record_advisory_decision.delay(
    operation_id=123,
    decision="approved_replay",
    decided_by="admin_user",
    notes="검토 후 승인. 일시적 네트워크 장애로 확인됨.",
)
```

#### 기록 구조

```json
{
  "decision_records": [
    {
      "decided_at": "2024-12-20T10:10:00Z",
      "decided_by": "admin_user",
      "decision": "approved_replay",
      "notes": "검토 후 승인",
      "advisory_at_decision": "2024-12-20T10:05:00Z",
      "advisory_recommendation": "replay",
      "advisory_confidence": 0.85
    }
  ]
}
```

---

## Celery Beat 스케줄 설정

```python
# myproject/celery.py 또는 settings.py

CELERY_BEAT_SCHEDULE = {
    # SLA 드리프트 감지: 15분마다
    "check-sla-drift": {
        "task": "shopping.tasks.drift_detection_tasks.check_sla_drift",
        "schedule": timedelta(minutes=15),
        "options": {"queue": "maintenance"},
    },
    # 포렌식 분석: 10분마다
    "analyze-pending-operations": {
        "task": "shopping.tasks.drift_detection_tasks.analyze_pending_operations",
        "schedule": timedelta(minutes=10),
        "options": {"queue": "maintenance"},
    },
    # 카오스 실험 정리: 5분마다
    "cleanup-chaos-experiments": {
        "task": "shopping.tasks.drift_detection_tasks.cleanup_expired_chaos_experiments",
        "schedule": timedelta(minutes=5),
        "options": {"queue": "maintenance"},
    },
}
```

---

## 관련 문서

- [09_CONFIGURATION.md](09_CONFIGURATION.md) - SLA 설정 참조
- [07_CONTROL_API.md](07_CONTROL_API.md) - 액션 실행 API
- [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) - DLQ 구조

---

## 테스트

```bash
# Forensic Advisor 테스트
pytest tests/self_healing/unit/test_forensic_advisor.py -v

# Chaos Context 테스트
pytest tests/self_healing/unit/test_chaos_context.py -v

# Drift Detection 테스트
pytest tests/self_healing/unit/test_drift_detection.py -v
```

---

## 주의사항

### ❌ 금지 사항

1. **자동 설정 변경 금지**: SLA 임계값, Circuit Breaker 설정 등을 시스템이 자동으로 변경하지 않습니다.
2. **자동 액션 실행 금지**: Replay, Block 등의 액션은 반드시 운영자가 직접 실행합니다.
3. **경고 무시 금지**: 모든 경고에는 `[ACTION REQUIRED]` 태그가 포함되며, 운영자 검토가 필수입니다.

### ✅ 허용 사항

1. **데이터 분석**: 메트릭 수집 및 패턴 분석
2. **조언 생성**: `next_action_hint` 필드 업데이트
3. **경고 발생**: 로그 및 알림 채널로 경고 전송
4. **카오스 실험 정리**: `auto_resolve=True`인 만료된 실험만 자동 해결

---

## 버전 히스토리

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2024-12-20 | 초기 릴리스: Forensic Advisor, Chaos Context, Drift Detection |

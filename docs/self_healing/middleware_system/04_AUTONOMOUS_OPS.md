# Self-Healing 자율 운영 시스템

> **Version**: 2.2.0
> **Updated**: 2026-01-02
> **Category**: 자율 운영 및 백그라운드 자동화

---

## 📋 목차

1. [개요](#1-개요)
2. [자동화 서비스](#2-자동화-서비스)
3. [Celery 어댑터](#3-celery-어댑터)
4. [Metrics 모듈](#4-metrics-모듈)
5. [Utils 모듈](#5-utils-모듈)
6. [Tasks 모듈](#6-tasks-모듈)

---

## 1. 개요

이 문서는 Self-Healing 시스템의 **자율 운영 및 백그라운드 자동화**를 다룹니다.

### 1.1 범위

- 자동화 서비스 (FinOps, Learning, Anomaly Detection)
- Celery Beat 태스크
- Prometheus 메트릭 수집
- 유틸리티 함수
- 스케줄링 및 Governance

### 1.2 아키텍처

```
┌─────────────────────────────────────────────────────────────────────────┐
│                       04_AUTONOMOUS_OPS 범위                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Celery Beat Scheduler                         │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │    │
│  │  │ chaos_       │ │ drift_       │ │ governance   │            │    │
│  │  │ scheduler    │ │ detection    │ │ tasks        │            │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘            │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Autonomous Services                           │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │    │
│  │  │ FinOps       │ │ Adaptive     │ │ Anomaly      │            │    │
│  │  │ Service      │ │ Learning     │ │ Detector     │            │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘            │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │    │
│  │  │ Pattern      │ │ TrendAnalyzer│ │ Capacity     │            │    │
│  │  │ Recognizer   │ │              │ │ Planner      │            │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘            │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                    │                                     │
│                                    ▼                                     │
│  ┌────────────────────────────────────────────────────────────────┐    │
│  │                    Metrics & Observability                       │    │
│  │  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐            │    │
│  │  │ Prometheus   │ │ Event        │ │ Reliability  │            │    │
│  │  │ Metrics      │ │ Handlers     │ │ Manager      │            │    │
│  │  └──────────────┘ └──────────────┘ └──────────────┘            │    │
│  └────────────────────────────────────────────────────────────────┘    │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 자동화 서비스

> 자율적으로 동작하는 학습 및 최적화 서비스

### 2.1 FinOpsService

**경로**: `selfhealing.services.finops`

**역할**: 비용 최적화 및 리소스 관리

| 메서드 | 설명 |
|--------|------|
| `analyze_costs` | 비용 분석 |
| `recommend_scaling` | 스케일링 권장 |
| `track_budget` | 예산 추적 |
| `generate_report` | 리포트 생성 |

**분석 항목**:
- 클라우드 리소스 사용량
- 유휴 리소스 탐지
- 예약 인스턴스 권장
- 비용 이상 탐지

### 2.2 AdaptiveLearningService

**경로**: `selfhealing.services.learning`

**역할**: 시스템 동작 학습 및 적응

| 메서드 | 설명 |
|--------|------|
| `train_model` | 모델 학습 |
| `predict` | 예측 수행 |
| `update_weights` | 가중치 업데이트 |
| `get_recommendations` | 권장 조치 조회 |

**학습 대상**:
- 트래픽 패턴
- 에러 발생 패턴
- 복구 시간 최적화
- Circuit Breaker 임계값

### 2.3 AnomalyDetectorService

**경로**: `selfhealing.services.anomaly`

**역할**: 이상 징후 탐지

| 메서드 | 설명 |
|--------|------|
| `detect` | 이상 탐지 |
| `train_baseline` | 베이스라인 학습 |
| `get_anomalies` | 이상 목록 조회 |
| `set_sensitivity` | 민감도 설정 |

**탐지 알고리즘**:
- Z-Score 기반
- IQR (Interquartile Range)
- 이동 평균 편차
- Isolation Forest (ML 기반)

### 2.4 PatternRecognizerService

**경로**: `selfhealing.services.pattern`

**역할**: 반복 패턴 인식

| 메서드 | 설명 |
|--------|------|
| `find_patterns` | 패턴 탐색 |
| `classify_incident` | 인시던트 분류 |
| `suggest_resolution` | 해결책 제안 |

**인식 패턴**:
- 배포 후 에러 급증
- 피크 시간대 장애
- 연쇄 실패 (Cascade)
- 메모리 누수 징후

### 2.5 TrendAnalyzerService

**경로**: `selfhealing.services.trend`

**역할**: 트렌드 분석

| 메서드 | 설명 |
|--------|------|
| `analyze_trend` | 트렌드 분석 |
| `forecast` | 예측 |
| `detect_seasonality` | 계절성 탐지 |

### 2.6 CapacityPlannerService

**경로**: `selfhealing.services.capacity`

**역할**: 용량 계획

| 메서드 | 설명 |
|--------|------|
| `estimate_capacity` | 용량 추정 |
| `plan_scaling` | 스케일링 계획 |
| `alert_threshold` | 임계값 알림 |

---

## 3. Celery 어댑터

**경로**: `selfhealing.adapters.celery/`

### 3.1 Tasks

**경로**: `selfhealing.adapters.celery.tasks`

| 태스크 | 설명 |
|--------|------|
| `process_dlq_item` | DLQ 항목 처리 |
| `run_circuit_breaker_check` | CB 상태 체크 |
| `send_alert` | 알림 전송 |
| `collect_metrics` | 메트릭 수집 |
| `run_health_check` | 헬스 체크 실행 |

**태스크 정의 예시**:
```python
from celery import shared_task

@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    autoretry_for=(Exception,),
    acks_late=True,
)
def process_dlq_item(self, item_id: str):
    """DLQ 항목 처리 태스크"""
    from selfhealing.services import get_dlq_service

    service = get_dlq_service()
    item = service.get_by_id(item_id)

    try:
        result = service.process(item)
        return {'status': 'success', 'result': result}
    except Exception as e:
        service.mark_failed(item, str(e))
        raise self.retry(exc=e)
```

### 3.2 Signal Hooks

**경로**: `selfhealing.adapters.celery.signals`

| 시그널 | 설명 |
|--------|------|
| `task_prerun` | 태스크 시작 전 |
| `task_postrun` | 태스크 완료 후 |
| `task_failure` | 태스크 실패 시 |
| `task_retry` | 태스크 재시도 시 |
| `worker_ready` | 워커 준비 완료 |
| `worker_shutdown` | 워커 종료 시 |

**시그널 핸들러 예시**:
```python
from celery.signals import task_failure
from selfhealing.metrics import get_metrics

@task_failure.connect
def on_task_failure(sender, task_id, exception, args, kwargs, traceback, einfo, **kw):
    """태스크 실패 시 메트릭 기록 및 알림"""
    metrics = get_metrics()
    metrics.task_failures.labels(task_name=sender.name).inc()

    if is_critical_task(sender.name):
        send_alert(
            title=f"Critical Task Failed: {sender.name}",
            message=str(exception),
            severity="high",
        )
```

### 3.3 Beat Schedule

**경로**: `selfhealing.adapters.celery.beat`

```python
CELERYBEAT_SCHEDULE = {
    'process-pending-dlq': {
        'task': 'selfhealing.tasks.process_pending_dlq',
        'schedule': crontab(minute='*/5'),  # 5분마다
    },
    'check-circuit-breakers': {
        'task': 'selfhealing.tasks.check_circuit_breakers',
        'schedule': crontab(minute='*/1'),  # 1분마다
    },
    'run-chaos-experiments': {
        'task': 'selfhealing.tasks.chaos_scheduler.run_scheduled_experiments',
        'schedule': crontab(minute=0, hour='*/2'),  # 2시간마다
    },
    'check-emergency-mode': {
        'task': 'selfhealing.tasks.governance.check_emergency_mode_expiry',
        'schedule': crontab(minute='*/30'),  # 30분마다
    },
    'drift-detection': {
        'task': 'selfhealing.tasks.drift_detection.run_drift_detection',
        'schedule': crontab(minute=0, hour='*/1'),  # 1시간마다
    },
    'daily-resilience-report': {
        'task': 'selfhealing.tasks.chaos_scheduler.generate_daily_resilience_report',
        'schedule': crontab(minute=0, hour=9),  # 매일 9시
    },
}
```

---

## 4. Metrics 모듈

**경로**: `selfhealing.metrics/`

### 4.1 Prometheus Metrics

**경로**: `selfhealing.metrics.prometheus`

| 컴포넌트 | 용도 |
|---------|------|
| `SelfHealingMetrics` | Self-Healing 메트릭 클래스 (싱글톤) |
| `get_metrics` | 메트릭 인스턴스 조회 함수 |

**제공 메트릭**:

| 메트릭 | 타입 | 설명 |
|--------|------|------|
| `dlq_items_total` | Counter | DLQ 항목 총 개수 |
| `dlq_pending_count` | Gauge | 대기 중인 DLQ 항목 수 |
| `circuit_breaker_state` | Gauge | CB 상태 (0=closed, 1=open, 0.5=half_open) |
| `recovery_time_seconds` | Histogram | 복구 시간 |
| `replay_attempts_total` | Counter | 리플레이 시도 횟수 |
| `error_budget_remaining` | Gauge | 남은 Error Budget |

**사용법**:
```python
from selfhealing.metrics import get_metrics

metrics = get_metrics()

# Counter 증가
metrics.dlq_items_total.labels(domain="payment", status="pending").inc()

# Gauge 설정
metrics.dlq_pending_count.labels(domain="payment").set(10)

# Histogram 관측
metrics.recovery_time_seconds.labels(domain="payment").observe(5.2)
```

### 4.2 Event Handlers

**경로**: `selfhealing.metrics.event_handlers`

| 컴포넌트 | 용도 |
|---------|------|
| `DLQMetricEventHandler` | DLQ 메트릭 이벤트 핸들러 |
| `CircuitBreakerEventHandler` | CB 메트릭 이벤트 핸들러 |
| `ReplayEventHandler` | 리플레이 메트릭 이벤트 핸들러 |
| `reset_event_handler_cache` | 이벤트 핸들러 캐시 초기화 |

### 4.3 Safe Gauge

**경로**: `selfhealing.metrics.safe_gauge`

| 컴포넌트 | 용도 |
|---------|------|
| `SafeGauge` | 스레드 안전 Gauge 래퍼 |
| `SafeGaugeChild` | SafeGauge 자식 래퍼 |

**특징**:
- 멀티프로세스 환경 안전
- 레이스 컨디션 방지
- 원자적 연산

### 4.4 Decorators

**경로**: `selfhealing.metrics.decorators`

| 데코레이터 | 용도 |
|------------|------|
| `@track_dlq_creation` | DLQ 생성 추적 |
| `@track_dlq_resolution` | DLQ 해결 추적 |
| `@track_replay` | 리플레이 추적 |
| `@track_execution_time` | 실행 시간 추적 |
| `@track_counter` | 카운터 추적 |

**사용법**:
```python
from selfhealing.metrics.decorators import track_execution_time

@track_execution_time(metric_name="payment_processing_seconds")
def process_payment(order_id: str):
    ...
```

### 4.5 Jitter

**경로**: `selfhealing.metrics.jitter`

| 컴포넌트 | 용도 |
|---------|------|
| `with_jitter` | 지터 적용 컨텍스트 매니저 |
| `calculate_jitter` | 지터 계산 함수 |
| `sleep_with_jitter` | 지터가 적용된 슬립 함수 |
| `JitterConfig` | 지터 설정 클래스 |

**용도**:
- 메트릭 수집 시간 분산
- Thundering Herd 방지
- 스케줄 충돌 방지

### 4.6 Reconciler

**경로**: `selfhealing.metrics.reconciler`

| 컴포넌트 | 용도 |
|---------|------|
| `MetricReconciler` | 메트릭 드리프트 조정기 |
| `DriftSeverity` | 드리프트 심각도 Enum |
| `DriftResult` | 드리프트 결과 DTO |
| `SyncResult` | 동기화 결과 DTO |
| `get_reconciler` | 조정기 인스턴스 조회 함수 |

**기능**:
- DB ↔ Prometheus 메트릭 불일치 탐지
- 주기적 동기화
- 드리프트 알림

### 4.7 Reliability

**경로**: `selfhealing.metrics.reliability`

| 컴포넌트 | 용도 |
|---------|------|
| `MetricReliability` | 메트릭 신뢰도 Enum |
| `METRIC_RELIABILITY_MAP` | 메트릭별 신뢰도 매핑 |
| `get_metric_reliability` | 메트릭 신뢰도 조회 |
| `get_reliability_description` | 신뢰도 설명 조회 |

**신뢰도 레벨**:
- `EXACT`: 정확 (DB 조회 기반)
- `EVENTUAL`: 최종 일관성 (캐시 기반)
- `APPROXIMATE`: 근사치 (샘플링 기반)

### 4.8 Reliability Manager

**경로**: `selfhealing.metrics.reliability_manager`

| 컴포넌트 | 용도 |
|---------|------|
| `ReliabilityLevel` | 신뢰도 레벨 Enum |
| `OperatingMode` | 운영 모드 Enum |
| `ReliabilityThresholds` | 신뢰도 임계값 설정 |
| `MetricReliabilityState` | 메트릭 신뢰도 상태 |

**운영 모드**:
- `NORMAL`: 정상 - 모든 기능 활성화
- `CAUTIOUS`: 주의 - 위험 작업 경고
- `STRICT`: 엄격 - 고신뢰도 메트릭만 사용
- `EMERGENCY`: 긴급 - 최소 기능만 활성화

### 4.9 Snapshot Storage

**경로**: `selfhealing.metrics.snapshot_storage`

| 컴포넌트 | 용도 |
|---------|------|
| `MetricSnapshot` | 메트릭 스냅샷 데이터 클래스 |
| L1 Local Snapshot | 로컬 파일 기반 스냅샷 저장소 |

**용도**:
- 프로세스 재시작 시 메트릭 복원
- 메트릭 히스토리 보존
- 비교 분석용 스냅샷

---

## 5. Utils 모듈

**경로**: `selfhealing.utils/`

### 5.1 Time Utilities

**경로**: `selfhealing.utils.time`

| 함수 | 용도 |
|------|------|
| `utc_now()` | UTC 현재 시간 조회 |
| `ensure_aware(dt)` | 타임존 인식 datetime 변환 |
| `to_iso_string(dt)` | ISO 문자열 변환 |
| `from_iso_string(s)` | ISO 문자열 파싱 |
| `elapsed_seconds(start, end)` | 경과 시간 계산 |
| `is_expired(dt, ttl)` | 만료 여부 확인 |
| `add_seconds(dt, seconds)` | 초 추가 |
| `format_duration(seconds)` | 기간 포맷팅 |

**사용법**:
```python
from selfhealing.utils.time import utc_now, elapsed_seconds, format_duration

start = utc_now()
# ... 작업 수행 ...
elapsed = elapsed_seconds(start, utc_now())
print(format_duration(elapsed))  # "5m 32s"
```

### 5.2 Async Healing Logger

**경로**: `selfhealing.utils.async_logger`

| 컴포넌트 | 용도 |
|---------|------|
| `AsyncHealingLogger` | 비동기 힐링 이벤트 로거 (Zero-Latency) |
| `EventSeverity` | 이벤트 심각도 Enum |

**특징**:
- 일반 이벤트: 배치로 모아서 전송
- CRITICAL 이벤트: 즉시 전송 (비동기지만 바로)
- 복구 경로에서 ~100ms 단축

**사용법**:
```python
from selfhealing.utils.async_logger import AsyncHealingLogger, EventSeverity

def send_to_command_center(events):
    requests.post('http://command-center/events', json=events)

# 설정 및 시작
AsyncHealingLogger.configure(flush_callback=send_to_command_center)
AsyncHealingLogger.start()

# 일반 이벤트 (배치 처리)
AsyncHealingLogger.log({'type': 'retry', 'service': 'payment'})

# CRITICAL 이벤트 (즉시 전송)
AsyncHealingLogger.log(
    {'type': 'cb_open', 'service': 'payment'},
    EventSeverity.CRITICAL
)

# 종료
AsyncHealingLogger.stop()
```

---

## 6. Tasks 모듈

**경로**: `selfhealing.tasks/`

### 6.1 Chaos Scheduler

**경로**: `selfhealing.tasks.chaos_scheduler`

| 함수 | 용도 |
|------|------|
| `run_scheduled_experiments` | 스케줄된 Chaos 실험 실행 |
| `generate_daily_resilience_report` | 일일 복원력 리포트 생성 |
| `cleanup_expired_approvals` | 만료된 승인 요청 정리 |
| `check_and_alert_pending_approvals` | 대기 중인 승인 알림 발송 |

**실험 스케줄 설정**:
```python
CHAOS_SCHEDULE = {
    'latency-test': {
        'enabled': True,
        'schedule': '0 */4 * * *',  # 4시간마다
        'target_services': ['payment', 'inventory'],
        'injection_type': 'latency',
        'params': {'duration_ms': 500},
    },
    'failure-test': {
        'enabled': True,
        'schedule': '0 2 * * 6',  # 매주 토요일 2시
        'target_services': ['notification'],
        'injection_type': 'exception',
        'params': {'error_rate': 0.1},
    },
}
```

### 6.2 Config Apply

**경로**: `selfhealing.tasks.config_apply`

| 태스크 | 용도 |
|--------|------|
| `apply_pending_config_changes` | 대기 중인 설정 변경 적용 |
| `apply_graceful_config_change` | Graceful 설정 변경 적용 |

**Graceful 적용 프로세스**:
1. 진행 중인 요청 완료 대기
2. 새 설정 적용
3. 검증 테스트 실행
4. 롤백 포인트 생성

### 6.3 Drift Detection

**경로**: `selfhealing.tasks.drift_detection`

| 컴포넌트 | 용도 |
|---------|------|
| `SLADriftDetector` | SLA 드리프트 감지기 |
| `FailedOperationQuerySet` | 쿼리셋 프로토콜 |
| `FailedOperationProtocol` | FailedOperation 프로토콜 |
| `SLAThresholdsProtocol` | SLA 임계값 프로토콜 |

**원칙**: "System provides data, humans make decisions."
- 경고만 생성
- 자동 조정 없음
- 운영자 판단 필요

**드리프트 임계값**:
| 레벨 | 드리프트 | 동작 |
|------|----------|------|
| Warning | 5% | 로그 기록 |
| Critical | 20% | 알림 발송 |
| Incident | 50% | 이벤트 유실 의심, 에스컬레이션 |

### 6.4 Governance

**경로**: `selfhealing.tasks.governance`

| 함수 | 용도 |
|------|------|
| `check_emergency_mode_expiry` | 긴급 모드 만료 체크 및 자동 복구 |
| `get_governance_beat_schedule` | Celery Beat 스케줄 설정 조회 |

**자동 복구 타임라인**:
| 경과 시간 | 동작 |
|-----------|------|
| 4시간 | Admin 경고 발송 |
| 6시간 | "2시간 후 자동 복구" 최종 경고 |
| 8시간 | NORMAL 모드로 자동 복구 |

**Governance 규칙**:
```python
GOVERNANCE_RULES = {
    'emergency_mode': {
        'max_duration_hours': 8,
        'warning_at_hours': [4, 6],
        'require_justification': True,
        'require_approval_for_extension': True,
    },
    'chaos_experiments': {
        'require_approval': True,
        'approval_timeout_hours': 24,
        'max_blast_radius': 0.1,  # 최대 10% 영향
    },
    'config_changes': {
        'require_review': True,
        'auto_apply_low_risk': True,
        'rollback_on_error': True,
    },
}
```

---

## 📎 관련 문서

- [00_INDEX.md](00_INDEX.md) - 문서 인덱스
- [01_MIDDLEWARE_GATEWAY.md](01_MIDDLEWARE_GATEWAY.md) - 미들웨어 게이트웨이
- [02_LOGIC_ENGINE.md](02_LOGIC_ENGINE.md) - 비즈니스 로직 엔진
- [03_INFRA_ADAPTER.md](03_INFRA_ADAPTER.md) - 인프라 어댑터

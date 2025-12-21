# Self-Healing Operations Guide

## 개요

이 문서는 Self-Healing 시스템의 운영자를 위한 가이드입니다. 일상적인 운영 작업, 장애 대응, 트러블슈팅 절차를 다룹니다.

---

## 📊 일일 운영 체크리스트

### 아침 점검 (Daily Health Check)

| 점검 항목 | 확인 방법 | 정상 기준 |
|-----------|-----------|-----------|
| Circuit Breaker 상태 | Grafana Dashboard | 모든 서비스 CLOSED |
| DLQ 대기 항목 수 | `GET /api/self-healing/control/dlq/summary/` | < 10개 |
| 재시도 성공률 | Prometheus: `retry_success_rate` | > 80% |
| SLA 위반 현황 | `GET /api/self-healing/control/sla/breaches/` | 0건 |
| 에러 버짯 잔여량 | Error Budget Dashboard | > 50% |

### 주간 점검 (Weekly Review)

- [ ] DLQ 오래된 항목 검토 (7일 이상 대기)
- [ ] Circuit Breaker 전환 이력 분석
- [ ] SLA 위반 트렌드 확인
- [ ] 도메인별 실패 패턴 분석

---

## 🔧 일반 운영 절차

### 1. DLQ 상태 확인

```bash
# DLQ 요약 조회
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/control/dlq/summary/

# 도메인별 대기 항목 조회
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/self-healing/control/dlq/list/?domain=payment&status=pending"
```

**응답 예시:**

```json
{
  "pending_by_domain": {
    "payment": 3,
    "point": 1,
    "inventory": 0
  },
  "total_pending": 4,
  "sla_at_risk": 1
}
```

### 2. 수동 리플레이 실행

```bash
# 단일 항목 리플레이
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": 12345}' \
  http://localhost:8000/api/self-healing/control/dlq/replay/

# 배치 리플레이 (도메인 전체)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "payment", "max_items": 10}' \
  http://localhost:8000/api/self-healing/control/dlq/batch-replay/
```

### 3. DLQ 항목 수동 해결 처리

```bash
# 수동 해결 완료 처리
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"id": 12345, "resolution_type": "manual_fix", "notes": "CS팀에서 수동 처리 완료"}' \
  http://localhost:8000/api/self-healing/control/dlq/resolve/
```

### 4. Circuit Breaker 상태 확인

```bash
# 현재 상태 조회
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/control/status/

# 특정 서비스 상태
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/self-healing/control/status/?service=toss_payment"
```

---

## 🚨 장애 대응 Runbook

### Scenario 1: Circuit Breaker OPEN

**증상:**
- Prometheus 알림: `CircuitBreakerOpen`
- 결제/외부 API 호출 실패 급증

**대응 절차:**

```
1. 외부 서비스 상태 확인
   └─ PG사 상태 페이지 확인
   └─ 직접 API 호출 테스트

2. 장애 범위 파악
   └─ Grafana에서 영향받는 도메인 확인
   └─ DLQ 급증 여부 확인

3-a. 외부 서비스 장애인 경우
     └─ CB 열림 상태 유지 (정상 동작)
     └─ PG사 복구 대기
     └─ 복구 후 자동 Half-Open 전환 확인

3-b. 일시적 오류인 경우 (false positive)
     └─ CB 수동 닫기 고려
     └─ force_close API 사용

4. 복구 후
   └─ 조건부 리플레이 자동 트리거 확인
   └─ DLQ 항목 리플레이 성공 확인
```

**CB 수동 닫기:**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"service": "toss_payment", "reason": "PG사 복구 확인, 수동 닫기"}' \
  http://localhost:8000/api/self-healing/control/allow/
```

### Scenario 2: DLQ 급증 (Spike)

**증상:**
- Prometheus 알림: `DLQSpikeDetected` 또는 `DLQHighPendingCount`
- 5분 내 DLQ 항목 2배 이상 증가

**대응 절차:**

```
1. 원인 분석
   └─ DLQ 항목의 failure_type 확인
   └─ 공통 패턴 식별 (특정 PG? 특정 상품?)

2. 원인별 대응
   2-a. 외부 서비스 장애
        └─ CB 열림 확인
        └─ 복구 대기

   2-b. 코드 버그
        └─ 롤백 검토
        └─ Hotfix 배포

   2-c. 데이터 문제
        └─ 문제 데이터 격리
        └─ 수동 수정 후 리플레이

3. 안정화 후
   └─ 배치 리플레이 실행
   └─ 성공률 모니터링
```

### Scenario 3: 재시도 성공률 저하

**증상:**
- Prometheus 알림: `RetrySuccessRateLow`
- 재시도 성공률 < 80%

**대응 절차:**

```
1. 실패 원인 분석
   └─ DLQ의 최근 실패 항목 조회
   └─ error_context 분석

2. 패턴 식별
   └─ 동일 에러 반복? → 버그 가능성
   └─ 타임아웃 증가? → 인프라 문제
   └─ 특정 시간대? → 부하 문제

3. 조치
   └─ 필요시 재시도 설정 조정
   └─ 근본 원인 해결 후 리플레이
```

### Scenario 4: SLA 위반 발생

**증상:**
- Prometheus 알림: `SLABreachDetected`
- 도메인 SLA 시간 초과

**대응 절차:**

```
1. 위반 항목 즉시 확인
   curl /api/self-healing/control/sla/breaches/

2. 우선순위 판단
   └─ payment: 최우선 (1시간 SLA)
   └─ inventory: 높음 (2시간 SLA)
   └─ point: 중간 (4시간 SLA)

3. 즉시 조치
   └─ 리플레이 가능: 수동 리플레이 실행
   └─ 수동 개입 필요: CS팀 에스컬레이션

4. 포스트모템
   └─ SLA 위반 원인 분석
   └─ 재발 방지책 수립
```

---

## 🔄 Circuit Breaker 제어

### 상태 전이 다이어그램

```
         ┌──────────────────────────────────────────────────────┐
         │                    CLOSED                             │
         │   (정상 운영, 요청 통과)                              │
         └──────────────────────────────────────────────────────┘
                              │
                              │ failure_count >= threshold
                              ▼
         ┌──────────────────────────────────────────────────────┐
         │                     OPEN                              │
         │   (차단, 요청 즉시 실패)                              │
         │   • 신규 요청 → 즉시 거부                            │
         │   • 실패한 요청 → DLQ 저장                           │
         └──────────────────────────────────────────────────────┘
                              │
                              │ recovery_timeout 경과
                              ▼
         ┌──────────────────────────────────────────────────────┐
         │                  HALF_OPEN                            │
         │   (테스트, 제한된 요청 허용)                          │
         │   • 일부 요청만 통과                                 │
         │   • 성공 → CLOSED 전환                               │
         │   • 실패 → OPEN 복귀                                 │
         └──────────────────────────────────────────────────────┘
```

### 수동 제어 명령

| 작업 | API | Risk Level |
|------|-----|------------|
| 강제 열기 (차단) | `POST /api/self-healing/control/block/` | HIGH |
| 강제 닫기 (허용) | `POST /api/self-healing/control/allow/` | HIGH |
| 상태 조회 | `GET /api/self-healing/control/status/` | LOW |

**주의사항:**
- 수동 제어는 TTL(기본 90분)이 적용됨
- TTL 만료 후 자동 제어로 복귀
- 수동 제어 사용 시 반드시 사유 기록

```bash
# 강제 열기 (외부 장애 대응)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "service": "toss_payment",
    "reason": "PG사 점검 공지로 인한 선제적 차단",
    "ttl_minutes": 60
  }' \
  http://localhost:8000/api/self-healing/control/block/
```

---

## 📈 모니터링 대시보드

### Grafana 대시보드 목록

| 대시보드 | URL | 용도 |
|----------|-----|------|
| Self-Healing Overview | `/d/self-healing-overview` | 전체 상태 요약 |
| DLQ Monitoring | `/d/dlq-monitoring` | DLQ 상세 분석 |
| Error Budget | `/d/error-budget` | SLO/Error Budget 추적 |

### 핵심 패널

**1. Circuit Breaker 상태 패널**
- 색상: 녹색(CLOSED), 빨강(OPEN), 노랑(HALF-OPEN)
- 서비스별 현재 상태 표시

**2. DLQ 대기 항목 패널**
- 도메인별 대기 항목 수
- 임계값: 녹색(<10), 노랑(<50), 빨강(≥100)

**3. 재시도 성공률 패널**
- 도메인별 성공률 게이지
- 목표: 80% 이상

---

## 🔍 트러블슈팅

### 문제: DLQ 항목이 리플레이되지 않음

**체크리스트:**

1. Circuit Breaker 상태 확인
   ```bash
   curl /api/self-healing/control/status/
   # CB가 OPEN이면 리플레이 차단됨
   ```

2. 리플레이 핸들러 등록 확인
   ```python
   from selfhealing.services.replay_service import get_replay_registry
   registry = get_replay_registry()
   print(registry.get_handler("payment"))  # None이면 미등록
   ```

3. 항목 상태 확인
   ```bash
   curl "/api/self-healing/control/dlq/list/?id=12345"
   # status가 'pending'이어야 리플레이 가능
   ```

### 문제: Circuit Breaker가 열리지 않음

**체크리스트:**

1. CB 활성화 확인
   ```python
   from selfhealing.core.config import get_circuit_breaker_settings
   cb = get_circuit_breaker_settings()
   print(cb.enabled)  # False면 비활성화
   ```

2. 실패 카운트 확인
   ```sql
   SELECT * FROM circuit_breaker_state WHERE service_name = 'toss_payment';
   ```

3. threshold 설정 확인
   ```python
   print(cb.failure_threshold)  # 기본값 5
   ```

### 문제: 메트릭이 수집되지 않음

**체크리스트:**

1. /metrics 엔드포인트 확인
   ```bash
   curl http://localhost:8000/metrics | grep dlq
   ```

2. Prometheus 타겟 상태 확인
   - Prometheus UI > Status > Targets
   - django job이 UP 상태인지 확인

3. 게이지 업데이트 태스크 확인
   ```bash
   celery -A myproject inspect scheduled
   # collect_metrics 태스크가 스케줄되어 있는지 확인
   ```

### 문제: Gauge 값이 실제와 다름 (Drift)

**체크리스트:**

1. 마지막 동기화 시간 확인
   ```bash
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/self-healing/metrics/status/
   ```

2. 수동 동기화 실행
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/self-healing/metrics/sync/
   ```

3. Drift 감지 포함 동기화
   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"detect_drift": true}' \
     http://localhost:8000/api/self-healing/metrics/sync/
   ```

4. 이벤트 핸들러 호출 여부 확인
   - 비즈니스 로직에서 `DLQMetricEventHandler.on_item_created()` 호출 여부
   - 로그에서 `[EventHandler]` 메시지 확인

### 문제: Gauge 값이 음수로 표시됨

**원인:** 서버 재시작 후 `dec()` 호출 시 발생 (Shadow counter 리셋)

**해결책:**

1. SafeGauge 사용 확인
   ```python
   from shopping.metrics.safe_gauge import SafeGauge
   
   # 올바른 사용
   safe_gauge = SafeGauge(original_gauge, "gauge_name")
   safe_gauge.labels(domain="payment").inc()
   safe_gauge.labels(domain="payment").dec()  # 0 미만으로 떨어지지 않음
   ```

2. 동기화로 Shadow counter 복구
   ```python
   # 단일 레이블 동기화
   safe_gauge.labels(domain="payment").sync_from_source(actual_db_count)
   
   # 또는 Reconciler 사용
   from shopping.metrics.reconciler import MetricReconciler
   reconciler = MetricReconciler()
   reconciler.sync_all_gauges()
   ```

3. SafeGauge 보호 동작 확인
   ```python
   # 현재 shadow 값 확인
   shadow = safe_gauge.labels(domain="payment").get_shadow_value()
   print(f"Shadow value: {shadow}")
   
   # 음수 시도 시 경고 로그 확인
   # [SafeGauge] gauge_name{domain=payment} dec blocked: shadow=0
   ```

### 문제: 로깅 레벨이 적절하지 않음

**증상:** DLQ 이벤트가 INFO로 기록되어 너무 많거나, Circuit Breaker가 WARNING으로 놓쳐짐

**해결책:**

1. 런타임에서 로깅 레벨 변경
   ```python
   from shopping.config import EventLoggingConfig
   
   config = EventLoggingConfig.get_instance()
   config.update(
       dlq_log_level="WARNING",  # 로그 줄이기
       circuit_breaker_log_level="INFO",  # 더 자세히
       updated_by="ops-team"
   )
   ```

2. 환경 변수로 기본값 설정
   ```bash
   export SH_EVENT_DLQ_LOG_LEVEL=WARNING
   export SH_EVENT_CIRCUIT_BREAKER_LOG_LEVEL=INFO
   export SH_EVENT_SLA_LOG_LEVEL=ERROR
   ```

3. 설정 초기화
   ```python
   config.reset()  # 환경 변수 기본값으로 복원
   ```

### 문제: 서버 시작 시 DB 부하 급증 (Thundering Herd)

**체크리스트:**

1. Jitter 활성화 확인
   ```bash
   # 환경 변수 확인
   echo $SELFHEALING_METRICS_JITTER_ENABLED  # true여야 함
   ```

2. Jitter 값 조정 (K8s 환경)
   ```bash
   # Pod 수에 따라 조정
   # 10 Pods: 30초, 100+ Pods: 60초
   SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS=60.0
   ```

3. 로그에서 Jitter 적용 확인
   ```
   [Jitter] Sleeping for 45.23s before sync_all_gauges
   ```

---

## 📊 메트릭 동기화 운영

> 상세 문서: [13_METRIC_COLLECTION_STRATEGY.md](13_METRIC_COLLECTION_STRATEGY.md)

### 메트릭 수집 전략 개요

| 메트릭 타입 | 수집 방식 | 정확도 | DB 쿼리 |
|------------|----------|--------|---------|
| Counter | Push Only | 100% | 없음 |
| Histogram | Push Only | 100% | 없음 |
| Gauge | Hybrid | ~99% | 재시작 시만 |

### 수동 동기화 실행

```bash
# 모든 Gauge 동기화
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/metrics/sync/

# 특정 도메인만 동기화
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"domain": "payment"}' \
  http://localhost:8000/api/self-healing/metrics/sync/
```

### Drift 임계값 관리

```bash
# 현재 Drift 임계값 조회
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/config/drift-thresholds/

# 임계값 변경 (런타임)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "warning_threshold": 0.10,
    "critical_threshold": 0.30
  }' \
  http://localhost:8000/api/self-healing/config/drift-thresholds/

# 기본값으로 리셋
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/self-healing/config/drift-thresholds/reset/
```

### Drift 심각도별 대응

| 심각도 | Drift 비율 | 알림 | 대응 |
|--------|-----------|------|------|
| normal | < 5% | 없음 | 정상 |
| warning | 5~20% | 로그 | 모니터링 |
| critical | 20~50% | Slack | 원인 분석 |
| incident | > 50% | 인시던트 | 즉시 조치 |

**50% 이상 Drift 발생 시:**

```
1. 인시던트 자동 생성됨
2. 이벤트 핸들러 호출 여부 확인
   └─ 비즈니스 로직에서 on_item_created() 호출?
   └─ 최근 배포에서 핸들러 제거됨?
3. 로그에서 누락된 이벤트 추적
4. 수동 동기화로 값 복원
```

### 모니터링 체크리스트

| 항목 | 확인 방법 | 기대값 |
|------|----------|--------|
| 메트릭 엔드포인트 | `curl /metrics` | 200 OK |
| Gauge 동기화 상태 | 로그: "Metrics reconciled" | 서버 시작 시 1회 |
| Jitter 적용 | 로그 시간 확인 | 인스턴스별 다른 시간 |
| Drift 경고 | 로그: "Metric drift detected" | 없음 (정상) |
| Drift 인시던트 | 로그: "METRIC INTEGRITY INCIDENT" | 없음 (정상) |

---

## 📋 관리 명령어

### Django Management Commands

```bash
# DLQ 상태 요약
python manage.py dlq_summary

# 오래된 DLQ 항목 정리
python manage.py cleanup_old_dlq --days=30 --dry-run

# SLA 위반 항목 조회
python manage.py check_sla_breaches --domain=payment

# Circuit Breaker 상태 조회
python manage.py cb_status

# 알림 규칙 생성
python manage.py generate_self_healing_alerts
```

### Celery Tasks

| 태스크 | 스케줄 | 설명 |
|--------|--------|------|
| `check_circuit_breaker_recovery` | 30초마다 | CB 자동 복구 체크 |
| `collect_all_metrics` | 1분마다 | 메트릭 게이지 업데이트 |
| `check_sla_breaches` | 5분마다 | SLA 위반 체크 |
| `cleanup_resolved_dlq` | 매일 자정 | 해결된 DLQ 정리 |

---

## 🚨 Error Budget 동결 대응

> 상세 문서: [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md)

### Scenario 5: Error Budget 위험 (< 20%)

**증상:**
- Prometheus 알림: `ErrorBudgetCritical`
- 배포 정책 API에서 `freeze_recommended` 상태

**대응 절차:**

```
1. Error Budget 상태 확인
   curl -H "Authorization: Bearer $TOKEN" \
     http://localhost:8000/api/self-healing/error-budget/status/

2. 배포 동결 결정
   ┌─────────────────────┐
   │ 동결 권고 확인       │
   └─────────┬───────────┘
             │
       ┌─────┴─────┐
       ▼           ▼
   ┌───────┐   ┌───────────┐
   │ 확정  │   │ Override  │
   │       │   │ (긴급시)  │
   └───┬───┘   └─────┬─────┘
       │             │
       ▼             ▼
   API 호출:     API 호출:
   /acknowledge  /override

3-a. 동결 확정 시
     └─ 모든 신규 기능 배포 중지
     └─ 기존 이슈 해결에 집중
     └─ 안정화 작업 수행

3-b. Override 승인 시 (긴급 배포 필요)
     └─ 사유 명시 (HOTFIX/SECURITY_PATCH)
     └─ 만료 시간 설정 (기본 4시간)
     └─ 배포 완료 후 상황 모니터링

4. 상황 해결 후
   └─ Error Budget 회복 확인 (> 50%)
   └─ 동결 해제 API 호출
   └─ 포스트모템 수행
```

**동결 확정 (Acknowledge):**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"justification": "Error budget critical, pausing all deployments"}' \
  http://localhost:8000/api/self-healing/deployment-policy/acknowledge/
```

**긴급 배포 Override:**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "justification": "Critical security patch CVE-2024-XXXX",
    "override_type": "security_patch",
    "deployment_name": "auth-service v2.1.0",
    "expires_hours": 2
  }' \
  http://localhost:8000/api/self-healing/deployment-policy/override/
```

**동결 해제:**

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"justification": "Situation resolved, budget recovered to 65%"}' \
  http://localhost:8000/api/self-healing/deployment-policy/lift/
```

### Scenario 6: Fast Burn Rate 감지

**증상:**
- Prometheus 알림: `ErrorBudgetFastBurn`
- 1시간 Burn Rate > 14.4x

**의미:**
- 현재 속도로 2일 내 Error Budget 완전 소진
- 즉각적인 조치 필요

**대응 절차:**

```
1. 원인 파악 (최우선)
   └─ 최근 배포 확인
   └─ 외부 서비스 상태 확인
   └─ 에러 로그 분석

2. 즉시 조치
   └─ 원인 배포 롤백
   └─ 외부 서비스 장애 시 CB 확인
   └─ 트래픽 감소 조치 (필요시)

3. 상태 모니터링
   └─ Burn Rate 정상화 확인
   └─ Budget 추가 소진 중단 확인

4. 포스트모템
   └─ 원인 분석
   └─ 재발 방지책
```

---

## 🔐 권한 관리

### 필요 권한

| 작업 | 필요 권한 |
|------|-----------|
| DLQ 조회 | `view_failedoperation` |
| DLQ 리플레이 | `change_failedoperation` |
| CB 상태 조회 | `view_circuitbreakerstate` |
| CB 수동 제어 | `change_circuitbreakerstate` |
| Error Budget 조회 | `view_errorbudget` |
| 배포 동결 결정 | `manage_deployment_freeze` |

### 역할별 권한

| 역할 | 권한 |
|------|------|
| Viewer | DLQ/CB/Error Budget 조회 |
| Operator | DLQ 리플레이, CB 상태 조회, 동결 확정/해제 |
| Admin | 모든 권한 (CB 수동 제어, Override 승인 포함) |

---

## 📞 에스컬레이션 매트릭스

| Severity | 조건 | 알림 채널 | 담당 |
|----------|------|-----------|------|
| Critical | CB OPEN > 5분, DLQ > 100, **Error Budget < 10%** | #critical-alerts, PagerDuty | On-call |
| High | DLQ 급증, SLA 위반, **Fast Burn Rate** | #ops-alerts | Ops 팀 |
| Medium | 재시도 성공률 저하, **Error Budget < 50%** | #dev-alerts | Dev 팀 |
| Low | Error Budget 50-75% | #dev-alerts | Dev 팀 |

---

## 📚 관련 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [04_DEAD_LETTER_QUEUE.md](04_DEAD_LETTER_QUEUE.md) - DLQ 시스템
- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 레퍼런스
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 설정 레퍼런스
- [12_ERROR_BUDGET.md](12_ERROR_BUDGET.md) - Error Budget 관리 및 배포 동결 권고

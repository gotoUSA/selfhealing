# 🔍 SRE 기능 갭 분석

> **문서 목적**: 현재 시스템을 Google SRE 원칙 및 업계 표준과 비교하여 부족한 기능을 식별하고 개선 방향을 제시합니다.

---

## 📊 현재 상태 요약

| 영역 | 구현 상태 | 성숙도 |
|-----|----------|--------|
| 자동 복구 (Self-Healing) | ✅ 구현됨 | 🟢 높음 |
| 재시도 메커니즘 | ✅ 구현됨 | 🟢 높음 |
| Circuit Breaker | ✅ 구현됨 | 🟢 높음 |
| Dead Letter Queue | ✅ 구현됨 | 🟢 높음 |
| 부하 테스트 | ✅ 구현됨 | 🟢 높음 |
| SLI/SLO 정의 | ⚠️ 부분적 | 🟡 중간 |
| 모니터링/Alerting | ⚠️ 부분적 | 🟡 중간 |
| 분산 추적 | ❌ 미구현 | 🔴 낮음 |
| Error Budget | ❌ 미구현 | 🔴 낮음 |
| Runbook 자동화 | ❌ 미구현 | 🔴 낮음 |
| Incident Management | ❌ 미구현 | 🔴 낮음 |
| Chaos Engineering (프로덕션) | ❌ 미구현 | 🔴 낮음 |

---

## ✅ 잘 구현된 기능

### 1. Self-Healing Layer (L3)

**현재 구현**:
- ✅ Exponential Backoff 재시도
- ✅ Dead Letter Queue (DLQ)
- ✅ Circuit Breaker (Toggle 기반)
- ✅ Rate Limit Cascade Detection
- ✅ Self-DDoS Protection
- ✅ SLA Timeout Abort
- ✅ Replay Service (수동/배치/조건부)

**강점**: 결제 도메인에 특화된 견고한 복구 메커니즘

### 2. 테스트 인프라

**현재 구현**:
- ✅ 1,900+ 단위/통합 테스트
- ✅ 74개 부하 테스트 시나리오 (Locust)
- ✅ Chaos, Hybrid, Integration, Load 테스트 분류
- ✅ pytest-xdist 병렬 실행

---

## ❌ 부족한 기능 (SRE 기준)

### 1. SLI/SLO 정의 및 관리

**현재 상태**: 명시적 SLI/SLO 정의 없음

**필요한 것**:
```yaml
# 예시: SLO 정의
slos:
  payment_availability:
    description: "결제 API 가용성"
    target: 99.9%
    measurement: "successful_payments / total_payments"
    window: 30d
    
  payment_latency:
    description: "결제 API P99 지연시간"
    target: 500ms
    measurement: "p99(payment_latency)"
    window: 30d
    
  dlq_resolution_time:
    description: "DLQ 항목 해결 시간"
    target: 4h
    measurement: "time(dlq_created -> dlq_resolved)"
    window: 7d
```

**권장 SLI**:
| SLI 유형 | 측정 대상 | 목표 |
|---------|----------|-----|
| 가용성 | 결제 성공률 | 99.9% |
| 지연시간 | 결제 API P99 | < 500ms |
| 품질 | DLQ 발생률 | < 0.1% |
| 신선도 | 데이터 동기화 지연 | < 1분 |

**구현 방법**:
```python
# shopping/observability/slo.py
from dataclasses import dataclass
from datetime import timedelta

@dataclass
class SLO:
    name: str
    target: float  # 0.999 = 99.9%
    window: timedelta
    
SLOS = {
    "payment_availability": SLO(
        name="결제 API 가용성",
        target=0.999,
        window=timedelta(days=30)
    ),
    "payment_latency_p99": SLO(
        name="결제 지연시간 P99",
        target=0.500,  # 500ms
        window=timedelta(days=30)
    ),
}
```

---

### 2. Error Budget

**현재 상태**: Error Budget 개념 없음

**필요한 것**:
```python
# Error Budget 계산
monthly_requests = 1_000_000
slo_target = 0.999  # 99.9%
error_budget = monthly_requests * (1 - slo_target)  # = 1,000 failures allowed

# 실시간 Error Budget 소진율
current_errors = 150
budget_consumed = current_errors / error_budget  # = 15%
budget_remaining = 100 - (budget_consumed * 100)  # = 85%
```

**Error Budget Policy 예시**:
```yaml
error_budget_policy:
  - condition: "budget_remaining < 50%"
    action: "배포 동결, 안정화 집중"
    
  - condition: "budget_remaining < 25%"
    action: "기능 개발 중단, 장애 대응만"
    
  - condition: "budget_remaining < 10%"
    action: "비상 대응 모드, 모든 팀 합류"
```

---

### 3. 분산 추적 (Distributed Tracing)

**현재 상태**: 기본 로깅만 구현

**필요한 것**:
```python
# OpenTelemetry 통합 예시
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.jaeger.thrift import JaegerExporter

tracer = trace.get_tracer(__name__)

@tracer.start_as_current_span("process_payment")
def process_payment(order_id: str):
    with tracer.start_as_current_span("validate_order") as span:
        span.set_attribute("order.id", order_id)
        validate_order(order_id)
    
    with tracer.start_as_current_span("call_toss_api") as span:
        span.set_attribute("pg.provider", "toss")
        result = call_toss_payment_api()
```

**권장 도구**:
- OpenTelemetry (표준)
- Jaeger (오픈소스 추적)
- AWS X-Ray (AWS 환경)
- Datadog APM (상용)

---

### 4. 모니터링 및 Alerting

**현재 상태**: 
- Prometheus 설정 파일 존재 (docker/prometheus/)
- Grafana 폴더 존재 (docker/grafana/)
- 실제 대시보드/알림 규칙 미구현

**필요한 것**:

#### 4.1 핵심 메트릭 대시보드
```yaml
# grafana/dashboards/self_healing.json
panels:
  - title: "DLQ Pending Count"
    query: "sum(dlq_entries{status='pending'})"
    alert: "pending > 100 for 5m"
    
  - title: "Circuit Breaker States"
    query: "circuit_breaker_state{service=~'.*'}"
    
  - title: "Retry Success Rate"
    query: "rate(retry_success[5m]) / rate(retry_total[5m])"
    
  - title: "Payment Success Rate"
    query: "rate(payment_success[5m]) / rate(payment_total[5m])"
```

#### 4.2 알림 규칙
```yaml
# prometheus/rules/alerts.yml
groups:
  - name: self_healing
    rules:
      - alert: DLQHighPending
        expr: dlq_pending_count > 100
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "DLQ 대기 항목 증가"
          
      - alert: CircuitBreakerOpen
        expr: circuit_breaker_state == 1  # 1 = open
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Circuit Breaker 열림"
          
      - alert: PaymentSuccessRateLow
        expr: payment_success_rate < 0.99
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "결제 성공률 저하"
```

---

### 5. Runbook 자동화

**현재 상태**: 수동 운영 가이드만 존재

**필요한 것**:
```python
# runbooks/circuit_breaker_recovery.py
"""
Runbook: Circuit Breaker Recovery

트리거: CircuitBreakerOpen 알림
자동 실행 단계:
1. PG 상태 확인 (health check)
2. 최근 실패 로그 수집
3. 필요시 자동 Half-Open 전환
4. Slack 알림 발송
5. 온콜 담당자 페이지
"""

async def auto_recover_circuit_breaker(service_name: str):
    # 1. 외부 서비스 상태 확인
    health = await check_pg_health(service_name)
    
    if health.is_healthy:
        # 2. 자동 Half-Open 전환
        await transition_to_half_open(service_name)
        
        # 3. 알림 발송
        await notify_slack(
            channel="#payments-alerts",
            message=f"🔄 {service_name} Circuit Breaker → Half-Open (자동)"
        )
    else:
        # 4. 온콜 페이지
        await page_oncall(
            severity="critical",
            message=f"🔴 {service_name} 복구 실패, 수동 개입 필요"
        )
```

---

### 6. Incident Management

**현재 상태**: 공식 인시던트 관리 프로세스 없음

**필요한 것**:

#### 6.1 인시던트 분류
```yaml
severity_levels:
  P0:
    description: "전체 서비스 다운"
    response_time: "5분 내"
    example: "결제 100% 실패"
    
  P1:
    description: "주요 기능 장애"
    response_time: "15분 내"
    example: "결제 성공률 < 95%"
    
  P2:
    description: "부분적 기능 저하"
    response_time: "1시간 내"
    example: "특정 PG사 장애"
    
  P3:
    description: "경미한 이슈"
    response_time: "다음 영업일"
    example: "로그 누락"
```

#### 6.2 포스트모템 템플릿
```markdown
# Incident #YYYY-MM-DD-001

## 요약
- **발생 시각**: 
- **해결 시각**: 
- **영향 범위**: 
- **심각도**: P1

## 타임라인
- 14:00 - 알림 발생
- 14:05 - 담당자 확인
- 14:15 - 원인 파악
- 14:30 - 해결

## 근본 원인
- 

## 영향
- 결제 실패: 150건
- 예상 손실: ₩1,500,000

## 조치 항목
- [ ] 단기: 
- [ ] 중기: 
- [ ] 장기: 

## 교훈
-
```

---

### 7. Chaos Engineering (프로덕션)

**현재 상태**: 테스트 환경에서만 카오스 테스트 실행

**필요한 것**:
```yaml
# chaos/experiments/payment_failure.yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: payment-pg-latency
spec:
  action: delay
  mode: all
  selector:
    labelSelectors:
      app: payment-service
  delay:
    latency: "500ms"
    correlation: "100"
    jitter: "50ms"
  duration: "5m"
  scheduler:
    cron: "@weekly"  # 매주 실행
```

**권장 도구**:
- Chaos Mesh (Kubernetes)
- Gremlin (상용)
- Litmus (오픈소스)

---

### 8. 카나리 배포 / 점진적 롤아웃

**현재 상태**: 미구현

**필요한 것**:
```yaml
# k8s/deployment-canary.yaml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: payment-service
spec:
  replicas: 10
  strategy:
    canary:
      steps:
        - setWeight: 10  # 10% 트래픽
        - pause: {duration: 10m}
        - setWeight: 25
        - pause: {duration: 10m}
        - setWeight: 50
        - pause: {duration: 10m}
        - setWeight: 100
      analysis:
        templates:
          - templateName: payment-success-rate
        startingStep: 1
```

---

### 9. 용량 계획 (Capacity Planning)

**현재 상태**: 미구현

**필요한 것**:
```python
# capacity/forecasting.py
def forecast_capacity(
    current_tps: float,
    growth_rate: float,  # 월간 성장률
    months_ahead: int
) -> dict:
    """
    향후 용량 요구사항 예측
    """
    projected_tps = current_tps * ((1 + growth_rate) ** months_ahead)
    
    return {
        "current_tps": current_tps,
        "projected_tps": projected_tps,
        "required_instances": ceil(projected_tps / TPS_PER_INSTANCE),
        "required_db_connections": projected_tps * 2,
        "estimated_monthly_cost": calculate_cost(projected_tps)
    }
```

---

### 10. 서비스 의존성 맵

**현재 상태**: 미구현

**필요한 것**:
```yaml
# service-dependencies.yaml
services:
  payment-api:
    type: internal
    tier: 1  # 핵심 서비스
    dependencies:
      - toss-payment-api:
          type: external
          criticality: high
          fallback: null
      - postgres:
          type: internal
          criticality: high
          fallback: read-replica
      - redis:
          type: internal
          criticality: medium
          fallback: local-cache
    slo:
      availability: 99.9%
      latency_p99: 500ms
```

---

## 🎯 권장 구현 우선순위

### Phase 1: 기반 (1-2주)
1. **SLI/SLO 정의** - 핵심 지표 3개 정의
2. **기본 메트릭 수집** - Prometheus 메트릭 추가
3. **Grafana 대시보드** - Self-Healing 모니터링

### Phase 2: 가시성 (2-4주)
4. **Alerting 규칙** - 핵심 알림 5개
5. **분산 추적** - OpenTelemetry 통합
6. **Error Budget** - 대시보드 추가

### Phase 3: 자동화 (4-6주)
7. **Runbook 자동화** - 상위 3개 시나리오
8. **인시던트 관리** - 프로세스 정의
9. **포스트모템 문화** - 템플릿 + 프로세스

### Phase 4: 성숙 (6-8주)
10. **Chaos Engineering** - 프로덕션 실험
11. **카나리 배포** - 점진적 롤아웃
12. **용량 계획** - 예측 모델

---

## 📚 참고 자료

- [Google SRE Book](https://sre.google/sre-book/table-of-contents/)
- [Google SRE Workbook](https://sre.google/workbook/table-of-contents/)
- [The Art of SLOs](https://sre.google/resources/practices-and-processes/art-of-slos/)
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [Chaos Mesh](https://chaos-mesh.org/)

---

*최종 업데이트: 2025-01*

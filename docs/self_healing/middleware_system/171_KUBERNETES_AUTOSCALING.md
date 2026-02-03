# 171. Kubernetes Autoscaling 구현

> **버전**: 1.1.0
> **작성일**: 2026-01-31
> **수정일**: 2026-02-03 (리뷰 반영)
> **의존성**: [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md)
> **예상 소요**: 2-3일

---

## 0. 리뷰 반영 사항 (v1.1.0)

| 항목 | 상태 | 섹션 |
|------|------|------|
| DB 커넥션 풀 계산식 | ✅ 추가 | §1.3 |
| task_reject_on_worker_lost 설정 | ✅ 추가 | §3.7 |
| Redis 폴링 부하 모니터링 | ✅ 추가 | §5.2 |
| RPS 기반 HPA (Prometheus Adapter) | ✅ 추가 | §3.8 |
| HPA 플래핑 방지 테스트 | ✅ 추가 | §6.4 |

---

## 1. 현재 문제점 (코드 근거)

### 1.1 고정된 Celery Worker 설정

**파일**: `k8s/celery-critical-worker.yaml`
**라인**: 16-25

```yaml
spec:
  replicas: 1  # ❌ 고정 1개
  selector:
    matchLabels:
      app: celery-critical-worker
  template:
    spec:
      containers:
        - name: celery-critical-worker
          command:
            - celery
            - -A
            - shopping
            - worker
            - --loglevel=info
            - --concurrency=2  # ❌ 고정 concurrency
            - --queues=critical
```

**문제점**:
1. **고정 레플리카**: 트래픽 증가 시 수동 스케일링 필요
2. **고정 concurrency**: Pod당 처리량 고정
3. **HPA 없음**: 자동 확장/축소 불가

---

### 1.2 Django 웹 서버도 고정

**파일**: `k8s/` 디렉토리 내 Django deployment (추정)

일반적으로 웹 서버도 고정 레플리카로 배포됨.

---

### 1.3 DB 커넥션 풀 한계치 계산 (신규)

> ⚠️ **리뷰 반영**: Django Pod 스케일 아웃 시 PostgreSQL `max_connections` 초과 위험

**코드 근거:**

```python
# requirements.txt (Line 54)
django-db-connection-pool==1.2.6

# myproject/settings/production.py (Line 54)
"CONN_MAX_AGE": 120,

# packages/selfhealing-python/src/selfhealing/core/safe_defaults.py (Line 339)
"connection_pool_size": (1, 100),
```

**커넥션 계산 공식:**

```
Total_Connections = (Django_Max_Pods × Workers_per_Pod × Conn_per_Worker)
                  + (Celery_Max_Pods × Concurrency × Conn_per_Task)
                  + Reserved_Connections

PostgreSQL_Max_Connections ≥ Total_Connections / 0.8  (20% 여유율)
```

**현재 설정 기준 계산:**

| 컴포넌트 | Max Pods | Workers/Concurrency | Conn/Unit | 소계 |
|----------|----------|---------------------|-----------|------|
| Django API | 20 | 4 (gunicorn) | 1 | 80 |
| Celery Default | 10 | 4 | 1 | 40 |
| Celery Critical | 5 | 2 | 1 | 10 |
| Celery Audit | 3 | 4 | 1 | 12 |
| Reserved (migrations, admin) | - | - | - | 10 |
| **Total** | - | - | - | **152** |

**권장 PostgreSQL 설정:**

```sql
-- postgresql.conf
max_connections = 200  -- 152 / 0.8 = 190, 여유 포함 200
```

**PgBouncer 도입 권장 (선택):**

```yaml
# k8s/pgbouncer-deployment.yaml (신규)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pgbouncer
  namespace: production
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: pgbouncer
          image: edoburu/pgbouncer:1.21.0
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: postgres-secrets
                  key: url
            - name: POOL_MODE
              value: "transaction"
            - name: DEFAULT_POOL_SIZE
              value: "20"
            - name: MAX_CLIENT_CONN
              value: "500"
            - name: MAX_DB_CONNECTIONS
              value: "100"
          ports:
            - containerPort: 5432
```

---

## 2. 구현 계획

### 2.1 HPA 적용 대상

| 컴포넌트 | 현재 | 목표 | 스케일 지표 |
|---------|-----|-----|-----------|
| Django API | 고정 2 | 2-20 | CPU, RPS |
| Celery Default Worker | 고정 2 | 2-10 | 큐 길이 |
| Celery Critical Worker | 고정 1 | 1-5 | 큐 길이 |
| Celery Audit Flush Worker | 없음 | 1-3 | Redis 버퍼 크기 |

### 2.2 아키텍처

```
                                    Metrics
                                       ↑
                    ┌──────────────────┴──────────────────┐
                    │                                      │
              Prometheus                             KEDA
              Adapter                              Scaler
                    │                                      │
                    ↓                                      ↓
               ┌────────┐                          ┌────────┐
               │  HPA   │                          │  HPA   │
               │ (CPU)  │                          │(Custom)│
               └────┬───┘                          └────┬───┘
                    │                                    │
          ┌─────────┴─────────┐              ┌───────────┴───────────┐
          ↓                   ↓              ↓                       ↓
     ┌─────────┐        ┌─────────┐    ┌──────────┐          ┌──────────┐
     │ Django  │        │ Django  │    │ Celery   │          │ Celery   │
     │ Pod 1   │        │ Pod N   │    │ Worker 1 │          │ Worker N │
     └─────────┘        └─────────┘    └──────────┘          └──────────┘
```

---

## 3. 구현 상세

### 3.1 Django API HPA (CPU 기반)

**파일**: `k8s/django-api-hpa.yaml` (신규)

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: django-api-hpa
  namespace: production
  labels:
    app: django-api
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: django-api

  minReplicas: 2
  maxReplicas: 20

  metrics:
    # CPU 사용률 기반
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70  # 70% 도달 시 스케일 아웃

    # 메모리 사용률 기반 (보조)
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 80

  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30  # 30초 안정화
      policies:
        - type: Percent
          value: 100  # 100% 증가 (2배)
          periodSeconds: 30
        - type: Pods
          value: 4  # 또는 4개씩
          periodSeconds: 30
      selectPolicy: Max

    scaleDown:
      stabilizationWindowSeconds: 300  # 5분 안정화 (급격한 축소 방지)
      policies:
        - type: Percent
          value: 50  # 50% 감소
          periodSeconds: 60
      selectPolicy: Max
```

---

### 3.2 Celery Worker HPA (KEDA - 큐 길이 기반)

KEDA (Kubernetes Event-Driven Autoscaling)를 사용하여 Redis 큐 길이 기반 스케일링.

**파일**: `k8s/keda-scaledobject-celery-default.yaml` (신규)

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: celery-default-worker-scaledobject
  namespace: production
spec:
  scaleTargetRef:
    name: celery-default-worker

  minReplicaCount: 2
  maxReplicaCount: 10

  pollingInterval: 15  # 15초마다 체크
  cooldownPeriod: 60   # 스케일 다운 대기 60초

  triggers:
    # Redis 큐 길이 기반
    - type: redis
      metadata:
        address: redis-master.production.svc.cluster.local:6379
        listName: celery  # Celery 기본 큐
        listLength: "100"  # 100개 초과 시 스케일 아웃
      authenticationRef:
        name: redis-trigger-auth
```

**파일**: `k8s/keda-triggerauth-redis.yaml`

```yaml
apiVersion: keda.sh/v1alpha1
kind: TriggerAuthentication
metadata:
  name: redis-trigger-auth
  namespace: production
spec:
  secretTargetRef:
    - parameter: password
      name: redis-secrets
      key: redis-password
```

---

### 3.3 Celery Critical Worker HPA

**파일**: `k8s/keda-scaledobject-celery-critical.yaml` (신규)

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: celery-critical-worker-scaledobject
  namespace: production
spec:
  scaleTargetRef:
    name: celery-critical-worker

  minReplicaCount: 1
  maxReplicaCount: 5

  pollingInterval: 5    # Critical은 더 자주 체크 (5초)
  cooldownPeriod: 30    # 빠른 스케일 다운 (30초)

  triggers:
    - type: redis
      metadata:
        address: redis-master.production.svc.cluster.local:6379
        listName: critical  # Critical 큐
        listLength: "10"    # 10개 초과 시 스케일 아웃 (민감)
      authenticationRef:
        name: redis-trigger-auth

  advanced:
    horizontalPodAutoscalerConfig:
      behavior:
        scaleUp:
          stabilizationWindowSeconds: 0  # 즉시 스케일 아웃
          policies:
            - type: Pods
              value: 2
              periodSeconds: 10
```

---

### 3.4 Celery Audit Flush Worker HPA

**파일**: `k8s/keda-scaledobject-celery-audit.yaml` (신규)

```yaml
apiVersion: keda.sh/v1alpha1
kind: ScaledObject
metadata:
  name: celery-audit-worker-scaledobject
  namespace: production
spec:
  scaleTargetRef:
    name: celery-audit-worker

  minReplicaCount: 1
  maxReplicaCount: 3

  pollingInterval: 10
  cooldownPeriod: 120

  triggers:
    # Redis Audit 버퍼 크기 기반
    - type: redis
      metadata:
        address: redis-master.production.svc.cluster.local:6379
        listName: audit_flush  # Audit 플러시 큐
        listLength: "50"
      authenticationRef:
        name: redis-trigger-auth

    # 또는 Prometheus 메트릭 기반
    # - type: prometheus
    #   metadata:
    #     serverAddress: http://prometheus.monitoring:9090
    #     metricName: selfhealing_redis_audit_buffer_size
    #     threshold: "10000"  # 버퍼 1만개 초과 시
    #     query: |
    #       sum(selfhealing_redis_audit_buffer_size{namespace="production"})
```

---

### 3.5 Django Deployment 수정

**파일**: `k8s/django-api-deployment.yaml` (기존 파일 수정)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: django-api
  namespace: production
spec:
  replicas: 2  # HPA에 의해 오버라이드됨
  selector:
    matchLabels:
      app: django-api
  template:
    metadata:
      labels:
        app: django-api
    spec:
      containers:
        - name: django-api
          image: myproject:latest
          resources:
            requests:
              cpu: "500m"      # HPA 계산에 사용
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"

          # Readiness Probe (스케일 아웃 시 트래픽 수신 전 체크)
          readinessProbe:
            httpGet:
              path: /health/ready/
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 5
            failureThreshold: 3

          # Liveness Probe
          livenessProbe:
            httpGet:
              path: /health/live/
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 10
            failureThreshold: 3

          # Lifecycle Hooks (Graceful Shutdown)
          lifecycle:
            preStop:
              exec:
                command:
                  - /bin/sh
                  - -c
                  - sleep 10  # 연결 드레이닝 대기
```

---

### 3.6 Celery Worker Deployment 수정

**파일**: `k8s/celery-default-worker.yaml` (기존 파일 수정)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: celery-default-worker
  namespace: production
spec:
  replicas: 2  # KEDA에 의해 오버라이드됨
  selector:
    matchLabels:
      app: celery-default-worker
  template:
    metadata:
      labels:
        app: celery-default-worker
    spec:
      terminationGracePeriodSeconds: 120  # 작업 완료 대기

      containers:
        - name: celery-worker
          image: myproject:latest
          command:
            - celery
            - -A
            - shopping
            - worker
            - --loglevel=info
            - --concurrency=4
            - --queues=celery
            - --prefetch-multiplier=4  # 미리 가져올 작업 수

          resources:
            requests:
              cpu: "250m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"

          # Liveness: 워커 프로세스 살아있는지
          livenessProbe:
            exec:
              command:
                - celery
                - -A
                - shopping
                - inspect
                - ping
                - --timeout
                - "10"
            initialDelaySeconds: 30
            periodSeconds: 60
            failureThreshold: 3

          # Graceful Shutdown
          lifecycle:
            preStop:
              exec:
                command:
                  - /bin/sh
                  - -c
                  - |
                    # 새 작업 수신 중단
                    celery -A shopping control cancel_consumer celery
                    # 진행 중인 작업 완료 대기
                    sleep 60
```

---

### 3.7 Celery 안전한 종료 설정 (신규)

> ⚠️ **리뷰 반영**: `task_reject_on_worker_lost` 설정으로 At-least-once 보장

**코드 근거:**

```python
# tests/self_healing/django/test_retry_configuration.py (Lines 46-48)
def test_acks_late_enabled(self):
    """acks_late가 활성화되어 있는지"""
    assert call_toss_confirm_api.acks_late is True

# packages/selfhealing-python/src/selfhealing/services/idempotency_service.py (Lines 42-76)
class IdempotencyDomain(Enum):
    EXTERNAL_SERVICE = "external_service"
    ASYNC_TASK = "async_task"
    # ... IdempotencyService 구현 완료
```

**현재 누락된 설정:**

```python
# myproject/celery.py에 task_reject_on_worker_lost 없음
```

**추가 필요 (myproject/celery.py):**

```python
# myproject/celery.py - app.conf.update() 블록에 추가
app.conf.update(
    # ... 기존 설정 ...

    # =========================================================================
    # Worker 비정상 종료 시 안전한 작업 재처리 (v1.1.0 추가)
    # =========================================================================
    # SIGKILL 등으로 Worker가 강제 종료되면 처리 중이던 작업을 큐로 반환
    # acks_late=True와 함께 At-least-once 전송 보장
    task_reject_on_worker_lost=True,

    # 작업 visibility timeout (Redis broker 전용)
    # 작업 처리 중 Worker 죽으면 이 시간 후 다른 Worker가 처리
    broker_transport_options={
        'visibility_timeout': 3600,  # 1시간 (긴 작업 고려)
    },
)
```

**멱등성 확인 체크리스트:**

| 태스크 | acks_late | IdempotencyService 연동 | 상태 |
|--------|-----------|------------------------|------|
| `call_toss_confirm_api` | ✅ | ✅ (76번 문서) | OK |
| `finalize_payment_confirm` | ✅ | ✅ | OK |
| `expire_points_task` | ✅ | ⚠️ 확인 필요 | - |
| `send_email_task` | ✅ | ✅ | OK |

---

### 3.8 RPS 기반 HPA (Prometheus Adapter) (신규)

> ⚠️ **리뷰 반영**: CPU는 사후 지표, RPS로 선제적 스케일링 필요

**Prometheus Adapter vs KEDA HTTP Scaler 비교:**

| 항목 | Prometheus Adapter | KEDA HTTP Scaler |
|------|-------------------|------------------|
| 성숙도 | ✅ 높음 (2018~) | ⚠️ 보통 (2021~) |
| 채택률 | ✅ 널리 사용 | ⚠️ 성장 중 |
| Scale to Zero | ❌ 미지원 | ✅ 지원 |
| 복잡도 | 중간 | 낮음 |
| Ingress 연동 | ✅ 네이티브 | ✅ 네이티브 |

**권장: Prometheus Adapter** (성숙도 및 채택률 우선)

**Prometheus Adapter 설치:**

```bash
# Helm으로 설치
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install prometheus-adapter prometheus-community/prometheus-adapter \
  --namespace monitoring \
  --set prometheus.url=http://prometheus.monitoring.svc.cluster.local \
  --set prometheus.port=9090
```

**Custom Metrics 설정:**

**파일**: `k8s/prometheus-adapter-config.yaml` (신규)

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: prometheus-adapter
  namespace: monitoring
data:
  config.yaml: |
    rules:
      # Django RPS (Requests Per Second) 메트릭
      - seriesQuery: 'django_http_requests_total{namespace!="",pod!=""}'
        resources:
          overrides:
            namespace: {resource: "namespace"}
            pod: {resource: "pod"}
        name:
          matches: "^(.*)_total$"
          as: "${1}_per_second"
        metricsQuery: 'sum(rate(<<.Series>>{<<.LabelMatchers>>}[2m])) by (<<.GroupBy>>)'

      # Nginx Ingress RPS (대안)
      - seriesQuery: 'nginx_ingress_controller_requests{namespace!=""}'
        resources:
          overrides:
            namespace: {resource: "namespace"}
            ingress: {resource: "ingress"}
        name:
          as: "requests_per_second"
        metricsQuery: 'sum(rate(<<.Series>>{<<.LabelMatchers>>}[2m])) by (<<.GroupBy>>)'
```

**Django HPA에 RPS 지표 추가:**

```yaml
# k8s/django-api-hpa.yaml 수정
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: django-api-hpa
  namespace: production
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: django-api

  minReplicas: 2
  maxReplicas: 20

  metrics:
    # CPU 사용률 기반 (기존)
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70

    # RPS 기반 (신규 - Prometheus Adapter 필요)
    - type: Pods
      pods:
        metric:
          name: django_http_requests_per_second
        target:
          type: AverageValue
          averageValue: "100"  # Pod당 100 RPS 초과 시 스케일 아웃

  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
        - type: Percent
          value: 100
          periodSeconds: 30
        - type: Pods
          value: 4
          periodSeconds: 30
      selectPolicy: Max
    scaleDown:
      stabilizationWindowSeconds: 300
      policies:
        - type: Percent
          value: 50
          periodSeconds: 60
      selectPolicy: Max
```

---

## 4. KEDA 설치

### 4.1 Helm으로 설치

```bash
# KEDA Helm repo 추가
helm repo add kedacore https://kedacore.github.io/charts
helm repo update

# KEDA 설치
helm install keda kedacore/keda \
  --namespace keda \
  --create-namespace \
  --set prometheus.metricServer.enabled=true \
  --set prometheus.metricServer.port=9022
```

### 4.2 확인

```bash
kubectl get pods -n keda
# keda-operator-...
# keda-metrics-apiserver-...
```

---

## 5. 모니터링 대시보드

### 5.1 Grafana Dashboard JSON

**파일**: `docker/grafana/dashboards/kubernetes-hpa.json` (신규)

```json
{
  "title": "Kubernetes HPA Monitoring",
  "panels": [
    {
      "title": "Django API Replicas",
      "type": "graph",
      "targets": [
        {
          "expr": "kube_hpa_status_current_replicas{hpa=\"django-api-hpa\"}",
          "legendFormat": "Current"
        },
        {
          "expr": "kube_hpa_status_desired_replicas{hpa=\"django-api-hpa\"}",
          "legendFormat": "Desired"
        }
      ]
    },
    {
      "title": "Celery Worker Replicas",
      "type": "graph",
      "targets": [
        {
          "expr": "kube_hpa_status_current_replicas{hpa=~\"celery-.*\"}",
          "legendFormat": "{{ hpa }}"
        }
      ]
    },
    {
      "title": "Redis Queue Lengths",
      "type": "graph",
      "targets": [
        {
          "expr": "redis_list_length{key=~\"celery|critical|audit_flush\"}",
          "legendFormat": "{{ key }}"
        }
      ]
    },
    {
      "title": "CPU Utilization by Pod",
      "type": "graph",
      "targets": [
        {
          "expr": "rate(container_cpu_usage_seconds_total{pod=~\"django-api-.*|celery-.*\"}[5m]) * 100",
          "legendFormat": "{{ pod }}"
        }
      ]
    }
  ]
}
```

---

### 5.2 Redis 폴링 부하 모니터링 (신규)

> ⚠️ **리뷰 반영**: KEDA 폴링이 Redis에 미치는 부하 모니터링 필요

**코드 근거:**

```yaml
# docker/prometheus/prometheus.yml (Lines 61-65)
- job_name: 'redis'
  static_configs:
    - targets: ['redis:9121']
  metrics_path: /metrics
```

**추가 알림 규칙:**

**파일**: `scripts/prometheus/self_healing_alerts.yml`에 추가

```yaml
  - name: self_healing_redis_load
    rules:
      # Redis Ops/Sec 급증 (KEDA 폴링 영향)
      - alert: RedisOpsPerSecSpike
        expr: |
          redis_instantaneous_ops_per_sec
          > 3 * avg_over_time(redis_instantaneous_ops_per_sec[1h])
        for: 5m
        labels:
          severity: warning
          team: ops
        annotations:
          summary: "Redis ops/sec spike detected"
          description: |
            Current: {{ $value | printf "%.0f" }} ops/sec
            This may indicate KEDA polling impact.
            Consider increasing pollingInterval.
          runbook_url: "https://docs.internal/runbooks/redis-ops-spike"

      # Redis CPU 사용률 높음
      - alert: RedisCPUHigh
        expr: redis_used_cpu_sys > 0.5
        for: 5m
        labels:
          severity: warning
          team: ops
        annotations:
          summary: "Redis CPU usage high ({{ $value | printf \"%.1f\" }})"
          description: |
            Redis system CPU usage exceeds 50%.
            Check for excessive polling or connection storms.
          runbook_url: "https://docs.internal/runbooks/redis-cpu-high"

      # Redis 연결 수 급증
      - alert: RedisConnectionsSpike
        expr: redis_connected_clients > 200
        for: 2m
        labels:
          severity: warning
          team: ops
        annotations:
          summary: "Redis connections spike: {{ $value }}"
          description: |
            Too many Redis connections.
            May indicate Pod scaling without proper connection pooling.
```

**KEDA 폴링 간격 튜닝 가이드:**

| 서비스 중요도 | pollingInterval | 권장 상황 |
|--------------|-----------------|----------|
| Critical (P0) | 5-10초 | 결제, 복구 태스크 |
| High (P1) | 15초 | 주문 처리 |
| Normal (P2) | 30초 | 이메일, 알림 |
| Low (P3) | 60초 | 정리 작업, 통계 |

```yaml
# KEDA ScaledObject 예시 - 폴링 최적화
spec:
  pollingInterval: 30  # 기본 30초 권장 (5초는 너무 공격적)
  cooldownPeriod: 120  # 2분 쿨다운
```

---

## 6. 테스트 계획

### 6.1 부하 테스트

```bash
# k6 부하 테스트
k6 run --vus 100 --duration 5m load_tests/api_stress.js

# 동시에 모니터링
kubectl get hpa -w
kubectl get pods -w
```

### 6.2 스케일 아웃 확인

```bash
# Redis 큐에 대량 작업 추가
python -c "
from shopping.tasks import process_order
for i in range(1000):
    process_order.delay(order_id=i)
"

# HPA 상태 확인
kubectl describe hpa celery-default-worker-hpa
kubectl describe scaledobject celery-default-worker-scaledobject
```

### 6.3 스케일 다운 확인

```bash
# 트래픽 중단 후 5분 대기
# Pod 수 감소 확인
kubectl get pods -l app=django-api -w
```

---

### 6.4 HPA 플래핑 방지 테스트 (신규)

> ⚠️ **리뷰 반영**: 계단식 부하 테스트로 플래핑 검증 필요

**코드 근거:**

```python
# packages/selfhealing-python/src/selfhealing/services/chaos/synthetic_load.py (Lines 48-52)
class LoadPattern(str, Enum):
    RAMP_UP = "ramp_up"      # 점진적 부하 증가
    RAMP_DOWN = "ramp_down"  # 점진적 부하 감소
    SPIKE = "spike"          # 급격한 부하 급증
    WAVE = "wave"            # 주기적 부하 패턴

# packages/selfhealing-python/src/selfhealing/settings/anti_flapping.py (Lines 52-79)
level_cooldown_seconds: int = Field(default=300, ...)
max_level_transitions_per_hour: int = Field(default=3, ...)
```

**테스트 시나리오:**

**파일**: `load_tests/scenarios/hpa/test_hpa_flapping.py` (신규)

```python
"""
HPA 플래핑 방지 테스트

목표: Pod가 급격히 생성/삭제되지 않고 부드럽게 스케일링되는지 검증
"""
import time
from locust import HttpUser, task, between
from locust import LoadTestShape


class StepLoadShape(LoadTestShape):
    """
    계단식 부하 패턴 (Step Load Pattern)

    1. 2분: 10 users (baseline)
    2. 2분: 50 users (step up)
    3. 2분: 100 users (peak)
    4. 2분: 50 users (step down)
    5. 2분: 10 users (return to baseline)
    """

    stages = [
        {"duration": 120, "users": 10, "spawn_rate": 5},   # 0-2분
        {"duration": 240, "users": 50, "spawn_rate": 10},  # 2-4분
        {"duration": 360, "users": 100, "spawn_rate": 20}, # 4-6분
        {"duration": 480, "users": 50, "spawn_rate": 10},  # 6-8분
        {"duration": 600, "users": 10, "spawn_rate": 5},   # 8-10분
    ]

    def tick(self):
        run_time = self.get_run_time()

        for stage in self.stages:
            if run_time < stage["duration"]:
                return (stage["users"], stage["spawn_rate"])

        return None  # 테스트 종료


class HPATestUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task(10)
    def health_check(self):
        self.client.get("/health/ready/")

    @task(5)
    def api_call(self):
        self.client.get("/api/products/")


# 검증 기준
FLAPPING_CRITERIA = {
    "max_scale_events_per_5min": 2,  # 5분당 최대 스케일 이벤트
    "min_pod_lifetime_seconds": 60,   # Pod 최소 생존 시간
    "stabilization_window": 300,      # 5분 안정화 윈도우
}
```

**실행 및 검증:**

```bash
# 테스트 실행
locust -f load_tests/scenarios/hpa/test_hpa_flapping.py \
  --host=http://django-api.production \
  --headless \
  --run-time=10m

# 동시에 HPA 이벤트 모니터링
kubectl get events --watch \
  --field-selector reason=SuccessfulRescale \
  -n production

# Pod 생성/삭제 빈도 확인
kubectl get pods -l app=django-api -w
```

**검증 쿼리 (Prometheus):**

```promql
# 5분간 스케일 이벤트 횟수 (2회 이하여야 정상)
increase(kube_hpa_status_current_replicas{hpa="django-api-hpa"}[5m])

# Pod 평균 생존 시간 (60초 이상이어야 정상)
avg(time() - kube_pod_start_time{pod=~"django-api-.*"})
```

---

## 7. 환경 변수 (KEDA 관련)

| 변수명 | 기본값 | 설명 |
|-------|-------|------|
| `KEDA_REDIS_ADDRESS` | - | Redis 주소 |
| `KEDA_POLLING_INTERVAL` | 15 | 메트릭 폴링 간격 (초) |
| `KEDA_COOLDOWN_PERIOD` | 60 | 스케일 다운 대기 (초) |

---

## 8. 체크리스트

### 8.1 인프라

- [ ] KEDA 설치
- [ ] Redis TriggerAuthentication 설정
- [ ] Prometheus Adapter 설치 (RPS HPA용)
- [ ] PgBouncer 검토 (선택)

### 8.2 Kubernetes 매니페스트

- [ ] `k8s/django-api-hpa.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-default.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-critical.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-audit.yaml` 생성
- [ ] `k8s/prometheus-adapter-config.yaml` 생성 (신규)
- [ ] 기존 Deployment에 resources 추가

### 8.3 코드 변경

- [ ] `myproject/celery.py`에 `task_reject_on_worker_lost=True` 추가
- [ ] `scripts/prometheus/self_healing_alerts.yml`에 Redis 알림 추가

### 8.4 모니터링

- [ ] Grafana 대시보드 추가
- [ ] 스케일 이벤트 알림 설정
- [ ] Redis 부하 알림 설정 (신규)

### 8.5 테스트

- [ ] 부하 테스트로 스케일 아웃 확인
- [ ] 스케일 다운 동작 확인
- [ ] Graceful Shutdown 확인
- [ ] HPA 플래핑 테스트 (신규)

---

## 9. 예상 효과

| 지표 | Before | After |
|-----|--------|-------|
| Django 최대 처리량 | 고정 (2 pods) | 자동 확장 (20 pods) |
| Celery 큐 지연 | 누적 가능 | 자동 해소 |
| 비용 효율 | 피크 기준 고정 | 사용량 기반 |
| 운영 부담 | 수동 스케일링 | 자동 |
| DB 커넥션 안정성 | 미계산 | 계산식 기반 (v1.1.0) |
| 작업 재처리 보장 | acks_late만 | At-least-once (v1.1.0) |

---

## 10. Appendix: Prometheus Adapter vs KEDA 비교

### 10.1 권장 조합

```
┌─────────────────────────────────────────────────────────────┐
│                    권장 아키텍처                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Django API          →  Prometheus Adapter + HPA           │
│   (CPU + RPS 지표)        (더 성숙, 널리 사용)               │
│                                                              │
│   Celery Workers      →  KEDA + ScaledObject                │
│   (Redis 큐 길이)         (이벤트 기반에 적합)               │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 10.2 상세 비교

| 항목 | Prometheus Adapter | KEDA |
|------|-------------------|------|
| **GitHub Stars** | ~1.8k | ~8k |
| **CNCF Status** | Incubating (간접) | Graduated |
| **첫 릴리즈** | 2018 | 2019 |
| **주요 사용처** | CPU/Memory + Custom Metrics | Event-driven 워크로드 |
| **Scale to Zero** | ❌ | ✅ |
| **Prometheus 필수** | ✅ | ❌ (선택) |
| **Redis 직접 연동** | ❌ (쿼리 필요) | ✅ (네이티브) |
| **Kafka 직접 연동** | ❌ | ✅ |
| **설정 복잡도** | 중간 | 낮음 |
| **커뮤니티 지원** | 활발 | 매우 활발 |

### 10.3 결론

- **Django API**: **Prometheus Adapter** 권장
  - CPU + RPS 조합으로 선제적 스케일링
  - HPA v2 네이티브 통합

- **Celery Workers**: **KEDA** 권장
  - Redis 큐 길이 직접 모니터링
  - Event-driven 워크로드에 최적화

---

## 11. 다음 단계

→ [172_OBSERVABILITY_METRICS.md](172_OBSERVABILITY_METRICS.md): 관측성 메트릭 강화 (선택)

또는 Phase 1 완료 후:
→ 실제 구현 및 부하 테스트

---

## 12. 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2026-01-31 | 초기 작성 |
| 1.1.0 | 2026-02-03 | 리뷰 반영: DB 커넥션 계산, task_reject_on_worker_lost, Redis 모니터링, RPS HPA, 플래핑 테스트 |

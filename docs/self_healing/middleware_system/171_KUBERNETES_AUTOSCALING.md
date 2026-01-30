# 171. Kubernetes Autoscaling 구현

> **버전**: 1.0.0
> **작성일**: 2026-01-31
> **의존성**: [170_KAFKA_AUDIT_ADAPTER.md](170_KAFKA_AUDIT_ADAPTER.md)
> **예상 소요**: 2-3일

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
- [ ] Prometheus 연동 (선택)

### 8.2 Kubernetes 매니페스트

- [ ] `k8s/django-api-hpa.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-default.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-critical.yaml` 생성
- [ ] `k8s/keda-scaledobject-celery-audit.yaml` 생성
- [ ] 기존 Deployment에 resources 추가

### 8.3 모니터링

- [ ] Grafana 대시보드 추가
- [ ] 스케일 이벤트 알림 설정

### 8.4 테스트

- [ ] 부하 테스트로 스케일 아웃 확인
- [ ] 스케일 다운 동작 확인
- [ ] Graceful Shutdown 확인

---

## 9. 예상 효과

| 지표 | Before | After |
|-----|--------|-------|
| Django 최대 처리량 | 고정 (2 pods) | 자동 확장 (20 pods) |
| Celery 큐 지연 | 누적 가능 | 자동 해소 |
| 비용 효율 | 피크 기준 고정 | 사용량 기반 |
| 운영 부담 | 수동 스케일링 | 자동 |

---

## 10. 다음 단계

→ [172_OBSERVABILITY_METRICS.md](172_OBSERVABILITY_METRICS.md): 관측성 메트릭 강화 (선택)

또는 Phase 1 완료 후:
→ 실제 구현 및 부하 테스트

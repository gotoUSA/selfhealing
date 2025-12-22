# Audit Logging Resilience 가이드

## 개요

감사 로깅의 핵심 원칙: **"감사가 비즈니스를 방해하지 않는다"**

감사 로깅 시스템은 엔터프라이즈급 복원력 패턴을 구현하여:
- 느린 백엔드가 메인 서비스를 차단하지 않도록 함
- 외부에서 감사 실패를 모니터링할 수 있음
- 중요 보안 이벤트는 OS 레벨에서 백업
- 장애 시 자동으로 폴백 모드로 전환

## 아키텍처

```
                    ┌─────────────────────────────────────────┐
                    │          CompositeBackend               │
                    │   ┌─────────────────────────────────┐   │
                    │   │      Circuit Breaker Layer      │   │
                    │   │  ┌─────────┐ ┌─────────┐        │   │
                    │   │  │CloudWatch│ │Datadog │  ...   │   │
                    │   │  └────┬────┘ └────┬────┘        │   │
                    │   └───────┼───────────┼─────────────┘   │
                    └───────────┼───────────┼─────────────────┘
                                │           │
                    ┌───────────▼───────────▼─────────────────┐
                    │         AuditMetrics (Prometheus)        │
                    │   audit_write_total, audit_failure_total │
                    │   audit_circuit_state, audit_degraded    │
                    └─────────────────────────────────────────┘
                                        │
                    ┌───────────────────▼─────────────────────┐
                    │          SyslogFallback                  │
                    │   (Critical Security Events to OS)       │
                    └─────────────────────────────────────────┘
                                        │
                    ┌───────────────────▼─────────────────────┐
                    │       DegradedModeManager               │
                    │   (Auto-switch to LocalFile on failure)  │
                    └─────────────────────────────────────────┘
```

## Circuit Breaker 패턴

### 상태 머신

```
    CLOSED ──(failures >= threshold)──> OPEN
       ▲                                  │
       │                           (timeout)
       │                                  ▼
       └──(successes >= threshold)── HALF_OPEN
                                          │
                                   (failure)
                                          │
                                          └──> OPEN
```

### 설정

```python
from selfhealing.audit import CircuitBreakerConfig, CircuitBreakerRegistry

# 기본 설정
config = CircuitBreakerConfig(
    failure_threshold=3,      # OPEN으로 전환하는 실패 횟수
    success_threshold=2,      # CLOSED로 복구하는 성공 횟수
    timeout_seconds=30.0,     # HALF_OPEN으로 전환하는 대기 시간
)

# 백엔드별 Circuit Breaker 가져오기
registry = CircuitBreakerRegistry()
cb = registry.get_or_create("cloudwatch", config)

# 수동 조작
cb.reset()                    # 강제 CLOSED
cb.force_open()               # 강제 OPEN

# 상태 확인
stats = cb.get_stats()
# {
#     "state": "CLOSED",
#     "failure_count": 0,
#     "success_count": 0,
#     "total_calls": 42,
#     "total_failures": 3,
#     "last_failure_time": null
# }
```

### CompositeBackend와 통합

```python
from selfhealing.audit import create_composite_backend

# Circuit Breaker 활성화 (기본값: True)
backend = create_composite_backend(
    enable_local=True,
    enable_cloudwatch=True,
    enable_datadog=True,
    enable_circuit_breaker=True,
    enable_metrics=True,
)

# 각 백엔드는 자동으로 Circuit Breaker로 보호됨
backend.write(event)  # 실패한 백엔드는 자동으로 우회됨
```

## Prometheus 메트릭

### 사용 가능한 메트릭

| 메트릭 이름 | 유형 | 라벨 | 설명 |
|-------------|------|------|------|
| `audit_write_total` | Counter | backend, status | 백엔드별 쓰기 작업 횟수 |
| `audit_failure_total` | Counter | backend, error_type | 백엔드별 실패 횟수 및 유형 |
| `audit_circuit_state` | Gauge | backend | 서킷 브레이커 상태 (0=CLOSED, 1=OPEN, 2=HALF_OPEN) |
| `audit_degraded_mode` | Gauge | - | 저하 모드 여부 (0 또는 1) |

### 메트릭 조회

```python
from selfhealing.audit import get_audit_metrics

metrics = get_audit_metrics()

# Prometheus 형식 출력
print(metrics.prometheus_format())
# audit_write_total{backend="cloudwatch",status="success"} 142
# audit_write_total{backend="cloudwatch",status="failure"} 3
# audit_failure_total{backend="cloudwatch",error_type="timeout"} 2
# audit_failure_total{backend="cloudwatch",error_type="connection_error"} 1
# audit_circuit_state{backend="cloudwatch"} 0
# audit_degraded_mode 0

# 통계 조회
stats = metrics.get_stats()
# {
#     "total_writes": 145,
#     "total_failures": 3,
#     "backends": {
#         "cloudwatch": {"writes": 142, "failures": 3}
#     },
#     "write_duration_avg": 0.025,
#     "degraded_mode": False
# }
```

## Syslog Fallback

### 중요 이벤트 정의

다음 이벤트 유형은 자동으로 OS syslog에도 기록됩니다:

- `security_policy_change` - 보안 정책 변경
- `authentication_config_change` - 인증 설정 변경
- `encryption_key_rotation` - 암호화 키 교체
- `admin_permission_change` - 관리자 권한 변경
- `all_backends_failed` - 모든 백엔드 실패
- `mass_data_modification` - 대량 데이터 변경
- `security_violation` - 보안 위반 감지

### 사용법

```python
from selfhealing.audit import log_critical_to_syslog, get_syslog_fallback

# 자동 감지 및 로깅
log_critical_to_syslog({
    "config_type": "security_policy_change",
    "actor": "admin@example.com",
    "action": "update",
    "changes": {"mfa_required": True}
})

# 수동 로깅
syslog = get_syslog_fallback()
syslog.log_backend_failure("cloudwatch", "Connection timeout")
syslog.log_circuit_open("datadog")
```

### 플랫폼별 동작

| 플랫폼 | 동작 |
|--------|------|
| Linux | syslog (LOG_AUTH facility) |
| macOS | syslog (LOG_AUTH facility) |
| Windows | stderr (콘솔 출력) |

## Degraded Mode

### 개념

외부 백엔드(CloudWatch, Datadog, S3 등)가 모두 실패하면 자동으로 Degraded Mode로 전환:

1. **로컬 파일 백엔드만 사용** - 감사 로그 손실 방지
2. **stderr로도 출력** - 컨테이너 로그에서 확인 가능
3. **자동 복구** - 백엔드 복구 시 정상 모드로 전환

### 관리

```python
from selfhealing.audit import DegradedModeManager

manager = DegradedModeManager()

# 상태 확인
print(manager.is_degraded)  # False

# 수동 전환
manager.enter("CloudWatch circuit open")
print(manager.is_degraded)  # True
print(manager.reason)       # "CloudWatch circuit open"

# 강제 모드 설정
manager.force_degraded("유지보수 중")  # 강제 저하 모드
manager.force_normal()                  # 강제 정상 모드

# 상태 조회
status = manager.get_status()
# {
#     "is_degraded": False,
#     "reason": null,
#     "entered_at": null,
#     "force_mode": null,
#     "auto_recovery_enabled": True
# }
```

## REST API 엔드포인트

### URL 설정

```python
# urls.py
from selfhealing.audit.api import get_audit_resilience_urls

urlpatterns = [
    # ... 기존 URL 패턴들 ...
    *get_audit_resilience_urls(),  # /api/v1/audit/... 엔드포인트 추가
]
```

### 사용 가능한 엔드포인트

#### GET /api/v1/audit/health

감사 시스템 전체 상태 확인.

```json
{
    "status": "healthy",
    "backends": {
        "local_file": {"healthy": true, "message": "OK"},
        "cloudwatch": {"healthy": true, "message": "OK"}
    },
    "degraded_mode": false,
    "open_circuits": []
}
```

#### GET /api/v1/audit/metrics

Prometheus 형식 메트릭 반환.

```
audit_write_total{backend="local_file",status="success"} 1523
audit_write_total{backend="cloudwatch",status="success"} 1520
audit_failure_total{backend="cloudwatch",error_type="timeout"} 3
audit_circuit_state{backend="cloudwatch"} 0
audit_degraded_mode 0
```

#### GET /api/v1/audit/circuit-breakers

모든 서킷 브레이커 상태 조회.

```json
{
    "circuits": {
        "cloudwatch": {
            "state": "CLOSED",
            "failure_count": 0,
            "success_count": 0,
            "total_calls": 1520,
            "total_failures": 3
        }
    },
    "open_circuits": []
}
```

#### POST /api/v1/audit/circuit-breakers

서킷 브레이커 조작.

```json
// 특정 서킷 리셋
{"action": "reset", "circuit": "cloudwatch"}

// 모든 서킷 리셋
{"action": "reset_all"}

// 강제 OPEN
{"action": "force_open", "circuit": "cloudwatch"}
```

#### GET /api/v1/audit/degraded-mode

저하 모드 상태 조회.

```json
{
    "is_degraded": false,
    "reason": null,
    "entered_at": null,
    "force_mode": null,
    "auto_recovery_enabled": true
}
```

#### POST /api/v1/audit/degraded-mode

저하 모드 제어.

```json
// 강제 저하 모드
{"action": "force_degraded", "reason": "유지보수 중"}

// 강제 정상 모드
{"action": "force_normal"}

// 자동 모드 (기본)
{"action": "auto"}
```

## Grafana 대시보드 설정

### 추천 패널

```yaml
# Audit System Health Dashboard

panels:
  - title: "Audit Write Rate"
    type: graph
    query: "rate(audit_write_total[5m])"
    
  - title: "Failure Rate by Backend"
    type: graph
    query: "rate(audit_failure_total[5m])"
    
  - title: "Circuit Breaker States"
    type: stat
    query: "audit_circuit_state"
    mappings:
      0: "CLOSED (정상)"
      1: "OPEN (차단)"
      2: "HALF_OPEN (복구 중)"
      
  - title: "Degraded Mode"
    type: stat
    query: "audit_degraded_mode"
    thresholds:
      - value: 0
        color: green
        text: "정상"
      - value: 1
        color: red
        text: "저하 모드"
```

### 알림 규칙

```yaml
alerts:
  - name: AuditCircuitOpen
    condition: audit_circuit_state > 0
    for: 1m
    severity: warning
    message: "Audit circuit breaker opened for {{ $labels.backend }}"
    
  - name: AuditDegradedMode
    condition: audit_degraded_mode == 1
    for: 0s
    severity: critical
    message: "Audit system in degraded mode - only local logging active"
    
  - name: AuditHighFailureRate
    condition: rate(audit_failure_total[5m]) > 0.1
    for: 5m
    severity: warning
    message: "High audit failure rate detected"
```

## 베스트 프랙티스

### 1. 모니터링 설정

```python
# settings.py

AUDIT_SETTINGS = {
    # Circuit Breaker 설정
    "circuit_breaker": {
        "failure_threshold": 3,
        "success_threshold": 2,
        "timeout_seconds": 30,
    },
    
    # 메트릭 활성화
    "enable_metrics": True,
    
    # Syslog 활성화 (Linux/macOS)
    "enable_syslog": True,
}
```

### 2. Kubernetes 배포

```yaml
# deployment.yaml

apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: app
          # Prometheus 스크래핑용 포트
          ports:
            - containerPort: 8000
              name: http
          # 헬스체크
          livenessProbe:
            httpGet:
              path: /api/v1/audit/health
              port: 8000
            initialDelaySeconds: 10
            periodSeconds: 30
          # Prometheus 어노테이션
          annotations:
            prometheus.io/scrape: "true"
            prometheus.io/port: "8000"
            prometheus.io/path: "/api/v1/audit/metrics"
```

### 3. 장애 대응 절차

```markdown
## Circuit Breaker OPEN 발생 시

1. `/api/v1/audit/health` 확인
2. 해당 백엔드 서비스 상태 점검
3. 필요시 수동 리셋: POST /api/v1/audit/circuit-breakers
   {"action": "reset", "circuit": "cloudwatch"}
4. 복구 확인 후 모니터링

## Degraded Mode 진입 시

1. 즉시 알림 확인
2. 모든 외부 백엔드 상태 점검
3. 로컬 파일 로그 확인 (데이터 손실 없음 확인)
4. 백엔드 복구 후 자동 정상화 대기
5. 필요시 강제 정상 모드: POST /api/v1/audit/degraded-mode
   {"action": "force_normal"}
```

## 트러블슈팅

### 문제: Circuit Breaker가 계속 OPEN 상태

```python
# 1. 상태 확인
from selfhealing.audit import CircuitBreakerRegistry

registry = CircuitBreakerRegistry()
for name, cb in registry.circuits.items():
    print(f"{name}: {cb.get_stats()}")

# 2. 수동 리셋
cb = registry.get("cloudwatch")
if cb:
    cb.reset()

# 3. 타임아웃 조정 (더 빠른 복구)
from selfhealing.audit import CircuitBreakerConfig
config = CircuitBreakerConfig(timeout_seconds=10)
```

### 문제: 메트릭이 수집되지 않음

```python
# 1. 메트릭 활성화 확인
from selfhealing.audit import create_composite_backend

backend = create_composite_backend(
    enable_metrics=True  # 반드시 True
)

# 2. 메트릭 직접 확인
from selfhealing.audit import get_audit_metrics
metrics = get_audit_metrics()
print(metrics.prometheus_format())
```

### 문제: Syslog에 로그가 없음

```python
# 1. 플랫폼 확인 (Windows는 stderr 사용)
import platform
print(platform.system())  # Linux, Darwin, Windows

# 2. 이벤트 유형 확인
from selfhealing.audit import get_syslog_fallback
syslog = get_syslog_fallback()
print(syslog.CRITICAL_EVENTS)  # 중요 이벤트 목록
```

## 변경 이력

| 버전 | 날짜 | 변경 내용 |
|------|------|----------|
| 1.0.0 | 2025-01-xx | 최초 릴리즈 - Circuit Breaker, Metrics, Syslog Fallback, Degraded Mode |

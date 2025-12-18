# Phase 2: Agent Mode (선택적)

## ⚠️ 중요: 이 모드는 선택 사항입니다!

Agent Mode는 **고객이 직접 설치 및 실행**해야 하는 유일한 Phase 2 컴포넌트입니다.

| 컴포넌트 | 고객 설치 필요? | 비고 |
|---------|---------------|------|
| Cloud Dashboard | ❌ NO | 웹 브라우저로 접속만 |
| Webhook | ❌ NO | 우리 서버에서 발송 |
| **Agent Mode** | ✅ YES | Docker sidecar 실행 필요 |

---

## Agent Mode가 필요한 경우

### 1. 로그 파일 수집
- 앱이 stdout 대신 파일에 로그 기록
- `/var/log/app/*.log` 파일 모니터링

### 2. 시스템 메트릭 수집
- CPU, Memory, Disk 사용량
- Network I/O

### 3. 프로세스 모니터링
- 앱 프로세스 상태
- Restart 감지

### 4. Prometheus 메트릭 스크래핑
- 앱이 자체 `/metrics` 엔드포인트 노출 시
- Agent가 스크래핑하여 Cloud로 전송

---

## Agent Mode가 필요 없는 경우

✅ **대부분의 사용자는 Agent 없이도 충분합니다!**

Phase 1에서 구현된 HTTPExporter가:
- 에러 이벤트 전송
- Circuit Breaker 상태 전송
- DLQ 이벤트 전송
- Request 메트릭 전송

이 모든 것을 앱 내부에서 처리합니다.

---

## 설치 방법 (선택 시)

### Option 1: Docker Sidecar

```yaml
# docker-compose.yml
version: "3.8"

services:
  # 고객 앱
  app:
    build: .
    ports:
      - "8000:8000"
    volumes:
      - app-logs:/var/log/app
    environment:
      - SELFHEALING_API_KEY=sh_live_xxxxx
  
  # SelfHealing Agent (sidecar)
  selfhealing-agent:
    image: selfhealing/agent:latest
    environment:
      - SELFHEALING_API_KEY=sh_live_xxxxx
      - SELFHEALING_ENDPOINT=https://ingest.selfhealing.io
    volumes:
      - app-logs:/var/log/app:ro  # 읽기 전용
      - /var/run/docker.sock:/var/run/docker.sock:ro  # 컨테이너 상태 모니터링
    depends_on:
      - app

volumes:
  app-logs:
```

### Option 2: Kubernetes DaemonSet

```yaml
# selfhealing-agent-daemonset.yaml
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: selfhealing-agent
  namespace: monitoring
spec:
  selector:
    matchLabels:
      app: selfhealing-agent
  template:
    metadata:
      labels:
        app: selfhealing-agent
    spec:
      containers:
        - name: agent
          image: selfhealing/agent:latest
          env:
            - name: SELFHEALING_API_KEY
              valueFrom:
                secretKeyRef:
                  name: selfhealing-secrets
                  key: api-key
            - name: SELFHEALING_ENDPOINT
              value: "https://ingest.selfhealing.io"
          volumeMounts:
            - name: varlog
              mountPath: /var/log
              readOnly: true
            - name: containers
              mountPath: /var/lib/docker/containers
              readOnly: true
          resources:
            limits:
              memory: 200Mi
              cpu: 100m
      volumes:
        - name: varlog
          hostPath:
            path: /var/log
        - name: containers
          hostPath:
            path: /var/lib/docker/containers
```

### Option 3: 직접 실행 (Linux/Mac)

```bash
# 다운로드
curl -sSL https://get.selfhealing.io/agent | sh

# 설정
export SELFHEALING_API_KEY="sh_live_xxxxx"

# 실행
selfhealing-agent start \
  --log-paths "/var/log/app/*.log" \
  --metrics-endpoint "http://localhost:8000/metrics"
```

---

## Agent 설정

```yaml
# /etc/selfhealing/agent.yaml

# 인증
api_key: sh_live_xxxxx

# 데이터 전송
endpoint: https://ingest.selfhealing.io
batch_size: 100
flush_interval: 5s

# 로그 수집
logs:
  enabled: true
  paths:
    - /var/log/app/*.log
    - /var/log/nginx/error.log
  exclude_patterns:
    - "healthcheck"
    - "metrics"
  
  # 파싱 규칙
  parsers:
    - type: json
      path_pattern: "/var/log/app/*.json"
    - type: regex
      path_pattern: "/var/log/nginx/*.log"
      pattern: '^(?P<remote_addr>\S+) - (?P<remote_user>\S+) \[(?P<time_local>[^\]]+)\]'

# 시스템 메트릭
system:
  enabled: true
  interval: 10s
  collect:
    - cpu
    - memory
    - disk
    - network

# 프로세스 모니터링
processes:
  enabled: true
  watch:
    - name: "python"
      cmdline_pattern: "manage.py runserver"
    - name: "celery"
      cmdline_pattern: "celery.*worker"

# Prometheus 스크래핑
prometheus:
  enabled: true
  targets:
    - url: http://localhost:8000/metrics
      interval: 15s
    - url: http://localhost:8001/metrics
      interval: 15s
```

---

## 수집 데이터 유형

### 1. 로그 이벤트

```json
{
  "type": "log",
  "timestamp": "2025-12-18T10:30:00.123Z",
  "source": "/var/log/app/error.log",
  "level": "ERROR",
  "message": "ConnectionError: Connection refused",
  "fields": {
    "service": "payment",
    "trace_id": "abc123"
  }
}
```

### 2. 시스템 메트릭

```json
{
  "type": "system_metrics",
  "timestamp": "2025-12-18T10:30:00.000Z",
  "host": "app-server-01",
  "metrics": {
    "cpu_percent": 45.2,
    "memory_percent": 72.1,
    "memory_used_mb": 1452,
    "disk_percent": 65.3,
    "network_bytes_sent": 12345678,
    "network_bytes_recv": 87654321
  }
}
```

### 3. 프로세스 상태

```json
{
  "type": "process",
  "timestamp": "2025-12-18T10:30:00.000Z",
  "host": "app-server-01",
  "processes": [
    {
      "name": "python",
      "pid": 1234,
      "status": "running",
      "cpu_percent": 12.5,
      "memory_mb": 256,
      "uptime_seconds": 86400
    }
  ]
}
```

### 4. Prometheus 메트릭

```json
{
  "type": "prometheus",
  "timestamp": "2025-12-18T10:30:00.000Z",
  "target": "http://localhost:8000/metrics",
  "metrics": [
    {
      "name": "http_requests_total",
      "type": "counter",
      "value": 12345,
      "labels": {"method": "GET", "status": "200"}
    }
  ]
}
```

---

## Agent 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SelfHealing Agent                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Input Collectors                          │   │
│  │                                                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │   │
│  │  │ Log Tail │  │ System   │  │ Process  │  │ Prom     │   │   │
│  │  │ Collector│  │ Metrics  │  │ Monitor  │  │ Scraper  │   │   │
│  │  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘   │   │
│  │       │             │             │             │          │   │
│  └───────┼─────────────┼─────────────┼─────────────┼──────────┘   │
│          │             │             │             │               │
│          └─────────────┴──────┬──────┴─────────────┘               │
│                               │                                      │
│                               ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Processing Pipeline                       │   │
│  │                                                              │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐   │   │
│  │  │ Parser   │→ │ Filter   │→ │ Enricher │→ │ Buffer   │   │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘   │   │
│  │                                                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Output                                    │   │
│  │                                                              │   │
│  │  ┌────────────────────────────────────────────────────┐    │   │
│  │  │ HTTP Exporter → https://ingest.selfhealing.io      │    │   │
│  │  └────────────────────────────────────────────────────┘    │   │
│  │                                                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Agent 구현 (Go)

Agent는 **Go로 구현**하여 단일 바이너리 배포:

```go
// agent/main.go

package main

import (
	"context"
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"
)

type Config struct {
	APIKey        string        `yaml:"api_key"`
	Endpoint      string        `yaml:"endpoint"`
	BatchSize     int           `yaml:"batch_size"`
	FlushInterval time.Duration `yaml:"flush_interval"`
	Logs          LogConfig     `yaml:"logs"`
	System        SystemConfig  `yaml:"system"`
}

func main() {
	cfg := loadConfig()
	
	// Create collectors
	collectors := []Collector{
		NewLogCollector(cfg.Logs),
		NewSystemCollector(cfg.System),
		NewProcessCollector(cfg.Processes),
	}
	
	// Create pipeline
	pipeline := NewPipeline(
		NewParser(),
		NewFilter(cfg.Logs.ExcludePatterns),
		NewEnricher(),
		NewBuffer(cfg.BatchSize, cfg.FlushInterval),
	)
	
	// Create exporter
	exporter := NewHTTPExporter(cfg.Endpoint, cfg.APIKey)
	
	// Wire everything
	for _, c := range collectors {
		go func(collector Collector) {
			for event := range collector.Events() {
				processed := pipeline.Process(event)
				exporter.Export(processed)
			}
		}(c)
	}
	
	// Wait for shutdown
	sigCh := make(chan os.Signal, 1)
	signal.Notify(sigCh, syscall.SIGINT, syscall.SIGTERM)
	<-sigCh
	
	log.Println("Shutting down...")
	exporter.Flush()
}
```

---

## 리소스 사용량

Agent는 경량으로 설계:

| 리소스 | 제한 |
|--------|------|
| CPU | < 100m (0.1 core) |
| Memory | < 200MB |
| Disk I/O | 최소 (읽기만) |
| Network | < 1MB/min (일반적) |

---

## 비교: Agent Mode vs HTTPExporter

| 기능 | HTTPExporter (Phase 1) | Agent Mode (Phase 2) |
|------|------------------------|----------------------|
| 설치 | `pip install` | Docker 또는 binary |
| 코드 변경 | `selfhealing.init()` | 없음 |
| 에러 수집 | ✅ | ✅ |
| CB 상태 | ✅ | ✅ |
| 로그 파일 | ❌ | ✅ |
| 시스템 메트릭 | ❌ | ✅ |
| 프로세스 상태 | ❌ | ✅ |
| 권장 대상 | 대부분의 사용자 | 심층 모니터링 필요 시 |

---

## 결론

### Agent Mode를 사용해야 하는 경우:

1. **로그 파일 기반 모니터링** 필요
2. **시스템 메트릭** (CPU/Memory) 수집 필요
3. **Prometheus 메트릭 스크래핑** 필요
4. **앱 코드 변경 불가능**한 상황

### Agent Mode가 필요 없는 경우:

1. HTTPExporter로 충분한 경우 (대부분!)
2. 앱이 이미 APM 도구 사용 중
3. 시스템 메트릭을 별도 수집 중

---

## 다음 단계

← [00-OVERVIEW.md](00-OVERVIEW.md)로 돌아가기

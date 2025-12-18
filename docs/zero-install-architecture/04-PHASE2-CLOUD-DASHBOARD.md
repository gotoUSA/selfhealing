# Phase 2: Cloud Dashboard

## 목적

**우리 서버에서 운영**하는 웹 대시보드. 고객은 설치 불필요, 웹 접속만.

---

## 고객 경험

```
1. 고객: pip install selfhealing + init() 설정
2. 고객: 웹 브라우저로 https://dashboard.selfhealing.io 접속
3. 고객: API Key로 로그인
4. 끝! 실시간 모니터링 시작
```

**고객이 해야 할 일: 없음** (Phase 1만 설정하면 자동)

---

## 대시보드 기능

### 1. Overview (홈)

```
┌─────────────────────────────────────────────────────────────────────┐
│  SelfHealing Dashboard                           [payment-service] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌───────────┐ │
│  │ SERVICES    │  │ ERRORS      │  │ CB OPEN     │  │ DLQ       │ │
│  │      3      │  │    127      │  │      1      │  │    23     │ │
│  │  healthy    │  │  last 24h   │  │  service    │  │  pending  │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └───────────┘ │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Error Rate (24h)                                            │   │
│  │  ████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │   │
│  │  0.5%                                                        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │  Circuit Breakers                                            │   │
│  │  ┌─────────────────────────────────────────────────────┐    │   │
│  │  │ ● payment_api     CLOSED   0 failures               │    │   │
│  │  │ ● order_service   OPEN     5 failures   ⚠️          │    │   │
│  │  │ ● notification    CLOSED   2 failures               │    │   │
│  │  └─────────────────────────────────────────────────────┘    │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 2. Errors Page

```
┌─────────────────────────────────────────────────────────────────────┐
│  Errors                                    [Last 24h ▼] [Filter ▼] │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ConnectionError: Connection refused                          │   │
│  │ POST /api/payments/confirm                                   │   │
│  │ 47 occurrences | Last: 2 min ago | payment-service           │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ TimeoutError: Read timed out                                 │   │
│  │ GET /api/orders/123                                          │   │
│  │ 23 occurrences | Last: 5 min ago | order-service             │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ValueError: Invalid payment amount                           │   │
│  │ Task: shopping.tasks.process_payment                         │   │
│  │ 12 occurrences | Last: 10 min ago | payment-service          │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 3. Circuit Breakers Page

```
┌─────────────────────────────────────────────────────────────────────┐
│  Circuit Breakers                                                   │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ payment_api                                          CLOSED  │   │
│  │ ──────────────────────────────────────────────────────────── │   │
│  │ Failures: 0/5    Recovery: 60s    Last transition: 2h ago   │   │
│  │                                                              │   │
│  │ [Timeline Graph: CLOSED → OPEN → HALF_OPEN → CLOSED]        │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ order_service                                     ⚠️ OPEN   │   │
│  │ ──────────────────────────────────────────────────────────── │   │
│  │ Failures: 5/5    Recovery: in 45s   Opened: 15s ago         │   │
│  │                                                              │   │
│  │ Recent Errors:                                               │   │
│  │ - ConnectionError: Connection refused (5x)                   │   │
│  │                                                              │   │
│  │ [Force Close] [View Errors]                                  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 4. DLQ (Dead Letter Queue) Page

```
┌─────────────────────────────────────────────────────────────────────┐
│  Dead Letter Queue                              [23 pending items]  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ [□] #1234 | payment | PG_TIMEOUT                            │   │
│  │     Order: #5678 | Amount: ₩50,000 | Created: 10 min ago    │   │
│  │     [Replay] [Review] [Reject]                               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ [□] #1235 | notification | SMTP_ERROR                       │   │
│  │     Email: user@example.com | Template: order_confirm       │   │
│  │     [Replay] [Review] [Reject]                               │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  [Replay Selected (0)] [Replay All Pending]                        │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

### 5. Settings Page

```
┌─────────────────────────────────────────────────────────────────────┐
│  Settings                                                           │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  API Keys                                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ sk_live_xxxx...xxxx (production)              [Regenerate]  │   │
│  │ sk_test_xxxx...xxxx (development)             [Regenerate]  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Alerting                                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Slack Webhook: https://hooks.slack.com/xxx   [✓] Enabled    │   │
│  │ Email: admin@company.com                     [✓] Enabled    │   │
│  │ Discord Webhook: (not configured)            [ ] Disabled   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  Circuit Breaker Defaults                                           │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Failure Threshold: [5]                                       │   │
│  │ Recovery Timeout:  [60] seconds                              │   │
│  │ Half-Open Limit:   [3] requests                              │   │
│  │                                              [Save Changes]  │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 기술 스택

| 컴포넌트 | 기술 | 이유 |
|---------|------|------|
| Frontend | React + TypeScript | 빠른 개발, 타입 안전 |
| UI Framework | Tailwind CSS | 빠른 스타일링 |
| Charts | Recharts | React 친화적 |
| Backend | FastAPI | 고성능, OpenAPI 자동 생성 |
| Database | PostgreSQL + TimescaleDB | 시계열 데이터 최적화 |
| Cache | Redis | 실시간 데이터 캐싱 |
| Auth | JWT + API Key | 간단하고 안전 |

---

## API 엔드포인트 (Cloud 서버)

### Events API (SDK → Cloud)

```
POST /api/v1/events
    - 이벤트 배치 수신
    
POST /api/v1/heartbeat
    - SDK heartbeat
```

### Dashboard API (Dashboard → Cloud)

```
GET  /api/v1/dashboard/overview
GET  /api/v1/dashboard/errors
GET  /api/v1/dashboard/circuit-breakers
GET  /api/v1/dashboard/dlq

POST /api/v1/circuit-breakers/{id}/force-close
POST /api/v1/circuit-breakers/{id}/force-open

POST /api/v1/dlq/{id}/replay
POST /api/v1/dlq/replay-batch
```

---

## 데이터 모델

### Service

```sql
CREATE TABLE services (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    environment VARCHAR(50) NOT NULL,
    last_seen_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Event

```sql
CREATE TABLE events (
    id BIGSERIAL,
    tenant_id UUID NOT NULL,
    service_id UUID NOT NULL,
    type VARCHAR(100) NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    
    PRIMARY KEY (id, timestamp)
) PARTITION BY RANGE (timestamp);

-- TimescaleDB hypertable
SELECT create_hypertable('events', 'timestamp');
```

### CircuitBreaker

```sql
CREATE TABLE circuit_breakers (
    id UUID PRIMARY KEY,
    tenant_id UUID NOT NULL,
    service_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    state VARCHAR(20) NOT NULL,
    failure_count INT DEFAULT 0,
    last_failure_at TIMESTAMP,
    opened_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);
```

---

## 실시간 업데이트

### WebSocket

```javascript
// Dashboard에서 실시간 업데이트 수신
const ws = new WebSocket('wss://api.selfhealing.io/ws');

ws.onmessage = (event) => {
    const data = JSON.parse(event.data);
    
    switch (data.type) {
        case 'circuit_breaker.opened':
            // CB OPEN 알림 표시
            showAlert('Circuit Breaker Opened', data.service);
            break;
        case 'error.new':
            // 새 에러 표시
            addError(data.error);
            break;
        case 'dlq.new':
            // DLQ 카운트 업데이트
            updateDLQCount(data.count);
            break;
    }
};
```

---

## 배포 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Kubernetes Cluster                            │
│                                                                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐     │
│  │ API Gateway     │  │ Event Ingestion │  │ Dashboard API   │     │
│  │ (NGINX)         │  │ Service         │  │ Service         │     │
│  │ - Rate Limit    │  │ - Kafka Writer  │  │ - Query         │     │
│  │ - Auth          │  │ - Validation    │  │ - WebSocket     │     │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘     │
│           │                    │                    │               │
│           ▼                    ▼                    ▼               │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Kafka / Redis Streams                     │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                │                                    │
│           ┌────────────────────┼────────────────────┐              │
│           ▼                    ▼                    ▼              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐   │
│  │ Analysis Worker │  │ Alerting Worker │  │ Aggregation     │   │
│  │ - Anomaly Detect│  │ - Slack/Email   │  │ Worker          │   │
│  │ - Pattern Match │  │ - Webhook       │  │ - Rollup        │   │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘   │
│                                │                                    │
│                                ▼                                    │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                PostgreSQL + TimescaleDB                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 비용 모델 (예시)

| 플랜 | 이벤트/월 | 보관 기간 | 가격 |
|------|----------|----------|------|
| Free | 10,000 | 7일 | $0 |
| Starter | 100,000 | 30일 | $29 |
| Pro | 1,000,000 | 90일 | $99 |
| Enterprise | Unlimited | 1년 | Custom |

---

## 다음 단계

→ [05-PHASE2-WEBHOOK.md](05-PHASE2-WEBHOOK.md)

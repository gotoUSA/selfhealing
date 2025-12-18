# Phase 2: Webhook Dispatcher

## 목적

**우리 서버에서 운영**하는 알림 시스템. 고객은 웹훅 URL만 등록하면 Slack/Discord/Email 알림 수신.

---

## 고객 경험

```
1. 고객: 대시보드 Settings에서 Slack Webhook URL 입력
2. 끝! 알림 자동 수신
```

**고객이 해야 할 일:**
- Slack/Discord에서 Webhook URL 생성
- 대시보드에 URL 붙여넣기
- 그 외 없음

---

## 지원 채널

| 채널 | 설정 방법 | 알림 형식 |
|------|----------|----------|
| **Slack** | Webhook URL | Rich message (블록) |
| **Discord** | Webhook URL | Embed message |
| **Email** | 이메일 주소 | HTML 이메일 |
| **PagerDuty** | Integration Key | Incident |
| **OpsGenie** | API Key | Alert |
| **Custom Webhook** | URL + Headers | JSON POST |

---

## 알림 유형

### 1. Circuit Breaker Alerts

```
┌─────────────────────────────────────────────────────────────┐
│ 🔴 Circuit Breaker OPENED                                   │
├─────────────────────────────────────────────────────────────┤
│ Service: payment_api                                        │
│ Environment: production                                     │
│ Failures: 5/5                                               │
│ Last Error: ConnectionError: Connection refused             │
│                                                             │
│ Opened at: 2025-12-18 10:30:00 UTC                         │
│                                                             │
│ [View in Dashboard]                                         │
└─────────────────────────────────────────────────────────────┘
```

### 2. Error Spike Alerts

```
┌─────────────────────────────────────────────────────────────┐
│ ⚠️ Error Spike Detected                                     │
├─────────────────────────────────────────────────────────────┤
│ Service: order-service                                      │
│ Error Rate: 15.3% (threshold: 5%)                          │
│                                                             │
│ Top Errors:                                                 │
│ • TimeoutError (45 occurrences)                            │
│ • ConnectionError (23 occurrences)                          │
│                                                             │
│ [View Details]                                              │
└─────────────────────────────────────────────────────────────┘
```

### 3. DLQ Alerts

```
┌─────────────────────────────────────────────────────────────┐
│ 📥 DLQ Items Pending                                        │
├─────────────────────────────────────────────────────────────┤
│ 23 items pending review                                     │
│                                                             │
│ Breakdown:                                                  │
│ • payment: 15 items                                         │
│ • notification: 8 items                                     │
│                                                             │
│ Oldest item: 2 hours ago                                    │
│                                                             │
│ [Review in Dashboard]                                       │
└─────────────────────────────────────────────────────────────┘
```

### 4. Recovery Alerts

```
┌─────────────────────────────────────────────────────────────┐
│ ✅ Circuit Breaker RECOVERED                                │
├─────────────────────────────────────────────────────────────┤
│ Service: payment_api                                        │
│ State: CLOSED                                               │
│ Downtime: 3m 45s                                            │
│                                                             │
│ Recovered at: 2025-12-18 10:33:45 UTC                      │
└─────────────────────────────────────────────────────────────┘
```

---

## 알림 규칙 설정

### Dashboard UI

```
┌─────────────────────────────────────────────────────────────────────┐
│  Alert Rules                                            [+ Add Rule]│
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Rule: Circuit Breaker Opens                                  │   │
│  │ ──────────────────────────────────────────────────────────── │   │
│  │ Condition: circuit_breaker.state = "open"                    │   │
│  │ Channels: Slack (#alerts), Email (admin@company.com)         │   │
│  │ Cooldown: 5 minutes                                          │   │
│  │                                                    [Edit] [✓]│   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Rule: Error Rate > 5%                                        │   │
│  │ ──────────────────────────────────────────────────────────── │   │
│  │ Condition: error_rate > 5% for 5 minutes                     │   │
│  │ Channels: Slack (#alerts)                                    │   │
│  │ Cooldown: 15 minutes                                         │   │
│  │                                                    [Edit] [✓]│   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ Rule: DLQ Items > 10                                         │   │
│  │ ──────────────────────────────────────────────────────────── │   │
│  │ Condition: dlq.pending_count > 10                            │   │
│  │ Channels: Email (admin@company.com)                          │   │
│  │ Cooldown: 1 hour                                             │   │
│  │                                                    [Edit] [✓]│   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SelfHealing Cloud                                 │
│                                                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Event Stream (Kafka)                      │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Alert Evaluator                           │   │
│  │  - 규칙 매칭                                                 │   │
│  │  - 조건 평가                                                 │   │
│  │  - 쿨다운 체크                                               │   │
│  │  - 집계 (동일 알림 그룹화)                                   │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│                               ▼                                      │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    Alert Queue (Redis)                       │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                               │                                      │
│         ┌─────────────────────┼─────────────────────┐               │
│         ▼                     ▼                     ▼               │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐        │
│  │ Slack       │      │ Discord     │      │ Email       │        │
│  │ Sender      │      │ Sender      │      │ Sender      │        │
│  └──────┬──────┘      └──────┬──────┘      └──────┬──────┘        │
│         │                    │                    │                 │
└─────────┼────────────────────┼────────────────────┼─────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
   ┌─────────────┐      ┌─────────────┐      ┌─────────────┐
   │ Slack API   │      │ Discord API │      │ SMTP Server │
   └─────────────┘      └─────────────┘      └─────────────┘
```

---

## Slack 메시지 형식

```json
{
  "blocks": [
    {
      "type": "header",
      "text": {
        "type": "plain_text",
        "text": "🔴 Circuit Breaker OPENED",
        "emoji": true
      }
    },
    {
      "type": "section",
      "fields": [
        {
          "type": "mrkdwn",
          "text": "*Service:*\npayment_api"
        },
        {
          "type": "mrkdwn",
          "text": "*Environment:*\nproduction"
        },
        {
          "type": "mrkdwn",
          "text": "*Failures:*\n5/5"
        },
        {
          "type": "mrkdwn",
          "text": "*Opened at:*\n2025-12-18 10:30 UTC"
        }
      ]
    },
    {
      "type": "section",
      "text": {
        "type": "mrkdwn",
        "text": "*Last Error:*\n```ConnectionError: Connection refused```"
      }
    },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": {
            "type": "plain_text",
            "text": "View in Dashboard"
          },
          "url": "https://dashboard.selfhealing.io/cb/payment_api"
        }
      ]
    }
  ]
}
```

---

## Discord Embed 형식

```json
{
  "embeds": [
    {
      "title": "🔴 Circuit Breaker OPENED",
      "color": 16711680,
      "fields": [
        {
          "name": "Service",
          "value": "payment_api",
          "inline": true
        },
        {
          "name": "Environment",
          "value": "production",
          "inline": true
        },
        {
          "name": "Failures",
          "value": "5/5",
          "inline": true
        },
        {
          "name": "Last Error",
          "value": "```ConnectionError: Connection refused```",
          "inline": false
        }
      ],
      "timestamp": "2025-12-18T10:30:00.000Z"
    }
  ]
}
```

---

## 쿨다운 & 집계

### 쿨다운 (Cooldown)

동일 알림이 반복되지 않도록 방지:

```python
# 쿨다운 설정
alert_rule = {
    "id": "cb_open",
    "cooldown": timedelta(minutes=5),  # 5분간 동일 알림 방지
}

# 쿨다운 체크
def should_send_alert(rule_id: str, service: str) -> bool:
    key = f"cooldown:{rule_id}:{service}"
    if redis.exists(key):
        return False
    redis.setex(key, rule.cooldown, "1")
    return True
```

### 집계 (Aggregation)

짧은 시간 내 다수 이벤트를 하나로 묶음:

```python
# 5초간 이벤트 집계
aggregation_window = timedelta(seconds=5)

# 집계 결과
{
    "type": "error_batch",
    "count": 47,
    "first_at": "2025-12-18T10:30:00Z",
    "last_at": "2025-12-18T10:30:05Z",
    "errors": [
        {"type": "ConnectionError", "count": 35},
        {"type": "TimeoutError", "count": 12},
    ]
}
```

---

## 구현 (Alert Worker)

```python
"""
alerting/worker.py
"""

import asyncio
import aiohttp
from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass
class AlertChannel:
    type: str  # "slack", "discord", "email"
    config: Dict[str, str]


@dataclass
class AlertRule:
    id: str
    condition: str
    channels: List[AlertChannel]
    cooldown_seconds: int = 300


class AlertWorker:
    """알림 워커"""
    
    def __init__(self, redis_client, rule_store):
        self.redis = redis_client
        self.rules = rule_store
        self.senders = {
            "slack": SlackSender(),
            "discord": DiscordSender(),
            "email": EmailSender(),
        }
    
    async def process_event(self, event: Dict[str, Any]) -> None:
        """이벤트 처리 및 알림 발송"""
        tenant_id = event.get("tenant_id")
        
        # 테넌트의 알림 규칙 조회
        rules = await self.rules.get_rules(tenant_id)
        
        for rule in rules:
            # 조건 평가
            if not self._evaluate_condition(rule.condition, event):
                continue
            
            # 쿨다운 체크
            if not await self._check_cooldown(rule, event):
                continue
            
            # 알림 발송
            for channel in rule.channels:
                await self._send_alert(channel, event)
    
    def _evaluate_condition(self, condition: str, event: Dict) -> bool:
        """조건 평가"""
        # 간단한 조건 파싱
        if condition == "circuit_breaker.opened":
            return event.get("type") == "circuit_breaker.opened"
        
        if condition.startswith("error_rate >"):
            threshold = float(condition.split(">")[1].strip().rstrip("%"))
            return event.get("error_rate", 0) > threshold
        
        return False
    
    async def _check_cooldown(self, rule: AlertRule, event: Dict) -> bool:
        """쿨다운 체크"""
        key = f"cooldown:{rule.id}:{event.get('service')}"
        
        if await self.redis.exists(key):
            return False
        
        await self.redis.setex(key, rule.cooldown_seconds, "1")
        return True
    
    async def _send_alert(self, channel: AlertChannel, event: Dict) -> None:
        """알림 발송"""
        sender = self.senders.get(channel.type)
        if sender:
            await sender.send(channel.config, event)


class SlackSender:
    """Slack 알림 발송"""
    
    async def send(self, config: Dict, event: Dict) -> None:
        webhook_url = config.get("webhook_url")
        
        payload = self._build_message(event)
        
        async with aiohttp.ClientSession() as session:
            await session.post(webhook_url, json=payload)
    
    def _build_message(self, event: Dict) -> Dict:
        """Slack 메시지 구성"""
        event_type = event.get("type", "unknown")
        
        if event_type == "circuit_breaker.opened":
            return self._build_cb_open_message(event)
        elif event_type == "error_spike":
            return self._build_error_spike_message(event)
        else:
            return self._build_generic_message(event)
    
    def _build_cb_open_message(self, event: Dict) -> Dict:
        return {
            "blocks": [
                {
                    "type": "header",
                    "text": {
                        "type": "plain_text",
                        "text": "🔴 Circuit Breaker OPENED",
                        "emoji": True
                    }
                },
                {
                    "type": "section",
                    "fields": [
                        {"type": "mrkdwn", "text": f"*Service:*\n{event.get('service')}"},
                        {"type": "mrkdwn", "text": f"*Environment:*\n{event.get('environment')}"},
                    ]
                }
            ]
        }
```

---

## 다음 단계

→ [06-PHASE2-AGENT-MODE.md](06-PHASE2-AGENT-MODE.md)

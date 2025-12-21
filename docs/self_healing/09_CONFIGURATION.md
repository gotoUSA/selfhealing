# Self-Healing Configuration Reference

## 개요

Self-Healing 시스템의 모든 설정은 Django settings의 `SELF_HEALING` 딕셔너리를 통해 구성됩니다. 이 문서는 사용 가능한 모든 설정 옵션과 기본값, 환경별 권장 설정을 제공합니다.

### 설정 위치

- 설정 정의: `packages/selfhealing-python/src/selfhealing/core/config.py`
- 개발 환경: [myproject/settings/local.py](../../myproject/settings/local.py)
- 운영 환경: [myproject/settings/production.py](../../myproject/settings/production.py)
- 테스트 환경: [myproject/settings/test.py](../../myproject/settings/test.py)

---

## 설정 아키텍처

```
Django settings.py
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│                    SELF_HEALING = {                       │
│  ┌─────────┐ ┌─────────┐ ┌────────────────┐ ┌─────────┐ │
│  │   SLA   │ │  RETRY  │ │ CIRCUIT_BREAKER│ │   DLQ   │ │
│  └────┬────┘ └────┬────┘ └───────┬────────┘ └────┬────┘ │
│       └───────────┴──────────────┴───────────────┘      │
│                          │                               │
│  ┌───────────────┐ ┌─────────────┐ ┌──────────────────┐ │
│  │  IDEMPOTENCY  │ │  FORENSIC   │ │  NOTIFICATIONS   │ │
│  └───────────────┘ └─────────────┘ └──────────────────┘ │
│                          │                               │
│  ┌───────────────┐ ┌─────────────────┐                  │
│  │   SECURITY    │ │   GOVERNANCE    │                  │
│  └───────────────┘ └─────────────────┘                  │
└──────────────────────────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────┐
│            SelfHealingConfig.load()                       │
│                                                           │
│  Dataclass 기반 타입 안전한 설정 객체 생성               │
└──────────────────────────────────────────────────────────┘
```

---

## 설정 카테고리

### 1. SLA (Service Level Agreement)

도메인별 복구 시간 SLA를 정의합니다. 이 시간 내에 해결되지 않으면 SLA 위반으로 기록됩니다.

```python
SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,        # 결제: 1시간 (Critical)
        "POINT_HOURS": 4,          # 포인트: 4시간 (Medium)
        "INVENTORY_HOURS": 2,      # 재고: 2시간 (High)
        "WEBHOOK_HOURS": 8,        # 웹훅: 8시간 (Low)
        "NOTIFICATION_HOURS": 24,  # 알림: 24시간 (Lowest)
        "DEFAULT_HOURS": 24,       # 기본값: 24시간
    },
}
```

**Dataclass: `SLAThresholds`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `payment_hours` | int | 1 | 결제 도메인 SLA (시간) |
| `point_hours` | int | 4 | 포인트 도메인 SLA (시간) |
| `inventory_hours` | int | 2 | 재고 도메인 SLA (시간) |
| `webhook_hours` | int | 8 | 웹훅 도메인 SLA (시간) |
| `notification_hours` | int | 24 | 알림 도메인 SLA (시간) |
| `default_hours` | int | 24 | 기본 SLA (시간) |

**사용 예시:**

```python
from selfhealing.core.config import get_sla_thresholds

sla = get_sla_thresholds()
payment_sla = sla.get_threshold("payment")  # timedelta(hours=1)
all_slas = sla.get_all_thresholds()  # dict of all domains
```

**런타임 동적 변경 (API):**

SLA 설정은 서버 재시작 없이 API를 통해 런타임에 변경할 수 있습니다.

```bash
# 현재 SLA 설정 조회
curl -X GET -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/sla/

# SLA 시간 변경 (즉시 적용)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "default_hours": 24,
    "thresholds_by_domain": {
      "payment": 2,
      "point": 8,
      "inventory": 4,
      "webhook": 12,
      "notification": 48
    }
  }' \
  $API_URL/api/self-healing/config/sla/

# 지연 적용 (60초 후 적용, 취소 가능)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "thresholds_by_domain": {"payment": 1},
    "apply_strategy": "delayed",
    "delay_seconds": 60
  }' \
  $API_URL/api/self-healing/config/sla/
```

> 💡 **도메인 중립적 설계**: `thresholds_by_domain`은 딕셔너리 형태로, 결제/포인트 외에도 어떤 도메인이든 자유롭게 추가/수정할 수 있습니다. 코드 변경 없이 새 도메인의 SLA를 정의할 수 있습니다.

---

### 2. RETRY (재시도 정책)

재시도 동작의 핵심 파라미터를 정의합니다.

```python
SELF_HEALING = {
    "RETRY": {
        "MAX_RETRIES": 3,       # 최대 재시도 횟수
        "BACKOFF_BASE": 4,      # 지수 백오프 베이스 (delay = base^attempt)
        "BACKOFF_MAX": 180,     # 최대 대기 시간 (초)
        "JITTER_PERCENT": 25,   # 지터 비율 (±25%)
        "MIN_DELAY": 1,         # 최소 대기 시간 (초)
    },
}
```

**Dataclass: `RetrySettings`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `max_retries` | int | 3 | 최대 재시도 횟수 |
| `backoff_base` | int | 4 | 지수 백오프 베이스 |
| `backoff_max` | int | 180 | 최대 대기 시간 (초) |
| `jitter_percent` | int | 25 | 지터 비율 (%) |
| `min_delay` | int | 1 | 최소 대기 시간 (초) |

**지연 시간 계산:**

```
delay = min(base^attempt, max_delay) × (1 ± jitter)

예시 (base=4, max=180, jitter=25%):
- 시도 1: 4^1 = 4초 → 3~5초
- 시도 2: 4^2 = 16초 → 12~20초
- 시도 3: 4^3 = 64초 → 48~80초
- 시도 4: 4^4 = 256초 → cap to 135~225초
```

**사용 예시:**

```python
from selfhealing.core.config import get_retry_settings

retry = get_retry_settings()
print(f"Max attempts: {retry.max_attempts}")
print(f"Backoff formula: {retry.backoff_base}^n with max {retry.backoff_max}s")
```

---

### 3. CIRCUIT_BREAKER (서킷 브레이커)

서킷 브레이커 동작을 제어합니다.

```python
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": True,                       # 활성화 여부 (기본: False)
        "FAILURE_THRESHOLD": 5,                # 실패 임계값
        "RECOVERY_TIMEOUT": 60,                # 복구 타임아웃 (초)
        "SUCCESS_THRESHOLD": 2,                # Half-Open 성공 임계값

        # Rate Limit Cascade 감지
        "RATE_LIMIT_CASCADE_THRESHOLD": 10,    # 429 응답 임계값
        "RATE_LIMIT_CASCADE_WINDOW_SECONDS": 60,  # 감지 윈도우

        # Self-DDoS 보호
        "SELF_DDOS_PROTECTION_ENABLED": True,
        "SELF_DDOS_REQUEST_THRESHOLD": 100,
        "SELF_DDOS_WINDOW_SECONDS": 10,
        "SELF_DDOS_BACKOFF_MULTIPLIER": 2.0,
    },
}
```

**Dataclass: `CircuitBreakerSettings`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | False | CB 활성화 여부 |
| `failure_threshold` | int | 5 | 연속 실패 횟수 임계값 |
| `recovery_timeout` | int | 60 | Half-Open 전환까지 대기 시간 (초) |
| `success_threshold` | int | 2 | Half-Open에서 Closed로 전환 성공 횟수 |
| `manual_override_ttl_minutes` | int | 90 | 수동 제어 TTL (분) |
| `half_open_request_limit` | int | 10 | Half-Open 상태 최대 요청 수 |
| `max_pending_duration_hours` | int | 4 | DLQ 대기 최대 시간 (시간) |
| `max_retry_lifetime_hours` | int | 24 | 재시도 최대 수명 (시간) |
| `rate_limit_cascade_threshold` | int | 10 | 429 응답 임계값 |
| `rate_limit_cascade_window_seconds` | int | 60 | Cascade 감지 윈도우 (초) |
| `self_ddos_protection_enabled` | bool | True | Self-DDoS 보호 활성화 |
| `self_ddos_request_threshold` | int | 100 | Self-DDoS 요청 임계값 |
| `self_ddos_window_seconds` | int | 10 | Self-DDoS 감지 윈도우 (초) |
| `self_ddos_backoff_multiplier` | float | 2.0 | Self-DDoS 백오프 승수 |

**⚠️ 중요**: `enabled` 기본값은 `False`입니다. 운영 환경에서 명시적으로 활성화해야 합니다.

---

### 4. DLQ (Dead Letter Queue)

DLQ 동작을 제어합니다.

```python
SELF_HEALING = {
    "DLQ": {
        "ENABLED": True,              # DLQ 활성화 여부
        "RETENTION_DAYS": 30,         # 보관 기간 (일)
        "MAX_REPLAY_ATTEMPTS": 2,     # 최대 리플레이 시도 횟수
        "AUTO_REPLAY_ENABLED": True,  # 자동 리플레이 활성화
        "REPLAY_DELAY_SECONDS": 60,   # 리플레이 간 딜레이 (초)
    },
}
```

**Dataclass: `DLQSettings`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `enabled` | bool | True | DLQ 활성화 여부 |
| `retention_days` | int | 30 | 보관 기간 (일) |
| `max_replay_attempts` | int | 2 | 최대 리플레이 시도 횟수 |

---

### 5. IDEMPOTENCY (멱등성)

캐시 기반 멱등성 설정입니다.

```python
SELF_HEALING = {
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,     # 기본 캐시 TTL (초)
        "PAYMENT_CACHE_TTL": 300,    # 결제 캐시 TTL (초, 5분)
        "WEBHOOK_CACHE_TTL": 60,     # 웹훅 캐시 TTL (초)
    },
}
```

**Dataclass: `IdempotencyConfig`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `default_cache_ttl` | int | 60 | 기본 캐시 TTL (초) |
| `payment_cache_ttl` | int | 300 | 결제 캐시 TTL (초) |
| `webhook_cache_ttl` | int | 60 | 웹훅 캐시 TTL (초) |

---

### 6. SECURITY (보안)

보안 관련 임계값입니다.

```python
SELF_HEALING = {
    "SECURITY": {
        "RATE_LIMIT_WINDOW": 60,        # 레이트 리밋 윈도우 (초)
        "RATE_LIMIT_MAX": 100,          # 윈도우 내 최대 요청 수
        "TEMP_BAN_HOURS": 1,            # 임시 차단 시간 (시간)
        "PERM_BAN_THRESHOLD": 5,        # 영구 차단 임계값
        "SUSPICIOUS_IP_CACHE_TIMEOUT": 86400,  # 의심 IP 캐시 (초, 24시간)
        "INJECTION_BAN_HOURS": 24,      # 인젝션 차단 시간 (시간)
        "FAILED_LOGIN_THRESHOLD": 5,    # 로그인 실패 임계값
    },
}
```

**Dataclass: `SecurityThresholds`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `rate_limit_window_seconds` | int | 60 | 레이트 리밋 윈도우 (초) |
| `rate_limit_max_requests` | int | 100 | 최대 요청 수 |
| `temporary_ban_hours` | int | 1 | 임시 차단 시간 (시간) |
| `permanent_ban_threshold` | int | 5 | 영구 차단 임계값 |
| `suspicious_ip_cache_timeout` | int | 86400 | 의심 IP 캐시 TTL (초) |
| `injection_ban_hours` | int | 24 | 인젝션 차단 시간 (시간) |
| `failed_login_threshold` | int | 5 | 로그인 실패 임계값 |
| `suspicious_ip_cache_prefix` | str | "security:suspicious_ip:" | 캐시 키 프리픽스 |
| `banned_ip_cache_prefix` | str | "security:banned_ip:" | 차단 IP 캐시 프리픽스 |

---

### 7. NOTIFICATIONS (알림)

알림 채널 및 제한 설정입니다.

```python
SELF_HEALING = {
    "NOTIFICATIONS": {
        # Slack 채널
        "CRITICAL_CHANNEL": "#critical-alerts",
        "HIGH_CHANNEL": "#ops-alerts",
        "MEDIUM_CHANNEL": "#dev-alerts",

        # 메시지 제한
        "LIMITS": {
            "SLACK_BLOCK_TEXT_LIMIT": 3000,
            "DESCRIPTION_MAX_LENGTH": 500,
            "ACTION_TAKEN_MAX_LENGTH": 200,
            "TITLE_MAX_LENGTH": 150,
            "TIMEOUT_SECONDS": 10,
        },
    },
}
```

**Dataclass: `SlackChannels`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `critical_channel` | str | "#critical-alerts" | 심각 알림 채널 |
| `high_channel` | str | "#ops-alerts" | 높음 알림 채널 |
| `medium_channel` | str | "#dev-alerts" | 중간 알림 채널 |

**Dataclass: `NotificationLimits`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `slack_block_text_limit` | int | 3000 | Slack 블록 텍스트 제한 |
| `description_max_length` | int | 500 | 설명 최대 길이 |
| `action_taken_max_length` | int | 200 | 조치 내용 최대 길이 |
| `title_max_length` | int | 150 | 제목 최대 길이 |
| `notification_timeout_seconds` | int | 10 | HTTP 타임아웃 (초) |

---

### 8. FORENSIC (포렌식)

DLQ 저장 시 컨텍스트 데이터 크기 제한입니다.

```python
SELF_HEALING = {
    "FORENSIC": {
        "ERROR_MESSAGE_MAX_LENGTH": 500,
        "RESPONSE_BODY_MAX_LENGTH": 5000,
        "USER_AGENT_MAX_LENGTH": 500,
    },
}
```

**Dataclass: `ForensicSettings`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `error_message_max_length` | int | 500 | 에러 메시지 최대 길이 |
| `response_body_max_length` | int | 5000 | 응답 본문 최대 길이 |
| `user_agent_max_length` | int | 500 | User-Agent 최대 길이 |

---

### 9. GOVERNANCE (거버넌스)

수동 제어 및 정책 관련 설정입니다.

```python
SELF_HEALING = {
    "GOVERNANCE": {
        "MANUAL_OVERRIDE_TTL_MINUTES": 90,   # 수동 제어 TTL (분)
        "HALF_OPEN_REQUEST_LIMIT": 10,       # Half-Open 요청 제한
        "MAX_PENDING_DURATION_HOURS": 4,     # 최대 대기 시간 (시간)
        "MAX_RETRY_LIFETIME_HOURS": 24,      # 재시도 수명 (시간)
    },
}
```

---

### 10. ERROR_BUDGET (에러 버짓)

Error Budget 및 Burn Rate 임계값 설정입니다. **API를 통해 런타임에 동적 변경이 가능합니다.**

```python
SELF_HEALING = {
    "ERROR_BUDGET": {
        # Error Budget 임계값 (%)
        "THRESHOLD_HEALTHY": 75.0,      # 정상 상태 (75% 이상)
        "THRESHOLD_CAUTION": 50.0,      # 주의 상태 (50-75%)
        "THRESHOLD_WARNING": 20.0,      # 경고 상태 (20-50%)
        "THRESHOLD_CRITICAL": 0.0,      # 위험 상태 (20% 미만)

        # Burn Rate 임계값 (Google SRE 권장)
        "BURN_RATE_FAST_CRITICAL": 14.4,  # 빠른 소진 위험 (2일 내 100% 소진)
        "BURN_RATE_FAST_WARNING": 6.0,    # 빠른 소진 경고 (5일 내 100% 소진)
        "BURN_RATE_SLOW_WARNING": 3.0,    # 느린 소진 경고 (10일 내 100% 소진)
        "BURN_RATE_SLOW_INFO": 1.0,       # 정상 소진율

        # Fail-Safe 설정
        "FAILSAFE_ALERT_ENABLED": True,   # Fail-Safe 발동 시 알림 발송
        "FAILSAFE_COOLDOWN_SECONDS": 300, # 연속 알림 방지 (5분)

        # Heartbeat (Dead Man's Snitch) 설정
        "HEARTBEAT_ENABLED": True,           # Heartbeat 활성화
        "HEARTBEAT_INTERVAL_SECONDS": 60,    # Heartbeat 발송 주기 (1분)
        "HEARTBEAT_TIMEOUT_SECONDS": 120,    # Heartbeat 타임아웃 (2분)

        # 복구 알림 (Recovery Notification) 설정
        "RECOVERY_ALERT_ENABLED": True,           # 복구 알림 발송
        "RECOVERY_ALERT_INCLUDE_DOWNTIME": True,  # 장애 시간 포함

        # Override 에스컬레이션 설정
        "ESCALATION_ENABLED": True,            # 에스컬레이션 활성화
        "ESCALATION_CHANNEL": "#governance",   # 에스컬레이션 채널
        "ESCALATION_MENTION": "@cto @security", # 멘션 대상
    },

    # 메트릭 수집 설정 (NEW)
    "METRIC_COLLECTION": {
        # 동기화 설정
        "SYNC_ON_STARTUP": True,              # 서버 시작 시 동기화
        "SCHEDULED_SYNC_ENABLED": False,      # 주기적 동기화 (권장: 비활성화)
        "SCHEDULED_SYNC_INTERVAL": 86400,     # 주기 (초), 기본 24시간

        # Jitter 설정 (Thundering Herd 방지)
        "JITTER_ENABLED": True,               # Jitter 활성화
        "JITTER_MAX_DELAY_SECONDS": 60.0,     # 최대 지연 시간 (초)

        # 어댑터 설정
        "ADAPTER_TYPE": "django",             # django, redis, null
        "REDIS_PREFIX": "sh:metrics:",        # Redis 어댑터용 키 프리픽스

        # Drift 감지
        "DRIFT_DETECTION_ENABLED": True,      # Drift 감지 활성화
        "DRIFT_WARNING_THRESHOLD": 0.05,      # 5% - 경고
        "DRIFT_CRITICAL_THRESHOLD": 0.20,     # 20% - 심각, 알림 발송
        "DRIFT_INCIDENT_THRESHOLD": 0.50,     # 50% - 인시던트, 이벤트 유실
        "DRIFT_INCIDENT_ENABLED": True,       # 인시던트 자동 생성
        "DRIFT_ALERT_ENABLED": True,          # 알림 발송 활성화
    },
}
```

**Dataclass: `ErrorBudgetConfig`**

| 필드 | 타입 | 기본값 | 범위 | 설명 |
|------|------|--------|------|------|
| `threshold_healthy` | float | 75.0 | 50-100% | 정상 상태 임계값 |
| `threshold_caution` | float | 50.0 | 20-80% | 주의 상태 임계값 |
| `threshold_warning` | float | 20.0 | 5-50% | 경고 상태 임계값 |
| `threshold_critical` | float | 0.0 | 0-20% | 위험 상태 임계값 |
| `burn_rate_fast_critical` | float | 14.4 | 10-50x | 빠른 소진 위험 임계값 |
| `burn_rate_fast_warning` | float | 6.0 | 3-15x | 빠른 소진 경고 임계값 |
| `burn_rate_slow_warning` | float | 3.0 | 1-10x | 느린 소진 경고 임계값 |
| `burn_rate_slow_info` | float | 1.0 | 0.5-3x | 정상 소진율 임계값 |
| `failsafe_alert_enabled` | bool | True | - | Fail-Safe 발동 시 알림 발송 |
| `failsafe_cooldown_seconds` | int | 300 | 60-3600 | 연속 알림 방지 쿨다운 (초) |
| `heartbeat_enabled` | bool | True | - | Heartbeat 활성화 여부 |
| `heartbeat_interval_seconds` | int | 60 | 10-300 | Heartbeat 발송 주기 (초) |
| `heartbeat_timeout_seconds` | int | 120 | 30-600 | Heartbeat 타임아웃 (초) |
| `recovery_alert_enabled` | bool | True | - | 복구 완료 알림 발송 여부 |
| `recovery_alert_include_downtime` | bool | True | - | 알림에 장애 시간 포함 |
| `escalation_enabled` | bool | True | - | Override 에스컬레이션 활성화 |
| `escalation_channel` | str | "#governance" | - | 에스컬레이션 Slack 채널 |
| `escalation_mention` | str | "@cto @security" | - | 에스컬레이션 멘션 대상 |

---

### 11. METRIC_COLLECTION (메트릭 수집) - NEW

> 상세 문서: [13_METRIC_COLLECTION_STRATEGY.md](13_METRIC_COLLECTION_STRATEGY.md)

메트릭 수집 및 동기화 전략을 설정합니다.

**Dataclass: `MetricCollectionSettings`**

| 필드 | 타입 | 기본값 | 변경 방법 | 설명 |
|------|------|--------|-----------|------|
| `sync_on_startup` | bool | True | 환경변수 | 서버 시작 시 Gauge 동기화 |
| `scheduled_sync_enabled` | bool | False | 환경변수 | 주기적 동기화 (권장: 비활성화) |
| `scheduled_sync_interval` | int | 86400 | 환경변수 | 동기화 주기 (초) |
| `jitter_enabled` | bool | True | API/환경변수 | Jitter 활성화 (분산 환경) |
| `jitter_max_delay_seconds` | float | 60.0 | API/환경변수 | 최대 Jitter 지연 시간 (0-300) |
| `adapter_type` | str | "null" | 환경변수 | 어댑터 유형 (django/redis/null) |
| `redis_prefix` | str | "sh:metrics:" | 환경변수 | Redis 키 프리픽스 |
| `drift_detection_enabled` | bool | True | API/환경변수 | Drift 감지 활성화 |
| `drift_warning_threshold` | float | 0.05 | API/환경변수 | 경고 임계값 (5%) |
| `drift_critical_threshold` | float | 0.20 | API/환경변수 | 심각 임계값 (20%) |
| `drift_incident_threshold` | float | 0.50 | API/환경변수 | 인시던트 임계값 (50%) |
| `drift_incident_enabled` | bool | True | API/환경변수 | 인시던트 자동 생성 |
| `drift_alert_enabled` | bool | True | API/환경변수 | 알림 발송 활성화 |

**설정 변경 방법:**

| 설정 그룹 | 변경 방법 | 재시작 필요 |
|-----------|-----------|-------------|
| **Jitter 설정** | API + 환경 변수 | ❌ 불필요 (API) |
| **Drift 임계값** | API + 환경 변수 | ❌ 불필요 (API) |
| **동기화 설정** | 환경 변수 전용 | ✅ 필요 |
| **어댑터 설정** | 환경 변수 전용 | ✅ 필요 |

> 💡 **Jitter API 지원**: `jitter_enabled`, `jitter_max_delay_seconds`는 `/api/self-healing/config/metrics/` 엔드포인트를 통해 런타임에 변경할 수 있습니다. 음수 방지(Clamping)가 적용되어 0.0~300.0 범위만 허용됩니다.

**환경 변수:**

```bash
# 메트릭 수집 기본 설정
SELFHEALING_METRICS_SYNC_ON_STARTUP=true
SELFHEALING_METRICS_ADAPTER_TYPE=django

# Jitter 설정 (K8s 환경) - 환경 변수 또는 API로 변경 가능
SELFHEALING_METRICS_JITTER_ENABLED=true
SELFHEALING_METRICS_JITTER_MAX_DELAY_SECONDS=60.0

# Drift 임계값 설정 (환경 변수 또는 API로 변경 가능)
SELFHEALING_DRIFT_WARNING_THRESHOLD=0.05
SELFHEALING_DRIFT_CRITICAL_THRESHOLD=0.20
SELFHEALING_DRIFT_INCIDENT_THRESHOLD=0.50
SELFHEALING_DRIFT_INCIDENT_ENABLED=true
```

**Drift 임계값 런타임 변경 (API):**

```bash
# 현재 설정 조회
curl -X GET -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/drift-thresholds/

# 임계값 변경
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "warning_threshold": 0.10,
    "critical_threshold": 0.30,
    "incident_threshold": 0.60
  }' \
  $API_URL/api/self-healing/config/drift-thresholds/

# 기본값으로 리셋
curl -X POST -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/drift-thresholds/reset/
```

**Jitter 런타임 변경 (API):**

```bash
# 현재 Metrics (Jitter 포함) 설정 조회
curl -X GET -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/metrics/

# Jitter 설정 변경 (서버 재시작 불필요)
# ⚠️ Clamping 적용: jitter_max_delay_seconds는 0.0~300.0 범위만 허용
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jitter_enabled": true,
    "jitter_max_delay_seconds": 30.0
  }' \
  $API_URL/api/self-healing/config/metrics/

# Jitter 비활성화 (긴급 상황 시)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jitter_enabled": false}' \
  $API_URL/api/self-healing/config/metrics/

# Graceful 전략으로 변경 (진행 중인 요청 완료 후 적용)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "jitter_max_delay_seconds": 120.0,
    "apply_strategy": "graceful",
    "grace_timeout_seconds": 30
  }' \
  $API_URL/api/self-healing/config/metrics/
```

> 🛡️ **삼각 방어 체계**: SLA(비즈니스 목표) + Error Budget(운영 예산) + Jitter(인프라 보호)가 모두 API로 런타임 변경 가능합니다.

**런타임 동적 변경 (API):**

```bash
# 현재 설정 조회
curl -X GET \
  -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/error-budget/

# 임계값 변경 (서버 재시작 불필요)
curl -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"threshold_warning": 25.0, "burn_rate_fast_critical": 12.0}' \
  $API_URL/api/self-healing/config/error-budget/

# Heartbeat 주기 변경
curl -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "heartbeat_enabled": true,
    "heartbeat_interval_seconds": 30,
    "heartbeat_timeout_seconds": 90
  }' \
  $API_URL/api/self-healing/config/error-budget/

# 에스컬레이션 설정 변경
curl -X PATCH \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "escalation_enabled": true,
    "escalation_channel": "#ops-critical",
    "escalation_mention": "@oncall"
  }' \
  $API_URL/api/self-healing/config/error-budget/
```

> 💡 **참고**: 임계값 변경은 즉시 적용됩니다. 변경 후 Error Budget 상태 판정에 새 임계값이 사용됩니다.

> ⚠️ **주의**: `heartbeat_timeout_seconds`는 항상 `heartbeat_interval_seconds`보다 커야 합니다.

---

## 설정 접근 API

### 전체 설정 로드

```python
from selfhealing.core.config import (
    get_config,
    reload_config,
    SelfHealingConfig,
)

# 캐시된 설정 가져오기
config = get_config()

# 설정 새로고침 (런타임 변경 시)
config = reload_config()

# 개별 설정 접근
print(config.sla.payment_hours)
print(config.circuit_breaker.enabled)
```

### 개별 설정 헬퍼 함수

```python
from selfhealing.core.config import (
    get_sla_thresholds,
    get_retry_settings,
    get_circuit_breaker_settings,
    get_dlq_settings,
    get_idempotency_config,
    get_security_thresholds,
    get_notification_limits,
    get_slack_channels,
    get_forensic_settings,
)

# 각 설정 카테고리별 접근
sla = get_sla_thresholds()
retry = get_retry_settings()
cb = get_circuit_breaker_settings()
dlq = get_dlq_settings()
```

### 설정 캐싱

설정은 모듈 레벨에서 캐시됩니다:

```python
_config_cache: SelfHealingConfig | None = None

def get_config() -> SelfHealingConfig:
    global _config_cache
    if _config_cache is None:
        _config_cache = SelfHealingConfig.load()
    return _config_cache
```

런타임 중 설정 변경이 필요하면 `reload_config()`를 호출하세요.

---

## 환경별 권장 설정

### 개발 환경 (local.py)

```python
SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    "RETRY": {
        "MAX_RETRIES": 3,        # 빠른 피드백을 위해 적은 횟수
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 60,       # 짧은 최대 딜레이
        "JITTER_PERCENT": 0.25,
    },
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 30,  # 짧은 복구 시간
    },
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 30,  # 빠른 리플레이
    },
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,
        "PAYMENT_CACHE_TTL": 300,
        "WEBHOOK_CACHE_TTL": 120,
    },
}
```

### 운영 환경 (production.py)

```python
SELF_HEALING = {
    "SLA": {
        "PAYMENT_HOURS": 1,
        "POINT_HOURS": 4,
        "INVENTORY_HOURS": 2,
        "WEBHOOK_HOURS": 8,
        "NOTIFICATION_HOURS": 24,
    },
    "RETRY": {
        "MAX_RETRIES": 5,        # 더 많은 재시도
        "BACKOFF_BASE": 2,
        "BACKOFF_MAX": 300,      # 5분 최대 딜레이
        "JITTER_PERCENT": 0.25,
    },
    "CIRCUIT_BREAKER": {
        "ENABLED": True,
        "FAILURE_THRESHOLD": 5,
        "SUCCESS_THRESHOLD": 3,
        "RECOVERY_TIMEOUT": 60,  # 적절한 복구 시간
    },
    "DLQ": {
        "AUTO_REPLAY_ENABLED": True,
        "MAX_REPLAY_ATTEMPTS": 3,
        "REPLAY_DELAY_SECONDS": 60,
    },
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,
        "PAYMENT_CACHE_TTL": 600,  # 10분 (더 긴 TTL)
        "WEBHOOK_CACHE_TTL": 120,
    },
}
```

### 테스트 환경 (test.py)

```python
SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": False,  # 테스트에서는 비활성화
    },
    "RETRY": {
        "MAX_RETRIES": 1,  # 빠른 테스트
        "BACKOFF_BASE": 1,
        "BACKOFF_MAX": 1,
    },
    "DLQ": {
        "AUTO_REPLAY_ENABLED": False,  # 테스트에서 수동 제어
    },
}
```

---

## 환경 변수 오버라이드

민감한 설정은 환경 변수로 오버라이드할 수 있습니다:

```python
# settings/production.py
import os

SELF_HEALING = {
    "CIRCUIT_BREAKER": {
        "ENABLED": os.getenv("SELF_HEALING_CB_ENABLED", "true").lower() == "true",
        "FAILURE_THRESHOLD": int(os.getenv("SELF_HEALING_CB_THRESHOLD", "5")),
    },
    "NOTIFICATIONS": {
        "CRITICAL_CHANNEL": os.getenv("SLACK_CRITICAL_CHANNEL", "#critical-alerts"),
    },
}
```

---

## 이벤트 로깅 설정 (EventLoggingConfig)

Self-Healing 이벤트 핸들러의 로깅 레벨은 런타임에 동적으로 변경 가능합니다.

### 환경 변수

| 환경 변수 | 기본값 | 설명 |
|-----------|--------|------|
| `SH_EVENT_DLQ_LOG_LEVEL` | INFO | DLQ 이벤트 로깅 레벨 |
| `SH_EVENT_CIRCUIT_BREAKER_LOG_LEVEL` | WARNING | Circuit Breaker 이벤트 로깅 레벨 |
| `SH_EVENT_SLA_LOG_LEVEL` | WARNING | SLA 위반 이벤트 로깅 레벨 |

### 사용 예시

```bash
# 환경 변수로 기본값 설정
export SH_EVENT_DLQ_LOG_LEVEL=DEBUG
export SH_EVENT_CIRCUIT_BREAKER_LOG_LEVEL=INFO
export SH_EVENT_SLA_LOG_LEVEL=INFO
```

### 런타임 API

```python
from shopping.config import EventLoggingConfig

# 현재 설정 조회
config = EventLoggingConfig.get_instance()
print(config.to_dict())
# {
#     "dlq_log_level": "INFO",
#     "circuit_breaker_log_level": "WARNING",
#     "sla_log_level": "WARNING",
#     "last_updated": "2025-01-10T12:00:00Z",
#     "updated_by": None
# }

# 런타임 설정 변경 (API 레벨)
config.update(
    dlq_log_level="DEBUG",
    circuit_breaker_log_level="INFO",
    updated_by="admin-api"
)

# 설정 초기화 (환경 변수 기본값으로)
config.reset()
```

### 설정 우선순위

1. **API 런타임 설정** (최우선) - `EventLoggingConfig.update()`
2. **환경 변수** - `SH_EVENT_*` 환경 변수
3. **하드코딩 기본값** - INFO (DLQ), WARNING (CB, SLA)

이 구조를 통해 운영자는 재배포 없이 로깅 레벨을 조정할 수 있습니다.

---

## Semantic Audit Logging (의미론적 감사 로그)

Config API를 통한 설정 변경 시, 기술적 필드명 대신 **비즈니스 맥락이 담긴 명칭**으로 감사 로그가 기록됩니다.

### 로그 출력 예시

**Before (기존):**
```
[ConfigAPI] metrics config update requested by admin: 
  changes={'jitter_enabled': False, 'jitter_max_delay_seconds': 5.0}, 
  strategy=immediate
```

**After (Semantic):**
```
[ConfigAPI] METRICS config updated by admin:
  • Infrastructure Protection (Jitter): enabled → disabled
  • Jitter Delay Threshold: 60.0s → 5.0s
  Applied: immediate
```

### 주요 필드 매핑

| 기술적 필드명 | Semantic 명칭 | 단위 |
|---------------|---------------|------|
| `jitter_enabled` | Infrastructure Protection (Jitter) | bool |
| `jitter_max_delay_seconds` | Jitter Delay Threshold | seconds |
| `failure_threshold` | Circuit Breaker Failure Threshold | count |
| `recovery_timeout` | Circuit Breaker Recovery Timeout | seconds |
| `threshold_warning` | Error Budget Warning Level | percent |
| `burn_rate_fast_critical` | Fast Burn Rate Critical Threshold | multiplier |
| `default_hours` | Default SLA Resolution Time | hours |
| `heartbeat_enabled` | Heartbeat (Dead Man's Snitch) Toggle | bool |

> 📍 **전체 매핑 목록**: [config_descriptions.py](../../packages/selfhealing-python/src/selfhealing/api/django/config_descriptions.py)

### 단위 포맷팅

| 단위 | 포맷 예시 |
|------|----------|
| `bool` | `enabled` / `disabled` |
| `seconds` | `60.0s` |
| `hours` | `24h` |
| `percent` | `75.0%` |
| `multiplier` | `14.4x` |
| `count` | `5` |

### 사용 예시 (Python)

```python
from selfhealing.api.django.config_descriptions import (
    get_field_description,
    format_value_change,
    format_changes_summary,
)

# 단일 필드 설명 조회
label = get_field_description("jitter_enabled")
# Returns: "Infrastructure Protection (Jitter)"

# 변경 사항 포맷팅
log_entry = format_value_change("jitter_max_delay_seconds", 60.0, 5.0)
# Returns: "Jitter Delay Threshold: 60.0s → 5.0s"

# 여러 변경 사항 요약
changes = {"jitter_enabled": False, "jitter_max_delay_seconds": 5.0}
previous = {"jitter_enabled": True, "jitter_max_delay_seconds": 60.0}
summary = format_changes_summary(changes, previous)
# Returns:
#   • Infrastructure Protection (Jitter): enabled → disabled
#   • Jitter Delay Threshold: 60.0s → 5.0s
```

### 거버넌스 이점

1. **감사 추적성**: 실사단이 로그만 보고도 변경 의도 파악 가능
2. **운영 이해도**: 기술적 용어 대신 비즈니스 맥락 제공
3. **일관성**: 모든 Config 타입에 동일한 포맷 적용
4. **확장성**: 새 필드 추가 시 `CONFIG_DESCRIPTIONS`에만 추가

---

## 설정 검증

애플리케이션 시작 시 설정 검증을 권장합니다:

```python
# apps.py 또는 ready() 메서드에서
from selfhealing.core.config import get_config

def validate_config():
    config = get_config()

    # CB 활성화 확인
    if not config.circuit_breaker.enabled:
        logger.warning("Circuit Breaker is DISABLED in production!")

    # SLA 검증
    if config.sla.payment_hours > 2:
        logger.warning("Payment SLA is too lenient!")

    # 재시도 설정 검증
    if config.retry.max_attempts < 2:
        logger.warning("Retry max attempts is very low!")
```

---

## 관련 문서

- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [05_RETRY_BACKOFF.md](05_RETRY_BACKOFF.md) - Retry 전략
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링
- [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) - Chaos Engineering

---

## Chaos Engineering 설정

> Chaos Engineering 시스템의 런타임 설정은 별도 모듈에서 관리됩니다.
> 상세 내용은 [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) 7절을 참조하세요.

### 설정 위치

- 런타임 설정: `selfhealing.services.runtime_config`
- Chaos 모듈: `selfhealing.services.chaos/`
- 상태 파일: `logs/selfhealing_state/runtime_config_chaos.json`

### 주요 설정 클래스

| 클래스 | 설명 | 위치 |
|--------|------|------|
| `SchedulerConfig` | 스케줄러 설정 | `chaos/scheduler.py` |
| `SafetyGuardConfig` | 안전장치 설정 | `chaos/safety_guard.py` |
| `BlastRadiusPolicy` | 폭발반경 정책 | `chaos/blast_radius.py` |
| `TTLConfig` | TTL 설정 | `chaos/stop_conditions.py` |
| `StopConditionsConfig` | 중단 조건 설정 | `chaos/stop_conditions.py` |
| `DryRunConfig` | Dry Run 설정 | `chaos/stop_conditions.py` |

### SchedulerConfig (스케줄러 설정)

```python
@dataclass
class SchedulerConfig:
    enabled: bool = False                          # 스케줄러 활성화
    dry_run_mode: bool = True                      # Dry Run 모드 (기본: True)
    dry_run_reason: str = "Initial deployment"     # Dry Run 사유
    default_schedule_hour_start: int = 2           # 실험 허용 시작 시간
    default_schedule_hour_end: int = 6             # 실험 허용 종료 시간
    auto_approve_instance_level: bool = True       # 인스턴스 레벨 자동 승인
    auto_approve_service_level: bool = False       # 서비스 레벨 자동 승인
    max_concurrent_experiments: int = 5            # 최대 동시 실험 수
    max_experiments_per_day: int = 10              # 일일 최대 실험 수
    min_interval_between_experiments_minutes: int = 30  # 최소 실험 간격
```

### StopConditionsConfig (자동 중단 조건)

```python
@dataclass
class StopConditionsConfig:
    max_error_rate_percent: float = 5.0            # 에러율 임계값 (%)
    max_latency_p99_ms: int = 2000                 # P99 지연시간 임계값 (ms)
    max_latency_p95_ms: int = 1000                 # P95 지연시간 임계값 (ms)
    min_error_budget_percent: float = 10.0         # 에러 버짓 최소값 (%)
    check_interval_seconds: int = 10               # 체크 주기 (초)
    consecutive_breaches_required: int = 2         # 연속 위반 횟수
    enabled: bool = True                           # 활성화 여부
```

### TTLConfig (자동 만료 설정)

```python
@dataclass
class TTLConfig:
    default_ttl_seconds: int = 600                 # 기본 TTL (10분)
    min_ttl_seconds: int = 60                      # 최소 TTL (1분)
    max_ttl_seconds: int = 3600                    # 최대 TTL (1시간)
    auto_expiration_enabled: bool = True           # 자동 만료 활성화
```

### DryRunConfig (Dry Run 설정)

```python
@dataclass
class DryRunConfig:
    enabled: bool = True                           # Dry Run 모드 (기본: True - 안전)
    reason: str = "Initial deployment"             # Dry Run 사유
```

### 런타임 설정 API

```python
from selfhealing.services.runtime_config import (
    get_scheduler_config,
    update_scheduler_config,
    get_stop_conditions_config,
    update_stop_conditions_config,
)

# 스케줄러 설정 조회
config = get_scheduler_config()
print(config.dry_run_mode)  # True

# 설정 업데이트
updated = update_scheduler_config(dry_run_mode=False)
```

# Self-Healing Configuration Reference - Core

## 개요

Self-Healing 시스템의 모든 설정은 Django settings의 `SELF_HEALING` 딕셔너리를 통해 구성됩니다. 이 문서는 사용 가능한 모든 설정 옵션과 기본값, 환경별 권장 설정을 제공합니다.

> **문서 분할 안내**: 설정 문서가 크기 때문에 3개로 분할되었습니다.
> - [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 핵심 설정 (현재 문서)
> - [09b_CONFIGURATION_ADVANCED.md](09b_CONFIGURATION_ADVANCED.md) - 고급 설정
> - [09c_CONFIGURATION_CHAOS.md](09c_CONFIGURATION_CHAOS.md) - Chaos Engineering 및 Audit

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

**적용 전략 옵션:**

| 옵션 | 설명 | 기본값 |
|------|------|--------|
| `apply_strategy` | `immediate` / `delayed` / `graceful` | `immediate` |
| `delay_seconds` | delayed 선택 시 지연 시간 (1-3600초) | - |
| `grace_timeout_seconds` | graceful 선택 시 최대 대기 시간 (1-300초) | 60 |

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

**런타임 동적 변경 (API):**

```bash
# 현재 Retry 설정 조회
curl -X GET -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/retry/

# 즉시 적용
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"max_attempts": 5, "apply_strategy": "immediate"}' \
  $API_URL/api/self-healing/config/retry/

# Graceful 적용 (진행 중인 재시도 완료 후 적용)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"max_attempts": 5, "apply_strategy": "graceful", "grace_timeout_seconds": 120}' \
  $API_URL/api/self-healing/config/retry/
```

> ⚠️ **주의**: Retry 설정은 진행 중인 재시도 작업에 영향을 줄 수 있으므로 기본 전략이 `delayed`입니다.

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

**⚠️ 중요**: `enabled` 기본값은 `False`입니다. 운영 환경에서 명시적으로 활성화해야 합니다.

**런타임 동적 변경 (API):**

```bash
# 현재 Circuit Breaker 설정 조회
curl -X GET -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/circuit-breaker/

# 즉시 적용 (긴급 상황)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"failure_threshold": 10, "apply_strategy": "immediate"}' \
  $API_URL/api/self-healing/config/circuit-breaker/
```

> ⚠️ **경고**: Circuit Breaker는 시스템 안정성을 보호합니다. 변경 시 주의가 필요하며, 기본 전략이 `delayed` (30초)입니다.

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

캐시 기반 멱등성 설정입니다. 도메인 중립적으로 설계되어 어떤 도메인에서든 사용할 수 있습니다.

```python
SELF_HEALING = {
    "IDEMPOTENCY": {
        "DEFAULT_CACHE_TTL": 60,      # 기본 캐시 TTL (초)
        "EXTENDED_CACHE_TTL": 300,    # 긴 TTL이 필요한 작업용 (초, 5분)
        "SHORT_CACHE_TTL": 60,        # 짧은 TTL이 필요한 작업용 (초)
    },
}
```

**Dataclass: `IdempotencyConfig`**

| 필드 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `default_cache_ttl` | int | 60 | 기본 캐시 TTL (초) |
| `extended_cache_ttl` | int | 300 | 긴 TTL이 필요한 작업용 (초) |
| `short_cache_ttl` | int | 60 | 짧은 TTL이 필요한 작업용 (초) |
| `clock_skew_tolerance_seconds` | float | 5.0 | 클럭 스큐 허용 오차 (초) |

> 💡 **도메인 중립적 설계**: TTL은 작업 특성(중요도, 지속시간)에 따라 선택하며, 특정 도메인에 종속되지 않습니다.

> ⚠️ **경고**: Idempotency는 중복 트랜잭션을 방지합니다. 변경 시 주의가 필요합니다.

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

> 🔒 **보안 경고**: Security 설정은 매우 민감합니다. 변경 전 반드시 검토하세요. 기본 지연 시간이 60초로 가장 깁니다.

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
}
```

> 💡 **적용 전략**: Error Budget 임계값 변경은 기본적으로 **30초 지연 적용**(delayed)됩니다.

---

### 11. METRIC_COLLECTION (메트릭 수집)

> 상세 문서: [14_METRIC_COLLECTION_CORE.md](14_METRIC_COLLECTION_CORE.md)

메트릭 수집 및 동기화 전략을 설정합니다.

```python
SELF_HEALING = {
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

> 🛡️ **삼각 방어 체계**: SLA(비즈니스 목표) + Error Budget(운영 예산) + Jitter(인프라 보호)가 모두 API로 런타임 변경 가능합니다.

---

## 관련 문서

- [09b_CONFIGURATION_ADVANCED.md](09b_CONFIGURATION_ADVANCED.md) - 적용 전략 및 환경별 설정
- [09c_CONFIGURATION_CHAOS.md](09c_CONFIGURATION_CHAOS.md) - Chaos Engineering 및 Audit Logging
- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [05_RETRY_BACKOFF.md](05_RETRY_BACKOFF.md) - Retry 전략
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링

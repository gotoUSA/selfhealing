# Self-Healing Configuration Reference

## 개요

Self-Healing 시스템의 모든 설정은 Django settings의 `SELF_HEALING` 딕셔너리를 통해 구성됩니다. 이 문서는 사용 가능한 모든 설정 옵션과 기본값, 환경별 권장 설정을 제공합니다.

### 설정 위치

- 설정 정의: [shopping/services/self_healing/config.py](../../shopping/services/self_healing/config.py)
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
from shopping.services.self_healing.config import get_sla_thresholds

sla = get_sla_thresholds()
payment_sla = sla.get_threshold("payment")  # timedelta(hours=1)
all_slas = sla.get_all_thresholds()  # dict of all domains
```

---

### 2. RETRY (재시도 정책)

재시도 동작의 핵심 파라미터를 정의합니다.

```python
SELF_HEALING = {
    "RETRY": {
        "MAX_ATTEMPTS": 3,      # 최대 재시도 횟수
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
| `max_attempts` | int | 3 | 최대 재시도 횟수 |
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
from shopping.services.self_healing.config import get_retry_settings

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
```

> 💡 **참고**: 임계값 변경은 즉시 적용됩니다. 변경 후 Error Budget 상태 판정에 새 임계값이 사용됩니다.

---

## 설정 접근 API

### 전체 설정 로드

```python
from shopping.services.self_healing.config import (
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
from shopping.services.self_healing.config import (
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

## 설정 검증

애플리케이션 시작 시 설정 검증을 권장합니다:

```python
# apps.py 또는 ready() 메서드에서
from shopping.services.self_healing.config import get_config

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

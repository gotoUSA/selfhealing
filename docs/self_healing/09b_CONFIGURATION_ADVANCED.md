# Self-Healing Configuration Reference - Advanced

> **문서 분할 안내**: 설정 문서가 크기 때문에 3개로 분할되었습니다.
> - [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 핵심 설정
> - [09b_CONFIGURATION_ADVANCED.md](09b_CONFIGURATION_ADVANCED.md) - 고급 설정 (현재 문서)
> - [09c_CONFIGURATION_CHAOS.md](09c_CONFIGURATION_CHAOS.md) - Chaos Engineering 및 Audit

---

## 적용 전략 (Apply Strategy)

Config API를 통한 설정 변경 시, 각 설정 유형별로 **기본 적용 전략**이 다릅니다. 이는 실수로 인한 시스템 불안정을 방지하고, 취소 기회를 제공합니다.

### 기본 적용 전략 테이블

| Config Type | 기본 전략 | 지연 시간 | 이유 |
|-------------|----------|----------|------|
| `sla` | IMMEDIATE | - | 비즈니스 목표, 즉시 반영 필요 |
| `metrics` | IMMEDIATE | - | 읽기 전용 설정, 부작용 적음 |
| `notification` | IMMEDIATE | - | 알림 채널, 즉시 반영 필요 |
| `forensic` | IMMEDIATE | - | 로그 설정, 즉시 반영 필요 |
| `rate_limit` | IMMEDIATE | - | 트래픽 제어, 즉시 적용 (경고 포함) |
| `retry` | DELAYED | 10초 | 진행 중인 재시도 작업 보호 |
| `dlq` | DELAYED | 10초 | 진행 중인 DLQ 처리 보호 |
| `error_budget` | DELAYED | 30초 | 임계값 변경 시 알림 폭발 방지 |
| `circuit_breaker` | DELAYED | 30초 | 시스템 안정성 보호, 상태 전환 주의 |
| `idempotency` | DELAYED | 30초 | 중복 방지 로직, 트랜잭션 보호 |
| `security` | DELAYED | 60초 | 매우 민감한 설정, 긴 취소 윈도우 |

### 적용 전략 옵션

| 전략 | 설명 | 사용 사례 |
|------|------|----------|
| `immediate` | 즉시 적용 | 긴급 변경, 운영자가 확신할 때 |
| `delayed` | N초 후 적용 (취소 가능) | 대부분의 변경, 안전한 기본값 |
| `graceful` | 진행 중인 작업 완료 후 적용 | 트랜잭션 안전 필요 시 |

### 사용 예시

```bash
# 1. 기본 전략 사용 (Error Budget → 30초 지연)
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -d '{"threshold_warning": 15.0}' \
  $API_URL/api/self-healing/config/error-budget/
# → 202 Accepted (30초 후 적용, 취소 가능)

# 2. 즉시 적용 강제
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -d '{"threshold_warning": 15.0, "apply_strategy": "immediate"}' \
  $API_URL/api/self-healing/config/error-budget/
# → 200 OK (즉시 적용)

# 3. 지연 시간 커스터마이징
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  -d '{"threshold_warning": 15.0, "apply_strategy": "delayed", "delay_seconds": 120}' \
  $API_URL/api/self-healing/config/error-budget/
# → 202 Accepted (2분 후 적용)

# 4. 예약된 변경 취소
curl -X POST -H "Authorization: Bearer $TOKEN" \
  $API_URL/api/self-healing/config/pending/<pending_id>/cancel/
```

### 왜 DELAYED가 기본인가? (Error Budget 예시)

```
시나리오: 운영자가 실수로 threshold_warning을 20% → 5%로 변경

즉시 적용(IMMEDIATE)의 문제:
- 현재 상태 15% → 기존 판정: CAUTION
- 새 임계값 5% 적용 → 즉시 판정: CRITICAL
- 즉시 Slack/PagerDuty 알림 폭발
- 취소 불가, 되돌리기 위해 또 다른 API 호출 필요

지연 적용(DELAYED)의 장점:
- 30초 취소 윈도우 제공
- 운영자가 실수 인지 시 즉시 취소 가능
- 팀 동료가 검토 후 취소 가능
- 알림 폭발 사전 방지
```

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

- [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 핵심 설정 카테고리
- [09c_CONFIGURATION_CHAOS.md](09c_CONFIGURATION_CHAOS.md) - Chaos Engineering 및 Audit Logging
- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [03_CIRCUIT_BREAKER.md](03_CIRCUIT_BREAKER.md) - Circuit Breaker 상세
- [05_RETRY_BACKOFF.md](05_RETRY_BACKOFF.md) - Retry 전략
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링

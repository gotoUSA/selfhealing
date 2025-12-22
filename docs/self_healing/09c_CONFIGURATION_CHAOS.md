# Self-Healing Configuration Reference - Chaos & Audit

> **문서 분할 안내**: 설정 문서가 크기 때문에 3개로 분할되었습니다.
> - [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 핵심 설정
> - [09b_CONFIGURATION_ADVANCED.md](09b_CONFIGURATION_ADVANCED.md) - 고급 설정
> - [09c_CONFIGURATION_CHAOS.md](09c_CONFIGURATION_CHAOS.md) - Chaos Engineering 및 Audit (현재 문서)

---

## ⚠️ 두 종류의 Dry Run 모드

Self-Healing 시스템에는 **두 가지 별도의 Dry Run 모드**가 있습니다. 혼동하지 않도록 주의하세요.

| 구분 | System Dry Run | Chaos Dry Run |
|------|----------------|---------------|
| **대상** | Self-Healing 액션 전체 (CB, Retry, DLQ 등) | Chaos Engineering 장애 주입만 |
| **기본값** | `False` (실제 동작) | `True` (시뮬레이션 - 안전) |
| **API** | `/system/dry-run/enable/`, `/system/dry-run/disable/` | `/chaos/config/dry-run/` |
| **용도** | 프로덕션에서 self-healing 로직 안전하게 테스트 | Chaos 실험 워크플로우 검증 |
| **설정 위치** | `selfhealing.services.system_control` | `selfhealing.services.chaos.stop_conditions` |

### System Dry Run (Self-Healing 전체)

Self-Healing 시스템 전체의 **실제 액션 실행**을 제어합니다.

```python
# system_control.py - SystemState
@dataclass
class SystemState:
    dry_run: bool = False  # 기본값: False (실제 동작)
```

**동작 방식:**
- `True`: 모든 self-healing 로직이 실행되지만, 실제 액션(Circuit Breaker 상태 변경, DLQ 저장, 재시도 등)은 **건너뜀**
- `False`: 정상적으로 모든 액션 실행

**API 사용:**
```bash
# Dry Run 활성화 (관찰 모드)
POST /api/self-healing/system/dry-run/enable/

# Dry Run 비활성화 (실제 동작)
POST /api/self-healing/system/dry-run/disable/

# 현재 상태 확인
GET /api/self-healing/system/status/
```

### Chaos Dry Run (장애 주입만)

Chaos Engineering **장애 주입 실험**의 실행을 제어합니다.

```python
# chaos/stop_conditions.py - DryRunConfig
@dataclass
class DryRunConfig:
    enabled: bool = True   # 기본값: True (시뮬레이션 - 안전)
    reason: str = "Initial deployment - simulation mode"
```

**동작 방식:**
- `True`: 장애 주입 없이 전체 워크플로우(안전 검사, 승인, 스케줄링 등)만 검증
- `False`: 실제 장애 주입 실행 (latency, error, resource exhaustion 등)

**API 사용:**
```bash
# Chaos Dry Run 설정 조회/변경
GET  /api/self-healing/chaos/config/dry-run/
PATCH /api/self-healing/chaos/config/dry-run/
{"enabled": false, "reason": "Production chaos testing approved"}
```

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

> **Note**: Dry Run 설정은 `DryRunConfig`에서 별도 관리됩니다. 위의 "두 종류의 Dry Run 모드" 섹션을 참조하세요.

```python
@dataclass
class SchedulerConfig:
    enabled: bool = False                          # 스케줄러 활성화
    default_schedule_hour_start: int = 2           # 실험 허용 시작 시간 (UTC)
    default_schedule_hour_end: int = 6             # 실험 허용 종료 시간 (UTC)
    auto_approve_instance_level: bool = True       # 인스턴스 레벨 자동 승인
    auto_approve_service_level: bool = False       # 서비스 레벨 자동 승인
    max_concurrent_experiments: int = 3            # 최대 동시 실험 수
    max_experiments_per_day: int = 10              # 일일 최대 실험 수
    min_interval_between_experiments_minutes: int = 30  # 최소 실험 간격 (분)
```

**API로 변경 가능한 필드:**

| 필드 | 타입 | 범위 | 설명 |
|------|------|------|------|
| `enabled` | bool | - | 스케줄러 마스터 스위치 |
| `default_schedule_hour_start` | int | 0-23 | 실험 허용 시작 시간 |
| `default_schedule_hour_end` | int | 0-23 | 실험 허용 종료 시간 |
| `auto_approve_instance_level` | bool | - | 인스턴스 레벨 자동 승인 |
| `auto_approve_service_level` | bool | - | 서비스 레벨 자동 승인 |
| `max_concurrent_experiments` | int | 1-10 | 최대 동시 실험 수 |
| `max_experiments_per_day` | int | 1-100 | 일일 최대 실험 수 |
| `min_interval_between_experiments_minutes` | int | 0-1440 | 최소 실험 간격 |

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

### DryRunConfig (Chaos Dry Run 설정)

> ⚠️ 이것은 **Chaos Engineering 장애 주입**에 대한 Dry Run입니다.
> Self-Healing 전체 시스템의 Dry Run은 `/system/dry-run/` API를 사용하세요.

```python
@dataclass
class DryRunConfig:
    enabled: bool = True                           # Dry Run 모드 (기본: True - 안전)
    reason: str = "Initial deployment - simulation mode"  # Dry Run 사유
```

**권장 사용 흐름:**
1. 처음 배포 시 `enabled=True` (기본값) 유지
2. Chaos 실험 워크플로우가 정상 동작하는지 확인
3. 승인 후 `enabled=False`로 변경하여 실제 장애 주입 시작
4. 문제 발생 시 즉시 `enabled=True`로 롤백

### 런타임 설정 API

```python
from selfhealing.services.runtime_config import (
    get_scheduler_config,
    update_scheduler_config,
    get_stop_conditions_config,
    update_stop_conditions_config,
)
from selfhealing.services.chaos.stop_conditions import get_dry_run_config
from selfhealing.services.system_control import get_system_control_manager

# 스케줄러 설정 조회
scheduler_config = get_scheduler_config()
print(scheduler_config.enabled)  # False

# Chaos Dry Run 설정 조회 (장애 주입용)
chaos_dry_run = get_dry_run_config()
print(chaos_dry_run.enabled)  # True (기본값 - 안전)

# System Dry Run 상태 조회 (Self-Healing 전체용)
system_manager = get_system_control_manager()
print(system_manager.is_dry_run())  # False (기본값 - 실제 동작)
```

### REST API 엔드포인트

#### Chaos Engineering 설정 API

| 설정 클래스 | API 엔드포인트 | 메소드 | 설명 |
|------------|---------------|--------|------|
| `SchedulerConfig` | `/chaos/config/scheduler/` | GET/PATCH | 스케줄러 설정 |
| `SafetyGuardConfig` | `/chaos/config/safety-guard/` | GET/PATCH | 안전장치 설정 |
| `BlastRadiusPolicy` | `/chaos/config/blast-radius/` | GET/PATCH | 폭발반경 정책 |
| `StopConditionsConfig` | `/chaos/config/stop-conditions/` | GET/PATCH | 자동 중단 조건 |
| `TTLConfig` | `/chaos/config/ttl/` | GET/PATCH | TTL 설정 |
| `DryRunConfig` | `/chaos/config/dry-run/` | GET/PATCH | **Chaos** 장애 주입 Dry Run |

#### System 전역 제어 API

| 기능 | API 엔드포인트 | 메소드 | 설명 |
|------|---------------|--------|------|
| System Status | `/system/status/` | GET | 전체 시스템 상태 조회 |
| System Enable | `/system/enable/` | POST | Self-Healing 활성화 |
| System Disable | `/system/disable/` | POST | Self-Healing 비활성화 (Kill Switch) |
| **System Dry Run Enable** | `/system/dry-run/enable/` | POST | **Self-Healing 전체** Dry Run 활성화 |
| **System Dry Run Disable** | `/system/dry-run/disable/` | POST | **Self-Healing 전체** Dry Run 비활성화 |

---

## Audit Logging System

Self-Healing 시스템의 모든 설정 변경은 규정 준수(GDPR/CCPA) 및 보안 감사를 위해 포괄적으로 기록됩니다.

### 개요

감사 로깅 시스템은 다음을 제공합니다:

| 기능 | 설명 |
|------|------|
| **IP 마스킹** | GDPR/CCPA 준수를 위해 마지막 2 옥텟 마스킹 (예: `192.168.***.***`) |
| **Old/New 값 추적** | 모든 설정 변경의 이전/이후 값 기록 |
| **JSON 구조화 로그** | 기계 파싱 및 분석 용이 |
| **Trace ID 상관관계** | W3C Trace Context 호환 요청 추적 |
| **Hash Chain 무결성** | 블록체인 방식의 변조 감지 |
| **다중 백엔드** | 로컬 파일, CloudWatch, Datadog, S3 WORM 등 |

### 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                    Audit Logging System                        │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │   Masking   │  │  Integrity   │  │   Trace Context         │ │
│  │ (IP/Email)  │  │ (Hash Chain) │  │ (Request Correlation)   │ │
│  └──────┬──────┘  └──────┬───────┘  └──────────┬──────────────┘ │
│         └────────────────┼─────────────────────┘                │
│                          ▼                                      │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │                    AuditLogger                           │   │
│  │  • log_change()  • log_batch_update()  • verify()       │   │
│  └──────────────────────────┬───────────────────────────────┘   │
│                             ▼                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │               CompositeBackend                           │   │
│  └──┬──────────┬──────────┬────────────┬───────────────────┘   │
│     ▼          ▼          ▼            ▼                        │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌──────────┐                   │
│ │ Local  │ │CloudW. │ │Datadog │ │ S3 WORM  │                   │
│ │(Active)│ │(Stub)  │ │(Stub)  │ │ (Stub)   │                   │
│ └────────┘ └────────┘ └────────┘ └──────────┘                   │
└─────────────────────────────────────────────────────────────────┘
```

### 기본 사용법

#### 설정 변경 기록

```python
from selfhealing.audit import log_config_change, AuditLogger

# 방법 1: 편의 함수 사용
log_config_change(
    config_type="CIRCUIT_BREAKER",
    config_key="failure_threshold",
    action="UPDATE",
    old_value=5,
    new_value=10,
    user="admin@example.com",
    ip_address="192.168.1.100",  # 자동으로 192.168.***.***.로 마스킹됨
    reason="Increase threshold for stability"
)

# 방법 2: AuditLogger 인스턴스 사용
logger = AuditLogger.get_instance()
logger.log_change(
    config_type="SLA",
    config_key="target_availability",
    action="UPDATE",
    old_value=0.99,
    new_value=0.995
)
```

#### Django View에서 사용

```python
from selfhealing.audit import log_config_change
from selfhealing.audit.masking import extract_ip_from_request
from selfhealing.audit.trace import get_trace_id

def update_config_view(request):
    old_value = get_current_config()
    new_value = request.POST.get('value')
    
    # 설정 업데이트 로직...
    
    log_config_change(
        config_type="NOTIFICATION",
        config_key="slack_webhook_url",
        action="UPDATE",
        old_value=old_value,
        new_value=new_value,
        user=request.user.email,
        ip_address=extract_ip_from_request(request),
        trace_id=get_trace_id(),  # 미들웨어가 자동 설정
    )
```

### IP 마스킹 (Privacy Compliance)

GDPR 및 CCPA 준수를 위해 IP 주소의 마지막 2개 옥텟이 자동으로 마스킹됩니다.

```python
from selfhealing.audit.masking import mask_ip, mask_email, mask_sensitive_fields

# IPv4 마스킹
mask_ip("192.168.1.100")  # → "192.168.***.***"
mask_ip("10.0.0.1", mask_last_octets=1)  # → "10.0.0.***"

# IPv6 마스킹
mask_ip("2001:db8::1")  # → "2001:db8::***"

# 이메일 마스킹
mask_email("john.doe@example.com")  # → "j***e@example.com"

# 민감 필드 일괄 마스킹
data = {
    "user": "admin",
    "password": "secret123",
    "api_key": "sk-xxx",
    "config": {"secret_token": "abc123"}
}
masked = mask_sensitive_fields(data, ["password", "api_key", "secret_token"])
# → {"user": "admin", "password": "***REDACTED***", ...}
```

### Hash Chain 무결성

모든 감사 로그는 블록체인과 유사한 해시 체인으로 연결되어 변조를 감지합니다.

#### 로그 엔트리 구조

```json
{
  "sequence": 42,
  "timestamp": "2025-01-15T10:30:00.123456+00:00",
  "config_type": "CIRCUIT_BREAKER",
  "config_key": "failure_threshold",
  "action": "UPDATE",
  "old_value": 5,
  "new_value": 10,
  "user": "admin@example.com",
  "ip_address": "192.168.***.***",
  "trace_id": "req-abc12345",
  "integrity": {
    "sequence": 42,
    "previous_hash": "a1b2c3d4e5f6...",
    "current_hash": "f6e5d4c3b2a1...",
    "algorithm": "sha256"
  }
}
```

#### 무결성 검증

```python
from selfhealing.audit import AuditLogger
from selfhealing.audit.integrity import verify_audit_log_integrity

# Logger를 통한 검증
logger = AuditLogger.get_instance()
is_valid, error_msg = logger.verify_integrity()

if not is_valid:
    print(f"무결성 검증 실패: {error_msg}")
    # 알림 전송 또는 조사 시작

# 파일 직접 검증
is_valid, error = verify_audit_log_integrity("/path/to/audit.log")
```

#### 변조 감지 상세

```python
from selfhealing.audit.integrity import HashChainVerifier

verifier = HashChainVerifier()

# 로그 파일 읽기
with open("audit.log") as f:
    entries = [json.loads(line) for line in f]

for entry in entries:
    verifier.add_entry(entry)

# 변조 지점 찾기
issues = verifier.find_tampering()
for issue in issues:
    print(f"변조 감지: {issue}")
    # "Entry 42: Hash mismatch - expected abc123, got def456"
```

### Trace ID (요청 추적)

분산 시스템에서 요청을 추적하기 위해 W3C Trace Context 호환 Trace ID를 사용합니다.

#### Django 미들웨어 설정

```python
# settings.py
MIDDLEWARE = [
    # ... 다른 미들웨어 ...
    'selfhealing.audit.trace.trace_id_middleware',
    # ... 다른 미들웨어 ...
]
```

#### 수동 Trace ID 관리

```python
from selfhealing.audit.trace import (
    generate_trace_id,
    get_trace_id,
    set_trace_id,
    TraceContext
)

# 새 Trace ID 생성
trace_id = generate_trace_id()  # → "req-abc12345"

# 컨텍스트 관리자 사용
with TraceContext() as trace_id:
    # 이 블록 내 모든 로그에 동일한 trace_id 적용
    log_config_change(...)
    do_something_else()

# 현재 Trace ID 조회
current = get_trace_id()  # → "req-abc12345" 또는 None
```

### Backend 설정

#### 1단계: 로컬 파일 (기본, 활성화)

```python
from selfhealing.audit.backends import LocalFileBackend

backend = LocalFileBackend(
    log_dir="/var/log/selfhealing/audit",
    enable_hash_chain=True,
    rotate_daily=True
)

# 설정된 백엔드로 로거 초기화
logger = AuditLogger.get_instance(backends=[backend])
```

#### 2-4단계: 클라우드 백엔드 (인터페이스만)

다음 백엔드는 인터페이스만 구현되어 있으며, 필요시 활성화할 수 있습니다:

```python
from selfhealing.audit.backends import (
    CloudWatchBackend,
    DatadogBackend,
    S3WormBackend,
    RemoteAuditBackend,
    CompositeBackend
)

# 설정 템플릿 조회
print(CloudWatchBackend.get_configuration_template())
# {
#   "aws_region": "us-east-1",
#   "log_group": "/selfhealing/audit",
#   "log_stream_prefix": "config-changes"
# }

# 다중 백엔드 구성 (예시)
backends = CompositeBackend([
    LocalFileBackend("/var/log/audit"),
    CloudWatchBackend(log_group="/prod/audit"),
    S3WormBackend(bucket="audit-logs", retention_days=2555),
])

logger = AuditLogger.get_instance(backends=[backends])
```

| 백엔드 | 상태 | 용도 |
|--------|------|------|
| `LocalFileBackend` | ✅ 활성 | 개발/테스트, 기본 감사 |
| `CloudWatchBackend` | ⚪ 인터페이스 | AWS 환경 중앙 로깅 |
| `DatadogBackend` | ⚪ 인터페이스 | 모니터링 통합 |
| `S3WormBackend` | ⚪ 인터페이스 | 규정 준수 (7년 보관) |
| `RemoteAuditBackend` | ⚪ 인터페이스 | 외부 감사 서버 |

### 로그 조회

```python
from datetime import datetime, timedelta
from selfhealing.audit import AuditLogger

logger = AuditLogger.get_instance()

# config_type으로 조회
changes = logger.query(config_type="CIRCUIT_BREAKER")

# 시간 범위로 조회
yesterday = datetime.now() - timedelta(days=1)
changes = logger.query(start_time=yesterday)

# 사용자별 조회
changes = logger.query(user="admin@example.com")

# 복합 조회
changes = logger.query(
    config_type="SLA",
    user="admin@example.com",
    start_time=yesterday,
    limit=100
)
```

### 설정 환경 변수

| 환경 변수 | 설명 | 기본값 |
|-----------|------|--------|
| `AUDIT_LOG_DIR` | 감사 로그 저장 디렉토리 | `./logs/audit` |
| `AUDIT_ENABLE_HASH_CHAIN` | 해시 체인 활성화 | `true` |
| `AUDIT_ROTATE_DAILY` | 일별 로그 로테이션 | `true` |
| `AUDIT_MASK_IP_OCTETS` | 마스킹할 IP 옥텟 수 | `2` |
| `AUDIT_CLOUDWATCH_LOG_GROUP` | CloudWatch 로그 그룹 | - |
| `AUDIT_DATADOG_API_KEY` | Datadog API 키 | - |
| `AUDIT_S3_BUCKET` | S3 WORM 버킷 | - |
| `AUDIT_REMOTE_ENDPOINT` | 원격 감사 서버 URL | - |

### 보안 권장사항

1. **로그 파일 권한**: 감사 로그는 최소 권한으로 보호
   ```bash
   chmod 640 /var/log/selfhealing/audit/*.log
   chown root:audit /var/log/selfhealing/audit/
   ```

2. **Hash Chain 상태 파일**: 별도 보관
   ```bash
   # 상태 파일은 다른 위치에 저장
   chmod 600 /var/lib/selfhealing/hash_chain_state.json
   ```

3. **정기 무결성 검증**: Cron 또는 스케줄러로 자동화
   ```python
   # 매일 자정 검증
   @scheduler.scheduled_job('cron', hour=0)
   def verify_audit_logs():
       logger = AuditLogger.get_instance()
       is_valid, error = logger.verify_integrity()
       if not is_valid:
           send_alert(f"Audit log integrity check failed: {error}")
   ```

4. **외부 백업**: 규정 준수를 위해 별도 시스템에 복제
   - S3 WORM (Object Lock) 사용 권장
   - 최소 7년 보관 (SOX, HIPAA 등)

---

## 관련 문서

- [09a_CONFIGURATION_CORE.md](09a_CONFIGURATION_CORE.md) - 핵심 설정 카테고리
- [09b_CONFIGURATION_ADVANCED.md](09b_CONFIGURATION_ADVANCED.md) - 고급 설정
- [13_CHAOS_ENGINEERING.md](13_CHAOS_ENGINEERING.md) - Chaos Engineering 상세
- [01_OVERVIEW.md](01_OVERVIEW.md) - 시스템 개요
- [08_OBSERVABILITY.md](08_OBSERVABILITY.md) - 메트릭 및 모니터링

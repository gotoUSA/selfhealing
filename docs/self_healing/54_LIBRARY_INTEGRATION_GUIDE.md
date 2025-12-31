# 54. 라이브러리 통합 가이드

> **문서 버전**: 1.1.0
> **생성일**: 2025-12-31
> **최종 수정**: 2025-12-31
> **대상**: Self-Healing 라이브러리 사용자
> **전제 조건**: [53_UNCONNECTED_FEATURES_ANALYSIS.md](53_UNCONNECTED_FEATURES_ANALYSIS.md) 참조

---

## 📋 목차

1. [개요](#1-개요)
2. [미들웨어 통합 가이드](#2-미들웨어-통합-가이드)
3. [서비스 통합 가이드](#3-서비스-통합-가이드)
4. [Core 컴포넌트 통합 가이드](#4-core-컴포넌트-통합-가이드)
5. [Celery 태스크 통합](#5-celery-태스크-통합)
5a. [시그널 및 이벤트 핸들러 통합](#5a-시그널-및-이벤트-핸들러-통합)
6. [Audit CLI 도구 통합](#6-audit-cli-도구-통합)
7. [환경별 설정 예시](#7-환경별-설정-예시)

---

## 1. 개요

### 1.1 이 문서의 목적

Self-Healing 라이브러리의 **선택적 기능**들을 프로덕션 환경에 통합하는 방법을 안내합니다.

```
┌─────────────────────────────────────────────────────────────────┐
│                    통합 수준별 가이드                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  Level 1: 기본 통합                                              │
│  ─────────────────                                               │
│  • HealthBridgeMiddleware                                        │
│  • SelfHealingMiddleware                                         │
│  • HybridRateLimitMiddleware                                     │
│                                                                  │
│  Level 2: 강화된 보안/감사                                       │
│  ────────────────────────                                        │
│  • + ActorContextMiddleware                                      │
│  • + SensitiveAccessLoggingMiddleware                           │
│  • + SecurityViolationService                                    │
│                                                                  │
│  Level 3: 고급 복원력                                            │
│  ─────────────────                                               │
│  • + TieringMiddleware (Load Shedding)                          │
│  • + IdempotencyService                                          │
│  • + CertificateExpiryMonitor                                    │
│  • + TLSResilientClient                                          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 미들웨어 통합 가이드

### 2.1 TieringMiddleware (Emergency Load Shedding)

#### 목적
Emergency Mode 시 API 중요도에 따라 트래픽을 선택적으로 차단합니다.

#### 통합 단계

**Step 1: settings.py에 미들웨어 추가**

```python
# settings.py

MIDDLEWARE = [
    # === Self-Healing Core ===
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",

    # === Django Core ===
    "django.middleware.security.SecurityMiddleware",
    # ... 기타 Django 미들웨어

    # === Load Shedding (Emergency Mode용) ===
    "selfhealing.api.django.tiering.TieringMiddleware",

    # === Rate Limiting ===
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
]

# 선택적 비활성화 (기본값: True)
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = True
```

**Step 2: API Tier 등록**

```python
# your_app/apps.py 또는 초기화 코드

from selfhealing.api.django.tiering import get_tier_registry, TierMapping

def register_api_tiers():
    """API별 Tier 등록"""
    registry = get_tier_registry()

    # Critical APIs (Emergency에서도 50% 허용)
    registry.register(TierMapping(
        path_pattern="/api/payments/",
        tier="critical",
        pattern_type="prefix"
    ))

    # Standard APIs (Emergency LEVEL_2에서 90% 차단)
    registry.register(TierMapping(
        path_pattern="/api/orders/",
        tier="standard",
        pattern_type="prefix"
    ))

    # Non-Essential APIs (Emergency에서 먼저 차단)
    registry.register(TierMapping(
        path_pattern="/api/recommendations/",
        tier="non_essential",
        pattern_type="prefix"
    ))
```

**Step 3: Emergency Mode 트리거 연동**

```python
# 자동 트리거: CircuitBreaker OPEN 시 Emergency Mode 진입
# 수동 트리거:
from selfhealing.services.emergency_mode import EmergencyManager

manager = EmergencyManager()
manager.escalate_to_level(2)  # LEVEL_2로 상승
```

#### Emergency Level 동작표

| Level | Critical | Standard | Non-Essential |
|-------|----------|----------|---------------|
| NORMAL (0) | 100% | 100% | 100% |
| LEVEL_1 (1) | 100% | 100% | 0% |
| LEVEL_2 (2) | 100% | 10% | 0% |
| LEVEL_3 (3) | 50% | 0% | 0% |

---

### 2.2 SensitiveAccessLoggingMiddleware (컴플라이언스 감사)

#### 목적
민감한 엔드포인트 접근을 로깅하여 컴플라이언스 감사 요구사항을 충족합니다.

#### 통합 단계

**Step 1: settings.py에 미들웨어 추가**

```python
MIDDLEWARE = [
    # ... 기존 미들웨어

    # === 민감 접근 로깅 (컴플라이언스) ===
    "selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware",
]
```

**Step 2: 민감 엔드포인트 정의 (선택적)**

```python
# settings.py

# 기본 민감 엔드포인트 (자동 감지)
# - /api/self-healing/audit/
# - /api/self-healing/config/
# - /api/self-healing/chaos/

# 추가 민감 엔드포인트 정의
SELFHEALING_SENSITIVE_ENDPOINTS = [
    "/api/admin/",
    "/api/users/profile/",
    "/api/payments/refund/",
]
```

**Step 3: 로그 확인**

```
[SensitiveAccessLog] GET /api/self-healing/audit/entries/
  user=admin@example.com ip=192.168.1.100
  status=200 duration=45.2ms
```

#### FAIL-OPEN 설계

```python
# 로깅 실패 시에도 요청 처리는 계속됨
try:
    self.access_logger.log_if_sensitive(request, response, response_time_ms)
except Exception as e:
    logger.error(f"[AccessLog] Middleware error (fail-open): {e}")
    # 요청은 정상 처리됨
```

---

### 2.3 ActorContextMiddleware (Actor 추적)

#### 목적
모든 요청에서 "누가" 작업을 수행하는지 자동 추적하여 감사 로그에 기록합니다.

#### 통합 단계

**Step 1: settings.py에 미들웨어 추가**

```python
MIDDLEWARE = [
    # ...
    "django.contrib.auth.middleware.AuthenticationMiddleware",

    # === Actor 컨텍스트 (인증 이후에 배치) ===
    "selfhealing.api.django.middleware.ActorContextMiddleware",
    # 또는 selfhealing 없이 사용:
    # "myproject.middleware.actor_middleware.ActorContextMiddleware",
]
```

**Step 2: 코드에서 Actor 정보 사용**

```python
from selfhealing.context import ActorContext

def my_view(request):
    # 현재 Actor 정보 조회
    actor = ActorContext.get_current()

    print(f"User: {actor.actor_id}")       # admin@example.com
    print(f"Type: {actor.actor_type}")     # user / system / anonymous
    print(f"IP: {actor.ip_address}")       # 192.168.1.100
    print(f"Session: {actor.session_id}")  # abc123...

    # AuditEntry에 자동으로 actor 정보가 채워짐
    AuditEntry.objects.create(
        action="config_change",
        # actor_id, actor_type은 자동 설정됨
    )
```

---

## 3. 서비스 통합 가이드

### 3.1 IdempotencyService (멱등성 보장)

#### 목적
재시도 시 동일한 결과를 보장하여 중복 처리를 방지합니다.

#### 통합 예시: 결제 처리

```python
# payment/services.py

from selfhealing.services.idempotency_service import (
    IdempotencyService,
    IdempotencyKey,
    IdempotencyDomain
)

class PaymentService:
    def __init__(self):
        self.idempotency = IdempotencyService()

    def process_payment(self, payment_id: int, amount: int) -> PaymentResult:
        """멱등성이 보장된 결제 처리"""

        # 1. 멱등성 키 생성
        key = IdempotencyKey.for_operation(
            entity_type="payment",
            entity_id=payment_id,
            operation="process"
        )

        # 2. 중복 체크
        check_result = self.idempotency.check(
            key=key,
            lookup_fn=lambda: self._find_existing_payment(payment_id)
        )

        if check_result.is_duplicate:
            # 이미 처리된 결제 → 기존 결과 반환
            return check_result.existing_result

        # 3. 새 결제 처리
        try:
            result = self._do_payment(payment_id, amount)

            # 4. 결과 저장 (재시도 시 반환용)
            self.idempotency.save_result(key, result)

            return result
        except Exception as e:
            # 실패 시 멱등성 키 삭제 (재시도 가능하게)
            self.idempotency.clear(key)
            raise
```

#### 이벤트 기반 멱등성

```python
from selfhealing.services.idempotency_service import IdempotencyKey

# Webhook 이벤트 처리
def handle_webhook(event_id: str, event_type: str, payload: dict):
    key = IdempotencyKey.for_event(
        event_id=event_id,
        event_type=event_type
    )

    result = idempotency_service.check(key, lookup_fn=lambda: get_processed_event(event_id))

    if result.is_duplicate:
        return {"status": "already_processed"}

    # 이벤트 처리...
```

---

### 3.2 SecurityViolationService (보안 위반 처리)

#### 목적
보안 위반을 감지하고, Self-Heal하지 않고 즉시 차단합니다.

#### 통합 예시: Webhook 서명 검증

```python
# webhooks/handlers.py

from selfhealing.services.security_violation_service import (
    SecurityViolationService,
    ViolationType,
    SecurityContext
)

class WebhookHandler:
    def __init__(self):
        self.security = SecurityViolationService()

    def handle_webhook(self, request):
        # 서명 검증
        if not self._verify_signature(request):
            # 보안 위반 기록 및 차단
            context = SecurityContext(
                source_ip=self._get_client_ip(request),
                endpoint=request.path,
                headers=dict(request.headers),
            )

            self.security.handle_violation(
                violation_type=ViolationType.SIGNATURE_INVALID,
                context=context,
                details={
                    "expected_signature": "...",
                    "received_signature": request.headers.get("X-Signature"),
                }
            )

            # 403 Forbidden 반환 (self-heal 하지 않음!)
            return HttpResponseForbidden("Invalid signature")

        # 정상 처리...
```

#### 위반 유형별 처리

```python
# 자동 차단 + 알림 라우팅
ViolationType.SIGNATURE_INVALID    # → CRITICAL, Slack + Email + SMS + PagerDuty
ViolationType.DATA_TAMPERED        # → CRITICAL
ViolationType.TOKEN_FORGED         # → CRITICAL
ViolationType.REPLAY_ATTACK        # → CRITICAL
ViolationType.UNAUTHORIZED_ACCESS  # → HIGH, Slack + Email
ViolationType.INJECTION_ATTEMPT    # → HIGH
ViolationType.RATE_LIMIT_ABUSE     # → MEDIUM, Slack only
ViolationType.SUSPICIOUS_ACTIVITY  # → MEDIUM
```

---

## 4. Core 컴포넌트 통합 가이드

### 4.1 CertificateExpiryMonitor (인증서 모니터링)

#### 목적
외부 API의 SSL 인증서 만료를 사전에 감지합니다.

#### Celery 태스크로 통합

```python
# your_app/tasks/infra_tasks.py

from celery import shared_task
from selfhealing.core.cert_monitor import (
    CertificateExpiryMonitor,
    CertificateStatus
)

# 모니터링할 엔드포인트 목록
MONITORED_ENDPOINTS = [
    "https://api.toss.im",
    "https://api.stripe.com",
    "https://api.your-partner.com",
]

@shared_task(name="check_certificate_expiry")
def check_certificate_expiry():
    """매일 실행: 인증서 만료 체크"""

    monitor = CertificateExpiryMonitor(
        warning_days=30,  # 30일 전부터 경고
        critical_days=7,  # 7일 전은 긴급
    )

    alerts = []

    for endpoint in MONITORED_ENDPOINTS:
        cert_info = monitor.check_endpoint(endpoint)

        if cert_info.status == CertificateStatus.CRITICAL:
            alerts.append({
                "level": "critical",
                "endpoint": endpoint,
                "expires_in_days": cert_info.days_remaining,
            })
        elif cert_info.status == CertificateStatus.EXPIRING_SOON:
            alerts.append({
                "level": "warning",
                "endpoint": endpoint,
                "expires_in_days": cert_info.days_remaining,
            })

    if alerts:
        send_certificate_alerts(alerts)

    return {"checked": len(MONITORED_ENDPOINTS), "alerts": len(alerts)}

def send_certificate_alerts(alerts):
    """Slack/Email 알림 전송"""
    for alert in alerts:
        if alert["level"] == "critical":
            # 긴급 알림
            send_slack_alert(
                channel="#ops-critical",
                message=f"🚨 인증서 만료 임박: {alert['endpoint']}\n"
                        f"남은 일수: {alert['expires_in_days']}일"
            )
        else:
            # 경고 알림
            send_slack_alert(
                channel="#ops-alerts",
                message=f"⚠️ 인증서 갱신 필요: {alert['endpoint']}\n"
                        f"남은 일수: {alert['expires_in_days']}일"
            )
```

#### Celery Beat 스케줄 설정

```python
# settings.py

CELERY_BEAT_SCHEDULE = {
    'check-certificates-daily': {
        'task': 'check_certificate_expiry',
        'schedule': crontab(hour=9, minute=0),  # 매일 오전 9시
    },
}
```

---

### 4.2 TLSResilientClient (TLS 복원력 HTTP 클라이언트)

#### 목적
TLS 오류 발생 시 적절히 분류하고 재시도 전략을 적용합니다.

#### 통합 예시: 외부 결제 API 호출

```python
# payment/external_clients.py

from selfhealing.core.tls_handler import (
    SimpleTLSResilientClient,
    TLSErrorType,
    TLSErrorSeverity
)

class PaymentGatewayClient:
    def __init__(self, base_url: str):
        self.client = SimpleTLSResilientClient(
            base_url=base_url,
            retry_on_tls_error=True,
            max_retries=3,
            alert_callback=self._handle_tls_alert
        )

    def charge(self, amount: int, card_token: str) -> dict:
        try:
            response = self.client.post(
                "/v1/charges",
                json={"amount": amount, "source": card_token}
            )
            return response.json()
        except TLSError as e:
            self._handle_tls_error(e)
            raise

    def _handle_tls_error(self, error):
        """TLS 오류 유형별 처리"""
        if error.error_type == TLSErrorType.CERTIFICATE_EXPIRED:
            # 인증서 만료: 즉시 알림, 재시도 불가
            send_urgent_alert(
                f"결제 게이트웨이 인증서 만료!\n"
                f"Endpoint: {error.endpoint}\n"
                f"즉시 조치 필요"
            )
        elif error.error_type == TLSErrorType.HANDSHAKE_TIMEOUT:
            # 핸드셰이크 타임아웃: 일시적, 재시도로 해결 가능
            logger.warning(f"TLS handshake timeout: {error.endpoint}")
        else:
            logger.error(f"TLS error: {error.error_type} - {error.error_message}")

    def _handle_tls_alert(self, error_info):
        """TLS 알림 콜백"""
        if error_info.severity == TLSErrorSeverity.CRITICAL:
            send_pagerduty_alert(error_info)
```

---

## 5. Celery 태스크 통합

### 5.1 권장 태스크 구조

```
your_app/
├── tasks/
│   ├── __init__.py
│   ├── infra_tasks.py      # 인프라 모니터링 (인증서, 헬스체크)
│   ├── security_tasks.py   # 보안 관련 (위반 보고서, 클린업)
│   └── selfhealing_tasks.py # DLQ 정리, 설정 동기화
```

### 5.2 인프라 태스크 예시

```python
# tasks/infra_tasks.py

from celery import shared_task
from selfhealing.core.cert_monitor import CertificateExpiryMonitor
from selfhealing.core.tls_handler import TLSErrorClassifier

@shared_task(name="infra.check_certificates")
def check_certificates():
    """인증서 만료 체크"""
    # ... 위 예시 참조

@shared_task(name="infra.cleanup_tls_error_logs")
def cleanup_tls_error_logs():
    """30일 이상 된 TLS 오류 로그 정리"""
    classifier = TLSErrorClassifier()
    deleted = classifier.cleanup_old_logs(days=30)
    return {"deleted": deleted}
```

### 5.3 Celery Beat 전체 설정

```python
# settings.py

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    # === 인프라 모니터링 ===
    'check-certificates': {
        'task': 'infra.check_certificates',
        'schedule': crontab(hour=9, minute=0),  # 매일 09:00
    },

    # === Self-Healing 정리 태스크 ===
    'cleanup-expired-dlq': {
        'task': 'selfhealing.cleanup_expired_dlq_entries',
        'schedule': crontab(hour=3, minute=0),  # 매일 03:00
    },
    'cleanup-old-audit-logs': {
        'task': 'selfhealing.cleanup_old_audit_entries',
        'schedule': crontab(hour=4, minute=0),  # 매일 04:00
    },

    # === 보안 태스크 ===
    'security-violation-report': {
        'task': 'security.generate_daily_report',
        'schedule': crontab(hour=8, minute=0),  # 매일 08:00
    },
}
```

---

## 5a. 시그널 및 이벤트 핸들러 통합

### 5a.1 Django 시그널 통합

Self-Healing 이벤트를 Django 시그널과 연동하는 방법입니다.

#### 커스텀 시그널 정의

```python
# your_app/signals.py

from django.dispatch import Signal

# Self-Healing 관련 커스텀 시그널
circuit_breaker_opened = Signal()  # sender, domain, failure_count
circuit_breaker_closed = Signal()  # sender, domain
emergency_mode_activated = Signal()  # sender, level
error_budget_exhausted = Signal()  # sender, domain, remaining_budget
```

#### 시그널 핸들러 구현

```python
# your_app/signal_handlers.py

from django.dispatch import receiver
from .signals import (
    circuit_breaker_opened,
    circuit_breaker_closed,
    emergency_mode_activated,
)

@receiver(circuit_breaker_opened)
def on_circuit_breaker_opened(sender, domain, failure_count, **kwargs):
    """Circuit Breaker OPEN 시 알림 발송"""
    from .notifications import send_ops_alert

    send_ops_alert(
        level="warning",
        title=f"Circuit Breaker OPEN: {domain}",
        message=f"연속 {failure_count}회 실패로 차단됨",
        channel="#ops-alerts"
    )

@receiver(emergency_mode_activated)
def on_emergency_activated(sender, level, **kwargs):
    """Emergency Mode 진입 시 비상 대응"""
    from .notifications import send_pagerduty_alert

    if level >= 2:
        send_pagerduty_alert(
            severity="critical",
            title=f"Emergency Mode Level {level} 활성화",
            dedup_key=f"emergency-level-{level}"
        )
```

#### apps.py에서 시그널 등록

```python
# your_app/apps.py

class YourAppConfig(AppConfig):
    def ready(self):
        # 시그널 핸들러 import (이것만으로 활성화됨!)
        import your_app.signal_handlers  # noqa

        # Self-Healing 이벤트 버스와 Django 시그널 연결
        self._connect_selfhealing_events()

    def _connect_selfhealing_events(self):
        """Self-Healing 내부 이벤트를 Django 시그널로 브릿지"""
        try:
            from selfhealing.services.event_bus import event_bus, EventType
            from .signals import (
                circuit_breaker_opened,
                circuit_breaker_closed,
                emergency_mode_activated,
            )

            def on_cb_open(event):
                circuit_breaker_opened.send(
                    sender=self.__class__,
                    domain=event.get("domain"),
                    failure_count=event.get("failure_count")
                )

            def on_emergency(event):
                emergency_mode_activated.send(
                    sender=self.__class__,
                    level=event.get("level")
                )

            event_bus.subscribe(EventType.CIRCUIT_BREAKER_OPENED, on_cb_open)
            event_bus.subscribe(EventType.EMERGENCY_ACTIVATED, on_emergency)

        except ImportError:
            pass  # selfhealing 패키지 미설치 시 무시
```

### 5a.2 Model 변경 추적

Self-Healing 설정 변경을 Django Model 시그널로 추적:

```python
# your_app/signals.py

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

@receiver(pre_save, sender='selfhealing.CircuitBreakerState')
def track_cb_state_change(sender, instance, **kwargs):
    """Circuit Breaker 상태 변경 추적"""
    if instance.pk:
        try:
            old = sender.objects.get(pk=instance.pk)
            if old.state != instance.state:
                log_state_transition(
                    domain=instance.domain,
                    from_state=old.state,
                    to_state=instance.state
                )
        except sender.DoesNotExist:
            pass
```
```

---

## 6. Audit CLI 도구 통합

### 6.1 개요

Audit 시스템의 고급 기능들은 **CLI 도구** 또는 **배치 작업**으로 통합합니다.
API 엔드포인트로 노출하지 않고, 관리자가 직접 실행하거나 cron/Celery Beat로 스케줄링합니다.

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Audit CLI 도구 통합 전략                               │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                           │
│  🔧 CLI 도구 (직접 실행)                                                  │
│  ──────────────────────                                                   │
│  • AuditExporter        - 감사 로그 내보내기                              │
│  • AuditIntegrityVerifier - 무결성 검증                                   │
│  • SignedManifest       - 법적 매니페스트 생성                            │
│                                                                           │
│  ⏰ 배치 작업 (Celery Beat / cron)                                        │
│  ─────────────────────────────────                                        │
│  • 일일 무결성 검증 (매일 03:00)                                          │
│  • 주간 WORM 백업 (매주 일요일)                                           │
│  • 분기별 매니페스트 생성 (Q1-Q4)                                         │
│                                                                           │
│  🔌 인프라 통합 (운영팀 설정)                                             │
│  ─────────────────────────────                                            │
│  • WriteAheadLog        - 분산 환경 장애 복구                             │
│  • AuditWatchdog        - 외부 모니터링 연동                              │
│  • WORMAdapter          - S3 Object Lock, Loki 등                         │
│                                                                           │
└──────────────────────────────────────────────────────────────────────────┘
```

### 6.2 AuditExporter 통합

#### 6.2.1 CLI 직접 실행

```bash
# 기본 JSON 내보내기
python -m selfhealing.audit.export \
    --format json \
    --output /backup/audit/$(date +%Y-%m-%d).json

# 특정 기간 내보내기
python -m selfhealing.audit.export \
    --format json \
    --start "2024-01-01" \
    --end "2024-12-31" \
    --output /backup/audit/2024-full.json

# CSV 형식 (Excel 분석용)
python -m selfhealing.audit.export \
    --format csv \
    --output /backup/audit/$(date +%Y-%m-%d).csv
```

#### 6.2.2 S3 Object Lock (WORM) 백업

```bash
# AWS S3 Object Lock으로 직접 내보내기
python -m selfhealing.audit.export \
    --target s3 \
    --bucket audit-archive-prod \
    --region ap-northeast-2 \
    --retention-days 2555  # 7년 보관
```

**환경 변수 설정**:
```bash
# AWS 자격 증명
export AWS_ACCESS_KEY_ID=xxx
export AWS_SECRET_ACCESS_KEY=xxx
export AWS_DEFAULT_REGION=ap-northeast-2

# S3 Object Lock 설정
export AUDIT_S3_BUCKET=audit-archive-prod
export AUDIT_S3_RETENTION_MODE=COMPLIANCE
export AUDIT_S3_RETENTION_DAYS=2555
```

#### 6.2.3 Grafana Loki 푸시

```bash
# Loki로 푸시 (실시간 분석용)
python -m selfhealing.audit.export \
    --target loki \
    --url http://loki.monitoring.svc:3100 \
    --labels "app=selfhealing,env=prod"
```

#### 6.2.4 Celery Beat 스케줄링

```python
# celery.py
CELERY_BEAT_SCHEDULE = {
    # 매일 새벽 3시에 S3로 백업
    "daily-audit-backup": {
        "task": "shopping.tasks.audit_tasks.backup_audit_to_s3",
        "schedule": crontab(hour=3, minute=0),
    },
    # 매시간 Loki로 푸시 (실시간에 가깝게)
    "hourly-loki-push": {
        "task": "shopping.tasks.audit_tasks.push_audit_to_loki",
        "schedule": crontab(minute=0),  # 매시 정각
    },
}
```

### 6.3 AuditIntegrityVerifier 통합

#### 6.3.1 CLI 직접 실행

```bash
# 전체 Audit 로그 무결성 검증
python -m selfhealing.audit.verify_audit_integrity

# 특정 기간만 검증
python -m selfhealing.audit.verify_audit_integrity \
    --start 2024-01-01 \
    --end 2024-12-31

# JSON 출력 (자동화용)
python -m selfhealing.audit.verify_audit_integrity \
    --format json \
    --output /var/log/audit/verification-$(date +%Y-%m-%d).json
```

#### 6.3.2 Celery Beat 일일 검증

```python
# celery.py
CELERY_BEAT_SCHEDULE = {
    # 매일 새벽 4시에 무결성 검증
    "daily-integrity-check": {
        "task": "shopping.tasks.audit_tasks.verify_audit_integrity",
        "schedule": crontab(hour=4, minute=0),
    },
}

# shopping/tasks/audit_tasks.py
from selfhealing.audit.verify_audit_integrity import AuditIntegrityVerifier

@app.task
def verify_audit_integrity():
    verifier = AuditIntegrityVerifier()
    result = verifier.verify_all()

    if not result.is_valid:
        # 알림 발송 (PagerDuty, Slack 등)
        notify_audit_breach(result.errors)

    return result.to_dict()
```

### 6.4 SignedManifest 통합 (컴플라이언스용)

#### 6.4.1 분기별 매니페스트 생성

```bash
# 분기별 매니페스트 생성 (법적 증거용)
python -m selfhealing.audit.signed_manifest \
    --period Q4-2024 \
    --private-key /etc/ssl/private/audit-signing.key \
    --timestamp-server "http://timestamp.digicert.com" \
    --output /audit/manifests/2024-Q4.manifest
```

#### 6.4.2 Celery Beat 분기별 스케줄

```python
# celery.py
CELERY_BEAT_SCHEDULE = {
    # 분기 첫 날 00:00에 이전 분기 매니페스트 생성
    "quarterly-manifest": {
        "task": "shopping.tasks.audit_tasks.generate_quarterly_manifest",
        "schedule": crontab(
            day_of_month=1,
            month_of_year="1,4,7,10",  # Q1, Q2, Q3, Q4 시작월
            hour=0,
            minute=0
        ),
    },
}
```

### 6.5 WriteAheadLog (HA 구성)

분산 환경에서 장애 복구를 위한 WAL 설정:

```python
# settings/production.py

# WAL 활성화 (고가용성 환경용)
SELFHEALING_WAL_ENABLED = True
SELFHEALING_WAL_DIRECTORY = "/var/lib/selfhealing/wal"
SELFHEALING_WAL_SYNC_MODE = "fsync"  # 가장 안전
SELFHEALING_WAL_MAX_SIZE_MB = 100
SELFHEALING_WAL_CLEANUP_INTERVAL_HOURS = 24
```

```python
# Django 앱 설정
# shopping/apps.py
from django.apps import AppConfig

class ShoppingConfig(AppConfig):
    def ready(self):
        from django.conf import settings

        if getattr(settings, 'SELFHEALING_WAL_ENABLED', False):
            from selfhealing.audit.wal import WriteAheadLog, WALConfig

            wal = WriteAheadLog(config=WALConfig(
                directory=settings.SELFHEALING_WAL_DIRECTORY,
                sync_mode=settings.SELFHEALING_WAL_SYNC_MODE,
            ))
            wal.start_recovery_check()  # 시작 시 복구 체크
```

### 6.6 AuditWatchdog (외부 모니터링)

```python
# settings/production.py

# Watchdog 설정 (Datadog, PagerDuty 등 연동)
SELFHEALING_WATCHDOG_ENABLED = True
SELFHEALING_WATCHDOG_HEARTBEAT_URL = "https://api.pagerduty.com/heartbeat/xxx"
SELFHEALING_WATCHDOG_INTERVAL_SECONDS = 60
SELFHEALING_WATCHDOG_ALERT_CHANNELS = ["pagerduty", "slack"]
```

```python
# 별도 프로세스로 실행 (Supervisor 등)
# /etc/supervisor/conf.d/audit-watchdog.conf
"""
[program:audit-watchdog]
command=/app/venv/bin/python -m selfhealing.audit.audit_watchdog
directory=/app
user=www-data
autostart=true
autorestart=true
startsecs=10
stopwaitsecs=60
environment=DJANGO_SETTINGS_MODULE="myproject.settings.production"
"""
```

---

## 7. 환경별 설정 예시

### 6.1 Development 환경

```python
# settings/development.py

from .base import *

# 최소 미들웨어만 활성화
MIDDLEWARE = [
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # Django core...
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",

    # Pool 디버깅용
    "myproject.middleware.pool_timeout_middleware.PoolTimeoutMiddleware",
]

# Tiering 비활성화
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = False
```

### 6.2 Staging 환경

```python
# settings/staging.py

from .base import *

# 프로덕션과 유사하게 설정
MIDDLEWARE = [
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # Django core...

    # === Staging에서 테스트 ===
    "selfhealing.api.django.tiering.TieringMiddleware",
    "selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware",
    "myproject.middleware.actor_middleware.ActorContextMiddleware",

    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
]
```

### 6.3 Production 환경

```python
# settings/production.py

from .base import *

MIDDLEWARE = [
    # === Self-Healing (최상단) ===
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.middleware.SelfHealingMiddleware",

    # === Django Core ===
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    # === Actor 추적 (인증 이후) ===
    "myproject.middleware.actor_middleware.ActorContextMiddleware",

    # === 컴플라이언스 ===
    "selfhealing.api.django.middleware.SensitiveAccessLoggingMiddleware",

    # === Load Shedding ===
    "selfhealing.api.django.tiering.TieringMiddleware",

    # === Rate Limiting ===
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
]

# 모든 기능 활성화
SELFHEALING_TIERING_MIDDLEWARE_ENABLED = True
```

---

## 📚 관련 문서

- [53_UNCONNECTED_FEATURES_ANALYSIS.md](53_UNCONNECTED_FEATURES_ANALYSIS.md) - 미연결 기능 분석
- [55_FEATURE_DISCOVERY_STRATEGY.md](55_FEATURE_DISCOVERY_STRATEGY.md) - 기능 탐색 전략
- [37_AUDIT_SYSTEM.md](37_AUDIT_SYSTEM.md) - Audit 시스템 전체 문서
- [09_CONFIGURATION.md](09_CONFIGURATION.md) - 전체 설정 가이드
- [10_OPERATIONS_GUIDE.md](10_OPERATIONS_GUIDE.md) - 운영 가이드
- [07_CONTROL_API.md](07_CONTROL_API.md) - Control API 문서

---

*문서 끝*

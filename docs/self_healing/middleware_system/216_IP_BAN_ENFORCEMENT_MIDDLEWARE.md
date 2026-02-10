# 216. IP Ban Enforcement Middleware 구현

> **문서 번호**: 216
> **분류**: Security - Critical Gap Fix
> **선행 문서**: 214_SECURITY_VULNERABILITY_FIXES_PART1, 215_SECURITY_VULNERABILITY_FIXES_PART2
> **작성일**: 2026-02-10
> **심각도**: CRITICAL

---

## 1. 문제 정의

### 1.1 발견된 취약점

**IP를 Redis에 ban으로 기록하지만, ban된 IP의 후속 요청을 거부하는 미들웨어가 존재하지 않는다.**

### 1.2 코드 근거

#### Ban 기록 함수 (존재함)

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/service.py`

```python
# L440-L460: 임시 IP 차단
def _temporary_ip_ban(self, ip_address: str, hours: int = 1) -> str:
    cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
    self.cache.set(
        cache_key,
        {"banned": True, "type": "temporary"},
        ttl=timedelta(hours=hours),
    )
    # ...

# L462-L477: 영구 IP 차단
def _permanent_ip_ban(self, ip_address: str) -> str:
    cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
    self.cache.set(cache_key, {"banned": True, "type": "permanent"}, ttl=None)
    # ...
```

#### Ban 확인 함수 (존재함)

```python
# L486-L490: 차단 여부 확인
def is_ip_banned(self, ip_address: str) -> bool:
    cache_key = f"{self.config.banned_ip_cache_prefix}{ip_address}"
    ban_info = self.cache.get(cache_key)
    return ban_info is not None and ban_info.get("banned", False)
```

#### Ban 호출 트리거 (존재함)

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/policies.py`

```python
# L71-L96: ViolationType → ActionPolicy 매핑에서 IP ban 사용하는 위반 유형:
ViolationType.TOKEN_FORGED → IP_TEMPORARY_BAN
ViolationType.SIGNATURE_INVALID → IP_TEMPORARY_BAN
ViolationType.REPLAY_ATTACK → IP_TEMPORARY_BAN
ViolationType.DATA_TAMPERED → IP_PERMANENT_BAN
ViolationType.INJECTION_ATTEMPT → IP_TEMPORARY_BAN
ViolationType.RATE_LIMIT_ABUSE → IP_TEMPORARY_BAN
ViolationType.ANOMALY_BEHAVIORAL → IP_TEMPORARY_BAN
ViolationType.AUDIT_TAMPERING → IP_PERMANENT_BAN
ViolationType.PRIVILEGE_ESCALATION → EMERGENCY_LEVEL_2 + SESSION_INVALIDATE + ACCOUNT_FREEZE
```

#### Ban 강제 적용 미들웨어 (존재하지 않음)

```
grep 결과: "is_ip_banned" → 전체 codebase에서 정의 1곳 (service.py L486)만 존재
미들웨어(api/django/middleware/)에서 호출하는 코드: 0건
```

### 1.3 결과

IP가 ban 처리되어도 해당 IP의 후속 요청이 정상적으로 처리된다. ban은 Redis에 기록만 될 뿐, **실제 차단이 이루어지지 않는다**.

---

## 2. 해결 방안

### 2.1 설계 원칙

1. **selfhealing 패키지 독립성 유지**: Django에 의존하지만 selfhealing 패키지 내부에 위치
2. **기존 패턴 준수**: `SecurityViolationService.is_ip_banned()` 재사용
3. **미들웨어 위치**: Django core security middleware 이전, 가능한 초기 단계에서 차단
4. **Fail-Open vs Fail-Closed**: Redis 장애 시 요청 허용 (Fail-Open) - 가용성 우선

### 2.2 미들웨어 위치 결정

**현재 미들웨어 스택** (`myproject/settings/base.py` L82-L168):

```
[0] PrometheusBeforeMiddleware     ← 메트릭 수집 시작
[1] trace_id_middleware            ← 분산 추적
[2] HealthBridgeMiddleware         ← 헬스체크 (항상 통과)
[3] TieringMiddleware              ← Emergency Mode 트래픽 제어
[4] SelfHealingMiddleware          ← CB + DLQ
[5] ActorContextMiddleware         ← 사용자 추적
[6] Django Core (Security, Session, CSRF, Auth, etc.)
[7] HybridRateLimitMiddleware      ← Rate Limit (Control API only)
[8] PoolCircuitBreakerMiddleware
[9] PoolTimeoutMiddleware
[10] ChaosMiddleware
[11] AuditMiddleware               ← 감사 로그 (항상 마지막)
[12] PrometheusAfterMiddleware     ← 메트릭 수집 완료
```

**IP Ban 미들웨어 삽입 위치**: **[3]과 [4] 사이** (TieringMiddleware 다음)

이유:
- HealthBridge보다 뒤: K8s probe는 IP ban 영향 받으면 안 됨
- Tiering보다 뒤: Emergency Mode에서는 어차피 트래픽 제한됨
- SelfHealingMiddleware보다 앞: ban된 IP의 요청이 DLQ에 적재되면 안 됨
- Django Auth보다 앞: 인증 처리 전에 차단해야 리소스 절약

---

## 3. 구현 명세

### 3.1 신규 파일

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/middleware/ip_ban.py`

```python
"""
IP Ban Enforcement Middleware.

Redis에 기록된 IP ban을 실제 HTTP 요청 단계에서 강제 적용합니다.

문제 배경 (216_IP_BAN_ENFORCEMENT_MIDDLEWARE):
- SecurityViolationService._temporary_ip_ban()과 _permanent_ip_ban()이
  Redis에 ban을 기록하지만, 후속 요청을 차단하는 미들웨어가 없었음
- is_ip_banned()가 정의만 되어 있고 호출되는 곳이 없었음

설계:
- FAIL-OPEN: Redis 장애 시 요청 허용 (가용성 우선)
- 헬스체크 경로 면제: /health/ 경로는 ban 대상에서 제외
- IP 추출: X-Forwarded-For → X-Real-IP → REMOTE_ADDR 순서

미들웨어 위치 (base.py MIDDLEWARE):
    [3] TieringMiddleware 다음
    [4] SelfHealingMiddleware 이전

Usage in settings.py:
    MIDDLEWARE = [
        ...
        "selfhealing.api.django.tiering.TieringMiddleware",
        "selfhealing.api.django.middleware.IPBanMiddleware",  # ← 신규
        "selfhealing.api.django.middleware.SelfHealingMiddleware",
        ...
    ]
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from django.http import HttpRequest, HttpResponse

logger = logging.getLogger(__name__)


class IPBanMiddleware:
    """
    IP Ban 강제 적용 미들웨어.

    SecurityViolationService가 Redis에 기록한 IP ban 정보를 확인하여
    ban된 IP의 요청을 403으로 거부합니다.

    Redis 키 패턴: security:banned_ip:{ip_address}
    Redis 값: {"banned": True, "type": "temporary"|"permanent"}

    Fail-Open: Redis 조회 실패 시 요청을 허용합니다.
    """

    # 헬스체크 경로 면제 (K8s probe, ELB health check 등)
    EXEMPT_PATH_PREFIXES = (
        "/health/",
        "/readiness/",
        "/liveness/",
    )

    def __init__(self, get_response):
        self.get_response = get_response
        self._cache = None
        self._config = None
        self._initialized = False

    def _lazy_init(self) -> None:
        """Lazy initialization to avoid circular imports at module load."""
        if self._initialized:
            return

        try:
            from selfhealing.services.security.models import SecurityConfig
            self._config = SecurityConfig.from_settings()
        except Exception as e:
            logger.warning(f"[IPBanMiddleware] Config init failed: {e}")
            self._config = None

        try:
            from selfhealing.factory import ProviderRegistry
            self._cache = ProviderRegistry.get_cache()
        except Exception as e:
            logger.debug(f"[IPBanMiddleware] Cache init failed (will retry): {e}")
            self._cache = None

        self._initialized = True

    def _get_cache(self):
        """Get cache provider, retrying if initial load failed."""
        if self._cache is not None:
            return self._cache

        try:
            from selfhealing.factory import ProviderRegistry
            self._cache = ProviderRegistry.get_cache()
        except Exception:
            pass

        return self._cache

    def _get_banned_ip_prefix(self) -> str:
        """Get banned IP cache prefix from config."""
        if self._config is not None:
            return self._config.banned_ip_cache_prefix
        # SecuritySettings의 기본값과 동일 (settings/security.py L113)
        return "security:banned_ip:"

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from django.http import JsonResponse

        self._lazy_init()

        # 헬스체크 경로 면제
        if any(request.path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        # IP 추출 (masking.py의 get_client_ip와 동일한 로직)
        client_ip = self._get_client_ip(request)

        # ban 여부 확인
        ban_info = self._check_ip_ban(client_ip)

        if ban_info is not None:
            ban_type = ban_info.get("type", "unknown")
            logger.warning(
                f"[IPBanMiddleware] Blocked banned IP: "
                f"type={ban_type}, path={request.path}"
            )

            return JsonResponse(
                {
                    "error": "Access denied",
                    "code": "IP_BANNED",
                    "ban_type": ban_type,
                },
                status=403,
            )

        return self.get_response(request)

    def _get_client_ip(self, request: HttpRequest) -> str:
        """
        클라이언트 IP 추출.

        audit/masking.py의 get_client_ip()와 동일한 순서:
        1. X-Forwarded-For (프록시/로드밸런서)
        2. X-Real-IP (리버스 프록시)
        3. REMOTE_ADDR (직접 연결)
        """
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        x_real_ip = request.META.get("HTTP_X_REAL_IP")
        if x_real_ip:
            return x_real_ip.strip()

        return request.META.get("REMOTE_ADDR", "unknown")

    def _check_ip_ban(self, ip_address: str) -> dict[str, Any] | None:
        """
        Redis에서 IP ban 정보 조회.

        Returns:
            ban 정보 dict (banned인 경우) or None (미차단/조회실패)

        FAIL-OPEN: Redis 조회 실패 시 None 반환 (요청 허용)
        """
        cache = self._get_cache()
        if cache is None:
            return None

        try:
            prefix = self._get_banned_ip_prefix()
            cache_key = f"{prefix}{ip_address}"
            ban_info = cache.get(cache_key)

            if ban_info is not None and isinstance(ban_info, dict):
                if ban_info.get("banned", False):
                    return ban_info

            return None

        except Exception as e:
            # FAIL-OPEN: Redis 장애 시 요청 허용
            logger.debug(f"[IPBanMiddleware] Cache check failed (fail-open): {e}")
            return None
```

### 3.2 `__init__.py` 수정

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/middleware/__init__.py`

```python
# 기존 import 블록 끝에 추가:

# ============================================================
# IP Ban Enforcement
# ============================================================
from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware
```

`__all__`에 `"IPBanMiddleware"` 추가.

### 3.3 `base.py` MIDDLEWARE 수정

**파일**: `myproject/settings/base.py` L82-L168

```python
MIDDLEWARE = [
    "django_prometheus.middleware.PrometheusBeforeMiddleware",
    "selfhealing.audit.trace.trace_id_middleware",
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    "selfhealing.api.django.tiering.TieringMiddleware",
    # ========================================================================
    # [3.5] IP Ban Enforcement (banned IP 즉시 차단)
    # ========================================================================
    # Redis에 기록된 IP ban을 HTTP 요청 단계에서 강제 적용
    # FAIL-OPEN: Redis 장애 시 요청 허용
    # Reference: 216_IP_BAN_ENFORCEMENT_MIDDLEWARE
    "selfhealing.api.django.middleware.IPBanMiddleware",  # ← 신규
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # ... 나머지 동일
]
```

---

## 4. Rate Limit 범위 확장 분석

### 4.1 현재 상태

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py`

```python
# L67: Control API만 보호
_FALLBACK_CONTROL_API_PATH_PREFIX = "/api/self-healing/"

# L569-L570: __call__에서 Control API가 아니면 즉시 통과
def __call__(self, request: HttpRequest) -> HttpResponse:
    control_api_prefix = _get_setting("control_api_path_prefix", _FALLBACK_CONTROL_API_PATH_PREFIX)
    if not request.path.startswith(control_api_prefix):
        return self.get_response(request)
```

### 4.2 이것이 의도적인 이유 (추측이 아닌 코드 근거)

`HybridRateLimitMiddleware`는 selfhealing 패키지의 **Control API**(`/api/self-healing/`)만을 보호하기 위해 설계되었다:

1. **클래스명**: `HybridRateLimitMiddleware` - "하이브리드"는 Redis+Local Memory failover를 의미
2. **경로 필터**: `_FALLBACK_CONTROL_API_PATH_PREFIX = "/api/self-healing/"` (L67)
3. **설정 키명**: `control_api_rate_limit`, `control_api_window_seconds` (L589-L590)

비즈니스 API(`/api/orders/`, `/api/payments/` 등)의 rate limiting은 **selfhealing 패키지의 책임 범위가 아니다**. 이것은 호스트 앱(shopping)이 별도로 구현해야 할 영역이다.

### 4.3 결론

**비즈니스 API rate limiting 부재는 selfhealing 패키지의 취약점이 아니다.** selfhealing의 `HybridRateLimitMiddleware`는 자기 API만 보호하는 것이 올바른 설계이다. 따라서 이 문서에서는 별도 구현을 제안하지 않는다.

---

## 5. IP 추출 로직 일원화

### 5.1 현재 상태: 동일 로직 2곳 중복

| 위치 | 함수명 | 로직 |
|---|---|---|
| `audit/masking.py` L393-L410 | `get_client_ip(request)` | X-Forwarded-For → X-Real-IP → REMOTE_ADDR |
| 본 문서 신규 IPBanMiddleware | `_get_client_ip(request)` | 동일 |

### 5.2 권장 사항

IPBanMiddleware에서 `audit.masking.get_client_ip()`를 직접 import하면 순환 의존성 위험이 있다 (middleware → audit → 기타 모듈). 따라서:

1. **1단계 (본 문서)**: IPBanMiddleware 내에 `_get_client_ip()` 인라인 구현
2. **후속 리팩토링**: `selfhealing.utils.network` 모듈로 IP 추출 로직을 분리하여 양쪽에서 import

---

## 6. 테스트 명세

### 6.1 단위 테스트

**파일**: `tests/self_healing/django/test_ip_ban_middleware.py`

```python
class TestIPBanMiddleware:
    """IPBanMiddleware 단위 테스트."""

    def test_banned_ip_returns_403(self):
        """ban된 IP의 요청이 403으로 거부되는지 확인."""

    def test_non_banned_ip_passes_through(self):
        """ban되지 않은 IP의 요청이 정상 통과하는지 확인."""

    def test_health_check_exempt(self):
        """헬스체크 경로가 ban에서 면제되는지 확인."""

    def test_redis_failure_fail_open(self):
        """Redis 장애 시 요청이 허용되는지 확인 (Fail-Open)."""

    def test_temporary_ban_type_in_response(self):
        """응답에 ban_type이 포함되는지 확인."""

    def test_permanent_ban_type_in_response(self):
        """영구 ban의 ban_type이 정확한지 확인."""

    def test_x_forwarded_for_ip_extraction(self):
        """X-Forwarded-For에서 IP가 올바르게 추출되는지 확인."""

    def test_lazy_init_only_once(self):
        """lazy init이 한 번만 실행되는지 확인."""
```

### 6.2 통합 테스트

```python
class TestIPBanIntegration:
    """IP Ban → Middleware 연동 통합 테스트."""

    def test_violation_triggers_ban_then_middleware_blocks(self):
        """
        1. SecurityViolationService.handle_violation(INJECTION_ATTEMPT) 호출
        2. _temporary_ip_ban()으로 Redis에 ban 기록
        3. 동일 IP의 후속 HTTP 요청이 403으로 차단됨
        """

    def test_ban_expiry_allows_request(self):
        """
        1. 임시 ban (TTL 1시간) 기록
        2. TTL 만료 후 요청이 다시 허용됨
        """
```

---

## 7. 영향 분석

### 7.1 수정 대상 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| `api/django/middleware/ip_ban.py` | **신규** | IPBanMiddleware 구현 |
| `api/django/middleware/__init__.py` | 수정 | IPBanMiddleware export 추가 |
| `myproject/settings/base.py` | 수정 | MIDDLEWARE 리스트에 삽입 |

### 7.2 하위 호환성

- **신규 미들웨어 추가**: 기존 코드 변경 없음
- **Redis 의존성**: 이미 존재하는 `banned_ip_cache_prefix` 키 패턴 재사용
- **Fail-Open**: Redis 없는 환경에서도 기존 동작 유지

### 7.3 성능 영향

- **모든 요청**에 Redis GET 1회 추가 (O(1), ~0.1ms)
- 헬스체크 경로는 Redis 조회 없이 즉시 통과
- 비교: `HybridRateLimitMiddleware`도 매 요청마다 Redis 조회 수행 중

# 216. IP Ban Enforcement Middleware 구현

> **문서 번호**: 216
> **분류**: Security - Critical Gap Fix
> **선행 문서**: 214_SECURITY_VULNERABILITY_FIXES_PART1, 215_SECURITY_VULNERABILITY_FIXES_PART2
> **작성일**: 2026-02-10
> **구현일**: 2026-02-11
> **심각도**: CRITICAL
> **상태**: ✅ 구현 완료

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
- IP 추출: selfhealing.utils.network.extract_client_ip() 재사용 (프로젝트 표준)
- 응답 최소화: 403 응답에 ban_type 미포함 (공격자 정보 노출 방지)

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
from typing import TYPE_CHECKING, Any

from selfhealing.utils.network import extract_client_ip

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
    # 참고: /health/는 nginx.conf에서 직접 응답하여 Django 미도달이나, 방어적으로 유지
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
        """Get banned IP cache prefix from config.

        CRITICAL: SecurityViolationService._temporary_ip_ban()/_permanent_ip_ban()과
        반드시 동일한 키 프리픽스를 사용해야 함. 변경 시 ban 조회 불가 버그 발생.
        """
        if self._config is not None:
            return self._config.banned_ip_cache_prefix
        # SecurityConfig 기본값과 동일 (models.py L112, settings/security.py L113)
        return "security:banned_ip:"

    def __call__(self, request: HttpRequest) -> HttpResponse:
        from django.http import JsonResponse

        self._lazy_init()

        # 헬스체크 경로 면제
        if any(request.path.startswith(prefix) for prefix in self.EXEMPT_PATH_PREFIXES):
            return self.get_response(request)

        # IP 추출 (프로젝트 표준: selfhealing.utils.network.extract_client_ip)
        client_ip = extract_client_ip(request, default="unknown")

        # ban 여부 확인
        ban_info = self._check_ip_ban(client_ip)

        if ban_info is not None:
            ban_type = ban_info.get("type", "unknown")
            logger.warning(
                f"[IPBanMiddleware] Blocked banned IP: "
                f"type={ban_type}, path={request.path}"
            )

            # 보안: ban_type을 응답에 포함하지 않음 (공격자 정보 노출 방지)
            # ban_type은 로그에만 기록
            return JsonResponse(
                {
                    "error": "Access denied",
                    "code": "IP_BANNED",
                },
                status=403,
            )

        return self.get_response(request)

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

## 5. IP 추출 로직 일원화 (리뷰 반영: utils.network 재사용)

### 5.1 결정: `selfhealing.utils.network.extract_client_ip()` 직접 사용

**원래 문서**: `_get_client_ip()` 인라인 구현 후 후속 리팩토링으로 `utils.network` 분리 예정

**리뷰 반영**: `selfhealing.utils.network.extract_client_ip()`가 **이미 존재**하므로 즉시 사용. `_get_client_ip()` 메서드 제거.

### 5.2 코드 근거

**파일**: `packages/selfhealing-python/src/selfhealing/utils/network.py`

```python
"""
Network Utilities.

All modules requiring client IP should use ``extract_client_ip``
to ensure consistent behaviour across audit, permission, actor context,
and canary feature-flag subsystems.
"""

def extract_client_ip(request: Any, *, default: str | None = None) -> str | None:
    meta = getattr(request, "META", None) or {}
    # 1) X-Forwarded-For – first entry is the original client
    # 2) X-Real-IP (nginx convention)
    # 3) REMOTE_ADDR – direct connection fallback
    ...
```

**사용 선례** (`context/actor_context.py` L269-L271):

```python
from selfhealing.utils.network import extract_client_ip
return extract_client_ip(request)
```

### 5.3 순환 참조 안전성 분석

`selfhealing.utils.network` 모듈의 import 체인:

```
utils/network.py
  └── from __future__ import annotations
  └── from typing import Any
  └── (외부 의존성 없음)
```

**결론**: 순수 유틸리티 모듈이므로 모듈 상단에서 직접 import 가능. lazy import 불필요.

### 5.4 IP Spoofing 대응

리뷰에서 `X-Forwarded-For` 첫 번째 값 신뢰의 위험성이 지적됨:

> 클라이언트가 `X-Forwarded-For: 1.2.3.4`를 조작하면, Nginx가 `1.2.3.4, real_ip`로 전달.
> `split(",")[0]`으로 조작된 `1.2.3.4`가 사용될 수 있음.

**분석 결과 — 미들웨어 단독 변경 불가**:

1. **Nginx 설정** (`nginx/nginx.conf` L69-L72): `X-Real-IP $remote_addr` 설정으로 Nginx 직접 연결 IP 보존
2. **시스템 전체 일관성**: `extract_client_ip`, `actor_context`, `access_logging`, `audit/masking` 모두 동일 순서 사용
3. **IPBanMiddleware만 독자적으로 변경하면**: ban 기록 시의 IP(SecurityViolationService)와 조회 시의 IP(IPBanMiddleware)가 불일치 → **ban 우회 버그 발생**

**결론**: IP Spoofing 방지는 인프라 레벨(Nginx에서 `X-Forwarded-For` 덮어쓰기 또는 trusted proxy 계층 고정)에서 해결해야 함. 미들웨어 단독 변경은 시스템 IP 불일치를 유발.

### 5.5 후속 리팩토링 대상

`access_logging.py`의 `_get_client_ip()`도 아직 `extract_client_ip`를 사용하지 않음 (인라인 구현 유지 중). 별도 PR에서 정리 권장.

---

## 6. 테스트 명세 (리뷰 반영: Mock 전략 구체화)

### 6.1 Mock 전략

프로젝트 표준 패턴에 따라 (`fakeredis` 미사용):

| 유형 | 방식 | 근거 |
|---|---|---|
| **단위 테스트** | `middleware._cache = MagicMock()` 직접 주입 | `test_security_violation_service.py` L432-L433 패턴 |
| **통합 테스트** | `ProviderRegistry.override_provider("cache", mock)` | `test_provider_registry_isolation.py` 패턴 |

### 6.2 단위 테스트

**파일**: `tests/self_healing/django/test_ip_ban_middleware.py`

```python
from unittest.mock import MagicMock, Mock

import pytest


class TestIPBanMiddleware:
    """IPBanMiddleware 단위 테스트."""

    def _make_middleware(self, ban_info=None, cache_error=False):
        """테스트용 미들웨어 팩토리.

        Mock 주입 패턴: test_security_violation_service.py L432-L433과 동일.
        """
        from selfhealing.api.django.middleware.ip_ban import IPBanMiddleware

        mock_response = Mock()
        middleware = IPBanMiddleware(get_response=lambda r: mock_response)

        mock_cache = MagicMock()
        if cache_error:
            mock_cache.get.side_effect = Exception("Redis down")
        else:
            mock_cache.get.return_value = ban_info

        middleware._cache = mock_cache
        middleware._initialized = True
        return middleware, mock_response

    def test_banned_ip_returns_403(self):
        """ban된 IP의 요청이 403으로 거부되는지 확인."""

    def test_non_banned_ip_passes_through(self):
        """ban되지 않은 IP의 요청이 정상 통과하는지 확인."""

    def test_health_check_exempt(self):
        """헬스체크 경로가 ban에서 면제되는지 확인."""

    def test_redis_failure_fail_open(self):
        """Redis 장애 시 요청이 허용되는지 확인 (Fail-Open)."""

    def test_response_does_not_expose_ban_type(self):
        """403 응답에 ban_type이 노출되지 않는지 확인 (보안)."""

    def test_ban_type_logged_in_warning(self):
        """ban_type이 로그에는 기록되는지 확인."""

    def test_extract_client_ip_integration(self):
        """extract_client_ip가 올바르게 호출되는지 확인."""

    def test_lazy_init_only_once(self):
        """lazy init이 한 번만 실행되는지 확인."""

    def test_cache_retry_on_initial_failure(self):
        """초기 캐시 로드 실패 시 _get_cache()에서 재시도하는지 확인."""
```

### 6.3 통합 테스트

```python
from unittest.mock import MagicMock

from selfhealing.factory import ProviderRegistry


class TestIPBanIntegration:
    """IP Ban → Middleware 연동 통합 테스트.

    ProviderRegistry.override_provider를 사용한 격리된 테스트 환경.
    """

    def test_violation_triggers_ban_then_middleware_blocks(self):
        """
        mock_cache = MagicMock()
        with ProviderRegistry.override_provider("cache", mock_cache):
            1. SecurityViolationService.handle_violation(INJECTION_ATTEMPT) 호출
            2. _temporary_ip_ban()으로 Redis에 ban 기록
            3. 동일 IP의 후속 HTTP 요청이 403으로 차단됨
        """

    def test_ban_expiry_allows_request(self):
        """
        1. 임시 ban (TTL 1시간) 기록
        2. TTL 만료 후 요청이 다시 허용됨
        """

    def test_key_prefix_matches_security_violation_service(self):
        """
        IPBanMiddleware와 SecurityViolationService가
        동일한 Redis 키 프리픽스(security:banned_ip:)를 사용하는지 확인.
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

---

## 8. 리뷰 반영 요약

> 리뷰 일자: 2026-02-11

### 8.1 반영된 항목

| # | 리뷰 항목 | 결정 | 근거 |
|---|---|---|---|
| 1 | 순환 참조 방지 전략 강화 | **현행 유지** | `SecurityConfig` → `SecuritySettings` → pydantic 체인에 `api.django` 참조 없음. `SelfHealingMiddleware._lazy_init()` (self_healing.py L81) 패턴과 동일. `ProviderRegistry` (factory.py L41)도 middleware 미참조 |
| 2 | IP Spoofing 방지 | **인프라 레벨에서 해결** | 미들웨어만 변경 시 `SecurityViolationService`의 ban 기록 IP와 불일치 발생. §5.4 상세 분석 참조 |
| 3 | 헬스체크 경로 유연성 | **하드코딩 유지** | `HealthBridgeMiddleware.BRIDGE_PATHS` (health_bridge.py L62-L67) 동일 패턴. `nginx.conf` L108-L111에서 `/health/` 직접 응답하므로 Django 미도달 |
| 4 | IP 추출 로직 재사용 | **`extract_client_ip` 사용** | `utils/network.py` 모듈이 이 목적으로 생성됨 (docstring: "All modules requiring client IP should use..."). `_get_client_ip()` 제거. §5 전면 개정 |
| 5 | Lazy Init 강화 | **현행 유지 + 주석 보강** | config/cache 분리 로딩 이미 구현됨. `_get_banned_ip_prefix()` fallback 기본값에 CRITICAL 주석 추가 |
| 6 | Redis Key Prefix 확인 | **`security:banned_ip:` 유지** | `SecurityViolationService`와 키 공유 필수. `_get_banned_ip_prefix()`에 CRITICAL 경고 주석 추가 |
| 7 | 테스트 Mock 전략 | **`MagicMock` + `override_provider`** | `fakeredis` 미사용 (프로젝트 미도입). §6.1 Mock 전략 표 참조 |

### 8.2 추가 보안 강화

| 변경 | 이유 | 코드 근거 |
|---|---|---|
| 403 응답에서 `ban_type` 제거 | 공격자에게 ban이 임시/영구인지 정보 노출 방지 | `ban_type`은 `logger.warning`에만 기록 |
| `import time` 제거 | 미사용 import 제거 | 원본 코드에서 `time` 사용처 0건 |
| `EXEMPT_PATH_PREFIXES` 주석 보강 | `/health/`는 `nginx.conf`에서 직접 응답함을 명시 | `nginx.conf` L108-L111: `return 200 "OK\n"` |
| `_get_banned_ip_prefix()` CRITICAL 주석 | 키 프리픽스 임의 변경 시 ban 무효화 버그 방지 | `service.py` L429, L456, L479, L488에서 동일 프리픽스 사용 |

### 8.3 선택 이유 요약

#### 리뷰 1 (순환 참조) — 현행 유지

`SecurityConfig.from_settings()` 호출 체인: `models.py` → `settings/__init__.py` → `settings/security.py(pydantic)`. 이 체인에서 `selfhealing.api.django.*`를 참조하는 곳이 없으므로 순환 참조 불가. `ProviderRegistry.get_cache()`도 `factory.py`에서 adapter 클래스만 참조할 뿐 middleware를 import하지 않음. 추가 방어 코드는 과잉 설계.

#### 리뷰 2 (IP Spoofing) — 인프라 레벨 해결

`X-Forwarded-For`의 **마지막** IP를 신뢰하는 방식으로 변경하면, `SecurityViolationService`가 ban 기록 시 사용한 IP(첫 번째)와 불일치함. ban은 `service.py`에서 `request_info.get("ip")`로 추출된 IP에 대해 수행되는데, 이 IP가 동일한 `X-Forwarded-For` 첫 번째 값임. 따라서 조회도 같은 로직을 사용해야 함.

#### 리뷰 3 (헬스체크 유연성) — 하드코딩 유지

설정 주입 방식의 장점(유연성)보다 단점(복잡성 증가, 기존 미들웨어와 불일치)이 큼. `HealthBridgeMiddleware`, `TieringMiddleware` 등 **기존 미들웨어 중 설정에서 면제 경로를 주입받는 것이 하나도 없음**. 또한 `EXEMPT_PATH_PREFIXES`의 3개 경로는 모두 Nginx 또는 HealthBridge에서 먼저 처리되므로 실질적으로 도달하지 않음.

#### 리뷰 4 (extract_client_ip) — 즉시 사용

216 원본 문서의 §5.2에서 "후속 리팩토링으로 `utils.network` 분리"라고 했으나, `utils/network.py`는 **이미 존재**하며 `actor_context.py`에서 사용 중. "후속 리팩토링"이 아니라 "이미 완료된 리팩토링"을 활용하는 것. 인라인 구현은 DRY 위반.

#### 리뷰 7 (테스트 Mock) — MagicMock + override_provider

`test_security_violation_service.py`에서 `MagicMock()` 직접 주입, `test_provider_registry_isolation.py`에서 `override_provider` 컨텍스트 매니저 사용이 이미 검증된 표준. `fakeredis`는 `requirements-dev.txt`에 없으며 프로젝트에서 미사용.

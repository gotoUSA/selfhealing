# 218. 세션 성능 개선, 캐시 키 상수화, 레거시 해시 호환, 의존성 수정

> **문서 번호**: 218
> **분류**: Security - Performance / Hardening / Dependency Fix
> **선행 문서**: 214, 215, 216, 217
> **작성일**: 2026-02-10
> **최종 수정**: 2026-02-11 (리뷰 #1-#6 반영, 구현 완료)
> **심각도**: HIGH (세션), MEDIUM (캐시 키, 레거시 해시), LOW (의존성)
> **구현 상태**: ✅ 완료 (2026-02-11)

---

## 1. 취약점 #1: 세션 무효화 전체 테이블 스캔

### 1.1 문제 정의

`_invalidate_user_sessions()`가 Django `Session` 테이블의 모든 활성 세션을 순회하면서 `_auth_user_id`를 비교한다. `django_session` 테이블에는 `user_id` 컬럼이 없어 Python 레벨 디코딩이 필수이다.

### 1.2 코드 근거

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/service.py` L354-L369

```python
# 3. Django 세션 백엔드가 있으면 DB 세션도 삭제
try:
    from django.contrib.sessions.models import Session
    from django.utils import timezone as dj_timezone

    active_sessions = Session.objects.filter(expire_date__gte=dj_timezone.now())
    deleted_count = 0
    for session in active_sessions:              # ← 전체 active session 순회
        data = session.get_decoded()              # ← Python 레벨 디코딩
        if str(data.get("_auth_user_id")) == str(user_id):
            session.delete()
            deleted_count += 1
```

`django_session` 테이블 스키마 (Django 기본):
- `session_key` (PK)
- `session_data` (TEXT, base64 encoded)
- `expire_date` (DATETIME, indexed)
- **`user_id` 컬럼 없음**

### 1.3 현재 세션 백엔드

**파일**: `myproject/settings/base.py`, `production.py`, `local.py`

```
SESSION_ENGINE 설정: 전체 settings 파일에서 0건 (grep 확인)
→ Django 기본값 사용: "django.contrib.sessions.backends.db"
```

Redis 캐시는 이미 구성됨:

**파일**: `myproject/settings/production.py` L67-L79

```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://redis:6379/1"),
        # ...
    }
}
```

**파일**: `myproject/settings/local.py` L108-L117

```python
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://localhost:6379/1",
        # ...
    }
}
```

### 1.4 해결 방안: Redis 세션 백엔드 전환

> **참고**: 215 문서 섹션 6.2.6에서 3가지 방안(A: Redis 세션, B: Custom Session Model, C: 매핑 테이블)을 분석하고 **방안 A**를 선택함. 본 문서는 구현 명세.

#### 구현 내용

**파일 1**: `myproject/settings/production.py`

```python
# 기존 CACHES 설정 아래에 추가:

# ==========================================================================
# Session Backend (Redis)
# ==========================================================================
# DB 세션 → Redis 세션으로 전환
# 이유: _invalidate_user_sessions()의 전체 테이블 스캔 문제 해결
# Reference: 215_SECURITY_VULNERABILITY_FIXES_PART2 섹션 6.2.6
# 운영 참고: Redis 재시작 시 Admin 세션 만료됨 (JWT 사용자는 무영향)
# Redis Persistence: k8s/redis-config.yaml 참조 (RDB만 사용, AOF off)
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

**파일 2**: `myproject/settings/local.py`

```python
# 동일하게 추가:
# 운영 참고: docker-compose.yml의 redis는 persist 미설정 → 재시작 시 세션 유실
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

#### 1.4.1 배포 시 기존 세션 영향 분석

DB 세션 → Redis 세션 전환 시, 기존 `django_session` 테이블에 저장된 세션은 더 이상 읽히지 않는다.

**영향 범위 (코드 근거):**

`base.py` L289-L290의 `DEFAULT_AUTHENTICATION_CLASSES`:
```python
"rest_framework_simplejwt.authentication.JWTAuthentication",  # ← 1순위
"rest_framework.authentication.SessionAuthentication",        # ← 2순위 (보조)
```

- **API 사용자 (JWT)**: **무영향** — JWT는 Redis 세션과 무관
- **Django Admin 사용자**: **1회 재로그인 필요** — `django.contrib.admin`이 SessionAuthentication 사용
- `rest_framework_simplejwt.token_blacklist`가 `INSTALLED_APPS`(`base.py` L59)에 설치됨 → JWT 토큰 관리는 DB 기반

**마이그레이션 전략 불필요 판단 근거:**

| 전략 | 비용 | 효과 |
|---|---|---|
| 단순 전환 (선택) | settings 2줄 변경 | Admin만 1회 재로그인 |
| DB+Redis 동시 읽기 커스텀 백엔드 | 신규 SessionBackend 클래스 50-100줄, 전환 기간 후 2차 배포, Django 내부 호환성 검증 | Admin 세션 무중단 유지 |

JWT가 주 인증 수단이고 영향이 Admin 세션에 한정되므로, **단순 전환**이 비용 대비 효과 면에서 적절하다.

#### 1.4.2 `django.contrib.sessions` INSTALLED_APPS 유지

> **리뷰 #5 반영**: 218 초안에서 "Session 모델 import가 ImportError로 처리"라고 기술했으나, 이것은 오류.

**정정**: `INSTALLED_APPS`에 `django.contrib.sessions`가 남아있으면 (`base.py` L46):
```python
"django.contrib.sessions",
```

`SESSION_ENGINE`이 cache여도 `from django.contrib.sessions.models import Session`은 **성공**한다. 따라서 `service.py` L354의 try-import 블록은 항상 실행되어 **빈 `django_session` 테이블에 무의미한 쿼리**를 날린다.

**`django.contrib.sessions` 제거 불가 이유:**
- `django.contrib.admin` (`base.py` L43)이 sessions에 의존
- `SessionMiddleware` (`base.py` L127)가 MIDDLEWARE에 포함 — allauth 소셜 로그인이 세션 기반 CSRF/state 저장에 의존

**해결**: 섹션 1.5에서 `_invalidate_user_sessions()` 수정 시 `SESSION_ENGINE` 체크를 추가하여 DB 스캔을 조건부로 실행한다.

### 1.5 역방향 조회 (Reverse Lookup) 구현: UserSessionRegistry

> **리뷰 #1 반영**: Redis 세션 전환만으로는 `_invalidate_user_sessions()`가 실제 Django 세션을 삭제할 수 없다. `user_session:{user_id}` 키를 생성(set)하는 코드가 전체 코드베이스에 0건이기 때문이다.

#### 1.5.1 문제 분석

Django의 `django.contrib.sessions.backends.cache`는 `session_key → session_data` 단방향 저장만 지원한다. `user_id`로 해당 유저의 `session_key`를 찾는 역방향 인덱스가 없다.

현재 `_invalidate_user_sessions()` Step 1:
```python
cache_key = f"user_session:{user_id}"
self.cache.delete(cache_key)   # ← 이 키를 생성하는 코드가 없음
```

**코드 근거 — 생성 코드 0건:**
```
grep "cache.set.*user_session" → 0건
grep "user_logged_in" → 0건 (django.contrib.auth.signals)
```

`shopping/signals.py`에는 `pre_social_login`, `post_save(SocialAccount)`, `post_save(Order)` 시그널만 존재. Django `user_logged_in` 시그널 핸들러 없음.

#### 1.5.2 네이밍 결정: `UserSessionRegistry`

| 후보 | 문제점 | 결정 |
|---|---|---|
| `RedisSessionManager` | `XTestSessionManager` (`services/xtest_session_manager.py` L76)와 혼동. "Manager"가 세션 자체를 관리하는 것으로 오해 | ❌ |
| `UserSessionRegistry` | 역할이 명확: `user_id → session_key` 매핑(레지스트리). 기존 `ProviderRegistry`, hooks 레지스트리 패턴과 일관. `XTestSessionManager`와 구분 | ✅ **선택** |

**선택 이유:**
1. 이 클래스는 세션 자체를 관리하지 않고, `user_id → session_key_set` **매핑만** 관리
2. `XTestSessionManager`(X-Test 세션 메타데이터: `xtest:session:{session_id}`)와 완전히 다른 도메인
3. 코드베이스에 `UserSessionRegistry` 이름 사용 0건 (충돌 없음)

#### 1.5.3 아키텍처 결정: hooks 패턴으로 구현

selfhealing 패키지는 호스트 앱(Django)에 직접 의존하지 않는다. 기존 `hooks.py`가 이 패턴을 사용:

```python
# hooks.py — selfhealing 패키지가 호스트 앱 인증 시스템에 의존하지 않으면서
# 보안 위반 시 호스트 앱의 토큰 무효화 등을 트리거할 수 있도록 함
```

동일한 패턴으로:
- **selfhealing 패키지**: `UserSessionRegistry` 유틸리티 클래스 제공 (Redis 매핑 관리)
- **호스트 앱(shopping)**: `user_logged_in` / `user_logged_out` Django 시그널에서 `UserSessionRegistry` 호출

#### 1.5.4 구현 명세: UserSessionRegistry (selfhealing 패키지)

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/session_registry.py` (신규)

```python
"""
UserSessionRegistry - user_id → session_key 역방향 매핑 관리.

Django 세션은 session_key → session_data 단방향 저장만 지원한다.
이 모듈은 user_id로 해당 유저의 session_key를 찾을 수 있도록
Redis SET 구조로 역방향 인덱스를 관리한다.

Redis 키 구조:
- security:user_sessions:{user_id} - SET: 해당 유저의 모든 session_key

Reference: 218_SESSION_CACHE_LEGACY_DEPENDENCY 섹션 1.5
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfhealing.interfaces.cache_provider import CacheProviderInterface

logger = logging.getLogger(__name__)

# Django 기본값: SESSION_COOKIE_AGE = 1209600 (2주)
# base.py에 SESSION_COOKIE_AGE 미설정 → Django 기본값 사용
_DEFAULT_SESSION_TTL_SECONDS = 1209600


class UserSessionRegistry:
    """
    user_id → session_key SET 역방향 매핑 관리.

    다중 세션 지원: 한 유저가 여러 기기에서 로그인할 수 있으므로
    단일 값이 아닌 Redis SET 구조(SADD, SMEMBERS)를 사용한다.

    Usage (호스트 앱의 signals.py에서):
        from selfhealing.services.security.session_registry import (
            get_user_session_registry,
        )

        @receiver(user_logged_in)
        def on_user_login(sender, request, user, **kwargs):
            registry = get_user_session_registry()
            registry.register(user.id, request.session.session_key)

        @receiver(user_logged_out)
        def on_user_logout(sender, request, user, **kwargs):
            if user and request.session.session_key:
                registry = get_user_session_registry()
                registry.unregister(user.id, request.session.session_key)
    """

    KEY_PREFIX = "security:user_sessions:"

    def __init__(self, cache: CacheProviderInterface | None = None):
        self._cache = cache

    @property
    def cache(self) -> CacheProviderInterface:
        if self._cache is None:
            from selfhealing.factory import ProviderRegistry

            try:
                self._cache = ProviderRegistry.get_cache()
            except (ValueError, ImportError):
                from selfhealing.interfaces.cache_provider import InMemoryCacheAdapter

                self._cache = InMemoryCacheAdapter()
        return self._cache

    def _key(self, user_id: int) -> str:
        return f"{self.KEY_PREFIX}{user_id}"

    def register(self, user_id: int, session_key: str) -> None:
        """
        로그인 시 user_id → session_key 매핑 등록.

        Redis SET에 session_key를 추가한다.
        TTL은 Django SESSION_COOKIE_AGE와 동기화 (기본 2주).

        Args:
            user_id: 로그인한 사용자 ID
            session_key: Django 세션의 session_key (request.session.session_key)
        """
        key = self._key(user_id)
        try:
            # CacheProviderInterface는 SET 자료구조를 직접 지원하지 않으므로
            # list 기반으로 구현 (기존 인터페이스 호환)
            existing = self.cache.get(key) or []
            if session_key not in existing:
                existing.append(session_key)
            ttl = self._get_session_ttl()
            self.cache.set(key, existing, ttl=timedelta(seconds=ttl))
            logger.debug(
                f"[UserSessionRegistry] Registered session for user {user_id}: "
                f"{session_key[:8]}... (total: {len(existing)})"
            )
        except Exception as e:
            logger.warning(f"[UserSessionRegistry] Failed to register session: {e}")

    def unregister(self, user_id: int, session_key: str) -> None:
        """
        로그아웃 시 user_id → session_key 매핑 제거.

        Args:
            user_id: 로그아웃한 사용자 ID
            session_key: 제거할 session_key
        """
        key = self._key(user_id)
        try:
            existing = self.cache.get(key) or []
            if session_key in existing:
                existing.remove(session_key)
            if existing:
                ttl = self._get_session_ttl()
                self.cache.set(key, existing, ttl=timedelta(seconds=ttl))
            else:
                self.cache.delete(key)
            logger.debug(
                f"[UserSessionRegistry] Unregistered session for user {user_id}: "
                f"{session_key[:8]}..."
            )
        except Exception as e:
            logger.warning(f"[UserSessionRegistry] Failed to unregister session: {e}")

    def get_session_keys(self, user_id: int) -> list[str]:
        """
        user_id에 연결된 모든 session_key 조회.

        Args:
            user_id: 조회할 사용자 ID

        Returns:
            session_key 리스트 (없으면 빈 리스트)
        """
        key = self._key(user_id)
        try:
            return self.cache.get(key) or []
        except Exception:
            return []

    def invalidate_all(self, user_id: int) -> int:
        """
        user_id의 모든 세션을 무효화.

        1. 레지스트리에서 session_key 목록 조회
        2. Django cache에서 각 session_key 삭제
        3. 레지스트리 키 자체 삭제

        Args:
            user_id: 무효화할 사용자 ID

        Returns:
            삭제된 세션 수
        """
        session_keys = self.get_session_keys(user_id)
        deleted = 0
        for sk in session_keys:
            try:
                # Django cache 세션 키 패턴:
                # django.contrib.sessions.cache:{session_key}
                # 또는 설정에 따라 다를 수 있으므로 양쪽 모두 삭제 시도
                self.cache.delete(sk)
                self.cache.delete(f"django.contrib.sessions.cache{sk}")
                deleted += 1
            except Exception:
                pass
        # 레지스트리 키 삭제
        self.cache.delete(self._key(user_id))
        logger.info(
            f"[UserSessionRegistry] Invalidated {deleted} sessions for user {user_id}"
        )
        return deleted

    @staticmethod
    def _get_session_ttl() -> int:
        """Django SESSION_COOKIE_AGE 설정값 조회."""
        try:
            from django.conf import settings as django_settings

            return getattr(
                django_settings, "SESSION_COOKIE_AGE", _DEFAULT_SESSION_TTL_SECONDS
            )
        except Exception:
            return _DEFAULT_SESSION_TTL_SECONDS


# =============================================================================
# Singleton
# =============================================================================

_registry: UserSessionRegistry | None = None


def get_user_session_registry() -> UserSessionRegistry:
    """UserSessionRegistry 싱글톤 반환."""
    global _registry
    if _registry is None:
        _registry = UserSessionRegistry()
    return _registry


def reset_user_session_registry() -> None:
    """싱글톤 초기화 (테스트용)."""
    global _registry
    _registry = None
```

#### 1.5.5 Django 어댑터 시그널 핸들러

> **리뷰 #7 반영**: 218 초기 구현에서는 `shopping/signals.py`(테스트베드)에 시그널 핸들러를 배치했으나,
> shopping은 테스트베드일 뿐이므로 selfhealing 패키지 내부로 이동.
> `adapters/celery/signal_hooks.py`와 동일한 패턴으로 `adapters/django/signal_hooks.py`에 배치.
> `SelfHealingConfig.ready()`에서 자동 연결되므로 호스트 앱에서 별도 코드 불필요.

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/django/signal_hooks.py` (신규)

```python
from selfhealing.services.security.session_registry import get_user_session_registry


def on_user_login_register_session(
    sender: Any, request: HttpRequest, user: Any, **kwargs: Any
) -> None:
    """
    로그인 시 UserSessionRegistry에 session_key 매핑 등록.

    Redis 세션 백엔드에서는 user_id → session_key 역방향 조회가 불가능하므로,
    로그인 시점에 매핑을 등록하여 세션 무효화 시 역방향 조회를 지원한다.
    """
    session_key = request.session.session_key
    if not session_key:
        request.session.save()
        session_key = request.session.session_key

    if session_key and user and user.pk:
        registry = get_user_session_registry()
        registry.register(user.pk, session_key)


def on_user_logout_unregister_session(
    sender: Any, request: HttpRequest, user: Any, **kwargs: Any
) -> None:
    """
    로그아웃 시 UserSessionRegistry에서 session_key 매핑 제거.
    """
    session_key = getattr(request.session, "session_key", None)
    if session_key and user and user.pk:
        registry = get_user_session_registry()
        registry.unregister(user.pk, session_key)


def connect_session_signals() -> None:
    """SelfHealingConfig.ready()에서 호출. dispatch_uid로 중복 방지."""
    from django.contrib.auth.signals import user_logged_in, user_logged_out

    user_logged_in.connect(
        on_user_login_register_session,
        dispatch_uid="selfhealing_session_register",
    )
    user_logged_out.connect(
        on_user_logout_unregister_session,
        dispatch_uid="selfhealing_session_unregister",
    )
```

**`SelfHealingConfig.ready()`에서 자동 연결:**

```python
# apps.py ready() 내부
self._connect_session_signals()

@staticmethod
def _connect_session_signals():
    try:
        from selfhealing.adapters.django.signal_hooks import connect_session_signals
        connect_session_signals()
    except Exception as e:
        logger.warning(f"[SelfHealing] Failed to connect session signals: {e}")
```

#### 1.5.6 Redis Persistence 운영 참고사항

> **리뷰 #4 반영**: Redis 세션 전환 시 영속성 설정을 확인해야 한다.

**현재 상태:**

| 환경 | 설정 파일 | Persistence | 세션 유실 위험 |
|---|---|---|---|
| K8s (Production) | `k8s/redis-config.yaml` L48-L56 | RDB만 (`save 900 1`, `save 300 10`, `save 60 10000`, `appendonly no`) | 최대 15분 분량 유실 가능 |
| Docker (Local) | `docker-compose.yml` L21-L28 | 없음 (기본 redis:7-alpine) | 재시작 시 전체 유실 |
| Load Test | `load_tests/docker/docker-compose.gap-tests.yml` L32 | `--save "" --appendonly no` | 재시작 시 전체 유실 |

**AOF 활성화 장단점 분석:**

| 항목 | 현재 (RDB만) | AOF everysec | AOF always |
|---|---|---|---|
| 최대 데이터 유실 | 15분 | 1초 | 0 |
| 쓰기 성능 | 최적 | 약간 저하 | 30-50% 저하 |
| 디스크 I/O | 낮음 | 중간 | 높음 |
| 파일 관리 | RDB만 | AOF rewrite 필요 | AOF rewrite 필요 |

**이 시스템에서의 판단:**

Redis DB 1에 저장되는 데이터 (`production.py` CACHES.default.LOCATION: `redis://redis:6379/1`):
- selfhealing 캐시 (IP ban, suspicious IP 카운터) — 유실 시 재생성 가능
- 세션 데이터 (Redis 전환 후) — 유실 시 Admin만 재로그인
- rate limit 카운터 — 유실 시 곧 재생성

JWT가 주 인증 수단이므로 세션 유실의 보안 위협은 낮다. **현재 RDB 설정 유지**를 선택하되, 운영 문서에 다음을 명시한다:

> ⚠️ **운영 주의**: Redis 재시작 시 로그인된 모든 Admin 세션이 만료됩니다.
> JWT 사용자(API 클라이언트)는 영향 없습니다.
> 필요시 `k8s/redis-config.yaml`에서 `appendonly yes`, `appendfsync everysec`로 변경하여 AOF 활성화 가능합니다.

---

## 2. 취약점 #2: Dead Code 제거 + `_invalidate_user_sessions()` 간소화

### 2.1 문제 정의

`_invalidate_user_sessions()`에서 사용하는 캐시 키 프리픽스가 하드코딩되어 있으며, 이 키들을 실제로 **생성(set)**하는 코드가 전체 코드베이스에 존재하지 않는다.

### 2.2 코드 근거

**파일**: `service.py` L341-L348

```python
related_prefixes = [
    f"user_token:{user_id}",      # ← 하드코딩
    f"user_permissions:{user_id}", # ← 하드코딩
    f"user_auth:{user_id}",       # ← 하드코딩
]
```

이 키들을 **쓰는(set)** 코드가 전체 codebase에 없다:

```
grep "cache.set.*user_token" → 0건
grep "cache.set.*user_permissions" → 0건
grep "cache.set.*user_auth" → 0건
grep "user_token:" → service.py L342 (삭제만)
grep "user_permissions:" → service.py L343 (삭제만)
grep "user_auth:" → service.py L344 (삭제만)
```

### 2.3 해결 방안 결정: Dead Code 제거

> **리뷰 #2 반영**: 218 초안에서는 이 키들을 `SecuritySettings`로 상수화하는 리팩토링을 제안했으나, 생성 코드가 없으므로 이것은 **Dead Code**이다. 상수화가 아닌 **제거**가 올바른 방향이다.

**상수화(설정화)를 선택하지 않는 이유:**

1. **YAGNI 원칙 위반**: `cache.set("user_token:{user_id}", ...)`를 실행하는 코드가 0건. 미래에 필요해질 때 추가해도 1-2줄 변경에 불과
2. **오해 유발**: `SecurityConfig.session_related_cache_prefixes` 설정이 존재하면, 코드 리뷰어나 새 개발자가 "이 키들이 어딘가에서 생성되고 있다"고 오해. 현재 218 초안 작성 시에도 이 오해가 발생
3. **기존 시스템 패턴과 불일치**: `SecurityConfig`의 기존 필드들(`suspicious_ip_cache_prefix`, `banned_ip_cache_prefix`)은 모두 실제 `cache.set()`에서 사용됨 (`service.py` L424 `_log_suspicious_ip`, L455 `_temporary_ip_ban`). 사용처 없는 설정을 추가하면 패턴 일관성이 깨짐

**결론**: Dead Code는 제거하고, 필요 시점에 키 생성 코드 + 삭제 코드 + 설정을 함께 추가한다.

### 2.4 `_invalidate_user_sessions()` 전체 재작성

> **리뷰 #1, #2, #5 통합 반영**: Dead Code 제거 + UserSessionRegistry 연동 + SESSION_ENGINE 조건부 체크

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/service.py`

기존 코드 (L322-L420) → 다음으로 교체:

```python
    def _invalidate_user_sessions(self, user_id: int) -> str:
        """
        Invalidate all sessions for a user.

        218_SESSION_CACHE_LEGACY_DEPENDENCY 리뷰 반영:
        - Dead Code 제거: user_token:, user_permissions:, user_auth: 삭제 로직 제거
          (생성 코드 0건 — 전체 코드베이스에 cache.set으로 이 키를 쓰는 곳 없음)
        - UserSessionRegistry 연동: 역방향 조회로 정확한 세션 삭제
        - SESSION_ENGINE 조건부 체크: DB 백엔드일 때만 Session 테이블 스캔
        - Hook 기반 확장: JWT 블랙리스트 등 (기존 hooks.py 유지)
        """
        invalidated_items = []

        try:
            # 1. UserSessionRegistry를 통한 세션 무효화 (역방향 조회)
            # user_logged_in 시그널에서 등록한 session_key 목록을 조회하여 삭제
            try:
                from selfhealing.services.security.session_registry import (
                    get_user_session_registry,
                )

                registry = get_user_session_registry()
                deleted_count = registry.invalidate_all(user_id)
                if deleted_count > 0:
                    invalidated_items.append(f"redis_sessions({deleted_count})")
                else:
                    invalidated_items.append("redis_sessions(0:no_registered_keys)")
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"[Security] UserSessionRegistry cleanup failed: {e}")

            # 2. Django DB 세션 삭제 (SESSION_ENGINE이 DB 백엔드일 때만)
            # django.contrib.sessions가 INSTALLED_APPS에 있으면 Session import는
            # 항상 성공하므로, SESSION_ENGINE을 명시적으로 체크해야 한다.
            try:
                from django.conf import settings as django_settings

                session_engine = getattr(
                    django_settings,
                    "SESSION_ENGINE",
                    "django.contrib.sessions.backends.db",
                )
                if "db" in session_engine or "cached_db" in session_engine:
                    from django.contrib.sessions.models import Session
                    from django.utils import timezone as dj_timezone

                    active_sessions = Session.objects.filter(
                        expire_date__gte=dj_timezone.now()
                    )
                    deleted_count = 0
                    for session in active_sessions:
                        data = session.get_decoded()
                        if str(data.get("_auth_user_id")) == str(user_id):
                            session.delete()
                            deleted_count += 1
                    if deleted_count > 0:
                        invalidated_items.append(f"django_sessions({deleted_count})")
                else:
                    logger.debug(
                        f"[Security] Skipping DB session scan: "
                        f"SESSION_ENGINE={session_engine}"
                    )
            except ImportError:
                pass
            except Exception as e:
                logger.debug(f"[Security] Django session cleanup skipped: {e}")

            # 3. 등록된 세션 무효화 콜백 실행 (JWT 블랙리스트 등)
            try:
                from selfhealing.services.security.hooks import (
                    get_session_invalidation_hooks,
                )

                for hook in get_session_invalidation_hooks():
                    try:
                        result = hook(user_id)
                        if result:
                            invalidated_items.append(result)
                    except Exception as hook_err:
                        logger.warning(
                            f"[Security] Session invalidation hook failed: {hook_err}"
                        )
            except ImportError:
                pass

            logger.info(
                f"[Security] Invalidated sessions for user {user_id}: "
                f"{', '.join(invalidated_items)}"
            )

            # === Audit 기록: 세션 무효화 (85_AUDIT_INTEGRATION Phase 1) ===
            log_security_violation_audit(
                violation_type="session_invalidation",
                action="invalidate_session",
                target=f"user:{user_id}",
                result="success",
                severity="high",
                operator="system",
                user_id=user_id,
                details={"invalidated": invalidated_items},
            )

            return (
                f"User sessions cleared for user {user_id}: "
                f"{', '.join(invalidated_items)}"
            )
        except Exception as e:
            logger.error(f"[Security] Failed to invalidate sessions: {e}")

            log_security_violation_audit(
                violation_type="session_invalidation",
                action="invalidate_session",
                target=f"user:{user_id}",
                result="failed",
                severity="high",
                operator="system",
                user_id=user_id,
                details={"error": str(e)},
            )

            return f"Session invalidation attempted but failed: {e}"
```

### 2.5 제거 항목 상세

| 제거된 코드 | 위치 | 이유 |
|---|---|---|
| `cache_key = f"user_session:{user_id}"` + `self.cache.delete(cache_key)` | L335-L337 | `UserSessionRegistry.invalidate_all()`로 대체 |
| `related_prefixes = [f"user_token:...", ...]` + 삭제 루프 | L340-L352 | Dead Code: `cache.set`으로 이 키를 생성하는 코드 0건 |

### 2.6 추가하지 않는 항목 (218 초안 대비)

| 218 초안 제안 | 결정 | 이유 |
|---|---|---|
| `SecuritySettings.session_cache_prefix` | ❌ 추가 안 함 | `UserSessionRegistry.KEY_PREFIX`에서 관리. `SecuritySettings`의 기존 prefix 필드(`suspicious_ip_cache_prefix`, `banned_ip_cache_prefix`)는 모두 `cache.set` 사용처가 있는 필드만 포함 |
| `SecuritySettings.session_related_cache_prefixes` | ❌ 추가 안 함 | Dead Code를 설정으로 격상하는 것은 부적절 |
| `SecurityConfig.session_cache_prefix` | ❌ 추가 안 함 | 동일 이유 |
| `SecurityConfig.session_related_cache_prefixes` | ❌ 추가 안 함 | 동일 이유 |

---

## 3. 취약점 #3: `decrypt_forensic()` 레거시 SHA-256 해시 미감지

### 3.1 문제 정의

`decrypt_forensic()`이 `encrypted:` 접두사가 없는 값은 모두 `ValueError`로 거부한다. Fernet 도입 이전에 `hash_for_audit()`으로 저장된 `sha256:` 형식의 레거시 데이터를 처리하지 못한다.

### 3.2 코드 근거

**파일**: `audit/masking.py` L190-L191

```python
def decrypt_forensic(encrypted_value: str) -> str:
    if not encrypted_value.startswith("encrypted:"):
        raise ValueError("Not a FORENSIC encrypted value (must start with 'encrypted:')")
    # ... Fernet 복호화만 시도
```

레거시 데이터 형식: `sha256:a1b2c3d4e5f6...` (`hash_for_audit()` 출력)

```python
def hash_for_audit(value: str, salt: str | None = None) -> str:
    # ...
    return f"sha256:{hash_value[:16]}"
```

### 3.3 함수 유지 근거

> **리뷰 #6 반영**: `decrypt_forensic`은 호출 0건, 테스트 0건이지만 **제거하지 않는다**.

**근거:**

1. `mask_with_level(value, MaskingLevel.FORENSIC)` (`masking.py` L130-L137)이 `encrypted:...` 형식 데이터를 **실제로 생성**한다
2. `test_role_based_masking.py` L63-L65에서 FORENSIC 마스킹이 `encrypted:` prefix를 생성하는 것이 검증됨
3. `get_masking_level_for_context()` (`masking.py` L237)가 `selfhealing_admin` 역할(priority ≥ 3)에 대해 `MaskingLevel.FORENSIC`을 반환
4. `decrypt_forensic`은 `mask_with_level(..., FORENSIC)`과 **대칭 쌍**. 암호화 함수가 존재하는 한 복호화 함수를 제거하면 **복구 불가능한 데이터**가 됨
5. 감사 로그 조회 API (어드민 UI)가 구현될 때 즉시 필요한 인프라 함수

**현재 FORENSIC 레벨 사용 경로:**
- `mask_with_level` → FORENSIC 분기 → `fernet.encrypt()` → `encrypted:...` 생성 (masking.py L130-L137)
- `_mask_error_message()` (`handler.py` L480)에서 `mask_with_level` 호출하나 **항상 `MaskingLevel.CLIENT`** → FORENSIC 미도달
- `get_masking_level_for_context()`를 프로덕션 코드에서 호출하여 `mask_with_level`에 전달하는 곳: **테스트 외 0건** (아직 연결 미완)

**결론**: Dead Code가 아니라 **아직 연결되지 않은 인프라**. 감사 로그 조회 API 구현 시 즉시 활성화된다.

### 3.4 해결 방안: 레거시 감지 + HMAC fallback 감지 추가

> **참고**: 215 문서 섹션 6.3에서 분석 완료. 본 문서는 구현 명세.

#### 수정: `decrypt_forensic()`에 레거시/HMAC 감지 추가

**파일**: `packages/selfhealing-python/src/selfhealing/audit/masking.py`

```python
def decrypt_forensic(encrypted_value: str) -> str:
    """
    FORENSIC 레벨로 암호화된 값을 복호화.

    레거시 호환 (218_SESSION_CACHE_LEGACY_DEPENDENCY):
    - "sha256:" 접두사: 해시 값으로 복원 불가 → 명확한 에러 메시지
    - "encrypted:hmac:" 접두사: HMAC fallback 값으로 복원 불가 → 명확한 에러 메시지
    - "encrypted:" 접두사: Fernet 복호화 시도

    Args:
        encrypted_value: "encrypted:..." 형식의 암호화된 문자열

    Returns:
        복호화된 원본 문자열

    Raises:
        ValueError: 잘못된 형식이거나 복호화 실패 시
        RuntimeError: encryption_key 미설정 시
    """
    # 레거시 SHA-256 해시 감지 (Fernet 도입 이전 데이터)
    if encrypted_value.startswith("sha256:"):
        raise ValueError(
            "This value was stored as a SHA-256 hash (pre-Fernet era). "
            "Hash values are one-way and cannot be decrypted. "
            "Original data is not recoverable."
        )

    if not encrypted_value.startswith("encrypted:"):
        raise ValueError(
            "Not a FORENSIC encrypted value (must start with 'encrypted:'). "
            f"Got prefix: '{encrypted_value[:20]}...'"
        )

    # HMAC fallback 감지 (_forensic_hmac_fallback 출력)
    # encryption_key 미설정 시 mask_with_level(FORENSIC)이 HMAC으로 폴백하며
    # "encrypted:hmac:..." 형식을 생성한다. 이 값은 복원 불가.
    token = encrypted_value[len("encrypted:"):]
    if token.startswith("hmac:"):
        raise ValueError(
            "This value was stored as an HMAC hash (Fernet key was unavailable "
            "at encryption time). HMAC values are one-way and cannot be decrypted. "
            "Original data is not recoverable."
        )

    fernet = _get_forensic_fernet()
    if fernet is None:
        raise RuntimeError(
            "Cannot decrypt: encryption_key is not configured. "
            "Set SELFHEALING_SECRET_ENCRYPTION_KEY environment variable."
        )

    try:
        decrypted = fernet.decrypt(token.encode())
        return decrypted.decode()
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}") from e
```

**HMAC fallback 감지 추가 이유 (218 초안에 없던 보완):**

`_forensic_hmac_fallback()` (`masking.py` L150-L169)은 `encrypted:hmac:...` 형식으로 데이터를 생성한다:
```python
return f"encrypted:hmac:{encoded}"
```

218 초안의 수정 코드에서는 `encrypted:` 접두사 검증 후 바로 Fernet 복호화를 시도하므로, HMAC fallback 값에 대해 "Decryption failed" 같은 불명확한 에러가 발생한다. 위 수정에서는 `encrypted:hmac:` 패턴을 명시적으로 감지하여 "복원 불가"임을 명확히 알린다.

---

## 4. 취약점 #4: selfhealing 패키지 `cryptography` 의존성 누락

### 4.1 문제 정의

FORENSIC 마스킹 레벨은 `cryptography` 라이브러리의 `Fernet`을 사용하지만, selfhealing 패키지의 `pyproject.toml`에 이 의존성이 선언되어 있지 않다.

### 4.2 코드 근거

#### FORENSIC 마스킹에서 `cryptography` 사용

**파일**: `audit/masking.py` L78

```python
from cryptography.fernet import Fernet
```

#### selfhealing 패키지 의존성 목록

**파일**: `packages/selfhealing-python/pyproject.toml` L40-L44

```toml
dependencies = [
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]
```

`cryptography`가 **없다**.

#### 프로젝트 루트에는 존재

**파일**: `pyproject.toml` (루트) L46

```toml
"cryptography>=44.0.0",
```

이것은 호스트 앱(shopping 테스트베드)의 의존성이며, selfhealing 패키지를 **독립적으로 설치할 때는 포함되지 않는다**.

### 4.3 해결 방안: Optional Dependency

`cryptography`는 FORENSIC 마스킹에서만 사용되며, 없어도 시스템은 동작한다 (`_get_forensic_fernet()`이 `ImportError`를 catch하고 `None` 반환 → AUDIT 레벨 폴백). 따라서 **optional dependency**로 추가한다.

#### 수정: selfhealing `pyproject.toml`

**파일**: `packages/selfhealing-python/pyproject.toml`

```toml
dependencies = [
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]

[project.optional-dependencies]
forensic = [
    "cryptography>=44.0.0",
]
```

설치 방법:
- 기본: `pip install selfhealing` (FORENSIC 없이)
- FORENSIC 포함: `pip install selfhealing[forensic]`

이미 `_get_forensic_fernet()`에 graceful fallback이 구현되어 있으므로 (`masking.py` L89-L92):

```python
except ImportError:
    logger.warning(
        "[Security] cryptography library not installed. "
        "FORENSIC masking will fall back to AUDIT level (hash only)."
    )
    return None
```

추가 코드 변경은 불필요하다.

---

## 5. 전체 수정 대상 파일 요약

> **리뷰 #1-#6 반영**: 218 초안 대비 SecuritySettings/SecurityConfig 변경 제거, UserSessionRegistry 신규, Dead Code 제거, HMAC fallback 감지 추가.

| 파일 | 변경 유형 | 관련 취약점 | 설명 |
|---|---|---|---|
| `myproject/settings/production.py` | 수정 | #1 | `SESSION_ENGINE`, `SESSION_CACHE_ALIAS` 추가 + 운영 주석 |
| `myproject/settings/local.py` | 수정 | #1 | 동일 |
| `services/security/session_registry.py` | **신규** | #1 (리뷰 #1) | `UserSessionRegistry` — `user_id → session_key` 역방향 매핑 |
| `adapters/django/signal_hooks.py` | **신규** | #1 (리뷰 #1, #7) | `user_logged_in`/`user_logged_out` 시그널 핸들러. `SelfHealingConfig.ready()`에서 자동 연결 |
| `adapters/django/apps.py` | 수정 | #1 (리뷰 #7) | `ready()`에 `_connect_session_signals()` 추가 |
| `services/security/service.py` | 수정 | #1, #2 (리뷰 #1, #2, #5) | `_invalidate_user_sessions()` 전체 재작성: Dead Code 제거 + UserSessionRegistry 연동 + SESSION_ENGINE 조건부 체크 |
| `audit/masking.py` | 수정 | #3 (리뷰 #6) | `decrypt_forensic()`에 `sha256:` 감지 + `encrypted:hmac:` 감지 추가 |
| `packages/selfhealing-python/pyproject.toml` | 수정 | #4 | `[project.optional-dependencies]` forensic 추가 |

### 5.1 218 초안 대비 변경하지 않는 파일

| 파일 | 218 초안 제안 | 최종 결정 | 이유 |
|---|---|---|---|
| `settings/security.py` | `session_cache_prefix`, `session_related_cache_prefixes` 추가 | ❌ 변경 안 함 | Dead Code를 설정으로 격상 부적절. `UserSessionRegistry.KEY_PREFIX`에서 관리. 기존 prefix 필드(`suspicious_ip_cache_prefix` 등)는 모두 `cache.set` 사용처가 있는 필드만 포함하는 패턴 (리뷰 #2) |
| `services/security/models.py` | `SecurityConfig`에 세션 필드 추가 | ❌ 변경 안 함 | SecuritySettings 변경 없으므로 `from_settings()` 매핑도 불필요 (리뷰 #2) |

---

## 6. 하위 호환성 분석

| 변경 | 하위 호환 | 근거 |
|---|---|---|
| Redis 세션 백엔드 전환 | ✅ | Django settings 변경만. 기존 DB 세션 유실되나 JWT 사용자 무영향, Admin만 1회 재로그인 (섹션 1.4.1) |
| `UserSessionRegistry` 신규 | ✅ | 신규 파일 추가, 기존 코드 미영향. 시그널 핸들러는 `try/except ImportError`로 selfhealing 미설치 시 무시 |
| Dead Code 제거 (`user_token:` 등) | ✅ | 삭제되는 `cache.delete()` 호출의 대상 키를 생성하는 코드가 0건이므로, 삭제 코드 제거 시 동작 변화 없음 |
| `_invalidate_user_sessions` 재작성 | ✅ | 기존 Step 1(무효 캐시 삭제)→ UserSessionRegistry, Step 2(Dead Code)→ 제거, Step 3(DB 스캔)→ SESSION_ENGINE 조건부, Step 4(hooks)→ 그대로 유지 |
| `decrypt_forensic` 레거시 감지 | ✅ | `sha256:` 값은 기존에도 `ValueError` 발생 ("must start with 'encrypted:'") → 메시지만 더 명확해짐. `encrypted:hmac:` 값도 기존에는 Fernet 복호화 실패로 불명확한 에러 → 명확한 메시지로 개선 |
| cryptography optional dep | ✅ | 기존 동작 변경 없음, 설치 옵션만 추가. `_get_forensic_fernet()`의 graceful fallback 이미 구현됨 |

---

## 7. 테스트 명세

> **리뷰 #1-#6 반영**: 218 초안의 테스트 명세를 리뷰 결과에 맞게 재작성.
> 캐시 키 상수화 테스트는 Dead Code 제거로 인해 삭제. UserSessionRegistry, SESSION_ENGINE 체크, HMAC fallback 감지 테스트 추가.

### 7.1 세션 백엔드 전환 검증

**파일**: `tests/self_healing/integration/django/test_session_backend.py` (신규)

```python
import pytest
from django.conf import settings


class TestRedisSessionBackend:
    """Redis 세션 백엔드 전환 검증."""

    def test_session_engine_is_cache(self):
        """SESSION_ENGINE이 'cache'로 설정되었는지 확인."""
        assert settings.SESSION_ENGINE == "django.contrib.sessions.backends.cache"

    def test_session_cache_alias_is_default(self):
        """SESSION_CACHE_ALIAS가 'default'인지 확인."""
        assert settings.SESSION_CACHE_ALIAS == "default"

    def test_sessions_app_still_installed(self):
        """django.contrib.sessions가 INSTALLED_APPS에 남아있는지 확인.
        (admin, SessionMiddleware, allauth 의존성 유지 목적)"""
        assert "django.contrib.sessions" in settings.INSTALLED_APPS

    def test_session_middleware_still_active(self):
        """SessionMiddleware가 MIDDLEWARE에 포함되어 있는지 확인."""
        assert any(
            "SessionMiddleware" in m for m in settings.MIDDLEWARE
        )
```

### 7.2 UserSessionRegistry 검증

**파일**: `packages/selfhealing-python/tests/unit/security/test_user_session_registry.py` (신규)

```python
import pytest
from unittest.mock import MagicMock

from selfhealing.services.security.session_registry import (
    UserSessionRegistry,
    reset_user_session_registry,
)


@pytest.fixture
def mock_cache():
    return MagicMock()


@pytest.fixture
def registry(mock_cache):
    return UserSessionRegistry(cache=mock_cache)


class TestUserSessionRegistry:
    """UserSessionRegistry 단위 테스트."""

    def test_register_adds_session_key(self, registry, mock_cache):
        """register()가 session_key를 리스트에 추가하는지 확인."""
        mock_cache.get.return_value = None
        registry.register(user_id=1, session_key="abc123")
        mock_cache.set.assert_called_once()
        args = mock_cache.set.call_args
        assert "abc123" in args[0][1]  # 저장된 리스트에 포함

    def test_register_multiple_sessions(self, registry, mock_cache):
        """동일 유저의 다중 세션(여러 기기)을 지원하는지 확인."""
        mock_cache.get.return_value = ["session_a"]
        registry.register(user_id=1, session_key="session_b")
        args = mock_cache.set.call_args
        saved_list = args[0][1]
        assert "session_a" in saved_list
        assert "session_b" in saved_list

    def test_register_deduplication(self, registry, mock_cache):
        """동일 session_key 중복 등록 방지 확인."""
        mock_cache.get.return_value = ["abc123"]
        registry.register(user_id=1, session_key="abc123")
        args = mock_cache.set.call_args
        assert args[0][1].count("abc123") == 1

    def test_unregister_removes_session_key(self, registry, mock_cache):
        """unregister()가 특정 session_key만 제거하는지 확인."""
        mock_cache.get.return_value = ["keep_this", "remove_this"]
        registry.unregister(user_id=1, session_key="remove_this")
        args = mock_cache.set.call_args
        saved_list = args[0][1]
        assert "keep_this" in saved_list
        assert "remove_this" not in saved_list

    def test_unregister_last_session_deletes_key(self, registry, mock_cache):
        """마지막 session_key 제거 시 레지스트리 키 자체를 삭제하는지 확인."""
        mock_cache.get.return_value = ["only_session"]
        registry.unregister(user_id=1, session_key="only_session")
        mock_cache.delete.assert_called_once()

    def test_get_session_keys_returns_list(self, registry, mock_cache):
        """get_session_keys()가 리스트를 반환하는지 확인."""
        mock_cache.get.return_value = ["s1", "s2"]
        result = registry.get_session_keys(user_id=1)
        assert result == ["s1", "s2"]

    def test_get_session_keys_empty(self, registry, mock_cache):
        """등록된 세션이 없을 때 빈 리스트 반환 확인."""
        mock_cache.get.return_value = None
        assert registry.get_session_keys(user_id=1) == []

    def test_invalidate_all_deletes_sessions(self, registry, mock_cache):
        """invalidate_all()이 모든 session_key + 레지스트리 키를 삭제하는지 확인."""
        mock_cache.get.return_value = ["s1", "s2"]
        deleted = registry.invalidate_all(user_id=1)
        assert deleted == 2
        # 각 session_key 삭제 + Django 캐시 키 패턴 삭제 + 레지스트리 키 삭제
        assert mock_cache.delete.call_count >= 3  # s1, s2, registry_key

    def test_invalidate_all_no_sessions(self, registry, mock_cache):
        """등록된 세션 없을 때 invalidate_all()이 0을 반환하는지 확인."""
        mock_cache.get.return_value = None
        deleted = registry.invalidate_all(user_id=1)
        assert deleted == 0

    def test_key_prefix_format(self, registry):
        """키 프리픽스가 security: 네임스페이스를 따르는지 확인."""
        key = registry._key(42)
        assert key == "security:user_sessions:42"
        assert key.startswith("security:")

    def test_register_failure_does_not_raise(self, registry, mock_cache):
        """Redis 장애 시 register()가 예외를 던지지 않는지 확인 (graceful)."""
        mock_cache.get.side_effect = Exception("Redis down")
        # 예외 없이 종료되어야 함
        registry.register(user_id=1, session_key="abc")
```

### 7.3 `_invalidate_user_sessions` 재작성 검증

**파일**: `packages/selfhealing-python/tests/unit/security/test_invalidate_sessions.py` (신규 또는 기존 확장)

```python
import pytest
from unittest.mock import MagicMock, patch


class TestInvalidateUserSessions:
    """_invalidate_user_sessions() 재작성 검증 (리뷰 #1, #2, #5 반영)."""

    def test_dead_code_removed_no_user_token_delete(self):
        """user_token:, user_permissions:, user_auth: 삭제 호출이 없는지 확인.
        (이전 Dead Code가 제거되었는지 검증)"""
        # service.py 소스코드에서 "user_token:", "user_permissions:", "user_auth:" 문자열 부재 확인
        import inspect
        from selfhealing.services.security.service import SecurityViolationService

        source = inspect.getsource(
            SecurityViolationService._invalidate_user_sessions
        )
        assert "user_token:" not in source
        assert "user_permissions:" not in source
        assert "user_auth:" not in source

    @patch("selfhealing.services.security.service.SecurityViolationService.cache")
    def test_session_engine_check_skips_db_scan_for_cache_backend(
        self,
        mock_cache,
    ):
        """SESSION_ENGINE이 'cache'일 때 Session.objects.filter가 호출되지 않는지 확인."""
        with patch("django.conf.settings") as mock_settings:
            mock_settings.SESSION_ENGINE = (
                "django.contrib.sessions.backends.cache"
            )
            # DB Session 모델이 쿼리되지 않아야 함
            # 구체적 구현은 integration test에서 검증

    def test_session_engine_check_runs_db_scan_for_db_backend(self):
        """SESSION_ENGINE이 'db'일 때 Session.objects.filter가 호출되는지 확인."""

    def test_user_session_registry_called(self):
        """UserSessionRegistry.invalidate_all()이 호출되는지 확인."""

    def test_hooks_still_executed(self):
        """JWT 블랙리스트 등 hooks가 여전히 실행되는지 확인.
        (기존 hooks.py 동작 유지)"""
```

### 7.4 `decrypt_forensic` 레거시/HMAC 감지 검증

> **리뷰 #3, #6 반영**: 테스트 0건이었던 decrypt_forensic에 대한 단위 테스트 추가.

**파일**: `packages/selfhealing-python/tests/audit/test_masking.py` (기존 파일에 클래스 추가)

```python
import pytest
from unittest.mock import patch, MagicMock

from selfhealing.audit.masking import decrypt_forensic, mask_with_level, MaskingLevel


class TestDecryptForensic:
    """decrypt_forensic() 단위 테스트 (218 리뷰 #3, #6)."""

    def test_sha256_prefix_raises_clear_error(self):
        """sha256: 접두사 → 복원 불가 명확한 에러 메시지."""
        with pytest.raises(ValueError, match="SHA-256 hash"):
            decrypt_forensic("sha256:a1b2c3d4e5f6")

    def test_sha256_error_mentions_non_recoverable(self):
        """sha256: 에러 메시지에 '복원 불가' 정보 포함."""
        with pytest.raises(ValueError, match="not recoverable"):
            decrypt_forensic("sha256:anything")

    def test_hmac_fallback_raises_clear_error(self):
        """encrypted:hmac: 접두사 → HMAC fallback 복원 불가 에러."""
        with pytest.raises(ValueError, match="HMAC"):
            decrypt_forensic("encrypted:hmac:dGVzdA==")

    def test_hmac_error_mentions_non_recoverable(self):
        """HMAC 에러 메시지에 '복원 불가' 정보 포함."""
        with pytest.raises(ValueError, match="not recoverable"):
            decrypt_forensic("encrypted:hmac:dGVzdA==")

    def test_unknown_prefix_raises_error(self):
        """인식 불가 접두사 → ValueError with prefix info."""
        with pytest.raises(ValueError, match="must start with 'encrypted:'"):
            decrypt_forensic("unknown:something")

    def test_unknown_prefix_shows_got_prefix(self):
        """에러 메시지에 실제 받은 접두사 표시."""
        with pytest.raises(ValueError, match="Got prefix:"):
            decrypt_forensic("random_value_without_prefix")

    def test_empty_string_raises_error(self):
        """빈 문자열 → ValueError."""
        with pytest.raises(ValueError):
            decrypt_forensic("")

    def test_no_fernet_key_raises_runtime_error(self):
        """encryption_key 미설정 시 RuntimeError."""
        with patch(
            "selfhealing.audit.masking._get_forensic_fernet", return_value=None
        ):
            with pytest.raises(RuntimeError, match="encryption_key"):
                decrypt_forensic("encrypted:validtoken")

    def test_roundtrip_encrypt_decrypt(self):
        """mask_with_level(FORENSIC) → decrypt_forensic 왕복 검증.
        Fernet 키가 설정된 환경에서만 의미 있음."""
        mock_fernet = MagicMock()
        mock_fernet.encrypt.return_value = b"fernet_token_data"
        mock_fernet.decrypt.return_value = b"original_value"

        with patch(
            "selfhealing.audit.masking._get_forensic_fernet",
            return_value=mock_fernet,
        ):
            encrypted = mask_with_level("original_value", MaskingLevel.FORENSIC)
            assert encrypted.startswith("encrypted:")

            decrypted = decrypt_forensic(encrypted)
            assert decrypted == "original_value"

    def test_invalid_fernet_token_raises_value_error(self):
        """유효하지 않은 Fernet 토큰 → ValueError(Decryption failed)."""
        mock_fernet = MagicMock()
        mock_fernet.decrypt.side_effect = Exception("InvalidToken")

        with patch(
            "selfhealing.audit.masking._get_forensic_fernet",
            return_value=mock_fernet,
        ):
            with pytest.raises(ValueError, match="Decryption failed"):
                decrypt_forensic("encrypted:corrupted_data")
```

### 7.5 의존성 검증

```python
class TestCryptographyOptionalDep:
    """cryptography optional dependency 검증."""

    def test_forensic_masking_without_cryptography(self):
        """cryptography 미설치 시 HMAC fallback (encrypted:hmac:... 형식)."""
        with patch(
            "selfhealing.audit.masking._get_forensic_fernet", return_value=None
        ):
            result = mask_with_level("test", MaskingLevel.FORENSIC)
            assert result.startswith("encrypted:hmac:")

    def test_forensic_masking_with_cryptography(self):
        """cryptography 설치 시 Fernet 암호화 (encrypted:... 형식, hmac: 아님)."""
        mock_fernet = MagicMock()
        mock_fernet.encrypt.return_value = b"fernet_encrypted_data"
        with patch(
            "selfhealing.audit.masking._get_forensic_fernet",
            return_value=mock_fernet,
        ):
            result = mask_with_level("test", MaskingLevel.FORENSIC)
            assert result.startswith("encrypted:")
            assert not result.startswith("encrypted:hmac:")
```

### 7.6 시그널 핸들러 검증

**파일**: `tests/self_healing/integration/django/test_session_signals.py` (신규)

```python
import pytest
from unittest.mock import patch, MagicMock


class TestSessionSignalHandlers:
    """shopping/signals.py의 세션 시그널 핸들러 검증."""

    def test_login_signal_registers_session(self):
        """user_logged_in 시그널 발생 시 UserSessionRegistry.register() 호출 확인."""

    def test_login_signal_creates_session_key_if_missing(self):
        """session_key가 None일 때 request.session.save() 호출 확인."""

    def test_logout_signal_unregisters_session(self):
        """user_logged_out 시그널 발생 시 UserSessionRegistry.unregister() 호출 확인."""

    def test_signal_graceful_without_selfhealing(self):
        """selfhealing 미설치 시 ImportError를 조용히 처리하는지 확인."""

    def test_signal_skips_anonymous_user(self):
        """user가 None이거나 pk가 None일 때 registry 호출 안 함 확인."""
```

---

## 8. 구현 결과 (2026-02-11)

### 8.1 수정된 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| `myproject/settings/production.py` | 수정 | `SESSION_ENGINE`, `SESSION_CACHE_ALIAS` 추가 |
| `myproject/settings/local.py` | 수정 | 동일 |
| `packages/selfhealing-python/src/selfhealing/services/security/session_registry.py` | **신규** | `UserSessionRegistry` 클래스 |
| `packages/selfhealing-python/src/selfhealing/adapters/django/signal_hooks.py` | **신규** | 세션 시그널 핸들러 (`connect_session_signals`) |
| `packages/selfhealing-python/src/selfhealing/adapters/django/apps.py` | 수정 | `ready()`에 `_connect_session_signals()` 추가 |
| `packages/selfhealing-python/src/selfhealing/services/security/service.py` | 수정 | `_invalidate_user_sessions()` 재작성 |
| `packages/selfhealing-python/src/selfhealing/audit/masking.py` | 수정 | `decrypt_forensic()` 레거시/HMAC 감지 추가 |
| `packages/selfhealing-python/pyproject.toml` | 수정 | `[project.optional-dependencies]` forensic 추가 |

### 8.2 테스트 파일

| 파일 | 테스트 수 | 설명 |
|---|---|---|
| `packages/selfhealing-python/tests/unit/security/test_user_session_registry.py` | 14 | UserSessionRegistry 단위 테스트 |
| `packages/selfhealing-python/tests/unit/security/test_invalidate_sessions.py` | 8 | 재작성된 _invalidate_user_sessions 검증 |
| `packages/selfhealing-python/tests/unit/audit/test_decrypt_forensic.py` | 12 | decrypt_forensic 레거시/HMAC/Fernet 검증 |
| `tests/self_healing/integration/django/test_session_backend.py` | 4 | Redis 세션 백엔드 설정 검증 |
| `packages/selfhealing-python/tests/unit/security/test_session_signal_hooks.py` | 7 | 시그널 핸들러 검증 (`adapters/django/signal_hooks.py`) |

### 8.3 기존 테스트 수정

| 파일 | 변경 | 이유 |
|---|---|---|
| `packages/selfhealing-python/tests/unit/security/test_session_invalidation_hooks.py` | `"session_cache"` → `"redis_sessions"` (2건) | Dead Code 제거로 결과 문자열 변경 |

### 8.4 테스트 결과

- security + audit 전체: **48 passed, 0 failed** (218 관련 테스트)
- 기존 session_invalidation_hooks: **7 passed** (회귀 없음)

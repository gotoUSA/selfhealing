# 217. TOKEN_FORGED 위반 시 JWT 블랙리스트 연동 + 시크릿 검증 호출 구현

> **문서 번호**: 217
> **분류**: Security - Critical Gap Fix
> **선행 문서**: 214, 215, 216
> **작성일**: 2026-02-10
> **심각도**: CRITICAL (JWT), CRITICAL (시크릿 검증)

---

## 1. 취약점 #1: TOKEN_FORGED 시 JWT 블랙리스트 미연동

### 1.1 문제 정의

**TOKEN_FORGED 보안 위반 감지 시, Django 세션만 무효화하고 JWT 토큰은 블랙리스트에 추가하지 않는다.**

JWT가 주 인증 수단(`DEFAULT_AUTHENTICATION_CLASSES[0]`)이므로, 세션 무효화만으로는 탈취된 JWT access token의 사용을 막을 수 없다.

### 1.2 코드 근거

#### JWT가 주 인증 수단 (코드 확인)

**파일**: `myproject/settings/base.py` L283-L285

```python
"DEFAULT_AUTHENTICATION_CLASSES": [
    "rest_framework_simplejwt.authentication.JWTAuthentication",   # ← [0] 주 인증
    "rest_framework.authentication.SessionAuthentication",          # ← [1] 보조 인증
],
```

#### token_blacklist 앱 이미 설치됨

**파일**: `myproject/settings/base.py` L58

```python
"rest_framework_simplejwt.token_blacklist",
```

#### TOKEN_FORGED 감지 시 세션만 무효화

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/service.py` L280-L285

```python
if violation_type == ViolationType.TOKEN_FORGED.value:
    if user_id:
        action_taken = self._invalidate_user_sessions(user_id)  # ← 세션만 무효화
    else:
        action_taken = "Token forged but no user associated"
```

#### `_invalidate_user_sessions()`에 JWT 블랙리스트 로직 없음

**파일**: `service.py` L322-L403

이 메서드가 수행하는 작업:
1. Redis 캐시 키 삭제 (`user_session:{user_id}`, `user_token:{user_id}` 등)
2. Django DB 세션 순회 삭제 (`Session.objects.filter(...)`)
3. Audit 기록

**없는 것**: `OutstandingToken`, `BlacklistedToken` 관련 코드 전무.

#### shopping 앱에는 JWT 블랙리스트 패턴 존재 (참고용)

**파일**: `shopping/services/user_service.py` L244-L264

```python
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
# ...
outstanding_tokens = OutstandingToken.objects.filter(user=user)
for outstanding_token in outstanding_tokens:
    _, created = BlacklistedToken.objects.get_or_create(token=outstanding_token)
```

이것은 "회원 탈퇴" 시 JWT를 무효화하는 로직이다. 동일한 패턴을 보안 위반 시에도 적용해야 한다.

### 1.3 패키지 경계 문제

selfhealing 패키지는 `rest_framework_simplejwt`에 대한 의존성이 없다:

**파일**: `packages/selfhealing-python/pyproject.toml` L40-L44

```toml
dependencies = [
    "redis>=5.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
]
```

`OutstandingToken`, `BlacklistedToken`은 `rest_framework_simplejwt.token_blacklist.models`에 속하며, 이것은 호스트 앱(shopping/Django)의 영역이다.

---

## 2. 해결 방안: 콜백 패턴

### 2.1 설계 원칙

selfhealing 패키지가 `rest_framework_simplejwt`에 직접 의존할 수 없으므로, **콜백 패턴**을 사용한다:

1. selfhealing이 `on_session_invalidation` 콜백 훅을 제공
2. 호스트 앱(Django `AppConfig.ready()`)에서 JWT 블랙리스트 콜백을 등록
3. `_invalidate_user_sessions()` 실행 시 등록된 콜백 호출

### 2.2 기존 패턴 확인: EventBus

selfhealing에는 이미 이벤트 시스템이 있다:

**파일**: `services/security/service.py` L596-L626

```python
def _emit_critical_violation_event(self, violation_type, incident_id, source_ip, user_id):
    from selfhealing.services.event_bus import EventType, get_event_bus
    bus = get_event_bus()
    bus.emit(event_type=EventType.SECURITY_VIOLATION_CRITICAL, ...)
```

그러나 `_invalidate_user_sessions()`는 `handle_violation()` → `_take_protective_action()` 내부에서 호출되므로, **동기적 콜백**이 더 적합하다 (JWT 무효화도 같은 트랜잭션 내에서 완료되어야 함).

### 2.3 구현 명세

#### 2.3.1 신규: 세션 무효화 콜백 레지스트리

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/hooks.py` (신규)

```python
"""
Security Hooks - 보안 이벤트 콜백 레지스트리.

selfhealing 패키지가 호스트 앱의 인증 시스템에 의존하지 않으면서도
보안 위반 시 호스트 앱의 토큰 무효화 등을 트리거할 수 있도록 합니다.

Usage (호스트 앱의 AppConfig.ready()):
    from selfhealing.services.security.hooks import register_session_invalidation_hook

    def blacklist_user_jwt(user_id: int) -> str:
        from rest_framework_simplejwt.token_blacklist.models import (
            BlacklistedToken, OutstandingToken,
        )
        tokens = OutstandingToken.objects.filter(user_id=user_id)
        count = 0
        for token in tokens:
            _, created = BlacklistedToken.objects.get_or_create(token=token)
            if created:
                count += 1
        return f"jwt_blacklisted({count})"

    register_session_invalidation_hook(blacklist_user_jwt)
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

# 콜백 타입: (user_id: int) -> str (결과 설명)
SessionInvalidationHook = Callable[[int], str]

_hooks: list[SessionInvalidationHook] = []


def register_session_invalidation_hook(hook: SessionInvalidationHook) -> None:
    """
    세션 무효화 시 실행할 콜백을 등록.

    등록된 콜백은 _invalidate_user_sessions(user_id)가 호출될 때
    순서대로 실행됩니다.

    Args:
        hook: (user_id: int) -> str 형태의 콜백 함수
    """
    _hooks.append(hook)
    logger.info(
        f"[SecurityHooks] Session invalidation hook registered: "
        f"{hook.__module__}.{hook.__qualname__}"
    )


def get_session_invalidation_hooks() -> list[SessionInvalidationHook]:
    """등록된 세션 무효화 콜백 목록 반환."""
    return list(_hooks)


def clear_session_invalidation_hooks() -> None:
    """모든 콜백 제거 (테스트용)."""
    _hooks.clear()
```

#### 2.3.2 수정: `_invalidate_user_sessions()`에 콜백 호출 추가

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/service.py`

`_invalidate_user_sessions()` 내부, Django 세션 삭제 후 (L373 이후)에 추가:

```python
            # 4. 등록된 세션 무효화 콜백 실행 (JWT 블랙리스트 등)
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
```

#### 2.3.3 수정: 호스트 앱 AppConfig에서 콜백 등록

**파일**: `selfhealing/adapters/django/apps.py`의 `ready()` 메서드에 추가:

```python
        # Register JWT blacklist hook for session invalidation
        self._register_jwt_blacklist_hook()

    def _register_jwt_blacklist_hook(self):
        """
        JWT 블랙리스트 콜백 등록.

        rest_framework_simplejwt.token_blacklist가 INSTALLED_APPS에 있을 때만
        콜백을 등록합니다.
        """
        try:
            from django.apps import apps
            if not apps.is_installed("rest_framework_simplejwt.token_blacklist"):
                logger.debug("[SelfHealing] token_blacklist not installed, skipping JWT hook")
                return

            from selfhealing.services.security.hooks import (
                register_session_invalidation_hook,
            )

            def blacklist_user_jwt(user_id: int) -> str:
                """사용자의 모든 OutstandingToken을 블랙리스트에 추가."""
                from rest_framework_simplejwt.token_blacklist.models import (
                    BlacklistedToken,
                    OutstandingToken,
                )
                tokens = OutstandingToken.objects.filter(user_id=user_id)
                count = 0
                for token in tokens:
                    _, created = BlacklistedToken.objects.get_or_create(token=token)
                    if created:
                        count += 1
                return f"jwt_blacklisted({count})" if count > 0 else ""

            register_session_invalidation_hook(blacklist_user_jwt)
            logger.info("[SelfHealing] JWT blacklist hook registered")

        except ImportError as e:
            logger.debug(f"[SelfHealing] JWT hook registration skipped: {e}")
        except Exception as e:
            logger.warning(f"[SelfHealing] JWT hook registration failed: {e}")
```

---

## 3. 취약점 #2: `validate_required_secrets()` 미호출

### 3.1 문제 정의

**`validate_required_secrets()` 함수가 존재하지만 `ready()`에서 호출되지 않는다.**

### 3.2 코드 근거

#### 함수 정의 (존재함)

**파일**: `packages/selfhealing-python/src/selfhealing/settings/secrets.py` L192-L273

```python
def validate_required_secrets(secrets: SecretsSettings | None = None) -> dict:
    """
    핵심 시크릿이 설정되었는지 검증.
    ...
    프로덕션 환경에서 CRITICAL 시크릿 미설정 시 RuntimeError 발생.
    """
```

검증 대상:
- **CRITICAL**: `encryption_key`, `audit_signing_key`
- **IMPORTANT**: `database_password`, `redis_password`
- **OPTIONAL**: `toss_secret_key`, `slack_webhook_token` 등 6개

#### `ready()`에서 미호출 (재확인)

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/django/apps.py` L112-L163

```python
def ready(self):
    post_migrate.connect(create_selfhealing_groups, ...)
    self._log_env_snapshot()
    self._sync_hash_chain_on_startup()
    self._validate_startup_config()        # ← config 검증은 있음
    self._schedule_gauge_hydration()
    self._start_precomputed_cache_worker()
    self._start_meta_watchdog()
    # ← validate_required_secrets() 호출 없음
```

### 3.3 구현 명세

#### 수정: `ready()`에 시크릿 검증 단계 추가

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/django/apps.py`

`self._validate_startup_config()` 호출 직후에 추가:

```python
        # Validate required secrets (Security Hardening)
        self._validate_secrets()
```

새 메서드:

```python
    def _validate_secrets(self):
        """
        핵심 시크릿 검증.

        Best-effort: 검증 실패 자체가 시스템 시작을 막지 않음.
        단, 프로덕션 환경에서 CRITICAL 시크릿 미설정 시 RuntimeError 발생.
        (validate_required_secrets() 내부에서 처리)

        Reference: 215_SECURITY_VULNERABILITY_FIXES_PART2 섹션 6.1
        """
        try:
            from selfhealing.settings.secrets import validate_required_secrets
            result = validate_required_secrets()

            critical_count = len(result.get("critical", []))
            warning_count = len(result.get("warning", []))

            if critical_count > 0:
                logger.error(
                    f"[SelfHealing] {critical_count} CRITICAL secrets not configured. "
                    "Check logs for details."
                )
            elif warning_count > 0:
                logger.warning(
                    f"[SelfHealing] {warning_count} important secrets not configured. "
                    "Check logs for details."
                )
            else:
                logger.info("[SelfHealing] All secrets validated successfully")

        except RuntimeError:
            # 프로덕션에서 CRITICAL 시크릿 미설정 → 재발생으로 시작 차단
            raise
        except Exception as e:
            # 기타 오류 → best-effort로 시작 계속
            logger.warning(f"[SelfHealing] Secrets validation failed: {e}")
```

---

## 4. 시퀀스 다이어그램: TOKEN_FORGED 방어 흐름 (수정 후)

```
공격자 요청 (탈취된 JWT)
     │
     ▼
[IPBanMiddleware] ──── ban된 IP? ──── Yes → 403 거부
     │ No
     ▼
[Django Auth] ──── JWT 유효? ──── No → 401 거부
     │ Yes                          (BlacklistedToken에 있으면 무효)
     ▼
[비즈니스 로직] ──── 이상 감지? ──── Yes →
     │                                    │
     ▼                                    ▼
 정상 응답                    SecurityViolationService.handle_violation()
                                          │
                              ┌───────────┼───────────────┐
                              ▼           ▼               ▼
                    세션 무효화    IP 임시 차단      JWT 블랙리스트  ← 신규
                    (cache+DB)   (Redis TTL)     (콜백 호출)
```

---

## 5. 영향 분석

### 5.1 수정 대상 파일

| 파일 | 변경 유형 | 설명 |
|---|---|---|
| `services/security/hooks.py` | **신규** | 세션 무효화 콜백 레지스트리 |
| `services/security/service.py` | 수정 | `_invalidate_user_sessions()`에 콜백 호출 추가 |
| `services/security/__init__.py` | 수정 | hooks 함수 export 추가 |
| `adapters/django/apps.py` | 수정 | `ready()`에 JWT 훅 등록 + 시크릿 검증 추가 |

### 5.2 패키지 의존성

- selfhealing 패키지: **새 의존성 없음** (콜백은 호스트 앱이 등록)
- `rest_framework_simplejwt`는 `TYPE_CHECKING` 또는 lazy import로만 참조
- `token_blacklist` 없는 환경에서는 훅이 등록되지 않을 뿐 에러 없음

### 5.3 하위 호환성

- 콜백 미등록 시: 기존 동작 그대로 (`_invalidate_user_sessions()`의 세션/캐시 무효화만 실행)
- 콜백 등록 시: 기존 동작 + JWT 블랙리스트 추가 실행
- 콜백 실패 시: 경고 로그만 남기고 나머지 무효화 계속 진행

---

## 6. 테스트 명세

### 6.1 hooks.py 단위 테스트

```python
class TestSecurityHooks:
    def test_register_hook(self):
        """콜백 등록 후 목록에 포함되는지 확인."""

    def test_clear_hooks(self):
        """clear_session_invalidation_hooks() 후 빈 리스트 확인."""

    def test_multiple_hooks_order(self):
        """여러 콜백이 등록 순서대로 반환되는지 확인."""
```

### 6.2 service.py 콜백 호출 테스트

```python
class TestInvalidateUserSessionsWithHooks:
    def test_hook_called_on_invalidation(self):
        """_invalidate_user_sessions() 호출 시 등록된 콜백이 실행되는지 확인."""

    def test_hook_result_in_invalidated_items(self):
        """콜백 반환값이 invalidated_items에 포함되는지 확인."""

    def test_hook_failure_does_not_block(self):
        """콜백 실패 시 나머지 무효화가 계속 진행되는지 확인."""

    def test_no_hooks_behaves_same_as_before(self):
        """콜백 미등록 시 기존 동작과 동일한지 확인."""
```

### 6.3 apps.py 통합 테스트

```python
class TestJWTBlacklistHookRegistration:
    def test_hook_registered_when_token_blacklist_installed(self):
        """token_blacklist 앱 설치 시 훅이 등록되는지 확인."""

    def test_hook_not_registered_when_token_blacklist_missing(self):
        """token_blacklist 미설치 시 훅이 등록되지 않는지 확인."""

    def test_validate_secrets_called_in_ready(self):
        """ready()에서 validate_required_secrets()가 호출되는지 확인."""
```

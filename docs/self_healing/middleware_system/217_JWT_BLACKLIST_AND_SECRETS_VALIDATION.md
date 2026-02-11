# 217. TOKEN_FORGED 위반 시 JWT 블랙리스트 연동 + 시크릿 검증 호출 구현

> **문서 번호**: 217
> **분류**: Security - Critical Gap Fix
> **선행 문서**: 214, 215, 216
> **작성일**: 2026-02-10
> **구현일**: 2026-02-11
> **심각도**: CRITICAL (JWT), CRITICAL (시크릿 검증)
> **상태**: ✅ 구현 완료

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
# ──────────────────────────────────────────────────────────────────
# user_id 타입이 int인 근거 (리뷰 #1 반영):
#   - shopping.User(AbstractUser)에 커스텀 PK 없음 → default PK 사용
#   - DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField" (base.py L267)
#   - BigAutoField는 Python int 타입
# 파일: shopping/models/user.py L20 | myproject/settings/base.py L267
# ──────────────────────────────────────────────────────────────────
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

            # TODO(#217): OutstandingToken 정리를 위해 Celery Beat에 flushexpiredtokens 등록 필요
            # 블랙리스트에 추가된 토큰의 OutstandingToken 레코드가 DB에 계속 누적됨.
            # OutstandingToken.user_id에는 FK 인덱스가 있으나, 만료 토큰 정리는 별도 필요.
            # Django 프로젝트의 CELERY_BEAT_SCHEDULE에 추가할 것:
            #   'flush-expired-tokens': {
            #       'task': 'django.core.management.call_command',
            #       'schedule': crontab(hour=2, minute=0),  # 매일 02:00
            #       'args': ('flushexpiredtokens',),
            #   }
            # Reference: simplejwt 내장 management command 'flushexpiredtokens'

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

        동작 모드:
        - Non-production: best-effort (검증 실패해도 시스템 시작 계속)
        - Production + CRITICAL 시크릿 미설정: RuntimeError 재발생으로 시작 차단

        Note: _validate_startup_config()은 모든 예외를 warning 처리(best-effort)하지만,
        이 메서드는 프로덕션 CRITICAL 시크릿에 한해 의도적으로 시작을 차단함.
        보안 시크릿 미설정 상태로 운영하는 것은 용납할 수 없기 때문.

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

        except RuntimeError as e:
            # 프로덕션에서 CRITICAL 시크릿 미설정 → 재발생으로 시작 차단
            # ──────────────────────────────────────────────────────────
            # 리뷰 #2 반영: traceback + resolution guide 추가
            # secrets.py L240-L254가 이미 개별 시크릿별 ERROR/WARNING을 로깅하지만,
            # traceback과 해결 방법(환경변수 설정 가이드)은 제공하지 않음.
            # 이 블록에서 보완하여 운영자가 즉시 조치할 수 있도록 함.
            # ──────────────────────────────────────────────────────────
            logger.critical(
                f"[SelfHealing] Secrets validation FAILED: {e}\n"
                "Resolution: Set the missing environment variables before starting.\n"
                "  CRITICAL secrets (env_prefix='SELFHEALING_SECRET_'):\n"
                "  - SELFHEALING_SECRET_ENCRYPTION_KEY: 데이터 암호화 키\n"
                "  - SELFHEALING_SECRET_AUDIT_SIGNING_KEY: 감사 로그 서명 키\n"
                "See: selfhealing/settings/secrets.py SecretsSettings 클래스 참조",
                exc_info=True,
            )
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

    def test_validate_secrets_critical_failure_logs_resolution_guide(self):
        """프로덕션 CRITICAL 시크릿 미설정 시 traceback + resolution guide가 로깅되는지 확인."""
```

---

## 7. 사전 리뷰 반영 사항

> 구현 전 코드 근거 검증(사전 질의 6건)과 리뷰(3건)를 수행하여 아래 사항을 본 문서에 반영함.

### 7.1 리뷰 #1: `SessionInvalidationHook` 타입 확인 — `Callable[[int], str]` 유지

| 항목 | 내용 |
|---|---|
| 판정 | **수정 불필요** (원안 유지) |
| 반영 위치 | 섹션 2.3.1 `hooks.py` 코드 블록 — 타입 근거 주석 추가 |

**코드 근거**:
- `shopping/models/user.py` L20: `class User(AbstractUser)` — 커스텀 PK 필드 없음
- `myproject/settings/base.py` L267: `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`
- `BigAutoField`는 Python `int` 타입이므로 `Callable[[int], str]`이 정확함
- `SIMPLE_JWT` 설정: `USER_ID_FIELD = "id"`, `USER_ID_CLAIM = "user_id"` — PK를 그대로 사용

### 7.2 리뷰 #2: `_validate_secrets` 에러 로깅 강화 — traceback + resolution guide

| 항목 | 내용 |
|---|---|
| 판정 | **보완** (except RuntimeError 블록 강화) |
| 반영 위치 | 섹션 3.3 `_validate_secrets()` 메서드 코드 블록 |

**코드 근거**:

| 기존 제공 (secrets.py) | 미제공 → 본 리뷰에서 추가 |
|---|---|
| 개별 시크릿별 ERROR/WARNING 로그 (L240-L254) | traceback (`exc_info=True`) |
| RuntimeError 메시지에 누락 시크릿 이름 포함 (L266) | resolution guide (환경변수명 + 설정 방법) |
| — | `[SelfHealing]` 접두사 통합 로그 |

**설계 결정**: `_validate_startup_config()`과의 동작 차이

| 메서드 | 예외 처리 | 근거 |
|---|---|---|
| `_validate_startup_config()` (apps.py L289) | Best-effort: `logger.warning()` 후 계속 | 설정은 Safe Default 적용 가능 |
| `_validate_secrets()` | Production CRITICAL → `raise` (시작 차단) | 암호화 키 없이 운영 불가 |

이 차이는 의도적이며 docstring에 명시함.

**환경변수 접두사 근거**:
- `secrets.py` L54: `env_prefix="SELFHEALING_SECRET_"`
- CRITICAL 시크릿: `SELFHEALING_SECRET_ENCRYPTION_KEY`, `SELFHEALING_SECRET_AUDIT_SIGNING_KEY`

### 7.3 리뷰 #3: `flushexpiredtokens` Celery Beat TODO 추가

| 항목 | 내용 |
|---|---|
| 판정 | **추가** (TODO 코멘트) → **구현 완료** |
| 반영 위치 | `tasks/cleanup_tasks.py` + `adapters/django/apps.py` DONE 코멘트 |

**코드 근거**:
- 코드베이스 전체 `flushexpiredtokens` 검색 결과: ~~**0건**~~ → **구현 완료**
- `OutstandingToken`은 `user_id` FK 인덱스가 있으나 (Django 자동 생성), 만료 토큰 자체의 정리는 별도 필요
- `simplejwt`의 내장 management command `flushexpiredtokens`가 이 용도로 제공됨

**구현 위치**:

| 파일 | 역할 |
|---|---|
| `selfhealing/tasks/cleanup_tasks.py` | Thin wrapper `flush_expired_jwt_tokens()` + `@shared_task` 래퍼 + `get_cleanup_beat_schedule()` 엔트리 |
| `myproject/celery.py` | `flush-expired-jwt-tokens` beat schedule 엔트리 (매일 02:30, maintenance 큐) |
| `adapters/django/apps.py` | TODO → DONE 코멘트 변경 |

---

## 8. 구현 이력

| 날짜 | 항목 | 상태 |
|---|---|---|
| 2026-02-11 | `services/security/hooks.py` 신규 생성 | ✅ |
| 2026-02-11 | `services/security/service.py` 콜백 호출 추가 (`_invalidate_user_sessions`) | ✅ |
| 2026-02-11 | `services/security/__init__.py` hooks export 추가 | ✅ |
| 2026-02-11 | `adapters/django/apps.py` `_validate_secrets()` 추가 | ✅ |
| 2026-02-11 | `adapters/django/apps.py` `_register_jwt_blacklist_hook()` 추가 | ✅ |
| 2026-02-11 | 단위 테스트 26개 작성 (hooks 6 + service 7 + apps 13) | ✅ |
| 2026-02-11 | 통합 테스트 4개 추가 (`test_app_config.py`) | ✅ |
| 2026-02-11 | `tasks/cleanup_tasks.py` `flush_expired_jwt_tokens` 추가 (TODO #217 구현) | ✅ |
| 2026-02-11 | `myproject/celery.py` beat schedule 엔트리 추가 | ✅ |
| 2026-02-11 | `adapters/django/apps.py` TODO → DONE 코멘트 변경 | ✅ |
| 2026-02-11 | 단위 테스트 11개 작성 (`test_flush_expired_jwt_tokens.py`) | ✅ |

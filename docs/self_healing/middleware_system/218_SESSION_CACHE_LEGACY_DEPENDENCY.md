# 218. 세션 성능 개선, 캐시 키 상수화, 레거시 해시 호환, 의존성 수정

> **문서 번호**: 218
> **분류**: Security - Performance / Hardening / Dependency Fix
> **선행 문서**: 214, 215, 216, 217
> **작성일**: 2026-02-10
> **심각도**: HIGH (세션), MEDIUM (캐시 키, 레거시 해시), LOW (의존성)

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
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

**파일 2**: `myproject/settings/local.py`

```python
# 동일하게 추가:
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"
```

#### selfhealing 패키지 변경: 불필요

이 변경은 **호스트 앱(Django settings)의 설정 변경**이다. selfhealing 패키지의 `_invalidate_user_sessions()` 코드는 수정하지 않아도 된다:

```python
# service.py L354: Django 세션 백엔드가 있으면 DB 세션도 삭제
try:
    from django.contrib.sessions.models import Session
```

`SESSION_ENGINE = "cache"` 사용 시, `Session` 모델 import가 `ImportError`로 처리되어 DB 세션 삭제 블록을 건너뛴다. Redis 세션은 캐시 키 삭제(`user_session:{user_id}`)로 처리된다.

**단, 주의사항**: Redis 캐시 세션 (`django.contrib.sessions.backends.cache`)은 Django의 `Session` 모델을 사용하지 않으므로, `Session.objects.filter()`가 빈 결과를 반환하거나 import 가능하나 테이블이 비어있게 된다. 이것은 기능적으로 올바르다 - 세션 데이터가 Redis에만 존재하므로.

#### 세션 무효화의 실제 동작 (Redis 전환 후)

Redis 세션 사용 시, `_invalidate_user_sessions()`의 동작:

1. **Step 1** (L338): `cache.delete("user_session:{user_id}")` → **Redis에서 직접 삭제 (O(1))**
2. **Step 2** (L341-L349): 관련 캐시 키 삭제 → **Redis에서 직접 삭제 (O(1))**
3. **Step 3** (L354-L369): Django Session DB 삭제 → **빈 결과 (Redis에 세션 있으므로)** 또는 ImportError 스킵

이 방식의 한계: `user_session:{user_id}` 키와 Django의 자동 생성 세션 키(예: `django.contrib.sessions.cache:abc123xyz`)가 **다른 키 패턴**이다. 이것은 215 문서 섹션 6.2에서 분석한 바와 같이, 근본적으로 Django 세션은 `user_id` 기반 역방향 조회를 지원하지 않는 설계 한계이다.

**결론**: JWT가 주 인증 수단이고(`base.py` L283), Django 세션은 보조이므로, JWT 블랙리스트(217 문서)와 결합하면 충분한 방어가 된다.

---

## 2. 취약점 #2: 캐시 키 하드코딩

### 2.1 문제 정의

`_invalidate_user_sessions()`에서 사용하는 캐시 키 프리픽스가 하드코딩되어 있으며, 이 키들을 실제로 생성하는 코드가 존재하지 않는다.

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
grep "user_token:" → 결과: service.py L342 (삭제만)
grep "user_permissions:" → 결과: service.py L343 (삭제만)
grep "user_auth:" → 결과: service.py L344 (삭제만)
```

반면, `SecuritySettings`에는 `suspicious_ip_cache_prefix`와 `banned_ip_cache_prefix`가 설정 가능형으로 존재:

**파일**: `settings/security.py` L108-L115

```python
suspicious_ip_cache_prefix: str = Field(
    default="security:suspicious_ip:",
    description="Redis key prefix for suspicious IPs",
)
banned_ip_cache_prefix: str = Field(
    default="security:banned_ip:",
    description="Redis key prefix for banned IPs",
)
```

### 2.3 해결 방안

> **참고**: 215 문서 섹션 6.4에서 분석 완료. 본 문서는 구현 명세.

#### 2.3.1 `SecuritySettings`에 세션 관련 프리픽스 추가

**파일**: `packages/selfhealing-python/src/selfhealing/settings/security.py`

```python
    # ==========================================================================
    # Session Invalidation Prefixes (216/217/218 Security Fix)
    # ==========================================================================
    session_cache_prefix: str = Field(
        default="user_session:",
        description="Redis key prefix for user sessions",
    )
    session_related_cache_prefixes: list[str] = Field(
        default=["user_token:", "user_permissions:", "user_auth:"],
        description="Additional cache key prefixes to clear on session invalidation",
    )
```

#### 2.3.2 `SecurityConfig`에 필드 추가

**파일**: `packages/selfhealing-python/src/selfhealing/services/security/models.py`

```python
@dataclass
class SecurityConfig:
    # ... 기존 필드 ...
    session_cache_prefix: str = "user_session:"
    session_related_cache_prefixes: list[str] = field(
        default_factory=lambda: ["user_token:", "user_permissions:", "user_auth:"]
    )

    @classmethod
    def from_settings(cls) -> SecurityConfig:
        security = get_security_settings()
        return cls(
            # ... 기존 필드 매핑 ...
            session_cache_prefix=security.session_cache_prefix,
            session_related_cache_prefixes=security.session_related_cache_prefixes,
        )
```

#### 2.3.3 `_invalidate_user_sessions()`에서 config 사용

**파일**: `service.py` 수정

```python
# 기존:
cache_key = f"user_session:{user_id}"
# 수정:
cache_key = f"{self.config.session_cache_prefix}{user_id}"

# 기존:
related_prefixes = [
    f"user_token:{user_id}",
    f"user_permissions:{user_id}",
    f"user_auth:{user_id}",
]
# 수정:
related_prefixes = [
    f"{prefix}{user_id}"
    for prefix in self.config.session_related_cache_prefixes
]
```

---

## 3. 취약점 #3: `decrypt_forensic()` 레거시 SHA-256 해시 미감지

### 3.1 문제 정의

`decrypt_forensic()`이 `encrypted:` 접두사가 없는 값은 모두 `ValueError`로 거부한다. Fernet 도입 이전에 `hash_for_audit()`으로 저장된 `sha256:` 형식의 레거시 데이터를 처리하지 못한다.

### 3.2 코드 근거

**파일**: `audit/masking.py` L151-L167

```python
def decrypt_forensic(encrypted_value: str) -> str:
    if not encrypted_value.startswith("encrypted:"):
        raise ValueError("Not a FORENSIC encrypted value (must start with 'encrypted:')")
    # ... Fernet 복호화만 시도
```

레거시 데이터 형식: `sha256:a1b2c3d4e5f6...` (L304-L320의 `hash_for_audit()` 출력)

```python
# L318-L320:
def hash_for_audit(value: str, salt: str | None = None) -> str:
    # ...
    return f"sha256:{hash_digest[:16]}"
```

### 3.3 해결 방안

> **참고**: 215 문서 섹션 6.3에서 분석 완료. 본 문서는 구현 명세.

#### 수정: `decrypt_forensic()`에 레거시 감지 추가

**파일**: `packages/selfhealing-python/src/selfhealing/audit/masking.py`

```python
def decrypt_forensic(encrypted_value: str) -> str:
    """
    FORENSIC 레벨로 암호화된 값을 복호화.

    레거시 호환 (218_SESSION_CACHE_LEGACY_DEPENDENCY):
    - "sha256:" 접두사: 해시 값으로 복원 불가 → 명확한 에러 메시지
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

    token = encrypted_value[len("encrypted:"):]

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

| 파일 | 변경 유형 | 취약점 # | 설명 |
|---|---|---|---|
| `myproject/settings/production.py` | 수정 | #1 | `SESSION_ENGINE`, `SESSION_CACHE_ALIAS` 추가 |
| `myproject/settings/local.py` | 수정 | #1 | 동일 |
| `settings/security.py` | 수정 | #2 | `session_cache_prefix`, `session_related_cache_prefixes` 추가 |
| `services/security/models.py` | 수정 | #2 | `SecurityConfig`에 세션 필드 추가 + `from_settings()` 매핑 |
| `services/security/service.py` | 수정 | #2 | 하드코딩 → `self.config` 참조로 변경 |
| `audit/masking.py` | 수정 | #3 | `decrypt_forensic()`에 `sha256:` 감지 추가 |
| `packages/selfhealing-python/pyproject.toml` | 수정 | #4 | `[project.optional-dependencies]` forensic 추가 |

---

## 6. 하위 호환성 분석

| 변경 | 하위 호환 | 근거 |
|---|---|---|
| Redis 세션 백엔드 | ✅ | Django settings 변경만, 코드 변경 없음 |
| 캐시 키 상수화 | ✅ | 기본값이 기존 하드코딩 값과 동일 |
| 레거시 해시 감지 | ✅ | 기존 `encrypted:` 값은 동일하게 처리, `sha256:` 값은 기존에도 `ValueError`였으나 메시지가 더 명확해짐 |
| cryptography optional dep | ✅ | 기존 동작 변경 없음, 설치 옵션만 추가 |

---

## 7. 테스트 명세

### 7.1 세션 백엔드 전환 검증

```python
class TestRedisSessionBackend:
    def test_session_engine_is_cache(self):
        """SESSION_ENGINE이 'cache'로 설정되었는지 확인."""

    def test_session_cache_alias_is_default(self):
        """SESSION_CACHE_ALIAS가 'default'인지 확인."""

    def test_session_stored_in_redis(self):
        """세션 생성 시 Redis에 저장되는지 확인."""
```

### 7.2 캐시 키 상수화 검증

```python
class TestCacheKeyConstantization:
    def test_default_values_match_hardcoded(self):
        """기본값이 기존 하드코딩 값과 동일한지 확인."""

    def test_custom_prefix_used(self):
        """설정 변경 시 커스텀 프리픽스가 사용되는지 확인."""

    def test_config_from_settings(self):
        """SecurityConfig.from_settings()가 새 필드를 올바르게 매핑하는지 확인."""
```

### 7.3 레거시 해시 감지 검증

```python
class TestDecryptForensicLegacy:
    def test_sha256_prefix_raises_clear_error(self):
        """sha256: 값에 대해 복원 불가 메시지를 포함한 ValueError."""

    def test_encrypted_prefix_still_works(self):
        """encrypted: 값은 기존대로 Fernet 복호화."""

    def test_unknown_prefix_raises_error(self):
        """알 수 없는 접두사에 대한 ValueError."""
```

### 7.4 의존성 검증

```python
class TestCryptographyOptionalDep:
    def test_forensic_masking_without_cryptography(self):
        """cryptography 미설치 시 AUDIT 레벨로 폴백."""

    def test_forensic_masking_with_cryptography(self):
        """cryptography 설치 시 Fernet 암호화."""
```

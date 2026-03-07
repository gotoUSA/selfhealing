# 로깅 표준 가이드라인

> **적용 범위**: `packages/selfhealing-python/src/selfhealing/` 전체
> **최종 수정일**: 2026-03-07
> **근거 문서**: `docs/self_healing/middleware_system/312_EXCEPTION_HIERARCHY_LOGGING_STANDARDIZATION.md` §7

---

## 1. 이벤트명 컨벤션

### 1.1 형식

```
{component}.{entity}_{action}
```

| 요소 | 설명 | 예시 |
|------|------|------|
| `component` | 모듈명 | `registry`, `circuit_breaker`, `dlq`, `audit`, `replay` |
| `entity` | 대상 명사 | `cache`, `entry`, `state`, `adapter`, `connection` |
| `action` | 과거형 동사 | `registered`, `created`, `changed`, `failed`, `completed` |

### 1.2 규칙

- 모든 이벤트명은 **소문자 + 밑줄**만 사용: `[a-z_]+\.[a-z_]+`
- 최소 하나의 `.`(dot)이 포함되어야 한다 (component와 action 구분)
- 동적 변수로 이벤트명을 구성하지 않는다: `logger.debug(variable)` ❌
- 문자열 리터럴로 직접 작성한다: `logger.debug("registry.cache_registered")` ✅

### 1.3 런타임 검증 (Q5 결정)

`settings/log_processors.py`에 이벤트명 검증 프로세서를 추가한다.
파이프라인 위치: `add_logger_name` 직후.

| 환경 | 동작 |
|------|------|
| `SELFHEALING_STRICT_LOG_VALIDATION=true` (DEV/TEST) | 컨벤션 위반 시 `ValueError` 발생 (fail-fast) |
| Production (기본값) | 위반을 Prometheus counter(`selfhealing_log_convention_violations_total`)로 기록만 |

---

## 2. 예외 체이닝 (Q3 결정)

외부 예외를 도메인 예외로 감쌀 때 반드시 `from` 절을 사용한다.

```python
# ✅ 올바른 패턴
try:
    redis_client.get(key)
except redis.TimeoutError as e:
    raise AdapterConnectionError(f"Redis timeout: {e}") from e

# ❌ 금지 — 원본 traceback 유실
try:
    redis_client.get(key)
except redis.TimeoutError as e:
    raise AdapterConnectionError(f"Redis timeout: {e}")

# ✅ 의도적 체인 끊기 (이유를 주석으로 명시)
try:
    decrypt(data)
except CryptoError as e:
    # 보안: 복호화 실패의 원인을 노출하지 않음
    raise AuthenticationError("Invalid credentials") from None
```

**Lint 강제**: `ruff` 룰 `B904` (`raise-without-from-inside-except`) 활성화 대상.

---

## 3. Suffix별 로그 레벨 가이드라인 (Q6 결정)

런타임으로 강제하지 않고 가이드라인으로 관리한다.
코드 리뷰 시 아래 테이블을 기준으로 검증한다.

| Suffix | 권장 최소 레벨 | 비고 |
|--------|--------------|------|
| `_failed` | `WARNING` | 단, retry 내부 중간 실패는 `DEBUG` 허용 |
| `_exhausted` | `WARNING` | 모든 재시도 소진 |
| `_error` | `ERROR` | 예상치 못한 에러 |
| `_blocked` | `WARNING` | CB open, budget blocked 등 |
| `_timeout` | `WARNING` | 타임아웃 발생 |
| `_registered`, `_created`, `_resolved` | `DEBUG` | 정상 흐름 |
| `_changed`, `_updated` | `INFO` | 상태 변경 |
| `_completed`, `_started` | `DEBUG` 또는 `INFO` | 작업 흐름 |

### 3.1 예외 허용 사례

`_failed` suffix를 `DEBUG`로 사용할 수 있는 정당한 사례:

- **비핵심 보조 기능의 fail-open**: 메인 로직에 영향 없이 빈 값으로 대체되는 경우
  - 예: ML 학습 스냅샷 실패 → 빈 스냅샷 반환 (`pattern_matcher.learning_snapshot_failed`)
- **retry 내부 중간 실패**: 최종 결과는 성공할 수 있는 중간 단계

위 사례 외에 `_failed`를 `DEBUG`/`INFO`로 사용하면 위반으로 간주한다.

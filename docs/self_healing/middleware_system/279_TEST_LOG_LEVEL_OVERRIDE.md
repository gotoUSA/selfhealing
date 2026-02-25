# 279. 테스트 로그 레벨 오버라이드

> **Status**: Implemented
> **Priority**: 1 (즉시 효과)
> **References**:
> - `pyproject.toml` — `[tool.pytest.ini_options]` log_level, `[tool.pytest_env]` SELFHEALING_TEST_LOG_LEVEL
> - `packages/selfhealing-python/tests/conftest.py` — `_isolate_logging_state_session` fixture
> - `packages/selfhealing-python/src/selfhealing/settings/structlog_config.py` — `root_logger.setLevel(logging.DEBUG)` (L149)

---

## 1. 문제

테스트 실행 시 로그가 과도하게 출력되어 테스트 결과 확인이 어려움.

**근본 원인**:

| 위치 | 문제 |
|------|------|
| `structlog_config.py` L149 | `root_logger.setLevel(logging.DEBUG)` — 모든 레벨 통과 |
| `conftest.py` `_isolate_logging_state_session` | `propagate=True`, `handlers=[]`만 설정하고 **레벨 제어 없음** |
| `conftest.py` `_ensure_selfhealing_propagates` | 같은 패턴 — `setLevel()` 미호출 |

결과: root logger `DEBUG` + selfhealing 로거 `propagate=True` → 4,795개 로그 포인트의 모든 출력이 콘솔에 노출

## 2. 해결 방법

두 레이어에서 로그 레벨을 제어한다.

### 2.1 pyproject.toml — pytest 로그 캡처 레벨

```toml
# pyproject.toml [tool.pytest.ini_options] 에 추가
log_level = "WARNING"
log_cli_level = "WARNING"
```

pytest의 로그 캡처 시스템이 WARNING 미만을 무시한다.

### 2.2 pyproject.toml — 환경변수 주입

```toml
# [tool.pytest_env] 에 추가
SELFHEALING_TEST_LOG_LEVEL = "WARNING"
```

### 2.3 conftest.py — `_isolate_logging_state_session` 수정

```python
@pytest.fixture(autouse=True, scope="session")
def _isolate_logging_state_session():
    import logging as _logging

    root = _logging.getLogger()
    root_level = root.level
    root_handlers = list(root.handlers)

    # ▼ 추가: 테스트 환경 로그 레벨 오버라이드
    test_log_level_name = os.environ.get("SELFHEALING_TEST_LOG_LEVEL", "WARNING")
    test_log_level = getattr(_logging, test_log_level_name.upper(), _logging.WARNING)
    root.setLevel(test_log_level)

    # (기존 코드 유지)
    _saved: dict[str, tuple[int, bool, list]] = {}
    for name, logger_obj in _logging.Logger.manager.loggerDict.items():
        ...
```

## 3. 효과

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| root logger 레벨 (테스트 시) | `DEBUG` (10) | `WARNING` (30) |
| 출력되는 로그 비율 | ~100% (4,795 포인트) | ~33% (warning 1,519 + error 76 + exception 762) |
| 디버깅 시 | 불가능 | `SELFHEALING_TEST_LOG_LEVEL=DEBUG pytest ...` |

## 4. 성능 영향

- `setLevel()`은 세션당 1회만 호출 (기존 최적화 유지)
- `_clear_cache()` 오버헤드: 165개 로거 × 1회 = 무시 가능
- `os.environ.get()` 호출: 세션당 1회

## 5. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `pyproject.toml` | `log_level`, `log_cli_level` = "WARNING" 추가, `SELFHEALING_TEST_LOG_LEVEL` 환경변수 추가 |
| `packages/selfhealing-python/tests/conftest.py` | `_isolate_logging_state_session`에 `root.setLevel(test_log_level)` 추가 |

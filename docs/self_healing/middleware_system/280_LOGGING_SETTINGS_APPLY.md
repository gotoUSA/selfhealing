# 280. LoggingSettings 컴포넌트별 로그 레벨 실적용

> **Status**: Implemented
> **Priority**: 2
> **References**:
> - `packages/selfhealing-python/src/selfhealing/settings/logging_config.py` — `LoggingSettings` (8개 컴포넌트 로그 레벨)
> - `packages/selfhealing-python/src/selfhealing/settings/structlog_config.py` — `_COMPONENT_LOGGER_MAP`, `_apply_component_log_levels()`
> - `packages/selfhealing-python/src/selfhealing/settings/root.py` — `SelfHealingSettings.logging` 필드

---

## 1. 문제

`LoggingSettings`에 8개 컴포넌트별 로그 레벨이 정의되어 있지만, 이 값을 실제 stdlib 로거에 `setLevel()`로 적용하는 코드가 없었다.

**근거 — logging_config.py (변경 전)**:

```python
class LoggingSettings(BaseSettings):
    dlq_log_level: str = Field(default="INFO")           # ← 정의만 있음
    circuit_breaker_log_level: str = Field(default="INFO")  # ← 적용 코드 없음
    # ... 6개 더
```

**근거 — structlog_config.py (변경 전)**:

```python
root_logger.setLevel(logging.DEBUG)  # ← 모든 레벨 통과, 컴포넌트별 제어 없음
```

결과: `SELFHEALING_LOGGING_CIRCUIT_BREAKER_LOG_LEVEL=WARNING` 환경변수를 설정해도 실제 로거에 반영되지 않는 **데드 코드** 상태.

## 2. 해결 방법

### 2.1 `_COMPONENT_LOGGER_MAP` 정의 (structlog_config.py)

`LoggingSettings` 필드명 → 실제 Python 모듈 경로(stdlib 로거 이름) 매핑:

```python
_COMPONENT_LOGGER_MAP: dict[str, list[str]] = {
    "dlq_log_level": [
        "selfhealing.services.dlq",
        "selfhealing.services.dlq_service",
        "selfhealing.services.dlq_models",
    ],
    "circuit_breaker_log_level": [
        "selfhealing.services.circuit_breaker",
        "selfhealing.services.circuit_breaker_service",
    ],
    "replay_log_level": [
        "selfhealing.services.replay_service",
        "selfhealing.services.adaptive_replay",
        "selfhealing.services.dlq.replay_operations",
    ],
    "sla_log_level": [
        "selfhealing.services.throttle.sla_notification",
    ],
    "forensic_log_level": [
        "selfhealing.services.forensic_audit_bridge",
    ],
    "emergency_log_level": [
        "selfhealing.services.emergency_mode",
        "selfhealing.services.namespace_emergency",
    ],
    "chaos_log_level": [
        "selfhealing.services.chaos",
    ],
    "l2_storage_log_level": [
        "selfhealing.adapters.memory.layered_repository",
        "selfhealing.services.precomputed_cache.l2_cache",
    ],
}
```

**매핑 근거**: 각 디렉토리의 파일에서 `structlog.get_logger()` 호출 확인.
`structlog.get_logger()`는 내부적으로 `logging.getLogger(__name__)`을 사용하므로,
모듈 경로가 곧 로거 이름이다. 예: `selfhealing/services/dlq/base.py` → 로거 이름 `selfhealing.services.dlq.base`
→ 부모 로거 `selfhealing.services.dlq`에 레벨을 설정하면 하위 모든 모듈에 전파된다.

### 2.2 `_apply_component_log_levels()` 함수

```python
def _apply_component_log_levels(settings: Any) -> None:
    for setting_name, logger_names in _COMPONENT_LOGGER_MAP.items():
        level_str = getattr(settings, setting_name, "INFO")
        level = getattr(logging, level_str.upper(), logging.INFO)
        for logger_name in logger_names:
            logging.getLogger(logger_name).setLevel(level)
```

### 2.3 `configure_structlog()`에서 호출

```python
def configure_structlog() -> None:
    settings = get_logging_settings()
    # ... (기존 코드)
    root_logger.setLevel(logging.DEBUG)
    _apply_component_log_levels(settings)  # ← 추가
```

## 3. 효과

| 환경변수 | 대상 로거 | 효과 |
|----------|-----------|------|
| `SELFHEALING_LOGGING_DLQ_LOG_LEVEL=WARNING` | `selfhealing.services.dlq.*` | DLQ INFO 로그 차단 |
| `SELFHEALING_LOGGING_CIRCUIT_BREAKER_LOG_LEVEL=WARNING` | `selfhealing.services.circuit_breaker.*` | CB 매 요청 INFO 차단 |
| `SELFHEALING_LOGGING_EMERGENCY_LOG_LEVEL=WARNING` | `selfhealing.services.emergency_mode.*` | (기본값 — 이미 WARNING) |

## 4. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `settings/structlog_config.py` | `_COMPONENT_LOGGER_MAP` 딕셔너리 추가, `_apply_component_log_levels()` 함수 추가, `configure_structlog()` 끝에서 호출 |

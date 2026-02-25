# 281. 로그 Rate Limiter (De-dup 프로세서)

> **Status**: Implemented
> **Priority**: 3
> **References**:
> - `packages/selfhealing-python/src/selfhealing/settings/log_processors.py` — `rate_limit_processor()`
> - `packages/selfhealing-python/src/selfhealing/settings/logging_config.py` — `log_rate_limit_window`, `log_rate_limit_max`
> - `packages/selfhealing-python/src/selfhealing/settings/structlog_config.py` — `shared_processors` 파이프라인

---

## 1. 문제

에러 상황에서 동일 이벤트가 수백~수천 건 반복 발생하여 로그 폭풍이 일어남.

**근거**:
- `logger.exception()` 호출 포인트: 762개
- 장애 시 매 요청마다 exception 로그 → 초당 수백~수천 건 동일 로그
- Loki/ELK 저장소 비용 증가 + 로그 검색 시 중요 이벤트 매몰

## 2. 해결 방법

structlog 프로세서 파이프라인에 Rate Limiter를 삽입하여,
동일 `(logger_name, event)` 조합이 윈도우 내 max_count를 초과하면 자동으로 drop한다.

### 2.1 프로세서 동작

```
이벤트 발생
  ↓
(logger_name, event_name) 키로 상태 조회
  ↓
┌─ 윈도우 만료 또는 첫 이벤트 ──→ 새 윈도우 시작, 통과
│  (이전 윈도우에서 suppress된 건수가 있으면 _rate_limit_suppressed_previous 주입)
│
├─ count ≤ max_count ──→ 통과
│
└─ count > max_count ──→ raise DropEvent (suppress, 카운터 증가)
```

### 2.2 핵심 설계 결정

| 결정 | 이유 |
|------|------|
| ERROR/CRITICAL은 절대 suppress하지 않음 | 장애 진단에 에러 로그가 필수. suppress하면 MTTD 증가 |
| 키 = `(logger_name, event_name)` | 같은 이벤트지만 다른 모듈에서 발생한 것은 별도 추적 |
| 윈도우 전환 시 suppress 카운트 주입 | 다음 통과 로그에 `_rate_limit_suppressed_previous=N` 필드로 기록 |
| `threading.Lock` 사용 | 멀티스레드 환경(Django WSGI worker)에서 안전 |
| `time.monotonic()` 사용 | 시스템 시계 변경에 영향받지 않음 |

### 2.3 구현 코드 (`settings/log_processors.py`)

```python
_rate_limit_state: dict[tuple[str, str], dict[str, Any]] = {}
_rate_limit_lock = threading.Lock()
_NEVER_SUPPRESS_LEVELS = frozenset({"error", "critical"})

def rate_limit_processor(logger, method_name, event_dict):
    if method_name in _NEVER_SUPPRESS_LEVELS:
        return event_dict

    window_seconds, max_count = _get_rate_limit_settings()
    if max_count <= 0 or window_seconds <= 0:
        return event_dict

    event_name = event_dict.get("event", "")
    logger_name = event_dict.get("logger", "unknown")
    key = (logger_name, event_name)
    now = time.monotonic()

    with _rate_limit_lock:
        state = _rate_limit_state.get(key)
        if state is None or (now - state["window_start"]) >= window_seconds:
            # 새 윈도우
            suppressed_count = state["suppressed"] if state else 0
            if suppressed_count > 0:
                event_dict["_rate_limit_suppressed_previous"] = suppressed_count
            _rate_limit_state[key] = {"count": 1, "window_start": now, "suppressed": 0}
            return event_dict

        state["count"] += 1
        if state["count"] <= max_count:
            return event_dict

        state["suppressed"] += 1
        raise structlog.DropEvent
```

### 2.4 파이프라인 위치

```python
shared_processors = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    rate_limit_processor,       # ← 위치: add_logger_name 직후
    sampling_processor,         #    (rate limit에 걸린 이벤트는 sampling에서 재처리 불필요)
    structlog.processors.TimeStamper(fmt="iso"),
    ...
]
```

Rate Limiter가 Sampling보다 먼저 오는 이유: rate limit에 의해 drop된 이벤트가
sampling 프로세서에서 다시 평가되는 불필요한 연산을 방지.

### 2.5 설정 (logging_config.py)

```python
class LoggingSettings(BaseSettings):
    log_rate_limit_window: int = Field(default=10, ge=0)
    log_rate_limit_max: int = Field(default=100, ge=0)
```

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_LOGGING_LOG_RATE_LIMIT_WINDOW` | 10 | 윈도우 크기(초). 0 = 비활성화 |
| `SELFHEALING_LOGGING_LOG_RATE_LIMIT_MAX` | 100 | 윈도우당 최대 동일 이벤트 수. 0 = 무제한 |

## 3. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `settings/log_processors.py` | **신규 생성**. `rate_limit_processor()`, `reset_rate_limit_state()` |
| `settings/logging_config.py` | `log_rate_limit_window`, `log_rate_limit_max` 필드 추가 |
| `settings/structlog_config.py` | `shared_processors`에 `rate_limit_processor` 삽입, import 추가 |

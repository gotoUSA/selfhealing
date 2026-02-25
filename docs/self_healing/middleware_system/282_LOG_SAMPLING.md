# 282. 로그 샘플링 프로세서

> **Status**: Implemented
> **Priority**: 4
> **References**:
> - `packages/selfhealing-python/src/selfhealing/settings/log_processors.py` — `sampling_processor()`
> - `packages/selfhealing-python/src/selfhealing/settings/logging_config.py` — `log_sampling_rate`, `log_sampling_events`
> - `packages/selfhealing-python/src/selfhealing/settings/structlog_config.py` — `shared_processors` 파이프라인
> - `packages/selfhealing-python/src/selfhealing/settings/audit_settings.py` — `sampling_rate_429` (비교 참고, 로그 샘플링 아님)

---

## 1. 문제

Happy path에서도 INFO 로그가 과다 발생.

**근거**:
- `logger.info()` 호출 포인트: 1,373개
- `logger.debug()` 호출 포인트: 1,065개
- 정상 작동 시에도 매 요청마다 `action_executor.execute`, `circuit_breaker.checked` 등 발생
- 기존 `audit_settings.py`의 `sampling_rate_429`은 감사 이벤트 전용이며, 로그에 대한 범용 샘플링은 없음

## 2. 해결 방법

structlog 프로세서로 DEBUG/INFO 로그를 확률적 샘플링한다.

### 2.1 프로세서 동작

```
이벤트 발생
  ↓
method_name ∈ {warning, error, critical}? ──→ 항상 통과
  ↓ (debug 또는 info)
sample_rate ≥ 1.0? ──→ 비활성화, 통과
  ↓
target_events 설정됨? ──→ event_name이 목록에 없으면 통과
  ↓
random() > sample_rate? ──→ raise DropEvent
  ↓
통과 (event_dict에 _sampled=True 주입)
```

### 2.2 핵심 설계 결정

| 결정 | 이유 |
|------|------|
| WARNING 이상은 항상 통과 | 에러/경고는 진단에 필수. 절대 drop하지 않음 |
| `target_events` 빈 문자열 = 모든 DEBUG/INFO에 적용 | 간편한 글로벌 볼륨 감소 |
| `target_events` 설정 시 = 해당 이벤트만 샘플링 | 핀포인트 제어. 예: `circuit_breaker.checked` |
| 통과 로그에 `_sampled=True` 표시 | 로그 분석 시 샘플링된 로그임을 인식 가능 |
| `random.random()` 사용 (S311 noqa) | 보안 목적이 아닌 성능 목적이므로 CSPRNG 불필요 |

### 2.3 구현 코드 (`settings/log_processors.py`)

```python
_SAMPLING_TARGET_LEVELS = frozenset({"debug", "info"})

def sampling_processor(logger, method_name, event_dict):
    if method_name not in _SAMPLING_TARGET_LEVELS:
        return event_dict

    sample_rate, target_events = _get_sampling_settings()
    if sample_rate >= 1.0:
        return event_dict

    event_name = event_dict.get("event", "")
    if target_events and event_name not in target_events:
        return event_dict

    if random.random() > sample_rate:
        raise structlog.DropEvent

    event_dict["_sampled"] = True
    return event_dict
```

### 2.4 설정 (logging_config.py)

```python
class LoggingSettings(BaseSettings):
    log_sampling_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    log_sampling_events: str = Field(default="")
```

| 환경변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_LOGGING_LOG_SAMPLING_RATE` | 1.0 | 샘플링 비율. 1.0 = 비활성화, 0.1 = 10%만 기록 |
| `SELFHEALING_LOGGING_LOG_SAMPLING_EVENTS` | "" (빈 문자열) | 쉼표 구분 이벤트명. 비어있으면 모든 DEBUG/INFO 대상 |

### 2.5 사용 예시

```bash
# 모든 DEBUG/INFO 로그의 10%만 기록
SELFHEALING_LOGGING_LOG_SAMPLING_RATE=0.1

# 특정 Hot path 이벤트에만 1% 샘플링 적용
SELFHEALING_LOGGING_LOG_SAMPLING_RATE=0.01
SELFHEALING_LOGGING_LOG_SAMPLING_EVENTS="circuit_breaker.checked,action_executor.execute"
```

### 2.6 기존 audit 샘플링과의 차이

| 항목 | audit `sampling_rate_429` | 이 프로세서 |
|------|---------------------------|-------------|
| 대상 | 감사 이벤트 (AuditEvent) | structlog 로그 |
| 적용 위치 | audit 서비스 계층 | structlog 프로세서 파이프라인 |
| 범위 | 429 응답 이벤트 전용 | 모든 DEBUG/INFO 또는 지정 이벤트 |

## 3. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `settings/log_processors.py` | `sampling_processor()` 함수 추가 |
| `settings/logging_config.py` | `log_sampling_rate`, `log_sampling_events` 필드 추가 |
| `settings/structlog_config.py` | `shared_processors`에 `sampling_processor` 삽입, import 추가 |

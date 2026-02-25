# 283. 동적 로그 레벨 API

> **Status**: Implemented
> **Priority**: 5
> **References**:
> - `packages/selfhealing-python/src/selfhealing/api/django/views/config.py` — `LoggingConfigView.put()` 오버라이드
> - `packages/selfhealing-python/src/selfhealing/settings/structlog_config.py` — `_apply_component_log_levels()`, `_COMPONENT_LOGGER_MAP`
> - `packages/selfhealing-python/src/selfhealing/settings/logging_config.py` — `reset_logging_settings()`
> - `packages/selfhealing-python/src/selfhealing/api/django/urls.py` L381 — `config/logging/` 엔드포인트
> - `packages/selfhealing-python/src/selfhealing/api/django/serializers/config/advanced_configs.py` — `LoggingConfigSerializer`

---

## 1. 문제

로그 레벨을 변경하려면 환경변수 수정 후 프로세스를 재시작해야 했음.

**근거**:
- `LoggingSettings`는 Pydantic `BaseSettings` + 싱글톤 캐싱 (`_settings` 모듈 변수)
- `get_logging_settings()`는 최초 1회 인스턴스 생성 후 캐시에서 반환
- 환경변수 변경 → 프로세스 재시작 없이는 반영 불가
- 기존 `LoggingConfigView`는 `BaseConfigView`를 그대로 상속하여 `RuntimeConfigManager`만 업데이트했고, 실제 stdlib 로거 레벨에는 반영하지 않았음

## 2. 해결 방법

기존 `LoggingConfigView.put()`을 오버라이드하여, 설정 업데이트 후 실제 stdlib 로거에도 레벨을 즉시 적용한다.

### 2.1 동작 흐름

```
PUT /api/self-healing/config/logging/
  ↓
BaseConfigView.put() 호출 (기존 로직)
  ├─ serializer 검증
  ├─ RuntimeConfigManager.update_with_strategy() → 설정 저장
  └─ Response 생성
  ↓
응답 성공 (200/202)?
  ↓
reset_logging_settings()        ← 싱글톤 캐시 무효화
  ↓
get_logging_settings()          ← 새 설정으로 재생성
  ↓
_apply_component_log_levels()   ← 8개 컴포넌트 로거에 setLevel() 적용
  ↓
감사 로그 기록
```

### 2.2 구현 코드 (`views/config.py`)

```python
class LoggingConfigView(BaseConfigView):
    serializer_class = LoggingConfigSerializer
    config_name = "logging"

    def put(self, request: Request) -> Response:
        import logging as _logging

        response = super().put(request)

        if response.status_code in (200, 202):
            try:
                from selfhealing.settings.structlog_config import (
                    _COMPONENT_LOGGER_MAP,
                    _apply_component_log_levels,
                )
                from selfhealing.settings.logging_config import (
                    get_logging_settings,
                    reset_logging_settings,
                )

                reset_logging_settings()
                settings = get_logging_settings()
                _apply_component_log_levels(settings)

                logger.info(
                    "config_api.logging_levels_applied_runtime",
                    applied_levels={
                        k: getattr(settings, k, "INFO")
                        for k in _COMPONENT_LOGGER_MAP
                    },
                    changed_by=str(request.user),
                )
            except Exception as exc:
                logger.warning(
                    "config_api.logging_runtime_apply_failed",
                    error=str(exc),
                )

        return response
```

### 2.3 핵심 설계 결정

| 결정 | 이유 |
|------|------|
| `super().put()` 먼저 호출 | 설정 저장이 실패하면 로거 레벨도 변경하지 않음 |
| 성공 응답(200/202)에서만 적용 | 검증 실패(400)나 에러(500) 시 적용하지 않음 |
| `reset_logging_settings()` 호출 | 싱글톤 캐시 무효화 필수 (안 하면 이전 값 유지) |
| `try/except`로 감싸기 | 로거 레벨 적용 실패가 API 응답에 영향을 주지 않도록 |
| 감사 로그에 `changed_by` 기록 | 보안: 누가 레벨을 변경했는지 추적 가능 |

### 2.4 보안 고려사항

- 기존 `IsSelfHealingAdmin` 권한 체크 유지 (PUT은 Admin만 가능)
- DEBUG 레벨로 변경 시 민감 정보가 로그에 포함될 수 있음 → 감사 로그 필수
- 변경 이력은 `RuntimeConfigManager`의 `ConfigHistory`에 자동 기록됨

### 2.5 API 사용 예시

```bash
# 현재 로깅 설정 조회
curl -X GET /api/self-healing/config/logging/

# Circuit Breaker 로그를 WARNING으로 변경 (즉시 적용)
curl -X PUT /api/self-healing/config/logging/ \
  -H "Content-Type: application/json" \
  -d '{
    "circuit_breaker_log_level": "WARNING",
    "apply_strategy": "immediate",
    "reason": "CB 로그 과다 발생으로 레벨 상향"
  }'
```

## 3. 기존 인프라 활용

| 기존 컴포넌트 | 역할 |
|---------------|------|
| `BaseConfigView.put()` | serializer 검증, RuntimeConfigManager 업데이트, 감사 로그 |
| `LoggingConfigSerializer` | 14개 필드 검증 (8 로그 레벨 + 3 포맷 + 3 출력) |
| `RuntimeConfigManager` | 설정 저장, 히스토리 기록, apply strategy 처리 |
| `_apply_component_log_levels()` | (280에서 추가) stdlib 로거에 setLevel() 적용 |
| `IsSelfHealingAdmin` | RBAC 권한 체크 |

## 4. 변경 파일

| 파일 | 변경 내용 |
|------|-----------|
| `api/django/views/config.py` | `LoggingConfigView.put()` 오버라이드 추가 |

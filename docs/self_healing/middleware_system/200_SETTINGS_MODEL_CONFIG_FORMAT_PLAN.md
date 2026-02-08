# 200. Settings model_config 포맷 통일 계획

> **상태**: ✅ 완료 (2026-02-09)
> **목적**: `settings/` 디렉토리 내 `model_config` 정의 방식을 `SettingsConfigDict`로 통일한다.

---

## 1. 현황

### 1-1. 표준 패턴 (90+ 파일)

모든 settings 파일이 사용하는 **정규 패턴**:

```python
# 예: settings/circuit_breaker.py L37-43
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_CB_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    validate_default=True,
)
```

### 1-2. 일탈 파일: `settings/kafka.py`

```python
# settings/kafka.py L50
class KafkaAuditSettings(BaseSettings):
    """Kafka 감사 로그 설정."""
    model_config = {"env_prefix": "SELFHEALING_KAFKA_AUDIT_"}
```

---

## 2. 문제점 분석

| 항목 | 표준 패턴 | `kafka.py` | 영향 |
|------|----------|-----------|------|
| `SettingsConfigDict` 사용 | ✅ | ❌ (plain dict) | IDE 자동완성, 타입 체크 누락 |
| `env_file=".env"` | ✅ | ❌ 누락 | `.env` 파일에서 설정 로드 불가 |
| `env_file_encoding="utf-8"` | ✅ | ❌ 누락 | 인코딩 문제 가능 |
| `extra="ignore"` | ✅ | ❌ 누락 | 미정의 환경변수 시 `ValidationError` 발생 |
| `validate_default=True` | ✅ | ❌ 누락 | 기본값 검증 스킵됨 |

### 2-1. 실질적 위험

`extra="ignore"` 누락이 가장 위험:

```bash
# 환경에 오타가 있을 경우
SELFHEALING_KAFKA_AUDIT_BOOSTRAP_SERVERS=kafka:9092  # 오타: BOOSTRAP

# 표준 패턴: extra="ignore" → 무시됨 (안전)
# kafka.py: extra 미지정 → pydantic v2 기본값 extra="ignore" 이지만
#           명시적이지 않으므로 버전 업그레이드 시 동작 변경 위험
```

---

## 3. 수정 계획

### 3-1. 대상 파일

| 파일 | 현재 | 변경 |
|------|------|------|
| `settings/kafka.py:50` | `model_config = {"env_prefix": "SELFHEALING_KAFKA_AUDIT_"}` | `model_config = SettingsConfigDict(...)` |

### 3-2. 변경 내용

```python
# Before (settings/kafka.py L50)
model_config = {"env_prefix": "SELFHEALING_KAFKA_AUDIT_"}

# After
model_config = SettingsConfigDict(
    env_prefix="SELFHEALING_KAFKA_AUDIT_",
    env_file=".env",
    env_file_encoding="utf-8",
    extra="ignore",
    validate_default=True,
)
```

### 3-3. 검증 항목

- [x] `KafkaAuditSettings()` 인스턴스 생성 정상 확인
- [x] `.env` 파일에서 `SELFHEALING_KAFKA_AUDIT_*` 읽기 확인
- [x] 미정의 환경변수가 `ValidationError`를 발생시키지 않는지 확인
- [x] 기존 테스트 통과 확인 (55 passed)

# 201. env_prefix 네이밍 규칙 통일 계획

> **상태**: ✅ 완료 (2026-02-09)
> **목적**: `settings/` 디렉토리의 `env_prefix`를 `SELFHEALING_<FEATURE>_` 패턴으로 통일한다.

---

## 1. 규칙 위반 (SELFHEALING_ 접두사 누락 또는 접미사 누락)

### 1-1. `settings/resource_guard.py` — `XTEST_` 접두사

```python
# settings/resource_guard.py L32-38
class ResourceGuardSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="XTEST_",           # ❌ SELFHEALING_ 아님
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
```

**문제**: `XTEST_`는 테스트 전용 접두사처럼 보이나, `ResourceGuardSettings`는 리소스 가드 설정으로 프로덕션에서도 사용됨. `SELFHEALING_` 네임스페이스 밖에 있으므로 관리 도구에서 누락될 수 있음.

### 1-2. `settings/namespace.py` — 피처 접미사 누락

```python
# settings/namespace.py L33-38
class NamespaceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SELFHEALING_",     # ❌ 피처 접미사 없음
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        validate_default=True,
    )
```

**문제**: `SELFHEALING_`만 사용하면 **다른 모든 Settings**의 환경변수와 충돌 가능. 예를 들어 `SELFHEALING_TIMEOUT`이 이 Settings에도 매칭됨.

---

## 2. 약어 불일치 (붙여쓰기 vs 언더스코어 구분)

### 2-1. 전체 env_prefix 목록 (불일치 항목 표시)

| 파일 | 현재 env_prefix | 패턴 | 기대값 (언더스코어 구분) |
|------|----------------|------|------------------------|
| `settings/error_budget.py:38` | `SELFHEALING_ERRORBUDGET_` | 붙여쓰기 | `SELFHEALING_ERROR_BUDGET_` |
| `settings/l2_storage.py:38` | `SELFHEALING_L2STORAGE_` | 붙여쓰기 | `SELFHEALING_L2_STORAGE_` |
| `settings/critical_worker.py:65` | `SELFHEALING_CRITICALWORKER_` | 붙여쓰기 | `SELFHEALING_CRITICAL_WORKER_` |
| `settings/anti_flapping.py:42` | `SELFHEALING_ANTIFLAPPING_` | 붙여쓰기 | `SELFHEALING_ANTI_FLAPPING_` |
| `settings/steady_state.py:39` | `SELFHEALING_STEADYSTATE_` | 붙여쓰기 | `SELFHEALING_STEADY_STATE_` |
| `settings/rate_limit.py:40` | `SELFHEALING_RATELIMIT_` | 붙여쓰기 | `SELFHEALING_RATE_LIMIT_` |

### 2-2. 언더스코어 구분을 준수하는 파일 (대조군)

| 파일 | env_prefix | 패턴 |
|------|-----------|------|
| `settings/error_budget_propagation.py:45` | `SELFHEALING_ERRORBUDGET_PROPAGATION_` | 붙여쓰기+구분 혼합 |
| `settings/chaos_blast_radius.py:43` | `SELFHEALING_CHAOS_BLAST_RADIUS_` | ✅ 언더스코어 구분 |
| `settings/stress_test.py:33` | `SELFHEALING_STRESS_TEST_` | ✅ 언더스코어 구분 |
| `settings/circuit_breaker.py:38` | `SELFHEALING_CB_` | 약어 (의도적) |
| `settings/circuit_breaker_advanced.py:22` | `SELFHEALING_CB_ADV_` | 약어 (의도적) |
| `settings/recovery_circuit_breaker.py:40` | `SELFHEALING_RECOVERY_CB_` | 약어 (의도적) |

`CB_`는 의도적 약어로 일관성이 있음. 문제는 `ERRORBUDGET_`, `CRITICALWORKER_` 등 **단어 경계를 무시한 붙여쓰기**.

---

## 3. 수정 계획

### 3-1. 규칙 위반 수정

| 파일 | Before | After |
|------|--------|-------|
| `settings/resource_guard.py:33` | `env_prefix="XTEST_"` | `env_prefix="SELFHEALING_RESOURCE_GUARD_"` |
| `settings/namespace.py:34` | `env_prefix="SELFHEALING_"` | `env_prefix="SELFHEALING_NAMESPACE_"` |

### 3-2. 약어 불일치 수정

| 파일 | Before | After |
|------|--------|-------|
| `settings/error_budget.py:38` | `SELFHEALING_ERRORBUDGET_` | `SELFHEALING_ERROR_BUDGET_` |
| `settings/l2_storage.py:38` | `SELFHEALING_L2STORAGE_` | `SELFHEALING_L2_STORAGE_` |
| `settings/critical_worker.py:65` | `SELFHEALING_CRITICALWORKER_` | `SELFHEALING_CRITICAL_WORKER_` |
| `settings/anti_flapping.py:42` | `SELFHEALING_ANTIFLAPPING_` | `SELFHEALING_ANTI_FLAPPING_` |
| `settings/steady_state.py:39` | `SELFHEALING_STEADYSTATE_` | `SELFHEALING_STEADY_STATE_` |
| `settings/rate_limit.py:40` | `SELFHEALING_RATELIMIT_` | `SELFHEALING_RATE_LIMIT_` |
| `settings/error_budget_propagation.py:45` | `SELFHEALING_ERRORBUDGET_PROPAGATION_` | `SELFHEALING_ERROR_BUDGET_PROPAGATION_` |

### 3-3. 파생 변경

env_prefix 변경 시 기존 배포 환경의 **환경변수 이름**이 달라지므로:

1. 변경된 prefix에 대해 **구 prefix → 신 prefix fallback** 로직을 일시적으로 추가.
2. 배포 가이드에 환경변수 이름 변경 사항 명시.
3. `.env.example` 업데이트.

### 3-4. 확정 규칙

```
SELFHEALING_{FEATURE_NAME_WITH_UNDERSCORES}_
```

- 단어 경계는 반드시 `_`으로 구분.
- 의도적 약어는 허용 (`CB_` = Circuit Breaker, `DLQ_` = Dead Letter Queue).
- `SELFHEALING_`만 단독 사용 금지.
- `SELFHEALING_` 이외의 접두사 사용 금지.

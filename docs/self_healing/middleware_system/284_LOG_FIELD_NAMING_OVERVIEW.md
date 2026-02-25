# 284. 넘버링 로그 필드 의미 기반 리네이밍 — 개요 및 명명 규칙

> **문서 번호**: 284
> **작성일**: 2026-02-25
> **상태**: 계획 수립 완료
> **대상**: `packages/selfhealing-python/src/selfhealing/` 전체
> **관련 문서**: 269_STRUCTLOG_MIGRATION.md, 271_STRUCTLOG_EVENT_CATALOG.md

---

## 1. 현황 진단

### 1.1 문제

structlog 전환(문서 269) 이후, 구조화 로깅의 `**kwargs` 방식에서 **동일 객체의 여러 속성을 로깅할 때 키 충돌을 회피하기 위해 넘버링 접미사(`_1`, `_2`, `_3` …)를 사용**하는 안티패턴이 코드 전역에 퍼져 있다.

### 1.2 정량 현황 (코드 전수 조사 기반)

```bash
# 전수 조사 명령
grep -rnE '[a-z_]+_[0-9]+=' packages/selfhealing-python/src/selfhealing/ --include="*.py"
# 결과: 297 매치 - 2 false positive = 295건
```

| 지표 | 값 |
|---|---|
| 총 넘버링 필드 인스턴스 | **295건** |
| 영향받는 파일 수 | **79개** |
| 고유 접두사 패턴 종류 | **27+종** |
| 번호 갭(gap) 존재 위치 | **35+개소** |
| 같은 접두사가 다른 의미로 사용되는 파일 | **15개** |
| 교차 접두사 넘버링 (cross-prefix) | **2개소** |

### 1.3 접두사별 분포

| 접두사 | 건수 | 비율 | 대표 예시 |
|---|---|---|---|
| `self_N` | 88 | 29.8% | `self._effective_ttl`, `self.config.target_service` |
| `result_N` | 47 | 15.9% | `result.success_count`, `result.failed` |
| `value_N` | 19 | 6.4% | 메모리 MB, `bool(url)` |
| `entry_N` | 14 | 4.7% | `entry.domain`, `entry.failure_type` |
| `report_N` | 11 | 3.7% | `report.grade`, `report.total_experiments` |
| `count_N` | 10 | 3.4% | `len(items)` 계열 |
| `request_N` | 9 | 3.1% | `request.service_name`, `request.actor` |
| `config_N` / `_config_N` | 11 | 3.7% | `config.initial_limit`, `_config.cb_enabled` |
| `record_N` | 7 | 2.4% | `record.winner_source`, `record.mismatch_type` |
| `pool_status_N` | 6 | 2.0% | `pool_status.total_capacity` |
| 기타 22종 | 73 | 24.7% | `batch_result_N`, `action_N`, `event_N` 등 |

### 1.4 심각도 근거

**문제 1: 로그만 보면 의미를 알 수 없음**

```python
# 현재 코드 (services/chaos/experiments/latency.py L68-75)
logger.info(
    "latency_injection.injecting_ms_latency_rate",
    _self=self.latency_ms,
    self_1=self.latency_jitter_ms,       # ← jitter_ms인지 알 수 없음
    self_2=self.config.target_service,   # ← target_service인지 알 수 없음
    self_3=self.config.injection_rate*100,
    self_4=self._effective_ttl,
    self_5=self._expires_at,
)
```

Loki/Grafana에서 이 로그를 조회하면:
```json
{"event": "latency_injection.injecting_ms_latency_rate", "_self": 500, "self_1": 100, "self_2": "payment-service", "self_3": 50, "self_4": 300, "self_5": "2026-02-25T12:00:00"}
```
→ `self_1=100`이 무엇인지 **코드를 보지 않으면 절대 알 수 없음**

**문제 2: 같은 필드명이 파일/호출마다 다른 의미**

```python
# services/chaos/experiments/infrastructure.py
L62:  self_1=self.check_mtls          # ← self_1 = mTLS 체크 여부
L155: self_1=self.affect_jwt          # ← self_1 = JWT 영향 여부
L249: self_1=self.config.target_service # ← self_1 = 대상 서비스
L342: self_1=self.error_rate*100      # ← self_1 = 에러율 퍼센트
```

**문제 3: 번호 갭 → 리팩토링 잔재**

```python
# core/action_executor.py L296-298
action_1=action.target,   # action_2 없이
action_3=action.params,   # action_3으로 점프
```

```python
# services/control_api_service/service.py L456-464
request=request.action,
request_1=request.service_name,
request_2=request.environment,
response=response.status,      # ← 접두사 전환
request_4=request.actor,       # ← request_3 누락
response_5=response.risk_level,# ← 교차 접두사 넘버링
request_6=request.reason,
```

---

## 2. 명명 규칙 (Naming Convention)

### 2.1 핵심 원칙

| 순서 | 규칙 | 설명 | 예시 |
|---|---|---|---|
| 1 | **속성명 직접 사용** | `self.xxx` → `xxx`, `obj.attr` → `attr` | `self._effective_ttl` → `effective_ttl` |
| 2 | **언더스코어 접두사 제거** | private 속성의 `_` 제거 | `self._failure_count` → `failure_count` |
| 3 | **단위 접미사 추가** | 계산값에 단위 명시 | `x / 1024 / 1024` → `xxx_mb`, `rate * 100` → `xxx_pct` |
| 4 | **bool은 의미 반영** | `bool(url)` → `xxx_configured` | `bool(self._admin_base_url)` → `admin_configured` |
| 5 | **len()은 카운트명** | `len(items)` → `items_count` | `len(candidates)` → `candidates_count` |
| 6 | **충돌 시만 접두사** | 같은 호출 내 이름 충돌 시 `obj_attr` | `request.action` + `response.status` → `action`, `response_status` |

### 2.2 충돌 해결 전략

같은 `logger.xxx()` 호출 내에서 이름이 겹칠 때:

| 상황 | 해결 | 예시 |
|---|---|---|
| `result=result.total` + `result.success` | 충돌 없음 → 속성명만 | `total=..., success=...` |
| 기존 `result=result.total` 유지 + 새 필드 | `result`은 유지, 나머지 속성명 | `result=total, success_count=...` |
| `level=self._state.level.name` + `level.name` | 의미 구분 접두사 | `current_level=..., requested_level=...` |
| `old_config.xxx` + `new_config.xxx` | `old_`/`new_` 접두사 | `old_sla_critical_ms=..., new_sla_critical_ms=...` |
| `config.min_items` + `config.max_items` (기존 `config=`) | 속성명만 (충돌 없음) | `new_max_items=config.max_items` |

### 2.3 기존 비넘버링 kwargs 보존

**기존에 넘버링 없이 사용되는 kwargs(`result=`, `config=`, `_self=` 등)는 변경하지 않는다.** 넘버링 접미사가 붙은 kwargs만 리네이밍 대상이다.

```python
# Before
logger.info("event",
    result=result.total,        # ← 유지
    result_1=result.success,    # ← 리네이밍 대상
    result_2=result.failed,     # ← 리네이밍 대상
)

# After
logger.info("event",
    result=result.total,        # ← 그대로
    success_count=result.success,
    failed_count=result.failed,
)
```

### 2.4 `_self` kwargs 보존

일부 로그 호출에서 `_self=self.xxx` 형태의 기존 kwarg이 존재한다. 이것은 넘버링 필드가 아니므로 **변경하지 않는다**. `self_1`, `self_2` 등 넘버링 접미사가 붙은 것만 리네이밍 대상이다.

---

## 3. 문서 구성

전체 295건을 디렉토리 단위로 분할하여 아래 문서에서 각각 구체적인 변환 테이블을 제공한다.

| 문서 | 대상 디렉토리 | 건수 | 설명 |
|---|---|---|---|
| **285** | `services/chaos/` | 66건 | Chaos experiment, reports, safety_guard 등 — `self_N` 최다 밀집 |
| **286** | `services/` (chaos 제외) | 72건 | DLQ, throttle, circuit_breaker, canary, postmortem 등 |
| **287** | `api/django/`, `adapters/`, `celery_tasks/` | 51건 | 외부 인터페이스 계층 |
| **288** | `audit/`, `core/`, `coordination/`, `tasks/` | 56건 | 내부 코어 계층 |
| **289** | `settings/`, `meta/`, `decorators/` 등 + 체크리스트 | 17건 | 기타 + 구현 우선순위 |

### 3.1 각 문서의 변환 테이블 형식

```
| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
```

### 3.2 구현 단위

- **파일 단위로 구현** (1 파일 = 1 PR 또는 1 커밋 청크)
- 테스트에서 해당 필드를 참조하는 경우 함께 수정
- Grafana/Loki 대시보드 쿼리는 별도 추적

---

## 4. false positive 제외 목록

grep 결과 297건 중 2건은 넘버링 로그 필드가 아닌 false positive:

| 파일 | 줄 | 내용 | 제외 사유 |
|---|---|---|---|
| `services/throttle/policy.py` | 300 | `phase_1=%d` | 포맷 문자열 내 텍스트 |
| `services/rate_limit_coordinator/coordinator.py` | 432 | `is_429=` | 함수 파라미터/데코레이터 |

---

## 5. 영향 범위

### 5.1 테스트 코드

넘버링 필드를 assert하는 테스트가 있을 수 있다. 각 문서 구현 시 다음 명령으로 확인:

```bash
grep -rn "self_1\|self_2\|result_1\|result_2" tests/ --include="*.py" | grep -v "__pycache__"
```

### 5.2 Grafana/Loki 대시보드

구조화 로그 필드명을 기반으로 대시보드 쿼리가 구성되어 있을 수 있다. `docker/grafana/` 내 대시보드 JSON 파일에서 필드명 참조를 확인해야 한다.

### 5.3 이벤트 카탈로그 (문서 271)

`271_STRUCTLOG_EVENT_CATALOG.md`에 이벤트별 kwargs가 문서화되어 있을 수 있으므로, 리네이밍 완료 후 카탈로그도 갱신해야 한다.

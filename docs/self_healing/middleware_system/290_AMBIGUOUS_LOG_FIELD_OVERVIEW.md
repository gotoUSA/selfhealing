# 290. 모호한 로그 필드 의미 기반 리네이밍 — 개요 및 명명 규칙

> **문서 번호**: 290
> **작성일**: 2026-02-25
> **상태**: 계획 수립 완료
> **대상**: `packages/selfhealing-python/src/selfhealing/` 전체
> **선행 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md (넘버링 접미사 제거)
> **관련 문서**: 269_STRUCTLOG_MIGRATION.md, 271_STRUCTLOG_EVENT_CATALOG.md, 285~289

---

## 1. 배경

### 1.1 문서 284~289 완료 이후 잔존 문제

문서 284~289에서 넘버링 접미사(`self_1`, `result_2` 등) 295건을 성공적으로 제거했다. 그러나 **넘버링 이전에 이미 존재하던 "첫 번째" 필드(`_self=`, `result=`, `count=`, `key=` 등)는 의도적으로 보존**했다(284 문서 §2.3 규칙).

현재 코드를 재조사한 결과, 이 보존된 필드들이 Loki/Grafana에서 여전히 **의미를 알 수 없는 상태**로 남아 있어 넘버링과 동일한 문제를 야기한다.

### 1.2 이번 문서의 범위

- **넘버링 접미사 없이** 모호한 이름만으로 사용되는 structlog kwargs **전수 리네이밍**
- 대상: `_self=`, `v=`, 단일 제네릭 단어(`count=`, `key=`, `value=` 등)

---

## 2. 현황 진단

### 2.1 전수 조사 방법

```bash
# 멀티라인 logger 호출을 파싱하여 모든 kwargs를 추출하는 Python 스크립트 사용
# 추출 기준: logger.info/warning/error/debug/critical/exception() 내부의 keyword arguments
# false positive 제거: Python 내장 파라미터(exc_info, timeout 등), Django/DRF 파라미터 제외
```

### 2.2 정량 현황

| 지표 | 값 |
|---|---|
| 전체 로그 kwargs 인스턴스 | **5,868건** |
| 모호한 kwargs 인스턴스 | **1,326건** |
| 영향받는 파일 수 | **422개** |
| 자동 변환 제안 가능 | **614건** (46.3%) |
| 수동 컨텍스트 검토 필요 | **712건** (53.7%) |

### 2.3 모호한 필드 분류

#### A. 불투명 필드 (Opaque) — 377건

필드명만으로는 **어떤 의미인지 절대 알 수 없는** 경우.

| 필드명 | 건수 | 문제 | 대표 예시 |
|---|---|---|---|
| `_self` | 300 | 55종의 서로 다른 `self.xxx` 값이 전부 `_self`로 출력 | `_self=self.experiment_id`, `_self=self._name`, `_self=self._region` |
| `v` | 52 | 한 글자 — 완전 불투명 | `v=v` (버전? 값? 변수?) |
| `_event` | 15 | structlog 내부 예약어와 혼동 가능 | `_event=event_data` |
| `pk` | 4 | 무슨 모델의 PK인지 불명 | `pk=pk` |
| `i` | 3 | 인덱스? 식별자? | `i=i` |
| `e` | 1 | 에러? 이벤트? | `e=e` |
| `n` | 1 | 이름? 숫자? | `n=n` |
| `p` | 1 | 파라미터? 포트? | `p=p` |

#### B. 제네릭 필드 (Generic) — 949건

필드명이 **너무 일반적이어서** 같은 이름이 파일/호출마다 다른 의미로 사용되는 경우.

| 필드명 | 건수 | 문제 | 대표 값 패턴 |
|---|---|---|---|
| `count` | 119 | `len(partitions)`, `len(items)`, `len(keys)` 등 뭘 세는지 불명 | 99건은 `len(...)` → 자동 변환 가능 |
| `key` | 105 | cache key, config key, redis key 등 혼용 | 파일 경로로 유추 시 41건 자동 변환 |
| `result` | 102 | success 여부, 에러, DLQ ID 등 다양 | `.dlq_id`, `.error`, `.get(...)` |
| `value` | 98 | adapter type, JSON payload, boolean 등 | `type(adapter).__name__`, `json.dumps(...)` |
| `request` | 79 | HTTP request, control API request 등 | `.path`, `.action`, `.service_name` |
| `domain` | 47 | healing domain — 대부분 `domain=domain` | 일관되게 `healing_domain`으로 변환 |
| `name` | 39 | metric name, config name, service name 등 | 컨텍스트별 구분 필요 |
| `action` | 35 | control action, audit action 등 | `.action_type`, `.action` |
| `level` | 29 | 로그 레벨, 부하 레벨, 보안 레벨 등 | 컨텍스트 의존 |
| `parameter` | 28 | auto-tuning parameter 이름 | 대부분 `parameter=parameter` |
| `entry` | 26 | DLQ entry, audit entry 등 | 컨텍스트 의존 |
| `status` | 25 | experiment status, HTTP status 등 | 컨텍스트 의존 |
| `session` | 25 | 세션 ID vs 세션 객체 | 컨텍스트 의존 |
| `actor` | 24 | 대부분 `actor=actor` | → `actor_id` |
| `state` | 21 | circuit breaker state, emergency state 등 | 컨텍스트 의존 |
| `message` | 20 | notification message vs error message | → `detail_message` |
| `component` | 19 | 어떤 컴포넌트인지 불명 | 컨텍스트 의존 |
| `config` | 17 | config 객체 전체 | 컨텍스트 의존 |
| `region` | 17 | → `target_region` | 16건 자동 변환 |
| `rollout` | 14 | canary rollout 관련 | 컨텍스트 의존 |
| `service` | 13 | → `target_service` or `service_name` | 컨텍스트 의존 |
| `target` | 10 | → `target_service` | 4건 자동 |
| `msg` | 7 | `message`와 중복 | → `detail_msg` |
| `report` | 7 | health report, chaos report 등 | 컨텍스트 의존 |
| `record` | 7 | audit record, comparison record | 컨텍스트 의존 |
| `response` | 6 | HTTP response, API response | 컨텍스트 의존 |
| `type` | 5 | experiment type, failure type 등 | 컨텍스트 의존 |
| `total` | 4 | 무엇의 총합인지 불명 | 컨텍스트 의존 |
| `info` | 4 | 무슨 정보인지 불명 | 컨텍스트 의존 |

### 2.4 `_self=` 다의성 워스트 케이스

같은 파일 안에서 `_self=`가 **완전히 다른 의미로 사용**되는 상위 사례:

| 파일 | `_self=`의 서로 다른 의미 수 | 값 목록 |
|---|---|---|
| `services/chaos/experiments/infrastructure.py` | **6** | `experiment_id`, `failure_rate*100`, `failure_type`, `latency_ms`, `skew_seconds`, `days_until_expiry` |
| `api/django/pool_circuit_breaker.py` | **5** | `_cache_interval_ms`, `_critical_stale_ms`, `_failure_count`, `_log_interval`, `_success_count` |
| `services/chaos/experiments/network.py` | **5** | `config.target_service`, `duration_seconds`, `experiment_id`, `loss_rate*100`, `partition_type` |
| `services/throttle/adaptive/__init__.py` | **5** | `_base_limit_before_emergency`, `_current_limit`, `_limit_before_429`, `config.sla_critical_ms`, `config.sla_warning_ms` |
| `services/adaptive_replay.py` | **4** | `_config.initial_items`, `_config.success_streak_required`, `_current_items`, `_success_streak` |

→ Loki에서 `_self=100`이라는 로그를 보면 **6가지 중 어느 것인지 코드를 보지 않으면 불가능**

---

## 3. 명명 규칙

### 3.1 핵심 원칙 (284 문서 §2.1 계승 + 확장)

| 순서 | 규칙 | 설명 | 예시 |
|---|---|---|---|
| 1 | **`_self=` → 속성명 직접 사용** | `_self=self.xxx` → `xxx=self.xxx` | `_self=self.experiment_id` → `experiment_id=self.experiment_id` |
| 2 | **언더스코어 접두사 제거** | private `_` 제거 | `_self=self._name` → `name=self._name` (충돌 시 `adapter_name`) |
| 3 | **단위 접미사 추가** | 계산값에 단위 명시 | `_self=self.failure_rate*100` → `failure_rate_pct=...` |
| 4 | **bool은 의미 반영** | `bool(url)` → `xxx_configured` | `_self=bool(self._admin_url)` → `admin_url_configured=...` |
| 5 | **len()은 카운트명** | `count=len(items)` → `items_count` | `count=len(partitions)` → `partitions_count` |
| 6 | **단일 글자 금지** | `v`, `e`, `n`, `i` 등을 의미 있는 이름으로 | `v=v` → 컨텍스트에 따라 `version`, `current_value` 등 |
| 7 | **`key=key` → 컨텍스트 접두사** | cache, config, redis 등 | `key=key` in redis_adapter → `redis_key=key` |
| 8 | **`domain=domain` → `healing_domain`** | 일관성 | selfhealing의 domain은 healing_domain |
| 9 | **`actor=actor` → `actor_id`** | 명확성 | actor가 ID 문자열임을 명시 |
| 10 | **`region=region` → `target_region`** | 무슨 region인지 명시 | failover, replication 맥락 |

### 3.2 충돌 해결 (284 문서 §2.2 계승)

| 상황 | 해결 | 예시 |
|---|---|---|
| 같은 logger 호출 내 이름 충돌 | 컨텍스트 접두사 추가 | `request_action=...`, `response_status=...` |
| 기존 명시적 kwargs와 충돌 | 새 필드에 접두사 | `experiment_id` 이미 있으면 `base_experiment_id` |
| `_self=self._name` + 기존 `name=...` 공존 | `_self` → `adapter_name` 등 | 파일 맥락에 따라 결정 |

### 3.3 `→수동` 표시 규칙

변환 테이블에서 `→수동`으로 표시된 항목은 다음 절차로 결정:

1. **코드의 해당 줄과 주변 맥락**을 확인
2. **변수가 무엇을 담고 있는지** 추적
3. **같은 logger 호출의 다른 kwargs와 충돌이 없는지** 확인
4. 284 문서의 명명 규칙을 적용하여 최종 이름 결정

`→수동_xxx_컨텍스트확인` 형태의 표시는 자동 분석에서 대략적인 방향만 제시한 것이므로, 구현 시 반드시 코드를 직접 확인할 것.

---

## 4. 문서 구성

전체 1,326건을 디렉토리 단위로 분할하여 아래 문서에서 각각 구체적인 변환 테이블을 제공한다.

| 문서 | 대상 디렉토리 | 총 건수 | 파일 수 | 자동 변환 | 수동 검토 | 핵심 필드 |
|---|---|---|---|---|---|---|
| **291** | `services/chaos/` | 122건 | 23개 | 94 | 28 | `_self(83)`, `value(8)`, `count(6)` |
| **292** | `services/` (chaos 제외) | 478건 | 159개 | 192 | 286 | `_self(63)`, `count(49)`, `value(47)` |
| **293** | `api/`, `adapters/` | 302건 | 87개 | 157 | 145 | `request(69)`, `_self(43)`, `key(41)` |
| **294** | `core/`, `coordination/`, `tasks/`, `celery_tasks/` | 150건 | 38개 | 67 | 83 | `_self(42)`, `result(19)`, `parameter(18)` |
| **295** | `audit/`, `settings/`, `meta/` 등 기타 | 274건 | 115개 | 104 | 170 | `_self(69)`, `v(52)`, `name(24)` |

### 4.1 변환 테이블 형식

```
| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
```

- **분류**: `불투명`(필드명으로 의미 유추 불가) / `제네릭`(너무 일반적)
- **변환 후 필드명**: 자동 제안된 이름 또는 `→수동` (구현 시 코드 맥락 기반 결정 필요)

### 4.2 구현 단위

- **파일 단위로 구현** (1 파일 = 1 커밋 청크)
- 테스트에서 해당 필드를 참조하는 경우 함께 수정
- `→수동` 항목은 구현 시 코드를 직접 읽고 최종 이름 결정

---

## 5. 구현 우선순위

### 5.1 권장 구현 순서

| 순서 | 문서 | 이유 |
|---|---|---|
| **1순위** | **291** (services/chaos/) | `_self` 83건 밀집, 자동 변환율 77%, 가장 빠르게 효과 볼 수 있음 |
| **2순위** | **294** (core/coordination/tasks/) | 건수 적고(150건), 내부 코어라 영향 범위 한정적 |
| **3순위** | **293** (api/adapters/) | 302건이지만 `request=`가 69건으로 많아 패턴 일관 처리 가능 |
| **4순위** | **295** (audit/settings/meta/) | `v=` 52건은 기계적 변환이지만 컨텍스트 확인 필요 |
| **5순위** | **292** (services/ non-chaos) | 478건으로 가장 많고, 수동 검토도 286건으로 가장 많아 마지막에 진행 |

### 5.2 `_self=` 우선 처리 전략

300건의 `_self=`는 모든 문서에 분산되어 있지만, **변환 규칙이 기계적**(속성명 그대로 사용)이므로 전 문서를 가로질러 일괄 처리하는 것도 가능하다. 단, 문서별 구현을 선호한다면 291부터 순차 진행.

---

## 6. 영향 범위

### 6.1 테스트 코드

```bash
# 모호한 필드를 assert하는 테스트 검색
grep -rn "_self=\|\"_self\"\|\bcount=\|\"count\"\|\bkey=\|\"key\"\|\"_event\"" tests/ --include="*.py" | grep -v "__pycache__"
```

### 6.2 Grafana/Loki 대시보드

`docker/grafana/` 내 대시보드 JSON에서 `_self`, `count`, `key`, `value` 등의 필드명 참조를 확인해야 한다.

### 6.3 이벤트 카탈로그 (문서 271)

리네이밍 완료 후 `271_STRUCTLOG_EVENT_CATALOG.md` 갱신 필요.

### 6.4 이전 문서(284~289)와의 관계

- 284~289: **넘버링 접미사** 제거 (`self_1` → `experiment_id`)
- 290~295: **비넘버링 모호 필드** 제거 (`_self` → `experiment_id`, `count` → `partitions_count`)
- 두 작업은 **보완 관계**이며, 290~295 완료 시 structlog kwargs의 의미 명확성이 100% 달성됨

---

## 7. false positive 제외 기준

다음 항목은 모호한 필드처럼 보이지만 **변경 대상이 아닌** 경우:

| 제외 유형 | 설명 | 예시 |
|---|---|---|
| Python 내장 파라미터 | 함수 호출의 표준 인자 | `exc_info=True`, `timeout=30` |
| Django/DRF 파라미터 | 프레임워크 API | `queryset=...`, `serializer_class=...` |
| Redis 명령 파라미터 | `.scan(cursor=0, count=100)` 등 | `count=100` in redis scan |
| 이미 명시적인 kwargs | 충분히 의미가 명확한 경우 | `service_name=...`, `experiment_id=...` |
| `error=str(e)` | 1,729건이지만 보편적 관행으로 변경 불필요 | `error=str(e)` |

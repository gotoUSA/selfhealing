# 285. 넘버링 로그 필드 리네이밍 — `services/chaos/` (66건)

> **문서 번호**: 285
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/services/chaos/`
> **관련 문서**: 284_LOG_FIELD_NAMING_OVERVIEW.md

---

## 1. 대상 범위

`services/chaos/` 디렉토리는 `self_N` 패턴의 **최대 밀집 지역**이다. Chaos experiment의 시작/종료 로그에서 `target_service`, `injection_rate`, `_effective_ttl` 등을 일관되게 `self_1`, `self_2`, `self_3`로 기록하고 있으며, 같은 `self_1`이 파일/호출마다 전혀 다른 속성을 가리킨다.

**총 66건, 14개 파일.**

---

## 2. 변환 테이블

### 2.1 services/chaos/base/experiment.py (6건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 141 | `self_1` | `self._effective_ttl` | `effective_ttl` | |
| 215 | `self_1` | `self.status` | `status` | 같은 `self_1`이 다른 의미 |
| 273 | `self_1` | `self.status` | `status` | |
| 294 | `self_1` | `self.config.grace_period_seconds` | `grace_period_seconds` | 같은 `self_1`이 또 다른 의미 |
| 469 | `self_1` | `self._effective_ttl` | `effective_ttl` | |
| 470 | `self_2` | `self._expires_at` | `expires_at` | |

### 2.2 services/chaos/base/ttl_helper.py (1건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 63 | `self_1` | `self._start_time` | `start_time` | |

### 2.3 services/chaos/experiments/audit.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 66 | `self_1` | `self.failure_rate*100` | `failure_rate_pct` | 단위 접미사 추가 |
| 234 | `self_1` | `self.duration_seconds` | `duration_seconds` | 같은 `self_1`이 다른 의미 |
| 235 | `self_2` | `self.payload_size_bytes` | `payload_size_bytes` | |

### 2.4 services/chaos/experiments/cascade.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 57 | `self_1` | `self.config.target_service` | `target_service` | |
| 58 | `self_2` | `self._effective_ttl` | `effective_ttl` | |
| 338 | `count_1` | `len(self.affected_services)` | `affected_services_count` | |

### 2.5 services/chaos/experiments/circuit_breaker.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 69 | `self_1` | `self._effective_ttl` | `effective_ttl` | |
| 232 | `status_1` | `status.get('traffic_percent')` | `traffic_percent` | |

### 2.6 services/chaos/experiments/http_errors.py (6건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 67 | `self_1` | `self.config.target_service` | `target_service` | |
| 68 | `self_2` | `self.config.injection_rate*100` | `injection_rate_pct` | 단위 접미사 |
| 69 | `self_3` | `self._effective_ttl` | `effective_ttl` | |
| 179 | `self_1` | `self.config.target_service` | `target_service` | L67과 동일 패턴 |
| 180 | `self_2` | `self.config.injection_rate*100` | `injection_rate_pct` | |
| 181 | `self_3` | `self._effective_ttl` | `effective_ttl` | |

### 2.7 services/chaos/experiments/infrastructure.py (8건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 62 | `self_1` | `self.check_mtls` | `check_mtls` | |
| 155 | `self_1` | `self.affect_jwt` | `affect_jwt` | 같은 `self_1`, 다른 의미 |
| 156 | `self_2` | `self.affect_cache` | `affect_cache` | |
| 249 | `self_1` | `self.config.target_service` | `target_service` | 같은 `self_1`, 또 다른 의미 |
| 342 | `self_1` | `self.error_rate*100` | `error_rate_pct` | |
| 343 | `self_2` | `self.config.target_service` | `target_service` | |
| 436 | `self_1` | `self.failure_rate*100` | `failure_rate_pct` | |
| 437 | `self_2` | `self.config.target_service` | `target_service` | |

### 2.8 services/chaos/experiments/latency.py (5건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 71 | `self_1` | `self.latency_jitter_ms` | `latency_jitter_ms` | |
| 72 | `self_2` | `self.config.target_service` | `target_service` | |
| 73 | `self_3` | `self.config.injection_rate*100` | `injection_rate_pct` | |
| 74 | `self_4` | `self._effective_ttl` | `effective_ttl` | |
| 75 | `self_5` | `self._expires_at` | `expires_at` | 최대 넘버링 `self_5` |

### 2.9 services/chaos/experiments/network.py (9건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 50 | `self_1` | `self.config.target_service` | `target_service` | |
| 51 | `self_2` | `self._effective_ttl` | `effective_ttl` | |
| 139 | `self_1` | `self.reset_probability*100` | `reset_probability_pct` | |
| 140 | `self_2` | `self._effective_ttl` | `effective_ttl` | |
| 246 | `self_2` | `self._effective_ttl` | `effective_ttl` | gap: `self_1` 없음 |
| 362 | `self_1` | `self.db_available` | `db_available` | |
| 363 | `self_2` | `self.cache_available` | `cache_available` | |
| 364 | `self_3` | `self._effective_ttl` | `effective_ttl` | |
| 401 | `partition_state_1` | `partition_state.is_full_partition` | `is_full_partition` | |

### 2.10 services/chaos/experiments/rate_limit.py (2건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 49 | `self_1` | `self.config.target_service` | `target_service` | |
| 50 | `self_2` | `self._effective_ttl` | `effective_ttl` | |

### 2.11 services/chaos/experiments/resource.py (6건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 86 | `value_1` | `max_bytes / 1024 / 1024` | `max_mb` | 단위 접미사 |
| 113 | `self_1` | `self.exhaustion_percent*100` | `exhaustion_pct` | |
| 114 | `self_2` | `self.config.target_service` | `target_service` | |
| 115 | `self_3` | `self._effective_ttl` | `effective_ttl` | |
| 236 | `self_1` | `self.target_pool` | `target_pool` | |
| 237 | `self_2` | `self._effective_ttl` | `effective_ttl` | |

### 2.12 services/chaos/experiments/timeout.py (3건)

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 42 | `self_1` | `self.config.target_service` | `target_service` | |
| 43 | `self_2` | `self.config.injection_rate*100` | `injection_rate_pct` | |
| 44 | `self_3` | `self._effective_ttl` | `effective_ttl` | |

### 2.13 services/chaos/reports.py (6건)

기존 kwargs 확인 결과:
- L381-387: `report_id=report_id`, `grade=grade`, `stats=stats['passed']` 존재
- L744-749: `report=report.grade` 존재
- L752-760: `report=report.report_id` 존재

| 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|
| 386 | `stats_3` | `stats['total']` | `total` | gap: `stats_1/2` 없음 |
| 747 | `report_1` | `report.total_experiments` | `total_experiments` | |
| 755 | `report_1` | `report.report_date` | `report_date` | 같은 `report_1` 다른 의미 |
| 756 | `report_2` | `report.grade` | `grade` | 기존 `grade=` 없음 확인 |
| 757 | `report_3` | `report.total_experiments` | `total_experiments` | |
| 758 | `report_4` | `report.passed_experiments` | `passed_experiments` | |

### 2.14 services/chaos/ 기타 (6건)

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 비고 |
|---|---|---|---|---|---|
| blast_radius.py | 903 | `request_1` | `request.status` | `status` | |
| blast_radius.py | 904 | `request_2` | `request.approved_by` | `approved_by` | |
| safety_guard/resource_guard.py | 261 | `status_1` | `status.memory_percent` | `memory_percent` | |
| scheduler/service.py | 565 | `gate_result_1` | `gate_result.threshold_percent` | `threshold_percent` | |
| synthetic_load.py | 395 | `self_1` | `self.experiment_id` | `experiment_id` | |
| synthetic_load.py | 426 | `self_1` | `self._stats.to_dict()` | `stats` | |

---

## 3. 변환 예시 (Before/After)

### 3.1 latency.py — 최다 넘버링 (5개)

```python
# Before (L68-75)
logger.info(
    "latency_injection.injecting_ms_latency_rate",
    _self=self.latency_ms,
    self_1=self.latency_jitter_ms,
    self_2=self.config.target_service,
    self_3=self.config.injection_rate*100,
    self_4=self._effective_ttl,
    self_5=self._expires_at,
)

# After
logger.info(
    "latency_injection.injecting_ms_latency_rate",
    _self=self.latency_ms,
    latency_jitter_ms=self.latency_jitter_ms,
    target_service=self.config.target_service,
    injection_rate_pct=self.config.injection_rate*100,
    effective_ttl=self._effective_ttl,
    expires_at=self._expires_at,
)
```

### 3.2 infrastructure.py — 같은 `self_1`이 5가지 다른 의미

```python
# Before (L62)
logger.info("mtls_outage.injecting", self_1=self.check_mtls)

# After
logger.info("mtls_outage.injecting", check_mtls=self.check_mtls)
```

```python
# Before (L436-437)
logger.info("...", self_1=self.failure_rate*100, self_2=self.config.target_service)

# After
logger.info("...", failure_rate_pct=self.failure_rate*100, target_service=self.config.target_service)
```

### 3.3 reports.py — 교차 호출 의미 충돌

```python
# Before (L752-758)
logger.info("resilience_report_audit.event",
    report=report.report_id,
    report_1=report.report_date,
    report_2=report.grade,
    report_3=report.total_experiments,
    report_4=report.passed_experiments,
)

# After
logger.info("resilience_report_audit.event",
    report=report.report_id,
    report_date=report.report_date,
    grade=report.grade,
    total_experiments=report.total_experiments,
    passed_experiments=report.passed_experiments,
)
```

---

## 4. 반복 패턴: chaos experiment 공통 구조

대부분의 chaos experiment는 `inject_chaos()` 메서드 시작부에 동일한 패턴으로 로깅한다:

```python
logger.info("xxx.injecting",
    self_1=self.config.target_service,   # → target_service
    self_2=self.config.injection_rate*100, # → injection_rate_pct (해당 시)
    self_3=self._effective_ttl,           # → effective_ttl
)
```

이 패턴은 7개 experiment 파일에서 반복되므로 일괄 적용이 가능하다.

---

## 5. 테스트 영향

```bash
# chaos 관련 테스트에서 넘버링 필드 참조 확인
grep -rn "self_1\|self_2\|self_3\|self_4\|self_5\|report_1\|stats_3\|count_1\|status_1\|value_1" \
    tests/ --include="*.py" | grep -i chaos
```

로그 필드를 assert하는 테스트가 있으면 함께 수정해야 한다.

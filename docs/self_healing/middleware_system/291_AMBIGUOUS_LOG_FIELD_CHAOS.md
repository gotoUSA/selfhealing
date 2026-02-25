# 291. 모호한 로그 필드 리네이밍 — services/chaos/

> **문서 번호**: 291
> **작성일**: 2026-02-25
> **상태**: 구현 대기
> **대상**: `packages/selfhealing-python/src/selfhealing/services/chaos/`
> **선행 문서**: 290_AMBIGUOUS_LOG_FIELD_OVERVIEW.md (명명 규칙 및 전체 개요)
> **구현 우선순위**: ★★★★★ (1순위 — 자동 변환율 77%, `_self` 밀집도 최고)

---

## 1. 범위 요약

| 지표 | 값 |
|---|---|
| 대상 파일 수 | **23개** |
| 모호한 kwargs 인스턴스 | **122건** |
| 불투명 필드(`_self`, `_event` 등) | ~85건 |
| 제네릭 필드(`count`, `key`, `value` 등) | ~37건 |
| 자동 변환 제안 가능 | ~94건 (77%) |
| 수동 컨텍스트 검토 필요(`→수동`) | ~28건 (23%) |

### 1.1 이 문서가 1순위인 이유

1. **자동 변환율 77%** — 5개 문서 중 가장 높아 빠르게 효과를 볼 수 있음
2. **`_self=self.experiment_id` 패턴 반복** — 대부분의 카오스 실험 클래스에서 `_self` → `experiment_id`로 일괄 변환 가능
3. **독립적인 디렉토리** — `services/chaos/`는 다른 모듈과 의존도가 낮아 리팩토링 영향을 격리 가능

---

## 2. 명명 규칙 (290 문서 §3 참조)

| 규칙 | 예시 |
|---|---|
| `_self=self.xxx` → `xxx` | `_self=self.experiment_id` → `experiment_id=self.experiment_id` |
| `count=len(items)` → `items_count` | `count=len(results)` → `results_count=len(results)` |
| `value=type(x).__name__` → `adapter_type` | 타입 이름 반환 시 통일 |
| `domain=domain` → `healing_domain` | selfhealing 도메인 구분자 |
| `key/value` 제네릭 → 컨텍스트 접두사 | `key=key` → `config_key=key` |
| `→수동` 표기 항목 | 코드 컨텍스트를 직접 확인하여 결정 |

---

## 3. 전수 변환 테이블

> **범례**
> - `↑` = 바로 위 행과 같은 파일
> - `→수동` = 자동 변환 불가, 코드 컨텍스트를 확인하여 수동 결정 필요
> - 모든 파일 경로는 `packages/selfhealing-python/src/selfhealing/` 기준 상대 경로

| 파일 | 줄 | 현재 필드명 | 현재 값 | 변환 후 필드명 | 분류 |
|---|---|---|---|---|---|
| `services/chaos/actionable_alert_urls.py` | 86 | `value` | `bool(self._admin_base_url)` | `→수동` | 제네릭 |
| `services/chaos/base/experiment.py` | 140 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 214 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 215 | `status` | `self.status` | `→수동` | 제네릭 |
| `↑` | 232 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 259 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 272 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 273 | `status` | `self.status` | `→수동` | 제네릭 |
| `↑` | 293 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 442 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 468 | `_self` | `self.config.target_service` | `target_service` | 불투명 |
| `↑` | 500 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 614 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 643 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 961 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 1032 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 1046 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 1091 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 1110 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 1135 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/base/ttl_helper.py` | 62 | `_self` | `self.ttl_seconds` | `ttl_seconds` | 불투명 |
| `↑` | 113 | `_self` | `self._start_time` | `start_time` | 불투명 |
| `services/chaos/blast_radius.py` | 241 | `domain` | `domain` | `healing_domain` | 제네릭 |
| `↑` | 426 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 427 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 902 | `request` | `request.experiment_id` | `→수동` | 제네릭 |
| `↑` | 903 | `status` | `request.status` | `→수동` | 제네릭 |
| `services/chaos/blast_radius_analyzer.py` | 284 | `level` | `level.value` | `→수동` | 제네릭 |
| `↑` | 285 | `count` | `len(affected_services)` | `affected_services_count` | 제네릭 |
| `services/chaos/experiments/audit.py` | 65 | `_self` | `self.failure_type` | `failure_type` | 불투명 |
| `↑` | 101 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 231 | `_self` | `self.flood_rate` | `flood_rate` | 불투명 |
| `↑` | 269 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/cascade.py` | 56 | `_self` | `self.failure_rate * 100` | `failure_rate_pct` | 불투명 |
| `↑` | 90 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 96 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 223 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 274 | `count` | `len(self.affected_services)` | `affected_services_count` | 제네릭 |
| `↑` | 275 | `_self` | `self._effective_ttl` | `effective_ttl` | 불투명 |
| `↑` | 306 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 311 | `service` | `service` | `→수동_service_컨텍스트확인` | 제네릭 |
| `↑` | 312 | `result` | `result.message` | `→수동` | 제네릭 |
| `↑` | 333 | `count` | `len(opened_services)` | `opened_services_count` | 제네릭 |
| `↑` | 350 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 356 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 386 | `count` | `len(self.affected_services)` | `affected_services_count` | 제네릭 |
| `services/chaos/experiments/circuit_breaker.py` | 68 | `_self` | `self.config.target_service` | `target_service` | 불투명 |
| `↑` | 88 | `result` | `result.message` | `→수동` | 제네릭 |
| `↑` | 119 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 125 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 231 | `status` | `status.get("canary_state")` | `→수동` | 제네릭 |
| `services/chaos/experiments/http_errors.py` | 64 | `_self` | `self.error_code` | `error_code` | 불투명 |
| `↑` | 100 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 106 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 174 | `_self` | `self.error_code` | `error_code` | 불투명 |
| `↑` | 210 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 216 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/infrastructure.py` | 61 | `_self` | `self.days_until_expiry` | `days_until_expiry` | 불투명 |
| `↑` | 96 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 152 | `_self` | `self.skew_seconds` | `skew_seconds` | 불투명 |
| `↑` | 189 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 244 | `_self` | `self.failure_rate * 100` | `failure_rate_pct` | 불투명 |
| `↑` | 280 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 335 | `_self` | `self.latency_ms` | `latency_ms` | 불투명 |
| `↑` | 372 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 427 | `_self` | `self.failure_type` | `failure_type` | 불투명 |
| `↑` | 464 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/latency.py` | 70 | `_self` | `self.latency_ms` | `latency_ms` | 불투명 |
| `↑` | 112 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 118 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/network.py` | 49 | `_self` | `self.loss_rate * 100` | `loss_rate_pct` | 불투명 |
| `↑` | 82 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 88 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 136 | `_self` | `self.config.target_service` | `target_service` | 불투명 |
| `↑` | 170 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 176 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 240 | `count` | `len(self.affected_endpoints)` | `affected_endpoints_count` | 제네릭 |
| `↑` | 241 | `_self` | `self.duration_seconds` | `duration_seconds` | 불투명 |
| `↑` | 274 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 355 | `_self` | `self.partition_type` | `partition_type` | 불투명 |
| `↑` | 410 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 416 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/rate_limit.py` | 48 | `_self` | `self.rate_limit_count` | `rate_limit_count` | 불투명 |
| `↑` | 93 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 99 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/resource.py` | 85 | `value` | `actual_bytes / 1024 / 1024` | `→수동` | 제네릭 |
| `↑` | 87 | `_self` | `self.SAFETY_MARGIN_PERCENT * 100` | `SAFETY_MARGIN_PERCENT_pct` | 불투명 |
| `↑` | 112 | `_self` | `self.resource_type` | `resource_type` | 불투명 |
| `↑` | 116 | `value` | `f"` | `→수동` | 제네릭 |
| `↑` | 147 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 153 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 233 | `_self` | `self.simulated_status` | `simulated_status` | 불투명 |
| `↑` | 288 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 294 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/experiments/timeout.py` | 41 | `_self` | `self.timeout_delay_seconds` | `timeout_delay_seconds` | 불투명 |
| `↑` | 76 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 82 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/isolation_helpers.py` | 302 | `count` | `len(results)` | `results_count` | 제네릭 |
| `services/chaos/reports.py` | 382 | `total` | `stats["total"]` | `→수동` | 제네릭 |
| `↑` | 711 | `report` | `report.grade` | `→수동` | 제네릭 |
| `↑` | 719 | `report` | `report.report_id` | `→수동` | 제네릭 |
| `services/chaos/safety_guard/guard.py` | 93 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 94 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 273 | `state` | `state.reason` | `→수동` | 제네릭 |
| `↑` | 379 | `result` | `result.warnings` | `→수동` | 제네릭 |
| `services/chaos/safety_guard/resource_guard.py` | 242 | `result` | `result.block_reason` | `→수동` | 제네릭 |
| `↑` | 254 | `result` | `result.block_reason` | `→수동` | 제네릭 |
| `↑` | 260 | `status` | `status.cpu_percent` | `→수동` | 제네릭 |
| `services/chaos/scheduler/service.py` | 101 | `key` | `key` | `config_key` | 제네릭 |
| `↑` | 102 | `value` | `value` | `config_value` | 제네릭 |
| `↑` | 768 | `_self` | `self._config.dry_run_reason` | `dry_run_reason` | 불투명 |
| `services/chaos/stop_conditions.py` | 244 | `key` | `key` | `→수동_key_컨텍스트확인` | 제네릭 |
| `↑` | 245 | `value` | `value` | `→수동_value_컨텍스트확인` | 제네릭 |
| `↑` | 360 | `value` | `[v.message for v in violations]` | `→수동` | 제네릭 |
| `services/chaos/synthetic_load.py` | 251 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 373 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 394 | `_self` | `self.target_service` | `target_service` | 불투명 |
| `↑` | 397 | `config` | `config.target_rps` | `→수동` | 제네릭 |
| `↑` | 425 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `services/chaos/traffic_shaper.py` | 300 | `_self` | `self.experiment_id` | `experiment_id` | 불투명 |
| `↑` | 302 | `config` | `config.target_rps` | `→수동` | 제네릭 |
| `↑` | 499 | `_self` | `self._current_rate_multiplier` | `current_rate_multiplier` | 불투명 |

---

## 4. `→수동` 항목 처리 가이드

`→수동` 표기 항목(28건)은 코드 컨텍스트를 직접 확인하여 다음 기준으로 필드명을 결정한다:

### 4.1 `status` / `state` 계열
- `status=self.status` — 실험 상태라면 `experiment_status`
- `status=request.status` — 요청 상태라면 `request_status`
- `state=state.reason` — 이유를 담고 있으므로 `state_reason`

### 4.2 `result` 계열
- `result=result.message` — 결과 메시지라면 `result_message`
- `result=result.warnings` — 경고 목록이면 `result_warnings`
- `result=result.block_reason` — 차단 사유면 `block_reason`

### 4.3 `config` 계열
- `config=config.target_rps` — 설정값을 기록하므로 `target_rps`

### 4.4 `value` 계열
- `value=bool(...)` — 불리언 플래그면 `admin_url_configured`
- `value=actual_bytes / 1024 / 1024` — 자원 사용량이면 `memory_usage_mb`
- `value=[v.message for v in violations]` — 위반 목록이면 `violation_messages`

---

## 5. 구현 절차

### 5.1 단계

1. 자동 변환(94건): 스크립트 또는 일괄 sed로 `_self=self.xxx` → `xxx=self.xxx` 변환
2. 수동 검토(28건): `→수동` 항목을 코드에서 직접 확인하여 필드명 결정
3. 테스트 실행: `pytest tests/ -k chaos` — 기존 테스트 통과 확인
4. Loki 쿼리 검증: 기존 대시보드/알림 규칙에서 이전 필드명 사용 여부 확인

### 5.2 주의사항

- **이벤트 메시지(첫 번째 positional 인자)는 절대 변경하지 않는다**
- structlog의 예약어(`exc_info`, `stack_info`)는 변경 대상이 아님
- 같은 logger 호출에서 새 필드명이 기존 다른 필드와 충돌하지 않는지 확인

---

## 6. 완료 기준

- [ ] 122건 전체 변환 완료 (자동 94건 + 수동 28건)
- [ ] `pytest tests/ -k chaos` 통과
- [ ] 변환 전후 필드명 매핑 기록 (이 문서의 테이블)
- [ ] Loki/Grafana 대시보드 쿼리 업데이트 확인

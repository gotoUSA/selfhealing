# 148. Postmortem 타임라인 스냅샷 보존

**문서 버전:** 1.0
**작성일:** 2026-01-28
**선행 문서:** [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md)
**상태:** 설계 완료

---

## 1. 목적

Postmortem 생성 시 타임라인에 포함된 핵심 메트릭과 로그의 **정적 스냅샷**을 저장하여, 외부 시스템(Prometheus, ELK 등)의 데이터 만료 후에도 Postmortem이 완전한 분석 자료로 남도록 함

---

## 2. 현재 상태 분석

### 2.1 현재 저장 내용

`_generate_postmortem_data()` 반환 구조:

| 필드 | 내용 | 한계 |
|------|------|------|
| `timeline` | 이벤트 타입, timestamp만 | 상세 메트릭 없음 |
| `system_snapshot` | 생성 시점의 CPU, 메모리 | 장애 중 추이 없음 |

### 2.2 system_snapshot 현재 내용

`collect_system_snapshot()` 반환값:

| 필드 | 소스 |
|------|------|
| `timestamp` | 현재 시각 |
| `cpu_percent` | `psutil.cpu_percent()` |
| `memory_percent` | `psutil.virtual_memory().percent` |
| `db_active_connections` | PostgreSQL 연결 수 |

**문제:** 장애 **발생 시점**이 아닌 **Postmortem 생성 시점** 데이터

---

## 3. 업계 표준 분석

### 3.1 Google SRE (Outalator)

**참조:** Google SRE Book - Tracking Outages

| 항목 | Google 방식 |
|------|------------|
| 알림 원본 저장 | "Outalator stores a copy of the original notification" |
| 메트릭 스냅샷 | 별도 메트릭 시스템에서 관리 (Prometheus 등) |
| 타임라인 | 주석(annotation) 기반 이벤트 기록 |

**핵심:** 알림/이벤트 원본은 저장, 메트릭은 **링크**로 참조

### 3.2 Datadog Incident Management

| 항목 | Datadog 방식 |
|------|-------------|
| 메트릭 스냅샷 | 인시던트 기간 동안의 메트릭 자동 캡처 |
| 보존 기간 | 인시던트 메트릭은 15개월 보존 |
| 대시보드 링크 | 타임 범위 고정된 대시보드 URL 생성 |

### 3.3 PagerDuty Postmortem

| 항목 | PagerDuty 방식 |
|------|---------------|
| 타임라인 | 이벤트 기반 자동 생성 |
| 메트릭 | 연동된 모니터링 도구에서 스냅샷 임베드 |
| 로그 | 핵심 로그 스니펫 첨부 |

### 3.4 업계 권장 사항 종합

| 데이터 유형 | 권장 방식 |
|------------|----------|
| 이벤트/알림 | 원본 텍스트 저장 (필수) |
| 메트릭 | 스냅샷 + 대시보드 링크 (권장) |
| 로그 | 핵심 에러 로그 스니펫 저장 (권장) |
| 전체 시계열 | 외부 시스템에 위임 (선택) |

---

## 4. 수집 시점 분석

### 4.1 수집 시점 옵션

| 시점 | 장점 | 단점 |
|------|------|------|
| **CB OPEN 시점** | 장애 시작 상태 캡처 | 복구 상태 미포함 |
| **CB CLOSED 시점** | 복구 상태 캡처 | 장애 피크 미포함 |
| **인시던트 기간 전체** | 완전한 추이 | 데이터 량 큼 |
| **주기적 샘플링** | 균형 잡힌 데이터 | 구현 복잡 |

### 4.2 권장 방식: 양 끝점 + 피크 캡처

| 캡처 시점 | 용도 |
|----------|------|
| CB OPEN 시점 | 장애 시작 상태 |
| CB CLOSED 시점 | 복구 완료 상태 |
| 장애 중 피크 | Prometheus 쿼리로 보완 |

---

## 5. 스냅샷 데이터 설계

### 5.1 확장된 스냅샷 구조

#### timeline_snapshot

| 필드 | 타입 | 설명 |
|------|------|------|
| `events` | `list` | 기존 타임라인 이벤트 |
| `metrics_at_open` | `dict` | CB OPEN 시점 메트릭 |
| `metrics_at_close` | `dict` | CB CLOSED 시점 메트릭 |
| `peak_metrics` | `dict` | 장애 중 피크 값 (Prometheus 쿼리) |
| `captured_logs` | `list` | 핵심 에러 로그 |
| `dashboard_links` | `dict` | 시간 범위 고정 대시보드 URL |

### 5.2 metrics_at_open / metrics_at_close 구조

| 필드 | 타입 | 소스 |
|------|------|------|
| `timestamp` | `str` | 캡처 시각 |
| `cpu_percent` | `float` | psutil |
| `memory_percent` | `float` | psutil |
| `db_connections` | `int` | PostgreSQL |
| `error_rate` | `float` | 애플리케이션 카운터 |
| `request_rate` | `float` | 애플리케이션 카운터 |
| `cb_states` | `dict` | 서비스별 CB 상태 |

### 5.3 peak_metrics 구조 (Prometheus 쿼리)

| 필드 | PromQL | 설명 |
|------|--------|------|
| `max_cpu` | `max_over_time(process_cpu_seconds_total[{duration}])` | CPU 최대값 |
| `max_memory` | `max_over_time(process_resident_memory_bytes[{duration}])` | 메모리 최대값 |
| `max_error_rate` | `max_over_time(selfhealing_error_rate_percent[{duration}])` | 에러율 최대값 |
| `max_latency_p99` | `max_over_time(histogram_quantile(0.99, rate(selfhealing_http_request_duration_seconds_bucket[1m]))[{duration}])` | P99 지연 최대값 |

### 5.4 captured_logs 구조

| 필드 | 타입 | 설명 |
|------|------|------|
| `timestamp` | `str` | 로그 시각 |
| `level` | `str` | `ERROR`, `CRITICAL` |
| `message` | `str` | 로그 메시지 (최대 500자) |
| `service` | `str` | 서비스명 |
| `trace_id` | `str` | 연관 trace ID |

### 5.5 dashboard_links 구조

| 필드 | 형식 | 설명 |
|------|------|------|
| `grafana_overview` | URL | 전체 대시보드 (시간 범위 고정) |
| `grafana_service` | URL | 서비스별 대시보드 |
| `prometheus_query` | URL | Prometheus 쿼리 UI |

---

## 6. CB OPEN 시점 스냅샷 저장

### 6.1 현재 CB OPEN 핸들러

`_on_circuit_breaker_opened_notify()` 함수:
- 알림 발송만 수행
- 스냅샷 저장 없음

### 6.2 새 핸들러 추가

**파일:** `services/event_bus.py`

**함수명:** `_on_circuit_breaker_opened_snapshot()`

**동작:**
1. CB OPEN 이벤트 수신
2. 시스템 스냅샷 수집
3. Redis에 임시 저장 (TTL 30분)

### 6.3 Redis Key 설계

| 키 패턴 | 타입 | TTL | 용도 |
|--------|------|-----|------|
| `selfhealing:cb_snapshot:open:{service}` | `HASH` | 30분 | OPEN 시점 스냅샷 |

---

## 7. Prometheus 쿼리 통합

### 7.1 Prometheus HTTP API

**엔드포인트:** `GET /api/v1/query_range`

**파라미터:**

| 파라미터 | 값 |
|---------|-----|
| `query` | PromQL 쿼리 |
| `start` | 인시던트 시작 시각 (Unix timestamp) |
| `end` | 인시던트 종료 시각 (Unix timestamp) |
| `step` | 샘플링 간격 (예: `60s`) |

### 7.2 쿼리 헬퍼 함수

**파일:** `services/postmortem/prometheus_collector.py`

**클래스:** `PrometheusMetricsCollector`

| 메서드 | 설명 |
|--------|------|
| `query_range()` | 시간 범위 쿼리 |
| `query_instant()` | 특정 시점 쿼리 |
| `get_peak_metrics()` | 인시던트 기간 피크 값 조회 |
| `generate_dashboard_link()` | 시간 범위 고정 대시보드 URL 생성 |

### 7.3 Settings

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `prometheus_url` | `str` | `http://localhost:9090` | Prometheus 주소 |
| `prometheus_enabled` | `bool` | `True` | Prometheus 쿼리 활성화 |
| `grafana_base_url` | `str` | `http://localhost:3000` | Grafana 주소 |
| `grafana_dashboard_uid` | `str` | `selfhealing` | 대시보드 UID |

---

## 8. 로그 수집

### 8.1 수집 범위

| 항목 | 값 |
|------|-----|
| 로그 레벨 | `ERROR`, `CRITICAL` |
| 시간 범위 | 인시던트 시작 ~ 종료 |
| 최대 개수 | 50개 |
| 최대 길이 | 메시지당 500자 |

### 8.2 수집 소스 옵션

| 소스 | 장점 | 단점 |
|------|------|------|
| 인메모리 로그 버퍼 | 외부 의존 없음 | 버퍼 크기 제한 |
| ELK (Elasticsearch) | 완전한 로그 | 추가 연동 필요 |
| Loki | Prometheus 생태계 | 추가 연동 필요 |

### 8.3 인메모리 로그 버퍼 (단기 구현)

**파일:** `services/postmortem/log_buffer.py`

**클래스:** `IncidentLogBuffer`

| 메서드 | 설명 |
|--------|------|
| `add_log()` | 에러 로그 추가 |
| `get_logs_for_period()` | 기간 내 로그 조회 |
| `clear_old_logs()` | 오래된 로그 정리 |

**버퍼 사이즈:** 최근 1000개 에러 로그 (TTL 1시간)

---

## 9. 대시보드 링크 생성

### 9.1 Grafana 시간 범위 URL

| 파라미터 | 설명 |
|---------|------|
| `from` | 시작 시각 (Unix ms) |
| `to` | 종료 시각 (Unix ms) |
| `orgId` | 조직 ID |
| `var-service` | 서비스 필터 |

**예시 URL:**
```
https://grafana.example.com/d/selfhealing/overview?orgId=1&from=1706000000000&to=1706003600000&var-service=database
```

### 9.2 Prometheus UI 링크

| 파라미터 | 설명 |
|---------|------|
| `g0.expr` | PromQL 쿼리 |
| `g0.range_input` | 시간 범위 |
| `g0.end_input` | 종료 시각 |

---

## 10. 구현 위치

### 10.1 새 모듈

| 파일 | 클래스/함수 |
|------|------------|
| `services/postmortem/prometheus_collector.py` | `PrometheusMetricsCollector` |
| `services/postmortem/log_buffer.py` | `IncidentLogBuffer` |
| `services/postmortem/snapshot_builder.py` | `SnapshotBuilder` |

### 10.2 기존 수정

| 파일 | 수정 내용 |
|------|----------|
| `event_bus.py` | CB OPEN 스냅샷 핸들러 추가 |
| `observability.py` | `_generate_postmortem_data()` 확장 |
| `base.py` | `collect_system_snapshot()` 확장 |

---

## 11. 스냅샷 빌더 설계

### 11.1 SnapshotBuilder 클래스

**파일:** `services/postmortem/snapshot_builder.py`

| 메서드 | 설명 |
|--------|------|
| `build()` | 전체 스냅샷 빌드 |
| `_get_open_snapshot()` | Redis에서 OPEN 스냅샷 조회 |
| `_collect_close_snapshot()` | CLOSED 시점 스냅샷 수집 |
| `_query_prometheus_peaks()` | Prometheus 피크 쿼리 |
| `_collect_error_logs()` | 에러 로그 수집 |
| `_generate_dashboard_links()` | 대시보드 링크 생성 |

### 11.2 빌드 순서

| 단계 | 동작 | 실패 시 |
|------|------|--------|
| 1 | Redis에서 OPEN 스냅샷 조회 | 빈 dict 사용 |
| 2 | CLOSED 시점 스냅샷 수집 | 필수 (실패 시 에러) |
| 3 | Prometheus 피크 쿼리 | 건너뛰기 (로그 경고) |
| 4 | 에러 로그 수집 | 빈 리스트 사용 |
| 5 | 대시보드 링크 생성 | 건너뛰기 |

---

## 12. Settings

### 12.1 스냅샷 관련 설정

| 설정 | 타입 | 기본값 | 설명 |
|------|------|--------|------|
| `snapshot_prometheus_enabled` | `bool` | `True` | Prometheus 쿼리 활성화 |
| `snapshot_prometheus_url` | `str` | `http://prometheus:9090` | Prometheus URL |
| `snapshot_prometheus_timeout` | `int` | `10` | 쿼리 타임아웃 (초) |
| `snapshot_logs_enabled` | `bool` | `True` | 로그 수집 활성화 |
| `snapshot_logs_max_count` | `int` | `50` | 최대 로그 개수 |
| `snapshot_logs_max_length` | `int` | `500` | 로그 메시지 최대 길이 |
| `snapshot_grafana_base_url` | `str` | `http://grafana:3000` | Grafana URL |
| `snapshot_grafana_dashboard_uid` | `str` | `selfhealing` | 대시보드 UID |

### 12.2 환경 변수

```
SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_ENABLED=true
SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_URL=http://prometheus:9090
SELFHEALING_POSTMORTEM_SNAPSHOT_PROMETHEUS_TIMEOUT=10
SELFHEALING_POSTMORTEM_SNAPSHOT_LOGS_ENABLED=true
SELFHEALING_POSTMORTEM_SNAPSHOT_LOGS_MAX_COUNT=50
SELFHEALING_POSTMORTEM_SNAPSHOT_GRAFANA_BASE_URL=http://grafana:3000
SELFHEALING_POSTMORTEM_SNAPSHOT_GRAFANA_DASHBOARD_UID=selfhealing
```

---

## 13. 구현 체크리스트

### 13.1 CB OPEN 스냅샷

- [ ] `_on_circuit_breaker_opened_snapshot()` 핸들러 생성
- [ ] Redis 저장 로직 구현
- [ ] `register_default_handlers()`에 등록

### 13.2 Prometheus Collector

- [ ] `services/postmortem/prometheus_collector.py` 생성
- [ ] `query_range()` 메서드 구현
- [ ] `get_peak_metrics()` 메서드 구현
- [ ] 연결 실패 시 graceful fallback

### 13.3 Log Buffer

- [ ] `services/postmortem/log_buffer.py` 생성
- [ ] 인메모리 버퍼 구현
- [ ] 로그 핸들러 연동 (logging.Handler)

### 13.4 Snapshot Builder

- [ ] `services/postmortem/snapshot_builder.py` 생성
- [ ] `build()` 메서드 구현
- [ ] 대시보드 링크 생성 로직

### 13.5 Postmortem 통합

- [ ] `_generate_postmortem_data()` 수정
- [ ] `SnapshotBuilder.build()` 호출
- [ ] 반환 구조에 확장 필드 추가

### 13.6 테스트

- [ ] CB OPEN 스냅샷 저장 확인
- [ ] Prometheus 쿼리 통합 테스트
- [ ] 대시보드 링크 생성 확인
- [ ] Prometheus 미연결 시 fallback 확인

---

## 14. 데이터 크기 고려

### 14.1 예상 크기

| 항목 | 예상 크기 |
|------|----------|
| 기존 timeline (30 이벤트) | ~5 KB |
| metrics_at_open | ~1 KB |
| metrics_at_close | ~1 KB |
| peak_metrics | ~2 KB |
| captured_logs (50개) | ~25 KB |
| dashboard_links | ~1 KB |
| **총합** | **~35 KB** |

### 14.2 저장 최적화

| 방안 | 설명 |
|------|------|
| 로그 압축 | 메시지 중복 제거 |
| 메트릭 샘플링 | 주요 지표만 저장 |
| 링크 단축 | URL 단축 서비스 사용 (선택) |

---

## 15. 관련 문서

- [146_POSTMORTEM_LIFECYCLE_INTEGRATION.md](146_POSTMORTEM_LIFECYCLE_INTEGRATION.md) - 생명주기 통합
- [147_POSTMORTEM_INCIDENT_GROUP.md](147_POSTMORTEM_INCIDENT_GROUP.md) - 인시던트 병합
- [129_POSTMORTEM_ROOT_CAUSE.md](129_POSTMORTEM_ROOT_CAUSE.md) - Root Cause 추론
- [08_OBSERVABILITY.md](../08_OBSERVABILITY.md) - PromQL 쿼리 참조

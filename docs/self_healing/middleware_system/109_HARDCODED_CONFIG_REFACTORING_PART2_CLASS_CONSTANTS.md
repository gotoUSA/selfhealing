# 109. 하드코딩 설정값 리팩토링 Part 2: 클래스/모듈 레벨 상수

## 문서 정보

| 항목 | 내용 |
|------|------|
| 문서 번호 | 109 |
| 작성일 | 2026-01-26 |
| 수정일 | 2026-01-26 |
| 대상 | 클래스/모듈 레벨의 DEFAULT_* 상수 (Settings 미연동 항목만) |
| 우선순위 | 중간 |
| 예상 작업량 | 8개 파일 |

---

## 1. 개요

### 1.1 정리 기준

- ✅ **포함**: Settings 연동이 필요한 항목 (운영 중 변경 가능성 있음)
- ❌ **제외**: 이미 Settings 연동 완료된 항목
- ❌ **제외**: 알고리즘 상수/내부 구현 세부사항

---

## 2. Settings 연동이 필요한 항목 (총 8개 파일)

### 2.1 adapters/rate_limit/redis_adapter.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 74-75 | `DEFAULT_TTL` | `3600` | `SELFHEALING_RATE_LIMIT_REDIS_TTL` |

**근거**: Rate limit TTL은 트래픽 패턴에 따라 운영 중 조정 필요

---

### 2.2 adapters/airgap/redis_adapter.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 52 | `DEFAULT_TTL` | `3600` | `SELFHEALING_AIRGAP_REDIS_TTL` |

**근거**: Air-gap TTL은 장애 격리 기간에 영향

---

### 2.3 adapters/audit/redis_buffer.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 58-59 | `DEFAULT_TTL_SECONDS` | `86400` | `SELFHEALING_AUDIT_BUFFER_REDIS_TTL` |

**근거**: 감사 버퍼 TTL은 규정 준수 요건에 따라 변경 가능

---

### 2.4 services/error_budget/multiplier.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 55 | `DEFAULT_CACHE_TTL_SECONDS` | `30.0` | `SELFHEALING_ERROR_BUDGET_MULTIPLIER_CACHE_TTL` |
| 58 | `DEFAULT_MAX_MULTIPLIER` | `10.0` | `SELFHEALING_ERROR_BUDGET_MULTIPLIER_MAX` |

**근거**: Error budget 승수는 SLA 정책에 따라 조정 필요

---

### 2.5 services/coordination/recovery_dashboard.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 310 | `DEFAULT_STALE_THRESHOLD_MINUTES` | `30` | `SELFHEALING_DASHBOARD_STALE_THRESHOLD_MINUTES` |
| 313 | `DEFAULT_MAX_REGIONAL_STATUS` | `5` | `SELFHEALING_DASHBOARD_MAX_REGIONAL_STATUS` |

**근거**: 대시보드 임계값은 모니터링 요건에 따라 조정 필요

---

### 2.6 metrics/snapshot_storage.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 131 | `DEFAULT_MAX_AGE` | `3600` | `SELFHEALING_METRICS_SNAPSHOT_MAX_AGE` |

**근거**: 메트릭 스냅샷 보존 기간은 스토리지 정책에 따라 조정

---

### 2.7 metrics/safe_gauge/core.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 293 | `DEFAULT_MAX_LABEL_COMBINATIONS` | `1000` | `SELFHEALING_SAFE_GAUGE_MAX_LABEL_COMBINATIONS` |

**근거**: 라벨 조합 제한은 카디널리티 폭발 방지를 위해 조정 가능

---

### 2.8 audit/integrity/cold_storage.py

| 라인 | 상수명 | 현재 값 | Settings 키 |
|------|--------|---------|-------------|
| 254 | `ARCHIVE_THRESHOLD_DAYS` | `7` | `SELFHEALING_AUDIT_COLD_ARCHIVE_THRESHOLD_DAYS` |
| 255 | `DEFAULT_COLD_RETENTION_YEARS` | `7` | `SELFHEALING_AUDIT_COLD_RETENTION_YEARS` |

**근거**: 감사 데이터 보존 기간은 규정 요건에 따라 다름

---

## 3. 제외된 항목 (이미 Settings 연동됨)

| 파일 | 상수 | 상태 |
|------|------|------|
| `audit/integrity/sequence.py` | `DEFAULT_PENDING_TTL_SECONDS` | ✅ 연동됨 |
| `audit/integrity/anchor.py` | `DEFAULT_RETENTION_DAYS` | ✅ 연동됨 |
| `audit/hash_chain_safety.py` | `DEFAULT_TIMEOUT_SECONDS` | ✅ 연동됨 |
| `services/namespace_emergency/cascade_detector.py` | `DEFAULT_ESCALATION_THRESHOLD` | ✅ 연동됨 |
| `services/namespace_emergency/tracker.py` | `CACHE_TTL_SECONDS` | ✅ 연동됨 |

---

## 4. 구현 순서

### Phase 1: Settings 파일 생성/확장

| 파일 | 작업 | 추가 필드 |
|------|------|----------|
| `settings/rate_limit.py` | 확장 | `redis_ttl` |
| `settings/airgap.py` | 신규 | `redis_ttl` |
| `settings/audit_settings.py` | 확장 | `buffer_redis_ttl` |
| `settings/error_budget.py` | 확장 | `multiplier_cache_ttl`, `multiplier_max` |
| `settings/dashboard.py` | 확장 | `stale_threshold_minutes`, `max_regional_status` |
| `settings/metrics.py` | 확장 | `snapshot_max_age` |
| `settings/safe_gauge.py` | 신규 | `max_label_combinations` |
| `settings/cascade_retention.py` | 확장 | `archive_threshold_days`, `cold_retention_years` |

---

## 5. 검증 체크리스트

| 항목 | 완료 |
|------|------|
| Settings 파일 생성/확장 | ☐ |
| 헬퍼 함수 생성 | ☐ |
| 클래스 수정 | ☐ |
| 하위 호환성 유지 | ☐ |
| 단위 테스트 | ☐ |

---

## 6. 관련 문서

- [108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md](108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
- [111_HARDCODED_CONFIG_REFACTORING_PART4_TIMEOUT_TTL.md](111_HARDCODED_CONFIG_REFACTORING_PART4_TIMEOUT_TTL.md)

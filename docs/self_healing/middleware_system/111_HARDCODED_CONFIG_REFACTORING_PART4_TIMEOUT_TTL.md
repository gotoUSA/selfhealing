# 111. 하드코딩 설정값 리팩토링 Part 4: Timeout/TTL

## 문서 정보

| 항목 | 내용 |
|------|------|
| 문서 번호 | 111 |
| 작성일 | 2026-01-26 |
| 수정일 | 2026-01-26 |
| 대상 | HTTP Timeout, Redis TTL (고가치 항목만) |
| 우선순위 | 중간 |
| 예상 작업량 | 10개 파일 |

---

## 1. 개요

### 1.1 정리 기준

- ✅ **포함**: 외부 서비스 연동 timeout (운영 중 조정 필요)
- ✅ **포함**: 캐시/데이터 TTL (정책에 따라 변경)
- ❌ **제외**: `page=1`, `page_size=20` 같은 UI 기본값 (변경 필요 없음)
- ❌ **제외**: `timedelta(hours=1)` 같은 알고리즘 로직 상수

---

## 2. Settings 연동이 필요한 항목 (총 10개 파일)

### 2.1 HTTP Timeout

#### 2.1.1 services/http_client.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 38 | `timeout=30` | `30` | `SELFHEALING_HTTP_CLIENT_DEFAULT_TIMEOUT` |

**근거**: 외부 API 응답 시간은 환경에 따라 다름

---

#### 2.1.2 adapters/queues/celery_adapter.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 488 | `inspect(timeout=2)` | `2` | `SELFHEALING_CELERY_INSPECT_TIMEOUT` |

**근거**: Celery inspect timeout은 워커 상태에 따라 조정 필요

---

### 2.2 Redis/Cache TTL

#### 2.2.1 services/canary/cross_cluster.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 848 | `ttl = 60 * 60 * 24 * 7` | `604800` (7일) | `SELFHEALING_CANARY_PROPAGATION_TTL` |

**근거**: Canary 전파 요청 보존 기간은 롤아웃 정책에 따라 다름

---

#### 2.2.2 services/daily_report/aggregator.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 62 | `timeout=86400 * 2` | `172800` (2일) | `SELFHEALING_DAILY_REPORT_CACHE_TTL` |

**근거**: 일일 리포트 캐시 기간

---

#### 2.2.3 services/governance_checks.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 296 | `TTLCache(default_ttl=30.0)` | `30.0` | `SELFHEALING_GOVERNANCE_CACHE_TTL` |

**근거**: 거버넌스 체크 캐시 TTL

---

#### 2.2.4 tasks/chaos_scheduler.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 435 | `acquire_lock(..., ttl_seconds=120)` | `120` | `SELFHEALING_CHAOS_EXPERIMENT_LOCK_TTL` |

**근거**: 분산 락 TTL은 실험 시간에 따라 조정

---

### 2.3 함수 파라미터 기본값 (cleanup 관련)

#### 2.3.1 services/pending_config.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 314 | `cleanup_expired(max_age_hours=24)` | `24` | `SELFHEALING_PENDING_CONFIG_CLEANUP_MAX_AGE_HOURS` |

**근거**: 만료된 설정 정리 기간

---

#### 2.3.2 tasks/config_apply.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 219 | `max_age_hours=24` | `24` | `SELFHEALING_CONFIG_CLEANUP_MAX_AGE_HOURS` |

**근거**: 설정 변경 정리 기간

---

#### 2.3.3 services/coordination/recovery_tasks.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 606 | `max_age_hours=168` | `168` (7일) | `SELFHEALING_RECOVERY_CLEANUP_MAX_AGE_HOURS` |

**근거**: 복구 세션 보존 기간

---

#### 2.3.4 services/coordination/pending_recovery_approval.py

| 라인 | 코드 | 현재 값 | Settings 키 |
|------|------|---------|-------------|
| 537 | `max_age_hours=24` | `24` | `SELFHEALING_APPROVAL_CLEANUP_MAX_AGE_HOURS` |

**근거**: 승인 요청 보존 기간

---

## 3. 제외된 항목 (저가치)

### 3.1 API View 기본값 (변경 필요 없음)

| 파일 | 코드 | 제외 이유 |
|------|------|----------|
| `api/django/views/*.py` | `page=1` | 표준 페이지네이션, 바꿀 이유 없음 |
| `api/django/views/*.py` | `page_size=20` | UI 표준값, 운영에서 안 바꿈 |
| `api/django/views/*.py` | `limit=100` | API 기본값, 거의 안 바꿈 |

### 3.2 timedelta 알고리즘 상수 (로직에 밀접)

| 파일 | 코드 | 제외 이유 |
|------|------|----------|
| `coordination/anti_flapping.py` | `timedelta(hours=1)` | 플래핑 감지 알고리즘 상수 |
| `core/auto_rollback_guard.py` | `timedelta(minutes=5)` | 롤백 안전 기간 (알고리즘) |
| `audit/integrity/health_score.py` | `timedelta(hours=24)` | 헬스 스코어 계산 윈도우 |

---

## 4. 구현 순서

### Phase 1: Settings 파일 생성/확장

| 파일 | 작업 | 추가 필드 |
|------|------|----------|
| `settings/http_client.py` | 신규 | `default_timeout` |
| `settings/celery_task.py` | 확장 | `inspect_timeout` |
| `settings/canary.py` | 확장 | `propagation_ttl` |
| `settings/daily_report.py` | 신규 | `cache_ttl` |
| `settings/governance.py` | 확장 | `cache_ttl` |
| `settings/chaos.py` | 확장 | `experiment_lock_ttl` |
| `settings/cleanup.py` | 확장 | `config_max_age_hours`, `recovery_max_age_hours`, `approval_max_age_hours` |

---

## 5. 검증 체크리스트

| 항목 | 완료 |
|------|------|
| Settings 파일 생성/확장 | ☐ |
| 헬퍼 함수 생성 | ☐ |
| 코드 수정 | ☐ |
| 하위 호환성 유지 | ☐ |
| 단위 테스트 | ☐ |

---

## 6. 관련 문서

- [108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md](108_HARDCODED_CONFIG_REFACTORING_PART1_CELERY_TASKS.md)
- [109_HARDCODED_CONFIG_REFACTORING_PART2_CLASS_CONSTANTS.md](109_HARDCODED_CONFIG_REFACTORING_PART2_CLASS_CONSTANTS.md)

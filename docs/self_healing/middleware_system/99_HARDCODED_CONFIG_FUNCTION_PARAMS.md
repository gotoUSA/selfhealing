# 99. 함수 파라미터 기본값 통합

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94-98
- **대상**: 함수 시그니처에 하드코딩된 기본값

---

## 1. 개요

이 문서는 함수 파라미터의 기본값으로 하드코딩된 설정값을 다룹니다.  
이러한 값들은 앞선 문서(95-98)에서 다루지 않은 추가적인 파일들을 대상으로 합니다.

---

## 2. 대상 파일 목록

| 파일 | 하드코딩 항목 수 | 유형 | 우선순위 |
|------|----------------|------|----------|
| `services/cleanup_service.py` | 4건 | 함수 파라미터 | 높음 |
| `services/pending_config.py` | 2건 | 함수 파라미터 | 중간 |
| `services/dashboard_service.py` | 3건 | 함수 파라미터 | 중간 |
| `services/config_history.py` | 2건 | 상수 + 함수 파라미터 | 중간 |
| `services/circuit_breaker/manual_control.py` | 1건 | 함수 파라미터 | 중간 |
| `services/blast_radius/service.py` | 3건 | 함수 파라미터 | 낮음 |
| `services/isolation/regional_gate.py` | 1건 | 함수 파라미터 | 낮음 |
| `adapters/memory/layered_repository.py` | 3건 | 함수 파라미터 | 낮음 |
| `adapters/postgres/repository.py` | 3건 | 함수 파라미터 | 낮음 |
| `utils/jitter.py` | 6건 | 함수 파라미터 | 낮음 |
| `utils/async_logger.py` | 2건 | 함수 파라미터 | 낮음 |

---

## 3. services/cleanup_service.py 상세

### 3.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 68 | `archive_old_dlq_entries` | `older_than_days` | `30` | DLQ 보존 일수 |
| 118 | `cleanup_expired_config` | `older_than_hours` | `24` | 설정 만료 시간 |
| 168 | `expire_approval_requests` | `older_than_hours` | `72` | 승인 요청 만료 시간 |
| 212 | `purge_old_audit_logs` | `older_than_days` | `90` | 감사 로그 보존 일수 |

### 3.2 현황 분석
- `tasks/cleanup_tasks.py`의 기본값과 동일한 값들
- 문서 97에서 생성하는 `settings/cleanup.py` 재활용 가능

### 3.3 구현 순서

1. **Step 1**: 문서 97의 `settings/cleanup.py` 생성 완료 확인
2. **Step 2**: `cleanup_service.py`에서 Settings import
3. **Step 3**: 각 함수 파라미터 기본값을 `None`으로 변경
4. **Step 4**: 함수 본문에서 Settings 값 사용

### 3.4 리팩토링 패턴

**Before:**
```
def archive_old_dlq_entries(self, older_than_days: int = 30):
    ...
```

**After:**
```
def archive_old_dlq_entries(self, older_than_days: int | None = None):
    settings = get_cleanup_settings()
    older_than_days = older_than_days or settings.dlq_older_than_days
    ...
```

---

## 4. services/pending_config.py 상세

### 4.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 305 | `get_pending` | `limit` | `50` | 조회 제한 |
| 314 | `cleanup_expired` | `max_age_hours` | `24` | 만료 기준 시간 |

### 4.2 구현 순서

1. **Step 1**: `settings/pending_config.py` 생성 또는 기존 Settings 확장
2. **Step 2**: 함수 파라미터 변경

### 4.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_PENDING_CONFIG_DEFAULT_LIMIT` | `50` | 조회 기본 제한 |
| `SELFHEALING_PENDING_CONFIG_MAX_AGE_HOURS` | `24` | 만료 기준 시간 |

---

## 5. services/dashboard_service.py 상세

### 5.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 385 | `get_recent_activity` | `hours` | `24` | 최근 활동 기준 시간 |
| 385 | `get_recent_activity` | `days` | `7` | 최근 활동 기준 일수 |
| 410 | `get_distribution` | `limit` | `10` | 분포 조회 제한 |
| 505-507 | `_calculate_health` | `10`, `50`, `5` | 상태 판단 임계치 |

### 5.2 현황 분석
- `settings/dashboard.py` 존재
- 일부 설정 누락

### 5.3 구현 순서

1. **Step 1**: `settings/dashboard.py` 확장
2. **Step 2**: 함수 파라미터 변경

### 5.4 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_DASHBOARD_RECENT_ACTIVITY_HOURS` | `24` | 최근 활동 시간 |
| `SELFHEALING_DASHBOARD_RECENT_ACTIVITY_DAYS` | `7` | 최근 활동 일수 |
| `SELFHEALING_DASHBOARD_DISTRIBUTION_LIMIT` | `10` | 분포 조회 제한 |

---

## 6. services/config_history.py 상세

### 6.1 발견된 하드코딩

| 라인 | 변수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 99 | `MAX_HISTORY_ENTRIES` | `50` | 최대 히스토리 엔트리 (Deprecated) |
| 277 | `get_history` - `limit` | `10` | 조회 제한 |

### 6.2 현황 분석
- `MAX_HISTORY_ENTRIES`는 Deprecated로 표시됨
- `_get_max_history_entries()` 함수로 대체 권장

### 6.3 구현 순서

1. **Step 1**: `settings/audit_settings.py`에 이미 존재하는 설정 확인
2. **Step 2**: 함수 파라미터 변경
3. **Step 3**: Deprecated 상수 제거 검토

---

## 7. services/circuit_breaker/manual_control.py 상세

### 7.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 496 | `extend_maintenance` | `additional_minutes` | `90` | 유지보수 연장 시간(분) |

### 7.2 구현 순서

1. **Step 1**: Circuit Breaker Settings 확장
2. **Step 2**: 함수 파라미터 변경

### 7.3 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CB_DEFAULT_MAINTENANCE_EXTENSION_MINUTES` | `90` | 유지보수 연장 기본값 |

---

## 8. services/blast_radius/service.py 상세

### 8.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 65 | `__init__` | `max_affected_percentage` | `10.0` | 최대 영향 비율 |
| 174 | `estimate_impact` | `total_users` | `1000` | 총 사용자 수 추정 |
| 447 | `get_history` | `limit` | `100` | 히스토리 조회 제한 |

### 8.2 구현 순서

1. **Step 1**: Blast Radius Settings 생성 또는 확장
2. **Step 2**: 함수 파라미터 변경

---

## 9. services/isolation/regional_gate.py 상세

### 9.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 139 | `isolate_region` | `duration_seconds` | `300` | 격리 지속 시간(초) |

### 9.2 구현 순서

1. **Step 1**: Regional 관련 Settings 생성 또는 확장
2. **Step 2**: 함수 파라미터 변경

### 9.3 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_REGIONAL_GATE_DEFAULT_ISOLATION_SECONDS` | `300` | 기본 격리 시간(초) |

---

## 10. adapters/memory/layered_repository.py 상세

### 10.1 발견된 하드코딩

| 라인 | 함수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 59 | `sync_interval_seconds` | `5` | 동기화 주기 |
| 73 | `ThreadPoolExecutor` | `4` | max_workers |
| 79 | `__init__` | `5.0` | sync_interval_seconds 기본값 |
| 655 | `save_with_ttl` | `90` | ttl_minutes 기본값 |
| 692 | `save_batch_with_ttl` | `90` | ttl_minutes 기본값 |

### 10.2 현황 분석
- `settings/l2_storage.py` 또는 `settings/layered_provider.py` 관련
- 일부 설정 누락 가능

### 10.3 구현 순서

1. **Step 1**: L2 Storage Settings 확장
2. **Step 2**: 함수 파라미터 및 상수 변경

### 10.4 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_L2_SYNC_INTERVAL_SECONDS` | `5.0` | 동기화 주기 |
| `SELFHEALING_L2_THREAD_POOL_WORKERS` | `4` | 스레드 풀 워커 수 |
| `SELFHEALING_L2_DEFAULT_TTL_MINUTES` | `90` | 기본 TTL(분) |

---

## 11. adapters/postgres/repository.py 상세

### 11.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 152 | `get_backend_pid_with_delay` | `delay_seconds` | `0.01` | 지연 시간 |
| 331 | `execute_timeout_query` | `timeout_ms`, `sleep_seconds` | `1`, `1` | 타임아웃, 슬립 |
| 412-413 | `timeout_context` | `lock_timeout_ms`, `statement_timeout_ms` | `0`, `0` | 타임아웃 기본값 |

### 11.2 구현 순서

1. **Step 1**: Postgres 관련 Settings 생성 또는 확장
2. **Step 2**: 함수 파라미터 변경

---

## 12. utils/jitter.py 상세

### 12.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 27 | `with_jitter` | `max_delay_seconds` | `60.0` | 최대 지연 |
| 28 | `with_jitter` | `min_delay_seconds` | `0.0` | 최소 지연 |
| 75-76 | `calculate_jitter` | 동일 | 동일 | |
| 99-100 | `sleep_with_jitter` | 동일 | 동일 | |
| 122-123 | `async_sleep_with_jitter` | 동일 | 동일 | |
| 154-155 | `JitterConfig` | 동일 | 동일 | |

### 12.2 현황 분석
- 유틸리티 함수로서 기본값은 합리적
- Settings 연동 시 전역 설정으로 활용 가능

### 12.3 구현 순서

1. **Step 1**: `settings/jitter.py` 생성 (선택적)
2. **Step 2**: 기본값을 Settings에서 로드 (선택적)

### 12.4 신규 Settings 항목 (선택적)

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_JITTER_DEFAULT_MAX_DELAY_SECONDS` | `60.0` | 기본 최대 지연 |
| `SELFHEALING_JITTER_DEFAULT_MIN_DELAY_SECONDS` | `0.0` | 기본 최소 지연 |

---

## 13. utils/async_logger.py 상세

### 13.1 발견된 하드코딩

| 라인 | 함수명 | 파라미터 | 하드코딩 값 | 설명 |
|------|--------|----------|------------|------|
| 114 | `stop` | `timeout` | `5.0` | 정지 타임아웃 |
| 193 | `_worker` | `timeout` | `1.0` | 큐 대기 타임아웃 |

### 13.2 구현 순서

1. **Step 1**: Logging Settings 확장
2. **Step 2**: 함수 파라미터 변경

---

## 14. 전체 구현 순서 요약

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1 | `cleanup_service.py` - Settings 연동 | 25분 |
| 2 | `settings/pending_config.py` 생성 | 15분 |
| 3 | `pending_config.py` - Settings 연동 | 15분 |
| 4 | `settings/dashboard.py` 확장 | 15분 |
| 5 | `dashboard_service.py` - Settings 연동 | 20분 |
| 6 | `config_history.py` - Settings 연동 | 15분 |
| 7 | Circuit Breaker Settings 확장 | 10분 |
| 8 | `manual_control.py` - Settings 연동 | 10분 |
| 9 | Blast Radius Settings 확장 | 15분 |
| 10 | `blast_radius/service.py` - Settings 연동 | 15분 |
| 11 | Regional Gate Settings 생성 | 15분 |
| 12 | `regional_gate.py` - Settings 연동 | 10분 |
| 13 | L2 Storage Settings 확장 | 15분 |
| 14 | `layered_repository.py` - Settings 연동 | 20분 |
| 15 | Postgres Settings 생성 (선택적) | 15분 |
| 16 | `repository.py` - Settings 연동 (선택적) | 15분 |
| 17 | Jitter Settings 생성 (선택적) | 10분 |
| 18 | `jitter.py` - Settings 연동 (선택적) | 15분 |
| 19 | Logging Settings 확장 | 10분 |
| 20 | `async_logger.py` - Settings 연동 | 10분 |
| 21 | 테스트 실행 및 검증 | 40분 |
| **총계** | | **약 5.5시간** |

---

## 15. 검증 체크리스트

- [ ] `cleanup_service.py` 하드코딩 4건 제거됨
- [ ] `pending_config.py` 하드코딩 2건 제거됨
- [ ] `dashboard_service.py` 하드코딩 4건 제거됨
- [ ] `config_history.py` 하드코딩 2건 제거됨
- [ ] `manual_control.py` 하드코딩 1건 제거됨
- [ ] `blast_radius/service.py` 하드코딩 3건 제거됨
- [ ] `regional_gate.py` 하드코딩 1건 제거됨
- [ ] `layered_repository.py` 하드코딩 4건 제거됨
- [ ] `repository.py` 하드코딩 3건 제거됨 (선택적)
- [ ] `jitter.py` 하드코딩 6건 제거됨 (선택적)
- [ ] `async_logger.py` 하드코딩 2건 제거됨
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

---

## 16. 전체 시리즈 요약 (문서 94-99)

### 16.1 총 작업량

| 문서 | 대상 | 파일 수 | 하드코딩 항목 | 예상 소요 |
|------|------|---------|-------------|----------|
| 95 | API 뷰 | 6개 | ~24건 | 4시간 |
| 96 | 서비스 계층 | 12개 | ~50건 | 6시간 |
| 97 | 태스크 | 10개 | ~47건 | 6.5시간 |
| 98 | Audit 모듈 | 12개 | ~50건 | 7시간 |
| 99 | 함수 파라미터 | 11개 | ~32건 | 5.5시간 |
| **총계** | | **51개** | **~203건** | **~29시간** |

### 16.2 권장 구현 순서

1. **1주차**: 문서 95 (API 뷰) + 문서 96 일부 (서비스 계층 - 높은 우선순위)
2. **2주차**: 문서 96 나머지 + 문서 97 (태스크)
3. **3주차**: 문서 98 (Audit 모듈)
4. **4주차**: 문서 99 (함수 파라미터) + 전체 검증

### 16.3 신규 Settings 파일 생성 목록

| 파일명 | 문서 | 우선순위 |
|--------|------|----------|
| `settings/stress_test.py` | 95 | 높음 |
| `settings/cleanup.py` | 97 | 높음 |
| `settings/precomputed_cache.py` | 96 | 중간 |
| `settings/recovery_task.py` | 97 | 중간 |
| `settings/intelligence_task.py` | 97 | 중간 |
| `settings/config_apply.py` | 97 | 중간 |
| `settings/wal.py` | 98 | 중간 |
| `settings/hash_chain.py` | 98 | 낮음 |
| `settings/pending_config.py` | 99 | 낮음 |
| `settings/jitter.py` (선택적) | 99 | 낮음 |

### 16.4 확장 필요 기존 Settings 목록

| 파일명 | 관련 문서 |
|--------|----------|
| `settings/dlq.py` | 96 |
| `settings/corruption_shield.py` | 96 |
| `settings/recovery_circuit_breaker.py` | 96 |
| `settings/redis_key_guard.py` | 96 |
| `settings/security.py` | 96 |
| `settings/chaos_blast_radius.py` | 96 |
| `settings/notification_channel.py` | 97 |
| `settings/chaos.py` | 97 |
| `settings/resilient_recorder.py` | 98 |
| `settings/cascade_retention.py` | 98 |
| `settings/audit_integrity.py` | 98 |
| `settings/dashboard.py` | 99 |
| `settings/l2_storage.py` | 99 |
| `settings/circuit_breaker.py` | 99 |

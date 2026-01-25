# 98. Audit 모듈 설정 외부화

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 계획
- **선행 문서**: 94-97
- **대상 경로**: `audit/`

---

## 1. 대상 파일 목록

| 파일 | 하드코딩 항목 수 | 기존 Settings 유무 | 우선순위 |
|------|----------------|-------------------|----------|
| `audit/resilient_recorder.py` | 5건 | O (resilient_recorder.py) | 높음 |
| `audit/cascade_config.py` | 7건 | O (cascade_retention.py) | 높음 |
| `audit/wal.py` | 5건 | X | 중간 |
| `audit/integrity/sequence.py` | 2건 | O (audit_integrity.py) | 중간 |
| `audit/ring_buffer.py` | 2건 | X | 낮음 |
| `audit/resilience/buffer.py` | 2건 | X | 낮음 |
| `audit/reconciler.py` | 4건 | X | 낮음 |
| `audit/sync_worker.py` | 5건 | X | 낮음 |
| `audit/hash_chain_safety.py` | 5건 | X | 낮음 |
| `audit/performance/watchdog.py` | 2건 | X | 낮음 |
| `audit/performance/async_writer.py` | 3건 | X | 낮음 |
| `audit/continuous_audit.py` | 6건 | X | 낮음 |

---

## 2. resilient_recorder.py 상세

### 2.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 67 | `buffer_capacity` | `10000` | 버퍼 용량 |
| 72 | `flush_interval_seconds` | `1.0` | 플러시 주기(초) |
| 73 | `flush_batch_size` | `100` | 플러시 배치 크기 |
| 78 | `circuit_timeout_seconds` | `30.0` | 서킷 타임아웃(초) |
| 256 | `stop` 함수 | `5.0` | 정지 타임아웃(초) |

### 2.2 현황 분석
- `settings/resilient_recorder.py`에 대부분 설정 존재
- dataclass `ResilientRecorderConfig`가 Settings 연동 안됨

### 2.3 구현 순서

1. **Step 1**: `settings/resilient_recorder.py` 완전성 검증
2. **Step 2**: `ResilientRecorderConfig`에 `from_settings()` 팩토리 추가
3. **Step 3**: 사용처에서 팩토리 메서드 사용

### 2.4 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_RESILIENT_RECORDER_STOP_TIMEOUT` | `5.0` | 정지 타임아웃(초) |

---

## 3. cascade_config.py 상세

### 3.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 46 | `max_chain_depth` | `10` | 체인 최대 깊이 |
| 170 | `hot_max_count` | `10000` | 핫 스토리지 최대 개수 |
| 174 | `warm_retention_days` | `90` | 웜 스토리지 보존 일수 |
| 178 | `cold_retention_days` | `365` | 콜드 스토리지 보존 일수 |
| 182 | `index_retention_days` | `30` | 인덱스 보존 일수 |
| 186 | `anchor_retention_days` | `90` | 앵커 보존 일수 |
| 256 | `max_events_per_second` | `1000` | 초당 최대 이벤트 |

### 3.2 현황 분석
- `settings/cascade_retention.py`에 보존 정책 설정 존재
- `CascadeConfig` 관련 dataclass가 Settings 연동 안됨

### 3.3 구현 순서

1. **Step 1**: `settings/cascade_retention.py` 누락 항목 추가
2. **Step 2**: 각 dataclass에 `from_settings()` 팩토리 추가
3. **Step 3**: 사용처에서 팩토리 메서드 사용

### 3.4 추가 필요 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_CASCADE_MAX_CHAIN_DEPTH` | `10` | 체인 최대 깊이 |
| `SELFHEALING_CASCADE_MAX_EVENTS_PER_SECOND` | `1000` | 초당 최대 이벤트 |

---

## 4. wal.py 상세

### 4.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 96 | `max_file_size_mb` | `100` | WAL 파일 최대 크기(MB) |
| 98 | `max_files` | `10` | 최대 보관 파일 수 |
| 103 | `group_commit_max_entries` | `100` | 그룹 커밋 최대 엔트리 |
| 104 | `group_commit_max_wait_ms` | `10` | 그룹 커밋 최대 대기(ms) |
| 723 | `create_wal_writer` 함수 | `100` | max_file_size_mb 기본값 |

### 4.2 현황 분석
- 전용 Settings 없음
- 신규 Settings 생성 필요

### 4.3 구현 순서

1. **Step 1**: `settings/wal.py` 생성
2. **Step 2**: `WALConfig`에 `from_settings()` 팩토리 추가
3. **Step 3**: 팩토리 함수에서 Settings 참조

### 4.4 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_WAL_MAX_FILE_SIZE_MB` | `100` | WAL 파일 최대 크기(MB) |
| `SELFHEALING_WAL_MAX_FILES` | `10` | 최대 보관 파일 수 |
| `SELFHEALING_WAL_GROUP_COMMIT_MAX_ENTRIES` | `100` | 그룹 커밋 최대 엔트리 |
| `SELFHEALING_WAL_GROUP_COMMIT_MAX_WAIT_MS` | `10` | 그룹 커밋 최대 대기(ms) |

---

## 5. integrity/sequence.py 상세

### 5.1 발견된 하드코딩

| 라인 | 상수명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 63 | `DEFAULT_PENDING_TTL_SECONDS` | `30` | 대기 TTL(초) |
| 64 | `DEFAULT_ORPHAN_TTL_SECONDS` | `86400` | 고아 TTL (24시간) |

### 5.2 현황 분석
- `settings/audit_integrity.py`에 설정 존재
- 모듈 상수가 Settings를 참조하지 않음

### 5.3 구현 순서

1. **Step 1**: `settings/audit_integrity.py` 검증
2. **Step 2**: 모듈 상수를 Settings 참조로 변경

---

## 6. ring_buffer.py 상세

### 6.1 발견된 하드코딩

| 라인 | 함수/파라미터 | 하드코딩 값 | 설명 |
|------|--------------|------------|------|
| 165 | `get_batch` | `100` | max_size 기본값 |
| 195 | `peek_batch` | `100` | max_size 기본값 |

### 6.2 구현 순서

1. **Step 1**: 기존 Audit Settings에 항목 추가
2. **Step 2**: 함수 기본값을 Settings 참조로 변경

---

## 7. resilience/buffer.py 상세

### 7.1 발견된 하드코딩

| 라인 | 상수명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 38 | `MAX_ENTRIES` | `10_000` | 버퍼 최대 엔트리 |
| 39 | `FLUSH_INTERVAL_SECONDS` | `30.0` | 플러시 주기(초) |

### 7.2 구현 순서

1. **Step 1**: Resilient Recorder Settings 확장
2. **Step 2**: 클래스 상수를 Settings 참조로 변경

---

## 8. reconciler.py 상세

### 8.1 발견된 하드코딩

| 라인 | 필드명/변수 | 하드코딩 값 | 설명 |
|------|------------|------------|------|
| 41 | `check_interval_seconds` | `300.0` | 체크 주기(초) |
| 50 | `max_resend_attempts` | `3` | 최대 재전송 시도 |
| 170 | `_confirmed_ids_max_size` | `10000` | 확인 ID 최대 크기 |
| 249 | `stop` 함수 | `5.0` | 정지 타임아웃(초) |

### 8.2 구현 순서

1. **Step 1**: `settings/audit_reconciler.py` 생성 또는 기존 확장
2. **Step 2**: dataclass 필드 기본값을 Settings 연동

### 8.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_AUDIT_RECONCILER_CHECK_INTERVAL` | `300.0` | 체크 주기(초) |
| `SELFHEALING_AUDIT_RECONCILER_MAX_RESEND_ATTEMPTS` | `3` | 최대 재전송 시도 |
| `SELFHEALING_AUDIT_RECONCILER_MAX_CONFIRMED_IDS` | `10000` | 확인 ID 최대 크기 |

---

## 9. sync_worker.py 상세

### 9.1 발견된 하드코딩

| 라인 | 필드명 | 하드코딩 값 | 설명 |
|------|--------|------------|------|
| 41 | `sync_interval_seconds` | `1.0` | 동기화 주기(초) |
| 47 | `max_retries` | `3` | 최대 재시도 |
| 48 | `retry_delay_seconds` | `1.0` | 재시도 지연(초) |
| 50 | `max_retry_delay_seconds` | `30.0` | 최대 재시도 지연(초) |
| 56 | `metrics_interval_seconds` | `60.0` | 메트릭 주기(초) |
| 233 | `stop` 함수 | `5.0` | 정지 타임아웃(초) |

### 9.2 구현 순서

1. **Step 1**: `settings/audit_sync.py` 생성 또는 기존 확장
2. **Step 2**: dataclass 필드 기본값을 Settings 연동

### 9.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_AUDIT_SYNC_INTERVAL_SECONDS` | `1.0` | 동기화 주기(초) |
| `SELFHEALING_AUDIT_SYNC_MAX_RETRIES` | `3` | 최대 재시도 |
| `SELFHEALING_AUDIT_SYNC_RETRY_DELAY` | `1.0` | 재시도 지연(초) |
| `SELFHEALING_AUDIT_SYNC_MAX_RETRY_DELAY` | `30.0` | 최대 재시도 지연(초) |
| `SELFHEALING_AUDIT_SYNC_METRICS_INTERVAL` | `60.0` | 메트릭 주기(초) |

---

## 10. hash_chain_safety.py 상세

### 10.1 발견된 하드코딩

| 라인 | 상수/변수 | 하드코딩 값 | 설명 |
|------|----------|------------|------|
| 215 | `max_file_size_mb` | `10` | 최대 파일 크기(MB) |
| 485 | `DEFAULT_TIMEOUT_SECONDS` | `300` | 기본 타임아웃 (5분) |
| 492 | `blocking_timeout` | `10.0` | 블로킹 타임아웃(초) |
| 605 | `DEFAULT_TIMEOUT_SECONDS` | `120` | 날짜별 타임아웃 (2분) |
| 725 | `MAX_REDIS_ENTRIES` | `1000` | Redis 최대 엔트리 |

### 10.2 구현 순서

1. **Step 1**: `settings/hash_chain.py` 생성
2. **Step 2**: 상수 및 기본값을 Settings 참조로 변경

### 10.3 신규 Settings 항목

| 환경 변수 | 기본값 | 설명 |
|----------|--------|------|
| `SELFHEALING_HASH_CHAIN_MAX_FILE_SIZE_MB` | `10` | 최대 파일 크기(MB) |
| `SELFHEALING_HASH_CHAIN_DEFAULT_TIMEOUT_SECONDS` | `300` | 기본 타임아웃(초) |
| `SELFHEALING_HASH_CHAIN_BLOCKING_TIMEOUT` | `10.0` | 블로킹 타임아웃(초) |
| `SELFHEALING_HASH_CHAIN_MAX_REDIS_ENTRIES` | `1000` | Redis 최대 엔트리 |

---

## 11. 기타 Audit 파일

### 11.1 performance/watchdog.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 47 | `check_interval_seconds` | `5.0` |
| 93 | `stop` 함수 | `5.0` |

### 11.2 performance/async_writer.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 40 | `max_queue_size` | `10000` |
| 42 | `flush_interval_seconds` | `0.1` |
| 80 | `stop` 함수 | `5.0` |
| 112 | `timeout` | `0.01` |

### 11.3 performance/batch_writer.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 23 | `flush_interval_seconds` | `10.0` |

### 11.4 performance/sampling.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 21 | `min_samples` | `10` |
| 22 | `max_samples` | `1000` |

### 11.5 resilience/degraded_mode.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 44 | `_check_interval_seconds` | `60` |

### 11.6 resilience/circuit_breaker.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 34 | `timeout_seconds` | `30.0` |
| 35 | `call_timeout_seconds` | `5.0` |

### 11.7 graceful_degradation/enums.py

| 라인 | 변수 | 하드코딩 값 |
|------|------|------------|
| 45 | `redis_timeout_seconds` | `5.0` |
| 46 | `replica_timeout_seconds` | `3.0` |
| 48 | `memory_max_entries` | `10000` |
| 56 | `recovery_timeout_seconds` | `30.0` |

### 11.8 continuous_audit.py

| 라인 | 함수 | 하드코딩 값 |
|------|------|------------|
| 435 | `query` | `limit=100` |
| 467 | `query_by_namespace` | `limit=100` |
| 497 | `query_by_correlation` | `limit=100` |
| 533 | `query_by_time_range` | `limit=100` |
| 570 | `export_to_json` | `limit=10000` |

---

## 12. 전체 구현 순서 요약

| 순서 | 작업 | 예상 소요 |
|------|------|----------|
| 1 | `resilient_recorder.py` - Settings 연동 | 25분 |
| 2 | `cascade_config.py` - Settings 연동 | 30분 |
| 3 | `settings/wal.py` 생성 | 20분 |
| 4 | `wal.py` - Settings 연동 | 25분 |
| 5 | `integrity/sequence.py` - Settings 연동 | 15분 |
| 6 | `ring_buffer.py` - Settings 연동 | 15분 |
| 7 | `resilience/buffer.py` - Settings 연동 | 15분 |
| 8 | `settings/audit_reconciler.py` 생성 또는 확장 | 20분 |
| 9 | `reconciler.py` - Settings 연동 | 20분 |
| 10 | `settings/audit_sync.py` 생성 또는 확장 | 20분 |
| 11 | `sync_worker.py` - Settings 연동 | 25분 |
| 12 | `settings/hash_chain.py` 생성 | 20분 |
| 13 | `hash_chain_safety.py` - Settings 연동 | 25분 |
| 14 | `performance/*.py` - Settings 연동 | 30분 |
| 15 | `resilience/*.py` - Settings 연동 | 25분 |
| 16 | `graceful_degradation/*.py` - Settings 연동 | 20분 |
| 17 | `continuous_audit.py` - Settings 연동 | 20분 |
| 18 | 테스트 실행 및 검증 | 40분 |
| **총계** | | **약 7시간** |

---

## 13. 검증 체크리스트

- [ ] `resilient_recorder.py` 하드코딩 5건 제거됨
- [ ] `cascade_config.py` 하드코딩 7건 제거됨
- [ ] `settings/wal.py` 생성됨
- [ ] `wal.py` 하드코딩 5건 제거됨
- [ ] `integrity/sequence.py` 하드코딩 2건 제거됨
- [ ] `ring_buffer.py` 하드코딩 2건 제거됨
- [ ] `reconciler.py` 하드코딩 4건 제거됨
- [ ] `sync_worker.py` 하드코딩 5건 제거됨
- [ ] `hash_chain_safety.py` 하드코딩 5건 제거됨
- [ ] performance 하위 파일 하드코딩 제거됨
- [ ] resilience 하위 파일 하드코딩 제거됨
- [ ] 기존 테스트 모두 통과
- [ ] 환경 변수 오버라이드 동작 확인

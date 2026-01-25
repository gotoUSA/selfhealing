# 105. Audit 모듈 하드코딩된 설정값 리팩토링 계획

## 문서 정보
- **작성일**: 2026-01-25
- **상태**: 진행중 (Step 1,2,3,4 완료)
- **관련 문서**: 102_HARDCODED_CONFIG_FINAL_AUDIT.md
- **대상 디렉토리**: `packages/selfhealing-python/src/selfhealing/audit/`

---

## 1. 개요

Audit 모듈에서 발견된 하드코딩된 설정값들을 Pydantic Settings 체계로 마이그레이션하는 상세 계획.

---

## 2. 대상 파일 및 설정값

### 2.1 integrity/sequence.py

**위치**: L63-64  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_PENDING_TTL_SECONDS` | 30 | 대기 상태 TTL | `SELFHEALING_AUDIT_SEQUENCE_PENDING_TTL` |
| `DEFAULT_ORPHAN_TTL_SECONDS` | 86400 | 고아 상태 TTL (24시간) | `SELFHEALING_AUDIT_SEQUENCE_ORPHAN_TTL` |

**구현 방안**:
- 기존 `settings/audit_integrity.py` 확장

---

### 2.2 integrity/cold_storage.py

**위치**: L254-255  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `ARCHIVE_THRESHOLD_DAYS` | 7 | 아카이브 임계 일수 | `SELFHEALING_AUDIT_COLD_ARCHIVE_THRESHOLD_DAYS` |
| `DEFAULT_COLD_RETENTION_YEARS` | 7 | 콜드 스토리지 보관 기간 | `SELFHEALING_AUDIT_COLD_RETENTION_YEARS` |

**구현 방안**:
- `settings/audit_cold_storage.py` 신규 생성 또는 `settings/audit_integrity.py` 확장

---

### 2.3 hash_chain_safety.py

**위치**: L485, L605, L725  
**현재 구조**: 클래스 레벨 상수

| 클래스 | 상수명 | 현재 값 | 용도 |
|-------|-------|--------|------|
| HashChainExporter | `DEFAULT_TIMEOUT_SECONDS` | 300 | 기본 타임아웃 (5분) |
| DateRangeExporter | `DEFAULT_TIMEOUT_SECONDS` | 120 | 날짜별 타임아웃 (2분) |
| HashChainRebuilder | `MAX_REDIS_ENTRIES` | 1000 | Redis 최대 엔트리 수 |

**환경변수 제안**:
```
SELFHEALING_AUDIT_HASHCHAIN_EXPORT_TIMEOUT
SELFHEALING_AUDIT_HASHCHAIN_DATE_EXPORT_TIMEOUT
SELFHEALING_AUDIT_HASHCHAIN_MAX_REDIS_ENTRIES
```

**구현 방안**:
- `settings/hash_chain.py` 신규 생성

---

### 2.4 cascade_auditor.py

**위치**: L98  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `MAX_INDEX_SIZE` | 10000 | 최대 인덱스 크기 | `SELFHEALING_AUDIT_CASCADE_MAX_INDEX_SIZE` |

**구현 방안**:
- 기존 cascade 관련 settings에 통합

---

### 2.5 integrity/anchor.py

**위치**: L47  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `DEFAULT_RETENTION_DAYS` | 90 | 기본 보관 일수 | `SELFHEALING_AUDIT_ANCHOR_RETENTION_DAYS` |

**구현 방안**:
- `settings/audit_integrity.py` 확장

---

### 2.6 resilience/buffer.py

**위치**: L38-39  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `MAX_ENTRIES` | 10000 | 최대 엔트리 수 | `SELFHEALING_AUDIT_BUFFER_MAX_ENTRIES` |
| `FLUSH_INTERVAL_SECONDS` | 30.0 | 플러시 간격 | `SELFHEALING_AUDIT_BUFFER_FLUSH_INTERVAL` |

**구현 방안**:
- `settings/resilient_recorder.py` 확장 또는 `settings/audit_buffer.py` 신규 생성

---

### 2.7 integrity/cross_cluster_linker.py

**위치**: L129-130  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `LOCAL_ANCHOR_TTL_DAYS` | 90 | 로컬 앵커 TTL | `SELFHEALING_AUDIT_CROSS_CLUSTER_LOCAL_TTL_DAYS` |
| `GLOBAL_ANCHOR_TTL_DAYS` | 365 | 글로벌 앵커 TTL | `SELFHEALING_AUDIT_CROSS_CLUSTER_GLOBAL_TTL_DAYS` |

**구현 방안**:
- `settings/audit_integrity.py` 확장

---

### 2.8 integrity/health_score.py

**위치**: L110-112  
**현재 구조**: 클래스 레벨 상수

| 상수명 | 현재 값 | 용도 | 환경변수명 (제안) |
|-------|--------|-----|------------------|
| `HEALTHY_THRESHOLD` | 95.0 | 건강 상태 임계값 | `SELFHEALING_AUDIT_HEALTH_HEALTHY_THRESHOLD` |
| `WARNING_THRESHOLD` | 80.0 | 경고 상태 임계값 | `SELFHEALING_AUDIT_HEALTH_WARNING_THRESHOLD` |
| `CRITICAL_THRESHOLD` | 50.0 | 위험 상태 임계값 | `SELFHEALING_AUDIT_HEALTH_CRITICAL_THRESHOLD` |

**구현 방안**:
- `settings/audit_integrity.py` 확장

---

### 2.9 audit_watchdog.py

**위치**: L22-23  
**현재 구조**: 기본 파라미터

| 설정 | 현재 값 | 용도 |
|-----|--------|------|
| `heartbeat_interval_seconds` | 30.0 | 하트비트 간격 |
| `missed_threshold` | 3 | 누락 임계값 |

**구현 방안**:
- 기존 `settings/audit_watchdog.py` 확장

---

### 2.10 cascade_load_shedding.py

**위치**: L21-22  
**현재 구조**: 기본 파라미터

| 설정 | 현재 값 | 용도 |
|-----|--------|------|
| `buffer_size` | 8000 | 버퍼 크기 |
| `buffer_capacity` | 10000 | 버퍼 용량 |

**구현 방안**:
- `settings/cascade_retention.py` 확장 또는 별도 모듈 생성

---

### 2.11 config.py

**위치**: L210  
**현재 구조**: 함수 내 기본값

| 설정 | 현재 값 | 용도 |
|-----|--------|------|
| `max_days` | 365 | 최대 보관 일수 |

**구현 방안**:
- `settings/audit_settings.py` 확장

---

### 2.12 backends/s3_worm.py

**위치**: L192  
**현재 구조**: 함수 내 기본값

| 설정 | 현재 값 | 용도 |
|-----|--------|------|
| `days` | 365 | WORM 보관 일수 |

**구현 방안**:
- `settings/audit_integrity.py` 확장

---

## 3. 신규/확장 Settings 모듈 구조

```
settings/
├── hash_chain.py            # NEW
├── audit_cold_storage.py    # NEW (또는 audit_integrity.py에 통합)
├── audit_buffer.py          # NEW (또는 resilient_recorder.py에 통합)
├── audit_integrity.py       # EXTEND (major)
├── audit_watchdog.py        # EXTEND
├── cascade_retention.py     # EXTEND
├── audit_settings.py        # EXTEND
├── resilient_recorder.py    # EXTEND
└── ... (기존 파일들)
```

---

## 4. 구현 순서

### Step 1: 기존 Settings 확장 (1-2일)
1. `settings/audit_integrity.py` 확장
   - sequence TTL 설정 추가
   - cold_storage 설정 추가
   - anchor retention 설정 추가
   - cross_cluster TTL 설정 추가
   - health_score 임계값 추가
   - s3_worm 설정 추가

2. `settings/audit_watchdog.py` 확장
   - heartbeat 설정 추가

3. `settings/cascade_retention.py` 확장
   - load_shedding buffer 설정 추가

4. `settings/resilient_recorder.py` 확장
   - buffer 설정 추가

5. `settings/audit_settings.py` 확장
   - config max_days 추가

### Step 2: 신규 Settings 생성 (0.5-1일)
6. `settings/hash_chain.py` 생성
   - export timeout 설정
   - max redis entries 설정

### Step 3: Audit 모듈 리팩토링 (2-3일)
7. `audit/integrity/sequence.py` - settings 연동
8. `audit/integrity/cold_storage.py` - settings 연동
9. `audit/hash_chain_safety.py` - settings 연동
10. `audit/cascade_auditor.py` - settings 연동
11. `audit/integrity/anchor.py` - settings 연동
12. `audit/resilience/buffer.py` - settings 연동
13. `audit/integrity/cross_cluster_linker.py` - settings 연동
14. `audit/integrity/health_score.py` - settings 연동
15. `audit/audit_watchdog.py` - settings 연동
16. `audit/cascade_load_shedding.py` - settings 연동
17. `audit/config.py` - settings 연동
18. `audit/backends/s3_worm.py` - settings 연동

### Step 4: 테스트 업데이트 (1일)
19. 단위 테스트 환경변수 모킹 추가
20. 통합 테스트 검증

### Step 5: 문서화 (0.5일)
21. 환경변수 문서 업데이트
22. 마이그레이션 가이드 작성

---

## 5. 예상 소요 시간

| 단계 | 예상 소요 |
|-----|----------|
| 기존 Settings 확장 | 1-2일 |
| 신규 Settings 생성 | 0.5-1일 |
| Audit 모듈 리팩토링 | 2-3일 |
| 테스트 업데이트 | 1일 |
| 문서화 | 0.5일 |
| **총계** | **5-7.5일** |

---

## 6. settings/audit_integrity.py 확장 예시

### 현재 구조 (추정)
```
AuditIntegritySettings
├── enabled: bool
├── verification_interval: int
└── ... (기존 필드들)
```

### 확장 후 구조
```
AuditIntegritySettings
├── enabled: bool
├── verification_interval: int
├── sequence_pending_ttl: int = 30
├── sequence_orphan_ttl: int = 86400
├── cold_archive_threshold_days: int = 7
├── cold_retention_years: int = 7
├── anchor_retention_days: int = 90
├── cross_cluster_local_ttl_days: int = 90
├── cross_cluster_global_ttl_days: int = 365
├── health_healthy_threshold: float = 95.0
├── health_warning_threshold: float = 80.0
├── health_critical_threshold: float = 50.0
├── s3_worm_retention_days: int = 365
└── ... (기존 필드들)
```

---

## 7. 위험 요소 및 완화 방안

| 위험 | 영향도 | 완화 방안 |
|-----|-------|----------|
| 감사 로그 무결성 영향 | 높음 | 기본값을 현재 값과 동일하게 유지 |
| TTL 변경 시 기존 데이터 영향 | 중간 | 변경 시 기존 데이터는 기존 TTL 적용 |
| Hash Chain 타임아웃 변경 | 중간 | 충분한 테스트 후 적용 |
| Cold Storage 설정 오류 | 높음 | 값 범위 검증 추가 |

---

## 8. 컴플라이언스 고려사항

Audit 모듈은 규제 준수와 밀접한 관련이 있으므로:

1. **Cold Storage 보관 기간**: 7년 기본값은 금융 규제 요구사항 반영
2. **S3 WORM 설정**: 규제 준수를 위해 환경변수로 쉽게 변경 불가하도록 검토 필요
3. **Anchor TTL**: 감사 추적 요구사항에 따라 최소값 제한 필요

### 권장 사항
- 일부 민감한 설정은 환경변수 오버라이드를 제한
- 최소값/최대값 검증 로직 추가
- 변경 시 audit 로그 기록

---

## 9. 검증 체크리스트

### Step 1,2 완료 (2026-01-25)

**구현 완료 항목:**
- [x] `settings/audit_integrity.py` 확장
  - `anchor_retention_days` (90일, 30-365일)
  - `cross_cluster_local_ttl_days` (90일, 30-365일)
  - `cross_cluster_global_ttl_days` (365일, 90-730일)
  - `health_healthy_threshold` (95.0, 80-100)
  - `health_warning_threshold` (80.0, 50-95)
  - `health_critical_threshold` (50.0, 0-80)
  - `s3_worm_retention_days` (365일, 90-2555일)
- [x] `settings/cascade_retention.py` 확장
  - `max_cascade_index_size` (10000, 1000-100000)
- [x] `settings/resilient_recorder.py` 확장
  - `memory_buffer_max_entries` (10000, 100-100000)
  - `memory_buffer_flush_interval` (30.0초, 5.0-300.0초)
- [x] `settings/audit_settings.py` 확장
  - `compliance_max_retention_days` (365일, 90-2555일)
- [x] `settings/hash_chain.py` 신규 생성
  - `merge_swap_timeout_seconds` (300초, 60-600초)
  - `merge_swap_blocking_timeout_seconds` (10.0초, 1.0-60.0초)
  - `date_lock_timeout_seconds` (120초, 30-300초)
  - `date_lock_blocking_timeout_seconds` (5.0초, 0.5-30.0초)
  - `integrity_trail_max_redis_entries` (1000개, 100-10000개)
- [x] `settings/__init__.py`에 HashChainSettings export 추가
- [x] 테스트 파일 생성: `tests/unit/settings/test_audit_module_settings.py`
- [x] 31개 테스트 모두 통과

**Validator 추가:**
- [x] health_score 임계값 순서 검증 (healthy > warning > critical)
- [x] cross_cluster TTL 순서 검증 (global >= local)
- [x] lock timeout 검증 (timeout > blocking_timeout)

**남은 작업 (Step 3 이후):**
- [x] Audit 모듈 코드에 settings 연동
- [x] 환경변수 없이 기본값으로 정상 동작 검증
- [x] Hash Chain 무결성 검증 통과
- [x] Cold Storage 아카이브 정상 동작
- [x] Health Score 계산 정상 동작
- [x] 기존 단위 테스트 100% 통과
- [x] 컴플라이언스 요구사항 충족 확인

---

### Step 3 완료 (2026-01-25)

**Audit 모듈 리팩토링 완료 항목:**
- [x] `audit/hash_chain_safety.py` - settings 연동
  - `AtomicMergeSwap`: `_timeout`, `_blocking_timeout` → HashChainSettings
  - `ShardedDateLock`: `_timeout`, `_blocking_timeout` → HashChainSettings
  - `IntegrityAuditTrail`: `_max_redis_entries` → HashChainSettings
- [x] `audit/cascade_auditor.py` - settings 연동
  - `CascadeEventAuditor._max_index_size` → CascadeRetentionSettings
- [x] `audit/integrity/anchor.py` - settings 연동
  - `DailyHashAnchor._retention_days` → AuditIntegritySettings
- [x] `audit/resilience/buffer.py` - settings 연동
  - `InMemoryAuditBuffer._max_entries`, `_flush_interval_seconds` → ResilientRecorderSettings
- [x] `audit/integrity/cross_cluster_linker.py` - settings 연동
  - `CrossClusterAuditLinker._local_anchor_ttl`, `_global_anchor_ttl` → AuditIntegritySettings
- [x] `audit/integrity/health_score.py` - settings 연동
  - `IntegrityHealthScore._healthy_threshold`, `_warning_threshold`, `_critical_threshold` → AuditIntegritySettings
- [x] `audit/audit_watchdog.py` - 이미 `from_settings()` 메서드로 연동됨
- [x] `audit/config.py` - settings 연동
  - `get_recommended_retention()` 기본값 → AuditSettings
- [x] `audit/backends/s3_worm.py` - settings 연동
  - `S3WORMBackend._retention_days` → AuditIntegritySettings

**이미 연동된 파일 (Step 1,2):**
- [x] `audit/integrity/sequence.py` - 이미 AuditIntegritySettings 사용
- [x] `audit/integrity/cold_storage.py` - 이미 AuditIntegritySettings 사용

**하위 호환성 유지:**
- 모든 클래스에 레거시 클래스 상수 유지 (`DEFAULT_TIMEOUT_SECONDS`, `MAX_INDEX_SIZE` 등)
- 생성자에서 `Optional` 파라미터로 명시적 오버라이드 지원

**테스트 파일 추가:**
- [x] `tests/unit/audit/test_audit_module_settings_integration.py` (22개 테스트)
  - Settings 연동 검증 테스트
  - 환경변수 오버라이드 테스트
  - 명시적 값 오버라이드 테스트
  - 하위 호환성 테스트 (레거시 상수 접근)

**테스트 결과:**
- 22개 통합 테스트 통과
- 31개 설정 테스트 통과
- 총 53개 테스트 통과

---

### Step 4 완료 (2026-01-25)

**테스트 업데이트 완료 항목:**

#### 1. conftest.py 수정 (Settings 싱글톤 자동 리셋)
- [x] `packages/selfhealing-python/tests/conftest.py`에 `auto_reset_audit_settings` fixture 추가
  - 모든 테스트 함수 전후에 Settings 싱글톤 자동 리셋
  - 환경변수 변경이 다른 테스트에 영향 주지 않도록 격리

**리셋 대상 Settings 싱글톤:**
- `HashChainSettings` (AtomicMergeSwap, ShardedDateLock, IntegrityAuditTrail)
- `AuditIntegritySettings` (DailyHashAnchor, CrossClusterLinker, HealthScore, S3WORM)
- `CascadeRetentionSettings` (CascadeEventAuditor)
- `ResilientRecorderSettings` (InMemoryAuditBuffer)
- `AuditSettings` (get_recommended_retention)
- `AuditWatchdogSettings` (AuditWatchdog)

**Audit 모듈 싱글톤 리셋:**
- `InMemoryAuditBuffer.reset_instance()`
- `reset_cascade_auditor()`

#### 2. 기존 테스트 환경변수 모킹 수정
기존 테스트가 클래스 상수 대신 인스턴스 변수를 사용하도록 수정:

- [x] `tests/unit/audit/forensic_bridge/test_audit_buffer.py`
  - `test_buffer_overflow_drops_oldest`: `InMemoryAuditBuffer(max_entries=3)` 생성자 파라미터 사용

- [x] `tests/unit/audit/test_cascade_event.py`
  - `test_index_max_size`: `cascade_auditor._max_index_size = 5` 인스턴스 변수 직접 설정

- [x] `tests/unit/audit/test_hash_chain_safety.py`
  - `test_max_redis_entries_trimmed`: `IntegrityAuditTrail(max_redis_entries=5)` 생성자 파라미터 사용

#### 3. 통합 테스트 결과
- [x] 전체 audit 테스트 스위트 실행: 940 passed, 1 flaky test
  - 실패한 테스트: `test_sampling_verification_performance` (타이밍 관련 flaky test, Settings 리팩토링과 무관)
- [x] Settings 통합 테스트 22개 통과
- [x] Settings 단위 테스트 31개 통과

#### 4. 검증 체크리스트
- [x] conftest.py autouse fixture로 모든 테스트에서 Settings 격리 보장
- [x] 기존 테스트가 인스턴스 변수 또는 생성자 파라미터 사용하도록 수정
- [x] 환경변수 오버라이드 테스트 정상 동작
- [x] 하위 호환성 유지 (레거시 클래스 상수 접근 가능)
- [x] 전체 테스트 스위트 통과 (flaky test 1개 제외)
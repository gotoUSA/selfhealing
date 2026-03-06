# 305. Audit Checkpoint 통합 — 3중 구현 제거

> **Status**: Refactor
> **Severity**: P0 (CRITICAL)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/audit/checkpoint_manager.py` — deprecate
> - `packages/selfhealing-python/src/selfhealing/audit/checkpoint_strategy.py` — canonical
> - `packages/selfhealing-python/src/selfhealing/audit/kafka_checkpoint.py` — deprecate
> **References**:
> - `audit/continuous_audit.py` — 이미 Strategy 사용 (이관 완료)
> - `audit/async_audit_lifecycle.py` — 레거시 CheckpointManager 사용 (미이관)
> - `audit/sync_worker.py` — 혼합 모드 (Strategy primary, Manager fallback)

---

## 1. 현황 및 문제

Audit 모듈에 **동일한 목적**(WAL 시퀀스 번호 영속화)의 Checkpoint 구현이 3개 공존한다.

| 구현체 | 파일 | 라인 수 | 백엔드 | 네임스페이스 |
|--------|------|---------|--------|------------|
| `CheckpointManager` | `checkpoint_manager.py` | 381 | File only | 단일 |
| `CheckpointStorageStrategy` + 4 impl | `checkpoint_strategy.py` | 1,351 | File/Redis/Kafka+Redis/Composite | 멀티 |
| `KafkaCheckpointManager` | `kafka_checkpoint.py` | 397 | Redis + File | 멀티 |

**총 중복 코드**: ~778줄 (CheckpointManager 381 + KafkaCheckpointManager 397)

---

## 2. 메서드 단위 중복 분석

### 2.1 CheckpointManager vs FileCheckpointStorage

| CheckpointManager 메서드 | FileCheckpointStorage 메서드 | 중복률 |
|--------------------------|------------------------------|--------|
| `save(last_sequence)` :183 | `save(namespace, data)` :287 | 90% |
| `load()` :260 | `load(namespace)` :317 | 90% |
| `load_full()` :292 | `load(namespace)` :317 | 95% |
| `delete()` :316 | `delete(namespace)` :345 | 95% |
| `exists()` :312 | `exists(namespace)` :355 | 100% |
| `get_age_seconds()` :330 | (없음) | 0% |
| Atomic write (tmp+rename) :219 | Atomic write (tmp+replace) :303 | 100% |
| Directory fsync :223 | fsync :308 | 100% |

### 2.2 KafkaCheckpointManager vs KafkaRedisCheckpointStorage

| KafkaCheckpointManager | KafkaRedisCheckpointStorage | 중복률 |
|------------------------|----------------------------|--------|
| `_save_to_redis()` :221 | `_write_to_redis()` | 100% |
| `_get_from_redis()` :210 | `load()` Redis 분기 | 95% |
| `_save_to_file()` :245 | `_file_backup.save()` | 100% |
| `_get_from_file()` :229 | `_file_backup.load()` | 95% |
| `delete_checkpoint()` :268 | `delete()` :869 | 90% |
| `get_wal_sequence()` :294 | `get_wal_sequence()` (상속) | 100% |

---

## 3. 데이터 모델 불일치

```
CheckpointData                UnifiedCheckpointData         KafkaCheckpointData
  last_sequence: int            wal_sequence: int             wal_sequence: int
  timestamp: float (epoch)      timestamp: str (ISO 8601)     timestamp: str (ISO 8601)
  version: int                  version: int                  version: int
  (없음)                        kafka_topic: str?             kafka_topic: str (필수)
  (없음)                        kafka_partition: int?          kafka_partition: int (필수)
  (없음)                        kafka_offset: int?             kafka_offset: int (필수)
  (없음)                        checksum: str?                 checksum: str (필수)
```

`UnifiedCheckpointData`가 모든 필드를 포괄하며 `from_legacy_checkpoint_data()` 변환기도 보유.

**레거시 변환 정책**: `UnifiedCheckpointData`의 Kafka 확장 필드(`kafka_topic`, `kafka_partition`, `kafka_offset`, `checksum`)는 모두 `Optional[str | int] = None`이므로, 레거시 `CheckpointData` 변환 시 해당 필드는 `None`으로 채워진다. `from_legacy_checkpoint_data()` (:106-121)가 `last_sequence → wal_sequence` 리네이밍과 `float timestamp → ISO 8601` 변환을 자동 처리한다.

---

## 4. 안전성 격차 (프로덕션 리스크)

| 검증 항목 | CheckpointManager | FileCheckpointStorage | KafkaCheckpointManager |
|----------|-------------------|-----------------------|------------------------|
| 파일 락킹 (cross-process) | msvcrt/fcntl | **누락** | **누락** |
| 스레드 락 범위 | 전체 메서드 | 전체 메서드 | cache만 |
| Checksum 검증 | 없음 | 없음 | 없음 |
| 오류 시 동작 | Silent (returns 0) | Silent (returns None) | Silent (returns None) |

**FileCheckpointStorage에 파일 락킹 누락** — 멀티 프로세스(gunicorn worker) 환경에서 동시 save() 호출 시 체크포인트 파일 손상 가능.

---

## 5. 호출자 마이그레이션 현황

| 호출자 | 현재 사용 | 마이그레이션 필요 |
|--------|----------|-----------------|
| `continuous_audit.py` | `CheckpointStorageStrategy` | 완료 |
| `async_audit_lifecycle.py` :191, :372 | `get_checkpoint_manager()` | **필요** |
| `sync_worker.py` :575-628 | 혼합 (Strategy → Manager 폴백) | **필요** (폴백 제거) |
| `kafka_checkpoint.py` :313 | `KafkaCheckpointManager` 직접 | **필요** (Strategy 전환) |

---

## 6. 구현 결정 사항

이 리팩토링에서 결정이 필요한 항목과 그 결론을 기록한다.

### 6.1 락킹 전략: 파일 락 전용 (Redis 분산 락 미적용)

`FileCheckpointStorage`는 "순수 파이썬, 외부 의존성 없음(소규모)" 설계 계약을 가진다 (`checkpoint_strategy.py` 모듈 docstring :1-8). 이 전략에 Redis 의존성을 주입하면 SRP 위반이며, 멀티 노드 환경에서의 동시성 제어는 `RedisCheckpointStorage`(내장 `DistributedRecoveryLock` :431-457) 또는 `CompositeCheckpointStorage`의 책임이다.

따라서 Phase 1에서는 `checkpoint_manager.py`의 `lock_file()/unlock_file()` (:49-70)만 이식하고, K8s 멀티 파드 환경에서 `FileCheckpointStorage`가 오용되지 않도록 DI/설정 계층에서 storage_type validation을 추가한다.

### 6.2 에러 핸들링 정책: 계층별 차등 적용

체크포인트 I/O 에러 처리는 **저장 계층**과 **호출자 계층**을 구분한다.

**저장 계층** (`CheckpointStorageStrategy` 구현체):
- `save()`: 실패 시 `CheckpointError` raise (현행 유지, `FileCheckpointStorage` :310-315, `RedisCheckpointStorage` :423-429)
- `load()`: 실패 시 `None` 반환 + warning 로그 (현행 유지, :334-339)
- `CompositeCheckpointStorage`: 모든 tier 실패 시에만 `CheckpointError` raise (:1020)

**호출자 계층** (`sync_worker`, `async_audit_lifecycle`):
- 체크포인트 실패로 워커가 중단되면 안 되므로 try/except로 예외를 삼킨다 (현행 유지)
- 단, **관측성 보강**을 위해 Prometheus 카운터(`checkpoint_save_failures_total`, `checkpoint_load_failures_total`)를 추가하여 실패 빈도를 모니터링한다

이 정책은 "워커 가용성 우선 + 메트릭 기반 관측성"으로 요약된다. 새로운 커스텀 예외(`CheckpointWriteError` 등)를 도입하지 않으며, 기존 `CheckpointError`와 호출자의 try/except 체인을 그대로 활용한다.

### 6.3 레거시 파일 마이그레이션: Lazy + Write-back

기존 `checkpoint.json`(단일 네임스페이스)을 `checkpoint.default.json`으로 변환하는 시점은 **최초 `load()` 호출 시 지연 평가(Lazy Evaluation)**로 처리한다.

- Startup 일괄 마이그레이션 불필요 (파일 1개뿐이라 이점 없음, 불필요한 I/O 부하 방지)
- `load()` 내부에서 변환 후 즉시 `save()`로 새 형식 저장 (Write-back 패턴)
- `from_legacy_checkpoint_data()`가 idempotent하므로 반복 호출 안전
- **rename은 `__init__()`이 아닌 `load()` 내부에서 파일 락을 획득한 후 수행** — gunicorn 멀티 워커가 동시에 `__init__()`을 실행할 때 발생하는 Race Condition 방지

### 6.4 Fallback 제거 후 워커 정책: 경고 로그 + 계속 실행

`sync_worker.py`에서 레거시 폴백 분기 제거 후, Strategy가 `None`이거나 실패할 경우 워커는 **경고 로그를 남기고 계속 실행**한다. 체크포인트 실패는 데이터 유실이 아닌 중복 처리 가능성만 증가시키며, 워커 정지 시 전체 audit pipeline 중단의 가용성 피해가 더 크기 때문이다.

### 6.5 동시성 테스트: 스레드 + 멀티프로세스 2단계

| 단계 | 범위 | 위치 | 필수 여부 |
|------|------|------|----------|
| 스레드 테스트 | 20+ 스레드 동시 save/load | `packages/selfhealing-python/tests/unit/` | **필수** |
| 멀티프로세스 테스트 | 2-3개 프로세스, 10-20회 write/read 교차 | `tests/` (통합 테스트) | **필수** |

OS 레벨 파일 락(`fcntl/msvcrt`)의 실효성은 멀티프로세스 테스트 없이는 검증 불가하며, gunicorn prefork 환경을 시뮬레이션해야 한다.

---

## 7. 리팩터링 계획

### Phase 1: FileCheckpointStorage 안전성 보강

```
checkpoint_strategy.py FileCheckpointStorage에:
  1. checkpoint_manager.py의 lock_file()/unlock_file() (L49-70) 이식
  2. save() 메서드에 파일 락킹 적용
  3. get_age_seconds() 추가 (CheckpointManager 고유 메서드)
  4. save()/load() 실패 시 Prometheus 카운터 증가 로직 추가

settings/ 에 K8s 환경 storage_type validation 추가:
  SELFHEALING_CHECKPOINT_STORAGE=file 일 때 K8s 환경 감지 시 경고 로그 출력
  멀티 파드 환경에서는 redis 또는 composite 사용을 강제하는 가이드 제공
```

### Phase 1.5: 레거시 파일 마이그레이션

```
checkpoint_strategy.py FileCheckpointStorage.load()에:
  1. 파일 락 획득 후 레거시 checkpoint.json 존재 확인
  2. checkpoint.json → checkpoint.default.json rename (double-check after lock)
  3. 레거시 형식 로드 → UnifiedCheckpointData 변환 → 새 형식으로 write-back
  4. 원자적 쓰기(tmp → fsync → rename) 보장으로 중단 시에도 원본 무결
```

### Phase 2: 호출자 마이그레이션

**선행 점검 (필수)**: `sync_worker`와 `async_audit_lifecycle`의 audit 이벤트 처리 로직이 **멱등성(Idempotency)**을 갖추고 있는지 확인한다. 체크포인트 실패 후 재시작 시 동일 WAL 시퀀스가 재처리되므로, 중복 시퀀스를 감지하고 skip하는 로직이 존재해야 한다. 멱등성이 미확보된 경우 이 Phase 전에 해당 로직을 선행 구현한다.

```
async_audit_lifecycle.py:
  get_checkpoint_manager() → get_default_checkpoint_strategy()
  .save(last_sequence) → .save("default", UnifiedCheckpointData(...))
  .load() → .get_wal_sequence("default")

sync_worker.py:
  레거시 폴백 분기 (L596-608) 제거
  CheckpointStorageStrategy만 사용
  Strategy None/실패 시 → 경고 로그 후 워커 계속 실행
```

### Phase 3: KafkaCheckpointManager 제거

```
kafka_checkpoint.py:
  sync_wal_to_kafka_with_checkpoint() → KafkaRedisCheckpointStorage 사용으로 전환
  KafkaCheckpointManager 클래스 삭제
  KafkaCheckpointData → UnifiedCheckpointData로 통일
```

### Phase 4: CheckpointManager deprecate

```
checkpoint_manager.py:
  CheckpointManager 클래스에 @deprecated 데코레이터 추가
  get_checkpoint_manager()에 deprecation warning 추가
  audit/__init__.py에서 export 유지 (하위호환)
  다음 메이저 버전에서 완전 삭제
```

---

## 8. 검증 기준

- [ ] `FileCheckpointStorage.save()`에 cross-process 파일 락킹 적용
- [ ] `FileCheckpointStorage` save/load 실패 시 Prometheus 카운터 증가
- [ ] K8s 환경에서 `SELFHEALING_CHECKPOINT_STORAGE=file` 시 경고 로그 출력
- [ ] 레거시 `checkpoint.json` → `checkpoint.default.json` 자동 마이그레이션 (load() 내 락 보호)
- [ ] 레거시 마이그레이션 후 write-back으로 새 형식 저장 확인
- [ ] `async_audit_lifecycle.py`가 `CheckpointStorageStrategy` 사용
- [ ] `sync_worker.py`에서 레거시 폴백 분기 제거
- [ ] `sync_worker` / `async_audit_lifecycle` audit 처리 멱등성 확인 (선행 점검)
- [ ] `KafkaCheckpointManager` 사용처 0건
- [ ] `CheckpointManager` 사용처 0건 (deprecated export만 유지)
- [ ] 단위 테스트: `FileCheckpointStorage` 멀티스레드 동시 접근 (20+ threads)
- [ ] 통합 테스트: `FileCheckpointStorage` 멀티프로세스 I/O 경합 (2-3 processes, subprocess.Popen)

---

## 9. 영향 범위

| 영향 대상 | 변경 유형 |
|----------|----------|
| `checkpoint_manager.py` | deprecate (삭제 예정) |
| `checkpoint_strategy.py` | 파일 락킹 추가, 레거시 마이그레이션, Prometheus 메트릭 |
| `kafka_checkpoint.py` | 삭제 (Strategy로 통합) |
| `async_audit_lifecycle.py` | import 변경 |
| `sync_worker.py` | 폴백 분기 제거 |
| `audit/__init__.py` | deprecated re-export 유지 |
| `settings/` | K8s 환경 checkpoint storage validation 추가 |
| 테스트 | 동시성 테스트 추가 (스레드 + 멀티프로세스) |

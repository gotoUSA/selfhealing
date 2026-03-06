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

## 6. 리팩터링 계획

### Phase 1: FileCheckpointStorage 안전성 보강

```
checkpoint_strategy.py FileCheckpointStorage에:
  1. checkpoint_manager.py의 lock_file()/unlock_file() (L49-70) 이식
  2. save() 메서드에 파일 락킹 적용
  3. get_age_seconds() 추가 (CheckpointManager 고유 메서드)
```

### Phase 2: 호출자 마이그레이션

```
async_audit_lifecycle.py:
  get_checkpoint_manager() → get_default_checkpoint_strategy()
  .save(last_sequence) → .save("default", UnifiedCheckpointData(...))
  .load() → .get_wal_sequence("default")

sync_worker.py:
  레거시 폴백 분기 (L596-608) 제거
  CheckpointStorageStrategy만 사용
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

## 7. 검증 기준

- [ ] `FileCheckpointStorage.save()`에 cross-process 파일 락킹 적용
- [ ] `async_audit_lifecycle.py`가 `CheckpointStorageStrategy` 사용
- [ ] `sync_worker.py`에서 레거시 폴백 분기 제거
- [ ] `KafkaCheckpointManager` 사용처 0건
- [ ] `CheckpointManager` 사용처 0건 (deprecated export만 유지)
- [ ] 기존 체크포인트 파일 → `UnifiedCheckpointData` 자동 마이그레이션 확인
- [ ] 단위 테스트: `FileCheckpointStorage` 동시 접근 테스트 추가

---

## 8. 영향 범위

| 영향 대상 | 변경 유형 |
|----------|----------|
| `checkpoint_manager.py` | deprecate (삭제 예정) |
| `checkpoint_strategy.py` | 파일 락킹 추가 |
| `kafka_checkpoint.py` | 삭제 (Strategy로 통합) |
| `async_audit_lifecycle.py` | import 변경 |
| `sync_worker.py` | 폴백 분기 제거 |
| `audit/__init__.py` | deprecated re-export 유지 |
| 테스트 | 동시성 테스트 추가 |

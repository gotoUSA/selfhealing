# 306. Audit WAL 통합 — 4중 구현 공통 기반 추출

> **Status**: Refactor
> **Severity**: P1 (HIGH)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/audit/wal/` — WriteAheadLog (canonical)
> - `packages/selfhealing-python/src/selfhealing/audit/hash_chain_safety.py` — HashChainWAL
> - `packages/selfhealing-python/src/selfhealing/audit/graceful_degradation/wal_recovery.py` — HashChainWALRecovery
> - `packages/selfhealing-python/src/selfhealing/audit/cascade_auditor/_wal_recovery.py` — WALRecoveryMixin
> **References**:
> - `audit/wal/_models.py` — WALEntry, WALConfig, WALStats
> - `audit/wal/_writer.py` — WALWriterMixin
> - `audit/wal/_reader.py` — WALReaderMixin
> - `audit/wal/_disk_manager.py` — WALDiskManagerMixin
> - `audit/wal/_serialization.py` — 직렬화 유틸리티

---

## 1. 현황 및 문제

Audit 모듈에 **동일 목적**(장애 복구를 위한 선행 기록)의 WAL이 4개 독립 구현되어 있다.

| 구현체 | 파일 | 포맷 | Checksum | 커밋 마커 | 스레드 락 |
|--------|------|------|---------|----------|----------|
| `WriteAheadLog` | `wal/` (6 파일) | Binary (CRC32) | CRC32 | 없음 | RLock |
| `HashChainWAL` | `hash_chain_safety.py` :223-491 | JSONL | 없음 | COMMITTED/ABORTED | RLock |
| `HashChainWALRecovery` | `graceful_degradation/wal_recovery.py` :24-434 | JSONL (날짜별) | 없음 | COMMIT 마커 | RLock |
| `WALRecoveryMixin` | `cascade_auditor/_wal_recovery.py` :44-336 | JSONL | 없음 | 없음 | **없음** |

**공통 base class 없음**, 각각 독립적으로 직렬화/복구/정리 로직 구현.

---

## 2. 코드 패턴 중복 분석

### 2.1 JSONL 쓰기 — 3곳에서 동일 패턴

```python
# HashChainWAL (hash_chain_safety.py:297)
self._wal_handle.write(json.dumps(entry) + "\n")
self._wal_handle.flush()
os.fsync(self._wal_handle.fileno())

# HashChainWALRecovery (wal_recovery.py:129)
self._wal_handle.write(json.dumps(entry) + "\n")
self._wal_handle.flush()
os.fsync(self._wal_handle.fileno())

# WALRecoveryMixin (_wal_recovery.py:28)
f.write(json.dumps(data) + "\n")
f.flush()
os.fsync(f.fileno())
```

### 2.2 파일 열기 — 3곳에서 `_ensure_file_open()` 구현

```python
# 공통 패턴 (3곳):
def _ensure_file_open(self):
    if self._wal_handle is None:
        self._wal_path.parent.mkdir(parents=True, exist_ok=True)
        self._wal_handle = open(self._wal_path, "a", encoding="utf-8")
```

### 2.3 JSONL 복구 파싱 — 4곳에서 동일 루프

```python
# 공통 패턴 (4곳):
for line in f:
    try:
        entry = json.loads(line.strip())
        # 엔트리 처리
    except json.JSONDecodeError:
        continue  # 손상된 줄 건너뜀
```

### 2.4 커밋 시퀀스 추적 — 2곳에서 동일 로직

```python
# HashChainWAL:382 + HashChainWALRecovery:200
committed_seqs = set()
for entry in file:
    if status == "COMMITTED":
        committed_seqs.add(seq)
# 필터: seq not in committed_seqs
```

---

## 3. 엔트리 모델 비교

| 필드 | WALEntry | HashChainSafetyWALEntry | HashChainRecoveryWALEntry | Cascade (dict) |
|------|---------|-------------------------|---------------------------|----------------|
| sequence | int | int | int | 없음 |
| timestamp | float | str | str | 없음 |
| data | dict | dict (`entry_data`) | dict (`entry_data`) | dict (전체) |
| checksum | str (CRC32) | 없음 | 없음 | 없음 |
| operation | 없음 | str (WRITE/ANCHOR/RECONCILE) | str (add_integrity/commit/abort) | 없음 |
| status | 없음 | str (PENDING/COMMITTED/ABORTED) | bool (`committed`) | 없음 |
| pod_id | 없음 | 없음 | str | 없음 |

---

## 4. 복구 전략 비교

| 구현체 | 중복 방지 | 재생 대상 | 정리 방식 |
|--------|----------|----------|----------|
| WriteAheadLog | seq 비교 | 없음 (감사 전용) | max_seq 기반 파일 삭제 :255 |
| HashChainWAL | 커밋 마커 (COMMITTED/ABORTED) | 없음 (앱 특화) | compact(seq 기반 재작성) :436 |
| HashChainWALRecovery | 2-level (Redis seq + IdempotencyService) | Redis pipeline | 파일명 날짜 기반 삭제 :393 |
| WALRecoveryMixin | 없음 (namespace 필터링) | CascadeEvent → Redis | namespace 제거 후 재작성 :275 |

---

## 5. 리팩터링 계획

### Phase 1: 공통 JSONL 유틸리티 추출

`audit/wal/_jsonl.py` 신규 생성:

```python
class JSONLWriter:
    """JSONL WAL 쓰기 공통 유틸리티"""
    def __init__(self, file_path: Path):
        self._path = file_path
        self._handle: IO | None = None
        self._lock = threading.RLock()

    def ensure_open(self) -> None: ...
    def append(self, entry: dict) -> None: ...     # write + flush + fsync
    def close(self) -> None: ...

class JSONLReader:
    """JSONL WAL 읽기 공통 유틸리티"""
    @staticmethod
    def iter_entries(file_path: Path) -> Iterator[dict]: ...
    @staticmethod
    def parse_with_committed_filter(
        file_path: Path,
        commit_field: str = "status",
        commit_value: str = "COMMITTED",
    ) -> tuple[list[dict], set[int]]: ...
```

### Phase 2: 기존 WAL에서 공통 유틸리티 사용

```
HashChainWAL:
  write_pending() → JSONLWriter.append() 사용
  get_uncommitted_entries() → JSONLReader.parse_with_committed_filter() 사용
  _ensure_file_open() 삭제 → JSONLWriter.ensure_open() 위임

HashChainWALRecovery:
  _write_to_wal_file() → JSONLWriter.append() 사용
  _recover_from_wal_file() → JSONLReader.iter_entries() 사용
  _ensure_wal_file_open() 삭제

WALRecoveryMixin:
  _append_to_wal() 모듈 함수 → JSONLWriter.append() 사용
  recover_from_local_wal() → JSONLReader.iter_entries() 사용
  _remove_namespace_from_wal() → JSONLReader + JSONLWriter 조합
  스레드 안전성 추가 (현재 락 없음)
```

### Phase 3: 정리 로직 통합

```
audit/wal/_cleanup.py 신규:
  def cleanup_by_sequence(file_path, keep_after_seq) → int  # compact
  def cleanup_by_age(directory, pattern, max_age_days) → int
  def cleanup_by_namespace(file_path, namespace) → int

기존 정리 메서드:
  HashChainWAL.compact() → cleanup_by_sequence() 위임
  HashChainWALRecovery.cleanup_old_wal_files() → cleanup_by_age() 위임
  WALRecoveryMixin._remove_namespace_from_wal() → cleanup_by_namespace() 위임
```

### Phase 4: 커밋 마커 프로토콜 통일

```
HashChainWAL과 HashChainWALRecovery의 커밋 추적을 통일:
  - 커밋 마커 형식: {"_marker": "COMMIT", "wal_sequence": int, "timestamp": str}
  - 공통 파서: JSONLReader.parse_with_committed_filter()에서 처리
```

---

## 6. 변경하지 않는 것

- **WriteAheadLog (Binary 포맷)**: Binary CRC32 포맷은 JSONL과 근본적으로 다르므로 통합 대상이 아님. 자체 mixin 구조를 유지.
- **도메인 특화 복구 로직**: HashChainWALRecovery의 IdempotencyService 연동, WALRecoveryMixin의 CascadeEvent 재구성 등은 각 도메인 고유 로직이므로 유지.

---

## 7. 검증 기준

- [ ] `JSONLWriter`/`JSONLReader` 신규 생성 및 단위 테스트
- [ ] HashChainWAL이 `JSONLWriter`/`JSONLReader` 사용
- [ ] HashChainWALRecovery가 `JSONLWriter`/`JSONLReader` 사용
- [ ] WALRecoveryMixin이 `JSONLWriter` 사용 + 스레드 락 추가
- [ ] 커밋 마커 형식 통일 확인
- [ ] 정리 유틸리티 통합 확인
- [ ] 기존 단위 테스트 전체 통과

---

## 8. 절감 효과

| 항목 | Before | After |
|------|--------|-------|
| JSONL 쓰기 구현 | 3곳 (각 ~15줄) | 1곳 (`JSONLWriter`) |
| 파일 열기 구현 | 3곳 (각 ~8줄) | 1곳 (`JSONLWriter.ensure_open`) |
| JSONL 파싱 루프 | 4곳 (각 ~20줄) | 1곳 (`JSONLReader.iter_entries`) |
| 커밋 추적 로직 | 2곳 (각 ~30줄) | 1곳 (`JSONLReader.parse_with_committed_filter`) |
| 정리 로직 | 3곳 (각 ~40줄) | 3 유틸리티 함수 |
| **예상 절감** | | **~200줄 중복 제거** |

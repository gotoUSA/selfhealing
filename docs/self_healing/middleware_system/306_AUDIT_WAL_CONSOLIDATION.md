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
    """JSONL WAL 쓰기 공통 유틸리티 (스레드 안전, fsync 정책, 크기 기반 로테이션)"""

    _serialize = staticmethod(json.dumps)  # OCP: 향후 orjson 교체 시 이 1곳만 수정

    def __init__(
        self,
        file_path: Path,
        fsync: bool = True,
        max_size_bytes: int | None = None,
    ):
        self._path = file_path
        self._handle: IO | None = None
        self._lock = threading.RLock()
        self._fsync = fsync
        self._max_size = max_size_bytes        # None이면 로테이션 비활성
        self._current_size: int = 0

    def ensure_open(self) -> None: ...
    def append(self, entry: dict) -> None: ...     # write + flush + 조건부 fsync + 로테이션 체크
    def close(self) -> None: ...
    def _maybe_rotate(self) -> None: ...           # RLock 내부에서 원자적 실행

class JSONLReader:
    """JSONL WAL 읽기 공통 유틸리티 (손상 라인 logged skip + 메트릭)"""

    @staticmethod
    def iter_entries(file_path: Path) -> Iterator[dict]: ...

    @staticmethod
    def parse_with_committed_filter(
        file_path: Path,
        commit_field: str = "status",
        commit_value: str = "COMMITTED",
    ) -> tuple[list[dict], set[int]]: ...
```

#### 5.1.1 Fsync 정책 (2-tier)

JSONLWriter는 `fsync: bool` 파라미터로 2단계 fsync 정책을 지원한다.

| 정책 | 값 | 적용 대상 | 근거 |
|------|-----|----------|------|
| **ALWAYS** | `True` | HashChainWAL, HashChainWALRecovery | 정합성 필수 — 커밋 마커 기반 복구에서 미기록 라인은 데이터 유실 |
| **NONE** | `False` | WALRecoveryMixin (CascadeAuditor) | Redis가 1차 복구 수단, 로컬 WAL은 best-effort fallback |

EVERYSEC (별도 백그라운드 스레드 필요)는 현 시점에서 복잡도 대비 이점이 낮아 도입하지 않는다.
WriteAheadLog의 Group Commit 패턴(`_writer.py:92-146`)이 이미 "배치 fsync"를 해결하고 있다.

> **설계 근거**: `HashChainWAL`은 이미 `sync_on_write: bool` 파라미터를 가지고 있다 (`hash_chain_safety.py:249`).
> WALRecoveryMixin의 `_append_to_wal()`은 현재도 fsync 없이 운영 중이다 (`_wal_recovery.py:40-41`).
> 인터페이스 수준에서 fsync 플래그를 받아두면, 향후 BATCH/EVERYSEC 확장 시 시그니처 변경 없이 내부만 수정하면 된다.

#### 5.1.2 손상 라인 처리 (Logged Skip + 메트릭)

JSONLReader.iter_entries()는 `json.JSONDecodeError` 발생 시 해당 라인을 건너뛰되,
**경고 로그를 남기고 메트릭 카운터를 증가**시킨다 (silent skip 금지).

```python
@staticmethod
def iter_entries(file_path: Path) -> Iterator[dict]:
    with open(file_path, encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "jsonl_reader.corrupted_line_skipped",
                    file=str(file_path),
                    line_no=line_no,
                )
                # Prometheus 메트릭 연동
                try:
                    from selfhealing.metrics.drift_metrics import (
                        record_wal_corrupted_line,
                    )
                    record_wal_corrupted_line()
                except ImportError:
                    pass
                continue
```

> **설계 근거**: JSONL은 라인 단위 독립성이 보장되므로, 한 줄 손상이 다른 줄에 영향을 주지 않는다.
> 처리를 중단하면 복구 실패 → 장애 확대 위험이 있으므로 skip이 안전하다.
> 다만 silent skip은 대량 트래픽에서 손상을 감지할 수 없으므로, `wal_corrupted_lines_total` 메트릭을 통해 모니터링한다.
> 기존 패턴 참조: `_writer.py:80-88`의 `record_wal_entry_written()` 메트릭 훅.

#### 5.1.3 직렬화 래핑 (OCP 준비)

JSONLWriter는 `_serialize` 클래스 변수를 통해 직렬화 함수를 간접 참조한다.

```python
class JSONLWriter:
    _serialize = staticmethod(json.dumps)  # 향후 orjson.dumps로 교체 가능

    def append(self, entry: dict) -> None:
        with self._lock:
            self.ensure_open()
            line = self._serialize(entry, default=str) + "\n"
            # ...
```

> **설계 근거**: 현 시점에서 orjson/ujson 도입은 불필요하다 (WAL 엔트리는 수백 바이트~수 KB, 병목은 fsync I/O).
> 프로젝트 전체가 표준 `json` 사용 중이며, C 확장 의존성은 K8s/Alpine 빌드 복잡도를 높인다.
> 다만 `_serialize`로 래핑해두면 향후 교체 시 **1곳만 수정**하면 된다.

#### 5.1.4 크기 기반 로테이션

JSONLWriter는 `max_size_bytes` 파라미터로 선택적 크기 기반 파일 로테이션을 지원한다.
로테이션 로직은 `append()` 내부의 **RLock 안에서 원자적으로 실행**된다.

```python
def append(self, entry: dict) -> None:
    with self._lock:
        self.ensure_open()
        line = self._serialize(entry, default=str) + "\n"
        self._handle.write(line)
        self._current_size += len(line.encode("utf-8"))
        if self._fsync:
            self._handle.flush()
            os.fsync(self._handle.fileno())
        self._maybe_rotate()  # RLock 내부 — 다른 스레드가 닫힌 핸들에 접근 불가

def _maybe_rotate(self) -> None:
    if self._max_size and self._current_size >= self._max_size:
        self._handle.close()
        rotated = self._path.with_suffix(f".{int(time.time())}.jsonl")
        self._path.rename(rotated)
        self._handle = open(self._path, "a", encoding="utf-8")
        self._current_size = 0
```

적용 매핑:

| 구현체 | max_size_bytes | 근거 |
|--------|---------------|------|
| HashChainWAL | `10 * 1024 * 1024` | 기존 `max_file_size_mb=10` 의도 실현 (미구현이었음) |
| HashChainWALRecovery | `None` (비활성) | 날짜 기반 파일명으로 암묵적 일 단위 로테이션 |
| WALRecoveryMixin | `None` (비활성) | 단일 파일, cleanup_by_namespace로 관리 |

> **설계 근거**: `HashChainWAL`은 `max_file_size_mb` 파라미터를 받지만 실제 로테이션 로직이 구현되어 있지 않다 (`hash_chain_safety.py:257`).
> `WriteAheadLog`의 `sync_and_maybe_rotate()` (`_serialization.py:61`)를 참조하되, JSONL 특화로 단순화한다.

#### 5.1.5 스레드 안전성 — WALRecoveryMixin 개선

WALRecoveryMixin은 현재 스레드 락이 없다 (`_wal_recovery.py:28-41`).
JSONLWriter 내부의 RLock으로 `append()` 단위 스레드 안전성을 확보한다.
상위 레벨 Block Lock은 **불필요** — CascadeAuditor는 트랜잭션 개념 없이 개별 이벤트 단위로 기록하며,
복구(`recover_from_local_wal`)도 startup 시 단일 스레드에서 실행된다.

> **설계 근거**: HashChainWAL은 write_pending → mark_committed의 2-phase 패턴이지만,
> CascadeAuditor는 `_save_to_local_wal()` (`_wal_recovery.py:138`)과
> `_record_dropped_to_wal()` (`_wal_recovery.py:166`) 모두 단일 이벤트 저장이다.
> Block Lock은 오히려 데드락 원인이 되거나 병목만 유발한다.

#### 5.1.6 엔트리 스키마 — 커밋 마커만 TypedDict 강제

JSONLWriter.append()는 `dict[str, Any]`를 그대로 받는다.
일반 데이터 엔트리의 스키마는 각 도메인(HashChain, Cascade 등)의 책임이다.
**커밋 마커만** Phase 4에서 TypedDict로 통일한다.

```python
class CommitMarker(TypedDict):
    _marker: Literal["COMMIT"]
    wal_sequence: int
    timestamp: str
```

> **설계 근거**: JSONLWriter는 전송 계층(Transport Layer)이지 도메인 계층이 아니다.
> 4개 구현체의 엔트리 형태가 근본적으로 다르므로 (§3 참조), 공통 필드를 강제하면
> 기존 3곳 모두 수정이 필요하고 결합도(Coupling)가 높아진다.

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
  _remove_namespace_from_wal() → JSONLReader + cleanup_by_namespace() 조합
  스레드 안전성 추가 (현재 락 없음 → JSONLWriter 내부 RLock으로 해결)
```

### Phase 3: 정리 로직 통합

`audit/wal/_cleanup.py` 신규 생성.
모든 정리 유틸리티는 **Atomic Replace 패턴**을 기본 적용한다.

```python
def atomic_rewrite(target: Path, lines: list[str]) -> None:
    """임시 파일에 쓴 후 원자적으로 교체 (데이터 유실 방지)."""
    tmp = target.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.writelines(lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)  # POSIX 원자적, Python 3.3+ Windows 지원
    # Directory fsync — 디렉토리 엔트리 변경 확실히 기록
    try:
        dir_fd = os.open(str(target.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # Windows 등 미지원 환경에서는 무시

def cleanup_by_sequence(file_path: Path, keep_after_seq: int) -> int:
    """시퀀스 기반 compact (HashChainWAL.compact() 위임 대상)."""
    ...  # 내부에서 atomic_rewrite() 사용

def cleanup_by_age(directory: Path, pattern: str, max_age_days: int) -> int:
    """날짜 기반 파일 삭제 (HashChainWALRecovery.cleanup_old_wal_files() 위임 대상)."""
    ...

def cleanup_by_namespace(file_path: Path, namespace: str) -> int:
    """네임스페이스 기반 필터링 재작성 (WALRecoveryMixin._remove_namespace_from_wal() 위임 대상)."""
    ...  # 내부에서 atomic_rewrite() 사용
```

> **설계 근거 — Atomic Replace 필수 적용**:
> 현재 `compact()` (`hash_chain_safety.py:475`)와 `_remove_namespace_from_wal()` (`_wal_recovery.py:294`)은
> `open("w")`로 기존 파일을 즉시 truncate한다. 쓰기 도중 crash 시 원본도 새 데이터도 없는 상태가 된다.
> `.tmp` + `os.replace()` 패턴으로 원자성을 보장한다.
> Directory fsync는 `checkpoint_manager.py:219-226`의 기존 패턴을 따른다.

기존 정리 메서드 매핑:

```
HashChainWAL.compact() → cleanup_by_sequence() 위임
HashChainWALRecovery.cleanup_old_wal_files() → cleanup_by_age() 위임
WALRecoveryMixin._remove_namespace_from_wal() → cleanup_by_namespace() 위임
```

### Phase 4: 커밋 마커 프로토콜 통일

```
HashChainWAL과 HashChainWALRecovery의 커밋 추적을 통일:
  - 커밋 마커 형식: {"_marker": "COMMIT", "wal_sequence": int, "timestamp": str}
  - 타입: CommitMarker (TypedDict) — 커밋 마커만 스키마 강제
  - 공통 파서: JSONLReader.parse_with_committed_filter()에서 처리
```

---

## 6. 변경하지 않는 것

- **WriteAheadLog (Binary 포맷)**: Binary CRC32 포맷은 JSONL과 근본적으로 다르므로 통합 대상이 아님. 자체 mixin 구조를 유지.
- **도메인 특화 복구 로직**: HashChainWALRecovery의 IdempotencyService 연동, WALRecoveryMixin의 CascadeEvent 재구성 등은 각 도메인 고유 로직이므로 유지.
- **표준 json 라이브러리**: orjson/ujson 등 외부 의존성은 도입하지 않음. `_serialize` 래핑으로 향후 교체 대비만 함.
- **EVERYSEC fsync 정책**: 별도 백그라운드 스레드 복잡도 대비 이점 부족. 2-tier (ALWAYS/NONE)로 충분.

---

## 7. 검증 기준

- [ ] `JSONLWriter`/`JSONLReader` 신규 생성 및 단위 테스트
- [ ] `JSONLWriter` fsync 파라미터 동작 검증 (True/False)
- [ ] `JSONLWriter` 크기 기반 로테이션 동작 검증
- [ ] `JSONLWriter._serialize` 래핑 통한 직렬화 교체 가능성 검증
- [ ] `JSONLReader` 손상 라인 logged skip + 메트릭 카운터 검증
- [ ] `atomic_rewrite()` 원자적 파일 교체 + directory fsync 검증
- [ ] `cleanup_by_sequence` / `cleanup_by_age` / `cleanup_by_namespace` 단위 테스트
- [ ] `CommitMarker` TypedDict 통일 검증
- [ ] HashChainWAL이 `JSONLWriter`/`JSONLReader` 사용
- [ ] HashChainWALRecovery가 `JSONLWriter`/`JSONLReader` 사용
- [ ] WALRecoveryMixin이 `JSONLWriter` 사용 + 스레드 안전성 확보 확인
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
| 정리 로직 | 3곳 (각 ~40줄, 비원자적) | 3 유틸리티 함수 (원자적 교체 적용) |
| fsync 정책 | 혼재 (있음/없음/조건부) | 2-tier 통일 (`fsync: bool`) |
| 스레드 안전성 | WALRecoveryMixin 락 없음 | JSONLWriter 내부 RLock으로 통일 |
| 손상 라인 처리 | silent skip (4곳) | logged skip + 메트릭 (1곳) |
| 파일 로테이션 | HashChainWAL 미구현 | 선택적 크기 기반 로테이션 |
| **예상 절감** | | **~200줄 중복 제거 + 안전성 강화** |

---

## 9. 설계 결정 기록 (Design Decision Log)

논의를 통해 확정된 7개 설계 결정을 기록한다.

| # | 결정 사항 | 판정 | 상세 절 |
|---|----------|------|--------|
| D1 | Fsync 정책: 2-tier (ALWAYS/NONE), EVERYSEC 미도입 | 확정 | §5.1.1 |
| D2 | 손상 라인: logged skip + `wal_corrupted_lines_total` 메트릭 | 확정 | §5.1.2 |
| D3 | 직렬화: 표준 json 유지, `_serialize` 래핑으로 OCP 준비 | 확정 | §5.1.3 |
| D4 | 로테이션: 선택적 크기 기반, RLock 내 원자적 실행 | 확정 | §5.1.4 |
| D5 | 스레드 락: JSONLWriter 내부 RLock만, 상위 Block Lock 불필요 | 확정 | §5.1.5 |
| D6 | 엔트리 스키마: dict[str, Any] 유지, 커밋 마커만 TypedDict | 확정 | §5.1.6 |
| D7 | Cleanup: Atomic Replace (.tmp + os.replace + directory fsync) 필수 | 확정 | §Phase 3 |

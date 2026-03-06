# 308. Audit Buffer 인터페이스 통일 + Export/Lua/Retention/Hash 중복 제거

> **Status**: Refactor
> **Severity**: P3 (LOW-MEDIUM)
> **Target**:
> - `packages/selfhealing-python/src/selfhealing/audit/ring_buffer.py` — RingBuffer[T]
> - `packages/selfhealing-python/src/selfhealing/audit/event_buffer.py` — RequestAuditBuffer
> - `packages/selfhealing-python/src/selfhealing/audit/resilience/buffer.py` — InMemoryAuditBuffer
> - `packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py` — DiskPersistentBuffer, DiskBufferAdapter
> - `packages/selfhealing-python/src/selfhealing/audit/persistence/mmap_buffer.py` — MmapBuffer
> - `packages/selfhealing-python/src/selfhealing/audit/export.py` — AuditExporter
> - `packages/selfhealing-python/src/selfhealing/audit/continuous_audit.py` — export_jsonl(), export_csv_compatible()
> - `packages/selfhealing-python/src/selfhealing/audit/continuous_audit_api.py` — ExportJSONLView, ExportCSVView
> - `packages/selfhealing-python/src/selfhealing/audit/redis_batch_lua.py` — AuditBatchLuaScripts
> - `packages/selfhealing-python/src/selfhealing/audit/performance/lua_atomic.py` — LuaAtomicHashChain
> - `packages/selfhealing-python/src/selfhealing/audit/retention_cleaner.py` — WALRetentionCleaner
> - `packages/selfhealing-python/src/selfhealing/audit/signed_manifest.py` — MerkleTree
> - `packages/selfhealing-python/src/selfhealing/audit/checksum.py` — compute_sha256 등

---

## Part A. Buffer 인터페이스 통일 (P3)

### 1. 현황

5개 Buffer 구현이 각각 다른 메서드명/시그니처를 사용하며 공통 인터페이스가 없다.

| 연산 | RingBuffer | InMemoryAudit | DiskPersistent | MmapBuffer | DiskBufferAdapter |
|------|-----------|---------------|----------------|-----------|-------------------|
| 추가 | `put()` :166 | `add()` :91 | `put()` :332 | `put()` :157 | `add()` :1160 |
| 개수 | `size` :148 | `get_buffer_size()` :165 | `count()` :642 | `count()` :230 | `__len__()` :1209 |
| 통계 | `get_stats()` :325 | `get_stats()` :170 | `get_stats()` :968 | `get_stats()` :241 | `get_stats()` :1200 |
| 정리 | `clear()` :313 | `clear()` :183 | 없음 | `clear()` :236 | 없음 |

**stats 반환 구조**도 전부 다름 (RingBufferStats dataclass vs dict with 5~8 keys).

### 2. 프로덕션 버그: DiskBufferAdapter.add()

[disk_buffer.py:1175](packages/selfhealing-python/src/selfhealing/audit/persistence/disk_buffer.py#L1175):
```python
def add(self, entry: dict[str, Any]) -> bool:
    result = self._disk_buffer.put(entry)  # Fail-Open 시 None 반환
    ...
    return True  # 항상 True → Fail-Open 상태를 숨김
```

호출자는 `add()`가 True를 반환하면 데이터가 저장되었다고 판단하지만, 디스크 Full 상태에서는 실제로 저장되지 않을 수 있음.

### 3. 타입 불일치: get_audit_buffer()

[resilience/buffer.py](packages/selfhealing-python/src/selfhealing/audit/resilience/buffer.py) `get_audit_buffer()`:
```python
def get_audit_buffer() -> InMemoryAuditBuffer:  # 타입 선언
    if buffer_type == "disk":
        return DiskBufferAdapter.get_instance()  # 실제로는 DiskBufferAdapter 반환
```

### 4. 리팩터링 계획

#### Phase 1: BufferProtocol 정의

```python
# audit/resilience/buffer_protocol.py
from typing import Protocol, runtime_checkable

@runtime_checkable
class AuditBufferProtocol(Protocol):
    def add(self, entry: dict[str, Any]) -> bool:
        """엔트리 추가. False = 저장 실패 (capacity 초과 또는 디스크 에러)"""
        ...
    def count(self) -> int: ...
    def get_stats(self) -> dict[str, Any]: ...
```

#### Phase 2: DiskBufferAdapter 버그 수정

```python
def add(self, entry: dict[str, Any]) -> bool:
    result = self._disk_buffer.put(entry)
    return result is not None  # None이면 False (Fail-Open 전파)
```

#### Phase 3: get_audit_buffer() 타입 수정

```python
def get_audit_buffer() -> AuditBufferProtocol:  # Protocol 반환
    ...
```

#### Phase 4: stats 공통 키 정의

모든 Buffer의 `get_stats()`에 최소한 다음 키 포함 보장:

```python
{
    "count": int,          # 현재 엔트리 수
    "total_added": int,    # 총 추가된 수
    "total_dropped": int,  # 총 유실된 수
}
```

---

## Part B. Data Export 3중 구현 제거 (P3)

### 1. 현황

JSONL/CSV 내보내기 로직이 3곳에서 각각 구현:

| 위치 | 역할 | 구현 내용 |
|------|------|----------|
| `export.py` :114 `AuditExporter` | CLI 도구 | JSONL, JSON, CSV 포맷 쓰기 |
| `continuous_audit.py` `export_jsonl()`/`export_csv_compatible()` | 서비스 레이어 | JSONL, CSV 포맷 쓰기 |
| `continuous_audit_api.py` :368 `ExportJSONLView`/:428 `ExportCSVView` | REST API | JSONL, CSV 스트리밍 응답 |

### 2. 리팩터링 계획

```
1. AuditExporter를 canonical 구현으로 지정
2. continuous_audit.py의 export_jsonl()/export_csv_compatible():
   → AuditExporter에 위임 (래퍼 메서드로 유지, 하위호환)
3. continuous_audit_api.py의 ExportJSONLView/ExportCSVView:
   → AuditExporter.export_to_stream() 사용
```

---

## Part C. Redis Lua Scripts 패턴 중복 (P4)

### 1. 현황

| 파일 | 스크립트 수 | 용도 |
|------|-----------|------|
| `redis_batch_lua.py` :32 | 3개 | Buffer → Queue 배치 이동 |
| `performance/lua_atomic.py` :40 | 3개 | 시퀀스 할당 + 상태 업데이트 |

스크립트 등록/로딩 패턴과 에러 핸들링이 각각 독립 구현.

### 2. 리팩터링 계획

```
audit/performance/lua_registry.py 신규:
  class LuaScriptRegistry:
      def register(self, name, script_body) -> None
      def execute(self, name, redis_client, keys, args) -> Any
      # 공통: 스크립트 SHA 캐싱, NOSCRIPT 자동 재등록, 에러 래핑

기존 파일:
  redis_batch_lua.py → LuaScriptRegistry 사용
  performance/lua_atomic.py → LuaScriptRegistry 사용
```

---

## Part D. Retention/Cleanup 패턴 중복 (P4)

### 1. 현황

| 파일 | 방식 | 패턴 |
|------|------|------|
| `retention_cleaner.py` :77 | mtime 기반 retention_days | glob → stat → unlink |
| `wal/_disk_manager.py` :63 | 디스크 공간 기반 priority purge | glob → stat → unlink |

파일 반복/삭제 패턴 (glob → stat → unlink)이 동일.

### 2. 리팩터링 계획

```
audit/cleanup_utils.py 신규:
  def iter_files_by_age(directory, pattern, max_age_days) -> Iterator[Path]
  def delete_files_by_age(directory, pattern, max_age_days) -> int
  def delete_files_by_priority(directory, pattern, priority_fn) -> int

기존 파일:
  retention_cleaner.py → cleanup_utils 사용
  wal/_disk_manager.py → cleanup_utils 사용
```

---

## Part E. Hash Computation 중복 (P4)

### 1. 현황

`signed_manifest.py` :87 `MerkleTree`가 `hashlib.new(self._hash_func, data).digest()`를 직접 호출.
`checksum.py`에 이미 `compute_sha256()` 등 중앙 해시 유틸리티 존재.

### 2. 리팩터링 계획

```
signed_manifest.py MerkleTree:
  hashlib.new(self._hash_func, data).digest()
  → checksum.compute_sha256(data) 또는 checksum.compute_hash(data, algorithm) 사용
```

---

## 검증 기준

### Part A (Buffer)
- [ ] `AuditBufferProtocol` 정의 및 모든 Buffer에서 구현 확인
- [ ] `DiskBufferAdapter.add()` Fail-Open 상태 전파 수정
- [ ] `get_audit_buffer()` 리턴 타입 `AuditBufferProtocol`로 수정
- [ ] stats 공통 키 (`count`, `total_added`, `total_dropped`) 보장

### Part B (Export)
- [ ] `continuous_audit.py` export 메서드가 `AuditExporter`에 위임
- [ ] `continuous_audit_api.py` View가 `AuditExporter` 사용
- [ ] 기존 CLI/API/서비스 동작 동일 확인

### Part C (Lua)
- [ ] `LuaScriptRegistry` 구현 및 단위 테스트
- [ ] 기존 Lua 스크립트 파일이 Registry 사용

### Part D (Retention)
- [ ] `cleanup_utils.py` 구현
- [ ] `retention_cleaner.py`와 `_disk_manager.py`가 유틸리티 사용

### Part E (Hash)
- [ ] `MerkleTree`가 `checksum.py` 유틸리티 사용

---

## 절감 효과 요약

| 항목 | 절감 라인 | 리스크 해소 |
|------|----------|------------|
| Buffer Protocol | ~0 (인터페이스만) | Fail-Open 숨김 버그 수정, 타입 안전성 |
| Export 위임 | ~200줄 | 포맷 불일치 방지 |
| Lua Registry | ~80줄 | 스크립트 관리 통일 |
| Cleanup Utils | ~100줄 | 정리 로직 통일 |
| Hash 통일 | ~30줄 | DRY 원칙 |
| **총계** | **~410줄** | |

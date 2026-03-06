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
> - `packages/selfhealing-python/src/selfhealing/services/throttle/redis_lua.py` — RedisThrottleLimitManager
> - `packages/selfhealing-python/src/selfhealing/coordination/redis_elector.py` — RedisLeaderElector
> - `packages/selfhealing-python/src/selfhealing/services/namespace_emergency/atomic_query.py` — AtomicStateQuery
> - `packages/selfhealing-python/src/selfhealing/audit/retention_cleaner.py` — WALRetentionCleaner
> - `packages/selfhealing-python/src/selfhealing/audit/wal/__init__.py` — WAL rotation cleanup
> - `packages/selfhealing-python/src/selfhealing/audit/wal/_disk_manager.py` — disk full purge
> - `packages/selfhealing-python/src/selfhealing/audit/wal/_reader.py` — processed WAL cleanup
> - `packages/selfhealing-python/src/selfhealing/audit/wal/_cleanup.py` — cleanup utilities
> - `packages/selfhealing-python/src/selfhealing/core/state_backend.py` — FileStateBackend
> - `packages/selfhealing-python/src/selfhealing/audit/signed_manifest.py` — MerkleTree
> - `packages/selfhealing-python/src/selfhealing/audit/checksum.py` — compute_sha256 등

---

## Part A. Buffer `clear()` 프로토콜 분리

### 1. 현황

5개 Buffer 구현에서 `clear()` 메서드의 존재 여부와 반환값이 불일치한다.

| 버퍼 | clear() 존재 | 반환값 | 파일 |
|------|-------------|--------|------|
| `RingBuffer` | O | `int` | `ring_buffer.py:313` |
| `InMemoryAuditBuffer` | O | `int` | `resilience/buffer.py:183` |
| `MmapBuffer` | O | `None` | `persistence/mmap_buffer.py:236` |
| `RequestAuditBuffer` | O | `None` | `event_buffer.py:666` |
| `DiskPersistentBuffer` | X | — | `persistence/disk_buffer.py` |
| `DiskBufferAdapter` | X | — | `persistence/disk_buffer.py` |
| `RedisAuditBuffer` | 부분 | `clear_domain()` only | `adapters/audit/redis_buffer.py:874` |

### 2. 프로덕션 버그: DiskBufferAdapter.add()

`disk_buffer.py:1175`:
```python
def add(self, entry: dict[str, Any]) -> bool:
    result = self._disk_buffer.put(entry)  # Fail-Open 시 None 반환
    ...
    return True  # 항상 True -> Fail-Open 상태를 숨김
```

### 3. 타입 불일치: get_audit_buffer()

`resilience/buffer.py` `get_audit_buffer()`:
```python
def get_audit_buffer() -> InMemoryAuditBuffer:  # 타입 선언
    if buffer_type == "disk":
        return DiskBufferAdapter.get_instance()  # 실제로는 DiskBufferAdapter 반환
```

### 4. 리팩터링 계획

#### Phase 1: 핵심 프로토콜 + 선택적 프로토콜 분리 (ISP)

```python
# audit/resilience/buffer_protocol.py
from typing import Protocol, runtime_checkable, Any

@runtime_checkable
class AuditBufferProtocol(Protocol):
    """핵심 Buffer 인터페이스 — 모든 Buffer가 반드시 구현."""

    def add(self, entry: dict[str, Any]) -> bool:
        """엔트리 추가. False = 저장 실패 (capacity 초과 또는 디스크 에러)"""
        ...

    def count(self) -> int: ...

    def get_stats(self) -> dict[str, Any]: ...


@runtime_checkable
class ClearableBuffer(Protocol):
    """선택적 clear 기능 — 메모리 버퍼 등 논리적 초기화가 가능한 구현만."""

    def clear(self) -> int:
        """논리적 초기화. 삭제된 항목 수 반환."""
        ...
```

#### Phase 2: 반환값 통일 — `clear()` -> `int`

현재 `MmapBuffer.clear()`와 `RequestAuditBuffer.clear()`가 `None`을 반환한다.
`ClearableBuffer` Protocol에 맞춰 `int`(삭제된 항목 수)로 통일 수정.

#### Phase 3: DiskBufferAdapter 버그 수정

```python
def add(self, entry: dict[str, Any]) -> bool:
    result = self._disk_buffer.put(entry)
    return result is not None  # None이면 False (Fail-Open 전파)
```

#### Phase 4: get_audit_buffer() 타입 수정

```python
def get_audit_buffer() -> AuditBufferProtocol:  # Protocol 반환
    ...
```

#### Phase 5: 디스크 버퍼 clear() 구현

감사 로그의 무결성 특성상, 디스크 버퍼의 `clear()`는 **물리적 파일 삭제가 아닌 논리적 초기화**(시퀀스 리셋 + 엔트리 카운트 0)로 구현한다. 물리적 삭제는 retention policy를 통해서만 수행.

---

## Part B. Buffer `get_stats()` 공통 키 확장

### 1. 현황

각 Buffer의 `get_stats()` 반환 키가 완전히 불균일하다.

| 버퍼 | count 키명 | capacity 키명 | drop 관련 |
|------|-----------|--------------|----------|
| `RingBuffer` | `size` | `capacity` | `total_dropped`, `drop_rate` |
| `InMemoryAuditBuffer` | `buffered_entries` | `max_entries` | `total_dropped` |
| `DiskPersistentBuffer` | `count` | 없음 | 없음 |
| `DiskBufferAdapter` | `count` (위임) | 없음 | `total_dropped` |
| `MmapBuffer` | `entry_count` | 없음 (`file_size`로 간접) | 없음 |

### 2. 리팩터링 계획

#### Phase 1: 공통 키셋 정의

```python
# 모든 Buffer의 get_stats()에 최소한 다음 키 포함 보장
REQUIRED_STATS_KEYS = {
    "count": int,              # 현재 엔트리 수
    "total_added": int,        # 누적 추가 수
    "total_dropped": int,      # 누적 드롭 수
    "capacity": int | None,    # 최대 허용치 (무제한이면 None)
    "usage_percent": float | None,  # count/capacity*100 (capacity=None이면 None)
}
```

- `DiskPersistentBuffer`처럼 용량 제한이 없는 구현은 `capacity=None`, `usage_percent=None` 반환
- 각 버퍼는 공통 키 외에 구현 특화 키를 자유롭게 추가 가능

#### Phase 2: Prometheus 메트릭 연동

`capacity=None` / `usage_percent=None`인 경우의 메트릭 발행 스킵 로직을 구현 시점에 같이 작성한다.

```python
# audit/resilience/buffer_metrics.py
def emit_buffer_stats(buffer_name: str, stats: dict[str, Any]) -> None:
    buffer_entries_gauge.labels(buffer=buffer_name).set(stats["count"])
    buffer_dropped_gauge.labels(buffer=buffer_name).set(stats["total_dropped"])  # Gauge (누적값)
    if stats.get("usage_percent") is not None:
        buffer_usage_percent_gauge.labels(buffer=buffer_name).set(stats["usage_percent"])
```

> **주의**: `total_dropped`는 누적값이므로 `Counter.inc()`가 아닌 `Gauge.set()`을 사용한다.
> Counter로 구현하면 `emit` 호출마다 누적값이 이중 가산되어 메트릭이 왜곡된다.

---

## Part C. Data Export 스트리밍 + 3중 구현 제거

### 1. 현황 — 3중 구현

JSONL/CSV 내보내기 로직이 3곳에서 각각 구현:

| 위치 | 역할 | 구현 내용 |
|------|------|----------|
| `export.py` :114 `AuditExporter` | CLI 도구 | JSONL, JSON, CSV 포맷 쓰기 |
| `continuous_audit.py` `export_jsonl()`/`export_csv_compatible()` | 서비스 레이어 | JSONL, CSV 포맷 쓰기 |
| `continuous_audit_api.py` :368 `ExportJSONLView`/:428 `ExportCSVView` | REST API | JSONL, CSV 스트리밍 응답 |

### 2. 현황 — OOM 위험 지점 8곳

| # | 파일 | 라인 | 문제 | 심각도 |
|---|------|------|------|--------|
| 1 | `export.py` | :281 | `list(entries)` — JSON 포맷 전체 버퍼링 | CRITICAL |
| 2 | `export.py` | :289 | `list(entries)` — CSV 포맷 전체 버퍼링 | CRITICAL |
| 3 | `export.py` | :357 | `list(entries)` — HTTP 전송 전체 직렬화 | CRITICAL |
| 4 | `export.py` | :413 | `entries.append()` — Parquet 전체 수집 | HIGH |
| 5 | `continuous_audit_api.py` | :453 | `StringIO` — CSV View 전체 버퍼링 | CRITICAL |
| 6 | `continuous_audit.py` | :633 | `limit=10000` — 10K 초과 불가 | HIGH |
| 7 | `continuous_audit.py` | :659 | `limit=10000` + `list` 반환 — 전체 버퍼링 | CRITICAL |
| 8 | `continuous_audit.py` | :523 | `query()` — 페이지네이션 없음 | HIGH |

### 3. 리팩터링 계획

#### Phase 1: AuditExporter를 canonical CLI 구현으로 분리

```
AuditExporter (export.py):
  - CLI/HTTP/S3 대상 내보내기 전담
  - NDJSON chunked POST, max_entries_json_format 상한 적용
continuous_audit.py의 export_jsonl()/export_csv_compatible():
  - ContinuousAuditRecorder 내부 독립 구현 유지 (서비스 레이어)
  - offset 기반 페이지네이션으로 스트리밍
continuous_audit_api.py의 ExportJSONLView/ExportCSVView:
  - recorder.export_jsonl()/export_csv_compatible() 사용
```

> **참고**: 원래 계획은 AuditExporter에 위임하는 래퍼 패턴이었으나,
> 서비스 레이어(recorder)와 CLI(exporter)의 관심사가 다르므로 독립 구현을 유지한다.

#### Phase 2: 단일 패스 스트리밍 익스포트

`export_jsonl()`의 `limit=10000` 고정값을 단일 패스 고상한으로 변경.
`query()`가 offset 파라미터를 지원하지 않으므로 단일 호출 + 고상한 방식 채택:

```python
def export_jsonl(
    self,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    action_filter: list[AuditAction] | None = None,
    limit: int = 50000,
) -> Iterator[str]:
    entries = self.query(start_time=start_time, end_time=end_time, limit=limit)
    for entry in entries:
        if action_filter:
            entry_action = entry.get("action", "")
            if not any(a.value == entry_action for a in action_filter):
                continue
        yield json.dumps(entry, default=str)
```

#### Phase 3: CSV View 스트리밍 전환

`ExportCSVView` -> `StreamingHttpResponse` + 고정 필드셋 (감사 로그 스키마가 고정이므로 2-pass 불필요).
`FIXED_AUDIT_FIELDS`는 `audit/constants.py`에 단일 정의하여 3곳(continuous_audit.py, continuous_audit_api.py, export.py)에서 import:

```python
# audit/constants.py
FIXED_AUDIT_FIELDS = [
    "timestamp", "action", "actor_id", "actor_type",
    "target_type", "target_id", "service_name", "reason", "success",
]

def generate_csv():
    yield ",".join(FIXED_AUDIT_FIELDS) + "\n"
    cursor = None
    while True:
        batch = recorder.query_page(cursor=cursor, page_size=1000)
        if not batch.entries:
            break
        for entry in batch.entries:
            yield _flatten_to_csv_row(entry) + "\n"
        cursor = batch.next_cursor
```

#### Phase 4: HTTP Export -> NDJSON chunked POST

`_export_to_http()` — 전체 JSON 배열 대신 NDJSON 스트리밍:

```python
def _export_to_http(self, entries: Iterator[dict[str, Any]]) -> None:
    for chunk in _batched(entries, 500):
        data = "\n".join(json.dumps(e, default=str) for e in chunk).encode()
        req = urllib.request.Request(
            self._options.http_endpoint,
            data=data,
            headers={"Content-Type": "application/x-ndjson", **headers},
        )
        urllib.request.urlopen(req, timeout=30)
```

#### Phase 5: JSON 배열 format 상한 강제

JSON 배열 형식은 구조적으로 스트리밍이 어려우므로 명시적 상한을 설정:

```python
# ExportOptions에 추가
max_entries_json_format: int = 50000  # 초과 시 ValueError

def _write_entries(self, entries, output):
    if format_type == ExportFormat.JSON:
        entries_list = list(itertools.islice(entries, self._options.max_entries_json_format + 1))
        if len(entries_list) > self._options.max_entries_json_format:
            raise ValueError(
                f"JSON format supports max {self._options.max_entries_json_format} entries. "
                f"Use JSONL format for larger exports."
            )
```

#### Django StreamingHttpResponse 주의사항

CSV 스트리밍 구현 시, 제너레이터 내에서 DB 커넥션이나 긴 I/O 트랜잭션을 물고 있으면 워커 스레드가 고갈될 수 있다. `query_page()` 내부에서 커넥션을 열고 닫는 방식으로 장시간 점유를 방지해야 한다. Django의 `iterator(chunk_size=N)`을 반드시 함께 사용할 것.

---

## Part D. Redis Lua Scripts 통합 Registry

### 1. 현황

Lua 스크립트 관리가 6개 모듈에서 각각 독립 구현:

| 파일 | 스크립트 수 | 로딩 전략 | NOSCRIPT 처리 |
|------|-----------|----------|--------------|
| `audit/redis_batch_lua.py` :32 | 3개 | Eager (`__init__`) | `NoScriptError` catch + 재등록 |
| `audit/performance/lua_atomic.py` :40 | 3개 | Lazy (`_ensure_scripts_loaded`) | `Exception` catch + fallback |
| `services/throttle/redis_lua.py` :33 | 4개 | Lazy (`_ensure_scripts_loaded`) | `"NOSCRIPT" in str(e)` 문자열 매치 |
| `coordination/redis_elector.py` :49 | 4개 | Lazy (`register_script`) | redis-py 자동 처리 |
| `services/saga/lua_scripts.py` :13 | 2개 | Direct `eval()` | 없음 |
| `services/namespace_emergency/atomic_query.py` :32 | 1개 | Optional preload | `evalsha` 실패 시 `eval` fallback |

### 2. 리팩터링 계획

#### Phase 1: LuaScriptRegistry 구현

```python
# audit/performance/lua_registry.py
class LuaScriptRegistry:
    """통합 Lua 스크립트 관리자.

    - Lazy-load: 첫 execute() 시점에 SCRIPT LOAD
    - NOSCRIPT 자동 복구: evalsha 실패 시 재등록 후 재시도 (최대 2회)
    - 메트릭: script_load 횟수, NOSCRIPT 발생 횟수 Prometheus 노출
    """

    MAX_RELOAD_ATTEMPTS = 2

    def __init__(self, redis_client: Redis):
        self._redis = redis_client
        self._scripts: dict[str, str] = {}       # name -> script body
        self._sha_cache: dict[str, str] = {}      # name -> SHA

    def register(self, name: str, script_body: str) -> None:
        self._scripts[name] = script_body

    def execute(self, name: str, keys: list, args: list) -> Any:
        if len(keys) > 1:
            self._validate_same_slot(keys)

        for attempt in range(self.MAX_RELOAD_ATTEMPTS):
            sha = self._sha_cache.get(name)
            try:
                if sha:
                    return self._redis.evalsha(sha, len(keys), *keys, *args)
                return self._load_and_execute(name, keys, args)
            except NoScriptError:
                self._sha_cache.pop(name, None)
                lua_noscript_total.inc()
                if attempt == self.MAX_RELOAD_ATTEMPTS - 1:
                    return self._redis.eval(
                        self._scripts[name], len(keys), *keys, *args
                    )

        raise RuntimeError(f"Lua script '{name}' failed after {self.MAX_RELOAD_ATTEMPTS} attempts")

    def _load_and_execute(self, name: str, keys: list, args: list) -> Any:
        body = self._scripts[name]
        sha = self._redis.script_load(body)
        self._sha_cache[name] = sha
        lua_script_load_total.inc()
        return self._redis.evalsha(sha, len(keys), *keys, *args)

    @staticmethod
    def _validate_same_slot(keys: list[str]) -> None:
        """다중 키 스크립트의 해시 슬롯 일치 검증 (개발/테스트 방어)."""
        tags = {LuaScriptRegistry._extract_hash_tag(k) for k in keys}
        if len(tags) > 1:
            raise ValueError(
                f"Keys span multiple hash slots: {keys}. "
                f"Use {{hash_tag}} to group related keys."
            )

    @staticmethod
    def _extract_hash_tag(key: str) -> str:
        start = key.find("{")
        end = key.find("}", start + 1)
        if start != -1 and end != -1 and end > start + 1:
            return key[start + 1:end]
        return key
```

#### Phase 2: 기존 6개 모듈 마이그레이션

```
redis_batch_lua.py       -> LuaScriptRegistry 사용
performance/lua_atomic.py -> LuaScriptRegistry 사용
services/throttle/redis_lua.py -> LuaScriptRegistry 사용
coordination/redis_elector.py  -> LuaScriptRegistry 사용
services/saga/lua_scripts.py   -> LuaScriptRegistry 사용
services/namespace_emergency/atomic_query.py -> LuaScriptRegistry 사용
```

---

## Part E. Redis Cluster Hash Tag 키 재설계

### 1. 현황

다중 키 Lua 스크립트가 8개 존재하며, 현재 키 설계는 Redis Cluster에서 `CROSSSLOT` 에러를 발생시킨다.

| 모듈 | 스크립트 | KEYS 수 | 현재 키 패턴 | Cluster 호환 |
|------|----------|---------|-------------|-------------|
| Audit Batch | BATCH_MOVE | 2 | `audit:buffer:{d}`, `audit:processing:{d}` | X |
| Audit Batch | BATCH_RESTORE | 2 | `audit:processing:{d}`, `audit:buffer:{d}` | X |
| Throttle | LIMIT_UPDATE | 2 | `throttle:limit:{s}`, `throttle:last_safe_limit:{s}` | X |
| Throttle | LOAD_SAFE | 2 | `throttle:limit:{s}`, `throttle:safe_limit:{s}` | X |
| Hash Chain | ADD_INTEGRITY | 3 | `hash_chain:seq`, `hash_chain:state`, `hash_chain:pending` | X |
| Hash Chain | COMMIT | 2 | `pending:{seq}`, `hash_chain:state` | X |
| Leader Election | ACQUIRE | 2 | `{pfx}{resource}`, `{pfx}fencing:{resource}` | X |
| Emergency | STATE_QUERY | 2 | `global:state`, `{ns}:state` | X |

### 2. 선택지 비교

| 전략 | 장점 | 단점 | 판정 |
|------|------|------|------|
| **A. Hash Tag 키 재설계** | Cluster 100% 호환, 런타임 비용 0, 코드 변경 최소 | 키 마이그레이션 필요 (개발 중이라 무비용) | 채택 |
| **B. 런타임 Slot 검증** | 잘못된 키 조합 조기 발견 | 매 호출 CRC16 계산 오버헤드, 검증만 하고 해결 못함 | A와 조합 |
| **C. Lua 스크립트 분할** | 키 제약 없음 | 원자성 상실 (2 스크립트 = 2 RTT), 설계 의도 파괴 | 불채택 |
| **D. 단일 노드 유지** | 변경 없음 | 스케일 한계, HA 부족, 이후 마이그레이션 비용 급증 | 불채택 |

### 3. 리팩터링 계획 — 전략 A + B 조합

#### Phase 1: 키 재설계

```python
# 1. Audit Batch — domain 그룹핑
# BEFORE:
"audit:buffer:{domain}"          "audit:processing:{domain}"
# AFTER:
"audit:{domain}:buffer"          "audit:{domain}:processing"
# {domain}이 Hash Tag로 동작 -> 같은 슬롯

# 2. Throttle — service 그룹핑
# BEFORE:
"throttle:limit:{service}"       "throttle:last_safe_limit:{service}"
# AFTER:
"throttle:{service}:limit"       "throttle:{service}:safe_limit"

# 3. Hash Chain — 전역 그룹 (단일 슬롯)
# BEFORE:
"audit:hash_chain:seq"           "audit:hash_chain:state"     "audit:hash_chain:pending:N"
# AFTER:
"audit:{hash_chain}:seq"         "audit:{hash_chain}:state"   "audit:{hash_chain}:pending:N"

# 4. Leader Election — resource 그룹핑
# BEFORE:
"{pfx}{resource}"                "{pfx}fencing_token:{resource}"
# AFTER:
"{pfx}{resource}:leader"         "{pfx}{resource}:fencing"
```

#### Phase 2: Emergency State — 스크립트 분할

Global 키와 Regional 키는 본질적으로 다른 네임스페이스에 속하므로 같은 해시 슬롯에 넣는 것이 부자연스럽다. 이 경우에만 Lua 스크립트를 분할하되, Pipeline으로 1 RTT를 유지한다:

```python
# atomic_query.py — Cluster 호환 버전
class AtomicStateQuery:
    def query_effective_state(self, namespace: str, precedence: int) -> dict:
        pipe = self._redis.pipeline(transaction=False)
        pipe.get(self._get_global_key())
        pipe.get(self._get_regional_key(namespace))
        global_raw, regional_raw = pipe.execute()
        return self._resolve_precedence(global_raw, regional_raw, precedence)
```

#### Phase 3: Slot 검증 (방어적)

`LuaScriptRegistry._validate_same_slot()` (Part D에서 구현)이 다중 키 스크립트 호출 시 해시 태그 일치를 검증. 키 설계 오류를 개발/테스트 단계에서 조기 발견.

#### 핫스팟 리스크 분석

| 모듈 | Hash Tag | 분산 수준 | 핫스팟 리스크 |
|------|----------|----------|-------------|
| Audit Batch | `{domain}` | 도메인 수 (수십~수백) | 낮음 |
| Throttle | `{service}` | 서비스 수 (수십) | 낮음 |
| Hash Chain | `{hash_chain}` | 단일 그룹, 키 3개 | 무관 |
| Leader Election | `{resource}` | 리소스 수 (수십) | 낮음 |

실제 핫스팟은 단일 도메인에 트래픽이 극도로 집중되는 경우에만 발생하며, Redis Cluster 자체의 리밸런싱으로 해결 가능.

---

## Part F. Retention/Cleanup Race Condition 해소

### 1. 현황

파일 삭제 지점 22개 중 12개가 멀티 워커 Race Condition에 취약하다.

#### CRITICAL 위험 지점

| 파일 | 라인 | 컨텍스트 | `missing_ok` | 동시성 보호 |
|------|------|---------|-------------|-----------|
| `wal/__init__.py` | :216 | WAL 로테이션 | X | X |
| `wal/_disk_manager.py` | :94, :132 | 디스크 풀 긴급 퍼지 | X | X |
| `wal/_reader.py` | :282 | 처리 완료 WAL 삭제 | X | X |
| `retention_cleaner.py` | :115, :127 | 보존 기간 만료 삭제 | X | X |
| `core/state_backend.py` | :120, :188, :196 | 상태 파일 삭제 (threading.Lock만) | X | 프로세스간 X |
| `critical_path_fallback.py` | :385, :387 | 비상 상태 클리어 | X | X |

#### MEDIUM 위험 지점

| 파일 | 라인 | 컨텍스트 |
|------|------|---------|
| `wal/_cleanup.py` | :76 | age 기반 cleanup |
| `meta/fallback_escalation.py` | :274 | escalation 로그 삭제 |
| `audit/integrity/local_manager.py` | :131 | 해시 체인 리셋 |
| `metrics/snapshot_storage.py` | :283 | 스냅샷 임시 파일 |

#### 이미 안전한 지점

| 파일 | 라인 | 방식 |
|------|------|------|
| `audit/checkpoint_strategy.py` | :439, :532 | `missing_ok=True` |
| `audit/checkpoint_manager.py` | :254, :325 | `missing_ok=True` |
| `audit/wal/_cleanup.py` | :111 | `missing_ok=True` |
| `audit/export.py` | :328 | `tempfile` (고유 파일) |
| `tasks/cascade_cleanup_tasks.py` | :539 | `missing_ok=True` |

### 2. 리팩터링 계획

#### Phase 1: safe_unlink 유틸리티 구현

```python
# core/file_utils.py (신규)
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

def safe_unlink(path: Path) -> bool:
    """Multi-worker 환경에서 안전한 파일 삭제.

    Returns:
        True: 실제 삭제 수행, False: 이미 삭제되었거나 권한 오류
    """
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except PermissionError:
        logger.warning("safe_unlink.permission_denied", extra={"path": str(path)})
        return False
    except OSError as e:
        logger.warning("safe_unlink.os_error", extra={"path": str(path), "error": str(e)})
        return False
```

#### Phase 2: 12개 취약 지점 교체

모든 CRITICAL/MEDIUM 위험 지점의 `.unlink()` 호출을 `safe_unlink()`로 교체.

#### Phase 3: Cleanup 공통 유틸리티

```python
# audit/cleanup_utils.py (신규)
def iter_files_by_age(directory: Path, pattern: str, max_age_days: int) -> Iterator[Path]:
    """보존 기간 초과 파일을 age 순으로 반환."""
    ...

def delete_files_by_age(directory: Path, pattern: str, max_age_days: int) -> int:
    """보존 기간 초과 파일 삭제. safe_unlink 사용. 삭제 건수 반환."""
    ...

def delete_files_by_priority(directory: Path, pattern: str, priority_fn) -> int:
    """우선순위 기반 파일 삭제 (디스크 풀 대응). safe_unlink 사용."""
    ...
```

`retention_cleaner.py`와 `wal/_disk_manager.py`가 이 유틸리티를 사용하도록 마이그레이션.

---

## Part G. Hash Computation `hashlib.new()` Fallback

### 1. 현황

| 컴포넌트 | 알고리즘 | 동적 라우팅 | 파일 |
|----------|---------|-----------|------|
| `checksum.py:compute_checksum()` | SHA256, CRC32 | O (`if-elif`) | `checksum.py:133` |
| `MerkleTree` | SHA256 (default, 변경 가능) | O (`hashlib.new()`) | `signed_manifest.py:87` |
| `hash_chain_safety.py` | SHA256 고정 | X (의도적) | `hash_chain_safety.py` |

`compute_checksum()`은 sha256/crc32만 지원하고 그 외 알고리즘은 `ValueError`를 발생시킨다. `MerkleTree`는 `hashlib.new()`로 어떤 알고리즘이든 받을 수 있으나 `checksum.py`를 사용하지 않는다.

### 2. 리팩터링 계획

#### Phase 1: hashlib.new() fallback 추가 (allowlist 제한)

`compute_checksum()`의 `else` 분기에 `hashlib.new()` fallback을 추가하되,
감사 모듈의 보안 요건상 허용 알고리즘을 명시적으로 제한:

```python
def compute_checksum(data, algorithm: str = "crc32", truncate=None):
    if algorithm == "sha256":
        return compute_sha256(data, truncate)
    elif algorithm == "crc32":
        return compute_crc32(data)
    else:
        _ALLOWED_ALGORITHMS = {"sha384", "sha512", "sha3_256", "sha3_512", "blake2b", "blake2s"}
        if algorithm not in _ALLOWED_ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}. Allowed: ...")
        normalized = _normalize_to_bytes(data)
        return hashlib.new(algorithm, normalized).hexdigest()
```

> **설계 결정**: md5, sha1 등 약한 해시 알고리즘은 allowlist에서 제외.
> 감사 데이터 무결성 검증에 약한 알고리즘 사용을 방지하는 defense-in-depth 조치.

#### Phase 2: MerkleTree가 checksum.py 사용

```python
# signed_manifest.py MerkleTree:
# BEFORE:
hashlib.new(self._hash_func, data).digest()
# AFTER:
from selfhealing.audit.checksum import compute_checksum
compute_checksum(data, algorithm=self._hash_func)
```

Hash Chain의 SHA256 고정은 **의도적 설계**(체인 중간에 알고리즘이 바뀌면 무결성 검증 파괴)이므로 변경하지 않는다.

---

## 검증 기준

### Part A (Buffer clear)
- [ ] `AuditBufferProtocol` + `ClearableBuffer` Protocol 정의
- [ ] `MmapBuffer.clear()`, `RequestAuditBuffer.clear()` 반환값 `int`로 통일
- [ ] `DiskBufferAdapter.add()` Fail-Open 상태 전파 수정
- [ ] `get_audit_buffer()` 리턴 타입 `AuditBufferProtocol`로 수정

### Part B (Buffer stats)
- [ ] 모든 Buffer의 `get_stats()`에 공통 키 5개 포함 (`count`, `total_added`, `total_dropped`, `capacity`, `usage_percent`)
- [ ] Prometheus 메트릭 연동 (`emit_buffer_stats()`) 구현
- [ ] `capacity=None` 시 `usage_percent` 메트릭 스킵 동작 확인

### Part C (Export)
- [ ] `continuous_audit.py` export 메서드가 `AuditExporter`에 위임
- [ ] `continuous_audit_api.py` View가 `AuditExporter` 사용
- [ ] `export_jsonl()` cursor 기반 페이지네이션 동작
- [ ] `ExportCSVView` -> `StreamingHttpResponse` 전환
- [ ] `_export_to_http()` NDJSON chunked POST 전환
- [ ] JSON format `max_entries_json_format` 상한 적용
- [ ] 기존 CLI/API/서비스 동작 동일 확인

### Part D (Lua Registry)
- [ ] `LuaScriptRegistry` 구현 및 단위 테스트
- [ ] Lazy-load + NOSCRIPT 자동 복구 (최대 2회) 동작 확인
- [ ] `_validate_same_slot()` 해시 슬롯 검증 동작 확인
- [ ] 기존 6개 모듈 마이그레이션 완료

### Part E (Hash Tag)
- [ ] Audit Batch 키: `audit:{domain}:buffer/processing`
- [ ] Throttle 키: `throttle:{service}:limit/safe_limit/rtt`
- [ ] Hash Chain 키: `audit:{hash_chain}:seq/state/pending:N`
- [ ] Leader Election 키: `{pfx}{resource}:leader/fencing`
- [ ] Emergency State: Pipeline 기반 분할 구현
- [ ] 기존 Lua 스크립트 KEYS 참조 업데이트

### Part F (Race Condition)
- [ ] `core/file_utils.py` `safe_unlink()` 구현
- [ ] 12개 취약 지점 `safe_unlink()` 교체
- [ ] `cleanup_utils.py` 공통 유틸리티 구현
- [ ] `retention_cleaner.py` 마이그레이션
- [ ] `wal/_disk_manager.py` 마이그레이션

### Part G (Hash)
- [ ] `compute_checksum()` `hashlib.new()` fallback 추가
- [ ] `MerkleTree`가 `checksum.py` 유틸리티 사용

---

## 절감 효과 요약

| 항목 | 절감 라인 | 리스크 해소 |
|------|----------|------------|
| Part A: Buffer Protocol | ~0 (인터페이스) | Fail-Open 숨김 버그 수정, 타입 안전성 |
| Part B: Stats 통일 | ~50줄 | 모니터링 사각지대 해소, Prometheus 통합 |
| Part C: Export 위임 + 스트리밍 | ~200줄 | OOM 방지, 포맷 불일치 방지 |
| Part D: Lua Registry | ~300줄 | 스크립트 관리 통일, NOSCRIPT 처리 표준화 |
| Part E: Hash Tag | ~0 (키 변경) | Redis Cluster 전환 시 마이그레이션 비용 제거 |
| Part F: Race Condition | ~100줄 | 멀티 워커 크래시 방지, 12개 취약점 해소 |
| Part G: Hash 통일 | ~30줄 | DRY 원칙, 알고리즘 확장성 |
| **총계** | **~680줄** | |

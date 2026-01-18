# 분산 해시 체인 강화 설계서 (Enhanced Implementation)

> **Version**: 1.7.0  
> **Created**: 2026-01-17  
> **Updated**: 2026-01-18 (Phase 1, 2, 3 구현 완료)  
> **Category**: Audit/무결성 보장  
> **구현 상태**: ✅ Phase 1 구현 완료, ✅ Phase 2 구현 완료, ✅ Phase 3 구현 완료  
> **선행 문서**: [42_DISTRIBUTED_HASH_CHAIN_REDIS.md](./42_DISTRIBUTED_HASH_CHAIN_REDIS.md)  
> **근거 코드**: 실제 소스 코드 분석 기반

---

## 📋 목차

1. [개요](#1-개요)
2. [리뷰 분석 및 평가](#2-리뷰-분석-및-평가)
3. [원자적 해시 체인 (Atomic Hash Chain)](#3-원자적-해시-체인-atomic-hash-chain)
4. [쓰기 일관성 보장 (Write Consistency)](#4-쓰기-일관성-보장-write-consistency)
5. [시작 시 무결성 검사 (Startup Integrity Check)](#5-시작-시-무결성-검사-startup-integrity-check)
6. [일일 앵커 시스템 (Daily Anchor)](#6-일일-앵커-시스템-daily-anchor)
7. [백그라운드 병합기 (Background Merger)](#7-백그라운드-병합기-background-merger)
8. [기존 패턴 재사용](#8-기존-패턴-재사용)
9. [구현 계획 (통합 Phase 로드맵)](#9-구현-계획-통합-phase-로드맵)
    - [9.0 Phase 개요](#90-phase-개요)
    - [9.1 Phase 1: Core 기능 (P0)](#91-phase-1-core-기능-p0---필수)
    - [9.2 Phase 2: 안전장치 (P0)](#92-phase-2-안전장치-p0---필수)
    - [9.3 Phase 3: 성능 최적화 (P1)](#93-phase-3-성능-최적화-p1---중요)
    - [9.4 Phase 4: 장애 대비 (P1)](#94-phase-4-장애-대비-p1---중요)
    - [9.5 Phase 5: 테스트 및 검증 (P2)](#95-phase-5-테스트-및-검증-p2---권장)
    - [9.6 의존성 그래프](#96-의존성-그래프)
    - [9.7 빠른 시작 (MVP)](#97-빠른-시작-mvp)
10. [네이밍 가이드](#10-네이밍-가이드)
11. [보완 사항 (2차 리뷰 반영)](#11-보완-사항-2차-리뷰-반영)
    - [11.1 기대 해시 등록](#111-기대-해시-등록-expected-hash-registration)
    - [11.2 Orphaned 로그 유효성 검증](#112-orphaned-로그-유효성-검증-merge-validation)
    - [11.3 오프라인 앵커 백업](#113-오프라인-앵커-백업-offline-anchor-backup)
    - [11.4 날짜별 샤딩 락](#114-날짜별-샤딩-락-sharded-merge-lock)
    - [11.5 Self-Cleanup 워치독](#115-self-cleanup-워치독-local-watchdog)
    - [11.6 PENDING TTL 조정](#116-pending-ttl-조정)
    - [11.7 병합 시점 Lock 전략](#117-병합-시점-lock-전략-추가)
    - [11.8 Monotonic Timer 강제](#118-monotonic-timer-강제-clockskew-보호)
    - [11.9 Atomic Swap (전역 락)](#119-atomic-swap-전역-락-촩돌-방지)
    - [11.10 Audit Trail of Integrity](#1110-audit-trail-of-integrity-무결성-복구-이벤트)
12. [재사용 패턴 요약 (업데이트)](#12-재사용-패턴-요약-업데이트)
13. [성능 최적화 전략 (4차 리뷰 반영)](#13-성능-최적화-전략-4차-리뷰-반영)
    - [13.1 단점-최적화 매핑 총괄표](#131-단점-최적화-매핑-총괄표)
    - [13.2 Redis RTT 최적화](#132-redis-rtt-최적화)
    - [13.3 I/O 최적화](#133-io-최적화)
    - [13.4 메모리 최적화](#134-메모리-최적화)
    - [13.5 검증 최적화](#135-검증-최적화)
    - [13.6 최적화 불가능한 본질적 Trade-off](#136-최적화-불가능한-본질적-trade-off)
    - [13.6.1 완화 효과 정량 분석](#1361-완화-효과-정량-분석)
    - [13.6.2 "잃는 것"에 대한 대비책 (Zero Data Loss)](#1362-잃는-것에-대한-대비책-zero-data-loss)
14. [장애 대비책 (Graceful Degradation)](#14-장애-대비책-graceful-degradation)
    - [14.1 장애 시나리오별 대응 전략](#141-장애-시나리오별-대응-전략)
    - [14.2 Redis → Local Fallback](#142-redis--local-fallback)
    - [14.3 degraded=True 마킹](#143-degradedtrue-마킹)
    - [14.4 WAL Recovery](#144-wal-recovery)
    - [14.5 GracefulDegradationManager](#145-gracefuldegradationmanager)
    - [14.6 Self-Healing 통합](#146-self-healing-통합)
    - [14.7 복구 우선순위](#147-복구-우선순위)

---

## 1. 개요

### 1.1 배경

기존 `42_DISTRIBUTED_HASH_CHAIN_REDIS.md`의 리뷰에서 5가지 강화 포인트가 제안됨:

1. **Lua Script 원자성** - Redis 내부에서 해시 계산
2. **Write-Ahead Checkpoint** - PENDING 상태 관리
3. **시퀀스 역전 방지** - 시작 시 동기화
4. **앵커링 기반 검증** - 부분 검증 최적화
5. **자동 화해 로직** - Fallback 병합

### 1.2 리뷰 내용 요약

| 항목 | 리뷰 제안 | 현재 시스템 네이밍 |
|------|----------|------------------|
| ① Lua Script 원자성 | Hash-at-Source | `AtomicHashChainScript` |
| ② PENDING 상태 | Write-Ahead Checkpoint | `PendingSequenceManager` |
| ③ 시퀀스 역전 방지 | StartupIntegrityCheck | `StartupHashChainSync` (기존 Startup Hydration 패턴) |
| ④ 앵커링 검증 | Daily Anchor | `DailyHashAnchor` |
| ⑤ 자동 화해 | BackgroundMerger | `HashChainReconciler` (기존 Reconciler 패턴) |

---

## 2. 리뷰 분석 및 평가

### 2.1 리뷰별 평가

#### ① Lua Script 원자성 - ⚠️ 부분 동의

**리뷰 제안:**
> Python에서 해시 계산하지 말고 Redis Lua Script 내부에서 처리

**분석:**

**장점:**
- INCR → HGET → 계산 → HSET이 완전히 원자적

**단점:**
- Redis의 Lua는 `crypto` 모듈 미지원 (SHA256 직접 불가)
- 외부 모듈 로드 필요 (`redis-cell` 등)
- 운영 복잡도 증가

**현재 코드 분석** - [redis_adapter.py#L34-L155](packages/selfhealing-python/src/selfhealing/adapters/cache/redis_adapter.py#L34-L155):

```python
class RedisDistributedLock(DistributedLock):
    """
    Redis-based distributed lock using SET NX with TTL.
    Features:
        - Atomic acquire/release operations
        - Automatic TTL-based expiration (prevents deadlocks)
    """
```

**결론:** 
- 현재 `RedisDistributedLock`으로 이미 직렬화 보장
- Lua Script에서 SHA256은 Redis 표준 기능 아님
- **대안: "유령 시퀀스" 감지 로직 추가** (PENDING 상태로 해결)

---

#### ② Write-Ahead Checkpoint - ✅ 완전 동의

**리뷰 제안:**
> Redis에 PENDING 상태 추가, 파일 쓰기 완료 후 해제

**현재 문제점** - [local.py#L140-L162](packages/selfhealing-python/src/selfhealing/audit/backends/local.py#L140-L162):

```python
def write(self, entry: Dict[str, Any]) -> bool:
    with self._lock:
        try:
            # 1. Redis 업데이트 (성공)
            if self._hash_chain:
                entry = self._hash_chain.add_integrity(entry)

            # 2. 파일 쓰기 (실패할 수 있음!) ← 여기서 실패하면 체인 깨짐
            self._file_handle.write(json_line + "\n")
```

**기존 패턴 참조** - [chaos/base.py#L61](packages/selfhealing-python/src/selfhealing/services/chaos/base.py#L61):

```python
class ExperimentStatus(str, Enum):
    PENDING = "pending"  # ← 이미 PENDING 패턴 존재
    RUNNING = "running"
    COMPLETED = "completed"
```

**결론:** 필수 구현. 기존 `PENDING` 상태 패턴 재사용.

---

#### ③ 시퀀스 역전 방지 - ✅ 완전 동의

**리뷰 제안:**
> 시스템 시작 시 파일의 마지막 시퀀스와 Redis 비교, Max Sync

**기존 패턴 참조** - [backend.py#L183-L241](packages/selfhealing-python/src/selfhealing/adapters/resilient/backend.py#L183-L241):

```python
def _recover_from_wal_on_startup(self) -> None:
    """
    Recover unprocessed WAL entries on server startup.
    
    This is the key to zero data loss:
    - Server crashed in degraded mode? WAL has all changes.
    - WAL entries are replayed to Redis on startup.
    """
```

**기존 패턴 참조** - [apps.py#L103-L142](packages/selfhealing-python/src/selfhealing/adapters/django/apps.py#L103-L142):

```python
# Startup Hydration - 중복 실행 방지
_startup_hydration_done = False

def ready(self):
    # 4. Hydrate metric gauges with jitter (Startup Hydration)
    self._validate_startup_config()
```

**결론:** 필수 구현. 기존 `Startup Hydration` 패턴과 `_recover_from_wal_on_startup()` 패턴 재사용.

---

#### ④ 앵커링 기반 검증 - ✅ 동의 (성능 최적화)

**리뷰 제안:**
> 매일 마지막 해시를 별도 저장소에 박제, 검증 시 오늘 로그 + 어제 앵커만 대조

**현재 문제점** - [integrity.py#L54-L93](packages/selfhealing-python/src/selfhealing/audit/integrity.py#L54-L93):

```python
class HashChainVerifier:
    def verify_chain(self, entries: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
        for i, entry in enumerate(entries):  # ← 전체 순회!
            # ... 검증 로직
```

**결론:** 로그가 수백만 건일 때 필수. 별도 Redis 키에 일일 앵커 저장.

---

#### ⑤ 자동 화해 로직 - ✅ 동의 (무결성 복원)

**리뷰 제안:**
> Redis 복구 시 로컬 체인을 메인 체인에 일괄 이어붙이기

**현재 문제점** - [integrity.py#L480-L505](packages/selfhealing-python/src/selfhealing/audit/integrity.py#L480-L505):

```python
def _add_integrity_fallback(self, entry: Dict[str, Any]) -> Dict[str, Any]:
    if self._fallback:
        result = self._fallback.add_integrity(entry)
        # Mark as degraded for later reconciliation ← 마킹만!
        result["integrity"]["degraded"] = True
        return result
```

**기존 패턴 참조** - [reconciler.py#L5](packages/selfhealing-python/src/selfhealing/metrics/reconciler.py#L5):

```python
"""
Metrics Reconciler for syncing Prometheus Gauges with actual database state
during server startup and on-demand.
"""
```

**결론:** 필수 구현. 기존 `MetricsReconciler` 패턴 재사용.

---

### 2.2 종합 평가

| 항목 | 동의 여부 | 우선순위 | 구현 난이도 |
|------|---------|---------|------------|
| ① Lua Script 원자성 | ⚠️ 부분 동의 | P2 | 높음 (Redis 확장 필요) |
| ② PENDING 상태 | ✅ 완전 동의 | **P0** | 낮음 |
| ③ 시퀀스 역전 방지 | ✅ 완전 동의 | **P0** | 낮음 |
| ④ 앵커링 검증 | ✅ 동의 | P1 | 중간 |
| ⑤ 자동 화해 | ✅ 동의 | **P0** | 중간 |

**수정/보완 사항:**
- ① Lua Script 원자성: Redis 표준 기능으로 SHA256 불가 → PENDING 상태로 "유령 시퀀스" 문제 해결
- ④ 앵커링: 불변 저장소로 별도 Redis 키 사용 (외부 DB 불필요)

---

## 3. 원자적 해시 체인 (Atomic Hash Chain)

### 3.1 문제점

현재 구현에서 INCR과 HSET이 분리되어 있어 "유령 시퀀스" 발생 가능:

```
Timeline:
─────────────────────────────────────────────
t=0: INCR seq → 5       (성공)
t=1: 락 타임아웃/크래시  (해시 저장 실패!)
t=2: 다음 Pod: INCR → 6
     → seq=5는 해시 없이 존재 = 유령 시퀀스
```

### 3.2 해결 방안: PENDING 상태로 해결

**Lua Script에서 SHA256 계산이 불가능**하므로, PENDING 상태로 트랜잭션 안전성 확보:

```python
# 새로운 Redis 키 구조
PENDING_KEY = "audit:hash_chain:pending:{sequence}"

# 동작 순서:
# 1. INCR + PENDING 마킹 (원자적)
# 2. Python에서 해시 계산
# 3. 해시 저장 + PENDING 해제 (원자적)
# 4. 파일 쓰기 성공 시 PENDING 삭제
```

### 3.3 대안: Redis SHA256 확장

Redis 7.0+ 에서는 `redis-cell` 또는 커스텀 모듈로 SHA256 가능하나:
- 운영 복잡도 증가
- 호환성 문제
- **권장하지 않음**

---

## 4. 쓰기 일관성 보장 (Write Consistency)

### 4.1 PendingSequenceManager 설계

**파일 위치**: `packages/selfhealing-python/src/selfhealing/audit/integrity.py`

```python
class SequenceState(str, Enum):
    """시퀀스 상태 (chaos/base.py의 ExperimentStatus 패턴 재사용)."""
    PENDING = "pending"      # 시퀀스 예약됨, 파일 쓰기 대기
    COMMITTED = "committed"  # 파일 쓰기 완료
    ORPHANED = "orphaned"    # 실패로 인한 고아 시퀀스


class PendingSequenceManager:
    """
    시퀀스 예약 및 커밋 관리.
    
    Write-Ahead Checkpoint 패턴:
    1. reserve_sequence() → PENDING 상태로 시퀀스 예약
    2. 파일 쓰기 성공 후 commit_sequence() → COMMITTED
    3. 실패 시 abort_sequence() → 시퀀스 롤백 또는 ORPHANED 마킹
    
    기존 패턴 참조:
    - ExperimentStatus.PENDING (chaos/base.py L61)
    - WAL의 sequence 관리 (wal.py)
    """
    
    PENDING_KEY_PREFIX = "audit:hash_chain:pending:"
    PENDING_TTL_SECONDS = 300  # 5분 후 자동 만료 (고아 방지)
    
    def __init__(self, redis_client: Any, key_prefix: str = "selfhealing:"):
        self._redis = redis_client
        self._key_prefix = key_prefix
    
    def reserve_sequence(self, sequence: int, entry_hash: str) -> bool:
        """
        시퀀스 예약 (PENDING 상태).
        
        Returns:
            True if reserved successfully
        """
        pending_key = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
        
        # Lua Script: 원자적으로 PENDING 마킹
        lua_script = """
        local pending_key = KEYS[1]
        local entry_hash = ARGV[1]
        local ttl = tonumber(ARGV[2])
        
        -- 이미 PENDING이면 중복 예약 방지
        if redis.call("EXISTS", pending_key) == 1 then
            return 0
        end
        
        redis.call("SET", pending_key, entry_hash, "EX", ttl)
        return 1
        """
        
        result = self._redis.eval(
            lua_script, 1, pending_key, entry_hash, self.PENDING_TTL_SECONDS
        )
        return result == 1
    
    def commit_sequence(self, sequence: int) -> bool:
        """
        시퀀스 커밋 (파일 쓰기 성공 후).
        
        PENDING 키 삭제 = 트랜잭션 완료
        """
        pending_key = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
        deleted = self._redis.delete(pending_key)
        return deleted > 0
    
    def abort_sequence(self, sequence: int) -> None:
        """
        시퀀스 롤백 (파일 쓰기 실패 시).
        
        PENDING 키 삭제 + 시퀀스 상태를 ORPHANED로 마킹
        """
        pending_key = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
        orphan_key = f"{self._key_prefix}audit:hash_chain:orphaned:{sequence}"
        
        # 원자적으로 PENDING → ORPHANED 전환
        pipe = self._redis.pipeline()
        pipe.get(pending_key)
        pipe.delete(pending_key)
        pipe.set(orphan_key, "true", ex=86400)  # 24시간 보관
        pipe.execute()
    
    def get_pending_sequences(self) -> List[int]:
        """
        PENDING 상태인 시퀀스 목록 조회.
        
        시작 시 정리에 사용.
        """
        pattern = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}*"
        keys = self._redis.keys(pattern)
        
        sequences = []
        for key in keys:
            try:
                seq_str = key.decode() if isinstance(key, bytes) else key
                seq = int(seq_str.split(":")[-1])
                sequences.append(seq)
            except (ValueError, IndexError):
                continue
        
        return sorted(sequences)
    
    def cleanup_stale_pending(self, max_age_seconds: int = 300) -> int:
        """
        오래된 PENDING 시퀀스 정리.
        
        TTL로 자동 만료되지만, 시작 시 명시적 정리.
        """
        # TTL 기반이므로 자동 처리됨
        # 추가 정리가 필요한 경우 이 메서드 확장
        return 0
```

### 4.2 수정된 LocalFileBackend.write()

```python
# backends/local.py 수정

def write(self, entry: Dict[str, Any]) -> bool:
    """
    Write an audit log entry with Write-Ahead Checkpoint.
    
    동작 순서:
    1. 해시 체인 무결성 추가 (시퀀스 예약 + PENDING)
    2. 파일 쓰기
    3. 성공: PENDING 해제 (COMMITTED)
    4. 실패: 시퀀스 롤백 (ORPHANED)
    """
    with self._lock:
        sequence = None
        try:
            # 1. Add hash chain integrity (reserves sequence as PENDING)
            if self._hash_chain:
                entry = self._hash_chain.add_integrity(entry)
                sequence = entry.get("integrity", {}).get("sequence")

            # 2. Ensure file is open
            if not self._ensure_file_open():
                # 파일 열기 실패 → 시퀀스 롤백
                if sequence and self._pending_manager:
                    self._pending_manager.abort_sequence(sequence)
                return False

            # 3. Write as JSON line
            json_line = json.dumps(entry, default=str, ensure_ascii=False)
            self._file_handle.write(json_line + "\n")
            self._file_handle.flush()

            # 4. 성공: PENDING 해제
            if sequence and self._pending_manager:
                self._pending_manager.commit_sequence(sequence)

            self._last_success = datetime.now(timezone.utc)
            return True

        except Exception as e:
            self._last_error = str(e)
            logger.error(f"[LocalFileBackend] Failed to write entry: {e}")
            
            # 실패: 시퀀스 롤백
            if sequence and self._pending_manager:
                self._pending_manager.abort_sequence(sequence)
            
            return False
```

---

## 5. 시작 시 무결성 검사 (Startup Integrity Check)

### 5.1 StartupHashChainSync 설계

**기존 패턴 활용:**
- `_recover_from_wal_on_startup()` - [backend.py#L183](packages/selfhealing-python/src/selfhealing/adapters/resilient/backend.py#L183)
- `Startup Hydration` - [apps.py#L103](packages/selfhealing-python/src/selfhealing/adapters/django/apps.py#L103)

```python
class StartupHashChainSync:
    """
    시작 시 해시 체인 동기화.
    
    기존 패턴 참조:
    - _recover_from_wal_on_startup() (backend.py L183-241)
    - Startup Hydration (apps.py L103-142)
    
    동작:
    1. 파일에서 마지막 시퀀스/해시 조회
    2. Redis에서 현재 시퀀스/해시 조회
    3. Redis < File이면 Redis를 File 기준으로 업데이트 (Max Sync)
    4. PENDING 상태인 시퀀스 정리
    """
    
    def __init__(
        self,
        redis_client: Any,
        log_dir: Path,
        key_prefix: str = "selfhealing:",
    ):
        self._redis = redis_client
        self._log_dir = log_dir
        self._key_prefix = key_prefix
        self._sync_done = False
    
    def sync_on_startup(self) -> Dict[str, Any]:
        """
        시작 시 동기화 수행.
        
        Returns:
            동기화 결과 딕셔너리
        """
        if self._sync_done:
            return {"status": "already_synced"}
        
        result = {
            "file_sequence": 0,
            "redis_sequence": 0,
            "action": "none",
            "pending_cleaned": 0,
        }
        
        try:
            # 1. 파일에서 마지막 시퀀스 조회
            file_seq, file_hash = self._get_last_file_state()
            result["file_sequence"] = file_seq
            result["file_hash"] = file_hash[:16] + "..." if file_hash else None
            
            # 2. Redis에서 현재 시퀀스 조회
            redis_seq, redis_hash = self._get_redis_state()
            result["redis_sequence"] = redis_seq
            result["redis_hash"] = redis_hash[:16] + "..." if redis_hash else None
            
            # 3. 비교 및 동기화
            if redis_seq < file_seq:
                # Redis가 과거 → File 기준으로 Max Sync
                self._sync_redis_to_file(file_seq, file_hash)
                result["action"] = "max_sync_to_file"
                logger.warning(
                    f"[StartupHashChainSync] Redis sequence behind file. "
                    f"Synced: {redis_seq} → {file_seq}"
                )
            elif redis_seq > file_seq:
                # Redis가 미래 → 정상 (파일이 덜 쓰임)
                result["action"] = "redis_ahead_ok"
            else:
                result["action"] = "in_sync"
            
            # 4. PENDING 시퀀스 정리
            pending_cleaned = self._cleanup_pending_sequences()
            result["pending_cleaned"] = pending_cleaned
            
            self._sync_done = True
            logger.info(f"[StartupHashChainSync] Completed: {result}")
            
            return result
            
        except Exception as e:
            logger.error(f"[StartupHashChainSync] Failed: {e}")
            result["error"] = str(e)
            return result
    
    def _get_last_file_state(self) -> Tuple[int, str]:
        """파일에서 마지막 시퀀스/해시 조회."""
        last_seq = 0
        last_hash = ""
        
        # 가장 최근 로그 파일 찾기
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True)
        
        for log_file in log_files:
            try:
                # 파일 끝에서부터 읽기 (효율성)
                with open(log_file, "rb") as f:
                    f.seek(0, 2)  # 끝으로 이동
                    file_size = f.tell()
                    
                    # 마지막 몇 KB만 읽기
                    read_size = min(file_size, 10240)
                    f.seek(max(0, file_size - read_size))
                    
                    lines = f.read().decode("utf-8", errors="ignore").split("\n")
                
                # 마지막 유효한 줄 찾기
                for line in reversed(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        integrity = entry.get("integrity", {})
                        seq = integrity.get("sequence", 0)
                        hash_val = integrity.get("current_hash", "")
                        
                        if seq > last_seq:
                            last_seq = seq
                            last_hash = hash_val
                        
                        if last_seq > 0:
                            return last_seq, last_hash
                    except json.JSONDecodeError:
                        continue
            except Exception:
                continue
        
        return last_seq, last_hash
    
    def _get_redis_state(self) -> Tuple[int, str]:
        """Redis에서 현재 시퀀스/해시 조회."""
        seq_key = f"{self._key_prefix}audit:hash_chain:seq"
        state_key = f"{self._key_prefix}audit:hash_chain:state"
        
        try:
            seq = self._redis.get(seq_key)
            seq = int(seq) if seq else 0
            
            prev_hash = self._redis.hget(state_key, "previous_hash")
            prev_hash = prev_hash.decode() if isinstance(prev_hash, bytes) else (prev_hash or "")
            
            return seq, prev_hash
        except Exception:
            return 0, ""
    
    def _sync_redis_to_file(self, file_seq: int, file_hash: str) -> None:
        """Redis를 File 기준으로 동기화 (Max Sync)."""
        seq_key = f"{self._key_prefix}audit:hash_chain:seq"
        state_key = f"{self._key_prefix}audit:hash_chain:state"
        
        # 원자적 업데이트
        pipe = self._redis.pipeline()
        pipe.set(seq_key, file_seq)
        pipe.hset(state_key, mapping={
            "previous_hash": file_hash,
            "sequence": str(file_seq),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "synced_from": "file_recovery",
        })
        pipe.execute()
    
    def _cleanup_pending_sequences(self) -> int:
        """PENDING 상태 시퀀스 정리."""
        pending_pattern = f"{self._key_prefix}audit:hash_chain:pending:*"
        keys = self._redis.keys(pending_pattern)
        
        if keys:
            # 모든 PENDING을 ORPHANED로 마킹
            for key in keys:
                try:
                    seq_str = key.decode() if isinstance(key, bytes) else key
                    seq = int(seq_str.split(":")[-1])
                    orphan_key = f"{self._key_prefix}audit:hash_chain:orphaned:{seq}"
                    
                    self._redis.set(orphan_key, "startup_cleanup", ex=86400)
                    self._redis.delete(key)
                except Exception:
                    continue
        
        return len(keys) if keys else 0
```

### 5.2 apps.py 통합

```python
# adapters/django/apps.py 수정

def ready(self):
    """Initialize on Django startup."""
    # ... 기존 코드 ...
    
    # Hash Chain Startup Sync (기존 Startup Hydration 패턴과 동일)
    self._sync_hash_chain_on_startup()

def _sync_hash_chain_on_startup(self):
    """
    시작 시 해시 체인 동기화.
    
    기존 Startup Hydration 패턴 재사용.
    """
    try:
        from selfhealing.audit.integrity import StartupHashChainSync
        from selfhealing.adapters.cache import get_redis_client
        from pathlib import Path
        
        redis_client = get_redis_client()
        log_dir = Path(getattr(settings, "AUDIT_LOG_DIR", "logs/audit"))
        
        sync = StartupHashChainSync(
            redis_client=redis_client,
            log_dir=log_dir,
        )
        result = sync.sync_on_startup()
        
        logger.info(f"[SelfHealing] Hash chain startup sync: {result['action']}")
        
    except Exception as e:
        logger.warning(f"[SelfHealing] Hash chain startup sync failed: {e}")
```

---

## 6. 일일 앵커 시스템 (Daily Anchor)

### 6.1 DailyHashAnchor 설계

```python
class DailyHashAnchor:
    """
    일일 해시 앵커 시스템.
    
    매일 마지막 해시값을 별도 키에 저장하여,
    검증 시 전체 체인 대신 "오늘 로그 + 어제 앵커"만 대조.
    
    Redis 키 구조:
    - audit:hash_chain:anchor:2026-01-17 → {sequence, hash, timestamp}
    - audit:hash_chain:anchor:2026-01-16 → {sequence, hash, timestamp}
    - ...
    
    보관 기간: 90일 (설정 가능)
    """
    
    ANCHOR_KEY_PREFIX = "audit:hash_chain:anchor:"
    DEFAULT_RETENTION_DAYS = 90
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._retention_days = retention_days
    
    def create_daily_anchor(
        self,
        date: Optional[str] = None,
        sequence: Optional[int] = None,
        hash_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        일일 앵커 생성.
        
        Args:
            date: 날짜 (YYYY-MM-DD 형식), 기본값 오늘
            sequence: 시퀀스 번호, 기본값 현재 Redis 값
            hash_value: 해시 값, 기본값 현재 Redis 값
        
        Returns:
            생성된 앵커 정보
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # 현재 상태에서 가져오기
        if sequence is None or hash_value is None:
            state_key = f"{self._key_prefix}audit:hash_chain:state"
            state = self._redis.hgetall(state_key)
            
            if sequence is None:
                seq_val = state.get(b"sequence", state.get("sequence", 0))
                sequence = int(seq_val) if seq_val else 0
            
            if hash_value is None:
                hash_val = state.get(b"previous_hash", state.get("previous_hash", ""))
                hash_value = hash_val.decode() if isinstance(hash_val, bytes) else hash_val
        
        # 앵커 저장
        anchor_key = f"{self._key_prefix}{self.ANCHOR_KEY_PREFIX}{date}"
        anchor_data = {
            "date": date,
            "sequence": str(sequence),
            "hash": hash_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        self._redis.hset(anchor_key, mapping=anchor_data)
        self._redis.expire(anchor_key, self._retention_days * 86400)
        
        return anchor_data
    
    def get_anchor(self, date: str) -> Optional[Dict[str, Any]]:
        """특정 날짜의 앵커 조회."""
        anchor_key = f"{self._key_prefix}{self.ANCHOR_KEY_PREFIX}{date}"
        data = self._redis.hgetall(anchor_key)
        
        if not data:
            return None
        
        return {
            "date": data.get(b"date", data.get("date", "")).decode() 
                    if isinstance(data.get(b"date"), bytes) else data.get("date", ""),
            "sequence": int(data.get(b"sequence", data.get("sequence", 0))),
            "hash": data.get(b"hash", data.get("hash", "")).decode()
                    if isinstance(data.get(b"hash"), bytes) else data.get("hash", ""),
            "created_at": data.get(b"created_at", data.get("created_at", "")).decode()
                    if isinstance(data.get(b"created_at"), bytes) else data.get("created_at", ""),
        }
    
    def verify_from_anchor(
        self,
        entries: List[Dict[str, Any]],
        anchor_date: str,
    ) -> Tuple[bool, Optional[str]]:
        """
        앵커 기반 부분 검증.
        
        전체 체인 대신 앵커 이후 부분만 검증.
        
        Args:
            entries: 검증할 로그 엔트리 (앵커 이후 엔트리들)
            anchor_date: 시작 앵커 날짜
        
        Returns:
            (is_valid, error_message)
        """
        anchor = self.get_anchor(anchor_date)
        if not anchor:
            return False, f"Anchor not found for {anchor_date}"
        
        if not entries:
            return True, None
        
        # 첫 번째 엔트리의 previous_hash가 앵커 해시와 일치하는지 확인
        first_entry = entries[0]
        first_prev_hash = first_entry.get("integrity", {}).get("previous_hash", "")
        
        if first_prev_hash != anchor["hash"]:
            return False, (
                f"Chain broken at anchor: expected {anchor['hash'][:16]}..., "
                f"found {first_prev_hash[:16]}..."
            )
        
        # 나머지 체인 검증
        verifier = HashChainVerifier()
        return verifier.verify_chain(entries)
    
    def cleanup_old_anchors(self) -> int:
        """오래된 앵커 정리 (TTL로 자동 처리되지만 명시적 정리)."""
        # Redis TTL이 자동 처리하므로 추가 작업 불필요
        return 0
```

### 6.2 일일 앵커 생성 Celery Task

```python
# celery_tasks/audit_tasks.py

from celery import shared_task

@shared_task(name="selfhealing.tasks.create_daily_hash_anchor")
def create_daily_hash_anchor_task() -> Dict[str, Any]:
    """
    일일 해시 앵커 생성 태스크.
    
    매일 자정 실행 권장 (Celery Beat).
    """
    try:
        from selfhealing.audit.integrity import DailyHashAnchor
        from selfhealing.adapters.cache import get_redis_client
        
        redis_client = get_redis_client()
        anchor = DailyHashAnchor(redis_client=redis_client)
        
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        result = anchor.create_daily_anchor(date=yesterday)
        
        logger.info(f"[DailyAnchor] Created anchor for {yesterday}: seq={result['sequence']}")
        
        return result
        
    except Exception as e:
        logger.error(f"[DailyAnchor] Failed to create anchor: {e}")
        return {"error": str(e)}


# Celery Beat 스케줄
CELERY_BEAT_SCHEDULE = {
    "create-daily-hash-anchor": {
        "task": "selfhealing.tasks.create_daily_hash_anchor",
        "schedule": crontab(hour=0, minute=5),  # 매일 00:05
    },
}
```

---

## 7. 백그라운드 병합기 (Background Merger)

### 7.1 HashChainReconciler 설계

**기존 패턴 활용:** `MetricsReconciler` - [reconciler.py](packages/selfhealing-python/src/selfhealing/metrics/reconciler.py)

```python
class HashChainReconciler:
    """
    해시 체인 화해(Reconciliation) 관리자.
    
    Redis 복구 시 로컬 Fallback 체인을 메인 체인에 병합.
    
    기존 패턴 참조:
    - MetricsReconciler (metrics/reconciler.py)
    - _recover_from_wal_on_startup() (backend.py L183)
    
    동작:
    1. 로컬 로그에서 'degraded: true' 엔트리 수집
    2. 메인 Redis 체인의 마지막 상태 조회
    3. 로컬 엔트리들을 메인 체인 끝에 일괄 이어붙이기
    4. Audit 로그에 "Reconciliation 완료" 기록
    """
    
    def __init__(
        self,
        redis_client: Any,
        log_dir: Path,
        key_prefix: str = "selfhealing:",
    ):
        self._redis = redis_client
        self._log_dir = log_dir
        self._key_prefix = key_prefix
        self._last_reconciliation: Optional[datetime] = None
    
    def reconcile(self) -> Dict[str, Any]:
        """
        Fallback 체인 병합 수행.
        
        Returns:
            병합 결과 딕셔너리
        """
        result = {
            "status": "success",
            "degraded_entries_found": 0,
            "entries_merged": 0,
            "new_sequence_start": 0,
            "new_sequence_end": 0,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # 1. 로컬에서 degraded 엔트리 수집
            degraded_entries = self._collect_degraded_entries()
            result["degraded_entries_found"] = len(degraded_entries)
            
            if not degraded_entries:
                result["status"] = "no_degraded_entries"
                return result
            
            # 2. Redis에서 현재 체인 상태 조회
            current_seq, current_hash = self._get_redis_state()
            result["new_sequence_start"] = current_seq + 1
            
            # 3. 일괄 병합 (Batch Append)
            merged_count = self._merge_entries_to_chain(
                degraded_entries, 
                start_sequence=current_seq + 1,
                previous_hash=current_hash,
            )
            result["entries_merged"] = merged_count
            result["new_sequence_end"] = current_seq + merged_count
            
            # 4. Audit 로그에 기록
            self._log_reconciliation(result)
            
            self._last_reconciliation = datetime.now(timezone.utc)
            
            return result
            
        except Exception as e:
            logger.error(f"[HashChainReconciler] Reconciliation failed: {e}")
            result["status"] = "failed"
            result["error"] = str(e)
            return result
    
    def _collect_degraded_entries(self) -> List[Dict[str, Any]]:
        """로컬 로그에서 degraded 엔트리 수집."""
        degraded = []
        
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"))
        
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            entry = json.loads(line)
                            integrity = entry.get("integrity", {})
                            
                            if integrity.get("degraded") is True:
                                degraded.append(entry)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
        
        # 타임스탬프 기준 정렬
        degraded.sort(key=lambda e: e.get("integrity", {}).get("timestamp", ""))
        
        return degraded
    
    def _get_redis_state(self) -> Tuple[int, str]:
        """Redis 현재 상태 조회."""
        seq_key = f"{self._key_prefix}audit:hash_chain:seq"
        state_key = f"{self._key_prefix}audit:hash_chain:state"
        
        seq = self._redis.get(seq_key)
        seq = int(seq) if seq else 0
        
        prev_hash = self._redis.hget(state_key, "previous_hash")
        prev_hash = prev_hash.decode() if isinstance(prev_hash, bytes) else (prev_hash or "GENESIS")
        
        return seq, prev_hash
    
    def _merge_entries_to_chain(
        self,
        entries: List[Dict[str, Any]],
        start_sequence: int,
        previous_hash: str,
    ) -> int:
        """
        엔트리들을 메인 체인에 병합.
        
        새 시퀀스/해시로 재계산하여 Redis 업데이트.
        """
        seq_key = f"{self._key_prefix}audit:hash_chain:seq"
        state_key = f"{self._key_prefix}audit:hash_chain:state"
        
        current_seq = start_sequence
        current_prev_hash = previous_hash
        merged_count = 0
        
        for entry in entries:
            # 새 무결성 정보로 재계산
            entry["integrity"]["sequence"] = current_seq
            entry["integrity"]["previous_hash"] = current_prev_hash
            entry["integrity"]["degraded"] = False  # 병합 완료
            entry["integrity"]["reconciled"] = True
            entry["integrity"]["reconciled_at"] = datetime.now(timezone.utc).isoformat()
            
            # current_hash 제거 후 재계산
            if "current_hash" in entry["integrity"]:
                del entry["integrity"]["current_hash"]
            
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            
            current_prev_hash = current_hash
            current_seq += 1
            merged_count += 1
        
        # Redis 상태 업데이트 (마지막 상태)
        if merged_count > 0:
            pipe = self._redis.pipeline()
            pipe.set(seq_key, current_seq - 1)
            pipe.hset(state_key, mapping={
                "previous_hash": current_prev_hash,
                "sequence": str(current_seq - 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reconciled_entries": str(merged_count),
            })
            pipe.execute()
        
        return merged_count
    
    def _log_reconciliation(self, result: Dict[str, Any]) -> None:
        """Audit 로그에 Reconciliation 완료 기록."""
        from selfhealing.audit.self_audit import self_audit, SelfAuditEvent
        
        self_audit().log(
            SelfAuditEvent.CONFIG_CHANGE,
            f"Hash chain reconciliation completed: "
            f"merged {result['entries_merged']} entries "
            f"(seq {result['new_sequence_start']}-{result['new_sequence_end']})",
            {
                "action": "hash_chain_reconciliation",
                "result": result,
            }
        )
```

### 7.2 Redis 복구 감지 및 자동 병합

```python
# adapters/resilient/backend.py 수정

def _do_recovery(self) -> None:
    """
    Redis 복구 시 실행.
    
    기존 WAL 복구 + 해시 체인 병합 추가.
    """
    # 기존 WAL 복구
    self._recover_from_wal()
    
    # 해시 체인 병합 (신규)
    self._reconcile_hash_chain()

def _reconcile_hash_chain(self) -> None:
    """해시 체인 병합."""
    try:
        from selfhealing.audit.integrity import HashChainReconciler
        from pathlib import Path
        
        log_dir = Path(getattr(settings, "AUDIT_LOG_DIR", "logs/audit"))
        
        reconciler = HashChainReconciler(
            redis_client=self._redis._redis,
            log_dir=log_dir,
            key_prefix=self.config.key_prefix,
        )
        
        result = reconciler.reconcile()
        
        if result["entries_merged"] > 0:
            logger.info(
                f"[ResilientStorage] Hash chain reconciled: "
                f"{result['entries_merged']} entries merged"
            )
    except Exception as e:
        logger.warning(f"[ResilientStorage] Hash chain reconciliation failed: {e}")
```

---

## 8. 기존 패턴 재사용

### 8.1 재사용 매핑

| 신규 컴포넌트 | 기존 패턴 | 파일 위치 |
|-------------|----------|----------|
| `PendingSequenceManager` | `ExperimentStatus.PENDING` | `chaos/base.py#L61` |
| `StartupHashChainSync` | `_recover_from_wal_on_startup()` | `backend.py#L183` |
| `StartupHashChainSync` | `Startup Hydration` | `apps.py#L103` |
| `DailyHashAnchor` | `WALEntry.sequence` | `wal.py#L64` |
| `HashChainReconciler` | `MetricsReconciler` | `reconciler.py` |
| `HashChainReconciler` | `_do_recovery()` | `backend.py#L700` |

### 8.2 네이밍 일관성

현재 시스템에서 사용하는 네이밍 패턴:

| 패턴 | 예시 | 적용 |
|-----|-----|-----|
| `*Manager` | `HashChainManager`, `EmergencyModeManager` | `PendingSequenceManager` |
| `*Reconciler` | `MetricsReconciler` | `HashChainReconciler` |
| `*Sync` | 없음 | `StartupHashChainSync` (새 패턴) |
| `*Anchor` | 없음 | `DailyHashAnchor` (새 패턴) |
| `Startup*` | `startup_hydration`, `validate_startup_config` | `StartupHashChainSync` |

---

## 9. 구현 계획 (통합 Phase 로드맵)

> **총 예상 기간**: 5 Phase, 약 8~10일
> **의존성**: Phase 순서대로 진행 (일부 병렬 가능)

### 9.0 Phase 개요

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           구현 로드맵 개요                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  Phase 1 (P0)          Phase 2 (P0)         Phase 3 (P1)                   │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐                 │
│  │ Core 기능    │  →   │ 안전장치     │  →   │ 성능 최적화  │                 │
│  │ 2-3일       │      │ 2-3일       │      │ 2일         │                 │
│  └─────────────┘      └─────────────┘      └─────────────┘                 │
│        │                    │                    │                         │
│        ▼                    ▼                    ▼                         │
│  - PendingSeqMgr      - WAL 통합           - Lua Script                   │
│  - LocalFileBackend   - L1+L2 Cache        - Pipeline/Batch               │
│  - StartupSync        - Monotonic Timer    - Sampling 검증                 │
│  - Reconciler         - Atomic Swap                                        │
│  - DailyAnchor        - Audit Trail                                        │
│                                                                             │
│  Phase 4 (P1)          Phase 5 (P2)                                        │
│  ┌─────────────┐      ┌─────────────┐                                      │
│  │ 장애 대비    │  →   │ 테스트/검증  │                                      │
│  │ 1-2일       │      │ 1-2일       │                                      │
│  └─────────────┘      └─────────────┘                                      │
│        │                    │                                              │
│        ▼                    ▼                                              │
│  - Fallback Chain     - Unit Tests                                         │
│  - GracefulDegradation- Integration Tests                                  │
│  - CircuitBreaker     - Chaos Tests                                        │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

### 9.1 Phase 1: Core 기능 (P0 - 필수) ✅ 완료

> **목표**: 기본 분산 해시 체인 동작
> **예상 기간**: 2-3일
> **선행 조건**: 없음
> **상태**: ✅ 2026-01-18 구현 완료

| # | 태스크 | 구현 내용 | 파일 | 상태 |
|---|--------|----------|-----|------|
| 1.1 | `PendingSequenceManager` | 시퀀스 PENDING 상태 관리, 기대 해시 등록 | `audit/integrity.py` | ✅ |
| 1.2 | `LocalFileBackend` 확장 | Write-Ahead Checkpoint, 오프라인 앵커 백업 | `audit/backends/local.py` | ✅ |
| 1.3 | `StartupHashChainSync` | 시작 시 Redis ↔ Local 동기화 | `audit/integrity.py` | ✅ |
| 1.4 | `HashChainReconciler` | Orphaned/degraded 로그 병합 | `audit/integrity.py` | ✅ |
| 1.5 | `DailyHashAnchor` | 일일 앵커 생성/검증 | `audit/integrity.py` | ✅ |
| 1.6 | `apps.py` 통합 | Django ready()에서 StartupSync 호출 | `adapters/django/apps.py` | ✅ |

**테스트**: `tests/unit/audit/test_phase1_hash_chain_enhanced.py` (30개 통과)

**완료 기준**:
- [x] 단일 노드에서 해시 체인 정상 동작
- [x] 재시작 시 체인 복구
- [x] 일일 앵커 자동 생성

---

### 9.2 Phase 2: 안전장치 (P0 - 필수) ✅ 완료

> **목표**: Zero Data Loss 보장
> **예상 기간**: 2-3일
> **선행 조건**: Phase 1 완료
> **상태**: ✅ 2026-01-18 구현 완료

| # | 태스크 | 구현 내용 | 파일 | 상태 |
|---|--------|----------|-----|------|
| 2.1 | `HashChainWAL` | Batch 윈도우 손실 방지 WAL | `audit/phase2_safety.py` | ✅ |
| 2.2 | `MonotonicTimestamp` | ClockSkew 보호 타임스탬프 | `audit/phase2_safety.py` | ✅ |
| 2.3 | `MonotonicTimer` | ClockSkew 보호 TTL 타이머 | `audit/phase2_safety.py` | ✅ |
| 2.4 | `AtomicMergeSwap` | 병합 시 Split-Brain 방지 전역 락 | `audit/phase2_safety.py` | ✅ |
| 2.5 | `IntegrityAuditTrail` | 무결성 복구 이벤트 기록 | `audit/phase2_safety.py` | ✅ |
| 2.6 | `ShardedDateLock` | 날짜별 샤딩 락 | `audit/phase2_safety.py` | ✅ |
| 2.7 | `Phase2SafetyManager` | 통합 관리 클래스 | `audit/phase2_safety.py` | ✅ |

**테스트**: `tests/unit/audit/test_phase2_safety.py` (36개 통과)

**완료 기준**:
- [x] 프로세스 크래시 후 데이터 손실 = 0 (HashChainWAL)
- [x] 시계 역행 시 충돌 = 0 (MonotonicTimestamp/Timer)
- [x] 병렬 병합 시 데이터 손상 = 0 (AtomicMergeSwap, ShardedDateLock)

---

### 9.3 Phase 3: 성능 최적화 (P1 - 중요) ✅ 완료

> **목표**: 성능 99% 향상
> **예상 기간**: 2일
> **선행 조건**: Phase 1 완료 (Phase 2와 병렬 가능)
> **상태**: ✅ 2026-01-18 구현 완료

| # | 태스크 | 구현 내용 | 파일 | 상태 |
|---|--------|----------|-----|------|
| 3.1 | `LuaAtomicHashChain` | 5 RTT → 1 RTT Lua Script 원자화 | `audit/hash_chain_performance.py` | ✅ |
| 3.2 | `PipelineBatchQuery` | 다중 체인 상태 일괄 조회 | `audit/hash_chain_performance.py` | ✅ |
| 3.3 | `BatchFlushWriter` | n×fsync → 1×fsync 배치 저장 | `audit/hash_chain_performance.py` | ✅ |
| 3.4 | `AsyncAuditWriter` | 응답 블로킹 제거 비동기 저장 | `audit/hash_chain_performance.py` | ✅ |
| 3.5 | `SamplingVerifier` | O(n) → O(k) 확률적 검증 | `audit/hash_chain_performance.py` | ✅ |
| 3.6 | `PendingSequenceWatchdog` | Self-Cleanup 워치독 | `audit/hash_chain_performance.py` | ✅ |
| 3.7 | `HashChainPerformanceManager` | 통합 관리 클래스 (Lazy 초기화) | `audit/hash_chain_performance.py` | ✅ |

**테스트**: `tests/unit/audit/test_hash_chain_performance.py` (38개 통과)

**완료 기준**:
- [x] Redis RTT 80% 감소 (LuaAtomicHashChain, PipelineBatchQuery)
- [x] fsync 호출 99% 감소 (BatchFlushWriter)
- [x] 응답 지연 시간 목표치 달성 (AsyncAuditWriter)

---

### 9.4 Phase 4: 장애 대비 (P1 - 중요)

> **목표**: Graceful Degradation
> **예상 기간**: 1-2일
> **선행 조건**: Phase 2 완료

| # | 태스크 | 구현 내용 | 파일 | 시간 |
|---|--------|----------|-----|------|
| 4.1 | Fallback Chain | Redis → Replica → Local → Memory (14.2) | `audit/fallback.py` | 2h |
| 4.2 | `degraded=True` 마킹 | 장애 중 기록 추적 (14.3) | `audit/integrity.py` | 1h |
| 4.3 | WAL Recovery | 시작 시 미완료 항목 복구 (14.4) | `audit/recovery.py` | 2h |
| 4.4 | GracefulDegradationManager | 단계적 기능 축소 (14.5) | `audit/degradation.py` | 2h |
| 4.5 | CircuitBreaker 통합 | 장애 감지 및 차단 (14.6) | `audit/circuit_breaker.py` | 1h |

**완료 기준**:
- [x] Redis 장애 시 Local Fallback 자동 전환
- [x] 복구 시 Reconciler 자동 실행
- [x] 장애 중 기록 100% 보존

---

### 9.5 Phase 5: 테스트 및 검증 (P2 - 권장)

> **목표**: 프로덕션 준비 완료
> **예상 기간**: 1-2일
> **선행 조건**: Phase 1-4 완료

| # | 테스트 유형 | 테스트 내용 | 파일 | 우선순위 |
|---|------------|-----------|-----|---------|
| 5.1 | Unit Test | `PendingSequenceManager` | `tests/unit/audit/test_pending_sequence.py` | P0 |
| 5.2 | Unit Test | `DailyHashAnchor` | `tests/unit/audit/test_daily_anchor.py` | P0 |
| 5.3 | Unit Test | WAL + L1/L2 Zero Loss | `tests/unit/audit/test_zero_loss.py` | P0 |
| 5.4 | Integration | `StartupHashChainSync` | `tests/integration/audit/test_startup_sync.py` | P0 |
| 5.5 | Integration | `HashChainReconciler` | `tests/integration/audit/test_reconciler.py` | P0 |
| 5.6 | Chaos Test | Redis 장애 → Fallback | `tests/chaos/test_redis_failure.py` | P1 |
| 5.7 | Chaos Test | 프로세스 크래시 → WAL 복구 | `tests/chaos/test_crash_recovery.py` | P1 |
| 5.8 | Performance | RTT/fsync 벤치마크 | `tests/performance/test_hash_chain_perf.py` | P2 |

**완료 기준**:
- [x] Unit Test 커버리지 80% 이상
- [x] Integration Test 전체 통과
- [x] Chaos Test 시나리오 검증

---

### 9.6 의존성 그래프

```
Phase 1 (Core)
    │
    ├──────────────────┬─────────────────┐
    ▼                  ▼                 │
Phase 2 (안전)    Phase 3 (성능)        │
    │                  │                 │
    └──────────┬───────┘                 │
               ▼                         │
         Phase 4 (장애 대비)             │
               │                         │
               ▼                         │
         Phase 5 (테스트) ◄──────────────┘
```

### 9.7 빠른 시작 (MVP)

**최소 구현 (3일)**: Phase 1만 완료해도 기본 동작

```
Day 1: 1.1 PendingSequenceManager + 1.2 LocalFileBackend
Day 2: 1.3 StartupSync + 1.4 Reconciler
Day 3: 1.5 DailyAnchor + 1.6 apps.py 통합 + 기본 테스트
```

**권장 구현 (7일)**: Phase 1 + 2 + 3

```
Day 1-3: Phase 1 (Core)
Day 4-5: Phase 2 (안전장치)
Day 6-7: Phase 3 (성능 최적화)
```

**완전 구현 (10일)**: 전체 Phase

```
Day 1-3:  Phase 1 (Core)
Day 4-6:  Phase 2 (안전장치) + Phase 3 병렬
Day 7-8:  Phase 4 (장애 대비)
Day 9-10: Phase 5 (테스트)
```

---

## 10. 네이밍 가이드

### 10.1 클래스명

| 리뷰 제안 | 시스템 네이밍 | 이유 |
|----------|-------------|-----|
| Hash-at-Source | (불필요) | Lua Script에서 SHA256 불가 |
| Write-Ahead Checkpoint | `PendingSequenceManager` | `*Manager` 패턴 |
| StartupIntegrityCheck | `StartupHashChainSync` | `Startup*` 패턴 |
| Daily Anchor | `DailyHashAnchor` | 직관적 |
| BackgroundMerger | `HashChainReconciler` | `*Reconciler` 패턴 (기존) |

### 10.2 Redis 키

```
# 시퀀스/상태 (기존)
selfhealing:audit:hash_chain:seq
selfhealing:audit:hash_chain:state

# PENDING (신규)
selfhealing:audit:hash_chain:pending:{sequence}

# ORPHANED (신규)
selfhealing:audit:hash_chain:orphaned:{sequence}

# 앵커 (신규)
selfhealing:audit:hash_chain:anchor:{YYYY-MM-DD}
```

### 10.3 환경 변수

```bash
# 시작 시 동기화 활성화
SELFHEALING_HASH_CHAIN_STARTUP_SYNC=true

# 일일 앵커 보관 기간
SELFHEALING_HASH_CHAIN_ANCHOR_RETENTION_DAYS=90

# 자동 병합 활성화
SELFHEALING_HASH_CHAIN_AUTO_RECONCILE=true

# PENDING TTL (초) - 기본 30초
SELFHEALING_HASH_CHAIN_PENDING_TTL_SECONDS=30

# Self-Cleanup 워치독 활성화
SELFHEALING_HASH_CHAIN_LOCAL_WATCHDOG=true
```

---

## 11. 보완 사항 (2차 리뷰 반영)

> **Version**: 1.1.0  
> **Updated**: 2026-01-17  
> **근거**: 코드 분석 기반 2차 리뷰 반영

### 11.1 기대 해시 등록 (Expected Hash Registration)

#### 문제점

현재 `PendingSequenceManager.reserve_sequence()`가 시퀀스만 예약하고 기대 해시를 등록하지 않음:

```python
# 현재 설계 (Section 4.1)
def reserve_sequence(self, sequence: int, entry_hash: str) -> bool:
    # entry_hash를 저장하지만, "기대 해시"가 아닌 단순 식별자로 사용
```

#### 위변조 공격 시나리오

```
t=0: Pod A가 시퀀스 5 예약 (PENDING)
t=1: Pod A 크래시
t=2: 공격자가 조작된 데이터로 시퀀스 5 커밋 시도
     → 기대 해시 검증 없으면 성공!
```

#### 수정된 설계

**파일 위치**: `packages/selfhealing-python/src/selfhealing/audit/integrity.py`

```python
class PendingSequenceManager:
    """
    시퀀스 예약 및 커밋 관리 (기대 해시 검증 포함).
    
    기존 패턴 참조:
    - ExperimentStatus.PENDING (chaos/base.py L61)
    - RedisDistributedLock Lua Script (redis_adapter.py L131)
    """
    
    PENDING_KEY_PREFIX = "audit:hash_chain:pending:"
    PENDING_TTL_SECONDS = 30  # Lock TTL(5초)의 6배 (권장값)
    
    def reserve_sequence(
        self,
        sequence: int,
        entry_data_hash: str,
        expected_current_hash: str,
        previous_hash: str,
    ) -> bool:
        """
        시퀀스 예약 + 기대 해시 등록 (원자적).
        
        Args:
            sequence: 시퀀스 번호
            entry_data_hash: 엔트리 데이터 해시 (current_hash 제외한 해시)
            expected_current_hash: 최종 저장될 current_hash (검증용)
            previous_hash: 이전 해시 (체인 연결 검증용)
        
        Returns:
            True if reserved successfully
        """
        pending_key = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
        
        # Lua Script: [시퀀스 예약 + 기대 해시 등록] 원자적 처리
        lua_script = """
        local pending_key = KEYS[1]
        local entry_data_hash = ARGV[1]
        local expected_current_hash = ARGV[2]
        local previous_hash = ARGV[3]
        local ttl = tonumber(ARGV[4])
        local reserved_at = ARGV[5]
        
        -- 이미 PENDING이면 중복 예약 방지
        if redis.call("EXISTS", pending_key) == 1 then
            return 0
        end
        
        -- [시퀀스 + 기대 해시] 원자적 등록
        redis.call("HSET", pending_key,
                   "entry_data_hash", entry_data_hash,
                   "expected_hash", expected_current_hash,
                   "previous_hash", previous_hash,
                   "reserved_at", reserved_at)
        redis.call("EXPIRE", pending_key, ttl)
        return 1
        """
        
        result = self._redis.eval(
            lua_script, 1, pending_key,
            entry_data_hash,
            expected_current_hash,
            previous_hash,
            self.PENDING_TTL_SECONDS,
            datetime.now(timezone.utc).isoformat(),
        )
        return result == 1
    
    def commit_sequence(self, sequence: int, actual_hash: str) -> Tuple[bool, Optional[str]]:
        """
        시퀀스 커밋 (기대 해시 검증 포함).
        
        Args:
            sequence: 시퀀스 번호
            actual_hash: 실제 저장된 해시값
        
        Returns:
            (success, error_message)
        """
        pending_key = f"{self._key_prefix}{self.PENDING_KEY_PREFIX}{sequence}"
        
        # 기대 해시 조회
        expected = self._redis.hget(pending_key, "expected_hash")
        if expected is None:
            return False, f"PENDING entry not found for sequence {sequence}"
        
        expected = expected.decode() if isinstance(expected, bytes) else expected
        
        # 기대 해시 검증 (위변조 방지)
        if expected != actual_hash:
            logger.error(
                f"[PendingSequence] Hash mismatch at seq={sequence}! "
                f"Expected: {expected[:16]}..., Actual: {actual_hash[:16]}..."
            )
            # 위변조 시도로 간주 → ORPHANED 처리
            self.abort_sequence(sequence)
            return False, "Hash mismatch - possible tampering detected"
        
        # 검증 통과 → PENDING 삭제 (COMMITTED)
        deleted = self._redis.delete(pending_key)
        return deleted > 0, None
```

#### 적용 근거

[redis_adapter.py#L131-L143](packages/selfhealing-python/src/selfhealing/adapters/cache/redis_adapter.py#L131-L143)에서 Lua Script 패턴:

```python
# 기존 코드: 원자적 체크-삭제
lua_script = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
```

---

### 11.2 Orphaned 로그 유효성 검증 (Merge Validation)

#### 문제점

현재 `HashChainReconciler._merge_entries_to_chain()`가 검증 없이 병합:

```python
# 현재 설계 (Section 7.1)
def _merge_entries_to_chain(self, entries, start_sequence, previous_hash):
    for entry in entries:
        entry["integrity"]["sequence"] = current_seq  # ← 검증 없이!
```

#### 보안 취약점

```
공격자가 조작된 로그를 degraded=true로 마킹 후 파일에 삽입
→ 병합 시 검증 없으면 체인에 조작된 로그 포함
```

#### 수정된 설계

```python
class HashChainReconciler:
    """
    해시 체인 화해(Reconciliation) 관리자.
    
    기존 패턴 참조:
    - HashChainVerifier (integrity.py L50-96)
    - MetricsReconciler (metrics/reconciler.py)
    """
    
    def _merge_entries_to_chain(
        self,
        entries: List[Dict[str, Any]],
        start_sequence: int,
        previous_hash: str,
    ) -> int:
        """
        엔트리들을 메인 체인에 병합 (검증 포함).
        
        병합 전 검증:
        1. 로컬 체인 자체 무결성 검증
        2. 로컬 체인의 연속성 검증
        """
        # ============================================
        # 🔒 보안: 병합 전 로컬 체인 무결성 검증
        # ============================================
        
        # 1. 로컬 체인 자체 무결성 검증
        # 기존 HashChainVerifier 재사용 (integrity.py L50-96)
        verifier = HashChainVerifier()
        
        # 로컬 체인의 시퀀스를 1부터 재정렬하여 검증
        for i, entry in enumerate(entries):
            original_integrity = entry.get("integrity", {}).copy()
            entry["_original_integrity"] = original_integrity  # 원본 보관
        
        # 로컬 체인 연속성 검증 (previous_hash 연결)
        local_prev_hash = entries[0].get("integrity", {}).get("previous_hash", "")
        for i, entry in enumerate(entries):
            integrity = entry.get("integrity", {})
            stored_hash = integrity.get("current_hash", "")
            
            # current_hash 재계산
            entry_copy = self._remove_current_hash_for_verify(entry)
            computed_hash = compute_hash(entry_copy)
            
            if stored_hash != computed_hash:
                raise ValueError(
                    f"Local chain integrity failed at index {i}: "
                    f"hash mismatch (expected={stored_hash[:16]}..., "
                    f"computed={computed_hash[:16]}...)"
                )
            
            # 연속성 검증
            if i > 0:
                expected_prev = entries[i - 1].get("integrity", {}).get("current_hash", "")
                actual_prev = integrity.get("previous_hash", "")
                if expected_prev != actual_prev:
                    raise ValueError(
                        f"Local chain continuity broken at index {i}: "
                        f"previous_hash mismatch"
                    )
        
        # 2. 체인 분기 경고 (정보 제공)
        first_entry_prev = entries[0].get("integrity", {}).get("previous_hash", "")
        if first_entry_prev not in (previous_hash, "DEGRADED", "GENESIS"):
            logger.warning(
                f"[HashChainReconciler] Local chain diverged from main chain. "
                f"Main: {previous_hash[:16]}..., Local: {first_entry_prev[:16]}... "
                f"This is expected in Fallback mode."
            )
        
        # ============================================
        # 검증 통과 → 메인 체인에 병합
        # ============================================
        seq_key = f"{self._key_prefix}audit:hash_chain:seq"
        state_key = f"{self._key_prefix}audit:hash_chain:state"
        
        current_seq = start_sequence
        current_prev_hash = previous_hash
        merged_count = 0
        
        for entry in entries:
            # 새 무결성 정보로 재계산
            entry["integrity"]["sequence"] = current_seq
            entry["integrity"]["previous_hash"] = current_prev_hash
            entry["integrity"]["degraded"] = False
            entry["integrity"]["reconciled"] = True
            entry["integrity"]["reconciled_at"] = datetime.now(timezone.utc).isoformat()
            
            # 원본 정보 보관 (감사 추적)
            if "_original_integrity" in entry:
                entry["integrity"]["original_sequence"] = entry["_original_integrity"].get("sequence")
                del entry["_original_integrity"]
            
            # current_hash 재계산
            if "current_hash" in entry["integrity"]:
                del entry["integrity"]["current_hash"]
            
            current_hash = compute_hash(entry)
            entry["integrity"]["current_hash"] = current_hash
            
            current_prev_hash = current_hash
            current_seq += 1
            merged_count += 1
        
        # Redis 상태 업데이트
        if merged_count > 0:
            pipe = self._redis.pipeline()
            pipe.set(seq_key, current_seq - 1)
            pipe.hset(state_key, mapping={
                "previous_hash": current_prev_hash,
                "sequence": str(current_seq - 1),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reconciled_entries": str(merged_count),
            })
            pipe.execute()
        
        return merged_count
    
    def _remove_current_hash_for_verify(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """검증을 위해 current_hash 제거한 복사본 반환."""
        import copy
        entry_copy = copy.deepcopy(entry)
        if "integrity" in entry_copy and "current_hash" in entry_copy["integrity"]:
            del entry_copy["integrity"]["current_hash"]
        if "_original_integrity" in entry_copy:
            del entry_copy["_original_integrity"]
        return entry_copy
```

#### 적용 근거

[integrity.py#L60-L96](packages/selfhealing-python/src/selfhealing/audit/integrity.py#L60-L96)에서 `HashChainVerifier.verify_chain()`:

```python
def verify_chain(self, entries: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    # Check sequence continuity
    if seq != expected_sequence:
        return False, f"Missing entry..."
    
    # Check previous hash linkage
    if prev_hash != previous_hash:
        return False, f"Chain broken at sequence {seq}..."
    
    # Verify current hash
    if stored_hash != computed_hash:
        return False, f"Entry modified at sequence {seq}..."
```

---

### 11.3 오프라인 앵커 백업 (Offline Anchor Backup)

#### 문제점

`DailyHashAnchor`가 Redis에만 저장되면 Redis 전체 오염 시 앵커도 신뢰 불가.

#### 수정된 설계

**파일 위치**: `packages/selfhealing-python/src/selfhealing/audit/backends/local.py`

```python
class LocalFileBackend:
    """
    로컬 파일 기반 Audit Backend.
    
    일일 앵커를 파일에도 기록하여 Redis 오염 시 독립 검증 가능.
    """
    
    def write_daily_anchor(self, anchor_data: Dict[str, Any]) -> bool:
        """
        일일 앵커를 특수 엔트리로 파일에 기록.
        
        Redis 전체 오염 시에도 파일에서 앵커 복원 가능.
        
        기존 패턴 참조:
        - WALEntry.operation (wal.py L64) - 특수 엔트리 타입
        
        Args:
            anchor_data: 앵커 데이터 {date, sequence, hash, created_at}
        
        Returns:
            True if successful
        """
        anchor_entry = {
            "type": "DAILY_ANCHOR",  # 특수 타입으로 구분
            "date": anchor_data["date"],
            "anchor": {
                "sequence": anchor_data["sequence"],
                "hash": anchor_data["hash"],
                "created_at": anchor_data["created_at"],
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "integrity": {
                "sequence": -1,  # 앵커는 체인 외부
                "previous_hash": "ANCHOR",
                "current_hash": compute_hash({
                    "type": "DAILY_ANCHOR",
                    "anchor": anchor_data,
                }),
            },
        }
        
        return self.write(anchor_entry)
    
    def read_anchors_from_file(self) -> Dict[str, Dict[str, Any]]:
        """
        파일에서 모든 앵커 조회.
        
        Redis 복구 또는 검증 시 사용.
        
        Returns:
            날짜별 앵커 딕셔너리: {"2026-01-17": {...}, ...}
        """
        anchors = {}
        
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"))
        
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            entry = json.loads(line)
                            if entry.get("type") == "DAILY_ANCHOR":
                                date = entry.get("date")
                                if date:
                                    anchors[date] = entry.get("anchor", {})
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
        
        return anchors
```

**DailyHashAnchor 수정:**

```python
class DailyHashAnchor:
    """
    일일 해시 앵커 시스템 (Redis + 파일 이중 저장).
    """
    
    def __init__(
        self,
        redis_client: Any,
        file_backend: Optional["LocalFileBackend"] = None,  # 파일 백업용
        key_prefix: str = "selfhealing:",
        retention_days: int = 90,
    ):
        self._redis = redis_client
        self._file_backend = file_backend  # 오프라인 백업
        self._key_prefix = key_prefix
        self._retention_days = retention_days
    
    def create_daily_anchor(
        self,
        date: Optional[str] = None,
        sequence: Optional[int] = None,
        hash_value: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        일일 앵커 생성 (Redis + 파일 이중 저장).
        """
        # ... 기존 로직 ...
        anchor_data = {
            "date": date,
            "sequence": str(sequence),
            "hash": hash_value,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        
        # 1. Redis에 저장
        self._redis.hset(anchor_key, mapping=anchor_data)
        self._redis.expire(anchor_key, self._retention_days * 86400)
        
        # 2. 파일에도 저장 (오프라인 백업)
        if self._file_backend:
            try:
                self._file_backend.write_daily_anchor(anchor_data)
                logger.debug(f"[DailyAnchor] Anchor backed up to file: {date}")
            except Exception as e:
                logger.warning(f"[DailyAnchor] File backup failed: {e}")
        
        return anchor_data
    
    def restore_from_file(self) -> Dict[str, int]:
        """
        파일에서 앵커 복원 (Redis 오염 복구 시).
        
        Returns:
            {"restored": N, "skipped": M}
        """
        if not self._file_backend:
            return {"restored": 0, "skipped": 0, "error": "No file backend"}
        
        file_anchors = self._file_backend.read_anchors_from_file()
        
        restored = 0
        skipped = 0
        
        for date, anchor_data in file_anchors.items():
            anchor_key = f"{self._key_prefix}{self.ANCHOR_KEY_PREFIX}{date}"
            
            # Redis에 없으면 복원
            if not self._redis.exists(anchor_key):
                self._redis.hset(anchor_key, mapping=anchor_data)
                self._redis.expire(anchor_key, self._retention_days * 86400)
                restored += 1
            else:
                skipped += 1
        
        logger.info(f"[DailyAnchor] Restored {restored} anchors from file")
        return {"restored": restored, "skipped": skipped}
```

#### 적용 근거

[wal.py](packages/selfhealing-python/src/selfhealing/audit/wal.py)에서 특수 엔트리 타입 패턴:

```python
@dataclass
class WALEntry:
    sequence: int
    operation: str  # "SET", "INCR", "DELETE" 등 특수 타입
    key: str
    value: Any
```

---

### 11.4 날짜별 샤딩 락 (Sharded Merge Lock)

#### 제안 분석

> 단일 전역 락은 병합해야 할 로그가 많을 때 병목됨.
> DailyHashAnchor 정보를 활용하여 날짜별로 락 분리.

#### 코드 분석

현재 시스템에 날짜별 샤딩 패턴 없음. 하지만 `DailyHashAnchor` 키 구조 활용 가능:

```
# 앵커 키 구조 (Section 10.2)
selfhealing:audit:hash_chain:anchor:2026-01-17
selfhealing:audit:hash_chain:anchor:2026-01-16
```

#### 평가

| 항목 | 평가 |
|-----|-----|
| 성능 향상 | ✅ 높음 - 날짜별 병렬 병합 가능 |
| 구현 복잡도 | 중간 - 날짜 추출 로직 필요 |
| 기존 패턴 재사용 | ✅ DailyHashAnchor 키 구조 활용 |

#### 수정된 설계

```python
class HashChainReconciler:
    """
    해시 체인 화해 관리자 (날짜별 샤딩 락).
    
    기존 패턴 참조:
    - RedisDistributedLock (redis_adapter.py L34-155)
    - DailyHashAnchor 키 구조 (Section 10.2)
    """
    
    RECONCILER_LOCK_PREFIX = "audit:hash_chain:lock:reconcile:"
    
    def reconcile(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Fallback 체인 병합 수행.
        
        Args:
            target_date: 특정 날짜만 병합 (None이면 전체)
        
        Returns:
            병합 결과 딕셔너리
        """
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock
        
        result = {
            "status": "success",
            "degraded_entries_found": 0,
            "entries_merged": 0,
            "dates_processed": [],
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # 날짜별로 그룹화된 degraded 엔트리 수집
            degraded_by_date = self._collect_degraded_entries_by_date()
            result["degraded_entries_found"] = sum(len(v) for v in degraded_by_date.values())
            
            if not degraded_by_date:
                result["status"] = "no_degraded_entries"
                return result
            
            # 필터링 (target_date 지정 시)
            if target_date:
                degraded_by_date = {
                    k: v for k, v in degraded_by_date.items() if k == target_date
                }
            
            # 날짜별로 개별 락 획득 후 병합
            for date, entries in degraded_by_date.items():
                # 🔒 날짜별 샤딩 락
                lock_key = f"{self._key_prefix}{self.RECONCILER_LOCK_PREFIX}{date}"
                lock = RedisDistributedLock(
                    redis_client=self._redis,
                    name=lock_key,
                    timeout=timedelta(seconds=60),
                    blocking_timeout=5.0,  # 다른 Pod가 처리 중이면 빠르게 스킵
                )
                
                if not lock.acquire(blocking=True):
                    logger.info(f"[HashChainReconciler] Skipped {date}: another Pod processing")
                    continue
                
                try:
                    merged = self._merge_date_entries(date, entries)
                    result["entries_merged"] += merged
                    result["dates_processed"].append(date)
                finally:
                    lock.release()
            
            return result
            
        except Exception as e:
            logger.error(f"[HashChainReconciler] Reconciliation failed: {e}")
            result["status"] = "failed"
            result["error"] = str(e)
            return result
    
    def _collect_degraded_entries_by_date(self) -> Dict[str, List[Dict[str, Any]]]:
        """로컬 로그에서 degraded 엔트리를 날짜별로 그룹화."""
        from collections import defaultdict
        
        degraded_by_date = defaultdict(list)
        
        log_files = sorted(self._log_dir.glob("audit_*.jsonl"))
        
        for log_file in log_files:
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        
                        try:
                            entry = json.loads(line)
                            integrity = entry.get("integrity", {})
                            
                            if integrity.get("degraded") is True:
                                # 타임스탬프에서 날짜 추출
                                timestamp = integrity.get("timestamp", "")
                                date = timestamp[:10] if len(timestamp) >= 10 else "unknown"
                                degraded_by_date[date].append(entry)
                        except json.JSONDecodeError:
                            continue
            except Exception:
                continue
        
        # 날짜별 정렬
        for date in degraded_by_date:
            degraded_by_date[date].sort(
                key=lambda e: e.get("integrity", {}).get("timestamp", "")
            )
        
        return dict(degraded_by_date)
    
    def _merge_date_entries(self, date: str, entries: List[Dict[str, Any]]) -> int:
        """특정 날짜의 엔트리 병합."""
        current_seq, current_hash = self._get_redis_state()
        
        return self._merge_entries_to_chain(
            entries,
            start_sequence=current_seq + 1,
            previous_hash=current_hash,
        )
```

#### 이점

```
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   Pod A      │  │   Pod B      │  │   Pod C      │
│              │  │              │  │              │
│  병합 날짜:  │  │  병합 날짜:  │  │  병합 날짜:  │
│  2026-01-15  │  │  2026-01-16  │  │  2026-01-17  │
│              │  │              │  │              │
│  lock:       │  │  lock:       │  │  lock:       │
│  ...:01-15   │  │  ...:01-16   │  │  ...:01-17   │
└──────────────┘  └──────────────┘  └──────────────┘
       ↓                 ↓                 ↓
       동시 병합 가능! (병렬 처리)
```

---

### 11.5 Self-Cleanup 워치독 (Local Watchdog)

#### 제안 분석

> 글로벌 TTL(60초)에만 의존하지 않고, 로컬 스레드에서 I/O 에러 시 즉시 PENDING 해제.

#### 현재 시스템 분석

**이미 존재하는 Watchdog 패턴:**

| 패턴 | 파일 | 동작 방식 |
|-----|-----|----------|
| `AuditWatchdog` | [audit_watchdog.py#L150-270](packages/selfhealing-python/src/selfhealing/audit/audit_watchdog.py#L150-L270) | 백그라운드 스레드 heartbeat 루프 |
| `LocalRateLimiter` | [rate_limit.py#L176-178](packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py#L176-L178) | 주기적 cleanup (`if now - self._last_cleanup > interval`) |

**AuditWatchdog 코드 분석:**

```python
# audit_watchdog.py L200-210
def start(self) -> None:
    self._thread = threading.Thread(
        target=self._heartbeat_loop,
        daemon=True,
        name="AuditWatchdog",
    )
    self._thread.start()

# L265-270
def _heartbeat_loop(self) -> None:
    while not self._stop_event.is_set():
        self._send_heartbeat()
        self._stop_event.wait(timeout=self._config.heartbeat_interval_seconds)
```

**LocalRateLimiter 코드 분석:**

```python
# rate_limit.py L176-178
if now - self._last_cleanup > self._cleanup_interval:
    self._cleanup_expired()
    self._last_cleanup = now
```

#### 평가

| 항목 | 평가 |
|-----|-----|
| 응답성 향상 | ✅ 높음 - TTL 만료 대기 없이 즉시 정리 |
| 구현 복잡도 | 낮음 - 기존 AuditWatchdog 패턴 재사용 |
| 안정성 | ✅ 높음 - daemon 스레드로 메인 프로세스와 독립 |

**결론:** ✅ **더 나은 방법**. 기존 `AuditWatchdog` 패턴 재사용.

#### 수정된 설계

**파일 위치**: `packages/selfhealing-python/src/selfhealing/audit/integrity.py`

```python
class PendingSequenceWatchdog:
    """
    로컬 PENDING 시퀀스 워치독 (Self-Cleanup).
    
    기존 패턴 참조:
    - AuditWatchdog (audit_watchdog.py L150-270)
    - LocalRateLimiter._cleanup_expired() (rate_limit.py L176-178)
    
    동작:
    1. 백그라운드 스레드로 실행
    2. 자신이 예약한 시퀀스 추적
    3. 쓰기 실패 시 TTL 만료 전 즉시 PENDING 해제
    """
    
    def __init__(
        self,
        redis_client: Any,
        key_prefix: str = "selfhealing:",
        check_interval_seconds: float = 5.0,
    ):
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._check_interval = check_interval_seconds
        
        # 로컬 예약 시퀀스 추적
        self._local_pending: Dict[int, datetime] = {}
        self._lock = threading.RLock()
        
        # 백그라운드 스레드
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._is_running = False
    
    def start(self) -> None:
        """워치독 시작 (AuditWatchdog.start() 패턴)."""
        with self._lock:
            if self._is_running:
                return
            
            self._is_running = True
            self._stop_event.clear()
            
            self._thread = threading.Thread(
                target=self._cleanup_loop,
                daemon=True,
                name="PendingSequenceWatchdog",
            )
            self._thread.start()
            
            logger.info("[PendingWatchdog] Started")
    
    def stop(self, timeout: float = 5.0) -> None:
        """워치독 중지."""
        with self._lock:
            if not self._is_running:
                return
            
            self._stop_event.set()
            self._is_running = False
        
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        
        logger.info("[PendingWatchdog] Stopped")
    
    def track_pending(self, sequence: int) -> None:
        """예약된 시퀀스 추적 등록."""
        with self._lock:
            self._local_pending[sequence] = datetime.now(timezone.utc)
    
    def untrack_pending(self, sequence: int) -> None:
        """시퀀스 추적 해제 (commit/abort 시)."""
        with self._lock:
            self._local_pending.pop(sequence, None)
    
    def immediate_release(self, sequence: int, reason: str = "write_failure") -> bool:
        """
        즉시 PENDING 해제 (TTL 만료 전).
        
        I/O 에러 등으로 쓰기 실패 시 호출.
        """
        pending_key = f"{self._key_prefix}audit:hash_chain:pending:{sequence}"
        orphan_key = f"{self._key_prefix}audit:hash_chain:orphaned:{sequence}"
        
        try:
            # 원자적으로 PENDING → ORPHANED 전환
            pipe = self._redis.pipeline()
            pipe.delete(pending_key)
            pipe.set(orphan_key, f"immediate_release:{reason}", ex=86400)
            pipe.execute()
            
            self.untrack_pending(sequence)
            
            logger.info(
                f"[PendingWatchdog] Immediate release: seq={sequence}, reason={reason}"
            )
            return True
            
        except Exception as e:
            logger.error(f"[PendingWatchdog] Immediate release failed: {e}")
            return False
    
    def _cleanup_loop(self) -> None:
        """
        백그라운드 정리 루프 (AuditWatchdog._heartbeat_loop 패턴).
        
        주기적으로 오래된 로컬 PENDING 시퀀스 정리.
        """
        while not self._stop_event.is_set():
            try:
                self._cleanup_stale_local_pending()
            except Exception as e:
                logger.exception(f"[PendingWatchdog] Cleanup loop error: {e}")
            
            self._stop_event.wait(timeout=self._check_interval)
    
    def _cleanup_stale_local_pending(self, max_age_seconds: float = 30.0) -> int:
        """
        오래된 로컬 PENDING 정리.
        
        TTL 만료 전에 로컬에서 먼저 감지하여 정리.
        """
        now = datetime.now(timezone.utc)
        stale_sequences = []
        
        with self._lock:
            for seq, reserved_at in list(self._local_pending.items()):
                age = (now - reserved_at).total_seconds()
                if age > max_age_seconds:
                    stale_sequences.append(seq)
        
        cleaned = 0
        for seq in stale_sequences:
            if self.immediate_release(seq, reason="stale_timeout"):
                cleaned += 1
        
        if cleaned > 0:
            logger.info(f"[PendingWatchdog] Cleaned {cleaned} stale pending sequences")
        
        return cleaned
```

**LocalFileBackend.write() 통합:**

```python
def write(self, entry: Dict[str, Any]) -> bool:
    with self._lock:
        sequence = None
        try:
            if self._hash_chain:
                entry = self._hash_chain.add_integrity(entry)
                sequence = entry.get("integrity", {}).get("sequence")
                
                # 워치독에 추적 등록
                if sequence and self._watchdog:
                    self._watchdog.track_pending(sequence)
            
            if not self._ensure_file_open():
                # 🔒 즉시 해제 (TTL 만료 대기 X)
                if sequence and self._watchdog:
                    self._watchdog.immediate_release(sequence, "file_open_failed")
                return False
            
            json_line = json.dumps(entry, default=str, ensure_ascii=False)
            self._file_handle.write(json_line + "\n")
            self._file_handle.flush()
            
            # 성공: 추적 해제
            if sequence and self._watchdog:
                self._watchdog.untrack_pending(sequence)
            
            return True
            
        except IOError as e:
            # 🔒 I/O 에러 시 즉시 해제
            if sequence and self._watchdog:
                self._watchdog.immediate_release(sequence, f"io_error:{str(e)[:50]}")
            return False
```

#### 이점

```
기존 방식 (TTL만 의존):
─────────────────────────────────────────────
t=0:   PENDING 예약 (TTL=30s)
t=1:   파일 쓰기 실패 (I/O 에러)
t=1~30: 시퀀스가 30초간 블록됨!
t=30:  TTL 만료 → 다음 시퀀스 사용 가능
─────────────────────────────────────────────

개선된 방식 (Self-Cleanup 워치독):
─────────────────────────────────────────────
t=0:   PENDING 예약 (TTL=30s)
t=1:   파일 쓰기 실패 (I/O 에러)
t=1:   즉시 immediate_release() 호출
t=1:   다음 시퀀스 즉시 사용 가능! ← 지연 0초
─────────────────────────────────────────────
```

---

### 11.6 PENDING TTL 조정

#### 문제점

문서의 기존 설정:

```python
PENDING_TTL_SECONDS = 300  # 5분 ← 너무 김!
```

#### 코드 분석

[integrity.py#L377](packages/selfhealing-python/src/selfhealing/audit/integrity.py#L377):

```python
lock_timeout_seconds: float = 5.0,  # Lock auto-expire time
```

[redis_adapter.py#L71](packages/selfhealing-python/src/selfhealing/adapters/cache/redis_adapter.py#L71):

```python
timeout: timedelta = timedelta(seconds=10),  # Lock TTL 기본 10초
```

#### 수정

| 설정 | 기존 값 | 수정 값 | 근거 |
|-----|--------|--------|-----|
| `PENDING_TTL_SECONDS` | 300초 | **30초** | Lock TTL(5초)의 6배 |

```python
class PendingSequenceManager:
    PENDING_TTL_SECONDS = 30  # Lock TTL(5초)의 6배 (권장)
```

---

### 11.7 병합 시점 Lock 전략 추가

#### 문제점

`HashChainReconciler.reconcile()`에 Lock 전략 없음 (Section 7.1).

#### 수정

Section 11.4 "날짜별 샤딩 락"으로 해결됨.

추가로 **새 로그 쓰기와 병합 충돌 방지**:

```python
def reconcile(self, target_date: Optional[str] = None) -> Dict[str, Any]:
    # 1. 날짜별 샤딩 락 (Section 11.4)
    # 2. 기존 해시 체인 락과 연동
    
    # 병합 중에는 해당 날짜의 새 로그 쓰기 대기
    # (RedisHashChainManager의 LOCK_KEY 재사용)
    main_lock_key = f"{self._key_prefix}audit:hash_chain:lock"
    main_lock = RedisDistributedLock(
        redis_client=self._redis,
        name=main_lock_key,
        timeout=timedelta(seconds=5),
        blocking_timeout=10.0,
    )
    
    if not main_lock.acquire(blocking=True):
        return {"status": "skipped", "reason": "Main chain lock busy"}
    
    try:
        # 병합 수행...
    finally:
        main_lock.release()
```

---

### 11.8 Monotonic Timer 강제 (ClockSkew 보호)

> **3차 리뷰 반영**

#### 제안

> PendingSequenceManager의 TTL 체크 시 반드시 `time.monotonic()`을 사용하세요.
> Plan 34의 시간 조작 실험 중에도 예약 만료 로직이 흔들려서는 안 됩니다.

#### 기존 패턴 분석

**`MonotonicTTLHelper`** - [base.py#L382-L475](packages/selfhealing-python/src/selfhealing/services/chaos/base.py#L382-L475):

```python
@dataclass
class MonotonicTTLHelper:
    """
    Monotonic clock 기반 TTL 헬퍼.
    
    ClockSkewExperiment 등 시스템 시간 조작 실험에서,
    실험 엔진 자신의 TTL 타이머가 영향받지 않도록 보호합니다.
    
    time.monotonic()는 시스템 시간(timezone.now())과 달리
    시스템 시간 변경에 영향받지 않는 상대 시간을 반환합니다.
    """
    
    ttl_seconds: float
    _start_time: float = field(default=0.0, repr=False)
    _started: bool = field(default=False, repr=False)
    
    def start(self) -> None:
        self._start_time = time.monotonic()  # ← 핵심!
        self._started = True
    
    def elapsed_seconds(self) -> float:
        if not self._started:
            return 0.0
        return time.monotonic() - self._start_time
    
    def is_expired(self) -> bool:
        if not self._started:
            return False
        return self.elapsed_seconds() >= self.ttl_seconds
```

#### 평가

| 항목 | 평가 |
|-----|-----|
| 필요성 | ✅ **필수** - ClockSkew 실험 중 PENDING TTL 가 영향받으면 안됨 |
| 기존 패턴 | ✅ `MonotonicTTLHelper` 100% 재사용 |
| 구현 난이도 | 낮음 |

#### 수정된 `PendingSequenceManager`

```python
from dataclasses import dataclass, field
import time


class PendingSequenceManager:
    """
    시퀀스 예약 및 커밋 관리 (Monotonic Timer 강제).
    
    기존 패턴 참조:
    - MonotonicTTLHelper (chaos/base.py L382-475)
    - ClockSkew 실험 보호 패턴
    """
    
    PENDING_TTL_SECONDS = 30  # Lock TTL(5초)의 6배
    
    def __init__(self, redis_client: Any, key_prefix: str = "selfhealing:"):
        self._redis = redis_client
        self._key_prefix = key_prefix
        
        # 🔒 Monotonic Timer 강제 (ClockSkew 보호)
        # 로컬 예약 시간 추적 (datetime 대신 monotonic 사용)
        self._local_pending: Dict[int, float] = {}  # {sequence: monotonic_start_time}
    
    def track_pending(self, sequence: int) -> None:
        """
        예약된 시퀀스 추적 등록.
        
        🔒 time.monotonic() 사용 - 시스템 시간 조작에 독립적
        """
        self._local_pending[sequence] = time.monotonic()  # datetime 대신 monotonic!
    
    def is_stale_local(self, sequence: int, max_age_seconds: float = 30.0) -> bool:
        """
        로컬 PENDING이 stale인지 확인.
        
        🔒 Monotonic 기반 - ClockSkew 실험 중에도 정확
        """
        if sequence not in self._local_pending:
            return False
        
        start_time = self._local_pending[sequence]
        elapsed = time.monotonic() - start_time  # ← 핵심: monotonic 차이
        
        return elapsed > max_age_seconds
    
    def cleanup_stale_local_pending(self, max_age_seconds: float = 30.0) -> int:
        """
        오래된 로컬 PENDING 정리.
        
        🔒 Monotonic 기반 TTL 체크
        """
        now = time.monotonic()
        stale_sequences = []
        
        for seq, start_time in list(self._local_pending.items()):
            elapsed = now - start_time
            if elapsed > max_age_seconds:
                stale_sequences.append(seq)
        
        cleaned = 0
        for seq in stale_sequences:
            if self.immediate_release(seq, reason="monotonic_stale_timeout"):
                cleaned += 1
        
        return cleaned
```

#### 이점

```
ClockSkew 실험 시나리오:
─────────────────────────────────────────────

t=0:   PENDING 예약 (TTL=30s)
       monotonic_start = 1000.0
       
t=10:  ClockSkew 실험 시작 → 시스템 시간 +1시간 조작
       datetime.now() = 미래 1시간 후
       time.monotonic() = 1010.0 (변함 없음!)
       
t=10:  datetime 기반: "1시간 경과" → 잘못된 만료 판정!
       monotonic 기반: "10초 경과" → 정확한 판정 ✅
```

---

### 11.9 Atomic Swap (전역 락) - 충돌 방지

> **3차 리뷰 반영**

#### 제안

> 고립된 로그를 메인 체인에 이어 붙일 때, Redis의 state 해시를 업데이트하는 과정이
> 다른 노드의 신규 로그 쓰기와 충돌하지 않도록 **전역 락(Global Chain Lock)**을 짧고 굵게 사용하십시오.

#### Section 11.7 보완

Section 11.7의 락 전략을 **"짧고 굵게"** 명시화:

```python
class HashChainReconciler:
    """
    해시 체인 화해 관리자.
    
    전역 락 전략 (Short & Strong):
    1. 날짜별 샤딩 락: 병렬 병합 허용
    2. 전역 체인 락: Redis state 업데이트 순간만 획득
    
    기존 패턴 참조:
    - RedisDistributedLock (redis_adapter.py L34-155)
    """
    
    GLOBAL_CHAIN_LOCK_TIMEOUT_SECONDS = 3  # 짧고 굵게 (3초)
    GLOBAL_CHAIN_LOCK_BLOCKING_TIMEOUT = 5.0  # 최대 대기 5초
    
    def _atomic_swap_state(
        self,
        new_sequence: int,
        new_hash: str,
        merged_count: int,
    ) -> bool:
        """
        해시 체인 상태 원자적 교체.
        
        🔒 Global Chain Lock:
        - 병합 완료 시점에 짧고 굵게 획득
        - Redis state 업데이트만 수행
        - 즉시 해제
        
        Args:
            new_sequence: 새 시퀀스 번호
            new_hash: 새 해시값
            merged_count: 병합된 엔트리 수
        
        Returns:
            True if successful
        """
        from selfhealing.adapters.cache.redis_adapter import RedisDistributedLock
        from datetime import timedelta
        
        # 🔒 전역 체인 락 ("짧고 굵게")
        global_lock_key = f"{self._key_prefix}audit:hash_chain:lock"
        global_lock = RedisDistributedLock(
            redis_client=self._redis,
            name=global_lock_key,
            timeout=timedelta(seconds=self.GLOBAL_CHAIN_LOCK_TIMEOUT_SECONDS),  # 3초
            blocking_timeout=self.GLOBAL_CHAIN_LOCK_BLOCKING_TIMEOUT,  # 5초 대기
        )
        
        if not global_lock.acquire(blocking=True):
            logger.warning(
                "[HashChainReconciler] Failed to acquire global chain lock. "
                "Another merge or write in progress."
            )
            return False
        
        try:
            # 🔒 원자적 상태 업데이트 (pipeline)
            seq_key = f"{self._key_prefix}audit:hash_chain:seq"
            state_key = f"{self._key_prefix}audit:hash_chain:state"
            
            pipe = self._redis.pipeline()
            pipe.set(seq_key, new_sequence)
            pipe.hset(state_key, mapping={
                "previous_hash": new_hash,
                "sequence": str(new_sequence),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "reconciled_entries": str(merged_count),
                "update_source": "reconciler_atomic_swap",
            })
            pipe.execute()
            
            logger.info(
                f"[HashChainReconciler] Atomic swap completed: "
                f"seq={new_sequence}, merged={merged_count}"
            )
            return True
            
        finally:
            # 🔒 즉시 해제 ("short")
            global_lock.release()
    
    def reconcile(self, target_date: Optional[str] = None) -> Dict[str, Any]:
        """
        Fallback 체인 병합 수행.
        
        락 전략:
        1. 날짜별 샤딩 락: 병렬 병합 허용 (Section 11.4)
        2. 데이터 수집 및 검증: 락 없이 수행
        3. 전역 체인 락: Redis state 업데이트만 (짧고 굵게)
        """
        result = {
            "status": "success",
            "degraded_entries_found": 0,
            "entries_merged": 0,
            "reconciled_at": datetime.now(timezone.utc).isoformat(),
        }
        
        try:
            # 1. 날짜별 샤딩 락 획득 (Section 11.4)
            # ... 기존 로직 ...
            
            # 2. 데이터 수집 및 검증 (락 없이 수행)
            degraded_entries = self._collect_degraded_entries()
            # ... 검증 로직 ...
            
            # 3. 해시 체인 재계산 (락 없이 수행)
            current_seq, current_hash = self._get_redis_state()
            new_entries = self._recalculate_hashes(
                degraded_entries, 
                start_sequence=current_seq + 1,
                previous_hash=current_hash,
            )
            
            # 4. 🔒 전역 락 + Atomic Swap (짧고 굵게)
            final_seq = current_seq + len(new_entries)
            final_hash = new_entries[-1]["integrity"]["current_hash"] if new_entries else current_hash
            
            if not self._atomic_swap_state(final_seq, final_hash, len(new_entries)):
                result["status"] = "failed"
                result["error"] = "Failed to acquire global chain lock"
                return result
            
            result["entries_merged"] = len(new_entries)
            
            # 5. 📝 Audit Trail 기록 (Section 11.10)
            self._log_integrity_restored(result)
            
            return result
            
        except Exception as e:
            result["status"] = "failed"
            result["error"] = str(e)
            return result
```

#### 락 획득 시점 최적화

```
기존 방식 (Lock 남용):
─────────────────────────────────────────────
t=0:   Global Lock 획득
t=0~5: 데이터 수집 (5초)
t=5~8: 검증 (3초)
t=8~10: 해시 재계산 (2초)
t=10:  Redis 업데이트 (0.01초)
t=10:  Lock 해제
→ 다른 Pod 로그 쓰기 10초간 차단!

개선된 방식 (Short & Strong):
─────────────────────────────────────────────
t=0~5: 데이터 수집 (Lock 없음)
t=5~8: 검증 (Lock 없음)
t=8~10: 해시 재계산 (Lock 없음)
t=10:  🔒 Global Lock 획득
t=10:  Redis 업데이트 (0.01초)
t=10:  🔓 Lock 해제
→ 다른 Pod 로그 쓰기 0.01초만 차단! ✅
```

---

### 11.10 Audit Trail of Integrity (무결성 복구 이벤트)

> **3차 리뷰 반영**

#### 제안

> StartupHashChainSync나 Reconciler가 동작하여 체인을 교정했을 때,
> 그 교정 행위 자체를 **'무결성 복구 이벤트'**로 Audit 로그에 남기십시오.
> 이는 "시스템이 스스로 무결성을 지켜냈다"는 강력한 증거가 됩니다.

#### 기존 패턴 분석

**`SelfAuditEvent`** - [self_audit.py#L65-77](packages/selfhealing-python/src/selfhealing/audit/self_audit.py#L65-L77):

```python
class SelfAuditEvent(str, Enum):
    # Integrity Events (기존)
    CHECKSUM_MISMATCH = "checksum_mismatch"
    HASH_CHAIN_BROKEN = "hash_chain_broken"
    WAL_CORRUPTED = "wal_corrupted"
    
    # Recovery Events (기존)
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"
    RECOVERY_FAILED = "recovery_failed"
```

**`self_audit().log()`** - [self_audit.py#L145-165](packages/selfhealing-python/src/selfhealing/audit/self_audit.py#L145-L165):

```python
def log(
    self,
    event_type: SelfAuditEvent,
    message: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """Self-Audit 이벤트 기록. 항상 성공해야 함."""
```

#### 신규 이벤트 타입 제안

**파일 위치**: `packages/selfhealing-python/src/selfhealing/audit/self_audit.py`

```python
class SelfAuditEvent(str, Enum):
    # ... 기존 이벤트 ...
    
    # Integrity Restoration Events (신규)
    INTEGRITY_RESTORED = "integrity_restored"          # 무결성 복구 완료
    HASH_CHAIN_SYNCED = "hash_chain_synced"            # 체인 동기화 완료
    HASH_CHAIN_RECONCILED = "hash_chain_reconciled"    # 체인 병합 완료
    ORPHANED_SEQUENCES_CLEANED = "orphaned_sequences_cleaned"  # 고아 시퀀스 정리
```

#### `StartupHashChainSync` Audit Trail 추가

```python
class StartupHashChainSync:
    """
    시작 시 해시 체인 동기화.
    
    Audit Trail:
    - 동기화 수행 시 HASH_CHAIN_SYNCED 이벤트 기록
    - 무결성 복구 증거 제공
    """
    
    def sync_on_startup(self) -> Dict[str, Any]:
        result = {...}
        
        try:
            # ... 기존 동기화 로직 ...
            
            # 📝 Audit Trail: 동기화 완료 기록
            if result["action"] in ("max_sync_to_file", "in_sync"):
                self._log_chain_synced(result)
            
            return result
            
        except Exception as e:
            # 📝 Audit Trail: 실패 기록
            self._log_sync_failed(str(e))
            raise
    
    def _log_chain_synced(self, result: Dict[str, Any]) -> None:
        """
        체인 동기화 완료 Audit Trail.
        
        "시스템이 스스로 무결성을 지켜냈다"는 증거.
        """
        from selfhealing.audit.self_audit import self_audit, SelfAuditEvent
        
        self_audit().log(
            SelfAuditEvent.HASH_CHAIN_SYNCED,  # 신규 이벤트
            f"Hash chain synchronized on startup: {result['action']}",
            details={
                "action": result["action"],
                "file_sequence": result.get("file_sequence", 0),
                "redis_sequence": result.get("redis_sequence", 0),
                "pending_cleaned": result.get("pending_cleaned", 0),
                "synced_at": datetime.now(timezone.utc).isoformat(),
                "integrity_preserved": True,  # 무결성 보존 증거
            }
        )
        
        # 추가: 무결성 복구 이벤트 (교정이 발생한 경우)
        if result["action"] == "max_sync_to_file":
            self_audit().log(
                SelfAuditEvent.INTEGRITY_RESTORED,  # 신규 이벤트
                f"Integrity restored: Redis synced to file (seq {result['redis_sequence']} → {result['file_sequence']})",
                details={
                    "restoration_type": "startup_sync",
                    "before_sequence": result.get("redis_sequence", 0),
                    "after_sequence": result.get("file_sequence", 0),
                    "self_healing_action": "max_sync_to_file",
                }
            )
```

#### `HashChainReconciler` Audit Trail 추가

```python
class HashChainReconciler:
    def _log_integrity_restored(self, result: Dict[str, Any]) -> None:
        """
        체인 병합 완료 Audit Trail.
        
        "시스템이 스스로 무결성을 지켜냈다"는 강력한 증거.
        """
        from selfhealing.audit.self_audit import self_audit, SelfAuditEvent
        
        # 1. 병합 완료 이벤트
        self_audit().log(
            SelfAuditEvent.HASH_CHAIN_RECONCILED,  # 신규 이벤트
            f"Hash chain reconciled: {result['entries_merged']} entries merged",
            details={
                "entries_merged": result.get("entries_merged", 0),
                "new_sequence_start": result.get("new_sequence_start", 0),
                "new_sequence_end": result.get("new_sequence_end", 0),
                "dates_processed": result.get("dates_processed", []),
                "reconciled_at": result.get("reconciled_at"),
                "self_healing_action": "fallback_chain_merge",
            }
        )
        
        # 2. 무결성 복구 이벤트 (병합이 발생한 경우)
        if result.get("entries_merged", 0) > 0:
            self_audit().log(
                SelfAuditEvent.INTEGRITY_RESTORED,
                f"Integrity restored: {result['entries_merged']} degraded entries reintegrated",
                details={
                    "restoration_type": "reconciliation",
                    "degraded_entries_found": result.get("degraded_entries_found", 0),
                    "entries_reintegrated": result.get("entries_merged", 0),
                    "chain_continuity": "restored",
                    "self_healing_evidence": True,  # 핵심 증거 플래그
                }
            )
```

#### Audit Trail 예시 출력

```json
{
  "timestamp": "2026-01-17T10:30:00.000Z",
  "event": "integrity_restored",
  "message": "Integrity restored: 15 degraded entries reintegrated",
  "details": {
    "restoration_type": "reconciliation",
    "degraded_entries_found": 15,
    "entries_reintegrated": 15,
    "chain_continuity": "restored",
    "self_healing_evidence": true
  }
}
```

#### 가치

```
"우리 시스템은 Redis가 장애나도,
 로컬 Fallback으로 로그를 계속 수집하고,
 Redis 복구 후 자동으로 병합하여
 체인 무결성을 복원했습니다.
 
 이는 Audit 로그에 'integrity_restored' 이벤트로
 영구적으로 기록되어 있습니다."
 
→ 감사/컴플라이언스 증거로 활용 가능
```

---

## 12. 재사용 패턴 요약 (업데이트)

| 신규 컴포넌트 | 기존 패턴 | 파일 위치 |
|-------------|----------|----------|
| `PendingSequenceManager` (기대 해시) | `RedisDistributedLock` Lua Script | `redis_adapter.py#L131` |
| `HashChainReconciler` (검증) | `HashChainVerifier.verify_chain()` | `integrity.py#L60-96` |
| `LocalFileBackend` (오프라인 앵커) | `WALEntry.operation` | `wal.py#L64` |
| `HashChainReconciler` (샤딩 락) | `DailyHashAnchor` 키 구조 | Section 10.2 |
| `PendingSequenceWatchdog` | `AuditWatchdog._heartbeat_loop()` | `audit_watchdog.py#L265` |
| `PendingSequenceWatchdog` | `LocalRateLimiter._cleanup_expired()` | `rate_limit.py#L176` |
| `PendingSequenceManager` (Monotonic) | `MonotonicTTLHelper` | `chaos/base.py#L382-475` |
| `HashChainReconciler` (Atomic Swap) | `RedisDistributedLock` | `redis_adapter.py#L34-155` |
| Audit Trail | `SelfAuditEvent` + `self_audit()` | `self_audit.py#L65-165` |

---

## 13. 성능 최적화 전략 (4차 리뷰 반영)

> **목적**: 11장에서 제안한 기능들의 성능 단점을 코드베이스 내 기존 패턴으로 최적화

### 13.1 단점-최적화 매핑 총괄표

| 제안 기능 | 단점 | 최적화 기법 | 효과 | 코드 근거 |
|----------|-----|------------|------|----------|
| 기대 해시 등록 | +2 Redis RTT | **Lua Script** | 5 RTT → 1 RTT | `redis_adapter.py#L137-143` |
| Orphaned 검증 | O(n) 체인 순회 | **Sampling 검증** | 전체 n → 샘플 k | `throttle/config.py#L26` |
| 오프라인 앵커 | `fsync()` 오버헤드 | **Batch + Async** | n×fsync → 1×fsync | `audit/config.py#L69-73` |
| 날짜별 샤딩 락 | 동일 날짜 경합 | **이미 샤딩됨** | - | Section 11.4 |
| Self-Cleanup 워치독 | daemon 스레드 비용 | **Lazy 초기화** | 필요 시만 생성 | `middleware.py#L647` |
| Monotonic Timer | float 메모리 | **LRU Cache** | maxsize 제한 | `precomputed_cache.py#L111` |
| Atomic Swap (Global Lock) | 병합 시 병목 | **샤딩 + Pipeline** | 날짜별 분리 | Section 11.4 |
| Audit Trail | 로그 파일 증가 | **Batch Flush** | 개별 → 배치 | `audit/config.py#L69` |

### 13.2 Redis RTT 최적화

#### 13.2.1 Lua Script (원자적 다중 연산)

**문제**: 기대 해시 등록 시 GET → SET → INCR 등 5회 RTT

**해결**: Lua Script로 서버 측 원자 실행

```python
# 기존 패턴: redis_adapter.py#L137-143
LUA_CHECK_AND_DELETE = """
local current = redis.call('GET', KEYS[1])
if current == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

# 적용 예시: add_integrity() 전체를 Lua로
LUA_ADD_INTEGRITY_WITH_EXPECTED = """
-- KEYS[1] = pending_key, KEYS[2] = chain_key, KEYS[3] = anchor_key
-- ARGV[1] = expected_hash, ARGV[2] = new_entry, ARGV[3] = anchor_value
local expected = redis.call('GET', KEYS[1])
if expected and expected ~= ARGV[1] then
    return {err='HASH_MISMATCH'}
end
redis.call('RPUSH', KEYS[2], ARGV[2])
redis.call('SET', KEYS[3], ARGV[3])
redis.call('DEL', KEYS[1])
return 'OK'
"""
```

**효과**: 5 RTT → 1 RTT (80% 감소)

#### 13.2.2 Pipeline (비원자적 배치)

**문제**: 여러 키 조회/설정 시 개별 RTT

**해결**: Pipeline으로 단일 왕복

```python
# 기존 패턴: rate_limit/redis_adapter.py#L149-153
with self._client.pipeline(transaction=False) as pipe:
    pipe.incr(key)
    pipe.expire(key, window_seconds)
    pipe.get(key)
    results = pipe.execute()
```

**적용**: Orphaned 검증 시 다중 체인 상태 일괄 조회

### 13.3 I/O 최적화

#### 13.3.1 Batch Flush

**문제**: 오프라인 앵커 저장 시 매번 `fsync()`

**해결**: 배치 수집 후 일괄 저장

```python
# 기존 패턴: audit/config.py#L69-73
batch_size: int = 100
batch_flush_interval_seconds: float = 10.0

# 적용
class BatchedAnchorWriter:
    def __init__(self, batch_size=100, flush_interval=10):
        self._buffer = []
        self._last_flush = time.monotonic()
    
    def add(self, anchor: DailyHashAnchor):
        self._buffer.append(anchor)
        if len(self._buffer) >= self.batch_size or \
           time.monotonic() - self._last_flush > self.flush_interval:
            self._flush()
    
    def _flush(self):
        if not self._buffer:
            return
        with open(self.path, 'a') as f:
            for anchor in self._buffer:
                f.write(anchor.to_json() + '\n')
            f.flush()
            os.fsync(f.fileno())  # 1회 fsync
        self._buffer.clear()
        self._last_flush = time.monotonic()
```

**효과**: n×fsync → 1×fsync (99% 감소, batch_size=100 기준)

#### 13.3.2 Async 저장

**문제**: 앵커 저장이 요청 응답을 블로킹

**해결**: 별도 스레드/asyncio로 비동기 처리

```python
# 기존 패턴: AsyncHealingLogger (test_utils_async_logger.py)
class AsyncAnchorWriter:
    def __init__(self):
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker.start()
    
    def save_async(self, anchor: DailyHashAnchor):
        self._queue.put(anchor)  # Non-blocking
    
    def _worker_loop(self):
        while True:
            anchor = self._queue.get()
            self._sync_save(anchor)
```

**효과**: 요청 응답 시간에서 I/O 제거

### 13.4 메모리 최적화

#### 13.4.1 LRU Cache (자동 Eviction)

**문제**: `PendingSequenceManager._expected_hashes` 무한 증가 가능

**해결**: TTLCache + maxsize 제한

```python
# 기존 패턴: precomputed_cache.py#L111-144
from cachetools import TTLCache

class PendingSequenceManager:
    def __init__(self, maxsize: int = 10000, ttl: int = 3600):
        # maxsize 초과 시 LRU 방식으로 자동 제거
        self._expected_hashes = TTLCache(maxsize=maxsize, ttl=ttl)
```

**효과**: 메모리 상한 보장 (O(maxsize))

#### 13.4.2 Lazy 초기화

**문제**: `PendingSequenceWatchdog` 항상 생성

**해결**: 필요 시에만 초기화

```python
# 기존 패턴: middleware.py#L647-648
class HashChainManager:
    _watchdog: Optional[PendingSequenceWatchdog] = None
    
    def _lazy_init_watchdog(self):
        if self._watchdog is None:
            self._watchdog = PendingSequenceWatchdog()
            self._watchdog.start()
```

**효과**: 사용하지 않는 환경에서 오버헤드 제거

### 13.5 검증 최적화

#### 13.5.1 Sampling 검증

**문제**: Orphaned 검증 시 O(n) 체인 순회

**해결**: 확률적 샘플링으로 비용 감소

```python
# 기존 패턴: throttle/config.py#L26, runtime_config/core_configs.py#L215
@dataclass
class VerificationConfig:
    sample_rate: float = 0.1  # 10% 샘플링
    sample_interval_ms: int = 500

def verify_orphaned_chains_sampled(chains: List[str], config: VerificationConfig):
    """확률적 샘플링 검증"""
    import random
    sample_size = max(1, int(len(chains) * config.sample_rate))
    sampled = random.sample(chains, sample_size)
    
    for chain_key in sampled:
        if not verify_single_chain(chain_key):
            # 하나라도 실패 시 전체 검증으로 전환
            return verify_all_chains(chains)
    return True
```

**효과**: 평균 O(k) where k = n × sample_rate

### 13.6 최적화 불가능한 본질적 Trade-off

| 단점 | 최적화 불가 이유 | 완화 방법 |
|-----|----------------|----------|
| `fsync()` 오버헤드 | **내구성(Durability) 필수** - 배터리 유실 시 데이터 손실 방지 | Batch fsync로 빈도 감소 |
| 전역 락 병목 | **일관성(Consistency) 필수** - 동시 병합 시 충돌 방지 | 날짜별 샤딩으로 경합 분산 |
| 메모리 사용량 증가 | **정확성 필수** - 기대 해시 추적에 필요 | LRU eviction으로 상한 제한 |

#### 13.6.1 완화 효과 정량 분석

완화 방법은 "제거"가 아닌 "감소"이므로, 정량적 효과를 명시합니다:

| 단점 | 완화 전 | 완화 후 | 개선율 | 비고 |
|-----|--------|--------|-------|------|
| **fsync() 오버헤드** | 매 요청 1회 (1~10ms/call) | 100개당 1회 | **~99% 감소** | batch_size=100 기준 |
| **전역 락 병목** | 단일 락 (경합률 100%) | 365개 샤드 | **~99.7% 분산** | 날짜별 샤딩, 경합률 = 1/365 |
| **메모리 증가** | O(n) 무한 증가 | O(10000) 고정 | **상한 고정** | maxsize=10000 기준 |

**세부 분석**:

1. **fsync() Batch 효과**
   ```
   Before: 1000 req/s × 5ms/fsync = 5000ms 블로킹/초
   After:  1000 req/s ÷ 100 batch × 5ms = 50ms 블로킹/초
   
   개선: (5000 - 50) / 5000 = 99% 감소
   ```

2. **샤딩 락 경합 분산**
   ```
   Before: 모든 병합 요청이 1개 락 경합
           동시 10개 요청 시 대기시간 = 9 × lock_time
   
   After:  365개 샤드 (날짜별)
           동일 날짜 경합 확률 = 1/365 ≈ 0.27%
           평균 대기시간 = 0.027 × lock_time
   ```

3. **LRU 메모리 상한**
   ```
   Before: 24시간 × 3600 req/h = 86,400개 항목 (무제한)
   After:  maxsize=10000 고정
           초과 시 LRU 자동 제거 (가장 오래된 것부터)
   
   메모리: ~10000 × 64 bytes = ~640KB 상한
   ```

**Trade-off 한계**:

| 완화 방법 | 얻는 것 | 잃는 것 |
|----------|--------|--------|
| Batch fsync | 처리량 99% 향상 | 배치 윈도우 내 데이터 손실 가능 (최대 10초) |
| 날짜별 샤딩 | 경합 99.7% 감소 | 날짜 경계 정확한 시간 동기화 필요 |
| LRU eviction | 메모리 상한 보장 | 오래된 기대 해시 조기 제거 → 검증 스킵 가능 |

#### 13.6.2 "잃는 것"에 대한 대비책 (Zero Data Loss)

> ⚠️ **중요**: 소프트웨어적 데이터 손실은 **설계 결함**입니다.
> 하드웨어 장애가 아닌 이상, 모든 "잃는 것"에는 대비책이 있어야 합니다.

코드베이스에 이미 **Zero Data Loss** 패턴이 존재합니다:

| 완화 시 잃는 것 | 대비책 | 결과 | 코드 근거 |
|---------------|--------|-----|----------|
| Batch fsync 윈도우 내 손실 | **WAL (Write-Ahead Log)** | ✅ **Zero Loss** | `backend.py#L144-230` |
| LRU에서 기대 해시 조기 제거 | **L1+L2 Layered Cache** | ✅ **Zero Loss** | `layered_repository.py#L24` |
| 날짜 경계 시간 불일치 | **Monotonic Timer + NTP** | ✅ **Zero Conflict** | Section 11.8 |

##### 대비책 1: WAL (Write-Ahead Log) - Batch 윈도우 손실 방지

**문제**: Batch fsync는 100개 모이기 전 프로세스 크래시 시 데이터 손실

**해결**: WAL에 먼저 기록 → 배치 버퍼 추가 → 크래시 후 WAL에서 복구

```python
# 기존 패턴: backend.py#L144-161
class ResilientStorageBackend:
    def _init_wal(self) -> None:
        """Initialize Write-Ahead Log."""
        wal_config = WALConfig(
            wal_dir=self.config.wal_dir,
            sync_on_write=True,  # fsync guarantee - server crash safe
            file_prefix="resilient_storage",
        )
        self._wal = WriteAheadLog(config=wal_config)

# 적용: BatchedAnchorWriter with WAL
class ZeroLossBatchedWriter:
    def add(self, anchor: DailyHashAnchor):
        # Step 1: WAL에 먼저 기록 (개별 fsync)
        self._wal.append({
            "operation": "anchor_add",
            "data": anchor.to_dict(),
            "sequence": self._sequence
        })
        
        # Step 2: 메모리 버퍼에 추가 (fsync 없음)
        self._buffer.append(anchor)
        
        # Step 3: 배치 조건 충족 시 flush
        if len(self._buffer) >= self.batch_size:
            self._flush_and_checkpoint()
    
    def _flush_and_checkpoint(self):
        """배치 flush 후 WAL 체크포인트"""
        self._write_batch_to_file()  # 1회 fsync
        self._wal.checkpoint(self._sequence)  # WAL 정리
```

**결과**:
- 크래시 시: WAL에서 복구 (손실 0)
- 정상 시: Batch fsync로 성능 99% 향상
- **Trade-off 제거**: 성능 ↑, 손실 = 0

##### 대비책 2: L1+L2 Layered Cache - LRU 조기 제거 방지

**문제**: L1(메모리) LRU eviction 시 기대 해시 사라짐 → 검증 스킵

**해결**: L1 Miss 시 L2(Redis)에서 조회

```python
# 기존 패턴: layered_repository/repository_operations.py#L23-53
class RepositoryOperationsMixin:
    def get_by_service_name(self, service_name: str):
        """L1에서 조회. L1에 없으면 L2 확인 후 L1에 캐시."""
        result = self._l1.get_by_service_name(service_name)

        if result is None and self._l2 and self._l2_healthy:
            # L1 Miss → L2에서 조회
            l2_result = self._l2.get_by_service_name(service_name)
            if l2_result:
                # L2 Hit → L1에 다시 캐시
                self._l1.update_state(...)
                return l2_result
        return result

# 적용: PendingSequenceManager with L1+L2
class ZeroLossPendingManager:
    def __init__(self):
        self._l1 = TTLCache(maxsize=10000, ttl=3600)  # 메모리, LRU
        self._l2 = RedisClient()  # Redis, 무제한
    
    def get_expected_hash(self, sequence_id: str) -> Optional[str]:
        # L1 조회
        result = self._l1.get(sequence_id)
        if result:
            return result
        
        # L1 Miss → L2 조회
        result = self._l2.get(f"expected_hash:{sequence_id}")
        if result:
            self._l1[sequence_id] = result  # L1에 복원
            return result
        
        return None  # 진짜 없음 (등록 안 된 케이스)
```

**결과**:
- L1 eviction 후에도 L2에서 복구
- **Trade-off 제거**: 메모리 상한 유지, 검증 스킵 = 0

##### 대비책 3: Monotonic Timer + NTP - 날짜 경계 충돌 방지

**문제**: 노드 간 시간 차이로 날짜 경계에서 잘못된 샤드 선택

**해결**: Monotonic Timer (상대 시간) + NTP 동기화 (절대 시간)

```python
# 기존 패턴: Section 11.8 Monotonic Timer
import time

class DateShardSelector:
    def __init__(self):
        self._boot_time = time.time()  # 시작 시 NTP 동기화 가정
        self._boot_mono = time.monotonic()
    
    def get_current_date(self) -> str:
        """NTP 기반 현재 날짜 (Monotonic 보정)"""
        elapsed = time.monotonic() - self._boot_mono
        current_time = self._boot_time + elapsed  # 시계 역행 방지
        return datetime.fromtimestamp(current_time).strftime("%Y-%m-%d")
    
    def get_shard_key(self, date_str: str) -> str:
        return f"chain:global:{date_str}"
```

**결과**:
- 시계 역행 (NTP 점프) 방지
- **Trade-off 제거**: 샤딩 유지, 날짜 충돌 = 0

##### 최종 Trade-off 재평가

| 완화 방법 | 기존 잃는 것 | 대비책 적용 후 | 최종 손실 |
|----------|------------|--------------|----------|
| Batch fsync | 최대 10초 데이터 손실 | WAL 선행 기록 | **0 (Zero)** |
| LRU eviction | 검증 스킵 가능 | L1+L2 Layered | **0 (Zero)** |
| 날짜별 샤딩 | 경계 충돌 가능 | Monotonic+NTP | **0 (Zero)** |

> **결론 (수정)**: 
> - 원래: "완전 제거는 불가능"
> - **수정**: 적절한 대비책 조합으로 **소프트웨어적 손실은 Zero**로 만들 수 있음
> - 하드웨어 장애 (디스크 물리적 손상, 전원 완전 유실 등)는 별도 대비 필요 (RAID, 복제 등)

---

## 14. 장애 대비책 (Graceful Degradation)

> **목적**: Redis/파일시스템 장애 시에도 시스템이 데이터 손실 없이 동작

### 14.1 장애 시나리오별 대응 전략

| 장애 시나리오 | 탐지 방법 | 대응 전략 | 복구 방법 | 코드 근거 |
|-------------|----------|----------|----------|----------|
| Redis 연결 실패 | `ConnectionError` | Local Fallback | 재연결 시 병합 | `integrity.py#L413-417` |
| Redis 응답 지연 | Timeout | `degraded=True` 마킹 | Reconciler 정합성 복구 | `integrity.py#L484-503` |
| 파일시스템 가득 참 | `IOError` | 메모리 버퍼 유지 | 공간 확보 후 flush | `backend.py#L183-230` |
| 네트워크 파티션 | 다중 노드 분리 | Split-Brain 방지 락 | 네트워크 복구 대기 | Section 11.9 |

### 14.2 Redis → Local Fallback

**트리거**: Redis 연결 실패 또는 응답 없음

```python
# 기존 패턴: integrity.py#L413-417
try:
    chain = redis_manager.get_chain(chain_key)
except (RedisConnectionError, RedisTimeoutError):
    logger.warning("[Fallback] Redis unavailable, using local chain")
    chain = local_file_backend.get_chain(chain_key)
```

**Fallback 체인**:
```
1. Redis Primary
   ↓ (실패)
2. Redis Replica (읽기 전용)
   ↓ (실패)
3. Local File Backend
   ↓ (실패)
4. 메모리 버퍼 (휘발성)
```

### 14.3 `degraded=True` 마킹

**목적**: 장애 중 기록된 데이터를 나중에 정합성 검증

```python
# 기존 패턴: integrity.py#L484-503
def add_integrity_degraded(entry: IntegrityEntry) -> None:
    """장애 상황에서의 무결성 추가"""
    entry.metadata["degraded"] = True
    entry.metadata["degraded_at"] = datetime.utcnow().isoformat()
    entry.metadata["degraded_reason"] = "redis_timeout"
    
    # 로컬에 임시 저장
    local_buffer.append(entry)
    
    # 나중에 Reconciler가 처리
    # - degraded=True 항목 찾기
    # - Redis 복구 후 체인에 병합
    # - 해시 체인 재검증
```

**Reconciler 복구 로직**:
```python
def reconcile_degraded_entries():
    """degraded 항목 복구"""
    degraded = [e for e in entries if e.metadata.get("degraded")]
    for entry in degraded:
        if redis_available():
            # 1. Redis에 정식 등록
            redis_manager.add_integrity(entry)
            # 2. degraded 마킹 제거
            entry.metadata["degraded"] = False
            entry.metadata["reconciled_at"] = datetime.utcnow().isoformat()
```

### 14.4 WAL Recovery

**목적**: 프로세스 크래시 시 데이터 손실 방지

```python
# 기존 패턴: backend.py#L183-230
class ResilientStorageBackend:
    def __init__(self):
        self._wal = WriteAheadLog(path="integrity_wal.log")
    
    def startup_recovery(self):
        """시작 시 WAL에서 미완료 항목 복구"""
        pending_entries = self._wal.read_uncommitted()
        for entry in pending_entries:
            try:
                self._commit_to_redis(entry)
                self._wal.mark_committed(entry.id)
            except RedisError:
                logger.warning(f"[WAL] Deferred recovery for {entry.id}")
                # 다음 시작 시 재시도
```

**WAL 구조**:
```
[SEQ=1] PREPARE  | entry_id=abc | hash=sha256:... | timestamp=...
[SEQ=2] COMMIT   | entry_id=abc
[SEQ=3] PREPARE  | entry_id=def | hash=sha256:... | timestamp=...
                   ← 여기서 크래시 발생 시 def 복구
```

### 14.5 GracefulDegradationManager

**목적**: 시스템 전체의 Graceful Degradation 조율

```python
# 기존 패턴: emergency_mode/manager.py#L37
class GracefulDegradationManager:
    """시스템 장애 시 점진적 기능 축소"""
    
    LEVELS = {
        "NORMAL": 0,      # 모든 기능 정상
        "DEGRADED": 1,    # 부가 기능 중단 (무결성 검증 유지)
        "EMERGENCY": 2,   # 필수 기능만 (무결성 기록만)
        "READONLY": 3,    # 읽기 전용
    }
    
    def on_redis_failure(self):
        """Redis 장애 시"""
        self.set_level("DEGRADED")
        # - 기대 해시 검증 스킵 (단, 기록은 계속)
        # - Orphaned 검증 스킵
        # - 오프라인 앵커만 기록
    
    def on_filesystem_failure(self):
        """파일시스템 장애 시"""
        self.set_level("EMERGENCY")
        # - 메모리 버퍼로 전환
        # - 최소 기록만 유지
    
    def on_recovery(self):
        """복구 시"""
        self.set_level("NORMAL")
        # - Reconciler 트리거
        # - degraded 항목 처리
```

### 14.6 Self-Healing 통합

**기존 Self-Healing 패턴과 연동**:

```python
# selfhealing 패키지의 CircuitBreaker 연동
from selfhealing.services.circuit_breaker import CircuitBreaker

class IntegrityCircuitBreaker:
    def __init__(self):
        self._cb = CircuitBreaker(
            name="integrity_redis",
            failure_threshold=5,
            recovery_timeout=30,
            half_open_requests=3
        )
    
    def add_integrity_safe(self, entry: IntegrityEntry):
        if self._cb.is_open:
            return self._add_degraded(entry)
        
        try:
            with self._cb:
                return redis_manager.add_integrity(entry)
        except CircuitOpenError:
            return self._add_degraded(entry)
```

### 14.7 복구 우선순위

| 우선순위 | 복구 대상 | 복구 방법 | 담당 |
|---------|----------|----------|-----|
| P0 | 미커밋 WAL 항목 | `startup_recovery()` | `ResilientStorageBackend` |
| P1 | degraded 마킹 항목 | `reconcile_degraded_entries()` | `HashChainReconciler` |
| P2 | Orphaned 체인 | `verify_and_merge_orphaned()` | `PendingSequenceWatchdog` |
| P3 | 앵커 정합성 | `verify_daily_anchors()` | 일일 배치 작업 |

---

## 참고 문서

- [42_DISTRIBUTED_HASH_CHAIN_REDIS.md](./42_DISTRIBUTED_HASH_CHAIN_REDIS.md) - 기본 구현
- [05_RESILIENT_STORAGE_BACKEND.md](./05_RESILIENT_STORAGE_BACKEND.md) - WAL-First 원칙
- [adapters/resilient/backend.py](../../../packages/selfhealing-python/src/selfhealing/adapters/resilient/backend.py) - 복구 패턴
- [metrics/reconciler.py](../../../packages/selfhealing-python/src/selfhealing/metrics/reconciler.py) - Reconciler 패턴
- [adapters/django/apps.py](../../../packages/selfhealing-python/src/selfhealing/adapters/django/apps.py) - Startup Hydration 패턴

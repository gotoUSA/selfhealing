# 55. Audit 시스템 수정 가이드

> **문서 버전**: 1.1.0
> **생성일**: 2026-01-01
> **최종 수정일**: 2026-01-01
> **목적**: Audit 시스템의 6가지 발견된 문제 수정 + 3가지 확장 가이드

---

## 📋 발견된 문제 요약

| # | 문제 | 심각도 | 상태 |
|---|------|-------|------|
| 1 | `get_audit_adapter()` 함수 미존재 | 🔴 Critical | ✅ 구현 완료 |
| 2 | Celery Task에서 actor_id 누락 | 🟡 Medium | ✅ 구현 완료 |
| 3 | ProviderRegistry에 audit_adapter 미등록 | 🟡 Medium | ✅ 구현 완료 |
| 4 | RateLimit 위치 변경 시 HealthBridge 간섭 | 🟡 Medium | 📖 가이드 제공 |
| 5 | WAL이 ContinuousAuditRecorder에 미연결 | 🟡 Medium | ✅ 구현 완료 |
| 6 | Exception Handling | 🟢 OK | 이미 적절함 |

### 📋 확장 기능

| # | 확장 | 심각도 | 상태 |
|---|------|-------|------|
| E1 | `ContextType` 스키마 표준화 | 🟡 Medium | ✅ 구현 완료 |
| E2 | WAL Group Commit 전략 | 🟡 Medium | ✅ 구현 완료 |
| E3 | Fail-Open 정책 명시 | 🔴 Critical | ✅ 구현 완료 |

---

## 🔴 수정 1: get_audit_adapter() 함수 미존재 (Critical)

### 문제 상황

```python
# error_budget_gate/gate.py L480
from selfhealing.adapters.audit import get_audit_adapter  # ← ImportError 발생!
```

`selfhealing/adapters/audit/__init__.py`에 `get_audit_adapter` 함수가 정의되어 있지 않음.

### 수정 방법

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/audit/__init__.py`

```python
# 추가할 내용
from typing import Optional
from selfhealing.interfaces.audit_adapter import AuditLogAdapter

# 기본 어댑터 인스턴스 (싱글톤)
_default_adapter: Optional[AuditLogAdapter] = None


def get_audit_adapter() -> AuditLogAdapter:
    """
    기본 AuditLogAdapter 인스턴스 반환.
    
    우선순위:
    1. ProviderRegistry에 등록된 어댑터
    2. 기본 FileAuditLogAdapter
    3. NullAuditLogAdapter (fallback)
    
    Returns:
        AuditLogAdapter 인스턴스
    """
    global _default_adapter
    
    if _default_adapter is not None:
        return _default_adapter
    
    # 1. ProviderRegistry 시도
    try:
        from selfhealing.factory import ProviderRegistry
        adapter = ProviderRegistry.get_audit_adapter()
        if adapter is not None:
            _default_adapter = adapter
            return adapter
    except (ImportError, ValueError, AttributeError):
        pass
    
    # 2. 기본 FileAuditLogAdapter
    try:
        from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter
        _default_adapter = FileAuditLogAdapter("logs/audit.jsonl")
        return _default_adapter
    except Exception:
        pass
    
    # 3. Fallback: NullAuditLogAdapter
    from selfhealing.adapters.audit.null_adapter import NullAuditLogAdapter
    _default_adapter = NullAuditLogAdapter()
    return _default_adapter


def set_audit_adapter(adapter: AuditLogAdapter) -> None:
    """기본 AuditLogAdapter 설정."""
    global _default_adapter
    _default_adapter = adapter


def reset_audit_adapter() -> None:
    """기본 AuditLogAdapter 초기화 (테스트용)."""
    global _default_adapter
    _default_adapter = None
```

**__all__ 업데이트**:
```python
__all__ = [
    # ... 기존 내용 ...
    "get_audit_adapter",
    "set_audit_adapter",
    "reset_audit_adapter",
]
```

### 테스트 확인

```python
# 테스트 코드
def test_get_audit_adapter():
    from selfhealing.adapters.audit import get_audit_adapter
    adapter = get_audit_adapter()
    assert adapter is not None
```

---

## 🟡 수정 2: Celery Task에서 actor_id 누락

### 문제 상황

```python
# shopping/tasks/dlq_replay_tasks.py
@shared_task
def replay_single_dlq_entry(self, dlq_id: int):
    service = get_replay_service()
    result = service.replay_single(dlq_id)  # actor_id = None!
```

Celery Worker에서는 Django request context가 없어 `ActorContext`가 비어있음.

### 수정 방법

**파일**: `shopping/tasks/dlq_replay_tasks.py`

```python
# 수정 전
@shared_task(bind=True, ...)
def replay_single_dlq_entry(self, dlq_id: int) -> dict:
    service = get_replay_service()
    result = service.replay_single(dlq_id)
    ...

# 수정 후
@shared_task(bind=True, ...)
def replay_single_dlq_entry(
    self, 
    dlq_id: int,
    actor_id: str = "celery_worker",
    actor_type: str = "scheduler",
) -> dict:
    from selfhealing.context.actor_context import ActorContext
    
    with ActorContext.set_actor(
        actor_id=actor_id,
        actor_type=actor_type,
        source="celery",
        metadata={"task_id": self.request.id},
    ):
        service = get_replay_service()
        result = service.replay_single(dlq_id)
        ...
```

### 적용 대상 Task 목록

| 파일 | Task 함수 |
|------|----------|
| `shopping/tasks/dlq_replay_tasks.py` | `replay_single_dlq_entry` |
| `shopping/tasks/dlq_replay_tasks.py` | `replay_batch_by_failure_type` |
| `shopping/tasks/dlq_replay_tasks.py` | `replay_batch_by_domain` |
| `shopping/tasks/dlq_replay_tasks.py` | `replay_on_circuit_breaker_recovery` |
| `shopping/tasks/dlq_replay_tasks.py` | `auto_replay_pending_entries` |

---

## 🟡 수정 3: ProviderRegistry에 audit_adapter 미등록

### 문제 상황

`ProviderRegistry.get_audit_adapter()` 메서드가 없음.
`audit_helpers.py`와 `governance_checks.py`에서 호출하지만 구현 안됨.

### 수정 방법

**파일**: `packages/selfhealing-python/src/selfhealing/factory.py`

```python
# ProviderRegistry 클래스에 추가

class ProviderRegistry:
    # 기존 레지스트리...
    _audit_adapters: dict[str, Type] = {}
    _default_audit: str = "file"
    
    # =========================================================================
    # Audit Adapter Registration
    # =========================================================================
    
    @classmethod
    def register_audit_adapter(cls, name: str, adapter_class: Type) -> None:
        """Register an audit log adapter."""
        cls._audit_adapters[name] = adapter_class
        logger.debug(f"[Registry] Registered audit adapter: {name}")
    
    @classmethod
    def get_audit_adapter(
        cls,
        name: Optional[str] = None,
        singleton: bool = True,
    ) -> "AuditLogAdapter":
        """
        Get audit adapter instance.
        
        Args:
            name: Adapter name (e.g., 'file', 'stdout', 'null')
            singleton: If True, return cached instance
            
        Returns:
            AuditLogAdapter instance
            
        Raises:
            ValueError: If no adapter registered
        """
        from selfhealing.interfaces.audit_adapter import AuditLogAdapter
        
        name = name or cls._default_audit
        
        if singleton:
            key = f"audit:{name}"
            if key in cls._instances:
                return cls._instances[key]
        
        if name not in cls._audit_adapters:
            # Auto-register defaults
            cls._auto_register_audit_adapters()
            
        if name not in cls._audit_adapters:
            raise ValueError(f"No audit adapter registered with name: {name}")
        
        adapter_class = cls._audit_adapters[name]
        
        # 기본 설정으로 인스턴스 생성
        if name == "file":
            instance = adapter_class("logs/audit.jsonl")
        elif name == "stdout":
            instance = adapter_class()
        elif name == "null":
            instance = adapter_class()
        else:
            instance = adapter_class()
        
        if singleton:
            cls._instances[key] = instance
        
        return instance
    
    @classmethod
    def _auto_register_audit_adapters(cls) -> None:
        """Auto-register default audit adapters."""
        try:
            from selfhealing.adapters.audit import (
                FileAuditLogAdapter,
                StdoutAuditLogAdapter,
                NullAuditLogAdapter,
            )
            cls.register_audit_adapter("file", FileAuditLogAdapter)
            cls.register_audit_adapter("stdout", StdoutAuditLogAdapter)
            cls.register_audit_adapter("null", NullAuditLogAdapter)
        except ImportError:
            pass
```

**_auto_register_adapters() 함수에 추가**:
```python
def _auto_register_adapters():
    # ... 기존 코드 ...
    
    # Audit adapters
    try:
        from selfhealing.adapters.audit import (
            FileAuditLogAdapter,
            StdoutAuditLogAdapter,
            NullAuditLogAdapter,
        )
        ProviderRegistry.register_audit_adapter("file", FileAuditLogAdapter)
        ProviderRegistry.register_audit_adapter("stdout", StdoutAuditLogAdapter)
        ProviderRegistry.register_audit_adapter("null", NullAuditLogAdapter)
    except ImportError:
        pass
```

---

## 🟡 수정 4: RateLimit 제외 경로 추가

### 문제 상황

`HybridRateLimitMiddleware`를 미들웨어 상단으로 옮기면 `/health/` 엔드포인트도 Rate Limit 체크 대상이 됨.
DB 장애 시 Health 체크가 막힐 수 있음.

### 수정 방법

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/rate_limit.py`

```python
# 클래스 상단에 제외 경로 추가

class HybridRateLimitMiddleware:
    """..."""
    
    # Rate Limit 제외 경로 (DB-independent 경로)
    EXCLUDED_PATHS = [
        "/api/self-healing/health/",
        "/health/",
        "/api/self-healing/circuit-breaker/pool/status/",
    ]
    
    def __call__(self, request: HttpRequest) -> HttpResponse:
        # 제외 경로 체크 (최우선)
        for excluded in self.EXCLUDED_PATHS:
            if request.path.startswith(excluded):
                return self.get_response(request)
        
        # Only apply to Control API
        if not request.path.startswith(CONTROL_API_PATH_PREFIX):
            return self.get_response(request)
        
        # ... 기존 로직 ...
```

### 권장 미들웨어 순서 (변경 시)

```python
MIDDLEWARE = [
    # === 최상단: Worker Saturation 방지 ===
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",
    # === Rate Limit (health 경로 제외됨) ===
    "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    # === Self-Healing ===
    "selfhealing.api.django.middleware.SelfHealingMiddleware",
    # ... 나머지 ...
]
```

---

## 🟡 수정 5: WAL 연동 설계 가이드

### 현재 상태

```
ContinuousAuditRecorder._record_with_integrity()
    └─ HashChainManager.add_integrity()  ← 해시 체인만 사용
    └─ AuditLogAdapter.log()             ← 직접 기록 (WAL 없음!)
```

### 목표 상태

```
ContinuousAuditRecorder._record_with_integrity()
    └─ HashChainManager.add_integrity()
    └─ WriteAheadLog.write()  ← WAL 먼저 기록
    └─ AuditLogAdapter.log()  ← 그 다음 Adapter
    └─ WriteAheadLog.commit() ← 성공 시 WAL 정리
```

### 구현 가이드

**파일**: `packages/selfhealing-python/src/selfhealing/audit/continuous_audit.py`

```python
class ContinuousAuditRecorder:
    def __init__(
        self,
        audit_adapter: AuditLogAdapter,
        config: Optional[AuditConfig] = None,
        alert_callback: Optional[Callable] = None,
        state_file: Optional[Path] = None,
        wal_enabled: bool = False,  # 신규 파라미터
        wal_config: Optional["WALConfig"] = None,  # 신규 파라미터
    ):
        # ... 기존 초기화 ...
        
        # WAL 초기화 (선택적)
        self._wal_enabled = wal_enabled
        self._wal: Optional["WriteAheadLog"] = None
        
        if wal_enabled:
            from selfhealing.audit.wal import WriteAheadLog, WALConfig
            self._wal = WriteAheadLog(config=wal_config or WALConfig())
    
    def _record_with_integrity(self, entry: AuditEntry) -> str:
        with self._lock:
            entry_dict = entry.to_dict()
            entry_dict = self._hash_manager.add_integrity(entry_dict)
            entry.details["integrity"] = entry_dict.get("integrity", {})
            
            # WAL 기록 (활성화된 경우)
            wal_seq = None
            if self._wal_enabled and self._wal:
                try:
                    wal_seq = self._wal.write(entry_dict)
                except Exception as e:
                    logger.warning(f"[ContinuousAudit] WAL write failed: {e}")
            
            # Adapter 기록
            self.audit_adapter.log(entry)
            
            # WAL 커밋 (성공 시)
            if wal_seq is not None and self._wal:
                try:
                    self._wal.mark_processed(wal_seq)
                except Exception as e:
                    logger.warning(f"[ContinuousAudit] WAL commit failed: {e}")
            
            # ID 생성
            integrity = entry_dict.get("integrity", {})
            audit_id = f"audit-{entry.timestamp.strftime('%Y%m%d%H%M%S')}-{integrity.get('sequence', 0):06d}"
            
            return audit_id
```

### 환경 설정

```python
# settings.py
AUDIT_WAL_ENABLED = os.getenv("AUDIT_WAL_ENABLED", "FALSE") == "TRUE"
AUDIT_WAL_DIR = os.getenv("AUDIT_WAL_DIR", "/var/log/audit/wal")
```

---

## 🟢 수정 6: Exception Handling (이미 적절함)

### 현재 상태

모든 Audit 로깅 지점에 `try-except` 방어가 있어 안전함.

| 위치 | 방어 패턴 |
|------|----------|
| middleware.py L1100 | `try-except → logger.warning` |
| pool_circuit_breaker.py L681 | `try-except → logger.debug` |
| governance_checks.py L112 | `try-except → logger.warning` |
| audit_helpers.py L66 | `try-except → logger.warning` |

### 추가 조치 없음

현재 패턴이 적절함: **Audit 실패가 메인 흐름을 중단시키지 않음**.

---

## � 확장 1: Audit 데이터 스키마 표준화 (수정 2 확장)

### 문제 상황

Celery Task와 미들웨어가 각기 다른 형식으로 Audit 데이터를 생성하면, 나중에 분석 시 일관성이 깨집니다.

```python
# 현재: actor_type만으로는 출처 구분 불가
entry = AuditEntry(
    action=AuditAction.DLQ_REPLAY_SUCCESS,
    actor_type="scheduler",  # ← 이게 미들웨어인지 Celery인지 알 수 없음
)
```

### 해결책: `ContextType` Enum 추가

**파일**: `packages/selfhealing-python/src/selfhealing/interfaces/audit_adapter.py`

```python
from enum import Enum

class ContextType(str, Enum):
    """Audit 이벤트 발생 컨텍스트 유형."""
    REQUEST = "request"      # HTTP 요청 처리 중 (미들웨어)
    TASK = "task"           # 백그라운드 태스크 (Celery, RQ)
    SYSTEM = "system"       # 시스템 자동화 (스케줄러, 자동 복구)
    WEBHOOK = "webhook"     # 외부 웹훅 처리
    CLI = "cli"             # CLI 명령 실행
    UNKNOWN = "unknown"     # 알 수 없음 (폴백)


@dataclass
class AuditEntry:
    action: AuditAction | str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    # Actor information
    actor_id: Optional[str] = field(default=None)
    actor_type: str = field(default="system")
    
    # 🆕 Context type - 발생 환경 구분
    context_type: ContextType = field(default=ContextType.UNKNOWN)
    
    # ... (기존 필드 유지) ...
```

### 업계 사례

| 시스템 | 구현 방식 |
|--------|----------|
| **AWS CloudTrail** | `eventSource` + `eventType`으로 API/Console/Lambda 구분 |
| **Datadog APM** | `trace.origin` 필드로 HTTP/Queue/Cron 구분 |
| **OpenTelemetry** | `SpanKind` (SERVER, CONSUMER, PRODUCER, INTERNAL) |

### 분석 시 이점

```sql
-- Elasticsearch/Splunk 쿼리 예시
SELECT COUNT(*) FROM audit_logs 
WHERE context_type = 'TASK' 
GROUP BY action, hour(timestamp)
```

---

## 🔵 확장 2: WAL 배치 커밋 전략 (수정 5 보완)

### 문제 상황

현재 WAL은 매 write마다 fsync를 수행하여 I/O 부하가 큽니다.

```python
# 현재: 매번 fsync
def write(self, data: Dict[str, Any]) -> int:
    ...
    if self._config.sync_on_write:
        self._current_handle.flush()
        os.fsync(self._current_handle.fileno())  # ← 비용 높음
```

### 해결책: Group Commit 옵션

**파일**: `packages/selfhealing-python/src/selfhealing/audit/wal.py`

```python
@dataclass
class WALConfig:
    """WAL 설정."""
    wal_dir: str = "/var/log/audit/wal"
    max_file_size_mb: int = 100
    sync_on_write: bool = True
    max_files: int = 10
    file_prefix: str = "audit_wal"
    
    # 🆕 Group Commit 설정
    group_commit_enabled: bool = False
    group_commit_max_entries: int = 100   # 최대 버퍼 엔트리 수
    group_commit_max_wait_ms: int = 10    # 최대 대기 시간 (ms)
    
    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024
```

### 구현 전략

```python
class WriteAheadLog:
    def __init__(self, ...):
        # Group Commit 버퍼
        self._group_buffer: List[Dict] = []
        self._last_flush_time: float = time.time()
        self._flush_lock = threading.Lock()
    
    def write(self, data: Dict[str, Any]) -> int:
        if self._config.group_commit_enabled:
            return self._buffered_write(data)
        return self._direct_write(data)
    
    def _buffered_write(self, data: Dict[str, Any]) -> int:
        with self._flush_lock:
            self._group_buffer.append(data)
            seq = self._sequence + len(self._group_buffer)
            
            # 플러시 조건 체크
            should_flush = (
                len(self._group_buffer) >= self._config.group_commit_max_entries
                or self._time_since_last_flush_ms() >= self._config.group_commit_max_wait_ms
            )
            
            if should_flush:
                self._flush_buffer()
            
            return seq
    
    def _flush_buffer(self) -> None:
        """버퍼의 모든 엔트리를 한 번에 기록."""
        if not self._group_buffer:
            return
        
        self._ensure_file_open()
        
        for entry in self._group_buffer:
            self._write_single_entry(entry)
        
        # 한 번의 fsync로 모든 엔트리 영속화
        if self._config.sync_on_write and self._current_handle:
            self._current_handle.flush()
            os.fsync(self._current_handle.fileno())
        
        self._group_buffer.clear()
        self._last_flush_time = time.time()
```

### 업계 사례

| 데이터베이스 | Group Commit 전략 |
|--------------|-------------------|
| **PostgreSQL** | `commit_delay` + `commit_siblings` 설정 |
| **MySQL InnoDB** | `innodb_flush_log_at_trx_commit=2` |
| **Kafka** | `linger.ms` + `batch.size`로 배치 전송 |
| **etcd WAL** | 16KB 버퍼 후 flush |

### 성능 향상 기대치

```
100 엔트리 × 개별 fsync = 100회 I/O
100 엔트리 × Group Commit = 1회 I/O
→ 99% I/O 감소
```

---

## 🔵 확장 3: Fail-Open 정책 명시 (Critical)

### 문제 상황

`ContinuousAuditRecorder._record_with_integrity()`에는 예외 처리가 없습니다!

```python
# 현재 코드 (위험!)
def _record_with_integrity(self, entry: AuditEntry) -> str:
    with self._lock:
        entry_dict = entry.to_dict()
        entry_dict = self._hash_manager.add_integrity(entry_dict)
        self.audit_adapter.log(entry)  # ← 실패하면 예외 전파!
        ...
```

이로 인해 Audit 저장소 장애 시 **비즈니스 로직까지 중단**될 수 있습니다.

### 해결책: 중앙화된 Fail-Open 패턴

**파일**: `packages/selfhealing-python/src/selfhealing/audit/continuous_audit.py`

```python
class ContinuousAuditRecorder:
    """
    FAIL-OPEN Design Policy:
    
    감사 로그 기록 실패가 비즈니스 처리를 방해하지 않습니다.
    - 기본: Audit 실패 → 경고 로그 + fallback stdout
    - 선택: fail_open=False로 Fail-Secure 모드 가능 (PCI-DSS)
    """
    
    def __init__(
        self,
        audit_adapter: AuditLogAdapter,
        config: Optional[AuditConfig] = None,
        fail_open: bool = True,           # 🆕 Fail-Open 정책
        fallback_to_stdout: bool = True,  # 🆕 실패 시 stdout 출력
        ...
    ):
        self._fail_open = fail_open
        self._fallback_to_stdout = fallback_to_stdout
        self._failed_write_count = 0  # 🆕 실패 통계
    
    def _record_with_integrity(self, entry: AuditEntry) -> str:
        with self._lock:
            entry_dict = entry.to_dict()
            entry_dict = self._hash_manager.add_integrity(entry_dict)
            entry.details["integrity"] = entry_dict.get("integrity", {})
            
            # Fail-Open 패턴 적용
            try:
                self.audit_adapter.log(entry)
            except Exception as e:
                self._failed_write_count += 1
                
                if self._fallback_to_stdout:
                    # Fallback: stdout에 최소한의 기록
                    import sys
                    print(
                        f"[FALLBACK_AUDIT_LOG] {entry.action}: {entry.to_json()}",
                        file=sys.stderr,
                    )
                
                if not self._fail_open:
                    # Fail-Secure 모드: 예외 전파
                    raise
                
                logger.warning(
                    f"[ContinuousAudit] Write failed (fail-open): {e}. "
                    f"Total failures: {self._failed_write_count}"
                )
            
            # ID 생성
            integrity = entry_dict.get("integrity", {})
            audit_id = f"audit-{entry.timestamp.strftime('%Y%m%d%H%M%S')}-{integrity.get('sequence', 0):06d}"
            
            return audit_id
```

### 업계 정책 비교

| 표준/시스템 | Fail 정책 | 근거 |
|------------|----------|------|
| **Netflix Zuul** | Fail-Open | 가용성 우선, 메트릭으로 모니터링 |
| **Stripe** | Fail-Open + 재시도 | 메모리 버퍼 후 비동기 flush |
| **PCI-DSS** | Fail-Secure **권장** | 단, 가용성 예외 허용 조항 있음 |
| **SOC2** | Fail-Open **허용** | 실패 기록만 있으면 됨 |

### 환경 변수 설정

```python
# settings.py
AUDIT_FAIL_OPEN = os.getenv("AUDIT_FAIL_OPEN", "TRUE") == "TRUE"
AUDIT_FALLBACK_STDOUT = os.getenv("AUDIT_FALLBACK_STDOUT", "TRUE") == "TRUE"
```

---

## 📊 수정 우선순위

```
1. get_audit_adapter() 함수 추가 (Critical - 런타임 에러)
2. ProviderRegistry 등록 (의존성)
3. Celery actor_id + context_type 래핑 (데이터 품질)
4. RateLimit 제외 경로 (안정성)
5. WAL 연동 + Group Commit (장기 과제)
6. Fail-Open 정책 적용 (Critical - 안정성)
```

---

## 🔗 관련 문서

- [37_CONTINUOUS_AUDIT_FINAL_IMPL.md](37_CONTINUOUS_AUDIT_FINAL_IMPL.md) - Audit 시스템 구현
- [53_UNCONNECTED_FEATURES_ANALYSIS.md](53_UNCONNECTED_FEATURES_ANALYSIS.md) - 미연결 기능 분석
- [54_LIBRARY_INTEGRATION_GUIDE.md](54_LIBRARY_INTEGRATION_GUIDE.md) - 라이브러리 통합 가이드

---

## 다음 단계: AuditMiddleware 구현

6가지 수정과 3가지 확장이 완료되면 중앙화된 `AuditMiddleware`를 구현할 수 있습니다.
자세한 설계는 **56_AUDIT_MIDDLEWARE_DESIGN.md**에서 다룹니다.

# 55. Audit 시스템 수정 가이드

> **문서 버전**: 1.0.0
> **생성일**: 2026-01-01
> **목적**: Audit 시스템의 6가지 발견된 문제 수정 가이드

---

## 📋 발견된 문제 요약

| # | 문제 | 심각도 | 상태 |
|---|------|-------|------|
| 1 | `get_audit_adapter()` 함수 미존재 | 🔴 Critical | 수정 필요 |
| 2 | Celery Task에서 actor_id 누락 | 🟡 Medium | 수정 필요 |
| 3 | ProviderRegistry에 audit_adapter 미등록 | 🟡 Medium | 수정 필요 |
| 4 | RateLimit 위치 변경 시 HealthBridge 간섭 | 🟡 Medium | 가이드 제공 |
| 5 | WAL이 ContinuousAuditRecorder에 미연결 | 🟡 Medium | 설계 가이드 |
| 6 | Exception Handling | 🟢 OK | 이미 적절함 |

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

## 📊 수정 우선순위

```
1. get_audit_adapter() 함수 추가 (Critical - 런타임 에러)
2. ProviderRegistry 등록 (의존성)
3. Celery actor_id 래핑 (데이터 품질)
4. RateLimit 제외 경로 (안정성)
5. WAL 연동 (장기 과제)
```

---

## 🔗 관련 문서

- [37_CONTINUOUS_AUDIT_FINAL_IMPL.md](37_CONTINUOUS_AUDIT_FINAL_IMPL.md) - Audit 시스템 구현
- [53_UNCONNECTED_FEATURES_ANALYSIS.md](53_UNCONNECTED_FEATURES_ANALYSIS.md) - 미연결 기능 분석
- [54_LIBRARY_INTEGRATION_GUIDE.md](54_LIBRARY_INTEGRATION_GUIDE.md) - 라이브러리 통합 가이드

---

## 다음 단계: AuditMiddleware 구현

6가지 수정이 완료되면 중앙화된 `AuditMiddleware`를 구현할 수 있습니다.
자세한 설계는 **56_AUDIT_MIDDLEWARE_DESIGN.md**에서 다룹니다.

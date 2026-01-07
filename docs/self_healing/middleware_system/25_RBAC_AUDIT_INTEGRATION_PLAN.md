# 25. RBAC-Audit 연동 및 Trace ID 일관성 구현 계획

## 문서 정보

| 항목 | 값 |
|------|-----|
| 버전 | 1.5.0 |
| 작성일 | 2026-01-07 |
| 최종 수정 | 2026-01-07 |
| 관련 문서 | 20_AUDIT_UNIFICATION_PLAN.md, 19_DLQ_AUTOMATION_BLUEPRINT.md |

---

## 0. 배경: 왜 이 연동이 필요한가?

### 0.1 자동화 중 수동 개입 가능 여부

DLQ, Replay, Retry, CB가 자동화되어 있지만, **수동 개입 API도 존재**합니다.

| 기능 | 자동화 | 수동 API | 파일 |
|------|--------|----------|------|
| DLQ Replay | Celery Beat 자동 | `DLQReplayView` | `api/django/dlq_views.py` |
| DLQ Retry | 자동 재시도 | `DLQRetryView` | `api/django/dlq_views.py` |
| CB Open | 임계값 도달 시 | `force_open()` | `circuit_breaker/manual_control.py` |
| CB Close | Half-Open 성공 시 | `force_close()` | `circuit_breaker/manual_control.py` |

**코드 근거** - `DLQReplayView` (`api/django/dlq_views.py`):
```python
class DLQReplayView(APIView):
    """수동 DLQ 리플레이 API."""
    permission_classes = [IsOperator]  # RBAC 적용
    
    def post(self, request, pk):
        service = ReplayService()
        result = service.replay_single(pk)
        # Audit 로깅됨
```

### 0.2 Idempotency 및 동시 실행 방지

**코드 근거** - `try_acquire_for_replay()` (`services/replay_service.py` L245-280):
```python
def _try_acquire_for_replay(self, dlq_id: int) -> tuple[bool, Optional[FailedOperation]]:
    """원자적으로 DLQ 항목을 REPLAYING 상태로 전환."""
    with transaction.atomic():
        failed_op = FailedOperation.objects.select_for_update(nowait=True).get(id=dlq_id)
        
        if failed_op.status != FailedOperationStatus.PENDING:
            return False, None  # 이미 처리 중이거나 완료됨
        
        failed_op.status = FailedOperationStatus.REPLAYING  # 상태 변경으로 동시 실행 방지
        failed_op.save()
        return True, failed_op
```

**동시 실행 방지 메커니즘:**
```
요청 A: try_acquire_for_replay(dlq_id=123)
    ↓ select_for_update(nowait=True)
    ↓ status = REPLAYING ✅ 성공
    
요청 B: try_acquire_for_replay(dlq_id=123)
    ↓ status != PENDING
    ↓ return False ❌ 거부됨
```

### 0.3 현재 Audit 기록의 한계

**현재 기록 내용:**
```json
{
  "actor_id": "admin@example.com",
  "actor_type": "user",          // ← 단순 "user"로만 기록
  "resolution_type": "manual"    // ← 수동 개입임은 알 수 있음
}
```

**문제점:**
- `actor_type="user"`는 어떤 권한으로 행동했는지 알 수 없음
- `selfhealing_admin`과 `selfhealing_operator`가 동일하게 `"user"`로 기록됨
- 기술 실사(Tech DD) 시 "권한 범위 내 행동" 증명 불가

---

## 0.4 리뷰 피드백 및 아키텍트 제안

### 리뷰 1: RBAC-Audit 연동 - "누가(Who)"를 증명하는 최종 조각

> "기술 실사(Tech DD) 시 '우리 시스템은 운영자가 자신의 권한 범위 내에서 행동했음을 감사 로그로 100% 증명할 수 있다'는 점을 강조하여 거버넌스 점수를 크게 높일 수 있습니다."

**결론**: RBAC 역할을 Audit에 기록하면 거버넌스 완전성 달성.

### 리뷰 2: trace_id 모든 Audit 기록 - 선택적 강제(Selective Enforcement)

모든 곳에 trace_id를 기록하는 것은 완벽한 관측성을 제공하지만, 현실적 제약이 존재합니다.

**현실적 문제 (코드 근거):**

| 문제 | 코드 현실 | 파일 |
|------|-----------|------|
| Celery Beat에서 trace_id 부재 | Task에 trace_id 파라미터 없음 | `adapters/celery/tasks/dlq_replay.py` |
| WAL에 trace_id 미기록 | `_write_to_wal()`에 trace_id 파라미터 없음 | `services/audit/base.py` L70 |
| Config Audit에는 trace_id 있음 | `"trace_id": get_trace_id()` | `audit/logger.py` L266 |

**아키텍트 제안 - 선택적 강제 전략:**

| Audit 유형 | trace_id 정책 | 이유 | 현재 구현 |
|------------|---------------|------|-----------|
| 운영자 액션 (RBAC) | **필수 기록** | 브라우저 세션 추적 필수 | ✅ `trace_id_middleware` 적용됨 |
| 장애 감지 (CB Open) | **필수 기록** | 원인 파악용 triggering_trace_id | ✅ `TraceContextProvider` 존재 |
| 단순 재시도 (Retry) | **선택 기록** | 로그 용량 우려 시 상위 trace_id만 | ⚠️ 미구현 |
| 배경 작업 (Beat) | **자체 생성** | `INTERNAL_BEAT_xxx` 식별자 | ⚠️ 미구현 |

---

## 1. 현재 상태 분석

### 1.1 RBAC 시스템 (구현 완료 ✅)

**파일**: `packages/selfhealing-python/src/selfhealing/api/django/permissions.py`

```
┌─────────────────────────────────────────────────────────────────┐
│                       RBAC 권한 계층                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  IsSelfHealingAdmin (selfhealing_admin 그룹)                     │
│       ↓ 상속                                                     │
│  IsOperator (selfhealing_operator 그룹)                          │
│       ↓ 상속                                                     │
│  IsViewer (selfhealing_viewer 그룹)                              │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**코드 근거** (`permissions.py` L55-95):
```python
class IsViewer(BasePermission):
    """읽기 전용 권한 (Viewer 역할)."""
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        # selfhealing_viewer, operator, admin 그룹 멤버십 확인
        return request.user.groups.filter(
            name__in=["selfhealing_viewer", "selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsOperator(BasePermission):
    """운영자 권한 (Operator 역할)."""
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.user.groups.filter(
            name__in=["selfhealing_operator", "selfhealing_admin"]
        ).exists()


class IsSelfHealingAdmin(BasePermission):
    """관리자 권한 (Admin 역할)."""
    
    def has_permission(self, request: Request, view: APIView) -> bool:
        return request.user.groups.filter(name="selfhealing_admin").exists()
```

### 1.2 ActorContext 시스템 (구현 완료 ✅)

**파일**: `packages/selfhealing-python/src/selfhealing/context/actor_context.py`

```
┌─────────────────────────────────────────────────────────────────┐
│                     ActorContext 데이터 흐름                      │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  HTTP Request                                                    │
│       ↓                                                          │
│  ActorContextMiddleware                                          │
│       ↓                                                          │
│  ActorContext.set_actor_from_django_request()                    │
│       ↓                                                          │
│  Actor 객체 생성 (contextvars에 저장)                            │
│       ↓                                                          │
│  AuditEntry.__post_init__() → ActorContext.get_current()         │
│       ↓                                                          │
│  actor_id, actor_type 자동 채워짐                                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**코드 근거** (`actor_context.py` L50-72):
```python
@dataclass
class Actor:
    """현재 작업을 수행하는 주체 정보."""
    
    actor_id: str
    actor_type: str = "user"           # ← 현재: "user" | "system" | "anonymous"
    source: str = "unknown"
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    set_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)  # ← 확장 가능
```

### 1.3 현재 문제점

**파일**: `actor_context.py` L145-180

```python
@classmethod
def set_actor_from_django_request(cls, request: Any) -> Generator[Actor, None, None]:
    # Extract user info
    if hasattr(request, "user") and request.user.is_authenticated:
        actor_id = getattr(request.user, "email", None) or str(request.user.pk)
        actor_type = "user"  # ❌ 단순 "user"로만 설정, RBAC 역할 정보 없음
    else:
        actor_id = "anonymous"
        actor_type = "anonymous"
    
    # ❌ 누락: request.user.groups 조회 없음
```

| 현재 기록 | 미기록 (문제) |
|-----------|---------------|
| `actor_id`: 이메일/PK | RBAC 역할 (`selfhealing_admin` 등) |
| `actor_type`: "user"/"system" | 사용자 그룹 목록 |
| `ip_address` | 권한 레벨 |
| `session_id` | |

---

## 2. 연동 순서

```
┌──────────────────────────────────────────────────────────────────────────┐
│                           연동 순서 (의존성 기반)                          │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Phase 1: 기반 인프라 수정                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 1.1 Actor 클래스에 roles 필드 추가                                │   │
│  │ 1.2 set_actor_from_django_request()에서 RBAC 역할 추출           │   │
│  │ 1.3 AuditEntry에 actor_roles 필드 추가                           │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 2: Audit Helper 함수 수정                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 2.1 _write_to_wal()에 actor_roles 파라미터 추가                   │   │
│  │ 2.2 _try_add_to_buffer()에 actor_roles 파라미터 추가             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 3: 개별 서비스 연동                                               │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 3.1 DLQ Audit (log_dlq_store_audit, log_dlq_replay_audit)        │   │
│  │ 3.2 CB Audit (log_cb_state_change_audit)                          │   │
│  │ 3.3 Replay Audit (ReplayService.replay_single)                    │   │
│  │ 3.4 Retry Audit (log_retry_audit)                                 │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│           ↓                                                              │
│  Phase 4: 테스트 및 검증                                                 │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │ 4.1 단위 테스트 추가                                              │   │
│  │ 4.2 통합 테스트 (RBAC → Audit 흐름)                               │   │
│  │ 4.3 Grafana 대시보드 쿼리 업데이트                                │   │
│  └──────────────────────────────────────────────────────────────────┘   │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

**순서가 중요한 이유:**
1. **Phase 1 필수 선행**: ActorContext가 역할 정보를 갖고 있어야 Audit Helper에서 사용 가능
2. **Phase 2 필수 선행**: Audit Helper 함수 시그니처가 변경되어야 개별 서비스에서 호출 가능
3. **Phase 3 독립 가능**: CB, DLQ, Replay, Retry는 Phase 2 완료 후 병렬 작업 가능

---

## 3. Phase 1: 기반 인프라 수정

### 3.1 Actor 클래스에 roles 필드 추가

**파일**: `packages/selfhealing-python/src/selfhealing/context/actor_context.py`

**현재 코드** (L50-72):
```python
@dataclass
class Actor:
    actor_id: str
    actor_type: str = "user"
    source: str = "unknown"
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    set_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
```

**수정 후 코드**:
```python
@dataclass
class Actor:
    actor_id: str
    actor_type: str = "user"
    source: str = "unknown"
    ip_address: Optional[str] = None
    session_id: Optional[str] = None
    set_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)
    
    # RBAC 역할 정보 (Phase 25 추가)
    roles: list[str] = field(default_factory=list)
    
    @property
    def highest_role(self) -> str:
        """RBAC 역할 중 가장 높은 권한 반환."""
        role_priority = {
            "selfhealing_admin": 3,
            "selfhealing_operator": 2,
            "selfhealing_viewer": 1,
        }
        if not self.roles:
            return self.actor_type  # fallback to actor_type
        return max(self.roles, key=lambda r: role_priority.get(r, 0), default=self.actor_type)
```

### 3.2 set_actor_from_django_request() 수정

**파일**: `packages/selfhealing-python/src/selfhealing/context/actor_context.py`

**현재 코드** (L145-180):
```python
@classmethod
def set_actor_from_django_request(cls, request: Any) -> Generator[Actor, None, None]:
    if hasattr(request, "user") and request.user.is_authenticated:
        actor_id = getattr(request.user, "email", None) or str(request.user.pk)
        actor_type = "user"
    else:
        actor_id = "anonymous"
        actor_type = "anonymous"

    return cls.set_actor(
        actor_id=actor_id,
        actor_type=actor_type,
        source=source,
        ...
    )
```

**수정 후 코드**:
```python
@classmethod
def set_actor_from_django_request(cls, request: Any) -> Generator[Actor, None, None]:
    if hasattr(request, "user") and request.user.is_authenticated:
        actor_id = getattr(request.user, "email", None) or str(request.user.pk)
        
        # RBAC 역할 추출 (Phase 25)
        roles = cls._extract_selfhealing_roles(request.user)
        
        # actor_type을 가장 높은 RBAC 역할로 설정
        if roles:
            actor_type = cls._get_highest_role(roles)
        else:
            actor_type = "user"
    else:
        actor_id = "anonymous"
        actor_type = "anonymous"
        roles = []

    return cls.set_actor(
        actor_id=actor_id,
        actor_type=actor_type,
        source=source,
        roles=roles,  # 새 파라미터
        ...
    )

@classmethod
def _extract_selfhealing_roles(cls, user: Any) -> list[str]:
    """사용자의 selfhealing RBAC 그룹 추출."""
    try:
        if hasattr(user, "groups"):
            return list(
                user.groups.filter(
                    name__startswith="selfhealing_"
                ).values_list("name", flat=True)
            )
    except Exception:
        pass
    return []

@classmethod
def _get_highest_role(cls, roles: list[str]) -> str:
    """RBAC 역할 중 가장 높은 권한 반환."""
    role_priority = {
        "selfhealing_admin": 3,
        "selfhealing_operator": 2,
        "selfhealing_viewer": 1,
    }
    return max(roles, key=lambda r: role_priority.get(r, 0), default="user")
```

### 3.3 set_actor() 시그니처 수정

**현재 코드** (L113-140):
```python
@classmethod
@contextmanager
def set_actor(
    cls,
    actor_id: str,
    actor_type: str = "user",
    source: str = "unknown",
    ip_address: Optional[str] = None,
    session_id: Optional[str] = None,
    **metadata: Any,
) -> Generator[Actor, None, None]:
```

**수정 후 코드**:
```python
@classmethod
@contextmanager
def set_actor(
    cls,
    actor_id: str,
    actor_type: str = "user",
    source: str = "unknown",
    ip_address: Optional[str] = None,
    session_id: Optional[str] = None,
    roles: Optional[list[str]] = None,  # 새 파라미터
    **metadata: Any,
) -> Generator[Actor, None, None]:
    actor = Actor(
        actor_id=actor_id,
        actor_type=actor_type,
        source=source,
        ip_address=ip_address,
        session_id=session_id,
        metadata=metadata,
        roles=roles or [],  # 새 필드
    )
```

### 3.4 AuditEntry 수정

**파일**: `packages/selfhealing-python/src/selfhealing/interfaces/audit_adapter.py`

**현재 코드** (L131-170):
```python
@dataclass
class AuditEntry:
    action: AuditAction | str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: Optional[str] = field(default=None)
    actor_type: str = field(default="system")
    context_type: ContextType = field(default=ContextType.UNKNOWN)
    # ...
```

**수정 후 코드**:
```python
@dataclass
class AuditEntry:
    action: AuditAction | str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: Optional[str] = field(default=None)
    actor_type: str = field(default="system")
    actor_roles: list[str] = field(default_factory=list)  # 새 필드
    context_type: ContextType = field(default=ContextType.UNKNOWN)
    # ...

    def __post_init__(self) -> None:
        """ActorContext에서 actor 정보 자동 채우기."""
        if self.actor_id is None and self.actor_type == "system":
            auto_actor_id, auto_actor_type = _get_default_actor()
            if auto_actor_id is not None:
                object.__setattr__(self, "actor_id", auto_actor_id)
                object.__setattr__(self, "actor_type", auto_actor_type)
        
        # RBAC 역할 자동 채우기 (Phase 25)
        if not self.actor_roles:
            actor = _get_actor_with_roles()
            if actor and actor.roles:
                object.__setattr__(self, "actor_roles", actor.roles)
```

---

## 4. Phase 2: Audit Helper 함수 수정

### 4.1 _write_to_wal() 수정

**파일**: `packages/selfhealing-python/src/selfhealing/services/audit/base.py`

```python
def _write_to_wal(
    event_type: str,
    source: str,
    details: Dict[str, Any],
    success: bool = True,
    error_message: Optional[str] = None,
    domain: Optional[str] = None,
    target_id: Optional[str] = None,
    actor_roles: Optional[list[str]] = None,  # 새 파라미터
) -> Optional[int]:
    """WAL에 이벤트 기록."""
    # ActorContext에서 자동으로 역할 가져오기
    if actor_roles is None:
        try:
            from selfhealing.context.actor_context import ActorContext
            actor = ActorContext.get_current()
            actor_roles = actor.roles
        except Exception:
            actor_roles = []
    
    # WAL 레코드에 역할 정보 포함
    wal_record = {
        "event_type": event_type,
        "source": source,
        "details": details,
        "success": success,
        "error_message": error_message,
        "domain": domain,
        "target_id": target_id,
        "actor_roles": actor_roles,  # 새 필드
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
```

### 4.2 개별 Audit Helper 함수에 actor_roles 전파

각 Audit Helper 함수는 수정이 필요 없음 - `_write_to_wal()`이 ActorContext에서 자동으로 가져옴.

**자동 전파 흐름:**
```
HTTP Request
    ↓
ActorContextMiddleware.set_actor_from_django_request()
    ↓ roles 설정
Actor(roles=["selfhealing_admin"])
    ↓ contextvars에 저장
log_cb_state_change_audit() 호출
    ↓
_write_to_wal()
    ↓ ActorContext.get_current().roles
WAL 레코드에 actor_roles 포함
```

---

## 5. Phase 3: 개별 서비스 연동

### 5.1 영향 받는 Audit Helper 함수 목록

| 함수 | 파일 | 연동 방식 |
|------|------|-----------|
| `log_dlq_store_audit` | `audit/dlq_audit.py` | 자동 (ActorContext) |
| `log_dlq_replay_audit` | `audit/dlq_audit.py` | 자동 (ActorContext) |
| `log_cb_state_change_audit` | `audit/cb_audit.py` | 자동 (ActorContext) |
| `log_governance_blocked_audit` | `audit/cb_audit.py` | 자동 (ActorContext) |
| `log_retry_audit` | `audit/retry_audit.py` | 자동 (ActorContext) |
| `log_rollback_audit` | `audit/retry_audit.py` | 자동 (ActorContext) |

**코드 근거** - 현재 호출 지점:

#### DLQ Store (자동)
`packages/selfhealing-python/src/selfhealing/services/dlq/base.py` L98-106:
```python
def _log_dlq_audit(self, action: str, dlq_id: int, domain: str, ...):
    if action == "store":
        log_dlq_store_audit(
            dlq_id=dlq_id,
            domain=domain,
            failure_type=failure_type,
            error_message=error_message,
            request=request,  # ← request에서 ActorContext 자동 설정
        )
```

#### DLQ Replay (자동)
`packages/selfhealing-python/src/selfhealing/services/replay_service.py` L400-405:
```python
# Audit 로깅: DLQ 리플레이 결과 기록
log_dlq_replay_audit(
    dlq_id=dlq_id,
    domain=failed_op_data.domain if failed_op_data else "unknown",
    success=result.success,
    error_message=result.error,
)
```

#### CB State Change (자동)
`packages/selfhealing-python/src/selfhealing/services/circuit_breaker/manual_control.py` L143-152:
```python
from selfhealing.services.audit_helpers import log_cb_state_change_audit
log_cb_state_change_audit(
    cb_name=service_name,
    old_state=previous_state,
    new_state=new_state,
    reason=f"force_open: {reason}" if reason else "force_open: manual",
)
```

### 5.2 수동 actor_id 전달이 필요한 경우

Celery Task처럼 HTTP 컨텍스트가 없는 경우:

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/celery/tasks/dlq_replay.py`

```python
@shared_task
def replay_single_dlq_entry(self, dlq_id: int, actor_info: dict = None) -> dict:
    """DLQ 항목 리플레이 (Celery Task)."""
    
    # Celery Task에서 ActorContext 복원
    from selfhealing.context.actor_context import restore_actor_from_celery
    
    with restore_actor_from_celery(actor_info):
        # 이제 ActorContext가 설정됨 → Audit에 역할 정보 포함
        service = ReplayService()
        result = service.replay_single(dlq_id)
```

**호출 측 (View에서 Task 호출 시):**
```python
from selfhealing.context.actor_context import get_actor_for_celery

class DLQReplayView(APIView):
    def post(self, request, pk):
        # actor_info를 Task에 전달
        replay_single_dlq_entry.delay(
            dlq_id=pk,
            actor_info=get_actor_for_celery(),  # 역할 정보 포함
        )
```

---

## 6. Phase 4: 테스트 및 검증

### 6.1 단위 테스트 추가

**파일**: `tests/self_healing/unit/test_rbac_audit_integration.py`

```python
class TestRBACAuditIntegration:
    """RBAC-Audit 연동 테스트."""
    
    def test_actor_context_extracts_selfhealing_roles(self):
        """Django 요청에서 RBAC 역할이 추출되는지 검증."""
        # Given
        mock_request = MagicMock()
        mock_request.user.is_authenticated = True
        mock_request.user.email = "admin@example.com"
        mock_request.user.groups.filter.return_value.values_list.return_value = [
            "selfhealing_admin"
        ]
        
        # When
        with ActorContext.set_actor_from_django_request(mock_request):
            actor = ActorContext.get_current()
        
        # Then
        assert "selfhealing_admin" in actor.roles
        assert actor.actor_type == "selfhealing_admin"  # 가장 높은 역할
    
    def test_audit_entry_includes_roles(self):
        """AuditEntry에 역할 정보가 포함되는지 검증."""
        # Given
        with ActorContext.set_actor(
            actor_id="test@example.com",
            actor_type="selfhealing_operator",
            roles=["selfhealing_operator", "selfhealing_viewer"],
        ):
            # When
            entry = AuditEntry(action=AuditAction.DLQ_REPLAY_SUCCESS)
        
        # Then
        assert entry.actor_roles == ["selfhealing_operator", "selfhealing_viewer"]
    
    def test_wal_includes_actor_roles(self):
        """WAL 레코드에 역할 정보가 포함되는지 검증."""
        # Given
        with ActorContext.set_actor(
            actor_id="admin@example.com",
            actor_type="selfhealing_admin",
            roles=["selfhealing_admin"],
        ):
            # When
            wal_seq = log_dlq_replay_audit(
                dlq_id=123,
                domain="payment",
                success=True,
            )
        
        # Then
        wal = get_wal_instance()
        records = wal.read_all()
        last_record = records[-1]
        assert last_record["actor_roles"] == ["selfhealing_admin"]
```

### 6.2 통합 테스트

**파일**: `tests/self_healing/integration/test_rbac_audit_flow.py`

```python
@pytest.mark.django_db
class TestRBACAuditFlowE2E:
    """RBAC → Audit 전체 흐름 E2E 테스트."""
    
    def test_admin_force_open_cb_audit_includes_role(self, client):
        """Admin이 CB를 force_open하면 audit에 역할이 기록되는지 검증."""
        # Given: selfhealing_admin 그룹에 속한 사용자
        admin_user = User.objects.create_user("admin@example.com")
        admin_group, _ = Group.objects.get_or_create(name="selfhealing_admin")
        admin_user.groups.add(admin_group)
        
        client.force_login(admin_user)
        
        # When: CB force_open API 호출
        response = client.post(
            "/api/self-healing/cb/toss-api/force-open/",
            {"reason": "PG 점검"},
            content_type="application/json",
        )
        
        # Then: Audit 로그에 역할 정보 포함
        assert response.status_code == 200
        
        # WAL 확인
        wal = get_wal_instance()
        records = wal.read_all()
        cb_record = [r for r in records if r["event_type"] == "CB_STATE_CHANGE"][-1]
        
        assert cb_record["actor_roles"] == ["selfhealing_admin"]
```

### 6.3 Grafana 대시보드 업데이트

**파일**: `docker/grafana/provisioning/dashboards/audit_monitoring.json`

새 패널 추가:
```json
{
  "title": "Actions by RBAC Role",
  "type": "piechart",
  "targets": [
    {
      "expr": "sum(increase(selfhealing_audit_events_total[1h])) by (actor_role)",
      "legendFormat": "{{actor_role}}"
    }
  ]
}
```

---

## 7. Phase 5: trace_id 일관성 확보

리뷰 피드백을 반영하여 trace_id를 모든 Audit에 일관되게 기록합니다.

### 7.1 _write_to_wal()에 trace_id 추가

**파일**: `packages/selfhealing-python/src/selfhealing/services/audit/base.py`

**현재 코드** (L70-110):
```python
def _write_to_wal(
    event_type: str,
    source: str,
    details: Dict[str, Any],
    success: bool = True,
    error_message: Optional[str] = None,
    domain: Optional[str] = None,
    target_id: Optional[str] = None,
) -> Optional[int]:
    # ❌ trace_id 파라미터 없음
```

**수정 후 코드**:
```python
def _write_to_wal(
    event_type: str,
    source: str,
    details: Dict[str, Any],
    success: bool = True,
    error_message: Optional[str] = None,
    domain: Optional[str] = None,
    target_id: Optional[str] = None,
    actor_roles: Optional[list[str]] = None,
    trace_id: Optional[str] = None,  # 새 파라미터
) -> Optional[int]:
    """WAL에 이벤트 기록."""
    # trace_id 자동 fallback
    if trace_id is None:
        try:
            from selfhealing.audit.trace import get_trace_id
            trace_id = get_trace_id()
        except Exception:
            trace_id = None
    
    wal_entry = {
        "record_id": record_id,
        "event_type": event_type,
        "trace_id": trace_id,  # 새 필드
        "source": source,
        "details": details,
        "actor_roles": actor_roles,
        ...
    }
```

### 7.2 Celery Task용 trace_id 전파

**새 함수 추가** - `packages/selfhealing-python/src/selfhealing/audit/trace.py`:

```python
def get_trace_for_celery() -> dict[str, Any]:
    """Celery Task에 전달할 trace 정보."""
    return {
        "trace_id": get_trace_id(),
        "source": "celery_propagated",
    }


@contextmanager
def restore_trace_from_celery(trace_info: Optional[dict[str, Any]]) -> Generator[str, None, None]:
    """Celery Task에서 trace 복원 또는 자체 생성."""
    if trace_info and trace_info.get("trace_id"):
        # 전파된 trace_id 사용
        with TraceContext(trace_info["trace_id"]) as trace_id:
            yield trace_id
    else:
        # 배경 작업: 새 trace_id 생성 (INTERNAL_BEAT_xxx 패턴)
        internal_trace_id = f"INTERNAL_BEAT_{generate_trace_id()}"
        with TraceContext(internal_trace_id) as trace_id:
            yield trace_id
```

### 7.3 Celery Beat Task 수정

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/celery/tasks/dlq_replay.py`

**현재 코드** (L27-55):
```python
@shared_task(...)
def replay_single_dlq_entry(self, dlq_id: int) -> dict:
    # ❌ trace_id 컨텍스트 없음
    service = ReplayService()
    result = service.replay_single(dlq_id)
```

**수정 후 코드**:
```python
@shared_task(...)
def replay_single_dlq_entry(
    self,
    dlq_id: int,
    actor_info: dict = None,
    trace_info: dict = None,  # 새 파라미터
) -> dict:
    """DLQ 항목 리플레이 (Celery Task)."""
    from selfhealing.context.actor_context import restore_actor_from_celery
    from selfhealing.audit.trace import restore_trace_from_celery
    
    # trace_info 없으면 Beat에서 호출된 것으로 간주 → 자체 생성
    with restore_trace_from_celery(trace_info):
        with restore_actor_from_celery(actor_info):
            service = ReplayService()
            result = service.replay_single(dlq_id)
    
    return {...}
```

**View에서 Task 호출 시:**
```python
from selfhealing.context.actor_context import get_actor_for_celery
from selfhealing.audit.trace import get_trace_for_celery

class DLQReplayView(APIView):
    def post(self, request, pk):
        replay_single_dlq_entry.delay(
            dlq_id=pk,
            actor_info=get_actor_for_celery(),
            trace_info=get_trace_for_celery(),  # trace_id 전파
        )
```

### 7.4 trace_id 정책 요약

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      trace_id 선택적 강제 정책                           │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  HTTP 요청 (운영자 액션)                                                │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ trace_id_middleware → get_trace_id() → 모든 Audit에 자동 포함  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Celery Task (View에서 호출)                                            │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ get_trace_for_celery() → trace_info 전달 → 원본 trace_id 유지  │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  Celery Beat (스케줄러 자동 호출)                                       │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │ trace_info=None → INTERNAL_BEAT_xxx 자체 생성                   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8. 파일 변경 목록

| Phase | 파일 | 변경 내용 |
|-------|------|-----------|
| 1.1 | `context/actor_context.py` | `Actor.roles` 필드 추가 |
| 1.2 | `context/actor_context.py` | `set_actor_from_django_request()` 역할 추출 |
| 1.3 | `context/actor_context.py` | `set_actor()` roles 파라미터 |
| 1.4 | `interfaces/audit_adapter.py` | `AuditEntry.actor_roles` 필드 |
| 2.1 | `services/audit/base.py` | `_write_to_wal()` actor_roles |
| 3.1 | (자동) | DLQ Audit - ActorContext 자동 전파 |
| 3.2 | (자동) | CB Audit - ActorContext 자동 전파 |
| 3.3 | (자동) | Replay Audit - ActorContext 자동 전파 |
| 3.4 | (자동) | Retry Audit - ActorContext 자동 전파 |
| 3.5 | `adapters/celery/tasks/*.py` | Celery Task actor_info 전달 |
| 4.1 | `tests/*/test_rbac_audit_*.py` | 테스트 추가 |
| 4.2 | `docker/grafana/*/audit_*.json` | 대시보드 업데이트 |
| **5.1** | `services/audit/base.py` | `_write_to_wal()` trace_id 파라미터 |
| **5.2** | `audit/trace.py` | `get_trace_for_celery()`, `restore_trace_from_celery()` |
| **5.3** | `adapters/celery/tasks/*.py` | trace_info 파라미터 및 자체 생성 로직 |

---

## 9. 예상 결과

### 8.1 현재 Audit 로그
```json
{
  "event_type": "CB_STATE_CHANGE",
  "actor_id": "admin@example.com",
  "actor_type": "user",
  "details": {
    "cb_name": "toss-api",
    "old_state": "closed",
    "new_state": "open"
  }
}
```

### 8.2 연동 후 Audit 로그
```json
{
  "event_type": "CB_STATE_CHANGE",
  "trace_id": "req-a1b2c3d4",
  "actor_id": "admin@example.com",
  "actor_type": "selfhealing_admin",
  "actor_roles": ["selfhealing_admin", "selfhealing_operator"],
  "details": {
    "cb_name": "toss-api",
    "old_state": "closed",
    "new_state": "open"
  }
}
```

### 8.3 Celery Beat에서 자동 실행 시 Audit 로그
```json
{
  "event_type": "DLQ_REPLAY_SUCCESS",
  "trace_id": "INTERNAL_BEAT_req-x9y8z7w6",
  "actor_id": "system",
  "actor_type": "system",
  "actor_roles": [],
  "details": {
    "dlq_id": 456,
    "domain": "payment"
  }
}
```

---

## 10. 작업 체크리스트

### Phase 1-4: RBAC-Audit 연동
- [x] **Phase 1.1**: Actor 클래스에 roles 필드 추가 ✅ (2026-01-07)
- [x] **Phase 1.2**: _extract_selfhealing_roles() 구현 ✅ (2026-01-07)
- [x] **Phase 1.3**: set_actor() roles 파라미터 추가 ✅ (2026-01-07)
- [x] **Phase 1.4**: AuditEntry.actor_roles 필드 추가 ✅ (2026-01-07)
- [x] **Phase 2.1**: _write_to_wal() actor_roles 지원 ✅ (2026-01-07)
- [x] **Phase 2.2**: _try_add_to_buffer() actor_roles 지원 ✅ (2026-01-07)
- [x] **Phase 3.5**: Celery Task actor_info 전달 ✅ (2026-01-07)
- [x] **Phase 4.1**: 단위 테스트 작성 ✅ (2026-01-07, 51개 테스트 통과)
- [x] **Phase 4.2**: 통합 테스트 작성 ✅ (2026-01-07, 13개 테스트 통과)
- [x] **Phase 4.3**: Grafana 대시보드 업데이트 ✅ (2026-01-07, RBAC 역할별 패널 5개 추가)

### Phase 5: trace_id 일관성 확보
- [x] **Phase 5.1**: _write_to_wal() trace_id 파라미터 추가 ✅ (2026-01-07)
- [x] **Phase 5.2**: get_trace_for_celery(), restore_trace_from_celery() 구현 ✅ (2026-01-07)
- [x] **Phase 5.3**: Celery Task에 trace_info 파라미터 추가 ✅ (2026-01-07)
- [x] **Phase 5.4**: Beat Task에 INTERNAL_BEAT_xxx 자체 생성 로직 추가 ✅ (2026-01-07)
- [x] **Phase 5.5**: 단위 테스트 14개 작성 및 통과 ✅ (2026-01-07)

---

## 11. FAQ

### Q1: 왜 CB, DLQ, Replay, Retry 각각 수정하지 않아도 되나요?

**A**: ActorContext가 contextvars 기반이므로, HTTP 요청 시작 시 한 번 설정하면 같은 요청 내 모든 코드에서 자동으로 접근 가능합니다.

```
HTTP Request → ActorContextMiddleware → ActorContext.set_actor()
                                              ↓
                                    (contextvars에 저장)
                                              ↓
            View → Service → log_cb_state_change_audit()
                                              ↓
                              _write_to_wal() → ActorContext.get_current()
                                              ↓
                                    actor.roles 자동 포함
```

### Q2: Celery Task에서는 어떻게 역할 정보를 유지하나요?

**A**: `get_actor_for_celery()`로 직렬화하여 Task에 전달하고, `restore_actor_from_celery()`로 복원합니다.

**코드 근거** (`actor_context.py` L319-360):
```python
def get_actor_for_celery() -> dict[str, Any]:
    """Get current actor info for passing to Celery task."""
    actor = ActorContext.get_current()
    return {
        "actor_id": actor.actor_id,
        "actor_type": actor.actor_type,
        "source": f"celery_from_{actor.source}",
        "roles": actor.roles,  # 역할 정보 포함
    }

@contextmanager
def restore_actor_from_celery(actor_info: dict) -> Generator[Actor, None, None]:
    """Restore actor context in Celery task."""
    with ActorContext.set_actor(
        actor_id=actor_info.get("actor_id", "unknown"),
        actor_type=actor_info.get("actor_type", "celery"),
        roles=actor_info.get("roles", []),  # 역할 복원
    ) as actor:
        yield actor
```

### Q3: trace_id가 없는 Celery Beat Task는 어떻게 처리하나요?

**A**: `restore_trace_from_celery()`가 trace_info=None일 때 `INTERNAL_BEAT_xxx` 형식으로 자체 생성합니다.

**코드 근거** (새로 추가될 코드):
```python
@contextmanager
def restore_trace_from_celery(trace_info: Optional[dict]) -> Generator[str, None, None]:
    if trace_info and trace_info.get("trace_id"):
        with TraceContext(trace_info["trace_id"]) as trace_id:
            yield trace_id
    else:
        # Beat에서 호출 시 자체 생성
        internal_trace_id = f"INTERNAL_BEAT_{generate_trace_id()}"
        with TraceContext(internal_trace_id) as trace_id:
            yield trace_id
```

**결과 예시:**
```json
{
  "event_type": "DLQ_REPLAY_SUCCESS",
  "trace_id": "INTERNAL_BEAT_req-a1b2c3d4",
  "actor_type": "system",
  "details": {...}
}
```

### Q4: 왜 모든 Audit에 trace_id를 필수로 넣지 않나요?

**A**: 리뷰어의 "선택적 강제(Selective Enforcement)" 전략을 채택했습니다.

| 이유 | 설명 |
|------|------|
| 로그 용량 | 수만 건의 Retry 로그에 trace_id 추가 시 용량 비대화 |
| 분석 비용 | 정적 분석 시 비용 상승 |
| 혼란 방지 | 억지로 생성한 trace_id는 오히려 오해 유발 |

**정책:**
- 운영자 액션: 필수 (HTTP 요청에서 자동 추출)
- CB Open: 필수 (triggering_trace_id)
- 단순 Retry: 선택 (상위 trace_id만)
- Beat: 자체 생성 (INTERNAL_BEAT_xxx)

---

## 변경 이력

| 버전 | 날짜 | 작성자 | 변경 내용 |
|------|------|--------|-----------|
| 1.0.0 | 2026-01-07 | AI Assistant | 초안 작성 |
| 1.1.0 | 2026-01-07 | AI Assistant | 리뷰 피드백 반영: 배경 섹션 추가, trace_id 일관성 Phase 5 추가, 선택적 강제 전략 문서화 |
| 1.2.0 | 2026-01-07 | AI Assistant | **Phase 1 구현 완료**: Actor.roles, _extract_selfhealing_roles(), set_actor() roles, AuditEntry.actor_roles, 39개 단위 테스트 |
| 1.3.0 | 2026-01-07 | AI Assistant | **Phase 2, 3 구현 완료**: _write_to_wal() actor_roles 자동 전파, _try_add_to_buffer() actor_roles 지원, Celery Task actor_info 전달, 51개 단위 테스트 통과 |
| 1.4.0 | 2026-01-07 | AI Assistant | **Phase 4 구현 완료**: 통합 테스트 13개 추가 (test_rbac_audit_flow.py), Grafana 대시보드 RBAC 역할별 패널 5개 추가 (dlq_monitoring.json), 전체 64개 테스트 통과 |
| 1.5.0 | 2026-01-07 | AI Assistant | **Phase 5 구현 완료**: trace_id 일관성 확보 - _write_to_wal() trace_id 파라미터, get_trace_for_celery(), restore_trace_from_celery(), Celery Task trace_info 파라미터, INTERNAL_BEAT_xxx 자체 생성, 14개 신규 테스트 추가, 전체 78개 테스트 통과 |

# 56. AuditMiddleware 설계 문서

> **문서 버전**: 1.1.0
> **생성일**: 2026-01-01
> **최종 수정일**: 2026-01-01
> **전제조건**: 55_AUDIT_SYSTEM_FIXES.md의 수정 완료
> **목적**: 중앙화된 AuditMiddleware 설계 및 구현 가이드
> **상태**: ✅ Phase 1 구현 완료

---

## 🎯 구현 완료 요약

| 구성 요소 | 파일 | 상태 |
|-----------|------|------|
| RequestAuditBuffer | `selfhealing/audit/event_buffer.py` | ✅ 완료 |
| AuditMiddleware | `selfhealing/api/django/audit_middleware.py` | ✅ 완료 |
| audit_helpers 하이브리드 | `selfhealing/services/audit_helpers.py` | ✅ 완료 |
| __init__.py export | `selfhealing/audit/__init__.py` | ✅ 완료 |

---

## 📋 개요

### 현재 문제: 분산된 Audit 호출

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    현재 상태: 분산된 Audit 호출                          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  SelfHealingMiddleware ─────────┬─▶ AuditLogger.log()                   │
│                                 │                                        │
│  PoolCircuitBreakerMiddleware ──┼─▶ ContinuousAuditRecorder.record()    │
│                                 │                                        │
│  DLQService ────────────────────┼─▶ audit_helpers.log_dlq_store_audit() │
│                                 │                                        │
│  ReplayService ─────────────────┼─▶ audit_helpers.log_dlq_replay_audit()│
│                                 │                                        │
│  GovernanceChecks ──────────────┴─▶ AuditLogAdapter.log_governance_*()  │
│                                                                          │
│  ❌ 문제점:                                                              │
│  • 3가지 다른 Audit 시스템 혼용                                          │
│  • 각 컴포넌트가 직접 Audit 호출 (일관성 부재)                           │
│  • HashChain 무결성이 일부 경로에서만 적용                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

### 목표: 중앙화된 AuditMiddleware

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    목표 상태: 중앙화된 Audit                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Request ──▶ [Entrance Middlewares] ──▶ [View] ──▶ [AuditMiddleware]    │
│                      │                     │              │              │
│                      │                     │              ▼              │
│                      │                     │      ┌──────────────┐       │
│                      ▼                     ▼      │ 이벤트 버퍼  │       │
│               request.META["X-AUDIT-EVENTS"]      │ 수집 & 배치  │       │
│               에 이벤트 적재                       │ 기록         │       │
│                                                   └──────┬───────┘       │
│                                                          │               │
│                                                          ▼               │
│                                            ContinuousAuditRecorder      │
│                                                   + HashChain           │
│                                                   + WAL (선택)          │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 🏗️ 아키텍처 설계

### 관문형 파이프라인 (Gateway Pipeline)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         미들웨어 파이프라인                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  [1] ENTRANCE (진입)                                                     │
│  ─────────────────                                                       │
│      HealthBridgeMiddleware    → DB-independent health                  │
│      HybridRateLimitMiddleware → Rate limiting (health 제외)            │
│      PoolCircuitBreakerMiddleware → Pool 고갈 방어                      │
│      SelfHealingMiddleware     → CB/DLQ 처리                            │
│                                                                          │
│  [2] EXECUTION (실행)                                                    │
│  ──────────────────                                                      │
│      Django Core Middlewares                                             │
│      View 처리                                                           │
│                                                                          │
│  [3] CAPTURE (캡처)                                                      │
│  ─────────────────                                                       │
│      AuditMiddleware           → 응답 캡처, 이벤트 수집                  │
│                                                                          │
│  [4] RECORDING (기록)                                                    │
│  ──────────────────                                                      │
│      ContinuousAuditRecorder   → HashChain + WAL + Adapter              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 📦 AuditMiddleware 구현

### 1. 이벤트 버퍼 클래스

```python
# selfhealing/audit/event_buffer.py

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from enum import Enum


class AuditEventType(Enum):
    """Audit 이벤트 유형."""
    DLQ_STORE = "dlq_store"
    DLQ_REPLAY = "dlq_replay"
    CB_STATE_CHANGE = "circuit_breaker_state_change"
    CB_REJECTION = "circuit_breaker_rejection"
    GOVERNANCE_BLOCKED = "governance_blocked"
    RATE_LIMITED = "rate_limited"
    ERROR_DETECTED = "error_detected"
    CONFIG_CHANGE = "config_change"
    MANUAL_OVERRIDE = "manual_override"


@dataclass
class AuditEvent:
    """Audit 이벤트."""
    event_type: AuditEventType
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str = "unknown"
    details: Dict[str, Any] = field(default_factory=dict)
    actor_id: Optional[str] = None
    actor_type: str = "system"
    success: bool = True
    error_message: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "source": self.source,
            "details": self.details,
            "actor_id": self.actor_id,
            "actor_type": self.actor_type,
            "success": self.success,
            "error_message": self.error_message,
        }


class RequestAuditBuffer:
    """
    요청별 Audit 이벤트 버퍼.
    
    request.META에 저장되어 미들웨어 체인 전체에서 이벤트 수집.
    AuditMiddleware에서 최종 기록.
    """
    
    META_KEY = "X-AUDIT-EVENTS"
    
    def __init__(self):
        self.events: List[AuditEvent] = []
        self.request_id: Optional[str] = None
        self.start_time: datetime = datetime.now(timezone.utc)
    
    def add_event(self, event: AuditEvent) -> None:
        """이벤트 추가."""
        self.events.append(event)
    
    def add(
        self,
        event_type: AuditEventType,
        source: str,
        details: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> None:
        """편의 메서드: 이벤트 생성 및 추가."""
        event = AuditEvent(
            event_type=event_type,
            source=source,
            details=details or {},
            **kwargs,
        )
        self.add_event(event)
    
    def get_events(self) -> List[AuditEvent]:
        """모든 이벤트 반환."""
        return self.events.copy()
    
    def has_events(self) -> bool:
        """이벤트 존재 여부."""
        return len(self.events) > 0
    
    @classmethod
    def get_or_create(cls, request) -> "RequestAuditBuffer":
        """request에서 버퍼 가져오거나 생성."""
        if not hasattr(request, "META"):
            return cls()
        
        if cls.META_KEY not in request.META:
            request.META[cls.META_KEY] = cls()
        
        return request.META[cls.META_KEY]
```

### 2. AuditMiddleware 구현

```python
# selfhealing/api/django/audit_middleware.py

import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from django.http import HttpRequest, HttpResponse

from selfhealing.audit.event_buffer import RequestAuditBuffer, AuditEvent, AuditEventType
from selfhealing.context.actor_context import ActorContext

logger = logging.getLogger(__name__)


class AuditMiddleware:
    """
    중앙화된 Audit 미들웨어.
    
    기능:
    1. 요청 시작 시 request_id 생성 및 버퍼 초기화
    2. 응답 반환 전 버퍼의 모든 이벤트 수집
    3. ContinuousAuditRecorder를 통해 일괄 기록 (HashChain 포함)
    
    CRITICAL: 이 미들웨어는 MIDDLEWARE 리스트 끝에 위치해야 함!
    (모든 이벤트가 수집된 후 기록하기 위해)
    
    Usage in settings.py:
        MIDDLEWARE = [
            # ... 다른 미들웨어들 ...
            "selfhealing.api.django.audit_middleware.AuditMiddleware",  # 마지막!
        ]
    """
    
    def __init__(self, get_response: Callable):
        self.get_response = get_response
        self._recorder = None
        self._initialized = False
    
    def _ensure_initialized(self) -> None:
        """Lazy 초기화."""
        if self._initialized:
            return
        
        try:
            from selfhealing.audit.continuous_audit import ContinuousAuditRecorder
            self._recorder = ContinuousAuditRecorder.get_instance()
        except Exception as e:
            logger.warning(f"[AuditMiddleware] Recorder init failed: {e}")
        
        self._initialized = True
    
    def __call__(self, request: HttpRequest) -> HttpResponse:
        """Process request/response."""
        self._ensure_initialized()
        
        # === Phase 1: 버퍼 초기화 ===
        buffer = RequestAuditBuffer.get_or_create(request)
        buffer.request_id = self._generate_request_id(request)
        
        # === Phase 2: 요청 처리 ===
        response = self.get_response(request)
        
        # === Phase 3: 응답 메타 수집 ===
        self._capture_response_meta(request, response, buffer)
        
        # === Phase 4: 이벤트 기록 ===
        if buffer.has_events():
            self._record_events(buffer, request, response)
        
        return response
    
    def _generate_request_id(self, request: HttpRequest) -> str:
        """요청 ID 생성 또는 추출."""
        # X-Request-ID 헤더가 있으면 사용
        request_id = request.META.get("HTTP_X_REQUEST_ID")
        if request_id:
            return request_id
        
        # 없으면 생성
        import uuid
        return str(uuid.uuid4())
    
    def _capture_response_meta(
        self,
        request: HttpRequest,
        response: HttpResponse,
        buffer: RequestAuditBuffer,
    ) -> None:
        """응답 메타데이터 캡처."""
        # 처리 시간
        elapsed = (datetime.now(timezone.utc) - buffer.start_time).total_seconds()
        
        # 에러 응답인 경우 이벤트 추가
        if response.status_code >= 400:
            buffer.add(
                event_type=AuditEventType.ERROR_DETECTED,
                source="AuditMiddleware",
                details={
                    "status_code": response.status_code,
                    "path": request.path,
                    "method": request.method,
                    "elapsed_seconds": elapsed,
                },
                success=False,
            )
    
    def _record_events(
        self,
        buffer: RequestAuditBuffer,
        request: HttpRequest,
        response: HttpResponse,
    ) -> None:
        """이벤트 일괄 기록."""
        if self._recorder is None:
            # Recorder 없으면 로그로 fallback
            for event in buffer.get_events():
                logger.info(f"[AuditMiddleware] {event.to_dict()}")
            return
        
        try:
            # Actor 컨텍스트 가져오기
            actor = ActorContext.get_current()
            actor_id = actor.actor_id if actor else None
            actor_type = actor.actor_type if actor else "system"
            
            # 요청 컨텍스트
            request_context = {
                "request_id": buffer.request_id,
                "path": request.path,
                "method": request.method,
                "status_code": response.status_code,
                "actor_id": actor_id,
                "actor_type": actor_type,
            }
            
            # 각 이벤트 기록
            for event in buffer.get_events():
                self._record_single_event(event, request_context)
                
        except Exception as e:
            # Audit 실패가 메인 흐름을 막지 않음
            logger.warning(f"[AuditMiddleware] Recording failed: {e}")
    
    def _record_single_event(
        self,
        event: AuditEvent,
        request_context: dict,
    ) -> None:
        """단일 이벤트 기록."""
        try:
            from selfhealing.interfaces.audit_adapter import AuditEntry, AuditAction
            
            # 이벤트 타입 → AuditAction 매핑
            action_map = {
                AuditEventType.DLQ_STORE: AuditAction.DLQ_STORE,
                AuditEventType.DLQ_REPLAY: AuditAction.DLQ_REPLAY_SUCCESS,
                AuditEventType.CB_STATE_CHANGE: AuditAction.CB_STATE_CHANGE,
                AuditEventType.CB_REJECTION: AuditAction.CB_FORCE_OPEN,
                AuditEventType.GOVERNANCE_BLOCKED: AuditAction.GOVERNANCE_BLOCKED,
                AuditEventType.RATE_LIMITED: AuditAction.RATE_LIMITED,
                AuditEventType.ERROR_DETECTED: AuditAction.ERROR_DETECTED,
                AuditEventType.CONFIG_CHANGE: AuditAction.CONFIG_CHANGE,
                AuditEventType.MANUAL_OVERRIDE: AuditAction.MANUAL_OVERRIDE,
            }
            
            action = action_map.get(event.event_type, AuditAction.GENERIC)
            
            entry = AuditEntry(
                action=action,
                actor_id=event.actor_id or request_context.get("actor_id"),
                actor_type=event.actor_type,
                target_type=event.source,
                target_id=request_context.get("request_id", ""),
                details={
                    **event.details,
                    "request_context": request_context,
                },
                success=event.success,
                error_message=event.error_message,
            )
            
            self._recorder.audit_adapter.log(entry)
            
        except Exception as e:
            logger.debug(f"[AuditMiddleware] Event record failed: {e}")
```

### 3. 기존 코드 리팩토링

**audit_helpers.py 수정**:
```python
# 기존: 직접 adapter 호출
def log_dlq_store_audit(...):
    adapter = _get_audit_adapter()
    adapter.log_dlq_store(...)

# 수정: 버퍼에 이벤트 적재
def log_dlq_store_audit(
    dlq_id: int,
    domain: str,
    failure_type: str,
    error_message: Optional[str] = None,
    request: Optional[Any] = None,  # 신규
) -> None:
    # 버퍼 사용 가능하면 버퍼에 적재
    if request is not None:
        buffer = RequestAuditBuffer.get_or_create(request)
        buffer.add(
            event_type=AuditEventType.DLQ_STORE,
            source="DLQService",
            details={
                "dlq_id": dlq_id,
                "domain": domain,
                "failure_type": failure_type,
                "error_message": error_message,
            },
        )
        return
    
    # 버퍼 없으면 직접 호출 (Celery 등)
    adapter = _get_audit_adapter()
    if adapter:
        adapter.log_dlq_store(...)
```

---

## 🔧 마이그레이션 가이드

### Phase 1: 버퍼 시스템 도입

1. `event_buffer.py` 생성
2. `AuditMiddleware` 생성
3. `settings.py`에 미들웨어 추가 (마지막 위치)

### Phase 2: 점진적 전환

1. `audit_helpers.py`에 request 파라미터 추가 (선택적)
2. 기존 직접 호출 유지 (하위 호환)
3. 새 코드에서 버퍼 패턴 사용

### Phase 3: 전면 전환

1. 모든 Audit 호출을 버퍼 패턴으로 전환
2. 직접 호출 코드 제거
3. HashChain + WAL 통합

---

## 📊 예상 효과

| 항목 | Before | After |
|------|--------|-------|
| Audit 진입점 | 5+ 개 | 1개 (AuditMiddleware) |
| HashChain 적용률 | 부분적 | 100% |
| WAL 보호 | 없음 | 선택적 활성화 |
| 코드 중복 | 높음 | 낮음 |
| 테스트 용이성 | 분산 | 집중 |

---

## ⚙️ Django settings.py 가이드

### 미들웨어 설정 (CRITICAL)

```python
# myproject/settings.py 또는 myproject/local.py

MIDDLEWARE = [
    # ═══════════════════════════════════════════════════════════════
    # [1] ENTRANCE (진입) - 최상단에 위치
    # ═══════════════════════════════════════════════════════════════
    "selfhealing.api.django.middleware.HealthBridgeMiddleware",  # DB-independent health
    
    # ═══════════════════════════════════════════════════════════════
    # [2] Django Core Middlewares
    # ═══════════════════════════════════════════════════════════════
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    
    # ═══════════════════════════════════════════════════════════════
    # [3] Self-Healing Middlewares (선택적 활성화)
    # ═══════════════════════════════════════════════════════════════
    # "selfhealing.api.django.rate_limit.HybridRateLimitMiddleware",
    # "selfhealing.api.django.pool_circuit_breaker.PoolCircuitBreakerMiddleware",
    # "selfhealing.api.django.middleware.SelfHealingMiddleware",
    
    # ═══════════════════════════════════════════════════════════════
    # [4] CAPTURE (캡처) - 맨 마지막에 위치!
    # ═══════════════════════════════════════════════════════════════
    "selfhealing.api.django.audit_middleware.AuditMiddleware",  # 맨 마지막!
]
```

### 왜 맨 마지막이어야 하는가?

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Django 미들웨어 실행 순서                              │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  Request →  [1] HealthBridge  → [2] SecurityMiddleware → ...            │
│             │                  │                                         │
│             │ 이벤트 적재      │ 이벤트 적재                              │
│             ▼                  ▼                                         │
│         request.META["X-AUDIT-EVENTS"]                                   │
│                                                                          │
│                          ... View 처리 ...                               │
│                                                                          │
│  Response ← [n] AuditMiddleware (맨 마지막 = 가장 먼저 응답 처리)        │
│             │                                                            │
│             │ 모든 이벤트 '낚아채서' 기록!                               │
│             ▼                                                            │
│    ContinuousAuditRecorder (단일 해시 체인)                              │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**핵심**: Django 미들웨어는 요청 시 순서대로, 응답 시 역순으로 처리됩니다.
따라서 AuditMiddleware가 `MIDDLEWARE` 리스트의 **맨 마지막**에 있으면,
**응답 시 가장 먼저** 처리되어 앞선 모든 미들웨어의 이벤트를 수집할 수 있습니다.

### 환경 변수 설정

```python
# .env 또는 환경 변수
AUDIT_MIDDLEWARE_ENABLED=TRUE    # 미들웨어 활성화 (기본: TRUE)
AUDIT_LOG_PATH=logs/audit.jsonl  # Audit 로그 경로
AUDIT_WAL_ENABLED=FALSE          # WAL 활성화 (기본: FALSE)
AUDIT_FAIL_OPEN=TRUE             # Fail-Open 정책 (기본: TRUE)
```

---

## 🔗 관련 문서

- [55_AUDIT_SYSTEM_FIXES.md](55_AUDIT_SYSTEM_FIXES.md) - 전제조건 수정 가이드
- [37_CONTINUOUS_AUDIT_FINAL_IMPL.md](37_CONTINUOUS_AUDIT_FINAL_IMPL.md) - Audit 시스템 구현

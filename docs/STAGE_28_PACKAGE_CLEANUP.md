# Stage 28: selfhealing 패키지 정리 및 확장

## 🎯 목표

selfhealing 패키지를 프레임워크 독립적인 완전한 제품으로 완성

---

## 📋 Phase 28-1: services/ Django fallback 정리

### 현재 문제점

`services/` 폴더에 Django import가 조건부로 남아있음:
```python
# 예: circuit_breaker_service.py
from .adapters.django_repositories import DjangoFailedOperationRepository
```

### 해결 방법: 명시적 의존성 주입

**수정 전:**
```python
class CircuitBreakerService:
    def __init__(self, repository=None):
        if repository is None:
            from .adapters.django_repositories import DjangoCircuitBreakerStateRepository
            repository = DjangoCircuitBreakerStateRepository()
        self._repo = repository
```

**수정 후:**
```python
class CircuitBreakerService:
    def __init__(self, repository: CircuitBreakerStateRepository):
        if repository is None:
            raise ValueError("repository is required. Use ServiceFactory for auto-configuration.")
        self._repo = repository
```

### 수정 대상 파일

| 파일 | 현재 상태 | 수정 내용 |
|------|-----------|-----------|
| `services/circuit_breaker_service.py` | Django fallback | 필수 DI |
| `services/dlq_service.py` | Django fallback | 필수 DI |
| `services/replay_service.py` | Django fallback | 필수 DI |
| `services/security_violation_service.py` | Django fallback | 필수 DI |
| `services/rate_limit_coordinator.py` | Django settings | Config 객체 주입 |

### ServiceFactory 패턴

**파일**: `packages/selfhealing-python/src/selfhealing/factory.py` (수정)

```python
"""
Service Factory for Self-Healing System

Provides framework-specific service construction.
Users should use the factory instead of direct instantiation.
"""

from typing import Optional, Type
from enum import Enum

from selfhealing.interfaces import (
    FailedOperationRepository,
    CircuitBreakerStateRepository,
    SecurityIncidentRepository,
)


class FrameworkType(str, Enum):
    """Supported frameworks"""
    DJANGO = "django"
    FASTAPI = "fastapi"
    FLASK = "flask"
    STANDALONE = "standalone"  # In-memory, no framework


class ServiceFactory:
    """
    Factory for creating self-healing services with proper dependencies.

    Usage:
        # Django project
        factory = ServiceFactory(framework=FrameworkType.DJANGO)
        cb_service = factory.create_circuit_breaker_service()

        # FastAPI project
        factory = ServiceFactory(framework=FrameworkType.FASTAPI)
        dlq_service = factory.create_dlq_service()

        # Standalone (testing)
        factory = ServiceFactory(framework=FrameworkType.STANDALONE)
    """

    def __init__(
        self,
        framework: FrameworkType = FrameworkType.STANDALONE,
        custom_repositories: Optional[dict] = None,
    ):
        self._framework = framework
        self._custom_repos = custom_repositories or {}
        self._repo_cache = {}

    def get_failed_operation_repository(self) -> FailedOperationRepository:
        """Get FailedOperation repository for current framework"""
        if "failed_operation" in self._custom_repos:
            return self._custom_repos["failed_operation"]

        if "failed_operation" in self._repo_cache:
            return self._repo_cache["failed_operation"]

        repo = self._create_repository("failed_operation")
        self._repo_cache["failed_operation"] = repo
        return repo

    def get_circuit_breaker_repository(self) -> CircuitBreakerStateRepository:
        """Get CircuitBreakerState repository for current framework"""
        if "circuit_breaker" in self._custom_repos:
            return self._custom_repos["circuit_breaker"]

        if "circuit_breaker" in self._repo_cache:
            return self._repo_cache["circuit_breaker"]

        repo = self._create_repository("circuit_breaker")
        self._repo_cache["circuit_breaker"] = repo
        return repo

    def _create_repository(self, repo_type: str):
        """Create repository based on framework"""
        if self._framework == FrameworkType.DJANGO:
            return self._create_django_repository(repo_type)
        elif self._framework == FrameworkType.FASTAPI:
            return self._create_fastapi_repository(repo_type)
        elif self._framework == FrameworkType.FLASK:
            return self._create_flask_repository(repo_type)
        else:
            return self._create_inmemory_repository(repo_type)

    def _create_django_repository(self, repo_type: str):
        """Create Django ORM based repository"""
        from selfhealing.adapters.django.repositories import (
            DjangoFailedOperationRepository,
            DjangoCircuitBreakerStateRepository,
            DjangoSecurityIncidentRepository,
        )

        mapping = {
            "failed_operation": DjangoFailedOperationRepository,
            "circuit_breaker": DjangoCircuitBreakerStateRepository,
            "security_incident": DjangoSecurityIncidentRepository,
        }
        return mapping[repo_type]()

    def _create_fastapi_repository(self, repo_type: str):
        """Create SQLAlchemy based repository for FastAPI"""
        from selfhealing.adapters.sqlalchemy.repositories import (
            SQLAlchemyFailedOperationRepository,
            SQLAlchemyCircuitBreakerStateRepository,
            SQLAlchemySecurityIncidentRepository,
        )

        mapping = {
            "failed_operation": SQLAlchemyFailedOperationRepository,
            "circuit_breaker": SQLAlchemyCircuitBreakerStateRepository,
            "security_incident": SQLAlchemySecurityIncidentRepository,
        }
        return mapping[repo_type]()

    def _create_flask_repository(self, repo_type: str):
        """Create Flask-SQLAlchemy based repository"""
        # Flask도 SQLAlchemy 사용
        return self._create_fastapi_repository(repo_type)

    def _create_inmemory_repository(self, repo_type: str):
        """Create in-memory repository for testing"""
        from selfhealing.adapters.memory.repositories import (
            InMemoryFailedOperationRepository,
            InMemoryCircuitBreakerStateRepository,
            InMemorySecurityIncidentRepository,
        )

        mapping = {
            "failed_operation": InMemoryFailedOperationRepository,
            "circuit_breaker": InMemoryCircuitBreakerStateRepository,
            "security_incident": InMemorySecurityIncidentRepository,
        }
        return mapping[repo_type]()

    # Service creation methods
    def create_circuit_breaker_service(self):
        from selfhealing.services.circuit_breaker_service import CircuitBreakerService
        return CircuitBreakerService(
            repository=self.get_circuit_breaker_repository()
        )

    def create_dlq_service(self):
        from selfhealing.services.dlq_service import DLQService
        return DLQService(
            repository=self.get_failed_operation_repository()
        )

    def create_replay_service(self):
        from selfhealing.services.replay_service import ReplayService
        return ReplayService(
            repository=self.get_failed_operation_repository()
        )
```

---

## 📋 Phase 28-2: FastAPI 어댑터 추가

### 디렉토리 구조

```
packages/selfhealing-python/src/selfhealing/adapters/
├── django/           # 기존
├── fastapi/          # 신규
│   ├── __init__.py
│   ├── middleware.py
│   ├── dependencies.py
│   └── routes.py
└── sqlalchemy/       # 신규 (FastAPI/Flask 공용)
    ├── __init__.py
    ├── models.py
    └── repositories.py
```

### FastAPI Middleware

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/fastapi/middleware.py`

```python
"""
FastAPI Middleware for Self-Healing

Provides:
- Request tracking for graceful shutdown
- Circuit breaker integration
- Error handling with DLQ
"""

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Callable
import uuid

from selfhealing.core.shutdown_coordinator import RequestTracker
from selfhealing.core.request_context import track_request


class SelfHealingMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for self-healing features.

    Usage:
        from selfhealing.adapters.fastapi import SelfHealingMiddleware

        app = FastAPI()
        app.add_middleware(SelfHealingMiddleware, request_tracker=tracker)
    """

    def __init__(
        self,
        app,
        request_tracker: RequestTracker,
        shutdown_coordinator=None,
    ):
        super().__init__(app)
        self._tracker = request_tracker
        self._shutdown = shutdown_coordinator

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        # Check if accepting requests
        if self._shutdown and not self._shutdown.is_accepting_requests():
            return Response(
                content='{"error": "Server is shutting down"}',
                status_code=503,
                media_type="application/json",
            )

        # Track request
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

        with track_request(
            self._tracker,
            request_id=request_id,
            endpoint=str(request.url.path),
            method=request.method,
        ):
            response = await call_next(request)

        return response
```

### FastAPI Dependencies

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/fastapi/dependencies.py`

```python
"""
FastAPI Dependencies for Self-Healing

Provides dependency injection for FastAPI routes.
"""

from fastapi import Depends
from typing import Generator

from selfhealing.factory import ServiceFactory, FrameworkType


# Global factory instance
_factory: ServiceFactory = None


def get_factory() -> ServiceFactory:
    """Get or create service factory"""
    global _factory
    if _factory is None:
        _factory = ServiceFactory(framework=FrameworkType.FASTAPI)
    return _factory


def get_circuit_breaker_service():
    """Dependency for circuit breaker service"""
    return get_factory().create_circuit_breaker_service()


def get_dlq_service():
    """Dependency for DLQ service"""
    return get_factory().create_dlq_service()


def get_replay_service():
    """Dependency for replay service"""
    return get_factory().create_replay_service()
```

---

## 📋 Phase 28-3: SQLAlchemy 어댑터 (Django 없이 DB 사용)

### SQLAlchemy Models

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/sqlalchemy/models.py`

```python
"""
SQLAlchemy Models for Self-Healing

Framework-agnostic database models using SQLAlchemy.
Compatible with FastAPI, Flask, or standalone Python.
"""

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, Boolean,
    JSON, Enum as SQLEnum, ForeignKey
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime

from selfhealing.interfaces import (
    FailedOperationStatus,
    FailedOperationDomain,
    CircuitBreakerStateEnum,
    SecurityIncidentType,
    SecuritySeverity,
    SecurityIncidentStatus,
)


Base = declarative_base()


class FailedOperationModel(Base):
    """SQLAlchemy model for FailedOperation"""

    __tablename__ = "selfhealing_failed_operations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    domain = Column(String(50), nullable=False, index=True)
    failure_type = Column(String(100), nullable=False)
    status = Column(String(50), default=FailedOperationStatus.PENDING.value, index=True)

    # References
    order_id = Column(Integer, nullable=True, index=True)
    payment_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)

    # Data
    snapshot_data = Column(JSON, default=dict)
    error_code = Column(String(50), default="")
    error_message = Column(Text, default="")

    # Retry
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    last_retry_at = Column(DateTime, nullable=True)

    # Forensic
    request_data = Column(JSON, default=dict)
    response_data = Column(JSON, default=dict)
    metadata = Column(JSON, default=dict)

    # Resolution
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_id = Column(Integer, nullable=True)
    resolution_type = Column(String(50), default="")
    resolution_note = Column(Text, default="")

    # Hints
    next_action_hint = Column(String(200), default="")
    recommended_action = Column(String(200), default="")

    # Timestamps
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    expires_at = Column(DateTime, nullable=True)


class CircuitBreakerStateModel(Base):
    """SQLAlchemy model for CircuitBreakerState"""

    __tablename__ = "selfhealing_circuit_breaker_states"

    id = Column(Integer, primary_key=True, autoincrement=True)
    service_name = Column(String(100), unique=True, nullable=False, index=True)
    state = Column(String(20), default=CircuitBreakerStateEnum.CLOSED.value)
    failure_count = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    last_failure_at = Column(DateTime, nullable=True)
    last_success_at = Column(DateTime, nullable=True)
    opened_at = Column(DateTime, nullable=True)
    half_opened_at = Column(DateTime, nullable=True)
    metadata = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class SecurityIncidentModel(Base):
    """SQLAlchemy model for SecurityIncident"""

    __tablename__ = "selfhealing_security_incidents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    incident_type = Column(String(50), nullable=False, index=True)
    severity = Column(String(20), nullable=False, index=True)
    status = Column(String(20), default=SecurityIncidentStatus.OPEN.value, index=True)
    source_ip = Column(String(45), nullable=True)
    user_id = Column(Integer, nullable=True, index=True)
    description = Column(Text, default="")
    evidence = Column(JSON, default=dict)
    resolution_note = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    resolved_at = Column(DateTime, nullable=True)
```

### SQLAlchemy Repository

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/sqlalchemy/repositories.py`

```python
"""
SQLAlchemy Repository Implementations

Implements repository interfaces using SQLAlchemy.
"""

from typing import Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from selfhealing.interfaces import (
    FailedOperationRepository,
    FailedOperationData,
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
)
from .models import FailedOperationModel, CircuitBreakerStateModel


class SQLAlchemyFailedOperationRepository(FailedOperationRepository):
    """SQLAlchemy implementation of FailedOperationRepository"""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def _get_session(self) -> Session:
        return self._session_factory()

    def create(self, data: FailedOperationData) -> FailedOperationData:
        session = self._get_session()
        try:
            model = FailedOperationModel(
                domain=data.domain,
                failure_type=data.failure_type,
                status=data.status,
                order_id=data.order_id,
                payment_id=data.payment_id,
                user_id=data.user_id,
                snapshot_data=data.snapshot_data,
                error_code=data.error_code,
                error_message=data.error_message,
            )
            session.add(model)
            session.commit()
            session.refresh(model)

            data.id = model.id
            data.created_at = model.created_at
            return data
        finally:
            session.close()

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        session = self._get_session()
        try:
            model = session.query(FailedOperationModel).filter_by(id=id).first()
            if model is None:
                return None
            return self._model_to_data(model)
        finally:
            session.close()

    def get_pending(self, limit: int = 100) -> List[FailedOperationData]:
        session = self._get_session()
        try:
            models = session.query(FailedOperationModel)\
                .filter_by(status="pending")\
                .limit(limit)\
                .all()
            return [self._model_to_data(m) for m in models]
        finally:
            session.close()

    def update_status(self, id: int, status: str) -> bool:
        session = self._get_session()
        try:
            result = session.query(FailedOperationModel)\
                .filter_by(id=id)\
                .update({"status": status, "updated_at": datetime.utcnow()})
            session.commit()
            return result > 0
        finally:
            session.close()

    def _model_to_data(self, model: FailedOperationModel) -> FailedOperationData:
        return FailedOperationData(
            id=model.id,
            domain=model.domain,
            failure_type=model.failure_type,
            status=model.status,
            order_id=model.order_id,
            payment_id=model.payment_id,
            user_id=model.user_id,
            snapshot_data=model.snapshot_data or {},
            error_code=model.error_code,
            error_message=model.error_message,
            retry_count=model.retry_count,
            max_retries=model.max_retries,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
```

---

## 📋 Phase 28-4: In-Memory Repository (테스트용)

**파일**: `packages/selfhealing-python/src/selfhealing/adapters/memory/repositories.py`

```python
"""
In-Memory Repository Implementations

For testing and standalone usage without database.
"""

from typing import Optional, List, Dict
from datetime import datetime, timezone
from threading import Lock

from selfhealing.interfaces import (
    FailedOperationRepository,
    FailedOperationData,
    CircuitBreakerStateRepository,
    CircuitBreakerStateData,
    SecurityIncidentRepository,
    SecurityIncidentData,
)


class InMemoryFailedOperationRepository(FailedOperationRepository):
    """In-memory implementation for testing"""

    def __init__(self):
        self._data: Dict[int, FailedOperationData] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, data: FailedOperationData) -> FailedOperationData:
        with self._lock:
            data.id = self._next_id
            self._next_id += 1
            data.created_at = datetime.now(timezone.utc)
            data.updated_at = data.created_at
            self._data[data.id] = data
            return data

    def get_by_id(self, id: int) -> Optional[FailedOperationData]:
        return self._data.get(id)

    def get_pending(self, limit: int = 100) -> List[FailedOperationData]:
        pending = [d for d in self._data.values() if d.status == "pending"]
        return pending[:limit]

    def update_status(self, id: int, status: str) -> bool:
        if id in self._data:
            self._data[id].status = status
            self._data[id].updated_at = datetime.now(timezone.utc)
            return True
        return False

    def delete(self, id: int) -> bool:
        if id in self._data:
            del self._data[id]
            return True
        return False


class InMemoryCircuitBreakerStateRepository(CircuitBreakerStateRepository):
    """In-memory circuit breaker state repository"""

    def __init__(self):
        self._data: Dict[str, CircuitBreakerStateData] = {}
        self._lock = Lock()

    def get_by_service(self, service_name: str) -> Optional[CircuitBreakerStateData]:
        return self._data.get(service_name)

    def save(self, data: CircuitBreakerStateData) -> CircuitBreakerStateData:
        with self._lock:
            data.updated_at = datetime.now(timezone.utc)
            self._data[data.service_name] = data
            return data

    def update_state(self, service_name: str, state: str) -> bool:
        if service_name in self._data:
            self._data[service_name].state = state
            self._data[service_name].updated_at = datetime.now(timezone.utc)
            return True
        return False


class InMemorySecurityIncidentRepository(SecurityIncidentRepository):
    """In-memory security incident repository"""

    def __init__(self):
        self._data: Dict[int, SecurityIncidentData] = {}
        self._next_id = 1
        self._lock = Lock()

    def create(self, data: SecurityIncidentData) -> SecurityIncidentData:
        with self._lock:
            data.id = self._next_id
            self._next_id += 1
            data.created_at = datetime.now(timezone.utc)
            self._data[data.id] = data
            return data

    def get_by_id(self, id: int) -> Optional[SecurityIncidentData]:
        return self._data.get(id)

    def get_open_incidents(self, limit: int = 100) -> List[SecurityIncidentData]:
        open_incidents = [d for d in self._data.values() if d.status == "open"]
        return open_incidents[:limit]
```

---

## 📁 파일 생성/수정 순서

### Phase 28-1
1. 수정: `services/circuit_breaker_service.py`
2. 수정: `services/dlq_service.py`
3. 수정: `services/replay_service.py`
4. 수정: `services/security_violation_service.py`
5. 수정: `factory.py`

### Phase 28-2
1. 생성: `adapters/fastapi/__init__.py`
2. 생성: `adapters/fastapi/middleware.py`
3. 생성: `adapters/fastapi/dependencies.py`
4. 생성: `adapters/fastapi/routes.py`

### Phase 28-3
1. 생성: `adapters/sqlalchemy/__init__.py`
2. 생성: `adapters/sqlalchemy/models.py`
3. 생성: `adapters/sqlalchemy/repositories.py`

### Phase 28-4
1. 생성: `adapters/memory/__init__.py`
2. 생성: `adapters/memory/repositories.py`

---

## ✅ 완료 기준

### Phase 28-1
- [x] services/ 에서 Django fallback 제거
- [x] ServiceFactory 개선
- [x] 기존 테스트 통과

### Phase 28-2
- [x] FastAPI middleware 구현
- [x] FastAPI dependencies 구현
- [x] FastAPI 예제 앱으로 테스트

### Phase 28-3
- [x] SQLAlchemy models 구현
- [x] SQLAlchemy repositories 구현
- [x] Docker Compose PostgreSQL 통합 테스트

### Phase 28-4
- [x] In-memory repositories 구현
- [x] 단위 테스트에서 활용

---

## 📝 새 세션 시작 프롬프트

```
# Phase 28-1
STAGE_28_PACKAGE_CLEANUP.md의 Phase 28-1 문서대로 구현해줘.
services/ 폴더의 Django fallback 정리하고 ServiceFactory 개선.

# Phase 28-2
STAGE_28_PACKAGE_CLEANUP.md의 Phase 28-2 문서대로 구현해줘.
FastAPI 어댑터 생성.

# Phase 28-3
STAGE_28_PACKAGE_CLEANUP.md의 Phase 28-3 문서대로 구현해줘.
SQLAlchemy 모델과 repository 구현.
```

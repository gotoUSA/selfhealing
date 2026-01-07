"""
Base ServiceFactory for Self-Healing Components.

Provides framework-specific service construction without Django fallbacks
in the services layer.

Storage Strategy (핵심 원칙: 호스트 DB에 침투하지 않음):
- 기본값: Memory (설치 즉시 작동, 외부 의존성 없음)
- opt-in: Redis, Django DB (호스트가 명시적으로 설정해야 함)

저장소 모드:
- Memory (Default): 테스트, 단일 서버, 최소 부하 → "Plug-and-Play"
- Layered (L1+L2): Memory + Redis → 분산 환경 고성능, L1으로 장애 내성
- Django DB (Opt-in): 영구 기록 필요 시 → 호스트가 마이그레이션 책임

Usage:
    # 기본 (Memory)
    factory = ServiceFactory()
    
    # 분산 환경 (Layered: Memory + Redis)
    factory = ServiceFactory(storage_mode="layered")
    
    # Django DB 사용 (opt-in, 명시적 설정 필요)
    factory = ServiceFactory(storage_mode="django")
"""

from __future__ import annotations

import logging
import os
from enum import Enum
from typing import TYPE_CHECKING, Optional, Dict, Any

if TYPE_CHECKING:
    from selfhealing.interfaces.repositories import (
        FailedOperationRepository,
        CircuitBreakerStateRepository,
        SecurityIncidentRepository,
    )

logger = logging.getLogger(__name__)


# =============================================================================
# Storage Mode Enum
# =============================================================================


class StorageMode(str, Enum):
    """저장소 모드."""
    
    MEMORY = "memory"      # 기본값: 메모리만 사용
    LAYERED = "layered"    # L1(Memory) + L2(Redis)
    DJANGO = "django"      # Django ORM (opt-in)
    FASTAPI = "fastapi"    # SQLAlchemy (미구현)


# =============================================================================
# Framework Type Enum
# =============================================================================


class FrameworkType(str, Enum):
    """Supported frameworks for adapter selection."""

    DJANGO = "django"
    FASTAPI = "fastapi"
    FLASK = "flask"
    STANDALONE = "standalone"  # In-memory, no framework


# =============================================================================
# Service Factory for Framework-Specific Construction
# =============================================================================


class ServiceFactory:
    """
    Factory for creating self-healing services with proper dependencies.

    핵심 원칙: 호스트 DB에 침투하지 않음
    - 기본값은 항상 Memory (외부 의존성 없음)
    - Django/Redis 사용은 호스트가 명시적으로 opt-in

    Usage:
        # 기본 (Memory, 설치 즉시 작동)
        factory = ServiceFactory()
        cb_service = factory.create_circuit_breaker_service()

        # 분산 환경 (L1 Memory + L2 Redis)
        factory = ServiceFactory(storage_mode=StorageMode.LAYERED)
        
        # Django DB 사용 (opt-in, 마이그레이션 필요)
        factory = ServiceFactory(storage_mode=StorageMode.DJANGO)
    """

    def __init__(
        self,
        framework: FrameworkType = FrameworkType.STANDALONE,
        storage_mode: Optional[StorageMode] = None,
        custom_repositories: Optional[Dict[str, Any]] = None,
    ):
        self._framework = framework
        self._custom_repos = custom_repositories or {}
        self._repo_cache: Dict[str, Any] = {}
        
        # 저장소 모드 결정 (환경변수 또는 파라미터)
        if storage_mode:
            self._storage_mode = storage_mode
        else:
            env_mode = os.environ.get("SELFHEALING_STORAGE", "memory").lower()
            self._storage_mode = StorageMode(env_mode) if env_mode in [m.value for m in StorageMode] else StorageMode.MEMORY

    @property
    def framework(self) -> FrameworkType:
        """Get the current framework type."""
        return self._framework
    
    @property
    def storage_mode(self) -> StorageMode:
        """Get the current storage mode."""
        return self._storage_mode

    def get_failed_operation_repository(self) -> "FailedOperationRepository":
        """Get FailedOperation repository for current framework."""
        if "failed_operation" in self._custom_repos:
            return self._custom_repos["failed_operation"]

        if "failed_operation" in self._repo_cache:
            return self._repo_cache["failed_operation"]

        repo = self._create_repository("failed_operation")
        self._repo_cache["failed_operation"] = repo
        return repo

    def get_circuit_breaker_repository(self) -> "CircuitBreakerStateRepository":
        """Get CircuitBreakerState repository for current framework."""
        if "circuit_breaker" in self._custom_repos:
            return self._custom_repos["circuit_breaker"]

        if "circuit_breaker" in self._repo_cache:
            return self._repo_cache["circuit_breaker"]

        repo = self._create_repository("circuit_breaker")
        self._repo_cache["circuit_breaker"] = repo
        return repo

    def get_security_incident_repository(self) -> "SecurityIncidentRepository":
        """Get SecurityIncident repository for current framework."""
        if "security_incident" in self._custom_repos:
            return self._custom_repos["security_incident"]

        if "security_incident" in self._repo_cache:
            return self._repo_cache["security_incident"]

        repo = self._create_repository("security_incident")
        self._repo_cache["security_incident"] = repo
        return repo

    def _create_repository(self, repo_type: str) -> Any:
        """
        Create repository based on storage_mode (not framework).
        
        핵심 원칙: 기본값은 항상 Memory
        """
        if self._storage_mode == StorageMode.DJANGO:
            return self._create_django_repository(repo_type)
        elif self._storage_mode == StorageMode.LAYERED:
            return self._create_layered_repository(repo_type)
        elif self._storage_mode == StorageMode.FASTAPI:
            return self._create_fastapi_repository(repo_type)
        else:
            # 기본값: Memory (외부 의존성 없음)
            return self._create_inmemory_repository(repo_type)

    def _create_django_repository(self, repo_type: str) -> Any:
        """
        Create Django ORM based repository.
        
        ⚠️ opt-in: 호스트가 명시적으로 SELFHEALING_STORAGE=django 설정 필요
        ⚠️ 마이그레이션: selfhealing.adapters.django를 INSTALLED_APPS에 추가 필요
        
        NOTE: Django repository 모듈이 현재 구현되지 않아 InMemory로 fallback합니다.
        """
        logger.warning(
            f"[ServiceFactory] Django repository not implemented. "
            f"Falling back to in-memory for: {repo_type}"
        )
        return self._create_inmemory_repository(repo_type)
    
    def _create_layered_repository(self, repo_type: str) -> Any:
        """
        Create Layered repository (L1 Memory + L2 Redis).
        
        분산 환경용: L1에서 즉시 판정, L2는 비동기 동기화
        """
        if repo_type == "circuit_breaker":
            from selfhealing.adapters.memory import LayeredCircuitBreakerStateRepository
            
            # L2 Redis 연결 시도
            l2_repo = None
            try:
                # Redis 저장소가 있으면 사용
                from selfhealing.adapters.redis import RedisCircuitBreakerStateRepository
                l2_repo = RedisCircuitBreakerStateRepository()
                logger.info("[ServiceFactory] Using Layered storage: L1=Memory + L2=Redis")
            except ImportError:
                logger.info("[ServiceFactory] Redis adapter not available. Using L1=Memory only")
            except Exception as e:
                logger.warning(f"[ServiceFactory] Redis connection failed: {e}. Using L1=Memory only")
            
            return LayeredCircuitBreakerStateRepository(l2_repo=l2_repo)
        else:
            # 다른 타입은 일단 Memory
            return self._create_inmemory_repository(repo_type)

    def _create_fastapi_repository(self, repo_type: str) -> Any:
        """
        Create SQLAlchemy based repository for FastAPI.
        
        Requires SQLALCHEMY_DATABASE_URL environment variable.
        If not set, falls back to in-memory storage.
        
        For production:
        - Set SQLALCHEMY_DATABASE_URL to PostgreSQL/MySQL connection string
        - Or use SELFHEALING_STORAGE=layered with Redis for distributed setup
        """
        import os
        database_url = os.environ.get("SQLALCHEMY_DATABASE_URL")
        
        if not database_url:
            logger.info(
                "[ServiceFactory] SQLALCHEMY_DATABASE_URL not set, "
                "falling back to in-memory storage."
            )
            return self._create_inmemory_repository(repo_type)
        
        try:
            from sqlalchemy import create_engine
            from selfhealing.adapters.sqlalchemy import (
                SQLAlchemyFailedOperationRepository,
                SQLAlchemyCircuitBreakerStateRepository,
                SQLAlchemySecurityIncidentRepository,
                create_session_factory,
                Base,
            )
            
            # Create engine and session factory (cached per database URL)
            if not hasattr(self, "_sqlalchemy_session_factory"):
                engine = create_engine(database_url)
                Base.metadata.create_all(engine)
                self._sqlalchemy_session_factory = create_session_factory(engine)
            
            mapping = {
                "failed_operation": SQLAlchemyFailedOperationRepository,
                "circuit_breaker": SQLAlchemyCircuitBreakerStateRepository,
                "security_incident": SQLAlchemySecurityIncidentRepository,
            }
            return mapping[repo_type](self._sqlalchemy_session_factory)
        except ImportError as e:
            logger.warning(
                f"[ServiceFactory] SQLAlchemy not available: {e}. "
                "Install with: pip install sqlalchemy"
            )
            return self._create_inmemory_repository(repo_type)

    def _create_flask_repository(self, repo_type: str) -> Any:
        """Create Flask-SQLAlchemy based repository."""
        # Flask also uses SQLAlchemy
        return self._create_fastapi_repository(repo_type)

    def _create_inmemory_repository(self, repo_type: str) -> Any:
        """Create in-memory repository for testing/standalone."""
        from selfhealing.adapters.memory import (
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
        """Create CircuitBreakerService with proper repository."""
        from selfhealing.services.circuit_breaker_service import CircuitBreakerService

        return CircuitBreakerService(repository=self.get_circuit_breaker_repository())

    def create_dlq_service(self):
        """Create DLQService with proper repository."""
        from selfhealing.services.dlq_service import DLQService

        return DLQService(repository=self.get_failed_operation_repository())

    def create_replay_service(self):
        """Create ReplayService with proper repository."""
        from selfhealing.services.replay_service import ReplayService

        return ReplayService(repository=self.get_failed_operation_repository())

    def create_security_violation_service(self):
        """Create SecurityViolationService with proper repository."""
        from selfhealing.services.security_violation_service import SecurityViolationService

        return SecurityViolationService(repository=self.get_security_incident_repository())

    def reset_cache(self) -> None:
        """Reset repository cache (for testing)."""
        self._repo_cache.clear()


# =============================================================================
# Global Factory Instance Management
# =============================================================================

# Global factory instance
_service_factory: Optional[ServiceFactory] = None


def get_service_factory() -> ServiceFactory:
    """
    Get the global ServiceFactory instance.

    Creates a STANDALONE factory by default.
    Call configure_service_factory() first for framework-specific setup.
    """
    global _service_factory
    if _service_factory is None:
        _service_factory = ServiceFactory(framework=FrameworkType.STANDALONE)
    return _service_factory


def configure_service_factory(
    framework: FrameworkType,
    custom_repositories: Optional[Dict[str, Any]] = None,
) -> ServiceFactory:
    """
    Configure the global ServiceFactory with framework type.

    Call this during app initialization (e.g., Django AppConfig.ready()).

    Args:
        framework: The framework type to use
        custom_repositories: Optional custom repository implementations

    Returns:
        The configured ServiceFactory instance
    """
    global _service_factory
    _service_factory = ServiceFactory(
        framework=framework,
        custom_repositories=custom_repositories,
    )
    logger.info(f"[ServiceFactory] Configured for framework: {framework.value}")
    return _service_factory


def reset_service_factory() -> None:
    """Reset the global ServiceFactory (for testing)."""
    global _service_factory
    _service_factory = None

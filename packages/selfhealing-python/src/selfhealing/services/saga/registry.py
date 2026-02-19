"""
Saga Registry.

Saga 정의 등록/조회 레지스트리.
ReplayHandler 레지스트리와 동일 패턴:
- register_replay_handler(handler) → register_saga(definition)
- get_replay_handler(domain) → get_saga_definition(name)
"""

from __future__ import annotations

from selfhealing.services.saga.models import SagaDefinition

# =============================================================================
# Saga Definition Registry
# =============================================================================

_saga_definitions: dict[str, SagaDefinition] = {}


def register_saga(definition: SagaDefinition) -> None:
    """Saga 정의 등록.

    동일 이름으로 재등록 시 기존 정의를 덮어쓴다 (버전 업그레이드 지원).
    단, 진행 중인 인스턴스는 구 버전의 definition_version을 보유하므로
    Orchestrator가 버전 불일치를 감지하여 SUSPENDED 처리한다.

    Raises:
        ValueError: 정의 유효성 검증 실패 시
    """
    valid, error = definition.validate()
    if not valid:
        raise ValueError(f"Invalid saga definition '{definition.name}': {error}")
    _saga_definitions[definition.name] = definition


def get_saga_definition(name: str) -> SagaDefinition | None:
    """등록된 Saga 정의 조회."""
    return _saga_definitions.get(name)


def list_saga_definitions() -> list[str]:
    """등록된 모든 Saga 이름 목록."""
    return list(_saga_definitions.keys())

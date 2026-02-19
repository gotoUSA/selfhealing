"""
Saga Core Models Package.

Saga Orchestrator의 코어 자료구조를 제공합니다.

Modules:
    - models: SagaStatus, StepResult, SagaContext, SagaStepStatus,
              SagaStepInstance, SagaDefinition, SagaInstance
    - step: SagaStep(ABC) — Forward + Compensate 인터페이스
    - registry: register_saga(), get_saga_definition(), list_saga_definitions()

Usage:
    from selfhealing.services.saga import (
        SagaStatus,
        SagaStepStatus,
        StepResult,
        SagaContext,
        SagaStepInstance,
        SagaDefinition,
        SagaInstance,
        SagaStep,
        ALLOWED_TRANSITIONS,
        register_saga,
        get_saga_definition,
        list_saga_definitions,
    )
"""

from selfhealing.services.saga.models import (
    ALLOWED_TRANSITIONS,
    SagaContext,
    SagaDefinition,
    SagaInstance,
    SagaStatus,
    SagaStepInstance,
    SagaStepStatus,
    StepResult,
)
from selfhealing.services.saga.registry import (
    get_saga_definition,
    list_saga_definitions,
    register_saga,
)
from selfhealing.services.saga.step import SagaStep

__all__ = [
    "ALLOWED_TRANSITIONS",
    "SagaContext",
    "SagaDefinition",
    "SagaInstance",
    "SagaStatus",
    "SagaStep",
    "SagaStepInstance",
    "SagaStepStatus",
    "StepResult",
    "get_saga_definition",
    "list_saga_definitions",
    "register_saga",
]

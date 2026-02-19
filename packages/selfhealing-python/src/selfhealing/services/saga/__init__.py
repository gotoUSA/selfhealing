"""
Saga Orchestrator Package.

Saga Orchestrator의 코어 자료구조 및 오케스트레이터를 제공합니다.

Modules:
    - models: SagaStatus, StepResult, SagaContext, SagaStepStatus,
              SagaStepInstance, SagaDefinition, SagaInstance
    - step: SagaStep(ABC) — Forward + Compensate 인터페이스
    - registry: register_saga(), get_saga_definition(), list_saga_definitions()
    - orchestrator: SagaOrchestrator — 분산 Saga 오케스트레이터
    - events: SagaEventType — Saga 이벤트 타입 상수
    - lua_scripts: SAGA_TRANSITION_SCRIPT, SAGA_INSTANCE_CAS_SCRIPT
    - tasks: resume_saga_instance_task, scan_orphan_sagas (Celery)

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
        SagaOrchestrator,
        SagaEventType,
        ALLOWED_TRANSITIONS,
        register_saga,
        get_saga_definition,
        list_saga_definitions,
    )
"""

from selfhealing.services.saga.events import SagaEventType
from selfhealing.services.saga.lua_scripts import (
    SAGA_INSTANCE_CAS_SCRIPT,
    SAGA_TRANSITION_SCRIPT,
)
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
from selfhealing.services.saga.orchestrator import SagaOrchestrator
from selfhealing.services.saga.registry import (
    get_saga_definition,
    list_saga_definitions,
    register_saga,
)
from selfhealing.services.saga.step import SagaStep

__all__ = [
    "ALLOWED_TRANSITIONS",
    "SAGA_INSTANCE_CAS_SCRIPT",
    "SAGA_TRANSITION_SCRIPT",
    "SagaContext",
    "SagaDefinition",
    "SagaEventType",
    "SagaInstance",
    "SagaOrchestrator",
    "SagaStatus",
    "SagaStep",
    "SagaStepInstance",
    "SagaStepStatus",
    "StepResult",
    "get_saga_definition",
    "list_saga_definitions",
    "register_saga",
]

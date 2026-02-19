"""
Saga EventType 확장.

Saga Orchestrator에서 사용하는 이벤트 타입을 정의한다.
기존 EventType enum에 추가되는 Saga 전용 이벤트 값을 문자열 상수로 관리한다.
"""


class SagaEventType:
    """Saga Orchestrator 이벤트 타입 상수.

    EventType enum에 추가할 Saga 이벤트 메시지 식별자.
    """

    SAGA_STARTED = "saga_started"
    """Saga 실행 시작."""

    SAGA_STEP_COMPLETED = "saga_step_completed"
    """Forward Step 실행 성공."""

    SAGA_STEP_FAILED = "saga_step_failed"
    """Forward Step 실행 실패."""

    SAGA_COMPLETED = "saga_completed"
    """모든 Forward Step 성공, Saga 완료."""

    SAGA_COMPENSATING = "saga_compensating"
    """역순 보상 Step 완료 (개별 Step 보상 성공마다 발행)."""

    SAGA_COMPENSATED = "saga_compensated"
    """모든 보상 완료."""

    SAGA_COMPENSATION_FAILED = "saga_compensation_failed"
    """보상 실패. DLQ에 저장됨."""

    SAGA_SUSPENDED = "saga_suspended"
    """서킷브레이커 OPEN으로 일시 중지."""

    SAGA_RESUMED = "saga_resumed"
    """중단된 Saga 재개."""

    SAGA_TIMED_OUT = "saga_timed_out"
    """전체 Saga 타임아웃 초과."""

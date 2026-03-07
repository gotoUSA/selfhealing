"""
Idempotency Models

Domain enums, key generation, and result dataclasses for idempotency operations.

Canonical location: ``selfhealing.services.idempotency.models``
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

T = TypeVar("T")


class IdempotencyDomain(str, Enum):
    """Domains that support idempotency checking (domain-neutral)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # 기존 도메인
    # ═══════════════════════════════════════════════════════════════════════════
    EXTERNAL_SERVICE = "external_service"
    """외부 서비스 호출 (결제, 알림 등)."""

    INTERNAL_PROCESS = "internal_process"
    """내부 프로세스 (재고 차감, 포인트 적립 등)."""

    ASYNC_TASK = "async_task"
    """비동기 작업 (Celery Task 등)."""

    EVENT = "event"
    """이벤트 처리 (Webhook, 메시지 등)."""

    CUSTOM = "custom"
    """커스텀 도메인."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Chaos Engineering 관련 도메인
    # Chaos 실험 실행 및 좀비 헌터 분산 락 관리
    # ═══════════════════════════════════════════════════════════════════════════
    CHAOS_EXPERIMENT = "chaos_experiment"
    """Chaos 실험 실행 (동일 실험 중복 실행 방지)."""

    CHAOS_ZOMBIE_HUNTER = "chaos_zombie_hunter"
    """Zombie Hunter 분산 락 (고아 실험 중복 rollback 방지).

    고아 상태의 실험을 감지하고 안전하게 정리합니다.
    """

    # ═══════════════════════════════════════════════════════════════════════════
    # 설정 관리 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    CONFIG_CHANGE = "config_change"
    """설정 변경 (동일 변경 중복 적용 방지)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # 저장소 동기화 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    L2_SYNC = "l2_sync"
    """L2 저장소 동기화 (복구 후 재동기화 중복 방지)."""

    WAL_RECOVERY = "wal_recovery"
    """WAL 복구 (동일 엔트리 중복 처리 방지)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Auto Tuning 관련 (순위 4 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════
    AUTO_ADJUSTMENT = "auto_adjustment"
    """자율 조정 (동일 조정 중복 적용 방지)."""

    # ═══════════════════════════════════════════════════════════════════════════
    # Multi-Region Active-Active 복구 액션
    # 리전 간 동일 복구 액션 중복 실행 방지
    # ═══════════════════════════════════════════════════════════════════════════
    RECOVERY_ACTION = "recovery_action"
    """복구 액션 (CB 리셋, Pod 재시작, DLQ 재시도 등) 중복 실행 방지."""


@dataclass
class IdempotencyKey:
    """
    Represents an idempotency key with domain context.

    The key is a combination of domain-specific identifiers that
    uniquely identify an operation.
    """

    domain: IdempotencyDomain
    key: str
    components: dict[str, Any]

    @property
    def cache_key(self) -> str:
        """Get the cache key for Redis/memcached storage."""
        return f"idempotency:{self.domain.value}:{self.key}"

    @property
    def hash(self) -> str:
        """Get a hash of the key for indexing."""
        return hashlib.sha256(self.cache_key.encode()).hexdigest()[:32]

    @classmethod
    def for_operation(
        cls,
        entity_type: str,
        entity_id: int,
        operation: str,
        domain: IdempotencyDomain = IdempotencyDomain.EXTERNAL_SERVICE,
    ) -> IdempotencyKey:
        """
        Create an idempotency key for a generic operation.

        Args:
            entity_type: Type of entity (e.g., "order", "user", "product")
            entity_id: The entity ID
            operation: The operation being performed (e.g., "process", "update")
            domain: The domain category

        Returns:
            IdempotencyKey for the operation
        """
        key = f"{entity_type}:{entity_id}:{operation}"
        return cls(
            domain=domain,
            key=key,
            components={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "operation": operation,
            },
        )

    @classmethod
    def for_event(cls, event_id: str) -> IdempotencyKey:
        """
        Create an idempotency key for event processing.

        Args:
            event_id: The unique event ID

        Returns:
            IdempotencyKey for the event
        """
        return cls(
            domain=IdempotencyDomain.EVENT,
            key=event_id,
            components={"event_id": event_id},
        )

    @classmethod
    def for_resource_action(
        cls,
        resource_type: str,
        resource_id: int,
        action: str,
        amount: int | None = None,
    ) -> IdempotencyKey:
        """
        Create an idempotency key for resource actions.

        Args:
            resource_type: Type of resource
            resource_id: The resource ID
            action: The action being performed
            amount: Optional amount for the action

        Returns:
            IdempotencyKey for the resource action
        """
        if amount is not None:
            key = f"{resource_type}:{resource_id}:{action}:{amount}"
        else:
            key = f"{resource_type}:{resource_id}:{action}"
        return cls(
            domain=IdempotencyDomain.INTERNAL_PROCESS,
            key=key,
            components={
                "resource_type": resource_type,
                "resource_id": resource_id,
                "action": action,
                "amount": amount,
            },
        )

    @classmethod
    def custom(cls, key: str, **components: Any) -> IdempotencyKey:
        """
        Create a custom idempotency key.

        Args:
            key: The raw key string
            **components: Key components for debugging

        Returns:
            IdempotencyKey with custom domain
        """
        return cls(
            domain=IdempotencyDomain.CUSTOM,
            key=key,
            components=components,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 신규 팩토리 메서드 (순위 5 - v2.4.0)
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def for_chaos_experiment(
        cls,
        schedule_id: str,
        experiment_type: str,
        target_service: str,
    ) -> IdempotencyKey:
        """
        Chaos 실험에 대한 멱등성 키 생성.

        동일 스케줄의 실험이 동시에 실행되는 것을 방지.

        Args:
            schedule_id: 스케줄 ID
            experiment_type: 실험 유형 (예: latency_injection, fault_injection)
            target_service: 대상 서비스

        Returns:
            IdempotencyKey for chaos experiment
        """
        key = f"chaos:{schedule_id}:{experiment_type}:{target_service}"
        return cls(
            domain=IdempotencyDomain.CHAOS_EXPERIMENT,
            key=key,
            components={
                "schedule_id": schedule_id,
                "experiment_type": experiment_type,
                "target_service": target_service,
            },
        )

    @classmethod
    def for_chaos_service_lock(
        cls,
        target_service: str,
    ) -> IdempotencyKey:
        """
        서비스 단위 Chaos 실험 락.

        동일 서비스에 2개 이상의 실험이 동시 실행되는 것을 방지.
        (원인 분석 명확성 확보)

        Schedule 락과 함께 사용하는 이중 락 패턴:
        - Service Lock: 동시성 제어 (실험 종료 시 해제)
        - Schedule Lock: 재실행 방지 (TTL까지 유지)

        Args:
            target_service: 대상 서비스명

        Returns:
            IdempotencyKey for service-level chaos lock

        Usage:
            # 이중 락 패턴 사용 예시
            service_lock = IdempotencyKey.for_chaos_service_lock(target_service)
            schedule_lock = IdempotencyKey.for_chaos_experiment(schedule_id, exp_type, target_service)

            # 1. 서비스 락 획득 (동시성 제어)
            if not idempotency.acquire_lock(service_lock, ttl_seconds=7200):
                return "다른 실험 실행 중"

            # 2. 스케줄 락 획득 (재실행 방지)
            if not idempotency.acquire_lock(schedule_lock, ttl_seconds=86400):
                idempotency.release_lock(service_lock)  # 롤백
                return "이미 실행된 스케줄"

            try:
                # 3. 실험 실행
                execute_experiment()
            finally:
                # 4. 서비스 락만 해제 (스케줄 락은 TTL 유지)
                idempotency.release_lock(service_lock)

        """
        key = f"chaos:service_lock:{target_service}"
        return cls(
            domain=IdempotencyDomain.CHAOS_EXPERIMENT,
            key=key,
            components={
                "lock_type": "service_level",
                "target_service": target_service,
            },
        )

    @classmethod
    def for_config_change(
        cls,
        config_key: str,
        new_value_hash: str,
        changed_by: str,
        request_id: str | None = None,
        window_id: str | None = None,
    ) -> IdempotencyKey:
        """
        설정 변경에 대한 멱등성 키 생성.

        동일 설정 변경이 중복 적용되는 것을 방지.

        Args:
            config_key: 설정 키
            new_value_hash: 새 값의 해시
            changed_by: 변경 주체
            request_id: 요청 ID (동일 요청 재시도만 중복 처리)
            window_id: 슬라이딩 윈도우 ID (시간 창 기반, 권장)

        멱등성 범위 정책:
        - request_id 제공: 동일 요청의 재시도만 중복
        - window_id 제공: 동일 윈도우 내 동일 변경만 중복
        - 둘 다 없음: new_value_hash 기준 (기존 동작)

        Returns:
            IdempotencyKey for config change

        Reference: Architect Review - "의도된 재설정 vs 중복 구분"
        """
        if request_id:
            # 요청 단위 멱등성 (가장 엄격)
            key = f"config:{config_key}:{request_id}"
        elif window_id:
            # 슬라이딩 윈도우 기반 (권장)
            key = f"config:{config_key}:{new_value_hash}:w{window_id}"
        else:
            # 기존 동작 (값 기반)
            key = f"config:{config_key}:{new_value_hash}"

        return cls(
            domain=IdempotencyDomain.CONFIG_CHANGE,
            key=key,
            components={
                "config_key": config_key,
                "new_value_hash": new_value_hash,
                "changed_by": changed_by,
                "request_id": request_id,
                "window_id": window_id,
            },
        )

    @classmethod
    def for_l2_sync(
        cls,
        service_name: str,
        record_id: str,
        intended_state: str,
    ) -> IdempotencyKey:
        """
        L2 동기화에 대한 멱등성 키 생성.

        복구 후 동일 레코드가 중복 동기화되는 것을 방지.

        Args:
            service_name: 서비스 이름
            record_id: 레코드 ID
            intended_state: 목표 상태

        Returns:
            IdempotencyKey for L2 sync
        """
        key = f"l2sync:{service_name}:{record_id}"
        return cls(
            domain=IdempotencyDomain.L2_SYNC,
            key=key,
            components={
                "service_name": service_name,
                "record_id": record_id,
                "intended_state": intended_state,
            },
        )

    @classmethod
    def for_wal_recovery(
        cls,
        wal_entry_id: str,
        operation: str,
    ) -> IdempotencyKey:
        """
        WAL 복구에 대한 멱등성 키 생성.

        동일 WAL 엔트리가 중복 처리되는 것을 방지.

        Args:
            wal_entry_id: WAL 엔트리 ID
            operation: 복구 작업 유형

        Returns:
            IdempotencyKey for WAL recovery
        """
        key = f"wal:{wal_entry_id}:{operation}"
        return cls(
            domain=IdempotencyDomain.WAL_RECOVERY,
            key=key,
            components={
                "wal_entry_id": wal_entry_id,
                "operation": operation,
            },
        )

    @classmethod
    def for_auto_adjustment(
        cls,
        module: str,
        parameter: str,
        target_value: str,
    ) -> IdempotencyKey:
        """
        자율 조정에 대한 멱등성 키 생성.

        동일 조정이 중복 적용되는 것을 방지.

        Args:
            module: 모듈 이름 (circuit_breaker, retry 등)
            parameter: 파라미터 이름
            target_value: 목표 값

        Returns:
            IdempotencyKey for auto adjustment

        Note:
            플래핑 체크는 AntiFlappingWindow를 별도로 사용하세요.
            get_anti_flapping_window().check_and_record(...)
        """
        key = f"adjust:{module}:{parameter}:{target_value}"
        return cls(
            domain=IdempotencyDomain.AUTO_ADJUSTMENT,
            key=key,
            components={
                "module": module,
                "parameter": parameter,
                "target_value": target_value,
            },
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # Multi-Region Active-Active 복구 액션
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def for_recovery_action(
        cls,
        action_type: str,
        target: str,
        region_id: str,
        session_id: str,
    ) -> IdempotencyKey:
        """
        복구 액션에 대한 멱등성 키 생성.

        Multi-Region Active-Active 환경에서 리전 간 동일 복구 액션의
        중복 실행을 방지합니다.

        Args:
            action_type: 액션 유형 ("cb_reset", "pod_restart", "dlq_retry" 등)
            target: 대상 (서비스명, Pod 이름 등)
            region_id: 실행 리전 (예: "ap-northeast-2")
            session_id: 복구 세션 ID (동일 복구 세션 내 중복 방지)

        Returns:
            IdempotencyKey for recovery action

        Example:
            # CB 리셋 전 멱등성 확인
            key = IdempotencyKey.for_recovery_action(
                action_type="cb_reset",
                target="payment_api",
                region_id="ap-northeast-2",
                session_id="sess-12345",
            )

            result = idempotency_service.check(key)
            if result.is_duplicate:
                logger.info("idempotency.already_executed_another_region")
                return

            # 실행
            circuit_breaker.reset("payment_api")
        """
        key = f"recovery:{action_type}:{target}:{session_id}"
        return cls(
            domain=IdempotencyDomain.RECOVERY_ACTION,
            key=key,
            components={
                "action_type": action_type,
                "target": target,
                "region_id": region_id,
                "session_id": session_id,
            },
        )

    @classmethod
    def for_cb_reset(
        cls,
        service_name: str,
        region_id: str,
        trigger_id: str,
    ) -> IdempotencyKey:
        """
        Circuit Breaker 리셋 전용 멱등성 키.

        for_recovery_action의 편의 메서드로, CB 리셋에 특화된 인터페이스를 제공합니다.

        Args:
            service_name: 서비스명 (예: "payment_api")
            region_id: 실행 리전 (예: "ap-northeast-2")
            trigger_id: 트리거 ID (예: recovery session ID)

        Returns:
            IdempotencyKey for CB reset action

        Example:
            key = IdempotencyKey.for_cb_reset(
                service_name="payment_api",
                region_id="ap-northeast-2",
                trigger_id="recovery-sess-abc123",
            )

            if not idempotency_service.check(key).is_duplicate:
                circuit_breaker.reset("payment_api")
        """
        return cls.for_recovery_action(
            action_type="cb_reset",
            target=service_name,
            region_id=region_id,
            session_id=trigger_id,
        )

    @classmethod
    def for_pod_restart(
        cls,
        pod_name: str,
        namespace: str,
        region_id: str,
        session_id: str,
    ) -> IdempotencyKey:
        """
        Pod 재시작 전용 멱등성 키.

        Args:
            pod_name: Pod 이름
            namespace: Kubernetes 네임스페이스
            region_id: 실행 리전
            session_id: 복구 세션 ID

        Returns:
            IdempotencyKey for pod restart action
        """
        target = f"{namespace}/{pod_name}"
        return cls.for_recovery_action(
            action_type="pod_restart",
            target=target,
            region_id=region_id,
            session_id=session_id,
        )

    @classmethod
    def for_dlq_retry(
        cls,
        queue_name: str,
        message_id: str,
        region_id: str,
        session_id: str,
    ) -> IdempotencyKey:
        """
        DLQ 재시도 전용 멱등성 키.

        Args:
            queue_name: DLQ 큐 이름
            message_id: 메시지 ID
            region_id: 실행 리전
            session_id: 복구 세션 ID

        Returns:
            IdempotencyKey for DLQ retry action
        """
        target = f"{queue_name}:{message_id}"
        return cls.for_recovery_action(
            action_type="dlq_retry",
            target=target,
            region_id=region_id,
            session_id=session_id,
        )


@dataclass
class IdempotencyResult(Generic[T]):
    """Result of an idempotency check."""

    is_duplicate: bool
    existing_record: T | None = None
    message: str = ""

    @property
    def should_proceed(self) -> bool:
        """Whether the operation should proceed (not a duplicate)."""
        return not self.is_duplicate

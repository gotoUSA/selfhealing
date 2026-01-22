"""
Causation Context - 인과관계 추적 컨텍스트.

비동기 경계(Celery/Kafka)에서 인과관계 정보를 안전하게 전파합니다.

Features:
- contextvars 기반 스레드/async 안전한 컨텍스트 관리
- Celery 태스크 간 인과관계 정보 전파
- cascade_id, parent_event_id, chain_depth 추적

Usage:
    # 새 Cascade 시작
    with CausationContext.start_cascade(namespace="seoul") as ctx:
        print(f"Cascade ID: {ctx.cascade_id}")
        do_work()
    
    # Celery 태스크 호출 시
    my_task.apply_async(
        args=[...],
        headers=get_causation_for_celery(),
    )
    
    # Celery 태스크 내에서 복원
    @shared_task(bind=True)
    def my_task(self, ...):
        with restore_causation_from_celery(self.request.headers or {}):
            do_work()

Reference:
    docs/self_healing/middleware_system/76_CASCADE_EVENT_AUDIT.md
    context/actor_context.py (패턴 참조)
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Generator, Optional

logger = logging.getLogger(__name__)


# =============================================================================
# Celery 헤더 상수
# =============================================================================


CELERY_HEADER_CASCADE_ID = "x-selfhealing-cascade-id"
"""Celery 메시지 헤더: Cascade ID."""

CELERY_HEADER_PARENT_EVENT = "x-selfhealing-parent-event"
"""Celery 메시지 헤더: 부모 이벤트 ID."""

CELERY_HEADER_CHAIN_DEPTH = "x-selfhealing-chain-depth"
"""Celery 메시지 헤더: 체인 깊이."""

CELERY_HEADER_NAMESPACE = "x-selfhealing-namespace"
"""Celery 메시지 헤더: 네임스페이스."""


# Kafka 헤더 (동일 구조)
KAFKA_HEADER_PREFIX = "selfhealing."
"""Kafka 메시지 헤더 접두사."""


# =============================================================================
# CausationInfo
# =============================================================================


@dataclass
class CausationInfo:
    """
    인과관계 추적 정보.
    
    contextvars를 사용하여 스레드/async 안전을 보장합니다.
    
    Attributes:
        cascade_id: 현재 Cascade Event ID
        parent_event_id: 부모 이벤트 ID (인과관계 체인)
        chain_depth: 현재 체인 깊이 (순환 참조 방지용)
        namespace: 네임스페이스
        metadata: 추가 메타데이터
    
    Code reference:
        context/actor_context.py#L48 (_current_actor ContextVar 패턴)
    """
    
    cascade_id: str
    """현재 Cascade Event ID."""
    
    parent_event_id: str
    """부모 이벤트 ID (인과관계 체인)."""
    
    chain_depth: int = 0
    """현재 체인 깊이 (순환 참조 방지용)."""
    
    namespace: str = "global"
    """네임스페이스."""
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
    
    def to_dict(self) -> Dict[str, Any]:
        """직렬화 (Celery/Kafka 전송용)."""
        return {
            "cascade_id": self.cascade_id,
            "parent_event_id": self.parent_event_id,
            "chain_depth": self.chain_depth,
            "namespace": self.namespace,
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CausationInfo":
        """역직렬화 (수신 측 복원용)."""
        return cls(
            cascade_id=data.get("cascade_id", ""),
            parent_event_id=data.get("parent_event_id", ""),
            chain_depth=data.get("chain_depth", 0),
            namespace=data.get("namespace", "global"),
            metadata=data.get("metadata", {}),
        )


# =============================================================================
# ContextVar 선언
# =============================================================================


_current_causation: ContextVar[Optional[CausationInfo]] = ContextVar(
    "current_causation", default=None
)
"""현재 인과관계 컨텍스트 (actor_context.py 패턴 준수)."""


# =============================================================================
# CausationContext
# =============================================================================


class CausationContext:
    """
    인과관계 컨텍스트 관리자.
    
    Python의 contextvars를 사용하여 스레드 및 async 환경에서
    안전하게 인과관계 정보를 추적합니다.
    
    Usage:
        # 새 Cascade 시작
        with CausationContext.start_cascade(namespace="seoul") as ctx:
            print(f"Cascade: {ctx.cascade_id}")
            # ctx.cascade_id 사용 가능
            do_work()
        
        # 기존 Cascade 계속 (비동기 경계 복원)
        with CausationContext.continue_cascade(causation_info):
            do_work()
        
        # 현재 컨텍스트 조회
        info = CausationContext.get_current()
        if info:
            print(f"Current cascade: {info.cascade_id}")
    
    Code reference:
        context/actor_context.py (ActorContext 패턴)
    """
    
    @classmethod
    @contextmanager
    def start_cascade(
        cls,
        namespace: str = "global",
        trigger_event_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Generator[CausationInfo, None, None]:
        """
        새 Cascade 시작.
        
        Args:
            namespace: 네임스페이스
            trigger_event_id: 트리거 이벤트 ID (없으면 자동 생성)
            metadata: 추가 메타데이터
        
        Yields:
            CausationInfo 인스턴스
        """
        cascade_id = f"cascade-{uuid.uuid4().hex[:12]}"
        event_id = trigger_event_id or f"evt-{uuid.uuid4().hex[:8]}"
        
        info = CausationInfo(
            cascade_id=cascade_id,
            parent_event_id=event_id,
            chain_depth=0,
            namespace=namespace,
            metadata=metadata or {},
        )
        
        token = _current_causation.set(info)
        try:
            logger.debug(
                f"[CausationContext] Started cascade: "
                f"cascade={cascade_id}, namespace={namespace}"
            )
            yield info
        finally:
            _current_causation.reset(token)
            logger.debug(
                f"[CausationContext] Ended cascade: "
                f"cascade={cascade_id}"
            )
    
    @classmethod
    @contextmanager
    def continue_cascade(
        cls,
        info: CausationInfo,
        increment_depth: bool = True,
    ) -> Generator[CausationInfo, None, None]:
        """
        기존 Cascade 계속 (비동기 경계 복원).
        
        Args:
            info: 복원할 CausationInfo
            increment_depth: 체인 깊이 증가 여부
        
        Yields:
            CausationInfo 인스턴스 (깊이 증가됨)
        """
        new_depth = info.chain_depth + 1 if increment_depth else info.chain_depth
        
        continued_info = CausationInfo(
            cascade_id=info.cascade_id,
            parent_event_id=info.parent_event_id,
            chain_depth=new_depth,
            namespace=info.namespace,
            metadata=dict(info.metadata),  # 복사
        )
        
        token = _current_causation.set(continued_info)
        try:
            logger.debug(
                f"[CausationContext] Continued cascade: "
                f"cascade={info.cascade_id}, depth={new_depth}"
            )
            yield continued_info
        finally:
            _current_causation.reset(token)
    
    @classmethod
    def get_current(cls) -> Optional[CausationInfo]:
        """
        현재 컨텍스트 조회.
        
        Returns:
            현재 CausationInfo 또는 None
        """
        return _current_causation.get()
    
    @classmethod
    def is_set(cls) -> bool:
        """
        컨텍스트 설정 여부 확인.
        
        Returns:
            컨텍스트가 설정되어 있으면 True
        """
        return _current_causation.get() is not None
    
    @classmethod
    def get_current_cascade_id(cls) -> Optional[str]:
        """
        현재 Cascade ID 조회.
        
        Returns:
            현재 cascade_id 또는 None
        """
        info = cls.get_current()
        return info.cascade_id if info else None
    
    @classmethod
    def get_current_depth(cls) -> int:
        """
        현재 체인 깊이 조회.
        
        Returns:
            현재 chain_depth (컨텍스트 없으면 0)
        """
        info = cls.get_current()
        return info.chain_depth if info else 0
    
    @classmethod
    @contextmanager
    def set_parent_event(
        cls,
        new_event_id: str,
    ) -> Generator[CausationInfo, None, None]:
        """
        부모 이벤트 ID 변경.
        
        현재 컨텍스트 내에서 새 효과를 기록할 때 사용합니다.
        
        Args:
            new_event_id: 새 부모 이벤트 ID
        
        Yields:
            업데이트된 CausationInfo
        """
        current = cls.get_current()
        if not current:
            raise RuntimeError("No causation context set")
        
        updated_info = CausationInfo(
            cascade_id=current.cascade_id,
            parent_event_id=new_event_id,
            chain_depth=current.chain_depth,
            namespace=current.namespace,
            metadata=dict(current.metadata),
        )
        
        token = _current_causation.set(updated_info)
        try:
            yield updated_info
        finally:
            _current_causation.reset(token)


# =============================================================================
# Celery 전파 함수
# =============================================================================


def get_causation_for_celery() -> Dict[str, str]:
    """
    Celery Task 호출 시 전달할 causation 헤더 생성.
    
    Usage:
        my_task.apply_async(
            args=[...],
            headers=get_causation_for_celery(),
        )
    
    Returns:
        Celery 메시지 헤더 딕셔너리
    
    Code reference:
        context/actor_context.py (get_actor_for_celery 패턴)
    """
    info = CausationContext.get_current()
    if not info:
        return {}
    
    return {
        CELERY_HEADER_CASCADE_ID: info.cascade_id,
        CELERY_HEADER_PARENT_EVENT: info.parent_event_id,
        CELERY_HEADER_CHAIN_DEPTH: str(info.chain_depth),
        CELERY_HEADER_NAMESPACE: info.namespace,
    }


@contextmanager
def restore_causation_from_celery(
    headers: Dict[str, str],
) -> Generator[Optional[CausationInfo], None, None]:
    """
    Celery Task에서 causation 복원.
    
    Usage:
        @shared_task(bind=True)
        def my_task(self, ...):
            with restore_causation_from_celery(self.request.headers or {}):
                do_work()
    
    Args:
        headers: Celery request headers
    
    Yields:
        복원된 CausationInfo 또는 None
    
    Code reference:
        context/actor_context.py (restore_actor_from_celery 패턴)
    """
    cascade_id = headers.get(CELERY_HEADER_CASCADE_ID)
    
    if not cascade_id:
        yield None
        return
    
    info = CausationInfo(
        cascade_id=cascade_id,
        parent_event_id=headers.get(CELERY_HEADER_PARENT_EVENT, ""),
        chain_depth=int(headers.get(CELERY_HEADER_CHAIN_DEPTH, "0")),
        namespace=headers.get(CELERY_HEADER_NAMESPACE, "global"),
        metadata={
            "restored_from": "celery",
            "restored_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    
    with CausationContext.continue_cascade(info) as ctx:
        yield ctx


def get_causation_for_kafka() -> Dict[str, bytes]:
    """
    Kafka 메시지 전송 시 전달할 causation 헤더 생성.
    
    Usage:
        producer.send(
            topic="my-topic",
            value=message,
            headers=list(get_causation_for_kafka().items()),
        )
    
    Returns:
        Kafka 메시지 헤더 딕셔너리 (bytes 값)
    """
    info = CausationContext.get_current()
    if not info:
        return {}
    
    return {
        f"{KAFKA_HEADER_PREFIX}cascade_id": info.cascade_id.encode("utf-8"),
        f"{KAFKA_HEADER_PREFIX}parent_event": info.parent_event_id.encode("utf-8"),
        f"{KAFKA_HEADER_PREFIX}chain_depth": str(info.chain_depth).encode("utf-8"),
        f"{KAFKA_HEADER_PREFIX}namespace": info.namespace.encode("utf-8"),
    }


@contextmanager
def restore_causation_from_kafka(
    headers: Optional[list] = None,
) -> Generator[Optional[CausationInfo], None, None]:
    """
    Kafka Consumer에서 causation 복원.
    
    Usage:
        for message in consumer:
            with restore_causation_from_kafka(message.headers):
                process_message(message)
    
    Args:
        headers: Kafka 메시지 헤더 리스트 [(key, value), ...]
    
    Yields:
        복원된 CausationInfo 또는 None
    """
    if not headers:
        yield None
        return
    
    # 헤더를 딕셔너리로 변환
    header_dict = {}
    for key, value in headers:
        if key.startswith(KAFKA_HEADER_PREFIX):
            short_key = key[len(KAFKA_HEADER_PREFIX):]
            header_dict[short_key] = value.decode("utf-8") if isinstance(value, bytes) else value
    
    cascade_id = header_dict.get("cascade_id")
    if not cascade_id:
        yield None
        return
    
    info = CausationInfo(
        cascade_id=cascade_id,
        parent_event_id=header_dict.get("parent_event", ""),
        chain_depth=int(header_dict.get("chain_depth", "0")),
        namespace=header_dict.get("namespace", "global"),
        metadata={
            "restored_from": "kafka",
            "restored_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    
    with CausationContext.continue_cascade(info) as ctx:
        yield ctx

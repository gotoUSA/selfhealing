"""
Recovery Session Archive.

PostgreSQL 기반 복구 세션 영속화 모듈.

Redis 세션 데이터를 PostgreSQL에 아카이브하여:
1. 복구 이력 장기 보관
2. 시스템 재시작 후 Resume 지원
3. 감사 및 분석용 데이터 제공

Phase 1.4 구현:
- RecoverySessionArchiveModel (Django ORM)
- RecoverySessionArchiveService (아카이브 서비스)
- RecoveryStepArchiveModel (단계별 상세)

Reference:
    docs/self_healing/middleware_system/77_RECOVERY_COORDINATOR.md#8.5.1
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .enums import RecoveryStatus
from .recovery_state import RecoverySession, RecoveryStep, RecoveryStepType

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Archive Data Models (Dataclass - Django 독립)
# =============================================================================

@dataclass
class RecoveryStepArchiveData:
    """
    복구 단계 아카이브 데이터.
    
    Django 모델에 저장할 단계 정보를 담는 데이터 클래스.
    """
    
    step_type: str
    """단계 유형 (RecoveryStepType.value)."""
    
    order: int
    """실행 순서."""
    
    status: str
    """상태 (RecoveryStatus.value)."""
    
    wait_after_seconds: int = 0
    """완료 후 대기 시간."""
    
    params: Dict[str, Any] = field(default_factory=dict)
    """단계 파라미터."""
    
    started_at: Optional[str] = None
    """시작 시각 (ISO 8601)."""
    
    completed_at: Optional[str] = None
    """완료 시각 (ISO 8601)."""
    
    error_message: Optional[str] = None
    """에러 메시지."""
    
    execution_time_ms: Optional[int] = None
    """실행 시간 (밀리초)."""
    
    retry_count: int = 0
    """재시도 횟수."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "step_type": self.step_type,
            "order": self.order,
            "status": self.status,
            "wait_after_seconds": self.wait_after_seconds,
            "params": self.params,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "error_message": self.error_message,
            "execution_time_ms": self.execution_time_ms,
            "retry_count": self.retry_count,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoveryStepArchiveData":
        """딕셔너리에서 생성."""
        return cls(
            step_type=data.get("step_type", ""),
            order=data.get("order", 0),
            status=data.get("status", "not_started"),
            wait_after_seconds=data.get("wait_after_seconds", 0),
            params=data.get("params", {}),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error_message=data.get("error_message"),
            execution_time_ms=data.get("execution_time_ms"),
            retry_count=data.get("retry_count", 0),
        )
    
    @classmethod
    def from_step(cls, step: RecoveryStep) -> "RecoveryStepArchiveData":
        """RecoveryStep에서 생성."""
        execution_time = None
        if step.started_at and step.completed_at:
            try:
                start = datetime.fromisoformat(step.started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(step.completed_at.replace("Z", "+00:00"))
                execution_time = int((end - start).total_seconds() * 1000)
            except (ValueError, TypeError):
                pass
        
        return cls(
            step_type=step.step_type.value,
            order=step.order,
            status=step.status.value,
            wait_after_seconds=step.wait_after_seconds,
            params=dict(step.params),
            started_at=step.started_at,
            completed_at=step.completed_at,
            error_message=step.error_message,
            execution_time_ms=execution_time,
        )


@dataclass
class RecoverySessionArchiveData:
    """
    복구 세션 아카이브 데이터.
    
    Django 모델에 저장할 세션 정보를 담는 데이터 클래스.
    """
    
    session_id: str
    """고유 세션 ID."""
    
    namespace: str
    """네임스페이스."""
    
    trigger_level: str
    """트리거 Emergency 레벨."""
    
    status: str
    """최종 상태 (RecoveryStatus.value)."""
    
    steps: List[RecoveryStepArchiveData] = field(default_factory=list)
    """단계 목록."""
    
    current_step_index: int = 0
    """현재/마지막 단계 인덱스."""
    
    started_at: Optional[str] = None
    """시작 시각."""
    
    completed_at: Optional[str] = None
    """완료 시각."""
    
    initiated_by: str = "system"
    """시작 주체."""
    
    abort_reason: Optional[str] = None
    """중단 사유."""
    
    cascade_event_id: Optional[str] = None
    """Cascade Event ID."""
    
    total_duration_seconds: Optional[int] = None
    """총 소요 시간 (초)."""
    
    metadata: Dict[str, Any] = field(default_factory=dict)
    """추가 메타데이터."""
    
    archived_at: Optional[str] = None
    """아카이브 시각."""
    
    def to_dict(self) -> Dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "session_id": self.session_id,
            "namespace": self.namespace,
            "trigger_level": self.trigger_level,
            "status": self.status,
            "steps": [s.to_dict() for s in self.steps],
            "current_step_index": self.current_step_index,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "initiated_by": self.initiated_by,
            "abort_reason": self.abort_reason,
            "cascade_event_id": self.cascade_event_id,
            "total_duration_seconds": self.total_duration_seconds,
            "metadata": self.metadata,
            "archived_at": self.archived_at,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RecoverySessionArchiveData":
        """딕셔너리에서 생성."""
        steps = [
            RecoveryStepArchiveData.from_dict(s)
            for s in data.get("steps", [])
        ]
        
        return cls(
            session_id=data.get("session_id", ""),
            namespace=data.get("namespace", ""),
            trigger_level=data.get("trigger_level", ""),
            status=data.get("status", "not_started"),
            steps=steps,
            current_step_index=data.get("current_step_index", 0),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            initiated_by=data.get("initiated_by", "system"),
            abort_reason=data.get("abort_reason"),
            cascade_event_id=data.get("cascade_event_id"),
            total_duration_seconds=data.get("total_duration_seconds"),
            metadata=data.get("metadata", {}),
            archived_at=data.get("archived_at"),
        )
    
    @classmethod
    def from_session(cls, session: RecoverySession) -> "RecoverySessionArchiveData":
        """RecoverySession에서 생성."""
        steps = [RecoveryStepArchiveData.from_step(s) for s in session.steps]
        
        # 총 소요 시간 계산
        total_duration = None
        if session.started_at and session.completed_at:
            try:
                start = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
                end = datetime.fromisoformat(session.completed_at.replace("Z", "+00:00"))
                total_duration = int((end - start).total_seconds())
            except (ValueError, TypeError):
                pass
        
        return cls(
            session_id=session.id,
            namespace=session.namespace,
            trigger_level=session.trigger_level,
            status=session.status.value,
            steps=steps,
            current_step_index=session.current_step_index,
            started_at=session.started_at,
            completed_at=session.completed_at,
            initiated_by=session.initiated_by,
            abort_reason=session.abort_reason,
            cascade_event_id=session.cascade_event_id,
            total_duration_seconds=total_duration,
            archived_at=datetime.now(timezone.utc).isoformat(),
        )
    
    def to_session(self) -> RecoverySession:
        """RecoverySession으로 변환 (Resume 지원)."""
        steps = [
            RecoveryStep(
                step_type=RecoveryStepType(s.step_type),
                order=s.order,
                status=RecoveryStatus(s.status),
                wait_after_seconds=s.wait_after_seconds,
                params=dict(s.params),
                started_at=s.started_at,
                completed_at=s.completed_at,
                error_message=s.error_message,
            )
            for s in self.steps
        ]
        
        return RecoverySession(
            id=self.session_id,
            namespace=self.namespace,
            trigger_level=self.trigger_level,
            status=RecoveryStatus(self.status),
            steps=steps,
            current_step_index=self.current_step_index,
            started_at=self.started_at,
            completed_at=self.completed_at,
            initiated_by=self.initiated_by,
            abort_reason=self.abort_reason,
            cascade_event_id=self.cascade_event_id,
        )


# =============================================================================
# Archive Service (Django ORM 연동)
# =============================================================================

class RecoverySessionArchiveService:
    """
    복구 세션 아카이브 서비스.
    
    Redis의 복구 세션을 PostgreSQL에 영속화하고,
    이력 조회 및 Resume 기능을 제공합니다.
    
    Features:
        - 세션 완료/중단 시 자동 아카이브
        - 히스토리 조회 (네임스페이스, 날짜, 상태 필터)
        - Resume 지원 (중단된 세션 재개)
        - 통계 집계
    
    Usage:
        service = get_recovery_session_archive_service()
        
        # 세션 아카이브
        service.archive_session(session)
        
        # 히스토리 조회
        history = service.get_history(namespace="global", limit=10)
        
        # Resume
        session = service.load_for_resume(session_id)
    """
    
    def __init__(self, use_django: bool = True):
        """
        초기화.
        
        Args:
            use_django: Django ORM 사용 여부 (False면 인메모리)
        """
        self._use_django = use_django
        self._lock = threading.Lock()
        
        # 인메모리 스토리지 (Django 없을 때)
        self._memory_storage: Dict[str, RecoverySessionArchiveData] = {}
    
    def archive_session(
        self,
        session: RecoverySession,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RecoverySessionArchiveData:
        """
        세션 아카이브.
        
        완료/중단된 세션을 PostgreSQL에 저장합니다.
        
        Args:
            session: 아카이브할 RecoverySession
            metadata: 추가 메타데이터
        
        Returns:
            저장된 RecoverySessionArchiveData
        """
        archive_data = RecoverySessionArchiveData.from_session(session)
        if metadata:
            archive_data.metadata.update(metadata)
        
        if self._use_django:
            self._save_to_django(archive_data)
        else:
            with self._lock:
                self._memory_storage[archive_data.session_id] = archive_data
        
        logger.info(
            f"[RecoverySessionArchive] Archived: session_id={session.id}, "
            f"status={session.status.value}"
        )
        
        return archive_data
    
    def _save_to_django(self, data: RecoverySessionArchiveData) -> None:
        """Django ORM으로 저장."""
        try:
            from selfhealing.api.django.tiering.models import RecoverySessionArchive
            
            # 기존 레코드 확인
            existing = RecoverySessionArchive.objects.filter(
                session_id=data.session_id
            ).first()
            
            if existing:
                # 업데이트
                existing.status = data.status
                existing.steps_json = json.dumps([s.to_dict() for s in data.steps])
                existing.current_step_index = data.current_step_index
                existing.completed_at = (
                    datetime.fromisoformat(data.completed_at.replace("Z", "+00:00"))
                    if data.completed_at else None
                )
                existing.abort_reason = data.abort_reason
                existing.total_duration_seconds = data.total_duration_seconds
                existing.metadata_json = json.dumps(data.metadata)
                existing.save()
            else:
                # 신규 생성
                RecoverySessionArchive.objects.create(
                    session_id=data.session_id,
                    namespace=data.namespace,
                    trigger_level=data.trigger_level,
                    status=data.status,
                    steps_json=json.dumps([s.to_dict() for s in data.steps]),
                    current_step_index=data.current_step_index,
                    started_at=(
                        datetime.fromisoformat(data.started_at.replace("Z", "+00:00"))
                        if data.started_at else None
                    ),
                    completed_at=(
                        datetime.fromisoformat(data.completed_at.replace("Z", "+00:00"))
                        if data.completed_at else None
                    ),
                    initiated_by=data.initiated_by,
                    abort_reason=data.abort_reason,
                    cascade_event_id=data.cascade_event_id,
                    total_duration_seconds=data.total_duration_seconds,
                    metadata_json=json.dumps(data.metadata),
                )
        except ImportError:
            logger.warning("[RecoverySessionArchive] Django model not available, using memory storage")
            with self._lock:
                self._memory_storage[data.session_id] = data
        except Exception as e:
            logger.exception(f"[RecoverySessionArchive] Django save error: {e}")
            # 폴백: 메모리 저장
            with self._lock:
                self._memory_storage[data.session_id] = data
    
    def get_session(self, session_id: str) -> Optional[RecoverySessionArchiveData]:
        """
        세션 조회.
        
        Args:
            session_id: 세션 ID
        
        Returns:
            RecoverySessionArchiveData 또는 None
        """
        if self._use_django:
            return self._load_from_django(session_id)
        
        with self._lock:
            return self._memory_storage.get(session_id)
    
    def _load_from_django(self, session_id: str) -> Optional[RecoverySessionArchiveData]:
        """Django ORM에서 로드."""
        try:
            from selfhealing.api.django.tiering.models import RecoverySessionArchive
            
            record = RecoverySessionArchive.objects.filter(
                session_id=session_id
            ).first()
            
            if not record:
                return None
            
            return self._record_to_archive_data(record)
        except ImportError:
            with self._lock:
                return self._memory_storage.get(session_id)
        except Exception as e:
            logger.exception(f"[RecoverySessionArchive] Django load error: {e}")
            return None
    
    def _record_to_archive_data(self, record) -> RecoverySessionArchiveData:
        """Django 레코드를 ArchiveData로 변환."""
        steps_raw = json.loads(record.steps_json) if record.steps_json else []
        steps = [RecoveryStepArchiveData.from_dict(s) for s in steps_raw]
        
        metadata = json.loads(record.metadata_json) if record.metadata_json else {}
        
        return RecoverySessionArchiveData(
            session_id=record.session_id,
            namespace=record.namespace,
            trigger_level=record.trigger_level,
            status=record.status,
            steps=steps,
            current_step_index=record.current_step_index,
            started_at=record.started_at.isoformat() if record.started_at else None,
            completed_at=record.completed_at.isoformat() if record.completed_at else None,
            initiated_by=record.initiated_by,
            abort_reason=record.abort_reason,
            cascade_event_id=record.cascade_event_id,
            total_duration_seconds=record.total_duration_seconds,
            metadata=metadata,
            archived_at=record.archived_at.isoformat() if hasattr(record, 'archived_at') and record.archived_at else None,
        )
    
    def get_history(
        self,
        namespace: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
    ) -> List[RecoverySessionArchiveData]:
        """
        히스토리 조회.
        
        Args:
            namespace: 필터링할 네임스페이스
            status: 필터링할 상태
            limit: 최대 개수
            offset: 시작 위치
            start_date: 시작 날짜
            end_date: 종료 날짜
        
        Returns:
            세션 목록 (최신순)
        """
        if self._use_django:
            return self._get_history_from_django(
                namespace=namespace,
                status=status,
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
            )
        
        # 인메모리
        with self._lock:
            sessions = list(self._memory_storage.values())
        
        # 필터링
        if namespace:
            sessions = [s for s in sessions if s.namespace == namespace]
        if status:
            sessions = [s for s in sessions if s.status == status]
        if start_date:
            sessions = [
                s for s in sessions
                if s.started_at and datetime.fromisoformat(s.started_at.replace("Z", "+00:00")) >= start_date
            ]
        if end_date:
            sessions = [
                s for s in sessions
                if s.started_at and datetime.fromisoformat(s.started_at.replace("Z", "+00:00")) <= end_date
            ]
        
        # 정렬 (최신순)
        sessions.sort(
            key=lambda x: x.started_at or "",
            reverse=True,
        )
        
        return sessions[offset:offset + limit]
    
    def _get_history_from_django(
        self,
        namespace: Optional[str],
        status: Optional[str],
        limit: int,
        offset: int,
        start_date: Optional[datetime],
        end_date: Optional[datetime],
    ) -> List[RecoverySessionArchiveData]:
        """Django ORM에서 히스토리 조회."""
        try:
            from selfhealing.api.django.tiering.models import RecoverySessionArchive
            
            queryset = RecoverySessionArchive.objects.all()
            
            if namespace:
                queryset = queryset.filter(namespace=namespace)
            if status:
                queryset = queryset.filter(status=status)
            if start_date:
                queryset = queryset.filter(started_at__gte=start_date)
            if end_date:
                queryset = queryset.filter(started_at__lte=end_date)
            
            queryset = queryset.order_by("-started_at")[offset:offset + limit]
            
            return [self._record_to_archive_data(r) for r in queryset]
        except ImportError:
            return self.get_history(
                namespace=namespace,
                status=status,
                limit=limit,
                offset=offset,
                start_date=start_date,
                end_date=end_date,
            )
        except Exception as e:
            logger.exception(f"[RecoverySessionArchive] Django history error: {e}")
            return []
    
    def load_for_resume(self, session_id: str) -> Optional[RecoverySession]:
        """
        Resume용 세션 로드.
        
        중단된 세션을 RecoverySession으로 복원합니다.
        
        Args:
            session_id: 세션 ID
        
        Returns:
            복원된 RecoverySession 또는 None
        """
        archive_data = self.get_session(session_id)
        
        if not archive_data:
            return None
        
        # 완료된 세션은 Resume 불가
        if archive_data.status in ("completed", "aborted"):
            logger.warning(
                f"[RecoverySessionArchive] Cannot resume {archive_data.status} session: {session_id}"
            )
            return None
        
        return archive_data.to_session()
    
    def get_resumable_sessions(
        self,
        namespace: Optional[str] = None,
    ) -> List[RecoverySessionArchiveData]:
        """
        Resume 가능한 세션 목록.
        
        IN_PROGRESS, HEALTH_CHECK 상태인 세션을 반환합니다.
        
        Args:
            namespace: 필터링할 네임스페이스
        
        Returns:
            Resume 가능한 세션 목록
        """
        resumable_statuses = [
            RecoveryStatus.IN_PROGRESS.value,
            RecoveryStatus.HEALTH_CHECK.value,
            RecoveryStatus.RECOVERING.value,
        ]
        
        sessions = []
        for status in resumable_statuses:
            sessions.extend(
                self.get_history(namespace=namespace, status=status, limit=100)
            )
        
        return sessions
    
    def get_statistics(
        self,
        namespace: Optional[str] = None,
        days: int = 30,
    ) -> Dict[str, Any]:
        """
        통계 집계.
        
        Args:
            namespace: 필터링할 네임스페이스
            days: 집계 기간 (일)
        
        Returns:
            통계 딕셔너리
        """
        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        all_sessions = self.get_history(
            namespace=namespace,
            start_date=start_date,
            limit=1000,
        )
        
        total = len(all_sessions)
        completed = sum(1 for s in all_sessions if s.status == "completed")
        failed = sum(1 for s in all_sessions if s.status == "failed")
        aborted = sum(1 for s in all_sessions if s.status == "aborted")
        
        # 평균 소요 시간
        durations = [
            s.total_duration_seconds
            for s in all_sessions
            if s.total_duration_seconds is not None and s.status == "completed"
        ]
        avg_duration = sum(durations) / len(durations) if durations else 0
        
        # 단계별 실패율
        step_failures: Dict[str, int] = {}
        step_totals: Dict[str, int] = {}
        for session in all_sessions:
            for step in session.steps:
                step_totals[step.step_type] = step_totals.get(step.step_type, 0) + 1
                if step.status == "failed":
                    step_failures[step.step_type] = step_failures.get(step.step_type, 0) + 1
        
        step_failure_rates = {
            step_type: step_failures.get(step_type, 0) / count * 100
            for step_type, count in step_totals.items()
            if count > 0
        }
        
        return {
            "period_days": days,
            "namespace": namespace,
            "total_sessions": total,
            "completed": completed,
            "failed": failed,
            "aborted": aborted,
            "success_rate": (completed / total * 100) if total > 0 else 0,
            "average_duration_seconds": avg_duration,
            "step_failure_rates": step_failure_rates,
        }
    
    def cleanup_old_archives(
        self,
        retention_days: int = 365,
    ) -> int:
        """
        오래된 아카이브 정리.
        
        Args:
            retention_days: 보관 기간 (일)
        
        Returns:
            삭제된 레코드 수
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        
        if self._use_django:
            try:
                from selfhealing.api.django.tiering.models import RecoverySessionArchive
                
                deleted, _ = RecoverySessionArchive.objects.filter(
                    started_at__lt=cutoff
                ).delete()
                
                logger.info(f"[RecoverySessionArchive] Cleaned up {deleted} old archives")
                return deleted
            except ImportError:
                pass
            except Exception as e:
                logger.exception(f"[RecoverySessionArchive] Cleanup error: {e}")
        
        # 인메모리 정리
        with self._lock:
            old_ids = [
                sid for sid, data in self._memory_storage.items()
                if data.started_at and datetime.fromisoformat(data.started_at.replace("Z", "+00:00")) < cutoff
            ]
            for sid in old_ids:
                del self._memory_storage[sid]
            
            return len(old_ids)


# =============================================================================
# Singleton
# =============================================================================

_archive_service: Optional[RecoverySessionArchiveService] = None
_archive_lock = threading.Lock()


def get_recovery_session_archive_service() -> RecoverySessionArchiveService:
    """RecoverySessionArchiveService 싱글톤 반환."""
    global _archive_service
    
    if _archive_service is not None:
        return _archive_service
    
    with _archive_lock:
        if _archive_service is None:
            # Django 사용 가능 여부 확인
            try:
                import django
                django.setup()
                use_django = True
            except Exception:
                use_django = False
            
            _archive_service = RecoverySessionArchiveService(use_django=use_django)
        return _archive_service


def reset_recovery_session_archive_service() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _archive_service
    with _archive_lock:
        _archive_service = None

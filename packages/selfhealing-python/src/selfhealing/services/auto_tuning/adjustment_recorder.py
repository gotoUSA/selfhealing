"""
Adjustment Recorder - 조정 기록기

모든 자율 조정을 영구 저장하고 조회할 수 있게 합니다.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from threading import RLock
from typing import Any

import structlog

from .models import AdjustmentRecord, TuningSession, TuningState

logger = structlog.get_logger()


class AdjustmentRecorder:
    """
    조정 기록기

    모든 자율 조정을 기록하고 조회 가능하게 함
    기본적으로 메모리에 저장하며, 파일 백업도 지원
    """

    MAX_RECORDS = 1000  # 최대 보관 기록 수

    def __init__(
        self,
        storage_path: str | None = None,
        enable_persistence: bool = False,
    ):
        """
        Args:
            storage_path: 파일 저장 경로 (없으면 메모리만)
            enable_persistence: 파일 영구 저장 활성화
        """
        self._lock = RLock()
        self._records: dict[str, AdjustmentRecord] = {}
        self._sessions: dict[str, TuningSession] = {}
        self._current_session_id: str | None = None

        self.storage_path = storage_path
        self.enable_persistence = enable_persistence and storage_path is not None

        if self.enable_persistence:
            self._load_from_file()

        logger.info("initialized")

    def record(
        self,
        parameter: str,
        old_value: float,
        new_value: float,
        reason: str,
        confidence: float = 0.0,
        triggered_by: str = "system",
        metrics_snapshot: dict[str, float] | None = None,
    ) -> AdjustmentRecord:
        """
        조정 기록

        Args:
            parameter: 파라미터 이름
            old_value: 이전 값
            new_value: 새 값
            reason: 조정 사유
            confidence: 신뢰도
            triggered_by: 트리거 주체
            metrics_snapshot: 메트릭 스냅샷

        Returns:
            생성된 AdjustmentRecord
        """
        with self._lock:
            record_id = self._generate_id()

            record = AdjustmentRecord(
                record_id=record_id,
                parameter=parameter,
                old_value=old_value,
                new_value=new_value,
                reason=reason,
                confidence=confidence,
                triggered_by=triggered_by,
                metrics_snapshot=metrics_snapshot or {},
            )

            self._records[record_id] = record

            # 현재 세션에 추가
            if self._current_session_id:
                session = self._sessions.get(self._current_session_id)
                if session:
                    session.adjustments.append(record_id)
                    session.total_adjustments += 1
                    if record.success:
                        session.successful_adjustments += 1

            # 오래된 기록 정리
            self._cleanup_old_records()

            # 영구 저장
            if self.enable_persistence:
                self._save_to_file()

            logger.info(
                "adjustment_recorder.recorded",
                tuning_parameter=parameter,
                old_value=old_value,
                new_value=new_value,
                record_id=record_id,
            )

            return record

    def record_dict(self, adjustment: dict[str, Any]) -> AdjustmentRecord:
        """딕셔너리로부터 기록 (RuntimeFeedbackLoop 호환)"""
        return self.record(
            parameter=adjustment.get("parameter", "unknown"),
            old_value=adjustment.get("old_value", 0),
            new_value=adjustment.get("new_value", 0),
            reason=adjustment.get("reason", ""),
            confidence=adjustment.get("confidence", 0.0),
            triggered_by=adjustment.get("triggered_by", "system"),
            metrics_snapshot=adjustment.get("metrics_snapshot"),
        )

    def mark_rollback(self, record_id: str) -> bool:
        """롤백 수행 기록"""
        with self._lock:
            record = self._records.get(record_id)
            if not record:
                return False

            record.rollback_performed = True
            record.rollback_timestamp = datetime.now(timezone.utc)

            # 세션 통계 업데이트
            if self._current_session_id:
                session = self._sessions.get(self._current_session_id)
                if session:
                    session.rolled_back_adjustments += 1

            if self.enable_persistence:
                self._save_to_file()

            logger.info(
                "adjustment_recorder.marked_rollback",
                record_id=record_id,
            )
            return True

    def get_record(self, record_id: str) -> AdjustmentRecord | None:
        """기록 조회"""
        with self._lock:
            return self._records.get(record_id)

    def get_records(
        self,
        parameter: str | None = None,
        limit: int = 50,
        include_rolled_back: bool = True,
    ) -> list[AdjustmentRecord]:
        """
        기록 목록 조회

        Args:
            parameter: 특정 파라미터만 필터
            limit: 최대 개수
            include_rolled_back: 롤백된 기록 포함 여부
        """
        with self._lock:
            records = list(self._records.values())

            # 필터링
            if parameter:
                records = [r for r in records if r.parameter == parameter]

            if not include_rolled_back:
                records = [r for r in records if not r.rollback_performed]

            # 최신 순 정렬
            records.sort(key=lambda r: r.timestamp, reverse=True)

            return records[:limit]

    def start_session(self, notes: str = "") -> TuningSession:
        """새 튜닝 세션 시작"""
        with self._lock:
            # 이전 세션 종료
            if self._current_session_id:
                self.end_session()

            session_id = self._generate_id()
            session = TuningSession(
                session_id=session_id,
                notes=notes,
            )

            self._sessions[session_id] = session
            self._current_session_id = session_id

            logger.info(
                "adjustment_recorder.started_session",
                session_id=session_id,
            )
            return session

    def end_session(self, state: TuningState = TuningState.COMPLETED) -> TuningSession | None:
        """현재 세션 종료"""
        with self._lock:
            if not self._current_session_id:
                return None

            session = self._sessions.get(self._current_session_id)
            if session:
                session.ended_at = datetime.now(timezone.utc)
                session.state = state

            self._current_session_id = None

            if self.enable_persistence:
                self._save_to_file()

            logger.info(
                "adjustment_recorder.ended_session",
                tuning_session_id=session.session_id if session else "unknown",
            )
            return session

    def get_session(self, session_id: str) -> TuningSession | None:
        """세션 조회"""
        with self._lock:
            return self._sessions.get(session_id)

    def get_current_session(self) -> TuningSession | None:
        """현재 세션 조회"""
        with self._lock:
            if self._current_session_id:
                return self._sessions.get(self._current_session_id)
            return None

    def get_statistics(self) -> dict[str, Any]:
        """통계 조회"""
        with self._lock:
            records = list(self._records.values())

            total = len(records)
            successful = sum(1 for r in records if r.success)
            rolled_back = sum(1 for r in records if r.rollback_performed)

            # 파라미터별 통계
            by_parameter: dict[str, int] = {}
            for r in records:
                by_parameter[r.parameter] = by_parameter.get(r.parameter, 0) + 1

            # 트리거별 통계
            by_trigger: dict[str, int] = {}
            for r in records:
                by_trigger[r.triggered_by] = by_trigger.get(r.triggered_by, 0) + 1

            return {
                "total_records": total,
                "successful": successful,
                "rolled_back": rolled_back,
                "success_rate": successful / total if total > 0 else 0,
                "rollback_rate": rolled_back / total if total > 0 else 0,
                "by_parameter": by_parameter,
                "by_trigger": by_trigger,
                "total_sessions": len(self._sessions),
                "current_session_id": self._current_session_id,
            }

    def clear(self) -> int:
        """모든 기록 삭제"""
        with self._lock:
            count = len(self._records)
            self._records.clear()
            self._sessions.clear()
            self._current_session_id = None

            if self.enable_persistence:
                self._save_to_file()

            logger.info(
                "adjustment_recorder.cleared_records",
                cleared_records_count=count,
            )
            return count

    def _generate_id(self) -> str:
        """고유 ID 생성"""
        return str(uuid.uuid4())[:8]

    def _cleanup_old_records(self):
        """오래된 기록 정리"""
        if len(self._records) <= self.MAX_RECORDS:
            return

        # 오래된 순으로 정렬
        sorted_records = sorted(self._records.items(), key=lambda x: x[1].timestamp)

        # 초과분 삭제
        to_delete = len(self._records) - self.MAX_RECORDS
        for record_id, _ in sorted_records[:to_delete]:
            del self._records[record_id]

        logger.debug(
            "adjustment_recorder.cleaned_up_old_records",
            to_delete=to_delete,
        )

    def _save_to_file(self):
        """파일로 저장"""
        if not self.storage_path:
            return

        try:
            data = {
                "records": {rid: r.to_dict() for rid, r in self._records.items()},
                "sessions": {sid: s.to_dict() for sid, s in self._sessions.items()},
                "current_session_id": self._current_session_id,
            }

            os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

            with open(self.storage_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            logger.debug("adjustment_recorder.saved_file")
        except Exception as e:
            logger.warning(
                "adjustment_recorder.save_failed",
                error=e,
            )

    def _load_from_file(self):
        """파일에서 로드"""
        if not self.storage_path or not os.path.exists(self.storage_path):
            return

        try:
            with open(self.storage_path, encoding="utf-8") as f:
                data = json.load(f)

            for rid, rdata in data.get("records", {}).items():
                self._records[rid] = AdjustmentRecord.from_dict(rdata)

            self._current_session_id = data.get("current_session_id")

            logger.info(
                "adjustment_recorder.loaded_records_file",
                records_count=len(self._records),
            )
        except Exception as e:
            logger.warning(
                "adjustment_recorder.load_failed",
                error=e,
            )


__all__ = ["AdjustmentRecorder"]

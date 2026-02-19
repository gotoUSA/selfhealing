"""
Cascade Auditor - WAL/Load Shedding 모듈.

로컬 WAL 저장, Load Shedding, 복구 관련 책임을 담당합니다.
중복되던 WAL 파일 쓰기 패턴을 _append_to_wal()로 통합합니다.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from selfhealing.audit.cascade_auditor._helpers import get_index_ids
from selfhealing.audit.cascade_event import CascadeEvent, ExternalTraceContext

logger = logging.getLogger(__name__)

# WAL 경로 상수
LOCAL_CASCADE_WAL_DIR = "/var/log/selfhealing/cascade_wal"
LOCAL_CASCADE_WAL_PATH = f"{LOCAL_CASCADE_WAL_DIR}/cascade_audit_wal.jsonl"
LOCAL_CASCADE_FALLBACK_PATH = LOCAL_CASCADE_WAL_PATH


def _append_to_wal(data: dict) -> None:
    """
    WAL 파일에 데이터를 JSONL 형식으로 추가.

    기존 _save_to_local_wal, _record_dropped_to_wal에서 반복되던
    Path.mkdir + open("a") + json.dumps 패턴을 통합합니다.

    Args:
        data: 저장할 딕셔너리 데이터
    """
    wal_path = Path(LOCAL_CASCADE_WAL_PATH)
    wal_path.parent.mkdir(parents=True, exist_ok=True)
    with open(wal_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(data) + "\n")


class WALRecoveryMixin:
    """WAL/Load Shedding/복구 관련 메서드."""

    def record_with_load_shedding(
        self,
        trigger_type: str,
        trigger_details: dict[str, Any],
        effects: list[dict[str, Any]],
        namespace: str,
        triggered_by: str | None = None,
        external_trace: ExternalTraceContext | None = None,
    ) -> CascadeEvent | None:
        """
        Load Shedding을 적용하여 Cascade Event 기록.

        버퍼 사용률에 따라 우선순위가 낮은 이벤트를 드롭합니다.
        CRITICAL 이벤트는 절대 드롭하지 않으며, 필요시 로컬 폴백을 사용합니다.

        Args:
            trigger_type: 트리거 유형
            trigger_details: 트리거 상세 정보
            effects: 연쇄 효과 목록
            namespace: 네임스페이스
            triggered_by: 트리거 주체
            external_trace: 외부 분산 추적 컨텍스트

        Returns:
            생성된 CascadeEvent 또는 None (드롭된 경우)
        """
        load_shedding = self._get_load_shedding()

        if not load_shedding:
            # Load Shedding 비활성화 시 일반 기록
            return self.record(
                trigger_type=trigger_type,
                trigger_details=trigger_details,
                effects=effects,
                namespace=namespace,
                triggered_by=triggered_by,
                external_trace=external_trace,
            )

        # 버퍼 상태 확인
        backend = self._get_backend()
        index_key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        buffer_size = len(get_index_ids(backend, index_key))

        # Load Shedding 결정
        decision = load_shedding.should_accept(
            trigger_type=trigger_type,
            buffer_size=buffer_size,
            buffer_capacity=self._max_index_size,
        )

        if not decision["accepted"]:
            # 드롭
            logger.warning(
                f"[CascadeAudit] Event dropped by load shedding: " f"trigger={trigger_type}, reason={decision['reason']}"
            )

            # 폴백 권장 시 로컬에 저장
            if decision.get("use_fallback"):
                self._record_dropped_to_wal(
                    trigger_type=trigger_type,
                    trigger_details=trigger_details,
                    effects=effects,
                    namespace=namespace,
                    reason=decision["reason"],
                )

            return None

        # 정상 기록
        return self.record(
            trigger_type=trigger_type,
            trigger_details=trigger_details,
            effects=effects,
            namespace=namespace,
            triggered_by=triggered_by,
            external_trace=external_trace,
        )

    def _save_to_local_wal(self, event: CascadeEvent) -> None:
        """
        로컬 WAL에 Cascade Event 저장.

        Redis 장애 시 로컬 WAL 파일에 JSONL 형식으로 저장합니다.

        Args:
            event: 저장할 CascadeEvent
        """
        try:
            _append_to_wal(event.to_dict())
            logger.info(f"[CascadeAudit] Saved to local WAL: cascade={event.id}")
        except Exception as e:
            logger.error(f"[CascadeAudit] Local WAL save failed: {e}")

    # 하위 호환성
    _save_to_local_fallback = _save_to_local_wal

    def _record_dropped_to_wal(
        self,
        trigger_type: str,
        trigger_details: dict[str, Any],
        effects: list[dict[str, Any]],
        namespace: str,
        reason: str,
    ) -> None:
        """
        드롭된 이벤트 정보를 WAL에 기록.

        Load Shedding으로 드롭된 이벤트의 최소 정보를 기록합니다.
        """
        try:
            _append_to_wal(
                {
                    "type": "dropped",
                    "trigger_type": trigger_type,
                    "namespace": namespace,
                    "reason": reason,
                    "effects_count": len(effects),
                    "dropped_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except Exception as e:
            logger.debug(f"[CascadeAudit] Dropped record save failed: {e}")

    # 하위 호환성
    _record_dropped_to_fallback = _record_dropped_to_wal

    def recover_from_local_wal(
        self,
        namespace: str = "global",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        로컬 WAL에서 Redis로 복구.

        Redis 장애 복구 후 로컬 WAL에 쌓인 이벤트를 Redis로 이관합니다.

        Args:
            namespace: 네임스페이스
            dry_run: True면 실제 복구 없이 대상만 확인

        Returns:
            복구 결과 통계
        """
        wal_path = Path(LOCAL_CASCADE_WAL_PATH)

        if not wal_path.exists():
            return {
                "status": "no_wal_data",
                "namespace": namespace,
                "recovered": 0,
                "failed": 0,
            }

        entries = []

        # WAL 파일에서 해당 네임스페이스 이벤트 읽기
        with open(wal_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("namespace") == namespace and entry.get("type") != "dropped":
                        entries.append(entry)
                except json.JSONDecodeError:
                    continue

        if dry_run:
            logger.info(f"[CascadeAudit] WAL recovery dry run: " f"found {len(entries)} entries, namespace={namespace}")
            return {
                "status": "dry_run",
                "namespace": namespace,
                "entries_to_recover": len(entries),
                "recovered": 0,
            }

        # Redis로 복구
        recovered = 0
        failed = 0

        for entry in entries:
            try:
                event = CascadeEvent.from_dict(entry)
                self._save_cascade_event(event)
                self._add_to_index(namespace, event.id)
                recovered += 1
            except Exception as e:
                logger.error(f"[CascadeAudit] Recovery failed: {e}")
                failed += 1

        # 복구 완료 후 해당 네임스페이스 엔트리 제거
        if recovered > 0 and failed == 0:
            self._remove_namespace_from_wal(namespace)

        logger.info(
            f"[CascadeAudit] WAL recovery completed: " f"recovered={recovered}, failed={failed}, namespace={namespace}"
        )

        return {
            "status": "completed",
            "namespace": namespace,
            "recovered": recovered,
            "failed": failed,
        }

    # 하위 호환성
    recover_from_local_fallback = recover_from_local_wal

    def _remove_namespace_from_wal(self, namespace: str) -> None:
        """WAL 파일에서 특정 네임스페이스 엔트리 제거."""
        wal_path = Path(LOCAL_CASCADE_WAL_PATH)

        if not wal_path.exists():
            return

        remaining = []

        with open(wal_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    if entry.get("namespace") != namespace:
                        remaining.append(line)
                except json.JSONDecodeError:
                    remaining.append(line)

        if remaining:
            with open(wal_path, "w", encoding="utf-8") as f:
                f.writelines(remaining)
        else:
            wal_path.unlink(missing_ok=True)

    # 하위 호환성
    _remove_namespace_from_fallback = _remove_namespace_from_wal

    def get_load_shedding_status(
        self,
        namespace: str = "global",
    ) -> dict[str, Any]:
        """
        Load Shedding 상태 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            Load Shedding 상태 정보
        """
        load_shedding = self._get_load_shedding()

        if not load_shedding:
            return {
                "enabled": False,
                "status": "DISABLED",
            }

        # 버퍼 상태 확인
        backend = self._get_backend()
        index_key = self.CASCADE_INDEX_KEY.format(namespace=namespace)
        buffer_size = len(get_index_ids(backend, index_key))

        status = load_shedding.get_status(
            buffer_size=buffer_size,
            buffer_capacity=self._max_index_size,
        )
        status["enabled"] = True
        status["namespace"] = namespace

        return status

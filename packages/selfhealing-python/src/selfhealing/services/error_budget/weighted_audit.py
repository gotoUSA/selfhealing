"""
Weighted Budget Audit Entry.

버짓 소진 시 적용된 가중치와 그 근거를 기록하는 무결성 로그 스키마입니다.
Hash Chain에 포함되어 "장애 은폐가 원천적으로 불가능한 시스템"을 구현합니다.

Features:
- 적용된 가중치 값 기록
- 근거가 된 Emergency ID 기록
- 도메인 가중치 정보 포함
- Hash Chain 통합

Usage:
    from selfhealing.services.error_budget.weighted_audit import (
        WeightedBudgetAuditEntry,
        WeightedAuditRecorder,
    )

    entry = WeightedBudgetAuditEntry(
        raw_consumption_minutes=1.0,
        weighted_consumption_minutes=5.0,
        level_multiplier=5.0,
        emergency_id="emg_123",
    )

    recorder = WeightedAuditRecorder()
    recorder.record(entry)

Reference:
    docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.6
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from selfhealing.core.timezone import now as utc_now

logger = structlog.get_logger()


# =============================================================================
# Weighted Budget Audit Entry
# =============================================================================


@dataclass
class WeightedBudgetAuditEntry:
    """
    가중치 적용된 버짓 소진 감사 로그.

    버짓 소진 시 적용된 가중치와 그 근거를 기록하여
    Hash Chain에 포함시킵니다.

    Features:
    - 적용된 가중치 값 기록
    - 근거가 된 Emergency ID 기록
    - 도메인 가중치 정보 포함
    - Hash Chain 통합

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.6
    """

    # 식별자
    audit_id: str = field(default_factory=lambda: f"wba_{uuid.uuid4().hex[:12]}")
    """감사 로그 ID."""

    # 시간 정보
    recorded_at: datetime = field(default_factory=utc_now)
    """기록 시각."""

    # 버짓 정보
    raw_consumption_minutes: float = 0.0
    """원시 소진량 (분)."""

    weighted_consumption_minutes: float = 0.0
    """가중치 적용된 소진량 (분)."""

    # 가중치 근거
    level_multiplier: float = 1.0
    """Emergency Level 기반 가중치."""

    domain_multiplier: float = 1.0
    """도메인 기반 가중치."""

    final_multiplier: float = 1.0
    """최종 적용 가중치."""

    # Emergency 근거
    emergency_id: str | None = None
    """관련 Emergency ID."""

    emergency_level: str | None = None
    """당시 Emergency Level."""

    # 도메인 정보
    crisis_domain: str | None = None
    """장애 발생 도메인."""

    error_domain: str | None = None
    """에러 발생 도메인."""

    hop_distance: int = 0
    """도메인 간 홉 거리."""

    # 메타데이터
    namespace: str | None = None
    """네임스페이스."""

    service_name: str | None = None
    """서비스 이름."""

    error_type: str | None = None
    """에러 유형."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "audit_id": self.audit_id,
            "recorded_at": self.recorded_at.isoformat(),
            "raw_consumption_minutes": self.raw_consumption_minutes,
            "weighted_consumption_minutes": self.weighted_consumption_minutes,
            "level_multiplier": self.level_multiplier,
            "domain_multiplier": self.domain_multiplier,
            "final_multiplier": self.final_multiplier,
            "emergency_id": self.emergency_id,
            "emergency_level": self.emergency_level,
            "crisis_domain": self.crisis_domain,
            "error_domain": self.error_domain,
            "hop_distance": self.hop_distance,
            "namespace": self.namespace,
            "service_name": self.service_name,
            "error_type": self.error_type,
        }

    def to_hash_chain_entry(self) -> dict[str, Any]:
        """
        Hash Chain 엔트리 변환.

        무결성 체인에 포함될 형식으로 변환합니다.
        """
        return {
            "type": "weighted_budget_consumption",
            "audit_id": self.audit_id,
            "timestamp": self.recorded_at.isoformat(),
            "consumption": {
                "raw": self.raw_consumption_minutes,
                "weighted": self.weighted_consumption_minutes,
            },
            "multipliers": {
                "level": self.level_multiplier,
                "domain": self.domain_multiplier,
                "final": self.final_multiplier,
            },
            "evidence": {
                "emergency_id": self.emergency_id,
                "emergency_level": self.emergency_level,
                "crisis_domain": self.crisis_domain,
                "error_domain": self.error_domain,
                "hop_distance": self.hop_distance,
            },
            "context": {
                "namespace": self.namespace,
                "service_name": self.service_name,
                "error_type": self.error_type,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeightedBudgetAuditEntry:
        """딕셔너리에서 생성."""
        recorded_at = data.get("recorded_at")
        if isinstance(recorded_at, str):
            recorded_at = datetime.fromisoformat(recorded_at)

        return cls(
            audit_id=data.get("audit_id", f"wba_{uuid.uuid4().hex[:12]}"),
            recorded_at=recorded_at or utc_now(),
            raw_consumption_minutes=data.get("raw_consumption_minutes", 0.0),
            weighted_consumption_minutes=data.get("weighted_consumption_minutes", 0.0),
            level_multiplier=data.get("level_multiplier", 1.0),
            domain_multiplier=data.get("domain_multiplier", 1.0),
            final_multiplier=data.get("final_multiplier", 1.0),
            emergency_id=data.get("emergency_id"),
            emergency_level=data.get("emergency_level"),
            crisis_domain=data.get("crisis_domain"),
            error_domain=data.get("error_domain"),
            hop_distance=data.get("hop_distance", 0),
            namespace=data.get("namespace"),
            service_name=data.get("service_name"),
            error_type=data.get("error_type"),
        )


# =============================================================================
# Weighted Audit Recorder
# =============================================================================


class WeightedAuditRecorder:
    """
    가중치 감사 로그 기록기.

    WeightedBudgetAuditEntry를 Hash Chain에 기록합니다.

    Reference:
        docs/self_healing/middleware_system/75_CRISIS_BUDGET_MULTIPLIER.md §8.6
    """

    def __init__(
        self,
        hash_chain_manager: Any | None = None,
        enable_hash_chain: bool = True,
    ):
        """
        WeightedAuditRecorder 초기화.

        Args:
            hash_chain_manager: Hash Chain 관리자 (None이면 lazy loading)
            enable_hash_chain: Hash Chain 기록 활성화 여부
        """
        self._hash_chain_manager = hash_chain_manager
        self._enable_hash_chain = enable_hash_chain
        self._entries: list[WeightedBudgetAuditEntry] = []

    def _get_hash_chain_manager(self) -> Any | None:
        """Hash Chain Manager 획득 (lazy loading)."""
        if self._hash_chain_manager is None:
            try:
                from selfhealing.services.hash_chain import get_hash_chain_manager

                self._hash_chain_manager = get_hash_chain_manager()
            except ImportError:
                logger.debug("weighted_audit.hash_chain_manager_available")
        return self._hash_chain_manager

    def record(self, entry: WeightedBudgetAuditEntry) -> None:
        """
        감사 로그 기록.

        Args:
            entry: 기록할 감사 로그
        """
        self._entries.append(entry)

        logger.debug(
            "weighted_audit.recorded",
            entry=entry.audit_id,
            raw_consumption_minutes=entry.raw_consumption_minutes,
            weighted_consumption_minutes=entry.weighted_consumption_minutes,
            final_multiplier=entry.final_multiplier,
        )

        # Hash Chain에 기록
        if self._enable_hash_chain:
            self._record_to_hash_chain(entry)

    def _record_to_hash_chain(self, entry: WeightedBudgetAuditEntry) -> None:
        """Hash Chain에 기록."""
        manager = self._get_hash_chain_manager()
        if manager:
            try:
                chain_entry = entry.to_hash_chain_entry()
                manager.add_entry(chain_entry)

                logger.debug(
                    "weighted_audit.added_hash_chain",
                    entry=entry.audit_id,
                )
            except Exception as e:
                logger.warning(
                    "weighted_audit.failed_add_hash_chain",
                    error=e,
                )

    def get_entries(
        self,
        limit: int = 100,
        namespace: str | None = None,
    ) -> list[WeightedBudgetAuditEntry]:
        """
        기록된 감사 로그 조회.

        Args:
            limit: 최대 조회 개수
            namespace: 네임스페이스 필터

        Returns:
            감사 로그 목록 (최신순)
        """
        entries = self._entries

        if namespace:
            entries = [e for e in entries if e.namespace == namespace]

        # 최신순 정렬
        entries = sorted(entries, key=lambda e: e.recorded_at, reverse=True)

        return entries[:limit]

    def get_total_consumption(
        self,
        namespace: str | None = None,
    ) -> dict[str, float]:
        """
        총 소진량 통계 조회.

        Args:
            namespace: 네임스페이스 필터

        Returns:
            raw_total, weighted_total, average_multiplier
        """
        entries = self._entries

        if namespace:
            entries = [e for e in entries if e.namespace == namespace]

        if not entries:
            return {
                "raw_total": 0.0,
                "weighted_total": 0.0,
                "average_multiplier": 1.0,
                "entry_count": 0,
            }

        raw_total = sum(e.raw_consumption_minutes for e in entries)
        weighted_total = sum(e.weighted_consumption_minutes for e in entries)
        avg_multiplier = weighted_total / raw_total if raw_total > 0 else 1.0

        return {
            "raw_total": raw_total,
            "weighted_total": weighted_total,
            "average_multiplier": avg_multiplier,
            "entry_count": len(entries),
        }


# =============================================================================
# Singleton
# =============================================================================

_weighted_audit_recorder: WeightedAuditRecorder | None = None


def get_weighted_audit_recorder() -> WeightedAuditRecorder:
    """WeightedAuditRecorder 싱글톤 반환."""
    global _weighted_audit_recorder
    if _weighted_audit_recorder is None:
        _weighted_audit_recorder = WeightedAuditRecorder()
    return _weighted_audit_recorder


def reset_weighted_audit_recorder() -> None:
    """싱글톤 리셋 (테스트용)."""
    global _weighted_audit_recorder
    _weighted_audit_recorder = None

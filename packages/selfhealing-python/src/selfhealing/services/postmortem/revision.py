"""
PostmortemRevisionManager - Postmortem 리비전(버전) 관리.

Postmortem 데이터의 수정 이력을 관리하여 변경 추적, 감사, 롤백 기능을 제공합니다.

Features:
- 리비전 생성/조회/비교
- 변경 사유 기록
- 이전 버전 조회 및 롤백
- Postmortem 봉인(Sealing) - 최종 확정 후 수정 불가
- HashChain 기반 무결성 해시

Storage:
- Redis: 메타데이터, 빠른 조회
- PostgreSQL JSONB: 스냅샷 영구 저장 (선택적)

Usage:
    manager = get_postmortem_revision_manager()
    revision = manager.create_revision(
        incident_id="AUTO-payment-20260128",
        new_data={"analysis": "Updated root cause"},
        changed_by="operator@example.com",
        change_reason="Root cause analysis updated",
        change_type=RevisionChangeType.ANALYSIS_UPDATE,
    )
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()


class RevisionChangeType(str, Enum):
    """리비전 변경 유형."""

    INITIAL = "initial"
    """최초 생성."""

    ANALYSIS_UPDATE = "analysis_update"
    """분석 내용 수정."""

    TIMELINE_CORRECTION = "timeline_correction"
    """타임라인 수정."""

    IMPROVEMENT_ADDED = "improvement_added"
    """개선사항 추가."""

    ANNOTATION = "annotation"
    """주석/코멘트 추가."""

    CORRECTION = "correction"
    """오류 수정."""

    SEALED = "sealed"
    """최종 봉인 (이후 수정 불가)."""

    ROLLBACK = "rollback"
    """이전 버전으로 롤백."""


@dataclass
class RevisionDiff:
    """두 리비전 간의 차이점."""

    added: dict[str, Any] = field(default_factory=dict)
    """추가된 필드와 값."""

    removed: dict[str, Any] = field(default_factory=dict)
    """제거된 필드와 값."""

    modified: dict[str, dict[str, Any]] = field(default_factory=dict)
    """수정된 필드 (old/new 값 포함)."""

    unchanged: list[str] = field(default_factory=list)
    """변경되지 않은 필드 목록."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "unchanged": self.unchanged,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RevisionDiff:
        """딕셔너리에서 생성."""
        return cls(
            added=data.get("added", {}),
            removed=data.get("removed", {}),
            modified=data.get("modified", {}),
            unchanged=data.get("unchanged", []),
        )

    @property
    def has_changes(self) -> bool:
        """변경사항이 있는지 확인."""
        return bool(self.added or self.removed or self.modified)


@dataclass
class PostmortemRevision:
    """Postmortem 리비전 데이터 모델."""

    revision_id: str
    """리비전 고유 ID."""

    incident_id: str
    """원본 Postmortem ID."""

    revision_number: int
    """리비전 순번 (1부터 시작)."""

    created_at: str
    """리비전 생성 시각 (ISO format)."""

    created_by: str
    """생성/수정자."""

    change_reason: str
    """변경 사유."""

    change_type: RevisionChangeType
    """변경 유형."""

    data_snapshot: dict[str, Any]
    """해당 시점 전체 데이터."""

    diff_from_previous: dict[str, Any] = field(default_factory=dict)
    """이전 버전과의 차이 (RevisionDiff.to_dict())."""

    integrity_hash: str = ""
    """무결성 해시."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리 변환."""
        return {
            "revision_id": self.revision_id,
            "incident_id": self.incident_id,
            "revision_number": self.revision_number,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "change_reason": self.change_reason,
            "change_type": self.change_type.value,
            "data_snapshot": self.data_snapshot,
            "diff_from_previous": self.diff_from_previous,
            "integrity_hash": self.integrity_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostmortemRevision:
        """딕셔너리에서 생성."""
        change_type_value = data.get("change_type", "initial")
        if isinstance(change_type_value, RevisionChangeType):
            change_type = change_type_value
        else:
            change_type = RevisionChangeType(change_type_value)

        return cls(
            revision_id=data.get("revision_id", ""),
            incident_id=data.get("incident_id", ""),
            revision_number=data.get("revision_number", 0),
            created_at=data.get("created_at", ""),
            created_by=data.get("created_by", ""),
            change_reason=data.get("change_reason", ""),
            change_type=change_type,
            data_snapshot=data.get("data_snapshot", {}),
            diff_from_previous=data.get("diff_from_previous", {}),
            integrity_hash=data.get("integrity_hash", ""),
        )


def compute_diff(old_data: dict[str, Any], new_data: dict[str, Any]) -> RevisionDiff:
    """
    두 데이터 간의 차이점 계산.

    Args:
        old_data: 이전 데이터
        new_data: 새 데이터

    Returns:
        RevisionDiff 객체
    """
    added: dict[str, Any] = {}
    removed: dict[str, Any] = {}
    modified: dict[str, dict[str, Any]] = {}
    unchanged: list[str] = []

    old_keys = set(old_data.keys())
    new_keys = set(new_data.keys())

    # 추가된 필드
    for key in new_keys - old_keys:
        added[key] = new_data[key]

    # 제거된 필드
    for key in old_keys - new_keys:
        removed[key] = old_data[key]

    # 공통 필드 비교
    for key in old_keys & new_keys:
        old_value = old_data[key]
        new_value = new_data[key]

        if old_value == new_value:
            unchanged.append(key)
        else:
            modified[key] = {"old": old_value, "new": new_value}

    return RevisionDiff(
        added=added,
        removed=removed,
        modified=modified,
        unchanged=unchanged,
    )


class PostmortemRevisionManager:
    """
    Postmortem 리비전 관리자.

    리비전 생성, 조회, 비교, 롤백, 봉인 기능을 제공합니다.

    Storage Strategy:
    - Redis 정상: Redis ZSET + HASH 기반 저장
    - Redis 장애: In-Memory Fallback (단일 프로세스)

    Usage:
        manager = get_postmortem_revision_manager()
        revision = manager.create_revision(...)
    """

    # Redis 키 패턴
    REVISIONS_ZSET_KEY = "selfhealing:pm:revisions:{incident_id}"
    REVISION_HASH_KEY = "selfhealing:pm:revision:{revision_id}"
    SEALED_KEY = "selfhealing:pm:sealed:{incident_id}"
    LATEST_DATA_KEY = "selfhealing:pm:latest:{incident_id}"

    def __init__(
        self,
        redis_client=None,
        hash_chain_state_file: Path | None = None,
        versioning_enabled: bool = True,
        max_revisions: int = 50,
        auto_seal_days: int = 30,
    ):
        """
        Initialize PostmortemRevisionManager.

        Args:
            redis_client: Redis 클라이언트 (없으면 인메모리 폴백)
            hash_chain_state_file: HashChain 상태 파일 경로
            versioning_enabled: 버전 관리 활성화 여부
            max_revisions: 최대 리비전 수
            auto_seal_days: 자동 봉인 일수 (0=비활성화)
        """
        self._redis_client = redis_client
        self._hash_chain_manager = None
        self._hash_chain_state_file = hash_chain_state_file
        self._versioning_enabled = versioning_enabled
        self._max_revisions = max_revisions
        self._auto_seal_days = auto_seal_days

        # In-memory fallback storage
        self._memory_revisions: dict[str, list[PostmortemRevision]] = {}
        self._memory_sealed: dict[str, bool] = {}
        self._memory_latest_data: dict[str, dict[str, Any]] = {}

    def _get_hash_chain_manager(self):
        """HashChainManager lazy 로드."""
        if self._hash_chain_manager is None:
            from selfhealing.audit.integrity.local_manager import HashChainManager

            if self._hash_chain_state_file:
                self._hash_chain_manager = HashChainManager(state_file=self._hash_chain_state_file)
            else:
                try:
                    default_path = Path("./data/postmortem_revision_chain.json")
                    self._hash_chain_manager = HashChainManager(state_file=default_path)
                except Exception:
                    self._hash_chain_manager = HashChainManager()

        return self._hash_chain_manager

    def _use_redis(self) -> bool:
        """Redis 사용 가능 여부."""
        if self._redis_client is None:
            return False
        try:
            self._redis_client.ping()
            return True
        except Exception:
            return False

    def _generate_revision_id(self) -> str:
        """새 리비전 ID 생성."""
        return f"REV-{uuid.uuid4().hex[:12]}"

    def is_sealed(self, incident_id: str) -> bool:
        """
        Postmortem 봉인 상태 확인.

        Args:
            incident_id: Postmortem ID

        Returns:
            봉인 여부
        """
        if self._use_redis():
            key = self.SEALED_KEY.format(incident_id=incident_id)
            result = self._redis_client.get(key)
            return result == "true" or result == b"true"
        else:
            return self._memory_sealed.get(incident_id, False)

    def _get_latest_revision_number(self, incident_id: str) -> int:
        """최신 리비전 번호 조회."""
        if self._use_redis():
            key = self.REVISIONS_ZSET_KEY.format(incident_id=incident_id)
            # ZSET에서 가장 높은 score (revision_number) 조회
            result = self._redis_client.zrevrange(key, 0, 0, withscores=True)
            if result:
                return int(result[0][1])
            return 0
        else:
            revisions = self._memory_revisions.get(incident_id, [])
            if revisions:
                return max(r.revision_number for r in revisions)
            return 0

    def _get_previous_revision(self, incident_id: str) -> PostmortemRevision | None:
        """이전 리비전 조회."""
        latest_number = self._get_latest_revision_number(incident_id)
        if latest_number == 0:
            return None
        return self.get_revision(incident_id, latest_number)

    def _save_revision(self, revision: PostmortemRevision) -> None:
        """리비전 저장."""
        if self._use_redis():
            # ZSET에 리비전 ID 추가 (score = revision_number)
            zset_key = self.REVISIONS_ZSET_KEY.format(incident_id=revision.incident_id)
            self._redis_client.zadd(zset_key, {revision.revision_id: revision.revision_number})

            # HASH에 리비전 상세 저장
            hash_key = self.REVISION_HASH_KEY.format(revision_id=revision.revision_id)
            self._redis_client.set(hash_key, json.dumps(revision.to_dict()))

            # 최신 데이터 업데이트
            latest_key = self.LATEST_DATA_KEY.format(incident_id=revision.incident_id)
            self._redis_client.set(latest_key, json.dumps(revision.data_snapshot))

            # 최대 리비전 수 제한 적용
            self._enforce_max_revisions(revision.incident_id)
        else:
            if revision.incident_id not in self._memory_revisions:
                self._memory_revisions[revision.incident_id] = []
            self._memory_revisions[revision.incident_id].append(revision)
            self._memory_latest_data[revision.incident_id] = revision.data_snapshot

            # 최대 리비전 수 제한 적용
            self._enforce_max_revisions(revision.incident_id)

    def _enforce_max_revisions(self, incident_id: str) -> None:
        """최대 리비전 수 제한 적용."""
        if self._use_redis():
            zset_key = self.REVISIONS_ZSET_KEY.format(incident_id=incident_id)
            count = self._redis_client.zcard(zset_key)
            if count > self._max_revisions:
                # 가장 오래된 리비전 삭제
                to_remove = self._redis_client.zrange(zset_key, 0, count - self._max_revisions - 1)
                for revision_id in to_remove:
                    if isinstance(revision_id, bytes):
                        revision_id = revision_id.decode()
                    hash_key = self.REVISION_HASH_KEY.format(revision_id=revision_id)
                    self._redis_client.delete(hash_key)
                    self._redis_client.zrem(zset_key, revision_id)
        else:
            revisions = self._memory_revisions.get(incident_id, [])
            if len(revisions) > self._max_revisions:
                # 리비전 번호 순으로 정렬 후 초과분 삭제
                revisions.sort(key=lambda r: r.revision_number)
                self._memory_revisions[incident_id] = revisions[-self._max_revisions :]

    def create_revision(
        self,
        incident_id: str,
        new_data: dict[str, Any],
        changed_by: str,
        change_reason: str,
        change_type: RevisionChangeType = RevisionChangeType.ANALYSIS_UPDATE,
    ) -> PostmortemRevision:
        """
        새 리비전 생성.

        Args:
            incident_id: Postmortem ID
            new_data: 수정된 데이터
            changed_by: 수정자
            change_reason: 변경 사유
            change_type: 변경 유형

        Returns:
            생성된 PostmortemRevision

        Raises:
            ValueError: 봉인된 Postmortem 수정 시도 시
        """
        if not self._versioning_enabled:
            # 버전 관리 비활성화 시 최소 리비전만 생성
            return PostmortemRevision(
                revision_id=self._generate_revision_id(),
                incident_id=incident_id,
                revision_number=1,
                created_at=datetime.now(timezone.utc).isoformat(),
                created_by=changed_by,
                change_reason=change_reason,
                change_type=change_type,
                data_snapshot=new_data,
            )

        # 봉인 상태 확인
        if self.is_sealed(incident_id):
            raise ValueError(f"Postmortem '{incident_id}' is sealed. Cannot create new revision.")

        # 이전 리비전 조회
        prev_revision = self._get_previous_revision(incident_id)
        prev_data = prev_revision.data_snapshot if prev_revision else {}
        prev_number = prev_revision.revision_number if prev_revision else 0

        # diff 계산
        diff = compute_diff(prev_data, new_data)

        # 새 리비전 번호
        new_revision_number = prev_number + 1

        # 리비전 ID 생성
        revision_id = self._generate_revision_id()

        # 현재 시각
        created_at = datetime.now(timezone.utc).isoformat()

        # 리비전 데이터 생성 (무결성 해시 추가 전)
        revision = PostmortemRevision(
            revision_id=revision_id,
            incident_id=incident_id,
            revision_number=new_revision_number,
            created_at=created_at,
            created_by=changed_by,
            change_reason=change_reason,
            change_type=change_type,
            data_snapshot=new_data,
            diff_from_previous=diff.to_dict(),
        )

        # HashChain 무결성 해시 추가
        try:
            hash_chain = self._get_hash_chain_manager()
            revision_dict = revision.to_dict()
            hashed_entry = hash_chain.add_integrity(revision_dict)
            integrity_info = hashed_entry.get("integrity", {})
            revision.integrity_hash = integrity_info.get("current_hash", "")
        except Exception as e:
            logger.warning(
                "revision_manager.failed_add_integrity_hash",
                error=e,
            )

        # 저장
        self._save_revision(revision)

        logger.info(
            "revision_manager.created_revision",
            incident_id=incident_id,
            new_revision_number=new_revision_number,
            change_type=change_type.value,
        )

        return revision

    def get_revision(
        self,
        incident_id: str,
        revision_number: int,
    ) -> PostmortemRevision | None:
        """
        특정 리비전 조회.

        Args:
            incident_id: Postmortem ID
            revision_number: 리비전 번호

        Returns:
            PostmortemRevision 또는 None
        """
        if self._use_redis():
            zset_key = self.REVISIONS_ZSET_KEY.format(incident_id=incident_id)
            # score(revision_number)로 조회
            revision_ids = self._redis_client.zrangebyscore(zset_key, revision_number, revision_number)
            if not revision_ids:
                return None

            revision_id = revision_ids[0]
            if isinstance(revision_id, bytes):
                revision_id = revision_id.decode()

            hash_key = self.REVISION_HASH_KEY.format(revision_id=revision_id)
            data = self._redis_client.get(hash_key)
            if data:
                if isinstance(data, bytes):
                    data = data.decode()
                return PostmortemRevision.from_dict(json.loads(data))
            return None
        else:
            revisions = self._memory_revisions.get(incident_id, [])
            for rev in revisions:
                if rev.revision_number == revision_number:
                    return rev
            return None

    def get_all_revisions(self, incident_id: str) -> list[PostmortemRevision]:
        """
        모든 리비전 목록 조회.

        Args:
            incident_id: Postmortem ID

        Returns:
            리비전 목록 (revision_number 오름차순)
        """
        if self._use_redis():
            zset_key = self.REVISIONS_ZSET_KEY.format(incident_id=incident_id)
            # 모든 revision_id 조회
            revision_ids = self._redis_client.zrange(zset_key, 0, -1)

            revisions = []
            for revision_id in revision_ids:
                if isinstance(revision_id, bytes):
                    revision_id = revision_id.decode()
                hash_key = self.REVISION_HASH_KEY.format(revision_id=revision_id)
                data = self._redis_client.get(hash_key)
                if data:
                    if isinstance(data, bytes):
                        data = data.decode()
                    revisions.append(PostmortemRevision.from_dict(json.loads(data)))

            return sorted(revisions, key=lambda r: r.revision_number)
        else:
            revisions = self._memory_revisions.get(incident_id, [])
            return sorted(revisions, key=lambda r: r.revision_number)

    def get_latest_revision(self, incident_id: str) -> PostmortemRevision | None:
        """
        최신 리비전 조회.

        Args:
            incident_id: Postmortem ID

        Returns:
            최신 PostmortemRevision 또는 None
        """
        latest_number = self._get_latest_revision_number(incident_id)
        if latest_number == 0:
            return None
        return self.get_revision(incident_id, latest_number)

    def compare_revisions(
        self,
        incident_id: str,
        revision_a: int,
        revision_b: int,
    ) -> RevisionDiff:
        """
        두 리비전 비교.

        Args:
            incident_id: Postmortem ID
            revision_a: 비교 리비전 A (이전 버전)
            revision_b: 비교 리비전 B (새 버전)

        Returns:
            RevisionDiff 객체

        Raises:
            ValueError: 리비전을 찾을 수 없는 경우
        """
        rev_a = self.get_revision(incident_id, revision_a)
        rev_b = self.get_revision(incident_id, revision_b)

        if not rev_a:
            raise ValueError(f"Revision {revision_a} not found for {incident_id}")
        if not rev_b:
            raise ValueError(f"Revision {revision_b} not found for {incident_id}")

        return compute_diff(rev_a.data_snapshot, rev_b.data_snapshot)

    def rollback_to_revision(
        self,
        incident_id: str,
        target_revision_number: int,
        rolled_back_by: str,
        rollback_reason: str,
    ) -> PostmortemRevision:
        """
        특정 리비전으로 롤백.

        새 리비전을 생성하여 대상 리비전의 데이터를 복원합니다.

        Args:
            incident_id: Postmortem ID
            target_revision_number: 롤백 대상 리비전 번호
            rolled_back_by: 롤백 수행자
            rollback_reason: 롤백 사유

        Returns:
            생성된 롤백 리비전

        Raises:
            ValueError: 대상 리비전을 찾을 수 없거나 봉인된 경우
        """
        if self.is_sealed(incident_id):
            raise ValueError(f"Postmortem '{incident_id}' is sealed. Cannot rollback.")

        target_revision = self.get_revision(incident_id, target_revision_number)
        if not target_revision:
            raise ValueError(f"Revision {target_revision_number} not found for {incident_id}")

        # 롤백 리비전 생성
        return self.create_revision(
            incident_id=incident_id,
            new_data=target_revision.data_snapshot,
            changed_by=rolled_back_by,
            change_reason=f"Rollback to revision {target_revision_number}: {rollback_reason}",
            change_type=RevisionChangeType.ROLLBACK,
        )

    def seal_postmortem(
        self,
        incident_id: str,
        sealed_by: str,
        seal_reason: str = "Analysis completed",
    ) -> PostmortemRevision:
        """
        Postmortem 봉인.

        봉인 후에는 더 이상 수정할 수 없습니다.

        Args:
            incident_id: Postmortem ID
            sealed_by: 봉인 수행자
            seal_reason: 봉인 사유

        Returns:
            봉인 리비전

        Raises:
            ValueError: 이미 봉인된 경우 또는 리비전이 없는 경우
        """
        if self.is_sealed(incident_id):
            raise ValueError(f"Postmortem '{incident_id}' is already sealed.")

        latest_revision = self.get_latest_revision(incident_id)
        if not latest_revision:
            raise ValueError(f"No revisions found for {incident_id}")

        # 봉인 리비전 생성
        seal_revision = self.create_revision(
            incident_id=incident_id,
            new_data=latest_revision.data_snapshot,
            changed_by=sealed_by,
            change_reason=seal_reason,
            change_type=RevisionChangeType.SEALED,
        )

        # 봉인 상태 설정
        if self._use_redis():
            key = self.SEALED_KEY.format(incident_id=incident_id)
            self._redis_client.set(key, "true")
        else:
            self._memory_sealed[incident_id] = True

        logger.info(
            "revision_manager.postmortem_sealed",
            incident_id=incident_id,
            sealed_by=sealed_by,
        )

        return seal_revision

    def unseal_postmortem(
        self,
        incident_id: str,
        unsealed_by: str,
        unseal_reason: str,
        approval_chain: list[str] | None = None,
    ) -> bool:
        """
        Postmortem 봉인 해제 (관리자 전용).

        봉인 해제 후 새 리비전 체인이 시작됩니다.

        Args:
            incident_id: Postmortem ID
            unsealed_by: 봉인 해제 수행자
            unseal_reason: 봉인 해제 사유
            approval_chain: 승인 체인 (선택)

        Returns:
            성공 여부

        Raises:
            ValueError: 봉인되지 않은 경우
        """
        if not self.is_sealed(incident_id):
            raise ValueError(f"Postmortem '{incident_id}' is not sealed.")

        # 봉인 해제
        if self._use_redis():
            key = self.SEALED_KEY.format(incident_id=incident_id)
            self._redis_client.delete(key)
        else:
            self._memory_sealed[incident_id] = False

        logger.warning(
            "revision_manager.postmortem_unsealed",
            incident_id=incident_id,
            unsealed_by=unsealed_by,
            unseal_reason=unseal_reason,
            approval_chain=approval_chain,
        )

        return True

    def get_revision_summary(self, incident_id: str) -> dict[str, Any]:
        """
        리비전 요약 정보 조회.

        Args:
            incident_id: Postmortem ID

        Returns:
            리비전 요약 딕셔너리
        """
        revisions = self.get_all_revisions(incident_id)
        is_sealed = self.is_sealed(incident_id)
        latest = self.get_latest_revision(incident_id)

        return {
            "incident_id": incident_id,
            "total_count": len(revisions),
            "is_sealed": is_sealed,
            "latest_revision": latest.revision_number if latest else 0,
            "revisions": [
                {
                    "revision_number": r.revision_number,
                    "created_at": r.created_at,
                    "created_by": r.created_by,
                    "change_type": r.change_type.value,
                    "change_reason": r.change_reason,
                }
                for r in revisions
            ],
        }


# ==========================================================================
# Singleton 관리
# ==========================================================================
_postmortem_revision_manager: PostmortemRevisionManager | None = None


def get_postmortem_revision_manager(
    redis_client=None,
    **kwargs,
) -> PostmortemRevisionManager:
    """
    Get singleton PostmortemRevisionManager instance.

    Args:
        redis_client: Redis 클라이언트 (선택)
        **kwargs: 추가 설정

    Returns:
        PostmortemRevisionManager 인스턴스
    """
    global _postmortem_revision_manager

    if _postmortem_revision_manager is None:
        # 설정에서 값 로드
        try:
            from selfhealing.settings.postmortem import get_postmortem_settings

            settings = get_postmortem_settings()
            versioning_enabled = getattr(settings, "versioning_enabled", True)
            max_revisions = getattr(settings, "max_revisions", 50)
            auto_seal_days = getattr(settings, "auto_seal_days", 30)
        except Exception:
            versioning_enabled = True
            max_revisions = 50
            auto_seal_days = 30

        _postmortem_revision_manager = PostmortemRevisionManager(
            redis_client=redis_client,
            versioning_enabled=kwargs.get("versioning_enabled", versioning_enabled),
            max_revisions=kwargs.get("max_revisions", max_revisions),
            auto_seal_days=kwargs.get("auto_seal_days", auto_seal_days),
            hash_chain_state_file=kwargs.get("hash_chain_state_file"),
        )

    return _postmortem_revision_manager


def reset_postmortem_revision_manager() -> None:
    """Reset singleton (for testing)."""
    global _postmortem_revision_manager
    _postmortem_revision_manager = None


# ==========================================================================
# Migration Utility
# ==========================================================================


def migrate_existing_postmortems(
    manager: PostmortemRevisionManager | None = None,
    batch_size: int = 100,
) -> dict[str, int]:
    """
    기존 Postmortem 데이터에 초기 리비전 생성.

    이미 리비전이 존재하는 Postmortem은 건너뜁니다.

    Args:
        manager: PostmortemRevisionManager 인스턴스 (없으면 싱글턴 사용)
        batch_size: 한 번에 처리할 Postmortem 수

    Returns:
        마이그레이션 결과 통계:
        - total: 전체 Postmortem 수
        - migrated: 마이그레이션된 수
        - skipped: 건너뛴 수 (이미 리비전 존재)
        - failed: 실패 수
    """
    if manager is None:
        manager = get_postmortem_revision_manager()

    result = {"total": 0, "migrated": 0, "skipped": 0, "failed": 0}

    try:
        from selfhealing.services.postmortem.store import get_healing_incidents
    except ImportError:
        logger.warning("migration")
        return result

    offset = 0
    while True:
        incidents = get_healing_incidents(
            limit=batch_size,
            offset=offset,
            use_db=True,
        )

        if not incidents:
            break

        for incident in incidents:
            result["total"] += 1
            incident_id = incident.get("incident_id")

            if not incident_id:
                logger.warning("migration")
                result["failed"] += 1
                continue

            # 이미 리비전 존재 여부 확인
            existing = manager.get_latest_revision(incident_id)
            if existing is not None:
                result["skipped"] += 1
                continue

            # 초기 리비전 생성
            try:
                manager.create_revision(
                    incident_id=incident_id,
                    new_data=incident,
                    changed_by="system:migration",
                    change_reason="Initial revision created during migration",
                    change_type=RevisionChangeType.INITIAL,
                )
                result["migrated"] += 1
                logger.debug(
                    "migration.created_initial_revision",
                    incident_id=incident_id,
                )
            except Exception as e:
                logger.warning(
                    "migration.failed_migrate",
                    incident_id=incident_id,
                    error=e,
                )
                result["failed"] += 1

        offset += batch_size

    logger.info(
        "migration.complete",
        result=result['total'],
        result_1=result['migrated'],
        result_2=result['skipped'],
        result_3=result['failed'],
    )

    return result


__all__ = [
    "RevisionChangeType",
    "RevisionDiff",
    "PostmortemRevision",
    "PostmortemRevisionManager",
    "compute_diff",
    "get_postmortem_revision_manager",
    "reset_postmortem_revision_manager",
    "migrate_existing_postmortems",
]

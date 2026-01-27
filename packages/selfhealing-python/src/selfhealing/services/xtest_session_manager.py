"""
X-Test Session Manager

X-Test 세션 메타데이터를 Redis에 저장하고 관리합니다.
세션 생성, 조회, 만료 감지, 아티팩트 등록 기능을 제공합니다.

Redis 키 구조:
- xtest:session:{session_id} - Hash: 세션 메타데이터
- xtest:session:active - Set: 활성 세션 ID 목록
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class XTestSessionMetadata:
    """X-Test 세션 메타데이터."""

    session_id: str
    created_at: datetime
    ttl_hours: int
    user: str
    components: List[str] = field(default_factory=list)
    artifacts: List[str] = field(default_factory=list)

    @property
    def expires_at(self) -> datetime:
        """세션 만료 시간."""
        return self.created_at + timedelta(hours=self.ttl_hours)

    @property
    def is_expired(self) -> bool:
        """세션 만료 여부."""
        return datetime.now(timezone.utc) > self.expires_at

    def to_dict(self) -> Dict[str, Any]:
        """직렬화용 딕셔너리 변환."""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at.isoformat(),
            "ttl_hours": self.ttl_hours,
            "user": self.user,
            "components": self.components,
            "artifacts": self.artifacts,
            "expires_at": self.expires_at.isoformat(),
            "is_expired": self.is_expired,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "XTestSessionMetadata":
        """딕셔너리에서 인스턴스 생성."""
        created_at = data.get("created_at")
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        elif created_at is None:
            created_at = datetime.now(timezone.utc)

        return cls(
            session_id=data.get("session_id", ""),
            created_at=created_at,
            ttl_hours=int(data.get("ttl_hours", 4)),
            user=data.get("user", "anonymous"),
            components=data.get("components", []),
            artifacts=data.get("artifacts", []),
        )


class XTestSessionManager:
    """
    X-Test 세션 메타데이터 관리자.

    Redis를 사용하여 X-Test 세션의 생성, 조회, 만료 감지, 아티팩트 등록을 처리합니다.
    """

    def __init__(self, redis_client: Optional[Any] = None):
        """
        Args:
            redis_client: Redis 클라이언트 (None이면 자동 생성)
        """
        self._redis = redis_client
        self._settings = None

    @property
    def settings(self):
        """설정 lazy loading."""
        if self._settings is None:
            from selfhealing.settings.xtest_cleanup import get_xtest_cleanup_settings
            self._settings = get_xtest_cleanup_settings()
        return self._settings

    @property
    def redis(self):
        """Redis 클라이언트 lazy loading."""
        if self._redis is None:
            try:
                from selfhealing.adapters.redis import get_redis_client
                self._redis = get_redis_client()
            except ImportError:
                logger.warning("[XTestSession] Redis adapter not available")
                self._redis = None
        return self._redis

    def _get_session_key(self, session_id: str) -> str:
        """세션 Redis 키 생성."""
        return f"{self.settings.redis_session_prefix}{session_id}"

    def _get_active_sessions_key(self) -> str:
        """활성 세션 목록 Redis 키."""
        return self.settings.redis_active_sessions_key

    def create_session(
        self,
        session_id: str,
        user: str = "anonymous",
        ttl_hours: Optional[int] = None,
    ) -> XTestSessionMetadata:
        """
        새 X-Test 세션 생성.

        Args:
            session_id: 세션 식별자
            user: 생성 사용자
            ttl_hours: 세션 TTL (None이면 설정값 사용)

        Returns:
            생성된 세션 메타데이터
        """
        if ttl_hours is None:
            ttl_hours = self.settings.session_ttl_hours

        metadata = XTestSessionMetadata(
            session_id=session_id,
            created_at=datetime.now(timezone.utc),
            ttl_hours=ttl_hours,
            user=user,
            components=[],
            artifacts=[],
        )

        if self.redis:
            try:
                session_key = self._get_session_key(session_id)
                active_key = self._get_active_sessions_key()

                # 세션 메타데이터 저장 (Hash)
                self.redis.hset(
                    session_key,
                    mapping={
                        "created_at": metadata.created_at.isoformat(),
                        "ttl_hours": str(ttl_hours),
                        "user": user,
                        "components": json.dumps([]),
                        "artifacts": json.dumps([]),
                    },
                )
                # 세션 TTL 설정 (여유 시간 1시간 추가)
                self.redis.expire(session_key, (ttl_hours + 1) * 3600)

                # 활성 세션 목록에 추가
                self.redis.sadd(active_key, session_id)

                logger.info(
                    f"[XTestSession] Created session: {session_id} "
                    f"(user={user}, ttl={ttl_hours}h)"
                )

            except Exception as e:
                logger.error(f"[XTestSession] Failed to create session: {e}")

        return metadata

    def get_session(self, session_id: str) -> Optional[XTestSessionMetadata]:
        """
        세션 메타데이터 조회.

        Args:
            session_id: 세션 식별자

        Returns:
            세션 메타데이터 (없으면 None)
        """
        if not self.redis:
            return None

        try:
            session_key = self._get_session_key(session_id)
            data = self.redis.hgetall(session_key)

            if not data:
                return None

            # Redis에서 가져온 bytes를 문자열로 변환
            str_data = {}
            for k, v in data.items():
                key = k.decode() if isinstance(k, bytes) else k
                val = v.decode() if isinstance(v, bytes) else v
                str_data[key] = val

            return XTestSessionMetadata(
                session_id=session_id,
                created_at=datetime.fromisoformat(str_data.get("created_at", "")),
                ttl_hours=int(str_data.get("ttl_hours", 4)),
                user=str_data.get("user", "anonymous"),
                components=json.loads(str_data.get("components", "[]")),
                artifacts=json.loads(str_data.get("artifacts", "[]")),
            )

        except Exception as e:
            logger.error(f"[XTestSession] Failed to get session {session_id}: {e}")
            return None

    def update_session(
        self,
        session_id: str,
        components: Optional[List[str]] = None,
        artifacts: Optional[List[str]] = None,
    ) -> bool:
        """
        세션 메타데이터 업데이트.

        Args:
            session_id: 세션 식별자
            components: 업데이트할 컴포넌트 목록
            artifacts: 업데이트할 아티팩트 목록

        Returns:
            성공 여부
        """
        if not self.redis:
            return False

        try:
            session_key = self._get_session_key(session_id)

            if not self.redis.exists(session_key):
                return False

            updates = {}
            if components is not None:
                updates["components"] = json.dumps(components)
            if artifacts is not None:
                updates["artifacts"] = json.dumps(artifacts)

            if updates:
                self.redis.hset(session_key, mapping=updates)

            return True

        except Exception as e:
            logger.error(f"[XTestSession] Failed to update session {session_id}: {e}")
            return False

    def register_artifact(
        self,
        session_id: str,
        artifact_id: str,
        component: str,
    ) -> bool:
        """
        세션에 아티팩트 등록.

        Args:
            session_id: 세션 식별자
            artifact_id: 아티팩트 ID
            component: 생성한 컴포넌트

        Returns:
            성공 여부
        """
        session = self.get_session(session_id)
        if not session:
            # 세션이 없으면 자동 생성
            session = self.create_session(session_id)

        # 아티팩트 추가
        artifacts = list(set(session.artifacts + [artifact_id]))
        components = list(set(session.components + [component]))

        return self.update_session(
            session_id,
            components=components,
            artifacts=artifacts,
        )

    def get_active_sessions(self) -> List[str]:
        """
        활성 세션 ID 목록 조회.

        Returns:
            활성 세션 ID 목록
        """
        if not self.redis:
            return []

        try:
            active_key = self._get_active_sessions_key()
            session_ids = self.redis.smembers(active_key)
            return [
                sid.decode() if isinstance(sid, bytes) else sid
                for sid in session_ids
            ]

        except Exception as e:
            logger.error(f"[XTestSession] Failed to get active sessions: {e}")
            return []

    def get_expired_sessions(self) -> List[XTestSessionMetadata]:
        """
        만료된 세션 목록 조회.

        Returns:
            만료된 세션 메타데이터 목록
        """
        expired_sessions = []
        active_ids = self.get_active_sessions()

        for session_id in active_ids:
            session = self.get_session(session_id)
            if session and session.is_expired:
                expired_sessions.append(session)

        logger.debug(
            f"[XTestSession] Found {len(expired_sessions)} expired sessions "
            f"out of {len(active_ids)} active"
        )

        return expired_sessions

    def delete_session(self, session_id: str) -> bool:
        """
        세션 삭제.

        Args:
            session_id: 세션 식별자

        Returns:
            성공 여부
        """
        if not self.redis:
            return False

        try:
            session_key = self._get_session_key(session_id)
            active_key = self._get_active_sessions_key()

            # 세션 메타데이터 삭제
            self.redis.delete(session_key)

            # 활성 목록에서 제거
            self.redis.srem(active_key, session_id)

            logger.info(f"[XTestSession] Deleted session: {session_id}")
            return True

        except Exception as e:
            logger.error(f"[XTestSession] Failed to delete session {session_id}: {e}")
            return False

    def get_sessions_count(self) -> int:
        """활성 세션 수 조회."""
        return len(self.get_active_sessions())


# =============================================================================
# Factory Function
# =============================================================================

_xtest_session_manager: Optional[XTestSessionManager] = None


def get_xtest_session_manager() -> XTestSessionManager:
    """
    XTestSessionManager 싱글톤 인스턴스 반환.

    Returns:
        XTestSessionManager 인스턴스
    """
    global _xtest_session_manager
    if _xtest_session_manager is None:
        _xtest_session_manager = XTestSessionManager()
    return _xtest_session_manager


def reset_xtest_session_manager() -> None:
    """세션 매니저 캐시 초기화 (테스트용)."""
    global _xtest_session_manager
    _xtest_session_manager = None


__all__ = [
    "XTestSessionMetadata",
    "XTestSessionManager",
    "get_xtest_session_manager",
    "reset_xtest_session_manager",
]

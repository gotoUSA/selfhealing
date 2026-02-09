"""
Configuration History & Rollback Service.

Redis에 설정 변경 이력을 저장하고 롤백 기능 제공.

Features:
- 변경 시 자동 버전 저장
- 최근 N개 버전 유지
- 특정 버전으로 롤백
- Redis 장애 시 Graceful Degradation

Usage:
    from selfhealing.services.config_history import get_config_history_service

    service = get_config_history_service()

    # 버전 저장
    version = service.save_version(
        config_type="circuit_breaker",
        values={"failure_threshold": 10},
        changed_by="admin",
        reason="Increase threshold for high load",
    )

    # 이력 조회
    history = service.get_history("circuit_breaker", limit=10)

    # 롤백
    rolled_back = service.rollback(
        config_type="circuit_breaker",
        target_version=1,
        rolled_back_by="admin",
    )

Audit:
- save_version: log_config_apply_audit(status="applied")
- rollback: log_rollback_audit(state="completed")

Reference:
    92_CONFIG_IMPLEMENTATION_GUIDE.md Week 4 [20] AuditSettings 참조.
"""

import hashlib
import json
import logging
import time
from typing import Any

from selfhealing.services.audit import log_config_apply_audit, log_rollback_audit

from .keys import (
    _get_config_current_key,
    _get_config_history_key,
    _get_config_version_key,
    _get_max_history_entries,
)
from .models import ConfigVersion

logger = logging.getLogger(__name__)


class ConfigHistoryService:
    """
    설정 변경 이력 관리 서비스.

    Features:
    - 변경 시 자동 버전 저장
    - 최근 N개 버전 유지
    - 특정 버전으로 롤백
    - Redis 장애 시 Graceful Degradation
    """

    # 지원하는 config_type 목록
    SUPPORTED_CONFIG_TYPES = [
        "circuit_breaker",
        "dlq",
        "retry",
        "sla",
        "slo",
        "rate_limit",
        "security",
        "idempotency",
        "notification",
        "forensic",
        "metrics",
        "error_budget",
        "drift_threshold",  # Drift 임계값 설정
        "emergency",  # Emergency Mode 설정
        "logging",  # Logging 설정
        "chaos",  # Chaos Engineering 설정
    ]

    def __init__(self):
        self._redis_client = None

    @property
    def redis_client(self):
        """Redis 클라이언트 획득 (Lazy loading)."""
        if self._redis_client is None:
            self._redis_client = self._get_redis_client()
        return self._redis_client

    def _get_redis_client(self):
        """Redis 클라이언트 획득."""
        try:
            from django.core.cache import caches

            cache = caches.get("default")
            if cache is None:
                logger.warning("[ConfigHistory] Default cache not configured")
                return None

            # django-redis 사용 시
            if hasattr(cache, "client"):
                client = cache.client.get_client()
                # 연결 테스트
                client.ping()
                return client

            # 다른 캐시 백엔드 사용 시
            logger.warning("[ConfigHistory] Cache backend does not support Redis client")
            return None

        except Exception as e:
            logger.warning(f"[ConfigHistory] Redis unavailable: {e}")
            return None

    def is_valid_config_type(self, config_type: str) -> bool:
        """유효한 config_type인지 확인."""
        return config_type in self.SUPPORTED_CONFIG_TYPES

    def save_version(
        self,
        config_type: str,
        values: dict[str, Any],
        changed_by: str,
        reason: str = "",
    ) -> ConfigVersion | None:
        """
        새 설정 버전 저장.

        Args:
            config_type: 설정 유형 (circuit_breaker, dlq, retry 등)
            values: 설정 값
            changed_by: 변경자
            reason: 변경 사유

        Returns:
            저장된 ConfigVersion 또는 None (Redis 장애 시)
        """
        if not self.is_valid_config_type(config_type):
            logger.error(f"[ConfigHistory] Invalid config_type: {config_type}")
            return None

        if not self.redis_client:
            logger.warning("[ConfigHistory] Redis unavailable - skip save")
            return None

        try:
            history_key = _get_config_history_key(config_type)
            version_key = _get_config_version_key(config_type)
            current_key = _get_config_current_key(config_type)

            # 새 버전 번호 (원자적 증가)
            version_num = self.redis_client.incr(version_key)

            # 해시 생성
            config_hash = self._compute_hash(values)

            version = ConfigVersion(
                version=version_num,
                timestamp=time.time(),
                config_type=config_type,
                values=values,
                changed_by=changed_by,
                reason=reason,
                hash=config_hash,
            )

            # 히스토리에 추가 (LPUSH + LTRIM)
            max_entries = _get_max_history_entries()
            pipe = self.redis_client.pipeline()
            pipe.lpush(history_key, json.dumps(version.to_dict()))
            pipe.ltrim(history_key, 0, max_entries - 1)
            pipe.set(current_key, json.dumps(version.to_dict()))
            pipe.execute()

            logger.info(
                f"[ConfigHistory] Saved: type={config_type}, " f"version={version_num}, by={changed_by}, reason={reason}"
            )

            # === Audit 기록: 설정 버전 저장 ===
            log_config_apply_audit(
                pending_id=None,
                config_key=config_type,
                old_value=None,
                new_value=values,
                status="applied",
                details={
                    "version": version_num,
                    "changed_by": changed_by,
                    "reason": reason,
                    "hash": config_hash,
                },
            )

            return version

        except Exception as e:
            logger.error(f"[ConfigHistory] Save failed: {e}", exc_info=True)
            return None

    def get_history(self, config_type: str, limit: int = 10) -> list[ConfigVersion]:
        """
        설정 변경 이력 조회.

        Args:
            config_type: 설정 유형
            limit: 조회할 버전 수

        Returns:
            ConfigVersion 목록 (최신순)
        """
        if not self.is_valid_config_type(config_type):
            logger.error(f"[ConfigHistory] Invalid config_type: {config_type}")
            return []

        if not self.redis_client:
            logger.warning("[ConfigHistory] Redis unavailable - returning empty history")
            return []

        try:
            history_key = _get_config_history_key(config_type)
            max_entries = _get_max_history_entries()
            entries = self.redis_client.lrange(history_key, 0, min(limit - 1, max_entries - 1))

            versions = []
            for entry in entries:
                try:
                    # bytes 또는 str 처리
                    if isinstance(entry, bytes):
                        entry = entry.decode("utf-8")
                    data = json.loads(entry)
                    versions.append(ConfigVersion.from_dict(data))
                except (json.JSONDecodeError, TypeError) as e:
                    logger.warning(f"[ConfigHistory] Failed to parse entry: {e}")
                    continue

            return versions

        except Exception as e:
            logger.error(f"[ConfigHistory] Get history failed: {e}", exc_info=True)
            return []

    def get_current_version(self, config_type: str) -> ConfigVersion | None:
        """현재 버전 조회."""
        if not self.is_valid_config_type(config_type):
            return None

        if not self.redis_client:
            return None

        try:
            current_key = _get_config_current_key(config_type)
            data = self.redis_client.get(current_key)

            if data:
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                return ConfigVersion.from_dict(json.loads(data))

            return None

        except Exception as e:
            logger.error(f"[ConfigHistory] Get current failed: {e}", exc_info=True)
            return None

    def get_version(self, config_type: str, version: int) -> ConfigVersion | None:
        """특정 버전 조회."""
        history = self.get_history(config_type, limit=_get_max_history_entries())

        for v in history:
            if v.version == version:
                return v

        return None

    def rollback(self, config_type: str, target_version: int, rolled_back_by: str) -> ConfigVersion | None:
        """
        특정 버전으로 롤백.

        Note: 이 메서드는 버전 이력만 저장합니다.
        실제 설정 적용은 호출자가 _apply_config_values()를 호출해야 합니다.

        Args:
            config_type: 설정 유형
            target_version: 롤백할 버전 번호
            rolled_back_by: 롤백 수행자

        Returns:
            새로 생성된 롤백 버전 정보 또는 None
        """
        target = self.get_version(config_type, target_version)

        if not target:
            logger.error(f"[ConfigHistory] Rollback failed: " f"version {target_version} not found for {config_type}")
            return None

        # 롤백도 새 버전으로 저장
        new_version = self.save_version(
            config_type=config_type,
            values=target.values,
            changed_by=rolled_back_by,
            reason=f"Rollback to version {target_version}",
        )

        if new_version:
            logger.info(
                f"[ConfigHistory] Rollback successful: {config_type} "
                f"v{target_version} -> v{new_version.version} by {rolled_back_by}"
            )

            # === Audit 기록: 설정 롤백 ===
            log_rollback_audit(
                request_id=f"config-rollback-{config_type}-{new_version.version}",
                stage_name=config_type,
                state="completed",
                triggered_by=rolled_back_by,
                reason=f"Rollback to version {target_version}",
                source_version=(str(new_version.version - 1) if new_version.version > 1 else None),
                target_version=str(target_version),
                affected_components=[config_type],
            )

        return new_version

    def compare_versions(self, config_type: str, version_a: int, version_b: int) -> dict[str, Any] | None:
        """
        두 버전 간 차이점 비교.

        Returns:
            차이점 딕셔너리 또는 None
        """
        v_a = self.get_version(config_type, version_a)
        v_b = self.get_version(config_type, version_b)

        if not v_a or not v_b:
            return None

        diff = {
            "version_a": version_a,
            "version_b": version_b,
            "config_type": config_type,
            "changes": {},
        }

        all_keys = set(v_a.values.keys()) | set(v_b.values.keys())

        for key in all_keys:
            val_a = v_a.values.get(key)
            val_b = v_b.values.get(key)

            if val_a != val_b:
                diff["changes"][key] = {
                    "from": val_a,
                    "to": val_b,
                }

        return diff

    def get_version_count(self, config_type: str) -> int:
        """저장된 버전 수 조회."""
        if not self.redis_client:
            return 0

        try:
            history_key = _get_config_history_key(config_type)
            return self.redis_client.llen(history_key)
        except Exception:
            return 0

    def clear_history(self, config_type: str) -> bool:
        """
        특정 config_type의 이력 삭제 (테스트용).

        WARNING: 프로덕션에서 사용 주의!
        """
        if not self.redis_client:
            return False

        try:
            history_key = _get_config_history_key(config_type)
            version_key = _get_config_version_key(config_type)
            current_key = _get_config_current_key(config_type)

            pipe = self.redis_client.pipeline()
            pipe.delete(history_key)
            pipe.delete(version_key)
            pipe.delete(current_key)
            pipe.execute()

            logger.warning(f"[ConfigHistory] Cleared history for {config_type}")
            return True

        except Exception as e:
            logger.error(f"[ConfigHistory] Clear failed: {e}")
            return False

    def _compute_hash(self, values: dict[str, Any]) -> str:
        """설정값 해시 계산."""
        sorted_str = json.dumps(values, sort_keys=True)
        return hashlib.sha256(sorted_str.encode()).hexdigest()[:16]


# 싱글톤 인스턴스
_config_history_service: ConfigHistoryService | None = None


def get_config_history_service() -> ConfigHistoryService:
    """ConfigHistoryService 싱글톤 반환."""
    global _config_history_service
    if _config_history_service is None:
        _config_history_service = ConfigHistoryService()
    return _config_history_service


def reset_config_history_service() -> None:
    """싱글톤 인스턴스 리셋 (테스트용)."""
    global _config_history_service
    _config_history_service = None

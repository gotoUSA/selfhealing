"""
Redis 기반 분산 Audit 버퍼.

기존 패턴 참조:
- CB Advanced Protection의 Redis-First + WAL 패턴
- RedisMetricSourceAdapter의 Write-Through 패턴
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import redis

logger = logging.getLogger(__name__)


def _get_audit_buffer_ttl() -> int:
    """AuditSettings에서 Redis 버퍼 TTL을 가져온다."""
    try:
        from selfhealing.settings.audit_settings import get_audit_settings

        return get_audit_settings().buffer_redis_ttl
    except Exception:
        return 86400  # 24시간 fallback


class AuditLogAdapterProtocol(Protocol):
    """Audit Log Adapter 프로토콜."""

    def log(self, entry: Any) -> None:
        """엔트리 기록."""
        ...

    def log_raw(self, entry: dict[str, Any]) -> None:
        """Raw 엔트리 기록."""
        ...


class RedisAuditBuffer:
    """
    분산 환경용 Redis Audit 버퍼.

    특징:
    - 모든 인스턴스가 동일한 Redis 버퍼 사용
    - LPUSH/RPOP으로 FIFO 순서 보장
    - Redis 장애 시 FileAuditLogAdapter로 자동 fallback
    - Redis 복구 시 로컬 WAL → Redis 동기화

    사용 예:
        redis_client = redis.from_url("redis://localhost:6379")
        file_fallback = FileAuditLogAdapter(log_dir="/var/log/audit")

        buffer = RedisAuditBuffer(
            redis_client=redis_client,
            fallback_adapter=file_fallback,
        )

        buffer.log(entry, domain="payment")
    """

    DEFAULT_KEY_PREFIX = "audit:buffer:"
    DEFAULT_TTL_SECONDS = 86400  # 하위 호환성용 레거시 상수
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(
        self,
        redis_client: redis.Redis,
        fallback_adapter: AuditLogAdapterProtocol | None = None,
        key_prefix: str | None = None,
        ttl_seconds: int | None = None,
        on_fallback: Callable[[Exception], None] | None = None,
    ):
        """
        RedisAuditBuffer 초기화.

        Args:
            redis_client: Redis 클라이언트
            fallback_adapter: 폴백 어댑터 (파일 등)
            key_prefix: Redis 키 프리픽스 (None = 기본값)
            ttl_seconds: TTL (초). None이면 Settings에서 가져옴.
            on_fallback: 폴백 발생 시 콜백
        """
        self._redis = redis_client
        self._fallback = fallback_adapter
        self._key_prefix = key_prefix if key_prefix is not None else self.DEFAULT_KEY_PREFIX
        self._ttl_seconds = ttl_seconds if ttl_seconds is not None else _get_audit_buffer_ttl()
        self._on_fallback = on_fallback

        # 상태 추적 (CB Advanced Protection 패턴)
        self._consecutive_failures = 0
        self._lock = threading.Lock()

        # 폴백 버퍼 (Redis 실패 시 임시 저장)
        self._fallback_buffer: list[dict[str, Any]] = []
        self._fallback_lock = threading.Lock()
        self._max_fallback = int(os.environ.get("SELFHEALING_REDIS_MAX_FALLBACK", "10000"))

        # 통계
        self._total_writes = 0
        self._total_fallbacks = 0
        self._total_flushes = 0
        self._total_batch_writes = 0
        self._total_batch_errors = 0

    def log(self, entry: dict[str, Any], domain: str = "default") -> bool:
        """
        Audit 엔트리 기록.

        Args:
            entry: Audit 엔트리 딕셔너리
            domain: 도메인 (키 분리용)

        Returns:
            True if Redis 성공, False if fallback 사용
        """
        key = f"{self._key_prefix}{domain}"

        try:
            payload = {
                "entry": entry,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "instance_id": self._get_instance_id(),
            }

            pipe = self._redis.pipeline()
            pipe.lpush(key, json.dumps(payload, default=str))
            pipe.expire(key, self._ttl_seconds)
            pipe.execute()

            # 성공 시 failure count 리셋
            with self._lock:
                self._consecutive_failures = 0
                self._total_writes += 1

            return True

        except Exception as e:
            logger.warning(f"[RedisAuditBuffer] Redis write failed: {e}")

            with self._lock:
                self._consecutive_failures += 1
                self._total_fallbacks += 1

            if self._on_fallback:
                try:
                    self._on_fallback(e)
                except Exception:
                    pass

            # Fallback to file
            if self._fallback:
                try:
                    if hasattr(self._fallback, "log_raw"):
                        self._fallback.log_raw(entry)
                    else:
                        self._fallback.log(entry)
                    logger.info("[RedisAuditBuffer] Used file fallback")
                except Exception as fallback_error:
                    logger.error(f"[RedisAuditBuffer] Fallback also failed: {fallback_error}")

            return False

    def log_batch(
        self,
        entries: list[dict[str, Any]],
        domain: str = "default",
    ) -> bool:
        """
        배치 로깅 - 단일 Redis pipeline으로 여러 이벤트 저장.

        개별 log() 호출 대비 Redis RTT를 N회 → 1회로 감소.

        Args:
            entries: 저장할 Audit 엔트리 딕셔너리 리스트
            domain: 도메인 (키 분리용)

        Returns:
            True if Redis 성공, False if 폴백 버퍼 사용
        """
        if not entries:
            return True

        key = f"{self._key_prefix}{domain}"
        timestamp = datetime.now(timezone.utc).isoformat()
        instance_id = self._get_instance_id()

        try:
            pipe = self._redis.pipeline(transaction=True)

            # 여러 항목을 한번에 LPUSH
            payloads = []
            for entry in entries:
                payload = {
                    "entry": entry,
                    "timestamp": timestamp,
                    "instance_id": instance_id,
                }
                payloads.append(json.dumps(payload, default=str))

            if payloads:
                pipe.lpush(key, *payloads)
                pipe.expire(key, self._ttl_seconds)
                pipe.execute()

            # 성공 시 통계 업데이트
            with self._lock:
                self._consecutive_failures = 0
                self._total_batch_writes += 1
                self._total_writes += len(entries)

            return True

        except Exception as e:
            logger.warning(f"[RedisAuditBuffer] Batch log failed: {e}, " f"entries_count={len(entries)}")

            with self._lock:
                self._consecutive_failures += 1
                self._total_batch_errors += 1

            # 폴백 버퍼에 저장
            self._store_in_fallback_buffer(entries, domain)

            return False

    def _store_in_fallback_buffer(
        self,
        entries: list[dict[str, Any]],
        domain: str = "default",
    ) -> None:
        """
        Redis 실패 시 메모리 폴백 버퍼에 임시 저장.

        Args:
            entries: 저장할 엔트리 리스트
            domain: 도메인
        """
        with self._fallback_lock:
            for entry in entries:
                self._fallback_buffer.append(
                    {
                        "entry": entry,
                        "domain": domain,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                )

            # 폴백 버퍼 크기 제한 (메모리 보호)
            if len(self._fallback_buffer) > self._max_fallback:
                overflow = len(self._fallback_buffer) - self._max_fallback
                self._fallback_buffer = self._fallback_buffer[overflow:]
                logger.warning(f"[RedisAuditBuffer] Fallback buffer overflow, " f"dropped {overflow} oldest entries")

    def retry_fallback_buffer(self) -> int:
        """
        폴백 버퍼의 엔트리를 Redis로 재시도.

        Returns:
            성공적으로 복구된 엔트리 수
        """
        with self._fallback_lock:
            if not self._fallback_buffer:
                return 0

            # 도메인별로 그룹핑
            entries_by_domain: dict[str, list[dict[str, Any]]] = {}
            for item in self._fallback_buffer:
                domain = item.get("domain", "default")
                if domain not in entries_by_domain:
                    entries_by_domain[domain] = []
                entries_by_domain[domain].append(item["entry"])

            # 각 도메인별로 배치 처리
            recovered = 0
            failed_items: list[dict[str, Any]] = []

            for domain, domain_entries in entries_by_domain.items():
                try:
                    if self.log_batch(domain_entries, domain):
                        recovered += len(domain_entries)
                    else:
                        # 실패 시 다시 폴백 버퍼에 보관
                        for entry in domain_entries:
                            failed_items.append(
                                {
                                    "entry": entry,
                                    "domain": domain,
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                }
                            )
                except Exception:
                    for entry in domain_entries:
                        failed_items.append(
                            {
                                "entry": entry,
                                "domain": domain,
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                            }
                        )

            # 실패한 항목만 유지
            self._fallback_buffer = failed_items

            return recovered

    def get_fallback_buffer_size(self) -> int:
        """폴백 버퍼의 현재 크기."""
        with self._fallback_lock:
            return len(self._fallback_buffer)

    def flush_to_external(
        self,
        target_adapter: AuditLogAdapterProtocol,
        batch_size: int = 100,
        domain: str | None = None,
    ) -> int:
        """
        Redis 버퍼를 외부 저장소로 플러시 (Sidecar/배치 작업용).

        Args:
            target_adapter: 대상 어댑터
            batch_size: 배치 크기
            domain: 특정 도메인만 처리 (None이면 전체)

        Returns:
            플러시된 엔트리 수
        """
        flushed = 0

        try:
            pattern = f"{self._key_prefix}{domain or '*'}"

            for key in self._redis.scan_iter(match=pattern):
                count = 0
                while count < batch_size:
                    item = self._redis.rpop(key)
                    if not item:
                        break

                    try:
                        payload = json.loads(item)
                        if hasattr(target_adapter, "log_raw"):
                            target_adapter.log_raw(payload["entry"])
                        else:
                            target_adapter.log(payload["entry"])
                        flushed += 1
                        count += 1
                    except Exception as e:
                        logger.error(f"[RedisAuditBuffer] Flush error: {e}")
                        # 실패 시 다시 넣기 (뒤에)
                        self._redis.rpush(key, item)
                        break

            with self._lock:
                self._total_flushes += flushed

        except Exception as e:
            logger.error(f"[RedisAuditBuffer] Flush scan error: {e}")

        return flushed

    def get_buffer_stats(self) -> dict[str, Any]:
        """버퍼 상태 조회."""
        stats = {
            "consecutive_failures": self._consecutive_failures,
            "total_writes": self._total_writes,
            "total_fallbacks": self._total_fallbacks,
            "total_flushes": self._total_flushes,
            "total_batch_writes": self._total_batch_writes,
            "total_batch_errors": self._total_batch_errors,
            "fallback_buffer_size": self.get_fallback_buffer_size(),
            "domains": {},
        }

        try:
            for key in self._redis.scan_iter(match=f"{self._key_prefix}*"):
                key_str = key.decode() if isinstance(key, bytes) else key
                domain = key_str.replace(self._key_prefix, "")
                stats["domains"][domain] = self._redis.llen(key)
        except Exception as e:
            logger.debug(f"[RedisAuditBuffer] Stats query failed: {e}")
            stats["error"] = str(e)

        return stats

    def get_pending_count(self, domain: str = "default") -> int:
        """특정 도메인의 대기 엔트리 수."""
        try:
            key = f"{self._key_prefix}{domain}"
            return self._redis.llen(key)
        except Exception:
            return -1

    def is_healthy(self) -> bool:
        """Redis 연결 상태 확인."""
        try:
            self._redis.ping()
            return True
        except Exception:
            return False

    def should_use_fallback(self) -> bool:
        """연속 실패로 폴백 사용 여부."""
        with self._lock:
            return self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES

    def _get_instance_id(self) -> str:
        """현재 인스턴스 식별자."""
        return os.environ.get("HOSTNAME", os.environ.get("INSTANCE_ID", "unknown"))

    def clear_domain(self, domain: str) -> int:
        """
        특정 도메인의 모든 엔트리 삭제 (테스트용).

        Returns:
            삭제된 엔트리 수
        """
        try:
            key = f"{self._key_prefix}{domain}"
            count = self._redis.llen(key)
            self._redis.delete(key)
            return count
        except Exception:
            return 0


# Factory 함수
def create_redis_audit_buffer(
    redis_url: str,
    fallback_log_dir: str | None = None,
    **kwargs,
) -> RedisAuditBuffer | None:
    """
    RedisAuditBuffer 팩토리.

    Args:
        redis_url: Redis URL (예: redis://localhost:6379)
        fallback_log_dir: 폴백 로그 디렉토리
        **kwargs: RedisAuditBuffer 추가 인자

    Returns:
        RedisAuditBuffer 또는 None (Redis 연결 실패 시)
    """
    try:
        import redis

        redis_client = redis.from_url(redis_url)
        redis_client.ping()  # 연결 확인

        fallback = None
        if fallback_log_dir:
            try:
                from selfhealing.adapters.audit.file_adapter import FileAuditLogAdapter

                fallback = FileAuditLogAdapter(log_dir=fallback_log_dir)
            except ImportError:
                logger.debug("[RedisAuditBuffer] FileAuditLogAdapter not available")

        return RedisAuditBuffer(
            redis_client=redis_client,
            fallback_adapter=fallback,
            on_fallback=lambda e: logger.warning(f"Redis audit fallback: {e}"),
            **kwargs,
        )

    except ImportError:
        logger.info("[RedisAuditBuffer] redis package not installed")
        return None
    except Exception as e:
        logger.info(f"[RedisAuditBuffer] Redis unavailable: {e}")
        return None

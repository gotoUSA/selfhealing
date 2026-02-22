"""
Redis 기반 분산 Audit 버퍼.

기존 패턴 참조:
- CB Advanced Protection의 Redis-First + WAL 패턴
- RedisMetricSourceAdapter의 Write-Through 패턴

기능:
- Processing Queue 패턴 적용
- ActiveKeySet O(1) 도메인 조회
- 청킹 구현
- LTRIM Safety Net
- Graceful Shutdown
"""

from __future__ import annotations

import atexit
import json
import os
import signal
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    import redis

logger = structlog.get_logger()


# 환경 변수 기본값
_MAX_PIPELINE_CHUNK = int(os.environ.get("SELFHEALING_MAX_PIPELINE_CHUNK", "1000"))
_BUFFER_WARNING_THRESHOLD = int(os.environ.get("SELFHEALING_BUFFER_WARNING", "10000"))
_BUFFER_CRITICAL_THRESHOLD = int(os.environ.get("SELFHEALING_BUFFER_CRITICAL", "50000"))
_SAFETY_LTRIM_THRESHOLD = int(os.environ.get("SELFHEALING_SAFETY_LTRIM", "100000"))


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
    - Processing Queue 패턴으로 데이터 손실 방지
    - ActiveKeySet으로 O(1) 도메인 조회

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
    ACTIVE_DOMAINS_SET = "audit:active_domains"  # ActiveKeySet

    def __init__(
        self,
        redis_client: redis.Redis,
        fallback_adapter: AuditLogAdapterProtocol | None = None,
        key_prefix: str | None = None,
        ttl_seconds: int | None = None,
        on_fallback: Callable[[Exception], None] | None = None,
        enable_graceful_shutdown: bool = True,
    ):
        """
        RedisAuditBuffer 초기화.

        Args:
            redis_client: Redis 클라이언트
            fallback_adapter: 폴백 어댑터 (파일 등)
            key_prefix: Redis 키 프리픽스 (None = 기본값)
            ttl_seconds: TTL (초). None이면 Settings에서 가져옴.
            on_fallback: 폴백 발생 시 콜백
            enable_graceful_shutdown: Graceful Shutdown 훅 등록 여부
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

        # 워커 식별자 (Processing Queue 패턴용)
        self._worker_id = f"{socket.gethostname()}-{os.getpid()}"

        # Lua 스크립트 (지연 초기화)
        self._lua_scripts = None

        # 통계
        self._total_writes = 0
        self._total_fallbacks = 0
        self._total_flushes = 0
        self._total_batch_writes = 0
        self._total_batch_errors = 0
        self._total_safety_ltrim = 0

        # Graceful Shutdown 훅 등록
        self._shutdown_registered = False
        if enable_graceful_shutdown:
            self._register_shutdown_hooks()

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
            logger.warning(
                "redis_audit_buffer.redis_write_failed",
                error=e,
            )

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
                    logger.info("redis_audit_buffer.used_file_fallback")
                except Exception as fallback_error:
                    logger.exception(
                        "redis_audit_buffer.fallback_also_failed",
                        fallback_error=fallback_error,
                    )

            return False

    def log_batch(
        self,
        entries: list[dict[str, Any]],
        domain: str = "default",
    ) -> bool:
        """
        배치 로깅 - 청킹 적용된 Redis pipeline으로 여러 이벤트 저장.

        개별 log() 호출 대비 Redis RTT를 N회 → 1회로 감소.
        MAX_PIPELINE_CHUNK 크기로 분할하여 메모리 보호.

        Args:
            entries: 저장할 Audit 엔트리 딕셔너리 리스트
            domain: 도메인 (키 분리용)

        Returns:
            True if Redis 성공, False if 폴백 버퍼 사용
        """
        if not entries:
            return True

        total = len(entries)
        success = True

        # 청킹 적용
        for chunk_start in range(0, total, _MAX_PIPELINE_CHUNK):
            chunk_end = min(chunk_start + _MAX_PIPELINE_CHUNK, total)
            chunk = entries[chunk_start:chunk_end]

            if not self._log_batch_chunk(chunk, domain):
                success = False
                # 실패한 청크는 폴백 버퍼에 저장
                self._store_in_fallback_buffer(chunk, domain)

        return success

    def _log_batch_chunk(
        self,
        entries: list[dict[str, Any]],
        domain: str = "default",
    ) -> bool:
        """
        단일 청크 처리 (최대 MAX_PIPELINE_CHUNK 개).

        Args:
            entries: 저장할 엔트리 리스트
            domain: 도메인

        Returns:
            성공 여부
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

                # ActiveKeySet에 도메인 추가
                pipe.sadd(self.ACTIVE_DOMAINS_SET, domain)
                pipe.expire(self.ACTIVE_DOMAINS_SET, 86400)

                pipe.execute()

            # 성공 시 통계 업데이트
            with self._lock:
                self._consecutive_failures = 0
                self._total_batch_writes += 1
                self._total_writes += len(entries)

            logger.debug(
                "redis_audit_buffer.batch_chunk_logged_entries",
                count=len(entries),
            )
            return True

        except Exception as e:
            logger.warning(
                f"[RedisAuditBuffer] Batch chunk failed: {e}",  # noqa: G004
                extra={"entries_count": len(entries), "domain": domain},
            )

            with self._lock:
                self._consecutive_failures += 1
                self._total_batch_errors += 1

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
                logger.warning(
                    "redis_audit_buffer.fallback_buffer_overflow_dropped",
                    overflow=overflow,
                )

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
                    # _log_batch_chunk를 직접 호출하여 폴백 버퍼 재진입 방지
                    # (log_batch는 실패 시 _store_in_fallback_buffer를 호출하여 데드락 발생)
                    if self._log_batch_chunk(domain_entries, domain):
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
                        logger.exception(
                            "redis_audit_buffer.flush_error",
                            error=e,
                        )
                        # 실패 시 다시 넣기 (뒤에)
                        self._redis.rpush(key, item)
                        break

            with self._lock:
                self._total_flushes += flushed

        except Exception as e:
            logger.exception(
                "redis_audit_buffer.flush_scan_error",
                error=e,
            )

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
            "total_safety_ltrim": self._total_safety_ltrim,
            "fallback_buffer_size": self.get_fallback_buffer_size(),
            "domains": {},
        }

        try:
            for domain in self._get_active_domains():
                key = f"{self._key_prefix}{domain}"
                size = self._redis.llen(key)
                stats["domains"][domain] = size

                # 임계치 체크 및 알림 발송
                self._check_buffer_threshold(domain, size)
        except Exception as e:
            logger.debug(
                "redis_audit_buffer.stats_query_failed",
                error=e,
            )
            stats["error"] = str(e)

        return stats

    def _check_buffer_threshold(self, domain: str, size: int) -> None:
        """버퍼 임계치 체크 및 알림 발송."""
        try:
            from selfhealing.metrics.audit_buffer_metrics import (
                audit_buffer_backpressure,
                audit_buffer_size,
            )

            # 메트릭 업데이트
            audit_buffer_size.labels(domain=domain).set(size)
            backpressure = min(1.0, size / max(1, _SAFETY_LTRIM_THRESHOLD))
            audit_buffer_backpressure.labels(domain=domain).set(backpressure)
        except ImportError:
            pass

        if size >= _BUFFER_CRITICAL_THRESHOLD:
            logger.exception(
                f"[RedisAuditBuffer] CRITICAL: Buffer overflow for {domain}",  # noqa: G004
                extra={"domain": domain, "size": size, "threshold": _BUFFER_CRITICAL_THRESHOLD},
            )
        elif size >= _BUFFER_WARNING_THRESHOLD:
            logger.warning(
                f"[RedisAuditBuffer] WARNING: Buffer high for {domain}",  # noqa: G004
                extra={"domain": domain, "size": size, "threshold": _BUFFER_WARNING_THRESHOLD},
            )

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

    def _get_lua_scripts(self):
        """Lua 스크립트 지연 초기화."""
        if self._lua_scripts is None:
            from selfhealing.audit.redis_batch_lua import AuditBatchLuaScripts

            self._lua_scripts = AuditBatchLuaScripts(self._redis)
        return self._lua_scripts

    def _get_active_domains(self) -> list[str]:
        """
        활성 도메인 조회 (O(1) 복잡도).

        ActiveKeySet을 사용하여 빠른 조회.
        """
        try:
            domains = self._redis.smembers(self.ACTIVE_DOMAINS_SET)

            # 빈 도메인 정리
            empty_domains = []
            result_domains = []

            for domain in domains:
                domain_str = domain.decode() if isinstance(domain, bytes) else domain
                key = f"{self._key_prefix}{domain_str}"

                if self._redis.llen(key) == 0:
                    empty_domains.append(domain_str)
                else:
                    result_domains.append(domain_str)

            # 빈 도메인 SET에서 제거
            if empty_domains:
                self._redis.srem(self.ACTIVE_DOMAINS_SET, *empty_domains)

            return result_domains

        except Exception as e:
            logger.debug(
                "redis_audit_buffer.activekeyset_query_failed",
                error=e,
            )
            return self._get_active_domains_fallback()

    def _get_active_domains_fallback(self) -> list[str]:
        """scan_iter 기반 fallback (ActiveKeySet 사용 불가 시)."""
        domains = set()
        try:
            for key in self._redis.scan_iter(f"{self._key_prefix}*"):
                key_str = key.decode() if isinstance(key, bytes) else key
                domain = key_str.replace(self._key_prefix, "")
                domains.add(domain)
        except Exception:
            pass
        return list(domains)

    def _add_to_active_domains(self, domains: set[str]) -> None:
        """활성 도메인 SET에 도메인 추가."""
        if domains:
            try:
                pipe = self._redis.pipeline()
                pipe.sadd(self.ACTIVE_DOMAINS_SET, *domains)
                pipe.expire(self.ACTIVE_DOMAINS_SET, 86400)  # 24시간 TTL
                pipe.execute()
            except Exception as e:
                logger.debug(
                    "redis_audit_buffer.failed_update_active_domains",
                    error=e,
                )

    def apply_safety_ltrim(self) -> dict[str, int]:
        """
        버퍼가 과도하게 커지면 LTRIM 적용.

        Returns:
            도메인별 트림된 항목 수
        """
        trimmed = {}
        try:
            for domain in self._get_active_domains():
                key = f"{self._key_prefix}{domain}"
                size = self._redis.llen(key)

                if size > _SAFETY_LTRIM_THRESHOLD:
                    self._redis.ltrim(key, 0, _SAFETY_LTRIM_THRESHOLD - 1)
                    trimmed_count = size - _SAFETY_LTRIM_THRESHOLD

                    logger.warning(
                        f"[RedisAuditBuffer] Safety LTRIM: {domain}",  # noqa: G004
                        extra={
                            "domain": domain,
                            "original_size": size,
                            "trimmed_to": _SAFETY_LTRIM_THRESHOLD,
                            "dropped": trimmed_count,
                        },
                    )

                    trimmed[domain] = trimmed_count

                    with self._lock:
                        self._total_safety_ltrim += trimmed_count

                    # 메트릭 기록
                    try:
                        from selfhealing.metrics.audit_buffer_metrics import (
                            audit_buffer_dropped_total,
                        )

                        audit_buffer_dropped_total.labels(domain=domain).inc(trimmed_count)
                    except ImportError:
                        pass

        except Exception as e:
            logger.exception(
                "redis_audit_buffer.safety_ltrim_failed",
                error=e,
            )

        return trimmed

    def flush_to_external_safe(
        self,
        target_adapter: AuditLogAdapterProtocol,
        batch_size: int = 500,
        domain: str | None = None,
    ) -> int:
        """
        Processing Queue 패턴을 사용한 안전한 플러시.

        데이터 손실 없이 순서를 보존하며 외부 저장소로 전송.

        Args:
            target_adapter: 대상 어댑터
            batch_size: 배치 크기
            domain: 특정 도메인만 처리 (None이면 전체)

        Returns:
            플러시된 엔트리 수
        """
        total_flushed = 0
        lua_scripts = self._get_lua_scripts()

        domains_to_process = [domain] if domain else self._get_active_domains()

        for current_domain in domains_to_process:
            try:
                # 1. Buffer → Processing Queue 원자적 이동
                moved = lua_scripts.atomic_batch_move(
                    domain=current_domain,
                    batch_size=batch_size,
                    worker_id=self._worker_id,
                )

                if moved == 0:
                    continue

                # 2. Processing Queue에서 데이터 읽기
                processing_key = f"audit:processing:{current_domain}"
                items = self._redis.lrange(processing_key, 0, moved - 1)

                entries = []
                for item in items:
                    try:
                        item_str = item.decode() if isinstance(item, bytes) else item
                        payload = json.loads(item_str)
                        entries.append(payload.get("entry", payload))
                    except (json.JSONDecodeError, AttributeError):
                        entries.append(item)

                # 3. 외부 어댑터로 저장
                if hasattr(target_adapter, "log_batch"):
                    target_adapter.log_batch(entries)
                else:
                    for entry in entries:
                        if hasattr(target_adapter, "log_raw"):
                            target_adapter.log_raw(entry)
                        else:
                            target_adapter.log(entry)

                # 4. 성공 시 Processing Queue 정리
                lua_scripts.atomic_batch_complete(current_domain, moved)
                total_flushed += moved

                logger.debug(
                    "redis_audit_buffer.flushed_entries",
                    moved=moved,
                    current_domain=current_domain,
                )

            except Exception as e:
                logger.exception(
                    "redis_audit_buffer.flush_failed",
                    current_domain=current_domain,
                    error=e,
                )

                # 5. 실패 시 순서 보존하여 복원
                try:
                    restored = lua_scripts.atomic_batch_restore(current_domain)
                    logger.info(
                        "redis_audit_buffer.restored_items_buffer",
                        restored=restored,
                    )
                except Exception as restore_error:
                    logger.exception(
                        "redis_audit_buffer.restore_failed",
                        restore_error=restore_error,
                    )

        with self._lock:
            self._total_flushes += total_flushed

        return total_flushed

    def recover_orphaned_processing_queues(
        self,
        timeout_seconds: int = 300,
    ) -> int:
        """
        타임아웃된 고아 Processing Queue 복구.

        Args:
            timeout_seconds: 고아 판단 임계 시간 (기본 5분)

        Returns:
            복구된 총 항목 수
        """
        recovered_total = 0
        lua_scripts = self._get_lua_scripts()

        orphaned = lua_scripts.get_orphaned_processing_queues(timeout_seconds)

        for processing_key, worker_id, age in orphaned:
            logger.warning(
                f"[RedisAuditBuffer] Orphaned queue detected",  # noqa: G004
                extra={
                    "processing_key": processing_key,
                    "worker_id": worker_id,
                    "age_seconds": age,
                },
            )

            # audit:processing:{domain}에서 domain 추출
            try:
                domain = processing_key.split(":")[-1]
                restored = lua_scripts.atomic_batch_restore(domain)
                recovered_total += restored
                logger.info(
                    "redis_audit_buffer.recovered_items",
                    restored=restored,
                    domain=domain,
                )
            except Exception as e:
                logger.exception(
                    "watchdog.recovery_failed",
                    processing_key=processing_key,
                    error=e,
                )

        return recovered_total

    def _register_shutdown_hooks(self) -> None:
        """Graceful Shutdown 훅 등록."""
        if self._shutdown_registered:
            return

        try:
            atexit.register(self._graceful_shutdown)

            # Windows에서는 SIGTERM이 없을 수 있음
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, self._signal_handler)
            if hasattr(signal, "SIGINT"):
                signal.signal(signal.SIGINT, self._signal_handler)

            self._shutdown_registered = True
            logger.debug("redis_audit_buffer.shutdown_hooks_registered")
        except Exception as e:
            logger.debug(
                "redis_audit_buffer.register_shutdown_hooks",
                error=e,
            )

    def _signal_handler(self, signum: int, frame: Any) -> None:
        """시그널 핸들러."""
        logger.info(
            "redis_audit_buffer.received_signal",
            signum=signum,
        )
        self._graceful_shutdown()

    def _graceful_shutdown(self) -> None:
        """
        Graceful Shutdown 처리.

        메모리 fallback 버퍼의 데이터를 Redis로 저장 시도.
        """
        # atexit 시점에서 logging stream이 닫힐 수 있으므로
        # 로깅 실패 시 "--- Logging error ---" 출력을 억제
        logging.raiseExceptions = False

        try:
            # 메모리 버퍼 → Redis 저장
            with self._fallback_lock:
                if self._fallback_buffer:
                    entries = list(self._fallback_buffer)

                    # 도메인별로 그룹핑하여 배치 저장
                    by_domain: dict[str, list] = {}
                    for item in entries:
                        domain = item.get("domain", "default")
                        if domain not in by_domain:
                            by_domain[domain] = []
                        by_domain[domain].append(item["entry"])

                    for domain, domain_entries in by_domain.items():
                        try:
                            # _log_batch_chunk 직접 호출 (log_batch는 폴백 버퍼 재진입 유발)
                            self._log_batch_chunk(domain_entries, domain)
                        except Exception:
                            pass

                    self._fallback_buffer.clear()

        except Exception:
            pass

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

            # ActiveKeySet에서도 제거
            self._redis.srem(self.ACTIVE_DOMAINS_SET, domain)

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

                fallback_path = Path(fallback_log_dir) / "audit_fallback.jsonl"
                fallback = FileAuditLogAdapter(file_path=fallback_path)
            except ImportError:
                logger.debug("redis_audit_buffer.fileauditlogadapter_available")

        return RedisAuditBuffer(
            redis_client=redis_client,
            fallback_adapter=fallback,
            on_fallback=lambda e: logger.warning(f"Redis audit fallback: {e}"),  # noqa: G004
            **kwargs,
        )

    except ImportError:
        logger.info("redis_audit_buffer.redis_package_installed")
        return None
    except Exception as e:
        logger.info(
            "redis_audit_buffer.redis_unavailable",
            error=e,
        )
        return None

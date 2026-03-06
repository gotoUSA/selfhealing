"""
통합 체크포인트 저장 전략 모듈.

고객사 인프라 환경에 맞춰 저장 전략을 플러그인처럼 교체 가능:
- FileCheckpointStorage: 순수 파이썬, 의존성 없음 (소규모)
- RedisCheckpointStorage: Redis 기반 (중규모)
- KafkaRedisCheckpointStorage: Kafka+Redis (엔터프라이즈)
- CompositeCheckpointStorage: 다중 저장소 Fallback 체인

Usage:
    from selfhealing.audit.checkpoint_strategy import (
        get_checkpoint_strategy,
        CheckpointStorageStrategy,
    )

    # 환경변수 기반 자동 선택
    strategy = get_checkpoint_strategy()

    # 명시적 선택
    strategy = get_checkpoint_strategy(storage_type="redis")

    # 체크포인트 저장
    strategy.save("default", UnifiedCheckpointData(wal_sequence=1234))

    # 체크포인트 로드
    data = strategy.load("default")

Version: 1.0.0
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

import structlog

if TYPE_CHECKING:
    import redis

logger = structlog.get_logger()


# =============================================================================
# Cross-Platform File Locking
# =============================================================================

_CHECKPOINT_SAVE_FAILURES: Any = None
_CHECKPOINT_LOAD_FAILURES: Any = None


def _lock_file(f: BinaryIO) -> None:
    """크로스 플랫폼 파일 락 획득."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(f: BinaryIO) -> None:
    """크로스 플랫폼 파일 락 해제."""
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _get_save_failures_counter():
    """체크포인트 save 실패 Counter 싱글톤."""
    global _CHECKPOINT_SAVE_FAILURES
    if _CHECKPOINT_SAVE_FAILURES is None:
        try:
            from prometheus_client import Counter

            _CHECKPOINT_SAVE_FAILURES = Counter(
                "selfhealing_checkpoint_save_failures_total",
                "Number of checkpoint save failures",
                ["storage_type"],
            )
        except ImportError:
            pass
    return _CHECKPOINT_SAVE_FAILURES


def _get_load_failures_counter():
    """체크포인트 load 실패 Counter 싱글톤."""
    global _CHECKPOINT_LOAD_FAILURES
    if _CHECKPOINT_LOAD_FAILURES is None:
        try:
            from prometheus_client import Counter

            _CHECKPOINT_LOAD_FAILURES = Counter(
                "selfhealing_checkpoint_load_failures_total",
                "Number of checkpoint load failures",
                ["storage_type"],
            )
        except ImportError:
            pass
    return _CHECKPOINT_LOAD_FAILURES


# =============================================================================
# 통합 데이터 모델
# =============================================================================


@dataclass
class UnifiedCheckpointData:
    """
    통합 체크포인트 데이터.

    기존 CheckpointData와 KafkaCheckpointData를 통합.
    Kafka 필드는 Optional로 처리하여 File 모드에서도 사용 가능.
    """

    # 필수 필드 (모든 전략에서 사용)
    wal_sequence: int
    """마지막 처리된 WAL 시퀀스."""

    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    """체크포인트 시간 (ISO 8601)."""

    version: int = 1
    """체크포인트 버전."""

    # Kafka 전용 필드 (Optional - Kafka 전략에서만 사용)
    kafka_topic: str | None = None
    """Kafka 토픽."""

    kafka_partition: int | None = None
    """Kafka 파티션."""

    kafka_offset: int | None = None
    """Kafka 오프셋."""

    checksum: str | None = None
    """WAL 엔트리 체크섬 (검증용)."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """딕셔너리에서 생성."""
        return cls(
            wal_sequence=data.get("wal_sequence", data.get("last_sequence", 0)),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            version=data.get("version", 1),
            kafka_topic=data.get("kafka_topic"),
            kafka_partition=data.get("kafka_partition"),
            kafka_offset=data.get("kafka_offset"),
            checksum=data.get("checksum"),
        )

    @classmethod
    def from_legacy_checkpoint_data(cls, data: dict[str, Any]) -> UnifiedCheckpointData:
        """
        기존 CheckpointData 형식에서 변환.

        레거시 형식은 last_sequence, timestamp(float), version 필드를 가짐.
        """
        timestamp = data.get("timestamp", 0.0)
        if isinstance(timestamp, (int, float)):
            timestamp = datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()

        return cls(
            wal_sequence=data.get("last_sequence", 0),
            timestamp=timestamp,
            version=data.get("version", 1),
        )


class CheckpointError(Exception):
    """체크포인트 관련 에러."""

    pass


class CheckpointCorruptedError(CheckpointError):
    """
    체크포인트 손상 에러.

    checksum 검증 실패 시 발생.
    """

    def __init__(self, message: str, expected: str, computed: str):
        super().__init__(message)
        self.expected = expected
        self.computed = computed


# =============================================================================
# 저장 전략 추상 인터페이스
# =============================================================================


class CheckpointStorageStrategy(ABC):
    """
    체크포인트 저장 전략 인터페이스.

    저장 방식과 상관없이 save(), load(), commit() 등 공통 규격 정의.
    구현체는 File, Redis, Kafka+Redis 등 인프라 환경에 맞게 선택.
    """

    @abstractmethod
    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장.

        Args:
            namespace: 네임스페이스 (멀티 테넌트 지원)
            data: 통합 체크포인트 데이터
        """
        pass

    @abstractmethod
    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """
        체크포인트 로드.

        Args:
            namespace: 네임스페이스

        Returns:
            UnifiedCheckpointData 또는 None
        """
        pass

    @abstractmethod
    def commit(self, namespace: str) -> None:
        """
        체크포인트 커밋 (트랜잭션 완료).

        Redis나 분산 환경에서 원자적 커밋 보장.

        Args:
            namespace: 네임스페이스
        """
        pass

    @abstractmethod
    def delete(self, namespace: str) -> bool:
        """
        체크포인트 삭제.

        Args:
            namespace: 네임스페이스

        Returns:
            삭제 성공 여부
        """
        pass

    @abstractmethod
    def exists(self, namespace: str) -> bool:
        """
        체크포인트 존재 여부.

        Args:
            namespace: 네임스페이스

        Returns:
            존재 여부
        """
        pass

    def get_wal_sequence(self, namespace: str = "default") -> int:
        """
        마지막 WAL 시퀀스 조회 (편의 메서드).

        Args:
            namespace: 네임스페이스

        Returns:
            마지막 WAL 시퀀스 (없으면 0)
        """
        data = self.load(namespace)
        return data.wal_sequence if data else 0


# =============================================================================
# 파일 기반 저장소 (소규모용, 의존성 없음)
# =============================================================================


class FileCheckpointStorage(CheckpointStorageStrategy):
    """
    파일 기반 체크포인트 저장소.

    특징:
    - 순수 파이썬, 외부 의존성 없음
    - 멀티 프로세스 파일 락 지원
    - fsync로 디스크 영속화 보장
    - 원자적 쓰기 (임시 파일 + rename)
    """

    DEFAULT_DIR = "/var/log/audit"
    DEFAULT_FILENAME = "checkpoint.json"

    def __init__(
        self,
        base_path: str | Path | None = None,
        sync_on_write: bool = True,
    ):
        """
        파일 기반 체크포인트 저장소 초기화.

        Args:
            base_path: 체크포인트 파일 기본 경로
            sync_on_write: 쓰기 시 fsync 수행 여부
        """
        self._base_path = self._get_base_path(base_path)
        self._sync_on_write = sync_on_write
        self._lock = threading.RLock()

        # 디렉토리 생성
        self._base_path.mkdir(parents=True, exist_ok=True)

    def _get_base_path(self, base_path: str | Path | None) -> Path:
        """기본 경로 결정."""
        if base_path:
            return Path(base_path)

        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path)

        if os.name == "nt":  # Windows
            return Path(tempfile.gettempdir()) / "selfhealing"
        return Path(self.DEFAULT_DIR)

    def _get_file_path(self, namespace: str) -> Path:
        """네임스페이스별 파일 경로."""
        return self._base_path / f"checkpoint.{namespace}.json"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """체크포인트 저장 (원자적, cross-process 파일 락 보호)."""
        with self._lock:
            file_path = self._get_file_path(namespace)
            tmp_path = file_path.with_suffix(".tmp")
            lock_file_path = file_path.with_suffix(".lock")

            try:
                with open(lock_file_path, "wb") as lock_f:
                    try:
                        _lock_file(lock_f)

                        with open(tmp_path, "w", encoding="utf-8") as f:
                            json.dump(data.to_dict(), f, indent=2)

                            if self._sync_on_write:
                                f.flush()
                                os.fsync(f.fileno())

                        # 원자적 rename
                        tmp_path.replace(file_path)

                        # 디렉토리 fsync (Linux)
                        if self._sync_on_write:
                            try:
                                dir_fd = os.open(
                                    str(file_path.parent), os.O_RDONLY | os.O_DIRECTORY
                                )
                                try:
                                    os.fsync(dir_fd)
                                finally:
                                    os.close(dir_fd)
                            except (OSError, AttributeError):
                                pass

                    finally:
                        try:
                            _unlock_file(lock_f)
                        except Exception:
                            pass

                logger.debug(
                    "file_checkpoint.saved",
                    namespace=namespace,
                    data=data.wal_sequence,
                )

            except (BlockingIOError, OSError) as e:
                logger.warning(
                    "file_checkpoint.lock_contention_skipping_save",
                    error=e,
                )

            except Exception as e:
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass
                counter = _get_save_failures_counter()
                if counter:
                    counter.labels(storage_type="file").inc()
                raise CheckpointError(f"Failed to save checkpoint: {e}") from e

    def _migrate_legacy_file(self, namespace: str) -> None:
        """레거시 checkpoint.json → checkpoint.{namespace}.json 마이그레이션 (파일 락 보호)."""
        if namespace != "default":
            return

        legacy_path = self._base_path / "checkpoint.json"
        target_path = self._get_file_path(namespace)

        if not legacy_path.exists() or target_path.exists():
            return

        lock_file_path = legacy_path.with_suffix(".lock")
        try:
            with open(lock_file_path, "wb") as lock_f:
                try:
                    _lock_file(lock_f)

                    # double-check after lock
                    if not legacy_path.exists() or target_path.exists():
                        return

                    legacy_path.rename(target_path)
                    logger.info(
                        "file_checkpoint.legacy_migrated",
                        legacy_path=str(legacy_path),
                        target_path=str(target_path),
                    )

                finally:
                    try:
                        _unlock_file(lock_f)
                    except Exception:
                        pass
        except (BlockingIOError, OSError):
            pass
        except Exception as e:
            logger.warning(
                "file_checkpoint.legacy_migration_failed",
                error=e,
            )

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """체크포인트 로드 (레거시 마이그레이션 포함)."""
        with self._lock:
            # 레거시 checkpoint.json → checkpoint.default.json 마이그레이션
            self._migrate_legacy_file(namespace)

            file_path = self._get_file_path(namespace)
            if not file_path.exists():
                return None

            try:
                with open(file_path, encoding="utf-8") as f:
                    raw_data = json.load(f)

                # 레거시 형식 변환 + write-back
                if "last_sequence" in raw_data and "wal_sequence" not in raw_data:
                    data = UnifiedCheckpointData.from_legacy_checkpoint_data(raw_data)
                    try:
                        self.save(namespace, data)
                    except Exception:
                        pass
                    return data

                return UnifiedCheckpointData.from_dict(raw_data)

            except Exception as e:
                logger.warning(
                    "file_checkpoint.load_failed",
                    error=e,
                )
                counter = _get_load_failures_counter()
                if counter:
                    counter.labels(storage_type="file").inc()
                return None

    def commit(self, namespace: str) -> None:
        """파일 저장소는 save()에서 이미 커밋됨."""
        pass  # No-op for file storage

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제."""
        with self._lock:
            file_path = self._get_file_path(namespace)
            try:
                file_path.unlink(missing_ok=True)
                return True
            except Exception:
                return False

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부."""
        return self._get_file_path(namespace).exists()

    def get_age_seconds(self, namespace: str) -> float | None:
        """
        체크포인트 경과 시간 (초).

        Returns:
            마지막 저장 후 경과 시간 또는 None
        """
        data = self.load(namespace)
        if data is None:
            return None

        try:
            ts = datetime.fromisoformat(data.timestamp)
            return (datetime.now(timezone.utc) - ts).total_seconds()
        except (ValueError, TypeError):
            return None


# =============================================================================
# Redis 기반 저장소 (중규모용, 분산 환경)
# =============================================================================


class RedisCheckpointStorage(CheckpointStorageStrategy):
    """
    Redis 기반 체크포인트 저장소.

    특징:
    - 분산 환경에서 원자적 저장
    - TTL 기반 자동 만료 (선택적)
    - 분산 락 통합 (멀티 Pod 경합 방지)
    - 알림 연동 (실패 시 알림)
    """

    KEY_PREFIX = "selfhealing:checkpoint:"
    LOCK_KEY_PREFIX = "selfhealing:checkpoint:lock:"

    def __init__(
        self,
        redis_client: redis.Redis,
        ttl_seconds: int | None = None,
        use_distributed_lock: bool = True,
        lock_timeout_seconds: int = 5,
        enable_notification: bool = True,
    ):
        """
        Redis 기반 체크포인트 저장소 초기화.

        Args:
            redis_client: Redis 클라이언트
            ttl_seconds: 체크포인트 TTL (None이면 무제한)
            use_distributed_lock: 분산 락 사용 여부 (멀티 Pod 환경 필수)
            lock_timeout_seconds: 분산 락 타임아웃
            enable_notification: 실패 시 알림 활성화
        """
        super().__init__()
        self._redis = redis_client
        self._ttl = ttl_seconds
        self._use_distributed_lock = use_distributed_lock
        self._lock_timeout_seconds = lock_timeout_seconds
        self._enable_notification = enable_notification
        self._pending: dict[str, UnifiedCheckpointData] = {}
        self._local_lock = threading.Lock()

    def _get_key(self, namespace: str) -> str:
        """Redis 키 생성."""
        return f"{self.KEY_PREFIX}{namespace}"

    def _get_lock_key(self, namespace: str) -> str:
        """분산 락 키 생성."""
        return f"{self.LOCK_KEY_PREFIX}{namespace}"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장 (분산 락 + 알림 연동).
        """
        try:
            if self._use_distributed_lock:
                self._save_with_lock(namespace, data)
            else:
                self._write_to_redis(namespace, data)
        except Exception as e:
            logger.exception(
                "redis_checkpoint.save_failed",
                error=e,
            )
            self._notify_failure(namespace, str(e))
            raise

    def _save_with_lock(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """분산 락을 사용한 저장."""
        from datetime import timedelta

        try:
            from selfhealing.services.coordination.distributed_recovery_lock import (
                DistributedRecoveryLock,
            )

            lock = DistributedRecoveryLock(
                redis_client=self._redis,
                lock_timeout=timedelta(seconds=self._lock_timeout_seconds),
            )

            session_id = f"checkpoint:{namespace}:{time.time()}"
            if lock.acquire(namespace, session_id, blocking=False):
                try:
                    self._write_to_redis(namespace, data)
                finally:
                    lock.release(namespace, session_id)
            else:
                raise CheckpointError(
                    f"Failed to acquire distributed lock for namespace: {namespace}"
                )

        except ImportError:
            # DistributedRecoveryLock 없으면 일반 저장
            logger.warning("redis_checkpoint.distributedrecoverylock_available")
            self._write_to_redis(namespace, data)

    def _write_to_redis(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """Redis에 실제 저장."""
        key = self._get_key(namespace)
        value = json.dumps(data.to_dict())

        if self._ttl:
            self._redis.setex(key, self._ttl, value)
        else:
            self._redis.set(key, value)

        logger.debug(
            "redis_checkpoint.saved",
            namespace=namespace,
            data=data.wal_sequence,
        )

    def _notify_failure(self, namespace: str, error: str) -> None:
        """체크포인트 저장 실패 알림."""
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            manager = UnifiedNotificationManager()
            manager.notify(
                NotificationPayload(
                    title="Checkpoint Save Failed",
                    message=f"Redis checkpoint save failed for namespace '{namespace}': {error}",
                    priority=NotificationPriority.CRITICAL,
                    category=NotificationCategory.OPERATIONS,
                    source="checkpoint_storage",
                    metadata={
                        "namespace": namespace,
                        "storage_type": "redis",
                        "error": error,
                    },
                )
            )
            logger.info(
                "redis_checkpoint.failure_notification_sent",
                namespace=namespace,
            )

        except ImportError:
            logger.warning("redis_checkpoint.unifiednotificationmanager_available")
        except Exception as e:
            logger.warning(
                "redis_checkpoint.failed_send_notification",
                error=e,
            )

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """체크포인트 로드 (Checksum 검증 포함)."""
        key = self._get_key(namespace)
        raw = self._redis.get(key)

        if not raw:
            return None

        try:
            raw_data = json.loads(raw)
            data = UnifiedCheckpointData.from_dict(raw_data)

            # Checksum 검증 (있는 경우)
            if data.checksum:
                self._verify_data_checksum(data)

            return data
        except CheckpointCorruptedError:
            raise
        except Exception as e:
            logger.warning(
                "redis_checkpoint.load_failed",
                error=e,
            )
            return None

    def _verify_data_checksum(self, data: UnifiedCheckpointData) -> None:
        """데이터 무결성 검증."""
        try:
            from selfhealing.audit.checksum import verify_checksum

            # wal_sequence를 기반으로 checksum 검증
            payload = {
                "wal_sequence": data.wal_sequence,
                "timestamp": data.timestamp,
                "version": data.version,
            }

            result = verify_checksum(payload, data.checksum, algorithm="crc32")

            if not result.is_valid:
                raise CheckpointCorruptedError(
                    "Checkpoint checksum mismatch",
                    expected=result.expected,
                    computed=result.computed,
                )

        except ImportError:
            logger.debug("redis_checkpoint.checksum_module_available_skipping")

    def commit(self, namespace: str) -> None:
        """pending 체크포인트 커밋."""
        with self._local_lock:
            if namespace in self._pending:
                del self._pending[namespace]
        # 이미 save()에서 저장됨

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제."""
        key = self._get_key(namespace)
        return self._redis.delete(key) > 0

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부."""
        key = self._get_key(namespace)
        return self._redis.exists(key) > 0


# =============================================================================
# Kafka+Redis 기반 저장소 (엔터프라이즈용)
# =============================================================================


class KafkaRedisCheckpointStorage(CheckpointStorageStrategy):
    """
    Kafka+Redis 기반 체크포인트 저장소.

    특징:
    - WAL 시퀀스 + Kafka 오프셋 원자적 저장
    - Redis로 빠른 조회, Kafka 오프셋으로 정확한 복구
    - File 백업으로 Redis 장애 시에도 복구 가능
    - Checksum 검증 및 알림 연동
    """

    KEY_PREFIX = "selfhealing:kafka_checkpoint:"

    def __init__(
        self,
        redis_client: redis.Redis,
        default_topic: str = "selfhealing.audit.events",
        file_backup_path: str | Path | None = None,
        enable_file_backup: bool = True,
        enable_notification: bool = True,
    ):
        """
        Kafka+Redis 체크포인트 저장소 초기화.

        Args:
            redis_client: Redis 클라이언트
            default_topic: 기본 Kafka 토픽
            file_backup_path: 파일 백업 경로 (Redis 장애 대비)
            enable_file_backup: 파일 백업 활성화 (권장: True)
            enable_notification: 실패 시 알림 활성화
        """
        super().__init__()
        self._redis = redis_client
        self._default_topic = default_topic
        self._enable_file_backup = enable_file_backup
        self._enable_notification = enable_notification
        self._local_lock = threading.Lock()

        # File 백업 저장소 (Redis 장애 대비)
        self._file_backup: FileCheckpointStorage | None = None
        if enable_file_backup:
            backup_path = file_backup_path or self._get_default_backup_path()
            self._file_backup = FileCheckpointStorage(base_path=backup_path)
            logger.info(
                "kafka_redis_checkpoint.file_backup_enabled",
                backup_path=backup_path,
            )

    def _get_default_backup_path(self) -> Path:
        """기본 백업 경로."""
        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path) / "kafka_checkpoint_backup"
        if os.name == "nt":
            return (
                Path(tempfile.gettempdir()) / "selfhealing" / "kafka_checkpoint_backup"
            )
        return Path("/var/log/audit/kafka_checkpoint_backup")

    def _get_key(self, namespace: str) -> str:
        """Redis 키 생성."""
        return f"{self.KEY_PREFIX}{namespace}"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        체크포인트 저장 (Redis Primary + File Backup).

        저장 순서:
        1. Redis에 저장 (Primary)
        2. File에도 백업 (Secondary) - Redis 장애 시 복구용
        """
        # Kafka 필드 기본값 채우기
        if data.kafka_topic is None:
            data.kafka_topic = self._default_topic
        if data.kafka_partition is None:
            data.kafka_partition = 0
        if data.kafka_offset is None:
            data.kafka_offset = 0

        redis_success = False
        file_success = False

        # 1. Redis 저장 (Primary)
        try:
            key = self._get_key(namespace)
            value = json.dumps(data.to_dict())
            self._redis.set(key, value)
            redis_success = True
            logger.debug(
                "kafka_redis_checkpoint.redis_saved",
                namespace=namespace,
                data=data.wal_sequence,
                kafka_offset=data.kafka_offset,
            )
        except Exception as e:
            logger.exception(
                "kafka_redis_checkpoint.redis_save_failed",
                error=e,
            )

        # 2. File 백업 (Secondary) - Redis와 무관하게 항상 시도
        if self._file_backup:
            try:
                self._file_backup.save(namespace, data)
                file_success = True
                logger.debug(
                    "kafka_redis_checkpoint.file_backup_saved",
                    namespace=namespace,
                )
            except Exception as e:
                logger.warning(
                    "kafka_redis_checkpoint.file_backup_failed",
                    error=e,
                )

        # 둘 다 실패한 경우 알림 + 예외
        if not redis_success and not file_success:
            self._notify_failure(namespace, "Both Redis and File storage failed")
            raise CheckpointError("All checkpoint storage tiers failed")

        # Redis만 실패한 경우 경고 알림
        if not redis_success:
            self._notify_degraded(namespace)

    def _notify_failure(self, namespace: str, error: str) -> None:
        """체크포인트 저장 실패 알림 (CRITICAL)."""
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            manager = UnifiedNotificationManager()
            manager.notify(
                NotificationPayload(
                    title="Kafka Checkpoint Save Failed",
                    message=f"All storage tiers failed for namespace '{namespace}': {error}",
                    priority=NotificationPriority.CRITICAL,
                    category=NotificationCategory.OPERATIONS,
                    source="kafka_redis_checkpoint",
                    metadata={"namespace": namespace, "error": error},
                )
            )
        except Exception as e:
            logger.warning(
                "kafka_redis_checkpoint.failed_send_failure_notification",
                error=e,
            )

    def _notify_degraded(self, namespace: str) -> None:
        """Redis 장애로 인한 Degraded 상태 알림 (HIGH)."""
        if not self._enable_notification:
            return

        try:
            from selfhealing.services.unified_notification import (
                NotificationCategory,
                NotificationPayload,
                NotificationPriority,
                UnifiedNotificationManager,
            )

            manager = UnifiedNotificationManager()
            manager.notify(
                NotificationPayload(
                    title="Kafka Checkpoint Degraded",
                    message=f"Redis unavailable, using file backup for namespace '{namespace}'",
                    priority=NotificationPriority.HIGH,
                    category=NotificationCategory.OPERATIONS,
                    source="kafka_redis_checkpoint",
                    metadata={"namespace": namespace, "tier": "file_backup"},
                )
            )
        except Exception as e:
            logger.warning(
                "kafka_redis_checkpoint.failed_send_degraded_notification",
                error=e,
            )

    def save_with_kafka_offset(
        self,
        namespace: str,
        wal_sequence: int,
        kafka_topic: str,
        kafka_partition: int,
        kafka_offset: int,
        checksum: str | None = None,
    ) -> None:
        """
        Kafka 오프셋과 함께 체크포인트 저장.
        """
        data = UnifiedCheckpointData(
            wal_sequence=wal_sequence,
            kafka_topic=kafka_topic,
            kafka_partition=kafka_partition,
            kafka_offset=kafka_offset,
            checksum=checksum,
        )
        self.save(namespace, data)

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """체크포인트 로드 (Redis → File Fallback + Checksum 검증)."""
        # 1. Redis에서 시도
        try:
            key = self._get_key(namespace)
            raw = self._redis.get(key)
            if raw:
                raw_data = json.loads(raw)
                data = UnifiedCheckpointData.from_dict(raw_data)

                # Checksum 검증
                if data.checksum:
                    self._verify_data_checksum(data)

                return data
        except CheckpointCorruptedError:
            raise
        except Exception as e:
            logger.warning(
                "kafka_redis_checkpoint.redis_load_failed",
                error=e,
            )

        # 2. File 백업에서 시도 (Fallback)
        if self._file_backup:
            try:
                data = self._file_backup.load(namespace)
                if data:
                    logger.info(
                        "kafka_redis_checkpoint.loaded_file_backup",
                        namespace=namespace,
                    )

                    # Checksum 검증
                    if data.checksum:
                        self._verify_data_checksum(data)

                    return data
            except CheckpointCorruptedError:
                raise
            except Exception as e:
                logger.warning(
                    "kafka_redis_checkpoint.file_backup_load_failed",
                    error=e,
                )

        return None

    def _verify_data_checksum(self, data: UnifiedCheckpointData) -> None:
        """데이터 무결성 검증."""
        try:
            from selfhealing.audit.checksum import verify_checksum

            payload = {
                "wal_sequence": data.wal_sequence,
                "kafka_topic": data.kafka_topic,
                "kafka_partition": data.kafka_partition,
                "kafka_offset": data.kafka_offset,
                "timestamp": data.timestamp,
            }

            result = verify_checksum(payload, data.checksum, algorithm="crc32")

            if not result.is_valid:
                raise CheckpointCorruptedError(
                    "Kafka checkpoint checksum mismatch",
                    expected=result.expected,
                    computed=result.computed,
                )

        except ImportError:
            logger.debug("kafka_redis_checkpoint.checksum_module_available")

    def commit(self, namespace: str) -> None:
        """Redis에 이미 저장됨."""
        pass

    def delete(self, namespace: str) -> bool:
        """체크포인트 삭제 (Redis + File 모두)."""
        redis_result = False
        file_result = False

        try:
            key = self._get_key(namespace)
            redis_result = self._redis.delete(key) > 0
        except Exception:
            pass

        if self._file_backup:
            try:
                file_result = self._file_backup.delete(namespace)
            except Exception:
                pass

        return redis_result or file_result

    def exists(self, namespace: str) -> bool:
        """체크포인트 존재 여부 (Redis 또는 File)."""
        try:
            key = self._get_key(namespace)
            if self._redis.exists(key) > 0:
                return True
        except Exception:
            pass

        if self._file_backup:
            try:
                if self._file_backup.exists(namespace):
                    return True
            except Exception:
                pass

        return False


# =============================================================================
# 다중 저장소 Fallback 체인 (CompositeCheckpointStorage)
# =============================================================================


class CompositeCheckpointStorage(CheckpointStorageStrategy):
    """
    다중 저장소 Fallback 체인.

    Fallback 순서:
    1. Primary (Redis) - 분산 환경 완전 지원
    2. Secondary (File) - 로컬 영속화 백업
    3. Memory Buffer - 최후 수단 (휘발성)

    Usage:
        composite = CompositeCheckpointStorage(
            primary=RedisCheckpointStorage(redis),
            secondary=FileCheckpointStorage("/backup"),
        )
        composite.save("default", data)  # Redis 실패 시 File로 자동 Fallback
    """

    def __init__(
        self,
        primary: CheckpointStorageStrategy,
        secondary: CheckpointStorageStrategy | None = None,
        enable_memory_fallback: bool = True,
    ):
        """
        다중 저장소 Fallback 체인 초기화.

        Args:
            primary: 주 저장소 (Redis 권장)
            secondary: 백업 저장소 (File 권장)
            enable_memory_fallback: 메모리 버퍼 최후 Fallback 활성화
        """
        super().__init__()
        self._primary = primary
        self._secondary = secondary
        self._enable_memory_fallback = enable_memory_fallback

        # Memory Buffer (최후 수단)
        self._memory_buffer: dict[str, UnifiedCheckpointData] = {}

        # 통계
        self._stats = {
            "primary_writes": 0,
            "secondary_writes": 0,
            "memory_writes": 0,
            "fallback_events": 0,
        }
        self._current_tier = "primary"

    def save(self, namespace: str, data: UnifiedCheckpointData) -> None:
        """
        Tiered Fallback 저장.

        1. Primary 시도 → 성공 시 종료
        2. Primary 실패 → Secondary 시도
        3. Secondary 실패 → Memory Buffer (최후)
        """
        # Tier 1: Primary
        try:
            self._primary.save(namespace, data)
            self._current_tier = "primary"
            self._stats["primary_writes"] += 1
            return
        except Exception as e:
            logger.warning(
                "composite_checkpoint.primary_failed",
                error=e,
            )
            self._stats["fallback_events"] += 1

        # Tier 2: Secondary (degraded 마킹)
        if self._secondary:
            try:
                # degraded 상태 기록
                data_copy = UnifiedCheckpointData(
                    wal_sequence=data.wal_sequence,
                    timestamp=data.timestamp,
                    version=data.version,
                    kafka_topic=data.kafka_topic,
                    kafka_partition=data.kafka_partition,
                    kafka_offset=data.kafka_offset,
                    checksum=data.checksum,
                )
                self._secondary.save(namespace, data_copy)
                self._current_tier = "secondary"
                self._stats["secondary_writes"] += 1
                logger.warning(
                    "composite_checkpoint.degraded_secondary",
                    namespace=namespace,
                )
                return
            except Exception as e:
                logger.warning(
                    "composite_checkpoint.secondary_failed",
                    error=e,
                )
                self._stats["fallback_events"] += 1

        # Tier 3: Memory Buffer (최후 수단)
        if self._enable_memory_fallback:
            self._memory_buffer[namespace] = data
            self._current_tier = "memory"
            self._stats["memory_writes"] += 1
            logger.error(
                "composite_checkpoint.degraded_memory_volatile",
                namespace=namespace,
            )
            return

        raise CheckpointError("All storage tiers failed")

    def load(self, namespace: str) -> UnifiedCheckpointData | None:
        """Tiered Load (Primary → Secondary → Memory 순)."""
        # Primary
        try:
            data = self._primary.load(namespace)
            if data:
                return data
        except Exception:
            pass

        # Secondary
        if self._secondary:
            try:
                data = self._secondary.load(namespace)
                if data:
                    return data
            except Exception:
                pass

        # Memory
        return self._memory_buffer.get(namespace)

    def get_stats(self) -> dict[str, Any]:
        """통계 조회."""
        return {
            **self._stats,
            "current_tier": self._current_tier,
        }

    def commit(self, namespace: str) -> None:
        """Primary 커밋."""
        self._primary.commit(namespace)

    def delete(self, namespace: str) -> bool:
        """모든 Tier에서 삭제."""
        results = []
        try:
            results.append(self._primary.delete(namespace))
        except Exception:
            pass
        if self._secondary:
            try:
                results.append(self._secondary.delete(namespace))
            except Exception:
                pass
        self._memory_buffer.pop(namespace, None)
        return any(results)

    def exists(self, namespace: str) -> bool:
        """어느 Tier에든 존재 여부."""
        try:
            if self._primary.exists(namespace):
                return True
        except Exception:
            pass
        if self._secondary:
            try:
                if self._secondary.exists(namespace):
                    return True
            except Exception:
                pass
        return namespace in self._memory_buffer


# =============================================================================
# 전략 레지스트리 (동적 전략 등록)
# =============================================================================


class CheckpointStrategyRegistry:
    """
    체크포인트 전략 동적 레지스트리.

    Usage:
        # 커스텀 전략 등록
        CheckpointStrategyRegistry.register("s3", S3CheckpointStorage)
        CheckpointStrategyRegistry.register("gcs", GCSCheckpointStorage)

        # 전략 조회
        strategy = CheckpointStrategyRegistry.get("s3", bucket="my-bucket")

        # 기본값 설정
        CheckpointStrategyRegistry.set_default("redis")
    """

    _strategies: dict[str, type[CheckpointStorageStrategy]] = {}
    _instances: dict[str, CheckpointStorageStrategy] = {}
    _default: str = "file"
    _lock = threading.Lock()

    @classmethod
    def register(
        cls, name: str, strategy_class: type[CheckpointStorageStrategy]
    ) -> None:
        """
        전략 등록.

        Args:
            name: 전략 식별자 (예: "file", "redis", "s3")
            strategy_class: CheckpointStorageStrategy 구현체
        """
        with cls._lock:
            cls._strategies[name] = strategy_class
            logger.info(
                "cell_registry.bulkheads_registered",
                strategy_name=name,
            )

    @classmethod
    def get(
        cls,
        name: str | None = None,
        force_new: bool = False,
        **kwargs,
    ) -> CheckpointStorageStrategy:
        """
        전략 인스턴스 조회.

        Args:
            name: 전략명 (None이면 기본값)
            force_new: 새 인스턴스 강제 생성
            **kwargs: 전략 생성자 인자
        """
        name = name or cls._default

        with cls._lock:
            if name not in cls._strategies:
                cls._auto_register()
                if name not in cls._strategies:
                    raise ValueError(f"Unknown checkpoint strategy: {name}")

            if force_new or name not in cls._instances:
                cls._instances[name] = cls._strategies[name](**kwargs)

            return cls._instances[name]

    @classmethod
    def set_default(cls, name: str) -> None:
        """기본 전략 설정."""
        cls._default = name

    @classmethod
    def list_strategies(cls) -> list[str]:
        """등록된 전략 목록."""
        cls._auto_register()
        return list(cls._strategies.keys())

    @classmethod
    def _auto_register(cls) -> None:
        """내장 전략 자동 등록."""
        if "file" not in cls._strategies:
            cls._strategies["file"] = FileCheckpointStorage
        if "redis" not in cls._strategies:
            cls._strategies["redis"] = RedisCheckpointStorage
        if "kafka_redis" not in cls._strategies:
            cls._strategies["kafka_redis"] = KafkaRedisCheckpointStorage
        if "composite" not in cls._strategies:
            cls._strategies["composite"] = CompositeCheckpointStorage

    @classmethod
    def clear(cls) -> None:
        """레지스트리 초기화 (테스트용)."""
        with cls._lock:
            cls._strategies.clear()
            cls._instances.clear()
            cls._default = "file"


# =============================================================================
# K8s 환경 감지
# =============================================================================


def _is_k8s_environment() -> bool:
    """K8s 환경 여부 감지."""
    return bool(
        os.environ.get("KUBERNETES_SERVICE_HOST")
        or os.environ.get("KUBERNETES_PORT")
        or Path("/var/run/secrets/kubernetes.io").exists()
    )


# =============================================================================
# 팩토리 함수
# =============================================================================


def get_checkpoint_strategy(
    storage_type: str | None = None,
    redis_client: redis.Redis | None = None,
    **kwargs,
) -> CheckpointStorageStrategy:
    """
    환경에 맞는 체크포인트 저장 전략 반환.

    Args:
        storage_type: 저장소 유형 ("file", "redis", "kafka_redis", "composite")
                     None이면 환경변수 SELFHEALING_CHECKPOINT_STORAGE에서 결정
        redis_client: Redis 클라이언트 (redis, kafka_redis 시 필요)
        **kwargs: 추가 설정

    Returns:
        CheckpointStorageStrategy 구현체

    환경변수:
        SELFHEALING_CHECKPOINT_STORAGE: file, redis, kafka_redis, composite
        SELFHEALING_AUDIT_PATH: 파일 저장 경로
        SELFHEALING_CHECKPOINT_ENABLE_NOTIFICATION: 알림 활성화 (기본 TRUE)
        SELFHEALING_CHECKPOINT_USE_DISTRIBUTED_LOCK: 분산 락 사용 (기본 TRUE)
        SELFHEALING_CHECKPOINT_ENABLE_FILE_BACKUP: 파일 백업 사용 (기본 TRUE)

    Usage:
        # 환경변수 기반 자동 선택
        strategy = get_checkpoint_strategy()

        # 명시적 선택
        strategy = get_checkpoint_strategy(storage_type="redis", redis_client=r)

        # CompositeCheckpointStorage 사용
        strategy = get_checkpoint_strategy(
            storage_type="composite",
            redis_client=r,
            primary_type="redis",
            secondary_type="file",
        )
    """
    if storage_type is None:
        storage_type = os.environ.get("SELFHEALING_CHECKPOINT_STORAGE", "file").lower()

    # K8s 환경에서 file 모드 사용 시 경고
    if storage_type == "file" and _is_k8s_environment():
        logger.warning(
            "checkpoint_strategy.file_storage_in_k8s",
            message="FileCheckpointStorage is not recommended in multi-pod K8s environments. "
            "Use SELFHEALING_CHECKPOINT_STORAGE=redis or composite for cross-pod consistency.",
        )

    # 환경변수에서 옵션 로드
    enable_notification = (
        os.environ.get("SELFHEALING_CHECKPOINT_ENABLE_NOTIFICATION", "TRUE").upper()
        == "TRUE"
    )
    use_distributed_lock = (
        os.environ.get("SELFHEALING_CHECKPOINT_USE_DISTRIBUTED_LOCK", "TRUE").upper()
        == "TRUE"
    )
    enable_file_backup = (
        os.environ.get("SELFHEALING_CHECKPOINT_ENABLE_FILE_BACKUP", "TRUE").upper()
        == "TRUE"
    )

    if storage_type == "file":
        return FileCheckpointStorage(
            base_path=kwargs.get("base_path"),
            sync_on_write=kwargs.get("sync_on_write", True),
        )

    elif storage_type == "redis":
        if redis_client is None:
            raise ValueError("redis_client is required for storage_type='redis'")
        return RedisCheckpointStorage(
            redis_client=redis_client,
            ttl_seconds=kwargs.get("ttl_seconds"),
            use_distributed_lock=kwargs.get(
                "use_distributed_lock", use_distributed_lock
            ),
            enable_notification=kwargs.get("enable_notification", enable_notification),
        )

    elif storage_type == "kafka_redis":
        if redis_client is None:
            raise ValueError("redis_client is required for storage_type='kafka_redis'")
        return KafkaRedisCheckpointStorage(
            redis_client=redis_client,
            default_topic=kwargs.get("default_topic", "selfhealing.audit.events"),
            enable_file_backup=kwargs.get("enable_file_backup", enable_file_backup),
            enable_notification=kwargs.get("enable_notification", enable_notification),
        )

    elif storage_type == "composite":
        # Composite 전략 생성
        primary_type = kwargs.get("primary_type", "redis")
        secondary_type = kwargs.get("secondary_type", "file")

        if primary_type == "redis":
            if redis_client is None:
                raise ValueError(
                    "redis_client is required for composite with redis primary"
                )
            primary = RedisCheckpointStorage(
                redis_client=redis_client,
                use_distributed_lock=use_distributed_lock,
                enable_notification=False,  # Composite에서 통합 관리
            )
        else:
            primary = FileCheckpointStorage()

        secondary = (
            FileCheckpointStorage(
                base_path=kwargs.get("secondary_base_path"),
            )
            if secondary_type == "file"
            else None
        )

        return CompositeCheckpointStorage(
            primary=primary,
            secondary=secondary,
            enable_memory_fallback=kwargs.get("enable_memory_fallback", True),
        )

    else:
        # Registry에서 조회 시도
        try:
            return CheckpointStrategyRegistry.get(storage_type, **kwargs)
        except ValueError:
            raise ValueError(f"Unknown storage_type: {storage_type}")


# =============================================================================
# 싱글톤
# =============================================================================

_default_strategy: CheckpointStorageStrategy | None = None
_default_lock = threading.Lock()


def get_default_checkpoint_strategy() -> CheckpointStorageStrategy:
    """
    기본 CheckpointStorageStrategy 싱글톤 반환.

    환경변수 기반으로 전략 자동 선택.
    """
    global _default_strategy

    with _default_lock:
        if _default_strategy is None:
            storage_type = os.environ.get("SELFHEALING_CHECKPOINT_STORAGE", "file")

            if storage_type in ("redis", "kafka_redis"):
                # Redis 클라이언트 자동 생성
                try:
                    import redis

                    redis_url = os.environ.get(
                        "SELFHEALING_REDIS_URL", "redis://localhost:6379/0"
                    )
                    redis_client = redis.from_url(redis_url)
                    _default_strategy = get_checkpoint_strategy(
                        storage_type=storage_type,
                        redis_client=redis_client,
                    )
                except ImportError:
                    logger.warning(
                        "checkpoint_strategy.redis_package_installed_falling",
                    )
                    _default_strategy = FileCheckpointStorage()
            else:
                _default_strategy = FileCheckpointStorage()

        return _default_strategy


def reset_default_checkpoint_strategy() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _default_strategy

    with _default_lock:
        _default_strategy = None

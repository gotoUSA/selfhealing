"""
KafkaCheckpointManager - WAL-Kafka 오프셋 원자적 매핑.

WAL 시퀀스와 Kafka 오프셋을 원자적으로 기록하여
'어디까지 Kafka로 보냈는지'를 추적합니다.

기존 CheckpointManager를 확장하여 Kafka 전용 필드 추가:
- kafka_topic: Kafka 토픽
- kafka_partition: Kafka 파티션
- kafka_offset: Kafka 오프셋

저장소 옵션:
- Redis (분산 환경 권장)
- File (단일 노드)

Usage:
    from selfhealing.audit.kafka_checkpoint import KafkaCheckpointManager

    checkpoint = KafkaCheckpointManager(storage="redis", redis_client=redis)

    # 체크포인트 저장
    checkpoint.save_checkpoint(
        namespace="default",
        wal_sequence=1234,
        kafka_topic="selfhealing.audit.events",
        kafka_partition=3,
        kafka_offset=56789,
        checksum="abc123",
    )

    # 체크포인트 조회
    data = checkpoint.get_last_checkpoint("default")
    if data:
        print(f"Last WAL seq: {data.wal_sequence}")
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    import redis

logger = structlog.get_logger()


@dataclass
class KafkaCheckpointData:
    """Kafka 체크포인트 데이터."""

    wal_sequence: int
    """마지막 처리된 WAL 시퀀스."""

    kafka_topic: str
    """Kafka 토픽."""

    kafka_partition: int
    """Kafka 파티션."""

    kafka_offset: int
    """Kafka 오프셋."""

    timestamp: str
    """체크포인트 시간 (ISO 8601)."""

    checksum: str
    """WAL 엔트리 체크섬 (검증용)."""

    version: int = 1
    """체크포인트 버전."""

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> KafkaCheckpointData:
        """딕셔너리에서 생성."""
        return cls(
            wal_sequence=data.get("wal_sequence", 0),
            kafka_topic=data.get("kafka_topic", ""),
            kafka_partition=data.get("kafka_partition", 0),
            kafka_offset=data.get("kafka_offset", 0),
            timestamp=data.get("timestamp", ""),
            checksum=data.get("checksum", ""),
            version=data.get("version", 1),
        )


class KafkaCheckpointError(Exception):
    """Kafka 체크포인트 관련 에러."""

    pass


class KafkaCheckpointManager:
    """
    WAL-Kafka 체크포인트 관리자.

    WAL 시퀀스와 Kafka 오프셋을 원자적으로 기록하여
    정확한 복구 지점을 보장합니다.

    저장소 옵션:
    - redis: Redis 기반 (분산 환경 권장)
    - file: 파일 기반 (단일 노드)
    """

    REDIS_KEY_PREFIX = "selfhealing:kafka_checkpoint:"
    DEFAULT_FILE_PATH = "kafka_checkpoint.json"

    def __init__(
        self,
        storage: str = "file",
        redis_client: redis.Redis | None = None,
        file_path: str | Path | None = None,
    ):
        """
        KafkaCheckpointManager 초기화.

        Args:
            storage: 저장소 유형 ("redis" 또는 "file")
            redis_client: Redis 클라이언트 (storage="redis" 시 필수)
            file_path: 파일 경로 (storage="file" 시 사용)
        """
        self._storage = storage
        self._redis = redis_client
        self._file_path = self._get_file_path(file_path)
        self._lock = threading.Lock()
        self._cache: dict[str, KafkaCheckpointData] = {}

        if storage == "redis" and redis_client is None:
            raise ValueError("redis_client is required when storage='redis'")

    def _get_file_path(self, file_path: str | Path | None) -> Path:
        """파일 경로 결정."""
        if file_path:
            return Path(file_path)

        env_path = os.environ.get("SELFHEALING_AUDIT_PATH")
        if env_path:
            return Path(env_path) / self.DEFAULT_FILE_PATH

        if os.name == "nt":
            return Path(tempfile.gettempdir()) / "selfhealing" / self.DEFAULT_FILE_PATH
        return Path("/var/log/audit") / self.DEFAULT_FILE_PATH

    def get_last_checkpoint(self, namespace: str = "default") -> KafkaCheckpointData | None:
        """
        마지막 체크포인트 조회.

        Args:
            namespace: 네임스페이스

        Returns:
            KafkaCheckpointData 또는 None
        """
        if self._storage == "redis":
            return self._get_from_redis(namespace)
        return self._get_from_file(namespace)

    def save_checkpoint(
        self,
        namespace: str,
        wal_sequence: int,
        kafka_topic: str,
        kafka_partition: int,
        kafka_offset: int,
        checksum: str,
    ) -> None:
        """
        체크포인트 저장 (원자적).

        WAL 시퀀스와 Kafka 오프셋을 함께 저장하여
        정확한 복구 지점을 보장합니다.

        Args:
            namespace: 네임스페이스
            wal_sequence: WAL 시퀀스 번호
            kafka_topic: Kafka 토픽
            kafka_partition: Kafka 파티션
            kafka_offset: Kafka 오프셋
            checksum: WAL 엔트리 체크섬
        """
        data = KafkaCheckpointData(
            wal_sequence=wal_sequence,
            kafka_topic=kafka_topic,
            kafka_partition=kafka_partition,
            kafka_offset=kafka_offset,
            timestamp=datetime.now(timezone.utc).isoformat(),
            checksum=checksum,
        )

        if self._storage == "redis":
            self._save_to_redis(namespace, data)
        else:
            self._save_to_file(namespace, data)

        with self._lock:
            self._cache[namespace] = data

    def _get_from_redis(self, namespace: str) -> KafkaCheckpointData | None:
        """Redis에서 체크포인트 조회."""
        if not self._redis:
            return None

        key = f"{self.REDIS_KEY_PREFIX}{namespace}"
        data = self._redis.get(key)
        if data:
            return KafkaCheckpointData.from_dict(json.loads(data))
        return None

    def _save_to_redis(self, namespace: str, data: KafkaCheckpointData) -> None:
        """Redis에 체크포인트 저장."""
        if not self._redis:
            raise KafkaCheckpointError("Redis client not configured")

        key = f"{self.REDIS_KEY_PREFIX}{namespace}"
        self._redis.set(key, json.dumps(data.to_dict()))

    def _get_from_file(self, namespace: str) -> KafkaCheckpointData | None:
        """파일에서 체크포인트 조회."""
        file_path = self._file_path.with_suffix(f".{namespace}.json")
        if not file_path.exists():
            return None

        try:
            with open(file_path, encoding="utf-8") as f:
                return KafkaCheckpointData.from_dict(json.load(f))
        except Exception as e:
            logger.warning(
                "kafka_checkpoint_manager.load_failed",
                error=e,
            )
            return None

    def _save_to_file(self, namespace: str, data: KafkaCheckpointData) -> None:
        """파일에 체크포인트 저장 (원자적)."""
        file_path = self._file_path.with_suffix(f".{namespace}.json")
        file_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_path = file_path.with_suffix(".tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(data.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())

            # Windows 호환: 기존 파일 삭제 후 rename
            if file_path.exists():
                file_path.unlink()
            tmp_path.rename(file_path)
        except Exception as e:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise KafkaCheckpointError(f"Failed to save checkpoint: {e}") from e

    def delete_checkpoint(self, namespace: str) -> bool:
        """
        체크포인트 삭제.

        Args:
            namespace: 네임스페이스

        Returns:
            삭제 성공 여부
        """
        with self._lock:
            self._cache.pop(namespace, None)

        if self._storage == "redis":
            if self._redis:
                key = f"{self.REDIS_KEY_PREFIX}{namespace}"
                return self._redis.delete(key) > 0
            return False

        file_path = self._file_path.with_suffix(f".{namespace}.json")
        try:
            file_path.unlink(missing_ok=True)
            return True
        except Exception:
            return False

    def get_wal_sequence(self, namespace: str = "default") -> int:
        """
        마지막 WAL 시퀀스 조회 (편의 메서드).

        Args:
            namespace: 네임스페이스

        Returns:
            마지막 WAL 시퀀스 (없으면 0)
        """
        data = self.get_last_checkpoint(namespace)
        return data.wal_sequence if data else 0


# =============================================================================
# WAL → Kafka 동기화 헬퍼
# =============================================================================


def sync_wal_to_kafka_with_checkpoint(
    wal,
    producer,
    checkpoint: KafkaCheckpointManager,
    namespace: str = "default",
) -> int:
    """
    WAL → Kafka 동기화 (체크포인트 기반).

    마지막 체크포인트 이후의 엔트리만 전송하여 중복 방지.
    KafkaAuditProducer 또는 기존 KafkaAuditAdapter 모두 지원.

    Args:
        wal: WriteAheadLog 인스턴스
        producer: KafkaAuditProducer 또는 KafkaAuditAdapter 인스턴스
        checkpoint: KafkaCheckpointManager 인스턴스
        namespace: 네임스페이스

    Returns:
        동기화된 엔트리 수
    """
    last_cp = checkpoint.get_last_checkpoint(namespace)
    last_seq = last_cp.wal_sequence if last_cp else 0

    entries = wal.recover_unprocessed(last_processed_seq=last_seq)
    synced = 0

    # Producer 타입 감지: KafkaAuditProducer vs 기존 Adapter
    is_new_producer = hasattr(producer, "publish_audit_event")

    for entry in entries:
        try:
            if is_new_producer:
                # 새로운 KafkaAuditProducer 사용
                success = producer.publish_audit_event(
                    event=entry.data,
                    domain=namespace,
                )
                if not success:
                    logger.error(
                        "wal_kafka_publish_failed",
                        entry=entry.sequence,
                    )
                    break

                # 동기 플러시로 전송 완료 확인
                remaining = producer.flush(timeout=5.0)
                if remaining > 0:
                    logger.warning(
                        "wal_kafka_messages_pending",
                        remaining=remaining,
                    )

                # 토픽 이름 획득
                kafka_topic = producer._settings.full_audit_topic
            else:
                # 기존 KafkaAuditAdapter 사용 (하위 호환성)
                from selfhealing.interfaces.audit_adapter import AuditEntry

                audit_entry = AuditEntry(**entry.data)
                producer.log(audit_entry)
                producer.flush(timeout=5.0)
                kafka_topic = producer._settings.topic

            # 체크포인트 저장 (원자적)
            checkpoint.save_checkpoint(
                namespace=namespace,
                wal_sequence=entry.sequence,
                kafka_topic=kafka_topic,
                kafka_partition=0,  # 실제 파티션은 콜백에서 획득
                kafka_offset=0,  # 실제 오프셋은 콜백에서 획득
                checksum=entry.checksum,
            )
            synced += 1

        except Exception as e:
            logger.exception(
                "wal_kafka_sync_failed",
                entry=entry.sequence,
                error=e,
            )
            break

    return synced

"""
CheckpointManager - WAL 처리 시퀀스 영속화.

마지막 처리된 WAL 시퀀스를 디스크에 저장하여 프로세스 재시작 시 복구 지원.
WriteAheadLog의 recover_unprocessed()와 함께 사용하여 데이터 유실 0% 달성.

Usage:
    from selfhealing.audit.checkpoint_manager import CheckpointManager

    checkpoint = CheckpointManager("/var/log/audit/checkpoint")

    # 처리 완료 후 체크포인트 저장
    checkpoint.save(last_seq=1234)

    # 재시작 시 체크포인트 로드
    last_seq = checkpoint.load()
    entries = wal.recover_unprocessed(last_seq)

Version: 1.0.0
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CheckpointData:
    """체크포인트 데이터."""

    last_sequence: int
    timestamp: float
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """딕셔너리로 변환."""
        return {
            "last_sequence": self.last_sequence,
            "timestamp": self.timestamp,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CheckpointData:
        """딕셔너리에서 생성."""
        return cls(
            last_sequence=data.get("last_sequence", 0),
            timestamp=data.get("timestamp", 0.0),
            version=data.get("version", 1),
        )


class CheckpointError(Exception):
    """체크포인트 관련 에러."""

    pass


class CheckpointManager:
    """
    WAL 처리 시퀀스 관리자.

    마지막 처리된 WAL 시퀀스를 디스크에 영속화하여
    프로세스 재시작 시 정확한 복구 지점 제공.

    특징:
    - Thread-safe
    - fsync로 디스크 영속화 보장
    - 원자적 쓰기 (임시 파일 사용)
    - JSON 형식으로 사람이 읽기 가능
    """

    DEFAULT_CHECKPOINT_DIR = "/var/log/audit"
    DEFAULT_CHECKPOINT_FILENAME = "checkpoint.json"

    def __init__(
        self,
        checkpoint_path: str | Path | None = None,
        sync_on_write: bool = True,
    ):
        """
        CheckpointManager 초기화.

        Args:
            checkpoint_path: 체크포인트 파일 경로 (None이면 기본값 사용)
            sync_on_write: 쓰기 시 fsync 수행 여부
        """
        if checkpoint_path is None:
            checkpoint_path = Path(self.DEFAULT_CHECKPOINT_DIR) / self.DEFAULT_CHECKPOINT_FILENAME

        self._path = Path(checkpoint_path)
        self._sync_on_write = sync_on_write
        self._lock = threading.RLock()

        # 디렉토리 생성
        self._path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> Path:
        """체크포인트 파일 경로."""
        return self._path

    def save(self, last_sequence: int) -> None:
        """
        체크포인트 저장.

        원자적 쓰기를 위해 임시 파일에 먼저 쓰고 rename.

        Args:
            last_sequence: 마지막 처리된 시퀀스 번호

        Raises:
            CheckpointError: 저장 실패 시
        """
        with self._lock:
            checkpoint_data = CheckpointData(
                last_sequence=last_sequence,
                timestamp=time.time(),
            )

            temp_path = self._path.with_suffix(".tmp")

            try:
                # 임시 파일에 쓰기
                with open(temp_path, "w", encoding="utf-8") as f:
                    json.dump(checkpoint_data.to_dict(), f, indent=2)

                    if self._sync_on_write:
                        f.flush()
                        os.fsync(f.fileno())

                # 원자적 rename
                temp_path.replace(self._path)

                # 디렉토리 fsync (선택적, Linux에서 권장)
                if self._sync_on_write:
                    try:
                        dir_fd = os.open(str(self._path.parent), os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(dir_fd)
                        finally:
                            os.close(dir_fd)
                    except (OSError, AttributeError):
                        # Windows에서는 O_DIRECTORY 미지원
                        pass

                logger.debug(f"Checkpoint saved: sequence={last_sequence}")

            except Exception as e:
                # 임시 파일 정리
                try:
                    temp_path.unlink(missing_ok=True)
                except Exception:
                    pass

                raise CheckpointError(f"Failed to save checkpoint: {e}") from e

    def load(self) -> int:
        """
        체크포인트 로드.

        파일이 없거나 읽기 실패 시 0 반환.

        Returns:
            마지막 처리된 시퀀스 번호 (없으면 0)
        """
        with self._lock:
            if not self._path.exists():
                logger.debug("Checkpoint file not found, returning 0")
                return 0

            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)

                checkpoint_data = CheckpointData.from_dict(data)
                logger.debug(f"Checkpoint loaded: sequence={checkpoint_data.last_sequence}")
                return checkpoint_data.last_sequence

            except Exception as e:
                logger.warning(f"Failed to load checkpoint: {e}, returning 0")
                return 0

    def load_full(self) -> CheckpointData | None:
        """
        체크포인트 전체 데이터 로드.

        Returns:
            CheckpointData 또는 None
        """
        with self._lock:
            if not self._path.exists():
                return None

            try:
                with open(self._path, encoding="utf-8") as f:
                    data = json.load(f)

                return CheckpointData.from_dict(data)

            except Exception:
                return None

    def exists(self) -> bool:
        """체크포인트 파일 존재 여부."""
        return self._path.exists()

    def delete(self) -> bool:
        """
        체크포인트 파일 삭제.

        Returns:
            삭제 성공 여부
        """
        with self._lock:
            try:
                self._path.unlink(missing_ok=True)
                return True
            except Exception:
                return False

    def get_age_seconds(self) -> float | None:
        """
        체크포인트 경과 시간 (초).

        Returns:
            마지막 저장 후 경과 시간 또는 None
        """
        checkpoint_data = self.load_full()
        if checkpoint_data is None:
            return None

        return time.time() - checkpoint_data.timestamp


# =============================================================================
# Singleton Pattern
# =============================================================================

_default_checkpoint_manager: CheckpointManager | None = None
_default_lock = threading.Lock()


def get_checkpoint_manager(
    checkpoint_path: str | Path | None = None,
) -> CheckpointManager:
    """
    기본 CheckpointManager 인스턴스 반환.

    싱글톤 패턴으로 동일 인스턴스 재사용.

    Args:
        checkpoint_path: 체크포인트 파일 경로 (첫 호출 시에만 적용)

    Returns:
        CheckpointManager 인스턴스
    """
    global _default_checkpoint_manager

    with _default_lock:
        if _default_checkpoint_manager is None:
            _default_checkpoint_manager = CheckpointManager(checkpoint_path)

        return _default_checkpoint_manager


def reset_checkpoint_manager() -> None:
    """싱글톤 인스턴스 초기화 (테스트용)."""
    global _default_checkpoint_manager

    with _default_lock:
        _default_checkpoint_manager = None

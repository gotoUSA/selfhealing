"""
Disk-Persistent Buffer 설정.

환경변수 기반 설정으로 LMDB 영속 버퍼 동작을 제어합니다.

환경변수 접두사: SELFHEALING_DISK_BUFFER_

주요 설정:
- storage_type: 스토리지 유형 (lmdb, mmap)
- data_dir: 데이터 디렉토리 경로
- lmdb_map_size_mb: LMDB 최대 크기
- max_entries: 최대 엔트리 수
- group_commit_enabled: Group Commit 활성화
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings

logger = structlog.get_logger()


def _is_windows() -> bool:
    """현재 플랫폼이 Windows인지 반환."""
    return sys.platform == "win32"


def _get_default_data_dir() -> str:
    """플랫폼별 기본 데이터 디렉토리 반환."""
    if _is_windows():
        # Windows: 임시 디렉토리 사용
        return os.path.join(os.environ.get("TEMP", "C:\\Temp"), "selfhealing", "buffer")
    return "/var/lib/selfhealing/buffer"


def _get_default_lmdb_map_size_mb() -> int:
    """플랫폼별 LMDB map_size 기본값 (MB).

    Linux: 10GB (가상 주소 공간만 예약, 실제 디스크 사용량은 데이터 크기에 비례)
    Windows: 256MB (writemap 모드에서 map_size 전체가 파일로 할당되므로 작게 설정)
    """
    if _is_windows():
        return 256
    return 10240


def _get_default_lmdb_writemap() -> bool:
    """플랫폼별 LMDB writemap 기본값.

    Linux (ext4/XFS): True (성능 향상)
    Windows: False (writemap=True 시 map_size 전체가 파일로 미리 할당됨)
    """
    return not _is_windows()


class DiskBufferSettings(BaseSettings):
    """
    Disk-Persistent Buffer 설정.

    환경변수:
    - SELFHEALING_DISK_BUFFER_* 접두사로 모든 설정 오버라이드 가능

    주요 기능 플래그:
    - group_commit_enabled: I/O 최적화를 위한 Group Commit
    - fail_open_on_disk_full: 디스크 풀 시 서비스 지속
    - priority_based_purge: 용량 초과 시 우선순위 기반 삭제
    - quarantine_on_corruption: DB 손상 시 격리 후 새 DB 생성
    - enable_dead_letter_db: Poison Pill 엔트리 격리
    """

    # ─────────────────────────────────────────────────────
    # 스토리지 설정
    # ─────────────────────────────────────────────────────

    storage_type: str = Field(
        default="lmdb",
        description="스토리지 유형: lmdb, mmap",
    )

    data_dir: str = Field(
        default_factory=_get_default_data_dir,
        description="데이터 디렉토리 경로",
    )

    # ─────────────────────────────────────────────────────
    # LMDB 설정
    # ─────────────────────────────────────────────────────

    lmdb_map_size_mb: int = Field(
        default_factory=_get_default_lmdb_map_size_mb,
        description=(
            "LMDB 최대 데이터베이스 크기 (MB). "
            "Linux: 10GB (가상 주소만 예약). "
            "Windows: 256MB (파일이 실제 할당되므로 작게 설정)."
        ),
    )

    lmdb_max_dbs: int = Field(
        default=10,
        description="LMDB 최대 데이터베이스 수",
    )

    lmdb_writemap: bool = Field(
        default_factory=_get_default_lmdb_writemap,
        description=(
            "LMDB writemap 모드. "
            "Linux (ext4/XFS): True (성능 향상). "
            "Windows: False (map_size 전체가 파일로 할당되는 문제 방지)."
        ),
    )

    lmdb_metasync: bool = Field(
        default=True,
        description="LMDB 메타데이터 동기화",
    )

    # ─────────────────────────────────────────────────────
    # Multi-Instance 설정
    # ─────────────────────────────────────────────────────

    include_hostname_in_db_name: bool = Field(
        default=True,
        description="DB 이름에 호스트네임 포함 (멀티 Pod 충돌 방지)",
    )

    include_pid_in_db_name: bool = Field(
        default=True,
        description=("DB 이름에 PID 포함 (멀티 프로세스 안전). " "개발 환경에서 재시작 시 DB가 누적되면 False로 설정."),
    )

    instance_name: str = Field(
        default="",
        description="인스턴스 이름 (메트릭 레이블용)",
    )

    # ─────────────────────────────────────────────────────
    # 버퍼 설정
    # ─────────────────────────────────────────────────────

    max_entries: int = Field(
        default=100000,
        description="최대 엔트리 수",
    )

    flush_batch_size: int = Field(
        default=1000,
        description="플러시 배치 크기",
    )

    # ─────────────────────────────────────────────────────
    # 정리 설정
    # ─────────────────────────────────────────────────────

    retention_hours: int = Field(
        default=72,
        description="데이터 보관 기간 (시간)",
    )

    cleanup_interval_seconds: float = Field(
        default=3600.0,
        description="정리 작업 주기 (초)",
    )

    # ─────────────────────────────────────────────────────
    # 무결성 설정
    # ─────────────────────────────────────────────────────

    enable_checksum: bool = Field(
        default=True,
        description="CRC32 체크섬 활성화",
    )

    sync_on_write: bool = Field(
        default=False,
        description="매 쓰기 시 fsync (성능 영향)",
    )

    # ─────────────────────────────────────────────────────
    # Group Commit 설정
    # ─────────────────────────────────────────────────────

    group_commit_enabled: bool = Field(
        default=True,
        description="Group Commit 활성화 (I/O 최적화)",
    )

    group_commit_interval_ms: int = Field(
        default=100,
        description="Group Commit 간격 (ms). 이 간격마다 fsync 수행.",
    )

    group_commit_max_entries: int = Field(
        default=100,
        description="Group Commit 최대 버퍼 엔트리 수",
    )

    # ─────────────────────────────────────────────────────
    # Disk Full 대응 설정
    # ─────────────────────────────────────────────────────

    fail_open_on_disk_full: bool = Field(
        default=True,
        description="디스크 풀 시 Fail-Open 모드 전환 (서비스 지속)",
    )

    disk_recovery_threshold: float = Field(
        default=0.1,
        description="디스크 복구 임계치 (10% 여유 시 정상 모드 복귀)",
    )

    priority_based_purge: bool = Field(
        default=True,
        description="우선순위 기반 삭제 활성화",
    )

    disk_full_threshold: float = Field(
        default=0.05,
        description="디스크 풀 임계치 (5% 미만 시 Fail-Open)",
    )

    # ─────────────────────────────────────────────────────
    # Quarantine 설정 (Corruption 대응)
    # ─────────────────────────────────────────────────────

    quarantine_on_corruption: bool = Field(
        default=True,
        description="DB 손상 시 격리 후 새 DB 생성",
    )

    quarantine_suffix: str = Field(
        default=".corrupt",
        description="격리된 DB 파일 접미사",
    )

    # ─────────────────────────────────────────────────────
    # Poison Pill 설정 (플러시 실패 엔트리 처리)
    # ─────────────────────────────────────────────────────

    max_flush_retries: int = Field(
        default=3,
        description="플러시 최대 재시도 횟수",
    )

    enable_dead_letter_db: bool = Field(
        default=True,
        description="Dead Letter DB 활성화 (Poison Pill 격리)",
    )

    # ─────────────────────────────────────────────────────
    # Graceful Shutdown 설정
    # ─────────────────────────────────────────────────────

    enable_shutdown_handlers: bool = Field(
        default=True,
        description="Graceful Shutdown 핸들러 자동 등록",
    )

    model_config = ConfigDict(
        env_prefix="SELFHEALING_DISK_BUFFER_",
        env_file=".env",
        extra="ignore",
    )

    @property
    def data_path(self) -> Path:
        """데이터 경로 반환."""
        return Path(self.data_dir)

    @property
    def lmdb_map_size_bytes(self) -> int:
        """LMDB 맵 크기 (바이트)."""
        return self.lmdb_map_size_mb * 1024 * 1024

    def validate_settings(self) -> list[str]:
        """
        설정 검증 및 경고 메시지 반환.

        Returns:
            경고 메시지 목록
        """
        warnings: list[str] = []

        # map_size 최소값 검증 (100MB)
        if self.lmdb_map_size_mb < 100:
            warnings.append(f"lmdb_map_size_mb too small: {self.lmdb_map_size_mb}MB. " "Minimum recommended is 100MB.")

        # max_entries 검증
        if self.max_entries < 100:
            warnings.append(f"max_entries too small: {self.max_entries}")

        # Group Commit 검증
        if self.group_commit_enabled:
            if self.group_commit_max_entries < 1:
                warnings.append("group_commit_max_entries must be >= 1")
            if self.group_commit_interval_ms < 10:
                warnings.append("group_commit_interval_ms must be >= 10ms")

        # sync_on_write와 group_commit 충돌 경고
        if self.sync_on_write and self.group_commit_enabled:
            warnings.append(
                "sync_on_write=True with group_commit_enabled=True "
                "reduces group commit benefits. Consider sync_on_write=False."
            )

        # Poison Pill 설정 검증
        if self.max_flush_retries < 1:
            warnings.append("max_flush_retries must be >= 1")

        return warnings

    def get_lmdb_open_kwargs(self) -> dict[str, Any]:
        """
        LMDB 환경 open() 인자 반환.

        Returns:
            lmdb.open() kwargs
        """
        return {
            "map_size": self.lmdb_map_size_bytes,
            "max_dbs": self.lmdb_max_dbs,
            "sync": self.sync_on_write,
            "writemap": self.lmdb_writemap,
            "metasync": self.lmdb_metasync,
        }


@lru_cache(maxsize=1)
def get_disk_buffer_settings() -> DiskBufferSettings:
    """설정 싱글톤 반환."""
    settings = DiskBufferSettings()

    # 검증 경고 로깅
    warnings = settings.validate_settings()
    if warnings:
        for warning in warnings:
            logger.warning(
                "disk_buffer_settings.event",
                warning=warning,
            )

    return settings


def reset_disk_buffer_settings() -> None:
    """설정 캐시 리셋 (테스트용)."""
    get_disk_buffer_settings.cache_clear()

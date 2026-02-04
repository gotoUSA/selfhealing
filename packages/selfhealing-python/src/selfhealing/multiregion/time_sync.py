"""
Time Sync 상태 모니터링.

AWS Time Sync Service (169.254.169.123) 사용 여부 및 시계 정확도를 확인합니다.
Active-Active 환경에서 충돌 해결(LWW)의 정확성을 위해 5ms 이하의 정밀도가 필요합니다.

사용 예:
    checker = TimeSyncChecker()
    status = checker.check_sync_status()
    if status.is_synced and status.offset_ms < 5:
        print("Time sync OK")
    else:
        print(f"Time sync issue: offset={status.offset_ms}ms")
"""

from __future__ import annotations

import logging
import platform
import subprocess
from dataclasses import dataclass
from functools import lru_cache

logger = logging.getLogger(__name__)


@dataclass
class TimeSyncStatus:
    """
    Time Sync 상태 정보.

    Attributes:
        is_synced: NTP 동기화 여부
        offset_ms: 시스템 시간 오프셋 (ms)
        source: NTP 소스 (예: '169.254.169.123')
        stratum: NTP Stratum (1=최상위, 16=미동기화)
    """

    is_synced: bool
    """NTP 동기화 여부."""

    offset_ms: float
    """시스템 시간 오프셋 (ms). 낮을수록 정확."""

    source: str
    """NTP 소스 IP 또는 호스트명."""

    stratum: int
    """NTP Stratum (계층). 1=최상위, 16=미동기화."""


class TimeSyncChecker:
    """
    AWS Time Sync Service 연동 체커.

    chronyc 또는 ntpq 명령을 사용하여 시계 동기화 상태를 확인합니다.
    AWS EC2/EKS 환경에서 169.254.169.123을 사용하면 5ms 이하 정밀도 가능.

    사용 예:
        checker = TimeSyncChecker()

        # 상태 확인
        status = checker.check_sync_status()
        print(f"Synced: {status.is_synced}, Offset: {status.offset_ms}ms")

        # 정확도만 확인
        accuracy_ms = checker.get_clock_accuracy_ms()
        if accuracy_ms > 5:
            logger.warning("Clock accuracy degraded")
    """

    AWS_TIME_SYNC_IP = "169.254.169.123"
    """AWS Time Sync Service IP."""

    def __init__(self):
        """초기화."""
        self._is_windows = platform.system() == "Windows"

    def check_sync_status(self) -> TimeSyncStatus:
        """
        시계 동기화 상태 확인.

        chronyc tracking 명령 결과를 파싱하여 상태를 반환합니다.
        Windows에서는 w32tm 명령을 사용합니다.

        Returns:
            TimeSyncStatus: 동기화 상태 정보
        """
        if self._is_windows:
            return self._check_windows_time()
        return self._check_linux_chrony()

    def _check_linux_chrony(self) -> TimeSyncStatus:
        """Linux chronyc tracking 결과 파싱."""
        try:
            result = subprocess.run(
                ["chronyc", "tracking"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                logger.warning("[TimeSync] chronyc command failed")
                return TimeSyncStatus(
                    is_synced=False,
                    offset_ms=float("inf"),
                    source="unknown",
                    stratum=16,
                )

            return self._parse_chronyc_output(result.stdout)

        except FileNotFoundError:
            logger.debug("[TimeSync] chronyc not found, trying ntpq")
            return self._check_linux_ntpq()
        except subprocess.TimeoutExpired:
            logger.warning("[TimeSync] chronyc command timeout")
            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="timeout",
                stratum=16,
            )
        except Exception as e:
            logger.warning(f"[TimeSync] chronyc check failed: {e}")
            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="error",
                stratum=16,
            )

    def _parse_chronyc_output(self, output: str) -> TimeSyncStatus:
        """chronyc tracking 출력 파싱."""
        lines = output.strip().split("\n")
        offset_ms = 0.0
        source = ""
        stratum = 16

        for line in lines:
            if "System time" in line:
                # "0.000000123 seconds fast of NTP time" → 0.000123 ms
                parts = line.split(":")[-1].strip().split()
                if len(parts) >= 2:
                    try:
                        offset_ms = abs(float(parts[0])) * 1000
                    except ValueError:
                        pass
            elif "Reference ID" in line:
                # "Reference ID    : A9FEA97B (169.254.169.123)"
                if "(" in line:
                    source = line.split("(")[-1].rstrip(")")
            elif "Stratum" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    try:
                        stratum = int(parts[-1].strip())
                    except ValueError:
                        pass

        is_synced = source == self.AWS_TIME_SYNC_IP and stratum <= 4 and offset_ms < 10  # 10ms 미만이면 정상

        return TimeSyncStatus(
            is_synced=is_synced,
            offset_ms=offset_ms,
            source=source,
            stratum=stratum,
        )

    def _check_linux_ntpq(self) -> TimeSyncStatus:
        """Linux ntpq 결과 파싱 (chronyc 없을 때 폴백)."""
        try:
            result = subprocess.run(
                ["ntpq", "-p"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return TimeSyncStatus(
                    is_synced=False,
                    offset_ms=float("inf"),
                    source="unknown",
                    stratum=16,
                )

            # ntpq 출력 파싱
            # *peer 표시가 현재 동기화 중인 서버
            for line in result.stdout.strip().split("\n"):
                if line.startswith("*"):
                    parts = line.split()
                    if len(parts) >= 9:
                        source = parts[0][1:]  # * 제거
                        try:
                            offset_ms = abs(float(parts[8]))
                            stratum = int(parts[2])
                            return TimeSyncStatus(
                                is_synced=True,
                                offset_ms=offset_ms,
                                source=source,
                                stratum=stratum,
                            )
                        except ValueError:
                            pass

            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="no_sync_peer",
                stratum=16,
            )

        except Exception as e:
            logger.warning(f"[TimeSync] ntpq check failed: {e}")
            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="error",
                stratum=16,
            )

    def _check_windows_time(self) -> TimeSyncStatus:
        """Windows w32tm 결과 파싱."""
        try:
            result = subprocess.run(
                ["w32tm", "/query", "/status"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if result.returncode != 0:
                return TimeSyncStatus(
                    is_synced=False,
                    offset_ms=float("inf"),
                    source="unknown",
                    stratum=16,
                )

            return self._parse_w32tm_output(result.stdout)

        except FileNotFoundError:
            logger.debug("[TimeSync] w32tm not found")
            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="w32tm_not_found",
                stratum=16,
            )
        except Exception as e:
            logger.warning(f"[TimeSync] w32tm check failed: {e}")
            return TimeSyncStatus(
                is_synced=False,
                offset_ms=float("inf"),
                source="error",
                stratum=16,
            )

    def _parse_w32tm_output(self, output: str) -> TimeSyncStatus:
        """w32tm /query /status 출력 파싱."""
        source = ""
        stratum = 16
        offset_ms = float("inf")

        for line in output.strip().split("\n"):
            line = line.strip()
            if "Source:" in line or "소스:" in line:
                # Source: time.windows.com
                source = line.split(":")[-1].strip()
            elif "Stratum:" in line or "계층:" in line:
                try:
                    stratum = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
            elif "Phase Offset:" in line or "위상 오프셋:" in line:
                # Phase Offset: 0.0012345s
                try:
                    value = line.split(":")[-1].strip()
                    # 초 단위를 ms로 변환
                    value = value.rstrip("s").strip()
                    offset_ms = abs(float(value)) * 1000
                except ValueError:
                    pass

        is_synced = stratum <= 4 and offset_ms < 100  # Windows는 100ms 허용

        return TimeSyncStatus(
            is_synced=is_synced,
            offset_ms=offset_ms,
            source=source,
            stratum=stratum,
        )

    def get_clock_accuracy_ms(self) -> float:
        """
        현재 시계 정확도 (ms) 반환.

        Returns:
            오프셋 값 (ms). 낮을수록 정확.
        """
        status = self.check_sync_status()
        return status.offset_ms

    def is_accurate(self, tolerance_ms: float = 5.0) -> bool:
        """
        시계가 충분히 정확한지 확인.

        Args:
            tolerance_ms: 허용 오차 (ms). 기본값 5ms (AWS Time Sync 기준)

        Returns:
            True if 시계 오차가 tolerance 이내
        """
        status = self.check_sync_status()
        return status.is_synced and status.offset_ms <= tolerance_ms


@lru_cache(maxsize=1)
def get_time_sync_checker() -> TimeSyncChecker:
    """
    TimeSyncChecker 싱글톤 반환.

    Returns:
        TimeSyncChecker 인스턴스
    """
    return TimeSyncChecker()

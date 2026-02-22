"""
Time Sync Checker 테스트.

테스트 대상:
- TimeSyncStatus: 시간 동기화 상태 데이터클래스
- TimeSyncChecker: 시간 동기화 검사기
"""

from unittest import mock

from selfhealing.multiregion.time_sync import (
    TimeSyncChecker,
    TimeSyncStatus,
)


class TestTimeSyncStatus:
    """TimeSyncStatus 데이터클래스 테스트."""

    def test_create_status(self) -> None:
        """상태 생성."""
        status = TimeSyncStatus(
            is_synced=True,
            offset_ms=0.5,
            source="169.254.169.123",
            stratum=1,
        )

        assert status.is_synced is True
        assert status.offset_ms == 0.5
        assert status.source == "169.254.169.123"
        assert status.stratum == 1

    def test_synced_false(self) -> None:
        """동기화 안됨 상태."""
        status = TimeSyncStatus(
            is_synced=False,
            offset_ms=100.0,
            source="unknown",
            stratum=16,
        )

        assert status.is_synced is False
        assert status.stratum == 16

    def test_aws_time_sync_source(self) -> None:
        """AWS Time Sync 소스."""
        status = TimeSyncStatus(
            is_synced=True,
            offset_ms=0.5,
            source="169.254.169.123",
            stratum=3,
        )

        assert status.source == "169.254.169.123"


class TestTimeSyncChecker:
    """TimeSyncChecker 테스트."""

    def test_parse_chronyc_output(self) -> None:
        """chronyc 결과 파싱."""
        checker = TimeSyncChecker()

        # chronyc tracking 출력
        chronyc_output = """Reference ID    : A9FEA97B (169.254.169.123)
Stratum         : 4
Ref time (UTC)  : Mon May 12 10:00:00 2025
System time     : 0.000001234 seconds fast of NTP time
Last offset     : +0.000001234 seconds
RMS offset      : 0.000001234 seconds
Frequency       : 12.345 ppm slow
Residual freq   : +0.001 ppm
Skew            : 0.123 ppm
Root delay      : 0.000123456 seconds
Root dispersion : 0.000123456 seconds
Update interval : 64.0 seconds
Leap status     : Normal"""

        status = checker._parse_chronyc_output(chronyc_output)

        assert status.is_synced is True
        assert status.source == "169.254.169.123"
        assert status.stratum == 4

    def test_get_clock_accuracy_ms(self) -> None:
        """시계 정확도 조회."""
        checker = TimeSyncChecker()

        with mock.patch.object(checker, "check_sync_status") as mock_check:
            mock_check.return_value = TimeSyncStatus(
                is_synced=True,
                offset_ms=2.5,
                source="169.254.169.123",
                stratum=3,
            )

            accuracy = checker.get_clock_accuracy_ms()

            assert accuracy == 2.5

    def test_is_using_aws_time_sync(self) -> None:
        """AWS Time Sync 소스 확인 (source 필드로)."""
        checker = TimeSyncChecker()

        with mock.patch.object(checker, "check_sync_status") as mock_check:
            mock_check.return_value = TimeSyncStatus(
                is_synced=True,
                offset_ms=0.5,
                source="169.254.169.123",
                stratum=3,
            )

            status = checker.check_sync_status()
            assert status.source == checker.AWS_TIME_SYNC_IP

    def test_not_using_aws_time_sync(self) -> None:
        """AWS Time Sync 미사용 (source 필드로)."""
        checker = TimeSyncChecker()

        with mock.patch.object(checker, "check_sync_status") as mock_check:
            mock_check.return_value = TimeSyncStatus(
                is_synced=True,
                offset_ms=0.5,
                source="pool.ntp.org",
                stratum=3,
            )

            status = checker.check_sync_status()
            assert status.source != checker.AWS_TIME_SYNC_IP

    def test_is_accurate(self) -> None:
        """is_accurate 메서드."""
        checker = TimeSyncChecker()

        with mock.patch.object(checker, "check_sync_status") as mock_check:
            # 정확도 충분
            mock_check.return_value = TimeSyncStatus(
                is_synced=True,
                offset_ms=2.0,
                source="169.254.169.123",
                stratum=3,
            )
            assert checker.is_accurate(tolerance_ms=5.0) is True

            # 정확도 부족
            mock_check.return_value = TimeSyncStatus(
                is_synced=True,
                offset_ms=10.0,
                source="169.254.169.123",
                stratum=3,
            )
            assert checker.is_accurate(tolerance_ms=5.0) is False

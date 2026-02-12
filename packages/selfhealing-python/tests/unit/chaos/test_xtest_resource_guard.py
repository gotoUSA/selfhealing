"""
X-Test Resource Guard 단위 테스트.

CPU/메모리 과부하 시 X-Test 차단 기능 검증.

테스트 케이스:
- test_cpu_threshold_blocks: CPU 80% 초과 시 차단
- test_memory_threshold_blocks: 메모리 85% 초과 시 차단
- test_below_threshold_allows: 임계값 미만 시 허용
- test_retry_after_value: 429 응답에 retry_after 포함
- test_cgroup_priority: 컨테이너 환경에서 cgroup 우선
- test_resource_check_disabled: 체크 비활성화 시 항상 허용
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestResourceGuardSettings:
    """ResourceGuardSettings 설정 테스트."""

    def test_default_cpu_threshold(self):
        """기본 CPU 임계값은 80%."""
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        settings = ResourceGuardSettings()
        assert settings.cpu_threshold == 80.0

    def test_default_memory_threshold(self):
        """기본 메모리 임계값은 85%."""
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        settings = ResourceGuardSettings()
        assert settings.memory_threshold == 85.0

    def test_default_enabled(self):
        """기본값으로 리소스 체크 활성화."""
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        settings = ResourceGuardSettings()
        assert settings.resource_check_enabled is True

    def test_default_retry_after(self):
        """기본 retry_after는 30초."""
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        settings = ResourceGuardSettings()
        assert settings.retry_after_seconds == 30

    def test_env_override(self):
        """환경변수로 설정 오버라이드."""
        from selfhealing.settings.resource_guard import ResourceGuardSettings

        with patch.dict(
            "os.environ",
            {
                "SELFHEALING_RESOURCE_GUARD_CPU_THRESHOLD": "90",
                "SELFHEALING_RESOURCE_GUARD_MEMORY_THRESHOLD": "95",
                "SELFHEALING_RESOURCE_GUARD_RESOURCE_CHECK_ENABLED": "false",
                "SELFHEALING_RESOURCE_GUARD_RETRY_AFTER_SECONDS": "60",
            },
        ):
            settings = ResourceGuardSettings()
            assert settings.cpu_threshold == 90.0
            assert settings.memory_threshold == 95.0
            assert settings.resource_check_enabled is False
            assert settings.retry_after_seconds == 60


class TestResourceGuard:
    """ResourceGuard 클래스 테스트."""

    def test_cpu_threshold_blocks(self):
        """CPU 80% 초과 시 차단."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # CPU 90%, 메모리 50% 시뮬레이션
        with (
            patch.object(guard, "_get_cpu_percent", return_value=90.0),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=50.0),
        ):

            result = guard.is_safe_for_chaos()

            assert result.is_safe is False
            assert result.cpu_percent == 90.0
            assert "CPU usage" in result.block_reason
            assert "80" in result.block_reason  # 임계값 언급

    def test_memory_threshold_blocks(self):
        """메모리 85% 초과 시 차단."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # CPU 50%, 메모리 90% 시뮬레이션
        with (
            patch.object(guard, "_get_cpu_percent", return_value=50.0),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=90.0),
        ):

            result = guard.is_safe_for_chaos()

            assert result.is_safe is False
            assert result.memory_percent == 90.0
            assert "Memory usage" in result.block_reason
            assert "85" in result.block_reason  # 임계값 언급

    def test_below_threshold_allows(self):
        """임계값 미만 시 허용."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # CPU 50%, 메모리 60% 시뮬레이션 (안전)
        with (
            patch.object(guard, "_get_cpu_percent", return_value=50.0),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=60.0),
        ):

            result = guard.is_safe_for_chaos()

            assert result.is_safe is True
            assert result.block_reason is None
            assert result.cpu_percent == 50.0
            assert result.memory_percent == 60.0

    def test_retry_after_value(self):
        """get_recommended_wait()는 설정된 retry_after 값 반환."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # 기본값 30초
        assert guard.get_recommended_wait() == 30

    def test_cgroup_priority(self):
        """cgroup 메트릭이 사용 가능하면 psutil보다 우선."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # cgroup에서 70% 반환, psutil에서 50% 반환
        with (
            patch.object(guard, "_get_cpu_percent", return_value=40.0),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=70.0),
            patch.object(guard, "_get_memory_percent_psutil", return_value=50.0),
        ):

            status = guard.get_resource_status()

            # cgroup 값(70%)이 사용되어야 함
            assert status.memory_percent == 70.0
            assert status.is_cgroup_available is True
            assert status.source == "cgroup"

    def test_psutil_fallback(self):
        """cgroup 미지원 시 psutil 폴백."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        # cgroup None (미지원), psutil 60% 반환
        with (
            patch.object(guard, "_get_cpu_percent", return_value=40.0),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=60.0),
        ):

            status = guard.get_resource_status()

            # psutil 값(60%)이 사용되어야 함
            assert status.memory_percent == 60.0
            assert status.is_cgroup_available is False
            assert status.source == "psutil"

    def test_resource_check_disabled(self):
        """리소스 체크 비활성화 시 항상 허용."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import (
            ResourceGuardSettings,
            reset_resource_guard_settings,
        )

        reset_resource_guard_settings()
        reset_resource_guard()

        # 설정에서 체크 비활성화
        with patch("selfhealing.services.chaos.safety_guard.resource_guard.get_resource_guard_settings") as mock_settings:
            mock_settings.return_value = ResourceGuardSettings(
                resource_check_enabled=False,
                cpu_threshold=80.0,
                memory_threshold=85.0,
                retry_after_seconds=30,
            )

            guard = ResourceGuard()

            # CPU/메모리 높아도 허용
            result = guard.is_safe_for_chaos()

            assert result.is_safe is True


class TestResourceCheckResult:
    """ResourceCheckResult 테스트."""

    def test_to_response_dict(self):
        """to_response_dict()는 429 응답 형식 반환."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceCheckResult,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()

        result = ResourceCheckResult(
            is_safe=False,
            cpu_percent=90.5,
            memory_percent=60.3,
            cpu_threshold=80.0,
            memory_threshold=85.0,
            block_reason="CPU usage 90.5% exceeds threshold 80%",
            source="psutil",
        )

        response_dict = result.to_response_dict()

        assert response_dict["error"] == "resource_overloaded"
        assert response_dict["cpu_percent"] == 90.5
        assert response_dict["memory_percent"] == 60.3
        assert response_dict["cpu_threshold"] == 80.0
        assert response_dict["memory_threshold"] == 85.0
        assert response_dict["retry_after"] == 30
        assert "CPU usage" in response_dict["message"]


class TestResourceGuardSingleton:
    """ResourceGuard 싱글톤 테스트."""

    def test_get_resource_guard_singleton(self):
        """get_resource_guard()는 동일 인스턴스 반환."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            get_resource_guard,
            reset_resource_guard,
        )

        reset_resource_guard()

        guard1 = get_resource_guard()
        guard2 = get_resource_guard()

        assert guard1 is guard2

    def test_reset_resource_guard(self):
        """reset_resource_guard()는 새 인스턴스 생성."""
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            get_resource_guard,
            reset_resource_guard,
        )

        guard1 = get_resource_guard()
        reset_resource_guard()
        guard2 = get_resource_guard()

        assert guard1 is not guard2


class TestResourceGuardExport:
    """services/chaos/safety_guard 패키지 export 테스트."""

    def test_export_from_package(self):
        """ResourceGuard가 패키지에서 export됨."""
        from selfhealing.services.chaos.safety_guard import (
            ResourceGuard,
            ResourceStatus,
            ResourceCheckResult,
            get_resource_guard,
            reset_resource_guard,
        )

        assert ResourceGuard is not None
        assert ResourceStatus is not None
        assert ResourceCheckResult is not None
        assert callable(get_resource_guard)
        assert callable(reset_resource_guard)


class TestResourceGuardCacheFallback:
    """_get_cpu_percent() 캐시 → psutil fallback 경로 테스트.

    220 구현에서 _get_cpu_percent()가 변경됨:
    1. SystemMetricsCache 캐시에서 값 조회 시도 (~0ms)
    2. 캐시 미가동 시 psutil.cpu_percent(interval=0.1) fallback (100ms)
    3. psutil도 실패 시 0.0 반환

    소스:
    - services/chaos/safety_guard/resource_guard.py L120-133
    - 220_SYSTEM_METRICS_CACHE_LAYER.md §4.2
    """

    def test_cache_running_returns_cache_value(self):
        """캐시 가동 중이면 캐시 값 반환 (~0ms).

        소스: resource_guard.py L126-127
            cache = get_system_metrics_cache()
            if cache.is_running():
                return cache.get_cpu_percent()
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = True
        mock_cache.get_cpu_percent.return_value = 42.5

        with patch(
            "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
            return_value=mock_cache,
        ):
            result = guard._get_cpu_percent()

        assert result == 42.5
        mock_cache.is_running.assert_called_once()
        mock_cache.get_cpu_percent.assert_called_once()

    def test_cache_not_running_falls_back_to_psutil(self):
        """캐시 미가동 시 psutil 직접 호출 fallback.

        소스: resource_guard.py L128-132
            except Exception:
                pass
            # Fallback: 직접 측정 (캐시 미가동 시)
            try:
                return psutil.cpu_percent(interval=0.1)
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = False

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                return_value=mock_cache,
            ),
            patch(
                "selfhealing.services.chaos.safety_guard.resource_guard.psutil.cpu_percent",
                return_value=67.3,
            ) as mock_psutil_cpu,
        ):
            result = guard._get_cpu_percent()

        assert result == 67.3
        mock_cache.is_running.assert_called_once()
        mock_cache.get_cpu_percent.assert_not_called()
        mock_psutil_cpu.assert_called_once_with(interval=0.1)

    def test_cache_import_error_falls_back_to_psutil(self):
        """캐시 import 실패 시 psutil fallback.

        소스: resource_guard.py L123-128
            try:
                from selfhealing.services.system_metrics_cache import get_system_metrics_cache
                ...
            except Exception:
                pass
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                side_effect=ImportError("module not found"),
            ),
            patch(
                "selfhealing.services.chaos.safety_guard.resource_guard.psutil.cpu_percent",
                return_value=55.0,
            ) as mock_psutil_cpu,
        ):
            result = guard._get_cpu_percent()

        assert result == 55.0
        mock_psutil_cpu.assert_called_once_with(interval=0.1)

    def test_cache_exception_falls_back_to_psutil(self):
        """캐시 조회 중 예외 발생 시 psutil fallback.

        소스: resource_guard.py L123-128
            try:
                ...
                cache = get_system_metrics_cache()
                if cache.is_running():
                    return cache.get_cpu_percent()
            except Exception:
                pass
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = True
        mock_cache.get_cpu_percent.side_effect = RuntimeError("cache broken")

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                return_value=mock_cache,
            ),
            patch(
                "selfhealing.services.chaos.safety_guard.resource_guard.psutil.cpu_percent",
                return_value=30.0,
            ) as mock_psutil_cpu,
        ):
            result = guard._get_cpu_percent()

        assert result == 30.0
        mock_psutil_cpu.assert_called_once_with(interval=0.1)

    def test_both_cache_and_psutil_fail_returns_zero(self):
        """캐시 + psutil 모두 실패 시 0.0 반환.

        소스: resource_guard.py L131-133
            except Exception as e:
                logger.warning(f"[ResourceGuard] Failed to get CPU percent: {e}")
                return 0.0
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = False

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                return_value=mock_cache,
            ),
            patch(
                "selfhealing.services.chaos.safety_guard.resource_guard.psutil.cpu_percent",
                side_effect=OSError("psutil failed"),
            ),
        ):
            result = guard._get_cpu_percent()

        assert result == 0.0

    def test_cache_value_used_in_is_safe_for_chaos(self):
        """캐시 값이 is_safe_for_chaos()까지 전파되어 차단 판정에 사용.

        소스: resource_guard.py L198-230
            status = self.get_resource_status()
            ...
            if status.cpu_percent > settings.cpu_threshold: → block

        검증: 캐시에서 90.0 반환 → cpu_threshold(80) 초과 → is_safe=False
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = True
        mock_cache.get_cpu_percent.return_value = 90.0  # > 80% threshold

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                return_value=mock_cache,
            ),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=50.0),
        ):
            result = guard.is_safe_for_chaos()

        assert result.is_safe is False
        assert result.cpu_percent == 90.0
        assert "CPU usage" in result.block_reason

    def test_cache_below_threshold_allows_chaos(self):
        """캐시 값이 임계값 이하이면 X-Test 허용.

        검증: 캐시에서 40.0 반환 → cpu_threshold(80) 미만 → is_safe=True
        """
        from selfhealing.services.chaos.safety_guard.resource_guard import (
            ResourceGuard,
            reset_resource_guard,
        )
        from selfhealing.settings.resource_guard import reset_resource_guard_settings

        reset_resource_guard_settings()
        reset_resource_guard()

        guard = ResourceGuard()

        mock_cache = MagicMock()
        mock_cache.is_running.return_value = True
        mock_cache.get_cpu_percent.return_value = 40.0  # < 80% threshold

        with (
            patch(
                "selfhealing.services.system_metrics_cache.get_system_metrics_cache",
                return_value=mock_cache,
            ),
            patch.object(guard, "_get_memory_percent_cgroup", return_value=None),
            patch.object(guard, "_get_memory_percent_psutil", return_value=50.0),
        ):
            result = guard.is_safe_for_chaos()

        assert result.is_safe is True
        assert result.cpu_percent == 40.0
        assert result.block_reason is None

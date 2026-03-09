"""selfhealing 패키지 공개 API (__init__.py) 단위 테스트.

검증 대상:
- __all__: 13개 공개 심볼의 완전성 (계약)
- Lazy import (PEP 562): __getattr__ 기반 지연 로딩 및 캐싱 동작
- Eager import: CircuitState, FailedOperationData 즉시 가용
- Backward compatibility: 기존 깊은 경로 import 하위 호환성
- Lazy import isolation: CircuitState만 import 시 heavy 모듈 미로드
- Side-effect 부재: import 시 configure_structlog() 미호출
- py.typed: PEP 561 마커 파일 존재
- reset_structlog_config(): _configured 플래그 상태 전이
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# =============================================================================
# 계약 검증: __all__ 완전성, py.typed 존재
# =============================================================================


class TestPublicApiContract:
    """selfhealing 패키지 공개 API 설계 계약 검증."""

    def test_all_exports_exactly_thirteen_symbols(self):
        """__all__에 정확히 13개의 공개 심볼이 선언되어야 한다."""
        import selfhealing

        assert len(selfhealing.__all__) == 13

    def test_py_typed_marker_exists_for_pep561(self):
        """PEP 561 py.typed 마커 파일이 패키지 루트에 존재해야 한다."""
        import selfhealing

        package_dir = Path(selfhealing.__file__).resolve().parent
        py_typed = package_dir / "py.typed"
        assert py_typed.exists(), f"py.typed not found at {py_typed}"


# =============================================================================
# 동작 검증: Lazy import, Eager import
# =============================================================================


class TestPublicApiBehavior:
    """selfhealing 패키지 공개 API lazy/eager import 동작 검증."""

    def test_all_covers_every_lazy_and_eager_export(self):
        """__all__이 _LAZY_IMPORTS 키 + eager export 를 빠짐없이 포함해야 한다."""
        import selfhealing

        expected = set(selfhealing._LAZY_IMPORTS.keys()) | {
            "__version__",
            "CircuitState",
            "FailedOperationData",
        }
        assert set(selfhealing.__all__) == expected

    def test_eager_imports_available_directly_in_module_namespace(self):
        """CircuitState와 FailedOperationData는 모듈 dict에 즉시 존재한다."""
        import selfhealing

        module_dict = vars(selfhealing)
        assert "CircuitState" in module_dict
        assert "FailedOperationData" in module_dict

    def test_lazy_import_resolves_to_actual_source_class(self):
        """lazy import된 SelfHealingError가 소스 모듈의 원본 클래스와 동일 객체이다."""
        import selfhealing
        from selfhealing.core.exceptions import SelfHealingError as SourceClass

        assert selfhealing.SelfHealingError is SourceClass

    def test_lazy_import_caches_in_module_globals_after_first_access(self):
        """lazy import 첫 접근 후 모듈 globals에 캐싱되어 __getattr__을 우회한다."""
        import selfhealing

        # Given — globals에서 제거하여 미캐싱 상태 재현
        selfhealing.__dict__.pop("ProviderRegistry", None)
        assert "ProviderRegistry" not in vars(selfhealing)

        # When — 첫 접근으로 __getattr__ 트리거
        _ = selfhealing.ProviderRegistry

        # Then — globals에 캐싱됨
        assert "ProviderRegistry" in vars(selfhealing)

    def test_getattr_unknown_name_raises_attribute_error(self):
        """_LAZY_IMPORTS에 없는 이름 접근 시 모듈명이 포함된 AttributeError 발생."""
        import selfhealing

        with pytest.raises(
            AttributeError, match=r"has no attribute 'NonExistentSymbol'"
        ):
            _ = selfhealing.NonExistentSymbol

    def test_deep_path_import_backward_compatible(self):
        """기존 깊은 경로 import가 공개 API와 동일 객체를 반환하여 하위 호환성을 유지한다."""
        import selfhealing
        from selfhealing.factory import ProviderRegistry as DeepProviderRegistry
        from selfhealing.interfaces.repositories import (
            FailedOperationData as DeepFailedOperationData,
        )
        from selfhealing.services import (
            get_circuit_breaker_service as deep_get_cb,
        )
        from selfhealing.services.replay_service import (
            ReplayService as DeepReplayService,
        )

        assert selfhealing.ProviderRegistry is DeepProviderRegistry
        assert selfhealing.FailedOperationData is DeepFailedOperationData
        assert selfhealing.get_circuit_breaker_service is deep_get_cb
        assert selfhealing.ReplayService is DeepReplayService

    def test_lazy_import_does_not_load_heavy_modules_until_accessed(self):
        """CircuitState만 사용 시 factory, services 모듈이 로드되지 않아야 한다."""
        import selfhealing

        # Given — lazy 모듈 캐시를 제거하여 미로드 상태 재현
        for name in list(selfhealing.__dict__):
            if name in selfhealing._LAZY_IMPORTS:
                selfhealing.__dict__.pop(name, None)
        sys.modules.pop("selfhealing.factory", None)
        sys.modules.pop("selfhealing.services.replay_service", None)

        # When — eager import만 접근
        _ = selfhealing.CircuitState

        # Then — heavy 모듈이 sys.modules에 로드되지 않음
        assert "selfhealing.factory" not in sys.modules
        assert "selfhealing.services.replay_service" not in sys.modules


# =============================================================================
# 동작 검증: Side-effect 부재
# =============================================================================


class TestPublicApiSideEffectBehavior:
    """selfhealing 패키지 import 시 부수효과 부재 검증."""

    @pytest.fixture(autouse=True)
    def _reset_structlog(self):
        """structlog 설정 플래그를 테스트 전후로 리셋."""
        from selfhealing.observability import structlog_config

        structlog_config.reset_structlog_config()
        yield
        structlog_config.reset_structlog_config()

    def test_import_selfhealing_does_not_trigger_structlog_configure(self):
        """selfhealing 패키지 import가 configure_structlog() 부수효과를 유발하지 않는다."""
        # When — 패키지 리로드 (모듈 레벨 코드 재실행)
        import selfhealing
        from selfhealing.observability import structlog_config

        importlib.reload(selfhealing)

        # Then — _configured가 False → configure_structlog()이 호출되지 않았음
        assert structlog_config._configured is False


# =============================================================================
# 동작 검증: reset_structlog_config() 상태 전이
# =============================================================================


class TestResetStructlogConfigBehavior:
    """reset_structlog_config() 상태 전이 동작 검증."""

    @pytest.fixture(autouse=True)
    def _reset_structlog(self):
        """structlog 설정 플래그를 테스트 후 복원."""
        yield
        from selfhealing.observability import structlog_config

        structlog_config.reset_structlog_config()

    def test_reset_structlog_config_transitions_configured_true_to_false(self):
        """_configured=True → reset_structlog_config() → _configured=False 상태 전이."""
        from selfhealing.observability import structlog_config

        # Given — 설정 완료 상태
        structlog_config._configured = True

        # When
        structlog_config.reset_structlog_config()

        # Then
        assert structlog_config._configured is False

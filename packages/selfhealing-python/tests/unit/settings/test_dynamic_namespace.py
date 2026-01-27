"""
동적 Namespace 단위 테스트.

X-Test-Mode 활성화 시 Redis 키 프리픽스 동적 변경 기능을 검증합니다.
"""

import pytest

from selfhealing.core.test_mode_context import TestModeContext
from selfhealing.settings.namespace import (
    NamespaceSettings,
    get_effective_key_prefix,
    get_key_prefix,
    reset_namespace_settings,
    SYNTHETIC_KEY_PREFIX,
)


@pytest.fixture(autouse=True)
def reset_settings():
    """각 테스트 후 설정 초기화."""
    yield
    reset_namespace_settings()


class TestGetEffectiveKeyPrefix:
    """get_effective_key_prefix 함수 테스트."""

    def test_default_prefix_without_synthetic(self):
        """합성 모드가 아닐 때 기본 프리픽스 반환."""
        prefix = get_effective_key_prefix()
        assert prefix == "selfhealing:"
        assert not prefix.startswith(SYNTHETIC_KEY_PREFIX)

    def test_synthetic_prefix_with_synthetic_mode(self):
        """합성 모드일 때 xtest: 프리픽스 추가."""
        with TestModeContext.start():
            prefix = get_effective_key_prefix()
            assert prefix.startswith(f"{SYNTHETIC_KEY_PREFIX}:")
            assert prefix == "xtest:selfhealing:"

    def test_custom_base_prefix(self):
        """커스텀 base_prefix 적용."""
        prefix = get_effective_key_prefix(base_prefix="custom")
        assert prefix == "custom:"
        
        with TestModeContext.start():
            prefix = get_effective_key_prefix(base_prefix="custom")
            assert prefix == "xtest:custom:"

    def test_prefix_restored_after_context_exit(self):
        """컨텍스트 종료 후 프리픽스 복원."""
        original = get_effective_key_prefix()
        
        with TestModeContext.start():
            synthetic = get_effective_key_prefix()
            assert synthetic != original
        
        after = get_effective_key_prefix()
        assert after == original


class TestNamespaceSettingsKeyPrefix:
    """NamespaceSettings.get_key_prefix 테스트."""

    def test_namespace_disabled_returns_base_only(self):
        """namespace_enabled=False 시 기본 프리픽스만 반환."""
        settings = NamespaceSettings(namespace_enabled=False)
        prefix = settings.get_key_prefix()
        assert prefix == "selfhealing:"

    def test_namespace_enabled_with_namespace(self):
        """namespace_enabled=True + namespace 설정 시."""
        settings = NamespaceSettings(
            namespace_enabled=True,
            namespace="production"
        )
        prefix = settings.get_key_prefix()
        assert prefix == "selfhealing:production:"

    def test_namespace_enabled_with_region(self):
        """namespace_enabled=True + region 설정 시."""
        settings = NamespaceSettings(
            namespace_enabled=True,
            region="seoul"
        )
        prefix = settings.get_key_prefix()
        assert prefix == "selfhealing:seoul:"


class TestDynamicPrefixIntegration:
    """동적 프리픽스 통합 테스트."""

    def test_key_format_in_production_mode(self):
        """운영 모드에서 키 형식 확인."""
        prefix = get_effective_key_prefix()
        dlq_key = f"{prefix}dlq:pending"
        assert dlq_key == "selfhealing:dlq:pending"
        assert "xtest" not in dlq_key

    def test_key_format_in_synthetic_mode(self):
        """합성 모드에서 키 형식 확인."""
        with TestModeContext.start(session_id="test-123"):
            prefix = get_effective_key_prefix()
            dlq_key = f"{prefix}dlq:pending"
            assert dlq_key == "xtest:selfhealing:dlq:pending"
            assert dlq_key.startswith("xtest:")

    def test_keys_are_isolated_between_modes(self):
        """운영 모드와 합성 모드의 키가 분리됨."""
        prod_prefix = get_effective_key_prefix()
        
        with TestModeContext.start():
            synth_prefix = get_effective_key_prefix()
        
        # 서로 다른 프리픽스
        assert prod_prefix != synth_prefix
        
        # 합성 프리픽스가 더 긴 패턴
        assert synth_prefix.startswith(SYNTHETIC_KEY_PREFIX)
        assert prod_prefix in synth_prefix

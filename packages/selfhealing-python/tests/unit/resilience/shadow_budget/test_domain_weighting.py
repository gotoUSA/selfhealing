"""
Domain SLA 기반 가중치 테스트.
"""

import selfhealing.settings

import pytest


class TestDomainSLAWeightConstants:
    """Domain 상수 정의 테스트."""

    def test_default_sla_hours_defined(self):
        """DEFAULT_SLA_HOURS가 24로 정의되어 있어야 함."""
        from selfhealing.services.error_budget.reconciliation.shadow_calculator import (
            DEFAULT_SLA_HOURS,
        )

        assert DEFAULT_SLA_HOURS == 24


class TestDomainWeighting:
    """Domain SLA 기반 가중치 테스트."""

    def test_unknown_domain_returns_base_weight(self, shadow_calculator):
        """알 수 없는 도메인은 기본 가중치(1.0) 반환."""
        weight = shadow_calculator._get_domain_weight("unknown")
        # DEFAULT_SLA_HOURS / DEFAULT_SLA_HOURS = 1.0
        assert weight == pytest.approx(1.0, rel=1e-6)

    def test_domain_weight_with_mocked_config(self, shadow_calculator, monkeypatch):
        """설정된 도메인은 SLA 기반 가중치 반환."""
        from dataclasses import dataclass, field

        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(
                default_factory=lambda: {
                    "payment": 1,  # 1시간 SLA → 24배
                    "order": 2,  # 2시간 SLA → 12배
                    "notification": 24,  # 24시간 SLA → 1배
                }
            )

        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)

        monkeypatch.setattr(
            selfhealing.settings,
            "get_config",
            lambda: MockConfig(),
        )

        # Payment: 24 / 1 = 24배
        assert shadow_calculator._get_domain_weight("payment") == pytest.approx(24.0, rel=1e-6)

        # Order: 24 / 2 = 12배
        assert shadow_calculator._get_domain_weight("order") == pytest.approx(12.0, rel=1e-6)

        # Notification: 24 / 24 = 1배
        assert shadow_calculator._get_domain_weight("notification") == pytest.approx(1.0, rel=1e-6)

    def test_domain_weight_case_insensitive(self, shadow_calculator, monkeypatch):
        """도메인 이름은 대소문자 구분 없음."""
        from dataclasses import dataclass, field

        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})

        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)

        monkeypatch.setattr(
            selfhealing.settings,
            "get_config",
            lambda: MockConfig(),
        )

        # 소문자/대문자 모두 동일한 가중치
        assert shadow_calculator._get_domain_weight("Payment") == pytest.approx(24.0, rel=1e-6)
        assert shadow_calculator._get_domain_weight("PAYMENT") == pytest.approx(24.0, rel=1e-6)

    def test_domain_weight_config_failure_returns_default(self, shadow_calculator, monkeypatch):
        """설정 조회 실패 시 기본값 1.0 반환."""

        def raise_error():
            raise RuntimeError("Config unavailable")

        monkeypatch.setattr(
            selfhealing.settings,
            "get_config",
            raise_error,
        )

        weight = shadow_calculator._get_domain_weight("payment")
        assert weight == 1.0


class TestDomainWeightIntegration:
    """Domain 가중치 통합 테스트."""

    def test_calculate_weighted_errors_with_domain(self, shadow_calculator, monkeypatch):
        """_calculate_weighted_errors에 domain 적용."""
        from dataclasses import dataclass, field

        @dataclass
        class MockSLAConfig:
            thresholds_by_domain: dict = field(default_factory=lambda: {"payment": 1})

        @dataclass
        class MockConfig:
            sla: MockSLAConfig = field(default_factory=MockSLAConfig)

        monkeypatch.setattr(
            selfhealing.settings,
            "get_config",
            lambda: MockConfig(),
        )

        # medium 10개 * 0.001 * prometheus(1.0) * payment(24) = 0.24분
        result = shadow_calculator._calculate_weighted_errors(
            errors_by_severity={"medium": 10},
            log_source="prometheus",
            domain="payment",
        )
        assert result == pytest.approx(0.24, rel=1e-3)

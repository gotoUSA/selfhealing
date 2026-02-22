"""
tier_mapping 유틸리티 단위 테스트.

ServiceConfig.criticality ↔ AdaptiveThrottle tier_id 매핑 검증.

테스트 대상:
1. CRITICALITY_TO_TIER 매핑 딕셔너리 완전성
2. TIER_TO_CRITICALITY 매핑 딕셔너리 완전성
3. get_tier_from_criticality() 정상/미지 입력
4. get_criticality_from_tier() 정상/미지 입력
5. VALID_TIER_IDS 집합 정합성
6. ServiceConfig valid_levels와의 일관성
"""

import logging

import pytest

from selfhealing.services.throttle.tier_mapping import (
    _DEFAULT_CRITICALITY,
    _DEFAULT_TIER,
    CRITICALITY_TO_TIER,
    TIER_TO_CRITICALITY,
    VALID_TIER_IDS,
    get_criticality_from_tier,
    get_tier_from_criticality,
)


class TestCriticalityToTierMapping:
    """CRITICALITY_TO_TIER 딕셔너리 검증."""

    def test_critical_maps_to_critical(self):
        """criticality 'critical' → tier 'critical'."""
        assert CRITICALITY_TO_TIER["critical"] == "critical"

    def test_high_maps_to_critical(self):
        """criticality 'high' → tier 'critical' (보호 대상)."""
        assert CRITICALITY_TO_TIER["high"] == "critical"

    def test_medium_maps_to_standard(self):
        """criticality 'medium' → tier 'standard'."""
        assert CRITICALITY_TO_TIER["medium"] == "standard"

    def test_low_maps_to_non_essential(self):
        """criticality 'low' → tier 'non_essential'."""
        assert CRITICALITY_TO_TIER["low"] == "non_essential"

    def test_covers_all_service_config_valid_levels(self):
        """ServiceConfig.__post_init__()의 valid_levels와 키가 일치."""

        # ServiceConfig.__post_init__에서 검증하는 valid_levels 기준
        valid_levels = {"critical", "high", "medium", "low"}
        assert set(CRITICALITY_TO_TIER.keys()) == valid_levels

    def test_all_values_are_valid_tier_ids(self):
        """매핑 결과가 모두 VALID_TIER_IDS에 포함."""
        for criticality, tier in CRITICALITY_TO_TIER.items():
            assert tier in VALID_TIER_IDS, f"CRITICALITY_TO_TIER['{criticality}'] = '{tier}' " f"is not in VALID_TIER_IDS"


class TestTierToCriticalityMapping:
    """TIER_TO_CRITICALITY 딕셔너리 검증."""

    def test_critical_maps_to_critical(self):
        """tier 'critical' → criticality 'critical'."""
        assert TIER_TO_CRITICALITY["critical"] == "critical"

    def test_standard_maps_to_medium(self):
        """tier 'standard' → criticality 'medium'."""
        assert TIER_TO_CRITICALITY["standard"] == "medium"

    def test_non_essential_maps_to_low(self):
        """tier 'non_essential' → criticality 'low'."""
        assert TIER_TO_CRITICALITY["non_essential"] == "low"

    def test_covers_all_valid_tier_ids(self):
        """VALID_TIER_IDS의 모든 tier가 매핑에 포함."""
        assert set(TIER_TO_CRITICALITY.keys()) == VALID_TIER_IDS


class TestGetTierFromCriticality:
    """get_tier_from_criticality() 변환 함수 검증."""

    @pytest.mark.parametrize(
        "criticality, expected_tier",
        [
            ("critical", "critical"),
            ("high", "critical"),
            ("medium", "standard"),
            ("low", "non_essential"),
        ],
    )
    def test_known_criticality_returns_correct_tier(self, criticality, expected_tier):
        """알려진 criticality에 대해 올바른 tier_id 반환."""
        assert get_tier_from_criticality(criticality) == expected_tier

    def test_case_insensitive(self):
        """대소문자 구분하지 않음."""
        assert get_tier_from_criticality("Critical") == "critical"
        assert get_tier_from_criticality("HIGH") == "critical"
        assert get_tier_from_criticality("Medium") == "standard"
        assert get_tier_from_criticality("LOW") == "non_essential"

    def test_unknown_criticality_returns_default_tier(self):
        """미지의 criticality → _DEFAULT_TIER 반환."""
        assert get_tier_from_criticality("unknown") == _DEFAULT_TIER

    def test_unknown_criticality_logs_warning(self, caplog):
        """미지의 criticality → 경고 로그 출력."""
        with caplog.at_level(logging.WARNING, logger="selfhealing.services.throttle.tier_mapping"):
            get_tier_from_criticality("unknown_value")

        assert any("unknown_criticality" in record.message for record in caplog.records)


class TestGetCriticalityFromTier:
    """get_criticality_from_tier() 변환 함수 검증."""

    @pytest.mark.parametrize(
        "tier_id, expected_criticality",
        [
            ("critical", "critical"),
            ("standard", "medium"),
            ("non_essential", "low"),
        ],
    )
    def test_known_tier_returns_correct_criticality(self, tier_id, expected_criticality):
        """알려진 tier_id에 대해 올바른 criticality 반환."""
        assert get_criticality_from_tier(tier_id) == expected_criticality

    def test_case_insensitive(self):
        """대소문자 구분하지 않음."""
        assert get_criticality_from_tier("Critical") == "critical"
        assert get_criticality_from_tier("STANDARD") == "medium"
        assert get_criticality_from_tier("NON_ESSENTIAL") == "low"

    def test_unknown_tier_returns_default_criticality(self):
        """미지의 tier_id → _DEFAULT_CRITICALITY 반환."""
        assert get_criticality_from_tier("unknown") == _DEFAULT_CRITICALITY

    def test_unknown_tier_logs_warning(self, caplog):
        """미지의 tier_id → 경고 로그 출력."""
        with caplog.at_level(logging.WARNING, logger="selfhealing.services.throttle.tier_mapping"):
            get_criticality_from_tier("unknown_tier")

        assert any("unknown_falling_back" in record.message for record in caplog.records)


class TestValidTierIds:
    """VALID_TIER_IDS 상수 검증."""

    def test_contains_expected_values(self):
        """PROTECTED_TIERS_ON_429의 'critical' 포함 확인."""
        from selfhealing.services.throttle.adaptive import PROTECTED_TIERS_ON_429

        assert PROTECTED_TIERS_ON_429.issubset(VALID_TIER_IDS)

    def test_matches_tier_to_criticality_keys(self):
        """TIER_TO_CRITICALITY 키와 동일."""
        assert VALID_TIER_IDS == set(TIER_TO_CRITICALITY.keys())


class TestCrossLayerTierConsistency:
    """
    tier_mapping.VALID_TIER_IDS ↔ tiering.DEFAULT_TIER_DEFINITIONS 일관성 검증.

    서비스 레이어(tier_mapping.py)와 Django API 레이어(tiering/defaults.py)가
    동일한 tier ID 집합을 사용하는지 보장한다.
    """

    @classmethod
    def setup_class(cls):
        """Django API 레이어 import를 위한 최소 Django 설정."""
        import os

        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "myproject.settings.test")

        import django

        django.setup()

        # pytest-django _dj_autoclear_mailbox fixture가 mail.outbox를 참조
        from django.core import mail

        if not hasattr(mail, "outbox"):
            mail.outbox = []

    def test_valid_tier_ids_matches_default_tier_definitions(self):
        """
        VALID_TIER_IDS == {td.id for td in DEFAULT_TIER_DEFINITIONS}.

        한쪽에 tier가 추가/삭제되면 이 테스트가 실패하여 불일치를 감지.
        """
        from selfhealing.api.django.tiering.defaults import DEFAULT_TIER_DEFINITIONS

        definition_ids = {td.id for td in DEFAULT_TIER_DEFINITIONS}
        assert VALID_TIER_IDS == definition_ids, (
            f"tier_mapping.VALID_TIER_IDS {VALID_TIER_IDS} != "
            f"DEFAULT_TIER_DEFINITIONS IDs {definition_ids}. "
            f"Missing in tier_mapping: {definition_ids - VALID_TIER_IDS}, "
            f"Extra in tier_mapping: {VALID_TIER_IDS - definition_ids}"
        )

    def test_criticality_to_tier_values_match_definitions(self):
        """
        CRITICALITY_TO_TIER의 모든 target tier_id가 DEFAULT_TIER_DEFINITIONS에 정의됨.
        """
        from selfhealing.api.django.tiering.defaults import DEFAULT_TIER_DEFINITIONS

        definition_ids = {td.id for td in DEFAULT_TIER_DEFINITIONS}
        for criticality, tier_id in CRITICALITY_TO_TIER.items():
            assert tier_id in definition_ids, (
                f"CRITICALITY_TO_TIER['{criticality}'] = '{tier_id}' " f"is not defined in DEFAULT_TIER_DEFINITIONS"
            )

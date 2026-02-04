"""
Service Locality Router 테스트.

테스트 대상:
- LocalityRule: 라우팅 규칙 데이터클래스
- ServiceLocalityRouter: 서비스 지역성 라우터
"""

import pytest

from selfhealing.multiregion.config import reset_multiregion_settings
from selfhealing.multiregion.router import (
    LocalityRule,
    ServiceLocalityRouter,
    reset_locality_router,
)


class TestLocalityRule:
    """LocalityRule 데이터클래스 테스트."""

    def test_create_rule(self) -> None:
        """규칙 생성."""
        rule = LocalityRule(
            pattern=r"^cb:payment_kakao.*",
            preferred_region="ap-northeast-2",
            description="한국 결제 서비스 CB",
        )

        assert rule.pattern == r"^cb:payment_kakao.*"
        assert rule.preferred_region == "ap-northeast-2"
        assert rule.description == "한국 결제 서비스 CB"

    def test_defaults(self) -> None:
        """기본값 확인."""
        rule = LocalityRule(
            pattern=r"^test:.*",
            preferred_region="us-east-1",
        )

        assert rule.description == ""


class TestServiceLocalityRouter:
    """ServiceLocalityRouter 클래스 테스트."""

    def setup_method(self) -> None:
        """테스트 전 리셋."""
        reset_multiregion_settings()
        reset_locality_router()

    def teardown_method(self) -> None:
        """테스트 후 리셋."""
        reset_multiregion_settings()
        reset_locality_router()

    def test_init_with_default_rules(self) -> None:
        """기본 규칙으로 초기화."""
        router = ServiceLocalityRouter()

        # 기본 규칙이 존재해야 함
        rules = router.get_rules_summary()
        assert len(rules) > 0

    def test_init_with_custom_rules(self) -> None:
        """커스텀 규칙으로 초기화."""
        custom_rules = [
            LocalityRule(
                pattern=r"^custom:.*",
                preferred_region="eu-west-1",
            ),
        ]
        router = ServiceLocalityRouter(rules=custom_rules)

        rules = router.get_rules_summary()
        assert len(rules) == 1
        assert rules[0]["pattern"] == r"^custom:.*"

    def test_add_rule(self) -> None:
        """규칙 추가."""
        # 기본 규칙으로 시작
        router = ServiceLocalityRouter()
        initial_count = len(router.get_rules_summary())

        rule = LocalityRule(
            pattern=r"^test:.*",
            preferred_region="us-west-2",
        )
        router.add_rule(rule)

        rules = router.get_rules_summary()
        assert len(rules) == initial_count + 1
        assert rules[-1]["pattern"] == r"^test:.*"

    def test_remove_rule(self) -> None:
        """규칙 제거."""
        rules = [
            LocalityRule(pattern=r"^test1:.*", preferred_region="us-east-1"),
            LocalityRule(pattern=r"^test2:.*", preferred_region="us-west-2"),
        ]
        router = ServiceLocalityRouter(rules=rules)

        result = router.remove_rule(r"^test1:.*")

        assert result is True
        remaining = router.get_rules_summary()
        assert len(remaining) == 1
        assert remaining[0]["pattern"] == r"^test2:.*"

    def test_remove_nonexistent_rule(self) -> None:
        """존재하지 않는 규칙 제거."""
        router = ServiceLocalityRouter(rules=[])

        result = router.remove_rule(r"^nonexistent:.*")

        assert result is False

    def test_get_preferred_region(self) -> None:
        """선호 리전 조회."""
        rules = [
            LocalityRule(
                pattern=r"^cb:payment_kakao.*",
                preferred_region="ap-northeast-2",
            ),
        ]
        router = ServiceLocalityRouter(rules=rules)

        preferred = router.get_preferred_region("cb:payment_kakao:main")

        assert preferred == "ap-northeast-2"

    def test_get_preferred_region_no_match(self) -> None:
        """매칭 없으면 None."""
        router = ServiceLocalityRouter(rules=[])

        preferred = router.get_preferred_region("unknown:key")

        assert preferred is None

    def test_should_write_locally_true(self) -> None:
        """현재 리전이 담당이면 True."""
        rules = [
            LocalityRule(
                pattern=r"^cb:payment_kakao.*",
                preferred_region="ap-northeast-2",
            ),
        ]
        router = ServiceLocalityRouter(
            rules=rules,
            current_region="ap-northeast-2",
        )

        result = router.should_write_locally("cb:payment_kakao:main")

        assert result is True

    def test_should_write_locally_false(self) -> None:
        """현재 리전이 담당 아니면 False."""
        rules = [
            LocalityRule(
                pattern=r"^cb:payment_kakao.*",
                preferred_region="ap-northeast-2",
            ),
        ]
        router = ServiceLocalityRouter(
            rules=rules,
            current_region="us-east-1",
        )

        result = router.should_write_locally("cb:payment_kakao:main")

        assert result is False

    def test_should_write_locally_no_rule(self) -> None:
        """규칙 없으면 항상 True."""
        router = ServiceLocalityRouter(
            rules=[],
            current_region="us-east-1",
        )

        result = router.should_write_locally("unknown:key")

        assert result is True

    def test_get_write_region(self) -> None:
        """쓰기 담당 리전 조회."""
        rules = [
            LocalityRule(
                pattern=r"^cb:payment_stripe.*",
                preferred_region="us-east-1",
            ),
        ]
        router = ServiceLocalityRouter(
            rules=rules,
            current_region="ap-northeast-2",
        )

        write_region = router.get_write_region("cb:payment_stripe:main")

        assert write_region == "us-east-1"

    def test_get_write_region_no_match(self) -> None:
        """매칭 없으면 현재 리전."""
        router = ServiceLocalityRouter(
            rules=[],
            current_region="ap-northeast-2",
        )

        write_region = router.get_write_region("unknown:key")

        assert write_region == "ap-northeast-2"

    def test_match_rule(self) -> None:
        """규칙 매칭."""
        rules = [
            LocalityRule(
                pattern=r"^cb:payment_kakao.*",
                preferred_region="ap-northeast-2",
                description="카카오페이",
            ),
        ]
        router = ServiceLocalityRouter(rules=rules)

        matched = router.match_rule("cb:payment_kakao:main")

        assert matched is not None
        assert matched.preferred_region == "ap-northeast-2"
        assert matched.description == "카카오페이"

    def test_match_rule_none(self) -> None:
        """매칭 없으면 None."""
        router = ServiceLocalityRouter(rules=[])

        matched = router.match_rule("unknown:key")

        assert matched is None

    def test_default_korean_payment_rule(self) -> None:
        """기본 한국 결제 규칙."""
        router = ServiceLocalityRouter()

        # 카카오페이 → 한국 리전
        preferred = router.get_preferred_region("cb:payment_kakao_main")
        assert preferred == "ap-northeast-2"

        # 토스페이 → 한국 리전
        preferred = router.get_preferred_region("cb:payment_toss_main")
        assert preferred == "ap-northeast-2"

    def test_default_global_payment_rule(self) -> None:
        """기본 글로벌 결제 규칙."""
        router = ServiceLocalityRouter()

        # Stripe → 미국 리전
        preferred = router.get_preferred_region("cb:payment_stripe_main")
        assert preferred == "us-east-1"

        # PayPal → 미국 리전
        preferred = router.get_preferred_region("cb:payment_paypal_main")
        assert preferred == "us-east-1"

    def test_set_current_region(self) -> None:
        """현재 리전 설정."""
        router = ServiceLocalityRouter(current_region="ap-northeast-2")

        assert router.get_current_region() == "ap-northeast-2"

        router.set_current_region("us-east-1")

        assert router.get_current_region() == "us-east-1"

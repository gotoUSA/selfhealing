"""
Service-based Write Locality Router.

서비스별로 담당 리전을 지정하여 쓰기 충돌을 최소화합니다.

핵심 개념:
- 특정 서비스의 CB 상태는 해당 서비스와 가장 가까운 리전에서 관리
- 예: 카카오페이 CB → 한국 리전 담당, Stripe CB → 미국 리전 담당
- 담당 리전에서만 쓰기를 하고, 다른 리전은 복제된 데이터를 읽기만 함
- 충돌 원천 차단
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from selfhealing.multiregion.config import get_multiregion_settings

logger = logging.getLogger(__name__)


@dataclass
class LocalityRule:
    """
    Locality 규칙.

    키 패턴과 담당 리전을 매핑합니다.

    Attributes:
        pattern: 키 패턴 (정규식)
        preferred_region: 선호 리전
        description: 규칙 설명
    """

    pattern: str
    """키 패턴 (정규식)."""

    preferred_region: str
    """선호 리전."""

    description: str = ""
    """규칙 설명."""


class ServiceLocalityRouter:
    """
    서비스 기반 Write-Locality 라우터.

    서비스별로 담당 리전을 지정하여 쓰기 충돌을 최소화합니다.

    왜 이렇게 하나요?
    문제 상황: 양쪽 리전에서 동시에 CB 상태 변경
    - 한국 리전: "카카오페이 장애 감지! cb:payment_kakao → OPEN"
    - 미국 리전: "카카오페이 정상! cb:payment_kakao → CLOSED"
    → LWW로 하나가 덮어씌워짐 → 잘못된 상태!

    해결: 카카오페이 CB는 한국 리전만 쓰기 담당
    → 충돌 원천 차단
    → 미국 리전은 복제된 상태를 읽기만 함

    사용 예:
        router = ServiceLocalityRouter()

        # 카카오페이 CB → 한국 리전 담당
        if router.should_write_locally("cb:payment_kakao"):
            # 현재 리전이 한국이면 쓰기 진행
            cb_service.update_state("payment_kakao", "OPEN")
        else:
            # 현재 리전이 미국이면 쓰기 스킵 (복제로 받음)
            logger.debug("Skipping write: not the preferred region")
    """

    # 기본 Locality 규칙
    DEFAULT_RULES: list[LocalityRule] = [
        # 한국 결제 서비스 → 한국 리전
        LocalityRule(
            pattern=r"^cb:payment_(kakao|toss|naverpay|samsung).*",
            preferred_region="ap-northeast-2",
            description="한국 결제 서비스 CB",
        ),
        # 글로벌 결제 서비스 → 미국 리전
        LocalityRule(
            pattern=r"^cb:payment_(stripe|paypal|braintree).*",
            preferred_region="us-east-1",
            description="글로벌 결제 서비스 CB",
        ),
        # 글로벌 알림 서비스 → 미국 리전
        LocalityRule(
            pattern=r"^cb:(notification_slack|notification_pagerduty).*",
            preferred_region="us-east-1",
            description="글로벌 알림 서비스 CB",
        ),
        # Emergency 상태 → Primary 리전만
        LocalityRule(
            pattern=r"^selfhealing:.*:emergency.*",
            preferred_region="ap-northeast-2",  # Primary
            description="Emergency 상태",
        ),
    ]

    def __init__(
        self,
        rules: list[LocalityRule] | None = None,
        current_region: str | None = None,
    ):
        """
        초기화.

        Args:
            rules: Locality 규칙 목록 (None이면 기본 규칙)
            current_region: 현재 리전 (None이면 설정에서 가져옴)
        """
        self._rules = list(rules) if rules else list(self.DEFAULT_RULES)
        self._settings = get_multiregion_settings()
        self._current_region = current_region or self._settings.current_region

        # 정규식 컴파일 캐시
        self._compiled_patterns: dict[str, re.Pattern[str]] = {}
        for rule in self._rules:
            self._compiled_patterns[rule.pattern] = re.compile(rule.pattern)

    def get_preferred_region(self, key: str) -> str | None:
        """
        키에 대한 선호 리전 반환.

        Args:
            key: Redis 키

        Returns:
            선호 리전 (매칭 규칙 없으면 None)
        """
        for rule in self._rules:
            pattern = self._compiled_patterns.get(rule.pattern)
            if pattern and pattern.match(key):
                return rule.preferred_region
        return None

    def should_write_locally(self, key: str) -> bool:
        """
        현재 리전에서 쓰기해야 하는지 확인.

        Args:
            key: Redis 키

        Returns:
            True: 현재 리전에서 쓰기
            False: 다른 리전이 담당 (복제로 받음)
        """
        preferred = self.get_preferred_region(key)

        if preferred is None:
            # 규칙 없으면 어디서든 쓰기 가능
            return True

        return preferred == self._current_region

    def get_write_region(self, key: str) -> str:
        """
        쓰기 담당 리전 반환.

        Args:
            key: Redis 키

        Returns:
            담당 리전 (규칙 없으면 현재 리전)
        """
        preferred = self.get_preferred_region(key)
        return preferred or self._current_region

    def add_rule(self, rule: LocalityRule) -> None:
        """
        규칙 추가.

        Args:
            rule: 추가할 규칙
        """
        self._rules.append(rule)
        self._compiled_patterns[rule.pattern] = re.compile(rule.pattern)

    def remove_rule(self, pattern: str) -> bool:
        """
        규칙 제거.

        Args:
            pattern: 제거할 패턴

        Returns:
            True if 제거됨
        """
        for i, rule in enumerate(self._rules):
            if rule.pattern == pattern:
                del self._rules[i]
                if pattern in self._compiled_patterns:
                    del self._compiled_patterns[pattern]
                return True
        return False

    def get_rules_summary(self) -> list[dict[str, str]]:
        """
        규칙 요약 반환.

        Returns:
            규칙 요약 목록
        """
        return [
            {
                "pattern": rule.pattern,
                "preferred_region": rule.preferred_region,
                "description": rule.description,
            }
            for rule in self._rules
        ]

    def get_current_region(self) -> str:
        """현재 리전 반환."""
        return self._current_region

    def set_current_region(self, region: str) -> None:
        """현재 리전 설정 (테스트용)."""
        self._current_region = region

    def match_rule(self, key: str) -> LocalityRule | None:
        """
        키에 매칭되는 규칙 반환.

        Args:
            key: Redis 키

        Returns:
            매칭되는 규칙 또는 None
        """
        for rule in self._rules:
            pattern = self._compiled_patterns.get(rule.pattern)
            if pattern and pattern.match(key):
                return rule
        return None


# 싱글톤
_router: ServiceLocalityRouter | None = None


def get_locality_router() -> ServiceLocalityRouter:
    """
    ServiceLocalityRouter 싱글톤 반환.

    Returns:
        ServiceLocalityRouter 인스턴스
    """
    global _router
    if _router is None:
        _router = ServiceLocalityRouter()
    return _router


def reset_locality_router() -> None:
    """라우터 리셋 (테스트용)."""
    global _router
    _router = None

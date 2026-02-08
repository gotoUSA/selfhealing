"""
Emergency Mode Enums and Level Rules.

Defines emergency levels and their traffic rules.
"""

from __future__ import annotations

from enum import IntEnum


class EmergencyLevel(IntEnum):
    """
    비상 모드 레벨 정의.

    각 레벨은 티어별 트래픽 허용 배율을 결정합니다.
    """

    NORMAL = 0  # 정상 운영 (모든 트래픽 허용)
    LEVEL_1 = 1  # 경미한 장애 - Tier 3 (Non-Essential)만 차단
    LEVEL_2 = 2  # 중간 장애 - Tier 2, 3 차단, Tier 1은 100%
    LEVEL_3 = 3  # 심각한 장애 - Tier 1만 50% 허용


# 각 레벨별 티어 트래픽 배율 규칙
EMERGENCY_LEVEL_RULES: dict[EmergencyLevel, dict[str, float]] = {
    EmergencyLevel.NORMAL: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 1.0,
    },
    EmergencyLevel.LEVEL_1: {
        "critical": 1.0,
        "standard": 1.0,
        "non_essential": 0.0,  # Non-Essential 차단
    },
    EmergencyLevel.LEVEL_2: {
        "critical": 1.0,
        "standard": 0.1,  # Standard 10%만 허용
        "non_essential": 0.0,  # Non-Essential 차단
    },
    EmergencyLevel.LEVEL_3: {
        "critical": 0.5,  # Critical도 50%만 허용
        "standard": 0.0,  # Standard 차단
        "non_essential": 0.0,  # Non-Essential 차단
    },
}

"""
레거시 호환 모듈 - 예외 핸들러 재수출.

기존 import 경로 호환을 위해 exceptions.handler 모듈의 내용을 재수출합니다.

사용 예시:
    # 레거시 경로 (호환)
    from selfhealing.api.django.exception_handler import selfhealing_exception_handler

    # 권장 경로
    from selfhealing.api.django.exceptions.handler import selfhealing_exception_handler
"""

from selfhealing.api.django.exceptions.handler import (
    selfhealing_exception_handler,
)

__all__ = [
    "selfhealing_exception_handler",
]

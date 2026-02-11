"""
미들웨어 단위 테스트용 conftest.

Django 미들웨어 단위 테스트에서 JsonResponse 등이 동작하도록
최소한의 Django 설정만 구성합니다.
"""

import django
from django.conf import settings

if not settings.configured:
    settings.configure(
        DEFAULT_CHARSET="utf-8",
    )

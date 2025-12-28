"""
pytest configuration for load_tests.core module

Django 설정 없이 순수 Python 단위 테스트를 실행합니다.
"""
import sys
from pathlib import Path

# Django 자동 로드 방지
sys.modules['django'] = type(sys)('django')
sys.modules['django.conf'] = type(sys)('django.conf')

# conftest.py 에서 pytest 설정
def pytest_configure(config):
    """Django 설정 없이 테스트 실행"""
    pass

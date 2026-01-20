"""
Django Integration Tests Conftest.

Django/shopping 앱에 의존하는 테스트용 fixtures.
이 폴더의 테스트는 mock_external_services fixture를 자동으로 적용받습니다.
"""
import pytest


# =============================================================================
# Django 의존 테스트에 mock_external_services 자동 적용
# =============================================================================
@pytest.fixture(autouse=True)
def apply_mock_external_services(mock_external_services):
    """
    이 폴더의 모든 테스트에 mock_external_services 적용.
    
    상위 conftest.py의 mock_external_services를 자동으로 사용합니다.
    """
    pass  # fixture가 요청되면 자동으로 yield됨


# =============================================================================
# Django 테스트 마커 자동 적용
# =============================================================================
def pytest_collection_modifyitems(config, items):
    """
    이 폴더의 모든 테스트에 requires_db 마커 자동 적용.
    """
    for item in items:
        # 이 conftest가 적용되는 테스트에만 마커 추가
        if "self_healing/django" in str(item.fspath):
            if "requires_db" not in [m.name for m in item.iter_markers()]:
                item.add_marker(pytest.mark.requires_db)

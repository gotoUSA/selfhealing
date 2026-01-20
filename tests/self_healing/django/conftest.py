"""
Django Integration Tests Conftest.

Django/shopping 앱에 의존하는 테스트용 fixtures.

중요: 이 폴더의 테스트들은 실제 Django/DB/Redis 연결을 테스트합니다.
mock_external_services를 사용하지 않습니다 - 실제 외부 의존성 테스트가 목적입니다.

실행 방법:
    docker-compose -f docker-compose.test.yml exec web pytest tests/self_healing/django/ -v
"""
import pytest


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

"""
Django Integration Tests Conftest.

Django/shopping 앱에 의존하는 테스트용 fixtures.

중요: 이 폴더의 테스트들은 실제 Django/DB/Redis 연결을 테스트합니다.
mock_external_services를 사용하지 않습니다 - 실제 외부 의존성 테스트가 목적입니다.

실행 방법:
    docker-compose -f docker-compose.test.yml run --rm test-hybrid-storage \
        python -m pytest tests/self_healing/django/ -v --no-cov -n 0 -m ""

NOTE: 이 폴더의 테스트는 docker-compose 환경에서만 실행됩니다.
      DB/Redis가 항상 가용하므로 requires_db 마커를 사용하지 않습니다.
"""
import pytest

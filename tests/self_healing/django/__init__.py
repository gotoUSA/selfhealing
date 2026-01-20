"""
Django Integration Tests for Self-Healing.

이 폴더에는 Django 모델, shopping 앱, factories에 의존하는 테스트가 포함됩니다.
순수 selfhealing 패키지 테스트는 packages/selfhealing-python/tests/unit/에 있습니다.

Test Categories:
    - Circuit Breaker: Django 모델 기반 상태 관리
    - DLQ: FailedOperation 모델 테스트
    - Cost-Aware Recovery: shopping.services 연동
    - Security: SecurityIncident 모델 테스트
    - Retry: shopping.tasks 연동

Required Fixtures:
    - mock_external_services: conftest.py에서 autouse로 적용
    - pytest.mark.django_db: DB 접근 필요시 사용
    
Docker Compose:
    docker-compose -f docker-compose.test.yml up -d
"""

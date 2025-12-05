"""
Path Traversal 테스트
=====================

Path Traversal 공격 시도에 대해 API가 적절히 방어하는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_path_traversal.py -v --no-cov
"""

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestPathTraversal:
    """
    🔐 Path Traversal 테스트

    파일 시스템 경로 조작 시도에 대해 API가 적절히 방어하는지 검증합니다.
    """

    @pytest.mark.parametrize(
        "path_payload",
        [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "....//....//....//etc/passwd",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2f",
            "..%252f..%252f..%252f",
        ],
        ids=[
            "unix_path",
            "windows_path",
            "double_slash",
            "url_encoded",
            "double_encoded",
        ],
    )
    def test_path_traversal_attempt(self, client, path_payload):
        """
        Path Traversal 시도 테스트

        파일 시스템 경로 조작 시도에 대해
        적절히 차단되는지 확인합니다.

        🔍 검증 포인트:
        - 404 또는 400 반환 (파일 내용 노출 안 됨)
        - 5xx 에러 발생하지 않음
        """
        # Arrange - path_payload is provided by parametrize

        # Act - 상품 ID 위치에 Path Traversal 시도
        response = client.get(f"/api/products/{path_payload}/")

        # Assert
        assert response.status_code < 500, f"Path Traversal로 서버 에러 발생!\n" f"payload={path_payload}"
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_404_NOT_FOUND,
        ], f"예상치 못한 응답: {response.status_code}"

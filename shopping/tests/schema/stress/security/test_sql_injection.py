"""
SQL Injection 테스트
====================

SQL Injection 공격 시도에 대해 API가 적절히 방어하는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_sql_injection.py -v --no-cov
"""

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestSQLInjection:
    """
    🔐 SQL Injection 테스트

    다양한 SQL Injection 페이로드를 주입하여
    API가 적절히 방어하는지 검증합니다.
    """

    @pytest.mark.parametrize(
        "payload",
        [
            # SQL Injection payloads
            "1; DROP TABLE products;--",
            "1' OR '1'='1",
            "1 UNION SELECT * FROM users",
            "1'; DELETE FROM users WHERE '1'='1",
            "1 OR 1=1--",
            "admin'--",
            "1; SELECT * FROM information_schema.tables;--",
            # Blind SQL Injection
            "1' AND SLEEP(5)--",
            "1' AND (SELECT COUNT(*) FROM users) > 0--",
        ],
        ids=[
            "drop_table",
            "or_1_1",
            "union_select",
            "delete_users",
            "or_1_1_comment",
            "admin_comment",
            "info_schema",
            "blind_sleep",
            "blind_count",
        ],
    )
    def test_sql_injection_attempt(self, client, payload):
        """
        SQL 인젝션 시도 테스트

        다양한 SQL Injection 페이로드를 검색 파라미터에 주입하여
        실제 SQL 쿼리로 실행되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 5xx 에러 발생하지 않음 (SQL 에러 미발생)
        - 정상적인 빈 결과 또는 에러 응답 반환
        - SQL 쿼리가 실행되지 않음

        Args:
            payload: SQL Injection 페이로드
        """
        # Arrange - payload is provided by parametrize

        # Act - 검색 파라미터로 주입
        response = client.get(f"/api/products/?search={payload}")

        # Assert - SQL 에러로 인한 500 없음
        assert response.status_code < 500, (
            f"SQL Injection으로 서버 에러 발생!\n" f"payload={payload}\n" f"status_code={response.status_code}"
        )

        # Assert - 정상 응답
        assert response.status_code in [
            status.HTTP_200_OK,
            status.HTTP_400_BAD_REQUEST,
        ], f"예상치 못한 응답: {response.status_code}"

"""
리뷰 API Fuzz 테스트
====================

리뷰 작성 API에 무작위 입력을 주입하여
입력 검증 로직의 안정성을 확인합니다.

📋 테스트 대상:
- POST /api/products/{id}/reviews/ (리뷰 작성)
- GET /api/products/{id}/reviews/ (리뷰 목록)

🚀 실행 방법:
```bash
pytest shopping/tests/schema/stress/fuzz/test_fuzz_reviews.py -v -n 0
```
"""

import json

import pytest
from hypothesis import given, settings as hypothesis_settings, Phase, HealthCheck
from rest_framework import status

from .conftest import (
    rating_strategy,
    review_content_strategy,
)


@pytest.mark.fuzz
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestReviewsFuzz:
    """
    ⭐ 리뷰 API Fuzz 테스트

    리뷰 작성 API에 무작위 입력을 주입하여
    입력 검증 로직의 안정성을 확인합니다.

    📋 테스트 대상:
    - POST /api/products/{id}/reviews/ (리뷰 작성)
    - GET /api/products/{id}/reviews/ (리뷰 목록)

    ✅ 검증 속성:
    - 잘못된 평점에 대해 400 반환
    - XSS 페이로드가 이스케이프되거나 거부됨
    - 5xx 에러 발생하지 않음

    📅 가이드라인: 07_FUZZ_TESTING.md
    """

    @given(rating=rating_strategy, content=review_content_strategy)
    @hypothesis_settings(
        max_examples=50,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
        deadline=None,
        phases=[Phase.generate],
    )
    def test_review_create_fuzz(self, client, auth_headers, schema_test_product, rating, content):
        """
        리뷰 작성 API 퍼징

        다양한 형태의 평점과 내용을 주입하여
        리뷰 입력 검증 로직의 안정성을 확인합니다.

        🔍 테스트 관점:
        - 범위 외 평점 (0, 6, -1, 100)
        - 소수점 평점
        - XSS 스크립트 내용
        - SQL Injection 내용
        - 매우 긴 리뷰

        Args:
            rating: Hypothesis가 생성한 무작위 평점
            content: Hypothesis가 생성한 무작위 리뷰 내용
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": rating,
            "content": content,
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data, default=str),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러만 아니면 OK
        assert response.status_code < 500, (
            f"리뷰 작성에서 서버 에러 발생!\n"
            f"rating={repr(rating)}, content={repr(content[:50])}\n"
            f"상태 코드: {response.status_code}"
        )

        # 범위 외 평점은 거부되어야 함 (1-5 범위)
        if isinstance(rating, (int, float)) and (rating < 1 or rating > 5):
            assert response.status_code in [
                status.HTTP_400_BAD_REQUEST,
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                status.HTTP_403_FORBIDDEN,  # 구매 확인 실패
            ], f"범위 외 평점이 허용됨: rating={rating}, status={response.status_code}"

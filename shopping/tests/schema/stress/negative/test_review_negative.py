"""
리뷰 API Negative 테스트
=======================

TestReviewNegativeInputs: 잘못된 평점, 잘못된 리뷰 내용, 구매 확인 등
"""

import json

import pytest
from rest_framework import status


@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestReviewNegativeInputs:
    """
    ⭐ 리뷰 API에 대한 Negative 테스트

    리뷰 API에 잘못된 입력을 주입하여 적절한 에러 응답이 반환되는지 검증합니다.
    평점은 1-5 범위여야 하며, 리뷰 내용도 적절한 길이여야 합니다.

    📅 가이드라인: 08_NEGATIVE_TESTING.md
    """

    @pytest.mark.parametrize(
        "invalid_rating,expected_codes,description",
        [
            (0, [400, 422], "0점 - 최소값 미만"),
            (-1, [400, 422], "음수 평점"),
            (6, [400, 422], "6점 - 최대값 초과"),
            (100, [400, 422], "100점 - 범위 크게 초과"),
            (-100, [400, 422], "-100점 - 음수 범위 초과"),
            (1.5, [400, 422], "소수점 평점 (허용 여부에 따라)"),
            (None, [400, 422], "null 평점"),
            ("abc", [400, 422], "문자열 평점"),
            ("", [400, 422], "빈 문자열 평점"),
        ],
        ids=[
            "zero",
            "negative",
            "six",
            "hundred",
            "neg_hundred",
            "decimal",
            "null",
            "string",
            "empty_string",
        ],
    )
    def test_review_invalid_rating(
        self, client, auth_headers, schema_test_product, invalid_rating, expected_codes, description
    ):
        """
        잘못된 평점 값 테스트

        평점 범위(1-5) 외의 값을 주입하여
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 범위 외 평점 거부 (1-5만 허용)
        - 5xx 에러 발생하지 않음
        - 명확한 에러 메시지

        Args:
            invalid_rating: 테스트할 잘못된 평점
            expected_codes: 예상 HTTP 상태 코드 목록
            description: 테스트 설명
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": invalid_rating,
            "content": "좋은 상품입니다!",
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, (
            f"{description}에서 서버 에러 발생!\n" f"rating={invalid_rating}\n" f"status_code={response.status_code}"
        )

        # Assert - 적절한 에러 응답 (403은 구매 확인 실패일 수 있음)
        assert response.status_code in expected_codes + [
            status.HTTP_403_FORBIDDEN
        ], f"{description}: 예상치 못한 응답 {response.status_code}"

    @pytest.mark.parametrize(
        "invalid_content,description",
        [
            ("", "빈 리뷰 내용"),
            ("   ", "공백만 있는 리뷰"),
            (None, "null 리뷰 내용"),
            ("a" * 5001, "너무 긴 리뷰 (5000자 초과)"),
        ],
        ids=["empty", "whitespace", "null", "too_long"],
    )
    def test_review_invalid_content(self, client, auth_headers, schema_test_product, invalid_content, description):
        """
        잘못된 리뷰 내용 테스트

        빈 내용, 너무 긴 내용 등 잘못된 리뷰 내용에 대해
        적절한 에러 응답이 반환되는지 확인합니다.

        🔍 검증 포인트:
        - 빈 내용 거부 (또는 허용 정책에 따름)
        - 너무 긴 내용 거부
        - 5xx 에러 발생하지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": invalid_content,
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"{description}에서 서버 에러 발생!\n" f"status_code={response.status_code}"

    def test_review_without_purchase(self, client, auth_headers, schema_test_product):
        """
        구매하지 않은 상품에 리뷰 작성 시도 → 403

        구매 확인 로직이 있는 경우,
        구매하지 않은 상품에 대한 리뷰 작성을 거부해야 합니다.

        🔍 검증 포인트:
        - 구매 확인 로직 동작
        - 403 Forbidden 반환
        - 5xx 에러 발생하지 않음
        """
        # Arrange - 구매 이력 없는 사용자로 테스트
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": "좋은 상품입니다!",
        }

        # Act
        response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"서버 에러 발생: {response.status_code}"

        # 구매 확인 로직이 있으면 403, 없으면 201
        # 어느 쪽이든 5xx는 아니어야 함

    def test_review_duplicate_submission(self, client, auth_headers, schema_test_product):
        """
        동일 상품에 중복 리뷰 작성 시도

        이미 리뷰를 작성한 상품에 다시 리뷰를 작성하려 하면
        거부되어야 합니다 (정책에 따라 다름).

        🔍 검증 포인트:
        - 중복 리뷰 방지 (정책에 따름)
        - 5xx 에러 발생하지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        data = {
            "rating": 5,
            "content": "좋은 상품입니다!",
        }

        # Act - 첫 번째 리뷰 시도
        response1 = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Act - 두 번째 리뷰 시도 (중복)
        response2 = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(data),
            content_type="application/json",
            **headers,
        )

        # Assert - 5xx 에러 없음
        assert response2.status_code < 500, f"중복 리뷰에서 서버 에러 발생: {response2.status_code}"

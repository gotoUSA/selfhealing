"""
XSS (Cross-Site Scripting) 테스트
=================================

XSS 공격 시도에 대해 API가 적절히 방어하는지 검증합니다.

실행 방법:
    pytest shopping/tests/schema/stress/security/test_xss.py -v --no-cov
"""

import json

import pytest
from rest_framework import status


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestXSS:
    """
    🔐 XSS (Cross-Site Scripting) 테스트

    XSS 페이로드를 주입하여 API가 적절히 방어하는지 검증합니다.
    """

    @pytest.mark.parametrize(
        "xss_payload",
        [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert('xss')>",
            "javascript:alert('xss')",
            "<svg onload=alert('xss')>",
            "<body onload=alert('xss')>",
            "'\"><script>alert('xss')</script>",
            "<iframe src='javascript:alert(1)'>",
        ],
        ids=[
            "script_tag",
            "img_onerror",
            "javascript_uri",
            "svg_onload",
            "body_onload",
            "quote_escape",
            "iframe_js",
        ],
    )
    def test_xss_attempt_in_search(self, client, xss_payload):
        """
        XSS 시도 테스트 (검색 파라미터)

        XSS 페이로드를 검색 파라미터에 주입하여
        응답에서 이스케이프 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 서버 에러 발생하지 않음
        - 응답에 스크립트 태그가 그대로 포함되지 않음
        """
        # Arrange - xss_payload is provided by parametrize

        # Act
        response = client.get(f"/api/products/?search={xss_payload}")

        # Assert - 5xx 에러 없음
        assert response.status_code < 500, f"XSS 페이로드로 서버 에러 발생!\n" f"payload={xss_payload}"

        # Assert - XSS 페이로드가 그대로 반영되지 않음
        # JSON 응답이므로 HTML 이스케이프가 덜 중요하지만 확인
        if response.status_code == status.HTTP_200_OK:
            content = response.content.decode("utf-8", errors="ignore")
            # 스크립트 태그가 그대로 포함되면 위험
            assert "<script>alert" not in content.lower(), f"XSS 페이로드가 응답에 포함됨!\n" f"payload={xss_payload}"

    def test_xss_attempt_in_post_data(self, client, auth_headers, schema_test_product):
        """
        XSS 시도 테스트 (POST 데이터)

        상품 문의 등 사용자 입력을 받는 API에 XSS 페이로드를 주입하여
        저장 및 반환 시 이스케이프 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 저장 시 에러 발생하지 않음
        - 반환 시 스크립트가 실행 가능한 형태로 포함되지 않음
        """
        # Arrange
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}
        xss_payloads = [
            "<script>alert('xss')</script>",
            "<img src=x onerror=alert(1)>",
        ]

        for payload in xss_payloads:
            data = {"content": payload}

            # Act - 상품 문의 작성 시도
            response = client.post(
                f"/api/products/{schema_test_product.id}/questions/",
                data=json.dumps(data),
                content_type="application/json",
                **headers,
            )

            # Assert - 5xx 에러 없음
            assert response.status_code < 500, f"XSS 페이로드로 서버 에러 발생!\n" f"payload={payload}"

            # 성공적으로 저장된 경우, 반환값 확인
            if response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
                content = response.content.decode("utf-8", errors="ignore")
                # 실행 가능한 스크립트 태그가 그대로 포함되면 안 됨
                assert "<script>alert" not in content, f"XSS 페이로드가 응답에 포함됨!"

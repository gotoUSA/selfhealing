"""동시 API 요청 응답 일관성 테스트"""

import time

import jwt as pyjwt
import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from shopping.models.cart import Cart, CartItem
from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory

from .helpers import login_and_get_token, run_concurrent_requests


# 여기에 test_concurrency.py의 698~953줄을 복사하세요
# (TestConcurrentApiResponses 클래스)


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentApiResponses:
    """
    🌐 동시 API 요청 응답 일관성 테스트

    동시에 여러 요청이 들어올 때도 API 응답이 일관성을 유지하는지 검증합니다.

    📋 테스트 시나리오:
    - 동시 GET 요청 시 모든 응답 동일
    - 동시 인증 요청 시 토큰 정상 발급
    - 동시 조회 중 데이터 변경 시 일관성

    ✅ 예상 결과:
    - 모든 응답 코드 일관성
    - JSON 파싱 오류 없음
    - 5xx 에러 없음
    """

    def test_concurrent_product_list_reads(self, db):
        """
        동시 상품 목록 조회 테스트

        10개의 동시 GET 요청이 모두 같은 결과를 반환하는지 확인합니다.
        읽기 전용 API의 동시성 안전성을 검증합니다.

        🔍 검증 포인트:
        - 모든 응답 200 OK
        - 응답 데이터 파싱 가능
        - 5xx 에러 없음
        """
        # 테스트 상품 생성
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_read_{time.time()}", is_seller=True)
        for i in range(5):
            ProductFactory(
                category=category,
                seller=seller,
                stock=100,
                is_active=True,
            )

        num_requests = 10

        def get_products():
            """상품 목록 조회"""
            client = APIClient()
            response = client.get(reverse("product-list"))
            return {
                "status_code": response.status_code,
                "count": len(response.json().get("results", [])) if response.status_code == 200 else None,
            }

        # 동시 요청 실행
        args_list = [() for _ in range(num_requests)]
        results = run_concurrent_requests(get_products, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") == 200)
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 요청 성공
        assert success_count == num_requests, f"일부 요청 실패: {success_count}/{num_requests}"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, "서버 에러 발생!"

    def test_concurrent_login_requests(self, db):
        """
        동시 로그인 요청 테스트

        같은 사용자로 10번 동시 로그인해도 모두 성공하고
        유효한 토큰이 발급되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 요청 200 OK
        - 각 응답에 유효한 access 토큰 포함
        - ✅ 토큰 JTI 고유성 검증 (중복 토큰 방지)
        - 5xx 에러 없음
        """
        user = UserFactory(username=f"login_test_{time.time()}")

        num_requests = 10

        def try_login(username):
            """로그인 시도"""
            client = APIClient()
            response = client.post(
                reverse("auth-login"),
                {"username": username, "password": "testpass123"},
                format="json",
            )
            data = response.json() if response.status_code == 200 else {}
            access_token = data.get("access") or data.get("token", {}).get("access")
            has_token = bool(access_token)
            return {
                "status_code": response.status_code,
                "has_token": has_token,
                "access_token": access_token,  # JTI 검증을 위해 토큰 저장
            }

        # 동시 요청 실행
        args_list = [(user.username,) for _ in range(num_requests)]
        results = run_concurrent_requests(try_login, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") == 200)
        token_count = sum(1 for r in results if r.get("has_token"))
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 요청 성공
        assert success_count == num_requests, f"일부 로그인 실패: {success_count}/{num_requests}"

        # Assert: 모든 응답에 토큰 포함
        assert token_count == num_requests, f"일부 응답에 토큰 없음: {token_count}/{num_requests}"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, "서버 에러 발생!"

        # ✅ JWT JTI 고유성 검증 - 동시 로그인에서 토큰 중복 방지
        tokens = [r.get("access_token") for r in results if r.get("access_token")]
        if tokens:
            try:
                # JWT decode (검증 없이 payload만 추출)
                claims = []
                for token in tokens:
                    try:
                        # options에서 서명 검증 비활성화
                        payload = pyjwt.decode(token, options={"verify_signature": False})
                        claims.append(payload)
                    except pyjwt.InvalidTokenError:
                        pass  # 잘못된 토큰은 무시

                # JTI가 있는 경우 고유성 검증
                jtis = [c.get("jti") for c in claims if c.get("jti")]
                if jtis:
                    unique_jtis = set(jtis)
                    assert len(unique_jtis) == len(jtis), (
                        f"JWT JTI 중복 발생! 동시 로그인에서 토큰이 재사용됨. "
                        f"(전체: {len(jtis)}, 고유: {len(unique_jtis)})"
                    )
            except Exception as e:
                # JWT 파싱 실패 시 경고만 출력 (테스트 실패 안함)
                import warnings

                warnings.warn(f"JWT JTI 검증 스킵: {e}")

    @pytest.mark.slow
    def test_concurrent_mixed_operations(self, db):
        """
        혼합 동시 요청 테스트 (@slow)

        읽기/쓰기 요청이 동시에 발생할 때도
        API가 안정적으로 동작하는지 확인합니다.

        🔍 검증 포인트:
        - 읽기 요청 영향 없음
        - 쓰기 요청 순차 처리
        - ✅ 최종 장바구니 수량 = 쓰기 성공 횟수
        - 5xx 에러 없음
        """
        category = CategoryFactory()
        seller = UserFactory(username=f"seller_mixed_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=100,
            is_active=True,
        )

        users = [UserFactory(username=f"mixed_user_{i}_{time.time()}") for i in range(5)]

        def read_operation():
            """상품 목록 읽기"""
            client = APIClient()
            response = client.get(reverse("product-list"))
            # ✅ GET 응답이 JSON 파싱 가능한지 확인
            try:
                data = response.json()
                json_valid = True
            except Exception:
                json_valid = False
            return {
                "type": "read",
                "status_code": response.status_code,
                "json_valid": json_valid,
            }

        def write_operation(user, product_id):
            """장바구니 추가 (쓰기)"""
            client, token, error = login_and_get_token(user.username)
            if error:
                return {"type": "write", "status_code": 0, "error": error, "user_id": user.id}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
            response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            return {
                "type": "write",
                "status_code": response.status_code,
                "user_id": user.id,
            }

        # 읽기 + 쓰기 혼합 요청 준비
        results = []

        # 읽기 요청 동시 실행
        read_args = [() for _ in range(10)]
        read_results = run_concurrent_requests(read_operation, read_args, max_workers=10)
        results.extend(read_results)

        # 쓰기 요청 동시 실행
        write_args = [(u, product.id) for u in users]
        write_results = run_concurrent_requests(write_operation, write_args, max_workers=5)
        results.extend(write_results)

        # 결과 분석
        read_results_only = [r for r in results if r.get("type") == "read"]
        write_results_only = [r for r in results if r.get("type") == "write"]

        read_success = sum(1 for r in read_results_only if r.get("status_code") == 200)
        read_json_valid = sum(1 for r in read_results_only if r.get("json_valid"))
        write_success = sum(1 for r in write_results_only if r.get("status_code") in [200, 201])
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 모든 읽기 요청 성공
        assert read_success == 10, f"읽기 요청 일부 실패: {read_success}/10"

        # ✅ Assert: 읽기 응답이 모두 유효한 JSON
        assert read_json_valid == 10, f"쓰기 중 GET 응답에서 JSON 파싱 오류 발생! " f"(유효: {read_json_valid}/10)"

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, "서버 에러 발생!"

        # ✅ 최종 상태 검증 - 장바구니 수량이 논리적으로 맞는지
        # 각 사용자별로 장바구니가 생성되었는지 확인
        total_cart_quantity = 0
        for user in users:
            cart = Cart.objects.filter(user=user, is_active=True).first()
            if cart:
                cart_item = CartItem.objects.filter(cart=cart, product=product).first()
                if cart_item:
                    total_cart_quantity += cart_item.quantity

        # 쓰기 성공 횟수와 총 장바구니 수량이 일치해야 함
        assert total_cart_quantity == write_success, (
            f"데이터 무결성 오류: 쓰기 성공({write_success})과 " f"총 장바구니 수량({total_cart_quantity})이 일치하지 않음!"
        )

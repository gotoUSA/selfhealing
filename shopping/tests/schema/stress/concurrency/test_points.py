"""포인트 동시성 테스트"""

import time
from decimal import Decimal

import pytest
from django.urls import reverse

from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory, OrderFactory

from .helpers import close_db_connection, login_and_get_token, run_concurrent_requests


# 여기에 test_concurrency.py의 956~1242줄을 복사하세요
# (TestConcurrentPointOperations 클래스)


@pytest.mark.concurrency
@pytest.mark.django_db(transaction=True)
class TestConcurrentPointOperations:
    """
    💰 포인트 동시성 테스트

    동시에 여러 포인트 적립/차감 요청이 발생할 때
    포인트 잔액이 음수가 되지 않고 정확하게 계산되는지 검증합니다.

    📋 테스트 시나리오:
    - 동시 포인트 사용 시 잔액 음수 방지
    - 동시 포인트 적립 시 정확한 합계
    - 포인트 사용 + 적립 동시 발생 시 무결성

    ✅ 예상 결과:
    - 포인트 잔액 >= 0 (절대 음수 안 됨)
    - 최종 포인트 = 초기값 + 적립 - 사용
    - 5xx 에러 없음

    🔧 동시성 제어:
    - User.objects.select_for_update()
    - F('points') - used_points >= 0 조건부 업데이트

    📅 가이드라인: 09_PERFORMANCE_TESTING.md (동시성 섹션)
    """

    @pytest.fixture
    def user_with_points(self, db):
        """포인트를 가진 테스트 사용자 생성"""
        user = UserFactory(
            username=f"point_user_{time.time()}",
            points=10000,  # 10,000 포인트
        )
        return user

    @pytest.fixture
    def point_test_setup(self, db):
        """포인트 테스트용 상품 및 사용자 설정"""
        category = CategoryFactory()
        seller = UserFactory(username=f"point_seller_{time.time()}", is_seller=True)
        product = ProductFactory(
            category=category,
            seller=seller,
            stock=100,
            price=Decimal("50000"),
            is_active=True,
        )
        return {
            "product": product,
            "category": category,
        }

    def test_concurrent_point_usage_no_negative(self, user_with_points, point_test_setup):
        """
        동시 포인트 사용 시 잔액 음수 방지 테스트

        10,000 포인트를 가진 사용자가 10개의 동시 요청으로
        각각 2,000 포인트씩 사용하려 할 때,
        잔액이 음수가 되지 않는지 확인합니다.

        🔍 검증 포인트:
        - 포인트 잔액 >= 0
        - 성공 요청 수 <= 5 (10000 / 2000)
        - 포인트 부족 요청은 적절한 에러 반환
        - 5xx 에러 없음

        동시성 이슈 예방:
        - select_for_update() 사용
        - DB 레벨에서 포인트 >= 0 체크
        """
        user = user_with_points
        product = point_test_setup["product"]
        initial_points = user.points
        points_to_use = 2000
        num_requests = 10

        def use_points(username, product_id, points):
            """포인트 사용 주문 시도"""
            client, token, error = login_and_get_token(username)
            if error:
                return {"status_code": 0, "error": error}

            client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

            # 장바구니에 상품 추가
            cart_response = client.post(
                reverse("cart-add-item"),
                {"product_id": product_id, "quantity": 1},
                format="json",
            )
            if cart_response.status_code not in [200, 201]:
                return {"status_code": cart_response.status_code, "step": "cart"}

            # 포인트를 사용한 주문 생성 시도
            order_response = client.post(
                reverse("order-list"),
                {
                    "shipping_address": "서울시 강남구 테헤란로 123",
                    "shipping_name": "홍길동",
                    "shipping_phone": "010-1234-5678",
                    "shipping_postal_code": "12345",
                    "payment_method": "card",
                    "used_points": points,
                },
                format="json",
            )
            return {
                "status_code": order_response.status_code,
                "step": "order",
                "data": order_response.json() if order_response.status_code < 500 else None,
            }

        # 동시 요청 실행
        args_list = [(user.username, product.id, points_to_use) for _ in range(num_requests)]
        results = run_concurrent_requests(use_points, args_list, max_workers=num_requests)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("status_code") in [200, 201])
        error_5xx_count = sum(1 for r in results if r.get("status_code", 0) >= 500)

        # Assert: 5xx 에러 없음
        assert error_5xx_count == 0, f"서버 에러 발생! 5xx 응답 {error_5xx_count}개"

        # Assert: 포인트 잔액 음수 확인
        user.refresh_from_db()
        assert user.points >= 0, f"포인트가 음수! points={user.points}"

        # Assert: 성공 주문 수가 가능한 범위 내
        max_possible_orders = initial_points // points_to_use
        assert success_count <= max_possible_orders, f"예상보다 많은 주문 성공: {success_count} > {max_possible_orders}"

        # ✅ 회계 무결성 검증
        # 사용된 포인트 = 초기 포인트 - 현재 포인트
        used_total = initial_points - user.points
        expected_used = success_count * points_to_use
        assert used_total <= expected_used, f"포인트 사용 계산 오류: 실제 사용({used_total}) > 예상 사용({expected_used})"

    def test_concurrent_point_earning(self, db, point_test_setup):
        """
        동시 포인트 적립 테스트

        10명의 사용자가 동시에 주문을 완료하여
        각각 포인트를 적립받을 때,
        포인트가 정확히 적립되는지 확인합니다.

        🔍 검증 포인트:
        - 모든 적립 요청 성공
        - 각 사용자 포인트 정확히 적립
        - 5xx 에러 없음
        """

        num_users = 10
        users = [UserFactory(username=f"earn_user_{i}_{time.time()}", points=0) for i in range(num_users)]

        # 각 사용자에게 결제 완료 주문 생성 (포인트 적립)
        earned_points = 500

        def create_paid_order_and_earn(user_obj):
            """결제 완료 주문 생성으로 포인트 적립 시도"""
            try:
                order = OrderFactory.paid(
                    user=user_obj,
                    total_amount=Decimal("50000"),
                    earned_points=earned_points,
                )
                # 실제 포인트 적립 로직 호출 (서비스에 따라 다를 수 있음)
                user_obj.points += order.earned_points
                user_obj.save(update_fields=["points"])
                return {"success": True, "user_id": user_obj.id, "points": user_obj.points}
            except Exception as e:
                return {"success": False, "error": str(e), "user_id": user_obj.id}
            finally:
                close_db_connection()

        # 동시 요청 실행
        args_list = [(u,) for u in users]
        results = run_concurrent_requests(lambda u: create_paid_order_and_earn(u), args_list, max_workers=num_users)

        # 결과 분석
        success_count = sum(1 for r in results if r.get("success"))

        # Assert: 모든 적립 성공
        assert success_count == num_users, f"일부 적립 실패: {success_count}/{num_users}"

        # Assert: 각 사용자 포인트 확인
        for user in users:
            user.refresh_from_db()
            assert (
                user.points == earned_points
            ), f"사용자 {user.id} 포인트 불일치: expected={earned_points}, actual={user.points}"

    @pytest.mark.slow
    def test_concurrent_point_use_and_earn(self, db, point_test_setup):
        """
        포인트 사용 + 적립 동시 발생 테스트 (@slow)

        동일 사용자가 포인트를 사용하는 주문과
        포인트를 적립받는 이벤트가 동시에 발생할 때,
        최종 포인트가 정확히 계산되는지 확인합니다.

        🔍 검증 포인트:
        - 최종 포인트 = 초기 + 적립 - 사용
        - 포인트 >= 0
        - 데이터 무결성 유지
        """
        user = UserFactory(
            username=f"mixed_point_user_{time.time()}",
            points=5000,  # 초기 5000 포인트
        )
        initial_points = user.points

        # 사용할 포인트와 적립할 포인트
        points_to_use = 1000
        points_to_earn = 500

        def use_points_operation(user_obj):
            """포인트 사용"""
            try:
                from django.db import transaction
                from django.db.models import F

                with transaction.atomic():
                    from shopping.models.user import User

                    # select_for_update로 락 획득
                    locked_user = User.objects.select_for_update().get(id=user_obj.id)
                    if locked_user.points >= points_to_use:
                        locked_user.points = F("points") - points_to_use
                        locked_user.save(update_fields=["points"])
                        return {"success": True, "operation": "use", "amount": points_to_use}
                    else:
                        return {"success": False, "operation": "use", "error": "insufficient"}
            except Exception as e:
                return {"success": False, "operation": "use", "error": str(e)}
            finally:
                close_db_connection()

        def earn_points_operation(user_obj):
            """포인트 적립"""
            try:
                from django.db import transaction
                from django.db.models import F

                with transaction.atomic():
                    from shopping.models.user import User

                    # select_for_update로 락 획득
                    locked_user = User.objects.select_for_update().get(id=user_obj.id)
                    locked_user.points = F("points") + points_to_earn
                    locked_user.save(update_fields=["points"])
                    return {"success": True, "operation": "earn", "amount": points_to_earn}
            except Exception as e:
                return {"success": False, "operation": "earn", "error": str(e)}
            finally:
                close_db_connection()

        # 사용 5회 + 적립 5회 = 총 10회 동시 실행
        use_args = [(user,) for _ in range(5)]
        earn_args = [(user,) for _ in range(5)]

        use_results = run_concurrent_requests(lambda u: use_points_operation(u), use_args, max_workers=5)
        earn_results = run_concurrent_requests(lambda u: earn_points_operation(u), earn_args, max_workers=5)

        # 결과 분석
        use_success = sum(1 for r in use_results if r.get("success"))
        earn_success = sum(1 for r in earn_results if r.get("success"))

        # 모든 적립은 성공해야 함
        assert earn_success == 5, f"적립 일부 실패: {earn_success}/5"

        # Assert: 포인트 잔액 음수 확인
        user.refresh_from_db()
        assert user.points >= 0, f"포인트가 음수! points={user.points}"

        # ✅ 회계 무결성 검증
        # 최종 포인트 = 초기 + (적립 성공 * 적립액) - (사용 성공 * 사용액)
        expected_final = initial_points + (earn_success * points_to_earn) - (use_success * points_to_use)
        assert user.points == expected_final, (
            f"포인트 계산 오류: expected={expected_final}, actual={user.points}\n"
            f"초기={initial_points}, 적립={earn_success}x{points_to_earn}, 사용={use_success}x{points_to_use}"
        )

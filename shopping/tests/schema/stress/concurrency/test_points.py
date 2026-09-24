"""포인트 동시성 테스트"""

import time
from decimal import Decimal

import pytest

from shopping.tests.factories import ProductFactory, UserFactory, CategoryFactory, OrderFactory

from .helpers import close_db_connection, run_concurrent_requests


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

        10,000 포인트를 가진 사용자의 주문 10건(각 2,000 포인트 사용)을 주문 처리 태스크가 동시에 처리할 때
        정확히 5건만 확정되고 잔액이 음수가 되지 않는지 확인합니다.

        포인트 차감은 주문 API 가 아니라 주문 처리 태스크(워커)에서 일어난다. API 는 잔액만 보고 202 를 주므로
        같은 포인트로 낸 주문 여러 건이 모두 접수되고, 워커 여러 개가 그 주문들을 동시에 처리한다 — 이 테스트는
        그 지점을 잰다. (예전 버전은 주문 API 에 `used_points`(응답 필드명)를 보내 포인트를 아예 쓰지 않았고,
        장바구니 하나를 스레드 10개가 나눠 써서 포인트 단계까지 가는 요청도 거의 없었다.)

        🔍 검증 포인트:
        - 확정 주문 정확히 5건 (10000 / 2000), 나머지 5건은 "포인트 사용 실패"로 실패
        - 포인트 잔액 = 초기 - 확정 × 2000 (= 0), 음수 없음
        - 실패한 주문의 재고는 복구 (재고 = 초기 - 5)
        - 태스크 예외 없음
        """
        from shopping.models.cart import Cart, CartItem
        from shopping.models.order import Order
        from shopping.services.point_service import PointService
        from shopping.tasks.order_tasks import process_order_heavy_tasks

        user = user_with_points
        product = point_test_setup["product"]
        points_to_use = 2000
        num_orders = 10

        # 잔액을 적립 건으로 채운다 (FIFO 가 실제로 적립 건을 깎도록)
        type(user).objects.filter(pk=user.pk).update(points=0)
        PointService.add_points(user=user, amount=10000, type="earn", description="stress seed")
        user.refresh_from_db()
        initial_points = user.points
        initial_stock = product.stock

        # 주문 API 가 만들어 둔 상태: 주문마다 비활성화된 장바구니 + pending 주문 (포인트는 아직 안 깎임)
        jobs = []
        for _ in range(num_orders):
            cart = Cart.objects.create(user=user, is_active=False)
            CartItem.objects.create(cart=cart, product=product, quantity=1)
            order = Order.objects.create(
                user=user,
                status="pending",
                total_amount=product.price,
                used_points=points_to_use,
                final_amount=product.price - points_to_use,
                shipping_name="홍길동",
                shipping_phone="010-1234-5678",
                shipping_postal_code="12345",
                shipping_address="서울시 강남구 테헤란로 123",
                shipping_address_detail="101동",
            )
            jobs.append((order.id, cart.id, points_to_use))

        def process(order_id, cart_id, use_points):
            """워커 하나가 주문 태스크 하나를 처리"""
            return process_order_heavy_tasks(order_id, cart_id, use_points)

        results = run_concurrent_requests(process, jobs, max_workers=num_orders)

        errors = [r for r in results if "exception_type" in r]
        assert errors == [], f"태스크 예외: {errors}"

        orders = Order.objects.filter(pk__in=[j[0] for j in jobs])
        confirmed = orders.filter(status="confirmed").count()
        failed = orders.filter(status="failed", failure_reason__startswith="포인트 사용 실패").count()
        max_possible_orders = initial_points // points_to_use
        assert confirmed == max_possible_orders, f"확정 {confirmed}건 (기대 {max_possible_orders})"
        assert failed == num_orders - max_possible_orders, f"포인트 부족 실패 {failed}건"

        # ✅ 회계 무결성: 사용된 포인트 = 확정 × 2000, 잔액 음수 없음
        user.refresh_from_db()
        assert user.points >= 0, f"포인트가 음수! points={user.points}"
        assert initial_points - user.points == confirmed * points_to_use

        # 실패한 주문의 재고는 돌아왔다
        product.refresh_from_db()
        assert product.stock == initial_stock - confirmed

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
